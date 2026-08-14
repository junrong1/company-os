/**
 * Talking to the gateway.
 *
 * The gateway is the client's only contact surface, and the client addresses it
 * at `/api` in both topologies — nginx proxies that prefix under the demo
 * profile, the Vite dev server proxies it in development. Nothing here knows
 * which of the two it is running under, which is the point.
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
  options: { runId?: string; runSeed?: number } = {},
  signal?: AbortSignal,
): Promise<CreatedRun> {
  const body: Record<string, unknown> = {}
  if (options.runId !== undefined) body.run_id = options.runId
  if (options.runSeed !== undefined) body.run_seed = options.runSeed

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
