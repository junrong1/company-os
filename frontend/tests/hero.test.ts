import { describe, expect, it } from 'vitest'

// @ts-expect-error - a plain .mjs tool, imported the way `cast.test.ts` imports `png.mjs`
import { quantise, writeGif } from '../scripts/gif.mjs'

/**
 * The GIF writer behind the README's hero (U23: M66).
 *
 * The hero itself is a checked-in recording and nothing automated can judge it — whether eight
 * seconds of a company reads as a company is a human call, the same call `screenshots.mjs`
 * exists to put in front of somebody. What *can* be checked is the encoder underneath it, and
 * it needs checking for the reason `png.mjs` needed it: it is a format written from the spec
 * rather than pulled from a registry, and a GIF that is wrong is not an error — it is a picture
 * of static, or one frame, or a palette of mud.
 *
 * So these tests **decode**. The reader below is written from the spec and shares nothing with
 * the writer: its own LZW, its own frame composition, its own disposal handling. What comes out
 * has to be the pixels that went in, which is a claim about the writer that no amount of
 * inspecting its output bytes would make.
 *
 * The round trip is exact rather than approximate, and that is a property of the input rather
 * than a tolerance: a palette holds 255 colours and these frames use six, so quantisation has
 * nothing to lose. A real capture has thousands and loses a little, which is what the separate
 * quantiser tests are about.
 */

interface Decoded {
  width: number
  height: number
  loops: number | null
  frames: Array<{ rgba: Uint8Array; delayMs: number; box: [number, number, number, number] }>
}

/**
 * A GIF89a reader, in terms of what these tests need.
 *
 * Scope: one global colour table, no local tables, no interlacing — which is what the writer
 * produces, and anything else throws rather than being guessed at.
 */
function readGif(bytes: Uint8Array): Decoded {
  let at = 0
  const byte = () => bytes[at++]
  const short = () => {
    const value = bytes[at] | (bytes[at + 1] << 8)
    at += 2
    return value
  }

  const signature = String.fromCharCode(...bytes.subarray(0, 6))
  if (signature !== 'GIF89a') throw new Error(`not a GIF89a: ${signature}`)
  at = 6

  const width = short()
  const height = short()
  const packed = byte()
  byte() // background index
  byte() // aspect ratio
  if ((packed & 0x80) === 0) throw new Error('no global colour table')

  const colours = 2 << (packed & 0x07)
  const palette: Array<[number, number, number]> = []
  for (let index = 0; index < colours; index += 1) {
    palette.push([bytes[at], bytes[at + 1], bytes[at + 2]])
    at += 3
  }

  /** One chain of length-prefixed sub-blocks, concatenated. */
  const subBlocks = (): Uint8Array => {
    const parts: Uint8Array[] = []
    for (let length = byte(); length !== 0; length = byte()) {
      parts.push(bytes.subarray(at, at + length))
      at += length
    }
    const total = parts.reduce((sum, part) => sum + part.length, 0)
    const joined = new Uint8Array(total)
    let offset = 0
    for (const part of parts) {
      joined.set(part, offset)
      offset += part.length
    }
    return joined
  }

  const decoded: Decoded = { width, height, loops: null, frames: [] }

  // The canvas frames are composed onto, exactly as a viewer keeps one. Disposal 1 — leave the
  // frame in place — is the only mode the writer uses, and it is the one that makes a
  // transparent pixel mean "whatever was there before".
  const canvas = new Uint8Array(width * height * 4)
  let control: { delayMs: number; transparent: number | null } | null = null

  for (;;) {
    const marker = byte()
    if (marker === 0x3b) break

    if (marker === 0x21) {
      const label = byte()
      if (label === 0xf9) {
        const size = byte()
        if (size !== 4) throw new Error('a graphic control extension is four bytes')
        const flags = byte()
        const delay = short()
        const transparent = byte()
        byte() // block terminator
        control = {
          delayMs: delay * 10,
          transparent: (flags & 0x01) === 1 ? transparent : null,
        }
        continue
      }
      if (label === 0xff) {
        const size = byte()
        const name = String.fromCharCode(...bytes.subarray(at, at + size))
        at += size
        const body = subBlocks()
        if (name === 'NETSCAPE2.0') decoded.loops = body[1] | (body[2] << 8)
        continue
      }
      subBlocks()
      continue
    }

    if (marker !== 0x2c) throw new Error(`unknown block 0x${marker.toString(16)}`)

    const left = short()
    const top = short()
    const boxWidth = short()
    const boxHeight = short()
    const imageFlags = byte()
    if ((imageFlags & 0x80) !== 0) throw new Error('local colour tables are not supported')
    if ((imageFlags & 0x40) !== 0) throw new Error('interlacing is not supported')

    const minimumCodeSize = byte()
    const indexes = inflateLzw(subBlocks(), minimumCodeSize, boxWidth * boxHeight)

    for (let y = 0; y < boxHeight; y += 1) {
      for (let x = 0; x < boxWidth; x += 1) {
        const index = indexes[y * boxWidth + x]
        if (control?.transparent === index) continue
        const [r, g, b] = palette[index]
        const target = ((top + y) * width + left + x) * 4
        canvas[target] = r
        canvas[target + 1] = g
        canvas[target + 2] = b
        canvas[target + 3] = 255
      }
    }

    decoded.frames.push({
      rgba: canvas.slice(),
      delayMs: control?.delayMs ?? 0,
      box: [left, top, boxWidth, boxHeight],
    })
    control = null
  }

  return decoded
}

