/**
 * Folding the graph, and drawing it. No React in here.
 *
 * Split from the component so that the component file exports only components — the project's
 * lint config asks for that, and a module exporting both loses fast refresh on the component.
 * It also means the properties the art direction states about this view are properties of
 * functions the suite can call with a recording context, rather than of pixels nobody can
 * assert on.
 *
 * The DAG is the second surface over the same state.
 *
 * Nodes and edges fold from exactly what the office reads — the store's items, the store's
 * per-department load, the catalog's authored edges. Nothing here has its own copy of the run.
 *
 * **Edge appearance derives from the source node's status**, so the graph itself shows where
 * work can flow without opening a node: solid means upstream delivered, dashed means upstream
 * is still open, and a violet stub means a decision is blocking that path.
 *
 * **The unlock cascade is the only animation in the product that carries information.** A
 * deliverable completes, the edge draws forward, the newly unlocked node lights. Everything
 * else that moves here is atmosphere and has been left out. It honours
 * `prefers-reduced-motion` by drawing the end state immediately rather than by disappearing —
 * the information is in the final frame, and only the reveal is motion.
 */

import { PAL, TACIT, ACCENT, withAlpha } from '../design/tokens'
import type { PixelContext } from '../design/text'
import type { ItemStatus } from '../net/store'
import { type Placement, type Point, layout, pointAt, route, runLength } from './layout'
import { GRID, type NodeView, STATUS, drawNode } from './nodes'

/** How long one layer of the cascade takes, and the stagger between layers. */
export const CASCADE_STAGGER_MS = 160

/** The whole cascade, across every layer. */
export const CASCADE_DURATION_MS = 2600

export interface DagModel {
  placements: Placement[]
  nodes: Record<string, NodeView>
  edges: Array<{ from: string; to: string }>
}

/**
 * Fold the model out of store state.
 *
 * A pure function taking plain data, so the suite drives it without a store or a canvas. That
 * is not only convenient: the layout properties this view rests on — stable layers, stable
 * order — are statements about this function, and they are worth asserting directly.
 */
export function buildModel(
  catalog: ReadonlyArray<{ id: string; title: string; dept: string; director: string; requires: { items: string[] } }>,
  items: Record<string, { status: ItemStatus }>,
  load: Record<string, number>,
  ceiling: number,
): DagModel {
  const placements = layout(catalog.map((entry) => ({ id: entry.id, requires: entry.requires.items })))
  const byId = new Map(placements.map((placement) => [placement.id, placement]))

  const nodes: Record<string, NodeView> = {}
  for (const entry of catalog) {
    const placement = byId.get(entry.id)
    if (placement === undefined) continue
    nodes[entry.id] = {
      id: entry.id,
      label: entry.title,
      status: items[entry.id]?.status ?? 'backlog',
      dept: entry.dept,
      // The department whose load this item's work sits in, which is the reporting line and
      // not the room — the two deliberately disagree for one person on the shipped roster.
      loadPermille: load[entry.director] ?? 0,
      ceiling,
      gx: placement.gx,
      gy: placement.gy,
    }
  }

  const edges: Array<{ from: string; to: string }> = []
  for (const entry of catalog) {
    for (const required of entry.requires.items) {
      if (nodes[required] !== undefined && nodes[entry.id] !== undefined) {
        edges.push({ from: required, to: entry.id })
      }
    }
  }

  return { placements, nodes, edges }
}

/**
 * Draw a polyline, pixel-stepped, optionally dashed and optionally partial.
 *
 * `upto` is how much of the run has been drawn, which is what makes the cascade a *draw*
 * rather than a fade. Dashes are stepped per pixel rather than set with `setLineDash`, because
 * the canvas dash phase is resolution-dependent and would break the pixel grid the moment the
 * stage was scaled.
 */
