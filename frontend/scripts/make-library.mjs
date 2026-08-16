#!/usr/bin/env node
/**
 * Draw the feature libraries: hair, outfits, accessories.
 *
 * These are what turn one body into a cast. The rig is constraint — a head that has to be the
 * same size in twelve cells, feet that have to land on one row — and these are the opposite:
 * the only rule is that any two shapes must be distinguishable by silhouette alone, because
 * R8 asks that a person be identifiable with their name label hidden.
 *
 * **Overlays are bands, not cells.** Hair occupies the head; an outfit occupies the torso and
 * the limbs; an accessory occupies a few rows. Authoring each as a full 48×64 cell would
 * commit tens of thousands of transparent glyphs whose only job is to be transparent. Each
 * library declares where its band starts and composition offsets it.
 *
 *   node scripts/make-library.mjs && node scripts/build-cast.mjs
 */

import { mkdirSync, writeFileSync } from 'node:fs'

import { SENTINELS } from './sentinels.mjs'
import { writePng } from './png.mjs'

const CELL_W = 48
const CX = 24
const VIEWS = ['down', 'up', 'side']

/** Mirrors the rig's head geometry. A hair shape that disagrees floats above the scalp. */
const HEAD_TOP = 2
const HEAD_CY = 11
const HEAD_R_X = 7
const HEAD_R_Y = 9

/**
 * A band of rows, addressed in whole-cell coordinates.
 *
 * `origin` is the row of the 48×64 cell that row 0 of this band sits on, and every write
 * subtracts it. That way the geometry in the shapes above is written against the rig's
 * coordinates — a hair shape says "the scalp is at row 2" because on the rig it is — while
 * only the rows that carry anything are stored.
 */
function band(height, origin) {
  const rows = Array.from({ length: height }, () => Array.from({ length: CELL_W }, () => null))
  return { rows, origin, height }
}

function put(cell, x, y, slot) {
  const row = y - cell.origin
  if (x < 0 || row < 0 || x >= CELL_W || row >= cell.height) return
  cell.rows[row][x] = slot
}

function rect(cell, x, y, width, height, slot) {
  for (let dy = 0; dy < height; dy += 1) {
    for (let dx = 0; dx < width; dx += 1) put(cell, x + dx, y + dy, slot)
  }
}

function ellipse(cell, cx, cy, rx, ry, slot, from = -Infinity, to = Infinity) {
  for (let y = -ry; y <= ry; y += 1) {
    if (cy + y < from || cy + y > to) continue
    for (let x = -rx; x <= rx; x += 1) {
      if ((x * x) / (rx * rx) + (y * y) / (ry * ry) <= 1) put(cell, cx + x, cy + y, slot)
    }
  }
}

/** The scalp: the top of the head plus one pixel, which is where all hair starts. */
function scalp(cell, slot, downTo) {
  ellipse(cell, CX, HEAD_CY, HEAD_R_X + 1, HEAD_R_Y + 1, slot, HEAD_TOP - 1, downTo)
}

// =========================================================================
// Hair
// =========================================================================

/*
 * Eight shapes, chosen to differ in *silhouette* rather than in detail. At native scale a
 * fringe and a side parting are the same shape; a crop and a bun are not. What has to survive
 * is the outline, because that is all there is to read from across the office.
 */
