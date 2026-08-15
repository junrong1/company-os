/**
 * Comparing options: the rules, with no React in them.
 *
 * The `X.tsx` plus `x-model.ts` split the HUD and the conversation already use, for the same
 * two reasons. The project's lint config asks that a component file export only components, so
 * a module exporting both loses fast refresh. And every rule below is decidable from plain
 * data, so the suite states them directly instead of rendering a column and reading its text —
 * which would prove the JSX is wired rather than that a stale result is dropped.
 *
 * Three decisions here are load-bearing.
 *
 * **The comparison slice is separate from the live trajectory store.** `state.trajectories` is
 * the parent run's *actual* history, bounded at 240 points and written only from what the store
 * read off an event. A branch's points are a *projection*, carrying a measured tick and an
 * authored basis, and folding them into the same slice would make the HUD's sparklines draw a
 * future nobody has lived. They live here, keyed by the comparison, and are dropped with it.
 *
 * **A result is invalidated by an authority, not by a timer.** R25 says a result is refused as
 * stale once the parent has moved that checkpoint's item — and the store already drops tray
 * entries when an item leaves `blocked`, from the generic item branch that covers resolution,
 * reassignment, attrition and a return to the backlog alike. The comparison hangs off the same
 * fact. A client-side expiry would be a second opinion about a question the wire answers.
 *
 * **Both routes read this same model.** The tray and the conversation open the same comparison
 * for the same checkpoint, so the two surfaces cannot disagree about what an option leads to —
 * which they would if each derived its own columns.
 */

import { type Direction, type MetricDef, direction } from '../design/tokens'
import type { Branch, Comparison, ProjectedFigure } from '../net/store'
import { comparisonKey } from '../net/store'

/**
 * Asking for a comparison.
 *
 * Its own sender rather than the generic `CommandSender`, because the payload needs the render
 * clock's tick and the render clock lives in the shell alongside the renderer that drives it.
 * Threading a tick getter down through two layers of panel would put the client's authority for
 * "now" in the hands of whichever component happened to hold it; keeping the payload builder
 * where the clock is means there is one answer.
 */
export interface CompareSender {
  (itemId: string, cpIndex: number, personId: string, inPerson: boolean): void
}

/**
 * The payload that asks for a comparison.
 *
 * `at_tick` comes from the *render* clock rather than from the store's tick, which is the same
 * rule every other tagged command follows and for the same reason: the store's tick is the last
 * one the kernel said out loud, and events land on about five ticks in twelve hundred. Tagging
 * from it would put every comparison well into the run's past. Sent as a string, matching the
 * convention every tick on the wire follows — `Number` would work for the whole of a normal run
 * and start silently losing the low bits above 2^53.
 */
export function comparePayload(
  itemId: string,
  cpIndex: number,
  personId: string,
  atTick: bigint,
  inPerson: boolean,
): Record<string, unknown> {
  return {
    item: itemId,
    cp_index: cpIndex,
    person: personId,
    at_tick: atTick.toString(),
    in_person: inPerson,
  }
}

/**
 * The comparison for a checkpoint, or `null` when there is none to show.
 *
 * Held against the tray, which is the wire's own authority on what is still waiting. A result
 * whose item has left `blocked` is not shown, whatever the slice still holds — the two are kept
 * in step by the store dropping both from the same branch, and this is the second guard on the
 * order they happen to be dropped in.
 */
export function comparisonFor(
  itemId: string,
  cpIndex: number,
  inPerson: boolean,
  comparisons: Record<string, Comparison>,
  tray: ReadonlyArray<{ itemId: string; cpIndex: number }>,
  /**
   * The sequence the viewer asked at. A record older than this is a *previous* answer.
   *
   * Without it, a record survives closing the panel — it is only dropped when the item leaves
   * `blocked` — so reopening the comparison thousands of ticks later renders the old
   * projection instantly, as though it were the answer to the click that had just been made.
   * A page reload does the same, because the stream resumes from the beginning and re-applies
   * every historical comparison record. The new one silently swaps in about half a second
   * later, and nothing marks the gap.
   */
  askedAtSeq: bigint = 0n,
): Comparison | null {
  const held = comparisons[comparisonKey(itemId, cpIndex, inPerson)]
  if (held === undefined) return null
  if (held.atSeq <= askedAtSeq) return null

  const stillWaiting = tray.some(
    (entry) => entry.itemId === itemId && entry.cpIndex === cpIndex,
  )
  return stillWaiting ? held : null
}

/**
 * What a branch column says about where it stopped.
 *
 * A branch that terminated renders its terminal reason rather than an empty trajectory: "the
 * company ran out of cash on day nine" is the most important thing a comparison can tell you,
 * and a column that showed a truncated line instead would bury it.
 */
export function stopSentence(branch: Branch): string {
  if (branch.stopReason === 'checkpoint') {
    const reached = branch.reached
    if (reached === null) return `Stops at the next decision, at tick ${branch.stopTick}.`
    return `Runs to the next decision — ${reached.label} on ${reached.itemId}, at tick ${reached.tick}. Nothing is settled there.`
  }
  if (branch.stopReason === 'insolvent') {
    return `The company runs out of cash at tick ${branch.stopTick}.`
  }
  if (branch.stopReason === 'horizon') {
    return `Raises no further decision. Runs to the horizon at tick ${branch.stopTick}.`
  }
  if (branch.stopReason === 'bound') {
    // Said as a limit of the projection, not as a fact about the run. The run goes further;
    // this is where the comparison stopped looking.
    return `Raises no further decision as far as this projection runs, which is tick ${branch.stopTick}. The run continues past there.`
  }
  return `Stops at tick ${branch.stopTick}.`
}

/**
 * How a projected figure reads against the parent's current value.
 *
 * The comparison the CEO is actually making is not "is this number big" but "is it better or
 * worse than where I am now", and the metric's own favourable direction decides which. `null`
 * for a figure that is not knowable, which is neither.
 */
export function projectedDirection(
  figure: ProjectedFigure,
  now: number | undefined,
  metric: MetricDef | undefined,
): Direction {
  if (figure.value === null || now === undefined || metric === undefined) return 'flat'
  return direction(metric, figure.value - now)
}
