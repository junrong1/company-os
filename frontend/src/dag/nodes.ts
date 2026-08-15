/**
 * Node encoding: border form first, glyph second, hue last.
 *
 * A node already spends two colour channels — the department stripe and the load bar. Status
 * cannot be a third. Three simultaneous colour codings on a small plate is unreadable, and it
 * would collide with both of the ones already there. So **status is carried by border form
 * plus a glyph**, with hue as a secondary cue that only ever reuses a semantic slot for the
 * thing it already means:
 *
 *   accent 石绿  = live, interactive          → in progress
 *   ok     翠绿  = a good outcome landed      → complete
 *   tacit  violet = knowledge only conversation yields → blocked
 *
 * **Blocked is the load-bearing state, and it must not be amber.** Amber means a *person* is
 * waiting on you, never a work item. Violet is not a dilution of `tacit` — a blocked node is
 * precisely the site where the tacit line is available, so it is the same meaning applied to
 * the object that holds it. In the office you see amber above a head; in the DAG you see
 * violet on a node. Same underlying fact, two different objects, and the pairing is teachable.
 *
 * Three redundant cues carry blocked — broken border, question-mark glyph, severed outgoing
 * edge — so the state survives both greyscale and pip scale. That redundancy is what the
 * "distinguishable with every status hue flattened to one value" test checks, and it is why
 * the encoding could be collapsed into the chain strip at all: **one function draws both**,
 * the full node at 2px stroke and the pip at 1px.
 */

import {
  ACCENT,
  AUTHORED_TUNING,
  OK,
  PAL,
  TACIT,
  deptColour,
  loadColour,
  loadFillPermille,
} from '../design/tokens'
import { type PixelContext, fitText, paintGrid, paintText } from '../design/text'
import type { ItemStatus } from '../net/store'

/** The shared grid. The DAG is drawn on the same 16px lattice as the office. */
export const GRID = 16

/** Node height in grid units. */
export const NODE_ROWS = 4

/** Node width in grid units. */
export const NODE_COLS = 11

export type BorderForm = 'none' | 'dotted' | 'solid' | 'broken' | 'double'
export type EdgeForm = 'dashed' | 'solid' | 'severed'

export interface StatusEncoding {
  border: BorderForm
  glyph: string
  /** Secondary only. Every value is an existing slot used for what it already means. */
  hue: string
  /** Derived from the *source* node, so the graph shows where work can flow. */
  edge: EdgeForm
  label: string
}

/**
 * The five states.
 *
 * Keyed by the kernel's own status strings, so there is no translation table between what an
 * event says and what a node draws.
 */
export const STATUS: Record<ItemStatus, StatusEncoding> = {
  backlog: {
    border: 'none',
    glyph: 'backlog',
    hue: PAL.jingyuhui,
    edge: 'dashed',
    label: PAL.jingyuhui,
  },
  assigned: {
    border: 'dotted',
    glyph: 'assigned',
    hue: PAL.yueyingbai,
    edge: 'dashed',
    label: PAL.xinghui,
  },
  active: {
    border: 'solid',
    glyph: 'work',
    hue: ACCENT,
    edge: 'dashed',
    label: PAL.yuebai,
  },
  blocked: {
    border: 'broken',
    glyph: 'raised',
    hue: TACIT,
    edge: 'severed',
    label: TACIT,
  },
  done: {
    border: 'double',
    glyph: 'deliver',
    hue: OK,
    edge: 'solid',
    label: PAL.yuebai,
  },
}

/**
 * 8×8 status glyphs. `.` transparent, `k` ink outline, `a` primary fill, `b` secondary.
 *
 * `work`, `raised` and `deliver` are the person glyphs reused: a node in progress, blocked, or
 * delivered means the same thing as a person doing, asking, or handing over, so giving them
 * separate glyphs would be two vocabularies for one idea.
 */
