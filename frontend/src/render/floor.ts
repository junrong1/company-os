/**
 * The floor: baked once, and the prop atlas that is not.
 *
 * Ported from script section 9 of `company-os.html` (`:2352` sizing, `buildStatic`, the tile
 * painters, `buildPropAtlas`).
 *
 * **The floor is baked into one canvas at 1x, once.** Walls, floors, doors, windows, lamp pools and
 * the vignette never change during a run, and repainting sixteen thousand individual pixels per
 * frame would spend the whole budget on something static.
 *
 * **Props are deliberately *not* baked.** A desk has to be able to draw over the legs of the person
 * sitting behind it, which means props and people depth-sort against each other — so props are
 * blitted per frame from a pre-rendered atlas. Painting them pixel-by-pixel each frame would be far
 * too slow; baking them into the floor would put every desk permanently behind every person.
 *
 * **Geometry comes from the kernel.** The prototype generates the floorplan from the browser
 * window; here it arrives in the genesis event, already computed. Two clients at different window
 * sizes therefore render the same office rather than folding the same log to different desks.
 */

import { ART, PROPS } from './sprites'
import { FLOORS, WALLC } from './palettes'

/** One tile is 16 logical pixels, drawn at integer zoom with nearest-neighbour scaling. */
export const TILE = 16

export const WALL = 0
export const FLOOR = 1
export const SOLID = 2
export const CORRIDOR = 3

/** The floor as the kernel recorded it, in the shape the genesis payload carries. */
export interface FloorData {
  cols: number
  rows: number
  hall: [number, number, number, number]
  spawn: [number, number]
  rooms: Array<{
    id: string
    kind: string
    band: string
    box: [number, number, number, number]
    door: [number, number]
    seat_y: number
    slots: Array<[number, number]>
    visit: [number, number] | null
  }>
  /** `[x, y, sprite, solid]` */
  furniture: Array<[number, number, string, number]>
  lamps: Array<[number, number]>
  windows: Array<[number, number]>
}

/** Which floor style each room id uses. Recorded at genesis by the kernel's room plan. */
export const ROOM_FLOORS: Record<string, string> = {
  exec: 'wood',
  sales: 'blue',
  accounting: 'green',
  meeting: 'slate',
  hr: 'violet',
  support: 'amber',
  admin: 'slate',
  lounge: 'wood',
}

/** Paint one pixel-art grid into a context at (ox, oy). */
export function paint(
  context: CanvasRenderingContext2D,
  rows: string[],
  ox: number,
  oy: number,
  palette: Record<string, string | null>,
): void {
  for (let y = 0; y < rows.length; y += 1) {
    const row = rows[y]
    for (let x = 0; x < row.length; x += 1) {
      const colour = palette[row[x]]
      if (!colour) continue
      context.fillStyle = colour
      context.fillRect(ox + x, oy + y, 1, 1)
    }
  }
}

/**
 * Deterministic speckle, so carpet has texture without a noise texture.
 *
 * A pure function of the pixel coordinate, which matters more than it looks: a random speckle would
 * change on every rebuild of the static layer, so a resize would visibly reshuffle the carpet.
 */
export function speck(x: number, y: number): number {
  return (((x * 73856093) ^ (y * 19349663)) >>> 0) % 13
}

