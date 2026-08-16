import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

import { act, createElement } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useRunStore } from '../src/net/store'
import { COMPOSITION_STORAGE_KEY } from '../src/ui/hud-model'
import {
  HINTS,
  HINTS_STORAGE_KEY,
  TALK_HINT,
  WALK_HINT,
  dismiss,
  loadDismissed,
  pendingHints,
  saveDismissed,
} from '../src/ui/hints'
import { Shell } from '../src/ui/Shell'
import { genesisFrame } from './helpers/frames'

/**
 * The two first-run hints (M7).
 *
 * The property under test is not "a hint renders" — it is that a hint renders *once*, that the
 * once survives a reload, and that none of it reaches the kernel. So most of this file is about
 * what happens on the second mount and about what was not sent.
 */

/**
 * A source file, read as text.
 *
 * The path goes through a parameter rather than being written inline, and that is not style:
 * Vite rewrites the literal form `new URL('./x', import.meta.url)` into a served asset URL, so
 * an inlined path resolves to `http://localhost:3000/...` and `fileURLToPath` refuses it. Both
 * `chrome.test.ts` and `dag.test.ts` route through a variable for the same reason.
 */
function read(relative: string): string {
  return readFileSync(fileURLToPath(new URL(relative, import.meta.url)), 'utf8')
}

/**
 * The run store as comparable text.
 *
 * Ticks and sequences are bigints, which `JSON.stringify` refuses outright, so they are spelled
 * out. Comparing the serialized whole rather than a chosen list of fields is the point: a hint
 * that quietly wrote itself into some other corner of the store would fail this, and a
 * field-by-field version would only check the corners somebody thought of.
 */
function storeText(): string {
  return JSON.stringify(useRunStore.getState(), (_key, value) =>
    typeof value === 'bigint' ? value.toString() : value,
  )
}

/** A storage that behaves like the browser's and can be inspected. */
function fakeStorage() {
  const entries = new Map<string, string>()
  return {
    getItem: (key: string): string | null => entries.get(key) ?? null,
    setItem: (key: string, value: string): void => {
      entries.set(key, value)
    },
    entries,
  }
}

// =========================================================================
// The memory
// =========================================================================

describe('remembering a dismissal', () => {
  it('keeps hint state beside the HUD composition, not in a second convention', () => {
    expect(HINTS_STORAGE_KEY).not.toBe(COMPOSITION_STORAGE_KEY)
    for (const key of [HINTS_STORAGE_KEY, COMPOSITION_STORAGE_KEY]) {
      expect(key.startsWith('company-os.')).toBe(true)
    }
  })

  it('starts with both hints pending', () => {
    const storage = fakeStorage()
    expect(loadDismissed(storage)).toEqual([])
    expect(pendingHints([]).map((hint) => hint.id)).toEqual([WALK_HINT, TALK_HINT])
  })

  it('round-trips a dismissal through storage', () => {
    const storage = fakeStorage()
    saveDismissed(storage, dismiss([], WALK_HINT))

    expect(loadDismissed(storage)).toEqual([WALK_HINT])
    expect(pendingHints(loadDismissed(storage)).map((hint) => hint.id)).toEqual([TALK_HINT])
  })

  it('returns the same array when a hint is dismissed twice', () => {
    // Otherwise every keypress after the first would be a fresh array, and the persisting
    // effect would write to storage on every keypress.
    const once = dismiss([], WALK_HINT)
    expect(dismiss(once, WALK_HINT)).toBe(once)
  })

  it('drops an id these hints do not have', () => {
    // A dismissal written by a build whose hints have since been replaced is not a statement
    // about the hints that exist now.
    const storage = fakeStorage()
    storage.setItem(HINTS_STORAGE_KEY, JSON.stringify(['walk', 'take-the-tour']))

    expect(loadDismissed(storage)).toEqual([WALK_HINT])
    expect(dismiss([], 'take-the-tour')).toEqual([])
  })

  it('shows both hints again rather than hiding them when the store is unusable', () => {
    // The cost of guessing wrong in this direction is one repeated hint. The other direction
    // hides the two sentences a first-time player needs in exactly the environments where
    // something is already broken.
    const corrupt = fakeStorage()
    corrupt.setItem(HINTS_STORAGE_KEY, 'not json')
    expect(loadDismissed(corrupt)).toEqual([])

    const wrongShape = fakeStorage()
    wrongShape.setItem(HINTS_STORAGE_KEY, JSON.stringify({ walk: true }))
    expect(loadDismissed(wrongShape)).toEqual([])

    const throwing = {
      getItem: () => {
        throw new Error('storage is blocked')
      },
      setItem: () => {
        throw new Error('storage is blocked')
      },
    }
    expect(loadDismissed(throwing)).toEqual([])
    // And a blocked write is not a broken render.
    expect(() => saveDismissed(throwing, [WALK_HINT])).not.toThrow()

    expect(loadDismissed(null)).toEqual([])
  })
})

