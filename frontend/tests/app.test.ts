import { act, createElement } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

import App from '../src/App'
import { useRunStore } from './helpers/store-helpers'

/**
 * The surface before the office: choosing which company to start.
 *
 * What is asserted here is the choice reaching the request, and the two ways it can go wrong
 * without anything throwing — a picker that offers a company the server would refuse, and a
 * backend that cannot list its companies leaving a page with no way to begin. Both are silent
 * failures: the first only shows up as a 400 after the click, the second as a start button that
 * was never rendered.
 *
 * What the panel *looks* like is judged by eye, as everywhere else in this suite.
 */

const STATUS = {
  service: 'gateway',
  healthy: true,
  status: 'healthy' as const,
  git_sha: 'abc1234',
  versions: { rules: 'r1', event_schema: 3, store_ddl: '3' },
  dependencies: [],
  checked_at: '2026-08-17T00:00:00.000+00:00',
}

const OFFERED = [
  {
    id: 'ashcroft',
    title: 'Ashcroft Press',
    summary: 'An independent publisher of nine.',
    people: 9,
    items: 5,
    loadable: true,
  },
  {
    id: 'default',
    title: 'Northwind Components',
    summary: 'A mid-size components company.',
    people: 10,
    items: 8,
    loadable: true,
  },
]

function createdBody(scenario: string) {
  return {
    run_id: 'run-created',
    run_seed: 1,
    tick: 0,
    rate: 1,
    terminal_reason: '',
    head_seq: 1,
    active: true,
    created: true,
    scenario,
  }
}

/**
 * A socket that does nothing.
 *
 * Creating a run swaps the page for the office, which opens the event stream — and jsdom's
 * `WebSocket` rejects the client's relative `/ws/<run>` outright, so without this every
 * assertion about the *request* would fail on what happened after it. `stream.ts` keeps a
 * `makeSocket` seam for the same reason: jsdom has no WebSocket worth driving.
 */
class SilentSocket {
  readyState = 0
  onopen: unknown = null
  onclose: unknown = null
  onerror: unknown = null
  onmessage: unknown = null
  // Declared and assigned rather than a `readonly url` parameter property: parameter
  // properties are not erasable syntax, and `erasableSyntaxOnly` is on. vitest does not
  // typecheck, so this compiled fine under `npm test` and failed only under `tsc -b` —
  // the same gap `2c38686` closed once already, and the reason U13 runs both in CI.
  readonly url: string
  constructor(url: string) {
    this.url = url
  }
  send() {}
  close() {}
  addEventListener() {}
  removeEventListener() {}
}

/** A gateway that answers status, a scenario list, and a creation — recording what was posted. */
function stubGateway(options: { scenarios?: unknown; listFails?: boolean } = {}) {
  const posted: Array<Record<string, unknown>> = []
  vi.stubGlobal('WebSocket', SilentSocket)

  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url.endsWith('/scenarios')) {
      if (options.listFails) throw new TypeError('Failed to fetch')
      return new Response(JSON.stringify({ scenarios: options.scenarios ?? OFFERED }), {
        status: 200,
      })
    }
    if (url.endsWith('/status')) return new Response(JSON.stringify(STATUS), { status: 200 })
    if (url.endsWith('/runs')) {
      const body = JSON.parse(String(init?.body ?? '{}')) as Record<string, unknown>
      posted.push(body)
      return new Response(JSON.stringify(createdBody(String(body.scenario ?? 'default'))), {
        status: 200,
      })
    }
    throw new Error(`unexpected request to ${url}`)
  })

  vi.stubGlobal('fetch', fetchMock)
  return posted
}

/** Mount the start screen and let both opening fetches settle. */
async function mountApp() {
  // React only suppresses its "not configured to support act" warning when the flag is set, and
  // this suite drives state through `act` from end to end.
  ;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
  window.history.replaceState(null, '', '/')

  const host = document.createElement('div')
  document.body.appendChild(host)
  const root = createRoot(host)

  await act(async () => {
    root.render(createElement(App))
  })

  return {
    host,
    unmount: () => {
      act(() => root.unmount())
      host.remove()
    },
  }
}

afterEach(() => {
  vi.unstubAllGlobals()
  useRunStore.getState().reset()
  window.history.replaceState(null, '', '/')
})

