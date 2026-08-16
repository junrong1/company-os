#!/usr/bin/env node
/**
 * Turn authored PNG art into the ASCII grids the cast is committed as.
 *
 * The authoring format is PNG because that is what a pixel artist works in. The committed
 * format is ASCII because a one-pixel change should show up in review as one changed
 * character rather than as a changed blob — and because it makes runtime palette
 * substitution free, since `paint()` is already a glyph-to-colour function.
 *
 * The conversion is exact or it fails. Every pixel is fully transparent or an exact sentinel;
 * anything else — a near-miss from a lossy save, a stray anti-aliased edge, a colour picked
 * by eye rather than by eyedropper — is an error naming the coordinate and the value it
 * found. A converter that snapped to the nearest slot would produce art that is subtly and
 * unfixably wrong, and would do it silently.
 *
 *   node scripts/grid-from-png.mjs <in.png> <cols> <rows> [--name X]
 *
 * A sheet wider or taller than one cell is split into cells left to right, top to bottom.
 */

import { readFileSync } from 'node:fs'

import { hexAt, readPng } from './png.mjs'

/** Slot glyphs, mirrored from src/render/cast/slots.ts. */
export const SENTINEL_GLYPHS = {
  '#ff0000': 's',
  '#cc0000': 'S',
  '#990000': 'l',
  '#00ff00': 'h',
  '#00cc00': 'H',
  '#009900': 'j',
  '#0000ff': 't',
  '#0000cc': 'T',
  '#000099': 'u',
  '#ffff00': 'p',
  '#cccc00': 'P',
  '#ff00ff': 'b',
  '#cc00cc': 'B',
  '#000000': 'k',
  '#00ffff': 'e',
  '#00cccc': 'm',
  '#ff8800': 'a',
}

export class ConversionError extends Error {}

/**
 * Convert decoded RGBA into a grid of glyph rows.
 *
 * Exported separately from the CLI so the round-trip test can drive it without a file.
 */
export function gridFromPixels(width, height, pixels, label = 'image') {
  const rows = []

  for (let y = 0; y < height; y += 1) {
    let row = ''
    for (let x = 0; x < width; x += 1) {
      const index = y * width + x
      const alpha = pixels[index * 4 + 3]

      if (alpha === 0) {
        row += '.'
        continue
      }
      if (alpha !== 255) {
        throw new ConversionError(
          `${label}: pixel ${x},${y} is ${alpha}/255 opaque. Pixel art has no partial ` +
            `alpha — this is almost always a resize that resampled instead of scaling.`,
        )
      }

      const hex = hexAt(pixels, index)
      const glyph = SENTINEL_GLYPHS[hex]
      if (glyph === undefined) {
        throw new ConversionError(
          `${label}: pixel ${x},${y} is ${hex}, which is not a slot sentinel.`,
        )
      }
      row += glyph
    }
    rows.push(row)
  }

  return rows
}

/** Split a sheet into `cols` × `rows` cells of equal size. */
export function cellsFrom(image, cols, rows, label = 'image') {
  if (image.width % cols !== 0 || image.height % rows !== 0) {
    throw new ConversionError(
      `${label}: ${image.width}×${image.height} does not divide into ${cols}×${rows} cells.`,
    )
  }

  const cellWidth = image.width / cols
  const cellHeight = image.height / rows
  const cells = []

  for (let row = 0; row < rows; row += 1) {
    for (let col = 0; col < cols; col += 1) {
      const slice = Buffer.alloc(cellWidth * cellHeight * 4)
      for (let y = 0; y < cellHeight; y += 1) {
        const from = ((row * cellHeight + y) * image.width + col * cellWidth) * 4
        image.pixels.copy(slice, y * cellWidth * 4, from, from + cellWidth * 4)
      }
      cells.push(gridFromPixels(cellWidth, cellHeight, slice, `${label} cell ${col},${row}`))
    }
  }

  return cells
}

function main(argv) {
  const [source, colsText, rowsText] = argv
  if (source === undefined) {
    console.error('usage: grid-from-png.mjs <in.png> [cols] [rows] [--name X]')
    process.exit(2)
  }

  const nameFlag = argv.indexOf('--name')
  const name = nameFlag === -1 ? 'CELLS' : argv[nameFlag + 1]

  const image = readPng(readFileSync(source))
  const cells = cellsFrom(image, Number(colsText ?? 1), Number(rowsText ?? 1), source)

  console.log(`export const ${name}: string[][] = [`)
  for (const cell of cells) {
    console.log('  [')
    for (const row of cell) console.log(`    '${row}',`)
    console.log('  ],')
  }
  console.log(']')
}

if (import.meta.url === `file://${process.argv[1]}`) {
  try {
    main(process.argv.slice(2))
  } catch (error) {
    console.error(error instanceof ConversionError ? error.message : error)
    process.exit(1)
  }
}
