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

  const actors: Actor[] = Object.values(state.people).map((person) => ({
    id: person.id,
    xMilli: person.xMilli,
    yMilli: person.yMilli,
    facing: person.facing as Facing,
    moving: person.state === 'walking',
    animTicks,
    waiting: person.waiting,
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
