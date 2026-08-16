/**
 * Turning a rig, a manifest and six colours into one person's sprite sheet.
 *
 * Pure, and deliberately so. Everything to the left of this module is data — grids of glyphs
 * and tables of hex — and everything to the right is a canvas. Keeping the seam here means
 * the suites can test what a person *looks like* without a canvas to draw on, which matters
 * because jsdom does not have one and installing a native canvas to check that a jacket
 * overwrites a shirt would be testing the canvas.
 *
 * The output is a flat RGBA buffer written with one `putImageData`. A 192×256 sheet is 49,152
 * pixels against the prototype's 1,920, and painting that with one `fillRect` per pixel per
 * person is fifty thousand canvas calls where one write will do.
 */

import { PAL } from '../../design/tokens'
import { ACCESSORIES, ACCESSORIES_BAND } from './accessories'
import { RIG } from './grammar'
import { HAIR, HAIR_BAND } from './hair'
import { OUTFITS, OUTFITS_BAND } from './outfits'
import {
  CELL_FRAMES,
  CELL_HEIGHT,
  CELL_WIDTH,
  EMPTY,
  GLYPH_SLOTS,
  type Skin,
  type Slot,
  VIEWS,
  type View,
} from './slots'

/** Which facing each row of a sheet carries, and which authored view draws it. */
export const SHEET_ROWS: { facing: string; view: View; mirrored: boolean }[] = [
  { facing: 'down', view: 'down', mirrored: false },
  { facing: 'up', view: 'up', mirrored: false },
  // Mirrored from `side` rather than authored, so two profiles cannot drift into being two
  // grids somebody has to keep pixel-identical by hand.
  { facing: 'left', view: 'side', mirrored: true },
  { facing: 'right', view: 'side', mirrored: false },
]

export const SHEET_WIDTH = CELL_WIDTH * CELL_FRAMES
export const SHEET_HEIGHT = CELL_HEIGHT * SHEET_ROWS.length

/** Who a person is, in the four choices and six colours that make them themselves. */
export interface Manifest {
  hair: number
  outfit: number
  /** `null` for the one person in the cast who wears nothing extra. */
  accessory: number | null
  skin: Skin
}

// =========================================================================
// Resolving six colours into seventeen
// =========================================================================

function shift(hex: string, factor: number): string {
  const value = Number.parseInt(hex.slice(1), 16)
  const channel = (bits: number): string => {
    const raw = Math.round(Math.min(255, Math.max(0, ((value >> bits) & 0xff) * factor)))
    return raw.toString(16).padStart(2, '0')
  }
  return `#${channel(16)}${channel(8)}${channel(0)}`
}

/**
 * A person's full slot palette, derived from the six they were cast with.
 *
 * Derived rather than authored, which is what stops a person ending up with a highlight that
 * does not belong to their own skin. Casting is choosing six colours; the other eleven are
 * consequences, and two of them are shared by everyone because an eye and an outline are
 * structure rather than material.
 *
 * The factors are the art direction's "two or three value groups per material" as arithmetic:
 * a shade close enough to read as the same cloth, a line dark enough to separate it from what
 * is behind it.
 */
export function resolve(skin: Skin): Record<Slot, string> {
  return {
    skin: skin.skin,
    skinShade: shift(skin.skin, 0.84),
    skinLine: shift(skin.skin, 0.66),
    hair: skin.hair,
    hairLight: shift(skin.hair, 1.28),
    hairDark: shift(skin.hair, 0.7),
    top: skin.top,
    topShade: shift(skin.top, 0.82),
    topLine: shift(skin.top, 0.62),
    legs: skin.legs,
    legsShade: shift(skin.legs, 0.78),
    shoes: skin.shoes,
    shoesShade: shift(skin.shoes, 0.72),
    outline: PAL.outline,
    eye: PAL.outline,
    mouth: shift(skin.skin, 0.58),
    accent: skin.accent,
  }
}

// =========================================================================
// Composition
// =========================================================================

/** Lay one grid's glyphs over a cell, starting at `origin`. Transparent glyphs skip. */
function overlay(cell: (Slot | null)[][], rows: readonly string[], origin: number): void {
  rows.forEach((row, y) => {
    const target = cell[y + origin]
    if (target === undefined) return
    ;[...row].forEach((glyph, x) => {
      if (glyph === EMPTY) return
      target[x] = GLYPH_SLOTS[glyph]
    })
  })
}

/**
 * One cell of one person: body, then clothes, then hair, then whatever they carry.
 *
 * The order is the order things sit in front of each other on a real person, and getting it
 * wrong is the failure that looks nearly right — a shirt over a face reads as a bug, but hair
 * *under* a collar just reads as a slightly odd haircut.
 */
export function composeCell(manifest: Manifest, view: View, frame: number): (Slot | null)[][] {
  const cell: (Slot | null)[][] = Array.from({ length: CELL_HEIGHT }, () =>
    Array.from({ length: CELL_WIDTH }, () => null),
  )

  overlay(cell, RIG[view][frame], 0)
  overlay(cell, OUTFITS[manifest.outfit][view], OUTFITS_BAND)
  overlay(cell, HAIR[manifest.hair][view], HAIR_BAND)
  if (manifest.accessory !== null) {
    overlay(cell, ACCESSORIES[manifest.accessory][view], ACCESSORIES_BAND)
  }

  return cell
}

/**
 * The whole sheet: four facings down, four frames across, as flat RGBA.
 *
 * Returned as a buffer rather than drawn, so the caller decides whether it becomes an
 * `ImageData` on a canvas or an array a test walks.
 */
export function composeSheet(manifest: Manifest): Uint8ClampedArray {
  const palette = resolve(manifest.skin)
  const data = new Uint8ClampedArray(SHEET_WIDTH * SHEET_HEIGHT * 4)

  SHEET_ROWS.forEach(({ view, mirrored }, row) => {
    for (let frame = 0; frame < CELL_FRAMES; frame += 1) {
      const cell = composeCell(manifest, view, frame)

      for (let y = 0; y < CELL_HEIGHT; y += 1) {
        for (let x = 0; x < CELL_WIDTH; x += 1) {
          const slot = cell[y][mirrored ? CELL_WIDTH - 1 - x : x]
          if (slot === null) continue

          const hex = Number.parseInt(palette[slot].slice(1), 16)
          const at = ((row * CELL_HEIGHT + y) * SHEET_WIDTH + frame * CELL_WIDTH + x) * 4
          data[at] = (hex >> 16) & 0xff
          data[at + 1] = (hex >> 8) & 0xff
          data[at + 2] = hex & 0xff
          // Fully opaque or fully absent, never in between. Partial alpha anywhere in this
          // pipeline is smoothing, and R13 admits none.
          data[at + 3] = 255
        }
      }
    }
  })

  return data
}

/** How many shapes each library offers, for the manifest rule and for the suites. */
export const LIBRARY_SIZES = {
  hair: HAIR.length,
  outfits: OUTFITS.length,
  accessories: ACCESSORIES.length,
  views: VIEWS.length,
} as const
