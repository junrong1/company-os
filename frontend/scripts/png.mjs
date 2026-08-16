/**
 * A PNG reader and writer, in terms of what this repository actually needs.
 *
 * Deliberately not a dependency. The plan called for `pngjs`, and then it turned out the
 * whole requirement is "decode an 8-bit indexed or truecolour PNG, and write an RGBA one" —
 * which is about a hundred lines on top of `node:zlib`, and `node:zlib` is already there.
 * Adding a package to the tree for this would mean a lockfile entry, a supply-chain edge and
 * an install step, all of which outlive the hundred lines.
 *
 * Scope, stated so the next person does not discover it the hard way: 8-bit channels only,
 * no interlacing, no 16-bit, no APNG. Every PNG this repository authors or reads is one of
 * the two shapes above, and anything else is an error rather than a silent misread.
 */

import { deflateSync, inflateSync } from 'node:zlib'

const SIGNATURE = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])

/** Channels per pixel, by PNG colour type. */
const CHANNELS = { 0: 1, 2: 3, 3: 1, 4: 2, 6: 4 }

/**
 * Undo one scanline's filter.
 *
 * The five filter types are the whole reason a PNG cannot be read by slicing bytes: each
 * line is encoded as a difference against its left neighbour, the line above, or both, so a
 * decoder has to walk them in order and cannot skip to the middle.
 */
function unfilter(type, line, previous, bpp) {
  for (let x = 0; x < line.length; x += 1) {
    const left = x >= bpp ? line[x - bpp] : 0
    const up = previous[x]
    const upLeft = x >= bpp ? previous[x - bpp] : 0

    if (type === 1) line[x] = (line[x] + left) & 0xff
    else if (type === 2) line[x] = (line[x] + up) & 0xff
    else if (type === 3) line[x] = (line[x] + ((left + up) >> 1)) & 0xff
    else if (type === 4) {
      const estimate = left + up - upLeft
      const dLeft = Math.abs(estimate - left)
      const dUp = Math.abs(estimate - up)
      const dUpLeft = Math.abs(estimate - upLeft)
      const nearest = dLeft <= dUp && dLeft <= dUpLeft ? left : dUp <= dUpLeft ? up : upLeft
      line[x] = (line[x] + nearest) & 0xff
    } else if (type !== 0) {
      throw new Error(`unknown PNG filter type ${type}`)
    }
  }
  return line
}

/**
 * Read a PNG into flat RGBA.
 *
 * Returns `{ width, height, pixels }` where `pixels` is `width * height * 4` bytes. Indexed
 * and truecolour both arrive here as RGBA, so callers never branch on colour type.
 */
export function readPng(buffer) {
  if (!buffer.subarray(0, 8).equals(SIGNATURE)) throw new Error('not a PNG')

  let width = 0
  let height = 0
  let depth = 0
  let colourType = 0
  let palette = null
  let transparency = null
  const parts = []

  let offset = 8
  while (offset < buffer.length) {
    const length = buffer.readUInt32BE(offset)
    const type = buffer.toString('ascii', offset + 4, offset + 8)
    const data = buffer.subarray(offset + 8, offset + 8 + length)
    offset += 12 + length

    if (type === 'IHDR') {
      width = data.readUInt32BE(0)
      height = data.readUInt32BE(4)
      depth = data[8]
      colourType = data[9]
      if (data[12] !== 0) throw new Error('interlaced PNGs are not supported')
    } else if (type === 'PLTE') palette = data
    else if (type === 'tRNS') transparency = data
    else if (type === 'IDAT') parts.push(data)
    else if (type === 'IEND') break
  }

  if (depth !== 8) throw new Error(`only 8-bit PNGs are supported, got ${depth}-bit`)

  const channels = CHANNELS[colourType]
  if (channels === undefined) throw new Error(`unknown PNG colour type ${colourType}`)

  const raw = inflateSync(Buffer.concat(parts))
  const stride = width * channels
  const bpp = channels

  const pixels = Buffer.alloc(width * height * 4)
  let previous = Buffer.alloc(stride)
  let cursor = 0

  for (let y = 0; y < height; y += 1) {
    const type = raw[cursor]
    cursor += 1
    const line = unfilter(type, Buffer.from(raw.subarray(cursor, cursor + stride)), previous, bpp)
    cursor += stride

    for (let x = 0; x < width; x += 1) {
      const source = x * channels
      const target = (y * width + x) * 4
      let r
      let g
      let b
      let a = 255

      if (colourType === 3) {
        const index = line[source]
        if (palette === null) throw new Error('indexed PNG with no palette')
        r = palette[index * 3]
        g = palette[index * 3 + 1]
        b = palette[index * 3 + 2]
        if (transparency !== null && index < transparency.length) a = transparency[index]
      } else if (colourType === 6) {
        r = line[source]
        g = line[source + 1]
        b = line[source + 2]
        a = line[source + 3]
      } else if (colourType === 2) {
        r = line[source]
        g = line[source + 1]
        b = line[source + 2]
      } else if (colourType === 4) {
        r = line[source]
        g = line[source]
        b = line[source]
        a = line[source + 1]
      } else {
        r = line[source]
        g = line[source]
        b = line[source]
      }

      pixels[target] = r
      pixels[target + 1] = g
      pixels[target + 2] = b
      pixels[target + 3] = a
    }

    previous = line
  }

  return { width, height, pixels }
}

function chunk(type, data) {
  const length = Buffer.alloc(4)
  length.writeUInt32BE(data.length, 0)
  const body = Buffer.concat([Buffer.from(type, 'ascii'), data])
  const crc = Buffer.alloc(4)
  crc.writeUInt32BE(crc32(body) >>> 0, 0)
  return Buffer.concat([length, body, crc])
}

const CRC_TABLE = (() => {
  const table = new Int32Array(256)
  for (let n = 0; n < 256; n += 1) {
    let c = n
    for (let k = 0; k < 8; k += 1) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1
    table[n] = c
  }
  return table
})()

function crc32(buffer) {
  let c = -1
  for (const byte of buffer) c = CRC_TABLE[(c ^ byte) & 0xff] ^ (c >>> 8)
  return c ^ -1
}

/** Write flat RGBA out as a truecolour-with-alpha PNG, unfiltered. */
export function writePng(width, height, pixels) {
  const stride = width * 4
  const raw = Buffer.alloc((stride + 1) * height)
  for (let y = 0; y < height; y += 1) {
    raw[y * (stride + 1)] = 0
    pixels.copy(raw, y * (stride + 1) + 1, y * stride, (y + 1) * stride)
  }

  const header = Buffer.alloc(13)
  header.writeUInt32BE(width, 0)
  header.writeUInt32BE(height, 4)
  header[8] = 8
  header[9] = 6
  return Buffer.concat([
    SIGNATURE,
    chunk('IHDR', header),
    chunk('IDAT', deflateSync(raw, { level: 9 })),
    chunk('IEND', Buffer.alloc(0)),
  ])
}

/** `#rrggbb` for one pixel, ignoring alpha. */
export function hexAt(pixels, index) {
  const at = index * 4
  return `#${pixels[at].toString(16).padStart(2, '0')}${pixels[at + 1]
    .toString(16)
    .padStart(2, '0')}${pixels[at + 2].toString(16).padStart(2, '0')}`
}
