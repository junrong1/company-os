/**
 * Characters: sprite sheets built at runtime, and the depth sort that puts them in the right order.
 *
 * Ported from script section 8 (`characterSheet`) and section 9 (the drawing pass) of
 * `company-os.html`.
 *
 * **Four facings by three walk frames, in one sheet per person.** Built once and cached, because a
 * sheet is 40x64 pixels painted one pixel at a time — cheap once, ruinous per frame.
 *
 * **The left-facing art is the right-facing art mirrored.** Drawing a separate left profile would
 * mean maintaining two grids that have to stay pixel-identical, and they would drift. Mirroring
 * makes that impossible by construction.
 *
 * **Everything is depth-sorted together, props included.** A desk drawn before a person always sits
 * behind them, which is wrong for the desk they are sitting *at*; a desk drawn after always sits in
 * front, which hides anyone standing beside it. Sorting both by their y coordinate is what lets a
 * person be behind their own desk and in front of the one below it.
 */

import { paint, TILE } from './floor'
import { hasLongHair, personPalette } from './palettes'
import { BODY, LEGS, LONG_HAIR } from './sprites'

/** Facing order in a sheet's rows. */
export const DIRS = ['down', 'up', 'left', 'right'] as const
export type Facing = (typeof DIRS)[number]

export const FRAMES = 3
export const SPRITE_WIDTH = 10
export const SPRITE_HEIGHT = 16

/** The prototype's walk cycle: frame 0, 1, 0, 2 — so the stride reads as alternating feet. */
export const WALK_CYCLE = [0, 1, 0, 2] as const

export interface Actor {
  id: string
  /** Position in milli-tiles, so interpolation stays integral. */
  xMilli: number
  yMilli: number
  facing: Facing
  /** Whether to animate the walk cycle. */
  moving: boolean
  /** Ticks elapsed, used to pick the walk frame. */
  animTicks: number
  /** True when this person is waiting on a CEO decision. */
  waiting?: boolean
}

/** One person's sheet: four facings down, three frames across. */
export interface CharacterSheet {
  canvas: HTMLCanvasElement
  palette: Record<string, string | null>
}

/**
 * Build (and cache) a character sheet.
 *
 * The cache is passed in rather than module-scope, so a renderer's `dispose()` drops it. A
 * module-scope cache is what makes a hot update leak one sheet set per save.
 */
export function characterSheet(
  id: string,
  cache: Map<string, CharacterSheet>,
  make: () => HTMLCanvasElement,
): CharacterSheet {
  const cached = cache.get(id)
  if (cached !== undefined) return cached

  const palette = personPalette(id)
  const long = hasLongHair(id)

  const canvas = make()
  canvas.width = SPRITE_WIDTH * FRAMES
  canvas.height = SPRITE_HEIGHT * DIRS.length

  const context = canvas.getContext('2d')
  if (context !== null) {
    DIRS.forEach((facing, row) => {
      const profile = facing === 'left' || facing === 'right'
      const body = BODY[profile ? 'side' : facing]
      const hair = long ? LONG_HAIR[profile ? 'side' : 'front'] : null

      for (let frame = 0; frame < FRAMES; frame += 1) {
        let rows = body.concat(LEGS[frame])
        let overlay = hair

        if (facing === 'left') {
          // Mirror the right-facing art so both profiles stay pixel-exact rather than being two
          // grids that have to be kept identical by hand.
          rows = rows.map((row) => [...row].reverse().join(''))
          if (overlay) overlay = overlay.map((row) => [...row].reverse().join(''))
        }

        paint(context, rows, frame * SPRITE_WIDTH, row * SPRITE_HEIGHT, palette)
        if (overlay) paint(context, overlay, frame * SPRITE_WIDTH, row * SPRITE_HEIGHT, palette)
      }
    })
  }

  const sheet: CharacterSheet = { canvas, palette }
  cache.set(id, sheet)
  return sheet
}

/** Which walk frame to draw. Standing still is always frame 0. */
export function walkFrame(actor: Actor): number {
  if (!actor.moving) return 0
  const step = Math.floor(actor.animTicks / 7) % WALK_CYCLE.length
  return WALK_CYCLE[step]
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

/** Draw one actor at its interpolated position. */
export function drawActor(
  context: CanvasRenderingContext2D,
  actor: Actor,
  sheet: CharacterSheet,
): void {
  const x = Math.round((actor.xMilli * TILE) / 1000)
  const y = Math.round((actor.yMilli * TILE) / 1000)

  const row = DIRS.indexOf(actor.facing)
  const frame = walkFrame(actor)

  // A contact shadow, so a person does not float the way unshadowed furniture does.
  context.fillStyle = 'rgba(0,0,0,0.20)'
  context.fillRect(x + 3, y + TILE - 2, SPRITE_WIDTH - 3, 2)

  context.drawImage(
    sheet.canvas,
    frame * SPRITE_WIDTH,
    Math.max(0, row) * SPRITE_HEIGHT,
    SPRITE_WIDTH,
    SPRITE_HEIGHT,
    x + 3,
    y,
    SPRITE_WIDTH,
    SPRITE_HEIGHT,
  )
}

/**
 * The beam over someone waiting on a decision.
 *
 * Amber is reserved for exactly this meaning across the whole product — the art direction spends it
 * on nothing else — so this is the only place that draws it.
 */
export const BEAM_COLOUR = '#f0a92b'

export function drawWaitingBeam(context: CanvasRenderingContext2D, actor: Actor): void {
  const x = Math.round((actor.xMilli * TILE) / 1000)
  const y = Math.round((actor.yMilli * TILE) / 1000)

  context.fillStyle = BEAM_COLOUR
  context.fillRect(x + 7, y - 6, 2, 4)
  context.fillRect(x + 7, y - 1, 2, 1)
}
