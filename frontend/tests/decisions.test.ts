import { act, createElement, useState } from 'react'
import { createRoot } from 'react-dom/client'

import { Shell } from '../src/ui/Shell'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { forkIdempotencyKey, forkTimeline } from '../src/net/gateway'
import type { ForkedTimeline, SwitchedTimeline } from '../src/net/gateway'
import { decisionKey, snapshotDecisionKey } from '../src/net/store'
import { DecisionsPanel } from '../src/ui/Decisions'
import { NO_SEQUENCE, pastDecisions } from '../src/ui/panels-model'
import { catalogFixture, genesisFrame, resolvedFrame } from './helpers/frames'
import { useRunStore } from './helpers/store-helpers'

/**
 * Forking from the client (U25: M44).
 *
 * The unit's whole claim is that a past decision is *reachable*: everything else about forking
 * shipped with U16 and U17 and none of it could be got at without leaving the game. So the
 * properties worth asserting are the ones between the store and the wire:
 *
 * * **a resolution is kept, with its sequence**, because a fork is addressed by the sequence of
 *   the decision it reconsiders and the store used to throw it away;
 * * **the key is minted once per intent**, because the child's id is minted from it — so a
 *   double-click on a random key is two identical timelines and the cap is what the player meets;
 * * **the list survives a resync**, filled in from the snapshot and honest about the half of it
 *   that can no longer be forked, rather than dropped the way a comparison is;
 * * **a successful fork lands somewhere**, on the Universe with the new node open, because a fork
 *   that leaves the player looking at the same office is the beat with its outcome removed.
 */

/**
 * Every root mounted, so every one can be *unmounted*.
 *
 * Removing the host element is not enough and the difference is load-bearing here: the shell
 * binds `keydown`, `keyup` and `blur` on `window`, and a root that is never unmounted never runs
 * the cleanup that removes them. A keyboard event in a later test then reaches every shell this
 * file ever mounted, and each one submits a command for the run *it* was attached to — which is
 * indistinguishable from the defect one of these tests exists to catch. Found exactly that way.
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

/**
 * The shell, with the run id moving the way the app moves it.
 *
 * `Shell` does not own its run id — entering a timeline replaces the run the shell is attached
 * to, so the app holds it and hands it back down. A test that only passes `onEnterTimeline` and
 * never re-renders is testing a shell that never changed run, which is precisely the boundary
 * some of these assertions are about.
 */
function ShellHarness({ from }: { from: string }) {
  const [runId, setRunId] = useState(from)
  return createElement(Shell, {
    runId,
    makeStream: () => ({ start: () => {}, stop: () => {} }),
    storage: null,
    onEnterTimeline: setRunId,
  })
}

/** A run with genesis landed and nothing decided. */
function aFreshRun(): void {
  act(() => {
    useRunStore.getState().reset()
    useRunStore.getState().apply(genesisFrame())
  })
}

function forked(overrides: Partial<ForkedTimeline> = {}): ForkedTimeline {
  return {
    child_run_id: 'run-1-child',
    parent_run_id: 'run-1',
    decision_seq: 12,
    forked_at_seq: 11,
    forked_at_tick: 600,
    lineage_root_id: 'run-1',
    item: 'wi_ap_map',
    cp_index: 0,
    option_index: 1,
    parent_option_index: 0,
    created: true,
    refusal: '',
    ...overrides,
  }
}

/** The tree the child's landing reads, with the parent paused and the child at its decision. */
function aLineage() {
  return {
    root_run_id: 'run-1',
    asked_about: 'run-1-child',
    cap: 16,
    nodes: [
      {
        run_id: 'run-1',
        parent_run_id: '',
        forked_at_seq: 0,
        tick: 900,
        day: 2,
        rate: 0,
        head_seq: 40,
        terminal_reason: '',
        created_at: '2026-08-27T00:00:00.000+00:00',
        item: '',
        cp_index: -1,
        option_index: -1,
        parent_option_index: -1,
        choice: '',
        parent_choice: '',
      },
      {
        run_id: 'run-1-child',
        parent_run_id: 'run-1',
        forked_at_seq: 11,
        tick: 600,
        day: 2,
        rate: 0,
        head_seq: 11,
        terminal_reason: '',
        created_at: '2026-08-27T00:01:00.000+00:00',
        item: 'wi_ap_map',
        cp_index: 0,
        option_index: 2,
        parent_option_index: 1,
        choice: 'Let the team decide',
        parent_choice: 'Paper is the record',
      },
    ],
  }
}

