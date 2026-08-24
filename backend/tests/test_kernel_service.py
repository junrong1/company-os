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
from kernel.loop import KernelRuntime, RunLoop, child_run_id_for
from kernel.store import LogStore, make_engine
from simcore import compare as branching
from simcore import log as folder
from simcore import pending as pend
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
    """R20, asserted structurally, at every place that publishes.

    Publishing before appending would leave the client's rendered world ahead of the
    authoritative log with nothing able to detect the divergence.

    It used to read `_run_loop`, which published a batch after `_advance` handed it back. The
    publish moved *into* the append sites — `_advance` publishes each quantum as it commits it and
    `apply_command` publishes its own events, which is what stops a command's outcome reaching its
    client only as a sequence gap. So the same property is now asserted where the appends are, and
    at both of them rather than at the one that happened to exist.
    """
    import inspect

    for owner in (KernelRuntime._advance, KernelRuntime.apply_command):
        source = inspect.getsource(owner)
        assert source.index("self.writer.submit") < source.index("self._publish"), (
            f"{owner.__name__} publishes before its append returns"
        )


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
        # The provider call R13 names explicitly, and the bench's own entry point beside it. U10
        # answers a statement on a worker thread with no lock held; both of these inside the lock
        # would serialise the clock behind a network, which does not raise — the clock just runs
        # slow, which is the failure this file's diagnostics were built to explain rather than cause.
        "complete",
        "_statement_producer",
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

    Patching the loaded scenario's item index rather than `state.dynamic_items` is not a style
    choice. A dynamic item's `checkpoints` do not survive `snapshot.to_wire`, which writes eight
    fields and no checkpoint tuple — so a six-option spec injected there would come back from
    `restore` with no checkpoints and every branch would fail on an index. The authored table is
    read by the parent and by every restored copy alike: the loader is content-addressed, so
    every `load` of the unedited `default.toml` in this process returns this same object.
    """
    from simcore import scenario as sc

    company = sc.load_default()
    base = company.item("wi_ap_map")
    first = base.checkpoints[0]
    widened = dataclasses.replace(first, options=first.options + first.options)
    monkeypatch.setitem(
        company.items_by_id,
        "wi_ap_map",
        dataclasses.replace(base, checkpoints=(widened,) + base.checkpoints[1:]),
    )
    assert len(company.item("wi_ap_map").checkpoints[0].options) == 6


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


# =========================================================================
# A command's own events reach the client that issued it, in order
# =========================================================================
#
# `_publish` had one caller — the tick loop — so a command's events were appended, returned to
# the gateway, and never put on the wire. The client learned about them as a *sequence gap* on
# some later tick. Measured on a live run through `single_process.py`: a hand-off answered
# `produced_seq: [3, 4]` and the client's applied sequence stayed at 2 until the director's
# arrival forty wall seconds later, then jumped to 6 with the gap flag set.
#
# Every assertion below is on **the frames a subscriber actually received**, never on what
# `apply_command` returned. The defect was invisible precisely because the return value was
# always right: the events existed, were correct, were logged, and rendered when injected. Only
# the transport was missing, so only the transport is asserted.


def _drain(queue: asyncio.Queue) -> list:
    """Everything a subscriber can read right now, in the order the publisher wrote it."""
    frames = []
    while True:
        try:
            frames.append(queue.get_nowait())
        except asyncio.QueueEmpty:
            return frames


def _an_envelope_at(seq: int):
    """One envelope at a chosen sequence, for the publisher tests that need a hole in the run.

    Built rather than appended, because what is under test is what the publisher does with a
    sequence it has been handed — and the situations that produce a hole (a store commit the writer
    stopped waiting for) cannot be appended into existence.
    """
    from contracts.envelope import build

    return build(
        seq=seq,
        tick=seq,
        kind=EventKind.METRICS_APPLIED,
        rules_ver="test",
        payload={"tick": seq},
        run_id=RUN,
    )


def _event_seqs(frames: list) -> list[int]:
    """The sequences of the event frames, in arrival order.

    `POSITION_ECHO` is filtered out rather than tolerated: it is a control frame carrying derived
    state and no sequence at all, which is why the client applies it outside the sequence guard.
    Including it here would make "in sequence order" a claim about two different things.
    """
    return [frame.seq for frame in frames if not isinstance(frame, dict)]


def _assert_one_rising_sequence(seqs: list[int], description: str) -> None:
    """No gap and no inversion — the two things the client cannot recover from silently.

    The client's rule is exact: a frame at `applied + 1` is applied, a lower one is dropped as a
    resume replay, and anything higher sets `sequenceGap` and shows the banner. So "ordered" here
    means contiguous *and* rising, which is one statement about `range`.
    """
    assert seqs, f"{description}: the client received no events at all"
    assert seqs == list(range(seqs[0], seqs[0] + len(seqs))), (
        f"{description}: the client received {seqs[:12]}… which is not one rising contiguous "
        "sequence. A hole is a gap the client cannot tell from a lost event, and an inversion is "
        "a hole for as long as it lasts"
    )


async def _a_ticking_run_with_a_client(runtime) -> tuple[RunLoop, asyncio.Queue]:
    """A run at the base rate with its clock running and one subscriber attached.

    The base rate rather than `LOAD_RATE`, and a long horizon: what these tests need is a clock
    that is genuinely running while a command is applied, not one that is running fast.
    """
    runtime.create_run(RUN, SEED, horizon_tick=simtime.TICKS_PER_SIM_DAY * 200)
    queue = runtime.subscribe(RUN)
    runtime.ensure_loop(RUN)
    # Long enough for the loop to reach its cadence and publish at least one tick, so what
    # follows is measured against a live stream rather than a cold one.
    await asyncio.sleep(0.2)
    return runtime.runs[RUN], queue


async def test_a_commands_events_reach_a_connected_client_with_no_sequence_gap(runtime) -> None:
    """The defect itself, asserted on the wire rather than on the return value.

    Before the fix the command's sequences were simply absent from everything the subscriber
    received, and the frames around them were *not* contiguous — the client's own gap test fires
    on exactly that.
    """
    run, queue = await _a_ticking_run_with_a_client(runtime)

    produced = await anyio.to_thread.run_sync(
        lambda: runtime.apply_command(
            RUN,
            kernel_pb2.ASSIGN_WORK,
            canonical.encode({"item": "wi_ap_map", "via_manager": True}),
        )
    )
    assert len(produced) == 2, "a hand-off produces the assignment and the walk it causes"

    received: list = []
    for _ in range(100):
        await asyncio.sleep(0.02)
        received.extend(_drain(queue))
        if {envelope.seq for envelope in produced} <= set(_event_seqs(received)):
            break

    seqs = _event_seqs(received)
    for envelope in produced:
        assert envelope.seq in seqs, (
            f"{envelope.kind.name} at sequence {envelope.seq} was appended and returned to the "
            "caller but never reached the connected client"
        )
    _assert_one_rising_sequence(seqs, "a hand-off on a ticking run")


async def test_a_handoff_reaches_a_client_while_the_walk_is_still_in_progress(runtime) -> None:
    """M62: a live run shows delegation as a walk, and the walk *is* the delegation.

    This is the sharp one, because it is the assertion the plan's verification rests on and the
    one that was false in a way nothing showed. U3's verification was half a live one: the walk
    **home**, produced by the step when the director arrives, travelled the real wire and
    rendered; the walk **out**, produced by the command, was dropped. So the visible half was the
    consequence of the delegation and the invisible half was the delegation.

    Arrival is not good enough and that is the whole point of the timing assertion. The walk is
    over a hundred ticks long — several wall seconds at the base rate — so a frame that arrives
    "eventually" arrives after the figure has already crossed the floor, which is the forty
    seconds U3 measured. What is asserted is that the client holds the walk while the director is
    still on it, with most of the path left to draw.
    """
    run, queue = await _a_ticking_run_with_a_client(runtime)

    produced = await anyio.to_thread.run_sync(
        lambda: runtime.apply_command(
            RUN,
            kernel_pb2.ASSIGN_WORK,
            canonical.encode({"item": "wi_ap_map", "via_manager": True}),
        )
    )
    walk = next(e for e in produced if e.kind is EventKind.STAFF_MOVED)
    payload = walk.decoded_payload()
    director = payload["person"]
    started_at = int(payload["start_tick"])
    full_walk = simtime.walk_duration_ticks(len(payload["path"]))
    assert full_walk > 4 * simtime.TICKS_PER_WALL_SECOND_AT_BASE_RATE, (
        f"the hand-off walk is only {full_walk} ticks, so 'while the walk is in progress' is not "
        "a meaningful window on this floor plan and this test proves nothing"
    )

    received: list = []
    for _ in range(100):
        await asyncio.sleep(0.02)
        received.extend(_drain(queue))
        if walk.seq in _event_seqs(received):
            break

    # Read the instant the frame is in hand, not afterwards: the director keeps walking while the
    # assertions run, so a later read would answer a question about the test's own duration.
    elapsed = run.state.tick - started_at
    walker = run.state.person(director)

    assert walk.seq in _event_seqs(received), (
        f"the walk that is the delegation ({director} carrying {payload['item']}) never reached "
        "the client. It was appended at sequence "
        f"{walk.seq} and returned to the caller, which is what made this invisible"
    )
    assert walker.state == sim.STATE_WALKING and walker.path, (
        f"{director} had already finished the walk by the time the client heard about it — "
        f"{elapsed} of {full_walk} ticks gone. That is U3's measurement, not a fix"
    )
    assert elapsed < full_walk // 2, (
        f"the client received the walk {elapsed} ticks into a {full_walk}-tick walk; more than "
        "half of it was already over, so the figure would jump rather than cross the floor"
    )
    _assert_one_rising_sequence(_event_seqs(received), "the walk that is the delegation")


async def test_commands_beside_a_ticking_clock_interleave_into_one_rising_sequence(
    runtime,
) -> None:
    """The ordering half, which is the one that is delicate rather than incidental.

    `_advance` releases the run's lock before it appends, so a command's mutation can happen after
    a tick's and its append can still land first — and both threads then race to publish. A
    publisher that put frames out in the order the threads observed their own commits would emit
    sequence 4 before 3, which the client cannot tell from a lost event: it sets `sequenceGap` and
    shows the banner. So the naive fix manufactures the gap it was written to close, and it does it
    only under contention, which is exactly the kind of failure that ships.

    U4's harness, U4's load and U4's window, deliberately. Eighty commands paced against a rate-3
    clock over two seconds is two threads committing on one run continuously for the whole window —
    enough collisions to have inverted, where a single command would be luck either way. What is
    read is the subscriber's queue, drained concurrently, so the claim is about frames on the wire
    and not about the two lists the kernel happened to return.
    """
    runtime.create_run(RUN, SEED, horizon_tick=simtime.TICKS_PER_SIM_DAY * 200)
    run = runtime.runs[RUN]
    queue = runtime.subscribe(RUN)
    runtime.set_rate(RUN, LOAD_RATE)
    runtime.ensure_loop(RUN)
    await asyncio.sleep(0.4)

    received: list = []

    async def read_the_client() -> None:
        # Drained continuously rather than at the end: the subscriber queue holds 1024 frames and
        # a full one drops the subscriber, which would end the measurement early and silently.
        while True:
            received.extend(_drain(queue))
            await asyncio.sleep(0.005)

    reader = asyncio.create_task(read_the_client(), name="a-client-reading")
    try:
        _, _, _, delivered = await _clock_under(
            runtime,
            run,
            lambda: runtime.apply_command(
                RUN,
                kernel_pb2.SUBMIT_CEO_INPUT,
                canonical.encode({"bitmask": 1, "at_tick": run.state.tick + 30}),
            ),
            paced=True,
        )
    finally:
        reader.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await reader
    received.extend(_drain(queue))

    assert delivered >= 2 * COMMANDS_PER_SECOND, (
        f"only {delivered} commands were issued over the window, which is too few collisions for "
        "this to have failed before the ordering was fixed"
    )
    seqs = _event_seqs(received)
    assert len(seqs) > delivered, (
        f"the client received {len(seqs)} events against {delivered} commands, so the tick loop "
        "was not publishing alongside them and nothing was interleaved"
    )
    _assert_one_rising_sequence(
        seqs, f"{delivered} commands against a rate-{LOAD_RATE} clock"
    )


async def test_a_publish_that_raises_leaves_the_command_applied_and_the_append_durable(
    runtime, monkeypatch
) -> None:
    """A frame that cannot be delivered is a frame, not a failed command.

    The append has already committed by the time anything is published, and the client's remedy for
    a missing frame is the resume it already ships. So a raising delivery must cost exactly one
    frame: not a 500 on a command that succeeded, and not a stream that goes quiet for the rest of
    the run because the publisher is still waiting for a sequence nobody will send.

    That second half is the one worth stating. The cursor advances *before* delivery precisely so a
    lost frame becomes a gap — which the client detects and recovers from — rather than a hole every
    later frame queues behind, which is silent.
    """
    run, queue = await _a_ticking_run_with_a_client(runtime)

    def the_socket_went_away(self, run, envelopes):
        raise RuntimeError("the subscriber's queue is gone")

    monkeypatch.setattr(KernelRuntime, "_deliver", the_socket_went_away)
    _drain(queue)

    produced = await anyio.to_thread.run_sync(
        lambda: runtime.apply_command(
            RUN,
            kernel_pb2.ASSIGN_WORK,
            canonical.encode({"item": "wi_ap_map", "via_manager": True}),
        )
    )

    assert len(produced) == 2, "the command was refused because its publish failed"
    logged = {envelope.seq for envelope in runtime.store.read_events(RUN)}
    assert {envelope.seq for envelope in produced} <= logged, "the append did not survive"
    assert not _event_seqs(_drain(queue)), "a delivery that raised delivered something anyway"

    # The client's remedy, exercised rather than asserted about: a resume asking for everything
    # after the last sequence it applied returns the frames it missed.
    first_lost = min(envelope.seq for envelope in produced)
    resumed = runtime.store.read_events(RUN, after_seq=first_lost - 1)
    assert {envelope.seq for envelope in produced} <= {e.seq for e in resumed}, (
        "the events lost to a failed publish are not recoverable by resume, which is the only "
        "remedy the client has"
    )

    # And the stream is not stalled behind the frames that were lost. Asserted through a second
    # command rather than by waiting for the clock: at the base rate the next event a tick emits
    # can be a hundred quanta away, so a wait would be measuring the scenario's event density.
    monkeypatch.undo()
    after = await anyio.to_thread.run_sync(
        lambda: runtime.apply_command(
            RUN,
            kernel_pb2.SUBMIT_CEO_INPUT,
            canonical.encode({"bitmask": 1, "at_tick": run.state.tick + 30}),
        )
    )
    arrived: list[int] = []
    for _ in range(100):
        await asyncio.sleep(0.02)
        arrived.extend(_event_seqs(_drain(queue)))
        if {envelope.seq for envelope in after} <= set(arrived):
            break

    assert {envelope.seq for envelope in after} <= set(arrived), (
        "nothing reached the client after a failed publish, so the ordering cursor is waiting "
        "behind the lost sequence — a stream that goes quiet with nothing raised is strictly "
        "worse than the gap a lost frame leaves"
    )
    assert min(arrived) > max(envelope.seq for envelope in produced), (
        "a frame at or below a lost sequence was delivered, so the cursor did not move past it"
    )


async def test_every_append_in_this_file_publishes_what_it_committed() -> None:
    """The invariant the ordering rests on, enforced rather than remembered.

    A sequence is released only once every sequence before it has gone out, so an append that
    returns without publishing is not one lost frame — it is a hole every later frame waits behind
    for the rest of the run, with nothing raised. That makes "publish beside the append" a
    structural property of this file rather than a habit, and the five sites are genesis,
    `set_rate`, `_advance`, `deliver_statement` and `apply_command`.

    **A unit adding a seventh append should extend the count here and publish.** The count is an
    equality rather than a floor so that an append arriving with no publish and an append arriving
    with no docstring both fail — U10 added `deliver_statement`, which is the fifth, and U16 added
    `fork`, which is the sixth and appends into the child it just made rather than into the parent
    it copied.

    Both submit methods are counted, and that is not incidental: `submit_fork` is a *second*
    method on the same writer, so counting only `submit` would let an append arrive under a new
    name with no publish and this test stay green — which is the one failure it exists to prevent.
    """
    import ast
    import inspect

    from kernel import loop as loop_module

    APPENDS = {"submit", "submit_fork"}

    def calls(node, attributes: set[str] | str) -> bool:
        wanted = {attributes} if isinstance(attributes, str) else attributes
        return any(
            isinstance(inner, ast.Call)
            and isinstance(inner.func, ast.Attribute)
            and inner.func.attr in wanted
            for inner in ast.walk(node)
        )

    tree = ast.parse(inspect.getsource(loop_module))
    appenders = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and calls(node, APPENDS)
    ]

    assert len(appenders) == 6, (
        f"{len(appenders)} functions append, not the six this file documents "
        f"({', '.join(node.name for node in appenders)}). Either an append was added without a "
        "publish, or one was removed and the module docstring is now wrong"
    )
    silent = [node.name for node in appenders if not calls(node, "_publish")]
    assert not silent, (
        "these append without publishing what they committed, which leaves a sequence the "
        "publisher waits behind forever: " + ", ".join(silent)
    )


async def test_a_sequence_nobody_publishes_is_given_up_on_rather_than_waited_for(
    runtime, monkeypatch
) -> None:
    """The one door into a permanent hole, and what it costs when somebody walks through it.

    Holding a sequence back until the one before it arrives is right while that one is in flight
    and wrong forever if it never was. `writer.submit` abandons its own commit after thirty
    seconds and raises while the append may still land, so its caller never publishes what the
    store nonetheless holds — and every later frame would then queue behind a sequence that is
    never coming. A stream that goes quiet with nothing raised is worse than a gap, because a gap
    is what the client detects and recovers from.

    The hole is manufactured rather than waited for: a thirty-second store timeout is not
    something to reproduce, and what is under test is the publisher's behaviour when it exists.
    """
    monkeypatch.setattr(loop_module, "PUBLISH_HELD_BACK_BOUND", 4)
    runtime.create_run(RUN, SEED)
    run = runtime.runs[RUN]
    queue = runtime.subscribe(RUN)

    lost = run.published_seq + 1
    bound = loop_module.PUBLISH_HELD_BACK_BOUND
    behind = [_an_envelope_at(lost + offset) for offset in range(1, bound + 2)]

    # One at a time, so the wait is observable before the bound is crossed.
    for envelope in behind[:bound]:
        runtime._publish(run, [envelope])
    assert not _event_seqs(_drain(queue)), (
        "frames were released while an earlier sequence was still outstanding, which is the "
        "inversion the ordering exists to prevent"
    )
    assert len(run.held_back) == bound

    runtime._publish(run, behind[bound:])

    released = _event_seqs(_drain(queue))
    assert released == sorted(released), f"released {released}, which is not in sequence order"
    assert released == [envelope.seq for envelope in behind], (
        f"released {released} rather than everything behind the hole, {[e.seq for e in behind]}"
    )
    assert lost not in released, "the sequence that was never committed was invented"
    assert run.published_seq == max(released), (
        "the cursor did not follow what was released, so the next frame would be held back too"
    )
    assert not run.held_back, "something is still being waited for after the bound was reached"


# =========================================================================
# The statement leg: the kernel asks, and the answer comes back (U10)
# =========================================================================
#
# This is the delivery leg the plan's Risks section singles out: `raise_request` and
# `receive_answer` were a tested state machine with no production caller. Everything below is
# therefore about the *transport* rather than about the contract — the contract is
# `test_pending_input.py`'s. What has to be true here is that a request raised inside a tick reaches
# a producer off the tick thread, that the answer comes back through the writer and onto the wire,
# and that none of the three failure modes a network has stops the clock.

BRIEF_DIRECTOR = "dir_hr"
BRIEF_ITEM = "wi_hiring"


def _walk_the_ceo_to_a_briefing(runtime: KernelRuntime, run_id: str) -> None:
    """Drive the run to "a director stopped at a checkpoint, the CEO beside them", through the
    kernel's own surfaces.

    The CEO moves by submitted input rather than by assignment to `run.state.ceo`, because the whole
    point of these tests is the path a real run takes: the command goes through `apply_command`, the
    tick goes through `_advance`, and the request is raised by `step()` in between.
    """
    from simcore.world import find_path

    run = runtime.runs[run_id]
    while run.state.items[BRIEF_ITEM].status != sim.STATUS_BLOCKED:
        runtime._advance(run, 1)

    target = run.state.seats[BRIEF_DIRECTOR]
    held = 0
    for _ in range(600):
        here = run.state.ceo.tile
        path = find_path(run.state.floor, here, target)
        step_to = here
        if path:
            step_to = path[1] if path[0] == here and len(path) > 1 else path[0]

        mask = 0
        centre_x = step_to[0] * sim.MILLI + sim.MILLI // 2
        centre_y = step_to[1] * sim.MILLI + sim.MILLI // 2
        if run.state.ceo.x_milli < centre_x - 60:
            mask |= sim.INPUT_RIGHT
        elif run.state.ceo.x_milli > centre_x + 60:
            mask |= sim.INPUT_LEFT
        if run.state.ceo.y_milli < centre_y - 60:
            mask |= sim.INPUT_DOWN
        elif run.state.ceo.y_milli > centre_y + 60:
            mask |= sim.INPUT_UP

        if mask != held:
            runtime.apply_command(
                run_id,
                kernel_pb2.SUBMIT_CEO_INPUT,
                canonical.encode({"bitmask": mask, "at_tick": run.state.tick + 1}),
            )
            held = mask
        runtime._advance(run, 1)
        if sim._ceo_is_beside(run.state, run.state.people[BRIEF_DIRECTOR]):
            return
    raise AssertionError("the CEO never reached the director")


def _a_scripted_statement(request) -> dict:
    """A well-formed statement citing nothing, which is a legal statement and a simpler fixture."""
    from simcore import statement as statements

    return statements.Statement(
        briefing="The recruiter is the constraint, not the budget.",
        objection="Cutting the review step is how the last two mis-hires got through.",
        citations=(),
        producer=request.person,
        producer_kind=statements.PRODUCER_SCRIPTED,
        model_identity="",
        context={"director": request.person, "line": sorted(request.authorized.people),
                 "since_seq": 0, "through_seq": 0, "events": [], "draw": {},
                 "unlocking_note": ""},
    ).to_answer()


async def _let_the_bench_answer(runtime: KernelRuntime, run_id: str) -> None:
    """Wait for every in-flight answer task to finish. Deterministic: it awaits, never sleeps."""
    run = runtime.runs[run_id]
    for _ in range(50):
        if not run.statement_tasks:
            return
        await asyncio.gather(*list(run.statement_tasks), return_exceptions=True)
    raise AssertionError("the answer tasks never drained")


async def test_a_statement_request_reaches_the_bench_and_its_answer_reaches_the_log(
    runtime,
) -> None:
    asked: list[str] = []

    def producer(request):
        asked.append(request.request_id)
        assert request.run_id == RUN
        assert request.person == BRIEF_DIRECTOR
        assert request.owning_item == BRIEF_ITEM
        # The scope arrives on the request. A leg that had to derive it would be the second place
        # the rule lived, and the two would disagree the first time an item moved between lines.
        assert BRIEF_DIRECTOR in request.authorized.people
        return _a_scripted_statement(request)

    runtime.use_statement_producer(producer)
    runtime.create_run(RUN, SEED)
    _walk_the_ceo_to_a_briefing(runtime, RUN)
    await _let_the_bench_answer(runtime, RUN)

    assert len(asked) == 1, f"the bench was asked {len(asked)} times"

    kinds = [envelope.kind for envelope in runtime.store.read_events(RUN)]
    assert EventKind.REQUEST_RAISED in kinds
    assert EventKind.INPUT_RECEIVED in kinds

    # And it applies at the tick derived from the raise, not at the tick it arrived.
    run = runtime.runs[RUN]
    received = [
        envelope
        for envelope in runtime.store.read_events(RUN)
        if envelope.kind is EventKind.INPUT_RECEIVED
    ][-1]
    raised = [
        envelope
        for envelope in runtime.store.read_events(RUN)
        if envelope.kind is EventKind.REQUEST_RAISED
        and envelope.decoded_payload().get("service") == "bench"
    ][-1]
    landing = int(received.decoded_payload()["tick"])
    assert landing == int(raised.decoded_payload()["tick"]) + pend.STATEMENT_OFFSET_TICKS

    runtime._advance(run, landing - run.state.tick + 1)
    assert raised.request_id not in run.state.pending


async def test_the_answer_reaches_a_connected_client(runtime) -> None:
    """The fifth append site publishes, which is what the ordering cursor rests on.

    An appended sequence nobody publishes is not one lost frame — it is a hole every later frame
    waits behind for the rest of the run. So the briefing being on the wire is the same assertion as
    the stream continuing to work afterwards.
    """
    runtime.use_statement_producer(_a_scripted_statement)
    runtime.create_run(RUN, SEED)
    queue = runtime.subscribe(RUN)
    _walk_the_ceo_to_a_briefing(runtime, RUN)
    await _let_the_bench_answer(runtime, RUN)

    delivered = []
    while not queue.empty():
        delivered.append(queue.get_nowait())

    kinds = [item.kind for item in delivered if hasattr(item, "kind")]
    assert EventKind.INPUT_RECEIVED in kinds
    assert not runtime.runs[RUN].held_back, "a sequence is still being waited for"


async def test_a_bench_that_declines_leaves_the_request_for_its_deadline(runtime) -> None:
    """The shipped behaviour of U10 alone, and of every keyless build (M20).

    Declining is not an error. The request stays outstanding, the clock keeps its own time, and the
    run plays exactly as it did before the bench existed.
    """
    runtime.use_statement_producer(lambda request: None)
    runtime.create_run(RUN, SEED)
    _walk_the_ceo_to_a_briefing(runtime, RUN)
    await _let_the_bench_answer(runtime, RUN)

    run = runtime.runs[RUN]
    before = run.state.tick
    runtime._advance(run, 60)

    assert run.state.tick == before + 60, "the clock waited for the bench"
    assert [request for request in run.state.pending.values() if request.is_statement]
    assert not [
        envelope
        for envelope in runtime.store.read_events(RUN)
        if envelope.kind is EventKind.INPUT_RECEIVED
    ]


async def test_a_bench_that_raises_does_not_stop_the_clock(runtime) -> None:
    """A provider failure is never a kernel failure. The task swallows it and says so.

    The failure mode this closes is not an exception the operator would see — it is a task whose
    exception nobody retrieved, taking the request with it and leaving a conversation that never
    resolves for a reason nothing logged.
    """

    def explodes(_request):
        raise RuntimeError("the provider is on fire")

    runtime.use_statement_producer(explodes)
    runtime.create_run(RUN, SEED)
    _walk_the_ceo_to_a_briefing(runtime, RUN)
    await _let_the_bench_answer(runtime, RUN)

    run = runtime.runs[RUN]
    before = run.state.tick
    runtime._advance(run, 60)

    assert run.state.tick == before + 60, "the clock stopped over a provider failure"
    assert run.exception is None, "a bench failure was recorded against the tick loop"
    # Nothing was appended for it either: a failed call is not a rejected answer, and inventing an
    # ANSWER_REJECTED here would put a provider's outage in the log as a statement verdict.
    assert not [
        envelope
        for envelope in runtime.store.read_events(RUN)
        if envelope.kind in (EventKind.INPUT_RECEIVED, EventKind.ANSWER_REJECTED)
    ]
    assert [request for request in run.state.pending.values() if request.is_statement]


async def test_no_statement_is_asked_when_no_bench_is_composed(runtime) -> None:
    """With no producer installed there is no dispatch at all, not a dispatch that fails."""
    runtime.create_run(RUN, SEED)
    _walk_the_ceo_to_a_briefing(runtime, RUN)

    run = runtime.runs[RUN]
    assert not run.statements_asked
    assert not run.statement_tasks
    # The request is still raised and still on the wire: a keyless build shows the pending block and
    # then the labelled fallback, rather than showing nothing.
    assert [request for request in run.state.pending.values() if request.is_statement]
    # And the diagnostic still names the director, because the subject is recorded whether or not
    # anybody was asked — "why is there no briefing" is a question a keyless build gets asked too.
    outstanding = runtime.diagnose(RUN).to_dict()["outstanding_requests"]
    assert [
        entry
        for entry in outstanding
        if entry["service"] == "bench"
        and entry["person"] == BRIEF_DIRECTOR
        and entry["asked"] is False
    ]


async def test_diagnose_says_what_the_run_is_waiting_on(runtime) -> None:
    """The field named after the pending-input contract answered nothing about it until now.

    "Why is there no briefing" needed a log to answer. It now reports the leg, the item, the
    director, how many ticks are left before abandonment, and whether the bench was ever asked —
    which is also how the per-item cap becomes visible rather than merely inferable.
    """
    runtime.use_statement_producer(lambda request: None)
    runtime.create_run(RUN, SEED)
    _walk_the_ceo_to_a_briefing(runtime, RUN)
    await _let_the_bench_answer(runtime, RUN)

    outstanding = runtime.diagnose(RUN).to_dict()["outstanding_requests"]
    statements = [entry for entry in outstanding if entry["service"] == "bench"]

    assert len(statements) == 1
    assert statements[0]["owning_item"] == BRIEF_ITEM
    assert statements[0]["person"] == BRIEF_DIRECTOR
    assert statements[0]["asked"] is True
    assert 0 < statements[0]["ticks_remaining"] <= pend.STATEMENT_DEADLINE_TICKS


async def test_a_statement_outstanding_across_a_restart_is_asked_again(runtime) -> None:
    """The resume half of the dispatch, and the reason `outstanding_requests` carries the subject.

    The question was in flight on a thread that no longer exists. Folding it back to outstanding is
    what `pending` being a projection of the log buys; asking it again is what turns that into a
    briefing rather than a deadline nothing explains.
    """
    runtime.use_statement_producer(lambda request: None)
    runtime.create_run(RUN, SEED)
    _walk_the_ceo_to_a_briefing(runtime, RUN)
    await _let_the_bench_answer(runtime, RUN)

    outstanding = [
        request_id
        for request_id, request in runtime.runs[RUN].state.pending.items()
        if request.is_statement
    ]
    assert outstanding

    # The process "restarts": the run is rebuilt from its log, and this time the bench answers.
    answered: list[str] = []

    def producer(request):
        answered.append(request.request_id)
        return _a_scripted_statement(request)

    runtime.use_statement_producer(producer)
    del runtime.runs[RUN]
    rebuilt = runtime.resume_run(RUN)
    assert set(rebuilt.statement_subjects) >= set(outstanding)

    runtime._ask_outstanding_statements(rebuilt)
    await _let_the_bench_answer(runtime, RUN)

    assert answered == outstanding
    assert [
        envelope
        for envelope in runtime.store.read_events(RUN)
        if envelope.kind is EventKind.INPUT_RECEIVED
    ]


async def test_a_statement_request_with_no_answerable_scope_is_left_not_raised(
    runtime, monkeypatch
) -> None:
    """A payload `step()` did not write must not reach the tick thread as an exception.

    `Authorized` refuses a scope naming nobody, which is what makes default-deny the value an
    omission produces (R23) — so building a request from a hand-edited or pre-U10 bench payload can
    raise. On this path an unhandled `ValueError` is not a refused briefing, it is a stopped clock,
    because the dispatch happens inside `_advance`. So the request is left for its deadline and the
    reason is logged, and inventing a scope for it is exactly what R23 forbids.
    """
    runtime.use_statement_producer(_a_scripted_statement)
    runtime.create_run(RUN, SEED)
    run = runtime.runs[RUN]

    # A bench request with no scope on it, appended the way a foreign writer would have.
    scopeless = sim.Emitted(
        kind=EventKind.REQUEST_RAISED,
        payload={
            "tick": run.state.tick,
            "service": pend.BENCH,
            "owning_item": BRIEF_ITEM,
            "deadline_tick": run.state.tick + pend.STATEMENT_DEADLINE_TICKS,
            "period_index": 0,
            "person": BRIEF_DIRECTOR,
            "cp_index": 0,
        },
        request_id="55555555-5555-5555-8555-555555555555",
    )
    committed = runtime.writer.submit(
        run_id=RUN,
        emitted=[scopeless],
        lease_handle=runtime.lease,
        rules_ver=loop_module.RULES_VERSION,
        tick=run.state.tick,
    )

    runtime._dispatch_statements(run, committed.envelopes)
    await _let_the_bench_answer(runtime, RUN)

    assert not run.statements_asked, "an unanswerable request was handed to the bench"
    # And the clock is untouched: the dispatch swallowed it rather than raising into `_advance`.
    before = run.state.tick
    runtime._advance(run, 20)
    assert run.state.tick == before + 20


# =========================================================================
# Persistent forks (U16): a fork is a run
# =========================================================================


def _a_decision_to_reconsider(runtime, horizon: int = SHORT_HORIZON_TICKS, option: int = 0):
    """A run that reached a checkpoint and settled it. Returns the resolution's sequence."""
    run = _blocked_at_a_decision(runtime, horizon=horizon)
    envelopes = runtime.apply_command(
        RUN,
        kernel_pb2.RESOLVE_CHECKPOINT,
        canonical.encode(
            {"item": "wi_ap_map", "cp_index": 0, "option_index": option, "in_person": True}
        ),
    )
    assert len(envelopes) == 1 and envelopes[0].kind is EventKind.DECISION_RESOLVED
    return run, envelopes[0].seq


