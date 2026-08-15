"""Branch comparison: where an option travels, without the run going there.

The load-bearing property is the first test in this file, and it is written first on purpose.
Everything else the comparison offers — trajectories, runway, what an option unlocks — is only
worth reading if running it changed nothing. R24 asks for that, and this file makes it a hash
comparison rather than a review of which lines mutate what.

The rest follows from the stop condition. A branch may not settle anything, so it runs exactly
as far as the next decision and then reports where it stopped.
"""

from __future__ import annotations

from typing import Any

import pytest

from contracts import canonical
from contracts.envelope import Envelope, EventKind, build
from simcore import compare
from simcore import hashing
from simcore import items as work
from simcore import lifecycle
from simcore import log as folder
from simcore import rates
from simcore import step as sim
from simcore import time as simtime
from simcore.rates import RULES_VERSION, TUNING

RUN = "run-compare"
SEED = 0xC0FFEE


class Recorder:
    """The harness the pending-input and replay suites use, kept identical on purpose."""

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

    def advance_until(self, predicate, limit: int = 60_000) -> None:
        for _ in range(limit):
            if predicate(self.state):
                return
            self.record(sim.step(self.state))
        raise AssertionError(f"condition never held within {limit} ticks")

    @property
    def hash(self) -> str:
        return hashing.state_hash(sim.snapshot(self.state)).overall


def blocked_at(item_id: str, person_id: str, horizon_days: int = 20) -> Recorder:
    """A run stopped at `item_id`'s first checkpoint, with the person waiting."""
    run = Recorder(horizon_tick=simtime.TICKS_PER_SIM_DAY * horizon_days)
    run.record(sim.assign_direct(run.state, item_id, person_id))
    run.advance_until(lambda state: state.items[item_id].status == sim.STATUS_BLOCKED)
    return run


def blocked_at_the_closing_cycle() -> Recorder:
    """A run stopped at the *first* of the closing-cycle item's two checkpoints.

    The only authored item with two, and therefore the only case that can distinguish "stopped
    at this checkpoint" from "stopped somewhere on this item". Reaching it is played rather
    than arranged: it gates on 24% Visibility, and settling the accounts-payable map in person
    on "PDF is the record" is exactly what pays for it.
    """
    run = blocked_at("wi_ap_map", "stf_ap", horizon_days=60)
    run.record(sim.resolve_checkpoint(run.state, "wi_ap_map", 0, 0, in_person=True))
    run.advance_until(lambda state: sim.is_unlocked(state, "wi_close"))
    run.record(sim.assign_direct(run.state, "wi_close", "dir_admin"))
    run.advance_until(lambda state: state.items["wi_close"].status == sim.STATUS_BLOCKED)
    return run


@pytest.fixture
def run() -> Recorder:
    return blocked_at("wi_ap_map", "stf_ap")


# =========================================================================
# R24: a branch never touches the parent
# =========================================================================


def test_a_branch_leaves_the_parent_byte_identical(run: Recorder) -> None:
    """AE9. The property the whole decision rests on.

    A hash comparison rather than a reading of which lines mutate what: the copy goes through
    the snapshot round-trip precisely so that "separate" is provable rather than reviewed, and
    this is the assertion that makes the proof mechanical.
    """
    before = run.hash
    before_tick = run.state.tick
    before_metrics = dict(run.state.metrics)

    compare.run_branch(run.state, "wi_ap_map", 0, 0, in_person=True)

    assert run.hash == before
    assert run.state.tick == before_tick
    assert run.state.metrics == before_metrics
    # R25: no clock movement in either direction.
    assert run.state.items["wi_ap_map"].status == sim.STATUS_BLOCKED
    assert run.state.items["wi_ap_map"].resolved == [False]


def test_every_option_leaves_the_parent_byte_identical(run: Recorder) -> None:
    """The same property across a whole comparison, not one branch.

    Three branches in sequence is the case that would catch a copy that is independent of the
    parent but shared *between* branches — each one would then start from the last one's
    finishing state, and only the first summary would be right.
    """
    before = run.hash
    options = work.spec("wi_ap_map").checkpoints[0].options

    for option_index in range(len(options)):
        compare.run_branch(run.state, "wi_ap_map", 0, option_index, in_person=True)
        assert run.hash == before


