/**
 * Ordering and drawing the chain strip. No React in here — see `ChainStrip.tsx`.
 *
 * The chain strip is the graph collapsed to one pip per item, in dependency order.
 *
 * **This is what makes the stage toggle safe.** A flow requirement says blocked work must be
 * visible in both the office and the DAG, and a full-stage toggle would break that if the DAG
 * were the only place status lived. Collapsing the graph to one pip per item in dependency
 * order, carrying the same border encoding, means a severed chain shows violet while you are
 * still standing in the office. You never need to open the DAG to learn that something
 * stopped; you open it to learn what is *downstream* of the thing that stopped.
 *
 * The pips are drawn by the same function as the full nodes, at 1px stroke. That is the whole
 * reason the encoding had to survive shrinking — hue alone would not have, and a second
 * simpler encoding for the strip would be a second thing to keep in agreement.
 */

import { PAL } from '../design/tokens'
import type { PixelContext } from '../design/text'
import type { ItemStatus } from '../net/store'
import { layerOf } from './layout'
import { PIP_GAP, PIP_HEIGHT, PIP_WIDTH, drawPip } from './nodes'

/** Padding around the row of pips. */
export const STRIP_PADDING = 6

export interface StripEntry {
  id: string
  status: ItemStatus
  dept: string
}

/**
 * The pips, in dependency order.
 *
 * Same ordering rule as the full graph — layer first, then item id — so a pip and its node are
 * in the same relative position on both surfaces. Two different orders would make the strip a
 * separate thing to learn rather than a smaller view of the same one.
 */
export function stripOrder(
  catalog: ReadonlyArray<{ id: string; dept: string; requires: { items: string[] } }>,
  items: Record<string, { status: ItemStatus }>,
): StripEntry[] {
  const layers = layerOf(catalog.map((entry) => ({ id: entry.id, requires: entry.requires.items })))

  return catalog
    .map((entry) => ({
      id: entry.id,
      dept: entry.dept,
      status: items[entry.id]?.status ?? ('backlog' as ItemStatus),
    }))
    .sort(
      (left, right) => layers[left.id] - layers[right.id] || left.id.localeCompare(right.id),
    )
}

/** How wide the strip needs to be for this many pips. */
export function stripWidth(count: number): number {
  if (count === 0) return STRIP_PADDING * 2
  return STRIP_PADDING * 2 + count * PIP_WIDTH + (count - 1) * PIP_GAP
}

/**
 * Draw the whole strip.
 *
 * Context-injected for the same reason the graph's draw is: the properties worth asserting are
 * "a blocked item drew a broken border in violet, at pip scale" and those are statements about
 * draw calls, not about pixels.
 */
export function drawStrip(
  context: PixelContext,
  entries: readonly StripEntry[],
  width: number,
  height: number,
): void {
  context.fillStyle = PAL.yanhanlan
  context.fillRect(0, 0, width, height)

  let x = STRIP_PADDING
  const y = Math.max(0, Math.round((height - PIP_HEIGHT) / 2))
  for (const entry of entries) {
    x += drawPip(context, x, y, entry)
  }
}