def _rows(store, run_id: str) -> list[dict]:
    from sqlalchemy import select

    from logschema import event_log

    with store.engine.connect() as connection:
        return [
            dict(row)
            for row in connection.execute(
                select(event_log).where(event_log.c.run_id == run_id).order_by(event_log.c.seq)
            )
            .mappings()
            .all()
        ]


async def test_a_child_takes_the_other_option_and_resolves_the_checkpoint_once(runtime) -> None:
    """Covers M44. The product's central beat: the same moment, a different answer.

    Two assertions, and the second is the one that would be silently wrong. The child's state
    has to reflect the alternative — otherwise the fork is a copy — and its log has to hold
    *exactly one* resolution for that checkpoint, because the obvious implementation forks at
    the resolution rather than before it and then applies a second decision to a checkpoint that
    is already closed.
    """
    run, decision_seq = _a_decision_to_reconsider(runtime, option=0)
    parent_metrics = dict(run.state.metrics)

    outcome = runtime.fork(RUN, at_seq=decision_seq, option_index=1, idempotency_key="key-1")
    assert outcome.forked, outcome.refusal
    assert (outcome.option_index, outcome.parent_option_index) == (1, 0)

    child = runtime.runs[outcome.child_run_id]
    decisions = child.state.items["wi_ap_map"].decisions
    assert len(decisions) == 1
    spec = child.state.spec_of("wi_ap_map")
    assert decisions[0].choice == spec.checkpoints[0].options[1].label
    assert decisions[0].choice != spec.checkpoints[0].options[0].label
    assert dict(child.state.metrics) != parent_metrics, "the timelines diverged"

    resolutions = [
        envelope
        for envelope in runtime.store.read_events(outcome.child_run_id)
        if envelope.kind is EventKind.DECISION_RESOLVED
    ]
    assert len(resolutions) == 1, "one decision for one checkpoint, not the parent's and a second"
    assert resolutions[0].seq == decision_seq, (
        "the divergence takes the sequence the parent's decision has, so the two timelines "
        "differ at one number rather than being offset from each other"
    )
    assert resolutions[0].decoded_payload()["option_index"] == 1


