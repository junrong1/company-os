import { act, createElement } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { MemoryUnavailable, type MemoryWire, fetchMemory } from '../src/net/gateway'
import { MemoryAffordance, MemoryPanel } from '../src/ui/Memory'
import {
  KIND_PHRASES,
  awaitingProse,
  eventLine,
  memoryScale,
  memorySentence,
  memoryStamp,
  toMemoryView,
} from '../src/ui/memory-model'
import { FALLBACK_SENTENCES } from '../src/ui/conversation-model'

/**
 * A director's memory, on the surface that shows it (U14: M36, M37, M38).
 *
 * Two properties are what this suite is for, and neither is about how the panel looks.
 *
 * **The panel opens on the derived half.** The selection is a log read and the prose is a provider
 * call, so the panel asks twice — and the assertions below are over the *calls*, because "it renders
 * eventually" would pass on a build that blocked the whole panel on a model. The keyless case is the
 * same property from the other side: one call, no pending line, nothing that cannot resolve (M38).
 *
 * **Every sentence carries its events, and every figure carries a marking.** The backend refuses a
 * summary sentence that cites nothing; this is the half of that claim the reader actually meets. The
 * marking is the *measured* one rather than the authored-tuning one on purpose: a sim-day and a log
 * sequence resolve to a row, so saying "authored tuning" about them would be the lie the two
 * attributes exist to keep apart.
 */

const HOSTS: HTMLElement[] = []

afterEach(() => {
  vi.unstubAllGlobals()
  for (const host of HOSTS.splice(0)) host.remove()
})

function wire(overrides: Partial<MemoryWire> = {}): MemoryWire {
  return {
    director: 'dir_hr',
    line: ['dir_hr', 'stf_rec'],
    as_of_tick: 2160,
    as_of_day: 4,
    through_day: 3,
    considered: 9,
    selected: 2,
    events: [
      {
        seq: 7,
        tick: 540,
        day: 1,
        kind: 'WORK_ASSIGNED',
        person: 'stf_rec',
        item: 'wi_hiring',
        detail: 'delegated',
      },
      {
        seq: 12,
        tick: 1620,
        day: 3,
        kind: 'CHECKPOINT_RAISED',
        person: 'stf_rec',
        item: 'wi_hiring',
        detail: 'Rewrite the post?',
      },
    ],
    summary: { status: 'absent', points: [], fallback: '', model_identity: '' },
    ...overrides,
  }
}

const WRITTEN = {
  status: 'written',
  fallback: '',
  model_identity: 'a-model-under-test',
  points: [
    { text: 'The search has been open since day 1.', citations: [7] },
    { text: 'It stopped for your decision on day 3.', citations: [12] },
  ],
}

/** Mount a component and hand back the host, flushing the effects' promises. */
async function mount(element: ReturnType<typeof createElement>): Promise<HTMLElement> {
  const host = document.createElement('div')
  document.body.appendChild(host)
  HOSTS.push(host)
  const root = createRoot(host)
  await act(async () => {
    root.render(element)
  })
  return host
}

/**
 * A fetcher that records what it was asked for.
 *
 * Referentially stable, which the panel's props require for the reason the shell's stream factory
 * does: it is an effect dependency, and a fresh arrow per render would re-read the memory forever.
 */
function recording(...answers: MemoryWire[]) {
  const calls: { directorId: string; summary: boolean }[] = []
  let index = 0
  const fetcher = async (
    _runId: string,
    directorId: string,
    options: { summary?: boolean } = {},
  ): Promise<MemoryWire> => {
    calls.push({ directorId, summary: options.summary === true })
    const answer = answers[Math.min(index, answers.length - 1)]
    index += 1
    return answer
  }
  return { calls, fetcher }
}

