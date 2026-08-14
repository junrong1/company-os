import { afterEach, describe, expect, it } from 'vitest'

import { CEO_ID, PALETTE_OVERRIDE, personPalette } from '../src/render/palettes'
import { DIRS, FRAMES, SPRITE_HEIGHT, SPRITE_WIDTH, characterSheet, depthSort } from '../src/render/actors'
import { CHARACTER_PALETTE } from '../src/render/sprites'
import { TILE } from '../src/render/floor'
import { actorsFromStore } from '../src/ui/stage'
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
