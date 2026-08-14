import { act } from 'react'
import { createElement, useRef } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import {
  LOAD_RAMP,
  RESERVED_BEAM,
  direction,
  loadColour,
  loadFillPermille,
  overCeiling,
} from '../src/design/tokens'
import { type Frame, type MetricDefLike, useRunStore } from './helpers/store-helpers'
import {
  COMPOSITION_STORAGE_KEY,
  DEFAULT_COMPOSITION,
  NON_REMOVABLE,
  PRESSURE_TILE,
  RUNWAY_TILE,
  TileNotRemovable,
  addTile,
  decisionPressure,
  loadComposition,
  pressureColour,
  removeTile,
  runwayDays,
  saveComposition,
  sparklinePoints,
  trajectoryDirection,
} from '../src/ui/hud-model'
import { catalogFixture, genesisFrame, itemFrame, loadFrame, metricsFrame } from './helpers/frames'

/**
 * The HUD's rules, and the two properties that are really about the design system.
 *
 * Most of what matters here is decidable without a DOM, so most of it is tested that way — a
 * component test that renders a tile and reads its text proves the JSX is wired, not that
 * falling manual hours read as a win. The two tests that *do* render are the ones whose claim
 * is about React itself: that a burst of events does not re-render a narrowly-subscribed panel,
 * and that composition survives a reload.
 */

afterEach(() => {
  useRunStore.getState().reset()
})

// =========================================================================
// Favourable direction
// =========================================================================

const MANUAL_HOURS: MetricDefLike = {
  key: 'manualHours',
  label: 'Manual work',
  unit: 'h/mo',
  chip_unit: 'h/mo',
  good: -1,
  floor: 0,
  ceiling: null,
  display_max: 400,
}

const CASH: MetricDefLike = {
  key: 'cash',
  label: 'Cash',
  unit: '$K',
  chip_unit: 'K',
  good: 1,
  floor: null,
  ceiling: null,
  display_max: 6000,
}

describe('favourable direction', () => {
  it('renders manual hours falling over ten sim-days as favourable', () => {
    // Ten daily samples, drifting down as decisions automate work away. This is the movement
    // the whole run is *for*, and a uniform rising-is-good rule would render it as a
    // regression.
    const points = Array.from({ length: 10 }, (_, day) => ({
      tick: BigInt(day * 540),
      value: 340 - day * 6,
    }))

    expect(trajectoryDirection(MANUAL_HOURS, points)).toBe('favourable')
    expect(direction(MANUAL_HOURS, -6)).toBe('favourable')
  })

  it('renders cash falling as unfavourable', () => {
    const points = Array.from({ length: 10 }, (_, day) => ({
      tick: BigInt(day * 540),
      value: 4800 - day * 120,
    }))

    expect(trajectoryDirection(CASH, points)).toBe('unfavourable')
    expect(direction(CASH, -120)).toBe('unfavourable')
  })

  it('gives the same movement opposite verdicts for the two metrics', () => {
    // The single assertion that makes the point: one number, two answers.
    expect(direction(MANUAL_HOURS, -10)).toBe('favourable')
    expect(direction(CASH, -10)).toBe('unfavourable')
  })
})

// =========================================================================
// Trajectories
// =========================================================================

describe('trajectories', () => {
  it('renders one data point flat rather than empty', () => {
    const single = [{ tick: 0n, value: 4800 }]

    expect(trajectoryDirection(CASH, single)).toBe('flat')

    // A line across the middle, not a dot in a corner: a run on its first sim-day has a real
    // value and no history, and "no data" would make that look broken.
    const points = sparklinePoints(single, 100, 24)
    expect(points).not.toBe('')
    expect(points).toBe('0,12 100,12')
  })

  it('renders a perfectly flat series on the midline rather than dividing by zero', () => {
    const flat = [
      { tick: 0n, value: 72 },
      { tick: 540n, value: 72 },
      { tick: 1080n, value: 72 },
    ]
    const points = sparklinePoints(flat, 100, 24)

    expect(points.split(' ')).toHaveLength(3)
    for (const pair of points.split(' ')) {
      expect(pair.endsWith(',12.0')).toBe(true)
    }
  })

  it('is empty only when there are genuinely no points', () => {
    expect(sparklinePoints([], 100, 24)).toBe('')
  })
})

