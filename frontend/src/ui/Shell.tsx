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
import { runState, useRunStore } from '../net/store'
import { Hud } from './Hud'
import { Panels } from './Panels'
import { INPUT_LEAD_TICKS, KEY_BITS, type Stage, actorsFromStore, bitmaskFor } from './stage'
import './shell.css'

export interface ShellProps {
  runId: string
  /**
   * Injectable so the suite can mount the shell without a socket.
   *
   * Must be referentially stable across renders — it is an effect dependency, and a fresh inline
   * arrow would tear the socket down and reconnect on every render.
   */
  makeStream?: (runId: string, onFrame?: () => void) => { start(): void; stop(): void }
}

/**
 * Whether a key event belongs to a form control rather than to the stage.
 *
 * The tray's options are radios and the HUD composer's toggles are checkboxes, so Tab and the
 * arrow keys have a native meaning whenever one of them holds focus.
 */
function typingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false
  const tag = target.tagName
  return (
    tag === 'INPUT' ||
    tag === 'TEXTAREA' ||
    tag === 'SELECT' ||
    tag === 'BUTTON' ||
    tag === 'SUMMARY' ||
    target.isContentEditable
  )
}

export function Shell({ runId, makeStream }: ShellProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const stageRef = useRef<HTMLDivElement | null>(null)
  const [stage, setStage] = useState<Stage>('office')

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

    const renderer = new Renderer({
      canvas,
      floor: runState().genesis?.floor,
      // Read straight from the store inside the frame callback. No subscription, because a
      // notification would only tell the loop something its next frame was going to read
      // anyway.
      actors: () => actorsFromStore(),
    })

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

    // Symmetric: everything `start`, `addListener` and `observe` did, undone.
    return () => {
      observer?.disconnect()
      renderer.dispose()
    }
  }, [hasGenesis])

  // --- CEO input --------------------------------------------------------
  const pressed = useRef(new Set<string>())
  const lastMask = useRef(0)

  const sendInput = useCallback(
    (mask: number) => {
      if (mask === lastMask.current) return
      lastMask.current = mask
      const atTick = runState().tick + INPUT_LEAD_TICKS
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
      ).catch(() => {
        // A dropped input is a missed step, not a broken client. The next change re-states
        // the whole held direction, so the error needs no recovery of its own.
      })
    },
    [runId],
  )

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

  const [rejection, setRejection] = useState<string | null>(null)

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

      {banner !== null && <p className="banner">{banner}</p>}

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
      </div>

      {/* Persists in both modes: a severed chain shows violet while you are still standing in
          the office, so you never need to open the DAG to learn that something stopped. */}
      <ChainStrip />

      <Panels onCommand={command} />
    </main>
  )
}
