/**
 * The Universe stage: every timeline, and the one you are standing in (M49, R11).
 *
 * **Canvas, in the DAG's idiom, rather than a DOM widget.** It is the second graph in the product
 * and it is drawn on the same lattice with the same edge routing, so the player learns one visual
 * language. A tree of divs would also have needed its own hit-testing story the moment the nodes
 * stopped being rectangles in a column.
 *
 * **Everything it draws is in `model.ts`.** What is left here is React: one fetch with an abort, a
 * click that hit-tests against the same placements the paint used, and a switch that hands the new
 * run id upwards. The properties the art direction states about this surface — which border form,
 * which hue, where the standing marker is — are assertions about pure functions rather than about
 * pixels nobody can read.
 *
 * **The tree is read on demand, not streamed.** A lineage changes when somebody forks or switches,
 * both of which this client is the one doing — so it is re-read after each, and there is no second
 * subscription to keep in step with the event stream.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { PAL } from '../design/tokens'
import {
  GatewayUnreachable,
  type LineageWire,
  LineageUnavailable,
  fetchLineage,
  switchTimeline,
} from '../net/gateway'
import {
  buildTreeModel,
  canvasSize,
  describeDivergence,
  describeTimeline,
  drawTree,
  timelineAt,
  timelineState,
} from './model'

export interface TreeProps {
  /** The timeline the player is standing in. Also the one a switch is made *from*. */
  runId: string
  /** False while another view holds the stage. Nothing is fetched or painted unseen. */
  active?: boolean
  /**
   * Enter another timeline. The caller owns the run id, the address bar and the stream.
   *
   * Called only after the backend has said the switch happened, so a client cannot end up
   * subscribed to a timeline whose clock nobody moved.
   */
  onEnter?: (runId: string, rate: number) => void
  /** Injectable for the suite, which mounts this without a gateway. Must be stable across renders. */
  reader?: (runId: string, signal?: AbortSignal) => Promise<LineageWire>
  /**
   * Injectable for the same reason, and it is the write half — a test must not need a server.
   *
   * `rate` is required on the answer rather than optional, because the shell writes it into the
   * store: a double that omitted it would make this component supply a default, and the only
   * available default — assume paused — is wrong in the direction that matters. The route always
   * states it, so the type says so.
   */
  switcher?: (
    runId: string,
    to: string,
    rate?: number,
  ) => Promise<{ refusal: string; rate: number }>
  /**
   * Open the aside on this node when the stage is entered (U25).
   *
   * A fork made somewhere else lands the player here, and landing them on the tree with nothing
   * selected would answer "what did that do?" with a graph they then have to find themselves in.
   * A hint rather than a controlled value: clicking another node still selects it, and this only
   * moves the selection when it changes.
   */
  selected?: string | null
}

