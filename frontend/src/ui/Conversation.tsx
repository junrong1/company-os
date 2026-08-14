/**
 * The conversation: what standing next to someone opens.
 *
 * **An overlay on the stage, not an entry in the panel rail.** The rail already holds the tray,
 * the org chart, the work and the outputs, and displacing one of them would mean walking over
 * to someone costs you sight of what you walked over about. Anchored inside `.stage`, which is
 * already `position: relative`, so it sits over the office without taking the HUD's row.
 *
 * **Everything it decides is in `conversation-model.ts`.** This file renders; it holds no rule.
 * Who is in range, whether they can be handed work, what a typed question matched — all of it
 * is asserted without a DOM, which is what makes the mechanic testable rather than the markup.
 */

import { useMemo, useState } from 'react'
import { useShallow } from 'zustand/react/shallow'

import { PAL, deptColour } from '../design/tokens'
import { useRunStore } from '../net/store'
import type { CommandSender } from './Panels'
import {
  type AssignableItem,
  type StoppedCard,
  assignCost,
  assignableWork,
  conversationHeader,
  resolvePayload,
  stoppedCard,
} from './conversation-model'

export interface ConversationProps {
  /** Who the CEO is standing next to, decided by the model from both parties' positions. */
  personId: string | null
  onCommand?: CommandSender
}

export function Conversation({ personId, onCommand }: ConversationProps) {
  const header = useRunStore(
    useShallow((state) =>
      conversationHeader(personId, state.genesis?.roster ?? {}, state.people),
    ),
  )
  const stopped = useRunStore(
    useShallow((state) =>
      stoppedCard(personId, state.tray, state.genesis?.catalog ?? [], state.tacitLines),
    ),
  )
  // Genesis is written once and never replaced, so these are stable by reference and need no
  // shallow comparison.
  const roster = useRunStore((state) => state.genesis?.roster)
  const catalog = useRunStore((state) => state.genesis?.catalog)

  // Encoded to strings before it reaches the equality check, the way the panels do it: a
  // selector that returned the item objects would hand back fresh references on every call and
  // re-render forever, because the check is `Object.is` on the selector's own output.
  const itemStates = useRunStore(
    useShallow((state) =>
      Object.fromEntries(
        Object.entries(state.items).map(([id, item]) => [
          id,
          `${item.status}|${item.unlocked ? '1' : '0'}`,
        ]),
      ),
    ),
  )

  // Derived rather than subscribed, so an item unlocking while the conversation is open
  // appears in it without the CEO stepping away and back (R12).
  const assignable = useMemo(() => {
    const items = Object.fromEntries(
      Object.entries(itemStates).map(([id, encoded]) => {
        const [status, unlocked] = encoded.split('|')
        return [id, { status, unlocked: unlocked === '1' }]
      }),
    )
    return assignableWork(personId, roster ?? {}, items, catalog ?? [])
  }, [personId, roster, itemStates, catalog])

  // Nothing rendered at all rather than an empty shell: a persistent "nobody nearby" card
  // would occupy the office permanently to say nothing, and proximity is supposed to be felt
  // as the panel appearing.
  if (header === null) return null

  return (
    <aside className="conversation" data-state={header.state} aria-label="Conversation">
      <header className="conversation__who">
        <span
          className="conversation__stripe"
          style={{ background: deptColour(header.dept) }}
          aria-hidden="true"
        />
        <span className="conversation__body">
          <span className="conversation__name">{header.name}</span>
          <span className="conversation__title">{header.title}</span>
          <span className="conversation__meta" style={{ color: PAL.textFaint }}>
            {header.dept} · {header.stateLabel}
          </span>
        </span>
      </header>

      {stopped !== null && (
        <Decision key={`${stopped.itemId}:${stopped.cpIndex}`} card={stopped} onCommand={onCommand} />
      )}

      {/* Only when they are free. Offering to hand new work to someone standing at a decision
          would invite the CEO to walk away from the thing they were summoned for. */}
      {stopped === null && header.state === 'idle' && assignable.length > 0 && (
        <section className="conversation__work">
          <h3>Work they could take</h3>
          {assignable.map((item) => (
            <Offer key={item.itemId} item={item} onCommand={onCommand} />
          ))}
        </section>
      )}
    </aside>
  )
}

/**
 * One thing this person could be handed.
 *
 * There is no route picker, because the route is not a choice made here — it follows from who
 * the item wants. Handing it to the person it wants is the direct route; handing it to their
 * director to pass on is the routed one. What differs is stated on the card rather than
 * discovered in the report (R13).
 */
function Offer({ item, onCommand }: { item: AssignableItem; onCommand?: CommandSender }) {
  return (
    <article className="offer" data-bypass={item.bypassesDirector}>
      <p className="offer__title">{item.title}</p>
      <p className="offer__brief">{item.brief}</p>
      <button type="button" onClick={() => onCommand?.('assign_work', item.payload)}>
        {item.bypassesDirector ? `Hand it to ${item.wantId}` : `Pass it to ${item.wantId}`}
      </button>
      <p className="offer__cost" style={{ color: PAL.textFaint }}>
        {assignCost(item)}
      </p>
    </article>
  )
}

/**
 * A decision, taken standing there.
 *
 * The tacit line is rendered above the options rather than after the choice, because R10 is
 * that each route states its cost *before* it is taken — and the whole cost of the tray route
 * is not hearing this. Keyed on the checkpoint by the caller, so moving to a different decision
 * clears the selected option instead of carrying it across.
 */
function Decision({ card, onCommand }: { card: StoppedCard; onCommand?: CommandSender }) {
  const [chosen, setChosen] = useState<number | null>(null)

  return (
    <section className="conversation__decision">
      <p className="conversation__item">{card.itemTitle}</p>
      <p className="conversation__prompt">{card.prompt}</p>

      {card.tacit !== '' && (
        <p className="conversation__tacit" style={{ color: PAL.tacit }}>
          <span className="conversation__badge">Only in person</span>
          {card.tacit}
        </p>
      )}

      {card.options.map((option, index) => (
        <label key={option.label} className="option" data-chosen={chosen === index}>
          <input
            type="radio"
            name={`talk:${card.itemId}:${card.cpIndex}`}
            checked={chosen === index}
            onChange={() => setChosen(index)}
          />
          <span className="option__label">{option.label}</span>
          <span className="option__detail">{option.detail}</span>
        </label>
      ))}

      <button
        type="button"
        disabled={chosen === null}
        onClick={() =>
          chosen !== null &&
          onCommand?.(
            'resolve_checkpoint',
            resolvePayload(card.itemId, card.cpIndex, chosen, true),
          )
        }
      >
        Decide here
      </button>
      <p className="conversation__cost" style={{ color: PAL.textFaint }}>
        {card.cost}
      </p>
    </section>
  )
}
