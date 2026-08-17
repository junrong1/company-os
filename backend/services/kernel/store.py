"""The log store: one writer, one transaction per tick, no mutation.

Four invariants, and the tests exist for the failure each one prevents.

**One transaction per tick (R21).** Every event from one `step()` appends atomically. A
partial tick folds to a state no pure step could produce, and the day-boundary hash would
report it at the *next* boundary as an unexplainable determinism regression rather than at
the fault.

**The next sequence is read inside the append transaction (R37).** As the run's committed
maximum plus one. An aborted tick therefore cannot advance the cursor and leave a gap,
which the corrupt-tail detector would misread as damage.

**Every append presents the lease's fencing token (R26).** So a kernel whose lease was
taken over cannot write, even if it does not yet know it lost it.

**All appends serialise through one writer (R35).** Per-run tick tasks enqueue a batch and
a single writer commits one tick per transaction. Without this, sole-writer would hold only
at the process level: two active runs appending concurrently could let a later sequence
become visible before an earlier one, and a tailer treating the highest row as the head
would skip an event permanently.
"""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, event, func, insert, select, update
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from contracts.envelope import Envelope, EventKind, build
from kernel import lease as lease_module
from kernel.lease import LeaseHandle, utc_now_iso
from logschema import (
    DDL_VERSION,
    append_only_ddl,
    event_log,
    is_append_only_refusal,
    metadata,
    runs,
    store_version,
)


class StoreError(Exception):
    """Something the kernel must not paper over."""


class DdlVersionMismatch(StoreError):
    """The store's schema is not the one this kernel understands."""


class SequenceCollision(StoreError):
    """Two events claimed one (run, seq).

    Fatal on purpose. The kernel halts rather than advancing the cursor: a duplicate
    sequence means two writers, and continuing would produce a log that folds to a state
    neither produced.
    """


class DuplicateAnswer(StoreError):
    """An answer for a request id that already has one (R25).

    Distinguished from a sequence collision because the two mean opposite things. A
    duplicate answer is *expected* traffic — a service that retried, or a reconnect
    replaying — and is handled by rejecting the duplicate and carrying on. A duplicate
    sequence means two writers reached the log, and the kernel must halt. Reporting both
    as the same fatal error would either hide a two-writer bug or halt the kernel over a
    retry.
    """


class RunAlreadyTerminated(StoreError):
    """Nothing appends after a run's terminal event (R25)."""


class FencedOut(StoreError):
    """This kernel's lease was taken over. It may no longer append."""


#: Above this many events, a fork is refused rather than attempted. One long transaction
#: holding the single writer would stall every other run's ticks.
FORK_PREFIX_MAX_EVENTS = 5_000


@dataclass(slots=True)
class AppendResult:
    """What one committed tick produced."""

    envelopes: list[Envelope]
    head_seq: int


@dataclass(slots=True)
class ForkResult:
    """A fork, or the reason there is not one."""

    child_run_id: str
    copied_through_seq: int
    refusal: str = ""

    @property
    def forked(self) -> bool:
        return not self.refusal


def make_engine(url: str, echo: bool = False) -> Engine:
    """Build an engine with both dialects configured to behave the same way.

    The SQLite configuration is not optional tuning. Left at its defaults, pysqlite
    silently disagrees with Postgres on three things that matter here: it emits its own
    implicit BEGIN in the wrong places (which breaks SAVEPOINT and makes isolation differ),
    it leaves foreign keys off, and it journals in a mode where a reader blocks a writer.

    A file-backed SQLite store also has its directory created here. The default URL points at
    `var/`, which is git-ignored and therefore absent from a fresh clone — and SQLite reports a
    missing parent directory as `unable to open database file`, which reads as a corrupt or
    locked store rather than as a directory that was never there.
    """
    parsed = make_url(url)
    if parsed.drivername.startswith("sqlite") and parsed.database not in (None, "", ":memory:"):
        Path(parsed.database).parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine(url, echo=echo, future=True)

    if engine.dialect.name != "sqlite":
        return engine

    @event.listens_for(engine, "connect")
    def _sqlite_connect(dbapi_connection: Any, _record: Any) -> None:
        # Hand transaction control to SQLAlchemy. pysqlite's implicit BEGIN is what breaks
        # SAVEPOINT and makes isolation differ from Postgres.
        dbapi_connection.isolation_level = None

        cursor = dbapi_connection.cursor()
        # Off by default, unlike Postgres.
        cursor.execute("PRAGMA foreign_keys=ON")
        # Persistent and database-wide once set. Gives non-blocking readers with one
        # writer — which is one writer *at a time*, not one writer, so it is not a
        # substitute for the lease.
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()

    @event.listens_for(engine, "begin")
    def _sqlite_begin(connection: Any) -> None:
        # IMMEDIATE takes the write lock up front. A deferred transaction that upgrades
        # mid-way can fail with SQLITE_BUSY against a concurrent writer, which would
        # surface as a flaky append rather than as the lease refusal it should be.
        connection.exec_driver_sql("BEGIN IMMEDIATE")

    return engine


