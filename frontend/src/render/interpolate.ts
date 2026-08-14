/**
 * Tick-space integer arithmetic, reimplemented on the client.
 *
 * This module is duplicated logic, deliberately. The client predicts the CEO's position
 * locally at zero latency and interpolates everyone else between authoritative ticks,
 * and both need the kernel's exact answer. The golden vectors in
 * `backend/tests/fixtures/golden/` are the only build-time guard on that duplication,
 * which is why they include tick indices above 2^53.
 *
 * **Everything here is `bigint`.** A tick index is a `uint64` on the wire, and `number`
 * silently loses precision above 2^53 — silently being the whole problem. Rendering
 * converts to `number` at the very end, after the integer maths is done.
 *
 * **Division floors, it does not truncate.** BigInt `/` rounds toward zero, while the
 * kernel's Python `//` rounds toward negative infinity. They agree on non-negative
 * values and disagree on negatives: `to_tile(-501)` is `-1` in Python and would be `0`
 * with a bare BigInt divide. The CEO's coordinates are positive in practice, but a
 * off-grid intermediate is one bug away, and a divergence of one tile between client and
 * kernel is exactly what the position echo would then flag as a mystery. `floorDiv`
 * removes the class of problem rather than relying on inputs staying positive.
 */

/** Walk speed: 7 tiles per 90 ticks. See `simcore/time.py` for the derivation. */
export const WALK_TILES_NUMERATOR = 7n
export const WALK_TILES_DENOMINATOR = 90n

/** Sub-tile positions are carried in thousandths of a tile. */
export const MILLI_TILES_PER_TILE = 1000n

export const TICKS_PER_SIM_HOUR = 60n
export const TICKS_PER_SIM_DAY = 540n
export const DAY_START_HOUR = 9n

/** The CEO's per-tick step, straight and diagonal, in milli-tiles. */
export const CEO_STRAIGHT_MILLI_PER_TICK = 144n
export const CEO_DIAGONAL_MILLI_PER_TICK = 102n
export const CEO_LOOKAHEAD_MILLI = 300n

/** Held-direction bitmask, matching the kernel's. */
export const INPUT_LEFT = 1
export const INPUT_RIGHT = 2
export const INPUT_UP = 4
export const INPUT_DOWN = 8

/**
 * Floor division, matching Python's `//`.
 *
 * BigInt `/` truncates toward zero; this rounds toward negative infinity, so the client
 * and the kernel agree for negative operands as well as positive ones.
 */
export function floorDiv(a: bigint, b: bigint): bigint {
  const quotient = a / b
  if (a % b !== 0n && a < 0n !== b < 0n) return quotient - 1n
  return quotient
}

/** Whole tiles covered after `elapsedTicks` of walking. */
export function tilesProgressed(elapsedTicks: bigint): bigint {
  if (elapsedTicks < 0n) throw new RangeError('sim-time does not run before tick 0')
  return floorDiv(elapsedTicks * WALK_TILES_NUMERATOR, WALK_TILES_DENOMINATOR)
}

/** Sub-tile progress in thousandths of a tile, for smooth interpolation. */
export function milliTilesProgressed(elapsedTicks: bigint): bigint {
  if (elapsedTicks < 0n) throw new RangeError('sim-time does not run before tick 0')
  return floorDiv(
    elapsedTicks * WALK_TILES_NUMERATOR * MILLI_TILES_PER_TILE,
    WALK_TILES_DENOMINATOR,
  )
}

/**
 * How many ticks it takes to walk `distanceTiles`, rounded up.
 *
 * Rounded up rather than down, because a floor would let an actor arrive before it had
 * covered the distance — visible on screen as sliding through the last tile.
 */
export function walkDurationTicks(distanceTiles: bigint): bigint {
  if (distanceTiles < 0n) throw new RangeError('distance cannot be negative')
  if (distanceTiles === 0n) return 0n
  const numerator = distanceTiles * WALK_TILES_DENOMINATOR
  // Ceiling division for positive operands.
  return floorDiv(numerator + WALK_TILES_NUMERATOR - 1n, WALK_TILES_NUMERATOR)
}

/** Round a milli-tile coordinate to the nearest tile. */
export function toTile(milli: bigint): bigint {
  return floorDiv(milli + MILLI_TILES_PER_TILE / 2n, MILLI_TILES_PER_TILE)
}

/** The 1-based day number containing `tick`. Tick 0 is day 1. */
export function dayOf(tick: bigint): bigint {
  if (tick < 0n) throw new RangeError('sim-time does not run before tick 0')
  return floorDiv(tick, TICKS_PER_SIM_DAY) + 1n
}

/** Clock time within the working day. */
export function hourMinuteOf(tick: bigint): { hour: bigint; minute: bigint } {
  if (tick < 0n) throw new RangeError('sim-time does not run before tick 0')
  const withinDay = tick % TICKS_PER_SIM_DAY
  return {
    hour: DAY_START_HOUR + floorDiv(withinDay, TICKS_PER_SIM_HOUR),
    minute: withinDay % TICKS_PER_SIM_HOUR,
  }
}

/** True on the tick a new day opens, including tick 0. */
export function isDayBoundary(tick: bigint): boolean {
  if (tick < 0n) throw new RangeError('sim-time does not run before tick 0')
  return tick % TICKS_PER_SIM_DAY === 0n
}

/**
 * The CEO's movement for one tick, given a held-direction bitmask.
 *
 * Collision is not handled here — it needs the floor, which the renderer owns. This is
 * the arithmetic half, and it must match the kernel exactly: the client's prediction is
 * always right precisely because the kernel applies the identical input at the identical
 * tick with these same constants.
 *
 * Diagonals use a smaller per-axis step than straight lines, so moving diagonally is not
 * faster than moving along an axis. The constant is `144/sqrt(2)` rounded, because the
 * exact value is irrational and could not agree bit-for-bit across two languages.
 */
export function ceoStep(bitmask: number): { dxMilli: bigint; dyMilli: bigint } {
  const dx = (bitmask & INPUT_RIGHT ? 1n : 0n) - (bitmask & INPUT_LEFT ? 1n : 0n)
  const dy = (bitmask & INPUT_DOWN ? 1n : 0n) - (bitmask & INPUT_UP ? 1n : 0n)

  if (dx === 0n && dy === 0n) return { dxMilli: 0n, dyMilli: 0n }

  const perAxis =
    dx !== 0n && dy !== 0n ? CEO_DIAGONAL_MILLI_PER_TICK : CEO_STRAIGHT_MILLI_PER_TICK

  return { dxMilli: dx * perAxis, dyMilli: dy * perAxis }
}
