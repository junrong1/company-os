/**
 * Two futures at one sim-day, side by side, with the decision that separated them named (M50).
 *
 * **It extends the comparison surface's column layout rather than inventing a second one.** The
 * product already has a place where a CEO reads two futures against each other — the branch
 * comparison at an open checkpoint — and a diff that spoke a different visual language would make
 * the player learn twice. Same rows, same direction glyphs, same marking on every figure, and the
 * same rule that a figure nobody could know renders as an em dash rather than a zero.
 *
 * **What is different is what it is a comparison *of*.** A branch comparison is a projection,
 * discarded when the checkpoint closes and never kept (R34, M51). This is two histories that were
 * both lived: each column is the kernel's own fold of a real log, at a tick both timelines
 * actually reached. Nothing here is a preview and nothing here is forked — which is why the two
 * surfaces stay separate components over one layout instead of one component over two meanings.
 *
 * **Full-bleed inside the Universe stage, over the tree it was entered from.** The tree stays
 * mounted underneath: closing the diff returns the player to the two nodes they picked, which is
 * where they will pick the next pair from.
 *
 * **Everything decidable is in `diff-model.ts`.** What is left here is React: one fetch with an
 * abort, a day control bounded by what came back, and the refusal rendered where the figures
 * would have been.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useShallow } from 'zustand/react/shallow'

import { PAL, directionColour } from '../design/tokens'
import { GatewayUnreachable, LineageUnavailable, fetchDiff } from '../net/gateway'
import type { DiffSideWire, DiffWire } from '../net/gateway'
import { useRunStore } from '../net/store'
import { Mark } from '../ui/Marking'
import {
  type DiffPair,
  clampDay,
  describeSide,
  figureLabel,
  figureText,
  lagNote,
  readSeparation,
  rowDirection,
} from './diff-model'
import { shortId } from './model'

export interface DiffProps {
  /** The two timelines, in the order they were picked. Left is the one the diff was armed from. */
  pair: DiffPair
  onClose: () => void
  /** Injectable for the suite, which mounts this without a report. Must be stable across renders. */
  reader?: (
    runId: string,
    against: string,
    day?: number,
    signal?: AbortSignal,
  ) => Promise<DiffWire>
}

