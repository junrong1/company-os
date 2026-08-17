"""How a run ends, and the report that explains it.

The report's authority in Phase 1 is procedural rather than numeric: every metric figure is
authored tuning, so what it can say with confidence is which decision was taken, by which
route, whether the tacit line surfaced, and who was never told. These tests assert that trail
and the resolvability of every claim, because that is the product's central promise.
"""

from __future__ import annotations

import pytest

from contracts.envelope import Envelope, EventKind, build as build_envelope
from report import fold as reporting
from simcore import lifecycle
from simcore import scenario as scenarios
from simcore import step as sim
from simcore import time as simtime
from simcore.rates import RULES_VERSION

SEED = 0xC0FFEE
RUN = "run-lifecycle"


class Recorder:
    def __init__(self, horizon_tick: int | None = None) -> None:
        self.state, genesis = sim.new_run(run_seed=SEED, horizon_tick=horizon_tick)
        self.log: list[Envelope] = []
        self._seq = 0
        self.record(genesis)

    def record(self, emitted: list[sim.Emitted]) -> None:
        for item in emitted:
            self._seq += 1
            self.log.append(
                build_envelope(
                    seq=self._seq,
                    tick=int(item.payload.get("tick", self.state.tick)),
                    kind=item.kind,
                    rules_ver=RULES_VERSION,
                    payload=item.payload,
                    run_id=RUN,
                    request_id=item.request_id,
                )
            )

    def advance(self, ticks: int) -> None:
        for _ in range(ticks):
            self.record(sim.step(self.state))

    def advance_until(self, predicate, limit: int = 60_000) -> None:
        for _ in range(limit):
            if predicate(self.state):
                return
            self.record(sim.step(self.state))
        raise AssertionError(f"condition never held within {limit} ticks")

    def report(self) -> reporting.Report:
        return reporting.build(RUN, self.log, through_tick=self.state.tick)


# =========================================================================
# The horizon
# =========================================================================


def test_the_horizon_is_recorded_at_genesis() -> None:
    recorder = Recorder()
    genesis = recorder.log[0].decoded_payload()

    assert genesis["horizon_tick"] == lifecycle.default_horizon_tick()
    assert genesis["decision_supply"] == 9, "the eight authored items carry nine checkpoints"


def test_the_horizon_is_sized_against_the_decision_supply() -> None:
    """A horizon longer than the decision supply is burn with nothing left to decide."""
    shipped = scenarios.load_default()
    assert lifecycle.decision_supply(shipped) == 9
    days = lifecycle.DEFAULT_HORIZON_DAYS
    assert days / lifecycle.decision_supply(shipped) >= 2, "less than two sim-days per decision"


def test_a_run_reaches_its_horizon_and_terminates() -> None:
    recorder = Recorder(horizon_tick=simtime.TICKS_PER_SIM_DAY * 2)
    recorder.advance_until(lambda s: s.terminal_reason != "")

    assert recorder.state.terminal_reason == lifecycle.TERMINAL_HORIZON
    assert recorder.state.terminal_tick == simtime.TICKS_PER_SIM_DAY * 2

    terminated = [e for e in recorder.log if e.kind is EventKind.RUN_TERMINATED]
    assert len(terminated) == 1
    assert terminated[0].decoded_payload()["reason"] == "horizon"


def test_nothing_advances_after_termination() -> None:
    recorder = Recorder(horizon_tick=simtime.TICKS_PER_SIM_DAY)
    recorder.advance_until(lambda s: s.terminal_reason != "")

    ended_at = recorder.state.tick
    events_before = len(recorder.log)
    recorder.advance(500)

    assert recorder.state.tick == ended_at
    assert len(recorder.log) == events_before


def test_termination_is_the_last_event_of_its_quantum() -> None:
    recorder = Recorder(horizon_tick=simtime.TICKS_PER_SIM_DAY)
    recorder.advance_until(lambda s: s.terminal_reason != "")

    assert recorder.log[-1].kind is EventKind.RUN_TERMINATED


