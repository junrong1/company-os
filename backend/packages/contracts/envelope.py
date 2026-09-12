"""The event envelope, in pure Python.

This module is the one every later unit codes against, and it is deliberately free
of gRPC: `simcore` imports it, and R5 forbids the kernel library from acquiring a
transport dependency. The generated stubs live in `contracts.grpc`, which only the
services import.

The central idea here is the two field groups. A field is either *hashed* — it
participates in replay identity (R11) and the state hash (R9) — or it is *metadata*
and participates in neither. Getting that wrong in either direction is a real bug:

* metadata in the hashed set makes two runs of one seed compare unequal when they
  are in fact identical, because ingest time and correlation ids differ per run;
* a hashed field in the metadata set makes two genuinely different runs compare
  equal, which is worse — it makes the replay test pass while replay is broken.

So the classification is asserted, and R11's requirement that a new field be
explicitly classified before it can be added is mechanical: the dataclass fields and
the two frozensets are compared in `tests/test_envelope.py`, and a field in neither
fails the suite.
"""

from __future__ import annotations

import dataclasses
import uuid
from enum import IntEnum
from typing import Any

from contracts import canonical

# The shape of the envelope itself. Distinct from the per-kind payload versions
# below: this changes when a field is added to the envelope, those change when one
# event type's payload changes. The status endpoint reports this one.
EVENT_SCHEMA_VERSION = 1


class EventKind(IntEnum):
    """Mirrors the EventKind enum in proto/events.proto; kept in sync by a test.

    A closed set. Decoding an unrecognised kind raises rather than skipping: an
    event the fold silently ignored is a divergence that announces itself only at
    the next day-boundary hash, with nothing pointing at the cause.
    """

    UNSPECIFIED = 0

    # Run lifecycle and determinism.
    GENESIS = 1
    DAY_CHECKPOINT = 2
    RATE_CHANGED = 3
    RUN_TERMINATED = 4
    RUN_FORKED = 5
    STORE_RECOVERED = 6

    # The pending-input contract.
    REQUEST_RAISED = 10
    INPUT_RECEIVED = 11
    ANSWER_REJECTED = 12

    # The CEO, and decisions.
    CEO_INPUT = 20
    CHECKPOINT_RAISED = 21
    DECISION_RESOLVED = 22
    QUESTION_ANSWERED = 23
    OPTIONS_COMPARED = 24
    AUTHORIZATION_DECIDED = 25

    # Work.
    WORK_ASSIGNED = 30
    WORK_REASSIGNED = 31
    WORK_RETURNED_TO_BACKLOG = 32
    DELIVERABLE_PRODUCED = 33
    ITEM_UNLOCKED = 34

    # The economy.
    METRICS_APPLIED = 40
    LOAD_CHANGED = 41
    HIRE_REQUESTED = 42
    HIRE_ARRIVED = 43
    HIRE_REFUSED = 44
    ATTRITION = 45
    DAILY_COSTS_APPLIED = 46

    # Commands whose outcome must survive a client reconnect (R30).
    COMMAND_REJECTED = 50

    # Movement. One event per resolved walk, never one per tick (R15).
    STAFF_MOVED = 60


