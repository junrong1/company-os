"""The gateway: the client's only contact surface.

Driven through the single-process composition, which is a production path (R16) rather than a test
harness — the same application objects the compose topology uses, wired without gRPC. What that
does not cover is stated in `single_process.py`: the Postgres-only hazards and gRPC serialisation,
which belong to the compose path and the contract tests.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from gateway import main as gateway_main
from gateway import stream as streaming
from gateway.commands import CommandLedger, Outcome, submit
from kernel.store import LogStore, make_engine
from simcore import time as simtime

RUN = "run-gateway"
SEED = 0xC0FFEE


@pytest.fixture
def composed(tmp_path, monkeypatch):
    """A kernel and a gateway in one process, with a run ready to drive."""
    monkeypatch.setenv("COMPANY_OS_STORE_URL", f"sqlite:///{tmp_path}/gw.sqlite3")

    import single_process

    runtime, client = single_process.compose()
    runtime.create_run(RUN, SEED, horizon_tick=simtime.TICKS_PER_SIM_DAY * 30)

    # A fresh ledger per test: the ledger is per-process state, and leaking it between tests
    # would make idempotency assertions depend on execution order.
    gateway_main._ledger = CommandLedger()

    yield runtime, client
    runtime.writer.stop()


@pytest.fixture
def api(composed):
    with TestClient(gateway_main.app) as client:
        yield client


def command(api, kind: str, payload: dict, key: str, run_id: str = RUN):
    return api.post(
        f"/runs/{run_id}/commands",
        json={"kind": kind, "payload": payload, "idempotency_key": key},
    )


# =========================================================================
# Commands are applied at a tick boundary and report their outcome
# =========================================================================


def test_a_command_is_applied_and_reports_the_outcome(api) -> None:
    response = command(api, "assign_work", {"item": "wi_faq", "person": "stf_cs"}, "k1")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == Outcome.APPLIED
    assert body["produced_seq"], "an applied command produced no events"
    assert body["command_id"].startswith("cmd-")


def test_the_response_carries_the_gateway_minted_command_id(api) -> None:
    """The causation trace that makes one identifier greppable across seven streams."""
    body = command(api, "assign_work", {"item": "wi_faq", "person": "stf_cs"}, "k1").json()
    command_id = body["command_id"]

    from logschema import event_log
    from sqlalchemy import select

    runtime = gateway_main._kernel.runtime
    with runtime.store.engine.connect() as connection:
        ids = connection.execute(select(event_log.c.command_id)).scalars().all()

    assert command_id in ids, "the command id was not carried onto the events it produced"


def test_a_rejected_command_returns_a_reason_and_mutates_nothing(api) -> None:
    """R11's shape: rejection is information the client renders, not an exception."""
    before = api.get(f"/runs/{RUN}/state").json()

    # wi_close is gated on visibility, so this is refused.
    response = command(api, "assign_work", {"item": "wi_close", "person": "dir_admin"}, "k1")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == Outcome.REJECTED
    assert "visibility" in body["reason"]
    assert body["produced_seq"] == []

    after = api.get(f"/runs/{RUN}/state").json()
    assert after["metrics"] == before["metrics"]


def test_a_command_crossing_a_reporting_line_is_rejected_with_a_reason(api) -> None:
    command(api, "assign_work", {"item": "wi_faq", "person": "stf_cs"}, "k1")

    response = command(
        api, "reassign_work", {"item": "wi_faq", "person": "stf_buyer"}, "k2"
    )

    body = response.json()
    assert body["status"] == Outcome.REJECTED
    assert "reporting line" in body["reason"]
    assert body["produced_seq"] == []


def test_a_command_referencing_an_unknown_run_is_not_found(api) -> None:
    """It never creates one."""
    response = command(api, "assign_work", {"item": "wi_faq", "person": "stf_cs"}, "k1", "nope")
    assert response.status_code == 404


def test_a_command_without_an_idempotency_key_is_refused(api) -> None:
    response = api.post(
        f"/runs/{RUN}/commands",
        json={"kind": "assign_work", "payload": {"item": "wi_faq", "person": "stf_cs"}},
    )
    assert response.json()["status"] == Outcome.REJECTED
    assert "idempotency key" in response.json()["reason"]


