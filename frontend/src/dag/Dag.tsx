/**
 * The DAG view: a canvas, a cascade, and a lifecycle.
 *
 * Everything it draws lives in `draw.ts`. What is left here is the part that is genuinely
 * about React — one narrow subscription per slice, a frame handle that is cancelled on
 * cleanup, and a cascade that restarts on a status change and then stops.
 */

import { useEffect, useMemo, useRef } from 'react'
import { useShallow } from 'zustand/react/shallow'

import { type ItemStatus, useRunStore } from '../net/store'
import { canvasSize } from './layout'
import {
  CASCADE_DURATION_MS,
  buildModel,
  drawGraph,
  prefersReducedMotion,
} from './draw'

export interface DagProps {
  /** False while the office holds the stage. The loop stops rather than drawing unseen. */
  active?: boolean
}

export function Dag({ active = true }: DagProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const frameRef = useRef<number | null>(null)

  const catalog = useRunStore(useShallow((state) => state.genesis?.catalog ?? []))
  const statuses = useRunStore(
    useShallow((state) =>
      Object.fromEntries(Object.entries(state.items).map(([id, item]) => [id, item.status])),
    ),
  )
  const load = useRunStore(useShallow((state) => state.load))
  const ceiling = useRunStore((state) => state.genesis?.load.ceiling ?? 1000)

  const model = useMemo(
    () =>
      buildModel(
        catalog,
        Object.fromEntries(
          Object.entries(statuses).map(([id, status]) => [id, { status: status as ItemStatus }]),
        ),
        load,
        ceiling,
      ),
    [catalog, statuses, load, ceiling],
  )

  const size = useMemo(() => canvasSize(model.placements), [model.placements])

  // The cascade restarts whenever a node's status changes — that is the event it exists to
  // reveal. Keyed on the statuses rather than on a timer, so a run that changes nothing draws
  // one final frame and stops.
  const cascadeKey = useMemo(() => JSON.stringify(statuses), [statuses])

  // The drawn model, held in a ref so a load-only change repaints on the next frame without
  // restarting the animation. `model` cannot be an effect dependency: its identity changes when
  // department load moves, and load is ambient telemetry — replaying the unlock cascade for it
  // would turn a causal signal into atmosphere, which is the one thing this view is not for.
  const modelRef = useRef(model)
  modelRef.current = model

  useEffect(() => {
    const canvas = canvasRef.current
    if (canvas === null || !active) return

    canvas.width = size.width
    canvas.height = size.height

    const context = canvas.getContext('2d')
    if (context === null) return
    context.imageSmoothingEnabled = false

    if (prefersReducedMotion()) {
      // The end state immediately. The information is in the final frame; only the reveal was
      // motion, so dropping the reveal costs nothing and keeps the signal.
      drawGraph(context, modelRef.current, 1, size.width, size.height)
      return
    }

    const started = performance.now()
    const step = (now: number) => {
      const progress = Math.min(1, (now - started) / CASCADE_DURATION_MS)
      // Read through the ref, so a load change repainting mid-cascade shows the new tint without
      // resetting the animation.
      drawGraph(context, modelRef.current, progress, size.width, size.height)
      // Stops at the end rather than looping. An animation that repeats forever would turn a
      // causal event into atmosphere, which is the one thing this view is not for.
      frameRef.current = progress < 1 ? requestAnimationFrame(step) : null
    }
    frameRef.current = requestAnimationFrame(step)

    return () => {
      if (frameRef.current !== null) {
        cancelAnimationFrame(frameRef.current)
        frameRef.current = null
      }
    }
    // `model` is deliberately absent: it changes on ambient load movement, and restarting the
    // cascade for that would spend a causal animation on telemetry. `cascadeKey` is the status
    // fingerprint, which is the change the cascade exists to reveal.
  }, [active, size.width, size.height, cascadeKey])

  return (
    <canvas
      ref={canvasRef}
      className="dag"
      width={size.width}
      height={size.height}
      aria-label="Work chain"
    />
  )
}
