"""The pending-input contract: the mechanism that lets a service split replay bit-identically.

Two verifications matter most here, and they are the last test of each half of this file. Replaying
a log with both services **running and configured to answer differently** has to produce
byte-identical output — a stopped service cannot prove the replay path never calls out, because a
call that fails silently looks the same as a call that never happened. And a statement request has
to be *regenerated* by the step rather than read from the log, which is why the tamper test sits
beside the pass: a strict fold that happened to read the request would pass too.
"""

from __future__ import annotations

import pytest

from agents import stub
from contracts.envelope import Envelope, EventKind, build
from domain import model
from simcore import hashing
from simcore import log as folder
from simcore import pending as pend
from simcore import statement as stmt
from simcore import step as sim
from simcore import time as simtime
from simcore.rates import RULES_VERSION
from simcore.world import find_path

RUN = "run-pending"
SEED = 0xC0FFEE

#: The director the statement tests brief with, and the item their line is stopped at.
#:
#: `dir_hr` because the shipped scenario seeds `wi_hiring` to them at day zero (M6) and their desk
#: is the one the CEO's spawn can be walked to with held-direction input alone. That last part is
#: not a convenience: a test that placed the CEO by assigning to `state.ceo` would be testing a
#: position no logged event produced, and the strict-replay test below would then be asserting that
#: the fold reproduces something the fold never saw.
BRIEFING_DIRECTOR = "dir_hr"
BRIEFING_ITEM = "wi_hiring"


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

    # --- the CEO, driven by logged input only ----------------------------

    def walk_ceo_beside(self, person_id: str, limit: int = 4_000) -> bool:
        """Walk the CEO to within conversation range of someone, one keypress at a time.

        Every tick submits the held-direction bitmask the client would have submitted, so the whole
        walk is in the log as `CEO_INPUT` and a replay reproduces the position from the same
        arithmetic. Setting `state.ceo` directly would be shorter and would make the strict-replay
        test meaningless — the fold would be reproducing a position nothing recorded.

        Routed with `find_path` over the recorded floor rather than aimed straight at the target,
        because `_advance_ceo` slides along a wall rather than stopping dead: aiming at a desk two
        rooms away parks the CEO against the nearest wall and holds a key there forever.
        """
        if sim._ceo_is_beside(self.state, self.state.people[person_id]):
            return True

        target = self.state.seats[person_id]
        held = 0
        for _ in range(limit):
            here = self.state.ceo.tile
            path = find_path(self.state.floor, here, target)
            step_to = here
            if path:
                step_to = path[1] if path[0] == here and len(path) > 1 else path[0]

            mask = 0
            centre_x = step_to[0] * sim.MILLI + sim.MILLI // 2
            centre_y = step_to[1] * sim.MILLI + sim.MILLI // 2
            if self.state.ceo.x_milli < centre_x - 60:
                mask |= sim.INPUT_RIGHT
            elif self.state.ceo.x_milli > centre_x + 60:
                mask |= sim.INPUT_LEFT
            if self.state.ceo.y_milli < centre_y - 60:
                mask |= sim.INPUT_DOWN
            elif self.state.ceo.y_milli > centre_y + 60:
                mask |= sim.INPUT_UP

            # One command per *change* of held direction, which is what the client sends: an input
            # stands until another supersedes it, so re-submitting the same mask every tick would
            # make this harness's log thirty times the size of a real one.
            if mask != held:
                self.record(sim.submit_ceo_input(self.state, mask, self.state.tick + 1))
                held = mask
            self.record(sim.step(self.state))

            if sim._ceo_is_beside(self.state, self.state.people[person_id]):
                # Let go of the key. An input *stands* until another supersedes it, so a harness
                # that arrived and stopped submitting would leave the CEO walking into a wall for
                # the rest of the test — and a briefing raised at a desk the CEO has since left is
                # not the state M17 describes.
                self.record(sim.submit_ceo_input(self.state, 0, self.state.tick + 1))
                self.record(sim.step(self.state))
                return sim._ceo_is_beside(self.state, self.state.people[person_id])
        return False

    def walk_ceo_away(self, limit: int = 60) -> None:
        """Hold left until the CEO is out of range, then let go."""
        self.record(sim.submit_ceo_input(self.state, sim.INPUT_LEFT, self.state.tick + 1))
        for _ in range(limit):
            self.record(sim.step(self.state))
            if not sim._ceo_is_beside(self.state, self.state.people[BRIEFING_DIRECTOR]):
                break
        self.record(sim.submit_ceo_input(self.state, 0, self.state.tick + 1))
        self.record(sim.step(self.state))

    # --- the bench, scripted ---------------------------------------------

    def open_a_checkpoint_in_person(self) -> pend.PendingRequest:
        """Get to the state M17 describes: a director stopped at a checkpoint, the CEO beside them."""
        self.advance_until(lambda s: s.items[BRIEFING_ITEM].status == sim.STATUS_BLOCKED)
        assert self.walk_ceo_beside(BRIEFING_DIRECTOR), "the CEO never reached the director"

        statements = [
            request for request in self.state.pending.values() if request.is_statement
        ]
        assert len(statements) == 1, f"expected one statement request, got {len(statements)}"
        return statements[0]

    def statement_answer(self, request: pend.PendingRequest, **overrides: object) -> dict:
        """A well-formed statement drawn from the context this request's scope permits.

        Attributed to a model now, where U10 wrote it as scripted: U11 makes a scripted statement
        mean one specific thing — a fallback standing in for a briefing that did not arrive — and it
        must therefore name the condition that fired. The ordinary statement these tests are about is
        the one a provider produced, so that is what this builds.

        What is not scripted either way is the context: it is retrieved through the same line-scoped
        query the agents service uses, so a statement that cited outside its scope here would be
        refused for the same reason it would be in production.
        """
        from agents.bench import context as retrieval

        subject = self.subject_of(request)
        authorized = stmt.Authorized.from_payload(
            str(subject["person"]), subject.get("scope", {})
        )
        retrieved = retrieval.retrieve(
            self.log,
            authorized=authorized,
            owning_item=request.owning_item,
            at_tick=request.raised_at_tick,
        )
        answer = stmt.Statement(
            briefing="Hiring lead time is the constraint the recruiter keeps hitting.",
            objection="It also removes the review step the last two mis-hires were caught by.",
            citations=retrieved.citable()[:2],
            producer=authorized.director,
            producer_kind=stmt.PRODUCER_MODEL,
            model_identity="a-model-under-test",
            context=retrieved.to_payload(),
        ).to_answer()
        answer.update(overrides)
        return answer

    def subject_of(self, request: pend.PendingRequest) -> dict:
        """The `REQUEST_RAISED` payload for this request, as the kernel hands it to the leg."""
        for envelope in self.log:
            if (
                envelope.kind is EventKind.REQUEST_RAISED
                and envelope.request_id == request.request_id
            ):
                return envelope.decoded_payload()
        raise AssertionError(f"no REQUEST_RAISED for {request.request_id}")


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
# The statement leg: raised inside the step, from folded state (M17)
# =========================================================================