const HAIR = [
  {
    name: 'crop',
    draw(cell, view) {
      scalp(cell, 'hair', HEAD_CY - 3)
      if (view !== 'up') rect(cell, CX - 7, HEAD_CY - 3, 15, 1, 'hair')
      rect(cell, CX - 5, HEAD_TOP - 1, 11, 2, 'hairLight')
    },
  },
  {
    name: 'part',
    draw(cell, view) {
      scalp(cell, 'hair', HEAD_CY - 1)
      // The parting itself, and a sweep that falls to one side.
      rect(cell, CX - 1, HEAD_TOP - 1, 2, 5, 'hairDark')
      rect(cell, view === 'side' ? CX + 3 : CX - 8, HEAD_CY - 4, 5, 6, 'hair')
      rect(cell, CX - 4, HEAD_TOP, 7, 2, 'hairLight')
    },
  },
  {
    name: 'curls',
    draw(cell) {
      scalp(cell, 'hair', HEAD_CY - 1)
      // A lumpy edge. Curls read as a broken silhouette, never as texture inside one.
      for (const [dx, dy] of [
        [-9, -6],
        [-8, -2],
        [-6, -8],
        [-2, -10],
        [2, -10],
        [6, -8],
        [8, -2],
        [9, -6],
      ]) {
        ellipse(cell, CX + dx, HEAD_CY + dy, 3, 3, 'hair')
      }
      ellipse(cell, CX, HEAD_CY - 8, 4, 3, 'hairLight')
    },
  },
  {
    name: 'bob',
    draw(cell, view) {
      scalp(cell, 'hair', HEAD_CY + 4)
      // Cut level with the jaw on both sides, which is the whole of a bob's silhouette.
      rect(cell, CX - 9, HEAD_CY - 4, 3, 10, 'hair')
      if (view !== 'side') rect(cell, CX + 7, HEAD_CY - 4, 3, 10, 'hair')
      rect(cell, CX - 6, HEAD_TOP - 1, 12, 2, 'hairLight')
      // Carved back off the face, or it is a helmet.
      if (view === 'down') rect(cell, CX - 5, HEAD_CY - 3, 11, 8, null)
    },
  },
  {
    name: 'long',
    draw(cell, view) {
      scalp(cell, 'hair', HEAD_CY + 2)
      rect(cell, CX - 9, HEAD_CY - 4, 3, 18, 'hair')
      if (view !== 'side') rect(cell, CX + 7, HEAD_CY - 4, 3, 18, 'hair')
      if (view === 'up') rect(cell, CX - 7, HEAD_CY - 4, 15, 18, 'hair')
      rect(cell, CX - 6, HEAD_TOP - 1, 12, 2, 'hairLight')
      if (view === 'down') rect(cell, CX - 5, HEAD_CY - 3, 11, 8, null)
    },
  },
  {
    name: 'tied',
    draw(cell, view) {
      scalp(cell, 'hair', HEAD_CY - 1)
      rect(cell, CX - 5, HEAD_TOP - 1, 11, 2, 'hairLight')
      // The tail is the identity, and it belongs behind the head — so it is biggest from
      // behind and a stub from the front.
      if (view === 'up') ellipse(cell, CX, HEAD_CY + 6, 4, 6, 'hair')
      else if (view === 'side') ellipse(cell, CX - 6, HEAD_CY + 3, 3, 5, 'hair')
      else rect(cell, CX - 2, HEAD_CY + 7, 5, 3, 'hair')
    },
  },
  {
    name: 'locs',
    draw(cell, view) {
      scalp(cell, 'hair', HEAD_CY - 1)
      // Vertical strands past the jaw, with gaps — a comb silhouette rather than a curtain.
      for (const dx of [-9, -6, -3, 0, 3, 6, 9]) {
        if (view === 'side' && dx > 2) continue
        rect(cell, CX + dx - 1, HEAD_CY - 3, 2, 14, 'hair')
      }
      rect(cell, CX - 6, HEAD_TOP - 1, 12, 2, 'hairLight')
      if (view === 'down') rect(cell, CX - 5, HEAD_CY - 2, 11, 7, null)
    },
  },
  {
    name: 'shaved',
    draw(cell) {
      // Almost nothing, on purpose. A cast where every silhouette has a mass on top of it has
      // no silhouette variation at all, so one identity has to be the person with none.
      scalp(cell, 'hairDark', HEAD_TOP + 4)
      rect(cell, CX - 5, HEAD_TOP, 11, 1, 'hair')
    },
  },
]

// =========================================================================
// Outfits
// =========================================================================

/*
 * Written in whole-cell coordinates, like everything else here, and stored from row 20 down.
 * `SHOULDER` and `HEM` are the rig's own torso bounds — an outfit that used its own numbering
 * would be an outfit that fits the body only by coincidence.
 */
const OUTFIT_BAND = 20
const OUTFIT_HEIGHT = 24
const SHOULDER = 21
const HEM = 41