describe('the memory model', () => {
  it('says what each event kind means, and falls back to the kind itself', () => {
    // The failure this prevents is a blank row: a kind added to the backend's admission table
    // before a phrase is added here still reads as something.
    expect(eventLine(wire().events[0])).toContain(KIND_PHRASES.WORK_ASSIGNED)
    expect(eventLine(wire().events[0])).toContain('stf_rec')
    expect(eventLine(wire().events[0])).toContain('wi_hiring')

    const invented = { ...wire().events[0], kind: 'SOMETHING_NEW', detail: '', item: '' }
    expect(eventLine(invented)).toBe('stf_rec SOMETHING_NEW')
  })

  it('reads a refusal through the same table the bench block does', () => {
    // One vocabulary of conditions across both surfaces. A timeout has to say the same thing
    // wherever the player meets it, or the two panels are two products.
    expect(memorySentence({ status: 'refused', fallback: 'timeout' })).toBe(
      FALLBACK_SENTENCES.timeout,
    )
    expect(memorySentence({ status: 'written', fallback: '' })).toBe('')
    expect(memorySentence({ status: 'pending', fallback: '' })).toContain('Putting')
    expect(memorySentence({ status: 'absent', fallback: '' })).toContain('events')
  })

  it('keeps the day being read and the day the account runs to as two facts', () => {
    expect(memoryStamp({ asOfDay: 7, throughDay: 4, status: 'written' })).toBe(
      'as of day 7 · account runs to day 4',
    )
    // Collapsed when they agree, and when there is no account to be running to: a stamp that
    // said "runs to day 7" over a panel with no prose would be describing nothing.
    expect(memoryStamp({ asOfDay: 7, throughDay: 7, status: 'written' })).toBe('as of day 7')
    expect(memoryStamp({ asOfDay: 7, throughDay: 4, status: 'absent' })).toBe('as of day 7')
  })

  it('says how much of the line the selection stands for', () => {
    expect(memoryScale({ considered: 40, selected: 12 })).toBe('12 of 40 events')
    // Not "12 of 12", which reads as a cap that happened to be reached rather than as the whole
    // of what the line has done.
    expect(memoryScale({ considered: 12, selected: 12 })).toBe('12 events')
  })

  it('maps the wire and treats an unknown status as an absent summary', () => {
    const view = toMemoryView(wire({ summary: { ...WRITTEN, status: 'something-else' } }))
    expect(view.status).toBe('absent')
    expect(awaitingProse(view)).toBe(false)
    expect(awaitingProse(toMemoryView(wire({ summary: { ...WRITTEN, status: 'pending' } })))).toBe(
      true,
    )
  })
})

describe('the memory panel', () => {
  it('opens on the derived selection and fills the prose in behind it', async () => {
    const derived = wire({ summary: { status: 'pending', points: [], fallback: '', model_identity: '' } })
    const { calls, fetcher } = recording(derived, wire({ summary: WRITTEN }))

    const host = await mount(
      createElement(MemoryPanel, { runId: 'run-1', directorId: 'dir_hr', name: 'Ada', fetcher }),
    )

    // Two calls, and the first one asked for no prose. This is the assertion that fails on a
    // build that waits for a model before drawing anything.
    expect(calls).toEqual([
      { directorId: 'dir_hr', summary: false },
      { directorId: 'dir_hr', summary: true },
    ])

    expect(host.querySelector('.memory')?.getAttribute('data-memory')).toBe('written')
    const points = [...host.querySelectorAll('.memory__point')]
    expect(points).toHaveLength(2)
    // Provenance per sentence, which is the panel's whole promise.
    for (const point of points) {
      expect(point.querySelector('.memory__cites')?.textContent).toMatch(/#\d+/)
    }
    expect(host.querySelectorAll('.memory__event')).toHaveLength(2)
  })

  it('asks once on a keyless run and never shows a line that cannot resolve', async () => {
    // Covers M38. The first read already says `absent`, so there is no second call and no
    // pending sentence — which is the version of this that would be easiest to ship broken.
    const { calls, fetcher } = recording(wire())

    const host = await mount(
      createElement(MemoryPanel, { runId: 'run-1', directorId: 'dir_hr', fetcher }),
    )

    expect(calls).toEqual([{ directorId: 'dir_hr', summary: false }])
    expect(host.querySelector('.memory')?.getAttribute('data-memory')).toBe('absent')
    expect(host.querySelector('.memory__because')?.textContent).toContain('events')
    expect(host.querySelectorAll('.memory__point')).toHaveLength(0)
    expect(host.querySelectorAll('.memory__event')).toHaveLength(2)
  })

  it('marks every figure it renders as counted rather than authored (R27, R28)', async () => {
    const { fetcher } = recording(wire({ summary: WRITTEN }))
    const host = await mount(
      createElement(MemoryPanel, { runId: 'run-1', directorId: 'dir_hr', fetcher }),
    )

    const withDigits = [
      ...host.querySelectorAll(
        '.memory__stamp, .memory__scale, .memory__day, .memory__seq, .memory__cites',
      ),
    ].filter((node) => /\d/.test(node.textContent ?? ''))

    expect(withDigits.length).toBeGreaterThan(4)
    for (const figure of withDigits) {
      expect(
        figure.querySelector('[data-measured]'),
        `unmarked figure: ${figure.textContent}`,
      ).not.toBeNull()
      // And not the other marking. A day and a sequence resolve to a row in the log; calling
      // them authored tuning would be the lie the two attributes exist to keep apart.
      expect(figure.querySelector('[data-authored-tuning]')).toBeNull()
    }
  })

  it('says which of the two failures happened', async () => {
    const failing = async () => {
      throw new MemoryUnavailable(503, 'the memory could not be read: the store is gone')
    }

    const host = await mount(
      createElement(MemoryPanel, { runId: 'run-1', directorId: 'dir_hr', fetcher: failing }),
    )

    expect(host.querySelector('.memory')?.getAttribute('data-memory')).toBe('error')
    expect(host.querySelector('.memory__error')?.textContent).toContain('store is gone')
    expect(host.querySelectorAll('.memory__event')).toHaveLength(0)
  })

  it('renders a line with no history as having none, rather than as empty chrome', async () => {
    const { fetcher } = recording(wire({ events: [], selected: 0, considered: 0, through_day: 0 }))
    const host = await mount(
      createElement(MemoryPanel, { runId: 'run-1', directorId: 'dir_cs', fetcher }),
    )

    expect(host.querySelector('.memory__empty')?.textContent).toContain('Nothing has happened')
  })
})

describe('the affordance', () => {
  it('reads nothing until it is opened', async () => {
    const { calls, fetcher } = recording(wire())

    const host = await mount(
      createElement(MemoryAffordance, { runId: 'run-1', directorId: 'dir_hr', name: 'Ada', fetcher }),
    )

    // A memory costs a log read and, on a changed selection, one model call. Four rails' worth of
    // them on mount is not something the player asked for.
    expect(calls).toEqual([])
    expect(host.querySelector('.memory')).toBeNull()

    const toggle = host.querySelector('.memory-affordance__toggle') as HTMLButtonElement
    await act(async () => {
      toggle.click()
    })

    expect(calls).toHaveLength(1)
    expect(host.querySelector('.memory')).not.toBeNull()
    expect(toggle.getAttribute('aria-expanded')).toBe('true')
  })
})

describe('the memory read itself', () => {
  it('asks the route for the run and the director, and only asks for prose when told to', async () => {
    const seen: string[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => {
        seen.push(url)
        return new Response(JSON.stringify(wire()), { status: 200 })
      }),
    )

    await fetchMemory('run 1', 'dir_hr')
    await fetchMemory('run 1', 'dir_hr', { summary: true })

    // Encoded, because a run id comes out of the address bar and a director id out of a scenario
    // file — neither is this module's to trust as a path segment.
    expect(seen[0]).toBe('/api/runs/run%201/memory/dir_hr')
    expect(seen[1]).toBe('/api/runs/run%201/memory/dir_hr?summary=1')
  })

  it('throws the status, so the panel can tell 404 from 503', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () =>
          new Response(JSON.stringify({ detail: 'not a director of run r' }), { status: 404 }),
      ),
    )

    await expect(fetchMemory('r', 'stf_rec')).rejects.toMatchObject({
      name: 'MemoryUnavailable',
      status: 404,
    })
  })
})

