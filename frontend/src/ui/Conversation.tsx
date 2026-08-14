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

import { useState } from 'react'
import { useShallow } from 'zustand/react/shallow'

import { PAL, deptColour } from '../design/tokens'
import { useRunStore } from '../net/store'
import type { CommandSender } from './Panels'
import { type StoppedCard, conversationHeader, resolvePayload, stoppedCard } from './conversation-model'

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
    </aside>
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
