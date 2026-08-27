/**
 * Talking to the gateway.
 *
 * The backend is the client's only contact surface, and the client addresses it
 * at `/api` in both setups — nginx proxies that prefix under `docker compose up`,
 * the Vite dev server proxies it in development. Nothing here knows which of the
 * two it is running under, which is the point.
 */

/** One dependency as the status endpoint reports it. */
export interface DependencyStatus {
  name: string
  required: boolean
  reachable: boolean
  detail: string
  note?: string
}

/** The payload a contributor pastes into an issue. */
export interface ServiceStatus {
  service: string
  healthy: boolean
  status: 'healthy' | 'unhealthy'
  git_sha: string
  versions: {
    rules: string | null
    event_schema: number | null
    store_ddl: string | null
  }
  dependencies: DependencyStatus[]
  checked_at: string
  store?: { backend: string; at: string }
  degraded?: string[]
}

export const GATEWAY_BASE = '/api'

export class GatewayUnreachable extends Error {
  constructor(cause: unknown) {
    super(`gateway unreachable: ${cause instanceof Error ? cause.message : String(cause)}`)
    this.name = 'GatewayUnreachable'
  }
}

/**
 * Fetch the gateway's status.
 *
 * A 503 is a successful read of an unhealthy service, not a failed request: the
 * gateway answers 503 while reporting exactly which dependency is down, and
 * throwing that away would discard the only useful thing in the response. Only a
 * transport failure is an error here.
 */
export async function fetchGatewayStatus(signal?: AbortSignal): Promise<ServiceStatus> {
  let response: Response
  try {
    response = await fetch(`${GATEWAY_BASE}/status`, { signal })
  } catch (cause) {
    throw new GatewayUnreachable(cause)
  }

  if (response.status !== 200 && response.status !== 503) {
    throw new GatewayUnreachable(`unexpected status ${response.status}`)
  }

  return (await response.json()) as ServiceStatus
}

// =========================================================================
// Which company to run
// =========================================================================

/**
 * One company a run could be created against.
 *
 * `loadable` is false for a file in the scenarios directory that the loader refuses, and the
 * entry is still here — with the reason — rather than filtered out by the server. An author who
 * mistyped a key needs to see why their file is not on offer, and a list that quietly dropped it
 * would read as the file never having been saved.
 */
export interface ScenarioChoice {
  id: string
  title?: string
  summary?: string
  people?: number
  items?: number
  loadable: boolean
  refusal?: string
}

/**
 * The companies this backend can start a run of.
 *
 * Unreachable is not the same as none: the caller shows the start button either way, because a
 * run with no scenario named is a run of the shipped company and that path does not need this
 * list. So a failure here costs the choice, not the ability to begin.
 */
export async function fetchScenarios(signal?: AbortSignal): Promise<ScenarioChoice[]> {
  let response: Response
  try {
    response = await fetch(`${GATEWAY_BASE}/scenarios`, { signal })
  } catch (cause) {
    throw new GatewayUnreachable(cause)
  }

  if (!response.ok) throw new GatewayUnreachable(`unexpected status ${response.status}`)

  const body = (await response.json()) as { scenarios?: ScenarioChoice[] }
  return Array.isArray(body.scenarios) ? body.scenarios : []
}

// =========================================================================
// Starting a run
// =========================================================================

/** A run, as the creation route reports it. */
export interface CreatedRun {
  run_id: string
  run_seed: number
  tick: number
  rate: number
  terminal_reason: string
  head_seq: number
  active: boolean
  /** Which company it is a run of. `default` when the request named none. */
  scenario?: string
  /**
   * False when the id already existed and this call returned that run instead.
   *
   * Not an error: the run id is its own idempotency key, so a client retrying a request whose
   * response it never saw gets the run it made rather than a second one or a 409.
   */
  created: boolean
}

export class RunNotCreated extends Error {
  constructor(detail: string) {
    super(detail)
    this.name = 'RunNotCreated'
  }
}

