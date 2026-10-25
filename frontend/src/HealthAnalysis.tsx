import { useQuery } from '@tanstack/react-query'
import { createContext, useContext, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'

import { getHealthAnalysis, getHealthRelation, type HealthAnalysis, type HealthRelation } from './api'
import { closeOutside } from './dialog'
import { clockOf, dayPart, type Line } from './HealthCharts'
import { DayMeasurements } from './HealthMeasures'
import { DaySessions } from './HealthPolar'
import { inText, RELATION_CHOICES, type Value } from './healthValues'
import { Legend, Tooltip, useWidth } from './charts'

// the large view of one chart of the Health history: the day values with a rolling mean and a band of one standard
// deviation, the numbers of the period, the last week against earlier weeks, a histogram, the change from one day to the next,
// each year by month, the correlation with a second value, and the outliers against the surrounding days, each of which opens its
// measurements to remove a wrong one. The server calculates; this file draws.

// activity: the Polar H10 values of that activity only
export type AnalysisSpec = { title: string; unit: string; decimals: number; lines: Line[]; activity?: number | null }
type Data = HealthAnalysis & { stats: NonNullable<HealthAnalysis['stats']>; histogram: NonNullable<HealthAnalysis['histogram']> }
type Draw = { data: Data; unit: string; decimals: number }

export const PERIODS: [number | null, string][] = [[90, '90 days'], [365, '1 year'], [1095, '3 years'], [null, 'All data']]
const WINDOWS = [7, 30, 90]
const VIEW_KEY = 'health-analysis-view'
// the user's own scores: never a wrong measurement, so no outlier list (nor the sleep values)
const DAILY = ['angina', 'ketones']
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
const PAD = { left: 52, right: 20, top: 14, bottom: 26 }
const DAY_MS = 86_400_000

export const ordinal = (day: string) => Math.round(Date.parse(`${day}T00:00:00Z`) / DAY_MS)
const utc = (n: number) => new Date(n * DAY_MS)
const fullDay = (day: string) =>
  new Date(`${day}T12:00:00`).toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' })
const signed = (v: number, decimals: number) => `${v > 0 ? '+' : v < 0 ? '−' : ''}${Math.abs(v).toFixed(decimals)}`
const dayMonth = (day: string) => new Date(`${day}T12:00:00`).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })
// the newest day of a list of days
const newest = <T extends { day: string }>(list: T[]): T => list.reduce((a, b) => (b.day > a.day ? b : a))

// a round step of 1, 2, 2.5 or 5 times a power of ten, and the decimals it needs
function niceStep(raw: number): number {
  const power = 10 ** Math.floor(Math.log10(raw))
  return [1, 2, 2.5, 5, 10].map(m => m * power).find(s => s >= raw * (1 - 1e-9)) ?? 10 * power
}
const digits = (step: number) => {
  const text = String(Number(step.toPrecision(3)))
  return text.includes('.') ? text.split('.')[1].length : 0
}

// a scale from round number to round number that holds lo and hi, with its ticks
function scale(lo: number, hi: number, count = 4): { min: number; max: number; ticks: number[]; step: number } {
  const room = hi === lo ? Math.max(Math.abs(lo) * 0.05, 1) : 0
  const step = niceStep((hi - lo + 2 * room) / count)
  const min = Math.floor((lo - room) / step + 1e-9) * step
  const max = Math.ceil((hi + room) / step - 1e-9) * step
  const ticks: number[] = []
  for (let t = min; t <= max + step / 2; t += step) ticks.push(Number(t.toFixed(10)))
  return { min, max, ticks, step }
}

function Grid({ width, ticks, y, decimals }: { width: number; ticks: number[]; y: (v: number) => number; decimals: number }) {
  return (
    <g className="axes">
      {ticks.map(t => (
        <g key={t}>
          <line x1={PAD.left} x2={width - PAD.right} y1={y(t)} y2={y(t)} className="grid" />
          <text x={PAD.left - 6} y={y(t) + 4} textAnchor="end">{t.toFixed(decimals)}</text>
        </g>
      ))}
    </g>
  )
}

// the date ticks of a period: Mondays for a short one, months up to three years, years beyond; thinned to fit
export function timeTicks(start: number, end: number, plotW: number): { at: number; label: string }[] {
  const span = end - start + 1
  const found: { at: number; label: string }[] = []
  const short = (d: Date, options: Intl.DateTimeFormatOptions) => d.toLocaleDateString('en-GB', { ...options, timeZone: 'UTC' })
  if (span <= 120) {
    for (let n = start; n <= end; n++) {
      if (utc(n).getUTCDay() === 1) found.push({ at: n, label: short(utc(n), { day: 'numeric', month: 'short' }) })
    }
  } else if (span <= 1100) {
    const first = utc(start)
    for (let d = new Date(Date.UTC(first.getUTCFullYear(), first.getUTCMonth() + 1, 1)); d.getTime() <= end * DAY_MS;
      d = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 1))) {
      found.push({ at: Math.round(d.getTime() / DAY_MS),
                   label: d.getUTCMonth() === 0 ? String(d.getUTCFullYear()) : short(d, { month: 'short' }) })
    }
  } else {
    for (let year = utc(start).getUTCFullYear() + 1; year <= utc(end).getUTCFullYear(); year++) {
      found.push({ at: Math.round(Date.UTC(year, 0, 1) / DAY_MS), label: String(year) })
    }
  }
  const gap = found.length > 1 ? found[1].at - found[0].at : span
  const every = Math.max(1, Math.ceil(64 / ((plotW / span) * gap)))
  return found.filter((_, i) => i % every === 0)
}

