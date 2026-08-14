"""The state hash: one digest per sim-day, with a sub-hash per subsystem.

Without this, a determinism regression in a long run is a hand bisect. With it,
replay divergence localises to a tick and then to a subtree — the overall hash says
*that* something diverged, the sub-hashes say *where*.

Two decisions here are load-bearing.

**The hash is computed over decoded state, never over a store-returned string.**
Postgres JSONB reorders object keys where SQLite preserves them, so hashing
serialised text would produce stable hashes locally and break the first time the
schema targets Postgres — as a determinism failure, with nothing pointing at the
store. This module therefore takes decoded values and canonicalises them itself, and
refuses pre-serialised bytes outright.

**The full subsystem list is declared now, at U3, including the subsystems that stay
empty until U7 and U8.** If a subsystem joined the hash only when it gained content,
every golden hash produced in U5 and U6 would change the moment U7 landed, with
nothing recording that the change was deliberate — and it is a change neither the
event-schema version (which covers event shape) nor the rules version (which covers
tuning and multiplier order) would capture. That gap is what `STATE_SHAPE_VERSION`
closes, and `SHAPE_HISTORY` is what stops it being bumped by accident or not at all.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from contracts import canonical

#: Digest width. 16 bytes is 128 bits — collision-free for this purpose, and short
#: enough to read in a log line.
_DIGEST_BYTES = 16

#: Which subsystems the state hash covers, per shape version. Append-only: a new
#: version extends the previous list, never rewrites it, so an old checkpoint's
#: shape stays interpretable.
#:
#: Adding a subsystem means adding an entry here *and* bumping
#: STATE_SHAPE_VERSION. Doing one without the other makes SUBSYSTEMS and this table
#: disagree, and the determinism suite fails — which is the point.
SHAPE_HISTORY: dict[int, tuple[str, ...]] = {
    1: (
        # Filled by U4: geometry, roster, work.
        "world",
        "people",
        "items",
        "metrics",
        "ceo",
        # Filled by U11: raised-but-unanswered requests, as a projection of the log.
        "pending",
        # Filled by U7. Empty at U4 and U5 so their golden hashes survive U7.
        "capacity",
        "morale",
        "hiring",
        # Filled by U8.
        "lifecycle",
    ),
}

STATE_SHAPE_VERSION = 1

SUBSYSTEMS: tuple[str, ...] = SHAPE_HISTORY[STATE_SHAPE_VERSION]


@dataclass(frozen=True, slots=True)
class StateHash:
    """A checkpoint's hashes, as the DAY_CHECKPOINT event carries them."""

    overall: str
    subsystems: dict[str, str]
    shape_version: int


def digest(value: Any) -> str:
    """Canonical digest of any allowed value.

    Canonicalisation happens here rather than at the call site, so two callers cannot
    hash the same state two ways.
    """
    return hashlib.blake2b(canonical.encode(value), digest_size=_DIGEST_BYTES).hexdigest()


def subsystem_hash(value: Any) -> str:
    return digest(value)


def state_hash(state: Mapping[str, Any]) -> StateHash:
    """Hash kernel state, subsystem by subsystem.

    Refuses state that is missing a declared subsystem or carries an undeclared one.
    Both would otherwise silently change what the hash covers, which is exactly the
    failure `STATE_SHAPE_VERSION` exists to make visible.
    """
    if not isinstance(state, Mapping):
        raise TypeError(
            f"state_hash takes decoded state, got {type(state).__name__}. The hash is "
            "computed over decoded state, never over a store-returned JSON string: "
            "Postgres JSONB reorders keys where SQLite does not."
        )

    missing = [name for name in SUBSYSTEMS if name not in state]
    if missing:
        raise ValueError(
            f"state is missing declared subsystems: {missing}. They ship empty rather "
            "than absent, so that a later unit filling them does not silently change "
            "every earlier golden hash."
        )

    undeclared = [name for name in state if name not in SUBSYSTEMS]
    if undeclared:
        raise ValueError(
            f"state carries undeclared subsystems: {undeclared}. Add them to "
            f"SHAPE_HISTORY and bump STATE_SHAPE_VERSION in the same change."
        )

    subsystems = {name: digest(state[name]) for name in SUBSYSTEMS}

    # The shape version is inside the overall hash, so two different shapes can never
    # collide even if their subsystem digests happen to match.
    overall = digest({"shape_version": STATE_SHAPE_VERSION, "subsystems": subsystems})

    return StateHash(overall=overall, subsystems=subsystems, shape_version=STATE_SHAPE_VERSION)


def empty_state() -> dict[str, Any]:
    """A state dict with every declared subsystem present and empty."""
    return {name: {} for name in SUBSYSTEMS}
