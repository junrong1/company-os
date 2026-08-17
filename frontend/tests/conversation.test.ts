import { act, createElement } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, describe, expect, it } from 'vitest'

import type { MetricDef } from '../src/design/tokens'
import type { EventFrame, OptionDef, PersonView, RosterEntry } from '../src/net/store'
import {
  comparePayload,
  comparisonFor,
  projectedDirection,
  stopSentence,
} from '../src/ui/comparison-model'
import {
  CLOSE_RADIUS_MILLI,
  DRAW_FIGURE_KEY,
  DRAW_FIGURE_LABEL,
  DRAW_FIGURE_UNIT,
  FROM_TRAY_COST,
  askPayload,
  askable,
  assignCost,
  assignableWork,
  IN_PERSON_COST,
  OPEN_RADIUS_MILLI,
  SWITCH_MARGIN_MILLI,
  consequenceDirection,
  conversationHeader,
  distanceMilli,
  optionConsequence,
  personActivity,
  resolvePayload,
  selectConversation,
  stoppedCard,
  tacitKey,
} from '../src/ui/conversation-model'
import { OptionConsequence } from '../src/ui/Consequence'
import { Conversation } from '../src/ui/Conversation'
import { OrgPanel, TrayPanel, WorkPanel } from '../src/ui/Panels'
import { actorsFromStore, typingTarget } from '../src/ui/stage'
import { useRunStore } from './helpers/store-helpers'
import {
  catalogFixture,
  genesisFixture,
  genesisFrame,
  itemFrame,
  metricDefsFixture,
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
    // Standing still, which is what "resting default" means for a walker: an empty path is
    // the kernel's own convention for not walking, so the start tick and the arrival state
    // are both meaningless rather than merely unset. Spelled out rather than left off,
    // because `Partial<PersonView>` would otherwise widen them to `undefined` and the only
    // thing that notices is `tsc` — which `npm test` does not run.
    path: [],
    pathStartTick: 0n,
    arrivesIn: '',
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
    const floor = floorOf([person('stf_ap', 0, 0)])

    const free = conversationHeader('stf_ap', roster, floor, [], {})
    expect(free?.waiting).toBe(false)
    expect(free?.stateLabel).toBe('Free right now')

    // Driven through the tray, which is how it actually happens: no event carries person
    // state, so the tray is the only thing that ever says somebody stopped.
    const tray = [
      {
        itemId: 'wi_ap_map',
        personId: 'stf_ap',
        cpIndex: 0,
        label: 'Approval',
        kind: 'approval',
        atTick: 600n,
        atSeq: 2n,
      },
    ]
    const stopped = conversationHeader('stf_ap', roster, floor, tray, {})

    // Same person, same conversation: only the card under the header changes.
    expect(stopped?.id).toBe(free?.id)
    expect(stopped?.waiting).toBe(true)
    expect(stopped?.stateLabel).toBe('Waiting on your decision')
  })

  it('stops reading as waiting once the decision leaves the tray', () => {
    // Nothing ever clears a recorded 'blocked', so trusting it would leave the beam burning
    // over somebody whose decision was taken minutes ago.
    useRunStore.getState().apply(genesisFrame())
    const roster = useRunStore.getState().genesis?.roster ?? {}
    const floor = floorOf([person('stf_ap', 0, 0, { state: 'blocked', waiting: true })])

    const header = conversationHeader('stf_ap', roster, floor, [], {})

    expect(header?.waiting).toBe(false)
    expect(header?.stateLabel).toBe('Free right now')
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

/**
 * One authored option, straight off the generated fixture.
 *
 * Named rather than found by predicate, so a test that quotes a figure quotes the option that
 * actually carries it — and fails loudly if that option is ever re-authored, which is the point
 * of pinning an authored number at all.
 */
function optionOf(itemId: string, cpIndex: number, optionIndex: number): OptionDef {
  const entry = catalogFixture().find((candidate) => candidate.id === itemId)
  if (entry === undefined) throw new Error(`no catalog entry ${itemId}`)
  const option = entry.checkpoints[cpIndex]?.options[optionIndex]
  if (option === undefined) throw new Error(`no option ${itemId}:${cpIndex}:${optionIndex}`)
  return option
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
// R35: what an option costs, on the option
// =========================================================================

describe('an option says what it costs', () => {
  it('carries the authored effect and note for every option at a checkpoint (R35, AE20)', () => {
    const withCheckpoints = catalogFixture().filter((entry) => entry.checkpoints.length > 0)
    expect(withCheckpoints.length).toBeGreaterThan(0)

    for (const entry of withCheckpoints) {
      for (const checkpoint of entry.checkpoints) {
        expect(checkpoint.options.length).toBeGreaterThan(0)
        for (const option of checkpoint.options) {
          // The kernel splits the pseudo-key out, so `effect` is metrics and nothing else.
          expect(option.effect).toBeTypeOf('object')
          expect(typeof option.draw_delta).toBe('number')
          // The sentence the deliverable's provenance records. Every authored option has one;
          // an option with no note would produce a deliverable that cannot say what was
          // decided.
          expect(option.note).toBeTruthy()
        }
      }
    }
  })

  it('renders each figure with its metric label, its sign and its unit', () => {
    const option = optionOf('wi_ap_map', 0, 0)
    const figures = optionConsequence(option, metricDefsFixture() as never)

    const byKey = Object.fromEntries(figures.map((figure) => [figure.key, figure]))
    expect(byKey.visibility.text).toBe('+6%')
    expect(byKey.leadTime.text).toBe('−1d')
    expect(byKey.morale.text).toBe('−1')
  })

  it('reads the metric table for what counts as good news, rather than the sign (R35)', () => {
    const defs = metricDefsFixture() as never
    // Lead time falling is a win and morale falling is not, and the two deltas have the same
    // sign. A uniform rising-is-good rule would colour them identically.
    const figures = optionConsequence(optionOf('wi_ap_map', 0, 0), defs)
    const byKey = Object.fromEntries(figures.map((figure) => [figure.key, figure]))

    expect(consequenceDirection(byKey.leadTime, defs)).toBe('favourable')
    expect(consequenceDirection(byKey.morale, defs)).toBe('unfavourable')
  })

  it('surfaces the recurring draw in its own unit, never as a metric delta', () => {
    const defs = metricDefsFixture() as never
    // "Approve it" on the automation item takes forty hours a month off accounting's draw.
    const option = optionOf('wi_ap_auto', 0, 0)
    expect(option.draw_delta).toBe(-40)

    const figures = optionConsequence(option, defs)
    const draw = figures.find((figure) => figure.key === DRAW_FIGURE_KEY)

    expect(draw?.text).toBe(`−40${DRAW_FIGURE_UNIT}`)
    expect(draw?.label).toBe(DRAW_FIGURE_LABEL)
    // `manualHours` is the *sum* of the department draws, so a draw change rendered as a
    // manualHours delta would be a second, disagreeing statement of one movement.
    expect(figures.some((figure) => figure.key === 'manualHours')).toBe(false)
    // Less recurring work is a win, even though the number is negative.
    expect(consequenceDirection(draw!, defs)).toBe('favourable')
    // And the draw sorts after the metrics, so two options read down the same column.
    expect(figures[figures.length - 1].key).toBe(DRAW_FIGURE_KEY)
  })

  it('renders every figure with its marking, and renders nothing when there is nothing', () => {
    // R28 is a completeness claim about *rendered* figures, so it has to be asserted on the
    // rendered output. Every sibling surface — the HUD tiles, the DAG load label, the branch
    // columns — carries an explicit marking assertion; this is the fourth surface that renders
    // an authored figure and it had none of its own.
    const host = document.createElement('div')
    document.body.appendChild(host)
    const root = createRoot(host)
    const defs = metricDefsFixture() as never

    act(() => {
      root.render(
        createElement(OptionConsequence, {
          option: optionOf('wi_ap_auto', 0, 0),
          metricDefs: defs,
        } as never),
      )
    })

    const figures = host.querySelectorAll('.consequence__figure')
    expect(figures.length).toBeGreaterThan(0)
    for (const figure of figures) {
      expect(figure.querySelector('[data-authored-tuning]')).not.toBeNull()
    }
    expect(host.querySelector('.consequence__note')?.textContent).toContain('Recorded as:')

    // The empty case renders nothing at all rather than an empty shell — an option that moves
    // nothing and records nothing has no consequence to show.
    act(() => {
      root.render(
        createElement(OptionConsequence, {
          option: { label: 'Nothing', detail: '', effect: {}, draw_delta: 0, note: '' },
          metricDefs: defs,
        } as never),
      )
    })
    expect(host.querySelector('.consequence')).toBeNull()

    act(() => root.unmount())
    host.remove()
  })

  it('renders no figures at all for an option that moves nothing', () => {
    // "Leave it at $10K" on the closing-cycle item authors an empty effect. A row of zeros
    // would read as a measurement that came back flat, and there was no measurement.
    const option = optionOf('wi_close', 1, 1)
    expect(option.effect).toEqual({})
    expect(option.draw_delta).toBe(0)
    expect(optionConsequence(option, metricDefsFixture() as never)).toEqual([])
    // The note still renders: "no change" is precisely what that option is for.
    expect(option.note).toBeTruthy()
  })

  it('shows no figures for a payload written before the option carried any', () => {
    // A run exported before genesis payload version 4 is still readable through the report
    // path, where the rules-version gate that rejects a live resync does not apply. No figures
    // is the honest answer for one of those — `undefined` reaching the renderer is not.
    const older = { label: 'Old', detail: 'From an older payload' } as never
    expect(optionConsequence(older, metricDefsFixture() as never)).toEqual([])
  })

  it('reaches the decision surface with no model key configured (AE23)', () => {
    // Nothing on this path consults a model, an agent or a bench: the figures ride on the
    // genesis event and are read straight off it. Asserted by driving the surface from
    // genesis and a raise alone, which is every frame a keyless run produces here.
    const { id, want } = itemWithCheckpoint()
    useRunStore.getState().apply(genesisFrame())
    useRunStore.getState().apply(raisedFrame({ seq: 2, item: id, person: want }))

    const state = useRunStore.getState()
    const card = stoppedCard(want, state.tray, state.genesis?.catalog ?? [], state.tacitLines)

    expect(card?.options.length).toBeGreaterThan(0)
    for (const option of card?.options ?? []) {
      expect(option.note).toBeTruthy()
    }
    expect(
      optionConsequence(card!.options[0], state.genesis?.metricDefs ?? []).length,
    ).toBeGreaterThan(0)
  })
})

// =========================================================================
// R21, R22, R34: where each option leads
// =========================================================================

/** An OPTIONS_COMPARED frame, in the shape the kernel's record actually has. */
function comparedFrame(options: {
  seq: number
  item: string
  person: string
  cpIndex?: number
  tick?: number
  inPerson?: boolean
  branches?: Array<Record<string, unknown>>
}): EventFrame {
  const tick = options.tick ?? 600
  return {
    kind: 'OPTIONS_COMPARED',
    seq: String(options.seq),
    tick: String(tick),
    schema_ver: 1,
    rules_ver: 'test',
    run_id: 'run-1',
    command_id: '',
    request_id: '',
    payload: {
      tick,
      item: options.item,
      cp_index: options.cpIndex ?? 0,
      person: options.person,
      requested_at_tick: tick,
      in_person: options.inPerson ?? true,
      branches: options.branches ?? [
        branchFixture(0, { stop_reason: 'horizon', unlocked: ['wi_ap_auto', 'wi_close'] }),
        branchFixture(1, { stop_reason: 'horizon', unlocked: ['wi_ap_auto'] }),
        branchFixture(2, { stop_reason: 'insolvent', runway: { value: 0, at_tick: 4200 } }),
      ],
    },
  }
}

function branchFixture(
  optionIndex: number,
  over: Record<string, unknown> = {},
): Record<string, unknown> {
  return {
    option_index: optionIndex,
    option_label: `Option ${optionIndex}`,
    option_note: `Recorded ${optionIndex}`,
    in_person: true,
    fork_tick: 600,
    stop_tick: 10800,
    stop_reason: 'horizon',
    stop_detail: 'the branch reached the run horizon',
    stopped_at: null,
    trajectories: {
      cash: [
        { tick: 600, value: 4800 },
        { tick: 5400, value: 4400 },
        { tick: 10800, value: 4100 },
      ],
    },
    metrics: {
      cash: { value: 4100, at_tick: 10800 },
      visibility: { value: 24, at_tick: 10800 },
    },
    runway: { value: 117, at_tick: 10800 },
    daily_cost: { value: 35, at_tick: 10800 },
    unlocked: [],
    foreclosed: [],
    ...over,
  }
}

describe('the comparison', () => {
  it('renders one column per option, each figure naming its tick (R22, AE15)', () => {
    const { id, want } = itemWithCheckpoint()
    useRunStore.getState().apply(genesisFrame())
    useRunStore.getState().apply(raisedFrame({ seq: 2, item: id, person: want }))
    useRunStore.getState().apply(comparedFrame({ seq: 3, item: id, person: want }))

    const state = useRunStore.getState()
    const comparison = comparisonFor(id, 0, true, state.comparisons, state.tray)

    expect(comparison).not.toBeNull()
    expect(comparison?.branches).toHaveLength(3)
    expect(comparison?.forkTick).toBe(600n)
    // A new event kind arriving in the stream must not read as a lost event. The store's gap
    // check is on the sequence alone, so a kind it did not recognise would still advance
    // `appliedSeq` — this states that it does, because a spurious gap banner would tell the
    // CEO their stream is broken every time they ran a comparison.
    expect(state.appliedSeq).toBe(3n)
    expect(state.sequenceGap).toBe(false)

    for (const branch of comparison?.branches ?? []) {
      // Every figure carries the tick it was measured at. A projection read without its
      // measurement point is indistinguishable from a fact about now.
      for (const figure of Object.values(branch.metrics)) {
        expect(figure.atTick).toBe(10800n)
      }
      expect(branch.runway.atTick).toBeGreaterThan(0n)
      for (const points of Object.values(branch.trajectories)) {
        expect(points[0].tick).toBe(branch.forkTick)
        expect(points[points.length - 1].tick).toBe(branch.stopTick)
      }
    }
  })

  it('leaves the parent live trajectory store untouched', () => {
    // The distinction the whole slice exists for. A branch's points are a projection; folding
    // them into `trajectories` would make the HUD's sparklines draw a future nobody has lived,
    // with no way to tell afterwards which points were which.
    const { id, want } = itemWithCheckpoint()
    useRunStore.getState().apply(genesisFrame())
    useRunStore.getState().apply(metricsFrame({ seq: 2, cash: 4800 }))
    useRunStore.getState().apply(raisedFrame({ seq: 3, item: id, person: want }))

    const before = JSON.stringify(
      useRunStore.getState().trajectories,
      (_, value) => (typeof value === 'bigint' ? value.toString() : value),
    )

    useRunStore.getState().apply(comparedFrame({ seq: 4, item: id, person: want }))

    const after = JSON.stringify(
      useRunStore.getState().trajectories,
      (_, value) => (typeof value === 'bigint' ? value.toString() : value),
    )
    expect(after).toBe(before)
    // And the projection did land, so the test is about where it went rather than about
    // nothing having happened.
    expect(Object.keys(useRunStore.getState().comparisons)).toEqual([`${id}:0:here`])
  })

  it('drops a displayed result when the item leaves blocked, with no client-side timer', () => {
    // R25, derived from the wire's own authority. The same store branch that drops a tray
    // entry drops the comparison, so resolution, reassignment, attrition and a return to the
    // backlog are all covered by one rule rather than four.
    const { id, want } = itemWithCheckpoint()
    useRunStore.getState().apply(genesisFrame())
    useRunStore.getState().apply(raisedFrame({ seq: 2, item: id, person: want }))
    useRunStore.getState().apply(comparedFrame({ seq: 3, item: id, person: want }))

    expect(useRunStore.getState().comparisons[`${id}:0:here`]).toBeDefined()

    useRunStore.getState().apply(
      itemFrame({ seq: 4, kind: 'DECISION_RESOLVED', item: id, status: 'active' }),
    )

    const state = useRunStore.getState()
    expect(state.comparisons[`${id}:0:here`]).toBeUndefined()
    expect(comparisonFor(id, 0, true, state.comparisons, state.tray)).toBeNull()
  })

  it('shows nothing once the tray no longer holds that checkpoint, whatever the slice holds', () => {
    // The second guard, and it is deliberately redundant with the one above: the tray is the
    // wire's statement of what is still waiting, and a result shown against a checkpoint that
    // is not is a projection from a run that no longer exists.
    const { id, want } = itemWithCheckpoint()
    useRunStore.getState().apply(genesisFrame())
    useRunStore.getState().apply(raisedFrame({ seq: 2, item: id, person: want }))
    useRunStore.getState().apply(comparedFrame({ seq: 3, item: id, person: want }))

    const held = useRunStore.getState().comparisons
    expect(comparisonFor(id, 0, true, held, [])).toBeNull()
    expect(comparisonFor(id, 0, true, held, useRunStore.getState().tray)).not.toBeNull()
  })

  it('replaces a second comparison at the same checkpoint rather than keeping both', () => {
    // Resolved during implementation, and replacing is the safe direction: the kernel computes
    // every branch before appending, so a record that arrives is complete, and the newer one
    // describes a later fork.
    const { id, want } = itemWithCheckpoint()
    useRunStore.getState().apply(genesisFrame())
    useRunStore.getState().apply(raisedFrame({ seq: 2, item: id, person: want }))
    useRunStore.getState().apply(comparedFrame({ seq: 3, item: id, person: want, tick: 600 }))
    useRunStore.getState().apply(comparedFrame({ seq: 4, item: id, person: want, tick: 900 }))

    const held = useRunStore.getState().comparisons
    expect(Object.keys(held)).toHaveLength(1)
    expect(held[`${id}:0:here`].forkTick).toBe(900n)
    expect(held[`${id}:0:here`].atSeq).toBe(4n)
  })

  it('marks the figures the panels render, not only the ones the HUD does (R27, R28)', () => {
    // The HUD sweep covers HUD tiles. These three are figures on the *panels* — a person's
    // progress in the org chart, an item's progress in the work list, and the visibility a
    // locked item is waiting on — and every one of them is authored effort or an authored
    // gate. "Every number the client renders" was not true while they were bare.
    const { id, want } = itemWithCheckpoint()

    act(() => {
      useRunStore.getState().apply(genesisFrame())
      useRunStore.getState().apply(
        itemFrame({ seq: 2, kind: 'WORK_ASSIGNED', item: id, status: 'active', person: want }),
      )
    })

    const host = document.createElement('div')
    document.body.appendChild(host)
    const root = createRoot(host)
    act(() => {
      root.render(createElement('div', null, createElement(OrgPanel), createElement(WorkPanel)))
    })

    const withDigits = (nodes: Element[]) =>
      nodes.filter((node) => /\d/.test(node.textContent ?? ''))

    // The work list's progress readout, and the gate a locked item is waiting on.
    const rendered = withDigits([
      ...host.querySelectorAll('.item__meta'),
      ...host.querySelectorAll('.item__locked'),
    ])
    expect(rendered.length).toBeGreaterThan(1)
    for (const figure of rendered) {
      expect(
        figure.querySelector('[data-authored-tuning]'),
        `unmarked figure: ${figure.textContent}`,
      ).not.toBeNull()
    }

    // A row with no figure carries no marking: the rule is "mark the number", not "decorate
    // every row". `waits on wi_ap_map` names work, not a quantity.
    const nameOnly = [...host.querySelectorAll('.item__locked')].find(
      (node) => !/\d/.test(node.textContent ?? ''),
    )
    expect(nameOnly).toBeDefined()
    expect(nameOnly?.querySelector('[data-authored-tuning]')).toBeNull()

    // The org chart's own progress figure, which this test used to record as unreachable: the
    // row reads `PersonView.itemId` and no event set it, so every row rendered as idle and the
    // marking in the source was never exercised. `WORK_ASSIGNED` sets it now, so the figure is
    // real coverage — one row shows a percentage, and it is marked like any other.
    const tasks = [...host.querySelectorAll('.person__task')]
    expect(tasks.length).toBeGreaterThan(0)

    const withProgress = withDigits(tasks)
    expect(withProgress).toHaveLength(1)
    for (const figure of withProgress) {
      expect(
        figure.querySelector('[data-authored-tuning]'),
        `unmarked figure: ${figure.textContent}`,
      ).not.toBeNull()
    }

    act(() => root.unmount())
    host.remove()
  })

  it('never shows one route the other route figures (R21)', () => {
    // Both affordances are mounted at once whenever the CEO stands next to the blocked person,
    // so a key without the route let a tray-run comparison satisfy the conversation's lookup —
    // and the in-person panel would render tray-priced branches. The premium between the two
    // is exactly what the panel exists to show, so the figures were wrong by the amount the
    // feature is about.
    const { id, want } = itemWithCheckpoint()
    useRunStore.getState().apply(genesisFrame())
    useRunStore.getState().apply(raisedFrame({ seq: 2, item: id, person: want }))
    useRunStore
      .getState()
      .apply(comparedFrame({ seq: 3, item: id, person: want, inPerson: false }))

    const state = useRunStore.getState()

    // The tray asked, so the tray sees it and the conversation does not.
    expect(comparisonFor(id, 0, false, state.comparisons, state.tray)).not.toBeNull()
    expect(comparisonFor(id, 0, true, state.comparisons, state.tray)).toBeNull()

    // And the two are held side by side rather than overwriting each other.
    useRunStore
      .getState()
      .apply(comparedFrame({ seq: 4, item: id, person: want, inPerson: true }))
    const both = useRunStore.getState()
    expect(Object.keys(both.comparisons).sort()).toEqual([`${id}:0:here`, `${id}:0:tray`])
    expect(comparisonFor(id, 0, true, both.comparisons, both.tray)?.inPerson).toBe(true)
    expect(comparisonFor(id, 0, false, both.comparisons, both.tray)?.inPerson).toBe(false)
  })

  it('does not answer a fresh ask with the record from a previous one (R25)', () => {
    // The record survives closing the panel — it is dropped only when the item leaves
    // `blocked` — and a reload re-applies every historical record off the stream. Without the
    // asked-at sequence, reopening rendered a projection thousands of ticks old as though it
    // were the answer to the click just made, then silently swapped it half a second later.
    const { id, want } = itemWithCheckpoint()
    useRunStore.getState().apply(genesisFrame())
    useRunStore.getState().apply(raisedFrame({ seq: 2, item: id, person: want }))
    useRunStore.getState().apply(comparedFrame({ seq: 3, item: id, person: want, tick: 600 }))

    const held = useRunStore.getState()
    // Asked before that record arrived: it is the answer.
    expect(comparisonFor(id, 0, true, held.comparisons, held.tray, 2n)).not.toBeNull()
    // Asked after it arrived: it is a previous answer, and the panel waits for a new one.
    expect(comparisonFor(id, 0, true, held.comparisons, held.tray, 3n)).toBeNull()
    expect(comparisonFor(id, 0, true, held.comparisons, held.tray, 99n)).toBeNull()

    // The newer record answers the newer ask.
    useRunStore.getState().apply(comparedFrame({ seq: 40, item: id, person: want, tick: 5000 }))
    const fresh = useRunStore.getState()
    expect(comparisonFor(id, 0, true, fresh.comparisons, fresh.tray, 3n)?.forkTick).toBe(5000n)
  })

  it('renders a terminal branch by its reason rather than as an empty trajectory', () => {
    const insolvent = {
      ...branchFixture(0),
      stop_reason: 'insolvent',
      stop_tick: 4200,
      runway: { value: 0, at_tick: 4200 },
    }
    const reached = {
      ...branchFixture(1),
      stop_reason: 'checkpoint',
      stop_tick: 2400,
      stopped_at: {
        item: 'wi_quotes',
        cp_index: 0,
        label: 'Decision',
        kind: 'decision',
        person: 'stf_buyer',
        tick: 2400,
      },
    }

    const { id, want } = itemWithCheckpoint()
    useRunStore.getState().apply(genesisFrame())
    useRunStore.getState().apply(raisedFrame({ seq: 2, item: id, person: want }))
    useRunStore
      .getState()
      .apply(comparedFrame({ seq: 3, item: id, person: want, branches: [insolvent, reached] }))

    const branches = useRunStore.getState().comparisons[`${id}:0:here`].branches

    expect(stopSentence(branches[0])).toContain('runs out of cash')
    expect(stopSentence(branches[0])).toContain('4200')
    expect(stopSentence(branches[1])).toContain('Nothing is settled')
    expect(branches[1].reached?.itemId).toBe('wi_quotes')
  })

  it('says a bounded branch stopped short rather than reporting it as the horizon', () => {
    // The kernel stops a branch after a fixed span whatever horizon the run carries, so one
    // comparison stays affordable on a run created with an arbitrary horizon. The column has
    // to say the projection ran out, not that the run did — reporting the second as the first
    // would claim the branch covered ground it never walked.
    const bounded = { ...branchFixture(0), stop_reason: 'bound', stop_tick: 22213 }
    const horizon = { ...branchFixture(1), stop_reason: 'horizon', stop_tick: 10800 }

    const { id, want } = itemWithCheckpoint()
    useRunStore.getState().apply(genesisFrame())
    useRunStore.getState().apply(raisedFrame({ seq: 2, item: id, person: want }))
    useRunStore
      .getState()
      .apply(comparedFrame({ seq: 3, item: id, person: want, branches: [bounded, horizon] }))

    const branches = useRunStore.getState().comparisons[`${id}:0:here`].branches

    expect(branches[0].stopReason).toBe('bound')
    expect(stopSentence(branches[0])).toContain('continues past there')
    expect(stopSentence(branches[0])).not.toContain('horizon')
    // And the genuine horizon case still reads as the end of the run.
    expect(stopSentence(branches[1])).toContain('horizon')
  })

  it('keeps a runway the kernel could not know distinct from zero', () => {
    // The two are the same pixel width and opposite in meaning: "not yet knowable" and
    // "insolvent". Coercing the null on the way in would render one as the other.
    const unknown = { ...branchFixture(0), runway: { value: null, at_tick: 700 } }
    const { id, want } = itemWithCheckpoint()
    useRunStore.getState().apply(genesisFrame())
    useRunStore.getState().apply(raisedFrame({ seq: 2, item: id, person: want }))
    useRunStore
      .getState()
      .apply(comparedFrame({ seq: 3, item: id, person: want, branches: [unknown] }))

    const branch = useRunStore.getState().comparisons[`${id}:0:here`].branches[0]
    expect(branch.runway.value).toBeNull()
    expect(branch.runway.atTick).toBe(700n)
  })

  it('sends the same payload from both routes, with only the route flag differing (AE14)', () => {
    // The tray and the conversation open the same comparison for the same checkpoint. The one
    // thing that legitimately differs is the route, because the kernel prices the two
    // differently and a branch has to price the one the CEO is about to take.
    const walked = comparePayload('wi_ap_map', 0, 'stf_ap', 1234n, true)
    const trayed = comparePayload('wi_ap_map', 0, 'stf_ap', 1234n, false)

    expect(walked).toEqual({
      item: 'wi_ap_map',
      cp_index: 0,
      person: 'stf_ap',
      // A string, matching the convention every tick on the wire follows: `Number` works for
      // the whole of a normal run and starts silently losing the low bits above 2^53.
      at_tick: '1234',
      in_person: true,
    })
    expect({ ...trayed, in_person: true }).toEqual(walked)
  })

  it('drops every held comparison on a resync', () => {
    // A resync means the client was too far behind to catch up by replay, so the fork tick
    // every held projection was measured at is somewhere in a stretch of the run this client
    // never saw. Keeping them would leave figures on screen whose basis cannot be accounted
    // for — and the CEO can ask again for the cost of a tenth of a second.
    const { id, want } = itemWithCheckpoint()
    useRunStore.getState().apply(genesisFrame())
    useRunStore.getState().apply(raisedFrame({ seq: 2, item: id, person: want }))
    useRunStore.getState().apply(comparedFrame({ seq: 3, item: id, person: want }))
    expect(Object.keys(useRunStore.getState().comparisons)).toHaveLength(1)

    useRunStore.getState().apply({
      kind: 'RESYNC',
      head_seq: '40',
      state: { metrics: { cash: 4000 }, items: {}, people: {}, lifecycle: { tick: 4000 } },
    })

    expect(useRunStore.getState().comparisons).toEqual({})
  })

  it('survives a record that names no item rather than storing one under an empty key', () => {
    // Total, like every other reader in the store: this runs inside the WebSocket message
    // handler, and a throw would escape `apply` and take the stream down over one bad frame.
    useRunStore.getState().apply(genesisFrame())
    useRunStore.getState().apply({
      kind: 'OPTIONS_COMPARED',
      seq: '9',
      tick: '600',
      schema_ver: 1,
      rules_ver: 'test',
      run_id: 'run-1',
      command_id: '',
      request_id: '',
      payload: { tick: 600, branches: 'not an array' },
    })

    expect(useRunStore.getState().comparisons).toEqual({})
    expect(useRunStore.getState().appliedSeq).toBe(9n)
  })

  it('opens from both surfaces and closes without committing anything (R34, AE14)', () => {
    // The one claim in this unit that is genuinely about the rendered surface rather than
    // about a rule: closing sends nothing, and the option list it sits under never went away.
    const { id, want } = itemWithCheckpoint()
    const asked: Array<[string, number, string, boolean]> = []
    const commands: string[] = []

    act(() => {
      useRunStore.getState().apply(genesisFrame())
      useRunStore.getState().apply(raisedFrame({ seq: 2, item: id, person: want }))
    })

    const host = document.createElement('div')
    document.body.appendChild(host)
    const root = createRoot(host)

    act(() => {
      root.render(
        createElement(TrayPanel, {
          onResolve: (kind: string) => commands.push(kind),
          onCompare: (item: string, cp: number, person: string, inPerson: boolean) =>
            asked.push([item, cp, person, inPerson]),
        } as never),
      )
    })

    // The option list is on the card before the comparison is opened.
    expect(host.querySelectorAll('.option').length).toBeGreaterThan(0)

    const ask = host.querySelector('.compare__ask') as HTMLButtonElement
    expect(ask).not.toBeNull()
    act(() => ask.click())

    // The tray route asks for the tray's own pricing, because that is the route it settles by.
    expect(asked).toEqual([[id, 0, want, false]])
    expect(commands).toEqual([])

    act(() => {
      useRunStore
        .getState()
        .apply(comparedFrame({ seq: 3, item: id, person: want, inPerson: false }))
    })

    expect(host.querySelectorAll('.branch').length).toBe(3)
    // Every figure in every column carries the marking (AE11).
    for (const figure of host.querySelectorAll('.branch__figure')) {
      expect(figure.querySelector('[data-authored-tuning]')).not.toBeNull()
    }

    const close = host.querySelector('.comparison__close') as HTMLButtonElement
    act(() => close.click())

    // Back to the option list, nothing sent, nothing committed.
    expect(host.querySelectorAll('.branch').length).toBe(0)
    expect(host.querySelectorAll('.option').length).toBeGreaterThan(0)
    expect(commands).toEqual([])
    expect(asked).toHaveLength(1)

    act(() => root.unmount())
    host.remove()
  })

  it('asks in person from the conversation, and from the tray from the tray (AE14)', () => {
    // The route flag is load-bearing: the kernel prices in-person and from-the-tray
    // differently, so a branch that priced the route the CEO is *not* about to take would be
    // wrong by exactly that premium. The tray side was covered and this side was not — which
    // made "opens from both surfaces" a claim only half the surfaces were tested for.
    const { id, want } = itemWithCheckpoint()
    const asked: Array<[string, number, string, boolean]> = []

    act(() => {
      useRunStore.getState().apply(genesisFrame())
      useRunStore.getState().apply(raisedFrame({ seq: 2, item: id, person: want }))
    })

    const host = document.createElement('div')
    document.body.appendChild(host)
    const root = createRoot(host)

    act(() => {
      root.render(
        createElement(Conversation, {
          personId: want,
          onCompare: (item: string, cp: number, person: string, inPerson: boolean) =>
            asked.push([item, cp, person, inPerson]),
        } as never),
      )
    })

    const ask = host.querySelector('.compare__ask') as HTMLButtonElement
    expect(ask).not.toBeNull()
    act(() => ask.click())

    // True, because the CEO is standing in front of them. The tray asserts the same call with
    // the flag the other way round, so the pair states the distinction rather than one side.
    expect(asked).toEqual([[id, 0, want, true]])

    act(() => root.unmount())
    host.remove()
  })

  it('renders the fallback sentence for a stop reason it does not recognise', () => {
    // `readBranch` is deliberately total against a malformed or older payload, so it can
    // produce a branch whose reason is none of the four. The column still has to say
    // something rather than render an empty string where a sentence belongs.
    const unknown = { ...branchFixture(0), stop_reason: 'something-new', stop_tick: 900 }
    const { id, want } = itemWithCheckpoint()
    useRunStore.getState().apply(genesisFrame())
    useRunStore.getState().apply(raisedFrame({ seq: 2, item: id, person: want }))
    useRunStore
      .getState()
      .apply(comparedFrame({ seq: 3, item: id, person: want, branches: [unknown] }))

    const branch = useRunStore.getState().comparisons[`${id}:0:here`].branches[0]
    expect(stopSentence(branch)).toBe('Stops at tick 900.')

    // And a checkpoint stop that names no checkpoint still reads as a stop.
    expect(stopSentence({ ...branch, stopReason: 'checkpoint', reached: null })).toContain(
      'next decision',
    )
  })

  it('keeps the comparisons slice by reference when an unrelated item moves', () => {
    // A performance contract with teeth: items leave `blocked` on every assignment, delivery
    // and attrition, and rebuilding the slice each time would hand it a new identity — waking
    // any subscriber that reads the whole slice for a change that did not happen.
    const catalogue = catalogFixture().filter((entry) => entry.checkpoints.length > 0)
    expect(catalogue.length).toBeGreaterThanOrEqual(2)
    const [held, other] = catalogue

    useRunStore.getState().apply(genesisFrame())
    useRunStore.getState().apply(raisedFrame({ seq: 2, item: held.id, person: held.want }))
    useRunStore
      .getState()
      .apply(comparedFrame({ seq: 3, item: held.id, person: held.want }))

    const before = useRunStore.getState().comparisons
    useRunStore.getState().apply(
      itemFrame({ seq: 4, kind: 'WORK_ASSIGNED', item: other.id, status: 'active' }),
    )

    expect(useRunStore.getState().comparisons).toBe(before)

    // And the slice is genuinely replaced when the item it holds a comparison for moves,
    // so the fast path is not simply never dropping anything.
    useRunStore.getState().apply(
      itemFrame({ seq: 5, kind: 'DECISION_RESOLVED', item: held.id, status: 'active' }),
    )
    expect(useRunStore.getState().comparisons).not.toBe(before)
    expect(useRunStore.getState().comparisons).toEqual({})
  })

  it('reads the projected direction against where the run is now, not against zero', () => {
    // The comparison the CEO is making is "better or worse than where I am", and the metric's
    // own favourable direction decides which. Lead time falling is a win; cash falling is not.
    const defs = metricDefsFixture() as never as MetricDef[]
    const leadTime = defs.find((metric) => metric.key === 'leadTime')
    const cash = defs.find((metric) => metric.key === 'cash')

    expect(projectedDirection({ value: 9, atTick: 10n }, 12, leadTime)).toBe('favourable')
    expect(projectedDirection({ value: 4100, atTick: 10n }, 4800, cash)).toBe('unfavourable')
    expect(projectedDirection({ value: 4800, atTick: 10n }, 4800, cash)).toBe('flat')
    // Not knowable is neither, and must not read as a fall to zero.
    expect(projectedDirection({ value: null, atTick: 10n }, 4800, cash)).toBe('flat')
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