function switched(overrides: Partial<SwitchedTimeline> = {}): SwitchedTimeline {
  return {
    from_run_id: 'run-1',
    to_run_id: 'run-1-child',
    lineage_root_id: 'run-1',
    paused_at_tick: 900,
    resumed_at_tick: 600,
    rate: 0,
    refusal: '',
    ...overrides,
  }
}

describe('what the store keeps of a decision', () => {
  it('keeps the option, the tick and the sequence a fork is addressed by', () => {
    aFreshRun()
    act(() => {
      useRunStore
        .getState()
        .apply(
          resolvedFrame({ seq: 12, item: 'wi_ap_map', optionIndex: 0, tick: 640 }),
        )
    })

    const record = useRunStore.getState().decisions[decisionKey('wi_ap_map', 0)]
    expect(record).toMatchObject({
      itemId: 'wi_ap_map',
      cpIndex: 0,
      optionIndex: 0,
      choice: 'PDF is the record',
      inPerson: true,
    })
    // The three fields the panel could not have worked without, and the ones the old projection
    // dropped: it advanced `resolvedCount` and kept none of this.
    expect(record.tick).toBe(640n)
    expect(record.atSeq).toBe(12n)
  })

  it('settles a replayed resolution onto the record it already wrote', () => {
    // A resume asks for "after N" and the window is inclusive at the edges, so an event the
    // client already applied can arrive again. A list would grow a second card for it.
    aFreshRun()
    act(() => {
      useRunStore.getState().apply(resolvedFrame({ seq: 12, item: 'wi_ap_map' }))
      useRunStore.getState().apply(resolvedFrame({ seq: 12, item: 'wi_ap_map' }))
    })

    expect(Object.keys(useRunStore.getState().decisions)).toHaveLength(1)
  })

  it('keeps two checkpoints of one item apart', () => {
    aFreshRun()
    act(() => {
      useRunStore.getState().apply(resolvedFrame({ seq: 12, item: 'wi_ap_map', cpIndex: 0 }))
      useRunStore.getState().apply(resolvedFrame({ seq: 20, item: 'wi_ap_map', cpIndex: 1 }))
    })

    expect(Object.keys(useRunStore.getState().decisions).sort()).toEqual([
      'wi_ap_map:0',
      'wi_ap_map:1',
    ])
  })

  it('is cleared with the run, so a timeline does not inherit its parent’s history', () => {
    aFreshRun()
    act(() => {
      useRunStore.getState().apply(resolvedFrame({ seq: 12, item: 'wi_ap_map' }))
      useRunStore.getState().reset()
    })

    expect(useRunStore.getState().decisions).toEqual({})
  })
})

