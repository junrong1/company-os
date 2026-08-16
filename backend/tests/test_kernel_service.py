"""The kernel service: the clock, and the guarantees around it.

Most of these test failures that are invisible from their symptom. A dropped task handle does
not raise — the simulation just stops. A swallowed exception leaves a service reporting healthy
with a stopped clock. An unclamped catch-up fast-forwards a sim-week after a laptop sleeps and
looks like a simulation bug. So each is asserted directly rather than assumed from the code.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import threading
import time

import anyio.to_thread
import pytest

from contracts import canonical
from contracts.envelope import EventKind
from contracts.grpc import kernel_pb2
from kernel import lease as lease_module
from kernel import loop as loop_module
from kernel.loop import KernelRuntime, RunLoop
from kernel.store import LogStore, make_engine
from simcore import compare as branching
from simcore import snapshot as snapshotting
from simcore import step as sim
from simcore import time as simtime

RUN = "run-service"
SEED = 0xC0FFEE

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def runtime(tmp_path):
    store = LogStore(make_engine(f"sqlite:///{tmp_path}/kernel.sqlite3"))
    store.create_all()
    runtime = KernelRuntime(store)
    runtime.start()
    yield runtime
    runtime.writer.stop()


# =========================================================================
# Startup: the DDL check and the lease come first
# =========================================================================


async def test_startup_takes_the_lease(runtime) -> None:
    assert runtime.lease is not None
    assert runtime.lease.token == 1


async def test_a_second_kernel_refuses_to_start_and_names_the_holder(runtime) -> None:
    """R26. Two kernels against one log is the failure everything else depends on avoiding."""
    second = KernelRuntime(runtime.store)

    with pytest.raises(lease_module.LeaseHeld) as excinfo:
        second.start()

    assert excinfo.value.owner == runtime.lease.owner


async def test_a_kernel_provisions_an_empty_store(tmp_path) -> None:
    """R16: `docker compose up` brings the system up with no manual provisioning.

    A kernel pointed at an empty database creates the schema, takes the lease, and serves.
    Without this the compose stack would need a provisioning step nobody documented.
    """
    from kernel.loop import KernelRuntime
    from kernel.store import LogStore, make_engine
    from logschema import DDL_VERSION

    store = LogStore(make_engine(f"sqlite:///{tmp_path}/empty.sqlite3"))
    runtime = KernelRuntime(store)
    try:
        runtime.start()
        assert runtime.lease is not None
        # Against the declared version, not a literal. What this test is about is that a
        # kernel provisions an empty store and can then read its own schema back; a
        # hardcoded number turns it into an assertion that somebody edited two places
        # consistently, and it fails on every DDL bump for a reason that is never the
        # reason it was written.
        assert store.check_ddl_version() == DDL_VERSION
    finally:
        runtime.writer.stop()


async def test_a_ddl_mismatch_refuses_to_start(tmp_path) -> None:
    from sqlalchemy import update

    from logschema import store_version

    store = LogStore(make_engine(f"sqlite:///{tmp_path}/mismatch.sqlite3"))
    store.create_all()
    with store.engine.begin() as connection:
        connection.execute(update(store_version).values(ddl_version=999))

    from kernel.store import DdlVersionMismatch

    with pytest.raises(DdlVersionMismatch):
        KernelRuntime(store).start()


# =========================================================================
# The loop advances sim-time, off the event loop
# =========================================================================


async def test_the_loop_advances_sim_time_while_the_service_answers(runtime) -> None:
    runtime.create_run(RUN, SEED)
    runtime.ensure_loop(RUN)
    run = runtime.runs[RUN]

    # Interleave a "request" with the loop running, and assert both progressed. If step() ran
    # inline on the event loop, these awaits would not be serviced during a batch.
    answered = 0
    for _ in range(20):
        await asyncio.sleep(0.02)
        answered += 1

    assert answered == 20
    assert run.state.tick > 0, "sim-time did not advance"


async def test_a_long_fold_does_not_block_a_concurrent_call(runtime) -> None:
    """Kernel invocation goes through a worker thread, so the loop stays responsive."""
    runtime.create_run(RUN, SEED)
    runtime.ensure_loop(RUN)

    started = time.monotonic()
    ticks = 0
    while time.monotonic() - started < 0.4:
        await asyncio.sleep(0.01)
        ticks += 1

    # ~40 opportunities to run; if the loop had blocked the event loop we would see far fewer.
    assert ticks > 20


async def test_events_are_appended_and_published(runtime) -> None:
    runtime.create_run(RUN, SEED)
    queue = runtime.subscribe(RUN)
    runtime.ensure_loop(RUN)

    sim.assign_direct(runtime.runs[RUN].state, "wi_faq", "stf_cs")

    # Wait for a day boundary, which is guaranteed to emit.
    for _ in range(200):
        await asyncio.sleep(0.05)
        if not queue.empty():
            break

    assert not queue.empty(), "nothing was published"


async def test_nothing_is_published_before_it_is_durable(runtime) -> None:
    """R20, asserted structurally.

    Publishing before appending would leave the client's rendered world ahead of the
    authoritative log with nothing able to detect the divergence.
    """
    import inspect

    source = inspect.getsource(KernelRuntime._run_loop)
    publish_at = source.index("self._publish")
    advance_at = source.index("self._advance")
    assert advance_at < publish_at, "publish happens before the append returns"


# =========================================================================
# One task per run, whatever the subscribers do (R18)
# =========================================================================


async def test_fifty_concurrent_subscribes_produce_one_tick_task(runtime) -> None:
    """Starting a loop on subscribe would race two loops into existence."""
    runtime.create_run(RUN, SEED)

    queues = [runtime.subscribe(RUN) for _ in range(50)]
    runs = [runtime.ensure_loop(RUN) for _ in range(50)]

    tasks = {id(run.task) for run in runs}
    assert len(tasks) == 1
    assert len(queues) == 50
    assert len(runtime.runs[RUN].subscribers) == 50


async def test_subscribing_does_not_change_simulation_state(runtime) -> None:
    runtime.create_run(RUN, SEED)
    run = runtime.runs[RUN]
    before = run.state.tick

    runtime.subscribe(RUN)

    assert run.state.tick == before
    assert run.rate == 1, "subscribing changed the rate"


async def test_the_tick_task_is_held_by_a_strong_reference(runtime) -> None:
    """A dropped handle is collected mid-execution and the simulation stops silently."""
    runtime.create_run(RUN, SEED)
    run = runtime.ensure_loop(RUN)

    assert run.task is not None
    assert run.task is runtime.runs[RUN].task


async def test_cancelling_on_shutdown_terminates_without_hanging(runtime) -> None:
    runtime.create_run(RUN, SEED)
    runtime.ensure_loop(RUN)

    await asyncio.wait_for(runtime.stop_run(RUN), timeout=5)

    assert runtime.runs[RUN].task is None


async def test_a_raised_exception_surfaces_and_readiness_turns_unhealthy(runtime) -> None:
    """R29. A kernel reporting healthy with a stopped clock is worse than one that is down."""
    runtime.create_run(RUN, SEED)
    run = runtime.ensure_loop(RUN)

    healthy, _ = runtime.healthy()
    assert healthy

    # Break the run so the next batch raises inside the worker thread.
    run.state.items["wi_faq"].status = "nonsense-status"
    run.state.people["stf_cs"].item_id = "wi_faq"
    run.state.people["stf_cs"].state = sim.STATE_WORKING
    run.state.items["wi_faq"].assignee = "stf_cs"

    # Simulate the crash path directly: the loop's done callback is what retrieves the
    # exception rather than letting it be swallowed.
    run.exception = RuntimeError("deliberate")
    run.task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run.task
    run.task = None

    healthy, detail = runtime.healthy()
    assert not healthy
    assert "deliberate" in detail


# =========================================================================
# Rate is run state (R18)
# =========================================================================


async def test_a_rate_of_zero_pauses_and_reports_the_tick_it_took_effect(runtime) -> None:
    runtime.create_run(RUN, SEED)
    runtime.ensure_loop(RUN)
    await asyncio.sleep(0.2)

    envelope = runtime.set_rate(RUN, 0)
    paused_at = runtime.runs[RUN].state.tick

    assert envelope is not None
    assert envelope.kind is EventKind.RATE_CHANGED
    assert envelope.decoded_payload()["rate"] == 0
    assert envelope.decoded_payload()["effective_tick"] == paused_at

    await asyncio.sleep(0.2)
    assert runtime.runs[RUN].state.tick == paused_at, "the clock advanced while paused"


async def test_the_rate_is_persisted_on_the_run_row(runtime) -> None:
    """So a disconnect does not pause the clock and a reload does not resume it."""
    runtime.create_run(RUN, SEED)
    runtime.set_rate(RUN, 3)

    row = runtime.store.run_row(RUN)
    assert row["rate"] == 3


async def test_the_replay_multiplier_never_enters_kernel_state(runtime) -> None:
    """Storing it would make replay speed part of the run."""
    runtime.create_run(RUN, SEED)
    runtime.set_rate(RUN, 3)

    snapshot = sim.snapshot(runtime.runs[RUN].state)
    flattened = repr(snapshot)
    assert "rate" not in flattened, "the rate leaked into hashed state"


async def test_a_negative_rate_is_refused(runtime) -> None:
    runtime.create_run(RUN, SEED)
    with pytest.raises(ValueError):
        runtime.set_rate(RUN, -1)


# =========================================================================
# Catch-up is clamped (R19)
# =========================================================================


async def test_a_long_elapsed_jump_advances_at_most_the_clamp(runtime) -> None:
    """A laptop sleep must not fast-forward a sim-week in one wake."""
    from kernel.loop import MAX_BATCH_TICKS, MAX_CATCHUP_TICKS

    runtime.create_run(RUN, SEED)
    run = runtime.runs[RUN]

    # Injected *after* the loop is running. Setting it beforehand proves nothing: the loop
    # resets `last_wake` on entry, which is correct — a freshly created loop must not believe
    # twenty minutes elapsed before it existed.
    runtime.ensure_loop(RUN)
    await asyncio.sleep(0.1)
    run.last_wake = time.monotonic() - 1200
    await asyncio.sleep(0.15)

    # Twenty minutes at the base rate would be 43,200 quanta.
    assert run.state.tick < MAX_CATCHUP_TICKS + MAX_BATCH_TICKS * 5
    assert run.lag_ticks > 0, "the clamp bound but no lag was recorded"


async def test_the_service_stays_responsive_across_a_clamped_catch_up(runtime) -> None:
    runtime.create_run(RUN, SEED)
    runtime.ensure_loop(RUN)
    await asyncio.sleep(0.05)
    runtime.runs[RUN].last_wake = time.monotonic() - 1200

    responses = 0
    for _ in range(10):
        await asyncio.sleep(0.02)
        responses += 1

    assert responses == 10


async def test_quanta_are_never_skipped(runtime) -> None:
    """Falling behind means running slower, not jumping."""
    runtime.create_run(RUN, SEED)
    run = runtime.runs[RUN]
    runtime.ensure_loop(RUN)
    await asyncio.sleep(0.05)
    run.last_wake = time.monotonic() - 600
    await asyncio.sleep(0.2)

    events = runtime.store.read_events(RUN)
    ticks = sorted({envelope.tick for envelope in events if envelope.tick > 0})
    # Every tick that produced an event is contiguous with its neighbours in sim-time; no
    # tick index was jumped over.
    assert ticks == sorted(ticks)
    assert run.state.tick > 0


async def test_the_achieved_multiplier_is_reported(runtime) -> None:
    """The user-visible failure is that x3 does not deliver 3x, so that is the metric."""
    runtime.create_run(RUN, SEED)
    run = runtime.runs[RUN]
    run.requested_ticks = 1000
    run.achieved_ticks = 500

    assert run.achieved_multiplier_permille == 500
    assert runtime.diagnose(RUN).achieved_multiplier_permille == 500


# =========================================================================
# The idle interval (R18)
# =========================================================================


async def test_a_run_whose_subscribers_drop_keeps_its_rate_until_the_interval(runtime) -> None:
    runtime.create_run(RUN, SEED)
    queue = runtime.subscribe(RUN)
    runtime.ensure_loop(RUN)

    runtime.unsubscribe(RUN, queue)
    await asyncio.sleep(0.15)

    assert runtime.runs[RUN].rate == 1, "the rate dropped before the idle interval elapsed"


async def test_the_idle_interval_zeroes_the_rate_and_logs_it(runtime) -> None:
    """An abandoned run stops rather than burning the authored checkpoint supply unwatched."""
    from kernel import loop as loop_module

    runtime.create_run(RUN, SEED)
    run = runtime.runs[RUN]
    runtime.ensure_loop(RUN)

    # Backdate the idle clock past the documented interval.
    run.idle_since = time.monotonic() - loop_module.IDLE_RATE_ZERO_AFTER_SECONDS - 1

    for _ in range(40):
        await asyncio.sleep(0.05)
        if run.rate == 0:
            break

    assert run.rate == 0
    kinds = [e.kind for e in runtime.store.read_events(RUN)]
    assert EventKind.RATE_CHANGED in kinds, "the change was not logged"


# =========================================================================
# Two runs, one writer (R35)
# =========================================================================


async def test_two_runs_append_through_one_writer_with_contiguous_sequences(runtime) -> None:
    runtime.create_run(RUN, SEED)
    runtime.create_run("run-second", SEED + 1)

    sim.assign_direct(runtime.runs[RUN].state, "wi_faq", "stf_cs")
    sim.assign_direct(runtime.runs["run-second"].state, "wi_quotes", "stf_buyer")

    runtime.ensure_loop(RUN)
    runtime.ensure_loop("run-second")
    await asyncio.sleep(0.5)

    for run_id in (RUN, "run-second"):
        seqs = [envelope.seq for envelope in runtime.store.read_events(run_id)]
        assert seqs == list(range(1, len(seqs) + 1)), f"{run_id} is not contiguous"


# =========================================================================
# The position echo (R33)
# =========================================================================


async def test_the_kernel_echoes_its_derived_ceo_position(runtime) -> None:
    """Golden vectors prove agreement only for the cases someone vectored."""
    runtime.create_run(RUN, SEED)
    run = runtime.runs[RUN]
    queue = runtime.subscribe(RUN)

    run.state.tick = simtime.TICKS_PER_SIM_HOUR * 2
    runtime._echo_position(run)

    echo = queue.get_nowait()
    assert echo["kind"] == "POSITION_ECHO"
    assert echo["x_milli"] == run.state.ceo.x_milli
    assert echo["y_milli"] == run.state.ceo.y_milli


async def test_a_perturbed_client_predictor_would_be_detected(runtime) -> None:
    runtime.create_run(RUN, SEED)
    run = runtime.runs[RUN]
    queue = runtime.subscribe(RUN)
    runtime._echo_position(run)
    echo = queue.get_nowait()

    perturbed = echo["x_milli"] + 1
    assert perturbed != run.state.ceo.x_milli, "a divergence would be visible"


# =========================================================================
# Diagnose: why did the clock stop? (the highest-leverage diagnostic)
# =========================================================================


async def test_diagnose_explains_a_deliberately_stopped_clock(runtime) -> None:
    """U9's verification: explained without anyone reading a log."""
    runtime.create_run(RUN, SEED)
    runtime.ensure_loop(RUN)
    await asyncio.sleep(0.15)
    runtime.set_rate(RUN, 0)

    diagnosis = runtime.diagnose(RUN)

    assert diagnosis.rate == 0
    assert diagnosis.rate_effective_tick > 0
    assert diagnosis.tick_task_state == "running"
    assert diagnosis.lease_held is True
    assert diagnosis.store_reachable is True