// =========================================================================
// Decision pressure
// =========================================================================

describe('decision pressure', () => {
  it('never renders in the reserved amber', () => {
    // The rule with teeth: decision pressure aggregates exactly the fact the beam signals, so
    // the intuitive colour is the one hue the design system reserves. Checked at every level,
    // because "usually neutral" is not the property.
    for (const waiting of [0, 1, 3, 9, 40]) {
      const pressure = decisionPressure(
        Array.from({ length: waiting }, () => ({ atTick: 0n })),
        1000n,
        9,
      )
      expect(pressureColour(pressure)).not.toBe(RESERVED_BEAM)
    }
  })

  it('reports the longest wait, not the most recent', () => {
    const pressure = decisionPressure(
      [{ atTick: 900n }, { atTick: 100n }, { atTick: 950n }],
      1000n,
      9,
    )

    expect(pressure.waiting).toBe(3)
    expect(pressure.supply).toBe(9)
    expect(pressure.oldestWaitTicks).toBe(900n)
  })

  it('never reports a negative wait for an entry tagged ahead of the clock', () => {
    // A tray entry can carry a tick the store has not caught up to yet, between an event
    // landing and the tick being read.
    const pressure = decisionPressure([{ atTick: 1200n }], 1000n, 9)
    expect(pressure.oldestWaitTicks).toBe(0n)
  })
})

// =========================================================================
// Runway
// =========================================================================

describe('runway', () => {
  it('is unknown before the first day of costs rather than infinite', () => {
    // Rendering infinity would say "you have forever" at exactly the moment the answer is
    // not yet knowable.
    expect(runwayDays(4800, 0)).toBeNull()
  })

  it('divides cash by the kernel-reported daily cost', () => {
    expect(runwayDays(4800, 20)).toBe(240)
    expect(runwayDays(45, 20)).toBe(2)
  })

  it('floors at zero once cash is already gone', () => {
    expect(runwayDays(-100, 20)).toBe(0)
  })
})

// =========================================================================
// Composition
// =========================================================================

