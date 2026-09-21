"""What is worth automating, and why this run is the argument for it (M57, M58).

This is the report's one prescriptive claim, and it is the one most easily made indefensible.
The fast way to satisfy M57 is to hand a model the run and ask it what to automate; what comes
back reads well, cites nothing that resolves, and states a payback for a company that does not
exist. So the shape here is the opposite one, in three parts that each refuse something:

**The candidates are authored.** A proposal comes from `[[automation]]` in the scenario file —
a table the loader's closed key set admits and validates (U6, U21) — so the set of things this
report can ever propose is a set somebody wrote down and a reviewer approved. Nothing generates
a candidate, and there is no code path by which one could appear.

**The evidence is folded.** An authored candidate is *proposed* only where this run's own fold
found its line over its ceiling for `MIN_OVERLOAD_DAYS` consecutive sim-days, and every reading
behind that resolves to the `DAY_CHECKPOINT` the kernel wrote at that boundary (M55). A company
that never overloaded a line gets no proposals rather than an invented one — which is the
answer that makes the ones it does get worth reading.

**The payback is arithmetic the simulation already charges.** A file authors one number, the
hours a month an automation takes off a line, and everything stated in money or in days is
computed from it through `step.draw_cost_of` and `compare.runway_days` — the same two functions
the day boundary and the comparison branch use. Nothing is stored: a payback is recomputed from
the economy at the day boundary it cites, so it cannot drift from the burn that boundary paid.
Automating work lowers a department's draw, a draw is staffed work carried into the daily burn
(R60), and so an automation shortens the burn rather than only moving a number on screen.

**Then, and only then, a model writes prose over it** (M58). The prose is attached to a proposal
that already exists, over figures that already resolve: `refusal_of` refuses a sentence that
cites nothing, cites outside the proposal's own evidence, or carries a figure this module did
not compute. A producer that answers about a proposal nobody authored is discarded by
`attach`, because prose is matched to an id rather than trusted to name one. With no model
configured every proposal renders complete and says its prose is absent (M20's rule, applied to
the report) — the figures are the claim, and the sentences are the reading of it.

**The guard runs here as well as at the producer, and here is the auditable copy.** This is the
same two-call-site shape `simcore.statement` describes for a briefing: the producer refuses
early, next to the prompt, and so does not pay to cache a reply nobody may read; this module
refuses at the point of publication, because the report is what carries the claim into an
exported file somebody opens a year later.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from report import fold as reporting
from simcore import capacity as cap
from simcore import compare as comparing
from simcore import scenario as scenarios
from simcore import statement as stmt
from simcore import step as sim
from simcore import time as simtime

#: Attached to every figure, exactly as the rest of the report attaches it (R41).
AUTHORED = reporting.AUTHORED

#: Consecutive sim-days over the ceiling before a line's load is a pattern rather than a week.
#:
#: **Three, and it is a reporting threshold rather than a rule of the simulation** — which is why
#: it is here and not in `rates.TUNING`. Nothing about a run changes when it moves: two runs of
#: one seed produce the same log, the same state hashes and the same overload readings under any
#: value of it, and only what the report is willing to *prescribe* from them differs. Putting it
#: in the tuning table would have made it part of the rules identity and every existing run
#: unplayable for a number that decides nothing the kernel does.
#:
#: Three rather than one because a single day over is what a busy Tuesday looks like, and the
#: whole worth of the prescription is that it points at the standing problem. It is stated on the
#: payload, so a reader disagreeing with it can see the spans and count for themselves.
MIN_OVERLOAD_DAYS = 3

#: Where a proposal's prose stands. Three members, and the surface renders each differently.
WRITTEN = "written"
#: No model configured, or nothing to write over. The proposal is complete without it.
ABSENT = "absent"
#: A bench answered and this repository would not show what it said.
REFUSED = "refused"

#: How many sentences one proposal's prose may carry. Four: a proposal is a paragraph in a
#: document that holds several, and a model given more room describes the same figures again.
MAX_SENTENCES = 4


# =========================================================================
# The economy a payback is computed against
# =========================================================================


@dataclass(frozen=True, slots=True)
class Economy:
    """What one timeline's day cost, at one day boundary, and what the draw is made of.

    Captured by the day walk rather than reconstructed, so the terms are the state the kernel
    actually reached and the boundary is one the log can cite. It holds the *terms* rather than
    the whole `State` for the reason `walk_days` exists at all: sixteen folded states held for
    the life of a report is the cost this module is built to avoid.
    """

    run_id: str
    day: int
    at_tick: int
    at_seq: int
    cash: int
    fixed_cost: int
    salaries: int
    manual_hours: int
    #: Director id -> the monthly draw their line carries at this boundary. A proposal cannot
    #: remove more than its own line holds, because `capacity.apply_draw_change` clamps at zero.
    line_hours: Mapping[str, int] = field(default_factory=dict)
    #: Director id -> the load the kernel recorded for that line here, in per mille. Taken off
    #: the state rather than recomputed, so a payback's "before" is the same number the overload
    #: section reports and the same one the HUD showed.
    line_load: Mapping[str, int] = field(default_factory=dict)
    #: What that load is made of: the line's in-flight queue, in effort units, and the people
    #: it is measured against. Both are inputs to `capacity.load_permille`, which is how the
    #: counterfactual below is the kernel's own function over a smaller draw rather than this
    #: module's arithmetic about one.
    line_queued: Mapping[str, int] = field(default_factory=dict)
    line_headcount: Mapping[str, int] = field(default_factory=dict)

    @property
    def daily_cost(self) -> int:
        return self.fixed_cost + sim.draw_cost_of(self.manual_hours) + self.salaries


def economy_at(run_id: str, day: int, at_tick: int, at_seq: int, state: Any) -> Economy:
    """One boundary's terms, read through the kernel's own cost and load functions."""
    fixed, _, salaries = sim.day_cost_terms(state)
    return Economy(
        run_id=run_id,
        day=day,
        at_tick=at_tick,
        at_seq=at_seq,
        cash=int(state.metrics["cash"]),
        fixed_cost=fixed,
        salaries=salaries,
        manual_hours=sum(
            department.monthly_hours for department in state.capacity.values()
        ),
        line_hours={
            director: department.monthly_hours
            for director, department in state.capacity.items()
        },
        line_load={
            director: department.load_permille
            for director, department in state.capacity.items()
        },
        line_queued={
            director: state.queued_units(director) for director in state.capacity
        },
        line_headcount={
            director: len(state.present_members(director)) for director in state.capacity
        },
    )


def _figure(value: int | None) -> dict[str, Any]:
    """One number on the payload, marked (R41).

    Every figure is a dict rather than a bare integer so the marking cannot be dropped by
    somebody adding a field: `test_report.py` sweeps the payload for dicts holding a `value` and
    requires a `basis` on each, and a scalar would simply not be swept.
    """
    return {"value": value, "basis": AUTHORED}


# =========================================================================
# What a proposal is made of
# =========================================================================


@dataclass(frozen=True, slots=True)
class Payback:
    """What one proposal would give back, in one timeline, at one cited day boundary.

    **Recomputed, never stored.** The authored figure is `removes_hours` and nothing else; the
    burn, the saving and the runway all come from `step.draw_cost_of` and
    `compare.runway_days` — the functions the day boundary and the comparison branch already
    use. A payback written into the scenario would be a number nobody could reconcile against
    the company it describes, and the first tuning pass would make it wrong silently.

    `removes_hours` is clamped to what the line actually carries at this boundary, because
    `capacity.apply_draw_change` clamps a draw at zero: reporting the requested reduction where
    the run could only have taken part of it is the misreport that clamp already argues against.

    **And it states what the proposal does to the load, not only to the burn.** The evidence
    that motivates a proposal is an overloaded line; a payback that answered only in money would
    leave the reader to assume the two are the same size, and on the shipped companies they are
    often not — a draw of 40 hours a month is 111 per mille of a two-person line, so a proposal
    against a line sitting at 1288 because its work is stopped in front of the CEO moves the
    figure that motivated it by almost nothing. That is worth knowing and this is where it is
    said. The proposal stands: the burn saving is real and the candidate is authored. What the
    reader gets is both numbers instead of one and an invitation to infer the other.
    """

    run_id: str
    day: int
    at_tick: int
    at_seq: int
    #: Sim-days this line spent over its ceiling in this timeline, across every span.
    days_over: int
    removes_hours: int
    manual_hours_before: int
    daily_burn_before: int
    daily_burn_after: int
    runway_days_before: int | None
    runway_days_after: int | None
    #: The line's load here, and what it would be with the draw gone. `before` is the kernel's
    #: own recorded figure; `after` is `capacity.load_permille` over the same queue and the same
    #: headcount with a smaller draw.
    load_permille_before: int = 0
    load_permille_after: int = 0

    @property
    def daily_saving(self) -> int:
        return self.daily_burn_before - self.daily_burn_after

    @property
    def runway_days_gained(self) -> int | None:
        if self.runway_days_before is None or self.runway_days_after is None:
            return None
        return self.runway_days_after - self.runway_days_before

    @property
    def load_permille_removed(self) -> int:
        return self.load_permille_before - self.load_permille_after

    @property
    def clears_the_ceiling(self) -> bool:
        """Whether this alone would take the line back under its ceiling.

        The question the evidence actually raises, answered rather than implied. False is the
        common case and is not an argument against the proposal — it is the difference between
        "this fixes the overload" and "this is worth doing and something else is the overload",
        which a reader should not have to work out from two numbers.
        """
        return self.load_permille_before > cap.LOAD_CEILING >= self.load_permille_after

    def figures(self) -> tuple[int, ...]:
        """Every number this payback states, for the prose guard to resolve against."""
        stated = (
            self.day,
            self.at_tick,
            self.at_seq,
            self.days_over,
            self.removes_hours,
            self.manual_hours_before,
            self.daily_burn_before,
            self.daily_burn_after,
            self.daily_saving,
            self.runway_days_before,
            self.runway_days_after,
            self.runway_days_gained,
            self.load_permille_before,
            self.load_permille_after,
            self.load_permille_removed,
            cap.LOAD_CEILING,
        )
        return tuple(value for value in stated if isinstance(value, int))

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "day": self.day,
            "at_tick": self.at_tick,
            "at_seq": self.at_seq,
            "days_over": _figure(self.days_over),
            "removes_draw_hours_per_month": _figure(self.removes_hours),
            "manual_hours_before": _figure(self.manual_hours_before),
            "daily_burn_before": _figure(self.daily_burn_before),
            "daily_burn_after": _figure(self.daily_burn_after),
            "daily_saving": _figure(self.daily_saving),
            "runway_days_before": _figure(self.runway_days_before),
            "runway_days_after": _figure(self.runway_days_after),
            "runway_days_gained": _figure(self.runway_days_gained),
            "load_permille_before": _figure(self.load_permille_before),
            "load_permille_after": _figure(self.load_permille_after),
            "load_permille_removed": _figure(self.load_permille_removed),
            "clears_the_ceiling": self.clears_the_ceiling,
            "basis": AUTHORED,
        }


def payback_of(
    economy: Economy, *, director: str, hours: int, days_over: int
) -> Payback:
    """One proposal's payback at one boundary, through the company's own arithmetic."""
    removed = min(hours, economy.line_hours.get(director, 0))
    before = economy.daily_cost
    after = economy.fixed_cost + sim.draw_cost_of(economy.manual_hours - removed) + economy.salaries

    # The kernel's own load function over the same queue and headcount with a smaller draw.
    # A `DepartmentCapacity` is a plain record and this one is thrown away — the folded state
    # is a read, and a report that mutated it to answer a question would be the second writer
    # of a run's capacity.
    lighter = cap.DepartmentCapacity(
        director_id=director,
        monthly_hours=economy.line_hours.get(director, 0) - removed,
    )
    return Payback(
        run_id=economy.run_id,
        day=economy.day,
        at_tick=economy.at_tick,
        at_seq=economy.at_seq,
        days_over=days_over,
        removes_hours=removed,
        manual_hours_before=economy.manual_hours,
        daily_burn_before=before,
        daily_burn_after=after,
        runway_days_before=comparing.runway_days(economy.cash, before),
        runway_days_after=comparing.runway_days(economy.cash, after),
        load_permille_before=economy.line_load.get(director, 0),
        load_permille_after=cap.load_permille(
            lighter,
            economy.line_queued.get(director, 0),
            economy.line_headcount.get(director, 0),
        ),
    )


@dataclass(frozen=True, slots=True)
class Citation:
    """One event a proposal is motivated by, addressed by run and sequence together (M55).

    The pair rather than a sequence, for the reason `fold.Claim` gives: a fork copies its
    parent's rows, so a bare sequence is an address only to whoever already knows which log it
    came from — and a proposal cites across every timeline in the tree at once.
    """

    run_id: str
    at_seq: int
    at_tick: int
    note: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "at_seq": self.at_seq,
            "at_tick": self.at_tick,
            "at_day": simtime.day_of(self.at_tick),
            "note": self.note,
        }


@dataclass(frozen=True, slots=True)
class Sentence:
    """One line of generated prose, and the sequences it was written over."""

    text: str
    citations: tuple[int, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"text": self.text, "citations": list(self.citations)}


@dataclass(frozen=True, slots=True)
class Note:
    """The prose over one proposal, or the stated absence of it (M58)."""

    status: str = ABSENT
    sentences: tuple[Sentence, ...] = ()
    model_identity: str = ""
    #: Why there is no prose, when a bench answered and this repository would not show it.
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "sentences": [sentence.to_dict() for sentence in self.sentences],
            "model_identity": self.model_identity,
            "reason": self.reason,
            #: Said on the payload rather than left to a caption: this is the one part of a
            #: proposal a model wrote, and a reader is entitled to know which part that is.
            "written_by": "a model, over figures it did not compute" if self.sentences else "",
        }


@dataclass(slots=True)
class Proposal:
    """One authored candidate, the run's evidence for it, and what it would give back."""

    id: str
    line: str
    director: str
    director_name: str
    title: str
    detail: str
    removes_hours: int
    #: How many timelines were folded, and in how many of them this line was over long enough
    #: to motivate the proposal. Never a sum of days across the tree — timelines share a prefix,
    #: so adding a parent's overloaded days to its children's counts the same days twice.
    timelines: int
    timelines_over: int
    #: True when no timeline escaped it, which is what makes this a standing problem rather than
    #: one a decision caused. `universe.LineOverload.everywhere` is the same claim about the line.
    everywhere: bool
    evidence: list[Citation] = field(default_factory=list)
    paybacks: list[Payback] = field(default_factory=list)
    note: Note = field(default_factory=Note)

    def citable(self) -> frozenset[int]:
        """Sequences the prose may cite.

        By sequence alone, while the evidence carries the pair. A proposal's evidence is a closed
        list this module built, so a cited sequence resolves to at least one named event in it —
        and the surface renders the citation against that list rather than searching the tree.
        """
        return frozenset(
            [citation.at_seq for citation in self.evidence]
            + [payback.at_seq for payback in self.paybacks]
        )

    def resolvable(self) -> frozenset[int]:
        """Every figure this proposal states, which is every figure its prose may write (M58).

        The same rule `memory.refusal_of` applies to a director's summary and for the same
        reason: a figure is a numeral, and a numeral the reader cannot find in what they are
        looking at is a number a model made up.
        """
        figures: set[int] = {
            self.removes_hours,
            self.timelines,
            self.timelines_over,
            MIN_OVERLOAD_DAYS,
        }
        for citation in self.evidence:
            figures.add(citation.at_seq)
            figures.add(citation.at_tick)
            figures.add(simtime.day_of(citation.at_tick))
            figures |= stmt.figures_in(citation.note)
        for payback in self.paybacks:
            figures |= set(payback.figures())
        figures |= stmt.figures_in(self.title)
        figures |= stmt.figures_in(self.detail)
        return frozenset(figures)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "line": self.line,
            "director": self.director,
            "director_name": self.director_name,
            "title": self.title,
            "detail": self.detail,
            "removes_draw_hours_per_month": _figure(self.removes_hours),
            "timelines": self.timelines,
            "timelines_over": self.timelines_over,
            "everywhere": self.everywhere,
            "evidence": [citation.to_dict() for citation in self.evidence],
            "payback": [payback.to_dict() for payback in self.paybacks],
            "note": self.note.to_dict(),
            "basis": AUTHORED,
        }

    def to_packet(self) -> dict[str, Any]:
        """What a prose producer is given, and the whole of what it is given.

        A payload rather than the dataclass, for the reason `read_memory` answers with one: the
        producer is in another service, reached through a callable the launcher installed, and
        handing it a live object would be handing it this module. What it cannot see, it cannot
        write about — and `citable` and `resolvable` travel with it so the cheap refusal at the
        producer and the auditable one here are refusing against the same two sets.
        """
        return {
            "proposal": self.id,
            "line": self.line,
            "director_name": self.director_name,
            "title": self.title,
            "detail": self.detail,
            "timelines": self.timelines,
            "timelines_over": self.timelines_over,
            "everywhere": self.everywhere,
            "removes_draw_hours_per_month": self.removes_hours,
            "evidence": [citation.to_dict() for citation in self.evidence],
            "payback": [payback.to_dict() for payback in self.paybacks],
            "citable": sorted(self.citable()),
            "resolvable": sorted(self.resolvable()),
            "max_sentences": MAX_SENTENCES,
        }