describe('the list across a resync', () => {
  it('fills in what the client never saw, and does not list what it did twice', () => {
    aFreshRun()
    act(() => {
      // One resolution this client watched land, with its sequence.
      useRunStore.getState().apply(resolvedFrame({ seq: 30, item: 'wi_ap_map', cpIndex: 1 }))
      // And a snapshot that knows about two — the one above, and one from before it attached.
      useRunStore.getState().apply({
        kind: 'RESYNC',
        head_seq: 60,
        state: {
          items: {
            wi_ap_map: {
              status: 'active',
              assignee: 'stf_ap',
              done_units: 40,
              resolved: [true, true],
              decisions: [
                { label: 'Information', choice: 'Paper is the record', at_tick: 300 },
                { label: 'Information', choice: 'PDF is the record', at_tick: 600 },
              ],
            },
          },
        },
      })
    })

    const held = useRunStore.getState().decisions
    // Two cards for two resolutions, not three and not one.
    expect(Object.keys(held).sort()).toEqual(
      [snapshotDecisionKey('wi_ap_map', 0), decisionKey('wi_ap_map', 1)].sort(),
    )
    // The one it watched keeps its sequence and is still forkable; the one it did not carries
    // zero, which is a state rather than a missing value.
    expect(held[decisionKey('wi_ap_map', 1)].atSeq).toBe(30n)
    expect(held[snapshotDecisionKey('wi_ap_map', 0)]).toMatchObject({
      choice: 'Paper is the record',
      cpIndex: -1,
      atSeq: 0n,
    })
  })

  it('does not duplicate across a second resync', () => {
    aFreshRun()
    const snapshot = {
      kind: 'RESYNC' as const,
      head_seq: 60,
      state: {
        items: {
          wi_ap_map: {
            status: 'active',
            resolved: [true],
            decisions: [{ label: 'Information', choice: 'Paper is the record', at_tick: 300 }],
          },
        },
      },
    }
    act(() => {
      useRunStore.getState().apply(snapshot)
      useRunStore.getState().apply(snapshot)
    })

    expect(Object.keys(useRunStore.getState().decisions)).toHaveLength(1)
  })

  it('says why a snapshot-derived decision cannot be forked, without promising a remedy', () => {
    const cards = pastDecisions(
      {
        [snapshotDecisionKey('wi_ap_map', 0)]: {
          itemId: 'wi_ap_map',
          cpIndex: -1,
          optionIndex: -1,
          choice: 'Paper is the record',
          label: 'Information',
          inPerson: false,
          tick: 300n,
          atSeq: 0n,
        },
      },
      catalogFixture(),
    )

    expect(cards[0].unforkable).toBe(NO_SEQUENCE)
    // The remedy a reader would reach for is reloading to replay the run, which is the very
    // thing that resynced. Naming it would repeat U16's "remedy that cannot work" finding.
    expect(NO_SEQUENCE.toLowerCase()).not.toContain('reload')
  })
})

describe('the model that joins a decision to its checkpoint', () => {
  it('marks the option taken and offers every other one', () => {
    const cards = pastDecisions(
      {
        [decisionKey('wi_ap_map', 0)]: {
          itemId: 'wi_ap_map',
          cpIndex: 0,
          optionIndex: 1,
          choice: 'Paper is the record',
          label: '',
          inPerson: true,
          tick: 600n,
          atSeq: 12n,
        },
      },
      catalogFixture(),
    )

    expect(cards[0].title).toBe('Map how accounts payable actually works')
    // The checkpoint's own label, read off the catalog rather than carried on the event.
    expect(cards[0].label).toBe('Information')
    expect(cards[0].taken).toBe('Paper is the record')
    expect(cards[0].takenIndex).toBe(1)
    expect(cards[0].alternatives.map((one) => one.index)).toEqual([0, 2])
    expect(cards[0].unforkable).toBe('')
  })

  it('lists newest first, and holds its order when two land on one tick', () => {
    const base = { itemId: 'wi_ap_map', cpIndex: 0, optionIndex: 0, label: '', inPerson: true }
    const cards = pastDecisions(
      {
        'wi_faq:0': { ...base, itemId: 'wi_faq', choice: 'a', tick: 900n, atSeq: 3n },
        'wi_ap_map:0': { ...base, choice: 'b', tick: 300n, atSeq: 1n },
        'wi_quotes:0': { ...base, itemId: 'wi_quotes', choice: 'c', tick: 900n, atSeq: 2n },
      },
      catalogFixture(),
    )

    expect(cards.map((card) => card.key)).toEqual(['wi_faq:0', 'wi_quotes:0', 'wi_ap_map:0'])
  })

  it('falls back to the option label when the index does not address the catalog', () => {
    // A run created before an option was added keeps its old-shaped catalog forever, and the
    // fold never re-derives it. The label is the handle that still works.
    const cards = pastDecisions(
      {
        [decisionKey('wi_ap_map', 0)]: {
          itemId: 'wi_ap_map',
          cpIndex: 0,
          optionIndex: 99,
          choice: 'Let the team decide',
          label: '',
          inPerson: false,
          tick: 600n,
          atSeq: 12n,
        },
      },
      catalogFixture(),
    )

    expect(cards[0].takenIndex).toBe(2)
    expect(cards[0].alternatives.map((one) => one.index)).toEqual([0, 1])
  })
})

