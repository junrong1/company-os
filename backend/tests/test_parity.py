"""The prototype's 29 runtime assertions, ported against the kernel.

This is the gate the plan puts before any new mechanic: prove the port, then change it.

**The assertions are ported, not the code.** `test/harness.js` drives the prototype's
real JavaScript through a DOM stub and a requestAnimationFrame queue. None of that
survives here — the kernel has no frames and no DOM. What survives is each `check(...)`
and the sequence of actions leading to it, so that a divergence in *behaviour* fails
while a difference in *mechanism* does not.

**29, not 30.** The harness has 30 `check(` call sites. One of them, `'second item also
reaches a decision'` at `harness.js:159`, sits in the `else` of a branch that is not
taken on the default path — it fires only if the second item somehow fails to reach its
checkpoint, and asserts `false` to report that. Porting it would mean porting an
assertion that never runs. It is recorded in
`test_the_thirtieth_assertion_is_accounted_for` instead, along with the condition that
keeps it unreachable.

**Wall seconds become ticks, exactly.** The harness advances in wall seconds. At the
prototype's `HOURS_PER_SEC = 0.6` and a one-sim-minute quantum, one wall second at x1 is
36 ticks — and the conversion is exact rather than approximate, because 90 ticks is 2.5
wall seconds and the ported walk speed of 7 tiles per 90 ticks *is* the prototype's 2.8
tiles per wall second. So walk timings agree without any fudge factor.

**Which of these are expected to change.** The assertions that depend on how much effort
has burned by a given moment are marked `effort_timed`. U7 alters the burn rate through
capacity and morale, so those are the ones that will move, and the marker makes U7's job
mechanical: `pytest -m effort_timed`. Nothing here asserts anything about manual hours,
so U7's manual-hours work needs no exemption.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from simcore import items as work
from simcore import people as roster
from simcore import step as sim
from simcore import time as simtime
from simcore.world import find_path, walkable

SEED = 0xC0FFEE

#: One wall second of the harness, in ticks, at x1. See the module docstring.
TICKS_PER_WALL_SECOND = simtime.TICKS_PER_WALL_SECOND_AT_BASE_RATE

GOLDEN = Path(__file__).resolve().parent / "fixtures" / "golden"


def _advance(state: sim.State, wall_seconds: int) -> None:
    """The harness's `advance(seconds)`."""
    for _ in range(wall_seconds * TICKS_PER_WALL_SECOND):
        sim.step(state)