export function Diff({ pair, onClose, reader }: DiffProps) {
  const [diff, setDiff] = useState<DiffWire | null>(null)
  const [error, setError] = useState<string | null>(null)
  // `null` until the player moves it. The first ask carries no day at all, because how far each
  // timeline got is the route's fact and a guess here would take a refusal it could have avoided.
  const [day, setDay] = useState<number | null>(null)

  const read = reader ?? fetchDiff

  // Reset when the pair changes. Decided during render rather than in an effect: a day chosen for
  // one pair is meaningless for the next, and a frame rendered with the old day against the new
  // pair would ask for it.
  const askedRef = useRef<string>('')
  const key = `${pair.left} ${pair.right}`
  if (askedRef.current !== key) {
    askedRef.current = key
    if (day !== null) setDay(null)
  }

  useEffect(() => {
    const controller = new AbortController()
    setError(null)
    read(pair.left, pair.right, day ?? undefined, controller.signal)
      .then((next) => {
        if (controller.signal.aborted) return
        setDiff(next)
      })
      .catch((cause: unknown) => {
        if (controller.signal.aborted) return
        setError(
          cause instanceof LineageUnavailable || cause instanceof GatewayUnreachable
            ? cause.message
            : String(cause),
        )
      })

    return () => controller.abort()
  }, [day, pair.left, pair.right, read])

  // Titles for the work a decision belongs to, so the header names work rather than an id. Read
  // from this run's genesis, which is every timeline in the lineage's genesis too — and falling
  // back to the id, which is what the tree shows beside it.
  const titles = useRunStore(
    useShallow((state) =>
      Object.fromEntries(
        (state.genesis?.catalog ?? []).map((entry) => [entry.id, entry.title]),
      ),
    ),
  )

  const separation = useMemo(
    () => (diff === null ? null : readSeparation(diff.separation)),
    [diff],
  )

  const step = useCallback(
    (next: number) => {
      if (diff === null) return
      setDay(clampDay(next, diff.max_day))
    },
    [diff],
  )

  if (error !== null) {
    return (
      <section className="diff" aria-label="Timeline diff">
        <p className="diff__error" style={{ color: PAL.textFaint }}>
          {error}
        </p>
        <button type="button" className="diff__close" onClick={onClose}>
          Back to the tree
        </button>
      </section>
    )
  }

  if (diff === null) {
    return (
      <section className="diff" aria-label="Timeline diff">
        <p className="diff__pending" style={{ color: PAL.textFaint }}>
          Folding both timelines to one day…
        </p>
        <button type="button" className="diff__close" onClick={onClose}>
          Back to the tree
        </button>
      </section>
    )
  }

  const lag = lagNote(diff)

  return (
    <section
      className="diff"
      aria-label="Timeline diff"
      data-day={diff.day}
      data-refused={diff.refusal !== ''}
    >
      <header className="diff__head">
        <p className="diff__title">
          {diff.refusal === ''
            ? `Two timelines at day ${diff.day}`
            : 'Two timelines, and why they cannot be shown side by side'}
          <Mark of="Timeline diff" withLabel />
        </p>

        {separation === null ? (
          <p className="diff__separated" style={{ color: PAL.textFaint }}>
            {/* Said rather than left blank. A tree whose divergence event has been trimmed still
                draws, and a diff over it is still honest — it simply cannot name the decision. */}
            The decision that separated these two is not in the log this build can read.
          </p>
        ) : separation.shared ? (
          <p className="diff__separated">
            They parted at <b>{titles[separation.item] ?? separation.item}</b>
            {separation.leftChoice !== '' && (
              <>
                {': '}
                {shortId(pair.left)} took <b>{separation.leftChoice}</b>; {shortId(pair.right)}{' '}
                took <b>{separation.rightChoice}</b>
              </>
            )}
            {separation.ancestorChoice !== '' && (
              <span className="diff__ancestor" style={{ color: PAL.textFaint }}>
                {' · '}
                {shortId(separation.atRunId)}, which both left, took {separation.ancestorChoice}
              </span>
            )}
          </p>
        ) : (
          <p className="diff__separated">
            {/* Two decisions, and neither side took the other's. Naming one of them would be
                naming a decision that is not what separated these two. */}
            They parted in {shortId(separation.atRunId)}, at two different decisions.
            {separation.turns.map((turn) => (
              <span className="diff__turn" key={turn}>
                {turn}
              </span>
            ))}
          </p>
        )}

        <button type="button" className="diff__close" onClick={onClose}>
          Back to the tree
        </button>
      </header>

      {/* A slider rather than a button per day. A day is a point on a line that runs to the
          horizon — a month of them in the shipped company — so a button each is thirty buttons.
          The steppers are here as well, because one day either way is the movement this control
          is actually used for. */}
      {/* Hidden when there is no shared range to move inside at all, which is the answer for two
          runs of different Universes: `max_day` comes back as zero, and a control reading "day 0"
          under a refusal that says these two share no Genesis is furniture pretending to work. */}
      <div className="diff__day" data-max={diff.max_day} hidden={diff.max_day < 1}>
        <button
          type="button"
          className="diff__step"
          data-step="back"
          disabled={diff.day <= 1}
          onClick={() => step(diff.day - 1)}
        >
          {'◀'}
        </button>
        <label className="diff__slider">
          <span className="diff__day-label">
            Day {diff.day}
            {/* The bound stated beside the reading rather than folded into it. A refused day is
                still the day that was asked for, and "day 9 of 4" would read as broken where
                "day 9, both reached day 4" reads as the reason underneath it. */}
            <span className="diff__bound" style={{ color: PAL.textFaint }}>
              {' · '}both reached day {diff.max_day}
            </span>
          </span>
          <input
            type="range"
            min={1}
            max={Math.max(diff.max_day, 1)}
            value={clampDay(diff.day, diff.max_day)}
            aria-label="The sim-day both timelines are compared at"
            onChange={(event) => step(Number(event.currentTarget.value))}
          />
        </label>
        <button
          type="button"
          className="diff__step"
          data-step="forward"
          disabled={diff.day >= diff.max_day}
          onClick={() => step(diff.day + 1)}
        >
          {'▶'}
        </button>
      </div>

      {diff.refusal !== '' ? (
        <p className="diff__refusal">{diff.refusal}</p>
      ) : (
        <>
          {/* The headings sit in the same grid template the figure rows do, so a column of
              numbers cannot end up under the other timeline's name. The first cell is the
              metric-name column and has nothing to say; the last names what the fourth column
              is, which was otherwise a column of signed numbers with no heading at all. */}
          <div className="diff__columns">
            <span className="diff__heading" aria-hidden="true" />
            <Column side={diff.left} runId={pair.left} which="left" day={diff.day} />
            <Column side={diff.right} runId={pair.right} which="right" day={diff.day} />
            <p className="diff__heading">difference</p>
          </div>

          <p className="diff__basis" style={{ color: PAL.textFaint }}>
            Both folded to tick {diff.at_tick}, which is where day {diff.day} opens. Each column is
            that timeline's own log replayed by the kernel's fold — not a projection, and not a
            second reading.
          </p>

          <ul className="diff__rows">
            {diff.rows.map((row) => {
              const direction = rowDirection(row)
              return (
                <li className="diff__row" key={row.key} data-metric={row.key}>
                  <span className="diff__name">
                    {row.label}
                    {row.unit !== '' && (
                      <span className="diff__unit" style={{ color: PAL.textFaint }}>
                        {' '}
                        {row.unit}
                      </span>
                    )}
                  </span>
                  <span className="diff__figure" data-figure={`${row.key}:left`}>
                    {figureText(row.left)}
                    <Mark of={figureLabel(diff.left, row)} />
                  </span>
                  <span className="diff__figure" data-figure={`${row.key}:right`}>
                    {figureText(row.right)}
                    <Mark of={figureLabel(diff.right, row)} />
                  </span>
                  <span
                    className="diff__delta"
                    data-figure={`${row.key}:delta`}
                    data-direction={direction}
                    style={{ color: directionColour(direction) }}
                  >
                    {row.delta === null || row.delta === 0
                      ? figureText(row.delta)
                      : `${row.delta > 0 ? '+' : ''}${row.delta}`}
                    <Mark of={`${row.label}, the difference`} />
                  </span>
                </li>
              )
            })}
          </ul>
        </>
      )}

      {lag !== '' && (
        <p className="diff__lag" style={{ color: PAL.textFaint }}>
          {lag}
        </p>
      )}
    </section>
  )
}

/** One side's heading: which timeline it is, and what it was doing at the day compared at. */
function Column({
  side,
  runId,
  which,
  day,
}: {
  side: DiffSideWire | null
  runId: string
  which: 'left' | 'right'
  day: number
}) {
  return (
    <article className="diff__side" data-side={which} data-run={runId}>
      <p className="diff__id">{shortId(runId)}</p>
      <p className="diff__where" style={{ color: PAL.textFaint }}>
        {describeSide(side, day)}
      </p>
      {side !== null && (
        <p className="diff__through" style={{ color: PAL.textFaint }}>
          {/* The provenance, stated rather than implied: every figure in this column came from
              folding this run's log through this sequence. */}
          folded through sequence {side.through_seq}
        </p>
      )}
    </article>
  )
}