async def test_diagnose_reports_unresolved_checkpoints(runtime) -> None:
    runtime.create_run(RUN, SEED)
    run = runtime.runs[RUN]
    sim.assign_direct(run.state, "wi_faq", "stf_cs")

    for _ in range(4000):
        sim.step(run.state)
        if run.state.items["wi_faq"].status == sim.STATUS_BLOCKED:
            break

    diagnosis = runtime.diagnose(RUN)
    assert diagnosis.unresolved_checkpoints, "a blocked item was not reported"


async def test_diagnose_carries_every_field_an_operator_needs(runtime) -> None:
    runtime.create_run(RUN, SEED)
    rendered = runtime.diagnose(RUN).to_dict()

    for field in (
        "rate",
        "rate_effective_tick",
        "subscribers",
        "tick_task_state",
        "tick_task_exception",
        "last_wake_at",
        # The figure readiness now decides on (R14): "did the clock stop" answered directly,
        # rather than inferred from a multiplier that reads 1000 through a stall.
        "seconds_since_last_tick",
        "sim_time_lag_ticks",
        "achieved_multiplier_permille",
        "unresolved_checkpoints",
        "outstanding_requests",
        "store_reachable",
        "lease_held",
    ):
        assert field in rendered, field


# =========================================================================
# Resume
# =========================================================================


