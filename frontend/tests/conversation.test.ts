import { afterEach, describe, expect, it } from 'vitest'

import type { PersonView } from '../src/net/store'
import {
  CLOSE_RADIUS_MILLI,
  OPEN_RADIUS_MILLI,
  SWITCH_MARGIN_MILLI,
  conversationHeader,
  distanceMilli,
  selectConversation,
} from '../src/ui/conversation-model'
import { useRunStore } from './helpers/store-helpers'
import { genesisFixture, genesisFrame } from './helpers/frames'

/**
 * The conversation's rules, asserted without a panel.
 *
 * Proximity *is* the interface here — no click, no keypress, being near someone is the whole
 * gesture — so who is in range is the mechanic rather than a detail of presentation, and it is
 * worth asserting directly. What a rendered card looks like is judged by eye; what opens it is
 * not.
 */

afterEach(() => {
  useRunStore.getState().reset()
})

/** A person standing at a position, with everything else at its resting default. */
function person(id: string, xMilli: number, yMilli: number, over: Partial<PersonView> = {}): PersonView {
  return {
    id,
    xMilli,
    yMilli,
    facing: 'up',
    state: 'idle',
    itemId: '',
    waiting: false,
    ...over,
  }
}

function floorOf(people: PersonView[]): Record<string, PersonView> {
  return Object.fromEntries(people.map((entry) => [entry.id, entry]))
}

const ORIGIN = { xMilli: 0, yMilli: 0 }

// =========================================================================
// R5: proximity opens and closes it, and nothing else does
// =========================================================================

describe('who the conversation is with', () => {
  it('opens with the nearest person once inside the open radius (R5, AE1)', () => {
    const people = floorOf([person('stf_ap', OPEN_RADIUS_MILLI - 100, 0)])

    expect(selectConversation(ORIGIN, people, null)).toBe('stf_ap')
  })

  it('stays closed while everyone is out of range (AE1)', () => {
    const people = floorOf([person('stf_ap', OPEN_RADIUS_MILLI + 1, 0)])

    expect(selectConversation(ORIGIN, people, null)).toBeNull()
  })

  it('closes once the person passes the close radius (R5, AE1)', () => {
    const people = floorOf([person('stf_ap', CLOSE_RADIUS_MILLI + 1, 0)])

    expect(selectConversation(ORIGIN, people, 'stf_ap')).toBeNull()
  })

  it('holds an open conversation between the two radii, so a boundary does not flicker', () => {
    // The whole point of two radii: at this distance the conversation is too far to *open* and
    // near enough to *keep*. With one radius, a single sub-tile step would open and close it.
    const between = (OPEN_RADIUS_MILLI + CLOSE_RADIUS_MILLI) / 2
    const people = floorOf([person('stf_ap', between, 0)])

    expect(selectConversation(ORIGIN, people, 'stf_ap')).toBe('stf_ap')
    expect(selectConversation(ORIGIN, people, null)).toBeNull()
  })

  it('opens with nobody when the floor is empty', () => {
    expect(selectConversation(ORIGIN, {}, null)).toBeNull()
  })

  it('closes when the person walks away, not only when the CEO does', () => {
    // Staff walk to desks and to meetings. A conversation the CEO is standing still in must
    // still end when the other party leaves.
    const near = floorOf([person('stf_ap', 500, 0)])
    expect(selectConversation(ORIGIN, near, 'stf_ap')).toBe('stf_ap')

    const gone = floorOf([person('stf_ap', CLOSE_RADIUS_MILLI + 500, 0)])
    expect(selectConversation(ORIGIN, gone, 'stf_ap')).toBeNull()
  })

  it('closes when the person is no longer on the floor at all', () => {
    expect(selectConversation(ORIGIN, {}, 'stf_ap')).toBeNull()
  })
})

// =========================================================================
// Two people at neighbouring desks
// =========================================================================

describe('choosing between two people', () => {
  it('does not swap until the second is nearer by the switch margin', () => {
    const held = person('stf_ap', 1000, 0)
    // Nearer, but not by enough. Two adjacent desks are often this close together, and without
    // the margin the panel trades between them as the CEO shifts.
    const rival = person('stf_buyer', 1000 - SWITCH_MARGIN_MILLI + 100, 0)

    expect(selectConversation(ORIGIN, floorOf([held, rival]), 'stf_ap')).toBe('stf_ap')
  })

  it('swaps once the second is nearer by more than the margin', () => {
    const held = person('stf_ap', 1500, 0)
    const rival = person('stf_buyer', 1500 - SWITCH_MARGIN_MILLI - 100, 0)

    expect(selectConversation(ORIGIN, floorOf([held, rival]), 'stf_ap')).toBe('stf_buyer')
  })

  it('will not hand the conversation to someone outside the open radius', () => {
    // Nearer than the person being held, but still too far to have opened one — so holding on
    // is right, and swapping would open a conversation proximity never earned.
    const held = person('stf_ap', CLOSE_RADIUS_MILLI - 50, 0)
    const rival = person('stf_buyer', OPEN_RADIUS_MILLI + 100, 0)

    expect(selectConversation(ORIGIN, floorOf([held, rival]), 'stf_ap')).toBe('stf_ap')
  })

  it('opens the next conversation when walking from one desk straight to another', () => {
    // The held person is out of range and someone else is in it: the panel should follow the
    // CEO rather than closing and waiting for another step.
    const left = person('stf_ap', CLOSE_RADIUS_MILLI + 500, 0)
    const arrived = person('stf_buyer', 400, 0)

    expect(selectConversation(ORIGIN, floorOf([left, arrived]), 'stf_ap')).toBe('stf_buyer')
  })

  it('picks the nearest of several in range', () => {
    const people = floorOf([
      person('stf_ap', 1800, 0),
      person('stf_buyer', 300, 0),
      person('stf_cs', 900, 0),
    ])

    expect(selectConversation(ORIGIN, people, null)).toBe('stf_buyer')
  })

  it('measures on both axes, not one', () => {
    const diagonal = floorOf([person('stf_ap', 1500, 1500)])
    expect(distanceMilli(ORIGIN, { xMilli: 1500, yMilli: 1500 })).toBeGreaterThan(1500)
    // 2121 milli-tiles away: within the close radius but too far to open.
    expect(selectConversation(ORIGIN, diagonal, null)).toBeNull()
    expect(selectConversation(ORIGIN, diagonal, 'stf_ap')).toBe('stf_ap')
  })
})