class LogStore:
    """The kernel's handle on the log. The only thing in the system that appends."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    # --- schema ----------------------------------------------------------

    def create_all(self) -> None:
        """Create the schema, the append-only triggers, and the version row.

        Schema creation is owned by a one-shot step or gated by the same lease as the
        kernel — a kernel that creates schema on startup unconditionally reintroduces the
        two-writer collision it exists to prevent.
        """
        metadata.create_all(self.engine)

        with self.engine.begin() as connection:
            for statement in append_only_ddl(connection.dialect.name):
                connection.exec_driver_sql(statement)

            existing = connection.execute(select(store_version.c.ddl_version)).first()
            if existing is None:
                connection.execute(
                    insert(store_version).values(
                        id=1, ddl_version=DDL_VERSION, created_at=utc_now_iso()
                    )
                )

    def check_ddl_version(self) -> int:
        """Refuse to start on a mismatch, naming both versions and the remedy."""
        with self.engine.connect() as connection:
            found = connection.execute(select(store_version.c.ddl_version)).scalar_one_or_none()

        if found is None:
            raise DdlVersionMismatch(
                "the store has no DDL version row. It was not created by this kernel. "
                "Remedy: point at an empty database and let the kernel create it, or "
                "restore a store created at DDL version "
                f"{DDL_VERSION}."
            )
        if found != DDL_VERSION:
            raise DdlVersionMismatch(
                f"store DDL version is {found}; this kernel understands {DDL_VERSION}. "
                "Refusing to start rather than appending to a schema it does not "
                "understand. Remedy: runs are disposable across a schema change — wipe "
                "the store and start a new run, or run a kernel built for DDL version "
                f"{found}."
            )
        return found

    # --- runs ------------------------------------------------------------

    def create_run(
        self,
        run_id: str,
        run_seed: int,
        rules_ver: str,
        quantum_sim_seconds: int,
        grid: tuple[int, int],
        horizon_tick: int | None = None,
        parent_run_id: str | None = None,
        forked_at_seq: int | None = None,
    ) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                insert(runs).values(
                    run_id=run_id,
                    run_seed=run_seed,
                    current_tick=0,
                    head_seq=0,
                    rate=1,
                    rules_ver=rules_ver,
                    quantum_sim_seconds=quantum_sim_seconds,
                    grid_cols=grid[0],
                    grid_rows=grid[1],
                    horizon_tick=horizon_tick,
                    parent_run_id=parent_run_id,
                    forked_at_seq=forked_at_seq,
                    # R21's first half: a run's lineage root is itself at creation. Set
                    # here rather than defaulted in the DDL because no portable server
                    # default can name another column of the row being inserted.
                    lineage_root_id=run_id,
                    created_at=utc_now_iso(),
                )
            )

    def run_row(self, run_id: str) -> dict[str, Any] | None:
        with self.engine.connect() as connection:
            row = connection.execute(select(runs).where(runs.c.run_id == run_id)).mappings().first()
        return dict(row) if row else None

    def list_runs(self) -> list[dict[str, Any]]:
        """Every run the store knows, oldest first.

        The kernel needs this at startup: a run's rate is run state, so a process restart must
        pick up the clock where it left off rather than leaving a run silently stopped (R18).
        Ordered by creation so resumption is deterministic across restarts, with the run id
        breaking ties — two runs created in the same clock tick would otherwise resume in whatever
        order the store happened to return them.
        """
        with self.engine.connect() as connection:
            rows = (
                connection.execute(select(runs).order_by(runs.c.created_at, runs.c.run_id))
                .mappings()
                .all()
            )
        return [dict(row) for row in rows]

    def terminate_run(self, run_id: str, terminal_seq: int, reason: str) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                update(runs)
                .where(runs.c.run_id == run_id)
                .values(terminal_seq=terminal_seq, terminal_reason=reason)
            )

    # --- the append path -------------------------------------------------

    def head_seq(self, run_id: str) -> int:
        """The run's committed maximum sequence. 0 for a run with no events."""
        with self.engine.connect() as connection:
            return self._committed_head(connection, run_id)

    @staticmethod
    def _committed_head(connection: Any, run_id: str) -> int:
        return int(
            connection.execute(
                select(func.coalesce(func.max(event_log.c.seq), 0)).where(
                    event_log.c.run_id == run_id
                )
            ).scalar_one()
        )

    def append_tick(
        self,
        run_id: str,
        emitted: list[Any],
        lease_handle: LeaseHandle,
        rules_ver: str,
        tick: int,
        fail_between_events: bool = False,
    ) -> AppendResult:
        """Append every event from one tick, atomically.

        `fail_between_events` is a test seam: it raises after the first insert but inside
        the transaction, so the suite can prove that a failure mid-tick leaves all of the
        tick's events or none of them. It is a parameter rather than monkeypatching so the
        guarantee is exercised through the real code path.
        """
        if not emitted:
            return AppendResult(envelopes=[], head_seq=self.head_seq(run_id))

        try:
            with self.engine.begin() as connection:
                # The fencing check, inside the same transaction as the writes. Reading it
                # beforehand would leave a window in which the lease changed hands.
                token = lease_module.current_token(connection)
                if token != lease_handle.token:
                    raise FencedOut(
                        f"this kernel holds lease token {lease_handle.token} but the store's "
                        f"current token is {token}. The lease was taken over; appending "
                        "would interleave two writers."
                    )

                run = connection.execute(
                    select(runs.c.terminal_seq).where(runs.c.run_id == run_id)
                ).first()
                if run is None:
                    raise StoreError(f"no such run: {run_id}")
                if run.terminal_seq is not None:
                    raise RunAlreadyTerminated(
                        f"run {run_id} ended at sequence {run.terminal_seq}; nothing appends "
                        "after a terminal event"
                    )

                # R37: the committed maximum plus one, read here rather than tracked in
                # memory, so an aborted tick cannot advance the cursor.
                next_seq = self._committed_head(connection, run_id) + 1

                envelopes: list[Envelope] = []
                ingested_at = utc_now_iso()

                for offset, item in enumerate(emitted):
                    envelope = build(
                        seq=next_seq + offset,
                        tick=tick,
                        kind=item.kind if isinstance(item.kind, EventKind) else EventKind(item.kind),
                        rules_ver=rules_ver,
                        payload=item.payload,
                        run_id=run_id,
                        command_id=getattr(item, "command_id", "") or "",
                        request_id=getattr(item, "request_id", "") or "",
                    )
                    row = envelope.to_dict()
                    row["ingested_at"] = ingested_at
                    connection.execute(insert(event_log).values(**row))
                    envelopes.append(envelope)

                    if fail_between_events and offset == 0 and len(emitted) > 1:
                        raise StoreError("injected failure between two events of one tick")

                head = next_seq + len(emitted) - 1
                connection.execute(
                    update(runs)
                    .where(runs.c.run_id == run_id)
                    .values(current_tick=tick, head_seq=head)
                )

                return AppendResult(envelopes=envelopes, head_seq=head)

        except IntegrityError as exc:
            detail = str(exc.orig)

            # Which unique index fired decides whether this is routine or fatal.
            if "ux_event_log_answer" in detail or "request_id" in detail.lower():
                raise DuplicateAnswer(
                    f"an answer for this request id already exists in run {run_id}; the "
                    "duplicate is rejected structurally rather than by a racing check. "
                    f"Underlying error: {exc.orig}"
                ) from exc

            raise SequenceCollision(
                f"two events claimed one sequence in run {run_id}. This means two writers "
                "reached the log; the kernel must halt rather than advance the cursor. "
                f"Underlying error: {exc.orig}"
            ) from exc

    # --- fork ------------------------------------------------------------

    def fork_run(
        self,
        parent_run_id: str,
        at_seq: int,
        child_run_id: str,
        lease_handle: LeaseHandle,
        prefix_bound: int = FORK_PREFIX_MAX_EVENTS,
    ) -> ForkResult:
        """Copy a parent's log prefix into a new run, atomically.

        An eager prefix copy rather than a copy-on-read view, because a view would make
        every child fold depend on its parent's rows staying exactly as they were — and the
        parent is still being appended to.

        This lives in the store rather than as a free-standing operation for one reason:
        it must happen inside the lease-fenced transaction. A fork is a write, so it has to
        present the same token every append does, or a fenced-out kernel could still create
        runs.

        Refused above `prefix_bound`. One long transaction holding the single writer would
        stall every other run's ticks, and the plan is explicit that this surfaces as a
        phantom outage in the lag metric — an operator would be looking for a store problem
        that does not exist.
        """
        with self.engine.begin() as connection:
            token = lease_module.current_token(connection)
            if token != lease_handle.token:
                raise FencedOut(
                    f"this kernel holds lease token {lease_handle.token} but the store's "
                    f"current token is {token}; a fork is a write and is refused too."
                )

            parent = connection.execute(
                select(runs).where(runs.c.run_id == parent_run_id)
            ).mappings().first()
            if parent is None:
                raise StoreError(f"no such run: {parent_run_id}")

            prefix_size = int(
                connection.execute(
                    select(func.count(event_log.c.seq)).where(
                        event_log.c.run_id == parent_run_id, event_log.c.seq <= at_seq
                    )
                ).scalar_one()
            )
            if prefix_size == 0:
                raise StoreError(
                    f"run {parent_run_id} has no events at or before sequence {at_seq}"
                )
            if prefix_size > prefix_bound:
                return ForkResult(
                    child_run_id="",
                    copied_through_seq=0,
                    refusal=(
                        f"the prefix at sequence {at_seq} holds {prefix_size} events, above "
                        f"the bound of {prefix_bound}. Refusing rather than holding the "
                        "single writer in one long transaction, which would stall every "
                        "other run's ticks and read as a store outage that is not happening."
                    ),
                )

            # The child inherits seed, quantum, grid and horizon. Horizon especially: it is
            # chosen at genesis and immutable, so a fork cannot outlive its parent's bound.
            connection.execute(
                insert(runs).values(
                    run_id=child_run_id,
                    run_seed=parent["run_seed"],
                    current_tick=parent["current_tick"],
                    head_seq=at_seq,
                    rate=0,  # a fresh child starts paused; starting it is a decision
                    rules_ver=parent["rules_ver"],
                    quantum_sim_seconds=parent["quantum_sim_seconds"],
                    grid_cols=parent["grid_cols"],
                    grid_rows=parent["grid_rows"],
                    horizon_tick=parent["horizon_tick"],
                    parent_run_id=parent_run_id,
                    forked_at_seq=at_seq,
                    # The creation rule, applied uniformly: a new row's lineage root is its
                    # own id. **U16 owns changing this to the parent's root**, which is
                    # R21's second half and what makes the HUD's aggregate span a lineage
                    # rather than a run. Written this way rather than left null so the
                    # column has no "no lineage yet" state for a reader to handle, and so
                    # the one line U16 changes is visible instead of implied.
                    lineage_root_id=child_run_id,
                    created_at=utc_now_iso(),
                )
            )

            rows = connection.execute(
                select(event_log)
                .where(event_log.c.run_id == parent_run_id, event_log.c.seq <= at_seq)
                .order_by(event_log.c.seq)
            ).mappings().all()

            for row in rows:
                copied = dict(row)
                copied["run_id"] = child_run_id
                connection.execute(insert(event_log).values(**copied))

            return ForkResult(child_run_id=child_run_id, copied_through_seq=at_seq)

    # --- reading ---------------------------------------------------------

    def read_events(
        self, run_id: str, after_seq: int = 0, through_seq: int | None = None, limit: int | None = None
    ) -> list[Envelope]:
        query = (
            select(event_log)
            .where(event_log.c.run_id == run_id, event_log.c.seq > after_seq)
            .order_by(event_log.c.seq)
        )
        if through_seq is not None:
            query = query.where(event_log.c.seq <= through_seq)
        if limit is not None:
            query = query.limit(limit)

        with self.engine.connect() as connection:
            rows = connection.execute(query).mappings().all()

        # Envelope.from_dict validates the payload on read as well as before append, so a
        # non-canonical value that somehow reached the store is caught rather than folded.
        return [Envelope.from_dict(dict(row)) for row in rows]

    def sequence_density(self, run_id: str) -> tuple[int, int]:
        """(count, max) for the run. A gap between them localises a corrupt tail."""
        with self.engine.connect() as connection:
            row = connection.execute(
                select(
                    func.count(event_log.c.seq),
                    func.coalesce(func.max(event_log.c.seq), 0),
                ).where(event_log.c.run_id == run_id)
            ).first()
        return (int(row[0]), int(row[1]))