def test_two_branches_do_not_see_each_other(run: Recorder) -> None:
    """Each branch forks from the parent, never from the branch before it."""
    first = compare.run_branch(run.state, "wi_ap_map", 0, 0, in_person=True)
    second = compare.run_branch(run.state, "wi_ap_map", 0, 0, in_person=True)

    assert first.fork_tick == second.fork_tick == run.state.tick
    assert first.to_state() == second.to_state()


def test_the_same_branch_computed_twice_is_identical(run: Recorder) -> None:
    """AE10. No shared draw rule is needed for this, and R23 is dropped because of it.

    `rng.draw` is a pure function of run seed, tick, purpose and entity with no generator
    state, so a branch reproduces the parent's draws tick for tick by construction. An
    unattended branch also invokes no agent, because no CEO is inside it.
    """
    first = compare.run_branch(run.state, "wi_ap_map", 0, 1, in_person=False)
    second = compare.run_branch(run.state, "wi_ap_map", 0, 1, in_person=False)

    assert first.to_state() == second.to_state()


# =========================================================================
# R21, R22: where a branch stops, and what it reports
# =========================================================================


def test_a_branch_stops_at_the_first_downstream_checkpoint() -> None:
    """AE15. Nothing inside a branch may settle a decision, so it runs only as far as the next.

    Two items in flight, so a downstream checkpoint genuinely arrives: the branch's own item
    has one checkpoint, and the second item raises one while the branch is running.
    """
    run = blocked_at("wi_ap_map", "stf_ap")
    run.record(sim.assign_direct(run.state, "wi_quotes", "stf_buyer"))

    summary = compare.run_branch(run.state, "wi_ap_map", 0, 0, in_person=True)

    assert summary.stop_reason == compare.STOP_CHECKPOINT
    assert summary.stop_tick > summary.fork_tick
    # Reached and unsettled — the branch reports it rather than answering it.
    assert summary.stopped_at is not None
    assert summary.stopped_at["item"] == "wi_quotes"
    assert summary.stopped_at["tick"] == summary.stop_tick


def test_a_branch_that_raises_nothing_runs_to_the_horizon(run: Recorder) -> None:
    """Nothing else is assigned, so no further checkpoint is reachable."""
    summary = compare.run_branch(run.state, "wi_ap_map", 0, 0, in_person=True)

    assert summary.stop_reason == lifecycle.TERMINAL_HORIZON
    assert summary.stop_tick == run.state.horizon_tick
    assert summary.stopped_at is None
    assert summary.stop_detail


def test_a_branch_whose_costs_cross_zero_terminates_on_insolvency() -> None:
    """It reports insolvency rather than continuing past it.

    Driven by starting the branch from a state whose cash is already nearly gone, so that the
    daily costs the branch itself applies are what cross the line — the crossing is the
    branch's own arithmetic, not a value handed to it.
    """
    run = blocked_at("wi_ap_map", "stf_ap")
    run.state.metrics["cash"] = 1

    summary = compare.run_branch(run.state, "wi_ap_map", 0, 0, in_person=True)

    assert summary.stop_reason == lifecycle.TERMINAL_INSOLVENT
    assert summary.stop_tick < run.state.horizon_tick
    assert summary.metrics["cash"].value < 0
    # And the parent is still solvent, which is the point of the whole exercise.
    assert run.state.metrics["cash"] == 1
    assert run.state.terminal_reason == ""


def test_every_figure_in_a_summary_names_the_tick_it_was_measured_at(run: Recorder) -> None:
    """R22, in the form that makes a projection readable rather than authoritative."""
    summary = compare.run_branch(run.state, "wi_ap_map", 0, 0, in_person=True)
    wire = summary.to_state()

    for key, figure in wire["metrics"].items():
        assert figure["at_tick"] == summary.stop_tick, key
    assert wire["runway"]["at_tick"] == summary.stop_tick
    assert wire["daily_cost"]["at_tick"] == summary.stop_tick

    for key, points in wire["trajectories"].items():
        assert points, key
        for point in points:
            assert summary.fork_tick <= point["tick"] <= summary.stop_tick
        # Oldest first, and the ends are pinned to the fork and the stop so a reader can line
        # two branches up against each other.
        assert [point["tick"] for point in points] == sorted(
            point["tick"] for point in points
        )
        assert points[0]["tick"] == summary.fork_tick
        assert points[-1]["tick"] == summary.stop_tick


