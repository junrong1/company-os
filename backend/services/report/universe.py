"""The Universe report: one artifact over a whole tree of timelines (M53–M56, M59).

A run report explains one timeline. This explains the Universe a Genesis produced: every timeline
forked from it, what separated each from its parent, and — the question the product is finally
for — where the company was overloaded, in which lines, on which days, and whether any decision
the CEO took ever changed that.

**It is identified by its lineage root, not by the timeline the player happens to be standing
in.** Two players who fork the same Genesis differently produce two Universes; one player
standing in a child and one standing in its parent are looking at the same Universe and must get
the same artifact. U22 mails this file to somebody, and a report whose identity depended on a
camera position would be a different document every time it was exported.

**Every claim carries a run and a sequence, and that is a change forks forced** (M55). A fork
copies its parent's rows verbatim — `INSERT..SELECT` over every column but `run_id` — so
sequence 42 exists in the parent and in every child, and below the divergence it is the same
event while above it, it is not. A bare sequence stopped being an address, so `Claim` and
`DecisionRecord` carry the pair.

**Overload is folded, not read off the log, and the plan's claim that the load events already
carry it is wrong.** `LOAD_CHANGED` is emitted only on a day that produced morale counters — a
department must have driven somebody *below the morale threshold* before the log says a word
about its load. Measured on a driven run: `dir_cs` sat at 1277 per-mille against a ceiling of
1000 for **twelve consecutive days**, and the log named it on three of them. Nine days of
continuous overload left no load event at all. So the answer to "when was this line overloaded"
comes from the fold at each day boundary, where the figure is exact, and each reading cites the
`DAY_CHECKPOINT` the kernel wrote at that tick — which is both the citation M55 asks for and the
hash that proves the reading is the kernel's own state rather than this module's reading of it.

Day boundaries rather than every tick is not a compromise: over-ceiling load costs morale at a
day boundary and nowhere else (`step._end_of_day`), so a day is the granularity the *simulation*
samples load at. A finer series here would report pressure the run never charged anybody for.

**One pass per timeline, not one per day.** `fold.walk_days` resumes each day from the last
rather than folding from zero; the measurement and the reason are in its section of `fold.py`.

**A timeline that cannot be folded costs its own section and nothing else.** The report catches
per timeline and carries the refusal where that timeline's figures would have been, for the same
reason the plan gives for keeping the report off the bench: one broken input must not take the
whole chain and everything downstream of it.

**And then it prescribes, from an authored catalog over folded evidence** (M57, M58). The
selection, the arithmetic and the guard on what a model may write over them are in
`report.proposals`; what is here is the edge: this module hands that one the overload it folded
and the economy each timeline reached, and carries the proposals back on the payload. The
direction is fixed — `proposals` imports nothing from here — so the module that decides what may
be proposed cannot reach the module that assembles the document around it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from logschema import lineage
from report import fold as reporting
from report import proposals as prescribing
from simcore import capacity as cap
from simcore import scenario as scenarios
from simcore import time as simtime
from simcore import verify as verifier

#: Attached to every figure, exactly as the run report attaches it (R41).
AUTHORED = reporting.AUTHORED


def invented_company(company: scenarios.Scenario) -> str:
    """M59's plain statement, naming the company and where it was authored.

    Said as a sentence rather than as a flag, because this artifact is the one thing here that
    leaves the machine. Its reader is not the operator, has not seen the office, and has no
    reason to assume the figures describe a simulation unless the document says so.
    """
    return (
        f"{company.title} is an invented company. Its people, its work, its costs and every "
        f"figure in this report are authored content in scenarios/{company.scenario_id}.toml — "
        "a simulation, not a measurement of anything that happened outside it."
    )


# =========================================================================
# Overload, by line and over time (M56)
# =========================================================================


@dataclass(frozen=True, slots=True)
class LoadReading:
    """One reporting line's load at one day boundary, and the event that proves it.

    `at_seq` is the `DAY_CHECKPOINT` the kernel wrote at this tick, or `GENESIS` on day 1, which
    opens at tick 0 where no checkpoint is written. A boundary the log cannot cite produces no
    reading at all — M55 is a claim about every figure, so a figure that cannot name its event is
    one this report does not present.
    """

    run_id: str
    director: str
    line: str
    day: int
    at_tick: int
    at_seq: int
    load_permille: int
    over: bool
    basis: str = AUTHORED

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "director": self.director,
            "line": self.line,
            "day": self.day,
            "at_tick": self.at_tick,
            "at_seq": self.at_seq,
            "load_permille": self.load_permille,
            "over": self.over,
            "basis": self.basis,
        }


@dataclass(frozen=True, slots=True)
class OverloadSpan:
    """Consecutive sim-days one line spent over its ceiling, in one timeline.

    A span rather than a list of days because that is the shape of the sentence a reader wants —
    "Customer Support was over its ceiling from day 3 to day 12" — and because it is what U21's
    proposals have to cite. `peak_at_seq` addresses the worst day inside it, so the headline
    figure and the span both resolve to events rather than only the span's opening.
    """

    run_id: str
    director: str
    line: str
    from_day: int
    to_day: int
    opened_at_seq: int
    peak_permille: int
    peak_day: int
    peak_at_seq: int
    basis: str = AUTHORED

    @property
    def days(self) -> int:
        return self.to_day - self.from_day + 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "director": self.director,
            "line": self.line,
            "from_day": self.from_day,
            "to_day": self.to_day,
            "days": self.days,
            "opened_at_seq": self.opened_at_seq,
            "peak_permille": self.peak_permille,
            "peak_day": self.peak_day,
            "peak_at_seq": self.peak_at_seq,
            "basis": self.basis,
        }


@dataclass(frozen=True, slots=True)
class LineOverload:
    """What the whole tree says about one reporting line.

    **Days are not summed across timelines, and that is the trap this shape exists to avoid.**
    Timelines share a prefix — a child's log *is* its parent's up to the divergence — so adding
    a parent's overloaded days to its children's counts the same days two and three times and
    reports a company far more overloaded than it was. What is safe to state across a tree is
    how many of its timelines the line was over ceiling in, where the earliest and the worst of
    those readings sit, and whether *every* timeline had it. The per-timeline spans carry the
    rest, each under the run it belongs to.

    `everywhere` is the figure U21 will want: a line over its ceiling in every timeline was never
    fixed by any decision the CEO took, which is a different problem from one that one option
    caused.
    """

    director: str
    line: str
    director_name: str
    #: How many timelines were folded, and in how many of them this line went over.
    timelines: int
    timelines_over: int
    #: The earliest and the highest readings anywhere in the tree, each addressed.
    first_over: LoadReading | None = None
    peak: LoadReading | None = None
    basis: str = AUTHORED

    @property
    def everywhere(self) -> bool:
        return self.timelines > 0 and self.timelines_over == self.timelines

    def to_dict(self) -> dict[str, Any]:
        return {
            "director": self.director,
            "line": self.line,
            "director_name": self.director_name,
            "timelines": self.timelines,
            "timelines_over": self.timelines_over,
            "everywhere": self.everywhere,
            "first_over": None if self.first_over is None else self.first_over.to_dict(),
            "peak": None if self.peak is None else self.peak.to_dict(),
            "basis": self.basis,
        }


def _lines_of(company: scenarios.Scenario) -> dict[str, str]:
    """Director id -> the reporting line they head, in the scenario's declaration order."""
    return {department.director: department.id for department in company.departments}


