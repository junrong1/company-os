/**
 * What a director's memory reads as, with no React in it.
 *
 * Split from the panel for the reason every other model here is: the rules are worth asserting
 * directly. Two of them carry the unit's claims rather than its presentation —
 *
 * * `memorySentence` is the four states of a summary reduced to one sentence each, and the
 *   `refused` arm goes through `fallbackSentence`, which is the *same* table the bench block reads.
 *   One vocabulary of conditions across both surfaces, so a timeout says the same thing wherever
 *   the player meets it;
 * * `eventLine` is how a logged event becomes a sentence a person can read. It is a lookup from the
 *   kind, so an event kind nobody wrote a phrase for reads as itself rather than as nothing — the
 *   failure that would otherwise be a blank row in a history panel.
 *
 * Nothing here derives a figure. Every number the panel shows — a day, a sequence, a count — comes
 * off the wire, because the backend's guard resolves a summary's figures against the same selection
 * and a number computed here would resolve against nothing.
 */

import type { MemoryEventWire, MemorySummaryWire, MemoryWire } from '../net/gateway'
import { fallbackSentence } from './conversation-model'

/** The four states of a summary, as the wire spells them. */
export type SummaryStatus = 'pending' | 'written' | 'absent' | 'refused'

const STATUSES: readonly string[] = ['pending', 'written', 'absent', 'refused']

/** One sentence of a summary, and the sequences it was written over. */
export interface SummaryPoint {
  text: string
  citations: number[]
}

/** One remembered event, ready to render. */
export interface MemoryEvent extends MemoryEventWire {
  /** The kind as a phrase, with whoever and whatever it was about folded in. */
  line: string
}

/** One director's memory, as the panel holds it. */
export interface MemoryView {
  directorId: string
  line: string[]
  asOfDay: number
  throughDay: number
  considered: number
  selected: number
  events: MemoryEvent[]
  status: SummaryStatus
  points: SummaryPoint[]
  /** Why there is no prose, in a sentence. Empty when there is prose. */
  because: string
  modelIdentity: string
}

/**
 * What each event kind says in words.
 *
 * Phrases rather than the enum, because the panel is the one surface in the product where a
 * *person* reads the log. The kinds are `context.RETRIEVABLE`'s, which is the closed set the
 * backend will ever admit — and `eventLine` falls back to the raw kind rather than to silence, so a
 * kind added there before a phrase is added here reads as itself instead of as an empty row.
 */
export const KIND_PHRASES: Record<string, string> = {
  WORK_ASSIGNED: 'took work on',
  WORK_REASSIGNED: 'work moved',
  WORK_RETURNED_TO_BACKLOG: 'work went back to the backlog',
  CHECKPOINT_RAISED: 'stopped for a decision',
  DECISION_RESOLVED: 'a decision was settled',
  DELIVERABLE_PRODUCED: 'delivered',
  QUESTION_ANSWERED: 'answered a question',
  ATTRITION: 'left the company',
  HIRE_REQUESTED: 'a hire was asked for',
  HIRE_ARRIVED: 'a hire arrived',
  HIRE_REFUSED: 'a hire was refused',
}

/** One event as a line: the phrase, who it was about, and what it was about. */
export function eventLine(event: MemoryEventWire): string {
  const phrase = KIND_PHRASES[event.kind] ?? event.kind
  const parts = [event.person, phrase].filter((part) => part !== '')
  const line = parts.join(' ')
  const about = [event.item, event.detail].filter((part) => part !== '').join(' · ')
  return about === '' ? line : `${line} — ${about}`
}

/**
 * The sentence the panel shows in place of prose.
 *
 * `written` returns the empty string rather than a sentence, so the caller renders the summary
 * itself and there is no line of chrome above prose that already says what it is.
 */
export function memorySentence(summary: Pick<MemorySummaryWire, 'status' | 'fallback'>): string {
  switch (asStatus(summary.status)) {
    case 'written':
      return ''
    case 'pending':
      return 'Putting their account of it together.'
    case 'absent':
      // Both causes of absence, in one sentence, because the player cannot act on the difference:
      // there is no bench configured, or nothing has happened on this line yet. The events list
      // underneath is what distinguishes them, and it is right there.
      return 'No account of it — the events are the whole of what this build can show you.'
    case 'refused':
      return fallbackSentence(summary.fallback)
  }
}

/**
 * The stamp under the heading: the day being read, and the day the account runs to.
 *
 * Two facts, and they come apart the moment a line goes quiet. Rendering only the first would have
 * the panel claim a summary is current when what it describes is a fortnight old; rendering only
 * the second would have it look stale on a run that is simply calm.
 */
export function memoryStamp(view: Pick<MemoryView, 'asOfDay' | 'throughDay' | 'status'>): string {
  const asOf = `as of day ${view.asOfDay}`
  if (view.status !== 'written' || view.throughDay === view.asOfDay) return asOf
  return `${asOf} · account runs to day ${view.throughDay}`
}

/** How much of the line's history the selection stands for. */
export function memoryScale(view: Pick<MemoryView, 'considered' | 'selected'>): string {
  if (view.considered <= view.selected) return `${view.selected} events`
  return `${view.selected} of ${view.considered} events`
}

/** The wire shape as the panel's view. */
export function toMemoryView(wire: MemoryWire): MemoryView {
  return {
    directorId: wire.director,
    line: wire.line ?? [],
    asOfDay: wire.as_of_day,
    throughDay: wire.through_day,
    considered: wire.considered,
    selected: wire.selected,
    events: (wire.events ?? []).map((event) => ({ ...event, line: eventLine(event) })),
    status: asStatus(wire.summary?.status ?? 'absent'),
    points: (wire.summary?.points ?? []).map((point) => ({
      text: point.text,
      citations: point.citations ?? [],
    })),
    because: memorySentence(wire.summary ?? { status: 'absent', fallback: '' }),
    modelIdentity: wire.summary?.model_identity ?? '',
  }
}

/**
 * Whether the prose is still to be asked for.
 *
 * The panel's whole two-call shape in one predicate: the first read answers `pending` only when a
 * bench is actually present, so a keyless run asks once and never renders a line that cannot
 * resolve.
 */
export function awaitingProse(view: Pick<MemoryView, 'status'>): boolean {
  return view.status === 'pending'
}

/** A status off the wire, or `absent` — which is the state that shows the events and no prose. */
function asStatus(value: string): SummaryStatus {
  return (STATUSES.includes(value) ? value : 'absent') as SummaryStatus
}
