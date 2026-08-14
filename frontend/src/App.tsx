import { useCallback, useEffect, useState } from 'react'
import './App.css'
import {
  GatewayUnreachable,
  type ServiceStatus,
  createRun,
  fetchGatewayStatus,
} from './net/gateway'
import { useRunStore } from './net/store'
import { rememberRunInLocation, runIdFromLocation } from './net/runid'
import { Shell } from './ui/Shell'

/**
 * The entry point: open a run, or start one.
 *
 * A session used to begin by running a command in a terminal, because nothing in the client
 * reached the gateway's creation route — so the page could only ever attach to a run some other
 * process had already made, and with no run named in the URL it had nothing to offer but the
 * gateway's own health. That health view is still the fallback when the gateway cannot be
 * reached, since it is the surface that can say why.
 *
 * Creation stays its own route rather than a command. The rule that no command brings a run
 * into being is what keeps a typo'd id from conjuring a simulation, and it is untouched here.
 */
/** How long to wait for the gateway to create a run before saying it did not answer. */
const CREATE_RUN_TIMEOUT_MS = 10_000

export default function App() {
  const [runId, setRunId] = useState(() =>
    typeof window === 'undefined' ? null : runIdFromLocation(window.location.search),
  )
  const [status, setStatus] = useState<ServiceStatus | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [starting, setStarting] = useState(false)

  const reset = useRunStore((state) => state.reset)

  useEffect(() => {
    if (runId !== null) return

    // Aborting on cleanup, and cleanup is symmetric with setup. StrictMode runs this effect
    // twice in development on purpose; an in-flight fetch from the discarded first pass would
    // otherwise resolve into an unmounted component.
    const controller = new AbortController()

    fetchGatewayStatus(controller.signal)
      .then((next) => {
        setStatus(next)
        setError(null)
      })
      .catch((cause: unknown) => {
        if (controller.signal.aborted) return
        setError(cause instanceof GatewayUnreachable ? cause.message : String(cause))
      })

    return () => controller.abort()
  }, [runId])

  const start = useCallback(() => {
    setStarting(true)
    setError(null)

    // Bounded, unlike the status fetch beside it, which is cancelled by its effect's cleanup.
    // Nothing cancels this one — a hung gateway would leave the button reading "Starting…"
    // for as long as the page stayed open, with a reload the only way out.
    const controller = new AbortController()
    const timeout = window.setTimeout(() => controller.abort(), CREATE_RUN_TIMEOUT_MS)

    createRun({}, controller.signal)
      .then((run) => {
        // Cleared before attaching, not after: the store still holds the finished run, and the
        // shell would render its terminal banner and its old floor over the new run until the
        // first frames arrived.
        reset()
        // In the URL so a reload re-attaches to this run rather than quietly starting another.
        rememberRunInLocation(run.run_id)
        setRunId(run.run_id)
      })
      .catch((cause: unknown) => {
        // Reported rather than swallowed: a start button that does nothing leaves a page with
        // no other way to begin.
        setError(
          controller.signal.aborted
            ? `the gateway did not answer within ${CREATE_RUN_TIMEOUT_MS / 1000}s`
            : cause instanceof Error
              ? cause.message
              : String(cause),
        )
      })
      .finally(() => {
        window.clearTimeout(timeout)
        setStarting(false)
      })
  }, [reset])

  if (runId !== null) return <Shell key={runId} runId={runId} onStartRun={start} />

  return (
    <main className="shell">
      <header>
        <h1>Company OS</h1>
        <p className="sub">
          Start a run, or open <code>?run=&lt;id&gt;</code> to attach to one.
        </p>
        <button type="button" onClick={start} disabled={starting}>
          {starting ? 'Starting…' : 'Start a run'}
        </button>
      </header>

      {error && (
        <section className="card bad">
          <h2>gateway</h2>
          <p>{error}</p>
          <p className="hint">
            Start the stack with <code>docker compose up</code>, or run the gateway directly
            and re-load.
          </p>
        </section>
      )}

      {status && (
        <section className={`card ${status.healthy ? 'ok' : 'bad'}`}>
          <h2>
            {status.service} <span className="pill">{status.status}</span>
          </h2>
          <dl>
            <dt>git sha</dt>
            <dd>{status.git_sha}</dd>
            <dt>rules version</dt>
            <dd>{status.versions.rules ?? <em>not established yet</em>}</dd>
            <dt>event schema</dt>
            <dd>{status.versions.event_schema ?? <em>not established yet</em>}</dd>
            <dt>store DDL</dt>
            <dd>{status.versions.store_ddl ?? <em>not established yet</em>}</dd>
          </dl>

          <h3>dependencies</h3>
          <ul>
            {status.dependencies.map((dependency) => (
              <li key={dependency.name} className={dependency.reachable ? 'up' : 'down'}>
                <strong>{dependency.name}</strong>
                {dependency.required ? '' : ' (optional)'} — {dependency.detail}
              </li>
            ))}
            {status.dependencies.length === 0 && <li className="up">none declared</li>}
          </ul>
        </section>
      )}

      {!status && !error && <p className="sub">contacting gateway…</p>}
    </main>
  )
}
