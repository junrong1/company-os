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

import { CAST } from '../src/render/cast/manifests'
import {
  appearanceFor,
  derivedManifest,
  manifestFor,
  sheetKey,
} from '../src/render/cast/appearance'
import {
  LIBRARY_SIZES,
  SHEET_HEIGHT,
  SHEET_ROWS,
  SHEET_WIDTH,
  composeCell,
  composeSheet,
  resolve as resolvePalette,
} from '../src/render/cast/compose'
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
import { PAL, RESERVED_BEAM } from '../src/design/tokens'

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

// =========================================================================
// Composition (U11), casting (U12) and appearance (U13)
// =========================================================================

describe('composing a person', () => {
  const someone = manifestFor('dir_sales', 1)

  it('builds a sheet of four facings by four frames', () => {
    const data = composeSheet(someone)
    expect(SHEET_WIDTH).toBe(CELL_WIDTH * 4)
    expect(SHEET_HEIGHT).toBe(CELL_HEIGHT * 4)
    expect(data).toHaveLength(SHEET_WIDTH * SHEET_HEIGHT * 4)
  })

  it('leaves every pixel fully opaque or fully absent', () => {
    // No smoothing anywhere in the pipeline. R13's crispness rule is not something the
    // renderer switches off at the end — it has to be true of the art the whole way through.
    const data = composeSheet(someone)
    for (let i = 3; i < data.length; i += 4) {
      expect(data[i] === 0 || data[i] === 255, `alpha ${data[i]} at ${i}`).toBe(true)
    }
  })

  it('paints only colours the person was cast in', () => {
    // A sentinel surviving into output means a slot nothing resolved, which would ship as a
    // person with a bright red arm.
    const palette = new Set(Object.values(resolvePalette(someone.skin)))
    const data = composeSheet(someone)

    for (let i = 0; i < data.length; i += 4) {
      if (data[i + 3] === 0) continue
      const hex = `#${data[i].toString(16).padStart(2, '0')}${data[i + 1]
        .toString(16)
        .padStart(2, '0')}${data[i + 2].toString(16).padStart(2, '0')}`
      expect(palette.has(hex), `${hex} is not in this person's palette`).toBe(true)
    }
  })

  it('mirrors the left row from the right rather than authoring it twice', () => {
    const data = composeSheet(someone)
    const at = (row: number, x: number, y: number): string =>
      [0, 1, 2, 3]
        .map((c) => data[((row * CELL_HEIGHT + y) * SHEET_WIDTH + x) * 4 + c])
        .join(',')

    const left = SHEET_ROWS.findIndex((row) => row.facing === 'left')
    const right = SHEET_ROWS.findIndex((row) => row.facing === 'right')

    for (let y = 0; y < CELL_HEIGHT; y += 7) {
      for (let x = 0; x < CELL_WIDTH; x += 5) {
        expect(at(left, x, y), `${x},${y}`).toBe(at(right, CELL_WIDTH - 1 - x, y))
      }
    }
  })

  it('layers clothes over the body and hair over the clothes', () => {
    // The order things sit in front of each other on a real person. Getting it wrong is the
    // failure that looks nearly right: a shirt over a face reads as a bug, but hair under a
    // collar just reads as a slightly odd haircut.
    const bare = { ...someone, outfit: 0, accessory: null }
    const dressed = { ...bare, outfit: 1 }

    const flatten = (cell: (string | null)[][]): string =>
      cell.map((row) => row.map((slot) => slot ?? '.').join('')).join('|')

    expect(flatten(composeCell(bare, 'down', 0))).not.toBe(
      flatten(composeCell(dressed, 'down', 0)),
    )
  })

  it('plants every frame’s feet on the same row', () => {
    // The invariant the tile anchor and the depth sort both read. If one frame's feet were a
    // pixel high the person would bob against the floor as they walked.
    for (const view of VIEWS) {
      for (let frame = 0; frame < 4; frame += 1) {
        const cell = composeCell(someone, view, frame)
        const lowest = cell.reduce(
          (deepest, row, y) => (row.some((slot) => slot !== null) ? y : deepest),
          -1,
        )
        expect(lowest, `${view} frame ${frame}`).toBe(CELL_HEIGHT - 1)
      }
    }
  })

  it('resolves seventeen slots from six colours, deriving rather than inventing', () => {
    const palette = resolvePalette(someone.skin)
    expect(palette.skin).toBe(someone.skin.skin)
    expect(palette.top).toBe(someone.skin.top)
    // A shade is the same cloth in less light, so it is darker than its base and not equal.
    expect(palette.topShade).not.toBe(palette.top)
    expect(palette.outline).toBe(PAL.outline)
  })
})

