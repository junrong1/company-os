/**
 * The timeline diff: the rules, with no React in them.
 *
 * The `X.tsx` plus `x-model.ts` split the HUD, the conversation and the comparison surface
 * already use, for the same two reasons. The lint config asks that a component file export only
 * components, so a module exporting both loses fast refresh; and every rule below is decidable
 * from plain data, so the suite states them directly rather than rendering a column and reading
 * its text — which would prove the JSX is wired rather than that a misleading diff is refused.
 *
 * Three decisions here are load-bearing.
 *
 * **The day is the server's to default, and the bound is the server's to state.** How far each
 * timeline got is a fact about rows the report reads, so the first ask carries no day and every
 * later one is clamped to the `max_day` that came back. A client that kept its own bound would
 * offer a day one side has not reached and take a refusal it could have avoided.
 *
 * **A delta reads left-to-right.** `delta` is the right side minus the left, so its direction
 * answers "is that timeline better than this one" — which is the comparison a player standing in
 * one of them is actually making. Reversing the two columns reverses every glyph, which is why
 * the sides are named by run id on the surface rather than by position alone.
 *
 * **A figure with no stated direction is rendered flat, not guessed at.** Runway is not a metric
 * and the wire gives it `good: 0`; the comparison surface renders it neutral for the same reason.
 * `directionOf` compares signs, and `Math.sign(0)` is 0, so a zero `good` would come back
 * *unfavourable* for every non-zero delta — a wrong answer that looks like a considered one.
 */

import { type Direction, directionOf } from '../design/tokens'
import type { DiffRowWire, DiffSideWire, DiffWire, PartingWire, SeparationWire } from '../net/gateway'
import { describeChoice, shortId } from './model'

// The wire shapes are `net/`'s, re-exported here rather than redefined — the rule `model.ts`
// states, for the reason it states: defining them in a view would put a `net -> ui` edge in a
// codebase that otherwise only has `ui -> net`.
export type {
  DiffRowWire,
  DiffSideWire,
  DiffWire,
  PartingWire,
  SeparationWire,
} from '../net/gateway'

/** Which two timelines a diff is of, once both have been picked. */
export interface DiffPair {
  left: string
  right: string
}

/**
 * Whether these two nodes can be diffed at all.
 *
 * The one rule the client can check on its own — the route owns the rest, because whether two
 * runs share a Genesis is a question about rows. Stated here so the tree can refuse to arm the
 * second pick rather than sending a request whose answer it already knows.
 */
export function diffable(left: string | null, right: string | null): boolean {
  return left !== null && right !== null && left !== '' && right !== '' && left !== right
}

/** Keep a day inside the range this diff can honestly answer for. */
export function clampDay(day: number, maxDay: number): number {
  if (!Number.isFinite(day)) return 1
  const bounded = Math.min(Math.max(Math.trunc(day), 1), Math.max(maxDay, 1))
  return bounded
}

/**
 * How a row's difference reads: is the right column better than the left, or worse?
 *
 * `good` comes off the wire because whether a movement is an improvement is the kernel's to
 * state — cash rising and manual hours falling are both wins, and a uniform rising-is-good rule
 * would render the automation gain as a regression. Zero means the kernel states no direction,
 * which is flat rather than a third opinion.
 */
export function rowDirection(row: DiffRowWire): Direction {
  if (row.delta === null || row.good === 0) return 'flat'
  return directionOf(row.delta, row.good)
}

/** A figure as it is written: an em dash for one the fold could not know, never a zero. */
export function figureText(value: number | null): string {
  return value === null ? '—' : String(value)
}

/** What the marking on a figure is about, so the accessible label names a column and a metric. */
export function figureLabel(side: DiffSideWire | null, row: DiffRowWire): string {
  return `${side === null ? 'timeline' : shortId(side.run_id)}, ${row.label}`
}

// =========================================================================
// What the two sides are
// =========================================================================

/**
 * What a column says about its timeline, as a statement about the day being compared at.
 *
 * **Never "running".** That is a claim about now, and now is the tree's to report: it is the
 * kernel that overlays a live fold on the rows, and this surface reads rows the kernel has not
 * written to yet. What is decidable here is decidable exactly — the timeline had ended by that
 * day, or its own log proves it went past it, or the day is as far as it got.
 */
