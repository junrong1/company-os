import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  MAX_EXTRAPOLATION_TICKS,
  RenderClock,
  SLEW_THRESHOLD_TICKS,
  TICKS_PER_WALL_MS_DENOMINATOR,
  TICKS_PER_WALL_MS_NUMERATOR,
  positionDiverged,
} from '../src/render/clock'
import { TICKS_PER_SIM_HOUR } from '../src/render/interpolate'
import { RESERVED_BEAM, relativeLuminance } from '../src/design/tokens'

/** Wall milliseconds that advance the clock by at least `ticks` at rate 1. */
function wallMsFor(ticks: bigint): number {
  return Number((ticks * TICKS_PER_WALL_MS_DENOMINATOR) / TICKS_PER_WALL_MS_NUMERATOR) + 1
}
import { Renderer, resetSheetCounter, sheetsGenerated } from '../src/render/index'
import { ART, PROPS, OUTLINE_GLYPHS, PROP_PALETTE } from '../src/render/sprites'
import {
  DIRS,
  FRAMES,
  SPRITE_HEIGHT,
  SPRITE_WIDTH,
  BEAM_COLOUR,
  STRIDE_MILLI,
  StrideTracker,
  WALK_CYCLE,
  characterSheet,
  depthSort,
  drawWaitingBeam,
} from '../src/render/actors'
import {
  type FloorData,
  CORRIDOR as CORRIDOR_TILE,
  FLOOR as FLOOR_TILE,
  SOLID as SOLID_TILE,
  TILE,
  WALL as WALL_TILE,
  buildGrid,
  chooseZoom,
  speck,
} from '../src/render/floor'

/**
 * The renderer's mechanical properties.
 *
 * Visual parity is judged by eye against side-by-side screenshots — no assertion here claims it.
 * What is asserted is everything that *can* be: the lifecycle the prototype lacks, the render
 * clock's four specified behaviours, and the sprite grids, which are re-validated here with the
 * same rules the prototype's own `test/sprites.js` applies (R33).
 */

// =========================================================================
// Lifecycle: the thing the prototype does not have
// =========================================================================

interface FakeFrames {
  request: (callback: (time: number) => void) => number
  cancel: (handle: number) => void
  pending: () => number
  runOne: () => void
  now: () => number
  advance: (ms: number) => void
}

function fakeFrames(): FakeFrames {
  const queued = new Map<number, (time: number) => void>()
  let nextHandle = 1
  let clock = 0

  return {
    request(callback) {
      const handle = nextHandle++
      queued.set(handle, callback)
      return handle
    },
    cancel(handle) {
      queued.delete(handle)
    },
    pending() {
      return queued.size
    },
    runOne() {
      const [handle, callback] = [...queued.entries()][0] ?? []
      if (handle === undefined || callback === undefined) return
      queued.delete(handle)
      callback(clock)
    },
    now() {
      return clock
    },
    advance(ms) {
      clock += ms
    },
  }
}

function renderer(frames: FakeFrames): Renderer {
  const canvas = document.createElement('canvas')
  return new Renderer({
    canvas,
    requestFrame: frames.request,
    cancelFrame: frames.cancel,
    now: frames.now,
  })
}

beforeEach(() => {
  resetSheetCounter()
})

