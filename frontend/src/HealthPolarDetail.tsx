import { useMutation, useQuery } from '@tanstack/react-query'
import { useState } from 'react'

import {
  addPolarActivity, getPolarActivities, getPolarDetail, labelPolarSession, removePolarSession, showPolarSession,
  type PolarCompareValues, type PolarDetail, type PolarHrv, type PolarRecovery, type PolarSession,
  type PolarStairs,
} from './api'
import { useForget } from './HealthMeasures'
import { ZONES_NEED } from './HealthPolarWeeks'
import { HRV_BETTER } from './healthValues'
import { Tooltip, useWidth } from './charts'

// one Polar H10 session in its popup: its activity and note, its HR curve with the zone limits, its values, the markers,
// the time in each zone, and how it compares with the earlier sessions of the same activity. An HRV test shows its HRV
// values in place of the zones and the comparison; an HR recovery test and a stairs test show their values above the
// markers

export const started = (iso: string) => new Date(iso).toLocaleString('en-GB', {
  weekday: 'short', day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit',
})
export const clockOf = (iso: string) => new Date(iso).toTimeString().slice(0, 5)
const clockAt = (epoch: number) => new Date(epoch * 1000).toTimeString().slice(0, 5)
const clockSeconds = (epoch: number) => new Date(epoch * 1000).toTimeString().slice(0, 8)
// a duration in whole minutes; from an hour on, hours and minutes
export const minutes = (seconds: number) => {
  const m = Math.round(seconds / 60)
  return m < 60 ? `${m} min` : `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, '0')} min`
}
const whole = (v: number | null) => (v === null ? '—' : String(Math.round(v)))
// a short time in seconds; from a minute on, minutes and seconds
const minSec = (s: number) => (s < 60 ? `${s} s` : `${Math.floor(s / 60)} min ${String(s % 60).padStart(2, '0')} s`)
const source = (s: PolarSession) => (s.source === 'logger' ? 'PC' : 'phone')

// the activity of a session, saved at once; the note stays as it is
export function ActivityChoice({ session }: { session: PolarSession }) {
  const forget = useForget()
  const label = useMutation({ mutationFn: (id: number | null) => labelPolarSession(session.id, { activity_id: id }),
                              onSuccess: forget })
  return (
    <>
      <ActivitySelect value={session.activity_id} onPick={id => label.mutate(id)} disabled={label.isPending}
                      label={`Activity of the session of ${started(session.start_at)}`} />
      {label.isError && <p className="cap-error">{label.error.message}</p>}
    </>
  )
}

const NEW = 'new'

// a list of the activities with New activity… at its end: that opens a field for the name, and Add puts the activity at the
// end of the list and picks it; without `tests`, the test activities are not in it (Start recording: a test starts from its
// own button)
export function ActivitySelect({ value, onPick, label, disabled = false, tests = true }: {
  value: number | null; onPick: (id: number | null) => void; label: string; disabled?: boolean; tests?: boolean
}) {
  const forget = useForget()
  const list = useQuery({ queryKey: ['polar-activities'], queryFn: getPolarActivities })
  const [naming, setNaming] = useState(false)
  const [name, setName] = useState('')
  const close = () => { setNaming(false); setName('') }
  const add = useMutation({ mutationFn: () => addPolarActivity(name.trim()),
                            onSuccess: ({ id }) => { close(); forget(); onPick(id) } })
  if (naming) {
    return (
      <form className="polar-activity-new" onSubmit={e => { e.preventDefault(); if (name.trim()) add.mutate() }}>
        <input autoFocus value={name} maxLength={40} placeholder="New activity" aria-label="Name of the new activity"
               onChange={e => setName(e.target.value)} onKeyDown={e => { if (e.key === 'Escape') close() }} />
        <button type="submit" className="quiet" disabled={!name.trim() || add.isPending}>Add</button>
        <button type="button" className="quiet" onClick={() => { close(); add.reset() }}>Cancel</button>
        {add.isError && <p className="cap-error">{add.error.message}</p>}
      </form>
    )
  }
  return (
    <select className="polar-activity" aria-label={label} value={value ?? ''} disabled={disabled}
            onChange={e => (e.target.value === NEW ? setNaming(true) : onPick(e.target.value ? Number(e.target.value) : null))}>
      <option value="">No activity</option>
      {list.data?.activities.filter(a => tests || a.test === null).map(a => <option key={a.id} value={a.id}>{a.name}</option>)}
      <option value={NEW}>New activity…</option>
    </select>
  )
}

