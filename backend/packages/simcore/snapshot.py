"""Snapshots: a pure function of a log prefix, and disposable by design.

A snapshot is never authoritative. It is a cache of "what the fold produces for this
prefix", keyed by the derived rules version, and it is dropped rather than migrated when
that version changes. Dropping loses nothing — the log is still there and re-folding
reproduces it — which is what makes the recovery ladder's cheapest rung cheap.

**Keyed on the derived rules version, and validated before use.** A snapshot taken under
different tuning constants describes a state the current rules would never produce. Loading
it would make fold-from-snapshot diverge from fold-from-zero while every integrity guard
reported a match, which is the single nastiest failure this design can have. R36's derived
version is what closes it: the key changes automatically when any constant does.

**The wire form carries slightly more than the hash view.** The state hash covers simulation
state; reconstructing a run additionally needs the seed, the grid and the scenario identity,
because the floor is regenerated rather than stored and the company is reloaded by name rather
than stored. Keeping those separate is deliberate — putting the seed into the hashed set would
be harmless today and wrong the first time a fork changed it, and putting the scenario there
would hash a value that cannot change within a run at every day boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from contracts import canonical
from simcore import hashing
from simcore import step as sim
from simcore.rates import RULES_VERSION
from simcore.world import plan_floor


class SnapshotInvalid(Exception):
    """This snapshot cannot be used, and says why."""


#: Bumped when the wire form changes shape. Distinct from the state-shape version, which
#: covers what the *hash* spans.
#:
#: 2: U6 added `scenario`, the identity of the company the state was built against (R7). A
#: snapshot restore is a way of obtaining state that bypasses the fold entirely, so it is one of
#: the three sites that has to check it — and it cannot check what it does not carry. The bump
#: makes every pre-U6 snapshot a documented drop-and-refold, which the format-version refusal
#: below already words as "a snapshot is a cache, so nothing is lost".
#:
#: 3: the MVP's U15 added `authorizations`, what each item has asked the CEO to allow. It is hashed
#: state, so a snapshot that omitted it would fail its own round-trip guard — but the bump is not
#: for that. It is for the *fork*: a child restored from a pre-U15 snapshot would come back with an
#: item that is stopped in its parent and moving in the child, and the two timelines would then
#: differ by something nobody decided.
SNAPSHOT_FORMAT_VERSION = 3


@dataclass(frozen=True, slots=True)
class Snapshot:
    run_id: str
    through_seq: int
    tick: int
    rules_ver: str
    state_shape_ver: int
    state_hash: str
    #: Canonical bytes. Never a JSON column in the store, for the usual reason.
    state: bytes

    def is_usable(self, running_rules_ver: str | None = None) -> bool:
        return self.rules_ver == (running_rules_ver or RULES_VERSION)


def to_wire(state: sim.State) -> dict[str, Any]:
    """Everything needed to reconstruct this state, canonically encodable."""
    return {
        "format": SNAPSHOT_FORMAT_VERSION,
        "run_seed": state.run_seed,
        # The company, by identity rather than by value (R7). Three fields instead of a whole
        # roster and catalog: the file is on disk and `from_wire` reloads it by name, which is
        # also what gives the restore something to refuse against. Storing the structure would
        # make a snapshot a second copy of the scenario that could disagree with the file while
        # reproducing its own hash.
        "scenario": state.scenario.identity(),
        "grid": [state.floor.cols, state.floor.rows],
        "tick": state.tick,
        "metrics": dict(state.metrics),
        "people": {pid: person.to_state() for pid, person in state.people.items()},
        "items": {iid: item.to_state() for iid, item in state.items.items()},
        "ceo": state.ceo.to_state(),
        "ceo_inputs": {str(tick): mask for tick, mask in sorted(state.ceo_inputs.items())},
        "outputs": [output.to_state() for output in state.outputs],
        # U7's subsystems. Their absence here was caught by the round-trip guard below
        # rather than by a reading of this function, which is the argument for having it:
        # a snapshot that silently omits a subsystem restores a state that looks complete.
        "capacity": {
            director: department.to_state()
            for director, department in state.capacity.items()
        },
        "morale": {
            person_id: person.to_state() for person_id, person in state.morale.items()
        },
        "hires": {
            request_id: hire.to_state() for request_id, hire in state.hires.items()
        },
        "departed": list(state.departed),
        "announced_unlocks": sorted(state.announced_unlocks),
        # U8's lifecycle. Caught by the round-trip guard below rather than by review, for the
        # third time — which is the argument for a snapshot that has to reproduce its own hash.
        "horizon_tick": state.horizon_tick,
        "terminal_reason": state.terminal_reason,
        "terminal_tick": state.terminal_tick,
        # U11's pending-input contract. Outstanding requests have to survive a restart or the
        # kernel would forget what it had asked — which is precisely why R23 requires them to be
        # a projection of the log rather than memory-only. Caught by the round-trip guard for the
        # fourth time; four catches from one guard is a strong argument for having it.
        "pending": {
            request_id: request.to_state()
            for request_id, request in state.pending.items()
        },
        # U15's Authorizations. They stall the items they belong to, so a restore that dropped them
        # would restart work the CEO has not agreed to — which is the same class of silent loss the
        # round-trip guard caught four times above, and this one would also be a rule being
        # forgotten rather than a number.
        "authorizations": {
            item_id: record.to_state()
            for item_id, record in state.authorizations.items()
        },
        "last_period_consulted": state.last_period_consulted,
        "queued_answers": {
            str(tick): list(answers)
            for tick, answers in sorted(state.queued_answers.items())
        },
        # Hiring items are created at runtime, so a restored run has to be able to rebuild
        # their specs — otherwise an in-flight hire would fold to an unknown item.
        "dynamic_items": {
            item_id: {
                "title": spec.title,
                "brief": spec.brief,
                "dept": spec.dept,
                "want": spec.want,
                "effort_hours": spec.effort_hours,
                "friction": spec.friction,
                "output_title": spec.output_title,
                "output_kind": spec.output_kind,
            }
            for item_id, spec in state.dynamic_items.items()
        },
    }


def from_wire(wire: dict[str, Any]) -> sim.State:
    """Rebuild state from a snapshot's wire form."""
    found = int(wire.get("format", 0))
    if found != SNAPSHOT_FORMAT_VERSION:
        raise SnapshotInvalid(
            f"snapshot format version {found}; this kernel writes and reads "
            f"{SNAPSHOT_FORMAT_VERSION}. Remedy: drop the snapshot and re-fold — a snapshot "
            "is a cache, so nothing is lost."
        )

    cols, rows = wire["grid"]
    floor = plan_floor(cols, rows)

    from simcore import capacity as cap
    from simcore import hiring
    from simcore import items as work
    from simcore import morale as mor
    from simcore import people as roster
    from simcore import scenario as sc

    # One of R7's three guard sites, and the one that bypasses the fold: a restore builds state
    # without replaying a genesis event, so nothing else on this path would notice that the
    # company had been edited under it. `at` names the site so the refusal says which of the
    # three fired.
    company = sc.load_recorded(wire.get("scenario"), at="a snapshot restore")

    seats = roster.assign_seats(company, floor)

    # An arrived hire is a person no scenario authored, so their seat comes from the snapshot.
    # Restored before the roster loop below, which reads `seats`.
    for recorded_hire in wire["hires"].values():
        if recorded_hire["status"] == "arrived" and recorded_hire["seat"]:
            seats[recorded_hire["person"]] = tuple(recorded_hire["seat"])
    state = sim.State(
        run_seed=int(wire["run_seed"]),
        scenario=company,
        tick=int(wire["tick"]),
        floor=floor,
        seats=seats,
        metrics=dict(wire["metrics"]),
        people={},
        items={},
        ceo=sim.CeoRuntime(
            x_milli=int(wire["ceo"]["x_milli"]),
            y_milli=int(wire["ceo"]["y_milli"]),
            facing=wire["ceo"]["facing"],
        ),
    )

    for person_id, recorded in wire["people"].items():
        state.people[person_id] = sim.PersonRuntime(
            id=person_id,
            pos=tuple(recorded["pos"]),
            seat=seats.get(person_id, tuple(recorded["pos"])),
            facing=recorded["facing"],
            state=recorded["state"],
            item_id=recorded["item"],
            cp_index=int(recorded["cp"]),
            met_ticks=int(recorded["met_ticks"]),
            path=tuple(tuple(tile) for tile in recorded["path"]),
            path_start_tick=int(recorded["path_start_tick"]),
            arrive=recorded["arrive"],
            arrive_item=recorded["arrive_item"],
            bypassed_director=bool(recorded["bypassed_director"]),
            # Sorted on the way back in as well as on the way out: a snapshot written before
            # this field existed has none, and a reload must not be the thing that decides
            # whether a repeat question is free.
            answered=sorted(recorded.get("answered", [])),
        )

    for item_id, recorded in wire["items"].items():
        state.items[item_id] = sim.ItemRuntime(
            id=item_id,
            done_units=int(recorded["done_units"]),
            status=recorded["status"],
            assignee=recorded["assignee"],
            visited=bool(recorded["visited"]),
            resolved=list(recorded["resolved"]),
            decisions=[
                sim.Decision(
                    label=decision["label"],
                    choice=decision["choice"],
                    note=decision["note"],
                    in_person=bool(decision["in_person"]),
                    tacit=decision["tacit"],
                    at_tick=int(decision["at_tick"]),
                    uninformed=tuple(decision["uninformed"]),
                )
                for decision in recorded["decisions"]
            ],
        )

    state.capacity = {
        director: cap.DepartmentCapacity(
            director_id=director,
            monthly_hours=int(recorded["monthly_hours"]),
            remaining_units=int(recorded["remaining_units"]),
            load_permille=int(recorded["load_permille"]),
        )
        for director, recorded in wire["capacity"].items()
    }
    state.morale = {
        person_id: mor.PersonMorale(
            value=int(recorded["value"]), days_below=int(recorded["days_below"])
        )
        for person_id, recorded in wire["morale"].items()
    }
    state.hires = {
        request_id: hiring.Hire(
            request_id=request_id,
            director_id=recorded["director"],
            person_id=recorded["person"],
            item_id=recorded["item"],
            status=recorded["status"],
            seat=tuple(recorded["seat"]) if recorded["seat"] else None,
            refusal=recorded["refusal"],
        )
        for request_id, recorded in wire["hires"].items()
    }
    state.departed = list(wire["departed"])
    state.announced_unlocks = sorted(wire.get("announced_unlocks", []))
    from simcore import pending as pend

    state.pending = {
        request_id: pend.PendingRequest(
            request_id=request_id,
            service=recorded["service"],
            owning_item=recorded["owning_item"],
            raised_at_tick=int(recorded["raised_at_tick"]),
            deadline_tick=int(recorded["deadline_tick"]),
            period_index=int(recorded["period_index"]),
        )
        for request_id, recorded in wire["pending"].items()
    }
    from simcore import authorization as authz

    state.authorizations = {
        item_id: authz.from_state(item_id, recorded)
        # `.get` rather than `[...]`: the format version above already refuses a snapshot older
        # than this field, so this is the belt on a check that has already passed rather than a
        # migration — and an empty default is the right answer for a state that had none.
        for item_id, recorded in wire.get("authorizations", {}).items()
    }
    state.last_period_consulted = int(wire["last_period_consulted"])
    state.queued_answers = {
        int(tick): list(answers) for tick, answers in wire["queued_answers"].items()
    }
    state.horizon_tick = int(wire["horizon_tick"])
    state.terminal_reason = wire["terminal_reason"]
    state.terminal_tick = int(wire["terminal_tick"])
    state.dynamic_items = {
        item_id: work.ItemSpec(
            id=item_id,
            title=recorded["title"],
            brief=recorded["brief"],
            dept=recorded["dept"],
            want=recorded["want"],
            effort_hours=int(recorded["effort_hours"]),
            friction=recorded["friction"],
            checkpoints=(),
            output_title=recorded["output_title"],
            output_kind=recorded["output_kind"],
        )
        for item_id, recorded in wire["dynamic_items"].items()
    }

    state.ceo_inputs = {int(tick): int(mask) for tick, mask in wire["ceo_inputs"].items()}
    state.outputs = [
        sim.Deliverable(
            item_id=output["item"],
            title=output["title"],
            kind=output["kind"],
            dept=output["dept"],
            day=int(output["day"]),
            provenance=tuple(output["provenance"]),
            tacit=tuple(output["tacit"]),
        )
        for output in wire["outputs"]
    ]

    return state


