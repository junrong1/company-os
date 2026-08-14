import { afterEach, describe, expect, it } from 'vitest'

import { CEO_ID, PALETTE_OVERRIDE, personPalette } from '../src/render/palettes'
import { DIRS, FRAMES, SPRITE_HEIGHT, SPRITE_WIDTH, characterSheet, depthSort, walkFrame } from '../src/render/actors'
import { CHARACTER_PALETTE } from '../src/render/sprites'
import { type FloorData, TILE, buildGrid, walkable } from '../src/render/floor'
import {
  CEO_DIAGONAL_MILLI_PER_TICK,
  CEO_LOOKAHEAD_MILLI,
  CEO_STRAIGHT_MILLI_PER_TICK,
  INPUT_DOWN,
  INPUT_LEFT,
  INPUT_RIGHT,
  INPUT_UP,
} from '../src/render/interpolate'
import { RenderClock, SLEW_THRESHOLD_TICKS } from '../src/render/clock'
import {
  CeoPrediction,
  MIN_INPUT_LEAD_TICKS,
  actorsFromStore,
  bitmaskFor,
  inputLeadTicks,
  shouldRestateHeldInput,
} from '../src/ui/stage'
import { useRunStore } from './helpers/store-helpers'
import { genesisFixture, genesisFrame } from './helpers/frames'

/**
 * The CEO as an actor on the floor.
 *
 * The projection is the unit under test rather than the canvas: what the renderer draws is
 * decided entirely by the list `actorsFromStore` returns and by the depth sort applied to it,
 * and both are decidable without a drawing surface. Visual parity stays judged by eye.
 */

afterEach(() => {
  useRunStore.getState().reset()
})

/** The roster ids the generated genesis fixture carries. */
function rosterIds(): string[] {
  return Object.keys(genesisFixture().payload.roster as Record<string, unknown>)
}

// =========================================================================
// R1: the CEO is on the floor, and findable
// =========================================================================

describe('the CEO in the actor projection', () => {
  it('is absent before genesis, so nothing is drawn into a floor that has not arrived', () => {
    expect(actorsFromStore()).toEqual([])
  })

  it('joins the projection once run state has a floor (R1)', () => {
    useRunStore.getState().apply(genesisFrame())

    const actors = actorsFromStore()
    const ceo = actors.find((actor) => actor.id === CEO_ID)

    expect(ceo).toBeDefined()
    // Everyone on the roster is still there; the CEO is an addition, not a replacement.
    for (const id of rosterIds()) {
      expect(actors.some((actor) => actor.id === id)).toBe(true)
    }
  })

  it('spawns where the kernel spawned them, not at an origin default', () => {
    useRunStore.getState().apply(genesisFrame())

    const [spawnX, spawnY] = (
      genesisFixture().payload.floor as { spawn: [number, number] }
    ).spawn
    const ceo = actorsFromStore().find((actor) => actor.id === CEO_ID)

    expect(ceo?.xMilli).toBe(spawnX * 1000)
    expect(ceo?.yMilli).toBe(spawnY * 1000)
    // A spawn of (0, 0) would make this test pass for the wrong reason.
    expect(spawnX + spawnY).toBeGreaterThan(0)
  })

  it('takes a supplied pose over the authoritative position, because the drawn one is predicted', () => {
    useRunStore.getState().apply(genesisFrame())

    const ceo = actorsFromStore({
      xMilli: 7000,
      yMilli: 9000,
      facing: 'left',
      moving: true,
    }).find((actor) => actor.id === CEO_ID)

    expect(ceo?.xMilli).toBe(7000)
    expect(ceo?.yMilli).toBe(9000)
    expect(ceo?.facing).toBe('left')
    expect(ceo?.moving).toBe(true)
  })

  it('rests when no direction is held, since the CEO has no walking state to read', () => {
    useRunStore.getState().apply(genesisFrame())

    const ceo = actorsFromStore().find((actor) => actor.id === CEO_ID)
    expect(ceo?.moving).toBe(false)
    expect(ceo?.waiting).toBe(false)
  })
})

// =========================================================================
// R1: the accent, and that it is not shared
// =========================================================================

