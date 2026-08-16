import { afterEach, describe, expect, it } from 'vitest'

import { type ControlFrame, useRunStore } from '../src/net/store'
import {
  genesisFixture,
  genesisFrame,
  itemFrame,
  metricsFrame,
  movedFrame,
} from './helpers/frames'

/**
 * What the store believes about the CEO.
 *
 * Three sources, and the distinction between them is the unit under test: genesis says where
 * the run *started*, a resync says where the CEO *is*, and the echo says where the kernel
 * thinks they are right now. Only the first two are a position to draw from; the echo is
 * something to check a prediction against, and writing it into the drawn position would make
 * the CEO jump backwards by a sim-hour of walking every time one arrived.
 */

afterEach(() => {
  useRunStore.getState().reset()
})

function spawn(): [number, number] {
  return (genesisFixture().payload.floor as { spawn: [number, number] }).spawn
}

/** A resync carrying a kernel snapshot, in the shape `simcore.snapshot()` produces. */
function resyncFrame(ceo: { x_milli: number; y_milli: number; facing: string }): ControlFrame {
  return {
    kind: 'RESYNC',
    head_seq: 42,
    state: {
      ceo: { ...ceo, inputs: {} },
      lifecycle: { tick: 900, terminal_reason: '', terminal_tick: 0, outputs: [] },
      metrics: { visibility: 6 },
      items: {},
      people: {},
      capacity: {},
    },
  }
}

describe('seeding the CEO', () => {
  it('starts at the floor spawn genesis carries', () => {
    useRunStore.getState().apply(genesisFrame())

    const [x, y] = spawn()
    expect(useRunStore.getState().ceo).toEqual({
      xMilli: x * 1000,
      yMilli: y * 1000,
      facing: 'down',
    })
  })

  it('takes a resync snapshot position, so attaching mid-run lands the CEO where they are', () => {
    useRunStore.getState().apply(genesisFrame())
    useRunStore.getState().apply(resyncFrame({ x_milli: 12_345, y_milli: 6_789, facing: 'left' }))

    expect(useRunStore.getState().ceo).toEqual({
      xMilli: 12_345,
      yMilli: 6_789,
      facing: 'left',
    })
  })

  it('keeps the spawn position when a resync carries no CEO, rather than zeroing it', () => {
    useRunStore.getState().apply(genesisFrame())

    const before = useRunStore.getState().ceo
    useRunStore.getState().apply({
      kind: 'RESYNC',
      head_seq: 7,
      state: { lifecycle: { tick: 10, terminal_reason: '', terminal_tick: 0, outputs: [] } },
    })

    expect(useRunStore.getState().ceo).toEqual(before)
  })
})

describe('the position echo', () => {
  it('is recorded without moving the position the client draws from', () => {
    useRunStore.getState().apply(genesisFrame())
    const seeded = useRunStore.getState().ceo

    useRunStore.getState().apply({
      kind: 'POSITION_ECHO',
      run_id: 'run-1',
      tick: 600,
      x_milli: 99_000,
      y_milli: 98_000,
    })

    expect(useRunStore.getState().ceoEcho).toEqual({
      xMilli: 99_000,
      yMilli: 98_000,
      tick: 600n,
    })
    expect(useRunStore.getState().ceo).toEqual(seeded)
  })

  it('is null until one arrives, so "no echo yet" and "echo at the origin" are different', () => {
    useRunStore.getState().apply(genesisFrame())
    expect(useRunStore.getState().ceoEcho).toBeNull()
  })

  it('advances the tick, because it is the only regular word the kernel says', () => {
    // Events are appended only on ticks that produce one, and a measured run emits on about
    // five ticks in twelve hundred. Without this the render clock has nothing to chase for
    // hundreds of ticks at a stretch, and every command tagged a few ticks ahead of a stale
    // tick lands in the kernel's past and is rejected.
    useRunStore.getState().apply(genesisFrame())
    expect(useRunStore.getState().tick).toBe(0n)

    useRunStore.getState().apply({
      kind: 'POSITION_ECHO',
      run_id: 'run-1',
      tick: 600,
      x_milli: 1000,
      y_milli: 2000,
    })

    expect(useRunStore.getState().tick).toBe(600n)
  })

  it('never rewinds the tick, since an echo can arrive behind its own batch', () => {
    // The echo is published from inside the kernel's batch while that batch's events are
    // published after it, so an echo can describe a tick the client has already passed.
    useRunStore.getState().apply(genesisFrame())
    useRunStore.getState().apply(
      metricsFrame({ seq: 2, tick: 1080 }),
    )
    expect(useRunStore.getState().tick).toBe(1080n)

    useRunStore.getState().apply({
      kind: 'POSITION_ECHO',
      run_id: 'run-1',
      tick: 600,
      x_milli: 1000,
      y_milli: 2000,
    })

    expect(useRunStore.getState().tick).toBe(1080n)
    // The position is still recorded — only the clock refuses to go backwards.
    expect(useRunStore.getState().ceoEcho?.tick).toBe(600n)
  })

  it('clears on reset, so a second run does not reconcile against the first run position', () => {
    useRunStore.getState().apply(genesisFrame())
    useRunStore.getState().apply({
      kind: 'POSITION_ECHO',
      run_id: 'run-1',
      tick: 600,
      x_milli: 99_000,
      y_milli: 98_000,
    })

    useRunStore.getState().reset()
    expect(useRunStore.getState().ceoEcho).toBeNull()
  })
})

