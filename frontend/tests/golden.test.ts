import { existsSync, readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { describe, expect, it } from 'vitest'

import {
  ceoStep,
  dayOf,
  hourMinuteOf,
  isDayBoundary,
  milliTilesProgressed,
  tilesProgressed,
  toTile,
  walkDurationTicks,
  walkPose,
  WALK_TILES_DENOMINATOR,
  WALK_TILES_NUMERATOR,
} from '../src/render/interpolate'
import { AV_DARK, AV_LIGHT, avatarColor, idHash, paletteIndex } from '../src/render/palette'
import { assertFixtureIsCurrent, genesisFixture } from './helpers/frames'

/**
 * The golden vectors, asserted on the TypeScript side.
 *
 * Two things in this client are reimplementations of kernel logic: the tick-space integer
 * arithmetic used to interpolate and to predict the CEO, and the avatar palette
 * derivation. Both are self-consistent by construction, so a Python test and a TypeScript
 * test can each pass while the two disagree with each other. These vectors are the only
 * build-time guard on that, which is why a missing fixture is a failure here and not a
 * skip — a skipped vector test reports green while nothing is being checked.
 *
 * Every integer in the fixtures is a string. `JSON.parse` yields doubles, which lose
 * precision above 2^53 without saying so, and the vectors deliberately include tick
 * indices past that point. Parsing with `BigInt` is the only correct reading.
 */

const HERE = dirname(fileURLToPath(import.meta.url))
const GOLDEN = join(HERE, '..', '..', 'backend', 'tests', 'fixtures', 'golden')

function load(name: string): any {
  const path = join(GOLDEN, name)
  if (!existsSync(path)) {
    throw new Error(
      `golden fixture ${name} is missing from ${GOLDEN}. Regenerate it with ` +
        '`uv run python scripts/generate_golden.py` from backend/. This is a failure, ' +
        'not a skip: these vectors are the only build-time guard on logic implemented ' +
        'in both Python and TypeScript.',
    )
  }
  return JSON.parse(readFileSync(path, 'utf8'))
}

/** A vectored tile, parsed the only correct way: as a pair of BigInts. */
function tileOf(pair: [string, string]): readonly [bigint, bigint] {
  return [BigInt(pair[0]), BigInt(pair[1])]
}

describe('the fixtures themselves', () => {
  it('are present', () => {
    for (const name of ['walk.json', 'clock.json', 'ceo.json', 'palette.json']) {
      expect(load(name)).toBeTruthy()
    }
  })

  it('fail loudly rather than skipping when one is missing', () => {
    expect(() => load('no-such-vector.json')).toThrow(/missing/)
  })

  it('fail loudly with the regeneration instruction when the genesis payload is stale', () => {
    // A missing fixture throws; a *stale* one parses cleanly and renders nothing, which is the
    // worse failure because it reports green. The helper checks the shape on read, so this
    // states that the check exists and says what to do rather than only that it fires.
    expect(() => genesisFixture()).not.toThrow()

    const stale = {
      kind: 'GENESIS',
      payload: {
        catalog: [
          {
            id: 'wi_ap_map',
            checkpoints: [{ options: [{ label: 'PDF is the record', detail: 'Searchable.' }] }],
          },
        ],
      },
    }
    expect(() => assertFixtureIsCurrent(stale as never)).toThrow(/stale/)
    expect(() => assertFixtureIsCurrent(stale as never)).toThrow(/generate_golden\.py/)
  })

  it('carry integers as strings, so precision cannot be lost on parse', () => {
    const walk = load('walk.json')
    for (const item of walk.milli_tiles_progressed) {
      expect(typeof item.elapsed).toBe('string')
      expect(typeof item.milli).toBe('string')
    }
  })
})

describe('tick-space movement matches the kernel', () => {
  const walk = load('walk.json')

  it('agrees on the walk speed rational', () => {
    expect(WALK_TILES_NUMERATOR).toBe(BigInt(walk.speed.numerator))
    expect(WALK_TILES_DENOMINATOR).toBe(BigInt(walk.speed.denominator))
  })

  it('agrees on whole tiles covered, at every vectored tick', () => {
    for (const item of walk.tiles_progressed) {
      expect(tilesProgressed(BigInt(item.elapsed))).toBe(BigInt(item.tiles))
    }
  })

  it('agrees on sub-tile interpolation, at every vectored tick', () => {
    for (const item of walk.milli_tiles_progressed) {
      expect(milliTilesProgressed(BigInt(item.elapsed))).toBe(BigInt(item.milli))
    }
  })

  it('agrees on walk duration for every vectored distance', () => {
    for (const item of walk.walk_duration_ticks) {
      expect(walkDurationTicks(BigInt(item.distance))).toBe(BigInt(item.ticks))
    }
  })

  it('agrees on where a walker is between tiles, at every vectored tick', () => {
    // The one piece of movement arithmetic the kernel never evaluates for itself: it snaps a
    // walker to whole tiles, and the sub-tile position exists only so this client can draw a
    // walk rather than a sequence of jumps. So this vector is the whole of the guard on it.
    const track = walk.track
    const origin = tileOf(track.origin)
    const path = track.path.map(tileOf)

    for (const item of track.cases) {
      const pose = walkPose(origin, path, BigInt(item.elapsed))
      expect(pose.xMilli, `at elapsed ${item.elapsed}`).toBe(BigInt(item.x_milli))
      expect(pose.yMilli, `at elapsed ${item.elapsed}`).toBe(BigInt(item.y_milli))
      expect(pose.arrived, `at elapsed ${item.elapsed}`).toBe(item.arrived)
    }
  })

  it('arrives exactly when the kernel says the walk is over', () => {
    const track = walk.track
    const duration = BigInt(track.duration_ticks)
    expect(walkDurationTicks(BigInt(track.path.length))).toBe(duration)

    const origin = tileOf(track.origin)
    const path = track.path.map(tileOf)

    expect(walkPose(origin, path, duration - 1n).arrived).toBe(false)
    expect(walkPose(origin, path, duration).arrived).toBe(true)
    // And arrival is a clamp, not an extrapolation: a tick index no run reaches still lands on
    // the destination rather than somewhere past it.
    const far = walkPose(origin, path, 2n ** 60n)
    expect([far.xMilli, far.yMilli]).toEqual([
      path[path.length - 1][0] * 1000n,
      path[path.length - 1][1] * 1000n,
    ])
  })

  it('is still exact above 2^53, where a number-based port would not be', () => {
    const beyond = walk.milli_tiles_progressed.filter(
      (item: { elapsed: string }) => BigInt(item.elapsed) > 2n ** 53n,
    )
    expect(beyond.length).toBeGreaterThan(0)

    for (const item of beyond) {
      const exact = milliTilesProgressed(BigInt(item.elapsed))
      expect(exact).toBe(BigInt(item.milli))

      // And demonstrate that the naive port really would have failed here, so this
      // test is guarding something rather than restating the one above.
      const naive = Math.floor((Number(item.elapsed) * 7 * 1000) / 90)
      expect(BigInt(naive)).not.toBe(exact)
    }
  })
})

describe('the clock matches the kernel', () => {
  const clock = load('clock.json')

  it('agrees on day, hour and minute for every vectored tick', () => {
    for (const item of clock.cases) {
      const tick = BigInt(item.tick)
      expect(dayOf(tick)).toBe(BigInt(item.day))

      const { hour, minute } = hourMinuteOf(tick)
      expect(hour).toBe(BigInt(item.hour))
      expect(minute).toBe(BigInt(item.minute))

      expect(isDayBoundary(tick)).toBe(item.day_boundary)
    }
  })
})

describe('CEO prediction matches the kernel', () => {
  const ceo = load('ceo.json')

  it('agrees on the per-tick step for every input combination', () => {
    for (const item of ceo.steps) {
      const { dxMilli, dyMilli } = ceoStep(Number(item.mask))
      expect(dxMilli).toBe(BigInt(item.dx_milli))
      expect(dyMilli).toBe(BigInt(item.dy_milli))
    }
  })

  it('agrees on milli-tile to tile rounding, including negatives', () => {
    /*
     * The negative cases are the interesting ones. Python's `//` floors and BigInt's `/`
     * truncates toward zero, so `toTile(-501)` is -1 in the kernel and would be 0 from a
     * bare BigInt divide. A one-tile disagreement between client and kernel is exactly
     * what the position echo would later surface as an unexplained divergence.
     */
    for (const item of ceo.to_tile) {
      expect(toTile(BigInt(item.milli))).toBe(BigInt(item.tile))
    }

    const negatives = ceo.to_tile.filter((item: { milli: string }) => BigInt(item.milli) < 0n)
    expect(negatives.length).toBeGreaterThan(0)
  })

  it('does not make diagonal movement faster than straight movement', () => {
    const straight = ceoStep(2) // right
    const diagonal = ceoStep(2 | 8) // right + down
    expect(diagonal.dxMilli).toBeLessThan(straight.dxMilli)
  })
})

describe('avatar palettes match the kernel', () => {
  const palette = load('palette.json')

  it('agrees on the palettes themselves', () => {
    expect([...AV_LIGHT]).toEqual(palette.light)
    expect([...AV_DARK]).toEqual(palette.dark)
  })

  it('agrees on hash, index and colour for every vectored id', () => {
    for (const item of palette.cases) {
      expect(String(idHash(item.id))).toBe(item.hash)
      expect(String(paletteIndex(item.id))).toBe(item.index)
      expect(avatarColor(item.id, false)).toBe(item.light)
      expect(avatarColor(item.id, true)).toBe(item.dark)
    }
  })

  it('covers an id long enough to wrap the 32-bit accumulator', () => {
    const long = palette.cases.filter((item: { id: string }) => item.id.length > 40)
    expect(long.length).toBeGreaterThan(0)

    // The wraparound is what makes these cases worth vectoring: an unbounded-integer
    // implementation agrees on short ids and drifts on these.
    for (const item of long) {
      expect(String(idHash(item.id))).toBe(item.hash)
    }
  })
})

describe('the genesis payload carries the company it was created against', () => {
  const genesis = genesisFixture()

  it('records which scenario, and which revision of it', () => {
    // R7. The client does not check the hash — three guards in the kernel do — but it is on the
    // wire, and a payload that stopped carrying it would mean an exported run had no provenance.
    const scenario = genesis.payload.scenario as Record<string, unknown>
    expect(typeof scenario.id).toBe('string')
    expect(scenario.id).toBe('default')
    expect(String(scenario.content_hash)).toMatch(/^[0-9a-f]{32}$/)
    expect(scenario.hash_ver).toBe(1)
  })

  it('describes every person the way the conversation surface needs them', () => {
    // M15: a company is a file, and a person in it carries a department, a title, a
    // responsibility and the tools, MCP servers and skills they are understood to have. Asserted
    // here rather than only in Python because "carries them to the client" is a claim about this
    // side of the wire, and a payload that dropped them would render an empty panel in silence.
    const roster = genesis.payload.roster as Record<string, Record<string, unknown>>
    const ids = Object.keys(roster)
    expect(ids.length).toBeGreaterThan(0)

    for (const id of ids) {
      const person = roster[id]
      for (const field of ['name', 'title', 'dept', 'rank', 'responsibility'] as const) {
        expect(typeof person[field], `${id}.${field}`).toBe('string')
        expect(person[field], `${id}.${field}`).not.toBe('')
      }
      for (const list of ['tools', 'mcp_servers', 'skills'] as const) {
        expect(Array.isArray(person[list]), `${id}.${list}`).toBe(true)
        const named = person[list] as unknown[]
        expect(named.length, `${id}.${list}`).toBeGreaterThan(0)
        // Names, and only names. Nothing here is a handle onto anything: M16 is that a tool a
        // scenario names is description, and a string is all the client is ever given.
        for (const entry of named) expect(typeof entry).toBe('string')
      }
    }
  })

  it('gives the client nothing it could invoke', () => {
    // The strings are the whole of it. If a tool ever arrived as an object with a URL or an
    // endpoint on it, this is what would fail — and that shape is the first step towards a
    // client that calls something a scenario named.
    const roster = genesis.payload.roster as Record<string, Record<string, unknown>>
    const everything = Object.values(roster).flatMap((person) => [
      ...(person.tools as string[]),
      ...(person.mcp_servers as string[]),
      ...(person.skills as string[]),
    ])

    expect(everything.length).toBeGreaterThan(0)
    for (const named of everything) {
      expect(typeof named).toBe('string')
      expect(named).not.toMatch(/^[a-z]+:\/\//)
    }
  })
})