describe('the CEO palette', () => {
  it('differs from every staff palette, so the player never loses themselves in the crowd', () => {
    const ceo = personPalette(CEO_ID)

    for (const id of rosterIds()) {
      const staff = personPalette(id)
      // The top is the accent slot, and it is the one that has to be unique.
      expect([staff.t, staff.T, staff.u]).not.toEqual([ceo.t, ceo.T, ceo.u])
    }
  })

  it('is the app accent rather than a hashed choice', () => {
    // Read off the override rather than re-deriving it: this asserts the override is what
    // reaches the sheet, which is the thing that could silently stop being true.
    expect(personPalette(CEO_ID).t).toBe(PALETTE_OVERRIDE[CEO_ID].t)
    expect(personPalette(CEO_ID).t).toBe('#57c3c2')
  })

  it('generates a rectangular sheet with a clean palette, like every other person', () => {
    const cache = new Map<string, ReturnType<typeof characterSheet>>()
    const sheet = characterSheet(CEO_ID, cache, () => ({
      width: 0,
      height: 0,
      getContext: () => null,
    }) as unknown as HTMLCanvasElement)

    expect(sheet.canvas.width).toBe(SPRITE_WIDTH * FRAMES)
    expect(sheet.canvas.height).toBe(SPRITE_HEIGHT * DIRS.length)

    // Every glyph the art uses resolves to a colour or to a deliberate transparent.
    for (const key of CHARACTER_PALETTE) {
      expect(key in sheet.palette).toBe(true)
    }
  })
})

// =========================================================================
// Depth: the CEO sorts with everyone else
// =========================================================================

describe('depth sorting the CEO', () => {
  /** The projection, reduced to the depth the renderer sorts on. */
  function depthOf(yMilli: number): number {
    return Math.round((yMilli * TILE) / 1000)
  }

  it('puts a CEO standing below a desk in front of it, and above it behind', () => {
    const desk = { depth: depthOf(5000), draw: () => {} }
    const below = { depth: depthOf(6000), draw: () => {} }
    const above = { depth: depthOf(4000), draw: () => {} }

    expect(depthSort([desk, below]).indexOf(below)).toBe(1)
    expect(depthSort([desk, above]).indexOf(above)).toBe(0)
  })

  it('sorts the CEO against staff by the same y, with no special case', () => {
    useRunStore.getState().apply(genesisFrame())

    const actors = actorsFromStore({ xMilli: 0, yMilli: 999_000, facing: 'down', moving: false })
    const drawables = actors.map((actor) => ({
      id: actor.id,
      depth: depthOf(actor.yMilli),
      draw: () => {},
    }))

    // Standing further down the floor than anybody, the CEO draws last.
    expect(depthSort(drawables).at(-1)).toMatchObject({ id: CEO_ID })
  })
})

// =========================================================================
// R2, R3, R4: predicting the CEO's position, and keeping it honest
// =========================================================================

/** The real floor, so collision is checked against the geometry the kernel used. */
function floorFixture(): FloorData {
  return genesisFixture().payload.floor as unknown as FloorData
}

/**
 * A prediction seeded at spawn, and the open direction to walk it in.
 *
 * The direction is discovered rather than assumed: the floor is generated, and a test that
 * hard-codes "left is open" turns a floor change into a movement regression that is not one.
 */
function atSpawn(): {
  prediction: CeoPrediction
  open: number
  spawn: [number, number]
  /** Ticks that can be walked in `open` before the wall lookahead stops the CEO. */
  clearTicks: number
} {
  const floor = floorFixture()
  const grid = buildGrid(floor)
  const [sx, sy] = floor.spawn

  const candidates: Array<[number, number, number]> = [
    [INPUT_LEFT, -1, 0],
    [INPUT_RIGHT, 1, 0],
    [INPUT_UP, 0, -1],
    [INPUT_DOWN, 0, 1],
  ]

  // The longest clear run, not merely an open neighbour. The floor is generated, so a test
  // that assumes room to walk turns a tighter layout into a movement regression that is not
  // one — which is exactly what a two-tile assumption did here.
  let best: [number, number] = [0, 0]
  for (const [mask, dx, dy] of candidates) {
    let clear = 0
    while (clear < 20 && walkable(grid, sx + dx * (clear + 1), sy + dy * (clear + 1))) clear += 1
    if (clear > best[1]) best = [mask, clear]
  }
  if (best[1] < 2) throw new Error('the spawn tile has nowhere to walk to')

  const prediction = new CeoPrediction()
  prediction.useFloor(floor)
  prediction.seed({ xMilli: sx * 1000, yMilli: sy * 1000, facing: 'down' }, 0n)

  // How many steps fit in the clear run, less the lookahead probe's reach. Derived from the
  // constants rather than measured by walking, so a broken predictor cannot quietly hand these
  // tests a clearance of zero and make every movement assertion vacuous.
  const clearTicks = Math.floor(
    (best[1] * 1000 - Number(CEO_LOOKAHEAD_MILLI)) / Number(CEO_STRAIGHT_MILLI_PER_TICK),
  )

  return { prediction, open: best[0], spawn: [sx, sy], clearTicks }
}

