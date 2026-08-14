/**
 * What the panels compute, with no React in them.
 *
 * Split from the components so the component file exports only components, which is what the
 * project's lint config asks for and what keeps fast refresh working on them.
 */

import type { CatalogEntry, ItemStatus } from '../net/store'

/** Percent complete, cross-multiplied so it agrees with the kernel's own threshold test. */
export function progressPercent(doneUnits: number, effortUnits: number): number {
  if (effortUnits <= 0) return 0
  return Math.min(100, Math.floor((doneUnits * 100) / effortUnits))
}

/**
 * Why an item is not available yet, in a sentence.
 *
 * Read from the catalog's authored gates rather than from a reason string on the wire: the
 * gates are immutable and already on the client, and a locked item produces no events to carry
 * an explanation.
 */
export function lockReason(
  entry: CatalogEntry,
  visibility: number,
  done: Set<string>,
): string {
  const missing = entry.requires.items.filter((id) => !done.has(id))
  if (missing.length > 0) return `waits on ${missing.join(', ')}`
  if (entry.requires.visibility !== null && visibility < entry.requires.visibility) {
    return `needs ${entry.requires.visibility}% visibility (at ${visibility}%)`
  }
  return ''
}

export const STATUS_LABEL: Record<ItemStatus, string> = {
  backlog: 'Backlog',
  assigned: 'Assigned',
  active: 'In progress',
  blocked: 'Needs you',
  done: 'Delivered',
}
