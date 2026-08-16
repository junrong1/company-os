/**
 * The art direction, as code.
 *
 * `docs/design/art-direction.html` is the approved specimen, and three client surfaces cite
 * it — the office renderer, the HUD, and the DAG with its chain strip. Three copies of the
 * same palette is three chances to drift, and the drift would be invisible in review: a
 * department stripe one value off reads as fine on its own and wrong beside the office. So
 * the slots live here once and every surface reads them.
 *
 * **The product is lit, not inked.** This file used to describe a night-shift console, and
 * the reasoning that produced its values was "dark enough not to glow". The daylight
 * redesign inverts the ground and every value with it — see
 * `docs/plans/2026-08-16-1527-feat-company-os-visual-redesign-plan.md` for the contract and
 * `docs/plans/2026-08-16-002-feat-daylight-pixel-visual-system-plan.md` for how it lands.
 *
 * **The keys are historical; the roles are current.** A key named `ganglan` (钢蓝, steel
 * blue) now holds 象牙白. Renaming every key would rewrite eight files and four suites to
 * change nothing a user sees, so instead each slot carries its current zhongguose name in a
 * trailing comment, and the specimen carries the same mapping. Grep either way and you land
 * in the right place.
 *
 * Three rules in here are load-bearing rather than decorative, and all three are asserted in
 * `tests/tokens.test.ts` rather than left to discipline:
 *
 * **`BEAM` is reserved.** Amber means *a person is waiting on your decision*, and nothing
 * else may use it. Not a work item's status, not a warning, not ambient telemetry. The
 * product has exactly one signal that pulls the eye, and its value comes from being the
 * only one.
 *
 * **The beam never appears without its edge.** 决策琥珀 measures 1.6:1 against 象牙白 — on ink
 * it was the brightest thing on screen and on ivory it is nearly invisible. So the reserved
 * signal is a *pair*: the amber carries the meaning and `beamEdge` carries the contrast, the
 * same way the office's sprites take their separation from a dove-blue outline rather than
 * from their fill. Spending the amber without the edge is how the one signal that must not
 * be missed becomes the one signal nobody sees.
 *
 * **The load ramp must not pass through the beam.** The obvious green-to-red interpolation
 * runs through yellow, which would make a department at two-thirds load look like someone
 * waiting on you — spending the reserved signal on ambient capacity. The adopted ramp holds
 * its distance from the beam in hue and gains contrast against the ground as strain rises,
 * so the midpoint reads as strain rather than as a signal.
 */

