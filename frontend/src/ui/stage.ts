/**
 * The stage's non-React parts: what the keyboard means, and what the renderer reads.
 *
 * Split from `Shell.tsx` so the component file exports only components. Both functions here are
 * worth stating on their own anyway — the bitmask is what the kernel replays position from, so a
 * wrong bit is a divergence rather than a cosmetic bug, and the actor projection is what the
 * frame loop calls sixty times a second.
 */

import type { Actor, Facing } from '../render/actors'
import { CEO_ID } from '../render/palettes'
import { type FloorData, buildGrid, walkable } from '../render/floor'
import { SLEW_THRESHOLD_TICKS } from '../render/clock'
import {
  CEO_LOOKAHEAD_MILLI,
  INPUT_DOWN,
  INPUT_LEFT,
  INPUT_RIGHT,
  INPUT_UP,
  ceoStep,
  toTile,
} from '../render/interpolate'
import { type CeoView, type PositionEcho, runState } from '../net/store'

/** Which view the stage is rendering. */
export type Stage = 'office' | 'dag'

/**
 * How far ahead of the rendered tick a CEO input is tagged, in *wall* milliseconds.
 *
 * The delay absorbs jitter so client and kernel agree by construction: both apply the identical
 * input at the identical tick, so the client's prediction is always right and reconciliation
 * never fires in practice. Too small and the input arrives for a tick the kernel already ran —
 * which is rejected, so the CEO simply does not move; too large and the controls feel detached.
 *
 * The budget it has to cover is wall time, not ticks: a round trip, plus the kernel's own
 * batching, which runs a 50 ms wake's worth of ticks in one go. A fixed *tick* lead therefore
 * shrinks in real terms exactly as the clock speeds up — six ticks is 167 ms at ×1 and 56 ms at
 * ×3, which no longer covers the batch. Holding the wall budget fixed instead keeps the margin
 * and keeps the felt latency the same at every rate.
 */
export const INPUT_LEAD_MS = 150n

/** Sim-ticks per wall millisecond at the base rate, as an exact rational. */
const TICKS_PER_WALL_MS_NUMERATOR = 36n
const TICKS_PER_WALL_MS_DENOMINATOR = 1000n

/** Never tag an input for the very next tick, however slowly the clock is running. */
export const MIN_INPUT_LEAD_TICKS = 4n

/**
 * Whether a rate change has to re-state the direction the player is already holding.
 *
 * A key held across a pause fires no fresh `keydown` on resume, so the held mask has not
 * changed and the "only send on a change" rule drops the one command that would get the CEO
 * walking again. The result is a CEO frozen until the player lets go and presses the same key,
 * which reads as the resume having failed rather than as an input rule.
 *
 * A pure predicate because the bug is silent by construction: it is easy to believe it is
 * fixed while nothing re-states anything.
 */
export function shouldRestateHeldInput(
  previousRate: number,
  nextRate: number,
  heldMask: number,
): boolean {
  return previousRate === 0 && nextRate !== 0 && heldMask !== 0
}

/** How many ticks ahead to tag an input, at this clock rate. */
export function inputLeadTicks(rate: number): bigint {
  const effective = BigInt(Math.max(1, Math.trunc(rate)))
  const ticks =
    (INPUT_LEAD_MS * TICKS_PER_WALL_MS_NUMERATOR * effective) / TICKS_PER_WALL_MS_DENOMINATOR
  return ticks < MIN_INPUT_LEAD_TICKS ? MIN_INPUT_LEAD_TICKS : ticks
}

/** Keys to the held-direction bitmask the kernel derives position from. */
export const KEY_BITS: Record<string, number> = {
  ArrowLeft: INPUT_LEFT,
  ArrowRight: INPUT_RIGHT,
  ArrowUp: INPUT_UP,
  ArrowDown: INPUT_DOWN,
  a: INPUT_LEFT,
  d: INPUT_RIGHT,
  w: INPUT_UP,
  s: INPUT_DOWN,
}

