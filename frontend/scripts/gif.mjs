/**
 * An animated GIF writer, in terms of what this repository actually needs.
 *
 * Deliberately not a dependency, for the reason `png.mjs` gives at length and one more of its
 * own. The reason it shares: the whole requirement is "turn a hundred RGBA frames into one
 * looping file", which is a palette, an LZW stream and a handful of blocks — and every package
 * that does it brings a lockfile entry and a supply-chain edge that outlive the code. The reason
 * of its own: the thing being encoded is a *designed* image. The office is flat pixel art on an
 * ivory ground, so a generic encoder's default dithering scatters noise across a floor that was
 * drawn without any, and the quality of this file is the first thing a stranger sees of the
 * product. Owning the quantiser is owning that.
 *
 * Scope, stated so the next person does not discover it the hard way: GIF89a, one global palette
 * of at most 255 colours plus transparency, no local palettes, no interlacing, no dithering.
 * Frames after the first are written as the changed rectangle only, with unchanged pixels left
 * transparent over the frame below — which is what keeps a hundred frames of a mostly-still UI
 * to a few megabytes rather than a few dozen.
 */

/** The transparent index, reserved so every frame after the first can leave pixels alone. */
const TRANSPARENT = 255

/** How many colours the palette may hold, given that one index is spoken for. */
const COLOURS = 255

/**
 * Every distinct colour in a set of frames, with how often each appears.
 *
 * Counted rather than sampled: at this size the histogram is cheap, and sampling is how a
 * quantiser loses a hue that appears in exactly one important place — the decision amber, say,
 * which is reserved for one meaning and shows up on one tile.
 */
function histogram(frames) {
  const counts = new Map()
  for (const { rgba } of frames) {
    for (let at = 0; at < rgba.length; at += 4) {
      const packed = (rgba[at] << 16) | (rgba[at + 1] << 8) | rgba[at + 2]
      counts.set(packed, (counts.get(packed) ?? 0) + 1)
    }
  }
  return counts
}

/** The widest channel of a box of colours, and how wide it is. */
function spread(box) {
  const low = [255, 255, 255]
  const high = [0, 0, 0]
  for (const { rgb } of box) {
    for (let channel = 0; channel < 3; channel += 1) {
      if (rgb[channel] < low[channel]) low[channel] = rgb[channel]
      if (rgb[channel] > high[channel]) high[channel] = rgb[channel]
    }
  }
  let widest = 0
  for (let channel = 1; channel < 3; channel += 1) {
    if (high[channel] - low[channel] > high[widest] - low[widest]) widest = channel
  }
  return { channel: widest, width: high[widest] - low[widest] }
}

/**
 * A palette by median cut.
 *
 * Median cut rather than a fixed web palette or a k-means: the input is a handful of designed
 * hues plus the grey ramp that antialiased text produces, and median cut spends its entries
 * where the colours actually are. Boxes are split at the *median by pixel count* rather than by
 * colour count, so a thousand pixels of one ivory outweigh a thousand distinct near-blacks that
 * cover a few hundred pixels between them.
 */
export function quantise(frames, max = COLOURS) {
  const counts = histogram(frames)
  const colours = [...counts].map(([packed, count]) => ({
    rgb: [(packed >> 16) & 255, (packed >> 8) & 255, packed & 255],
    count,
  }))

  // Few enough to keep every one, which is the common case for a flat UI and is exact rather
  // than merely good. Also the case the splitter below cannot handle gracefully: with two
  // colours there is one split to make and 253 entries left over.
  if (colours.length <= max) return colours.map(({ rgb }) => rgb)

  let boxes = [colours]
  while (boxes.length < max) {
    // Split the box with the widest channel, weighted by how many pixels it covers: a wide box
    // nobody can see is not worth an entry.
    let chosen = -1
    let best = 0
    boxes.forEach((box, index) => {
      if (box.length < 2) return
      const { width } = spread(box)
      const pixels = box.reduce((total, entry) => total + entry.count, 0)
      const score = width * Math.log2(pixels + 1)
      if (score > best) {
        best = score
        chosen = index
      }
    })
    if (chosen < 0) break

    const box = boxes[chosen]
    const { channel } = spread(box)
    box.sort((left, right) => left.rgb[channel] - right.rgb[channel])
    const half = box.reduce((total, entry) => total + entry.count, 0) / 2
    let carried = 0
    let at = 0
    // `at` is the last index of the left half, so it stops at `length - 2`: one step further
    // and the right half is empty, which is a box with no colours in it and an average of
    // NaN. Found exactly that way, by encoding a two-colour test pattern.
    while (at < box.length - 2 && carried + box[at].count < half) {
      carried += box[at].count
      at += 1
    }
    boxes = [...boxes.slice(0, chosen), box.slice(0, at + 1), box.slice(at + 1), ...boxes.slice(chosen + 1)]
  }

  const palette = boxes.map((box) => {
    const total = box.reduce((sum, entry) => sum + entry.count, 0)
    return [0, 1, 2].map((channel) =>
      Math.round(box.reduce((sum, entry) => sum + entry.rgb[channel] * entry.count, 0) / total),
    )
  })
  return palette
}

