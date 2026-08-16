/**
 * Characters: sprite sheets composed at runtime, and the depth sort that puts them in order.
 *
 * **A person is a rig wearing a manifest.** One authored body — three views by four frames —
 * plus hair, an outfit and at most one accessory, resolved against six colours that were read
 * out of that person's approved candidate. This is what the prototype's 10×16 cast already
 * did with one hair option and a hashed palette; what changed is fidelity and library size,
 * not architecture, which is why the cache and its `dispose()` contract are untouched.
 *
 * **The left-facing art is the right-facing art mirrored.** Drawing a separate left profile
 * would mean maintaining two grids that have to stay pixel-identical, and they would drift.
 * Mirroring makes that impossible by construction.
 *
 * **A sheet is one canvas write, not fifty thousand.** 192×256 is 49,152 pixels; `compose`
 * builds them as a flat buffer and this puts them down in one call. The prototype's
 * pixel-at-a-time paint was affordable at 1,920 and is not here.
 *
 * **Everything is depth-sorted together, props included.** A desk drawn before a person always
 * sits behind them, which is wrong for the desk they are sitting *at*; a desk drawn after
 * always sits in front, which hides anyone standing beside it. Sorting both by the row their
 * feet are on is what lets a person be behind their own desk and in front of the one below.
 */

import { RESERVED_BEAM, RESERVED_BEAM_EDGE } from '../design/tokens'
import { manifestFor, sheetKey } from './cast/appearance'
import { SHEET_HEIGHT, SHEET_ROWS, SHEET_WIDTH, composeSheet } from './cast/compose'
import { CELL_FRAMES, CELL_HEIGHT, CELL_WIDTH, WALK_CYCLE } from './cast/slots'
import { CONTACT_SHADOW, TILE } from './floor'

/** Facing order in a sheet's rows, which is the order `SHEET_ROWS` composes them in. */
export const DIRS = SHEET_ROWS.map((row) => row.facing) as unknown as readonly [
  'down',
  'up',
  'left',
  'right',
]
export type Facing = (typeof DIRS)[number]

export const FRAMES = CELL_FRAMES
export const SPRITE_WIDTH = CELL_WIDTH
export const SPRITE_HEIGHT = CELL_HEIGHT

export { WALK_CYCLE }

/**
 * How far a person travels between walk frames, in milli-tiles.
 *
 * The cadence used to come off the store's tick — `animTicks / 7` — which gave everybody the
 * same stride rate regardless of how fast they actually move. The CEO covers a tile in about
 * seven ticks and staff take about thirteen, so one of the two was always skating.
 *
 * Keying the frame to distance instead makes both correct from one rule, and 250 milli-tiles
 * is a quarter of a tile per step: a full four-frame cycle per tile, for a figure two tiles
 * tall.
 */
export const STRIDE_MILLI = 250

export interface Actor {
  id: string
  /** Position in milli-tiles, so interpolation stays integral. */
  xMilli: number
  yMilli: number
  facing: Facing
  /** Whether to animate the walk cycle. */
  moving: boolean
  /** Ticks elapsed. Retained for callers; the walk frame no longer reads it. */
  animTicks: number
  /** True when this person is waiting on a CEO decision. */
  waiting?: boolean
}

/** One person's sheet: four facings down, four frames across. */
export interface CharacterSheet {
  canvas: HTMLCanvasElement
  palette: Record<string, string | null>
}

/**
 * Build (and cache) a character sheet.
 *
 * The cache is passed in rather than module-scope, so a renderer's `dispose()` drops it. A
 * module-scope cache is what makes a hot update leak one sheet set per save.
 *
 * Keyed on the person *and their appearance*, not on the person: a resync into a different
 * run picks a different letter for the same id, and an id-only key would serve the previous
 * run's face out of the cache.
 */
export function characterSheet(
  id: string,
  runSeed: number,
  cache: Map<string, CharacterSheet>,
  make: () => HTMLCanvasElement,
): CharacterSheet {
  const key = sheetKey(id, runSeed)
  const cached = cache.get(key)
  if (cached !== undefined) return cached

  const canvas = make()
  canvas.width = SHEET_WIDTH
  canvas.height = SHEET_HEIGHT

  const manifest = manifestFor(id, runSeed)
  const context = canvas.getContext('2d')
  if (context !== null) {
    const data = composeSheet(manifest)
    // One write. `createImageData` rather than `new ImageData` because jsdom has the former
    // on the context and not the latter as a global.
    const image = context.createImageData(SHEET_WIDTH, SHEET_HEIGHT)
    image.data.set(data)
    context.putImageData(image, 0, 0)
  }

  const sheet: CharacterSheet = { canvas, palette: {} }
  cache.set(key, sheet)
  return sheet
}

/**
 * How far each actor has walked, so their legs keep their own cadence.
 *
 * Renderer state rather than actor state, because an `Actor` is projected fresh from the
 * store every frame and anything stored on one is thrown away before the next. Dropped by
 * `dispose()` alongside the sheet cache, and pruned when somebody leaves the floor — a Map
 * keyed by id that nothing ever removes from is the same leak in a different shape.
 */
