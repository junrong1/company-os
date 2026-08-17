import { useCallback, useEffect, useState } from 'react'
import './App.css'
import {
  GatewayUnreachable,
  type ScenarioChoice,
  type ServiceStatus,
  createRun,
  fetchGatewayStatus,
  fetchScenarios,
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

/**
 * The name the backend gives its shipped company, used only to *preselect* it in the picker.
 *
 * Never used as a value to send when the list has not arrived. "We could not ask which companies
 * exist" and "the user chose the shipped one" are different states, and collapsing them would have
 * the client assert a name it never learned — which is wrong the first time a deployment's default
 * is called something else.
 */
const SHIPPED_SCENARIO = 'default'

export default function App() {
  const [runId, setRunId] = useState(() =>
    typeof window === 'undefined' ? null : runIdFromLocation(window.location.search),
  )
  const [status, setStatus] = useState<ServiceStatus | null>(null)
  const [scenarios, setScenarios] = useState<ScenarioChoice[]>([])
  // Empty means no choice has been made — the server picks. It stays empty for as long as the
  // list has not arrived, so a backend that cannot list its companies still starts a run of
  // whichever one it considers its own default.
  const [chosen, setChosen] = useState('')
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

    // Its own failure path, and deliberately a quiet one: the choice is an offer, and a
    // backend that cannot list its companies can still start a run of the shipped one. Raising
    // this as the page's error would replace a working start button with a diagnostic.
    fetchScenarios(controller.signal)
      .then((offered) => {
        setScenarios(offered)
        // Preselect the shipped company where it is on offer, and otherwise the first one that
        // would actually load — so the button never starts by submitting a name the server has
        // already said it will refuse.
        const playable = offered.filter((entry) => entry.loadable)
        if (playable.length === 0) return
        const shipped = playable.find((entry) => entry.id === SHIPPED_SCENARIO)
        setChosen((shipped ?? playable[0]).id)
      })
      .catch(() => setScenarios([]))

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

    createRun({ scenario: chosen }, controller.signal)
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
  }, [reset, chosen])

  if (runId !== null) return <Shell key={runId} runId={runId} onStartRun={start} />

  return (
    <main className="shell">
      <header>
        <h1>Company OS</h1>
        <p className="sub">
          Start a run, or open <code>?run=&lt;id&gt;</code> to attach to one.
        </p>

        {scenarios.length > 0 && (
          <Companies chosen={chosen} offered={scenarios} onChoose={setChosen} />
        )}

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

/**
 * Which company to start.
 *
 * Radios rather than a select, because the choice is between two descriptions and a select
 * shows one line of one of them. What separates these companies is the summary — nine people
 * against ten, a returns pile against a duplicate order entry — and a picker that hid it would
 * be asking somebody to choose between two names.
 *
 * A file the loader refuses is shown disabled with its reason. It cannot be started, and it
 * would be worse to omit it: the author of a scenario that will not load is exactly the person
 * standing in front of this list, and a silently missing file reads as a file that never saved.
 */
function Companies({
  chosen,
  offered,
  onChoose,
}: {
  chosen: string
  offered: ScenarioChoice[]
  onChoose: (id: string) => void
}) {
  return (
    <fieldset className="companies">
      <legend>Company</legend>
      {offered.map((entry) => (
        <label key={entry.id} className="company" data-loadable={entry.loadable}>
          <input
            type="radio"
            name="scenario"
            value={entry.id}
            checked={chosen === entry.id}
            disabled={!entry.loadable}
            onChange={() => onChoose(entry.id)}
          />
          <span className="company__body">
            <span className="company__title">{entry.title ?? entry.id}</span>
            {entry.loadable ? (
              <>
                <span className="company__summary">{entry.summary}</span>
                <span className="company__size">
                  {entry.people} people · {entry.items} pieces of work
                </span>
              </>
            ) : (
              <span className="company__refusal">{entry.refusal}</span>
            )}
          </span>
        </label>
      ))}
    </fieldset>
  )
}
