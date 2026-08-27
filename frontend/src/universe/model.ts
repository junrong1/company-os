/**
 * The Universe: every timeline one Genesis produced, folded into a tree and drawn on a grid.
 *
 * **The same idiom as the DAG, deliberately.** Same 16px lattice, same orthogonally routed edges,
 * same rule that layout is stable under insertion. A second visual language for the second graph in
 * the product would make the player learn two things that mean the same thing — and the DAG's rule
 * about stability applies here for a stronger reason: a fork *is* an insertion, so a layout that
 * reshuffled would rearrange the tree in the same gesture that added to it.
 *
 * **Depth is the fork chain and order is the store's.** A node's column is how many forks separate
 * it from the lineage root, which is authored history and cannot change. Its row comes from the
 * order the store returned — creation order, then run id — which is stable under insertion because
 * a new row is always newest. Ordering by anything derived (day reached, whether it is running)
 * would move a node while the player was looking at it.
 *
 * **Three states, and none of them is a colour on its own.** Active, paused and ended are carried
 * by a border form and a label first, with hue as the secondary cue and only ever in a slot that
 * already means that thing. Amber is not available here: it means a *person* is waiting on the CEO,
 * never a timeline.
 */

import { ACCENT, OK, PAL, TACIT } from '../design/tokens'
import type { LineageWire, TimelineWire } from '../net/gateway'
import { type PixelContext, fitText, paintText } from '../design/text'

// =========================================================================
// The wire
// =========================================================================
//
// Defined in `net/gateway.ts` and re-exported here, not the other way round. The store module
// states the rule and the reason: wire shapes belong to `net/`, and defining them in a view and
// importing them downwards would put a `net -> ui` edge in a codebase that otherwise only has
// `ui -> net`. What this file owns is what the tree *does* with them.

export type { LineageWire, TimelineWire } from '../net/gateway'

// =========================================================================
// The three states
// =========================================================================

/** Where a timeline is. A closed set, and every member renders differently. */
export type TimelineState = 'active' | 'paused' | 'ended'

export interface StateEncoding {
  border: 'solid' | 'dotted' | 'double'
  hue: string
  label: string
}

/**
 * The three states of a node.
 *
 * `active` reuses the accent — live and interactive, which is what it already means in the DAG and
 * on a button. `ended` reuses `ok`: a run that reached its horizon is an outcome that landed, and
 * an insolvent one is still a finished history rather than a fault. `paused` is the rule colour,
 * which is to say no hue at all — a timeline nobody is playing should not compete for attention
 * with the one that is.
 */
export const STATES: Record<TimelineState, StateEncoding> = {
  active: { border: 'solid', hue: ACCENT, label: 'running' },
  paused: { border: 'dotted', hue: PAL.rule, label: 'paused' },
  ended: { border: 'double', hue: OK, label: 'ended' },
}

/**
 * Which state a timeline is in.
 *
 * Read from the row's own fields rather than from a status string the backend could have composed,
 * because the two questions are separate facts: a run that has ended is ended whatever its rate
 * says, which is exactly the case a half-finished switch leaves behind.
 */
export function timelineState(node: Pick<TimelineWire, 'rate' | 'terminal_reason'>): TimelineState {
  if (node.terminal_reason !== '') return 'ended'
  return node.rate > 0 ? 'active' : 'paused'
}

/** What the node says under its id: where it is, and why it is not somewhere else. */
export function describeTimeline(node: TimelineWire): string {
  const state = timelineState(node)
  if (state === 'ended') return `ended day ${node.day} · ${node.terminal_reason}`
  if (state === 'active') return `running · day ${node.day}`
  return `paused · day ${node.day}`
}

/**
 * The decision that separated this timeline from its parent, in one line.
 *
 * Empty for the root, which was not separated from anything. Named from the two option labels the
 * tree carries rather than from the client's own catalog: a tree spans a lineage, and the labels
 * arrive with the node.
 */
export function describeDivergence(node: TimelineWire): string {
  if (node.parent_run_id === '' || node.item === '') return ''
  if (node.choice === '' && node.parent_choice === '') return node.item
  return `${node.item}: ${node.choice} instead of ${node.parent_choice}`
}

// =========================================================================
// Layout
// =========================================================================

/** The shared grid. The tree is drawn on the same lattice as the office and the DAG. */
export const GRID = 16

/** Node size in grid units. Wider than a DAG node: a run id and a day do not abbreviate well. */
export const NODE_COLS = 14
export const NODE_ROWS = 3