// =========================================================================
// Movement on the wire (M62, R15)
// =========================================================================

/** The hand-off walk `walk.json`'s track is generated from, trimmed to three tiles. */
const WALK = {
  person: 'dir_admin',
  from: [21, 13] as [number, number],
  path: [
    [20, 13],
    [20, 12],
    [20, 11],
  ] as Array<[number, number]>,
  startTick: 100,
}

describe('a walk', () => {
  it('lands as a path and a start tick, not as a position', () => {
    useRunStore.getState().apply(genesisFrame())
    useRunStore.getState().apply(
      movedFrame({ seq: 2, ...WALK, item: 'wi_ap_map', then: 'walking' }),
    )

    const walker = useRunStore.getState().people[WALK.person]
    expect(walker.path).toEqual(WALK.path)
    expect(walker.pathStartTick).toBe(100n)
    expect(walker.state).toBe('walking')
    expect(walker.arrivesIn).toBe('walking')
    // The tile the walk set off from, which the path excludes.
    expect([walker.xMilli, walker.yMilli]).toEqual([21_000, 13_000])
  })

  it('carries the item, which is what makes the org chart progress row reachable', () => {
    useRunStore.getState().apply(genesisFrame())
    expect(useRunStore.getState().people[WALK.person].itemId).toBe('')

    useRunStore.getState().apply(movedFrame({ seq: 2, ...WALK, item: 'wi_ap_map' }))

    expect(useRunStore.getState().people[WALK.person].itemId).toBe('wi_ap_map')
  })

  it('replaces the previous path rather than merging with it', () => {
    // The interruption that happens: a reassignment mid-walk emits a fresh path from wherever
    // the walker stopped. Keeping any of the old one would leave them heading for a desk they
    // never reach, which is the walk the client would still be drawing.
    useRunStore.getState().apply(genesisFrame())
    useRunStore.getState().apply(movedFrame({ seq: 2, ...WALK, item: 'wi_ap_map' }))

    const replacement: Array<[number, number]> = [
      [22, 13],
      [23, 13],
    ]
    useRunStore.getState().apply(
      movedFrame({
        seq: 3,
        person: WALK.person,
        from: [20, 12],
        path: replacement,
        startTick: 140,
        item: 'wi_close',
        then: 'working',
      }),
    )

    const walker = useRunStore.getState().people[WALK.person]
    expect(walker.path).toEqual(replacement)
    expect(walker.pathStartTick).toBe(140n)
    expect([walker.xMilli, walker.yMilli]).toEqual([20_000, 12_000])
    expect(walker.arrivesIn).toBe('working')
  })

  it('is ignored for somebody this run has never heard of', () => {
    // A frame for another run rather than a person to invent: a half-built `PersonView` with no
    // seat would be drawn standing in a corner of the office.
    useRunStore.getState().apply(genesisFrame())
    const before = useRunStore.getState().people

    useRunStore.getState().apply(movedFrame({ seq: 2, ...WALK, person: 'nobody' }))

    expect(useRunStore.getState().people).toBe(before)
  })

  it('drops a malformed tile rather than taking the stream down', () => {
    // Every reader in the store is total, because this runs inside the WebSocket message
    // handler: a throw would escape `apply` and lose the stream over one bad frame.
    useRunStore.getState().apply(genesisFrame())
    const frame = movedFrame({ seq: 2, ...WALK })
    frame.payload.path = [[20, 13], 'not a tile', [20, 11]]

    expect(() => useRunStore.getState().apply(frame)).not.toThrow()
    expect(useRunStore.getState().people[WALK.person].path).toEqual([
      [20, 13],
      [20, 11],
    ])
  })
})

