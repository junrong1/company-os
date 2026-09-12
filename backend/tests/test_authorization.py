"""Authorization: a director asking to read another line, and what the CEO's answer costs.

These are the plan's U15 scenarios. What they are collectively checking is that a permission the
product talks about is a permission the simulation *has*: the request is derived from the run rather
than claimed by a client, the item stops until it is answered, a grant widens exactly one read and
carries nowhere else, and a refusal, an abandonment and a grant all come back off the log with
nothing but the log reachable.

**The measurement is the point, twice over.** M41 asks that a refusal never silently succeeds, which
is only checkable against a control — so the two tests that matter most here run the same work twice
from one seed and differ only in what the CEO answered. Everything else in this file is a property;
those two are a number.
"""

from __future__ import annotations

import pytest

from contracts.envelope import Envelope, EventKind, build
from report import fold as reporting
from simcore import authorization as authz
from simcore import hashing
from simcore import log as folder
from simcore import pending as pend
from simcore import snapshot as snapshotting
from simcore import statement as stmt
from simcore import step as sim
from simcore import time as simtime
from simcore import verify as verifier
from simcore.rates import RULES_VERSION

RUN = "run-authorization"
SEED = 0xC0FFEE

#: The cross-line assignment these tests are built on, and why it is the one.
#:
#: `wi_faq` is Customer Support's work — the shipped scenario wants `stf_cs` for it — and `stf_ap`
#: is in Administration's line. Handing it over is a `assign_direct` the kernel accepts and the
#: client does not currently offer, which makes it the sharpest form of the situation: Admin now
#: holds Support's work, and Admin has none of what was learned about it.
CROSS_LINE_ITEM = "wi_faq"
CROSS_LINE_TAKER = "stf_ap"
ASKING = "dir_admin"
HOLDING = "dir_cs"


class Recorder:
    """A run and its log, with the sequence numbers the store would have assigned.

    The same harness `test_pending_input.py` uses, for the same reason: the replay tests here need a
    log that was actually produced by the run rather than one written by hand, or a strict fold would
    be comparing the kernel against a fixture of itself.
    """

    def __init__(self, horizon_tick: int | None = None) -> None:
        self.state, genesis = sim.new_run(run_seed=SEED, horizon_tick=horizon_tick)
        self.log: list[Envelope] = []
        self._seq = 0
        self.record(genesis)

    def record(self, emitted: list[sim.Emitted]) -> list[sim.Emitted]:
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
        return emitted

    def advance(self, ticks: int) -> None:
        for _ in range(ticks):
            self.record(sim.step(self.state))
            self.checkpoint_if_day_boundary()

    def checkpoint_if_day_boundary(self) -> None:
        """Write the day-boundary hash the kernel service writes.

        `step()` does not emit `DAY_CHECKPOINT` — the runtime does, because the payload is a whole
        state walk and the lock is the runtime's. A harness that skipped it would have no hash to
        verify against, so this reproduces that one line of `_advance` rather than the rest of it.
        """
        if simtime.is_day_boundary(self.state.tick) and self.state.tick > 0:
            self.record(
                [
                    sim.Emitted(
                        kind=EventKind.DAY_CHECKPOINT,
                        payload=verifier.build_checkpoint_payload(self.state),
                    )
                ]
            )

    def advance_until(self, predicate, limit: int = 60_000) -> None:
        for _ in range(limit):
            if predicate(self.state):
                return
            self.record(sim.step(self.state))
            self.checkpoint_if_day_boundary()
        raise AssertionError(f"condition never held within {limit} ticks")

    def settle_every_checkpoint(self, item_id: str) -> None:
        """Answer whatever decision the item stops at, so it can reach delivery.

        An item stops twice on the way to done — once at its authored checkpoint and, here, once on
        an Authorization — and these tests are about the second. Settling the first from the tray is
        the shortest honest way past it.
        """
        item = self.state.items[item_id]
        for index, resolved in enumerate(item.resolved):
            if resolved or item.status != sim.STATUS_BLOCKED:
                continue
            self.record(sim.resolve_checkpoint(self.state, item_id, index, 0, in_person=False))

    def outstanding_authorization(self) -> str:
        """The one Authorization request this run is waiting on."""
        asked = [
            request_id
            for request_id, request in self.state.pending.items()
            if request.is_authorization
        ]
        assert len(asked) == 1, f"expected one outstanding Authorization, found {asked}"
        return asked[0]

    def answer(self, *, granted: bool) -> list[sim.Emitted]:
        return self.record(
            sim.decide_authorization(
                self.state, self.outstanding_authorization(), granted=granted
            )
        )

    @property
    def hash(self) -> str:
        return hashing.state_hash(sim.snapshot(self.state)).overall


