import { afterEach, describe, expect, it } from 'vitest'

import { type ControlFrame, useRunStore } from '../src/net/store'
import { genesisFixture, genesisFrame, metricsFrame } from './helpers/frames'

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