/** How far the prediction has travelled from spawn, on either axis. */
function travelled(prediction: CeoPrediction, spawn: [number, number]): number {
  const pose = prediction.pose()
  return Math.abs(pose.xMilli - spawn[0] * 1000) + Math.abs(pose.yMilli - spawn[1] * 1000)
}

describe('predicting CEO movement', () => {
  it('advances every tick a direction is held, and stops when it is released (R2)', () => {
    const { prediction, open, spawn } = atSpawn()

    prediction.hold(open, 1n)
    prediction.advanceTo(5n)
    expect(travelled(prediction, spawn)).toBe(5 * Number(CEO_STRAIGHT_MILLI_PER_TICK))

    const stoppedAt = travelled(prediction, spawn)
    prediction.hold(0, 6n)
    prediction.advanceTo(40n)
    expect(travelled(prediction, spawn)).toBe(stoppedAt)
    expect(prediction.pose().moving).toBe(false)
  })

  it('holds a direction until it is superseded, matching the kernel', () => {
    // One command per *change*, not one per tick. This is the property the kernel had to be
    // fixed to share: reading only the tagged tick moves the CEO a seventh of a tile and stops.
    const { prediction, open, spawn, clearTicks } = atSpawn()
    expect(clearTicks).toBeGreaterThan(5)

    prediction.hold(open, 1n)
    prediction.advanceTo(BigInt(clearTicks))

    expect(travelled(prediction, spawn)).toBe(clearTicks * Number(CEO_STRAIGHT_MILLI_PER_TICK))
  })

  it('does not apply an input before the tick it was tagged for', () => {
    const { prediction, open, spawn } = atSpawn()

    prediction.hold(open, 10n)
    prediction.advanceTo(9n)
    expect(travelled(prediction, spawn)).toBe(0)

    prediction.advanceTo(10n)
    expect(travelled(prediction, spawn)).toBe(Number(CEO_STRAIGHT_MILLI_PER_TICK))
  })

  it('resets rather than walking when the gap is too large to chase', () => {
    // A backgrounded tab or a resync leaves a gap of thousands of ticks. Replaying them would
    // burn a frame to arrive somewhere the next echo corrects anyway, so past the render
    // clock's own slew threshold this is a reset. The prediction stays put and the history is
    // dropped, so no echo is later checked against a tick from before the jump.
    const { prediction, open, spawn } = atSpawn()
    prediction.hold(open, 1n)
    prediction.advanceTo(5n)
    const beforeJump = travelled(prediction, spawn)
    expect(beforeJump).toBeGreaterThan(0)

    prediction.advanceTo(5n + SLEW_THRESHOLD_TICKS + 1n)

    expect(travelled(prediction, spawn)).toBe(beforeJump)
    // History dropped: an echo for a tick before the jump has no second opinion to compare to.
    expect(prediction.reconcile({ xMilli: 0, yMilli: 0, tick: 5n })).toBe(false)
  })

  it('walks the whole gap when it is within the threshold', () => {
    const { prediction, open, spawn, clearTicks } = atSpawn()
    const gap = BigInt(Math.min(clearTicks, Number(SLEW_THRESHOLD_TICKS) - 1))

    prediction.hold(open, 1n)
    prediction.advanceTo(gap)

    expect(travelled(prediction, spawn)).toBe(
      Number(gap) * Number(CEO_STRAIGHT_MILLI_PER_TICK),
    )
  })

  it('uses the diagonal step, so diagonals are not faster than straight lines', () => {
    const floor = floorFixture()
    const grid = buildGrid(floor)

    // Searched across the whole floor rather than assumed at spawn. Requiring an open
    // diagonal *at spawn* is what made the earlier version of this test skip itself silently
    // on the shipped floor — it asserted nothing at all and reported green.
    const diagonals: Array<[number, number, number]> = [
      [INPUT_LEFT | INPUT_UP, -1, -1],
      [INPUT_LEFT | INPUT_DOWN, -1, 1],
      [INPUT_RIGHT | INPUT_UP, 1, -1],
      [INPUT_RIGHT | INPUT_DOWN, 1, 1],
    ]

    let found: { mask: number; x: number; y: number } | null = null
    for (let y = 1; y < floor.rows - 1 && found === null; y += 1) {
      for (let x = 1; x < floor.cols - 1 && found === null; x += 1) {
        if (!walkable(grid, x, y)) continue
        for (const [mask, dx, dy] of diagonals) {
          // Both neighbours open as well as the diagonal itself: the kernel tests each axis
          // separately, so a diagonal into a corner moves on one axis only.
          if (
            walkable(grid, x + dx, y + dy) &&
            walkable(grid, x + dx, y) &&
            walkable(grid, x, y + dy) &&
            walkable(grid, x + dx * 2, y + dy * 2)
          ) {
            found = { mask, x, y }
            break
          }
        }
      }
    }

    expect(found, 'the floor has no open diagonal anywhere').not.toBeNull()
    if (found === null) return

    const prediction = new CeoPrediction()
    prediction.useFloor(floor)
    prediction.seed(
      { xMilli: found.x * 1000, yMilli: found.y * 1000, facing: 'down' },
      0n,
    )
    prediction.hold(found.mask, 1n)
    prediction.advanceTo(1n)

    const pose = prediction.pose()
    expect(Math.abs(pose.xMilli - found.x * 1000)).toBe(Number(CEO_DIAGONAL_MILLI_PER_TICK))
    expect(Math.abs(pose.yMilli - found.y * 1000)).toBe(Number(CEO_DIAGONAL_MILLI_PER_TICK))
    expect(CEO_DIAGONAL_MILLI_PER_TICK).toBeLessThan(CEO_STRAIGHT_MILLI_PER_TICK)
  })

  it('will not walk the CEO through a wall, so it cannot diverge on collision', () => {
    const floor = floorFixture()
    const grid = buildGrid(floor)
    const prediction = new CeoPrediction()
    prediction.useFloor(floor)
    prediction.seed(
      { xMilli: floor.spawn[0] * 1000, yMilli: floor.spawn[1] * 1000, facing: 'down' },
      0n,
    )

    // Every direction in turn, long enough to reach a wall in each.
    for (const [index, mask] of [INPUT_LEFT, INPUT_UP, INPUT_RIGHT, INPUT_DOWN].entries()) {
      prediction.hold(mask, BigInt(index * 100 + 1))
      prediction.advanceTo(BigInt(index * 100 + 100))
    }

    const pose = prediction.pose()
    const tile = (milli: number) => Math.floor((milli + 500) / 1000)
    expect(walkable(grid, tile(pose.xMilli), tile(pose.yMilli))).toBe(true)
  })

  it('animates the walk cycle while held and rests when released', () => {
    const { prediction, open } = atSpawn()

    prediction.hold(open, 1n)
    prediction.advanceTo(4n)
    expect(prediction.pose().moving).toBe(true)

    // Frame 0 is standing still; a moving actor cycles through the stride.
    const frames = new Set(
      [0, 7, 14, 21].map((ticks) =>
        walkFrame({ ...prediction.pose(), id: CEO_ID, animTicks: ticks }),
      ),
    )
    expect(frames.size).toBeGreaterThan(1)

    prediction.hold(0, 5n)
    prediction.advanceTo(6n)
    expect(walkFrame({ ...prediction.pose(), id: CEO_ID, animTicks: 7 })).toBe(0)
  })

  it('faces the direction of travel', () => {
    const floor = floorFixture()
    const grid = buildGrid(floor)
    const [sx, sy] = floor.spawn

    for (const [mask, dx, dy, facing] of [
      [INPUT_LEFT, -1, 0, 'left'],
      [INPUT_RIGHT, 1, 0, 'right'],
      [INPUT_UP, 0, -1, 'up'],
      [INPUT_DOWN, 0, 1, 'down'],
    ] as const) {
      if (!walkable(grid, sx + dx, sy + dy)) continue
      const prediction = new CeoPrediction()
      prediction.useFloor(floor)
      prediction.seed({ xMilli: sx * 1000, yMilli: sy * 1000, facing: 'down' }, 0n)
      prediction.hold(mask, 1n)
      prediction.advanceTo(1n)
      expect(prediction.pose().facing).toBe(facing)
    }
  })
})