/**
 * Whether a key event belongs to a form control rather than to the stage.
 *
 * The tray's options are radios and the HUD composer's toggles are checkboxes, so Tab and the
 * arrow keys have a native meaning whenever one of them holds focus. The ask box makes this
 * load-bearing rather than merely polite: "why" and "who decides" are typed with W, A, S and D
 * in them, so a stage that took its keys unconditionally would walk the CEO out of the
 * conversation while the CEO was typing into it.
 */
export function typingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false
  const tag = target.tagName
  return (
    tag === 'INPUT' ||
    tag === 'TEXTAREA' ||
    tag === 'SELECT' ||
    tag === 'BUTTON' ||
    tag === 'SUMMARY' ||
    // Coerced rather than returned directly: `||` yields its last operand, and
    // `isContentEditable` is absent on some elements and in jsdom, so the declared `boolean`
    // return could hand back `undefined`. Harmless where the result is only ever tested for
    // truthiness, and a trap for anything that compares it.
    target.isContentEditable === true
  )
}

/** The held-direction bitmask for a set of pressed keys. */
export function bitmaskFor(pressed: Iterable<string>): number {
  let mask = 0
  for (const key of pressed) {
    mask |= KEY_BITS[key] ?? 0
  }
  return mask
}

/**
 * Where to draw the CEO, and whether their legs should be moving.
 *
 * Passed in rather than read from the store because the drawn position is a *prediction*: the
 * kernel echoes its own answer about once a sim-hour, which is far too rarely to render from.
 * The stage owns the prediction; the store owns what the wire said.
 */
export interface CeoPose {
  xMilli: number
  yMilli: number
  facing: Facing
  moving: boolean
}

/**
 * How many ticks of predicted positions to keep.
 *
 * An echo names the tick it was taken at, and the client is always somewhat ahead of that, so
 * comparing it against the position *now* would report divergence every time the CEO walked.
 * The comparison is against what this client predicted for that same tick, which means keeping
 * a little history. The echo arrives about once a sim-hour (60 ticks), so this is several
 * echoes' worth of slack.
 */
export const PREDICTION_HISTORY_TICKS = 256n

/**
 * The CEO's position, predicted locally and audited by the kernel.
 *
 * The kernel is authoritative and echoes its answer about once per sim-hour — far too rarely
 * to render from, which is why the client predicts. Prediction is exact rather than
 * approximate: the same integer arithmetic (`ceoStep`, golden-vectored against the kernel),
 * the same collision rule (`walkable`, rebuilt from the same recorded geometry), and the same
 * scheduling — an input applies at the tick it was tagged for and *stands* until superseded.
 * So reconciliation is a guard against a bug, not a routine correction, and a divergence is
 * worth surfacing rather than smoothing away.
 *
 * A class rather than module state: two of these must be able to exist at once without
 * touching each other, which is what makes it testable and what keeps a second run from
 * inheriting the first run's position.
 */
export class CeoPrediction {
  private xMilli = 0n
  private yMilli = 0n
  private facingNow: Facing = 'down'
  private grid: number[][] | null = null

  /** Held-direction masks by the tick they apply at. Mirrors the kernel's `ceo_inputs`. */
  private readonly scheduled = new Map<bigint, number>()
  /** Predicted positions by tick, for checking an echo against its own tick. */
  private readonly history = new Map<bigint, readonly [bigint, bigint]>()

  /** The last tick stepped. `null` until the first advance, which only establishes a start. */
  private appliedThrough: bigint | null = null
  private movingNow = false

  /** Give the prediction the floor it collides against. */
  useFloor(floor: FloorData | undefined): void {
    this.grid = floor === undefined ? null : buildGrid(floor)
  }

  /**
   * Start from an authoritative position: genesis' spawn, or a resync's snapshot.
   *
   * Clears the history, because positions predicted before a reseed describe a different
   * timeline and an echo must never be checked against one of them.
   */
  seed(ceo: CeoView, tick: bigint): void {
    this.xMilli = BigInt(Math.trunc(ceo.xMilli))
    this.yMilli = BigInt(Math.trunc(ceo.yMilli))
    this.facingNow = (['down', 'up', 'left', 'right'] as const).includes(ceo.facing as Facing)
      ? (ceo.facing as Facing)
      : 'down'
    this.appliedThrough = tick
    this.movingNow = false
    this.history.clear()
  }