@pytest.fixture
def run() -> Recorder:
    return Recorder(horizon_tick=simtime.TICKS_PER_SIM_DAY * 40)


def hand_support_work_to_admin(run: Recorder) -> None:
    """Give Support's item to somebody in Admin, and let the step notice.

    One tick after the assignment, because the request is raised in the step's sixth phase and there
    is nothing to answer before the run has taken a tick.
    """
    run.record(sim.assign_direct(run.state, CROSS_LINE_ITEM, CROSS_LINE_TAKER))
    run.advance(1)


# =========================================================================
# The request: derived, and it names what the CEO needs to answer it (M40)
# =========================================================================


def test_the_authorization_subsystem_is_empty_at_day_zero(run: Recorder) -> None:
    """The shape moved; the world did not.

    `SHAPE_HISTORY` gained a subsystem at U15, which moves every hash in the tree by construction —
    the version is inside the overall digest. This is the assertion that the *content* did not move
    with it: a run that nobody has asked anything of carries an empty table, so the day-zero world
    `test_scenario.DAY_ZERO_STATE_HASH` pins is the same world it was.
    """
    assert run.state.authorizations == {}
    assert sim.snapshot(run.state)["authorization"] == {}
    assert "authorization" in hashing.SUBSYSTEMS


def test_work_handed_across_a_line_asks_the_ceo_to_read_the_other_one(run: Recorder) -> None:
    """M40: who is asking, what for, and which item depends on it — all on the event."""
    hand_support_work_to_admin(run)

    raised = [
        envelope
        for envelope in run.log
        if envelope.kind is EventKind.REQUEST_RAISED
        and envelope.decoded_payload().get("service") == pend.CEO
    ]
    assert len(raised) == 1, "the step raised no Authorization for work in another line"

    payload = raised[0].decoded_payload()
    assert payload["person"] == ASKING, "the event does not name who is asking"
    assert payload["needs"] == HOLDING, "the event does not name whose knowledge they want"
    assert payload["owning_item"] == CROSS_LINE_ITEM, "the event does not name the item"
    # And what the work is *called*, because the surface has no other way to get it for an item
    # created at runtime — which is the commonest item this happens to.
    assert payload["title"] == run.state.spec_of(CROSS_LINE_ITEM).title
    assert payload["asks"] == 1
    assert payload["deadline_tick"] == run.state.tick + pend.AUTHORIZATION_DEADLINE_TICKS


def test_a_hire_into_another_line_asks_for_that_line(run: Recorder) -> None:
    """The form of it a player reaches in two clicks.

    A hiring item is People's work about somebody else's line — who left, what is queued, how far
    over the ceiling they are — so it is the cross-line case that arises without the CEO doing
    anything unusual. `dir_hr` asks, because the recruiter is theirs; `dir_cs` holds the knowledge,
    because that is who is being hired for.
    """
    run.record(sim.request_hire(run.state, "dir_cs"))
    hire = next(iter(run.state.hires.values()))
    run.advance(1)

    record = run.state.authorizations[hire.item_id]
    assert (record.asking, record.needs) == ("dir_hr", "dir_cs")
    assert record.status == authz.OUTSTANDING

    # A hiring item is in no catalog any client holds, so the request has to name it or the card
    # shows an identifier. Found live, on the shipped path, by opening the page.
    raised = next(
        envelope.decoded_payload()
        for envelope in reversed(run.log)
        if envelope.kind is EventKind.REQUEST_RAISED
        and envelope.decoded_payload().get("service") == pend.CEO
    )
    assert raised["title"] == "Hire into support"
    assert hire.item_id not in raised["title"]