# =========================================================================
# The single writer (R35)
# =========================================================================


@dataclass(slots=True)
class _WriteJob:
    run_id: str
    emitted: list[Any]
    lease_handle: LeaseHandle
    rules_ver: str
    tick: int
    done: threading.Event = field(default_factory=threading.Event)
    result: AppendResult | None = None
    error: BaseException | None = None


class StoreWriter:
    """One queue, one worker, one tick per transaction.

    Per-run tick tasks enqueue a batch; this drains them one at a time. That is what makes
    sole-writer true at the *transaction* level rather than merely at the process level —
    with more than one run active, concurrent appends could otherwise make a later
    sequence visible before an earlier one.
    """

    def __init__(self, store: LogStore) -> None:
        self.store = store
        self._queue: queue.Queue[_WriteJob | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._appends = 0

    @property
    def appends(self) -> int:
        return self._appends

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._drain, name="store-writer", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._thread is None:
            return
        self._queue.put(None)
        self._thread.join(timeout=5)
        self._thread = None

    def _drain(self) -> None:
        while True:
            job = self._queue.get()
            if job is None:
                return
            try:
                job.result = self.store.append_tick(
                    run_id=job.run_id,
                    emitted=job.emitted,
                    lease_handle=job.lease_handle,
                    rules_ver=job.rules_ver,
                    tick=job.tick,
                )
                self._appends += 1
            except BaseException as exc:  # noqa: BLE001 - relayed to the submitter
                job.error = exc
            finally:
                job.done.set()

    def submit(
        self,
        run_id: str,
        emitted: list[Any],
        lease_handle: LeaseHandle,
        rules_ver: str,
        tick: int,
        timeout: float = 30.0,
    ) -> AppendResult:
        """Enqueue a tick's events and wait for the commit.

        Blocking is correct here: the tick loop must know the events are durable before
        anything is published (R20), and no event may reach a subscriber before it is.
        """
        if self._thread is None:
            raise StoreError("the store writer is not running; call start() first")

        job = _WriteJob(
            run_id=run_id,
            emitted=emitted,
            lease_handle=lease_handle,
            rules_ver=rules_ver,
            tick=tick,
        )
        self._queue.put(job)

        if not job.done.wait(timeout=timeout):
            raise StoreError(f"the store writer did not commit within {timeout}s")
        if job.error is not None:
            raise job.error
        assert job.result is not None
        return job.result


__all__ = [
    "AppendResult",
    "DdlVersionMismatch",
    "FORK_PREFIX_MAX_EVENTS",
    "DuplicateAnswer",
    "FencedOut",
    "ForkResult",
    "LogStore",
    "RunAlreadyTerminated",
    "SequenceCollision",
    "StoreError",
    "StoreWriter",
    "is_append_only_refusal",
    "make_engine",
]
