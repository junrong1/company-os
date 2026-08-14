import { afterEach, describe, expect, it } from 'vitest'

import type { EventFrame, PersonView, RosterEntry } from '../src/net/store'
import {
  CLOSE_RADIUS_MILLI,
  FROM_TRAY_COST,
  askPayload,
  askable,
  assignCost,
  assignableWork,
  IN_PERSON_COST,
  OPEN_RADIUS_MILLI,
  SWITCH_MARGIN_MILLI,
  conversationHeader,
  distanceMilli,
  personActivity,
  resolvePayload,
  selectConversation,
  stoppedCard,
  tacitKey,
} from '../src/ui/conversation-model'
import { actorsFromStore, typingTarget } from '../src/ui/stage'
import { useRunStore } from './helpers/store-helpers'
import {
  catalogFixture,
  genesisFixture,
  genesisFrame,
  itemFrame,
  metricsFrame,
} from './helpers/frames'

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

// =========================================================================
// R7-R10: deciding in person, and what the tray must never show
// =========================================================================

const RAISED_TACIT = 'The rule ignores value. I collect three quotes for a thousand-dollar desk.'

/** A CHECKPOINT_RAISED frame, as the kernel now sends it. */
function raisedFrame(options: {
  seq: number
  item: string
  person: string
  cpIndex?: number
  tacit?: string
}): EventFrame {
  return {
    kind: 'CHECKPOINT_RAISED',
    seq: String(options.seq),
    tick: '600',
    schema_ver: 3,
    rules_ver: 'test',
    run_id: 'run-1',
    command_id: '',
    request_id: '',
    payload: {
      tick: 600,
      item: options.item,
      item_status: 'blocked',
      person: options.person,
      cp_index: options.cpIndex ?? 0,
      label: 'Approval',
      kind: 'approval',
      tacit: options.tacit ?? RAISED_TACIT,
    },
  }
}

/** The first catalogue item that actually has a checkpoint to stop at. */
function itemWithCheckpoint(): { id: string; want: string } {
  const entry = catalogFixture().find((candidate) => candidate.checkpoints.length > 0)
  if (entry === undefined) throw new Error('no authored item has a checkpoint')
  return { id: entry.id, want: entry.want }
}

describe('deciding in person', () => {
  it('shows the prompt, the options and the line said only in person (R7)', () => {
    const { id, want } = itemWithCheckpoint()
    useRunStore.getState().apply(genesisFrame())
    useRunStore.getState().apply(raisedFrame({ seq: 2, item: id, person: want }))

    const state = useRunStore.getState()
    const card = stoppedCard(want, state.tray, state.genesis?.catalog ?? [], state.tacitLines)

    expect(card).not.toBeNull()
    expect(card?.prompt).toBeTruthy()
    expect(card?.options.length).toBeGreaterThan(0)
    expect(card?.tacit).toBe(RAISED_TACIT)
  })

  it('states its cost before the choice is made (R10)', () => {
    const { id, want } = itemWithCheckpoint()
    useRunStore.getState().apply(genesisFrame())
    useRunStore.getState().apply(raisedFrame({ seq: 2, item: id, person: want }))

    const state = useRunStore.getState()
    const card = stoppedCard(want, state.tray, state.genesis?.catalog ?? [], state.tacitLines)

    expect(card?.cost).toBe(IN_PERSON_COST)
    // Both routes say what they cost, and they do not say the same thing.
    expect(FROM_TRAY_COST).not.toBe(IN_PERSON_COST)
  })

  it('sends the in-person flag, and the tray sends it unset (R9, AE2, AE3)', () => {
    expect(resolvePayload('wi_ap_map', 0, 1, true)).toEqual({
      item: 'wi_ap_map',
      cp_index: 0,
      option_index: 1,
      in_person: true,
    })
    expect(resolvePayload('wi_ap_map', 0, 1, false).in_person).toBe(false)
  })

  it('offers no card for a person who is not stopped', () => {
    useRunStore.getState().apply(genesisFrame())
    const state = useRunStore.getState()

    expect(stoppedCard('stf_ap', state.tray, state.genesis?.catalog ?? [], {})).toBeNull()
    expect(stoppedCard(null, state.tray, state.genesis?.catalog ?? [], {})).toBeNull()
  })

  it('picks the decision belonging to this person, not the first one waiting', () => {
    const catalogue = catalogFixture().filter((entry) => entry.checkpoints.length > 0)
    // Loud rather than skipped, matching the fixture helper's own rule: a test that returns
    // early when its precondition lapses reports green while checking nothing.
    expect(catalogue.length).toBeGreaterThanOrEqual(2)

    useRunStore.getState().apply(genesisFrame())
    useRunStore
      .getState()
      .apply(raisedFrame({ seq: 2, item: catalogue[0].id, person: 'stf_ap' }))
    useRunStore
      .getState()
      .apply(raisedFrame({ seq: 3, item: catalogue[1].id, person: 'stf_buyer' }))

    const state = useRunStore.getState()
    const card = stoppedCard('stf_buyer', state.tray, state.genesis?.catalog ?? [], state.tacitLines)

    expect(card?.itemId).toBe(catalogue[1].id)
  })

  it('renders the decision even when the kernel sent no tacit line for it', () => {
    const { id, want } = itemWithCheckpoint()
    useRunStore.getState().apply(genesisFrame())
    useRunStore.getState().apply(raisedFrame({ seq: 2, item: id, person: want, tacit: '' }))

    const state = useRunStore.getState()
    const card = stoppedCard(want, state.tray, state.genesis?.catalog ?? [], state.tacitLines)

    // A missing line is a missing line, not a reason to hide the decision.
    expect(card).not.toBeNull()
    expect(card?.tacit).toBe('')
    expect(card?.options.length).toBeGreaterThan(0)
  })
})

