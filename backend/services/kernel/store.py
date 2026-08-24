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

from sqlalchemy import Engine, create_engine, event, func, insert, literal, select, update
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


def prefix_bound_refusal(through_seq: int, prefix_size: int, bound: int) -> str:
    """The sentence a fork above the bound is refused with, written once.

    Two callers check this: the kernel, so an oversized prefix is refused before it pays for a
    fold, and `fork_run` itself, inside the transaction, which is where the count is
    authoritative. Two checks are correct; two sentences would be two things to keep in step.
    """
    return (
        f"the prefix at sequence {through_seq} holds {prefix_size} events, above the bound of "
        f"{bound}. Refusing rather than holding the single writer in one long transaction, "
        "which would stall every other run's ticks and read as a store outage that is not "
        "happening."
    )


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
    #: The tick the child was born at — the tick of its fork point, never the parent's present
    #: one. See `fork_run` for what the parent's present tick would cost.
    child_tick: int = 0
    #: The lineage the child belongs to, copied from the parent (R21).
    lineage_root_id: str = ""
    #: What the divergence appended, committed in the same transaction as the copy. Empty when
    #: the fork carried no divergence, and empty on `existed` — those envelopes were published
    #: by the call that first made this child.
    envelopes: list[Envelope] = field(default_factory=list)
    #: Whether this call found the child rather than making it. The retry-after-a-restart case:
    #: the gateway's ledger is in-memory, so a retried fork reaches the store, and the store is
    #: what makes it idempotent (M47).
    existed: bool = False

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


