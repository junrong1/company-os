import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  createRun,
  fetchGatewayStatus,
  fetchScenarios,
  GatewayUnreachable,
} from '../src/net/gateway'
import { rememberRunInLocation, runIdFromLocation } from '../src/net/runid'

/**
 * The module under test makes exactly one judgement call, so that is what is
 * tested: a 503 from the gateway is a successful read of an unhealthy service,
 * because the body carries the reason the service is unhealthy. Treating it as a
 * failed request would throw away the only useful part of the response — and
 * "the kernel is down" is precisely what the gateway exists to be able to say.
 */

const unhealthyBody = {
  service: 'gateway',
  healthy: false,
  status: 'unhealthy' as const,
  git_sha: 'unknown',
  versions: { rules: null, event_schema: null, store_ddl: null },
  dependencies: [
    { name: 'kernel', required: true, reachable: false, detail: 'unreachable' },
  ],
  checked_at: '2026-08-13T00:00:00.000+00:00',
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('fetchGatewayStatus', () => {
  it('returns the report when the gateway is healthy', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(JSON.stringify({ ...unhealthyBody, healthy: true, status: 'healthy' }), { status: 200 })),
    )

    const status = await fetchGatewayStatus()

    expect(status.healthy).toBe(true)
    expect(status.service).toBe('gateway')
  })

  it('parses a 503 body instead of throwing, so the reason survives', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(JSON.stringify(unhealthyBody), { status: 503 })),
    )

    const status = await fetchGatewayStatus()

    expect(status.healthy).toBe(false)
    expect(status.dependencies[0]?.name).toBe('kernel')
    expect(status.dependencies[0]?.reachable).toBe(false)
  })

  it('throws GatewayUnreachable when the transport fails', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new TypeError('Failed to fetch')
      }),
    )

    await expect(fetchGatewayStatus()).rejects.toBeInstanceOf(GatewayUnreachable)
  })

  it('throws on a status that is neither 200 nor 503', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('nope', { status: 500 })))

    await expect(fetchGatewayStatus()).rejects.toBeInstanceOf(GatewayUnreachable)
  })
})

// =========================================================================
// R25, R26: starting a run from the page
// =========================================================================

const createdBody = {
  run_id: 'run-abc123',
  run_seed: 12345,
  tick: 0,
  rate: 1,
  terminal_reason: '',
  head_seq: 1,
  active: true,
  created: true,
}

describe('createRun', () => {
  it('posts once and returns the run to attach to (R25, AE11)', async () => {
    // Typed parameters so the recorded calls carry types, rather than being cast back.
    const fetchMock = vi.fn(
      async (_url: string, _init: RequestInit) =>
        new Response(JSON.stringify(createdBody), { status: 200 }),
    )
    vi.stubGlobal('fetch', fetchMock)

    const run = await createRun()

    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/runs')
    expect(init.method).toBe('POST')
    expect(run.run_id).toBe('run-abc123')
    expect(run.created).toBe(true)
  })

  it('sends no id or seed when it is not asked to, so the gateway mints both', async () => {
    // Typed parameters so the recorded calls carry types, rather than being cast back.
    const fetchMock = vi.fn(
      async (_url: string, _init: RequestInit) =>
        new Response(JSON.stringify(createdBody), { status: 200 }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await createRun()

    const [, init] = fetchMock.mock.calls[0]
    expect(JSON.parse(String(init.body))).toEqual({})
  })

  it('passes an id and a seed through when given them', async () => {
    // Typed parameters so the recorded calls carry types, rather than being cast back.
    const fetchMock = vi.fn(
      async (_url: string, _init: RequestInit) =>
        new Response(JSON.stringify(createdBody), { status: 200 }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await createRun({ runId: 'run-fixed', runSeed: 7 })

    const [, init] = fetchMock.mock.calls[0]
    expect(JSON.parse(String(init.body))).toEqual({ run_id: 'run-fixed', run_seed: 7 })
  })

  it('names a company when one was chosen', async () => {
    // Typed parameters so the recorded calls carry types, rather than being cast back.
    const fetchMock = vi.fn(
      async (_url: string, _init: RequestInit) =>
        new Response(JSON.stringify(createdBody), { status: 200 }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await createRun({ scenario: 'ashcroft' })

    const [, init] = fetchMock.mock.calls[0]
    expect(JSON.parse(String(init.body))).toEqual({ scenario: 'ashcroft' })
  })

  it('sends no company at all when the choice is empty, so the server picks the shipped one', async () => {
    // The default is the *absence* of the field rather than the string "default". A client
    // that spelled the default out would be a second place the shipped company is named, and
    // the two would disagree the first time the backend's default moved.
    const fetchMock = vi.fn(
      async (_url: string, _init: RequestInit) =>
        new Response(JSON.stringify(createdBody), { status: 200 }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await createRun({ scenario: '' })

    const [, init] = fetchMock.mock.calls[0]
    expect(JSON.parse(String(init.body))).toEqual({})
  })

  it('attaches to a run that already exists rather than erroring', async () => {
    // The run id is its own idempotency key, so a retry whose first response was never seen
    // must not end up with two runs and must not look like a failure.
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () =>
          new Response(JSON.stringify({ ...createdBody, created: false }), { status: 200 }),
      ),
    )

    const run = await createRun({ runId: 'run-abc123' })

    expect(run.created).toBe(false)
    expect(run.run_id).toBe('run-abc123')
  })

  it('reports why a creation failed rather than failing silently', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () =>
          new Response(JSON.stringify({ detail: 'could not create the run: bad seed' }), {
            status: 400,
          }),
      ),
    )

    await expect(createRun()).rejects.toThrow('bad seed')
  })

  it('reports an unreachable gateway as such', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new TypeError('Failed to fetch')
      }),
    )

    await expect(createRun()).rejects.toBeInstanceOf(GatewayUnreachable)
  })

  it('survives a body it cannot read on a failure', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response('<html>502</html>', { status: 502 })),
    )

    await expect(createRun()).rejects.toThrow('502')
  })
})

