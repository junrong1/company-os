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

import { NO_METRIC_DEFS, PAL, deptColour } from '../design/tokens'
import { type AnsweredQuestion, useRunStore } from '../net/store'
import { CompareAffordance } from './Comparison'
import type { CompareSender } from './comparison-model'
import { OptionConsequence } from './Consequence'
import { AuthorizationCard, type CommandSender } from './Panels'
import { Described, Scripted } from './Marking'
import {
  type AssignableItem,
  type BenchBlock,
  type PersonSchema,
  type StoppedCard,
  askPayload,
  askable,
  assignCost,
  assignableWork,
  benchBlock,
  conversationHeader,
  personSchema,
  resolvePayload,
  stoppedCard,
} from './conversation-model'
import { MemoryAffordance } from './Memory'
import { posedPeople } from './stage'

/**
 * The empty answer list, as one shared value.
 *
 * A fresh `[]` from the selector would be a new reference every call, and the store's equality
 * check is `Object.is` on the selector's output — so an unasked person would re-render this
 * panel forever.
 */
const EMPTY_ANSWERS: AnsweredQuestion[] = []

export interface ConversationProps {
  /** Who the CEO is standing next to, decided by the model from both parties' positions. */
  personId: string | null
  /**
   * Which run this conversation is in, for the memory affordance in the header (U14).
   *
   * Optional so every existing caller and every test that mounts this panel without a run keeps
   * working: with no run named the header offers no memory, which is the honest state rather than
   * a button that cannot say what it would read.
   */
  runId?: string
  onCommand?: CommandSender
  onCompare?: CompareSender
}

export function Conversation({ personId, runId, onCommand, onCompare }: ConversationProps) {
  const header = useRunStore(
    useShallow((state) =>
      conversationHeader(
        personId,
        state.genesis?.roster ?? {},
        // Posed at the store's tick: the recorded state of somebody whose walk has ended is
        // `walking`, because arrival is in no event, and the header would say "Walking over"
        // about a person standing still in front of the CEO.
        posedPeople(state.people, state.tick),
        state.tray,
        state.items,
      ),
    ),
  )
  const stopped = useRunStore(
    useShallow((state) =>
      stoppedCard(personId, state.tray, state.genesis?.catalog ?? [], state.tacitLines),
    ),
  )
  // The array itself is replaced whenever this person answers, so reference equality is
  // exactly the right check and no shallow comparison is needed.
  const answers = useRunStore((state) =>
    personId === null ? EMPTY_ANSWERS : (state.answers[personId] ?? EMPTY_ANSWERS),
  )
  // Subscribed narrowly and combined outside the selector, rather than calling `stoppedCard` a
  // second time inside one: the card is already selected above, and a selector that rebuilt it
  // would do the tray scan twice per render for one value.
  const statements = useRunStore(useShallow((state) => state.statements))
  const benchPresent = useRunStore((state) => state.spend.benchPresent)

  // What this person has asked the CEO to allow (U15, M40). Selected by asking director rather
  // than by item, because the question belongs to the person the CEO is standing in front of —
  // the tray answers it from across the floor, and this answers it in front of them.
  const asks = useRunStore(
    useShallow((state) =>
      Object.values(state.authorizations).filter((entry) => entry.asking === personId),
    ),
  )

  // Genesis is written once and never replaced, so these are stable by reference and need no
  // shallow comparison.
  const roster = useRunStore((state) => state.genesis?.roster)
  const catalog = useRunStore((state) => state.genesis?.catalog)

  // Derived from a value that cannot change within a run, so it is memoised on the person
  // rather than subscribed to: the schema is scenario content, and a scenario is immutable for
  // the life of the run.
  const schema = useMemo(() => personSchema(personId, roster ?? {}), [personId, roster])

  // Read off the roster rather than inferred from having a memory: the route answers 404 for a
  // specialist, and finding that out by rendering a button and pressing it is not a design.
  const isDirector = personId !== null && roster?.[personId]?.rank === 'director'

  const bench = useMemo(
    () => benchBlock(stopped, statements, benchPresent),
    [stopped, statements, benchPresent],
  )

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
        {/* In the header rather than below the decision, and only for a director. It answers "what
            do you remember", which is a question about the person you are standing in front of —
            unlike the briefing below, which is about the decision they are stopped at. A
            specialist has no memory to offer: they answer from an authored script (M14). */}
        {runId !== undefined && isDirector && (
          <MemoryAffordance runId={runId} directorId={header.id} name={header.name} />
        )}
      </header>

      {schema !== null && <Schema schema={schema} name={header.name} />}

      {/* Above the decision, unlike the briefing below it, because this is not something to read
          while deciding — it is a second thing to decide, and it is stopping work right now. The
          same card the tray renders, so the two surfaces cannot ask the question two ways. */}
      {asks.map((ask) => (
        <AuthorizationCard
          key={ask.requestId || ask.itemId}
          entry={ask}
          item={(catalog ?? []).find((entry) => entry.id === ask.itemId)}
          nameOf={(id) => roster?.[id]?.name ?? id}
          onDecide={onCommand}
        />
      ))}

      {stopped !== null && (
        <Decision
          key={`${stopped.itemId}:${stopped.cpIndex}`}
          card={stopped}
          personId={header.id}
          onCommand={onCommand}
          onCompare={onCompare}
        />
      )}

      {/* Below the decision, never above it. The options are what the CEO walked over for and what
          they can act on; a briefing is what they read while deciding. Above the card it would put
          a paragraph — often a pending one — between the player and the only buttons on the panel. */}
      {bench !== null && <Bench block={bench} name={header.name} />}

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

      {/* Askable in every state. Someone stopped at a decision is exactly who it is worth
          asking why, and that is the mechanic rather than an oversight. */}
      <Ask personId={header.id} answers={answers} onCommand={onCommand} />
    </aside>
  )
}

