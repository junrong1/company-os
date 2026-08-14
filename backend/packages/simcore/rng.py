"""Counter-based randomness: a pure function of coordinates, with no state.

Every draw is derived from `(run_seed, tick, purpose_tag, entity_id)` through a
SplitMix64-class mixer. Three consequences, and each is the reason a stateful
generator was rejected rather than a bonus on top of it:

*There is no generator state to snapshot.* A snapshot is a pure function of the log
prefix, and a `random.Random` would add an opaque blob to it that has to be
serialised, versioned and restored exactly.

*Adding a stochastic consumer does not renumber existing draws.* With a sequential
generator, inserting one draw shifts every draw after it, which silently invalidates
every seeded fixture in the suite. Here a new consumer takes a new `purpose_tag` and
occupies its own coordinate space.

*It is reimplementable in TypeScript.* If the client ever predicts a stochastic
outcome, the same integers produce the same answer — the mixer is 64-bit integer
arithmetic, expressible with BigInt.

The standard library was not an option regardless: CPython guarantees stability only
for `random()` and the compatible seeder. `shuffle`, `sample`, `choices` and
`randrange` are explicitly subject to change between versions, so a seeded fixture
built on them can break on a patch upgrade.

Note that draws are logged anyway, per origin R3, which makes replay independent of
even this code. What this module buys is *seed* determinism — two fresh runs from one
seed producing one run — which logging cannot provide and which the capacity-loop
success criterion requires.

**There is no float API here, deliberately.** The plan calls for a normalised draw;
it is provided as integer-domain helpers instead. A float-returning mixer is the
easiest route for a float to reach kernel state in violation of R6, and every
consumer in this simulation wants an integer decision anyway.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from functools import lru_cache
from typing import TypeVar

_MASK64 = (1 << 64) - 1
_GOLDEN_GAMMA = 0x9E3779B97F4A7C15

T = TypeVar("T")


def _splitmix64(value: int) -> int:
    """The SplitMix64 finalizer: strong avalanche, cheap, and easy to port."""
    value = (value + _GOLDEN_GAMMA) & _MASK64
    mixed = value
    mixed = ((mixed ^ (mixed >> 30)) * 0xBF58476D1CE4E5B9) & _MASK64
    mixed = ((mixed ^ (mixed >> 27)) * 0x94D049BB133111EB) & _MASK64
    return (mixed ^ (mixed >> 31)) & _MASK64


@lru_cache(maxsize=2048)
def purpose_key(purpose: str) -> int:
    """A stable 64-bit coordinate for a purpose tag.

    Emphatically not `hash()`: Python salts string hashing per process, so a run
    would produce different draws on every restart. BLAKE2b is stable by
    specification, across processes and across interpreter versions.
    """
    if not purpose:
        raise ValueError("a purpose tag is required; it is what separates draw streams")
    digest = hashlib.blake2b(purpose.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big")


@lru_cache(maxsize=8192)
def entity_key(entity: str) -> int:
    """A stable 64-bit coordinate for an entity id. Empty means "no entity"."""
    digest = hashlib.blake2b(entity.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big")


def draw(run_seed: int, tick: int, purpose: str, entity: str = "") -> int:
    """A uniform 64-bit integer for these coordinates. Same inputs, same answer.

    Order matters only in that it is fixed: seed, then tick, then purpose, then
    entity, each folded through the mixer.
    """
    if tick < 0:
        raise ValueError(f"tick cannot be negative; got {tick}")

    accumulator = run_seed & _MASK64
    for coordinate in (tick & _MASK64, purpose_key(purpose), entity_key(entity)):
        accumulator = _splitmix64(accumulator ^ coordinate)
    return accumulator


def below(
    bound: int, *, run_seed: int, tick: int, purpose: str, entity: str = ""
) -> int:
    """A draw in `[0, bound)`.

    Uses a widening multiply rather than a modulo. Modulo introduces bias toward the
    low end of the range; the widening multiply's residual bias is on the order of
    `bound / 2^64`, which for any bound this simulation uses is far below anything
    observable. Rejection sampling would be exact but needs a retry coordinate,
    which is state by another name.
    """
    if bound <= 0:
        raise ValueError(f"bound must be positive; got {bound}")
    return (draw(run_seed, tick, purpose, entity) * bound) >> 64


def chance(
    numerator: int,
    denominator: int,
    *,
    run_seed: int,
    tick: int,
    purpose: str,
    entity: str = "",
) -> bool:
    """Does a `numerator/denominator` event occur? Probability as two integers."""
    if denominator <= 0:
        raise ValueError(f"denominator must be positive; got {denominator}")
    if numerator <= 0:
        return False
    if numerator >= denominator:
        return True
    return below(
        denominator, run_seed=run_seed, tick=tick, purpose=purpose, entity=entity
    ) < numerator


def choice(
    options: Sequence[T], *, run_seed: int, tick: int, purpose: str, entity: str = ""
) -> T:
    """One member of `options`.

    Indexing a sequence rather than using `random.choice`, whose algorithm CPython
    does not promise to keep stable.
    """
    if not options:
        raise ValueError("cannot choose from an empty sequence")
    index = below(
        len(options), run_seed=run_seed, tick=tick, purpose=purpose, entity=entity
    )
    return options[index]
