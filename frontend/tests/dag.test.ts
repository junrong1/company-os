import { act, createElement } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { AUTHORED_TUNING, PAL, RESERVED_BEAM, TACIT, loadColour } from '../src/design/tokens'
import { FONT, paintText } from '../src/design/text'
import { SEVERED_FRACTION, buildModel, drawGraph, drawPolyline } from '../src/dag/draw'
import { stripOrder, stripWidth } from '../src/dag/strip'
import {
  CyclicGraph,
  canvasSize,
  layerOf,
  layout,
  pointAt,
  route,
  runLength,
} from '../src/dag/layout'
import { GRID, NODE_COLS, NODE_ROWS, PIP_WIDTH, STATUS, drawNode, drawPip } from '../src/dag/nodes'
import { resetSheetCounter, sheetsGenerated } from '../src/render/index'
import { type ItemStatus, useRunStore } from '../src/net/store'
import { Shell } from '../src/ui/Shell'
import {
  RecordingContext,
  catalogFixture,
  genesisFrame,
  itemFrame,
  loadFrame,
} from './helpers/frames'

/**
 * The DAG's encoding and layout.
 *
 * Every drawing assertion here reads *draw calls* rather than pixels. jsdom does not implement
 * `getContext`, and standing up a real canvas to check that a blocked node drew violet would
 * test the canvas implementation instead of the encoding. So each draw path takes its context
 * as a parameter and the suite hands it a recorder — which is the only way to state the
 * properties the art direction actually cares about: which colour, on which border form, and
 * whether the distinction survives with hue stripped out.
 */

const CEILING = 1000
const ALL_STATUSES: ItemStatus[] = ['backlog', 'assigned', 'active', 'blocked', 'done']

afterEach(() => {
  useRunStore.getState().reset()
})

function baseNode(status: ItemStatus, loadPermille = 400) {
  return {
    id: 'wi_ap_map',
    label: 'AP PROCESS MAP',
    status,
    dept: 'accounting',
    loadPermille,
    ceiling: CEILING,
    gx: 1,
    gy: 1,
  }
}

// =========================================================================
// Layout stability
// =========================================================================

describe('layout', () => {
  it('assigns layers by longest-path depth so every edge points forward', () => {
    const layers = layerOf([
      { id: 'a', requires: [] },
      { id: 'b', requires: ['a'] },
      { id: 'c', requires: ['a'] },
      // `d` waits on both a root and a depth-1 node. Shortest-path would put it at 1 and draw
      // the edge from `b` running backwards into its own layer.
      { id: 'd', requires: ['a', 'b'] },
    ])

    expect(layers).toEqual({ a: 0, b: 1, c: 1, d: 2 })
  })

  it('orders within a layer by item id, not by input order', () => {
    const forwards = layout([
      { id: 'wi_a', requires: [] },
      { id: 'wi_b', requires: [] },
      { id: 'wi_c', requires: [] },
    ])
    const backwards = layout([
      { id: 'wi_c', requires: [] },
      { id: 'wi_b', requires: [] },
      { id: 'wi_a', requires: [] },
    ])

    expect(forwards).toEqual(backwards)
    expect(forwards.map((placement) => placement.id)).toEqual(['wi_a', 'wi_b', 'wi_c'])
  })

  it('does not change any existing node when an item unlocks', () => {
    // The property the whole layout exists for. Unlocking changes availability and status —
    // neither of which is an input here — so the placements have to be identical, not merely
    // similar. Asserted against the real authored graph rather than a toy one.
    const catalog = catalogFixture()

    // Everything unlocks and moves: each item takes a different one of the five states, which
    // in the store is exactly what an unlock cascade looks like.
    const backlog = Object.fromEntries(
      catalog.map((entry) => [entry.id, { status: 'backlog' as ItemStatus }]),
    )
    const moved = Object.fromEntries(
      catalog.map((entry, index) => [
        entry.id,
        { status: ALL_STATUSES[index % ALL_STATUSES.length] },
      ]),
    )

    const beforeModel = buildModel(catalog, backlog, {}, CEILING)
    const afterModel = buildModel(catalog, moved, { dir_admin: 900 }, CEILING)

    expect(afterModel.placements).toEqual(beforeModel.placements)
    for (const placement of afterModel.placements) {
      const original = beforeModel.placements.find((entry) => entry.id === placement.id)
      expect(placement.layer).toBe(original?.layer)
      expect(placement.row).toBe(original?.row)
      expect(placement.gx).toBe(original?.gx)
      expect(placement.gy).toBe(original?.gy)
    }
  })

  it('names a cycle rather than overflowing the stack', () => {
    expect(() =>
      layerOf([
        { id: 'a', requires: ['b'] },
        { id: 'b', requires: ['a'] },
      ]),
    ).toThrow(CyclicGraph)
  })

  it('ignores an edge naming an item the catalog does not carry', () => {
    // The catalog is the authority on what exists; a dangling requirement should not sink the
    // whole view.
    expect(layerOf([{ id: 'a', requires: ['ghost'] }])).toEqual({ a: 0 })
  })

  it('routes orthogonally, on the grid, never curved', () => {
    const [from, to] = layout([
      { id: 'a', requires: [] },
      { id: 'b', requires: ['a'] },
    ])

    const points = route(from, to)

    expect(points).toHaveLength(4)
    for (let i = 1; i < points.length; i += 1) {
      const horizontal = points[i][1] === points[i - 1][1]
      const vertical = points[i][0] === points[i - 1][0]
      // Every leg is axis-aligned. A diagonal would mean a curve was approximated.
      expect(horizontal || vertical).toBe(true)
    }
    // The vertical leg sits on a lattice line, so nearest-neighbour scaling keeps it a clean
    // trace rather than a two-pixel smear.
    expect(points[1][0] % GRID).toBe(0)
    expect(runLength(points)).toBeGreaterThan(0)
  })

  it('sizes the canvas to hold every node', () => {
    const placements = layout(catalogFixture().map((e) => ({ id: e.id, requires: e.requires.items })))
    const size = canvasSize(placements)

    for (const placement of placements) {
      // The node's whole footprint, not just its origin. Checking the origin alone would pass for
      // a canvas that clipped every node's right and bottom edges off.
      expect((placement.gx + NODE_COLS) * GRID).toBeLessThanOrEqual(size.width)
      expect((placement.gy + NODE_ROWS) * GRID).toBeLessThanOrEqual(size.height)
    }
  })
})

