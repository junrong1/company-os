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

**Four legs share this transport, and `service` is what tells them apart** (R1). A period consult
asks the domain service for metric effects; a resolution asks for a *choice*; a statement asks a
director for prose and citations; an Authorization asks the **CEO** whether one line may read
another's (U15). They take different answers, different validation and different application paths,
so the fold's answer dispatch has to know which one it is holding — a statement that fell through to
the resolver would resolve or escalate a checkpoint.

**The fourth leg is answered by a person, and that changes two things and nothing else.** Its answer
arrives as a *command* rather than on a service stream, because the CEO's grant is a player input
and player inputs carry idempotency keys — so `decide_authorization` applies it at the tick it is
issued at instead of queueing it for a landing tick. And its window is sized against somebody
noticing a card and deciding, which is two orders of magnitude slower than a loopback and one slower
than a provider; see `AUTHORIZATION_DEADLINE_TICKS`. Everything else it shares: the caps, the
deadline, the abandonment, and the projection that makes a restarted kernel remember what it
asked.

The discriminator is the `service` field rather than a new `kind` field beside it, and that is a
decision with a price attached. `PendingRequest.to_state()` is inside the `pending` subsystem of
the state hash, so a second field would change that shape — and R27 requires a `to_state()` shape
change to move `STATE_SHAPE_VERSION` and record a history entry. Naming the *leg* rather than the
process is the reading of `service` that makes one field enough: the bench and the resolver live in
one service today and are two legs regardless.

**A statement's window is sized against a provider API, not against a compose network.** The
shared one-sim-day deadline is five wall-seconds at the fastest clock rate the client offers, which
is the right order for a service on a loopback and the wrong one for a model call. Left shared,
the bench's dominant path at speed would be abandonment, and whether a briefing appeared at all
would be a function of the player's clock rate (R18). So a statement request carries its own
deadline, and the whole sizing argument is written out at `STATEMENT_ANSWER_SECONDS` below.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from simcore import time as simtime
from simcore.rates import TUNING

#: The synthetic item a company-scoped consult attaches to. Not a work item anybody can see: its
#: only job is to be the thing that stalls when the domain service does not answer.
SYNTHETIC_PERIOD_ITEM = "__period__"

#: Sim-ticks before an unanswered request is abandoned, for a leg that does not size its own.
#:
#: One sim-day, so a service that is slow or restarting has a generous window, and a service that is
#: gone does not stall a period forever. That is the right order for a service on a loopback and the
#: wrong one for a provider API — five wall-seconds at the fastest clock rate the client offers —
#: so the bench carries its own; see `STATEMENT_DEADLINE_TICKS`.
REQUEST_DEADLINE_TICKS = simtime.TICKS_PER_SIM_DAY

#: How many requests one item may have outstanding, and one run in total. Enforced inside the step
#: so the limit is deterministic.
MAX_OUTSTANDING_PER_ITEM = 3
MAX_OUTSTANDING_PER_RUN = 32

#: Which leg answers a request, and therefore which validation and which application path it
#: takes. See the module docstring for why this is one field rather than a (service, kind) pair.
DOMAIN = "domain"
AGENTS = "agents"
BENCH = "bench"
#: The CEO. Not a service and not a process — the *leg* is what this field names (see above), and
#: the leg that answers an Authorization is a person at a keyboard.
CEO = "ceo"

# =========================================================================
# The statement window (R17, R18)
# =========================================================================
#
# Two numbers, both derived from one wall-clock budget, because the thing being sized is a
# provider API call and a provider API call is a wall-clock quantity. The conversion is where the
# clock rate enters, and it is the fastest rate that binds: a fixed number of sim-ticks buys the
# fewest wall-seconds there, so a window that survives x3 survives x1 with three times the room.

#: The fastest clock rate the client offers. `frontend/src/ui/Shell.tsx` offers 0, 1 and 3.
#:
#: Named here rather than inlined because R18 is a sizing rule against *this* number, and a client
#: that adds x6 has to come back to this line rather than discover the consequence as briefings
#: that stopped appearing at the new speed.
FASTEST_CLIENT_RATE = 3

#: Wall-seconds the answering leg gets, measured at the fastest clock rate.
#:
#: Ten seconds, which is a whole short completion plus the store read that assembled its context
#: plus the two thread hops on either side of it — and roughly a tenth of a twenty-sim-day run at
#: x3, which is the honest cost of asking a network a question inside a simulation this fast.
#:
#: The number is a budget for the *leg*, not for the provider: R5 sends a timeout, an error, a
#: rate limit and an exhausted ceiling to the same scripted fallback, so what has to fit inside
#: this is "the leg produces an answer of some kind", and U11's provider timeout has to be
#: configured inside it rather than beside it. A leg that takes longer than this has its answer
#: refused with a reason (see `step.receive_answer`), never applied at whatever tick it happened
#: to arrive at — which is the whole of R17.
STATEMENT_ANSWER_SECONDS = 10