/**
 * What this person is responsible for, and what they are described as having (M15).
 *
 * **Above the decision, below the name.** It is the answer to "who am I standing in front of",
 * which is the question the CEO has at the moment the panel opens and before they read what the
 * person is stopped on. Under the ask box it would be a footnote about someone whose decision
 * you had already taken.
 *
 * **Nothing here is interactive, and that is the design rather than an omission** (M16). A tool
 * renders as a word, not a button: a name in one of these lists is what a person is understood
 * to have, and no code path in this repository turns one into a call. Making it clickable would
 * promise a capability the product does not have — so the marking says "described, not wired
 * up" and there is nothing to press to find out.
 */
function Schema({ schema, name }: { schema: PersonSchema; name: string }) {
  return (
    <section className="conversation__schema">
      {schema.responsibility !== '' && (
        <p className="schema__responsibility">
          {schema.responsibility} <Described of={`what ${name} is responsible for`} />
        </p>
      )}

      {schema.lists.map((list) => (
        <div key={list.key} className="schema__list" data-schema={list.key}>
          <h4>
            {list.label} <Described of={`${name}'s ${list.label.toLowerCase()}`} />
          </h4>
          <ul>
            {list.entries.map((entry) => (
              <li key={entry}>{entry}</li>
            ))}
          </ul>
        </div>
      ))}

      {/* Said once, in words, under the lists it is about. The glyph beside each heading is the
          marking; this is the sentence a reader needs the first time they meet it. */}
      <p className="schema__note" style={{ color: PAL.textFaint }}>
        Described, not wired up — nothing here is executed.
      </p>
    </section>
  )
}

/**
 * The ask box, and what this person has already said.
 *
 * Free text rather than four buttons. Buttons would remove the guessing that fixed answers
 * create, but free text needs no rework when a hearing API replaces the script — that change
 * swaps the producer, not the surface. Misses are therefore expected, and each person has a
 * line for one.
 *
 * The answers come from the store rather than from state held here, which is what makes them
 * survive walking away and coming back, and survive a reload.
 */
