import { readFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { describe, expect, it } from 'vitest'

// @ts-expect-error — dev-only script, plain JS, not part of the bundle's type graph.
import { hexAt, readPng, writePng } from '../scripts/png.mjs'
// @ts-expect-error — same.
import { ConversionError, cellsFrom, gridFromPixels } from '../scripts/grid-from-png.mjs'
// @ts-expect-error — same.
import { extract } from '../scripts/palette-from-candidate.mjs'

import {
  CAST_PALETTE,
  CELL_HEIGHT,
  CELL_WIDTH,
  EMPTY,
  GLYPH_SLOTS,
  SENTINELS,
  SENTINEL_GLYPHS,
  VIEWS,
  WALK_CYCLE,
} from '../src/render/cast/slots'
import { RESERVED_BEAM } from '../src/design/tokens'

/**
 * The seam between how the cast is authored and how it is committed.
 *
 * Art is drawn as PNG, because that is what a pixel artist works in and what the 33 approved
 * candidates already are. It is committed as ASCII, because a one-pixel change should show up
 * in review as one changed character. Everything below is about that conversion being exact:
 * a converter that snapped a near-miss to the nearest slot would produce art that is subtly
 * and unfixably wrong, and would do it without saying so.
 */

/**
 * The approved casting board, which lives outside the client and is read rather than shipped.
 *
 * Resolved from the test file's own location rather than from the working directory, so the
 * suite does not depend on being run from `frontend/`.
 */
const CANDIDATES = resolve(
  dirname(fileURLToPath(import.meta.url)),
  '../../docs/assets/company-os-visual-redesign/roster-characters',
)

function candidate(name: string): Buffer {
  return readFileSync(join(CANDIDATES, name))
}

/** Build an RGBA buffer from a grid of glyphs, painting each slot its sentinel. */
function pixelsFrom(rows: string[]): Buffer {
  const height = rows.length
  const width = rows[0].length
  const pixels = Buffer.alloc(width * height * 4)

  rows.forEach((row, y) => {
    ;[...row].forEach((glyph, x) => {
      const at = (y * width + x) * 4
      if (glyph === EMPTY) return
      const hex = SENTINELS[GLYPH_SLOTS[glyph]]
      const value = Number.parseInt(hex.slice(1), 16)
      pixels[at] = (value >> 16) & 0xff
      pixels[at + 1] = (value >> 8) & 0xff
      pixels[at + 2] = value & 0xff
      pixels[at + 3] = 255
    })
  })

  return pixels
}

describe('the slot contract', () => {
  it('gives every slot a sentinel no other slot shares', () => {
    const used = Object.values(SENTINELS)
    expect(new Set(used).size).toBe(used.length)
  })

  it('maps every glyph to a slot and every sentinel back to its glyph', () => {
    for (const [glyph, slot] of Object.entries(GLYPH_SLOTS)) {
      expect(SENTINELS[slot], `${glyph} → ${slot}`).toMatch(/^#[0-9a-f]{6}$/)
      expect(SENTINEL_GLYPHS[SENTINELS[slot]]).toBe(glyph)
    }
  })

  it('spends the reserved amber on no slot', () => {
    // A sentinel is never seen, but a sentinel that *was* the beam would mean a slot whose
    // authored colour and the product's one reserved signal are indistinguishable in the
    // source art.
    expect(Object.values(SENTINELS)).not.toContain(RESERVED_BEAM)
  })

  it('keeps the sentinels far enough apart to be unmistakable by eye', () => {
    // They are picked with an eyedropper by a person. Two a few values apart would let a
    // wrong pick produce art that is subtly and permanently miscast.
    const values = Object.values(SENTINELS).map((hex) => Number.parseInt(hex.slice(1), 16))
    for (let i = 0; i < values.length; i += 1) {
      for (let j = i + 1; j < values.length; j += 1) {
        const a = values[i]
        const b = values[j]
        const distance =
          Math.abs(((a >> 16) & 0xff) - ((b >> 16) & 0xff)) +
          Math.abs(((a >> 8) & 0xff) - ((b >> 8) & 0xff)) +
          Math.abs((a & 0xff) - (b & 0xff))
        expect(distance).toBeGreaterThanOrEqual(51)
      }
    }
  })

  it('authors three views and mirrors the fourth', () => {
    expect(VIEWS).toEqual(['down', 'up', 'side'])
    // `left` is absent on purpose: two profiles somebody has to keep pixel-identical by hand
    // are two profiles that drift.
    expect(VIEWS).not.toContain('left')
  })

  it('walks through four frames without standing still mid-stride', () => {
    expect(WALK_CYCLE).toHaveLength(4)
    expect(WALK_CYCLE).not.toContain(0)
    expect(new Set(WALK_CYCLE).size).toBeGreaterThan(1)
  })
})

describe('the PNG codec', () => {
  it('reads an indexed candidate as RGBA', () => {
    const image = readPng(candidate('dir_sales-a.png'))
    expect(image.width).toBe(CELL_WIDTH)
    expect(image.height).toBe(CELL_HEIGHT)
    expect(image.pixels).toHaveLength(CELL_WIDTH * CELL_HEIGHT * 4)
  })

  it('round-trips its own output', () => {
    const image = readPng(candidate('dir_sales-a.png'))
    const again = readPng(writePng(image.width, image.height, image.pixels))

    expect(again.width).toBe(image.width)
    expect(again.height).toBe(image.height)
    expect(Buffer.compare(again.pixels, image.pixels)).toBe(0)
  })

  it('refuses a file that is not a PNG', () => {
    expect(() => readPng(Buffer.from('not a png at all, really'))).toThrow(/not a PNG/)
  })
})

describe('converting art to grids', () => {
  const sample = [
    '.kkk.',
    'kshsk',
    '.tTt.',
    '.pPp.',
    '.b.b.',
  ]

  it('turns sentinels into glyphs, pixel for pixel', () => {
    const pixels = pixelsFrom(sample)
    expect(gridFromPixels(5, 5, pixels)).toEqual(sample)
  })

  it('round-trips a grid through a real PNG', () => {
    // The property the whole seam rests on: what an artist saves is what gets committed.
    const pixels = pixelsFrom(sample)
    const encoded = writePng(5, 5, pixels)
    const decoded = readPng(encoded)
    expect(gridFromPixels(decoded.width, decoded.height, decoded.pixels)).toEqual(sample)
  })

  it('names the pixel when a colour is not a sentinel', () => {
    const pixels = pixelsFrom(sample)
    // One channel off by one — the shape a lossy save or a colour-profile conversion takes.
    pixels[(1 * 5 + 2) * 4] += 1

    expect(() => gridFromPixels(5, 5, pixels, 'sheet')).toThrow(ConversionError)
    expect(() => gridFromPixels(5, 5, pixels, 'sheet')).toThrow(/pixel 2,1/)
  })

  it('refuses partial alpha rather than rounding it', () => {
    // No smoothing, anywhere in the pipeline — R13 starts at the source art.
    const pixels = pixelsFrom(sample)
    pixels[(1 * 5 + 1) * 4 + 3] = 128

    expect(() => gridFromPixels(5, 5, pixels, 'sheet')).toThrow(/opaque/)
  })

  it('refuses a sheet that does not divide into whole cells', () => {
    const image = { width: 5, height: 5, pixels: pixelsFrom(sample) }
    expect(() => cellsFrom(image, 2, 1, 'sheet')).toThrow(ConversionError)
  })

  it('splits a sheet left to right, top to bottom', () => {
    const rows = ['ss..', 'ss..', '..hh', '..hh']
    const image = { width: 4, height: 4, pixels: pixelsFrom(rows) }
    const cells = cellsFrom(image, 2, 2, 'sheet')

    expect(cells).toHaveLength(4)
    expect(cells[0]).toEqual(['ss', 'ss'])
    expect(cells[1]).toEqual(['..', '..'])
    expect(cells[2]).toEqual(['..', '..'])
    expect(cells[3]).toEqual(['hh', 'hh'])
  })

  it('emits only glyphs the cast palette knows', () => {
    for (const row of gridFromPixels(5, 5, pixelsFrom(sample))) {
      for (const glyph of row) expect(CAST_PALETTE.has(glyph)).toBe(true)
    }
  })
})

describe('extracting a palette from a candidate', () => {
  const ROSTER = [
    'you',
    'dir_sales',
    'stf_order',
    'stf_field',
    'dir_admin',
    'stf_ap',
    'stf_buyer',
    'dir_cs',
    'stf_cs',
    'dir_hr',
    'stf_rec',
  ]
  const ALL = ROSTER.flatMap((id) => ['a', 'b', 'c'].map((letter) => `${id}-${letter}`))

  it('extracts six slots from every one of the thirty-three', () => {
    expect(ALL).toHaveLength(33)

    for (const name of ALL) {
      const skin = extract(readPng(candidate(`${name}.png`)), name)
      for (const slot of ['skin', 'hair', 'top', 'legs', 'shoes', 'accent']) {
        expect(skin[slot], `${name}.${slot}`).toMatch(/^#[0-9a-f]{6}$/)
      }
    }
  })

  it('only ever names a colour the candidate itself contains', () => {
    // The reason this is a script rather than thirty-three hand-typed palettes: a transcribed
    // hex digit is a colour nobody notices is wrong.
    for (const name of ALL.slice(0, 8)) {
      const image = readPng(candidate(`${name}.png`))
      const present = new Set<string>()
      for (let i = 0; i < image.width * image.height; i += 1) {
        if (image.pixels[i * 4 + 3] > 0) present.add(hexAt(image.pixels, i))
      }

      for (const [slot, hex] of Object.entries(extract(image, name))) {
        expect(present.has(hex as string), `${name}.${slot} = ${hex}`).toBe(true)
      }
    }
  })

  it('never casts anyone in the reserved amber', () => {
    for (const name of ALL) {
      const skin = extract(readPng(candidate(`${name}.png`)), name)
      expect(Object.values(skin), name).not.toContain(RESERVED_BEAM)
    }
  })

  it('finds skin at the face rather than in a beard or a fringe', () => {
    // The guard is warmth rather than lightness: the darkest skin tone in this roster and the
    // lightest hair colour are within a few points of each other, so any lightness threshold
    // either casts a shadowed jaw as skin or refuses a real deep skin tone.
    const warmth = (hex: string): number => {
      const value = Number.parseInt(hex.slice(1), 16)
      return ((value >> 16) & 0xff) / Math.max(1, value & 0xff)
    }

    for (const name of ALL) {
      const { skin } = extract(readPng(candidate(`${name}.png`)), name)
      expect(warmth(skin), `${name} skin ${skin}`).toBeGreaterThan(1.8)
    }
  })

  it('never casts the outline as hair', () => {
    // Two candidates have hair short enough that the crown is mostly silhouette. Excluding
    // only the single commonest edge colour left the second outline value to win those, and
    // they came out with 鸽蓝 hair.
    for (const name of ALL) {
      const { hair } = extract(readPng(candidate(`${name}.png`)), name)
      expect(hair, `${name}`).not.toBe('#253147')
    }
  })
})