// =========================================================================
// AE8: reconciliation against the kernel's echo
// =========================================================================

describe('reconciling against the position echo', () => {
  it('leaves an agreeing prediction alone', () => {
    const { prediction, open } = atSpawn()
    prediction.hold(open, 1n)
    prediction.advanceTo(10n)

    const before = prediction.pose()
    const diverged = prediction.reconcile({
      xMilli: before.xMilli,
      yMilli: before.yMilli,
      tick: 10n,
    })

    expect(diverged).toBe(false)
    expect(prediction.pose()).toEqual(before)
  })

  it('compares against the prediction for the echoed tick, not the position now (AE8)', () => {
    const { prediction, open } = atSpawn()
    prediction.hold(open, 1n)
    prediction.advanceTo(5n)
    const atFive = prediction.pose()

    // The client walks on while the echo is in flight. Comparing the tick-5 echo against the
    // tick-20 position would report a divergence on every step the CEO ever takes.
    prediction.advanceTo(20n)
    expect(prediction.pose().xMilli + prediction.pose().yMilli).not.toBe(
      atFive.xMilli + atFive.yMilli,
    )

    const diverged = prediction.reconcile({
      xMilli: atFive.xMilli,
      yMilli: atFive.yMilli,
      tick: 5n,
    })

    expect(diverged).toBe(false)
  })

  it('snaps to the kernel and re-walks the ticks since, so a snap eats no input (AE8)', () => {
    const { prediction, open, spawn, clearTicks } = atSpawn()
    expect(clearTicks).toBeGreaterThan(10)
    const step = Number(CEO_STRAIGHT_MILLI_PER_TICK)

    prediction.hold(open, 1n)
    prediction.advanceTo(5n)
    const atFive = prediction.pose()
    prediction.advanceTo(10n)
    expect(travelled(prediction, spawn)).toBe(10 * step)

    // The kernel says tick 5 was one step *behind* where this client put it. Behind rather
    // than ahead so the corrected position is back along the corridor already walked, which
    // keeps the assertion about reconciliation instead of about which tile is a wall.
    const towardsSpawn = (value: number, origin: number) =>
      value === origin ? value : value + (value > origin ? -step : step)

    const diverged = prediction.reconcile({
      xMilli: towardsSpawn(atFive.xMilli, spawn[0] * 1000),
      yMilli: towardsSpawn(atFive.yMilli, spawn[1] * 1000),
      tick: 5n,
    })

    expect(diverged).toBe(true)
    // Snapped to tick 5's corrected position, then ticks 6 through 10 walked again on top of
    // it — nine steps from spawn rather than ten, with none of those five ticks discarded.
    expect(travelled(prediction, spawn)).toBe(9 * step)
  })

  it('reports nothing when it has no prediction for the echoed tick', () => {
    const { prediction } = atSpawn()

    // Before any advance there is no second opinion, so there is no disagreement to report.
    expect(prediction.reconcile({ xMilli: 999_999, yMilli: 999_999, tick: 3n })).toBe(false)
  })

  it('drops its history on a reseed, so an echo never compares two timelines', () => {
    const { prediction, open } = atSpawn()
    prediction.hold(open, 1n)
    prediction.advanceTo(10n)

    prediction.seed({ xMilli: 5000, yMilli: 5000, facing: 'up' }, 10n)

    expect(prediction.reconcile({ xMilli: 123, yMilli: 456, tick: 10n })).toBe(false)
    expect(prediction.pose()).toMatchObject({ xMilli: 5000, yMilli: 5000, facing: 'up' })
  })
})