// each recording of the value as a dot at its time (several on one day show apart; a value of the day, as the angina
// score, has its day values as dots), the rolling mean of the day values and its band
function TrendChart({ data, unit, decimals }: Draw) {
  const [ref, width] = useWidth()
  const [hover, setHover] = useState<number | null>(null)
  const height = 340
  const { points } = data
  // the recordings at their place in the days: the ordinal of their day plus the part of the day passed, as the day values
  // sit at the middle of their day
  const recorded = data.readings.map(r => ({ ...r, n: ordinal(r.day) + dayPart(r.at) - 0.5 }))
  const start = ordinal(data.start)
  const end = ordinal(data.end)
  const plotW = width - PAD.left - PAD.right
  const plotH = height - PAD.top - PAD.bottom
  // the band of ±1 standard deviation never takes the scale below 0 when no value is below 0 (a time, a count), nor a %
  // above 100 when no value is above 100 (a share): it stops there
  const values = [...points.map(p => p.value), ...recorded.map(r => r.value)]
  const floor = Math.min(...values) >= 0 ? 0 : -Infinity
  const ceiling = unit === '%' && Math.max(...values) <= 100 ? 100 : Infinity
  const low = (p: Data['points'][number]) => Math.max(floor, p.mean - (p.std ?? 0))
  const high = (p: Data['points'][number]) => Math.min(ceiling, p.mean + (p.std ?? 0))
  const { min, max, ticks, step } = scale(Math.min(...values, ...points.map(low)), Math.max(...values, ...points.map(high)))
  const x = (n: number) => PAD.left + (plotW * (n - start + 0.5)) / (end - start + 1)
  const y = (v: number) => PAD.top + plotH * (1 - (v - min) / (max - min))
  const days = points.map(p => ordinal(p.day))
  // the mean and its band break where no value comes for longer than the window: no line across a year without data
  const runs: number[][] = []
  days.forEach((n, i) => {
    if (i === 0 || n - days[i - 1] > data.window) runs.push([])
    runs[runs.length - 1].push(i)
  })
  const at = (i: number, v: number) => `${x(days[i]).toFixed(1)},${y(v).toFixed(1)}`
  const meanPath = runs.map(run => run.map((i, j) => `${j ? 'L' : 'M'}${at(i, points[i].mean)}`).join('')).join('')
  const bandPath = runs.filter(run => run.length > 1).map(run => {
    const up = run.map((i, j) => `${j ? 'L' : 'M'}${at(i, high(points[i]))}`).join('')
    const down = [...run].reverse().map(i => `L${at(i, low(points[i]))}`).join('')
    return `${up}${down}Z`
  }).join('')
  const far = new Set(data.outliers.map(o => o.day))
  const pick = (clientX: number, box: DOMRect) => {
    const n = start + ((clientX - box.left - PAD.left) / plotW) * (end - start + 1) - 0.5
    let best = 0
    days.forEach((d, i) => { if (Math.abs(d - n) < Math.abs(days[best] - n)) best = i })
    setHover(best)
  }
  const h = hover === null ? null : points[hover]
  const shown = Math.max(decimals, digits(step))
  return (
    <div className="chart" ref={ref}>
      <Legend items={[{ label: data.per === 'session' ? 'Each session' : recorded.length ? 'Each recording' : 'Daily mean',
                        kind: 'dots' },
                      { label: `${data.window}-day centred rolling mean`, kind: 's1', line: true },
                      { label: '±1 standard deviation', kind: 'band' }]} />
      <svg width={width} height={height} role="img"
           aria-label={`${data.per === 'session' ? 'Each session' : recorded.length ? 'Each recording' : 'Daily means'} with the `
             + `rolling mean, ${unit}`}
           onPointerMove={e => pick(e.clientX, e.currentTarget.getBoundingClientRect())} onPointerLeave={() => setHover(null)}>
        <Grid width={width} ticks={ticks} y={y} decimals={digits(step)} />
        <g className="axes">
          {timeTicks(start, end, plotW).map(t => (
            <text key={t.at} x={x(t.at)} y={height - 6} textAnchor="middle">{t.label}</text>
          ))}
        </g>
        <path d={bandPath} className="band" />
        {recorded.length ? recorded.map(r => (
          <circle key={`${r.at}-${r.value}`} cx={x(r.n)} cy={y(r.value)} r={recorded.length > 400 ? 1.6 : 2.4} className="value-dot" />
        )) : points.map((p, i) => (
          <circle key={i} cx={x(days[i])} cy={y(p.value)} r={points.length > 400 ? 1.6 : 2.4}
                  className="value-dot" />
        ))}
        {points.filter(p => far.has(p.day)).map((p, i) => (
          <circle key={`far-${i}`} cx={x(ordinal(p.day))} cy={y(p.value)} r={5} className="far-ring" />
        ))}
        <path d={meanPath} className="line s1" />
        {h && (
          <>
            <line className="crosshair" x1={x(days[hover!])} x2={x(days[hover!])} y1={PAD.top} y2={PAD.top + plotH} />
            <circle cx={x(days[hover!])} cy={y(h.value)} r={4} className="dot s1" />
          </>
        )}
      </svg>
      <Tooltip tip={h ? {
        x: Math.min(x(days[hover!]) + 12, width - 200), y: 8, title: fullDay(h.day),
        // a value of the sessions has no daily mean: the sessions of the day follow, each at its time
        rows: [...(data.per === 'session' ? [] : [{ key: `${unit} daily mean`.trim(), value: h.value.toFixed(shown), kind: 'none' }]),
               { key: 'rolling mean', value: h.mean.toFixed(shown), kind: 's1' },
               ...(h.std === null ? [] : [{ key: 'standard deviation', value: `±${h.std.toFixed(shown)}`, kind: 'none' }]),
               ...recorded.filter(r => r.day === h.day).map(r => ({ key: `at ${clockOf(r.at)}`, value: r.value.toFixed(shown),
                                                                   kind: 'none' }))],
      } : null} />
    </div>
  )
}