/**
 * The rig's own shoulder half-width. An outfit that stays inside it changes nothing a viewer
 * can read from across the office.
 *
 * This is the finding that reshaped half the library. The first set drew every garment
 * *within* the body the rig already fills — a collar here, a seam there — so hair was the
 * only channel that changed a person's outline, and eight hair shapes cannot distinguish
 * eleven people by silhouette. R8 asks for a combination of silhouette, hair, skin, outfit
 * and accent, and an outfit that never touches the outline is not in that combination.
 *
 * So half of these now break the shoulder line: a flared jacket, a bulky knit, a hood, puff
 * sleeves. The other half stay tailored on purpose, because a cast where everybody has a big
 * silhouette has no silhouette variation either.
 */
const BODY_HALF = 8

const OUTFITS = [
  {
    name: 'shirt',
    draw(cell, view) {
      collar(cell, view)
      rect(cell, CX - 8, SHOULDER + 3, 17, 1, 'topLine')
      // Rolled cuffs: a small step out at the forearm. Small, but on the outline — the three
      // tailored outfits used to be identical in silhouette, which meant only five of the
      // eight were doing any work at the distance a person is actually read from.
      rect(cell, CX - BODY_HALF - 1, SHOULDER + 12, 3, 3, 'topShade')
      if (view !== 'side') rect(cell, CX + BODY_HALF - 1, SHOULDER + 12, 3, 3, 'topShade')
    },
  },
  {
    name: 'jacket',
    draw(cell, view) {
      collar(cell, view)
      // Open and flared past the shoulder, so the outline itself says "jacket" — the lapels
      // alone were invisible from any distance at which a person is 22 pixels wide.
      rect(cell, CX - BODY_HALF - 2, SHOULDER + 2, 4, 16, 'accent')
      if (view !== 'side') rect(cell, CX + BODY_HALF - 1, SHOULDER + 2, 4, 16, 'accent')
      rect(cell, CX - 3, SHOULDER + 2, 7, 5, 'topShade')
    },
  },
  {
    name: 'blazer',
    draw(cell, view) {
      collar(cell, view)
      // Squared shoulders — a tailored jacket's one structural claim, and one pixel of it is
      // enough to be a different outline from a shirt's.
      rect(cell, CX - BODY_HALF - 1, SHOULDER + 1, 19, 3, 'topShade')
      rect(cell, CX - 8, SHOULDER + 4, 2, HEM - 5, 'topShade')
      if (view !== 'side') rect(cell, CX + 7, SHOULDER + 4, 2, HEM - 5, 'topShade')
      // A buttoned front: one vertical seam and two accent pips.
      rect(cell, CX, SHOULDER + 4, 1, HEM - 8, 'topLine')
      put(cell, CX + 1, SHOULDER + 7, 'accent')
      put(cell, CX + 1, SHOULDER + 12, 'accent')
    },
  },
  {
    name: 'knit',
    draw(cell, view) {
      collar(cell, view)
      // Bulky: a shoulder wider than the body under it, which is most of what reads as wool.
      rect(cell, CX - BODY_HALF - 1, SHOULDER + 1, 19, 6, 'top')
      rect(cell, CX - BODY_HALF - 1, SHOULDER + 1, 19, 1, 'topShade')
      // Ribbing at the hem, which is the rest of it.
      rect(cell, CX - 7, HEM - 4, 15, 3, 'topShade')
      for (let y = SHOULDER + 8; y < HEM - 5; y += 3) {
        rect(cell, CX - 6, y, 13, 1, 'topShade')
      }
    },
  },
  {
    name: 'polo',
    draw(cell, view) {
      collar(cell, view)
      rect(cell, CX - 3, SHOULDER, 7, 4, 'topShade')
      rect(cell, CX, SHOULDER + 1, 1, 4, 'topLine')
      // Short sleeves that end in a cuff and leave the forearm bare, so the outline steps
      // *in* where the other outfits step out.
      rect(cell, CX - BODY_HALF - 1, SHOULDER + 2, 3, 6, 'top')
      if (view !== 'side') rect(cell, CX + BODY_HALF - 1, SHOULDER + 2, 3, 6, 'top')
      rect(cell, CX - BODY_HALF - 1, SHOULDER + 7, 3, 2, 'topLine')
      if (view !== 'side') rect(cell, CX + BODY_HALF - 1, SHOULDER + 7, 3, 2, 'topLine')
    },
  },
  {
    name: 'hoodie',
    draw(cell, view) {
      // The hood is the silhouette, and it sits above and behind the shoulders.
      ellipse(cell, CX, SHOULDER - 2, BODY_HALF + 1, 5, 'topShade', SHOULDER - 5, SHOULDER + 1)
      collar(cell, view)
      rect(cell, CX - 5, SHOULDER + 1, 11, 3, 'topShade')
      rect(cell, CX - 4, SHOULDER + 8, 9, 4, 'topLine')
      rect(cell, CX - 1, SHOULDER + 2, 1, 4, 'accent')
    },
  },
  {
    name: 'blouse',
    draw(cell, view) {
      collar(cell, view)
      // Puff sleeves: a bulge at the shoulder that falls back to the arm underneath.
      ellipse(cell, CX - BODY_HALF - 1, SHOULDER + 4, 3, 4, 'top')
      if (view !== 'side') ellipse(cell, CX + BODY_HALF + 1, SHOULDER + 4, 3, 4, 'top')
      rect(cell, CX - 8, HEM - 3, 17, 1, 'topLine')
      put(cell, CX, SHOULDER + 3, 'accent')
    },
  },
  {
    name: 'apron',
    draw(cell, view) {
      collar(cell, view)
      // A bib and two straps, with the skirt of it wider than the body. Reads instantly and
      // belongs to exactly one kind of role.
      rect(cell, CX - 5, SHOULDER + 3, 11, 12, 'accent')
      rect(cell, CX - BODY_HALF - 1, SHOULDER + 14, 19, HEM - SHOULDER - 15, 'accent')
      rect(cell, CX - 5, SHOULDER, 2, 4, 'accent')
      if (view !== 'side') rect(cell, CX + 4, SHOULDER, 2, 4, 'accent')
      rect(cell, CX - 5, SHOULDER + 10, 11, 1, 'topLine')
    },
  },
]

