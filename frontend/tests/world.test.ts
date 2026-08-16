import { describe, expect, it } from 'vitest'

import {
  CORRIDOR,
  FLOOR,
  type FloorData,
  PROP_KEYS,
  PROP_SOURCE,
  SOLID,
  TILE,
  WALL,
  buildGrid,
  buildPropAtlas,
  buildStatic,
  chooseZoom,
  speck,
  walkable,
} from '../src/render/floor'
import { drawActor } from '../src/render/actors'

/**
 * The office's geometry, pinned.
 *
 * Written before `TILE` moved from 16 to 32 and unchanged by that move, which is the whole
 * point of it. The art resolution and the geometry are different things that happened to be
 * the same number: `TILE` says how many logical pixels one floor tile is *drawn* as, while
 * `buildGrid` and `walkable` work in tile *units* the kernel also uses. If doubling the first
 * changed the second, the client and the kernel would disagree about where a wall is, and the
 * only symptom would be a CEO who walks through one.
 *
 * So every assertion here is expressed in tiles, or as a formula over `TILE` rather than a
 * literal. A test that said `expect(canvas.width).toBe(496)` would have had to be edited to
 * make the change pass, and a characterisation test you edit to make the change pass has
 * characterised nothing.
 */

const FIXTURE: FloorData = {
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
    {
      id: 'accounting',
      kind: 'office',
      band: 'bottom',
      box: [9, 11, 15, 16],
      door: [12, 10],
      seat_y: 13,
      slots: [
        [10, 13],
        [12, 13],
      ],
      visit: null,
    },
  ],
  furniture: [
    [10, 4, 'desk', 1],
    [10, 3, 'chair', 0],
    [10, 14, 'desk', 1],
  ],
  lamps: [[10, 2]],
  windows: [
    [2, 0],
    [7, 0],
  ],
}

/** The whole grid as one string, so a single moved cell fails loudly. */
function signature(grid: number[][]): string {
  return grid.map((row) => row.join('')).join('|')
}

/** jsdom has no canvas, and these assertions are about sizes rather than pixels. */
function fakeCanvas(): HTMLCanvasElement {
  return { width: 0, height: 0, getContext: () => null } as unknown as HTMLCanvasElement
}

/** Records where a sprite was drawn, which is the only thing the anchor test asks about. */
class AnchorRecorder {
  fillStyle = ''
  readonly images: { dx: number; dy: number; dw: number; dh: number }[] = []

  fillRect(): void {}

  drawImage(
    _source: unknown,
    _sx: number,
    _sy: number,
    _sw: number,
    _sh: number,
    dx: number,
    dy: number,
    dw: number,
    dh: number,
  ): void {
    this.images.push({ dx, dy, dw, dh })
  }
}

const ACTOR_AT_TILE_3_4 = {
  id: 'stf_cs',
  xMilli: 3_000,
  yMilli: 4_000,
  facing: 'down' as const,
  moving: false,
  animTicks: 0,
}

function counts(grid: number[][]): Record<number, number> {
  const tally: Record<number, number> = { [WALL]: 0, [FLOOR]: 0, [SOLID]: 0, [CORRIDOR]: 0 }
  for (const row of grid) for (const cell of row) tally[cell] += 1
  return tally
}

describe('the walkability grid', () => {
  const grid = buildGrid(FIXTURE)

  it('is the size the floor says it is, in tiles', () => {
    expect(grid).toHaveLength(FIXTURE.rows)
    for (const row of grid) expect(row).toHaveLength(FIXTURE.cols)
  })

  it('fills each room, its door, the hall, and marks solid furniture', () => {
    // Two rooms of 7×6, minus the two tiles a solid desk claims, plus two door tiles.
    const tally = counts(grid)
    expect(tally[FLOOR]).toBe(7 * 6 * 2 - 2 + 2)
    expect(tally[CORRIDOR]).toBe(29 * 2)
    expect(tally[SOLID]).toBe(2)
    expect(tally[WALL]).toBe(31 * 18 - tally[FLOOR] - tally[CORRIDOR] - tally[SOLID])
  })

  it('places every kind of cell where the floor put it', () => {
    expect(grid[3][10]).toBe(FLOOR) // a chair is not solid
    expect(grid[4][10]).toBe(SOLID) // a desk is
    expect(grid[7][12]).toBe(FLOOR) // the door
    expect(grid[8][1]).toBe(CORRIDOR)
    expect(grid[0][0]).toBe(WALL)
  })

  it('has not moved a single cell', () => {
    // The characterisation itself. Counts and spot checks would let a whole room shift by one
    // column and still pass; this would not.
    expect(signature(grid)).toMatchSnapshot()
  })
})