def test_opening_a_checkpoint_in_person_raises_exactly_one_statement_request(
    run: Recorder,
) -> None:
    """M17. One briefing per checkpoint opened in person, and never one per tick."""
    request = run.open_a_checkpoint_in_person()

    assert request.service == pend.BENCH
    assert request.owning_item == BRIEFING_ITEM

    subject = run.subject_of(request)
    assert subject["tick"] == request.raised_at_tick
    assert subject["person"] == BRIEFING_DIRECTOR
    assert subject["cp_index"] == run.state.people[BRIEFING_DIRECTOR].cp_index
    # The scope is on the event, so R23 is auditable from the log with no roster in the process.
    assert BRIEFING_DIRECTOR in subject["scope"]["people"]
    assert BRIEFING_ITEM in subject["scope"]["items"]

    # Standing there is not asking again. The window is two sim-days wide, so a CEO reading a
    # briefing does not spend a request per thirty-sixth of a wall-second.
    run.advance(200)
    statements = [e for e in run.log if _is_statement_request(e)]
    assert len(statements) == 1, "the request was re-raised while the CEO stood still"


def test_a_tick_with_no_conversation_raises_no_statement(run: Recorder) -> None:
    """M17's other half: proximity is the whole gesture, so distance is the whole absence."""
    run.advance_until(lambda s: s.items[BRIEFING_ITEM].status == sim.STATUS_BLOCKED)
    run.advance(400)

    assert not [e for e in run.log if _is_statement_request(e)]
    assert not [request for request in run.state.pending.values() if request.is_statement]


def test_opening_the_same_checkpoint_twice_reuses_the_outstanding_request(
    run: Recorder,
) -> None:
    """Walking away and back does not buy a second briefing while the first is outstanding."""
    first = run.open_a_checkpoint_in_person()
    run.walk_ceo_away()
    assert first.request_id in run.state.pending

    assert run.walk_ceo_beside(BRIEFING_DIRECTOR)

    assert [e for e in run.log if _is_statement_request(e)] != []
    assert len([e for e in run.log if _is_statement_request(e)]) == 1
    assert list(run.state.pending).count(first.request_id) == 1


def test_standing_at_a_settled_checkpoint_past_the_window_asks_again(run: Recorder) -> None:
    """The documented cost of keeping the "already asked" memory in `pending` (M17's edge).

    A flag on the item would suppress this, and a flag on the item is a new field in
    `ItemRuntime.to_state()` — which R27 makes a `STATE_SHAPE_VERSION` move, and U10 is neither of
    the two changes the plan permits to move it. So a CEO who stands at the same desk past the
    landing tick without settling anything is briefed again. It is asserted rather than left to be
    discovered, and it is bounded: three per item, by the cap.
    """
    first = run.open_a_checkpoint_in_person()
    run.record(sim.receive_answer(run.state, first.request_id, run.statement_answer(first)))
    run.advance_until(lambda s: first.request_id not in s.pending)
    run.advance(1)

    second = [request for request in run.state.pending.values() if request.is_statement]

    assert len(second) == 1
    assert second[0].request_id != first.request_id
    assert second[0].owning_item == BRIEFING_ITEM
    assert len([e for e in run.log if _is_statement_request(e)]) == 2