export class StrideTracker {
  private readonly walked = new Map<string, number>()
  private readonly seen = new Map<string, readonly [number, number]>()

  /** Advance one actor by the distance they moved since the last frame. */
  advance(actor: Actor): void {
    const previous = this.seen.get(actor.id)
    this.seen.set(actor.id, [actor.xMilli, actor.yMilli])
    if (previous === undefined) return

    const moved = Math.abs(actor.xMilli - previous[0]) + Math.abs(actor.yMilli - previous[1])
    // A teleport — a resync, a fork, a seeded spawn — is not a walk, and treating it as one
    // would spin the legs through a whole cycle in a frame.
    if (moved > TILE * 20) return
    this.walked.set(actor.id, (this.walked.get(actor.id) ?? 0) + moved)
  }

  /** Which frame this actor's legs are on. Standing is always frame 0. */
  frame(actor: Actor): number {
    if (!actor.moving) return 0
    const steps = Math.floor((this.walked.get(actor.id) ?? 0) / STRIDE_MILLI)
    return WALK_CYCLE[steps % WALK_CYCLE.length]
  }

  /** Forget anyone no longer on the floor. */
  retain(ids: Iterable<string>): void {
    const live = new Set(ids)
    for (const id of [...this.walked.keys()]) if (!live.has(id)) this.walked.delete(id)
    for (const id of [...this.seen.keys()]) if (!live.has(id)) this.seen.delete(id)
  }

  get size(): number {
    return this.walked.size
  }

  clear(): void {
    this.walked.clear()
    this.seen.clear()
  }
}

/** Something to draw, and the y it sorts on. */
export interface Drawable {
  /** Screen y in logical pixels, used for the depth sort. */
  depth: number
  draw: (context: CanvasRenderingContext2D) => void
}

/**
 * Sort by depth, breaking ties stably.
 *
 * The tie-break is by insertion order rather than by id: two things at the same y must not swap
 * between frames, because a swap at 60fps reads as flicker rather than as a sort.
 */
export function depthSort(drawables: Drawable[]): Drawable[] {
  return drawables
    .map((item, index) => ({ item, index }))
    .sort((a, b) => a.item.depth - b.item.depth || a.index - b.index)
    .map(({ item }) => item)
}

/**
 * Where a person's cell lands, given the tile they are standing on.
 *
 * Their feet sit on the last row of that tile and the cell rises from there, which for a
 * 64-pixel figure on a 32-pixel tile means the top half of them overhangs the tile above.
 * That overhang is the whole reason the depth key is the feet row rather than the cell's top:
 * sorting by the top would put a person behind furniture they are standing well in front of.
 */
export function placement(actor: Actor): { left: number; top: number; feet: number } {
  const x = Math.round((actor.xMilli * TILE) / 1000)
  const y = Math.round((actor.yMilli * TILE) / 1000)
  return {
    left: x + Math.round((TILE - SPRITE_WIDTH) / 2),
    top: y + TILE - SPRITE_HEIGHT,
    feet: y + TILE,
  }
}

/** Draw one actor at its interpolated position. */
export function drawActor(
  context: CanvasRenderingContext2D,
  actor: Actor,
  sheet: CharacterSheet,
  frame = 0,
): void {
  const { left, top, feet } = placement(actor)
  const row = Math.max(0, DIRS.indexOf(actor.facing))

  // A contact shadow, so a person does not float the way unshadowed furniture does. Shares
  // the value furniture uses — two shadows in one room lit differently is worse than none.
  context.fillStyle = CONTACT_SHADOW
  context.fillRect(left + 14, feet - 4, SPRITE_WIDTH - 28, 4)

  context.drawImage(
    sheet.canvas,
    frame * SPRITE_WIDTH,
    row * SPRITE_HEIGHT,
    SPRITE_WIDTH,
    SPRITE_HEIGHT,
    left,
    top,
    SPRITE_WIDTH,
    SPRITE_HEIGHT,
  )
}

/**
 * The beam over someone waiting on a decision.
 *
 * Amber is reserved for exactly this meaning across the whole product — the art direction
 * spends it on nothing else — so this is the only place that draws it.
 *
 * It used to be `#f0a92b` while the chrome's was `#f2c46b`: two ambers for one meaning, in a
 * product whose single strongest claim is that there is exactly one signal that pulls the
 * eye. They are one value now, and it is the token module's.
 */
export const BEAM_COLOUR = RESERVED_BEAM

export function drawWaitingBeam(context: CanvasRenderingContext2D, actor: Actor): void {
  const { left, top } = placement(actor)
  const centre = left + Math.round(SPRITE_WIDTH / 2)

  // The edge first, then the amber inside it. On a daylight floor the amber alone is a pale
  // mark on a pale room; the outline is what makes the one signal that must not be missed
  // legible, and it is the same dove-blue separation the sprites take their edges from.
  context.fillStyle = RESERVED_BEAM_EDGE
  context.fillRect(centre - 3, top - 13, 6, 11)
  context.fillRect(centre - 3, top - 1, 6, 3)

  context.fillStyle = BEAM_COLOUR
  context.fillRect(centre - 2, top - 12, 4, 9)
  context.fillRect(centre - 2, top, 4, 1)
}
