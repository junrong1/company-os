"""Baseline load: the work that exists whether or not the CEO assigns anything.

This is the mechanic that makes a run produce outcomes nobody scripted. Without it the
office is idle until the CEO acts, and every number moves only when they do.

**The draw is allocated across the whole reporting line, director included.** That is
load-bearing rather than tidy: Customer Support and People each hold exactly one
non-director on the shipped roster, so allocating across non-directors only would leave a
department with nobody to allocate across after a single attrition event — and the draw
would silently vanish rather than pressing on someone.

**Draw is deducted from available hours before queue effort burns** (R47), so it enters the
workload measure directly rather than being a separate display. **Unconsumed draw expires at
day end**, which makes the ceiling a pressure rather than a trapdoor: a department that fell
behind yesterday starts today at its authored draw, not at yesterday's plus today's.

**`manualHours` is derived from the sum of draws** (R49), not authored independently. The
unit is chosen so the conversion is the identity: draws are authored in the same hours-per-
month the metric displays, and a decision that automates work reduces a department's draw by
exactly the figure its own copy quotes. Authoring the two separately is how a report ends up
unable to reconcile its own decision consequences against its own trajectory.

**And the draw carries into daily fixed cost** (R60), because a recurring draw is staffed
work. Without that term the economy is one-directional: automating work would reduce a
number on screen and nothing else, and returning work to the backlog would strictly dominate
hiring.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from simcore import time as simtime
from simcore.rates import Multiplier

if TYPE_CHECKING:  # pragma: no cover - annotations only
    from simcore.scenario import Scenario

#: Business days in a month, for converting a monthly draw into a daily one. 20 divides
#: 3600 exactly, so a day's draw in effort units is always a whole number.
BUSINESS_DAYS_PER_MONTH = 20

#: One sim-hour of effort, matching `items.EFFORT_UNITS_PER_SIM_HOUR`.
EFFORT_UNITS_PER_SIM_HOUR = 3600

#: Effort units one authored hour-per-month of draw consumes each business day.
#: 3600 / 20 = 180.
DRAW_UNITS_PER_MONTHLY_HOUR = EFFORT_UNITS_PER_SIM_HOUR // BUSINESS_DAYS_PER_MONTH

#: The display maximum, replacing the prototype's 500.
#:
#: Presentation rather than content, which is why it stays a constant while the draws it scales
#: against are authored per scenario. Every draw a scenario may declare is bounded by
#: `scenario.MAX_DRAW_HOURS_PER_MONTH`, so a bar reading against this can overflow but never
#: mislead about direction — and a scenario-authored display maximum would be a second number
#: an author could set inconsistently with the draws it describes.
MANUAL_HOURS_DISPLAY_MAX = 400

#: Hours a person has in a business day, before draw and queue effort.
AVAILABLE_SIM_HOURS_PER_DAY = simtime.SIM_HOURS_PER_DAY

#: Load is carried in per-mille rather than percent, so a four-person department's share
#: divides without rounding to something misleading.
LOAD_SCALE = 1000

#: Above this, throughput degrades and morale drops. Load may exceed it — R22 is explicit
#: that over-ceiling load never blocks assignment.
LOAD_CEILING = LOAD_SCALE

#: How hard over-ceiling load bites. At twice the ceiling the head item's burn rate is
#: multiplied by 1/2; the shape is deliberately gentle, because the ceiling is a pressure.
OVER_CEILING_FLOOR = Multiplier(1, 4)


@dataclass(slots=True)
class DepartmentCapacity:
    """One reporting line's draw for the current business day."""

    director_id: str
    #: Authored monthly draw, in the unit `manualHours` displays. Mutated by decisions.
    monthly_hours: int
    #: Effort units of draw still unconsumed today. Reset at each day boundary.
    remaining_units: int = 0
    #: Load in per-mille of available capacity, recomputed as the day runs.
    load_permille: int = 0

    def to_state(self) -> dict[str, Any]:
        return {
            "monthly_hours": self.monthly_hours,
            "remaining_units": self.remaining_units,
            "load_permille": self.load_permille,
        }


def daily_draw_units(monthly_hours: int) -> int:
    """A day's worth of an authored monthly draw, in effort units. Always integral."""
    return monthly_hours * DRAW_UNITS_PER_MONTHLY_HOUR