# Payload schema version per event type. Adding a kind is additive — existing
# events keep their kind and version, so replay is unaffected — but it is a
# deliberate edit here rather than an implicit consequence of producing a new
# event, and the suite pins this table so an accidental addition fails.
#
# The unit named against each kind is the one that starts producing it.
KIND_SCHEMA_VERSIONS: dict[EventKind, int] = {
    # 2: U13/U14 added `catalog`, the authored work graph. Additive — the fold reads named
    # keys and ignores the rest — but a version bump regardless, because a consumer that
    # needs the graph has to be able to tell whether the event it is holding carries it.
    # 3: Phase 2 added `name`, `initials` and `title` to each roster entry, so a client can
    # say who the CEO is standing next to rather than showing them an id.
    # 4: Phase 3 added `effect`, `draw_delta` and `note` to each catalog option, reversing the
    # withholding `catalog_to_state`'s docstring used to state (R35). Additive, but a consumer
    # that needs an option's consequence has to be able to tell whether the event it is holding
    # carries it — the same argument that bumped this for the catalog and the roster names.
    # 5: The MVP's U6 made a company a file, and genesis records which one: `scenario` carries the
    # id, the canonical content hash and the hash version (R7), and each roster entry gained the
    # `responsibility`, `tools`, `mcp_servers` and `skills` a scenario now authors for everybody
    # (M15). One bump for both, because the payload changes once — the data move and the schema
    # extension land together so the golden fixtures regenerate once rather than twice. A run
    # written at version 4 records no scenario identity, and `scenario.load_recorded` refuses it
    # by name with the remedy rather than folding it against whatever is on disk.
    EventKind.GENESIS: 5,  # U4/U8, MVP U6
    EventKind.DAY_CHECKPOINT: 1,  # U3/U15
    EventKind.RATE_CHANGED: 1,  # U9
    EventKind.RUN_TERMINATED: 1,  # U8
    EventKind.RUN_FORKED: 1,  # U15
    EventKind.STORE_RECOVERED: 1,  # U6/U9
    # 2: the MVP's U10 added a third leg to the pending-input contract. A statement request carries
    # the director it was raised on, the checkpoint it is about, and the authorized scope the leg
    # must query under (R23) — present only on that leg, so a period consult's payload is
    # byte-identical to version 1 and a log written before this unit still replays strictly. The
    # bump is for the consumer that has to be able to tell whether the event it is holding can carry
    # a director at all.
    # 3: the MVP's U15 added a fourth leg, answered by the CEO rather than by a service. An
    # Authorization request carries the director asking, the line whose knowledge they need, and
    # which time of asking this is — present only on that leg, so the other three payloads are
    # byte-identical to version 2 and every log written before this unit still replays strictly.
    EventKind.REQUEST_RAISED: 3,  # U11, MVP U10, MVP U15
    # 2: the answer to a statement request rides here rather than on a new kind, which is what keeps
    # it inside the partial unique index that already enforces one answer per request per run. The
    # discriminator is on the payload: `service` names the leg, and the answer then carries the
    # prose, the objection, the citations, the retrieved context and the producer identity (R2, M31,
    # M32). Additive — a resolution and a period consult are unchanged — and versioned because a
    # client rendering a briefing has to know whether this event can hold one.
    EventKind.INPUT_RECEIVED: 2,  # U11, MVP U10
    EventKind.ANSWER_REJECTED: 1,  # U11
    EventKind.CEO_INPUT: 1,  # U4
    # 2: U13/U14 added `item_status`, the status the item landed in. Every kind that moves
    # an item now says where it moved it to, so a read-side consumer updates its node from
    # the event instead of keeping its own copy of the rule for what each kind does to work.
    # 3: Phase 2 added `tacit`, the line surfaced only by resolving in person. It arrives when
    # the checkpoint is raised rather than at genesis, so the script is not on the wire before
    # anybody has stopped at anything.
    EventKind.CHECKPOINT_RAISED: 3,  # U4
    EventKind.DECISION_RESOLVED: 2,  # U4
    # The answer text rides on the event rather than being re-derived by readers. Every other
    # scripted line in this system is re-derived from state — the report rebuilds checkpoint
    # tacit lines that way — but a *generated* answer cannot be, so storing it now is what
    # keeps the log's shape unchanged when a hearing API replaces the roster script.
    EventKind.QUESTION_ANSWERED: 1,  # Phase 2
    # The branch summaries the CEO was shown, on the record so the second plan's citation
    # contract has something to point at. Operational: it mutates no state, so the fold neither
    # applies it nor regenerates it — regenerating would make every replay re-run N branches
    # for no gain in what the replay proves.
    EventKind.OPTIONS_COMPARED: 1,  # Phase 3
    # The CEO's answer to a cross-line read, and the consequence it had for the item. An input:
    # nothing in the run derives it, so the fold re-issues the command rather than regenerating the
    # event — which is what makes a grant and a refusal both replay with no model, no bench and no
    # client reachable.
    EventKind.AUTHORIZATION_DECIDED: 1,  # MVP U15
    # 3: the MVP's U15 marked the assignment `request_hire` derives with `for_hire`, so the fold
    # can tell it from an assignment somebody issued. Additive, and it is the field that decides
    # whether the event is applied or regenerated — a log written without it folds the old way,
    # which is to say it does not fold at all once it holds a hire.
    EventKind.WORK_ASSIGNED: 3,  # U4, MVP U15
    EventKind.WORK_REASSIGNED: 2,  # U4
    EventKind.WORK_RETURNED_TO_BACKLOG: 2,  # U7
    EventKind.DELIVERABLE_PRODUCED: 2,  # U4
    # 2: Phase 2 moved the announcement out of item completion and into the step, and dropped
    # `because` with it — an unlock now follows from state rather than from one cause, and a
    # Visibility gain from asking releases work no completed item can be blamed for.
    EventKind.ITEM_UNLOCKED: 2,  # U4
    EventKind.METRICS_APPLIED: 1,  # U8/U11
    EventKind.LOAD_CHANGED: 1,  # U7
    EventKind.HIRE_REQUESTED: 1,  # U7
    EventKind.HIRE_ARRIVED: 1,  # U7
    EventKind.HIRE_REFUSED: 1,  # U7
    EventKind.ATTRITION: 2,  # U7 — 2: `item_status` for a returned item (U13/U14)
    EventKind.DAILY_COSTS_APPLIED: 1,  # U7
    EventKind.COMMAND_REJECTED: 1,  # U10
    # A walk: the person, the tiles they will cross, and the tick the crossing started.
    # Version 1 and expected to stay there — the payload is a path and a tick, and the whole
    # design claim is that nothing per-tick has to be added to it.
    EventKind.STAFF_MOVED: 1,  # MVP U3
}