describe('HUD composition', () => {
  it('refuses to remove runway', () => {
    expect(() => removeTile(DEFAULT_COMPOSITION, RUNWAY_TILE)).toThrow(TileNotRemovable)
  })

  it('refuses to remove decision pressure', () => {
    expect(() => removeTile(DEFAULT_COMPOSITION, PRESSURE_TILE)).toThrow(TileNotRemovable)
  })

  it('refuses rather than silently ignoring, so the control cannot lie', () => {
    // A control that appears to work and does nothing is worse than one that refuses: the CEO
    // would conclude the tile is gone.
    let refused = false
    try {
      removeTile(DEFAULT_COMPOSITION, RUNWAY_TILE)
    } catch (cause) {
      refused = true
      expect(cause).toBeInstanceOf(TileNotRemovable)
      expect(String(cause)).toContain('insolvency')
    }
    expect(refused).toBe(true)
  })

  it('allows every other tile to go', () => {
    let composition = [...DEFAULT_COMPOSITION]
    for (const tile of DEFAULT_COMPOSITION) {
      if (NON_REMOVABLE.includes(tile)) continue
      composition = removeTile(composition, tile)
    }
    expect(composition).toEqual([...NON_REMOVABLE])
  })

  it('restores a removed tile to its default position', () => {
    const without = removeTile(DEFAULT_COMPOSITION, 'leadTime')
    const restored = addTile(without, 'leadTime')
    expect(restored).toEqual([...DEFAULT_COMPOSITION])
  })

  it('survives a reload', () => {
    const store = new Map<string, string>()
    const storage = {
      getItem: (key: string) => store.get(key) ?? null,
      setItem: (key: string, value: string) => void store.set(key, value),
    }

    const trimmed = removeTile(removeTile(DEFAULT_COMPOSITION, 'visibility'), 'morale')
    saveComposition(storage, trimmed)

    expect(loadComposition(storage)).toEqual(trimmed)
  })

  it('never appears in the log', () => {
    // Arrangement is a client preference; the log is a permanent record of what happened in the
    // company. Asserted against a store that has *already applied events*, so "the log did not
    // move" is a real observation rather than a restatement of an empty store — the earlier
    // version of this test asserted appliedSeq was 0n before anything had ever been applied,
    // which was true no matter what the composition path did.
    useRunStore.getState().apply(genesisFrame() as Frame)
    useRunStore.getState().apply(metricsFrame({ seq: 7, cash: 4700 }) as Frame)

    const before = {
      appliedSeq: useRunStore.getState().appliedSeq,
      metrics: { ...useRunStore.getState().metrics },
      items: { ...useRunStore.getState().items },
    }

    const store = new Map<string, string>()
    const storage = {
      getItem: (key: string) => store.get(key) ?? null,
      setItem: (key: string, value: string) => void store.set(key, value),
    }

    saveComposition(storage, removeTile(DEFAULT_COMPOSITION, 'cash'))

    // It went to client storage, under exactly one key...
    expect([...store.keys()]).toEqual([COMPOSITION_STORAGE_KEY])
    // ...and moved nothing the log is the record of.
    expect(useRunStore.getState().appliedSeq).toBe(before.appliedSeq)
    expect(useRunStore.getState().metrics).toEqual(before.metrics)
    expect(useRunStore.getState().items).toEqual(before.items)
  })

  it('repairs a stored arrangement that lost a non-removable tile', () => {
    // User data from a previous build, so it is validated rather than trusted.
    const storage = { getItem: () => JSON.stringify(['cash', 'morale']) }
    const loaded = loadComposition(storage)

    for (const required of NON_REMOVABLE) {
      expect(loaded).toContain(required)
    }
  })

  it('falls back to the default rather than refusing to render', () => {
    expect(loadComposition({ getItem: () => 'not json' })).toEqual([...DEFAULT_COMPOSITION])
    expect(loadComposition({ getItem: () => '{"not":"an array"}' })).toEqual([
      ...DEFAULT_COMPOSITION,
    ])
    expect(loadComposition(null)).toEqual([...DEFAULT_COMPOSITION])
    expect(
      loadComposition({
        getItem: () => {
          throw new Error('storage is blocked')
        },
      }),
    ).toEqual([...DEFAULT_COMPOSITION])
  })
})

// =========================================================================
// The load ramp
// =========================================================================

describe('the load ramp', () => {
  it('shows red once a department crosses its ceiling', () => {
    const ceiling = 1000

    expect(overCeiling(1120, ceiling)).toBe(true)
    // The last two stops are the warm ones — 赭石 then 朱红. Over-ceiling has to be in them.
    expect(LOAD_RAMP.slice(-2)).toContain(loadColour(1120, ceiling))
    expect(loadColour(1400, ceiling)).toBe(LOAD_RAMP[LOAD_RAMP.length - 1])
  })

  it('never passes through the reserved amber', () => {
    // The whole reason the ramp desaturates through 苍绿 instead of interpolating green to red:
    // a department at two-thirds load would otherwise look like a person waiting on a decision.
    for (let permille = 0; permille <= 2000; permille += 10) {
      expect(loadColour(permille, 1000)).not.toBe(RESERVED_BEAM)
    }
    expect(LOAD_RAMP).not.toContain(RESERVED_BEAM)
  })

  it('keeps saying something above the ceiling instead of pinning immediately', () => {
    // Over-ceiling load never blocks assignment, so the difference between 140% and 200% is one
    // a CEO acts on.
    expect(loadFillPermille(1000, 1000)).toBeLessThan(1000)
    expect(loadFillPermille(1400, 1000)).toBe(1000)
    expect(loadFillPermille(2800, 1000)).toBe(1000)
  })

  it('reads its ceiling off the wire rather than assuming one', () => {
    // The same load is a different colour under a different ceiling, which is the point: a
    // tuning pass that moves the ceiling must not leave the ramp reading against a stale scale.
    expect(loadColour(600, 1000)).not.toBe(loadColour(600, 400))
  })

  it('survives a nonsensical ceiling instead of dividing by zero', () => {
    expect(loadColour(500, 0)).toBe(LOAD_RAMP[0])
    expect(loadFillPermille(500, 0)).toBe(0)
  })
})

