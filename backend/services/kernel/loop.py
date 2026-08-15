"""The tick loop: the kernel's clock, and the only thing that appends.

Five decisions here are load-bearing, and each prevents a failure that is hard to diagnose
from its symptom.

**One task per active run, and its lifecycle follows the run's persisted rate — not the
subscriber count.** Starting a loop on subscribe would make clock ownership conditional on a
gateway event, and would race two loops into existence on near-simultaneous subscribes. That
is the multiple-tick-loop failure arriving through the front door. Rate is run state, so a
reload does not pause the clock and a disconnect does not either.

**A strong reference is held for the task's lifetime, and cancellation is explicit.** The event
loop keeps only weak references to tasks, so a dropped handle can be collected mid-execution —
and the failure mode is not an exception, it is the simulation silently stopping. Bare
`create_task` also swallows the exception from a crashed loop, so the task is wrapped and its
exception retrieved.

**The kernel is invoked off the event loop.** FastAPI's threadpool applies only to functions
FastAPI itself calls; a synchronous `step()` invoked from inside an `async` loop runs inline and
stalls every request and every WebSocket send. Kernel invocation and every long fold go through
`anyio.to_thread.run_sync`. This keeps the loop responsive; it does not make ticks cheaper,
because the GIL still serialises pure-Python work.

**Catch-up is clamped, and read from a monotonic clock.** Elapsed real time decides how many
quanta to run, which taken naively fast-forwards a sim-week after a laptop sleeps. When the
clamp binds, the clock falls behind rather than sprinting — quanta are never skipped, so
falling behind means the clock runs slower than requested and the lag is reported.

**Nothing is published before it is durable** (R20). A committed transaction can still roll back
after power loss, so publishing before appending would leave the client's rendered world ahead
of the authoritative log with nothing able to detect the divergence.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass, field
from typing import Any

import anyio.to_thread

from contracts.envelope import Envelope, EventKind
from kernel import lease as lease_module
from kernel.store import LogStore, RunAlreadyTerminated, StoreWriter
from servicekit import logging as svclog
from simcore import log as folder
from simcore import step as sim
from simcore import time as simtime
from simcore import verify as verifier
from simcore.rates import RULES_VERSION

log = svclog.get_logger("kernel")

#: How often the loop wakes. At the base rate this is a couple of quanta per wake, which is
#: enough to amortise the worker-thread hop without holding the GIL long enough to show as
#: stream jitter.
WAKE_INTERVAL_SECONDS = 0.05

#: The most sim-time one wake may cover. One sim-day: a laptop that slept for an hour resumes
#: behind rather than sprinting a sim-week in a single excursion.
MAX_CATCHUP_TICKS = simtime.TICKS_PER_SIM_DAY

#: The most quanta one excursion into the worker thread may run. Bounded above so a single
#: excursion cannot hold the GIL past the point where stream jitter shows.
MAX_BATCH_TICKS = 120

#: After this long with no subscribers, an active run has its rate set to zero — and the change
#: is logged, so nothing happens silently.
#:
#: This closes a semantic hazard rather than an availability one: a run whose observer crashed
#: keeps burning fixed costs and consuming the authored checkpoint supply, and could reach
#: insolvency with zero decisions taken. Replay integrity is unharmed — the log is a faithful
#: record — but the report would present a browser crash and a strategy in the same shape.
IDLE_RATE_ZERO_AFTER_SECONDS = 300.0

#: How often the kernel echoes its own derived CEO position (R33).
#:
#: Golden vectors prove agreement only for the cases someone thought to vector. This is the
#: runtime half: a mis-ported clamp or a numeric drift is caught by comparison against the
#: authority rather than by hoping a vector covered it.
POSITION_ECHO_INTERVAL_TICKS = simtime.TICKS_PER_SIM_HOUR


@dataclass(slots=True)
class Diagnosis:
    """Everything needed to answer "why did the clock stop", without reading a log."""

    run_id: str
    rate: int
    rate_effective_tick: int
    subscribers: int
    tick_task_state: str
    tick_task_exception: str
    last_wake_at: str
    sim_time_lag_ticks: int
    achieved_multiplier_permille: int
    unresolved_checkpoints: list[str]
    outstanding_requests: list[dict[str, Any]]
    store_reachable: bool
    lease_held: bool
    terminal_reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "rate": self.rate,
            "rate_effective_tick": self.rate_effective_tick,
            "subscribers": self.subscribers,
            "tick_task_state": self.tick_task_state,
            "tick_task_exception": self.tick_task_exception,
            "last_wake_at": self.last_wake_at,
            "sim_time_lag_ticks": self.sim_time_lag_ticks,
            "achieved_multiplier_permille": self.achieved_multiplier_permille,
            "unresolved_checkpoints": list(self.unresolved_checkpoints),
            "outstanding_requests": list(self.outstanding_requests),
            "store_reachable": self.store_reachable,
            "lease_held": self.lease_held,
            "terminal_reason": self.terminal_reason,
        }


@dataclass(slots=True)
class RunLoop:
    """One run's clock. Exactly one of these exists per active run."""

    run_id: str
    state: sim.State
    #: Persisted on the run row. 0 is paused.
    rate: int = 1
    rate_effective_tick: int = 0
    #: Wall-clock instant of the last wake, from a monotonic clock.
    last_wake: float = field(default_factory=time.monotonic)
    last_wake_wall: str = ""
    #: Quanta the clamp prevented from running. The user-visible failure is that x3 does not
    #: deliver 3x, so this is the headline number rather than tick duration.
    lag_ticks: int = 0
    requested_ticks: int = 0
    achieved_ticks: int = 0
    #: Set when no subscriber has been attached since this instant.
    idle_since: float | None = field(default_factory=time.monotonic)
    subscribers: set[asyncio.Queue] = field(default_factory=set)
    task: asyncio.Task | None = None
    exception: BaseException | None = None
    stopped: bool = False
    last_position_echo_tick: int = 0

    @property
    def achieved_multiplier_permille(self) -> int:
        """Achieved against nominal, in per-mille. 1000 is keeping up."""
        if not self.requested_ticks:
            return 1000
        return self.achieved_ticks * 1000 // self.requested_ticks