function paintFloorTile(
  context: CanvasRenderingContext2D,
  tx: number,
  ty: number,
  palette: { a: string; b: string; c: string; seam: string },
  wood: boolean,
): void {
  const ox = tx * TILE
  const oy = ty * TILE
  context.fillStyle = palette.a
  context.fillRect(ox, oy, TILE, TILE)

  if (wood) {
    // Floorboards run the whole way across; only every fourth tile gets an end joint, otherwise
    // the staggered joints read as brickwork rather than as boards.
    context.fillStyle = palette.b
    context.fillRect(ox, oy + 5, TILE, 5)
    context.fillStyle = palette.c
    context.fillRect(ox, oy + 11, TILE, 5)
    context.fillStyle = palette.seam
    context.fillRect(ox, oy + 4, TILE, 1)
    context.fillRect(ox, oy + 10, TILE, 1)
    context.fillRect(ox, oy + 15, TILE, 1)
    if (tx % 4 === 1) {
      context.fillRect(ox + 6, oy, 1, 4)
      context.fillRect(ox + 6, oy + 11, 1, 4)
    }
    if (tx % 4 === 3) context.fillRect(ox + 10, oy + 5, 1, 5)
    context.fillStyle = palette.a
    context.fillRect(ox + 3, oy + 7, 6, 1)
    context.fillRect(ox + 9, oy + 13, 5, 1)
    return
  }

  for (let y = 0; y < TILE; y += 1) {
    for (let x = 0; x < TILE; x += 1) {
      const n = speck(ox + x, oy + y)
      if (n === 0) {
        context.fillStyle = palette.b
        context.fillRect(ox + x, oy + y, 1, 1)
      } else if (n === 5) {
        context.fillStyle = palette.c
        context.fillRect(ox + x, oy + y, 1, 1)
      }
    }
  }
  context.fillStyle = palette.seam
  context.fillRect(ox, oy, TILE, 1)
  context.fillRect(ox, oy, 1, TILE)
}

function paintWall(context: CanvasRenderingContext2D, tx: number, ty: number): void {
  const ox = tx * TILE
  const oy = ty * TILE
  context.fillStyle = WALLC.face
  context.fillRect(ox, oy, TILE, TILE)
  context.fillStyle = WALLC.faceLo
  context.fillRect(ox, oy + 11, TILE, 3)
  context.fillStyle = WALLC.base
  context.fillRect(ox, oy + 14, TILE, 2)
  context.fillStyle = WALLC.cap
  context.fillRect(ox, oy, TILE, 4)
  context.fillStyle = WALLC.capLip
  context.fillRect(ox, oy, TILE, 1)
}

function paintWindow(context: CanvasRenderingContext2D, tx: number, ty: number): void {
  const ox = tx * TILE
  const oy = ty * TILE
  // Frame, glass, sky band, mullion, sill — the prototype's exact colours and offsets.
  context.fillStyle = '#2b313d'
  context.fillRect(ox + 2, oy + 4, 12, 9)
  context.fillStyle = '#6f8fb0'
  context.fillRect(ox + 3, oy + 5, 10, 7)
  context.fillStyle = '#9dbdd8'
  context.fillRect(ox + 3, oy + 5, 10, 3)
  context.fillStyle = '#2b313d'
  context.fillRect(ox + 8, oy + 5, 1, 7)
  context.fillStyle = '#8d97ab'
  context.fillRect(ox + 1, oy + 12, 14, 2)
}

/** Build the layer that never changes. Returns a canvas at 1x. */
export function buildStatic(floor: FloorData, make: () => HTMLCanvasElement): HTMLCanvasElement {
  const width = floor.cols * TILE
  const height = floor.rows * TILE
  const canvas = make()
  canvas.width = width
  canvas.height = height

  const context = canvas.getContext('2d')
  if (context === null) return canvas
  context.imageSmoothingEnabled = false

  const grid = buildGrid(floor)

  for (let y = 0; y < floor.rows; y += 1) {
    for (let x = 0; x < floor.cols; x += 1) {
      if (grid[y][x] === WALL) paintWall(context, x, y)
    }
  }
  for (const [x, y] of floor.windows) {
    if (grid[y][x] === WALL) paintWindow(context, x, y)
  }

  for (const room of floor.rooms) {
    const style = ROOM_FLOORS[room.id] ?? 'slate'
    const palette = FLOORS[style]
    const wood = style === 'wood'
    const [x1, y1, x2, y2] = room.box
    for (let y = y1; y <= y2; y += 1) {
      for (let x = x1; x <= x2; x += 1) paintFloorTile(context, x, y, palette, wood)
    }
    paintFloorTile(context, room.door[0], room.door[1], palette, wood)
  }

  const [hx1, hy1, hx2, hy2] = floor.hall
  for (let y = hy1; y <= hy2; y += 1) {
    for (let x = hx1; x <= hx2; x += 1) paintFloorTile(context, x, y, FLOORS.hall, false)
  }

  // Warm pools under the ceiling lamps, then a soft vignette at the edges. Baked, because the
  // lighting is as static as the floor it falls on.
  context.globalCompositeOperation = 'lighter'
  for (const [lx, ly] of floor.lamps) {
    const cx = lx * TILE + TILE / 2
    const cy = ly * TILE + TILE / 2
    const pool = context.createRadialGradient(cx, cy, 2, cx, cy, TILE * 2.6)
    pool.addColorStop(0, 'rgba(255, 226, 160, 0.16)')
    pool.addColorStop(0.55, 'rgba(255, 214, 140, 0.06)')
    pool.addColorStop(1, 'rgba(255, 214, 140, 0)')
    context.fillStyle = pool
    context.fillRect(cx - TILE * 3, cy - TILE * 3, TILE * 6, TILE * 6)
  }
  context.globalCompositeOperation = 'source-over'

  const vignette = context.createRadialGradient(
    width / 2,
    height / 2,
    Math.min(width, height) * 0.34,
    width / 2,
    height / 2,
    width * 0.62,
  )
  vignette.addColorStop(0, 'rgba(0,0,0,0)')
  vignette.addColorStop(1, 'rgba(0,0,0,0.34)')
  context.fillStyle = vignette
  context.fillRect(0, 0, width, height)

  return canvas
}