// =========================================================================
// Rendering: the two claims that are about React
// =========================================================================

/**
 * A panel that subscribes to exactly one metric and counts its own renders.
 *
 * The narrow-selector case, which is the one the store's design rests on. If this re-renders
 * when unrelated events land, then every panel does, and the canvas is being driven through
 * the framework rather than around it.
 */
function makeCountingPanel(select: (state: ReturnType<typeof useRunStore.getState>) => unknown) {
  const renders = { count: 0 }

  function Panel() {
    renders.count += 1
    const value = useRunStore(select as never)
    const seen = useRef(value)
    seen.current = value
    return createElement('span', { 'data-value': String(value) })
  }

  return { Panel, renders }
}

describe('a burst of events', () => {
  let host: HTMLDivElement
  let root: ReturnType<typeof createRoot> | null = null

  beforeEach(() => {
    host = document.createElement('div')
    document.body.appendChild(host)
  })

  afterEach(() => {
    act(() => root?.unmount())
    root = null
    host.remove()
  })

  it('updates the canvas without re-rendering a subscribed panel', () => {
    const { Panel, renders } = makeCountingPanel((state) => state.metrics.cash ?? 0)

    act(() => {
      root = createRoot(host)
      root.render(createElement(Panel))
    })

    const afterMount = renders.count
    expect(afterMount).toBeGreaterThan(0)

    // Forty events that move everything the canvas reads — positions, load, ticks — and leave
    // cash alone. The store advances on every one of them.
    act(() => {
      useRunStore.getState().apply(genesisFrame() as Frame)
    })
    const afterGenesis = renders.count

    act(() => {
      for (let i = 0; i < 40; i += 1) {
        useRunStore.getState().apply({
          kind: 'LOAD_CHANGED',
          seq: String(100 + i),
          tick: String(600 + i),
          schema_ver: 1,
          rules_ver: 'test',
          run_id: 'run-1',
          command_id: '',
          request_id: '',
          payload: {
            tick: 600 + i,
            load: { dir_admin: 400 + i, dir_sales: 300, dir_cs: 200, dir_hr: 100 },
          },
        } as Frame)
      }
    })

    // The store took every one of them...
    expect(useRunStore.getState().appliedSeq).toBe(139n)
    expect(useRunStore.getState().load.dir_admin).toBe(439)
    // ...and the panel watching cash never woke up.
    expect(renders.count).toBe(afterGenesis)
  })

  it('does re-render the panel when its own slice moves', () => {
    // The negative of the test above. Without this, "it never re-rendered" would also be
    // satisfied by a panel that is simply broken.
    const { Panel, renders } = makeCountingPanel((state) => state.metrics.cash ?? 0)

    act(() => {
      root = createRoot(host)
      root.render(createElement(Panel))
    })
    const before = renders.count

    act(() => {
      useRunStore.getState().apply(metricsFrame({ seq: 5, cash: 4700 }) as Frame)
    })

    expect(renders.count).toBeGreaterThan(before)
    expect(useRunStore.getState().metrics.cash).toBe(4700)
  })
})

// =========================================================================
// Regressions found in review
// =========================================================================