def _write_events(
    connection: Any,
    *,
    run_id: str,
    next_seq: int,
    emitted: list[Any],
    rules_ver: str,
    tick: int,
    fail_between_events: bool = False,
) -> list[Envelope]:
    """Build and insert a contiguous block of events, inside a transaction already open.

    Shared by the two things that append: a tick, and the divergence a fork commits alongside
    its copy. One function rather than two because the envelope is the log's contract — the
    sequence, the schema version, the canonical payload check — and a second copy of it in the
    fork path would be a second place for that contract to be almost right.

    It opens no transaction and takes no lease: both belong to the caller, which is what lets
    a fork's copy and its divergence be one commit rather than two.
    """
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

    return envelopes


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

                envelopes = _write_events(
                    connection,
                    run_id=run_id,
                    next_seq=next_seq,
                    emitted=emitted,
                    rules_ver=rules_ver,
                    tick=tick,
                    fail_between_events=fail_between_events,
                )

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
        through_seq: int,
        child_run_id: str,
        lease_handle: LeaseHandle,
        *,
        child_tick: int | None = None,
        emitted: list[Any] | None = None,
        rules_ver: str = "",
        prefix_bound: int = FORK_PREFIX_MAX_EVENTS,
    ) -> ForkResult:
        """Copy a parent's log prefix into a new run, and diverge it, in one transaction.

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

        **`emitted` is the divergence, and it commits here rather than in a second append.**
        A child that carried its parent's prefix and nothing else is a plausible-looking run
        that is not a fork — it is a paused copy — and the retry that would fix it finds the
        child row already there and reports success. So the two are one transaction: a fork
        either has its different decision or it never happened, and the existence of the child
        row is therefore a complete fork rather than a stage of one.

        **`child_tick` is the tick at the fork point, and passing the parent's present tick is
        the defect this parameter exists to have a name for.** The child row used to inherit
        `current_tick`, so a child forked at day four while the parent had reached day twelve
        resumed by folding the copied prefix eight days forward with no inputs — a run that
        looks like a fork and is not the fork point. `None` means the highest tick the copied
        prefix holds, which is the honest answer when there is no divergence to place; the
        kernel passes the tick of the decision being reconsidered, so the child diverges at the
        same instant its parent did.

        **A child id that already exists is returned rather than raised on.** The gateway's
        deduplicating ledger is in memory, so a fork retried after a restart reaches this far;
        because the id is minted from the caller's idempotency key it lands on the child the
        first attempt made, and the retry's answer is that child (M47).
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

            existing = connection.execute(
                select(runs).where(runs.c.run_id == child_run_id)
            ).mappings().first()
            if existing is not None:
                return ForkResult(
                    child_run_id=child_run_id,
                    copied_through_seq=int(existing["forked_at_seq"] or 0),
                    child_tick=int(existing["current_tick"]),
                    lineage_root_id=str(existing["lineage_root_id"]),
                    existed=True,
                )

            prefix = connection.execute(
                select(
                    func.count(event_log.c.seq),
                    func.coalesce(func.max(event_log.c.tick), 0),
                ).where(event_log.c.run_id == parent_run_id, event_log.c.seq <= through_seq)
            ).first()
            prefix_size, prefix_tick = int(prefix[0]), int(prefix[1])

            if prefix_size == 0:
                raise StoreError(
                    f"run {parent_run_id} has no events at or before sequence {through_seq}"
                )
            if prefix_size > prefix_bound:
                return ForkResult(
                    child_run_id="",
                    copied_through_seq=0,
                    refusal=prefix_bound_refusal(through_seq, prefix_size, prefix_bound),
                )

            born_at = prefix_tick if child_tick is None else child_tick
            lineage_root_id = str(parent["lineage_root_id"])

            # The child inherits seed, quantum, grid and horizon. Horizon especially: it is
            # chosen at genesis and immutable, so a fork cannot outlive its parent's bound.
            #
            # It inherits neither `terminal_seq` nor `terminal_reason`, and that is the whole
            # of what makes forking an ended timeline work: the fork point is before the
            # terminal event, so the child is a run that has not ended yet.
            connection.execute(
                insert(runs).values(
                    run_id=child_run_id,
                    run_seed=parent["run_seed"],
                    current_tick=born_at,
                    head_seq=through_seq,
                    rate=0,  # a fresh child starts paused; starting it is a decision
                    rules_ver=parent["rules_ver"],
                    quantum_sim_seconds=parent["quantum_sim_seconds"],
                    grid_cols=parent["grid_cols"],
                    grid_rows=parent["grid_rows"],
                    horizon_tick=parent["horizon_tick"],
                    parent_run_id=parent_run_id,
                    forked_at_seq=through_seq,
                    # R21's second half, and the line U9 left for this unit: a child belongs
                    # to its parent's lineage rather than founding one of its own. It is what
                    # makes the HUD's aggregate span a lineage, and it is what makes U12's
                    # response cache — keyed on `(cache_key, lineage_root_id)` — reach a
                    # parent's entry from a child, which until now it could not.
                    lineage_root_id=lineage_root_id,
                    created_at=utc_now_iso(),
                )
            )

            # One set-based insert rather than one round-trip per event. At the prefix bound
            # that is the difference between a statement and five thousand of them, inside a
            # transaction that holds the store's write lock — and on SQLite the loser of that
            # contention surfaces as a killed tick loop rather than as a slow fork.
            copied_columns = [column.name for column in event_log.columns]
            connection.execute(
                insert(event_log).from_select(
                    copied_columns,
                    select(
                        *[
                            literal(child_run_id).label("run_id")
                            if column.name == "run_id"
                            else column
                            for column in event_log.columns
                        ]
                    )
                    .where(
                        event_log.c.run_id == parent_run_id,
                        event_log.c.seq <= through_seq,
                    )
                    .order_by(event_log.c.seq),
                )
            )

            envelopes: list[Envelope] = []
            if emitted:
                envelopes = _write_events(
                    connection,
                    run_id=child_run_id,
                    next_seq=through_seq + 1,
                    emitted=emitted,
                    rules_ver=rules_ver,
                    tick=born_at,
                )
                connection.execute(
                    update(runs)
                    .where(runs.c.run_id == child_run_id)
                    .values(head_seq=through_seq + len(emitted))
                )

            return ForkResult(
                child_run_id=child_run_id,
                copied_through_seq=through_seq,
                child_tick=born_at,
                lineage_root_id=lineage_root_id,
                envelopes=envelopes,
            )

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

    def prefix_size(self, run_id: str, through_seq: int) -> int:
        """How many events a fork at this sequence would copy.

        Asked before the prefix is read, so an oversized one is refused without being pulled into
        memory first — and so the refusal can name the real count rather than the bound plus one,
        which is all a limited read could say.
        """
        with self.engine.connect() as connection:
            return int(
                connection.execute(
                    select(func.count(event_log.c.seq)).where(
                        event_log.c.run_id == run_id, event_log.c.seq <= through_seq
                    )
                ).scalar_one()
            )

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

    def perform(self, store: LogStore) -> AppendResult:
        return store.append_tick(
            run_id=self.run_id,
            emitted=self.emitted,
            lease_handle=self.lease_handle,
            rules_ver=self.rules_ver,
            tick=self.tick,
        )


@dataclass(slots=True)
class _ForkJob:
    """A fork, queued behind the ticks rather than racing them (R22).

    The copy is a write, and on SQLite every write takes the database's one write lock. Run
    out of band it contends with the writer for that lock, and the loser is whichever
    transaction asked second — which is a killed tick loop about as often as it is a failed
    fork. Queued here it simply waits its turn, and the clock waits one transaction for it,
    which is what the prefix bound is sized to keep short.
    """

    parent_run_id: str
    through_seq: int
    child_run_id: str
    lease_handle: LeaseHandle
    child_tick: int | None
    emitted: list[Any]
    rules_ver: str
    prefix_bound: int
    done: threading.Event = field(default_factory=threading.Event)
    result: ForkResult | None = None
    error: BaseException | None = None

    def perform(self, store: LogStore) -> ForkResult:
        return store.fork_run(
            parent_run_id=self.parent_run_id,
            through_seq=self.through_seq,
            child_run_id=self.child_run_id,
            lease_handle=self.lease_handle,
            child_tick=self.child_tick,
            emitted=self.emitted,
            rules_ver=self.rules_ver,
            prefix_bound=self.prefix_bound,
        )


class StoreWriter:
    """One queue, one worker, one tick per transaction.

    Per-run tick tasks enqueue a batch; this drains them one at a time. That is what makes
    sole-writer true at the *transaction* level rather than merely at the process level —
    with more than one run active, concurrent appends could otherwise make a later
    sequence visible before an earlier one.

    A fork is the second job kind, for the same reason there is a queue at all rather than
    because a fork is a tick. See `_ForkJob`.
    """

    def __init__(self, store: LogStore) -> None:
        self.store = store
        self._queue: queue.Queue[_WriteJob | _ForkJob | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._appends = 0
        self._forks = 0

    @property
    def appends(self) -> int:
        return self._appends

    @property
    def forks(self) -> int:
        return self._forks

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
                job.result = job.perform(self.store)
                if isinstance(job, _ForkJob):
                    self._forks += 1
                else:
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

    def submit_fork(
        self,
        parent_run_id: str,
        through_seq: int,
        child_run_id: str,
        lease_handle: LeaseHandle,
        *,
        child_tick: int | None = None,
        emitted: list[Any] | None = None,
        rules_ver: str = "",
        prefix_bound: int = FORK_PREFIX_MAX_EVENTS,
        timeout: float = 30.0,
    ) -> ForkResult:
        """Enqueue a fork and wait for the commit.

        Blocking for the same reason `submit` is: the caller has to know the child is durable
        before it registers a run against it, and a run registered against a copy that never
        committed is a run whose next command raises.
        """
        if self._thread is None:
            raise StoreError("the store writer is not running; call start() first")

        job = _ForkJob(
            parent_run_id=parent_run_id,
            through_seq=through_seq,
            child_run_id=child_run_id,
            lease_handle=lease_handle,
            child_tick=child_tick,
            emitted=list(emitted or ()),
            rules_ver=rules_ver,
            prefix_bound=prefix_bound,
        )
        self._queue.put(job)

        if not job.done.wait(timeout=timeout):
            raise StoreError(f"the store writer did not commit the fork within {timeout}s")
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
    "prefix_bound_refusal",
]
