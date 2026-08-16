/**
 * What the client believes the run is.
 *
 * Three rules shape this module, and each one exists because the obvious version is wrong.
 *
 * **The store is not a second fold.** Every value in here is *read off an event*, never
 * recomputed from one. Metrics arrive as a full map on the events that move them; load
 * arrives as a full map on `LOAD_CHANGED`; an item's new status arrives as `item_status` on
 * every event that moves it. That is deliberate — the report service is already a second
 * consumer of the fold, and a third one reimplementing the rules would make the client
 * disagree with the kernel about what happened while passing all of its own tests. If a
 * value is not on the wire, it does not belong in here.
 *
 * **The event stream does not go through React state.** Events land here through
 * `setState`, and the canvas reads `getState()` inside its own frame loop. A high-frequency
 * stream driving component state would re-render the tree at the tick rate, and the canvas
 * does not need React to draw — it needs the numbers. Panels subscribe narrowly, and object
 * or array selectors go through `useShallow`: in zustand's current major a selector
 * returning a fresh reference every call re-renders forever, because the equality check is
 * `Object.is` on the selector's output and a new object is never `Object.is` to the last.
 *
 * **Sequences and ticks are `bigint`.** They are `uint64` on the wire and arrive as strings
 * for exactly that reason. `Number(seq)` works fine for the whole of development and starts
 * silently losing the low bits above 2^53.
 */

import { create } from 'zustand'
import { subscribeWithSelector } from 'zustand/middleware'

import type { MetricDef } from '../design/tokens'
import type { FloorData } from '../render/floor'

// =========================================================================
// What the wire carries
// =========================================================================

/** One authored work item, as the genesis payload's catalog carries it. */
export interface CatalogEntry {
  id: string
  title: string
  brief: string
  dept: string
  want: string
  /** The reporting line whose load this item's work sits in. */
  director: string
  effort_hours: number
  effort_units: number
  friction: string
  requires: { visibility: number | null; items: string[] }
  unlocks: string[]
  visit_meeting: boolean
  final: boolean
  output_title: string
  output_kind: string
  checkpoints: CheckpointDef[]
}

/**
 * One way to settle a checkpoint, and what it costs.
 *
 * `effect` holds metric deltas only. The recurring-draw change is `draw_delta`, split out by
 * the kernel because it is not a metric — `manualHours` is derived from the sum of department
 * draws, so rendering `draw` as a metric movement would show a movement no metric makes. The
 * department it moves is the catalog entry's own `director`.
 *
 * Every figure in here is authored tuning, and every surface that renders one has to say so.
 */
export interface OptionDef {
  label: string
  detail: string
  effect: Record<string, number>
  draw_delta: number
  /** The sentence the deliverable's provenance records — what survives the run. */
  note: string
}

/**
 * A decision point, as authored.
 *
 * No `tacit` field, and that is the mechanic rather than an omission: the line only an
 * in-person resolution surfaces is never shipped at genesis. It reaches the client on the
 * resolution that earned it. The option's arithmetic *is* shipped, at genesis payload version
 * 4 — see `catalog_to_state` for why that withholding was reversed and this one was not.
 */
export interface CheckpointDef {
  at_percent: number
  kind: string
  label: string
  prompt: string
  options: OptionDef[]
}

export interface RosterEntry {
  /** Who they are, so a conversation names a person rather than a row id. */
  name: string
  initials: string
  title: string
  dept: string
  mgr: string
  rank: string
  seat: [number, number]
}

/** Everything the run was created with. Immutable for its lifetime, inherited by forks. */
export interface Genesis {
  runSeed: number
  quantumSimSeconds: number
  grid: [number, number]
  horizonTick: bigint
  decisionSupply: number
  rulesVer: string
  floor: FloorData
  roster: Record<string, RosterEntry>
  catalog: CatalogEntry[]
  metricDefs: MetricDef[]
  /** The load ramp's domain, read from the kernel rather than written down twice. */
  load: { scale: number; ceiling: number }
}

/** One event, in the shape `envelope_frame` sends it. */
export interface EventFrame {
  kind: string
  seq: string
  tick: string
  schema_ver: number
  rules_ver: string
  run_id: string
  command_id: string
  request_id: string
  payload: Record<string, unknown>
}

/** A control frame: not an event, and carries no sequence. */
export interface ControlFrame {
  kind: 'RESYNC' | 'RESYNC_REQUIRED' | 'POSITION_ECHO' | 'ERROR' | 'MODEL_SPEND'
  [key: string]: unknown
}

export type Frame = EventFrame | ControlFrame

export function isEventFrame(frame: Frame): frame is EventFrame {
  return typeof (frame as EventFrame).seq === 'string'
}

// =========================================================================
// What the client shows
// =========================================================================

/** The five display states, which are the kernel's five statuses. */
export type ItemStatus = 'backlog' | 'assigned' | 'active' | 'blocked' | 'done'

export interface ItemView {
  id: string
  status: ItemStatus
  assignee: string
  doneUnits: number
  /** True once the item's gates have cleared. Backlog and unavailable are different. */
  unlocked: boolean
  resolvedCount: number
}

export interface PersonView {
  id: string
  /** Milli-tiles, so integer arithmetic matches the kernel's. */
  xMilli: number
  yMilli: number
  facing: string
  state: string
  itemId: string
  /** True when this person is waiting on the CEO. The one thing the beam may mean. */
  waiting: boolean
}

/**
 * Where the wire says the CEO is.
 *
 * Distinct from where the client is *drawing* them. This is the authoritative side: seeded from
 * the floor's spawn at genesis, replaced wholesale by a resync's snapshot, and corrected about
 * once a sim-hour by the position echo. The predicted position the renderer draws lives in the
 * stage, because a prediction is recomputed rather than read off an event, and the rule this
 * module opens with is that nothing recomputed belongs in here.
 */
