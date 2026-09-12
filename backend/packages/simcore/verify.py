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

**And the shape version is read before either of them is believed** (R27). A day-boundary hash
covers whatever subsystems the state shape declared when it was written, and the version is inside
the digest — so a log written under an older shape cannot match a hash computed under a newer one,
however healthy it is. Without this check that is indistinguishable from corruption, and U15 would
have made every run written before it report as damaged on the day it landed. So a recorded shape
that is not the running one is reported as a **version move**: the hashes are declared
incomparable, the sequence density still stands on its own, and the summary says which versions are
involved rather than inviting an operator to truncate a run that is fine.
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
    #: Set when this log's checkpoints were written under a different state shape (R27).
    #:
    #: Not a divergence and not damage: it means the hashes cannot be compared at all, so the
    #: checkpoint half of verification is skipped and says so. A run in this state is still
    #: foldable — nothing about the shape version stops the fold — and it is still unplayable, for
    #: the separate reason that its rules version moved with it.
    shape_move: dict = field(default_factory=dict)
    #: The last sequence that folds cleanly. Truncating here costs at most one sim-day.
    last_good_seq: int = 0
    last_good_tick: int = 0
    #: Present when the run cannot be folded at all.
    refusal: str = ""

    def summary(self) -> str:
        if self.healthy and self.shape_move:
            return (
                f"{self.event_count} events, sequence dense through {self.max_seq}; "
                f"day-boundary hashes not compared — they were written under state shape "
                f"{self.shape_move['recorded']} and this build writes "
                f"{self.shape_move['running']}"
            )
        if self.healthy:
            return (
                f"{self.event_count} events, sequence dense through {self.max_seq}, "
                f"every day-boundary hash matches"
            )
        parts = []
        if self.shape_move:
            parts.append(
                f"day-boundary hashes were written under state shape "
                f"{self.shape_move['recorded']} and this build writes "
                f"{self.shape_move['running']}, so they were not compared"
            )
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
            written_under = int(payload.get("state_shape_ver", 1))
            if written_under != hashing.STATE_SHAPE_VERSION and not report.shape_move:
                # **Before any hash is compared** (R27). The checkpoint carries the shape it was
                # written under precisely so this question can be asked first, and asking it second
                # would mean reporting a deliberate change as corruption — with a "truncate here"
                # remedy attached to a run that has nothing wrong with it. The first mismatch is
                # enough: a log's checkpoints are written by one build.
                report.shape_move = {
                    "recorded": written_under,
                    "running": hashing.STATE_SHAPE_VERSION,
                    "at_seq": envelope.seq,
                    "at_tick": int(payload["tick"]),
                    "history": list(hashing.SHAPE_HISTORY.get(written_under, ())),
                }
            recorded.append((int(payload["tick"]), envelope.seq, payload["state_hash"]))
    recorded.sort()

    # Fold up to each boundary and compare, unless the shape moved — in which case the fold still
    # runs and only the comparison is skipped. That is deliberately not an early return: a fold that
    # completes is itself evidence, so `last_good_seq` keeps meaning "the last sequence that folds
    # cleanly" instead of collapsing to "the last sequence that exists". What is lost with the
    # hashes is the ability to detect a *wrong* state, and the report says so rather than implying
    # the checkpoints passed.
    #
    # **When U15 moved the shape it moved the rules with it**, so a run written before it is refused
    # by the guard above and never reaches here. This branch is for a shape move that does not change
    # what the numbers mean — a hashed subsystem added to a run that plays identically — which R27
    # asks be legible whichever unit eventually makes one.
    #
    # Earliest divergence is the useful one: a later mismatch is a consequence of it, not
    # independent evidence.
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

        if report.shape_move:
            # Folded, not compared. The recorded digest covers a different set of subsystems and
            # carries a different version inside it, so a mismatch here would say nothing about
            # whether the state is right.
            last_good_seq = seq
            last_good_tick = tick
            continue

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
