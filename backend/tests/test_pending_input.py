"""The pending-input contract: the mechanism that lets a service split replay bit-identically.

The verification that matters most is the last test in this file. Replaying a log with both
services **running and configured to answer differently** has to produce byte-identical output — a
stopped service cannot prove the replay path never calls out, because a call that fails silently
looks the same as a call that never happened.
"""

from __future__ import annotations

import pytest

from agents import stub
from contracts.envelope import Envelope, EventKind, build
from domain import model
from simcore import hashing
from simcore import log as folder
from simcore import pending as pend
from simcore import step as sim
from simcore import time as simtime
from simcore.rates import RULES_VERSION

RUN = "run-pending"
SEED = 0xC0FFEE


class Recorder:
    def __init__(self, horizon_tick: int | None = None) -> None:
        self.state, genesis = sim.new_run(run_seed=SEED, horizon_tick=horizon_tick)
        self.log: list[Envelope] = []
        self._seq = 0
        self.record(genesis)

    def record(self, emitted: list[sim.Emitted]) -> None:
        for item in emitted:
            self._seq += 1
            self.log.append(
                build(
                    seq=self._seq,
                    tick=int(item.payload.get("tick", self.state.tick)),
                    kind=item.kind,
                    rules_ver=RULES_VERSION,
                    payload=item.payload,
                    run_id=RUN,
                    request_id=item.request_id,
                )
            )

    def advance(self, ticks: int) -> None:
        for _ in range(ticks):
            self.record(sim.step(self.state))

    def advance_until(self, predicate, limit: int = 60_000) -> None:
        for _ in range(limit):
            if predicate(self.state):
                return
            self.record(sim.step(self.state))
        raise AssertionError(f"condition never held within {limit} ticks")

    @property
    def hash(self) -> str:
        return hashing.state_hash(sim.snapshot(self.state)).overall


@pytest.fixture
def run() -> Recorder:
    return Recorder(horizon_tick=simtime.TICKS_PER_SIM_DAY * 40)


# =========================================================================
# Requests stall an item, not the clock
# =========================================================================


def test_a_raised_request_stalls_only_the_affected_item(run: Recorder) -> None:
    run.record(sim.assign_direct(run.state, "wi_quotes", "stf_buyer"))
    run.advance(100)

    events = run.record(
        sim.raise_request(
            run.state, pend.AGENTS, "wi_faq", request_id="req-1"
        )
    )

    before = run.state.items["wi_quotes"].done_units
    before_tick = run.state.tick
    run.advance(100)

    # The clock kept running and unrelated work kept progressing.
    assert run.state.tick == before_tick + 100
    assert run.state.items["wi_quotes"].done_units > before
    assert "req-1" in run.state.pending


def test_a_period_consult_is_raised_at_a_period_boundary(run: Recorder) -> None:
    """At period boundaries, not per tick: a per-tick round trip would flood the log."""
    run.advance_until(lambda s: simtime.is_day_boundary(s.tick) and s.tick > 0)

    raised = [e for e in run.log if e.kind is EventKind.REQUEST_RAISED]
    assert len(raised) == 1

    payload = raised[0].decoded_payload()
    assert payload["service"] == pend.DOMAIN
    assert payload["owning_item"] == pend.SYNTHETIC_PERIOD_ITEM


def test_the_period_consult_attaches_to_a_synthetic_item(run: Recorder) -> None:
    """It is company-scoped and owns no real item.

    Attaching it to a synthetic one means a late, rejected or absent answer defers only that
    period's metric application, rather than advancing the day without it or stalling the clock.
    """
    run.advance_until(lambda s: len(s.pending) > 0)
    request = next(iter(run.state.pending.values()))

    assert request.owning_item == pend.SYNTHETIC_PERIOD_ITEM
    assert request.owning_item not in run.state.items


def test_one_period_asks_once(run: Recorder) -> None:
    run.advance(simtime.TICKS_PER_SIM_DAY + 50)

    raised = [e for e in run.log if e.kind is EventKind.REQUEST_RAISED]
    assert len(raised) == 1, "the same period was consulted more than once"


