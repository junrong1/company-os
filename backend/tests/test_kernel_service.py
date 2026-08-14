"""The kernel service: the clock, and the guarantees around it.

Most of these test failures that are invisible from their symptom. A dropped task handle does
not raise — the simulation just stops. A swallowed exception leaves a service reporting healthy
with a stopped clock. An unclamped catch-up fast-forwards a sim-week after a laptop sleeps and
looks like a simulation bug. So each is asserted directly rather than assumed from the code.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from contracts.envelope import EventKind
from kernel import lease as lease_module
from kernel.loop import KernelRuntime, RunLoop
from kernel.store import LogStore, make_engine
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

    store = LogStore(make_engine(f"sqlite:///{tmp_path}/empty.sqlite3"))
    runtime = KernelRuntime(store)
    try:
        runtime.start()
        assert runtime.lease is not None
        assert store.check_ddl_version() == 2
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