  /**
   * Schedule a held direction, at the same tick the command sent to the kernel names.
   *
   * Applying it immediately instead would feel a few ticks sharper and would be wrong at every
   * one of them: the kernel applies it at `atTick`, so predicting it earlier guarantees the
   * disagreement that reconciliation exists to catch.
   */
  hold(mask: number, atTick: bigint): void {
    this.scheduled.set(atTick, mask)
  }

  /** Whether any direction is scheduled — used to re-state a held key across a pause. */
  get heldMask(): number {
    let latest: bigint | null = null
    for (const tick of this.scheduled.keys()) {
      if (latest === null || tick > latest) latest = tick
    }
    return latest === null ? 0 : (this.scheduled.get(latest) ?? 0)
  }

  /**
   * The held direction for `tick`: the most recent input at or before it.
   *
   * The kernel's `_held_bitmask`, ported. Superseded entries are dropped as they are passed,
   * for the same reason: ticks only move forward, so a past input can never apply again.
   */
  private heldAt(tick: bigint): number {
    let held: bigint | null = null
    for (const at of this.scheduled.keys()) {
      if (at <= tick && (held === null || at > held)) held = at
    }
    if (held === null) return 0

    for (const at of [...this.scheduled.keys()]) {
      if (at < held) this.scheduled.delete(at)
    }
    return this.scheduled.get(held) ?? 0
  }

  /** Advance the prediction to `tick`, one tick at a time. */
  advanceTo(tick: bigint): void {
    if (this.appliedThrough === null) {
      this.appliedThrough = tick
      return
    }
    if (tick <= this.appliedThrough) return

    // Too far to walk: a backgrounded tab or a resync leaves a gap of thousands of ticks, and
    // replaying them would burn a frame to arrive somewhere the next echo corrects anyway. The
    // render clock draws the same line at the same threshold.
    if (tick - this.appliedThrough > SLEW_THRESHOLD_TICKS) {
      this.appliedThrough = tick
      this.history.clear()
      return
    }

    for (let at = this.appliedThrough + 1n; at <= tick; at += 1n) this.stepOne(at)
    this.appliedThrough = tick
    this.prune(tick)
  }

  /** One tick of movement, matching `simcore.step._advance_ceo` axis for axis. */
  private stepOne(tick: bigint): void {
    const mask = this.heldAt(tick)
    const { dxMilli, dyMilli } = ceoStep(mask)
    this.movingNow = dxMilli !== 0n || dyMilli !== 0n

    if (this.movingNow) {
      const grid = this.grid

      if (dxMilli !== 0n) {
        const candidate = this.xMilli + dxMilli
        const lookahead = dxMilli > 0n ? CEO_LOOKAHEAD_MILLI : -CEO_LOOKAHEAD_MILLI
        const probe = toTile(candidate + lookahead)
        // No floor yet means nothing to collide with. Predicting movement anyway would be a
        // guess; standing still until genesis arrives is the honest reading.
        if (grid !== null && walkable(grid, Number(probe), Number(toTile(this.yMilli)))) {
          this.xMilli = candidate
        }
      }

      // Deliberately reads the x that the block above may just have changed, exactly as the
      // kernel does. Testing both axes against the pre-move position would let the CEO cut a
      // corner the kernel refuses, which is a divergence one diagonal at a wall away.
      if (dyMilli !== 0n) {
        const candidate = this.yMilli + dyMilli
        const lookahead = dyMilli > 0n ? CEO_LOOKAHEAD_MILLI : -CEO_LOOKAHEAD_MILLI
        const probe = toTile(candidate + lookahead)
        if (grid !== null && walkable(grid, Number(toTile(this.xMilli)), Number(probe))) {
          this.yMilli = candidate
        }
      }

      const ax = dxMilli < 0n ? -dxMilli : dxMilli
      const ay = dyMilli < 0n ? -dyMilli : dyMilli
      this.facingNow =
        ax > ay ? (dxMilli > 0n ? 'right' : 'left') : dyMilli > 0n ? 'down' : 'up'
    }

    this.history.set(tick, [this.xMilli, this.yMilli] as const)
  }

