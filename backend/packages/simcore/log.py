"""The fold: a log prefix in, state out.

This is the single place a log becomes state. The report service is a second consumer of
it (U8), and a divergent reimplementation there would make the audit artifact disagree with
the kernel about what happened *while passing its own tests* — the product's central claim,
that every report claim resolves to the event that produced it, would be false while
appearing to hold. So there is one fold, in the kernel library, and the report imports it.

**The fold replays the step function; it does not reconstruct state from payloads.** This
is the load-bearing design decision here, and the first attempt got it wrong. There is no
tick event per quantum — under a fixed quantum one would carry no information — so effort
burn and mid-walk positions appear in *no* event. They change every tick. Rebuilding state
by applying the payloads of logged events would therefore leave `done_units` and every
walking actor's position wrong at any sequence between two events, and the error would only
show up as a state-hash mismatch with nothing pointing at the cause.

So the log records **inputs**, and the fold re-runs the deterministic simulation over them:

* *Inputs* are the things the kernel could not have computed — commands from the CEO, the
  held-direction bitmask, and answers arriving from an external service. They are applied
  at the tick they carry.
* *Outputs* are everything `step()` derives. They are regenerated rather than applied, and
  the logged copies become a **check** on the replay instead of its input.

That second point is what makes replay identity meaningful rather than tautological. If the
fold applied logged outputs, a replay would agree with the log by construction and prove
nothing. Regenerating them means a divergence is detected.

**The rules-version guard lives here, not in any caller.** Replay, the report, and a
snapshot load are three callers and each could forget it. Putting the check inside the fold
means no consumer can omit it (R27).

**The scenario guard follows the same argument, at three sites rather than one** (R7). Each is a
distinct way of obtaining state: the from-zero fold reads the identity off the genesis event it
replays; a snapshot restore reads it off the snapshot and never enters the fold
(`snapshot.from_wire`); and a fold given `resume_from` *skips genesis*, so without its own check
it would be the one path that carried a state built from one company into a process running
another. Two of the three are in this module; each passes an `at` that names it, so three guards
do not produce one indistinguishable refusal.

**Whether the fold is at the live head is a parameter, never inferred.** Re-issuing an
unanswered external request is permitted only at the live head — never during historical
replay, fork reconstruction, or a report fold (R3). Inferring it from the caller would let
the same log fold two ways depending on who asked.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from contracts.envelope import Envelope, EventKind
from simcore import scenario as scenarios
from simcore import step as sim
from simcore.rates import RULES_VERSION


class FoldRefused(Exception):
    """The fold will not run, and says why.

    Not a failure to retry. It means the log and the running code disagree about what the
    numbers mean, and folding anyway would produce a state that looks valid and is not.
    """


class UnknownEventInFold(Exception):
    """An event kind the fold does not handle.

    Raised rather than skipped. A skipped event is a divergence with nothing attached to
    it: the day-boundary hash reports a mismatch much later, and nothing points at the
    cause.
    """


class ReplayDiverged(Exception):
    """A regenerated event does not match the one in the log.

    This is the failure the fold exists to be able to detect. It carries the sequence and
    tick so the divergence localises immediately.
    """


#: Events the kernel could not have computed. Applied at the tick they carry.
#:
#: `WORK_ASSIGNED` is in both groups depending on its payload: the command that starts a
#: hand-off is an input, while the one the director's arrival produces is an output. The
#: `handoff_completed` flag distinguishes them, which is why it is on the payload.
INPUT_KINDS = frozenset(
    {
        EventKind.CEO_INPUT,
        EventKind.WORK_ASSIGNED,
        EventKind.WORK_REASSIGNED,
        EventKind.DECISION_RESOLVED,
        EventKind.INPUT_RECEIVED,
        EventKind.RATE_CHANGED,
        # U7's commands: the CEO shedding load, and asking for a hire.
        EventKind.WORK_RETURNED_TO_BACKLOG,
        EventKind.HIRE_REQUESTED,
        # Phase 2's command. Re-issued from the question rather than folded from the answer, so
        # the Visibility trajectory is reproduced by the rule that priced it and not by the
        # number the log happens to carry.
        EventKind.QUESTION_ANSWERED,
    }
)

#: Events `step()` derives. Regenerated by the fold, and compared against the log.
OUTPUT_KINDS = frozenset(
    {
        EventKind.DAILY_COSTS_APPLIED,
        EventKind.CHECKPOINT_RAISED,
        EventKind.DELIVERABLE_PRODUCED,
        EventKind.ITEM_UNLOCKED,
        EventKind.METRICS_APPLIED,
        # U7's derived events. Attrition especially: it is what lets load rise with no CEO
        # action, so it has to regenerate from the same morale counters rather than be
        # applied from a payload.
        EventKind.LOAD_CHANGED,
        EventKind.ATTRITION,
        EventKind.HIRE_ARRIVED,
        EventKind.HIRE_REFUSED,
        # Raised by the step at a period boundary, and rejected or abandoned by the step's own
        # validation. Both regenerate, which is what makes a tampered one detectable.
        EventKind.REQUEST_RAISED,
        EventKind.ANSWER_REJECTED,
        # A walk (R15). Derived: the path is `find_path` over recorded geometry and the start
        # tick is the tick it was resolved at, so it regenerates exactly — which is what makes
        # a tampered path detectable rather than merely present. It is also the first output
        # kind a *command* produces, which is why `_replay` matches a tick's outputs in two
        # passes; see there.
        EventKind.STAFF_MOVED,
    }
)

#: Kinds that exist in the closed set from U2 — so adding the mechanic is additive rather
#: than a contract change — but which have no fold semantics yet. The fold refuses them
#: rather than skipping: a log containing one was not written by this kernel, and quietly
#: ignoring it would fold to a state that is missing whatever it described.
#:
#: U7 moves these into OUTPUT_KINDS in the same change that starts producing them.
#: Emptied by U7, which implemented every kind that was here. Kept as the mechanism: U8's
#: and U11's kinds go in until those units give them semantics, and the fold refuses them
#: meanwhile rather than skipping.
NOT_YET_IMPLEMENTED_KINDS: frozenset[EventKind] = frozenset()

#: Facts about the run's life rather than its state. Neither applied nor regenerated.
OPERATIONAL_KINDS = frozenset(
    {
        EventKind.DAY_CHECKPOINT,
        EventKind.STORE_RECOVERED,
        EventKind.COMMAND_REJECTED,
        EventKind.RUN_FORKED,
        EventKind.RUN_TERMINATED,
        # A branch comparison mutates no state, so it is neither an input to re-apply nor an
        # output to regenerate. Regenerating it would make every replay of the run re-run N
        # branches and compare their summaries, which costs a tenth of a second per branch and
        # proves nothing the rest of the replay does not already prove — the branches were
        # computed from state the replay has just reproduced exactly.
        EventKind.OPTIONS_COMPARED,
    }
)


def issued_at_tick(payload: dict, fallback: int) -> int:
    """The tick a command was *issued* at, which is when the replay must re-apply it.

    Usually that is the payload's `tick`. `CEO_INPUT` is the exception, and it is a sharp
    one: its `tick` is the tick the input *applies at*, deliberately a fixed delay ahead of
    the one being rendered so that client and kernel agree by construction. The live path
    registers it while the clock still reads the earlier tick, and `step()` consumes it when
    the clock reaches the later one.

    Scheduling the replay by the applies-at tick therefore registers the input one tick too
    late — after `step()` has already passed the tick that would have consumed it — and the
    CEO never moves. `submitted_at_tick` is on the payload for exactly this reason.
    """
    return int(payload.get("submitted_at_tick", payload.get("tick", fallback)))


def is_output(kind: EventKind, payload: dict) -> bool:
    """Whether an event is something `step()` derives, rather than an input to it.

    Kind alone is not enough, and the exception is instructive: `WORK_ASSIGNED` is a
    *command* when it starts a hand-off and an *output* when the director's arrival
    completes one. Classifying by kind put those on opposite sides of the partition in the
    two places that needed to agree, which the strict comparison caught immediately. One
    predicate, used by both.
    """
    if kind is EventKind.WORK_ASSIGNED:
        return bool(payload.get("handoff_completed"))
    return kind in OUTPUT_KINDS


@dataclass(slots=True)
class FoldResult:
    state: sim.State
    through_seq: int
    #: Requests raised but never answered in this prefix. A projection of the log (R23),
    #: never memory — which is what lets a restarted kernel know what is outstanding.
    outstanding_requests: dict[str, dict] = field(default_factory=dict)
    #: Populated only when folding at the live head, and only then may a caller re-issue
    #: an unanswered request.
    at_live_head: bool = False
    #: Day-boundary hashes seen in the log, by tick. `verify.py` checks them.
    checkpoints: dict[int, str] = field(default_factory=dict)


def check_rules_version(log_rules_ver: str, running: str | None = None) -> None:
    """Refuse a fold whose log was written under different rules (R10, R27).

    The origin already treats runs as disposable across a tuning change; this makes that
    mechanical rather than aspirational. The alternative is worse than a refusal: constants
    that no longer match would replay the same events to different numbers, with nothing
    indicating which set is real.
    """
    running = running or RULES_VERSION
    if log_rules_ver != running:
        raise FoldRefused(
            f"this log was written under rules version {log_rules_ver}; the running rules "
            f"are {running}. Refusing to fold: the tuning constants and multiplier "
            "composition order have changed, so the same events would produce different "
            "numbers. The run is unplayable under current rules, but its report stays "
            "valid and self-describing."
        )


def fold(
    events: Iterable[Envelope],
    *,
    at_live_head: bool,
    running_rules_ver: str | None = None,
    strict: bool = False,
    resume_from: tuple[sim.State, int] | None = None,
    through_tick: int | None = None,
) -> FoldResult:
    """Fold a log prefix into state by replaying it.

    `strict` additionally compares every regenerated output event against the logged one,
    which is what turns a fold into a replay *check*. It is off by default because the
    report folds for content rather than for verification.

    `resume_from` is `(state, through_seq)` from a snapshot. The events passed must be
    exactly those after that sequence.

    `through_tick` is where the clock stopped, and it has to be passed in rather than
    inferred. There is no tick event per quantum, so a run that ticked on past its last
    event leaves no trace of that in the log — the fold would stop at the final event's
    tick and be short by however long the office was quiet. Current tick lives on the run
    row and on snapshots for exactly this reason. Omitting it folds only as far as the log
    itself proves, which is the right default for a report but wrong for a resume.
    """
    ordered = list(events)
    for envelope in ordered:
        check_rules_version(envelope.rules_ver, running_rules_ver)

    if resume_from is not None:
        state, through_seq = resume_from
        state = _clone(state)
        # R7's third guard site, and the one that would otherwise be skipped: this path never
        # touches a genesis event, so nothing on it reads a recorded scenario identity. It is
        # also the richest of the three, because both sides are whole `Scenario` objects — so
        # the refusal can name a reordering, which two digests cannot express.
        state.scenario = scenarios.verify_unchanged(
            state.scenario, at="a fold resumed from a snapshot, which skips genesis"
        )
    else:
        if not ordered:
            raise FoldRefused("empty log: there is no state to fold to")
        genesis = ordered[0]
        if genesis.kind is not EventKind.GENESIS:
            raise FoldRefused(
                f"the log begins with {genesis.kind.name} at sequence {genesis.seq}, not "
                "GENESIS. A fold has nothing to apply it to."
            )
        state = _apply_genesis(genesis)
        through_seq = genesis.seq
        ordered = ordered[1:]

    inputs: dict[int, list[Envelope]] = {}
    logged_outputs: dict[int, list[Envelope]] = {}
    checkpoints: dict[int, str] = {}
    outstanding: dict[str, dict] = {}
    final_tick = state.tick

    for envelope in ordered:
        kind = envelope.kind
        payload = envelope.decoded_payload()
        tick = int(payload.get("tick", envelope.tick))
        final_tick = max(final_tick, tick)

        if kind is EventKind.GENESIS:
            raise UnknownEventInFold(
                f"a second GENESIS event at sequence {envelope.seq}; a run has one"
            )

        if kind is EventKind.DAY_CHECKPOINT:
            checkpoints[tick] = payload["state_hash"]
            continue

        if kind is EventKind.REQUEST_RAISED:
            outstanding[envelope.request_id or payload.get("request_id", "")] = {
                "owning_item": payload.get("owning_item", ""),
                "raised_at_tick": tick,
                "service": payload.get("service", ""),
            }
            logged_outputs.setdefault(tick, []).append(envelope)
            continue

        if kind is EventKind.ANSWER_REJECTED:
            outstanding.pop(envelope.request_id or payload.get("request_id", ""), None)
            logged_outputs.setdefault(tick, []).append(envelope)
            continue

        if kind is EventKind.INPUT_RECEIVED:
            outstanding.pop(envelope.request_id or payload.get("request_id", ""), None)
            inputs.setdefault(issued_at_tick(payload, envelope.tick), []).append(envelope)
            continue

        if is_output(kind, payload):
            logged_outputs.setdefault(tick, []).append(envelope)
            continue

        if kind in INPUT_KINDS:
            inputs.setdefault(issued_at_tick(payload, envelope.tick), []).append(envelope)
            continue

        if kind in OPERATIONAL_KINDS:
            continue

        if kind in NOT_YET_IMPLEMENTED_KINDS:
            raise UnknownEventInFold(
                f"{kind.name} at sequence {envelope.seq} has no fold semantics yet; it "
                "arrives with U7. Refusing rather than skipping: a log holding it was not "
                "written by this kernel, and folding on would silently drop whatever it "
                "described."
            )

        raise UnknownEventInFold(
            f"the fold does not handle {kind.name} (sequence {envelope.seq})"
        )

    if through_tick is not None:
        if through_tick < final_tick:
            raise FoldRefused(
                f"asked to fold through tick {through_tick}, but the log holds an event at "
                f"tick {final_tick}. A run row behind its own log means the two disagree "
                "about how far the run got."
            )
        final_tick = through_tick

    _replay(state, inputs, logged_outputs, final_tick, strict=strict)

    return FoldResult(
        state=state,
        through_seq=max(through_seq, max((e.seq for e in ordered), default=through_seq)),
        outstanding_requests=outstanding,
        at_live_head=at_live_head,
        checkpoints=checkpoints,
    )


def _replay(
    state: sim.State,
    inputs: dict[int, list[Envelope]],
    logged_outputs: dict[int, list[Envelope]],
    final_tick: int,
    *,
    strict: bool,
) -> None:
    """Advance the simulation tick by tick, applying inputs where the log says they landed.

    Commands are applied when the clock reads the tick they were recorded at, then the tick
    advances — the same order the live path uses, where a command is applied at a tick
    boundary and `step()` runs after it.

    **A tick's outputs have two producers, and the strict comparison accounts for both.** Until
    movement went on the wire (R15), every output event was the step's, so one count check per
    tick was enough. A command derives one now — `assign_via_manager` resolves the director's
    path before it returns — and in the live log those sit *after* the step's at the same tick,
    because the clock only reaches tick T inside the step that ends there and a command applied
    at T follows it.

    So `logged_outputs[T]` is matched in that order: the step's regenerated outputs first, then
    the commands' when the loop comes back round to T, and only then is the tick's list required
    to be exhausted. Two independent count checks would not work — the step's check runs before
    the commands at that tick have been applied, so the remainder is expected in between and a
    divergence afterwards. Partitioning the logged events by producer instead would mean
    guessing which producer wrote each one, which the log does not record.
    """
    #: How much of each tick's logged outputs has been matched. Keyed by tick because the two
    #: passes for one tick happen in different iterations of this loop.
    matched: dict[int, int] = {}

    while state.tick <= final_tick:
        from_commands: list[sim.Emitted] = []
        for envelope in inputs.get(state.tick, ()):
            from_commands.extend(_apply_input(state, envelope))

        if strict:
            logged = logged_outputs.get(state.tick, [])
            matched[state.tick] = _compare(
                state.tick, from_commands, logged, matched.get(state.tick, 0)
            )
            _expect_exhausted(state.tick, logged, matched[state.tick])

        if state.tick == final_tick:
            break

        produced = sim.step(state)

        if strict:
            matched[state.tick] = _compare(
                state.tick,
                produced,
                logged_outputs.get(state.tick, []),
                matched.get(state.tick, 0),
            )


def _apply_input(state: sim.State, envelope: Envelope) -> list[sim.Emitted]:
    """Re-issue a command exactly as it was originally issued, and return what it produced.

    The events come back rather than being discarded because a command can now derive an
    output — a walk — and an output the fold regenerated but never compared would be an output
    the log could hold a tampered copy of. `_replay` is what compares them.
    """
    kind = envelope.kind
    payload = envelope.decoded_payload()

    if kind is EventKind.CEO_INPUT:
        state.ceo_inputs[int(payload["tick"])] = int(payload["bitmask"])
        return []

    if kind is EventKind.WORK_ASSIGNED:
        if payload.get("handoff_started"):
            return sim.assign_via_manager(state, payload["item"])
        return sim.assign_direct(state, payload["item"], payload["person"])

    if kind is EventKind.WORK_REASSIGNED:
        return sim.reassign(state, payload["item"], payload["to"])

    if kind is EventKind.DECISION_RESOLVED:
        return sim.resolve_checkpoint(
            state,
            payload["item"],
            int(payload["cp_index"]),
            int(payload["option_index"]),
            in_person=bool(payload["in_person"]),
        )

    if kind is EventKind.QUESTION_ANSWERED:
        # Re-issued as the command, not folded from the payload. Replaying the *answer* would
        # let a log disagree with the roster it was produced from; replaying the *question*
        # reproduces the Visibility trajectory from the same rule that priced it originally.
        return sim.ask_person(state, payload["person"], str(payload["asked"]))

    if kind is EventKind.RATE_CHANGED:
        # Rate is run state, not simulation state: the replay multiplier is deliberately
        # not stored, so there is nothing here to apply to the fold.
        return []

    if kind is EventKind.WORK_RETURNED_TO_BACKLOG:
        return sim.return_to_backlog(state, payload["item"])

    if kind is EventKind.HIRE_REQUESTED:
        return sim.request_hire(state, payload["director"])

    if kind is EventKind.INPUT_RECEIVED:
        # The answer is *in* this event, which is the whole point: replay re-queues the logged
        # answer and never opens the stream (R3). Re-issuing would make the replay depend on
        # what the service would say today rather than on what it said then.
        return sim.receive_answer(
            state,
            envelope.request_id or payload.get("request_id", ""),
            dict(payload.get("answer", {})),
            at_tick=int(payload["tick"]),
        )

    raise UnknownEventInFold(f"no replay path for input {kind.name} at sequence {envelope.seq}")


def _compare(
    tick: int, produced: list[sim.Emitted], logged: list[Envelope], already_matched: int
) -> int:
    """Check regenerated outputs against the log, and say how far through the tick we got.

    The whole point of regenerating them. `already_matched` is where in this tick's logged
    outputs to continue from: a tick's outputs come from the step and then from any command
    applied at that tick, and both are matched against one logged sequence in log order.

    Having more logged than produced is *not* a divergence here — the step's pass runs before
    the commands at that tick have been applied, so a remainder is the normal case. Only
    `_expect_exhausted`, called once the tick can produce nothing further, may draw that
    conclusion.
    """
    produced_outputs = [item for item in produced if is_output(item.kind, item.payload)]
    available = logged[already_matched:]

    if len(produced_outputs) > len(available):
        raise ReplayDiverged(
            f"at tick {tick} the replay produced {len(produced_outputs)} output events "
            f"({[e.kind.name for e in produced_outputs]}) but the log holds only "
            f"{len(available)} unmatched at that tick ({[e.kind.name for e in available]})"
        )

    for regenerated, recorded in zip(produced_outputs, available, strict=False):
        if regenerated.kind is not recorded.kind:
            raise ReplayDiverged(
                f"at tick {tick} the replay produced {regenerated.kind.name} where the log "
                f"holds {recorded.kind.name} (sequence {recorded.seq})"
            )
        if regenerated.payload != recorded.decoded_payload():
            raise ReplayDiverged(
                f"at tick {tick} the payload of {recorded.kind.name} at sequence "
                f"{recorded.seq} does not match the replay"
            )

    return already_matched + len(produced_outputs)


def _expect_exhausted(tick: int, logged: list[Envelope], matched: int) -> None:
    """Every output the log holds for this tick must have been regenerated by now.

    Split out of `_compare` because a tick's outputs are matched in two passes and only the
    second one knows it is the last. This is the half of the old count check that caught a
    *missing* regenerated event, and it has to be asked after the commands at that tick have
    been re-issued rather than before.
    """
    if matched < len(logged):
        left = [envelope.kind.name for envelope in logged[matched:]]
        raise ReplayDiverged(
            f"at tick {tick} the log holds {len(logged) - matched} output events the replay "
            f"did not produce ({left}), from sequence {logged[matched].seq} onwards"
        )


def _apply_genesis(envelope: Envelope) -> sim.State:
    """Rebuild the world the run was created with.

    Geometry is recorded at genesis, and the floor is regenerated from the recorded grid
    rather than carried through every snapshot: the generator is deterministic, so the two
    agree. The recorded copy is what makes that safe — and it is compared, so a generator
    that stopped being deterministic is caught here rather than as a mystery divergence.

    The company is regenerated the same way and for the same reason — by name from the recorded
    identity, not from the payload's roster and catalog — and it is checked the same way too
    (R7). The recorded roster and catalog are handed to the guard so that a refusal can name the
    person or the item that moved rather than only two digests.
    """
    payload = envelope.decoded_payload()
    cols, rows = payload["grid"]

    company = scenarios.load_recorded(
        payload.get("scenario"),
        at="the from-zero fold, replaying genesis",
        recorded_roster=payload.get("roster"),
        recorded_catalog=payload.get("catalog"),
    )

    # The horizon is read from the genesis event, not defaulted. It is chosen at genesis and
    # immutable, so a fold that used the running default would give a resumed run a different
    # bound from the one it was created with — and it would terminate at the wrong tick, which
    # is exactly the failure recording it was meant to prevent.
    state, _ = sim.new_run(
        run_seed=payload["run_seed"],
        cols=cols,
        rows=rows,
        horizon_tick=int(payload["horizon_tick"]),
        scenario=company,
    )
    state.metrics = dict(payload["metrics"])
    state.tick = 0

    if state.floor.to_state() != payload["floor"]:
        raise FoldRefused(
            "the floor regenerated from the recorded grid does not match the floor recorded "
            "at genesis, so positions, paths and arrival ticks in this log cannot be "
            "reproduced. The floor generator is not deterministic for this grid."
        )

    return state


def _clone(state: sim.State) -> sim.State:
    """A deep-enough copy that a resumed fold cannot mutate the snapshot it came from."""
    import copy

    return copy.deepcopy(state)


def replay_to_state(
    events: Iterable[Envelope],
    running_rules_ver: str | None = None,
    strict: bool = True,
    through_tick: int | None = None,
) -> sim.State:
    """Fold a complete log from zero. Never re-issues an external call (R3)."""
    return fold(
        events,
        at_live_head=False,
        running_rules_ver=running_rules_ver,
        strict=strict,
        through_tick=through_tick,
    ).state