// =========================================================================
// Nothing about a hint reaches the kernel
// =========================================================================

describe('hint state and the log', () => {
  it('names no command, no event kind and no network module', () => {
    // The runtime assertion below proves this build sends nothing. This proves there is no path
    // by which a later one could: an event the fold cannot reproduce fails strict replay, so
    // "hints are client state" has to be a property of the module rather than of its callers.
    const source = read('../src/ui/hints.ts')

    expect(source).not.toMatch(/from '\.\.\/net\//)
    expect(source).not.toContain('submitCommand')
    expect(source).not.toContain('fetch(')
    // The two shapes a client-to-kernel write takes in this codebase.
    expect(source).not.toMatch(/EventKind|useRunStore/)
  })
})

// =========================================================================
// On screen, once
// =========================================================================

describe('the hints over the office', () => {
  let host: HTMLDivElement
  let root: ReturnType<typeof createRoot> | null = null
  let fetchSpy: ReturnType<typeof vi.fn>

  const stream = { start: () => {}, stop: () => {} }
  const makeStream = () => stream

  function mount(storage: Pick<Storage, 'getItem' | 'setItem'> | null) {
    act(() => {
      root = createRoot(host)
      root.render(createElement(Shell, { runId: 'run-1', makeStream, storage }))
    })
    act(() => {
      useRunStore.getState().apply(genesisFrame())
    })
  }

  function unmount() {
    act(() => root?.unmount())
    root = null
  }

  function cards(): HTMLElement[] {
    return [...host.querySelectorAll<HTMLElement>('.hints__card')]
  }

  beforeEach(() => {
    host = document.createElement('div')
    document.body.appendChild(host)
    // Every command the shell sends goes out through `fetch`, so a spy here is the whole of the
    // client's write path to the log.
    fetchSpy = vi.fn(() =>
      Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve({ produced_seq: [], reason: '' }),
      } as unknown as Response),
    )
    vi.stubGlobal('fetch', fetchSpy)
  })

  afterEach(() => {
    unmount()
    host.remove()
    vi.unstubAllGlobals()
    useRunStore.getState().reset()
  })

  it('shows both hints on a first run', () => {
    mount(fakeStorage())

    expect(cards().map((card) => card.dataset.hint)).toEqual([WALK_HINT, TALK_HINT])
    for (const hint of HINTS) {
      expect(host.textContent).toContain(hint.text)
    }
  })

  it('shows a dismissed hint neither again nor after a reload', () => {
    const storage = fakeStorage()
    mount(storage)

    const walk = cards()[0]
    expect(walk.dataset.hint).toBe(WALK_HINT)
    act(() => walk.querySelector<HTMLButtonElement>('.hints__dismiss')?.click())

    // Gone now, and the other one is untouched: dismissal is per hint, not a single "seen the
    // tutorial" flag.
    expect(cards().map((card) => card.dataset.hint)).toEqual([TALK_HINT])
    expect(storage.getItem(HINTS_STORAGE_KEY)).toBe(JSON.stringify([WALK_HINT]))

    // A reload is a fresh mount against the same browser store.
    unmount()
    mount(storage)
    expect(cards().map((card) => card.dataset.hint)).toEqual([TALK_HINT])

    act(() => cards()[0].querySelector<HTMLButtonElement>('.hints__dismiss')?.click())
    expect(cards()).toEqual([])

    unmount()
    mount(storage)
    expect(host.querySelector('.hints')).toBeNull()
  })

  it('writes nothing to the kernel when a hint is dismissed', () => {
    mount(fakeStorage())
    const before = storeText()

    act(() => cards()[0].querySelector<HTMLButtonElement>('.hints__dismiss')?.click())

    // No command, so no event, so nothing in the log the fold would have to reproduce.
    expect(fetchSpy).not.toHaveBeenCalled()
    expect(storeText()).toBe(before)
  })

  it('earns the walk hint by walking rather than by being read', () => {
    const storage = fakeStorage()
    mount(storage)

    act(() => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight' }))
    })

    expect(cards().map((card) => card.dataset.hint)).toEqual([TALK_HINT])
    expect(loadDismissed(storage)).toEqual([WALK_HINT])
    // The keypress itself is a command; the dismissal rode along with it and added nothing.
    expect(fetchSpy).toHaveBeenCalledTimes(1)
    const [, request] = fetchSpy.mock.calls[0] as [string, RequestInit]
    expect(String(request.body)).not.toContain('hint')
  })

  it('is not cleared by a key release the CEO never pressed', () => {
    // Blur and keyup both send a zero mask. A hint cleared by one of those would vanish before
    // the player had done anything.
    const storage = fakeStorage()
    mount(storage)

    act(() => {
      window.dispatchEvent(new Event('blur'))
    })

    expect(cards().map((card) => card.dataset.hint)).toEqual([WALK_HINT, TALK_HINT])
    expect(loadDismissed(storage)).toEqual([])
  })

  it('keeps the hints off the chain view, where neither can be performed', () => {
    mount(fakeStorage())
    expect(host.querySelector('.hints')).not.toBeNull()

    const toDag = host.querySelectorAll<HTMLButtonElement>('.stage-toggle button')[1]
    act(() => toDag.click())

    expect(host.querySelector('main')?.dataset.stage).toBe('dag')
    expect(host.querySelector('.hints')).toBeNull()
  })

  it('remembers nothing when the browser cannot, and still renders', () => {
    mount(null)
    expect(cards()).toHaveLength(HINTS.length)

    act(() => cards()[0].querySelector<HTMLButtonElement>('.hints__dismiss')?.click())
    expect(cards()).toHaveLength(HINTS.length - 1)

    // Back on the next load, because there was nowhere to write it down. Stated rather than
    // left implicit: this is the one case where "shown once" cannot hold, and the alternative
    // was hiding both hints from a browser that is already misbehaving.
    unmount()
    mount(null)
    expect(cards()).toHaveLength(HINTS.length)
  })
})