/** GIF's variable-width LZW, decoded. The mirror of the writer's, written from the spec. */
function inflateLzw(bytes: Uint8Array, minimumCodeSize: number, expected: number): Uint8Array {
  const clear = 1 << minimumCodeSize
  const end = clear + 1

  const out = new Uint8Array(expected)
  let written = 0

  let dictionary: number[][] = []
  let codeSize = minimumCodeSize + 1
  const reset = () => {
    dictionary = []
    for (let index = 0; index < clear; index += 1) dictionary.push([index])
    dictionary.push([], [])
    codeSize = minimumCodeSize + 1
  }
  reset()

  let held = 0
  let heldBits = 0
  let cursor = 0
  let previous: number[] | null = null

  for (;;) {
    while (heldBits < codeSize) {
      if (cursor >= bytes.length) return out
      held |= bytes[cursor++] << heldBits
      heldBits += 8
    }
    const code = held & ((1 << codeSize) - 1)
    held >>= codeSize
    heldBits -= codeSize

    if (code === clear) {
      reset()
      previous = null
      continue
    }
    if (code === end) break

    let entry: number[]
    if (code < dictionary.length) entry = dictionary[code]
    else if (previous !== null) entry = [...previous, previous[0]]
    else throw new Error('the stream opens with a code it has not defined')

    for (const index of entry) out[written++] = index

    if (previous !== null) {
      dictionary.push([...previous, entry[0]])
      if (dictionary.length === 1 << codeSize && codeSize < 12) codeSize += 1
    }
    previous = entry
  }

  return out
}

/** A small animation with few enough colours that quantisation has nothing to lose. */
function aRedBoxCrossingIvory(width = 40, height = 24, count = 8) {
  const frames = []
  for (let index = 0; index < count; index += 1) {
    const rgba = new Uint8Array(width * height * 4)
    for (let y = 0; y < height; y += 1) {
      for (let x = 0; x < width; x += 1) {
        const at = (y * width + x) * 4
        const inside = x >= index * 3 && x < index * 3 + 8 && y >= 6 && y < 18
        rgba[at] = inside ? 0xc8 : 0xff
        rgba[at + 1] = inside ? 0x40 : 0xfe
        rgba[at + 2] = inside ? 0x2a : 0xf8
        rgba[at + 3] = 255
      }
    }
    frames.push({ rgba, delayMs: 80 })
  }
  return { width, height, frames }
}