describe('the panel', () => {
  it('says the list is empty rather than rendering nothing', async () => {
    aFreshRun()
    const host = await mount(createElement(DecisionsPanel, { runId: 'run-1' }))

    expect(host.querySelector('[data-panel="decisions"]')).not.toBeNull()
    expect(host.querySelector('.hint')?.textContent).toContain('Nothing has been settled')
    expect(host.querySelectorAll('.decided')).toHaveLength(0)
  })

  it('shows the option taken, the route it was taken by, and a fork per alternative', async () => {
    aFreshRun()
    act(() => {
      useRunStore
        .getState()
        .apply(resolvedFrame({ seq: 12, item: 'wi_ap_map', optionIndex: 1, choice: 'Paper is the record' }))
    })

    const host = await mount(createElement(DecisionsPanel, { runId: 'run-1' }))

    expect(host.querySelector('.decided__taken')?.textContent).toContain('Paper is the record')
    // Which route settled it, because it is the difference the whole product is about.
    expect(host.querySelector('.decided__route')?.textContent).toContain('in person')

    const alternatives = [...host.querySelectorAll('.alternative__label')].map(
      (node) => node.textContent,
    )
    expect(alternatives).toEqual(['PDF is the record', 'Let the team decide'])
    expect(host.querySelectorAll('.alternative__fork')).toHaveLength(2)
  })

  it('carries the marking on every figure it shows, like the tray and the conversation', async () => {
    aFreshRun()
    act(() => {
      useRunStore.getState().apply(resolvedFrame({ seq: 12, item: 'wi_ap_map', optionIndex: 1 }))
    })

    const host = await mount(createElement(DecisionsPanel, { runId: 'run-1' }))

    const figures = host.querySelectorAll('.consequence__figure')
    expect(figures.length).toBeGreaterThan(0)
    for (const figure of figures) {
      expect(figure.querySelector('[data-authored-tuning]')).not.toBeNull()
    }
  })

  it('offers no fork it cannot address, and says why', async () => {
    aFreshRun()
    act(() => {
      useRunStore.getState().apply({
        kind: 'RESYNC',
        head_seq: 60,
        state: {
          items: {
            wi_ap_map: {
              status: 'active',
              resolved: [true],
              decisions: [
                { label: 'Information', choice: 'Paper is the record', at_tick: 300 },
              ],
            },
          },
        },
      })
    })

    const host = await mount(createElement(DecisionsPanel, { runId: 'run-1' }))

    // Listed, which is the point — a resync must not make the run look like it has no history.
    expect(host.querySelector('.decided__taken')?.textContent).toContain('Paper is the record')
    expect(host.querySelector('.decided__unforkable')?.textContent).toContain('snapshot')
    // And a snapshot-derived record has no checkpoint index, so there is no alternative to offer.
    expect(host.querySelectorAll('.alternative__fork')).toHaveLength(0)
  })

  it('is offered from a terminated timeline, which is the point of keeping one', async () => {
    aFreshRun()
    act(() => {
      useRunStore.getState().apply(resolvedFrame({ seq: 12, item: 'wi_ap_map', optionIndex: 1 }))
      useRunStore.getState().apply({
        kind: 'RUN_TERMINATED',
        seq: '40',
        tick: '10800',
        schema_ver: 2,
        rules_ver: 'test',
        run_id: 'run-1',
        command_id: '',
        request_id: '',
        payload: { tick: 10800, reason: 'horizon' },
      })
    })

    const host = await mount(createElement(DecisionsPanel, { runId: 'run-1' }))

    expect(useRunStore.getState().terminal?.reason).toBe('horizon')
    const buttons = [...host.querySelectorAll('.alternative__fork')] as HTMLButtonElement[]
    expect(buttons).toHaveLength(2)
    expect(buttons.every((button) => button.disabled)).toBe(false)
  })
})