export interface CeoView {
  xMilli: number
  yMilli: number
  facing: string
}

/**
 * One thing a person said when asked.
 *
 * Kept per person rather than as one stream, because R18 is that returning to somebody shows
 * what *they* have already told you — the value of the mechanic is that knowledge is attached
 * to a person you have to walk to, not to a transcript.
 */
export interface AnsweredQuestion {
  /** The intent the kernel matched, or an empty string when it matched none. */
  question: string
  label: string
  /** What the CEO actually typed. */
  asked: string
  answer: string
  /** True for the three questions where undocumented knowledge lives. */
  tacit: boolean
  /** True when this was the first time this person answered this question. */
  firstTime: boolean
  tick: bigint
}

/** The kernel's own derived CEO position, as the last echo reported it. */
export interface PositionEcho {
  xMilli: number
  yMilli: number
  tick: bigint
}

/** One entry in the decision tray: an item stopped, waiting on the CEO. */
export interface TrayEntry {
  itemId: string
  personId: string
  cpIndex: number
  label: string
  kind: string
  atTick: bigint
  atSeq: bigint
}

export interface DeliverableView {
  itemId: string
  title: string
  kind: string
  dept: string
  day: number
  provenance: string[]
  tacit: string[]
}

/** One point on a metric's trajectory. Ticks kept so the sparkline is over sim-time. */
export interface TrajectoryPoint {
  tick: bigint
  value: number
}

/** How many points a trajectory keeps. Bounded: a long run must not grow the client. */
export const TRAJECTORY_CAPACITY = 240

/**
 * What the run has spent on model calls, against what it may (M28).
 *
 * The one slice in this store that is *not* read off an event, and it cannot be. What a call
 * cost depends on which provider answered and what it counted, so an event carrying it would be
 * an output the kernel's fold could not reproduce and strict replay would fail on every run that
 * used the bench. So it arrives as a **control frame** — the same channel `POSITION_ECHO` uses
 * for derived state that is not part of the log — and the store still computes nothing, which is
 * the rule that matters.
 *
 * `maxCalls` and `maxTokens` are `null` only when an operator explicitly removed the ceiling.
 * That is a display state ("no ceiling"), never the result of an absent setting: the backend's
 * shipped default is finite and an unreadable setting keeps it.
 *
 * `lineage*` is the aggregate across every timeline forked from the same root — one number for
 * what the session cost. The *ceiling* is deliberately not aggregated: it is enforced per run,
 * because a lineage-wide budget would leave a child at its parent's exhaustion point and the
 * diff would then present budget as consequence.
 */
export interface SpendView {
  calls: number
  tokens: number
  /** Turns U12's cache answered. Not calls, and why the count can sit still while a run talks. */
  cacheHits: number
  maxCalls: number | null
  maxTokens: number | null
  lineageCalls: number
  lineageTokens: number
  /** False with no provider configured. A supported mode (M20), not a fault. */
  benchPresent: boolean
  /** A ceiling has stopped the calls. The run has not stopped; nothing here ever stops it. */
  quiet: boolean
}

/**
 * Zero, with the bench absent and the shipped ceiling unknown.
 *
 * The state before any frame arrives, and also the state of a keyless run for its whole life —
 * which is why it renders rather than hiding the tile. `null` bounds here mean "not yet told",
 * and the tile says so; it does not guess at the default, because a guessed ceiling is a figure
 * presented as measured.
 */
export function emptySpend(): SpendView {
  return {
    calls: 0,
    tokens: 0,
    cacheHits: 0,
    maxCalls: null,
    maxTokens: null,
    lineageCalls: 0,
    lineageTokens: 0,
    benchPresent: false,
    quiet: false,
  }
}

// =========================================================================
// Branch comparisons
// =========================================================================
//
// These live here rather than in `ui/comparison-model.ts` because they are wire shapes, and the
// store owns wire shapes — `CatalogEntry`, `OptionDef`, `PersonView` and the rest are all
// defined here and imported *by* the ui models. Defining them in the ui layer and importing
// them down into `net/` inverted that, and put a `net -> ui` edge in a codebase that otherwise
// only has `ui -> net`.

/**
 * The key a comparison is held under: one per checkpoint *per route*.
 *
 * The route belongs in the key, and leaving it out was a real bug. Both affordances are mounted
 * at once whenever the CEO is standing next to the person who is blocked — the tray card and the
 * conversation's decision card — so a comparison run from the tray would satisfy the
 * conversation's lookup and render tray-priced branches under the in-person panel. The kernel
 * prices the two routes differently on purpose (in person pays morale and visibility, the tray
 * costs morale), which is exactly the premium the panel exists to show, so the figures would
 * have been wrong by the amount the feature is about.
 */
export function comparisonKey(itemId: string, cpIndex: number, inPerson: boolean): string {
  return `${itemId}:${cpIndex}:${inPerson ? 'here' : 'tray'}`
}


/** One sample on a projected trajectory, and the tick it was measured at. */
export interface ProjectedPoint {
  tick: bigint
  value: number
}

/**
 * One number in a branch summary, and the tick it was measured at (R22).
 *
 * `value` is `null` where the kernel could not know it — a runway before the branch has paid a
 * day of costs. That is a different thing from zero, and rendering it as zero would say the
 * company is insolvent at the moment the answer is merely unknown.
 */
export interface ProjectedFigure {
  value: number | null
  atTick: bigint
}

/**
 * Why a branch stopped. The kernel's own vocabulary, not a second one.
 *
 * `bound` is distinct from `horizon` on purpose: "ran to the end of the run" and "ran as far as
 * a comparison goes, and the run continues past here" are different facts, and rendering the
 * second as the first would overstate what the projection covers.
 */
export type StopReason = 'checkpoint' | 'horizon' | 'insolvent' | 'bound' | ''

