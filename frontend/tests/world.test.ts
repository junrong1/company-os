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
  ROOM_FLOORS,
  chooseZoom,
  speck,
  walkable,
} from '../src/render/floor'
import { drawActor } from '../src/render/actors'
import { ART } from '../src/render/sprites'
import { FLOORS, GLASS, WALLC } from '../src/render/palettes'
import { PAL, RESERVED_BEAM, ROOM_FLOOR, relativeLuminance } from '../src/design/tokens'

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

interface Fill {
  x: number
  y: number
  width: number
  height: number
  fill: string
}

interface Gradient {
  stops: { offset: number; colour: string }[]
  addColorStop: (offset: number, colour: string) => void
}

/**
 * A recording 2D context for the baked layer.
 *
 * Records fills, gradients and composite-operation changes, because those are the three
 * things the daylight shell's properties are about: what colour went down, how the light
 * falls off, and whether anything reached for the `lighter` blend that a near-white floor
 * cannot survive.
 */
class LayerRecorder {
  fillStyle: string | Gradient = ''
  globalCompositeOperation = 'source-over'
  readonly fills: Fill[] = []
  readonly gradients: Gradient[] = []
  readonly composites: string[] = []

  fillRect(x: number, y: number, width: number, height: number): void {
    if (typeof this.fillStyle === 'string') {
      this.fills.push({ x, y, width, height, fill: this.fillStyle })
    }
    this.composites.push(this.globalCompositeOperation)
  }

  createRadialGradient(): Gradient {
    const stops: { offset: number; colour: string }[] = []
    const gradient: Gradient = {
      stops,
      addColorStop: (offset, colour) => {
        stops.push({ offset, colour })
      },
    }
    this.gradients.push(gradient)
    return gradient
  }

  colours(): Set<string> {
    return new Set(this.fills.map((fill) => fill.fill))
  }
}

