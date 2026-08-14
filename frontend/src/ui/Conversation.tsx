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

import { useShallow } from 'zustand/react/shallow'

import { PAL } from '../design/tokens'
import { deptColour } from '../design/tokens'
import { useRunStore } from '../net/store'
import { conversationHeader } from './conversation-model'

export interface ConversationProps {
  /** Who the CEO is standing next to, decided by the model from both parties' positions. */
  personId: string | null
}

export function Conversation({ personId }: ConversationProps) {
  const header = useRunStore(
    useShallow((state) =>
      conversationHeader(personId, state.genesis?.roster ?? {}, state.people),
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
    </aside>
  )
}
