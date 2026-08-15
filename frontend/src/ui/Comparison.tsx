/**
 * The branch comparison, side by side.
 *
 * One component, rendered from both routes. The tray and the conversation open the comparison
 * for the same checkpoint, and two implementations would eventually price one option two ways
 * — the same argument that put the option consequence in its own file, applied to the surface
 * that follows an option further out.
 *
 * **Closing commits nothing** (R34). The button sends no command; it clears the local
 * selection and returns the CEO to the option list they opened it from, in whichever surface
 * that was. A comparison informs a choice and never makes one, and a close that quietly
 * settled the decision would be the single worst thing this panel could do.
 *
 * **Every figure is marked and tick-stamped.** The marking because R28 admits no exceptions,
 * and these are the most invented numbers on the screen. The tick because a projection read
 * without its measurement point is indistinguishable from a fact about now.
 */

import { useState } from 'react'
import { useShallow } from 'zustand/react/shallow'

import { type MetricDef, NO_METRIC_DEFS, PAL, directionColour } from '../design/tokens'
import { type Branch, type Comparison, type ProjectedFigure, useRunStore } from '../net/store'
import { Mark } from './Marking'
import {
  type CompareSender,
  comparisonFor,
  projectedDirection,
  stopSentence,
} from './comparison-model'

export interface ComparisonProps {
  comparison: Comparison
  metricDefs: readonly MetricDef[]
  /** The parent's current metrics, so each projection reads against where the run is now. */
  now: Record<string, number>
  /** Titles for the work an option opens or closes, so a column names work rather than ids. */
  titles: Record<string, string>
  onClose: () => void
}

function Figure({
  figure,
  now,
  metric,
  label,
}: {
  figure: ProjectedFigure
  now: number | undefined
  metric: MetricDef | undefined
  label: string
}) {
  const dir = projectedDirection(figure, now, metric)

  return (
    <span className="branch__figure" data-direction={dir}>
      <span className="branch__value" style={{ color: directionColour(dir) }}>
        {/* An em dash, not a zero. A figure the kernel could not know — a runway before the
            branch has paid a day of costs — is not the same as one that came back zero, and
            the two are the same pixel width and opposite in meaning. */}
        {figure.value === null ? '—' : figure.value}
      </span>
      <span className="branch__at" style={{ color: PAL.textFaint }}>
        at {figure.atTick.toString()}
      </span>
      <Mark of={label} />
    </span>
  )
}

function Column({
  branch,
  metricDefs,
  now,
  titles,
}: {
  branch: Branch
  metricDefs: readonly MetricDef[]
  now: Record<string, number>
  titles: Record<string, string>
}) {
  return (
    <article className="branch" data-option={branch.optionIndex} data-stop={branch.stopReason}>
      <p className="branch__label">{branch.optionLabel}</p>
      {/* Whatever ended it. A branch that went insolvent says so here rather than rendering a
          truncated line and leaving the CEO to notice the trajectory stopped early. */}
      <p className="branch__stop">{stopSentence(branch)}</p>

      {metricDefs.map((metric) => (
        <div className="branch__row" key={metric.key} data-metric={metric.key}>
          <span className="branch__name">{metric.label}</span>
          <Figure
            figure={branch.metrics[metric.key] ?? { value: null, atTick: branch.stopTick }}
            now={now[metric.key]}
            metric={metric}
            label={`${branch.optionLabel}, ${metric.label}`}
          />
        </div>
      ))}

      <div className="branch__row" data-metric="runway">
        <span className="branch__name">Runway</span>
        <Figure
          figure={branch.runway}
          // Runway has no metric definition on the wire, so it has no favourable direction to
          // read against. Rendered neutral rather than guessed at.
          now={undefined}
          metric={undefined}
          label={`${branch.optionLabel}, runway`}
        />
      </div>

      {/* Named with the tick they were read at, like every other figure. One branch can stop
          days before another, so a longer-running column opens more work simply by running
          longer — without the tick the two look comparable when they are not. */}
      <p className="branch__gates-at" style={{ color: PAL.textFaint }}>
        Gates as of tick {branch.gatesAtTick.toString()}
        <Mark of={`${branch.optionLabel}, gates`} />
      </p>
      <ul className="branch__gates" data-unlocked={branch.unlocked.length}>
        {branch.unlocked.map((itemId) => (
          <li key={`open:${itemId}`} data-gate="unlocked">
            Opens {titles[itemId] ?? itemId}
          </li>
        ))}
        {branch.foreclosed.map((itemId) => (
          <li key={`shut:${itemId}`} data-gate="foreclosed">
            Closes {titles[itemId] ?? itemId}
          </li>
        ))}
        {branch.unlocked.length === 0 && branch.foreclosed.length === 0 && (
          <li data-gate="none" style={{ color: PAL.textFaint }}>
            Opens and closes nothing
          </li>
        )}
      </ul>

      {branch.optionNote !== '' && (
        <p className="branch__note" style={{ color: PAL.textFaint }}>
          Recorded as: {branch.optionNote}
        </p>
      )}
    </article>
  )
}