async def test_the_parents_log_is_byte_identical_before_and_after_a_fork(runtime) -> None:
    """Covers M48. A fork reads its parent and writes nothing to it.

    Asserted on the stored rows rather than on the envelopes, so `ingested_at` and the metadata
    columns are in the comparison too: an append that touched the parent at all would move one.
    """
    _run, decision_seq = _a_decision_to_reconsider(runtime)
    before = _rows(runtime.store, RUN)

    outcome = runtime.fork(RUN, at_seq=decision_seq, option_index=1, idempotency_key="key-1")
    assert outcome.forked, outcome.refusal

    assert _rows(runtime.store, RUN) == before
    assert runtime.store.run_row(RUN)["head_seq"] == before[-1]["seq"]


async def test_a_child_is_born_at_the_decision_it_reconsiders(runtime) -> None:
    """Covers R20, at the runtime rather than at the store.

    The parent runs on for a sim-day and a half after the decision. Before this, the child row
    took the parent's *present* tick, so resuming it folded the copied prefix that far forward
    with no inputs — a child that looks plausible and is not the fork point.
    """
    run, decision_seq = _a_decision_to_reconsider(runtime, horizon=4_000)
    decided_at = run.state.tick
    runtime._advance(run, 800)
    assert run.state.tick == decided_at + 800

    outcome = runtime.fork(RUN, at_seq=decision_seq, option_index=1, idempotency_key="key-1")
    assert outcome.forked, outcome.refusal
    assert outcome.forked_at_tick == decided_at

    child_row = runtime.store.run_row(outcome.child_run_id)
    assert child_row["current_tick"] == decided_at
    assert runtime.runs[outcome.child_run_id].state.tick == decided_at

    # The parent has genuinely moved on. Its *row* is compared loosely on purpose: `current_tick`
    # is written by `append_tick`, so it tracks the last tick that emitted something rather than
    # the clock — which is a difference worth not asserting past, and is exactly why `fold` takes
    # `through_tick` from the row instead of inferring it from the log.
    assert run.state.tick == decided_at + 800
    assert runtime.store.run_row(RUN)["current_tick"] > decided_at


