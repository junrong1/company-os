/**
 * Characters: the approved casting board, drawn on the floor.
 *
 * **A person is their own art.** Every figure in the office is a cell of `cast/atlas.png`,
 * which holds all 33 candidates the redesign approved, each as four facings by four frames.
 * There is no composition step and no runtime palette substitution, because there is nothing
 * to compose — the art already exists and it is better than anything assembled from parts.
 *
 * This replaced a layered rig, and the replacement is the interesting part. The rig was a
 * shared body wearing hair, outfit and accessory libraries; it scaled to any number of
 * coworkers and it produced people visibly cruder than the candidates sitting in `docs/`. At
 * 22 pixels wide a face is four pixels and a jacket is a silhouette, and a generator does not
 * reach what a person drew. Reaching for the board instead is both simpler and better.
 *
 * **Everything is depth-sorted together, props included.** A desk drawn before a person always
 * sits behind them, which is wrong for the desk they are sitting *at*; a desk drawn after
 * always sits in front, which hides anyone standing beside it. Sorting both by the row a
 * person's feet are on is what lets somebody be behind their own desk and in front of the one
 * below.
 */

import { RESERVED_BEAM, RESERVED_BEAM_EDGE } from '../design/tokens'
import {
  CELL_HEIGHT,
  CELL_WIDTH,
  SHEET_FRAMES,
  WALK_CYCLE,
  atlasImage,
  cellRect,
} from './cast/atlas'
import { CONTACT_SHADOW, TILE } from './floor'

/** Facing order, which is the order the atlas builder writes rows in. */
export const DIRS = ['down', 'up', 'left', 'right'] as const
export type Facing = (typeof DIRS)[number]

export const FRAMES = SHEET_FRAMES
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

/**
 * How far each actor has walked, so their legs keep their own cadence.
 *
 * Renderer state rather than actor state, because an `Actor` is projected fresh from the store
 * every frame and anything stored on one is thrown away before the next. Dropped by
 * `dispose()`, and pruned when somebody leaves the floor — a Map keyed by id that nothing ever
 * removes from is the same leak the renderer's lifecycle exists to prevent, in a different
 * shape.
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

/**
 * Draw one actor at its interpolated position.
 *
 * Silently draws nothing when the atlas has not arrived. The frame loop asks and never waits:
 * an image decode is one round trip against a renderer that owes a frame every sixteen
 * milliseconds, and a blank office for the length of a decode is the one thing this redesign
 * cannot afford to look like.
 */
export function drawActor(
  context: CanvasRenderingContext2D,
  actor: Actor,
  runSeed: number,
  frame = 0,
): void {
  const atlas = atlasImage()
  if (atlas === null) return

  const { left, top, feet } = placement(actor)
  const facing = Math.max(0, DIRS.indexOf(actor.facing))
  const { sx, sy } = cellRect(actor.id, runSeed, facing, frame)

  // A contact shadow, so a person does not float the way unshadowed furniture does. Shares the
  // value furniture uses — two shadows in one room lit differently is worse than none.
  context.fillStyle = CONTACT_SHADOW
  context.fillRect(left + 16, feet - 4, SPRITE_WIDTH - 32, 4)

  context.drawImage(
    atlas,
    sx,
    sy,
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
 * product whose single strongest claim is that there is exactly one signal that pulls the eye.
 * They are one value now, and it is the token module's.
 */
export const BEAM_COLOUR = RESERVED_BEAM

export function drawWaitingBeam(context: CanvasRenderingContext2D, actor: Actor): void {
  const { left, top } = placement(actor)
  const centre = left + Math.round(SPRITE_WIDTH / 2)

  // The edge first, then the amber inside it. On a daylight floor the amber alone is a pale
  // mark on a pale room; the outline is what makes the one signal that must not be missed
  // legible, and it is the same dove-blue separation the sprites take their edges from.
  context.fillStyle = RESERVED_BEAM_EDGE
  context.fillRect(centre - 4, top - 14, 8, 12)
  context.fillRect(centre - 4, top - 1, 8, 3)

  context.fillStyle = BEAM_COLOUR
  context.fillRect(centre - 3, top - 13, 6, 10)
  context.fillRect(centre - 3, top, 6, 1)
}
