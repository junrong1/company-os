/**
 * The shell: one stage, two views over it, and everything else around the edge.
 *
 * **The stage toggles; it does not split.** The office is where the CEO acts, the DAG is
 * where they orient, and those are different jobs at different cadences. A split view leaves
 * both cramped and a panel tab leaves the DAG unusable in a narrow rail, so `Tab` swaps what
 * the stage renders and the chain strip persists in both — which is what makes the toggle
 * safe, because blocked work stays visible while you are standing in the office.
 *
 * **Every lifecycle here is symmetric.** The renderer, the event stream, and the keyboard
 * listeners all set up and tear down in pairs, and none of them is guarded by a "did we
 * already do this" ref. Strict mode runs an extra setup–cleanup–setup cycle in development
 * precisely to catch the asymmetric version, and a guard makes double-*stop* safe while
 * leaving double-*start* leaking — which is the direction strict mode actually exercises.
 *
 * **The canvas does not read React state.** The renderer pulls actors out of the store inside
 * its own frame callback. A high-frequency stream driving component state would re-render the
 * tree at the tick rate to produce pixels React never touches.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useShallow } from 'zustand/react/shallow'

import { PAL } from '../design/tokens'
import { ChainStrip } from '../dag/ChainStrip'
import { Dag } from '../dag/Dag'
import { Renderer } from '../render/index'
import { EventStream, newIdempotencyKey, submitCommand } from '../net/stream'
import { runState, subscribeTo, useRunStore } from '../net/store'
import { comparePayload } from './comparison-model'
import { Conversation } from './Conversation'
import { selectConversation } from './conversation-model'
import {
  TALK_HINT,
  WALK_HINT,
  dismiss,
  loadDismissed,
  pendingHints,
  saveDismissed,
} from './hints'
import { Hud } from './Hud'
import { Panels } from './Panels'
import {
  CeoPrediction,
  KEY_BITS,
  type Stage,
  actorsFromStore,
  bitmaskFor,
  inputLeadTicks,
  nextStage,
  posedPeople,
  shouldRestateHeldInput,
  typingTarget,
} from './stage'
import { Tree } from '../universe/Tree'
import './shell.css'

export interface ShellProps {
  runId: string
  /**
   * Start another run. Offered when this one has ended (R26).
   *
   * Owned by the app rather than by the shell, because starting a run replaces the run the
   * shell exists to render — the shell cannot both be the thing that starts it and the thing
   * that gets torn down and rebuilt for it.
   */
  onStartRun?: () => void
  /**
   * Enter another timeline of this lineage (U17).
   *
   * Owned by the app for the same reason `onStartRun` is: it replaces the run the shell is
   * attached to. The shell asks the backend, and the app moves the address bar and the run id —
   * so the store is cleared and the stream re-subscribed by the one component that owns both.
   */
  onEnterTimeline?: (runId: string) => void
  /**
   * Injectable so the suite can mount the shell without a socket.
   *
   * Must be referentially stable across renders — it is an effect dependency, and a fresh inline
   * arrow would tear the socket down and reconnect on every render.
   */
  makeStream?: (runId: string, onFrame?: () => void) => { start(): void; stop(): void }
  /**
   * Where the first-run hints remember having been dismissed (M7).
   *
   * Injectable for the same reason the HUD's is: a hint that survives a reload is the whole
   * property, and asserting it against a real browser store would make one test's dismissal
   * the next test's starting state. `null` disables persistence, which is also what a browser
   * with storage blocked looks like.
   */
  storage?: Pick<Storage, 'getItem' | 'setItem'> | null
}