describe('the renderer lifecycle', () => {
  it('leaves no pending frame handle after start then stop', () => {
    const frames = fakeFrames()
    const instance = renderer(frames)

    instance.start()
    expect(frames.pending()).toBe(1)

    instance.stop()
    expect(frames.pending()).toBe(0)
    expect(instance.running).toBe(false)
  })

  it('runs exactly one loop through a strict-mode setup-cleanup-setup cycle', () => {
    /*
     * The cycle React runs in development. A module-scope loop with no cancel path — which is what
     * the prototype has — would leave two loops drawing the same canvas at different phases.
     */
    const frames = fakeFrames()
    const instance = renderer(frames)

    instance.start() // setup
    instance.stop() // cleanup
    instance.start() // setup again

    expect(frames.pending()).toBe(1)
  })

  it('is idempotent on start, which is the direction strict mode exercises', () => {
    const frames = fakeFrames()
    const instance = renderer(frames)

    instance.start()
    instance.start()
    instance.start()

    expect(frames.pending()).toBe(1)
  })

  it('is idempotent on stop', () => {
    const frames = fakeFrames()
    const instance = renderer(frames)
    instance.start()

    instance.stop()
    instance.stop()

    expect(frames.pending()).toBe(0)
  })

  it('does not draw a frame that was scheduled before a stop', () => {
    const frames = fakeFrames()
    const instance = renderer(frames)
    instance.start()

    instance.stop()
    frames.runOne() // the already-queued callback fires after the stop

    expect(instance.frameCount).toBe(0)
  })

  it('advances the clock once per frame', () => {
    const frames = fakeFrames()
    const instance = renderer(frames)
    instance.start()

    frames.advance(100)
    frames.runOne()

    expect(instance.frameCount).toBe(1)
    expect(instance.clock.tick).toBeGreaterThan(0n)
  })

  it('tears down the loop, the listeners and the sprite cache on dispose', () => {
    /* Vite gives non-component modules no automatic disposal, so every save would leak a set. */
    const frames = fakeFrames()
    const instance = renderer(frames)
    const target = document.createElement('div')
    const handler = vi.fn()

    instance.addListener(target, 'click', handler)
    instance.start()
    expect(instance.listenerCount).toBe(1)
    expect(instance.sheetsBuilt).toBe(true)

    instance.dispose()

    expect(frames.pending()).toBe(0)
    expect(instance.listenerCount).toBe(0)
    expect(instance.sheetsBuilt).toBe(false)

    target.dispatchEvent(new Event('click'))
    expect(handler).not.toHaveBeenCalled()
  })

  it('builds one sprite sheet set per renderer, not per start', () => {
    const frames = fakeFrames()
    const instance = renderer(frames)

    instance.start()
    instance.stop()
    instance.start()

    expect(sheetsGenerated()).toBe(1)
  })
})

// =========================================================================
// The render clock
// =========================================================================

