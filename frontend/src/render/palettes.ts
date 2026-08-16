/**
 * Colour: floor styles, wall shading, and the per-person palettes derived from an id.
 *
 * Extracted from script section 8 of `company-os.html` rather than retyped, for the same reason
 * as the sprite grids: a wrong hex digit is invisible in review.
 *
 * Nobody's likeness is used. A person's skin, hair, top, trousers and shoes are all chosen from
 * their id's hash — the same hash the golden vectors pin against the kernel — so the cast is
 * visually distinct with no avatar assets and no photographs.
 */

import { idHash } from './palette'

/**
 * One room's floor.
 *
 * `a`, `b` and `c` are the base and its two speckle values; `seam` is the tile joint. `rug`
 * and `trim` are the department's own, and are the whole reason this shape changed.
 */
export interface FloorStyle {
  a: string
  b: string
  c: string
  seam: string
  /** The rug laid inside the room, or `null` for a corridor. */
  rug: string | null
  /** The department's identity, spent on a border course and a rug edge. */
  trim: string
}

/**
 * Floor palettes, keyed by a room's floor style.
 *
 * Inverted, and re-thought rather than re-tinted. Every one of these used to be a saturated
 * dark slab covering a whole room, which is how a department announced itself — and it is
 * also why the office read as seven coloured caves rather than as one lit floor with teams
 * on it.
 *
 * R10 asks for the department to arrive through rugs, trim and detail instead. So the base
 * is light in every room, the difference between rooms is a *tint* of the base rather than a
 * hue, and the identity is spent where identity belongs: a border course at the room's edge
 * and a rug in the middle of it, both in the department's own colour at full strength
 * because both are lines and shapes rather than fields.
 */
export const FLOORS: Record<string, FloorStyle> = {
  wood: {
    a: '#e8cfae',
    b: '#e0c49f',
    c: '#efd9bc',
    seam: '#c9a97f',
    rug: '#e3d3bd',
    trim: '#8a5a34', // 土色
  },
  blue: {
    a: '#eef4f8',
    b: '#e6eef4',
    c: '#f4f8fb',
    seam: '#cfe0ea',
    rug: '#dae8f2',
    trim: '#2376b7', // 花青
  },
  green: {
    a: '#eef7f2',
    b: '#e5f1ea',
    c: '#f4faf7',
    seam: '#cfe5da',
    rug: '#d8ece2',
    trim: '#189c7b', // 竹绿
  },
  slate: {
    a: '#f2f5f7',
    b: '#eaeff2',
    c: '#f8fafb',
    seam: '#dbe3e9',
    rug: '#e2e7ec',
    trim: '#475164', // 鲸鱼灰
  },
  violet: {
    a: '#f2f0f6',
    b: '#eae7f1',
    c: '#f8f6fb',
    seam: '#ddd8e8',
    rug: '#e4e0ee',
    trim: '#74759b', // 落电紫
  },
  amber: {
    a: '#fbf2e3',
    b: '#f5eada',
    c: '#fdf8ee',
    seam: '#e8d8bf',
    rug: '#f4e6d3',
    trim: '#aa6a4c', // 火泥棕
  },
  hall: {
    // Circulation, and it should read as circulation: the quietest floor in the building,
    // with no rug, because nobody sits here.
    a: '#f7fbf9',
    b: '#f1f6f3',
    c: '#fbfefc',
    seam: '#e2ebe6',
    rug: null,
    trim: '#c3d5de',
  },
}

/**
 * Walls are drawn 2.5D: a lit cap on top, a shaded face, a trim line at the floor.
 *
 * A light partition rather than a fortress. The old values were a slate block that read as
 * masonry, which is most of what made the office look like a corridor system rather than a
 * studio someone works in — R10 asks for architectural shells and this is the shell.
 *
 * The skirting is the change that matters most and is the least obvious. It used to be the
 * darkest value in the room and four pixels of it ran along the base of every wall, which
 * is a plinth. It is a trim line now: still the darkest of the five, but a *line*, so the
 * wall meets the floor instead of standing on a foundation.
 */
export const WALLC = {
  cap: '#eef7f2', // 月白 — the top surface, catching the light
  capLip: '#fffef8', // 象牙白 — the lit edge itself, one pixel
  face: '#d0dfe6', // 远天蓝 — the partition face
  faceLo: '#bccfda', // 远天蓝, shaded — the face falling away toward the floor
  base: '#93b5cf', // 星蓝 — trim where the wall meets the floor
} as const

/**
 * Glazing, and what is on the other side of it.
 *
 * The old window was a nine-by-twelve slot with a sky band. At 32 pixels a tile there is
 * room for a window someone would actually stand at, and R10 asks for daylight to come from
 * somewhere rather than from a lamp pool on the floor.
 */
export const GLASS = {
  frame: '#475164', // 鲸鱼灰 — the frame and the mullion
  sill: '#8a5a34', // 土色 — warm timber
  sillLit: '#a8703f', // 土色, lifted — the sill's top edge
  // Deliberately not `WALLC.base`. The two were the same value for a moment and the skirting
  // read as a strip of sky running around the bottom of the room.
  sky: '#7aa7c8', // 星蓝, deepened — sky at the top of the pane
  skyLow: '#c3d5de', // 远天蓝 — haze toward the horizon
  glint: '#fffef8', // 象牙白 — one highlight, so glass reads as glass
  leaf: '#189c7b', // 竹绿 — a plant on some sills
  spine: '#aa6a4c', // 火泥棕 — books on others
} as const

