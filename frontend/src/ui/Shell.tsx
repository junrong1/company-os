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

import { ChainStrip } from '../dag/ChainStrip'
import { Dag } from '../dag/Dag'
import { Renderer } from '../render/index'
import { EventStream, newIdempotencyKey, submitCommand } from '../net/stream'
import { runState, subscribeTo, useRunStore } from '../net/store'
import { Conversation } from './Conversation'
import { selectConversation } from './conversation-model'
import { Hud } from './Hud'
import { Panels } from './Panels'
import {
  CeoPrediction,
  KEY_BITS,
  type Stage,
  actorsFromStore,
  bitmaskFor,
  inputLeadTicks,
  shouldRestateHeldInput,
  typingTarget,
} from './stage'
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
   * Injectable so the suite can mount the shell without a socket.
   *
   * Must be referentially stable across renders — it is an effect dependency, and a fresh inline
   * arrow would tear the socket down and reconnect on every render.
   */
  makeStream?: (runId: string, onFrame?: () => void) => { start(): void; stop(): void }
}

export function Shell({ runId, makeStream, onStartRun }: ShellProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const stageRef = useRef<HTMLDivElement | null>(null)
  const [stage, setStage] = useState<Stage>('office')
  const [rejection, setRejection] = useState<string | null>(null)

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
      // Read straight from the store inside the frame callback. No subscription, because a
      // notification would only tell the loop something its next frame was going to read
      // anyway.
      actors: () => actorsFromStore(prediction.pose()),
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
          if (prediction.reconcile(echo)) runState().markDiverged(true)
        }

        // Recomputed every frame from *both* parties' positions: staff walk to desks and to
        // meetings, so the person the CEO is talking to can leave a conversation the CEO is
        // standing perfectly still in. Pushed into React only when the answer changes, so a
        // frame loop does not re-render the tree sixty times a second.
        const next = selectConversation(prediction.pose(), runState().people, nearbyRef.current)
        if (next !== nearbyRef.current) {
          nearbyRef.current = next
          setNearby(next)
        }
      },
    })

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

  const sendInput = useCallback(
    (mask: number, force = false) => {
      // `force` is for a resume: a key held across a pause fires no fresh keydown, so the mask
      // has not changed and the guard below would drop the one command that gets the CEO
      // walking again.
      if (mask === lastMask.current && !force) return
      lastMask.current = mask
      const state = runState()
      const atTick = state.tick + inputLeadTicks(state.rate)

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
          // Movement swallows its errors on purpose — a dropped input is a missed step, and
          // the next change re-states the whole held direction. A paused run is the one
          // exception: it rejects *every* command, so that same silence would make the pause
          // button read as a broken build rather than as a stopped world.
          if (outcome.status === 'run_paused') setRejection(outcome.reason)
        })
        .catch(() => {
          // A dropped input is a missed step, not a broken client. The next change re-states
          // the whole held direction, so the error needs no recovery of its own.
        })
    },
    [runId, prediction],
  )

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
        setStage((current) => (current === 'office' ? 'dag' : 'office'))
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
            <button type="button" className="banner__action" onClick={onStartRun}>
              Start another
            </button>
          )}
        </p>
      )}

      <Hud />

      <div className="stage" ref={stageRef}>
        {/* Both views stay mounted. Unmounting the canvas on every toggle would tear down the
            sprite sheets and the frame loop, and rebuild them on the way back — the toggle is
            supposed to be free, and the office has to be rendering unchanged when you
            return. */}
        <canvas ref={canvasRef} className="office" data-hidden={stage !== 'office'} />
        <div className="dag-host" data-hidden={stage !== 'dag'}>
          <Dag active={stage === 'dag'} />
        </div>

        {/* Only over the office. On the chain view there is no floor to be standing on, and a
            conversation panel there would claim a proximity the stage is not showing. */}
        {stage === 'office' && <Conversation personId={nearby} onCommand={command} />}
      </div>

      {/* Persists in both modes: a severed chain shows violet while you are still standing in
          the office, so you never need to open the DAG to learn that something stopped. */}
      <ChainStrip />

      <Panels onCommand={command} />
    </main>
  )
}