describe('the render clock', () => {
  it('advances at the base rate', () => {
    const clock = new RenderClock(0n, 1)
    clock.onAuthoritativeTick(1000n)

    clock.advance(1000) // one wall second

    // 36 ticks per wall second at the base rate, plus slew toward the authority.
    expect(clock.tick).toBeGreaterThanOrEqual(36n)
  })

  it('advances three times faster at rate three, without leaving the path', () => {
    const single = new RenderClock(0n, 1)
    const triple = new RenderClock(0n, 3)

    // A gap inside the slew threshold, and wide enough that neither clock reaches the
    // extrapolation ceiling. Set it far ahead instead and both clocks set *directly* to it and
    // then clamp at authority+1, which hides the rate difference entirely — which is what the
    // first version of this test did.
    single.onAuthoritativeTick(50n)
    triple.onAuthoritativeTick(50n)

    single.advance(100)
    triple.advance(100)

    // The rate multiplies elapsed time; it does not change what a tick means, which is why
    // actors stay on their paths rather than being displaced.
    expect(triple.tick).toBeGreaterThan(single.tick)
  })

  it('clamps to the pause tick exactly when the rate goes to zero', () => {
    /*
     * Clamped, not merely frozen. A clock left wherever it happened to be could sit slightly ahead
     * of the pause tick, and actors would snap *backwards* on resume — which looks like the
     * simulation lost work.
     */
    const clock = new RenderClock(0n, 1)
    clock.onAuthoritativeTick(500n)
    clock.advance(1000)

    clock.onRateChange(0, 480n)

    expect(clock.tick).toBe(480n)

    clock.advance(5000)
    expect(clock.tick).toBe(480n)
  })

  it('resumes from the pause tick rather than from where it drifted', () => {
    const clock = new RenderClock(0n, 1)
    clock.onRateChange(0, 300n)
    clock.advance(1000)
    expect(clock.tick).toBe(300n)

    clock.onRateChange(1, 300n)
    clock.onAuthoritativeTick(320n)
    clock.advance(100)

    expect(clock.tick).toBeGreaterThanOrEqual(300n)
  })

  it('slews toward the authority instead of snapping', () => {
    const clock = new RenderClock(0n, 1)

    // A gap inside the threshold: chased, not jumped.
    clock.onAuthoritativeTick(60n)
    expect(clock.tick).toBe(0n)

    clock.advance(1)
    expect(clock.tick).toBeGreaterThan(0n)
    expect(clock.tick).toBeLessThan(60n)
  })

  it('sets directly rather than slewing past the threshold', () => {
    /* Slewing a thirty-day gap would render a provably wrong world for minutes. */
    const clock = new RenderClock(0n, 1)
    const far = SLEW_THRESHOLD_TICKS + 10_000n

    clock.onAuthoritativeTick(far)

    expect(clock.tick).toBe(far)
  })

  it('extrapolates at most its budget past the authority, then freezes and says so', () => {
    const clock = new RenderClock(0n, 1)
    clock.onAuthoritativeTick(100n)
    clock.advance(10)
    expect(clock.stalled).toBe(false)

    // No further authoritative ticks: the clock runs out of budget. Derived from the constant
    // rather than a literal duration, so resizing the budget does not turn this into a test
    // that passes by not reaching the cap at all.
    clock.advance(wallMsFor(100n + MAX_EXTRAPOLATION_TICKS + 10n))

    expect(clock.tick).toBe(100n + MAX_EXTRAPOLATION_TICKS)
    expect(clock.stalled).toBe(true)
  })

  it('clears the stall when the authority moves again', () => {
    const clock = new RenderClock(0n, 1)
    clock.onAuthoritativeTick(100n)
    clock.advance(wallMsFor(100n + MAX_EXTRAPOLATION_TICKS + 10n))
    expect(clock.stalled).toBe(true)

    clock.onAuthoritativeTick(100n + MAX_EXTRAPOLATION_TICKS + 40n)

    expect(clock.stalled).toBe(false)
  })

  it('does not stall across one position-echo interval, which is how often the kernel speaks', () => {
    // Events land on about five ticks in twelve hundred, so the echo — once per sim-hour — is
    // the regular signal. A budget under that interval freezes the office between every pair.
    const clock = new RenderClock(0n, 1)
    clock.onAuthoritativeTick(0n)

    clock.advance(wallMsFor(TICKS_PER_SIM_HOUR))

    expect(clock.stalled).toBe(false)
  })

  it('sets directly on a resync', () => {
    const clock = new RenderClock(0n, 1)
    clock.onAuthoritativeTick(50n)

    clock.onResync(999_999n)

    expect(clock.tick).toBe(999_999n)
    expect(clock.stalled).toBe(false)
  })

  it('ignores an out-of-order authoritative tick', () => {
    const clock = new RenderClock(0n, 1)
    clock.onAuthoritativeTick(500n)

    clock.onAuthoritativeTick(400n)

    expect(clock.view().authoritative).toBe(500n)
  })

  it('keeps working above 2^53, where a number-based clock would not', () => {
    const huge = 2n ** 53n + 12_345n
    const clock = new RenderClock(huge, 1)
    clock.onAuthoritativeTick(huge + 10n)

    clock.advance(100)

    expect(clock.tick).toBeGreaterThan(huge)
    // A double could not represent this range distinctly.
    expect(clock.tick).not.toBe(BigInt(Number(clock.tick) + 1))
  })

  it('does not lose fractional time across slow frames', () => {
    /* 36 ticks per 1000ms means most frames carry a remainder; dropping it would run slow. */
    const clock = new RenderClock(0n, 1)
    clock.onAuthoritativeTick(10_000n)

    for (let index = 0; index < 100; index += 1) clock.advance(10)

    // 100 frames x 10ms = 1000ms = 36 ticks of elapsed time, before slew.
    expect(clock.tick).toBeGreaterThanOrEqual(36n)
  })
})