/** The checkpoint a branch stopped at, reached and unsettled. */
export interface ReachedCheckpoint {
  itemId: string
  cpIndex: number
  label: string
  kind: string
  personId: string
  tick: bigint
}

/** One option, followed to the next decision. */
export interface Branch {
  optionIndex: number
  optionLabel: string
  optionNote: string
  forkTick: bigint
  stopTick: bigint
  stopReason: StopReason
  stopDetail: string
  reached: ReachedCheckpoint | null
  trajectories: Record<string, ProjectedPoint[]>
  metrics: Record<string, ProjectedFigure>
  runway: ProjectedFigure
  dailyCost: ProjectedFigure
  unlocked: string[]
  foreclosed: string[]
  /** The tick the gate lists were read at — the branch's own stop, which differs per branch. */
  gatesAtTick: bigint
}

/** One comparison: every option at one checkpoint, as the kernel reported it. */
export interface Comparison {
  itemId: string
  cpIndex: number
  personId: string
  forkTick: bigint
  inPerson: boolean
  branches: Branch[]
  /** The sequence the record arrived at. Newer wins, so a re-read cannot go backwards. */
  atSeq: bigint
}

export type ConnectionStatus = 'idle' | 'connecting' | 'live' | 'resyncing' | 'lost'

export interface RunStore {
  // --- connection -------------------------------------------------------
  connection: ConnectionStatus
  runId: string | null
  /** The highest sequence applied. What a resume asks to continue after. */
  appliedSeq: bigint
  /** Set when a frame arrives out of order, which means the stream lost an event. */
  sequenceGap: boolean
  lastError: string | null

  // --- the run ----------------------------------------------------------
  genesis: Genesis | null
  tick: bigint
  rate: number
  metrics: Record<string, number>
  /** The last movement per metric, as the kernel reported it after clamping. */
  deltas: Record<string, number>
  trajectories: Record<string, TrajectoryPoint[]>
  items: Record<string, ItemView>
  people: Record<string, PersonView>
  /** Per reporting line, in per-mille of available capacity. */
  load: Record<string, number>
  /** The kernel's own total for the last day's costs. Runway divides cash by this. */
  dailyCost: number
  /** What the run has spent on model calls (M28). Measured, not authored. */
  spend: SpendView
  tray: TrayEntry[]
  deliverables: DeliverableView[]
  terminal: { reason: string; tick: bigint } | null
  /**
   * The line said only in person, per raised checkpoint, keyed `item:cpIndex`.
   *
   * R8 kept structurally rather than by convention. The obvious home for this is a field on
   * `TrayEntry`, and that is precisely what must not happen: the tray renders from those
   * entries, so a tacit line living on one is a line the tray *can* show, and the guarantee
   * would then rest on nobody ever adding it to the card. Held apart, the tray has nothing to
   * leak — the only reader is the conversation.
   */
  tacitLines: Record<string, string>
  /**
   * Branch comparisons, keyed `item:cpIndex`.
   *
   * Held apart from `trajectories` on purpose, and the distinction is the whole point of the
   * mechanic. `trajectories` is the parent run's *actual* history, written only from what the
   * store read off an event; a branch's points are a *projection* carrying a measured tick and
   * an authored basis. Folding one into the other would make the HUD's sparklines draw a
   * future nobody has lived, and there would be no way to tell afterwards which points were
   * which.
   *
   * A second comparison at one checkpoint replaces the first. That matches how the decision
   * card is keyed today, and it is the safe direction: the kernel computes every branch before
   * appending, so a record that arrives is complete, and the newer one describes a later fork.
   */
  comparisons: Record<string, Comparison>
  /**
   * What each person has said, newest first, keyed by person id.
   *
   * Folded out of the answer event rather than held in component state, which is what makes it
   * survive closing the conversation, walking away, and a reload — the reload replays the
   * events and rebuilds this from them (R18, R19).
   */
  answers: Record<string, AnsweredQuestion[]>
  /** Where the wire last said the CEO is. What the stage's prediction seeds from. */
  ceo: CeoView
  /** The last position echo, or `null` before one has arrived. Reconciled against, not drawn. */
  ceoEcho: PositionEcho | null
  /** R33: the kernel's derived CEO position disagreed with the client's prediction. */
  diverged: boolean

  // --- actions ----------------------------------------------------------
  apply(frame: Frame): void
  applyAll(frames: Frame[]): void
  setConnection(status: ConnectionStatus, error?: string | null): void
  markDiverged(diverged: boolean): void
  reset(): void
}

function emptyRun(): Omit<
  RunStore,
  'apply' | 'applyAll' | 'setConnection' | 'markDiverged' | 'reset'
> {
  return {
    connection: 'idle',
    runId: null,
    appliedSeq: 0n,
    sequenceGap: false,
    lastError: null,
    genesis: null,
    tick: 0n,
    rate: 1,
    metrics: {},
    deltas: {},
    trajectories: {},
    items: {},
    people: {},
    load: {},
    dailyCost: 0,
    spend: emptySpend(),
    tray: [],
    deliverables: [],
    terminal: null,
    tacitLines: {},
    comparisons: {},
    answers: {},
    ceo: { xMilli: 0, yMilli: 0, facing: 'down' },
    ceoEcho: null,
    diverged: false,
  }
}

// =========================================================================
// Reading the wire safely
// =========================================================================

/**
 * Parse a `uint64` that may arrive as a string or a number.
 *
 * Event envelopes send both sequence and tick as strings, precisely so the parse cannot lose
 * precision. Control frames are derived state produced outside that contract and send plain
 * numbers, so both forms have to be accepted — and the string form is the one that is safe.
 */
function toBig(value: unknown): bigint {
  if (typeof value === 'bigint') return value
  if (typeof value === 'number') return BigInt(Math.trunc(value))
  if (typeof value !== 'string' || value === '') return 0n
  try {
    return BigInt(value)
  } catch {
    // `BigInt("12.5")` and `BigInt("nonsense")` throw. Total like `toInt` and `toStr`, because
    // this runs inside the WebSocket message handler: a throw here would escape `apply` and take
    // the stream down over one malformed frame.
    return 0n
  }
}

