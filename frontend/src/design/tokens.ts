/**
 * The art direction, as code.
 *
 * `docs/design/art-direction.html` is the approved specimen, and three client surfaces cite
 * it — the office renderer, the HUD, and the DAG with its chain strip. Three copies of the
 * same palette is three chances to drift, and the drift would be invisible in review: a
 * department stripe one value off reads as fine on its own and wrong beside the office. So
 * the slots live here once and every surface reads them.
 *
 * Two rules in here are load-bearing rather than decorative, and both are asserted in the
 * suites rather than left to discipline:
 *
 * **`BEAM` is reserved.** Amber means *a person is waiting on your decision*, and nothing
 * else may use it. Not a work item's status, not a warning, not ambient telemetry. The
 * product has exactly one signal that pulls the eye, and its value comes from being the
 * only one.
 *
 * **The load ramp must not pass through the beam.** The obvious green-to-red interpolation
 * runs through yellow, which would make a department at two-thirds load look like someone
 * waiting on you — spending the reserved signal on ambient capacity. The adopted ramp
 * drains saturation toward 苍绿 at the ceiling and brings warmth back as red, so the
 * midpoint reads as strain rather than as a signal.
 *
 * Names are the specimen's zhongguose names, kept so a value here is greppable in the doc
 * that approved it.
 */

/** Every colour the specimen names. Ground, semantic slots, room floors, chrome. */
export const PAL = {
  // Ground: the console the office sits in.
  ganglan: '#0f1423',
  yanhanlan: '#131824',
  gangqing: '#142334',
  qinghui: '#2b333e',
  jingyuhui: '#475164',
  yueyingbai: '#c0c4c3',
  xinghui: '#b2bbbe',

  // Semantic slots.
  shilv: '#57c3c2',
  cuilv: '#20a162',
  zhuhong: '#ed5126',
  zheshi: '#862617',
  beam: '#f2c46b',
  tacit: '#c4b1ff',

  // Room floors.
  tuose: '#66462a',
  lujiaozong: '#e3bd8d',
  huaqing: '#2376b7',
  dianqing: '#1661ab',
  zhulv: '#1ba784',
  luodianzi: '#74759b',
  huonizong: '#aa6a4c',
  canglv: '#223e36',

  // Chrome and text.
  mise: '#f9e9cd',
  yuebai: '#eef7f2',
  text: '#e9edf4',
  textMuted: '#a2abbb',
  textFaint: '#737d8e',
  rule: '#262b36',
} as const

/**
 * The one reserved value.
 *
 * Exported separately from `PAL.beam` so that "nothing else uses the beam" is a property a
 * test can state directly, rather than a comment somebody has to remember.
 */
export const RESERVED_BEAM = PAL.beam

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

/** Interactive, selected. Already means "live", which is why it carries in-progress. */
export const ACCENT = PAL.shilv

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
  meeting: PAL.qinghui,
  admin: PAL.qinghui,
  people: PAL.luodianzi,
  hr: PAL.luodianzi,
  support: PAL.huonizong,
  cs: PAL.huonizong,
}

/** The floor colour for a department, falling back to panel grey for an unknown room. */
export function deptColour(dept: string): string {
  return ROOM_FLOOR[dept] ?? PAL.qinghui
}

// =========================================================================
// The load ramp
// =========================================================================

/**
 * 竹绿 → 青矾绿 → 亚丁绿 → 苍绿 → 赭石 → 朱红.
 *
 * Saturation drains toward 苍绿 at the ceiling, then warmth returns as red. No step comes
 * near the beam, which is the whole point — see the module note.
 */
export const LOAD_RAMP: readonly string[] = [
  '#1ba784',
  '#2c9678',
  '#428675',
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

export type Direction = 'favourable' | 'unfavourable' | 'flat'

/**
 * Whether a movement is good news for this metric.
 *
 * The definition comes off the wire rather than being written down here. Cash rising and
 * manual hours falling are both wins, and they have opposite signs — a uniform
 * rising-is-good rule would render the automation gain, which is the entire point of a run,
 * as a regression. That rule is the kernel's to state, and the HUD's to read.
 */
export function direction(metric: MetricDef, delta: number): Direction {
  if (delta === 0) return 'flat'
  return Math.sign(delta) === Math.sign(metric.good) ? 'favourable' : 'unfavourable'
}

/** The hue a delta renders in. Never the beam — a metric movement is not a person waiting. */
export function directionColour(dir: Direction): string {
  if (dir === 'favourable') return OK
  if (dir === 'unfavourable') return ERR
  return PAL.jingyuhui
}
