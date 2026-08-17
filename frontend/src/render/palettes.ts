/**
 * Colour: the office's floors, its walls, its glazing and the light that falls through it.
 *
 * People used to live here too — skin, hair and clothing tables indexed by a hash of a
 * person's id. They moved to `cast/`, where a person is a rig wearing a manifest rather than
 * five array lookups, and the hash survives as the rule that dresses anyone the casting board
 * never met.
 *
 * Nobody's likeness is used, which was true of the hashed tables and is still true: the
 * shipped cast is original art wearing colours read out of original art.
 */

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

/**
 * The CEO's sprite id, matching the prototype's `drawSprite(S.ceo, 'you')`.
 *
 * Not on the roster: the CEO is the player, so they have no desk, no reporting line and no
 * scenario entry. Everything keyed by person id — the sheet cache, the manifest lookup — still
 * keys on this one, which is why it is a constant rather than a literal spelled in four
 * places.
 */
export const CEO_ID = 'you'