describe('the capacity tile reads real departments', () => {
  it('does not mistake the genesis load domain for a department map', () => {
    // The genesis payload's `load` is the *ramp's domain* — `{scale, ceiling}` — which is also a
    // map of numbers. A generic "if it is a number map, it is the per-department load" check
    // projected two departments named "scale" and "ceiling" into the capacity tile at 100% each,
    // and left every real department reading zero. The suites missed it because they applied a
    // synthetic LOAD_CHANGED immediately after genesis, overwriting the wrong value.
    useRunStore.getState().apply(genesisFrame() as Frame)

    expect(useRunStore.getState().load).toEqual({})
    expect(Object.keys(useRunStore.getState().load)).not.toContain('scale')
    expect(Object.keys(useRunStore.getState().load)).not.toContain('ceiling')

    // The domain itself is still read — just into the place that describes it.
    expect(useRunStore.getState().genesis?.load).toEqual({ scale: 1000, ceiling: 1000 })
  })

  it('takes the per-department map from a load event', () => {
    useRunStore.getState().apply(genesisFrame() as Frame)
    useRunStore.getState().apply(
      loadFrame({ seq: 9, load: { dir_admin: 900, dir_sales: 400 } }) as Frame,
    )

    expect(useRunStore.getState().load).toEqual({ dir_admin: 900, dir_sales: 400 })
  })
})

