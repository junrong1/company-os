/**
 * The 5×7 label face, and the two painters every pixel surface draws through.
 *
 * Room names and person labels in the prototype are drawn with the system sans over the
 * canvas, which floats on top of the art instead of belonging to it — a hinted, antialiased,
 * platform-variable face sitting on nearest-neighbour pixel art. A bitmap alphabet built from
 * the same grid pipeline stays crisp at every integer zoom and is the same shape as
 * everything around it.
 *
 * Generated, not downloaded: a web font would be a network request in a product whose art is
 * compiled at runtime, and it would render differently on the first frame than the tenth.
 *
 * Kept here rather than in `render/` or `dag/` because both surfaces draw labels, and the
 * face is exactly the kind of thing that ends up subtly forked when it lives inside one
 * consumer.
 */

import { PAL } from './tokens'

/** Rows are `/`-separated, `1` is ink and `0` is transparent. 5 wide, 7 tall. */
export const FONT: Record<string, string> = {
  A: '01110/10001/10001/11111/10001/10001/10001',
  B: '11110/10001/10001/11110/10001/10001/11110',
  C: '01110/10001/10000/10000/10000/10001/01110',
  D: '11110/10001/10001/10001/10001/10001/11110',
  E: '11111/10000/10000/11110/10000/10000/11111',
  F: '11111/10000/10000/11110/10000/10000/10000',
  G: '01110/10001/10000/10111/10001/10001/01110',
  H: '10001/10001/10001/11111/10001/10001/10001',
  I: '11111/00100/00100/00100/00100/00100/11111',
  J: '00111/00010/00010/00010/00010/10010/01100',
  K: '10001/10010/10100/11000/10100/10010/10001',
  L: '10000/10000/10000/10000/10000/10000/11111',
  M: '10001/11011/10101/10101/10001/10001/10001',
  N: '10001/11001/10101/10011/10001/10001/10001',
  O: '01110/10001/10001/10001/10001/10001/01110',
  P: '11110/10001/10001/11110/10000/10000/10000',
  Q: '01110/10001/10001/10001/10101/10011/01101',
  R: '11110/10001/10001/11110/10100/10010/10001',
  S: '01111/10000/10000/01110/00001/00001/11110',
  T: '11111/00100/00100/00100/00100/00100/00100',
  U: '10001/10001/10001/10001/10001/10001/01110',
  V: '10001/10001/10001/10001/10001/01010/00100',
  W: '10001/10001/10001/10101/10101/11011/10001',
  X: '10001/10001/01010/00100/01010/10001/10001',
  Y: '10001/10001/01010/00100/00100/00100/00100',
  Z: '11111/00001/00010/00100/01000/10000/11111',
  '0': '01110/10001/10011/10101/11001/10001/01110',
  '1': '00100/01100/00100/00100/00100/00100/01110',
  '2': '01110/10001/00001/00010/00100/01000/11111',
  '3': '11111/00010/00100/00010/00001/10001/01110',
  '4': '00010/00110/01010/10010/11111/00010/00010',
  '5': '11111/10000/11110/00001/00001/10001/01110',
  '6': '00110/01000/10000/11110/10001/10001/01110',
  '7': '11111/00001/00010/00100/01000/01000/01000',
  '8': '01110/10001/10001/01110/10001/10001/01110',
  '9': '01110/10001/10001/01111/00001/00010/01100',
  '.': '00000/00000/00000/00000/00000/01100/01100',
  ':': '00000/01100/01100/00000/01100/01100/00000',
  '%': '11001/11010/00010/00100/01000/01011/10011',
  $: '00100/01111/10100/01110/00101/11110/00100',
  '+': '00000/00100/00100/11111/00100/00100/00000',
  '-': '00000/00000/00000/11111/00000/00000/00000',
  '/': '00001/00010/00010/00100/01000/01000/10000',
  ' ': '00000/00000/00000/00000/00000/00000/00000',
  // The authored-tuning marking (R36). In the face rather than drawn as a one-off shape, so
  // the canvas surfaces mark a figure with the same character the DOM surfaces do — the
  // marking is one token, and two glyphs for it would be two markings.
  '≈': '00000/01011/11010/00000/01011/11010/00000',
}

/** Advance per character, in face units: 5 wide plus one column of tracking. */
export const ADVANCE = 6

/** Glyph height in face units. */
export const CAP_HEIGHT = 7

/**
 * The subset of a 2D context these painters need.
 *
 * Narrow on purpose. jsdom does not implement `getContext`, so every drawing path in the
 * client takes its context as a parameter and the suites hand it a recorder. That is not a
 * testing convenience bolted on: it is what lets a test assert "this state drew a violet
 * severed edge" instead of comparing screenshots.
 */
export interface PixelContext {
  fillStyle: string | CanvasGradient | CanvasPattern
  fillRect(x: number, y: number, width: number, height: number): void
}

/** A palette mapping one grid character to a colour. A missing key is transparent. */
export type GridPalette = Record<string, string | undefined>

/** Paint a character grid at an integer zoom. The one primitive under all the pixel art. */
export function paintGrid(
  context: PixelContext,
  rows: readonly string[],
  originX: number,
  originY: number,
  palette: GridPalette,
  zoom = 1,
): void {
  for (let y = 0; y < rows.length; y += 1) {
    const row = rows[y]
    for (let x = 0; x < row.length; x += 1) {
      const colour = palette[row[x]]
      if (colour === undefined) continue
      context.fillStyle = colour
      context.fillRect(originX + x * zoom, originY + y * zoom, zoom, zoom)
    }
  }
}

/**
 * Draw a string in the label face, returning the width it consumed.
 *
 * Upper-cased on the way in: the face has no lower case, and silently dropping lower-case
 * characters would render "Sales" as "S" with no indication why.
 */
export function paintText(
  context: PixelContext,
  text: string,
  originX: number,
  originY: number,
  zoom = 1,
  colour: string = PAL.text,
): number {
  let cursor = originX
  const ink: GridPalette = { '1': colour }

  for (const character of text.toUpperCase()) {
    const definition = FONT[character]
    if (definition === undefined) {
      // An unmapped character advances rather than collapsing, so a label with one odd
      // glyph keeps its spacing instead of shifting everything after it left.
      cursor += ADVANCE * zoom
      continue
    }
    paintGrid(context, definition.split('/'), cursor, originY, ink, zoom)
    cursor += ADVANCE * zoom
  }

  return cursor - originX
}

/** How wide a string will be, without drawing it. Trailing tracking excluded. */
export function textWidth(text: string, zoom = 1): number {
  if (text.length === 0) return 0
  return text.length * ADVANCE * zoom - zoom
}

/**
 * Truncate to fit a pixel width, in whole characters.
 *
 * Clipping mid-glyph would leave a column of orphaned pixels that reads as a rendering
 * fault rather than as elision.
 */
export function fitText(text: string, availableWidth: number, zoom = 1): string {
  const perCharacter = ADVANCE * zoom
  if (perCharacter <= 0) return ''
  const fits = Math.floor((availableWidth + zoom) / perCharacter)
  if (fits >= text.length) return text
  return text.slice(0, Math.max(0, fits))
}
