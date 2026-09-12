"""The ported simulation, asserted against the behaviours that make it this product.

These are the plan's U4 scenarios. Several of them are the product's central claims
rather than incidental behaviour — that work genuinely stops until the CEO decides,
that in person yields something the tray does not, and that the org chart is a
constraint — so they are asserted directly rather than through a proxy.

The parity suite in U5 ports the prototype's own 29 assertions. This file tests the
port's mechanics; that one tests agreement with what shipped.
"""

from __future__ import annotations

import json

import pytest

from contracts.envelope import EventKind
from simcore import effects
from simcore import hashing
from simcore import items as work
from simcore import people as roster
from simcore import scenario as sc
from simcore import rates
from simcore import step as sim
from simcore import time as simtime
from simcore.world import find_path, plan_floor, walkable

SEED = 0xC0FFEE

#: The shipped company. The roster and the work graph are authored in
#: `scenarios/default.toml` now, so these tests read them off the loaded scenario — which is
#: also the object the run under test carries, so there is no second copy to drift from.
SHIPPED = sc.load_default()


@pytest.fixture
def run() -> sim.State:
    state, _ = sim.new_run(run_seed=SEED)
    return state


def advance(state: sim.State, ticks: int) -> list[sim.Emitted]:
    events: list[sim.Emitted] = []
    for _ in range(ticks):
        events.extend(sim.step(state))
    return events


def run_until(state: sim.State, predicate, limit: int = 20_000) -> list[sim.Emitted]:
    """Advance until `predicate(state)` holds, collecting events."""
    events: list[sim.Emitted] = []
    for _ in range(limit):
        if predicate(state):
            return events
        events.extend(sim.step(state))
    raise AssertionError(f"predicate never held within {limit} ticks (tick {state.tick})")


# =========================================================================
# The floor and the roster
# =========================================================================


def test_every_desk_is_reachable_from_spawn(run: sim.State) -> None:
    """A desk nobody can walk to is a desk nobody works at."""
    for person_id, seat in run.seats.items():
        path = find_path(run.floor, run.floor.spawn, seat)
        assert path, f"no path from spawn to {person_id}'s desk at {seat}"
        assert path[-1] == seat or not walkable(run.floor, *seat)


def test_no_two_people_share_a_chair(run: sim.State) -> None:
    seats = list(run.seats.values())
    assert len(seats) == len(set(seats)), f"duplicate seats: {seats}"


def test_the_roster_has_four_load_bearing_reporting_lines() -> None:
    """Eight rooms, four departments. Capacity follows the line, not the room."""
    lines = SHIPPED.lines
    assert set(lines) == {"dir_sales", "dir_admin", "dir_cs", "dir_hr"}
    # The director is a member of their own line, not an overseer of it.
    for director, members in lines.items():
        assert director in members


def test_two_lines_hold_exactly_one_non_director() -> None:
    """The reason U7's capacity draw has to include the director.

    Customer Support and People each have a single specialist, so one attrition event
    would leave a department with nobody to allocate a draw across.
    """
    lines = SHIPPED.lines
    assert len(lines["dir_cs"]) == 2
    assert len(lines["dir_hr"]) == 2


def test_the_room_and_the_reporting_line_disagree_for_priya() -> None:
    """The deliberate mismatch in the sample data, and it has to survive the port."""
    priya = SHIPPED.person("stf_ap")
    assert priya.dept == "accounting"
    assert SHIPPED.reporting_line_of("stf_ap") == "dir_admin"
    assert SHIPPED.person("dir_admin").dept == "admin"


def test_a_smaller_grid_still_seats_everyone() -> None:
    """Desk spacing tightens and rows are added until everyone fits."""
    floor = plan_floor(26, 16)
    seats = roster.assign_seats(SHIPPED, floor)
    assert len(set(seats.values())) == len(SHIPPED.people)


# =========================================================================
# Work stops for the CEO
# =========================================================================


def test_work_does_not_progress_while_a_checkpoint_is_unresolved(run: sim.State) -> None:
    """The product's central claim, asserted directly."""
    sim.assign_direct(run, "wi_ap_map", "stf_ap")
    run_until(run, lambda s: s.items["wi_ap_map"].status == sim.STATUS_BLOCKED)

    item = run.items["wi_ap_map"]
    frozen = item.done_units

    advance(run, 300)

    assert item.status == sim.STATUS_BLOCKED
    assert item.done_units == frozen, "effort burned while the CEO had not decided"
    assert run.people["stf_ap"].state == sim.STATE_BLOCKED


def test_the_clock_keeps_running_while_an_item_is_stalled(run: sim.State) -> None:
    """Only the item stalls. A stopped clock is a different thing entirely."""
    sim.assign_direct(run, "wi_ap_map", "stf_ap")
    run_until(run, lambda s: s.items["wi_ap_map"].status == sim.STATUS_BLOCKED)

    before = run.tick
    advance(run, 100)
    assert run.tick == before + 100


def _raised_for(events: list[sim.Emitted], item_id: str) -> list[sim.Emitted]:
    """Every checkpoint raised for one item.

    Filtered by item rather than taking the first raise in the batch. Since M6 a run opens
    with one authored assignment already at its checkpoint, so the first `CHECKPOINT_RAISED`
    in any collected stretch belongs to the seeded item, not to the one the test assigned.
    """
    return [
        event
        for event in events
        if event.kind.name == "CHECKPOINT_RAISED" and event.payload["item"] == item_id
    ]


def test_a_checkpoint_is_raised_at_its_threshold(run: sim.State) -> None:
    sim.assign_direct(run, "wi_ap_map", "stf_ap")
    events = run_until(run, lambda s: s.items["wi_ap_map"].status == sim.STATUS_BLOCKED)

    raised = _raised_for(events, "wi_ap_map")
    assert len(raised) == 1

    payload = raised[0].payload
    spec = SHIPPED.item("wi_ap_map")
    assert payload["at_percent"] == spec.checkpoints[0].at_percent
    # Cross-multiplied threshold: done * 100 >= at * total.
    assert payload["done_units"] * 100 >= spec.checkpoints[0].at_percent * spec.effort_units


def test_a_raised_checkpoint_carries_the_line_said_only_in_person(run: sim.State) -> None:
    """R7: the conversation has to be able to show it before the CEO chooses.

    Carried on the raise rather than in the genesis catalog, so the whole script is not on
    the wire before anybody has stopped at anything.
    """
    sim.assign_direct(run, "wi_ap_map", "stf_ap")
    events = run_until(run, lambda s: s.items["wi_ap_map"].status == sim.STATUS_BLOCKED)

    raised = _raised_for(events, "wi_ap_map")
    assert raised[0].payload["tacit"] == SHIPPED.item("wi_ap_map").checkpoints[0].tacit
    assert raised[0].payload["tacit"]


def test_the_genesis_catalog_ships_no_tacit_lines() -> None:
    """R8: nothing that is only earned in person may arrive before it is earned."""
    for entry in work.catalog_to_state(SHIPPED):
        for checkpoint in entry["checkpoints"]:
            assert "tacit" not in checkpoint


def test_every_authored_checkpoint_has_a_line_worth_walking_for() -> None:
    for spec in SHIPPED.items:
        for index, checkpoint in enumerate(spec.checkpoints):
            assert checkpoint.tacit, f"{spec.id} checkpoint {index} has no tacit line"


def test_work_resumes_after_the_decision(run: sim.State) -> None:
    sim.assign_direct(run, "wi_ap_map", "stf_ap")
    run_until(run, lambda s: s.items["wi_ap_map"].status == sim.STATUS_BLOCKED)

    sim.resolve_checkpoint(run, "wi_ap_map", 0, 0, in_person=False)
    before = run.items["wi_ap_map"].done_units
    advance(run, 60)

    assert run.items["wi_ap_map"].done_units > before


# =========================================================================
# In person versus from the tray
# =========================================================================


def _block_first_checkpoint(state: sim.State) -> None:
    sim.assign_direct(state, "wi_ap_map", "stf_ap")
    run_until(state, lambda s: s.items["wi_ap_map"].status == sim.STATUS_BLOCKED)


def test_a_decision_taken_in_person_records_tacit_knowledge() -> None:
    state, _ = sim.new_run(run_seed=SEED)
    _block_first_checkpoint(state)

    events = sim.resolve_checkpoint(state, "wi_ap_map", 0, 0, in_person=True)

    decision = state.items["wi_ap_map"].decisions[0]
    assert decision.in_person is True
    assert decision.tacit, "an in-person decision must surface the tacit line"
    assert "supplier close" in decision.tacit
    assert events[0].payload["tacit_recorded"] is True