def capture(run_id: str, state: sim.State, through_seq: int) -> Snapshot:
    """Take a snapshot of this state at this sequence."""
    hashed = hashing.state_hash(sim.snapshot(state))
    return Snapshot(
        run_id=run_id,
        through_seq=through_seq,
        tick=state.tick,
        rules_ver=RULES_VERSION,
        state_shape_ver=hashed.shape_version,
        state_hash=hashed.overall,
        state=canonical.encode(to_wire(state)),
    )


def restore(snapshot: Snapshot, running_rules_ver: str | None = None) -> sim.State:
    """Load a snapshot, refusing one written under different rules.

    The guard is here as well as in the fold, because loading a snapshot is a way of
    obtaining state that bypasses the fold entirely.
    """
    running = running_rules_ver or RULES_VERSION
    if snapshot.rules_ver != running:
        raise SnapshotInvalid(
            f"this snapshot was taken under rules version {snapshot.rules_ver}; the running "
            f"rules are {running}. It describes a state the current rules would never "
            "produce. Remedy: drop it and re-fold from the log — a snapshot is a cache, so "
            "nothing is lost."
        )
    if snapshot.state_shape_ver != hashing.STATE_SHAPE_VERSION:
        raise SnapshotInvalid(
            f"this snapshot covers state shape version {snapshot.state_shape_ver}; this "
            f"kernel hashes shape version {hashing.STATE_SHAPE_VERSION}. Drop and re-fold."
        )

    state = from_wire(canonical.decode(snapshot.state))

    # A snapshot that does not reproduce its own hash is corrupt, and finding that out here
    # is much cheaper than finding it out at the next day boundary.
    rehashed = hashing.state_hash(sim.snapshot(state)).overall
    if rehashed != snapshot.state_hash:
        raise SnapshotInvalid(
            f"snapshot for {snapshot.run_id} at sequence {snapshot.through_seq} does not "
            f"reproduce its own state hash (recorded {snapshot.state_hash}, recomputed "
            f"{rehashed}). Drop and re-fold."
        )

    return state


def invalidated_by(snapshot: Snapshot, running_rules_ver: str | None = None) -> str:
    """Why this snapshot is unusable, or the empty string if it is fine."""
    running = running_rules_ver or RULES_VERSION
    if snapshot.rules_ver != running:
        return f"rules version changed: {snapshot.rules_ver} -> {running}"
    if snapshot.state_shape_ver != hashing.STATE_SHAPE_VERSION:
        return (
            f"state shape version changed: {snapshot.state_shape_ver} -> "
            f"{hashing.STATE_SHAPE_VERSION}"
        )
    return ""
