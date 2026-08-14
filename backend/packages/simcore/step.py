"""The simulation: state, the tick function, and the commands that drive it.

Ported from script sections 4, 6 and 7 of `company-os.html` (`S`, assignment and
hand-off, `tick`, `step`, `moveCeo`).

Three ports here are changes of mechanism rather than translations, and each is forced
by replay:

**Arrival intents replace `onArrive` closures.** The prototype stores a function on the
actor: `director.onArrive = () => { ... startWork(staff, w) }`. A closure cannot be
written to a log or restored from one, so a run that resumed mid-walk would lose what
the walk was *for* — the director would arrive and simply stand there. Arrival is
therefore a declared intent (`ARRIVE_HANDOFF`, `ARRIVE_START_WORK`, ...) that the tick
function dispatches on. Same behaviour, in a form that survives a restart.

**Positions are derived from ticks, not accumulated per frame.** The prototype adds
`(dx/dist) * tilesPerSec * dt` to a float position every frame, so position depends on
frame timing. Here a walker records its path and the tick it started, and its position
is a function of `tick - path_start_tick`. Identical inputs give identical positions
regardless of how the wall clock behaved.

**The CEO is derived from logged input, not from a client-asserted position.** The
client submits the held-direction bitmask per tick; the kernel applies the identical
input at the identical tick with this same integer arithmetic. That is what makes the
client's local prediction always right, and it collapses the log's dominant write
source from roughly 36 position rows per second to one event per keypress.

**Phase order within a tick is fixed**, which is a determinism requirement rather than a
style preference: seed determinism needs a stable order as much as a stable RNG. The
order is day boundary, then work, then movement, then CEO — matching the prototype,
whose `tick(dt)` runs the rollover and work before the loop moves anyone.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from contracts.envelope import EventKind
from simcore import capacity as cap
from simcore import effects
from simcore import hiring
from simcore import items as work
from simcore import lifecycle
from simcore import morale as mor
from simcore import pending as pend
from simcore import people as roster
from simcore import time as simtime
from simcore.rates import DIRECTOR_RATE, RULES_VERSION, TUNING, apply_rates
from simcore.world import DEFAULT_COLS, DEFAULT_ROWS, Floor, find_path, plan_floor, walkable

# --- CEO movement ---------------------------------------------------------

#: The CEO's position is carried in thousandths of a tile.
MILLI = simtime.MILLI_TILES_PER_TILE

#: Derived from the prototype's `5.2` tiles per wall second: 5.2 / 36 ticks per tick is
#: 0.14444 tiles, or 144.44 milli-tiles, taken as 144.
#:
#: An integer constant rather than an exact rational, because the diagonal case has no
#: exact form: normalising by hypot means dividing by sqrt(2), which is irrational and
#: cannot be reproduced bit-for-bit in two languages. 102 is 144/sqrt(2) rounded. The
#: resulting speed differs from the prototype by about 0.3%, which is invisible, and in
#: exchange the client's prediction agrees with the kernel exactly instead of drifting a
#: fraction of a tile per second until reconciliation fires.
CEO_STRAIGHT_MILLI_PER_TICK = 144
CEO_DIAGONAL_MILLI_PER_TICK = 102

#: The prototype looks 0.3 tiles ahead so the sprite never sinks into a wall.
CEO_LOOKAHEAD_MILLI = 300

#: Held-direction bitmask, as the client submits it.
INPUT_LEFT = 1
INPUT_RIGHT = 2
INPUT_UP = 4
INPUT_DOWN = 8
INPUT_MASK = INPUT_LEFT | INPUT_RIGHT | INPUT_UP | INPUT_DOWN

# --- arrival intents ------------------------------------------------------

ARRIVE_NONE = ""
#: Sit down and start on the item already assigned.
ARRIVE_START_WORK = "start_work"
#: A director reaching the specialist's desk: hand over, then walk home.
ARRIVE_HANDOFF = "handoff"
#: A director reaching their own desk after a hand-off.
ARRIVE_SIT_IDLE = "sit_idle"
#: Reaching the meeting room for a cross-department check.
ARRIVE_ENTER_MEETING = "enter_meeting"
#: Reaching the desk again after a meeting.
ARRIVE_RESUME_WORK = "resume_work"

# --- person and item state ------------------------------------------------

STATE_IDLE = "idle"
STATE_WORKING = "working"
STATE_BLOCKED = "blocked"
STATE_WALKING = "walking"
STATE_MEETING = "meeting"

STATUS_BACKLOG = "backlog"
STATUS_ASSIGNED = "assigned"
STATUS_ACTIVE = "active"
STATUS_BLOCKED = "blocked"
STATUS_DONE = "done"

IN_FLIGHT = (STATUS_ASSIGNED, STATUS_ACTIVE, STATUS_BLOCKED)


class CommandRejected(Exception):
    """A command that mutates nothing, and says why.

    The reason is the sentence the client shows, so it reads as an explanation rather
    than a code.
    """


@dataclass(slots=True)
class PersonRuntime:
    id: str
    pos: tuple[int, int]
    seat: tuple[int, int]
    facing: str = "up"
    state: str = STATE_IDLE
    item_id: str = ""
    cp_index: int = -1
    met_ticks: int = 0
    path: tuple[tuple[int, int], ...] = ()
    path_start_tick: int = 0
    arrive: str = ARRIVE_NONE
    arrive_item: str = ""
    #: Set when work was assigned to this person around their director.
    bypassed_director: bool = False

    @property
    def rank(self) -> str:
        return roster.spec(self.id).rank

    def to_state(self) -> dict[str, Any]:
        return {
            "pos": list(self.pos),
            "facing": self.facing,
            "state": self.state,
            "item": self.item_id,
            "cp": self.cp_index,
            "met_ticks": self.met_ticks,
            "path": [list(tile) for tile in self.path],
            "path_start_tick": self.path_start_tick,
            "arrive": self.arrive,
            "arrive_item": self.arrive_item,
            "bypassed_director": self.bypassed_director,
        }


@dataclass(slots=True)
class Decision:
    """What the CEO settled, and by which route.

    `tacit` is populated only for an in-person resolution. That asymmetry is the
    product's central claim, so it lives in state rather than in presentation.
    """

    label: str
    choice: str
    note: str
    in_person: bool
    tacit: str
    at_tick: int
    #: Directors who were not informed, because the work bypassed them.
    uninformed: tuple[str, ...] = ()


@dataclass(slots=True)
class ItemRuntime:
    id: str
    done_units: int = 0
    status: str = STATUS_BACKLOG
    assignee: str = ""
    visited: bool = False
    resolved: list[bool] = field(default_factory=list)
    decisions: list[Decision] = field(default_factory=list)

    def to_state(self) -> dict[str, Any]:
        return {
            "done_units": self.done_units,
            "status": self.status,
            "assignee": self.assignee,
            "visited": self.visited,
            "resolved": list(self.resolved),
            "decisions": [
                {
                    "label": d.label,
                    "choice": d.choice,
                    "note": d.note,
                    "in_person": d.in_person,
                    "tacit": d.tacit,
                    "at_tick": d.at_tick,
                    "uninformed": list(d.uninformed),
                }
                for d in self.decisions
            ],
        }


@dataclass(slots=True)
class CeoRuntime:
    x_milli: int
    y_milli: int
    facing: str = "down"

    @property
    def tile(self) -> tuple[int, int]:
        return (to_tile(self.x_milli), to_tile(self.y_milli))

    def to_state(self) -> dict[str, Any]:
        return {"x_milli": self.x_milli, "y_milli": self.y_milli, "facing": self.facing}


@dataclass(slots=True)
class Deliverable:
    item_id: str
    title: str
    kind: str
    dept: str
    day: int
    provenance: tuple[str, ...]
    tacit: tuple[str, ...]

    def to_state(self) -> dict[str, Any]:
        return {
            "item": self.item_id,
            "title": self.title,
            "kind": self.kind,
            "dept": self.dept,
            "day": self.day,
            "provenance": list(self.provenance),
            "tacit": list(self.tacit),
        }


@dataclass(slots=True)
class Emitted:
    """One event a step or a command produced.

    Sequence numbers are deliberately absent: the kernel service assigns them inside
    the append transaction (R37), and simcore has no business knowing about them.
    """

    kind: EventKind
    payload: dict[str, Any]
    request_id: str = ""
    #: Gateway-minted, carried onto every event a command produces. The causation trace that
    #: makes one identifier greppable across seven service streams.
    command_id: str = ""


@dataclass(slots=True)
class State:
    """Everything a run is. Integers throughout; no float, no set."""

    run_seed: int
    tick: int
    floor: Floor
    seats: dict[str, tuple[int, int]]
    metrics: dict[str, int]
    people: dict[str, PersonRuntime]
    items: dict[str, ItemRuntime]
    ceo: CeoRuntime
    outputs: list[Deliverable] = field(default_factory=list)
    #: CEO input bitmasks by the tick they apply at.
    ceo_inputs: dict[int, int] = field(default_factory=dict)
    #: Per reporting line. Baseline draw, and the load it produces (U7).
    capacity: dict[str, cap.DepartmentCapacity] = field(default_factory=dict)
    #: Per person. The company `morale` metric is this aggregated (R56).
    morale: dict[str, mor.PersonMorale] = field(default_factory=dict)
    #: Requested and arrived hires, keyed by request id.
    hires: dict[str, hiring.Hire] = field(default_factory=dict)
    #: Work items created at runtime — hiring items. Authored items live in `items.ITEMS`.
    dynamic_items: dict[str, work.ItemSpec] = field(default_factory=dict)
    #: People who have left. Kept rather than deleted so the log stays interpretable.
    departed: list[str] = field(default_factory=list)
    #: Chosen at genesis, immutable, inherited by forks (U8).
    horizon_tick: int = 0
    #: Set when the run ends. Nothing advances afterwards.
    terminal_reason: str = ""
    terminal_tick: int = 0
    #: Raised-but-unanswered requests, keyed by request id. A projection of the log (R23).
    pending: dict[str, pend.PendingRequest] = field(default_factory=dict)
    #: The last period the domain service was consulted for, so one period asks once.
    last_period_consulted: int = 0
    #: Answers that arrived and are waiting for the tick they apply at.
    queued_answers: dict[int, list[dict[str, Any]]] = field(default_factory=dict)

    def spec_of(self, item_id: str) -> work.ItemSpec:
        """An item's spec, authored or created at runtime."""
        if item_id in self.dynamic_items:
            return self.dynamic_items[item_id]
        return work.spec(item_id)

    def line_of(self, person_id: str) -> str:
        return roster.reporting_line_of(person_id)

    def present_members(self, director_id: str) -> tuple[str, ...]:
        """Everyone still in this line, the director included and departures excluded."""
        members = [
            person_id
            for person_id in cap.members_of(director_id)
            if person_id not in self.departed
        ]
        members.extend(
            hire.person_id
            for hire in self.hires.values()
            if hire.status == "arrived" and hire.director_id == director_id
        )
        return tuple(members)

    def queued_units(self, director_id: str) -> int:
        """Remaining effort on this line's in-flight items, for the load measure (R20)."""
        total = 0
        for item in self.items.values():
            if item.status not in IN_FLIGHT or not item.assignee:
                continue
            if self.line_of_assignee(item.assignee) != director_id:
                continue
            total += max(0, self.spec_of(item.id).effort_units - item.done_units)
        return total

    def line_of_assignee(self, person_id: str) -> str:
        for hire in self.hires.values():
            if hire.person_id == person_id:
                return hire.director_id
        return roster.reporting_line_of(person_id)

    def person(self, person_id: str) -> PersonRuntime:
        try:
            return self.people[person_id]
        except KeyError:
            raise CommandRejected(f"no such person: {person_id}") from None

    def item(self, item_id: str) -> ItemRuntime:
        try:
            return self.items[item_id]
        except KeyError:
            raise CommandRejected(f"no such work item: {item_id}") from None


