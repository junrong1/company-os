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

import pytest
from fastapi.testclient import TestClient

from conftest import SEED, Recorder, build_lineage, kernel_on, played
from contracts import canonical
from contracts.envelope import Envelope, EventKind
from logschema import lineage as lin
from report import fold as reporting
from report import universe as universes
from simcore import capacity as cap
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