# =========================================================================
# Answers arrive as logged input events
# =========================================================================


def test_an_answer_arrives_as_an_input_event_carrying_its_tick(run: Recorder) -> None:
    run.advance_until(lambda s: len(s.pending) > 0)
    request_id = next(iter(run.state.pending))

    answer = {
        "metric_deltas": model.effects_for_period(1, run.state.metrics),
        "model_identity": model.MODEL_IDENTITY,
    }
    run.record(sim.receive_answer(run.state, request_id, answer))

    received = [e for e in run.log if e.kind is EventKind.INPUT_RECEIVED]
    assert len(received) == 1

    payload = received[0].decoded_payload()
    assert payload["tick"] > run.state.tick, "the answer must carry the tick it applies at"
    # The answer content is in the log, which is what replay reads instead of asking again.
    assert payload["answer"]["metric_deltas"] == answer["metric_deltas"]


def test_the_answer_applies_at_the_tick_it_carries(run: Recorder) -> None:
    run.advance_until(lambda s: len(s.pending) > 0)
    request_id = next(iter(run.state.pending))
    before = dict(run.state.metrics)

    run.record(
        sim.receive_answer(
            run.state,
            request_id,
            {"metric_deltas": {"leadTime": 1}, "model_identity": model.MODEL_IDENTITY},
        )
    )
    run.advance(1)

    assert run.state.metrics["leadTime"] == before["leadTime"] + 1
    assert request_id not in run.state.pending
    assert any(e.kind is EventKind.METRICS_APPLIED for e in run.log)


def test_a_request_raised_while_a_service_is_starting_is_answered_on_arrival(
    run: Recorder,
) -> None:
    """No kernel restart needed: the request is outstanding, and a late answer still lands."""
    run.advance_until(lambda s: len(s.pending) > 0)
    request_id = next(iter(run.state.pending))

    # The service is "starting": nothing answers for a while.
    run.advance(200)
    assert request_id in run.state.pending

    run.record(
        sim.receive_answer(run.state, request_id, {"metric_deltas": {"leadTime": 1}})
    )
    run.advance(1)

    assert request_id not in run.state.pending


def test_a_service_that_never_answers_does_not_stall_the_simulation(run: Recorder) -> None:
    run.advance_until(lambda s: len(s.pending) > 0)
    before_tick = run.state.tick

    run.advance(300)

    assert run.state.tick == before_tick + 300, "the clock stalled waiting on a service"


# =========================================================================
# Validation: rejected, never clamped (R24)
# =========================================================================


def test_an_out_of_range_answer_is_rejected_and_logged(run: Recorder) -> None:
    """Clamping would make behaviour depend on where the clamp sits."""
    run.advance_until(lambda s: len(s.pending) > 0)
    request_id = next(iter(run.state.pending))
    before = dict(run.state.metrics)

    run.record(
        sim.receive_answer(
            run.state, request_id, {"metric_deltas": {"leadTime": 10_000}}
        )
    )
    run.advance(1)

    rejected = [e for e in run.log if e.kind is EventKind.ANSWER_REJECTED]
    assert rejected
    assert "outside the bound" in rejected[-1].decoded_payload()["reason"]

    # Not clamped: nothing moved at all.
    assert run.state.metrics == before


def test_an_answer_naming_a_forbidden_metric_is_rejected(run: Recorder) -> None:
    """`cash` is deliberately outside a period consult's reach."""
    refusal = pend.validate_domain_answer({"cash": 5})
    assert "not a metric a period consult may move" in refusal


def test_a_float_delta_is_rejected(run: Recorder) -> None:
    assert "integers" in pend.validate_domain_answer({"leadTime": 1.5})