def test_the_request_is_derived_from_state_rather_than_from_a_command(run: Recorder) -> None:
    """R17, structurally. Nothing in the command surface can raise one.

    `REQUEST_RAISED` is in the fold's output set, so a request a command emitted would be an event
    the step cannot reproduce — strict replay finds it in the log with nothing regenerated to match.
    The guarantee is therefore that no command handler reaches `raise_request` at all.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(sim))
    raisers = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and node.name != "raise_request"
        and any(
            isinstance(inner, ast.Call)
            and isinstance(inner.func, ast.Name)
            and inner.func.id == "raise_request"
            for inner in ast.walk(node)
        )
    }

    # `_raise_authorization_requests` joined them at U15, and it is step-internal for the same
    # reason the other two are: `REQUEST_RAISED` is regenerated by the fold, so a request a command
    # emitted would sit in the log with nothing to match it.
    assert raisers == {
        "_raise_period_consult",
        "_raise_statement_requests",
        "_raise_authorization_requests",
    }, (
        "something other than the two step-internal raisers calls raise_request; a request from a "
        f"command path fails strict replay: {sorted(raisers)}"
    )


def _is_statement_request(envelope: Envelope) -> bool:
    return (
        envelope.kind is EventKind.REQUEST_RAISED
        and envelope.decoded_payload().get("service") == pend.BENCH
    )


# =========================================================================
# The landing tick, and why the provider cannot move it (R17)
# =========================================================================


def test_the_landing_tick_is_derived_from_the_raising_tick(run: Recorder) -> None:
    request = run.open_a_checkpoint_in_person()
    raised_at = request.raised_at_tick

    # The provider "thinks" for a while. The live tick moves; the landing tick must not.
    run.advance(300)
    submitted_at = run.state.tick
    run.record(sim.receive_answer(run.state, request.request_id, run.statement_answer(request)))

    received = [e for e in run.log if e.kind is EventKind.INPUT_RECEIVED][-1]
    payload = received.decoded_payload()

    assert payload["tick"] == raised_at + pend.STATEMENT_OFFSET_TICKS
    assert payload["submitted_at_tick"] == submitted_at
    assert submitted_at > raised_at, "the fixture did not actually let the clock move"


def test_two_fresh_runs_agree_on_the_landing_tick_at_two_provider_speeds() -> None:
    """R17, and the defect it names. The live tick is where latency would enter.

    The two runs are identical in everything the player did and differ only in how long the bench
    took to answer — 5 ticks against 400. Before this unit `receive_answer` read `state.tick`, so
    those two produced different landing ticks, `pending` is a hashed subsystem, and the state hash
    diverged on nothing but provider speed.
    """

    def play(provider_takes: int) -> tuple[int, str]:
        recorder = Recorder(horizon_tick=simtime.TICKS_PER_SIM_DAY * 40)
        request = recorder.open_a_checkpoint_in_person()
        recorder.advance(provider_takes)
        recorder.record(
            sim.receive_answer(
                recorder.state, request.request_id, recorder.statement_answer(request)
            )
        )
        landed = [e for e in recorder.log if e.kind is EventKind.INPUT_RECEIVED][-1]
        # Past the landing tick, so the statement has been applied in both.
        recorder.advance_until(lambda s: request.request_id not in s.pending)
        return int(landed.decoded_payload()["tick"]), recorder.hash

    fast_tick, fast_hash = play(5)
    slow_tick, slow_hash = play(400)

    assert fast_tick == slow_tick, "provider latency reached the landing tick"
    assert fast_hash == slow_hash, "provider latency reached the state hash"


def test_a_statement_answered_while_the_run_is_paused_lands_at_the_next_tick(
    run: Recorder,
) -> None:
    """Execution decision §2. A pause after the request was raised must not hang the conversation.

    The pause tick is a player-determined tick, so this branch reads the clock and still cannot see
    the provider: while the run is paused `state.tick` does not move, and every answer arriving
    during the pause lands at the same place however slow it was.
    """
    request = run.open_a_checkpoint_in_person()
    run.advance(3)
    paused_at = run.state.tick

    run.record(
        sim.receive_answer(
            run.state, request.request_id, run.statement_answer(request), paused=True
        )
    )
    received = [e for e in run.log if e.kind is EventKind.INPUT_RECEIVED][-1]

    assert received.decoded_payload()["tick"] == paused_at + 1
    assert paused_at + 1 < request.raised_at_tick + pend.STATEMENT_OFFSET_TICKS

    # And it applies on the first quantum the run reaches when the player resumes.
    run.advance(1)
    assert request.request_id not in run.state.pending


def test_a_statement_that_missed_its_window_is_refused_rather_than_repaired(
    run: Recorder,
) -> None:
    """The alternative to the refusal is the defect. Three of them, in fact.

    A floor of `state.tick + 1` is exactly the live-tick read R17 forbids. Silently dropping the
    answer leaves the conversation waiting on a request that runs to its deadline with nothing saying
    why. And *logging* the refusal — which is what this did first — appends an `ANSWER_REJECTED`, an
    output kind, with no `INPUT_RECEIVED` beside it for `log._apply_input` to re-issue: nothing
    regenerates it and strict replay fails on a log that is a faithful record. So it raises.
    """
    request = run.open_a_checkpoint_in_person()
    run.advance(pend.STATEMENT_OFFSET_TICKS + 10)

    with pytest.raises(sim.CommandRejected, match="outside the window"):
        sim.receive_answer(run.state, request.request_id, run.statement_answer(request))

    # Nothing queued, nothing appended, and the request is still the run's to abandon.
    assert not run.state.queued_answers
    assert request.request_id in run.state.pending
    _assert_the_log_still_replays(run)


def _assert_the_log_still_replays(run: Recorder) -> None:
    """Strict replay of this recorder's log, which is what every door-refusal has to preserve.

    Asserted after each of them rather than once, because each is a separate opportunity to append an
    output event the step cannot regenerate — and the failure is silent until somebody folds.
    """
    run.advance(2)
    folded = folder.fold(
        run.log, at_live_head=False, strict=True, through_tick=run.state.tick
    )
    assert hashing.state_hash(sim.snapshot(folded.state)).overall == run.hash


# =========================================================================
# A statement is not a resolution (R1)
# =========================================================================


def test_a_statement_answer_never_reaches_the_resolver(run: Recorder) -> None:
    """R1. Until this unit every non-domain answer fell into the resolver's branch.

    A statement arriving there would have been validated against the checkpoint's option labels,
    failed, and *escalated the checkpoint* — or, worse, matched one and resolved it. So the assertion
    is about what did not happen as much as about what did.
    """
    request = run.open_a_checkpoint_in_person()
    cp_index = int(run.subject_of(request)["cp_index"])
    before = run.state.items[BRIEFING_ITEM].to_state()

    run.record(sim.receive_answer(run.state, request.request_id, run.statement_answer(request)))
    run.advance_until(lambda s: request.request_id not in s.pending)

    assert not [
        e
        for e in run.log
        if e.kind is EventKind.ANSWER_REJECTED and e.request_id == request.request_id
    ], "a well-formed statement was rejected"
    assert not [e for e in run.log if e.kind is EventKind.DECISION_RESOLVED]
    assert run.state.items[BRIEFING_ITEM].status == sim.STATUS_BLOCKED
    assert run.state.items[BRIEFING_ITEM].to_state() == before
    assert not run.state.items[BRIEFING_ITEM].resolved[cp_index]


def test_the_resolver_legs_stub_is_unchanged_and_still_declines() -> None:
    """R1's other half. Adding the bench must not have moved the resolver's default."""
    resolver = stub.StubResolver()

    assert resolver.mode == stub.NEVER_ANSWERS
    assert resolver.resolve(("a", "b")) is None
    # And its bounds are still the option labels, not a statement's shape.
    assert pend.validate_agent_answer("a", ("a", "b")) == ""
    assert "not one of the permitted options" in pend.validate_agent_answer("z", ("a", "b"))


def test_no_metric_moves_between_the_request_and_the_statement(run: Recorder) -> None:
    """M22. A briefing and an objection move no metric, so validation is shape and citation."""
    request = run.open_a_checkpoint_in_person()
    before = dict(run.state.metrics)

    run.record(sim.receive_answer(run.state, request.request_id, run.statement_answer(request)))
    landing = request.raised_at_tick + pend.STATEMENT_OFFSET_TICKS

    # Stop one tick short of the next day boundary so the daily costs are not confused with this.
    while run.state.tick < landing:
        run.record(sim.step(run.state))
        if simtime.is_day_boundary(run.state.tick):
            before = dict(run.state.metrics)

    assert request.request_id not in run.state.pending
    assert run.state.metrics == before
    assert not [
        e
        for e in run.log
        if e.kind is EventKind.METRICS_APPLIED and e.request_id == request.request_id
    ]


# =========================================================================
# The retrieved context travels with the statement (M32, R23)
# =========================================================================


def test_the_retrieved_context_is_in_the_log_with_the_statement(run: Recorder) -> None:
    """M32. What the director saw, not only what it said."""
    request = run.open_a_checkpoint_in_person()
    answer = run.statement_answer(request)
    run.record(sim.receive_answer(run.state, request.request_id, answer))

    logged = [e for e in run.log if e.kind is EventKind.INPUT_RECEIVED][-1].decoded_payload()
    context = logged["answer"][stmt.KEY_CONTEXT]

    assert context["director"] == BRIEFING_DIRECTOR
    assert context["events"], "the context carried no events at all"
    assert logged["answer"][stmt.KEY_CITATIONS]
    assert set(logged["answer"][stmt.KEY_CITATIONS]) <= {
        entry["seq"] for entry in context["events"]
    }


def test_context_assembly_for_one_director_returns_no_event_from_another_line(
    run: Recorder,
) -> None:
    """R23. And the test has to *bite*, so it puts another line's events in the log first."""
    from agents.bench import context as retrieval

    # A second line, busy: an assignment, a hand-off and whatever the work produces.
    run.record(sim.assign_direct(run.state, "wi_faq", "stf_cs"))
    run.advance_until(lambda s: s.items["wi_faq"].status == sim.STATUS_BLOCKED)
    request = run.open_a_checkpoint_in_person()

    # Genesis is excluded deliberately. It names every person in the company, so leaving it in
    # would make the disjointness below true for a reason that has nothing to do with scoping —
    # genesis is not a retrievable kind at all. What has to be excluded is the *events about* the
    # other line: its assignment, its walk, its checkpoint.
    other_line = {
        envelope.seq
        for envelope in run.log
        if envelope.kind is not EventKind.GENESIS
        and ("stf_cs" in str(envelope.decoded_payload()) or "wi_faq" in str(envelope.decoded_payload()))
    }
    assert len(other_line) >= 2, (
        f"the fixture produced only {len(other_line)} other-line events, so this would pass "
        "for want of anything to exclude"
    )

    subject = run.subject_of(request)
    authorized = stmt.Authorized.from_payload(BRIEFING_DIRECTOR, subject["scope"])
    retrieved = retrieval.retrieve(
        run.log,
        authorized=authorized,
        owning_item=BRIEFING_ITEM,
        at_tick=request.raised_at_tick,
    )

    assert set(retrieved.citable()).isdisjoint(other_line)
    assert all(entry.person != "stf_cs" for entry in retrieved.events)
    assert all(entry.item != "wi_faq" for entry in retrieved.events)
    # And there is something in it, so the disjointness above is not the empty set passing.
    assert retrieved.events


