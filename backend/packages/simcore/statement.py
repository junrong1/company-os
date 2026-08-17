"""What a director may read, what it may say, and the predicates that decide both.

A director is a *statement producer*, never a resolver. It answers with prose and citations where
the CEO answers with a choice, so a statement is validated on shape and citation rather than on
bounds — a statement moves no metric (M22), and there is nothing to clamp.

**These predicates live in `packages/` because two processes run them, and there is one copy.**
The agents service rejects early, so a bad statement never crosses the wire and the guard sits next
to the prompt that produced it. The kernel re-checks at answer-application time, so the verdict is
reproducible from the log — which is what makes M18 and M19 *auditable* rather than merely
enforced: a reader can re-derive from a log that a statement was refused, without trusting the
service that refused it. Two call sites, one implementation. `simcore` is the only place both may
import, and `tests/test_import_boundaries.py` already requires it to be pure, so nothing here
touches transport or a store.

**Scope is carried, not computed by the reader.** `Authorized` is derived once, inside `step()`,
from state the fold reproduces, and recorded on the request the leg receives. The leg is therefore
*told* what it may read rather than deciding — which is what "default-deny at the query layer, with
no reachable unscoped variant" means concretely (R23), because a leg that does not compute its own
scope cannot widen it. An `Authorized` with an empty line reads nothing rather than everything, and
the constructor refuses one: default-deny has to be the value an omission produces, not a
convention.

**Citations resolve against the context the statement was produced from.** Every cited sequence
must appear in the retrieved context, and every context entry must be inside the authorized scope.
That is a chain a log reader can walk with no store and no model: the request records the scope, the
answer records the context, and the citations point into it. A statement that cited another
reporting line would have to have been *shown* it, and the middle link is where that is caught.

**What U11 adds here, and adds rather than copies.** M18's ranking guard — a statement that names a
preferred option, ranks the options or asserts one is better — and M19's figure guard, that a number
resolves to an event, a branch result or authored content. Both are pure functions over the same
inputs, so both belong beside `refusal` and are called from it; U11's `bench/guards.py` is the
agents-side *call site* and the persona material, not a second copy of a predicate. U15 constrains
`Authorized` rather than replacing it: an Authorization grant widens the scope a director is given,
and the signature it has to widen exists here already.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Any

from simcore import scenario as sc

# =========================================================================
# The answer's shape
# =========================================================================
#
# Spelled once, because three readers share it: the leg that builds the answer, the kernel that
# validates it, and the client that renders it. A key spelled two ways is a field that silently
# arrives empty.

KEY_BRIEFING = "briefing"
KEY_OBJECTION = "objection"
KEY_CITATIONS = "citations"
KEY_PRODUCER = "producer"
KEY_PRODUCER_KIND = "producer_kind"
KEY_MODEL_IDENTITY = "model_identity"
KEY_CONTEXT = "context"

#: Where a statement came from. A closed set, and neither value carries provider words: R5 forbids
#: provider-supplied text in the log, and the report reads these to say who spoke.
PRODUCER_MODEL = "model"
PRODUCER_SCRIPTED = "scripted"
PRODUCER_KINDS = (PRODUCER_MODEL, PRODUCER_SCRIPTED)

#: Bounds on what may become a permanent logged fact.
#:
#: The same argument `MAX_QUESTION_CHARS` makes in `step.py`, one step stronger: this text is
#: written verbatim into an append-only log, broadcast to every subscriber, re-validated on every
#: read, copied by every fork and interpolated into the exported report. It is worth bounding where
#: it is priced rather than trusting the producer.
#:
#: The prose cap is *the loader's*, imported rather than restated, so a fallback line the scenario
#: authored and a briefing a model generated are held to one length by one number. Two 512s in the
#: tree would be two numbers somebody could move independently, and the symptom would be an authored
#: reply the guard refused.
MAX_PROSE_CHARS = sc.MAX_PROSE_CHARS
MAX_CITATIONS = 12
MAX_CONTEXT_EVENTS = 24
MAX_IDENTITY_CHARS = 96

#: How long any one text field of a retrieved context event may be.
#:
#: Its own number rather than `MAX_IDENTITY_CHARS`, which it happens to equal: an event kind, a
#: person id, an item id and a one-line detail are not model identifiers, and a unit that moves one
#: cap should not silently move the other. Deliberately *looser* than what `bench/context.py`
#: truncates to, so a producer that respects its own cap can never assemble a field this refuses —
#: the failure that shape prevents is a statement rejected for a length the producer chose.
MAX_CONTEXT_FIELD_CHARS = 96

#: The keys a retrieved context and one of its events may carry, and no others.
#:
#: A closed set rather than a minimum, because the context is written verbatim into an append-only
#: log and interpolated into the exported report: an unknown key is either a typo whose value is
#: silently doing nothing, or a field being smuggled past the caps above. The scenario loader makes
#: the same call about an authored file, for the same reason. `bench/context.py` writes exactly
#: these; a unit adding a field adds it here in the same change.
CONTEXT_KEYS = frozenset(
    {"director", "line", "since_seq", "through_seq", "events", "draw", "unlocking_note"}
)
CONTEXT_EVENT_KEYS = frozenset({"seq", "tick", "kind", "person", "item", "detail"})


@dataclass(frozen=True, slots=True)
class Authorized:
    """What one director may be shown, and therefore what it may cite.

    Default-deny, enforced at construction rather than at every read: an `Authorized` naming no
    people is refused, because "nobody" is the value an omitted scope produces and it must not be
    the value that means "everybody". `permits_*` then answers a closed question per fact.

    **That refusal is unreachable from `authorized_for` and exists for `from_payload`.** The deriving
    constructor puts the director into their own scope, so it cannot produce an empty one; the
    payload constructor is handed a mapping nothing in this process wrote — a hand-edited log, or a
    bench request from a build that predates the recorded scope. `KernelRuntime._askable` is built
    entirely around that one case, because an exception from it would otherwise travel up the tick
    thread.

    U15's Authorization mechanic widens `people` and `items` for a granted cross-line read. That is
    why the fields are sets a grant can add to rather than a single line id a grant would have to
    replace — the signature is already the one an Authorization has to constrain.
    """

    director: str
    #: Person ids in this reporting line, the director included. Sorted on the wire.
    people: frozenset[str]
    #: Item ids assigned into this line, plus the item the request is about.
    items: frozenset[str]

    def __post_init__(self) -> None:
        if not self.director:
            raise ValueError("an authorized scope names the director it belongs to")
        if not self.people:
            raise ValueError(
                f"the authorized scope for {self.director!r} names no people. An empty scope is "
                "default-deny — it reads nothing — and refusing it here is what keeps it from "
                "being reached by accident and read as 'unscoped'."
            )

    def permits_person(self, person_id: str) -> bool:
        return person_id in self.people

    def permits_item(self, item_id: str) -> bool:
        return item_id in self.items

    def to_payload(self) -> dict[str, Any]:
        """The form the request event records. Sorted, so two runs of one seed agree."""
        return {"people": sorted(self.people), "items": sorted(self.items)}

    @classmethod
    def from_payload(cls, director: str, payload: Mapping[str, Any]) -> Authorized:
        return cls(
            director=director,
            people=frozenset(str(person) for person in payload.get("people", ())),
            items=frozenset(str(item) for item in payload.get("items", ())),
        )


def authorized_for(
    scenario: sc.Scenario,
    director_id: str,
    *,
    line_members: Collection[str],
    line_items: Collection[str],
) -> Authorized:
    """The scope a director reads under. Derived from folded state, never from the leg.

    `line_members` and `line_items` come from the run rather than from the scenario, and both have
    to: an arrived hire is in the line and on no authored roster, and which items are assigned into
    a line changes every time the CEO reassigns one. The scenario is still passed, because the
    director's own line membership is authored and a caller that had to assemble that too would be
    the second place the rule lived.
    """
    people = set(scenario.lines.get(director_id, ()))
    people.add(director_id)
    people.update(line_members)
    return Authorized(
        director=director_id, people=frozenset(people), items=frozenset(line_items)
    )


@dataclass(frozen=True, slots=True)
class StatementRequest:
    """The question the kernel hands the answering leg, and the whole of what it hands over.

    A type rather than a dict passed between two services, and it lives here rather than on either
    side of the seam for the reason the answer keys do: the kernel builds it and the leg reads it, so
    a field spelled two ways is a briefing that arrives about the wrong checkpoint.
    `proto/agents.proto` describes the same message; this is the in-process form of it, and the
    collapse to one process changed the transport without changing the vocabulary.

    It carries `authorized` rather than letting the leg derive it. That is R23's query half in one
    line: the scope is derived once, inside `step()`, from state the fold reproduces, and recorded on
    the event — so a leg cannot widen what it was given, and a log reader can check what it was.
    """

    run_id: str
    request_id: str
    person: str
    owning_item: str
    cp_index: int
    raised_at_tick: int
    authorized: Authorized

    @classmethod
    def from_raised(
        cls, run_id: str, request_id: str, payload: Mapping[str, Any]
    ) -> StatementRequest:
        """Built from a `REQUEST_RAISED` payload — live, or refolded after a restart.

        One constructor for both, deliberately. A restarted kernel rebuilds its outstanding requests
        from the log and has to be able to ask them again; a second construction path for the
        resumed case is where the two would disagree about a field the live one happened to fill.
        """
        person = str(payload.get("person", ""))
        return cls(
            run_id=run_id,
            request_id=request_id,
            person=person,
            owning_item=str(payload.get("owning_item", "")),
            cp_index=int(payload.get("cp_index", -1)),
            raised_at_tick=int(payload.get("raised_at_tick", payload.get("tick", 0))),
            authorized=Authorized.from_payload(person, payload.get("scope", {})),
        )


@dataclass(frozen=True, slots=True)
class Statement:
    """One director's briefing and its objection, with what it was drawn from.

    The objection is a field of its own rather than a paragraph inside the briefing, because a
    bench that only agrees adds nothing to a decision the CEO was going to take anyway — and a
    surface cannot render as two blocks what arrived as one.
    """

    briefing: str
    objection: str
    #: Log sequences the prose points at. The report resolves each one back to its event.
    citations: tuple[int, ...]
    #: Who said it — a person id on the roster, and the director the request named.
    producer: str
    producer_kind: str
    #: Empty for a scripted reply; a model identifier for a generated one. Never a provider name
    #: or a base URL: R6 keeps those out of the log entirely.
    model_identity: str
    #: The retrieved context, logged with the statement rather than beside it (M32). A re-ranked or
    #: re-queried retrieval at replay time would hand the director a different world while the log
    #: claimed fidelity.
    context: dict[str, Any]

    def to_answer(self) -> dict[str, Any]:
        """The answer payload, as it rides `INPUT_RECEIVED`."""
        return {
            KEY_BRIEFING: self.briefing,
            KEY_OBJECTION: self.objection,
            KEY_CITATIONS: list(self.citations),
            KEY_PRODUCER: self.producer,
            KEY_PRODUCER_KIND: self.producer_kind,
            KEY_MODEL_IDENTITY: self.model_identity,
            KEY_CONTEXT: self.context,
        }


def is_statement_answer(answer: Mapping[str, Any]) -> bool:
    """Whether this answer is a statement rather than a resolution or a metric consult.

    The payload discriminator R1 asks for, and it is read on the *answer* as well as on the
    request: the request's leg is what the fold dispatches on, and this is what keeps a malformed
    answer on the bench leg from being read as a resolution one branch further down.

    Either field is enough, deliberately. An answer carrying only an objection is not a statement and
    `refusal` says so with a sentence about the missing briefing — which is a better reading than
    "this is not a statement at all", because the leg plainly tried to make one.
    """
    return KEY_BRIEFING in answer or KEY_OBJECTION in answer


def refusal(answer: Mapping[str, Any], *, authorized: Authorized) -> str:
    """Return the reason this statement may not be appended, or the empty string.

    Ordered cheapest-first and shape-before-meaning, for the reason the scenario loader validates
    in two phases: a citation check reading a `citations` field that failed its own type check
    would report an invented failure about the real one.

    Rejected, never repaired. Trimming an over-long briefing or dropping an out-of-scope citation
    would make behaviour depend on where the repair sat, and would diverge live from replay if the
    two sides repaired differently — the same argument `pending.validate_domain_answer` makes about
    clamping. R5 sends every refusal to one exit: that director's scripted reply for that turn.
    """
    for key in (KEY_BRIEFING, KEY_OBJECTION, KEY_PRODUCER, KEY_PRODUCER_KIND):
        if not isinstance(answer.get(key), str):
            return f"a statement needs {key!r} as text, got {type(answer.get(key)).__name__}"

    briefing = str(answer[KEY_BRIEFING])
    objection = str(answer[KEY_OBJECTION])

    if not briefing.strip():
        return "a statement with no briefing is not a statement"
    if not objection.strip():
        return (
            "a statement carries an objection as well as a briefing (M14); a bench that only "
            "agrees adds nothing to a decision the CEO was going to take anyway"
        )

    for key, text in ((KEY_BRIEFING, briefing), (KEY_OBJECTION, objection)):
        if len(text) > MAX_PROSE_CHARS:
            return f"{key} is {len(text)} characters; the limit is {MAX_PROSE_CHARS}"
        offending = sc.control_character(text)
        if offending:
            return f"{key} contains {offending}, which no authored or generated line may carry"

    producer_kind = str(answer[KEY_PRODUCER_KIND])
    if producer_kind not in PRODUCER_KINDS:
        return f"{producer_kind!r} is not a producer kind; the set is {list(PRODUCER_KINDS)}"

    identity = answer.get(KEY_MODEL_IDENTITY, "")
    if not isinstance(identity, str) or len(identity) > MAX_IDENTITY_CHARS:
        return f"model_identity must be text of at most {MAX_IDENTITY_CHARS} characters"

    producer = str(answer[KEY_PRODUCER])
    if producer != authorized.director:
        # The producer is the director the request named. Without this a statement could be
        # attributed to somebody whose scope it was never drawn under, which would make the
        # citation check below check the wrong line.
        return (
            f"{producer!r} produced a statement for a request raised on {authorized.director!r}'s "
            "line; a statement is attributable or it is not a statement"
        )

    context = answer.get(KEY_CONTEXT)
    if not isinstance(context, dict):
        return "a statement carries the context it was produced from (M32), as a mapping"

    unknown = sorted(set(context) - CONTEXT_KEYS)
    if unknown:
        # Refused rather than dropped, and the same rule the scenario loader applies to an authored
        # file: an unknown key is either a typo whose value is silently doing nothing, or a field
        # somebody is smuggling into an append-only log. Neither is worth admitting to find out.
        return f"the retrieved context carries keys nothing reads: {unknown}"

    note = context.get("unlocking_note", "")
    if not isinstance(note, str) or len(note) > MAX_PROSE_CHARS:
        return f"the context's unlocking note must be text of at most {MAX_PROSE_CHARS} characters"

    for key in ("since_seq", "through_seq"):
        span = context.get(key, 0)
        if isinstance(span, bool) or not isinstance(span, int) or span < 0:
            return f"the context's {key!r} is {span!r}; it is a whole number"

    draw = context.get("draw", {})
    if not isinstance(draw, dict) or any(
        not isinstance(name, str)
        or isinstance(figure, bool)
        or not isinstance(figure, int)
        for name, figure in draw.items()
    ):
        return "the context's draw is a flat mapping of names to whole numbers"

    # `director` and `line` are what a log reader checks the scope against without a roster, so a
    # statement that disagreed with the request about either would make R23 unauditable while
    # appearing to carry its evidence. Checked rather than trusted for the same reason the entries
    # below are: the leg wrote them.
    if context.get("director") != authorized.director:
        return (
            f"the context says it was drawn for {context.get('director')!r} while the request was "
            f"raised on {authorized.director!r}'s line"
        )

    line = context.get("line", [])
    if not isinstance(line, list) or not all(isinstance(member, str) for member in line):
        return "the context's line is a list of person ids"
    outside = sorted(set(line) - authorized.people)
    if outside:
        return f"the context names {outside} as {authorized.director!r}'s line, and they are not"

    entries = context.get("events", [])
    if not isinstance(entries, list):
        return "the retrieved context's events must be a list"
    if len(entries) > MAX_CONTEXT_EVENTS:
        return (
            f"the retrieved context holds {len(entries)} events; the limit is "
            f"{MAX_CONTEXT_EVENTS}, because it becomes a permanent logged fact"
        )

    retrieved: set[int] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            return "each retrieved context event is a mapping"
        malformed = _entry_malformed(entry)
        if malformed:
            return malformed
        out_of_scope = _entry_outside_scope(entry, authorized)
        if out_of_scope:
            return out_of_scope
        retrieved.add(int(entry["seq"]))

    citations = answer.get(KEY_CITATIONS, [])
    if not isinstance(citations, list):
        return "citations are a list of log sequences"
    if len(citations) > MAX_CITATIONS:
        return f"a statement cites at most {MAX_CITATIONS} sequences, not {len(citations)}"
    if len(set(citations)) != len(citations):
        # Refused rather than de-duplicated, for the reason nothing here is repaired: the cap counts
        # entries, so `[7, 7, 7]` spends three of twelve on one event, and a producer that meant to
        # cite three things and cited one three times has a bug the fallback should surface.
        return "a statement cites each sequence once; the list holds a duplicate"

    for cited in citations:
        if isinstance(cited, bool) or not isinstance(cited, int) or cited <= 0:
            return f"{cited!r} is not a log sequence; citations are positive integers"
        if cited not in retrieved:
            # R23's chain, and the link that makes it checkable with no store: a director can only
            # cite what it was shown, and what it was shown is in this same event.
            return (
                f"sequence {cited} is cited but is not in the context this statement was produced "
                f"from, so it resolves to nothing the producer was authorized to read"
            )

    return ""


def _entry_malformed(entry: Mapping[str, Any]) -> str:
    """Why this context entry is not a context entry, or the empty string.

    Shape before meaning, and the ordering is load-bearing rather than tidy: the scope check below
    reads `person` and `item`, and the citation check coerces `seq` — so a `seq` of `"tomorrow"`
    would raise `ValueError` out of `refusal`, out of `_apply_statement_answer`, out of `step()` and
    into the tick task, where an unhandled exception is a stopped clock rather than a refused
    briefing. A guard that can crash the clock is worse than no guard.

    An unknown key is refused for the same reason the loader refuses one in a scenario file: this
    becomes a permanent logged fact, and a field nothing reads is either a typo doing nothing or
    something being smuggled past the size caps.
    """
    unknown = sorted(set(entry) - CONTEXT_EVENT_KEYS)
    if unknown:
        return f"a retrieved context event carries keys nothing reads: {unknown}"

    for key in ("seq", "tick"):
        value = entry.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return f"a retrieved context event's {key!r} is {value!r}; it is a whole number"

    for key in ("kind", "person", "item", "detail"):
        value = entry.get(key, "")
        if not isinstance(value, str) or len(value) > MAX_CONTEXT_FIELD_CHARS:
            return (
                f"a retrieved context event's {key!r} must be text of at most "
                f"{MAX_CONTEXT_FIELD_CHARS} characters"
            )

    return ""


def _entry_outside_scope(entry: Mapping[str, Any], authorized: Authorized) -> str:
    """Why this context entry is not this director's to read, or the empty string.

    Default-deny: an entry naming neither a person nor an item in scope is refused rather than
    admitted as company-wide. A company-wide fact is not another line's event, but it is not this
    line's either, and the retrieval that assembled the context is the place to decide it belongs —
    admitting it here would make the guard's answer depend on which kinds somebody remembered.
    """
    person = str(entry.get("person", ""))
    item = str(entry.get("item", ""))

    if person and not authorized.permits_person(person):
        return (
            f"the context holds an event about {person!r}, who is not in {authorized.director!r}'s "
            "reporting line (R23)"
        )
    if item and not authorized.permits_item(item):
        return (
            f"the context holds an event about item {item!r}, which is not assigned into "
            f"{authorized.director!r}'s reporting line (R23)"
        )
    if not person and not item:
        return (
            "the context holds an event naming neither a person nor an item, so nothing places it "
            f"inside {authorized.director!r}'s scope; cross-line reads are default-deny (R23)"
        )
    return ""


