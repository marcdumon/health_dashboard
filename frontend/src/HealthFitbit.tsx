import { useMutation, useQuery } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'

import { ApiError, downloadFitbit, getFitbitDay, loginFitbit, type FitbitExercise, type HealthView } from './api'
import { closeOutside } from './dialog'
import { clockOf, minutes } from './HealthPolarDetail'
import { Legend, Tooltip, useWidth } from './charts'

// the Fitbit band through Google Health, as the Sleep card: a few values of the page's day, the time of the last download,
// Download Fitbit (nothing is downloaded without it), Day (the charts of the day) and, when the token is missing or
// refused, Log in to Google Health

const longDay = (day: string) =>
  new Date(`${day}T12:00:00`).toLocaleDateString('en-GB', { weekday: 'long', day: 'numeric', month: 'long' })
// a time as the Withings card writes it: 18 Oct, 11:36
const when = (iso: string) => {
  const d = new Date(iso)
  return `${d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })}, ${d.toTimeString().slice(0, 5)}`
}
const HOUR = 3_600_000
const hhmm = (t: number) => new Date(t).toTimeString().slice(0, 5)

export function FitbitCard({ data, onChange }: { data: HealthView; onChange: (v: HealthView) => void }) {
  const status = data.fitbit
  const run = useMutation({ mutationFn: () => downloadFitbit(data.day), onSuccess: onChange })
  const login = useMutation({ mutationFn: () => loginFitbit(data.day), onSuccess: view => { run.reset(); onChange(view) } })
  const [open, setOpen] = useState(false)
  const v = status.day ?? {}
  const refused = run.error instanceof ApiError && run.error.needsLogin
  const shown: [string, string | null][] = [
    ['Resting HR', v['fitbit:resting_hr'] === undefined ? null : `${v['fitbit:resting_hr']} bpm`],
    ['HRV, night mean RMSSD', v['fitbit:hrv'] === undefined ? null : `${v['fitbit:hrv'].toFixed(1)} ms`],
    ['SpO2, night mean', v['fitbit:spo2'] === undefined ? null : `${v['fitbit:spo2'].toFixed(1)} %`],
    ['Time asleep', v['fitbit:asleep'] === undefined ? null : minutes(v['fitbit:asleep'] * 3600)],
    ['Steps', v['fitbit:steps'] === undefined ? null : v['fitbit:steps'].toLocaleString('en-GB')],
  ]
  return (
    <article className="habit-card health-latest health-fitbit">
      <h2>Fitbit{data.day !== data.today && <span className="muted"> {longDay(data.day)}</span>}</h2>
      {status.day ? (
        <dl className="health-values">
          {shown.map(([label, value]) => (
            <div key={label}>
              <dt>{label}</dt>
              {value === null ? <dd className="muted">not measured</dd> : <dd>{value}</dd>}
            </div>
          ))}
        </dl>
      ) : status.downloaded_at && <p className="muted">No Fitbit data on this day.</p>}
      <div className="health-status">
        <span className="muted">{status.downloaded_at ? `Downloaded ${when(status.downloaded_at)}` : 'Not downloaded yet'}</span>
        {status.configured && status.token && (
          <button type="button" className="quiet" disabled={run.isPending || login.isPending} onClick={() => run.mutate()}>
            {run.isPending ? 'Downloading…' : 'Download Fitbit'}
          </button>
        )}
        {status.day && <button type="button" className="quiet" onClick={() => setOpen(true)}>Day</button>}
        {status.configured && (!status.token || refused) && (
          <button type="button" className="quiet" disabled={login.isPending} onClick={() => login.mutate()}>
            {login.isPending ? 'Waiting for the login in the browser…' : 'Log in to Google Health'}
          </button>
        )}
        {status.message && <p className="cap-error">{status.message}</p>}
        {run.isError && <p className="cap-error">{run.error.message}</p>}
        {login.isError && <p className="cap-error">{login.error.message}</p>}
      </div>
      {open && <FitbitDayDialog day={data.day} onClose={() => setOpen(false)} />}
    </article>
  )
}

