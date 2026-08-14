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

import type { CatalogEntry, PersonView, RosterEntry, TrayEntry } from '../net/store'

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

// =========================================================================
// The stopped card: a decision taken in person
// =========================================================================

/** The key a raised checkpoint's tacit line is held under. */
export function tacitKey(itemId: string, cpIndex: number): string {
  return `${itemId}:${cpIndex}`
}

/**
 * What each route costs, stated before the choice rather than after it (R10).
 *
 * The kernel prices both — in person yields the tacit line, morale +2 and visibility +2; from
 * the tray, morale −1 and no tacit line — and these sentences are that price in words. A cost
 * you only discover in the report is not a choice you were offered.
 */
export const IN_PERSON_COST = 'Deciding here surfaces what they know, and they will remember it.'
export const FROM_TRAY_COST =
  'Settling from the tray records no tacit line, and it costs morale.'

export interface DecisionOption {
  label: string
  detail: string
}

/** A person stopped at a decision, as the conversation shows it. */
export interface StoppedCard {
  itemId: string
  itemTitle: string
  cpIndex: number
  label: string
  prompt: string
  options: DecisionOption[]
  /**
   * The line they say only in person. Empty when the kernel has not sent one, which is a
   * missing line rather than a reason to hide the decision.
   */
  tacit: string
  cost: string
}

/**
 * The decision this person is stopped at, or `null` when they are not stopped at one.
 *
 * Reads the raised checkpoint out of the tray — the same list the tray panel renders — because
 * that is what the kernel actually said is waiting, and re-deriving "which checkpoint are they
 * at" from progress would be a second implementation of the kernel's threshold rule.
 */
export function stoppedCard(
  personId: string | null,
  tray: TrayEntry[],
  catalog: CatalogEntry[],
  tacitLines: Record<string, string>,
): StoppedCard | null {
  if (personId === null) return null

  const entry = tray.find((candidate) => candidate.personId === personId)
  if (entry === undefined) return null

  const item = catalog.find((candidate) => candidate.id === entry.itemId)
  const checkpoint = item?.checkpoints[entry.cpIndex]

  return {
    itemId: entry.itemId,
    itemTitle: item?.title ?? entry.itemId,
    cpIndex: entry.cpIndex,
    label: entry.label,
    prompt: checkpoint?.prompt ?? entry.label,
    options: checkpoint?.options ?? [],
    tacit: tacitLines[tacitKey(entry.itemId, entry.cpIndex)] ?? '',
    cost: IN_PERSON_COST,
  }
}

/** The payload that resolves a decision, by whichever route (R9). */
export function resolvePayload(
  itemId: string,
  cpIndex: number,
  optionIndex: number,
  inPerson: boolean,
): Record<string, unknown> {
  return { item: itemId, cp_index: cpIndex, option_index: optionIndex, in_person: inPerson }
}

// =========================================================================
// The free card: handing work over
// =========================================================================

/** One thing this person could be handed, and what handing it over would mean. */
export interface AssignableItem {
  itemId: string
  title: string
  brief: string
  dept: string
  /** Who would end up doing the work. The person being talked to, or one of their reports. */
  wantId: string
  /** True when handing it over goes around the specialist's director. */
  bypassesDirector: boolean
  /** The director who would be left uninformed, or an empty string. */
  uninformed: string
  payload: Record<string, unknown>
}

/**
 * What this person could take on, or route to someone who reports to them (R12).
 *
 * The route follows from who the item wants rather than from a button the player picks. An item
 * wanting *this* person is handed over directly, which is the physical act of giving it to them
 * — and if they report to someone, that someone is now blind to it (R13). An item wanting one of
 * their reports is handed to them to pass on, which is what routing through a director *is*.
 *
 * Availability comes off the wire, exactly as the work panel takes it: the kernel owns whether
 * an item's gates have cleared, and deciding it here from the authored gates would be a second
 * implementation of that rule — the two already check visibility and prerequisites in opposite
 * orders, so they would eventually disagree while each passed its own tests.
 */
export function assignableWork(
  personId: string | null,
  roster: Record<string, RosterEntry>,
  items: Record<string, { status: string; unlocked: boolean }>,
  catalog: CatalogEntry[],
): AssignableItem[] {
  if (personId === null || roster[personId] === undefined) return []

  const assignable: AssignableItem[] = []

  for (const entry of catalog) {
    const item = items[entry.id]
    // Backlog *and* unlocked. An item already assigned is not offered again, and a locked one
    // would be rejected by the kernel with a reason the conversation has no room to explain.
    if (item === undefined || item.status !== 'backlog' || !item.unlocked) continue

    const want = entry.want
    const wantEntry = roster[want]
    if (wantEntry === undefined) continue

    const direct = want === personId
    const routed = wantEntry.mgr === personId

    if (!direct && !routed) continue

    // Only a direct hand-over can bypass, and only when the person actually reports to
    // someone: an item wanting a director has nobody above them to go around.
    const bypassed = direct && wantEntry.mgr !== '' ? wantEntry.mgr : ''

    assignable.push({
      itemId: entry.id,
      title: entry.title,
      brief: entry.brief,
      dept: entry.dept,
      wantId: want,
      bypassesDirector: bypassed !== '',
      uninformed: bypassed,
      payload: direct
        ? { item: entry.id, person: want, via_manager: false }
        : // Routed carries no person: the kernel resolves the reporting line from the item's
          // own `want`, and naming one here would be a second opinion about the org chart.
          { item: entry.id, via_manager: true },
    })
  }

  return assignable
}

/** What handing this item over costs, said before the hand-over rather than after (R10, R13). */
export function assignCost(item: AssignableItem): string {
  if (item.bypassesDirector) {
    return `Straight to ${item.wantId}. ${item.uninformed} will not know about it.`
  }
  return `Through you to ${item.wantId}, so the reporting line stays informed.`
}

// =========================================================================
// The ask box
// =========================================================================

/**
 * The payload that asks somebody something.
 *
 * The raw text goes to the kernel, not a matched intent. Matching lives there because that is
 * the side that prices the answer — Visibility is charged once per person per question, so the
 * kernel has to know which question was asked and cannot take the client's word for it. It is
 * also where a hearing API will replace the script, and matching belongs with the producer.
 */
export function askPayload(personId: string, question: string): Record<string, unknown> {
  return { person: personId, question }
}

/** Whether a typed question is worth sending at all. */
export function askable(question: string): boolean {
  return question.trim() !== ''
}
