"""The tick loop: the kernel's clock, and the only thing that appends.

Eight decisions here are load-bearing, and each prevents a failure that is hard to diagnose
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

**One lock per run, and it is a `threading.Lock`** (R13). The command route is a synchronous
FastAPI route, so Starlette runs it on a worker thread; `_advance` runs on an anyio worker
thread. Both touch `run.state`, and until this nothing in `backend/` outside tests held a lock
at all. An `asyncio.Lock` would be the wrong primitive rather than a slower one: it is not
thread-safe, and its `acquire` is a coroutine that has to be awaited on the event loop — which
is not where either holder runs, so from a worker thread it would be no lock at all. A plain
`Lock` rather than an `RLock` because neither path re-enters. It is a per-run *attribute* rather
than a private detail so that a multi-run operation can take two of them in run-id order (R22),
which is what U17 will need. And it is taken **per quantum rather than across the batch**: a
partly advanced batch is a consistent state, while holding across 120 quanta would make a
command wait for 120 append round-trips.

**Nothing slow happens while that lock is held.** No append, no store round-trip, no wait, and
— when U10 puts a director's answer on this path — no provider call. The tick loop needs the
lock dozens of times a batch for about ten microseconds each, and a blocking call inside it does
not raise, it just makes the clock run slow, which is the failure this file's diagnostics were
built to explain rather than to cause. The rule has one consequence worth stating: the single
command that reads the *whole* state runs against a copy taken under the lock rather than under
the lock itself. `test_no_store_round_trip_or_wait_happens_inside_the_lock` reads this file to
keep the rule true.

**The clock's worker slots and a comparison's are different pools, and readiness asks whether
the clock is *moving*** (R14). Both halves close one failure. `anyio.to_thread.run_sync` draws
from a process-wide 40-slot limiter, and until this the tick loop, the lease heartbeat and every
synchronous FastAPI route drew from that one limiter — including the comparison route, whose
handler is about a quarter of a second of pure Python per option and up to six options per
command. Measured before the change, at rate 3 with the horizon a branch actually runs to: one
comparison at a time costs the clock nothing (947-960 permille of nominal), eight at once costs
it real time (894-926, and sim-time lag appears), and sixteen at once halves the clock
(683-744 permille, 558-689 ticks of lag). Nothing raises while that happens; the clock just runs
slow.

So there are two limiters and they are deliberately different primitives, because they are
acquired from different worlds. The clock's is an `anyio.CapacityLimiter` sized at one slot per
run plus one for the heartbeat, taken by `to_thread.run_sync` from the event loop — a dedicated
limiter is not merely a bigger allowance, it is a *disjoint* one: with the default limiter fully
borrowed, a hop on its own limiter still starts immediately. Branch execution's is a
`threading.BoundedSemaphore`, because it is acquired on a request thread that may have no event
loop at all — an `anyio.CapacityLimiter` has no blocking synchronous `acquire`, and releasing one
from a worker thread would touch event-loop objects from the wrong thread. Saturating it makes a
comparison *queue*, holding no GIL while it waits, rather than compete.

Readiness is the other half, and without it the first half would be invisible. `healthy()` asked
whether the tick task was `done()`, which a starved clock is not — it is alive, waiting for a
worker slot or for the GIL, and reporting ready. It now asks when sim-time last moved, so a
kernel whose clock has stopped says so and names the tick it stopped at.

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
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import anyio.to_thread

from contracts.envelope import Envelope, EventKind
from kernel import lease as lease_module
from kernel.store import LogStore, RunAlreadyTerminated, StoreWriter
from servicekit import logging as svclog
from simcore import log as folder
from simcore import snapshot as snapshotting
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

#: How many comparisons may be stepping branches at the same instant (R14).
#:
#: **The number is small because the constraint is the GIL, not the CPU.** A branch is pure
#: Python, so extra concurrency does not make the work finish sooner; it divides one interpreter
#: further and takes the share out of the tick loop, which wants the GIL for well under a
#: millisecond in every fifty and can only wait its turn for it.
#:
#: Sized by measurement rather than by argument. Sixteen six-option comparisons in flight at rate
#: 3, three samples each, on the machine and the harness U4's floors were taken on — the clock's
#: observed ticks against nominal, and the sim-time lag over the window:
#:
#:     slots  40 (the shipped default limiter)  683, 699, 770 permille   502-603 ticks of lag
#:     slots  16                                686, 686, 703 permille   561-596 ticks of lag
#:     slots   8                                898, 908, 927 permille    82-198 ticks of lag
#:     slots   4                                978, 982, 982 permille         0 ticks of lag
#:     slots   2                                967, 971, 972 permille         0 ticks of lag
#:     slots   1                                959, 962, 965 permille         0 ticks of lag
#:
#: The cliff is between four and eight, so two sits a factor of four inside it. One, two and four
#: are indistinguishable in what matters — no lag at all, and every figure above U4's floors — so
#: the choice among them is settled on the other two counts rather than on the clock:
#:
#: *Throughput says fewer.* Comparisons completed in the same window fell as slots rose: 19 at one
#: slot, 18 at two, 16 at four. Pure-Python parallelism has negative returns, so a bigger pool
#: makes every caller wait longer for the same total work.
#:
#: *Independence says more than one.* A single slot is a process-wide mutex by another name, and it
#: would make a comparison on one run wait for a comparison on another with nothing measured to
#: say that is necessary. Two is the smallest number that keeps two runs from serialising.
#:
#: This bounds concurrency, not cost. One comparison is still about 1.3 seconds of branch stepping
#: at six options; shortening *that* is `MAX_BRANCH_DAYS`' job and not this one.
BRANCH_SLOTS = 2

#: The limiter branch execution draws from, and nothing else does.
#:
#: Process-wide rather than per-runtime, because the resource it rations is process-wide: there is
#: one GIL, and two `KernelRuntime`s in one process would otherwise grant themselves a pool each.
#: A `BoundedSemaphore` rather than a `Semaphore` so an unbalanced release raises here instead of
#: quietly widening the pool.
#:
#: **Acquiring it blocks the calling thread, so it must never be acquired on the event loop.** The
#: command route is a synchronous FastAPI route and therefore runs on a worker thread, which is
#: what makes that safe; a future `async` caller has to hop to a thread first, or it will block
#: every request and every WebSocket send on the queue this limiter is here to create.
BRANCH_LIMITER = threading.BoundedSemaphore(BRANCH_SLOTS)

#: Clock slots beyond one per run: the lease heartbeat's.
#:
#: One is exactly enough and the arithmetic is worth stating. A run's loop awaits its own
#: `_advance`, so a run can never want two slots at once; `_maybe_zero_idle_rate`'s hop is on the
#: same awaited path, so it wants the run's slot rather than another. The heartbeat is the only
#: other holder, and it is one task for the whole process.
CLOCK_SLOTS_BESIDES_RUNS = 1

#: How long sim-time may stand still on a run whose rate is non-zero before readiness calls it
#: stalled.
#:
#: Sized against the readiness probe rather than against the loop. A healthy loop progresses every
#: `WAKE_INTERVAL_SECONDS`, so anything above a second would do on that side; what sets the number
#: is that `docker-compose.yml` probes `/status` every 5s with 6 retries. A threshold below the
#: probe interval reports stalls the prober cannot see twice in a row, which is flapping rather
#: than detection — and a 120-quantum batch against a slow Postgres is 120 append round-trips,
#: which is the legitimate pause this must not call a stall.
#:
#: At 6 retries the container is left alone for 30 seconds before it is restarted, so a transient
#: costs a warning rather than a restart loop.
CLOCK_STALL_SECONDS = 5.0


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
    #: How long sim-time has stood still. The direct answer to "did the clock stop", and the
    #: figure readiness decides on — `achieved_multiplier_permille` reads 1000 through a stall of
    #: several wall seconds, so it cannot be that figure.
    seconds_since_last_tick: int
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
            "seconds_since_last_tick": self.seconds_since_last_tick,
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
    #: The one lock between a command and a tick (R13). See the module docstring for why it is
    #: a `threading.Lock`, why it is taken per quantum, and why nothing slow may happen inside
    #: it. Public, so a multi-run operation can take two in run-id order (R22).
    lock: threading.Lock = field(default_factory=threading.Lock)
    #: Persisted on the run row. 0 is paused.
    rate: int = 1
    rate_effective_tick: int = 0
    #: Wall-clock instant of the last wake, from a monotonic clock.
    last_wake: float = field(default_factory=time.monotonic)
    last_wake_wall: str = ""
    #: The last tick the clock actually reached, and when it reached it, from a monotonic clock.
    #:
    #: Readiness reads these rather than the task's state (R14). "The tick task is alive" and "the
    #: clock is moving" are different facts, and a starved loop satisfies the first: it is sitting
    #: in an `await`, waiting for a worker slot or for its turn at the GIL, with nothing raised
    #: and nothing done. Sim-time is the only witness that cannot be faked by a live task.
    #:
    #: Stamped at loop start and on a resume as well as on progress, so a run that has just been
    #: unpaused gets a full stall interval before anyone calls its clock stopped.
    progress_tick: int = 0
    progress_at: float = field(default_factory=time.monotonic)
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
        #: Worker-thread slots the clock and the lease heartbeat draw from, and nothing else
        #: (R14). Grown by `ensure_loop`; never shrunk, because shrinking is where a limiter can
        #: be resized below what is already borrowed.
        #:
        #: Constructible here, outside any event loop, because anyio's limiter binds to a backend
        #: lazily on first *use* — which is the tick loop's own hop. The runtime is already
        #: loop-bound by `task` and by every subscriber queue, so this adds no new constraint.
        self.clock_slots = anyio.CapacityLimiter(1 + CLOCK_SLOTS_BESIDES_RUNS)

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
                # On the clock's own limiter (R14): a heartbeat that cannot get a worker thread
                # because commands hold every default slot lets the lease lapse, and a lapsed
                # lease is another kernel appending alongside this one.
                held = await anyio.to_thread.run_sync(
                    self._heartbeat_once, limiter=self.clock_slots
                )
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

        This is also where the clock's limiter is sized, because it is the one place that knows a
        run is about to want a slot and it is guaranteed to be on the event loop — it calls
        `create_task`, which has nowhere else to run. Sizing it from `len(self.runs)` rather than
        from a fixed constant matters: a constant would silently cap how many runs can tick at
        once, and the symptom would be a run whose clock is simply slower than the others'.
        """
        run = self.runs[run_id]
        self.clock_slots.total_tokens = max(
            self.clock_slots.total_tokens, len(self.runs) + CLOCK_SLOTS_BESIDES_RUNS
        )
        if run.task is not None and not run.task.done():
            return run

        run.stopped = False
        # Before the task exists, not inside it: a `RunLoop` built minutes ago carries a
        # construction-time stamp, and readiness asked between here and the loop's first wake
        # would read that as a clock that has been stopped for minutes.
        run.progress_tick = run.state.tick
        run.progress_at = time.monotonic()
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

        The lock covers the new rate and the tick it took effect at together, and nothing after
        (R13). Those two are one fact — "the clock changed speed here" — and read a quantum
        apart they describe a tick the rate was never in force at. The row update and the append
        are store round-trips and stay outside.
        """
        if rate < 0:
            raise ValueError("rate cannot be negative")

        run = self.runs[run_id]
        with run.lock:
            previous = run.rate
            run.rate = rate
            effective_tick = run.rate_effective_tick = run.state.tick
            if previous == 0 and rate > 0:
                # A resumed clock starts its stall interval here. Without this, a run paused for
                # ten minutes reports its clock stalled the instant it is unpaused — which is
                # true of the ten minutes and false of the run.
                run.progress_tick = run.state.tick
                run.progress_at = time.monotonic()

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
                        "tick": effective_tick,
                        "rate": rate,
                        "previous_rate": previous,
                        "effective_tick": effective_tick,
                    },
                )
            ],
            lease_handle=self.lease,
            rules_ver=RULES_VERSION,
            tick=effective_tick,
        )
        log.info(
            "rate changed",
            extra={"run": run_id, "tick": effective_tick, "rate": rate, "was": previous},
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
            #
            # On the clock's own limiter, never the shared default one (R14). The default limiter
            # is what every synchronous FastAPI route draws from, so a burst of comparisons can
            # borrow all forty of its slots and leave the clock waiting for a thread — which does
            # not raise and does not appear as lag until the catch-up clamp binds. A disjoint
            # limiter is not a larger allowance, it is one commands cannot reach into.
            try:
                envelopes = await anyio.to_thread.run_sync(
                    self._advance, run, batch, limiter=self.clock_slots
                )
            except Exception:
                # Re-raised so the task's done callback records it and readiness reports
                # unhealthy naming the failure, rather than the loop dying quietly.
                raise

            run.achieved_ticks += batch
            if run.state.tick > run.progress_tick:
                # Sim-time moved, so the clock is alive in the only sense readiness cares about.
                # Recorded from the tick rather than from the batch size, because a batch cut
                # short by `run.stopped` or a terminal reason advanced fewer quanta than it asked
                # for and the tick is what actually happened.
                run.progress_tick = run.state.tick
                run.progress_at = time.monotonic()
            self._publish(run, envelopes)

    def _advance(self, run: RunLoop, ticks: int) -> list[Envelope]:
        """Run `ticks` quanta, appending each one in its own transaction.

        One transaction per tick (R21), through the single writer (R35). Returns the committed
        envelopes so the caller can publish them — after they are durable, never before.

        **The lock is taken once per quantum and released before the append** (R13). Per quantum
        because a partly advanced batch is a consistent state, so there is nothing to gain from
        holding across up to 120 of them and a command waiting on 120 append round-trips to
        lose. Released before the append because `writer.submit` blocks on the store writer's
        commit: inside the lock, every command on this run would queue behind the store, and a
        second run's ticks would too by way of the single writer they share.

        Releasing before the append does mean a command's events can be sequenced ahead of a
        tick's even though its mutation happened after — the same window that exists today,
        since nothing held a lock across the handler and its append either. Closing *that* needs
        commands enqueued onto the tick loop rather than applied from the request thread, which
        is a different design and not this one. What is closed here is the one that corrupts: two
        threads inside `run.state` at the same instant.

        The tick each append is stamped with is read under the lock, so a command applied at
        tick 100 is recorded at tick 100 rather than at whatever the clock reached while its
        events were in the writer's queue.
        """
        committed: list[Envelope] = []

        for _ in range(ticks):
            if run.stopped or run.state.terminal_reason:
                break

            with run.lock:
                emitted = sim.step(run.state)
                at_tick = run.state.tick

                if simtime.is_day_boundary(at_tick) and at_tick > 0:
                    # A whole-state walk, so it belongs inside the lock rather than beside it:
                    # a checkpoint payload assembled across two quanta would hash to a state
                    # the run was never in, and the day checkpoint is the one event whose whole
                    # job is to be that hash.
                    emitted.append(
                        sim.Emitted(
                            kind=EventKind.DAY_CHECKPOINT,
                            payload=verifier.build_checkpoint_payload(run.state),
                        )
                    )

                if at_tick - run.last_position_echo_tick >= POSITION_ECHO_INTERVAL_TICKS:
                    run.last_position_echo_tick = at_tick
                    # The server half of the divergence detector. Not appended to the log: it is
                    # derived state, and a client that disagrees can be told so without making
                    # the disagreement a permanent fact.
                    #
                    # Inside the lock, even though it publishes: it is a state read plus a
                    # non-blocking enqueue, and outside it the echoed tick and the echoed
                    # position could come from different quanta — which is precisely the
                    # disagreement this echo exists to detect, manufactured by the detector.
                    self._echo_position(run)

            if not emitted:
                continue

            result = self.writer.submit(
                run_id=run.run_id,
                emitted=emitted,
                lease_handle=self.lease,
                rules_ver=RULES_VERSION,
                tick=at_tick,
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
        # The clock's limiter, for the same reason `_advance` uses it: this is on the tick loop's
        # own awaited path, so a slot it cannot get is the clock not running.
        await anyio.to_thread.run_sync(self.set_rate, run.run_id, 0, limiter=self.clock_slots)
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

        **Under the run's lock, and released before the append** (R13). Every handler here is a
        few microseconds of dictionary work, so the tick loop waits for a quantum's worth of
        nothing — with one exception, handled below. The append is outside the lock because
        `writer.submit` blocks on the store: inside it, the tick loop would serialise behind
        every command's commit.

        **Each handler is given the state it runs against, rather than reading `run.state`
        itself.** That is what makes the exception expressible. A comparison is the only command
        that changes nothing and still appends, and it is also the only one that reads the
        *whole* state — `snapshot.capture` walks it twice and the branches then spend about a
        third of a second on it. Holding the lock across that would stop the clock for as long
        as the branches ran, so instead the *copy* is taken under the lock and the comparison
        runs against the copy. The lock is held for the capture, about six tenths of a
        millisecond, and for nothing after it.

        That is strictly more correct than reading the live state, not merely cheaper: the
        branches, the staleness guard and the recorded tick then all describe one instant, which
        is the property `run_comparison` documents as load-bearing and could previously only
        intend. It is also why `compare._capture_of`'s retry has nothing to catch on this path —
        the copy it takes is of a state nothing else can touch.

        **And the branches themselves run inside `BRANCH_LIMITER`, outside the lock** (R14). The
        copy is what makes that possible: with the handler under the lock there would be nothing
        for a limiter to protect, because the clock would already be waiting on the lock rather
        than on a thread. Measured 779 permille under the lock against 944 beside it.
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
            kernel_pb2.ASSIGN_WORK: lambda state: (
                sim.assign_via_manager(state, decoded["item"])
                if decoded.get("via_manager")
                else sim.assign_direct(state, decoded["item"], decoded["person"])
            ),
            kernel_pb2.REASSIGN_WORK: lambda state: sim.reassign(
                state, decoded["item"], decoded["person"]
            ),
            kernel_pb2.RETURN_TO_BACKLOG: lambda state: sim.return_to_backlog(
                state, decoded["item"]
            ),
            kernel_pb2.RESOLVE_CHECKPOINT: lambda state: sim.resolve_checkpoint(
                state,
                decoded["item"],
                whole("cp_index"),
                whole("option_index"),
                in_person=bool(decoded["in_person"]),
            ),
            kernel_pb2.SUBMIT_CEO_INPUT: lambda state: sim.submit_ceo_input(
                state, whole("bitmask"), whole("at_tick")
            ),
            kernel_pb2.REQUEST_HIRE: lambda state: sim.request_hire(state, decoded["director"]),
            # `.get` rather than `[...]`: this is the one command carrying free-form text a
            # person typed, so a payload missing a key is a client mistake to answer with a
            # reason. A KeyError here would escape as a 500 — nothing above this catches
            # anything but `CommandRejected` — and `ask_person` rejects an empty person id
            # with a sentence of its own.
            kernel_pb2.ASK_PERSON: lambda state: sim.ask_person(
                state, str(decoded.get("person", "")), str(decoded.get("question", ""))
            ),
            # Runs its branches here, in the handler, and therefore *outside* the append
            # transaction the writer opens below. That placement is the whole reason a
            # comparison is affordable: the single writer holds one transaction per tick, and
            # three branches at a tenth of a second each inside it would stall every other
            # run's clock and read as a store outage that is not happening.
            #
            # It is also the reason this handler is given a copy rather than the live state: the
            # branches are outside the append transaction but they are not outside the lock
            # unless something puts them there. See `READ_ONLY` below.
            #
            # `.get` rather than `[...]` for the same reason `ask_person` uses it: a payload
            # missing a key is a client mistake to answer with a reason, and a KeyError here
            # would escape as a 500 because nothing above catches anything but CommandRejected.
            kernel_pb2.COMPARE_OPTIONS: lambda state: sim.compare_options(
                state,
                str(decoded.get("item", "")),
                whole("cp_index", -1),
                str(decoded.get("person", "")),
                whole("at_tick", 0),
                in_person=bool(decoded.get("in_person", False)),
            ),
        }

        # Commands that read state and write none of it. A comparison is the only one — step.py
        # calls it "the only command that changes nothing and still appends" — and it is also
        # the only handler costing more than a quantum, so the two facts cancel: a command that
        # mutates nothing *can* run against a copy, and one costing a third of a second must.
        READ_ONLY = frozenset({kernel_pb2.COMPARE_OPTIONS})

        handler = dispatch.get(kind)
        if handler is None:
            raise sim.CommandRejected(f"no handler for command kind {kind}")

        if kind in READ_ONLY:
            with run.lock:
                # The whole state, walked twice, with the tick loop shut out for the duration —
                # which is what makes the capture whole. `restore` re-hashes what it rebuilt and
                # refuses a mismatch, so a `SnapshotInvalid` out of here is no longer a torn read
                # to retry past: it would mean the snapshot round-trip has genuinely lost a
                # field, which is a bug to see rather than to paper over.
                instant = snapshotting.capture(run_id, run.state, through_seq=0)
            # Outside the lock and inside the branch limiter (R14). Outside the lock because U4
            # measured the alternative — the handler under the lock rather than beside it — at 779
            # permille against 944, and a limiter buys nothing while the tick loop is blocked on a
            # lock. Inside the limiter because this is the only handler whose cost is seconds
            # rather than microseconds, and a waiting comparison holds no GIL while it waits.
            with BRANCH_LIMITER:
                emitted = handler(snapshotting.restore(instant))
            applied_tick = instant.tick
        else:
            with run.lock:
                emitted = handler(run.state)
                applied_tick = run.state.tick

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
                # Read under the lock, so a command applied at tick 100 is recorded at tick 100
                # rather than at whatever the clock reached while its events sat in the writer's
                # queue. For a comparison that is a third of a second of drift removed.
                tick=applied_tick,
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

        # Under the lock: this walks `state.items`, and a dynamic item arriving on a tick
        # mid-iteration raises "dictionary changed size during iteration". A diagnostic that
        # fails when the run is busy fails exactly when it is wanted.
        with run.lock:
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
            seconds_since_last_tick=int(time.monotonic() - run.progress_at),
            sim_time_lag_ticks=run.lag_ticks,
            achieved_multiplier_permille=run.achieved_multiplier_permille,
            unresolved_checkpoints=unresolved,
            outstanding_requests=[],
            store_reachable=self._store_reachable,
            lease_held=self.lease is not None,
            terminal_reason=run.state.terminal_reason,
        )

    def healthy(self) -> tuple[bool, str]:
        """Readiness reflects tick *progress*, not tick-task liveness (R14, amending R29).

        A dead task for a run with a non-zero rate is unhealthy, and the exception is named —
        a kernel that reports healthy while its clock is stopped is worse than one that is
        down, because nothing prompts anyone to look.

        **A live task is not enough, and that was the hole.** A starved loop is neither done nor
        crashed: it is sitting in an `await`, waiting for a worker slot or for its turn at the
        GIL, with nothing raised and nothing advanced. R29 as written reported that as ready. So
        liveness is now the first question and not the only one — the second is when sim-time last
        moved, which no amount of waiting can fake.

        Sim-time rather than `achieved_multiplier_permille`, deliberately. That field computes its
        wanted ticks with `int(elapsed * ...)` and drops the remainder at every wake, so it reads
        1000 through a stall of several wall seconds; a readiness probe resting on it would report
        ready through exactly the failure this is here to catch.
        """
        now = time.monotonic()

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

            stalled_for = now - run.progress_at
            if stalled_for > CLOCK_STALL_SECONDS:
                return False, (
                    f"run {run.run_id} has rate {run.rate} and a live tick task, but sim-time "
                    f"has not moved past tick {run.progress_tick} for {stalled_for:.1f}s — the "
                    f"clock is starved rather than stopped, and a clock is called stalled after "
                    f"{CLOCK_STALL_SECONDS:.0f}s"
                )

        return True, "every run with a non-zero rate has a clock that is still moving"
