"""Morale, per person, with a feedback path back into the kernel.

R38 asks for at least one degraded signal that feeds back as a kernel input, and this is it:
sustained low morale slows a person down, and a longer run of it removes them. That is what
lets load rise without any CEO action, which is what makes a run produce an outcome nobody
scripted.

**Morale is per-person state; the company metric is the roster aggregate** (R56). The
distinction is the whole point. If morale were a single company number, one overloaded
specialist would slow every employee and could trigger attrition among people who were never
overloaded — the feedback would be indiscriminate, and the CEO could not act on it because it
would not point anywhere.

**Decision effects still move the company number, and they do it by moving everyone.** A
company-wide decision applies its delta to each person, so the aggregate moves by exactly the
authored figure. Only load-driven degradation is targeted. That keeps authored consequences
readable while letting the feedback loop be specific.

**Two guards, and without either the loop has no exit** (R57):

* the burn multiplier has a **floor**, so a demoralised person still makes progress;
* **hiring items are exempt** from morale degradation, so the one lever that fixes overload
  cannot itself be throttled by the overload.

Without both, a single attrition event starts a spiral no CEO action can arrest — the
department is short-handed, so load rises, so morale falls, so throughput falls, so the
hiring item that would fix it takes longer, forever.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from simcore import people as roster
from simcore.rates import Multiplier

#: Everyone starts here, so the aggregate matches the prototype's authored 72.
INITIAL_MORALE = 72

#: Below this, the clock starts counting toward degradation.
MORALE_THRESHOLD = 40

#: Consecutive business days below the threshold before throughput degrades.
DEGRADE_AFTER_DAYS = 2

#: Consecutive business days below the threshold before someone leaves.
ATTRITION_AFTER_DAYS = 5

#: How much a demoralised person slows, and the floor that stops it becoming a standstill.
MORALE_DEGRADE = Multiplier(3, 5)
MORALE_DEGRADE_FLOOR = Multiplier(1, 2)

#: Morale lost per day by each member of a department over its ceiling.
OVER_CEILING_MORALE_PER_DAY = 4


@dataclass(slots=True)
class PersonMorale:
    value: int = INITIAL_MORALE
    #: Consecutive business days below the threshold. Reset by a single day above it, so
    #: recovery is real rather than merely slowing the decline.
    days_below: int = 0

    def to_state(self) -> dict[str, Any]:
        return {"value": self.value, "days_below": self.days_below}


def new_morale() -> dict[str, PersonMorale]:
    return {person.id: PersonMorale() for person in roster.PEOPLE}


def company_morale(morale: dict[str, PersonMorale]) -> int:
    """The roster aggregate, which is what the `morale` metric shows.

    An integer mean. With everyone equal it is exactly the individual value, which is what
    keeps an authored company-wide delta legible in the metric.
    """
    if not morale:
        return 0
    return sum(person.value for person in morale.values()) // len(morale)


def apply_company_delta(morale: dict[str, PersonMorale], delta: int) -> int:
    """Apply an authored company-wide effect to everyone, and report the aggregate change.

    Applied per person rather than to the aggregate, because the aggregate is derived. Doing
    it the other way would leave the two disagreeing the moment anything targeted happened.
    """
    before = company_morale(morale)
    for person in morale.values():
        person.value = max(0, min(100, person.value + delta))
    return company_morale(morale) - before


def apply_person_delta(morale: dict[str, PersonMorale], person_id: str, delta: int) -> None:
    person = morale[person_id]
    person.value = max(0, min(100, person.value + delta))


def degrade_multiplier(morale: dict[str, PersonMorale], person_id: str) -> Multiplier:
    """This person's burn multiplier, read from their own morale (R56).

    Floored, per R57.
    """
    person = morale.get(person_id)
    if person is None or person.days_below < DEGRADE_AFTER_DAYS:
        return Multiplier(1, 1)
    return (
        MORALE_DEGRADE_FLOOR
        if MORALE_DEGRADE.is_below(MORALE_DEGRADE_FLOOR)
        else MORALE_DEGRADE
    )


def roll_day(morale: dict[str, PersonMorale]) -> dict[str, int]:
    """Advance each person's consecutive-days-below counter at a day boundary.

    Returns the counters that changed, so the day-boundary event can record them — the
    report has to be able to explain why someone slowed down or left.
    """
    changed: dict[str, int] = {}
    for person_id, person in morale.items():
        before = person.days_below
        if person.value < MORALE_THRESHOLD:
            person.days_below += 1
        else:
            person.days_below = 0
        if person.days_below != before:
            changed[person_id] = person.days_below
    return changed


def attrition_candidate(
    morale: dict[str, PersonMorale], director_id: str, present: set[str]
) -> str | None:
    """Who leaves, if anyone: the lowest-morale non-director still in this line (R38).

    Directors never leave. That is not sentiment about seniority — a department with no
    director has no reporting line to assign through and no one to allocate a draw across,
    so removing one would make the department unreachable rather than overloaded.

    Ties break on roster order, so the answer is deterministic.
    """
    members = [
        person_id
        for person_id in roster.reporting_lines()[director_id]
        if person_id != director_id and person_id in present
    ]
    eligible = [
        person_id
        for person_id in members
        if morale[person_id].days_below >= ATTRITION_AFTER_DAYS
    ]
    if not eligible:
        return None
    return min(eligible, key=lambda person_id: (morale[person_id].value, person_id))


def to_state(morale: dict[str, PersonMorale]) -> dict[str, Any]:
    return {person_id: person.to_state() for person_id, person in morale.items()}