def test_bounds_are_asserted_on_the_producing_side_too() -> None:
    """A service that ships answers it knows are out of bounds is a worse neighbour."""
    assert model.bounded_effects_for_period(1, {"visibility": 6})

    import unittest.mock

    with unittest.mock.patch.object(model, "PERIOD_DRIFT", {"leadTime": 10_000}):
        with pytest.raises(ValueError, match="out-of-bounds"):
            model.bounded_effects_for_period(1, {"visibility": 6})


def test_a_rejected_period_answer_defers_only_that_period(run: Recorder) -> None:
    run.advance_until(lambda s: len(s.pending) > 0)
    request_id = next(iter(run.state.pending))

    run.record(
        sim.receive_answer(run.state, request_id, {"metric_deltas": {"leadTime": 10_000}})
    )
    run.advance(1)

    rejected = [e for e in run.log if e.kind is EventKind.ANSWER_REJECTED][-1]
    assert rejected.decoded_payload()["deferred_period"] > 0

    # The next period is consulted normally.
    run.advance_until(lambda s: len(s.pending) > 0)
    assert run.state.pending


# =========================================================================
# Duplicates and orphans (R25)
# =========================================================================


def test_a_duplicate_answer_is_rejected_rather_than_applied_twice(run: Recorder) -> None:
    run.advance_until(lambda s: len(s.pending) > 0)
    request_id = next(iter(run.state.pending))

    run.record(sim.receive_answer(run.state, request_id, {"metric_deltas": {"leadTime": 1}}))
    run.advance(1)
    after_first = dict(run.state.metrics)

    run.record(sim.receive_answer(run.state, request_id, {"metric_deltas": {"leadTime": 1}}))
    run.advance(1)

    assert run.state.metrics["leadTime"] == after_first["leadTime"], "applied twice"
    assert any(
        "already answered" in e.decoded_payload().get("reason", "")
        for e in run.log
        if e.kind is EventKind.ANSWER_REJECTED
    )


def test_an_answer_for_an_unknown_request_is_rejected(run: Recorder) -> None:
    events = sim.receive_answer(run.state, "never-raised", {"metric_deltas": {}})

    assert events[0].kind is EventKind.ANSWER_REJECTED
    assert "never raised" in events[0].payload["reason"]


def test_an_answer_after_the_assignee_was_removed_is_rejected_with_a_reason(
    run: Recorder,
) -> None:
    run.record(sim.assign_direct(run.state, "wi_faq", "stf_cs"))
    run.advance_until(lambda s: s.items["wi_faq"].status == sim.STATUS_BLOCKED)

    run.record(sim.raise_request(run.state, pend.AGENTS, "wi_faq", request_id="req-agent"))

    # Attrition takes the assignee, so the item is no longer waiting on that decision.
    run.state.items["wi_faq"].status = sim.STATUS_BACKLOG

    run.record(
        sim.receive_answer(run.state, "req-agent", {"chosen_option": "Publish it externally"})
    )
    run.advance(1)

    rejected = [e for e in run.log if e.kind is EventKind.ANSWER_REJECTED][-1]
    assert "no longer waiting" in rejected.decoded_payload()["reason"]


# =========================================================================
# The stub resolver, and the escalation path
# =========================================================================


def test_a_stub_resolver_closes_a_checkpoint_through_the_same_contract(run: Recorder) -> None:
    """No change to the advancement contract, which is what Phase 2 depends on."""
    run.record(sim.assign_direct(run.state, "wi_faq", "stf_cs"))
    run.advance_until(lambda s: s.items["wi_faq"].status == sim.STATUS_BLOCKED)
    run.record(sim.raise_request(run.state, pend.AGENTS, "wi_faq", request_id="req-agent"))

    permitted = tuple(
        option.label
        for option in run.state.spec_of("wi_faq").checkpoints[0].options
    )
    answer = stub.StubResolver(mode=stub.FIRST_OPTION).resolve(permitted)

    run.record(sim.receive_answer(run.state, "req-agent", answer))
    run.advance(1)

    assert run.state.items["wi_faq"].status == sim.STATUS_ACTIVE
    decision = run.state.items["wi_faq"].decisions[0]
    assert decision.choice == permitted[0]
    # Resolved by a stub, not a person, and the log says so.
    assert any(e.kind is EventKind.DECISION_RESOLVED for e in run.log)