def new_capacity(scenario: Scenario) -> dict[str, DepartmentCapacity]:
    """Every department at its authored draw, with today's allocation not yet made.

    Insertion order is the scenario's department declaration order, not sorted. The attrition
    loop walks `state.capacity`, so two departments shedding somebody on one day emit their
    events in this order — which makes the file's own ordering part of what a run reproduces.
    """
    return {
        director: DepartmentCapacity(
            director_id=director,
            monthly_hours=monthly,
            remaining_units=daily_draw_units(monthly),
        )
        for director, monthly in scenario.draws.items()
    }


def manual_hours(capacity: dict[str, DepartmentCapacity]) -> int:
    """The `manualHours` metric: the sum of the draws (R49).

    Derived rather than accumulated, so it cannot drift from the draws it describes.
    """
    return sum(department.monthly_hours for department in capacity.values())


def per_member_units(department: DepartmentCapacity, headcount: int) -> int:
    """Each member's share of today's draw.

    Integer division, with the remainder left in the department rather than rounded away —
    a department whose draw does not divide evenly still consumes all of it, because the
    remainder stays in `remaining_units` and shows up as load.
    """
    if headcount <= 0:
        raise ValueError(
            f"{department.director_id} has no members to allocate a draw across. The "
            "director is always a member, so this means the reporting line is malformed."
        )
    return daily_draw_units(department.monthly_hours) // headcount


def load_permille(department: DepartmentCapacity, queued_units: int, headcount: int) -> int:
    """Load as per-mille of the department's available hours for a day.

    Workload is the department's remaining queue effort plus its baseline draw, measured
    against the hours its members actually have (R20). Both terms are in effort units, so
    this is a ratio of comparable quantities rather than a blend of a count and an estimate.
    """
    available = headcount * AVAILABLE_SIM_HOURS_PER_DAY * EFFORT_UNITS_PER_SIM_HOUR
    if available <= 0:
        return 0
    demand = queued_units + daily_draw_units(department.monthly_hours)
    return demand * LOAD_SCALE // available


def over_ceiling_multiplier(load: int) -> Multiplier:
    """How much over-ceiling load slows the head item.

    R22: it multiplies the *head item's* burn rate down rather than splitting effort across
    the queue. Splitting would make an overloaded department finish everything slowly at
    once, which reads as progress; slowing the head item makes the queue visibly stall,
    which is the signal the CEO is supposed to act on.

    Floored, so a department cannot be slowed to a standstill it can never recover from.
    """
    if load <= LOAD_CEILING:
        return Multiplier(1, 1)

    # Ceiling over load: at 2x the ceiling the rate halves, at 4x it quarters.
    candidate = Multiplier(LOAD_CEILING, load)
    return OVER_CEILING_FLOOR if candidate.is_below(OVER_CEILING_FLOOR) else candidate


def consume(department: DepartmentCapacity, units: int) -> int:
    """Draw down today's baseline. Returns how much was actually consumed."""
    taken = min(department.remaining_units, max(0, units))
    department.remaining_units -= taken
    return taken


def expire_and_refresh(capacity: dict[str, DepartmentCapacity]) -> dict[str, int]:
    """Day boundary: unconsumed draw expires, and today's is issued.

    Expiring rather than accumulating is what makes the ceiling a pressure and not a
    trapdoor — a department that fell behind does not start the next day already over.

    Returns what expired per department, so the day-boundary event can record it: a draw
    that silently vanished would make the load signal unexplainable.
    """
    expired: dict[str, int] = {}
    for director, department in capacity.items():
        if department.remaining_units:
            expired[director] = department.remaining_units
        department.remaining_units = daily_draw_units(department.monthly_hours)
    return expired


def apply_draw_change(
    capacity: dict[str, DepartmentCapacity], director_id: str, monthly_delta: int
) -> int:
    """Change a department's recurring draw, and report the effective delta.

    A draw cannot go below zero, and the *effective* delta is returned rather than the
    requested one — the report has to reconcile decision consequences against the
    `manualHours` trajectory, and it cannot do that if a clamped reduction is reported at
    its full requested size.
    """
    department = capacity[director_id]
    before = department.monthly_hours
    department.monthly_hours = max(0, before + monthly_delta)

    # Today's remaining draw follows the change, so a decision is felt in the current day
    # rather than only from tomorrow.
    department.remaining_units = min(
        department.remaining_units, daily_draw_units(department.monthly_hours)
    )
    return department.monthly_hours - before


def to_state(capacity: dict[str, DepartmentCapacity]) -> dict[str, Any]:
    return {director: department.to_state() for director, department in capacity.items()}
