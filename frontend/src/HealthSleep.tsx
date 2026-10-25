import { useMutation, useQuery } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'

import { getSleepSummary, getSleepTimes, importSleep, type HealthView, type SleepGroup } from './api'
import { closeOutside } from './dialog'
import { ordinal, PERIODS, timeTicks } from './HealthAnalysis'
import { SleepTimesChart } from './HealthCharts'
import { clockOf, minutes } from './HealthPolarDetail'

// the sleep of the page's day from Sleep as Android (the backup that Dropbox syncs to the PC), as the Withings card: the
// total sleep time with the times of its sleeps, the time in bed and awake, the stages and cycles; the time of
// the backup and of the last import, and Import sleep, which reads the backup. A sleep belongs to the day on which it
// ends; a value that was not measured says so.

const longDay = (day: string) =>
  new Date(`${day}T12:00:00`).toLocaleDateString('en-GB', { weekday: 'long', day: 'numeric', month: 'long' })
const inMinutes = (min: number | undefined) => (min === undefined ? null : minutes(min * 60))
const inHours = (hours: number | undefined) => (hours === undefined ? null : minutes(hours * 3600))
// a time as the Withings card writes it: 18 Oct, 11:36
const when = (iso: string) => {
  const d = new Date(iso)
  return `${d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })}, ${d.toTimeString().slice(0, 5)}`
}
const VIEW_KEY = 'health-sleep-times-view'
const DAY_MS = 86_400_000

// minutes as hours and minutes
const hm = (m: number) => `${Math.floor(m / 60)} h ${String(Math.round(m % 60)).padStart(2, '0')} min`
// the strength of a correlation in words (|r|: below 0.1 none, 0.3 weak, 0.5 moderate, else strong)
const strength = (r: number) => (Math.abs(r) < 0.1 ? 'no relation' : Math.abs(r) < 0.3 ? 'weak'
  : Math.abs(r) < 0.5 ? 'moderate' : 'strong')
const GROUPS: [string, keyof SleepGroup][] = [['Main sleep', 'sleep_min'], ['Bedtime', 'bedtime'], ['Wake time', 'wake'],
                                              ['Mid-sleep', 'mid']]

