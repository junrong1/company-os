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

**The two guards M18 and M19 name are here, and they are here once.** `ranking` refuses a statement
that names a preferred option, ranks the options or asserts one is better; `unresolved_figure`
refuses a number that resolves to no event, no option and no authored figure the director was shown.
Both are pure functions over the same inputs, both are called from `refusal`, and `bench/guards.py`
is the agents-side *call site* rather than a second copy. Both are lexical over a closed vocabulary,
which is not a stylistic choice: the refusal they produce is an output event the fold regenerates on
every replay, so a predicate that consulted a model would make the *record* of a refusal
irreproducible.

U15 constrains `Authorized` rather than replacing it: an Authorization grant widens the scope a
director is given, and the signature it has to widen exists here already.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Any

from simcore import items as work
from simcore import scenario as sc
from simcore import time as simtime

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
KEY_FALLBACK = "fallback"

#: Where a statement came from. A closed set, and neither value carries provider words: R5 forbids
#: provider-supplied text in the log, and the report reads these to say who spoke.
PRODUCER_MODEL = "model"
PRODUCER_SCRIPTED = "scripted"
PRODUCER_KINDS = (PRODUCER_MODEL, PRODUCER_SCRIPTED)

#: A guard refused what the provider said, so the scripted reply stands in its place.
#:
#: The one member of the set below that is not a `modelgw.FailureKind`, because it is the one
#: condition that is *ours*: the provider answered, and this repository decided the answer could not
#: be shown. Naming it separately is what lets a report distinguish "the bench was unreachable" from
#: "the bench ranked the options", which are the same outcome for the player and completely
#: different problems for whoever is reading the log afterwards.
FALLBACK_GUARD_REFUSED = "guard_refused"

#: Why a scripted reply stands where a briefing would have. A closed set (R5, M21).
#:
#: Every member but the first is the *value* of a `modelgw.FailureKind`, spelled here rather than
#: imported because `tests/test_import_boundaries.py` forbids `simcore` from importing the model
#: gateway at all — the sim must not learn what a provider is. Two spellings of one set is exactly
#: the drift this file's own docstring argues against, so `tests/test_bench.py` asserts the two agree
#: as a set rather than trusting that whoever adds a failure kind remembers this line.
#:
#: **`not_configured` is deliberately absent, and its absence is M20.** A run with no key configured
#: raises the request and lets it reach its deadline, exactly as a U10-only build did: the
#: conversation is the Phase 2 conversation, with no canned block in it. A fallback is what a
#: *configured* bench produces when it fails, and "there is no bench" is not a failure.
FALLBACK_REASONS = (
    FALLBACK_GUARD_REFUSED,
    "auth_rejected",
    "ceiling_reached",
    "empty_response",
    "gateway_fault",
    "invalid_request",
    "malformed_response",
    "provider_error",
    "rate_limited",
    "timeout",
    "unreachable",
)

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
    #: Empty for a briefing a model produced; one of `FALLBACK_REASONS` for a scripted reply
    #: standing in its place (M21). The client renders it as the stated reason rather than leaving
    #: canned prose to be mistaken for a briefing.
    fallback: str = ""

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
            KEY_FALLBACK: self.fallback,
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


