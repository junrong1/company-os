import { act, createElement } from 'react'
import { createRoot } from 'react-dom/client'

import { afterEach, describe, expect, it, vi } from 'vitest'

import { AuthorizationCard, OrgPanel, TrayPanel } from '../src/ui/Panels'
import { authorizationKey } from '../src/ui/panels-model'
import { actorsFromStore } from '../src/ui/stage'
import type { EventFrame } from '../src/net/store'
import { decisionPressure } from '../src/ui/hud-model'
import { catalogFixture, genesisFrame } from './helpers/frames'
import { useRunStore } from './helpers/store-helpers'

/**
 * Authorization on the client (U15: M40, M41, M42).
 *
 * The backend owns whether a director may read another line; what this side owns is whether the
 * CEO ever finds out they were asked. So the properties here are the ones between the wire and the
 * two surfaces:
 *
 * * **a request opens a card that names all three things** — who asks, whose knowledge, which item
 *   — because a permission prompt missing any of them is a dialog box rather than a decision;
 * * **it lights the beam on the asking director**, in the office and on the rail, because a player
 *   who does not scan the panels would otherwise watch an item stop for a reason nothing showed
 *   them. That is the failure M41 exists to prevent, and it is a client failure;
 * * **it does not touch decision pressure**, because the denominator is the authored decision
 *   supply and asking for permission is not progress through the work;
 * * **the answer is keyed once per intent**, so a double-click is answered with the first press's
 *   outcome instead of "no outstanding Authorization request";
 * * **it survives a resync**, unlike a comparison, because the question is still open and the item
 *   is still stopped.
 */

const ROOTS: Array<{ host: HTMLElement; root: ReturnType<typeof createRoot> }> = []

afterEach(() => {
  vi.unstubAllGlobals()
  for (const mounted of ROOTS.splice(0)) {
    act(() => mounted.root.unmount())
    mounted.host.remove()
  }
})

async function mount(element: ReturnType<typeof createElement>): Promise<HTMLElement> {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const root = createRoot(host)
  ROOTS.push({ host, root })
  await act(async () => {
    root.render(element)
  })
  return host
}

function aFreshRun(): void {
  act(() => {
    useRunStore.getState().reset()
    useRunStore.getState().apply(genesisFrame())
  })
}

/** The request the kernel raises when one line's work needs another line's knowledge. */
function askFrame(options: {
  seq?: number
  tick?: number
  item?: string
  asking?: string
  needs?: string
  asks?: number
  requestId?: string
  service?: string
  title?: string
}): EventFrame {
  return {
    kind: 'REQUEST_RAISED',
    seq: String(options.seq ?? 10),
    tick: String(options.tick ?? 60),
    schema_ver: 3,
    rules_ver: 'test',
    run_id: 'run-1',
    command_id: '',
    request_id: options.requestId ?? 'req-authz-1',
    payload: {
      tick: options.tick ?? 60,
      service: options.service ?? 'ceo',
      owning_item: options.item ?? 'wi_faq',
      deadline_tick: (options.tick ?? 60) + 2160,
      period_index: 0,
      person: options.asking ?? 'dir_admin',
      needs: options.needs ?? 'dir_cs',
      asks: options.asks ?? 1,
      title: options.title ?? 'Write the support FAQ',
    },
  }
}

function decidedFrame(options: {
  seq?: number
  item?: string
  granted?: boolean
}): EventFrame {
  return {
    kind: 'AUTHORIZATION_DECIDED',
    seq: String(options.seq ?? 11),
    tick: '61',
    schema_ver: 1,
    rules_ver: 'test',
    run_id: 'run-1',
    command_id: '',
    request_id: 'req-authz-1',
    payload: {
      tick: 61,
      request: 'req-authz-1',
      item: options.item ?? 'wi_faq',
      person: 'dir_admin',
      needs: 'dir_cs',
      granted: options.granted ?? true,
      asks: 1,
      raised_at_tick: 60,
      item_stalled: !(options.granted ?? true),
      item_status: 'active',
    },
  }
}

function abandonedFrame(seq = 12, item = 'wi_faq'): EventFrame {
  return {
    kind: 'ANSWER_REJECTED',
    seq: String(seq),
    tick: '2220',
    schema_ver: 1,
    rules_ver: 'test',
    run_id: 'run-1',
    command_id: '',
    request_id: 'req-authz-1',
    payload: {
      tick: 2220,
      reason: 'no answer within 2160 ticks of being raised',
      owning_item: item,
      service: 'ceo',
      abandoned: true,
      escalated_to_ceo: false,
      deferred_period: 0,
    },
  }
}