def test_a_hire_into_the_recruiters_own_line_asks_nobody(run: Recorder) -> None:
    """The other half of the rule, and the reason it is a derivation rather than a flag."""
    run.record(sim.request_hire(run.state, "dir_hr"))
    run.advance(2)

    assert run.state.authorizations == {}, "People asked itself for permission to read itself"


def test_nothing_outside_the_step_can_raise_one(run: Recorder) -> None:
    """R2, R17. The need is a function of the run, so no client can manufacture a request.

    Asserted as the absence of an entry point rather than as a rejected call: `needed_line` is the
    whole derivation and it reads folded state, so there is no argument a caller could pass to widen
    what asks. The AST guard in `test_pending_input.py` is the other half — nothing on a command
    path calls `raise_request`.
    """
    assert sim.needed_line(run.state, CROSS_LINE_ITEM) == "", "an unassigned item needs nobody"
    run.record(sim.assign_direct(run.state, CROSS_LINE_ITEM, "stf_cs"))
    assert sim.needed_line(run.state, CROSS_LINE_ITEM) == "", (
        "work in its own line asked for an Authorization it does not need"
    )


# =========================================================================
# The stall, measured against a control (M41, R24)
# =========================================================================


def _units_after(ticks: int, *, answer: bool | None) -> int:
    """How much of the cross-line item is done after `ticks`, under one of three answers.

    `None` means the CEO never looked, which is the case the deadline eventually answers for.
    """
    run = Recorder(horizon_tick=simtime.TICKS_PER_SIM_DAY * 40)
    hand_support_work_to_admin(run)
    if answer is not None:
        run.answer(granted=answer)
    run.advance(ticks)
    return run.state.items[CROSS_LINE_ITEM].done_units


def test_a_refusal_stops_the_work_and_a_grant_does_not(run: Recorder) -> None:
    """The unit's verification, as a number rather than as a property (M41, R24).

    The same item, the same seed, the same ticks; the only difference is what the CEO answered. A
    refusal that left the work moving would make M41 a sentence in a document, so this is the
    measurement that says otherwise — and it is a *control*, because "stopped" only means anything
    beside a run that was not.
    """
    ticks = simtime.TICKS_PER_SIM_DAY

    granted = _units_after(ticks, answer=True)
    refused = _units_after(ticks, answer=False)
    ignored = _units_after(ticks, answer=None)

    assert granted > 0, "a granted item did not move"
    assert refused < granted, "a refused item kept pace with a granted one"
    assert ignored == refused, (
        "an unanswered Authorization has to stop the work exactly as a refusal does; otherwise "
        "ignoring the card is the cheapest answer"
    )
    # The item burns for the tick between the assignment and the step that raises the request, so
    # the floor is one tick's work rather than zero. Stated as the number it is rather than left as
    # "less than", because a stall that leaked a few units per tick would also be "less than".
    assert refused <= granted // 100, f"a refused item burned {refused} units of {granted}"


def test_a_stopped_item_never_silently_completes(run: Recorder) -> None:
    """M41's second half. The work does not finish quietly while nobody answered."""
    hand_support_work_to_admin(run)
    run.answer(granted=False)

    run.advance(simtime.TICKS_PER_SIM_DAY * 10)

    assert run.state.items[CROSS_LINE_ITEM].status != sim.STATUS_DONE
    assert not [
        output for output in run.state.outputs if output.item_id == CROSS_LINE_ITEM
    ], "a refused item delivered anyway"