# =========================================================================
# Idempotency (R30)
# =========================================================================


def test_a_retried_command_with_the_same_key_applies_once(api) -> None:
    first = command(api, "assign_work", {"item": "wi_faq", "person": "stf_cs"}, "same").json()
    second = command(api, "assign_work", {"item": "wi_faq", "person": "stf_cs"}, "same").json()

    assert first["status"] == Outcome.APPLIED
    assert second["status"] == Outcome.DUPLICATE
    # The *original* outcome, not a fresh one. That is what the key is for.
    assert second["produced_seq"] == first["produced_seq"]


def test_an_outcome_can_be_resolved_by_key_after_a_reconnect(api) -> None:
    """A client that never saw its response resolves it rather than retrying blind."""
    applied = command(api, "assign_work", {"item": "wi_faq", "person": "stf_cs"}, "k9").json()

    recovered = api.get(f"/runs/{RUN}/commands/k9")

    assert recovered.status_code == 200
    assert recovered.json()["produced_seq"] == applied["produced_seq"]


def test_an_unknown_key_says_the_command_was_never_applied(api) -> None:
    response = api.get(f"/runs/{RUN}/commands/never-sent")
    assert response.status_code == 404
    assert "never applied" in response.json()["detail"]


def test_the_ledger_is_keyed_on_run_as_well_as_key(composed) -> None:
    """Two runs may legitimately use the same key, and a fork shares its parent's history."""
    runtime, client = composed
    runtime.create_run("run-other", SEED + 1)
    ledger = CommandLedger()

    first = submit(
        client, ledger, RUN, "assign_work",
        {"item": "wi_faq", "person": "stf_cs"}, "shared", "cmd-1",
    )
    second = submit(
        client, ledger, "run-other", "assign_work",
        {"item": "wi_faq", "person": "stf_cs"}, "shared", "cmd-2",
    )

    assert first.status == Outcome.APPLIED
    assert second.status == Outcome.APPLIED, "the key collided across runs"


# =========================================================================
# A paused run has no tick boundary
# =========================================================================


def test_a_command_submitted_while_paused_resolves_rather_than_hanging(api) -> None:
    """The tempting implementation waits for a boundary that never comes."""
    command(api, "set_rate", {"rate": 0}, "pause")

    response = command(api, "assign_work", {"item": "wi_faq", "person": "stf_cs"}, "k1")

    body = response.json()
    assert body["status"] == Outcome.RUN_PAUSED
    assert "not queued" in body["reason"], "the client must know nothing will happen later"
    assert body["produced_seq"] == []


def test_a_command_after_termination_is_rejected_and_mutates_nothing(composed) -> None:
    runtime, client = composed
    run = runtime.runs[RUN]
    run.state.terminal_reason = "horizon"

    ledger = CommandLedger()
    result = submit(
        client, ledger, RUN, "assign_work",
        {"item": "wi_faq", "person": "stf_cs"}, "k1", "cmd-1",
    )

    assert result.status == Outcome.RUN_TERMINATED
    assert result.produced_seq == []


# =========================================================================
# Resume is keyed on run and sequence (R31)
# =========================================================================


def test_a_client_reconnecting_with_a_stale_sequence_receives_the_missed_window(api) -> None:
    command(api, "assign_work", {"item": "wi_faq", "person": "stf_cs"}, "k1")
    client = gateway_main._kernel

    head = client.head_seq(RUN)
    assert head > 1

    plan = streaming.plan_resume(client, RUN, after_seq=1)

    assert not plan.resync
    assert [envelope.seq for envelope in plan.backlog] == list(range(2, head + 1))


def test_resume_beyond_the_window_returns_a_snapshot_instead_of_events(api) -> None:
    """Replaying a month of history to a client that only needs current state is slower."""
    client = gateway_main._kernel

    # Pretend the head is far ahead of what this client last saw.
    runtime = client.runtime
    original = client.head_seq

    def far_ahead(run_id: str) -> int:
        return streaming.RESUME_WINDOW_EVENTS + 500

    client.head_seq = far_ahead  # type: ignore[method-assign]
    try:
        plan = streaming.plan_resume(client, RUN, after_seq=0)
    finally:
        client.head_seq = original  # type: ignore[method-assign]

    assert plan.resync
    assert plan.backlog == []


