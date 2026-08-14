/**
 * The HUD's rules, with no React in them.
 *
 * Split out of the component for two reasons that point the same way. The project's lint
 * config asks that a component file export only components, so that fast refresh works on it —
 * a module exporting both loses HMR for the component. And every rule below is decidable from
 * plain data, so keeping them here means the suite states them directly instead of rendering a
 * tile and reading its text, which would prove the JSX is wired rather than that falling manual
 * hours read as a win.
 *
 * Four decisions here are load-bearing, and each one is asserted rather than left to care.
 *
 * **Trajectories, not bare values.** A cash figure tells you where you are; the shape of the
 * last twenty sim-days tells you whether the thing you did worked. The bare value is the one
 * a spreadsheet gives you, and it is the one that makes a run feel like a dashboard rather
 * than a company.
 *
 * **Every metric declares its own favourable direction, and it comes off the wire.** Cash
 * rising and manual hours falling are both wins, and they have opposite signs. A uniform
 * rising-is-good rule would render the automation gain — the entire point of a run — as a
 * regression. The rule is the kernel's to state (`metric_defs.good` in the genesis payload),
 * and the HUD's to read.
 *
 * **Decision pressure renders in neutral chrome.** It aggregates exactly the fact the beam
 * signals — people are waiting on you — and the beam is the one hue the design system
 * reserves. Rendering the aggregate in amber too would spend the reserved signal on a
 * counter, and the counter would then compete with the actual people it is counting.
 *
 * **Runway and decision pressure cannot be removed.** Composition is otherwise the CEO's;
 * these two are the ones whose absence changes what a run means. Runway is how long you have,
 * and decision pressure is what is waiting — a HUD without them can show a company sliding
 * into insolvency with nine unread decisions and look calm.
 */

import { type Direction, type MetricDef, PAL, direction } from '../design/tokens'
import type { TrajectoryPoint } from '../net/store'

// =========================================================================
// Composition
// =========================================================================

/** The tiles that are not metrics. Metric tiles are named by their metric key. */
export const RUNWAY_TILE = 'runway'
export const PRESSURE_TILE = 'decisionPressure'
export const CAPACITY_TILE = 'capacity'

/**
 * The default arrangement.
 *
 * Runway leads because it is the number that bounds every other decision, and decision
 * pressure sits beside it because those two together are the whole "should I be worried"
 * question.
 */
export const DEFAULT_COMPOSITION: readonly string[] = [
  RUNWAY_TILE,
  PRESSURE_TILE,
  'cash',
  'manualHours',
  'leadTime',
  'morale',
  'visibility',
  CAPACITY_TILE,
] as const

/** The two whose absence changes what a run means. See the module note. */
export const NON_REMOVABLE: readonly string[] = [RUNWAY_TILE, PRESSURE_TILE] as const

/** Where the arrangement is kept. Client-side, per user, and never in the log. */
export const COMPOSITION_STORAGE_KEY = 'company-os.hud.composition'

export class TileNotRemovable extends Error {
  constructor(tile: string) {
    super(
      `${tile} cannot be removed from the HUD: a run can slide into insolvency with unread ` +
        `decisions and look calm without it`,
    )
    this.name = 'TileNotRemovable'
  }
}

/**
 * Drop a tile from the arrangement.
 *
 * Throws rather than silently ignoring: a control that appears to work and does nothing is
 * worse than one that refuses, because the CEO would conclude the tile is gone.
 */
export function removeTile(composition: readonly string[], tile: string): string[] {
  if (NON_REMOVABLE.includes(tile)) throw new TileNotRemovable(tile)
  return composition.filter((entry) => entry !== tile)
}

/** Put a tile back, in its default position relative to the tiles already present. */
export function addTile(composition: readonly string[], tile: string): string[] {
  if (composition.includes(tile)) return [...composition]
  const order = DEFAULT_COMPOSITION.indexOf(tile)
  if (order < 0) return [...composition, tile]
  const next = [...composition, tile]
  return next.sort(
    (left, right) => DEFAULT_COMPOSITION.indexOf(left) - DEFAULT_COMPOSITION.indexOf(right),
  )
}

/**
 * Read the stored arrangement, repairing anything unusable.
 *
 * A stored arrangement is user data from a previous version of the app, so it is validated
 * rather than trusted: unknown tiles are dropped, and the non-removable ones are restored if
 * an older build let them go. Falling back to the default on any parse failure is the right
 * call — losing a layout is a minor annoyance, refusing to render a HUD is not.
 */