def test_the_same_decision_from_the_tray_records_none() -> None:
    state, _ = sim.new_run(run_seed=SEED)
    _block_first_checkpoint(state)

    events = sim.resolve_checkpoint(state, "wi_ap_map", 0, 0, in_person=False)

    decision = state.items["wi_ap_map"].decisions[0]
    assert decision.in_person is False
    assert decision.tacit == ""
    assert events[0].payload["tacit_recorded"] is False


def test_in_person_and_tray_differ_in_morale_and_visibility() -> None:
    """Walking over is rewarded; clearing the tray costs a little.

    Both runs take the identical option, so the only difference is the route.
    """
    in_person, _ = sim.new_run(run_seed=SEED)
    _block_first_checkpoint(in_person)
    sim.resolve_checkpoint(in_person, "wi_ap_map", 0, 0, in_person=True)

    from_tray, _ = sim.new_run(run_seed=SEED)
    _block_first_checkpoint(from_tray)
    sim.resolve_checkpoint(from_tray, "wi_ap_map", 0, 0, in_person=False)

    assert in_person.metrics["morale"] > from_tray.metrics["morale"]
    assert in_person.metrics["visibility"] > from_tray.metrics["visibility"]


def test_a_checkpoint_cannot_be_resolved_twice(run: sim.State) -> None:
    _block_first_checkpoint(run)
    sim.resolve_checkpoint(run, "wi_ap_map", 0, 0, in_person=False)

    with pytest.raises(sim.CommandRejected, match="already resolved"):
        sim.resolve_checkpoint(run, "wi_ap_map", 0, 1, in_person=False)


def test_an_unknown_option_is_refused_and_mutates_nothing(run: sim.State) -> None:
    _block_first_checkpoint(run)
    before = dict(run.metrics)

    with pytest.raises(sim.CommandRejected):
        sim.resolve_checkpoint(run, "wi_ap_map", 0, 99, in_person=False)

    assert run.metrics == before
    assert run.items["wi_ap_map"].resolved == [False]


# =========================================================================
# Deliverables and provenance
# =========================================================================


def test_a_deliverable_carries_item_assignee_and_shaping_decisions(run: sim.State) -> None:
    _block_first_checkpoint(run)
    sim.resolve_checkpoint(run, "wi_ap_map", 0, 0, in_person=True)
    run_until(run, lambda s: s.items["wi_ap_map"].status == sim.STATUS_DONE)

    assert len(run.outputs) == 1
    output = run.outputs[0]

    assert output.item_id == "wi_ap_map"
    assert output.title == SHIPPED.item("wi_ap_map").output_title
    assert any("Priya Raman" in line for line in output.provenance)
    assert any("CEO decision: PDF is the record" in line for line in output.provenance)
    assert output.tacit, "an in-person decision leaves its tacit line on the deliverable"


def test_a_tray_only_deliverable_carries_no_tacit_line(run: sim.State) -> None:
    _block_first_checkpoint(run)
    sim.resolve_checkpoint(run, "wi_ap_map", 0, 0, in_person=False)
    run_until(run, lambda s: s.items["wi_ap_map"].status == sim.STATUS_DONE)

    assert run.outputs[0].tacit == ()
    assert any("from the tray" in line for line in run.outputs[0].provenance)


def test_completing_an_item_applies_its_effect(run: sim.State) -> None:
    before = run.metrics["visibility"]
    _block_first_checkpoint(run)
    sim.resolve_checkpoint(run, "wi_ap_map", 0, 1, in_person=False)
    run_until(run, lambda s: s.items["wi_ap_map"].status == sim.STATUS_DONE)

    # +3 from the chosen option, -1 morale side effect, +10 on completion.
    assert run.metrics["visibility"] == before + 3 + 10


# =========================================================================
# Unlock gates
# =========================================================================


def test_an_item_gated_on_a_prerequisite_stays_unavailable(run: sim.State) -> None:
    assert not sim.is_unlocked(run, "wi_ap_auto")
    assert "Map how accounts payable" in sim.lock_reason(run, "wi_ap_auto")

    with pytest.raises(sim.CommandRejected):
        sim.assign_direct(run, "wi_ap_auto", "stf_ap")

    assert run.items["wi_ap_auto"].status == sim.STATUS_BACKLOG


def test_clearing_the_prerequisite_unlocks_it(run: sim.State) -> None:
    _block_first_checkpoint(run)
    sim.resolve_checkpoint(run, "wi_ap_map", 0, 0, in_person=False)
    events = run_until(run, lambda s: s.items["wi_ap_map"].status == sim.STATUS_DONE)

    assert sim.is_unlocked(run, "wi_ap_auto")
    unlocked = [e for e in events if e.kind.name == "ITEM_UNLOCKED"]
    assert any(e.payload["item"] == "wi_ap_auto" for e in unlocked)


def test_an_item_gated_on_visibility_stays_unavailable(run: sim.State) -> None:
    assert run.metrics["visibility"] < 24
    assert not sim.is_unlocked(run, "wi_close")
    assert "24% visibility" in sim.lock_reason(run, "wi_close")


def test_raising_visibility_unlocks_the_gated_item(run: sim.State) -> None:
    run.metrics["visibility"] = 24
    assert sim.is_unlocked(run, "wi_close")


# =========================================================================
# The reporting line
# =========================================================================


def test_assignment_through_the_line_makes_the_director_walk_over(run: sim.State) -> None:
    events = sim.assign_via_manager(run, "wi_ap_map")

    assert events[0].payload["via"] == "dir_admin"
    grace = run.people["dir_admin"]
    assert grace.state == sim.STATE_WALKING
    assert grace.path, "the hand-off is physical, so there has to be a path"
    assert grace.arrive == sim.ARRIVE_HANDOFF


def test_the_specialist_starts_only_once_the_director_arrives(run: sim.State) -> None:
    sim.assign_via_manager(run, "wi_ap_map")

    advance(run, 5)
    assert run.items["wi_ap_map"].done_units == 0, "work started before the hand-off landed"

    run_until(run, lambda s: s.people["stf_ap"].state == sim.STATE_WORKING)
    assert run.items["wi_ap_map"].status == sim.STATUS_ACTIVE


def test_the_director_returns_to_their_own_desk_after_handing_off(run: sim.State) -> None:
    sim.assign_via_manager(run, "wi_ap_map")
    run_until(run, lambda s: s.people["dir_admin"].state == sim.STATE_IDLE)

    grace = run.people["dir_admin"]
    assert grace.pos == grace.seat
    assert grace.item_id == ""


def test_bypassing_a_director_costs_morale_and_records_them_as_not_knowing(
    run: sim.State,
) -> None:
    before = run.metrics["morale"]
    events = sim.assign_direct(run, "wi_ap_map", "stf_ap")

    assert run.metrics["morale"] == before - 3
    payload = events[0].payload
    assert payload["bypassed_director"] is True
    assert payload["uninformed"] == ["dir_admin"]
    assert run.people["stf_ap"].bypassed_director is True


def test_the_uninformed_director_is_carried_onto_the_decision(run: sim.State) -> None:
    """What the report needs: which directors were left uninformed, per decision."""
    _block_first_checkpoint(run)
    events = sim.resolve_checkpoint(run, "wi_ap_map", 0, 0, in_person=True)

    assert events[0].payload["uninformed"] == ["dir_admin"]
    assert run.items["wi_ap_map"].decisions[0].uninformed == ("dir_admin",)


def test_assigning_a_director_their_own_item_needs_no_hand_off(run: sim.State) -> None:
    """wi_close wants the director. There is nobody to route through."""
    run.metrics["visibility"] = 24
    events = sim.assign_via_manager(run, "wi_close")

    assert events[0].payload["person"] == "dir_admin"
    assert events[0].payload["bypassed_director"] is False
    assert run.metrics["morale"] == 72, "a director has no director to bypass"


# =========================================================================
# Reassignment
# =========================================================================


def test_reassignment_within_a_reporting_line_retains_burned_effort(run: sim.State) -> None:
    sim.assign_direct(run, "wi_quotes", "stf_buyer")
    advance(run, 120)

    burned = run.items["wi_quotes"].done_units
    assert burned > 0

    events = sim.reassign(run, "wi_quotes", "dir_admin")

    assert run.items["wi_quotes"].assignee == "dir_admin"
    assert run.items["wi_quotes"].done_units == burned
    assert events[0].payload["retained_units"] == burned