// the sleep pattern of the period of the history from the main sleep of each day (its longest, so a nap does not count):
// how much the bedtime and the wake time vary, the weekdays against the weekend (by the wake day), and the main sleep
// against the ln RMSSD of the HRV test of the same morning
export function SleepPattern({ days, fetched }: { days: number; fetched: string | null }) {
  const found = useQuery({ queryKey: ['sleep-summary', days, fetched], queryFn: () => getSleepSummary(days) })
  if (found.isError) return <p className="cap-error">{found.error.message}</p>
  const s = found.data
  if (!s) return null
  const { r, slope, pairs } = s.hrv
  const hrv = r === null || slope === null
    ? `too few mornings with both: ${pairs} of the 10 needed. Make the HRV test after waking up.`
    : `r = ${r.toFixed(2)} (${strength(r)}), n = ${pairs} mornings: ln RMSSD ${slope >= 0 ? '+' : ''}${slope.toFixed(2)} per hour `
      + 'of sleep.'
  const groups = [['Weekdays', s.weekday], ['Weekend', s.weekend]] as const
  return (
    <section className="health-group sleep-pattern">
      <h3>Sleep pattern <span className="muted">the main sleep of each day (its longest), {s.nights} nights</span></h3>
      <dl className="health-values">
        <div><dt>Bedtime, standard deviation</dt><dd>{s.bedtime_sd ?? '—'} {s.bedtime_sd !== null && <small>min</small>}</dd></div>
        <div><dt>Wake time, standard deviation</dt><dd>{s.wake_sd ?? '—'} {s.wake_sd !== null && <small>min</small>}</dd></div>
        <div><dt>Social jetlag</dt>
          <dd>{s.jetlag_min ?? '—'} {s.jetlag_min !== null && <small>min, the mid-sleep of the weekend later</small>}</dd></div>
      </dl>
      <div className="health-table">
        <table>
          <thead>
            <tr><th scope="col">By the wake day</th><th scope="col" className="num">Nights</th>
              {GROUPS.map(([label]) => <th key={label} scope="col" className="num">{label}</th>)}</tr>
          </thead>
          <tbody>
            {groups.map(([name, g]) => (
              <tr key={name}>
                <td>{name}</td><td className="num">{g?.nights ?? 0}</td>
                {GROUPS.map(([label, key]) => (
                  <td key={label} className="num">{g === null ? '—' : key === 'sleep_min' ? hm(g.sleep_min) : g[key]}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="muted">Main sleep and the HRV test of the same morning: {hrv}</p>
    </section>
  )
}

export function SleepCard({ data, onChange }: { data: HealthView; onChange: (v: HealthView) => void }) {
  const status = data.sleep
  const run = useMutation({ mutationFn: () => importSleep(data.day), onSuccess: onChange })
  const day = status.day
  const v = day?.values ?? {}
  const isToday = data.day === data.today
  const deep = inMinutes(v['sleep:deep'])
  const stages: [string, string | null, string?][] = [
    ['Deep sleep', deep, v['sleep:deep_pct'] === undefined ? undefined : `${v['sleep:deep_pct'].toFixed(0)} %`],
    ['Light sleep', inMinutes(v['sleep:light'])],
    ['REM sleep', inMinutes(v['sleep:rem'])],
    ['Time awake', inMinutes(v['sleep:awake'])],
    ['Sleep cycles', v['sleep:cycles'] === undefined ? null : String(v['sleep:cycles'])],
  ]
  return (
    <article className="habit-card health-latest health-sleep">
      <h2>Sleep{!isToday && <span className="muted"> {longDay(data.day)}</span>}</h2>
      {day ? (
        <>
          <div className="health-reading">
            <span className="health-label">Total sleep time</span>
            <span className="health-value">{inHours(v['sleep:total'])}</span>
            <span className="muted">
              {day.sleeps.map(s => `${clockOf(s.start_at)}–${clockOf(s.end_at)}`).join(', ')}
              {day.sleeps.length > 1 && ` (${day.sleeps.length} sleeps)`}, {inHours(v['sleep:in_bed'])} in bed
            </span>
          </div>
          <section className="health-group">
            <h3>Sleep stages <span className="muted">estimates of Sleep as Android</span></h3>
            <dl className="health-values">
              {stages.map(([label, value, note]) => (
                <div key={label}>
                  <dt>{label}</dt>
                  {value === null ? <dd className="muted">not measured</dd> : <dd>{value} {note && <small>{note}</small>}</dd>}
                </div>
              ))}
            </dl>
          </section>
        </>
      ) : status.imported_at && <p className="muted">No sleep ends on this day.</p>}
      <div className="health-status">
        <span className="muted">
          {[status.backup_at && `Backup ${when(status.backup_at)}`,
            status.imported_at ? `imported ${when(status.imported_at)}` : status.backup_at && 'not imported yet']
            .filter(Boolean).join(', ')}
        </span>
        {status.waiting && <strong>A new backup is ready.</strong>}
        {status.configured && (
          <button type="button" className="quiet" disabled={run.isPending || !status.backup_at} onClick={() => run.mutate()}>
            {run.isPending ? 'Importing…' : 'Import sleep'}
          </button>
        )}
        {status.message && <p className="cap-error">{status.message}</p>}
        {run.isError && <p className="cap-error">{run.error.message}</p>}
      </div>
    </article>
  )
}

// the large view of the sleep times: the sleep diary over 90 days, a year, 3 years or all data since the first sleep, as
// the large view of the other charts; the period chosen last in this browser
function SleepTimesDialog({ onClose }: { onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null)
  useEffect(() => { dialog.current?.showModal() }, [])
  const [days, setDays] = useState<number | null>(() => {
    try {
      const saved = JSON.parse(localStorage.getItem(VIEW_KEY) ?? '')
      return PERIODS.some(([d]) => d === saved) ? saved : 365
    } catch { return 365 }
  })
  const choose = (next: number | null) => {
    setDays(next)
    try { localStorage.setItem(VIEW_KEY, JSON.stringify(next)) } catch { /* no storage: the choice lasts this view */ }
  }
  const found = useQuery({ queryKey: ['health-sleep-times', days], queryFn: () => getSleepTimes(days),
                           placeholderData: previous => previous })
  const start = found.data ? ordinal(found.data.first) : 0
  const end = found.data ? ordinal(found.data.last) : -1
  const list = Array.from({ length: end - start + 1 }, (_, i) => new Date((start + i) * DAY_MS).toISOString().slice(0, 10))
  return createPortal(
    <dialog ref={dialog} className="health-analysis accent-health" onClose={onClose} aria-labelledby="sleep-times-title"
            {...closeOutside}>
      <div className="analysis-head">
        <h2 id="sleep-times-title">Sleep times<span className="muted">each sleep from bedtime to wake time</span></h2>
        <button type="button" className="quiet" onClick={() => dialog.current?.close()}>Close</button>
      </div>
      <div className="analysis-controls">
        <div className="research-switch" role="group" aria-label="Period">
          {PERIODS.map(([n, label]) => (
            <button key={label} type="button" aria-pressed={days === n} onClick={() => choose(n)}>{label}</button>
          ))}
        </div>
        {found.isFetching && <span className="muted">Loading…</span>}
      </div>
      {found.isError && <p className="cap-error">{found.error.message}</p>}
      {found.data && (
        <section className="analysis-card analysis-wide">
          <h3>{found.data.sleeps.length} sleeps on {new Set(found.data.sleeps.map(s => s.day)).size} days</h3>
          <SleepTimesChart days={list} sleeps={found.data.sleeps} height={480}
                           ticks={plotW => timeTicks(start, end, plotW).map(t => ({ at: t.at - start, label: t.label }))} />
        </section>
      )}
    </dialog>,
    document.body,
  )
}

// the Enlarge button of the sleep times chart; Esc or Close shuts the large view
export function SleepTimesEnlarge() {
  const [open, setOpen] = useState(false)
  return (
    <>
      <button type="button" className="quiet health-enlarge" aria-label="Enlarge the sleep times"
              onClick={() => setOpen(true)}>Enlarge</button>
      {open && <SleepTimesDialog onClose={() => setOpen(false)} />}
    </>
  )
}