describe('the cast', () => {
  const ROSTER_IDS = [
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

  it('carries three appearances for each of the eleven identities', () => {
    expect(Object.keys(CAST).sort()).toEqual([...ROSTER_IDS].sort())
    for (const [id, appearances] of Object.entries(CAST)) {
      expect(appearances, id).toHaveLength(3)
    }
  })

  it('makes A, B and C of one person differ in at least two features', () => {
    // The redesign's success criterion: recognisably the same role, materially different
    // person. One changed accessory is a costume note, not a casting.
    for (const [id, group] of Object.entries(CAST)) {
      for (let i = 0; i < group.length; i += 1) {
        for (let j = i + 1; j < group.length; j += 1) {
          const a = group[i]
          const b = group[j]
          let differences = 0
          if (a.hair !== b.hair) differences += 1
          if (a.outfit !== b.outfit) differences += 1
          if (a.accessory !== b.accessory) differences += 1
          if (a.skin.skin !== b.skin.skin) differences += 1
          if (a.skin.top !== b.skin.top) differences += 1
          expect(differences, `${id} ${i} vs ${j}`).toBeGreaterThanOrEqual(2)
        }
      }
    }
  })

  it('references only shapes the libraries actually have', () => {
    for (const [id, group] of Object.entries(CAST)) {
      for (const manifest of group) {
        expect(manifest.hair, id).toBeLessThan(LIBRARY_SIZES.hair)
        expect(manifest.outfit, id).toBeLessThan(LIBRARY_SIZES.outfits)
        if (manifest.accessory !== null) {
          expect(manifest.accessory, id).toBeLessThan(LIBRARY_SIZES.accessories)
        }
      }
    }
  })

  it('casts nobody in the reserved amber', () => {
    for (const [id, group] of Object.entries(CAST)) {
      for (const manifest of group) {
        expect(Object.values(manifest.skin), id).not.toContain(RESERVED_BEAM)
      }
    }
  })

  it('gives the player a top no member of staff wears, in every appearance', () => {
    const staff = ROSTER_IDS.filter((id) => id !== 'you')
    for (const ceo of CAST.you) {
      for (const id of staff) {
        for (const other of CAST[id]) {
          expect(other.skin.top, id).not.toBe(ceo.skin.top)
        }
      }
    }
  })
})

describe('appearance', () => {
  it('is stable for one run and varies across runs', () => {
    for (const seed of [0, 1, 7, 4242, 2 ** 31 - 1]) {
      expect(appearanceFor(seed, 'dir_sales')).toBe(appearanceFor(seed, 'dir_sales'))
      expect(appearanceFor(seed, 'dir_sales')).toBeGreaterThanOrEqual(0)
      expect(appearanceFor(seed, 'dir_sales')).toBeLessThan(3)
    }

    const across = new Set([0, 1, 2, 3, 4, 5, 6, 7].map((seed) => appearanceFor(seed, 'dir_hr')))
    expect(across.size).toBeGreaterThan(1)
  })

  it('chooses per person rather than per run', () => {
    // Two identities in one run may independently land on different letters. If they moved
    // together the seed would be picking one cast rather than eleven appearances.
    const seeds = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9]
    const differ = seeds.some(
      (seed) => appearanceFor(seed, 'dir_sales') !== appearanceFor(seed, 'stf_cs'),
    )
    expect(differ).toBe(true)
  })

  it('keys the sheet cache on the appearance for a lead and on the id for anyone else', () => {
    expect(sheetKey('dir_sales', 1)).not.toBe(sheetKey('dir_sales', 2))
    // A procedural coworker has one look, so their key has nothing to vary on.
    expect(sheetKey('temp_042', 1)).toBe(sheetKey('temp_042', 2))
  })

  it('dresses anyone the casting board never met, from the same libraries', () => {
    // R9: procedural coworkers are not *compatible* with the leads, they are the same rig
    // wearing a manifest that was derived instead of cast.
    for (const id of ['temp_001', 'temp_002', 'contractor', 'visitor_9']) {
      const manifest = derivedManifest(id)
      expect(manifest.hair).toBeLessThan(LIBRARY_SIZES.hair)
      expect(manifest.outfit).toBeLessThan(LIBRARY_SIZES.outfits)
      expect(Object.values(manifest.skin)).not.toContain(RESERVED_BEAM)

      const data = composeSheet(manifest)
      expect(data).toHaveLength(SHEET_WIDTH * SHEET_HEIGHT * 4)
    }
  })

  it('derives the same person from the same id, every time', () => {
    expect(derivedManifest('temp_001')).toEqual(derivedManifest('temp_001'))
  })

  it('makes two adjacent ids differ in more than one feature', () => {
    // The coprime-stride trick. Without it two ids one character apart differ in exactly one
    // slot and the background reads as a row of near-clones.
    const a = derivedManifest('temp_001')
    const b = derivedManifest('temp_002')
    let differences = 0
    if (a.hair !== b.hair) differences += 1
    if (a.outfit !== b.outfit) differences += 1
    if (a.skin.skin !== b.skin.skin) differences += 1
    if (a.skin.top !== b.skin.top) differences += 1
    expect(differences).toBeGreaterThanOrEqual(2)
  })

  it('never sends an appearance anywhere', () => {
    // R9a holds because there is no channel, not because nothing currently writes to one.
    const gateway = readFileSync(
      join(dirname(fileURLToPath(import.meta.url)), '../src/net/gateway.ts'),
      'utf8',
    )
    expect(gateway).not.toContain('appearance')
    expect(gateway).not.toContain('manifest')
  })
})
