/**
 * The cast's glyph contract: what a pixel of a person can be.
 *
 * Every cell of the rig and of the three feature libraries is an ASCII grid, and every glyph
 * in one of those grids is a *slot* rather than a colour. A slot is resolved per identity at
 * composition time, which is what lets one authored body wear eleven faces — the same trick
 * `paint()` has always done for the prototype's 10×16 cast, widened from sixteen glyphs to
 * what 48×64 actually needs.
 *
 * **Each slot also has a sentinel colour.** Art is authored as PNG, because that is what a
 * pixel artist works in and what the 33 approved candidates already are, and committed as
 * ASCII, because that is what a reviewer can read a diff of. `scripts/grid-from-png.mjs`
 * moves between the two, and the sentinels are what make that conversion exact rather than
 * approximate: a pixel is either one of these values or it is an error naming its coordinate.
 *
 * The sentinels are deliberately garish and deliberately far apart in RGB. They are never
 * seen — every one is substituted before anything reaches a canvas — so the only thing they
 * have to be is unmistakable for each other under an artist's eyedropper.
 */

/** One material a person is made of. */
export type Slot =
  | 'skin'
  | 'skinShade'
  | 'skinLine'
  | 'hair'
  | 'hairLight'
  | 'hairDark'
  | 'top'
  | 'topShade'
  | 'topLine'
  | 'legs'
  | 'legsShade'
  | 'shoes'
  | 'shoesShade'
  | 'outline'
  | 'eye'
  | 'mouth'
  | 'accent'

/**
 * Glyph to slot.
 *
 * Mnemonic where it can be — `s` skin, `h` hair, `t` top, `p` trousers, `b` boots — and
 * case-shifted for the lighter or darker sibling, which is the convention the prototype's
 * grids already used. Keeping it means an artist who has seen one of these files can read the
 * other.
 */
export const GLYPH_SLOTS: Record<string, Slot> = {
  s: 'skin',
  S: 'skinShade',
  l: 'skinLine',
  h: 'hair',
  H: 'hairLight',
  j: 'hairDark',
  t: 'top',
  T: 'topShade',
  u: 'topLine',
  p: 'legs',
  P: 'legsShade',
  b: 'shoes',
  B: 'shoesShade',
  k: 'outline',
  e: 'eye',
  m: 'mouth',
  a: 'accent',
}

/** Transparent. Not a slot, and never a colour. */
export const EMPTY = '.'

/** Every glyph a cast grid may contain. */
export const CAST_PALETTE = new Set([EMPTY, ...Object.keys(GLYPH_SLOTS)])

/**
 * The colour an artist paints a slot in, before it is a slot.
 *
 * Chosen to be mutually distant rather than to be pretty: two sentinels a few values apart
 * would let a lossy save, a wrong colour profile or a stray anti-aliased pixel land on the
 * wrong slot and produce art that is subtly, unfixably wrong. At these distances anything
 * that is not exact is an error, which is what the converter reports.
 */
export const SENTINELS: Record<Slot, string> = {
  skin: '#ff0000',
  skinShade: '#cc0000',
  skinLine: '#990000',
  hair: '#00ff00',
  hairLight: '#00cc00',
  hairDark: '#009900',
  top: '#0000ff',
  topShade: '#0000cc',
  topLine: '#000099',
  legs: '#ffff00',
  legsShade: '#cccc00',
  shoes: '#ff00ff',
  shoesShade: '#cc00cc',
  outline: '#000000',
  eye: '#00ffff',
  mouth: '#00cccc',
  accent: '#ff8800',
}

/** Sentinel colour back to the glyph that means it. Built once, used by the converter. */
export const SENTINEL_GLYPHS: Record<string, string> = Object.fromEntries(
  Object.entries(GLYPH_SLOTS).map(([glyph, slot]) => [SENTINELS[slot], glyph]),
)

/**
 * One character cell: 48 wide, 64 tall, transparent.
 *
 * The size the approved candidates were drawn at, and the size the office's 32-pixel tile was
 * chosen to host — a figure occupies roughly 22×62 of it, which is 0.7 tiles wide by 1.9
 * tall.
 */
export const CELL_WIDTH = 48
export const CELL_HEIGHT = 64

/** The row a person's feet stand on. The depth sort and the tile anchor both read this. */
export const FEET_ROW = CELL_HEIGHT - 1

/**
 * The three authored views.
 *
 * `left` is absent on purpose: it is `side` mirrored at composition time, so the two profiles
 * cannot drift into being two grids somebody has to keep pixel-identical by hand.
 */
export const VIEWS = ['down', 'up', 'side'] as const
export type View = (typeof VIEWS)[number]

/** Frame 0 is standing; 1 through 3 are the stride. */
export const CELL_FRAMES = 4

/**
 * The walk cycle, as indices into a view's frames.
 *
 * Contact, pass, contact, pass — so a stride alternates feet and returns through the same
 * middle pose, which is what makes four frames read as a walk rather than as a shuffle.
 * Standing is frame 0 and is not in the cycle.
 */
export const WALK_CYCLE = [1, 2, 3, 2] as const

/**
 * A person's six-slot palette, as extracted from a candidate.
 *
 * The seventeen slots above resolve from these six: shades and lines are derived, so casting
 * a person is choosing six colours rather than seventeen, and two people can never end up
 * with a highlight that does not belong to their own skin.
 */
export interface Skin {
  skin: string
  hair: string
  top: string
  legs: string
  shoes: string
  accent: string
}
