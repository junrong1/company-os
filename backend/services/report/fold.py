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

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from contracts.envelope import Envelope, EventKind
from simcore import compare as comparing
from simcore import effects
from simcore import hashing
from simcore import lifecycle
from simcore import log as folder
from simcore import pending as pend
from simcore import step as sim
from simcore import time as simtime

#: Attached to every figure the report presents. Phase 1 has no demand model.
AUTHORED = "authored-tuning"


@dataclass(slots=True)
class Claim:
    """One number, and the event that produced it — named by run and by sequence.

    **A sequence alone stopped being an address when forks landed** (U16, U20). A fork copies
    its parent's rows verbatim, so sequence 42 exists in the parent and in every child, and
    below the divergence it is the same event in all of them while above it, it is not. A claim
    carrying a bare sequence is therefore resolvable only by whoever already knows which log it
    came from — which the run report did know, and the Universe report does not. Both carry the
    pair now, so "follow this number back to its event" is one lookup on any surface.
    """

    run_id: str
    label: str
    value: Any
    at_seq: int
    at_tick: int
    #: Always AUTHORED in Phase 1. A field rather than a footnote so it cannot be dropped.
    basis: str = AUTHORED

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "label": self.label,
            "value": self.value,
            "at_seq": self.at_seq,
            "at_tick": self.at_tick,
            "at_day": simtime.day_of(self.at_tick),
            "basis": self.basis,
        }


@dataclass(slots=True)
class DecisionRecord:
    """A decision, and everything about how it was taken.

    Addressed by run and sequence together, for the reason `Claim` gives: the same decision
    sequence exists in a parent and in each of its children, and a Universe report lists all of
    them beside each other.
    """

    run_id: str
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
            "run_id": self.run_id,
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
class AuthorizationRecord:
    """One time a director asked to read another line, and what it cost (M43).

    **One row per ask, not per item.** M42 makes a second request for the same knowledge a fresh
    question rather than a retry, so the report has to be able to show a CEO who refused on day two
    and granted on day five as two decisions with two consequences — collapsing them onto the item
    would report the last answer as though it had always been the answer.

    The consequence is a measured quantity rather than an adjective: `stalled_ticks` is how long the
    work stood still between the ask and the answer, and for a refusal that is still standing it is
    how long it has stood still so far. That is the figure that makes "a refusal has consequences"
    checkable from the log by somebody who does not trust the sentence above it.
    """

    item: str
    #: The director who asked, and the line whose knowledge they asked for.
    asking: str
    needs: str
    #: Which time of asking this is, for this item.
    asks: int
    #: One of five: `open` while nobody has answered, `granted`, `refused`, `abandoned` when the
    #: deadline answered for the CEO, and `overtaken` when the work ended before anybody did.
    outcome: str
    raised_at_seq: int
    raised_at_tick: int
    decided_at_seq: int = 0
    decided_at_tick: int = 0
    #: Sim-ticks the item stood still because of this ask. Still accumulating when `open`.
    stalled_ticks: int = 0
    #: True while the CEO has not answered and the run has not answered for them.
    open: bool = False
    #: Whether the item this was about had been delivered by the end of the log.
    delivered: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "item": self.item,
            "asking": self.asking,
            "needs": self.needs,
            "asks": self.asks,
            "outcome": self.outcome,
            "raised_at_seq": self.raised_at_seq,
            "raised_at_tick": self.raised_at_tick,
            "raised_at_day": simtime.day_of(self.raised_at_tick),
            "decided_at_seq": self.decided_at_seq,
            "decided_at_tick": self.decided_at_tick,
            "decided_at_day": simtime.day_of(self.decided_at_tick) if self.decided_at_tick else 0,
            "stalled_ticks": self.stalled_ticks,
            "stalled_hours": self.stalled_ticks // simtime.TICKS_PER_SIM_HOUR,
            "open": self.open,
            "delivered": self.delivered,
            "basis": AUTHORED,
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
    #: Every Authorization the CEO was asked for, and what each answer did (M43, U15).
    authorizations: list[AuthorizationRecord] = field(default_factory=list)

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
            "authorizations": [record.to_dict() for record in self.authorizations],
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
                    run_id=run_id,
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

        elif envelope.kind is EventKind.REQUEST_RAISED and payload.get("service") == pend.CEO:
            report.authorizations.append(
                AuthorizationRecord(
                    item=str(payload.get("owning_item", "")),
                    asking=str(payload.get("person", "")),
                    needs=str(payload.get("needs", "")),
                    asks=int(payload.get("asks", 1)),
                    outcome="open",
                    raised_at_seq=envelope.seq,
                    raised_at_tick=tick,
                    open=True,
                )
            )

        elif envelope.kind is EventKind.AUTHORIZATION_DECIDED:
            _close_authorization(
                report,
                str(payload.get("item", "")),
                outcome="granted" if payload.get("granted") else "refused",
                seq=envelope.seq,
                tick=tick,
            )

        elif envelope.kind is EventKind.ANSWER_REJECTED and payload.get("service") == pend.CEO:
            # The two ways an Authorization ends without the CEO answering it, and the report keeps
            # them apart because they read differently to a person: the deadline answered for them,
            # or the work ended before anybody did.
            _close_authorization(
                report,
                str(payload.get("owning_item", "")),
                outcome="abandoned" if payload.get("abandoned") else "overtaken",
                seq=envelope.seq,
                tick=tick,
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
            "decision_supply": lifecycle.decision_supply(state.scenario),
            "deliverables": len(report.deliverables),
        }

    _settle_authorizations(report, state)
    report.claims = _claims(report, state, ordered[-1].seq)
    return report