describe('forking', () => {
  it('names the decision’s own sequence and the option to take instead', async () => {
    aFreshRun()
    act(() => {
      useRunStore
        .getState()
        .apply(resolvedFrame({ seq: 12, item: 'wi_ap_map', optionIndex: 1 }))
    })

    const asked: Array<[string, bigint, number]> = []
    const host = await mount(
      createElement(DecisionsPanel, {
        runId: 'run-1',
        forker: async (runId: string, atSeq: bigint, optionIndex: number) => {
          asked.push([runId, atSeq, optionIndex])
          return forked({ option_index: optionIndex })
        },
        switcher: async () => switched(),
      }),
    )

    await act(async () => {
      ;(host.querySelectorAll('.alternative__fork')[1] as HTMLButtonElement).click()
    })

    // The second alternative of a three-option checkpoint whose option 1 was taken is index 2 —
    // the index the *catalog* holds it at, never its position in the list of what is left.
    expect(asked).toEqual([['run-1', 12n, 2]])
  })

  it('switches into the child at rate zero and hands it up only then', async () => {
    aFreshRun()
    act(() => {
      useRunStore.getState().apply(resolvedFrame({ seq: 12, item: 'wi_ap_map', optionIndex: 1 }))
    })

    const switches: Array<[string, string, number | undefined]> = []
    const landed: string[] = []
    const host = await mount(
      createElement(DecisionsPanel, {
        runId: 'run-1',
        onEntered: (child: string) => landed.push(child),
        forker: async () => forked(),
        switcher: async (runId: string, to: string, rate?: number) => {
          switches.push([runId, to, rate])
          // Nothing has been handed upwards yet: the clock has not moved.
          expect(landed).toEqual([])
          return switched()
        },
      }),
    )

    await act(async () => {
      ;(host.querySelector('.alternative__fork') as HTMLButtonElement).click()
    })

    expect(switches).toEqual([['run-1', 'run-1-child', 0]])
    expect(landed).toEqual(['run-1-child'])
  })

  it('shows a refusal rather than failing silently, and stays where it is', async () => {
    aFreshRun()
    act(() => {
      useRunStore.getState().apply(resolvedFrame({ seq: 12, item: 'wi_ap_map', optionIndex: 1 }))
    })

    const landed: string[] = []
    const host = await mount(
      createElement(DecisionsPanel, {
        runId: 'run-1',
        onEntered: (child: string) => landed.push(child),
        forker: async () =>
          forked({
            child_run_id: '',
            created: false,
            refusal: 'this lineage already holds 16 timelines, which is the cap.',
          }),
        switcher: async () => switched(),
      }),
    )

    await act(async () => {
      ;(host.querySelector('.alternative__fork') as HTMLButtonElement).click()
    })

    expect(host.querySelector('.decided__refusal')?.textContent).toContain('16 timelines')
    expect(landed).toEqual([])
    // And the button comes back, rather than staying stuck on "Forking…".
    expect((host.querySelector('.alternative__fork') as HTMLButtonElement).disabled).toBe(false)
  })

  it('says the timeline exists when only the switch was refused', async () => {
    // Two different repairs: the fork failed and there is nothing, or the fork worked and the
    // clock did not move. Collapsing them would send the player looking for a timeline they
    // have, or hunting for one they do not.
    aFreshRun()
    act(() => {
      useRunStore.getState().apply(resolvedFrame({ seq: 12, item: 'wi_ap_map', optionIndex: 1 }))
    })

    const landed: string[] = []
    const host = await mount(
      createElement(DecisionsPanel, {
        runId: 'run-1',
        onEntered: (child: string) => landed.push(child),
        forker: async () => forked(),
        switcher: async () => switched({ refusal: 'run-1 is not the timeline you are in.' }),
      }),
    )

    await act(async () => {
      ;(host.querySelector('.alternative__fork') as HTMLButtonElement).click()
    })

    expect(host.querySelector('.decided__refusal')?.textContent).toContain('Universe tree')
    expect(landed).toEqual([])
  })

  it('holds every other fork while one is in flight', async () => {
    aFreshRun()
    act(() => {
      useRunStore.getState().apply(resolvedFrame({ seq: 12, item: 'wi_ap_map', optionIndex: 1 }))
    })

    let release: (() => void) | null = null
    const host = await mount(
      createElement(DecisionsPanel, {
        runId: 'run-1',
        forker: async () =>
          new Promise<ForkedTimeline>((resolve) => {
            release = () => resolve(forked())
          }),
        switcher: async () => switched(),
      }),
    )

    await act(async () => {
      ;(host.querySelector('.alternative__fork') as HTMLButtonElement).click()
    })

    const buttons = [...host.querySelectorAll('.alternative__fork')] as HTMLButtonElement[]
    expect(buttons[0].textContent).toBe('Forking…')
    // A fork writes a run. Two in flight would switch the player into whichever answered last.
    expect(buttons.every((button) => button.disabled)).toBe(true)

    await act(async () => {
      release?.()
    })
  })

  it('offers nothing to press when it cannot say which run it would fork', async () => {
    aFreshRun()
    act(() => {
      useRunStore.getState().apply(resolvedFrame({ seq: 12, item: 'wi_ap_map', optionIndex: 1 }))
    })

    const host = await mount(createElement(DecisionsPanel, {}))
    const buttons = [...host.querySelectorAll('.alternative__fork')] as HTMLButtonElement[]
    expect(buttons.every((button) => button.disabled)).toBe(true)
  })
})

