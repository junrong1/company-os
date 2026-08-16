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

import { PAL, withAlpha } from '../design/tokens'
import { ADVANCE, type PixelContext, paintText } from '../design/text'
import { ART, PROPS } from './sprites'
import {
  DAYLIGHT,
  DAYLIGHT_REACH,
  DAYLIGHT_STRENGTH,
  FLOORS,
  type FloorStyle,
  GLASS,
  WALLC,
} from './palettes'

/**
 * The shadow a thing standing on the floor casts.
 *
 * 鸽蓝 rather than black, and lighter than it was. A contact shadow's job is to say "this
 * object touches the ground here"; on ink that took 22% black because the ground was already
 * dark, and the same value on a daylight floor reads as a hole rather than as contact.
 * Shared by furniture and by people, so the two cannot drift into looking lit from different
 * rooms.
 */
export const CONTACT_SHADOW = withAlpha(PAL.text, 0.13)

/**
 * One tile is 32 logical pixels, drawn at integer zoom with nearest-neighbour scaling.
 *
 * **This is an art resolution, not a unit of geometry.** The kernel's floor is 31×18 *tiles*
 * and `buildGrid`, `walkable` and every milli-tile position work in those units, none of
 * which move when this number does. What moves is how much art one tile holds.
 *
 * It doubled from 16 for the cast. The approved 48×64 characters put a figure of roughly
 * 22×62 pixels inside their canvas; against a 16-pixel tile that person stands almost four
 * tiles tall and a tile and a half wide, which is not a character in an office but a
 * character wearing one. At 32 the same figure is 0.7 tiles wide by 1.9 tall — the
 * proportion the art was drawn for.
 *
 * The floor is consequently 992×576 at zoom 1 rather than 496×288, which is larger than the
 * stage on most laptops. `camera.ts` is what makes that survivable.
 */
export const TILE = 32

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

/**
 * Which floor style each room id uses. Recorded at genesis by the kernel's room plan.
 *
 * `cs` and `people` are here because `ROOM_FLOOR` in the token module already knew about
 * them and this map did not, so those two rooms fell through to slate and lost their
 * department while the panels beside them kept it. One roster, two answers.
 */