/**
 * Start a run, and get back what to attach to.
 *
 * Creation stays its own route rather than a command, which is the rule that keeps a typo'd id
 * in a client from conjuring a simulation: `POST /runs/{id}/commands` answers not-found for an
 * unknown run on purpose. So this is a different verb on a different path, and the run id is
 * its own idempotency key.
 *
 * A failure is reported as an error to be shown rather than swallowed — a start button that
 * does nothing is the worst possible answer, since the page it leaves behind has no other way
 * to begin.
 */
export async function createRun(
  options: { runId?: string; runSeed?: number; scenario?: string } = {},
  signal?: AbortSignal,
): Promise<CreatedRun> {
  const body: Record<string, unknown> = {}
  if (options.runId !== undefined) body.run_id = options.runId
  if (options.runSeed !== undefined) body.run_seed = options.runSeed
  // A name, never a path — the backend resolves it inside its own scenarios directory and
  // refuses anything that could leave it. Omitted means the shipped company, which is what
  // every caller written before the choice existed keeps asking for.
  if (options.scenario !== undefined && options.scenario !== '') body.scenario = options.scenario

  let response: Response
  try {
    response = await fetch(`${GATEWAY_BASE}/runs`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(body),
      signal,
    })
  } catch (cause) {
    throw new GatewayUnreachable(cause)
  }

  if (!response.ok) {
    const detail = (await response.json().catch(() => ({}))) as { detail?: string }
    throw new RunNotCreated(
      detail.detail ?? `the gateway answered ${response.status} to a run creation`,
    )
  }

  return (await response.json()) as CreatedRun
}

// =========================================================================
// A director's memory (U14)
// =========================================================================

/**
 * One event in a director's memory, as the route reduces it.
 *
 * A wire shape, and deliberately the *reduced* one: a sequence, the tick and the sim-day it
 * happened on, the kind, who and what it was about, and one short detail. No payload crosses, and
 * the whole scoped slice does not either — what the client can address is the selection (M37).
 */
export interface MemoryEventWire {
  seq: number
  tick: number
  day: number
  kind: string
  person: string
  item: string
  detail: string
}

/** The note over the selection, or the stated absence of one. */
export interface MemorySummaryWire {
  /** `pending` · `written` · `absent` · `refused`. Four states, and the panel renders each. */
  status: string
  points: { text: string; citations: number[] }[]
  /** One of the backend's fallback reasons when `status` is `refused`; empty otherwise. */
  fallback: string
  /** The model that wrote it. Never a provider name — R6 keeps those out of everything shown. */
  model_identity: string
}

/** What a director remembers about their line, as the route answers it. */
export interface MemoryWire {
  director: string
  line: string[]
  as_of_tick: number
  as_of_day: number
  /** The day of the newest selected event. Not the same fact as `as_of_day`; see the panel. */
  through_day: number
  /** How many of this line's events the selection was drawn from. */
  considered: number
  selected: number
  events: MemoryEventWire[]
  summary: MemorySummaryWire
}

export class MemoryUnavailable extends Error {
  /** The status the gateway answered with, so the panel can tell 404 from 503. */
  readonly status: number

  constructor(status: number, detail: string) {
    super(detail)
    this.name = 'MemoryUnavailable'
    this.status = status
  }
}

/**
 * Read one director's memory.
 *
 * **Called twice per open, and that is the design.** The derived selection is a log read and comes
 * back at once; the prose is a provider call and does not. So the panel asks first without it,
 * renders, and asks again with `summary: true` — which is why this takes the flag rather than
 * hiding two functions behind one name. The events are identical either way: the flag buys prose
 * about them, never more of them.
 *
 * A 404 and a 503 are both thrown as `MemoryUnavailable` carrying the status, because the panel
 * says different things about them: not-found is "they do not have one", and 503 is "we could not
 * read it". Collapsing the two would let a deployment fault render as a person with no history.
 */