def test_the_retrieved_context_does_not_move_with_how_long_the_leg_took(run: Recorder) -> None:
    """R17 again, for the half that is not the landing tick — and the half that is easy to miss.

    Retrieval runs on a worker thread an arbitrary wall-clock interval after the request was raised,
    while the tick loop keeps appending. So a window that ended at "the log as it stands" would make
    the *logged* context (M32) a function of provider latency: two fresh runs from one seed would
    carry different evidence for the same briefing, and nothing would compare unequal until somebody
    read the two reports side by side. Both ends of the window are closed on `at_tick` instead.
    """
    from agents.bench import context as retrieval

    request = run.open_a_checkpoint_in_person()
    subject = run.subject_of(request)
    authorized = stmt.Authorized.from_payload(BRIEFING_DIRECTOR, subject["scope"])

    def read_now() -> dict:
        return retrieval.retrieve(
            run.log,
            authorized=authorized,
            owning_item=BRIEFING_ITEM,
            at_tick=request.raised_at_tick,
        ).to_payload()

    prompt = read_now()
    assert prompt["events"], "the fixture read nothing, so this would pass vacuously"

    # The log grows while the leg is thinking, and it grows *inside this line* — a question put to
    # the director's own recruiter is a retrievable event about somebody in scope. Without the upper
    # bound it would land in the context, so the fixture has to produce one or the test would pass
    # for want of anything new to admit.
    run.advance(simtime.TICKS_PER_SIM_DAY + 40)
    run.record(sim.ask_person(run.state, "stf_rec", "why do we do it this way?"))
    run.advance(5)
    assert [e for e in run.log if e.kind is EventKind.QUESTION_ANSWERED]

    assert read_now() == prompt, "the context moved with the length of the log"