def _close_authorization(
    report: Report, item_id: str, *, outcome: str, seq: int, tick: int
) -> None:
    """Attach an answer to the newest open ask for this item.

    Newest rather than first, because M42's second ask is a second row: an answer belongs to the
    question that was actually open when it arrived. A missing row is ignored rather than invented —
    a log prefix can hold an answer whose request is before the prefix, and a report that
    manufactured the question would be reporting an ask nobody made.
    """
    for record in reversed(report.authorizations):
        if record.item == item_id and record.open:
            record.outcome = outcome
            record.decided_at_seq = seq
            record.decided_at_tick = tick
            record.stalled_ticks = max(0, tick - record.raised_at_tick)
            record.open = False
            return


def _settle_authorizations(report: Report, state: sim.State) -> None:
    """Finish the rows the log did not close, and say what each one's item ended up doing.

    An ask the CEO never answered is still stalling its item at the head of the log, so its
    consequence is measured to *there* rather than left at zero — an unanswered request that
    reported no cost would be the one reading of this mechanic that is definitely wrong.
    """
    delivered = {deliverable["item"] for deliverable in report.deliverables}
    for record in report.authorizations:
        if record.open:
            record.stalled_ticks = max(0, state.tick - record.raised_at_tick)
        record.delivered = record.item in delivered


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
                run_id=report.run_id,
                label=f"final {key}",
                value=last["value"],
                at_seq=last["at_seq"],
                at_tick=last["at_tick"],
            )
        )

    outcome = report.outcome
    claims.append(
        Claim(
            run_id=report.run_id,
            label="outcome",
            value=outcome["reason"],
            at_seq=outcome["at_seq"],
            at_tick=outcome["at_tick"],
        )
    )
    claims.append(
        Claim(
            run_id=report.run_id,
            label="decisions taken",
            value=outcome["decisions_taken"],
            at_seq=outcome["at_seq"],
            at_tick=outcome["at_tick"],
        )
    )
    claims.append(
        Claim(
            run_id=report.run_id,
            label="decisions in person",
            value=sum(1 for decision in report.decisions if decision.path == "in person"),
            at_seq=head_seq,
            at_tick=state.tick,
        )
    )
    claims.append(
        Claim(
            run_id=report.run_id,
            label="tacit lines surfaced",
            value=sum(1 for decision in report.decisions if decision.tacit_surfaced),
            at_seq=head_seq,
            at_tick=state.tick,
        )
    )
    claims.append(
        Claim(
            run_id=report.run_id,
            label="deliverables",
            value=len(report.deliverables),
            at_seq=head_seq,
            at_tick=state.tick,
        )
    )

    return claims


