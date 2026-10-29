import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { createContext, Fragment, type ReactNode, useContext, useEffect, useRef, useState } from 'react'

import {
  addKetoneReading, fetchWithings, getHealth, getHealthHistory, getPolarActivities, removeKetoneReading, setAngina, setKetoneTime,
  setTape, showMeasurement, type HealthDayRow, type HealthMeasurement, type HealthView, type KetostixLevel, type PolarStatus,
  type RecordingPoint, type WithingsStatus, type WithingsValue,
} from './api'
import { EnlargeButton, MoreChoices } from './HealthAnalysis'
import { BarChart, LineChart, SleepTimesChart, type Line } from './HealthCharts'
import { DayMeasurements, summary, useForget, whole } from './HealthMeasures'
import { DaySessions, PolarCard, SessionsDialog } from './HealthPolar'
import { TEST_STEPS } from './HealthPolarTests'
import { ActivityWeeks, EnergyWeeks, PolarWeeks, ZoneWeeks } from './HealthPolarWeeks'
import { FitbitCard } from './HealthFitbit'
import { Food, foodLine, foodSummary } from './HealthFood'
import { HealthOverview } from './HealthOverview'
import { SleepCard, SleepPattern, SleepTimesEnlarge } from './HealthSleep'
import { SupplementHistory } from './HealthSupplementHistory'
import { supplementSummary, Supplements } from './HealthSupplements'
import { TapeGuideButton } from './HealthTapeGuide'
import { clock, TimeField } from './HealthTime'
import {
  COMPOSITION, FITBIT, FITBIT_GROUPS, FOOD, H10, HRV, HRV_BETTER, inText, RECOVERY, SLEEP, STAIRS, TAPE, TAPE_PAIRS, VESSELS,
  type Value,
} from './healthValues'

// the user's health: each day the supplements taken (each amount at its clock time), one angina score 0 to 5 with a
// note, and the Withings blood pressure and scale measurements, fetched by the app; a history puts them on one date axis.

const longDay = (day: string) =>
  new Date(`${day}T12:00:00`).toLocaleDateString('en-GB', { weekday: 'long', day: 'numeric', month: 'long' })
const shift = (day: string, days: number) => {
  const d = new Date(`${day}T12:00:00`)
  d.setDate(d.getDate() + days)
  return d.toISOString().slice(0, 10)
}
const when = (iso: string) => {
  const d = new Date(iso)
  return `${d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })}, ${d.toTimeString().slice(0, 5)}`
}
// in the order of the body composition: fat before muscle
const PART_KINDS: [string, string][] = [['fat_mass', 'Fat'], ['muscle_mass', 'Muscle'], ['fat_free_mass', 'Fat-free']]
const TREND_NOTE = 'the trend moves 10 % a day towards the mean of each day'
const PARTS: [string, string][] = [
  ['left_arm', 'Left arm'], ['right_arm', 'Right arm'], ['left_leg', 'Left leg'], ['right_leg', 'Right leg'], ['trunk', 'Trunk'],
]

export default function HealthPage() {
  const client = useQueryClient()
  const [day, setDay] = useState<string | null>(null)
  const view = useQuery({
    queryKey: ['health', day], queryFn: () => getHealth(day ?? undefined), placeholderData: previous => previous,
    refetchInterval: q => (q.state.data?.polar.running ? 2000 : false),
  })
  const keep = (data: HealthView) => {
    client.setQueryData(['health', day], data)
    client.invalidateQueries({ queryKey: ['health-history'] })
    client.invalidateQueries({ queryKey: ['health-overview'] })
    client.invalidateQueries({ queryKey: ['sleep-summary'] })
  }
  if (view.isError) return <section className="page accent-health"><p className="cap-error">{view.error.message}</p></section>
  if (!view.data) return null
  const data = view.data
  return (
    <section className="page accent-health health-page">
      <header className="page-head"><h1>Health dashboard</h1></header>
      <HealthOverview day={data.day} />
      <div className="health-top">
        {/* the Polar and Sleep cards under the Today card: the parts of the day fold, so the left column has room, and the
            two columns end at about the same height */}
        <div className="health-side">
          <TodayCard data={data} onChange={keep} onDay={next => setDay(next === data.today ? null : next)} />
          <PolarCard data={data} onChange={keep} />
          <SleepCard data={data} onChange={keep} />
          <FitbitCard data={data} onChange={keep} />
        </div>
        <WithingsCard data={data} onChange={keep} />
      </div>
      <HistoryCard fetched={data.withings.last_fetch} ketostix={data.ketostix} polar={data.polar} />
    </section>
  )
}

function TodayCard({ data, onChange, onDay }: { data: HealthView; onChange: (v: HealthView) => void; onDay: (day: string) => void }) {
  const isToday = data.day === data.today
  return (
    <article className="habit-card health-today">
      <div className="health-day-head">
        <div>
          <h2>{isToday ? 'Today' : longDay(data.day)}</h2>
          <p className="muted">{isToday ? longDay(data.day) : 'catching up a day'}</p>
        </div>
        {/* side by side: a step back and a step forward without a move of the mouse across the card */}
        <div className="health-day-steps">
          <button type="button" className="quiet" aria-label="Previous day" onClick={() => onDay(shift(data.day, -1))}>‹</button>
          <button type="button" className="quiet" aria-label="Next day" disabled={isToday}
                  onClick={() => onDay(shift(data.day, 1))}>›</button>
        </div>
      </div>
      <Fold id="supplements" title="Supplements" summary={supplementSummary(data.supplements)}>
        <Supplements data={data} onChange={onChange} />
      </Fold>
      <Fold id="food" title="Food" summary={foodSummary(data)}>
        <Food key={`food-${data.day}`} data={data} onChange={onChange} />
      </Fold>
      <Fold id="ketones" title="Ketones" summary={ketoneSummary(data)}>
        <Ketones data={data} onChange={onChange} />
      </Fold>
      {/* the angina score after the ketones: it is noted at the end of the day. A new day starts the note and the tape
          fields from its own values; each part has its own key, as siblings with one key left the part of the day before
          on the page */}
      <Fold id="angina" title="Angina pectoris" summary={anginaSummary(data)}>
        <Angina key={`angina-${data.day}`} data={data} onChange={onChange} />
      </Fold>
      <Fold id="tape" title="Tape measure" summary={tapeSummary(data)}>
        <TapeCard key={`tape-${data.day}`} data={data} onChange={onChange} />
      </Fold>
    </article>
  )
}

