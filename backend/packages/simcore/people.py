"""The roster: desks follow departments, reporting lines do not.

Ported from script section 2 of `company-os.html` (`:1170` roster, `assignSeats`,
`VOICE`).

The mismatch in the sample data is deliberate and load-bearing: Priya sits in
Accounting but reports to the Administration director, exactly the kind of thing a
real org chart has. It means "which room someone sits in" and "whose reporting line
they are in" are two different questions, and the simulation needs both — seating
comes from the room, while assignment, delegation and (from U7) capacity all follow
the reporting line.

That gives **four** load-bearing departments rather than eight rooms: the four
directors and their reports. Two of them, Customer Support and People, hold exactly
one non-director each, which is why U7's capacity draw has to include the director —
a single attrition event would otherwise leave a department with nobody to allocate
across.

The scripted dialogue is ported as-is rather than deferred. It is the content Phase 2
replaces with the hearing API, and porting it now means Phase 2 changes the producer
rather than adding the data.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from simcore.world import Floor, walkable


@dataclass(frozen=True, slots=True)
class PersonSpec:
    """One person on the shipped roster.

    `dept` is the *room* they sit in. `mgr` is the reporting line. They disagree for
    Priya, on purpose.
    """

    id: str
    name: str
    initials: str
    title: str
    dept: str
    mgr: str | None
    rank: str  # "director" | "staff"
    slot: int


PEOPLE: tuple[PersonSpec, ...] = (
    PersonSpec("dir_sales", "Marcus Webb", "MW", "VP, Sales", "sales", None, "director", 2),
    PersonSpec(
        "stf_order", "Dana Reyes", "DR", "Order Processing Specialist", "sales", "dir_sales", "staff", 0
    ),
    PersonSpec("stf_field", "Tom Baird", "TB", "Account Executive", "sales", "dir_sales", "staff", 1),
    PersonSpec(
        "dir_admin", "Grace Okafor", "GO", "Director, Administration", "admin", None, "director", 1
    ),
    PersonSpec("stf_ap", "Priya Raman", "PR", "Accounts Payable", "accounting", "dir_admin", "staff", 0),
    PersonSpec("stf_buyer", "Victor Hale", "VH", "Procurement Buyer", "admin", "dir_admin", "staff", 0),
    PersonSpec(
        "dir_cs", "Nina Kaur", "NK", "Customer Support Manager", "support", None, "director", 1
    ),
    PersonSpec("stf_cs", "Owen Cole", "OC", "Support, First Line", "support", "dir_cs", "staff", 0),
    PersonSpec("dir_hr", "Ruth Bello", "RB", "People Manager", "hr", None, "director", 1),
    PersonSpec("stf_rec", "Sam Delgado", "SD", "Recruiter", "hr", "dir_hr", "staff", 0),
)

PEOPLE_BY_ID: dict[str, PersonSpec] = {person.id: person for person in PEOPLE}


def spec(person_id: str) -> PersonSpec:
    try:
        return PEOPLE_BY_ID[person_id]
    except KeyError:
        raise KeyError(f"no person {person_id!r} on the roster") from None


def directors() -> tuple[str, ...]:
    return tuple(person.id for person in PEOPLE if person.rank == "director")


def reporting_line_of(person_id: str) -> str:
    """The director whose line this person is in. A director is their own line."""
    person = spec(person_id)
    return person.mgr if person.mgr else person.id


def reporting_lines() -> dict[str, tuple[str, ...]]:
    """Director id -> every member of that line, the director included.

    The director is a member, not an overseer of members. U7's capacity draw is
    allocated across this whole tuple, and two of the four lines would otherwise be
    empty after one attrition event.
    """
    lines: dict[str, list[str]] = {director: [director] for director in directors()}
    for person in PEOPLE:
        if person.mgr:
            lines[person.mgr].append(person.id)
    return {director: tuple(members) for director, members in lines.items()}


def direct_reports(director_id: str) -> tuple[str, ...]:
    return tuple(person.id for person in PEOPLE if person.mgr == director_id)


def same_reporting_line(a: str, b: str) -> bool:
    """Whether a reassignment between these two stays inside one line.

    Assignment across reporting lines is rejected and mutates nothing, so this is the
    predicate that decides it.
    """
    return reporting_line_of(a) == reporting_line_of(b)


def assign_seats(floor: Floor) -> dict[str, tuple[int, int]]:
    """Resolve everyone's desk from the generated plan.

    A port of `assignSeats()`, including both fallbacks. The invariant it exists to
    hold is that no two people share a chair — a room too small for everyone in it
    seats the overflow on any free floor tile inside that room, and only then on the
    spawn point.

    Deterministic: roster order decides who gets contested slots, and roster order is
    fixed.
    """
    taken: set[tuple[int, int]] = set()
    seats: dict[str, tuple[int, int]] = {}

    for person in PEOPLE:
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


def roster_to_state(seats: dict[str, tuple[int, int]]) -> dict[str, Any]:
    """The immutable part of the roster, as the genesis payload carries it.

    Name, initials and title are here because the client has to be able to say who it is
    talking to. Standing next to someone is the whole gesture, and a conversation headed
    `stf_ap` would name a row in a table rather than a person — which is the opposite of what
    the in-person route exists to be worth. They are authored constants that never move, so
    shipping them once at genesis is cheaper than a lookup the client cannot perform.
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
        }
        for person in PEOPLE
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
    matching is clever. Misses are expected, which is why every person has a line for one.
    """
    lowered = question.lower()
    for intent in ASK_INTENTS:
        if any(key in lowered for key in intent.keys):
            return intent
    return None


#: What each person says when the question matched nothing (R16).
#:
#: One per person rather than one shared line, because a miss is common enough that a generic
#: "not in the script" would be most of what a player hears from the mechanic. Said in their own
#: voice, a miss still reveals character and still points at what they *can* answer.
DEFLECTIONS: dict[str, str] = {
    "stf_order": "I would not know about that. Ask me why the entry works the way it does, where the exceptions are, what I decide myself, or where the time goes.",
    "stf_ap": "That is above my desk. What I can tell you is why the reconciliation runs as it does, the exceptions I make, where my authority ends, and what eats the month.",
    "stf_buyer": "No idea, honestly. Ask me why we quote the way we do, when I go single-source, what is mine to call, or where an order parks.",
    "stf_cs": "Not something I see from first line. Ask me why the answers take as long as they do, what I escalate, what is my judgement, or what fills the queue.",
    "stf_rec": "I could not say. Ask me why the postings drift, which candidates skip the process, where I set the bar, or what the scheduling costs.",
    "stf_field": "That is not my end of it. Ask me why I keep my own numbers, what I take verbally, what I promise on my own, or what stops me on the road.",
    "dir_sales": "I would be guessing, and you would be able to tell. Ask me why the numbers land where they do, what reaches me, what is mine to approve, or what we all know is broken.",
    "dir_admin": "I do not have that to hand. Ask me why the close runs long, what we let slide at quarter end, where the approval line sits, or what everything waits on.",
    "dir_cs": "You would want someone closer to it than me. Ask me why first line costs what it does, what comes to me, how escalation actually works, or what fills Owen's day.",
    "dir_hr": "I have nothing useful on that. Ask me why the requirements drift, which hires go around us, who really decides, or what the agreeing costs.",
}


def deflection_for(person_id: str) -> str:
    """This person's line for a question they cannot answer. Raises for a stranger."""
    spec(person_id)
    return DEFLECTIONS[person_id]