@pytest.fixture(scope="module")
def observed() -> dict[str, Any]:
    """Play the harness's scenario once and record what it checks at each stage.

    The harness is one sequential script over shared mutable state, so replaying it per
    assertion would be both slow and a different test. It runs once; each ported
    assertion then reads the value the harness would have read at that point.
    """
    seen: dict[str, Any] = {}
    state, _ = sim.new_run(run_seed=SEED)

    # --- 1. Initial state ------------------------------------------------
    seen["all_idle"] = all(person.state == sim.STATE_IDLE for person in state.people.values())
    seen["visibility_start"] = state.metrics["visibility"]
    seen["available_at_start"] = [
        item.id for item in work.ITEMS if item.requires == work.Requires()
    ]
    seen["locked_at_start"] = [
        item.id for item in work.ITEMS if item.requires != work.Requires()
    ]

    # --- 2. Assign through the reporting line ----------------------------
    sim.assign_via_manager(state, "wi_ap_map")
    director = state.people["dir_admin"]
    staff = state.people["stf_ap"]

    seen["director_state_on_assign"] = director.state
    seen["director_path_cells"] = len(director.path)

    _advance(state, 12)
    seen["staff_state_after_12s"] = staff.state
    seen["staff_item_after_12s"] = staff.item_id
    seen["director_state_after_12s"] = director.state

    # --- 3. Work stalls at the checkpoint --------------------------------
    _advance(state, 40)
    item = state.items["wi_ap_map"]
    seen["staff_state_after_52s"] = staff.state
    seen["item_status_after_52s"] = item.status

    before_stall = item.done_units
    _advance(state, 10)
    seen["done_before_stall"] = before_stall
    seen["done_after_stall"] = item.done_units
    seen["blocked_count"] = sum(
        1 for candidate in state.items.values() if candidate.status == sim.STATUS_BLOCKED
    )

    # --- 4. In-person decisions surface tacit knowledge ------------------
    visibility_before = state.metrics["visibility"]
    morale_before = state.metrics["morale"]
    sim.resolve_checkpoint(state, "wi_ap_map", 0, 0, in_person=True)

    seen["staff_state_after_resolve"] = staff.state
    seen["item_status_after_resolve"] = item.status
    seen["visibility_before_resolve"] = visibility_before
    seen["visibility_after_resolve"] = state.metrics["visibility"]
    seen["morale_before_resolve"] = morale_before
    seen["morale_after_resolve"] = state.metrics["morale"]
    seen["tacit_recorded"] = bool(item.decisions[0].tacit)

    # --- 5. Completion produces a deliverable with provenance ------------
    _advance(state, 60)
    seen["item_status_after_completion"] = item.status
    seen["output_count"] = len(state.outputs)
    seen["output_title"] = state.outputs[0].title if state.outputs else ""
    seen["provenance_lines"] = len(state.outputs[0].provenance) if state.outputs else 0
    seen["staff_state_after_completion"] = staff.state
    seen["staff_item_after_completion"] = staff.item_id
    seen["followup_unlocked"] = sim.is_unlocked(state, "wi_ap_auto")

    # --- 6. Bypassing the director costs morale --------------------------
    morale_before_direct = state.metrics["morale"]
    sim.assign_direct(state, "wi_dup_entry", "stf_order")
    seen["morale_before_direct"] = morale_before_direct
    seen["morale_after_direct"] = state.metrics["morale"]
    seen["order_state_after_direct"] = state.people["stf_order"].state

    # --- 7. Tray decisions get no bonus ----------------------------------
    _advance(state, 70)
    second = state.items["wi_dup_entry"]
    seen["second_status_after_70s"] = second.status
    seen["second_progress_percent"] = (
        second.done_units * 100 // work.spec("wi_dup_entry").effort_units
    )

    if second.status == sim.STATUS_BLOCKED:
        visibility_before_tray = state.metrics["visibility"]
        sim.resolve_checkpoint(state, "wi_dup_entry", 0, 1, in_person=False)
        seen["visibility_before_tray"] = visibility_before_tray
        seen["visibility_after_tray"] = state.metrics["visibility"]
        seen["tray_tacit"] = second.decisions[0].tacit

    # --- 8. Time passes and fixed costs accrue ---------------------------
    seen["day"] = simtime.day_of(state.tick)
    seen["cash"] = state.metrics["cash"]

    # --- 9. Pathfinding respects walls -----------------------------------
    # The harness uses `S.people[4].seat`, which is the fifth roster entry: Priya.
    far = state.seats[roster.PEOPLE[4].id]
    path = find_path(state.floor, state.floor.spawn, far)
    seen["far_seat"] = far
    seen["path_length"] = len(path)
    seen["path_all_walkable"] = all(walkable(state.floor, x, y) for x, y in path)
    seen["every_desk_reachable"] = all(
        len(find_path(state.floor, state.floor.spawn, seat)) > 0
        for seat in state.seats.values()
    )
    seen["distinct_seats"] = len(set(state.seats.values())) == len(state.seats)
    seen["grid"] = (state.floor.cols, state.floor.rows)
    seen["room_count"] = len(state.floor.rooms)

    return seen


# =========================================================================
# 1. Initial state  (harness.js:100-104)
# =========================================================================


def test_everyone_starts_idle(observed: dict[str, Any]) -> None:
    assert observed["all_idle"] is True


def test_visibility_starts_at_6_percent(observed: dict[str, Any]) -> None:
    assert observed["visibility_start"] == 6


def test_five_directives_available_at_start(observed: dict[str, Any]) -> None:
    assert len(observed["available_at_start"]) == 5, observed["available_at_start"]


def test_three_directives_locked_at_start(observed: dict[str, Any]) -> None:
    assert len(observed["locked_at_start"]) == 3, observed["locked_at_start"]


# =========================================================================
# 2. Assign through the reporting line  (harness.js:110-113)
# =========================================================================


def test_director_walks_to_hand_off(observed: dict[str, Any]) -> None:
    assert observed["director_state_on_assign"] == sim.STATE_WALKING
    assert observed["director_path_cells"] > 0


@pytest.mark.effort_timed
def test_specialist_started_the_work(observed: dict[str, Any]) -> None:
    assert observed["staff_state_after_12s"] == sim.STATE_WORKING
    assert observed["staff_item_after_12s"] == "wi_ap_map"


@pytest.mark.effort_timed
def test_director_returns_to_their_desk(observed: dict[str, Any]) -> None:
    """The harness accepts either, because the return walk may still be in progress."""
    assert observed["director_state_after_12s"] in (sim.STATE_IDLE, sim.STATE_WALKING)


# =========================================================================
# 3. Work stalls at the checkpoint  (harness.js:117-123)
# =========================================================================