export function describeSide(side: DiffSideWire | null, day: number): string {
  if (side === null) return ''
  if (side.terminal_reason !== '') return `ended by day ${day} · ${side.terminal_reason}`
  if (side.current_day > day) return `went on past day ${day}`
  return `day ${day} is as far as it got`
}

/**
 * The sentence the day control's bound needs, or an empty one.
 *
 * The bound is the lesser of what each side is *known* to have reached, and what is known comes
 * from a run row and the log under it — neither of which moves while a timeline is quiet. So a
 * diff can refuse a day a clock has plainly passed, and the note is what keeps that from reading
 * as the diff being broken.
 *
 * Nothing to say once both timelines had ended by the day compared at: a history that is over
 * cannot get further on, so the bound is the whole of it.
 */
export function lagNote(diff: DiffWire): string {
  const open = [diff.left, diff.right].filter(
    (side): side is DiffSideWire => side !== null && side.terminal_reason === '',
  )
  if (open.length === 0) return ''
  const names = open.map((side) => shortId(side.run_id)).join(' and ')
  return (
    `This diff goes to day ${diff.max_day} because that is as far as the store has been told ` +
    `${names} got. A run's stored day moves only when something is appended, so a timeline ` +
    'still advancing can be further on than that.'
  )
}

// =========================================================================
// What separated them
// =========================================================================

/** The diff's reading of a separation: one decision two ways, or two decisions. */
export interface SeparationReading {
  /** The timeline the two parted in — their nearest common ancestor. */
  atRunId: string
  /** True when one decision separated them, so it can be named once and read twice. */
  shared: boolean
  /** The decision, when there is one. Empty when there is not. */
  item: string
  /** What each side is playing at that decision. Empty when `shared` is false. */
  leftChoice: string
  rightChoice: string
  /**
   * The option the timeline they both left took, when that is a third one neither side is on.
   *
   * Two forks of one checkpoint leave the parent on an option that is now on neither column, and
   * a diff that did not say so would read as if one of these two had been the original.
   */
  ancestorChoice: string
  /** One line per side, when two different decisions separated them. Empty when one did. */
  turns: string[]
}

/**
 * Read a separation into what the surface says about it.
 *
 * `null` when the route could not name one: a trimmed divergence, or a tree whose ancestries
 * never meet. The surface says the diff is unnamed rather than inventing a decision for it.
 */
export function readSeparation(separation: SeparationWire | null): SeparationReading | null {
  if (separation === null || separation.partings.length === 0) return null

  const byside = new Map(separation.partings.map((parting) => [parting.side, parting]))

  if (!separation.shared) {
    return {
      atRunId: separation.at_run_id,
      shared: false,
      item: '',
      leftChoice: '',
      rightChoice: '',
      ancestorChoice: '',
      turns: separation.partings.map((parting) =>
        describeChoice(parting.item, parting.choice, parting.parent_choice),
      ),
    }
  }

  const first = separation.partings[0]
  return {
    atRunId: separation.at_run_id,
    shared: true,
    item: first.item,
    // A side with no parting of its own *is* the ancestor, so what it plays at this decision is
    // the option the other side turned away from.
    leftChoice: choiceOn(byside, 'left'),
    rightChoice: choiceOn(byside, 'right'),
    ancestorChoice: ancestorChoice(separation),
    turns: [],
  }
}

function choiceOn(byside: Map<string, PartingWire>, side: 'left' | 'right'): string {
  const mine = byside.get(side)
  if (mine !== undefined) return mine.choice
  const theirs = byside.get(side === 'left' ? 'right' : 'left')
  return theirs === undefined ? '' : theirs.parent_choice
}

/**
 * The option the ancestor took, when neither side is on it.
 *
 * Empty whenever one of the two *is* the ancestor, because then that option is already a column.
 */
function ancestorChoice(separation: SeparationWire): string {
  if (separation.partings.length < 2) return ''
  const taken = new Set(separation.partings.map((parting) => parting.choice))
  const parent = separation.partings[0].parent_choice
  return taken.has(parent) ? '' : parent
}

/**
 * The one-line form, for a surface with room for a line rather than a paragraph.
 *
 * Built out of the tree's own `describeChoice`, so a decision named beside the tree and the same
 * decision named over the diff are the same sentence.
 */
export function describeSeparation(reading: SeparationReading | null): string {
  if (reading === null) return ''
  if (!reading.shared) return reading.turns.join(' · ')
  return describeChoice(reading.item, reading.rightChoice, reading.leftChoice)
}