export function SessionDetail({ id }: { id: number }) {
  const found = useQuery({ queryKey: ['polar-detail', id], queryFn: () => getPolarDetail(id) })
  if (found.isError) return <p className="cap-error">{found.error.message}</p>
  if (!found.data) return null
  const detail = found.data
  const s = detail.session
  const test = s.activity_test === 'hrv'
  const values: [string, string, string][] = [
    ['Duration', minutes(s.seconds), ''],
    ['Mean HR', whole(s.mean_hr), 'bpm'],
    ['Maximum HR', whole(s.max_hr), 'bpm'],
    // an HRV test is a rest: no energy expenditure and no training load
    ...(test ? [] : [['Net energy expenditure', detail.energy === null ? '—' : String(detail.energy), 'kcal'],
                     ['Training load (Edwards TRIMP)', detail.trimp === null ? '—' : detail.trimp.toFixed(0), '']] as
      [string, string, string][]),
  ]
  return (
    <div className="polar-detail">
      <DetailHead session={s} />
      <HrCurve detail={detail} />
      <dl className="health-values">
        {values.map(([label, value, unit]) => (
          <div key={label}><dt>{label}</dt><dd>{value} {unit && value !== '—' && <small>{unit}</small>}</dd></div>
        ))}
      </dl>
      {!test && (
        <p className="muted polar-energy-note">{detail.energy === null ? detail.energy_missing : 'Net energy expenditure: what '
          + 'the exercise adds to your BMR, a rough estimate from the HR (Keytel et al. 2005); too high for weights. '
          + detail.energy_note}</p>
      )}
      {detail.hrv && <HrvPart hrv={detail.hrv} start={detail.start} />}
      {detail.recovery && <RecoveryPart recovery={detail.recovery} start={detail.start} />}
      {detail.stairs && <StairsPart stairs={detail.stairs} start={detail.start} />}
      <Markers session={s} />
      {!test && (
        <>
          <Zones detail={detail} />
          <Compare detail={detail} />
        </>
      )}
    </div>
  )
}

function DetailHead({ session }: { session: PolarSession }) {
  const forget = useForget()
  const mark = useMutation({ mutationFn: () => (session.removed ? showPolarSession(session.id) : removePolarSession(session.id)),
                             onSuccess: forget })
  return (
    <div className="polar-detail-head">
      <p><strong>{started(session.start_at)}</strong> <span className="muted">{minutes(session.seconds)}, {source(session)}
        {session.removed && ', removed: not in the history'}</span></p>
      <div className="polar-detail-label">
        <ActivityChoice session={session} />
        <NoteField key={session.id} session={session} />
        <button type="button" className="quiet" disabled={mark.isPending} onClick={() => mark.mutate()}>
          {session.removed ? 'Show again' : 'Remove'}</button>
      </div>
      {mark.isError && <p className="cap-error">{mark.error.message}</p>}
    </div>
  )
}

// the note, saved when the field loses the focus
function NoteField({ session }: { session: PolarSession }) {
  const forget = useForget()
  const [note, setNote] = useState(session.note ?? '')
  const save = useMutation({ mutationFn: () => labelPolarSession(session.id, { note }), onSuccess: forget })
  return (
    <>
      <textarea className="polar-note" rows={2} maxLength={2000} placeholder="Note" aria-label="Note of the session" value={note}
                onChange={e => setNote(e.target.value)}
                onBlur={() => { if (note.trim() !== (session.note ?? '')) save.mutate() }} />
      {save.isError && <p className="cap-error">{save.error.message}</p>}
    </>
  )
}

const RECOVERY_ITEMS: [keyof PolarRecovery, string][] = [
  ['hr_stop', 'HR at the stop'], ['hr_60', 'HR 60 s later'], ['hr_120', 'HR 120 s later'], ['drop_60', 'Drop in 60 s'],
  ['drop_120', 'Drop in 120 s'],
]

// the HR recovery test of a session: when the stop was and how it was found, and its values or why it has none
function RecoveryPart({ recovery, start }: { recovery: PolarRecovery; start: number }) {
  return (
    <section className="health-group">
      <h3>HR recovery {recovery.stop_s !== null && (
        <span className="muted">stop at {clockSeconds(start + recovery.stop_s)} ({recovery.stop_from === 'marker'
          ? 'marker' : 'highest HR'})</span>)}</h3>
      {recovery.reason ? <p className="muted">{recovery.reason}</p> : <RecoveryValues recovery={recovery} />}
    </section>
  )
}