describe('the store', () => {
  it('opens a card carrying who asks, whose knowledge, and which item', () => {
    aFreshRun()
    act(() => useRunStore.getState().apply(askFrame({})))

    const entry = useRunStore.getState().authorizations.wi_faq
    expect(entry).toBeDefined()
    expect(entry.asking).toBe('dir_admin')
    expect(entry.needs).toBe('dir_cs')
    expect(entry.itemId).toBe('wi_faq')
    // The envelope's request id, not the payload's: it is what the answer is addressed to, and
    // rebuilding it on this side would be a derivation whose inputs the client cannot see.
    expect(entry.requestId).toBe('req-authz-1')
    expect(entry.asks).toBe(1)
  })

  it('ignores a request on any other leg', () => {
    aFreshRun()
    act(() => {
      useRunStore.getState().apply(askFrame({ service: 'bench', requestId: 'req-bench' }))
      useRunStore.getState().apply(askFrame({ service: 'domain', requestId: 'req-domain' }))
    })

    expect(useRunStore.getState().authorizations).toEqual({})
  })

  it('closes the card when the CEO answers, either way', () => {
    for (const granted of [true, false]) {
      aFreshRun()
      act(() => {
        useRunStore.getState().apply(askFrame({}))
        useRunStore.getState().apply(decidedFrame({ granted }))
      })

      expect(useRunStore.getState().authorizations).toEqual({})
    }
  })

  it('closes the card when the deadline answers for them', () => {
    aFreshRun()
    act(() => {
      useRunStore.getState().apply(askFrame({}))
      useRunStore.getState().apply(abandonedFrame())
    })

    expect(useRunStore.getState().authorizations).toEqual({})
  })

  it('keeps a second ask as a second card, with its count', () => {
    aFreshRun()
    act(() => {
      useRunStore.getState().apply(askFrame({}))
      useRunStore.getState().apply(decidedFrame({ granted: false }))
      useRunStore
        .getState()
        .apply(askFrame({ seq: 20, tick: 1200, asks: 2, requestId: 'req-authz-2' }))
    })

    const entry = useRunStore.getState().authorizations.wi_faq
    expect(entry.asks).toBe(2)
    expect(entry.requestId).toBe('req-authz-2')
  })

  it('rebuilds outstanding asks from a resync and drops answered ones', () => {
    aFreshRun()
    act(() => {
      useRunStore.getState().apply({
        kind: 'RESYNC',
        head_seq: '40',
        state: {
          authorization: {
            wi_faq: {
              asking: 'dir_admin',
              needs: 'dir_cs',
              request_id: 'req-after-resync',
              status: 'outstanding',
              raised_at_tick: 60,
              decided_at_tick: 0,
              asks: 1,
              abandoned: false,
            },
            wi_quotes: {
              asking: 'dir_admin',
              needs: 'dir_sales',
              request_id: '',
              status: 'granted',
              raised_at_tick: 10,
              decided_at_tick: 20,
              asks: 1,
              abandoned: false,
            },
          },
        },
      })
    })

    const held = useRunStore.getState().authorizations
    expect(Object.keys(held)).toEqual(['wi_faq'])
    expect(held.wi_faq.requestId).toBe('req-after-resync')
  })
})

describe('the beam', () => {
  it('lights on the asking director in the office', () => {
    aFreshRun()
    const before = actorsFromStore().find((actor) => actor.id === 'dir_admin')
    expect(before?.waiting).toBe(false)

    act(() => useRunStore.getState().apply(askFrame({})))

    const after = actorsFromStore().find((actor) => actor.id === 'dir_admin')
    expect(after?.waiting).toBe(true)
  })

  it('goes out when the question is answered', () => {
    aFreshRun()
    act(() => {
      useRunStore.getState().apply(askFrame({}))
      useRunStore.getState().apply(decidedFrame({ granted: true }))
    })

    const actor = actorsFromStore().find((actor) => actor.id === 'dir_admin')
    expect(actor?.waiting).toBe(false)
  })

  it('lights on the rail as well as on the floor', async () => {
    aFreshRun()
    act(() => useRunStore.getState().apply(askFrame({})))

    const host = await mount(createElement(OrgPanel, {}))
    const rows = Array.from(host.querySelectorAll('.person')) as HTMLElement[]
    const asking = rows.find((row) => row.textContent?.includes('dir_admin'))

    expect(asking?.dataset.waiting).toBe('1')
    expect(asking?.textContent).toContain('Needs you')
  })
})

