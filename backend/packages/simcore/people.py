"""The roster's *mechanisms*: seating, the genesis projection, and the four questions.

The roster itself is no longer here. `PEOPLE`, `VOICE` and `DEFLECTIONS` were module
constants until U6, which is what made "add a company" a code change (M9, M13); they are
authored in `backend/scenarios/*.toml` now and reached through `simcore.scenario`. What is
left in this module is the part that is a *rule* rather than content:

**Seating**, because desks come from the generated floor plan and the plan is not authored.
A scenario says which room somebody sits in and which slot they prefer; which tile that
turns out to be depends on the grid the run was created with, so it is resolved per run.

**The genesis projection**, because what the client is told about a person is a wire
contract rather than scenario data.

**The four questions**, because the kernel prices an answer and therefore has to be the side
that decides which question was asked. A scenario authors the four replies; it does not get
to add a fifth question, and `scenario.VOICE_SLOTS` is asserted to agree with `ASK_SLOTS`.

The mismatch in the shipped data stays load-bearing and stays authored: Priya sits in
Accounting and reports to the Administration director, so "which room someone sits in" and
"whose reporting line they are in" are two different questions. Seating reads the room;
assignment, delegation and capacity read the line.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from simcore.world import Floor, walkable

if TYPE_CHECKING:  # pragma: no cover - annotations only
    from simcore.scenario import Scenario


def assign_seats(scenario: Scenario, floor: Floor) -> dict[str, tuple[int, int]]:
    """Resolve everyone's desk from the generated plan.

    A port of `assignSeats()`, including both fallbacks. The invariant it exists to
    hold is that no two people share a chair — a room too small for everyone in it
    seats the overflow on any free floor tile inside that room, and only then on the
    spawn point.

    Deterministic: roster order decides who gets contested slots, and roster order is part of
    what a scenario *is* — it is inside the content hash, so a file that reorders two people
    seats them differently and says so at the R7 guard.
    """
    taken: set[tuple[int, int]] = set()
    seats: dict[str, tuple[int, int]] = {}

    for person in scenario.people:
        room = next((r for r in floor.rooms if r.id == person.dept), None)
        slots = room.slots if room else []

        spot: tuple[int, int] | None = next(
            (slot for index, slot in enumerate(slots) if index >= person.slot and slot not in taken),
            None,
        )
        if spot is None:
            spot = next((slot for slot in slots if slot not in taken), None)

        # Last resort for a room too small for everyone in it: any free walkable tile
        # inside that room. Two people must never share a chair.
        if spot is None and room is not None:
            for y in range(room.y1, room.y2 + 1):
                for x in range(room.x1, room.x2 + 1):
                    if walkable(floor, x, y) and (x, y) not in taken:
                        spot = (x, y)
                        break
                if spot is not None:
                    break

        if spot is None:
            spot = floor.spawn

        taken.add(spot)
        seats[person.id] = spot

    return seats


def roster_to_state(
    scenario: Scenario, seats: dict[str, tuple[int, int]]
) -> dict[str, dict[str, Any]]:
    """The immutable part of the roster, as the genesis payload carries it.

    Name, initials and title are here because the client has to be able to say who it is
    talking to. Standing next to someone is the whole gesture, and a conversation headed
    `stf_ap` would name a row in a table rather than a person — which is the opposite of what
    the in-person route exists to be worth. They are scenario fields that cannot move within a
    run, so shipping them once at genesis is cheaper than a lookup the client cannot perform.

    **`responsibility`, `tools`, `mcp_servers` and `skills` ride here too** (M15), and for
    everyone rather than for whoever a model happens to answer for. The client's conversation
    surface is what shows them (U7), and shipping them at genesis is the same argument as the
    names: they are immutable, inherited by forks, and an exported run has to stay
    self-contained without the kernel's process to ask.

    They are description and nothing else (M16). Nothing in this repository turns a tool name
    into a call, and `test_scenario.py` asserts the absence of a mechanism rather than the
    absence of a call site.
    """
    return {
        person.id: {
            "name": person.name,
            "initials": person.initials,
            "title": person.title,
            "dept": person.dept,
            "mgr": person.mgr or "",
            "rank": person.rank,
            "seat": list(seats[person.id]),
            "responsibility": person.responsibility,
            "tools": list(person.tools),
            "mcp_servers": list(person.mcp_servers),
            "skills": list(person.skills),
        }
        for person in scenario.people
    }


@dataclass(frozen=True, slots=True)
class AskIntent:
    """One of the four questions worth asking, and how a typed question reaches it.

    `tacit` marks the three where undocumented knowledge lives. Those are the ones that raise
    Visibility the first time a person answers them; the bottleneck question is a number they
    would have given you anyway, so it pays nothing.
    """

    slot: str
    label: str
    tacit: bool
    keys: tuple[str, ...]


#: Ported from the prototype's `ASK_MAP` (`company-os.html:3049`), keyword sets included.
#:
#: Matching lives here rather than on the client, and deliberately: the kernel has to know
#: *which* question was asked to charge Visibility once per person per question, so a client
#: that classified the text would be handing the kernel a fact it prices without being able to
#: check. It is also where the hearing API replaces the script, and matching belongs with the
#: producer rather than with the surface that shows the answer.
#:
#: The four slots are the four a scenario authors, and `scenario.VOICE_SLOTS` declares them for
#: the format. Kept as two lists with a test that they agree, rather than one importing the
#: other: `scenario` imports nothing from here and must not, and a format that read its
#: required keys out of a matching table would make adding a keyword a file-format change.
#:
#: Order matters. The first intent whose keywords appear wins, so a question mentioning both
#: "why" and "slow" is read as a why.
ASK_INTENTS: tuple[AskIntent, ...] = (
    AskIntent("why", "Why", True, ("why", "reason", "because")),
    AskIntent(
        "exception", "Exceptions", True, ("exception", "edge", "irregular", "unusual", "special")
    ),
    AskIntent(
        "axis",
        "Who decides",
        True,
        ("who decide", "decides", "judg", "criteri", "rule", "threshold", "approve"),
    ),
    AskIntent("bottleneck", "Bottleneck", False, ("time", "bottleneck", "slow", "stuck", "long", "eating")),
)

ASK_SLOTS: tuple[str, ...] = tuple(intent.slot for intent in ASK_INTENTS)

TACIT_SLOTS: frozenset[str] = frozenset(intent.slot for intent in ASK_INTENTS if intent.tacit)


def match_intent(question: str) -> AskIntent | None:
    """Which of the four a typed question is asking, or `None` for a miss.

    Case-insensitive substring matching, as the prototype does. Crude on purpose: the point of
    free text is that it needs no rework when a hearing API replaces the script, not that the
    matching is clever. Misses are expected, which is why every person authors a line for one.
    """
    lowered = question.lower()
    for intent in ASK_INTENTS:
        if any(key in lowered for key in intent.keys):
            return intent
    return None