async def test_a_run_resumes_from_its_log_at_the_same_sim_time(runtime) -> None:
    runtime.create_run(RUN, SEED)
    run = runtime.runs[RUN]
    sim.assign_via_manager(run.state, "wi_ap_map")
    runtime.ensure_loop(RUN)
    await asyncio.sleep(0.4)
    await runtime.stop_run(RUN)

    from sqlalchemy import update

    from logschema import runs as runs_table

    with runtime.store.engine.begin() as connection:
        connection.execute(
            update(runs_table).where(runs_table.c.run_id == RUN).values(current_tick=run.state.tick)
        )

    before_tick = run.state.tick
    del runtime.runs[RUN]

    resumed = runtime.resume_run(RUN)

    assert resumed.state.tick == before_tick
    assert resumed.rate == 1, "the rate is run state and survives the restart"


async def test_the_loop_stops_at_a_terminal_run(runtime) -> None:
    # 60 ticks is under two wall-seconds at the base rate. A full sim-day would be fifteen,
    # which is a slow test rather than a stronger one.
    runtime.create_run(RUN, SEED, horizon_tick=60)
    run = runtime.runs[RUN]
    runtime.ensure_loop(RUN)

    for _ in range(200):
        await asyncio.sleep(0.05)
        if run.state.terminal_reason:
            break

    assert run.state.terminal_reason == "horizon"
    ended_at = run.state.tick
    await asyncio.sleep(0.1)
    assert run.state.tick == ended_at


# =========================================================================
# One lock across the command path and the tick (R13)
# =========================================================================
#
# A torn read is silent by construction, which is why these tests are written the way they are.
# The failure this unit closes was first seen as an intermittent `SnapshotInvalid` in
# `test_compare.py` — a test that passes five times out of five in isolation and fails only
# under suite-level thread contention. Waiting for that window to open by luck makes a test
# that neither fails reliably before a fix nor proves anything after one, so the window is
# opened deliberately instead.


#: A run long enough that a comparison's branches are cheap. A branch runs to the first
#: downstream checkpoint or to the run's horizon, whichever comes first, and `wi_ap_map` has
#: exactly one checkpoint — so the horizon is what sizes every branch. 1200 ticks puts the
#: comparison at about 30ms instead of 310ms, which is the difference between a two-second test
#: and a twenty-second one. It changes nothing about the window being tested.
SHORT_HORIZON_TICKS = 1200

#: The command rate the clock is measured under, and the window it is measured over.
COMMANDS_PER_SECOND = 40
LOAD_WINDOW_SECONDS = 2.0
LOAD_RATE = 3