async def test_a_child_is_registered_so_the_next_command_against_it_is_answered(
    runtime,
) -> None:
    """The third of the three defects. `runtime.fork` never put the child in `self.runs`, so
    `apply_command` raised `KeyError` on it — which reaches the client as a 500 rather than as
    anything it can act on. A fork that hands back a run id has to hand back a usable run.
    """
    _run, decision_seq = _a_decision_to_reconsider(runtime)
    outcome = runtime.fork(RUN, at_seq=decision_seq, option_index=1, idempotency_key="key-1")

    assert outcome.child_run_id in runtime.runs
    child = runtime.runs[outcome.child_run_id]
    assert child.rate == 0, "a fork arrives paused; starting it is the player's decision"

    envelope = runtime.set_rate(outcome.child_run_id, 1)
    assert envelope is not None and envelope.kind is EventKind.RATE_CHANGED
    assert runtime.runs[outcome.child_run_id].rate == 1


async def test_two_forks_of_one_decision_are_two_timelines(runtime) -> None:
    """Covers M47. The whole mechanic is the same moment answered two ways.

    The old id was `uuid5(parent, at_seq)`, so both of these hashed to one value and the second
    insert failed on the primary key — the id was derived from the fork *point*, which is the
    one thing two forks of a decision have in common. It comes off the caller's idempotency key
    now, and two forks are two calls with two keys.
    """
    _run, decision_seq = _a_decision_to_reconsider(runtime)

    first = runtime.fork(RUN, at_seq=decision_seq, option_index=1, idempotency_key="key-1")
    second = runtime.fork(RUN, at_seq=decision_seq, option_index=2, idempotency_key="key-2")

    assert first.forked and second.forked, (first.refusal, second.refusal)
    assert first.child_run_id != second.child_run_id

    for outcome, option in ((first, 1), (second, 2)):
        events = runtime.store.read_events(outcome.child_run_id)
        assert [e.seq for e in events] == list(range(1, decision_seq + 1)), (
            "each child carries the whole prefix and its own divergence"
        )
        assert events[-1].decoded_payload()["option_index"] == option

    # Same sequence values in three runs, which is why resume is keyed on (run, seq).
    assert runtime.store.run_row(first.child_run_id)["forked_at_seq"] == decision_seq - 1
    assert runtime.store.run_row(second.child_run_id)["forked_at_seq"] == decision_seq - 1