# =========================================================================
# The timeline diff (U18)
# =========================================================================
#
# Two futures at one sim-day, metric by metric, with the decision that separated them named.
#
# **Both sides fold through the fold above** (R12). Nothing here reconstructs state: a diff
# whose figures came from a second reading would let the Universe and the report disagree about
# a run while each passed its own tests — the same failure the module docstring opens with, and
# worse here, because the diff is the surface the product's central claim is demonstrated on.
#
# **Both sides fold from zero, on every ask, including every step of the day control.** The
# report already folds from zero on every build and the plan accepts that at MVP scale; this adds
# a multiplier the report does not have, because moving the day one step is a fresh pair of
# folds. Measured on the compose path over a twenty-one-day lineage of 88 events a side: about
# 250 ms per step, which is a control that feels immediate. Persisted snapshots are the lever if
# it ever binds, and the shape here does not need to change for them: `state_at_day` is the one
# place that decides where a fold starts.
#
# **The comparison point is a day's *first* tick, and that choice is load-bearing.** Folding to
# a day's *last* tick would mean folding a timeline whose clock is standing still somewhere
# inside that day forward through ticks it never ran — inventing a future for it and reporting
# the invention beside the other side's history. A day's first tick is reached by both sides or
# by neither, so "at the same sim-day" is one tick, on both sides, or it is a refusal. It is
# also the tick the kernel checkpoints its own state hash on, which is what makes the fold here
# checkable against the log rather than merely trusted.

#: Attached to the runway figure in place of a favourable direction.
#:
#: Runway is not a metric and has no `good` on the wire, so the diff states that it has none
#: rather than choosing one. The comparison surface renders it neutral for exactly this reason;
#: a diff that decided rising runway were favourable would be the second answer to a question
#: the kernel deliberately does not answer.
NO_DIRECTION = 0


@dataclass(frozen=True, slots=True)
class TimelineLog:
    """One side of a diff: its log, and the store's reading of where its clock got to.

    **The row is a projection, and the log is a floor under it.** `runs.current_tick` moves only
    on append, so a timeline that has been quiet has a row behind its own clock — U17 measured a
    run whose state was at tick 58 with a row saying tick 1. The report cannot overlay the live
    fold the way the kernel's own tree read does, because that would mean importing the kernel
    (R4). What it can do is take the larger of the row and the newest tick the log actually
    proves, and bound the diff by that — which leaves the bound conservative and never
    optimistic, so a day this diff offers is a day both timelines really reached.

    **What the row says about a run being over is not carried at all**, and that is a deliberate
    omission rather than a gap. Measured live on the compose path: two timelines that had both
    reached their horizon had `terminal_reason` NULL and rates of 1 and 3 in `runs`, and no
    terminal event in either log — the fact lives only in the kernel's folded state. So the diff
    reads it from *its own* fold, where it is exact and free, and the column says whether the
    timeline had ended **by the day being compared at**, which is the question a column at that
    day is answering anyway.
    """

    run_id: str
    events: list[Envelope]
    #: `runs.current_tick`, as read.
    current_tick: int

    @property
    def reached_tick(self) -> int:
        """The newest tick this timeline is *known* to have reached.

        The envelope's own tick rather than its payload's: a `CEO_INPUT` names the tick it
        applies at, which the run may not have got to yet, and a floor built from that would
        claim reach the log does not prove.
        """
        return max(self.current_tick, max((event.tick for event in self.events), default=0))

    @property
    def current_day(self) -> int:
        return simtime.day_of(self.reached_tick)


@dataclass(frozen=True, slots=True)
class Side:
    """Where one timeline was when its clock reached the day being compared at."""

    run_id: str
    #: The last sequence folded, which together with the run id is where every figure came from.
    through_seq: int
    #: The kernel's own hash of the state these figures were read off. At a day boundary the log
    #: holds a DAY_CHECKPOINT carrying the same value, so "the diff folds what the kernel folded"
    #: is checkable rather than asserted.
    state_hash: str
    #: Whether this timeline had already ended **by the day being compared at**, and why. Read
    #: from the fold rather than from the run row — see `TimelineLog`, and the measurement in it.
    terminal_reason: str
    #: The newest tick this timeline is known to have reached, and its day. The bound on the day
    #: control comes from the lesser of the two sides'.
    reached_tick: int
    current_day: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "through_seq": self.through_seq,
            "state_hash": self.state_hash,
            "terminal_reason": self.terminal_reason,
            "reached_tick": self.reached_tick,
            "current_day": self.current_day,
        }


