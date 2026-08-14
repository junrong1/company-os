"""The pending-input contract: how the kernel asks another service a question.

This is the mechanism that reconciles a service split with bit-identical replay, and it is the
same shape as the stall-at-checkpoint behaviour the simulation already had — which is why the
architecture accommodates it without redesign.

**No network call happens inside `step()`** (R2). The kernel emits a request event and stalls the
affected *item*; the clock keeps running. The answer arrives later as a logged input event
carrying the tick it applies at. On replay the kernel reads that logged answer and never opens the
stream at all (R3), which is what makes replay independent of whether the services are reachable —
or of what they would say today.

**Deadlines are counted in sim-ticks and evaluated here, inside the step.** A wall-clock retry
would produce an event whose tick index depended on machine speed. That still *replays* fine — the
log is a faithful record — but it breaks seed determinism, because two fresh runs from one seed on
two machines would diverge. Counting in sim-ticks makes abandonment a property of the run rather
than of the hardware.

**Caps are enforced here too, for the same reason.** A run that raised requests without limit
would grow its log without bound; enforcing the cap inside the step makes the limit deterministic
rather than dependent on how fast answers happened to arrive.

**Every raised request names an owning item**, because the entire failure story rests on stalling
*that item* while the clock continues. The period-boundary domain consult is company-scoped and
owns no real item, so it attaches to a synthetic one — which means a late, rejected or absent
answer defers only that period's metric application, rather than either advancing the day without
it or stalling the clock.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from simcore import time as simtime

#: The synthetic item a company-scoped consult attaches to. Not a work item anybody can see: its
#: only job is to be the thing that stalls when the domain service does not answer.
SYNTHETIC_PERIOD_ITEM = "__period__"

#: Sim-ticks before an unanswered request is abandoned. One sim-day, so a service that is slow or
#: restarting has a generous window, and a service that is gone does not stall a period forever.
REQUEST_DEADLINE_TICKS = simtime.TICKS_PER_SIM_DAY

#: How many requests one item may have outstanding, and one run in total. Enforced inside the step
#: so the limit is deterministic.
MAX_OUTSTANDING_PER_ITEM = 3
MAX_OUTSTANDING_PER_RUN = 32

DOMAIN = "domain"
AGENTS = "agents"


class RequestCapExceeded(Exception):
    """The run tried to raise more requests than the cap allows.

    Deterministic by construction: the cap is checked inside the step, so the same seed hits it at
    the same tick on every machine.
    """


@dataclass(slots=True)
class PendingRequest:
    """A question asked and not yet answered. A projection of the log, never memory-only (R23)."""

    request_id: str
    service: str
    owning_item: str
    raised_at_tick: int
    deadline_tick: int
    #: Period consults only.
    period_index: int = 0

    def overdue(self, tick: int) -> bool:
        return tick >= self.deadline_tick

    def to_state(self) -> dict[str, Any]:
        return {
            "service": self.service,
            "owning_item": self.owning_item,
            "raised_at_tick": self.raised_at_tick,
            "deadline_tick": self.deadline_tick,
            "period_index": self.period_index,
        }


# =========================================================================
# Versioned bounds (R24)
# =========================================================================
#
# Bounds are part of the rules version, and they are asserted twice: before append, and again on
# read. Validating twice is not belt-and-braces — a malformed answer that reached the store becomes
# a permanent logged fact that every future fold reproduces, and the report would then present it
# with a resolvable event behind it, which makes it look *more* credible rather than less.

#: The most any single metric may move from one period consult. Wide enough for the authored
#: deltas, narrow enough that a runaway model is caught rather than applied.
METRIC_DELTA_BOUND = 60

#: Metrics a domain answer is allowed to move at all. `cash` is deliberately absent: a demand
#: model that could move cash directly would be able to fund or bankrupt the company outside the
#: economy, and the report could not reconcile it against any decision.
PERMITTED_METRIC_KEYS = ("manualHours", "leadTime", "morale", "visibility")


def validate_domain_answer(deltas: dict[str, int]) -> str:
    """Return the reason this answer is out of domain, or the empty string if it is fine.

    Rejected and logged, never clamped. Clamping would make behaviour depend on where the clamp
    sits, and would diverge live from replay if the two paths clamped differently.
    """
    if not isinstance(deltas, dict):
        return f"metric deltas must be a mapping, got {type(deltas).__name__}"

    for key, value in deltas.items():
        if key not in PERMITTED_METRIC_KEYS:
            return (
                f"{key!r} is not a metric a period consult may move; permitted: "
                f"{list(PERMITTED_METRIC_KEYS)}"
            )
        if isinstance(value, bool) or not isinstance(value, int):
            return f"the delta for {key!r} is {type(value).__name__}; deltas are integers (R6)"
        if abs(value) > METRIC_DELTA_BOUND:
            return (
                f"the delta for {key!r} is {value}, outside the bound of "
                f"+/-{METRIC_DELTA_BOUND}"
            )

    return ""


def validate_agent_answer(chosen: str, permitted: tuple[str, ...]) -> str:
    """Return the reason this resolution is out of domain, or the empty string."""
    if not isinstance(chosen, str) or not chosen:
        return "a resolution must name an option"
    if chosen not in permitted:
        return f"{chosen!r} is not one of the permitted options {list(permitted)}"
    return ""


def request_id_for(run_id: str, raised_at_seq: int) -> str:
    """Deterministic, run-scoped, and derived from the raising event's sequence (R3).

    A per-run counter would collide across a fork and let an answer intended for the parent be
    accepted by the child.
    """
    from contracts.envelope import request_id_for as derive

    return derive(run_id, raised_at_seq)


def to_state(pending: dict[str, PendingRequest]) -> dict[str, Any]:
    return {request_id: request.to_state() for request_id, request in pending.items()}