/** Rebuild the walkability grid the kernel used, from the recorded geometry. */
export function buildGrid(floor: FloorData): number[][] {
  const grid: number[][] = []
  for (let y = 0; y < floor.rows; y += 1) grid.push(new Array<number>(floor.cols).fill(WALL))

  for (const room of floor.rooms) {
    const [x1, y1, x2, y2] = room.box
    for (let y = y1; y <= y2; y += 1) {
      for (let x = x1; x <= x2; x += 1) grid[y][x] = FLOOR
    }
    grid[room.door[1]][room.door[0]] = FLOOR
  }

  const [hx1, hy1, hx2, hy2] = floor.hall
  for (let y = hy1; y <= hy2; y += 1) {
    for (let x = hx1; x <= hx2; x += 1) grid[y][x] = CORRIDOR
  }

  for (const [x, y, , solid] of floor.furniture) {
    if (solid) grid[y][x] = SOLID
  }

  return grid
}

// =========================================================================
// The prop atlas
// =========================================================================

export const PROP_KEYS = Object.keys(PROPS)
export const PROP_INDEX = new Map(PROP_KEYS.map((key, index) => [key, index]))

/** Pre-render every prop once into a strip, so a frame is a blit rather than a repaint. */
export function buildPropAtlas(make: () => HTMLCanvasElement): HTMLCanvasElement {
  const canvas = make()
  canvas.width = TILE * PROP_KEYS.length
  canvas.height = TILE

  const context = canvas.getContext('2d')
  if (context === null) return canvas

  PROP_KEYS.forEach((key, index) => paint(context, PROPS[key], index * TILE, 0, ART))
  return canvas
}

/** Blit one prop, with a contact shadow so furniture does not float. */
export function blitProp(
  context: CanvasRenderingContext2D,
  atlas: HTMLCanvasElement,
  sprite: string,
  tx: number,
  ty: number,
  solid: boolean,
): void {
  const index = PROP_INDEX.get(sprite)
  if (index === undefined) return

  if (solid) {
    context.fillStyle = 'rgba(0,0,0,0.22)'
    context.fillRect(tx * TILE + 2, ty * TILE + TILE - 2, TILE - 4, 2)
  }
  context.drawImage(atlas, index * TILE, 0, TILE, TILE, tx * TILE, ty * TILE, TILE, TILE)
}

/**
 * Choose the integer zoom and how many tiles fit, for a given stage size.
 *
 * Integer only, and nearest-neighbour: a fractional zoom resamples the art and it stops being pixel
 * art. Bigger windows get a bigger zoom rather than only more tiles, or people end up looking like
 * ants on a huge floor.
 */
export function chooseZoom(availableWidth: number, availableHeight: number): number {
  const width = Math.max(320, availableWidth)
  const height = Math.max(220, availableHeight)

  let zoom = width >= 1500 && height >= 700 ? 3 : width >= 620 ? 2 : 1
  while (zoom > 1) {
    const cols = Math.floor(width / (TILE * zoom))
    const rows = Math.floor(height / (TILE * zoom))
    if (cols >= 26 && rows >= 16) break
    zoom -= 1
  }
  return zoom
}
