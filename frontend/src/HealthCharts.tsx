import { useState } from 'react'

import type { HealthDayRow, RecordingPoint } from './api'
import { inText } from './healthValues'
import { Legend, Tooltip, useWidth } from './charts'

// the Health history as small charts on one date axis, one under the other: lines for blood pressure, weight and
// fat % (each its own scale, never two in one chart), bars from zero for the angina score and the supplements taken.
// Every chart has a crosshair or a bar tooltip; the table under the charts holds the same numbers.

const PAD = { left: 40, right: 48, top: 12, bottom: 24 }
const HEIGHT = 150

const shortDay = (day: string) => new Date(`${day}T12:00:00`).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })
const longDay = (day: string) =>
  new Date(`${day}T12:00:00`).toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short' })

// label: the legend in a chart of two; name: the full name in the large view ('Systolic blood pressure'); dots: each value
// as a dot, without a line (the HRV tests under their 7-day mean)
export type Line = { key: string; label: string; kind: 's1' | 's2'; name?: string; dots?: boolean }
export const clockOf = (iso: string) => new Date(iso).toTimeString().slice(0, 5)
// the place of a time in the days of a chart: the index of its day plus the part of the day passed
export const dayPart = (iso: string) => {
  const at = new Date(iso)
  return (at.getHours() * 3600 + at.getMinutes() * 60 + at.getSeconds()) / 86400
}
// a value of a day by its name; a day without it (no measurement) gives null
const read = (row: HealthDayRow, key: string) => {
  const v = row[key]
  return typeof v === 'number' ? v : null
}

function DayAxis({ width, days, height = HEIGHT }: { width: number; days: string[]; height?: number }) {
  const plotW = width - PAD.left - PAD.right
  const every = Math.max(1, Math.ceil(days.length / Math.floor(plotW / 64)))
  return (
    <g className="axes">
      {days.map((day, i) => i % every === 0 && (
        <text key={day} x={PAD.left + (plotW * (i + 0.5)) / days.length} y={height - 6} textAnchor="middle">{shortDay(day)}</text>
      ))}
    </g>
  )
}

function Grid({ width, ticks, y, decimals, label }: {
  width: number; ticks: number[]; y: (v: number) => number; decimals: number; label?: (tick: number) => string
}) {
  return (
    <g className="axes">
      {ticks.map(t => (
        <g key={t}>
          <line x1={PAD.left} x2={width - PAD.right} y1={y(t)} y2={y(t)} className="grid" />
          <text x={PAD.left - 6} y={y(t) + 4} textAnchor="end">{label ? label(t) : t.toFixed(decimals)}</text>
        </g>
      ))}
    </g>
  )
}

// a line chart's scale fits its data with some room: weight from 81 to 85 kg, not from 0. The room never takes the scale
// below 0 when no value is below 0 (a time, a count), nor a % above 100 when no value is above 100 (a share)
function span(values: number[], decimals: number, unit: string): [number, number, number[]] {
  const lo = Math.min(...values)
  const hi = Math.max(...values)
  const room = Math.max((hi - lo) * 0.2, decimals ? 0.5 : 3)
  const f = 10 ** decimals
  const min = Math.max(Math.floor((lo - room) * f) / f, lo >= 0 ? 0 : -Infinity)
  const max = Math.min(Math.ceil((hi + room) * f) / f, unit === '%' && hi <= 100 ? 100 : Infinity)
  return [min, max, [min, Math.round(((min + max) / 2) * f) / f, max]]
}

