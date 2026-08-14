"""The run report: what happened, and which event says so.

The report is a **read-side fold** and it imports the kernel library rather than
reinterpreting the log. That is a correctness requirement, not tidiness: a divergent
reimplementation here would make the audit artifact disagree with the kernel about what
happened while passing its own tests, and the product's central claim — that every report
claim resolves to the event that produced it — would be false while appearing to hold.

**Every number is marked as authored tuning** (R41). In Phase 1 there is no model behind any
of these figures; they are constants somebody chose. The report's reader is not the operator
and cannot be assumed to know that, so the marking travels with the data rather than living in
a caption someone might drop.

**The provenance trail is the report's most defensible content.** Since every metric number is
authored, what the report can say with real authority is procedural: which decision was taken,
by which route, whether the tacit line surfaced, and which directors were never told. None of
that is tuned — it is a record of what the CEO actually did.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from contracts.envelope import Envelope, EventKind
from simcore import hashing
from simcore import lifecycle
from simcore import log as folder
from simcore import step as sim
from simcore import time as simtime

#: Attached to every figure the report presents. Phase 1 has no demand model.
AUTHORED = "authored-tuning"


@dataclass(slots=True)
class Claim:
    """One number, and the event sequence that produced it."""

    label: str
    value: Any
    at_seq: int
    at_tick: int
    #: Always AUTHORED in Phase 1. A field rather than a footnote so it cannot be dropped.
    basis: str = AUTHORED

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "value": self.value,
            "at_seq": self.at_seq,
            "at_tick": self.at_tick,
            "at_day": simtime.day_of(self.at_tick),
            "basis": self.basis,
        }


@dataclass(slots=True)
class DecisionRecord:
    """A decision, and everything about how it was taken."""

    item: str
    choice: str
    note: str
    #: "in person" or "from the tray". The distinction the product is built around.
    path: str
    tacit_surfaced: bool
    tacit: str
    uninformed_directors: list[str]
    at_seq: int
    at_tick: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "item": self.item,
            "choice": self.choice,
            "note": self.note,
            "path": self.path,
            "tacit_surfaced": self.tacit_surfaced,
            "tacit": self.tacit,
            "uninformed_directors": list(self.uninformed_directors),
            "at_seq": self.at_seq,
            "at_tick": self.at_tick,
            "at_day": simtime.day_of(self.at_tick),
        }


@dataclass(slots=True)
class Report:
    run_id: str
    rules_ver: str
    state_shape_ver: int
    outcome: dict[str, Any] = field(default_factory=dict)
    trajectories: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    decisions: list[DecisionRecord] = field(default_factory=list)
    deliverables: list[dict[str, Any]] = field(default_factory=list)
    #: Every figure presented, each carrying the sequence it came from.
    claims: list[Claim] = field(default_factory=list)
    #: Departments left over their ceiling, and when.
    load_events: list[dict[str, Any]] = field(default_factory=list)
    attrition: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            # The report names its rules version: it is only interpretable against the
            # constants it was produced under.
            "rules_ver": self.rules_ver,
            "state_shape_ver": self.state_shape_ver,
            "every_number_is": AUTHORED,
            "outcome": self.outcome,
            "trajectories": self.trajectories,
            "decisions": [decision.to_dict() for decision in self.decisions],
            "deliverables": self.deliverables,
            "claims": [claim.to_dict() for claim in self.claims],
            "load_events": self.load_events,
            "attrition": self.attrition,
        }

    def claim_for(self, label: str) -> Claim | None:
        return next((claim for claim in self.claims if claim.label == label), None)


def build(run_id: str, events: list[Envelope], through_tick: int | None = None) -> Report:
    """Fold a log into a report. Reads only; never writes.

    The fold is `simcore.log.fold` with `at_live_head=False`, which is what forbids it from
    re-issuing an external request (R3). A report that re-issued would change the run it is
    describing.
    """
    ordered = sorted(events, key=lambda envelope: envelope.seq)
    if not ordered:
        raise ValueError("cannot report on an empty log")

    result = folder.fold(
        ordered, at_live_head=False, strict=False, through_tick=through_tick
    )
    state = result.state

    report = Report(
        run_id=run_id,
        rules_ver=ordered[0].rules_ver,
        state_shape_ver=hashing.STATE_SHAPE_VERSION,
    )

    previous: dict[str, int] = {}

    for envelope in ordered:
        payload = envelope.decoded_payload()
        tick = int(payload.get("tick", envelope.tick))

        # Every metric movement, attributed to the event that produced it. This is what makes
        # "follow a number back to its cause" a lookup rather than an inference.
        metrics = payload.get("metrics")
        if isinstance(metrics, dict):
            for key, value in metrics.items():
                if previous.get(key) != value:
                    report.trajectories.setdefault(key, []).append(
                        {
                            "value": value,
                            "at_seq": envelope.seq,
                            "at_tick": tick,
                            "at_day": simtime.day_of(tick),
                            "kind": envelope.kind.name,
                            "basis": AUTHORED,
                        }
                    )
                    previous[key] = value

        if envelope.kind is EventKind.DECISION_RESOLVED:
            report.decisions.append(
                DecisionRecord(
                    item=payload["item"],
                    choice=payload["choice"],
                    note=payload["note"],
                    path="in person" if payload["in_person"] else "from the tray",
                    tacit_surfaced=bool(payload["tacit_recorded"]),
                    tacit=_tacit_for(state, payload["item"], int(payload["cp_index"])),
                    uninformed_directors=list(payload.get("uninformed", [])),
                    at_seq=envelope.seq,
                    at_tick=tick,
                )
            )

        elif envelope.kind is EventKind.DELIVERABLE_PRODUCED:
            recorded = payload["deliverable"]
            report.deliverables.append(
                {
                    "item": recorded["item"],
                    "title": recorded["title"],
                    "kind": recorded["kind"],
                    "provenance": list(recorded["provenance"]),
                    "tacit": list(recorded["tacit"]),
                    "at_seq": envelope.seq,
                    "at_day": simtime.day_of(tick),
                }
            )

        elif envelope.kind is EventKind.LOAD_CHANGED:
            report.load_events.append(
                {
                    "load": payload.get("load", {}),
                    "days_below_threshold": payload.get("days_below_threshold", {}),
                    "at_seq": envelope.seq,
                    "at_day": simtime.day_of(tick),
                    "basis": AUTHORED,
                }
            )

        elif envelope.kind is EventKind.ATTRITION:
            report.attrition.append(
                {
                    "person": payload["person"],
                    "director": payload["director"],
                    "returned_item": payload.get("returned_item", ""),
                    "at_seq": envelope.seq,
                    "at_day": simtime.day_of(tick),
                    "basis": AUTHORED,
                }
            )

        elif envelope.kind is EventKind.RUN_TERMINATED:
            report.outcome = {
                "reason": payload["reason"],
                "detail": payload["detail"],
                "at_seq": envelope.seq,
                "at_tick": tick,
                "at_day": simtime.day_of(tick),
                "decisions_taken": payload["decisions_taken"],
                "decision_supply": payload["decision_supply"],
                "deliverables": payload["deliverables"],
            }

    if not report.outcome:
        report.outcome = {
            "reason": "running",
            "detail": "the run has not ended",
            "at_seq": ordered[-1].seq,
            "at_tick": state.tick,
            "at_day": simtime.day_of(state.tick),
            "decisions_taken": len(report.decisions),
            "decision_supply": lifecycle.decision_supply(),
            "deliverables": len(report.deliverables),
        }

    report.claims = _claims(report, state, ordered[-1].seq)
    return report


def _tacit_for(state: sim.State, item_id: str, cp_index: int) -> str:
    """The tacit line, if this decision surfaced one.

    Read from the folded state rather than from the event, because the event records *that* a
    line surfaced while the line itself belongs to the authored checkpoint.
    """
    item = state.items.get(item_id)
    if item is None or cp_index >= len(item.decisions):
        return ""
    for decision in item.decisions:
        if decision.tacit:
            return decision.tacit
    return ""


def _claims(report: Report, state: sim.State, head_seq: int) -> list[Claim]:
    """Every headline figure, each pointing at the event it came from."""
    claims: list[Claim] = []

    for key, points in report.trajectories.items():
        last = points[-1]
        claims.append(
            Claim(
                label=f"final {key}",
                value=last["value"],
                at_seq=last["at_seq"],
                at_tick=last["at_tick"],
            )
        )

    outcome = report.outcome
    claims.append(
        Claim(
            label="outcome",
            value=outcome["reason"],
            at_seq=outcome["at_seq"],
            at_tick=outcome["at_tick"],
        )
    )
    claims.append(
        Claim(
            label="decisions taken",
            value=outcome["decisions_taken"],
            at_seq=outcome["at_seq"],
            at_tick=outcome["at_tick"],
        )
    )
    claims.append(
        Claim(
            label="decisions in person",
            value=sum(1 for decision in report.decisions if decision.path == "in person"),
            at_seq=head_seq,
            at_tick=state.tick,
        )
    )
    claims.append(
        Claim(
            label="tacit lines surfaced",
            value=sum(1 for decision in report.decisions if decision.tacit_surfaced),
            at_seq=head_seq,
            at_tick=state.tick,
        )
    )
    claims.append(
        Claim(
            label="deliverables",
            value=len(report.deliverables),
            at_seq=head_seq,
            at_tick=state.tick,
        )
    )

    return claims