// =========================================================================
// R4, R23: the clock rate is what movement costs
// =========================================================================

describe('movement against the clock', () => {
  /** Ticks the render clock covers in `ms` of wall time at `rate`. */
  function ticksIn(ms: number, rate: number): bigint {
    const clock = new RenderClock(0n, rate)
    // A far-off authority, so the extrapolation cap plays no part in this measurement.
    clock.onAuthoritativeTick(0n)
    clock.advance(ms)
    return clock.tick
  }

  it('covers three times the ground per wall-second at x3 (R4)', () => {
    const atOne = ticksIn(1000, 1)
    const atThree = ticksIn(1000, 3)

    // Capped by the extrapolation budget rather than unbounded, but the ratio is the claim:
    // sim-time is what movement is priced in, so a faster clock buys more walking per second.
    expect(atThree).toBeGreaterThan(atOne)

    // Three times the ticks is three times the ground, which is what "a faster clock buys
    // more walking" means once movement is priced in sim-time rather than wall time.
    const { prediction: slow, open, spawn, clearTicks } = atSpawn()
    const oneSecond = Math.floor(clearTicks / 3)
    expect(oneSecond).toBeGreaterThan(0)

    slow.hold(open, 1n)
    slow.advanceTo(BigInt(oneSecond))

    const { prediction: fast } = atSpawn()
    fast.hold(open, 1n)
    fast.advanceTo(BigInt(oneSecond * 3))

    expect(travelled(fast, spawn)).toBe(3 * travelled(slow, spawn))
  })

  it('stops dead at rate zero, because the clock it walks along is clamped (R23)', () => {
    const clock = new RenderClock(100n, 0)
    clock.onRateChange(0, 100n)
    clock.advance(5000)
    expect(clock.tick).toBe(100n)

    const { prediction, open, spawn } = atSpawn()
    prediction.advanceTo(100n)
    prediction.hold(open, 101n)

    // The clock did not move, so neither does the prediction that walks along it.
    prediction.advanceTo(clock.tick)
    expect(travelled(prediction, spawn)).toBe(0)
  })
})