/** A lookup from a packed RGB to its palette index, memoised because most pixels repeat. */
function indexer(palette) {
  const known = new Map()
  return (r, g, b) => {
    const packed = (r << 16) | (g << 8) | b
    const held = known.get(packed)
    if (held !== undefined) return held
    let best = 0
    let distance = Infinity
    for (let index = 0; index < palette.length; index += 1) {
      const [pr, pg, pb] = palette[index]
      const delta = (pr - r) ** 2 + (pg - g) ** 2 + (pb - b) ** 2
      if (delta < distance) {
        distance = delta
        best = index
        if (delta === 0) break
      }
    }
    known.set(packed, best)
    return best
  }
}

/**
 * GIF's variable-width LZW, as the spec defines it.
 *
 * The one place a GIF encoder is genuinely fiddly, and the fiddliness is all in the code width:
 * it starts one bit above the minimum, grows by one each time the dictionary fills, and resets
 * on a clear code — and a decoder that disagrees by one bit produces a picture of static rather
 * than an error. The clear code is emitted first and again whenever the dictionary reaches
 * 4096 entries, which is the only bound the format has.
 */
function lzw(indexes, minimumCodeSize) {
  const clear = 1 << minimumCodeSize
  const end = clear + 1

  const bytes = []
  let held = 0
  let heldBits = 0
  let codeSize = minimumCodeSize + 1

  const emit = (code) => {
    held |= code << heldBits
    heldBits += codeSize
    while (heldBits >= 8) {
      bytes.push(held & 0xff)
      held >>= 8
      heldBits -= 8
    }
  }

  let dictionary = new Map()
  let next = end + 1
  const reset = () => {
    dictionary = new Map()
    next = end + 1
    codeSize = minimumCodeSize + 1
  }

  emit(clear)
  reset()

  let current = indexes[0]
  for (let at = 1; at < indexes.length; at += 1) {
    const value = indexes[at]
    const key = current * 4096 + value
    const found = dictionary.get(key)
    if (found !== undefined) {
      current = found
      continue
    }
    emit(current)
    if (next < 4096) {
      dictionary.set(key, next)
      next += 1
      if (next > 1 << codeSize && codeSize < 12) codeSize += 1
    } else {
      emit(clear)
      reset()
    }
    current = value
  }
  emit(current)
  emit(end)
  if (heldBits > 0) bytes.push(held & 0xff)

  // Sub-blocks of at most 255 bytes, each preceded by its length, terminated by a zero.
  const blocked = []
  for (let at = 0; at < bytes.length; at += 255) {
    const chunk = bytes.slice(at, at + 255)
    blocked.push(chunk.length, ...chunk)
  }
  blocked.push(0)
  return Buffer.from(blocked)
}

/** The rectangle in which two indexed frames differ, or null when they do not. */
function changed(previous, current, width, height) {
  let left = width
  let right = -1
  let top = height
  let bottom = -1
  for (let y = 0; y < height; y += 1) {
    const row = y * width
    for (let x = 0; x < width; x += 1) {
      if (previous[row + x] === current[row + x]) continue
      if (x < left) left = x
      if (x > right) right = x
      if (y < top) top = y
      if (y > bottom) bottom = y
    }
  }
  if (right < 0) return null
  return { left, top, width: right - left + 1, height: bottom - top + 1 }
}