export const GLYPHS: Record<string, readonly string[]> = {
  work: ['..kkkkk.', '..kaaak.', '..kbbbk.', '..kaaak.', '..kbbbk.', '..kaaak.', '..kkkkk.', '........'],
  raised: ['..kkkk..', '.kaaaak.', '.kk..ak.', '....kk..', '...kk...', '...kk...', '........', '...kk...'],
  deliver: ['.kkkkkk.', '.kaakak.', '.kaakak.', '.kkkkkk.', '.kaakak.', '.kaakak.', '.kkkkkk.', '........'],
  backlog: ['........', '.kkkkkk.', '.k....k.', '.k....k.', '.k....k.', '.k....k.', '.kkkkkk.', '........'],
  assigned: ['........', '..k..kk.', '...k..k.', 'kkkk..k.', '...k..k.', '..k..kk.', '........', '........'],
}

/**
 * The plate behind a glyph is dark, so the glyph's *structure* has to be the light value.
 *
 * Inking `k` in the stage colour would make the outline vanish into the plate and leave only
 * the interior fill showing — the glyph would read as two dashes rather than as a symbol.
 */
export const GLYPH_PALETTE = {
  k: PAL.yueyingbai,
  a: PAL.jingyuhui,
  b: PAL.qinghui,
}

/**
 * Draw a border in one of the five forms.
 *
 * Form is the primary status channel, shared by the full node and the collapsed pip. Scaling
 * down is the test: dotted thins to corner ticks and broken keeps its gap, so all five stay
 * distinguishable at pip size and in greyscale — which hue alone would not survive.
 */
export function drawBorder(
  context: PixelContext,
  x: number,
  y: number,
  width: number,
  height: number,
  form: BorderForm,
  hue: string,
  thickness = 2,
): void {
  if (form === 'none') return
  const t = Math.max(1, thickness)
  context.fillStyle = hue

  if (form === 'solid') {
    context.fillRect(x, y, width, t)
    context.fillRect(x, y + height - t, width, t)
    context.fillRect(x, y, t, height)
    context.fillRect(x + width - t, y, t, height)
    return
  }

  if (form === 'dotted') {
    const step = Math.max(3, t * 3)
    const run = Math.max(1, t)
    for (let i = 0; i < width; i += step) {
      context.fillRect(x + i, y, run, t)
      context.fillRect(x + i, y + height - t, run, t)
    }
    for (let i = 0; i < height; i += step) {
      context.fillRect(x, y + i, t, run)
      context.fillRect(x + width - t, y + i, t, run)
    }
    return
  }

  if (form === 'broken') {
    // Solid except a gap on the *outgoing* edge — the chain is cut here, and the gap is on the
    // side the work would have flowed out of.
    const gap = Math.max(4, Math.round(height * 0.35))
    const mid = Math.round(height / 2)
    context.fillRect(x, y, width, t)
    context.fillRect(x, y + height - t, width, t)
    context.fillRect(x, y, t, height)
    context.fillRect(x + width - t, y, t, Math.max(0, mid - gap / 2))
    context.fillRect(x + width - t, y + mid + gap / 2, t, Math.max(0, height - mid - gap / 2))
    return
  }

  // double: two concentric rules, reading as "sealed". This exists because complete and in
  // progress would otherwise share the solid form, and the encoding has to hold with hue
  // stripped out entirely.
  const offset = t + Math.max(1, t)
  context.fillRect(x, y, width, t)
  context.fillRect(x, y + height - t, width, t)
  context.fillRect(x, y, t, height)
  context.fillRect(x + width - t, y, t, height)
  context.fillRect(x + offset, y + offset, width - offset * 2, t)
  context.fillRect(x + offset, y + height - offset - t, width - offset * 2, t)
  context.fillRect(x + offset, y + offset, t, height - offset * 2)
  context.fillRect(x + width - offset - t, y + offset, t, height - offset * 2)
}

/** Everything one node needs to draw itself. Folded from the same state the office reads. */
export interface NodeView {
  id: string
  label: string
  status: ItemStatus
  /** The room colour of the department the work sits in. */
  dept: string
  /** Per-mille of available capacity for the owning reporting line. */
  loadPermille: number
  /** Read off the wire, never assumed. */
  ceiling: number
  /** Grid coordinates, in units of GRID. */
  gx: number
  gy: number
  cols?: number
  /** True while the unlock cascade is lighting this node. */
  lit?: boolean
}