def test_reassignment_across_reporting_lines_is_rejected_and_mutates_nothing(
    run: sim.State,
) -> None:
    """The org chart is a constraint, not a suggestion."""
    sim.assign_direct(run, "wi_quotes", "stf_buyer")
    advance(run, 120)

    before_assignee = run.items["wi_quotes"].assignee
    before_units = run.items["wi_quotes"].done_units
    before_metrics = dict(run.metrics)

    with pytest.raises(sim.CommandRejected, match="different reporting line"):
        sim.reassign(run, "wi_quotes", "stf_cs")

    assert run.items["wi_quotes"].assignee == before_assignee
    assert run.items["wi_quotes"].done_units == before_units
    assert run.metrics == before_metrics
    assert run.people["stf_cs"].item_id == ""


def test_reassigning_something_not_in_flight_is_rejected(run: sim.State) -> None:
    with pytest.raises(sim.CommandRejected, match="nothing in flight"):
        sim.reassign(run, "wi_quotes", "stf_buyer")


# =========================================================================
# Directors carry work more slowly
# =========================================================================


def test_a_director_burns_effort_more_slowly_than_a_specialist(run: sim.State) -> None:
    """The prototype's `p.rank === 'director' ? 0.8 : 1`, as an exact 4/5.

    **Changed by U7, deliberately.** This asserted a flat `10 * 48` units, because a tick was
    60 units and a director's was 60*4/5. Since U7 the baseline draw comes off available hours
    before queue effort burns (R47), so the 60 is now 60 minus that person's share of their
    department's draw, and the director rate applies to what is left. The *relationship* the
    assertion was protecting — a director carries work more slowly — is what is asserted now,
    so the test survives future retuning of the draws.
    """
    run.metrics["visibility"] = 24
    sim.assign_via_manager(run, "wi_close")
    run_until(run, lambda s: s.people["dir_admin"].state == sim.STATE_WORKING)

    director_burn = sim._burn_this_tick(run, run.people["dir_admin"], run.items["wi_close"])
    specialist_burn = sim._burn_this_tick(run, run.people["stf_buyer"], run.items["wi_close"])

    assert director_burn < specialist_burn
    # Still exactly 4/5 of the specialist's, on the same department's draw share.
    assert director_burn == specialist_burn * 4 // 5


def test_the_baseline_draw_comes_off_before_queue_effort(run: sim.State) -> None:
    """R47, and the reason the previous test's absolute numbers moved.

    dir_admin draws 120 h/mo across three members: 21600 units a day, 7200 each, 13 a tick.
    So a specialist in that line puts 47 of their 60 units into the queue.
    """
    sim.assign_direct(run, "wi_ap_map", "stf_ap")
    run_until(run, lambda s: s.people["stf_ap"].state == sim.STATE_WORKING)

    before = run.items["wi_ap_map"].done_units
    advance(run, 10)
    burned = run.items["wi_ap_map"].done_units - before

    assert burned == 10 * 47
    assert burned < 10 * work.EFFORT_UNITS_PER_TICK, "the draw is not being deducted"


# =========================================================================
# The cross-department meeting
# =========================================================================


def test_cross_department_work_goes_to_the_meeting_room(run: sim.State) -> None:
    sim.assign_direct(run, "wi_dup_entry", "stf_order")
    run_until(run, lambda s: s.people["stf_order"].state == sim.STATE_MEETING)

    dana = run.people["stf_order"]
    meeting = run.floor.room("meeting")
    assert meeting.x1 <= dana.pos[0] <= meeting.x2
    assert run.items["wi_dup_entry"].visited is True


def test_the_meeting_lasts_two_sim_hours_then_work_resumes(run: sim.State) -> None:
    sim.assign_direct(run, "wi_dup_entry", "stf_order")
    run_until(run, lambda s: s.people["stf_order"].state == sim.STATE_MEETING)

    frozen = run.items["wi_dup_entry"].done_units
    advance(run, work.MEETING_SIM_HOURS * simtime.TICKS_PER_SIM_HOUR - 1)
    assert run.people["stf_order"].state == sim.STATE_MEETING
    assert run.items["wi_dup_entry"].done_units == frozen, "work progressed during a meeting"

    run_until(run, lambda s: s.people["stf_order"].state == sim.STATE_WORKING)
    assert run.items["wi_dup_entry"].visited is True


def test_the_meeting_happens_only_once(run: sim.State) -> None:
    sim.assign_direct(run, "wi_dup_entry", "stf_order")
    run_until(run, lambda s: s.people["stf_order"].state == sim.STATE_MEETING)
    run_until(run, lambda s: s.people["stf_order"].state == sim.STATE_WORKING)

    meetings = 0
    for _ in range(2000):
        sim.step(run)
        if run.people["stf_order"].state == sim.STATE_MEETING:
            meetings += 1
    assert meetings == 0


# =========================================================================
# Movement on the wire (M62, R15)
# =========================================================================


def _moves(events: list[sim.Emitted], person_id: str = "") -> list[sim.Emitted]:
    """Every movement event, optionally for one person.

    Filtered by person rather than taken positionally, following `_raised_for` above: a run
    opens with an authored assignment in flight, so a collected stretch of a day holds walks
    the test did not ask for — the seeded assignee going to a meeting, a director walking home.
    """
    return [
        event
        for event in events
        if event.kind is EventKind.STAFF_MOVED
        and (person_id == "" or event.payload["person"] == person_id)
    ]


def test_a_walk_across_the_floor_is_one_event_carrying_the_whole_path(run: sim.State) -> None:
    """Covers M62. The event the client draws delegation from.

    One event, carrying person, path and start tick — not one per tick. Position is a function
    of `tick - start_tick`, so the intermediate positions are the client's to derive.
    """
    events = sim.assign_via_manager(run, "wi_ap_map")

    moved = _moves(events)
    assert len(moved) == 1, f"one walk, one event: {[e.kind.name for e in events]}"

    payload = moved[0].payload
    grace = run.people["dir_admin"]
    assert payload["person"] == "dir_admin"
    assert payload["from"] == list(grace.pos)
    assert payload["path"] == [list(tile) for tile in grace.path]
    assert payload["start_tick"] == grace.path_start_tick == run.tick
    assert payload["item"] == "wi_ap_map", "the org chart's progress row reads this"
    assert payload["then"] == sim.STATE_WALKING, "a hand-off ends in the walk home"
    assert payload["path"], "the hand-off is physical, so there has to be a path"


def test_walking_emits_nothing_per_tick(run: sim.State) -> None:
    """The reason the path is on the event at all.

    A per-tick position event would be roughly 36 rows per walker per wall second in an
    append-only log, to say what the path and the start tick already say.
    """
    sim.assign_via_manager(run, "wi_ap_map")
    grace = run.people["dir_admin"]
    start = grace.pos

    during = run_until(run, lambda s: s.people["dir_admin"].pos != start)
    assert _moves(during, "dir_admin") == [], "a tick of walking announced itself"

    # And the walker did move, so the silence is not the walk failing to start.
    assert run.people["dir_admin"].pos != start


def test_the_whole_walk_produces_exactly_one_event_per_leg(run: sim.State) -> None:
    """A hand-off is two legs: over to the desk, then home again.

    Two events for the round trip and nothing in between, which is what a client needs to draw
    a director crossing the floor twice.
    """
    sim.assign_via_manager(run, "wi_ap_map")
    home = run_until(run, lambda s: s.people["dir_admin"].state == sim.STATE_IDLE)

    walks_home = _moves(home, "dir_admin")
    assert len(walks_home) == 1
    assert walks_home[0].payload["then"] == sim.STATE_IDLE
    assert walks_home[0].payload["item"] == "", "the work was handed over on arrival"
    assert walks_home[0].payload["path"][-1] == list(run.people["dir_admin"].seat)