# --- the classification R11 requires --------------------------------------

HASHED_FIELDS = frozenset({"seq", "tick", "kind", "schema_ver", "rules_ver", "payload"})

METADATA_FIELDS = frozenset({"run_id", "command_id", "request_id", "ingested_at"})


class UnknownEventKind(ValueError):
    """A kind outside the closed set. Never silently skipped."""


class EnvelopeInvalid(ValueError):
    """The envelope itself is malformed, independently of its payload."""


# A fixed namespace, so a request id is a pure function of (run, sequence) in every
# process and every Python version. uuid5 is SHA-1 based and stable by definition.
REQUEST_ID_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "company-os/v1/request-id")


def request_id_for(run_id: str, raised_at_seq: int) -> str:
    """The request id for the RequestRaised event at `raised_at_seq` in `run_id`.

    Derived rather than minted, which buys two things. It is deterministic, so
    replay reconstructs the same id without storing a counter. And it is run-scoped:
    a fork re-issuing an unanswered request derives a *child*-scoped id from the
    child run, so parent and child never hold the same in-flight id and an answer
    intended for one cannot be accepted by the other (R3).
    """
    if raised_at_seq < 0:
        raise ValueError("sequence cannot be negative")
    return str(uuid.uuid5(REQUEST_ID_NAMESPACE, f"{run_id}:{raised_at_seq}"))


