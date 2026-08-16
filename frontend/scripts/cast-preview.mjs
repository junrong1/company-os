/**
 * Composition, for the dev-only scripts.
 *
 * The client's `composeCell` is TypeScript and these are plain Node scripts; importing it
 * would mean a build step in front of tools whose value is being one command. The grids are
 * generated *text*, so reading them back is exact rather than approximate, and
 * `tests/cast.test.ts` holds this and the client to the same answer — a casting script that
 * disagreed with the product about what a person looks like would be worse than none.
 *
 * Two callers, and they want different things from it. `contact-sheet.mjs` wants pixels.
 * `make-manifests.mjs` wants only the silhouette, because that is what R8 is about: an
 * identity has to be recognisable with its name label hidden, and from across the office the
 * outline is all there is.
 */

import { readFileSync } from 'node:fs'

export const CELL_W = 48
export const CELL_H = 64

export const GLYPH_SLOTS = {
  s: 'skin', S: 'skinShade', l: 'skinLine',
  h: 'hair', H: 'hairLight', j: 'hairDark',
  t: 'top', T: 'topShade', u: 'topLine',
  p: 'legs', P: 'legsShade',
  b: 'shoes', B: 'shoesShade',
  k: 'outline', e: 'eye', m: 'mouth', a: 'accent',
}

/** Every 48-wide row literal in a generated module, in order. */
function rowsOf(file) {
  const text = readFileSync(new URL(`../src/render/cast/${file}`, import.meta.url), 'utf8')
  return [...text.matchAll(/'([.a-zA-Z]{48})'/g)].map((match) => match[1])
}

function chunk(rows, height) {
  const out = []
  for (let i = 0; i < rows.length; i += height) out.push(rows.slice(i, i + height))
  return out
}

const RIG = chunk(rowsOf('grammar.ts'), CELL_H)
const HAIR = chunk(rowsOf('hair.ts'), 24)
const OUTFITS = chunk(rowsOf('outfits.ts'), 24)
const ACCESSORIES = chunk(rowsOf('accessories.ts'), 40)

/**
 * How many *shapes* each library has: its cell count over the three views.
 *
 * Its own name because the two are easy to confuse and expensive to confuse. A caller that
 * picked a shape with `% HAIR.length` and indexed with `shape * 3` would reach past the end
 * two times in three, which is exactly what one did.
 */
export const SHAPES = {
  hair: HAIR.length / 3,
  outfits: OUTFITS.length / 3,
  accessories: ACCESSORIES.length / 3,
}

const BANDS = { hair: 0, outfits: 20, accessories: 0 }

/** One cell of one person, as slot names. View 0 is the down idle the candidates are drawn in. */
export function composeCell(manifest, view = 0, frame = 0) {
  const cell = Array.from({ length: CELL_H }, () => Array.from({ length: CELL_W }, () => null))

  const lay = (rows, origin) => {
    if (rows === undefined) return
    rows.forEach((row, y) => {
      const target = cell[y + origin]
      if (target === undefined) return
      ;[...row].forEach((glyph, x) => {
        if (glyph === '.') return
        target[x] = GLYPH_SLOTS[glyph]
      })
    })
  }

  lay(RIG[view * 4 + frame], 0)
  lay(OUTFITS[manifest.outfit * 3 + view], BANDS.outfits)
  lay(HAIR[manifest.hair * 3 + view], BANDS.hair)
  if (manifest.accessory !== null && manifest.accessory !== undefined) {
    lay(ACCESSORIES[manifest.accessory * 3 + view], BANDS.accessories)
  }

  return cell
}

/**
 * The outline of a person, with everything inside it thrown away.
 *
 * This is the string R8 is actually about. Two people whose silhouettes match are two people
 * a viewer cannot tell apart across a room, however different their colours are — and colour
 * is the first thing a small sprite loses to a busy background.
 */
export function silhouette(manifest) {
  return composeCell(manifest)
    .map((row) => row.map((slot) => (slot === null ? '.' : '#')).join(''))
    .join('')
}
