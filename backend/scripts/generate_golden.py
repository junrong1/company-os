#!/usr/bin/env python
"""Generate the golden vectors the TypeScript side asserts against.

    uv run python scripts/generate_golden.py

Golden vectors exist because a few things are implemented in *both* languages, and
the plan is explicit that they are the only build-time guard on that duplication. The
client reimplements integer movement so it can predict the CEO at zero latency, and it
derives avatar palettes from ids. Either could drift from the kernel in a way that no
Python test and no TypeScript test would notice, because each would be self-consistent.

Two properties of the format are deliberate:

**Every integer is emitted as a string.** `JSON.parse` produces doubles, which lose
precision silently above 2^53 — and silently is the problem. Strings force the
TypeScript side to choose a parse, and the only correct choice is BigInt. If the
vectors were numbers, a naive port would pass every case that happened to stay in
range, which is precisely the failure mode the plan calls out.

**The vectors include values above 2^53 on purpose.** A tick index that large is not
reachable in a real run; it is there so that a numeric port fails here rather than in
production.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parent.parent
GOLDEN = BACKEND / "tests" / "fixtures" / "golden"

# `packages/` and `services/` are import roots rather than installed distributions, and the
# `pythonpath` setting in pyproject.toml that arranges that is *pytest's*. A script run
# directly gets none of it, so it arranges its own — otherwise the command the README
# documents fails on a bare `import simcore`.
for root in ("packages", "services"):
    path = str(BACKEND / root)
    if path not in sys.path:
        sys.path.insert(0, path)

from simcore import people as roster  # noqa: E402 - after the path bootstrap above
from simcore import step as sim  # noqa: E402
from simcore import time as simtime  # noqa: E402

#: The prototype's avatar palettes, from `company-os.html:1530`.
AV_LIGHT = (
    "#2a55c9",
    "#0f7b6c",
    "#a8541c",
    "#6b46c1",
    "#1d6fb8",
    "#8a5a00",
    "#b3261e",
    "#3f6212",
)
AV_DARK = (
    "#5b82ea",
    "#2f9c8a",
    "#c9762f",
    "#8f6ee0",
    "#3f8fd6",
    "#b58325",
    "#d9564b",
    "#5f8f2a",
)


def js_hash(text: str) -> int:
    """The prototype's `hash(s)`, including its 32-bit truncation.

        function hash(s) {
          let h = 0;
          for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
          return Math.abs(h);
        }

    The `| 0` is the whole difficulty. It coerces to a *signed* 32-bit integer on every
    iteration, so the accumulator wraps and can go negative — and `Math.abs` then folds
    it back. A Python port using unbounded integers agrees for short strings and
    diverges for longer ones, which is exactly the kind of drift a golden vector is for.
    """
    accumulator = 0
    for character in text:
        accumulator = (accumulator * 31 + ord(character)) & 0xFFFFFFFF
        if accumulator >= 0x80000000:
            accumulator -= 0x100000000
    return abs(accumulator)


def avatar_palette(person_id: str) -> dict[str, Any]:
    index = js_hash(person_id) % len(AV_LIGHT)
    return {
        "id": person_id,
        "hash": str(js_hash(person_id)),
        "index": str(index),
        "light": AV_LIGHT[index],
        "dark": AV_DARK[index],
    }


def walk_vector() -> dict[str, Any]:
    """Tick-derived movement: the arithmetic the client reimplements for interpolation."""
    # Small, hand-checkable ticks, then values that break a double.
    ticks = [
        0,
        1,
        7,
        13,
        45,
        89,
        90,
        91,
        180,
        539,
        540,
        12_345,
        2**31,
        2**52,
        2**53 - 1,
        2**53,
        2**53 + 1,
        2**53 + 7,
        2**60 + 12_345,
    ]
    distances = [0, 1, 2, 7, 13, 14, 27, 100, 2**53 + 1]

    return {
        "speed": {
            "numerator": str(simtime.WALK_TILES_NUMERATOR),
            "denominator": str(simtime.WALK_TILES_DENOMINATOR),
            "milli_per_tile": str(simtime.MILLI_TILES_PER_TILE),
        },
        "tiles_progressed": [
            {"elapsed": str(tick), "tiles": str(simtime.tiles_progressed(tick))}
            for tick in ticks
        ],
        "milli_tiles_progressed": [
            {"elapsed": str(tick), "milli": str(simtime.milli_tiles_progressed(tick))}
            for tick in ticks
        ],
        "walk_duration_ticks": [
            {"distance": str(distance), "ticks": str(simtime.walk_duration_ticks(distance))}
            for distance in distances
        ],
    }


def clock_vector() -> dict[str, Any]:
    ticks = [0, 1, 59, 60, 539, 540, 541, 1079, 1080, 12_345, 2**53 + 1]
    return {
        "ticks_per_sim_day": str(simtime.TICKS_PER_SIM_DAY),
        "ticks_per_sim_hour": str(simtime.TICKS_PER_SIM_HOUR),
        "day_start_hour": str(simtime.DAY_START_HOUR),
        "cases": [
            {
                "tick": str(tick),
                "day": str(simtime.day_of(tick)),
                "hour": str(simtime.hour_minute_of(tick)[0]),
                "minute": str(simtime.hour_minute_of(tick)[1]),
                "day_boundary": simtime.is_day_boundary(tick),
            }
            for tick in ticks
        ],
    }


def ceo_vector() -> dict[str, Any]:
    """The CEO's per-tick step, which the client predicts locally.

    Collision needs the floor, so that is not vectored here — this pins the arithmetic,
    and R33's periodic position echo is the runtime guard on the rest. The plan is
    explicit that vectors only prove the cases someone thought to vector.
    """
    masks = [
        sim.INPUT_RIGHT,
        sim.INPUT_LEFT,
        sim.INPUT_UP,
        sim.INPUT_DOWN,
        sim.INPUT_RIGHT | sim.INPUT_DOWN,
        sim.INPUT_LEFT | sim.INPUT_UP,
        sim.INPUT_LEFT | sim.INPUT_RIGHT,
        sim.INPUT_UP | sim.INPUT_DOWN,
        0,
    ]

    def axis_delta(mask: int) -> tuple[int, int]:
        dx = (1 if mask & sim.INPUT_RIGHT else 0) - (1 if mask & sim.INPUT_LEFT else 0)
        dy = (1 if mask & sim.INPUT_DOWN else 0) - (1 if mask & sim.INPUT_UP else 0)
        if dx == 0 and dy == 0:
            return (0, 0)
        per_axis = (
            sim.CEO_DIAGONAL_MILLI_PER_TICK if dx and dy else sim.CEO_STRAIGHT_MILLI_PER_TICK
        )
        return (dx * per_axis, dy * per_axis)

    milli_cases = [0, 499, 500, 501, 1000, 1499, 1500, 3144, -1, -500, -501, 2**53 + 1]

    return {
        "straight_milli_per_tick": str(sim.CEO_STRAIGHT_MILLI_PER_TICK),
        "diagonal_milli_per_tick": str(sim.CEO_DIAGONAL_MILLI_PER_TICK),
        "lookahead_milli": str(sim.CEO_LOOKAHEAD_MILLI),
        "input_bits": {
            "left": str(sim.INPUT_LEFT),
            "right": str(sim.INPUT_RIGHT),
            "up": str(sim.INPUT_UP),
            "down": str(sim.INPUT_DOWN),
        },
        "steps": [
            {
                "mask": str(mask),
                "dx_milli": str(axis_delta(mask)[0]),
                "dy_milli": str(axis_delta(mask)[1]),
            }
            for mask in masks
        ],
        "to_tile": [{"milli": str(value), "tile": str(sim.to_tile(value))} for value in milli_cases],
    }


def palette_vector() -> dict[str, Any]:
    """Avatar palettes for every roster id, plus strings that exercise the wraparound."""
    extras = [
        "",
        "a",
        "z",
        "dir_sales",
        "a-very-long-identifier-that-overflows-the-32-bit-accumulator-many-times-over",
        "stf_ap" * 12,
    ]
    ids = [person.id for person in roster.PEOPLE] + extras

    return {
        "light": list(AV_LIGHT),
        "dark": list(AV_DARK),
        "cases": [avatar_palette(person_id) for person_id in ids],
    }


def genesis_vector() -> dict[str, Any]:
    """A real genesis payload, so the client parses the shape the kernel actually sends.

    Not a hand-written fixture. The client's HUD reads `metric_defs`, its DAG lays out
    `catalog`, and both of those arrived in the payload with U13 and U14 — a fixture typed
    out by hand would be a second, drifting opinion about the very shape it exists to pin,
    and the drift would show up as a client that renders nothing against a live gateway.

    Integers stay integers here, unlike the movement vectors. This fixture is a *payload*,
    and the client has to parse it exactly as it comes off the wire; converting the numbers
    to strings would test a format nothing sends. The one field the client must read as a
    bigint — the tick index — is exercised by `clock.json` and `walk.json` instead.
    """
    from contracts.envelope import KIND_SCHEMA_VERSIONS

    _, emitted = sim.new_run(run_seed=0xC0FFEE)
    genesis = emitted[0]
    return {
        "kind": genesis.kind.name,
        # Emitted so the frontend's frame helper can read the version rather than write it
        # down. The hardcoded copy had been stale for two payload versions — inert, since
        # nothing reads `schema_ver`, but a number in a fixture that quietly stops being true
        # is the same shape of problem as a bound nothing enforces.
        "schema_ver": KIND_SCHEMA_VERSIONS[genesis.kind],
        "payload": genesis.payload,
    }


def main() -> int:
    GOLDEN.mkdir(parents=True, exist_ok=True)

    vectors = {
        "walk.json": walk_vector(),
        "clock.json": clock_vector(),
        "ceo.json": ceo_vector(),
        "palette.json": palette_vector(),
        "genesis.json": genesis_vector(),
    }

    header = (
        "Generated by backend/scripts/generate_golden.py. Do not edit. "
        "Every integer is a string: JSON.parse loses precision above 2^53, and the "
        "point of these vectors is to catch exactly that."
    )

    for name, payload in vectors.items():
        path = GOLDEN / name
        path.write_text(json.dumps({"_": header, **payload}, indent=2, sort_keys=True) + "\n")
        print(f"wrote {path.relative_to(BACKEND)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