  private prune(tick: bigint): void {
    if (this.history.size <= Number(PREDICTION_HISTORY_TICKS)) return
    const oldest = tick - PREDICTION_HISTORY_TICKS
    for (const at of [...this.history.keys()]) {
      if (at < oldest) this.history.delete(at)
    }
  }

  /**
   * Check an echo against what this client predicted for that same tick.
   *
   * Returns whether the two disagreed. On a disagreement the prediction snaps to the kernel's
   * answer and re-walks the ticks since, so the correction lands without discarding input the
   * player has already given. Tolerance is zero on purpose: the arithmetic is integer and
   * identical on both sides, so any difference at all is a bug rather than drift, and a
   * tolerance would hide exactly the class of error this exists to find.
   */
  reconcile(echo: PositionEcho): boolean {
    const predicted = this.history.get(echo.tick)
    // Nothing predicted for that tick — before the first advance, or after a reseed dropped
    // the history. There is no disagreement to report, because there is no second opinion.
    if (predicted === undefined) return false

    const echoX = BigInt(Math.trunc(echo.xMilli))
    const echoY = BigInt(Math.trunc(echo.yMilli))
    if (predicted[0] === echoX && predicted[1] === echoY) return false

    const resumeAt = this.appliedThrough
    this.xMilli = echoX
    this.yMilli = echoY
    this.history.clear()
    this.appliedThrough = echo.tick
    // Re-walk what the player did since the echoed tick, so the snap corrects the error
    // without also discarding the input that followed it.
    if (resumeAt !== null) this.advanceTo(resumeAt)

    return true
  }

  /** Where to draw the CEO, and whether their legs should move. */
  pose(): CeoPose {
    return {
      xMilli: Number(this.xMilli),
      yMilli: Number(this.yMilli),
      facing: this.facingNow,
      moving: this.movingNow,
    }
  }
}

/**
 * The actors the renderer draws, read from the store.
 *
 * A plain function rather than a hook: this is called inside a `requestAnimationFrame`
 * callback, where hooks do not exist and a subscription would buy nothing — every notification
 * would tell the loop something its next frame was going to read anyway.
 *
 * The CEO joins the same list as the staff rather than being drawn afterwards, so one depth
 * sort covers everyone. Drawing them last would put them permanently in front of every desk,
 * including the ones they are standing behind.
 */
export function actorsFromStore(ceo?: CeoPose): Actor[] {
  const state = runState()

  // The walk frame is picked from elapsed ticks, and the render clock is the one that has to
  // drive it — the store's tick only moves when an event lands, which would freeze the cycle
  // mid-stride between events.
  const animTicks = Number(state.tick)

  // Who the kernel says is stopped, read off the tray. `person.waiting` is set at genesis and
  // by a resync and by nothing else — no event carries person state — so the beam that tells
  // the CEO somebody needs them would never light during a run (R20).
  const waiting = new Set(state.tray.map((entry) => entry.personId))

  const actors: Actor[] = Object.values(state.people).map((person) => ({
    id: person.id,
    xMilli: person.xMilli,
    yMilli: person.yMilli,
    facing: person.facing as Facing,
    moving: person.state === 'walking',
    animTicks,
    waiting: waiting.has(person.id) || person.waiting,
  }))

  // Nothing to draw before genesis: the floor has not arrived, so a CEO at the store's zeroed
  // default would be a figure standing in a corner of a room that does not exist yet.
  if (state.genesis === null) return actors

  // Falls back to the authoritative position when no prediction is running, which is what
  // makes the CEO appear at the right place on the very first frame after attaching — before
  // any key has been pressed and before the first echo has arrived.
  const pose: CeoPose = ceo ?? {
    xMilli: state.ceo.xMilli,
    yMilli: state.ceo.yMilli,
    facing: state.ceo.facing as Facing,
    moving: false,
  }

  actors.push({
    id: CEO_ID,
    xMilli: pose.xMilli,
    yMilli: pose.yMilli,
    facing: pose.facing,
    // The CEO has no `walking` state to derive this from — staff get theirs from the kernel,
    // and the CEO's movement is client-predicted — so it comes from whether a key is held.
    moving: pose.moving,
    animTicks,
    waiting: false,
  })

  return actors
}