// =========================================================================
// R23 / AE10: pause is stated, and a held key survives it
// =========================================================================

describe('pausing and resuming', () => {
  it('re-states a held direction on resume, which no keydown would (AE10)', () => {
    expect(shouldRestateHeldInput(0, 1, INPUT_LEFT)).toBe(true)
    expect(shouldRestateHeldInput(0, 3, INPUT_UP | INPUT_LEFT)).toBe(true)
  })

  it('re-states nothing when no key is held', () => {
    expect(shouldRestateHeldInput(0, 1, 0)).toBe(false)
  })

  it('re-states nothing when the run was not paused', () => {
    expect(shouldRestateHeldInput(1, 3, INPUT_LEFT)).toBe(false)
    expect(shouldRestateHeldInput(3, 1, INPUT_LEFT)).toBe(false)
  })

  it('re-states nothing on the way into a pause', () => {
    expect(shouldRestateHeldInput(1, 0, INPUT_LEFT)).toBe(false)
  })
})

// =========================================================================
// Tagging an input for a tick the kernel has not run yet
// =========================================================================

describe('the input lead', () => {
  it('grows with the rate, so the wall-time margin holds at every speed', () => {
    // The margin has to cover a round trip plus the kernel's batching, both measured in wall
    // time. A fixed tick lead shrinks in real terms exactly as the clock speeds up.
    expect(inputLeadTicks(3)).toBeGreaterThan(inputLeadTicks(1))
    // Proportional up to the floor division that keeps ticks whole, not exactly triple.
    expect(inputLeadTicks(3)).toBeGreaterThanOrEqual(inputLeadTicks(1) * 3n)
    expect(inputLeadTicks(3)).toBeLessThan(inputLeadTicks(1) * 3n + 3n)
  })

  it('never tags an input for the very next tick', () => {
    expect(inputLeadTicks(0)).toBeGreaterThanOrEqual(MIN_INPUT_LEAD_TICKS)
    expect(inputLeadTicks(1)).toBeGreaterThanOrEqual(MIN_INPUT_LEAD_TICKS)
  })
})

describe('the held-direction bitmask', () => {
  it('reads WASD and the arrows the same way', () => {
    expect(bitmaskFor(['w'])).toBe(bitmaskFor(['ArrowUp']))
    expect(bitmaskFor(['a', 's'])).toBe(bitmaskFor(['ArrowLeft', 'ArrowDown']))
  })

  it('ignores keys that are not directions', () => {
    expect(bitmaskFor(['q', 'Shift'])).toBe(0)
  })
})