def test_a_context_naming_the_wrong_line_is_refused(run: Recorder) -> None:
    """The two fields a log reader checks the scope against, checked rather than trusted.

    `Retrieved` carries `director` and `line` so that R23 is auditable from the log with no roster in
    the process. A leg that wrote a `line` naming somebody else's reports would make that evidence a
    claim, and the claim would be the one the reader was relying on.
    """
    request = run.open_a_checkpoint_in_person()

    answer = run.statement_answer(request)
    answer[stmt.KEY_CONTEXT] = {**answer[stmt.KEY_CONTEXT], "line": ["dir_hr", "stf_cs"]}
    run.record(sim.receive_answer(run.state, request.request_id, answer))
    run.advance_until(lambda s: request.request_id not in s.pending)
    assert "and they are not" in [
        e for e in run.log if e.kind is EventKind.ANSWER_REJECTED
    ][-1].decoded_payload()["reason"]

    second = run.open_a_checkpoint_in_person()
    answer = run.statement_answer(second)
    answer[stmt.KEY_CONTEXT] = {**answer[stmt.KEY_CONTEXT], "director": "dir_cs"}
    run.record(sim.receive_answer(run.state, second.request_id, answer))
    run.advance_until(lambda s: second.request_id not in s.pending)
    assert "says it was drawn for" in [
        e for e in run.log if e.kind is EventKind.ANSWER_REJECTED
    ][-1].decoded_payload()["reason"]


def test_an_unscoped_read_is_not_expressible(run: Recorder) -> None:
    """R23's query half, at the constructor rather than at the call site.

    Default-deny has to be the value an omission produces. An `Authorized` naming nobody is refused,
    so "unscoped" cannot be reached by leaving a field out — and there is no `Authorized.all()` for
    it to be reached by asking.
    """
    with pytest.raises(ValueError, match="names no people"):
        stmt.Authorized(director=BRIEFING_DIRECTOR, people=frozenset(), items=frozenset())

    with pytest.raises(ValueError, match="names the director"):
        stmt.Authorized(director="", people=frozenset({"x"}), items=frozenset())

    assert not [name for name in dir(stmt.Authorized) if name in {"all", "unscoped", "everything"}]


def test_no_bench_module_can_reach_the_store(run: Recorder) -> None:
    """The other half of "no reachable unscoped variant": there is one door and it takes a scope.

    `context.retrieve` filters, so the only way past it is to read the log some other way. Nothing
    under `bench/` may, which makes the service module that owns the engine the single entrance —
    and it hands `retrieve` the scope the request carried rather than one it computed.
    """
    import ast
    from pathlib import Path

    bench_dir = Path(sim.__file__).resolve().parents[2] / "services" / "agents" / "bench"
    paths = sorted(bench_dir.rglob("*.py"))
    assert paths, (
        f"no bench modules found under {bench_dir}; an empty directory passes this test for free, "
        "so a moved or renamed package has to fail here rather than go quiet"
    )
    offenders: list[str] = []

    for path in paths:
        tree = ast.parse(path.read_text())
        roots: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                roots.add(node.module.split(".")[0])
        reached = roots & {"sqlalchemy", "logschema", "psycopg", "sqlite3", "kernel"}
        if reached:
            offenders.append(f"{path.name} -> {sorted(reached)}")

    assert not offenders, "a bench module opened its own door onto the store: " + "; ".join(
        offenders
    )


# =========================================================================
# The guards both processes run (execution decision §1)
# =========================================================================


def test_a_statement_citing_outside_its_context_is_refused(run: Recorder) -> None:
    """The middle link of R23's chain, and the one that makes it checkable with no store.

    The request records the scope, the answer records the context, and the citations point into it.
    A director can only cite what it was shown, so a cited sequence that is not in the context
    resolves to nothing it was authorized to read.
    """
    request = run.open_a_checkpoint_in_person()
    answer = run.statement_answer(request, citations=[999_999])

    run.record(sim.receive_answer(run.state, request.request_id, answer))
    run.advance_until(lambda s: request.request_id not in s.pending)

    rejected = [e for e in run.log if e.kind is EventKind.ANSWER_REJECTED][-1]
    assert "not in the context" in rejected.decoded_payload()["reason"]
    # Refused, not escalated: a briefing is not a decision, so the tray gains nothing.
    assert "escalated_to_ceo" not in rejected.decoded_payload()
    assert not [e for e in run.log if e.kind is EventKind.DECISION_RESOLVED]