/** Gaps, in grid units. */
export const DEPTH_GAP_COLS = 4
export const ROW_GAP_ROWS = 1

export interface TimelinePlacement {
  runId: string
  /** How many forks from the lineage root. Authored history, so it cannot move. */
  depth: number
  row: number
  /**
   * Top-left corner, in **canvas pixels** rather than in grid units.
   *
   * The one place this surface departs from the DAG's shape, and deliberately: the DAG's
   * placements are grid units multiplied at draw time, which is right for something painted per
   * frame and never clicked. This tree is painted once per lineage change and is *hit-tested* on
   * every click, so keeping one unit end to end is what stops a click landing on the wrong node —
   * which is exactly the bug the first version of this had.
   */
  x: number
  y: number
}

/**
 * Place every node.
 *
 * Depth is the length of the parent chain, computed iteratively rather than recursively: a lineage
 * is shallow, but a `parent_run_id` pointing at a node this tree does not hold — a row trimmed from
 * a store this build did not write — must produce a placement rather than a stack overflow. An
 * unknown parent is treated as depth zero, which draws the node as a root of its own instead of
 * dropping it.
 */
export function layoutTimelines(nodes: readonly TimelineWire[]): TimelinePlacement[] {
  const byId = new Map(nodes.map((node) => [node.run_id, node]))
  const depths = new Map<string, number>()

  const depthOf = (runId: string): number => {
    const cached = depths.get(runId)
    if (cached !== undefined) return cached

    // Iterative, with a guard, and memoised on the way out. A `parent_run_id` pointing at a row
    // this tree does not hold — trimmed by a store this build did not write — stops the walk and
    // draws the node as a root of its own rather than dropping it; a cycle stops it too, because a
    // surface that hangs on a malformed store is worse than one that draws it oddly.
    const seen = new Set<string>()
    let cursor = runId
    let depth = 0
    while (!seen.has(cursor)) {
      seen.add(cursor)
      const parent = byId.get(cursor)?.parent_run_id ?? ''
      if (parent === '' || !byId.has(parent)) break
      depth += 1
      const known = depths.get(parent)
      if (known !== undefined) {
        depth += known
        break
      }
      cursor = parent
    }

    depths.set(runId, depth)
    return depth
  }

  const rows = new Map<number, number>()
  return nodes.map((node) => {
    const depth = depthOf(node.run_id)
    const row = rows.get(depth) ?? 0
    rows.set(depth, row + 1)
    return {
      runId: node.run_id,
      depth,
      row,
      x: depth * (NODE_COLS + DEPTH_GAP_COLS) * GRID,
      y: row * (NODE_ROWS + ROW_GAP_ROWS) * GRID,
    }
  })
}

/** The canvas the placements need, in pixels, with a one-node tree still getting a canvas. */
export function canvasSize(placements: readonly TimelinePlacement[]): {
  width: number
  height: number
} {
  const right = Math.max(
    ...placements.map((place) => place.x + NODE_COLS * GRID),
    NODE_COLS * GRID,
  )
  const bottom = Math.max(
    ...placements.map((place) => place.y + NODE_ROWS * GRID),
    NODE_ROWS * GRID,
  )
  // One grid unit of margin, so the outermost node's border is not flush with the edge.
  return { width: right + GRID, height: bottom + GRID }
}

/** Which node a click at these canvas coordinates landed on, or `null`. */
export function timelineAt(
  placements: readonly TimelinePlacement[],
  x: number,
  y: number,
): string | null {
  for (const place of placements) {
    if (
      x >= place.x &&
      x < place.x + NODE_COLS * GRID &&
      y >= place.y &&
      y < place.y + NODE_ROWS * GRID
    ) {
      return place.runId
    }
  }
  return null
}

// =========================================================================
// Drawing
// =========================================================================

export interface TreeModel {
  placements: TimelinePlacement[]
  nodes: Record<string, TimelineWire>
  /** Where the player is standing. Marked rather than merely coloured. */
  standingIn: string
}

export function buildTreeModel(lineage: LineageWire): TreeModel {
  return {
    placements: layoutTimelines(lineage.nodes),
    nodes: Object.fromEntries(lineage.nodes.map((node) => [node.run_id, node])),
    standingIn: lineage.asked_about,
  }
}

/**
 * Draw the tree.
 *
 * No React, no store, and a `PixelContext` rather than a canvas context — so the suite drives it
 * with a recording context and asserts the properties the art direction states: which form, which
 * colour, at which size. That is the same seam `dag/draw.ts` has, for the same reason.
 */