describe('fetchScenarios', () => {
  it('lists the companies a run can be created against', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () =>
          new Response(
            JSON.stringify({
              scenarios: [
                { id: 'ashcroft', title: 'Ashcroft Press', loadable: true, people: 9, items: 5 },
                { id: 'default', title: 'Northwind Components', loadable: true, people: 10 },
              ],
            }),
            { status: 200 },
          ),
      ),
    )

    const offered = await fetchScenarios()

    expect(offered.map((entry) => entry.id)).toEqual(['ashcroft', 'default'])
    expect(offered[0].title).toBe('Ashcroft Press')
  })

  it('keeps a scenario that will not load, with its reason', async () => {
    // The server does not filter these out and neither does this. An author whose file is
    // refused is exactly the person reading the list, and a dropped entry reads as a file that
    // never saved rather than as one with a mistake in it.
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () =>
          new Response(
            JSON.stringify({
              scenarios: [{ id: 'broken', loadable: false, refusal: 'no department is declared' }],
            }),
            { status: 200 },
          ),
      ),
    )

    const [entry] = await fetchScenarios()

    expect(entry.loadable).toBe(false)
    expect(entry.refusal).toContain('department')
  })

  it('reports an unreachable gateway rather than an empty list', async () => {
    // The caller decides that an unlistable backend still gets a start button. It cannot decide
    // that if "none offered" and "could not ask" arrive as the same value.
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new TypeError('Failed to fetch')
      }),
    )

    await expect(fetchScenarios()).rejects.toBeInstanceOf(GatewayUnreachable)
  })

  it('tolerates a body with no scenarios array', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(JSON.stringify({}), { status: 200 })),
    )

    await expect(fetchScenarios()).resolves.toEqual([])
  })
})

describe('remembering the run in the URL', () => {
  it('puts the created id in the address bar so a reload re-attaches (R25)', () => {
    window.history.replaceState(null, '', '/')
    expect(runIdFromLocation(window.location.search)).toBeNull()

    rememberRunInLocation('run-abc123')

    expect(runIdFromLocation(window.location.search)).toBe('run-abc123')
  })

  it('replaces rather than pushes, so the back button does not undo starting a run', () => {
    window.history.replaceState(null, '', '/')
    const before = window.history.length

    rememberRunInLocation('run-abc123')

    expect(window.history.length).toBe(before)
  })

  it('leaves the URL alone when it already names that run', () => {
    window.history.replaceState(null, '', '/?run=run-abc123')
    const before = window.location.href

    rememberRunInLocation('run-abc123')

    expect(window.location.href).toBe(before)
  })

  it('replaces the id when a second run starts', () => {
    window.history.replaceState(null, '', '/?run=run-first')

    rememberRunInLocation('run-second')

    expect(runIdFromLocation(window.location.search)).toBe('run-second')
  })
})
