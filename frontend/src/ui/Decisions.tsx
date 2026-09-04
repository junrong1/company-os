/**
 * What you decided, and what else you could have chosen (U25: M44).
 *
 * **The entry point to the product's central beat.** Everything else about forking existed
 * already — the verb, the tree, the switch — and none of it was reachable without leaving the
 * game: a fork had to be posted by hand, because nothing in the client surfaced a *resolved*
 * decision. The tray holds what is open, the DAG holds items, and the store threw the option,
 * the tick and the sequence away on the way past. This panel is the other half.
 *
 * **A fork is two calls, and the second one is what makes it visible.** Forking alone leaves the
 * player looking at the same office at the same tick, which is the beat with its outcome removed;
 * so a successful fork switches into the child and hands it upwards, and the shell opens the
 * Universe on the new node. Both calls are injectable for the same reason the tree's are: a test
 * of this behaviour must not need a server.
 *
 * **The switch is asked for at rate zero explicitly**, though a fresh child is paused anyway.
 * It only bites on a retry — the same alternative forked twice is one timeline, by design — and
 * arriving inside a timeline already running at ×3 would spend sim-time in a world the player has
 * not looked at yet.
 */

import { useCallback, useMemo, useState } from 'react'
import { useShallow } from 'zustand/react/shallow'

import { type MetricDef, NO_METRIC_DEFS, PAL } from '../design/tokens'
import { forkTimeline, switchTimeline } from '../net/gateway'
import { useRunStore } from '../net/store'
import { OptionConsequence } from './Consequence'
import { type Alternative, type PastDecision, pastDecisions } from './panels-model'

export interface DecisionsPanelProps {
  /**
   * Which run's decisions these are, and the timeline a fork is taken *from*.
   *
   * Absent means the panel renders its own absence rather than a list of buttons that cannot
   * say which run they would fork. The same judgement the org panel makes about a memory.
   */
  runId?: string
  /**
   * Land in the timeline the fork produced. Called only after the backend moved the clock into
   * it, so the player cannot end up looking at a timeline nobody resumed.
   */
  onEntered?: (childRunId: string, rate: number) => void
  /** Injectable for the suite, which mounts this without a gateway. Stable across renders. */
  forker?: typeof forkTimeline
  /** Injectable for the same reason, and it is the second half — a fork that is not entered. */
  switcher?: typeof switchTimeline
}

export function DecisionsPanel({
  runId,
  onEntered,
  forker,
  switcher,
}: DecisionsPanelProps) {
  const decisions = useRunStore(useShallow((state) => state.decisions))
  const catalog = useRunStore(useShallow((state) => state.genesis?.catalog ?? []))
  // Genesis is written once and never replaced, so this is stable by reference.
  const metricDefs = useRunStore((state) => state.genesis?.metricDefs) ?? NO_METRIC_DEFS

  // Which alternative is in flight, as `key:optionIndex`. One at a time: a fork writes a run,
  // and two in flight would leave the player being switched into whichever answered last.
  const [forking, setForking] = useState<string | null>(null)
  const [refusal, setRefusal] = useState<string | null>(null)

  const cards = useMemo(() => pastDecisions(decisions, catalog), [decisions, catalog])

  const fork = useCallback(
    (decision: PastDecision, optionIndex: number) => {
      if (runId === undefined) return
      setRefusal(null)
      setForking(`${decision.key}:${optionIndex}`)

      const post = forker ?? forkTimeline
      const enter = switcher ?? switchTimeline

      post(runId, decision.atSeq, optionIndex)
        .then((forked) => {
          if (forked.refusal) {
            // A refusal is a 200 with a sentence — a full lineage, a sequence that is not a
            // decision, a prefix above the size bound. Shown, not swallowed: the button would
            // otherwise read as dead at exactly the moment the answer is interesting.
            setRefusal(forked.refusal)
            setForking(null)
            return
          }

          return enter(runId, forked.child_run_id, 0).then((switched) => {
            if (switched.refusal) {
              // The timeline exists — it is on the tree and it can be entered from there — so
              // this says the switch failed rather than the fork, which are different repairs.
              setRefusal(
                `The timeline was made, and the clock did not move into it: ${switched.refusal} ` +
                  'It is on the Universe tree.',
              )
              setForking(null)
              return
            }
            // With the rate the switch reported, which for a fresh child is zero. Without it
            // the store's default stands in, and the clock control reads ×1 over a paused world.
            onEntered?.(forked.child_run_id, switched.rate)
            setForking(null)
          })
        })
        .catch((cause: unknown) => {
          setRefusal(cause instanceof Error ? cause.message : String(cause))
          setForking(null)
        })
    },
    [forker, onEntered, runId, switcher],
  )

  return (
    <section className="panel" data-panel="decisions" data-decided={cards.length}>
      <h2>Decided</h2>

      {cards.length === 0 && (
        <p className="hint">
          Nothing has been settled yet. Every decision you take lands here, and you can come back
          and take it differently.
        </p>
      )}

      {cards.map((decision) => (
        <DecisionCard
          key={decision.key}
          decision={decision}
          metricDefs={metricDefs}
          forking={forking}
          canFork={runId !== undefined && decision.unforkable === ''}
          onFork={fork}
        />
      ))}

      {refusal !== null && (
        <p className="decided__refusal" style={{ color: PAL.textFaint }}>
          {refusal}
        </p>
      )}
    </section>
  )
}