#: Sim-ticks between the tick that raised a statement request and the tick its answer applies at.
#:
#: Two sim-days at the shipped clock. Derived from the raising tick and nothing else (R17): the
#: live tick is where provider latency would enter, and `receive_answer` computed the landing tick
#: from it until this unit — so two fresh runs from one seed diverged on how long a model took to
#: answer, and the *state hash* diverged with them, because `pending` is a hashed subsystem and the
#: landing tick decides when a request leaves it.
#:
#: It is deliberately not a "nice" small number like one sim-hour. The offset is not a rendering
#: delay — the answer event is published to the client the moment it is durable, so the briefing
#: appears when the director actually answers — it is the window inside which an answer counts. Too
#: short and a real provider misses it at x3; too long and an item holds a cap slot for a
#: meaningful fraction of the run.
STATEMENT_OFFSET_TICKS = (
    STATEMENT_ANSWER_SECONDS * simtime.TICKS_PER_WALL_SECOND_AT_BASE_RATE * FASTEST_CLIENT_RATE
)

#: How large an answer's payload may be, encoded.
#:
#: **Checked where the answer is *appended*, not where it is validated, and the distinction is the
#: reason this exists.** A statement rides `INPUT_RECEIVED`, which is written the moment the leg
#: answers; the guard runs at the landing tick, two sim-days later. So every per-field cap in
#: `simcore.statement` decides whether a statement *stands* and none of them decides whether it
#: reaches the log — by the time the guard has an opinion, the row is a permanent fact the log
#: cannot take back. Something has to bound the bytes at the door, and this is it.
#:
#: The belt on top of caps that are already there, in the same shape as
#: `step.MAX_COMPARISON_PAYLOAD_BYTES` and for the identical reason. A full statement — two 512-char
#: fields, twelve citations and twenty-four context entries — encodes to roughly five kilobytes, so
#: this is about three times the real case. A period consult's metric deltas and a resolution's
#: option label are both two orders of magnitude under it.
MAX_ANSWER_PAYLOAD_BYTES = 16 * 1024

# =========================================================================
# The Authorization window (U15)
# =========================================================================

#: Wall-seconds the CEO gets to answer an Authorization, measured at the fastest clock rate.
#:
#: Twenty, derived the same way the bench's ten is and for the same reason: what is being sized is
#: a wall-clock quantity — somebody noticing a card on the rail, reading who is asking and what for,
#: and deciding — so the conversion happens here and the fastest rate is what binds.
#:
#: Twice the bench's, because a person is slower than a provider and because the cost of being wrong
#: is asymmetric: a statement that misses its window is one briefing, while an Authorization that
#: misses its window is a stalled item and a refusal the CEO did not make. At the shipped clock this
#: is a minute of wall time at rate 1 and twenty seconds at rate 3, and the item is stopped for all
#: of it — so a player who is not watching the rail still finds out, which is what M41 asks for.
#:
#: **Read from the tuning table rather than written here, unlike the three windows above it**, and
#: `rates.TUNING` says why: those size how long a service is given to answer the same question, and
#: this one decides how long work is stopped and when a refusal happens by default. Two runs of one
#: seed under two values produce different work, so it is part of the rules identity and moving it
#: correctly invalidates runs written under the old one.
CEO_ANSWER_SECONDS = TUNING["authorization_ceo_answer_seconds"]

#: Sim-ticks before an unanswered Authorization is abandoned, and abandonment is a refusal (M41).
#:
#: Four sim-days at the shipped clock. It does not count down while the run is paused, because
#: nothing does: a deadline in sim-ticks is a property of the run rather than of the wall clock, so
#: a player who pauses to think is not answering by timeout.
AUTHORIZATION_DEADLINE_TICKS = (
    CEO_ANSWER_SECONDS * simtime.TICKS_PER_WALL_SECOND_AT_BASE_RATE * FASTEST_CLIENT_RATE
)

#: Sim-ticks between a refusal and the director asking again (M42).
#:
#: Half the window, so a refused item asks about twice per window rather than once per tick — which
#: is the whole reason this number exists. Without it the derivation would raise a fresh request on
#: the tick after every refusal, and a CEO who said no once would be asked thirty-six times a wall
#: second by a director whose item is stopped either way.
#:
#: It is not a retry: each ask is a new request with its own id, its own deadline and its own row in
#: the report, which is the distinction M42 draws between "ask again" and "keep asking".
AUTHORIZATION_REASK_TICKS = AUTHORIZATION_DEADLINE_TICKS // 2

