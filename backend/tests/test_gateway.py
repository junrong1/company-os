"""The gateway: the client's only contact surface.

Driven through `single_process.compose()`, which is *the* production composition rather than a test
harness — it is what the `backend` container runs, so these tests exercise the wiring the one
command ships and not a second topology built for them. What a run here does not cover is stated in
`single_process.py`: the Postgres-only hazards, because the store defaults to SQLite. Those belong
to the store suite's two dialects and to the compose path, which points this same launcher at
Postgres.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import WebSocketDisconnect
from fastapi.testclient import TestClient

from agents import main as agents_main
from contracts import canonical
from contracts.envelope import EventKind
from gateway import main as gateway_main
from gateway import stream as streaming
from gateway.commands import CommandLedger, Outcome, submit
from kernel.store import LogStore, make_engine
from simcore import time as simtime
from simcore.step import CommandRejected

RUN = "run-gateway"
SEED = 0xC0FFEE


@pytest.fixture
def composed(tmp_path, monkeypatch):
    """A kernel and a gateway in one process, with a run ready to drive."""
    monkeypatch.setenv("COMPANY_OS_STORE_URL", f"sqlite:///{tmp_path}/gw.sqlite3")

    import single_process

    # The spend ledger holds one engine for the process, built on first use — which is
    # inside `compose()`. Left over from a previous test it would still be pointed at that
    # test's store, so the spend frame this run published would be counting somebody else's
    # calls. Cleared *before* composing, because composing is what builds it.
    #
    # The statement leg's log reader (U10) is the same shape of per-process handle and needs the
    # same clearing, and the consequence of forgetting is worse rather than merely different: a
    # director would be briefed from the events of whichever store the previous test wrote.
    agents_main._LEDGER = None
    agents_main._dispose_log_engine()

    runtime, client = single_process.compose()
    runtime.create_run(RUN, SEED, horizon_tick=simtime.TICKS_PER_SIM_DAY * 30)

    # A fresh ledger per test: the ledger is per-process state, and leaking it between tests
    # would make idempotency assertions depend on execution order.
    gateway_main._ledger = CommandLedger()

    yield runtime, client
    runtime.writer.stop()
    agents_main.ledger().dispose()
    agents_main._LEDGER = None
    agents_main._dispose_log_engine()


@pytest.fixture
def api(composed):
    with TestClient(gateway_main.app) as client:
        yield client


def command(api, kind: str, payload: dict, key: str, run_id: str = RUN):
    return api.post(
        f"/runs/{run_id}/commands",
        json={"kind": kind, "payload": payload, "idempotency_key": key},
    )


def _kind(name: str) -> int:
    """One proto command enum value, for the few tests that drive the runtime directly."""
    from contracts.grpc import kernel_pb2

    return getattr(kernel_pb2, name)


def _encode(payload: dict) -> bytes:
    from contracts import canonical

    return canonical.encode(payload)


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


def test_set_rate_is_accepted_while_paused_so_a_pause_is_not_a_one_way_door(api) -> None:
    """The guard must not reject the one command that can lift the pause.

    Guarding `set_rate` alongside everything else makes a paused run permanently unreachable —
    and, because rate is run state (R18), a restart reloads it still paused. The rejection would
    even tell the caller to set a non-zero rate, which is the request it just refused.
    """
    command(api, "set_rate", {"rate": 0}, "pause")

    resumed = command(api, "set_rate", {"rate": 1}, "resume").json()

    assert resumed["status"] == Outcome.APPLIED, resumed["reason"]

    # And the run is genuinely driveable again, not merely reported as resumed.
    assigned = command(api, "assign_work", {"item": "wi_faq", "person": "stf_cs"}, "k1").json()
    assert assigned["status"] != Outcome.RUN_PAUSED


def test_a_comparison_is_accepted_while_paused_unlike_every_other_player_command(
    composed, api
) -> None:
    """The second exemption from the pause guard, and the reason it is sound.

    The guard exists because a command that mutates state needs a tick boundary to mutate it
    at, and a paused run never reaches one. A comparison mutates nothing — it steps a copy —
    so the guard's own premise does not hold for it. Pausing to weigh two options is also
    precisely when a CEO wants one, so rejecting it here would refuse the mechanic at the
    moment it is most useful.

    Asserted beside a command that *is* refused, so this states an exemption rather than a
    guard that has stopped working.
    """
    runtime, _ = composed
    run = runtime.runs[RUN]

    runtime.apply_command(
        RUN,
        _kind("ASSIGN_WORK"),
        _encode({"item": "wi_ap_map", "person": "stf_ap", "via_manager": False}),
    )
    while run.state.items["wi_ap_map"].status != "blocked":
        runtime._advance(run, 1)

    command(api, "set_rate", {"rate": 0}, "pause")

    refused = command(api, "assign_work", {"item": "wi_faq", "person": "stf_cs"}, "k1").json()
    assert refused["status"] == Outcome.RUN_PAUSED

    compared = command(
        api,
        "compare_options",
        {
            "item": "wi_ap_map",
            "cp_index": 0,
            "person": "stf_ap",
            "at_tick": run.state.tick,
            "in_person": True,
        },
        "compare-while-paused",
    ).json()

    assert compared["status"] == Outcome.APPLIED, compared["reason"]
    assert len(compared["produced_seq"]) == 1
    # And the pause held: a comparison stops no clock and starts none.
    assert runtime.runs[RUN].rate == 0


# =========================================================================
# The comparison, through the whole composed stack (R8, AE23)
# =========================================================================


def _stopped_at_a_decision(runtime, item: str = "wi_ap_map", person: str = "stf_ap"):
    """Drive the composed run until somebody is waiting on the CEO."""
    run = runtime.runs[RUN]
    runtime.apply_command(
        RUN,
        _kind("ASSIGN_WORK"),
        _encode({"item": item, "person": person, "via_manager": False}),
    )
    while run.state.items[item].status != "blocked":
        runtime._advance(run, 1)
    return run


def test_a_comparison_through_the_composed_stack_returns_branch_summaries(
    composed, api
) -> None:
    """AE23. The command path, the guard, the event and the client's read, together.

    Driven through the composed stack rather than against the kernel directly, because the
    repo's documented failure mode is golden-tested code with no call path — a capability that
    passes its own tests and reaches no surface is not done. No model key is configured here
    and none exists anywhere in this repo; the branches are arithmetic.
    """
    runtime, client = composed
    run = _stopped_at_a_decision(runtime)

    response = command(
        api,
        "compare_options",
        {
            "item": "wi_ap_map",
            "cp_index": 0,
            "person": "stf_ap",
            "at_tick": run.state.tick,
            "in_person": True,
        },
        "compare-1",
    ).json()

    assert response["status"] == Outcome.APPLIED, response["reason"]
    assert len(response["produced_seq"]) == 1

    # And the record the client will read is on the log, complete.
    [record] = [
        event
        for event in client.read_events(RUN, after_seq=0)
        if event.kind is EventKind.OPTIONS_COMPARED
    ]
    payload = record.decoded_payload()

    assert payload["item"] == "wi_ap_map"
    assert len(payload["branches"]) == 3
    for branch in payload["branches"]:
        assert branch["trajectories"]
        assert branch["metrics"]["cash"]["at_tick"] == branch["stop_tick"]
        assert branch["stop_reason"] in ("checkpoint", "horizon", "insolvent")


def test_a_comparison_advances_nothing_and_leaves_the_run_where_it_was(composed, api) -> None:
    """AE9 through the stack: the command that appends without mutating."""
    runtime, _ = composed
    run = _stopped_at_a_decision(runtime)

    from simcore import hashing
    from simcore import step as sim

    before = hashing.state_hash(sim.snapshot(run.state)).overall
    before_tick = run.state.tick

    command(
        api,
        "compare_options",
        {
            "item": "wi_ap_map",
            "cp_index": 0,
            "person": "stf_ap",
            "at_tick": before_tick,
            "in_person": True,
        },
        "compare-1",
    )

    assert hashing.state_hash(sim.snapshot(run.state)).overall == before
    assert run.state.tick == before_tick


def test_a_stale_comparison_reaches_the_client_as_a_reason_not_an_error(composed, api) -> None:
    """AE19. A rejection is a successful request whose answer is "no".

    Answered 200 with a sentence rather than 4xx, because the client's banner shows the reason
    and an error status would be handled by the transport layer as a failed request — which
    reads as a dead button rather than as an explanation.
    """
    runtime, _ = composed
    run = _stopped_at_a_decision(runtime)
    at_tick = run.state.tick

    # Settle it, so the comparison the CEO was about to ask for is against a run that has gone.
    runtime.apply_command(
        RUN,
        _kind("RESOLVE_CHECKPOINT"),
        _encode({"item": "wi_ap_map", "cp_index": 0, "option_index": 0, "in_person": True}),
    )

    response = command(
        api,
        "compare_options",
        {
            "item": "wi_ap_map",
            "cp_index": 0,
            "person": "stf_ap",
            "at_tick": at_tick,
            "in_person": True,
        },
        "compare-stale",
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == Outcome.REJECTED
    assert "stale" in body["reason"]
    assert body["produced_seq"] == [], "a rejection must produce no events"


def test_a_run_resumed_after_a_restart_can_still_be_compared(tmp_path, monkeypatch) -> None:
    """The comparison needs nothing but the state a resume rebuilds.

    A branch forks from live state, so a run reconstructed from its log has everything a
    comparison needs — and nothing about the comparison is itself persisted state, so there is
    no risk of a resumed run holding a half-written one.
    """
    monkeypatch.setenv("COMPANY_OS_STORE_URL", f"sqlite:///{tmp_path}/compare-resume.sqlite3")

    import single_process
    from simcore import step as sim

    async def restart() -> None:
        # `stop()` rather than only stopping the writer, because that is what releases the
        # lease. Two kernels against one store is what the lease exists to prevent, so a test
        # that skipped the release would be blocked by a working safeguard.
        first, _ = single_process.compose()
        first.create_run(RUN, SEED, horizon_tick=simtime.TICKS_PER_SIM_DAY * 30)
        stopped_at_tick = _stopped_at_a_decision(first).state.tick
        await first.stop()

        second, _ = single_process.compose()
        try:
            resumed = second.resume_run(RUN)
            assert resumed.state.tick == stopped_at_tick
            assert resumed.state.items["wi_ap_map"].status == "blocked"

            envelopes = second.apply_command(
                RUN,
                _kind("COMPARE_OPTIONS"),
                _encode(
                    {
                        "item": "wi_ap_map",
                        "cp_index": 0,
                        "person": "stf_ap",
                        "at_tick": resumed.state.tick,
                        "in_person": True,
                    }
                ),
            )

            assert len(envelopes) == 1
            assert envelopes[0].kind is EventKind.OPTIONS_COMPARED
            assert len(canonical.decode(envelopes[0].payload)["branches"]) == 3
            # And the resumed run is still stopped exactly where it was.
            assert resumed.state.tick == stopped_at_tick
            assert sim.snapshot(resumed.state)["items"]["wi_ap_map"]["status"] == "blocked"
        finally:
            await second.stop()

    asyncio.run(restart())


def test_a_malformed_number_is_a_reason_rather_than_a_500(composed, api) -> None:
    """A client mistake is answered, not crashed on.

    `canonical` rejects floats but passes strings and nulls straight through, so a bare
    `int(...)` over a payload field is a 500 waiting to happen — and nothing above the dispatch
    catches anything but `CommandRejected`, so it reached FastAPI's default handler. Verified
    against every wrong shape a browser can actually send.

    Swept across the older commands too: they shared the exposure, and fixing it for the new
    one while leaving `resolve_checkpoint` crashing on the same input would have been a strange
    place to stop.
    """
    runtime, _ = composed
    run = _stopped_at_a_decision(runtime)

    for label, value in (("a word", "abc"), ("null", None), ("a list", [0]), ("huge", "9" * 5000)):
        for field in ("cp_index", "at_tick"):
            payload = {
                "item": "wi_ap_map",
                "cp_index": 0,
                "person": "stf_ap",
                "at_tick": run.state.tick,
                "in_person": True,
            }
            payload[field] = value
            response = command(api, "compare_options", payload, f"{field}-{label}")

            assert response.status_code == 200, f"{field}={label!r} crashed the request"
            body = response.json()
            assert body["status"] == Outcome.REJECTED
            assert field in body["reason"]
            assert body["produced_seq"] == []

    # The two commands that carried the same shape before this change.
    older = command(
        api,
        "resolve_checkpoint",
        {"item": "wi_ap_map", "cp_index": "x", "option_index": 0, "in_person": True},
        "older-resolve",
    )
    assert older.status_code == 200 and older.json()["status"] == Outcome.REJECTED

    ceo = command(api, "submit_ceo_input", {"bitmask": None, "at_tick": "1"}, "older-input")
    assert ceo.status_code == 200 and ceo.json()["status"] == Outcome.REJECTED


def test_a_run_that_ends_mid_command_answers_with_a_reason(composed) -> None:
    """The race the append-only store is right to refuse, answered rather than crashed on.

    Every caller checks for a terminal run before dispatching, so this is the window between
    that check and the append — ordinarily microseconds, but over a second for a comparison,
    which is the only command that spends that long between its guard and its write. The store
    refuses correctly; what was wrong is that its refusal reached the client as an opaque 500.
    """
    runtime, _ = composed
    run = _stopped_at_a_decision(runtime)

    # The guard passes, and the run ends before the write — exactly the race, made certain.
    run.state.terminal_reason = ""
    payload = _encode(
        {
            "item": "wi_ap_map",
            "cp_index": 0,
            "person": "stf_ap",
            "at_tick": run.state.tick,
            "in_person": True,
        }
    )
    runtime.store.terminate_run(
        RUN, terminal_seq=runtime.store.head_seq(RUN), reason="horizon"
    )

    with pytest.raises(CommandRejected) as refusal:
        runtime.apply_command(RUN, _kind("COMPARE_OPTIONS"), payload)

    assert str(refusal.value)
    assert "500" not in str(refusal.value)


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
# Request origin (R26)
# =========================================================================


def test_a_stream_connection_from_another_origin_is_refused(api) -> None:
    """R26. Loopback is not a boundary against a browser.

    A WebSocket handshake is exempt from every cross-origin rule the browser applies to
    `fetch`: no preflight, no `Access-Control-*`, the socket simply connects. So any page
    the operator has open could read a run's event stream — and this port now fronts the
    report, the diagnose call and the domain surface too.
    """
    with pytest.raises(WebSocketDisconnect) as refused:
        with api.websocket_connect(
            f"/ws/{RUN}", headers={"origin": "https://evil.example", "host": "testserver"}
        ) as socket:
            socket.receive_json()

    assert refused.value.code == 1008, "policy violation is the close code for this"


def test_a_stream_connection_from_the_clients_own_origin_is_accepted(api) -> None:
    """The other half, and the half that would silently break the product if it failed.

    "Its own" is the `Host` header rather than a configured hostname: the client reaches
    this port under three authorities — direct, through nginx, through the dev server —
    and pinning a list would mean editing the backend to change a published port.
    """
    with api.websocket_connect(
        f"/ws/{RUN}", headers={"origin": "http://testserver", "host": "testserver"}
    ) as socket:
        assert socket.receive_json()["kind"] == "GENESIS"


def test_a_handshake_with_no_origin_at_all_is_accepted(api) -> None:
    """curl, `websocat`, the CLI and this test client send none.

    A browser always sends `Origin` on a handshake, so its absence means the caller is not
    a browser — and a hostile page cannot become one by omitting the header, because the
    browser writes it. Refusing them would harden nothing and would break every
    non-browser consumer of the stream.
    """
    with api.websocket_connect(f"/ws/{RUN}") as socket:
        assert socket.receive_json()["kind"] == "GENESIS"


def test_the_authority_is_compared_and_the_scheme_is_not() -> None:
    """Behind a proxy this process cannot see the scheme, and must not guess at it.

    nginx sends no `X-Forwarded-Proto`, so `https` upstream is indistinguishable from
    `http`; the vite dev server rewrites the origin to its own target, which it spells
    `ws://` where the same address reached directly is spelled `http://`. A foreign
    origin differs in the authority every time, which is what is compared.
    """
    assert gateway_main.origin_is_our_own("http://127.0.0.1:8790", "127.0.0.1:8790")
    assert gateway_main.origin_is_our_own("ws://127.0.0.1:8800", "127.0.0.1:8800")
    assert gateway_main.origin_is_our_own("https://127.0.0.1:8790", "127.0.0.1:8790")

    assert not gateway_main.origin_is_our_own("http://evil.example", "127.0.0.1:8790")
    # A port is part of an authority: another server on this machine is not this server.
    assert not gateway_main.origin_is_our_own("http://127.0.0.1:5173", "127.0.0.1:8800")


def test_a_deliberate_second_front_end_can_be_named(monkeypatch) -> None:
    """The escape hatch, for a proxy that does not forward `Host` unchanged.

    Empty in every shipped configuration — nginx forwards `$http_host` and the dev server
    rewrites the origin, so both are same-origin by construction. A value here is
    somebody's decision rather than something the product needs.
    """
    monkeypatch.setenv(gateway_main.ENV_ALLOWED_ORIGINS, "http://studio.local:3000, http://x:1")

    assert gateway_main.origin_is_our_own("http://studio.local:3000", "127.0.0.1:8800")
    assert not gateway_main.origin_is_our_own("http://studio.local:3001", "127.0.0.1:8800")


# =========================================================================
# The model-spend control frame (M28)
# =========================================================================


def test_the_spend_frame_reaches_a_subscriber_on_connect(api) -> None:
    """M28's tile has nothing to render until something publishes.

    U9 built the counter, the read and the client's reducer, and nothing sent the frame —
    so the tile showed its zero-and-absent state for the life of every run, keyed or not.
    The first frame matters even for a keyless run, because it is what carries the
    ceiling: before it the tile shows "of —", which is honest and useless.
    """
    frame = _first_spend_frame(api)

    assert frame is not None, "no MODEL_SPEND frame was published"
    assert frame["run_id"] == RUN
    assert frame["max_calls"] == 200, "the shipped ceiling, reported rather than guessed"
    assert frame["bench_present"] is False, "no key configured is a supported state"


def test_the_spend_frame_carries_every_field_the_clients_reducer_reads(api) -> None:
    """The wire contract, from the side that sends it.

    `readSpend` in `frontend/src/net/store.ts` reads these nine keys and defends against
    each being absent — which means a publisher that omitted one would produce a tile
    showing zero rather than an error anybody could see. Asserting the keys here is what
    turns that defence into a belt rather than the only strap.
    """
    frame = _first_spend_frame(api)

    assert frame is not None
    for field in (
        "calls",
        "tokens",
        "cache_hits",
        "max_calls",
        "max_tokens",
        "lineage_calls",
        "lineage_tokens",
        "bench_present",
        "quiet",
    ):
        assert field in frame, field

    # R6 by omission: which provider answered is not on this wire, and the tile has no
    # use for it. `ModelGateway.describe()` reports it where an operator wants it.
    assert "provider" not in frame and "model" not in frame and "api_key" not in frame


def test_the_spend_frame_moves_during_a_run(api, monkeypatch) -> None:
    """M28 says the HUD updates *during* a run, which is the whole of why this exists.

    A counter read once at connect would satisfy a screenshot and nothing else: the
    figure it is about is the one that grows while the player is briefing somebody.
    """
    from modelgw.ceiling import Spend

    # Shortened rather than waited out: the claim is that a moving counter reaches the
    # client, not that it takes two seconds to. The route reads this when a connection
    # opens, which is what makes it overridable here at all.
    monkeypatch.setattr(streaming, "SPEND_POLL_SECONDS", 0.02)

    with api.websocket_connect(f"/ws/{RUN}") as socket:
        first = _drain_for_spend(socket)
        assert first is not None and first["calls"] == 0

        agents_main.ledger().add(RUN, Spend(calls=3, input_tokens=120, output_tokens=40))

        moved = _drain_for_spend(socket, limit=200)

    assert moved is not None, "the counter moved and the stream never said so"
    assert moved["calls"] == 3
    assert moved["tokens"] == 160
    # The lineage total is a join to `runs.lineage_root_id`, and a run is its own root at
    # creation — so it equals this run's spend until U16 makes a child point at a parent.
    assert moved["lineage_calls"] == 3


def test_the_spend_frame_is_not_an_event(api) -> None:
    """A control frame, and U9's docstring says why it can never be anything else.

    What a call cost depends on which provider answered and what it counted, so an event
    carrying it would be an output the fold cannot reproduce — strict replay would then
    fail on every run that used the bench. The client's `isEventFrame` discriminates on
    `seq` being a string, so a sequence on this frame would route it into the event fold.
    """
    frame = _first_spend_frame(api)

    assert frame is not None
    assert "seq" not in frame, "a sequence would make the client fold this as an event"
    assert gateway_main._kernel.read_events(RUN, after_seq=0), "the run does have events"
    for envelope in gateway_main._kernel.read_events(RUN, after_seq=0):
        assert "SPEND" not in envelope.kind.name, "spend must not be in the log"


def _first_spend_frame(api) -> dict | None:
    with api.websocket_connect(f"/ws/{RUN}") as socket:
        return _drain_for_spend(socket)


def _drain_for_spend(socket, limit: int = 40) -> dict | None:
    """Read frames until the spend frame arrives, or give up.

    The stream carries the event backlog and position echoes on the same socket, so this
    cannot assume the spend frame is first — and asserting on frame *order* between two
    independent publishers would be a test of the scheduler.
    """
    for _ in range(limit):
        frame = socket.receive_json()
        if frame.get("kind") == "MODEL_SPEND":
            return frame
    return None


# =========================================================================
# Exposure (R34) and the kernel dependency
# =========================================================================


def test_the_gateway_reports_an_in_process_kernel(api) -> None:
    body = api.get("/status").json()
    kernel_dep = body["dependencies"][0]

    assert kernel_dep["name"] == "kernel"
    assert kernel_dep["required"] is False
    assert "in-process kernel" in kernel_dep["detail"]


def test_the_gateway_does_not_import_the_kernel() -> None:
    """R4: the gateway does not reach into the kernel.

    Still true, and still worth asserting now that both live in one process: the boundary was
    never the gRPC hop, it was the import rule, and the collapse deleted the hop rather than
    the rule. The composition lives outside both services because composing them is neither
    service's job.
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