// =========================================================================
// R8: the line is unreachable from anywhere but the floor
// =========================================================================

describe('what the tray can and cannot reach', () => {
  it('keeps the tacit line off the tray entries entirely (R8, AE3)', () => {
    const { id, want } = itemWithCheckpoint()
    useRunStore.getState().apply(genesisFrame())
    useRunStore.getState().apply(raisedFrame({ seq: 2, item: id, person: want }))

    const state = useRunStore.getState()
    expect(state.tray).toHaveLength(1)

    // Structural, not a convention: the tray renders from these entries, so a tacit line the
    // entry does not carry is a line the tray cannot leak however its card is later edited.
    for (const entry of state.tray) {
      expect(Object.values(entry).map(String).join(' ')).not.toContain(RAISED_TACIT)
      expect('tacit' in entry).toBe(false)
    }
  })

  it('keeps it out of the genesis catalog, so it is not on the wire before it is earned (R8)', () => {
    for (const entry of catalogFixture()) {
      for (const checkpoint of entry.checkpoints) {
        expect('tacit' in checkpoint).toBe(false)
      }
    }
  })

  it('holds it where only the conversation looks', () => {
    const { id, want } = itemWithCheckpoint()
    useRunStore.getState().apply(genesisFrame())
    useRunStore.getState().apply(raisedFrame({ seq: 2, item: id, person: want }))

    expect(useRunStore.getState().tacitLines[tacitKey(id, 0)]).toBe(RAISED_TACIT)
  })
})

// =========================================================================
// R20, R21, R22: being summoned
// =========================================================================

describe('being summoned to a decision', () => {
  it('puts the person in the tray and marks them waiting on the floor (R20, R21)', () => {
    const { id, want } = itemWithCheckpoint()
    useRunStore.getState().apply(genesisFrame())
    useRunStore.getState().apply(raisedFrame({ seq: 2, item: id, person: want }))

    const state = useRunStore.getState()
    expect(state.tray.map((entry) => entry.personId)).toContain(want)
    expect(state.items[id].status).toBe('blocked')
  })

  it('leaves progress where it was while the decision is unresolved (R22, AE9)', () => {
    const { id, want } = itemWithCheckpoint()
    useRunStore.getState().apply(genesisFrame())
    useRunStore.getState().apply(raisedFrame({ seq: 2, item: id, person: want }))
    const stalled = useRunStore.getState().items[id].doneUnits

    // Time passes — a day's costs land — with nobody deciding anything.
    useRunStore.getState().apply(metricsFrame({ seq: 3, tick: 1080 }))

    expect(useRunStore.getState().items[id].doneUnits).toBe(stalled)
    expect(useRunStore.getState().items[id].status).toBe('blocked')
    expect(useRunStore.getState().tick).toBe(1080n)
  })

  it('clears the tray entry once the item is no longer blocked', () => {
    const { id, want } = itemWithCheckpoint()
    useRunStore.getState().apply(genesisFrame())
    useRunStore.getState().apply(raisedFrame({ seq: 2, item: id, person: want }))
    expect(useRunStore.getState().tray).toHaveLength(1)

    useRunStore.getState().apply(
      itemFrame({ seq: 3, kind: 'DECISION_RESOLVED', item: id, status: 'active', person: want }),
    )

    expect(useRunStore.getState().tray).toHaveLength(0)
  })
})