# =========================================================================
# Selecting the candidates this run is the argument for (M57)
# =========================================================================


def propose(
    company: scenarios.Scenario | None,
    overload: Sequence[Any],
    timelines: Sequence[Any],
) -> list[Proposal]:
    """The authored candidates this Universe's own fold motivates, in authored order.

    `overload` is `universe.LineOverload` per line and `timelines` is `universe.TimelineReport`
    per node; both are passed rather than imported, because `universe` imports this module and
    the edge has to run one way. The order is the file's, so adding a fork appends evidence to a
    proposal rather than reordering the section.

    A candidate whose line no timeline overloaded for long enough produces **nothing** — not an
    entry saying so. A proposal is a claim that this company should spend money on something,
    and the report has no business making one it cannot support from its own fold.
    """
    if company is None:
        return []

    folded = [timeline for timeline in timelines if not timeline.refusal]
    by_line = {line.line: line for line in overload}
    out: list[Proposal] = []

    for candidate in company.automations:
        line = by_line.get(candidate.line)
        if line is None:
            # A scenario cannot author a fifth line and the loader cross-checks the four, so
            # this is a Universe with no folded timeline to read a department off at all.
            continue

        motivated = [
            (timeline, spans)
            for timeline in folded
            if (spans := _qualifying(timeline, line.director))
        ]
        if not motivated:
            continue

        proposal = Proposal(
            id=candidate.id,
            line=candidate.line,
            director=line.director,
            director_name=line.director_name,
            title=candidate.title,
            detail=candidate.detail,
            removes_hours=candidate.removes_draw_hours_per_month,
            timelines=len(folded),
            timelines_over=len(motivated),
            everywhere=len(motivated) == len(folded),
        )

        for timeline, spans in motivated:
            proposal.evidence.extend(_evidence(timeline, line, spans, candidate))
            # A timeline whose every day boundary was uncitable has no economy to divide, and
            # it gets evidence with no payback rather than a payback measured at a boundary the
            # log cannot name — the same rule `LoadReading` applies one level down.
            economy = timeline.economy
            if economy is not None:
                proposal.paybacks.append(
                    payback_of(
                        economy,
                        director=line.director,
                        hours=candidate.removes_draw_hours_per_month,
                        days_over=sum(
                            span.days
                            for span in timeline.spans
                            if span.director == line.director
                        ),
                    )
                )
        out.append(proposal)

    return out