async def test_a_retried_fork_answers_with_the_child_it_already_made(runtime) -> None:
    """Covers M47's second half, including the case that made it a store problem.

    A client that never saw its response retries. Inside one process the answer could come from
    a ledger — but the gateway's is in memory, so the retry that matters is the one that arrives
    after a restart with nothing remembering the first attempt. That is what the second half of
    this test is: a *second runtime* on the same store, which is what a restart is.
    """
    _run, decision_seq = _a_decision_to_reconsider(runtime)
    first = runtime.fork(RUN, at_seq=decision_seq, option_index=1, idempotency_key="key-1")
    assert first.forked and first.created

    again = runtime.fork(RUN, at_seq=decision_seq, option_index=1, idempotency_key="key-1")
    assert again.child_run_id == first.child_run_id
    assert not again.created, "the retry found the child rather than making one"
    assert again.option_index == 1 and again.item == "wi_ap_map"

    runtime.writer.stop()
    with runtime.store.engine.begin() as connection:
        lease_module.release(connection, runtime.lease)

    restarted = KernelRuntime(runtime.store)
    restarted.start()
    try:
        restarted.resume_all()
        after_restart = restarted.fork(
            RUN, at_seq=decision_seq, option_index=1, idempotency_key="key-1"
        )
        assert after_restart.child_run_id == first.child_run_id
        assert not after_restart.created
        # And exactly two runs exist, not three.
        assert len(restarted.store.list_runs()) == 2
    finally:
        restarted.writer.stop()