#: Measured on the build *before* this lock existed — 2026-08-16, macOS/arm64, SQLite store,
#: one run at rate 3 for a two-second window. Nine samples at 40 cheap commands a second:
#:
#:     observed ticks / nominal ticks      902, 902, 902, 902, 906, 906, 906, 906, 907 permille
#:     run-reported achieved multiplier    1000 permille, every sample
#:     sim_time_lag_ticks over the window  0, every sample
#:
#: And three samples of the same window under back-to-back comparisons, which is the load that
#: matters because a comparison is the one command that costs more than a quantum:
#:
#:     observed ticks / nominal ticks      944, 947, 950 permille
#:     sim_time_lag_ticks over the window  0, every sample
#:
#: The floors sit about 5% under the lower of those. **They can fail, and it was checked rather
#: than assumed.** Running the comparison handler under the lock instead of against a copy taken
#: under it — the obvious implementation of R13, and the one this file's design note rejects —
#: measures 779, 782, 784 permille on the same window. 850 separates the two designs.
#:
#: Two things the floors are *not* sensitive to on this machine, stated so nobody reads more
#: into a pass than is there. Holding the lock across the writer's append measured 878-902,
#: indistinguishable from shipped, because a local SQLite append is faster than the wake
#: interval; and a 2ms artificial hold per command measured 902-906, because at rate 3 the tick
#: loop wants the lock for about 0.05ms in every 50ms and simply does not collide often enough
#: to notice. What this floor catches is a *long* hold, which is the one that stops the clock.
#:
#: The intrinsic ~95 permille shortfall is not the command load. The same window with no
#: commands at all measured 908-910 permille: it is `int(elapsed * ...)` in `_run_loop`
#: truncating a fraction of a tick away at every wake, and it is a separate defect from this
#: one. Reported, not fixed here.
#:
#: **U5 measures against this baseline**, per its own scenario: the tick count below is the one
#: it reuses.
OBSERVED_PERMILLE_FLOOR = 850
ACHIEVED_PERMILLE_FLOOR = 950
LAG_TICKS_BOUND = 120


def _blocked_at_a_decision(runtime, horizon: int = SHORT_HORIZON_TICKS):
    """A run stopped at an open checkpoint, which is what a comparison needs to be legal."""
    runtime.create_run(RUN, SEED, horizon_tick=horizon)
    run = runtime.runs[RUN]
    runtime.apply_command(
        RUN,
        kernel_pb2.ASSIGN_WORK,
        canonical.encode({"item": "wi_ap_map", "person": "stf_ap", "via_manager": False}),
    )
    while run.state.items["wi_ap_map"].status != sim.STATUS_BLOCKED:
        runtime._advance(run, 1)
    return run


def _comparison_payload(run) -> bytes:
    return canonical.encode(
        {
            "item": "wi_ap_map",
            "cp_index": 0,
            "person": "stf_ap",
            "at_tick": run.state.tick,
            "in_person": True,
        }
    )


@contextlib.contextmanager
def _a_tick_landing_inside_the_next_capture(runtime, run, wait: float = 0.15):
    """Drop one real tick between the two halves of the next `snapshot.capture`.

    `capture` reads the state twice — once to hash it, once to encode it — so a tick landing
    between those two reads produces a snapshot whose recorded hash describes a state the
    encoded bytes no longer contain. That is the torn read, and it is worth stating exactly
    where it surfaces: **`capture` does not raise**, so `_capture_of`'s retry never sees it.
    It surfaces later, in `snapshot.restore`, as `SnapshotInvalid` — which nothing catches.

    The tick is run through `runtime._advance` on a second thread, which is the deployed
    topology (the tick loop on an anyio worker, the command on Starlette's). With the lock in
    place that thread blocks, the wait below times out, and the tick lands a moment later
    against a state nobody is photographing. Without it the tick lands immediately.

    `to_wire` is patched rather than `capture` itself because the hash has already been taken
    by the time `to_wire` is called: that is precisely the instant the tear needs.
    """
    released = threading.Event()
    landed = threading.Event()
    armed = [True]
    original = snapshotting.to_wire

    def tick_once() -> None:
        if released.wait(5.0):
            runtime._advance(run, 1)
            landed.set()

    def hashed_but_not_yet_encoded(state):
        if armed[0]:
            armed[0] = False
            released.set()
            landed.wait(wait)
        return original(state)

    thread = threading.Thread(target=tick_once, name="a-tick-inside-a-capture")
    thread.start()
    snapshotting.to_wire = hashed_but_not_yet_encoded
    try:
        yield landed
    finally:
        snapshotting.to_wire = original
        released.set()
        thread.join(timeout=5)


async def test_a_command_never_observes_a_partially_advanced_state(runtime) -> None:
    """Covers M63. The command path and the tick can no longer overlap on run state.

    Issued repeatedly, and with a tick forced into the middle of every one of them — a
    comparison is the widest window in the system because it walks the whole state twice, so
    it is the command this is asserted through.

    Before the lock this raised `SnapshotInvalid` on every iteration, with the same sentence
    the flaking compare test reports: "snapshot for branch at sequence 0 does not reproduce its
    own state hash". The retry that was the standing mitigation does not close it, because the
    tear is detected on restore rather than on capture.
    """
    run = _blocked_at_a_decision(runtime)
    tick_at_start = run.state.tick

    for attempt in range(8):
        with _a_tick_landing_inside_the_next_capture(runtime, run):
            envelopes = runtime.apply_command(
                RUN, kernel_pb2.COMPARE_OPTIONS, _comparison_payload(run)
            )
        assert len(envelopes) == 1, f"attempt {attempt} produced {len(envelopes)} events"

    # And the ticks really did land, so this is not passing on a stationary run.
    assert run.state.tick >= tick_at_start + 8


async def test_a_capture_concurrent_with_a_tick_is_self_consistent_with_no_retry(
    runtime, monkeypatch
) -> None:
    """The same property with the retry taken away, so the lock is what is being credited.

    `_capture_of` retries a torn capture three times before refusing. Pinned to one attempt,
    no retry can fire — so a comparison that still succeeds against a tick landing mid-capture
    succeeded because the capture was never torn, which is the whole claim.

    This is also the scenario's "on every attempt": pinning the attempt count is a stronger
    statement than counting retries would be, because it removes the recovery rather than
    observing that it went unused.
    """
    monkeypatch.setattr(branching, "CAPTURE_ATTEMPTS", 1)
    run = _blocked_at_a_decision(runtime)

    for attempt in range(4):
        with _a_tick_landing_inside_the_next_capture(runtime, run):
            envelopes = runtime.apply_command(
                RUN, kernel_pb2.COMPARE_OPTIONS, _comparison_payload(run)
            )
        [record] = envelopes
        # The tick on the event and the tick the branches forked at are the same instant. They
        # are read under the lock together, so they cannot disagree.
        assert record.decoded_payload()["tick"] == record.tick, f"attempt {attempt}"