@dataclass(frozen=True, slots=True)
class Offered:
    """The checkpoint as it was put to the director: what may be named, and which figures resolve.

    Both guards below need it, and neither can be checked without it. M18 is about the *options* —
    a statement that names a preferred one is refused — so the labels have to be known to the
    predicate rather than guessed at from the prose. M19 is about *figures*, and the authored side of
    what resolves is here: an option's deltas, and the digits in the copy the director was shown.

    **Two adapters build it, and neither side computes the other's.** The kernel builds one from
    folded state at the landing tick, which is the authoritative reading, and the answering leg builds
    one from the genesis catalog, which is the same authored content projected onto the wire. This is
    deliberately *not* the shape `Authorized` has — a scope is carried on the request precisely so a
    leg cannot widen it — and the reason the two differ is worth stating: a scope is state-dependent
    and is a permission, while a checkpoint's options are immutable authored content already sent to
    every client. There is nothing here a leg could widen in its own favour, and the kernel's copy
    decides regardless.
    """

    #: Option labels, in authored order. What M18 refuses a statement for preferring.
    labels: tuple[str, ...]
    #: Every whole number the authored content in front of the director shows.
    figures: frozenset[int]

    @classmethod
    def from_checkpoint(cls, item: work.ItemSpec, cp_index: int) -> Offered:
        """From folded state, which is what the kernel's verdict is derived from.

        `rendered_prompt` rather than the raw template, because the rendered form is what the client
        shows and therefore what the prompt puts in front of the director — the template's
        `{hours}` is not a figure anybody was shown.
        """
        checkpoint = item.checkpoints[cp_index]
        figures: set[int] = set(_digits_in(work.rendered_prompt(item, cp_index)))
        figures.update(_digits_in(checkpoint.label))
        for option in checkpoint.options:
            figures.update(_digits_in(option.label))
            figures.update(_digits_in(option.detail))
            figures.update(_digits_in(option.note))
            # Zero is skipped in both adapters, and the reason is agreement rather than tidiness:
            # `catalog_to_state` splits the draw out of `effect` and writes it as `draw_delta: 0`
            # where an option does not move it, so admitting zero here would make the wire's reading
            # of one authored checkpoint hold a figure the state's reading did not.
            figures.update(abs(int(delta)) for delta in option.effect.values() if delta)
        return cls(
            labels=tuple(option.label for option in checkpoint.options),
            figures=frozenset(figures),
        )

    @classmethod
    def from_catalog(cls, entry: Mapping[str, Any], cp_index: int) -> Offered:
        """From the genesis catalog, which is the same authored content on the wire.

        Tolerant of a missing key rather than raising, because this reads a payload: the leg that
        calls it is optional by construction, and an exception here would take out a briefing over a
        catalog shape rather than declining one. A checkpoint it cannot find yields an empty offer,
        which refuses every figure — default-deny in the direction that costs a briefing rather than
        the direction that admits an invented number.
        """
        checkpoints = entry.get("checkpoints", [])
        if not isinstance(checkpoints, list) or not 0 <= cp_index < len(checkpoints):
            return cls(labels=(), figures=frozenset())
        checkpoint = checkpoints[cp_index]
        if not isinstance(checkpoint, Mapping):
            return cls(labels=(), figures=frozenset())

        figures: set[int] = set(_digits_in(str(checkpoint.get("prompt", ""))))
        figures.update(_digits_in(str(checkpoint.get("label", ""))))
        labels: list[str] = []
        options = checkpoint.get("options", [])
        for option in options if isinstance(options, list) else ():
            if not isinstance(option, Mapping):
                continue
            labels.append(str(option.get("label", "")))
            for key in ("label", "detail", "note"):
                figures.update(_digits_in(str(option.get(key, ""))))
            effect = option.get("effect", {})
            if isinstance(effect, Mapping):
                figures.update(
                    abs(int(delta))
                    for delta in effect.values()
                    if isinstance(delta, int) and not isinstance(delta, bool) and delta
                )
            draw_delta = option.get("draw_delta", 0)
            if isinstance(draw_delta, int) and not isinstance(draw_delta, bool) and draw_delta:
                figures.add(abs(draw_delta))
        return cls(labels=tuple(labels), figures=frozenset(figures))


# =========================================================================
# M18: a director briefs and objects, and never ranks
# =========================================================================
#
# Lexical, over a closed vocabulary, and that is the only kind of predicate this can be: it runs in
# two processes, has to agree with itself byte-for-byte, and its verdict is regenerated by the fold
# on every replay. A model asked to judge whether another model ranked something would make the
# guard non-deterministic, which would make the *refusal event* non-reproducible — and the refusal is
# the auditable half.

#: Phrases that assert a preference or an ordering, whoever they are said about.
#:
#: Matched against a flattened form of the prose, so punctuation and spacing cannot smuggle one past.
#: A closed list is a stated boundary rather than a claim of completeness: what it buys is that the
#: obvious ways to rank are refused deterministically and the prompt is told not to try. What escapes
#: it is caught by the CEO reading a briefing that argues for one option, which is a product problem
#: rather than a silent one.
RANKING_PHRASES = (
    "i recommend",
    "i would recommend",
    "my recommendation",
    "i advise",
    "my advice",
    "i would choose",
    "i would go with",
    "i would take",
    "you should choose",
    "you should take",
    "you should go with",
    "the best option",
    "the best choice",
    "the right choice",
    "the right call",
    "the safest option",
    "the strongest option",
    "the weakest option",
    "the worst option",
    "the obvious choice",
    "the obvious answer",
    "first choice",
    "second choice",
    "in order of preference",
    "ranked",
    "i rank",
    "i prefer",
    "we prefer",
    "is preferable",
    "is the better",
    "is the best",
    "is the worst",
    "clearly better",
    "clearly worse",
    "outweighs",
)