function Ask({
  personId,
  answers,
  onCommand,
}: {
  personId: string
  answers: AnsweredQuestion[]
  onCommand?: CommandSender
}) {
  const [question, setQuestion] = useState('')

  const send = () => {
    if (!askable(question)) return
    onCommand?.('ask_person', askPayload(personId, question))
    setQuestion('')
  }

  return (
    <section className="conversation__ask">
      <form
        onSubmit={(event) => {
          event.preventDefault()
          send()
        }}
      >
        <input
          type="text"
          value={question}
          placeholder="Ask them something…"
          aria-label={`Ask ${personId} a question`}
          onChange={(event) => setQuestion(event.target.value)}
        />
        <button type="submit" disabled={!askable(question)}>
          Ask
        </button>
      </form>

      {answers.length === 0 && (
        <p className="hint">Try why, exceptions, who decides, or where the time goes.</p>
      )}

      {answers.map((said, index) => (
        <article
          // Indexed because the same question asked twice is two entries with identical
          // content, and the list is newest-first so an index is stable for a given render.
          key={`${said.tick}:${index}`}
          className="said"
          data-tacit={said.tacit && said.firstTime}
          data-matched={said.question !== ''}
        >
          <p className="said__q">{said.question === '' ? said.asked : said.label}</p>
          <p className="said__a">{said.answer}</p>
        </article>
      ))}
    </section>
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
function Decision({
  card,
  personId,
  onCommand,
  onCompare,
}: {
  card: StoppedCard
  personId: string
  onCommand?: CommandSender
  onCompare?: CompareSender
}) {
  const [chosen, setChosen] = useState<number | null>(null)
  // Genesis is written once and never replaced, so this is stable by reference.
  const metricDefs = useRunStore((state) => state.genesis?.metricDefs) ?? NO_METRIC_DEFS

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
          {/* The prose price and the arithmetic price, together. The detail line says what the
              option does; the figures say what it costs. Withholding the second was the old
              behaviour, and the marking is what makes showing it defensible (R35). */}
          <OptionConsequence option={option} metricDefs={metricDefs} />
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

      {/* Under the options, never in place of them. Closing the comparison returns the CEO to
          exactly this list, with nothing committed (R34). `inPerson` is true here because the
          CEO is standing in front of the person — the branches price the route they are
          actually about to take. */}
      <CompareAffordance
        itemId={card.itemId}
        cpIndex={card.cpIndex}
        personId={personId}
        inPerson
        onCompare={onCompare}
      />
    </section>
  )
}

/**
 * What the director says about the decision in front of the CEO (M14, M21).
 *
 * **Four states, one block, resolving in place.** The pending state is the one it spends most of its
 * life in against a real provider, and it is a stated line rather than a spinner — a spinner says
 * "wait", and the whole point is that the CEO is not waiting: the settle action on the card above is
 * never disabled by anything here. Without the pending state a fast player settles before the
 * briefing lands and the bench is decorative, and a slow provider reads as a broken panel.
 *
 * **The briefing and the objection are two blocks, because they arrived as two fields.** A bench that
 * only agrees adds nothing to a decision the CEO was going to take anyway, so the objection is
 * required — and rendering it as a second paragraph of the briefing would let the reader skim past
 * the half that costs them something.
 *
 * **Canned prose carries the marking, and the reason is spelled out in words beside it.** Strip every
 * hue from this panel and a scripted reply is still distinguishable from a briefing: the glyph, the
 * label and the sentence all say so, and `data-scripted` is what the suite reads. That property is
 * the whole reason the marking is a component rather than three lines of JSX.
 */
function Bench({ block, name }: { block: BenchBlock; name: string }) {
  return (
    <section className="conversation__bench" data-bench={block.status}>
      <h3>
        What {name} says
        {block.scripted && <Scripted of={`what stands in for ${name}'s briefing`} />}
      </h3>

      {block.status === 'pending' && (
        <p className="bench__pending" style={{ color: PAL.textFaint }}>
          {name} is putting it together. Decide without them if you would rather.
        </p>
      )}

      {block.status === 'unanswered' && (
        <p className="bench__absent" style={{ color: PAL.textFaint }}>
          Nothing came back from {name}. {block.because}
        </p>
      )}

      {block.briefing !== '' && (
        <>
          <p className="bench__briefing">{block.briefing}</p>
          {/* Labelled, because the objection is the half a reader would otherwise take for more of
              the briefing — and it is the half that argues against what they are about to do. */}
          <p className="bench__objection">
            <span className="bench__label">Their objection</span>
            {block.objection}
          </p>
        </>
      )}

      {block.because !== '' && block.status === 'scripted' && (
        <p className="bench__because" style={{ color: PAL.textFaint }}>
          {block.because}
        </p>
      )}

      {/* Provenance, not links: nothing in this build resolves a sequence to its event yet — that is
          the report's job (M55) — so these render as what they are, which is the evidence the
          briefing was checked against. */}
      {block.citations.length > 0 && (
        <p className="bench__citations" style={{ color: PAL.textFaint }}>
          Drawn from {block.citations.map((seq) => `#${seq}`).join(', ')}
        </p>
      )}
    </section>
  )
}