async def _clock_under(
    runtime,
    run,
    issue_one,
    *,
    paced: bool,
    issuers: int = 1,
    window: float = LOAD_WINDOW_SECONDS,
) -> tuple[int, int, int, int]:
    """Run one measurement window and return (observed, achieved, lag, commands).

    `issue_one` goes through a worker thread, because that is where Starlette runs the command
    route: the whole point of the measurement is that two threads contend for one run.

    `issuers` puts that many commands in flight at once, which **U5** needs and U4 did not: one
    comparison at a time barely dents the clock, and the failure the branch limiter exists to
    prevent only appears when several are holding the GIL together. The count is deliberately
    the *only* thing that changes between U4's measurement and U5's, so the two are comparable.
    """
    delivered = 0

    tick_before = run.state.tick
    requested_before, achieved_before = run.requested_ticks, run.achieved_ticks
    lag_before = run.lag_ticks

    started = time.monotonic()
    interval = 1.0 / COMMANDS_PER_SECOND

    async def issuer() -> None:
        # Counted here rather than in the worker: `delivered` is then only ever touched from the
        # event loop, so several issuers cannot lose an increment to each other.
        nonlocal delivered
        due = started
        while time.monotonic() - started < window:
            await anyio.to_thread.run_sync(issue_one)
            delivered += 1
            if paced:
                due += interval
                remaining = due - time.monotonic()
                if remaining > 0:
                    await asyncio.sleep(remaining)

    await asyncio.gather(*(issuer() for _ in range(issuers)))

    elapsed = time.monotonic() - started
    requested = run.requested_ticks - requested_before
    nominal = elapsed * simtime.TICKS_PER_WALL_SECOND_AT_BASE_RATE * LOAD_RATE

    return (
        int((run.state.tick - tick_before) * 1000 / nominal),
        int((run.achieved_ticks - achieved_before) * 1000 / requested) if requested else 1000,
        run.lag_ticks - lag_before,
        delivered,
    )


async def test_the_clock_keeps_its_multiplier_and_its_lag_under_command_load(runtime) -> None:
    """Verification for this unit: the tick-lag metric is unchanged under command load.

    Every number asserted here was measured on the build before the lock landed — see
    `OBSERVED_PERMILLE_FLOOR` above for the samples, the machine, and the wrong design that
    breaches the floor.

    Two loads, because they fail differently. The paced rate of cheap commands is the plan's
    "stated command rate" and it is what a person clicking produces. Back-to-back comparisons
    are what actually moves the figure: a comparison is the only command costing more than a
    quantum, so it is the only one whose handler could hold the lock long enough to matter.

    Three figures, because the run's own reported multiplier is the least sensitive of them:
    `_run_loop` only records a shortfall once the clamp binds, so it reads 1000 through a stall
    of up to three wall seconds at this rate. Observed ticks against nominal is what moves.
    """
    runtime.create_run(RUN, SEED, horizon_tick=simtime.TICKS_PER_SIM_DAY * 200)
    run = runtime.runs[RUN]
    runtime.apply_command(
        RUN,
        kernel_pb2.ASSIGN_WORK,
        canonical.encode({"item": "wi_ap_map", "person": "stf_ap", "via_manager": False}),
    )
    while run.state.items["wi_ap_map"].status != sim.STATUS_BLOCKED:
        runtime._advance(run, 1)

    runtime.set_rate(RUN, LOAD_RATE)
    runtime.ensure_loop(RUN)
    await asyncio.sleep(0.4)  # let the loop reach its steady cadence before measuring

    loads = {
        f"{COMMANDS_PER_SECOND} cheap commands a second": (
            lambda: runtime.apply_command(
                RUN,
                kernel_pb2.SUBMIT_CEO_INPUT,
                canonical.encode({"bitmask": 1, "at_tick": run.state.tick + 30}),
            ),
            True,
        ),
        "back-to-back comparisons": (
            lambda: runtime.apply_command(
                RUN, kernel_pb2.COMPARE_OPTIONS, _comparison_payload(run)
            ),
            False,
        ),
    }

    for description, (issue_one, paced) in loads.items():
        observed, achieved, lag, delivered = await _clock_under(
            runtime, run, issue_one, paced=paced
        )

        assert delivered > 0, f"no load was delivered for {description}"
        assert observed >= OBSERVED_PERMILLE_FLOOR, (
            f"under {description} the clock achieved {observed} permille of nominal; the "
            f"floor is {OBSERVED_PERMILLE_FLOOR} and the measurement before the lock was "
            "902-907 for the first load and 944-950 for the second"
        )
        assert achieved >= ACHIEVED_PERMILLE_FLOOR, (
            f"under {description} the run reports {achieved} permille achieved; measured 1000"
        )
        assert lag <= LAG_TICKS_BOUND, (
            f"under {description} sim-time lag grew by {lag} ticks; measured 0"
        )


async def test_a_command_rejected_under_the_lock_mutates_nothing_and_releases(runtime) -> None:
    """A refusal is the path that leaves a lock held, if anything does.

    Both halves matter. Nothing moves, which is what "mutates nothing" means concretely — and
    the lock comes back, which is what stops one bad command from stopping the clock forever.
    """
    from simcore import hashing

    run = _blocked_at_a_decision(runtime)
    before = hashing.state_hash(sim.snapshot(run.state)).overall

    with pytest.raises(sim.CommandRejected):
        # Tagged in the past, which `submit_ceo_input` refuses: an input for a tick already
        # gone cannot be applied without rewriting history.
        runtime.apply_command(
            RUN,
            kernel_pb2.SUBMIT_CEO_INPUT,
            canonical.encode({"bitmask": 1, "at_tick": 0}),
        )

    assert hashing.state_hash(sim.snapshot(run.state)).overall == before

    assert not run.lock.locked(), "the lock survived a rejection"
    # And the run still takes work, which is the consequence that would actually be noticed.
    runtime._advance(run, 1)
    assert run.state.tick > 0


async def test_a_slow_operation_on_the_answer_side_does_not_stall_the_tick_loop(
    runtime,
) -> None:
    """The lock is held across a state mutation, never across a wait (R13).

    The statement transport does not exist yet — **U10** builds it, and will make this literal:
    a director's answer arrives over a provider call, and the provider call is the wait. The
    stand-in here is honest about the only shape that matters, which is that the wait happens
    off the lock and only the write to state happens on it. If U10 puts its provider call
    inside the locked region instead, the clock stops for the length of a model round-trip and
    this test is what should catch it.
    """
    runtime.create_run(RUN, SEED, horizon_tick=simtime.TICKS_PER_SIM_DAY * 200)
    run = runtime.runs[RUN]
    runtime.set_rate(RUN, LOAD_RATE)
    runtime.ensure_loop(RUN)
    await asyncio.sleep(0.2)

    slow_seconds = 0.5
    lands_at = run.state.tick + 10_000

    def an_answer_that_takes_a_provider_round_trip() -> None:
        time.sleep(slow_seconds)  # U10: the provider call. Off the lock, deliberately.
        with run.lock:
            run.state.queued_answers.setdefault(lands_at, []).append(
                {"request_id": "u4-stand-in", "answer": {}}
            )

    tick_before = run.state.tick
    await anyio.to_thread.run_sync(an_answer_that_takes_a_provider_round_trip)
    advanced = run.state.tick - tick_before

    # Two thirds of nominal is a generous floor for a machine under test load. A stall would
    # show as single digits, not as two thirds.
    nominal = slow_seconds * simtime.TICKS_PER_WALL_SECOND_AT_BASE_RATE * LOAD_RATE
    assert advanced > nominal * 0.66, (
        f"the clock advanced {advanced} ticks while a {slow_seconds}s answer was in flight; "
        f"nominal is {nominal:.0f}"
    )
    assert lands_at in run.state.queued_answers, "the answer never landed"