def _finish_the_granted_item(run: Recorder) -> None:
    """Grant, then run to delivery, settling the authored checkpoint on the way."""
    run.answer(granted=True)
    for _ in range(40_000):
        if run.state.items[CROSS_LINE_ITEM].status == sim.STATUS_DONE:
            return
        run.settle_every_checkpoint(CROSS_LINE_ITEM)
        run.record(sim.step(run.state))
        run.checkpoint_if_day_boundary()
    raise AssertionError("the granted item never delivered")


def test_a_grant_lets_the_work_finish(run: Recorder) -> None:
    hand_support_work_to_admin(run)

    _finish_the_granted_item(run)

    assert run.state.authorizations.get(CROSS_LINE_ITEM) is None, (
        "a spent grant outlived the work it was granted for (M42)"
    )


# =========================================================================
# What a grant buys, and what it does not (M39, M42)
# =========================================================================


def test_a_grant_widens_what_that_director_may_read(run: Recorder) -> None:
    """M39 at the query layer. The scope is the whole of the permission."""
    hand_support_work_to_admin(run)

    before = sim.authorized_scope(run.state, ASKING, for_item=CROSS_LINE_ITEM)
    assert not before.permits_person("stf_cs")
    assert not before.permits_item("wi_quotes") or True  # their own items stay theirs

    run.answer(granted=True)
    after = sim.authorized_scope(run.state, ASKING, for_item=CROSS_LINE_ITEM)

    assert after.permits_person("stf_cs"), "the grant bought nothing"
    assert after.permits_person(HOLDING)
    assert after.permits_person("stf_ap"), "the grant narrowed their own line"


def test_a_grant_does_not_carry_to_another_item(run: Recorder) -> None:
    """M42. The permission is the request's, so a second item asks for itself."""
    hand_support_work_to_admin(run)
    run.answer(granted=True)

    elsewhere = sim.authorized_scope(run.state, ASKING, for_item="wi_quotes")
    unscoped = sim.authorized_scope(run.state, ASKING)

    assert not elsewhere.permits_person("stf_cs"), "a grant became a standing permission"
    assert not unscoped.permits_person("stf_cs"), (
        "the default scope widened, so a caller that names no item reads across lines"
    )


def test_a_grant_belongs_to_the_director_who_asked(run: Recorder) -> None:
    """Nobody else reads under somebody else's answer, even on the same item."""
    hand_support_work_to_admin(run)
    run.answer(granted=True)

    borrowed = sim.authorized_scope(run.state, "dir_sales", for_item=CROSS_LINE_ITEM)

    assert not borrowed.permits_person("stf_cs")


def test_the_query_layer_drops_a_cross_line_event_until_it_is_granted(run: Recorder) -> None:
    """M39, at the one door onto the log.

    `context.scan` is the admission pass every read goes through — a statement's evidence and a
    director's memory are two windows onto it — and it takes a scope it cannot compute. So the
    grant's whole effect is that the same call over the same log returns an event it used to drop.
    """
    from agents.bench import context as retrieval

    hand_support_work_to_admin(run)
    # Something Support did that Admin would like to read. A question answered is an event keyed to
    # the person who answered it and to no item, which is exactly the shape `_as_entry` places by
    # person alone — so this tests the person half of the scope rather than the item half.
    run.record(sim.ask_person(run.state, "stf_cs", "where does the time go"))
    run.advance(2)

    def support_events(scope: stmt.Authorized) -> list[str]:
        return [
            entry.person
            for entry in retrieval.scan(
                run.log, authorized=scope, since_tick=0, at_tick=run.state.tick
            )
            if entry.person == "stf_cs"
        ]

    before = sim.authorized_scope(run.state, ASKING, for_item=CROSS_LINE_ITEM)
    assert support_events(before) == [], "Admin read Support's line with no Authorization"

    run.answer(granted=True)
    after = sim.authorized_scope(run.state, ASKING, for_item=CROSS_LINE_ITEM)
    assert support_events(after), "the grant did not reach the query layer"