#: Tokens that put two things in an order. Only refused *between two option labels*, and that
#: narrowing is what keeps the predicate precise: "the close runs long rather than short" is a
#: director describing their department, while "keep the paper rather than drop it" is a director
#: doing the CEO's comparing. The first must survive and the second must not, and the option labels
#: are what tells them apart.
COMPARATIVE_TOKENS = (
    " than ",
    " over ",
    " versus ",
    " vs ",
    " rather than ",
    " instead of ",
    " ahead of ",
    " beats ",
)


def ranking(briefing: str, objection: str, offered: Offered) -> str:
    """Why this statement ranks the options, or the empty string (M18).

    Two independent checks, because there are two ways to rank and only one of them uses a word that
    means it. The first is a preference phrase anywhere in the prose. The second is a comparative
    construction *between two of the options on offer* — which carries no preference word at all, and
    is the shape a model reaches for when it has been told not to recommend one.
    """
    for field_name, text in ((KEY_BRIEFING, briefing), (KEY_OBJECTION, objection)):
        flat = _flattened(text)
        for phrase in RANKING_PHRASES:
            if phrase in flat:
                return (
                    f"the {field_name} says {phrase!r}, which ranks the options or names a "
                    "preferred one. A director briefs and objects; the choice is the CEO's (M18)"
                )

        # Over the whole field rather than per sentence. Scoping it to a sentence would let a
        # comparison survive by putting a full stop between the two labels, and "keep the paper.
        # Rather than drop it." is the same ranking as the version with a comma.
        named = [label for label in offered.labels if label and _flattened(label).strip() in flat]
        if len(set(named)) < 2:
            continue
        for token in COMPARATIVE_TOKENS:
            if token in flat:
                return (
                    f"the {field_name} puts {named[0]!r} and {named[1]!r} in an order with "
                    f"{token.strip()!r}. Comparing the options against each other is the "
                    "comparison surface's job, and it marks its figures; a briefing may not (M18)"
                )
    return ""


# =========================================================================
# M19: every figure resolves to something the director was shown
# =========================================================================


def unresolved_figure(
    briefing: str, objection: str, context: Mapping[str, Any], offered: Offered
) -> str:
    """Why a figure in this statement resolves to nothing, or the empty string (M19).

    **Digits, not words.** `"two reasons"` is prose and `"2 reasons"` is a figure, and the prompt
    says so. The line is drawn at the numeral because that is where every other figure in this
    product is drawn — the HUD's completeness sweep is over rendered numerals, and a guard that tried
    to resolve spelled-out counts would refuse "the close takes five days for two reasons", which is
    an authored sentence already in the shipped company.

    **A derived figure is not a shown figure.** The resolvable set is what the director was handed:
    the sequences and ticks of its retrieved context, the sim-days those ticks fall in, its
    department's draw, and the digits in the authored copy and option deltas. A statement that
    converts 550 per-mille into "55%" is refused, and that is the intended reading of M19 rather
    than a limitation of it — a figure the CEO cannot resolve back to a row is a figure the report
    cannot either.
    """
    resolvable = _resolvable(context, offered)

    for field_name, text in ((KEY_BRIEFING, briefing), (KEY_OBJECTION, objection)):
        for token in _FIGURE.findall(text):
            bare = token.replace(",", "").rstrip(".")
            if not bare:
                continue
            if "." in bare:
                return (
                    f"the {field_name} carries the figure {token!r}, and every figure in this "
                    "company is a whole number; nothing the director was shown could have produced "
                    "it (M19)"
                )
            if int(bare) not in resolvable:
                return (
                    f"the {field_name} carries the figure {token!r}, which resolves to no event, "
                    "no option and no authored figure in the context this statement was produced "
                    "from (M19)"
                )
    return ""


