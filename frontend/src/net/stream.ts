/**
 * The WebSocket the run arrives on, and the REST call commands leave by.
 *
 * The backend is the client's only contact surface, and the client addresses it at `/api` and
 * `/ws` in both setups — nginx proxies those prefixes under `docker compose up`, the Vite dev
 * server proxies them in development. Nothing here knows which it is running under.
 *
 * **Resume is keyed on run *and* sequence.** A fork shares sequence values with its parent,
 * so reconnecting with a bare sequence names two different events once a fork exists. The
 * socket carries the run in its path and the sequence in its query, and the gateway rejects
 * the combination if it cannot be satisfied — which is the case a client cannot detect for
 * itself, because from its side a parent's sequence looks perfectly valid.
 *
 * **A reconnect is expected, not exceptional.** The queue on the gateway side is bounded and
 * overflow closes the socket, on the reasoning that the client's own resume is the recovery
 * path and buffering to protect a slow client buys nothing. That makes "the socket closed"
 * an ordinary event this module has to handle well rather than an error it reports.
 *
 * **Commands carry an idempotency key.** A command whose response the client may never see
 * has to be retryable, and a retry without a key would apply twice. The key is minted per
 * logical command, not per attempt, so every attempt of the same command shares it.
 */

import { GATEWAY_BASE } from './gateway'
import { type Frame, useRunStore } from './store'

/** Where the event socket lives. Proxied to the backend in both setups. */
export const STREAM_BASE = '/ws'

/** Backoff for a dropped socket, in milliseconds. Capped, and the cap is deliberate. */
export const RECONNECT_DELAYS_MS = [250, 500, 1000, 2000, 4000] as const

export interface StreamOptions {
  runId: string
  /** Injectable so the suite can drive a fake socket without a server. */
  makeSocket?: (url: string) => StreamSocket
  /** Injectable for the same reason; the real one is `setTimeout`. */
  schedule?: (callback: () => void, delayMs: number) => number
  cancel?: (handle: number) => void
  /** Called for every frame after the store has applied it. */
  onFrame?: (frame: Frame) => void
}

/**
 * The part of `WebSocket` this module uses.
 *
 * Narrow on purpose: jsdom has no WebSocket worth driving, and a suite that has to stand up a
 * server to test reconnection tests the server instead of the reconnection.
 */
export interface StreamSocket {
  close(): void
  send(data: string): void
  onopen: (() => void) | null
  onclose: (() => void) | null
  onerror: ((error: unknown) => void) | null
  onmessage: ((event: { data: string }) => void) | null
}

/**
 * One connection to one run, with its own reconnect state.
 *
 * A class rather than a hook: the socket outlives any component, and tying its lifetime to a
 * render cycle is how you get two sockets under strict mode's double-invocation. The shell
 * owns one of these and calls `start`/`stop` symmetrically, the same shape the renderer uses.
 */
export class EventStream {
  private readonly runId: string
  private readonly makeSocket: (url: string) => StreamSocket
  private readonly schedule: (callback: () => void, delayMs: number) => number
  private readonly cancel: (handle: number) => void
  private readonly onFrame?: (frame: Frame) => void

  private socket: StreamSocket | null = null
  private retryHandle: number | null = null
  private attempt = 0
  /** `null` means stopped — the single source of truth, the way the renderer's handle is. */
  private running = false

  constructor(options: StreamOptions) {
    this.runId = options.runId
    this.makeSocket =
      options.makeSocket ?? ((url) => new WebSocket(url) as unknown as StreamSocket)
    this.schedule = options.schedule ?? ((callback, delay) => window.setTimeout(callback, delay))
    this.cancel = options.cancel ?? ((handle) => window.clearTimeout(handle))
    this.onFrame = options.onFrame
  }

  get connected(): boolean {
    return this.socket !== null
  }

  get retries(): number {
    return this.attempt
  }

  /** The URL for the next attempt, resuming after whatever the store has applied. */
  url(): string {
    const after = useRunStore.getState().appliedSeq
    return `${STREAM_BASE}/${encodeURIComponent(this.runId)}?after_seq=${after.toString()}`
  }

  /** Open the socket. Idempotent, so a double setup leaves one connection. */
  start(): void {
    if (this.running) return
    this.running = true
    this.open()
  }

  /**
   * Close and stay closed.
   *
   * Symmetric with `start` rather than guarded by a "did we already stop" flag: a guard makes
   * double-stop safe and leaves double-*start* silently leaking a socket, which is the
   * direction strict mode actually exercises.
   */
  stop(): void {
    this.running = false
    this.clearRetry()
    const socket = this.socket
    this.socket = null
    if (socket !== null) {
      // Detached before closing: the close handler would otherwise schedule a reconnect for
      // a stream that was deliberately shut down.
      socket.onopen = null
      socket.onclose = null
      socket.onerror = null
      socket.onmessage = null
      socket.close()
    }
    useRunStore.getState().setConnection('idle')
  }

