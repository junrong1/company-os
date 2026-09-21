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

**The company is on `State`, not in a global.** The roster and the work graph were module
constants until U6, read here at fold and step time. One process ticks many runs at once, so a
scenario in a global would be a single company shared across runs of different ones — and the
symptom would be a run seating people from somebody else's roster, or pricing a decision
against a department it does not have. `state.scenario` is an immutable fold-time input,
excluded from the state hash because the genesis event already records its content hash and
hashing the same structure twice would move the state-shape version for a value that cannot
change within a run. `snapshot()` below is the only input to `hashing.state_hash`, and it omits
the scenario — enforced rather than remembered, because `state_hash` refuses an undeclared
subsystem.

**Three lookups go through `State` rather than through the scenario directly**, and that is the
point of them: `rank_of`, `name_of` and `manager_of` answer for an arrived hire, who is in
`state.people` and on no authored roster. Reading the roster straight raised `KeyError` past
the point where `CommandRejected` is caught.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from contracts.envelope import EventKind
from simcore import authorization as authz
from simcore import capacity as cap
from simcore import effects
from simcore import hiring
from simcore import items as work
from simcore import lifecycle
from simcore import morale as mor
from simcore import pending as pend
from simcore import people as roster
from simcore import scenario as sc
from simcore import statement as stmt
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

#: How far ahead of the current tick an input may be tagged.
#:
#: The client tags a wall-time budget's worth, which is about sixteen ticks at the fastest
#: rate; a sim-day is absurdly generous by comparison. The bound exists because `ceo_inputs` is
#: hashed state that is scanned every tick: without it, inputs tagged for arbitrarily distant
#: ticks accumulate forever — the pruning only drops entries the clock has *passed* — and every
#: tick of the run pays for them.
#:
#: A guard rather than tuning, so it stays out of TUNING and out of the rules version. Changing
#: a limit on what may be submitted does not change what a recorded run means.
MAX_INPUT_LEAD_TICKS = simtime.TICKS_PER_SIM_DAY

#: How near the CEO must stand for a director to brief them, in milli-tiles.
#:
#: The client's `OPEN_RADIUS_MILLI` (`frontend/src/ui/conversation-model.ts`), and it has to be the
#: same number: the pending block for a statement renders on the conversation panel that radius
#: opens, so a kernel with its own radius would raise requests for a director the client is not
#: showing, or show a panel with no briefing coming.
#:
#: A guard rather than tuning, so it stays out of TUNING and out of the rules version. It bounds
#: when the kernel *asks* a question; it changes nothing about what a recorded run means, and moving
#: the rules version would invalidate every kept snapshot to say "the CEO now stands a little
#: closer".
STATEMENT_RANGE_MILLI = 1900

#: How long a typed question may be.
#:
#: The text is written verbatim into an append-only log, broadcast to every subscriber,
#: re-validated on every read and copied by every fork, so it is worth bounding where it is
#: priced rather than trusting the browser. Generous for a sentence someone types.
MAX_QUESTION_CHARS = 500

#: How many branches one comparison may run (R26).
#:
#: The count is derived from the authored option list rather than submitted, so this bounds
#: what a *checkpoint* can cost rather than what a client can ask for — and it is still a
#: submission guard, because what it bounds is the work one command causes. Every authored
#: checkpoint offers three; six leaves room for one to grow without this becoming the thing
#: that has to be edited first. Measured: 0.27s per branch at `MAX_BRANCH_DAYS`, so six is
#: about 1.6 seconds worst case — all of it synchronous, on the request thread, holding the
#: GIL. That is the real cost of this bound and it is stated rather than estimated.
#:
#: A guard rather than tuning, so it stays out of TUNING and out of the rules version. A
#: comparison changes no state, so its bound cannot change what a recorded run means — and
#: moving the rules version would invalidate every kept snapshot and make current logs
#: unfoldable, which is a real cost for a number that decides nothing about the simulation.
MAX_BRANCHES_PER_COMPARISON = 6

#: How large a comparison record may be, encoded.
#:
#: Every branch carries a bounded trajectory per metric, so the payload is already bounded by
#: construction — this is the belt on top of it, and it exists because the log is append-only
#: and cannot take a row back. A full comparison of three branches over a twenty-day horizon
#: encodes to roughly sixteen kilobytes, so this is about four times the real case.
MAX_COMPARISON_PAYLOAD_BYTES = 64 * 1024

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

