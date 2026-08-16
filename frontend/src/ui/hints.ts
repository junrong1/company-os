/**
 * The two first-run hints, and the memory that stops them coming back (M7).
 *
 * **They are client state, and never anything else.** Nothing here reaches the kernel. A hint
 * dismissal is not a fact about the company, so writing one to the log would put a row in an
 * append-only record that the fold cannot reproduce — and a log the fold cannot reproduce fails
 * strict replay. That is not a stylistic preference about where preferences live: it is the
 * difference between a run that replays and one that does not. So there is no command, no event
 * kind, and no import of `../net/` in this module.
 *
 * **Dismissal is per browser, not per run.** A run is one sitting and the product wants two of
 * them compared in one sitting, so a hint scoped to the run would reappear on the second — and
 * a hint that reappears is not a first-run hint, it is a banner. It sits in local storage beside
 * the HUD's composition, under the same `company-os.` prefix, for the same reason: one storage
 * convention rather than two.
 *
 * **A hint is dismissed by being earned, as well as by being closed.** The walk hint goes when
 * the CEO first walks and the conversation hint goes when they first stand next to someone,
 * which is what makes these hints rather than a tutorial. The close button stays, because a
 * player who already knows both should not have to perform them to clear the corner.
 */

/** The CEO does not know the office is walkable until something says so. */
export const WALK_HINT = 'walk'

/** And walking is pointless until they know what standing next to somebody does. */
export const TALK_HINT = 'talk'

export interface Hint {
  id: string
  text: string
}

/**
 * Both hints, in the order they are needed.
 *
 * Walking first: the conversation hint describes something you cannot do until you have crossed
 * the floor, and a player reading them top to bottom should find the prerequisite first.
 *
 * Both name the keys and the gesture rather than the mechanic. "Standing next to someone
 * surfaces tacit knowledge" is the truth and is unreadable to somebody who has been in the
 * product for four seconds; what they need is "walk over there and you will hear more".
 */
export const HINTS: readonly Hint[] = [
  {
    id: WALK_HINT,
    text: 'Walk with the arrow keys, or W A S D.',
  },
  {
    id: TALK_HINT,
    text: 'Stand next to someone to talk to them. In person they say more than the tray does.',
  },
] as const

/** Where the dismissals are kept. Client-side, per browser, and never in the log. */
export const HINTS_STORAGE_KEY = 'company-os.hints.dismissed'

const KNOWN = new Set(HINTS.map((hint) => hint.id))

/**
 * Which hints have been dismissed already.
 *
 * Every failure path answers "none of them", which is the safe direction: the cost is showing a
 * hint a second time, against a first-time player who never sees either. Unknown ids are
 * dropped rather than kept — a dismissal written by a build whose hints have since been
 * replaced is not a statement about these hints.
 *
 * A `null` storage means this browser cannot remember, so nothing is dismissed and both hints
 * show on every mount. That is the honest answer rather than a silent "assume dismissed", which
 * would hide the two sentences the product needs most from exactly the environments where
 * something is already wrong.
 */
export function loadDismissed(storage: Pick<Storage, 'getItem'> | null): string[] {
  if (storage === null) return []

  let stored: string | null = null
  try {
    stored = storage.getItem(HINTS_STORAGE_KEY)
  } catch {
    return []
  }
  if (stored === null) return []

  let parsed: unknown
  try {
    parsed = JSON.parse(stored)
  } catch {
    return []
  }
  if (!Array.isArray(parsed)) return []

  return parsed
    .filter((entry): entry is string => typeof entry === 'string' && KNOWN.has(entry))
    .sort()
}

/** Persist the dismissals. Client-side only — this never reaches the log. */
export function saveDismissed(
  storage: Pick<Storage, 'setItem'> | null,
  dismissed: readonly string[],
): void {
  if (storage === null) return
  try {
    storage.setItem(HINTS_STORAGE_KEY, JSON.stringify([...dismissed].sort()))
  } catch {
    // A full or blocked storage costs the player one repeated hint on their next load, and
    // nothing else. Failing the render over it would be a worse trade.
  }
}

/**
 * Record a hint as done, whether it was closed or earned.
 *
 * Returns the *same array* when there is nothing to add, so the persisting effect and every
 * memo downstream see an unchanged reference. Without that, marking the walk hint dismissed on
 * every keypress would write to storage on every keypress.
 */
export function dismiss(dismissed: readonly string[], id: string): string[] {
  if (!KNOWN.has(id) || dismissed.includes(id)) return dismissed as string[]
  return [...dismissed, id].sort()
}

/** The hints still to show, in authored order. */
export function pendingHints(dismissed: readonly string[]): Hint[] {
  return HINTS.filter((hint) => !dismissed.includes(hint.id))
}
