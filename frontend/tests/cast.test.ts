import { readFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { describe, expect, it } from 'vitest'

// @ts-expect-error — dev-only scripts, plain JS, not part of the bundle's type graph.
import { hexAt, readPng, writePng } from '../scripts/png.mjs'
// @ts-expect-error — same.
import { extract } from '../scripts/palette-from-candidate.mjs'
// @ts-expect-error — same.
import { CELL_H, CELL_W, FACINGS, FRAMES, sheetFor } from '../scripts/make-cast-atlas.mjs'

import {
  ATLAS,
  CELL_HEIGHT,
  CELL_WIDTH,
  SHEET_FACINGS,
  SHEET_FRAMES,
} from '../src/render/cast/atlas-index'
import {
  APPEARANCES,
  IDENTITIES,
  WALK_CYCLE,
  appearanceFor,
  candidateFor,
  cellRect,
  skinFor,
} from '../src/render/cast/atlas'
import { RESERVED_BEAM } from '../src/design/tokens'

/**
 * The cast, and the pipeline that gives the casting board its directions.
 *
 * The board's 33 candidates *are* the shipped art. Nothing here composes a person out of
 * parts, because an earlier version did and produced people visibly cruder than the art that
 * already existed — at 22 pixels wide a face is four pixels, and a generator does not reach
 * what a person drew.
 *
 * So what is asserted is that the derivations preserve the art: the silhouette does not move,
 * the feet stay planted, no colour is invented, and every frame of every direction is made of
 * pixels the board approved.
 */

const CANDIDATES = resolve(
  dirname(fileURLToPath(import.meta.url)),
  '../../docs/assets/company-os-visual-redesign/roster-characters',
)

function candidate(name: string): Buffer {
  return readFileSync(join(CANDIDATES, `${name}.png`))
}

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

/** Every opaque colour in a buffer, as `#rrggbb`. */
function coloursOf(pixels: Uint8ClampedArray | Buffer, count: number): Set<string> {
  const found = new Set<string>()
  for (let i = 0; i < count; i += 1) {
    if (pixels[i * 4 + 3] === 0) continue
    found.add(hexAt(pixels, i))
  }
  return found
}

/** The opaque bounding box of one cell inside a sheet. */
function boundsOf(
  sheet: Uint8ClampedArray,
  stride: number,
  ox: number,
  oy: number,
): { minX: number; maxX: number; minY: number; maxY: number } {
  let minX = CELL_W
  let maxX = -1
  let minY = CELL_H
  let maxY = -1
  for (let y = 0; y < CELL_H; y += 1) {
    for (let x = 0; x < CELL_W; x += 1) {
      if (sheet[((oy + y) * stride + ox + x) * 4 + 3] === 0) continue
      minX = Math.min(minX, x)
      maxX = Math.max(maxX, x)
      minY = Math.min(minY, y)
      maxY = Math.max(maxY, y)
    }
  }
  return { minX, maxX, minY, maxY }
}

describe('the PNG codec', () => {
  it('reads an indexed candidate as RGBA at the production canvas size', () => {
    const image = readPng(candidate('dir_sales-a'))
    expect(image.width).toBe(CELL_WIDTH)
    expect(image.height).toBe(CELL_HEIGHT)
    expect(image.pixels).toHaveLength(CELL_WIDTH * CELL_HEIGHT * 4)
  })

  it('round-trips its own output', () => {
    const image = readPng(candidate('dir_sales-a'))
    const again = readPng(writePng(image.width, image.height, image.pixels))
    expect(Buffer.compare(again.pixels, image.pixels)).toBe(0)
  })

  it('refuses a file that is not a PNG', () => {
    expect(() => readPng(Buffer.from('not a png at all, really'))).toThrow(/not a PNG/)
  })
})

describe('every candidate on the board', () => {
  it('is exactly the production canvas, with transparent corners', () => {
    for (const name of ALL) {
      const image = readPng(candidate(name))
      expect(image.width, name).toBe(48)
      expect(image.height, name).toBe(64)
      for (const [x, y] of [
        [0, 0],
        [47, 0],
        [0, 63],
        [47, 63],
      ]) {
        expect(image.pixels[(y * 48 + x) * 4 + 3], `${name} corner ${x},${y}`).toBe(0)
      }
    }
  })

  it('has no partial alpha anywhere', () => {
    // R13's crispness rule starts at the source art. A resize that resampled rather than
    // scaled would show up here and nowhere else until it was on screen.
    for (const name of ALL) {
      const { pixels } = readPng(candidate(name))
      for (let i = 3; i < pixels.length; i += 4) {
        expect(pixels[i] === 0 || pixels[i] === 255, `${name} alpha ${pixels[i]}`).toBe(true)
      }
    }
  })

  it('never uses the reserved amber', () => {
    for (const name of ALL) {
      const image = readPng(candidate(name))
      expect([...coloursOf(image.pixels, 48 * 64)], name).not.toContain(RESERVED_BEAM)
    }
  })
})

describe('deriving the directions', () => {
  const image = readPng(candidate('dir_hr-a'))
  const front = new Uint8ClampedArray(image.pixels)
  const skin = extract(image, 'dir_hr-a')
  const sheet = sheetFor(front, skin)
  const stride = CELL_W * FRAMES

  const cellAt = (facing: number, frame: number) =>
    boundsOf(sheet, stride, frame * CELL_W, facing * CELL_H)

  it('builds four facings of four frames', () => {
    expect(sheet).toHaveLength(stride * CELL_H * FACINGS * 4)
    expect(FACINGS).toBe(SHEET_FACINGS)
    expect(FRAMES).toBe(SHEET_FRAMES)
  })

  it('leaves the front idle exactly as it was drawn', () => {
    // The pose a viewer sees almost all the time is the approved art, pixel for pixel. Any
    // transformation of it would be a transformation of somebody's approved face.
    for (let y = 0; y < CELL_H; y += 1) {
      for (let x = 0; x < CELL_W; x += 1) {
        const from = (y * CELL_W + x) * 4
        const to = (y * stride + x) * 4
        for (let c = 0; c < 4; c += 1) {
          expect(sheet[to + c], `${x},${y} channel ${c}`).toBe(front[from + c])
        }
      }
    }
  })

  it('mirrors the left facing from the right rather than inventing one', () => {
    for (let y = 0; y < CELL_H; y += 4) {
      for (let x = 0; x < CELL_W; x += 3) {
        const right = ((3 * CELL_H + y) * stride + x) * 4
        const left = ((2 * CELL_H + y) * stride + (CELL_W - 1 - x)) * 4
        expect(sheet[left + 3], `${x},${y}`).toBe(sheet[right + 3])
        if (sheet[right + 3] === 0) continue
        expect(sheet[left], `${x},${y}`).toBe(sheet[right])
      }
    }
  })

  it('turns the face away without moving the silhouette', () => {
    // The turn reads because a viewer recognises a person by their outline long before they
    // resolve a face at this size. Moving the outline would make it somebody else.
    expect(cellAt(1, 0)).toEqual(cellAt(0, 0))
  })

  it('leaves no skin behind in a head that has turned away', () => {
    // The head fills with hair. An earlier version kept a patch of skin for a neck and it read
    // as a hole punched through it, because at this size the jaw and the neck are three rows.
    const { minY } = cellAt(1, 0)
    let skinPixels = 0
    for (let y = minY + 3; y < minY + 12; y += 1) {
      for (let x = 15; x < 33; x += 1) {
        const at = (1 * CELL_H + y) * stride + x
        if (sheet[at * 4 + 3] === 0) continue
        if (hexAt(sheet, at) === skin.skin.toLowerCase()) skinPixels += 1
      }
    }
    expect(skinPixels).toBe(0)
  })

  it('plants every frame of every facing on the same row', () => {
    // The invariant the tile anchor and the depth sort both read. A frame whose feet were a
    // pixel high would bob the person against the floor as they walked.
    for (let facing = 0; facing < FACINGS; facing += 1) {
      for (let frame = 0; frame < FRAMES; frame += 1) {
        expect(cellAt(facing, frame).maxY, `${facing}/${frame}`).toBe(cellAt(facing, 0).maxY)
      }
    }
  })

  it('makes the stride out of the person who is walking', () => {
    // The whole claim of this pipeline is that it adds directions and a gait and draws
    // nothing. The one derived value it is allowed is the darkened crown on the back of a
    // head, which is the only shade a turn needs that a front pose does not already carry.
    const approved = coloursOf(front, CELL_W * CELL_H)
    const drawn = coloursOf(sheet, sheet.length / 4)
    const invented = [...drawn].filter((hex) => !approved.has(hex))
    expect(invented.length, `invented ${invented.join(', ')}`).toBeLessThanOrEqual(2)
  })

  it('actually moves between frames', () => {
    // A "walk" whose frames are identical is a slide, and it would pass every assertion above.
    const signature = (frame: number): string => {
      let out = ''
      for (let y = 0; y < CELL_H; y += 1) {
        for (let x = 0; x < CELL_W; x += 1) {
          out += sheet[(y * stride + frame * CELL_W + x) * 4 + 3] === 0 ? '.' : '#'
        }
      }
      return out
    }
    expect(signature(0)).not.toBe(signature(1))
    expect(signature(1)).not.toBe(signature(3))
  })
})

describe('the atlas index', () => {
  it('covers every candidate on the board, one row each', () => {
    expect(Object.keys(ATLAS).sort()).toEqual([...ALL].sort())
    const rows = Object.values(ATLAS).map((entry) => entry.row)
    expect(new Set(rows).size).toBe(rows.length)
  })

  it('names only colours the candidate itself contains', () => {
    // The reason the palette is extracted rather than transcribed: a hand-typed hex digit is a
    // colour nobody notices is wrong.
    for (const name of ALL.slice(0, 8)) {
      const image = readPng(candidate(name))
      const present = coloursOf(image.pixels, 48 * 64)
      for (const [slot, hex] of Object.entries(ATLAS[name].skin)) {
        expect(present.has(hex), `${name}.${slot} = ${hex}`).toBe(true)
      }
    }
  })

  it('casts nobody in the reserved amber', () => {
    for (const [name, entry] of Object.entries(ATLAS)) {
      expect(Object.values(entry.skin), name).not.toContain(RESERVED_BEAM)
    }
  })

  it('finds skin at the face rather than in a beard or a fringe', () => {
    // The guard is warmth rather than lightness: the darkest skin in this roster and the
    // lightest hair sit within a few points of each other.
    const warmth = (hex: string): number => {
      const value = Number.parseInt(hex.slice(1), 16)
      return ((value >> 16) & 0xff) / Math.max(1, value & 0xff)
    }
    for (const [name, entry] of Object.entries(ATLAS)) {
      expect(warmth(entry.skin.skin), `${name} skin ${entry.skin.skin}`).toBeGreaterThan(1.8)
    }
  })

  it('never casts the outline as hair', () => {
    for (const [name, entry] of Object.entries(ATLAS)) {
      expect(entry.skin.hair, name).not.toBe('#253147')
    }
  })
})

describe('who somebody looks like', () => {
  it('covers the eleven identities the office can show', () => {
    expect(IDENTITIES).toHaveLength(11)
    expect([...IDENTITIES].sort()).toEqual([...ROSTER].sort())
  })

  it('is stable within a run and varies across runs', () => {
    for (const seed of [0, 1, 7, 4242, 2 ** 31 - 1]) {
      expect(appearanceFor(seed, 'dir_sales')).toBe(appearanceFor(seed, 'dir_sales'))
      expect(appearanceFor(seed, 'dir_sales')).toBeLessThan(APPEARANCES)
    }
    const across = new Set([0, 1, 2, 3, 4, 5, 6, 7].map((s) => appearanceFor(s, 'dir_hr')))
    expect(across.size).toBeGreaterThan(1)
  })

  it('chooses per person rather than per run', () => {
    // Two identities in one run may independently land on different letters. If they moved
    // together the seed would be picking one cast rather than eleven appearances.
    const seeds = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9]
    expect(seeds.some((s) => appearanceFor(s, 'dir_sales') !== appearanceFor(s, 'stf_cs'))).toBe(
      true,
    )
  })

  it('draws a lead as their own art', () => {
    for (const id of ROSTER) {
      for (const seed of [0, 1, 2, 5, 99]) {
        expect(candidateFor(id, seed).startsWith(`${id}-`), `${id} at ${seed}`).toBe(true)
      }
    }
  })

  it('casts anyone the board never met as somebody who is on it', () => {
    // R9, read as simply as it can be: a background coworker is not *compatible* with the
    // leads, they are the same casting board.
    for (const id of ['temp_001', 'temp_002', 'contractor', 'visitor_9']) {
      expect(ATLAS[candidateFor(id, 3)], id).toBeDefined()
      expect(skinFor(id, 3).skin, id).toMatch(/^#[0-9a-f]{6}$/)
    }
  })

  it('derives the same coworker from the same id every time', () => {
    expect(candidateFor('temp_001', 3)).toBe(candidateFor('temp_001', 3))
  })

  it('points at the right cell of the atlas', () => {
    const entry = ATLAS[candidateFor('dir_sales', 1)]
    const { sx, sy } = cellRect('dir_sales', 1, 2, 3)
    expect(sx).toBe(3 * CELL_WIDTH)
    expect(sy).toBe((entry.row * SHEET_FACINGS + 2) * CELL_HEIGHT)
  })

  it('walks through a cycle that never stands still', () => {
    expect(WALK_CYCLE).toHaveLength(4)
    expect(WALK_CYCLE).not.toContain(0)
    expect(new Set(WALK_CYCLE).size).toBeGreaterThan(1)
  })

  it('never sends an appearance anywhere', () => {
    // R9a holds because there is no channel, not because nothing currently writes to one.
    const gateway = readFileSync(
      join(dirname(fileURLToPath(import.meta.url)), '../src/net/gateway.ts'),
      'utf8',
    )
    expect(gateway).not.toContain('appearance')
    expect(gateway).not.toContain('candidate')
  })
})