@dataclass(frozen=True, slots=True)
class Row:
    """One figure, on both sides, with the definition needed to read the difference.

    The metric's own `good` travels with it, because whether a movement is an improvement is the
    kernel's to state and the surface's to read — cash rising and manual hours falling are both
    wins, and a uniform rule renders the automation gain as a regression. `None` on a side is a
    figure that side could not know, which is not the same as zero and must not render as one.
    """

    key: str
    label: str
    unit: str
    good: int
    left: int | None
    right: int | None
    basis: str = AUTHORED

    @property
    def delta(self) -> int | None:
        if self.left is None or self.right is None:
            return None
        return self.right - self.left

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "unit": self.unit,
            "good": self.good,
            "left": self.left,
            "right": self.right,
            "delta": self.delta,
            "basis": self.basis,
        }


@dataclass(slots=True)
class Diff:
    """Two timelines at one sim-day, and what separated them."""

    day: int
    #: The tick both sides were folded to. One tick, not two — see the section note above.
    at_tick: int
    #: The furthest day both sides have reached, and therefore the bound on the day control.
    max_day: int
    left: Side | None = None
    right: Side | None = None
    rows: list[Row] = field(default_factory=list)
    separation: dict[str, Any] | None = None
    #: Set when the answer is no. The request was well-formed; the diff would have misled.
    refusal: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "day": self.day,
            "at_tick": self.at_tick,
            "max_day": self.max_day,
            "every_number_is": AUTHORED,
            "state_shape_ver": hashing.STATE_SHAPE_VERSION,
            "left": None if self.left is None else self.left.to_dict(),
            "right": None if self.right is None else self.right.to_dict(),
            "rows": [row.to_dict() for row in self.rows],
            "separation": self.separation,
            "refusal": self.refusal,
        }


def fold_tick_of(envelope: Envelope) -> int:
    """The tick the fold places an event at.

    The fold's own expression, *called* rather than copied, because the truncation below has to
    agree with it exactly: the fold refuses a `through_tick` below the tick the log proves the
    run reached, so a prefix built by a different rule would refuse on a log the fold would
    have accepted. U18 wrote this as a copy of one branch of that rule and U19 made it the call
    — the rule moved underneath it, which is what a copy of somebody else's rule does.
    """
    return folder.issued_at_tick(envelope.decoded_payload(), envelope.tick)


def state_at_day(events: list[Envelope], day: int) -> tuple[sim.State, int]:
    """Fold a timeline to the first tick of `day`. Returns the state and the sequence reached.

    **The prefix is a filter rather than a sequence cut, and the rule it filters by is the
    fold's own.** A `CEO_INPUT` carries the tick it *applies* at, deliberately a few ticks ahead
    of the tick it was submitted on, and an `INPUT_RECEIVED` carries the tick its answer lands
    at — so a held direction or an arriving statement across a day boundary leaves an event
    naming a tick past it. `fold_tick_of` is the fold's `issued_at_tick`, so such an event is
    **kept** and applied at the tick it was issued on, which is where the run really was.

    U18 filtered by the payload's own tick and dropped it, because `fold` then refused a
    sequence prefix holding one — and the drop cost the state hash, which covers the CEO's
    scheduled inputs, so at a straddling boundary this fold's hash was not the kernel's. U19
    closed that in `fold` (the bound is the tick the log proves, not the largest it names), and
    both readings now produce the byte the log's `DAY_CHECKPOINT` holds.
    """
    at_tick = simtime.tick_of_day_start(day)
    prefix = [envelope for envelope in events if fold_tick_of(envelope) <= at_tick]
    result = folder.fold(prefix, at_live_head=False, strict=False, through_tick=at_tick)
    return result.state, result.through_seq


