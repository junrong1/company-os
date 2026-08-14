/**
 * The render clock: what the screen believes the time is.
 *
 * The authoritative clock lives in the kernel and arrives in bursts — a batch of ticks lands, then
 * nothing for a frame or two. Rendering directly from it makes actors jump. So the render clock is
 * a separate, smoothly-advancing value that *chases* the authoritative one.
 *
 * Four behaviours, and each exists because the naive version is visibly wrong:
 *
 * **It slews rather than jumping.** Snapping to each arriving tick makes every actor teleport a
 * few pixels on every batch. Slewing closes the gap over several frames, which reads as motion.
 *
 * **Pause clamps to the pause tick exactly.** Not "stops advancing" — clamps. If the render clock
 * were merely frozen wherever it happened to be, it could sit slightly ahead of the pause tick,
 * and actors would snap *backwards* on resume. That is worse than a stutter, because it looks like
 * the simulation lost work.
 *
 * **A large gap sets directly instead of slewing.** Slewing a thirty-day resync would render a
 * provably wrong world for minutes while it caught up. Past the threshold it is a reset, not a
 * chase.
 *
 * **Extrapolation is capped, then it freezes and says so.** Running ahead of the authority is how
 * smooth motion is possible at all, but running *far* ahead is fabricating a world. Past the
 * budget the clock stops and a stalled indicator becomes true, so the UI can say "the clock
 * stopped" rather than showing a confidently wrong office.
 *
 * The budget is sized by how often the authority actually speaks, which is not every tick. Events
 * are appended only on ticks that produce one, and a measured run emits on about five ticks in
 * twelve hundred; the regular signal is the position echo, once per sim-hour. A one-tick budget —
 * which is what this held while nothing fed the clock at all — stalls between every pair of
 * echoes and freezes the office for fifty-nine ticks in sixty.
 *
 * Everything is `bigint`, for the same reason as `interpolate.ts`: tick indices are `uint64` and
 * `number` loses precision above 2^53 silently.
 */

/** Beyond this gap, the clock resets instead of chasing. */
export const SLEW_THRESHOLD_TICKS = 120n

/** How much of the remaining gap one frame closes, in per-mille. */
export const SLEW_PER_MILLE = 250n

/**
 * How far the render clock may lead the last authoritative tick before it freezes.
 *
 * One and a half echo intervals. It has to exceed one interval or the clock stalls waiting for
 * a signal that is still coming; the half is slack for a slow frame and for the kernel's own
 * batching, which runs up to a 50 ms wake's worth of ticks at once. Past this the authority has
 * genuinely stopped talking, which is what the stall is for.
 */
export const MAX_EXTRAPOLATION_TICKS = 90n

/** Sim-ticks per wall millisecond at the base rate, as an exact rational (36/1000). */
export const TICKS_PER_WALL_MS_NUMERATOR = 36n
export const TICKS_PER_WALL_MS_DENOMINATOR = 1000n

export interface ClockView {
  /** The tick to render. */
  tick: bigint
  /** True when the clock has run out of extrapolation budget and stopped. */
  stalled: boolean
  /** The last tick the kernel actually confirmed. */
  authoritative: bigint
  rate: number
}

export class RenderClock {
  private renderTick: bigint
  private authoritativeTick: bigint
  private rateValue: number
  private pausedAtTick: bigint | null = null
  private stalledFlag = false
  /** Fractional wall-millisecond remainder, kept so slow frames do not lose time. */
  private remainderMs = 0n

  constructor(startTick: bigint = 0n, rate = 1) {
    this.renderTick = startTick
    this.authoritativeTick = startTick
    this.rateValue = rate
  }

  view(): ClockView {
    return {
      tick: this.renderTick,
      stalled: this.stalledFlag,
      authoritative: this.authoritativeTick,
      rate: this.rateValue,
    }
  }

  get tick(): bigint {
    return this.renderTick
  }