def test_a_trajectory_stays_bounded_over_a_full_horizon(run: Recorder) -> None:
    """A branch measures about 10,800 ticks; it must not carry a point for each of them."""
    summary = compare.run_branch(run.state, "wi_ap_map", 0, 0, in_person=True)

    for key, points in summary.trajectories.items():
        assert len(points) <= compare.TRAJECTORY_POINT_BOUND, key
    assert summary.stop_tick - summary.fork_tick > compare.TRAJECTORY_POINT_BOUND


@pytest.mark.parametrize("horizon_days", [5, 20, 60, 400])
def test_the_trajectory_bound_holds_at_any_horizon_the_caller_asks_for(
    horizon_days: int,
) -> None:
    """The case the default-horizon fixture hid.

    `POST /runs` takes `horizon_tick` from the caller with no upper limit, and the first version
    of this bound was a constant derived from the *default* horizon that no production code
    read — so a long run sailed past it and only a test with a default fixture was watching.
    Parameterised over horizons on both sides of the cap, because "the one we happened to use"
    is not the property.
    """
    run = blocked_at("wi_ap_map", "stf_ap", horizon_days=horizon_days)
    summary = compare.run_branch(run.state, "wi_ap_map", 0, 0, in_person=True)

    for key, points in summary.trajectories.items():
        assert len(points) <= compare.TRAJECTORY_POINT_BOUND, (horizon_days, key, len(points))


def test_a_branch_stops_at_the_comparison_bound_and_says_so() -> None:
    """A run whose horizon is past what a comparison will step.

    Reported as `bound`, never as `horizon`: "ran to the end of the run" and "ran as far as a
    comparison goes, and the run continues past here" are different facts, and reporting the
    second as the first would claim the projection covers ground it never walked.
    """
    beyond = compare.MAX_BRANCH_DAYS * 3
    run = blocked_at("wi_ap_map", "stf_ap", horizon_days=beyond)

    summary = compare.run_branch(run.state, "wi_ap_map", 0, 0, in_person=True)

    assert summary.stop_reason == compare.STOP_BOUND
    assert summary.stop_reason != lifecycle.TERMINAL_HORIZON
    assert summary.stop_tick < run.state.horizon_tick
    assert "not in this projection" in summary.stop_detail
    # And the cap is what stopped it, within the tick the loop checks on.
    span = summary.stop_tick - summary.fork_tick
    assert span <= compare.MAX_BRANCH_DAYS * simtime.TICKS_PER_SIM_DAY


def test_one_command_costs_the_same_however_long_the_run_is() -> None:
    """The guard that makes a comparison affordable regardless of what the caller asked for.

    The branches are stepped synchronously inside a request, on the same worker pool every other
    run's clock ticks on, so an unbounded horizon would let one command hold that pool for as
    long as the caller cared to ask for.
    """
    spans = []
    for horizon_days in (compare.MAX_BRANCH_DAYS * 2, compare.MAX_BRANCH_DAYS * 10):
        run = blocked_at("wi_ap_map", "stf_ap", horizon_days=horizon_days)
        summary = compare.run_branch(run.state, "wi_ap_map", 0, 0, in_person=True)
        spans.append(summary.stop_tick - summary.fork_tick)

    # A run ten times longer costs a branch exactly the same number of steps.
    assert spans[0] == spans[1]


def test_a_branch_carries_the_option_it_is_a_branch_of(run: Recorder) -> None:
    option = work.spec("wi_ap_map").checkpoints[0].options[2]
    summary = compare.run_branch(run.state, "wi_ap_map", 0, 2, in_person=False)

    assert summary.option_label == option.label
    assert summary.option_note == option.note
    assert summary.in_person is False


def test_the_route_is_priced_the_way_the_run_would_price_it(run: Recorder) -> None:
    """In person yields the visibility and morale premium; from the tray it costs morale.

    A branch that priced a route the CEO is not about to take would be wrong by exactly that
    premium, which is why the route is a parameter rather than a constant.
    """
    walked = compare.run_branch(run.state, "wi_ap_map", 0, 0, in_person=True)
    trayed = compare.run_branch(run.state, "wi_ap_map", 0, 0, in_person=False)

    first_walked = walked.trajectories["visibility"][0].value
    first_trayed = trayed.trajectories["visibility"][0].value
    assert first_walked > first_trayed


# =========================================================================
# R22: what an option opens and closes
# =========================================================================


