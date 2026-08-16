#!/usr/bin/env node
/**
 * Pull a person's six-slot palette out of their approved candidate sprite.
 *
 * The 33 candidates in `docs/assets/company-os-visual-redesign/roster-characters/` are the
 * casting board the redesign approved. They are indexed PNGs sharing a disciplined 33-colour
 * union, which makes this mechanical: read the pixels, look at where in the figure each
 * colour lives, and assign it to the slot that region belongs to.
 *
 * Mechanical is the point. Transcribing thirty-three palettes by hand is thirty-three
 * chances to typo a hex digit into art nobody will look at closely enough to catch, and the
 * symptom would be one person whose jacket is very slightly the wrong blue.
 *
 *   node scripts/palette-from-candidate.mjs <candidate.png> [...]
 *
 * Prints a manifest fragment per file. The extraction is a starting point that a casting
 * pass is expected to correct by eye — a figure whose jacket and trousers share a value
 * needs a person to decide which is which — so it prints rather than writes.
 */

import { readFileSync } from 'node:fs'
import { basename } from 'node:path'

import { hexAt, readPng } from './png.mjs'

/**
 * Where in a 48×64 figure each slot lives.
 *
 * Fractions of the figure's own bounding box rather than of the canvas, because a candidate
 * occupies roughly 22 of 48 columns and the empty margin would drag every band off centre.
 *
 * `skin` and `hair` are not in this list, and the reason is worth stating. Reading them as
 * two stacked bands — crown then face — gets them backwards on any figure whose hair frames
 * the face, which is most of them: the top band picks up a highlight and the band below it
 * picks up the hair still hanging there. They are found by `faceAndHair` instead, which asks
 * a question bands cannot: what colour is at the centre of the head, where the eyes are.
 */
const BANDS = [
  { slot: 'top', top: 0.34, bottom: 0.62 },
  { slot: 'legs', top: 0.66, bottom: 0.88 },
  { slot: 'shoes', top: 0.92, bottom: 1.0 },
]

/** A figure is about 3.5 heads tall, so the eyes sit here and the crown sits above them. */
const FACE_TOP = 0.13
const FACE_BOTTOM = 0.24
const CROWN_BOTTOM = 0.1

/**
 * How red a colour is against how blue, which is what separates skin from hair here.
 *
 * Lightness does not: the darkest skin tone in this roster and the lightest hair colour sit
 * within a few points of each other, so any threshold either casts a shadowed jaw as skin or
 * refuses a real deep skin tone. Warmth separates them cleanly across all 33.
 */
function warmth(hex) {
  const value = Number.parseInt(hex.slice(1), 16)
  return ((value >> 16) & 0xff) / Math.max(1, value & 0xff)
}

/** Distance from grey, for finding the one colour a person is accented with. */
function chroma(hex) {
  const value = Number.parseInt(hex.slice(1), 16)
  const r = (value >> 16) & 0xff
  const g = (value >> 8) & 0xff
  const b = value & 0xff
  return Math.max(r, g, b) - Math.min(r, g, b)
}

