#!/usr/bin/env node
/**
 * Draw the body rig, as PNG sheets in sentinel colours.
 *
 * The rig is the shared body every person in the office is built on: three views by four
 * frames, carrying skin, limb silhouette and the outline. Hair, clothing and accessories are
 * overlays and live in their own libraries.
 *
 * **Why this is a program and not a folder of hand-drawn cells.** A rig is the one piece of
 * character art that is almost entirely constraint rather than expression: the head has to be
 * the same size in all twelve cells or a person's identity changes when they turn round, the
 * feet have to land on row 63 in all twelve or the depth sort disagrees with the collision
 * grid, and the two contact frames have to be exact mirrors or the walk limps. Those are
 * properties a generator holds by construction and a hand keeps only by care. Expression
 * lives in the libraries this rig wears, which is where a hand belongs.
 *
 * The output goes through `grid-from-png.mjs` like any other art, so the committed grids come
 * off the same path an artist's PNG would. Re-run both when the rig changes:
 *
 *   node scripts/make-rig.mjs && node scripts/build-cast.mjs
 */

import { SENTINELS } from './sentinels.mjs'
import { writePng } from './png.mjs'

export const CELL_W = 48
export const CELL_H = 64
export const FRAMES = 4

/** A cell of glyph slots, transparent until something is drawn into it. */
function blank() {
  return Array.from({ length: CELL_H }, () => Array.from({ length: CELL_W }, () => null))
}

function put(cell, x, y, slot) {
  if (x < 0 || y < 0 || x >= CELL_W || y >= CELL_H) return
  cell[y][x] = slot
}

function rect(cell, x, y, width, height, slot) {
  for (let dy = 0; dy < height; dy += 1) {
    for (let dx = 0; dx < width; dx += 1) put(cell, x + dx, y + dy, slot)
  }
}

/** A filled ellipse, which is how a head and a shoulder both get their curve. */
function ellipse(cell, cx, cy, rx, ry, slot) {
  for (let y = -ry; y <= ry; y += 1) {
    for (let x = -rx; x <= rx; x += 1) {
      if ((x * x) / (rx * rx) + (y * y) / (ry * ry) <= 1) put(cell, cx + x, cy + y, slot)
    }
  }
}

/**
 * Trace every filled pixel that touches empty space, in the outline slot.
 *
 * Done once at the end rather than drawn by hand, which is what makes the silhouette exact:
 * an outline that is a consequence of the shape cannot disagree with the shape.
 */
function outline(cell) {
  const before = cell.map((row) => [...row])
  for (let y = 0; y < CELL_H; y += 1) {
    for (let x = 0; x < CELL_W; x += 1) {
      if (before[y][x] === null) continue
      const exposed = [
        [x - 1, y],
        [x + 1, y],
        [x, y - 1],
        [x, y + 1],
      ].some(([nx, ny]) => {
        if (nx < 0 || ny < 0 || nx >= CELL_W || ny >= CELL_H) return true
        return before[ny][nx] === null
      })
      if (exposed) cell[y][x] = 'outline'
    }
  }
}

// =========================================================================
// The figure
// =========================================================================

/*
 * Proportions, all measured against the 62 rows a figure occupies.
 *
 * The head is 18 of them, which is the ~3.5-head geometry `VISUAL_DESIGN.md` asks for and the
 * approved candidates were drawn to. Everything else follows from where the head ends.
 */
const CX = 24 // the figure's centre column
const HEAD_TOP = 2
const HEAD_R_X = 7
const HEAD_R_Y = 9
const HEAD_CY = HEAD_TOP + HEAD_R_Y
const NECK_Y = HEAD_CY + HEAD_R_Y - 1
const TORSO_TOP = NECK_Y + 2
const TORSO_BOTTOM = 41
const WAIST_Y = 34
const HIP_Y = TORSO_BOTTOM
const FOOT_Y = CELL_H - 1
const SHOE_TOP = FOOT_Y - 3

/** How far each foot swings from centre, per frame. Frame 0 is standing. */
const STRIDE = [
  { left: 0, right: 0, lift: 0 },
  { left: -4, right: 4, lift: 1 },
  { left: 0, right: 0, lift: 0 },
  { left: 4, right: -4, lift: 1 },
]

function head(cell, view) {
  ellipse(cell, CX, HEAD_CY, HEAD_R_X, HEAD_R_Y, 'skin')

  // A jaw that narrows, so a head is a head rather than a ball.
  rect(cell, CX - 4, HEAD_CY + HEAD_R_Y - 2, 9, 2, 'skin')
  put(cell, CX - HEAD_R_X, HEAD_CY + HEAD_R_Y - 2, null)
  put(cell, CX + HEAD_R_X, HEAD_CY + HEAD_R_Y - 2, null)

  if (view === 'down') {
    rect(cell, CX - 4, HEAD_CY - 1, 2, 2, 'eye')
    rect(cell, CX + 3, HEAD_CY - 1, 2, 2, 'eye')
    rect(cell, CX - 2, HEAD_CY + 4, 4, 1, 'mouth')
    // One shaded pixel under each cheekbone, which is the whole of the shading budget a face
    // this size can carry without turning into noise.
    put(cell, CX - 5, HEAD_CY + 2, 'skinShade')
    put(cell, CX + 5, HEAD_CY + 2, 'skinShade')
  }

  if (view === 'side') {
    // A nose, and one eye. A profile is mostly its silhouette at this scale.
    put(cell, CX + HEAD_R_X, HEAD_CY, 'skin')
    put(cell, CX + HEAD_R_X + 1, HEAD_CY + 1, 'skin')
    rect(cell, CX + 2, HEAD_CY - 1, 2, 2, 'eye')
    rect(cell, CX + 2, HEAD_CY + 4, 3, 1, 'mouth')
  }

  rect(cell, CX - 2, NECK_Y, 5, 3, 'skin')
  rect(cell, CX - 2, NECK_Y, 5, 1, 'skinShade')
}

