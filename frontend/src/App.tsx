import { useEffect, useState } from 'react'
import './App.css'
import {
  fetchGatewayStatus,
  GatewayUnreachable,
  type ServiceStatus,
} from './net/gateway'
import { runIdFromLocation } from './net/runid'
import { Shell } from './ui/Shell'

/**
 * The entry point: find a run, or explain why there is nothing to show.
 *
 * The gateway's own status is the fallback view, and that is deliberate — it is the surface a
 * contributor already has open when something is wrong, and it can explain that the kernel is
 * down. U1 shipped exactly this; the office, HUD and DAG now sit in front of it when there is
 * a run to render.
 */
export default function App() {
  const [runId] = useState(() =>
    typeof window === 'undefined' ? null : runIdFromLocation(window.location.search),
  )
  const [status, setStatus] = useState<ServiceStatus | null>(null)
  const [error, setError] = useState<string | null>(null)

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

  if (runId !== null) return <Shell runId={runId} />

  return (
    <main className="shell">
      <header>
        <h1>Company OS</h1>
        <p className="sub">
          No run selected. Open <code>?run=&lt;id&gt;</code> to attach to one.
        </p>
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
