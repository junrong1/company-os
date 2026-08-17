"""Sim-time: an integer tick count, and everything derived from it.

The quantum is one sim-minute, recorded at genesis. Every temporal quantity in the
simulation — the clock, burned effort, positions, arrival ticks — is derived from the
tick index, so nothing needs a wall-clock read and nothing accumulates a float.

The constants come from the prototype rather than being invented:

    HOURS_PER_SEC = 0.6      ->  36 ticks per wall second at x1, 108 at x3
    DAY_START = 9, DAY_END = 18  ->  a nine-hour day, so 540 ticks

**The rate multiplier is deliberately absent from this module.** Position and
progress are functions of the tick index alone. x1 and x3 differ only in how quickly
wall-clock time produces ticks, never in what a tick means — which is what keeps
replay speed out of the run, and is why degrading the multiplier under lag is free.

One conversion is a real change from the prototype rather than a port of it. The
prototype walks at `2.8` tiles per *wall* second (`step(p, 2.8, dt)`), which cannot
replay: latency and frame timing would become part of the run. Expressed in sim-time
that is 2.8 / 0.6 = 14/3 tiles per sim-hour, and with 60 ticks to the sim-hour it is
exactly **7 tiles per 90 ticks** — a ratio of two integers, so arrival ticks are
computable, no float enters the arithmetic, and the same result is reproducible in
TypeScript with BigInt.
"""

from __future__ import annotations

from collections.abc import Sequence

# --- the quantum ----------------------------------------------------------

#: Sim-seconds per tick. Recorded at genesis and immutable for a run.
QUANTUM_SIM_SECONDS = 60

TICKS_PER_SIM_HOUR = 60

#: How many ticks one wall second yields at the base rate. Used by the tick loop
#: (U9) and never stored in kernel state or written to the log — storing it would
#: make replay speed part of the run.
TICKS_PER_WALL_SECOND_AT_BASE_RATE = 36

# --- the working day ------------------------------------------------------

DAY_START_HOUR = 9
DAY_END_HOUR = 18
SIM_HOURS_PER_DAY = DAY_END_HOUR - DAY_START_HOUR
TICKS_PER_SIM_DAY = SIM_HOURS_PER_DAY * TICKS_PER_SIM_HOUR  # 540

# --- walking --------------------------------------------------------------

#: Walk speed as an exact rational: WALK_TILES_NUMERATOR tiles per
#: WALK_TILES_DENOMINATOR ticks. See the module docstring for the derivation.
WALK_TILES_NUMERATOR = 7
WALK_TILES_DENOMINATOR = 90

#: Sub-tile positions are carried in thousandths of a tile. Integer fixed point
#: rather than a float, for the same reason as everything else here.
MILLI_TILES_PER_TILE = 1000


def day_of(tick: int) -> int:
    """The 1-based day number containing `tick`. Tick 0 is day 1."""
    _check(tick)
    return tick // TICKS_PER_SIM_DAY + 1


def hour_minute_of(tick: int) -> tuple[int, int]:
    """Clock time within the working day, as two integers."""
    _check(tick)
    within_day = tick % TICKS_PER_SIM_DAY
    return (
        DAY_START_HOUR + within_day // TICKS_PER_SIM_HOUR,
        within_day % TICKS_PER_SIM_HOUR,
    )


def is_day_boundary(tick: int) -> bool:
    """True on the tick a new day opens, including tick 0.

    This is where the state-hash checkpoint is emitted, which makes it a per-tick
    cost spike on one tick in 540 — the reason the tick budget is measured per wake
    rather than per tick.
    """
    _check(tick)
    return tick % TICKS_PER_SIM_DAY == 0


def tick_of_day_start(day: int) -> int:
    """The first tick of a 1-based day number."""
    if day < 1:
        raise ValueError(f"day is 1-based; got {day}")
    return (day - 1) * TICKS_PER_SIM_DAY


def ticks_from_sim_hours(hours: int) -> int:
    return hours * TICKS_PER_SIM_HOUR


def walk_duration_ticks(distance_tiles: int) -> int:
    """How many ticks it takes to walk `distance_tiles`.

    Rounded up. A floor would let an actor arrive before it had covered the
    distance, which is visible on screen as sliding through the last tile.
    """
    if distance_tiles < 0:
        raise ValueError(f"distance cannot be negative; got {distance_tiles}")
    if distance_tiles == 0:
        return 0
    # Ceiling division without float or math.ceil.
    return -(-distance_tiles * WALK_TILES_DENOMINATOR // WALK_TILES_NUMERATOR)


def tiles_progressed(elapsed_ticks: int) -> int:
    """Whole tiles covered after `elapsed_ticks` of walking."""
    _check(elapsed_ticks)
    return elapsed_ticks * WALK_TILES_NUMERATOR // WALK_TILES_DENOMINATOR


def milli_tiles_progressed(elapsed_ticks: int) -> int:
    """Sub-tile progress in thousandths of a tile, for interpolation.

    The client reimplements this for smooth movement, so it is one of the values the
    golden vectors cover — including above 2^53, where a naive numeric port loses
    precision silently.
    """
    _check(elapsed_ticks)
    return (
        elapsed_ticks * WALK_TILES_NUMERATOR * MILLI_TILES_PER_TILE
    ) // WALK_TILES_DENOMINATOR


def walk_position_milli(
    origin: tuple[int, int],
    path: Sequence[tuple[int, int]],
    elapsed_ticks: int,
) -> tuple[int, int]:
    """Where a walker is between two tiles, in milli-tiles.

    The kernel itself never needs this: `step._advance_walker` snaps to `path[covered - 1]`,
    because a sub-tile coordinate in hashed state would be a hashed value nothing reads and
    every day-boundary hash would carry it. The **client** needs it, because a figure that
    jumps a whole tile every thirteen ticks does not read as walking.

    So the definition lives here for exactly the reason `milli_tiles_progressed` does — the
    client reimplements it, and the golden vector is the only build-time guard on the
    reimplementation. Written in the kernel rather than only in TypeScript so that there *is*
    a vector to generate: a client-only interpolation would be self-consistent and unchecked,
    which is the failure mode the vectors exist for.

    `path` follows `world.find_path`: the origin is excluded and the target included, so the
    tile after `n` whole tiles of progress is `path[n - 1]` and `origin` is where `n` is zero.
    Progress past the end of the path is the destination, not an extrapolation — arrival is a
    clamp, and `walk_duration_ticks(len(path))` is the tick it happens at.
    """
    _check(elapsed_ticks)

    if not path:
        return (origin[0] * MILLI_TILES_PER_TILE, origin[1] * MILLI_TILES_PER_TILE)

    milli = milli_tiles_progressed(elapsed_ticks)
    covered = milli // MILLI_TILES_PER_TILE
    within = milli % MILLI_TILES_PER_TILE

    if covered >= len(path):
        return (path[-1][0] * MILLI_TILES_PER_TILE, path[-1][1] * MILLI_TILES_PER_TILE)

    here = origin if covered == 0 else path[covered - 1]
    ahead = path[covered]

    # Adjacent tiles, so each difference is exactly one or zero: this is a straight
    # interpolation and not a general line-drawing routine.
    return (
        here[0] * MILLI_TILES_PER_TILE + (ahead[0] - here[0]) * within,
        here[1] * MILLI_TILES_PER_TILE + (ahead[1] - here[1]) * within,
    )


def _check(tick: int) -> None:
    if tick < 0:
        raise ValueError(f"sim-time does not run before tick 0; got {tick}")