describe('who is carrying what', () => {
  it('follows an assignment, and lets go when the deliverable lands', () => {
    useRunStore.getState().apply(genesisFrame())
    useRunStore.getState().apply(
      itemFrame({ seq: 2, kind: 'WORK_ASSIGNED', item: 'wi_ap_map', status: 'active' }),
    )
    expect(useRunStore.getState().people.stf_ap.itemId).toBe('wi_ap_map')

    useRunStore.getState().apply({
      ...itemFrame({ seq: 3, kind: 'DELIVERABLE_PRODUCED', item: 'wi_ap_map', status: 'done' }),
      payload: {
        tick: 900,
        item: 'wi_ap_map',
        item_status: 'done',
        person: 'stf_ap',
        deliverable: { item: 'wi_ap_map', title: 'A map', kind: 'doc', dept: 'admin', day: 2 },
      },
    })

    // Otherwise the row would read 100% for the rest of the run.
    expect(useRunStore.getState().people.stf_ap.itemId).toBe('')
  })

  it('moves with a reassignment, so two rows never claim one item', () => {
    useRunStore.getState().apply(genesisFrame())
    useRunStore.getState().apply(
      itemFrame({ seq: 2, kind: 'WORK_ASSIGNED', item: 'wi_ap_map', status: 'active' }),
    )
    useRunStore.getState().apply({
      ...itemFrame({ seq: 3, kind: 'WORK_REASSIGNED', item: 'wi_ap_map', status: 'assigned' }),
      payload: {
        tick: 300,
        item: 'wi_ap_map',
        item_status: 'assigned',
        from: 'stf_ap',
        to: 'dir_admin',
      },
    })

    expect(useRunStore.getState().people.stf_ap.itemId).toBe('')
    expect(useRunStore.getState().people.dir_admin.itemId).toBe('wi_ap_map')
  })

  it('lets the director go once the hand-off completes', () => {
    // The kernel clears the director's `item_id` on the tick they arrive, and this is the event
    // that says so. Without it a director who delegated would keep the work on their row.
    useRunStore.getState().apply(genesisFrame())
    useRunStore.getState().apply(movedFrame({ seq: 2, ...WALK, item: 'wi_ap_map', then: 'walking' }))
    expect(useRunStore.getState().people.dir_admin.itemId).toBe('wi_ap_map')

    useRunStore.getState().apply({
      ...itemFrame({ seq: 3, kind: 'WORK_ASSIGNED', item: 'wi_ap_map', status: 'active' }),
      payload: {
        tick: 268,
        item: 'wi_ap_map',
        item_status: 'active',
        person: 'stf_ap',
        via: 'dir_admin',
        handoff_completed: true,
      },
    })

    expect(useRunStore.getState().people.dir_admin.itemId).toBe('')
    expect(useRunStore.getState().people.stf_ap.itemId).toBe('wi_ap_map')
  })
})

describe('attaching mid-walk', () => {
  it('takes the in-flight path from the resync snapshot rather than the desk', () => {
    // The reconnect that must not teleport anybody. `simcore.snapshot()` carries `path` and
    // `path_start_tick` because the kernel's own resume needs them; reading them here is what
    // makes a client that attaches mid-walk join the walk.
    useRunStore.getState().apply(genesisFrame())
    useRunStore.getState().apply({
      kind: 'RESYNC',
      head_seq: 90,
      state: {
        ceo: { x_milli: 1000, y_milli: 1000, facing: 'down', inputs: {} },
        lifecycle: { tick: 140, terminal_reason: '', terminal_tick: 0, outputs: [] },
        metrics: {},
        items: {},
        capacity: {},
        people: {
          dir_admin: {
            // Two tiles in: the kernel advances `pos` as the walk progresses.
            pos: [20, 12],
            facing: 'up',
            state: 'walking',
            item: 'wi_ap_map',
            cp: -1,
            met_ticks: 0,
            path: WALK.path,
            path_start_tick: 100,
            arrive: 'handoff',
            arrive_item: 'wi_ap_map',
            bypassed_director: false,
            answered: [],
          },
        },
      },
    })

    const walker = useRunStore.getState().people.dir_admin
    expect(walker.state).toBe('walking')
    expect(walker.path).toEqual(WALK.path)
    expect(walker.pathStartTick).toBe(100n)
    expect(walker.itemId).toBe('wi_ap_map')
    // Their current tile, not their desk: the walk is joined from where it has got to.
    expect([walker.xMilli, walker.yMilli]).toEqual([20_000, 12_000])
  })

  it('leaves a standing person with no path, so nothing interpolates from nowhere', () => {
    useRunStore.getState().apply(genesisFrame())
    useRunStore.getState().apply(resyncFrame({ x_milli: 0, y_milli: 0, facing: 'down' }))

    for (const person of Object.values(useRunStore.getState().people)) {
      expect(person.path).toEqual([])
    }
  })
})

describe('the clock only moves forward', () => {
  it('will not let an event rewind the tick either', () => {
    // The mirror of the echo case: the echo is published from inside the kernel's batch while
    // that batch's events are published after it, so an event can arrive carrying a tick the
    // echo has already passed.
    useRunStore.getState().apply(genesisFrame())
    useRunStore.getState().apply({
      kind: 'POSITION_ECHO',
      run_id: 'run-1',
      tick: 900,
      x_milli: 1000,
      y_milli: 2000,
    })
    expect(useRunStore.getState().tick).toBe(900n)

    useRunStore.getState().apply(metricsFrame({ seq: 2, tick: 600 }))

    expect(useRunStore.getState().tick).toBe(900n)
    // The event still applied — only the clock refused to go backwards.
    expect(useRunStore.getState().appliedSeq).toBe(2n)
  })
})