// the five values of an HR recovery test, in bpm
export function RecoveryValues({ recovery }: { recovery: PolarRecovery }) {
  return (
    <dl className="health-values">
      {RECOVERY_ITEMS.map(([key, label]) => (
        <div key={key}><dt>{label}</dt><dd>{whole(recovery[key] as number | null)} <small>bpm</small></dd></div>
      ))}
    </dl>
  )
}

// the stairs test of a session: when the climb was, its values and each pause, or why it has none
function StairsPart({ stairs, start }: { stairs: PolarStairs; start: number }) {
  const pauses = stairs.pause_list ?? []
  return (
    <section className="health-group">
      <h3>Stairs test {stairs.start_s !== null && stairs.end_s !== null && (
        <span className="muted">climb from {clockSeconds(start + stairs.start_s)} to {clockSeconds(start + stairs.end_s)}</span>)}
      </h3>
      {stairs.reason ? <p className="muted">{stairs.reason}</p> : (
        <>
          <StairsValues stairs={stairs} />
          {pauses.length > 0 && (
            <p className="muted">Pauses: {pauses.map(p => `${clockSeconds(start + p.at_s)} for ${minSec(p.seconds)}`).join(', ')}.</p>
          )}
        </>
      )}
    </section>
  )
}

// the values of a stairs test: the climb time, the pauses and the highest HR of the climb, then the HR at the 8th floor
// and its drops, or why these have none
export function StairsValues({ stairs }: { stairs: PolarStairs }) {
  return (
    <>
      <dl className="health-values">
        <div><dt>Climb time</dt><dd>{stairs.climb_s === null ? '—' : minSec(stairs.climb_s)}</dd></div>
        <div><dt>Pauses</dt><dd>{stairs.pauses ?? '—'} {stairs.pause_s ? <small>{minSec(stairs.pause_s)} in all</small> : null}</dd></div>
        <div><dt>Highest HR</dt><dd>{whole(stairs.max_hr)} <small>bpm</small></dd></div>
        {!stairs.recovery_reason && (
          <>
            <div><dt>HR at the 8th floor</dt><dd>{whole(stairs.hr_top)} <small>bpm</small></dd></div>
            <div><dt>Drop in 60 s</dt><dd>{whole(stairs.drop_60)} <small>bpm</small></dd></div>
            <div><dt>Drop in 120 s</dt><dd>{whole(stairs.drop_120)} <small>bpm</small></dd></div>
          </>
        )}
      </dl>
      {stairs.recovery_reason && <p className="muted">HR after the climb: {stairs.recovery_reason}</p>}
    </>
  )
}

const HRV_ITEMS: [keyof PolarHrv, string, string, number][] = [
  ['rmssd', 'RMSSD', 'ms', 1], ['ln_rmssd', 'ln RMSSD', '', 2], ['sdnn', 'SDNN', 'ms', 1], ['pnn50', 'pNN50', '%', 1],
  ['lf', 'LF', 'ms²', 0], ['hf', 'HF', 'ms²', 0], ['lf_hf', 'LF/HF', '', 2], ['stress', 'Stress index (Baevsky)', '', 1],
  ['rest_hr', 'Resting HR', 'bpm', 1],
]

// the HRV test of a session: its values (or why it has none) with the way each one is better, the window and the wrong
// beats, and its beat intervals
function HrvPart({ hrv, start }: { hrv: PolarHrv & { beats: [number, number, number][] }; start: number }) {
  return (
    <section className="health-group">
      <h3>HRV test {hrv.from_ms !== null && hrv.to_ms !== null && (
        <span className="muted">{clockAt(hrv.from_ms / 1000)}–{clockAt(hrv.to_ms / 1000)}
          {hrv.wrong_pct !== null && `, ${hrv.wrong_pct} % wrong beats`}</span>)}</h3>
      {hrv.reason ? <p className="muted">{hrv.reason}</p> : (
        <dl className="health-values">
          {HRV_ITEMS.map(([key, label, unit, decimals]) => {
            const v = hrv[key]
            return (
              <div key={key}><dt>{label}</dt><dd>{typeof v === 'number' ? v.toFixed(decimals) : '—'}
                {unit && typeof v === 'number' && <small> {unit}</small>}</dd>
                <span className="health-better">{HRV_BETTER[`h10:${key}`]}</span></div>
            )
          })}
        </dl>
      )}
      {hrv.beats.length > 0 && <RrChart beats={hrv.beats} start={start} />}
    </section>
  )
}

