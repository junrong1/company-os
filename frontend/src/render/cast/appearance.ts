/**
 * Who a person looks like this run, and who anyone not on the roster looks like at all.
 *
 * Two rules, and the important thing about both is where they are *not*. The kernel never
 * learns which of a person's three appearances a run picked, so "a cosmetic choice has no
 * simulation effect" is true because there is no channel by which it could be, rather than
 * because nothing currently writes to one. And a background coworker is not *compatible* with
 * the authored leads — they are the same rig wearing a manifest that was derived instead of
 * cast, which is what makes them belong to the same game rather than merely match it.
 */

import { idHash } from '../palette'
import { HAIR } from './hair'
import { OUTFITS } from './outfits'
import { ACCESSORIES } from './accessories'
import { CAST } from './manifests'
import type { Manifest } from './compose'
import type { Skin } from './slots'

/** How many appearances every named identity carries until final casting. */
export const APPEARANCES = 3

/**
 * Which of A, B or C this run shows for this person.
 *
 * A pure function of the run's seed and the person's id, which is the whole mechanism: the
 * selection is recomputed identically on every frame of every session attached to the run, so
 * nothing has to be stored, transmitted or kept in sync. Two runs of one scenario open with
 * visibly different casts; one run holds its cast from genesis to horizon.
 */
export function appearanceFor(runSeed: number, personId: string): number {
  // Mixed rather than concatenated, so that two people whose ids differ in their last
  // character do not move together when the seed changes.
  const mixed = idHash(`${personId}#${runSeed}`)
  return mixed % APPEARANCES
}

/**
 * A palette for somebody the casting board never met.
 *
 * The procedural coworkers R9 names. Colours come from the same families the authored cast
 * uses rather than from anywhere in RGB, because "the same body grammar and palette logic" is
 * the requirement and a coworker in a hue no lead could wear would fail it while satisfying
 * every other clause.
 */
const SKINS = ['#f1c8ae', '#e3ab86', '#d89a52', '#c98662', '#b56743', '#a86745', '#8b5d47', '#70452f']
const HAIRS = ['#3a2728', '#4b302b', '#65443a', '#70452f', '#a8adb4', '#d6a04d', '#b56743', '#9d7f5f']
const TOPS = ['#2376b7', '#5a8fb7', '#b8c8d6', '#65758b', '#74759b', '#aa6a4c', '#c85c5c', '#2f8c8b']
const LEGS = ['#3f4b63', '#253147', '#4b302b', '#65758b']
const SHOES = ['#3a2728', '#65443a', '#253147', '#a8adb4']
const ACCENTS = ['#2376b7', '#d6a04d', '#189c7b', '#c85c5c', '#74759b', '#aa6a4c']

/**
 * Everything about a person, derived from their id alone.
 *
 * Each feature gets its *own* hash of the id rather than a different stride through one.
 * The prototype used coprime-ish divisors — `h % 8`, `floor(h / 7) % 8`, `floor(h / 13) % 8`
 * — and the idea was that walking one hash at different rates decorrelates the features. It
 * does not, for the ids this actually gets: `temp_001` and `temp_002` hash one apart, so only
 * the divide-by-one slot is guaranteed to change and every other feature agrees. A row of
 * background staff came out as near-clones with slightly different skin.
 *
 * Salting the id per feature decorrelates properly, because each salt lands somewhere
 * unrelated in the hash space rather than one step along the same line.
 */
function pickBy<T>(personId: string, salt: string, table: readonly T[]): T {
  return table[idHash(`${personId}:${salt}`) % table.length]
}

export function derivedManifest(personId: string): Manifest {
  const skin: Skin = {
    skin: pickBy(personId, 'skin', SKINS),
    hair: pickBy(personId, 'hair', HAIRS),
    top: pickBy(personId, 'top', TOPS),
    legs: pickBy(personId, 'legs', LEGS),
    shoes: pickBy(personId, 'shoes', SHOES),
    accent: pickBy(personId, 'accent', ACCENTS),
  }

  // One slot more than there are accessories, so somebody carries nothing — a company where
  // every single person wears a lanyard is a company with no lanyard signal.
  const accessory = idHash(`${personId}:accessory`) % (ACCESSORIES.length + 1)

  return {
    hair: pickBy(personId, 'hairShape', HAIR.map((_, index) => index)),
    outfit: pickBy(personId, 'outfit', OUTFITS.map((_, index) => index)),
    accessory: accessory === ACCESSORIES.length ? null : accessory,
    skin,
  }
}

/**
 * The manifest to draw this person with, in this run.
 *
 * Authored where the casting board has an answer, derived where it does not. Both come back
 * as the same shape, so nothing downstream branches on whether somebody is a lead.
 */
export function manifestFor(personId: string, runSeed: number): Manifest {
  const cast = CAST[personId]
  if (cast === undefined) return derivedManifest(personId)
  return cast[appearanceFor(runSeed, personId)]
}

/**
 * The cache key a sheet is stored under.
 *
 * Includes the appearance, so a resync into a different run cannot serve the previous run's
 * face out of a cache that only knew about ids.
 */
export function sheetKey(personId: string, runSeed: number): string {
  return CAST[personId] === undefined
    ? personId
    : `${personId}@${appearanceFor(runSeed, personId)}`
}