function toInt(value: unknown, fallback = 0): number {
  return typeof value === 'number' && Number.isFinite(value) ? Math.trunc(value) : fallback
}

function toStr(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : fallback
}

const STATUSES: readonly ItemStatus[] = ['backlog', 'assigned', 'active', 'blocked', 'done']

function toStatus(value: unknown): ItemStatus | null {
  return STATUSES.includes(value as ItemStatus) ? (value as ItemStatus) : null
}

/** Append to a bounded trajectory. The oldest point falls off the front. */
function pushPoint(
  series: TrajectoryPoint[] | undefined,
  tick: bigint,
  value: number,
): TrajectoryPoint[] {
  const next = series === undefined ? [] : series.slice()
  next.push({ tick, value })
  return next.length > TRAJECTORY_CAPACITY ? next.slice(next.length - TRAJECTORY_CAPACITY) : next
}

// =========================================================================
// The store
// =========================================================================

export const useRunStore = create<RunStore>()(
  subscribeWithSelector((set, get) => ({
    ...emptyRun(),

    /**
     * Apply one frame.
     *
     * Ordered on purpose: control frames first, because a resync replaces state wholesale
     * and must not be interleaved with an event that predates it; then the sequence guard;
     * then the projection.
     */
    apply(frame: Frame): void {
      if (!isEventFrame(frame)) {
        applyControl(set, get, frame)
        return
      }

      const seq = toBig(frame.seq)
      const applied = get().appliedSeq

      // Duplicate or out-of-order. A resume can legitimately re-send events the client
      // already has — it asks for "after N" and the window is inclusive at the edges — so a
      // replay of an applied sequence is dropped silently. A *gap* is different: it means
      // the stream lost an event, and every projection after it is suspect.
      if (seq <= applied) return
      if (applied > 0n && seq > applied + 1n) {
        set({ sequenceGap: true })
      }

      applyEvent(set, get, frame, seq)
    },

    applyAll(frames: Frame[]): void {
      for (const frame of frames) get().apply(frame)
    },

    setConnection(status: ConnectionStatus, error: string | null = null): void {
      set({ connection: status, lastError: error })
    },

    markDiverged(diverged: boolean): void {
      set({ diverged })
    },

    reset(): void {
      set(emptyRun())
    },
  })),
)

type Setter = (partial: Partial<RunStore>) => void
type Getter = () => RunStore

function applyControl(set: Setter, get: Getter, frame: ControlFrame): void {
  if (frame.kind === 'POSITION_ECHO') {
    // Recorded, not applied. The comparison belongs to whoever owns the prediction — the
    // stage — and writing the echo straight into `ceo` would make the drawn position jump
    // backwards by up to a sim-hour of walking every time one arrived.
    const tick = toBig(frame.tick)
    const patch: Partial<RunStore> = {
      ceoEcho: { xMilli: toInt(frame.x_milli), yMilli: toInt(frame.y_milli), tick },
    }

    // The echo is also the only *regular* statement of what tick the kernel is on. Events are
    // appended only when a tick produces one, and a measured run emits on about five ticks in
    // twelve hundred — so without this the client's clock has nothing to chase for hundreds of
    // ticks at a stretch, and every command it tags "a few ticks ahead" lands in the past.
    //
    // Only ever forwards: the echo is published from inside the batch while the batch's own
    // events are published after it, so an echo can arrive describing a tick the client has
    // already passed, and rewinding on one would make time stutter.
    if (tick > get().tick) patch.tick = tick

    set(patch)
    return
  }

  if (frame.kind === 'RESYNC') {
    // The client was too far behind to catch up by replay. A hard reset, not a merge:
    // folding a snapshot into partially-applied state would produce a world that no
    // sequence of events could have produced.
    const current = get()

    if (current.genesis === null) {
      // A resync is the *only* frame a client gets when it reconnects far enough behind, and it
      // carries no genesis. Without one there is no catalog, no roster and no floor, so the
      // console would render empty while reporting itself live — which reads as a broken client
      // rather than as a client that cannot show this run. Say so instead.
      set({
        connection: 'lost',
        lastError:
          'the run resynced before its genesis arrived, so there is no catalog or floor to ' +
          'render. Reload to replay the run from its first event.',
      })
      return
    }

    const state = readSnapshot(current, frame.state as Record<string, unknown> | undefined)
    set({
      ...state,
      appliedSeq: toBig(frame.head_seq),
      sequenceGap: false,
      connection: 'live',
      lastError: null,
    })
    return
  }

  if (frame.kind === 'MODEL_SPEND') {
    // Read, never accumulated. The backend's counter is the authority — it is what the
    // ceiling is checked against, and it survives a restart — so a client that added up
    // deltas would drift the moment one frame was dropped and would then disagree with the
    // number that actually refuses a call.
    //
    // The publisher is the stream that already sends `POSITION_ECHO`, and the reading is
    // `GET /runs/{id}/spend` on the agents service, or one query against `model_spend`
    // joined to `runs.lineage_root_id`. Until that publish exists the tile renders the
    // zero-and-absent state, which is the same state a keyless run shows for its whole
    // life — so the surface is never wrong, only quiet.
    set({ spend: readSpend(frame) })
    return
  }

  if (frame.kind === 'RESYNC_REQUIRED' || frame.kind === 'ERROR') {
    set({ connection: 'lost', lastError: toStr(frame.detail, frame.kind) })
  }
}

/**
 * The spend frame, read defensively.
 *
 * A missing ceiling is `null` — "not told" — rather than the shipped default. Substituting
 * the default here would put an invented number in the one tile whose whole claim is that
 * its figures are measured.
 */
