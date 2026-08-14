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
