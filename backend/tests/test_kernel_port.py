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

from simcore import hashing
from simcore import items as work
from simcore import people as roster
from simcore import step as sim
from simcore import time as simtime
from simcore.world import find_path, plan_floor, walkable

SEED = 0xC0FFEE


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
    lines = roster.reporting_lines()
    assert set(lines) == {"dir_sales", "dir_admin", "dir_cs", "dir_hr"}
    # The director is a member of their own line, not an overseer of it.
    for director, members in lines.items():
        assert director in members


def test_two_lines_hold_exactly_one_non_director() -> None:
    """The reason U7's capacity draw has to include the director.

    Customer Support and People each have a single specialist, so one attrition event
    would leave a department with nobody to allocate a draw across.
    """
    lines = roster.reporting_lines()
    assert len(lines["dir_cs"]) == 2
    assert len(lines["dir_hr"]) == 2


def test_the_room_and_the_reporting_line_disagree_for_priya() -> None:
    """The deliberate mismatch in the sample data, and it has to survive the port."""
    priya = roster.spec("stf_ap")
    assert priya.dept == "accounting"
    assert roster.reporting_line_of("stf_ap") == "dir_admin"
    assert roster.spec("dir_admin").dept == "admin"


def test_a_smaller_grid_still_seats_everyone() -> None:
    """Desk spacing tightens and rows are added until everyone fits."""
    floor = plan_floor(26, 16)
    seats = roster.assign_seats(floor)
    assert len(set(seats.values())) == len(roster.PEOPLE)


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


def test_a_checkpoint_is_raised_at_its_threshold(run: sim.State) -> None:
    sim.assign_direct(run, "wi_ap_map", "stf_ap")
    events = run_until(run, lambda s: s.items["wi_ap_map"].status == sim.STATUS_BLOCKED)

    raised = [e for e in events if e.kind.name == "CHECKPOINT_RAISED"]
    assert len(raised) == 1

    payload = raised[0].payload
    spec = work.spec("wi_ap_map")
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

    raised = [e for e in events if e.kind.name == "CHECKPOINT_RAISED"]
    assert raised[0].payload["tacit"] == work.spec("wi_ap_map").checkpoints[0].tacit
    assert raised[0].payload["tacit"]


def test_the_genesis_catalog_ships_no_tacit_lines() -> None:
    """R8: nothing that is only earned in person may arrive before it is earned."""
    for entry in work.catalog_to_state():
        for checkpoint in entry["checkpoints"]:
            assert "tacit" not in checkpoint


def test_every_authored_checkpoint_has_a_line_worth_walking_for() -> None:
    for spec in work.ITEMS:
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
    assert output.title == work.spec("wi_ap_map").output_title
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
    assert len(snapshot["morale"]) == len(roster.PEOPLE)
    assert snapshot["hiring"] == {}, "no hire has been requested"
    assert hashing.STATE_SHAPE_VERSION == 1, "the subsystem list did not change, so nor does this"


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

    assert [entry["id"] for entry in catalog] == [item.id for item in work.ITEMS]

    edges = {
        (required, entry["id"])
        for entry in catalog
        for required in entry["requires"]["items"]
    }
    assert ("wi_ap_map", "wi_ap_auto") in edges, "the authored edge has to be on the wire"

    by_id = {entry["id"]: entry for entry in catalog}
    for item in work.ITEMS:
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
        for item in work.ITEMS
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
            # Prose, not arithmetic: an option is priced in its detail line. Shipping the
            # deltas would let the CEO optimise against numbers instead of judgement.
            for option in checkpoint["options"]:
                assert set(option) == {"label", "detail"}


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
        for item in work.ITEMS
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
        item = work.spec(item_id)
        rendered = by_id[item_id]["checkpoints"][cp_index]["prompt"]
        assert rendered == work.rendered_prompt(item, cp_index)
        assert rendered != item.checkpoints[cp_index].prompt

    for entry in catalog:
        assert entry["brief"] == work.rendered_brief(work.spec(entry["id"]))


def test_catalog_names_the_department_whose_load_the_work_sits_in() -> None:
    """The DAG's load tint reads `director`, and it is derived rather than authored."""
    _, genesis = sim.new_run(run_seed=SEED)
    catalog = genesis[0].payload["catalog"]

    for entry in catalog:
        item = work.spec(entry["id"])
        assert entry["director"] == work.director_for(item)
        assert entry["director"] == roster.reporting_line_of(item.want)


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

    spec = work.spec("wi_ap_map")
    assert produced[0].payload["done_units"] >= spec.effort_units
    assert produced[0].payload["item_status"] == sim.STATUS_DONE