function Numbers({ data, unit, decimals }: Draw) {
  const s = data.stats
  const span = ordinal(data.end) - ordinal(data.start) + 1
  const trend = s.trend_per_month
  const rows: [string, string, string][] = [
    data.per === 'session' ? ['Sessions (n)', String(s.count), `in ${span} days`]
      : ['Days with data (n)', String(s.count), `of ${span} days`],
    ['Mean', s.mean.toFixed(decimals + 1), unit],
    ['Median', s.median.toFixed(decimals + 1), unit],
    ['Standard deviation', s.std === null ? '—' : s.std.toFixed(decimals + 1), unit],
    ['Lowest', s.min.value.toFixed(decimals), fullDay(s.min.day)],
    ['Highest', s.max.value.toFixed(decimals), fullDay(s.max.day)],
    ['Linear trend per month', trend === null ? '—' : signed(trend, decimals + 2), unit ? `${unit}, least squares` : 'least squares'],
    ['Trend over the period', trend === null ? '—' : signed((trend * span) / 30.4375, decimals + 1), unit],
  ]
  return (
    <dl className="health-values analysis-numbers">
      {rows.map(([label, value, note]) => (
        <div key={label}><dt>{label}</dt><dd>{value}</dd><dd className="muted">{note}</dd></div>
      ))}
    </dl>
  )
}

// a line at a value; with a label, the value is written at its top (the newest day: its colour alone is too light)
type Mark = { name: string; value: number; className: string; label?: string }
type LegendItem = { label: string; kind: string; line?: boolean }

// a histogram with lines at given values (mean and median; zero and the 90th percentile of the change)
function Bars({ histogram, marks, legend, unit, decimals, label, what }: {
  histogram: { edges: number[]; counts: number[] }; marks: Mark[]; legend: LegendItem[]; unit: string; decimals: number
  label: string; what: [string, string]
}) {
  const [ref, width] = useWidth()
  const [hover, setHover] = useState<number | null>(null)
  const height = 240
  const { edges, counts } = histogram
  // all values the same: one bar around that value
  const same = edges[0] === edges[edges.length - 1]
  const lo = same ? edges[0] - 0.5 : edges[0]
  const hi = same ? edges[0] + 0.5 : edges[edges.length - 1]
  const plotW = width - PAD.left - PAD.right
  const plotH = height - PAD.top - PAD.bottom
  const { max, ticks } = scale(0, Math.max(...counts))
  const x = (v: number) => PAD.left + (plotW * (v - lo)) / (hi - lo)
  const y = (v: number) => PAD.top + plotH * (1 - v / max)
  const shown = Math.max(decimals, edges.length > 1 ? digits(edges[1] - edges[0]) : 0)
  const range = (i: number) => (same ? edges[0].toFixed(shown) : `${edges[i].toFixed(shown)} to ${edges[i + 1].toFixed(shown)}`)
  const every = Math.max(1, Math.ceil(56 / (plotW / Math.max(1, edges.length - 1))))
  return (
    <div className="chart" ref={ref}>
      <Legend items={legend} />
      <svg width={width} height={height} role="img" aria-label={`${label}, ${unit}`} onPointerLeave={() => setHover(null)}>
        <Grid width={width} ticks={ticks} y={y} decimals={0} />
        <g className="axes">
          {edges.map((e, i) => i % every === 0 && (
            <text key={e} x={x(e)} y={height - 6} textAnchor="middle">{e.toFixed(shown)}</text>
          ))}
        </g>
        {counts.map((c, i) => {
          const left = same ? x(lo) : x(edges[i])
          const right = same ? x(hi) : x(edges[i + 1])
          return (
            <g key={i} className={hover === i ? 'hovered' : ''} onPointerEnter={() => setHover(i)}>
              <rect className="hit" x={left} y={PAD.top} width={Math.max(1, right - left)} height={plotH} />
              {c > 0 && (
                <rect className="bar s1" x={left + 0.5} y={y(c)} width={Math.max(1, right - left - 1)} height={y(0) - y(c)} rx={2} />
              )}
            </g>
          )
        })}
        {marks.filter(m => m.value >= lo && m.value <= hi).map(m => (
          <line key={m.name} className={`mark-line ${m.className}`} x1={x(m.value)} x2={x(m.value)} y1={PAD.top}
                y2={PAD.top + plotH} />
        ))}
        {/* above the plot, where no other line runs: the mean and the median are often right beside it */}
        {marks.filter(m => m.label && m.value >= lo && m.value <= hi).map(m => (
          <text key={`${m.name}-label`} className="mark-label" y={PAD.top - 3} textAnchor="middle"
                x={Math.min(Math.max(x(m.value), PAD.left + 14), width - PAD.right - 14)}>{m.label}</text>
        ))}
      </svg>
      <Tooltip tip={hover === null ? null : {
        x: Math.min(x(edges[Math.min(hover + 1, edges.length - 1)]) + 8, width - 180), y: 8, title: `${range(hover)} ${unit}`.trim(),
        rows: [{ key: counts[hover] === 1 ? what[0] : what[1], value: String(counts[hover]), kind: 's1' }],
      }} />
    </div>
  )
}