// =========================================================================
// Node encoding
// =========================================================================

describe('node encoding', () => {
  it('renders a blocked node violet, never amber', () => {
    const context = new RecordingContext()
    drawNode(context, baseNode('blocked'))

    expect(context.colours()).toContain(TACIT)
    expect(context.colours()).not.toContain(RESERVED_BEAM)
  })

  it('never renders any status in the reserved amber', () => {
    // Amber belongs to a *person* waiting on the CEO, never to a work item. Checked across
    // every state and at both sizes, because "the one we thought about" is not the property.
    for (const status of ALL_STATUSES) {
      const node = new RecordingContext()
      drawNode(node, baseNode(status))
      expect(node.colours()).not.toContain(RESERVED_BEAM)

      const pip = new RecordingContext()
      drawPip(pip, 0, 0, { status, dept: 'accounting' })
      expect(pip.colours()).not.toContain(RESERVED_BEAM)
    }
  })

  it('marks the owning department load signal as authored tuning (R27)', () => {
    // The DAG renders one number, and R27 admits no exceptions. Checked by painting the string
    // the node should have drawn into a second recorder and asserting every one of its pixels
    // is present in the node's own draw calls — which proves the *glyph* landed rather than
    // proving a string was concatenated somewhere.
    const permille = 400
    const node = new RecordingContext()
    drawNode(node, baseNode('active', permille))

    const percent = Math.round((permille * 100) / CEILING)
    const colour = PAL.jingyuhui
    const originX = 1 * GRID + 10
    const originY = 1 * GRID + 18

    const expected = new RecordingContext()
    paintText(expected, `LOAD ${percent}% ${AUTHORED_TUNING.glyph}`, originX, originY, 1, colour)

    const drawn = new Set(node.geometry().split(';'))
    for (const rect of expected.rects) {
      expect(drawn.has(`${rect.x},${rect.y},${rect.width},${rect.height}`)).toBe(true)
    }

    // The marking has to be *ink*, not an advance. `paintText` skips an unmapped character by
    // advancing the cursor, so a glyph missing from the face would draw nothing at all and the
    // subset check above would still pass.
    expect(FONT[AUTHORED_TUNING.glyph]).toBeTruthy()
    const unmarked = new RecordingContext()
    paintText(unmarked, `LOAD ${percent}%`, originX, originY, 1, colour)
    expect(expected.rects.length).toBeGreaterThan(unmarked.rects.length)
  })

  it('marks the load signal without reaching for the reserved beam', () => {
    for (const permille of [0, 400, 1000, 1400]) {
      const context = new RecordingContext()
      drawNode(context, baseNode('active', permille))
      expect(context.colours()).not.toContain(RESERVED_BEAM)
    }
  })

  it('keeps the five states distinguishable with every status hue flattened to one value', () => {
    // The greyscale test. Status is carried by border *form* plus a glyph precisely so that hue
    // can be removed without collapsing two states together — a node already spends colour on
    // the department stripe and the load bar, and a third coding would be unreadable.
    const geometries = new Map<string, ItemStatus>()

    for (const status of ALL_STATUSES) {
      const context = new RecordingContext()
      drawNode(context, baseNode(status))
      const signature = context.geometry()

      const clash = geometries.get(signature)
      expect(clash, `${status} and ${clash} are identical once hue is removed`).toBeUndefined()
      geometries.set(signature, status)
    }

    expect(geometries.size).toBe(ALL_STATUSES.length)
  })

  it('keeps them distinguishable at pip scale too', () => {
    // The strip is why the encoding had to survive shrinking: dotted thins to corner ticks and
    // broken keeps its gap, so all five states hold at 1px stroke.
    const geometries = new Set<string>()

    for (const status of ALL_STATUSES) {
      const context = new RecordingContext()
      drawPip(context, 0, 0, { status, dept: 'accounting' })
      geometries.add(context.geometry())
    }

    expect(geometries.size).toBe(ALL_STATUSES.length)
  })

  it('shows the owning department load on the same ramp the office uses', () => {
    // The same function the HUD's capacity heat calls, reading the same ceiling off the wire.
    // Two ramps would be two things to keep in agreement, and they would stop agreeing.
    for (const permille of [0, 400, 900, 1000, 1120, 1400]) {
      const context = new RecordingContext()
      drawNode(context, baseNode('active', permille))

      expect(context.colours()).toContain(loadColour(permille, CEILING))
    }
  })

  it('reads the load of the reporting line, not of the room', () => {
    // Priya sits in Accounting and reports to Admin. The deliberate mismatch in the sample data
    // is the reason the node's load comes from `director` rather than from `dept`.
    const catalog = catalogFixture()
    const priyas = catalog.filter((entry) => entry.want === 'stf_ap')
    expect(priyas.length).toBeGreaterThan(0)

    const model = buildModel(
      catalog,
      {},
      { dir_admin: 1200, dir_sales: 100 },
      CEILING,
    )

    for (const entry of priyas) {
      expect(entry.director).toBe('dir_admin')
      expect(model.nodes[entry.id].loadPermille).toBe(1200)
    }
  })

  it('marks the load figure red past the ceiling', () => {
    const over = new RecordingContext()
    drawNode(over, baseNode('active', 1200))
    expect(over.colours()).toContain(PAL.zhuhong)

    const under = new RecordingContext()
    drawNode(under, baseNode('active', 400))
    expect(under.inColour(PAL.zhuhong)).toHaveLength(0)
  })
})

