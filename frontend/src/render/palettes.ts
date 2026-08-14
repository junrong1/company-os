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

/** Floor palettes, keyed by a room's floor style. */
export const FLOORS: Record<string, { a: string; b: string; c: string; seam: string }> =
{
  "wood": {
    "a": "#8a6647",
    "b": "#7f5c3f",
    "c": "#745338",
    "seam": "#63472f"
  },
  "blue": {
    "a": "#3c4a63",
    "b": "#38455d",
    "c": "#425072",
    "seam": "#313c51"
  },
  "green": {
    "a": "#3a5a4b",
    "b": "#365446",
    "c": "#3f6151",
    "seam": "#2e4a3d"
  },
  "slate": {
    "a": "#454b5a",
    "b": "#414755",
    "c": "#4b5262",
    "seam": "#383d4a"
  },
  "violet": {
    "a": "#4a4265",
    "b": "#453e5f",
    "c": "#514870",
    "seam": "#3b3552"
  },
  "amber": {
    "a": "#5e4c36",
    "b": "#584833",
    "c": "#66533b",
    "seam": "#4b3d2c"
  },
  "hall": {
    "a": "#2f333d",
    "b": "#2c303a",
    "c": "#343943",
    "seam": "#262a33"
  }
}

/** Walls are drawn 2.5D: a lit cap on top, a shaded face, a dark skirting. */
export const WALLC = {
  "cap": "#767f92",
  "capLip": "#8d97ab",
  "face": "#4c5464",
  "faceLo": "#3d4453",
  "base": "#2b313d"
} as const

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