// =========================================================================
// The divergence detector (R33)
// =========================================================================

describe('position divergence', () => {
  it('reports agreement when the prediction matches the echo', () => {
    expect(
      positionDiverged({ xMilli: 3144n, yMilli: 8000n }, { xMilli: 3144n, yMilli: 8000n }),
    ).toBe(false)
  })

  it('detects a deliberately perturbed predictor rather than tolerating it', () => {
    /* Golden vectors prove agreement only for the cases someone thought to vector. */
    expect(
      positionDiverged({ xMilli: 3145n, yMilli: 8000n }, { xMilli: 3144n, yMilli: 8000n }),
    ).toBe(true)
  })

  it('detects divergence on either axis', () => {
    expect(
      positionDiverged({ xMilli: 3144n, yMilli: 8001n }, { xMilli: 3144n, yMilli: 8000n }),
    ).toBe(true)
  })
})

// =========================================================================
// Sprite grids, re-validated (R33)
// =========================================================================

describe('the ported sprite grids', () => {
  it('has every prop 32 rows of 32 columns, in the prop palette', () => {
    for (const [name, rows] of Object.entries(PROPS)) {
      expect(rows.length, `${name} row count`).toBe(32)
      for (const [index, row] of rows.entries()) {
        expect(row.length, `${name} row ${index} width`).toBe(32)
        for (const glyph of row) {
          expect(PROP_PALETTE.has(glyph), `${name} row ${index} glyph ${glyph}`).toBe(true)
        }
      }
    }
  })

  it('is a daylight set with dark outlines, rather than an ink set', () => {
    // Deliberately not "every value clears a luminance floor". Structure is legitimately mid
    // — a chair post and a window frame are 鲸鱼灰 and should be — so a floor high enough to
    // mean anything would forbid the furniture from having any weight at all.
    //
    // What is actually claimed is the shape of the set: the two outline glyphs are the two
    // darkest values in it, and most of the rest is light. An ink-room palette fails the
    // second half immediately, which is what makes this worth asserting.
    const byLuminance = Object.entries(ART).sort(
      ([, a], [, b]) => relativeLuminance(a) - relativeLuminance(b),
    )

    const darkestTwo = new Set(byLuminance.slice(0, 2).map(([glyph]) => glyph))
    expect(darkestTwo).toEqual(OUTLINE_GLYPHS)

    const light = byLuminance.filter(([, colour]) => relativeLuminance(colour) > 0.15)
    expect(light.length).toBeGreaterThanOrEqual(Math.ceil(byLuminance.length * 0.6))
  })

  it('spends the reserved amber on no prop', () => {
    expect(Object.values(ART)).not.toContain(RESERVED_BEAM)
  })

  it('maps every prop glyph except transparent to a colour', () => {
    for (const glyph of PROP_PALETTE) {
      if (glyph === '.') continue
      expect(ART[glyph], `glyph ${glyph}`).toMatch(/^#[0-9a-f]{6}$/i)
    }
  })

  it('carries every grid the office is furnished from', () => {
    // Sixteen props rather than the prototype's ten: R10 asks for artwork, plants and
    // collaboration spaces, and none of those existed. The assertion itself is what catches a
    // grid deleted by accident, which is the only reason to count them.
    expect(Object.keys(PROPS)).toHaveLength(16)

  })
})

// =========================================================================
// The drawing pass
// =========================================================================

/**
 * A recording 2D context.
 *
 * jsdom does not implement `getContext`, and installing the `canvas` package would pull a native
 * dependency into the test run for very little. Recording the calls is also more useful than
 * rendering them: what these tests need to know is the *order* things were drawn in, which pixels
 * would not tell us without an image comparison.
 */
function recordingCanvas(): { canvas: HTMLCanvasElement; calls: string[] } {
  const calls: string[] = []
  const context = {
    canvas: null as unknown as HTMLCanvasElement,
    fillStyle: '',
    globalCompositeOperation: 'source-over',
    imageSmoothingEnabled: true,
    fillRect: () => calls.push('fillRect'),
    clearRect: () => calls.push('clearRect'),
    drawImage: (...args: unknown[]) => calls.push(`drawImage:${args.length}`),
    createRadialGradient: () => ({ addColorStop: () => undefined }),
    createImageData: (width: number, height: number) => {
      calls.push('createImageData')
      return { width, height, data: new Uint8ClampedArray(width * height * 4) }
    },
    putImageData: () => calls.push('putImageData'),
    save: () => calls.push('save'),
    restore: () => calls.push('restore'),
    scale: (x: number) => calls.push(`scale:${x}`),
  }
  const canvas = {
    width: 0,
    height: 0,
    getContext: () => context,
  } as unknown as HTMLCanvasElement
  return { canvas, calls }
}

const FLOOR_FIXTURE: FloorData = {
  cols: 31,
  rows: 18,
  hall: [1, 8, 29, 9],
  spawn: [3, 8],
  rooms: [
    {
      id: 'sales',
      kind: 'office',
      band: 'top',
      box: [9, 1, 15, 6],
      door: [12, 7],
      seat_y: 3,
      slots: [
        [10, 3],
        [12, 3],
      ],
      visit: null,
    },
  ],
  furniture: [
    [10, 4, 'desk', 1],
    [10, 3, 'chair', 0],
  ],
  lamps: [[10, 2]],
  windows: [[2, 0]],
}

describe('the depth sort', () => {
  it('orders by depth', () => {
    const order: string[] = []
    const sorted = depthSort([
      { depth: 30, draw: () => order.push('low') },
      { depth: 10, draw: () => order.push('high') },
      { depth: 20, draw: () => order.push('middle') },
    ])
    for (const item of sorted) item.draw(null as unknown as CanvasRenderingContext2D)

    expect(order).toEqual(['high', 'middle', 'low'])
  })

  it('is stable at equal depth, so nothing flickers between frames', () => {
    /* Two things at the same y must not swap: a swap at 60fps reads as flicker, not as a sort. */
    const order: string[] = []
    const sorted = depthSort([
      { depth: 10, draw: () => order.push('first') },
      { depth: 10, draw: () => order.push('second') },
      { depth: 10, draw: () => order.push('third') },
    ])
    for (const item of sorted) item.draw(null as unknown as CanvasRenderingContext2D)

    expect(order).toEqual(['first', 'second', 'third'])
  })
})

const ACTOR = {
  id: 'stf_cs',
  xMilli: 10_000,
  yMilli: 3_000,
  facing: 'down' as const,
  moving: false,
  animTicks: 0,
}

describe('character sheets', () => {
  it('builds one sheet per person and caches it', () => {
    const cache = new Map()
    const make = () => recordingCanvas().canvas

    const first = characterSheet('stf_cs', 1, cache, make)
    const second = characterSheet('stf_cs', 1, cache, make)

    expect(second).toBe(first)
    expect(cache.size).toBe(1)
  })

  it('sizes a sheet as four facings by four frames', () => {
    const cache = new Map()
    const sheet = characterSheet('dir_sales', 1, cache, () => recordingCanvas().canvas)

    expect(sheet.canvas.width).toBe(SPRITE_WIDTH * FRAMES)
    expect(sheet.canvas.height).toBe(SPRITE_HEIGHT * DIRS.length)
  })

  it('keys the cache on the appearance, not only on the person', () => {
    // A resync into a different run picks a different letter for the same id. An id-only key
    // would serve the previous run's face out of the cache and there would be no symptom
    // beyond a person quietly not changing.
    const cache = new Map()
    const make = () => recordingCanvas().canvas

    characterSheet('dir_sales', 1, cache, make)
    characterSheet('dir_sales', 2, cache, make)
    characterSheet('dir_sales', 3, cache, make)

    expect(cache.size).toBeGreaterThan(1)
  })

  it('writes a sheet in one call rather than one per pixel', () => {
    // 192×256 is 49,152 pixels. The prototype's paint-per-pixel was affordable at 1,920 and
    // is not here, and the only way this regresses is quietly.
    const recording = recordingCanvas()
    characterSheet('stf_cs', 1, new Map(), () => recording.canvas)

    expect(recording.calls.filter((call) => call === 'putImageData')).toHaveLength(1)
    expect(recording.calls.filter((call) => call === 'fillRect')).toHaveLength(0)
  })
})

describe('the stride', () => {
  const walking = { ...ACTOR, moving: true }

  it('stands still on frame zero', () => {
    const stride = new StrideTracker()
    expect(stride.frame({ ...ACTOR, moving: false })).toBe(0)
  })

  it('alternates feet as a person covers ground', () => {
    // Keyed to distance rather than to elapsed ticks, which is what lets the CEO at 144
    // milli-tiles a tick and staff at about 78 share one rule without either skating.
    const stride = new StrideTracker()
    const frames: number[] = []

    let x = walking.xMilli
    for (let step = 0; step < 8; step += 1) {
      stride.advance({ ...walking, xMilli: x })
      frames.push(stride.frame({ ...walking, xMilli: x }))
      x += STRIDE_MILLI
    }

    expect(new Set(frames).size).toBeGreaterThan(1)
    expect(frames).not.toContain(0)
  })

  it('does not spin the legs through a cycle when somebody is teleported', () => {
    // A resync, a fork or a seeded spawn moves a person across the floor in one frame. That
    // is not a walk, and counting it as one runs the whole cycle in a single frame.
    const stride = new StrideTracker()
    stride.advance(walking)
    stride.advance({ ...walking, xMilli: walking.xMilli + 400_000 })

    expect(stride.frame(walking)).toBe(WALK_CYCLE[0])
  })

  it('forgets anyone who has left the floor', () => {
    // A Map keyed by id that nothing removes from is the same leak the renderer's lifecycle
    // exists to prevent, in a different shape.
    const stride = new StrideTracker()
    stride.advance(walking)
    stride.advance({ ...walking, id: 'dir_hr' })
    expect(stride.size).toBe(0)

    stride.advance({ ...walking, xMilli: walking.xMilli + 100 })
    expect(stride.size).toBe(1)

    stride.retain([])
    expect(stride.size).toBe(0)
  })
})

describe('the zoom', () => {
  it('is always an integer', () => {
    for (const [width, height] of [
      [320, 220],
      [800, 600],
      [1600, 900],
      [2560, 1440],
    ]) {
      expect(Number.isInteger(chooseZoom(width, height))).toBe(true)
    }
  })

  it('gives a bigger window a bigger zoom, not only more tiles', () => {
    expect(chooseZoom(1600, 900)).toBeGreaterThan(chooseZoom(700, 500))
  })

  it('steps down when the smallest sensible floor would not fit', () => {
    // 26x16 tiles is the floor's minimum; at zoom 2 that needs 832x512.
    expect(chooseZoom(700, 400)).toBe(1)
  })

  it('sizes the canvas to the floor times the zoom', () => {
    const { canvas } = recordingCanvas()
    const instance = new Renderer({
      canvas,
      floor: FLOOR_FIXTURE,
      makeCanvas: () => recordingCanvas().canvas,
    })

    instance.resize(1600, 900)

    expect(canvas.width).toBe(FLOOR_FIXTURE.cols * TILE * instance.currentZoom)
    expect(canvas.height).toBe(FLOOR_FIXTURE.rows * TILE * instance.currentZoom)
  })
})

describe('the static layer', () => {
  it('is baked once, and dropped by dispose', () => {
    const frames = fakeFrames()
    const instance = new Renderer({
      canvas: recordingCanvas().canvas,
      floor: FLOOR_FIXTURE,
      makeCanvas: () => recordingCanvas().canvas,
      requestFrame: frames.request,
      cancelFrame: frames.cancel,
      now: frames.now,
    })

    instance.start()
    expect(instance.staticLayerBuilt).toBe(true)

    instance.dispose()
    expect(instance.staticLayerBuilt).toBe(false)
  })

  it('reconstructs walkability from the recorded geometry', () => {
    const grid = buildGrid(FLOOR_FIXTURE)

    expect(grid[3][12]).toBe(FLOOR_TILE) // inside the sales room
    expect(grid[8][3]).toBe(CORRIDOR_TILE) // the hall
    expect(grid[4][10]).toBe(SOLID_TILE) // the desk
    expect(grid[0][0]).toBe(WALL_TILE) // outer wall
  })

  it('speckles deterministically, so a resize does not reshuffle the carpet', () => {
    expect(speck(41, 17)).toBe(speck(41, 17))
    expect(speck(41, 17)).not.toBe(speck(42, 17))
  })
})

describe('a frame', () => {
  it('draws the floor before anything that sorts against it', () => {
    const { canvas, calls } = recordingCanvas()
    const frames = fakeFrames()
    const instance = new Renderer({
      canvas,
      floor: FLOOR_FIXTURE,
      actors: () => [ACTOR],
      makeCanvas: () => recordingCanvas().canvas,
      requestFrame: frames.request,
      cancelFrame: frames.cancel,
      now: frames.now,
    })

    instance.start()
    frames.advance(16)
    frames.runOne()

    expect(calls).toContain('clearRect')
    expect(calls.filter((call) => call.startsWith('drawImage')).length).toBeGreaterThan(1)
    // Composed at 1x and scaled by an integer factor, so the art is never resampled.
    expect(calls).toContain('scale:1')
    expect(calls.indexOf('save')).toBeLessThan(calls.indexOf('restore'))
  })

  it('draws a person in front of the desk below them and behind their own', () => {
    /* The reason props are not baked into the floor: they have to sort against people. */
    const order: string[] = []
    const sorted = depthSort([
      { depth: 4 * TILE, draw: () => order.push('desk-below') },
      { depth: 3 * TILE, draw: () => order.push('person') },
      { depth: 3 * TILE - 1, draw: () => order.push('desk-above') },
    ])
    for (const item of sorted) item.draw(null as unknown as CanvasRenderingContext2D)

    expect(order).toEqual(['desk-above', 'person', 'desk-below'])
  })

  it('caches one character sheet per actor drawn, not one per frame', () => {
    const frames = fakeFrames()
    const instance = new Renderer({
      canvas: recordingCanvas().canvas,
      floor: FLOOR_FIXTURE,
      actors: () => [ACTOR, { ...ACTOR, id: 'dir_sales' }],
      makeCanvas: () => recordingCanvas().canvas,
      requestFrame: frames.request,
      cancelFrame: frames.cancel,
      now: frames.now,
    })

    instance.start()
    for (let index = 0; index < 5; index += 1) {
      frames.advance(16)
      frames.runOne()
    }

    expect(instance.cachedSheets).toBe(2)
  })

  it('draws the reserved amber only for someone waiting on a decision', () => {
    /* Amber means one thing across the whole product, and this is the only place that spends
     * it. Asserted against the token module rather than against a literal, because for a
     * while there were two of them — the canvas drew `#f0a92b` while the chrome drew
     * `#f2c46b`, which is two ambers for one meaning in a product whose strongest claim is
     * that exactly one signal pulls the eye. Pinning the hex here is what let them drift. */
    expect(BEAM_COLOUR).toBe(RESERVED_BEAM)

    const withBeam = recordingCanvas()
    drawWaitingBeam(withBeam.canvas.getContext('2d')!, { ...ACTOR, waiting: true })
    expect(withBeam.calls.filter((call) => call === 'fillRect').length).toBeGreaterThan(0)
  })
})