describe('the tray', () => {
  it('counts an ask apart from the decision supply', async () => {
    aFreshRun()
    act(() => useRunStore.getState().apply(askFrame({})))

    const host = await mount(createElement(TrayPanel, {}))
    const panel = host.querySelector('[data-panel="tray"]') as HTMLElement

    // The two counts are the whole of this assertion. Decision pressure is measured against the
    // authored decision supply, so an Authorization arriving in `data-waiting` would make asking
    // for permission read as a decision the scenario never wrote.
    expect(panel.dataset.waiting).toBe('0')
    expect(panel.dataset.asks).toBe('1')

    // And the figure itself, read the way the HUD reads it: from the tray, which an
    // Authorization never enters.
    const state = useRunStore.getState()
    expect(state.tray).toHaveLength(0)
    expect(decisionPressure(state.tray, state.tick, 9).waiting).toBe(0)
  })

  it('renders the card with its consequence stated', async () => {
    aFreshRun()
    act(() => useRunStore.getState().apply(askFrame({})))

    const host = await mount(createElement(TrayPanel, {}))
    const card = host.querySelector('[data-kind="authorization"]') as HTMLElement

    expect(card).not.toBeNull()
    expect(card.textContent).toContain('asks to read')
    expect(card.textContent).toContain('stopped until you answer')
    expect(card.textContent).toContain('Saying nothing is a refusal too')
  })

  it('sends the verdict against the request the card names', async () => {
    const sent: Array<[string, Record<string, unknown>, string | undefined]> = []
    const entry = {
      itemId: 'wi_faq',
      requestId: 'req-authz-1',
      asking: 'dir_admin',
      needs: 'dir_cs',
      title: 'Write the support FAQ',
      asks: 1,
      atTick: 60n,
      deadlineTick: 2220n,
    }

    const host = await mount(
      createElement(AuthorizationCard, {
        entry,
        item: catalogFixture().find((item) => item.id === 'wi_faq'),
        nameOf: (id: string) => id,
        onDecide: (kind: string, payload: Record<string, unknown>, key?: string) => {
          sent.push([kind, payload, key])
        },
      }),
    )

    const buttons = Array.from(host.querySelectorAll('button')) as HTMLButtonElement[]
    act(() => buttons[0].click())
    act(() => buttons[1].click())

    expect(sent[0][0]).toBe('decide_authorization')
    expect(sent[0][1]).toEqual({ request: 'req-authz-1', granted: true })
    expect(sent[1][1]).toEqual({ request: 'req-authz-1', granted: false })
  })

  it('keys the answer once per intent rather than once per attempt', async () => {
    const sent: Array<string | undefined> = []
    const entry = {
      itemId: 'wi_faq',
      requestId: 'req-authz-1',
      asking: 'dir_admin',
      needs: 'dir_cs',
      title: 'Write the support FAQ',
      asks: 1,
      atTick: 60n,
      deadlineTick: 2220n,
    }

    const host = await mount(
      createElement(AuthorizationCard, {
        entry,
        item: undefined,
        nameOf: (id: string) => id,
        onDecide: (_kind: string, _payload: Record<string, unknown>, key?: string) => {
          sent.push(key)
        },
      }),
    )

    const grant = host.querySelector('button') as HTMLButtonElement
    act(() => grant.click())
    act(() => grant.click())

    // Two presses, one key: the second is answered with the first one's outcome rather than
    // reaching a kernel that has already closed the question and would refuse with a sentence
    // nobody who double-clicked would understand.
    expect(sent).toEqual([
      authorizationKey('req-authz-1', true),
      authorizationKey('req-authz-1', true),
    ])
    // And refusing is a different intent, so it is a different key.
    expect(authorizationKey('req-authz-1', false)).not.toBe(
      authorizationKey('req-authz-1', true),
    )
  })

  it('names work the catalog has never heard of', async () => {
    // The commonest case, and the one the suites all missed: a hiring item is created at runtime,
    // so a card that looked it up in the genesis catalog showed the CEO `wi_hire-hire_sales_1` and
    // asked them to decide about it. Found by opening the page.
    aFreshRun()
    act(() =>
      useRunStore
        .getState()
        .apply(askFrame({ item: 'wi_hire-hire_sales_1', title: 'Hire into sales' })),
    )

    const host = await mount(createElement(TrayPanel, {}))
    const card = host.querySelector('[data-kind="authorization"]') as HTMLElement

    expect(card.textContent).toContain('Hire into sales')
    expect(card.textContent).not.toContain('wi_hire-hire_sales_1')
  })

  it('says when the CEO is being asked a second time', async () => {
    aFreshRun()
    act(() => useRunStore.getState().apply(askFrame({ asks: 2 })))

    const host = await mount(createElement(TrayPanel, {}))
    const card = host.querySelector('[data-kind="authorization"]') as HTMLElement

    expect(card.textContent).toContain('Asked again')
  })
})