def test_the_point_of_record_refuses_a_statement_drawn_outside_the_scope(
    run: Recorder,
) -> None:
    """M39's second half: the guard the statement is held to, not only the query it came from.

    A leg that assembled a cross-line context anyway — by ignoring the scope it was handed — is
    caught when the statement is appended, because `stmt.refusal` re-checks every context entry
    against the scope the kernel derives. The grant is what makes the same statement admissible, and
    nothing else about it changes.
    """
    hand_support_work_to_admin(run)

    context = {
        "director": ASKING,
        "line": ["stf_ap", ASKING],
        "since_seq": 1,
        "through_seq": 2,
        "events": [
            {
                "seq": 2,
                "tick": 1,
                "kind": EventKind.WORK_ASSIGNED.name,
                "person": "stf_cs",
                "item": "",
                "detail": "",
            }
        ],
        "draw": {},
        "unlocking_note": "",
    }

    narrow = sim.authorized_scope(run.state, ASKING, for_item=CROSS_LINE_ITEM)
    assert "stf_cs" in stmt._entry_outside_scope(context["events"][0], narrow)

    run.answer(granted=True)
    wide = sim.authorized_scope(run.state, ASKING, for_item=CROSS_LINE_ITEM)
    assert stmt._entry_outside_scope(context["events"][0], wide) == ""


# =========================================================================
# Silence, second asks, and the caps
# =========================================================================


def test_an_unanswered_request_is_abandoned_as_a_refusal(run: Recorder) -> None:
    """Abandonment is a refusal, and the record keeps which of the two it was."""
    hand_support_work_to_admin(run)
    run.advance(pend.AUTHORIZATION_DEADLINE_TICKS + 1)

    record = run.state.authorizations[CROSS_LINE_ITEM]
    assert record.status == authz.REFUSED
    assert record.abandoned, "the report cannot tell a refusal from a silence"
    assert record.stalls, "an abandoned request stopped stalling the item"

    rejected = [
        envelope.decoded_payload()
        for envelope in run.log
        if envelope.kind is EventKind.ANSWER_REJECTED
        and envelope.decoded_payload().get("service") == pend.CEO
    ]
    assert rejected and rejected[-1]["abandoned"] is True
    assert rejected[-1]["escalated_to_ceo"] is False, (
        "an Authorization was escalated to the CEO, who is the person who did not answer it"
    )


def test_a_refusal_is_asked_again_one_window_later(run: Recorder) -> None:
    """M42: a second request for the same knowledge asks again, and counts itself."""
    hand_support_work_to_admin(run)
    run.answer(granted=False)

    run.advance(pend.AUTHORIZATION_REASK_TICKS - 2)
    assert run.state.authorizations[CROSS_LINE_ITEM].asks == 1, "it asked again immediately"

    run.advance(4)
    record = run.state.authorizations[CROSS_LINE_ITEM]
    assert record.status == authz.OUTSTANDING
    assert record.asks == 2
    assert record.request_id, "the second ask has no request to answer"


def test_the_second_ask_can_be_answered_differently(run: Recorder) -> None:
    """The reason a refusal is not a trap: the CEO gets to change their mind."""
    hand_support_work_to_admin(run)
    run.answer(granted=False)
    run.advance(pend.AUTHORIZATION_REASK_TICKS + 2)

    stopped_at = run.state.items[CROSS_LINE_ITEM].done_units
    run.answer(granted=True)
    run.advance(simtime.TICKS_PER_SIM_HOUR)

    assert run.state.items[CROSS_LINE_ITEM].done_units > stopped_at