def _resolvable(context: Mapping[str, Any], offered: Offered) -> frozenset[int]:
    """Every whole number this statement is entitled to say.

    Assembled from the *logged* context rather than from a fresh retrieval, which is what keeps the
    kernel's verdict re-derivable from the log alone: a reader with the event and the scenario can
    rebuild this set exactly and check the prose against it, with no store and no model.
    """
    figures: set[int] = set(offered.figures)

    for key in ("since_seq", "through_seq"):
        value = context.get(key, 0)
        if isinstance(value, int) and not isinstance(value, bool):
            figures.add(value)

    entries = context.get("events", [])
    for entry in entries if isinstance(entries, list) else ():
        if not isinstance(entry, Mapping):
            continue
        seq = entry.get("seq")
        if isinstance(seq, int) and not isinstance(seq, bool):
            figures.add(seq)
        tick = entry.get("tick")
        if isinstance(tick, int) and not isinstance(tick, bool):
            figures.add(tick)
            # The day, because that is the unit a director speaks in and the unit the HUD renders.
            # `day_of` rather than a division written here: one convention, in the module that owns
            # it, so a guard cannot disagree with the surface about which day a tick is.
            figures.add(simtime.day_of(tick))
        figures.update(_digits_in(str(entry.get("detail", ""))))

    draw = context.get("draw", {})
    if isinstance(draw, Mapping):
        figures.update(
            value for value in draw.values() if isinstance(value, int) and not isinstance(value, bool)
        )

    figures.update(_digits_in(str(context.get("unlocking_note", ""))))
    return frozenset(figures)


#: A run of digits, with the separators a number is written with. Deliberately greedy over commas and
#: dots so that `1,200` and `7.5` arrive as one token each rather than as `1` and `200` — a splitting
#: pattern would resolve `1,200` by finding `200` in the context and calling it cited.
_FIGURE = re.compile(r"\d[\d,.]*")

#: Anything that is not a letter or a digit, for flattening prose before a phrase match.
_NOT_WORD = re.compile(r"[^a-z0-9]+")


def _flattened(text: str) -> str:
    """Lowercased, punctuation flattened to single spaces, padded at both ends.

    Padded so that a phrase list can be written with leading and trailing spaces where word
    boundaries matter — `" vs "` must not match inside "visits" — without every entry needing a
    regex.
    """
    return f" {_NOT_WORD.sub(' ', text.lower()).strip()} "


def _digits_in(text: str) -> set[int]:
    """Every whole number written in digits in this text."""
    found: set[int] = set()
    for token in _FIGURE.findall(text):
        bare = token.replace(",", "").rstrip(".")
        if bare and "." not in bare:
            found.add(int(bare))
    return found


def refusal(answer: Mapping[str, Any], *, authorized: Authorized, offered: Offered) -> str:
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

    # The producer kind and the fallback reason are one fact spelled two ways, so they are checked
    # against each other rather than separately. A scripted statement with no reason would be a
    # canned briefing nobody could explain, and a generated one carrying a reason would say the
    # provider both answered and did not — and the report reads both fields to say who spoke.
    reason = answer.get(KEY_FALLBACK, "")
    if not isinstance(reason, str) or (reason and reason not in FALLBACK_REASONS):
        return f"{reason!r} is not a fallback reason; the set is {list(FALLBACK_REASONS)}"
    if producer_kind == PRODUCER_SCRIPTED and not reason:
        return (
            "a scripted statement stands in for a briefing that did not arrive, so it names the "
            f"condition that fired (M21); the set is {list(FALLBACK_REASONS)}"
        )
    if producer_kind == PRODUCER_MODEL and reason:
        return (
            f"a statement the model produced carries no fallback reason, and this one says "
            f"{reason!r}"
        )
    if producer_kind == PRODUCER_SCRIPTED and identity:
        # R6: nothing that identifies a provider may enter the log for a turn no provider answered.
        return "a scripted statement carries no model identity, and this one names one"

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

    # Last, and last on purpose. Both predicates read the prose and the context, and both would
    # report an invented failure about a malformed one — the figure check resolves against
    # `context["events"]`, which is only known to be a list of well-shaped entries by this line.
    #
    # A scripted reply is held to both as well, and is not exempted for being ours. The authored
    # deflection a fallback speaks in is content somebody edits, and a company whose authored line
    # ranked the options would ship a guard that never fired on the one statement it was certain to
    # see.
    ranked = ranking(briefing, objection, offered)
    if ranked:
        return ranked

    return unresolved_figure(briefing, objection, context, offered)


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