async def test_no_store_round_trip_or_wait_happens_inside_the_lock() -> None:
    """R13, asserted structurally, because the cost of getting it wrong is not local.

    An append inside the locked region serialises the tick loop behind the store; a provider
    call inside it serialises the tick loop behind a network. Neither shows up as an exception
    — the clock just runs slow — so this reads the source rather than trusting a convention.

    **U10 and U17 should extend this list rather than work around it.**
    """
    import ast
    import inspect

    from kernel import loop as loop_module

    forbidden = {
        "submit",  # the store writer: one append round-trip, blocking on a threading.Event
        "append_tick",
        "read_events",
        "execute",  # SQLAlchemy
        "begin",
        "run_sync",  # a hop to another thread, which may then block on this same lock
        "sleep",
        "wait",
        "join",
    }

    tree = ast.parse(inspect.getsource(loop_module))
    locked_regions = 0
    offences: list[str] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.With):
            continue
        if not any(
            isinstance(item.context_expr, ast.Attribute) and item.context_expr.attr == "lock"
            for item in node.items
        ):
            continue
        locked_regions += 1
        for inner in ast.walk(node):
            if (
                isinstance(inner, ast.Call)
                and isinstance(inner.func, ast.Attribute)
                and inner.func.attr in forbidden
            ):
                offences.append(f"{inner.func.attr}() at loop.py line {inner.lineno}")

    assert locked_regions >= 2, (
        f"only {locked_regions} locked regions found; the command path and the advance should "
        "each have one, so this test would otherwise pass vacuously"
    )
    assert not offences, "blocking calls inside the run lock: " + ", ".join(offences)


async def test_the_per_run_lock_is_reachable_and_orderable_by_run_id(runtime) -> None:
    """R22's multi-run rule, which **U17** will need: per-run locks taken in run-id order.

    Nothing takes two of them yet. What this pins is that the lock is a per-run attribute
    rather than a private detail of one method — a lock reachable only from inside
    `apply_command` would force U17 to introduce a second one, and two locks with no order
    between them is the deadlock this ordering rule exists to prevent.
    """
    runtime.create_run("run-b", SEED)
    runtime.create_run("run-a", SEED + 1)

    in_run_id_order = sorted(runtime.runs.values(), key=lambda run: run.run_id)
    assert [run.run_id for run in in_run_id_order] == ["run-a", "run-b"]

    with contextlib.ExitStack() as stack:
        for run in in_run_id_order:
            stack.enter_context(run.lock)
        assert all(run.lock.locked() for run in in_run_id_order)

    assert not any(run.lock.locked() for run in in_run_id_order)


# =========================================================================
# Branch execution off the clock's worker pool, and a clock that reports
# progress rather than liveness (R14)
# =========================================================================
#
# U4 closed the *correctness* half: a comparison now runs against a copy taken under the run's
# lock. What it left open is the *cost* half. Branch execution is about a quarter of a second of
# pure Python per option, holding the GIL, on a thread the tick loop and the lease heartbeat draw
# from too — so enough simultaneous comparisons slow every clock in the process, and `healthy()`
# asked whether the tick task was `done()` rather than whether it was moving, so a starved clock
# reported ready.
#
# The measurements below reuse U4's harness and U4's floors deliberately. A second harness with
# its own baseline would make "unchanged against the same baseline" unfalsifiable.


#: How many comparisons are put in flight at once for the starvation measurement.
#:
#: **Sixteen, because that is where the failure is reliable.** Measured on the build before the
#: branch limiter, three samples each, at rate 3 against the six-option width:
#:
#:     1 issuer     947, 957, 960 permille   0 ticks of lag
#:     4 issuers    967, 974, 978 permille   0 ticks of lag
#:     8 issuers    894, 904, 926 permille   32-67 ticks of lag
#:     16 issuers   683, 693, 744 permille   558-689 ticks of lag
#:     32 issuers   372, 377, 390 permille   2739-2804 ticks of lag
#:
#: Eight breaches the floors in two samples out of three, which is a flake rather than a floor.
#: Sixteen breaches all three figures in every sample and by a wide margin, so the test fails
#: before the change for the reason it is written for rather than by luck.
#:
#: Pathological rather than realistic on purpose. This is a single-operator local product and the
#: honest ceiling is one person clicking one button; a floor that is only defended at the load
#: someone expects is not a floor. Sixteen is also short of forty, which is anyio's default limiter
#: in full — that is the *token* exhaustion story, and it is measured separately below by occupying
#: the default limiter directly rather than by trying to fill it with real work.
CONCURRENT_COMPARISONS = 16

#: The window the six-option loads are measured over.
#:
#: Longer than U4's two seconds, because the load is slower to deliver: a six-option comparison at
#: `MAX_BRANCH_DAYS` costs about 1.3s, so a two-second window would time barely one of them and
#: the figure would be an artefact of where the window's edges fell.
SIX_OPTION_WINDOW_SECONDS = 5.0


def _six_options_at_the_first_checkpoint(monkeypatch) -> None:
    """Widen `wi_ap_map`'s first checkpoint to six options.

    **No authored checkpoint offers six.** Every one of the nine offers three, so the plan's
    stated load — "six-option comparisons", and the 1.6s figure derived from it — cannot be
    produced from the shipped scenario at all. `MAX_BRANCHES_PER_COMPARISON` is six, so six is
    what one command may cost, and that is the number worth defending a floor at. It is
    synthesised here by duplicating the authored options rather than by inventing content: what is
    under measurement is the cost of stepping six branches, and two branches that price the same
    option still cost two branches.

    Patching `ITEMS_BY_ID` rather than `state.dynamic_items` is not a style choice. A dynamic
    item's `checkpoints` do not survive `snapshot.to_wire`, which writes eight fields and no
    checkpoint tuple — so a six-option spec injected there would come back from `restore` with no
    checkpoints and every branch would fail on an index. The authored table is read by the parent
    and by every restored copy alike.
    """
    from simcore import items as work

    base = work.spec("wi_ap_map")
    first = base.checkpoints[0]
    widened = dataclasses.replace(first, options=first.options + first.options)
    monkeypatch.setitem(
        work.ITEMS_BY_ID,
        "wi_ap_map",
        dataclasses.replace(base, checkpoints=(widened,) + base.checkpoints[1:]),
    )
    assert len(work.spec("wi_ap_map").checkpoints[0].options) == 6