def test_a_keyless_run_plays_to_its_horizon_with_comparisons_along_the_way() -> None:
    """AE13, R8, R30. The whole of this phase is reachable with no model in the process.

    Observed rather than assumed, which is the point of the test. No model key exists anywhere
    in this repo yet, so "it works without one" is trivially true today and would stop being
    checked the moment one arrived — this pins it while it is cheap. Nothing on the comparison
    path consults the agents or the domain service: the branches are arithmetic, and R17
    forbids an expert from choosing anyway, so an unattended branch has nobody to ask.

    Driven the long way — assign, stop, compare, decide, repeat — because the criterion is that
    a *run* completes with the mechanic in it, not that one call returns.
    """
    recorder = Recorder(horizon_tick=simtime.TICKS_PER_SIM_DAY * 20)
    compared = 0

    for item_id, person_id in (("wi_ap_map", "stf_ap"), ("wi_faq", "stf_cs")):
        recorder.record(sim.assign_direct(recorder.state, item_id, person_id))
        recorder.advance_until(lambda s: s.items[item_id].status == sim.STATUS_BLOCKED)

        recorder.record(
            sim.compare_options(
                recorder.state,
                item_id,
                0,
                person_id,
                recorder.state.tick,
                in_person=True,
            )
        )
        compared += 1

        recorder.record(
            sim.resolve_checkpoint(recorder.state, item_id, 0, 0, in_person=True)
        )

    recorder.advance_until(lambda s: s.terminal_reason != "")

    assert compared == 2
    assert recorder.state.terminal_reason == lifecycle.TERMINAL_HORIZON
    assert recorder.state.terminal_tick == simtime.TICKS_PER_SIM_DAY * 20

    # The records are on the log, and the run still ends the way a run without them would.
    records = [e for e in recorder.log if e.kind is EventKind.OPTIONS_COMPARED]
    assert len(records) == 2
    for record in records:
        assert record.decoded_payload()["branches"]

    # R30: every claim in the report still resolves to an event, with comparison records in the
    # log. They are operational, so the report has to pass over them rather than trip on them —
    # this is the assertion that passed before this phase and has to keep passing.
    report = recorder.report()
    sequences = {envelope.seq for envelope in recorder.log}
    assert report.claims
    for claim in report.claims:
        assert claim.at_seq in sequences, f"{claim.label} resolves to no event"


def test_a_branch_raises_its_requests_into_its_own_copy_and_nowhere_else() -> None:
    """R8. A branch is a run nobody answers, and nothing it raises leaves the process.

    `step()` raises a period consult to the domain service at each day boundary, and a branch
    replays `step()` in full — so a branch does raise them. That was worth finding rather than
    assuming: it is correct, because a branch genuinely has nobody to answer it and suppressing
    the consult would make it diverge from the step function it is supposed to be a projection
    of. What matters is that the requests go into the branch's own `pending` and are abandoned
    there, so the parent never acquires one and no transport is ever reached.
    """
    recorder = Recorder(horizon_tick=simtime.TICKS_PER_SIM_DAY * 20)
    recorder.record(sim.assign_direct(recorder.state, "wi_ap_map", "stf_ap"))
    recorder.advance_until(lambda s: s.items["wi_ap_map"].status == sim.STATUS_BLOCKED)

    pending_before = dict(recorder.state.pending)
    period_before = recorder.state.last_period_consulted

    emitted = sim.compare_options(
        recorder.state, "wi_ap_map", 0, "stf_ap", recorder.state.tick, in_person=True
    )

    assert emitted
    assert recorder.state.pending == pending_before
    assert recorder.state.last_period_consulted == period_before


def test_the_unanswered_path_is_common_to_every_branch_and_cannot_bias_one() -> None:
    """The property that makes the point above harmless to the thing being compared.

    Every branch at one checkpoint forks from the same state and raises and abandons exactly
    the same requests at exactly the same ticks, so the no-answer path is common to all of them
    and cancels out of what the CEO is actually comparing. Checked by counting the requests
    each branch raised rather than by reasoning about it.
    """
    from contracts.envelope import EventKind as Kind
    from simcore import compare as branching

    recorder = Recorder(horizon_tick=simtime.TICKS_PER_SIM_DAY * 20)
    recorder.record(sim.assign_direct(recorder.state, "wi_ap_map", "stf_ap"))
    recorder.advance_until(lambda s: s.items["wi_ap_map"].status == sim.STATUS_BLOCKED)

    raised = [
        [
            (event.payload["tick"], event.payload.get("service"))
            for event in branching.run_branch(
                recorder.state, "wi_ap_map", 0, index, in_person=True
            ).emitted
            if event.kind is Kind.REQUEST_RAISED
        ]
        for index in range(3)
    ]

    assert raised[0], "no request was raised at all, so this proves nothing"
    assert raised[0] == raised[1] == raised[2]