const RR = { height: 160, left: 44, right: 16, top: 10, bottom: 24 }

// the beat intervals of the test window: a line through the clean beats, the wrong beats as marked dots; a crosshair
// gives the time and the interval
function RrChart({ beats, start }: { beats: [number, number, number][]; start: number }) {
  const [ref, width] = useWidth()
  const [hover, setHover] = useState<number | null>(null)
  const good = beats.filter(b => b[2] === 1)
  const values = (good.length ? good : beats).map(b => b[1])
  const lo = Math.floor((Math.min(...values) - 20) / 50) * 50
  const hi = Math.ceil((Math.max(...values) + 20) / 50) * 50
  const first = beats[0][0]
  const last = beats[beats.length - 1][0]
  const plotW = width - RR.left - RR.right
  const plotH = RR.height - RR.top - RR.bottom
  const x = (s: number) => RR.left + (plotW * (s - first)) / Math.max(1, last - first)
  const y = (v: number) => RR.top + plotH * (1 - (Math.min(hi, Math.max(lo, v)) - lo) / (hi - lo))
  const path = good.map((b, i) => `${i ? 'L' : 'M'}${x(b[0]).toFixed(1)},${y(b[1]).toFixed(1)}`).join('')
  const pick = (clientX: number, box: DOMRect) => {
    const s = first + ((clientX - box.left - RR.left) / plotW) * (last - first)
    let best = 0
    beats.forEach((b, i) => { if (Math.abs(b[0] - s) < Math.abs(beats[best][0] - s)) best = i })
    setHover(best)
  }
  const h = hover === null ? null : beats[hover]
  return (
    <div className="chart polar-rr" ref={ref}>
      <svg width={width} height={RR.height} role="img" aria-label="Beat intervals of the test window, ms"
           onPointerMove={e => pick(e.clientX, e.currentTarget.getBoundingClientRect())} onPointerLeave={() => setHover(null)}>
        <g className="axes">
          {[lo, (lo + hi) / 2, hi].map(t => (
            <g key={t}>
              <line x1={RR.left} x2={width - RR.right} y1={y(t)} y2={y(t)} className="grid" />
              <text x={RR.left - 6} y={y(t) + 4} textAnchor="end">{t}</text>
            </g>
          ))}
          {[first, (first + last) / 2, last].map(t => (
            <text key={t} x={x(t)} y={RR.height - 6} textAnchor="middle">{clockAt(start + t)}</text>
          ))}
        </g>
        <path d={path} className="line s1" />
        {beats.map((b, i) => b[2] === 0 && <circle key={i} className="rr-wrong" cx={x(b[0])} cy={y(b[1])} r={3} />)}
        {h && <line className="crosshair" x1={x(h[0])} x2={x(h[0])} y1={RR.top} y2={RR.top + plotH} />}
      </svg>
      <Tooltip tip={h ? {
        x: Math.min(x(h[0]) + 12, width - 170), y: 8, title: clockAt(start + h[0]),
        rows: [{ key: h[2] ? 'ms' : 'ms, wrong beat', value: String(Math.round(h[1])), kind: 's1' }],
      } : null} />
    </div>
  )
}

const CURVE = { height: 220, left: 40, right: 34, top: 14, bottom: 24 }
// the steps of the clock axis in minutes: the smallest that leaves 70 px between two labels
const CLOCK_STEPS = [5, 10, 15, 30, 60, 120, 240]