def test_two_options_that_gate_differently_produce_different_unlock_sets(
    run: Recorder,
) -> None:
    """The accounts-payable checkpoint is the available case.

    Its three options pay different Visibility, and `wi_close` gates on Visibility — so the
    option chosen decides whether that item is reachable at all. That is precisely the kind of
    consequence a delta on a tile cannot express, and the reason a branch exists.
    """
    sets = [
        set(compare.run_branch(run.state, "wi_ap_map", 0, index, in_person=True).unlocked)
        for index in range(len(work.spec("wi_ap_map").checkpoints[0].options))
    ]

    assert any(sets[i] != sets[j] for i in range(len(sets)) for j in range(i + 1, len(sets))), (
        f"every option unlocked the same work ({sets}); this checkpoint no longer "
        "demonstrates that an option's gate consequence differs, and the case has to move"
    )


def test_a_branch_does_not_report_its_own_item_as_foreclosed(run: Recorder) -> None:
    """Completing work is not foreclosing it.

    The first cut of the gate predicate excluded items that had reached `done`, so the very
    item a branch was a branch of dropped out of the "after" set the moment the option got it
    finished — and every branch reported its own item as foreclosed. The option that got the
    work done read as the option that made it impossible.
    """
    summary = compare.run_branch(run.state, "wi_ap_map", 0, 0, in_person=True)

    assert "wi_ap_map" not in summary.foreclosed
    # And it did finish, so the case is live rather than vacuous.
    assert "wi_ap_auto" in summary.unlocked


def test_the_accounts_payable_options_differ_in_what_they_open(run: Recorder) -> None:
    """The concrete case behind the test above it, pinned.

    Visibility gates `wi_close` at 24%. "PDF is the record" pays six points and reaches it;
    "Let the team decide" pays one and does not. That is a consequence no delta on a tile can
    express, and it is the reason a branch exists at all.
    """
    pdf = compare.run_branch(run.state, "wi_ap_map", 0, 0, in_person=True)
    delegated = compare.run_branch(run.state, "wi_ap_map", 0, 2, in_person=True)

    assert "wi_close" in pdf.unlocked
    assert "wi_close" not in delegated.unlocked


def test_unlocked_names_only_work_the_fork_could_not_reach(run: Recorder) -> None:
    """Derived from the same gate predicate the kernel assigns against, in both directions."""
    summary = compare.run_branch(run.state, "wi_ap_map", 0, 0, in_person=True)

    for item_id in summary.unlocked:
        assert not sim.is_unlocked(run.state, item_id), item_id
    for item_id in summary.foreclosed:
        assert sim.is_unlocked(run.state, item_id), item_id
    assert not set(summary.unlocked) & set(summary.foreclosed)


def test_no_checkpoint_downstream_of_the_fork_is_resolved_inside_a_branch() -> None:
    """R22, structurally: a branch takes no action at anything it reaches.

    Checked over the branch's own emitted events rather than over its final state, because a
    decision taken and then overwritten would leave no trace in the latter.
    """
    run = blocked_at("wi_ap_map", "stf_ap")
    run.record(sim.assign_direct(run.state, "wi_quotes", "stf_buyer"))

    summary = compare.run_branch(run.state, "wi_ap_map", 0, 0, in_person=True)

    # Exactly one decision: the one the branch is a branch of, at the fork tick.
    resolutions = [
        event for event in summary.emitted if event.kind is EventKind.DECISION_RESOLVED
    ]
    assert len(resolutions) == 1
    assert resolutions[0].payload["item"] == "wi_ap_map"
    assert resolutions[0].payload["tick"] == summary.fork_tick

    # And nothing was assigned inside it either — an unassigned item stays unassigned.
    assert not [
        event
        for event in summary.emitted
        if event.kind in (EventKind.WORK_ASSIGNED, EventKind.WORK_REASSIGNED)
        and not event.payload.get("handoff_completed")
    ]


# =========================================================================
# What the runner refuses
# =========================================================================


def test_a_branch_of_an_item_that_is_not_stopped_is_refused() -> None:
    run = Recorder(horizon_tick=simtime.TICKS_PER_SIM_DAY * 20)
    run.record(sim.assign_direct(run.state, "wi_ap_map", "stf_ap"))

    with pytest.raises(sim.CommandRejected) as refusal:
        compare.run_branch(run.state, "wi_ap_map", 0, 0, in_person=True)

    # The item's own title, not its id: a refusal is the sentence the client shows, and the
    # existing rejections all name work the way the CEO sees it named.
    assert work.spec("wi_ap_map").title in str(refusal.value)


