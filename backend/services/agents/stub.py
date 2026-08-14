"""The Phase 1 agent resolver: a stub that makes no decisions.

Its entire value is proving the pending-input contract works before Phase 2 depends on it. In
Phase 1 every decision point is human-resolved, because no other resolver exists — so this exists
to exercise the boundary, not to play the game.

**It never chooses on the CEO's behalf by default.** `NEVER_ANSWERS` is the shipped behaviour: it
receives the request and declines, which drives the item to the CEO through the existing tray. That
is the Phase 1 semantics, and it means the stub being present changes nothing about how a run
plays.

**The other two modes exist to test the contract, and they are the interesting ones.** `FIRST_OPTION`
proves an agent can close a checkpoint through the same path a human uses, with no change to the
advancement contract. `OUT_OF_BOUNDS` proves the kernel rejects a malformed answer rather than
clamping it — which is the behaviour Phase 2 actually depends on, because an LLM will return
out-of-range values as a matter of course.

R37 is why the answer carries a resolver identity: a resolution never assumes human origin, so
Phase 2 is additive rather than a change to the log's contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: Shipped behaviour: decline, and let the CEO decide.
NEVER_ANSWERS = "never-answers"
#: Take the first permitted option. Proves the boundary closes a checkpoint.
FIRST_OPTION = "first-option"
#: Answer with something outside the permitted set. Proves the kernel rejects rather than clamps.
OUT_OF_BOUNDS = "out-of-bounds"

RESOLVER_KIND_STUB = "STUB"


@dataclass(slots=True)
class StubResolver:
    """A resolver whose behaviour is chosen at construction, so the contract is testable."""

    mode: str = NEVER_ANSWERS
    identity: str = "phase-1-stub"

    def resolve(self, permitted_options: tuple[str, ...]) -> dict[str, Any] | None:
        """Answer a resolution request, or return None to decline.

        Declining is not an error. An unanswered request leaves its item stalled while the clock
        continues — the same behaviour as an unresolved CEO decision — and the deadline eventually
        escalates it to the tray.
        """
        if self.mode == NEVER_ANSWERS:
            return None

        if self.mode == OUT_OF_BOUNDS:
            chosen = "an-option-nobody-offered"
        else:
            chosen = permitted_options[0] if permitted_options else ""

        return {
            "chosen_option": chosen,
            # R37: never assumes human origin. The report can then say a decision was not made by
            # anyone, rather than presenting it as though it were.
            "resolver_kind": RESOLVER_KIND_STUB,
            "resolver_identity": self.identity,
            "provenance": {"mode": self.mode},
        }