def test_the_launcher_starts_the_runtimes_background_work_on_startup(tmp_path, monkeypatch) -> None:
    """`resume_all` and the lease heartbeat have to run because the *app* started.

    Every other test here calls `resume_all` itself, which is why nothing caught the launcher
    registering it on a hook that never fires: `create_service_app` builds each app with an
    explicit `lifespan=`, and Starlette runs the `on_startup` list only under its default one.
    So a run was resumed by every test and by nothing in production, the lease was never
    renewed past its thirty-second TTL, and neither failure raised anything.

    Driven through `main()` with the server stubbed out, rather than by calling the wrapper
    directly: the assertion worth having is that the launcher's own entrypoint installs it, so
    deleting the one line that does would fail here.
    """
    monkeypatch.setenv("COMPANY_OS_STORE_URL", f"sqlite:///{tmp_path}/lifespan.sqlite3")

    import uvicorn

    import single_process
    from kernel import lease as lease_module

    async def restart() -> None:
        first, _ = single_process.compose()
        first.create_run("run-lifespan", SEED, horizon_tick=simtime.TICKS_PER_SIM_DAY * 5)
        await first.stop()

        # The app is a module-level singleton, so the wrapper it is about to be given has to
        # come back off afterwards or every later test inherits a second runtime's startup.
        original = gateway_main.app.router.lifespan_context
        served: dict = {}
        monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: served.update(app=app))

        single_process.main()

        app = served["app"]
        assert app is gateway_main.app
        second = gateway_main._kernel.runtime
        try:
            async with app.router.lifespan_context(app):
                assert "run-lifespan" in second.runs, "startup did not resume the run"
                assert second.runs["run-lifespan"].task is not None, "its clock is not running"
                assert second._heartbeat is not None, "the lease heartbeat never started"

            # And the teardown is the one that releases the lease, so a restart takes it back
            # immediately rather than waiting out the thirty-second TTL. A further acquisition
            # succeeding in milliseconds is only possible if `stop` ran.
            with second.store.engine.begin() as connection:
                lease_module.acquire(connection)
        finally:
            gateway_main.app.router.lifespan_context = original
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