def test_a_branch_of_an_option_that_does_not_exist_is_refused(run: Recorder) -> None:
    with pytest.raises(sim.CommandRejected):
        compare.run_branch(run.state, "wi_ap_map", 0, 99, in_person=True)
    with pytest.raises(sim.CommandRejected):
        compare.run_branch(run.state, "wi_ap_map", 9, 0, in_person=True)


def test_a_branch_of_a_run_with_no_horizon_is_refused() -> None:
    """An unbounded parent has no rule that would stop the branch.

    Refused rather than bounded by a number of this module's own choosing: a stopping rule the
    parent does not have would report a horizon the run will never reach.
    """
    run = Recorder(horizon_tick=simtime.TICKS_PER_SIM_DAY * 20)
    run.record(sim.assign_direct(run.state, "wi_ap_map", "stf_ap"))
    run.advance_until(lambda state: state.items["wi_ap_map"].status == sim.STATUS_BLOCKED)
    run.state.horizon_tick = 0

    with pytest.raises(sim.CommandRejected) as refusal:
        compare.run_branch(run.state, "wi_ap_map", 0, 0, in_person=True)

    assert "horizon" in str(refusal.value)


def test_a_branch_of_a_terminated_run_is_refused(run: Recorder) -> None:
    run.state.terminal_reason = lifecycle.TERMINAL_HORIZON

    with pytest.raises(sim.CommandRejected):
        compare.run_branch(run.state, "wi_ap_map", 0, 0, in_person=True)


# =========================================================================
# The runner is a library, and the fold never sees a branch
# =========================================================================


def test_a_branch_appends_nothing_to_the_log(run: Recorder) -> None:
    """R24, satisfied structurally: a branch is never written, so leakage is unreachable.

    The runner returns its events rather than emitting them, and the recorder is the only
    thing in this suite that writes. Nothing here can put a branch in a log by accident.
    """
    before = len(run.log)
    summary = compare.run_branch(run.state, "wi_ap_map", 0, 0, in_person=True)

    assert len(run.log) == before
    assert summary.emitted, "the branch produced no events at all, which cannot be right"
    assert folder.fold(run.log, at_live_head=True, through_tick=run.state.tick).state.tick == (
        run.state.tick
    )


# =========================================================================
# The command: one event, no state change (R21, R25, R26, R29)
# =========================================================================


def compare_at(run: Recorder, item_id: str = "wi_ap_map", **over: Any) -> list[sim.Emitted]:
    """The command as the surface sends it, defaulted to the situation the run is in."""
    item = run.state.items[item_id]
    request: dict[str, Any] = {
        "item_id": item_id,
        "cp_index": 0,
        "person_id": item.assignee,
        "at_tick": run.state.tick,
        "in_person": True,
    }
    request.update(over)
    return sim.compare_options(run.state, **request)


def test_a_comparison_produces_one_event_carrying_every_branch(run: Recorder) -> None:
    emitted = compare_at(run)

    assert len(emitted) == 1
    assert emitted[0].kind is EventKind.OPTIONS_COMPARED

    payload = emitted[0].payload
    assert payload["item"] == "wi_ap_map"
    assert payload["cp_index"] == 0
    assert payload["person"] == "stf_ap"
    assert payload["tick"] == run.state.tick
    assert len(payload["branches"]) == len(work.spec("wi_ap_map").checkpoints[0].options)
    for index, branch in enumerate(payload["branches"]):
        assert branch["option_index"] == index
        assert branch["fork_tick"] == run.state.tick


def test_a_comparison_advances_no_tick_and_changes_no_state(run: Recorder) -> None:
    """AE9, R25. The command that appends without mutating, which is the whole of it."""
    before = run.hash
    before_tick = run.state.tick

    run.record(compare_at(run))

    assert run.hash == before
    assert run.state.tick == before_tick
    assert run.state.items["wi_ap_map"].status == sim.STATUS_BLOCKED


def test_the_record_carries_what_the_client_said_it_was_looking_at(run: Recorder) -> None:
    """The tag the client sent, kept beside the tick the branches actually forked at.

    Recording only the kernel's own tick would make the record unable to say whether the two
    ever disagreed, which is exactly the question a stale comparison raises.
    """
    payload = compare_at(run, at_tick=run.state.tick - 5)[0].payload

    assert payload["requested_at_tick"] == run.state.tick - 5
    assert payload["tick"] == run.state.tick