def test_a_sequence_ahead_of_the_head_is_refused(api) -> None:
    """A parent's sequence against a forked child names a different event."""
    client = gateway_main._kernel

    with pytest.raises(streaming.ResyncRequired) as excinfo:
        streaming.plan_resume(client, RUN, after_seq=10_000)

    assert "fork shares sequence values" in str(excinfo.value)


def test_sequences_reach_the_client_as_strings(api) -> None:
    """`JSON.parse` loses precision above 2^53, and these are uint64."""
    command(api, "assign_work", {"item": "wi_faq", "person": "stf_cs"}, "k1")
    client = gateway_main._kernel

    frame = streaming.envelope_frame(client.read_events(RUN, after_seq=0)[0])

    assert isinstance(frame["seq"], str)
    assert isinstance(frame["tick"], str)


# =========================================================================
# The outbound queue is bounded (R31)
# =========================================================================


async def test_a_client_that_stops_reading_is_closed_rather_than_buffered() -> None:
    """Buffering unboundedly buys nothing: the client's own resume is the recovery path."""
    connection = streaming.Connection(RUN, limit=4)

    for index in range(4):
        assert connection.offer({"seq": str(index)}) is True

    assert connection.offer({"seq": "overflow"}) is False
    assert connection.dropped is True


async def test_the_sender_guards_every_send_against_a_closed_socket() -> None:
    """A close can land between the queue pop and the write."""
    connection = streaming.Connection(RUN)
    sent: list = []

    async def send(frame):
        if len(sent) == 1:
            raise RuntimeError("socket closed")
        sent.append(frame)

    connection.offer({"seq": "1"})
    connection.offer({"seq": "2"})

    sender = asyncio.create_task(connection.run_sender(send))
    await asyncio.sleep(0.05)
    await connection.close()
    await asyncio.sleep(0)

    assert connection.closed is True
    assert len(sent) == 1, "the sender kept writing after the socket refused"
    sender.cancel()


async def test_the_sender_tracks_the_last_sequence_it_delivered() -> None:
    """So a reconnect can resume from exactly where the stream stopped."""
    connection = streaming.Connection(RUN)
    connection.offer({"seq": "7"})

    sender = asyncio.create_task(connection.run_sender(lambda frame: asyncio.sleep(0)))
    await asyncio.sleep(0.05)

    assert connection.last_sent_seq == 7
    await connection.close()
    sender.cancel()


# =========================================================================
# The stream itself
# =========================================================================


def test_two_clients_on_one_run_both_receive_events(api) -> None:
    with api.websocket_connect(f"/ws/{RUN}?after_seq=0") as first:
        with api.websocket_connect(f"/ws/{RUN}?after_seq=0") as second:
            command(api, "assign_work", {"item": "wi_faq", "person": "stf_cs"}, "k1")

            first_frame = first.receive_json()
            second_frame = second.receive_json()

    assert first_frame["kind"] == "GENESIS"
    assert second_frame["kind"] == "GENESIS"


def test_the_stream_replays_the_backlog_on_connect(api) -> None:
    command(api, "assign_work", {"item": "wi_faq", "person": "stf_cs"}, "k1")

    with api.websocket_connect(f"/ws/{RUN}?after_seq=0") as socket:
        kinds = [socket.receive_json()["kind"] for _ in range(2)]

    assert kinds[0] == "GENESIS"
    assert "WORK_ASSIGNED" in kinds


def test_a_subscriber_never_receives_an_event_beyond_the_durable_head(api) -> None:
    """R20: no event reaches a subscriber before it is durable."""
    command(api, "assign_work", {"item": "wi_faq", "person": "stf_cs"}, "k1")
    client = gateway_main._kernel
    head = client.head_seq(RUN)

    with api.websocket_connect(f"/ws/{RUN}?after_seq=0") as socket:
        frames = [socket.receive_json() for _ in range(2)]

    for frame in frames:
        assert int(frame["seq"]) <= head


# =========================================================================
# Exposure (R34) and the kernel dependency
# =========================================================================