// =========================================================================
// R12, R13, R14: handing work over from inside the conversation
// =========================================================================

/** Item states as the store holds them, defaulting everything to available backlog. */
function backlog(over: Record<string, { status?: string; unlocked?: boolean }> = {}) {
  const items: Record<string, { status: string; unlocked: boolean }> = {}
  for (const entry of catalogFixture()) {
    items[entry.id] = {
      status: over[entry.id]?.status ?? 'backlog',
      unlocked: over[entry.id]?.unlocked ?? true,
    }
  }
  return items
}

function rosterOf(): Record<string, RosterEntry> {
  useRunStore.getState().apply(genesisFrame())
  return useRunStore.getState().genesis?.roster ?? {}
}

/** A specialist who reports to somebody, and one item that wants them. */
function aSpecialistWithWork(roster: Record<string, RosterEntry>) {
  const entry = catalogFixture().find((candidate) => (roster[candidate.want]?.mgr ?? '') !== '')
  if (entry === undefined) throw new Error('no authored item wants a person with a director')
  return { itemId: entry.id, personId: entry.want, director: roster[entry.want].mgr }
}

describe('handing work over in person', () => {
  it('lists the unlocked backlog work this person could take (R12)', () => {
    const roster = rosterOf()
    const { itemId, personId } = aSpecialistWithWork(roster)

    const offers = assignableWork(personId, roster, backlog(), catalogFixture())

    expect(offers.map((offer) => offer.itemId)).toContain(itemId)
    // Everything offered is genuinely for them or for one of their reports.
    for (const offer of offers) {
      expect(offer.wantId === personId || roster[offer.wantId].mgr === personId).toBe(true)
    }
  })

  it('offers nothing locked and nothing already assigned (R12)', () => {
    const roster = rosterOf()
    const { itemId, personId } = aSpecialistWithWork(roster)

    const locked = assignableWork(
      personId,
      roster,
      backlog({ [itemId]: { unlocked: false } }),
      catalogFixture(),
    )
    expect(locked.map((offer) => offer.itemId)).not.toContain(itemId)

    const taken = assignableWork(
      personId,
      roster,
      backlog({ [itemId]: { status: 'active' } }),
      catalogFixture(),
    )
    expect(taken.map((offer) => offer.itemId)).not.toContain(itemId)
  })

  it('records the director as uninformed when handed straight to a specialist (R13, AE6)', () => {
    const roster = rosterOf()
    const { itemId, personId, director } = aSpecialistWithWork(roster)

    const offer = assignableWork(personId, roster, backlog(), catalogFixture()).find(
      (candidate) => candidate.itemId === itemId,
    )

    expect(offer?.bypassesDirector).toBe(true)
    expect(offer?.uninformed).toBe(director)
    expect(offer?.payload).toEqual({ item: itemId, person: personId, via_manager: false })
  })

  it('does not bypass anyone when routed through the director (R13)', () => {
    const roster = rosterOf()
    const { itemId, director } = aSpecialistWithWork(roster)

    const offer = assignableWork(director, roster, backlog(), catalogFixture()).find(
      (candidate) => candidate.itemId === itemId,
    )

    expect(offer?.bypassesDirector).toBe(false)
    expect(offer?.uninformed).toBe('')
    // No person named: the kernel resolves the line from the item's own `want`, and naming one
    // here would be a second opinion about the org chart.
    expect(offer?.payload).toEqual({ item: itemId, via_manager: true })
  })

  it("offers a director the work wanting any of their reports", () => {
    const roster = rosterOf()
    const { director } = aSpecialistWithWork(roster)

    const offers = assignableWork(director, roster, backlog(), catalogFixture())
    const reports = Object.entries(roster)
      .filter(([, entry]) => entry.mgr === director)
      .map(([id]) => id)

    const routed = offers.filter((offer) => offer.wantId !== director)
    expect(routed.length).toBeGreaterThan(0)
    for (const offer of routed) expect(reports).toContain(offer.wantId)
  })

  it('bypasses nobody when the item wants a director outright', () => {
    const roster = rosterOf()
    const entry = catalogFixture().find((candidate) => (roster[candidate.want]?.mgr ?? '') === '')
    // Loud rather than skipped: without an item wanting a director, this test asserts nothing.
    expect(entry, 'no authored item wants a director outright').toBeDefined()
    if (entry === undefined) return

    const offer = assignableWork(entry.want, roster, backlog(), catalogFixture()).find(
      (candidate) => candidate.itemId === entry.id,
    )

    // There is nobody above them to go around.
    expect(offer?.bypassesDirector).toBe(false)
    expect(offer?.payload).toEqual({ item: entry.id, person: entry.want, via_manager: false })
  })

  it('offers nothing for somebody who is not on the roster', () => {
    const roster = rosterOf()

    expect(assignableWork('nobody', roster, backlog(), catalogFixture())).toEqual([])
    expect(assignableWork(null, roster, backlog(), catalogFixture())).toEqual([])
  })

  it('picks up an item that unlocks while the conversation is open', () => {
    const roster = rosterOf()
    const { itemId, personId } = aSpecialistWithWork(roster)

    const before = assignableWork(
      personId,
      roster,
      backlog({ [itemId]: { unlocked: false } }),
      catalogFixture(),
    )
    expect(before.map((offer) => offer.itemId)).not.toContain(itemId)

    // The same conversation, one visibility gain later.
    const after = assignableWork(personId, roster, backlog(), catalogFixture())
    expect(after.map((offer) => offer.itemId)).toContain(itemId)
  })

  it('says what each route costs before the hand-over (R10, R13)', () => {
    const roster = rosterOf()
    const { itemId, personId, director } = aSpecialistWithWork(roster)

    const direct = assignableWork(personId, roster, backlog(), catalogFixture()).find(
      (candidate) => candidate.itemId === itemId,
    )
    const routed = assignableWork(director, roster, backlog(), catalogFixture()).find(
      (candidate) => candidate.itemId === itemId,
    )

    expect(assignCost(direct!)).toContain(director)
    expect(assignCost(direct!)).not.toBe(assignCost(routed!))
  })

  it('sends the same payloads the work panel sends, so both routes stay one rule (R14)', () => {
    const roster = rosterOf()
    const { itemId, personId, director } = aSpecialistWithWork(roster)

    const direct = assignableWork(personId, roster, backlog(), catalogFixture()).find(
      (candidate) => candidate.itemId === itemId,
    )
    const routed = assignableWork(director, roster, backlog(), catalogFixture()).find(
      (candidate) => candidate.itemId === itemId,
    )

    // The work panel's own two buttons, verbatim.
    expect(direct?.payload).toEqual({ item: itemId, person: personId, via_manager: false })
    expect(routed?.payload).toEqual({ item: itemId, via_manager: true })
  })
})