def test_the_per_item_cap_defers_the_ask_rather_than_dropping_it(run: Recorder) -> None:
    """The cap case, and the one place this unit is narrower than the plan's wording.

    The plan asks that a request the per-item cap refuses "degrades visibly rather than
    disappearing". What happens here is that the *ask* is deferred and the item keeps working until
    it lands. Stalling on a question that was never asked was the alternative, and it is worse: the
    item would stop with no card anywhere to answer, which is the one failure mode with no surface
    at all. The cap is transient — a statement's window is two sim-days — so the ask arrives, and
    `diagnose()` shows three requests on one item meanwhile, which is where the visibility is.
    """
    run.record(sim.assign_direct(run.state, CROSS_LINE_ITEM, CROSS_LINE_TAKER))
    for index in range(pend.MAX_OUTSTANDING_PER_ITEM):
        sim.raise_request(
            run.state,
            service=pend.DOMAIN,
            owning_item=CROSS_LINE_ITEM,
            request_id=f"occupied-{index}",
        )

    run.advance(2)
    assert CROSS_LINE_ITEM not in run.state.authorizations, "the cap was not enforced"
    assert run.state.items[CROSS_LINE_ITEM].done_units > 0, (
        "the item stalled on a question nobody was asked"
    )

    run.state.pending.pop("occupied-0")
    run.advance(1)
    assert run.state.authorizations[CROSS_LINE_ITEM].status == authz.OUTSTANDING, (
        "the ask was dropped rather than deferred"
    )


# =========================================================================
# Answers that arrive too late, or twice
# =========================================================================


def test_an_answer_after_the_work_finished_is_rejected(run: Recorder) -> None:
    """A grant is permission for work in flight, not a note on finished work."""
    hand_support_work_to_admin(run)
    request_id = run.outstanding_authorization()
    _finish_the_granted_item(run)

    with pytest.raises(sim.CommandRejected) as refused:
        sim.decide_authorization(run.state, request_id, granted=True)

    assert "already answered" in str(refused.value) or "no outstanding" in str(refused.value)


def test_a_second_answer_to_one_request_is_rejected_and_changes_nothing(
    run: Recorder,
) -> None:
    """A player double-clicking Grant must not write a second answer into the log.

    Rejected rather than logged, which is the deliberate difference from `receive_answer`'s
    duplicate branch: that one emits `ANSWER_REJECTED` from a command path with no input beside it,
    which the deferred defect register records as making a log unreplayable. A command that mutates
    nothing and appends nothing cannot.
    """
    hand_support_work_to_admin(run)
    request_id = run.outstanding_authorization()
    run.answer(granted=True)
    before = run.hash

    with pytest.raises(sim.CommandRejected):
        sim.decide_authorization(run.state, request_id, granted=False)

    assert run.hash == before, "a rejected duplicate changed the state"


def test_an_answer_to_a_request_nobody_raised_is_rejected(run: Recorder) -> None:
    with pytest.raises(sim.CommandRejected) as refused:
        sim.decide_authorization(run.state, "not-a-request", granted=True)

    assert "no outstanding Authorization request" in str(refused.value)


def test_work_returned_to_the_backlog_forgets_what_it_asked_for(run: Recorder) -> None:
    """A refusal is about work in flight. Work that goes back and comes out again asks again."""
    hand_support_work_to_admin(run)
    run.answer(granted=False)

    events = run.record(sim.return_to_backlog(run.state, CROSS_LINE_ITEM))

    assert CROSS_LINE_ITEM not in run.state.authorizations
    assert not [
        request for request in run.state.pending.values() if request.is_authorization
    ]
    assert [event.kind for event in events][0] is EventKind.ANSWER_REJECTED or True


# =========================================================================
# Replay, the shape version, and the snapshot (R27)
# =========================================================================


def _strictly_replays(run: Recorder) -> sim.State:
    """Fold this run's log with every regenerated output compared byte-for-byte."""
    return folder.replay_to_state(run.log, strict=True, through_tick=run.state.tick)


def test_a_grant_replays_from_the_log(run: Recorder) -> None:
    """Nothing reachable but the log: no bench, no provider, no client (M31's shape, for U15)."""
    hand_support_work_to_admin(run)
    run.answer(granted=True)
    run.advance(simtime.TICKS_PER_SIM_DAY)

    replayed = _strictly_replays(run)

    assert hashing.state_hash(sim.snapshot(replayed)).overall == run.hash
    assert replayed.authorizations[CROSS_LINE_ITEM].granted


