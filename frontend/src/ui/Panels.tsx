/**
 * The panels: the org chart, the backlog, the tray, and what the run has produced.
 *
 * Ported from the prototype's section 10 (`company-os.html:2719`), minus the `innerHTML`
 * rebuilding and the change-signature cache that existed to work around it. React's
 * reconciliation *is* that cache, done properly — the prototype only needed
 * `changed(key, sig)` because rebuilding markup destroys focus and selection.
 *
 * **Panels select narrowly.** Every subscription here is the smallest slice that panel renders,
 * so a burst of events that moves cash does not re-render the org chart. Object and array
 * selectors go through `useShallow` for the reason the store module explains at length.
 *
 * **Inspecting shows less than conversation.** A person's row shows observable state only —
 * what they hold, how far along it is, whether they are stopped. The tacit line stays earned by
 * walking over, and it appears here only on a deliverable that already recorded one. This is
 * the one place where adopting the obvious affordance wholesale would dissolve the mechanic the
 * product is built on.
 */

import { useMemo, useState } from 'react'
import { useShallow } from 'zustand/react/shallow'

import { type MetricDef, NO_METRIC_DEFS, PAL, RESERVED_BEAM, deptColour } from '../design/tokens'
import { type CatalogEntry, type ItemStatus, type TrayEntry, useRunStore } from '../net/store'
import { CompareAffordance } from './Comparison'
import type { CompareSender } from './comparison-model'
import { OptionConsequence } from './Consequence'
import { FROM_TRAY_COST, resolvePayload } from './conversation-model'
import { STATUS_LABEL, lockReason, progressPercent } from './panels-model'

export interface CommandSender {
  (kind: string, payload: Record<string, unknown>): void
}

// =========================================================================
// The org chart
// =========================================================================

/**
 * Who is doing what.
 *
 * The reporting line is the structure, not the room: Priya sits in Accounting and reports to
 * Admin, and the deliberate mismatch in the sample data is the whole reason the org chart is a
 * constraint rather than a decoration.
 */
export function OrgPanel({ onWalkTo }: { onWalkTo?: (personId: string) => void }) {
  const roster = useRunStore(useShallow((state) => state.genesis?.roster ?? {}))
  const people = useRunStore(
    useShallow((state) =>
      Object.fromEntries(
        Object.entries(state.people).map(([id, person]) => [
          id,
          `${person.state}|${person.itemId}|${person.waiting ? '1' : '0'}`,
        ]),
      ),
    ),
  )
  const items = useRunStore(
    useShallow((state) =>
      Object.fromEntries(
        Object.entries(state.items).map(([id, item]) => [id, item.doneUnits]),
      ),
    ),
  )
  const catalog = useRunStore(useShallow((state) => state.genesis?.catalog ?? []))

  const effort = useMemo(() => {
    const byId: Record<string, number> = {}
    for (const entry of catalog) byId[entry.id] = entry.effort_units
    return byId
  }, [catalog])

  const lines = useMemo(() => {
    const grouped: Record<string, string[]> = {}
    for (const [id, entry] of Object.entries(roster)) {
      const line = entry.rank === 'director' ? id : entry.mgr || id
      grouped[line] = grouped[line] ?? []
      if (id !== line) grouped[line].push(id)
    }
    return grouped
  }, [roster])

  const row = (personId: string, isDirector: boolean) => {
    const encoded = people[personId] ?? 'idle||0'
    const [state, itemId, waiting] = encoded.split('|')
    const entry = roster[personId]

    let task = 'Free right now'
    if (waiting === '1') task = 'Needs you'
    else if (itemId !== '')
      task = `${itemId} — ${progressPercent(items[itemId] ?? 0, effort[itemId] ?? 0)}%`
    else if (state === 'walking') task = 'Walking'
    else if (state === 'meeting') task = 'In a meeting'

    return (
      <button
        type="button"
        key={personId}
        className={isDirector ? 'person' : 'person person--sub'}
        data-waiting={waiting}
        onClick={() => onWalkTo?.(personId)}
        title="Walk over to their desk"
      >
        <span
          className="person__stripe"
          style={{ background: deptColour(entry?.dept ?? '') }}
          aria-hidden="true"
        />
        <span className="person__body">
          <span className="person__name">{personId}</span>
          <span className="person__task">{task}</span>
        </span>
        {waiting === '1' && (
          // The one place amber is correct: a person is waiting on your decision.
          <span className="person__beam" style={{ background: RESERVED_BEAM }} aria-label="waiting" />
        )}
      </button>
    )
  }

  return (
    <section className="panel" data-panel="org">
      <h2>Org</h2>
      {Object.entries(lines)
        .sort(([left], [right]) => left.localeCompare(right))
        .map(([director, members]) => (
          <div className="line" key={director}>
            {row(director, true)}
            {members.sort().map((member) => row(member, false))}
          </div>
        ))}
    </section>
  )
}