class KernelRuntime:
    """Owns the store, the lease, the writer, and one loop per active run.

    The kernel's *first* acts are the DDL check and lease acquisition, because both are startup
    failures rather than transients: appending to a schema this kernel does not understand, or
    appending alongside a second kernel, are worse than not starting.
    """

    def __init__(self, store: LogStore) -> None:
        self.store = store
        self.writer = StoreWriter(store)
        self.lease: lease_module.LeaseHandle | None = None
        self.runs: dict[str, RunLoop] = {}
        #: Command outcomes by idempotency key, per run. A projection of what was applied, so a
        #: client whose response never arrived can resolve it rather than retrying blind (R30).
        self._outcomes: dict[str, dict[str, dict[str, Any]]] = {}
        self._heartbeat: asyncio.Task | None = None
        self._store_reachable = True

    # --- startup and shutdown --------------------------------------------

    def start(self) -> None:
        """Provision if empty, take the lease, check the schema, start the single writer.

        The order is deliberate and each step depends on the one before it.

        **Table creation first, and idempotent.** R16 requires `docker compose up` to bring the
        system up with no manual provisioning, so a kernel pointed at an empty database has to
        create the schema. It cannot be lease-gated in the strict sense, because the lease lives
        in a table that would not exist yet — but creation is `CREATE TABLE`-if-absent and never
        alters an existing table, so two kernels racing here converge rather than conflict.

        **Then the lease.** Only one kernel proceeds past this line.

        **Then the DDL version.** After creation, so a fresh store passes; after the lease, so a
        kernel that lost the race does not report a schema complaint when its real problem is
        that another kernel owns the log.

        Raises rather than degrading. A kernel that cannot hold the lease must exit non-zero and
        name the holder, because the alternative is two writers against one log.
        """
        self.store.create_all()

        with self.store.engine.begin() as connection:
            self.lease = lease_module.acquire(connection)

        self.store.check_ddl_version()

        log.info(
            "lease acquired",
            extra={"owner": self.lease.owner, "token": self.lease.token},
        )
        self.writer.start()

    async def start_background(self) -> None:
        self._heartbeat = asyncio.create_task(self._renew_lease(), name="kernel-lease-heartbeat")
        self.resume_all()

    def resume_all(self) -> list[str]:
        """Rebuild every run from its log and start the clock for the ones that were running.

        Without this the kernel holds the lease, answers its status endpoint, and advances nothing:
        `ensure_loop` had no production caller at all, so a run's clock only ever ran under test.

        A run's rate is *run state* (R18), so it is read from the row rather than defaulted —
        a restart must not pause a running run, and must not resume a paused one. A terminated run
        is skipped entirely: nothing appends after a terminal event, so a loop for it would wake
        forever with nothing to do.

        Returns the ids whose clocks were started, for the caller to log.

        One resumption failing must not stop the others. A corrupt tail in one run is that run's
        problem — folding it raises, and swallowing that here would be wrong, but so would letting
        it take down every other run in the store. It is recorded against the run instead, where
        readiness reports it.
        """
        started: list[str] = []

        for row in self.store.list_runs():
            run_id = str(row["run_id"])
            if run_id in self.runs:
                continue
            if row["terminal_reason"]:
                continue

            try:
                run = self.resume_run(run_id)
            except Exception as exc:  # noqa: BLE001 - recorded per run, never fatal to the rest
                log.error(
                    "could not resume run",
                    extra={"run": run_id, "error": f"{type(exc).__name__}: {exc}"},
                )
                continue

            if run.rate > 0:
                self.ensure_loop(run_id)
                started.append(run_id)

        if started:
            log.info("resumed run clocks", extra={"runs": ",".join(started)})
        return started

    async def stop(self) -> None:
        """Cancel every task explicitly, after the lifespan yield.

        Explicit because a dropped handle is collected mid-execution and the work stops
        silently; after the yield because cancelling before it would stop the clock while
        requests were still being served.
        """
        for run in list(self.runs.values()):
            await self.stop_run(run.run_id)

        if self._heartbeat is not None:
            self._heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._heartbeat
            self._heartbeat = None

        self.writer.stop()

        if self.lease is not None:
            with contextlib.suppress(Exception):
                with self.store.engine.begin() as connection:
                    lease_module.release(connection, self.lease)

    async def _renew_lease(self) -> None:
        while True:
            await asyncio.sleep(lease_module.HEARTBEAT_SECONDS)
            if self.lease is None:
                continue
            try:
                held = await anyio.to_thread.run_sync(self._heartbeat_once)
            except Exception as exc:  # noqa: BLE001 - the store may be down; keep trying
                self._store_reachable = False
                log.warning("lease heartbeat failed", extra={"error": str(exc)})
                continue

            self._store_reachable = True
            if not held:
                # Someone took the lease. Stop appending immediately: this kernel is fenced.
                log.error(
                    "the writer lease was taken over; halting every tick loop rather than "
                    "appending alongside another writer"
                )
                for run in list(self.runs.values()):
                    run.stopped = True

    def _heartbeat_once(self) -> bool:
        with self.store.engine.begin() as connection:
            return lease_module.heartbeat(connection, self.lease)

    # --- runs -------------------------------------------------------------

    def create_run(self, run_id: str, run_seed: int, horizon_tick: int | None = None) -> RunLoop:
        state, genesis = sim.new_run(run_seed=run_seed, horizon_tick=horizon_tick)

        self.store.create_run(
            run_id=run_id,
            run_seed=run_seed,
            rules_ver=RULES_VERSION,
            quantum_sim_seconds=simtime.QUANTUM_SIM_SECONDS,
            grid=(state.floor.cols, state.floor.rows),
            horizon_tick=state.horizon_tick,
        )
        self.writer.submit(
            run_id=run_id,
            emitted=genesis,
            lease_handle=self.lease,
            rules_ver=RULES_VERSION,
            tick=0,
        )

        run = RunLoop(run_id=run_id, state=state)
        self.runs[run_id] = run
        return run

    def resume_run(self, run_id: str) -> RunLoop:
        """Rebuild a run from its log. Sim-time and mid-path actors survive."""
        row = self.store.run_row(run_id)
        if row is None:
            raise KeyError(f"no such run: {run_id}")

        events = self.store.read_events(run_id)
        folded = folder.fold(
            events, at_live_head=True, through_tick=int(row["current_tick"])
        )

        run = RunLoop(run_id=run_id, state=folded.state, rate=int(row["rate"]))
        self.runs[run_id] = run
        return run

    def ensure_loop(self, run_id: str) -> RunLoop:
        """Start the tick task if the run's persisted rate says it should be running.

        Idempotent, and that is the point: fifty concurrent subscribes produce one task,
        because the task's existence follows the rate rather than the subscription.
        """
        run = self.runs[run_id]
        if run.task is not None and not run.task.done():
            return run

        run.stopped = False
        task = asyncio.create_task(self._run_loop(run), name=f"tick-loop-{run_id}")
        run.task = task  # strong reference, held for the task's lifetime

        def _retrieve(finished: asyncio.Task) -> None:
            # Retrieving the exception is what stops it being swallowed. Readiness then
            # reports unhealthy and names the failure (R29).
            if finished.cancelled():
                return
            exc = finished.exception()
            if exc is not None:
                run.exception = exc
                log.error(
                    "tick loop crashed",
                    extra={"run": run.run_id, "error": f"{type(exc).__name__}: {exc}"},
                )

        task.add_done_callback(_retrieve)
        return run

    async def stop_run(self, run_id: str) -> None:
        run = self.runs.get(run_id)
        if run is None:
            return
        run.stopped = True
        if run.task is not None:
            run.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await run.task
            run.task = None

    def set_rate(self, run_id: str, rate: int) -> Envelope | None:
        """Change a run's rate, and record the tick it took effect at.

        The *multiplier* deliberately never enters kernel state: storing it would make replay
        speed part of the run, and keeping it out means degrading it under lag is free.
        """
        if rate < 0:
            raise ValueError("rate cannot be negative")

        run = self.runs[run_id]
        previous = run.rate
        run.rate = rate
        run.rate_effective_tick = run.state.tick

        with self.store.engine.begin() as connection:
            from sqlalchemy import update

            from logschema import runs as runs_table

            connection.execute(
                update(runs_table).where(runs_table.c.run_id == run_id).values(rate=rate)
            )

        appended = self.writer.submit(
            run_id=run_id,
            emitted=[
                sim.Emitted(
                    kind=EventKind.RATE_CHANGED,
                    payload={
                        "tick": run.state.tick,
                        "rate": rate,
                        "previous_rate": previous,
                        "effective_tick": run.state.tick,
                    },
                )
            ],
            lease_handle=self.lease,
            rules_ver=RULES_VERSION,
            tick=run.state.tick,
        )
        log.info(
            "rate changed",
            extra={"run": run_id, "tick": run.state.tick, "rate": rate, "was": previous},
        )
        return appended.envelopes[0] if appended.envelopes else None

    # --- subscriptions ----------------------------------------------------

    def subscribe(self, run_id: str) -> asyncio.Queue:
        """Attach a read-only subscriber. Has no effect on simulation state (R18)."""
        run = self.runs[run_id]
        queue: asyncio.Queue = asyncio.Queue(maxsize=1024)
        run.subscribers.add(queue)
        run.idle_since = None
        return queue

    def unsubscribe(self, run_id: str, queue: asyncio.Queue) -> None:
        run = self.runs.get(run_id)
        if run is None:
            return
        run.subscribers.discard(queue)
        if not run.subscribers:
            run.idle_since = time.monotonic()

    def _publish(self, run: RunLoop, envelopes: list[Envelope]) -> None:
        """Deliver to subscribers. Called only after the append has committed (R20)."""
        for envelope in envelopes:
            for queue in list(run.subscribers):
                try:
                    queue.put_nowait(envelope)
                except asyncio.QueueFull:
                    # The recovery path already exists: the subscriber resumes from its
                    # sequence. Buffering unboundedly would buy nothing.
                    run.subscribers.discard(queue)
                    log.warning(
                        "subscriber fell behind and was dropped; it can resume from its "
                        "sequence",
                        extra={"run": run.run_id},
                    )

    # --- the loop itself --------------------------------------------------

    async def _run_loop(self, run: RunLoop) -> None:
        run.last_wake = time.monotonic()

        while not run.stopped:
            await asyncio.sleep(WAKE_INTERVAL_SECONDS)

            now = time.monotonic()
            elapsed = now - run.last_wake
            run.last_wake = now
            run.last_wake_wall = lease_module.utc_now_iso()

            if run.state.terminal_reason:
                return

            if run.rate == 0:
                await self._maybe_zero_idle_rate(run)
                continue

            await self._maybe_zero_idle_rate(run)
            if run.rate == 0:
                continue

            wanted = int(elapsed * simtime.TICKS_PER_WALL_SECOND_AT_BASE_RATE * run.rate)
            if wanted <= 0:
                continue

            run.requested_ticks += wanted

            clamped = min(wanted, MAX_CATCHUP_TICKS)
            if clamped < wanted:
                run.lag_ticks += wanted - clamped
                log.warning(
                    "catch-up clamped; the clock falls behind rather than sprinting",
                    extra={"run": run.run_id, "wanted": wanted, "ran": clamped},
                )

            batch = min(clamped, MAX_BATCH_TICKS)
            if batch < clamped:
                run.lag_ticks += clamped - batch

            # Off the event loop: a synchronous step invoked inline would stall every request
            # and every WebSocket send for the duration of the batch.
            try:
                envelopes = await anyio.to_thread.run_sync(self._advance, run, batch)
            except Exception:
                # Re-raised so the task's done callback records it and readiness reports
                # unhealthy naming the failure, rather than the loop dying quietly.
                raise

            run.achieved_ticks += batch
            self._publish(run, envelopes)

    def _advance(self, run: RunLoop, ticks: int) -> list[Envelope]:
        """Run `ticks` quanta, appending each one in its own transaction.

        One transaction per tick (R21), through the single writer (R35). Returns the committed
        envelopes so the caller can publish them — after they are durable, never before.
        """
        committed: list[Envelope] = []

        for _ in range(ticks):
            if run.stopped or run.state.terminal_reason:
                break

            emitted = sim.step(run.state)

            if simtime.is_day_boundary(run.state.tick) and run.state.tick > 0:
                emitted.append(
                    sim.Emitted(
                        kind=EventKind.DAY_CHECKPOINT,
                        payload=verifier.build_checkpoint_payload(run.state),
                    )
                )

            if (
                run.state.tick - run.last_position_echo_tick >= POSITION_ECHO_INTERVAL_TICKS
            ):
                run.last_position_echo_tick = run.state.tick
                # The server half of the divergence detector. Not appended to the log: it is
                # derived state, and a client that disagrees can be told so without making
                # the disagreement a permanent fact.
                self._echo_position(run)

            if not emitted:
                continue

            result = self.writer.submit(
                run_id=run.run_id,
                emitted=emitted,
                lease_handle=self.lease,
                rules_ver=RULES_VERSION,
                tick=run.state.tick,
            )
            committed.extend(result.envelopes)

        return committed

    def _echo_position(self, run: RunLoop) -> None:
        """R33: publish the kernel's own derived CEO position for the client to compare."""
        for queue in list(run.subscribers):
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(
                    {
                        "kind": "POSITION_ECHO",
                        "run_id": run.run_id,
                        "tick": run.state.tick,
                        "x_milli": run.state.ceo.x_milli,
                        "y_milli": run.state.ceo.y_milli,
                    }
                )

    async def _maybe_zero_idle_rate(self, run: RunLoop) -> None:
        """An abandoned run stops rather than burning unwatched.

        Subscribing is read-only and has no effect on state, so this is not "stop when nobody
        is looking" — it is a documented interval after which an unobserved run is paused, and
        the change is logged so nothing happens silently.
        """
        if run.rate == 0 or run.subscribers or run.idle_since is None:
            return
        if time.monotonic() - run.idle_since < IDLE_RATE_ZERO_AFTER_SECONDS:
            return

        log.info(
            "no subscribers for the idle interval; setting rate to zero",
            extra={"run": run.run_id, "tick": run.state.tick},
        )
        await anyio.to_thread.run_sync(self.set_rate, run.run_id, 0)
        run.idle_since = None

    # --- commands ---------------------------------------------------------

    def apply_command(
        self, run_id: str, kind: int, payload: bytes, command_id: str = ""
    ) -> list[Envelope]:
        """Apply a command at a tick boundary and append what it produced.

        Validation and application happen together, not in two steps: a command validated
        against a state the tick then changed would be applied against a world that no longer
        matches what it was checked against.

        A rejection raises `CommandRejected` and mutates nothing — the events list is empty,
        which is what "mutates nothing" means concretely.
        """
        from contracts import canonical
        from contracts.grpc import kernel_pb2

        run = self.runs[run_id]
        decoded = canonical.decode(payload) if payload else {}

        def whole(key: str, default: int | None = None) -> int:
            """One integer field off a client-supplied payload, or a reason why not.

            Every command here coerces at least one field with `int(...)`, and a bare `int()`
            over a value a browser chose is a 500 waiting to happen: `canonical` rejects floats
            but passes strings and nulls straight through, so `{"cp_index": "abc"}` and
            `{"cp_index": null}` both raise past the point where `CommandRejected` is caught.
            Nothing above this catches anything else, so it reaches FastAPI's default handler
            and the client gets an opaque 500 for what is a client mistake.

            `default` of `None` means the field is required, which is the distinction the older
            `[...]` versus `.get(...)` split was trying to draw and drew only for the
            missing-key half.
            """
            if key not in decoded:
                if default is not None:
                    return default
                raise sim.CommandRejected(f"this command needs {key!r} and the payload has none")
            try:
                return int(decoded[key])
            except (TypeError, ValueError):
                raise sim.CommandRejected(
                    f"{key!r} is {decoded[key]!r}, which is not a whole number"
                ) from None

        dispatch = {
            kernel_pb2.ASSIGN_WORK: lambda: (
                sim.assign_via_manager(run.state, decoded["item"])
                if decoded.get("via_manager")
                else sim.assign_direct(run.state, decoded["item"], decoded["person"])
            ),
            kernel_pb2.REASSIGN_WORK: lambda: sim.reassign(
                run.state, decoded["item"], decoded["person"]
            ),
            kernel_pb2.RETURN_TO_BACKLOG: lambda: sim.return_to_backlog(
                run.state, decoded["item"]
            ),
            kernel_pb2.RESOLVE_CHECKPOINT: lambda: sim.resolve_checkpoint(
                run.state,
                decoded["item"],
                whole("cp_index"),
                whole("option_index"),
                in_person=bool(decoded["in_person"]),
            ),
            kernel_pb2.SUBMIT_CEO_INPUT: lambda: sim.submit_ceo_input(
                run.state, whole("bitmask"), whole("at_tick")
            ),
            kernel_pb2.REQUEST_HIRE: lambda: sim.request_hire(run.state, decoded["director"]),
            # `.get` rather than `[...]`: this is the one command carrying free-form text a
            # person typed, so a payload missing a key is a client mistake to answer with a
            # reason. A KeyError here would escape as a 500 — nothing above this catches
            # anything but `CommandRejected` — and `ask_person` rejects an empty person id
            # with a sentence of its own.
            kernel_pb2.ASK_PERSON: lambda: sim.ask_person(
                run.state, str(decoded.get("person", "")), str(decoded.get("question", ""))
            ),
            # Runs its branches here, in the handler, and therefore *outside* the append
            # transaction the writer opens below. That placement is the whole reason a
            # comparison is affordable: the single writer holds one transaction per tick, and
            # three branches at a tenth of a second each inside it would stall every other
            # run's clock and read as a store outage that is not happening.
            #
            # `.get` rather than `[...]` for the same reason `ask_person` uses it: a payload
            # missing a key is a client mistake to answer with a reason, and a KeyError here
            # would escape as a 500 because nothing above catches anything but CommandRejected.
            kernel_pb2.COMPARE_OPTIONS: lambda: sim.compare_options(
                run.state,
                str(decoded.get("item", "")),
                whole("cp_index", -1),
                str(decoded.get("person", "")),
                whole("at_tick", 0),
                in_person=bool(decoded.get("in_person", False)),
            ),
        }

        handler = dispatch.get(kind)
        if handler is None:
            raise sim.CommandRejected(f"no handler for command kind {kind}")

        emitted = handler()
        for item in emitted:
            item.command_id = command_id

        if not emitted:
            return []

        try:
            result = self.writer.submit(
                run_id=run_id,
                emitted=emitted,
                lease_handle=self.lease,
                rules_ver=RULES_VERSION,
                tick=run.state.tick,
            )
        except RunAlreadyTerminated as ended:
            # The run ended between this command being checked and its events being written.
            # Every caller already checks for a terminal run before dispatching, so this is the
            # race rather than the ordinary case — and it is a real window for a comparison,
            # which spends over a second between its guard and its append. The store is right
            # to refuse; what was wrong is that the refusal reached the client as an opaque 500
            # instead of the sentence it already carries.
            raise sim.CommandRejected(str(ended)) from None

        self._outcomes.setdefault(run_id, {})
        return result.envelopes

    def record_outcome(
        self, run_id: str, idempotency_key: str, applied_tick: int, produced: list[int]
    ) -> None:
        """Remember a command's outcome so a reconnecting client can resolve it (R30)."""
        self._outcomes.setdefault(run_id, {})[idempotency_key] = {
            "applied_tick": applied_tick,
            "produced_seq": list(produced),
        }

    def outcome_by_key(self, run_id: str, idempotency_key: str) -> dict[str, Any] | None:
        return self._outcomes.get(run_id, {}).get(idempotency_key)

    # --- fork and export --------------------------------------------------

    def fork(self, parent_run_id: str, at_seq: int):
        """Enqueued as a command and committed inside the lease-fenced transaction."""
        import uuid

        child_run_id = f"{parent_run_id}-fork-{uuid.uuid5(uuid.NAMESPACE_URL, f'{parent_run_id}:{at_seq}').hex[:8]}"
        return self.store.fork_run(
            parent_run_id=parent_run_id,
            at_seq=at_seq,
            child_run_id=child_run_id,
            lease_handle=self.lease,
        )

    def export(self, run_id: str) -> tuple[bytes, str]:
        from simcore import export as exporter
        from simcore import hashing

        run = self.runs.get(run_id)
        events = self.store.read_events(run_id)
        row = self.store.run_row(run_id)
        through_tick = run.state.tick if run else int(row["current_tick"]) if row else 0

        if run is not None:
            state = run.state
        else:
            state = folder.fold(
                events, at_live_head=False, through_tick=through_tick
            ).state

        state_hash = hashing.state_hash(sim.snapshot(state)).overall
        artifact = exporter.export_run(
            run_id=run_id, events=events, through_tick=through_tick, state_hash=state_hash
        )
        return artifact, state_hash

    # --- diagnosis --------------------------------------------------------

    def diagnose(self, run_id: str) -> Diagnosis:
        """Why did the clock stop? Answered without anyone reading a log."""
        run = self.runs[run_id]

        if run.task is None:
            task_state = "absent"
        elif run.task.cancelled():
            task_state = "cancelled"
        elif run.task.done():
            task_state = "finished"
        else:
            task_state = "running"

        unresolved = [
            f"{item.id}:cp{item.decisions and len(item.decisions) or 0}"
            for item in run.state.items.values()
            if item.status == sim.STATUS_BLOCKED
        ]

        return Diagnosis(
            run_id=run_id,
            rate=run.rate,
            rate_effective_tick=run.rate_effective_tick,
            subscribers=len(run.subscribers),
            tick_task_state=task_state,
            tick_task_exception=(
                f"{type(run.exception).__name__}: {run.exception}" if run.exception else ""
            ),
            last_wake_at=run.last_wake_wall,
            sim_time_lag_ticks=run.lag_ticks,
            achieved_multiplier_permille=run.achieved_multiplier_permille,
            unresolved_checkpoints=unresolved,
            outstanding_requests=[],
            store_reachable=self._store_reachable,
            lease_held=self.lease is not None,
            terminal_reason=run.state.terminal_reason,
        )

    def healthy(self) -> tuple[bool, str]:
        """Readiness reflects tick-task liveness (R29).

        A dead task for a run with a non-zero rate is unhealthy, and the exception is named —
        a kernel that reports healthy while its clock is stopped is worse than one that is
        down, because nothing prompts anyone to look.
        """
        for run in self.runs.values():
            if run.rate == 0 or run.state.terminal_reason:
                continue
            if run.task is None or run.task.done():
                detail = (
                    f"{type(run.exception).__name__}: {run.exception}"
                    if run.exception
                    else "the tick task is not running"
                )
                return False, f"run {run.run_id} has rate {run.rate} but {detail}"
        return True, "every run with a non-zero rate has a live tick task"
