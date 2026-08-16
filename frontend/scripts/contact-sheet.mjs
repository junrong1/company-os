#!/usr/bin/env node
/**
 * The cast, beside the casting board it came from.
 *
 * Thirty-three composed down-idles in one column and the thirty-three approved candidates in
 * the next, at 4× so a person is actually visible. This is the review artifact the redesign's
 * success criteria are judged from — "A, B and C remain recognisable as the same role while
 * differing materially" and "leads and coworkers share a visual family" are human calls, and
 * a human needs to see them side by side to make one.
 *
 * The last two rows are procedural coworkers, who are on the sheet for the claim that matters
 * most about them: they are not *compatible* with the leads, they are the same rig.
 *
 *   node scripts/contact-sheet.mjs
 */

import { mkdirSync, readFileSync, readdirSync, writeFileSync } from 'node:fs'
import { basename, join } from 'node:path'

import { readPng, writePng } from './png.mjs'

const CANDIDATES = new URL(
  '../../docs/assets/company-os-visual-redesign/roster-characters/',
  import.meta.url,
)
const OUT = new URL('../../docs/assets/company-os-visual-redesign/verification/', import.meta.url)

const CELL_W = 48
const CELL_H = 64
const SCALE = 4
const GUTTER = 8

/**
 * Composition, re-implemented over the committed grids.
 *
 * The client's own `composeCell` is TypeScript and this is a plain Node script; importing it
 * would mean a build step in front of a tool whose value is being one command. The grids and
 * the manifests are both generated *text*, so reading them back is exact rather than
 * approximate — and `tests/cast.test.ts` is what holds this and the client to the same
 * answer, since a contact sheet that disagreed with the product would be worse than none.
 */

/** Pull `name: [ 'row', ... ]` cell arrays out of a generated module. */
function cellsOf(text) {
  return [...text.matchAll(/'([.a-zA-Z]{48})'/g)].map((match) => match[1])
}

function chunk(rows, size) {
  const out = []
  for (let i = 0; i < rows.length; i += size) out.push(rows.slice(i, i + size))
  return out
}

const GLYPH_SLOTS = {
  s: 'skin', S: 'skinShade', l: 'skinLine',
  h: 'hair', H: 'hairLight', j: 'hairDark',
  t: 'top', T: 'topShade', u: 'topLine',
  p: 'legs', P: 'legsShade',
  b: 'shoes', B: 'shoesShade',
  k: 'outline', e: 'eye', m: 'mouth', a: 'accent',
}

function shift(hex, factor) {
  const value = Number.parseInt(hex.slice(1), 16)
  const channel = (bits) =>
    Math.round(Math.min(255, Math.max(0, ((value >> bits) & 0xff) * factor)))
      .toString(16)
      .padStart(2, '0')
  return `#${channel(16)}${channel(8)}${channel(0)}`
}

function resolve(skin) {
  return {
    skin: skin.skin, skinShade: shift(skin.skin, 0.84), skinLine: shift(skin.skin, 0.66),
    hair: skin.hair, hairLight: shift(skin.hair, 1.28), hairDark: shift(skin.hair, 0.7),
    top: skin.top, topShade: shift(skin.top, 0.82), topLine: shift(skin.top, 0.62),
    legs: skin.legs, legsShade: shift(skin.legs, 0.78),
    shoes: skin.shoes, shoesShade: shift(skin.shoes, 0.72),
    outline: '#253147', eye: '#253147',
    mouth: shift(skin.skin, 0.58), accent: skin.accent,
  }
}

const grid = (file, height) =>
  chunk(cellsOf(readFileSync(new URL(`../src/render/cast/${file}`, import.meta.url), 'utf8')), height)

const rig = grid('grammar.ts', CELL_H)
const hair = grid('hair.ts', 24)
const outfits = grid('outfits.ts', 24)
const accessories = grid('accessories.ts', 40)

/**
 * How many *shapes* each library has, which is its cell count over the three views.
 *
 * Worth its own name: the procedural block picked a shape with `% hair.length` and indexed
 * with `shape * 3`, which for a library of 8 shapes stored as 24 cells reaches past the end
 * two times out of three. Cells and shapes are different counts and the code read as though
 * they were one.
 */
const SHAPES = {
  hair: hair.length / 3,
  outfits: outfits.length / 3,
  accessories: accessories.length / 3,
}

const manifestSource = readFileSync(
  new URL('../src/render/cast/manifests.ts', import.meta.url),
  'utf8',
)

/** Read the generated manifests back without evaluating TypeScript. */
function manifests() {
  const out = []
  const blocks = manifestSource.split(/^  (\w+): \[$/m)
  for (let i = 1; i < blocks.length; i += 2) {
    const identity = blocks[i]
    const body = blocks[i + 1]
    const entries = [...body.matchAll(
      /hair: (\d+),\s*outfit: (\d+),\s*accessory: (\d+|null),\s*skin: \{([^}]*)\}/g,
    )]
    entries.forEach((entry, index) => {
      const skin = Object.fromEntries(
        [...entry[4].matchAll(/(\w+): '(#[0-9a-f]{6})'/g)].map((m) => [m[1], m[2]]),
      )
      out.push({
        id: `${identity}-${'abc'[index]}`,
        hair: Number(entry[1]),
        outfit: Number(entry[2]),
        accessory: entry[3] === 'null' ? null : Number(entry[3]),
        skin,
      })
    })
  }
  return out
}