def test_a_branch_does_not_repeat_what_the_comparison_already_says(run: Recorder) -> None:
    """The item, the checkpoint and the route are constant across every branch.

    They belong once at the top of the payload rather than up to
    `MAX_BRANCHES_PER_COMPARISON` times inside it — this is the exact payload the byte bound
    exists to hold down. They stay Python-side fields, because a summary passed around in
    process should still know which decision it is a branch of.
    """
    payload = compare_at(run)[0].payload

    for key in ("item", "cp_index", "person", "in_person"):
        assert key in payload

    for branch in payload["branches"]:
        assert "item" not in branch
        assert "cp_index" not in branch
        assert "in_person" not in branch

    # The dataclass still carries them, so the runner's own callers are unaffected.
    summary = compare.run_branch(run.state, "wi_ap_map", 0, 0, in_person=True)
    assert summary.item == "wi_ap_map"
    assert summary.cp_index == 0
    assert summary.in_person is True


def test_a_comparison_records_no_item_status(run: Recorder) -> None:
    """A comparison moves nothing, so it must not claim to.

    Every kind that carries `item_status` is a kind that moved an item, and a read-side
    consumer updates its node from that field. A comparison carrying one would be a statement
    about work that this event did not do anything to.
    """
    payload = compare_at(run)[0].payload
    assert "item_status" not in payload


# --- the guards -----------------------------------------------------------


def test_a_comparison_on_an_item_that_is_not_stopped_is_refused_and_appends_nothing() -> None:
    run = Recorder(horizon_tick=simtime.TICKS_PER_SIM_DAY * 20)
    run.record(sim.assign_direct(run.state, "wi_ap_map", "stf_ap"))
    before, before_log = run.hash, len(run.log)

    with pytest.raises(sim.CommandRejected) as refusal:
        sim.compare_options(
            run.state, "wi_ap_map", 0, "stf_ap", run.state.tick, in_person=True
        )

    assert "stale" in str(refusal.value)
    assert run.hash == before
    assert len(run.log) == before_log


def test_a_comparison_of_a_resolved_checkpoint_is_refused_as_stale(run: Recorder) -> None:
    """AE19, the first of the four ways the situation moves out from under a request."""
    at_tick = run.state.tick
    run.record(sim.resolve_checkpoint(run.state, "wi_ap_map", 0, 0, in_person=True))

    with pytest.raises(sim.CommandRejected) as refusal:
        sim.compare_options(run.state, "wi_ap_map", 0, "stf_ap", at_tick, in_person=True)
    assert "stale" in str(refusal.value)


def test_a_comparison_of_a_completed_item_is_refused_as_stale(run: Recorder) -> None:
    at_tick = run.state.tick
    run.record(sim.resolve_checkpoint(run.state, "wi_ap_map", 0, 0, in_person=True))
    run.advance_until(lambda state: state.items["wi_ap_map"].status == sim.STATUS_DONE)

    with pytest.raises(sim.CommandRejected):
        sim.compare_options(run.state, "wi_ap_map", 0, "stf_ap", at_tick, in_person=True)


def test_a_comparison_of_a_reassigned_item_is_refused_as_stale(run: Recorder) -> None:
    at_tick = run.state.tick
    # Within the reporting line, which is the only reassignment the org chart permits.
    run.record(sim.reassign(run.state, "wi_ap_map", "dir_admin"))

    with pytest.raises(sim.CommandRejected):
        sim.compare_options(run.state, "wi_ap_map", 0, "stf_ap", at_tick, in_person=True)


def test_a_comparison_whose_assignee_has_gone_is_refused_as_stale(run: Recorder) -> None:
    """The attrition case: the item goes back to the backlog with nobody on it."""
    at_tick = run.state.tick
    run.record(sim.return_to_backlog(run.state, "wi_ap_map"))

    with pytest.raises(sim.CommandRejected):
        sim.compare_options(run.state, "wi_ap_map", 0, "stf_ap", at_tick, in_person=True)


def test_a_comparison_naming_the_wrong_person_is_refused_as_stale(run: Recorder) -> None:
    """The case the status check alone does not cover.

    Reassigning takes the item out of `blocked`, work resumes, and it can reach the *same*
    checkpoint again with somebody else in front of it. The status is then blocked once more
    and the request is still describing a situation that has gone.
    """
    with pytest.raises(sim.CommandRejected) as refusal:
        sim.compare_options(
            run.state, "wi_ap_map", 0, "dir_admin", run.state.tick, in_person=True
        )

    assert "stale" in str(refusal.value)
    assert "dir_admin" in str(refusal.value)


