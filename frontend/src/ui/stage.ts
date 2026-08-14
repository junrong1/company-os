/**
 * The stage's non-React parts: what the keyboard means, and what the renderer reads.
 *
 * Split from `Shell.tsx` so the component file exports only components. Both functions here are
 * worth stating on their own anyway — the bitmask is what the kernel replays position from, so a
 * wrong bit is a divergence rather than a cosmetic bug, and the actor projection is what the
 * frame loop calls sixty times a second.
 */

import type { Actor } from '../render/actors'
import { INPUT_DOWN, INPUT_LEFT, INPUT_RIGHT, INPUT_UP } from '../render/interpolate'
import { runState } from '../net/store'

/** Which view the stage is rendering. */
export type Stage = 'office' | 'dag'

/**
 * How far ahead of the rendered tick a CEO input is tagged.
 *
 * The delay absorbs jitter so client and kernel agree by construction: both apply the identical
 * input at the identical tick, so the client's prediction is always right and reconciliation
 * never fires in practice. Too small and the input arrives for a tick the kernel already ran;
 * too large and the controls feel detached.
 */
export const INPUT_LEAD_TICKS = 6n

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

/** The held-direction bitmask for a set of pressed keys. */
export function bitmaskFor(pressed: Iterable<string>): number {
  let mask = 0
  for (const key of pressed) {
    mask |= KEY_BITS[key] ?? 0
  }
  return mask
}

/**
 * The actors the renderer draws, read from the store.
 *
 * A plain function rather than a hook: this is called inside a `requestAnimationFrame`
 * callback, where hooks do not exist and a subscription would buy nothing — every notification
 * would tell the loop something its next frame was going to read anyway.
 */
export function actorsFromStore(): Actor[] {
  const state = runState()
  return Object.values(state.people).map((person) => ({
    id: person.id,
    xMilli: person.xMilli,
    yMilli: person.yMilli,
    facing: person.facing as Actor['facing'],
    moving: person.state === 'walking',
    // The walk frame is picked from elapsed ticks, and the render clock is the one that has to
    // drive it — the store's tick only moves when an event lands, which would freeze the cycle
    // mid-stride between events.
    animTicks: Number(state.tick),
    waiting: person.waiting,
  }))
}
