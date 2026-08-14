/**
 * The chain strip, mounted.
 *
 * The ordering rule and the drawing live in `strip.ts`. This is the canvas and the
 * subscriptions: two narrow slices, redrawn when either moves, and no animation — the strip is
 * a readout, and the cascade belongs to the surface where the causality is visible.
 */

import { useEffect, useMemo, useRef } from 'react'
import { useShallow } from 'zustand/react/shallow'

import { type ItemStatus, useRunStore } from '../net/store'
import { PIP_HEIGHT } from './nodes'
import { STRIP_PADDING, drawStrip, stripOrder, stripWidth } from './strip'

export function ChainStrip() {
  const canvasRef = useRef<HTMLCanvasElement | null>(null)

  const catalog = useRunStore(useShallow((state) => state.genesis?.catalog ?? []))
  const statuses = useRunStore(
    useShallow((state) =>
      Object.fromEntries(Object.entries(state.items).map(([id, item]) => [id, item.status])),
    ),
  )

  const entries = useMemo(
    () =>
      stripOrder(
        catalog,
        Object.fromEntries(
          Object.entries(statuses).map(([id, status]) => [id, { status: status as ItemStatus }]),
        ),
      ),
    [catalog, statuses],
  )

  const width = stripWidth(entries.length)
  const height = PIP_HEIGHT + STRIP_PADDING * 2

  useEffect(() => {
    const canvas = canvasRef.current
    if (canvas === null) return
    canvas.width = width
    canvas.height = height
    const context = canvas.getContext('2d')
    if (context === null) return
    context.imageSmoothingEnabled = false
    // No animation: the strip is a readout, and the cascade belongs to the surface where the
    // causality is visible.
    drawStrip(context, entries, width, height)
  }, [entries, width, height])

  return (
    <div className="strip" data-items={entries.length}>
      <canvas ref={canvasRef} width={width} height={height} aria-label="Chain" />
    </div>
  )
}