def test_a_tag_a_little_ahead_of_the_clock_is_accepted(run: Recorder) -> None:
    """The client tags from its render clock, which legitimately runs ahead.

    The store's tick is the last one the kernel said out loud, and events land on about five
    ticks in twelve hundred — so tagging from it would put every comparison well into the run's
    past. The render clock is the smooth estimate that exists for exactly this, and it drifts
    ahead between position echoes an hour apart. Refusing anything ahead at all would reject
    nearly every real comparison while catching nothing, which is why the guard is a bound
    rather than a pin.
    """
    assert sim.compare_options(
        run.state, "wi_ap_map", 0, "stf_ap", run.state.tick + 60, in_person=True
    )


def test_a_tag_that_has_run_away_from_the_clock_is_refused(run: Recorder) -> None:
    """Not the same failure as staleness, and it says so.

    Nothing has happened at that tick yet, so there is nothing there to compare rather than
    something that has moved on. The bound is the sim-day of lead `submit_ceo_input` allows,
    which is absurdly generous against an echo interval of one sim-hour.
    """
    with pytest.raises(sim.CommandRejected) as refusal:
        sim.compare_options(
            run.state,
            "wi_ap_map",
            0,
            "stf_ap",
            run.state.tick + sim.MAX_INPUT_LEAD_TICKS + 1,
            in_person=True,
        )

    assert "ahead" in str(refusal.value)


def test_a_comparison_of_a_checkpoint_the_item_is_not_stopped_at_is_refused() -> None:
    """The closing-cycle item carries two, and it is stopped at exactly one of them."""
    run = blocked_at_the_closing_cycle()
    assert len(work.spec("wi_close").checkpoints) == 2

    with pytest.raises(sim.CommandRejected):
        sim.compare_options(run.state, "wi_close", 1, "dir_admin", run.state.tick, in_person=True)

    # And the one it *is* stopped at works, so the test is about the index rather than the item.
    assert sim.compare_options(
        run.state, "wi_close", 0, "dir_admin", run.state.tick, in_person=True
    )


def test_a_checkpoint_offering_more_options_than_the_bound_is_refused(
    run: Recorder, monkeypatch
) -> None:
    """R26. Every branch steps the whole simulation, so the bound is on what a command costs."""
    monkeypatch.setattr(sim, "MAX_BRANCHES_PER_COMPARISON", 2)

    with pytest.raises(sim.CommandRejected) as refusal:
        compare_at(run)

    assert str(sim.MAX_BRANCHES_PER_COMPARISON) in str(refusal.value)


def test_the_branch_bound_is_a_guard_and_not_a_tuning_constant() -> None:
    """R26 departs from its literal wording, and the repo's own rule is why.

    A bound on what may be submitted stays out of the tuning table; a number that changes what
    a recorded run means goes in. A comparison changes no state, so its bound cannot change
    what a run means — and putting it in `TUNING` would move the rules version, which
    invalidates every kept snapshot and makes current logs unfoldable.
    """
    assert "branch" not in " ".join(TUNING).lower()
    assert "compar" not in " ".join(TUNING).lower()
    # The rules version is derived from the tuning table, so this is the assertion that the
    # bound stayed out of it: adding it would change the string and unfold nothing.
    assert RULES_VERSION == rates.rules_version()


def test_an_over_long_comparison_payload_is_refused_rather_than_written(
    run: Recorder, monkeypatch
) -> None:
    """An append-only log cannot take a row back, so the bound is at the entry point."""
    monkeypatch.setattr(sim, "MAX_COMPARISON_PAYLOAD_BYTES", 16)
    before = len(run.log)

    with pytest.raises(sim.CommandRejected) as refusal:
        compare_at(run)

    assert "refused at the entry point" in str(refusal.value).lower()
    assert len(run.log) == before


def test_a_real_comparison_is_comfortably_inside_the_payload_bound(run: Recorder) -> None:
    """The bound is a belt, not the thing the real case is sized against."""
    encoded = len(canonical.encode(compare_at(run)[0].payload))

    assert encoded < sim.MAX_COMPARISON_PAYLOAD_BYTES
    # And large enough that the bound is guarding something rather than unreachable.
    assert encoded > 1_000


# --- the sweep ------------------------------------------------------------