def test_the_gateway_reports_an_in_process_kernel(api) -> None:
    body = api.get("/status").json()
    kernel_dep = body["dependencies"][0]

    assert kernel_dep["name"] == "kernel"
    assert kernel_dep["required"] is False
    assert "single-process" in kernel_dep["detail"]


def test_the_gateway_does_not_import_the_kernel() -> None:
    """R4: the gateway and the kernel meet at a proto contract.

    The single-process composition lives outside both services, because composing them is
    neither service's job.
    """
    import ast
    import pathlib

    from gateway import main as module

    service = pathlib.Path(module.__file__).parent
    for path in service.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith("kernel"), f"{path.name} imports {node.module}"
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("kernel"), f"{path.name} imports {alias.name}"


def test_the_gateway_answers_without_a_kernel_client(monkeypatch) -> None:
    """It stays up to explain the outage rather than joining it."""
    monkeypatch.setattr(gateway_main, "_kernel", None)

    with TestClient(gateway_main.app) as client:
        status = client.get("/status")
        assert status.status_code == 200, "the gateway failed its own health"

        command = client.post(
            f"/runs/{RUN}/commands",
            json={"kind": "assign_work", "payload": {}, "idempotency_key": "k"},
        )
        assert command.status_code == 503
        assert "no kernel client" in command.json()["detail"]


# =========================================================================
# U10's verification
# =========================================================================


def test_a_client_drives_a_run_through_assignment_decision_and_hire(api, composed) -> None:
    """The whole surface, over REST, while the stream stays gap-free."""
    runtime, client = composed
    run = runtime.runs[RUN]

    assert command(api, "assign_work", {"item": "wi_faq", "person": "stf_cs"}, "a1").json()[
        "status"
    ] == Outcome.APPLIED

    # Advance to the checkpoint the way the tick loop would, appending each tick.
    from simcore import step as sim
    from simcore.rates import RULES_VERSION

    for _ in range(40_000):
        emitted = sim.step(run.state)
        if emitted:
            runtime.writer.submit(
                run_id=RUN,
                emitted=emitted,
                lease_handle=runtime.lease,
                rules_ver=RULES_VERSION,
                tick=run.state.tick,
            )
        if run.state.items["wi_faq"].status == sim.STATUS_BLOCKED:
            break

    assert run.state.items["wi_faq"].status == sim.STATUS_BLOCKED

    decided = command(
        api,
        "resolve_checkpoint",
        {"item": "wi_faq", "cp_index": 0, "option_index": 0, "in_person": True},
        "a2",
    ).json()
    assert decided["status"] == Outcome.APPLIED

    hired = command(api, "request_hire", {"director": "dir_cs"}, "a3").json()
    assert hired["status"] == Outcome.APPLIED

    # And the log is gap-free across all of it.
    seqs = [envelope.seq for envelope in client.read_events(RUN, after_seq=0)]
    assert seqs == list(range(1, len(seqs) + 1)), "the event stream has a gap"


# =========================================================================
# Creating a run
# =========================================================================


def test_a_run_can_be_created_over_the_api(api) -> None:
    """The gap that made every other route unreachable in practice.

    Nothing could create a run before this: creation is deliberately not a command, and no route
    reached the kernel's `create_run`. A client could stream and command only a run that some other
    process had already made.
    """
    response = api.post("/runs", json={"run_seed": 12345})

    assert response.status_code == 200
    body = response.json()
    assert body["created"] is True
    assert body["run_id"].startswith("run-")
    assert body["run_seed"] == 12345
    assert body["rate"] == 1, "a new run's clock is running, not paused"
    assert body["active"] is True

    # And it is immediately usable through the routes that need an existing run.
    state = api.get(f"/runs/{body['run_id']}/state")
    assert state.status_code == 200
    assert state.json()["run_id"] == body["run_id"]


def test_creating_a_run_mints_a_seed_and_reports_it(api) -> None:
    """The seed is the run's identity for reproduction, so a caller must learn it.

    Two fresh runs from one seed produce identical logs (R11); a caller that let the server pick
    one and never saw it back would have no way to ask for that run again.
    """
    response = api.post("/runs", json={})

    body = response.json()
    assert isinstance(body["run_seed"], int)
    assert body["run_seed"] > 0