// =========================================================================
// Edges
// =========================================================================

function twoNodeModel(sourceStatus: ItemStatus) {
  return buildModel(
    [
      {
        id: 'wi_a',
        title: 'UPSTREAM',
        dept: 'accounting',
        director: 'dir_admin',
        requires: { items: [] },
      },
      {
        id: 'wi_b',
        title: 'DOWNSTREAM',
        dept: 'sales',
        director: 'dir_sales',
        requires: { items: ['wi_a'] },
      },
    ],
    { wi_a: { status: sourceStatus }, wi_b: { status: 'backlog' } },
    { dir_admin: 400, dir_sales: 300 },
    CEILING,
  )
}

describe('edges', () => {
  it('derives appearance from the source node, so the graph shows where work can flow', () => {
    const delivered = new RecordingContext()
    drawGraph(delivered, twoNodeModel('done'), 1, 400, 200)
    // Solid, drawn in the accent — upstream delivered.
    expect(delivered.colours()).toContain(PAL.shilv)

    const open = new RecordingContext()
    drawGraph(open, twoNodeModel('active'), 1, 400, 200)
    // Dashed, in wall grey — upstream is still open.
    expect(open.colours()).toContain(PAL.jingyuhui)
    expect(open.colours()).not.toContain(TACIT)
  })

  it('severs a blocked node’s outgoing edge and caps it with a perpendicular tick', () => {
    const context = new RecordingContext()
    drawGraph(context, twoNodeModel('blocked'), 1, 400, 200)

    expect(context.colours()).toContain(TACIT)
    expect(context.colours()).not.toContain(RESERVED_BEAM)

    const violet = context.inColour(TACIT)
    expect(violet.length).toBeGreaterThan(1)

    // The cap is 12 long and 2 thick on one axis — a tick across the trace, not more trace.
    const cap = violet.find(
      (rect) => (rect.width === 12 && rect.height === 2) || (rect.width === 2 && rect.height === 12),
    )
    expect(cap).toBeDefined()
  })

  it('stops the severed stub short, because the gap is the information', () => {
    const [from, to] = layout([
      { id: 'wi_a', requires: [] },
      { id: 'wi_b', requires: ['wi_a'] },
    ])
    const points = route(from, to)

    const partial = new RecordingContext()
    drawPolyline(partial, points, SEVERED_FRACTION, TACIT, 2, 0)
    const whole = new RecordingContext()
    drawPolyline(whole, points, 1, TACIT, 2, 0)

    const drawn = (context: RecordingContext) =>
      context.rects.reduce((total, rect) => total + Math.max(rect.width, rect.height), 0)

    expect(drawn(partial)).toBeLessThan(drawn(whole))
    expect(SEVERED_FRACTION).toBeLessThan(1)
  })

  it('places the cap perpendicular to the leg it lands on', () => {
    // A tick drawn *along* the leg would read as more edge rather than as a stop, so the
    // orientation has to be right in both cases — and which case you get depends only on where
    // the two nodes sit, which is why both are pinned here rather than one.

    // Same row: the stub is still on the outgoing horizontal leg.
    const sameRow = route(
      { id: 'a', layer: 0, row: 0, gx: 1, gy: 1 },
      { id: 'b', layer: 1, row: 0, gx: 16, gy: 1 },
    )
    const flat = pointAt(sameRow, SEVERED_FRACTION)
    expect(flat.vertical).toBe(false)
    expect(flat.y).toBe(48)

    // Rows far apart: the vertical leg dominates the run, so the stub lands on it.
    const farRow = route(
      { id: 'a', layer: 0, row: 0, gx: 1, gy: 1 },
      { id: 'b', layer: 1, row: 3, gx: 16, gy: 19 },
    )
    const upright = pointAt(farRow, SEVERED_FRACTION)
    expect(upright.vertical).toBe(true)
    expect(upright.x).toBe(224)
  })

  it('animates each transition rather than snapping the graph on', () => {
    // The unlock cascade is the only animation in the product that carries information: the
    // edge draws forward, then the downstream node lights. Partway through, strictly less is
    // drawn than at the end.
    const model = twoNodeModel('done')

    const early = new RecordingContext()
    drawGraph(early, model, 0.05, 400, 200)
    const late = new RecordingContext()
    drawGraph(late, model, 1, 400, 200)

    const accentDrawn = (context: RecordingContext) =>
      context.inColour(PAL.shilv).reduce((total, r) => total + Math.max(r.width, r.height), 0)

    expect(accentDrawn(early)).toBeLessThan(accentDrawn(late))
  })
})