# =========================================================================
# The bench leg, over the production composition (U10)
# =========================================================================


def test_the_launcher_wires_the_kernel_to_the_agents_surfaces_producer(composed) -> None:
    """The third thing the launcher hands over, and the reason it has to.

    The kernel raises a statement request inside `step()`; the director lives in the agents service;
    and neither service may import the other (R4). So the wiring is a callable installed from
    outside both, exactly as the kernel client and the spend reader are — and if it were forgotten,
    every run would raise briefings nobody ever answered, which reads as a broken bench rather than
    as a missing line.
    """
    runtime, _ = composed

    assert runtime._statement_producer is agents_main.produce_statement


def test_a_briefing_crosses_the_whole_leg_and_lands_in_the_log(composed, monkeypatch) -> None:
    """End to end over `compose()`: the tick raises it, the agents surface answers it, the log holds it.

    Every other test of this leg stubs one side. This one stubs only the *prose* — which is U11's,
    and the one thing U10 does not build — so the store read, the line-scoped retrieval, the guard
    both processes share, the writer and the publisher are all the shipped code. What it proves is
    the thing the plan's Risks section says is most likely to be missed: that the transport exists.
    """
    from test_kernel_service import _walk_the_ceo_to_a_briefing

    from simcore import statement as statements

    runtime, _ = composed
    seen: list[object] = []

    def compose_prose(_request, retrieved):
        # The context is the shipped retrieval's, drawn under the scope the request carried.
        seen.append(retrieved)
        return (
            "The recruiter is the constraint.",
            "And cutting review is how the last mis-hire got through.",
            retrieved.citable()[:1],
            "",
            statements.PRODUCER_SCRIPTED,
        )

    monkeypatch.setattr(agents_main, "compose_statement", compose_prose)

    async def drive() -> None:
        _walk_the_ceo_to_a_briefing(runtime, RUN)
        run = runtime.runs[RUN]
        for _ in range(50):
            if not run.statement_tasks:
                break
            await asyncio.gather(*list(run.statement_tasks), return_exceptions=True)

    asyncio.run(drive())

    assert len(seen) == 1, "the agents surface was not asked exactly once"
    assert seen[0].director == "dir_hr"
    assert seen[0].events, "the retrieval read nothing out of the real store"

    received = [
        envelope
        for envelope in runtime.store.read_events(RUN)
        if envelope.kind is EventKind.INPUT_RECEIVED
    ]
    assert len(received) == 1
    answer = received[0].decoded_payload()["answer"]
    assert answer[statements.KEY_PRODUCER] == "dir_hr"
    assert answer[statements.KEY_CONTEXT]["events"], "M32: the context is not in the log"

    # And then the clock is run *to the landing tick*, because that is where the guard runs. Without
    # this the absence of an `ANSWER_REJECTED` below would mean nothing had been checked yet rather
    # than that the check passed — which is what it meant when this test was first written.
    run = runtime.runs[RUN]
    landing = int(received[0].decoded_payload()["tick"])
    assert landing > run.state.tick, "the fixture already passed the landing tick"
    runtime._advance(run, landing - run.state.tick)

    request_id = received[0].request_id
    assert request_id not in run.state.pending, "the statement never applied"
    # This request's rejections only. The window is two sim-days wide, so the period consult raised
    # at the first day boundary reaches its own one-sim-day deadline inside it and is abandoned —
    # which is the shared deadline doing exactly what R18 says it does, on a different leg.
    assert not [
        envelope
        for envelope in runtime.store.read_events(RUN)
        if envelope.kind is EventKind.ANSWER_REJECTED and envelope.request_id == request_id
    ], "the shared guard refused a statement its own retrieval assembled"


def test_with_no_bench_configured_the_request_is_raised_and_left(composed) -> None:
    """M20's shape for U10 alone: the shipped `compose_statement` declines, and nothing breaks.

    Declining is the whole of what a keyless build does here, and it is deliberately not the same as
    the leg being absent — the request is raised, published, and left for its deadline, so the client
    has a pending block to render and then a labelled fallback to replace it with (U11's surface).
    """
    from test_kernel_service import _walk_the_ceo_to_a_briefing

    runtime, _ = composed

    async def drive() -> None:
        _walk_the_ceo_to_a_briefing(runtime, RUN)
        run = runtime.runs[RUN]
        for _ in range(50):
            if not run.statement_tasks:
                break
            await asyncio.gather(*list(run.statement_tasks), return_exceptions=True)

    asyncio.run(drive())

    run = runtime.runs[RUN]
    assert [request for request in run.state.pending.values() if request.is_statement]
    assert not [
        envelope
        for envelope in runtime.store.read_events(RUN)
        if envelope.kind is EventKind.INPUT_RECEIVED
    ]

    before = run.state.tick
    runtime._advance(run, 40)
    assert run.state.tick == before + 40, "the clock waited for a bench that declined"