// a part of the Today card that folds: closed, it shows one line with what the day holds; open, its inputs. Each part
// remembers its own choice in this browser, closed at first
function Fold({ id, title, summary, children }: { id: string; title: string; summary: string; children: ReactNode }) {
  const key = `health-fold-${id}`
  const [open, setOpen] = useState(() => {
    try { return localStorage.getItem(key) === 'open' } catch { return false }
  })
  const toggle = () => {
    setOpen(!open)
    try { localStorage.setItem(key, open ? 'closed' : 'open') } catch { /* no storage: the choice lasts this visit */ }
  }
  return (
    <section className={`health-fold${open ? ' open' : ''}`}>
      <button type="button" className="health-fold-head" aria-expanded={open} onClick={toggle}>
        <span className="health-fold-mark" aria-hidden="true">{open ? '▾' : '▸'}</span>
        <span className="health-fold-title">{title}</span> <span className="muted">{summary}</span>
      </button>
      {open && children}
    </section>
  )
}

// the line of a closed part: what the day holds
function ketoneSummary(data: HealthView): string {
  if (data.ketones.length === 0) return 'no reading'
  const top = Math.max(...data.ketones.map(r => r.level))
  const label = data.ketostix.find(k => k.level === top)?.label ?? ''
  return `${data.ketones.length} ${data.ketones.length === 1 ? 'reading:' : 'readings, the highest:'} ${label} ${top} mmol/L`
}
function anginaSummary(data: HealthView): string {
  const a = data.angina
  if (a?.not_measured) return 'not measured'
  if (a?.score === null || a?.score === undefined) return 'no score'
  return `score ${a.score}${a.note ? `: ${a.note}` : ''}`
}
function tapeSummary(data: HealthView): string {
  const measured = Object.keys(data.tape.values).length
  if (measured) return `${measured} of ${TAPE.length} sites measured`
  return data.tape.last ? `last measured on ${longDay(data.tape.last.day)}` : 'not measured yet'
}

function Angina({ data, onChange }: { data: HealthView; onChange: (v: HealthView) => void }) {
  const score = data.angina?.score ?? null
  const notMeasured = data.angina?.not_measured ?? false
  const [note, setNote] = useState(data.angina?.note ?? '')
  const save = useMutation({
    mutationFn: (next: { score: number | null; note: string; notMeasured: boolean }) =>
      setAngina(data.day, next.score, next.note, next.notMeasured),
    onSuccess: onChange,
  })
  return (
    <section className="health-angina">
      <div className="health-scores" role="group" aria-label="Angina score of the day">
        {[0, 1, 2, 3, 4, 5].map(n => (
          // clicking the chosen score again clears it
          <button key={n} type="button" className={`choice-chip${score === n ? ' chosen' : ''}`} aria-pressed={score === n}
                  disabled={save.isPending} onClick={() => save.mutate({ score: score === n ? null : n, note, notMeasured: false })}>
            {n}
          </button>
        ))}
        {/* no exertion that day: no score, not a 0 */}
        <button type="button" className={`choice-chip${notMeasured ? ' chosen' : ''}`} aria-pressed={notMeasured}
                title="No physical exertion that day" disabled={save.isPending}
                onClick={() => save.mutate({ score: null, note, notMeasured: !notMeasured })}>Not measured</button>
      </div>
      <textarea className="research-text" rows={2} value={note} placeholder="A note, for example what you were doing"
                aria-label="Angina note" onChange={e => setNote(e.target.value)}
                onBlur={() => note.trim() !== (data.angina?.note ?? '') && save.mutate({ score, note, notMeasured })} />
      {save.isError && <p className="cap-error">{save.error.message}</p>}
    </section>
  )
}

// Ketostix: each strip read is a reading with its time. A colour notes one at the time now; the list under the colours
// changes a time or removes a reading at once
function Ketones({ data, onChange }: { data: HealthView; onChange: (v: HealthView) => void }) {
  const add = useMutation({ mutationFn: (level: number) => addKetoneReading(data.day, level, clock()), onSuccess: onChange })
  const time = useMutation({ mutationFn: ({ id, at }: { id: number; at: string }) => setKetoneTime(id, at), onSuccess: onChange })
  const remove = useMutation({ mutationFn: (id: number) => removeKetoneReading(id), onSuccess: onChange })
  const error = add.error ?? time.error ?? remove.error
  const step = (level: number) => data.ketostix.findIndex(k => k.level === level)
  return (
    <section className="health-angina health-ketones">
      <div className="health-scores health-strips" role="group" aria-label="Note a Ketostix reading at the time now">
        {data.ketostix.map((k, i) => (
          <button key={k.level} type="button" className="choice-chip" disabled={add.isPending} onClick={() => add.mutate(k.level)}>
            <i className={`strip-colour strip-${i}`} />{k.label} <small>{k.level} mmol/L</small>
          </button>
        ))}
      </div>
      {data.ketones.length > 0 && (
        <ul className="health-ketone-list">
          {data.ketones.map(r => (
            <li key={r.id}>
              <TimeField key={`${r.id}-${r.taken_at}`} value={r.taken_at} label={`Time of the reading of ${r.taken_at}`}
                         onSave={at => time.mutate({ id: r.id, at })} />
              <span className="health-ketone-level"><i className={`strip-colour strip-${step(r.level)}`} />
                {data.ketostix[step(r.level)]?.label ?? ''} <small className="muted">{r.level} mmol/L</small></span>
              <button type="button" className="quiet" disabled={remove.isPending} onClick={() => remove.mutate(r.id)}>Remove</button>
            </li>
          ))}
        </ul>
      )}
      {error && <p className="cap-error">{error.message}</p>}
    </section>
  )
}

// the tape measure, once a week: one field per site in cm. A field saves when you leave it; an empty field removes its
// value. The grey hints are the last values before this day
function TapeCard({ data, onChange }: { data: HealthView; onChange: (v: HealthView) => void }) {
  const save = useMutation({ mutationFn: ({ site, cm }: { site: string; cm: number | null }) => setTape(data.day, site, cm),
                             onSuccess: onChange })
  const last = data.tape.last
  return (
    <section className="health-angina health-tape">
      <div className="health-moment-head">
        <div className="health-head-actions"><TapeGuideButton /></div>
      </div>
      <div className="health-tape-grid">
        {TAPE.map(([key, label]) => {
          const site = key.slice('tape:'.length)
          return <TapeField key={site} label={label} value={data.tape.values[site] ?? null} hint={last?.values[site] ?? null}
                            onSave={cm => save.mutate({ site, cm })} />
        })}
      </div>
      {save.isError && <p className="cap-error">{save.error.message}</p>}
    </section>
  )
}