// the charts of a day on one clock axis from midnight to midnight, one under the other: the HR (the exercises shaded),
// the sleep stages, SpO2, HRV and the steps of each hour; the exercises in a table
function FitbitDayDialog({ day, onClose }: { day: string; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null)
  useEffect(() => { dialog.current?.showModal() }, [])
  const found = useQuery({ queryKey: ['fitbit-day', day], queryFn: () => getFitbitDay(day) })
  const [y, m, d] = day.split('-').map(Number)
  const from = new Date(y, m - 1, d).getTime()
  const to = new Date(y, m - 1, d + 1).getTime()
  const f = found.data
  const shade = (f?.exercises ?? []).map(e => ({ start: Date.parse(e.start_at), end: Date.parse(e.end_at), name: e.name }))
  const hours = new Map<number, number>()
  for (const [at, count] of f?.steps ?? []) {
    const hour = new Date(at)
    hour.setMinutes(0, 0, 0)
    hours.set(hour.getTime(), (hours.get(hour.getTime()) ?? 0) + count)
  }
  const empty = f && !f.hr.length && !f.spo2.length && !f.hrv.length && !f.steps.length && !f.stages.length && !f.exercises.length
  return createPortal(
    <dialog ref={dialog} className="health-analysis accent-health" onClose={onClose} aria-labelledby="fitbit-day-title"
            {...closeOutside}>
      <div className="analysis-head">
        <h2 id="fitbit-day-title">Fitbit<span className="muted">{longDay(day)}</span></h2>
        <button type="button" className="quiet" onClick={() => dialog.current?.close()}>Close</button>
      </div>
      {found.isError && <p className="cap-error">{found.error.message}</p>}
      {found.isPending && <p className="muted">Loading…</p>}
      {empty && <p className="muted">No Fitbit data on this day.</p>}
      {f && (
        <div className="fitbit-day">
          {f.hr.length > 0 && (
            <section className="analysis-card">
              <h3>Heart rate<span className="muted">bpm, the mean of each minute</span></h3>
              <DayChart from={from} to={to} points={f.hr} label="HR" unit="bpm" decimals={0} kind="line" shade={shade} />
            </section>
          )}
          {f.stages.length > 0 && (
            <section className="analysis-card">
              <h3>Sleep stages</h3>
              <StagesChart from={from} to={to} stages={f.stages} />
            </section>
          )}
          {f.spo2.length > 0 && (
            <section className="analysis-card">
              <h3>SpO2<span className="muted">%</span></h3>
              <DayChart from={from} to={to} points={f.spo2} label="SpO2" unit="%" decimals={1} kind="dots" />
            </section>
          )}
          {f.hrv.length > 0 && (
            <section className="analysis-card">
              <h3>HRV<span className="muted">RMSSD, ms</span></h3>
              <DayChart from={from} to={to} points={f.hrv} label="RMSSD" unit="ms" decimals={1} kind="dots" />
            </section>
          )}
          {hours.size > 0 && (
            <section className="analysis-card">
              <h3>Steps<span className="muted">each hour</span></h3>
              <DayChart from={from} to={to} points={[...hours.entries()].sort((a, b) => a[0] - b[0])} label="Steps" unit=""
                        decimals={0} kind="bars" />
            </section>
          )}
          {f.exercises.length > 0 && <Exercises exercises={f.exercises} />}
        </div>
      )}
    </dialog>,
    document.body,
  )
}