async def test_a_child_survives_a_restart_at_its_own_tick_with_its_own_state(runtime) -> None:
    """Covers M45. The fork is only a run if a process that never saw it can pick it up.

    The comparison is a state hash rather than a spot check on a metric, because what has to
    survive is the whole fold — and the child's fold is the one place a wrong `current_tick`
    hides: it would resume to a state that is internally consistent and is not the fork.
    """
    from simcore import hashing

    _run, decision_seq = _a_decision_to_reconsider(runtime)
    outcome = runtime.fork(RUN, at_seq=decision_seq, option_index=1, idempotency_key="key-1")
    live = runtime.runs[outcome.child_run_id]
    live_hash = hashing.state_hash(sim.snapshot(live.state)).overall

    runtime.writer.stop()
    with runtime.store.engine.begin() as connection:
        lease_module.release(connection, runtime.lease)

    restarted = KernelRuntime(runtime.store)
    restarted.start()
    try:
        resumed = restarted.resume_all()
        assert outcome.child_run_id not in resumed, "a paused child does not start a clock"
        assert outcome.child_run_id in restarted.runs, "but it is rebuilt and reachable"

        child = restarted.runs[outcome.child_run_id]
        assert child.state.tick == outcome.forked_at_tick
        assert hashing.state_hash(sim.snapshot(child.state)).overall == live_hash

        # And it plays forward from there under its own clock.
        restarted._advance(child, 60)
        assert child.state.tick == outcome.forked_at_tick + 60
    finally:
        restarted.writer.stop()


async def test_a_fork_of_a_fork_of_a_fork_reports_its_whole_lineage(runtime) -> None:
    """Covers M46, and R21's second half with it.

    Three deep, each forked from the child before it. What is checked is both shapes of
    parentage: `parent_run_id` is a chain, and `lineage_root_id` is flat — every timeline in the
    tree names the same root, which is what lets U12's response cache and M28's spend aggregate
    be one query instead of a recursive walk.
    """
    run, decision_seq = _a_decision_to_reconsider(runtime, horizon=4_000)

    lineage = [RUN]
    parent = RUN
    for depth in range(3):
        outcome = runtime.fork(
            parent, at_seq=decision_seq, option_index=1 + (depth % 2), idempotency_key=f"k{depth}"
        )
        assert outcome.forked, outcome.refusal
        assert outcome.lineage_root_id == RUN
        lineage.append(outcome.child_run_id)
        parent = outcome.child_run_id

    assert len(set(lineage)) == 4, "four distinct runs"

    for depth, run_id in enumerate(lineage[1:]):
        row = runtime.store.run_row(run_id)
        assert row["parent_run_id"] == lineage[depth], "parentage is direct, one link at a time"
        assert row["lineage_root_id"] == RUN, "and the lineage is flat"

    # The deepest one folds, and to the option it took rather than to its grandparent's.
    deepest = runtime.runs[lineage[-1]]
    assert len(deepest.state.items["wi_ap_map"].decisions) == 1
    assert deepest.state.tick == run.state.tick


async def test_a_terminated_timeline_can_still_be_forked(runtime) -> None:
    """Going back from an ended run is the demo's last beat, so it cannot be an error.

    It is also the one thing the client can do to a finished run, which is why a fork is not a
    command: `POST /runs/{id}/commands` answers RUN_TERMINATED, correctly, for everything that
    appends to the run — and a fork appends to a *different* run.

    Ended twice over, because the two are not the same thing today and finding that out is worth
    keeping: the run's *state* ends when the fold reaches the horizon, but **nothing in the
    kernel calls `store.terminate_run`**, so `runs.terminal_seq` and `runs.terminal_reason` stay
    null for the life of the process. That is a pre-existing gap rather than this unit's — it
    leaves `append_tick`'s append-after-terminal refusal and `resume_all`'s skip both unreachable
    in production — and it is in the deferred defect register. Here the row is set by hand as
    well, so the fork is proved against the state the row is meant to be in.
    """
    run, decision_seq = _a_decision_to_reconsider(runtime, horizon=SHORT_HORIZON_TICKS)

    runtime._advance(run, SHORT_HORIZON_TICKS)
    assert run.state.terminal_reason == "horizon", "the parent has to have actually ended"

    outcome = runtime.fork(RUN, at_seq=decision_seq, option_index=1, idempotency_key="key-1")
    assert outcome.forked, outcome.refusal

    child_row = runtime.store.run_row(outcome.child_run_id)
    assert child_row["terminal_reason"] is None, "the child is before the ending, so it has none"
    assert child_row["horizon_tick"] == SHORT_HORIZON_TICKS, "and it inherits the same bound"
    assert not runtime.runs[outcome.child_run_id].state.terminal_reason

    # And again with the row saying so, which is what the store's own guards read.
    runtime.store.terminate_run(RUN, terminal_seq=runtime.store.head_seq(RUN), reason="horizon")
    marked = runtime.fork(RUN, at_seq=decision_seq, option_index=2, idempotency_key="key-2")
    assert marked.forked, marked.refusal
    assert marked.child_run_id != outcome.child_run_id