export function Tree({
  runId,
  active = true,
  onEnter,
  reader,
  switcher,
  selected: opened = null,
}: TreeProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const [lineage, setLineage] = useState<LineageWire | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [selected, setSelected] = useState<string | null>(null)
  const [refusal, setRefusal] = useState<string | null>(null)
  const [entering, setEntering] = useState<string | null>(null)

  const read = reader ?? fetchLineage
  const write = switcher ?? switchTimeline

  useEffect(() => {
    if (!active) return

    const controller = new AbortController()
    setError(null)
    read(runId, controller.signal)
      .then((next) => {
        if (controller.signal.aborted) return
        setLineage(next)
      })
      .catch((cause: unknown) => {
        if (controller.signal.aborted) return
        setError(
          cause instanceof LineageUnavailable || cause instanceof GatewayUnreachable
            ? cause.message
            : String(cause),
        )
      })

    return () => controller.abort()
  }, [active, read, runId])

  // The caller's node, taken as the selection whenever it changes. Compared against what was
  // last taken rather than against the current selection, so a player who clicks away from a
  // forked node is not dragged back to it on the next render.
  const takenRef = useRef<string | null>(null)
  if (opened !== null && opened !== takenRef.current) {
    takenRef.current = opened
    if (opened !== selected) setSelected(opened)
  }

  const model = useMemo(() => (lineage === null ? null : buildTreeModel(lineage)), [lineage])
  const size = useMemo(
    () => (model === null ? { width: 0, height: 0 } : canvasSize(model.placements)),
    [model],
  )

  useEffect(() => {
    const canvas = canvasRef.current
    if (canvas === null || model === null || !active) return

    canvas.width = size.width
    canvas.height = size.height
    const context = canvas.getContext('2d')
    if (context === null) return
    context.imageSmoothingEnabled = false
    drawTree(context, model, size.width, size.height)
  }, [active, model, size.width, size.height])

  const click = useCallback(
    (event: React.MouseEvent<HTMLCanvasElement>) => {
      if (model === null) return
      const canvas = event.currentTarget
      const box = canvas.getBoundingClientRect()
      // Scaled from the box to the canvas, because the element is laid out by CSS and the
      // placements are in canvas pixels. Without this a click lands on the wrong node at every
      // width but one.
      const x = ((event.clientX - box.left) * canvas.width) / box.width
      const y = ((event.clientY - box.top) * canvas.height) / box.height
      const hit = timelineAt(model.placements, x, y)
      if (hit !== null) {
        setSelected(hit)
        setRefusal(null)
      }
    },
    [model],
  )

  const enter = useCallback(
    (to: string) => {
      setRefusal(null)
      setEntering(to)
      write(runId, to)
        .then((outcome) => {
          if (outcome.refusal) {
            setRefusal(outcome.refusal)
            setEntering(null)
            return
          }
          // Upwards only after the backend moved the clock, and carrying the rate it moved to:
          // a timeline's rate is the one thing this client never learns from its log, because
          // `RATE_CHANGED` is appended when a rate moves and an entered timeline resumes at the
          // rate it was left at. Re-reading the tree is the caller's job too: it happens when
          // `runId` changes, which is the effect above.
          onEnter?.(to, outcome.rate)
          setEntering(null)
        })
        .catch((cause: unknown) => {
          setRefusal(cause instanceof Error ? cause.message : String(cause))
          setEntering(null)
        })
    },
    [onEnter, runId, write],
  )

  const node = selected === null ? undefined : model?.nodes[selected]
  const full = lineage !== null && lineage.nodes.length >= lineage.cap

  return (
    <section className="universe" aria-label="Universe">
      {error !== null && (
        <p className="universe__error" style={{ color: PAL.textFaint }}>
          {error}
        </p>
      )}

      {model !== null && (
        <canvas
          ref={canvasRef}
          className="universe__tree"
          width={size.width}
          height={size.height}
          onClick={click}
          aria-label="Timelines"
        />
      )}

      <div className="universe__aside">
        {lineage !== null && (
          <p className="universe__scale" style={{ color: PAL.textFaint }}>
            {lineage.nodes.length} of {lineage.cap} timelines
            {full && ' · full, so no more forks'}
          </p>
        )}

        {node === undefined ? (
          <p className="universe__hint" style={{ color: PAL.textFaint }}>
            {model === null ? 'Reading the tree.' : 'Pick a timeline to see where it went.'}
          </p>
        ) : (
          <article className="universe__node" data-state={timelineState(node)}>
            <p className="universe__id">{node.run_id}</p>
            <p className="universe__where" style={{ color: PAL.textFaint }}>
              {describeTimeline(node)}
            </p>
            {describeDivergence(node) !== '' && (
              <p className="universe__divergence">{describeDivergence(node)}</p>
            )}

            {node.run_id === runId ? (
              <p className="universe__standing" style={{ color: PAL.textFaint }}>
                You are standing in this one.
              </p>
            ) : (
              <button
                type="button"
                className="universe__enter"
                disabled={entering !== null || timelineState(node) === 'ended'}
                onClick={() => enter(node.run_id)}
              >
                {entering === node.run_id ? 'Entering…' : 'Enter this timeline'}
              </button>
            )}

            {timelineState(node) === 'ended' && (
              <p className="universe__ended" style={{ color: PAL.textFaint }}>
                An ended timeline cannot be entered. It can still be forked, which is the point of
                keeping it.
              </p>
            )}
          </article>
        )}

        {refusal !== null && (
          <p className="universe__refusal" style={{ color: PAL.textFaint }}>
            {refusal}
          </p>
        )}
      </div>
    </section>
  )
}