// A line whose value the chart's `points` have (each recording at its time) draws those, each at its clock time inside the
// slot of its day, so several recordings of one day show; a line without them (a value of the day, a 7-day mean) draws the
// values of the days, at noon
export function LineChart({ rows, lines, unit, decimals, label, onPick, connect = false, band, points = [] }: {
  rows: HealthDayRow[]; lines: Line[]; unit: string; decimals: number; label: string; onPick?: (day: string) => void
  connect?: boolean; band?: { low: string; high: string; label: string }; points?: RecordingPoint[]
}) {
  const [ref, width] = useWidth()
  const [hover, setHover] = useState<number | null>(null)
  // the recordings of each line that has them, in time order: the slot of their day (i) and their place (f)
  const slot = new Map(rows.map((r, i) => [r.day, i]))
  const timed: Record<string, { i: number; f: number; v: number; at: string; time: number }[]> = Object.fromEntries(
    lines.map(l => [l.key, points.flatMap(p => {
      const i = slot.get(p.day)
      const v = p[l.key]
      return i === undefined || typeof v !== 'number' ? [] : [{ i, v, at: p.at, f: i + dayPart(p.at), time: Date.parse(p.at) }]
    }).sort((a, b) => a.time - b.time)]).filter(([, found]) => found.length > 0))
  const isTimed = (l: Line) => l.key in timed
  const values = [...lines.filter(l => !isTimed(l)).flatMap(l => rows.map(r => read(r, l.key))),
                  ...Object.values(timed).flat().map(t => t.v),
                  ...(band ? rows.flatMap(r => [read(r, band.low), read(r, band.high)]) : [])].filter((v): v is number => v !== null)
  if (values.length === 0) return <p className="stats-empty">No {inText(label)} in these days.</p>
  const [min, max, ticks] = span(values, decimals, unit)
  const plotW = width - PAD.left - PAD.right
  const plotH = HEIGHT - PAD.top - PAD.bottom
  const at = (f: number) => PAD.left + (plotW * f) / rows.length
  const x = (i: number) => at(i + 0.5)
  const y = (v: number) => PAD.top + plotH * (1 - (v - min) / (max - min))
  // a day without a value leaves a gap; a value between two gaps shows as a dot, else it would not show at all. With
  // `connect` (a weekly measurement) the line joins the values over the days between, and each value has its dot
  const path = (key: string) => {
    let started = false
    return rows.reduce((d, r, i) => {
      const v = read(r, key)
      if (v === null) return d
      const join = connect ? started : i > 0 && read(rows[i - 1], key) !== null
      started = true
      return `${d}${join ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`
    }, '')
  }
  const lone = (key: string, all: boolean) => rows.map((r, i) => ({ r, i })).filter(({ r, i }) => read(r, key) !== null && (all
    || ((i === 0 || read(rows[i - 1], key) === null) && (i === rows.length - 1 || read(rows[i + 1], key) === null))))
  // the band (a normal range): an area between two values of each day, broken where a day has none
  const runs = band ? rows.reduce<number[][]>((found, r, i) => {
    if (read(r, band.low) === null || read(r, band.high) === null) return found
    if (found.length && found[found.length - 1].at(-1) === i - 1) found[found.length - 1].push(i)
    else found.push([i])
    return found
  }, []) : []
  const area = (run: number[]) => `M${run.map(i => `${x(i).toFixed(1)},${y(read(rows[i], band!.high)!).toFixed(1)}`).join('L')}`
    + `L${[...run].reverse().map(i => `${x(i).toFixed(1)},${y(read(rows[i], band!.low)!).toFixed(1)}`).join('L')}Z`
  const pick = (clientX: number, box: DOMRect) => {
    const i = Math.round(((clientX - box.left - PAD.left) / plotW) * rows.length - 0.5)
    setHover(Math.min(rows.length - 1, Math.max(0, i)))
  }
  const h = hover === null ? null : rows[hover]
  return (
    <div className="chart" ref={ref}>
      {(lines.length > 1 || band) && (
        <Legend items={[...lines.map(l => ({ label: l.label, kind: l.dots ? `${l.kind} dot` : l.kind, line: !l.dots })),
                        ...(band ? [{ label: band.label, kind: 'band' }] : [])]} />
      )}
      <svg width={width} height={HEIGHT} role="img" aria-label={`${label}, ${unit}, per day`} className={onPick ? 'pickable' : ''}
           onPointerMove={e => pick(e.clientX, e.currentTarget.getBoundingClientRect())} onPointerLeave={() => setHover(null)}
           onClick={() => hover !== null && onPick?.(rows[hover].day)}>
        <Grid width={width} ticks={ticks} y={y} decimals={decimals} />
        <DayAxis width={width} days={rows.map(r => r.day)} />
        {runs.map(run => <path key={run[0]} d={area(run)} className="band" />)}
        {hover !== null && <line className="crosshair" x1={x(hover)} x2={x(hover)} y1={PAD.top} y2={PAD.top + plotH} />}
        {lines.map(l => {
          if (isTimed(l)) {
            const found = timed[l.key]
            // as the values of the days: the line breaks over a day without a recording, unless `connect`; a recording
            // that the line does not reach shows as a dot
            const joined = (n: number) => n > 0 && n < found.length && (connect || found[n].i - found[n - 1].i <= 1)
            const end = found[found.length - 1]
            return (
              <g key={l.key}>
                {!l.dots && (
                  <path d={found.map((t, n) => `${joined(n) ? 'L' : 'M'}${at(t.f).toFixed(1)},${y(t.v).toFixed(1)}`).join('')}
                        className={`line ${l.kind}`} />
                )}
                {found.filter((_, n) => l.dots || connect || (!joined(n) && !joined(n + 1))).map(t => (
                  <circle key={`${t.at}-${t.v}`} cx={at(t.f)} cy={y(t.v)} r={3} className={`dot ${l.kind}`} />))}
                {!l.dots && (
                  <>
                    <circle cx={at(end.f)} cy={y(end.v)} r={4} className={`dot ${l.kind}`} />
                    <text x={at(end.f) + 8} y={y(end.v) + 4} className="end-label">{end.v.toFixed(decimals)}</text>
                  </>
                )}
                {found.filter(t => t.i === hover).map(t => (
                  <circle key={`h-${t.at}-${t.v}`} cx={at(t.f)} cy={y(t.v)} r={4} className={`dot ${l.kind}`} />))}
              </g>
            )
          }
          const last = rows.map((r, i) => ({ r, i })).reverse().find(({ r }) => read(r, l.key) !== null)
          const now = h ? read(h, l.key) : null
          return (
            <g key={l.key}>
              {!l.dots && <path d={path(l.key)} className={`line ${l.kind}`} />}
              {lone(l.key, connect || Boolean(l.dots)).map(({ r, i }) => (
                <circle key={r.day} cx={x(i)} cy={y(read(r, l.key)!)} r={3} className={`dot ${l.kind}`} />))}
              {/* the end label of the dots would cover that of the line beside them */}
              {last && !l.dots && (
                <>
                  <circle cx={x(last.i)} cy={y(read(last.r, l.key)!)} r={4} className={`dot ${l.kind}`} />
                  <text x={x(last.i) + 8} y={y(read(last.r, l.key)!) + 4} className="end-label">
                    {read(last.r, l.key)!.toFixed(decimals)}
                  </text>
                </>
              )}
              {now !== null && <circle cx={x(hover!)} cy={y(now)} r={4} className={`dot ${l.kind}`} />}
            </g>
          )
        })}
      </svg>
      <Tooltip tip={h && lines.some(l => (isTimed(l) ? timed[l.key].some(t => t.i === hover) : read(h, l.key) !== null)) ? {
        x: Math.min(x(hover!) + 12, width - 160), y: 8, title: longDay(h.day),
        rows: [...lines.flatMap(l => {
          // a line of recordings: each recording of the day with its clock time
          const found = isTimed(l) ? timed[l.key].filter(t => t.i === hover) : []
          if (found.length) {
            return found.map(t => ({ key: `${inText(l.label)} at ${clockOf(t.at)} ${unit}`.trim(), value: t.v.toFixed(decimals),
                                     kind: l.kind }))
          }
          const v = isTimed(l) ? null : read(h, l.key)
          return [{ key: `${inText(l.label)} ${unit}`.trim(), value: v === null ? '—' : v.toFixed(decimals), kind: l.kind }]
        }), ...(band && read(h, band.low) !== null && read(h, band.high) !== null
          ? [{ key: band.label.toLowerCase(), kind: 'band',
               value: `${read(h, band.low)!.toFixed(decimals)}–${read(h, band.high)!.toFixed(decimals)}` }] : [])],
      } : null} />
    </div>
  )
}