// =========================================================================
// R15-R19: the ask box
// =========================================================================

/** A QUESTION_ANSWERED frame, as the kernel sends it. */
function answeredFrame(options: {
  seq: number
  person: string
  asked: string
  question?: string
  label?: string
  answer: string
  tacit?: boolean
  firstTime?: boolean
  tick?: number
  visibility?: number
}): EventFrame {
  const matched = (options.question ?? '') !== ''
  return {
    kind: 'QUESTION_ANSWERED',
    seq: String(options.seq),
    tick: String(options.tick ?? 600),
    schema_ver: 1,
    rules_ver: 'test',
    run_id: 'run-1',
    command_id: '',
    request_id: '',
    payload: {
      tick: options.tick ?? 600,
      person: options.person,
      asked: options.asked,
      matched,
      question: options.question ?? '',
      label: options.label ?? '',
      answer: options.answer,
      tacit: options.tacit ?? false,
      first_time: options.firstTime ?? false,
      deltas: {},
      metrics: { cash: 4800, manualHours: 340, leadTime: 12, morale: 72, visibility: options.visibility ?? 6 },
    },
  }
}

describe('asking a question', () => {
  it('sends the typed text, not a matched intent (R15)', () => {
    // Matching lives in the kernel, which is the side that charges for the answer.
    expect(askPayload('stf_ap', 'why does it work that way?')).toEqual({
      person: 'stf_ap',
      question: 'why does it work that way?',
    })
  })

  it('will not send an empty question', () => {
    expect(askable('')).toBe(false)
    expect(askable('   ')).toBe(false)
    expect(askable('why')).toBe(true)
  })

  it('keeps what a person said, newest first (R18)', () => {
    useRunStore.getState().apply(genesisFrame())
    useRunStore.getState().apply(
      answeredFrame({ seq: 2, person: 'stf_ap', asked: 'why?', question: 'why', label: 'Why', answer: 'First answer', tacit: true, firstTime: true }),
    )
    useRunStore.getState().apply(
      answeredFrame({ seq: 3, person: 'stf_ap', asked: 'exceptions?', question: 'exception', label: 'Exceptions', answer: 'Second answer', tacit: true, firstTime: true }),
    )

    const said = useRunStore.getState().answers.stf_ap
    expect(said.map((entry) => entry.answer)).toEqual(['Second answer', 'First answer'])
  })

  it('keeps each person answers separate, because the knowledge is theirs (R18)', () => {
    useRunStore.getState().apply(genesisFrame())
    useRunStore.getState().apply(
      answeredFrame({ seq: 2, person: 'stf_ap', asked: 'why?', question: 'why', label: 'Why', answer: 'Priya reason' }),
    )

    expect(useRunStore.getState().answers.stf_ap).toHaveLength(1)
    expect(useRunStore.getState().answers.stf_buyer).toBeUndefined()
  })

  it('renders a deflection as something they said, not as an error (R16)', () => {
    useRunStore.getState().apply(genesisFrame())
    useRunStore.getState().apply(
      answeredFrame({ seq: 2, person: 'stf_ap', asked: 'the weather?', answer: 'That is above my desk.' }),
    )

    const [said] = useRunStore.getState().answers.stf_ap
    expect(said.question).toBe('')
    expect(said.answer).toBe('That is above my desk.')
    expect(said.tacit).toBe(false)
    expect(said.firstTime).toBe(false)
  })

  it('marks a first tacit answer, which is what the mechanic exists for (R17)', () => {
    useRunStore.getState().apply(genesisFrame())
    useRunStore.getState().apply(
      answeredFrame({ seq: 2, person: 'stf_ap', asked: 'why?', question: 'why', label: 'Why', answer: 'A', tacit: true, firstTime: true, visibility: 8 }),
    )
    useRunStore.getState().apply(
      answeredFrame({ seq: 3, person: 'stf_ap', asked: 'why again?', question: 'why', label: 'Why', answer: 'A', tacit: true, firstTime: false, visibility: 8 }),
    )

    const said = useRunStore.getState().answers.stf_ap
    expect(said[0].firstTime).toBe(false)
    expect(said[1].firstTime).toBe(true)
    // The HUD reads visibility off the same event.
    expect(useRunStore.getState().metrics.visibility).toBe(8)
  })

  it('rebuilds the answers when the run is replayed (R19, AE7)', () => {
    const frames = [
      genesisFrame(),
      answeredFrame({ seq: 2, person: 'stf_ap', asked: 'why?', question: 'why', label: 'Why', answer: 'A', tacit: true, firstTime: true }),
      answeredFrame({ seq: 3, person: 'dir_hr', asked: 'who decides?', question: 'axis', label: 'Who decides', answer: 'B', tacit: true, firstTime: true }),
    ]

    useRunStore.getState().applyAll(frames)
    const live = useRunStore.getState().answers

    // A reload is exactly this: a fresh client replaying the same log.
    useRunStore.getState().reset()
    useRunStore.getState().applyAll(frames)

    expect(useRunStore.getState().answers).toEqual(live)
    expect(useRunStore.getState().answers.stf_ap[0].answer).toBe('A')
  })

  it('drops the answers on reset, so a second run does not inherit them', () => {
    useRunStore.getState().apply(genesisFrame())
    useRunStore.getState().apply(
      answeredFrame({ seq: 2, person: 'stf_ap', asked: 'why?', question: 'why', answer: 'A' }),
    )

    useRunStore.getState().reset()
    expect(useRunStore.getState().answers).toEqual({})
  })
})

