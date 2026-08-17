"""Branch comparison: where an option travels, without the run going there.

A single delta says where a metric steps. It cannot say what an option opens or forecloses, or
how far the runway moves by the next decision — and those are the questions a CEO weighing
three options at a checkpoint is actually asking. So the CEO may run one branch per option,
forked at that tick and advanced to the first downstream checkpoint it raises (R21, R22).

**A branch is a function call against a copy of live state, not a forked run.** The obvious
implementation reaches for `fork_run`, and research found it a poor fit in five separate ways:
it copies the parent's prefix eagerly under a 5,000-event bound, the child shares sequence
values with its parent, the child id is derived from parent and sequence alone so two branches
at one tick collide, the child arrives paused with nothing to fold or advance it, and the
append-only log refuses the deletion a discard would need. Meanwhile the kernel is a pure
function that runs headless by design, and a default 20-day horizon is about 10,800 ticks at
roughly 0.13 seconds — 0.27 at the bound this module caps branches at, so a full six-option
comparison is about 1.6 seconds worst case. So the branch runs here, in memory, and R24 is
satisfied *structurally* —
a branch is never written to the store, so collision and leakage are not states this system
can reach.

**The copy goes through the snapshot round-trip.** `to_wire` then `from_wire` yields an
independent state, and `restore` already re-hashes what it rebuilt and refuses a mismatch, so
the copy is provably both separate and equal. A shallow copy would share the mutable per-person
and per-item objects the step function writes through, and the parent would drift — which is
the failure that would be least visible and most damaging, because the parent's own trajectory
would be wrong and nothing would say so.

**Nothing inside a branch settles anything.** That is what keeps it honest: it applies the one
option it is a branch of, then runs until the next decision arrives and stops there, reporting
the checkpoint as reached and unsettled. A branch that answered its own downstream questions
would be a simulation of a CEO rather than a projection of one option.

**The route is a parameter.** Deciding in person yields the tacit line, morale and Visibility;
deciding from the tray costs morale. A branch that priced a route the CEO is not about to take
would be wrong by exactly that premium, so the surface that asks for the comparison says which
route it is asking about.

**No draw rule is needed, and R23 is dropped because of it.** `rng.draw` is a pure function of
run seed, tick, purpose and entity with no generator state, so a branch reproduces the parent's
draws tick for tick by construction. An unattended branch also invokes no agent, because no CEO
is inside it. The shared-input rule R23 asked for would have protected nothing.

**A branch is a run nobody answers, and that is the honest projection.** `step()` raises a
period consult to the domain service at each day boundary, and a branch replays `step()` in
full — so a branch raises those requests too, into its own copy of `pending`, and abandons them
a sim-day later when nothing has replied. Nothing leaves the process: `raise_request` only
writes to state, the transport is the kernel service's job, and the branch's state is discarded
seconds later. Two consequences worth stating rather than discovering.

*It is correct.* A branch genuinely has nobody to answer it. There is no service that could,
and R17 forbids an expert from choosing inside one anyway. Suppressing the consult would make
the branch diverge from the step function, and "the branch prices the decision the way the run
would" is the property that makes a projection worth reading at all.

*It cannot bias a comparison.* Every branch at one checkpoint forks from the same state and
raises and abandons exactly the same requests at exactly the same ticks, so the no-answer path
is common to all of them and cancels out of the thing the CEO is actually comparing. What it
does mean is that a branch's absolute figures describe a run whose domain service stays silent
— which is a bound on how far a projection may be read, not an error in it.

This module is pure `simcore` and the import-boundary tests hold it there. That constraint is
the reason the runner is a library rather than a service call: the kernel service imports it,
the report could, and neither has to stand a transport up to ask where an option leads.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from contracts.envelope import EventKind
from simcore import effects, lifecycle, snapshot
from simcore import step as sim
from simcore import time as simtime

#: Why a branch stopped, when it stopped because a decision arrived.
#:
#: The other two reasons are the run's own terminal reasons, reused rather than renamed: a
#: branch that ran out of cash ended for exactly the reason a run would, and giving it a second
#: vocabulary would make the two unreconcilable in a report.
STOP_CHECKPOINT = "checkpoint"

#: Why a branch stopped, when it stopped because a comparison does not run further than this.
#:
#: Distinct from the horizon on purpose. "Ran to the end of the run" and "ran as far as a
#: comparison goes, and the run continues past here" are different facts, and reporting the
#: second as the first would overstate what the projection covers.
STOP_BOUND = "bound"

#: How far a branch will step, whatever horizon the run carries.
#:
#: Twice the horizon this product designs for, which is itself sized against the decision
#: supply — nine authored checkpoints, "a little over two days per decision". A run far beyond
#: that is burn with nothing left to decide, and a comparison of one is not worth the seconds it
#: would cost: the branches are stepped synchronously inside a request, holding the GIL for the
#: whole of it.
#:
#: This is the bound on *one* comparison's cost and it is the only one that shortens a comparison.
#: How many may run at once is a separate question and a separate limiter, in the kernel service
#: where the tick loop it competes with lives (`loop.BRANCH_SLOTS`, R14) — a library that has no
#: log and no clock is the wrong place to ration the clock's worker pool.
#:
#: A branch that reaches this stops and says so, rather than the comparison being refused. A
#: refusal would make a legitimately long run uncomparable; stopping early and reporting the
#: reason gives the CEO a shorter projection and tells them it is shorter.
#:
#: A submission guard rather than tuning, for the same reason the branch count is: it bounds
#: what one command may cost and cannot change what a recorded run means.
MAX_BRANCH_DAYS = lifecycle.DEFAULT_HORIZON_DAYS * 2

#: The most trajectory points one branch reports per metric.
#:
#: Derived from the tick cap rather than authored beside it, which is what makes it true. A
#: branch samples at each day boundary it crosses — the cadence the metrics actually move at,
#: since costs land daily — plus the fork and the stop, so the two ends are pinned and two
#: branches line up against each other. Cap the days and the point count follows.
#:
#: The first version of this was a *separate* constant derived from the default horizon and read
#: by no production code, while the sampling bounded itself on the run's actual horizon — which
#: `POST /runs` takes from the caller with no upper limit. A 200-day run produced 138 points
#: against a "bound" of 82, and the only thing that noticed was a test whose fixture happened to
#: use the default. A bound nothing enforces is a comment.
TRAJECTORY_POINT_BOUND = MAX_BRANCH_DAYS + 2


@dataclass(frozen=True, slots=True)
class Point:
    """One sample on a projected trajectory, and the tick it was measured at."""

    tick: int
    value: int

    def to_state(self) -> dict[str, Any]:
        return {"tick": self.tick, "value": self.value}


@dataclass(frozen=True, slots=True)
class Figure:
    """One number, and the tick it was measured at (R22).

    `value` is `None` for a figure that is not knowable. No branch produces one today: the only
    such figure is a runway, and `runway_days` returns `None` only for a burn of zero, while
    the fixed daily cost is a positive authored constant. Kept as a shape rather than removed
    because `runway_days` states the rule the client's own runway tile states — and a rule that
    holds only because one constant happens to be non-zero is worth keeping expressible.
    """

    value: int | None
    at_tick: int

    def to_state(self) -> dict[str, Any]:
        return {"value": self.value, "at_tick": self.at_tick}


@dataclass(frozen=True, slots=True)
class BranchSummary:
    """One option, followed to the next decision.

    Every figure carries the tick it was measured at, because a projection read without its
    measurement point is indistinguishable from a fact about now.
    """

    item: str
    cp_index: int
    option_index: int
    option_label: str
    option_note: str
    in_person: bool

    fork_tick: int
    stop_tick: int
    stop_reason: str
    stop_detail: str
    #: The checkpoint the branch stopped at, reached and unsettled, or `None`.
    stopped_at: dict[str, Any] | None

    trajectories: dict[str, list[Point]]
    metrics: dict[str, Figure]
    runway: Figure
    daily_cost: Figure
    unlocked: list[str]
    foreclosed: list[str]
    #: The tick the two gate lists above were read at — the branch's own stop, which differs
    #: between branches. Every other figure names its tick for exactly this reason: one branch
    #: can stop days before another, and a longer-running one opens more simply by running
    #: longer. Without the tick, two columns look comparable when they are not.
    gates_at_tick: int = 0

    #: What the branch emitted. Returned rather than appended: the runner is a library and has
    #: no log. Kept off `to_state` — a comparison record carries what the CEO was shown, not a
    #: second copy of a simulation nobody ran.
    emitted: list[sim.Emitted] = field(default_factory=list, repr=False)

    def to_state(self) -> dict[str, Any]:
        """The branch as the comparison record and the client read it.

        `item`, `cp_index` and `in_person` stay off the wire. They are constant across every
        branch of one comparison — all of them fork from the same checkpoint by the same route,
        and only the option differs — so the comparison payload carries them once at the top
        and repeating them here would write the same three values up to
        `MAX_BRANCHES_PER_COMPARISON` times into the payload the byte bound exists to hold
        down. They stay dataclass fields, because a summary handed around in Python should
        still know which decision it is a branch of.
        """
        return {
            "option_index": self.option_index,
            "option_label": self.option_label,
            "option_note": self.option_note,
            "fork_tick": self.fork_tick,
            "stop_tick": self.stop_tick,
            "stop_reason": self.stop_reason,
            "stop_detail": self.stop_detail,
            "stopped_at": self.stopped_at,
            "trajectories": {
                key: [point.to_state() for point in points]
                for key, points in sorted(self.trajectories.items())
            },
            "metrics": {key: figure.to_state() for key, figure in sorted(self.metrics.items())},
            "runway": self.runway.to_state(),
            "daily_cost": self.daily_cost.to_state(),
            "unlocked": list(self.unlocked),
            "foreclosed": list(self.foreclosed),
            "gates_at_tick": self.gates_at_tick,
        }


def runway_days(cash: int, daily_cost: int) -> int | None:
    """How many sim-days the cash lasts at this burn.

    `None` before there is a burn to divide by, and zero once the cash has already gone. The
    client's own runway tile states the same two rules; they agree because both are the tile's
    rule, and the branch's copy exists so a *projected* runway is computed the same way the
    live one is rather than by whatever felt right here.
    """
    if daily_cost <= 0:
        return None
    if cash <= 0:
        return 0
    return cash // daily_cost


def run_comparison(
    state: sim.State, item_id: str, cp_index: int, *, in_person: bool
) -> list[BranchSummary]:
    """Every option at one checkpoint, all forked from the same instant.

    **The single capture is a correctness requirement, not an optimisation.** The obvious
    implementation calls `run_branch` once per option, and each of those reads `state.tick` and
    copies the parent afresh — which is correct only if the parent is standing still. It is not:
    the tick loop advances `run.state` on an anyio worker thread while the command runs on
    Starlette's, and nothing in the kernel locks between them. Measured with a two-thread
    harness at the shipped batch cadence, the three branches of one comparison forked 360 ticks
    apart — two-thirds of a sim-day. The CEO then reads three columns headed "where each option
    leads" whose differences are partly just the parent drifting underneath them, and nothing on
    screen says so.

    Capturing once and restoring per branch makes "every branch forks from the same state" true
    rather than merely intended. It also narrows the window in which a tick can tear a capture
    from N to one, and it gives `unlocked_before` a fork-accurate reading — measured off the
    copy, which `restore` has just proved equal to the instant it was taken.
    """
    _refuse_unless_forkable(state, item_id, cp_index, 0)

    fork = _capture_of(state)
    options = state.spec_of(item_id).checkpoints[cp_index].options
    return [
        _branch_from(fork, item_id, cp_index, index, in_person=in_person)
        for index in range(len(options))
    ]


def run_branch(
    state: sim.State,
    item_id: str,
    cp_index: int,
    option_index: int,
    *,
    in_person: bool,
) -> BranchSummary:
    """Follow one option to the next decision, and report where it got to.

    One option, for a caller that wants exactly one. A *comparison* goes through
    `run_comparison`, which forks every option from a single capture — see there for why that
    distinction is load-bearing rather than tidy.

    Pure: the parent is read and never written, which `test_compare.py` asserts as a hash
    comparison before anything else in that file.
    """
    _refuse_unless_forkable(state, item_id, cp_index, option_index)
    return _branch_from(_capture_of(state), item_id, cp_index, option_index, in_person=in_person)


def _branch_from(
    fork: snapshot.Snapshot,
    item_id: str,
    cp_index: int,
    option_index: int,
    *,
    in_person: bool,
) -> BranchSummary:
    """Run one option from an already-taken fork.

    `restore` here cannot fail on a torn read: `_capture_of` returns only a snapshot it has
    already restored once, and a `Snapshot` is immutable bytes. A `SnapshotInvalid` out of this
    line would mean the round-trip has genuinely lost a field, which is a bug to see rather than
    to retry past.
    """
    branch = snapshot.restore(fork)
    fork_tick = branch.tick

    # An answer the parent has queued would be applied by `step` inside the branch, and an
    # agent answer resolves a checkpoint (`_apply_agent_answer`) — so the module's claim that
    # nothing inside a branch settles anything was true only because no production caller
    # queues one yet. Dropped from the copy, which makes the claim structural instead of
    # contingent. The parent keeps its own queue: this is the branch declining to answer a
    # question that was never asked of it.
    branch.queued_answers = {}

    # From the copy, not the parent. The parent may have moved since the capture, and a gate it
    # opened in that window would otherwise show up as work this option *closed* — the panel
    # would say "Closes X" about work the run had just made available.
    unlocked_before = _gates_open(branch)

    emitted = list(
        sim.resolve_checkpoint(branch, item_id, cp_index, option_index, in_person=in_person)
    )

    trajectories: dict[str, list[Point]] = {
        key: [Point(tick=fork_tick, value=branch.metrics[key])] for key in effects.METRIC_KEYS
    }

    stop_reason = ""
    stop_detail = ""
    stopped_at: dict[str, Any] | None = None

    # The lesser of the run's own horizon and what a comparison will spend. `_refuse_unless
    # _forkable` has already refused a run with no horizon, so this terminates either way.
    last_tick = min(
        branch.horizon_tick, fork_tick + MAX_BRANCH_DAYS * simtime.TICKS_PER_SIM_DAY
    )

    while branch.tick < last_tick:
        produced = sim.step(branch)
        emitted.extend(produced)

        # One point per day boundary crossed. Capping the days is what bounds the series, so
        # there is no second rule here to keep in agreement with the first.
        if simtime.is_day_boundary(branch.tick):
            _sample(trajectories, branch)

        # Termination first, and the order is the whole of it. One tick can produce both — the
        # day boundary applies costs in phase one, `_block` raises in phase three, and
        # `_check_termination` runs in phase seven — and the first version of this checked the
        # checkpoint first and broke there. A branch that bankrupted the company on the same
        # tick it raised a decision would have reported "runs to the next decision" and never
        # mentioned the insolvency, which is the single most important thing a comparison can
        # say. A run that has ended has no decision left to reach.
        if branch.terminal_reason:
            stop_reason = branch.terminal_reason
            terminal = next(
                (event for event in produced if event.kind is EventKind.RUN_TERMINATED), None
            )
            stop_detail = "" if terminal is None else str(terminal.payload["detail"])
            break

        raised = next(
            (event for event in produced if event.kind is EventKind.CHECKPOINT_RAISED), None
        )
        if raised is not None:
            stop_reason = STOP_CHECKPOINT
            stopped_at = {
                "item": raised.payload["item"],
                "cp_index": int(raised.payload["cp_index"]),
                "label": raised.payload["label"],
                "kind": raised.payload["kind"],
                "person": raised.payload["person"],
                "tick": int(raised.payload["tick"]),
            }
            stop_detail = (
                f"{raised.payload['person']} stopped at a {raised.payload['kind']} on "
                f"{raised.payload['item']}. The branch reports it and takes no action."
            )
            break

    if not stop_reason and branch.tick >= branch.horizon_tick:
        # The loop ran out of horizon without the step raising termination, which happens when
        # the parent was already at or past its horizon tick.
        stop_reason = lifecycle.TERMINAL_HORIZON
        stop_detail = (
            f"the branch reached the run's horizon of {branch.horizon_tick} ticks, recorded "
            "at genesis."
        )
    elif not stop_reason:
        # Stopped short of the run's own end. Said plainly, because reporting this as the
        # horizon would claim the projection covers the rest of the run when it does not.
        stop_reason = STOP_BOUND
        stop_detail = (
            f"a comparison runs {MAX_BRANCH_DAYS} sim-days and this run's horizon is further "
            f"out, so the branch stops at tick {branch.tick}. What happens after that is not "
            "in this projection."
        )

    _sample(trajectories, branch)

    fixed, draw_cost, salaries = sim.day_cost_terms(branch)
    burn = fixed + draw_cost + salaries
    checkpoint = branch.spec_of(item_id).checkpoints[cp_index]
    # Once, not once per direction: the predicate walks every authored item and re-evaluates
    # its gates, and the two sets below are differences over the same answer.
    unlocked_after = _gates_open(branch)

    return BranchSummary(
        item=item_id,
        cp_index=cp_index,
        option_index=option_index,
        option_label=checkpoint.options[option_index].label,
        option_note=checkpoint.options[option_index].note,
        in_person=in_person,
        fork_tick=fork_tick,
        stop_tick=branch.tick,
        stop_reason=stop_reason,
        stop_detail=stop_detail,
        stopped_at=stopped_at,
        trajectories=trajectories,
        metrics={
            key: Figure(value=branch.metrics[key], at_tick=branch.tick)
            for key in effects.METRIC_KEYS
        },
        runway=Figure(value=runway_days(branch.metrics["cash"], burn), at_tick=branch.tick),
        daily_cost=Figure(value=burn, at_tick=branch.tick),
        unlocked=sorted(unlocked_after - unlocked_before),
        foreclosed=sorted(unlocked_before - unlocked_after),
        gates_at_tick=branch.tick,
        emitted=emitted,
    )


# =========================================================================
# The parts, kept separable so each can be argued with on its own
# =========================================================================


def _refuse_unless_forkable(
    state: sim.State, item_id: str, cp_index: int, option_index: int
) -> None:
    """Everything that can fail, resolved before any work is done.

    The same ordering `ask_person` states its reason for, applied here for a weaker reason —
    the branch mutates nothing, so a late failure would corrupt nothing. It is still worth
    doing, because the caller appends an event describing what was shown and a refusal that
    arrived after ten thousand ticks of stepping would have cost that for nothing.
    """
    if state.terminal_reason:
        raise sim.CommandRejected(
            f"the run ended ({state.terminal_reason}); there is nothing downstream to compare"
        )

    if not state.horizon_tick:
        raise sim.CommandRejected(
            "this run has no horizon, so a branch has no rule that would stop it. A stopping "
            "rule the parent does not have would report a bound the run will never reach."
        )

    item = state.item(item_id)
    spec = state.spec_of(item_id)

    if cp_index < 0 or cp_index >= len(spec.checkpoints):
        raise sim.CommandRejected(f"{item_id} has no checkpoint {cp_index}")

    checkpoint = spec.checkpoints[cp_index]
    if option_index < 0 or option_index >= len(checkpoint.options):
        raise sim.CommandRejected(f"no option {option_index} on that checkpoint")

    # The same three conditions `resolve_checkpoint` enforces, checked here so a comparison is
    # refused for the reason a decision would be refused rather than by an exception escaping
    # from inside a branch.
    if item.status != sim.STATUS_BLOCKED or not item.assignee:
        raise sim.CommandRejected(
            f'"{spec.title}" is {item.status} rather than stopped at a decision. There is '
            "nothing to compare until somebody is waiting on you for it."
        )
    if item.resolved[cp_index]:
        raise sim.CommandRejected("that checkpoint is already resolved")
    not_reached_yet = sim.checkpoint_not_reached(item, spec, checkpoint)
    if not_reached_yet:
        raise sim.CommandRejected(not_reached_yet)


#: How many times a capture is retried when the parent moves underneath it.
#:
#: Small on purpose. A tick lands about once every 28ms at the shipped rate and a capture takes
#: under a millisecond, so losing three in a row means the run is ticking far faster than the
#: capture can complete and the honest answer is to say so rather than to spin.
CAPTURE_ATTEMPTS = 3


def _capture_of(state: sim.State) -> snapshot.Snapshot:
    """A snapshot of this state, proved restorable, taken while the run may be moving.

    `snapshot.restore` re-hashes what it rebuilt and refuses a mismatch, so the round-trip is
    its own correctness argument — and it is the reason this is a round-trip rather than a
    `deepcopy`. A `deepcopy` would also be independent, and it would silently carry forward a
    field the snapshot has forgotten how to write; that omission has been caught four times by
    this guard already, each time by a subsystem that looked complete.

    **The retry is here because the parent is not standing still.** `capture` reads the state
    twice — once to hash it, once to encode it — and walks several dicts while doing so. A tick
    landing in the middle produces either a `RuntimeError` from a dict that changed size
    mid-iteration, or — the commoner case by far — a `Snapshot` whose recorded hash describes a
    state its encoded bytes no longer contain. Neither is `CommandRejected`, so without this
    both escape as an opaque 500 for something the client did nothing wrong to cause.

    **The retry spans the whole round-trip, and that placement is the fix rather than a tidy-up.**
    The first version retried `capture` alone, which cannot see the second failure mode at all:
    `capture` returns a torn snapshot without raising, and the tear is only detected later, by
    `restore` inside `_branch_from`, where no `except` covers it. Three attempts were therefore
    spent on a call that had already succeeded, and the intermittent `SnapshotInvalid` this guard
    was written to absorb went straight past it. Restoring here is what makes the failure
    *detectable in the place that can retry it*.

    So the returned snapshot is one that has been restored once already, and that is a stronger
    promise than "captured": a `Snapshot` is immutable bytes and `restore` is a pure function of
    them, so a restore that succeeded once succeeds every time. `_branch_from` restores the same
    snapshot per option and cannot fail on a torn read.

    The proof copy is thrown away rather than handed to the first branch. A branch mutates the
    state it runs, so reusing this one would make the proof and the first option share an object
    — and "every branch forks from the same state" would then hold for all but one of them,
    which is the failure that would be least visible.

    It costs one extra restore per comparison — about 0.6ms against the second or so the branches
    spend — so the price of proving it is under a thousandth of the price of doing it. Paying it
    unconditionally, rather than only when the parent might be moving, is deliberate: this function
    cannot know whether its caller handed it a private copy, and a guard that has to be told when
    to run is a guard that will one day not be told.

    Retrying is safe precisely because neither half mutates the parent: a torn read is discarded
    and the next attempt starts fresh. Exhausting the attempts is reported as a refusal with a
    reason, which is the honest answer — the run really is moving too fast to photograph.
    """
    for attempt in range(CAPTURE_ATTEMPTS):
        try:
            taken = snapshot.capture("branch", state, through_seq=0)
            snapshot.restore(taken)
            return taken
        except (snapshot.SnapshotInvalid, RuntimeError):
            if attempt == CAPTURE_ATTEMPTS - 1:
                raise sim.CommandRejected(
                    "the run moved while its state was being copied, "
                    f"{CAPTURE_ATTEMPTS} times running. Try the comparison again, or slow the "
                    "clock first — a branch has to be forked from a single instant."
                ) from None
    raise AssertionError("unreachable: the loop either returns or raises")


def _sample(trajectories: dict[str, list[Point]], branch: sim.State) -> None:
    """Record one point per metric, unless this tick is already the last one recorded."""
    for key, points in trajectories.items():
        if points and points[-1].tick == branch.tick:
            continue
        points.append(Point(tick=branch.tick, value=branch.metrics[key]))


def _gates_open(state: sim.State) -> set[str]:
    """Every authored item whose gates are currently clear.

    Read through `sim.is_unlocked`, which is the predicate the kernel assigns against. Deciding
    it here from the authored gates would be a second implementation of that rule, and the two
    already check visibility and prerequisites in opposite orders — they would eventually
    disagree while each passed its own tests.

    **The gate predicate and nothing else.** The first version of this excluded items that had
    reached `done`, reasoning that finished work is not work an option opened — which is true,
    and irrelevant, because the set is differenced against itself. Excluding them made the very
    item the branch was a branch of drop out of the "after" set the moment it completed, and
    every branch then reported its own item as *foreclosed*: the option that got the work
    finished read as the option that made it impossible. Unlocking and foreclosing are
    movements of a gate; completion is not one.
    """
    return {item.id for item in state.scenario.items if sim.is_unlocked(state, item.id)}