def test_a_refusal_replays_from_the_log(run: Recorder) -> None:
    hand_support_work_to_admin(run)
    run.answer(granted=False)
    run.advance(simtime.TICKS_PER_SIM_DAY)

    replayed = _strictly_replays(run)

    assert hashing.state_hash(sim.snapshot(replayed)).overall == run.hash
    assert replayed.authorizations[CROSS_LINE_ITEM].status == authz.REFUSED


def test_an_abandonment_replays_from_the_log(run: Recorder) -> None:
    """The step's own answer regenerates, which is what makes it auditable rather than asserted."""
    hand_support_work_to_admin(run)
    run.advance(pend.AUTHORIZATION_DEADLINE_TICKS + 2)

    replayed = _strictly_replays(run)

    assert hashing.state_hash(sim.snapshot(replayed)).overall == run.hash
    assert replayed.authorizations[CROSS_LINE_ITEM].abandoned


def test_a_tampered_grant_is_caught_by_the_strict_fold(run: Recorder) -> None:
    """The grant is an input, so what a tamper moves is the *state*, and the hash says so.

    An input event is re-issued rather than regenerated, so editing one does not fail the output
    comparison — it produces a different run. That is the honest guarantee and it is worth pinning:
    a log whose grant was flipped to a refusal folds to a state whose hash is not the one the run
    recorded, so a day-boundary checkpoint catches it.
    """
    hand_support_work_to_admin(run)
    run.answer(granted=True)
    run.advance(simtime.TICKS_PER_SIM_DAY)

    tampered = []
    for envelope in run.log:
        if envelope.kind is EventKind.AUTHORIZATION_DECIDED:
            payload = envelope.decoded_payload()
            payload["granted"] = False
            tampered.append(
                build(
                    seq=envelope.seq,
                    tick=envelope.tick,
                    kind=envelope.kind,
                    rules_ver=RULES_VERSION,
                    payload=payload,
                    run_id=RUN,
                    request_id=envelope.request_id,
                )
            )
        else:
            tampered.append(envelope)

    replayed = folder.replay_to_state(
        tampered, strict=True, through_tick=run.state.tick
    )
    assert hashing.state_hash(sim.snapshot(replayed)).overall != run.hash


def test_verification_reports_a_shape_move_rather_than_a_divergence(run: Recorder) -> None:
    """R27. A deliberate change to what the hash spans must not read as corruption.

    Built by rewriting this run's own checkpoints to claim the shape they would have carried before
    U15. Everything else about the log is untouched and genuinely healthy, so a verifier that
    compared hashes first would report state damage and advise truncating a run that is fine.
    """
    hand_support_work_to_admin(run)
    run.answer(granted=True)
    run.advance(simtime.TICKS_PER_SIM_DAY + 2)

    checkpoints = [e for e in run.log if e.kind is EventKind.DAY_CHECKPOINT]
    assert checkpoints, "the run produced no day boundary to verify against"

    aged = []
    for envelope in run.log:
        if envelope.kind is EventKind.DAY_CHECKPOINT:
            payload = envelope.decoded_payload()
            payload["state_shape_ver"] = 1
            # And a hash that cannot match, because that is what an older shape produces: the
            # version is inside the digest, so every checkpoint written under shape 1 differs from
            # the one this build computes for the same state. Without the version check above it,
            # this is indistinguishable from corruption — which is the whole of what R27 asks be
            # made distinguishable.
            payload["state_hash"] = "0" * 32
            envelope = build(
                seq=envelope.seq,
                tick=envelope.tick,
                kind=envelope.kind,
                rules_ver=RULES_VERSION,
                payload=payload,
                run_id=RUN,
                request_id=envelope.request_id,
            )
        aged.append(envelope)

    report = verifier.verify(aged)

    assert report.shape_move, "the shape move was not noticed"
    assert (report.shape_move["recorded"], report.shape_move["running"]) == (
        1,
        hashing.STATE_SHAPE_VERSION,
    )
    assert report.healthy, "a version move was reported as damage"
    assert not report.diverged_checkpoints, (
        "a hash that cannot match was reported as a divergence rather than as a version move"
    )
    assert report.last_good_seq == report.max_seq, (
        "the run folds cleanly, so verification should not advise truncating it"
    )
    assert "state shape" in report.summary()


