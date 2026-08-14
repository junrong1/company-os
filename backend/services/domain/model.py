"""The metric and effect model, behind the origin R15 seam.

Phase 1 ships the flat authored deltas the prototype already has. There is no demand model here
and no elasticity — the numbers are constants somebody chose, which is exactly why every figure
the report presents is marked as authored tuning.

**The value of this service in Phase 1 is the seam, not the model.** Phase 4 replaces this file
with something that actually models demand, and nothing else changes: the contract is the same,
the bounds are the same, and the kernel keeps consulting it at period boundaries.

**It is also the only reason the poisoned-answer path is reachable.** In Phase 1 this computes
pure functions over integers with no state and no I/O; putting it out-of-process imports the whole
failure surface — down, slow, restarting, duplicate, orphan, malformed — for what could be a
function call. R24's bounds validation is the mitigation, and it is worth having regardless,
because Phase 2's agent producer is an LLM that will return malformed and out-of-range values as a
matter of course.
"""

from __future__ import annotations

from simcore import pending

#: What a period costs the company, in the units the metrics display.
#:
#: Authored, flat, and deliberately modest: this is the recurring drift a company experiences when
#: nobody is deciding anything — lead time creeps up, visibility decays as knowledge goes stale.
#: The CEO's decisions are what move the numbers meaningfully; this is the tide they work against.
PERIOD_DRIFT = {
    "leadTime": 1,
    "visibility": -1,
}

#: Identifies what produced an answer. The report names it, because "a flat authored table" and
#: "a demand model" deserve different amounts of trust and the reader cannot tell them apart.
MODEL_IDENTITY = "phase-1-authored-flat"


def effects_for_period(period_index: int, metrics: dict[str, int]) -> dict[str, int]:
    """What this period does to the metrics.

    Pure, integer, and independent of everything except the period index and current metrics —
    which is what makes it replayable and what makes the seam cheap to replace.

    The drift is suppressed once visibility is high: a company that can see itself does not decay
    at the same rate. That is the one conditional in the model, and it exists so the CEO's
    visibility work has a compounding payoff rather than a one-off one.
    """
    if metrics.get("visibility", 0) >= 60:
        return {"leadTime": PERIOD_DRIFT["leadTime"]}
    return dict(PERIOD_DRIFT)


def bounded_effects_for_period(period_index: int, metrics: dict[str, int]) -> dict[str, int]:
    """The answer this service will actually give, checked against the kernel's own bounds first.

    Validating on the producing side as well as the consuming side is not redundant: it means a
    bug here is caught as a bug here, rather than as a rejected answer the kernel has to log and
    escalate. The kernel still validates — it does not trust this — but a service that ships
    answers it knows are out of bounds is a worse neighbour.
    """
    proposed = effects_for_period(period_index, metrics)
    refusal = pending.validate_domain_answer(proposed)
    if refusal:
        raise ValueError(f"the authored model produced an out-of-bounds answer: {refusal}")
    return proposed