#: What each arrival intent leaves the walker in once the path runs out.
#:
#: A table rather than a chain of branches inside `_arrive`, because the movement event has to
#: carry the same answer: a client is *told* what a walk ends in rather than mapping the intent
#: itself, and a second copy of this mapping in TypeScript would be duplicated logic with
#: nothing comparing the two halves. `_arrive` reads it, so there is one place to change.
ARRIVE_STATE: dict[str, str] = {
    ARRIVE_NONE: STATE_IDLE,
    ARRIVE_START_WORK: STATE_WORKING,
    ARRIVE_RESUME_WORK: STATE_WORKING,
    ARRIVE_ENTER_MEETING: STATE_MEETING,
    ARRIVE_SIT_IDLE: STATE_IDLE,
    # A hand-off does not end in standing: the director hands the work across and sets off
    # home in the same tick, which is a walk of its own and an event of its own.
    ARRIVE_HANDOFF: STATE_WALKING,
}

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
    #: Which of the four questions this person has already answered.
    #:
    #: A *sorted list*, not a set, and that is not a stylistic choice: a set anywhere in hashed
    #: state raises `NotCanonical`, and it raises at a day-boundary hash rather than at the line
    #: that put it there. Kept sorted on insert so two states that have heard the same questions
    #: in different orders hash the same.
    answered: list[str] = field(default_factory=list)

    # No `rank` property. It read the module-level roster and raised `KeyError` for an arrived
    # hire — someone in `state.people` whom no scenario authored — and a `PersonRuntime` has no
    # way to reach the company the run was created against. It is `State.rank_of` now.

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
            "answered": sorted(self.answered),
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
    #: The company this run was created against (M9). An immutable fold-time input, deliberately
    #: outside the state hash — see the module docstring, and `snapshot()` at the foot of it.
    scenario: sc.Scenario
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
    #: Work items created at runtime — hiring items. Authored items live on `scenario.items`.
    #:
    #: Kept as a second table rather than merged into the scenario, and U6 confirmed it has to
    #: stay one: `snapshot.to_wire` writes eight fields per dynamic item and no `checkpoints`
    #: tuple, so a branch forked from a state holding an authored item here would come back with
    #: an empty tuple and fail on an index. The authored catalog stays on the static path.
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
    #: What each item has asked the CEO for permission to read, keyed by item (U15, M39-M42).
    #:
    #: Keyed by item rather than by request id, and that is what makes M42 structural: a grant lives
    #: on the item that asked for it, so nothing carries it to a second item and "no standing
    #: permission" is a property of the table rather than a check somebody has to remember. The
    #: request id is a field on the record, empty once the question has been answered.
    authorizations: dict[str, authz.Authorization] = field(default_factory=dict)
    #: The last period the domain service was consulted for, so one period asks once.
    last_period_consulted: int = 0
    #: Answers that arrived and are waiting for the tick they apply at.
    queued_answers: dict[int, list[dict[str, Any]]] = field(default_factory=dict)
    #: Items whose unlock has already been announced, sorted.
    #:
    #: Unlocking is a consequence of state rather than of any one command — a completed
    #: prerequisite does it, and so does a Visibility gain from asking — so the announcement
    #: belongs to the step, which regenerates on replay, rather than to whichever command
    #: happened to move the number. This is the memory that makes "newly" mean anything: without
    #: it the step can only answer "what is available now", which is true every tick.
    announced_unlocks: list[str] = field(default_factory=list)

    def spec_of(self, item_id: str) -> work.ItemSpec:
        """An item's spec, authored or created at runtime."""
        if item_id in self.dynamic_items:
            return self.dynamic_items[item_id]
        return self.scenario.item(item_id)

    def line_of(self, person_id: str) -> str:
        return self.scenario.reporting_line_of(person_id)

    def rank_of(self, person_id: str) -> str:
        """A person's rank, `"staff"` for an arrived hire.

        A hire is in `state.people` and on no authored roster, so the roster lookup this
        replaces raised `KeyError`. `"staff"` rather than a refusal because that is what a hire
        *is* — hiring adds capacity to a line, never a second head of one, and `attrition` and
        the bypass penalty both need an answer rather than an exception.
        """
        person = self.scenario.people_by_id.get(person_id)
        return person.rank if person is not None else "staff"

    def name_of(self, person_id: str) -> str:
        """A person's name for a message or a provenance line; their id if nobody authored one.

        The id rather than a placeholder: a deliverable's provenance is an audit line, and
        "Somebody — 12 hours of work" would be less use than the id the log already carries.
        """
        person = self.scenario.people_by_id.get(person_id)
        return person.name if person is not None else person_id

    def manager_of(self, person_id: str) -> str:
        """Whose line this person reports into, `""` for a director.

        For an authored person this is exactly their authored `manager`, so no shipped value
        moves. For an arrived hire — whom no scenario authored — it is the director of the line
        they were hired into, which is the answer that makes assigning work around them count as
        a bypass in the same way it does for anybody else at their level.
        """
        person = self.scenario.people_by_id.get(person_id)
        if person is not None:
            return person.mgr
        for hire in self.hires.values():
            if hire.person_id == person_id:
                return hire.director_id
        return ""

    def present_members(self, director_id: str) -> tuple[str, ...]:
        """Everyone still in this line, the director included and departures excluded."""
        members = [
            person_id
            for person_id in self.scenario.lines.get(director_id, ())
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
        """The line an assignee's work loads, hires included."""
        for hire in self.hires.values():
            if hire.person_id == person_id:
                return hire.director_id
        return self.scenario.reporting_line_of(person_id)

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
    scenario: sc.Scenario | None = None,
) -> tuple[State, list[Emitted]]:
    """Create a run, and the genesis event recording what it was created with.

    Geometry is computed here, once, and recorded. Nothing downstream re-derives it
    from a viewport.

    `scenario` defaults to the shipped company rather than being required, which is what let the
    wiring land without touching forty-two call sites — and it is the right default anyway:
    "which company" is a choice the gateway offers (U7), not something every caller of the
    kernel library has an opinion about. The fold passes the one the run recorded, and
    `log._apply_genesis` is the site that refuses a scenario whose file has since changed.
    """
    company = sc.load_default() if scenario is None else scenario
    floor = plan_floor(cols, rows)
    seats = roster.assign_seats(company, floor)

    state = State(
        run_seed=run_seed,
        scenario=company,
        tick=0,
        floor=floor,
        seats=seats,
        metrics=effects.initial_metrics(company),
        people={
            person.id: PersonRuntime(id=person.id, pos=seats[person.id], seat=seats[person.id])
            for person in company.people
        },
        items={
            item.id: ItemRuntime(id=item.id, resolved=[False] * len(item.checkpoints))
            for item in company.items
        },
        ceo=CeoRuntime(x_milli=floor.spawn[0] * MILLI, y_milli=floor.spawn[1] * MILLI),
        capacity=cap.new_capacity(company),
        morale=mor.new_morale(company),
        horizon_tick=(
            lifecycle.default_horizon_tick() if horizon_tick is None else horizon_tick
        ),
    )
    _seed_authored_work(state)
    _refresh_load(state)

    # Anything already available on day one is announced by genesis itself — the catalog and
    # the starting metrics are both in that payload — so it is recorded as said rather than
    # re-announced by the first step.
    state.announced_unlocks = sorted(_newly_available(state))

    genesis = Emitted(
        kind=EventKind.GENESIS,
        payload={
            "run_seed": run_seed,
            "quantum_sim_seconds": simtime.QUANTUM_SIM_SECONDS,
            "grid": [cols, rows],
            "horizon_tick": state.horizon_tick,
            "decision_supply": lifecycle.decision_supply(company),
            "rules_ver": RULES_VERSION,
            # Which company this run is of, and which exact revision of it (R7). Three sites
            # check it and refuse a run whose file has since been edited; see
            # `scenario.load_recorded`. It rides genesis rather than a store column so that an
            # exported log carries its own provenance and a fork inherits it for free.
            "scenario": company.identity(),
            "metrics": effects.initial_metrics(company),
            "draws": dict(company.draws),
            "floor": floor.to_state(),
            "roster": roster.roster_to_state(company, seats),
            "items": [item.id for item in company.items],
            # The authored work graph. Ids alone were enough while nothing read the
            # dependency edges; U14's DAG assigns layers by longest-path depth over the
            # whole graph, so it needs the edges before anything unlocks.
            "catalog": work.catalog_to_state(company),
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


def _seed_authored_work(state: State) -> None:
    """Put the authored day-zero work in flight, so day one is not an empty floor (M6).

    **This emits no event, and that is the load-bearing part.** The fold rebuilds genesis by
    calling this same `new_run` (`log._apply_genesis`), so the seed is already applied by the
    time any logged event is replayed. A `WORK_ASSIGNED` alongside it would be worse than
    redundant: that kind is an *input*, so the replay would re-issue `assign_direct` against
    an item this function had already made active, and the command would be rejected mid-fold.
    The genesis event is the record; the seed is part of what genesis means.

    **It is still not on the genesis payload, and U6 decided that deliberately.** U2 deferred
    the question here on the reasoning that R7's mechanism would make the seed checkable. R7
    makes it checkable *by hash*: the seed is inside the scenario's content hash, so a file whose
    seed moved is refused at all three guards by name and revision. A payload copy would be a
    key nothing reads — the fold rebuilds the seed by calling this function with the recorded
    scenario rather than by reading a payload, the client learns the seeded item from the first
    `CHECKPOINT_RAISED` (which names the person, the item and the effort already burned, with no
    command anywhere before it to explain them), and the drift report would need a fourth
    comparison projection to say anything about it that the hash does not already refuse. A
    scenario field the payload does not carry narrows the *report* rather than the guard, and
    `_mismatch` says so in as many words.

    **It goes through `_start_work` rather than setting the fields itself**, so a seeded person
    reaches their desk by the same path a delegated one does. They are already sitting at it at
    genesis, so no walk is generated.

    **A seed that would walk refuses rather than dropping the walk.** Since movement went on the
    wire (R15), `_start_work` emits an event for anyone not already at their desk, and genesis has
    nowhere to put one: `new_run` returns GENESIS alone, and the fold rebuilds this state by
    *calling* `new_run` rather than by replaying events. So a movement event produced here would
    either be swallowed — the client never sees the walk and the person teleports on their first
    step — or appended, which puts an output event at tick zero that no `step()` regenerates and
    makes strict replay diverge on it. Neither happens today, and U6 kept it that way rather than
    teaching genesis to carry a walk: a scenario cannot express a seed that walks, because
    `[[seeded_assignment]]` names an item and a person and the person's desk is where they start.
    A format that let an author seed somebody mid-floor would have to be refused at *load*, where
    the message can name the line, rather than here at genesis.

    A log written before this seed existed folds to a different day-zero state under an
    unchanged rules version, because RULES_VERSION digests the tuning table and the multiplier
    order — not the authored roster or work graph. That is survivable only because U9's
    DDL_VERSION bump makes the store a documented wipe; there are no older logs to fold.
    """
    for seeded in state.scenario.seeded:
        item = state.items[seeded.item_id]
        person = state.people[seeded.person_id]
        item.assignee = person.id
        item.done_units = seeded.done_units(state.scenario.item(seeded.item_id))
        if _start_work(state, person, item):
            raise ValueError(
                f"the seeded assignment of {seeded.item_id} puts {seeded.person_id} at "
                f"{person.pos} rather than at their desk {person.seat}, so genesis would have "
                "to carry a movement event — and it cannot, because the fold rebuilds genesis "
                "by calling new_run, so an output event at tick zero regenerates nowhere and "
                "strict replay diverges on it. Seed people at their desks, or teach the fold "
                "to compare genesis' own outputs."
            )


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

    # Phase 5b — what has become available. Derived from state rather than announced by the
    # command that moved the gate, so it regenerates on replay whatever cleared it: a completed
    # prerequisite, or the Visibility a question just paid out.
    events.extend(_announce_unlocks(state))

    # Phase 6 — the pending-input contract: apply answers that land on this tick, raise the
    # period consult at a period boundary and a statement request where the CEO is standing at an
    # open checkpoint, and abandon anything past its deadline. All of it inside the step, so the
    # deadline, the cap and the request itself are properties of the run rather than of the machine.
    #
    # After movement, because whether the CEO is beside a director is a fact about where this tick
    # left them; before abandonment, because a request raised this tick is not overdue this tick and
    # asking the question in the other order would only invite somebody to wonder.
    events.extend(_apply_queued_answers(state))
    events.extend(_raise_period_consult(state))
    events.extend(_raise_statement_requests(state))
    # After the statement requests, because an item stalled on an Authorization never reaches a
    # checkpoint and so never has a briefing to raise — the order says which of the two can
    # suppress the other, and it is this one.
    events.extend(_raise_authorization_requests(state))
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
    *,
    subject: dict[str, Any] | None = None,
    deadline_ticks: int | None = None,
) -> list[Emitted]:
    """Emit a request against the owning item. The clock keeps running.

    No network call happens here (R2). The kernel records that it asked; the transport is the
    caller's business, and on replay the caller never runs.

    **This does not stall the item by itself, and for three of the four legs nothing else does
    either.** It records the request in `state.pending` and emits `REQUEST_RAISED`; nothing in
    `_advance_work` reads `state.pending`, so a period consult, a resolution and a statement all
    leave their item burning exactly as before — which is right for each of them, because the
    period consult owns a synthetic item, the resolver's item is already blocked at its checkpoint,
    and a briefing is not something work waits for.

    **The Authorization leg is the one that stalls, and it stalls on its own record rather than on
    this one** (U15, R24). `_raise_authorization_requests` writes `state.authorizations[item]`
    beside the request, and `_advance_work` reads *that* — so the stall is a fact about the item
    that survives the request being answered, abandoned or asked again, which a projection of
    outstanding questions cannot express.

    `subject` is the leg-specific half of the payload and is absent entirely for a leg that has
    none. Merged rather than nested, because the sequence diagram this implements names `person` on
    the event; conditional rather than defaulted, because a key added here for the bench would
    change what `step()` regenerates for a *period consult* — and every log written before this unit
    would then fail strict replay on an event the bench has nothing to do with.

    `deadline_ticks` is per request for the same reason it is per leg (R18). A statement's window is
    sized against a provider API and the period consult's against a service on a loopback; sharing
    one number would make the bench's dominant path at speed abandonment. It costs no state-shape
    move, because `PendingRequest.deadline_tick` is already an absolute tick per request.
    """
    per_item = pend.outstanding_for_item(state.pending, owning_item)
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

    window = pend.REQUEST_DEADLINE_TICKS if deadline_ticks is None else deadline_ticks
    request = pend.PendingRequest(
        request_id=request_id,
        service=service,
        owning_item=owning_item,
        raised_at_tick=state.tick,
        deadline_tick=state.tick + window,
        period_index=period_index,
    )
    state.pending[request_id] = request

    return [
        Emitted(
            kind=EventKind.REQUEST_RAISED,
            payload={
                # The subject goes first so the contract's own keys win a collision. Canonical
                # encoding sorts keys, so the order costs nothing on the wire and buys the
                # guarantee that a leg cannot rewrite `service` or `deadline_tick` by naming them.
                **(subject or {}),
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
    state: State,
    request_id: str,
    answer: dict[str, Any],
    at_tick: int | None = None,
    *,
    paused: bool = False,
) -> list[Emitted]:
    """Queue an answer for the tick it applies at.

    Only the tick loop appends (R22), so an answer arriving on a stream is enqueued here and
    applied at a tick boundary rather than written on the spot. That is what makes sole-writer
    true at the transaction level rather than merely at the process level.

    **A statement's landing tick is derived from the tick that raised the request, never from the
    live tick** (R17). The live tick at the moment an answer arrives is how long the provider took,
    so a landing tick read from it makes provider latency hashed state — `pending` is a hashed
    subsystem and the landing tick is when a request leaves it — and two fresh runs from one seed
    diverge on nothing the player did. Deriving it from `raised_at_tick` makes latency invisible to
    the run, which is the same call this contract already made for deadlines.

    `paused` is the one branch that reads the clock, and it reads a tick the *player* set rather
    than one the provider set: while the run is paused `state.tick` does not move, so every answer
    arriving during a pause lands at the same tick however slow it was. Without this branch a
    request raised at T, a pause at T+2 and an answer at T+5 wall-clock leaves the conversation
    waiting for a tick the run will never reach, with the sim-tick deadline unable to fire either —
    the hang execution decision §2 closes. It is a parameter rather than a field on `State` for the
    reason §2 gives: a `rate` on `State` would move the state-shape version and regenerate every
    golden fixture to buy what the input event already carries.

    §2's table says "the current tick" for the paused branch; this adds one to it, and the reason is
    mechanical rather than a departure. `step()` increments the clock *before* it applies queued
    answers, so an answer filed at the tick the run is paused at is never popped. The first tick a
    paused run reaches when it resumes is that tick plus one, and it is just as much a
    player-determined tick.

    **Refusals on this path raise rather than emit, and that is a replay requirement rather than a
    style choice.** `ANSWER_REJECTED` is in the fold's *output* set, so the fold expects `step()` to
    regenerate every one of them — and it can only regenerate the ones the step derives. A refusal
    from here appends an output event with no `INPUT_RECEIVED` beside it, so there is nothing for
    `log._apply_input` to re-issue, nothing regenerates, and `_expect_exhausted` fails the strict
    comparison on a log that is in fact a faithful record. `CommandRejected` mutates nothing and
    appends nothing, which is what `compare_options` does with `MAX_COMPARISON_PAYLOAD_BYTES` and for
    the same reason. What the log then says about the request is that it was never answered, which is
    true, and the step's own abandonment says so at the deadline.

    The refusals are therefore checked **only on the live path**. On replay `at_tick` is set, the
    answer is already a logged fact, and re-checking it could only refuse a log that exists.
    """
    if not isinstance(answer, dict):
        # Checked before anything is read off it, because `dict(answer)` below is what raises and it
        # sits after the queue mutation — a non-mapping answer used to queue itself and then kill the
        # tick loop at the landing tick, two sim-days later, with the exception nowhere near the
        # cause. A leg that answers the wrong shape is a rejection with a sentence.
        raise CommandRejected(
            f"an answer is a mapping, got {type(answer).__name__}"
        )

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

    if at_tick is not None:
        # Replay's path: the landing tick is a recorded fact on an input event, so it is read
        # rather than re-derived. `log._apply_input` passes it, which is what makes a replay
        # reproduce a landing tick it could not otherwise know — a pause is in the log
        # independently, but re-deriving would mean the fold reconstructing which branch fired.
        applies_at = at_tick
    else:
        applies_at = _landing_tick(state, request, paused=paused)

        unloggable = _answer_not_loggable(answer)
        if unloggable:
            raise CommandRejected(unloggable)

        if applies_at <= state.tick:
            raise CommandRejected(
                f"the answer applies at tick {applies_at}, which the run passed at tick "
                f"{state.tick}; the leg had "
                f"{request.deadline_tick - request.raised_at_tick} ticks and its answer landed "
                "outside the window. The request stays outstanding and the step abandons it at "
                "its deadline."
            )

    # The answer itself is logged, carrying the tick it applies at. This is the event replay
    # reads instead of re-issuing the call (R3) — so the content has to be here, not merely the
    # fact that something answered.
    #
    # Built *before* the queue is touched. `dict(answer)` is the last thing that can fail, and a
    # failure after the mutation leaves an answer in memory that the log has no row for — which
    # applies at the landing tick and takes the state hash away from the log with it.
    emitted = [
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

    state.queued_answers.setdefault(applies_at, []).append(
        {"request_id": request_id, "answer": answer}
    )
    return emitted


def _landing_tick(state: State, request: pend.PendingRequest, *, paused: bool) -> int:
    """The tick this answer applies at, derived and never read from the live clock (R17)."""
    if not request.is_statement:
        # The domain consult and the resolver keep the tick they have always landed at. Neither is
        # reached by a provider — the shipped domain model is arithmetic in-process and the shipped
        # resolver declines — so neither carries the latency R17 is about, and moving them would
        # change a period's metric application tick for no requirement.
        return state.tick + 1
    if paused:
        return state.tick + 1
    return request.raised_at_tick + pend.STATEMENT_OFFSET_TICKS


def _answer_not_loggable(answer: dict[str, Any]) -> str:
    """Why this answer may not become a logged fact, or the empty string.

    Encoded rather than estimated, because the encoded form is what the log holds and a character
    count over the prose would miss a context list entirely. `contracts.canonical` is the same
    encoder the append uses, so the two cannot disagree about the size — and it is pure, which is
    what lets this live inside the step at all.
    """
    from contracts import canonical

    try:
        size = len(canonical.encode(answer))
    except (TypeError, ValueError) as refused:
        # The append would refuse it too — no float may cross the log (R6) — but it would refuse it
        # from inside the writer, where the failure is a 500 or a dead dispatch task rather than a
        # rejection anybody can read. Caught here so it is a logged reason instead.
        return f"the answer cannot be canonically encoded: {refused}"

    if size > pend.MAX_ANSWER_PAYLOAD_BYTES:
        return (
            f"the answer encodes to {size} bytes, over the bound of "
            f"{pend.MAX_ANSWER_PAYLOAD_BYTES}; the log is append-only and cannot take a row back"
        )
    return ""


def _apply_queued_answers(state: State) -> list[Emitted]:
    """Apply answers whose tick has arrived, validating each before it becomes a logged fact."""
    events: list[Emitted] = []

    for queued in state.queued_answers.pop(state.tick, []):
        request_id = queued["request_id"]
        answer = queued["answer"]
        request = state.pending.get(request_id)
        if request is None:
            continue

        # Three legs, three branches (R1). Until this unit the dispatch was two, so *every*
        # non-domain answer fell into the resolver — and a statement arriving there would have
        # resolved or escalated a checkpoint, which is the one thing a director must never do.
        if request.service == pend.DOMAIN:
            events.extend(_apply_domain_answer(state, request, answer))
        elif request.is_statement:
            events.extend(_apply_statement_answer(state, request, answer))
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


def _apply_statement_answer(
    state: State, request: pend.PendingRequest, answer: dict[str, Any]
) -> list[Emitted]:
    """Accept a briefing, or refuse it and say why. Moves no metric (M22).

    **Accepting produces no event, and that is the point.** The statement is already in the log:
    it rode `INPUT_RECEIVED`, which is an input kind and is therefore read rather than regenerated,
    so the prose, the citations, the retrieved context and the producer identity are all a recorded
    fact by the time this runs (R2, M31, M32). What is left for the landing tick is the only state
    consequence a statement has — the request stops being outstanding — and `pending` is a hashed
    subsystem, so that consequence is already covered.

    **Refusing produces one, and that is also the point.** `ANSWER_REJECTED` is an output kind, so
    the verdict is regenerated by the step and compared byte-for-byte against the log. That is what
    makes the guard auditable rather than merely enforced: a reader can re-derive from the log that a
    statement was refused, without trusting the service that produced it. Execution decision §1 is
    the whole of this branch's reason for existing.

    The scope is re-derived here rather than read off the request, for the reason the fold
    regenerates outputs at all: a scope read from the payload would make the check as trustworthy as
    the payload. It comes from folded state — the producer's line, and which items are assigned into
    it — so a tampered request cannot widen it.
    """
    del state.pending[request.request_id]

    producer = str(answer.get(stmt.KEY_PRODUCER, ""))
    refusal = _statement_refusal(state, request, producer, answer)

    if refusal:
        # No escalation flag: an item waiting on a *decision* escalates to the CEO's tray, and a
        # briefing is not a decision. The checkpoint is exactly as open as it was, and R5 sends this
        # to the same exit as a provider error — that director's scripted reply for that turn.
        return [
            Emitted(
                kind=EventKind.ANSWER_REJECTED,
                payload={
                    "tick": state.tick,
                    "reason": refusal,
                    "owning_item": request.owning_item,
                    "service": request.service,
                    "producer": producer,
                },
                request_id=request.request_id,
            )
        ]

    return []


def _statement_refusal(
    state: State, request: pend.PendingRequest, producer: str, answer: dict[str, Any]
) -> str:
    """Why this statement may not stand, or the empty string. Pure over folded state."""
    if not stmt.is_statement_answer(answer):
        return (
            "an answer on the bench leg that carries no briefing is not a statement; the leg "
            "answered the wrong shape rather than declining"
        )

    item = state.items.get(request.owning_item)
    if item is None or not item.assignee:
        return (
            "the item is no longer somebody's to brief on — it was returned to the backlog, or "
            "its assignee was removed by attrition"
        )

    if producer not in state.people or producer in state.departed:
        return f"{producer!r} is not somebody this company can brief the CEO"

    if state.rank_of(producer) != "director":
        # M14: four directors are model-backed and specialists stay scripted. A statement
        # attributed to a specialist is a persona the scenario did not author.
        return f"{producer!r} is not a director, and only a director produces a statement (M14)"

    # Derived from the *item's* line, not from the producer the answer names. Deriving it from the
    # producer would make the attribution check compare a value to itself, and the scope check would
    # then be against whichever line the answer claimed — which is the leg choosing its own scope by
    # a different door. `stmt.refusal` catches the mismatch.
    #
    # The line is read at the *landing* tick, while the leg drew its context under the line recorded
    # at the raising tick up to two sim-days earlier. So there are two ways to arrive here and folded
    # state cannot tell them apart — the answer names a director the request was not raised on, or
    # the item was reassigned across lines while the director was thinking — and the message names
    # both rather than asserting the one that happens to be more common. Either way the statement is
    # about work this producer does not own, which is the fact that decides it.
    authorized = authorized_scope(
        state, state.line_of_assignee(item.assignee), for_item=request.owning_item
    )
    if authorized.director != producer:
        return (
            f"{producer!r} produced a statement about an item in {authorized.director!r}'s line: "
            "either it is misattributed, or the item was reassigned across lines while the "
            "director was thinking"
        )

    offered = _offered_at(state, request.owning_item, item.assignee)
    if offered is None:
        # The CEO settled the checkpoint from the tray while the director was thinking. A briefing
        # about a decision already taken is refused rather than shown: there is nothing left for it
        # to inform, and M18's and M19's predicates would be checked against options that are no
        # longer on offer — which is a guard adjudicating a question nobody asked.
        return (
            "the checkpoint this statement is about is no longer open; it was settled while the "
            "director was thinking, so there is no decision left for a briefing to inform"
        )

    return stmt.refusal(answer, authorized=authorized, offered=offered)


def _offered_at(state: State, item_id: str, assignee: str) -> stmt.Offered | None:
    """The checkpoint this item is stopped at, as the guards need to see it, or `None`.

    **Derived here rather than read off the request, for the same reason the scope is.** The leg
    builds its own from the genesis catalog and rejects early; this is the reading that decides, and
    it comes from folded state so a tampered payload cannot widen what a statement may say. The two
    agree because the catalog is a projection of exactly this authored content — `Offered` owns both
    adapters so that agreement is one file's problem rather than two services'.

    The index lives on the *person*, not the item: `cp_index` is where the assignee stopped, which is
    also how `_open_checkpoint_in_line` finds a checkpoint to brief on in the first place. `None`
    means there is no open checkpoint any more.
    """
    person = state.people.get(assignee)
    if item_id not in state.items or person is None or person.cp_index < 0:
        return None
    # `.get` rather than `scenario.item`, which raises: an item created at runtime is not in the
    # authored catalog, and a hiring item reaching here would take the clock down rather than decline
    # a briefing. Those items carry no checkpoints, so `cp_index` is already `-1` for them — this is
    # the guard for the case that stops being true.
    spec = state.scenario.items_by_id.get(item_id)
    if spec is None or not 0 <= person.cp_index < len(spec.checkpoints):
        return None
    return stmt.Offered.from_checkpoint(spec, person.cp_index)


def authorized_scope(state: State, director_id: str, *, for_item: str = "") -> stmt.Authorized:
    """What this director may read *now*, derived from folded state (R23).

    The one derivation for a statement. `step()` records it on the request so the leg is *told* its
    scope, and this same function re-derives it at the landing tick so the check does not trust
    what it recorded. A second derivation in the agents service would be the second place the rule
    lived, and the two would disagree the first time a reassignment moved an item between lines.

    **Public because a second reader exists now, and it is not a second derivation.** U14's memory
    surface is a read over the same log under the same kind of scope, and the kernel is the only
    component that holds folded state — so it derives that scope here, beside this one, and hands
    it to the leg exactly as `step()` does. What it must not become is a scope the agents service
    computes: `bench/` cannot call this, because `simcore` is not importable from a bench module
    that has no state to call it with.

    **`for_item` is the whole of what a granted Authorization buys** (U15, M39). Given an item whose
    record is `granted`, the scope widens to hold the other line's present members and the items
    assigned into it — so the same `scan` that refuses a cross-line event before the grant admits it
    after, with nothing about the filter changing. Two properties follow from it being a parameter
    rather than a field on the scope:

    * the widening is per item, so a grant on one item is not a permission on another (M42). The
      caller has to name the item it is reading *for*, which is the question an Authorization
      answers and not one a director id can answer on its own;
    * the default is the unwidened line, so every existing caller — and every caller somebody adds
      without knowing this mechanic exists — gets default-deny. R23 asks for no reachable unscoped
      variant, and an omitted argument producing a wider scope would be exactly that.

    A grant belonging to a *different* director widens nothing: the record names who asked, and an
    item reassigned across lines after a grant is read by whoever holds it now under their own
    scope. That is the same rule `_statement_refusal` applies to attribution, one layer down.
    """
    people = list(state.present_members(director_id))
    items = _line_items(state, director_id)

    granted = state.authorizations.get(for_item) if for_item else None
    if granted is not None and granted.granted and granted.asking == director_id:
        people.extend(state.present_members(granted.needs))
        items.extend(_line_items(state, granted.needs))

    return stmt.authorized_for(
        state.scenario,
        director_id,
        line_members=people,
        line_items=items,
    )


def remembered_scope(state: State, director_id: str) -> stmt.Authorized:
    """What this director may read about their line's *past* (M36).

    Everyone the scenario ever put in this line, plus every hire that ever arrived into it, whether
    or not they are still here. A memory that lost its departures would rewrite the run every time
    somebody quit: the events those people produced happened, and the director was there for them.

    **It returns the same set as `authorized_scope` today, and that is a defect over there rather
    than a redundancy here.** Two independent reasons make the sets agree. A departed *authored*
    member is retained by `authorized_for` itself, which starts from `scenario.lines` — the roster
    rather than the roll call — and that is deliberate: a statement's evidence window is three days,
    so a recent departure is this line's recent history. A departed *hire* is retained by
    `present_members`, which filters `scenario.lines` on `departed` and then appends arrived hires
    with no such check — so a line that lost a hire keeps their headcount, their morale
    contribution and their share of the daily draw. That one is a live capacity defect, measured on
    the shipped company (headcount 3 → 3, 10,800 units a day for somebody who left), recorded in the
    deferred defect register by U14, and deliberately not fixed here: it moves a metric, and a read
    surface is the wrong unit to move one from.

    This function is what keeps a memory correct through that fix. When `present_members` stops
    counting a departed hire, `authorized_scope` will stop admitting them and this will not —
    without either caller changing, and without anybody having to notice that a memory needed to.

    It is not wider in any other direction. The items are the ones assigned into the line now, so an
    item reassigned to another line takes its item-keyed events with it — its *person*-keyed events
    stay, because those name people who are still this director's. That is the honest reading of
    "what a director carries forward about their reporting line", and the alternative — every item
    the line ever touched — would need a history of assignment this state does not keep.
    """
    return stmt.authorized_for(
        state.scenario,
        director_id,
        line_members=[
            hire.person_id
            for hire in state.hires.values()
            if hire.status == "arrived" and hire.director_id == director_id
        ],
        line_items=_line_items(state, director_id),
    )


def _line_items(state: State, director_id: str) -> list[str]:
    """The items assigned into this line, by the reporting line of whoever holds each one.

    Shared by both scopes above so that "which work is this director's" is one expression. It was
    written twice for one line of code and the second copy is exactly where a reassignment rule
    would eventually be applied to one caller and not the other.
    """
    return [
        item_id
        for item_id, item in state.items.items()
        if item.assignee and state.line_of_assignee(item.assignee) == director_id
    ]


def _raise_statement_requests(state: State) -> list[Emitted]:
    """Ask a director for a briefing when the CEO opens their checkpoint in person (M17).

    **Derived here, inside the step, and never issued from a command handler.** `REQUEST_RAISED` is
    in the fold's output set and is compared byte-for-byte against what `step()` reproduces, so a
    request emitted from a command path fails strict replay — the fold would regenerate nothing at
    that tick and find an event in the log. Three facts the fold already reproduces decide it: the
    CEO standing next to a director, an open checkpoint on an item in that director's line, and no
    statement already asked for that checkpoint.

    **Never per tick** (M17). The third condition is what makes that true: a request outstanding for
    the checkpoint suppresses the next one, and the window is `STATEMENT_OFFSET_TICKS` wide — two
    sim-days — so a CEO standing at a desk asks once and not thirty-six times a wall-second.

    **The cap is avoided rather than caught.** `raise_request` raises `RequestCapExceeded` past the
    cap, and an exception escaping `step()` is not a refused request, it is a dead clock: the tick
    task's done callback records it and readiness reports a stopped run. So the count is checked
    here, against the same helper the cap uses, and an item at its limit is skipped. It is still
    caught below as well, because the *run*-wide cap can bind on an item that is under its own.
    """
    events: list[Emitted] = []

    for director_id in state.scenario.directors:
        if director_id in state.departed or director_id not in state.people:
            continue
        if not _ceo_is_beside(state, state.people[director_id]):
            continue

        open_checkpoint = _open_checkpoint_in_line(state, director_id)
        if open_checkpoint is None:
            continue
        item_id, cp_index = open_checkpoint

        if _already_asked(state, item_id, cp_index, director_id):
            continue
        if pend.outstanding_for_item(state.pending, item_id) >= pend.MAX_OUTSTANDING_PER_ITEM:
            continue

        # No guard around this. `authorized_for` puts the director in their own scope, so the empty
        # scope `Authorized` refuses is unreachable from here — `director_id` comes from
        # `scenario.directors` and cannot be blank. The refusal exists for `Authorized.from_payload`,
        # which builds one from a payload nothing in this process wrote.
        #
        # Widened by a granted Authorization on this item, and recorded widened (U15, M39). The leg
        # is told what it may read and the landing-tick check re-derives the same thing, so a grant
        # that arrived between the two is the one case where the recorded scope is narrower than the
        # one the statement is judged against — never wider, which is the direction that would
        # matter.
        authorized = authorized_scope(state, director_id, for_item=item_id)

        try:
            events.extend(
                raise_request(
                    state,
                    service=pend.BENCH,
                    owning_item=item_id,
                    request_id=pend.statement_request_id(
                        director_id, item_id, cp_index, state.tick
                    ),
                    subject={
                        # Named on the event because the sequence diagram names it, and because
                        # the leg has to know whose persona to speak in and which scope to query
                        # under. The scope is recorded rather than left implicit so a log reader
                        # can audit R23 without re-deriving the roster.
                        "person": director_id,
                        "cp_index": cp_index,
                        "scope": authorized.to_payload(),
                    },
                    deadline_ticks=pend.STATEMENT_DEADLINE_TICKS,
                )
            )
        except pend.RequestCapExceeded:
            # The run-wide cap. Deterministic, so this is a run that has genuinely stopped being
            # answered; the briefing is skipped and `diagnose()` reports what is outstanding.
            continue

    return events


def _already_asked(state: State, item_id: str, cp_index: int, director_id: str) -> bool:
    """Whether a statement is outstanding for this checkpoint.

    Read off `state.pending` rather than off a marker on the item, and that is a deliberate limit
    rather than an oversight: an "already briefed" flag would be a new field in
    `ItemRuntime.to_state()`, which R27 makes a `STATE_SHAPE_VERSION` move, and this unit is neither
    of the two changes the plan says may move it.

    What it costs, stated exactly, because the loose version of it is wrong: a CEO who stands at the
    same desk past the landing tick without settling the checkpoint is briefed again, once per
    `STATEMENT_OFFSET_TICKS`. The per-item cap does *not* bound the total — it bounds how many are
    outstanding at once, and each answer clears one — so the bound is the run's length over the
    window, about ten in a twenty-sim-day run rather than three. Each is a fresh turn two sim-days
    apart rather than a retry of a refused one, which is the distinction the plan's "never a retry
    loop" draws; U15 owns the state-shape move that would let it be once.
    """
    return any(
        request.is_statement
        and request.owning_item == item_id
        and request.request_id
        == pend.statement_request_id(director_id, item_id, cp_index, request.raised_at_tick)
        for request in state.pending.values()
    )


def _ceo_is_beside(state: State, person: PersonRuntime) -> bool:
    """Whether the CEO is standing within conversation range of this person.

    The same radius the client opens a conversation at — `OPEN_RADIUS_MILLI` in
    `frontend/src/ui/conversation-model.ts` — because the pending block appears on the panel that
    radius opens, and a kernel with a radius of its own would raise requests at distances the client
    never shows a panel at.

    **Necessary, and not sufficient**, so the claim is bounded here rather than overstated. The
    client picks a *single* nearest person and holds them out to `CLOSE_RADIUS_MILLI` with a
    `SWITCH_MARGIN_MILLI` steal margin; this loops over every director. Two directors inside 1.9
    tiles, or a held conversation retained at 2.4 tiles while a second director enters at 1.8, both
    raise a request the client is not currently rendering a panel for. Sharing the radius is what
    makes the common case agree; U11's surface decides what to do with the uncommon one, and the
    request is on the log either way.

    Compared as *squared* milli-tiles rather than through a square root. `Math.hypot` is a float and
    no float may cross this kernel (R6); squaring both sides is the only form that is exact in
    integers and agrees with the client everywhere except exactly on the boundary, where a float
    rounding of an irrational distance was never going to be reproducible anyway.

    The person's position is their tile, scaled, while the CEO's is milli-tiles: the CEO is derived
    from logged input at milli-tile resolution and a walker's position is a tile per tick, so this is
    a half-tile quantisation on one side of the comparison and not an approximation of either.
    """
    dx = state.ceo.x_milli - person.pos[0] * MILLI
    dy = state.ceo.y_milli - person.pos[1] * MILLI
    return dx * dx + dy * dy <= STATEMENT_RANGE_MILLI * STATEMENT_RANGE_MILLI


def _open_checkpoint_in_line(state: State, director_id: str) -> tuple[str, int] | None:
    """The item in this director's line that is stopped at a checkpoint, and which one.

    Iterated in `state.items` order, which is the authored catalog order followed by any item
    created at runtime — deterministic, and the order the rest of this module reads the work graph
    in. The first one wins: two items in one line stopped at once is a real state, and briefing on
    the earlier of them is a choice the fold reproduces rather than a coin toss.
    """
    for item_id, item in state.items.items():
        if item.status != STATUS_BLOCKED or not item.assignee:
            continue
        if state.line_of_assignee(item.assignee) != director_id:
            continue
        cp_index = state.people[item.assignee].cp_index
        if cp_index < 0:
            continue
        return (item_id, cp_index)
    return None


def needed_line(state: State, item_id: str) -> str:
    """The reporting line whose knowledge this item's work depends on, or the empty string (M40).

    **Derived from folded state and authored content, never asked for.** A request the CEO answers
    has to be something `step()` reproduces, because `REQUEST_RAISED` is an output the fold
    regenerates and compares byte-for-byte — so "who needs whose knowledge" is a function of the
    run, not a claim a service or a client makes. Nothing outside this file can cause an
    Authorization to be raised.

    Two ways an item ends up being about another line, both of them ordinary:

    **A hire is always about the line it hires into.** `request_hire` routes recruitment through
    People — the item is the recruiter's work — while what it is *for* is another director's line:
    who left, what is queued, how far over the ceiling they are. That is the shape of the mechanic
    at its plainest, and it is reachable from the shipped client in two clicks.

    **Work handed to somebody outside its authored line takes its history with it.** An item's
    authored `want` says whose work this is; when the CEO hands it to a specialist in another line,
    the director now holding it has none of what was learned about it. `assign_direct` accepts any
    person, so this is the kernel's own reading of the org chart rather than a rule the client
    enforces.

    Neither source needs a scenario to author anything, which is deliberate: a mechanic that only
    fired on content somebody remembered to write would be a mechanic that never fired. The empty
    string means "this item needs nobody else", which is the common case and costs one dictionary
    lookup and one comparison per in-flight item per tick.
    """
    item = state.items.get(item_id)
    if item is None or not item.assignee or item.status not in IN_FLIGHT:
        return ""

    holder = state.line_of_assignee(item.assignee)
    if not holder:
        return ""

    about = _line_the_work_is_about(state, item_id)
    # Not another line, not a line at all, or a director this company does not have: all three are
    # "nobody to ask", and answering them the same way is what keeps this from raising a request
    # naming a person no surface can render.
    if not about or about == holder or about not in state.scenario.directors:
        return ""
    return about


def _line_the_work_is_about(state: State, item_id: str) -> str:
    """Whose line this item's work concerns, regardless of who is holding it.

    The hire lookup comes first because a hiring item's authored `want` is the *recruiter*, whose
    line is the one already holding it — so reading the spec first would answer "its own line" for
    the one case this mechanic exists for most plainly.
    """
    for hire in state.hires.values():
        if hire.item_id == item_id:
            return hire.director_id

    spec = state.spec_of(item_id)
    if not spec.want or spec.want not in state.people:
        return ""
    return state.line_of_assignee(spec.want)


def _raise_authorization_requests(state: State) -> list[Emitted]:
    """Ask the CEO whether a line may read another's, for every item that needs it (M40).

    **Inside the step, for the reason every other request is** (R2, R17). `REQUEST_RAISED` is in the
    fold's output set and is compared byte-for-byte against what the step reproduces, so a request
    raised from a command handler would fail strict replay — and an Authorization raised by a
    *client* would be the mechanic asking itself for permission.

    Four conditions decide it, each of which the fold reproduces: the item is in flight and needs
    another line (`needed_line`), nothing is already outstanding or granted for it, enough of the
    run has passed since a refusal for the director to ask again, and the caps allow the request.

    **The cap is avoided rather than caught**, exactly as `_raise_statement_requests` does it:
    `RequestCapExceeded` escaping `step()` is not a refused request, it is a dead clock. An item at
    its limit is skipped and asks on a later tick — and the item is stopped meanwhile, so the player
    sees a stalled item rather than a request that silently disappeared.

    Iterated in `state.items` order — the authored catalog followed by anything created at runtime —
    so two runs of one seed raise the same requests in the same order, which is what the strict
    comparison is comparing.
    """
    events: list[Emitted] = []

    for item_id in list(state.items):
        needs = needed_line(state, item_id)
        if not needs:
            continue

        item = state.items[item_id]
        asking = state.line_of_assignee(item.assignee)
        # A director who has left cannot ask, and one this run never seated cannot either. The item
        # keeps whatever record it has: a stall is not lifted by the asker departing, because the
        # knowledge is still missing and the work is still the line's.
        if asking in state.departed or asking not in state.people:
            continue

        standing = state.authorizations.get(item_id)
        if standing is not None:
            if standing.status != authz.REFUSED:
                continue
            if not standing.ready_to_ask_again(
                state.tick, after=pend.AUTHORIZATION_REASK_TICKS
            ):
                continue
            if standing.needs != needs:
                # The work changed lines while it was stopped. The old refusal answered a question
                # about a line this item is no longer about, so it does not carry: this is a fresh
                # ask with its own count, not the second ask of the old one.
                standing = None

        if pend.outstanding_for_item(state.pending, item_id) >= pend.MAX_OUTSTANDING_PER_ITEM:
            continue

        request_id = authorization_request_id(item_id, needs, state.tick)
        try:
            events.extend(
                raise_request(
                    state,
                    service=pend.CEO,
                    owning_item=item_id,
                    request_id=request_id,
                    subject={
                        # Everything the tray card names, so the CEO can answer it from across the
                        # floor (M40): who is asking, whose knowledge they want, and which item
                        # stops until it is answered. Recorded on the event rather than looked up
                        # by a surface, because a client that derived it would be a second opinion
                        # about the org chart.
                        "person": asking,
                        "needs": needs,
                        "asks": 1 if standing is None else standing.asks + 1,
                        # The work's name, because the surface has no other way to get it for the
                        # commonest case. A hiring item is created at runtime and is in no catalog,
                        # so a client rendering the card from the genesis payload showed the CEO
                        # `wi_hire-hire_sales_1` and asked them to decide about it. Found by opening
                        # the page rather than by any suite: every test named an authored item.
                        "title": state.spec_of(item_id).title,
                    },
                    deadline_ticks=pend.AUTHORIZATION_DEADLINE_TICKS,
                )
            )
        except pend.RequestCapExceeded:
            # The run-wide cap, which can bind on an item that is under its own. Deterministic, so
            # this is a run that has genuinely stopped being answered; the item stays stopped and
            # `diagnose()` reports what is outstanding.
            continue

        state.authorizations[item_id] = authz.Authorization(
            item_id=item_id,
            asking=asking,
            needs=needs,
            request_id=request_id,
            status=authz.OUTSTANDING,
            raised_at_tick=state.tick,
            asks=1 if standing is None else standing.asks + 1,
        )

    return events


def authorization_request_id(item_id: str, needs: str, tick: int) -> str:
    """The id of the Authorization request for this item, this line and this tick.

    Derived from state the fold reproduces, because the request is (R17): `step()` raises it and has
    no sequence number to hand, so the three facts that identify *which* question this is stand in
    for one. The tick is what separates a second ask from the first, which is why it is in here and
    why two asks are two rows in the report rather than one row asked twice.

    It carries `statement_request_id`'s limitation unchanged and for the same reason — `State` holds
    no run id, so a parent and a fork standing in the same state mint the same id — and the same
    thing keeps it safe: an answer names the run it is for, and `decide_authorization` looks the
    request up in *that* run's `pending`.
    """
    return pend.request_id_for(f"authorization:{item_id}:{needs}", tick)


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

    The window is read off the request rather than from the module constant, because a statement's
    is four times the shared one (R18) and a message naming the wrong number is a diagnostic that
    sends the reader to the wrong constant. For a period consult it reproduces today's text exactly.
    """
    events: list[Emitted] = []

    for request_id in [
        request_id
        for request_id, request in state.pending.items()
        if request.overdue(state.tick)
    ]:
        request = state.pending.pop(request_id)

        # An abandoned Authorization is a refusal, not a third state (U15, M41). The CEO was asked,
        # the run waited a window, and the item stays stopped — which is the same consequence a
        # refusal has and is the reason silence is not allowed to mean something else here. The
        # record keeps which of the two it was, because the report prints it and because "I said no"
        # and "I never looked" read differently to a person.
        standing = state.authorizations.get(request.owning_item)
        if request.is_authorization and standing is not None and standing.request_id == request_id:
            authz.refuse(standing, state.tick, abandoned=True)

        # A statement is never escalated, because a briefing is not a decision: the checkpoint the
        # director was going to talk about is already the CEO's to settle, and flagging it would put
        # a second claim on the tray for something that was never off it. An Authorization is not
        # escalated either, and for the stronger reason: it was the CEO's question from the moment
        # it was raised, so escalating it to them would be the tray pointing at itself.
        escalated = (
            not request.is_statement
            and not request.is_authorization
            and request.owning_item != pend.SYNTHETIC_PERIOD_ITEM
        )
        events.append(
            Emitted(
                kind=EventKind.ANSWER_REJECTED,
                payload={
                    "tick": state.tick,
                    "reason": (
                        f"no answer within {request.deadline_tick - request.raised_at_tick} "
                        "ticks of being raised"
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
                "decision_supply": lifecycle.decision_supply(state.scenario),
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


def draw_cost_of(monthly_hours: int) -> int:
    """What a day of a recurring draw costs, for that many authored monthly hours (R60).

    Split out of `day_cost_terms` because U21 needs the *counterfactual*: an automation proposal
    states what the burn would be with some of the draw gone, and the only honest way to say that
    is to run the company's own arithmetic over a smaller number. A report that multiplied out
    its own copy would be a third opinion about what a draw costs — and the first tuning pass to
    move `draw_cost_per_monthly_hour` would leave the prescription quoting a saving the day
    boundary never paid.

    Integer division at the end, not per department, which is why the report has to call this on
    a *total* rather than on the hours it wants to remove: the saving is the difference between
    two totals, and two roundings do not subtract to one.
    """
    return monthly_hours * TUNING["draw_cost_per_monthly_hour"] // 100


def day_cost_terms(state: State) -> tuple[int, int, int]:
    """What a day costs this company: fixed, the recurring draw, and salaries.

    Named and public because three callers need it and a second implementation would drift. The
    day boundary applies it; a comparison branch divides cash by it to state a runway at its
    stopping tick, and the Universe report divides by it twice to state what an automation would
    give back. A runway computed from a second copy of this arithmetic would disagree with the
    burn the same branch actually paid.
    """
    fixed = TUNING["fixed_cost_per_day"]
    draw_cost = draw_cost_of(cap.manual_hours(state.capacity))
    return fixed, draw_cost, hiring.salary_total(state.hires)


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
    fixed, draw_cost, salaries = day_cost_terms(state)
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
            state.scenario, state.morale, director, set(state.present_members(director))
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
    released: list[Emitted] = []
    if person is not None and person.item_id:
        item = state.items[person.item_id]
        item.status = STATUS_BACKLOG
        item.assignee = ""
        returned = item.id
        person.item_id = ""
        person.state = STATE_IDLE
        released = _drop_authorization(
            state,
            returned,
            "the person holding the item left, so there is no work left for an Authorization "
            "to unblock",
        )

    return released + [
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
            return _walk_to(state, person, person.seat, ARRIVE_RESUME_WORK)
        return []

    if person.state != STATE_WORKING or not person.item_id:
        return []

    item = state.items[person.item_id]
    spec = state.spec_of(person.item_id)
    if item.status == STATUS_BLOCKED:
        return []

    # **The stall U15 built, and the whole consequence of a refusal** (M41, R24). An item whose
    # Authorization is outstanding or refused burns nothing: no effort, no checkpoint, no meeting
    # and no delivery. It is read off the item's own record rather than off `state.pending`, so a
    # refusal keeps stalling after the question has left the outstanding table — which is the
    # difference between "we are waiting for an answer" and "the answer was no".
    #
    # The item's *status* is deliberately left alone. `blocked` means stopped at a checkpoint and
    # the tray renders exactly that set; an Authorization is its own card with its own actions, and
    # borrowing the status would put a decision in the tray that has no options on it.
    standing = state.authorizations.get(item.id)
    if standing is not None and standing.stalls:
        return []

    item.done_units += _burn_this_tick(state, person, item)
    total = spec.effort_units

    # Cross-department work needs a meeting before it can go further.
    if spec.visit_meeting and not item.visited and work.visit_meeting_due(item.done_units, total):
        item.visited = True
        meeting = state.floor.room("meeting")
        destination = meeting.visit or (meeting.x1 + 1, meeting.y2)
        return _walk_to(state, person, destination, ARRIVE_ENTER_MEETING)

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
    if state.rank_of(person.id) == "director":
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
        f"{state.name_of(person.id)} — {spec.effort_hours} hours of work, "
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

    # What it asked the CEO for, which finished work no longer needs. A granted Authorization is
    # spent here rather than kept: M42 gives permission to a request, and the request ends with the
    # work it was about.
    events.extend(
        _drop_authorization(
            state, item.id, "the work finished before the CEO answered"
        )
    )

    # A finished hiring item seats the arrival, or refuses with a reason (R25).
    events.extend(_complete_hire(state, item.id))
    _refresh_load(state)

    # What this unlocks is announced by the step, not from here. Completing an item is only one
    # of the ways a gate clears — a Visibility gain from asking is another — and an announcement
    # made by whichever command moved the number cannot be regenerated by a replay that
    # re-issues that command and then compares the step's own output against the log.
    return events


def _newly_available(state: State) -> list[str]:
    """Backlog items whose gates are now clear, in item order."""
    return [
        item.id
        for item in state.scenario.items
        if state.items[item.id].status == STATUS_BACKLOG
        and item.requires != work.Requires()
        and is_unlocked(state, item.id)
    ]


def _announce_unlocks(state: State) -> list[Emitted]:
    """Say what has become available since the last time anyone looked.

    Once per item for the life of the run: an item that unlocks, gets assigned and is later
    returned to the backlog is already known to be available, and re-announcing it would make
    "unlocked" read as a repeating event rather than as a threshold crossed.
    """
    already = set(state.announced_unlocks)
    fresh = [item_id for item_id in _newly_available(state) if item_id not in already]
    if not fresh:
        return []

    state.announced_unlocks = sorted(already | set(fresh))
    return [
        Emitted(kind=EventKind.ITEM_UNLOCKED, payload={"tick": state.tick, "item": item_id})
        for item_id in fresh
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
) -> list[Emitted]:
    """Resolve a path and announce it. One event per walk, and none per tick (R15).

    The path plus the tick it started on is the whole of the walk: position is a function of
    `tick - path_start_tick`, as `_advance_walker` above is, so a client holding both derives
    every intermediate position itself. Emitting per tick instead would put roughly 36 rows per
    walker per wall second into an append-only log to restate what one row already said — the
    same arithmetic that collapsed the CEO's movement to one event per keypress.

    **Unconditional, including when the path came back empty.** `find_path` returns nothing for
    a walker already standing on the destination, and it would be tempting to stay silent for
    that case. A walk that sometimes emits and sometimes does not is a walk whose absence from
    the log cannot be told apart from a dropped event, either by the strict replay's count or by
    a reader; one row on a case that barely arises is the cheaper half of that trade.
    """
    person.path = tuple(find_path(state.floor, person.pos, destination))
    person.path_start_tick = state.tick
    person.arrive = intent
    person.state = STATE_WALKING

    return [
        Emitted(
            kind=EventKind.STAFF_MOVED,
            payload={
                "tick": state.tick,
                "person": person.id,
                # Where the walk begins. The path excludes the origin — `find_path`'s
                # convention, because arrival is detected by the path emptying — so without
                # this the client has nothing to interpolate the first tile *from*, and a
                # resync's `pos` is where the walker has already got to rather than where
                # they set off.
                "from": list(person.pos),
                "path": [list(tile) for tile in person.path],
                # The same number as `tick`, and carried anyway because R15 names it. `tick`
                # is where this event sits in sim-time, which is what every payload here means
                # by it; `start_tick` is what a client subtracts from the tick it is drawing.
                # They are equal because a walk is announced on the tick it is resolved, and a
                # reader deriving one from the other would be depending on that staying true.
                "start_tick": person.path_start_tick,
                # What the walker is carrying. The org chart's progress row reads this: a
                # person's item reaches the client on no other event, so the row was marked
                # and unreachable before this.
                "item": person.item_id,
                # The state the walk ends in, stated by the kernel rather than mapped by the
                # client from the arrival intent. `_arrive` reads the same table, so "what
                # does this walk lead to" has one answer rather than one per language — and
                # without it a client would have no way to stop showing somebody as walking,
                # because arrival is in no event either.
                "then": ARRIVE_STATE[intent],
            },
        )
    ]


def _arrive(state: State, person: PersonRuntime) -> list[Emitted]:
    intent = person.arrive
    person.arrive = ARRIVE_NONE
    # One table, read here and by the movement event's `then`. The hand-off branch below
    # re-enters `_walk_to`, which sets `walking` again; the table's answer for that intent is
    # the same, so the two agree rather than one overwriting the other.
    person.state = ARRIVE_STATE.get(intent, STATE_IDLE)

    if intent == ARRIVE_ENTER_MEETING:
        person.met_ticks = 0
        return []

    if intent == ARRIVE_HANDOFF:
        item_id = person.arrive_item
        person.arrive_item = ""
        person.item_id = ""
        item = state.items[item_id]
        staff = state.people[item.assignee]

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
        # The director walks home; the specialist starts. Both may produce a walk, and both
        # come after the hand-off itself: the assignment is the fact, and the movement is what
        # the fact caused.
        events.extend(_walk_to(state, person, person.seat, ARRIVE_SIT_IDLE))
        events.extend(_start_work(state, staff, item))
        # Stamped after the work starts, not before: at the point the payload was built the
        # item was still `assigned`, and a read-side consumer told that would move its node
        # to a status the item left in the same operation, with no later event to correct it.
        events[0].payload["item_status"] = item.status
        return events

    return []


def _start_work(state: State, person: PersonRuntime, item: ItemRuntime) -> list[Emitted]:
    person.item_id = item.id
    item.status = STATUS_ACTIVE

    if person.pos != person.seat:
        return _walk_to(state, person, person.seat, ARRIVE_START_WORK)

    person.state = STATE_WORKING
    return []


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
    if at_tick > state.tick + MAX_INPUT_LEAD_TICKS:
        # Pruning only drops inputs the clock has passed, so an unbounded lead would let the
        # scanned-every-tick dict grow for the life of the run.
        raise CommandRejected(
            f"input is for tick {at_tick}, more than {MAX_INPUT_LEAD_TICKS} ticks ahead "
            f"(now {state.tick})"
        )

    # Supersession follows *submission* order, not tick order. The two can disagree: the
    # client's lead is a wall-time budget converted at the current rate, so changing rate
    # mid-hold can tag a release for an earlier tick than the press it supersedes. With only
    # "the most recent input at or before this tick", the release would pass and the older
    # press would then resurrect — the CEO walking off on a key nobody is holding.
    for scheduled in [tick for tick in state.ceo_inputs if tick >= at_tick]:
        del state.ceo_inputs[scheduled]

    state.ceo_inputs[at_tick] = bitmask
    return [
        Emitted(
            kind=EventKind.CEO_INPUT,
            payload={"tick": at_tick, "bitmask": bitmask, "submitted_at_tick": state.tick},
        )
    ]


def ask_person(state: State, person_id: str, question: str) -> list[Emitted]:
    """Ask someone one of the four questions, and record what they said.

    The kernel matches the typed text rather than trusting a slot from the client, because it
    is the side that *prices* the answer: charging Visibility once per person per question
    means knowing which question was asked, and a classification the kernel cannot check is a
    number it cannot defend.

    A miss is not a rejection. Nothing is recorded and nothing moves, but the person still says
    something, and the log is still the honest record of what the CEO asked whom — which is
    what this command exists to be.
    """
    person = state.person(person_id)

    if person_id in state.departed:
        # They left. Their desk is still on the floor and their runtime is kept so the log
        # stays interpretable, but a company cannot go on learning from someone who resigned —
        # and Visibility is the metric that would otherwise keep paying out for it.
        raise CommandRejected(f"{person_id} has left the company")

    if len(question) > MAX_QUESTION_CHARS:
        raise CommandRejected(
            f"that question is {len(question)} characters; the limit is {MAX_QUESTION_CHARS}"
        )

    # Everything that can fail is resolved *before* anything is mutated, and this ordering is
    # the whole point rather than a style preference. `state.people` contains arrived hires,
    # who are not on the authored roster and have no script — so looking their answer up after
    # charging Visibility raises a bare KeyError, past the point where `CommandRejected` is
    # caught, leaving state changed with no event to explain it. A state hash that moves with
    # nothing in the log is replay identity broken silently, which is the one failure this
    # kernel is built to prevent.
    try:
        deflection = state.scenario.deflection_of(person_id)
    except KeyError:
        raise CommandRejected(
            f"{person_id} joined after the run started and has nothing scripted to say yet"
        ) from None

    intent = roster.match_intent(question)

    if intent is None:
        return [
            Emitted(
                kind=EventKind.QUESTION_ANSWERED,
                payload={
                    "tick": state.tick,
                    "person": person_id,
                    "asked": question,
                    "matched": False,
                    "question": "",
                    "label": "",
                    "answer": deflection,
                    "tacit": False,
                    "first_time": False,
                    "deltas": {},
                    "metrics": dict(state.metrics),
                },
            )
        ]

    # Resolved before the mutations below, for the reason stated above.
    answer = state.scenario.voice_of(person_id, intent.slot)

    # First time for *this person and this question*. Someone else having answered "why" costs
    # nothing here — the knowledge is theirs, not the company's.
    first_time = intent.slot not in person.answered
    if first_time:
        # Re-sorted rather than appended: the list is hashed state, and two runs that heard the
        # same questions in a different order have to hash the same.
        person.answered = sorted([*person.answered, intent.slot])

    effective: dict[str, int] = {}
    if first_time and intent.tacit:
        _, effective = effects.apply_effect(
            state.metrics, {"visibility": TUNING["visibility_per_tacit_answer"]}
        )

    # What this gain unlocks is announced by the next step rather than from here. See
    # `_announce_unlocks`: an announcement made by the command cannot be regenerated by a
    # replay that re-issues the command and compares the step's output against the log.
    return [
        Emitted(
            kind=EventKind.QUESTION_ANSWERED,
            payload={
                "tick": state.tick,
                "person": person_id,
                "asked": question,
                "matched": True,
                "question": intent.slot,
                "label": intent.label,
                "answer": answer,
                "tacit": intent.tacit,
                "first_time": first_time,
                "deltas": effective,
                "metrics": dict(state.metrics),
            },
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

    director_id = state.line_of(spec.want)
    if director_id == spec.want:
        # The wanted person *is* the director; there is nobody to route through.
        return assign_direct(state, item_id, spec.want)

    staff = state.person(spec.want)
    director = state.person(director_id)
    item.status = STATUS_ASSIGNED
    item.assignee = staff.id

    director.item_id = item_id
    walked = _walk_to(state, director, staff.seat, ARRIVE_HANDOFF)
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
        ),
        # The walk the assignment causes, after the assignment itself. This is the event that
        # makes delegation visible: it is the whole of M62's headline case, and the client draws
        # the director crossing the floor from it.
        *walked,
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

    manager = state.manager_of(person_id)
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
    events.extend(_start_work(state, person, item))
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
    # Compared through `line_of_assignee` rather than through the authored roster, so an arrived
    # hire has a line here too. Identical for every authored person; the roster lookup it
    # replaces raised `KeyError` for a hire, past where `CommandRejected` is caught.
    if state.line_of_assignee(current) != state.line_of_assignee(person_id):
        raise CommandRejected(
            f"{state.name_of(person_id)} is in a different reporting line from "
            f"{state.name_of(current)}; reassignment across lines is not permitted"
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
    events.extend(_start_work(state, person, item))
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

    # Before the event, so the log reads in the order the state changed: the item goes back, and
    # what it had asked the CEO for goes with it.
    released = _drop_authorization(
        state,
        item_id,
        "the item went back to the backlog, so there is no work left for an Authorization to "
        "unblock",
    )

    return released + [
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

    **Who recruits comes from the scenario.** This function named `stf_rec` and the `hr` room
    outright until U6 — two default-scenario values sitting in the kernel, so a second company
    would have tried to assign hiring work to somebody it does not have and raised mid-command.
    `Scenario.recruiter` derives it: the People line's first non-director in roster order, or its
    director if the line is one person. The item's room follows the recruiter's own desk, which
    reproduces `hr` for the shipped company without naming it.
    """
    if director_id not in state.capacity:
        raise CommandRejected(f"{director_id} is not a department")

    ordinal = 1 + sum(1 for hire in state.hires.values() if hire.director_id == director_id)
    person_id = hiring.hire_person_id(director_id, ordinal)
    request_id = f"hire-{person_id}"
    item_id = f"wi_{request_id}"

    if request_id in state.hires:
        raise CommandRejected(f"{person_id} is already being hired")

    target_room = state.scenario.room_of_line(director_id)
    recruiter = state.scenario.recruiter()
    spec = work.ItemSpec(
        id=item_id,
        title=f"Hire into {target_room}",
        brief=f"Recruit and onboard one person for {state.name_of(director_id)}'s line.",
        dept=state.scenario.person(recruiter).dept,
        want=recruiter,
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
    assigned = assign_direct(state, item_id, recruiter)
    for event in assigned:
        if event.kind is EventKind.WORK_ASSIGNED:
            # **The mark that stops this assignment being applied twice**, and it closes a defect
            # that made any run which ever hired unfoldable. `HIRE_REQUESTED` is an input the fold
            # re-issues, and re-issuing it calls `assign_direct` — which produces this very event.
            # Without a mark the fold *also* classified this `WORK_ASSIGNED` as an input and applied
            # it a second time, where it found the item already active and raised `CommandRejected`
            # out of the fold. Measured before the fix: a run that requested one hire could not be
            # replayed, reported or forked, and the report answered 500.
            #
            # The same shape as `handoff_completed` and for the same reason: `is_output` asks "did
            # another input derive this?", and one command deriving an event another command also
            # produces is exactly the case that predicate exists for.
            event.payload["for_hire"] = True
    events.extend(assigned)
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
        state.floor, state.scenario.room_of_line(hire.director_id), taken
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


def decide_authorization(state: State, request_id: str, *, granted: bool) -> list[Emitted]:
    """Grant or refuse one director's request to read another line (M40, M42).

    **A command rather than an answer on the pending-input contract's delivery path**, and the
    difference is the answerer. A statement is delivered by a leg on a thread and lands at a derived
    tick two sim-days later; this is a person pressing a button, so it carries an idempotency key,
    it is applied at the tick it is issued at, and a retry is answered with the original outcome
    rather than applied twice (R30). It is also why a duplicate is a rejection here rather than a
    logged `ANSWER_REJECTED`: the deferred defect that makes a duplicate answer unreplayable is
    reached through `receive_answer`'s `request is None` branch, and a player double-clicking Grant
    is a far likelier duplicate than a service answering twice.

    **The verdict is applied to the record, not to the request.** The request leaves `state.pending`
    because it has been answered; the record stays, because it is what `_advance_work` reads and
    what the next ask counts from. That split is what lets a refusal go on stalling an item after
    the question is gone, and what keeps `pending` meaning exactly "asked and unanswered".

    Rejected, mutating nothing, in three cases, and each is a sentence a client shows:

    * the request is not outstanding — answered already, abandoned at its deadline, or never raised;
    * the item is no longer somebody's to do, so there is nothing left for the answer to unblock.
      **This is the "granted after the item completed" case** (M41's neighbour): an item that
      finishes clears its record, so a grant arriving afterwards finds no request and is refused
      with the reason rather than recorded as a permission over finished work;
    * the record and the request disagree about which question is open, which means the state was
      edited by something other than this module.
    """
    request = state.pending.get(request_id)
    if request is None or not request.is_authorization:
        raise CommandRejected(
            f"no outstanding Authorization request {request_id!r}: it was already answered, "
            "abandoned at its deadline, or never raised. Nothing was changed."
        )

    record = state.authorizations.get(request.owning_item)
    if record is None or record.request_id != request_id:
        raise CommandRejected(
            f"the Authorization record for {request.owning_item} does not name request "
            f"{request_id!r}, so there is no question here to answer"
        )

    item = state.items.get(request.owning_item)
    if item is None or item.status not in IN_FLIGHT or not item.assignee:
        raise CommandRejected(
            f'"{state.spec_of(request.owning_item).title}" is no longer in flight, so there is '
            "nothing left for an Authorization to unblock. The request is closed."
        )

    del state.pending[request_id]
    if granted:
        authz.grant(record, state.tick)
    else:
        authz.refuse(record, state.tick)

    return [
        Emitted(
            kind=EventKind.AUTHORIZATION_DECIDED,
            payload={
                "tick": state.tick,
                "request": request_id,
                "item": record.item_id,
                # Named `person` for the asking director because every other event that names a
                # person spells it this way, and a surface reading the six-kind convention should
                # not have to learn a fifth word for "who this is about".
                "person": record.asking,
                "needs": record.needs,
                "granted": granted,
                "asks": record.asks,
                "raised_at_tick": record.raised_at_tick,
                # What the answer did to the work, stated by the kernel rather than inferred by a
                # client from the verdict. The two are the same today and a surface that derived one
                # from the other would be a second copy of `Authorization.stalls`.
                "item_stalled": record.stalls,
                "item_status": item.status,
            },
        )
    ]


def _drop_authorization(state: State, item_id: str, reason: str) -> list[Emitted]:
    """Forget what this item asked for, because there is no work left for the answer to unblock.

    Called where an item stops being somebody's — it delivered, it went back to the backlog, or
    attrition took its assignee. Three consequences, and the third is the one worth stating:

    * the record goes, so a later assignment starts from no answer rather than from an old one. A
      refusal is about work in flight, and work that went back to the backlog and came out again is
      a different situation the CEO may answer differently;
    * any outstanding request goes with it, because nothing is waiting on it any more;
    * and that removal is **announced**. `ANSWER_REJECTED` is an output the fold regenerates, so a
      request that vanished from `state.pending` with nothing in the log to say why would leave the
      log's own projection of what is outstanding disagreeing with the state it folds to.
    """
    record = state.authorizations.pop(item_id, None)
    if record is None:
        return []

    request = state.pending.pop(record.request_id, None) if record.request_id else None
    if request is None:
        return []

    return [
        Emitted(
            kind=EventKind.ANSWER_REJECTED,
            payload={
                "tick": state.tick,
                "reason": reason,
                "owning_item": item_id,
                "service": request.service,
                "abandoned": False,
                "escalated_to_ceo": False,
                "deferred_period": request.period_index,
            },
            request_id=record.request_id,
        )
    ]


def checkpoint_not_reached(
    item: ItemRuntime, spec: work.ItemSpec, checkpoint: work.Checkpoint
) -> str:
    """Why this checkpoint cannot be acted on yet, or the empty string if it can.

    Shared by `resolve_checkpoint` and the comparison's guard because the arithmetic is easy to
    copy slightly wrong: the progress figure cross-multiplies and has to survive an item with
    zero effort, and the two callers were carrying identical copies of it. Returns the sentence
    rather than raising, so each caller keeps its own ending — a decision adds "Nobody is
    waiting on you for it", and a comparison has its own reason to give.
    """
    if work.checkpoint_reached(item.done_units, spec.effort_units, checkpoint.at_percent):
        return ""
    progress = item.done_units * 100 // spec.effort_units if spec.effort_units else 0
    return (
        f'"{spec.title}" has not reached that decision point yet — it is at {progress}% '
        f"of {checkpoint.at_percent}%."
    )


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
    not_reached_yet = checkpoint_not_reached(item, spec, checkpoint)
    if not_reached_yet:
        raise CommandRejected(f"{not_reached_yet} Nobody is waiting on you for it.")

    option = checkpoint.options[option_index]
    item.resolved[cp_index] = True

    # R59: an authored effect names a recurring-draw change, not a manual-hours delta. The
    # metric follows from the draws, so applying both would double-count.
    metric_effect, draw_delta = effects.split_draw(option.effect)
    morale_delta = metric_effect.pop("morale", 0)

    _, effective = effects.apply_effect(state.metrics, metric_effect)

    draw_effective = 0
    if draw_delta:
        director = work.director_for(state.scenario, spec)
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
    manager = state.manager_of(person.id)
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
                "draw_department": work.director_for(state.scenario, spec) if draw_delta else "",
                "metrics": dict(state.metrics),
                "item_status": item.status,
            },
        )
    ]


def compare_options(
    state: State,
    item_id: str,
    cp_index: int,
    person_id: str,
    at_tick: int,
    in_person: bool,
) -> list[Emitted]:
    """Run one branch per option at an open checkpoint, and record what was shown.

    The only command that changes nothing and still appends. That is the whole shape of it:
    R24 is satisfied because a branch is never written to the store, and this event exists so
    that the figures the CEO weighed are on the record afterwards rather than lost with the
    panel that showed them.

    **Everything that can fail is resolved before any branch runs.** The ordering `ask_person`
    states its reason for, applied here for a second reason as well: a branch costs about a
    tenth of a second, so a refusal that arrived after three of them would have spent that for
    nothing.

    **Staleness, as far as the state can prove it (R25, AE19).** The CEO asked about a specific
    situation — this person, stopped at this checkpoint, at the tick they were looking at — and
    the comparison is against a world that no longer exists if any of it has changed. Being
    *blocked at this checkpoint* is the authority, and it covers all four of the ways the
    situation moves: resolving it, completing the item, reassigning it and losing its assignee
    to attrition each take the item out of `blocked`. The person is checked too, because a
    reassignment followed by the work re-reaching the same checkpoint puts a *different* person
    in front of the same question.

    **The tick is bounded, not pinned.** The client tags from its render clock, which is the
    only authority it has for "now" — the store's tick is the last one the kernel said out loud
    and events land on about five ticks in twelve hundred, so tagging from it would put every
    comparison well into the run's past. The render clock is an estimate and legitimately runs
    ahead of the kernel's tick between position echoes, so refusing anything ahead at all would
    reject nearly every real comparison while catching nothing. What is refused is a tag that
    has run away — the same sim-day of lead `submit_ceo_input` allows, and absurdly generous
    against an echo interval of one sim-hour.

    This is deliberately not backed by a recorded blocked-at tick. Adding one would put a field
    into hashed state and bump the state-shape version, which invalidates every existing
    snapshot — a large price for a guard the three checks above already close.

    **The branch bound is a submission guard, not tuning** (R26). It bounds what one command
    may cause the kernel to do. A comparison changes no state, so its bound cannot change what
    a recorded run means, and it therefore stays out of `TUNING` and out of the rules version —
    where moving it would invalidate every kept snapshot and make current logs unfoldable.
    """
    # Imported here rather than at module scope: `simcore.compare` imports this module, and a
    # top-level import either way round is a cycle. The same shape `items.director_for` uses.
    from contracts import canonical
    from simcore import compare as branching

    item = state.item(item_id)
    spec = state.spec_of(item_id)

    if cp_index < 0 or cp_index >= len(spec.checkpoints):
        raise CommandRejected(f"{item_id} has no checkpoint {cp_index}")

    # Bounded on both sides. The forward half is the render clock running away; the backward
    # half is simply that a tick is unsigned, and without it a negative tag is written verbatim
    # into an append-only log as the thing the client claimed "now" was.
    if at_tick < 0:
        raise CommandRejected(f"a tick is unsigned; this comparison is tagged at {at_tick}")

    if at_tick > state.tick + MAX_INPUT_LEAD_TICKS:
        raise CommandRejected(
            f"this comparison is tagged at tick {at_tick}, more than "
            f"{MAX_INPUT_LEAD_TICKS} ticks ahead of the run's clock (now {state.tick}). "
            "Nothing has happened there yet to compare."
        )

    # Blocked *at this checkpoint*, which the person's own runtime records because `_block` set
    # it. Reading the item's status alone would accept a request about the second checkpoint on
    # an item stopped at the first.
    waiting = state.people.get(item.assignee)
    stopped_here = (
        item.status == STATUS_BLOCKED
        and waiting is not None
        and waiting.cp_index == cp_index
        and not item.resolved[cp_index]
    )
    if not stopped_here:
        raise CommandRejected(
            f'"{spec.title}" is no longer stopped at that decision, so this comparison is '
            "stale. Whatever moved it — you settled it, it finished, it was reassigned, or "
            "its owner left — the branches would be forked from a run that no longer exists."
        )

    if item.assignee != person_id:
        raise CommandRejected(
            f'"{spec.title}" is now with {item.assignee or "nobody"} rather than '
            f"{person_id}, so this comparison is stale. It reached that decision again with "
            "somebody else in front of it."
        )

    options = spec.checkpoints[cp_index].options
    if len(options) > MAX_BRANCHES_PER_COMPARISON:
        raise CommandRejected(
            f"that checkpoint offers {len(options)} options and a comparison runs at most "
            f"{MAX_BRANCHES_PER_COMPARISON} branches. Each branch steps the whole simulation "
            "to the next decision, so the bound is on what one command may cost."
        )

    # Every branch is computed before anything is appended, and every one of them is forked
    # from a *single* capture of the parent — `run_comparison` takes that capture once and
    # restores it per option. Calling the single-branch runner in a loop here would re-read the
    # parent per option, and the parent moves: the tick loop advances it on another thread with
    # no lock between them, so the columns would be forked from instants up to a sim-day apart.
    #
    # Outside any append transaction: the single writer holds one transaction per tick, and
    # branches inside it would stall every other run's clock and read as a store outage that is
    # not happening.
    summaries = branching.run_comparison(state, item_id, cp_index, in_person=in_person)
    branches = [summary.to_state() for summary in summaries]

    payload = {
        "tick": state.tick,
        "item": item_id,
        "cp_index": cp_index,
        "person": person_id,
        # What the client said it was looking at, kept beside the tick the branches actually
        # forked at. The two are usually equal and the record is worth nothing if it only
        # keeps the one the kernel chose.
        "requested_at_tick": at_tick,
        "in_person": in_person,
        "branches": branches,
    }

    encoded = len(canonical.encode(payload))
    if encoded > MAX_COMPARISON_PAYLOAD_BYTES:
        raise CommandRejected(
            f"that comparison encodes to {encoded} bytes and the limit is "
            f"{MAX_COMPARISON_PAYLOAD_BYTES}. Refused at the entry point rather than written: "
            "an append-only log cannot take a row back."
        )

    return [Emitted(kind=EventKind.OPTIONS_COMPARED, payload=payload)]


# =========================================================================
# The state hash's view of all this
# =========================================================================


def snapshot(state: State) -> dict[str, Any]:
    """Kernel state as the state hash sees it.

    Every declared subsystem is present. The ones U7 and U8 fill are empty rather than
    absent, so their arrival is a recorded state-shape change rather than a silent hash
    break.

    **`state.scenario` is deliberately not here**, and the omission is enforced rather than
    remembered: `hashing.state_hash` refuses an undeclared subsystem, so adding the company to
    this dictionary would require a `SHAPE_HISTORY` entry and a `STATE_SHAPE_VERSION` bump — a
    deliberate act with a recorded reason, not an accident. It should not be added. The scenario
    is an immutable fold-time input whose content hash the genesis event already records and all
    three R7 guards already check; hashing the same structure per day boundary would cost every
    golden fixture in the tree to carry a value that cannot move within a run.
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
        # U15. Its own subsystem rather than a field on each item, because it is a record of what
        # the CEO was asked and answered rather than a property of the work — and because a
        # divergence in *who may read what* localises to its own subtree at the next day boundary
        # instead of arriving as "items changed".
        "authorization": authz.to_state(state.authorizations),
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
            # Under lifecycle rather than as a subsystem of its own: this is a record of what
            # the run has already said, not a part of the world, and a new top-level key would
            # bump the state-shape version to carry a bookkeeping list.
            "announced_unlocks": sorted(state.announced_unlocks),
        },
    }