def _qualifying(timeline: Any, director: str) -> list[Any]:
    """This timeline's spans for one line that are long enough to motivate a proposal."""
    return [
        span
        for span in timeline.spans
        if span.director == director and span.days >= MIN_OVERLOAD_DAYS
    ]


def _evidence(
    timeline: Any, line: Any, spans: list[Any], candidate: scenarios.Automation
) -> list[Citation]:
    """The events one timeline contributes: the standing overload, and the work that names it.

    The first qualifying span's opening and its worst day, because those are the two readings a
    reader checks — when it started and how bad it got — and both address a `DAY_CHECKPOINT` the
    kernel wrote. Then every decision taken on an item the catalog says would deliver this
    automation, which is the other half of "the events that motivate it": a CEO who has already
    settled a checkpoint on the duplicate order entry has said out loud that it is a problem.
    """
    span = spans[0]
    opened = (
        f"{line.line} went over its ceiling on day {span.from_day} and stayed over "
        f"to day {span.to_day}"
    )
    peaked = (
        f"its worst day was day {span.peak_day}, at {span.peak_permille} per mille of what "
        "the line can carry"
    )

    # One citation when the worst day *is* the opening day, which is what a company authored
    # over its ceiling at genesis looks like. Two entries addressing one event would render as
    # two chips the reader clicks onto the same place, and would double-count the evidence a
    # proposal appears to rest on.
    if span.peak_at_seq == span.opened_at_seq:
        found = [
            Citation(
                run_id=span.run_id,
                at_seq=span.opened_at_seq,
                at_tick=simtime.tick_of_day_start(span.from_day),
                note=f"{opened}, and {peaked}",
            )
        ]
    else:
        found = [
            Citation(
                run_id=span.run_id,
                at_seq=span.opened_at_seq,
                at_tick=simtime.tick_of_day_start(span.from_day),
                note=opened,
            ),
            Citation(
                run_id=span.run_id,
                at_seq=span.peak_at_seq,
                at_tick=simtime.tick_of_day_start(span.peak_day),
                note=peaked,
            ),
        ]

    report = timeline.report
    if report is None:  # pragma: no cover - a timeline with spans has been folded
        return found

    for decision in report.decisions:
        if decision.item not in candidate.motivated_by:
            continue
        found.append(
            Citation(
                run_id=decision.run_id,
                at_seq=decision.at_seq,
                at_tick=decision.at_tick,
                note=f"the CEO settled {decision.item}: {decision.choice}",
            )
        )
    return found


