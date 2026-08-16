#!/usr/bin/env node
/**
 * Turn the authored art in `art/` into the grid modules the client imports.
 *
 * One command, so the committed grids and the art they came from cannot drift: if the module
 * in `src/render/cast/` disagrees with the PNG in `art/`, running this fixes it, and the diff
 * says which pixels moved.
 *
 *   node scripts/make-rig.mjs && node scripts/make-library.mjs && node scripts/build-cast.mjs
 *
 * Everything goes through `grid-from-png.mjs`, including the rig, so the generated art takes
 * exactly the path a hand-drawn PNG would rather than a shortcut around it.
 */

import { readFileSync, writeFileSync } from 'node:fs'

import { cellsFrom } from './grid-from-png.mjs'
import { readPng } from './png.mjs'

const HEADER = `/**
 * GENERATED — do not edit by hand.
 *
 * Authored in \`art/\` and built by \`scripts/build-cast.mjs\`. Edit the art and re-run; a
 * change made here is a change the next build silently discards.
 *
 * Committed as text rather than kept as PNG so a one-pixel change shows up in review as one
 * changed character, and so runtime palette substitution stays free — \`paint()\` is already a
 * glyph-to-colour function, and a glyph here is a slot rather than a colour.
 */

`

function art(path) {
  return readPng(readFileSync(new URL(`../art/${path}`, import.meta.url)))
}

function out(path, body) {
  const target = new URL(`../src/render/cast/${path}`, import.meta.url)
  writeFileSync(target, HEADER + body)
  console.log(`src/render/cast/${path}`)
}

function cellLiteral(cell, indent) {
  const pad = ' '.repeat(indent)
  return [`${pad}[`, ...cell.map((row) => `${pad}  '${row}',`), `${pad}],`].join('\n')
}

// =========================================================================
// The body rig
// =========================================================================

{
  const views = ['down', 'up', 'side']
  const cells = cellsFrom(art('rig/body.png'), 4, views.length, 'rig/body.png')

  const body = [
    "import type { View } from './slots'",
    '',
    '/** The shared body, by view and then by frame. Frame 0 is standing. */',
    'export const RIG: Record<View, string[][]> = {',
    ...views.map((view, row) =>
      [
        `  ${view}: [`,
        ...cells.slice(row * 4, row * 4 + 4).map((cell) => cellLiteral(cell, 4)),
        '  ],',
      ].join('\n'),
    ),
    '}',
    '',
  ].join('\n')

  out('grammar.ts', body)
}

// =========================================================================
// The feature libraries
// =========================================================================

/**
 * Overlays are banded rather than full cells.
 *
 * A hair shape occupies the head and nothing else; an outfit occupies the torso and the
 * limbs; an accessory occupies a few rows. Authoring each as a full 48×64 cell would commit
 * tens of thousands of transparent glyphs whose only job is to be transparent — the plan
 * costed the libraries that way and it came to nearly nine thousand lines of nothing.
 *
 * So each library declares the row its band starts at, and composition offsets it. The art is
 * the same; what changes is how much of it is padding.
 */
const LIBRARIES = [
  { file: 'hair.png', name: 'HAIR', module: 'hair.ts', band: 0, height: 24, shapes: 8 },
  { file: 'outfits.png', name: 'OUTFITS', module: 'outfits.ts', band: 20, height: 24, shapes: 8 },
  {
    file: 'accessories.png',
    name: 'ACCESSORIES',
    module: 'accessories.ts',
    band: 0,
    height: 40,
    shapes: 6,
  },
]

for (const library of LIBRARIES) {
  const image = art(`library/${library.file}`)
  const cells = cellsFrom(image, 3, library.shapes, library.file)

  const body = [
    "import type { View } from './slots'",
    '',
    '/** Where in a 48×64 cell this overlay’s band begins. */',
    `export const ${library.name}_BAND = ${library.band}`,
    '',
    `/** One entry per shape, each with the three authored views. */`,
    `export const ${library.name}: Record<View, string[]>[] = [`,
    ...Array.from({ length: library.shapes }, (_, shape) =>
      [
        '  {',
        ...['down', 'up', 'side'].map((view, index) =>
          [
            `    ${view}: [`,
            ...cells[shape * 3 + index].map((row) => `      '${row}',`),
            '    ],',
          ].join('\n'),
        ),
        '  },',
      ].join('\n'),
    ),
    ']',
    '',
  ].join('\n')

  out(library.module, body)
}
