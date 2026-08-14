/**
 * Layout: circuit board, not flowchart.
 *
 * The failure mode for this view is a default graph library — bezier curves, drop shadows,
 * rounded pills, a force-directed layout that reshuffles every time the data changes. Drawn
 * instead on the same 16px grid as the office, with orthogonally routed pixel-stepped edges,
 * it belongs to the same world.
 *
 * **Layers come from longest-path depth, and order within a layer from the item id.** Both
 * halves matter, and both are about the same thing: an unlock must not reshuffle the graph.
 * Longest-path depth is stable under a status change because it depends only on the dependency
 * edges, which are authored and immutable. Ordering by item id is stable because ids never
 * change — ordering by anything derived (status, assignee, progress) would make the graph
 * rearrange itself while the CEO was reading it, which is exactly when they are trying to
 * hold a shape in their head.
 *
 * The alternative — shortest-path depth — would place a node one layer after its *earliest*
 * prerequisite rather than its latest, which draws edges running backwards through the layer
 * they came from. Longest-path guarantees every edge points forward.
 */

import { GRID, NODE_COLS, NODE_ROWS } from './nodes'

/** The dependency graph, as the genesis catalog carries it. */
export interface GraphEntry {
  id: string
  /** Ids this item requires. Authored, immutable, and the only input to layering. */
  requires: string[]
}

export interface Placement {
  id: string
  /** Longest-path depth from a root. */
  layer: number
  /** Position within the layer, ascending by item id. */
  row: number
  gx: number
  gy: number
}

/** Horizontal gap between layers, in grid units. */
export const LAYER_GAP_COLS = 4

/** Vertical gap between rows, in grid units. */
export const ROW_GAP_ROWS = 2

export class CyclicGraph extends Error {
  constructor(remaining: string[]) {
    super(
      `the work graph has a cycle among ${remaining.join(', ')}: every item in it waits on ` +
        `another, so none of them can ever start`,
    )
    this.name = 'CyclicGraph'
  }
}

/**
 * Longest-path depth for every node.
 *
 * Computed by repeated relaxation over a topological order rather than by recursion: the graph
 * is authored and small, and a cycle in authored data should produce a named error rather than
 * a stack overflow. An edge naming an unknown id is ignored — the catalog is the authority on
 * what exists, and a dangling requirement should not sink the whole view.
 */
export function layerOf(entries: readonly GraphEntry[]): Record<string, number> {
  const known = new Set(entries.map((entry) => entry.id))
  const requires = new Map<string, string[]>()
  for (const entry of entries) {
    requires.set(
      entry.id,
      entry.requires.filter((id) => known.has(id)),
    )
  }

  const layers: Record<string, number> = {}
  let remaining = entries.map((entry) => entry.id)

  while (remaining.length > 0) {
    const ready = remaining.filter((id) =>
      (requires.get(id) ?? []).every((dependency) => dependency in layers),
    )
    if (ready.length === 0) throw new CyclicGraph(remaining.slice().sort())

    for (const id of ready) {
      const dependencies = requires.get(id) ?? []
      // The *longest* path: one past the deepest prerequisite, so every edge points forward.
      layers[id] = dependencies.reduce(
        (deepest, dependency) => Math.max(deepest, layers[dependency] + 1),
        0,
      )
    }
    remaining = remaining.filter((id) => !ready.includes(id))
  }

  return layers
}

/**
 * Place every node on the grid.
 *
 * Deterministic in both axes and dependent only on the ids and the authored edges, which is
 * what makes the "unlocking changes no existing node's layer or order" property hold by
 * construction rather than by test.
 */
export function layout(entries: readonly GraphEntry[]): Placement[] {
  const layers = layerOf(entries)

  const byLayer = new Map<number, string[]>()
  for (const entry of entries) {
    const layer = layers[entry.id]
    const bucket = byLayer.get(layer) ?? []
    bucket.push(entry.id)
    byLayer.set(layer, bucket)
  }

  const placements: Placement[] = []
  for (const [layer, ids] of byLayer) {
    // Sorted by id, so the order is a property of the data and not of iteration order.
    const ordered = ids.slice().sort()
    ordered.forEach((id, row) => {
      placements.push({
        id,
        layer,
        row,
        gx: 1 + layer * (NODE_COLS + LAYER_GAP_COLS),
        gy: 1 + row * (NODE_ROWS + ROW_GAP_ROWS),
      })
    })
  }

  return placements.sort(
    (left, right) => left.layer - right.layer || left.id.localeCompare(right.id),
  )
}

/** How large a canvas the placements need, in pixels. */
export function canvasSize(placements: readonly Placement[]): { width: number; height: number } {
  let cols = 0
  let rows = 0
  for (const placement of placements) {
    cols = Math.max(cols, placement.gx + NODE_COLS + 1)
    rows = Math.max(rows, placement.gy + NODE_ROWS + 1)
  }
  return { width: cols * GRID, height: rows * GRID }
}

export type Point = readonly [number, number]

/**
 * Route an edge orthogonally: out the right edge, step vertically at the midpoint, in the left.
 *
 * Never curved, and every turn lands on the grid. A bezier would be one line of code and would
 * make the view look like every other graph tool; the orthogonal route is what makes it read as
 * a board with traces on it.
 */
export function route(from: Placement, to: Placement): Point[] {
  const ax = (from.gx + NODE_COLS) * GRID
  const ay = (from.gy + NODE_ROWS / 2) * GRID
  const bx = to.gx * GRID
  const by = (to.gy + NODE_ROWS / 2) * GRID
  // Snapped to the lattice, so the vertical leg sits on a grid line rather than half a pixel
  // off it — which at nearest-neighbour scaling is the difference between a clean trace and a
  // two-pixel-wide smear.
  const mid = Math.round((ax + bx) / 2 / GRID) * GRID
  return [
    [ax, ay],
    [mid, ay],
    [mid, by],
    [bx, by],
  ]
}

/** Total Manhattan length of a route, which is what a partial draw is measured against. */
export function runLength(points: readonly Point[]): number {
  let total = 0
  for (let i = 1; i < points.length; i += 1) {
    total += Math.abs(points[i][0] - points[i - 1][0]) + Math.abs(points[i][1] - points[i - 1][1])
  }
  return total
}

/**
 * The point a fraction along a route, and whether that leg is vertical.
 *
 * The orientation is what lets a severed edge be capped with a *perpendicular* tick: a tick
 * drawn along the leg would read as more edge rather than as a stop.
 */
export function pointAt(
  points: readonly Point[],
  fraction: number,
): { x: number; y: number; vertical: boolean } {
  let budget = runLength(points) * fraction
  for (let i = 1; i < points.length; i += 1) {
    const [x0, y0] = points[i - 1]
    const [x1, y1] = points[i]
    const length = Math.abs(x1 - x0) + Math.abs(y1 - y0)
    const vertical = x0 === x1
    const step = vertical ? (y1 > y0 ? 1 : -1) : x1 > x0 ? 1 : -1
    if (budget <= length) {
      return vertical
        ? { x: x0, y: y0 + step * budget, vertical }
        : { x: x0 + step * budget, y: y0, vertical }
    }
    budget -= length
  }
  const last = points[points.length - 1]
  return { x: last[0], y: last[1], vertical: false }
}
