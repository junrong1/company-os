/**
 * The renderer, with an explicit lifecycle.
 *
 * The prototype starts its animation loop at module scope (`company-os.html:3269`) and contains no
 * cancel path anywhere in the file. That is fine for one page that never unmounts, and it breaks
 * two ways under a framework:
 *
 * **React strict mode** runs an extra setup–cleanup–setup cycle in development. A module-scope loop
 * starts once and is never torn down, so the second setup leaves two loops running, each drawing
 * the same canvas at its own cadence.
 *
 * **Vite hot update** re-evaluates the module. Non-component modules get no automatic disposal, so
 * every save leaks another loop, another set of listeners, and another generated sprite sheet. The
 * symptom is a page that gets progressively slower as you work on it, which reads as a performance
 * problem rather than a lifecycle one.
 *
 * So `start()` and `stop()` own the frame handle, cleanup is **symmetric** rather than guarded by a
 * "did we already stop?" ref, and `dispose()` tears down the loop, the listeners and the sprite
 * cache on hot update. Symmetric matters: a guard makes double-stop safe but leaves double-*start*
 * silently leaking, which is the direction strict mode actually exercises.
 */

import {
  type Actor,
  type CharacterSheet,
  type Drawable,
  StrideTracker,
  characterSheet,
  depthSort,
  drawActor,
  drawWaitingBeam,
  placement,
} from './actors'
import { RenderClock } from './clock'
import {
  type FloorData,
  TILE,
  blitProp,
  buildPropAtlas,
  buildStatic,
  chooseZoom,
} from './floor'

export interface RendererOptions {
  canvas: HTMLCanvasElement
  /** The floor, as the kernel recorded it at genesis. */
  floor?: FloorData
  /**
   * The run's seed, which is what picks each person's appearance.
   *
   * Read here rather than sent anywhere: the kernel never learns which of a person's three
   * appearances a run chose, so R9a's "affects no simulation state" holds because there is no
   * channel by which it could, not because nothing writes to one.
   */
  runSeed?: number
  /** Where the actors are this frame. Read from the store, not from React state. */
  actors?: () => Actor[]
  /** Injectable so tests can supply a canvas without a DOM. */
  makeCanvas?: () => HTMLCanvasElement
  /** Called once per frame with the tick to draw. */
  onFrame?: (tick: bigint, stalled: boolean) => void
  /** Injectable for tests; defaults to the real one. */
  requestFrame?: (callback: (time: number) => void) => number
  cancelFrame?: (handle: number) => void
  now?: () => number
}

/** How many sprite sheets have been generated. Exposed so a leak is observable in a test. */
let generatedSheets = 0

export function sheetsGenerated(): number {
  return generatedSheets
}

export function resetSheetCounter(): void {
  generatedSheets = 0
}

export class Renderer {
  readonly clock: RenderClock

  private canvas: HTMLCanvasElement
  private floor?: FloorData
  private runSeed: number
  private actors: () => Actor[]
  private makeCanvas: () => HTMLCanvasElement
  private onFrame?: (tick: bigint, stalled: boolean) => void
  private requestFrame: (callback: (time: number) => void) => number
  private cancelFrame: (handle: number) => void
  private now: () => number

  /** The one frame handle. `null` means not running — the single source of truth. */
  private handle: number | null = null
  private lastFrameAt = 0
  private listeners: Array<() => void> = []
  private sheets: Map<string, CharacterSheet> | null = null
  private readonly stride = new StrideTracker()
  private staticLayer: HTMLCanvasElement | null = null
  private propAtlas: HTMLCanvasElement | null = null
  private zoom = 1
  private frames = 0

  constructor(options: RendererOptions) {
    this.canvas = options.canvas
    this.floor = options.floor
    this.runSeed = options.runSeed ?? 0
    this.actors = options.actors ?? (() => [])
    this.makeCanvas =
      options.makeCanvas ?? (() => document.createElement('canvas'))
    this.onFrame = options.onFrame
    this.requestFrame =
      options.requestFrame ?? ((callback) => requestAnimationFrame(callback))
    this.cancelFrame = options.cancelFrame ?? ((handle) => cancelAnimationFrame(handle))
    this.now = options.now ?? (() => performance.now())
    this.clock = new RenderClock()
  }

  get running(): boolean {
    return this.handle !== null
  }

  get frameCount(): number {
    return this.frames
  }

  /**
   * Begin drawing. Idempotent: calling it twice leaves one loop.
   *
   * Idempotence is the property strict mode needs. Without it the second setup starts a second
   * loop against the same canvas, and the two fight over the same pixels at different phases.
   */
  start(): void {
    if (this.handle !== null) return

    this.ensureSheets()
    this.lastFrameAt = this.now()

    const tick = (): void => {
      // Re-check on every frame: a stop between the schedule and the callback must not draw.
      if (this.handle === null) return

      const at = this.now()
      this.clock.advance(at - this.lastFrameAt)
      this.lastFrameAt = at
      this.frames += 1

      this.draw()
      this.onFrame?.(this.clock.tick, this.clock.stalled)

      this.handle = this.requestFrame(tick)
    }

    this.handle = this.requestFrame(tick)
  }