def test_the_interpolated_position_agrees_with_the_kernel_at_every_tile(run: sim.State) -> None:
    """The client interpolates; the kernel snaps. They must agree where both have an answer.

    `walk_position_milli` is only ever evaluated on the client, so this is the check that it
    describes the same walk `_advance_walker` does: on the tick a whole tile is completed the
    two must name the same tile, and between them the client is allowed to be part-way.
    """
    sim.assign_via_manager(run, "wi_ap_map")
    grace = run.people["dir_admin"]
    origin, path, started = grace.pos, grace.path, grace.path_start_tick

    milli = simtime.MILLI_TILES_PER_TILE
    checked = 0
    for _ in range(simtime.walk_duration_ticks(len(path)) + 2):
        sim.step(run)
        elapsed = run.tick - started
        x_milli, y_milli = simtime.walk_position_milli(origin, path, elapsed)

        if x_milli % milli == 0 and y_milli % milli == 0:
            # A whole tile: the client is standing exactly where the kernel put the walker.
            assert (x_milli // milli, y_milli // milli) == grace.pos
            checked += 1
        else:
            # Between tiles: within one tile of the kernel's answer, never further.
            assert abs(x_milli - grace.pos[0] * milli) <= milli
            assert abs(y_milli - grace.pos[1] * milli) <= milli

    assert checked > 1, "no tile boundary was reached, so nothing was compared"


def test_a_reassigned_walk_emits_a_fresh_path_and_abandons_the_old_one(run: sim.State) -> None:
    """The walk is interrupted, and the client is told which path to draw instead.

    The interruption that actually happens: the director is half way across the floor carrying
    work to a specialist when the CEO gives it to the director themselves. The hand-off walk is
    abandoned mid-floor and a new one home replaces it — so a client holding only the first path
    would draw the director walking to a desk they never reach.
    """
    sim.assign_via_manager(run, "wi_ap_map")
    grace = run.people["dir_admin"]
    abandoned = grace.path
    advance(run, 40)
    assert grace.state == sim.STATE_WALKING and grace.pos != grace.seat, "sanity: mid-walk"

    events = sim.reassign(run, "wi_ap_map", "dir_admin")
    moved = _moves(events)

    assert len(moved) == 1, "one walk begins, so one event"
    payload = moved[0].payload
    assert payload["person"] == "dir_admin"
    assert payload["path"] != [list(tile) for tile in abandoned], "the old path is superseded"
    assert payload["path"] == [list(tile) for tile in grace.path]
    assert payload["from"] == list(grace.pos), "the new walk starts where the old one stopped"
    assert payload["start_tick"] == run.tick, "and it starts now, not when the first one did"
    assert payload["then"] == sim.STATE_WORKING
    # The specialist the work was walking towards has nothing to wait for any more.
    assert run.people["stf_ap"].state == sim.STATE_IDLE


def test_every_walk_says_what_it_ends_in(run: sim.State) -> None:
    """`then` is the kernel's answer, so no client has to map the arrival intent itself.

    Asserted over two days of a busy floor rather than per intent, because the value of the
    field is that it is present on every walk — and over two days the floor produces all four
    arrivals: a hand-off, the walk home, a meeting and the return to work.
    """
    walks = _moves(sim.assign_via_manager(run, "wi_ap_map"))
    walks += _moves(sim.assign_direct(run, "wi_dup_entry", "stf_order"))
    walks += _moves(advance(run, simtime.TICKS_PER_SIM_DAY * 2))

    legal = {sim.STATE_IDLE, sim.STATE_WORKING, sim.STATE_MEETING, sim.STATE_WALKING}
    assert {walk.payload["then"] for walk in walks} == legal, (
        "two days should exercise every arrival: "
        f"{[(w.payload['person'], w.payload['then']) for w in walks]}"
    )
    assert set(sim.ARRIVE_STATE.values()) == legal, (
        "the table gained a state the client is not told about"
    )


def test_the_meeting_walk_and_the_walk_back_are_both_announced(run: sim.State) -> None:
    """Cross-department work walks to the meeting room and back, so both legs are events."""
    sim.assign_direct(run, "wi_dup_entry", "stf_order")
    to_meeting = run_until(run, lambda s: s.people["stf_order"].state == sim.STATE_MEETING)
    assert len(_moves(to_meeting, "stf_order")) == 1
    assert _moves(to_meeting, "stf_order")[0].payload["then"] == sim.STATE_MEETING

    back = run_until(run, lambda s: s.people["stf_order"].state == sim.STATE_WORKING)
    assert len(_moves(back, "stf_order")) == 1
    assert _moves(back, "stf_order")[0].payload["then"] == sim.STATE_WORKING


def test_genesis_walks_nobody(run: sim.State) -> None:
    """The seed puts people at their desks, so day zero holds no movement event.

    `new_run` returns GENESIS alone and the fold rebuilds genesis by calling it, so a movement
    event here would regenerate nowhere. `_seed_authored_work` refuses rather than dropping it,
    and this is the check that the refusal is unreachable on the shipped scenario.
    """
    _, emitted = sim.new_run(run_seed=SEED)
    assert [event.kind for event in emitted] == [EventKind.GENESIS]

    for seeded in SHIPPED.seeded:
        person = run.people[seeded.person_id]
        assert person.pos == person.seat, f"{seeded.person_id} is seeded away from their desk"


# =========================================================================
# The CEO, derived from logged input
# =========================================================================


def test_a_logged_input_bitmask_reproduces_the_same_position_track() -> None:
    """R12: the client's prediction is right because the arithmetic is identical.

    Two fresh runs fed the same logged inputs must produce the same track, tick for
    tick. This is the property that makes reconciliation never fire in practice.
    """

    def track(state: sim.State) -> list[tuple[int, int, int]]:
        out = []
        for offset, mask in enumerate((sim.INPUT_RIGHT, sim.INPUT_RIGHT, sim.INPUT_DOWN)):
            sim.submit_ceo_input(state, mask, at_tick=state.tick + 1 + offset * 0)
            sim.step(state)
            out.append((state.tick, state.ceo.x_milli, state.ceo.y_milli))
        for _ in range(30):
            sim.submit_ceo_input(state, sim.INPUT_RIGHT, at_tick=state.tick + 1)
            sim.step(state)
            out.append((state.tick, state.ceo.x_milli, state.ceo.y_milli))
        return out

    first, _ = sim.new_run(run_seed=SEED)
    second, _ = sim.new_run(run_seed=SEED)

    assert track(first) == track(second)


def test_the_ceo_starts_at_spawn(run: sim.State) -> None:
    assert run.ceo.tile == run.floor.spawn


def test_a_held_direction_moves_the_ceo_by_the_integer_step(run: sim.State) -> None:
    start = run.ceo.x_milli
    sim.submit_ceo_input(run, sim.INPUT_RIGHT, at_tick=run.tick + 1)
    sim.step(run)

    assert run.ceo.x_milli == start + sim.CEO_STRAIGHT_MILLI_PER_TICK
    assert run.ceo.facing == "right"


def test_diagonal_movement_uses_the_normalised_step(run: sim.State) -> None:
    """Not 144 on both axes: that would make diagonals faster than straight lines."""
    start = (run.ceo.x_milli, run.ceo.y_milli)
    sim.submit_ceo_input(run, sim.INPUT_RIGHT | sim.INPUT_DOWN, at_tick=run.tick + 1)
    sim.step(run)

    assert run.ceo.x_milli - start[0] == sim.CEO_DIAGONAL_MILLI_PER_TICK
    assert sim.CEO_DIAGONAL_MILLI_PER_TICK < sim.CEO_STRAIGHT_MILLI_PER_TICK


def test_opposed_directions_cancel(run: sim.State) -> None:
    before = (run.ceo.x_milli, run.ceo.y_milli)
    sim.submit_ceo_input(run, sim.INPUT_LEFT | sim.INPUT_RIGHT, at_tick=run.tick + 1)
    sim.step(run)

    assert (run.ceo.x_milli, run.ceo.y_milli) == before


def test_the_ceo_cannot_walk_through_a_wall(run: sim.State) -> None:
    for _ in range(400):
        sim.submit_ceo_input(run, sim.INPUT_UP, at_tick=run.tick + 1)
        sim.step(run)

    assert walkable(run.floor, *run.ceo.tile)


def test_a_held_direction_keeps_moving_until_it_is_superseded(run: sim.State) -> None:
    """R2: held means held.

    The client sends one command per *change* of held direction — walking is run-length
    encoded, so roughly 36 rows a second of movement becomes one row a keypress. Applying a
    logged mask only on the exact tick it was tagged for therefore moves the CEO a seventh
    of a tile per keypress and then stops, which is a CEO who cannot cross the floor.
    """
    # Left of spawn is open corridor. Asserted rather than assumed, so a floor change fails
    # here saying what it broke instead of looking like a movement regression.
    x, y = run.ceo.tile
    assert walkable(run.floor, x - 1, y)

    start = run.ceo.x_milli
    sim.submit_ceo_input(run, sim.INPUT_LEFT, at_tick=run.tick + 1)

    advance(run, 10)

    assert start - run.ceo.x_milli == 10 * sim.CEO_STRAIGHT_MILLI_PER_TICK


def test_releasing_a_direction_stops_the_ceo(run: sim.State) -> None:
    sim.submit_ceo_input(run, sim.INPUT_LEFT, at_tick=run.tick + 1)
    advance(run, 5)
    stopped_at = run.ceo.x_milli
    assert stopped_at < run.floor.spawn[0] * 1000

    sim.submit_ceo_input(run, 0, at_tick=run.tick + 1)
    advance(run, 20)

    assert run.ceo.x_milli == stopped_at


def test_a_superseded_input_is_dropped_rather_than_accumulating(run: sim.State) -> None:
    """The held mask is hashed state, so an unpruned dict grows for the length of a run."""
    for _ in range(50):
        sim.submit_ceo_input(run, sim.INPUT_RIGHT, at_tick=run.tick + 1)
        sim.step(run)

    # One held input, plus anything still scheduled ahead of the current tick.
    assert len(run.ceo_inputs) == 1


def test_an_input_scheduled_ahead_does_not_apply_early(run: sim.State) -> None:
    """The client tags inputs a few ticks ahead so both sides apply them at the same tick."""
    start = run.ceo.x_milli
    sim.submit_ceo_input(run, sim.INPUT_LEFT, at_tick=run.tick + 6)

    advance(run, 5)
    assert run.ceo.x_milli == start

    advance(run, 1)
    assert start - run.ceo.x_milli == sim.CEO_STRAIGHT_MILLI_PER_TICK


def test_an_input_for_a_past_tick_is_rejected(run: sim.State) -> None:
    advance(run, 5)
    with pytest.raises(sim.CommandRejected, match="not in the future"):
        sim.submit_ceo_input(run, sim.INPUT_RIGHT, at_tick=run.tick)


def test_an_unknown_input_bit_is_rejected(run: sim.State) -> None:
    with pytest.raises(sim.CommandRejected, match="outside the known set"):
        sim.submit_ceo_input(run, 0b10000, at_tick=run.tick + 1)


# =========================================================================
# A full sim-day, headless
# =========================================================================


def test_the_kernel_advances_a_full_sim_day(run: sim.State) -> None:
    """U4's verification, minus the import check, which lives in the boundary suite.

    **Changed by U7, deliberately.** The daily burn was the prototype's flat 18. Since U7 a
    department's recurring draw is staffed work and carries into the burn (R60), and arrived
    hires add a salary (R24). Asserting the components rather than one total keeps the test
    readable when the draws are retuned, and makes it obvious which term moved.
    """
    sim.assign_via_manager(run, "wi_ap_map")
    advance(run, simtime.TICKS_PER_SIM_DAY)

    assert run.tick == simtime.TICKS_PER_SIM_DAY
    assert simtime.day_of(run.tick) == 2

    fixed = 18
    draw_cost = 340 * 5 // 100  # 340 h/mo of authored draw, at 5 hundredths of $K each
    assert run.metrics["cash"] == 4800 - (fixed + draw_cost)


def test_the_walk_and_the_work_account_for_every_tick(run: sim.State) -> None:
    """Hand-computed, so the test would catch a change in the arithmetic.

    Grace walks 13 tiles from her desk to Priya's, which is ceil(13 * 90 / 7) = 168 ticks.
    Priya then works the remaining 372 ticks of the day.

    **Changed by U7, deliberately.** Each of those ticks used to contribute 60 units; it now
    contributes 47, because 13 go to her department's baseline draw first (R47).
    """
    sim.assign_via_manager(run, "wi_ap_map")
    advance(run, simtime.TICKS_PER_SIM_DAY)

    walk_ticks = simtime.walk_duration_ticks(13)
    assert walk_ticks == 168
    assert run.items["wi_ap_map"].done_units == (simtime.TICKS_PER_SIM_DAY - walk_ticks) * 47


def test_a_full_day_hashes_identically_from_two_identical_runs() -> None:
    """Seed determinism: two fresh runs from one seed produce one run."""

    def play() -> str:
        state, _ = sim.new_run(run_seed=SEED)
        sim.assign_via_manager(state, "wi_ap_map")
        advance(state, simtime.TICKS_PER_SIM_DAY)
        return hashing.state_hash(sim.snapshot(state)).overall

    assert play() == play()


def test_the_snapshot_covers_every_declared_subsystem(run: sim.State) -> None:
    """**Changed by U7, deliberately.**

    `capacity` and `morale` shipped empty at U3–U6 precisely so that U7 filling them would be
    a visible change rather than a silent hash break. This is that change: they now carry
    content, `hiring` stays empty until a hire is requested, and `lifecycle` waits for U8.
    The state-shape version is unchanged because the subsystem *list* did not change — which
    is exactly the distinction it exists to draw.
    """
    advance(run, 10)
    snapshot = sim.snapshot(run)

    assert set(snapshot) == set(hashing.SUBSYSTEMS)
    assert set(snapshot["capacity"]) == {"dir_admin", "dir_sales", "dir_cs", "dir_hr"}
    assert len(snapshot["morale"]) == len(SHIPPED.people)
    assert snapshot["hiring"] == {}, "no hire has been requested"
    # **Changed again by the MVP's U15**, which is the other half of the same distinction: U7 filled
    # two declared subsystems and moved nothing, and U15 *declared* one — what the CEO was asked to
    # allow — so the version moves even though a run that never asks carries an empty table.
    assert snapshot["authorization"] == {}, "nobody has asked to read another line"
    assert hashing.STATE_SHAPE_VERSION == 2, "the subsystem list changed at U15, so this moved"


def test_the_snapshot_is_canonical_and_free_of_floats(run: sim.State) -> None:
    from contracts import canonical

    sim.assign_via_manager(run, "wi_ap_map")
    advance(run, 200)
    canonical.validate(sim.snapshot(run))


def test_every_emitted_payload_is_canonical(run: sim.State) -> None:
    """Anything that reaches the log has to be integer-only and hashable."""
    from contracts import canonical

    state, genesis = sim.new_run(run_seed=SEED)
    events = list(genesis)
    events.extend(sim.assign_via_manager(state, "wi_ap_map"))
    events.extend(advance(state, simtime.TICKS_PER_SIM_DAY))
    state.metrics["visibility"] = 24
    events.extend(sim.assign_direct(state, "wi_quotes", "stf_buyer"))
    events.extend(advance(state, 400))

    assert len(events) > 3
    for event in events:
        canonical.validate(event.payload)


# =========================================================================
# The catalog the genesis event carries (U13/U14)
# =========================================================================


def test_genesis_carries_the_whole_work_graph() -> None:
    """Every item and every dependency edge, before anything unlocks.

    The DAG layers by longest-path depth over the whole graph. A client told about an
    item only when it became available would re-layer on every unlock, which is the one
    thing that layout exists to prevent.
    """
    _, genesis = sim.new_run(run_seed=SEED)
    catalog = genesis[0].payload["catalog"]

    assert [entry["id"] for entry in catalog] == [item.id for item in SHIPPED.items]

    edges = {
        (required, entry["id"])
        for entry in catalog
        for required in entry["requires"]["items"]
    }
    assert ("wi_ap_map", "wi_ap_auto") in edges, "the authored edge has to be on the wire"

    by_id = {entry["id"]: entry for entry in catalog}
    for item in SHIPPED.items:
        # Every id an edge names has to resolve, or the layout has a dangling layer.
        for required in by_id[item.id]["requires"]["items"]:
            assert required in by_id
        for unlocked in by_id[item.id]["unlocks"]:
            assert unlocked in by_id


def test_the_catalog_withholds_the_tacit_line() -> None:
    """The line only walking over surfaces is not shipped at genesis.

    This is the product's central mechanic rather than a privacy nicety: a client holding
    every tacit line at genesis could render the whole of what conversation is for, and
    clearing the tray would stop costing anything.
    """
    _, genesis = sim.new_run(run_seed=SEED)
    catalog = genesis[0].payload["catalog"]

    authored = {
        checkpoint.tacit
        for item in SHIPPED.items
        for checkpoint in item.checkpoints
        if checkpoint.tacit
    }
    assert authored, "the fixture would prove nothing if no item authored a tacit line"

    serialised = json.dumps(catalog)
    for line in authored:
        assert line not in serialised

    for entry in catalog:
        for checkpoint in entry["checkpoints"]:
            assert "tacit" not in checkpoint
            # The option's arithmetic *is* shipped now (R35), and the tacit line still is
            # not. Pinned as an exact key set so that reversing one withholding cannot
            # quietly carry the other along with it.
            for option in checkpoint["options"]:
                assert set(option) == {"label", "detail", "effect", "draw_delta", "note"}


def test_each_catalog_option_carries_its_authored_consequence() -> None:
    """R35, AE20: what an option costs reaches the decision surface.

    The reversal of a deliberate withholding, and the docstring on `catalog_to_state`
    records why. What makes it defensible is the marking: the figure is visible and
    labelled as authored tuning rather than hidden and imagined.
    """
    _, genesis = sim.new_run(run_seed=SEED)
    by_id = {entry["id"]: entry for entry in genesis[0].payload["catalog"]}

    for item in SHIPPED.items:
        for cp_index, checkpoint in enumerate(item.checkpoints):
            shipped = by_id[item.id]["checkpoints"][cp_index]["options"]
            assert len(shipped) == len(checkpoint.options)
            for option, authored in zip(shipped, checkpoint.options, strict=True):
                metric_effect, draw = effects.split_draw(authored.effect)
                assert option["effect"] == metric_effect
                assert option["draw_delta"] == draw
                # The sentence the deliverable's provenance records — the one that
                # survives the run.
                assert option["note"] == authored.note


def test_the_draw_key_never_reaches_the_client_as_a_metric_delta() -> None:
    """R49: `manualHours` is the sum of the department draws, not an authored delta.

    An option carrying the recurring-draw pseudo-key inside `effect` would render as a
    metric movement no metric makes. It is split into `draw_delta`, which the department
    the entry already names owns.
    """
    _, genesis = sim.new_run(run_seed=SEED)
    catalog = genesis[0].payload["catalog"]

    with_draw = [
        (entry["id"], option)
        for entry in catalog
        for checkpoint in entry["checkpoints"]
        for option in checkpoint["options"]
        if option["draw_delta"] != 0
    ]
    assert with_draw, "the fixture would prove nothing if no option moved a draw"

    for entry in catalog:
        for checkpoint in entry["checkpoints"]:
            for option in checkpoint["options"]:
                assert effects.DRAW_KEY not in option["effect"]
                assert set(option["effect"]) <= set(effects.METRIC_KEYS)


def test_an_option_that_costs_nothing_ships_an_empty_effect() -> None:
    """The approval checkpoint on the closing-cycle item authors `{}`.

    Shipped as an empty map rather than as zeros for every metric, so the client can render
    no figures at all instead of a row of zeros that reads as a measurement.
    """
    _, genesis = sim.new_run(run_seed=SEED)
    by_id = {entry["id"]: entry for entry in genesis[0].payload["catalog"]}

    unchanged = by_id["wi_close"]["checkpoints"][1]["options"][1]
    assert unchanged["label"] == "Leave it at $10K"
    assert unchanged["effect"] == {}
    assert unchanged["draw_delta"] == 0
    assert unchanged["note"]


def test_catalog_copy_arrives_rendered() -> None:
    """No `{hours}` template reaches the client.

    Filling the template client-side would be a second implementation of authored copy in
    a second language, with no golden vector cheap enough to guard it.
    """
    _, genesis = sim.new_run(run_seed=SEED)
    catalog = genesis[0].payload["catalog"]

    # Only prompts are templated today. Asserted rather than assumed, so this test keeps
    # proving something if a templated brief is authored later.
    templated = [
        (item.id, cp_index)
        for item in SHIPPED.items
        for cp_index, checkpoint in enumerate(item.checkpoints)
        if "{hours" in checkpoint.prompt
    ]
    assert templated, "the fixture would prove nothing without a templated prompt"

    serialised = json.dumps(catalog)
    assert "{hours}" not in serialised
    assert "{hours_word}" not in serialised
    assert "{Hours_word}" not in serialised

    by_id = {entry["id"]: entry for entry in catalog}
    for item_id, cp_index in templated:
        item = SHIPPED.item(item_id)
        rendered = by_id[item_id]["checkpoints"][cp_index]["prompt"]
        assert rendered == work.rendered_prompt(item, cp_index)
        assert rendered != item.checkpoints[cp_index].prompt

    for entry in catalog:
        assert entry["brief"] == work.rendered_brief(SHIPPED.item(entry["id"]))


def test_catalog_names_the_department_whose_load_the_work_sits_in() -> None:
    """The DAG's load tint reads `director`, and it is derived rather than authored."""
    _, genesis = sim.new_run(run_seed=SEED)
    catalog = genesis[0].payload["catalog"]

    for entry in catalog:
        item = SHIPPED.item(entry["id"])
        assert entry["director"] == work.director_for(SHIPPED, item)
        assert entry["director"] == SHIPPED.reporting_line_of(item.want)


def test_genesis_carries_the_metric_table_with_its_favourable_directions() -> None:
    """The HUD reads `good` rather than re-deriving it.

    Spending cash and cutting hours are both negative numbers. A client applying a uniform
    rising-is-good rule would render the automation gain — the point of a run — as a
    regression, and there is no golden vector cheap enough to guard a second copy of this
    table in TypeScript.
    """
    from simcore import capacity as cap
    from simcore import effects

    _, genesis = sim.new_run(run_seed=SEED)
    payload = genesis[0].payload

    defs = {entry["key"]: entry for entry in payload["metric_defs"]}
    assert set(defs) == set(effects.METRIC_KEYS)
    assert defs["manualHours"]["good"] == -1, "falling is the win"
    assert defs["leadTime"]["good"] == -1
    assert defs["cash"]["good"] == 1

    for metric in effects.METRICS:
        assert defs[metric.key]["label"] == metric.label
        assert defs[metric.key]["unit"] == metric.unit
        assert defs[metric.key]["floor"] == metric.floor
        assert defs[metric.key]["ceiling"] == metric.ceiling

    # The bar's maximum for manual hours is coupled to the draws that produce the value,
    # so it is the re-authored figure rather than the prototype's 500.
    assert defs["manualHours"]["display_max"] == cap.MANUAL_HOURS_DISPLAY_MAX

    # The load ramp's domain, so the HUD's capacity heat and the DAG's node tint read the
    # same scale rather than each writing it down.
    assert payload["load"] == {"scale": cap.LOAD_SCALE, "ceiling": cap.LOAD_CEILING}


def test_every_item_moving_event_reports_the_status_it_landed_in() -> None:
    """A read-side consumer moves its node from the event, not from its own rule table.

    Without this, a client would need its own copy of "what does each event kind do to an
    item" — a second implementation of the fold's most load-bearing mapping, passing its own
    tests while disagreeing with the kernel. Two of these are stamped *after* the operation
    finishes rather than when the payload is built, because a direct assignment passes
    through `assigned` and lands on `active` inside one command.
    """
    state, events = sim.new_run(run_seed=SEED)
    collected = list(events)

    # Routed through the director, so the item sits at `assigned` while they walk it over.
    collected.extend(sim.assign_via_manager(state, "wi_ap_map"))
    collected.extend(run_until(state, lambda s: s.items["wi_ap_map"].status == sim.STATUS_ACTIVE))
    collected.extend(run_until(state, lambda s: s.items["wi_ap_map"].status == sim.STATUS_BLOCKED))
    collected.extend(sim.resolve_checkpoint(state, "wi_ap_map", 0, 0, in_person=False))
    collected.extend(run_until(state, lambda s: s.items["wi_ap_map"].status == sim.STATUS_DONE))

    # And a return to the backlog, which no single item's happy path reaches.
    collected.extend(sim.assign_direct(state, "wi_dup_entry", "stf_order"))
    collected.extend(sim.return_to_backlog(state, "wi_dup_entry"))

    moving = {
        "WORK_ASSIGNED",
        "WORK_REASSIGNED",
        "WORK_RETURNED_TO_BACKLOG",
        "CHECKPOINT_RAISED",
        "DECISION_RESOLVED",
        "DELIVERABLE_PRODUCED",
    }
    seen: set[str] = set()
    for event in collected:
        if event.kind.name not in moving:
            continue
        assert "item_status" in event.payload, f"{event.kind.name} does not say where it moved"
        assert event.payload["item_status"] in {
            sim.STATUS_BACKLOG,
            sim.STATUS_ASSIGNED,
            sim.STATUS_ACTIVE,
            sim.STATUS_BLOCKED,
            sim.STATUS_DONE,
        }
        seen.add(event.payload["item_status"])

    # All five display states reached, which is what U14's node encoding has to cover.
    assert seen == {
        sim.STATUS_BACKLOG,
        sim.STATUS_ASSIGNED,
        sim.STATUS_ACTIVE,
        sim.STATUS_BLOCKED,
        sim.STATUS_DONE,
    }


def test_a_direct_assignment_reports_active_not_the_status_it_passed_through() -> None:
    """The stamped-after-the-fact case, asserted on its own.

    `assign_direct` sets `assigned`, builds the payload, then starts the work. Reporting the
    status as it stood when the payload was built would tell a consumer to render `assigned`
    for an item that is already burning effort, with no later event to correct it.
    """
    state, _ = sim.new_run(run_seed=SEED)
    events = sim.assign_direct(state, "wi_ap_map", "stf_ap")

    assert events[0].kind.name == "WORK_ASSIGNED"
    assert events[0].payload["item_status"] == sim.STATUS_ACTIVE
    assert state.items["wi_ap_map"].status == sim.STATUS_ACTIVE


def test_reassignment_reports_active_not_the_status_it_passed_through() -> None:
    """`reassign` is the third stamped-after-the-fact site, and the only one with no test.

    Like `assign_direct` it sets `assigned`, builds the payload, then calls `_start_work` — so the
    stamp has to happen after the operation. Asserted on its own because the journey test above
    reaches this site only incidentally, and a regression here would look like an item stuck at
    `assigned` while it burns effort.
    """
    state, _ = sim.new_run(run_seed=SEED)
    sim.assign_direct(state, "wi_ap_map", "stf_ap")

    events = sim.reassign(state, "wi_ap_map", "dir_admin")

    assert events[0].kind.name == "WORK_REASSIGNED"
    assert events[0].payload["item_status"] == sim.STATUS_ACTIVE
    assert state.items["wi_ap_map"].status == sim.STATUS_ACTIVE


def test_attrition_reports_the_status_of_the_item_it_returned() -> None:
    """ATTRITION carries `returned_item_status`, not `item_status`.

    The name differs deliberately: every other kind carrying a status also carries `item`, and this
    one carries `returned_item`. A consumer generalising the six-kind convention would otherwise
    read the field as the status of an item the payload never names.
    """
    state, _ = sim.new_run(run_seed=SEED)
    sim.assign_direct(state, "wi_ap_map", "stf_ap")

    events = sim._depart(state, "stf_ap", "dir_admin")

    assert events[0].kind.name == "ATTRITION"
    payload = events[0].payload
    assert payload["returned_item"] == "wi_ap_map"
    assert payload["returned_item_status"] == sim.STATUS_BACKLOG
    assert "item_status" not in payload, "the field is named for what it describes"
    assert state.items["wi_ap_map"].status == sim.STATUS_BACKLOG
    assert state.items["wi_ap_map"].assignee == ""


def test_attrition_with_nothing_in_flight_reports_no_returned_status() -> None:
    """Someone leaving empty-handed returns no item, so there is no status to report."""
    state, _ = sim.new_run(run_seed=SEED)

    events = sim._depart(state, "stf_ap", "dir_admin")

    assert events[0].payload["returned_item"] == ""
    assert events[0].payload["returned_item_status"] == ""


def test_a_delivered_item_reports_its_final_effort(run: sim.State) -> None:
    """DELIVERABLE_PRODUCED carries `done_units`, so progress lands on complete.

    Without it `done_units` reaches a consumer only on CHECKPOINT_RAISED, and a delivered item
    renders at its last checkpoint's percentage forever.
    """
    _block_first_checkpoint(run)
    sim.resolve_checkpoint(run, "wi_ap_map", 0, 0, in_person=False)
    events = run_until(run, lambda s: s.items["wi_ap_map"].status == sim.STATUS_DONE)

    produced = [event for event in events if event.kind.name == "DELIVERABLE_PRODUCED"]
    assert len(produced) == 1

    spec = SHIPPED.item("wi_ap_map")
    assert produced[0].payload["done_units"] >= spec.effort_units
    assert produced[0].payload["item_status"] == sim.STATUS_DONE


# =========================================================================
# Asking, and what an answer is worth
# =========================================================================


def _ask(state: sim.State, person_id: str, question: str) -> dict:
    """Ask, and return the QUESTION_ANSWERED payload."""
    events = sim.ask_person(state, person_id, question)
    answered = [e for e in events if e.kind.name == "QUESTION_ANSWERED"]
    assert len(answered) == 1
    return answered[0].payload


def test_the_first_tacit_answer_raises_visibility(run: sim.State) -> None:
    """R17, AE4."""
    before = run.metrics["visibility"]

    payload = _ask(run, "stf_ap", "why does it work that way?")

    assert payload["matched"] is True
    assert payload["question"] == "why"
    assert payload["first_time"] is True
    assert payload["answer"] == SHIPPED.voice_of("stf_ap", "why")
    assert run.metrics["visibility"] == before + rates.TUNING["visibility_per_tacit_answer"]


def test_asking_the_same_question_again_pays_nothing(run: sim.State) -> None:
    """R17, AE4: the answer returns, and nothing moves."""
    _ask(run, "stf_ap", "why does it work that way?")
    after_first = run.metrics["visibility"]

    payload = _ask(run, "stf_ap", "why though?")

    assert payload["first_time"] is False
    assert payload["answer"] == SHIPPED.voice_of("stf_ap", "why")
    assert payload["deltas"] == {}
    assert run.metrics["visibility"] == after_first


def test_the_bottleneck_question_never_moves_visibility(run: sim.State) -> None:
    before = run.metrics["visibility"]

    payload = _ask(run, "stf_ap", "where does the time go?")

    assert payload["question"] == "bottleneck"
    assert payload["tacit"] is False
    assert payload["answer"] == SHIPPED.voice_of("stf_ap", "bottleneck")
    assert run.metrics["visibility"] == before


def test_the_knowledge_is_theirs_not_the_company_s(run: sim.State) -> None:
    """Asking one person why does not make everybody else's why free."""
    _ask(run, "stf_ap", "why?")
    before = run.metrics["visibility"]

    payload = _ask(run, "stf_buyer", "why?")

    assert payload["first_time"] is True
    assert run.metrics["visibility"] == before + rates.TUNING["visibility_per_tacit_answer"]


def test_a_question_matching_nothing_deflects_and_records_nothing(run: sim.State) -> None:
    """R16, AE5."""
    before = dict(run.metrics)

    payload = _ask(run, "stf_ap", "what do you think of the weather")

    assert payload["matched"] is False
    assert payload["answer"] == SHIPPED.deflection_of("stf_ap")
    assert run.metrics == before
    assert run.people["stf_ap"].answered == []


def test_each_person_deflects_in_their_own_voice(run: sim.State) -> None:
    lines = {person.id: SHIPPED.deflection_of(person.id) for person in SHIPPED.people}

    assert len(set(lines.values())) == len(SHIPPED.people)
    for person_id, line in lines.items():
        assert line, f"{person_id} has no deflection line"


def test_every_person_has_all_four_scripted_answers() -> None:
    for person in SHIPPED.people:
        for slot in roster.ASK_SLOTS:
            assert SHIPPED.voice_of(person.id, slot), f"{person.id} has no {slot} answer"


def test_the_four_intents_match_their_keywords_case_insensitively() -> None:
    """R15."""
    assert roster.match_intent("WHY is that").slot == "why"
    assert roster.match_intent("any EXCEPTIONS?").slot == "exception"
    assert roster.match_intent("who decides this").slot == "axis"
    assert roster.match_intent("what is the BOTTLENECK").slot == "bottleneck"
    assert roster.match_intent("hello there") is None


def test_three_of_the_four_are_tacit() -> None:
    assert roster.TACIT_SLOTS == frozenset({"why", "exception", "axis"})


def test_asking_someone_who_is_not_on_the_roster_is_rejected(run: sim.State) -> None:
    before = hashing.state_hash(sim.snapshot(run)).overall

    with pytest.raises(sim.CommandRejected):
        sim.ask_person(run, "nobody", "why?")

    assert hashing.state_hash(sim.snapshot(run)).overall == before


def test_answered_questions_stay_canonical_in_hashed_state(run: sim.State) -> None:
    """A set or a float reaching hashed state raises at a day boundary, far from the cause."""
    _ask(run, "stf_ap", "why?")
    _ask(run, "stf_ap", "any exceptions?")

    recorded = sim.snapshot(run)["people"]["stf_ap"]["answered"]
    assert isinstance(recorded, list)
    assert recorded == sorted(recorded)
    # Raises NotCanonical if anything in here is a set or a float.
    hashing.state_hash(sim.snapshot(run))


def test_two_states_that_heard_the_same_questions_hash_the_same(run: sim.State) -> None:
    """Order of asking must not change identity, which is what the sorting is for."""
    other, _ = sim.new_run(run_seed=SEED)

    _ask(run, "stf_ap", "why?")
    _ask(run, "stf_ap", "any exceptions?")

    _ask(other, "stf_ap", "any exceptions?")
    _ask(other, "stf_ap", "why?")

    assert (
        hashing.state_hash(sim.snapshot(run)).overall
        == hashing.state_hash(sim.snapshot(other)).overall
    )


def test_a_visibility_gain_from_asking_announces_what_it_unlocked(run: sim.State) -> None:
    """Asking opens work, and the floor has to be told."""
    gate = SHIPPED.item("wi_close").requires.visibility
    assert gate is not None
    assert not sim.is_unlocked(run, "wi_close")

    # Enough first tacit answers to clear the gate.
    slots = ["why?", "any exceptions?", "who decides?"]
    for person in SHIPPED.people:
        for question in slots:
            sim.ask_person(run, person.id, question)
        if sim.is_unlocked(run, "wi_close"):
            break

    assert sim.is_unlocked(run, "wi_close")

    # Announced by the step, not by the command, so it regenerates on replay.
    events = advance(run, 1)
    unlocked = [e for e in events if e.kind.name == "ITEM_UNLOCKED"]
    assert any(e.payload["item"] == "wi_close" for e in unlocked)


def test_an_unlock_is_announced_once(run: sim.State) -> None:
    for person in SHIPPED.people:
        for question in ["why?", "any exceptions?", "who decides?"]:
            sim.ask_person(run, person.id, question)

    first = [e for e in advance(run, 1) if e.kind.name == "ITEM_UNLOCKED"]
    again = [e for e in advance(run, 5) if e.kind.name == "ITEM_UNLOCKED"]

    assert first
    assert not again


def test_answered_questions_survive_a_snapshot(run: sim.State) -> None:
    """R19, AE7."""
    from simcore import snapshot as snap

    _ask(run, "stf_ap", "why?")
    _ask(run, "stf_ap", "who decides?")
    visibility = run.metrics["visibility"]

    restored = snap.from_wire(snap.to_wire(run))

    assert restored.people["stf_ap"].answered == ["axis", "why"]

    # And a repeat ask on the restored run pays nothing.
    payload = _ask(restored, "stf_ap", "why?")
    assert payload["first_time"] is False
    assert restored.metrics["visibility"] == visibility


def test_asking_a_hire_is_rejected_and_mutates_nothing(run: sim.State) -> None:
    """A command must be total before it is effectful.

    `state.people` contains arrived hires, who are not on the authored roster and have no
    script. Looking their answer up *after* charging Visibility raised a bare KeyError past
    the point where CommandRejected is caught, leaving state changed with no event to explain
    it — a state hash that moves with nothing in the log is replay identity broken silently.
    """
    run.people["hire_sales_1"] = sim.PersonRuntime(
        id="hire_sales_1", pos=(5, 5), seat=(5, 5)
    )
    before = hashing.state_hash(sim.snapshot(run)).overall

    with pytest.raises(sim.CommandRejected):
        sim.ask_person(run, "hire_sales_1", "why does it work that way?")

    assert hashing.state_hash(sim.snapshot(run)).overall == before
    assert run.people["hire_sales_1"].answered == []


def test_no_command_that_rejects_may_leave_state_changed(run: sim.State) -> None:
    """The general form of the bug above, applied across the commands that can reject.

    Written as a sweep rather than one case each: the failure was an *ordering* mistake, and
    ordering mistakes are made once per command by whoever writes the next one.
    """
    run.people["hire_sales_1"] = sim.PersonRuntime(
        id="hire_sales_1", pos=(5, 5), seat=(5, 5)
    )

    attempts = [
        lambda: sim.ask_person(run, "hire_sales_1", "why?"),
        lambda: sim.ask_person(run, "nobody", "why?"),
        lambda: sim.ask_person(run, "stf_ap", "x" * (sim.MAX_QUESTION_CHARS + 1)),
        lambda: sim.assign_direct(run, "wi_ap_map", "nobody"),
        lambda: sim.resolve_checkpoint(run, "wi_ap_map", 0, 0, in_person=True),
        lambda: sim.submit_ceo_input(run, sim.INPUT_LEFT, at_tick=run.tick),
        lambda: sim.submit_ceo_input(
            run, sim.INPUT_LEFT, at_tick=run.tick + sim.MAX_INPUT_LEAD_TICKS + 1
        ),
    ]

    for attempt in attempts:
        before = hashing.state_hash(sim.snapshot(run)).overall
        with pytest.raises(sim.CommandRejected):
            attempt()
        assert hashing.state_hash(sim.snapshot(run)).overall == before


def test_an_overlong_question_is_rejected(run: sim.State) -> None:
    """The text is written verbatim into an append-only log and copied by every fork."""
    ok = "why " * 10
    assert len(ok) <= sim.MAX_QUESTION_CHARS
    sim.ask_person(run, "stf_ap", ok)

    with pytest.raises(sim.CommandRejected, match="the limit is"):
        sim.ask_person(run, "stf_ap", "why " + "x" * sim.MAX_QUESTION_CHARS)


def test_an_input_tagged_absurdly_far_ahead_is_rejected(run: sim.State) -> None:
    """`ceo_inputs` is hashed state scanned every tick; pruning only drops what the clock passed."""
    sim.submit_ceo_input(run, sim.INPUT_LEFT, at_tick=run.tick + sim.MAX_INPUT_LEAD_TICKS)

    with pytest.raises(sim.CommandRejected, match="ticks ahead"):
        sim.submit_ceo_input(
            run, sim.INPUT_LEFT, at_tick=run.tick + sim.MAX_INPUT_LEAD_TICKS + 1
        )


def test_the_held_input_dict_stays_bounded_under_a_flood(run: sim.State) -> None:
    for offset in range(1, 200):
        sim.submit_ceo_input(run, sim.INPUT_LEFT, at_tick=run.tick + offset)

    advance(run, 250)

    # Everything the clock has passed is gone; nothing is scheduled beyond it.
    assert len(run.ceo_inputs) == 1


def test_a_later_submission_supersedes_an_earlier_tagged_tick(run: sim.State) -> None:
    """Supersession follows submission order, not tick order.

    The client's lead is a wall-time budget converted at the current rate, so changing rate
    mid-hold can tag a *release* for an earlier tick than the press it supersedes. Under
    "the most recent input at or before this tick" alone, the release passes and the older
    press then resurrects — the CEO walking off on a key nobody is holding.
    """
    x, y = run.ceo.tile
    assert walkable(run.floor, x - 1, y)

    # Press, tagged well ahead — as it would be at a fast rate.
    sim.submit_ceo_input(run, sim.INPUT_LEFT, at_tick=run.tick + 20)
    # Release, tagged nearer — as it would be after dropping to x1.
    sim.submit_ceo_input(run, 0, at_tick=run.tick + 5)

    start = run.ceo.x_milli
    advance(run, 60)

    assert run.ceo.x_milli == start, "the superseded press came back to life"
    assert len(run.ceo_inputs) == 1


def test_asking_someone_who_has_left_is_rejected(run: sim.State) -> None:
    """A company cannot go on learning from someone who resigned."""
    run.departed.append("stf_ap")
    before = hashing.state_hash(sim.snapshot(run)).overall

    with pytest.raises(sim.CommandRejected, match="has left"):
        sim.ask_person(run, "stf_ap", "why?")

    assert hashing.state_hash(sim.snapshot(run)).overall == before