export function loadComposition(storage: Pick<Storage, 'getItem'> | null): string[] {
  if (storage === null) return [...DEFAULT_COMPOSITION]

  let stored: string | null = null
  try {
    stored = storage.getItem(COMPOSITION_STORAGE_KEY)
  } catch {
    return [...DEFAULT_COMPOSITION]
  }
  if (stored === null) return [...DEFAULT_COMPOSITION]

  let parsed: unknown
  try {
    parsed = JSON.parse(stored)
  } catch {
    return [...DEFAULT_COMPOSITION]
  }
  if (!Array.isArray(parsed)) return [...DEFAULT_COMPOSITION]

  const known = parsed.filter(
    (entry): entry is string =>
      typeof entry === 'string' && DEFAULT_COMPOSITION.includes(entry),
  )
  let repaired = known
  for (const required of NON_REMOVABLE) {
    if (!repaired.includes(required)) repaired = addTile(repaired, required)
  }
  return repaired.length === 0 ? [...DEFAULT_COMPOSITION] : repaired
}

/** Persist the arrangement. Client-side only — this never reaches the log. */
export function saveComposition(
  storage: Pick<Storage, 'setItem'> | null,
  composition: readonly string[],
): void {
  if (storage === null) return
  try {
    storage.setItem(COMPOSITION_STORAGE_KEY, JSON.stringify(composition))
  } catch {
    // A full or blocked storage costs the CEO their layout on next load, and nothing else.
    // Failing the render over it would be a worse trade.
  }
}

// =========================================================================
// Reading a trajectory
// =========================================================================

/**
 * Which way a trajectory has gone, in this metric's own terms.
 *
 * One point is flat, not empty: a run that has just started has a real value and no history,
 * and rendering that as "no data" would make the first sim-day look broken. Zero points is
 * the only genuinely empty case.
 */
export function trajectoryDirection(
  metric: MetricDef,
  points: readonly TrajectoryPoint[],
): Direction {
  if (points.length < 2) return 'flat'
  return direction(metric, points[points.length - 1].value - points[0].value)
}

/**
 * The trajectory as a polyline in a 0..1 box, oldest on the left.
 *
 * A flat single point draws a line across the middle rather than a dot in a corner, so the
 * tile reads as "steady" rather than as "one sample".
 */
export function sparklinePoints(
  points: readonly TrajectoryPoint[],
  width: number,
  height: number,
): string {
  if (points.length === 0) return ''
  if (points.length === 1) {
    const mid = height / 2
    return `0,${mid} ${width},${mid}`
  }

  let low = points[0].value
  let high = points[0].value
  for (const point of points) {
    if (point.value < low) low = point.value
    if (point.value > high) high = point.value
  }
  const span = high - low

  return points
    .map((point, index) => {
      const x = (index / (points.length - 1)) * width
      // A perfectly flat series has no span to normalise against, so it sits on the midline
      // instead of dividing by zero and rendering nothing.
      const y = span === 0 ? height / 2 : height - ((point.value - low) / span) * height
      return `${x.toFixed(1)},${y.toFixed(1)}`
    })
    .join(' ')
}

// =========================================================================
// Derived tiles
// =========================================================================

/**
 * How many sim-days the cash lasts at the last reported daily cost.
 *
 * `null` means not yet knowable: before the first day's costs land there is no burn to divide
 * by, and rendering infinity would say "you have forever" at exactly the moment the answer is
 * unknown. Negative cash is already insolvent, so the runway is zero rather than negative.
 */
export function runwayDays(cash: number, dailyCost: number): number | null {
  if (dailyCost <= 0) return null
  if (cash <= 0) return 0
  return Math.floor(cash / dailyCost)
}

export interface Pressure {
  /** How many decisions are waiting on the CEO right now. */
  waiting: number
  /** The authored decision supply for the whole run, from genesis. */
  supply: number
  /** How long the longest-waiting decision has been waiting, in ticks. */
  oldestWaitTicks: bigint
}

export function decisionPressure(
  tray: ReadonlyArray<{ atTick: bigint }>,
  tick: bigint,
  supply: number,
): Pressure {
  let oldest = 0n
  for (const entry of tray) {
    const waited = tick > entry.atTick ? tick - entry.atTick : 0n
    if (waited > oldest) oldest = waited
  }
  return { waiting: tray.length, supply, oldestWaitTicks: oldest }
}

/**
 * The colour decision pressure renders in.
 *
 * Deliberately a function rather than a constant, so that "this is never the beam" is a
 * property a test states about the thing the component actually calls.
 */
export function pressureColour(pressure: Pressure): string {
  if (pressure.waiting === 0) return PAL.jingyuhui
  // Neutral chrome at every level. The escalation is in weight and in the count, not in hue.
  return PAL.yueyingbai
}
