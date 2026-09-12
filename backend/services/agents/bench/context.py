"""What a director is shown before it speaks: a structured query over the log, scoped by line.

**Retrieval is a structured query, not a vector search.** The compose store is Alpine Postgres with
no pgvector, and swapping the base image would change the one command M1 promises for a capability a
forty-five-event run does not need. What a director needs is not "the nearest paragraph" — it is the
events touching its own reporting line since a bounded point, what its department's recurring draw is
doing, and the note on the deliverable that unlocked the item it is being asked about. All three are
lookups.

**Line-scoped by construction, with no reachable unscoped variant** (R23). Two functions here
return events — `scan`, the admission pass, and `retrieve`, the statement's window onto it — and
neither can be called without an `Authorized`, which neither computes: the scope is derived once
inside `step()` from folded state and recorded on the request, so this module is *told* what it may
read. U14's memory is a third window onto the same `scan`, which is why that pass is a function
rather than a loop inside `retrieve`: one filter, several windows, and nothing that reads the log
without a scope. That is the strong form of default-deny — a reader that does
not derive its own scope cannot widen it.

**U15's Authorization is that signature being used, and nothing in this file changed for it**
(M39). A granted Authorization widens the `Authorized` the kernel derives — `step.authorized_scope`
adds the other line's people and items for the item the grant was about — and it arrives here as a
scope like any other. So "a director cannot read another line without Authorization" is enforced by
the same two lines that enforce line scoping at all: `permits_person` and `permits_item`, over a
set this module cannot compute and cannot extend. There is no branch here for an authorized read,
which is the property worth keeping: a second code path for the permitted case would be a second
place the boundary lived.

Three further things keep the door shut rather than merely closed:

* the kinds that can be retrieved at all are an explicit table, and each entry says which payload
  keys name a person and which names an item. A kind nobody listed is never retrieved, so adding a
  kind is a decision about scope rather than a consequence of it existing;
* an event that names neither a person nor an item in scope is dropped, including a company-wide one.
  A company-wide fact is not another line's event, but it is not this line's either, and admitting it
  here would make the boundary depend on which kinds somebody remembered to think about;
* nothing in this package opens a store. The events arrive as an argument, from the service module
  that owns the engine, and `tests/test_pending_input.py` reads these files to keep it that way.

**The context is returned to be logged, not to be recomputed** (M32). `to_payload` is the exact
shape that rides `INPUT_RECEIVED` beside the prose, because a re-ranked or re-queried retrieval at
replay time would hand the director a different world while the log claimed fidelity.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from contracts.envelope import Envelope, EventKind
from simcore import statement as stmt
from simcore import time as simtime

#: How far back a director remembers, in sim-ticks.
#:
#: Three sim-days. The bound exists because the retrieved context becomes a permanent logged fact
#: and because a briefing about a decision point is about the recent past — a director quoting an
#: event from two weeks ago is quoting something the CEO has already acted on. U14 extends a
#: director's *memory* past this; a statement's working set is deliberately shorter than a memory.
LOOKBACK_TICKS = 3 * simtime.TICKS_PER_SIM_DAY

#: The most events one context may carry. Held to `simcore.statement`'s cap, which is the number the
#: kernel-side guard refuses above — one number, so a leg cannot assemble a context the kernel will
#: then reject for being too large.
MAX_EVENTS = stmt.MAX_CONTEXT_EVENTS

#: How much of one payload field travels as the entry's readable detail.
#:
#: Short on purpose. This is a label for a director to speak from, not the payload: a copied payload
#: would put a metric table and a floor plan into every statement, and the statement goes into an
#: append-only log and then into the exported report.
#:
#: Inside `stmt.MAX_CONTEXT_FIELD_CHARS`, which is what the kernel-side guard refuses above, and the
#: suite holds it there: a producer whose truncation exceeded the guard's cap would assemble
#: statements the kernel then rejected for a length nobody chose deliberately.
MAX_DETAIL_CHARS = 64

#: Which event kinds a director may be shown, and where each one names the people and the item it is
#: about. The second element is the item key; the first is every key that names a person.
#:
#: Written out per kind rather than probed generically, because the keys genuinely differ — a
#: reassignment names `from` and `to`, a return names `was_assigned_to`, and a hire names `director`.
#: A generic "look for anything person-shaped" would admit an event whose person field somebody
#: renamed, which is a scope leak arriving through a refactor.
RETRIEVABLE: dict[EventKind, tuple[tuple[str, ...], str]] = {
    EventKind.WORK_ASSIGNED: (("person",), "item"),
    EventKind.WORK_REASSIGNED: (("from", "to"), "item"),
    EventKind.WORK_RETURNED_TO_BACKLOG: (("was_assigned_to",), "item"),
    EventKind.CHECKPOINT_RAISED: (("person",), "item"),
    EventKind.DECISION_RESOLVED: ((), "item"),
    EventKind.DELIVERABLE_PRODUCED: (("person",), "item"),
    EventKind.QUESTION_ANSWERED: (("person",), ""),
    EventKind.ATTRITION: (("person", "director"), "returned_item"),
    EventKind.HIRE_REQUESTED: (("director", "person"), "item"),
    EventKind.HIRE_ARRIVED: (("director", "person"), ""),
    EventKind.HIRE_REFUSED: (("director",), ""),
}

#: A short, human-readable field per kind, so a director has something to speak from beyond an id.
#: Never the whole payload: the context is logged, and a payload copied wholesale would put a metric
#: table and a floor plan into every statement.
DETAIL_KEYS: dict[EventKind, tuple[str, ...]] = {
    EventKind.WORK_ASSIGNED: ("via",),
    EventKind.WORK_REASSIGNED: ("retained_units",),
    EventKind.WORK_RETURNED_TO_BACKLOG: ("retained_units",),
    EventKind.CHECKPOINT_RAISED: ("label",),
    EventKind.DECISION_RESOLVED: ("choice",),
    EventKind.DELIVERABLE_PRODUCED: ("deliverable",),
    EventKind.QUESTION_ANSWERED: ("label",),
    EventKind.ATTRITION: ("days_below_threshold",),
    EventKind.HIRE_REQUESTED: ("effort_hours",),
    EventKind.HIRE_ARRIVED: ("person",),
    EventKind.HIRE_REFUSED: ("reason",),
}


@dataclass(frozen=True, slots=True)
class RetrievedEvent:
    """One event a director may quote, reduced to what it may quote about it."""

    seq: int
    tick: int
    kind: str
    person: str
    item: str
    detail: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "tick": self.tick,
            "kind": self.kind,
            "person": self.person,
            "item": self.item,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class Retrieved:
    """Everything one director was shown for one statement.

    Carries `director` and `line` so that a log reader can check the scope the statement was drawn
    under against the scope the request recorded, with no roster and no store — which is what makes
    R23 auditable rather than asserted.
    """

    director: str
    line: tuple[str, ...]
    since_seq: int
    through_seq: int
    events: tuple[RetrievedEvent, ...] = ()
    #: The department's recurring draw and the load it is producing. Not an event, so it carries no
    #: sequence and is deliberately not citable: a figure a director quotes has to resolve to
    #: something, and this resolves to state rather than to a row.
    draw: dict[str, int] = field(default_factory=dict)
    #: The note on the deliverable that unlocked the item, or empty. See `_unlocking_note`.
    unlocking_note: str = ""

    def to_payload(self) -> dict[str, Any]:
        """The form that rides `INPUT_RECEIVED` beside the prose (M32).

        Sorted and integer-only, because it is canonically encoded into an append-only log: a set or
        a float here would be refused at the append, and an unsorted map would make two runs of one
        seed compare unequal.
        """
        return {
            "director": self.director,
            "line": sorted(self.line),
            "since_seq": self.since_seq,
            "through_seq": self.through_seq,
            "events": [event.to_payload() for event in self.events],
            "draw": {key: int(value) for key, value in sorted(self.draw.items())},
            "unlocking_note": self.unlocking_note,
        }

    def citable(self) -> tuple[int, ...]:
        """The sequences a statement drawn from this context may cite."""
        return tuple(event.seq for event in self.events)


def retrieve(
    events: Iterable[Envelope],
    *,
    authorized: stmt.Authorized,
    owning_item: str,
    at_tick: int,
) -> Retrieved:
    """The context for one statement. Requires a scope, and never widens the one it is given.

    `events` is the run's log in sequence order, as the service module read it. It is passed rather
    than fetched because a fetch here would be a second door onto the store — and the whole of R23's
    query half is that there is one door and it takes a scope.

    **The window is closed at both ends, and the upper end is `at_tick` *exclusive*.** This runs on a
    worker thread an arbitrary wall-clock interval after the request was raised, while the tick loop
    keeps appending — so anything that read "the log as it stands" would make the retrieved context a
    function of provider latency. And the context is logged (M32), so that is not a stale read, it is
    a permanent record that two fresh runs from one seed would disagree about.

    Exclusive rather than inclusive because the raising tick is not closed when the request is
    raised: a command applied at the same tick lands *after* the step's events, so an inclusive bound
    would admit rows that may or may not exist yet depending on when the read happened. Everything at
    `at_tick - 1` and earlier is settled by the time the step reaches `at_tick`, which is what makes
    the filtered set a function of the log prefix rather than of the clock.

    `through_seq` follows from the same rule: it is the last sequence *kept*, not the head of the log.
    """
    ordered = list(events)
    since_tick = max(0, at_tick - LOOKBACK_TICKS)
    genesis = next(
        (envelope for envelope in ordered if envelope.kind is EventKind.GENESIS), None
    )

    kept = list(scan(ordered, authorized=authorized, since_tick=since_tick, at_tick=at_tick))

    #: The authored monthly draw comes from genesis and the load it is producing from the most
    #: recent `LOAD_CHANGED` *inside the window*. Two sources because they are two facts: what the
    #: department is committed to, and how far through it is.
    draw: dict[str, int] = {}
    if genesis is not None:
        authored = genesis.decoded_payload().get("draws", {})
        if isinstance(authored, dict) and authorized.director in authored:
            draw["monthly_hours"] = int(authored[authorized.director])

    for envelope in ordered:
        # Its own pass rather than a branch inside `scan`, because it is not an event a director
        # may quote: it is one company-wide payload read for one department's figure, and it
        # carries no sequence precisely so that nothing can cite it. Keeping it out of `scan` is
        # what lets that function be "every event this scope places" with no exceptions in it.
        if envelope.kind is not EventKind.LOAD_CHANGED:
            continue
        payload = envelope.decoded_payload()
        tick = int(payload.get("tick", envelope.tick))
        if not since_tick <= tick < at_tick:
            continue
        # Read for the department's own figure only. The payload is company-wide — one entry per
        # director — so taking it whole would be the cross-line read this module exists to
        # prevent, arriving through a field rather than through an event, which is the harder
        # half to notice.
        mine = payload.get("load", {})
        if isinstance(mine, dict) and authorized.director in mine:
            draw["load_permille"] = int(mine[authorized.director])

    if len(kept) > MAX_EVENTS:
        # The most recent, because a briefing is about the decision point in front of the CEO. Kept
        # from the tail rather than sampled: a sample would make the context depend on how many
        # events happened to precede it, and two runs of one seed would then be shown different
        # worlds for the same reason.
        kept = kept[-MAX_EVENTS:]

    return Retrieved(
        director=authorized.director,
        line=tuple(sorted(authorized.people)),
        # The span of what was actually kept, not of the log. The head of the log is where a latency
        # read would come in, and both of these are logged with the statement.
        since_seq=kept[0].seq if kept else 0,
        through_seq=kept[-1].seq if kept else 0,
        events=tuple(kept),
        draw=draw,
        unlocking_note=_unlocking_note(genesis, kept, owning_item, authorized),
    )


def scan(
    events: Iterable[Envelope],
    *,
    authorized: stmt.Authorized,
    since_tick: int,
    at_tick: int,
) -> tuple[RetrievedEvent, ...]:
    """Every event in `[since_tick, at_tick)` that this scope places, in sequence order.

    **The one admission pass, and the reason it is public** (R23). `retrieve` bounds it to three
    sim-days and caps it at what a statement may carry; `memory.select` runs it over the whole run
    and then ranks what came back. Both are windows onto the same filter — the kind table, the
    person and item keys, and the default-deny that drops an event naming neither — and that filter
    is the whole of what keeps one line's events out of another's. A memory that re-implemented it
    would be the second place the boundary lived, and the first divergence would be silent.

    It cannot be called without an `Authorized`, and it does not compute one. That is the same
    signature `retrieve` has and it is deliberate: a reader that cannot derive its own scope cannot
    widen it.
    """
    kept: list[RetrievedEvent] = []
    for envelope in events:
        if envelope.kind not in RETRIEVABLE:
            continue
        payload = envelope.decoded_payload()
        tick = int(payload.get("tick", envelope.tick))
        if not since_tick <= tick < at_tick:
            continue
        entry = _as_entry(envelope, payload, authorized)
        if entry is not None:
            kept.append(entry)
    return tuple(kept)


def _as_entry(
    envelope: Envelope, payload: dict[str, Any], authorized: stmt.Authorized
) -> RetrievedEvent | None:
    """This event as something the director may quote, or `None` if it is not theirs to read.

    Default-deny in the literal sense: the return is `None` unless a named person or a named item is
    inside the scope. An event that names neither cannot be placed, so it is not admitted.
    """
    person_keys, item_key = RETRIEVABLE[envelope.kind]

    person = ""
    for key in person_keys:
        candidate = str(payload.get(key, ""))
        if candidate and authorized.permits_person(candidate):
            person = candidate
            break

    item = str(payload.get(item_key, "")) if item_key else ""
    if item and not authorized.permits_item(item):
        item = ""

    if not person and not item:
        return None

    return RetrievedEvent(
        seq=envelope.seq,
        tick=int(payload.get("tick", envelope.tick)),
        kind=envelope.kind.name,
        person=person,
        item=item,
        detail=_detail(envelope.kind, payload),
    )


def _detail(kind: EventKind, payload: dict[str, Any]) -> str:
    """One short readable field, or the empty string. Never the payload."""
    for key in DETAIL_KEYS.get(kind, ()):
        value = payload.get(key)
        if isinstance(value, dict):
            value = value.get("title", "")
        if value not in (None, "", {}):
            return str(value)[:MAX_DETAIL_CHARS]
    return ""


def _unlocking_note(
    genesis: Envelope | None,
    kept: Sequence[RetrievedEvent],
    owning_item: str,
    authorized: stmt.Authorized,
) -> str:
    """The note on the deliverable that unlocked this item, when it is this line's to read.

    **Often empty, and that is R23 rather than an unfinished lookup.** An item's prerequisites come
    from the authored graph on the genesis payload, and upstream work is frequently another line's —
    so the deliverable that unlocked this item is frequently a fact this director is not authorized
    to have read. The prerequisite is therefore checked against the scope like everything else, and a
    cross-line prerequisite yields nothing.

    It is a string rather than a citable entry because the sequence would be the thing out of scope.
    A director may know that the work it depends on landed; it may not quote the row.
    """
    if genesis is None:
        return ""

    catalog = genesis.decoded_payload().get("catalog", [])
    if not isinstance(catalog, list):
        return ""

    prerequisites = {
        str(item)
        for entry in catalog
        if isinstance(entry, dict) and entry.get("id") == owning_item
        for item in entry.get("requires", {}).get("items", [])
    }
    if not prerequisites:
        return ""

    for entry in reversed(kept):
        if entry.kind != EventKind.DELIVERABLE_PRODUCED.name:
            continue
        if entry.item in prerequisites and authorized.permits_item(entry.item):
            return entry.detail
    return ""
