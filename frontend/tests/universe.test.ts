import { act, createElement } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ACCENT, OK, PAL } from '../src/design/tokens'
import { LineageUnavailable, fetchLineage, switchTimeline } from '../src/net/gateway'
import type { LineageWire, TimelineWire } from '../src/net/gateway'
import { Tree } from '../src/universe/Tree'
import {
  GRID,
  NODE_COLS,
  NODE_ROWS,
  buildTreeModel,
  canvasSize,
  describeDivergence,
  describeTimeline,
  drawTree,
  layoutTimelines,
  shortId,
  timelineAt,
  timelineState,
} from '../src/universe/model'
import { STAGES, nextStage } from '../src/ui/stage'
import { RecordingContext } from './helpers/frames'

/**
 * The Universe: every timeline, and moving between them (U17: M49, R11).
 *
 * The properties worth asserting here are the ones a screenshot cannot show and a reader cannot
 * check by eye:
 *
 * * **layout is stable under insertion.** A fork *is* an insertion, so a tree that reordered would
 *   rearrange itself in the same gesture that added to it — and the player's own history would
 *   move while they were looking at it;
 * * **the three states are distinguishable with every hue flattened**, because a node's state is
 *   carried by border form and words first and colour third;
 * * **a switch reaches the surface only after the backend moved the clock**, so the client cannot
 *   end up subscribed to a timeline nobody resumed;
 * * **an ended timeline cannot be entered and can still be forked**, which is the demo's last beat
 *   and the one rule of this surface a player will test on purpose.
 */

const HOSTS: HTMLElement[] = []

afterEach(() => {
  vi.unstubAllGlobals()
  for (const host of HOSTS.splice(0)) host.remove()
})

function node(overrides: Partial<TimelineWire> = {}): TimelineWire {
  return {
    run_id: 'run-1',
    parent_run_id: '',
    forked_at_seq: 0,
    tick: 1080,
    day: 3,
    rate: 1,
    head_seq: 40,
    terminal_reason: '',
    created_at: '2026-08-27T00:00:00.000+00:00',
    item: '',
    cp_index: -1,
    option_index: -1,
    parent_option_index: -1,
    choice: '',
    parent_choice: '',
    ...overrides,
  }
}

function lineage(nodes: TimelineWire[], asked = nodes[0].run_id): LineageWire {
  return { root_run_id: nodes[0].run_id, asked_about: asked, cap: 16, nodes }
}

/** A parent, a child and a grandchild — the shape a tree has to get right and a list does not. */
function aDeepLineage(): LineageWire {
  return lineage([
    node({ run_id: 'run-1', rate: 0 }),
    node({
      run_id: 'run-1-aaa',
      parent_run_id: 'run-1',
      forked_at_seq: 12,
      item: 'wi_ap_map',
      cp_index: 0,
      option_index: 1,
      parent_option_index: 0,
      choice: 'Keep the paper',
      parent_choice: 'Drop it',
      day: 4,
    }),
    node({
      run_id: 'run-1-bbb',
      parent_run_id: 'run-1-aaa',
      forked_at_seq: 30,
      item: 'wi_faq',
      cp_index: 1,
      option_index: 2,
      parent_option_index: 0,
      choice: 'Publish it',
      parent_choice: 'Sit on it',
      rate: 0,
      terminal_reason: 'insolvent',
      day: 9,
    }),
  ])
}

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

describe('the three states', () => {
  it('reads a node as active, paused or ended from its own fields', () => {
    expect(timelineState(node({ rate: 1 }))).toBe('active')
    expect(timelineState(node({ rate: 0 }))).toBe('paused')
    // Ended wins over a rate, which is exactly the state a half-finished switch leaves behind:
    // the run ended and its row still says it was running.
    expect(timelineState(node({ rate: 3, terminal_reason: 'horizon' }))).toBe('ended')
  })

  it('says where a timeline is, and why it is not somewhere else', () => {
    expect(describeTimeline(node({ rate: 2, day: 3 }))).toBe('running · day 3')
    expect(describeTimeline(node({ rate: 0, day: 3 }))).toBe('paused · day 3')
    expect(describeTimeline(node({ terminal_reason: 'insolvent', day: 9 }))).toBe(
      'ended day 9 · insolvent',
    )
  })

  it('names the decision that separated two timelines, and nothing for a root', () => {
    const [root, child] = aDeepLineage().nodes
    expect(describeDivergence(root)).toBe('')
    expect(describeDivergence(child)).toBe('wi_ap_map: Keep the paper instead of Drop it')
  })
})