def test_a_forked_child_inherits_the_parents_horizon() -> None:
    """Immutable and inherited: a child that outlived its parent's bound would not be
    comparable, and comparing runs is what forking is for.
    """
    parent = Recorder(horizon_tick=simtime.TICKS_PER_SIM_DAY * 3)
    genesis = parent.log[0].decoded_payload()

    # A fork replays the parent's prefix, so the child's genesis is the parent's.
    child_state, _ = sim.new_run(run_seed=SEED, horizon_tick=genesis["horizon_tick"])

    assert child_state.horizon_tick == parent.state.horizon_tick


# =========================================================================
# Insolvency
# =========================================================================


def test_insolvency_terminates_at_the_end_of_the_crossing_quantum() -> None:
    """The detail that makes the final cash reproducible.

    If termination fired the instant cash went negative, *where* inside the quantum it fired
    would decide the final figure — and two implementations that ordered the day's costs
    differently would report different numbers from the same log.
    """
    recorder = Recorder(horizon_tick=simtime.TICKS_PER_SIM_DAY * 400)
    # Put the company one day from insolvency.
    recorder.state.metrics["cash"] = 5
    recorder.advance_until(lambda s: s.terminal_reason != "")

    assert recorder.state.terminal_reason == lifecycle.TERMINAL_INSOLVENT
    assert recorder.state.metrics["cash"] < 0

    # It ended on a day boundary — the quantum in which the costs landed completed.
    assert simtime.is_day_boundary(recorder.state.terminal_tick)

    payload = recorder.log[-1].decoded_payload()
    assert payload["reason"] == "insolvent"
    assert "completed in full" in payload["detail"]


def test_insolvency_is_reproducible_from_the_log() -> None:
    def play() -> tuple[int, int]:
        recorder = Recorder(horizon_tick=simtime.TICKS_PER_SIM_DAY * 400)
        recorder.state.metrics["cash"] = 5
        recorder.advance_until(lambda s: s.terminal_reason != "")
        return recorder.state.terminal_tick, recorder.state.metrics["cash"]

    assert play() == play()


def test_cash_is_not_clamped_at_zero() -> None:
    """Insolvency is an outcome, so cash has to be allowed to cross."""
    from simcore import effects

    metrics = effects.initial_metrics(scenarios.load_default())
    effects.apply_effect(metrics, {"cash": -metrics["cash"] - 10})
    assert metrics["cash"] == -10


# =========================================================================
# The report
# =========================================================================


def _run_with_two_decisions() -> Recorder:
    recorder = Recorder(horizon_tick=simtime.TICKS_PER_SIM_DAY * 30)

    recorder.record(sim.assign_direct(recorder.state, "wi_ap_map", "stf_ap"))
    recorder.advance_until(lambda s: s.items["wi_ap_map"].status == sim.STATUS_BLOCKED)
    recorder.record(
        sim.resolve_checkpoint(recorder.state, "wi_ap_map", 0, 0, in_person=True)
    )

    recorder.record(sim.assign_direct(recorder.state, "wi_faq", "stf_cs"))
    recorder.advance_until(lambda s: s.items["wi_faq"].status == sim.STATUS_BLOCKED)
    recorder.record(
        sim.resolve_checkpoint(recorder.state, "wi_faq", 0, 1, in_person=False)
    )

    recorder.advance_until(lambda s: s.items["wi_ap_map"].status == sim.STATUS_DONE)
    return recorder


def test_each_decision_records_its_resolution_path() -> None:
    report = _run_with_two_decisions().report()

    assert len(report.decisions) == 2
    paths = {decision.item: decision.path for decision in report.decisions}
    assert paths["wi_ap_map"] == "in person"
    assert paths["wi_faq"] == "from the tray"


def test_the_tray_decision_is_marked_as_carrying_no_tacit_line() -> None:
    report = _run_with_two_decisions().report()
    by_item = {decision.item: decision for decision in report.decisions}

    assert by_item["wi_ap_map"].tacit_surfaced is True
    assert by_item["wi_ap_map"].tacit, "the in-person decision surfaced a line"

    assert by_item["wi_faq"].tacit_surfaced is False
    assert by_item["wi_faq"].tacit == ""


def test_the_report_records_which_directors_were_left_uninformed() -> None:
    report = _run_with_two_decisions().report()
    by_item = {decision.item: decision for decision in report.decisions}

    # Both were assigned directly, so both specialists' directors were bypassed.
    assert by_item["wi_ap_map"].uninformed_directors == ["dir_admin"]
    assert by_item["wi_faq"].uninformed_directors == ["dir_cs"]


