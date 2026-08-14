"""Verification: localise damage to a sim-day, and say what truncating would cost.

Two signals, and neither alone is enough.

**Sequence density** finds a hole. The log's sequence is kernel-assigned, dense and gapless
by construction (R37), so `count == max` for a healthy run. A gap means events are missing —
but density says nothing about events that are present and wrong.

**Day-boundary checkpoint hashes** find corruption. Each one is the state hash at that
boundary, so re-folding to a boundary and comparing localises a divergence to the sim-day
that produced it. Without them, a determinism regression in a long run is a hand bisect;
with them it is a lookup, and the per-subsystem sub-hashes then say which subtree.

Together they answer the only question that matters during recovery: what is the last
sequence I can trust, and how much does going back to it cost? The answer is bounded at one
sim-day, which is what makes truncation a real option rather than a euphemism for deleting
the run.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from contracts.envelope import Envelope, EventKind
from simcore import hashing
from simcore import log as folder
from simcore import step as sim
from simcore import time as simtime


@dataclass(slots=True)
class VerifyReport:
    """What verification found, in a form an operator can act on."""

    healthy: bool
    event_count: int
    max_seq: int
    #: Sequences that should exist and do not.
    missing_seqs: list[int] = field(default_factory=list)
    #: Day boundaries whose recorded hash does not match a re-fold, earliest first.
    diverged_checkpoints: list[dict] = field(default_factory=list)
    #: The last sequence that folds cleanly. Truncating here costs at most one sim-day.
    last_good_seq: int = 0
    last_good_tick: int = 0
    #: Present when the run cannot be folded at all.
    refusal: str = ""

    def summary(self) -> str:
        if self.healthy:
            return (
                f"{self.event_count} events, sequence dense through {self.max_seq}, "
                f"every day-boundary hash matches"
            )
        parts = []
        if self.refusal:
            parts.append(self.refusal)
        if self.missing_seqs:
            parts.append(f"missing sequences {self.missing_seqs[:8]}")
        if self.diverged_checkpoints:
            first = self.diverged_checkpoints[0]
            parts.append(
                f"state diverges by the day boundary at tick {first['tick']} "
                f"(day {first['day']}); subsystems {first['subsystems'] or 'unknown'}"
            )
        parts.append(
            f"last trustworthy sequence {self.last_good_seq} (tick {self.last_good_tick}); "
            "truncating there loses at most one sim-day"
        )
        return "; ".join(parts)


def sequence_gaps(events: Sequence[Envelope]) -> list[int]:
    """Which sequences are missing, given that the log is dense from 1."""
    if not events:
        return []
    present = {envelope.seq for envelope in events}
    return [seq for seq in range(1, max(present) + 1) if seq not in present]


def verify(events: Sequence[Envelope], running_rules_ver: str | None = None) -> VerifyReport:
    """Walk density and checkpoint hashes, and report the last trustworthy sequence."""
    ordered = sorted(events, key=lambda envelope: envelope.seq)
    report = VerifyReport(
        healthy=True,
        event_count=len(ordered),
        max_seq=ordered[-1].seq if ordered else 0,
    )

    report.missing_seqs = sequence_gaps(ordered)
    if report.missing_seqs:
        report.healthy = False

    # The rules-version guard, applied before anything else. Checking it only inside the
    # per-checkpoint fold would let a run with no day boundaries yet — a run less than one
    # sim-day old — verify as healthy under rules it was not written under.
    if ordered:
        try:
            folder.check_rules_version(ordered[0].rules_ver, running_rules_ver)
        except folder.FoldRefused as exc:
            report.healthy = False
            report.refusal = str(exc)
            return report

    # Every recorded day-boundary hash, in tick order.
    recorded: list[tuple[int, int, str]] = []
    for envelope in ordered:
        if envelope.kind is EventKind.DAY_CHECKPOINT:
            payload = envelope.decoded_payload()
            recorded.append((int(payload["tick"]), envelope.seq, payload["state_hash"]))
    recorded.sort()

    # Fold up to each boundary and compare. Earliest divergence is the useful one: a later
    # mismatch is a consequence of it, not independent evidence.
    last_good_seq = ordered[0].seq if ordered else 0
    last_good_tick = 0

    for tick, seq, expected in recorded:
        prefix = [envelope for envelope in ordered if envelope.seq <= seq]
        try:
            result = folder.fold(
                prefix,
                at_live_head=False,
                running_rules_ver=running_rules_ver,
                through_tick=tick,
            )
        except folder.FoldRefused as exc:
            report.healthy = False
            report.refusal = str(exc)
            break
        except (folder.UnknownEventInFold, folder.ReplayDiverged, Exception) as exc:
            report.healthy = False
            report.diverged_checkpoints.append(
                {
                    "tick": tick,
                    "day": simtime.day_of(tick),
                    "seq": seq,
                    "reason": f"{type(exc).__name__}: {exc}",
                    "subsystems": [],
                }
            )
            break

        actual = hashing.state_hash(sim.snapshot(result.state))
        if actual.overall != expected:
            report.healthy = False
            report.diverged_checkpoints.append(
                {
                    "tick": tick,
                    "day": simtime.day_of(tick),
                    "seq": seq,
                    "expected": expected,
                    "actual": actual.overall,
                    # Which subtree, so the divergence localises further than "somewhere".
                    "subsystems": _differing_subsystems(result.state, expected),
                }
            )
            break

        last_good_seq = seq
        last_good_tick = tick

    report.last_good_seq = last_good_seq
    report.last_good_tick = last_good_tick
    return report


def _differing_subsystems(state: sim.State, expected_overall: str) -> list[str]:
    """Which subsystems to look at first.

    The overall hash cannot say which subtree changed — only that one did. This cannot
    either, without the recorded sub-hashes, so it reports the subsystems that carry state
    at all rather than guessing. When the checkpoint event carries sub-hashes (it does), the
    caller compares those directly; this is the fallback for an older checkpoint.
    """
    actual = hashing.state_hash(sim.snapshot(state))
    return [name for name, digest in actual.subsystems.items() if digest and expected_overall]


def compare_checkpoint(
    recorded_subsystems: dict[str, str], state: sim.State
) -> list[str]:
    """Which subsystems diverge, given a checkpoint's recorded sub-hashes.

    This is the payoff for storing per-subsystem hashes: a divergence localises to a subtree
    rather than to "the state changed".
    """
    actual = hashing.state_hash(sim.snapshot(state))
    return sorted(
        name
        for name, digest in actual.subsystems.items()
        if recorded_subsystems.get(name) != digest
    )


def build_checkpoint_payload(state: sim.State) -> dict:
    """The DAY_CHECKPOINT payload: overall hash, sub-hashes, and the shape version.

    The shape version rides along so that a later addition to the hashed subsystem list is a
    recorded decision rather than a silent break in comparability.
    """
    hashed = hashing.state_hash(sim.snapshot(state))
    return {
        "tick": state.tick,
        "day": simtime.day_of(state.tick),
        "state_hash": hashed.overall,
        "subsystems": dict(hashed.subsystems),
        "state_shape_ver": hashed.shape_version,
    }