function readSpend(frame: ControlFrame): SpendView {
  return {
    calls: toInt(frame.calls),
    tokens: toInt(frame.tokens),
    cacheHits: toInt(frame.cache_hits),
    maxCalls: toBound(frame.max_calls),
    maxTokens: toBound(frame.max_tokens),
    lineageCalls: toInt(frame.lineage_calls),
    lineageTokens: toInt(frame.lineage_tokens),
    benchPresent: frame.bench_present === true,
    quiet: frame.quiet === true,
  }
}

/** A ceiling, or `null` for an unlimited one — and for anything unreadable. */
function toBound(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? Math.trunc(value) : null
}

function applyEvent(set: Setter, get: Getter, frame: EventFrame, seq: bigint): void {
  const state = get()
  const payload = frame.payload
  const tick = toBig(payload.tick ?? frame.tick)
  const patch: Partial<RunStore> = { appliedSeq: seq }

  // Monotonic, for the same reason the echo branch is: the echo is published from inside the
  // kernel's batch while that batch's events are published after it, so an event can arrive
  // carrying a tick the echo has already passed. `appliedSeq` carries the ordering guarantee
  // for everything else; the clock must not go backwards under it.
  if (tick > state.tick) patch.tick = tick

  if (frame.kind === 'GENESIS') {
    Object.assign(patch, readGenesis(payload), { runId: frame.run_id, tick })
  }

  // Metrics arrive as a full map on every event that moves them, so the client reads the
  // kernel's answer rather than accumulating deltas — which would drift the moment an event
  // was dropped, and would disagree with the kernel about a clamped movement.
  const metrics = payload.metrics
  if (isNumberMap(metrics)) {
    patch.metrics = { ...metrics }
    patch.trajectories = extendTrajectories(state.trajectories, metrics, tick)
  }

  const deltas = payload.deltas
  if (isNumberMap(deltas) && Object.keys(deltas).length > 0) {
    patch.deltas = { ...deltas }
  }

  // Per reporting line, in per-mille. Genesis is excluded deliberately: its `load` is the ramp's
  // *domain* (`{scale, ceiling}`), which is also a map of numbers, so a generic check would project
  // two departments named "scale" and "ceiling" into the capacity tile and leave every real
  // department reading zero. `readGenesis` takes that key into `genesis.load` instead.
  const load = payload.load
  if (frame.kind !== 'GENESIS' && isNumberMap(load)) {
    patch.load = { ...load }
  }

  if (frame.kind === 'DAILY_COSTS_APPLIED') {
    patch.dailyCost = toInt(payload.total_cost)
  }

  if (frame.kind === 'RATE_CHANGED') {
    patch.rate = toInt(payload.rate, state.rate)
  }

  if (frame.kind === 'RUN_TERMINATED') {
    patch.terminal = { reason: toStr(payload.reason, 'ended'), tick }
  }

  // --- items ------------------------------------------------------------
  const itemId = toStr(payload.item)
  const status = toStatus(payload.item_status)
  if (itemId !== '' && status !== null) {
    const holder = statusAssignee(frame, payload, status)
    patch.items = withItem(patch.items ?? state.items, itemId, {
      status,
      // Omitted when the event names no holder, so `withItem` keeps the one already there.
      ...(holder === null ? {} : { assignee: holder }),
      doneUnits: toInt(payload.done_units, state.items[itemId]?.doneUnits ?? 0),
      unlocked: true,
    })
    // An item that is no longer blocked is no longer waiting on anybody, so its tray entries go
    // with it. One rule covering reassignment, attrition and a return to the backlog — each of
    // which can move an item out of `blocked` without a DECISION_RESOLVED to clear the tray.
    if (status !== 'blocked') {
      patch.tray = (patch.tray ?? state.tray).filter((entry) => entry.itemId !== itemId)
      // And the comparison goes with the tray entry, from the same branch and by the same
      // rule (R25). A displayed result whose checkpoint has been settled, reassigned, or lost
      // its owner is a projection from a run that no longer exists. Dropped here — derived
      // from the wire's own authority — rather than by a client-side timer, which would be a
      // second opinion about a question the wire already answers.
      patch.comparisons = withoutItem(patch.comparisons ?? state.comparisons, itemId)
    }
  }

  if (frame.kind === 'ITEM_UNLOCKED' && itemId !== '') {
    patch.items = withItem(patch.items ?? state.items, itemId, { unlocked: true })
  }

  if (frame.kind === 'ATTRITION') {
    const returned = toStr(payload.returned_item)
    // Named for what it describes: this event carries `returned_item`, not `item`, so the status
    // field is named to match rather than reusing the six-kind `item_status` convention.
    const returnedStatus = toStatus(payload.returned_item_status)
    if (returned !== '' && returnedStatus !== null) {
      patch.items = withItem(patch.items ?? state.items, returned, {
        status: returnedStatus,
        assignee: '',
      })
      patch.tray = (patch.tray ?? state.tray).filter((entry) => entry.itemId !== returned)
      patch.comparisons = withoutItem(patch.comparisons ?? state.comparisons, returned)
    }
  }

  // --- comparisons ------------------------------------------------------
  if (frame.kind === 'OPTIONS_COMPARED') {
    const comparison = readComparison(payload, seq)
    if (comparison !== null) {
      patch.comparisons = {
        ...(patch.comparisons ?? state.comparisons),
        [comparisonKey(comparison.itemId, comparison.cpIndex, comparison.inPerson)]:
          comparison,
      }
    }
  }

  // --- the tray ---------------------------------------------------------
  if (frame.kind === 'CHECKPOINT_RAISED') {
    const cpIndex = toInt(payload.cp_index, -1)
    // De-duplicated on (item, checkpoint) rather than appended blindly: a resume replays events
    // the client may already hold, and a second entry for one checkpoint would both over-count
    // decision pressure and collide on its React key.
    const existing = (patch.tray ?? state.tray).filter(
      (entry) => !(entry.itemId === itemId && entry.cpIndex === cpIndex),
    )
    patch.tray = [
      ...existing,
      {
        itemId,
        personId: toStr(payload.person),
        cpIndex,
        label: toStr(payload.label),
        kind: toStr(payload.kind),
        atTick: tick,
        atSeq: seq,
      },
    ]

    // Deliberately not on the entry above. See `tacitLines` on the store.
    const tacit = toStr(payload.tacit)
    if (tacit !== '') {
      patch.tacitLines = { ...state.tacitLines, [`${itemId}:${cpIndex}`]: tacit }
    }
  }

  if (frame.kind === 'DECISION_RESOLVED') {
    // The tray entry is already gone: this event reports the item back at `active`, and the item
    // branch above drops the entries of anything that is no longer blocked. Only the resolved
    // count is this branch's to advance.
    const existing = (patch.items ?? state.items)[itemId]
    if (existing !== undefined) {
      patch.items = withItem(patch.items ?? state.items, itemId, {
        resolvedCount: existing.resolvedCount + 1,
      })
    }
  }

  // --- what people said -------------------------------------------------
  if (frame.kind === 'QUESTION_ANSWERED') {
    const personId = toStr(payload.person)
    if (personId !== '') {
      const said: AnsweredQuestion = {
        question: toStr(payload.question),
        label: toStr(payload.label),
        asked: toStr(payload.asked),
        answer: toStr(payload.answer),
        tacit: payload.tacit === true,
        firstTime: payload.first_time === true,
        tick,
      }
      // Newest first, as the prototype's panel shows it: the answer you just heard is the one
      // you are reading, and a long conversation should not push it off the bottom.
      patch.answers = {
        ...state.answers,
        [personId]: [said, ...(state.answers[personId] ?? [])],
      }
    }
  }

  // --- deliverables -----------------------------------------------------
  if (frame.kind === 'DELIVERABLE_PRODUCED') {
    const record = payload.deliverable
    if (isRecord(record)) {
      patch.deliverables = [...state.deliverables, readDeliverable(record)]
    }
  }

  set(patch)
}