describe('the call this surface makes', () => {
  it('mints one key per intent, so a retry is the first timeline’s answer', () => {
    // The child's id is minted from this key. A fresh random one per attempt makes a
    // double-click two identical timelines and a lost response a third, and the sixteen-timeline
    // cap is what the player meets for it (U16's review, finding 1).
    expect(forkIdempotencyKey(12n, 2)).toBe(forkIdempotencyKey(12n, 2))
    // And two *different* alternatives at one decision are two timelines, which is the mechanic.
    expect(forkIdempotencyKey(12n, 2)).not.toBe(forkIdempotencyKey(12n, 0))
    expect(forkIdempotencyKey(12n, 2)).not.toBe(forkIdempotencyKey(30n, 2))
  })

  it('posts the sequence as a number, with the derived key', async () => {
    const bodies: string[] = []
    const urls: string[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string, init: RequestInit) => {
        urls.push(url)
        bodies.push(String(init.body))
        return new Response(JSON.stringify(forked()), { status: 200 })
      }),
    )

    await forkTimeline('run 1', 12n, 2)

    expect(urls).toEqual(['/api/runs/run%201/fork'])
    expect(JSON.parse(bodies[0])).toEqual({
      // A number, because the route parses it as one and refuses a string outright — unlike
      // every tick and sequence on the event wire, which travel as strings.
      at_seq: 12,
      option_index: 2,
      idempotency_key: forkIdempotencyKey(12n, 2),
    })
  })

  it('refuses a sequence a JSON number cannot carry exactly', async () => {
    // The route's own reasoning, applied one layer earlier: rounding it would name a different
    // decision than the player chose, and nothing in the answer would say so.
    const called = vi.fn()
    vi.stubGlobal('fetch', called)

    await expect(forkTimeline('run-1', BigInt(Number.MAX_SAFE_INTEGER) + 1n, 0)).rejects.toThrow(
      /different decision/,
    )
    expect(called).not.toHaveBeenCalled()
  })

  it('reports a refusal on the payload and a 404 as an error', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        new Response(JSON.stringify(forked({ refusal: 'that sequence is not a decision.' })), {
          status: 200,
        }),
      ),
    )
    expect((await forkTimeline('run-1', 12n, 2)).refusal).toContain('not a decision')

    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(JSON.stringify({ detail: 'no run run-9' }), { status: 404 })),
    )
    await expect(forkTimeline('run-9', 12n, 2)).rejects.toThrow('no run run-9')
  })
})