describe('where it is reachable from', () => {
  it('offers one per director in the rail, and none for a specialist', async () => {
    const { useRunStore } = await import('./helpers/store-helpers')
    const { genesisFrame } = await import('./helpers/frames')
    const { OrgPanel } = await import('../src/ui/Panels')

    act(() => {
      useRunStore.getState().reset()
      useRunStore.getState().apply(genesisFrame())
    })

    const host = await mount(createElement(OrgPanel, { runId: 'run-1' }))

    const toggles = [...host.querySelectorAll('.memory-affordance__toggle')]
    const directors = Object.entries(useRunStore.getState().genesis?.roster ?? {}).filter(
      ([, entry]) => entry.rank === 'director',
    )
    expect(directors.length).toBeGreaterThan(1)
    expect(toggles).toHaveLength(directors.length)

    // One per *line*, not one per person: a specialist has no memory, and offering a button that
    // answers 404 would be finding that out by pressing it.
    expect(toggles.length).toBeLessThan(Object.keys(useRunStore.getState().people).length)
  })

  it('is not offered at all with no run named', async () => {
    const { useRunStore } = await import('./helpers/store-helpers')
    const { genesisFrame } = await import('./helpers/frames')
    const { OrgPanel } = await import('../src/ui/Panels')

    act(() => {
      useRunStore.getState().reset()
      useRunStore.getState().apply(genesisFrame())
    })

    const host = await mount(createElement(OrgPanel, {}))
    expect(host.querySelectorAll('.memory-affordance__toggle')).toHaveLength(0)
  })

  it('is in the conversation header for a director and not for a specialist', async () => {
    const { useRunStore } = await import('./helpers/store-helpers')
    const { genesisFrame } = await import('./helpers/frames')
    const { Conversation } = await import('../src/ui/Conversation')

    act(() => {
      useRunStore.getState().reset()
      useRunStore.getState().apply(genesisFrame())
    })

    const withDirector = await mount(
      createElement(Conversation, { personId: 'dir_hr', runId: 'run-1' }),
    )
    expect(
      withDirector.querySelector('.conversation__who .memory-affordance__toggle'),
    ).not.toBeNull()

    const withSpecialist = await mount(
      createElement(Conversation, { personId: 'stf_ap', runId: 'run-1' }),
    )
    expect(withSpecialist.querySelector('.memory-affordance__toggle')).toBeNull()
  })
})