/**
 * Who holds the item after this event, or `null` when this event does not say.
 *
 * The distinction matters. Backlog means nobody, and stating that rather than leaving the previous
 * assignee in place is what keeps a returned item from rendering as still owned. But
 * `DECISION_RESOLVED` names no person at all — the work goes back to whoever already held it — and
 * treating "not named" as "nobody" would blank the assignee on every decision, leaving an item
 * rendering as in-progress with no one on it.
 */
function statusAssignee(
  frame: EventFrame,
  payload: Record<string, unknown>,
  status: ItemStatus,
): string | null {
  if (status === 'backlog') return ''
  if (frame.kind === 'WORK_REASSIGNED') return toStr(payload.to)
  const person = toStr(payload.person)
  return person === '' ? null : person
}

function withItem(
  items: Record<string, ItemView>,
  id: string,
  patch: Partial<ItemView>,
): Record<string, ItemView> {
  const existing: ItemView = items[id] ?? {
    id,
    status: 'backlog',
    assignee: '',
    doneUnits: 0,
    unlocked: false,
    resolvedCount: 0,
  }
  return { ...items, [id]: { ...existing, ...patch, id } }
}

/**
 * Drop every comparison belonging to an item, whichever of its checkpoints it was at.
 *
 * Returns the *same reference* when there is nothing to drop, which is the common case — items
 * leave `blocked` on every assignment, reassignment, delivery and attrition, and a comparison is
 * rare and short-lived. Rebuilding the map regardless would hand the slice a new identity on
 * effectively every one of those events, waking any subscriber that reads the whole slice for a
 * change that did not happen.
 */
function withoutItem(
  comparisons: Record<string, Comparison>,
  itemId: string,
): Record<string, Comparison> {
  if (!Object.values(comparisons).some((comparison) => comparison.itemId === itemId)) {
    return comparisons
  }

  const next: Record<string, Comparison> = {}
  for (const [key, comparison] of Object.entries(comparisons)) {
    if (comparison.itemId !== itemId) next[key] = comparison
  }
  return next
}

/**
 * Read a comparison record off the wire.
 *
 * Total, like every other reader here: this runs inside the WebSocket message handler, and a
 * throw would escape `apply` and take the stream down over one malformed frame. A record that
 * names no item is dropped rather than stored under an empty key.
 */
function readComparison(
  payload: Record<string, unknown>,
  seq: bigint,
): Comparison | null {
  const itemId = toStr(payload.item)
  if (itemId === '') return null

  const branches = Array.isArray(payload.branches) ? payload.branches : []

  return {
    itemId,
    cpIndex: toInt(payload.cp_index, -1),
    personId: toStr(payload.person),
    forkTick: toBig(payload.tick),
    inPerson: payload.in_person === true,
    branches: branches.filter(isRecord).map(readBranch),
    atSeq: seq,
  }
}

function readBranch(record: Record<string, unknown>): Branch {
  const reached = record.stopped_at

  return {
    optionIndex: toInt(record.option_index, -1),
    optionLabel: toStr(record.option_label),
    optionNote: toStr(record.option_note),
    forkTick: toBig(record.fork_tick),
    stopTick: toBig(record.stop_tick),
    stopReason: toStr(record.stop_reason) as StopReason,
    stopDetail: toStr(record.stop_detail),
    reached: isRecord(reached)
      ? {
          itemId: toStr(reached.item),
          cpIndex: toInt(reached.cp_index, -1),
          label: toStr(reached.label),
          kind: toStr(reached.kind),
          personId: toStr(reached.person),
          tick: toBig(reached.tick),
        }
      : null,
    trajectories: readProjectedSeries(record.trajectories),
    metrics: readFigures(record.metrics),
    runway: readFigure(record.runway),
    dailyCost: readFigure(record.daily_cost),
    unlocked: readStrings(record.unlocked),
    foreclosed: readStrings(record.foreclosed),
    gatesAtTick: toBig(record.gates_at_tick),
  }
}