@dataclasses.dataclass(frozen=True, slots=True)
class Envelope:
    """One append to the log.

    Field order here matches proto/events.proto: hashed fields first, metadata
    after, so the grouping is visible in the type rather than only in a comment.
    """

    # hashed
    seq: int
    tick: int
    kind: EventKind
    schema_ver: int
    rules_ver: str
    payload: bytes

    # metadata
    run_id: str
    command_id: str = ""
    request_id: str = ""
    ingested_at: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.kind, EventKind) or self.kind is EventKind.UNSPECIFIED:
            raise UnknownEventKind(
                f"kind {self.kind!r} is not a recognised event kind. The set is closed; "
                "add it to EventKind and KIND_SCHEMA_VERSIONS deliberately."
            )
        if self.seq < 0 or self.tick < 0:
            raise EnvelopeInvalid("seq and tick are unsigned")
        if not self.rules_ver:
            raise EnvelopeInvalid(
                "rules_ver is required: every integrity guard keys on it, and an empty "
                "value would make a stale snapshot compare as current"
            )
        if not self.run_id:
            raise EnvelopeInvalid("run_id is required")
        # Validating the payload here means a non-canonical value cannot become a
        # permanent logged fact, and the error names the offending path.
        canonical.decode(self.payload)

    # --- replay identity ---------------------------------------------------

    def hashed_projection(self) -> tuple[Any, ...]:
        """The part of this envelope that replay identity is compared over (R11).

        Ingest time, correlation ids, producing host and store backend are excluded
        because they differ between two runs of the same seed. Comparing them would
        make R11 fail on runs that are identical.
        """
        return (
            self.seq,
            self.tick,
            int(self.kind),
            self.schema_ver,
            self.rules_ver,
            self.payload,
        )

    def decoded_payload(self) -> Any:
        return canonical.decode(self.payload)

    # --- wire form ---------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """A plain dict for the store and the stream. Payload stays as bytes."""
        return {
            "seq": self.seq,
            "tick": self.tick,
            "kind": int(self.kind),
            "schema_ver": self.schema_ver,
            "rules_ver": self.rules_ver,
            "payload": self.payload,
            "run_id": self.run_id,
            "command_id": self.command_id,
            "request_id": self.request_id,
            "ingested_at": self.ingested_at,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Envelope:
        try:
            kind = EventKind(raw["kind"])
        except ValueError as exc:
            raise UnknownEventKind(
                f"event kind {raw['kind']!r} is not in the closed set. Rejected rather "
                "than skipped: a skipped event is a divergence with no cause attached."
            ) from exc
        except KeyError as exc:
            raise EnvelopeInvalid("envelope has no kind") from exc

        payload = raw["payload"]
        if isinstance(payload, str):
            payload = payload.encode("utf-8")

        return cls(
            seq=int(raw["seq"]),
            tick=int(raw["tick"]),
            kind=kind,
            schema_ver=int(raw["schema_ver"]),
            rules_ver=str(raw["rules_ver"]),
            payload=payload,
            run_id=str(raw["run_id"]),
            command_id=str(raw.get("command_id", "")),
            request_id=str(raw.get("request_id", "")),
            ingested_at=str(raw.get("ingested_at", "")),
        )


def build(
    *,
    seq: int,
    tick: int,
    kind: EventKind,
    rules_ver: str,
    payload: Any,
    run_id: str,
    command_id: str = "",
    request_id: str = "",
) -> Envelope:
    """Build an envelope, taking the payload as a value and the version from the table.

    Taking `schema_ver` from KIND_SCHEMA_VERSIONS rather than from the caller is what
    keeps a producer from stamping a version its payload does not match.
    """
    try:
        schema_ver = KIND_SCHEMA_VERSIONS[kind]
    except KeyError as exc:
        raise UnknownEventKind(
            f"{kind.name} has no payload schema version. Add it to "
            "KIND_SCHEMA_VERSIONS in the same change that starts producing it."
        ) from exc

    return Envelope(
        seq=seq,
        tick=tick,
        kind=kind,
        schema_ver=schema_ver,
        rules_ver=rules_ver,
        payload=canonical.encode(payload),
        run_id=run_id,
        command_id=command_id,
        request_id=request_id,
    )


def canonical_log_projection(envelopes: list[Envelope]) -> bytes:
    """The whole-log comparison R11 specifies: two fresh runs, one seed, identical.

    Compared as a projection over sequence, tick, kind, both versions and payload —
    never over the metadata group.
    """
    return canonical.encode(
        [
            [
                envelope.seq,
                envelope.tick,
                int(envelope.kind),
                envelope.schema_ver,
                envelope.rules_ver,
                envelope.payload.decode("utf-8"),
            ]
            for envelope in envelopes
        ]
    )