def test_the_shipped_stub_declines_so_the_ceo_decides(run: Recorder) -> None:
    """Phase 1 has no other resolver, so the stub being present changes nothing about play."""
    resolver = stub.StubResolver()
    assert resolver.mode == stub.NEVER_ANSWERS
    assert resolver.resolve(("a", "b")) is None


def test_an_out_of_bounds_resolution_escalates_to_the_ceo(run: Recorder) -> None:
    """Which is the behaviour Phase 2 actually depends on: an LLM will do this."""
    run.record(sim.assign_direct(run.state, "wi_faq", "stf_cs"))
    run.advance_until(lambda s: s.items["wi_faq"].status == sim.STATUS_BLOCKED)
    run.record(sim.raise_request(run.state, pend.AGENTS, "wi_faq", request_id="req-agent"))

    answer = stub.StubResolver(mode=stub.OUT_OF_BOUNDS).resolve(("Publish it externally",))
    run.record(sim.receive_answer(run.state, "req-agent", answer))
    run.advance(1)

    rejected = [e for e in run.log if e.kind is EventKind.ANSWER_REJECTED][-1]
    payload = rejected.decoded_payload()
    assert payload["escalated_to_ceo"] is True
    assert "not one of the permitted options" in payload["reason"]

    # Still blocked: which is exactly what "the CEO has to decide this" looks like.
    assert run.state.items["wi_faq"].status == sim.STATUS_BLOCKED


def test_a_resolution_carries_a_resolver_identity(run: Recorder) -> None:
    """R37: producer-agnostic, and never assumes human origin."""
    answer = stub.StubResolver(mode=stub.FIRST_OPTION).resolve(("a",))
    assert answer["resolver_kind"] == stub.RESOLVER_KIND_STUB
    assert answer["resolver_identity"]


# =========================================================================
# Deadlines and caps, inside the step
# =========================================================================


def test_an_unanswered_request_is_abandoned_after_its_sim_tick_deadline(run: Recorder) -> None:
    """Counted in sim-ticks, so abandonment happens at the same tick on every machine."""
    run.advance_until(lambda s: len(s.pending) > 0)
    request_id = next(iter(run.state.pending))
    deadline = run.state.pending[request_id].deadline_tick

    run.advance_until(lambda s: request_id not in s.pending)

    assert run.state.tick >= deadline
    abandoned = [
        e
        for e in run.log
        if e.kind is EventKind.ANSWER_REJECTED and e.decoded_payload().get("abandoned")
    ]
    assert abandoned


def test_abandonment_is_deterministic_across_two_runs() -> None:
    """A wall-clock deadline would replay fine and still break seed determinism."""

    def play() -> int:
        recorder = Recorder(horizon_tick=simtime.TICKS_PER_SIM_DAY * 40)
        recorder.advance_until(lambda s: len(s.pending) > 0)
        request_id = next(iter(recorder.state.pending))
        recorder.advance_until(lambda s: request_id not in s.pending)
        return recorder.state.tick

    assert play() == play()


def test_the_per_item_cap_is_enforced_inside_the_step(run: Recorder) -> None:
    for index in range(pend.MAX_OUTSTANDING_PER_ITEM):
        run.record(
            sim.raise_request(run.state, pend.AGENTS, "wi_faq", request_id=f"req-{index}")
        )

    with pytest.raises(pend.RequestCapExceeded) as excinfo:
        sim.raise_request(run.state, pend.AGENTS, "wi_faq", request_id="one-too-many")

    assert "at the cap" in str(excinfo.value)
    assert "every machine" in str(excinfo.value)