/**
 * One node, full size.
 *
 * Drawing order matters: the plate, then the two colour channels that are already spent, then
 * the glyph in the one corner neither of them occupies, then the label, then the border. The
 * border goes last so the status cue is never overdrawn by a fill.
 */
export function drawNode(context: PixelContext, node: NodeView): void {
  const encoding = STATUS[node.status]
  const x = node.gx * GRID
  const y = node.gy * GRID
  const width = (node.cols ?? NODE_COLS) * GRID
  const height = NODE_ROWS * GRID

  // Plate. A completed node's interior is recessed, which is the third cue on `done`.
  context.fillStyle = PAL.qinghui
  context.fillRect(x, y, width, height)
  context.fillStyle = node.status === 'done' ? '#0c1019' : PAL.gangqing
  context.fillRect(x + 2, y + 2, width - 4, height - 4)

  // Department stripe, left edge, in the room's own floor colour.
  context.fillStyle = deptColour(node.dept)
  context.fillRect(x + 2, y + 2, 4, height - 4)

  // Load bar, bottom edge, on the ramp the office uses.
  const trackWidth = width - 20
  context.fillStyle = PAL.qinghui
  context.fillRect(x + 10, y + height - 8, trackWidth, 4)
  context.fillStyle = loadColour(node.loadPermille, node.ceiling)
  const fill = Math.round(
    (loadFillPermille(node.loadPermille, node.ceiling) * trackWidth) / 1000,
  )
  context.fillRect(x + 10, y + height - 8, fill, 4)

  // Status glyph, top-right — never where the stripe or the bar sit.
  paintGrid(context, GLYPHS[encoding.glyph], x + width - 16, y + 7, GLYPH_PALETTE, 1)

  paintText(context, fitText(node.label, width - 28), x + 10, y + 7, 1, encoding.label)

  // The owning department's load, on the same scale the office's capacity tile shows — the
  // ceiling comes off the wire for both, so the two cannot read against different domains.
  //
  // Marked, because it is a figure the client renders and R27 admits no exceptions. The marking
  // is appended to the text rather than tinted in, since it must survive greyscale and the hue
  // here is already spent saying "over ceiling".
  const percent = Math.round((node.loadPermille * 100) / Math.max(1, node.ceiling))
  paintText(
    context,
    `LOAD ${percent}% ${AUTHORED_TUNING.glyph}`,
    x + 10,
    y + 18,
    1,
    node.loadPermille > node.ceiling ? PAL.zhuhong : PAL.jingyuhui,
  )

  drawBorder(context, x, y, width, height, encoding.border, encoding.hue, 2)

  // The cascade's payload rides on top rather than replacing the status border: an unlock is
  // an event, and the status is a state. Overwriting one with the other would lose the state
  // for the length of the animation.
  if (node.lit === true && node.status !== 'blocked') {
    context.fillStyle = ACCENT
    context.fillRect(x - 2, y - 2, width + 4, 1)
    context.fillRect(x - 2, y + height + 1, width + 4, 1)
  }
}

/** How wide a pip is, and the gap between two of them. */
export const PIP_WIDTH = 10
export const PIP_HEIGHT = 14
export const PIP_GAP = 3

/**
 * One pip: the same encoding, collapsed.
 *
 * Drawn by the same border function as the full node, at 1px stroke. That is the whole reason
 * the encoding had to survive shrinking — a second, simpler pip encoding would be a second
 * thing to keep in agreement, and it would quietly stop agreeing.
 */
export function drawPip(
  context: PixelContext,
  x: number,
  y: number,
  node: Pick<NodeView, 'status' | 'dept'>,
): number {
  const encoding = STATUS[node.status]

  context.fillStyle = PAL.gangqing
  context.fillRect(x, y, PIP_WIDTH, PIP_HEIGHT)
  context.fillStyle = deptColour(node.dept)
  context.fillRect(x, y, 2, PIP_HEIGHT)
  drawBorder(context, x, y, PIP_WIDTH, PIP_HEIGHT, encoding.border, encoding.hue, 1)

  return PIP_WIDTH + PIP_GAP
}