export const ROOM_FLOORS: Record<string, string> = {
  exec: 'wood',
  executive: 'wood',
  lounge: 'wood',
  sales: 'blue',
  accounting: 'green',
  meeting: 'slate',
  admin: 'slate',
  hr: 'violet',
  people: 'violet',
  support: 'amber',
  cs: 'amber',
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
  palette: FloorStyle,
  wood: boolean,
): void {
  const ox = tx * TILE
  const oy = ty * TILE
  context.fillStyle = palette.a
  context.fillRect(ox, oy, TILE, TILE)

  if (wood) {
    // Floorboards run the whole way across; only every fourth tile gets an end joint, otherwise
    // the staggered joints read as brickwork rather than as boards.
    //
    // Re-derived at 32 rather than doubled. The board *bands* scale — three boards of ten
    // rows instead of three of five — but a seam stays one pixel, because a doubled seam is a
    // two-pixel dark line and reads as a gap between boards rather than as the joint between
    // them. That distinction is the whole reason this function was not simply scaled at the
    // blit like the props were.
    context.fillStyle = palette.b
    context.fillRect(ox, oy + 11, TILE, 10)
    context.fillStyle = palette.c
    context.fillRect(ox, oy + 22, TILE, 10)
    context.fillStyle = palette.seam
    context.fillRect(ox, oy + 10, TILE, 1)
    context.fillRect(ox, oy + 21, TILE, 1)
    context.fillRect(ox, oy + 31, TILE, 1)
    if (tx % 4 === 1) {
      context.fillRect(ox + 12, oy, 1, 10)
      context.fillRect(ox + 12, oy + 22, 1, 9)
    }
    if (tx % 4 === 3) context.fillRect(ox + 20, oy + 11, 1, 10)
    // Two short grain lifts, so a board is not a flat band.
    context.fillStyle = palette.a
    context.fillRect(ox + 6, oy + 15, 12, 1)
    context.fillRect(ox + 18, oy + 26, 10, 1)
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

/** What a room is called, in the words the approved direction uses on the floor. */
export const ROOM_NAMES: Record<string, string> = {
  exec: 'EXECUTIVE',
  executive: 'EXECUTIVE',
  lounge: 'COMMONS',
  sales: 'SALES',
  accounting: 'ACCOUNTING',
  meeting: 'MEETING ROOM',
  admin: 'ADMINISTRATION',
  hr: 'PEOPLE',
  people: 'PEOPLE',
  support: 'CUSTOMER TEAM',
  cs: 'CUSTOMER TEAM',
}

/**
 * The room's name, set into its top-left corner.
 *
 * The approved direction labels every room on the floor, and it is doing more work than it
 * looks: a department is carried by a border course and a rug, and both of those say *which*
 * team without saying *what they do*. A player who has not memorised the palette reads the
 * word.
 *
 * Baked into the static layer rather than drawn per frame, because a room's name is as fixed
 * as the room. Drawn in the department's own trim so the label and the course agree.
 */
function paintRoomLabel(context: CanvasRenderingContext2D, room: FloorData['rooms'][number]): void {
  const name = ROOM_NAMES[room.id]
  if (name === undefined) return

  const [x1, y1, x2] = room.box
  const palette = FLOORS[ROOM_FLOORS[room.id] ?? 'slate']
  const width = (x2 - x1 + 1) * TILE
  const text = name.length * ADVANCE

  // Skipped rather than clipped in a room too narrow to hold it — a half-written word is
  // worse than none.
  if (text + 8 > width) return

  // Centred, not tucked into the corner the approved screen puts it in. The corner is where
  // the kernel puts a bookshelf, and the first version of this rendered EXECUTIVE as UTIVE
  // and CUSTOMER TEAM as OMER TEAM.
  const left = x1 * TILE + Math.round((width - text) / 2)
  const top = y1 * TILE + 8

  paintText(context as unknown as PixelContext, name, left, top, 1, palette.trim)
}

/**
 * A course of the department's colour running around the inside of the room's edge.
 *
 * Two pixels wide. The department used to be the whole floor, and this is what carries it
 * instead — enough to say whose room this is from across the office, not enough to compete
 * with the people standing in it.
 */
function paintBorderCourse(
  context: CanvasRenderingContext2D,
  box: readonly [number, number, number, number],
  palette: FloorStyle,
): void {
  const [x1, y1, x2, y2] = box
  const left = x1 * TILE
  const top = y1 * TILE
  const width = (x2 - x1 + 1) * TILE
  const height = (y2 - y1 + 1) * TILE

  context.fillStyle = palette.trim
  context.fillRect(left, top, width, 2)
  context.fillRect(left, top + height - 2, width, 2)
  context.fillRect(left, top, 2, height)
  context.fillRect(left + width - 2, top, 2, height)
}

/**
 * A rug, inset one tile from the room's walls.
 *
 * Two things at once. It is where the department's colour lives now, and it is what keeps
 * the walkable middle of the room readable — the art direction asks for a clear centre with
 * the dense detail pushed to the edges, and a rug is a shape that says "this is the middle"
 * without putting anything in it.
 *
 * Skipped in a room too small to inset, and in a corridor, which has no rug because nobody
 * sits in one.
 */
function paintRug(
  context: CanvasRenderingContext2D,
  box: readonly [number, number, number, number],
  palette: FloorStyle,
): void {
  if (palette.rug === null) return

  const [x1, y1, x2, y2] = box
  if (x2 - x1 < 3 || y2 - y1 < 3) return

  const left = (x1 + 1) * TILE
  const top = (y1 + 1) * TILE
  const width = (x2 - x1 - 1) * TILE
  const height = (y2 - y1 - 1) * TILE

  context.fillStyle = palette.rug
  context.fillRect(left, top, width, height)
  context.fillStyle = palette.trim
  context.fillRect(left, top, width, 1)
  context.fillRect(left, top + height - 1, width, 1)
  context.fillRect(left, top, 1, height)
  context.fillRect(left + width - 1, top, 1, height)
}

function paintWall(context: CanvasRenderingContext2D, tx: number, ty: number): void {
  const ox = tx * TILE
  const oy = ty * TILE
  context.fillStyle = WALLC.face
  context.fillRect(ox, oy, TILE, TILE)
  context.fillStyle = WALLC.faceLo
  context.fillRect(ox, oy + 22, TILE, 6)
  context.fillStyle = WALLC.base
  context.fillRect(ox, oy + 28, TILE, 4)
  context.fillStyle = WALLC.cap
  context.fillRect(ox, oy, TILE, 8)
  // One pixel, not two. This is the light catching the top edge of the wall, and a light
  // *band* would read as a second surface rather than as an edge.
  context.fillStyle = WALLC.capLip
  context.fillRect(ox, oy, TILE, 1)
}

/**
 * One window, and what is sitting on its sill.
 *
 * The dressing is a pure function of the tile coordinate, like `speck` and for the same
 * reason: a random plant would move every time the static layer was rebuilt, so a resize
 * would visibly redecorate the office.
 */
function paintWindow(context: CanvasRenderingContext2D, tx: number, ty: number): void {
  const ox = tx * TILE
  const oy = ty * TILE

  // Frame, then the pane inside it. The mullion stays one pixel for the same reason the
  // wall's cap lip does — two would read as a post rather than as a glazing bar.
  context.fillStyle = GLASS.frame
  context.fillRect(ox + 3, oy + 6, 26, 21)

  context.fillStyle = GLASS.skyLow
  context.fillRect(ox + 5, oy + 8, 22, 17)
  context.fillStyle = GLASS.sky
  context.fillRect(ox + 5, oy + 8, 22, 9)

  context.fillStyle = GLASS.frame
  context.fillRect(ox + 15, oy + 8, 1, 17)
  context.fillRect(ox + 5, oy + 16, 22, 1)

  // One diagonal glint. Without it the pane is a blue rectangle rather than glass.
  context.fillStyle = GLASS.glint
  for (let step = 0; step < 5; step += 1) {
    context.fillRect(ox + 7 + step, oy + 13 - step, 2, 1)
  }

  // Sill: timber, with its top edge catching the light.
  context.fillStyle = GLASS.sill
  context.fillRect(ox + 1, oy + 27, 30, 4)
  context.fillStyle = GLASS.sillLit
  context.fillRect(ox + 1, oy + 27, 30, 1)

  paintSillDressing(context, tx, ty, ox, oy)
}

/** A plant on some sills, books on others, nothing on most. */
function paintSillDressing(
  context: CanvasRenderingContext2D,
  tx: number,
  ty: number,
  ox: number,
  oy: number,
): void {
  const choice = speck(tx * TILE, ty * TILE) % 3

  if (choice === 0) {
    context.fillStyle = GLASS.spine
    context.fillRect(ox + 20, oy + 21, 3, 6)
    context.fillRect(ox + 24, oy + 22, 3, 5)
    context.fillStyle = GLASS.sill
    context.fillRect(ox + 23, oy + 21, 1, 6)
    return
  }

  if (choice === 1) {
    context.fillStyle = GLASS.spine
    context.fillRect(ox + 6, oy + 23, 6, 4)
    context.fillStyle = GLASS.leaf
    context.fillRect(ox + 7, oy + 19, 4, 4)
    context.fillRect(ox + 6, oy + 20, 6, 2)
    context.fillRect(ox + 8, oy + 17, 1, 2)
  }
}

/**
 * A frame around a doorway, so a door reads as an opening rather than as a missing wall.
 *
 * Drawn onto the wall tiles either side of the door rather than onto the door tile itself,
 * which stays walkable floor.
 */
function paintDoorFrame(
  context: CanvasRenderingContext2D,
  grid: number[][],
  dx: number,
  dy: number,
): void {
  const ox = dx * TILE
  const oy = dy * TILE
  const horizontal = grid[dy]?.[dx - 1] === WALL && grid[dy]?.[dx + 1] === WALL

  context.fillStyle = GLASS.sill
  if (horizontal) {
    context.fillRect(ox - 3, oy, 3, TILE)
    context.fillRect(ox + TILE, oy, 3, TILE)
    context.fillStyle = GLASS.sillLit
    context.fillRect(ox - 3, oy, 3, 1)
    context.fillRect(ox + TILE, oy, 3, 1)
    return
  }

  context.fillRect(ox, oy - 3, TILE, 3)
  context.fillRect(ox, oy + TILE, TILE, 3)
  context.fillStyle = GLASS.sillLit
  context.fillRect(ox, oy - 3, TILE, 1)
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
    paintBorderCourse(context, room.box, palette)
    paintRug(context, room.box, palette)
  }

  const [hx1, hy1, hx2, hy2] = floor.hall
  for (let y = hy1; y <= hy2; y += 1) {
    for (let x = hx1; x <= hx2; x += 1) paintFloorTile(context, x, y, FLOORS.hall, false)
  }

  for (const room of floor.rooms) paintDoorFrame(context, grid, room.door[0], room.door[1])
  for (const room of floor.rooms) paintRoomLabel(context, room)

  paintDressing(context, floor, grid)
  paintDaylight(context, floor)

  return canvas
}

/**
 * The props that make a room inhabited rather than merely furnished.
 *
 * R10 asks for artwork, plants and collaboration spaces, and the kernel's `floor.furniture` is
 * where furniture that *matters* lives — a desk somebody sits at, a chair, a solid a person
 * cannot walk through. Adding wall art to that list would mean a genesis payload change and a
 * walkability change for something nobody interacts with.
 *
 * So dressing is the client's, and it is non-solid by construction: it only ever lands on a
 * wall tile or on a floor tile the kernel has not already claimed, and it is baked into the
 * static layer rather than depth-sorted, so it can never come between a person and their desk.
 *
 * Placement is a pure function of the tile, like `speck` and the sill dressing, so a resize
 * does not redecorate.
 */
function paintDressing(
  context: CanvasRenderingContext2D,
  floor: FloorData,
  grid: number[][],
): void {
  const atlas = new Map<string, string[]>(Object.entries(PROPS))
  const claimed = new Set(floor.furniture.map(([x, y]) => `${x},${y}`))
  for (const room of floor.rooms) {
    for (const [sx, sy] of room.slots) claimed.add(`${sx},${sy}`)
    claimed.add(`${room.door[0]},${room.door[1]}`)
  }
  for (const [wx, wy] of floor.windows) claimed.add(`${wx},${wy}`)

  const place = (x: number, y: number, sprite: string): void => {
    if (claimed.has(`${x},${y}`)) return
    const grid5 = atlas.get(sprite)
    if (grid5 === undefined) return
    claimed.add(`${x},${y}`)
    paint(context, grid5, x * TILE, y * TILE, ART)
  }

  for (const room of floor.rooms) {
    const [x1, y1, x2, y2] = room.box
    if (x2 - x1 < 3 || y2 - y1 < 3) continue

    // Wall art, on the interior wall run above the room. The choice of piece is the tile's,
    // so two rooms do not both get the same picture.
    const artX = x1 + 2 + (speck(x1, y1) % Math.max(1, x2 - x1 - 3))
    if (grid[y1 - 1]?.[artX] === WALL) {
      place(artX, y1 - 1, speck(artX, y1) % 2 === 0 ? 'art' : 'pinboard')
    }

    // A plant in a corner the desks did not take, and a stool beside it in the larger rooms.
    place(x2, y1, 'floorplant')
    if (x2 - x1 >= 5) place(x1, y2, speck(x1, y2) % 2 === 0 ? 'stool' : 'lamp')
  }
}

/**
 * Light falling into the room from the glazing.
 *
 * This replaces two things that both had to go. The lamp pools were warm circles on the
 * floor under ceiling fixtures — an ink-room idea, and on a near-white floor the `lighter`
 * composite they used blows straight to white and the floor stops existing. And the vignette
 * was a 34% black radial over the whole office, which is exactly the dark overlay R10
 * forbids and most of why the product read as a control room.
 *
 * What replaces them is directional: light comes from where the windows are, falls off over
 * a few tiles, and never brightens past the surface it is warming, because it is a warm
 * colour at a low alpha rather than an additive blend.
 */
function paintDaylight(context: CanvasRenderingContext2D, floor: FloorData): void {
  const reach = TILE * DAYLIGHT_REACH

  for (const [wx, wy] of floor.windows) {
    const cx = wx * TILE + TILE / 2
    // Anchored just inside the glass rather than at the tile's centre, so the falloff starts
    // at the opening instead of inside the wall.
    const inward = wy === 0 ? 1 : -1
    const cy = wy * TILE + TILE / 2 + (inward * TILE) / 2

    const light = context.createRadialGradient(cx, cy, TILE / 2, cx, cy, reach)
    light.addColorStop(0, withAlpha(DAYLIGHT, DAYLIGHT_STRENGTH))
    light.addColorStop(0.5, withAlpha(DAYLIGHT, DAYLIGHT_STRENGTH * 0.35))
    light.addColorStop(1, withAlpha(DAYLIGHT, 0))
    context.fillStyle = light
    context.fillRect(cx - reach, cy - reach, reach * 2, reach * 2)
  }
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

/**
 * Whether a tile can be stood on, matching `simcore.world.walkable` exactly.
 *
 * Duplicated on purpose, like the tick arithmetic in `interpolate.ts`: the client predicts the
 * CEO's position locally and has to reach the kernel's answer, collision included. A client
 * that let the CEO through a wall would not merely look wrong — it would diverge from the
 * kernel on the very next position echo, and the banner would report a mystery.
 *
 * Out of bounds is not walkable rather than an error, because the lookahead probe deliberately
 * asks about tiles past the edge.
 */
export function walkable(grid: number[][], x: number, y: number): boolean {
  if (x < 0 || y < 0 || y >= grid.length) return false
  const row = grid[y]
  if (x >= row.length) return false
  return row[x] === FLOOR || row[x] === CORRIDOR
}

// =========================================================================
// The prop atlas
// =========================================================================

export const PROP_KEYS = Object.keys(PROPS)
export const PROP_INDEX = new Map(PROP_KEYS.map((key, index) => [key, index]))

/**
 * How many logical pixels one prop grid is authored at.
 *
 * The same as `TILE` again. For one commit it was 16 while the resolution change landed and
 * the props were still the prototype's art scaled at the blit; now they are drawn at the size
 * they are shown at, and the scaling is gone. Kept as its own name rather than folded back
 * into `TILE` because they are different claims — one is how big a tile is drawn, the other
 * is how big the art for it was authored — and the redraw is exactly the moment that
 * distinction was load-bearing.
 */
export const PROP_SOURCE = TILE

/** Pre-render every prop once into a strip, so a frame is a blit rather than a repaint. */
export function buildPropAtlas(make: () => HTMLCanvasElement): HTMLCanvasElement {
  const canvas = make()
  canvas.width = PROP_SOURCE * PROP_KEYS.length
  canvas.height = PROP_SOURCE

  const context = canvas.getContext('2d')
  if (context === null) return canvas

  PROP_KEYS.forEach((key, index) => paint(context, PROPS[key], index * PROP_SOURCE, 0, ART))
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
    context.fillStyle = CONTACT_SHADOW
    context.fillRect(tx * TILE + 4, ty * TILE + TILE - 4, TILE - 8, 4)
  }
  // Source is `PROP_SOURCE` square, destination is `TILE` square. Nearest-neighbour, because
  // the renderer disables smoothing before it draws anything.
  context.drawImage(
    atlas,
    index * PROP_SOURCE,
    0,
    PROP_SOURCE,
    PROP_SOURCE,
    tx * TILE,
    ty * TILE,
    TILE,
    TILE,
  )
}

/**
 * Choose the integer zoom for a given stage size.
 *
 * Integer only, and nearest-neighbour: a fractional zoom resamples the art and it stops being
 * pixel art. Bigger windows get a bigger zoom rather than only more tiles, or people end up
 * looking like ants on a huge floor.
 *
 * Two things changed when the tile doubled. The ladder is {1, 2} rather than {1, 2, 3},
 * because zoom 1 at 32 pixels a tile is exactly what zoom 2 at 16 was on screen — no display
 * loses fidelity, the rungs are just numbered differently.
 *
 * And the "26×16 tiles must fit" clamp is gone. It existed because the canvas was the whole
 * floor and a zoom that overflowed the stage had nowhere to put the overflow. The camera
 * answers that now, so the question here narrows to the one it was always really asking: how
 * big should a person be on this display.
 */
export function chooseZoom(availableWidth: number, availableHeight: number): number {
  const width = Math.max(320, availableWidth)
  const height = Math.max(220, availableHeight)

  return width >= 1280 && height >= 700 ? 2 : 1
}