function Spread({ data, unit, decimals }: Draw) {
  const last = newest(data.points)
  return (
    <Bars histogram={data.histogram} unit={unit} decimals={decimals} label="Histogram of the day values" what={['day', 'days']}
          marks={[{ name: 'mean', value: data.stats.mean, className: 'mean' },
                  { name: 'median', value: data.stats.median, className: 'median' },
                  { name: 'newest', value: last.value, className: 'latest', label: last.value.toFixed(decimals) }]}
          legend={[{ label: 'Days per bin', kind: 's1' }, { label: 'Mean', kind: 's2', line: true },
                   { label: 'Median', kind: 'median', line: true },
                   { label: `Newest day, ${dayMonth(last.day)}`, kind: 'latest', line: true }]} />
  )
}

// the normal day-to-day variability: a change smaller than its 90th percentile is noise
function Steps({ data, unit, decimals }: Draw) {
  const steps = data.steps
  if (!steps) return <p className="stats-empty">No consecutive days with data in this period.</p>
  const size = (v: number) => `${v.toFixed(decimals + 1)} ${unit}`.trim()
  // the newest change: only between the two newest days when they follow each other, as the histogram counts only those
  const [before, last] = data.points.slice(-2)
  const change = before && last && ordinal(last.day) - ordinal(before.day) === 1 ? last.value - before.value : null
  const newestMark = change === null ? []
    : [{ name: 'newest', value: change, className: 'latest', label: signed(change, decimals + 1) }]
  const newestKey = change === null ? [] : [{ label: `Newest change, ${dayMonth(before.day)} to ${dayMonth(last.day)}`, kind: 'latest',
                                              line: true }]
  return (
    <>
      <p className="analysis-say">
        Median absolute change <strong>{size(steps.median_size)}</strong>, 90th percentile <strong>{size(steps.p90_size)}</strong>.
        A day-to-day change below the 90th percentile is within normal variability.
        <span className="muted"> n = {steps.count} pairs of consecutive days.</span>
      </p>
      <Bars histogram={steps.histogram} unit={unit} decimals={decimals} label="Histogram of the change from one day to the next"
            what={['change', 'changes']}
            marks={[{ name: 'zero', value: 0, className: 'median' }, { name: 'below', value: -steps.p90_size, className: 'limit' },
                    { name: 'above', value: steps.p90_size, className: 'limit' }, ...newestMark]}
            legend={[{ label: 'Changes per bin', kind: 's1' }, { label: 'Zero', kind: 'median', line: true },
                     { label: '±90th percentile', kind: 'limit', line: true }, ...newestKey]} />
    </>
  )
}

const shortDate = (day: string) =>
  new Date(`${day}T12:00:00`).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })
const AGO: Record<number, string> = { 0: 'Latest 7 days', 30: '30 days earlier', 90: '90 days earlier', 365: '365 days earlier' }