describe('what the landing knows that the log does not', () => {
  it('clears the run at the rate the switch reported, in one write', () => {
    // A run's rate is the one field this client never learns from its log: `RATE_CHANGED` is
    // appended when a rate *moves*, and neither a run created at ×1 nor a child forked at zero
    // has moved. Measured on the compose path: the child's log held ten events and not one of
    // them was a rate, so the clock control read ×1 over a world standing still.
    aFreshRun()
    act(() => {
      useRunStore.getState().reset(0)
    })
    expect(useRunStore.getState().rate).toBe(0)

    // And with nothing said, the empty state stands — which is right for a run being started
    // rather than entered, and is the only case left that reaches it.
    act(() => {
      useRunStore.getState().reset()
    })
    expect(useRunStore.getState().rate).toBe(1)
  })

  it('does not read entering a timeline as resuming the one it left', async () => {
    // The defect this pins is a command the player never issued. Leaving a *paused* timeline
    // used to return the store to its defaults — rate 0 to 1 — which the shell read as a resume
    // and answered by re-stating the held direction into the timeline it had just entered. That
    // one arrives paused, refuses it, and the refusal reaches the player as a banner.
    const { Shell } = await import('../src/ui/Shell')

    const posted: string[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => {
        if (url.includes('/commands')) {
          posted.push(url)
          return new Response(
            JSON.stringify({ produced_seq: [], reason: 'the run is paused.' }),
            { status: 200 },
          )
        }
        if (url.endsWith('/fork')) return new Response(JSON.stringify(forked()), { status: 200 })
        if (url.endsWith('/switch')) return new Response(JSON.stringify(switched()), { status: 200 })
        return new Response(JSON.stringify(aLineage()), { status: 200 })
      }),
    )

    aFreshRun()
    act(() => {
      // The parent is paused, which is where a switch leaves it.
      useRunStore.getState().apply({
        kind: 'RATE_CHANGED',
        seq: '11',
        tick: '900',
        schema_ver: 2,
        rules_ver: 'test',
        run_id: 'run-1',
        command_id: '',
        request_id: '',
        payload: { tick: 900, rate: 0 },
      })
      useRunStore.getState().apply(resolvedFrame({ seq: 12, item: 'wi_ap_map', optionIndex: 1 }))
    })

    const stream = { start: () => {}, stop: () => {} }
    let attached = 'run-1'
    const host = await mount(
      createElement(Shell, {
        runId: attached,
        makeStream: () => stream,
        storage: null,
        onEnterTimeline: (next: string) => {
          attached = next
        },
      }),
    )

    await act(async () => {
      ;(host.querySelector('.alternative__fork') as HTMLButtonElement).click()
    })
    await act(async () => {})

    // Nothing was sent into the timeline the player just entered, and no banner claims one was
    // refused. The clock control reads what the switch actually reported.
    expect(posted).toEqual([])
    expect(host.querySelector('.banner')).toBeNull()
    expect(useRunStore.getState().rate).toBe(0)
  })

  it('does not walk the CEO into a timeline on a key held in another one', async () => {
    // The other half, and the worse half. Entering a timeline left *running* from a paused one
    // moves the store's rate 0 to 1 legitimately — so carrying the real rate does not stop the
    // shell reading it as a resume, and the command it re-states is accepted rather than
    // refused. The CEO then sets off across a world the player has only just arrived in, in a
    // direction they were holding somewhere else.
    const posted: string[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => {
        if (url.includes('/commands')) {
          posted.push(url)
          return new Response(JSON.stringify({ produced_seq: ['9'], reason: '' }), { status: 200 })
        }
        if (url.endsWith('/switch')) {
          return new Response(JSON.stringify(switched({ rate: 1 })), { status: 200 })
        }
        return new Response(JSON.stringify(aLineage()), { status: 200 })
      }),
    )

    aFreshRun()
    act(() => {
      useRunStore.getState().apply({
        kind: 'RATE_CHANGED',
        seq: '11',
        tick: '900',
        schema_ver: 2,
        rules_ver: 'test',
        run_id: 'run-1',
        command_id: '',
        request_id: '',
        payload: { tick: 900, rate: 0 },
      })
    })

    const host = await mount(createElement(ShellHarness, { from: 'run-1' }))

    // A direction held in this timeline, which the paused run refuses and the kernel therefore
    // never applied. It must not be re-stated into the next one.
    act(() => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'w', bubbles: true }))
    })
    posted.length = 0

    await act(async () => {
      ;(host.querySelectorAll('.stage-toggle button')[2] as HTMLButtonElement).click()
    })
    await act(async () => {})
    const canvas = host.querySelector('canvas.universe__tree') as HTMLCanvasElement
    canvas.getBoundingClientRect = () =>
      ({ left: 0, top: 0, width: canvas.width, height: canvas.height }) as DOMRect
    await act(async () => {
      // The child's plate, one depth along.
      canvas.dispatchEvent(
        new MouseEvent('click', { bubbles: true, clientX: 18 * 16 + 4, clientY: 4 }),
      )
    })
    await act(async () => {
      ;(host.querySelector('.universe__enter') as HTMLButtonElement).click()
    })
    await act(async () => {})

    expect(useRunStore.getState().rate).toBe(1)
    expect(posted).toEqual([])
  })
})