describe('the layout', () => {
  it('places a node by its distance from the lineage root', () => {
    const placements = layoutTimelines(aDeepLineage().nodes)

    expect(placements.map((place) => place.depth)).toEqual([0, 1, 2])
    expect(placements.map((place) => place.row)).toEqual([0, 0, 0])
    expect(placements[1].x).toBeGreaterThan(placements[0].x)
  })

  it('does not reorder what is already there when a fork arrives', () => {
    // The property the surface rests on. A fork is an insertion, so a layout that reshuffled
    // would rearrange the player's history in the same gesture that added to it.
    const nodes = aDeepLineage().nodes
    const before = layoutTimelines(nodes)

    const withSibling = layoutTimelines([
      ...nodes,
      node({ run_id: 'run-1-ccc', parent_run_id: 'run-1', forked_at_seq: 12, item: 'wi_faq' }),
    ])

    for (const place of before) {
      const after = withSibling.find((entry) => entry.runId === place.runId)
      expect(after, place.runId).toBeDefined()
      expect([after?.x, after?.y], place.runId).toEqual([place.x, place.y])
    }
    // And the newcomer is placed under its parent's column rather than at the root's.
    expect(withSibling.at(-1)?.depth).toBe(1)
    expect(withSibling.at(-1)?.row).toBe(1)
  })

  it('gives a one-node lineage a canvas, and hit-tests what it drew', () => {
    const placements = layoutTimelines([node()])
    const size = canvasSize(placements)

    expect(size.width).toBeGreaterThanOrEqual(NODE_COLS * GRID)
    expect(size.height).toBeGreaterThanOrEqual(NODE_ROWS * GRID)
    expect(timelineAt(placements, 4, 4)).toBe('run-1')
    expect(timelineAt(placements, size.width - 1, size.height - 1)).toBeNull()
  })

  it('draws a node whose parent is not in the tree rather than dropping it', () => {
    // A row trimmed by a store this build did not write. The alternative is a node that silently
    // vanishes from a history, which is worse than one drawn as a root of its own.
    const orphan = layoutTimelines([node({ run_id: 'run-x', parent_run_id: 'run-gone' })])
    expect(orphan).toHaveLength(1)
    expect(orphan[0].depth).toBe(0)
  })
})

describe('drawing the tree', () => {
  it('marks where the player is standing, in a way that survives greyscale', () => {
    const model = buildTreeModel(lineage(aDeepLineage().nodes, 'run-1-aaa'))
    const size = canvasSize(model.placements)
    const context = new RecordingContext()

    drawTree(context, model, size.width, size.height)

    const accent = context.inColour(ACCENT)
    expect(accent.length, 'the standing marker was not drawn').toBeGreaterThan(0)

    // A filled bar, not a tint: it has width and height, so it reads with every hue flattened.
    const standing = model.placements.find((place) => place.runId === 'run-1-aaa')
    expect(
      accent.some(
        (rect) => rect.x === (standing?.x ?? -1) + 2 && rect.width === 4 && rect.height > 4,
      ),
      'the standing marker is not the left-edge bar',
    ).toBe(true)
  })

  it('gives each state its own form, and only reuses a hue for what it already means', () => {
    const model = buildTreeModel(aDeepLineage())
    const size = canvasSize(model.placements)
    const context = new RecordingContext()

    drawTree(context, model, size.width, size.height)

    const hues = context.colours()
    // `ok` for an ended timeline: a run that finished is an outcome that landed, not a fault.
    expect(hues.has(OK)).toBe(true)
    // Amber is reserved for a person waiting on the CEO and must never appear here.
    expect([...hues].some((hue) => hue.toLowerCase() === '#f2c46b')).toBe(false)
    // The ground is the product's, not a surface of its own.
    expect(hues.has(PAL.ganglan)).toBe(true)
  })

  it('shortens a run id to its tail rather than truncating its head', () => {
    // Forked ids are `<parent>-<hex>`, so the head is what every sibling shares.
    expect(shortId('run-1-abcdef123456')).toBe('abcdef12')
    expect(shortId('run-1')).toBe('1')
  })
})

describe('the stage toggle', () => {
  it('cycles three stages and always lands on one of them', () => {
    expect(STAGES).toEqual(['office', 'dag', 'universe'])
    expect(nextStage('office')).toBe('dag')
    expect(nextStage('dag')).toBe('universe')
    expect(nextStage('universe')).toBe('office')
  })
})