// the HR of each second as one line (one point per pixel: the mean of its seconds), thin lines at the zone limits, the
// markers of a PC session as ticks on top, the stop and the 60 s and 120 s after it of an HR recovery test as dots; a
// crosshair gives the time and the HR
function HrCurve({ detail }: { detail: PolarDetail }) {
  const [ref, width] = useWidth()
  const [hover, setHover] = useState<number | null>(null)
  const { hr, start, zones, session } = detail
  const n = hr.length
  let low = Infinity
  let high = -Infinity
  for (const v of hr) {
    if (v !== null) {
      low = Math.min(low, v)
      high = Math.max(high, v)
    }
  }
  const empty = n === 0 || low === Infinity
  const lo = Math.floor((low - 5) / 10) * 10
  const hi = Math.ceil((high + 5) / 10) * 10
  const plotW = width - CURVE.left - CURVE.right
  const plotH = CURVE.height - CURVE.top - CURVE.bottom
  const x = (i: number) => CURVE.left + (plotW * i) / Math.max(1, n - 1)
  const y = (v: number) => CURVE.top + plotH * (1 - (v - lo) / (hi - lo))
  let path = ''
  if (!empty) {
    const step = Math.max(1, Math.floor(n / plotW))
    let gap = true
    for (let i = 0; i < n; i += step) {
      let sum = 0
      let count = 0
      for (let j = i; j < Math.min(n, i + step); j++) {
        const v = hr[j]
        if (v !== null) {
          sum += v
          count++
        }
      }
      if (count === 0) {
        gap = true
        continue
      }
      path += `${gap ? 'M' : 'L'}${x(i).toFixed(1)},${y(sum / count).toFixed(1)}`
      gap = false
    }
  }
  const stepMin = CLOCK_STEPS.find(m => (plotW * m * 60) / Math.max(1, n) >= 70) ?? CLOCK_STEPS[CLOCK_STEPS.length - 1]
  const ticks: number[] = []
  for (let t = Math.ceil(start / (stepMin * 60)) * stepMin * 60; t < start + n; t += stepMin * 60) ticks.push(t)
  const pick = (clientX: number, box: DOMRect) => {
    const i = Math.round(((clientX - box.left - CURVE.left) / plotW) * (n - 1))
    setHover(Math.min(n - 1, Math.max(0, i)))
  }
  const now = hover === null ? null : hr[hover]
  // an HR recovery test: dots at the stop and 60 s and 120 s after it; a stairs test: the same from the 8th floor
  const stopAt = detail.recovery?.stop_s ?? detail.stairs?.end_s ?? null
  const recoveryDots = stopAt === null ? [] : [0, 60, 120].map(after => stopAt + after).filter(i => i < n && hr[i] !== null)
  return (
    <div className="chart polar-curve" ref={ref}>
      {empty ? <p className="stats-empty">No heart rate in this session.</p> : (
        <>
          <svg width={width} height={CURVE.height} role="img" aria-label="Heart rate of each second, bpm"
               onPointerMove={e => pick(e.clientX, e.currentTarget.getBoundingClientRect())} onPointerLeave={() => setHover(null)}>
            <g className="axes">
              {[lo, (lo + hi) / 2, hi].map(t => (
                <g key={t}>
                  <line x1={CURVE.left} x2={width - CURVE.right} y1={y(t)} y2={y(t)} className="grid" />
                  <text x={CURVE.left - 6} y={y(t) + 4} textAnchor="end">{t}</text>
                </g>
              ))}
              {ticks.map(t => <text key={t} x={x(t - start)} y={CURVE.height - 6} textAnchor="middle">{clockAt(t)}</text>)}
            </g>
            {zones?.limits.map((limit, k) => limit > lo && limit < hi && (
              <g key={limit}>
                <line className="zone-line" x1={CURVE.left} x2={width - CURVE.right} y1={y(limit)} y2={y(limit)} />
                <text className="zone-label" x={width - CURVE.right + 4} y={y(limit) - 3}>Z{k + 1}</text>
              </g>
            ))}
            <path d={path} className="line s1" />
            {session.markers.map(m => {
              const i = Math.round(new Date(m.at).getTime() / 1000 - start)
              return i >= 0 && i < n && (
                <line key={`${m.at}-${m.label}`} className="marker-tick" x1={x(i)} x2={x(i)} y1={CURVE.top - 8} y2={CURVE.top + 4}>
                  <title>{`${clockOf(m.at)} ${m.label}`}</title>
                </line>
              )
            })}
            {recoveryDots.map(i => <circle key={i} className="dot s2" cx={x(i)} cy={y(hr[i] as number)} r={4} />)}
            {hover !== null && <line className="crosshair" x1={x(hover)} x2={x(hover)} y1={CURVE.top} y2={CURVE.top + plotH} />}
            {hover !== null && now !== null && <circle className="dot s1" cx={x(hover)} cy={y(now)} r={4} />}
          </svg>
          <Tooltip tip={hover === null ? null : {
            x: Math.min(x(hover) + 12, width - 170), y: 8, title: clockAt(start + hover),
            rows: [{ key: 'bpm', value: now === null ? '—' : String(Math.round(now)), kind: 's1' }],
          }} />
        </>
      )}
    </div>
  )
}