// a tick label of its own: the Ketostix bars stand on the steps of the strip (0 to 5), the axis says their mmol/L.
// A marked day (angina not measured) has no bar but a grey dot above the baseline. `readings` (each Ketostix reading): a
// day with readings has a dot for each at its clock time inside the slot of the day, in place of its bar, and the tooltip
// lists them with their time
export function BarChart({ rows, value, max, label, format, ticks, onPick, tickLabel, marked, readings = [] }: {
  rows: HealthDayRow[]; value: (row: HealthDayRow) => number | null; max: number; label: string
  format: (row: HealthDayRow) => string; ticks: number[]; onPick?: (day: string) => void; tickLabel?: (tick: number) => string
  marked?: (row: HealthDayRow) => boolean; readings?: { at: string; day: string; value: number; text: string }[]
}) {
  const [ref, width] = useWidth()
  const [hover, setHover] = useState<number | null>(null)
  if (!rows.some(r => value(r) !== null || marked?.(r))) return <p className="stats-empty">No {inText(label)} in these days.</p>
  const plotW = width - PAD.left - PAD.right
  const plotH = HEIGHT - PAD.top - PAD.bottom
  const band = plotW / rows.length
  const bar = Math.max(2, Math.min(14, band - 2))
  const y = (v: number) => PAD.top + plotH * (1 - v / max)
  const base = PAD.top + plotH
  const h = hover === null ? null : rows[hover]
  const slot = new Map(rows.map((r, i) => [r.day, i]))
  const dots = readings.flatMap(p => {
    const i = slot.get(p.day)
    return i === undefined ? [] : [{ ...p, i, x: PAD.left + band * (i + dayPart(p.at)) }]
  })
  const dotted = new Set(dots.map(d => d.i))
  const listed = dots.filter(d => d.i === hover)
  return (
    <div className="chart" ref={ref}>
      <svg width={width} height={HEIGHT} role="img" aria-label={`${label} per day`} className={onPick ? 'pickable' : ''}
           onPointerLeave={() => setHover(null)} onClick={() => hover !== null && onPick?.(rows[hover].day)}>
        <Grid width={width} ticks={ticks} y={y} decimals={0} label={tickLabel} />
        <DayAxis width={width} days={rows.map(r => r.day)} />
        {rows.map((r, i) => {
          const v = dotted.has(i) ? null : value(r)
          const left = PAD.left + band * i + (band - bar) / 2
          const top = v === null ? base : y(v)
          const radius = Math.min(4, bar / 2, base - top)
          return (
            <g key={r.day} className={hover === i ? 'hovered' : ''} onPointerEnter={() => setHover(i)}>
              <rect className="hit" x={PAD.left + band * i} y={PAD.top} width={band} height={plotH} />
              {/* rounded at the data end, square at the baseline; a zero shows as a thin line on the baseline */}
              {v !== null && v > 0 && (
                <path className="bar s1" d={`M${left},${base}V${top + radius}q0,${-radius} ${radius},${-radius}`
                  + `h${bar - 2 * radius}q${radius},0 ${radius},${radius}V${base}Z`} />
              )}
              {v === 0 && <rect className="bar s1 zero" x={left} y={base - 2} width={bar} height={2} />}
              {v === null && marked?.(r) && <circle className="bar-mark" cx={left + bar / 2} cy={base - 5} r={Math.min(3, bar / 2)} />}
            </g>
          )
        })}
        {dots.map(d => (
          <circle key={`${d.at}-${d.value}`} cx={d.x} cy={y(d.value)} r={d.i === hover ? 4.5 : 3.5} className="dot s1"
                  pointerEvents="none" />
        ))}
      </svg>
      <Tooltip tip={h ? { x: Math.min(PAD.left + band * (hover! + 1) + 8, width - 160), y: 8, title: longDay(h.day),
                          rows: listed.length ? listed.map(d => ({ key: `at ${clockOf(d.at)}`, value: d.text, kind: 'none' }))
                            : [{ key: '', value: format(h), kind: 'none' }] } : null} />
    </div>
  )
}