// =========================================================================
// The work
// =========================================================================

export function WorkPanel({ onAssign }: { onAssign?: CommandSender }) {
  const catalog = useRunStore(useShallow((state) => state.genesis?.catalog ?? []))
  const items = useRunStore(
    useShallow((state) =>
      Object.fromEntries(
        Object.entries(state.items).map(([id, item]) => [
          id,
          `${item.status}|${item.assignee}|${item.doneUnits}|${item.unlocked ? '1' : '0'}`,
        ]),
      ),
    ),
  )
  const visibility = useRunStore((state) => state.metrics.visibility ?? 0)

  const done = useMemo(() => {
    const finished = new Set<string>()
    for (const [id, encoded] of Object.entries(items)) {
      if (encoded.startsWith('done|')) finished.add(id)
    }
    return finished
  }, [items])

  return (
    <section className="panel" data-panel="work">
      <h2>Work</h2>
      {catalog.map((entry) => {
        const encoded = items[entry.id] ?? 'backlog|||0'
        const [status, assignee, doneUnits, unlocked] = encoded.split('|')
        // Availability comes off the wire; the reason is only the sentence explaining it. The
        // kernel owns whether an item's gates have cleared (`is_unlocked`), and deciding it here
        // from the authored gates would be a second implementation of that rule — the two already
        // check visibility and prerequisites in opposite orders, so they would eventually disagree
        // while each passed its own tests.
        const available = unlocked === '1'
        const reason = available ? '' : lockReason(entry, visibility, done)

        return (
          <article className="item" key={entry.id} data-status={status} data-available={available}>
            <span
              className="item__stripe"
              style={{ background: deptColour(entry.dept) }}
              aria-hidden="true"
            />
            <div className="item__body">
              <p className="item__title">{entry.title}</p>
              <p className="item__meta">
                {STATUS_LABEL[status as ItemStatus] ?? status}
                {assignee !== '' && ` · ${assignee}`}
                {status !== 'backlog' &&
                  ` · ${progressPercent(Number(doneUnits), entry.effort_units)}%`}
              </p>
              {!available && <p className="item__locked">{reason}</p>}
              {available && status === 'backlog' && (
                <div className="item__actions">
                  <button
                    type="button"
                    onClick={() =>
                      onAssign?.('assign_work', { item: entry.id, via_manager: true })
                    }
                  >
                    Route through the director
                  </button>
                  <button
                    type="button"
                    onClick={() =>
                      onAssign?.('assign_work', {
                        item: entry.id,
                        person: entry.want,
                        via_manager: false,
                      })
                    }
                    title="Faster, but their director will not know about it"
                  >
                    Straight to {entry.want}
                  </button>
                </div>
              )}
            </div>
          </article>
        )
      })}
    </section>
  )
}

// =========================================================================
// The tray
// =========================================================================

/**
 * The decisions waiting on the CEO.
 *
 * Resolving from here is deliberately the lesser route: it settles the question and records
 * that no tacit line was surfaced. The panel says so rather than leaving the CEO to discover
 * it in the report, because a cost you only learn about afterwards is not a choice.
 */
export function TrayPanel({
  onResolve,
  onCompare,
}: {
  onResolve?: CommandSender
  onCompare?: CompareSender
}) {
  const tray = useRunStore(useShallow((state) => state.tray))
  const catalog = useRunStore(useShallow((state) => state.genesis?.catalog ?? []))
  // Genesis is written once and never replaced, so this is stable by reference.
  const metricDefs = useRunStore((state) => state.genesis?.metricDefs) ?? NO_METRIC_DEFS

  const byId = useMemo(() => {
    const index: Record<string, CatalogEntry> = {}
    for (const entry of catalog) index[entry.id] = entry
    return index
  }, [catalog])

  return (
    <section className="panel" data-panel="tray" data-waiting={tray.length}>
      <h2>Waiting on you</h2>
      {tray.length === 0 && <p className="hint">Nobody is stopped.</p>}
      {tray.map((entry) => (
        <TrayCard
          key={`${entry.itemId}:${entry.cpIndex}`}
          entry={entry}
          item={byId[entry.itemId]}
          metricDefs={metricDefs}
          onResolve={onResolve}
          onCompare={onCompare}
        />
      ))}
    </section>
  )
}