export async function fetchMemory(
  runId: string,
  directorId: string,
  options: { summary?: boolean } = {},
  signal?: AbortSignal,
): Promise<MemoryWire> {
  const query = options.summary === true ? '?summary=1' : ''
  const path = `${GATEWAY_BASE}/runs/${encodeURIComponent(runId)}/memory/${encodeURIComponent(
    directorId,
  )}${query}`

  let response: Response
  try {
    response = await fetch(path, { signal })
  } catch (cause) {
    throw new GatewayUnreachable(cause)
  }

  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as { detail?: string }
    throw new MemoryUnavailable(
      response.status,
      body.detail ?? `the gateway answered ${response.status} to a memory read`,
    )
  }

  return (await response.json()) as MemoryWire
}

// =========================================================================
// The Universe: the tree of timelines, and moving between them (U17)
// =========================================================================

/** One timeline, as `GET /runs/{id}/lineage` describes it. */
export interface TimelineWire {
  run_id: string
  parent_run_id: string
  forked_at_seq: number
  tick: number
  day: number
  rate: number
  head_seq: number
  terminal_reason: string
  created_at: string
  /** The decision that separated this timeline from its parent. Empty on the root. */
  item: string
  cp_index: number
  option_index: number
  parent_option_index: number
  choice: string
  parent_choice: string
}

/** The tree, as the route answers it. */
export interface LineageWire {
  root_run_id: string
  /** The run the caller asked about — where the player is standing. */
  asked_about: string
  /** How many timelines one lineage may hold, so the surface can say so before a refusal. */
  cap: number
  nodes: TimelineWire[]
}

export class LineageUnavailable extends Error {
  readonly status: number

  constructor(status: number, detail: string) {
    super(detail)
    this.name = 'LineageUnavailable'
    this.status = status
  }
}

/**
 * The tree of timelines this run belongs to.
 *
 * A run with no forks answers with a one-node tree, which is what lets the Universe stage render
 * from the very first run rather than appearing the first time somebody forks.
 */
export async function fetchLineage(
  runId: string,
  signal?: AbortSignal,
): Promise<LineageWire> {
  let response: Response
  try {
    response = await fetch(
      `${GATEWAY_BASE}/runs/${encodeURIComponent(runId)}/lineage`,
      { signal },
    )
  } catch (cause) {
    throw new GatewayUnreachable(cause)
  }

  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as { detail?: string }
    throw new LineageUnavailable(
      response.status,
      body.detail ?? `the gateway answered ${response.status} to a lineage read`,
    )
  }

  return (await response.json()) as LineageWire
}

/** What a switch answers with: where the clock went, or why it did not go. */
export interface SwitchedTimeline {
  from_run_id: string
  to_run_id: string
  lineage_root_id: string
  paused_at_tick: number
  resumed_at_tick: number
  rate: number
  /** Set when the answer is no — another lineage, the run you are on, an ended target. */
  refusal: string
}

/**
 * Move the clock to another timeline of the same lineage.
 *
 * `runId` is the timeline being left and `to` is the one being entered, which is the direction the
 * route takes: the client knows what it is attached to and is asking to leave it, so a stale
 * `runId` is refused rather than quietly pausing whatever the server thought was current.
 *
 * A refusal comes back on the payload rather than as an error, like a rejected command: the request
 * was well-formed and the answer is no, which the surface renders. Only a transport failure and a
 * 404 throw.
 */
export async function switchTimeline(
  runId: string,
  to: string,
  rate?: number,
  signal?: AbortSignal,
): Promise<SwitchedTimeline> {
  const body: Record<string, unknown> = { to }
  if (rate !== undefined) body.rate = rate

  let response: Response
  try {
    response = await fetch(`${GATEWAY_BASE}/runs/${encodeURIComponent(runId)}/switch`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(body),
      signal,
    })
  } catch (cause) {
    throw new GatewayUnreachable(cause)
  }

  if (!response.ok) {
    const detail = (await response.json().catch(() => ({}))) as { detail?: string }
    throw new LineageUnavailable(
      response.status,
      detail.detail ?? `the gateway answered ${response.status} to a switch`,
    )
  }

  return (await response.json()) as SwitchedTimeline
}