  get stalled(): boolean {
    return this.stalledFlag
  }

  get rate(): number {
    return this.rateValue
  }

  /** A tick the kernel has confirmed. Clears the stall, since the authority moved. */
  onAuthoritativeTick(tick: bigint): void {
    if (tick < this.authoritativeTick) return // out-of-order frame; the newest wins
    this.authoritativeTick = tick
    this.stalledFlag = false

    const gap = tick > this.renderTick ? tick - this.renderTick : this.renderTick - tick
    if (gap > SLEW_THRESHOLD_TICKS) {
      // Too far to chase. Slewing here would render a knowingly wrong world for minutes.
      this.renderTick = tick
      this.remainderMs = 0n
    }
  }

  /**
   * A rate change, carrying the tick it took effect at.
   *
   * Zero clamps the render clock to that exact tick. Anything else resumes from there.
   */
  onRateChange(rate: number, effectiveTick: bigint): void {
    this.rateValue = rate
    if (rate === 0) {
      this.pausedAtTick = effectiveTick
      this.renderTick = effectiveTick
      this.remainderMs = 0n
      this.stalledFlag = false
    } else {
      this.pausedAtTick = null
    }
  }

  /** A hard reset: the client was too far behind to catch up by replay. */
  onResync(tick: bigint): void {
    this.renderTick = tick
    this.authoritativeTick = tick
    this.remainderMs = 0n
    this.stalledFlag = false
    if (this.rateValue === 0) this.pausedAtTick = tick
  }

  /**
   * Advance by a frame's worth of wall time.
   *
   * The order here is the behaviour: paused clamps and returns; otherwise the clock advances by
   * elapsed time, then slews toward the authority, then has its lead capped.
   */
  advance(elapsedMs: number): void {
    if (this.rateValue === 0) {
      if (this.pausedAtTick !== null) this.renderTick = this.pausedAtTick
      return
    }

    const elapsed = BigInt(Math.max(0, Math.round(elapsedMs)))
    const scaled =
      elapsed * TICKS_PER_WALL_MS_NUMERATOR * BigInt(this.rateValue) + this.remainderMs
    const advanceBy = scaled / TICKS_PER_WALL_MS_DENOMINATOR
    this.remainderMs = scaled % TICKS_PER_WALL_MS_DENOMINATOR

    this.renderTick += advanceBy

    // Slew toward the authority: close a fraction of the gap each frame, so a batch of ticks
    // arriving at once is absorbed over several frames rather than snapping.
    if (this.authoritativeTick > this.renderTick) {
      const behind = this.authoritativeTick - this.renderTick
      this.renderTick += (behind * SLEW_PER_MILLE) / 1000n
    }

    // Cap the lead. Past one quantum the clock is fabricating a world, so it stops and says so.
    const ceiling = this.authoritativeTick + MAX_EXTRAPOLATION_TICKS
    if (this.renderTick > ceiling) {
      this.renderTick = ceiling
      this.stalledFlag = true
      this.remainderMs = 0n
    }
  }
}

/**
 * Whether the client's own prediction has diverged from the kernel's derived position (R33).
 *
 * Golden vectors prove agreement only for the cases someone thought to vector. This is the runtime
 * half: the kernel periodically echoes where it thinks the CEO is, and any disagreement is surfaced
 * rather than silently tolerated. A mis-recorded in-person resolution is the failure this catches.
 */
export function positionDiverged(
  predicted: { xMilli: bigint; yMilli: bigint },
  echoed: { xMilli: bigint; yMilli: bigint },
  toleranceMilli = 0n,
): boolean {
  const dx = predicted.xMilli > echoed.xMilli ? predicted.xMilli - echoed.xMilli : echoed.xMilli - predicted.xMilli
  const dy = predicted.yMilli > echoed.yMilli ? predicted.yMilli - echoed.yMilli : echoed.yMilli - predicted.yMilli
  return dx > toleranceMilli || dy > toleranceMilli
}