export function Shell({
  runId,
  makeStream,
  onStartRun,
  onEnterTimeline,
  storage,
}: ShellProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const stageRef = useRef<HTMLDivElement | null>(null)
  const [stage, setStage] = useState<Stage>('office')
  const [rejection, setRejection] = useState<string | null>(null)
  const [startingRun, setStartingRun] = useState(false)

  // Who the CEO is standing next to. Held as React state because a panel renders from it, but
  // *decided* in the frame loop, because it depends on the predicted position — which React
  // never sees — and on where the staff have walked to.
  const [nearby, setNearby] = useState<string | null>(null)
  const nearbyRef = useRef<string | null>(null)

  // Rebuilt when the run changes, so starting a second run does not inherit the first one's
  // position or its scheduled inputs. Keyed during render rather than reset in an effect: the
  // very first frame of the new run must not draw the old run's CEO.
  const predictionRef = useRef<{ runId: string; value: CeoPrediction } | null>(null)
  if (predictionRef.current === null || predictionRef.current.runId !== runId) {
    predictionRef.current = { runId, value: new CeoPrediction() }
  }
  const prediction = predictionRef.current.value

  // Which of the two first-run hints are done with. Read once, at mount, from the browser
  // rather than from the run: a hint scoped to the run would come back on the second one, and
  // the product is built around comparing two runs in a sitting.
  const resolvedStorage =
    storage === undefined ? (typeof localStorage === 'undefined' ? null : localStorage) : storage
  const [dismissed, setDismissed] = useState<string[]>(() => loadDismissed(resolvedStorage))

  useEffect(() => {
    saveDismissed(resolvedStorage, dismissed)
  }, [resolvedStorage, dismissed])

  const connection = useRunStore((state) => state.connection)
  const sequenceGap = useRunStore((state) => state.sequenceGap)
  const diverged = useRunStore((state) => state.diverged)
  const lastError = useRunStore((state) => state.lastError)
  const rate = useRunStore((state) => state.rate)
  const terminal = useRunStore(useShallow((state) => state.terminal))
  const hasGenesis = useRunStore((state) => state.genesis !== null)

  // --- the event stream -------------------------------------------------
  useEffect(() => {
    const stream =
      makeStream === undefined ? new EventStream({ runId }) : makeStream(runId)
    stream.start()
    return () => stream.stop()
  }, [runId, makeStream])

  // --- the renderer -----------------------------------------------------
  useEffect(() => {
    const canvas = canvasRef.current
    if (canvas === null) return

    const floor = runState().genesis?.floor
    prediction.useFloor(floor)

    let seenEcho: unknown = null

    const renderer = new Renderer({
      canvas,
      floor,
      // Which of each person's three appearances this run shows. Read here and sent nowhere:
      // the kernel never learns the answer, so a cosmetic choice cannot reach the simulation
      // because there is no path by which it could.
      runSeed: runState().genesis?.runSeed ?? 0,
      // Read straight from the store inside the frame callback. No subscription, because a
      // notification would only tell the loop something its next frame was going to read
      // anyway.
      actors: (tick) => actorsFromStore(prediction.pose(), tick),
      onFrame: (tick) => {
        // The render clock is the client's estimate of the kernel's tick — smooth, and
        // re-anchored every time the authority speaks. Walking the prediction along it is
        // what makes movement cost sim-time: at ×3 the clock advances three times as fast,
        // so the CEO covers three times the ground per wall-second, and at rate zero it is
        // clamped and the CEO stands still.
        prediction.advanceTo(tick)

        // Compared once per echo rather than once per frame: `reconcile` re-walks the ticks
        // since the echoed one, which is wasted work on an echo already accounted for.
        const echo = runState().ceoEcho
        if (echo !== null && echo !== seenEcho) {
          seenEcho = echo
          // Cleared as well as set. One disagreeing echo used to latch the banner for the rest
          // of the run, so a single transient correction left a permanent claim that the
          // client and kernel disagree — long after they had stopped.
          runState().markDiverged(prediction.reconcile(echo))
        }

        // Recomputed every frame from *both* parties' positions: staff walk to desks and to
        // meetings, so the person the CEO is talking to can leave a conversation the CEO is
        // standing perfectly still in. Pushed into React only when the answer changes, so a
        // frame loop does not re-render the tree sixty times a second.
        //
        // Posed at the clock's tick rather than read from the store, because the store holds
        // where a walk *began*. Reading that would price proximity against a desk somebody left
        // a sim-hour ago, and the conversation would open on a person standing across the room.
        const next = selectConversation(
          prediction.pose(),
          posedPeople(runState().people, tick),
          nearbyRef.current,
        )
        if (next !== nearbyRef.current) {
          nearbyRef.current = next
          setNearby(next)
        }
      },
    })

    // The clock is the client's best estimate of the tick the kernel is on, so it is also what
    // an input is tagged against. Published through a ref rather than state: `sendInput` is a
    // callback, and re-creating it on every tick would rebind the keyboard listeners.
    readClockTick.current = () => renderer.clock.tick

    // Nothing fed the render clock, so it sat at its start tick reporting itself stalled. The
    // authority is the store's tick — moved by events, and between them by the position echo.
    const unsubscribeTick = subscribeTo(
      (state) => state.tick,
      (tick) => renderer.clock.onAuthoritativeTick(tick),
      true,
    )
    const unsubscribeRate = subscribeTo(
      (state) => state.rate,
      (rate) => renderer.clock.onRateChange(rate, runState().tick),
      true,
    )
    // Genesis and a resync are the two moments the wire states a CEO position outright. Both
    // replace the prediction rather than correcting it: there is no earlier prediction that
    // could be right, and an echo checked against a pre-seed history would compare timelines.
    const unsubscribeCeo = subscribeTo(
      (state) => state.ceo,
      (ceo) => prediction.seed(ceo, runState().tick),
      true,
    )

    const resize = () => {
      const host = stageRef.current
      if (host === null) return
      renderer.resize(host.clientWidth, host.clientHeight)
    }
    resize()
    renderer.addListener(window, 'resize', resize)

    // The stage resizes without the window resizing — the banner appearing, a HUD tile being
    // removed, the composer opening. A window listener alone leaves the canvas at a stale integer
    // zoom until something unrelated happens to fire.
    let observer: ResizeObserver | null = null
    if (typeof ResizeObserver !== 'undefined' && stageRef.current !== null) {
      observer = new ResizeObserver(resize)
      observer.observe(stageRef.current)
    }

    renderer.start()

    // Symmetric: everything `start`, `addListener`, `observe` and `subscribeTo` did, undone.
    return () => {
      readClockTick.current = null
      unsubscribeTick()
      unsubscribeRate()
      unsubscribeCeo()
      observer?.disconnect()
      renderer.dispose()
    }
  }, [hasGenesis, prediction])

  // --- CEO input --------------------------------------------------------
  const pressed = useRef(new Set<string>())
  const lastMask = useRef(0)
  /** Reads the render clock's tick. Set by the renderer effect, which owns the clock. */
  const readClockTick = useRef<(() => bigint) | null>(null)

  const sendInput = useCallback(
    (mask: number, force = false) => {
      // `force` is for a resume: a key held across a pause fires no fresh keydown, so the mask
      // has not changed and the guard below would drop the one command that gets the CEO
      // walking again.
      if (mask === lastMask.current && !force) return
      lastMask.current = mask

      // The walk hint is earned rather than read: somebody who has just walked does not need
      // to be told how. Zero is excluded because a release and a blur both send it, and a hint
      // must not be cleared by the CEO letting go of a key they never pressed.
      if (mask !== 0) setDismissed((current) => dismiss(current, WALK_HINT))

      const state = runState()
      // From the *render* clock, not the store's tick. The store's tick is the last one the
      // kernel actually said out loud, and it says so rarely — events land on about five ticks
      // in twelve hundred, and the position echo speaks once a sim-hour. Tagging from it puts
      // most keypresses up to a full echo interval in the kernel's past, where they are
      // rejected for not being in the future and the CEO does not move. The render clock is
      // the smooth estimate that exists for exactly this.
      const now = readClockTick.current?.() ?? state.tick
      const atTick = now + inputLeadTicks(state.rate)

      // Predicted at the same tick the kernel is told to apply it at. Predicting it now
      // instead would feel a few ticks sharper and be wrong at every tick until the key was
      // released — a standing disagreement the echo would report as a divergence.
      prediction.hold(mask, atTick)
      // Run-length encoded by construction: one command per *change* of held direction, not
      // one per frame. Roughly 36 rows a second of walking becomes one row a keypress.
      //
      // The tick goes out as a **string**, matching the convention every tick on the wire follows.
      // `Number(atTick)` would work for the whole of a normal run and start silently losing the low
      // bits above 2^53 — and this is the value the kernel replays the CEO's position from, so the
      // failure would be a divergence rather than a rounding error. The kernel already coerces it
      // with `int(...)`, which accepts a string.
      void submitCommand(
        runId,
        'submit_ceo_input',
        { bitmask: mask, at_tick: atTick.toString() },
        newIdempotencyKey('ceo'),
      )
        .then((outcome) => {
          // Movement swallows *transport* errors on purpose — a dropped input is a missed
          // step, and the next change re-states the whole held direction. A refusal is not
          // that. The prediction has already applied this input locally, so a kernel that
          // declined it leaves the two disagreeing until the next echo up to a sim-hour
          // later, and the only thing the player would see is a divergence banner with no
          // cause. Paused is the common case; a tick that has already passed is the one that
          // would otherwise be a mystery.
          if (outcome.produced_seq.length === 0 && outcome.reason !== '') {
            setRejection(outcome.reason)
          }
        })
        .catch(() => {
          // A dropped input is a missed step, not a broken client. The next change re-states
          // the whole held direction, so the error needs no recovery of its own.
        })
    },
    [runId, prediction],
  )

  // The other half of earning a hint: the CEO is standing next to somebody, so the panel that
  // opened has already said what the hint was going to. Driven off `nearby` rather than off the
  // panel's own render, because the panel is what the hint describes and a hint that outlives
  // the thing it describes is the failure being avoided.
  useEffect(() => {
    if (nearby !== null) setDismissed((current) => dismiss(current, TALK_HINT))
  }, [nearby])

  // A key held across a pause fires no keydown on resume, so without re-stating the held
  // direction the CEO stays frozen until the player lets go and presses again — which reads
  // as the resume having failed.
  const previousRate = useRef(rate)
  useEffect(() => {
    const held = bitmaskFor(pressed.current)
    const restate = shouldRestateHeldInput(previousRate.current, rate, held)
    previousRate.current = rate
    if (restate) sendInput(held, true)
  }, [rate, sendInput])

  useEffect(() => {
    const down = (event: KeyboardEvent) => {
      // The stage's keys are only the stage's while the stage has the keyboard. Inside a form
      // control, Tab is how you leave it and the arrows are how you pick a radio option — and the
      // decision tray is built out of radios. Swallowing them globally would make the tray
      // unusable without a mouse and would remove focus navigation from the whole app.
      if (typingTarget(event.target)) return

      if (event.key === 'Tab') {
        event.preventDefault()
        setStage(nextStage)
        return
      }
      if (!(event.key in KEY_BITS)) return
      event.preventDefault()
      pressed.current.add(event.key)
      sendInput(bitmaskFor(pressed.current))
    }

    const up = (event: KeyboardEvent) => {
      if (!(event.key in KEY_BITS)) return
      // Deliberately *not* gated on the focus target: a key pressed on the stage and released
      // after focus moved must still clear, or the CEO walks into a wall forever.
      pressed.current.delete(event.key)
      sendInput(bitmaskFor(pressed.current))
    }

    // Releasing the window with a key held would otherwise leave the CEO walking into a wall
    // forever, because the keyup lands somewhere else.
    const blur = () => {
      pressed.current.clear()
      sendInput(0)
    }

    window.addEventListener('keydown', down)
    window.addEventListener('keyup', up)
    window.addEventListener('blur', blur)
    return () => {
      window.removeEventListener('keydown', down)
      window.removeEventListener('keyup', up)
      window.removeEventListener('blur', blur)
    }
  }, [sendInput])

  const command = useCallback(
    (kind: string, payload: Record<string, unknown>) => {
      setRejection(null)
      submitCommand(runId, kind, payload, newIdempotencyKey(kind))
        .then((outcome) => {
          // A rejection is a *successful* request whose answer is "no", and the reason is a
          // sentence written to be shown. Applying is reported through the events the command
          // produced, so only the answers that produce no events need surfacing here — otherwise
          // crossing a reporting line, or acting on a paused or ended run, fails in silence and
          // reads as a dead button.
          if (outcome.produced_seq.length === 0 && outcome.reason !== '') {
            setRejection(outcome.reason)
          }
        })
        .catch((cause: unknown) => {
          // A transport failure is recoverable by asking for the key's outcome rather than
          // retrying blind, but the user still has to be told the command may not have landed.
          setRejection(cause instanceof Error ? cause.message : String(cause))
        })
    },
    [runId],
  )

  const setRate = useCallback(
    (next: number) => command('set_rate', { rate: next }),
    [command],
  )

  /**
   * Ask for a branch comparison.
   *
   * Built here rather than in the panel that offers it, for the same reason `sendInput` is:
   * the tag comes from the *render* clock, and the render clock lives with the renderer that
   * drives it. The store's tick is the last one the kernel actually said out loud — events
   * land on about five ticks in twelve hundred — so a request tagged from it would describe a
   * moment well inside the run's past. That was a real defect on the input path, and threading
   * a tick getter down through two layers of panel is how the client would end up with a
   * second answer to "what time is it".
   */
  const compare = useCallback(
    (itemId: string, cpIndex: number, personId: string, inPerson: boolean) => {
      const now = readClockTick.current?.() ?? runState().tick
      command('compare_options', comparePayload(itemId, cpIndex, personId, now, inPerson))
    },
    [command],
  )

  const hints = useMemo(() => pendingHints(dismissed), [dismissed])

  const banner = useMemo(() => {
    if (terminal !== null) return `The run ended: ${terminal.reason}`
    if (rejection !== null) return rejection
    if (sequenceGap) return 'The stream lost an event; reconnecting will resume from the gap.'
    if (connection === 'lost') return lastError ?? 'The event stream dropped.'
    if (diverged) return 'The client and the kernel disagree about where the CEO is.'
    // A live stream can still carry a diagnostic — a frame this client could not read leaves a
    // hole in the sequence, and reporting it only when the socket drops would mean never.
    if (lastError !== null) return lastError
    return null
  }, [terminal, rejection, sequenceGap, connection, lastError, diverged])

  return (
    <main className="shell" data-stage={stage} data-connection={connection}>
      <header className="topbar">
        <h1>Company OS</h1>
        <nav className="stage-toggle" aria-label="Stage">
          <button
            type="button"
            data-active={stage === 'office'}
            onClick={() => setStage('office')}
          >
            Office
          </button>
          <button type="button" data-active={stage === 'dag'} onClick={() => setStage('dag')}>
            Chain
          </button>
          {/* Third, and last in the Tab cycle: it is the view of every timeline rather than of
              this one, so reaching it is a deliberate act. */}
          <button
            type="button"
            data-active={stage === 'universe'}
            onClick={() => setStage('universe')}
          >
            Universe
          </button>
          <span className="hint">Tab</span>
        </nav>
        <div className="rates" aria-label="Clock">
          {[0, 1, 3].map((option) => (
            <button
              key={option}
              type="button"
              data-active={rate === option}
              onClick={() => setRate(option)}
            >
              {option === 0 ? 'Pause' : `×${option}`}
            </button>
          ))}
        </div>
      </header>

      {banner !== null && (
        <p className="banner">
          {banner}
          {/* A finished run is a dead screen otherwise, and the whole point of a run this
              short is that two of them can be compared in one sitting (R26). */}
          {terminal !== null && onStartRun !== undefined && (
            <button
              type="button"
              className="banner__action"
              // Disabled on the first press: creation is a round trip, and two presses would
              // make two runs and orphan one of them.
              disabled={startingRun}
              onClick={() => {
                setStartingRun(true)
                onStartRun()
              }}
            >
              {startingRun ? 'Starting…' : 'Start another'}
            </button>
          )}
        </p>
      )}

      {/* Not before there is a world. The HUD reads metrics, cash and load, and with an empty
          store every tile renders a real-looking zero — which is what a player sees for the whole
          of an attach, and for the moment between leaving one timeline and the next one's genesis
          landing. A stated absence is the honest frame for both. */}
      {hasGenesis ? (
        <Hud />
      ) : (
        <p className="shell__attaching" style={{ color: PAL.textFaint }}>
          {connection === 'lost'
            ? 'The stream is down. Nothing below is current.'
            : 'Attaching to the timeline.'}
        </p>
      )}

      <div className="stage" ref={stageRef}>
        {/* Both views stay mounted. Unmounting the canvas on every toggle would tear down the
            sprite sheets and the frame loop, and rebuild them on the way back — the toggle is
            supposed to be free, and the office has to be rendering unchanged when you
            return. */}
        <canvas ref={canvasRef} className="office" data-hidden={stage !== 'office'} />
        <div className="dag-host" data-hidden={stage !== 'dag'}>
          <Dag active={stage === 'dag'} />
        </div>
        {/* Hidden rather than unmounted, like the DAG: the tree is a read the player comes back to,
            and re-fetching it on every trip through the stage toggle would spend a round trip to
            show the same nodes. `active` stops the fetch and the paint while it is not on stage. */}
        <div className="universe-host" data-hidden={stage !== 'universe'}>
          <Tree
            runId={runId}
            active={stage === 'universe'}
            onEnter={(next) => {
              // The shell asks and the app moves. Everything run-scoped in the store is cleared
              // here rather than in the app, because the store is what the incoming stream writes
              // into and its sequence guard drops anything at or below the sequence it already
              // applied — so a new timeline's frames would be dropped wholesale without this.
              runState().reset()
              setRejection(null)
              onEnterTimeline?.(next)
            }}
          />
        </div>

        {/* Only over the office, for the same reason the conversation is: both hints describe
            things you do on the floor, and neither is performable on the chain view. Opposite
            corner from the conversation, so earning the second hint never hides the panel that
            proves it was earned. */}
        {stage === 'office' && hints.length > 0 && (
          <ul className="hints" aria-label="Getting started">
            {hints.map((hint) => (
              <li key={hint.id} className="hints__card" data-hint={hint.id}>
                {hint.text}
                <button
                  type="button"
                  className="hints__dismiss"
                  // Labelled with the hint it closes: two buttons reading "Got it" are two
                  // identical controls to a screen reader moving through them by label.
                  aria-label={`Got it: ${hint.text}`}
                  onClick={() => setDismissed((current) => dismiss(current, hint.id))}
                >
                  Got it
                </button>
              </li>
            ))}
          </ul>
        )}

        {/* Only over the office. On the chain view there is no floor to be standing on, and a
            conversation panel there would claim a proximity the stage is not showing. */}
        {stage === 'office' && (
          <Conversation
            personId={nearby}
            runId={runId}
            onCommand={command}
            onCompare={compare}
          />
        )}
      </div>

      {/* Persists in both modes: a severed chain shows violet while you are still standing in
          the office, so you never need to open the DAG to learn that something stopped. */}
      <ChainStrip />

      <Panels runId={runId} onCommand={command} onCompare={compare} />
    </main>
  )
}