function DecisionCard({
  decision,
  metricDefs,
  forking,
  canFork,
  onFork,
}: {
  decision: PastDecision
  metricDefs: readonly MetricDef[]
  forking: string | null
  canFork: boolean
  onFork: (decision: PastDecision, optionIndex: number) => void
}) {
  return (
    <article className="decided" data-forkable={canFork}>
      <p className="decision__item">{decision.title}</p>
      {decision.label !== '' && <p className="decided__label">{decision.label}</p>}

      <p className="decided__taken">
        <span className="decided__marker" aria-hidden="true">
          ✓
        </span>
        {decision.taken}
        <span className="decided__route" style={{ color: PAL.textFaint }}>
          {/* Which route it was settled by, because it is the difference the product is about:
              in person the tacit line came out, from the tray it did not. Recorded on the event
              and therefore on the record, so the card states it rather than implying it. */}
          {decision.inPerson ? ' · in person' : ' · from the tray'}
        </span>
      </p>

      {decision.unforkable !== '' && (
        <p className="decided__unforkable" style={{ color: PAL.textFaint }}>
          {decision.unforkable}
        </p>
      )}

      {decision.alternatives.map((alternative) => (
        <AlternativeRow
          key={alternative.option.label}
          decision={decision}
          alternative={alternative}
          metricDefs={metricDefs}
          forking={forking}
          canFork={canFork}
          onFork={onFork}
        />
      ))}
    </article>
  )
}

function AlternativeRow({
  decision,
  alternative,
  metricDefs,
  forking,
  canFork,
  onFork,
}: {
  decision: PastDecision
  alternative: Alternative
  metricDefs: readonly MetricDef[]
  forking: string | null
  canFork: boolean
  onFork: (decision: PastDecision, optionIndex: number) => void
}) {
  const mine = `${decision.key}:${alternative.index}`

  return (
    <div className="alternative">
      <span className="alternative__label">{alternative.option.label}</span>
      <span className="alternative__detail">{alternative.option.detail}</span>
      {/* The same component the tray and the conversation render, so three surfaces cannot
          price one option three ways — and every figure carries its own marking, which is what
          keeps this panel inside R28's sweep rather than beside it. */}
      <OptionConsequence option={alternative.option} metricDefs={metricDefs} />
      <button
        type="button"
        className="alternative__fork"
        // Disabled while any fork is in flight, not only this one: a fork writes a run, and two
        // answers would switch the player into whichever landed second.
        disabled={!canFork || forking !== null}
        onClick={() => onFork(decision, alternative.index)}
        title="Branch a timeline that takes this option instead"
      >
        {forking === mine ? 'Forking…' : 'Take this instead'}
      </button>
    </div>
  )
}