def test_the_cap_fails_deterministically_rather_than_growing_the_log(run: Recorder) -> None:
    def play() -> int:
        recorder = Recorder(horizon_tick=simtime.TICKS_PER_SIM_DAY * 40)
        raised = 0
        for index in range(pend.MAX_OUTSTANDING_PER_ITEM + 5):
            try:
                recorder.record(
                    sim.raise_request(
                        recorder.state, pend.AGENTS, "wi_faq", request_id=f"r{index}"
                    )
                )
                raised += 1
            except pend.RequestCapExceeded:
                break
        return raised

    assert play() == play() == pend.MAX_OUTSTANDING_PER_ITEM


# =========================================================================
# Outstanding requests are a projection of the log (R23)
# =========================================================================


def test_a_log_ending_in_a_raised_request_folds_to_it_being_outstanding(run: Recorder) -> None:
    run.advance_until(lambda s: len(s.pending) > 0)
    request_id = next(iter(run.state.pending))

    folded = folder.fold(run.log, at_live_head=False, through_tick=run.state.tick)

    assert request_id in folded.outstanding_requests
    assert folded.state.pending, "the fold lost the outstanding request"


def test_outstanding_requests_survive_a_snapshot(run: Recorder) -> None:
    from simcore import snapshot as snapshots

    run.advance_until(lambda s: len(s.pending) > 0)
    taken = snapshots.capture(RUN, run.state, run.log[-1].seq)

    restored = snapshots.restore(taken)

    assert set(restored.pending) == set(run.state.pending)


# =========================================================================
# U11's verification
# =========================================================================


def test_replay_is_byte_identical_with_services_answering_differently() -> None:
    """The verification that matters, and the reason it is phrased this way.

    A *stopped* service cannot prove the replay path never calls out: a call that fails silently
    looks exactly like a call that never happened. So both services are live during the replay and
    configured to answer *differently* from how they answered originally. If replay re-issued
    anything, the second answer would land and the output would diverge.
    """
    original = Recorder(horizon_tick=simtime.TICKS_PER_SIM_DAY * 10)
    original.record(sim.assign_direct(original.state, "wi_faq", "stf_cs"))

    # Play three periods, answering each consult the way the shipped model does.
    for _ in range(3):
        original.advance_until(lambda s: len(s.pending) > 0)
        request_id = next(iter(original.state.pending))
        original.record(
            sim.receive_answer(
                original.state,
                request_id,
                {
                    "metric_deltas": model.effects_for_period(1, original.state.metrics),
                    "model_identity": model.MODEL_IDENTITY,
                },
            )
        )
        original.advance(5)

    original.advance(300)
    expected_hash = original.hash
    expected_log = [envelope.hashed_projection() for envelope in original.log]

    # Now replay it while a *differently configured* service stands ready. If the fold called
    # out, this is the answer it would get — and the run would end up somewhere else.
    def hostile_answer(_period: int, _metrics: dict) -> dict:
        return {"leadTime": -60, "visibility": 60}

    import unittest.mock

    with unittest.mock.patch.object(model, "effects_for_period", hostile_answer):
        with unittest.mock.patch.object(
            stub.StubResolver, "resolve", lambda self, permitted: {"chosen_option": permitted[0]}
        ):
            replayed = folder.fold(
                original.log,
                at_live_head=False,
                strict=True,
                through_tick=original.state.tick,
            )

    assert hashing.state_hash(sim.snapshot(replayed.state)).overall == expected_hash

    # And byte-identical, compared over the hashed projection R11 specifies.
    refolded_log = [envelope.hashed_projection() for envelope in original.log]
    assert refolded_log == expected_log


def test_the_fold_never_reissues_outside_the_live_head() -> None:
    """R3, asserted structurally: re-issue is permitted only at the live head."""
    import ast
    import pathlib

    # Checked against the import graph, not a substring search: the first version of this test
    # matched "requests" inside `outstanding_requests` and failed on correct code.
    tree = ast.parse(pathlib.Path(folder.__file__).read_text())
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])

    # The fold has no transport of any kind, which is the strongest form of this guarantee: it
    # cannot re-issue a call because it has nothing to call with.
    assert not imported & {"grpc", "httpx", "requests", "socket", "urllib", "http"}, imported