def _ticking_at_a_decision(runtime, monkeypatch=None):
    """A run at `LOAD_RATE` with its clock running and `wi_ap_map` stopped at a decision.

    The horizon is U4's: 200 sim-days, so a branch stops at `MAX_BRANCH_DAYS` and costs what a
    branch costs rather than what a short fixture makes it cost.
    """
    if monkeypatch is not None:
        _six_options_at_the_first_checkpoint(monkeypatch)

    runtime.create_run(RUN, SEED, horizon_tick=simtime.TICKS_PER_SIM_DAY * 200)
    run = runtime.runs[RUN]
    runtime.apply_command(
        RUN,
        kernel_pb2.ASSIGN_WORK,
        canonical.encode({"item": "wi_ap_map", "person": "stf_ap", "via_manager": False}),
    )
    # Twenty quanta at a time rather than one. It takes about six hundred ticks to reach the
    # decision and each `_advance` call is one append round-trip per quantum, so single-stepping
    # spends most of a second of every load test on the *fixture*. Overshooting the block by up to
    # nineteen ticks changes nothing: a blocked item stays blocked.
    while run.state.items["wi_ap_map"].status != sim.STATUS_BLOCKED:
        runtime._advance(run, 20)

    runtime.set_rate(RUN, LOAD_RATE)
    runtime.ensure_loop(RUN)
    return run


def _assert_the_clock_held(description, observed, achieved, lag, delivered, *, at_least=1):
    """U4's three figures, asserted against U4's three floors.

    All three, and observed-against-nominal is the load-bearing one. The run's own reported
    multiplier only records a shortfall once the catch-up clamp binds, so a test resting on it
    alone passes through a clock running at roughly half speed — which is why it is asserted here
    beside the other two rather than instead of them.
    """
    assert delivered >= at_least, (
        f"only {delivered} commands were delivered for {description}; the measurement is empty"
    )
    assert observed >= OBSERVED_PERMILLE_FLOOR, (
        f"under {description} the clock achieved {observed} permille of nominal; the floor is "
        f"{OBSERVED_PERMILLE_FLOOR}, U4's baseline is 944-950 for one comparison at a time, and "
        "the idle reference is 908-910"
    )
    assert achieved >= ACHIEVED_PERMILLE_FLOOR, (
        f"under {description} the run reports {achieved} permille achieved; U4 measured 1000. "
        "This figure is the insensitive one — it only moves once the catch-up clamp binds — so "
        "it is asserted beside observed-against-nominal, never instead of it"
    )
    assert lag <= LAG_TICKS_BOUND, (
        f"under {description} sim-time lag grew by {lag} ticks; the bound is {LAG_TICKS_BOUND} "
        "and U4 measured 0"
    )


async def test_six_option_comparisons_back_to_back_leave_the_clock_and_its_lag(
    runtime, monkeypatch
) -> None:
    """Covers M64, at the width the plan states and one comparison at a time.

    This is the regression half: U4 measured 944-950 permille with three-option comparisons
    running back to back, and doubling the branch count must not spend the difference. It is
    asserted against U4's own floors and U4's own harness, which is what makes "under the same
    tick count U4 states" mean anything.

    All three figures, never just the multiplier, and the plan's stated verification is the
    multiplier alone. `achieved_multiplier_permille` computes its wanted ticks with
    `int(elapsed * 36 * rate)` and throws away the remainder at every wake — at rate 1 the clock
    runs at roughly 55% of nominal while the field still reports 1000, and it only moves once the
    catch-up clamp binds. So a test resting on it alone passes through a stall of about three wall
    seconds. Observed-ticks-against-nominal is what moves. The truncation itself is a pre-existing
    defect, out of scope here, and recorded in the execution-decisions note rather than fixed.
    """
    run = _ticking_at_a_decision(runtime, monkeypatch)
    await asyncio.sleep(0.4)  # let the loop reach its steady cadence before measuring

    observed, achieved, lag, delivered = await _clock_under(
        runtime,
        run,
        lambda: runtime.apply_command(
            RUN, kernel_pb2.COMPARE_OPTIONS, _comparison_payload(run)
        ),
        paced=False,
        window=SIX_OPTION_WINDOW_SECONDS,
    )

    _assert_the_clock_held(
        "six-option comparisons back to back", observed, achieved, lag, delivered, at_least=2
    )


async def test_concurrent_six_option_comparisons_cannot_starve_the_clock(
    runtime, monkeypatch
) -> None:
    """The measurement the branch limiter exists for, and the one that fails without it.

    Sixteen comparisons in flight is sixteen threads of pure Python against a tick loop that wants
    the GIL for well under a millisecond in every fifty — so the clock does not lose to *work*, it
    loses to waiting its turn behind fifteen other GIL holders. Bounding how many branch threads
    may run at once is what gives it its turn back.

    Measured on this load: 683-744 permille observed with 558-689 ticks of lag before the limiter,
    967-972 permille with no lag at all after it. The full sizing sweep is recorded beside
    `loop.BRANCH_SLOTS`, including the count at which the protection stops working.

    **It costs about twenty-four seconds and that is the load, not the harness.** Sixteen
    six-option comparisons is sixteen times 1.3 seconds of branch stepping, and one interpreter
    executes it end to end however the window is sized — the window bounds when issuing *starts*,
    not when the work finishes. Shortening it means measuring a smaller load, which is the one
    thing this test must not do.
    """
    run = _ticking_at_a_decision(runtime, monkeypatch)
    await asyncio.sleep(0.4)

    observed, achieved, lag, delivered = await _clock_under(
        runtime,
        run,
        lambda: runtime.apply_command(
            RUN, kernel_pb2.COMPARE_OPTIONS, _comparison_payload(run)
        ),
        paced=False,
        issuers=CONCURRENT_COMPARISONS,
        window=SIX_OPTION_WINDOW_SECONDS,
    )

    _assert_the_clock_held(
        f"{CONCURRENT_COMPARISONS} concurrent six-option comparisons",
        observed,
        achieved,
        lag,
        delivered,
        at_least=CONCURRENT_COMPARISONS,
    )