@dataclass(slots=True)
class Walked:
    """What one pass over a timeline's days produced."""

    readings: list[LoadReading] = field(default_factory=list)
    #: Day boundaries whose fold did not reproduce the hash the kernel wrote there, each
    #: localised to the subsystems that differ.
    diverged: list[dict[str, Any]] = field(default_factory=list)
    #: The company this timeline is a run of, taken from the fold rather than reloaded. The fold
    #: resolves it through `scenario.load_recorded` and passes R7's guard doing so, so a file
    #: edited since the run refuses here exactly as it refuses everywhere else.
    company: scenarios.Scenario | None = None
    #: What a day cost at the last boundary this timeline reached, which is what a proposal's
    #: payback is computed against. The *last* one, because a prescription is forward-looking:
    #: it says what automating would give back from here, so the terms it divides are the ones
    #: the company is standing on. Captured during the walk rather than by a second fold, and
    #: kept as terms rather than as a `State` — sixteen folded states held for the life of a
    #: report is the cost `walk_days` exists to avoid.
    economy: prescribing.Economy | None = None


def walk(run_id: str, timeline: reporting.TimelineLog) -> Walked:
    """Every line's load at every day boundary this timeline reached, in one pass.

    The divergence list is `simcore.verify`'s checkpoint comparison, done as the walk goes.
    `verify` itself folds from zero at every checkpoint, which is O(days squared) — 2617 ms for
    a single 30-day timeline against 254 ms for one walk of it, and a sixteen-timeline lineage is
    the difference between minutes and seconds. The comparison is the same one: the hash recorded
    at the boundary against the hash of the state folded to it, with `compare_checkpoint` called
    for the localisation exactly when a day fails. Sequence density, `verify`'s other half, is
    answered separately by `sequence_gaps` — it is O(n) and needs no fold at all.
    """
    walked = Walked()
    lines: dict[str, str] = {}
    through_day = simtime.day_of(timeline.reached_tick)

    for folded in reporting.walk_days(timeline.events, through_day=through_day):
        if walked.company is None:
            walked.company = folded.state.scenario
            lines = _lines_of(folded.state.scenario)

        if not folded.agrees:
            walked.diverged.append(
                {
                    "day": folded.day,
                    "at_tick": folded.at_tick,
                    "at_seq": folded.at_seq,
                    "subsystems": verifier.compare_checkpoint(
                        folded.recorded_subsystems, folded.state
                    ),
                }
            )

        if folded.at_seq == 0:
            # No event at this boundary to cite. See `LoadReading`. A payback is a claim like
            # any other, so it is measured only where the log can address the measurement.
            continue

        walked.economy = prescribing.economy_at(
            run_id, folded.day, folded.at_tick, folded.at_seq, folded.state
        )

        for director, department in folded.state.capacity.items():
            walked.readings.append(
                LoadReading(
                    run_id=run_id,
                    director=director,
                    line=lines.get(director, director),
                    day=folded.day,
                    at_tick=folded.at_tick,
                    at_seq=folded.at_seq,
                    load_permille=department.load_permille,
                    over=department.load_permille > cap.LOAD_CEILING,
                )
            )
    return walked