def claims_of(proposals: Sequence[Proposal]) -> list[reporting.Claim]:
    """The prescription's own figures, each addressed like every other claim in the report.

    One per payback rather than one per proposal, because a payback is a per-timeline fact
    measured at a per-timeline boundary — and the day checkpoint it cites is what makes "follow
    this number back to its event" one lookup. A tree-wide saving would be produced by no event
    at all, which is the claim `universe._claims` already declines to make.
    """
    claims: list[reporting.Claim] = []
    for proposal in proposals:
        for payback in proposal.paybacks:
            claims.append(
                reporting.Claim(
                    run_id=payback.run_id,
                    label=f"daily burn saved by {proposal.id}",
                    value=payback.daily_saving,
                    at_seq=payback.at_seq,
                    at_tick=payback.at_tick,
                )
            )
    return claims


# =========================================================================
# The prose, and what it may say (M58)
# =========================================================================


def attach(proposals: Sequence[Proposal], written: Iterable[Mapping[str, Any]]) -> None:
    """Put a producer's prose on the proposals it is about, refusing anything it may not say.

    **Matched by id, never trusted to name one.** A reply about a proposal this report did not
    make is discarded rather than rendered: the catalog is what decides what may be proposed,
    and a producer that could add an entry by naming it would be the generated prescription this
    whole module exists to prevent.

    Every reply is re-guarded here, whatever the producer already did. That is the two-call-site
    shape `simcore.statement` describes: the producer's guard is the cheap one and keeps an
    unusable reply out of the response cache, and this one is the auditable one, because this is
    the copy that reaches the exported file.
    """
    by_id = {proposal.id: proposal for proposal in proposals}

    for reply in written:
        proposal = by_id.get(str(reply.get("proposal", "")))
        if proposal is None:
            continue

        reason = str(reply.get("refusal", ""))
        if reason:
            proposal.note = Note(status=REFUSED, reason=reason)
            continue

        sentences = _sentences_of(reply.get("sentences", []))
        if not sentences:
            proposal.note = Note(status=ABSENT)
            continue

        refused = refusal_of(sentences, proposal)
        if refused:
            proposal.note = Note(status=REFUSED, reason=refused)
            continue

        proposal.note = Note(
            status=WRITTEN,
            sentences=sentences,
            model_identity=str(reply.get("model_identity", ""))[: stmt.MAX_IDENTITY_CHARS],
        )