describe('typing into the ask box', () => {
  it('does not drive the CEO, though the questions are full of WASD', () => {
    // "why", "who decides", "what is slow" — every one of them types movement keys.
    const box = document.createElement('input')
    expect(typingTarget(box)).toBe(true)

    const textarea = document.createElement('textarea')
    expect(typingTarget(textarea)).toBe(true)
  })

  it('still lets the stage take keys pressed on the floor', () => {
    expect(typingTarget(document.createElement('canvas'))).toBe(false)
    expect(typingTarget(document.createElement('div'))).toBe(false)
    expect(typingTarget(null)).toBe(false)
  })

  it('leaves the tray radios and the composer toggles their own keys', () => {
    expect(typingTarget(document.createElement('button'))).toBe(true)
    expect(typingTarget(document.createElement('select'))).toBe(true)
    expect(typingTarget(document.createElement('summary'))).toBe(true)
  })
})

// =========================================================================
// R20: the floor and the conversation agree about who is stopped
// =========================================================================

describe('what a person is actually doing', () => {
  it('reads waiting off the tray, since no event carries person state (R20)', () => {
    const { id, want } = itemWithCheckpoint()
    useRunStore.getState().apply(genesisFrame())
    useRunStore.getState().apply(raisedFrame({ seq: 2, item: id, person: want }))

    const state = useRunStore.getState()
    // The wire's own copy still says idle, and would for the whole run.
    expect(state.people[want].state).toBe('idle')
    expect(state.people[want].waiting).toBe(false)

    const activity = personActivity(want, state.tray, state.items, state.people[want].state)
    expect(activity.waiting).toBe(true)
    expect(activity.state).toBe('blocked')
  })

  it('lights the beam on the floor for whoever is in the tray (R20)', () => {
    const { id, want } = itemWithCheckpoint()
    useRunStore.getState().apply(genesisFrame())
    useRunStore.getState().apply(raisedFrame({ seq: 2, item: id, person: want }))

    const actors = actorsFromStore()
    expect(actors.find((actor) => actor.id === want)?.waiting).toBe(true)
    // And nobody else.
    expect(actors.filter((actor) => actor.waiting === true)).toHaveLength(1)
  })

  it('says a person holding work is working, not free', () => {
    const { id, want } = itemWithCheckpoint()
    useRunStore.getState().apply(genesisFrame())
    useRunStore
      .getState()
      .apply(itemFrame({ seq: 2, kind: 'WORK_ASSIGNED', item: id, status: 'active', person: want }))

    const state = useRunStore.getState()
    const header = conversationHeader(want, state.genesis?.roster ?? {}, state.people, state.tray, state.items)

    expect(header?.stateLabel).toBe('Working')
    expect(header?.itemId).toBe(id)
  })

  it('says a person holding nothing is free', () => {
    useRunStore.getState().apply(genesisFrame())
    const state = useRunStore.getState()

    const header = conversationHeader('stf_ap', state.genesis?.roster ?? {}, state.people, state.tray, state.items)
    expect(header?.stateLabel).toBe('Free right now')
  })

  it('says waiting rather than working when they are stopped at a decision', () => {
    const { id, want } = itemWithCheckpoint()
    useRunStore.getState().apply(genesisFrame())
    useRunStore
      .getState()
      .apply(itemFrame({ seq: 2, kind: 'WORK_ASSIGNED', item: id, status: 'active', person: want }))
    useRunStore.getState().apply(raisedFrame({ seq: 3, item: id, person: want }))

    const state = useRunStore.getState()
    const header = conversationHeader(want, state.genesis?.roster ?? {}, state.people, state.tray, state.items)

    expect(header?.stateLabel).toBe('Waiting on your decision')
    expect(header?.waiting).toBe(true)
  })
})