/** Every outfit has a neckline; only the shape of it differs. */
function collar(cell, view) {
  if (view === 'up') {
    rect(cell, CX - 4, 0, 9, 2, 'topShade')
    return
  }
  rect(cell, CX - 3, 0, 7, 2, 'topShade')
  if (view === 'down') rect(cell, CX - 2, 0, 5, 1, 'topLine')
}

// =========================================================================
// Accessories
// =========================================================================

/*
 * From the top of the cell down past the wrists, because these hang off a head, a neck or an
 * arm and those span most of a figure. At most one per person: R8 allows one restrained
 * accent, and two make a costume.
 */
const ACCESSORY_BAND = 0
const ACCESSORY_HEIGHT = 40
const EYE_Y = HEAD_CY - 1
const NECK_Y = 20
const WRIST_Y = 37
const ACCESSORIES = [
  {
    name: 'glasses',
    draw(cell, view) {
      // Nothing from behind. A pair of glasses seen from the back of a head is two pixels of
      // arm, and drawing them anyway is how an accessory starts reading as a headband.
      if (view === 'up') return
      if (view === 'side') {
        rect(cell, CX + 1, EYE_Y - 1, 5, 4, 'outline')
        rect(cell, CX + 2, EYE_Y, 3, 2, 'accent')
        return
      }
      rect(cell, CX - 6, EYE_Y - 1, 5, 4, 'outline')
      rect(cell, CX + 2, EYE_Y - 1, 5, 4, 'outline')
      rect(cell, CX - 5, EYE_Y, 3, 2, 'accent')
      rect(cell, CX + 3, EYE_Y, 3, 2, 'accent')
      rect(cell, CX - 1, EYE_Y, 2, 1, 'outline')
    },
  },
  {
    name: 'lanyard',
    draw(cell, view) {
      rect(cell, CX - 4, NECK_Y + 1, 1, 6, 'accent')
      if (view !== 'side') rect(cell, CX + 4, NECK_Y + 1, 1, 6, 'accent')
      rect(cell, CX - 2, NECK_Y + 7, 5, 5, 'accent')
      rect(cell, CX - 1, NECK_Y + 8, 3, 3, 'skinLine')
    },
  },
  {
    name: 'headset',
    draw(cell, view) {
      rect(cell, CX - 9, HEAD_TOP + 2, 2, 8, 'outline')
      if (view !== 'side') rect(cell, CX + 8, HEAD_TOP + 2, 2, 8, 'outline')
      rect(cell, CX - 5, HEAD_TOP - 2, 11, 2, 'outline')
      rect(cell, CX - 10, EYE_Y - 2, 3, 5, 'accent')
      if (view !== 'side') rect(cell, CX + 8, EYE_Y - 2, 3, 5, 'accent')
      // The boom mic, which only exists from the front and the side.
      if (view === 'down') rect(cell, CX - 7, EYE_Y + 4, 5, 1, 'outline')
      if (view === 'side') rect(cell, CX + 2, EYE_Y + 4, 5, 1, 'outline')
    },
  },
  {
    name: 'cap',
    draw(cell, view) {
      // A dome with a brim, not a slab. The first version was two stacked rectangles in the
      // accent with no outline, and on a head it read as a block of colour hovering above
      // somebody rather than as a hat — the one accessory big enough that its silhouette is
      // the whole of what it says.
      ellipse(cell, CX, HEAD_TOP + 6, 8, 6, 'accent', HEAD_TOP - 1, HEAD_TOP + 6)
      rect(cell, CX - 8, HEAD_TOP + 5, 17, 2, 'topShade')
      // A crown seam, so the dome has a top rather than only an edge.
      rect(cell, CX - 1, HEAD_TOP - 1, 2, 2, 'topShade')

      // The peak, which is the only part with a direction — so it disappears from behind.
      if (view === 'down') rect(cell, CX - 7, HEAD_TOP + 7, 15, 2, 'topShade')
      if (view === 'side') rect(cell, CX + 2, HEAD_TOP + 7, 11, 2, 'topShade')
    },
  },
  {
    name: 'scarf',
    draw(cell, view) {
      rect(cell, CX - 6, NECK_Y - 1, 13, 4, 'accent')
      rect(cell, CX - 6, NECK_Y + 1, 13, 1, 'topShade')
      if (view !== 'up') rect(cell, CX - 4, NECK_Y + 3, 3, 8, 'accent')
    },
  },
  {
    name: 'watch',
    draw(cell, view) {
      // The quietest option there is: three pixels on one wrist. Someone in the cast has to
      // be the person you notice for their silhouette rather than for what they are wearing.
      const x = view === 'side' ? CX + 3 : CX - 10
      rect(cell, x, WRIST_Y, 4, 2, 'accent')
    },
  },
]