@pytest.mark.effort_timed
def test_stops_at_the_decision_point(observed: dict[str, Any]) -> None:
    assert observed["staff_state_after_52s"] == sim.STATE_BLOCKED
    assert observed["item_status_after_52s"] == sim.STATUS_BLOCKED


def test_no_progress_until_you_decide(observed: dict[str, Any]) -> None:
    """The prototype compares floats within 0.001. Integers compare exactly."""
    assert observed["done_after_stall"] == observed["done_before_stall"]


def test_one_item_waiting_in_the_tray(observed: dict[str, Any]) -> None:
    assert observed["blocked_count"] == 1


# =========================================================================
# 4. In-person decisions surface tacit knowledge  (harness.js:128-132)
# =========================================================================


def test_work_resumes_after_the_decision(observed: dict[str, Any]) -> None:
    assert observed["staff_state_after_resolve"] == sim.STATE_WORKING
    assert observed["item_status_after_resolve"] == sim.STATUS_ACTIVE


def test_in_person_decision_raises_visibility(observed: dict[str, Any]) -> None:
    """+6 from the option, +2 for having walked over."""
    assert observed["visibility_after_resolve"] == observed["visibility_before_resolve"] + 6 + 2


def test_in_person_decision_raises_morale(observed: dict[str, Any]) -> None:
    """-1 from the option, +2 for having walked over."""
    assert observed["morale_after_resolve"] == observed["morale_before_resolve"] - 1 + 2


def test_tacit_knowledge_recorded(observed: dict[str, Any]) -> None:
    assert observed["tacit_recorded"] is True


# =========================================================================
# 5. Completion produces a deliverable with provenance  (harness.js:136-141)
# =========================================================================


@pytest.mark.effort_timed
def test_work_completed(observed: dict[str, Any]) -> None:
    assert observed["item_status_after_completion"] == sim.STATUS_DONE


@pytest.mark.effort_timed
def test_one_deliverable_produced(observed: dict[str, Any]) -> None:
    assert observed["output_count"] == 1
    assert observed["output_title"] == "AP process map (12 steps)"


@pytest.mark.effort_timed
def test_deliverable_carries_provenance(observed: dict[str, Any]) -> None:
    assert observed["provenance_lines"] >= 2


@pytest.mark.effort_timed
def test_specialist_back_to_idle(observed: dict[str, Any]) -> None:
    assert observed["staff_state_after_completion"] == sim.STATE_IDLE
    # The prototype clears `itemId` to null; the port uses the empty string, since a
    # nullable field would have to be encoded specially to cross the log.
    assert observed["staff_item_after_completion"] == ""


@pytest.mark.effort_timed
def test_followup_work_unlocked(observed: dict[str, Any]) -> None:
    assert observed["followup_unlocked"] is True


# =========================================================================
# 6. Bypassing the director costs morale  (harness.js:147-148)
# =========================================================================


def test_direct_assignment_costs_3_morale(observed: dict[str, Any]) -> None:
    assert observed["morale_after_direct"] == observed["morale_before_direct"] - 3


def test_specialist_started_moving(observed: dict[str, Any]) -> None:
    assert observed["order_state_after_direct"] in (sim.STATE_WORKING, sim.STATE_WALKING)


# =========================================================================
# 7. Tray decisions get no bonus  (harness.js:156-157)
# =========================================================================


@pytest.mark.effort_timed
def test_tray_decision_gets_no_in_person_bonus(observed: dict[str, Any]) -> None:
    assert "visibility_after_tray" in observed, (
        "the second item never reached its checkpoint, so the harness's else branch "
        "would have fired: status="
        f"{observed['second_status_after_70s']} progress={observed['second_progress_percent']}%"
    )
    assert observed["visibility_after_tray"] == observed["visibility_before_tray"]


@pytest.mark.effort_timed
def test_tray_decision_records_no_tacit_knowledge(observed: dict[str, Any]) -> None:
    """The prototype stores `null`; the port stores an empty string. Both are falsy."""
    assert observed["tray_tacit"] == ""


# =========================================================================
# 8. Time passes and fixed costs accrue  (harness.js:163-164)
# =========================================================================


@pytest.mark.effort_timed
def test_days_advanced(observed: dict[str, Any]) -> None:
    assert observed["day"] > 1


@pytest.mark.effort_timed
def test_cash_reduced_by_fixed_costs(observed: dict[str, Any]) -> None:
    assert observed["cash"] < 4800


# =========================================================================
# 9. Pathfinding respects walls  (harness.js:171-175)
# =========================================================================