#: Sim-ticks before an unanswered statement request is abandoned (R18).
#:
#: Twice the window, so a request whose answer is merely late is abandoned one window after the
#: window closed — long enough that the abandonment is unambiguous rather than a race with the
#: landing tick, short enough that a leg that is simply gone does not hold a cap slot for four
#: sim-days. Four times the shared one-sim-day deadline, which is the independence R18 asks for
#: stated as an arithmetic fact rather than as an intention.
STATEMENT_DEADLINE_TICKS = 2 * STATEMENT_OFFSET_TICKS


class RequestCapExceeded(Exception):
    """The run tried to raise more requests than the cap allows.

    Deterministic by construction: the cap is checked inside the step, so the same seed hits it at
    the same tick on every machine.
    """


@dataclass(slots=True)
class PendingRequest:
    """A question asked and not yet answered. A projection of the log, never memory-only (R23)."""

    request_id: str
    #: Which leg answers this — `DOMAIN`, `AGENTS` or `BENCH`. The fold's answer dispatch reads
    #: it, which is what keeps a statement out of the resolver (R1).
    service: str
    owning_item: str
    raised_at_tick: int
    deadline_tick: int
    #: Period consults only.
    period_index: int = 0

    def overdue(self, tick: int) -> bool:
        return tick >= self.deadline_tick

    @property
    def is_statement(self) -> bool:
        """Whether this is a request for prose and citations rather than for a choice."""
        return self.service == BENCH

    @property
    def is_authorization(self) -> bool:
        """Whether this is a question for the CEO rather than for a service.

        Read by the step's abandonment, which has to mark the item's record refused, and by the
        kernel's statement dispatch, which must not carry it to the bench. Both read the leg rather
        than the shape of the payload, for the reason the module docstring gives: the leg is the one
        field that says which validation and which application path a request takes.
        """
        return self.service == CEO

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


def outstanding_for_item(
    pending: dict[str, PendingRequest], owning_item: str, service: str = ""
) -> int:
    """How many requests this item already has outstanding, optionally on one leg only.

    One rule, two readers. `step.raise_request` uses it to enforce the cap and
    `step._raise_statement_requests` uses it to *avoid* the cap — the derivation skips an item at
    its limit rather than raising inside the tick function, because `RequestCapExceeded` escaping
    `step()` is not a refusal, it is a dead clock. Two copies of the count would be two chances to
    disagree about which requests count.
    """
    return sum(
        1
        for request in pending.values()
        if request.owning_item == owning_item and (not service or request.service == service)
    )


def statement_request_id(director_id: str, owning_item: str, cp_index: int, tick: int) -> str:
    """The id of the statement request for this director, item, checkpoint and tick.

    Derived from state the fold reproduces, because the request itself is (R17): `step()` raises it
    and `step()` has no sequence number to hand — the store assigns those inside the append
    transaction. So the four facts that identify *which* briefing this is stand in for the sequence.

    **Not run-scoped, and the limitation is stated rather than argued away.** `State` carries no run
    id, so nothing derivable inside `step()` can distinguish a parent from a fork that is in the same
    state — and at the fork tick they are in the same state by construction. So a parent and a child
    standing at the same checkpoint at the same tick mint the *same* id. `_raise_period_consult`
    derives its id the same way and has the same property; what is new is that a statement's answer
    is a provider's prose rather than in-process arithmetic.

    What keeps it safe is the delivery point rather than the id: an answer names the run it is for,
    and `KernelRuntime.deliver_statement` looks it up in *that* run's `pending`. Nothing routes one
    run's answer to another, so the collision is reachable only by a caller that mixes runs by hand.
    Pre-divergence it is also harmless — the two runs are the same run, so an answer is equally
    correct for both, which is the same argument execution decision §3 makes about the prefix copy.
    Post-divergence it needs both timelines to reach the same tick with the same checkpoint open,
    which the player's own inputs decide.

    Closing it properly needs a run identifier the fold reproduces, which is a change to `State`'s
    shape and belongs with **U16**, the unit that owns fork identity (R10). Recorded in the deferred
    defect register.
    """
    return request_id_for(f"statement:{director_id}:{owning_item}:{cp_index}", tick)


def request_id_for(run_id: str, raised_at_seq: int) -> str:
    """Deterministic, run-scoped, and derived from the raising event's sequence (R3).

    A per-run counter would collide across a fork and let an answer intended for the parent be
    accepted by the child.
    """
    from contracts.envelope import request_id_for as derive

    return derive(run_id, raised_at_seq)


def to_state(pending: dict[str, PendingRequest]) -> dict[str, Any]:
    return {request_id: request.to_state() for request_id, request in pending.items()}