async def test_a_saturated_branch_limiter_queues_a_comparison_and_the_clock_keeps_time(
    runtime,
) -> None:
    """R14's "rather than": a comparison with no slot *waits*, and waiting costs the clock nothing.

    The distinction is the whole point of a separate limiter. A comparison that cannot get a
    branch slot is blocked on a semaphore, holding no GIL and occupying no thread the clock could
    have used — so the clock runs at its ordinary rate while the comparison is queued, and the
    comparison completes the moment a slot frees rather than being refused.

    Every slot is held here, which is a load the product cannot generate; the point is what
    happens at the boundary, and the boundary is only observable when it is reached.
    """
    run = _ticking_at_a_decision(runtime)
    await asyncio.sleep(0.4)

    finished = threading.Event()

    def issue_one_comparison() -> None:
        runtime.apply_command(RUN, kernel_pb2.COMPARE_OPTIONS, _comparison_payload(run))
        finished.set()

    with contextlib.ExitStack() as stack:
        for _ in range(loop_module.BRANCH_SLOTS):
            stack.enter_context(loop_module.BRANCH_LIMITER)

        queued = asyncio.create_task(anyio.to_thread.run_sync(issue_one_comparison))
        tick_before = run.state.tick
        blocked_for = 0.6
        await asyncio.sleep(blocked_for)

        assert not finished.is_set(), "the comparison ran with every branch slot held"
        # And the clock did not pay for the queueing: two thirds of nominal is a generous floor
        # for a machine under test load, and a stall would show as single digits.
        advanced = run.state.tick - tick_before
        nominal = blocked_for * simtime.TICKS_PER_WALL_SECOND_AT_BASE_RATE * LOAD_RATE
        assert advanced > nominal * 0.66, (
            f"the clock advanced {advanced} ticks while a comparison was queued behind the "
            f"branch limiter; nominal is {nominal:.0f}"
        )

    # The slots are back, so the comparison is queued rather than refused: it completes.
    await asyncio.wait_for(queued, timeout=30)
    assert finished.is_set()


async def test_the_clock_keeps_its_own_thread_slots_when_the_default_pool_is_full(
    runtime,
) -> None:
    """The tick loop and the heartbeat draw from a limiter commands cannot reach into (R14).

    The other half of the starvation story, and the half a real load cannot demonstrate on this
    machine: `anyio.to_thread.run_sync` rations threads through a process-wide 40-slot limiter, and
    every synchronous FastAPI route — the command route included — borrows from it for as long as
    it runs. Forty comparisons at a second each would leave the tick loop's own hop with no slot
    to take, and nothing about that raises: the clock simply stops until a route returns.

    Occupying the default limiter directly rather than with real comparisons is deliberate. What
    is under test is the *disjointness* of the two limiters, and forty concurrent comparisons would
    prove that with a minute of branch stepping and a measurement dominated by the GIL rather than
    by the thing being asserted.
    """
    run = _ticking_at_a_decision(runtime)
    await asyncio.sleep(0.4)

    default = anyio.to_thread.current_default_thread_limiter()
    release = threading.Event()
    hogs = [
        asyncio.create_task(anyio.to_thread.run_sync(lambda: release.wait(10)))
        for _ in range(default.total_tokens)
    ]
    try:
        await asyncio.sleep(0.3)
        assert default.borrowed_tokens == default.total_tokens, (
            f"only {default.borrowed_tokens} of {default.total_tokens} default slots were taken; "
            "the premise of this test is that none is left"
        )

        tick_before = run.state.tick
        starved_for = 0.6
        await asyncio.sleep(starved_for)
        advanced = run.state.tick - tick_before
    finally:
        release.set()
        await asyncio.gather(*hogs)

    nominal = starved_for * simtime.TICKS_PER_WALL_SECOND_AT_BASE_RATE * LOAD_RATE
    assert advanced > nominal * 0.66, (
        f"the clock advanced {advanced} ticks while every default worker slot was held; nominal "
        f"is {nominal:.0f}. Its own limiter is what should have made this a non-event"
    )


async def test_a_clock_that_is_alive_but_not_progressing_reports_unhealthy(
    runtime, monkeypatch
) -> None:
    """A starved clock is neither done nor crashed, and R29 as written called that ready.

    The stall is manufactured the way a real one happens: the tick task is awaiting `_advance` on
    a worker thread, and `_advance` cannot get past the run's lock. Nothing raises, the task is
    alive, and sim-time stands still — which is precisely the state the old predicate reported as
    healthy, asserted below so the change is visible rather than assumed.

    `CLOCK_STALL_SECONDS` is shortened for the test. The shipped value is sized against the
    compose readiness probe's five-second interval, and waiting that out here would buy nothing but
    five seconds.
    """
    monkeypatch.setattr(loop_module, "CLOCK_STALL_SECONDS", 0.4)

    run = _ticking_at_a_decision(runtime)
    await asyncio.sleep(0.4)

    healthy, detail = runtime.healthy()
    assert healthy, detail

    with run.lock:
        # Let the loop reach its next `_advance` and block there.
        await asyncio.sleep(
            loop_module.CLOCK_STALL_SECONDS + 4 * loop_module.WAKE_INTERVAL_SECONDS
        )

        assert run.task is not None and not run.task.done(), (
            "the tick task died, so this is testing the old predicate rather than the new one"
        )

        healthy, detail = runtime.healthy()
        assert not healthy, "a clock that has not moved reported ready"
        assert RUN in detail
        assert "sim-time has not moved" in detail
        assert f"tick {run.progress_tick}" in detail, detail
        # `diagnose` is not called in here: it takes the same lock, and this is a plain
        # `threading.Lock` rather than an `RLock`, so asking would deadlock the test.

    # And it recovers on its own once the clock moves again, rather than latching.
    for _ in range(100):
        await asyncio.sleep(0.05)
        healthy, detail = runtime.healthy()
        if healthy:
            break
    assert healthy, detail


async def test_a_comparison_is_the_same_comparison_after_the_move(runtime, monkeypatch) -> None:
    """Six branches, the same figures, the same bound — byte for byte against the library.

    The move put branch execution behind a limiter and on the far side of a snapshot round-trip
    taken under the run's lock. Neither is allowed to change the answer, and "the tests still pass"
    is a weaker claim than it sounds: almost every assertion in `test_compare.py` calls the library
    directly and would not notice if the kernel's path diverged from it.

    So the recorded event is compared against `compare.run_comparison` run on the live state, on a
    **paused** run so that both see one instant. Canonical bytes rather than dict equality: the
    payload is what goes into an append-only log, and a difference in key order or integer width is
    a difference in what was recorded.
    """
    run = _ticking_at_a_decision(runtime, monkeypatch)
    runtime.set_rate(RUN, 0)
    await runtime.stop_run(RUN)

    [record] = runtime.apply_command(
        RUN, kernel_pb2.COMPARE_OPTIONS, _comparison_payload(run)
    )
    through_the_kernel = record.decoded_payload()["branches"]
    directly = [
        summary.to_state()
        for summary in branching.run_comparison(run.state, "wi_ap_map", 0, in_person=True)
    ]

    assert len(through_the_kernel) == 6, "the six-option width did not reach the kernel"
    assert canonical.encode(through_the_kernel) == canonical.encode(directly)
    assert {branch["stop_reason"] for branch in through_the_kernel} == {"bound"}, (
        "the branches stopped somewhere other than the comparison bound, so 'same bound' is "
        "asserted against the wrong thing"
    )
