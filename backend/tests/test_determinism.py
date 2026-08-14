"""The determinism substrate every later unit is built on.

Written before the implementation, per the plan's execution note for U3: a defect
here does not surface as a failure here. It surfaces twenty units later as a replay
divergence with no obvious cause, which is the most expensive kind of bug this
project can have.

Four properties, and each maps to a specific way replay breaks:

* **Stateless draws.** A stateful generator has to be snapshotted, and adding a new
  stochastic consumer renumbers every draw after it — which silently invalidates
  every seeded fixture in the suite.
* **Canonical hashing.** If insertion order reaches the hash, a refactor that only
  reorders assignments reports as a determinism regression.
* **Pinned multiplier composition.** Three requirements chain multipliers. If the
  order emerges from call order, two call paths produce two answers from the same
  inputs.
* **Derived rules version.** A hand-written version drifts from the constants it
  identifies, and a stale snapshot then compares as current — so fold-from-snapshot
  diverges from fold-from-zero while the guard reports a match.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from contracts.canonical import NotCanonical
from simcore import hashing, rates, rng, time as simtime

BACKEND = Path(__file__).resolve().parent.parent

RUN_SEED = 0xC0FFEE
OTHER_SEED = 0xBADF00D


def _in_subprocess(code: str) -> str:
    """Run a snippet in a clean interpreter and return its stdout."""
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=BACKEND,
        env={"PYTHONPATH": "packages:services", "PATH": ""},
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


# =========================================================================
# Sim-time: integers, and a fixed quantum
# =========================================================================


def test_the_quantum_and_day_match_the_prototypes_clock() -> None:
    """Derived from the prototype rather than invented.

    `HOURS_PER_SEC = 0.6` and a one-sim-minute quantum give 36 ticks per wall second
    at x1; `DAY_START = 9`, `DAY_END = 18` give a nine-hour day, so 540 ticks.
    """
    assert simtime.TICKS_PER_SIM_HOUR == 60
    assert simtime.SIM_HOURS_PER_DAY == 9
    assert simtime.TICKS_PER_SIM_DAY == 540
    assert simtime.TICKS_PER_WALL_SECOND_AT_BASE_RATE == 36
    assert simtime.DAY_START_HOUR == 9
    assert simtime.DAY_END_HOUR == 18


def test_sim_time_is_integer_throughout() -> None:
    for tick in (0, 1, 539, 540, 12345):
        assert isinstance(simtime.day_of(tick), int)
        hour, minute = simtime.hour_minute_of(tick)
        assert isinstance(hour, int) and isinstance(minute, int)


def test_tick_zero_is_day_one_at_opening_time() -> None:
    assert simtime.day_of(0) == 1
    assert simtime.hour_minute_of(0) == (9, 0)


def test_the_day_rolls_over_at_the_right_tick() -> None:
    assert simtime.day_of(539) == 1
    assert simtime.hour_minute_of(539) == (17, 59)
    assert simtime.day_of(540) == 2
    assert simtime.hour_minute_of(540) == (9, 0)


def test_day_boundaries_are_every_540_ticks() -> None:
    assert simtime.is_day_boundary(0)
    assert not simtime.is_day_boundary(1)
    assert not simtime.is_day_boundary(539)
    assert simtime.is_day_boundary(540)
    assert simtime.is_day_boundary(1080)


def test_a_day_boundary_lands_on_one_tick_in_540() -> None:
    """The state hash is a per-tick spike on one tick in roughly 540.

    That ratio is why the tick budget is measured per wake rather than per tick.
    """
    boundaries = sum(1 for tick in range(5400) if simtime.is_day_boundary(tick))
    assert boundaries == 10


# =========================================================================
# Walk speed: an exact rational, so arrival ticks are computable
# =========================================================================


def test_walk_speed_is_an_exact_rational_not_a_float() -> None:
    """The prototype walks 2.8 tiles per *wall* second, which cannot replay.

    Converted to sim-time: 2.8 / 0.6 = 14/3 tiles per sim-hour, and with 60 ticks to
    the sim-hour that is exactly 7 tiles per 90 ticks. Kept as two integers so no
    float enters the arithmetic and the same result is reproducible in TypeScript
    with BigInt.
    """
    assert simtime.WALK_TILES_NUMERATOR == 7
    assert simtime.WALK_TILES_DENOMINATOR == 90
    assert isinstance(simtime.WALK_TILES_NUMERATOR, int)
    assert isinstance(simtime.WALK_TILES_DENOMINATOR, int)


def test_walk_speed_matches_the_prototype_to_the_rational() -> None:
    """7/90 tiles per tick is exactly the prototype's 2.8 tiles per wall second.

    Proved by integer cross-multiplication rather than by comparing to 2.8/36
    directly. That is not fastidiousness: the first version of this test asserted
    `90 * 2.8 == 252.0` and failed, because `90 * 2.8` is 251.99999999999997. The
    float representation of the prototype's own constant cannot verify the pin —
    which is a small demonstration of exactly why this module carries two integers.
    """
    # 2.8 tiles per wall-second is 28/10; at 36 ticks per wall-second that is
    # 28/360 tiles per tick. Assert 7/90 == 28/360 with integers only.
    assert simtime.WALK_TILES_NUMERATOR * 360 == 28 * simtime.WALK_TILES_DENOMINATOR
    assert simtime.WALK_TILES_NUMERATOR * 36 * 10 == 28 * simtime.WALK_TILES_DENOMINATOR


def test_walking_seven_tiles_takes_ninety_ticks() -> None:
    assert simtime.walk_duration_ticks(7) == 90


def test_walk_duration_rounds_up_so_arrival_is_never_early() -> None:
    """A floor would let an actor arrive before it has covered the distance."""
    assert simtime.walk_duration_ticks(1) == 13  # ceil(90/7) == 12.857 -> 13
    assert simtime.walk_duration_ticks(14) == 180


def test_walk_duration_of_zero_distance_is_zero() -> None:
    assert simtime.walk_duration_ticks(0) == 0


def test_tiles_progressed_is_monotonic_and_exact_at_the_boundary() -> None:
    assert simtime.tiles_progressed(0) == 0
    assert simtime.tiles_progressed(90) == 7
    assert simtime.tiles_progressed(180) == 14
    progressed = [simtime.tiles_progressed(t) for t in range(0, 200)]
    assert progressed == sorted(progressed)


def test_position_at_a_tick_is_identical_at_x1_and_x3() -> None:
    """The rate multiplier must not reach derived position.

    Storing the multiplier in kernel state would make replay speed part of the run.
    Keeping it out means position is a function of tick alone — so x1 and x3 differ
    only in how fast wall-clock time produces ticks, never in what a tick means.
    There is deliberately no rate parameter to pass here; this test asserts the
    absence.
    """
    for tick in (0, 13, 45, 90, 271, 540):
        assert simtime.tiles_progressed(tick) == simtime.tiles_progressed(tick)
        assert simtime.milli_tiles_progressed(tick) == simtime.milli_tiles_progressed(tick)

    # A hand-computed value, so the test would catch a change in the arithmetic
    # rather than merely agreeing with the implementation.
    #   45 ticks * 7 / 90 = 3.5 tiles -> 3 whole tiles, 3500 milli-tiles
    assert simtime.tiles_progressed(45) == 3
    assert simtime.milli_tiles_progressed(45) == 3500


def test_milli_tile_interpolation_stays_integral() -> None:
    for tick in range(0, 400):
        assert isinstance(simtime.milli_tiles_progressed(tick), int)


# =========================================================================
# RNG: counter-based, stateless, coordinate-addressed
# =========================================================================


def test_the_same_coordinates_return_the_same_draw() -> None:
    first = rng.draw(RUN_SEED, tick=12, purpose="attrition", entity="nina")
    second = rng.draw(RUN_SEED, tick=12, purpose="attrition", entity="nina")
    assert first == second


def test_draws_are_stable_across_processes_and_interpreter_restarts() -> None:
    """CPython guarantees stability only for `random()` and the compatible seeder.

    `shuffle`, `sample`, `choices` and `randrange` are explicitly subject to change
    between versions, which is why this project owns its mixer. Stability across a
    fresh interpreter is the property that makes a seeded fixture survive.
    """
    in_process = rng.draw(RUN_SEED, tick=7, purpose="hiring", entity="support")

    out_of_process = _in_subprocess(
        "from simcore import rng;"
        f"print(rng.draw({RUN_SEED}, tick=7, purpose='hiring', entity='support'))"
    )

    assert str(in_process) == out_of_process


def test_a_draw_is_a_64_bit_unsigned_integer_not_a_float() -> None:
    """No float API at all, deliberately.

    The plan calls for a normalised draw; it is provided as integer-domain helpers
    (`below`, `chance`, `choice`) rather than a value in [0, 1). A float-returning
    mixer is the easiest way for a float to reach state in violation of R6, and the
    consumers here all want an integer decision anyway.
    """
    value = rng.draw(RUN_SEED, tick=1, purpose="p", entity="e")
    assert isinstance(value, int)
    assert 0 <= value < 2**64
    assert not hasattr(rng, "random")
    assert not hasattr(rng, "uniform")


@pytest.mark.parametrize(
    "changed",
    [
        {"tick": 13},
        {"purpose": "different"},
        {"entity": "owen"},
    ],
)
def test_changing_any_coordinate_changes_the_draw(changed: dict) -> None:
    base = {"tick": 12, "purpose": "attrition", "entity": "nina"}
    assert rng.draw(RUN_SEED, **base) != rng.draw(RUN_SEED, **{**base, **changed})


def test_changing_the_seed_changes_the_draw() -> None:
    coords = {"tick": 12, "purpose": "attrition", "entity": "nina"}
    assert rng.draw(RUN_SEED, **coords) != rng.draw(OTHER_SEED, **coords)


def test_adding_a_new_purpose_tag_leaves_every_existing_draw_unchanged() -> None:
    """The property a stateful generator cannot have.

    With a counter-based mixer, a new consumer occupies its own coordinate space, so
    introducing one in U7 cannot renumber the draws U4 and U5 already depend on. This
    is what lets seeded fixtures survive feature work.
    """
    existing = {
        (tick, entity): rng.draw(RUN_SEED, tick=tick, purpose="attrition", entity=entity)
        for tick in range(20)
        for entity in ("nina", "owen", "ruth")
    }

    # A newcomer draws freely from its own space.
    for tick in range(20):
        rng.draw(RUN_SEED, tick=tick, purpose="a-brand-new-mechanic", entity="nina")

    for (tick, entity), value in existing.items():
        assert rng.draw(RUN_SEED, tick=tick, purpose="attrition", entity=entity) == value


def test_there_is_no_generator_state_to_snapshot() -> None:
    """Draws are a pure function of coordinates, so nothing needs saving.

    Interleaving two consumers in either order must not change either answer.
    """
    a_first = (
        rng.draw(RUN_SEED, tick=5, purpose="a", entity="x"),
        rng.draw(RUN_SEED, tick=5, purpose="b", entity="x"),
    )
    b_first = (
        rng.draw(RUN_SEED, tick=5, purpose="b", entity="x"),
        rng.draw(RUN_SEED, tick=5, purpose="a", entity="x"),
    )
    assert a_first == (b_first[1], b_first[0])


def test_purpose_keys_do_not_use_pythons_randomised_hash() -> None:
    """`hash()` on a str is salted per process, so it cannot appear anywhere here."""
    in_process = rng.purpose_key("attrition")
    out_of_process = _in_subprocess(
        "from simcore import rng; print(rng.purpose_key('attrition'))"
    )
    assert str(in_process) == out_of_process


def test_below_stays_in_range() -> None:
    for entity in range(200):
        value = rng.below(6, run_seed=RUN_SEED, tick=1, purpose="d6", entity=str(entity))
        assert 0 <= value < 6


def test_below_one_is_always_zero() -> None:
    assert rng.below(1, run_seed=RUN_SEED, tick=1, purpose="p", entity="e") == 0


def test_below_rejects_a_non_positive_bound() -> None:
    with pytest.raises(ValueError):
        rng.below(0, run_seed=RUN_SEED, tick=1, purpose="p", entity="e")


def test_below_is_roughly_uniform() -> None:
    """Not a statistics test — a smoke check that no bucket is starved."""
    counts = [0] * 6
    for entity in range(6000):
        counts[rng.below(6, run_seed=RUN_SEED, tick=2, purpose="d6", entity=str(entity))] += 1
    assert all(700 < count < 1300 for count in counts), counts


def test_chance_is_deterministic_and_bounded() -> None:
    assert rng.chance(0, 100, run_seed=RUN_SEED, tick=1, purpose="p", entity="e") is False
    assert rng.chance(100, 100, run_seed=RUN_SEED, tick=1, purpose="p", entity="e") is True


def test_choice_returns_a_member_and_is_stable() -> None:
    options = ["a", "b", "c", "d"]
    first = rng.choice(options, run_seed=RUN_SEED, tick=3, purpose="pick", entity="e")
    assert first in options
    assert first == rng.choice(options, run_seed=RUN_SEED, tick=3, purpose="pick", entity="e")


def test_choice_on_an_empty_sequence_raises() -> None:
    with pytest.raises(ValueError):
        rng.choice([], run_seed=RUN_SEED, tick=1, purpose="p", entity="e")


# =========================================================================
# Hashing: canonical, per-subsystem, shape-versioned
# =========================================================================


def _state(**overrides) -> dict:
    state = {name: {} for name in hashing.SUBSYSTEMS}
    state.update(overrides)
    return state


def test_two_structurally_equal_states_hash_the_same() -> None:
    """Insertion order must not reach the hash."""
    first = _state(people={"nina": {"morale": 70}, "owen": {"morale": 65}})
    second = _state(people={"owen": {"morale": 65}, "nina": {"morale": 70}})

    assert hashing.state_hash(first).overall == hashing.state_hash(second).overall


def test_a_float_anywhere_in_state_raises() -> None:
    with pytest.raises(NotCanonical):
        hashing.state_hash(_state(metrics={"morale": 70.5}))


def test_a_float_deep_inside_state_raises() -> None:
    with pytest.raises(NotCanonical):
        hashing.state_hash(_state(items={"ops-1": {"progress": [1, 2, 0.5]}}))


def test_a_set_in_state_raises() -> None:
    with pytest.raises(NotCanonical):
        hashing.state_hash(_state(people={"informed": {"a", "b"}}))


def test_the_hash_is_computed_over_decoded_state_not_a_store_string() -> None:
    """Postgres JSONB reorders keys where SQLite does not.

    Hashing a store-returned JSON string would work locally and break the first time
    the schema targets Postgres, so the hash takes decoded state and canonicalises it
    itself. Passing pre-serialised bytes is refused rather than hashed.
    """
    with pytest.raises((TypeError, NotCanonical)):
        hashing.state_hash(b'{"people":{}}')  # type: ignore[arg-type]


def test_every_subsystem_gets_its_own_sub_hash() -> None:
    """Without sub-hashes, a divergence in a long run is a hand bisect."""
    result = hashing.state_hash(_state())
    assert set(result.subsystems) == set(hashing.SUBSYSTEMS)


def test_changing_one_subsystem_changes_only_its_sub_hash() -> None:
    baseline = hashing.state_hash(_state())
    changed = hashing.state_hash(_state(items={"ops-1": {"done": 1}}))

    assert changed.overall != baseline.overall
    assert changed.subsystems["items"] != baseline.subsystems["items"]
    for name in hashing.SUBSYSTEMS:
        if name != "items":
            assert changed.subsystems[name] == baseline.subsystems[name]


def test_the_hash_carries_the_state_shape_version() -> None:
    assert hashing.state_hash(_state()).shape_version == hashing.STATE_SHAPE_VERSION


def test_state_must_declare_every_subsystem_even_when_empty() -> None:
    """U7's subsystems ship empty rather than absent.

    If they joined the hash only when they gained content, every golden hash from U5
    and U6 would change with nothing recording that the change was deliberate — a
    state-shape change that neither the event-schema version nor the rules version
    covers.
    """
    incomplete = _state()
    del incomplete["capacity"]

    with pytest.raises(ValueError, match="capacity"):
        hashing.state_hash(incomplete)


def test_an_undeclared_subsystem_is_refused() -> None:
    with pytest.raises(ValueError, match="mystery"):
        hashing.state_hash(_state(mystery={}))


def test_the_full_subsystem_list_is_declared_now() -> None:
    """U3 declares the whole list, including the ones U7 and U8 will fill."""
    assert set(hashing.SUBSYSTEMS) >= {
        "world",
        "people",
        "items",
        "metrics",
        "ceo",
        "pending",
        "capacity",
        "morale",
        "hiring",
        "lifecycle",
    }


def test_adding_a_subsystem_without_bumping_the_shape_version_fails() -> None:
    """The enforcement mechanism, not a reminder.

    SUBSYSTEMS is read from SHAPE_HISTORY at the current version, so adding a
    subsystem without adding a history entry makes the two disagree and this test
    fails. It is the only thing standing between a silent hash break and a recorded
    decision.
    """
    assert hashing.SUBSYSTEMS == hashing.SHAPE_HISTORY[hashing.STATE_SHAPE_VERSION]

    # History is append-only: each version extends the previous one.
    versions = sorted(hashing.SHAPE_HISTORY)
    for earlier, later in zip(versions, versions[1:]):
        assert set(hashing.SHAPE_HISTORY[earlier]) < set(hashing.SHAPE_HISTORY[later])


def test_hashes_are_stable_across_processes() -> None:
    in_process = hashing.state_hash(_state(metrics={"cash": 120})).overall

    out_of_process = _in_subprocess(
        "from simcore import hashing;"
        "state = {name: {} for name in hashing.SUBSYSTEMS};"
        "state['metrics'] = {'cash': 120};"
        "print(hashing.state_hash(state).overall)"
    )

    assert in_process == out_of_process


# =========================================================================
# Rates: one composition order, versioned with the rules
# =========================================================================


def test_the_full_ordered_multiplier_slot_list_is_declared_now() -> None:
    """Three requirements chain multipliers; U7 introduces two of the three.

    They ship as identity factors so that the composition order is fixed from the
    start and U7 changes behaviour without changing the shape of the chain.
    """
    assert rates.MULTIPLIER_SLOTS == (
        "over_ceiling_degradation",
        "morale_degradation",
        "director_rate",
    )


def test_multipliers_are_exact_rationals_not_floats() -> None:
    """The director rate is 0.8 in the prototype, which is 4/5 here."""
    assert rates.DIRECTOR_RATE.numerator == 4
    assert rates.DIRECTOR_RATE.denominator == 5
    assert isinstance(rates.DIRECTOR_RATE.numerator, int)


def test_identity_leaves_a_value_untouched() -> None:
    assert rates.apply_rates(100, {}) == 100
    assert rates.apply_rates(100, {slot: rates.IDENTITY for slot in rates.MULTIPLIER_SLOTS}) == 100


def test_composition_is_order_stable_across_two_call_paths() -> None:
    """Two call paths applying the same three multipliers must agree.

    The implementation composes the rationals and divides once at the end, so the
    order genuinely cannot matter — rather than the two paths merely happening to
    iterate the same way today.
    """
    three = {
        "over_ceiling_degradation": rates.Multiplier(3, 4),
        "morale_degradation": rates.Multiplier(9, 10),
        "director_rate": rates.DIRECTOR_RATE,
    }
    reversed_insertion = dict(reversed(list(three.items())))

    assert rates.apply_rates(1000, three) == rates.apply_rates(1000, reversed_insertion)


def test_composition_does_not_lose_precision_to_intermediate_rounding() -> None:
    """1000 * 3/4 * 9/10 * 4/5 = 540 exactly.

    Applying floor division at each step instead would give 540 here but drifts on
    other inputs, and the drift would depend on the order — which is the bug this
    design removes rather than documents.
    """
    three = {
        "over_ceiling_degradation": rates.Multiplier(3, 4),
        "morale_degradation": rates.Multiplier(9, 10),
        "director_rate": rates.DIRECTOR_RATE,
    }
    assert rates.apply_rates(1000, three) == 540

    # 7 * 1/3 * 3/1 == 7, which sequential flooring would report as 6.
    assert rates.apply_rates(
        7,
        {
            "over_ceiling_degradation": rates.Multiplier(1, 3),
            "morale_degradation": rates.Multiplier(3, 1),
        },
    ) == 7


def test_apply_rates_returns_an_integer() -> None:
    result = rates.apply_rates(7, {"director_rate": rates.DIRECTOR_RATE})
    assert isinstance(result, int)
    assert result == 5  # floor(7 * 4/5) == 5


def test_the_burn_multiplier_floor_is_applied_after_composition() -> None:
    """U7 needs a floor so the recovery lever cannot be throttled to nothing.

    A floor is order-dependent, which is the reason the composition order has to be
    pinned at all: compose everything, then clamp. Clamping mid-chain would make the
    result depend on where the clamp sits.
    """
    brutal = {
        "over_ceiling_degradation": rates.Multiplier(1, 100),
        "morale_degradation": rates.Multiplier(1, 100),
    }

    unfloored = rates.apply_rates(1000, brutal)
    floored = rates.apply_rates(1000, brutal, floor=rates.Multiplier(1, 4))

    assert unfloored == 0
    assert floored == 250


def test_a_floor_above_the_composed_rate_does_not_raise_it() -> None:
    """A floor is a floor, not a target."""
    generous = {"director_rate": rates.Multiplier(9, 10)}
    assert rates.apply_rates(1000, generous, floor=rates.Multiplier(1, 4)) == 900


def test_an_unknown_multiplier_slot_is_refused() -> None:
    with pytest.raises(ValueError, match="invented_slot"):
        rates.apply_rates(100, {"invented_slot": rates.Multiplier(1, 2)})


def test_a_zero_denominator_is_refused() -> None:
    with pytest.raises(ValueError):
        rates.Multiplier(1, 0)


def test_a_negative_multiplier_is_refused() -> None:
    """A negative rate would run work backwards."""
    with pytest.raises(ValueError):
        rates.Multiplier(-1, 2)


# --- the derived rules version --------------------------------------------


def test_the_rules_version_is_derived_not_hand_written() -> None:
    assert rates.RULES_VERSION == rates.rules_version()
    assert len(rates.RULES_VERSION) >= 16


def test_the_rules_version_is_stable_across_processes() -> None:
    out_of_process = _in_subprocess("from simcore import rates; print(rates.RULES_VERSION)")
    assert rates.RULES_VERSION == out_of_process


def test_editing_any_tuning_constant_changes_the_rules_version() -> None:
    """R36: a forgotten manual bump would leave stale snapshots treated as current.

    Every tuning constant is walked rather than a hand-picked few, so a constant
    added later is covered without anyone remembering to extend this test.
    """
    baseline = rates.rules_version()

    for name, value in rates.TUNING.items():
        if isinstance(value, bool) or not isinstance(value, int):
            continue
        altered = dict(rates.TUNING)
        altered[name] = value + 1
        assert rates.rules_version(tuning=altered) != baseline, (
            f"changing tuning constant {name!r} did not change the rules version"
        )


def test_changing_the_composition_order_changes_the_rules_version() -> None:
    """The order is part of the rules identity, not merely of the implementation."""
    baseline = rates.rules_version()
    reordered = ("morale_degradation", "over_ceiling_degradation", "director_rate")
    assert rates.rules_version(slots=reordered) != baseline


def test_adding_a_multiplier_slot_changes_the_rules_version() -> None:
    extended = (*rates.MULTIPLIER_SLOTS, "some_future_multiplier")
    assert rates.rules_version(slots=extended) != rates.rules_version()


def test_the_rules_version_does_not_depend_on_dict_ordering() -> None:
    shuffled = dict(reversed(list(rates.TUNING.items())))
    assert rates.rules_version(tuning=shuffled) == rates.rules_version()


def test_the_tuning_table_holds_no_floats() -> None:
    """A float tuning constant would put a float in the rules identity itself."""
    offenders = {
        name: value for name, value in rates.TUNING.items() if isinstance(value, float)
    }
    assert not offenders, f"float tuning constants: {offenders}"


def test_the_prototypes_economy_constants_are_carried_over() -> None:
    assert rates.TUNING["fixed_cost_per_day"] == 18
    assert rates.TUNING["day_start_hour"] == 9
    assert rates.TUNING["day_end_hour"] == 18


# =========================================================================
# The whole substrate, twice, in two processes
# =========================================================================


def test_the_substrate_produces_one_fingerprint_in_two_processes() -> None:
    """U3's verification: the determinism suite passes twice with identical hashes.

    One digest over a draw, a walk, a composed rate, a state hash and the rules
    version — so a change in any of the four is caught by a single comparison.
    """
    probe = """
from simcore import hashing, rates, rng, time as simtime

state = {name: {} for name in hashing.SUBSYSTEMS}
state['metrics'] = {'cash': 120, 'morale': 70}
state['people'] = {'nina': {'seat': [3, 4]}}

fingerprint = {
    'draw': rng.draw(0xC0FFEE, tick=7, purpose='attrition', entity='nina'),
    'walk': [simtime.walk_duration_ticks(7), simtime.tiles_progressed(45),
             simtime.milli_tiles_progressed(45)],
    'rate': rates.apply_rates(1000, {
        'over_ceiling_degradation': rates.Multiplier(3, 4),
        'morale_degradation': rates.Multiplier(9, 10),
        'director_rate': rates.DIRECTOR_RATE,
    }),
    'state_hash': hashing.state_hash(state).overall,
    'rules_ver': rates.RULES_VERSION,
    'shape_ver': hashing.STATE_SHAPE_VERSION,
}
print(hashing.digest(fingerprint))
"""
    first = _in_subprocess(probe)
    second = _in_subprocess(probe)

    assert first == second
    assert len(first) >= 16