describe('walkability', () => {
  const grid = buildGrid(FIXTURE)

  it('answers identically for every cell of the floor', () => {
    const answers: string[] = []
    for (let y = 0; y < FIXTURE.rows; y += 1) {
      let row = ''
      for (let x = 0; x < FIXTURE.cols; x += 1) row += walkable(grid, x, y) ? '1' : '0'
      answers.push(row)
    }
    expect(answers.join('|')).toMatchSnapshot()
  })

  it('refuses out of bounds rather than throwing', () => {
    // The lookahead probe deliberately asks about tiles past the edge.
    expect(walkable(grid, -1, 5)).toBe(false)
    expect(walkable(grid, 5, -1)).toBe(false)
    expect(walkable(grid, FIXTURE.cols, 5)).toBe(false)
    expect(walkable(grid, 5, FIXTURE.rows)).toBe(false)
    expect(walkable(grid, 9999, 9999)).toBe(false)
  })

  it('lets a person stand on a floor or a corridor and nowhere else', () => {
    expect(walkable(grid, 10, 3)).toBe(true) // room floor
    expect(walkable(grid, 1, 8)).toBe(true) // corridor
    expect(walkable(grid, 10, 4)).toBe(false) // a desk
    expect(walkable(grid, 0, 0)).toBe(false) // a wall
  })
})

describe('the art resolution', () => {
  it('draws a tile at the size the renderer says, and the floor at a multiple of it', () => {
    // Expressed as a formula rather than a number, so this survives the resolution change it
    // was written to guard.
    expect(TILE).toBeGreaterThan(0)
    expect(TILE % 8).toBe(0)

    const width = FIXTURE.cols * TILE
    const height = FIXTURE.rows * TILE
    expect(width % TILE).toBe(0)
    expect(height % TILE).toBe(0)
  })

  it('keeps the speckle a pure function of the pixel, so a rebuild is identical', () => {
    // A random speckle would reshuffle the carpet on every resize.
    expect(speck(0, 0)).toBe(speck(0, 0))
    expect(speck(37, 11)).toBe(speck(37, 11))
    expect(speck(0, 0)).not.toBe(speck(1, 0))
  })

  it('builds the prop atlas at its source size, one cell per prop', () => {
    const atlas = buildPropAtlas(fakeCanvas)
    expect(atlas.height).toBe(PROP_SOURCE)
    expect(atlas.width).toBe(PROP_SOURCE * PROP_KEYS.length)
  })

  it('bakes the static layer to the whole floor, in tiles times the tile', () => {
    const layer = buildStatic(FIXTURE, fakeCanvas)
    expect(layer.width).toBe(FIXTURE.cols * TILE)
    expect(layer.height).toBe(FIXTURE.rows * TILE)
  })

  it('stands a person on the tile their position names, whatever the sprite’s size', () => {
    // The anchor that has to survive the cast. A sprite shorter than a tile and one taller
    // than a tile must both put their feet on the same row, or a person's apparent position
    // would shift the day the real art lands and the depth sort would start disagreeing with
    // the collision grid.
    const context = new AnchorRecorder()
    const sheet = {
      canvas: fakeCanvas(),
      palette: {},
    }

    drawActor(context as unknown as CanvasRenderingContext2D, ACTOR_AT_TILE_3_4, sheet)

    const drawn = context.images.at(-1)
    expect(drawn).toBeDefined()
    // Feet on the last row of tile y=4, centred on tile x=3.
    expect((drawn?.dy ?? 0) + (drawn?.dh ?? 0)).toBe(5 * TILE)
    const centre = 3 * TILE + TILE / 2
    expect(Math.abs((drawn?.dx ?? 0) + (drawn?.dw ?? 0) / 2 - centre)).toBeLessThanOrEqual(1)
  })
})

describe('choosing a zoom', () => {
  it('never returns a fractional or zero zoom', () => {
    // Fractional would resample the art and it would stop being pixel art.
    for (const [w, h] of [
      [320, 220],
      [640, 400],
      [992, 576],
      [1280, 450],
      [1440, 900],
      [2400, 1400],
      [4000, 2400],
    ]) {
      const zoom = chooseZoom(w, h)
      expect(Number.isInteger(zoom), `${w}x${h}`).toBe(true)
      expect(zoom, `${w}x${h}`).toBeGreaterThanOrEqual(1)
    }
  })

  it('never shrinks as the stage grows', () => {
    let previous = 0
    for (const width of [320, 640, 992, 1280, 1600, 2000, 2400, 3200]) {
      const zoom = chooseZoom(width, Math.round(width * 0.6))
      expect(zoom, `${width}`).toBeGreaterThanOrEqual(previous)
      previous = zoom
    }
  })
})