def spans_of(readings: list[LoadReading]) -> list[OverloadSpan]:
    """Consecutive over-ceiling days, per line, in the order the lines were declared.

    A gap of one day closes a span rather than being smoothed over. Load is sampled at day
    boundaries because that is where the simulation charges for it, so a day back under the
    ceiling is a day the company was not paying — and a span that spanned it would claim
    otherwise.
    """
    spans: list[OverloadSpan] = []
    by_director: dict[str, list[LoadReading]] = {}
    for reading in readings:
        by_director.setdefault(reading.director, []).append(reading)

    for director, series in by_director.items():
        run: list[LoadReading] = []
        for reading in sorted(series, key=lambda entry: entry.day):
            if reading.over and (not run or reading.day == run[-1].day + 1):
                run.append(reading)
                continue
            if run:
                spans.append(_span(director, run))
            run = [reading] if reading.over else []
        if run:
            spans.append(_span(director, run))
    return spans


def _span(director: str, run: list[LoadReading]) -> OverloadSpan:
    peak = max(run, key=lambda reading: (reading.load_permille, -reading.day))
    return OverloadSpan(
        run_id=run[0].run_id,
        director=director,
        line=run[0].line,
        from_day=run[0].day,
        to_day=run[-1].day,
        opened_at_seq=run[0].at_seq,
        peak_permille=peak.load_permille,
        peak_day=peak.day,
        peak_at_seq=peak.at_seq,
    )


