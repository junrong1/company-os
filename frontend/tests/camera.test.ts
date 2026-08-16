import { describe, expect, it } from 'vitest'

import { Camera, DEAD_ZONE, follow } from '../src/render/camera'
import { TILE } from '../src/render/floor'

/**
 * The viewport, and the three things that go wrong without each of its rules.
 *
 * Every number here is in logical pixels before the zoom, which is the space the camera works
 * in — the zoom is an integer multiplier applied after, and mixing the two is how a
 * fractional translation gets in.
 */

/** The default office: 31×18 tiles at 32 pixels each — 992×576. */
const WORLD = { width: 31 * TILE, height: 18 * TILE }

/**
 * A 1280×800 laptop, after the chrome has taken its share.
 *
 * Worth reading the numbers: the office is 992 wide and this stage is 1264, so the whole
 * floor fits *horizontally* and the camera centres it. What does not fit is the height — 576
 * against 450 — which is the axis the camera actually follows on the most common display
 * there is, and the reason the office needed a camera at all.
 */
const LAPTOP = { width: 1264, height: 450 }

/** A stage narrower than the office, so both axes have to follow. */
const NARROW = { width: 700, height: 400 }

describe('following', () => {
  it('does not move while the target stays near the middle', () => {
    // Following every pixel makes the room swim under a walking character, which reads as the
    // floor being unstable rather than as the camera being attentive.
    const start = { x: 120, y: 60 }
    const centre = { x: start.x + NARROW.width / 2, y: start.y + NARROW.height / 2 }

    const nudge = NARROW.width * DEAD_ZONE * 0.4
    const moved = follow(start, { x: centre.x + nudge, y: centre.y }, NARROW, WORLD)

    expect(moved.x).toBe(start.x)
    expect(moved.y).toBe(start.y)
  })

  it('moves on the first step outside the dead zone', () => {
    const start = { x: 120, y: 60 }
    const centre = { x: start.x + NARROW.width / 2, y: start.y + NARROW.height / 2 }

    const past = NARROW.width * DEAD_ZONE
    const moved = follow(start, { x: centre.x + past, y: centre.y }, NARROW, WORLD)

    expect(moved.x).toBeGreaterThan(start.x)
  })

  it('centres the axis the floor already fits on', () => {
    // On a laptop the office is narrower than the stage, so there is nothing to follow
    // horizontally — and a camera that followed anyway would slide a floor with room to
    // spare from side to side as the player walked.
    const left = follow({ x: 0, y: 0 }, { x: 10, y: 300 }, LAPTOP, WORLD)
    const right = follow(left, { x: WORLD.width - 10, y: 300 }, LAPTOP, WORLD)

    expect(right.x).toBe(left.x)
    expect(left.x).toBe(Math.round((WORLD.width - LAPTOP.width) / 2))
  })

  it('never shows a coordinate outside the building', () => {
    // Including both corners of a diagonal walk into one, which is where a clamp applied per
    // axis in the wrong order lets the office float.
    for (const target of [
      { x: 0, y: 0 },
      { x: WORLD.width, y: 0 },
      { x: 0, y: WORLD.height },
      { x: WORLD.width, y: WORLD.height },
      { x: -5000, y: -5000 },
      { x: 99_999, y: 99_999 },
    ]) {
      const view = follow({ x: 120, y: 60 }, target, NARROW, WORLD)
      expect(view.x, `${target.x},${target.y}`).toBeGreaterThanOrEqual(0)
      expect(view.y, `${target.x},${target.y}`).toBeGreaterThanOrEqual(0)
      expect(view.x + NARROW.width).toBeLessThanOrEqual(WORLD.width)
      expect(view.y + NARROW.height).toBeLessThanOrEqual(WORLD.height)
    }
  })

  it('centres and holds when the whole floor fits', () => {
    // The large-display case. Following inside a viewport that already shows everything would
    // slide the room around for no reason at all.
    const roomy = { width: WORLD.width + 400, height: WORLD.height + 200 }
    const first = follow({ x: 0, y: 0 }, { x: 10, y: 10 }, roomy, WORLD)
    const second = follow(first, { x: WORLD.width - 10, y: WORLD.height - 10 }, roomy, WORLD)

    expect(second).toEqual(first)
    expect(first.x).toBe(Math.round((WORLD.width - roomy.width) / 2))
  })

  it('translates by whole pixels over a long diagonal walk', () => {
    // A fractional offset resamples every pixel in the office and it stops being pixel art —
    // the same reason `chooseZoom` refuses a fractional zoom.
    let view = { x: 0, y: 0 }
    for (let step = 0; step < 1000; step += 1) {
      const target = { x: 40 + step * 0.7, y: 30 + step * 0.37 }
      view = follow(view, target, NARROW, WORLD)
      expect(Number.isInteger(view.x), `x at ${step}`).toBe(true)
      expect(Number.isInteger(view.y), `y at ${step}`).toBe(true)
    }
  })

  it('keeps the target inside the viewport across the whole floor', () => {
    // AE4, as a sweep: no character becomes obscured at any supported width.
    for (const stage of [
      { width: 700, height: 400 },
      { width: 1264, height: 450 },
      { width: 1424, height: 620 },
      { width: 1904, height: 840 },
    ]) {
      const camera = new Camera()
      camera.centreOn({ x: 0, y: 0 }, stage, WORLD)

      // Walked across the whole floor and back down it, because a clamp that is right on one
      // axis and wrong on the other only shows up on a path that uses both.
      for (let x = 0; x <= WORLD.width; x += TILE) {
        for (const y of [0, WORLD.height / 2, WORLD.height]) {
          const view = camera.track({ x, y }, stage, WORLD)
          // The viewport is the stage, in world coordinates. When the floor is narrower than
          // the stage the offset goes negative — that is what centring a small floor in a
          // wide canvas means — so the span is the stage's own size, not the floor's.
          const visible =
            x >= view.x &&
            x <= view.x + stage.width &&
            y >= view.y &&
            y <= view.y + stage.height
          expect(visible, `${stage.width}×${stage.height} at ${x},${y}`).toBe(true)
        }
      }
    }
  })
})

describe('the camera', () => {
  it('opens centred rather than easing in from the origin', () => {
    // Following in from 0,0 would open every session with the camera sliding across the
    // office, which reads as a loading animation nobody asked for.
    const camera = new Camera()
    const middle = { x: WORLD.width / 2, y: WORLD.height / 2 }
    const view = camera.centreOn(middle, NARROW, WORLD)

    expect(view.x).toBe(Math.round(middle.x - NARROW.width / 2))
  })

  it('forgets where it was looking on dispose', () => {
    const camera = new Camera()
    camera.centreOn({ x: 700, y: 300 }, NARROW, WORLD)
    expect(camera.x).toBeGreaterThan(0)

    camera.reset()
    expect(camera.x).toBe(0)
    expect(camera.y).toBe(0)
  })
})
