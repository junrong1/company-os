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
 * A decision point, as authored.
 *
 * No `tacit` field, and that is the mechanic rather than an omission: the line only an
 * in-person resolution surfaces is never shipped at genesis. It reaches the client on the
 * resolution that earned it.
 */
export interface CheckpointDef {
  at_percent: number
  kind: string
  label: string
  prompt: string
  options: Array<{ label: string; detail: string }>
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
  kind: 'RESYNC' | 'RESYNC_REQUIRED' | 'POSITION_ECHO' | 'ERROR'
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
    tray: [],
    deliverables: [],
    terminal: null,
    tacitLines: {},
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

  if (frame.kind === 'RESYNC_REQUIRED' || frame.kind === 'ERROR') {
    set({ connection: 'lost', lastError: toStr(frame.detail, frame.kind) })
  }
}

function applyEvent(set: Setter, get: Getter, frame: EventFrame, seq: bigint): void {
  const state = get()
  const payload = frame.payload
  const tick = toBig(payload.tick ?? frame.tick)
  const patch: Partial<RunStore> = { appliedSeq: seq, tick }

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
    roster: (payload.roster ?? {}) as Record<string, RosterEntry>,
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