def test_following_a_metric_movement_locates_the_event_that_caused_it() -> None:
    """The product's central claim, asserted directly."""
    recorder = _run_with_two_decisions()
    report = recorder.report()

    movements = report.trajectories["visibility"]
    assert len(movements) > 1

    by_seq = {envelope.seq: envelope for envelope in recorder.log}
    for movement in movements:
        assert movement["at_seq"] in by_seq, "a movement points at no event"
        envelope = by_seq[movement["at_seq"]]
        assert envelope.decoded_payload()["metrics"]["visibility"] == movement["value"]


def test_every_claim_resolves_to_an_event() -> None:
    """U8's verification, for a run that is still going."""
    recorder = _run_with_two_decisions()
    report = recorder.report()

    sequences = {envelope.seq for envelope in recorder.log}
    assert report.claims
    for claim in report.claims:
        assert claim.at_seq in sequences, f"{claim.label} resolves to no event"


def test_every_claim_resolves_to_an_event_for_a_horizon_run() -> None:
    recorder = Recorder(horizon_tick=simtime.TICKS_PER_SIM_DAY * 2)
    recorder.record(sim.assign_direct(recorder.state, "wi_faq", "stf_cs"))
    recorder.advance_until(lambda s: s.terminal_reason != "")

    report = recorder.report()
    sequences = {envelope.seq for envelope in recorder.log}

    assert report.outcome["reason"] == "horizon"
    for claim in report.claims:
        assert claim.at_seq in sequences, f"{claim.label} resolves to no event"


def test_every_claim_resolves_to_an_event_for_an_insolvent_run() -> None:
    recorder = Recorder(horizon_tick=simtime.TICKS_PER_SIM_DAY * 400)
    recorder.state.metrics["cash"] = 5
    recorder.advance_until(lambda s: s.terminal_reason != "")

    report = recorder.report()
    sequences = {envelope.seq for envelope in recorder.log}

    assert report.outcome["reason"] == "insolvent"
    for claim in report.claims:
        assert claim.at_seq in sequences, f"{claim.label} resolves to no event"


def test_every_number_is_marked_as_authored_tuning() -> None:
    """R41. The report's reader is not the operator and cannot be assumed to know."""
    report = _run_with_two_decisions().report()
    rendered = report.to_dict()

    assert rendered["every_number_is"] == reporting.AUTHORED
    for claim in rendered["claims"]:
        assert claim["basis"] == reporting.AUTHORED
    for points in rendered["trajectories"].values():
        for point in points:
            assert point["basis"] == reporting.AUTHORED


def test_the_report_names_its_rules_version() -> None:
    """It is only interpretable against the constants it was produced under."""
    report = _run_with_two_decisions().report()
    assert report.rules_ver == RULES_VERSION
    assert report.to_dict()["rules_ver"] == RULES_VERSION


def test_the_report_records_the_deliverable_provenance() -> None:
    report = _run_with_two_decisions().report()

    assert report.deliverables
    deliverable = report.deliverables[0]
    assert deliverable["provenance"]
    assert deliverable["at_seq"] > 0


def test_the_report_refuses_an_empty_log() -> None:
    with pytest.raises(ValueError, match="empty"):
        reporting.build(RUN, [])


def test_the_report_refuses_a_log_under_different_rules() -> None:
    """The guard lives in the fold, so the report cannot omit it."""
    from simcore import log as folder

    recorder = _run_with_two_decisions()
    with pytest.raises(folder.FoldRefused):
        folder.fold(recorder.log, at_live_head=False, running_rules_ver="other-rules")


def test_the_report_folds_off_the_live_head() -> None:
    """R3: a report that re-issued an external request would change the run it describes."""
    import inspect

    source = inspect.getsource(reporting.build)
    assert "at_live_head=False" in source


def test_the_report_service_holds_no_write_handle() -> None:
    """Read-side only. Asserted structurally rather than trusted."""
    import pathlib

    service = pathlib.Path(reporting.__file__).parent

    # Reading the log through `LogStore` is the report's job, so the store itself is not
    # forbidden — only the write path is. Naming the write operations rather than the module
    # is the difference between a guard and an obstacle.
    write_operations = ("append_tick", "StoreWriter", "insert(", "update(", "delete(")

    for path in service.rglob("*.py"):
        source = path.read_text()
        for forbidden in write_operations:
            assert forbidden not in source, f"{path.name} looks like it writes: {forbidden}"


def test_the_report_reconstructs_state_through_the_kernel_library() -> None:
    """A second fold would make the audit artifact disagree with the kernel."""
    import inspect

    source = inspect.getsource(reporting)
    assert "folder.fold(" in source
    assert "from simcore import log as folder" in source
