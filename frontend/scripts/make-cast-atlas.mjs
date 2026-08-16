#!/usr/bin/env node
/**
 * The cast, built from the approved casting board itself.
 *
 * **This replaces a rig, and the reason is worth recording.** The first attempt authored a
 * shared body and dressed it from hair, outfit and accessory libraries — which is a sound way
 * to build a cast, scales to any number of coworkers, and produced people visibly cruder than
 * the 33 candidates sitting in `docs/assets/`. The casting board is approved art with faces,
 * jackets, posture and hair that a generator does not reach at 22 pixels wide. Rebuilding it
 * from parts was solving a problem that had already been solved.
 *
 * So the candidates *are* the art. What this script does is give them the directions and the
 * frames the movement model needs, by deriving them from each person's own pixels rather than
 * by drawing anything new:
 *
 *   down   the candidate, verbatim
 *   right  the candidate, verbatim
 *   left   the candidate, mirrored
 *   up     the candidate with the face turned away
 *   frames a bob and a leg offset, so a walk is that person walking
 *
 * The one honest compromise is the side view: a front pose cannot be turned into a true
 * profile by any transformation of its own pixels, and inventing one would put a face on the
 * cast that the casting board never approved. Using the front pose for horizontal movement is
 * the convention small-sprite games have always used, and it keeps every person exactly the
 * person who was cast.
 *
 *   node scripts/make-cast-atlas.mjs
 */

import { mkdirSync, readFileSync, readdirSync, writeFileSync } from 'node:fs'
import { basename, join } from 'node:path'

import { readPng, writePng } from './png.mjs'
import { extract } from './palette-from-candidate.mjs'

const CANDIDATES = new URL(
  '../../docs/assets/company-os-visual-redesign/roster-characters/',
  import.meta.url,
)

export const CELL_W = 48
export const CELL_H = 64
export const FRAMES = 4
/** down, up, left, right — the order the renderer indexes rows in. */
export const FACINGS = 4

const SHEET_W = CELL_W * FRAMES
const SHEET_H = CELL_H * FACINGS

// =========================================================================
// Pixel helpers
// =========================================================================

function cell() {
  return new Uint8ClampedArray(CELL_W * CELL_H * 4)
}

function get(px, x, y) {
  if (x < 0 || y < 0 || x >= CELL_W || y >= CELL_H) return null
  const at = (y * CELL_W + x) * 4
  if (px[at + 3] === 0) return null
  return [px[at], px[at + 1], px[at + 2]]
}

function set(px, x, y, rgb) {
  if (x < 0 || y < 0 || x >= CELL_W || y >= CELL_H) return
  const at = (y * CELL_W + x) * 4
  if (rgb === null) {
    px[at + 3] = 0
    return
  }
  px[at] = rgb[0]
  px[at + 1] = rgb[1]
  px[at + 2] = rgb[2]
  px[at + 3] = 255
}

function hexToRgb(hex) {
  const v = Number.parseInt(hex.slice(1), 16)
  return [(v >> 16) & 0xff, (v >> 8) & 0xff, v & 0xff]
}

/** The opaque bounding box of a figure. */
function bounds(px) {
  let minX = CELL_W
  let maxX = -1
  let minY = CELL_H
  let maxY = -1
  for (let y = 0; y < CELL_H; y += 1) {
    for (let x = 0; x < CELL_W; x += 1) {
      if (px[(y * CELL_W + x) * 4 + 3] === 0) continue
      minX = Math.min(minX, x)
      maxX = Math.max(maxX, x)
      minY = Math.min(minY, y)
      maxY = Math.max(maxY, y)
    }
  }
  return { minX, maxX, minY, maxY }
}

// =========================================================================
// Derivations
// =========================================================================

/** Mirror a cell horizontally. */
function mirror(px) {
  const out = cell()
  for (let y = 0; y < CELL_H; y += 1) {
    for (let x = 0; x < CELL_W; x += 1) {
      set(out, CELL_W - 1 - x, y, get(px, x, y))
    }
  }
  return out
}

/**
 * The same person, seen from behind.
 *
 * The head's interior becomes hair and the silhouette does not move, which is what makes the
 * turn read: a viewer recognises a person from their outline and their colours long before
 * they resolve a face at this size. Everything below the head is unchanged, because the back
 * of a jacket and the front of one are the same jacket at 22 pixels wide.
 *
 * The head fills completely. An earlier version kept a patch of skin for a neck and it read
 * as a hole punched through the hair, because at this size the jaw and the neck are the same
 * three rows — there is no room to be subtle about it. The collar below the head is what says
 * a head is attached to somebody.
 */
function fromBehind(px, skin) {
  const out = px.slice()
  const { minX, maxX, minY, maxY } = bounds(px)
  const height = maxY - minY + 1
  const headBottom = minY + Math.round(height * 0.28)

  const hair = hexToRgb(skin.hair)
  const hairDark = hexToRgb(skin.hair).map((c) => Math.round(c * 0.72))

  for (let y = minY; y <= headBottom; y += 1) {
    for (let x = minX; x <= maxX; x += 1) {
      const current = get(px, x, y)
      if (current === null) continue

      // The outline is structure and stays put — it is the silhouette, and the silhouette is
      // the whole reason this reads as the same person.
      const isEdge =
        get(px, x - 1, y) === null ||
        get(px, x + 1, y) === null ||
        get(px, x, y - 1) === null ||
        get(px, x, y + 1) === null
      if (isEdge) continue

      // A darker band at the crown gives the back of a head some form instead of a disc.
      set(out, x, y, y < minY + Math.round(height * 0.08) ? hairDark : hair)
    }
  }

  return out
}

