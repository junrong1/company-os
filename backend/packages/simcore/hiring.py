"""Hiring: the one response to overload that adds capacity, and what it costs.

Hiring is a **work item** routed through the People department (R23), not a menu action. It
consumes sim-time before the hire arrives, which is what makes overload something you have to
see coming rather than something you fix on the tick you notice it.

**A hire costs cash on arrival and adds a recurring salary to the daily fixed cost** (R24).
The recurring half is what makes hiring a real trade against automating work: automation
reduces a department's draw and therefore its burn (R60), hiring raises headcount and
therefore the burn. Without both terms pulling, one strictly dominates.

**Which room, and which recruiter, come from the scenario.** `target_room_for` lived here and
read a module-level roster; it is `Scenario.room_of_line` now, next to `Scenario.recruiter`,
because both answers are properties of the company the run was created against. A new hire
sits in their director's own room: Priya's split between room and reporting line is authored
sample data, deliberately, and a *new* hire has no reason to inherit that mismatch.

**The floor planner guarantees a desk inside the hire's own department, or refuses with a
reason** (R25). Refusing is the interesting half. Seating someone on an arbitrary tile would
be the easy fallback and would quietly break the thing the office is *for*: you can see who
works where. A department that cannot fit another desk is a real constraint, and saying so is
more useful than pretending otherwise.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from simcore.world import Floor, walkable

#: Cash on arrival, in $K — the unit the metric displays.
HIRE_COST_CASH = 90

#: Added to the daily fixed cost for as long as they are employed, in $K per day.
HIRE_SALARY_PER_DAY = 4

#: How much work a hiring item is, in sim-hours. Long enough that overload has to be
#: anticipated rather than reacted to.
HIRE_EFFORT_HOURS = 12

#: A hire's starting morale. Deliberately at the roster's initial value rather than higher:
#: a new arrival who lifted the aggregate would make hiring a morale lever, which it is not.
HIRE_INITIAL_MORALE = 72


@dataclass(slots=True)
class Hire:
    """One requested hire, and where it got to."""

    request_id: str
    #: The reporting line they will join.
    director_id: str
    person_id: str
    #: The work item that has to finish before they arrive.
    item_id: str
    status: str = "requested"  # requested | arrived | refused
    seat: tuple[int, int] | None = None
    refusal: str = ""

    def to_state(self) -> dict[str, Any]:
        return {
            "director": self.director_id,
            "person": self.person_id,
            "item": self.item_id,
            "status": self.status,
            "seat": list(self.seat) if self.seat else None,
            "refusal": self.refusal,
        }


def hire_person_id(director_id: str, ordinal: int) -> str:
    """A stable, derived id, so a replay reconstructs the same person.

    Derived from the line and an ordinal rather than minted randomly: a random id would be a
    value the kernel could not recompute, which would make it an input that has to be logged
    (origin R3) for no benefit.
    """
    return f"hire_{director_id.removeprefix('dir_')}_{ordinal}"


def plan_desk(
    floor: Floor, department_room: str, taken: set[tuple[int, int]]
) -> tuple[tuple[int, int] | None, str]:
    """Find a desk for a new hire in their own department's room.

    Three attempts, cheapest first, mirroring what the floor planner already does for the
    shipped roster:

    1. an unused authored desk slot;
    2. any free walkable tile in the room, which is the same last resort `assign_seats` uses
       when a room is too small for everyone in it;
    3. refuse, naming the room.

    Returns `(seat, refusal)`; exactly one is populated.
    """
    room = next((candidate for candidate in floor.rooms if candidate.id == department_room), None)
    if room is None:
        return None, f"there is no {department_room} room on this floor"

    for slot in room.slots:
        if slot not in taken:
            return slot, ""

    for y in range(room.y1, room.y2 + 1):
        for x in range(room.x1, room.x2 + 1):
            if (x, y) not in taken and walkable(floor, x, y):
                return (x, y), ""

    return None, (
        f"{room.name} has no room for another desk: every authored slot and every free tile "
        f"in the room is taken. The hire is refused rather than seated somewhere arbitrary — "
        f"a person sitting outside their department would make the floor stop meaning "
        f"anything."
    )


def salary_total(hires: dict[str, Hire]) -> int:
    """Recurring daily cost of everyone who has arrived."""
    return sum(
        HIRE_SALARY_PER_DAY for hire in hires.values() if hire.status == "arrived"
    )


def to_state(hires: dict[str, Hire]) -> dict[str, Any]:
    return {request_id: hire.to_state() for request_id, hire in hires.items()}