# =========================================================================
# Every day of one timeline, in one pass (U20)
# =========================================================================
#
# `state_at_day` answers "where was this timeline on day N" and folds from zero to do it. Asking
# it for *every* day is O(days squared), and the Universe report asks for every day of every
# timeline — so the cost the diff accepts for one step of a control becomes the cost of opening
# the report.
#
# **Measured, on a 30-day timeline of 123 events**: 2617 ms folding each day from zero, 254 ms
# walking the days once and resuming each from the last. Both produce byte-identical state
# hashes at every boundary, and both agree with the `DAY_CHECKPOINT` the kernel wrote there.
# A three-timeline lineage is the difference between eight seconds and under one.
#
# The resumption is `fold`'s own `resume_from`, not a second fold: each day is handed the
# previous day's state and exactly the events issued since it. Nothing here re-derives a tick.


@dataclass(frozen=True, slots=True)
class DayFold:
    """One timeline at one day boundary: where it was, and the event that proves it was there."""

    day: int
    at_tick: int
    #: The event this boundary is cited by: the `DAY_CHECKPOINT` the kernel wrote at this tick,
    #: or `GENESIS` at day 1, which opens at tick 0 where no checkpoint is written. Zero when the
    #: log holds neither — a prefix rather than a healthy run, and a reading with no citation,
    #: which is a reading the report does not present (M55).
    at_seq: int
    state: sim.State
    #: This fold's own hash of the state above.
    state_hash: str
    #: The hash the kernel recorded at this boundary, and its sub-hashes. Empty at day 1 and in a
    #: log whose checkpoint is missing.
    recorded_hash: str = ""
    recorded_subsystems: dict[str, str] = field(default_factory=dict)

    @property
    def agrees(self) -> bool:
        """Whether this fold reproduced the kernel's own hash.

        True when there is nothing recorded to check against, because an absent checkpoint is not
        a disagreement. Whether an absence is itself a problem is the sequence-density question,
        which `simcore.verify.sequence_gaps` answers separately and cheaply.
        """
        return not self.recorded_hash or self.recorded_hash == self.state_hash


def window_day(issued_at_tick: int) -> int:
    """Which day boundary's fold an event issued at this tick belongs to.

    The boundary at day D folds the ticks `(start(D-1), start(D)]` — the previous day's, and D's
    own opening tick. So an event is *not* in the window of the day it falls in: one issued on the
    last tick of day 4 is applied on the way to day 5's boundary, and one issued exactly on a
    boundary was already applied by the walk that stopped there.

    Expressed as `day_of(t - 1) + 1` so the division stays `simtime`'s. A second arithmetic for a
    sim-day here is exactly the drift the tree, the HUD and the report are written to avoid.
    """
    if issued_at_tick <= 0:
        return 1
    return simtime.day_of(issued_at_tick - 1) + 1


def walk_days(events: list[Envelope], *, through_day: int) -> Iterator[DayFold]:
    """Fold one timeline day by day, yielding it at each boundary. One pass over the log.

    Yields rather than returns, because the caller wants a handful of figures per day and a list
    would hold one whole `State` per day per timeline for the life of the build. Each yielded
    state is the fold's own — `fold` clones on resume — so the next day cannot move the last one
    underneath a caller that kept it.
    """
    ordered = sorted(events, key=lambda envelope: envelope.seq)

    windows: dict[int, list[Envelope]] = {}
    for envelope in ordered:
        windows.setdefault(window_day(fold_tick_of(envelope)), []).append(envelope)

    #: Where each boundary's citation and recorded hash come from. Day 1 cites genesis, which is
    #: the event that produced the state at tick 0 and carries no hash of its own.
    cited: dict[int, tuple[int, str, dict[str, str]]] = {}
    for envelope in ordered:
        if envelope.kind is EventKind.GENESIS:
            cited.setdefault(0, (envelope.seq, "", {}))
        elif envelope.kind is EventKind.DAY_CHECKPOINT:
            payload = envelope.decoded_payload()
            cited[int(payload["tick"])] = (
                envelope.seq,
                str(payload["state_hash"]),
                {
                    str(name): str(digest)
                    for name, digest in (payload.get("subsystems") or {}).items()
                },
            )

    resume: tuple[sim.State, int] | None = None
    for day in range(1, through_day + 1):
        at_tick = simtime.tick_of_day_start(day)
        result = folder.fold(
            windows.get(day, []),
            at_live_head=False,
            strict=False,
            resume_from=resume,
            through_tick=at_tick,
        )
        resume = (result.state, result.through_seq)
        at_seq, recorded_hash, recorded_subsystems = cited.get(at_tick, (0, "", {}))
        yield DayFold(
            day=day,
            at_tick=at_tick,
            at_seq=at_seq,
            state=result.state,
            state_hash=hashing.state_hash(sim.snapshot(result.state)).overall,
            recorded_hash=recorded_hash,
            recorded_subsystems=recorded_subsystems,
        )