def answer_for(person_id: str, slot: str) -> str:
    """This person's scripted reply for one of the four questions."""
    spec(person_id)
    return VOICE[person_id][slot]


#: Scripted replies, four per person. This is where the hearing API plugs in at
#: Phase 2; the shape stays, the producer changes.
#:
#: Keys are the four questions the prototype's panel offers: why / exception / axis /
#: bottleneck.
VOICE: dict[str, dict[str, str]] = {
    "stf_order": {
        "why": "Sales promises a date in their spreadsheet, then I retype it into the order system. Flip that order and sales cannot answer the customer.",
        "exception": "For our three largest accounts I hold stock over the phone before I enter anything. That never shows up in the system.",
        "axis": "I decide whether to pull a delivery date forward. Under three days I just do it and tell nobody.",
        "bottleneck": "Typing the same numbers twice. About 25 hours a month.",
    },
    "stf_ap": {
        "why": "Invoices arrive on paper and as PDF, so I reconcile both before I post anything. With only one of them, things slip through.",
        "exception": "The last three days of the month I release payment before the paper arrives. Waiting would miss the supplier close.",
        "axis": "Anything over $10K needs the manager approval. Nobody has revisited that line in five years.",
        "bottleneck": "Matching. Seven minutes an invoice, four hundred invoices a month.",
    },
    "stf_buyer": {
        "why": "Three competing quotes on everything, so we can defend the price if an auditor asks.",
        "exception": "Repairs and emergency parts go single-source. The plant stops otherwise, so I write the paperwork afterwards.",
        "axis": "The three-quote rule ignores value. I collect three quotes for a thousand-dollar desk.",
        "bottleneck": "Waiting for quotes to come back. Every order parks 2.5 days there.",
    },
    "stf_cs": {
        "why": "I write the same answers by hand every time. We have templates, but I rework them for each customer.",
        "exception": "Anything that mentions cancelling goes straight to Nina, whatever else it says.",
        "axis": "Whether a ticket goes to engineering is my call. About five a day I genuinely cannot judge.",
        "bottleneck": "Questions we have already answered. Six of every ten tickets.",
    },
    "stf_rec": {
        "why": "What the hiring manager wants and what the job post says do not match. Rewriting it pauses the listing, so we run it as is.",
        "exception": "Referrals skip the requirements entirely. That route actually retains better.",
        "axis": "I set the first-round bar. I have never aligned it with the people who run the interviews.",
        "bottleneck": "Interview scheduling. Six emails per candidate.",
    },
    "stf_field": {
        "why": "I carry my own spreadsheet so I can answer on delivery dates at the customer table. They cannot see our system.",
        "exception": "Repeat orders I take verbally and collect the paperwork later.",
        "axis": "The delivery promise is mine. I check stock with Dana, but urgent ones I call myself.",
        "bottleneck": "I cannot process an order while I am travelling.",
    },
    "dir_sales": {
        "why": "I see the numbers daily. Why they came out that way, I would have to ask the floor.",
        "exception": "The exceptions live with the team. What reaches me is already too late to fix.",
        "axis": "Credit and discounts are mine. Everything else sits with the account owner.",
        "bottleneck": "The duplicate order entry. We know about it; nobody has a free hand to fix it.",
    },
    "dir_admin": {
        "why": "The close takes five days for two reasons — waiting on paper invoices, and approvals sitting idle. Both causes start outside my department.",
        "exception": "At quarter end we let the close run a day late to make the numbers line up.",
        "axis": "Manager approval above $10K. That threshold can move. Nobody has decided to move it.",
        "bottleneck": "Idle approvals. If the approver is travelling, everything parks for two days.",
    },
    "dir_cs": {
        "why": "First-line response is entirely people. I cannot see a way to shrink it without losing quality.",
        "exception": "Cancellations and anything legal come to me, regardless of content.",
        "axis": "There is no written escalation rule. It is Owen judgement.",
        "bottleneck": "Answering repeat questions. That is 60% of Owen time.",
    },
    "dir_hr": {
        "why": "We ask the hiring manager what they want, then translate it into our language for the posting. That is where it drifts.",
        "exception": "Executive hires run through a different route and leave no record.",
        "axis": "The hiring manager decides at the end, but we filter before they ever see a candidate.",
        "bottleneck": "Agreeing requirements. Two weeks of back and forth.",
    },
}