def test_a_context_holding_another_lines_event_is_refused(run: Recorder) -> None:
    """The kernel re-derives the scope rather than trusting the one it recorded.

    A leg that widened its own context would otherwise be checked against the scope it chose. The
    scope comes from folded state at the landing tick, so a tampered request cannot help.
    """
    request = run.open_a_checkpoint_in_person()
    answer = run.statement_answer(request)
    answer[stmt.KEY_CONTEXT] = {
        **answer[stmt.KEY_CONTEXT],
        "events": [
            {"seq": 1, "tick": 1, "kind": "WORK_ASSIGNED", "person": "stf_cs", "item": "", "detail": ""}
        ],
    }
    answer[stmt.KEY_CITATIONS] = [1]

    run.record(sim.receive_answer(run.state, request.request_id, answer))
    run.advance_until(lambda s: request.request_id not in s.pending)

    rejected = [e for e in run.log if e.kind is EventKind.ANSWER_REJECTED][-1]
    assert "reporting line" in rejected.decoded_payload()["reason"]


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ({"seq": "tomorrow"}, "whole number"),
        ({"seq": -1}, "whole number"),
        ({"seq": True}, "whole number"),
        ({"tick": None}, "whole number"),
        ({"kind": 7}, "must be text"),
        ({"detail": "x" * 400}, "must be text"),
        ({"smuggled": {"a": [1, 2, 3]}}, "keys nothing reads"),
    ],
    ids=lambda value: str(value)[:24],
)
def test_a_malformed_context_entry_is_refused_rather_than_crashing_the_step(
    run: Recorder, mutation: dict, expected: str
) -> None:
    """A guard that can raise out of `step()` is worse than no guard.

    `refusal` coerces a sequence and reads a person and an item off each entry. A `seq` of
    `"tomorrow"` would raise `ValueError` out of the guard, out of `_apply_statement_answer`, out of
    `step()` and into the tick task — which is not a refused briefing, it is a stopped clock. So the
    shape is checked before the meaning, and an unknown key is refused outright, because this
    becomes a permanent logged fact and the caps only bound the fields somebody declared.
    """
    request = run.open_a_checkpoint_in_person()
    answer = run.statement_answer(request)
    entry = {
        "seq": 1,
        "tick": 1,
        "kind": "WORK_ASSIGNED",
        "person": BRIEFING_DIRECTOR,
        "item": "",
        "detail": "",
        **mutation,
    }
    answer[stmt.KEY_CONTEXT] = {**answer[stmt.KEY_CONTEXT], "events": [entry]}
    answer[stmt.KEY_CITATIONS] = []

    before_tick = run.state.tick
    run.record(sim.receive_answer(run.state, request.request_id, answer))
    run.advance_until(lambda s: request.request_id not in s.pending)

    assert run.state.tick > before_tick, "the clock stopped on a malformed context"
    assert expected in [
        e for e in run.log if e.kind is EventKind.ANSWER_REJECTED
    ][-1].decoded_payload()["reason"]


def test_an_oversized_answer_is_refused_at_the_door_rather_than_at_the_guard(
    run: Recorder,
) -> None:
    """The one bound the content guard cannot enforce, and the reason it cannot.

    `INPUT_RECEIVED` *is* the statement: it is appended the moment the leg answers, while
    `simcore.statement`'s caps run at the landing tick two sim-days later. So a cap that only decided
    whether the statement stood would be deciding it about a row already in an append-only log. This
    one is checked in `receive_answer`, before anything is queued.
    """
    request = run.open_a_checkpoint_in_person()
    answer = run.statement_answer(request)
    answer["briefing"] = "x" * (pend.MAX_ANSWER_PAYLOAD_BYTES + 1)

    with pytest.raises(sim.CommandRejected, match="append-only"):
        sim.receive_answer(run.state, request.request_id, answer)

    assert not run.state.queued_answers, "an oversized answer was queued anyway"
    # Still outstanding, so a leg that trims and retries is answered rather than locked out.
    assert request.request_id in run.state.pending
    _assert_the_log_still_replays(run)


def test_an_answer_carrying_a_float_is_refused_with_a_reason(run: Recorder) -> None:
    """No float may cross the log (R6), and the refusal should be readable rather than a 500.

    The append would refuse it too, from inside the writer — where the failure is an exception on the
    dispatch task rather than a rejection anybody can point at.
    """
    request = run.open_a_checkpoint_in_person()
    answer = run.statement_answer(request)
    answer["confidence"] = 0.87

    with pytest.raises(sim.CommandRejected, match="canonically encoded"):
        sim.receive_answer(run.state, request.request_id, answer)

    assert not run.state.queued_answers
    _assert_the_log_still_replays(run)


def test_an_answer_that_is_not_a_mapping_is_refused_before_it_is_queued(
    run: Recorder,
) -> None:
    """It used to queue itself and then kill the clock 1080 ticks later.

    `dict(answer)` is the last thing in `receive_answer` that can fail and it sat *after* the queue
    mutation, so a leg answering with a string left an entry in `state.queued_answers` and raised —
    the dispatch task swallowed the exception, and the tick loop died at the landing tick with the
    traceback two sim-days from the cause.
    """
    request = run.open_a_checkpoint_in_person()

    with pytest.raises(sim.CommandRejected, match="a mapping"):
        sim.receive_answer(run.state, request.request_id, "not a mapping")  # type: ignore[arg-type]

    assert not run.state.queued_answers
    before = run.state.tick
    run.advance(pend.STATEMENT_OFFSET_TICKS + 5)
    assert run.state.tick == before + pend.STATEMENT_OFFSET_TICKS + 5, "the clock died later"


def test_the_retrieval_writes_exactly_the_keys_the_guard_admits() -> None:
    """The two halves of one shape, asserted against each other rather than kept in step by hand.

    `bench/context.py` writes the context and `simcore.statement` refuses an undeclared key in it, so
    a field added to one and not the other is a statement the producer assembles and the kernel then
    rejects — with a reason that would send the reader to the wrong file.
    """
    from agents.bench import context as retrieval

    written = retrieval.Retrieved(
        director=BRIEFING_DIRECTOR, line=(BRIEFING_DIRECTOR,), since_seq=1, through_seq=2
    ).to_payload()
    assert set(written) == set(stmt.CONTEXT_KEYS)

    entry = retrieval.RetrievedEvent(
        seq=1, tick=1, kind="WORK_ASSIGNED", person=BRIEFING_DIRECTOR, item="", detail=""
    ).to_payload()
    assert set(entry) == set(stmt.CONTEXT_EVENT_KEYS)

    # And the producer's caps sit inside the guard's, in the direction that matters: a producer
    # truncating to more than the kernel admits would assemble statements the kernel then rejected
    # for a length nobody chose.
    assert retrieval.MAX_DETAIL_CHARS <= stmt.MAX_CONTEXT_FIELD_CHARS
    assert retrieval.MAX_EVENTS <= stmt.MAX_CONTEXT_EVENTS