describe('where a fork lands the player', () => {
  it('opens the Universe on the new node, and clears the timeline it left', async () => {
    const { Shell } = await import('../src/ui/Shell')

    aFreshRun()
    act(() => {
      useRunStore.getState().apply(resolvedFrame({ seq: 12, item: 'wi_ap_map', optionIndex: 1 }))
    })

    // The whole landing is two network calls the shell does not make itself, so they are the
    // two this test stands in for. Everything after them is the shell's.
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => {
        if (url.endsWith('/fork')) {
          return new Response(JSON.stringify(forked()), { status: 200 })
        }
        if (url.endsWith('/switch')) {
          return new Response(JSON.stringify(switched()), { status: 200 })
        }
        if (url.endsWith('/lineage')) {
          return new Response(JSON.stringify(aLineage()), { status: 200 })
        }
        return new Response('{}', { status: 200 })
      }),
    )

    const entered: string[] = []
    const stream = { start: () => {}, stop: () => {} }
    const host = await mount(
      createElement(Shell, {
        runId: 'run-1',
        makeStream: () => stream,
        storage: null,
        onEnterTimeline: (next: string) => entered.push(next),
      }),
    )

    expect(host.querySelector('main')?.dataset.stage).toBe('office')

    await act(async () => {
      ;(host.querySelector('.alternative__fork') as HTMLButtonElement).click()
    })
    // The switch and the lineage read both resolve on later microtasks.
    await act(async () => {})

    // The stage moved on its own: without it the player presses the button and sees the same
    // office at the same tick, because a child arrives paused at the decision it reconsiders.
    expect(host.querySelector('main')?.dataset.stage).toBe('universe')
    expect(entered).toEqual(['run-1-child'])
    // The store was cleared before the new timeline's frames could be dropped by the sequence
    // guard, which is what leaves the HUD stating its absence rather than showing the parent's.
    expect(host.querySelector('.shell__attaching')).not.toBeNull()
    // And the tree opened on the node the fork made, rather than on nothing.
    expect(host.querySelector('.universe__id')?.textContent).toBe('run-1-child')
    expect(host.querySelector('.universe__divergence')?.textContent).toContain(
      'Let the team decide',
    )
  })
})