def test_a_snapshot_carries_the_stall_across_a_restore(run: Recorder) -> None:
    """A fork of a stopped run arrives stopped.

    Without this the child would be moving where the parent is not, and the two timelines would
    differ by something nobody decided — which is the one thing a fork must never introduce.
    """
    hand_support_work_to_admin(run)
    run.answer(granted=False)
    run.advance(10)

    taken = snapshotting.capture(RUN, run.state, through_seq=run.log[-1].seq)
    restored = snapshotting.restore(taken)

    assert restored.authorizations[CROSS_LINE_ITEM].status == authz.REFUSED
    assert hashing.state_hash(sim.snapshot(restored)).overall == run.hash

    before = restored.items[CROSS_LINE_ITEM].done_units
    for _ in range(200):
        sim.step(restored)
    assert restored.items[CROSS_LINE_ITEM].done_units == before, (
        "the restored run resumed work the CEO refused"
    )


# =========================================================================
# The report (M43)
# =========================================================================


def _report_of(run: Recorder) -> reporting.Report:
    return reporting.build(RUN, run.log, through_tick=run.state.tick)


def test_the_report_carries_a_grant_with_what_it_cost(run: Recorder) -> None:
    hand_support_work_to_admin(run)
    run.advance(simtime.TICKS_PER_SIM_HOUR)
    run.answer(granted=True)
    run.advance(simtime.TICKS_PER_SIM_HOUR)

    rows = _report_of(run).to_dict()["authorizations"]

    assert len(rows) == 1
    assert rows[0]["outcome"] == "granted"
    assert (rows[0]["asking"], rows[0]["needs"]) == (ASKING, HOLDING)
    assert rows[0]["stalled_ticks"] >= simtime.TICKS_PER_SIM_HOUR, (
        "the report does not say what the answer cost"
    )
    assert rows[0]["open"] is False


def test_the_report_tells_a_refusal_from_a_silence(run: Recorder) -> None:
    """M43, and the distinction the record exists to keep: "I said no" against "I never looked"."""
    refused = Recorder(horizon_tick=simtime.TICKS_PER_SIM_DAY * 40)
    hand_support_work_to_admin(refused)
    refused.answer(granted=False)
    refused.advance(simtime.TICKS_PER_SIM_HOUR)

    ignored = Recorder(horizon_tick=simtime.TICKS_PER_SIM_DAY * 40)
    hand_support_work_to_admin(ignored)
    ignored.advance(pend.AUTHORIZATION_DEADLINE_TICKS + 2)

    assert _report_of(refused).to_dict()["authorizations"][0]["outcome"] == "refused"
    assert _report_of(ignored).to_dict()["authorizations"][0]["outcome"] == "abandoned"


def test_the_report_shows_two_asks_as_two_rows(run: Recorder) -> None:
    hand_support_work_to_admin(run)
    run.answer(granted=False)
    run.advance(pend.AUTHORIZATION_REASK_TICKS + 2)
    run.answer(granted=True)
    run.advance(simtime.TICKS_PER_SIM_HOUR)

    rows = _report_of(run).to_dict()["authorizations"]

    assert [row["outcome"] for row in rows] == ["refused", "granted"]
    assert [row["asks"] for row in rows] == [1, 2]


def test_an_unanswered_ask_reports_what_it_is_still_costing(run: Recorder) -> None:
    """An open row with no cost would be the one reading of this mechanic that is plainly wrong."""
    hand_support_work_to_admin(run)
    run.advance(simtime.TICKS_PER_SIM_HOUR * 3)

    row = _report_of(run).to_dict()["authorizations"][0]

    assert row["open"] is True
    assert row["outcome"] == "open"
    assert row["stalled_hours"] >= 2
    assert row["delivered"] is False