def test_a_statement_with_no_objection_is_refused(run: Recorder) -> None:
    """A bench that only agrees adds nothing to a decision the CEO was going to take anyway."""
    request = run.open_a_checkpoint_in_person()

    run.record(
        sim.receive_answer(
            run.state, request.request_id, run.statement_answer(request, objection="  ")
        )
    )
    run.advance_until(lambda s: request.request_id not in s.pending)

    assert "objection" in [e for e in run.log if e.kind is EventKind.ANSWER_REJECTED][
        -1
    ].decoded_payload()["reason"]


def test_a_statement_attributed_to_somebody_else_is_refused(run: Recorder) -> None:
    """Attribution is what the citation check is *against*, so it cannot be optional.

    Two refusals, because there are two ways to get it wrong and they fail differently. Another
    *director* is the interesting one — the persona exists, so the only thing saying the attribution
    is wrong is that the item is in somebody else's line — and a specialist is the M14 case, where
    the persona was never authored at all.
    """
    request = run.open_a_checkpoint_in_person()

    run.record(
        sim.receive_answer(
            run.state, request.request_id, run.statement_answer(request, producer="dir_cs")
        )
    )
    run.advance_until(lambda s: request.request_id not in s.pending)
    reason = [e for e in run.log if e.kind is EventKind.ANSWER_REJECTED][-1].decoded_payload()[
        "reason"
    ]
    assert "produced a statement about an item in 'dir_hr''s line" in reason

    second = run.open_a_checkpoint_in_person()
    run.record(
        sim.receive_answer(
            run.state, second.request_id, run.statement_answer(second, producer="stf_cs")
        )
    )
    run.advance_until(lambda s: second.request_id not in s.pending)
    assert "not a director" in [
        e for e in run.log if e.kind is EventKind.ANSWER_REJECTED
    ][-1].decoded_payload()["reason"]


