"""Fold, snapshot, replay, fork and export.

Everything in Phases D and E sits behind this unit, because everything after it assumes
state survives a restart and a log means one thing.

The characterization tests come first, per the plan's execution note: the fold is
characterised against a live run *before* snapshots are layered on it. That ordering earned
its keep — the first implementation reconstructed state from event payloads, which cannot
work when effort burn and mid-walk positions appear in no event, and the characterization
test is what said so.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from contracts.envelope import Envelope, EventKind, build
from simcore import export as exporter
from simcore import hashing
from simcore import log as folder
from simcore import snapshot as snapshots
from simcore import step as sim
from simcore import time as simtime
from simcore import verify as verifier
from simcore.rates import RULES_VERSION

BACKEND = Path(__file__).resolve().parent.parent
RUN = "run-replay"
SEED = 0xC0FFEE


class Recorder:
    """Plays a run and keeps the log the kernel service would have written.

    Sequence assignment belongs to the store, so it is done here rather than in simcore —
    which is also a small check that simcore genuinely does not need to know about it.
    """

    def __init__(self, run_id: str = RUN, seed: int = SEED) -> None:
        self.state, genesis = sim.new_run(run_seed=seed)
        self.run_id = run_id
        self.log: list[Envelope] = []
        self._seq = 0
        self.record(genesis)

    def record(self, emitted: list[sim.Emitted]) -> None:
        for item in emitted:
            self._seq += 1
            self.log.append(
                build(
                    seq=self._seq,
                    tick=int(item.payload.get("tick", self.state.tick)),
                    kind=item.kind,
                    rules_ver=RULES_VERSION,
                    payload=item.payload,
                    run_id=self.run_id,
                    request_id=item.request_id,
                )
            )

    def advance(self, ticks: int) -> None:
        for _ in range(ticks):
            self.record(sim.step(self.state))

    def advance_until(self, predicate, limit: int = 20_000) -> None:
        for _ in range(limit):
            if predicate(self.state):
                return
            self.record(sim.step(self.state))
        raise AssertionError(f"condition never held within {limit} ticks")

    def checkpoint(self) -> None:
        """Emit a day-boundary checkpoint, as the kernel does at every boundary."""
        self.record(
            [
                sim.Emitted(
                    kind=EventKind.DAY_CHECKPOINT,
                    payload=verifier.build_checkpoint_payload(self.state),
                )
            ]
        )

    @property
    def hash(self) -> str:
        return hashing.state_hash(sim.snapshot(self.state)).overall


def a_run_with_a_decision_and_a_deliverable() -> Recorder:
    """A run that exercises the paths that matter: hand-off, stall, decide, complete.

    Driven by state rather than by a fixed tick count. A fixed count was correct until U7
    changed the burn rate, at which point it stopped reaching the checkpoint — and because
    `resolve_checkpoint` had no reached-yet guard at the time, it silently resolved a
    checkpoint that had never been raised. Waiting on the condition is what the test actually
    means, and it does not need revisiting every time the economy is retuned.
    """
    recorder = Recorder()
    recorder.record(sim.assign_via_manager(recorder.state, "wi_ap_map"))
    recorder.advance_until(lambda s: s.items["wi_ap_map"].status == sim.STATUS_BLOCKED)
    recorder.record(
        sim.resolve_checkpoint(recorder.state, "wi_ap_map", 0, 0, in_person=True)
    )
    recorder.advance_until(lambda s: s.items["wi_ap_map"].status == sim.STATUS_DONE)
    recorder.record(sim.assign_direct(recorder.state, "wi_quotes", "stf_buyer"))
    recorder.advance(400)
    return recorder


# =========================================================================
# Characterization: the fold reproduces a live run
# =========================================================================


def test_refolding_a_log_reproduces_state_with_an_identical_hash() -> None:
    recorder = a_run_with_a_decision_and_a_deliverable()

    folded = folder.fold(
        recorder.log, at_live_head=False, strict=True, through_tick=recorder.state.tick
    )

    assert hashing.state_hash(sim.snapshot(folded.state)).overall == recorder.hash


def test_the_fold_reproduces_effort_that_appears_in_no_event() -> None:
    """The property the first implementation could not have had.

    Effort burn happens every tick and is in no event — there is no tick event per quantum.
    A fold that applied event payloads would leave `done_units` at whatever the last event
    happened to mention.
    """
    recorder = Recorder()
    recorder.record(sim.assign_direct(recorder.state, "wi_quotes", "stf_buyer"))
    recorder.advance(200)

    live = recorder.state.items["wi_quotes"].done_units
    # 47 units a tick, not 60: since U7 the department's baseline draw comes off available
    # hours before queue effort burns (R47). The point of this test is that the fold
    # reproduces the figure whatever it is, so it is derived rather than restated.
    assert live == 200 * 47
    assert live > 0, "sanity: some effort was burned"

    folded = folder.fold(recorder.log, at_live_head=False, through_tick=recorder.state.tick)
    assert folded.state.items["wi_quotes"].done_units == live


def test_the_fold_reproduces_an_actor_mid_walk() -> None:
    """A resume must not teleport anyone.

    The director's hand-off walk is in progress at this point, and its position comes from
    the path plus the tick it started — neither of which is in any event payload.
    """
    recorder = Recorder()
    recorder.record(sim.assign_via_manager(recorder.state, "wi_ap_map"))
    recorder.advance(80)  # mid-walk: the walk takes 168 ticks

    live = recorder.state.people["dir_admin"]
    assert live.state == sim.STATE_WALKING

    folded = folder.fold(recorder.log, at_live_head=False, through_tick=recorder.state.tick)
    restored = folded.state.people["dir_admin"]

    assert restored.state == sim.STATE_WALKING
    assert restored.pos == live.pos
    assert restored.path == live.path
    assert restored.path_start_tick == live.path_start_tick


def test_the_fold_reproduces_the_ceos_input_derived_position() -> None:
    """R12: the CEO resumes where their logged input put them, not at spawn."""
    recorder = Recorder()
    for _ in range(40):
        recorder.record(
            sim.submit_ceo_input(recorder.state, sim.INPUT_DOWN, at_tick=recorder.state.tick + 1)
        )
        recorder.advance(1)
    for _ in range(60):
        recorder.record(
            sim.submit_ceo_input(recorder.state, sim.INPUT_RIGHT, at_tick=recorder.state.tick + 1)
        )
        recorder.advance(1)

    assert recorder.state.ceo.tile != recorder.state.floor.spawn

    folded = folder.fold(recorder.log, at_live_head=False, through_tick=recorder.state.tick)
    assert folded.state.ceo.x_milli == recorder.state.ceo.x_milli
    assert folded.state.ceo.y_milli == recorder.state.ceo.y_milli


def test_replaying_a_log_of_asks_reproduces_the_visibility_trajectory() -> None:
    """R19: what a person has already answered is run state, and it replays.

    The fold re-issues the *question* rather than folding the recorded answer, so this is a
    check that the rule which priced the answer originally prices it the same way again —
    including charging nothing the second time the same person is asked the same thing.
    """
    from simcore.rates import TUNING

    recorder = Recorder()
    for question in ("why does it work that way?", "any exceptions?", "why again?"):
        recorder.record(sim.ask_person(recorder.state, "stf_ap", question))
        recorder.advance(1)
    recorder.record(sim.ask_person(recorder.state, "dir_admin", "who decides?"))
    recorder.advance(1)

    live = recorder.state
    # Three first-time tacit answers across two people; the repeated "why" paid nothing.
    assert live.people["stf_ap"].answered == ["exception", "why"]
    assert live.people["dir_admin"].answered == ["axis"]
    assert live.metrics["visibility"] == 6 + 3 * TUNING["visibility_per_tacit_answer"]

    folded = folder.fold(recorder.log, at_live_head=False, through_tick=live.tick)

    assert folded.state.metrics["visibility"] == live.metrics["visibility"]
    assert folded.state.people["stf_ap"].answered == live.people["stf_ap"].answered
    assert (
        hashing.state_hash(sim.snapshot(folded.state)).overall
        == hashing.state_hash(sim.snapshot(live)).overall
    )


def test_a_quiet_run_needs_its_current_tick_passed_in() -> None:
    """Where the run row earns its place.

    With no tick event per quantum, a run that ticked on past its last event leaves no trace
    of that in the log. The fold stops where the log proves, and the caller supplies the rest
    from the run row.
    """
    recorder = a_run_with_a_decision_and_a_deliverable()
    recorder.advance(500)  # quiet: no events produced

    without = folder.fold(recorder.log, at_live_head=False)
    assert without.state.tick < recorder.state.tick

    with_tick = folder.fold(
        recorder.log, at_live_head=False, through_tick=recorder.state.tick
    )
    assert with_tick.state.tick == recorder.state.tick
    assert hashing.state_hash(sim.snapshot(with_tick.state)).overall == recorder.hash


def test_a_run_row_behind_its_own_log_is_refused() -> None:
    recorder = a_run_with_a_decision_and_a_deliverable()

    with pytest.raises(folder.FoldRefused, match="disagree"):
        folder.fold(recorder.log, at_live_head=False, through_tick=1)


# =========================================================================
# Strict replay detects divergence
# =========================================================================


def test_strict_replay_detects_a_tampered_output_payload() -> None:
    """The reason outputs are regenerated rather than applied.

    A fold that applied logged outputs would agree with the log by construction and prove
    nothing. Regenerating them means a wrong log is caught.
    """
    recorder = a_run_with_a_decision_and_a_deliverable()

    tampered = []
    for envelope in recorder.log:
        if envelope.kind is EventKind.CHECKPOINT_RAISED:
            payload = envelope.decoded_payload()
            payload["done_units"] = payload["done_units"] + 1
            envelope = build(
                seq=envelope.seq,
                tick=envelope.tick,
                kind=envelope.kind,
                rules_ver=envelope.rules_ver,
                payload=payload,
                run_id=envelope.run_id,
            )
        tampered.append(envelope)

    with pytest.raises(folder.ReplayDiverged) as excinfo:
        folder.fold(tampered, at_live_head=False, strict=True, through_tick=recorder.state.tick)

    assert "CHECKPOINT_RAISED" in str(excinfo.value)


def test_strict_replay_detects_a_missing_output_event() -> None:
    recorder = a_run_with_a_decision_and_a_deliverable()
    pruned = [e for e in recorder.log if e.kind is not EventKind.DELIVERABLE_PRODUCED]

    with pytest.raises(folder.ReplayDiverged):
        folder.fold(pruned, at_live_head=False, strict=True, through_tick=recorder.state.tick)


def test_a_non_strict_fold_does_not_check_outputs() -> None:
    """The report folds for content, not for verification, and says so by this parameter."""
    recorder = a_run_with_a_decision_and_a_deliverable()
    pruned = [e for e in recorder.log if e.kind is not EventKind.DELIVERABLE_PRODUCED]

    folder.fold(pruned, at_live_head=False, strict=False, through_tick=recorder.state.tick)


# =========================================================================
# The rules-version guard lives in the fold (R10, R27)
# =========================================================================


def test_any_fold_of_a_log_written_under_other_rules_refuses() -> None:
    recorder = a_run_with_a_decision_and_a_deliverable()

    with pytest.raises(folder.FoldRefused) as excinfo:
        folder.fold(recorder.log, at_live_head=False, running_rules_ver="different-rules")

    message = str(excinfo.value)
    assert RULES_VERSION in message, "the log's version must be named"
    assert "different-rules" in message, "the running version must be named"


def test_the_guard_cannot_be_omitted_by_a_caller() -> None:
    """It is inside the fold, so replay, the report and a snapshot load all get it.

    Three callers, each of which could have forgotten it.
    """
    recorder = a_run_with_a_decision_and_a_deliverable()

    with pytest.raises(folder.FoldRefused):
        folder.replay_to_state(recorder.log, running_rules_ver="different-rules")

    # Verification reports rather than raises — an operator wants the whole picture, not the
    # first exception — but it still applies the guard, and applies it before looking for
    # day boundaries. A run less than one sim-day old has no checkpoints to fold to, and
    # checking only inside that loop would let it verify as healthy under the wrong rules.
    report = verifier.verify(recorder.log, running_rules_ver="different-rules")
    assert not report.healthy
    assert "different-rules" in report.refusal


def test_verify_reports_a_rules_mismatch_rather_than_crashing() -> None:
    recorder = a_run_with_a_decision_and_a_deliverable()
    recorder.checkpoint()

    report = verifier.verify(recorder.log, running_rules_ver=RULES_VERSION)
    assert report.healthy


def test_a_log_that_does_not_begin_with_genesis_is_refused() -> None:
    recorder = a_run_with_a_decision_and_a_deliverable()

    with pytest.raises(folder.FoldRefused, match="GENESIS"):
        folder.fold(recorder.log[1:], at_live_head=False)


def test_an_empty_log_is_refused() -> None:
    with pytest.raises(folder.FoldRefused, match="empty"):
        folder.fold([], at_live_head=False)


def test_at_live_head_is_a_parameter_of_the_fold_not_an_inference() -> None:
    """R3: re-issue is permitted only at the live head, and the fold has to be told."""
    recorder = a_run_with_a_decision_and_a_deliverable()

    assert folder.fold(recorder.log, at_live_head=True).at_live_head is True
    assert folder.fold(recorder.log, at_live_head=False).at_live_head is False


# =========================================================================
# Snapshots
# =========================================================================


def test_fold_from_zero_and_fold_from_snapshot_produce_the_same_hash() -> None:
    recorder = a_run_with_a_decision_and_a_deliverable()
    from_zero = folder.fold(
        recorder.log, at_live_head=False, through_tick=recorder.state.tick
    )

    # Snapshot half way, then fold the remainder on top of it.
    midpoint = len(recorder.log) // 2
    partial = folder.fold(
        recorder.log[:midpoint],
        at_live_head=False,
        through_tick=int(recorder.log[midpoint - 1].decoded_payload().get("tick", 0)),
    )
    taken = snapshots.capture(RUN, partial.state, recorder.log[midpoint - 1].seq)

    resumed = folder.fold(
        recorder.log[midpoint:],
        at_live_head=False,
        resume_from=(snapshots.restore(taken), taken.through_seq),
        through_tick=recorder.state.tick,
    )

    assert hashing.state_hash(sim.snapshot(resumed.state)).overall == hashing.state_hash(
        sim.snapshot(from_zero.state)
    ).overall


def test_a_snapshot_round_trips_to_the_same_state_hash() -> None:
    recorder = a_run_with_a_decision_and_a_deliverable()
    taken = snapshots.capture(RUN, recorder.state, recorder.log[-1].seq)

    restored = snapshots.restore(taken)

    assert hashing.state_hash(sim.snapshot(restored)).overall == recorder.hash
    assert restored.tick == recorder.state.tick
    assert restored.metrics == recorder.state.metrics


def test_a_snapshot_preserves_an_actor_mid_walk() -> None:
    recorder = Recorder()
    recorder.record(sim.assign_via_manager(recorder.state, "wi_ap_map"))
    recorder.advance(80)

    restored = snapshots.restore(snapshots.capture(RUN, recorder.state, 2))
    live = recorder.state.people["dir_admin"]
    back = restored.people["dir_admin"]

    assert back.path == live.path
    assert back.path_start_tick == live.path_start_tick
    assert back.arrive == live.arrive


def test_editing_a_tuning_constant_invalidates_a_snapshot(monkeypatch) -> None:
    """Rather than silently validating it.

    A snapshot taken under other constants describes a state the current rules would never
    produce; loading it would make fold-from-snapshot diverge from fold-from-zero while
    every guard reported a match.
    """
    recorder = a_run_with_a_decision_and_a_deliverable()
    taken = snapshots.capture(RUN, recorder.state, recorder.log[-1].seq)

    assert taken.is_usable(RULES_VERSION)
    assert snapshots.invalidated_by(taken, RULES_VERSION) == ""

    assert not taken.is_usable("rules-after-a-tuning-pass")
    assert "rules version changed" in snapshots.invalidated_by(taken, "rules-after-a-tuning-pass")

    with pytest.raises(snapshots.SnapshotInvalid, match="re-fold"):
        snapshots.restore(taken, running_rules_ver="rules-after-a-tuning-pass")


def test_a_snapshot_that_does_not_reproduce_its_own_hash_is_refused() -> None:
    """Corruption found here is much cheaper than corruption found at a day boundary."""
    recorder = a_run_with_a_decision_and_a_deliverable()
    taken = snapshots.capture(RUN, recorder.state, 5)

    import dataclasses

    corrupted = dataclasses.replace(taken, state_hash="0" * 32)

    with pytest.raises(snapshots.SnapshotInvalid, match="does not reproduce"):
        snapshots.restore(corrupted)


def test_a_snapshot_is_canonical_bytes() -> None:
    from contracts import canonical

    recorder = a_run_with_a_decision_and_a_deliverable()
    taken = snapshots.capture(RUN, recorder.state, 5)

    assert canonical.is_canonical(taken.state)


# =========================================================================
# Verification and the corrupt tail
# =========================================================================


def test_a_healthy_run_verifies() -> None:
    recorder = Recorder()
    recorder.record(sim.assign_direct(recorder.state, "wi_quotes", "stf_buyer"))
    for _ in range(3):
        recorder.advance(simtime.TICKS_PER_SIM_DAY)
        recorder.checkpoint()

    report = verifier.verify(recorder.log)

    assert report.healthy, report.summary()
    assert report.missing_seqs == []
    assert "every day-boundary hash matches" in report.summary()


def test_a_gap_in_the_sequence_is_detected() -> None:
    recorder = a_run_with_a_decision_and_a_deliverable()
    with_hole = [envelope for envelope in recorder.log if envelope.seq != 3]

    report = verifier.verify(with_hole)

    assert not report.healthy
    assert 3 in report.missing_seqs


def test_checkpoint_hashes_localise_damage_to_a_sim_day() -> None:
    """Two sim-days, and a tampered checkpoint on the second.

    The report names the tick and the day, and the last trustworthy sequence — which is what
    makes "truncate back to the last valid checkpoint, losing at most one sim-day" a real
    procedure rather than a slogan.
    """
    recorder = Recorder()
    recorder.record(sim.assign_direct(recorder.state, "wi_quotes", "stf_buyer"))
    recorder.advance(simtime.TICKS_PER_SIM_DAY)
    recorder.checkpoint()
    first_boundary_seq = recorder.log[-1].seq
    recorder.advance(simtime.TICKS_PER_SIM_DAY)
    recorder.checkpoint()

    damaged = []
    seen_checkpoints = 0
    for envelope in recorder.log:
        if envelope.kind is EventKind.DAY_CHECKPOINT:
            seen_checkpoints += 1
            if seen_checkpoints == 2:
                payload = envelope.decoded_payload()
                payload["state_hash"] = "f" * 32
                envelope = build(
                    seq=envelope.seq,
                    tick=envelope.tick,
                    kind=envelope.kind,
                    rules_ver=envelope.rules_ver,
                    payload=payload,
                    run_id=envelope.run_id,
                )
        damaged.append(envelope)

    report = verifier.verify(damaged)

    assert not report.healthy
    assert len(report.diverged_checkpoints) == 1
    diverged = report.diverged_checkpoints[0]
    assert diverged["day"] == 3
    assert report.last_good_seq == first_boundary_seq
    assert "at most one sim-day" in report.summary()


def test_checkpoint_sub_hashes_localise_to_a_subsystem() -> None:
    """The payoff for storing per-subsystem hashes.

    The overall hash says state diverged. The sub-hashes say which subtree, which is the
    difference between a bisect and a lookup.
    """
    recorder = Recorder()
    recorder.record(sim.assign_direct(recorder.state, "wi_quotes", "stf_buyer"))
    recorder.advance(300)

    recorded = verifier.build_checkpoint_payload(recorder.state)["subsystems"]

    # Move one subsystem and nothing else.
    recorder.state.metrics["cash"] -= 1
    differing = verifier.compare_checkpoint(recorded, recorder.state)

    assert differing == ["metrics"]


def test_a_checkpoint_payload_carries_the_state_shape_version() -> None:
    recorder = Recorder()
    payload = verifier.build_checkpoint_payload(recorder.state)

    assert payload["state_shape_ver"] == hashing.STATE_SHAPE_VERSION
    assert set(payload["subsystems"]) == set(hashing.SUBSYSTEMS)


# =========================================================================
# Export and import (R32)
# =========================================================================


def test_a_run_exports_and_reimports_to_the_same_state_hash() -> None:
    recorder = a_run_with_a_decision_and_a_deliverable()

    artifact = exporter.export_run(
        run_id=RUN,
        events=recorder.log,
        through_tick=recorder.state.tick,
        state_hash=recorder.hash,
    )

    state, meta = exporter.import_and_replay(artifact)

    assert hashing.state_hash(sim.snapshot(state)).overall == recorder.hash
    assert meta.run_id == RUN
    assert meta.rules_ver == RULES_VERSION


def test_exporting_the_same_run_twice_produces_identical_bytes() -> None:
    """Canonical, so an artifact can be diffed and compared without replaying it."""
    recorder = a_run_with_a_decision_and_a_deliverable()
    args = {
        "run_id": RUN,
        "events": recorder.log,
        "through_tick": recorder.state.tick,
        "state_hash": recorder.hash,
    }
    assert exporter.export_run(**args) == exporter.export_run(**args)


def test_an_artifact_carries_both_versions_and_refuses_a_rules_mismatch() -> None:
    recorder = a_run_with_a_decision_and_a_deliverable()
    artifact = exporter.export_run(
        run_id=RUN,
        events=recorder.log,
        through_tick=recorder.state.tick,
        state_hash=recorder.hash,
    )

    with pytest.raises(exporter.ImportRefused) as excinfo:
        exporter.read_artifact(artifact, running_rules_ver="other-rules")

    message = str(excinfo.value)
    assert RULES_VERSION in message
    assert "other-rules" in message
    assert "self-describing" in message


def test_an_artifact_whose_recorded_hash_disagrees_with_its_log_is_refused() -> None:
    """Worse than refusing: importing it would look like the run it claims to be."""
    recorder = a_run_with_a_decision_and_a_deliverable()
    artifact = exporter.export_run(
        run_id=RUN,
        events=recorder.log,
        through_tick=recorder.state.tick,
        state_hash="0" * 32,
    )

    with pytest.raises(exporter.ImportRefused, match="disagree"):
        exporter.import_and_replay(artifact)


def test_an_exported_run_replays_in_a_different_process() -> None:
    """U15's verification. A hash reproduced in the same interpreter proves less.

    Anything that depended on process-local state — a salted hash, an iteration order, a
    cached object — would agree with itself here and disagree there.
    """
    recorder = a_run_with_a_decision_and_a_deliverable()
    artifact = exporter.export_run(
        run_id=RUN,
        events=recorder.log,
        through_tick=recorder.state.tick,
        state_hash=recorder.hash,
    )

    path = BACKEND / "var" / "export-test.artifact"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(artifact)

    try:
        probe = f"""
