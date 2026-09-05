import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { act, createElement } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ACCENT, OK, PAL, TACIT } from '../src/design/tokens'
import { LineageUnavailable, fetchDiff, fetchLineage, switchTimeline } from '../src/net/gateway'
import type {
  DiffRowWire,
  DiffSideWire,
  DiffWire,
  LineageWire,
  SeparationWire,
  TimelineWire,
} from '../src/net/gateway'
import { Diff } from '../src/universe/Diff'
import { Tree } from '../src/universe/Tree'
import {
  clampDay,
  describeSeparation,
  describeSide,
  diffable,
  figureText,
  lagNote,
  readSeparation,
  rowDirection,
} from '../src/universe/diff-model'
import {
  GRID,
  NODE_COLS,
  NODE_ROWS,
  buildTreeModel,
  canvasSize,
  describeChoice,
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
import { useRunStore } from './helpers/store-helpers'

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

const ROOTS: Array<{ host: HTMLElement; root: ReturnType<typeof createRoot> }> = []

afterEach(() => {
  vi.unstubAllGlobals()
  // Unmounted, not merely removed. A root whose host is gone still holds the window listeners
  // its tree registered, and a keyboard event in a later test then reaches every shell this file
  // ever mounted — which is indistinguishable from the defect such a test is written to catch.
  // U25 found this shape in `decisions.test.ts`; it was in this file too, unexercised.
  for (const mounted of ROOTS.splice(0)) {
    act(() => mounted.root.unmount())
    mounted.host.remove()
  }
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
  const root = createRoot(host)
  ROOTS.push({ host, root })
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

// =========================================================================
// The timeline diff (U18: M50, M52)
// =========================================================================

function diffRow(overrides: Partial<DiffRowWire> = {}): DiffRowWire {
  return {
    key: 'cash',
    label: 'Cash',
    unit: '$K',
    good: 1,
    left: 4695,
    right: 4600,
    delta: -95,
    basis: 'authored-tuning',
    ...overrides,
  }
}

function diffSide(overrides: Partial<DiffSideWire> = {}): DiffSideWire {
  return {
    run_id: 'run-1',
    through_seq: 19,
    state_hash: '058dcfa4ec7bfcca369b97d86b3bf996',
    terminal_reason: '',
    reached_tick: 2233,
    current_day: 5,
    ...overrides,
  }
}

function separationWire(overrides: Partial<SeparationWire> = {}): SeparationWire {
  return {
    at_run_id: 'run-1',
    shared: true,
    partings: [
      {
        side: 'right',
        run_id: 'run-1-aaa',
        item: 'wi_ap_map',
        cp_index: 0,
        at_seq: 8,
        option_index: 1,
        choice: 'Paper is the record',
        parent_option_index: 0,
        parent_choice: 'PDF is the record',
      },
    ],
    ...overrides,
  }
}

function diffWire(overrides: Partial<DiffWire> = {}): DiffWire {
  return {
    day: 4,
    at_tick: 1620,
    max_day: 4,
    every_number_is: 'authored-tuning',
    state_shape_ver: 1,
    left: diffSide(),
    right: diffSide({ run_id: 'run-1-aaa', through_seq: 18, current_day: 4 }),
    rows: [
      diffRow(),
      diffRow({ key: 'manualHours', label: 'Manual work', good: -1, left: 340, right: 310, delta: -30 }),
      diffRow({ key: 'runway', label: 'Runway', unit: 'days', good: 0, left: 134, right: 120, delta: -14 }),
    ],
    separation: separationWire(),
    refusal: '',
    ...overrides,
  }
}

describe('reading a difference', () => {
  it('reads a metric against the direction the kernel says is an improvement', () => {
    // Cash rising and manual hours falling are both wins, and they have opposite signs. A
    // uniform rising-is-good rule would render the automation gain — the point of a run — as a
    // regression, which is why `good` is on the wire at all.
    expect(rowDirection(diffRow({ good: 1, delta: 120 }))).toBe('favourable')
    expect(rowDirection(diffRow({ good: 1, delta: -120 }))).toBe('unfavourable')
    expect(rowDirection(diffRow({ good: -1, delta: -30 }))).toBe('favourable')
  })

  it('renders a figure with no stated direction flat rather than guessing one', () => {
    // The trap, and the reason this is a test rather than an inline `directionOf`. Runway is not
    // a metric and the wire gives it `good: 0`; `Math.sign(0)` is 0, so a naive sign comparison
    // calls *every* non-zero delta unfavourable — a wrong answer that looks considered.
    expect(rowDirection(diffRow({ key: 'runway', good: 0, delta: -14 }))).toBe('flat')
    expect(rowDirection(diffRow({ key: 'runway', good: 0, delta: 14 }))).toBe('flat')
  })

  it('treats a figure neither side could know as no difference at all', () => {
    expect(rowDirection(diffRow({ left: null, right: null, delta: null }))).toBe('flat')
    expect(figureText(null)).toBe('—')
    expect(figureText(0)).toBe('0')
  })

  it('keeps a chosen day inside the range the diff can answer for', () => {
    expect(clampDay(7, 4)).toBe(4)
    expect(clampDay(0, 4)).toBe(1)
    expect(clampDay(3, 4)).toBe(3)
    // A max of zero still leaves day one askable, which is what a lineage read before either
    // clock moved looks like.
    expect(clampDay(3, 0)).toBe(1)
  })

  it('refuses to arm a diff of one timeline against itself', () => {
    expect(diffable('run-1', 'run-1-aaa')).toBe(true)
    expect(diffable('run-1', 'run-1')).toBe(false)
    expect(diffable(null, 'run-1')).toBe(false)
  })

  it('says what a column was doing at the day compared at, and never that it is running', () => {
    // "Running" is a claim about *now*, and now is the tree's to report — it is the kernel that
    // overlays a live fold on the rows, and this surface reads rows the kernel has not written
    // to. Measured live before this was fixed: two timelines that had reached their horizon read
    // "running · day 21" here while the tree beside them read "ended day 21 · horizon".
    expect(describeSide(diffSide({ terminal_reason: 'insolvent' }), 9)).toBe(
      'ended by day 9 · insolvent',
    )
    expect(describeSide(diffSide({ current_day: 12 }), 9)).toBe('went on past day 9')
    expect(describeSide(diffSide({ current_day: 9 }), 9)).toBe('day 9 is as far as it got')

    // The bound comes from readings that stand still while a timeline is quiet, so the note is
    // what keeps a refusal from reading as the diff being broken. A Universe in which both sides
    // had ended by that day has nothing to warn about: a finished history cannot get further on.
    const open = lagNote(diffWire())
    expect(open).toContain('day 4')
    expect(open).toContain('appended')
    expect(
      lagNote(
        diffWire({
          left: diffSide({ terminal_reason: 'horizon' }),
          right: diffSide({ run_id: 'run-1-aaa', terminal_reason: 'insolvent' }),
        }),
      ),
    ).toBe('')
  })
})

describe('naming what separated two timelines', () => {
  it('reads one decision as two options, taking the ancestor from the parting itself', () => {
    // The plain case: one timeline is the other's parent, so it has no parting of its own and
    // what it plays at that decision is the option the child turned away from.
    const reading = readSeparation(separationWire())

    expect(reading?.shared).toBe(true)
    expect(reading?.item).toBe('wi_ap_map')
    expect(reading?.leftChoice).toBe('PDF is the record')
    expect(reading?.rightChoice).toBe('Paper is the record')
    expect(reading?.ancestorChoice, 'neither side left a third option behind').toBe('')
  })

  it('names the option the timeline they both left is still on', () => {
    // Two forks of one checkpoint. The parent is on an option that is now on neither column, and
    // a diff that did not say so would read as if one of these two had been the original.
    const reading = readSeparation(
      separationWire({
        partings: [
          {
            side: 'left',
            run_id: 'run-1-aaa',
            item: 'wi_ap_map',
            cp_index: 0,
            at_seq: 8,
            option_index: 1,
            choice: 'Paper is the record',
            parent_option_index: 0,
            parent_choice: 'PDF is the record',
          },
          {
            side: 'right',
            run_id: 'run-1-bbb',
            item: 'wi_ap_map',
            cp_index: 0,
            at_seq: 8,
            option_index: 2,
            choice: 'Neither, for now',
            parent_option_index: 0,
            parent_choice: 'PDF is the record',
          },
        ],
      }),
    )

    expect(reading?.leftChoice).toBe('Paper is the record')
    expect(reading?.rightChoice).toBe('Neither, for now')
    expect(reading?.ancestorChoice).toBe('PDF is the record')
  })

  it('names two decisions when two cousins left their ancestor at different ones', () => {
    const reading = readSeparation(
      separationWire({
        shared: false,
        partings: [
          {
            side: 'left',
            run_id: 'run-1-aaa',
            item: 'wi_ap_map',
            cp_index: 0,
            at_seq: 8,
            option_index: 1,
            choice: 'Paper',
            parent_option_index: 0,
            parent_choice: 'PDF',
          },
          {
            side: 'right',
            run_id: 'run-1-bbb',
            item: 'wi_faq',
            cp_index: 1,
            at_seq: 31,
            option_index: 1,
            choice: 'Publish it',
            parent_option_index: 0,
            parent_choice: 'Sit on it',
          },
        ],
      }),
    )

    expect(reading?.shared).toBe(false)
    expect(reading?.turns).toEqual(['wi_ap_map: Paper instead of PDF', 'wi_faq: Publish it instead of Sit on it'])
    // And there is no single pair of options, because there is no single decision.
    expect([reading?.leftChoice, reading?.rightChoice]).toEqual(['', ''])
  })

  it('says nothing rather than inventing a decision the log cannot name', () => {
    expect(readSeparation(null)).toBeNull()
    expect(readSeparation(separationWire({ partings: [] }))).toBeNull()
  })

  it('says a decision the same way the tree does', () => {
    // One sentence about one fact. Two copies would drift on the first rewording, and the drift
    // would read as the tree and the diff disagreeing about a decision.
    expect(describeSeparation(readSeparation(separationWire()))).toBe(
      'wi_ap_map: Paper is the record instead of PDF is the record',
    )
    expect(describeChoice('wi_ap_map', '', '')).toBe('wi_ap_map')
    expect(describeChoice('', 'a', 'b')).toBe('')
  })
})

describe('picking two timelines', () => {
  it('marks the held node on the far edge from the one you are standing in', async () => {
    // Two facts about one node — where you are, and what you are about to read against — so one
    // node can be both and neither mark can hide the other. Position carries it, so it survives
    // greyscale exactly as the standing marker does.
    const model = buildTreeModel(lineage(aDeepLineage().nodes, 'run-1'), 'run-1')
    const size = canvasSize(model.placements)
    const context = new RecordingContext()

    drawTree(context, model, size.width, size.height)

    const place = model.placements.find((entry) => entry.runId === 'run-1')
    const standing = context
      .inColour(ACCENT)
      .filter((rect) => rect.x === (place?.x ?? -1) + 2 && rect.width === 4)
    const held = context
      .inColour(TACIT)
      .filter((rect) => rect.width === 4 && rect.height > 4)

    expect(standing.length, 'the standing marker went missing').toBeGreaterThan(0)
    expect(held.length, 'the held marker was not drawn').toBeGreaterThan(0)
    expect(held[0].x).toBeGreaterThan(standing[0].x)
  })

  it('opens the diff on the second pick, and asks for no day on the first read', async () => {
    const asked: Array<[string, string, number | undefined]> = []
    const host = await mount(
      createElement(Tree, {
        runId: 'run-1',
        reader: async () => aDeepLineage(),
        switcher: async () => ({ refusal: '', rate: 1 }),
        diffReader: async (left: string, right: string, day?: number) => {
          asked.push([left, right, day])
          return diffWire()
        },
      }),
    )

    const canvas = host.querySelector('canvas.universe__tree') as HTMLCanvasElement
    canvas.getBoundingClientRect = () =>
      ({ left: 0, top: 0, width: canvas.width, height: canvas.height }) as DOMRect
    const placements = layoutTimelines(aDeepLineage().nodes)
    const click = async (runId: string) => {
      const place = placements.find((entry) => entry.runId === runId)
      await act(async () => {
        canvas.dispatchEvent(
          new MouseEvent('click', {
            bubbles: true,
            clientX: (place?.x ?? 0) + 4,
            clientY: (place?.y ?? 0) + 4,
          }),
        )
      })
    }

    await click('run-1')
    expect(host.querySelector('.diff'), 'a diff opened on one pick').toBeNull()

    await act(async () => {
      ;(host.querySelector('.universe__diff') as HTMLButtonElement).click()
    })
    expect(host.querySelector('.universe__pinned')?.textContent).toContain('Pick the timeline')
    expect(asked, 'a diff was asked for before the second pick').toEqual([])

    await click('run-1-aaa')

    // The day is deliberately absent: how far each timeline got is the route's fact, and a guess
    // here would take a refusal it could have avoided.
    expect(asked).toEqual([['run-1', 'run-1-aaa', undefined]])
    expect(host.querySelector('.diff')).not.toBeNull()
    expect(host.querySelector('.universe__pinned'), 'the hold survived the pick').toBeNull()
  })

  it('will not offer a diff in a lineage of one', async () => {
    const host = await mount(
      createElement(Tree, {
        runId: 'run-1',
        reader: async () => lineage([node({ run_id: 'run-1' })]),
        switcher: async () => ({ refusal: '', rate: 1 }),
      }),
    )

    const canvas = host.querySelector('canvas.universe__tree') as HTMLCanvasElement
    canvas.getBoundingClientRect = () =>
      ({ left: 0, top: 0, width: canvas.width, height: canvas.height }) as DOMRect
    await act(async () => {
      canvas.dispatchEvent(new MouseEvent('click', { bubbles: true, clientX: 4, clientY: 4 }))
    })

    const button = host.querySelector('.universe__diff') as HTMLButtonElement
    expect(button.disabled).toBe(true)
    expect(button.textContent).toContain('Nothing to diff it against')
  })
})

describe('the diff surface', () => {
  const pair = { left: 'run-1', right: 'run-1-aaa' }

  // The panel names the work a decision belongs to out of *this* run's genesis, which is every
  // timeline in the lineage's genesis too. Emptied first, so the two cases below — a title
  // resolved and a title that cannot be — are pinned rather than inherited from whichever test
  // last applied a frame.
  beforeEach(() => {
    act(() => {
      useRunStore.getState().reset()
    })
  })

  it('renders metric by metric with the separating decision named', async () => {
    const host = await mount(
      createElement(Diff, { pair, onClose: () => {}, reader: async () => diffWire() }),
    )

    expect(host.querySelector('.diff__title')?.textContent).toContain('day 4')
    // No genesis yet, so the item falls back to its id — which is what the tree shows beside it.
    expect(host.querySelector('.diff__separated')?.textContent).toContain('wi_ap_map')
    expect(host.querySelector('.diff__separated')?.textContent).toContain('Paper is the record')

    const rows = [...host.querySelectorAll('.diff__row')]
    expect(rows.map((row) => row.getAttribute('data-metric'))).toEqual([
      'cash',
      'manualHours',
      'runway',
    ])

    // Both columns and the difference, on every row.
    const cash = rows[0]
    expect(cash.querySelector('[data-figure="cash:left"]')?.textContent).toContain('4695')
    expect(cash.querySelector('[data-figure="cash:right"]')?.textContent).toContain('4600')
    expect(cash.querySelector('[data-figure="cash:delta"]')?.textContent).toContain('-95')
    expect(cash.querySelector('.diff__delta')?.getAttribute('data-direction')).toBe('unfavourable')

    // And the runway, which has no stated direction, is flat rather than called a regression.
    expect(rows[2].querySelector('.diff__delta')?.getAttribute('data-direction')).toBe('flat')
  })

  it('names the work a decision belongs to once the run has said what it is', async () => {
    const { genesisFrame } = await import('./helpers/frames')
    act(() => {
      useRunStore.getState().apply(genesisFrame())
    })

    const host = await mount(
      createElement(Diff, { pair, onClose: () => {}, reader: async () => diffWire() }),
    )

    const named = host.querySelector('.diff__separated')?.textContent ?? ''
    expect(named).not.toContain('wi_ap_map')
    expect(named).toContain('Map how accounts payable actually works')
  })

  it('marks every figure as authored tuning, and would fail if one were not', async () => {
    // Covers M52, by the same sweep shape that guards the HUD: every figure is found by its own
    // attribute and required to carry exactly one marking, with a smuggled figure proving the
    // sweep can fail.
    const host = await mount(
      createElement(Diff, { pair, onClose: () => {}, reader: async () => diffWire() }),
    )

    const figures = [...host.querySelectorAll('[data-figure]')]
    expect(figures.length, 'three rows, two sides and a difference each').toBe(9)
    for (const figure of figures) {
      const name = figure.getAttribute('data-figure')
      expect(figure.querySelector('[data-authored-tuning]'), `${name} is unmarked`).not.toBeNull()
      expect(
        figure.querySelector('[data-measured]'),
        `${name} claims to be measured, and no figure in a diff is`,
      ).toBeNull()
    }

    const smuggled = document.createElement('span')
    smuggled.setAttribute('data-figure', 'smuggled')
    host.querySelector('.diff__rows')?.appendChild(smuggled)
    const unmarked = [...host.querySelectorAll('[data-figure]')].filter(
      (figure) => figure.querySelector('[data-authored-tuning]') === null,
    )
    expect(unmarked.map((figure) => figure.getAttribute('data-figure'))).toEqual(['smuggled'])
  })

  it('renders a refusal where the figures would have been', async () => {
    // The whole point of refusing: a reason a player can read, in the place they were looking.
    const host = await mount(
      createElement(Diff, {
        pair,
        onClose: () => {},
        reader: async () =>
          diffWire({
            rows: [],
            left: null,
            right: null,
            day: 9,
            max_day: 4,
            refusal: 'run-1-aaa has reached day 4, so there is no day 9 in it to compare.',
          }),
      }),
    )

    expect(host.querySelector('.diff')?.getAttribute('data-refused')).toBe('true')
    expect(host.querySelector('.diff__refusal')?.textContent).toContain('no day 9')
    expect(host.querySelectorAll('.diff__row')).toHaveLength(0)
    expect(host.querySelectorAll('[data-figure]')).toHaveLength(0)
  })

  it('offers no day control when the two share no range to move inside', async () => {
    // Two runs of different Universes. `max_day` comes back as zero, and a control reading
    // "day 0" under a refusal saying they share no Genesis is furniture pretending to work.
    const host = await mount(
      createElement(Diff, {
        pair,
        onClose: () => {},
        reader: async () =>
          diffWire({
            day: 0,
            max_day: 0,
            rows: [],
            left: null,
            right: null,
            separation: null,
            refusal: 'run-elsewhere is not a timeline of this Universe, whose root is run-1.',
          }),
      }),
    )

    expect(host.querySelector<HTMLElement>('.diff__day')?.hidden).toBe(true)
    expect(host.querySelector('.diff__refusal')?.textContent).toContain('not a timeline')
    expect(host.querySelector('.diff__separated')?.textContent).toContain('not in the log')
  })

  it('moves the day within the bound the answer stated, and re-reads at it', async () => {
    const asked: Array<number | undefined> = []
    const host = await mount(
      createElement(Diff, {
        pair,
        onClose: () => {},
        reader: async (_left: string, _right: string, day?: number) => {
          asked.push(day)
          return diffWire({ day: day ?? 4 })
        },
      }),
    )

    expect(asked).toEqual([undefined])

    await act(async () => {
      ;(host.querySelector('[data-step="back"]') as HTMLButtonElement).click()
    })
    expect(asked).toEqual([undefined, 3])

    // The forward step at the bound is unreachable rather than refused, which is what turns the
    // not-yet-reached case into a state of the control instead of an error to walk into.
    await act(async () => {
      ;(host.querySelector('[data-step="forward"]') as HTMLButtonElement).click()
    })
    expect(asked).toEqual([undefined, 3, 4])
    expect((host.querySelector('[data-step="forward"]') as HTMLButtonElement).disabled).toBe(true)
    expect((host.querySelector('[data-step="back"]') as HTMLButtonElement).disabled).toBe(false)
  })

  it('says why the diff could not be read', async () => {
    const host = await mount(
      createElement(Diff, {
        pair,
        onClose: () => {},
        reader: async () => {
          throw new LineageUnavailable(404, 'no run run-1')
        },
      }),
    )

    expect(host.querySelector('.diff__error')?.textContent).toContain('no run run-1')
    expect(host.querySelector('.diff__close')).not.toBeNull()
  })
})

describe('the call the diff makes', () => {
  it('asks the mounted report, encoding both run ids and the day', async () => {
    const seen: string[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => {
        seen.push(url)
        return new Response(JSON.stringify(diffWire()), { status: 200 })
      }),
    )

    await fetchDiff('run 1', 'run/2')
    await fetchDiff('run-1', 'run-2', 3)

    // `/report` is inside the path because the diff is answered by the report app the launcher
    // mounts, not by the gateway — the one call in this client with a service prefix in it.
    expect(seen).toEqual([
      '/api/report/runs/run%201/diff/run%2F2',
      '/api/report/runs/run-1/diff/run-2?day=3',
    ])
  })

  it('reports a not-found as the failure the tree\'s other reads already throw', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(JSON.stringify({ detail: 'no run run-9' }), { status: 404 })),
    )

    await expect(fetchDiff('run-9', 'run-1')).rejects.toThrow(LineageUnavailable)
  })
})

