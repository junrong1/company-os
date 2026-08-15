"""Branch comparison: where an option travels, without the run going there.

The load-bearing property is the first test in this file, and it is written first on purpose.
Everything else the comparison offers — trajectories, runway, what an option unlocks — is only
worth reading if running it changed nothing. R24 asks for that, and this file makes it a hash
comparison rather than a review of which lines mutate what.

The rest follows from the stop condition. A branch may not settle anything, so it runs exactly
as far as the next decision and then reports where it stopped.
"""

from __future__ import annotations

import pytest

from contracts.envelope import Envelope, EventKind, build
from simcore import compare
from simcore import hashing
from simcore import items as work
from simcore import lifecycle
from simcore import log as folder
from simcore import step as sim
from simcore import time as simtime
from simcore.rates import RULES_VERSION

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