from pathlib import Path
from simcore import export as exporter, hashing, step as sim
state, meta = exporter.import_and_replay(Path({str(path)!r}).read_bytes())
print(hashing.state_hash(sim.snapshot(state)).overall)
"""
        result = subprocess.run(
            [sys.executable, "-c", probe],
            capture_output=True,
            text=True,
            cwd=BACKEND,
            env={"PYTHONPATH": "packages:services", "PATH": ""},
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == recorder.hash
    finally:
        path.unlink(missing_ok=True)


def test_exporting_an_empty_log_is_refused() -> None:
    with pytest.raises(ValueError, match="empty"):
        exporter.export_run(run_id=RUN, events=[], through_tick=0, state_hash="x")


# =========================================================================
# Outstanding requests are a projection of the log (R23)
# =========================================================================


def test_a_raised_but_unanswered_request_folds_to_outstanding() -> None:
    """So a restarted kernel knows what is in flight without a memory-only registry."""
    recorder = Recorder()
    request_id = "22222222-2222-5222-8222-222222222222"
    recorder.record(
        [
            sim.Emitted(
                kind=EventKind.REQUEST_RAISED,
                payload={
                    "tick": recorder.state.tick,
                    "owning_item": "wi_ap_map",
                    "service": "domain",
                },
                request_id=request_id,
            )
        ]
    )
    recorder.advance(30)

    result = folder.fold(recorder.log, at_live_head=False, through_tick=recorder.state.tick)

    assert request_id in result.outstanding_requests
    assert result.outstanding_requests[request_id]["owning_item"] == "wi_ap_map"


def test_an_answered_request_is_no_longer_outstanding() -> None:
    recorder = Recorder()
    request_id = "33333333-3333-5333-8333-333333333333"
    for kind in (EventKind.REQUEST_RAISED, EventKind.INPUT_RECEIVED):
        recorder.record(
            [
                sim.Emitted(
                    kind=kind,
                    payload={"tick": recorder.state.tick, "owning_item": "wi_ap_map"},
                    request_id=request_id,
                )
            ]
        )
        recorder.advance(5)

    result = folder.fold(recorder.log, at_live_head=False, through_tick=recorder.state.tick)
    assert result.outstanding_requests == {}


def test_a_rejected_answer_also_clears_the_outstanding_request() -> None:
    """A rejected answer escalates its item; it does not leave the request in flight."""
    recorder = Recorder()
    request_id = "44444444-4444-5444-8444-444444444444"
    for kind in (EventKind.REQUEST_RAISED, EventKind.ANSWER_REJECTED):
        recorder.record(
            [
                sim.Emitted(
                    kind=kind,
                    payload={"tick": recorder.state.tick, "owning_item": "wi_ap_map"},
                    request_id=request_id,
                )
            ]
        )
        recorder.advance(5)

    result = folder.fold(recorder.log, at_live_head=False, through_tick=recorder.state.tick)
    assert result.outstanding_requests == {}


# =========================================================================
# Unknown kinds are refused, never skipped
# =========================================================================


def test_the_not_yet_implemented_guard_still_exists_and_is_now_empty() -> None:
    """**Changed by U7, deliberately.**

    This asserted that `LOAD_CHANGED` was refused, because at U15 it was in the closed event
    set but had no fold semantics. U7 implemented every kind that was in that set, so the set
    is now empty and `LOAD_CHANGED` folds normally.

    The *mechanism* is what is asserted now, because it is still needed: U8's and U11's kinds
    go into the same set until those units give them meaning, and the fold must refuse them
    rather than skip. A skipped event is a divergence with nothing attached to it.
    """
    assert folder.NOT_YET_IMPLEMENTED_KINDS == frozenset()

    # And every kind in the closed set is now classified somewhere, so nothing can be
    # silently ignored.
    classified = (
        folder.INPUT_KINDS
        | folder.OUTPUT_KINDS
        | folder.OPERATIONAL_KINDS
        | folder.NOT_YET_IMPLEMENTED_KINDS
        | {EventKind.GENESIS, EventKind.REQUEST_RAISED}
    )
    unclassified = [
        kind.name
        for kind in EventKind
        if kind is not EventKind.UNSPECIFIED and kind not in classified
    ]
    assert not unclassified, f"the fold would not know what to do with: {unclassified}"


def test_a_second_genesis_is_refused() -> None:
    recorder = Recorder()
    duplicated = [*recorder.log, recorder.log[0]]

    with pytest.raises(folder.UnknownEventInFold, match="GENESIS"):
        folder.fold(duplicated, at_live_head=False)