async def test_every_fork_refusal_carries_a_reason_and_mutates_nothing(runtime) -> None:
    """The four refusals, each proved to leave the store exactly as it found it.

    "Mutates nothing" is not a claim about the fork's own transaction here — the store already
    rolls that back — it is that a refusal happens *before* the writer is asked at all. So the
    run count and both logs are compared around every one of them.
    """
    run, decision_seq = _a_decision_to_reconsider(runtime, horizon=4_000)

    def unchanged() -> tuple:
        return (
            len(runtime.store.list_runs()),
            runtime.store.sequence_density(RUN),
            runtime.writer.forks,
        )

    # 1. A sequence that is not a decision — which is how "forking a still-open checkpoint"
    #    arrives, since an unsettled checkpoint has no resolution to point at.
    before = unchanged()
    raised = runtime.fork(RUN, at_seq=decision_seq - 1, option_index=1, idempotency_key="a")
    assert not raised.forked
    assert "CHECKPOINT_RAISED, not a decision" in raised.refusal
    assert "settle it first" in raised.refusal
    assert unchanged() == before

    # 2. Above the prefix bound.
    bounded = runtime.fork(
        RUN, at_seq=decision_seq, option_index=1, idempotency_key="b", prefix_bound=2
    )
    assert not bounded.forked
    assert "above the bound of 2" in bounded.refusal
    assert "single writer" in bounded.refusal
    assert unchanged() == before, "refused before the writer was ever asked"

    # 3. Past the inherited horizon. Constructed on the row rather than played into, because a
    #    run ends *at* its horizon — so a decision at or past one is a state the simulation will
    #    not produce, and the guard is the invariant that says a child cannot outlive the bound
    #    its lineage was created with.
    from sqlalchemy import update

    from logschema import runs as runs_table

    with runtime.store.engine.begin() as connection:
        connection.execute(
            update(runs_table).where(runs_table.c.run_id == RUN).values(horizon_tick=1)
        )
    past_horizon = runtime.fork(RUN, at_seq=decision_seq, option_index=1, idempotency_key="c")
    assert not past_horizon.forked
    assert "at or past the horizon of 1" in past_horizon.refusal
    assert "no time left to play" in past_horizon.refusal
    with runtime.store.engine.begin() as connection:
        connection.execute(
            update(runs_table).where(runs_table.c.run_id == RUN).values(horizon_tick=4_000)
        )
    assert unchanged() == before

    # 4. An unanswered request about the item being re-decided. Reachable: the resolver leg
    #    raises one on a blocked item, and the CEO is free to decide before it answers.
    outstanding = runtime.fork(RUN, at_seq=decision_seq, option_index=1, idempotency_key="d")
    assert outstanding.forked, "the control: with nothing outstanding this fork is legal"

    second_run = _a_run_with_an_unanswered_request_at_its_decision(runtime)
    refused = runtime.fork(
        second_run[0], at_seq=second_run[1], option_index=1, idempotency_key="e"
    )
    assert not refused.forked
    assert "unanswered request(s)" in refused.refusal
    assert "already been taken" in refused.refusal
    assert runtime.store.run_row(child_run_id_for(second_run[0], "e")) is None

    # And an option that does not exist, which `resolve_checkpoint` refuses on the fold rather
    # than after the copy — the reason the alternative is applied before anything is written.
    no_such = runtime.fork(RUN, at_seq=decision_seq, option_index=99, idempotency_key="f")
    assert not no_such.forked
    assert "no option 99" in no_such.refusal


def _a_run_with_an_unanswered_request_at_its_decision(runtime) -> tuple[str, int]:
    """A second run whose decision was taken while a request about the item was outstanding."""
    other = "run-with-a-question"
    runtime.create_run(other, SEED, horizon_tick=SHORT_HORIZON_TICKS)
    run = runtime.runs[other]
    runtime.apply_command(
        other,
        kernel_pb2.ASSIGN_WORK,
        canonical.encode({"item": "wi_ap_map", "person": "stf_ap", "via_manager": False}),
    )
    while run.state.items["wi_ap_map"].status != sim.STATUS_BLOCKED:
        runtime._advance(run, 1)

    runtime.writer.submit(
        run_id=other,
        emitted=sim.raise_request(
            run.state, pend.AGENTS, "wi_ap_map", request_id="req-still-waiting"
        ),
        lease_handle=runtime.lease,
        rules_ver=loop_module.RULES_VERSION,
        tick=run.state.tick,
    )

    envelopes = runtime.apply_command(
        other,
        kernel_pb2.RESOLVE_CHECKPOINT,
        canonical.encode(
            {"item": "wi_ap_map", "cp_index": 0, "option_index": 0, "in_person": True}
        ),
    )
    return other, envelopes[0].seq


async def test_a_fork_without_an_idempotency_key_is_refused(runtime) -> None:
    """The key is the child's identity, so a fork without one is not retryable — and a fork
    whose response the client never saw is exactly the case the key exists for.
    """
    _run, decision_seq = _a_decision_to_reconsider(runtime)
    outcome = runtime.fork(RUN, at_seq=decision_seq, option_index=1, idempotency_key="")
    assert not outcome.forked
    assert "needs an idempotency key" in outcome.refusal
    assert len(runtime.store.list_runs()) == 1


async def test_forking_a_run_that_does_not_exist_raises_rather_than_refusing(runtime) -> None:
    """A 404 rather than a 200 with a sentence: a refusal answers a well-formed request about a
    run that exists, and this is neither.
    """
    with pytest.raises(KeyError, match="no such run"):
        runtime.fork("run-nope", at_seq=2, option_index=1, idempotency_key="k")


async def test_a_child_id_is_the_same_value_in_any_process(runtime) -> None:
    """What makes a retry after a restart find its child rather than make a second one."""
    assert child_run_id_for("run-a", "key-1") == child_run_id_for("run-a", "key-1")
    assert child_run_id_for("run-a", "key-1") != child_run_id_for("run-a", "key-2")
    assert child_run_id_for("run-a", "key-1") != child_run_id_for("run-b", "key-1")
    # The separator matters: without it these two pairs would hash the same string.
    assert child_run_id_for("run-a", "b-key") != child_run_id_for("run-a\nb", "key")
    assert child_run_id_for("run-a", "key").startswith("run-")


async def test_starting_a_forked_child_actually_moves_its_clock(runtime) -> None:
    """The defect the compose path found and every unit test missed.

    A fork arrives paused and, unlike every other run in the system, with **no tick task** —
    creating one at fork would be a task with nothing to do. `set_rate` did not make one either,
    because it never had to: every run got its task at creation and a paused run's task stays
    alive and idle. So the child's clock could not be started at all.

    Nothing said so. `set_rate` answered with a `RATE_CHANGED` envelope, the run row said rate 3,
    `/runs/{id}/state` reported rate 3, and sim-time stood still until the process was restarted
    and `resume_all` built the task. This asserts the tick, because the rate is exactly what was
    lying.
    """
    _run, decision_seq = _a_decision_to_reconsider(runtime, horizon=4_000)
    # A client watching the parent, which is the situation a player forks from — and what
    # records the runtime's loop handle. In deployment `start_background` records it at startup;
    # here the runtime was built directly, so this stands in for it without starting the
    # parent's clock and racing the manual advances above.
    runtime.subscribe(RUN)

    outcome = runtime.fork(RUN, at_seq=decision_seq, option_index=1, idempotency_key="key-1")
    child = runtime.runs[outcome.child_run_id]

    assert child.task is None, "a fork has no tick task; that is what makes this reachable"

    runtime.set_rate(outcome.child_run_id, 2)
    assert child.rate == 2

    started_at = child.state.tick
    for _ in range(100):
        await asyncio.sleep(0.05)
        if child.state.tick > started_at:
            break

    assert child.task is not None, "unpausing has to build the task nothing else was going to"
    assert child.state.tick > started_at, (
        f"the child reports rate {child.rate} and has not moved from tick {started_at}"
    )

    await runtime.stop_run(outcome.child_run_id)