export function ComparisonPanel({
  comparison,
  metricDefs,
  now,
  titles,
  onClose,
}: ComparisonProps) {
  return (
    <section className="comparison" data-item={comparison.itemId} data-cp={comparison.cpIndex}>
      <header className="comparison__head">
        <p className="comparison__title">
          Where each option leads
          <Mark of="Branch comparison" withLabel />
        </p>
        <p className="comparison__basis" style={{ color: PAL.textFaint }}>
          Forked at tick {comparison.forkTick.toString()}. Each branch takes no further
          decision — it stops at the next one and reports it.
        </p>
      </header>

      <div className="comparison__columns" data-branches={comparison.branches.length}>
        {comparison.branches.map((branch) => (
          <Column
            key={branch.optionIndex}
            branch={branch}
            metricDefs={metricDefs}
            now={now}
            titles={titles}
          />
        ))}
      </div>

      {/* R34: back to the option list, with nothing committed. */}
      <button type="button" className="comparison__close" onClick={onClose}>
        Close and choose
      </button>
    </section>
  )
}

/**
 * The affordance both surfaces render: ask for a comparison, then read it.
 *
 * Held here rather than in each surface so the tray route and the conversation route are the
 * same route with a different flag — `in_person` is genuinely different between them, because
 * the kernel prices the two routes differently and a branch that priced the one the CEO is not
 * about to take would be wrong by exactly that premium.
 *
 * The open flag is local state, and closing only clears it. Nothing is sent, nothing is
 * committed, and the option list this sits under never went away (R34).
 */
export function CompareAffordance({
  itemId,
  cpIndex,
  personId,
  inPerson,
  onCompare,
}: {
  itemId: string
  cpIndex: number
  personId: string
  inPerson: boolean
  onCompare?: CompareSender
}) {
  // `null` while closed; the sequence the run was at when the CEO asked, once open. A record
  // older than that is a previous answer — from before the panel was closed, or replayed off
  // the stream after a reload — and showing it would answer this click with that one.
  const [askedAtSeq, setAskedAtSeq] = useState<bigint | null>(null)
  const open = askedAtSeq !== null

  const comparison = useRunStore(
    useShallow((state) =>
      comparisonFor(
        itemId,
        cpIndex,
        inPerson,
        state.comparisons,
        state.tray,
        askedAtSeq ?? 0n,
      ),
    ),
  )
  const metricDefs = useRunStore((state) => state.genesis?.metricDefs) ?? NO_METRIC_DEFS
  const now = useRunStore(useShallow((state) => state.metrics))
  const titles = useRunStore(
    useShallow((state) =>
      Object.fromEntries(
        (state.genesis?.catalog ?? []).map((entry) => [entry.id, entry.title]),
      ),
    ),
  )

  // Derived rather than kept in sync: the store drops a comparison when its item leaves
  // `blocked`, so a result that has gone stale on the wire disappears from here without any
  // client-side timer and without this component knowing why (R25).
  const showing = open && comparison !== null

  return (
    <div className="compare" data-open={open} data-showing={showing}>
      {!open && (
        <button
          type="button"
          className="compare__ask"
          onClick={() => {
            // Stamped before the command goes out, so any record already on the stream is
            // older by construction and cannot answer this click.
            setAskedAtSeq(useRunStore.getState().appliedSeq)
            onCompare?.(itemId, cpIndex, personId, inPerson)
          }}
        >
          Compare where each option leads
        </button>
      )}

      {/* Open, and the record has not landed yet. Said rather than left blank, and with the
          way back kept available — a comparison the kernel refuses would otherwise leave this
          open forever with nothing in it and no button to close. Keeping the ask button hidden
          while open is also what makes a second request at one checkpoint unreachable, so
          "the newer record replaces the older" never has to arbitrate between two in flight. */}
      {open && comparison === null && (
        <p className="compare__pending" style={{ color: PAL.textFaint }}>
          Running one branch per option…
          <button type="button" className="compare__cancel" onClick={() => setAskedAtSeq(null)}>
            Cancel
          </button>
        </p>
      )}

      {showing && (
        <ComparisonPanel
          comparison={comparison}
          metricDefs={metricDefs}
          now={now}
          titles={titles}
          onClose={() => setAskedAtSeq(null)}
        />
      )}
    </div>
  )
}

