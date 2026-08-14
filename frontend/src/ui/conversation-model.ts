/**
 * Who the CEO is standing next to, and what the conversation says about them.
 *
 * A pure model, with no React and no canvas in it, for the reason the HUD and the panels split
 * the same way: the rules here are the mechanic. Proximity *is* the interface — being near
 * someone is the whole gesture, with no click and no keypress — so "who is in range" is worth
 * asserting directly rather than through a rendered panel.
 *
 * **Range is three numbers, not one.** A single radius makes two adjacent desks trade the
 * conversation back and forth every time the CEO breathes: standing exactly on the boundary,
 * one milli-tile of movement crosses it in both directions. So the radius to *open* at is
 * smaller than the radius to *close* at, and a second person has to be nearer by a margin
 * before they take the conversation over. The prototype's single 1.9 tiles is the open radius;
 * the other two are new, and they are what stops the flicker.
 *
 * **Both parties move.** Staff walk to desks and to meetings, so the person the CEO is talking
 * to can walk out of a conversation the CEO is standing perfectly still in. Range is recomputed
 * from both positions every frame rather than only when the CEO moves.
 */

import type { PersonView, RosterEntry } from '../net/store'

/** Within this, a conversation opens. The prototype's `TALK_RANGE`, in milli-tiles. */
export const OPEN_RADIUS_MILLI = 1900

/**
 * Beyond this, an open conversation closes.
 *
 * Wider than the open radius on purpose. With one radius, a CEO standing at 1.9 tiles opens and
 * closes the panel on every sub-tile step, which reads as a broken build rather than as a
 * boundary.
 */
export const CLOSE_RADIUS_MILLI = 2400

/**
 * How much nearer a second person must be before they take the conversation over.
 *
 * Two people at neighbouring desks are often within a few hundred milli-tiles of each other,
 * and without a margin the conversation swaps between them as the CEO shifts.
 */
export const SWITCH_MARGIN_MILLI = 500

/** Straight-line distance in milli-tiles. Presentation only — nothing here is hashed state. */
export function distanceMilli(
  a: { xMilli: number; yMilli: number },
  b: { xMilli: number; yMilli: number },
): number {
  return Math.hypot(a.xMilli - b.xMilli, a.yMilli - b.yMilli)
}

/**
 * Who the conversation is with, given where everyone is and who it was with last.
 *
 * `null` means nobody, which is a different thing from "the same person as before" and is why
 * the previous selection is an input rather than something this function could infer.
 */
export function selectConversation(
  ceo: { xMilli: number; yMilli: number },
  people: Record<string, PersonView>,
  current: string | null,
): string | null {
  let nearest: string | null = null
  let nearestDistance = Infinity

  for (const person of Object.values(people)) {
    const distance = distanceMilli(ceo, person)
    if (distance < nearestDistance) {
      nearestDistance = distance
      nearest = person.id
    }
  }

  if (nearest === null) return null

  const held = current === null ? undefined : people[current]
  if (held !== undefined) {
    const heldDistance = distanceMilli(ceo, held)

    // Still in the room. Keep them unless someone else is *decisively* nearer, so that two
    // people standing together do not trade the panel between them.
    if (heldDistance <= CLOSE_RADIUS_MILLI) {
      const stolen =
        nearest !== current &&
        nearestDistance <= OPEN_RADIUS_MILLI &&
        heldDistance - nearestDistance > SWITCH_MARGIN_MILLI
      return stolen ? nearest : current
    }
    // Out of range. Falls through rather than returning null outright: walking from one desk
    // straight to the next should open the next conversation, not close and wait for a step.
  }

  return nearestDistance <= OPEN_RADIUS_MILLI ? nearest : null
}

/** What a person is doing, in words rather than in the kernel's vocabulary. */
export const STATE_LABEL: Record<string, string> = {
  idle: 'Free right now',
  working: 'Working',
  blocked: 'Waiting on your decision',
  walking: 'Walking over',
  meeting: 'In a meeting',
}

/** The header every conversation carries, whatever card sits under it (R6). */
export interface ConversationHeader {
  id: string
  name: string
  initials: string
  title: string
  dept: string
  /** The kernel's state string, for anything that needs to branch on it. */
  state: string
  /** That state in words, for the panel. */
  stateLabel: string
  /** True when this person is stopped at a decision the CEO has to take. */
  waiting: boolean
  /** The item they are holding, or an empty string. */
  itemId: string
}

/**
 * The conversation header for a person, or `null` when there is nothing to talk to.
 *
 * Returns `null` rather than a placeholder when the roster or the live person is missing: a
 * conversation with a person the run does not have is not a degraded conversation, it is a bug,
 * and rendering "Unknown — Unknown" would hide it.
 */
export function conversationHeader(
  personId: string | null,
  roster: Record<string, RosterEntry>,
  people: Record<string, PersonView>,
): ConversationHeader | null {
  if (personId === null) return null

  const entry = roster[personId]
  const person = people[personId]
  if (entry === undefined || person === undefined) return null

  return {
    id: personId,
    name: entry.name,
    initials: entry.initials,
    title: entry.title,
    dept: entry.dept,
    state: person.state,
    stateLabel: STATE_LABEL[person.state] ?? person.state,
    waiting: person.waiting,
    itemId: person.itemId,
  }
}