# =========================================================================
# One timeline's place in the Universe
# =========================================================================


@dataclass(slots=True)
class TimelineReport:
    """One timeline: its own report, its overload, and what separated it from its parent."""

    run_id: str
    parent_run_id: str
    forked_at_seq: int
    reached_tick: int
    current_day: int
    #: The decision this timeline reconsidered, as the tree read it off the two logs. Empty for
    #: the lineage root, which was not separated from anything.
    separation: dict[str, Any] = field(default_factory=dict)
    report: reporting.Report | None = None
    spans: list[OverloadSpan] = field(default_factory=list)
    readings: list[LoadReading] = field(default_factory=list)
    #: The terms at this timeline's last cited day boundary. Read by `proposals.propose` and
    #: deliberately absent from `to_dict`: it is an input to a payback rather than a figure the
    #: report states, and every number it produces is on the proposal, addressed.
    economy: prescribing.Economy | None = None
    #: Sequences the log should hold and does not, and day boundaries whose fold did not
    #: reproduce the hash the kernel wrote there. Both empty is what a healthy timeline looks
    #: like; either non-empty is stated beside the figures rather than instead of them, because
    #: a reader deciding whether to trust a number wants both.
    missing_seqs: list[int] = field(default_factory=list)
    diverged_days: list[dict[str, Any]] = field(default_factory=list)
    #: Set when this timeline could not be folded at all. Its figures are absent; the rest of
    #: the Universe is not.
    refusal: str = ""

    @property
    def trustworthy(self) -> bool:
        return not self.refusal and not self.missing_seqs and not self.diverged_days

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "parent_run_id": self.parent_run_id,
            "forked_at_seq": self.forked_at_seq,
            "reached_tick": self.reached_tick,
            "current_day": self.current_day,
            "separation": self.separation,
            "report": None if self.report is None else self.report.to_dict(),
            "spans": [span.to_dict() for span in self.spans],
            "load": [reading.to_dict() for reading in self.readings],
            "missing_seqs": list(self.missing_seqs),
            "diverged_days": list(self.diverged_days),
            "trustworthy": self.trustworthy,
            "refusal": self.refusal,
        }