def to_tile(milli: int) -> int:
    """Round a milli-tile coordinate to the nearest tile, without float."""
    return (milli + MILLI // 2) // MILLI


# =========================================================================
# Genesis
# =========================================================================


def new_run(
    run_seed: int,
    cols: int = DEFAULT_COLS,
    rows: int = DEFAULT_ROWS,
    horizon_tick: int | None = None,
) -> tuple[State, list[Emitted]]:
    """Create a run, and the genesis event recording what it was created with.

    Geometry is computed here, once, and recorded. Nothing downstream re-derives it
    from a viewport.
    """
    floor = plan_floor(cols, rows)
    seats = roster.assign_seats(floor)

    state = State(
        run_seed=run_seed,
        tick=0,
        floor=floor,
        seats=seats,
        metrics=effects.initial_metrics(),
        people={
            person.id: PersonRuntime(id=person.id, pos=seats[person.id], seat=seats[person.id])
            for person in roster.PEOPLE
        },
        items={
            item.id: ItemRuntime(id=item.id, resolved=[False] * len(item.checkpoints))
            for item in work.ITEMS
        },
        ceo=CeoRuntime(x_milli=floor.spawn[0] * MILLI, y_milli=floor.spawn[1] * MILLI),
        capacity=cap.new_capacity(),
        morale=mor.new_morale(),
        horizon_tick=(
            lifecycle.default_horizon_tick() if horizon_tick is None else horizon_tick
        ),
    )
    _refresh_load(state)

    genesis = Emitted(
        kind=EventKind.GENESIS,
        payload={
            "run_seed": run_seed,
            "quantum_sim_seconds": simtime.QUANTUM_SIM_SECONDS,
            "grid": [cols, rows],
            "horizon_tick": state.horizon_tick,
            "decision_supply": lifecycle.decision_supply(),
            "rules_ver": RULES_VERSION,
            "metrics": effects.initial_metrics(),
            "draws": dict(cap.DEPARTMENT_DRAWS),
            "floor": floor.to_state(),
            "roster": roster.roster_to_state(seats),
            "items": [item.id for item in work.ITEMS],
            # The authored work graph. Ids alone were enough while nothing read the
            # dependency edges; U14's DAG assigns layers by longest-path depth over the
            # whole graph, so it needs the edges before anything unlocks.
            "catalog": work.catalog_to_state(),
            # The metric table, `good` included. The HUD renders each metric against its
            # own favourable direction, and deriving that client-side would make cutting
            # manual hours read as a regression.
            "metric_defs": effects.metric_defs_to_state(),
            # What the load ramp's domain is. The HUD's per-department capacity heat and
            # the DAG's node load tint are asserted to agree, so the scale they read
            # against comes from the kernel rather than being written down twice.
            "load": {"scale": cap.LOAD_SCALE, "ceiling": cap.LOAD_CEILING},
        },
    )
    return state, [genesis]


# =========================================================================
# The tick
# =========================================================================


def step(state: State) -> list[Emitted]:
    """Advance one quantum. Deterministic, and free of I/O by construction.

    No network call happens here (R2). Anything the kernel needs from another service
    arrives as a logged input event carrying the tick it applies at, which is why this
    function replays without the services being reachable.
    """
    events: list[Emitted] = []
    if state.terminal_reason:
        # Nothing appends after a terminal event, and nothing advances either.
        return events
    state.tick += 1

    # Phase 1 — the day boundary.
    if simtime.is_day_boundary(state.tick):
        events.extend(_roll_over_day(state))

    # Phase 2 — the baseline draw, which consumes capacity whether or not the CEO assigned
    # anything (R47). Deliberately its own phase rather than a side effect of someone
    # working: a department with no project still has its recurring workload, and consuming
    # it only when somebody happened to be at their desk would make the load signal a
    # function of assignment rather than of the department.
    _consume_baseline_draw(state)

    # Phase 3 — work, in roster order.
    for person_id in list(state.people):
        events.extend(_advance_work(state, state.people[person_id]))

    # Phase 4 — movement, and the arrival intents it fires.
    for person_id in list(state.people):
        events.extend(_advance_walker(state, state.people[person_id]))

    # Phase 5 — the CEO's logged input for this tick.
    _advance_ceo(state)

    # Phase 6 — the pending-input contract: apply answers that land on this tick, raise the
    # period consult at a period boundary, and abandon anything past its deadline. All three
    # inside the step, so both the deadline and the cap are properties of the run rather than of
    # the machine.
    events.extend(_apply_queued_answers(state))
    events.extend(_raise_period_consult(state))
    events.extend(_abandon_overdue_requests(state))

    # Phase 7 — has the run ended? Evaluated here, at the boundary of the quantum that just
    # completed, so the crossing quantum finishes in full and termination is its last event.
    events.extend(_check_termination(state))

    return events


# =========================================================================
# The pending-input contract
# =========================================================================


def raise_request(
    state: State,
    service: str,
    owning_item: str,
    request_id: str,
    period_index: int = 0,
) -> list[Emitted]:
    """Emit a request and stall the owning item. The clock keeps running.

    No network call happens here (R2). The kernel records that it asked; the transport is the
    caller's business, and on replay the caller never runs.
    """
    per_item = sum(
        1 for request in state.pending.values() if request.owning_item == owning_item
    )
    if per_item >= pend.MAX_OUTSTANDING_PER_ITEM:
        raise pend.RequestCapExceeded(
            f"{owning_item} already has {per_item} outstanding requests, at the cap of "
            f"{pend.MAX_OUTSTANDING_PER_ITEM}. Enforced inside the step, so this happens at the "
            "same tick on every machine."
        )
    if len(state.pending) >= pend.MAX_OUTSTANDING_PER_RUN:
        raise pend.RequestCapExceeded(
            f"the run has {len(state.pending)} outstanding requests, at the cap of "
            f"{pend.MAX_OUTSTANDING_PER_RUN}"
        )

    request = pend.PendingRequest(
        request_id=request_id,
        service=service,
        owning_item=owning_item,
        raised_at_tick=state.tick,
        deadline_tick=state.tick + pend.REQUEST_DEADLINE_TICKS,
        period_index=period_index,
    )
    state.pending[request_id] = request

    return [
        Emitted(
            kind=EventKind.REQUEST_RAISED,
            payload={
                "tick": state.tick,
                "service": service,
                "owning_item": owning_item,
                "deadline_tick": request.deadline_tick,
                "period_index": period_index,
            },
            request_id=request_id,
        )
    ]


def receive_answer(
    state: State, request_id: str, answer: dict[str, Any], at_tick: int | None = None
) -> list[Emitted]:
    """Queue an answer for the tick it applies at.

    Only the tick loop appends (R22), so an answer arriving on a stream is enqueued here and
    applied at a tick boundary rather than written on the spot. That is what makes sole-writer
    true at the transaction level rather than merely at the process level.
    """
    request = state.pending.get(request_id)
    if request is None:
        # Either already answered, or never asked. Either way it is a duplicate, and the
        # duplicate is rejected rather than applied a second time (R25).
        return [
            Emitted(
                kind=EventKind.ANSWER_REJECTED,
                payload={
                    "tick": state.tick,
                    "reason": (
                        "no outstanding request with this id; it was already answered, "
                        "abandoned, or never raised"
                    ),
                    "owning_item": "",
                },
                request_id=request_id,
            )
        ]

    applies_at = max(state.tick + 1, at_tick or state.tick + 1)
    state.queued_answers.setdefault(applies_at, []).append(
        {"request_id": request_id, "answer": answer}
    )

    # The answer itself is logged, carrying the tick it applies at. This is the event replay
    # reads instead of re-issuing the call (R3) — so the content has to be here, not merely the
    # fact that something answered.
    return [
        Emitted(
            kind=EventKind.INPUT_RECEIVED,
            payload={
                "tick": applies_at,
                "submitted_at_tick": state.tick,
                "service": request.service,
                "owning_item": request.owning_item,
                "period_index": request.period_index,
                "answer": dict(answer),
            },
            request_id=request_id,
        )
    ]


def _apply_queued_answers(state: State) -> list[Emitted]:
    """Apply answers whose tick has arrived, validating each before it becomes a logged fact."""
    events: list[Emitted] = []

    for queued in state.queued_answers.pop(state.tick, []):
        request_id = queued["request_id"]
        answer = queued["answer"]
        request = state.pending.get(request_id)
        if request is None:
            continue

        if request.service == pend.DOMAIN:
            events.extend(_apply_domain_answer(state, request, answer))
        else:
            events.extend(_apply_agent_answer(state, request, answer))

    return events


def _apply_domain_answer(
    state: State, request: pend.PendingRequest, answer: dict[str, Any]
) -> list[Emitted]:
    deltas = answer.get("metric_deltas", {})
    refusal = pend.validate_domain_answer(deltas)

    if refusal:
        # Rejected and logged, never clamped. The period's metric application is simply
        # deferred: the clock does not stall and the day does not advance without saying so.
        del state.pending[request.request_id]
        return [
            Emitted(
                kind=EventKind.ANSWER_REJECTED,
                payload={
                    "tick": state.tick,
                    "reason": refusal,
                    "owning_item": request.owning_item,
                    "service": request.service,
                    "deferred_period": request.period_index,
                },
                request_id=request.request_id,
            )
        ]

    del state.pending[request.request_id]
    _, effective = effects.apply_effect(state.metrics, dict(deltas))
    _refresh_load(state)

    return [
        Emitted(
            kind=EventKind.METRICS_APPLIED,
            payload={
                "tick": state.tick,
                "period_index": request.period_index,
                "deltas": effective,
                "metrics": dict(state.metrics),
            },
            request_id=request.request_id,
        ),
    ]


def _apply_agent_answer(
    state: State, request: pend.PendingRequest, answer: dict[str, Any]
) -> list[Emitted]:
    item = state.items.get(request.owning_item)
    if item is None or item.status != STATUS_BLOCKED:
        del state.pending[request.request_id]
        return [
            Emitted(
                kind=EventKind.ANSWER_REJECTED,
                payload={
                    "tick": state.tick,
                    "reason": (
                        "the item is no longer waiting on a decision — it was resolved, "
                        "reassigned, or its assignee was removed by attrition"
                    ),
                    "owning_item": request.owning_item,
                    "service": request.service,
                },
                request_id=request.request_id,
            )
        ]

    spec = state.spec_of(item.id)
    cp_index = state.people[item.assignee].cp_index
    checkpoint = spec.checkpoints[cp_index]
    permitted = tuple(option.label for option in checkpoint.options)

    chosen = answer.get("chosen_option", "")
    refusal = pend.validate_agent_answer(chosen, permitted)

    if refusal:
        # Escalated to the CEO through the existing tray rather than stalled indefinitely: the
        # item stays blocked, which is exactly what "the CEO has to decide this" looks like.
        del state.pending[request.request_id]
        return [
            Emitted(
                kind=EventKind.ANSWER_REJECTED,
                payload={
                    "tick": state.tick,
                    "reason": refusal,
                    "owning_item": request.owning_item,
                    "service": request.service,
                    "escalated_to_ceo": True,
                },
                request_id=request.request_id,
            )
        ]

    del state.pending[request.request_id]
    option_index = permitted.index(chosen)

    # Resolved through the same contract a human uses — no change to the advancement contract,
    # which is what lets Phase 2 substitute an LLM resolver without touching this path.
    return resolve_checkpoint(state, item.id, cp_index, option_index, in_person=False)


def _raise_period_consult(state: State) -> list[Emitted]:
    """Ask the domain service for this period's metric effects.

    At a period boundary, not per tick: demand modelling is naturally periodic, and a per-tick
    round trip would both stall the loop and flood the log.
    """
    if not simtime.is_day_boundary(state.tick) or state.tick == 0:
        return []

    period = simtime.day_of(state.tick)
    if period <= state.last_period_consulted:
        return []
    state.last_period_consulted = period

    # Derived from the tick rather than from a sequence, because simcore does not know
    # sequences; the kernel service rewrites it to the sequence-derived form when it appends.
    request_id = pend.request_id_for(f"period:{period}", state.tick)

    try:
        return raise_request(
            state,
            service=pend.DOMAIN,
            owning_item=pend.SYNTHETIC_PERIOD_ITEM,
            request_id=request_id,
            period_index=period,
        )
    except pend.RequestCapExceeded:
        # The cap is deterministic, so this is a run that has genuinely stopped being answered.
        # Deferring the period is the documented behaviour; the clock does not stall.
        return []


def _abandon_overdue_requests(state: State) -> list[Emitted]:
    """Abandon anything past its sim-tick deadline.

    Counted in sim-ticks, so abandonment happens at the same tick on every machine. A wall-clock
    deadline would replay fine and still break seed determinism.
    """
    events: list[Emitted] = []

    for request_id in [
        request_id
        for request_id, request in state.pending.items()
        if request.overdue(state.tick)
    ]:
        request = state.pending.pop(request_id)
        escalated = request.owning_item != pend.SYNTHETIC_PERIOD_ITEM
        events.append(
            Emitted(
                kind=EventKind.ANSWER_REJECTED,
                payload={
                    "tick": state.tick,
                    "reason": (
                        f"no answer within {pend.REQUEST_DEADLINE_TICKS} ticks of being raised"
                    ),
                    "owning_item": request.owning_item,
                    "service": request.service,
                    "abandoned": True,
                    "escalated_to_ceo": escalated,
                    "deferred_period": request.period_index,
                },
                request_id=request_id,
            )
        )

    return events


def _check_termination(state: State) -> list[Emitted]:
    if state.terminal_reason:
        return []

    ending = lifecycle.check(state.tick, state.metrics["cash"], state.horizon_tick)
    if ending is None:
        return []

    state.terminal_reason = ending.reason
    state.terminal_tick = ending.tick

    return [
        Emitted(
            kind=EventKind.RUN_TERMINATED,
            payload={
                "tick": ending.tick,
                "day": ending.day,
                "reason": ending.reason,
                "detail": ending.detail,
                "metrics": dict(state.metrics),
                "decisions_taken": sum(
                    len(item.decisions) for item in state.items.values()
                ),
                "decision_supply": lifecycle.decision_supply(),
                "deliverables": len(state.outputs),
            },
        )
    ]


def _consume_baseline_draw(state: State) -> None:
    """Draw down every department's recurring workload for this tick."""
    for director, department in state.capacity.items():
        headcount = len(state.present_members(director))
        if headcount <= 0:
            continue
        per_tick = (
            cap.per_member_units(department, headcount)
            * headcount
            // simtime.TICKS_PER_SIM_DAY
        )
        cap.consume(department, per_tick)


def _roll_over_day(state: State) -> list[Emitted]:
    """The day boundary: costs, draw refresh, morale feedback and attrition.

    Order is fixed and matters. Costs first, because they are a fact about the day that
    ended. Then the draw expires and reissues, so the load signal describes the new day.
    Then morale rolls, because its counters are in business days. Attrition last, because it
    reads the counters morale just updated.
    """
    events: list[Emitted] = []

    # --- costs. A recurring draw is staffed work and carries into the burn (R60), and each
    # arrived hire adds a recurring salary (R24). Automating work therefore reduces the
    # burn, which is what stops returning work to the backlog from strictly dominating.
    fixed = TUNING["fixed_cost_per_day"]
    draw_cost = cap.manual_hours(state.capacity) * TUNING["draw_cost_per_monthly_hour"] // 100
    salaries = hiring.salary_total(state.hires)
    total = fixed + draw_cost + salaries

    _, effective = effects.apply_effect(state.metrics, {"cash": -total})

    # --- the draw expires and reissues (R47).
    expired = cap.expire_and_refresh(state.capacity)

    events.append(
        Emitted(
            kind=EventKind.DAILY_COSTS_APPLIED,
            payload={
                "tick": state.tick,
                "day": simtime.day_of(state.tick),
                "fixed_cost": fixed,
                "draw_cost": draw_cost,
                "salaries": salaries,
                "total_cost": total,
                "expired_draw_units": expired,
                "deltas": effective,
                "metrics": dict(state.metrics),
            },
        )
    )

    # --- morale feedback. Over-ceiling departments cost their own members morale, so the
    # signal points at the department that is actually overloaded (R56).
    for director, department in state.capacity.items():
        if department.load_permille <= cap.LOAD_CEILING:
            continue
        for person_id in state.present_members(director):
            if person_id in state.morale:
                mor.apply_person_delta(
                    state.morale, person_id, -mor.OVER_CEILING_MORALE_PER_DAY
                )

    counters = mor.roll_day(state.morale)
    state.metrics["morale"] = mor.company_morale(state.morale)

    if counters:
        events.append(
            Emitted(
                kind=EventKind.LOAD_CHANGED,
                payload={
                    "tick": state.tick,
                    "day": simtime.day_of(state.tick),
                    "load": {
                        director: department.load_permille
                        for director, department in state.capacity.items()
                    },
                    "days_below_threshold": counters,
                    "morale": mor.company_morale(state.morale),
                },
            )
        )

    # --- attrition. Load can now rise with no CEO action, which is the point (R38).
    for director in list(state.capacity):
        leaving = mor.attrition_candidate(
            state.morale, director, set(state.present_members(director))
        )
        if leaving is None:
            continue
        events.extend(_depart(state, leaving, director))

    _refresh_load(state)
    return events


def _depart(state: State, person_id: str, director_id: str) -> list[Emitted]:
    """Remove someone, returning whatever they were holding to the backlog."""
    state.departed.append(person_id)
    person = state.people.get(person_id)

    returned = ""
    if person is not None and person.item_id:
        item = state.items[person.item_id]
        item.status = STATUS_BACKLOG
        item.assignee = ""
        returned = item.id
        person.item_id = ""
        person.state = STATE_IDLE

    return [
        Emitted(
            kind=EventKind.ATTRITION,
            payload={
                "tick": state.tick,
                "person": person_id,
                "director": director_id,
                "morale": state.morale[person_id].value,
                "days_below_threshold": state.morale[person_id].days_below,
                "returned_item": returned,
                # The status the returned item landed in, so a read-side consumer moves its
                # node from this event rather than inferring what attrition does to work.
                #
                # Named for the field it describes rather than reusing `item_status`: every other
                # kind that carries `item_status` also carries `item`, and this one carries
                # `returned_item`. A consumer generalising the six-kind convention would read this
                # as the status of an item that is not in the payload.
                "returned_item_status": STATUS_BACKLOG if returned else "",
                "remaining_in_line": len(state.present_members(director_id)),
            },
        )
    ]


def _refresh_load(state: State) -> None:
    """Recompute each department's load, and the metrics derived from capacity."""
    for director, department in state.capacity.items():
        headcount = len(state.present_members(director))
        department.load_permille = cap.load_permille(
            department, state.queued_units(director), headcount
        )

    # R49: the metric is the sum of the draws, never accumulated separately.
    state.metrics["manualHours"] = cap.manual_hours(state.capacity)
    state.metrics["morale"] = mor.company_morale(state.morale)
    effects.clamp(state.metrics)


def _advance_work(state: State, person: PersonRuntime) -> list[Emitted]:
    if person.state == STATE_MEETING:
        person.met_ticks += 1
        if person.met_ticks >= work.MEETING_SIM_HOURS * simtime.TICKS_PER_SIM_HOUR:
            person.met_ticks = 0
            _walk_to(state, person, person.seat, ARRIVE_RESUME_WORK)
        return []

    if person.state != STATE_WORKING or not person.item_id:
        return []

    item = state.items[person.item_id]
    spec = state.spec_of(person.item_id)
    if item.status == STATUS_BLOCKED:
        return []

    item.done_units += _burn_this_tick(state, person, item)
    total = spec.effort_units

    # Cross-department work needs a meeting before it can go further.
    if spec.visit_meeting and not item.visited and work.visit_meeting_due(item.done_units, total):
        item.visited = True
        meeting = state.floor.room("meeting")
        destination = meeting.visit or (meeting.x1 + 1, meeting.y2)
        _walk_to(state, person, destination, ARRIVE_ENTER_MEETING)
        return []

    next_cp = next(
        (
            index
            for index, checkpoint in enumerate(spec.checkpoints)
            if not item.resolved[index]
            and work.checkpoint_reached(item.done_units, total, checkpoint.at_percent)
        ),
        None,
    )
    if next_cp is not None:
        return _block(state, person, item, next_cp)

    if item.done_units >= total:
        return _complete(state, person, item)

    return []


def _burn_this_tick(state: State, person: PersonRuntime, item: ItemRuntime) -> int:
    """How much effort this person puts into their head item this tick.

    Two stages, in this order, and the order is R47's:

    **The baseline draw comes off available hours first.** A department's recurring workload
    is not a separate display — it consumes the same hours the queue wants, so it is deducted
    before queue effort burns. That is what makes the ceiling bite without blocking anything.

    **Then the multiplier chain, composed once and floored once.** Over-ceiling degradation
    and morale degradation join the director rate in the slot order `rates.py` pins. The
    floor is what stops a demoralised, overloaded person from making no progress at all — and
    hiring items are exempt from the morale term entirely (R57), because the lever that fixes
    overload must not be throttled by the overload.
    """
    director = state.line_of_assignee(person.id)
    department = state.capacity.get(director)

    available = work.EFFORT_UNITS_PER_TICK
    if department is not None:
        headcount = len(state.present_members(director))
        # The share is *computed* here, not consumed: the department's draw is drawn down
        # once per tick in its own phase, regardless of who is working. This is the part of
        # it that comes off this person's available hours.
        share = cap.per_member_units(department, headcount) // simtime.TICKS_PER_SIM_DAY
        available = max(0, available - share)

    multipliers = {}
    if person.rank == "director":
        multipliers["director_rate"] = DIRECTOR_RATE
    if department is not None:
        multipliers["over_ceiling_degradation"] = cap.over_ceiling_multiplier(
            department.load_permille
        )
    is_hiring_item = item.id in state.dynamic_items
    if not is_hiring_item:
        multipliers["morale_degradation"] = mor.degrade_multiplier(state.morale, person.id)

    return apply_rates(available, multipliers, floor=cap.OVER_CEILING_FLOOR)


def _block(
    state: State, person: PersonRuntime, item: ItemRuntime, cp_index: int
) -> list[Emitted]:
    """Work stops. The clock keeps running; only this item is stalled."""
    checkpoint = state.spec_of(item.id).checkpoints[cp_index]

    person.state = STATE_BLOCKED
    person.cp_index = cp_index
    item.status = STATUS_BLOCKED

    return [
        Emitted(
            kind=EventKind.CHECKPOINT_RAISED,
            payload={
                "tick": state.tick,
                "item": item.id,
                "person": person.id,
                "cp_index": cp_index,
                "kind": checkpoint.kind,
                "label": checkpoint.label,
                "at_percent": checkpoint.at_percent,
                "done_units": item.done_units,
                "item_status": item.status,
                # The line this person says only in person. Carried here rather than in the
                # genesis catalog on purpose: shipping every tacit line at genesis would put
                # the whole script on the wire before anyone had stopped at anything, whereas
                # this arrives exactly when someone *is* stopped, holding it.
                #
                # That the tray never shows it stays a client-side guarantee, which is the
                # known hole this phase accepts: the kernel cannot tell where the CEO is
                # standing when it decides what to send.
                "tacit": checkpoint.tacit,
            },
        )
    ]


def _complete(state: State, person: PersonRuntime, item: ItemRuntime) -> list[Emitted]:
    spec = state.spec_of(item.id)
    item.status = STATUS_DONE
    _, effective = effects.apply_effect(state.metrics, spec.effect)

    stops = len(spec.checkpoints)
    provenance = [
        f"{roster.spec(person.id).name} — {spec.effort_hours} hours of work, "
        f"{stops} decision stop{'' if stops == 1 else 's'}",
        *(
            f"CEO decision: {d.choice} ({'in person' if d.in_person else 'from the tray'})"
            for d in item.decisions
        ),
        *(
            "One piece of tacit knowledge, from talking in person"
            for d in item.decisions
            if d.tacit
        ),
    ]

    deliverable = Deliverable(
        item_id=item.id,
        title=spec.output_title,
        kind=spec.output_kind,
        dept=spec.dept,
        day=simtime.day_of(state.tick),
        provenance=tuple(provenance),
        tacit=tuple(d.tacit for d in item.decisions if d.tacit),
    )
    state.outputs.insert(0, deliverable)

    person.state = STATE_IDLE
    person.item_id = ""

    events = [
        Emitted(
            kind=EventKind.DELIVERABLE_PRODUCED,
            payload={
                "tick": state.tick,
                "item": item.id,
                "person": person.id,
                "deliverable": deliverable.to_state(),
                "deltas": effective,
                "metrics": dict(state.metrics),
                "final": spec.final,
                "item_status": item.status,
                # Carried so a read-side consumer's progress readout lands on complete rather
                # than freezing at whatever the last checkpoint reported. `done_units` otherwise
                # only ever appears on CHECKPOINT_RAISED, so a delivered item would render at its
                # final checkpoint's percentage forever.
                "done_units": item.done_units,
            },
        )
    ]

    # A finished hiring item seats the arrival, or refuses with a reason (R25).
    events.extend(_complete_hire(state, item.id))
    _refresh_load(state)

    # What this unlocks directly, plus anything the visibility gain just released.
    for unlocked_id in _newly_available(state):
        events.append(
            Emitted(
                kind=EventKind.ITEM_UNLOCKED,
                payload={"tick": state.tick, "item": unlocked_id, "because": item.id},
            )
        )

    return events


def _newly_available(state: State) -> list[str]:
    """Backlog items whose gates are now clear, in item order."""
    return [
        item.id
        for item in work.ITEMS
        if state.items[item.id].status == STATUS_BACKLOG
        and item.requires != work.Requires()
        and is_unlocked(state, item.id)
    ]


def _advance_walker(state: State, person: PersonRuntime) -> list[Emitted]:
    """Move a walker, and fire its arrival intent when the path runs out.

    Position is a function of the tick index, so this is a lookup rather than an
    accumulation.
    """
    if person.state != STATE_WALKING:
        return []

    if not person.path:
        return _arrive(state, person)

    elapsed = state.tick - person.path_start_tick
    covered = simtime.tiles_progressed(elapsed)

    if covered >= len(person.path):
        person.facing = _facing(person.pos, person.path[-1]) or person.facing
        person.pos = person.path[-1]
        person.path = ()
        return _arrive(state, person)

    if covered >= 1:
        destination = person.path[covered - 1]
        person.facing = _facing(person.pos, destination) or person.facing
        person.pos = destination

    return []


def _facing(origin: tuple[int, int], destination: tuple[int, int]) -> str:
    dx = destination[0] - origin[0]
    dy = destination[1] - origin[1]
    if dx == 0 and dy == 0:
        return ""
    if abs(dx) > abs(dy):
        return "right" if dx > 0 else "left"
    return "down" if dy > 0 else "up"


def _walk_to(
    state: State, person: PersonRuntime, destination: tuple[int, int], intent: str
) -> None:
    person.path = tuple(find_path(state.floor, person.pos, destination))
    person.path_start_tick = state.tick
    person.arrive = intent
    person.state = STATE_WALKING


def _arrive(state: State, person: PersonRuntime) -> list[Emitted]:
    intent = person.arrive
    person.arrive = ARRIVE_NONE

    if intent in (ARRIVE_START_WORK, ARRIVE_RESUME_WORK):
        person.state = STATE_WORKING
        return []

    if intent == ARRIVE_ENTER_MEETING:
        person.state = STATE_MEETING
        person.met_ticks = 0
        return []

    if intent == ARRIVE_SIT_IDLE:
        person.state = STATE_IDLE
        return []

    if intent == ARRIVE_HANDOFF:
        item_id = person.arrive_item
        person.arrive_item = ""
        person.item_id = ""
        item = state.items[item_id]
        staff = state.people[item.assignee]

        # The director walks home; the specialist starts.
        _walk_to(state, person, person.seat, ARRIVE_SIT_IDLE)

        events = [
            Emitted(
                kind=EventKind.WORK_ASSIGNED,
                payload={
                    "tick": state.tick,
                    "item": item_id,
                    "person": staff.id,
                    "via": person.id,
                    "handoff_completed": True,
                },
            )
        ]
        _start_work(state, staff, item)
        # Stamped after the work starts, not before: at the point the payload was built the
        # item was still `assigned`, and a read-side consumer told that would move its node
        # to a status the item left in the same operation, with no later event to correct it.
        events[0].payload["item_status"] = item.status
        return events

    person.state = STATE_IDLE
    return []


def _start_work(state: State, person: PersonRuntime, item: ItemRuntime) -> None:
    person.item_id = item.id
    item.status = STATUS_ACTIVE

    if person.pos != person.seat:
        _walk_to(state, person, person.seat, ARRIVE_START_WORK)
    else:
        person.state = STATE_WORKING


def _held_bitmask(state: State) -> int:
    """The direction being held this tick: the most recent input at or before it.

    An input *stands* until another supersedes it, which is what "held" means. The client
    sends one command per change of held direction — walking is run-length encoded, so a
    second of movement is one row rather than thirty-six — and reading only the exact tick
    would move the CEO a seventh of a tile per keypress and then stop dead.

    Superseded entries are dropped as they are passed. Ticks advance monotonically, so a past
    input can never apply again, and `ceo_inputs` is hashed state: leaving them in place would
    grow the state hash's input for the length of the run and make the scan below O(run).
    Inputs still tagged for future ticks are kept, because they have not happened yet.
    """
    held_at = max((tick for tick in state.ceo_inputs if tick <= state.tick), default=None)
    if held_at is None:
        return 0

    for tick in [tick for tick in state.ceo_inputs if tick < held_at]:
        del state.ceo_inputs[tick]

    return state.ceo_inputs[held_at]


def _advance_ceo(state: State) -> None:
    """Apply the CEO's held-direction input for this tick, if one was logged."""
    bitmask = _held_bitmask(state)
    if not bitmask:
        return

    dx = (1 if bitmask & INPUT_RIGHT else 0) - (1 if bitmask & INPUT_LEFT else 0)
    dy = (1 if bitmask & INPUT_DOWN else 0) - (1 if bitmask & INPUT_UP else 0)
    if dx == 0 and dy == 0:
        return

    per_axis = CEO_DIAGONAL_MILLI_PER_TICK if dx and dy else CEO_STRAIGHT_MILLI_PER_TICK
    ceo = state.ceo

    # Look ahead so the sprite never sinks half-way into a wall, and test each axis
    # separately so sliding along a wall works rather than stopping dead.
    if dx:
        candidate = ceo.x_milli + dx * per_axis
        probe = to_tile(candidate + dx * CEO_LOOKAHEAD_MILLI)
        if walkable(state.floor, probe, to_tile(ceo.y_milli)):
            ceo.x_milli = candidate
    if dy:
        candidate = ceo.y_milli + dy * per_axis
        probe = to_tile(candidate + dy * CEO_LOOKAHEAD_MILLI)
        if walkable(state.floor, to_tile(ceo.x_milli), probe):
            ceo.y_milli = candidate

    ceo.facing = (
        ("right" if dx > 0 else "left") if abs(dx) > abs(dy) else ("down" if dy > 0 else "up")
    )


# =========================================================================
# Commands
# =========================================================================


def is_unlocked(state: State, item_id: str) -> bool:
    requires = state.spec_of(item_id).requires
    if requires.visibility is not None and state.metrics["visibility"] < requires.visibility:
        return False
    return all(state.items[required].status == STATUS_DONE for required in requires.items)


def lock_reason(state: State, item_id: str) -> str:
    requires = state.spec_of(item_id).requires
    if requires.visibility is not None and state.metrics["visibility"] < requires.visibility:
        return (
            f"Unlocks at {requires.visibility}% visibility "
            f"(now {state.metrics['visibility']}%)"
        )
    missing = [
        required for required in requires.items if state.items[required].status != STATUS_DONE
    ]
    if missing:
        return f'Needs "{state.spec_of(missing[0]).title}" first'
    return ""


def submit_ceo_input(state: State, bitmask: int, at_tick: int) -> list[Emitted]:
    """Log a held-direction input for a future tick.

    Tagged a fixed delay ahead of the tick being rendered, so both sides agree by
    construction. An input for a tick that has already passed cannot be applied without
    rewriting history, so it is rejected.
    """
    if bitmask & ~INPUT_MASK:
        raise CommandRejected(f"input bitmask {bitmask} has bits outside the known set")
    if at_tick <= state.tick:
        raise CommandRejected(
            f"input is for tick {at_tick}, which is not in the future (now {state.tick})"
        )

    state.ceo_inputs[at_tick] = bitmask
    return [
        Emitted(
            kind=EventKind.CEO_INPUT,
            payload={"tick": at_tick, "bitmask": bitmask, "submitted_at_tick": state.tick},
        )
    ]


def assign_via_manager(state: State, item_id: str) -> list[Emitted]:
    """Through the reporting line: the director walks over and hands the work across.

    The org chart becomes visible because the hand-off is physical.
    """
    item = state.item(item_id)
    spec = state.spec_of(item_id)

    if item.status != STATUS_BACKLOG:
        raise CommandRejected(f'"{spec.title}" is already {item.status}')
    if not is_unlocked(state, item_id):
        raise CommandRejected(lock_reason(state, item_id) or "not available yet")

    director_id = roster.reporting_line_of(spec.want)
    if director_id == spec.want:
        # The wanted person *is* the director; there is nobody to route through.
        return assign_direct(state, item_id, spec.want)

    staff = state.person(spec.want)
    director = state.person(director_id)
    item.status = STATUS_ASSIGNED
    item.assignee = staff.id

    director.item_id = item_id
    _walk_to(state, director, staff.seat, ARRIVE_HANDOFF)
    director.arrive_item = item_id
    _refresh_load(state)

    return [
        Emitted(
            kind=EventKind.WORK_ASSIGNED,
            payload={
                "tick": state.tick,
                "item": item_id,
                "person": staff.id,
                "via": director.id,
                "bypassed_director": False,
                "handoff_started": True,
                # Still `assigned`: the director is walking the work over, and nothing burns
                # until they arrive.
                "item_status": item.status,
            },
        )
    ]


def assign_direct(state: State, item_id: str, person_id: str) -> list[Emitted]:
    """Straight to the specialist: faster, but their director is now blind to it."""
    item = state.item(item_id)
    spec = state.spec_of(item_id)

    if item.status != STATUS_BACKLOG:
        raise CommandRejected(f'"{spec.title}" is already {item.status}')
    if not is_unlocked(state, item_id):
        raise CommandRejected(lock_reason(state, item_id) or "not available yet")

    person = state.person(person_id)
    item.status = STATUS_ASSIGNED
    item.assignee = person.id

    manager = roster.spec(person_id).mgr
    effective: dict[str, int] = {}
    if manager:
        person.bypassed_director = True
        # Company-wide, so the aggregate moves by exactly the authored 3.
        effective = {"morale": mor.apply_company_delta(state.morale, -3)}
        state.metrics["morale"] = mor.company_morale(state.morale)

    events = [
        Emitted(
            kind=EventKind.WORK_ASSIGNED,
            payload={
                "tick": state.tick,
                "item": item_id,
                "person": person.id,
                "via": "",
                "bypassed_director": bool(manager),
                "uninformed": [manager] if manager else [],
                "deltas": effective,
                "metrics": dict(state.metrics),
            },
        )
    ]
    _start_work(state, person, item)
    _refresh_load(state)
    # Stamped after the work starts: a direct assignment goes straight to active, and the
    # payload has to report where the item ended up rather than where it passed through.
    events[0].payload["item_status"] = item.status
    return events


def reassign(state: State, item_id: str, person_id: str) -> list[Emitted]:
    """Move in-flight work to someone else.

    Inside one reporting line this succeeds and keeps the effort already burned. Across
    reporting lines it is rejected and mutates nothing — the org chart is a constraint,
    not a suggestion.
    """
    item = state.item(item_id)
    if item.status not in IN_FLIGHT:
        raise CommandRejected(f"{item_id} is {item.status}; there is nothing in flight")

    current = item.assignee
    if not roster.same_reporting_line(current, person_id):
        raise CommandRejected(
            f"{roster.spec(person_id).name} is in a different reporting line from "
            f"{roster.spec(current).name}; reassignment across lines is not permitted"
        )

    retained = item.done_units
    previous = state.people[current]
    previous.state = STATE_IDLE
    previous.item_id = ""
    previous.cp_index = -1

    person = state.person(person_id)
    item.assignee = person.id
    item.status = STATUS_ASSIGNED

    events = [
        Emitted(
            kind=EventKind.WORK_REASSIGNED,
            payload={
                "tick": state.tick,
                "item": item_id,
                "from": current,
                "to": person.id,
                "retained_units": retained,
            },
        )
    ]
    _start_work(state, person, item)
    _refresh_load(state)
    events[0].payload["item_status"] = item.status
    return events


def return_to_backlog(state: State, item_id: str) -> list[Emitted]:
    """Hand an in-flight item back, keeping the effort already burned (R42).

    The response to overload that costs no cash — which is exactly why the economy needs the
    draw to carry into the burn. If automating work saved nothing, this would strictly
    dominate hiring: shed the load, pay nothing, lose nothing.
    """
    item = state.item(item_id)
    if item.status not in IN_FLIGHT:
        raise CommandRejected(f"{item_id} is {item.status}; there is nothing in flight")

    retained = item.done_units
    assignee = item.assignee
    person = state.people.get(assignee)
    if person is not None:
        person.state = STATE_IDLE
        person.item_id = ""
        person.cp_index = -1

    item.status = STATUS_BACKLOG
    item.assignee = ""
    _refresh_load(state)

    return [
        Emitted(
            kind=EventKind.WORK_RETURNED_TO_BACKLOG,
            payload={
                "tick": state.tick,
                "item": item_id,
                "was_assigned_to": assignee,
                "retained_units": retained,
                "item_status": item.status,
                "load": {
                    director: department.load_permille
                    for director, department in state.capacity.items()
                },
                "metrics": dict(state.metrics),
            },
        )
    ]


def request_hire(state: State, director_id: str) -> list[Emitted]:
    """Ask for a hire. It becomes a work item routed through People (R23).

    Not a menu action that adds a person. It consumes sim-time, which is what makes overload
    something to anticipate rather than something to fix on the tick it is noticed.
    """
    if director_id not in state.capacity:
        raise CommandRejected(f"{director_id} is not a department")

    ordinal = 1 + sum(1 for hire in state.hires.values() if hire.director_id == director_id)
    person_id = hiring.hire_person_id(director_id, ordinal)
    request_id = f"hire-{person_id}"
    item_id = f"wi_{request_id}"

    if request_id in state.hires:
        raise CommandRejected(f"{person_id} is already being hired")

    target_room = hiring.target_room_for(director_id)
    spec = work.ItemSpec(
        id=item_id,
        title=f"Hire into {roster.spec(director_id).dept}",
        brief=f"Recruit and onboard one person for {roster.spec(director_id).name}'s line.",
        dept="hr",
        want="stf_rec",
        effort_hours=hiring.HIRE_EFFORT_HOURS,
        friction="Scheduling interviews.",
        checkpoints=(),
        output_title=f"New hire for {target_room}",
        output_kind="Hire",
    )
    state.dynamic_items[item_id] = spec
    state.items[item_id] = ItemRuntime(id=item_id, resolved=[])
    state.hires[request_id] = hiring.Hire(
        request_id=request_id,
        director_id=director_id,
        person_id=person_id,
        item_id=item_id,
    )

    events = [
        Emitted(
            kind=EventKind.HIRE_REQUESTED,
            payload={
                "tick": state.tick,
                "request": request_id,
                "director": director_id,
                "person": person_id,
                "item": item_id,
                "effort_hours": hiring.HIRE_EFFORT_HOURS,
            },
        )
    ]
    events.extend(assign_direct(state, item_id, "stf_rec"))
    return events


def _complete_hire(state: State, item_id: str) -> list[Emitted]:
    """A finished hiring item: seat them, or refuse with a reason (R25)."""
    hire = next(
        (candidate for candidate in state.hires.values() if candidate.item_id == item_id), None
    )
    if hire is None or hire.status != "requested":
        return []

    taken = {person.seat for person in state.people.values()}
    taken.update(
        existing.seat for existing in state.hires.values() if existing.seat is not None
    )

    seat, refusal = hiring.plan_desk(
        state.floor, hiring.target_room_for(hire.director_id), taken
    )

    if seat is None:
        hire.status = "refused"
        hire.refusal = refusal
        return [
            Emitted(
                kind=EventKind.HIRE_REFUSED,
                payload={
                    "tick": state.tick,
                    "request": hire.request_id,
                    "director": hire.director_id,
                    "reason": refusal,
                },
            )
        ]

    hire.status = "arrived"
    hire.seat = seat
    state.seats[hire.person_id] = seat
    state.people[hire.person_id] = PersonRuntime(id=hire.person_id, pos=seat, seat=seat)
    state.morale[hire.person_id] = mor.PersonMorale(value=hiring.HIRE_INITIAL_MORALE)

    _, effective = effects.apply_effect(state.metrics, {"cash": -hiring.HIRE_COST_CASH})
    _refresh_load(state)

    return [
        Emitted(
            kind=EventKind.HIRE_ARRIVED,
            payload={
                "tick": state.tick,
                "request": hire.request_id,
                "director": hire.director_id,
                "person": hire.person_id,
                "seat": list(seat),
                "cash_cost": hiring.HIRE_COST_CASH,
                "salary_per_day": hiring.HIRE_SALARY_PER_DAY,
                "deltas": effective,
                "metrics": dict(state.metrics),
            },
        )
    ]


def resolve_checkpoint(
    state: State, item_id: str, cp_index: int, option_index: int, in_person: bool
) -> list[Emitted]:
    """Settle a decision. Done in person, the tacit knowledge comes out too."""
    item = state.item(item_id)
    spec = state.spec_of(item_id)

    if cp_index < 0 or cp_index >= len(spec.checkpoints):
        raise CommandRejected(f"{item_id} has no checkpoint {cp_index}")
    if item.resolved[cp_index]:
        raise CommandRejected("that checkpoint is already resolved")

    checkpoint = spec.checkpoints[cp_index]
    if option_index < 0 or option_index >= len(checkpoint.options):
        raise CommandRejected(f"no option {option_index} on that checkpoint")

    # The checkpoint has to have actually been raised. Without this the CEO can answer a
    # question nobody has asked yet, which puts a DECISION_RESOLVED in the log with no
    # CHECKPOINT_RAISED before it — and the work then never stalls, so the product's central
    # behaviour quietly stops happening.
    #
    # The prototype has no equivalent guard because its `resolve` is only reachable from a
    # tray that renders blocked items; a kernel command has no such protection and needs the
    # rule stated. Found when U7's slower burn rate meant a fixed tick count no longer
    # reached the checkpoint, and the early resolution succeeded instead of failing.
    if not work.checkpoint_reached(item.done_units, spec.effort_units, checkpoint.at_percent):
        progress = item.done_units * 100 // spec.effort_units if spec.effort_units else 0
        raise CommandRejected(
            f'"{spec.title}" has not reached that decision point yet — it is at {progress}% '
            f"of {checkpoint.at_percent}%. Nobody is waiting on you for it."
        )

    option = checkpoint.options[option_index]
    item.resolved[cp_index] = True

    # R59: an authored effect names a recurring-draw change, not a manual-hours delta. The
    # metric follows from the draws, so applying both would double-count.
    metric_effect, draw_delta = effects.split_draw(option.effect)
    morale_delta = metric_effect.pop("morale", 0)

    _, effective = effects.apply_effect(state.metrics, metric_effect)

    draw_effective = 0
    if draw_delta:
        director = work.director_for(spec)
        draw_effective = cap.apply_draw_change(state.capacity, director, draw_delta)

    # In person the CEO hears what the tray never shows. From the tray they get the
    # decision and none of the context, and morale reflects it.
    morale_delta += 2 if in_person else -1
    if in_person:
        _, side = effects.apply_effect(state.metrics, {"visibility": 2})
        for key, delta in side.items():
            effective[key] = effective.get(key, 0) + delta

    # Authored morale effects are company-wide, so they move every person and the aggregate
    # moves by exactly the authored figure. Only load-driven degradation is targeted (R56).
    if morale_delta:
        effective["morale"] = effective.get("morale", 0) + mor.apply_company_delta(
            state.morale, morale_delta
        )

    _refresh_load(state)

    person = state.people[item.assignee]
    manager = roster.spec(person.id).mgr
    uninformed = (manager,) if person.bypassed_director and manager else ()

    item.decisions.append(
        Decision(
            label=checkpoint.label,
            choice=option.label,
            note=option.note,
            in_person=in_person,
            tacit=checkpoint.tacit if in_person else "",
            at_tick=state.tick,
            uninformed=uninformed,
        )
    )

    person.state = STATE_WORKING
    person.cp_index = -1
    item.status = STATUS_ACTIVE

    return [
        Emitted(
            kind=EventKind.DECISION_RESOLVED,
            payload={
                "tick": state.tick,
                "item": item_id,
                "cp_index": cp_index,
                "option_index": option_index,
                "choice": option.label,
                "note": option.note,
                "in_person": in_person,
                "tacit_recorded": bool(in_person and checkpoint.tacit),
                "uninformed": list(uninformed),
                "deltas": effective,
                "draw_delta": draw_effective,
                "draw_department": work.director_for(spec) if draw_delta else "",
                "metrics": dict(state.metrics),
                "item_status": item.status,
            },
        )
    ]


# =========================================================================
# The state hash's view of all this
# =========================================================================


def snapshot(state: State) -> dict[str, Any]:
    """Kernel state as the state hash sees it.

    Every declared subsystem is present. The ones U7 and U8 fill are empty rather than
    absent, so their arrival is a recorded state-shape change rather than a silent hash
    break.
    """
    return {
        "world": state.floor.to_state(),
        "people": {pid: person.to_state() for pid, person in state.people.items()},
        "items": {iid: item.to_state() for iid, item in state.items.items()},
        "metrics": dict(state.metrics),
        "ceo": {
            **state.ceo.to_state(),
            "inputs": {str(tick): mask for tick, mask in sorted(state.ceo_inputs.items())},
        },
        "pending": pend.to_state(state.pending),
        "capacity": cap.to_state(state.capacity),
        "morale": mor.to_state(state.morale),
        "hiring": hiring.to_state(state.hires),
        "lifecycle": {
            "horizon_tick": state.horizon_tick,
            "terminal_reason": state.terminal_reason,
            "terminal_tick": state.terminal_tick,
            "tick": state.tick,
            "day": simtime.day_of(state.tick),
            "outputs": [output.to_state() for output in state.outputs],
        },
    }