function TrayCard({
  entry,
  item,
  metricDefs,
  onResolve,
  onCompare,
}: {
  entry: TrayEntry
  item: CatalogEntry | undefined
  metricDefs: readonly MetricDef[]
  onResolve?: CommandSender
  onCompare?: CompareSender
}) {
  const [chosen, setChosen] = useState<number | null>(null)
  const checkpoint = item?.checkpoints[entry.cpIndex]

  return (
    <article className="decision" data-kind={entry.kind}>
      <p className="decision__item">{item?.title ?? entry.itemId}</p>
      <p className="decision__prompt">{checkpoint?.prompt ?? entry.label}</p>

      {(checkpoint?.options ?? []).map((option, index) => (
        <label key={option.label} className="option" data-chosen={chosen === index}>
          <input
            type="radio"
            name={`${entry.itemId}:${entry.cpIndex}`}
            checked={chosen === index}
            onChange={() => setChosen(index)}
          />
          <span className="option__label">{option.label}</span>
          <span className="option__detail">{option.detail}</span>
          {/* The same component the conversation renders, so the two surfaces cannot price one
              option two ways. The tray still withholds the tacit line — that is the mechanic,
              and it is held apart from the tray entry precisely so this card has nothing to
              leak. What it no longer withholds is the arithmetic. */}
          <OptionConsequence option={option} metricDefs={metricDefs} />
        </label>
      ))}

      <div className="decision__actions">
        <button
          type="button"
          disabled={chosen === null}
          onClick={() =>
            chosen !== null &&
            onResolve?.(
              'resolve_checkpoint',
              // The same payload builder the conversation uses, with the flag the other way
              // round. One place to read what distinguishes the two routes (R9).
              resolvePayload(entry.itemId, entry.cpIndex, chosen, false),
            )
          }
        >
          Settle from here
        </button>
        <p className="decision__cost" style={{ color: PAL.textFaint }}>
          {FROM_TRAY_COST} Walk over to hear what they know.
        </p>
      </div>

      {/* AE14: the comparison is reachable from here as well as from the conversation, and it
          reads the same model — so the two surfaces cannot disagree about where an option
          leads. `inPerson` is false because settling from the tray is what this card does, and
          the branches have to price the route the CEO is actually about to take. */}
      <CompareAffordance
        itemId={entry.itemId}
        cpIndex={entry.cpIndex}
        personId={entry.personId}
        inPerson={false}
        onCompare={onCompare}
      />
    </article>
  )
}

// =========================================================================
// What the run produced
// =========================================================================

export function OutputPanel() {
  const deliverables = useRunStore(useShallow((state) => state.deliverables))

  return (
    <section className="panel" data-panel="outputs">
      <h2>Produced</h2>
      {deliverables.length === 0 && <p className="hint">Nothing has shipped yet.</p>}
      {deliverables.map((output) => (
        <article className="output" key={`${output.itemId}:${output.day}`}>
          <p className="output__title">{output.title}</p>
          <p className="output__meta">
            {output.kind} · day {output.day}
          </p>
          <ul className="output__provenance">
            {output.provenance.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
          {output.tacit.length > 0 && (
            <ul className="output__tacit" style={{ color: PAL.tacit }}>
              {output.tacit.map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ul>
          )}
        </article>
      ))}
    </section>
  )
}

export function Panels({
  onCommand,
  onCompare,
  onWalkTo,
}: {
  onCommand?: CommandSender
  onCompare?: CompareSender
  onWalkTo?: (personId: string) => void
}) {
  return (
    <div className="panels">
      <TrayPanel onResolve={onCommand} onCompare={onCompare} />
      <OrgPanel onWalkTo={onWalkTo} />
      <WorkPanel onAssign={onCommand} />
      <OutputPanel />
    </div>
  )
}