// =========================================================================
// R6: the conversation names a person
// =========================================================================

describe('what the conversation says about them', () => {
  it('names the person, their title, their department and their state (R6)', () => {
    useRunStore.getState().apply(genesisFrame())
    const state = useRunStore.getState()

    const header = conversationHeader('stf_ap', state.genesis?.roster ?? {}, state.people)

    expect(header).not.toBeNull()
    expect(header?.name).toBe('Priya Raman')
    expect(header?.title).toBe('Accounts Payable')
    // The room she sits in, which is deliberately not the line she reports to.
    expect(header?.dept).toBe('accounting')
    expect(header?.stateLabel).toBe('Free right now')
  })

  it('carries a name and a title for every person on the roster', () => {
    const roster = genesisFixture().payload.roster as Record<
      string,
      { name?: string; title?: string; initials?: string }
    >

    for (const [id, entry] of Object.entries(roster)) {
      expect(entry.name, `${id} has no name`).toBeTruthy()
      expect(entry.title, `${id} has no title`).toBeTruthy()
      expect(entry.initials, `${id} has no initials`).toBeTruthy()
    }
  })

  it('re-renders into the stopped shape when the person becomes blocked, without reopening', () => {
    useRunStore.getState().apply(genesisFrame())
    const roster = useRunStore.getState().genesis?.roster ?? {}

    const free = conversationHeader('stf_ap', roster, floorOf([person('stf_ap', 0, 0)]))
    expect(free?.waiting).toBe(false)
    expect(free?.stateLabel).toBe('Free right now')

    const stopped = conversationHeader(
      'stf_ap',
      roster,
      floorOf([person('stf_ap', 0, 0, { state: 'blocked', waiting: true, itemId: 'w_ap' })]),
    )
    // Same person, same conversation: only the card under the header changes.
    expect(stopped?.id).toBe(free?.id)
    expect(stopped?.waiting).toBe(true)
    expect(stopped?.stateLabel).toBe('Waiting on your decision')
  })

  it('is null rather than a placeholder for a person the run does not have', () => {
    useRunStore.getState().apply(genesisFrame())
    const state = useRunStore.getState()

    expect(conversationHeader('nobody', state.genesis?.roster ?? {}, state.people)).toBeNull()
    expect(conversationHeader(null, state.genesis?.roster ?? {}, state.people)).toBeNull()
  })

  it('puts every kernel person state into words', () => {
    useRunStore.getState().apply(genesisFrame())
    const roster = useRunStore.getState().genesis?.roster ?? {}

    for (const state of ['idle', 'working', 'blocked', 'walking', 'meeting']) {
      const header = conversationHeader(
        'stf_ap',
        roster,
        floorOf([person('stf_ap', 0, 0, { state })]),
      )
      // Never the raw kernel token, which is what a missing label would fall through to.
      expect(header?.stateLabel).not.toBe(state)
    }
  })
})

// =========================================================================
// R11, R24: the clock is not a party to the conversation
// =========================================================================

describe('the conversation and the clock', () => {
  it('leaves the rate alone when one opens (R11, AE9)', () => {
    useRunStore.getState().apply(genesisFrame())
    const before = useRunStore.getState().rate

    selectConversation(ORIGIN, floorOf([person('stf_ap', 100, 0)]), null)

    expect(useRunStore.getState().rate).toBe(before)
  })

  it('still names someone in range at rate zero, so a paused conversation stays readable (R24, AE10)', () => {
    useRunStore.getState().apply(genesisFrame())
    useRunStore.getState().apply({
      kind: 'RATE_CHANGED',
      seq: '99',
      tick: '600',
      schema_ver: 1,
      rules_ver: 'test',
      run_id: 'run-1',
      command_id: '',
      request_id: '',
      payload: { tick: 600, rate: 0 },
    })
    expect(useRunStore.getState().rate).toBe(0)

    const state = useRunStore.getState()
    const people = floorOf([person('stf_ap', 100, 0)])
    expect(selectConversation(ORIGIN, people, 'stf_ap')).toBe('stf_ap')
    expect(conversationHeader('stf_ap', state.genesis?.roster ?? {}, people)).not.toBeNull()
  })
})