def _sentences_of(raw: Any) -> tuple[Sentence, ...]:
    """A reply's sentences, as far as they are the shape this reads. Tolerant, then guarded."""
    if not isinstance(raw, list):
        return ()
    found: list[Sentence] = []
    for entry in raw:
        if not isinstance(entry, Mapping):
            continue
        text = str(entry.get("text", "")).strip()
        cited = entry.get("citations", [])
        if not text or not isinstance(cited, list):
            continue
        found.append(
            Sentence(
                text=text,
                citations=tuple(
                    int(value)
                    for value in cited
                    if isinstance(value, int) and not isinstance(value, bool)
                ),
            )
        )
    return tuple(found)


def refusal_of(sentences: Sequence[Sentence], proposal: Proposal) -> str:
    """Why this prose may not be published, or the empty string.

    Four rules, ordered shape before meaning, and each one is about what its absence would let
    into a document that leaves the machine:

    * a sentence with no citation is a claim with nothing behind it, and the whole promise of
      this report is that every claim resolves to an event;
    * a citation outside the proposal's own evidence resolves to nothing the reader is shown,
      so the chip under the sentence would point at a hole;
    * **a figure that resolves to nothing is M58**, and it is the rule the requirement exists
      for: a payback a model computed is exactly the number nobody can check;
    * a control character or an over-long sentence is text this repository will not interpolate
      into an exported HTML file, whoever produced it.

    `simcore.statement.ranking` is deliberately *not* applied, and the reason is worth stating
    because every other prose guard in the tree applies it. A director may not rank the options
    at a checkpoint because the choosing is the CEO's; a proposal is the report recommending
    something, which is what M57 asks it to do. What stops that from being a model's opinion is
    not a vocabulary check — it is that the candidate was authored and the figures were folded
    before any prose existed.
    """
    citable = proposal.citable()
    resolvable = proposal.resolvable()

    if len(sentences) > MAX_SENTENCES:
        return (
            f"the prose for {proposal.id} runs to {len(sentences)} sentences; the limit is "
            f"{MAX_SENTENCES}"
        )

    for sentence in sentences:
        offending = scenarios.control_character(sentence.text)
        if offending:
            return f"a sentence contains {offending}, which no generated line may carry"
        if len(sentence.text) > scenarios.MAX_PROSE_CHARS:
            return (
                f"a sentence is {len(sentence.text)} characters; the limit is "
                f"{scenarios.MAX_PROSE_CHARS}"
            )

        if not sentence.citations:
            return (
                f"the sentence {sentence.text[:60]!r} cites nothing, so no event in this "
                "Universe stands behind it (M55)"
            )
        for cited in sentence.citations:
            if cited not in citable:
                return (
                    f"sequence {cited} is cited but is not among the events this proposal was "
                    f"drawn from, so it resolves to nothing the reader is shown (M55)"
                )

        for token in stmt.figures_in(sentence.text):
            if token not in resolvable:
                return (
                    f"the sentence {sentence.text[:60]!r} carries the figure {token}, which no "
                    "figure in this proposal resolves to (M58)"
                )

    return ""
