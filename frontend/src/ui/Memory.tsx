/**
 * What a director remembers about their line, as the CEO reads it (M36, M37, M38).
 *
 * **The derived half renders first, and the prose fills in behind a stated line.** The selection is
 * a read of the log and arrives at once; the summary is a provider call and does not. So the panel
 * asks twice — once to open, once for the note — and it is never blank while a model is thinking.
 * The line it shows in the meantime is a sentence, not a spinner: a spinner says "wait", and there
 * is nothing here to wait for. The events are the answer; the prose is a convenience over them.
 *
 * **Every sentence carries the events it was written over.** That is the panel's whole promise and
 * the reason the backend refuses a summary sentence that cites nothing: a reader can always ask
 * which events a claim came from, and the answer is on the claim. The chips are provenance rather
 * than links, for the same reason the bench block's citations are — nothing in this build resolves a
 * sequence to its event yet, which is the report's job (M55).
 *
 * **Raw memory is not reachable from here** (M37). There is no "show me everything" affordance,
 * because there is nothing behind the panel to show: the route answers with the selection, and the
 * whole scoped slice never crosses the wire.
 */

import { useEffect, useState } from 'react'

import { PAL } from '../design/tokens'
import {
  GatewayUnreachable,
  type MemoryWire,
  MemoryUnavailable,
  fetchMemory,
} from '../net/gateway'
import { Measured } from './Marking'
import {
  type MemoryView,
  awaitingProse,
  memoryScale,
  memoryStamp,
  toMemoryView,
} from './memory-model'

export interface MemoryFetcher {
  (
    runId: string,
    directorId: string,
    options?: { summary?: boolean },
    signal?: AbortSignal,
  ): Promise<MemoryWire>
}

export interface MemoryPanelProps {
  runId: string
  directorId: string
  /** What to call them in the heading. The id is the fallback, never nothing. */
  name?: string
  /**
   * Injectable so the suite can mount the panel without a gateway.
   *
   * Must be referentially stable across renders, like the shell's stream factory: it is an effect
   * dependency, and a fresh inline arrow would re-read the memory on every render.
   */
  fetcher?: MemoryFetcher
}

export function MemoryPanel({ runId, directorId, name, fetcher }: MemoryPanelProps) {
  const [view, setView] = useState<MemoryView | null>(null)
  const [error, setError] = useState<string | null>(null)
  const read = fetcher ?? fetchMemory

  useEffect(() => {
    // Aborting on cleanup, and the cleanup is symmetric with the setup: strict mode runs this
    // effect twice in development on purpose, and an in-flight read from the discarded first pass
    // would otherwise resolve into an unmounted panel.
    const controller = new AbortController()
    setView(null)
    setError(null)

    read(runId, directorId, { summary: false }, controller.signal)
      .then((wire) => {
        if (controller.signal.aborted) return
        const derived = toMemoryView(wire)
        setView(derived)
        // Only when a bench is actually present, which is what the first read's `pending` means.
        // On a keyless run this is where the panel stops, with no second call and no line that
        // could never resolve (M38).
        if (!awaitingProse(derived)) return
        return read(runId, directorId, { summary: true }, controller.signal).then((withProse) => {
          if (controller.signal.aborted) return
          setView(toMemoryView(withProse))
        })
      })
      .catch((cause: unknown) => {
        if (controller.signal.aborted) return
        // The two failures say different things, and the panel says them differently: a 404 is
        // "they do not have one", a 503 is "we could not read it", and a transport failure is the
        // gateway being gone. Rendering one sentence for all three would let a deployment fault
        // read as a person with no history.
        setError(
          cause instanceof MemoryUnavailable || cause instanceof GatewayUnreachable
            ? cause.message
            : String(cause),
        )
      })

    return () => controller.abort()
  }, [read, runId, directorId])

  const who = name ?? directorId

  return (
    <section className="memory" data-memory={view?.status ?? (error === null ? 'loading' : 'error')}>
      <h4 className="memory__heading">
        What {who} remembers
        {view !== null && (
          <span className="memory__stamp" style={{ color: PAL.textFaint }}>
            {memoryStamp(view)}
            {/* A sim-day is read off the log rather than authored, so it carries the measured
                marking rather than the authored-tuning one. The panel's claim about every figure
                on it is that it resolves to a row. */}
            <Measured of={`the day ${who}'s memory is read as of`} />
          </span>
        )}
      </h4>

      {error !== null && (
        <p className="memory__error" style={{ color: PAL.textFaint }}>
          {error}
        </p>
      )}

      {view === null && error === null && (
        <p className="memory__loading" style={{ color: PAL.textFaint }}>
          Reading their line.
        </p>
      )}

      {view !== null && (
        <>
          {view.because !== '' && (
            <p className="memory__because" style={{ color: PAL.textFaint }}>
              {view.because}
            </p>
          )}

          {view.points.length > 0 && (
            <ul className="memory__summary">
              {view.points.map((point) => (
                <li key={point.text} className="memory__point">
                  {point.text}
                  <span className="memory__cites" style={{ color: PAL.textFaint }}>
                    {point.citations.map((seq) => `#${seq}`).join(' ')}
                    <Measured of="the events this sentence was written over" />
                  </span>
                </li>
              ))}
            </ul>
          )}

          <p className="memory__scale" style={{ color: PAL.textFaint }}>
            {memoryScale(view)}
            <Measured of={`how much of ${who}'s line this shows`} />
          </p>

          {view.events.length === 0 ? (
            <p className="memory__empty" style={{ color: PAL.textFaint }}>
              Nothing has happened on this line yet.
            </p>
          ) : (
            <ol className="memory__events">
              {view.events.map((event) => (
                <li key={event.seq} className="memory__event" data-kind={event.kind}>
                  <span className="memory__day">
                    day {event.day}
                    <Measured of={`the day of event ${event.seq}`} />
                  </span>
                  <span className="memory__line">{event.line}</span>
                  <span className="memory__seq" style={{ color: PAL.textFaint }}>
                    #{event.seq}
                    <Measured of={`the log sequence of event ${event.seq}`} />
                  </span>
                </li>
              ))}
            </ol>
          )}
        </>
      )}
    </section>
  )
}

/**
 * The button that opens one director's memory, and the panel under it.
 *
 * Mounted in two places — the org panel's director row and the conversation header — because the
 * two are different questions with the same answer: "what has been going on in that line" from the
 * rail, and "what do you remember" while standing in front of them. One component either way, so
 * the two surfaces cannot show a director two different histories.
 *
 * Closed by default, and the read happens on open rather than on mount. A memory costs a log read
 * and, on the first open of a changed selection, one model call — so opening four panels because
 * the rail rendered is not something the player asked for.
 */
export function MemoryAffordance({ runId, directorId, name, fetcher }: MemoryPanelProps) {
  const [open, setOpen] = useState(false)
  const who = name ?? directorId

  return (
    <div className="memory-affordance" data-open={open}>
      <button
        type="button"
        className="memory-affordance__toggle"
        aria-expanded={open}
        onClick={() => setOpen((current) => !current)}
        title={`What ${who} remembers about their line`}
      >
        {open ? 'Hide memory' : 'Memory'}
      </button>
      {open && (
        <MemoryPanel runId={runId} directorId={directorId} name={name} fetcher={fetcher} />
      )}
    </div>
  )
}