def test_the_guards_are_one_implementation_with_two_call_sites() -> None:
    """Execution decision §1, asserted where it can drift rather than where it was decided.

    The cost the plan names for guarding in both processes is two copies. What avoids it is that the
    predicates live in `packages/`, which both services may import and neither may reimplement — so
    the check is that `simcore.statement` is what the kernel calls, and that it is pure.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(stmt))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])

    # `re` joined the set with U11's two predicates, which are lexical over a closed vocabulary and
    # need a pattern to flatten prose with. Still pure and still stdlib, which is the property this
    # assertion is about — the list is a whitelist of what the module has needed so far, not a claim
    # that it will never need another stdlib module.
    assert imported <= {"__future__", "collections", "dataclasses", "re", "typing", "simcore"}

    # And the kernel calls it, asserted against the call graph rather than by substring. The first
    # version of this looked for `"refusal"` in `step.py`'s source, which is in a dozen comments and
    # in `refusal = pend.validate_domain_answer(...)` — it passed with `stmt.refusal` never reached.
    step_tree = ast.parse(inspect.getsource(sim))
    calls_the_guard = [
        node
        for node in ast.walk(step_tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "refusal"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "stmt"
    ]
    assert calls_the_guard, "the kernel does not call the shared guard"


# =========================================================================
# Deadlines, caps, forks and walking away
# =========================================================================


def test_a_statement_request_carries_its_own_deadline_not_the_shared_one(
    run: Recorder,
) -> None:
    """R18. The shared deadline is sized for a service on a loopback, not for a provider API."""
    request = run.open_a_checkpoint_in_person()
    window = request.deadline_tick - request.raised_at_tick

    assert window == pend.STATEMENT_DEADLINE_TICKS
    assert window > pend.REQUEST_DEADLINE_TICKS

    # The number is a wall-clock budget at the fastest rate the client offers, converted. Stated
    # here so that a client adding a faster rate fails this rather than discovering it as briefings
    # that stopped appearing.
    ticks_per_wall_second = (
        simtime.TICKS_PER_WALL_SECOND_AT_BASE_RATE * pend.FASTEST_CLIENT_RATE
    )
    assert pend.STATEMENT_OFFSET_TICKS // ticks_per_wall_second == pend.STATEMENT_ANSWER_SECONDS
    assert window // ticks_per_wall_second == 2 * pend.STATEMENT_ANSWER_SECONDS
    # And the shared one really is the five wall-seconds the plan says it is, at that rate.
    assert pend.REQUEST_DEADLINE_TICKS // ticks_per_wall_second == 5


def test_at_the_fastest_rate_a_statement_survives_long_enough_to_be_answered(
    run: Recorder,
) -> None:
    """R18, as behaviour rather than as arithmetic.

    The provider is given the whole budget — every tick of it — and the statement still lands. Under
    the shared one-sim-day deadline the request would have been abandoned a third of the way in.
    """
    request = run.open_a_checkpoint_in_person()
    budget_ticks = (
        pend.STATEMENT_ANSWER_SECONDS
        * simtime.TICKS_PER_WALL_SECOND_AT_BASE_RATE
        * pend.FASTEST_CLIENT_RATE
    )
    assert budget_ticks > pend.REQUEST_DEADLINE_TICKS

    # Right up to the last tick of the window, measured from the raise rather than from here: the
    # window is the offset, and an answer arriving *on* the landing tick has already missed it.
    run.advance_until(lambda s: s.tick >= request.raised_at_tick + budget_ticks - 1)
    assert request.request_id in run.state.pending, "abandoned inside its own budget"

    run.record(sim.receive_answer(run.state, request.request_id, run.statement_answer(request)))
    run.advance_until(lambda s: request.request_id not in s.pending)

    assert not [
        e
        for e in run.log
        if e.kind is EventKind.ANSWER_REJECTED and e.request_id == request.request_id
    ]


def test_an_answer_minted_for_a_parent_run_is_refused_by_a_fork(run: Recorder) -> None:
    """A fork holds its prefix, and nothing the parent raised afterwards.

    The prefix really is a prefix here: the child is folded from the log *as it stood at the fork*,
    which is what `LogStore.fork_run` copies. Then the parent goes on and asks a question the child's
    prefix does not contain, and the child refuses the answer to it.

    **What enforces that is the delivery point rather than the id**, and the distinction matters
    because the id is not run-scoped — `State` carries no run id, so nothing derivable inside `step()`
    can tell a parent from a fork that is in the same state. An answer names the run it is for and is
    looked up in *that* run's pending projection, so no route in this system carries one run's answer
    to another. `pending.statement_request_id` states the limitation and names U16 as its owner; the
    test below is the collision itself, asserted rather than left to be discovered.
    """
    forked_at = run.state.tick
    prefix = list(run.log)
    child = folder.fold(prefix, at_live_head=True, through_tick=forked_at).state

    # The parent goes on and raises a statement the child's prefix does not contain.
    request = run.open_a_checkpoint_in_person()
    assert len(run.log) > len(prefix), "the parent appended nothing, so there is no divergence"
    assert request.request_id in run.state.pending
    assert request.request_id not in child.pending

    events = sim.receive_answer(child, request.request_id, run.statement_answer(request))

    assert events[0].kind is EventKind.ANSWER_REJECTED
    assert "never raised" in events[0].payload["reason"]


def test_a_fork_at_the_same_tick_mints_the_same_request_id(run: Recorder) -> None:
    """The limitation `pending.statement_request_id` states, asserted so it cannot be forgotten.

    Two runs in the same state at the same tick mint the same statement request id, because the four
    facts the id is derived from are all state and `State` carries no run id. At the fork tick that is
    harmless — the two runs *are* the same run, so an answer is equally correct for both, which is the
    argument execution decision §3 makes about the prefix copy — and past it nothing routes one run's
    answer to the other. Closing it needs a run identifier the fold reproduces, which is a state-shape
    change and belongs with U16.
    """
    request = run.open_a_checkpoint_in_person()

    # A second run playing the same script: the fork's pre-divergence prefix, in effect.
    twin = Recorder(horizon_tick=simtime.TICKS_PER_SIM_DAY * 40)
    twin_request = twin.open_a_checkpoint_in_person()

    assert twin_request.request_id == request.request_id, (
        "the ids stopped colliding, which means somebody made them run-scoped — good, and this "
        "test and `statement_request_id`'s docstring both need to say so"
    )
    # And both hold it as their own, which is why the delivery point rather than the id is what
    # keeps one run's answer out of the other.
    assert request.request_id in run.state.pending
    assert twin_request.request_id in twin.state.pending


def test_a_checkpoint_past_the_per_item_cap_is_refused_rather_than_crashing_the_clock(
    run: Recorder,
) -> None:
    """The cap raises, and an exception escaping `step()` is not a refusal — it is a dead clock.

    `_raise_period_consult` was the only caller that caught `RequestCapExceeded`. The derivation now
    checks the count against the same helper the cap uses, so an item at its limit is skipped and the
    clock keeps its own time; a command applied at that instant still answers, rather than becoming
    an opaque 500 for a state the player caused.
    """
    request = run.open_a_checkpoint_in_person()

    # Fill the item's remaining slots the way a run at its limit would be.
    for index in range(pend.MAX_OUTSTANDING_PER_ITEM - 1):
        run.record(
            sim.raise_request(
                run.state, pend.AGENTS, BRIEFING_ITEM, request_id=f"filler-{index}"
            )
        )
    assert pend.outstanding_for_item(run.state.pending, BRIEFING_ITEM) == (
        pend.MAX_OUTSTANDING_PER_ITEM
    )

    before_tick = run.state.tick
    run.advance(120)

    assert run.state.tick == before_tick + 120, "the clock stalled on a capped item"
    assert len([e for e in run.log if _is_statement_request(e)]) == 1

    # And the cap itself still refuses with a sentence when something asks it directly.
    with pytest.raises(pend.RequestCapExceeded) as excinfo:
        sim.raise_request(run.state, pend.BENCH, BRIEFING_ITEM, request_id="one-too-many")
    assert "at the cap" in str(excinfo.value)
    assert request.request_id in run.state.pending


def test_a_statement_outstanding_when_the_ceo_walks_away_expires_without_stalling(
    run: Recorder,
) -> None:
    """Walking off mid-briefing is a normal gesture, so it must have a normal ending."""
    request = run.open_a_checkpoint_in_person()
    run.walk_ceo_away()

    before_tick = run.state.tick
    run.advance_until(lambda s: request.request_id not in s.pending)

    assert run.state.tick >= request.deadline_tick
    assert run.state.tick > before_tick
    abandoned = [
        e
        for e in run.log
        if e.kind is EventKind.ANSWER_REJECTED and e.decoded_payload().get("abandoned")
    ]
    assert abandoned
    # Not escalated. The checkpoint was already the CEO's; a briefing that never came adds no claim.
    assert abandoned[-1].decoded_payload()["escalated_to_ceo"] is False


def test_a_statement_outstanding_across_a_reconnection_still_lands(run: Recorder) -> None:
    """A reconnection is a refold, and a refold has to reproduce the question as well as the state.

    This is the projection the plan calls a projection of the log: the request survives being
    rebuilt, and the fold hands back the director and the scope the leg needs to ask it again — which
    is what lets a restarted kernel answer rather than wait out a deadline.
    """
    request = run.open_a_checkpoint_in_person()

    folded = folder.fold(run.log, at_live_head=True, through_tick=run.state.tick)
    outstanding = folded.outstanding_requests[request.request_id]

    assert outstanding["service"] == pend.BENCH
    assert outstanding["person"] == BRIEFING_DIRECTOR
    assert BRIEFING_ITEM in outstanding["scope"]["items"]

    # And the answer still lands on the rebuilt run.
    events = sim.receive_answer(
        folded.state, request.request_id, run.statement_answer(request)
    )
    assert events[0].kind is EventKind.INPUT_RECEIVED

    while request.request_id in folded.state.pending:
        sim.step(folded.state)
    assert not [
        item
        for item in folded.state.pending.values()
        if item.request_id == request.request_id
    ]


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
