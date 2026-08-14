"""Run export: the machine leaves as an artifact.

R32. A run exports as a self-contained file carrying its log, its genesis, and both
versions, and imports into a different process to replay to the same state hash.

This is also the calibrated substitute for backup rotation. There is no deploy and no
on-call here; "export the run" is the honest equivalent of a backup for a system whose runs
are already disposable across a tuning change.

**Both versions travel with it, and are checked on import.** An artifact that replayed under
whatever rules happened to be running would produce different numbers from the run it claims
to be, and would look authoritative doing so. The import refuses instead, and names what it
found — which is what makes the artifact self-describing rather than merely portable.

The container is canonical bytes, so exporting the same run twice produces identical files.
That matters more than it sounds: it means an artifact can be diffed, and two people can
confirm they are looking at the same run without replaying it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from contracts import canonical
from contracts.envelope import EVENT_SCHEMA_VERSION, Envelope
from simcore import hashing
from simcore import log as folder
from simcore import step as sim
from simcore.rates import RULES_VERSION


class ImportRefused(Exception):
    """The artifact will not be imported, and says why."""


#: Bumped when the container's shape changes.
ARTIFACT_VERSION = 1


@dataclass(slots=True)
class Artifact:
    run_id: str
    events: list[Envelope]
    rules_ver: str
    event_schema_ver: int
    state_shape_ver: int
    through_tick: int
    state_hash: str


def export_run(
    run_id: str,
    events: list[Envelope],
    through_tick: int,
    state_hash: str,
) -> bytes:
    """Serialise a run into a self-contained artifact.

    `through_tick` is carried explicitly because the log does not imply it: with no tick
    event per quantum, a run that ticked on past its last event leaves no trace of that in
    the log. Without it, an import would replay a shorter run and disagree about the hash
    for a reason that looks like corruption.
    """
    if not events:
        raise ValueError("nothing to export: the log is empty")

    return canonical.encode(
        {
            "artifact_version": ARTIFACT_VERSION,
            "run_id": run_id,
            "rules_ver": RULES_VERSION,
            "event_schema_ver": EVENT_SCHEMA_VERSION,
            "state_shape_ver": hashing.STATE_SHAPE_VERSION,
            "through_tick": through_tick,
            "state_hash": state_hash,
            "events": [
                {
                    "seq": envelope.seq,
                    "tick": envelope.tick,
                    "kind": int(envelope.kind),
                    "schema_ver": envelope.schema_ver,
                    "rules_ver": envelope.rules_ver,
                    # Canonical bytes are UTF-8 text by construction, so this stays
                    # readable and byte-stable rather than base64.
                    "payload": envelope.payload.decode("utf-8"),
                    "run_id": envelope.run_id,
                    "command_id": envelope.command_id,
                    "request_id": envelope.request_id,
                }
                for envelope in sorted(events, key=lambda item: item.seq)
            ],
        }
    )


def read_artifact(raw: bytes, running_rules_ver: str | None = None) -> Artifact:
    """Parse and validate an artifact without replaying it."""
    try:
        decoded: dict[str, Any] = canonical.decode(raw)
    except canonical.NotCanonical as exc:
        raise ImportRefused(f"the artifact is not canonical: {exc}") from exc

    found_version = int(decoded.get("artifact_version", 0))
    if found_version != ARTIFACT_VERSION:
        raise ImportRefused(
            f"artifact version {found_version}; this kernel reads {ARTIFACT_VERSION}"
        )

    running = running_rules_ver or RULES_VERSION
    if decoded["rules_ver"] != running:
        raise ImportRefused(
            f"this run was recorded under rules version {decoded['rules_ver']}; the running "
            f"rules are {running}. Refusing to import: replaying it under different tuning "
            "constants would produce different numbers while looking authoritative. The "
            "artifact stays valid and self-describing — it needs a kernel built for its own "
            "rules version."
        )

    if int(decoded["event_schema_ver"]) != EVENT_SCHEMA_VERSION:
        raise ImportRefused(
            f"artifact carries event schema version {decoded['event_schema_ver']}; this "
            f"kernel writes {EVENT_SCHEMA_VERSION}"
        )

    if int(decoded["state_shape_ver"]) != hashing.STATE_SHAPE_VERSION:
        raise ImportRefused(
            f"artifact covers state shape version {decoded['state_shape_ver']}; this kernel "
            f"hashes {hashing.STATE_SHAPE_VERSION}, so the hashes are not comparable"
        )

    events = [
        Envelope.from_dict({**recorded, "payload": recorded["payload"].encode("utf-8")})
        for recorded in decoded["events"]
    ]

    return Artifact(
        run_id=decoded["run_id"],
        events=events,
        rules_ver=decoded["rules_ver"],
        event_schema_ver=int(decoded["event_schema_ver"]),
        state_shape_ver=int(decoded["state_shape_ver"]),
        through_tick=int(decoded["through_tick"]),
        state_hash=decoded["state_hash"],
    )


def import_and_replay(
    raw: bytes, running_rules_ver: str | None = None
) -> tuple[sim.State, Artifact]:
    """Import an artifact and replay it, refusing if the hash does not reproduce.

    The hash check is the whole value of the format: an artifact that imports but replays to
    something else is worse than one that refuses, because it looks like the run it claims to
    be.
    """
    artifact = read_artifact(raw, running_rules_ver)

    state = folder.replay_to_state(
        artifact.events,
        running_rules_ver=running_rules_ver,
        strict=True,
        through_tick=artifact.through_tick,
    )

    reproduced = hashing.state_hash(sim.snapshot(state)).overall
    if reproduced != artifact.state_hash:
        raise ImportRefused(
            f"the artifact for run {artifact.run_id} replays to state hash {reproduced}, but "
            f"records {artifact.state_hash}. The log and the recorded outcome disagree."
        )

    return state, artifact