function Exercises({ exercises }: { exercises: FitbitExercise[] }) {
  const value = (v: number | null, unit = '') => (v === null ? '—' : `${Math.round(v)}${unit}`)
  return (
    <section className="analysis-card">
      <h3>Exercises</h3>
      <div className="health-table">
        <table>
          <thead>
            <tr><th scope="col">Exercise</th><th scope="col">Time</th><th scope="col" className="num">Active time</th>
              <th scope="col" className="num">Mean HR</th><th scope="col" className="num">kcal</th>
              <th scope="col" className="num">Active zone minutes</th></tr>
          </thead>
          <tbody>
            {exercises.map(e => (
              <tr key={e.start_at}>
                <td>{e.name}</td><td>{clockOf(e.start_at)}–{clockOf(e.end_at)}</td>
                <td className="num">{e.active_min === null ? '—' : minutes(e.active_min * 60)}</td>
                <td className="num">{value(e.mean_hr, ' bpm')}</td><td className="num">{value(e.kcal)}</td>
                <td className="num">{value(e.azm)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}

const PAD = { left: 44, right: 16, top: 10, bottom: 24 }

// the clock times of the axis: every 3 hours of the local day (a day with a clock change keeps its real times)
function clockTicks(from: number): number[] {
  const start = new Date(from)
  return Array.from({ length: 9 }, (_, i) => new Date(start.getFullYear(), start.getMonth(), start.getDate(), i * 3).getTime())
}

// the label of a clock tick; the midnight at the end of the day is 24:00
const tickLabel = (t: number, from: number) => (t > from && new Date(t).getHours() === 0 ? '24:00' : hhmm(t))

// the scale of a value: from 0 for bars, else the values with some room, never above 100 for a % of 100 or less
function scale(values: number[], decimals: number, bars: boolean, unit: string): [number, number] {
  const lo = Math.min(...values)
  const hi = Math.max(...values)
  if (bars) return [0, Math.max(1, Math.ceil(hi * 1.1))]
  const room = Math.max((hi - lo) * 0.15, decimals ? 0.5 : 2)
  return [Math.floor(lo - room), unit === '%' && hi <= 100 ? Math.min(100, Math.ceil(hi + room)) : Math.ceil(hi + room)]
}

// one value through the day: a line (broken where the band sent nothing for over 5 minutes), dots, or a bar per hour;
// the exercises as shaded periods; a crosshair gives the time and the value
function DayChart({ from, to, points, label, unit, decimals, kind, shade = [] }: {
  from: number; to: number; points: [number, number][]; label: string; unit: string; decimals: number
  kind: 'line' | 'dots' | 'bars'; shade?: { start: number; end: number; name: string }[]
}) {
  const [ref, width] = useWidth()
  const [hover, setHover] = useState<number | null>(null)
  const height = 170
  const plotW = width - PAD.left - PAD.right
  const plotH = height - PAD.top - PAD.bottom
  const [lo, hi] = scale(points.map(p => p[1]), decimals, kind === 'bars', unit)
  const x = (t: number) => PAD.left + (plotW * (t - from)) / (to - from)
  const y = (v: number) => PAD.top + plotH * (1 - (v - lo) / (hi - lo))
  let path = ''
  if (kind === 'line') {
    points.forEach(([t, v], i) => {
      const gap = i === 0 || t - points[i - 1][0] > 5 * 60_000
      path += `${gap ? 'M' : 'L'}${x(t).toFixed(1)},${y(v).toFixed(1)}`
    })
  }
  // a bar stands for its hour: its middle is its place
  const middle = (t: number) => t + (kind === 'bars' ? HOUR / 2 : 0)
  const pick = (clientX: number, box: DOMRect) => {
    const t = from + ((clientX - box.left - PAD.left) / plotW) * (to - from)
    let best = 0
    points.forEach((p, i) => { if (Math.abs(middle(p[0]) - t) < Math.abs(middle(points[best][0]) - t)) best = i })
    setHover(best)
  }
  const h = hover === null ? null : points[hover]
  const barW = Math.max(1, (plotW * HOUR) / (to - from) - 2)
  const time = (t: number) => (kind === 'bars' ? `${hhmm(t)}–${hhmm(t + HOUR)}` : hhmm(t))
  return (
    <div className="chart" ref={ref}>
      {shade.length > 0 && <Legend items={[{ label: 'Exercise', kind: 'exercise' }]} />}
      <svg width={width} height={height} role="img" aria-label={`${label} through the day${unit ? `, ${unit}` : ''}`}
           onPointerMove={e => pick(e.clientX, e.currentTarget.getBoundingClientRect())} onPointerLeave={() => setHover(null)}>
        {shade.map(s => (
          <rect key={s.start} className="exercise-shade" x={x(Math.max(from, s.start))} y={PAD.top}
                width={Math.max(2, x(Math.min(to, s.end)) - x(Math.max(from, s.start)))} height={plotH}>
            <title>{s.name}</title>
          </rect>
        ))}
        <g className="axes">
          {[lo, (lo + hi) / 2, hi].map(t => (
            <g key={t}>
              <line x1={PAD.left} x2={width - PAD.right} y1={y(t)} y2={y(t)} className="grid" />
              <text x={PAD.left - 6} y={y(t) + 4} textAnchor="end">{Math.round(t)}</text>
            </g>
          ))}
          {clockTicks(from).map(t => (
            <text key={t} x={x(t)} y={height - 6} textAnchor="middle">{tickLabel(t, from)}</text>
          ))}
        </g>
        {kind === 'line' && <path d={path} className="line s1" />}
        {kind === 'dots' && points.map(([t, v]) => <circle key={t} className="dot s1" cx={x(t)} cy={y(v)} r={3} />)}
        {kind === 'bars' && points.map(([t, v]) => (
          <rect key={t} className="bar s1" x={x(t) + 1} y={y(v)} width={barW} height={Math.max(0, y(0) - y(v))} rx={2} />
        ))}
        {h && <line className="crosshair" x1={x(middle(h[0]))} x2={x(middle(h[0]))} y1={PAD.top} y2={PAD.top + plotH} />}
      </svg>
      <Tooltip tip={h ? {
        x: Math.min(x(h[0]) + 12, width - 170), y: 8, title: time(h[0]),
        rows: [{ key: unit || label.toLowerCase(), value: h[1].toFixed(decimals), kind: 's1' }],
      } : null} />
    </div>
  )
}

// the order of the stages from the top: awake, then lighter to deeper sleep; a stage of the classic sleep record
// (restless, asleep) gets its own row
const STAGE_ROWS: [string, string][] = [['AWAKE', 'Awake'], ['RESTLESS', 'Restless'], ['REM', 'REM'], ['LIGHT', 'Light'],
                                        ['ASLEEP', 'Asleep'], ['DEEP', 'Deep']]

// the hypnogram of the nights that touch the day: each stage period as a bar in the row of its stage, cut at midnight;
// hover gives the stage and its times
function StagesChart({ from, to, stages }: { from: number; to: number; stages: { start: string; end: string; type: string }[] }) {
  const [ref, width] = useWidth()
  const [hover, setHover] = useState<number | null>(null)
  const present = new Set(stages.map(s => s.type))
  const rows = [...STAGE_ROWS.filter(([type]) => present.has(type)),
                ...[...present].filter(type => !STAGE_ROWS.some(([t]) => t === type)).map(type => [type, type] as [string, string])]
  const rowH = 22
  const height = PAD.top + rows.length * rowH + PAD.bottom
  const plotW = width - PAD.left - PAD.right
  const x = (t: number) => PAD.left + (plotW * (Math.min(to, Math.max(from, t)) - from)) / (to - from)
  const shown = stages.map(s => ({ ...s, a: Date.parse(s.start), b: Date.parse(s.end) })).filter(s => s.b > from && s.a < to)
  const h = hover === null ? null : shown[hover]
  return (
    <div className="chart" ref={ref}>
      <svg width={width} height={height} role="img" aria-label="Sleep stages through the day">
        <g className="axes">
          {rows.map(([type, name], i) => (
            <text key={type} x={PAD.left - 6} y={PAD.top + i * rowH + rowH / 2 + 4} textAnchor="end">{name}</text>
          ))}
          {clockTicks(from).map(t => (
            <text key={t} x={x(t)} y={height - 6} textAnchor="middle">{tickLabel(t, from)}</text>
          ))}
        </g>
        {shown.map((s, i) => {
          const row = rows.findIndex(([type]) => type === s.type)
          return (
            <rect key={`${s.start}-${s.type}`} className="bar s1" x={x(s.a)} y={PAD.top + row * rowH + 3}
                  width={Math.max(1, x(s.b) - x(s.a))} height={rowH - 6} rx={2} opacity={hover === null || hover === i ? 1 : 0.6}
                  onPointerEnter={() => setHover(i)} onPointerLeave={() => setHover(null)} />
          )
        })}
      </svg>
      <Tooltip tip={h ? {
        x: Math.min(x(h.a) + 12, width - 170), y: 8, title: `${clockOf(h.start)}–${clockOf(h.end)}`,
        rows: [{ key: rows.find(([type]) => type === h.type)?.[1] ?? h.type, value: minutes((h.b - h.a) / 1000), kind: 's1' }],
      } : null} />
    </div>
  )
}