describe('the store as a projection', () => {
  it('keeps the assignee across a decision, which names no person', () => {
    // DECISION_RESOLVED carries no `person` — the work returns to whoever already held it. Reading
    // "not named" as "nobody" blanked the assignee on every decision, leaving an item rendering as
    // in-progress with no one on it.
    useRunStore.getState().apply(genesisFrame() as Frame)
    const item = catalogFixture()[0].id

    useRunStore.getState().apply(
      itemFrame({ seq: 20, kind: 'WORK_ASSIGNED', item, status: 'active', person: 'stf_ap' }) as Frame,
    )
    expect(useRunStore.getState().items[item].assignee).toBe('stf_ap')

    // The kernel's DECISION_RESOLVED payload shape: an item and a status, no person.
    useRunStore.getState().apply({
      kind: 'DECISION_RESOLVED',
      seq: '21',
      tick: '700',
      schema_ver: 2,
      rules_ver: 'test',
      run_id: 'run-1',
      command_id: '',
      request_id: '',
      payload: { tick: 700, item, item_status: 'active', cp_index: 0, option_index: 0 },
    } as Frame)

    expect(useRunStore.getState().items[item].assignee).toBe('stf_ap')
    expect(useRunStore.getState().items[item].resolvedCount).toBe(1)
  })

  it('clears the assignee when an item goes back to the backlog', () => {
    // The other half of the rule: backlog genuinely means nobody, and leaving the previous holder
    // in place would render a returned item as still owned.
    useRunStore.getState().apply(genesisFrame() as Frame)
    const item = catalogFixture()[0].id

    useRunStore.getState().apply(
      itemFrame({ seq: 20, kind: 'WORK_ASSIGNED', item, status: 'active', person: 'stf_ap' }) as Frame,
    )
    useRunStore.getState().apply(
      itemFrame({ seq: 21, kind: 'WORK_RETURNED_TO_BACKLOG', item, status: 'backlog' }) as Frame,
    )

    expect(useRunStore.getState().items[item].assignee).toBe('')
  })

  it('does not duplicate a tray entry when a resume replays a raised checkpoint', () => {
    // A resume asks for "everything after N" and can legitimately re-send events the client
    // already holds. Appending blindly both over-counted decision pressure and collided on the
    // React key.
    useRunStore.getState().apply(genesisFrame() as Frame)
    const item = catalogFixture()[0].id

    useRunStore.getState().apply(
      itemFrame({ seq: 30, kind: 'CHECKPOINT_RAISED', item, status: 'blocked', cpIndex: 0 }) as Frame,
    )
    expect(useRunStore.getState().tray).toHaveLength(1)

    // Same checkpoint, later sequence — what a replay across a fork boundary looks like.
    useRunStore.getState().apply(
      itemFrame({ seq: 31, kind: 'CHECKPOINT_RAISED', item, status: 'blocked', cpIndex: 0 }) as Frame,
    )
    expect(useRunStore.getState().tray).toHaveLength(1)
  })

  it('drops an item’s tray entries whenever it stops being blocked', () => {
    // One rule covering reassignment, attrition and a return to the backlog — each of which moves
    // an item out of `blocked` with no DECISION_RESOLVED to clear the tray behind it.
    useRunStore.getState().apply(genesisFrame() as Frame)
    const item = catalogFixture()[0].id

    useRunStore.getState().apply(
      itemFrame({ seq: 30, kind: 'CHECKPOINT_RAISED', item, status: 'blocked' }) as Frame,
    )
    expect(useRunStore.getState().tray).toHaveLength(1)

    useRunStore.getState().apply(
      itemFrame({ seq: 31, kind: 'WORK_REASSIGNED', item, status: 'active' }) as Frame,
    )
    expect(useRunStore.getState().tray).toHaveLength(0)
  })

  it('reads attrition’s returned status from the field named for it', () => {
    // ATTRITION carries `returned_item`, not `item`, so its status field is named to match rather
    // than reusing the six-kind `item_status` convention.
    useRunStore.getState().apply(genesisFrame() as Frame)
    const item = catalogFixture()[0].id

    useRunStore.getState().apply(
      itemFrame({ seq: 30, kind: 'CHECKPOINT_RAISED', item, status: 'blocked' }) as Frame,
    )

    useRunStore.getState().apply({
      kind: 'ATTRITION',
      seq: '31',
      tick: '800',
      schema_ver: 2,
      rules_ver: 'test',
      run_id: 'run-1',
      command_id: '',
      request_id: '',
      payload: {
        tick: 800,
        person: 'stf_ap',
        director: 'dir_admin',
        returned_item: item,
        returned_item_status: 'backlog',
      },
    } as Frame)

    expect(useRunStore.getState().items[item].status).toBe('backlog')
    expect(useRunStore.getState().items[item].assignee).toBe('')
    expect(useRunStore.getState().tray).toHaveLength(0)
  })

  it('survives a malformed sequence instead of throwing out of the socket handler', () => {
    // `BigInt("not-a-number")` throws, and `apply` runs inside the WebSocket message handler — so
    // one bad frame took the whole stream down with no diagnostic and no reconnect.
    useRunStore.getState().apply(genesisFrame() as Frame)
    const before = useRunStore.getState().appliedSeq

    expect(() =>
      useRunStore.getState().apply({
        kind: 'LOAD_CHANGED',
        seq: 'not-a-number',
        tick: '12.5',
        schema_ver: 1,
        rules_ver: 'test',
        run_id: 'run-1',
        command_id: '',
        request_id: '',
        payload: { tick: 600, load: { dir_admin: 400 } },
      } as Frame),
    ).not.toThrow()

    // Unreadable, so it is dropped rather than applied — and the cursor does not move backwards.
    expect(useRunStore.getState().appliedSeq).toBe(before)
  })

  it('refuses a resync that arrives before any genesis, instead of rendering an empty console', () => {
    // A resync carries no genesis. Without one there is no catalog, roster or floor — so
    // reporting the connection as live would render an empty console that reads as a broken
    // client rather than as a client that cannot show this run.
    useRunStore.getState().apply({
      kind: 'RESYNC',
      head_seq: '900',
      state: { metrics: { cash: 4000 } },
    } as unknown as Frame)

    expect(useRunStore.getState().connection).toBe('lost')
    expect(useRunStore.getState().lastError).toContain('genesis')
    expect(useRunStore.getState().genesis).toBeNull()
  })

  it('seeds availability against the visibility the run actually starts at', () => {
    // The kernel emits no ITEM_UNLOCKED for a gate that was never closed, so a gate the run starts
    // above has to read as open from the first frame. Nothing gates below the shipped starting
    // visibility today; this keeps that from being load-bearing.
    useRunStore.getState().apply(genesisFrame() as Frame)

    const items = useRunStore.getState().items
    for (const entry of catalogFixture()) {
      const gate = entry.requires.visibility
      const expected =
        entry.requires.items.length === 0 && (gate === null || 6 >= gate)
      expect(items[entry.id].unlocked, entry.id).toBe(expected)
    }
  })
})