describe('the Universe surface', () => {
  it('reads the tree and offers the timeline you are not in', async () => {
    const reader = async () => aDeepLineage()
    const host = await mount(
      createElement(Tree, { runId: 'run-1', reader, switcher: async () => ({ refusal: '', rate: 1 }) }),
    )

    expect(host.querySelector('canvas.universe__tree')).not.toBeNull()
    expect(host.querySelector('.universe__scale')?.textContent).toContain('3 of 16')
    // Nothing selected yet: the panel says so rather than picking for the player.
    expect(host.querySelector('.universe__hint')?.textContent).toContain('Pick a timeline')
  })

  it('enters a timeline only after the backend has moved the clock', async () => {
    const asked: Array<[string, string]> = []
    const entered: string[] = []
    let resolve: (value: { refusal: string; rate: number }) => void = () => {}

    const host = await mount(
      createElement(Tree, {
        runId: 'run-1',
        reader: async () => aDeepLineage(),
        onEnter: (next: string) => entered.push(next),
        switcher: (from: string, to: string) => {
          asked.push([from, to])
          return new Promise<{ refusal: string; rate: number }>((settle) => {
            resolve = settle
          })
        },
      }),
    )

    // Select the child by clicking where it was drawn, which is the same placement the paint used.
    const canvas = host.querySelector('canvas.universe__tree') as HTMLCanvasElement
    const placements = layoutTimelines(aDeepLineage().nodes)
    const child = placements.find((place) => place.runId === 'run-1-aaa')
    canvas.getBoundingClientRect = () =>
      ({ left: 0, top: 0, width: canvas.width, height: canvas.height }) as DOMRect

    await act(async () => {
      canvas.dispatchEvent(
        new MouseEvent('click', {
          bubbles: true,
          clientX: (child?.x ?? 0) + 4,
          clientY: (child?.y ?? 0) + 4,
        }),
      )
    })
    expect(host.querySelector('.universe__id')?.textContent).toBe('run-1-aaa')

    const enter = host.querySelector('.universe__enter') as HTMLButtonElement
    await act(async () => {
      enter.click()
    })

    // Asked, and not yet entered: the run id must not move until the clock has.
    expect(asked).toEqual([['run-1', 'run-1-aaa']])
    expect(entered).toEqual([])
    expect((host.querySelector('.universe__enter') as HTMLButtonElement).disabled).toBe(true)

    await act(async () => {
      resolve({ refusal: '', rate: 1 })
    })
    expect(entered).toEqual(['run-1-aaa'])
  })

  it('shows a refusal rather than moving the player', async () => {
    const entered: string[] = []
    const host = await mount(
      createElement(Tree, {
        runId: 'run-1',
        reader: async () => aDeepLineage(),
        onEnter: (next: string) => entered.push(next),
        switcher: async () => ({ refusal: 'run-1-aaa is not in the same lineage as run-1.', rate: 0 }),
      }),
    )

    const canvas = host.querySelector('canvas.universe__tree') as HTMLCanvasElement
    canvas.getBoundingClientRect = () =>
      ({ left: 0, top: 0, width: canvas.width, height: canvas.height }) as DOMRect
    const child = layoutTimelines(aDeepLineage().nodes).find(
      (place) => place.runId === 'run-1-aaa',
    )
    await act(async () => {
      canvas.dispatchEvent(
        new MouseEvent('click', {
          bubbles: true,
          clientX: (child?.x ?? 0) + 4,
          clientY: (child?.y ?? 0) + 4,
        }),
      )
    })
    await act(async () => {
      ;(host.querySelector('.universe__enter') as HTMLButtonElement).click()
    })

    expect(entered).toEqual([])
    expect(host.querySelector('.universe__refusal')?.textContent).toContain('same lineage')
  })

  it('offers no way into an ended timeline, and says it can still be forked', async () => {
    const host = await mount(
      createElement(Tree, {
        runId: 'run-1',
        reader: async () => aDeepLineage(),
        switcher: async () => ({ refusal: '', rate: 1 }),
      }),
    )

    const canvas = host.querySelector('canvas.universe__tree') as HTMLCanvasElement
    canvas.getBoundingClientRect = () =>
      ({ left: 0, top: 0, width: canvas.width, height: canvas.height }) as DOMRect
    const ended = layoutTimelines(aDeepLineage().nodes).find(
      (place) => place.runId === 'run-1-bbb',
    )
    await act(async () => {
      canvas.dispatchEvent(
        new MouseEvent('click', {
          bubbles: true,
          clientX: (ended?.x ?? 0) + 4,
          clientY: (ended?.y ?? 0) + 4,
        }),
      )
    })

    expect((host.querySelector('.universe__enter') as HTMLButtonElement).disabled).toBe(true)
    expect(host.querySelector('.universe__ended')?.textContent).toContain('forked')
  })

  it('says the lineage is full instead of leaving a fork to be refused', async () => {
    const nodes = Array.from({ length: 4 }, (_, index) =>
      node({ run_id: `run-${index}`, parent_run_id: index === 0 ? '' : 'run-0' }),
    )
    const host = await mount(
      createElement(Tree, {
        runId: 'run-0',
        reader: async () => ({ ...lineage(nodes), cap: 4 }),
        switcher: async () => ({ refusal: '', rate: 1 }),
      }),
    )

    expect(host.querySelector('.universe__scale')?.textContent).toContain('full')
  })

  it('reads nothing while another view holds the stage', async () => {
    const reads: string[] = []
    await mount(
      createElement(Tree, {
        runId: 'run-1',
        active: false,
        reader: async (runId: string) => {
          reads.push(runId)
          return aDeepLineage()
        },
        switcher: async () => ({ refusal: '', rate: 1 }),
      }),
    )

    expect(reads).toEqual([])
  })

  it('says why the tree could not be read', async () => {
    const host = await mount(
      createElement(Tree, {
        runId: 'run-1',
        reader: async () => {
          throw new LineageUnavailable(404, 'no run run-1')
        },
        switcher: async () => ({ refusal: '', rate: 1 }),
      }),
    )

    expect(host.querySelector('.universe__error')?.textContent).toContain('no run run-1')
  })
})