export function drawPolyline(
  context: PixelContext,
  points: readonly Point[],
  upto: number,
  colour: string,
  thickness: number,
  dash = 0,
): void {
  let budget = runLength(points) * upto
  let walked = 0
  context.fillStyle = colour

  for (let i = 1; i < points.length && budget > 0; i += 1) {
    const [x0, y0] = points[i - 1]
    const [x1, y1] = points[i]
    const length = Math.abs(x1 - x0) + Math.abs(y1 - y0)
    const take = Math.min(length, budget)
    budget -= take
    const vertical = x0 === x1
    const step = vertical ? (y1 > y0 ? 1 : -1) : x1 > x0 ? 1 : -1

    if (dash === 0) {
      if (vertical) {
        context.fillRect(x0 - thickness / 2, step > 0 ? y0 : y0 - take, thickness, take)
      } else {
        context.fillRect(step > 0 ? x0 : x0 - take, y0 - thickness / 2, take, thickness)
      }
    } else {
      for (let d = 0; d < take; d += 1) {
        if (Math.floor((walked + d) / dash) % 2) continue
        if (vertical) context.fillRect(x0 - thickness / 2, y0 + step * d, thickness, 1)
        else context.fillRect(x0 + step * d, y0 - thickness / 2, 1, thickness)
      }
    }
    walked += take
  }
}

/** How far along a severed edge the stub stops before its tick. */
export const SEVERED_FRACTION = 0.34

/** The graph's background lattice: 鲸鱼灰, barely there. */
export const LATTICE = withAlpha(PAL.jingyuhui, 0.1)

/**
 * One frame of the graph.
 *
 * Exported and context-injected so the suite can assert what a state *drew* — which colour, on
 * which form, with which edge — rather than comparing screenshots. Every property the art
 * direction states about this view is a property of this function's draw calls.
 */
export function drawGraph(
  context: PixelContext,
  model: DagModel,
  progress = 1,
  width = 0,
  height = 0,
): void {
  context.fillStyle = PAL.ganglan
  context.fillRect(0, 0, width, height)

  // A faint lattice, so nodes read as placed on a grid rather than floating. 鲸鱼灰 at a
  // tenth: on ink the lattice was a light line lifting off a dark ground, and inverting it
  // at the same alpha would have drawn a dark line on ivory at more than twice the apparent
  // weight.
  context.fillStyle = LATTICE
  for (let x = 0; x < width; x += GRID) context.fillRect(x, 0, 1, height)
  for (let y = 0; y < height; y += GRID) context.fillRect(0, y, width, 1)

  const byId = new Map(model.placements.map((placement) => [placement.id, placement]))
  const maxLayer = model.placements.reduce((deepest, p) => Math.max(deepest, p.layer), 0)

  for (const edge of model.edges) {
    const from = byId.get(edge.from)
    const to = byId.get(edge.to)
    if (from === undefined || to === undefined) continue

    const points = route(from, to)
    // The *source* node's status decides the edge, which is what makes the graph readable
    // without opening anything.
    const form = STATUS[model.nodes[edge.from].status].edge

    // The unlit channel, so an edge is present even before it carries anything. The rule
    // colour rather than the track colour — a track fill is a shape seen against a panel,
    // and this is a line seen against the ground.
    drawPolyline(context, points, 1, PAL.rule, 2, 0)

    if (form === 'solid') {
      const start = from.layer / (maxLayer + 1)
      const span = 1 / (maxLayer + 1)
      const local = Math.max(0, Math.min(1, (progress - start) / span))
      if (local > 0) drawPolyline(context, points, local, ACCENT, 2, 0)
    } else if (form === 'dashed') {
      drawPolyline(context, points, 1, PAL.jingyuhui, 2, 5)
    } else {
      // Severed: a violet stub that stops short, capped with a perpendicular tick. The gap is
      // the information — work cannot flow past a decision nobody has taken.
      drawPolyline(context, points, SEVERED_FRACTION, TACIT, 2, 0)
      const cap = pointAt(points, SEVERED_FRACTION)
      context.fillStyle = TACIT
      if (cap.vertical) context.fillRect(cap.x - 6, cap.y - 1, 12, 2)
      else context.fillRect(cap.x - 1, cap.y - 6, 2, 12)
    }
  }

  for (const placement of model.placements) {
    const node = model.nodes[placement.id]
    if (node === undefined) continue
    const lit = progress >= (placement.layer + 0.55) / (maxLayer + 1)
    drawNode(context, { ...node, lit })
  }
}

/** Whether the viewer has asked for less motion. Defaults to "no" where it cannot be asked. */
export function prefersReducedMotion(): boolean {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return false
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches
}