export function extract(image, label = 'candidate') {
  const { width, height, pixels } = image

  let minX = width
  let maxX = -1
  let minY = height
  let maxY = -1
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      if (pixels[(y * width + x) * 4 + 3] === 0) continue
      minX = Math.min(minX, x)
      maxX = Math.max(maxX, x)
      minY = Math.min(minY, y)
      maxY = Math.max(maxY, y)
    }
  }
  if (maxX < 0) throw new Error(`${label}: every pixel is transparent`)

  const figureTop = minY
  const figureHeight = maxY - minY + 1

  const tally = (from, to, fromX = minX, toX = maxX, skip = new Set()) => {
    const counts = new Map()
    for (let y = from; y <= to; y += 1) {
      for (let x = fromX; x <= toX; x += 1) {
        const index = y * width + x
        if (pixels[index * 4 + 3] === 0) continue
        const hex = hexAt(pixels, index)
        if (skip.has(hex)) continue
        counts.set(hex, (counts.get(hex) ?? 0) + 1)
      }
    }
    return counts
  }

  const commonest = (counts, exclude = new Set()) =>
    [...counts]
      .filter(([hex]) => !exclude.has(hex))
      .sort((a, b) => b[1] - a[1])[0]?.[0]

  /**
   * The outline, found by asking what an outline actually is.
   *
   * It is the colour on the silhouette: the opaque pixels with a transparent neighbour. Two
   * cheaper definitions were tried and both are wrong. A fixed lightness threshold puts the
   * cut in the wrong place for candidates whose outline is 鸽蓝 at 0.19 rather than something
   * obviously black. "The darkest common colour" picks a person's dark hair instead, which is
   * darker than 鸽蓝 and just as common — and then the real outline, unexcluded, wins the
   * crown and the figure is cast with 鸽蓝 hair.
   *
   * Tracing the boundary has neither failure mode, because nothing but the outline is on it.
   */
  const whole = tally(figureTop, maxY)
  const boundary = new Map()
  for (let y = minY; y <= maxY; y += 1) {
    for (let x = minX; x <= maxX; x += 1) {
      const index = y * width + x
      if (pixels[index * 4 + 3] === 0) continue
      const exposed = [
        [x - 1, y],
        [x + 1, y],
        [x, y - 1],
        [x, y + 1],
      ].some(([nx, ny]) => {
        if (nx < 0 || ny < 0 || nx >= width || ny >= height) return true
        return pixels[(ny * width + nx) * 4 + 3] === 0
      })
      if (!exposed) continue
      const hex = hexAt(pixels, index)
      boundary.set(hex, (boundary.get(hex) ?? 0) + 1)
    }
  }

  /**
   * Every colour that is mostly edge, not just the commonest one.
   *
   * Some candidates outline in two values — a hard 鸽蓝 and a softer shade under it — and
   * excluding only the first leaves the second to win a crown on the figures whose hair is
   * short enough that the outline is most of what is up there. A colour that spends more than
   * a third of its pixels on the silhouette is structure, whatever else it is.
   */
  const ignore = new Set()
  for (const [hex, edge] of boundary) {
    if (edge >= (whole.get(hex) ?? edge) / 3) ignore.add(hex)
  }

  /**
   * Skin from the face, hair from the crown above it.
   *
   * The face is the centre of the head at eye height, which is the one place on a figure that
   * is skin on every identity — a hat covers the crown, a collar covers the neck, sleeves
   * cover the arms, but if the face were covered there would be no face. Hair is then simply
   * the crown's colour that is not that.
   */
  const band = (top, bottom) => [
    figureTop + Math.floor(figureHeight * top),
    figureTop + Math.min(figureHeight - 1, Math.ceil(figureHeight * bottom)),
  ]

  const figureWidth = maxX - minX + 1
  const faceLeft = minX + Math.floor(figureWidth * 0.3)
  const faceRight = maxX - Math.floor(figureWidth * 0.3)
  const [faceFrom, faceTo] = band(FACE_TOP, FACE_BOTTOM)

  // Skin is the commonest thing at the face that could plausibly *be* skin. Without a guard,
  // a heavy beard, a fringe or a shadowed jaw wins the box on a handful of candidates and
  // casts them with near-black skin — which reads at native scale as a person with no face.
  //
  // The guard is warmth rather than lightness, because lightness does not separate them: the
  // darkest skin in this roster and the lightest hair sit within a few points of each other.
  // Red-against-blue does separate them cleanly — every skin tone in the candidate palette
  // runs above 2:1 and every hair colour below 1.8:1.
  const faceCounts = tally(faceFrom, faceTo, faceLeft, faceRight, ignore)
  const plausible = new Map([...faceCounts].filter(([hex]) => warmth(hex) > 2))
  const skin = commonest(plausible) ?? commonest(faceCounts)
  if (skin === undefined) throw new Error(`${label}: no skin found at the face`)

  const [crownFrom, crownTo] = band(0, CROWN_BOTTOM)
  const hair =
    commonest(tally(crownFrom, crownTo, minX, maxX, ignore), new Set([skin])) ?? skin

  const claimed = new Set([skin, hair])
  const skinSlots = { skin, hair }

  for (const { slot, top, bottom } of BANDS) {
    const [from, to] = band(top, bottom)
    const counts = tally(from, to, minX, maxX, ignore)
    // Falling back to a colour already claimed rather than throwing, because sharing one is a
    // real thing a person can look like: dark shoes under dark trousers give a shoe band with
    // nothing in it that the legs did not already take. Refusing to cast that person would be
    // the script deciding an outfit is invalid.
    const pick = commonest(counts, claimed) ?? commonest(counts) ?? skinSlots.legs
    if (pick === undefined) throw new Error(`${label}: nothing at all in the ${slot} band`)
    claimed.add(pick)
    skinSlots[slot] = pick
  }

  // The accent is whatever is left with the most chroma — a scarf, a lanyard, a bag. If a
  // person has none, they take their own top, which reads as "no accent" rather than as a
  // random colour appearing on their cuff.
  const everything = [...tally(figureTop, maxY)]
    .filter(([hex]) => !claimed.has(hex))
    .sort((a, b) => chroma(b[0]) - chroma(a[0]))
  skinSlots.accent = everything[0]?.[0] ?? skinSlots.top

  return skinSlots
}

function main(files) {
  if (files.length === 0) {
    console.error('usage: palette-from-candidate.mjs <candidate.png> [...]')
    process.exit(2)
  }

  for (const file of files) {
    const id = basename(file, '.png')
    const skin = extract(readPng(readFileSync(file)), id)
    const body = Object.entries(skin)
      .map(([slot, hex]) => `${slot}: '${hex}'`)
      .join(', ')
    console.log(`  '${id}': { ${body} },`)
  }
}

if (import.meta.url === `file://${process.argv[1]}`) {
  main(process.argv.slice(2))
}