// =========================================================================
// The beam is lit before either hint has been read (M6)
// =========================================================================

describe('the opening frame', () => {
  afterEach(() => {
    useRunStore.getState().reset()
  })

  it('lights the waiting beam on the seeded director from the first checkpoint', async () => {
    const { actorsFromStore } = await import('../src/ui/stage')

    act(() => {
      useRunStore.getState().apply(genesisFrame())
    })

    // Nobody is waiting yet: the kernel raises the seeded checkpoint on its first step, so the
    // client's own day zero is one event long.
    expect(actorsFromStore().filter((actor) => actor.waiting)).toEqual([])

    // The kernel's tick-1 event for a fresh run, verbatim in shape: this is what
    // `test_a_new_run_reaches_an_unresolved_checkpoint_with_no_command` asserts is produced
    // with no command sent.
    act(() => {
      useRunStore.getState().apply({
        kind: 'CHECKPOINT_RAISED',
        seq: '2',
        tick: '1',
        schema_ver: 3,
        rules_ver: 'test',
        run_id: 'run-1',
        command_id: '',
        request_id: '',
        payload: {
          tick: 1,
          item: 'wi_hiring',
          person: 'dir_hr',
          cp_index: 0,
          kind: 'info',
          label: 'Information',
          at_percent: 45,
          done_units: 35680,
          item_status: 'blocked',
          tacit: 'referrals skip the requirements entirely',
        },
      })
    })

    const waiting = actorsFromStore().filter((actor) => actor.waiting)
    expect(waiting.map((actor) => actor.id)).toEqual(['dir_hr'])
    expect(useRunStore.getState().items.wi_hiring?.status).toBe('blocked')
    expect(useRunStore.getState().items.wi_hiring?.assignee).toBe('dir_hr')
  })
})