  /** Stop drawing and release the frame handle. Idempotent, and symmetric with `start`. */
  stop(): void {
    if (this.handle === null) return
    this.cancelFrame(this.handle)
    this.handle = null
  }

  /** Everything `stop` does, plus the listeners and the sprite cache. For hot update. */
  dispose(): void {
    this.stop()
    for (const remove of this.listeners) remove()
    this.listeners = []
    this.sheets = null
    this.staticLayer = null
    this.propAtlas = null
    this.stride.clear()
  }

  /** Register a listener whose removal `dispose` will handle. */
  addListener(target: EventTarget, type: string, handler: EventListener): void {
    target.addEventListener(type, handler)
    this.listeners.push(() => target.removeEventListener(type, handler))
  }

  get listenerCount(): number {
    return this.listeners.length
  }

  get sheetsBuilt(): boolean {
    return this.sheets !== null
  }

  get staticLayerBuilt(): boolean {
    return this.staticLayer !== null
  }

  /** How many character sheets are cached. Exposed so a leak is observable. */
  get cachedSheets(): number {
    return this.sheets?.size ?? 0
  }

  private ensureSheets(): void {
    if (this.sheets !== null) return

    // Built once per renderer instance, and dropped by `dispose`. The prototype builds these at
    // module scope, which is why a hot update leaks one set per save.
    this.sheets = new Map<string, CharacterSheet>()
    this.propAtlas = buildPropAtlas(this.makeCanvas)
    if (this.floor !== undefined) {
      this.staticLayer = buildStatic(this.floor, this.makeCanvas)
    }
    generatedSheets += 1
  }

  /** Resize to the stage, choosing an integer zoom. Nearest-neighbour, never resampled. */
  resize(availableWidth: number, availableHeight: number): void {
    this.zoom = chooseZoom(availableWidth, availableHeight)
    if (this.floor === undefined) return
    this.canvas.width = this.floor.cols * TILE * this.zoom
    this.canvas.height = this.floor.rows * TILE * this.zoom
  }

  get currentZoom(): number {
    return this.zoom
  }

  /**
   * One frame: the baked floor, then everything else in depth order.
   *
   * Props and people sort together. A desk drawn before every person always sits behind the one
   * sitting at it; drawn after, it hides anyone standing beside it. Sorting both by y is what makes
   * a person appear behind their own desk and in front of the row below.
   */
  private draw(): void {
    const context = this.canvas.getContext('2d')
    if (context === null) return

    context.imageSmoothingEnabled = false
    context.clearRect(0, 0, this.canvas.width, this.canvas.height)

    // Everything is composed at 1x and scaled by an integer factor at the end, so the art stays
    // true pixel art at any size.
    context.save()
    context.scale(this.zoom, this.zoom)

    if (this.staticLayer !== null) context.drawImage(this.staticLayer, 0, 0)

    const drawables: Drawable[] = []

    if (this.floor !== undefined && this.propAtlas !== null) {
      const atlas = this.propAtlas
      for (const [x, y, sprite, solid] of this.floor.furniture) {
        drawables.push({
          depth: y * TILE,
          draw: (target) => blitProp(target, atlas, sprite, x, y, solid === 1),
        })
      }
    }

    // No floor means no world to stand anyone in. Genesis lands in the store synchronously
    // from the socket handler, but React only rebuilds this renderer *after* the next paint —
    // so without this guard the frame in between draws actors onto a blank, unsized canvas,
    // and every run opens with the CEO flashing over nothing.
    const cache = this.floor === undefined ? null : this.sheets
    if (cache !== null) {
      const present = this.actors()
      this.stride.retain(present.map((actor) => actor.id))

      for (const actor of present) {
        this.stride.advance(actor)
        const frame = this.stride.frame(actor)
        const sheet = characterSheet(actor.id, this.runSeed, cache, this.makeCanvas)
        drawables.push({
          // The row their feet are on, not the top of their cell. A 64-pixel figure on a
          // 32-pixel tile overhangs the tile above it, and sorting by the top would put a
          // person behind furniture they are standing well in front of.
          depth: placement(actor).feet,
          draw: (target) => {
            drawActor(target, actor, sheet, frame)
            if (actor.waiting === true) drawWaitingBeam(target, actor)
          },
        })
      }
    }

    for (const drawable of depthSort(drawables)) drawable.draw(context)

    context.restore()
  }
}

/**
 * Vite hot-update disposal.
 *
 * Non-component modules get no automatic handling, so without this every save leaves the previous
 * module's loop running.
 */
export function registerHotDispose(renderer: Renderer): void {
  if (import.meta.hot) {
    import.meta.hot.dispose(() => renderer.dispose())
  }
}