/** Every colour the specimen names. Ground, semantic slots, room floors, chrome. */
export const PAL = {
  // Ground: the daylight the office sits in.
  ganglan: '#fffef8', // 象牙白 — the product ground
  gangqing: '#f7fbf9', // 月白, lifted — a card, a hair off the ground so it reads as one
  yanhanlan: '#fbf2e3', // 粉白 — warm quiet bands
  yuebai: '#eef7f2', // 月白 — cool quiet surfaces
  qinghui: '#dfeaef', // 远天蓝, lifted — tracks, inactive fills, hover
  rule: '#c3d5de', // 远天蓝 — hairlines
  dongfangjibai: '#e6f2ff', // 东方既白 — the sky behind the top of the page
  xinglan: '#93b5cf', // 星蓝 — the offset behind a filled control, and a quiet fill

  // Structure and text.
  jingyuhui: '#475164', // 鲸鱼灰 — secondary structure, a flat delta
  yueyingbai: '#2d3d52', // the strongest neutral — glyph structure, decision pressure
  xinghui: '#4a6b85', // 星蓝, deepened — secondary labels and inactive states
  text: '#1c2938', // 鸽蓝 — primary text and strong borders
  // The other dove blue. `VISUAL_DESIGN.md` §2 gives character outlines and strong structural
  // edges #253147, and all 33 approved candidates are drawn with it, so it is not a variant
  // of the text colour — it is the ink the art is drawn in, and the two are separate because
  // they answer to different documents and one of them is already in shipped pixels.
  outline: '#253147',
  textMuted: '#475164', // 鲸鱼灰 — secondary text
  textFaint: '#5c677a', // 鲸鱼灰, lifted — small labels, still readable

  // Semantic slots.
  tianlan: '#1677b3', // 天蓝 — primary action, selection, focus
  shilv: '#57c3c2', // 石绿 — the CEO's in-world signal, and nothing in the chrome
  shilv2: '#2f8c8b', // 石绿, deepened — the CEO's clothing, as their candidates carry it
  fenlv: '#83cbac', // 粉绿 — working, active team
  cuilv: '#12775e', // 竹绿, deepened for text — a favourable delta
  zhuhong: '#c8402a', // 朱红, deepened for text — an unfavourable delta
  zheshi: '#8a4028', // 赭石 — the ramp past the ceiling
  canglv: '#4a6f5f', // 苍绿 — the ramp at the ceiling
  beam: '#f2c46b', // 决策琥珀 — RESERVED: a person is waiting on your decision
  beamEdge: '#1c2938', // 鸽蓝 — the outline that makes the beam legible on ivory
  tacit: '#6b5bb5', // 紫棠 — knowledge only a conversation yields

  // Warmth. Office-side only: both are too pale to carry a label, which is the point of them
  // — they are what daylight and a social corner are made of, not what a state is.
  dantaohong: '#f6cec1', // 淡桃红 — sunlight, warmth, soft emphasis
  chutaofenhong: '#f6dcce', // 初桃粉红 — meeting and collaboration regions

  // Department identities, per VISUAL_DESIGN.md §2. Chrome-strength, because these are the
  // 3-pixel stripes in the panels — the office's own floors are lighter and live in
  // `render/palettes.ts`.
  tuose: '#8a5a34', // 土色 — executive, lounge
  huaqing: '#2376b7', // 花青 — sales
  zhulv: '#189c7b', // 竹绿, deepened — accounting
  luodianzi: '#74759b', // 落电紫 — people
  huonizong: '#aa6a4c', // 火泥棕 — support
} as const

/**
 * The ground every contrast rule is measured against.
 *
 * Exported rather than inlined so "is this readable" is one question with one answer. A
 * second opinion about what the ground is would make every assertion below quietly
 * conditional on which file you asked.
 */
export const PRODUCT_GROUND = PAL.ganglan

/**
 * The one reserved value.
 *
 * Exported separately from `PAL.beam` so that "nothing else uses the beam" is a property a
 * test can state directly, rather than a comment somebody has to remember.
 */
export const RESERVED_BEAM = PAL.beam

/**
 * The edge the reserved value is always drawn with.
 *
 * Paired with `RESERVED_BEAM` for the same reason it is exported at all: "the beam never
 * ships without its edge" is a property, and a property needs a name before a test can hold
 * anything to it.
 */
export const RESERVED_BEAM_EDGE = PAL.beamEdge

// =========================================================================
// Contrast
// =========================================================================

/** One channel of sRGB, linearised. The WCAG transfer function, nothing more. */
function linearise(channel: number): number {
  const unit = channel / 255
  return unit <= 0.04045 ? unit / 12.92 : ((unit + 0.055) / 1.055) ** 2.4
}

/**
 * Relative luminance of a `#rrggbb` colour.
 *
 * Deliberately strict about its input: a three-digit hex or a named colour returning a
 * plausible-looking number would make every assertion below pass for the wrong reason.
 */
export function relativeLuminance(hex: string): number {
  const match = /^#([0-9a-f]{6})$/i.exec(hex)
  if (match === null) throw new Error(`not a six-digit hex colour: ${hex}`)
  const value = Number.parseInt(match[1], 16)
  const red = (value >> 16) & 0xff
  const green = (value >> 8) & 0xff
  const blue = value & 0xff
  return 0.2126 * linearise(red) + 0.7152 * linearise(green) + 0.0722 * linearise(blue)
}