function bake(floor: FloorData = FIXTURE): LayerRecorder {
  const recorder = new LayerRecorder()
  buildStatic(
    floor,
    () =>
      ({
        width: 0,
        height: 0,
        getContext: () => recorder,
      }) as unknown as HTMLCanvasElement,
  )
  return recorder
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

// =========================================================================
// The daylight shell (U5)
// =========================================================================

describe('the baked layer', () => {
  it('lays down no dark field, whatever it lays down in lines', () => {
    // The vignette was a 34% black radial over the whole office and it is the single thing
    // that most made this product read as a control room. Stated as a property rather than as
    // "the vignette was deleted", because the failure mode is it coming back as something
    // else — an edge shade, an ambient occlusion pass, a "subtle" overlay.
    //
    // Scoped to *fields* rather than to every fill, because the props baked into this layer
    // are outlined in 鸽蓝 and have to be: an outline holds a shape apart from what is behind
    // it, and a dark outline on a light floor is the art direction working rather than
    // failing. A vignette is thousands of pixels; an outline is a line.
    const FIELD = 64

    for (const fill of bake().fills) {
      if (fill.width * fill.height <= FIELD) continue
      expect(
        relativeLuminance(fill.fill),
        `a ${fill.width}×${fill.height} field in ${fill.fill}`,
      ).toBeGreaterThan(relativeLuminance(PAL.jingyuhui) - 0.001)
    }
  })

  it('never reaches for a blend a near-white floor cannot survive', () => {
    // `lighter` had headroom on ink. On a daylight floor it goes straight to pure white and
    // the floor stops existing.
    expect(bake().composites.every((mode) => mode === 'source-over')).toBe(true)
  })

  it('spends the reserved amber on nothing', () => {
    expect(bake().colours()).not.toContain(RESERVED_BEAM)
  })

  it('draws every colour from the office palette rather than inventing one', () => {
    const known = new Set<string>([
      ...Object.values(PAL),
      ...Object.values(WALLC),
      ...Object.values(GLASS),
      ...Object.values(ART),
      ...Object.values(FLOORS).flatMap((style) => Object.values(style)),
    ])

    for (const colour of bake().colours()) {
      expect(known.has(colour), `${colour} belongs to no palette`).toBe(true)
    }
  })

  it('builds a wall out of a cap, a face and a trim line rather than a plinth', () => {
    const layer = bake()
    const drawn = layer.colours()

    expect(drawn).toContain(WALLC.face)
    expect(drawn).toContain(WALLC.cap)
    expect(drawn).toContain(WALLC.capLip)
    expect(drawn).toContain(WALLC.base)

    // The skirting is a line, not a foundation: it may not be the tallest band on the wall.
    const base = layer.fills.filter((fill) => fill.fill === WALLC.base)
    const cap = layer.fills.filter((fill) => fill.fill === WALLC.cap)
    expect(base.length).toBeGreaterThan(0)
    expect(Math.max(...base.map((f) => f.height))).toBeLessThan(
      Math.max(...cap.map((f) => f.height)),
    )
  })

  it('glazes the wall rather than painting over it', () => {
    // Deliberately not "filter the fills by the window's colours and check each one's tile".
    // The palette is shared on purpose — 竹绿 is a plant on a sill *and* the accounting
    // room's border course, 鲸鱼灰 is a window frame *and* administration's — so filtering by
    // value asks a question the palette cannot answer.
    //
    // What is actually being claimed is that nothing the shell bakes escapes the floor, and
    // windows are the only thing drawn on the outermost tiles, so a pane that overran its
    // tile would run off the canvas.
    const layer = bake()
    const width = FIXTURE.cols * TILE
    const height = FIXTURE.rows * TILE

    for (const fill of layer.fills) {
      expect(fill.x, `${fill.fill}`).toBeGreaterThanOrEqual(0)
      expect(fill.y, `${fill.fill}`).toBeGreaterThanOrEqual(0)
      expect(fill.x + fill.width, `${fill.fill}`).toBeLessThanOrEqual(width)
      expect(fill.y + fill.height, `${fill.fill}`).toBeLessThanOrEqual(height)
    }

    // And a window was drawn at all, so the sweep above is not passing on an empty wall.
    expect(layer.colours()).toContain(GLASS.sky)
    expect(layer.colours()).toContain(GLASS.glint)
  })

  it('gives each room a border course and a rug in its own department', () => {
    const layer = bake()
    const sales = FLOORS[ROOM_FLOORS.sales]
    const accounting = FLOORS[ROOM_FLOORS.accounting]

    expect(layer.colours()).toContain(sales.trim)
    expect(layer.colours()).toContain(sales.rug)
    expect(layer.colours()).toContain(accounting.trim)
    expect(layer.colours()).toContain(accounting.rug)

    // The rug is inset from the walls, which is what keeps the walkable middle readable.
    const rug = layer.fills.find((fill) => fill.fill === sales.rug)
    const [x1, y1] = FIXTURE.rooms[0].box
    expect(rug?.x).toBe((x1 + 1) * TILE)
    expect(rug?.y).toBe((y1 + 1) * TILE)
  })

  it('leaves the corridor without a rug, because nobody sits in one', () => {
    expect(FLOORS.hall.rug).toBeNull()
  })

  it('keeps every floor light enough to be a ground rather than a state', () => {
    // The inverse of the chrome's rule. A department stripe in the panels has to clear 3:1
    // because it is the only thing distinguishing one team from another; a department
    // *floor* has to do the opposite, or the office is seven coloured caves again.
    for (const [style, palette] of Object.entries(FLOORS)) {
      expect(relativeLuminance(palette.a), `${style} base`).toBeGreaterThan(0.6)
      if (palette.rug !== null) {
        expect(relativeLuminance(palette.rug), `${style} rug`).toBeGreaterThan(0.5)
      }
    }
  })

  it('knows a floor style for every room the office can build', () => {
    // `cs` and `people` fell through to slate while the panels beside them kept their
    // department, which is one roster answered two ways.
    for (const room of Object.keys(ROOM_FLOOR)) {
      expect(ROOM_FLOORS[room], room).toBeDefined()
      expect(FLOORS[ROOM_FLOORS[room]], room).toBeDefined()
    }
  })

  it('dresses the same sill the same way every time it is rebuilt', () => {
    // A random plant would redecorate the office on every resize.
    expect(bake().fills).toEqual(bake().fills)
  })

  it('lights the room from the glazing, falling off to nothing', () => {
    const layer = bake()

    expect(layer.gradients).toHaveLength(FIXTURE.windows.length)
    for (const gradient of layer.gradients) {
      const alphas = gradient.stops.map((stop) => Number(/,\s*([\d.]+)\)$/.exec(stop.colour)?.[1]))

      expect(gradient.stops.length).toBeGreaterThanOrEqual(3)
      // Brightest at the glass, nothing at the far edge, and monotonic in between.
      expect(alphas[0]).toBeGreaterThan(0)
      expect(alphas.at(-1)).toBe(0)
      for (let i = 1; i < alphas.length; i += 1) {
        expect(alphas[i]).toBeLessThanOrEqual(alphas[i - 1])
      }
      // Warm, and never the one warm value that means something else.
      for (const stop of gradient.stops) {
        expect(stop.colour).not.toContain(RESERVED_BEAM.slice(1))
      }
    }
  })

  it('builds a floor with no windows at all rather than throwing', () => {
    const dark: FloorData = { ...FIXTURE, windows: [] }
    const layer = bake(dark)
    expect(layer.gradients).toHaveLength(0)
    expect(layer.fills.length).toBeGreaterThan(0)
  })
})