  private open(): void {
    const store = useRunStore.getState()
    store.setConnection(this.attempt === 0 ? 'connecting' : 'resyncing')

    const socket = this.makeSocket(this.url())
    this.socket = socket

    socket.onopen = () => {
      if (this.socket !== socket) return
      this.attempt = 0
      useRunStore.getState().setConnection('live')
    }

    socket.onmessage = (event) => {
      // Parsing and *applying* are both inside the guard. A malformed payload can fail either
      // step — `JSON.parse` on bad text, or the projection on a well-formed frame whose fields
      // are the wrong shape — and an escaping throw would take down a working stream over one
      // bad frame, with no diagnostic and no reconnect.
      try {
        const frame = JSON.parse(event.data) as Frame
        useRunStore.getState().apply(frame)
        this.onFrame?.(frame)
      } catch (cause) {
        // Not a reason to tear down the stream, but a reason to say so: silently dropping it
        // would leave a gap with no explanation, and the sequence guard would then report it as
        // lost data instead of as a frame this client could not read.
        useRunStore.getState().setConnection('live', `unreadable frame: ${String(cause)}`)
      }
    }

    socket.onerror = () => {
      if (this.socket !== socket) return
      // Errors arrive before close and carry nothing useful about the cause in a browser, so
      // the close handler does the work. Recorded so the shell can say the last attempt
      // failed rather than showing "connecting" forever.
      useRunStore.getState().setConnection('lost', 'the event stream errored')
    }

    socket.onclose = () => {
      // Only if this is still *our* socket. A close arriving from a superseded connection — one
      // whose retry already fired — must not reset state or schedule a second retry, or two
      // ladders end up running against one stream.
      if (this.socket !== socket) return
      this.socket = null
      if (!this.running) return

      // The reason is carried over rather than cleared. `onerror` fires *before* `onclose` and is
      // the only place a browser says anything about why, so defaulting the message to null here
      // would erase the one diagnostic the user could have seen — leaving a bare "the event
      // stream dropped" for every cause.
      const store = useRunStore.getState()
      store.setConnection('lost', store.lastError)
      this.scheduleRetry()
    }
  }

  private scheduleRetry(): void {
    const delay =
      RECONNECT_DELAYS_MS[Math.min(this.attempt, RECONNECT_DELAYS_MS.length - 1)]
    this.attempt += 1
    this.retryHandle = this.schedule(() => {
      this.retryHandle = null
      if (this.running) this.open()
    }, delay)
  }

  private clearRetry(): void {
    if (this.retryHandle !== null) {
      this.cancel(this.retryHandle)
      this.retryHandle = null
    }
    this.attempt = 0
  }
}

// =========================================================================
// Commands
// =========================================================================

/** The gateway's answer to a command: the outcome of *applying*, not of accepting. */
export interface CommandOutcome {
  status: string
  applied_tick: number
  produced_seq: number[]
  reason: string
  command_id?: string
}

let keyCounter = 0

/**
 * A fresh idempotency key.
 *
 * Minted per logical command, then reused across every retry of it. A key per *attempt*
 * would defeat the whole mechanism — each retry would look like a new command and apply
 * again, which is exactly the failure the key exists to prevent.
 */
export function newIdempotencyKey(prefix = 'cmd'): string {
  keyCounter += 1
  const random = Math.random().toString(36).slice(2, 10)
  return `${prefix}-${keyCounter}-${random}`
}

/**
 * Submit a command and report what applying it did.
 *
 * A rejection is a successful request with the answer "no", and the reason is a sentence the
 * client shows. Only a transport failure or a missing run is an exception here — the same
 * judgement `fetchGatewayStatus` makes about a 503.
 */
export async function submitCommand(
  runId: string,
  kind: string,
  payload: Record<string, unknown>,
  idempotencyKey: string,
): Promise<CommandOutcome> {
  const response = await fetch(`${GATEWAY_BASE}/runs/${encodeURIComponent(runId)}/commands`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ kind, payload, idempotency_key: idempotencyKey }),
  })

  if (response.status === 404) {
    const body = (await response.json().catch(() => ({}))) as { detail?: string }
    throw new Error(body.detail ?? `no run ${runId}`)
  }

  if (!response.ok) {
    throw new Error(`the gateway answered ${response.status}`)
  }

  return (await response.json()) as CommandOutcome
}

/**
 * Ask what a previously-submitted key did (R30).
 *
 * The call a reconnecting client makes instead of retrying blind. `null` means the command
 * was never applied, so submitting it again is safe — which is a different answer from "it
 * failed", and the difference is the one the client needs.
 */
export async function commandOutcome(
  runId: string,
  idempotencyKey: string,
): Promise<CommandOutcome | null> {
  const response = await fetch(
    `${GATEWAY_BASE}/runs/${encodeURIComponent(runId)}/commands/${encodeURIComponent(idempotencyKey)}`,
  )
  if (response.status === 404) return null
  if (!response.ok) throw new Error(`the gateway answered ${response.status}`)
  return (await response.json()) as CommandOutcome
}