// the sleep diary: each sleep as a bar from its bedtime to its wake time, on a clock axis from 00:00 at the bottom to 24:00
// at the top, so naps and split sleep show as they were. A sleep over midnight has a part in the column of each of its
// days; the tooltip of a day gives each of its sleeps with its times and its sleep time. Over many days a column is
// narrower than a pixel: the bars then touch. `ticks` (the large view): the labels of the date axis, at the index of
// their day, in place of the days
const SLEEP_HEIGHT = 230
const localDay = (d: Date) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
const hoursOf = (d: Date) => d.getHours() + d.getMinutes() / 60 + d.getSeconds() / 3600
const asleep = (min: number) => {
  const m = Math.round(min)
  return m < 60 ? `${m} min` : `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, '0')} min`
}

export function SleepTimesChart({ days, sleeps, onPick, height = SLEEP_HEIGHT, ticks }: {
  days: string[]; sleeps: { start_at: string; end_at: string; sleep_min: number }[]; onPick?: (day: string) => void
  height?: number; ticks?: (plotW: number) => { at: number; label: string }[]
}) {
  const [ref, width] = useWidth()
  const [hover, setHover] = useState<number | null>(null)
  if (sleeps.length === 0) return <p className="stats-empty">No sleep in these days.</p>
  const plotW = width - PAD.left - PAD.right
  const plotH = height - PAD.top - PAD.bottom
  const band = plotW / days.length
  const bar = band >= 4 ? Math.min(14, band - 2) : Math.max(1, band)
  const y = (hours: number) => PAD.top + plotH * (1 - hours / 24)
  const slot = new Map(days.map((day, i) => [day, i]))
  // each sleep cut at midnight: a part for each day it touches, from its clock time to the next
  const parts = sleeps.flatMap(s => {
    const end = new Date(s.end_at)
    const found = []
    for (let from = new Date(s.start_at); from < end;) {
      const midnight = new Date(from)
      midnight.setHours(24, 0, 0, 0)
      const to = end < midnight ? end : midnight
      const i = slot.get(localDay(from))
      if (i !== undefined) found.push({ i, from: hoursOf(from), to: to === midnight ? 24 : hoursOf(to), sleep: s })
      from = to
    }
    return found
  })
  const day = hover === null ? null : days[hover]
  const listed = [...new Set(parts.filter(p => p.i === hover).map(p => p.sleep))]
  const pick = (clientX: number, box: DOMRect) => {
    const i = Math.floor((clientX - box.left - PAD.left) / band)
    setHover(i >= 0 && i < days.length ? i : null)
  }
  return (
    <div className="chart" ref={ref}>
      <svg width={width} height={height} role="img" aria-label="Sleep times per day" className={onPick ? 'pickable' : ''}
           onPointerMove={e => pick(e.clientX, e.currentTarget.getBoundingClientRect())} onPointerLeave={() => setHover(null)}
           onClick={() => day !== null && onPick?.(day)}>
        <Grid width={width} ticks={[0, 6, 12, 18, 24]} y={y} decimals={0} label={t => `${String(t).padStart(2, '0')}:00`} />
        {ticks ? (
          <g className="axes">
            {ticks(plotW).map(t => (
              <text key={t.at} x={PAD.left + band * (t.at + 0.5)} y={height - 6} textAnchor="middle">{t.label}</text>
            ))}
          </g>
        ) : <DayAxis width={width} days={days} height={height} />}
        {parts.map(p => (
          <g key={`${p.sleep.start_at}-${p.i}`} className={p.i === hover ? 'hovered' : ''}>
            <rect className="bar s1" x={PAD.left + band * p.i + (band - bar) / 2} y={y(p.to)} width={bar}
                  height={Math.max(1, y(p.from) - y(p.to))} rx={Math.min(2, bar / 2)} />
          </g>
        ))}
        {hover !== null && band < 4 && (
          <line className="crosshair" x1={PAD.left + band * (hover + 0.5)} x2={PAD.left + band * (hover + 0.5)} y1={PAD.top}
                y2={PAD.top + plotH} />
        )}
      </svg>
      <Tooltip tip={day ? { x: Math.min(PAD.left + band * (hover! + 1) + 8, width - 180), y: 8, title: longDay(day),
                            rows: listed.length ? listed.map(s => ({ key: `${clockOf(s.start_at)}–${clockOf(s.end_at)}`,
                                                                     value: asleep(s.sleep_min), kind: 'none' }))
                              : [{ key: 'no sleep', value: '', kind: 'none' }] } : null} />
    </div>
  )
}
