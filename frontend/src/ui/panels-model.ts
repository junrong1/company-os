/**
 * What the panels compute, with no React in them.
 *
 * Split from the components so the component file exports only components, which is what the
 * project's lint config asks for and what keeps fast refresh working on them.
 */

import type { CatalogEntry, DecisionRecord, ItemStatus, OptionDef } from '../net/store'

/** Percent complete, cross-multiplied so it agrees with the kernel's own threshold test. */
export function progressPercent(doneUnits: number, effortUnits: number): number {
  if (effortUnits <= 0) return 0
  return Math.min(100, Math.floor((doneUnits * 100) / effortUnits))
}

/**
 * Why an item is not available yet, in a sentence.
 *
 * Read from the catalog's authored gates rather than from a reason string on the wire: the
 * gates are immutable and already on the client, and a locked item produces no events to carry
 * an explanation.
 */
export function lockReason(
  entry: CatalogEntry,
  visibility: number,
  done: Set<string>,
): string {
  const missing = entry.requires.items.filter((id) => !done.has(id))
  if (missing.length > 0) return `waits on ${missing.join(', ')}`
  if (entry.requires.visibility !== null && visibility < entry.requires.visibility) {
    return `needs ${entry.requires.visibility}% visibility (at ${visibility}%)`
  }
  return ''
}

export const STATUS_LABEL: Record<ItemStatus, string> = {
  backlog: 'Backlog',
  assigned: 'Assigned',
  active: 'In progress',
  blocked: 'Needs you',
  done: 'Delivered',
}

// =========================================================================
// Past decisions, and the alternatives to them (U25: M44)
// =========================================================================

/** One option that was not taken, with the index a fork request names. */
export interface Alternative {
  index: number
  option: OptionDef
}

/**
 * One settled decision, joined to the authored checkpoint it settled.
 *
 * The join is the catalog's, not the wire's: `DECISION_RESOLVED` carries the checkpoint index
 * and the option's label, and every other word on the card — the item's title, what was being
 * decided, what the alternatives were and what they cost — is authored content already sitting
 * on the client. Nothing is asked for a second time to render this.
 */
export interface PastDecision {
  key: string
  itemId: string
  /** The item's authored title, falling back to its id when the catalog does not name it. */
  title: string
  /** What was being decided: the checkpoint's authored label, or the one the record carries. */
  label: string
  /** The option taken, and the index the catalog holds it at. `-1` when only the label is known. */
  taken: string
  takenIndex: number
  inPerson: boolean
  tick: bigint
  atSeq: bigint
  /** Every option that was not taken. Empty when the checkpoint cannot be addressed. */
  alternatives: Alternative[]
  /** Why this decision cannot be forked, or `''` when it can. */
  unforkable: string
}

/**
 * Why a decision cannot be forked, in the words the panel shows.
 *
 * It names the fact rather than a remedy, deliberately: the remedy a reader would expect —
 * reload and replay the run — is the very thing that resynced, so a card promising it would be
 * promising something that may not work. The same trap U16's review caught on the unanswered
 * request refusal.
 */
export const NO_SEQUENCE =
  'Settled before this client attached. The snapshot carries the decision but not its place in ' +
  'the log, which is what a fork names.'

/**
 * Every settled decision, newest first, with its alternatives.
 *
 * **Newest first**, like the answers a person has given, and for the same reason: the panel is a
 * fixed column in a rail that a whole run's decisions will not fit in, and the decision worth
 * reconsidering first is the one whose consequences the player is watching land. The Universe
 * tree is the surface that reads left to right, in fork order; this one reads down, in time.
 *
 * Ties break on the key so that two decisions settled on one tick — reachable, since a tick is a
 * whole sim-minute — hold their order across a re-render instead of swapping under the cursor.
 */
export function pastDecisions(
  decisions: Record<string, DecisionRecord>,
  catalog: readonly CatalogEntry[],
): PastDecision[] {
  const byId: Record<string, CatalogEntry> = {}
  for (const entry of catalog) byId[entry.id] = entry

  const cards = Object.entries(decisions).map(([key, decision]) => {
    const entry = byId[decision.itemId]
    const checkpoint =
      decision.cpIndex >= 0 ? entry?.checkpoints?.[decision.cpIndex] : undefined
    const options = checkpoint?.options ?? []

    // Which option was taken, preferring the index the event stated over a label match. The
    // label is the fallback for a catalog whose option order this run does not share — a
    // pre-scenario run keeps its old-shaped catalog forever — and it is the only handle a
    // snapshot-derived record has at all.
    const takenIndex =
      decision.optionIndex >= 0 && decision.optionIndex < options.length
        ? decision.optionIndex
        : options.findIndex((option) => option.label === decision.choice)

    return {
      key,
      itemId: decision.itemId,
      title: entry?.title ?? decision.itemId,
      label: checkpoint?.label ?? decision.label ?? '',
      taken: decision.choice,
      takenIndex,
      inPerson: decision.inPerson,
      tick: decision.tick,
      atSeq: decision.atSeq,
      alternatives: options
        .map((option, index) => ({ index, option }))
        .filter((alternative) => alternative.index !== takenIndex),
      unforkable: decision.atSeq > 0n ? '' : NO_SEQUENCE,
    }
  })

  return cards.sort((left, right) =>
    left.tick === right.tick
      ? left.key.localeCompare(right.key)
      : left.tick > right.tick
        ? -1
        : 1,
  )
}

// =========================================================================
// Authorization (U15)
// =========================================================================

/**
 * The idempotency key for answering one Authorization. Once per intent, not per attempt (R30).
 *
 * Derived from the request and the verdict rather than minted per press, for the reason the fork
 * key is derived from the decision and the option: the second press of Grant is the *same* intent
 * and must be answered with the first press's outcome, while Refuse is a different intent and must
 * not be. A random key would send both to the kernel, where the second finds the question already
 * closed and comes back with a true sentence that reads as a bug to whoever double-clicked.
 *
 * Here rather than in `Panels.tsx` because that file exports components and this is not one — the
 * same split `lockReason` and `progressPercent` are on, and the reason the lint rule asks for it is
 * that a module mixing the two loses fast refresh for everything in it.
 */
export function authorizationKey(requestId: string, granted: boolean): string {
  return `authz-${requestId}-${granted ? 'grant' : 'refuse'}`
}
