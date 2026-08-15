/**
 * The HUD: what the CEO steers by.
 *
 * Components only. The rules they render — favourable direction, trajectory shape, runway,
 * decision pressure, and which tiles may be removed — live in `hud-model.ts`, where they are
 * decidable from plain data and the suite can state them without a DOM.
 *
 * Two things about the subscriptions matter. Every selector is the smallest slice its tile
 * renders, so a burst of events that moves cash does not wake the capacity tile. And every
 * object or array selector goes through `useShallow`: in zustand's current major a selector
 * returning a fresh reference re-renders forever, because the equality check is `Object.is` on
 * the selector's output and a new object is never `Object.is` to the last one.
 */

import { useEffect, useMemo, useState } from 'react'
import { useShallow } from 'zustand/react/shallow'

import {
  type MetricDef,
  direction,
  directionColour,
  loadColour,
  loadFillPermille,
  overCeiling,
} from '../design/tokens'
import { type TrajectoryPoint, useRunStore } from '../net/store'
import { Mark } from './Marking'
import {
  CAPACITY_TILE,
  DEFAULT_COMPOSITION,
  NON_REMOVABLE,
  PRESSURE_TILE,
  RUNWAY_TILE,
  type Pressure,
  addTile,
  decisionPressure,
  loadComposition,
  pressureColour,
  removeTile,
  runwayDays,
  saveComposition,
  sparklinePoints,
  trajectoryDirection,
} from './hud-model'

// =========================================================================
// The component
// =========================================================================

function formatValue(metric: MetricDef, value: number): string {
  if (metric.key === 'cash') return value.toLocaleString('en-US')
  return String(value)
}

function formatDelta(metric: MetricDef, delta: number): string {
  const sign = delta > 0 ? '+' : ''
  const unit = metric.chip_unit === '' ? '' : metric.chip_unit
  return `${sign}${delta}${unit}`
}

interface MetricTileProps {
  metric: MetricDef
  value: number
  delta: number
  points: readonly TrajectoryPoint[]
}

function MetricTile({ metric, value, delta, points }: MetricTileProps) {
  const movement = direction(metric, delta)
  const trend = trajectoryDirection(metric, points)
  const fill =
    metric.display_max > 0
      ? Math.max(0, Math.min(100, Math.round((value / metric.display_max) * 100)))
      : 0

  return (
    <article className="tile" data-tile={metric.key} data-trend={trend}>
      <p className="tile__label">
        {metric.label}
        {/* On the label rather than beside the value: this tile renders a value, a delta and a
            sparkline, and they are all the same authored figure seen three ways. One marking
            covers the tile; three would read as three separate claims. */}
        <Mark of={metric.label} />
      </p>
      <div className="tile__row">
        <span className="tile__value">{formatValue(metric, value)}</span>
        <span className="tile__unit">{metric.unit === '/100' ? '' : metric.unit}</span>
        {delta !== 0 && (
          <span
            className="tile__delta"
            data-direction={movement}
            style={{ color: directionColour(movement) }}
          >
            {formatDelta(metric, delta)}
          </span>
        )}
      </div>
      <svg
        className="spark"
        viewBox="0 0 100 24"
        preserveAspectRatio="none"
        aria-hidden="true"
        data-points={points.length}
      >
        <polyline
          points={sparklinePoints(points, 100, 24)}
          fill="none"
          stroke={directionColour(trend)}
          strokeWidth="1.5"
        />
      </svg>
      <div className="bar">
        <i style={{ width: `${fill}%` }} />
      </div>
    </article>
  )
}

function RunwayTile({ cash, dailyCost }: { cash: number; dailyCost: number }) {
  const days = runwayDays(cash, dailyCost)
  return (
    <article className="tile tile--fixed" data-tile={RUNWAY_TILE}>
      <p className="tile__label">
        Runway
        {/* Marked whatever it reads. Runway is `null` before the first day's costs and zero at
            insolvency, and both of those are still authored arithmetic — a marking that
            appeared only once there was a number would be absent at exactly the two moments
            the figure is most likely to be believed. */}
        <Mark of="Runway" />
      </p>
      <div className="tile__row">
        <span className="tile__value">{days === null ? '—' : days}</span>
        <span className="tile__unit">{days === null ? 'no burn yet' : 'days'}</span>
      </div>
      <p className="tile__foot">
        {dailyCost > 0 ? `at ${dailyCost} $K/day` : 'awaiting the first day of costs'}
      </p>
    </article>
  )
}

function PressureTile({ pressure }: { pressure: Pressure }) {
  return (
    <article
      className="tile tile--fixed"
      data-tile={PRESSURE_TILE}
      // Neutral chrome, always. The beam belongs to the people this counts, not to the count.
      style={{ color: pressureColour(pressure) }}
    >
      <p className="tile__label">
        Waiting on you
        {/* The count is a fact about the log; the *supply* it is counted against is authored,
            and so is the wait measured in ticks. Marking the tile rather than picking the two
            derived halves out of it keeps R28 simple: nothing here is presented as measured. */}
        <Mark of="Decision pressure" />
      </p>
      <div className="tile__row">
        <span className="tile__value">{pressure.waiting}</span>
        <span className="tile__unit">of {pressure.supply} this run</span>
      </div>
      <p className="tile__foot">
        {pressure.waiting === 0
          ? 'nobody is stopped'
          : `longest wait ${pressure.oldestWaitTicks.toString()} ticks`}
      </p>
    </article>
  )
}