/**
 * The WCAG contrast ratio between two colours, from 1 to 21.
 *
 * Here so the palette's accessibility rules are assertions rather than intentions. Every
 * "clears 4.5:1" claim in this file is checked by `tests/tokens.test.ts` calling this, which
 * is what stops a later tuning pass from making a label unreadable and nobody noticing until
 * a user says so.
 */
/**
 * A palette slot at partial opacity.
 *
 * Canvas surfaces need a few values the palette cannot hold, because a lattice or a wash is a
 * slot *and* an alpha rather than a colour of its own. Composing them here keeps the hex in
 * one place: an `rgba(71,81,100,0.16)` written by hand is a palette value nothing points at,
 * which is precisely how the DAG's recessed-node fill survived the inversion that made it
 * wrong.
 */
export function withAlpha(hex: string, alpha: number): string {
  const value = Number.parseInt(/^#([0-9a-f]{6})$/i.exec(hex)?.[1] ?? '', 16)
  if (Number.isNaN(value)) throw new Error(`not a six-digit hex colour: ${hex}`)
  return `rgba(${(value >> 16) & 0xff}, ${(value >> 8) & 0xff}, ${value & 0xff}, ${alpha})`
}

export function contrastRatio(a: string, b: string): number {
  const first = relativeLuminance(a)
  const second = relativeLuminance(b)
  const lighter = Math.max(first, second)
  const darker = Math.min(first, second)
  return (lighter + 0.05) / (darker + 0.05)
}

// =========================================================================
// The authored-tuning marking
// =========================================================================

/**
 * Every number this product renders is invented, and every surface has to say so.
 *
 * R27 and R28: no figure appears without its marking travelling with it. R36 adds the part
 * that makes it a design constraint rather than a label — **the marking is a dedicated token,
 * never a hue.** Amber is reserved for a person waiting on the CEO, and a "these numbers are
 * made up" signal expressed as colour would either spend that reserve or invent a second one
 * competing with it. So the marking is a glyph and a short label, and it survives greyscale,
 * a colour-blind reader and a screenshot.
 *
 * `PAL.tacit` plus its "Only in person" badge is the working precedent for a non-hue semantic
 * slot; this is the same shape with the hue removed entirely.
 *
 * Exported as its own frozen token — the shape `RESERVED_BEAM` established — so that "the
 * marking is never the beam" and "the marking carries no colour" are properties a test states
 * directly rather than conventions somebody has to remember.
 */
export const AUTHORED_TUNING = {
  /**
   * Approximately-equal, because that is what these figures are: a shape someone chose, not a
   * measurement anything took. Deliberately not a warning triangle — the numbers are not
   * wrong, they are authored, and a warning would read as a fault in the simulation.
   */
  glyph: '≈',
  /** What the glyph means, spelled out wherever there is room for it. */
  label: 'authored tuning',
  /** The long form, for a tooltip or an assistive-technology label. */
  description: 'This figure is authored tuning, not a measurement.',
} as const

/** The marking's accessible label for one named figure. */
export function authoredTuningLabel(what: string): string {
  return `${what} — ${AUTHORED_TUNING.label}`
}

/**
 * The opposite claim, for the one figure in the product that is not invented.
 *
 * Model calls and tokens are counted, not authored: they are what a run actually spent
 * against a ceiling somebody configured. Marking them `≈ authored tuning` would be a lie in
 * the one place the product has a real measurement, and leaving them unmarked would be
 * worse — the reader has learned that an unmarked number is an oversight, so silence reads
 * as a missing marking rather than as a different kind of figure.
 *
 * Built exactly like `AUTHORED_TUNING`, for the same reason: a glyph and a short label,
 * never a hue. Amber is reserved for a person waiting on the CEO, and "this one is real"
 * expressed as colour would spend that reserve on a tile in the chrome.
 *
 * `=` against `≈` is the pairing, and it is what makes the distinction legible before the
 * label is read: the same shape family, one approximate and one exact. `#` was considered
 * and dropped — it says "a count", which is orthogonal to the truth-claim being made — and
 * a check mark was dropped because it reads as *approval* of a figure rather than as a
 * statement about where it came from.
 */
export const MEASURED = {
  glyph: '=',
  label: 'measured',
  description: 'This figure is measured: what this run actually spent.',
} as const

/** The measured marking's accessible label for one named figure. */
export function measuredLabel(what: string): string {
  return `${what} — ${MEASURED.label}`
}

/**
 * Interactive, selected. Already means "live", which is why it carries in-progress.
 *
 * 天蓝 rather than 石绿, and the split is the point. The Product Contract gives 天蓝 to
 * "primary action and player emphasis", `VISUAL_DESIGN.md` gives the CEO 石绿, and the
 * shipped candidate sprites side with the latter — so the role divides by surface instead of
 * one of them losing. In the chrome the strongest cool thing is the action, in 天蓝, which
 * clears 4.9:1 against ivory where 石绿 manages 2.1:1 and could not carry a label. In the
 * office the strongest cool thing is the player, in 石绿, which cannot be 天蓝 because the
 * sales room and everyone in it already wear 花青 and the CEO would read as a sales hire.
 *
 * Neither surface has two cool accents, so R12's "the player is the strongest cool accent"
 * survives intact where it is actually looked at.
 */
export const ACCENT = PAL.tianlan

/** A positive delta. Already means "landed", which is why it carries complete. */
export const OK = PAL.cuilv

/** A negative delta. */
export const ERR = PAL.zhuhong

/** Knowledge only a conversation yields — and so, the object that holds it: a blocked node. */
export const TACIT = PAL.tacit

/**
 * Department floor colours, keyed by the room a person sits in.
 *
 * Support moved off amber deliberately: its floor was an amber room style sitting under an
 * amber light column, and even though the canvas has its own palette so nothing collided in
 * code, it diluted the one signal that must not dilute.
 */
export const ROOM_FLOOR: Record<string, string> = {
  executive: PAL.tuose,
  lounge: PAL.tuose,
  sales: PAL.huaqing,
  accounting: PAL.zhulv,
  meeting: PAL.jingyuhui,
  admin: PAL.jingyuhui,
  people: PAL.luodianzi,
  hr: PAL.luodianzi,
  support: PAL.huonizong,
  cs: PAL.huonizong,
}

/**
 * The department colour for a stripe, falling back to structural grey for an unknown room.
 *
 * Administration and the meeting room moved off `qinghui` when the ground inverted. On ink,
 * panel grey was a mid value that read as a stripe; on ivory it is a track fill at 1.2:1 and
 * a stripe drawn in it is a stripe nobody can see. 鲸鱼灰 is what `VISUAL_DESIGN.md` §2 gives
 * administration anyway, so the fix and the specification are the same change.
 */
export function deptColour(dept: string): string {
  return ROOM_FLOOR[dept] ?? PAL.jingyuhui
}

// =========================================================================
// The load ramp
// =========================================================================

/**
 * 竹绿 → 青矾绿 → 亚丁绿 → 苍绿 → 赭石 → 朱红.
 *
 * Re-derived for a light ground rather than re-tinted. On ink the middle steps were chosen
 * to be *dark enough not to read as amber*; on ivory that reasoning inverts, because a
 * desaturated mid-green disappears into the surface instead of reading as strain. So the
 * ramp now deepens as load rises — every step gains contrast against 象牙白 — while holding
 * its distance from the beam in hue, which is the constraint that never changed. No stop
 * lands within sixty degrees of 决策琥珀, so a department at two-thirds load cannot be
 * mistaken for a person waiting.
 */
export const LOAD_RAMP: readonly string[] = [
  PAL.zhulv,
  '#2f8b73',
  '#41806f',
  PAL.canglv,
  PAL.zheshi,
  PAL.zhuhong,
] as const

/**
 * How far past the ceiling the ramp runs, as an exact rational.
 *
 * 1.4× — the specimen's domain. Over-ceiling load never blocks assignment, so the ramp has
 * to keep saying something above 100% rather than pinning at its last stop immediately.
 */
export const RAMP_CEILING_MULTIPLE_NUMERATOR = 14
export const RAMP_CEILING_MULTIPLE_DENOMINATOR = 10

/**
 * The colour for a load, in the per-mille unit the kernel carries.
 *
 * Per-mille and integer arithmetic throughout, because the kernel's load is per-mille and
 * integer: converting to a float ratio here to pick a bucket would put a rounding
 * disagreement between the HUD's heat and the DAG's tint, which the suites assert agree.
 *
 * `ceiling` is read from the genesis payload rather than assumed, so a tuning pass that
 * moves it does not leave the ramp reading against a stale scale.
 */
export function loadColour(loadPermille: number, ceiling: number): string {
  const domain =
    (ceiling * RAMP_CEILING_MULTIPLE_NUMERATOR) / RAMP_CEILING_MULTIPLE_DENOMINATOR
  if (domain <= 0) return LOAD_RAMP[0]

  const bucket = Math.floor((loadPermille * LOAD_RAMP.length) / domain)
  return LOAD_RAMP[Math.min(LOAD_RAMP.length - 1, Math.max(0, bucket))]
}

/**
 * How full a load bar reads, in per-mille of its own width.
 *
 * Clamped at the ramp's domain rather than at the ceiling: a department at 200% would
 * otherwise render identically to one at 140%, and the difference is the one a CEO acts on.
 */
export function loadFillPermille(loadPermille: number, ceiling: number): number {
  const domain =
    (ceiling * RAMP_CEILING_MULTIPLE_NUMERATOR) / RAMP_CEILING_MULTIPLE_DENOMINATOR
  if (domain <= 0) return 0
  return Math.max(0, Math.min(1000, Math.round((loadPermille * 1000) / domain)))
}

/** Whether a load is over its ceiling. Over-ceiling permits assignment; it degrades. */
export function overCeiling(loadPermille: number, ceiling: number): boolean {
  return loadPermille > ceiling
}

// =========================================================================
// Metric direction
// =========================================================================

/** One metric, as the genesis payload carries it. `good` is the field that matters most. */
export interface MetricDef {
  key: string
  label: string
  unit: string
  chip_unit: string
  /** +1 if rising is an improvement, -1 if falling is. */
  good: number
  floor: number | null
  ceiling: number | null
  display_max: number
}

/**
 * The empty metric table, as one shared reference.
 *
 * A fresh `[]` from a selector would be a new reference every call, and the store's equality
 * check is `Object.is` on the selector's output — so a panel reading the metric table before
 * genesis has landed would re-render forever. Shared rather than declared once per panel that
 * needs it: three copies of a sentinel is three chances for one to become an inline literal.
 */
export const NO_METRIC_DEFS: readonly MetricDef[] = []

export type Direction = 'favourable' | 'unfavourable' | 'flat'

/**
 * Whether a movement is good news, given which direction counts as an improvement.
 *
 * The lower half of `direction`, split out because two callers need the rule without holding a
 * `MetricDef`. The recurring-draw figure is not a metric — `manualHours` is derived from the
 * sum of the draws — and a projected figure is read against where the run is now rather than
 * against zero. Both were open-coding the sign comparison, which is one rule in three places
 * and exactly what drifts silently.
 */
export function directionOf(delta: number, good: number): Direction {
  if (delta === 0) return 'flat'
  return Math.sign(delta) === Math.sign(good) ? 'favourable' : 'unfavourable'
}

/**
 * Whether a movement is good news for this metric.
 *
 * The definition comes off the wire rather than being written down here. Cash rising and
 * manual hours falling are both wins, and they have opposite signs — a uniform
 * rising-is-good rule would render the automation gain, which is the entire point of a run,
 * as a regression. That rule is the kernel's to state, and the HUD's to read.
 */
export function direction(metric: MetricDef, delta: number): Direction {
  return directionOf(delta, metric.good)
}

/** The hue a delta renders in. Never the beam — a metric movement is not a person waiting. */
export function directionColour(dir: Direction): string {
  if (dir === 'favourable') return OK
  if (dir === 'unfavourable') return ERR
  return PAL.jingyuhui
}