def _side(timeline: TimelineLog, day: int) -> tuple[Side, sim.State]:
    """One side folded, and the state it was folded to — which the rows still need."""
    state, through_seq = state_at_day(timeline.events, day)
    return Side(
        run_id=timeline.run_id,
        through_seq=through_seq,
        state_hash=hashing.state_hash(sim.snapshot(state)).overall,
        terminal_reason=state.terminal_reason,
        reached_tick=timeline.reached_tick,
        current_day=timeline.current_day,
    ), state


def _rows(left: sim.State, right: sim.State) -> list[Row]:
    """Every figure the two sides are compared on, in the order the HUD names them.

    The metric table comes from the kernel rather than from a list here, so the diff's columns
    are the run's columns and a metric added to one is added to the other. Runway follows them,
    computed by `simcore.compare.runway_days` off `day_cost_terms` — the same two calls a
    comparison branch makes, so a runway in the Universe and a runway at a checkpoint are one
    rule rather than two that agree today.
    """
    rows: list[Row] = []
    for definition in effects.metric_defs_to_state():
        key = str(definition["key"])
        rows.append(
            Row(
                key=key,
                label=str(definition["label"]),
                unit=str(definition["unit"]),
                good=int(definition["good"]),
                left=left.metrics.get(key),
                right=right.metrics.get(key),
            )
        )

    rows.append(
        Row(
            key="runway",
            label="Runway",
            unit="days",
            good=NO_DIRECTION,
            left=_runway(left),
            right=_runway(right),
        )
    )
    return rows


def _runway(state: sim.State) -> int | None:
    fixed, draw_cost, salaries = sim.day_cost_terms(state)
    return comparing.runway_days(state.metrics["cash"], fixed + draw_cost + salaries)


def diff(
    left: TimelineLog,
    right: TimelineLog,
    *,
    day: int | None = None,
    separation: dict[str, Any] | None = None,
) -> Diff:
    """Two timelines at one sim-day (M50, M52).

    `day` defaults to the furthest day *both* sides have reached, which is the newest point the
    two can honestly be compared at. A day past that is refused with the reason rather than
    folded, because the only way to produce figures for a day a timeline has not reached is to
    run it there — and a column of invented ticks beside a column of history is the one output
    this surface must never produce.

    Whether the two are timelines of one Universe is not decidable from two logs, so it is not
    decided here: the caller holds the tree and refuses before calling. What *is* decidable is
    that a timeline diffed against itself has nothing to separate it, and that is refused here.
    """
    max_day = min(left.current_day, right.current_day)
    chosen = max_day if day is None else day

    if left.run_id == right.run_id:
        return Diff(
            day=chosen,
            at_tick=simtime.tick_of_day_start(max(chosen, 1)),
            max_day=max_day,
            separation=separation,
            refusal=(
                f"{left.run_id} is one timeline. A diff needs two, and a timeline against "
                "itself has no decision separating it."
            ),
        )

    if chosen < 1:
        return Diff(
            day=chosen,
            at_tick=0,
            max_day=max_day,
            separation=separation,
            refusal=f"day {chosen} is before the run began; days are numbered from 1.",
        )

    if chosen > max_day:
        behind = left if left.current_day < right.current_day else right
        return Diff(
            day=chosen,
            at_tick=simtime.tick_of_day_start(chosen),
            max_day=max_day,
            separation=separation,
            refusal=(
                f"{behind.run_id} has reached day {behind.current_day}, so there is no day "
                f"{chosen} in it to compare. This diff goes to day {max_day}."
            ),
        )

    left_side, left_state = _side(left, chosen)
    right_side, right_state = _side(right, chosen)

    return Diff(
        day=chosen,
        at_tick=simtime.tick_of_day_start(chosen),
        max_day=max_day,
        left=left_side,
        right=right_side,
        rows=_rows(left_state, right_state),
        separation=separation,
    )
