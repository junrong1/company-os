"""Who a director is when it speaks, and what stands in when it cannot.

**A persona is read, not written.** Every field comes from the roster the genesis event already
carries — name, title, department, responsibility, tools — so lighting up a specialist later is a
scenario edit rather than a code change (M15), and a second company gets its four personas by being
a file. Nothing here is authored in this repository except the rules the prompt states, which is the
line M13 rests on.

**It is read from the log rather than from the scenario file, and that is deliberate.** The leg could
load `scenarios/<id>.toml` by the id genesis records, and it would usually agree. Usually is the
problem: a scenario file is editable and a log is not, so a run whose file changed underneath it
would be briefed by a director who no longer matches the company the run is of. The genesis payload
is the run's own copy, inherited by every fork, and it is what the client is already drawing.

**The scripted reply is this repository's prose, not the scenario's.** The obvious alternative was the
authored `deflection` line each person already carries, which would give each director their own
voice for a turn the bench could not answer. It is not reachable: `roster_to_state` does not carry
`deflection`, so putting it on the wire would move the genesis payload — and the genesis payload is
what every golden fixture in two languages is taken from. Flavour is not worth regenerating the
fixture set, and the honest reading is that a fallback is *not* the director speaking. It is this
product saying the bench did not answer, which is why it names the condition that fired (M21) and why
the surface renders that reason rather than leaving canned prose to pass for a briefing.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

#: What stands where a briefing would have been.
#:
#: One pair for every director and every condition, and both halves of that are choices. Per-director
#: prose would need the scenario on the wire; per-condition prose would put the reason in two places,
#: the text and the enum, and the two would drift the first time somebody edited one. The reason is
#: rendered from the enum beside this text, so the text does not restate it.
#:
#: It reads as an absence rather than as an opinion, because it enters an append-only log attributed
#: to a named person and is copied into every fork and the exported report. "I have nothing for you"
#: is a true sentence about a turn a provider did not answer. Anything more would be this repository
#: putting analysis in a director's mouth.
FALLBACK_BRIEFING = (
    "I have not been able to put a briefing together for this one. Read the decision from the "
    "options and what you already know about my line."
)

#: The objection half, which is not optional: `simcore.statement.refusal` requires one, because a
#: bench that only agrees adds nothing. Stating that the objection is *missing* is the objection a
#: fallback honestly has — and it tells the CEO what they are deciding without, which is the whole
#: consequence of the bench being down.
FALLBACK_OBJECTION = (
    "Take the missing objection as missing information rather than as my agreement. Nobody has "
    "argued the other side of this in front of you."
)


@dataclass(frozen=True, slots=True)
class Persona:
    """One director, as the prompt will describe them to a provider.

    Every field is scenario-authored, and every field therefore travels in the *user* turn as
    delimited data rather than in the system instruction — see `prompts.py`, which is where that rule
    is enforced and explained. This type deliberately has no `render` method for that reason: a
    persona that could render itself into an instruction is a persona somebody will render into one.
    """

    person_id: str
    name: str
    title: str
    dept: str
    responsibility: str
    tools: tuple[str, ...]
    rank: str

    @property
    def is_director(self) -> bool:
        return self.rank == "director"


def persona_for(genesis_payload: Mapping[str, Any], person_id: str) -> Persona | None:
    """The persona for one person, or `None` if the roster does not describe them.

    `None` rather than a raise, and rather than a persona with empty fields. The caller's whole job
    is to be optional: a log whose roster does not name the person the request was raised on is a
    declined statement, not an exception on the tick loop's worker thread, and not a briefing spoken
    by somebody with no title.
    """
    roster = genesis_payload.get("roster", {})
    if not isinstance(roster, Mapping):
        return None
    entry = roster.get(person_id)
    if not isinstance(entry, Mapping):
        return None

    tools = entry.get("tools", [])
    return Persona(
        person_id=person_id,
        name=str(entry.get("name", "")),
        title=str(entry.get("title", "")),
        dept=str(entry.get("dept", "")),
        responsibility=str(entry.get("responsibility", "")),
        tools=tuple(str(tool) for tool in tools) if isinstance(tools, list) else (),
        rank=str(entry.get("rank", "")),
    )
