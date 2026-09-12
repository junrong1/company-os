"""Authorization: what a director needs to read another line, and what refusing it costs.

**A director reads their own reporting line and nothing else by default** (M39, R23).
`step.authorized_scope` derives that scope from folded state and `agents.bench.context.scan`
filters every read through it, so a briefing is drawn from one line's events by construction rather
than by a rule somebody remembered to apply. This module is the other half: what happens when one
line's work genuinely needs another line's knowledge. The director asks, the CEO answers, and both
answers are recorded (M40).

**The request is raised inside `step()`, and the item stops until it is answered** (M41, R24). That
stall is the whole mechanic, and it is new here: `raise_request` records a request and emits an
event, and until this unit nothing in `_advance_work` or the burn path read `state.pending` — so an
Authorization would have cost nothing at all. The work would have finished on schedule whether the
CEO granted, refused, or never looked, and "a refusal never silently succeeds" would have been a
sentence rather than a behaviour. An item whose Authorization is outstanding or refused therefore
burns nothing, and the sim-days that costs are what the report puts beside the decision (M43).

**A refusal is not final, and that is what keeps it a decision rather than a trap.** A refused item
stays stopped, the director asks again one window later, and the CEO may answer differently. What
refusing buys the CEO is exactly what granting costs them — time, in the currency this whole
simulation is denominated in — so the choice is a trade rather than a right answer with a wrong one
beside it.

**A grant is spent on the request that asked for it** (M42). The record is kept against the *item*,
so a second item needing the same line raises its own request; a granted Authorization never becomes
a standing permission, and there is no settings surface that grants one. A standing grant would
quietly delete the human from the loop the product is built on, which is the one thing this mechanic
exists to prevent.

**An abandoned request is a refusal, not a third state.** Silence is already how a player declines
everything else here, and a run holding two meanings for silence would have to explain the
difference to somebody. What the record keeps is *which* of the two it was, because the report says
so and the two read differently to a person: "I said no" and "I never looked" are the same
consequence and a different account of it.

**Nothing in this module reads the clock, opens a store, or knows about a service.** It is a record
and the rules over it, so the same code decides a live grant and a replayed one — which is what
makes "both the grant and the refusal replay from the log with no model reachable" true by
construction rather than by a second implementation agreeing with the first.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: Where an Authorization stands. A closed set, and every member is a state a surface renders.
OUTSTANDING = "outstanding"
GRANTED = "granted"
REFUSED = "refused"
STATUSES: tuple[str, ...] = (OUTSTANDING, GRANTED, REFUSED)

#: The statuses that stop the item.
#:
#: Two of the three, which is the asymmetry M41 asks for: a granted Authorization lets the work
#: continue and *everything else* — outstanding, refused, refused-by-abandonment — does not. The
#: absence of a record is not a status and does not stall: an item that needs nobody else's
#: knowledge has no Authorization to be waiting on.
STALLING: tuple[str, ...] = (OUTSTANDING, REFUSED)


@dataclass(slots=True)
class Authorization:
    """One item's request to read another line, and what the CEO did with it.

    Keyed by item in `State.authorizations`, which is what makes M42 structural rather than
    enforced: the grant lives on the item that asked, so nothing carries it to a second item, and
    "no standing permission" is a property of where the record is kept rather than a check somebody
    has to remember to write.
    """

    item_id: str
    #: The director whose line holds the work, and therefore who is asking.
    asking: str
    #: The director whose line holds the knowledge. Never equal to `asking`: an item that needs its
    #: own line's knowledge needs no Authorization, and `step.needed_line` returns nothing for one.
    needs: str
    #: The pending request this is the record of. Empty once the request has been answered — the
    #: request leaves `state.pending` at that moment, and a record naming one that is gone would
    #: read as outstanding to anything that looked.
    request_id: str
    status: str
    raised_at_tick: int
    #: When the CEO answered, or when the deadline answered for them. Zero while outstanding.
    decided_at_tick: int = 0
    #: How many times this item has asked. The second ask is a fresh request rather than a retry of
    #: a refused one (M42), and this is what lets the report say the CEO was asked twice.
    asks: int = 1
    #: Whether the refusal was the deadline's rather than the CEO's. Same consequence, different
    #: account of it, and the report prints which.
    abandoned: bool = False

    @property
    def stalls(self) -> bool:
        return self.status in STALLING

    @property
    def granted(self) -> bool:
        return self.status == GRANTED

    def ready_to_ask_again(self, tick: int, *, after: int) -> bool:
        """Whether enough of the run has passed since a refusal for the director to ask again.

        Counted in sim-ticks from the tick the refusal landed, so the second ask happens at the same
        tick on every machine — the same argument the whole pending-input contract makes for
        deadlines, and for the same reason: a wall-clock cooldown would replay faithfully and still
        make two fresh runs of one seed diverge on how fast the hardware was.

        Only a refusal comes back. An outstanding request is already the question, and a granted one
        has been answered — asking again over a live grant would be the retry loop M42 is written
        against rather than the second question it asks for.
        """
        return self.status == REFUSED and tick - self.decided_at_tick >= after

    def to_state(self) -> dict[str, Any]:
        """The hashed form. Integers and strings only; no set, no float (R6)."""
        return {
            "asking": self.asking,
            "needs": self.needs,
            "request_id": self.request_id,
            "status": self.status,
            "raised_at_tick": self.raised_at_tick,
            "decided_at_tick": self.decided_at_tick,
            "asks": self.asks,
            "abandoned": self.abandoned,
        }


def grant(record: Authorization, tick: int) -> None:
    """The CEO said yes. The item resumes on the next tick this state advances."""
    record.status = GRANTED
    record.decided_at_tick = tick
    record.request_id = ""
    record.abandoned = False


def refuse(record: Authorization, tick: int, *, abandoned: bool = False) -> None:
    """The CEO said no, or said nothing for long enough that the run answered for them."""
    record.status = REFUSED
    record.decided_at_tick = tick
    record.request_id = ""
    record.abandoned = abandoned


def to_state(records: dict[str, Authorization]) -> dict[str, Any]:
    return {item_id: record.to_state() for item_id, record in records.items()}


def from_state(item_id: str, recorded: dict[str, Any]) -> Authorization:
    """Rebuild one record from its wire form, for a snapshot restore or a fork."""
    return Authorization(
        item_id=item_id,
        asking=str(recorded["asking"]),
        needs=str(recorded["needs"]),
        request_id=str(recorded.get("request_id", "")),
        status=str(recorded["status"]),
        raised_at_tick=int(recorded["raised_at_tick"]),
        decided_at_tick=int(recorded.get("decided_at_tick", 0)),
        asks=int(recorded.get("asks", 1)),
        abandoned=bool(recorded.get("abandoned", False)),
    )