function torso(cell, view) {
  const shoulder = view === 'side' ? 5 : 8
  const waist = view === 'side' ? 4 : 6

  // Tapered, not a box. A rectangle from shoulder to hem reads as a sign rather than as a
  // person — the waist is most of what says there is a body under the clothes.
  for (let y = TORSO_TOP; y < TORSO_BOTTOM; y += 1) {
    const t = Math.min(1, Math.max(0, (y - TORSO_TOP) / (WAIST_Y - TORSO_TOP)))
    const halfWidth = Math.round(shoulder - (shoulder - waist) * t)
    rect(cell, CX - halfWidth, y, halfWidth * 2 + 1, 1, 'top')
  }

  // Shoulders round off rather than ending square.
  put(cell, CX - shoulder, TORSO_TOP, null)
  put(cell, CX + shoulder, TORSO_TOP, null)

  // A hem, so the top reads as a garment with an edge rather than as a painted torso.
  rect(cell, CX - waist, TORSO_BOTTOM - 3, waist * 2 + 1, 2, 'topShade')
  rect(cell, CX - waist, TORSO_BOTTOM - 1, waist * 2 + 1, 1, 'topLine')
}

function arms(cell, view, frame) {
  const swing = STRIDE[frame]
  const armTop = TORSO_TOP + 2
  const armLength = 14

  // Drawn in the garment's shade rather than its base. An arm hanging at the side of a torso
  // the same colour is an arm nobody can see, and putting a gap between them instead would
  // read as a person holding their arms out.
  const draw = (side, offset) => {
    const x = view === 'side' ? CX + 4 : CX + side * 8
    rect(cell, x - 1, armTop + Math.max(0, offset), 3, armLength, 'topShade')
    rect(cell, x - 1, armTop + armLength + Math.max(0, offset), 3, 3, 'skin')
  }

  if (view === 'side') {
    // One arm, swinging opposite the near leg — which is what makes a profile walk read as a
    // walk rather than as a slide.
    draw(1, swing.right > 0 ? 1 : 0)
    return
  }

  draw(-1, swing.left > 0 ? 1 : 0)
  draw(1, swing.right > 0 ? 1 : 0)
}

function legs(cell, view, frame) {
  const swing = STRIDE[frame]
  const legTop = HIP_Y - 1
  const legHeight = SHOE_TOP - legTop

  const draw = (side, shift, lift) => {
    const x = view === 'side' ? CX - 3 + shift : CX + side * 5 - 3 + shift
    rect(cell, x, legTop, 6, legHeight - lift, 'legs')
    rect(cell, x, legTop + legHeight - lift - 2, 6, 2, 'legsShade')
    rect(cell, x - 1, SHOE_TOP - lift, 8, 4, 'shoes')
    rect(cell, x - 1, FOOT_Y - lift, 8, 1, 'shoesShade')
  }

  // Only the *forward* foot leaves the ground. Lifting both is a hop, and it also breaks the
  // one thing every cell has to agree on — that a person's contact point is row 63, which is
  // what the tile anchor and the depth sort both read.
  const lift = (offset) => (offset > 0 ? swing.lift : 0)

  if (view === 'side') {
    // The far leg first, so the near one draws over it.
    draw(0, swing.right, lift(swing.right))
    draw(0, swing.left, lift(swing.left))
    return
  }

  draw(-1, swing.left > 0 ? 1 : 0, lift(swing.left))
  draw(1, swing.right > 0 ? 1 : 0, lift(swing.right))
}

export function buildCell(view, frame) {
  const cell = blank()
  legs(cell, view, frame)
  torso(cell, view)
  arms(cell, view, frame)
  head(cell, view)
  outline(cell)
  return cell
}

// =========================================================================
// Out to a sheet
// =========================================================================

export function sheetPixels(views) {
  const width = CELL_W * FRAMES
  const height = CELL_H * views.length
  const pixels = Buffer.alloc(width * height * 4)

  views.forEach((view, row) => {
    for (let frame = 0; frame < FRAMES; frame += 1) {
      const cell = buildCell(view, frame)
      for (let y = 0; y < CELL_H; y += 1) {
        for (let x = 0; x < CELL_W; x += 1) {
          const slot = cell[y][x]
          if (slot === null) continue
          const hex = SENTINELS[slot]
          const value = Number.parseInt(hex.slice(1), 16)
          const at = ((row * CELL_H + y) * width + frame * CELL_W + x) * 4
          pixels[at] = (value >> 16) & 0xff
          pixels[at + 1] = (value >> 8) & 0xff
          pixels[at + 2] = value & 0xff
          pixels[at + 3] = 255
        }
      }
    }
  })

  return { width, height, pixels }
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const { mkdirSync, writeFileSync } = await import('node:fs')
  const views = ['down', 'up', 'side']
  const sheet = sheetPixels(views)

  mkdirSync(new URL('../art/rig/', import.meta.url), { recursive: true })
  writeFileSync(
    new URL('../art/rig/body.png', import.meta.url),
    writePng(sheet.width, sheet.height, sheet.pixels),
  )
  console.log(`art/rig/body.png — ${views.length} views × ${FRAMES} frames`)
}