function readProjectedSeries(value: unknown): Record<string, ProjectedPoint[]> {
  if (!isRecord(value)) return {}
  const series: Record<string, ProjectedPoint[]> = {}
  for (const [key, points] of Object.entries(value)) {
    if (!Array.isArray(points)) continue
    series[key] = points
      .filter(isRecord)
      .map((point) => ({ tick: toBig(point.tick), value: toInt(point.value) }))
  }
  return series
}

function readFigures(value: unknown): Record<string, ProjectedFigure> {
  if (!isRecord(value)) return {}
  const figures: Record<string, ProjectedFigure> = {}
  for (const [key, figure] of Object.entries(value)) {
    figures[key] = readFigure(figure)
  }
  return figures
}

/**
 * One figure, keeping `null` distinct from zero.
 *
 * A runway the kernel could not know — no day of costs paid yet — arrives as `null`, and
 * coercing it to zero here would render "insolvent" where the honest answer is "not yet
 * knowable". The two are the same pixel width and opposite in meaning.
 */
function readFigure(value: unknown): ProjectedFigure {
  if (!isRecord(value)) return { value: null, atTick: 0n }
  return {
    value: typeof value.value === 'number' ? Math.trunc(value.value) : null,
    atTick: toBig(value.at_tick),
  }
}

function readStrings(value: unknown): string[] {
  return Array.isArray(value) ? value.map((entry) => String(entry)) : []
}

function extendTrajectories(
  current: Record<string, TrajectoryPoint[]>,
  metrics: Record<string, number>,
  tick: bigint,
): Record<string, TrajectoryPoint[]> {
  const next: Record<string, TrajectoryPoint[]> = { ...current }
  for (const [key, value] of Object.entries(metrics)) {
    next[key] = pushPoint(current[key], tick, value)
  }
  return next
}

function readGenesis(payload: Record<string, unknown>): Partial<RunStore> {
  const grid = Array.isArray(payload.grid) ? payload.grid : [0, 0]
  const loadDomain = isRecord(payload.load) ? payload.load : {}

  const genesis: Genesis = {
    runSeed: toInt(payload.run_seed),
    quantumSimSeconds: toInt(payload.quantum_sim_seconds, 60),
    grid: [toInt(grid[0]), toInt(grid[1])],
    horizonTick: toBig(payload.horizon_tick),
    decisionSupply: toInt(payload.decision_supply),
    rulesVer: toStr(payload.rules_ver),
    floor: payload.floor as FloorData,
    roster: readRoster(payload.roster),
    catalog: Array.isArray(payload.catalog) ? (payload.catalog as CatalogEntry[]) : [],
    metricDefs: Array.isArray(payload.metric_defs) ? (payload.metric_defs as MetricDef[]) : [],
    load: { scale: toInt(loadDomain.scale, 1000), ceiling: toInt(loadDomain.ceiling, 1000) },
  }

  // Every catalog entry gets a node from the first event, backlog and locked. The DAG lays
  // out the whole graph up front precisely so an unlock reshuffles nothing.
  //
  // Availability is seeded against the metrics this *same payload* carries rather than against a
  // constant, because a visibility gate the run already starts above would otherwise read as
  // locked forever — the kernel emits no ITEM_UNLOCKED for a gate that was never closed. Nothing
  // gates below the shipped starting visibility today; this keeps that from being load-bearing.
  const initialMetrics = isNumberMap(payload.metrics) ? payload.metrics : {}
  const startingVisibility = initialMetrics.visibility ?? 0

  const items: Record<string, ItemView> = {}
  for (const entry of genesis.catalog) {
    const gate = entry.requires.visibility
    items[entry.id] = {
      id: entry.id,
      status: 'backlog',
      assignee: '',
      doneUnits: 0,
      unlocked:
        entry.requires.items.length === 0 && (gate === null || startingVisibility >= gate),
      resolvedCount: 0,
    }
  }

  const people: Record<string, PersonView> = {}
  for (const [id, entry] of Object.entries(genesis.roster)) {
    people[id] = {
      id,
      xMilli: entry.seat[0] * 1000,
      yMilli: entry.seat[1] * 1000,
      facing: 'up',
      state: 'idle',
      itemId: '',
      waiting: false,
    }
  }

  // The CEO spawns where the kernel spawned them (`CeoRuntime(x_milli=floor.spawn[0] * MILLI,
  // ...)`), read off the floor the genesis payload carries rather than written down a second
  // time here. Facing matches `CeoRuntime`'s default, for the same reason.
  const ceo: CeoView = {
    xMilli: genesis.floor.spawn[0] * 1000,
    yMilli: genesis.floor.spawn[1] * 1000,
    facing: 'down',
  }

  return { genesis, items, people, ceo }
}

/**
 * The roster, with the fields that arrived late defaulted rather than cast over.
 *
 * `name`, `initials` and `title` joined the genesis payload at schema version 3. A blanket cast
 * would type an older payload as if it carried them and render `undefined` at the reader — and
 * a run exported before the change is still readable through the report path, where the
 * rules-version gate that rejects a live resync does not apply. Falling back to the id is worse
 * than a name and much better than the word "undefined" where a person should be.
 */
function readRoster(value: unknown): Record<string, RosterEntry> {
  if (!isRecord(value)) return {}

  const roster: Record<string, RosterEntry> = {}
  for (const [id, entry] of Object.entries(value)) {
    if (!isRecord(entry)) continue
    const seat = Array.isArray(entry.seat) ? entry.seat : [0, 0]
    roster[id] = {
      name: toStr(entry.name, id),
      initials: toStr(entry.initials, id.slice(0, 2).toUpperCase()),
      title: toStr(entry.title),
      dept: toStr(entry.dept),
      mgr: toStr(entry.mgr),
      rank: toStr(entry.rank, 'staff'),
      seat: [toInt(seat[0]), toInt(seat[1])],
    }
  }
  return roster
}