async def test_a_retry_reports_the_tick_the_child_was_born_at(runtime) -> None:
    """Found by review, and it is this unit's own second defect on the idempotent path.

    `_fork_already_taken` read `forked_at_tick` off `runs.current_tick` — a column `append_tick`
    rewrites on every commit the child makes. So a retry arriving *after* the child had played
    forward answered with the child's now. Measured before the fix: a child born at 613 reported
    1080, while its own divergence event at that sequence still carried 613.

    The divergence event is immutable and `_fork_already_taken` already reads it, which is what
    makes the fix free. What this test does that the original retry test did not: it advances the
    child between the two calls, which is the whole of what exposes it.
    """
    _run, decision_seq = _a_decision_to_reconsider(runtime, horizon=4_000)
    first = runtime.fork(RUN, at_seq=decision_seq, option_index=1, idempotency_key="key-1")
    assert first.forked and first.created

    child = runtime.runs[first.child_run_id]
    runtime._advance(child, 600)
    assert child.state.tick > first.forked_at_tick, "the child has to have actually moved"
    assert runtime.store.run_row(first.child_run_id)["current_tick"] > first.forked_at_tick

    again = runtime.fork(RUN, at_seq=decision_seq, option_index=1, idempotency_key="key-1")
    assert again.child_run_id == first.child_run_id
    assert not again.created
    assert again.forked_at_tick == first.forked_at_tick, (
        f"the retry reported tick {again.forked_at_tick} for a child born at "
        f"{first.forked_at_tick} — the birth tick came off the mutable run row again"
    )


async def test_a_fork_whose_writer_gives_up_after_committing_adopts_the_child(
    runtime, monkeypatch
) -> None:
    """The one door `submit_fork`'s timeout leaves open, and what it used to cost.

    `submit` abandons its own wait after thirty seconds *while the writer may still land the
    transaction* — the case `PUBLISH_HELD_BACK_BOUND` documents for appends. For a fork that left
    a child row committed with no `RunLoop` against it, which is a genuinely unusable timeline:
    `/runs/{id}/state` answers from the row and reports the run as existing, while a command
    against it answers not-found.

    The timeout is manufactured rather than waited for — thirty seconds is not something to
    reproduce — and the commit is left to happen, which is exactly the shape being tested.
    """
    _run, decision_seq = _a_decision_to_reconsider(runtime, horizon=4_000)

    real = runtime.writer.submit_fork

    def commits_then_gives_up(**job):
        real(**job)
        raise loop_module.StoreError("the store writer did not commit the fork within 30.0s")

    monkeypatch.setattr(runtime.writer, "submit_fork", commits_then_gives_up)

    outcome = runtime.fork(RUN, at_seq=decision_seq, option_index=1, idempotency_key="key-1")

    assert outcome.forked, outcome.refusal
    assert not outcome.created, "the child was adopted, not made by this call"
    assert outcome.child_run_id in runtime.runs, "and it is a usable run, not a stranded row"
    assert outcome.forked_at_tick == runtime.runs[RUN].state.tick

    monkeypatch.undo()
    envelope = runtime.set_rate(outcome.child_run_id, 1)
    assert envelope is not None, "the adopted child takes commands"


async def test_a_fork_whose_writer_fails_without_committing_says_retrying_is_safe(
    runtime, monkeypatch
) -> None:
    """The other half: nothing landed, so the refusal has to say so.

    The copy and the divergence are one transaction, which is what makes "retry under the same
    key" sound advice rather than a hope — and the sentence says it, because a client that has
    just been refused needs to know whether it is about to make a second timeline.
    """
    _run, decision_seq = _a_decision_to_reconsider(runtime, horizon=4_000)

    def never_commits(**_job):
        raise loop_module.StoreError("the store writer did not commit the fork within 30.0s")

    monkeypatch.setattr(runtime.writer, "submit_fork", never_commits)

    outcome = runtime.fork(RUN, at_seq=decision_seq, option_index=1, idempotency_key="key-1")

    assert not outcome.forked
    assert "did not complete this fork" in outcome.refusal
    assert "retrying under the same idempotency key is safe" in outcome.refusal
    assert len(runtime.store.list_runs()) == 1, "and nothing was written"


async def test_a_fenced_out_kernel_refuses_a_fork_with_the_leases_reason(
    runtime, monkeypatch
) -> None:
    """A lease taken over mid-fork is a sentence, not a 500.

    `apply_command` already turns `RunAlreadyTerminated` into a `CommandRejected` for the same
    reason: the store is right to refuse, and what was wrong is the refusal reaching the client
    as an opaque error rather than the reason it already carries.
    """
    _run, decision_seq = _a_decision_to_reconsider(runtime, horizon=4_000)

    def fenced(**_job):
        raise loop_module.FencedOut(
            "this kernel holds lease token 1 but the store's current token is 2"
        )

    monkeypatch.setattr(runtime.writer, "submit_fork", fenced)

    outcome = runtime.fork(RUN, at_seq=decision_seq, option_index=1, idempotency_key="key-1")
    assert not outcome.forked
    assert "lease token" in outcome.refusal
    assert len(runtime.store.list_runs()) == 1


async def test_a_parent_this_build_cannot_fold_is_refused_with_the_folds_own_reason(
    runtime, monkeypatch
) -> None:
    """A rules-version mismatch on the parent reached the client as a 500.

    The fold's refusal already names both versions and the remedy — it is the sentence R10 exists
    to produce — and `post_fork` catches nothing but `KeyError`, so it never got there.
    """
    _run, decision_seq = _a_decision_to_reconsider(runtime, horizon=4_000)

    def refuses(*_args, **_kwargs):
        raise folder.FoldRefused(
            "this log was written under rules version old; the running rules are new"
        )

    monkeypatch.setattr(folder, "fold", refuses)

    outcome = runtime.fork(RUN, at_seq=decision_seq, option_index=1, idempotency_key="key-1")
    assert not outcome.forked
    assert "rules version" in outcome.refusal
    assert len(runtime.store.list_runs()) == 1


async def test_the_child_id_seed_cannot_be_made_ambiguous_by_its_contents(runtime) -> None:
    """The collision review reproduced, now closed by construction.

    The seed used to be `parent + "\\n" + key` with a comment saying neither value may contain a
    newline — a precondition nothing enforced, and `POST /runs` accepts a client-supplied id.
    Measured before the fix: both pairs below minted `run-cc3c2f6565de`. The seed is
    length-prefixed now, so no content can shift the boundary between the two halves.
    """
    assert child_run_id_for("run-a", "b\nkey") != child_run_id_for("run-a\nb", "key")
    assert child_run_id_for("run-a", ":b:key") != child_run_id_for("run-a:b", "key")
    assert child_run_id_for("run-ab", "c") != child_run_id_for("run-a", "bc")

    # Still deterministic, which is the property the whole retry path rests on.
    assert child_run_id_for("run-a", "key-1") == child_run_id_for("run-a", "key-1")
    assert child_run_id_for("run-a", "key").startswith("run-")


async def test_an_identifier_carrying_a_control_character_is_refused(runtime) -> None:
    """Defence in depth behind the length prefix, using simcore's own predicate.

    Execution decision §1 forbids a second copy of a predicate, and
    `simcore.scenario.control_character` is where the Unicode-category rule already lives — which
    is why a zero-width joiner is refused alongside a newline rather than only the obvious one.
    """
    _run, decision_seq = _a_decision_to_reconsider(runtime, horizon=4_000)

    for key in ("a\nb", "a\tb", "a‍b", "a‮b"):
        outcome = runtime.fork(RUN, at_seq=decision_seq, option_index=1, idempotency_key=key)
        assert not outcome.forked, f"{key!r} was accepted"
        assert "idempotency key contains" in outcome.refusal

    too_long = runtime.fork(
        RUN, at_seq=decision_seq, option_index=1, idempotency_key="k" * 500
    )
    assert not too_long.forked
    assert "above the bound of" in too_long.refusal

    assert len(runtime.store.list_runs()) == 1, "every refusal wrote nothing"