/** Down-idle only: it is the frame the candidates are drawn in. */
function compose(manifest) {
  const cell = Array.from({ length: CELL_H }, () => Array.from({ length: CELL_W }, () => null))
  const lay = (rows, origin) => {
    rows.forEach((row, y) => {
      if (cell[y + origin] === undefined) return
      ;[...row].forEach((glyph, x) => {
        if (glyph === '.') return
        cell[y + origin][x] = GLYPH_SLOTS[glyph]
      })
    })
  }

  // Views are stored down: down, up, side — four frames each. Index 0 is the down idle.
  lay(rig[0], 0)
  lay(outfits[manifest.outfit * 3], 20)
  lay(hair[manifest.hair * 3], 0)
  if (manifest.accessory !== null) lay(accessories[manifest.accessory * 3], 0)

  return cell
}

// =========================================================================
// The sheet
// =========================================================================

const files = readdirSync(CANDIDATES).filter((n) => n.endsWith('.png')).sort()
const cast = manifests()
const extra = ['temp_001', 'temp_002']

const columns = 2
const rows = cast.length + extra.length
const width = (CELL_W * SCALE + GUTTER) * columns + GUTTER
const height = (CELL_H * SCALE + GUTTER) * rows + GUTTER
const pixels = Buffer.alloc(width * height * 4)

// 象牙白, so the sheet is judged on the ground the product actually uses.
for (let i = 0; i < width * height; i += 1) {
  pixels[i * 4] = 0xff
  pixels[i * 4 + 1] = 0xfe
  pixels[i * 4 + 2] = 0xf8
  pixels[i * 4 + 3] = 0xff
}

function blit(cell, palette, column, row) {
  const ox = GUTTER + column * (CELL_W * SCALE + GUTTER)
  const oy = GUTTER + row * (CELL_H * SCALE + GUTTER)

  for (let y = 0; y < CELL_H; y += 1) {
    for (let x = 0; x < CELL_W; x += 1) {
      const slot = cell[y][x]
      if (slot === null || slot === undefined) continue
      const hex = Number.parseInt((palette[slot] ?? '#ff00ff').slice(1), 16)
      for (let sy = 0; sy < SCALE; sy += 1) {
        for (let sx = 0; sx < SCALE; sx += 1) {
          const at = ((oy + y * SCALE + sy) * width + ox + x * SCALE + sx) * 4
          pixels[at] = (hex >> 16) & 0xff
          pixels[at + 1] = (hex >> 8) & 0xff
          pixels[at + 2] = hex & 0xff
          pixels[at + 3] = 255
        }
      }
    }
  }
}

function blitCandidate(file, row) {
  const image = readPng(readFileSync(join(CANDIDATES.pathname, file)))
  const ox = GUTTER + (CELL_W * SCALE + GUTTER)
  const oy = GUTTER + row * (CELL_H * SCALE + GUTTER)

  for (let y = 0; y < image.height; y += 1) {
    for (let x = 0; x < image.width; x += 1) {
      const from = (y * image.width + x) * 4
      if (image.pixels[from + 3] === 0) continue
      for (let sy = 0; sy < SCALE; sy += 1) {
        for (let sx = 0; sx < SCALE; sx += 1) {
          const at = ((oy + y * SCALE + sy) * width + ox + x * SCALE + sx) * 4
          pixels[at] = image.pixels[from]
          pixels[at + 1] = image.pixels[from + 1]
          pixels[at + 2] = image.pixels[from + 2]
          pixels[at + 3] = 255
        }
      }
    }
  }
}

cast.forEach((manifest, row) => {
  blit(compose(manifest), resolve(manifest.skin), 0, row)
  const file = files.find((name) => basename(name, '.png') === manifest.id)
  if (file !== undefined) blitCandidate(file, row)
})

// The procedural coworkers, on the sheet for the one claim that matters about them.
const SKINS = ['#f1c8ae', '#e3ab86', '#d89a52', '#c98662', '#b56743', '#a86745', '#8b5d47', '#70452f']
const HAIRS = ['#3a2728', '#4b302b', '#65443a', '#70452f', '#a8adb4', '#d6a04d', '#b56743', '#9d7f5f']
const TOPS = ['#2376b7', '#5a8fb7', '#b8c8d6', '#65758b', '#74759b', '#aa6a4c', '#c85c5c', '#2f8c8b']

function idHash(text) {
  let a = 0
  for (let i = 0; i < text.length; i += 1) a = (a * 31 + text.charCodeAt(i)) | 0
  return Math.abs(a)
}

extra.forEach((id, index) => {
  const manifest = {
    hair: idHash(`${id}:hairShape`) % SHAPES.hair,
    outfit: idHash(`${id}:outfit`) % SHAPES.outfits,
    accessory: idHash(`${id}:accessory`) % (SHAPES.accessories + 1),
    skin: {
      skin: SKINS[idHash(`${id}:skin`) % SKINS.length],
      hair: HAIRS[idHash(`${id}:hair`) % HAIRS.length],
      top: TOPS[idHash(`${id}:top`) % TOPS.length],
      legs: '#3f4b63',
      shoes: '#3a2728',
      accent: '#d6a04d',
    },
  }
  if (manifest.accessory >= SHAPES.accessories) manifest.accessory = null
  blit(compose(manifest), resolve(manifest.skin), 0, cast.length + index)
})

mkdirSync(OUT, { recursive: true })
writeFileSync(new URL('cast-contact-sheet.png', OUT), writePng(width, height, pixels))
console.log(
  `docs/assets/company-os-visual-redesign/verification/cast-contact-sheet.png — ` +
    `${cast.length} cast beside their candidates, plus ${extra.length} procedural`,
)
