"""Multiplier composition, the tuning table, and the derived rules version.

Three separate requirements chain rate multipliers — over-ceiling degradation, morale
degradation, and the director rate — so the composition order is itself part of the
rules and is pinned here rather than emerging from the order callers happen to apply
things in.

**Multipliers are exact rationals, not floats.** The prototype's director rate is
`0.8`; here it is `4/5`. All multipliers compose by accumulating numerators and
denominators, with a single floor division at the end. That has a property worth
stating plainly: the result cannot depend on the order, because there is no
intermediate rounding to be ordered. Applying `//` at each step would make
`7 * 1/3 * 3/1` come out as 6 in one order and 7 in another.

**The order still matters, because one operation is not commutative.** U7 gives the
burn multiplier a floor, so the recovery lever cannot be throttled to nothing exactly
when it is needed. A floor applied mid-chain would give a different answer depending
on where it sat, so the rule is: compose everything, *then* clamp.

**The rules version is derived, never hand-written.** It is a digest over this tuning
table and the composition order, computed at import. A hand-maintained version drifts
from the constants it identifies, and the failure is nasty: a stale snapshot compares
as current, so fold-from-snapshot diverges from fold-from-zero while the integrity
guard reports a match.

The full ordered slot list is declared now even though U7 introduces two of the three
multipliers. Its slots ship as identity factors, so U7 changes behaviour without
changing the shape of the chain — and without changing what U5's golden vectors mean.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from simcore import hashing

# --- multipliers ----------------------------------------------------------

#: The composition order, and part of the rules identity. Changing the order or
#: adding a slot changes RULES_VERSION, which invalidates snapshots — correctly, since
#: it changes what the numbers mean.
MULTIPLIER_SLOTS: tuple[str, ...] = (
    # U7. Load above the ceiling degrades throughput.
    "over_ceiling_degradation",
    # U7. Read from the individual's morale, not the company aggregate.
    "morale_degradation",
    # U4. Directors carry work more slowly — they still have a department to run.
    "director_rate",
)


@dataclass(frozen=True, slots=True)
class Multiplier:
    """A rate factor as two integers. No float ever enters the chain."""

    numerator: int
    denominator: int

    def __post_init__(self) -> None:
        if isinstance(self.numerator, bool) or isinstance(self.denominator, bool):
            raise ValueError("a multiplier is two integers, not booleans")
        if not isinstance(self.numerator, int) or not isinstance(self.denominator, int):
            raise ValueError(
                f"a multiplier is two integers; got "
                f"{type(self.numerator).__name__}/{type(self.denominator).__name__}. "
                "R6: no float value accumulates in kernel state."
            )
        if self.denominator <= 0:
            raise ValueError(f"denominator must be positive; got {self.denominator}")
        if self.numerator < 0:
            raise ValueError(
                f"numerator cannot be negative; got {self.numerator}. A negative rate "
                "would run work backwards."
            )

    def is_below(self, other: Multiplier) -> bool:
        """Cross-multiplied comparison, so no division and no float."""
        return self.numerator * other.denominator < other.numerator * self.denominator


IDENTITY = Multiplier(1, 1)

#: The prototype's `p.rank === 'director' ? 0.8 : 1`.
DIRECTOR_RATE = Multiplier(4, 5)


def compose(multipliers: Mapping[str, Multiplier]) -> Multiplier:
    """Combine the supplied slots, in the declared order, into one rational.

    Absent slots are the identity, so a caller never has to pass a full set.
    """
    unknown = sorted(set(multipliers) - set(MULTIPLIER_SLOTS))
    if unknown:
        raise ValueError(
            f"unknown multiplier slot(s): {unknown}. The slot list is declared in "
            "MULTIPLIER_SLOTS and is part of the rules version."
        )

    numerator = 1
    denominator = 1
    for slot in MULTIPLIER_SLOTS:
        factor = multipliers.get(slot, IDENTITY)
        numerator *= factor.numerator
        denominator *= factor.denominator

    return Multiplier(numerator, denominator)


def apply_rates(
    base_units: int,
    multipliers: Mapping[str, Multiplier],
    floor: Multiplier | None = None,
) -> int:
    """Apply the multiplier chain to an integer quantity.

    Compose first, clamp second, divide once. See the module docstring for why that
    order is the whole point.
    """
    if isinstance(base_units, bool) or not isinstance(base_units, int):
        raise ValueError(f"base_units must be an integer; got {type(base_units).__name__}")

    composed = compose(multipliers)
    if floor is not None and composed.is_below(floor):
        composed = floor

    return base_units * composed.numerator // composed.denominator


# --- the tuning table -----------------------------------------------------

#: Every number that shapes behaviour, in one place, so the rules version can be a
#: digest over it. Integers only: a float here would put a float into the rules
#: identity itself.
#:
#: Later units add to this table. Doing so changes RULES_VERSION and therefore
#: invalidates existing snapshots and makes existing runs unplayable under the new
#: rules — which is correct, and is why the plan treats runs as disposable across a
#: tuning change.
TUNING: dict[str, int] = {
    # Clock, from the prototype's HOURS_PER_SEC / DAY_START / DAY_END.
    "quantum_sim_seconds": 60,
    "ticks_per_sim_hour": 60,
    "ticks_per_sim_day": 540,
    "day_start_hour": 9,
    "day_end_hour": 18,
    # Economy, from the prototype's FIXED_COST_PER_DAY.
    "fixed_cost_per_day": 18,
    # What a department's recurring draw costs per day, in hundredths of $K per authored
    # monthly hour. A draw is staffed work, so it carries into the burn (R60) — and that is
    # what gives automation a payback. Without this term, reducing a draw would move a number
    # on screen and nothing else, and returning work to the backlog would strictly dominate
    # hiring. At the authored draws this is ~17 $K/day against the prototype's 18 flat.
    "draw_cost_per_monthly_hour": 5,
    # Movement, as an exact rational (see simcore.time).
    "walk_tiles_numerator": 7,
    "walk_tiles_denominator": 90,
    # Work, from the prototype's tick function.
    "director_rate_numerator": 4,
    "director_rate_denominator": 5,
    "meeting_sim_hours": 2,
    "visit_meeting_progress_threshold_pct": 20,
}


def rules_version(
    tuning: Mapping[str, int] | None = None,
    slots: tuple[str, ...] | None = None,
) -> str:
    """Digest over the tuning table and the multiplier composition order.

    The parameters exist for the determinism suite, which asserts that editing any
    constant or reordering any slot moves this value. Production callers use
    RULES_VERSION.
    """
    return hashing.digest(
        {
            "tuning": dict(TUNING if tuning is None else tuning),
            "multiplier_order": list(MULTIPLIER_SLOTS if slots is None else slots),
        }
    )


#: Computed at import. Every integrity guard keys on this value.
RULES_VERSION: str = rules_version()

#: A human-readable label riding alongside as metadata. Never used as an identity.
RULES_LABEL = "phase-1"