function CapacityTile({
  load,
  ceiling,
  labels,
}: {
  load: Record<string, number>
  ceiling: number
  labels: Record<string, string>
}) {
  const lines = Object.entries(load).sort(([left], [right]) => left.localeCompare(right))

  return (
    <article className="tile tile--wide" data-tile={CAPACITY_TILE}>
      <p className="tile__label">
        Capacity
        {/* The load percentage is per-mille of an authored monthly capacity against an authored
            ceiling — every part of it invented, including the scale it is read against. */}
        <Mark of="Capacity" />
      </p>
      {lines.length === 0 && <p className="tile__foot">no departments yet</p>}
      {lines.map(([director, permille]) => (
        <div className="heat" key={director} data-over={overCeiling(permille, ceiling)}>
          <span className="heat__name">{labels[director] ?? director}</span>
          <div className="heat__track">
            <i
              // The same ramp the DAG's nodes use, reading the same ceiling off the wire.
              style={{
                width: `${loadFillPermille(permille, ceiling) / 10}%`,
                background: loadColour(permille, ceiling),
              }}
            />
          </div>
          <span className="heat__value">{Math.round((permille * 100) / ceiling)}%</span>
        </div>
      ))}
    </article>
  )
}

export interface HudProps {
  /** Injectable so the suite can drive persistence without touching a real browser store. */
  storage?: Pick<Storage, 'getItem' | 'setItem'> | null
}

export function Hud({ storage }: HudProps = {}) {
  const resolvedStorage =
    storage === undefined ? (typeof localStorage === 'undefined' ? null : localStorage) : storage

  const [composition, setComposition] = useState<string[]>(() =>
    loadComposition(resolvedStorage),
  )

  useEffect(() => {
    saveComposition(resolvedStorage, composition)
  }, [resolvedStorage, composition])

  // Narrow selectors, and every object or array one goes through `useShallow`. Without it a
  // selector building a fresh object each call re-renders forever: the equality check is
  // `Object.is` on the output, and a new object is never `Object.is` to the previous one.
  const metricDefs = useRunStore(useShallow((state) => state.genesis?.metricDefs ?? []))
  const metrics = useRunStore(useShallow((state) => state.metrics))
  const deltas = useRunStore(useShallow((state) => state.deltas))
  const trajectories = useRunStore(useShallow((state) => state.trajectories))
  const load = useRunStore(useShallow((state) => state.load))
  const dailyCost = useRunStore((state) => state.dailyCost)
  const tick = useRunStore((state) => state.tick)
  const trayLength = useRunStore((state) => state.tray.length)
  const tray = useRunStore(useShallow((state) => state.tray.map((entry) => entry.atTick)))
  const supply = useRunStore((state) => state.genesis?.decisionSupply ?? 0)
  const ceiling = useRunStore((state) => state.genesis?.load.ceiling ?? 1000)
  const roster = useRunStore(useShallow((state) => state.genesis?.roster ?? {}))

  const pressure = useMemo(
    () => decisionPressure(tray.map((atTick) => ({ atTick })), tick, supply),
    [tray, tick, supply],
  )

  const departmentLabels = useMemo(() => {
    const labels: Record<string, string> = {}
    for (const [id, entry] of Object.entries(roster)) {
      if (entry.rank === 'director') labels[id] = entry.dept
    }
    return labels
  }, [roster])

  const defsByKey = useMemo(() => {
    const byKey: Record<string, MetricDef> = {}
    for (const def of metricDefs) byKey[def.key] = def
    return byKey
  }, [metricDefs])

  return (
    <section className="hud" data-tiles={composition.length} data-waiting={trayLength}>
      {composition.map((tile) => {
        if (tile === RUNWAY_TILE) {
          return (
            <RunwayTile key={tile} cash={metrics.cash ?? 0} dailyCost={dailyCost} />
          )
        }
        if (tile === PRESSURE_TILE) {
          return <PressureTile key={tile} pressure={pressure} />
        }
        if (tile === CAPACITY_TILE) {
          return (
            <CapacityTile key={tile} load={load} ceiling={ceiling} labels={departmentLabels} />
          )
        }

        const metric = defsByKey[tile]
        if (metric === undefined) return null
        return (
          <MetricTile
            key={tile}
            metric={metric}
            value={metrics[tile] ?? 0}
            delta={deltas[tile] ?? 0}
            points={trajectories[tile] ?? []}
          />
        )
      })}

      <HudComposer composition={composition} onChange={setComposition} />
    </section>
  )
}

function HudComposer({
  composition,
  onChange,
}: {
  composition: string[]
  onChange: (next: string[]) => void
}) {
  const [refusal, setRefusal] = useState<string | null>(null)

  return (
    <details className="composer">
      <summary>HUD</summary>
      {DEFAULT_COMPOSITION.map((tile) => {
        const present = composition.includes(tile)
        const fixed = NON_REMOVABLE.includes(tile)
        return (
          <label key={tile} data-fixed={fixed}>
            <input
              type="checkbox"
              checked={present}
              disabled={fixed}
              onChange={() => {
                setRefusal(null)
                if (!present) {
                  onChange(addTile(composition, tile))
                  return
                }
                try {
                  onChange(removeTile(composition, tile))
                } catch (cause) {
                  setRefusal(cause instanceof Error ? cause.message : String(cause))
                }
              }}
            />
            {tile}
          </label>
        )
      })}
      {refusal !== null && <p className="composer__refusal">{refusal}</p>}
    </details>
  )
}