def test_path_found_to_a_distant_desk(observed: dict[str, Any]) -> None:
    assert observed["path_length"] > 6, f"only {observed['path_length']} cells"


def test_path_never_crosses_a_wall(observed: dict[str, Any]) -> None:
    assert observed["path_all_walkable"] is True


def test_every_desk_is_reachable(observed: dict[str, Any]) -> None:
    assert observed["every_desk_reachable"] is True


def test_every_person_got_a_distinct_desk(observed: dict[str, Any]) -> None:
    assert observed["distinct_seats"] is True


# =========================================================================
# Traceability
# =========================================================================


#: Every `check(` call site in `test/harness.js`, in source order, mapped to the test
#: that ports it. `None` means the site does not execute on the default path.
#:
#: An explicit map rather than a count of test functions: a count tells you the number
#: is wrong but not which assertion went missing, and it breaks the moment a helper test
#: is added to this module. This says which harness line each test descends from, so the
#: parity claim is auditable against the source it claims parity with.
HARNESS_CHECKS: tuple[tuple[int, str, str | None], ...] = (
    (100, "everyone starts idle", "test_everyone_starts_idle"),
    (101, "visibility starts at 6%", "test_visibility_starts_at_6_percent"),
    (102, "5 directives available at start", "test_five_directives_available_at_start"),
    (104, "3 directives locked at start", "test_three_directives_locked_at_start"),
    (110, "director walks to hand off", "test_director_walks_to_hand_off"),
    (112, "specialist started the work", "test_specialist_started_the_work"),
    (113, "director returns to their desk", "test_director_returns_to_their_desk"),
    (117, "stops at the decision point", "test_stops_at_the_decision_point"),
    (121, "no progress until you decide", "test_no_progress_until_you_decide"),
    (123, "one item waiting in the tray", "test_one_item_waiting_in_the_tray"),
    (128, "work resumes after the decision", "test_work_resumes_after_the_decision"),
    (129, "in-person decision raises visibility", "test_in_person_decision_raises_visibility"),
    (131, "in-person decision raises morale", "test_in_person_decision_raises_morale"),
    (132, "tacit knowledge recorded", "test_tacit_knowledge_recorded"),
    (136, "work completed", "test_work_completed"),
    (137, "one deliverable produced", "test_one_deliverable_produced"),
    (138, "deliverable carries provenance", "test_deliverable_carries_provenance"),
    (140, "specialist back to idle", "test_specialist_back_to_idle"),
    (141, "follow-up work unlocked", "test_followup_work_unlocked"),
    (147, "direct assignment costs 3 morale", "test_direct_assignment_costs_3_morale"),
    (148, "specialist started moving", "test_specialist_started_moving"),
    (156, "tray decision gets no in-person bonus", "test_tray_decision_gets_no_in_person_bonus"),
    (
        157,
        "tray decision records no tacit knowledge",
        "test_tray_decision_records_no_tacit_knowledge",
    ),
    # The else branch. Fires only when the second item has *not* reached its
    # checkpoint, and asserts false to report that. See the test below.
    (159, "second item also reaches a decision", None),
    (163, "days advanced", "test_days_advanced"),
    (164, "cash reduced by fixed costs", "test_cash_reduced_by_fixed_costs"),
    (171, "path found to a distant desk", "test_path_found_to_a_distant_desk"),
    (172, "path never crosses a wall", "test_path_never_crosses_a_wall"),
    (173, "every desk is reachable", "test_every_desk_is_reachable"),
    (175, "every person got a distinct desk", "test_every_person_got_a_distinct_desk"),
)


def test_all_thirty_harness_call_sites_are_accounted_for() -> None:
    assert len(HARNESS_CHECKS) == 30
    assert len({line for line, _, _ in HARNESS_CHECKS}) == 30, "duplicate line numbers"


def test_twenty_nine_assertions_are_ported() -> None:
    ported = [name for _, _, name in HARNESS_CHECKS if name is not None]
    assert len(ported) == 29
    assert len(set(ported)) == 29, "two harness checks mapped to one test"


def test_every_ported_check_has_a_test_function_here() -> None:
    """A mapping that names a test which does not exist is worse than no mapping."""
    import sys

    module = sys.modules[__name__]
    missing = [
        name for _, _, name in HARNESS_CHECKS if name is not None and not hasattr(module, name)
    ]
    assert not missing, f"mapped to tests that do not exist: {missing}"