// the minutes in each zone, zone 5 on top; without a birth date in person.toml, where to set it
function Zones({ detail }: { detail: PolarDetail }) {
  if (!detail.zones) return <section className="health-group"><h3>Zones</h3><p className="muted">{ZONES_NEED}</p></section>
  const { max_hr: top, limits, seconds } = detail.zones
  const total = seconds.reduce((a, b) => a + b, 0) || 1
  const rows: [string, string, number, string][] = [5, 4, 3, 2, 1].map(k => [
    `Zone ${k}`, k === 5 ? `${limits[4]} bpm and more` : `${limits[k - 1]}–${limits[k] - 1} bpm`, seconds[k], `z${k}`])
  rows.push(['Below zone 1', `under ${limits[0]} bpm`, seconds[0], 'z0'])
  return (
    <section className="health-group">
      <h3>Zones <span className="muted">from the maximum HR {top} bpm (220 − age)</span></h3>
      <ul className="bar-table polar-zones">
        {rows.map(([name, range, s, kind]) => (
          <li key={name}>
            <span className="bar-label">{name} <small className="muted">{range}</small></span>
            <span className="bar-track"><span className={`bar-fill ${kind}`} style={{ width: `${(100 * s) / total}%` }} /></span>
            <span className="bar-value">{minutes(s)} <small>{Math.round((100 * s) / total)} %</small></span>
          </li>
        ))}
      </ul>
    </section>
  )
}

// the markers of a PC session with the HR of their second
function Markers({ session }: { session: PolarSession }) {
  const { markers } = session
  if (markers.length === 0) return null
  return (
    <section className="health-group">
      <h3>Markers</h3>
      <div className="health-table">
        <table>
          <thead>
            <tr>
              <th scope="col">Time</th><th scope="col">Marker</th><th scope="col" className="num">HR</th>
            </tr>
          </thead>
          <tbody>
            {markers.map(m => (
              <tr key={`${m.at}-${m.label}`}>
                <td>{clockOf(m.at)}</td><td>{m.label.charAt(0).toUpperCase() + m.label.slice(1)}</td>
                <td className="num">{whole(m.hr)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}

const COMPARED: [keyof PolarCompareValues, string, string][] = [
  ['seconds', 'Duration', 'min'], ['mean_hr', 'Mean HR', 'bpm'], ['max_hr', 'Maximum HR', 'bpm'],
  ['high_zone_min', 'Time in zone 3 or higher', 'min'],
]
// a value of the comparison in its unit: the duration in minutes, the time in zones to 0.1 min, the HR in whole bpm
const shown = (key: keyof PolarCompareValues, v: number | null) => (v === null ? '—'
  : key === 'seconds' ? String(Math.round(v / 60)) : key === 'high_zone_min' ? v.toFixed(1) : String(Math.round(v)))
const difference = (key: keyof PolarCompareValues, a: number | null, b: number | null) => {
  if (a === null || b === null) return '—'
  const d = key === 'seconds' ? (a - b) / 60 : a - b
  const text = key === 'high_zone_min' ? d.toFixed(1) : String(Math.round(d))
  if (Number(text) === 0) return '0'
  return d > 0 ? `+${text}` : text.replace('-', '−')
}

function Compare({ detail }: { detail: PolarDetail }) {
  const { session, compare } = detail
  // one earlier session has no mean
  const usual = compare === null ? '' : compare.count === 1 ? `Previous ${session.activity} session`
    : `Mean of the last ${compare.count} ${session.activity} sessions`
  return (
    <section className="health-group">
      <h3>Compare with the same activity</h3>
      {session.activity_id === null
        ? <p className="muted">Choose an activity to compare this session with others of the same activity.</p>
        : compare === null ? <p className="muted">There is no earlier {session.activity} session to compare with.</p> : (
          <div className="health-table">
            <table>
              <thead>
                <tr>
                  <th scope="col">Value</th><th scope="col" className="num">This session</th>
                  <th scope="col" className="num">{usual}</th><th scope="col" className="num">Difference</th>
                </tr>
              </thead>
              <tbody>
                {COMPARED.map(([key, label, unit]) => (
                  <tr key={key}>
                    <td>{label} <small className="muted">{unit}</small></td>
                    <td className="num">{shown(key, compare.this[key])}</td>
                    <td className="num">{shown(key, compare.usual[key])}</td>
                    <td className="num">{difference(key, compare.this[key], compare.usual[key])}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
    </section>
  )
}