describe('the diff reads down a column', () => {
  /**
   * The one defect in this unit that was live and that nothing above could see.
   *
   * The headings were a flex row of two equal cards and the figure rows were a four-column grid
   * of their own, so the two never had to agree. **Measured in the browser at 1568px: the left
   * timeline's card spanned x 40 to 358 while its own figures sat at 462 to 532 — entirely
   * underneath the *right* timeline's card.** Every number was read under the other timeline's
   * name, which is the worst thing this surface can do, and every assertion in this file passed
   * because they are all about which figure carries which attribute rather than where it lands.
   *
   * jsdom computes no layout, so neither half of this is a pixel check. What is checkable is
   * what made the drift possible — two grids each free to declare its own columns — and what
   * would make it possible again: a heading and a figure in different cells of one template.
   */
  const CSS = readFileSync(
    // Resolved from this file rather than from `new URL(..., import.meta.url)`, which the other
    // source-reading suites use: those run under the node environment and this one runs under
    // jsdom, where `import.meta.url` is an http URL and `fileURLToPath` refuses it.
    resolve(dirname(fileURLToPath(import.meta.url)), '../src/ui/shell.css'),
    'utf8',
  ).replace(/\/\*[\s\S]*?\*\//g, '')

  function block(rule: string): string {
    const found = new RegExp(`\\${rule}\\s*\\{([^}]*)\\}`).exec(CSS)
    expect(found, `${rule} has no rule in shell.css`).not.toBeNull()
    return found?.[1] ?? ''
  }

  it('drives the heading row and every figure row from one column template', () => {
    expect(CSS, 'the shared column template is gone').toContain('--diff-columns:')
    for (const rule of ['.diff__columns', '.diff__row']) {
      expect(block(rule), `${rule} declares columns of its own`).toContain(
        'grid-template-columns: var(--diff-columns)',
      )
    }
  })

  it('puts each side in the same cell of that template as its own figures', async () => {
    const host = await mount(
      createElement(Diff, {
        pair: { left: 'run-1', right: 'run-1-aaa' },
        onClose: () => {},
        reader: async () => diffWire(),
      }),
    )

    // One grid template, so a cell index is a column. The heading in cell 1 has to be the
    // timeline whose figures are in cell 1 of every row below it.
    const headings = [...(host.querySelector('.diff__columns')?.children ?? [])]
    expect(headings).toHaveLength(4)
    expect(headings[1].getAttribute('data-side')).toBe('left')
    expect(headings[2].getAttribute('data-side')).toBe('right')

    for (const row of host.querySelectorAll('.diff__row')) {
      const key = row.getAttribute('data-metric')
      const cells = [...row.children]
      expect(cells, `${key} does not fill the template`).toHaveLength(4)
      expect(cells[1].getAttribute('data-figure')).toBe(`${key}:left`)
      expect(cells[2].getAttribute('data-figure')).toBe(`${key}:right`)
      expect(cells[3].getAttribute('data-figure')).toBe(`${key}:delta`)
    }
  })
})