def test_the_thirtieth_assertion_is_accounted_for(observed: dict[str, Any]) -> None:
    """`harness.js:159` — `'second item also reaches a decision'`, asserting false.

    It lives in the `else` of `if (w3.status === 'blocked')`, so it fires only when the
    second item has *not* reached its checkpoint, and exists to report that failure. The
    condition that keeps it unreachable is asserted here directly: on the default path
    the second item does reach its checkpoint, so the branch is never taken.
    """
    assert observed["second_status_after_70s"] == sim.STATUS_BLOCKED, (
        "the second item failed to reach its decision point, which is the only "
        "circumstance in which the harness's 30th check would execute"
    )


# =========================================================================
# Golden vectors — the Python side
# =========================================================================


def _load(name: str) -> dict[str, Any]:
    """Load a fixture, failing loudly rather than skipping when it is absent.

    A skipped golden-vector test is worse than a missing one: the suite reports green
    while the only guard on the duplicated client arithmetic is not running.
    """
    path = GOLDEN / name
    if not path.exists():
        raise AssertionError(
            f"golden fixture {name} is missing from {GOLDEN}. Regenerate it with "
            "`uv run python scripts/generate_golden.py`. This is a failure, not a skip: "
            "these vectors are the only build-time guard on logic implemented in both "
            "Python and TypeScript."
        )
    return json.loads(path.read_text())


def test_the_fixture_directory_exists() -> None:
    assert GOLDEN.is_dir(), f"{GOLDEN} is missing"


@pytest.mark.parametrize("name", ["walk.json", "clock.json", "ceo.json", "palette.json"])
def test_the_fixtures_are_present_and_loadable(name: str) -> None:
    assert _load(name)


def test_a_missing_fixture_fails_rather_than_skipping() -> None:
    with pytest.raises(AssertionError, match="missing"):
        _load("no-such-vector.json")


def test_golden_walk_vector_matches_the_kernel() -> None:
    """The vectors must describe this kernel, not a past one.

    Regenerating is a deliberate act; this catches a vector that has gone stale against
    the code it is supposed to pin.
    """
    vector = _load("walk.json")

    assert int(vector["speed"]["numerator"]) == simtime.WALK_TILES_NUMERATOR
    assert int(vector["speed"]["denominator"]) == simtime.WALK_TILES_DENOMINATOR

    for case in vector["tiles_progressed"]:
        assert simtime.tiles_progressed(int(case["elapsed"])) == int(case["tiles"])
    for case in vector["milli_tiles_progressed"]:
        assert simtime.milli_tiles_progressed(int(case["elapsed"])) == int(case["milli"])
    for case in vector["walk_duration_ticks"]:
        assert simtime.walk_duration_ticks(int(case["distance"])) == int(case["ticks"])


def test_golden_walk_vector_covers_values_above_2_53() -> None:
    """The point of the vector: a double-based port fails here rather than in production."""
    vector = _load("walk.json")
    elapsed = [int(case["elapsed"]) for case in vector["milli_tiles_progressed"]]
    assert any(value > 2**53 for value in elapsed)

    # And the answer for such a value must itself be unrepresentable as a double.
    big = max(elapsed)
    exact = simtime.milli_tiles_progressed(big)
    assert exact > 2**53
    assert float(exact) != exact or exact != int(float(exact)), (
        "pick a vector value whose result a double cannot hold exactly"
    )


def test_golden_clock_vector_matches_the_kernel() -> None:
    vector = _load("clock.json")
    for case in vector["cases"]:
        tick = int(case["tick"])
        assert simtime.day_of(tick) == int(case["day"])
        hour, minute = simtime.hour_minute_of(tick)
        assert hour == int(case["hour"])
        assert minute == int(case["minute"])
        assert simtime.is_day_boundary(tick) is case["day_boundary"]


def test_golden_ceo_vector_matches_the_kernel() -> None:
    vector = _load("ceo.json")

    assert int(vector["straight_milli_per_tick"]) == sim.CEO_STRAIGHT_MILLI_PER_TICK
    assert int(vector["diagonal_milli_per_tick"]) == sim.CEO_DIAGONAL_MILLI_PER_TICK
    for case in vector["to_tile"]:
        assert sim.to_tile(int(case["milli"])) == int(case["tile"])


def test_golden_palette_vector_covers_every_person() -> None:
    vector = _load("palette.json")
    covered = {case["id"] for case in vector["cases"]}
    for person in roster.PEOPLE:
        assert person.id in covered, f"no palette vector for {person.id}"


def test_golden_palette_vector_exercises_the_32_bit_wraparound() -> None:
    """A Python port with unbounded integers agrees on short ids and drifts on long ones.

    So at least one case has to be long enough to wrap the accumulator, or the vector
    proves only the easy half.
    """
    vector = _load("palette.json")
    assert any(len(case["id"]) > 40 for case in vector["cases"])