@dataclass(slots=True)
class Universe:
    """Every timeline one Genesis produced, and what the tree of them says (M53)."""

    root_run_id: str
    #: The timeline the caller asked about. On the payload so a surface can mark where the
    #: player is standing; the report's *identity* is the root, and never this.
    asked_about: str
    rules_ver: str
    state_shape_ver: int
    company: dict[str, Any] = field(default_factory=dict)
    #: M59, said rather than flagged.
    invented_company: str = ""
    timelines: list[TimelineReport] = field(default_factory=list)
    #: M56, across the tree. Per line, never summed over timelines — see `LineOverload`.
    overload: list[LineOverload] = field(default_factory=list)
    #: M57. Authored candidates this Universe's own fold is the argument for, in the order the
    #: scenario declares them. Empty is a company whose lines nobody overloaded for long enough,
    #: which is a report with nothing to prescribe rather than one that failed to prescribe.
    proposals: list[prescribing.Proposal] = field(default_factory=list)
    #: Every figure in the report, each addressed by run and sequence together (M55).
    claims: list[reporting.Claim] = field(default_factory=list)

    @property
    def load_scale(self) -> dict[str, Any]:
        """The domain every load reading is against, marked like every other figure.

        A ceiling is a constant somebody chose, which is the definition of authored tuning — so
        it carries the marking rather than sitting above the figures as an unattributed fact. It
        comes from `simcore.capacity` rather than being written down here, for the reason the
        genesis payload gives for carrying it to the HUD: a second copy of the ramp's domain is
        the drift where two surfaces start colouring the same load differently.
        """
        return {
            "scale_permille": cap.LOAD_SCALE,
            "ceiling_permille": cap.LOAD_CEILING,
            "basis": AUTHORED,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "root_run_id": self.root_run_id,
            "asked_about": self.asked_about,
            "rules_ver": self.rules_ver,
            "state_shape_ver": self.state_shape_ver,
            "every_number_is": AUTHORED,
            "invented_company": self.invented_company,
            "company": self.company,
            "load_scale": self.load_scale,
            "timelines": [timeline.to_dict() for timeline in self.timelines],
            "overload": [line.to_dict() for line in self.overload],
            "proposals": [proposal.to_dict() for proposal in self.proposals],
            "prescription_rule": self.prescription_rule,
            "claims": [claim.to_dict() for claim in self.claims],
        }

    @property
    def prescription_rule(self) -> dict[str, Any]:
        """When this report is willing to propose something, said rather than implied (M57).

        On the payload for the same reason `load_scale` is: a reader who disagrees with the
        threshold can see it, count the spans themselves and decide the report is wrong — which
        is a different and much better position than wondering where the proposals came from.
        """
        return {
            "min_overload_days": prescribing.MIN_OVERLOAD_DAYS,
            "says": (
                "a candidate is proposed only where this Universe's own fold found its line "
                f"over the ceiling for {prescribing.MIN_OVERLOAD_DAYS} consecutive sim-days or "
                "more, in at least one timeline"
            ),
            "basis": AUTHORED,
        }

    def timeline_for(self, run_id: str) -> TimelineReport | None:
        return next((entry for entry in self.timelines if entry.run_id == run_id), None)

    def proposal_for(self, proposal_id: str) -> prescribing.Proposal | None:
        return next((entry for entry in self.proposals if entry.id == proposal_id), None)


def build(
    tree: lineage.Tree, logs: Mapping[str, reporting.TimelineLog]
) -> Universe:
    """Fold a whole lineage into one report. Reads only; never writes.

    The node order is the tree's — creation, then run id — which is stable under insertion, so a
    new fork appends a section rather than reordering the ones already written. A node the caller
    could not read a log for is skipped rather than reported empty: there is a difference between
    a timeline whose figures are missing and a timeline that is not in this artifact at all, and
    only the store knows which happened.
    """
    universe = Universe(
        root_run_id=tree.root_run_id,
        asked_about=tree.asked_about,
        rules_ver="",
        state_shape_ver=0,
    )

    company: scenarios.Scenario | None = None

    for node in tree.nodes:
        timeline = logs.get(node.run_id)
        if timeline is None or not timeline.events:
            continue

        entry = TimelineReport(
            run_id=node.run_id,
            parent_run_id=node.parent_run_id,
            forked_at_seq=node.forked_at_seq,
            reached_tick=timeline.reached_tick,
            current_day=timeline.current_day,
            separation=_separation_of(node),
        )
        universe.timelines.append(entry)

        # Density first, and outside the try: it is a property of the sequences themselves and
        # answerable on a log that will not fold, which is exactly when somebody wants it.
        entry.missing_seqs = verifier.sequence_gaps(timeline.events)

        # Two folds, and two separate refusals. The run report and the day walk can fail
        # independently — the first reads the whole log to its head, the second stops at every
        # boundary — and collapsing them onto one message would leave a reader unable to tell a
        # timeline with no figures at all from one whose overload section is the part that is
        # missing.
        try:
            entry.report = reporting.build(
                node.run_id, timeline.events, through_tick=timeline.reached_tick
            )
        except Exception as exc:  # noqa: BLE001 - one timeline's failure, stated where it happened
            entry.refusal = f"this timeline could not be folded: {type(exc).__name__}: {exc}"
            continue

        try:
            walked = walk(node.run_id, timeline)
        except Exception as exc:  # noqa: BLE001 - the overload section alone is lost
            entry.refusal = (
                f"this timeline's days could not be walked: {type(exc).__name__}: {exc}"
            )
            continue

        entry.readings = walked.readings
        entry.diverged_days = walked.diverged
        entry.spans = spans_of(walked.readings)
        entry.economy = walked.economy

        if company is None and walked.company is not None:
            company = walked.company
            universe.rules_ver = entry.report.rules_ver
            universe.state_shape_ver = entry.report.state_shape_ver
            universe.company = {
                "id": company.scenario_id,
                "title": company.title,
                "summary": company.summary,
                "content_hash": company.content_hash,
            }
            universe.invented_company = invented_company(company)

    universe.overload = _overload(universe.timelines, company)
    universe.proposals = prescribing.propose(company, universe.overload, universe.timelines)
    universe.claims = _claims(universe.timelines) + prescribing.claims_of(universe.proposals)
    return universe