def test_every_command_that_can_reject_leaves_the_state_untouched() -> None:
    """Resolve everything that can fail before mutating anything.

    A sweep rather than one assertion per command, because the mistake is made once per
    command by whoever adds the next one: a command that charges a metric and *then* looks
    something up raises past the point where `CommandRejected` is caught, leaving state
    changed with no event to explain it. A state hash that moves with nothing in the log is
    replay identity broken silently, which is the one failure this kernel exists to prevent.
    """
    rejections = [
        ("assign_via_manager", lambda s: sim.assign_via_manager(s, "wi_ai_rank")),
        ("assign_direct", lambda s: sim.assign_direct(s, "wi_ai_rank", "dir_sales")),
        ("reassign", lambda s: sim.reassign(s, "wi_quotes", "stf_ap")),
        ("return_to_backlog", lambda s: sim.return_to_backlog(s, "wi_quotes")),
        ("resolve_checkpoint", lambda s: sim.resolve_checkpoint(s, "wi_quotes", 0, 0, in_person=True)),
        ("submit_ceo_input", lambda s: sim.submit_ceo_input(s, 1, at_tick=0)),
        ("request_hire", lambda s: sim.request_hire(s, "nobody")),
        ("ask_person", lambda s: sim.ask_person(s, "stf_ap", "x" * 5_000)),
        ("compare_options", lambda s: sim.compare_options(s, "wi_quotes", 0, "stf_buyer", 0, in_person=True)),
    ]

    for name, command in rejections:
        run = blocked_at("wi_ap_map", "stf_ap")
        before = run.hash

        with pytest.raises(sim.CommandRejected):
            command(run.state)

        assert run.hash == before, f"{name} mutated state on the path to a rejection"


# --- the record, and the fold ---------------------------------------------


def test_refolding_a_log_holding_comparison_records_reproduces_the_run(run: Recorder) -> None:
    """R29. The record is on the log and the fold neither applies nor regenerates it."""
    run.record(compare_at(run))
    run.advance(600)
    # A second record after the clock has moved, so the fold has to skip more than one and at
    # more than one tick.
    run.record(compare_at(run))

    expected = run.hash
    refolded = folder.fold(
        run.log, at_live_head=False, strict=True, through_tick=run.state.tick
    )

    assert hashing.state_hash(sim.snapshot(refolded.state)).overall == expected


def test_the_fold_treats_a_comparison_as_operational(run: Recorder) -> None:
    """Not an input to re-apply, not an output to regenerate.

    Regenerating would make every replay re-run N branches at a tenth of a second each and
    prove nothing the rest of the replay does not already prove — the branches were computed
    from state the replay has just reproduced exactly. Strict mode is the assertion: it
    compares regenerated outputs against the log one for one, so a comparison classified as an
    output would fail the count immediately.
    """
    assert EventKind.OPTIONS_COMPARED in folder.OPERATIONAL_KINDS
    assert EventKind.OPTIONS_COMPARED not in folder.INPUT_KINDS
    assert EventKind.OPTIONS_COMPARED not in folder.OUTPUT_KINDS

    run.record(compare_at(run))
    run.advance(50)

    folder.fold(run.log, at_live_head=False, strict=True, through_tick=run.state.tick)


def test_a_comparison_record_survives_a_round_trip_through_the_envelope(run: Recorder) -> None:
    """Canonical encoding rejects anything that is not integers and strings.

    Worth stating for this payload in particular: a branch summary is the most deeply nested
    thing this log carries, and `Figure.value` is `None` for a runway that is not yet knowable
    — which is the one shape most likely to fall outside what canonical accepts.
    """
    emitted = compare_at(run)[0]
    envelope = build(
        seq=1,
        tick=run.state.tick,
        kind=emitted.kind,
        rules_ver=RULES_VERSION,
        payload=emitted.payload,
        run_id=RUN,
    )

    assert envelope.decoded_payload() == emitted.payload


def test_a_branch_whose_runway_is_not_yet_knowable_records_a_null(run: Recorder) -> None:
    """The `None` case, driven rather than asserted about a type.

    A branch that stops before paying a day of costs has no burn to divide by. Reporting
    infinity there would say "you have forever" at the moment the answer is unknown.
    """
    assert compare.runway_days(4800, 0) is None
    assert compare.runway_days(-1, 20) == 0

    summary = compare.run_branch(run.state, "wi_ap_map", 0, 0, in_person=True)
    # The real branch does pay costs, so its runway is a number — the null path is the one
    # above, and this is here so the two are stated together rather than in two places.
    assert summary.runway.value is not None