export function drawTree(
  context: PixelContext,
  model: TreeModel,
  width: number,
  height: number,
): void {
  // The product ground, like the DAG's. A surface that painted its own would read as a
  // different application the first time the palette moved.
  context.fillStyle = PAL.ganglan
  context.fillRect(0, 0, width, height)

  const byId = new Map(model.placements.map((place) => [place.runId, place]))

  // Edges first, so a node always sits on top of the line that reaches it.
  for (const place of model.placements) {
    const node = model.nodes[place.runId]
    const parent = byId.get(node?.parent_run_id ?? '')
    if (node === undefined || parent === undefined) continue
    drawEdge(context, parent, place)
  }

  for (const place of model.placements) {
    const node = model.nodes[place.runId]
    if (node === undefined) continue
    drawTimeline(context, node, place, place.runId === model.standingIn)
  }
}

/**
 * One timeline's plate.
 *
 * The border form carries the state, the label repeats it in words, and the hue is third. Where the
 * player is standing is a *filled* left edge rather than a colour — it survives greyscale, and it is
 * the one thing on this surface a player has to be able to find without reading.
 */
export function drawTimeline(
  context: PixelContext,
  node: TimelineWire,
  place: TimelinePlacement,
  standingIn: boolean,
): void {
  const encoding = STATES[timelineState(node)]
  const x = place.x
  const y = place.y
  const width = NODE_COLS * GRID
  const height = NODE_ROWS * GRID

  context.fillStyle = PAL.rule
  context.fillRect(x, y, width, height)
  context.fillStyle = PAL.gangqing
  context.fillRect(x + 2, y + 2, width - 4, height - 4)

  // The state's border, in its own form. Dotted is drawn as dashes rather than as a stipple, so it
  // survives at this size; double is two rules with the plate between them.
  context.fillStyle = encoding.hue
  if (encoding.border === 'solid') {
    context.fillRect(x, y, width, 2)
    context.fillRect(x, y + height - 2, width, 2)
  } else if (encoding.border === 'dotted') {
    for (let step = 0; step < width; step += 8) {
      context.fillRect(x + step, y, 4, 2)
      context.fillRect(x + step, y + height - 2, 4, 2)
    }
  } else {
    context.fillRect(x, y, width, 2)
    context.fillRect(x, y + 4, width, 1)
    context.fillRect(x, y + height - 2, width, 2)
  }

  if (standingIn) {
    // Where the player is. A solid bar down the left edge in the accent, which is the only place
    // this surface spends it.
    context.fillStyle = ACCENT
    context.fillRect(x + 2, y + 2, 4, height - 4)
  }

  const inset = x + (standingIn ? 10 : 6)
  const room = width - (inset - x) - 6
  paintText(context, fitText(shortId(node.run_id), room), inset, y + 6, 1, PAL.text)
  paintText(context, fitText(describeTimeline(node), room), inset, y + 18, 1, PAL.textFaint)

  const divergence = describeDivergence(node)
  if (divergence !== '') {
    // Violet, because a divergence is a decision — the same slot the DAG spends on a blocked node
    // and the office spends on the tacit line. One meaning, three objects.
    paintText(context, fitText(divergence, room), inset, y + 30, 1, TACIT)
  }
}

/** The orthogonal elbow from a parent's right edge to a child's left edge. */
export function drawEdge(
  context: PixelContext,
  parent: TimelinePlacement,
  child: TimelinePlacement,
): void {
  const fromX = parent.x + NODE_COLS * GRID
  const fromY = parent.y + (NODE_ROWS * GRID) / 2
  const toX = child.x
  const toY = child.y + (NODE_ROWS * GRID) / 2
  const midX = fromX + (toX - fromX) / 2

  context.fillStyle = PAL.rule
  context.fillRect(fromX, fromY - 1, Math.max(1, midX - fromX), 2)
  const top = Math.min(fromY, toY)
  context.fillRect(midX - 1, top, 2, Math.max(2, Math.abs(toY - fromY)))
  context.fillRect(midX, toY - 1, Math.max(1, toX - midX), 2)
}

/**
 * A run id, short enough to read on a plate.
 *
 * Forked ids are `<parent>-<twelve hex>`, which is unreadable at this size and unnecessary: what
 * distinguishes two siblings is the tail. The full id is in the panel beside the tree and in the
 * address bar, so nothing is lost.
 */
export function shortId(runId: string): string {
  const tail = runId.split('-').pop() ?? runId
  return tail.length > 8 ? tail.slice(0, 8) : tail
}