/**
 * One looping GIF from a sequence of RGBA frames.
 *
 * `frames` is `[{ rgba, delayMs }]`, every one `width * height * 4` bytes, and `rgba` is read as
 * opaque — a screenshot has no alpha worth carrying and the one transparent index is spent on
 * frame differencing instead.
 */
export function writeGif({ width, height, frames, loop = 0 }) {
  if (frames.length === 0) throw new Error('a GIF needs at least one frame')
  for (const { rgba } of frames) {
    if (rgba.length !== width * height * 4) {
      throw new Error(`a frame is ${rgba.length} bytes, not ${width * height * 4}`)
    }
  }

  const palette = quantise(frames)
  const toIndex = indexer(palette)

  const indexed = frames.map(({ rgba }) => {
    const out = new Uint8Array(width * height)
    for (let at = 0, pixel = 0; at < rgba.length; at += 4, pixel += 1) {
      out[pixel] = toIndex(rgba[at], rgba[at + 1], rgba[at + 2])
    }
    return out
  })

  // 256 entries whatever the palette's real size: a GIF colour table is a power of two, and
  // padding it is cheaper than the arithmetic to pick the smallest one that fits.
  const table = Buffer.alloc(256 * 3)
  palette.forEach(([r, g, b], index) => {
    table[index * 3] = r
    table[index * 3 + 1] = g
    table[index * 3 + 2] = b
  })

  const parts = [Buffer.from('GIF89a', 'ascii')]

  const screen = Buffer.alloc(7)
  screen.writeUInt16LE(width, 0)
  screen.writeUInt16LE(height, 2)
  screen[4] = 0xf7 // global colour table, 8 bits per channel, 256 entries
  screen[5] = 0 // background index
  screen[6] = 0 // no pixel aspect ratio
  parts.push(screen, table)

  // NETSCAPE2.0, which is how a GIF says "loop". Nothing in the format proper does.
  const netscape = Buffer.alloc(19)
  netscape.write('\x21\xff\x0bNETSCAPE2.0', 0, 'binary')
  netscape[14] = 3
  netscape[15] = 1
  netscape.writeUInt16LE(loop, 16)
  netscape[18] = 0
  parts.push(netscape)

  let below = null
  for (let at = 0; at < frames.length; at += 1) {
    const full = indexed[at]
    const box =
      below === null
        ? { left: 0, top: 0, width, height }
        : (changed(below, full, width, height) ?? { left: 0, top: 0, width: 1, height: 1 })

    const pixels = new Uint8Array(box.width * box.height)
    for (let y = 0; y < box.height; y += 1) {
      for (let x = 0; x < box.width; x += 1) {
        const source = (box.top + y) * width + box.left + x
        pixels[y * box.width + x] =
          below !== null && below[source] === full[source] ? TRANSPARENT : full[source]
      }
    }

    const control = Buffer.alloc(8)
    control[0] = 0x21
    control[1] = 0xf9
    control[2] = 4
    // Disposal 1 — leave the frame in place — which is the half of frame differencing the
    // decoder has to agree to. With disposal 2 the transparent pixels would be cleared to the
    // background and the animation would strobe.
    control[3] = 0b0000_0101
    control.writeUInt16LE(Math.max(2, Math.round((frames[at].delayMs ?? 80) / 10)), 4)
    control[6] = TRANSPARENT
    control[7] = 0
    parts.push(control)

    const descriptor = Buffer.alloc(10)
    descriptor[0] = 0x2c
    descriptor.writeUInt16LE(box.left, 1)
    descriptor.writeUInt16LE(box.top, 3)
    descriptor.writeUInt16LE(box.width, 5)
    descriptor.writeUInt16LE(box.height, 7)
    descriptor[9] = 0 // no local colour table, not interlaced
    parts.push(descriptor, Buffer.from([8]), lzw(pixels, 8))

    below = full
  }

  parts.push(Buffer.from([0x3b]))
  return Buffer.concat(parts)
}