def test_creating_the_same_run_id_twice_returns_the_first_run(api) -> None:
    """The run id is its own idempotency key.

    A client retrying a request whose response it never saw must not end up with two runs, and must
    not be told its retry failed.
    """
    first = api.post("/runs", json={"run_id": "run-idem", "run_seed": 7}).json()
    assert first["created"] is True

    second = api.post("/runs", json={"run_id": "run-idem", "run_seed": 999}).json()

    assert second["created"] is False
    assert second["run_id"] == "run-idem"


def test_a_created_run_advances_its_own_clock(composed) -> None:
    """The other half of the gap: `ensure_loop` had no production caller at all.

    Before this, a created run held a genesis event and never ticked — the kernel would hold the
    lease, report healthy, and advance nothing.
    """
    runtime, client = composed

    async def drive() -> int:
        client.create_run("run-ticking", SEED, horizon_tick=simtime.TICKS_PER_SIM_DAY)
        run = runtime.runs["run-ticking"]
        assert run.task is not None, "creating a run did not start its clock"

        for _ in range(200):
            await asyncio.sleep(0.01)
            if run.state.tick > 0:
                return run.state.tick
        return run.state.tick

    assert asyncio.run(drive()) > 0, "the clock never advanced"


# =========================================================================
# Runs survive a restart
# =========================================================================


def test_a_restart_resumes_the_clock_of_a_running_run(tmp_path, monkeypatch) -> None:
    """Rate is run state (R18), so a restart picks the clock back up.

    This is what `resume_all` exists for. Without it a process restart left every run stopped while
    the kernel reported itself healthy — indistinguishable, from outside, from a paused run.

    The first kernel is shut down with `stop()` rather than just stopping its writer, because that
    is what releases the writer lease. Two kernels against one store is exactly what the lease
    exists to prevent (R26), so a test that skipped the release would be blocked by a working
    safeguard rather than exercising resumption.
    """
    monkeypatch.setenv("COMPANY_OS_STORE_URL", f"sqlite:///{tmp_path}/resume.sqlite3")

    import single_process

    async def restart() -> None:
        first, _ = single_process.compose()
        first.create_run("run-resumed", SEED, horizon_tick=simtime.TICKS_PER_SIM_DAY * 5)
        first.create_run("run-paused", SEED + 1, horizon_tick=simtime.TICKS_PER_SIM_DAY * 5)
        first.set_rate("run-paused", 0)
        await first.stop()

        # A second kernel against the same store, which is what a restart is.
        second, _ = single_process.compose()
        try:
            started = second.resume_all()

            assert "run-resumed" in started, "a running run's clock did not come back"
            assert "run-paused" not in started, "a paused run must stay paused across a restart"
            assert second.runs["run-paused"].rate == 0
            assert second.runs["run-resumed"].task is not None
        finally:
            await second.stop()

    asyncio.run(restart())


def test_resumption_skips_a_terminated_run(tmp_path, monkeypatch) -> None:
    """Nothing appends after a terminal event, so a loop for one would wake forever with no work."""
    monkeypatch.setenv("COMPANY_OS_STORE_URL", f"sqlite:///{tmp_path}/terminal.sqlite3")

    import single_process

    async def restart() -> None:
        first, _ = single_process.compose()
        first.create_run("run-over", SEED, horizon_tick=simtime.TICKS_PER_SIM_DAY)
        first.store.terminate_run("run-over", terminal_seq=1, reason="horizon")
        await first.stop()

        second, _ = single_process.compose()
        try:
            assert second.resume_all() == []
            assert "run-over" not in second.runs
        finally:
            await second.stop()

    asyncio.run(restart())


def test_listing_runs_is_ordered_deterministically(composed) -> None:
    """Resumption order must not depend on what the store happened to return."""
    runtime, _ = composed
    runtime.create_run("run-b", SEED + 1)
    runtime.create_run("run-a", SEED + 2)

    ids = [row["run_id"] for row in runtime.store.list_runs()]

    assert RUN in ids and "run-a" in ids and "run-b" in ids
    assert ids == [row["run_id"] for row in runtime.store.list_runs()], "unstable ordering"