function Earlier({ data, unit, decimals }: Draw) {
  return (
    <table className="analysis-earlier">
      <thead>
        <tr><th scope="col">Period</th><th scope="col" className="num">Mean{unit && ` (${unit})`}</th>
          <th scope="col" className="num">Difference</th></tr>
      </thead>
      <tbody>
        {data.earlier.map(w => (
          <tr key={w.ago}>
            <th scope="row">{AGO[w.ago] ?? `${w.ago} days earlier`}
              <small className="muted">
                {shortDate(w.from)} to {shortDate(w.to)}, {w.count} {w.count === 1 ? 'day' : 'days'}
              </small></th>
            <td className="num">{w.mean === null ? 'no values' : w.mean.toFixed(decimals + 1)}</td>
            <td className="num">{w.ago === 0 ? '' : w.diff === null ? '—' : signed(w.diff, decimals + 1)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

export type Axis = { label: string; unit: string; decimals: number }
type Related = Omit<HealthRelation, 'by_lag'>

// a correlation in words: its strength and sign, r, n and the regression slope
export function link(found: Related, y: Axis, x: Axis): string {
  if (found.r === null) return found.n < 3 ? `Too few days with both values (n = ${found.n}).` : 'One of the two values is constant.'
  const size = Math.abs(found.r)
  const strength = size < 0.1 ? 'No' : size < 0.3 ? 'Weak' : size < 0.5 ? 'Moderate' : 'Strong'
  const sign = size < 0.1 ? '' : found.r > 0 ? ' positive' : ' negative'
  // a small slope (per mg of a supplement) keeps 2 significant digits, not -0.00
  const per = found.slope !== null && Math.abs(found.slope) < 10 ** -(y.decimals + 1)
    ? `${found.slope > 0 ? '+' : '−'}${Math.abs(found.slope).toPrecision(2)}` : signed(found.slope ?? 0, y.decimals + 2)
  const slope = found.slope === null || size < 0.1 ? ''
    : ` Slope ${per} ${y.unit} per ${x.unit || 'unit'} of ${inText(x.label)}.`
  const delay = found.lag === 0 ? ''
    : ` Delay ${found.lag} ${found.lag === 1 ? 'day' : 'days'}: ${inText(x.label)} on day D − ${found.lag} against`
      + ` ${inText(y.label)} on day D.`
  return `${strength}${sign} correlation (r = ${found.r.toFixed(2)}, n = ${found.n} days).${slope}${delay}`
    + ' Correlation does not imply causation.'
}

export function Scatter({ found, x: xAxis, y: yAxis, height = 300 }: { found: Related; x: Axis; y: Axis; height?: number }) {
  const [ref, width] = useWidth()
  const [hover, setHover] = useState<number | null>(null)
  const { points } = found
  const plotW = width - PAD.left - PAD.right
  const plotH = height - PAD.top - PAD.bottom
  const sx = scale(Math.min(...points.map(p => p.x)), Math.max(...points.map(p => p.x)))
  const sy = scale(Math.min(...points.map(p => p.y)), Math.max(...points.map(p => p.y)))
  const x = (v: number) => PAD.left + (plotW * (v - sx.min)) / (sx.max - sx.min)
  const y = (v: number) => PAD.top + plotH * (1 - (v - sy.min) / (sy.max - sy.min))
  // the straight line only where it stays inside the chart
  const line = found.slope === null || found.intercept === null ? null : (() => {
    const { slope, intercept } = found as { slope: number; intercept: number }
    if (slope === 0) return [sx.min, sx.max]
    const cross = [sy.min, sy.max].map(v => (v - intercept) / slope)
    return [Math.max(sx.min, Math.min(...cross)), Math.min(sx.max, Math.max(...cross))]
  })()
  const pick = (clientX: number, clientY: number, box: DOMRect) => {
    let best = 0
    let near = Infinity
    points.forEach((p, i) => {
      const d = (x(p.x) - (clientX - box.left)) ** 2 + (y(p.y) - (clientY - box.top)) ** 2
      if (d < near) { near = d; best = i }
    })
    setHover(near < 900 ? best : null)
  }
  const h = hover === null ? null : points[hover]
  const last = newest(points)
  return (
    <div className="chart" ref={ref}>
      <Legend items={[{ label: 'Days', kind: 'dots' }, { label: `Newest day, ${dayMonth(last.day)}`, kind: 'latest round' },
                      ...(line ? [{ label: 'Least-squares line', kind: 's2', line: true }] : [])]} />
      <p className="analysis-axis">{yAxis.label}{yAxis.unit && `, ${yAxis.unit}`}</p>
      <svg width={width} height={height} role="img" aria-label={`${yAxis.label} against ${xAxis.label}, by day`}
           onPointerMove={e => pick(e.clientX, e.clientY, e.currentTarget.getBoundingClientRect())}
           onPointerLeave={() => setHover(null)}>
        <Grid width={width} ticks={sy.ticks} y={y} decimals={digits(sy.step)} />
        <g className="axes">
          {sx.ticks.map(t => <text key={t} x={x(t)} y={height - 6} textAnchor="middle">{t.toFixed(digits(sx.step))}</text>)}
        </g>
        {points.map(p => <circle key={p.day} cx={x(p.x)} cy={y(p.y)} r={points.length > 300 ? 2.2 : 3.2} className="value-dot" />)}
        {line && found.slope !== null && found.intercept !== null && (
          <line className="line s2" x1={x(line[0])} x2={x(line[1])} y1={y(found.intercept + found.slope * line[0])}
                y2={y(found.intercept + found.slope * line[1])} />
        )}
        {/* the newest day on top of the others and the line: its own colour, full and larger */}
        <circle cx={x(last.x)} cy={y(last.y)} r={5} className="dot latest" />
        {h && <circle cx={x(h.x)} cy={y(h.y)} r={5} className="dot s1" />}
      </svg>
      <p className="analysis-axis x">{xAxis.label}{xAxis.unit && `, ${xAxis.unit}`}</p>
      <Tooltip tip={h ? {
        x: Math.min(x(h.x) + 12, width - 220), y: Math.max(8, y(h.y) - 60), title: fullDay(h.day),
        rows: [{ key: `${yAxis.unit} ${inText(yAxis.label)}`.trim(), value: h.y.toFixed(yAxis.decimals), kind: 'none' },
               { key: `${xAxis.unit} ${inText(xAxis.label)}${h.x_day === h.day ? '' : `, ${shortDate(h.x_day)}`}`.trim(),
                 value: h.x.toFixed(xAxis.decimals), kind: 'none' }],
      } : null} />
    </div>
  )
}

const RELATION_KEY = 'health-relation-other'
const MAX_LAG = 30
// a delay with fewer day pairs is shown grey: its r says little
const MIN_PAIRS = 10

// r for every delay from 0 to 30 days, one bar each; a click chooses that delay for the scatter plot
function LagChart({ byLag, lag, onPick }: { byLag: HealthRelation['by_lag']; lag: number; onPick: (lag: number) => void }) {
  const [ref, width] = useWidth()
  const [hover, setHover] = useState<number | null>(null)
  const height = 170
  const plotW = width - PAD.left - PAD.right
  const plotH = height - PAD.top - PAD.bottom
  const band = plotW / byLag.length
  const bar = Math.max(2, Math.min(16, band - 3))
  const y = (v: number) => PAD.top + plotH * (1 - (v + 1) / 2)
  const enough = byLag.filter(d => d.r !== null && d.n >= MIN_PAIRS)
  const strongest = enough.length ? enough.reduce((a, b) => (Math.abs(b.r!) > Math.abs(a.r!) ? b : a)).lag : null
  const h = hover === null ? null : byLag[hover]
  return (
    <div className="chart" ref={ref}>
      <svg width={width} height={height} role="img" aria-label="Correlation r per delay in days" onPointerLeave={() => setHover(null)}>
        <Grid width={width} ticks={[-1, 0, 1]} y={y} decimals={0} />
        <g className="axes">
          {byLag.map((d, i) => d.lag % 5 === 0 && (
            <text key={d.lag} x={PAD.left + band * (i + 0.5)} y={height - 6} textAnchor="middle">{d.lag}</text>
          ))}
        </g>
        {byLag.map((d, i) => {
          const left = PAD.left + band * i + (band - bar) / 2
          const top = d.r === null ? y(0) : Math.min(y(0), y(d.r))
          const size = d.r === null ? 0 : Math.abs(y(d.r) - y(0))
          const kind = d.n < MIN_PAIRS ? 'few' : (d.r ?? 0) >= 0 ? 's1' : 's2'
          return (
            <g key={d.lag} className={`lag${hover === i ? ' hovered' : ''}`}
               onPointerEnter={() => setHover(i)} onClick={() => onPick(d.lag)}>
              <rect className="hit" x={PAD.left + band * i} y={PAD.top} width={band} height={plotH} />
              {d.r !== null && <rect className={`bar ${kind}`} x={left} y={top} width={bar} height={Math.max(1, size)} rx={2} />}
              {d.lag === lag && (
                <rect className="lag-chosen" x={PAD.left + band * i + 1} y={PAD.top} width={band - 2} height={plotH} />
              )}
              {d.lag === strongest && (
                <text className="end-label" x={left + bar / 2} y={(d.r ?? 0) >= 0 ? top - 4 : top + size + 12} textAnchor="middle">
                  strongest
                </text>
              )}
            </g>
          )
        })}
      </svg>
      <p className="analysis-axis x">delay in days (the second value that many days earlier)</p>
      <Tooltip tip={h ? {
        x: Math.min(PAD.left + band * (hover! + 1) + 8, width - 200), y: 8, title: `Delay ${h.lag} ${h.lag === 1 ? 'day' : 'days'}`,
        rows: [{ key: 'r', value: h.r === null ? '—' : h.r.toFixed(2), kind: 'none' },
               { key: 'n, day pairs', value: String(h.n), kind: 'none' }],
      } : null} />
    </div>
  )
}

// values of the history that are not in RELATION_CHOICES: the amount of each supplement taken
export const MoreChoices = createContext<Value[]>([])

// the value of the chart against a second value chosen here, by day, over the period of the view
function Relation({ valueKey, name, spec, days }: { valueKey: string; name: string; spec: AnalysisSpec; days: number | null }) {
  const choices = [...RELATION_CHOICES, ...useContext(MoreChoices)].filter(([k]) => k !== valueKey)
  const [other, setOther] = useState(() => {
    let saved = ''
    try { saved = localStorage.getItem(RELATION_KEY) ?? '' } catch { /* no storage: the default value */ }
    return choices.some(([k]) => k === saved) ? saved : valueKey === 'systolic' ? 'weight' : 'systolic'
  })
  const choose = (next: string) => {
    setOther(next)
    try { localStorage.setItem(RELATION_KEY, next) } catch { /* no storage: the choice lasts this view */ }
  }
  const [lag, setLag] = useState(0)
  const found = useQuery({
    queryKey: ['health-relation', valueKey, other, days, lag, spec.activity ?? null],
    queryFn: () => getHealthRelation(valueKey, other, days, lag, spec.activity ?? null),
    placeholderData: previous => previous,
  })
  const [, label, unit, decimals] = choices.find(([k]) => k === other) ?? choices[0]
  const xAxis = { label, unit, decimals }
  const yAxis = { label: name, unit: spec.unit, decimals: spec.decimals }
  return (
    <>
      <div className="analysis-against-row">
        <label className="analysis-against">Compare with
          <select value={other} onChange={e => choose(e.target.value)}>
            {choices.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
          </select>
        </label>
        <label className="analysis-against">Delay
          <select value={lag} onChange={e => setLag(Number(e.target.value))}>
            {Array.from({ length: MAX_LAG + 1 }, (_, n) => (
              <option key={n} value={n}>{n === 0 ? 'none, same day' : `${n} ${n === 1 ? 'day' : 'days'} earlier`}</option>
            ))}
          </select>
        </label>
      </div>
      {found.isError && <p className="cap-error">{found.error.message}</p>}
      {found.data?.by_lag.some(d => d.r !== null) && (
        <>
          <LagChart byLag={found.data.by_lag} lag={lag} onPick={setLag} />
          <p className="analysis-say muted">
            31 delays are tested, so one strong r can be chance. Trust a delay only with enough day pairs (n) and a plausible
            cause. Grey: fewer than {MIN_PAIRS} day pairs.
          </p>
        </>
      )}
      {found.data && <p className="analysis-say">{link(found.data, yAxis, xAxis)}</p>}
      {found.data && found.data.n >= 3 && <Scatter found={found.data} x={xAxis} y={yAxis} />}
    </>
  )
}

function Years({ data, unit, decimals }: Draw) {
  const [ref, width] = useWidth()
  const [hover, setHover] = useState<number | null>(null)
  const height = 260
  const { years } = data
  const plotW = width - PAD.left - PAD.right
  const plotH = height - PAD.top - PAD.bottom
  const values = years.flatMap(y => y.months).filter((v): v is number => v !== null)
  const { min, max, ticks, step } = scale(Math.min(...values), Math.max(...values))
  const x = (m: number) => PAD.left + (plotW * (m + 0.5)) / 12
  const y = (v: number) => PAD.top + plotH * (1 - (v - min) / (max - min))
  // the last year and the one before stand out; the older years stay grey
  const kind = (i: number) => (i === years.length - 1 ? 's1' : i === years.length - 2 ? 's2' : 'other')
  const path = (months: (number | null)[]) => months.reduce<string>((d, v, m) => (
    v === null ? d : `${d}${m > 0 && months[m - 1] !== null ? 'L' : 'M'}${x(m).toFixed(1)},${y(v).toFixed(1)}`), '')
  const order = years.map((_, i) => i).sort((a, b) => (kind(a) === 'other' ? 0 : 1) - (kind(b) === 'other' ? 0 : 1))
  const shown = Math.max(decimals, digits(step))
  return (
    <div className="chart" ref={ref}>
      <Legend items={[
        ...years.slice(-2).reverse().map((yr, i) => ({ label: String(yr.year), kind: i === 0 ? 's1' : 's2', line: true })),
        ...(years.length > 2 ? [{ label: 'Older years', kind: 'other', line: true }] : []),
      ]} />
      <svg width={width} height={height} role="img" aria-label={`Each year by month, ${unit}`} onPointerLeave={() => setHover(null)}
           onPointerMove={e => {
             const box = e.currentTarget.getBoundingClientRect()
             setHover(Math.min(11, Math.max(0, Math.floor(((e.clientX - box.left - PAD.left) / plotW) * 12))))
           }}>
        <Grid width={width} ticks={ticks} y={y} decimals={digits(step)} />
        <g className="axes">
          {MONTHS.map((name, m) => <text key={name} x={x(m)} y={height - 6} textAnchor="middle">{name}</text>)}
        </g>
        {hover !== null && <line className="crosshair" x1={x(hover)} x2={x(hover)} y1={PAD.top} y2={PAD.top + plotH} />}
        {order.map(i => {
          const yr = years[i]
          const lone = yr.months.map((v, m) => ({ v, m })).filter(({ v, m }) => v !== null
            && (m === 0 || yr.months[m - 1] === null) && (m === 11 || yr.months[m + 1] === null))
          return (
            <g key={yr.year}>
              <path d={path(yr.months)} className={`line ${kind(i)}`} />
              {lone.map(({ v, m }) => <circle key={m} cx={x(m)} cy={y(v!)} r={2.5} className={`dot ${kind(i)}`} />)}
            </g>
          )
        })}
      </svg>
      <Tooltip tip={hover === null ? null : {
        x: Math.min(x(hover) + 12, width - 180), y: 8, title: MONTHS[hover],
        rows: years.map((yr, i) => ({ yr, i })).reverse().filter(({ yr }) => yr.months[hover] !== null)
          .map(({ yr, i }) => ({ key: String(yr.year), value: `${yr.months[hover]!.toFixed(shown)} ${unit}`.trim(), kind: kind(i) })),
      }} />
    </div>
  )
}

function Outliers({ data, unit, decimals }: Draw) {
  const [open, setOpen] = useState<string | null>(null)
  // a value of the Polar H10 sessions comes from sessions, not from measurements
  const sessions = data.key.startsWith('h10:')
  const what = sessions ? 'sessions' : 'measurements'
  if (data.outliers.length === 0) return <p className="muted">No outliers in this period.</p>
  return (
    <ul className="analysis-outliers">
      {data.outliers.map((o, i) => (
        <li key={`${o.day}-${i}`}>
          <div className="analysis-outlier">
            <span className="health-measure-time">{fullDay(o.day)}</span>
            <span><strong>{o.value.toFixed(decimals)} {unit}</strong>{' '}
              <span className="muted">
                expected {o.expected.toFixed(decimals)} ({signed(o.value - o.expected, decimals)}), z = {o.z.toFixed(1)}
              </span></span>
            <button type="button" className="quiet" aria-expanded={open === o.day}
                    onClick={() => setOpen(open === o.day ? null : o.day)}>
              {open === o.day ? `Hide ${what}` : `Show ${what}`}
            </button>
          </div>
          {open === o.day && (sessions ? <DaySessions day={o.day} /> : <DayMeasurements day={o.day} />)}
        </li>
      ))}
    </ul>
  )
}

function readView(): { days: number | null; window: number } {
  try {
    const saved = JSON.parse(localStorage.getItem(VIEW_KEY) ?? '')
    if (PERIODS.some(([d]) => d === saved.days) && WINDOWS.includes(saved.window)) return saved
  } catch { /* nothing saved or no storage: the default view */ }
  return { days: 365, window: 30 }
}

function AnalysisDialog({ spec, onClose }: { spec: AnalysisSpec; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null)
  useEffect(() => { dialog.current?.showModal() }, [])
  const [key, setKey] = useState(spec.lines[0].key)
  const [view, setView] = useState(readView)
  const choose = (next: { days: number | null; window: number }) => {
    setView(next)
    try { localStorage.setItem(VIEW_KEY, JSON.stringify(next)) } catch { /* no storage: the choice lasts this view */ }
  }
  const found = useQuery({
    queryKey: ['health-analysis', key, view.days, view.window, spec.activity ?? null],
    queryFn: () => getHealthAnalysis(key, view.days, view.window, spec.activity ?? null),
    placeholderData: previous => previous,
  })
  const line = spec.lines.find(l => l.key === key) ?? spec.lines[0]
  const name = line.name ?? (spec.lines.length > 1 ? `${spec.title} (${inText(line.label)})` : spec.title)
  const data = found.data?.stats && found.data.histogram ? found.data as Data : null
  const draw = data && { data, unit: spec.unit, decimals: spec.decimals }
  return createPortal(
    <dialog ref={dialog} className="health-analysis accent-health" onClose={onClose} aria-labelledby="analysis-title"
            {...closeOutside}>
      <div className="analysis-head">
        <h2 id="analysis-title">{name}
          {spec.unit && <span className="muted">{spec.unit}</span>}</h2>
        <button type="button" className="quiet" onClick={() => dialog.current?.close()}>Close</button>
      </div>
      <div className="analysis-controls">
        {spec.lines.length > 1 && (
          <div className="research-switch" role="group" aria-label="Value">
            {spec.lines.map(l => (
              <button key={l.key} type="button" aria-pressed={key === l.key} onClick={() => setKey(l.key)}>{l.label}</button>
            ))}
          </div>
        )}
        <div className="research-switch" role="group" aria-label="Period">
          {PERIODS.map(([days, label]) => (
            <button key={label} type="button" aria-pressed={view.days === days}
                    onClick={() => choose({ ...view, days })}>{label}</button>
          ))}
        </div>
        <div className="research-switch" role="group" aria-label="Rolling mean window">
          <span className="switch-label">Rolling mean</span>
          {WINDOWS.map(n => (
            <button key={n} type="button" aria-pressed={view.window === n}
                    onClick={() => choose({ ...view, window: n })}>{n} days</button>
          ))}
        </div>
        {found.isFetching && <span className="muted">Calculating…</span>}
      </div>
      {found.isError && <p className="cap-error">{found.error.message}</p>}
      {found.isPending && <p className="muted">Calculating…</p>}
      {found.data && !draw && <p className="stats-empty">No values in this period. Choose a longer period.</p>}
      {draw && (
        <div className="analysis-grid">
          <section className="analysis-card analysis-wide">
            <h3>{data.per === 'session' ? 'Sessions' : 'Daily means'} and rolling mean <span className="muted">band: ±1 standard
              deviation</span></h3>
            <TrendChart {...draw} />
          </section>
          <section className="analysis-card">
            <h3>Numbers <span className="muted">of the period</span></h3>
            <Numbers {...draw} />
          </section>
          <section className="analysis-card">
            <h3>Comparison with earlier periods <span className="muted">7-day means; uses all data, also outside the chosen
              period</span></h3>
            <Earlier {...draw} />
          </section>
          <section className="analysis-card">
            <h3>Distribution <span className="muted">histogram of the {data.per === 'session' ? 'sessions' : 'daily means'}</span></h3>
            <Spread {...draw} />
          </section>
          {/* a value of the sessions has no value of a day: no change from day to day and no correlation by day */}
          {data.per === 'day' && (
            <section className="analysis-card">
              <h3>Day-to-day variability <span className="muted">change between consecutive days</span></h3>
              <Steps {...draw} />
            </section>
          )}
          <section className="analysis-card">
            <h3>Year over year <span className="muted">monthly means</span></h3>
            <Years {...draw} />
          </section>
          {data.per === 'day' && (
            <section className="analysis-card">
              <h3>
                Correlation <span className="muted">daily means of the chosen period, the second value up to 30 days earlier</span>
              </h3>
              <Relation valueKey={key} name={name} spec={spec} days={view.days} />
            </section>
          )}
          {!DAILY.includes(key) && !key.startsWith('sleep:') && !key.startsWith('supp:') && !key.startsWith('fitbit:') && (
            <section className="analysis-card analysis-wide">
              <h3>Outliers</h3>
              <p className="muted">
                Robust z-score above 3.5 against the median and the median absolute deviation of the
                surrounding {data.window} days, largest first. Open a day to remove a faulty measurement; removed measurements are
                listed under the table, with Put back.
              </p>
              <Outliers {...draw} />
            </section>
          )}
        </div>
      )}
    </dialog>,
    document.body,
  )
}

// the button next to a chart's title; the large view opens over the page and Esc or Close shuts it
export function EnlargeButton({ spec }: { spec: AnalysisSpec }) {
  const [open, setOpen] = useState(false)
  return (
    <>
      <button type="button" className="quiet health-enlarge" aria-label={`Enlarge ${spec.title} with its analysis`}
              onClick={() => setOpen(true)}>Enlarge</button>
      {open && <AnalysisDialog spec={spec} onClose={() => setOpen(false)} />}
    </>
  )
}