// =========================================================================
// The chain strip
// =========================================================================

describe('the chain strip', () => {
  it('orders pips exactly as the graph orders nodes', () => {
    // Same rule — layer first, then item id — so a pip and its node hold the same relative
    // position. Two orders would make the strip a separate thing to learn.
    const catalog = catalogFixture()
    const entries = stripOrder(catalog, {})
    const placements = layout(catalog.map((e) => ({ id: e.id, requires: e.requires.items })))

    expect(entries.map((entry) => entry.id)).toEqual(placements.map((p) => p.id))
  })

  it('reflects a transition under the same encoding as the full node', () => {
    const catalog = catalogFixture()
    const target = catalog[0].id

    const blocked = stripOrder(catalog, { [target]: { status: 'blocked' } })
    expect(blocked.find((entry) => entry.id === target)?.status).toBe('blocked')

    const pip = new RecordingContext()
    drawPip(pip, 0, 0, { status: 'blocked', dept: 'accounting' })
    const node = new RecordingContext()
    drawNode(node, baseNode('blocked'))

    // The same hue and the same border form, from the same table. Not a lookalike.
    expect(pip.colours()).toContain(STATUS.blocked.hue)
    expect(node.colours()).toContain(STATUS.blocked.hue)
    expect(STATUS.blocked.border).toBe('broken')
  })

  it('sizes itself to the number of items', () => {
    expect(stripWidth(0)).toBeGreaterThan(0)
    expect(stripWidth(8)).toBeGreaterThan(8 * PIP_WIDTH)
    expect(stripWidth(9)).toBeGreaterThan(stripWidth(8))
  })
})

// =========================================================================
// The store's projection, and the stage toggle
// =========================================================================