/**
 * One frame of a walk, made out of the person who is walking.
 *
 * `lift` bobs everything above the knees; `swing` slides the lower legs. Both are one pixel,
 * because at this size two is a limp. Nothing is drawn — every pixel in every frame is a pixel
 * the casting board approved.
 */
function stride(px, lift, swing) {
  const out = cell()
  const { minX, maxX, minY, maxY } = bounds(px)
  const height = maxY - minY + 1
  const kneeY = minY + Math.round(height * 0.78)
  const centre = (minX + maxX) / 2

  for (let y = 0; y < CELL_H; y += 1) {
    for (let x = 0; x < CELL_W; x += 1) {
      const rgb = get(px, x, y)
      if (rgb === null) continue

      if (y < kneeY) {
        // Above the knee: the whole body rises.
        set(out, x, y - lift, rgb)
      } else if (y < maxY) {
        // The shins swing, left and right halves in opposite directions.
        set(out, x + swing * (x < centre ? -1 : 1), y - lift, rgb)
      } else {
        // The last row is contact with the floor and never leaves it. Every frame plants
        // somebody on the same tile, which the depth sort and the tile anchor both read.
        set(out, x, y, rgb)
      }
    }
  }

  return out
}

// =========================================================================
// A sheet per candidate
// =========================================================================

/** The four facings, each as four frames, for one candidate. */
export function sheetFor(front, skin) {
  const back = fromBehind(front, skin)
  const right = front
  const left = mirror(front)

  const rows = [front, back, left, right]
  const sheet = new Uint8ClampedArray(SHEET_W * SHEET_H * 4)

  rows.forEach((base, row) => {
    // Frame 0 stands. 1 and 3 are the contacts, 2 is the pass — so the cycle alternates feet
    // and returns through the same middle pose.
    const frames = [
      stride(base, 0, 0),
      stride(base, 1, 1),
      stride(base, 0, 0),
      stride(base, 1, -1),
    ]

    frames.forEach((frame, index) => {
      for (let y = 0; y < CELL_H; y += 1) {
        for (let x = 0; x < CELL_W; x += 1) {
          const at = (y * CELL_W + x) * 4
          if (frame[at + 3] === 0) continue
          const to = ((row * CELL_H + y) * SHEET_W + index * CELL_W + x) * 4
          sheet[to] = frame[at]
          sheet[to + 1] = frame[at + 1]
          sheet[to + 2] = frame[at + 2]
          sheet[to + 3] = 255
        }
      }
    })
  })

  return sheet
}

// =========================================================================
// The atlas
// =========================================================================

if (import.meta.url === `file://${process.argv[1]}`) {
  const files = readdirSync(CANDIDATES)
    .filter((name) => name.endsWith('.png'))
    .sort()

  const width = SHEET_W
  const height = SHEET_H * files.length
  const atlas = Buffer.alloc(width * height * 4)
  const index = {}

  files.forEach((file, row) => {
    const id = basename(file, '.png')
    const image = readPng(readFileSync(join(CANDIDATES.pathname, file)))
    if (image.width !== CELL_W || image.height !== CELL_H) {
      throw new Error(`${id}: expected ${CELL_W}×${CELL_H}, got ${image.width}×${image.height}`)
    }

    const front = new Uint8ClampedArray(image.pixels)
    const skin = extract(image, id)
    const sheet = sheetFor(front, skin)

    for (let y = 0; y < SHEET_H; y += 1) {
      const from = y * SHEET_W * 4
      const to = ((row * SHEET_H + y) * width) * 4
      Buffer.from(sheet.buffer, from, SHEET_W * 4).copy(atlas, to)
    }

    index[id] = { row, skin }
  })

  mkdirSync(new URL('../src/render/cast/', import.meta.url), { recursive: true })
  writeFileSync(
    new URL('../src/render/cast/atlas.png', import.meta.url),
    writePng(width, height, atlas),
  )
  writeFileSync(
    new URL('../src/render/cast/atlas-index.ts', import.meta.url),
    [
      '/**',
      ' * GENERATED — do not edit by hand.',
      ' *',
      ' * Built by `scripts/make-cast-atlas.mjs` from the approved casting board in',
      ' * `docs/assets/company-os-visual-redesign/roster-characters/`. Every pixel of every frame',
      ' * is a pixel that board approved; the script adds directions and a stride and draws',
      ' * nothing.',
      ' */',
      '',
      '/** One candidate: which row of the atlas it occupies, and the six colours it is made of. */',
      'export interface CastEntry {',
      '  row: number',
      '  skin: {',
      '    skin: string',
      '    hair: string',
      '    top: string',
      '    legs: string',
      '    shoes: string',
      '    accent: string',
      '  }',
      '}',
      '',
      `export const CELL_WIDTH = ${CELL_W}`,
      `export const CELL_HEIGHT = ${CELL_H}`,
      `export const SHEET_FRAMES = ${FRAMES}`,
      `export const SHEET_FACINGS = ${FACINGS}`,
      '',
      '/** Every candidate on the board, keyed `<identity>-<a|b|c>`. */',
      'export const ATLAS: Record<string, CastEntry> = {',
      ...Object.entries(index).map(
        ([id, entry]) =>
          `  '${id}': { row: ${entry.row}, skin: { ${Object.entries(entry.skin)
            .map(([slot, hex]) => `${slot}: '${hex}'`)
            .join(', ')} } },`,
      ),
      '}',
      '',
    ].join('\n'),
  )

  console.log(
    `src/render/cast/atlas.png — ${files.length} candidates × ${FACINGS} facings × ${FRAMES} frames`,
  )
}
