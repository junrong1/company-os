import { afterEach, describe, expect, it, vi } from 'vitest'

import { fetchGatewayStatus, GatewayUnreachable } from '../src/net/gateway'

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