/**
 * Daylight, as it falls into the room from the glazing.
 *
 * Warm and additive-by-alpha rather than a `lighter` composite. On ink the lamp pools used
 * `lighter` because the ground had headroom; on a near-white floor the same operation blows
 * straight to pure white and the room loses its floor. So this is 淡桃红 laid over the floor
 * at a low alpha, which warms without brightening past the surface it is warming.
 *
 * Deliberately not amber. Sunlight and "a person is waiting on your decision" are the two
 * warmest things that can appear in this office, and only one of them is allowed to pull the
 * eye.
 */
export const DAYLIGHT = '#f6cec1'

/** How far into the room light reaches from a window, in tiles. */
export const DAYLIGHT_REACH = 6

/** How strong the light is directly at the glass. */
export const DAYLIGHT_STRENGTH = 0.3

export const SKINS: string[][] = [["#f6d3ae","#e2b087","#a06c46"],["#eec49a","#d3a173","#915c38"],["#d8a273","#bb8355","#7e4b2c"],["#b07d51","#94643c","#603c21"],["#8a5a37","#6f4527","#462b17"],["#5f3d26","#4a2d1a","#2d1b10"]]
export const HAIRS: string[][] = [["#6b4426","#8a5c34","#422915"],["#2f2a2f","#453d45","#1a171a"],["#d9a94e","#efc873","#a97c2c"],["#8f3f28","#ad5636","#5c2517"],["#a9a6a2","#c6c3bf","#726f6c"],["#3f6f7a","#54909c","#28474f"]]
export const TOPS: string[][] = [["#3f6fd8","#2f55a8","#1f3a75"],["#2f9e78","#22785b","#175340"],["#d8683f","#ad4f2e","#7a351d"],["#8b5cc7","#6d449e","#4b2e6e"],["#d24a4a","#a83636","#742323"],["#5f8f3a","#4a712c","#31491d"],["#e0b13f","#b88c2c","#80601c"],["#4a5568","#39424f","#262c36"]]
export const PANTS: string[][] = [["#4a5a78","#39465e","#26303f"],["#6b5340","#544032","#372a20"],["#3f4654","#313743","#20242c"]]
export const SHOES: string[][] = [["#4a3526","#2f2118"],["#3a3a42","#23232a"],["#5a3f2a","#38261a"]]

/**
 * The CEO's sprite id, matching the prototype's `drawSprite(S.ceo, 'you')`.
 *
 * Not on the roster: the CEO is the player, so they have no desk, no reporting line and no
 * `PersonSpec`. Everything keyed by person id — the sheet cache, the palette map — still keys
 * on this one, which is why it is a constant rather than a literal spelled in four places.
 */
export const CEO_ID = 'you'

/**
 * Hand-set exceptions, where the derived palette read badly for a specific person.
 *
 * The CEO's entry is not a legibility fix but R1: they wear the app's accent so they stay
 * findable among ten staff at a glance. Deriving their palette from `idHash('you')` like
 * everyone else would put them in whichever top the hash landed on, and the player would lose
 * themselves in a crowd. Overriding the three top shades rather than adding pixel rows keeps
 * the CEO on the same generated sheet as everyone else.
 */
export const PALETTE_OVERRIDE: Record<string, Record<string, string>> =
{
  [CEO_ID]: {
    "t": "#57c3c2",
    "T": "#3d9695",
    "u": "#276665"
  }
}

/**
 * A person's glyph palette, derived from their id.
 *
 * The divisors (7, 13, 31, 53, 97) are coprime-ish strides through the same hash, which is what
 * keeps skin, hair, top, trousers and shoes from correlating — two people with adjacent hashes
 * differ in several features rather than one.
 */
export function personPalette(id: string): Record<string, string | null> {
  const h = idHash(id)
  const skin = SKINS[h % SKINS.length]
  const hair = HAIRS[Math.floor(h / 7) % HAIRS.length]
  const top = TOPS[Math.floor(h / 13) % TOPS.length]
  const pant = PANTS[Math.floor(h / 31) % PANTS.length]
  const shoe = SHOES[Math.floor(h / 53) % SHOES.length]

  return {
    '.': null,
    j: hair[2],
    h: hair[0],
    H: hair[1],
    l: skin[2],
    s: skin[0],
    S: skin[1],
    w: '#f7f4ef',
    e: '#2b2430',
    c: '#dd8f84',
    u: top[2],
    t: top[0],
    T: top[1],
    p: pant[0],
    P: pant[1],
    b: shoe[0],
    B: shoe[1],
    ...(PALETTE_OVERRIDE[id] ?? {}),
  }
}

/** Roughly half the cast gets long hair, also from the hash. */
export function hasLongHair(id: string): boolean {
  return Math.floor(idHash(id) / 97) % 2 === 1
}
