/**
 * Loading the cast, and deciding who anybody looks like.
 *
 * Every person in the office is drawn from `atlas.png` — one image holding all 33 approved
 * candidates, each as four facings by four frames. The art is the casting board's; the atlas
 * builder gave it directions and a stride and drew nothing.
 *
 * **The atlas arrives asynchronously and the office does not wait for it.** An image decode is
 * one round trip against a renderer that has to produce a frame every sixteen milliseconds, so
 * the frame loop draws the floor and the furniture from the first frame and starts drawing
 * people the moment the image resolves. Blocking would mean a blank office for as long as the
 * decode takes, which is the one thing the daylight redesign cannot afford to look like.
 */

import { ATLAS, CELL_HEIGHT, CELL_WIDTH, SHEET_FACINGS, SHEET_FRAMES } from './atlas-index'
import atlasUrl from './atlas.png'
import { idHash } from '../palette'
import { CEO_ID } from '../palettes'

export { CELL_HEIGHT, CELL_WIDTH, SHEET_FACINGS, SHEET_FRAMES }

/** How many appearances every named identity carries until final casting. */
export const APPEARANCES = 3

/** Frame 0 stands; 1 and 3 are the contacts and 2 is the pass. */
export const WALK_CYCLE = [1, 2, 3, 2] as const

/** The eleven identities the casting board covers, derived from the atlas rather than listed. */
export const IDENTITIES: string[] = [
  ...new Set(Object.keys(ATLAS).map((key) => key.slice(0, key.lastIndexOf('-')))),
].sort()

/**
 * Which of A, B or C this run shows for this person.
 *
 * A pure function of the run's seed and the person's id, and that is the whole mechanism: the
 * selection is recomputed identically on every frame of every session attached to the run, so
 * nothing has to be stored, transmitted or kept in sync. The kernel never learns the answer,
 * which is why "a cosmetic choice has no simulation effect" is true because there is no channel
 * by which it could be, rather than because nothing currently writes to one.
 */
export function appearanceFor(runSeed: number, personId: string): number {
  // Mixed rather than concatenated, so two people whose ids differ in their last character do
  // not move together when the seed changes.
  return idHash(`${personId}#${runSeed}`) % APPEARANCES
}

/**
 * Which candidate to draw somebody as.
 *
 * The eleven leads are cast to their own art. Anyone else — the procedural coworkers R9 names
 * — is drawn as a candidate chosen from their id, which is what makes them the same game
 * rather than a similar one: they are not *compatible* with the leads, they are literally the
 * same casting board.
 */
export function candidateFor(personId: string, runSeed: number): string {
  const letter = 'abc'[appearanceFor(runSeed, personId)]
  const own = `${personId}-${letter}`
  if (own in ATLAS) return own

  const keys = Object.keys(ATLAS).sort()
  return keys[idHash(`${personId}:cast`) % keys.length]
}

/** The six colours a person is made of, for the panels and the portraits. */
export function skinFor(personId: string, runSeed: number): CastSkin {
  return ATLAS[candidateFor(personId, runSeed)].skin
}

export interface CastSkin {
  skin: string
  hair: string
  top: string
  legs: string
  shoes: string
  accent: string
}

/** Where in the atlas one person's cell sits, in source pixels. */
export function cellRect(
  personId: string,
  runSeed: number,
  facing: number,
  frame: number,
): { sx: number; sy: number } {
  const entry = ATLAS[candidateFor(personId, runSeed)]
  return {
    sx: frame * CELL_WIDTH,
    sy: (entry.row * SHEET_FACINGS + facing) * CELL_HEIGHT,
  }
}

// =========================================================================
// Loading
// =========================================================================

let image: HTMLImageElement | null = null
let loading: Promise<HTMLImageElement> | null = null

/**
 * The atlas image, once.
 *
 * Module-scope on purpose, unlike the sprite-sheet cache the renderer owns: this is one
 * immutable image shared by every renderer that will ever exist in the page, so tying its
 * lifetime to a renderer instance would re-decode it on every hot update and every run.
 */
export function loadAtlas(): Promise<HTMLImageElement> {
  if (loading !== null) return loading

  loading = new Promise((resolve, reject) => {
    if (typeof Image === 'undefined') {
      reject(new Error('no Image constructor'))
      return
    }
    const element = new Image()
    element.decoding = 'async'
    element.onload = () => {
      image = element
      resolve(element)
    }
    element.onerror = () => reject(new Error('the cast atlas failed to load'))
    element.src = atlasUrl
  })

  return loading
}

/** The atlas if it has arrived, or `null`. The frame loop asks, it never waits. */
export function atlasImage(): HTMLImageElement | null {
  return image
}

/** For tests and for hot update: forget the image so the next `loadAtlas` starts over. */
export function resetAtlas(): void {
  image = null
  loading = null
}

/**
 * The CEO is on the board like everyone else.
 *
 * Kept as an assertion rather than an assumption: the player has to be findable among ten
 * staff at a glance, and they cannot be if the atlas has no art for them.
 */
export const CEO_HAS_ART = `${CEO_ID}-a` in ATLAS