describe('choosing a company before the run', () => {
  it('offers every company the backend lists', async () => {
    stubGateway()
    const { host, unmount } = await mountApp()

    const titles = [...host.querySelectorAll('.company__title')].map((node) => node.textContent)
    expect(titles).toEqual(['Ashcroft Press', 'Northwind Components'])

    // The shipped company is preselected, so the button does the same thing it did before the
    // choice existed for anyone who does not make one.
    const checked = host.querySelector('input[name="scenario"]:checked') as HTMLInputElement
    expect(checked.value).toBe('default')

    unmount()
  })

  it('starts a run of the company that was chosen', async () => {
    const posted = stubGateway()
    const { host, unmount } = await mountApp()

    const ashcroft = host.querySelector('input[value="ashcroft"]') as HTMLInputElement
    await act(async () => ashcroft.click())

    const start = host.querySelector('button') as HTMLButtonElement
    await act(async () => start.click())

    expect(posted).toEqual([{ scenario: 'ashcroft' }])

    unmount()
  })

  it('names the shipped company when it is the one left selected', async () => {
    // It sends what the picker shows, including when that is the preselected default: the user
    // is looking at "Northwind Components", and a request that named nothing would be relying on
    // the server's default happening to agree with what was on screen.
    const posted = stubGateway()
    const { host, unmount } = await mountApp()

    const start = host.querySelector('button') as HTMLButtonElement
    await act(async () => start.click())

    expect(posted).toEqual([{ scenario: 'default' }])

    unmount()
  })

  it('shows a scenario that will not load, disabled, with its reason', async () => {
    const posted = stubGateway({
      scenarios: [
        OFFERED[1],
        { id: 'broken', loadable: false, refusal: 'person[2]: room is "attic"' },
      ],
    })
    const { host, unmount } = await mountApp()

    const broken = host.querySelector('input[value="broken"]') as HTMLInputElement
    expect(broken.disabled).toBe(true)
    expect(host.querySelector('.company__refusal')?.textContent).toContain('attic')

    // And it cannot become the thing that gets started.
    await act(async () => broken.click())
    const start = host.querySelector('button') as HTMLButtonElement
    await act(async () => start.click())
    expect(posted).toEqual([{ scenario: 'default' }])

    unmount()
  })

  it('preselects a company that will load when the shipped one is not on offer', async () => {
    // A deployment whose scenarios directory does not contain `default.toml`. Without this the
    // page would open with a preselected name the server has already said it will refuse, and
    // the first click would be a 400.
    const posted = stubGateway({
      scenarios: [
        { id: 'broken', loadable: false, refusal: 'no department is declared' },
        OFFERED[0],
      ],
    })
    const { host, unmount } = await mountApp()

    const checked = host.querySelector('input[name="scenario"]:checked') as HTMLInputElement
    expect(checked.value).toBe('ashcroft')

    const start = host.querySelector('button') as HTMLButtonElement
    await act(async () => start.click())
    expect(posted).toEqual([{ scenario: 'ashcroft' }])

    unmount()
  })

  it('still starts a run when the backend cannot list its companies, naming none', async () => {
    // The choice is an offer, not a precondition. A failure to list it costs the choice and must
    // not cost the start button — the page has no other way to begin.
    //
    // And the request names *nothing*, rather than guessing "default": the client never learned
    // which companies exist, so asserting a name here would be asserting something it was never
    // told. The server picks its own, which is the one party that knows.
    const posted = stubGateway({ listFails: true })
    const { host, unmount } = await mountApp()

    expect(host.querySelector('.companies')).toBeNull()
    expect(host.querySelector('.card.bad')).toBeNull()

    const start = host.querySelector('button') as HTMLButtonElement
    await act(async () => start.click())

    expect(posted).toEqual([{}])

    unmount()
  })

  it('names nothing when every company on offer is refused', async () => {
    // Nothing is playable, so nothing is preselected, and the request falls back to the server's
    // own default rather than to a name from the refused list.
    const posted = stubGateway({
      scenarios: [{ id: 'broken', loadable: false, refusal: 'no department is declared' }],
    })
    const { host, unmount } = await mountApp()

    expect(host.querySelector('input[name="scenario"]:checked')).toBeNull()

    const start = host.querySelector('button') as HTMLButtonElement
    await act(async () => start.click())

    expect(posted).toEqual([{}])

    unmount()
  })
})