describe('the two calls this surface makes', () => {
  it('asks the route for the lineage, encoding the run id', async () => {
    const seen: string[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => {
        seen.push(url)
        return new Response(JSON.stringify(aDeepLineage()), { status: 200 })
      }),
    )

    await fetchLineage('run 1')
    expect(seen).toEqual(['/api/runs/run%201/lineage'])
  })

  it('sends the timeline to enter, and the rate only when one is named', async () => {
    const bodies: string[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (_url: string, init: RequestInit) => {
        bodies.push(String(init.body))
        return new Response(JSON.stringify({ refusal: '' }), { status: 200 })
      }),
    )

    await switchTimeline('run-1', 'run-2')
    await switchTimeline('run-1', 'run-2', 0)

    expect(JSON.parse(bodies[0])).toEqual({ to: 'run-2' })
    // Zero is a rate, not an absence: switching into a paused timeline leaves it paused, and it
    // has to be possible to say so.
    expect(JSON.parse(bodies[1])).toEqual({ to: 'run-2', rate: 0 })
  })
})

describe('the stage the shell puts it on', () => {
  it('offers the Universe beside the office and the chain, and Tab reaches it', async () => {
    const { Shell } = await import('../src/ui/Shell')
    const { useRunStore } = await import('./helpers/store-helpers')
    const { genesisFrame } = await import('./helpers/frames')

    const stream = { start: () => {}, stop: () => {} }
    const host = await mount(
      createElement(Shell, { runId: 'run-1', makeStream: () => stream, storage: null }),
    )
    act(() => {
      useRunStore.getState().reset()
      useRunStore.getState().apply(genesisFrame())
    })

    const buttons = [...host.querySelectorAll('.stage-toggle button')] as HTMLButtonElement[]
    expect(buttons.map((button) => button.textContent)).toEqual(['Office', 'Chain', 'Universe'])

    await act(async () => {
      buttons[2].click()
    })
    expect(host.querySelector('main')?.dataset.stage).toBe('universe')

    // The office canvas is hidden rather than unmounted, like the trip through the DAG: coming
    // back from the Universe must not rebuild the renderer.
    expect(host.querySelector('canvas.office')).not.toBeNull()
    expect(host.querySelector<HTMLElement>('.universe-host')?.dataset.hidden).toBe('false')

    // Tab cycles, and lands back in the office rather than sticking on the last stage.
    await act(async () => {
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', bubbles: true }))
    })
    expect(host.querySelector('main')?.dataset.stage).toBe('office')
  })

  it('does not render a HUD for a world that does not exist', async () => {
    // The moment between leaving one timeline and the next one's genesis landing, which is also
    // every attach. With an empty store every tile renders a real-looking zero, and a player
    // cannot tell that from a company with no cash.
    const { Shell } = await import('../src/ui/Shell')
    const { useRunStore } = await import('./helpers/store-helpers')
    const { genesisFrame } = await import('./helpers/frames')

    const stream = { start: () => {}, stop: () => {} }
    act(() => {
      useRunStore.getState().reset()
    })
    const host = await mount(
      createElement(Shell, { runId: 'run-2', makeStream: () => stream, storage: null }),
    )

    expect(host.querySelectorAll('[data-tile]')).toHaveLength(0)
    expect(host.querySelector('.shell__attaching')?.textContent).toContain('Attaching')

    act(() => {
      useRunStore.getState().apply(genesisFrame())
    })

    expect(host.querySelector('.shell__attaching')).toBeNull()
    expect(host.querySelectorAll('[data-tile]').length).toBeGreaterThan(0)
  })
})