function TapeField({ label, value, hint, onSave }: {
  label: string; value: number | null; hint: number | null; onSave: (cm: number | null) => void
}) {
  const shown = value === null ? '' : String(value)
  const [draft, setDraft] = useState(shown)
  useEffect(() => setDraft(shown), [shown])
  const leave = () => {
    const text = draft.trim()
    const cm = text === '' ? null : Number(text)
    if (cm === null || !Number.isNaN(cm)) {
      if (cm !== value) onSave(cm)
    }
  }
  return (
    <label className="health-tape-field">{label}
      <input type="number" inputMode="decimal" step="0.1" min={10} max={250} value={draft}
             placeholder={hint === null ? '' : String(hint)} aria-label={`${label} in cm`}
             onChange={e => setDraft(e.target.value)} onBlur={leave} />
    </label>
  )
}

function WithingsCard({ data, onChange }: { data: HealthView; onChange: (v: HealthView) => void }) {
  const fetchNow = useMutation({ mutationFn: () => fetchWithings(data.day), onSuccess: onChange })
  const { pressure, values, segments } = data.latest
  const status = data.withings
  const partDate = PART_KINDS.flatMap(([kind]) => PARTS.map(([part]) => segments[kind]?.[part]?.measured_at)).find(Boolean)
  // the card follows the day of the page: on an earlier day the newest values up to the end of that day
  const isToday = data.day === data.today
  return (
    <article className="habit-card health-latest">
      <h2>Withings{!isToday && <span className="muted"> up to {longDay(data.day)}</span>}</h2>
      {pressure ? (
        <div className="health-reading">
          <span className="health-label">Blood pressure</span>
          <span className="health-value">{whole(pressure.systolic)}/{whole(pressure.diastolic)} <small>mmHg</small></span>
          <span className="muted">{pressure.pulse !== null && `pulse ${whole(pressure.pulse)}, `}{when(pressure.measured_at)}</span>
        </div>
      ) : status.connected && <p className="muted">No blood pressure measured {isToday ? 'yet' : 'up to this day'}.</p>}
      <ValueGroup title="Body composition" list={COMPOSITION} values={values} />
      <ValueGroup title="Heart, vessels and nerves" list={VESSELS} values={values} />
      {partDate && (
        <section className="health-group">
          <h3>Per body part <span className="muted">{when(partDate)}</span></h3>
          <div className="health-table">
            <table className="health-parts-table">
              <thead>
                <tr>
                  <th scope="col">Body part</th>
                  {PART_KINDS.map(([kind, label]) => <th key={kind} scope="col" className="num">{label}</th>)}
                </tr>
              </thead>
              <tbody>
                {PARTS.map(([part, label]) => (
                  <tr key={part}>
                    <th scope="row">{label}</th>
                    {PART_KINDS.map(([kind]) => {
                      const v = segments[kind]?.[part]
                      return <td key={kind} className="num">{v ? `${v.value.toFixed(1)} kg` : '—'}</td>
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
      <WithingsStatusLine status={status} fetching={fetchNow.isPending} onFetch={() => fetchNow.mutate()} />
      {fetchNow.isError && <p className="cap-error">{fetchNow.error.message}</p>}
    </article>
  )
}

function ValueGroup({ title, list, values }: { title: string; list: Value[]; values: HealthView['latest']['values'] }) {
  const shown = list.filter(([key]) => values[key])
  if (shown.length === 0) return null
  return (
    <section className="health-group">
      <h3>{title}</h3>
      <dl className="health-values">
        {shown.map(([key, label, unit, decimals]) => {
          const v = values[key] as WithingsValue
          return (
            <div key={key}>
              <dt>{label}</dt>
              <dd>{v.value.toFixed(decimals)} {unit && <small>{unit}</small>}</dd>
              <dd className="muted">{when(v.measured_at)}</dd>
            </div>
          )
        })}
      </dl>
    </section>
  )
}

function WithingsStatusLine({ status, fetching, onFetch }: { status: WithingsStatus; fetching: boolean; onFetch: () => void }) {
  const connect = () => { window.location.href = '/api/health/withings/connect' }
  if (!status.configured) {
    return (
      <div className="health-setup">
        <p><strong>Connect Withings once, about 10 minutes:</strong></p>
        <ol>
          <li>Open <a href="https://developer.withings.com/dashboard/" target="_blank" rel="noreferrer">developer.withings.com</a> and
            sign in with your Withings account.</li>
          <li>Create an application for the Public API. Give it a name, for example "Health dashboard".</li>
          <li>As callback URL, fill in exactly: <code>{status.callback_url}</code></li>
          <li>Copy its Client ID and Client Secret into the <code>.env</code> file in the app folder, as two lines:
            <br /><code>WITHINGS_CLIENT_ID=…</code><br /><code>WITHINGS_CLIENT_SECRET=…</code></li>
          <li>Reload this page and press Connect Withings.</li>
        </ol>
        {status.error && <p className="cap-error">{status.error}</p>}
      </div>
    )
  }
  if (!status.connected) {
    return (
      <div className="health-setup">
        {status.error && <p className="cap-error">{status.error}</p>}
        <button type="button" className="primary" onClick={connect}>{status.error ? 'Connect again' : 'Connect Withings'}</button>
      </div>
    )
  }
  const busy = fetching || status.fetching
  return (
    <div className="health-status">
      <span className="muted">
        {busy ? 'Fetching new measurements…' : status.last_fetch ? `Last fetch ${when(status.last_fetch)}` : 'Not fetched yet'}
      </span>
      <button type="button" className="quiet" disabled={busy} onClick={onFetch}>Fetch now</button>
      {status.error && <p className="cap-error">{status.error}</p>}
    </div>
  )
}

const RANGES = [30, 90, 365]
const TABS: [string, string][] = [['heart', 'Heart and vessels'], ['h10', 'Polar H10'], ['hrv', 'HRV'], ['recovery', 'Recovery'],
                                  ['stairs', 'Stairs'], ['sleep', 'Sleep'], ['fitbit', 'Fitbit'], ['body', 'Body composition'], ['parts', 'Body parts'],
                                  ['tape', 'Tape measure'], ['supplements', 'Supplements'], ['food', 'Food'],
                                  ['daily', 'Angina and ketones']]
const TAB_KEY = 'health-history-tab'
const ACTIVITY_KEY = 'health-h10-activity'

// a click on a day in any chart of the history opens that day in the table
const PickDay = createContext<((day: string) => void) | undefined>(undefined)
// each recording of the days of the history at its time: a chart of a value that has recordings shows each of them
const Recordings = createContext<RecordingPoint[]>([])

// one line chart under its caption; a single value names itself in the caption, two get a legend. Enlarge opens its
// analysis; a chart of a group (the arms of the muscle mass) names the group there
function Chart({ rows, title, unit, decimals, lines, note = '', group = '', connect = false, activity = null, band }: {
  rows: HealthDayRow[]; title: string; unit: string; decimals: number; lines: Line[]; note?: string; group?: string
  connect?: boolean; activity?: { id: number; name: string } | null; band?: { low: string; high: string; label: string }
}) {
  const onPick = useContext(PickDay)
  const points = useContext(Recordings)
  return (
    <figure>
      <figcaption>{title} <span className="muted">{[unit, note].filter(Boolean).join(', ')}</span>
        <EnlargeButton spec={{
          title: [group ? `${group}, ${inText(title)}` : title, activity?.name].filter(Boolean).join(', '), unit, decimals, lines,
          activity: activity?.id ?? null,
        }} /></figcaption>
      <LineChart rows={rows} label={title} unit={unit} decimals={decimals} lines={lines} onPick={onPick} connect={connect}
                 band={band} points={points} />
    </figure>
  )
}

const one = (key: string, label: string): Line[] => [{ key, label, kind: 's1' }]
// a value with its 7-day mean (the server's <key>_7d), the mean with one decimal more
const MEAN_NOTE = '7-day mean'
const withMean = (key: string, label: string, decimals: number): Line[] => [
  { key, label, kind: 's1' },
  { key: `${key}_7d`, label: '7-day mean', kind: 's2', decimals: decimals + 1, name: `${label}, 7-day mean` }]

// what a Polar H10 value of a session is, where its name does not say it
const H10_NOTES: Record<string, string> = {
  'h10:mean_hr': 'over the whole session',
}
// what each value of the Recovery tab is
const RECOVERY_NOTES: Record<string, string> = {
  'h10:drop_60': 'the HR at the stop minus the HR 60 s later',
  'h10:drop_120': 'the HR at the stop minus the HR 120 s later',
}
// what each value of the Stairs tab is
const STAIRS_NOTES: Record<string, string> = {
  'h10:stairs_time': 'from the first press of the marker to the last, the pauses included',
  'h10:stairs_pause_time': 'all the pauses together',
  'h10:stairs_hr_top': 'at the last press of the marker',
  'h10:stairs_drop_60': 'the HR at the 8th floor minus the HR 60 s later',
  'h10:stairs_drop_120': 'the HR at the 8th floor minus the HR 120 s later',
}
// what a value of the Fitbit tab is, where its name does not say it: a day adds up the sleeps that end on it
const FITBIT_NOTES: Record<string, string> = {
  'fitbit:asleep': 'a day: its sleeps added', 'fitbit:awake': 'a day: its sleeps added', 'fitbit:deep': 'a day: its sleeps added',
  'fitbit:light': 'a day: its sleeps added', 'fitbit:rem': 'a day: its sleeps added', 'fitbit:awakenings': 'a day: its sleeps added',
  'fitbit:kcal': 'the BMR included',
}
// what each value of the Sleep tab is: a day adds up the sleeps that end on it
const SLEEP_NOTES: Record<string, string> = {
  'sleep:total': 'a day: its sleeps added, without the time awake',
  'sleep:count': 'that end on the day',
  'sleep:in_bed': 'a day: its sleeps added',
  'sleep:awake': 'a day: its sleeps added',
  'sleep:deep': 'a day: its sleeps added, estimated by Sleep as Android',
  'sleep:light': 'a day: its sleeps added, estimated by Sleep as Android',
  'sleep:rem': 'a day: its sleeps with REM added, estimated by Sleep as Android',
  'sleep:deep_pct': 'of the time in a stage, a day: its sleeps added, estimated by Sleep as Android',
  'sleep:cycles': 'a day: its sleeps added, found by Sleep as Android',
}
// what a value of the Food tab is, where its name does not say it
const FOOD_NOTES: Record<string, string> = {
  'food:amount': '0 = usual', 'food:protein': '0 = usual', 'food:carbs': '0 = usual', 'food:fat': '0 = usual',
  'food:fasted': 'before the first food of the day', 'food:refeed_day': 'the 7 days after a fast',
}
// the food values drawn as bars from 0: the marks (yes or no) and the day numbers
const FOOD_BARS = new Set(['food:alcohol', 'food:salt', 'food:fast_day', 'food:refeed_day'])
// the values of a fast day: their charts only in a period with a fast day
const FAST_KEYS = new Set(['food:kcal', 'food:protein_g', 'food:carbs_g', 'food:fat_g'])

function FoodBars({ rows, value: key, label, onPick }: {
  rows: HealthDayRow[]; value: string; label: string; onPick: (day: string) => void
}) {
  const value = (r: HealthDayRow) => {
    const found = r[key]
    return typeof found === 'number' ? found : null
  }
  const mark = key === 'food:alcohol' || key === 'food:salt'
  const max = mark ? 1 : Math.max(1, ...rows.map(r => value(r) ?? 0))
  const text = (v: number) => (mark ? (v ? 'yes' : 'no') : key === 'food:fast_day' && v === 0 ? 'no fast' : `${inText(label)} ${v}`)
  return (
    <figure><figcaption>{label} {FOOD_NOTES[key] && <span className="muted">{FOOD_NOTES[key]}</span>}
      <EnlargeButton spec={{ title: label, unit: '', decimals: 0, lines: one(key, label) }} /></figcaption>
      <BarChart rows={rows} label={label} value={value} max={max} ticks={[0, max]} onPick={onPick}
                tickLabel={mark ? t => (t ? 'yes' : 'no') : undefined}
                format={r => { const v = value(r); return v === null ? 'not noted' : text(v) }} />
    </figure>
  )
}

function HistoryCard({ fetched, ketostix, polar }: { fetched: string | null; ketostix: KetostixLevel[]; polar: PolarStatus }) {
  const step = (level: number | null) => (level === null ? null : ketostix.findIndex(k => k.level === level))
  const [days, setDays] = useState(30)
  const [tab, setTab] = useState(() => {
    try { return localStorage.getItem(TAB_KEY) || 'heart' } catch { return 'heart' }
  })
  const choose = (next: string) => {
    setTab(next)
    try { localStorage.setItem(TAB_KEY, next) } catch { /* no storage: the choice lasts this visit */ }
  }
  // the activity of the HR charts of the Polar H10 tab: one at a time, never all sessions in one line (the HR of a session
  // depends on what was done); the last one chosen in this browser, else the activity of the newest session. Not HRV: its
  // tests have their own tab
  const [activityPick, setActivityPick] = useState<number | null>(() => {
    try { return Number(localStorage.getItem(ACTIVITY_KEY)) || null } catch { return null }
  })
  const activities = useQuery({ queryKey: ['polar-activities'], queryFn: getPolarActivities })
  const exercises = activities.data?.activities.filter(a => a.test !== 'hrv') ?? []
  const chosen = exercises.find(a => a.id === activityPick)
    ?? [...exercises].sort((a, b) => (b.last_at ?? '').localeCompare(a.last_at ?? ''))[0] ?? null
  const activity = chosen?.id ?? null
  const chooseActivity = (next: number) => {
    setActivityPick(next)
    try { localStorage.setItem(ACTIVITY_KEY, String(next)) } catch { /* no storage: the choice lasts this visit */ }
  }
  // the charts stay while those of another activity or period load, so the page keeps its place
  const history = useQuery({ queryKey: ['health-history', days, fetched, activity], queryFn: () => getHealthHistory(days, activity),
                             placeholderData: previous => previous })
  const rows = history.data?.days ?? []
  // each recording at its time, for the charts and the ketones
  const points = history.data?.points ?? []
  // a day with a value of an HRV test: without one, the HRV tab says how to make a test in place of empty charts
  const tested = rows.some(r => HRV.some(([key]) => typeof r[key] === 'number'))
  // the same for the Recovery tab and the HR recovery tests
  const recovered = rows.some(r => RECOVERY.some(([key]) => typeof r[key] === 'number'))
  // and for the Stairs tab and the stairs tests
  const climbed = rows.some(r => STAIRS.some(([key]) => typeof r[key] === 'number'))
  // and for the Sleep tab: each sleep with its times, for the sleep diary
  const sleeps = history.data?.sleeps ?? []
  const slept = rows.some(r => typeof r['sleep:total'] === 'number')
  const hasValue = (key: string) => rows.some(r => typeof r[key] === 'number')
  const worn = FITBIT.some(([key]) => hasValue(key))
  // a noted day has its fast day number (0 on a day that is not a fast day)
  const fed = hasValue('food:fast_day')
  const fasting = rows.some(r => Number(r['food:fast_day']) > 0)
  // the day clicked in a chart (or a row of the table): its row opens with each of its measurements
  const [picked, setPicked] = useState<string | null>(null)
  // the popup of all Polar H10 sessions, the same as on the Polar H10 card
  const [sessionsOpen, setSessionsOpen] = useState(false)
  // the amount of each supplement taken, a value of the day like the others
  const supplements = history.data?.supplements ?? []
  const supplementChoices = supplements.map((k): Value => [k.key, `${k.name} taken`, k.unit, 1])
  return (
    <PickDay.Provider value={setPicked}>
    <Recordings.Provider value={points}>
    <MoreChoices.Provider value={supplementChoices}>
    <article className="habit-card health-history">
      <div className="health-history-head">
        <h2>History</h2>
        <div className="research-switch" role="group" aria-label="Days shown">
          {RANGES.map(n => (
            <button key={n} type="button" aria-pressed={days === n} onClick={() => setDays(n)}>{n} days</button>
          ))}
        </div>
      </div>
      <div className="research-switch health-tabs" role="tablist" aria-label="Values shown">
        {TABS.map(([key, label]) => (
          <button key={key} type="button" role="tab" aria-selected={tab === key} aria-pressed={tab === key}
                  onClick={() => choose(key)}>{label}</button>
        ))}
      </div>
      {history.isError && <p className="cap-error">{history.error.message}</p>}
      {rows.length > 0 && tab === 'heart' && (
        <div className="health-charts">
          <Chart rows={rows} title="Systolic blood pressure" unit="mmHg" decimals={0} note={MEAN_NOTE}
                 lines={[{ key: 'systolic', label: 'Systolic', kind: 's1', name: 'Systolic blood pressure' },
                         { key: 'systolic_7d', label: '7-day mean', kind: 's2', decimals: 1,
                           name: 'Systolic blood pressure, 7-day mean' }]} />
          <Chart rows={rows} title="Diastolic blood pressure" unit="mmHg" decimals={0} note={MEAN_NOTE}
                 lines={[{ key: 'diastolic', label: 'Diastolic', kind: 's1', name: 'Diastolic blood pressure' },
                         { key: 'diastolic_7d', label: '7-day mean', kind: 's2', decimals: 1,
                           name: 'Diastolic blood pressure, 7-day mean' }]} />
          <Chart rows={rows} title="Pulse" unit="bpm" note={`blood pressure monitor, ${MEAN_NOTE}`} decimals={0}
                 lines={withMean('pulse', 'Pulse', 0)} />
          <Chart rows={rows} title="Heart rate (scale)" unit="bpm" note={`standing, ${MEAN_NOTE}`} decimals={0}
                 lines={withMean('heart_rate', 'Heart rate', 0)} />
          {VESSELS.map(([key, label, unit, decimals]) => (
            <Chart key={key} rows={rows} title={label} unit={unit} decimals={decimals} note={MEAN_NOTE}
                   lines={withMean(key, label, decimals)} />
          ))}
        </div>
      )}
      {rows.length > 0 && tab === 'h10' && (
        <div className="health-charts">
          {polar.sessions > 0 && (
            <button type="button" className="quiet polar-all-sessions" onClick={() => setSessionsOpen(true)}>
              All sessions ({polar.sessions})</button>
          )}
          <figure><figcaption>Time per week <span className="muted">min, by activity</span></figcaption>
            <PolarWeeks days={days} /></figure>
          <figure><figcaption>Time per week in each HR zone <span className="muted">min, every session but the HRV tests; the
            zones from 220 − age</span></figcaption>
            <ZoneWeeks days={days} /></figure>
          <figure><figcaption>Net energy expenditure per week <span className="muted">kcal, by activity, every session but the
            HRV tests: what the exercise adds to your BMR, a rough estimate from the HR; too high for weights</span></figcaption>
            <EnergyWeeks days={days} /></figure>
          <figure><figcaption>Activity per week <span className="muted">min, every session but the HRV tests: moderate
            64–77 % of the maximum HR, vigorous from 77 %; the WHO asks at least 150 a week</span></figcaption>
            <ActivityWeeks days={days} /></figure>
          <Chart rows={rows} title="Training load" unit="TRIMP" decimals={0}
                 note="Edwards TRIMP (the minutes in each zone × the zone number) of the 7 days up to each day, and of 28 days per week"
                 lines={[{ key: 'load:acute', label: '7 days', kind: 's1', name: 'Acute training load (7 days)' },
                         { key: 'load:chronic', label: '28 days, a week', kind: 's2', name: 'Chronic training load (28 days, a week)' }]} />
          <Chart rows={rows} title="Acute:chronic load ratio" unit="" decimals={2}
                 note="the 7-day load ÷ the 28-day load a week: about 0.8–1.3 is your usual training, above 1.5 much more than usual"
                 lines={one('load:ratio', 'Ratio')} />
          <label className="polar-filter">Activity
            <select value={chosen?.id ?? ''} onChange={e => chooseActivity(Number(e.target.value))}>
              {exercises.map(a => <option key={a.id} value={a.id}>{a.name}</option>)}
            </select>
          </label>
          {chosen && H10.map(([key, label, unit, decimals]) => (
            <Chart key={key} rows={rows} title={label} unit={unit} decimals={decimals} lines={one(key, label)} connect
                   note={[H10_NOTES[key], `${chosen.name} only`].filter(Boolean).join(', ')} activity={{ id: chosen.id, name: chosen.name }} />
          ))}
        </div>
      )}
      {rows.length > 0 && tab === 'hrv' && !tested && (
        <p className="muted">No HRV test with values in these days. To make one: press HRV test on the Polar H10 card. A phone
          session counts too when you give it the activity HRV.</p>
      )}
      {rows.length > 0 && tab === 'hrv' && tested && (
        <div className="health-charts">
          {/* not `connect`: the 7-day mean is a value of each day, and its line breaks after 7 days without a test. ln RMSSD
              has the normal range, and its coefficient of variation follows it */}
          {HRV.map(([key, label, unit, decimals]) => (key === 'h10:ln_rmssd' ? (
            <Fragment key={key}>
              <Chart rows={rows} title="ln RMSSD" unit="" decimals={2}
                     note={`${HRV_BETTER[key]}; normal range: 60-day mean ± 0.5 SD`}
                     lines={[{ key, label: 'Test', kind: 's1', name: 'ln RMSSD', dots: true },
                             { key: 'h10:ln_rmssd_7d', label: '7-day mean', kind: 's2', name: 'ln RMSSD, 7-day mean' }]}
                     band={{ low: 'h10:ln_rmssd_low', high: 'h10:ln_rmssd_high', label: 'Normal range' }} />
              <Chart rows={rows} title="Coefficient of variation of ln RMSSD" unit="%" decimals={1}
                     note="lower is better: the body copes more evenly; from the tests of the 7 days up to each day, at least 3"
                     lines={[{ key: 'h10:ln_rmssd_cv7', label: 'CV', kind: 's1', name: 'ln RMSSD, 7-day coefficient of variation' }]} />
            </Fragment>
          ) : (
            <Chart key={key} rows={rows} title={label} unit={unit} decimals={decimals} note={HRV_BETTER[key]}
                   lines={[{ key, label: 'Test', kind: 's1', name: `${label} (HRV test)`, dots: true },
                           { key: `${key}_7d`, label: '7-day mean', kind: 's2', name: `${label}, 7-day mean` }]} />
          )))}
        </div>
      )}
      {rows.length > 0 && tab === 'recovery' && !recovered && (
        <p className="muted">No HR recovery test with values in these days. To make one: press HR recovery test on the Polar
          H10 card. A phone session counts too when you give it the activity HR recovery: in the phone app, press the marker
          when the effort ends.</p>
      )}
      {rows.length > 0 && tab === 'recovery' && recovered && (
        <div className="health-charts">
          {RECOVERY.map(([key, label, unit, decimals]) => (
            <Chart key={key} rows={rows} title={label} unit={unit} decimals={decimals} lines={one(key, label)} connect
                   note={RECOVERY_NOTES[key]} />
          ))}
        </div>
      )}
      {rows.length > 0 && tab === 'stairs' && !climbed && (
        <p className="muted">No stairs test with values in these days. To make one, follow these steps:</p>
      )}
      {rows.length > 0 && tab === 'stairs' && climbed && (
        <div className="health-charts">
          {STAIRS.map(([key, label, unit, decimals]) => (
            <Chart key={key} rows={rows} title={label} unit={unit} decimals={decimals} lines={one(key, label)} connect
                   note={STAIRS_NOTES[key]} />
          ))}
        </div>
      )}
      {rows.length > 0 && tab === 'stairs' && (
        <details className="health-stairs-steps" open={!climbed}>
          <summary>How to make a stairs test</summary>
          <ol className="polar-test-steps">{TEST_STEPS.stairs.map(step => <li key={step}>{step}</li>)}</ol>
        </details>
      )}
      {rows.length > 0 && tab === 'sleep' && !slept && (
        <p className="muted">No sleep in these days. Press Import sleep on the Sleep card.</p>
      )}
      {rows.length > 0 && tab === 'sleep' && slept && <SleepPattern days={days} fetched={fetched} />}
      {rows.length > 0 && tab === 'sleep' && slept && (
        <div className="health-charts">
          <figure><figcaption>Sleep times <span className="muted">each sleep from bedtime to wake time</span>
            <SleepTimesEnlarge /></figcaption>
            <SleepTimesChart days={rows.map(r => r.day)} sleeps={sleeps} onPick={setPicked} /></figure>
          {SLEEP.map(([key, label, unit, decimals]) => (
            <Chart key={key} rows={rows} title={label} unit={unit} decimals={decimals} lines={one(key, label)}
                   note={SLEEP_NOTES[key]} />
          ))}
        </div>
      )}
      {rows.length > 0 && tab === 'fitbit' && !worn && (
        <p className="muted">No Fitbit data in these days. Press Download Fitbit on the Fitbit card.</p>
      )}
      {rows.length > 0 && tab === 'fitbit' && worn && (
        <div className="health-charts">
          {FITBIT_GROUPS.map(([group, values]) => {
            const shown = values.filter(([key]) => hasValue(key))
            return shown.length > 0 && (
              <Fragment key={group}>
                <h3 className="health-chart-group">{group}</h3>
                {shown.map(([key, label, unit, decimals]) => (
                  <Chart key={key} rows={rows} title={label} unit={unit} decimals={decimals} lines={one(key, label)}
                         note={FITBIT_NOTES[key]} />
                ))}
              </Fragment>
            )
          })}
        </div>
      )}
      {rows.length > 0 && tab === 'body' && (
        <div className="health-charts">
          {COMPOSITION.filter(([key]) => !key.endsWith('cellular_water')).map(([key, label, unit, decimals]) => (
            <Fragment key={key}>
              <Chart rows={rows} title={label} unit={unit} decimals={decimals}
                     note={TREND_NOTE}
                     lines={[{ key, label: key === 'weight' ? 'Weigh-in' : 'Measurement', kind: 's1', name: label, dots: true },
                             { key: `${key}_trend`, label: 'Trend', kind: 's2', name: `Trend ${inText(label)}` }]} />
              {/* the two kinds of water follow the water, each in its own color: the measurements as dots, the trend as
                  a line */}
              {key === 'hydration' && (
                <Chart rows={rows} title="Intracellular and extracellular water" unit="kg" note={TREND_NOTE}
                       decimals={1}
                       lines={[{ key: 'intracellular_water', label: 'Intracellular', kind: 's1', name: 'Intracellular water',
                                 dots: true },
                               { key: 'intracellular_water_trend', label: 'Intracellular trend', kind: 's1',
                                 name: 'Trend intracellular water' },
                               { key: 'extracellular_water', label: 'Extracellular', kind: 's2', name: 'Extracellular water',
                                 dots: true },
                               { key: 'extracellular_water_trend', label: 'Extracellular trend', kind: 's2',
                                 name: 'Trend extracellular water' }]} />
              )}
            </Fragment>
          ))}
        </div>
      )}
      {rows.length > 0 && tab === 'parts' && (
        <div className="health-charts">
          {PART_KINDS.map(([kind, label]) => (
            <section key={kind} className="health-part-row">
              <h3>{label} <span className="muted">kg</span></h3>
              <div className="health-part-charts">
                <Chart rows={rows} title="Arms" unit="kg" decimals={2} group={`${label} mass`}
                       lines={[{ key: `${kind}:left_arm`, label: 'Left', kind: 's1', name: `${label} mass, left arm` },
                               { key: `${kind}:right_arm`, label: 'Right', kind: 's2', name: `${label} mass, right arm` }]} />
                <Chart rows={rows} title="Legs" unit="kg" decimals={2} group={`${label} mass`}
                       lines={[{ key: `${kind}:left_leg`, label: 'Left', kind: 's1', name: `${label} mass, left leg` },
                               { key: `${kind}:right_leg`, label: 'Right', kind: 's2', name: `${label} mass, right leg` }]} />
                <Chart rows={rows} title="Trunk" unit="kg" decimals={1} group={`${label} mass`}
                       lines={one(`${kind}:trunk`, 'Trunk')} />
              </div>
            </section>
          ))}
        </div>
      )}
      {rows.length > 0 && tab === 'tape' && (
        <div className="health-charts">
          {/* a chart each for the sites without a side; the left and right sides share one */}
          {TAPE.filter(([key]) => !TAPE_PAIRS.some(([site]) => key.startsWith(`tape:${site}_`)))
            .map(([key, label, unit, decimals]) => (
            <Chart key={key} rows={rows} title={label} unit={unit} note="tape measure" decimals={decimals} lines={one(key, label)}
                   connect />
          ))}
          {TAPE_PAIRS.map(([site, label]) => (
            <Chart key={site} rows={rows} title={label} unit="cm" note="tape measure" decimals={1} connect
                   lines={[{ key: `tape:${site}_left`, label: 'Left', kind: 's1', name: `${label}, left` },
                           { key: `tape:${site}_right`, label: 'Right', kind: 's2', name: `${label}, right` }]} />
          ))}
          <TapeTable rows={rows} />
        </div>
      )}
      {rows.length > 0 && tab === 'supplements' && (
        <SupplementHistory days={days} fetched={fetched} />
      )}
      {rows.length > 0 && tab === 'food' && !fed && (
        <p className="muted">No food noted in these days. Note it in the Food part of the Today card.</p>
      )}
      {rows.length > 0 && tab === 'food' && fed && (
        <div className="health-charts">
          {FOOD.filter(([key]) => !FAST_KEYS.has(key) || fasting).map(([key, label, unit, decimals]) => (FOOD_BARS.has(key)
            ? <FoodBars key={key} rows={rows} value={key} label={label} onPick={setPicked} />
            : <Chart key={key} rows={rows} title={label} unit={unit} decimals={decimals} lines={one(key, label)}
                     note={FOOD_NOTES[key]} />))}
        </div>
      )}
      {rows.length > 0 && tab === 'daily' && (
        <div className="health-charts">
          <figure><figcaption>Angina pectoris <span className="muted">0 to 5</span>
            <EnlargeButton spec={{ title: 'Angina pectoris', unit: 'score', decimals: 1, lines: one('angina', 'Angina') }} />
          </figcaption>
            <BarChart rows={rows} label="Angina scores" value={r => r.angina} max={5} ticks={[0, 5]} onPick={setPicked}
                      marked={r => r.angina_not_measured}
                      format={r => (r.angina !== null ? `angina ${r.angina}`
                        : r.angina_not_measured ? 'not measured (no exertion)' : 'no score')} /></figure>
          <figure><figcaption>Ketones <span className="muted">Ketostix, mmol/L</span>
            <EnlargeButton spec={{ title: 'Ketones', unit: 'mmol/L', decimals: 1, lines: one('ketones', 'Ketones') }} />
          </figcaption>
            <BarChart rows={rows} label="Ketostix readings" value={r => step(r.ketones)} max={ketostix.length - 1}
                      ticks={ketostix.map((_, i) => i)} tickLabel={t => String(ketostix[t]?.level ?? '')} onPick={setPicked}
                      format={r => (r.ketones === null ? 'no reading'
                        : `${ketostix[step(r.ketones) ?? 0]?.label ?? ''} ${r.ketones} mmol/L`
                          + (r.ketone_readings > 1 ? `, the highest of ${r.ketone_readings}` : ''))}
                      readings={points.flatMap(p => (typeof p.ketones === 'number' ? [{
                        at: p.at, day: p.day, value: step(p.ketones) ?? 0,
                        text: `${ketostix[step(p.ketones) ?? 0]?.label ?? ''} ${p.ketones} mmol/L` }] : []))} />
          </figure>
        </div>
      )}
      {rows.length > 0 && <HistoryTable rows={rows} picked={picked} onPick={day => setPicked(day === picked ? null : day)} />}
      <RemovedList removed={history.data?.removed ?? []} />
      {sessionsOpen && <SessionsDialog first="list" button={polar.button} onClose={() => setSessionsOpen(false)} />}
    </article>
    </MoreChoices.Provider>
    </Recordings.Provider>
    </PickDay.Provider>
  )
}

// the tape measurements of the period, one row per measurement day, newest first; each value with its change since the
// measurement before it
function TapeTable({ rows }: { rows: HealthDayRow[] }) {
  const days = rows.filter(r => TAPE.some(([key]) => typeof r[key] === 'number'))
  if (!days.length) return <p className="stats-empty">No tape measurements in these days.</p>
  const before = (index: number, key: string) => {
    for (let k = index - 1; k >= 0; k -= 1) {
      const v = days[k][key]
      if (typeof v === 'number') return v
    }
    return null
  }
  return (
    <div className="health-table health-tape-table">
      <table>
        <thead>
          <tr><th scope="col">Day</th>{TAPE.map(([key, label]) => <th key={key} scope="col">{label}</th>)}</tr>
        </thead>
        <tbody>
          {days.map((r, i) => (
            <tr key={r.day}>
              <th scope="row">{longDay(r.day)}</th>
              {TAPE.map(([key]) => {
                const v = r[key]
                const prev = before(i, key)
                const change = typeof v === 'number' && prev !== null ? v - prev : null
                return (
                  <td key={key} className="num">{typeof v === 'number' ? v.toFixed(1) : ''}
                    {change !== null && <small className="muted"> {change > 0 ? '+' : ''}{change.toFixed(1)}</small>}</td>
                )
              })}
            </tr>
          )).reverse()}
        </tbody>
      </table>
    </div>
  )
}

function HistoryTable({ rows, picked, onPick }: { rows: HealthDayRow[]; picked: string | null; onPick: (day: string) => void }) {
  const fixed = (v: unknown, decimals: number) => (typeof v === 'number' ? v.toFixed(decimals) : '')
  const [open, setOpen] = useState(false)
  const pickedRow = useRef<HTMLTableRowElement>(null)
  // a day clicked in a chart opens the table and brings its row into view; the row of a closed table has no place
  // on the page yet, so the scroll waits for the table to be open
  useEffect(() => {
    if (picked) setOpen(true)
  }, [picked])
  useEffect(() => {
    if (picked && open) pickedRow.current?.scrollIntoView({ block: 'center', inline: 'start', behavior: 'smooth' })
  }, [picked, open])
  return (
    <details className="health-table-wrap" open={open} onToggle={e => setOpen(e.currentTarget.open)}>
      <summary>Table, one row per day</summary>
      <div className="health-table">
        <table>
          <thead>
            <tr>
              <th scope="col">Day</th><th scope="col">Angina</th><th scope="col">Ketones</th>
              <th scope="col">Blood pressure</th>
              <th scope="col">Pulse</th><th scope="col">Weight</th><th scope="col">Fat %</th><th scope="col">Muscle</th>
              <th scope="col">Visceral fat</th>
            </tr>
          </thead>
          <tbody>
            {[...rows].reverse().map(r => [
              <tr key={r.day} ref={r.day === picked ? pickedRow : undefined} className={r.day === picked ? 'picked' : ''}>
                <td>
                  <button type="button" className="link-button health-day-button" aria-expanded={r.day === picked}
                          onClick={() => onPick(r.day)}>{longDay(r.day)}</button>
                </td>
                <td className="num">{r.angina ?? (r.angina_not_measured && <small className="muted">not measured</small>)}</td>
                <td className="num">{r.ketones ?? ''}
                  {r.ketone_readings > 1 && <small className="muted"> ({r.ketone_readings})</small>}</td>
                <td className="num">{r.systolic !== null ? `${fixed(r.systolic, 0)}/${fixed(r.diastolic, 0)}` : ''}
                  {r.readings > 1 && <small className="muted"> ({r.readings})</small>}</td>
                <td className="num">{fixed(r.pulse, 0)}</td>
                <td className="num">{fixed(r.weight, 1)}</td>
                <td className="num">{fixed(r.fat_ratio, 1)}</td>
                <td className="num">{fixed(r.muscle_mass, 1)}</td>
                <td className="num">{fixed(r.visceral_fat, 1)}</td>
              </tr>,
              r.day === picked && (
                <tr key={`${r.day}-measures`} className="picked health-day-detail">
                  <td colSpan={9}>{foodLine(r) && <p className="health-day-food">{foodLine(r)}</p>}
                    <DayMeasurements day={r.day} /><DaySessions day={r.day} /></td>
                </tr>
              ),
            ])}
          </tbody>
        </table>
      </div>
    </details>
  )
}

// what the user removed as wrong, each with Put back: a wrong click is easy to undo
function RemovedList({ removed }: { removed: HealthMeasurement[] }) {
  const forget = useForget()
  const back = useMutation({ mutationFn: (grpids: number[]) => showMeasurement(grpids), onSuccess: forget })
  if (removed.length === 0) return null
  return (
    <section className="health-removed">
      <h3>Removed measurements <span className="muted">not shown in the charts, the table and the latest values</span></h3>
      <ul>
        {removed.map(m => (
          <li key={m.grpids.join('-')}>
            <span className="health-measure-time">{longDay(m.day ?? m.measured_at.slice(0, 10))}, {m.measured_at.slice(11, 16)}</span>
            <span>{summary(m)}</span>
            <button type="button" className="quiet" disabled={back.isPending} onClick={() => back.mutate(m.grpids)}>Put back</button>
          </li>
        ))}
      </ul>
      {back.isError && <p className="cap-error">{back.error.message}</p>}
    </section>
  )
}