def _separation_of(node: lineage.Node) -> dict[str, Any]:
    """What this timeline reconsidered, from the divergence the tree already read.

    Taken from the node rather than re-derived: `lineage._divergences` reads both sides of every
    fork in one batched select, and a second reading here would be a second answer to a question
    the tree has already answered for the surface the player navigates by.
    """
    if not node.parent_run_id or node.item == "":
        return {}
    return {
        "from_run_id": node.parent_run_id,
        "item": node.item,
        "cp_index": node.cp_index,
        "at_seq": node.forked_at_seq + 1,
        "option_index": node.option_index,
        "choice": node.choice,
        "parent_option_index": node.parent_option_index,
        "parent_choice": node.parent_choice,
    }


def _overload(
    timelines: list[TimelineReport], company: scenarios.Scenario | None
) -> list[LineOverload]:
    """M56 across the tree: which lines, where, and whether any timeline escaped it."""
    if company is None:
        return []

    folded = [timeline for timeline in timelines if not timeline.refusal]
    lines = _lines_of(company)
    out: list[LineOverload] = []

    for director, line in lines.items():
        person = company.people_by_id.get(director)
        over_in = [
            timeline
            for timeline in folded
            if any(span.director == director for span in timeline.spans)
        ]
        readings = [
            reading
            for timeline in folded
            for reading in timeline.readings
            if reading.director == director and reading.over
        ]
        out.append(
            LineOverload(
                director=director,
                line=line,
                director_name=person.name if person else director,
                timelines=len(folded),
                timelines_over=len(over_in),
                first_over=min(readings, key=lambda r: (r.day, r.run_id), default=None),
                peak=max(readings, key=lambda r: (r.load_permille, -r.day), default=None),
            )
        )
    return out


def _claims(timelines: list[TimelineReport]) -> list[reporting.Claim]:
    """Every figure in the Universe, each naming its run and its sequence (M55).

    The union of the timelines' own claims plus one per line per timeline for the overload, and
    deliberately nothing tree-wide. A count over a tree — how many timelines, how many decisions
    across all of them — is produced by no single event, so presenting one as a claim would mean
    either citing an event that did not produce it or carrying a claim with no citation. The tree
    structure is on the payload as structure; the claims stay resolvable.
    """
    claims: list[reporting.Claim] = []
    for timeline in timelines:
        if timeline.report is not None:
            claims.extend(timeline.report.claims)
        for span in _first_span_per_line(timeline.spans):
            days = sum(
                other.days
                for other in timeline.spans
                if other.director == span.director
            )
            claims.append(
                reporting.Claim(
                    run_id=timeline.run_id,
                    label=f"days over ceiling: {span.line}",
                    value=days,
                    at_seq=span.opened_at_seq,
                    at_tick=simtime.tick_of_day_start(span.from_day),
                )
            )
    return claims


def _first_span_per_line(spans: list[OverloadSpan]) -> list[OverloadSpan]:
    """The earliest span of each line, which is where that line's claim is cited."""
    first: dict[str, OverloadSpan] = {}
    for span in spans:
        held = first.get(span.director)
        if held is None or span.from_day < held.from_day:
            first[span.director] = span
    return list(first.values())