// =========================================================================
// Out to sheets
// =========================================================================

function sheet(shapes, height, origin) {
  const width = CELL_W * VIEWS.length
  const total = height * shapes.length
  const pixels = Buffer.alloc(width * total * 4)

  shapes.forEach((row_, row) => {
    void row_
    VIEWS.forEach((view, column) => {
      const cell = band(height, origin)
      shapes[row].draw(cell, view)

      for (let y = 0; y < height; y += 1) {
        for (let x = 0; x < CELL_W; x += 1) {
          const slot = cell.rows[y][x]
          if (slot === null) continue
          const value = Number.parseInt(SENTINELS[slot].slice(1), 16)
          const at = ((row * height + y) * width + column * CELL_W + x) * 4
          pixels[at] = (value >> 16) & 0xff
          pixels[at + 1] = (value >> 8) & 0xff
          pixels[at + 2] = value & 0xff
          pixels[at + 3] = 255
        }
      }
    })
  })

  return { width, height: total, pixels }
}

if (import.meta.url === `file://${process.argv[1]}`) {
  mkdirSync(new URL('../art/library/', import.meta.url), { recursive: true })

  for (const [file, shapes, height, offset] of [
    ['hair.png', HAIR, 24, 0],
    ['outfits.png', OUTFITS, OUTFIT_HEIGHT, OUTFIT_BAND],
    ['accessories.png', ACCESSORIES, ACCESSORY_HEIGHT, ACCESSORY_BAND],
  ]) {
    const image = sheet(shapes, height, offset)
    writeFileSync(
      new URL(`../art/library/${file}`, import.meta.url),
      writePng(image.width, image.height, image.pixels),
    )
    console.log(`art/library/${file} — ${shapes.length} shapes × ${VIEWS.length} views`)
  }
}
