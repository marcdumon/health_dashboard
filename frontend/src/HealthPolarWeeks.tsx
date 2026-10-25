import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'

import { getPolarActivity, getPolarEnergy, getPolarWeeks, getPolarZones } from './api'
import { Legend, niceMax, Tooltip, useWidth } from './charts'

// the time per week of the Polar H10 sessions as stacked bars: by activity (all activities: the time adds up, whatever
// the activity), the minutes in each HR zone, and the net energy expenditure by activity. The legend names the parts, the
// tooltip gives the values of a week, and a table under the chart holds the same numbers

const PAD = { left: 40, right: 16, top: 12, bottom: 24 }
const HEIGHT = 170
const SLOTS = 8
const OTHER = 'Other or none'
// what the zones need, and where to set it
export const ZONES_NEED = 'The zones need your birth date: set birth_date in the [person] part of person.toml in the app '
  + 'folder (person.example.toml shows how: copy it to person.toml the first time), then restart the app.'
const weekOf = (day: string) => new Date(`${day}T12:00:00`).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })

// the parts of the bars, bottom first: their name and their colour class
type Part = { name: string; kind: string }

// a week and its value of each part, in `unit`
type Week = { week: string; values: Record<string, number> }

// target: a line at a value each week should reach, with its name
function WeekBars({ weeks, parts, label, unit = 'min', target }: {
  weeks: Week[]; parts: Part[]; label: string; unit?: string; target?: { value: number; label: string }
}) {
  const [ref, width] = useWidth()
  const [hover, setHover] = useState<number | null>(null)
  const shown = parts.filter(p => weeks.some(w => w.values[p.name]))
  const totals = weeks.map(w => shown.reduce((sum, p) => sum + (w.values[p.name] ?? 0), 0))
  const max = niceMax(Math.max(30, target?.value ?? 0, ...totals))
  const plotW = width - PAD.left - PAD.right
  const plotH = HEIGHT - PAD.top - PAD.bottom
  const band = plotW / Math.max(1, weeks.length)
  const bar = Math.max(4, Math.min(28, band - 4))
  const y = (v: number) => PAD.top + plotH * (1 - v / max)
  const every = Math.max(1, Math.ceil(weeks.length / Math.floor(plotW / 56)))
  if (shown.length === 0) return <div className="chart" ref={ref}><p className="stats-empty">No sessions in these days.</p></div>
  return (
    <div className="chart" ref={ref}>
      <Legend items={shown.map(p => ({ label: p.name, kind: p.kind }))} />
      <svg width={width} height={HEIGHT} role="img" aria-label={label} onPointerLeave={() => setHover(null)}>
        <g className="axes">
          {[0, max / 2, max].map(t => (
            <g key={t}>
              <line x1={PAD.left} x2={width - PAD.right} y1={y(t)} y2={y(t)} className="grid" />
              <text x={PAD.left - 6} y={y(t) + 4} textAnchor="end">{t}</text>
            </g>
          ))}
          {weeks.map((w, i) => i % every === 0 && (
            <text key={w.week} x={PAD.left + band * (i + 0.5)} y={HEIGHT - 6} textAnchor="middle">{weekOf(w.week)}</text>
          ))}
        </g>
        {weeks.map((w, i) => {
          let base = 0
          return (
            <g key={w.week} className={hover === i ? 'hovered' : ''} onPointerEnter={() => setHover(i)}>
              <rect className="hit" x={PAD.left + band * i} y={PAD.top} width={band} height={plotH} />
              {shown.map(p => {
                const v = w.values[p.name] ?? 0
                if (!v) return null
                const top = y(base + v)
                const bottom = y(base)
                base += v
                return <rect key={p.name} className={`seg ${p.kind}`} x={PAD.left + band * i + (band - bar) / 2} y={top}
                             width={bar} height={Math.max(1, bottom - top)} />
              })}
            </g>
          )
        })}
        {target && (
          <g>
            <line className="target" x1={PAD.left} x2={width - PAD.right} y1={y(target.value)} y2={y(target.value)} />
            <text className="target-label" x={width - PAD.right} y={y(target.value) - 5} textAnchor="end">
              {target.label} {target.value} {unit}
            </text>
          </g>
        )}
      </svg>
      <Tooltip tip={hover === null || !totals[hover] ? null : {
        x: Math.min(PAD.left + band * (hover + 1) + 8, width - 170), y: 8, title: `Week of ${weekOf(weeks[hover].week)}`,
        rows: [...shown.filter(p => weeks[hover].values[p.name]).map(p => ({
          key: p.name, value: `${weeks[hover].values[p.name]} ${unit}`, kind: p.kind })),
               { key: 'in all', value: `${totals[hover]} ${unit}`, kind: 'none' }],
      }} />
      <details className="polar-week-table">
        <summary>Table, one row per week</summary>
        <div className="health-table">
          <table>
            <thead>
              <tr>
                <th scope="col">Week of</th>
                {shown.map(p => <th key={p.name} scope="col" className="num">{p.name}</th>)}
                <th scope="col" className="num">In all</th>
              </tr>
            </thead>
            <tbody>
              {weeks.map((w, i) => (
                <tr key={w.week}>
                  <td>{weekOf(w.week)}</td>
                  {shown.map(p => <td key={p.name} className="num">{w.values[p.name] ?? 0}</td>)}
                  <td className="num">{totals[i]}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </div>
  )
}

// the values of each week by activity, and the parts: each activity keeps the colour of its place in the activity list (8
// colours); later activities and the sessions without one share one grey part
function byActivity(found: { week: string; values: Record<string, number> }[], names: string[]): [Week[], Part[]] {
  const group = (name: string) => (names.indexOf(name) >= 0 && names.indexOf(name) < SLOTS ? name : OTHER)
  const weeks = found.map(w => {
    const values: Record<string, number> = {}
    for (const [name, v] of Object.entries(w.values)) values[group(name)] = (values[group(name)] ?? 0) + v
    return { week: w.week, values }
  })
  return [weeks, [...names.slice(0, SLOTS).map((name, i) => ({ name, kind: `c${i + 1}` })), { name: OTHER, kind: 'other' }]]
}

// the time per week by activity, of all sessions
export function PolarWeeks({ days }: { days: number }) {
  const found = useQuery({ queryKey: ['polar-weeks', days], queryFn: () => getPolarWeeks(days, null) })
  if (found.isError) return <p className="cap-error">{found.error.message}</p>
  if (!found.data) return null
  const [weeks, parts] = byActivity(found.data.weeks.map(w => ({ week: w.week, values: w.minutes })), found.data.activities)
  return <WeekBars weeks={weeks} parts={parts} label="Minutes of the sessions per week, by activity" />
}

// the net energy expenditure per week by activity, of all sessions but the HRV tests, in the colours of the time per week;
// without a value it needs in person.toml, what to set
export function EnergyWeeks({ days }: { days: number }) {
  const found = useQuery({ queryKey: ['polar-energy', days], queryFn: () => getPolarEnergy(days) })
  if (found.isError) return <p className="cap-error">{found.error.message}</p>
  if (!found.data) return null
  if (found.data.missing) return <p className="muted">{found.data.missing}</p>
  const [weeks, parts] = byActivity(found.data.weeks.map(w => ({ week: w.week, values: w.kcal })), found.data.activities)
  return <WeekBars weeks={weeks} parts={parts} unit="kcal" label="Net energy expenditure of the sessions per week, by activity" />
}

// the WHO minutes of activity per week: the moderate minutes and the vigorous ones counted double, against the WHO minimum
// (a minimum: more is fine); without a birth date in person.toml, what to set
export function ActivityWeeks({ days }: { days: number }) {
  const found = useQuery({ queryKey: ['polar-activity', days], queryFn: () => getPolarActivity(days) })
  if (found.isError) return <p className="cap-error">{found.error.message}</p>
  if (!found.data) return null
  if (found.data.missing) return <p className="muted">{found.data.missing}</p>
  const parts = [{ name: 'Moderate', kind: 'z2' }, { name: 'Vigorous, counted double', kind: 'z4' }]
  const weeks = found.data.weeks.map(w => ({ week: w.week, values: { Moderate: w.moderate, 'Vigorous, counted double': 2 * w.vigorous } }))
  return <WeekBars weeks={weeks} parts={parts} label="WHO minutes of activity per week"
                   target={{ value: found.data.target, label: 'WHO minimum' }} />
}

// the minutes per week in each HR zone (zone 1 at the bottom), of all sessions but the HRV tests; without a birth date in
// person.toml, where to set it
export function ZoneWeeks({ days }: { days: number }) {
  const found = useQuery({ queryKey: ['polar-zones', days], queryFn: () => getPolarZones(days) })
  if (found.isError) return <p className="cap-error">{found.error.message}</p>
  if (!found.data) return null
  const { limits, weeks } = found.data
  if (limits === null || weeks === null) {
    return <p className="muted">{ZONES_NEED}</p>
  }
  const parts = limits.map((low, k) => ({
    name: `Zone ${k + 1} (${k === limits.length - 1 ? `${low}+` : `${low}–${limits[k + 1] - 1}`} bpm)`, kind: `z${k + 1}` }))
  return (
    <WeekBars weeks={weeks.map(w => ({ week: w.week, values: Object.fromEntries(parts.map((p, k) => [p.name, w.minutes[k]])) }))}
              parts={parts} label="Minutes of the sessions per week in each HR zone" />
  )
}