describe('the palette', () => {
  it('keeps every colour when they fit, rather than approximating any of them', () => {
    const { frames } = aRedBoxCrossingIvory()
    expect(quantise(frames)).toEqual([
      [255, 254, 248],
      [200, 64, 42],
    ])
  })

  it('splits down to the bound, and every entry is a colour', () => {
    // A gradient, which is the shape a UI's antialiased text actually produces: hundreds of
    // near-neighbours rather than a handful of flat fills.
    const rgba = new Uint8Array(64 * 64 * 4)
    for (let index = 0; index < 64 * 64; index += 1) {
      rgba[index * 4] = index % 256
      rgba[index * 4 + 1] = (index * 7) % 256
      rgba[index * 4 + 2] = (index * 13) % 256
      rgba[index * 4 + 3] = 255
    }

    const palette = quantise([{ rgba, delayMs: 80 }])

    expect(palette.length).toBeGreaterThan(200)
    expect(palette.length).toBeLessThanOrEqual(255)
    // The bug this pins: a box split at its last index leaves an empty box behind, and the
    // average of no colours is NaN. Found by encoding a two-colour test pattern.
    for (const channel of palette.flat()) {
      expect(Number.isInteger(channel)).toBe(true)
      expect(channel).toBeGreaterThanOrEqual(0)
      expect(channel).toBeLessThanOrEqual(255)
    }
  })
})

describe('the file', () => {
  it('round-trips every frame, pixel for pixel', () => {
    const { width, height, frames } = aRedBoxCrossingIvory()

    const decoded = readGif(writeGif({ width, height, frames }))

    expect(decoded.width).toBe(width)
    expect(decoded.height).toBe(height)
    expect(decoded.frames).toHaveLength(frames.length)
    for (let index = 0; index < frames.length; index += 1) {
      expect(Array.from(decoded.frames[index].rgba)).toEqual(Array.from(frames[index].rgba))
    }
  })

  it('loops forever, which nothing in the format proper says', () => {
    const { width, height, frames } = aRedBoxCrossingIvory()

    expect(readGif(writeGif({ width, height, frames })).loops).toBe(0)
  })

  it('carries each frame’s delay, and never one a viewer would round away', () => {
    const { width, height, frames } = aRedBoxCrossingIvory()
    frames[1].delayMs = 5 // below the hundredth-of-a-second floor the format stores in

    const decoded = readGif(writeGif({ width, height, frames }))

    expect(decoded.frames[0].delayMs).toBe(80)
    // Clamped to two hundredths rather than written as zero: a zero delay is "as fast as
    // possible" in some viewers and "a tenth of a second" in others, and both are wrong.
    expect(decoded.frames[1].delayMs).toBe(20)
  })

  it('writes only what changed, which is what makes a hundred frames a small file', () => {
    const { width, height, frames } = aRedBoxCrossingIvory()

    const decoded = readGif(writeGif({ width, height, frames }))

    expect(decoded.frames[0].box).toEqual([0, 0, width, height])
    for (const frame of decoded.frames.slice(1)) {
      const [, , boxWidth, boxHeight] = frame.box
      expect(boxWidth * boxHeight).toBeLessThan(width * height)
    }
  })

  it('costs far less than the frames it is made of', () => {
    const { width, height, frames } = aRedBoxCrossingIvory(120, 90, 40)

    const bytes = writeGif({ width, height, frames })

    expect(bytes.length).toBeLessThan((width * height * frames.length) / 20)
  })

  it('refuses a frame that is not the size it was told', () => {
    const { width, height, frames } = aRedBoxCrossingIvory()
    frames[2].rgba = new Uint8Array(4)

    expect(() => writeGif({ width, height, frames })).toThrow(/not/)
  })

  it('refuses to write nothing at all', () => {
    expect(() => writeGif({ width: 4, height: 4, frames: [] })).toThrow(/at least one frame/)
  })
})