function readDeliverable(record: Record<string, unknown>): DeliverableView {
  return {
    itemId: toStr(record.item),
    title: toStr(record.title),
    kind: toStr(record.kind),
    dept: toStr(record.dept),
    day: toInt(record.day),
    provenance: Array.isArray(record.provenance) ? record.provenance.map((line) => String(line)) : [],
    tacit: Array.isArray(record.tacit) ? record.tacit.map((line) => String(line)) : [],
  }
}

/**
 * Replace state from a snapshot.
 *
 * The snapshot is `simcore.snapshot()` — the same projection the state hash is taken over —
 * so this reads the kernel's own subsystems rather than a shape invented for the client.
 * Genesis is kept: a resync does not change what the run was created with.
 */
function readSnapshot(
  current: RunStore,
  snapshot: Record<string, unknown> | undefined,
): Partial<RunStore> {
  if (snapshot === undefined) return { connection: 'live' }

  const patch: Partial<RunStore> = {}

  const metrics = snapshot.metrics
  if (isNumberMap(metrics)) patch.metrics = { ...metrics }

  const lifecycle = snapshot.lifecycle
  if (isRecord(lifecycle)) {
    patch.tick = toBig(lifecycle.tick)
    const reason = toStr(lifecycle.terminal_reason)
    patch.terminal =
      reason === '' ? null : { reason, tick: toBig(lifecycle.terminal_tick) }
    const outputs = lifecycle.outputs
    if (Array.isArray(outputs)) {
      patch.deliverables = outputs.filter(isRecord).map(readDeliverable)
    }
  }

  const capacity = snapshot.capacity
  if (isRecord(capacity)) {
    const load: Record<string, number> = {}
    for (const [director, department] of Object.entries(capacity)) {
      if (isRecord(department)) load[director] = toInt(department.load_permille)
    }
    patch.load = load
  }

  const items = snapshot.items
  if (isRecord(items)) {
    const next: Record<string, ItemView> = {}
    for (const [id, item] of Object.entries(items)) {
      if (!isRecord(item)) continue
      const resolved = Array.isArray(item.resolved) ? item.resolved : []
      next[id] = {
        id,
        status: toStatus(item.status) ?? 'backlog',
        assignee: toStr(item.assignee),
        doneUnits: toInt(item.done_units),
        // Availability is not in the snapshot's item shape; a locked item is one whose
        // authored gates the catalog still names and which has not moved.
        unlocked: current.items[id]?.unlocked ?? false,
        resolvedCount: resolved.filter(Boolean).length,
      }
    }
    patch.items = next
  }

  const people = snapshot.people
  if (isRecord(people)) {
    const next: Record<string, PersonView> = {}
    for (const [id, person] of Object.entries(people)) {
      if (!isRecord(person)) continue
      const pos = Array.isArray(person.pos) ? person.pos : [0, 0]
      next[id] = {
        id,
        xMilli: toInt(pos[0]) * 1000,
        yMilli: toInt(pos[1]) * 1000,
        facing: toStr(person.facing, 'up'),
        state: toStr(person.state, 'idle'),
        itemId: toStr(person.item),
        waiting: toStr(person.state) === 'blocked',
      }
    }
    patch.people = next
  }

  // The snapshot is `simcore.snapshot()`, whose `ceo` key carries the kernel's own derived
  // position. This is what makes attaching to a run in progress put the CEO where they
  // actually are rather than back at spawn.
  const ceo = snapshot.ceo
  if (isRecord(ceo)) {
    patch.ceo = {
      xMilli: toInt(ceo.x_milli),
      yMilli: toInt(ceo.y_milli),
      facing: toStr(ceo.facing, 'down'),
    }
  }

  // Comparisons do not survive a resync. A resync means the client was too far behind to catch
  // up by replay, so the fork tick every held projection was measured at is somewhere in a
  // stretch of the run this client never saw. Keeping them would leave figures on screen whose
  // basis is a state the client can no longer account for — and the CEO can ask again for the
  // cost of a tenth of a second.
  patch.comparisons = {}

  // A snapshot carries no tray of its own; it is rebuilt from the items that are blocked.
  if (patch.items !== undefined) {
    patch.tray = Object.values(patch.items)
      .filter((item) => item.status === 'blocked')
      .map((item) => ({
        itemId: item.id,
        personId: item.assignee,
        cpIndex: item.resolvedCount,
        label: '',
        kind: '',
        atTick: patch.tick ?? current.tick,
        atSeq: 0n,
      }))
  }

  return patch
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isNumberMap(value: unknown): value is Record<string, number> {
  if (!isRecord(value)) return false
  return Object.values(value).every((entry) => typeof entry === 'number')
}

// =========================================================================
// Reading the store without re-rendering
// =========================================================================

/**
 * The store's current state, for callers outside React.
 *
 * This is how the canvas reads. Not a shortcut around the framework: a renderer drawing at
 * 60 fps does not want a subscription at all, because every notification it received would
 * be one it had already accounted for by reading the latest value on its own next frame.
 */
export function runState(): RunStore {
  return useRunStore.getState()
}

/**
 * Bind to a slice of the store without re-rendering anything.
 *
 * The transient subscription the plan asks for. `subscribeWithSelector` fires the listener
 * only when the selected value actually changes, so a renderer that cares about one field
 * is not woken by every event. Returns its own unsubscribe, which callers must invoke —
 * symmetric cleanup, the same rule the renderer's `start`/`stop` pair follows.
 */
export function subscribeTo<T>(
  selector: (state: RunStore) => T,
  listener: (value: T, previous: T) => void,
  fireImmediately = false,
): () => void {
  return useRunStore.subscribe(selector, listener, { fireImmediately })
}