describe('an item moving through all five statuses', () => {
  it('updates its node within one event', () => {
    const store = useRunStore.getState()
    store.apply(genesisFrame())

    const item = catalogFixture()[0].id
    let seq = 10

    const journey: Array<[string, ItemStatus]> = [
      ['WORK_ASSIGNED', 'assigned'],
      ['WORK_ASSIGNED', 'active'],
      ['CHECKPOINT_RAISED', 'blocked'],
      ['DECISION_RESOLVED', 'active'],
      ['DELIVERABLE_PRODUCED', 'done'],
      ['WORK_RETURNED_TO_BACKLOG', 'backlog'],
    ]

    for (const [kind, status] of journey) {
      seq += 1
      useRunStore.getState().apply(itemFrame({ seq, kind, item, status }))

      // One event, and the node is already there. The client reads `item_status` off the wire
      // rather than keeping its own table of what each event kind does to work — which would be
      // a third reimplementation of the fold, disagreeing with the kernel while passing.
      expect(useRunStore.getState().items[item].status).toBe(status)

      const model = buildModel(
        catalogFixture(),
        useRunStore.getState().items,
        useRunStore.getState().load,
        CEILING,
      )
      expect(model.nodes[item].status).toBe(status)
    }
  })

  it('shows the same transition in the strip while the stage renders the office', () => {
    // The property that makes a full-stage toggle safe: blocked work is visible in both places,
    // so you never need to open the DAG to learn that something stopped.
    const store = useRunStore.getState()
    store.apply(genesisFrame())

    const item = catalogFixture()[0].id
    useRunStore
      .getState()
      .apply(itemFrame({ seq: 11, kind: 'CHECKPOINT_RAISED', item, status: 'blocked' }))

    const entries = stripOrder(catalogFixture(), useRunStore.getState().items)
    const pip = entries.find((entry) => entry.id === item)
    expect(pip?.status).toBe('blocked')

    const context = new RecordingContext()
    drawPip(context, 0, 0, { status: pip!.status, dept: pip!.dept })
    expect(context.colours()).toContain(TACIT)
    expect(context.colours()).not.toContain(RESERVED_BEAM)
  })

  it('carries the department load onto every node from one load event', () => {
    const store = useRunStore.getState()
    store.apply(genesisFrame())
    useRunStore
      .getState()
      .apply(loadFrame({ seq: 20, load: { dir_admin: 1150, dir_sales: 300, dir_cs: 200, dir_hr: 100 } }))

    const model = buildModel(
      catalogFixture(),
      useRunStore.getState().items,
      useRunStore.getState().load,
      CEILING,
    )

    const adminNodes = Object.values(model.nodes).filter((node) => node.loadPermille === 1150)
    expect(adminNodes.length).toBeGreaterThan(0)
    for (const node of adminNodes) {
      const context = new RecordingContext()
      drawNode(context, node)
      expect(context.colours()).toContain(loadColour(1150, CEILING))
    }
  })
})

describe('the stage toggle', () => {
  let host: HTMLDivElement
  let root: ReturnType<typeof createRoot> | null = null

  beforeEach(() => {
    host = document.createElement('div')
    document.body.appendChild(host)
    resetSheetCounter()
  })

  afterEach(() => {
    act(() => root?.unmount())
    root = null
    host.remove()
  })

  it('leaves the office rendering unchanged after a round trip', () => {
    const stream = { start: () => {}, stop: () => {} }

    act(() => {
      root = createRoot(host)
      root.render(
        createElement(Shell, { runId: 'run-1', makeStream: () => stream }),
      )
    })

    act(() => {
      useRunStore.getState().apply(genesisFrame())
    })

    const office = host.querySelector('canvas.office')
    expect(office).not.toBeNull()

    const sheetsAtStart = sheetsGenerated()
    // Asserted, or the comparison below would hold just as well for a renderer that never
    // started — which is the way this test would rot into passing for the wrong reason.
    expect(sheetsAtStart).toBeGreaterThan(0)

    const toDag = host.querySelectorAll('.stage-toggle button')[1] as HTMLButtonElement
    const toOffice = host.querySelectorAll('.stage-toggle button')[0] as HTMLButtonElement

    act(() => toDag.click())
    expect(host.querySelector('main')?.dataset.stage).toBe('dag')
    // Hidden, not unmounted. The same canvas element, so the sprite sheets and the frame loop
    // survived the trip.
    expect(host.querySelector('canvas.office')).toBe(office)

    act(() => toOffice.click())
    expect(host.querySelector('main')?.dataset.stage).toBe('office')
    expect(host.querySelector('canvas.office')).toBe(office)

    // Nothing was rebuilt: a fresh renderer would have generated another sheet set, which is
    // exactly the leak the lifecycle work in U12 exists to prevent.
    expect(sheetsGenerated()).toBe(sheetsAtStart)
  })
})
