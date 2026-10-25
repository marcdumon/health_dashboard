import { useMutation, useQuery } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'

import {
  addPolarActivity, getPolarActivities, getPolarDay, getPolarSessions, getPolarWaiting, importPolar, removePolarActivity,
  removePolarSession, removePolarSessions, renamePolarActivity, showPolarSession, showPolarSessions,
  type HealthView,
  type PolarActivity, type PolarHrvCard, type PolarRecoveryCard, type PolarSession, type PolarStairsCard, type PolarStatus,
} from './api'
import { closeOutside } from './dialog'
import { useForget } from './HealthMeasures'
import { ActivityChoice, clockOf, minutes, SessionDetail, StairsValues, started } from './HealthPolarDetail'
import {
  isRecording, LivePart, StartRecording, TEST_TITLES, usePolarLive, type PolarTestKind,
} from './HealthPolarRecord'
import { TestDialog, useHrvAlarm } from './HealthPolarTests'

// the Polar H10 chest strap, as the Withings card: the last session up to the day of the page, the last HRV test of the
// page's day and the 7 days before it, the last HR recovery test and stairs test of the page's day and the 30 days
// before it, how many sessions wait in the folders on the PC, and the import button: Import from phone and PC moves the
// phone app's files from the phone to the PC, then reads the files of the phone app and the logger's sessions on the PC.
// All sessions opens a popup: every session with its activity; a click opens its detail. Start recording only records;
// HRV test, HR recovery test and Stairs test open the popup of the test (the stairs test is recorded with the phone, so
// its button needs no logger). While a recording with the logger runs, its live part takes the
// place of the values.

const longDay = (day: string) =>
  new Date(`${day}T12:00:00`).toLocaleDateString('en-GB', { weekday: 'long', day: 'numeric', month: 'long' })
const whole = (v: number | null) => (v === null ? '—' : String(Math.round(v)))
const waitText = ({ sessions, recalc }: { sessions: number; recalc: boolean }, button: string) => (recalc
  ? `All ${sessions} sessions wait for ${button}: the calculations changed.`
  : `${sessions} ${sessions === 1 ? 'session' : 'sessions'} in the folders on the PC ${sessions === 1 ? 'waits' : 'wait'} for `
    + button)
const PAGE = 20
// the line of the import: the move from the phone (the file that comes in now), the reading of the files on the PC, or
// the last import with where it read from
const importLine = (s: PolarStatus) => {
  if (s.running && s.phase === 'copy') {
    return s.total ? `Moving the files from the phone… file ${Math.min(s.done + 1, s.total)} of ${s.total}`
      : 'Moving the files from the phone…'
  }
  if (s.running) return `Reading the files on the PC… file ${s.done} of ${s.total}`
  if (!s.last_import) return 'Not imported yet'
  return `Last import ${started(s.last_import)}${s.last_note ? `, ${s.last_note}` : ''}`
}

export function PolarCard({ data, onChange }: { data: HealthView; onChange: (v: HealthView) => void }) {
  const status = data.polar
  const forget = useForget()
  // the sessions popup: its list, or the detail of one session; the popup of a test
  const [listAt, setListAt] = useState<'list' | number | null>(null)
  const [testOpen, setTestOpen] = useState<PolarTestKind | null>(null)
  // an import that ends before its answer comes never shows as running: the sessions are read again at once
  const start = useMutation({ mutationFn: () => importPolar(data.day),
                              onSuccess: view => { onChange(view); if (!view.polar.running) forget() } })
  // when an import ends, the sessions and the history are read again
  const running = useRef(status.running)
  useEffect(() => {
    if (running.current && !status.running) forget()
    running.current = status.running
  }, [status.running])
  // each session that a running import saves shows at once
  const saved = useRef(status.saved)
  useEffect(() => {
    if (status.running && status.saved !== saved.current) forget()
    saved.current = status.saved
  }, [status.saved])
  const waiting = useQuery({ queryKey: ['polar-waiting'], queryFn: getPolarWaiting, enabled: status.configured })
  const isToday = data.day === data.today
  const last = status.last_exercise
  const hrv = status.last_hrv
  const recovery = status.last_recovery
  const stairs = status.last_stairs
  const { live, keep } = usePolarLive()
  const recording = isRecording(live)
  useHrvAlarm(live)
  return (
    <article className="habit-card health-latest health-polar">
      <h2>Polar H10{!isToday && <span className="muted"> up to {longDay(data.day)}</span>}</h2>
      {live && recording ? <LivePart live={live} keep={keep} onOpenTest={setTestOpen} /> : (
        <>
          {last && (
            <Values title="Last session"
                    when={[started(last.start_at), last.activity, minutes(last.seconds)].filter(Boolean).join(', ')}
                    items={[['Mean HR', whole(last.mean_hr), 'bpm'], ['Maximum HR', whole(last.max_hr), 'bpm'],
                            // an estimate from the HR: the detail of the session says how
                            ...(last.energy === null ? [] : [['Net energy expenditure', String(last.energy), 'kcal']] as Item[])]} />
          )}
          {hrv && <HrvCard test={hrv} />}
          {recovery && <RecoveryCard test={recovery} />}
          {stairs && <StairsCard test={stairs} />}
          {status.configured && !last && !hrv && !recovery && !stairs && (
            <p className="muted">{status.sessions ? 'No session up to this day.' : `No sessions yet. Press ${status.button}.`}</p>
          )}
        </>
      )}
      {status.configured ? (
        <div className="health-status polar-status">
          <p className="muted polar-import-line">
            {importLine(status)}
            {!status.running && waiting.data && waiting.data.sessions > 0 && (
              <strong className="polar-waiting"> {waitText(waiting.data, status.button)}</strong>
            )}
          </p>
          {/* the sessions on the left, a new recording on the right */}
          <div className="polar-buttons">
            <div className="polar-group">
              {status.sessions > 0 && (
                <button type="button" className="quiet" onClick={() => setListAt('list')}>All sessions ({status.sessions})</button>
              )}
              <button type="button" className="quiet" disabled={start.isPending || status.running}
                      onClick={() => start.mutate()}>{status.button}</button>
            </div>
            {!recording && (
              <div className="polar-group">
                {live?.configured && <StartRecording keep={keep} />}
                {((live?.configured ? ['hrv', 'recovery', 'stairs'] : ['stairs']) as PolarTestKind[]).map(test => (
                  <button key={test} type="button" className="quiet" onClick={() => setTestOpen(test)}>{TEST_TITLES[test]}</button>
                ))}
              </div>
            )}
          </div>
          {status.error && <p className="cap-error">{status.error}</p>}
          {/* why the phone was not read: only after a press on this page, so a reload does not show it again */}
          {start.isSuccess && !status.running && status.phone_error && <p className="cap-error">{status.phone_error}</p>}
          {start.isError && <p className="cap-error">{start.error.message}</p>}
          {waiting.isError && <p className="cap-error">{waiting.error.message}</p>}
          {live && !recording && live.error && <p className="cap-error">{live.error}</p>}
          {live && !live.configured && (
            <p className="muted polar-hint">To record from here: add <code>logger_project = '/folder/of/polar_h10_logger'</code> to
              the <code>[polar]</code> part of <code>health_dashboard.toml</code>, then restart the app.</p>
          )}
        </div>
      ) : (
        <div className="health-setup">
          <p><strong>Set the two folders once:</strong></p>
          <ol>
            <li>Open <code>health_dashboard.toml</code> in the app folder.</li>
            <li>Add these three lines, with your own folders:<br /><code>[polar]</code><br />
              <code>phone_dir = '/folder/of/the/Polar Sensor Logger files'</code><br />
              <code>logger_dir = '/folder/of/the/logger sessions'</code></li>
            <li>Restart the app and reload this page.</li>
          </ol>
        </div>
      )}
      {listAt !== null && <SessionsDialog first={listAt} button={status.button} onClose={() => setListAt(null)} />}
      {testOpen && (
        <TestDialog test={testOpen} live={live} keep={keep} onClose={() => setTestOpen(null)}
                    onOpenSession={id => { setTestOpen(null); setListAt(id) }} />
      )}
    </article>
  )
}

// the last HRV test of the page's day or the 7 days before it: its values, or why it has none, and the status of ln RMSSD
function HrvCard({ test }: { test: PolarHrvCard }) {
  const s = test.status
  const cv = s.cv_7d === null ? '' : ` Coefficient of variation over 7 days: ${s.cv_7d.toFixed(1)} %.`
  const line = s.low === null ? `${s.missing} more ${s.missing === 1 ? 'test' : 'tests'} for your normal range.${cv}`
    : s.mean_7d === null || s.high === null ? cv.trim()
    : `7-day mean ln RMSSD ${s.mean_7d.toFixed(2)}: ${s.status} your normal range ${s.low.toFixed(2)}–${s.high.toFixed(2)}.${cv}`
  return (
    <section className="health-group">
      <h3>Last HRV test <span className="muted">{started(test.start_at)}</span></h3>
      {test.reason ? <p className="muted">{test.reason}</p> : (
        <dl className="health-values">
          <div><dt>RMSSD</dt><dd>{test.rmssd === null ? '—' : test.rmssd.toFixed(1)} <small>ms</small></dd></div>
          <div><dt>ln RMSSD</dt><dd>{test.ln_rmssd === null ? '—' : test.ln_rmssd.toFixed(2)}</dd></div>
          <div><dt>Resting HR</dt><dd>{whole(test.rest_hr)} <small>bpm</small></dd></div>
        </dl>
      )}
      {line && <p className="polar-hrv-status">{line}</p>}
    </section>
  )
}

// the last HR recovery test of the page's day or the 30 days before it: its values, or why it has none
function RecoveryCard({ test }: { test: PolarRecoveryCard }) {
  return (
    <section className="health-group">
      <h3>Last HR recovery test <span className="muted">{started(test.start_at)}</span></h3>
      {test.reason ? <p className="muted">{test.reason}</p> : (
        <dl className="health-values">
          <div><dt>HR at the stop</dt><dd>{whole(test.hr_stop)} <small>bpm</small></dd></div>
          <div><dt>Drop in 60 s</dt><dd>{whole(test.drop_60)} <small>bpm</small></dd></div>
          <div><dt>Drop in 120 s</dt><dd>{whole(test.drop_120)} <small>bpm</small></dd></div>
        </dl>
      )}
    </section>
  )
}

// the last stairs test of the page's day or the 30 days before it: its values, or why it has none
function StairsCard({ test }: { test: PolarStairsCard }) {
  return (
    <section className="health-group">
      <h3>Last stairs test <span className="muted">{started(test.start_at)}</span></h3>
      {test.reason ? <p className="muted">{test.reason}</p> : <StairsValues stairs={test} />}
    </section>
  )
}

type Item = [string, string, string]

// the values of a session, as the Withings card shows its values
function Values({ title, when, items }: { title: string; when: string; items: Item[] }) {
  return (
    <section className="health-group">
      <h3>{title} <span className="muted">{when}</span></h3>
      <dl className="health-values">
        {items.map(([label, value, unit]) => (
          <div key={label}><dt>{label}</dt><dd>{value} {unit && <small>{unit}</small>}</dd></div>
        ))}
      </dl>
    </section>
  )
}

// every session in a popup: the list (Show more for older ones, the removed sessions under them), the detail of one
// session, or the list of activities; the list keeps its place and its selection behind the other two. It opens on
// `first`: the list, or the detail of one session (from the result of a test). The Polar H10 tab of the history opens it too
export function SessionsDialog({ first, button, onClose }: { first: 'list' | number; button: string; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null)
  useEffect(() => { dialog.current?.showModal() }, [])
  const [view, setView] = useState<'list' | 'activities' | number>(first)
  // the detail and the activities start at the top; Back to all sessions comes back to the place in the list
  const place = useRef(0)
  const show = (next: 'list' | 'activities' | number) => {
    if (view === 'list' && dialog.current) place.current = dialog.current.scrollTop
    setView(next)
  }
  useEffect(() => { dialog.current?.scrollTo(0, view === 'list' ? place.current : 0) }, [view])
  const title = view === 'list' ? 'Polar H10 sessions' : view === 'activities' ? 'Activities' : 'Polar H10 session'
  return createPortal(
    <dialog ref={dialog} className="health-regime health-sessions accent-health" onClose={onClose}
            aria-labelledby="sessions-title" {...closeOutside}>
      <div className="analysis-head">
        <h2 id="sessions-title">{title}</h2>
        <div className="polar-head-actions">
          {view === 'list'
            ? <button type="button" className="quiet" onClick={() => show('activities')}>Activities</button>
            : <button type="button" className="quiet" onClick={() => show('list')}>Back to all sessions</button>}
          <button type="button" className="quiet" onClick={() => dialog.current?.close()}>Close</button>
        </div>
      </div>
      <div hidden={view !== 'list'}><SessionList button={button} onOpen={show} /></div>
      {view === 'activities' && <ActivitiesEditor />}
      {typeof view === 'number' && <SessionDetail id={view} />}
    </dialog>,
    document.body,
  )
}

function SessionList({ button, onOpen }: { button: string; onOpen: (id: number) => void }) {
  const [count, setCount] = useState(PAGE)
  const found = useQuery({ queryKey: ['polar-sessions', count], queryFn: () => getPolarSessions(0, count),
                           placeholderData: previous => previous })
  if (found.isError) return <p className="cap-error">{found.error.message}</p>
  if (!found.data) return null
  const { sessions, total, removed } = found.data
  return (
    <>
      {sessions.length === 0 ? <p className="muted">No sessions yet. Close this popup, and press {button}.</p>
        : <SessionTable sessions={sessions} onOpen={onOpen}
                        select={{ total, onAll: () => setCount(Math.max(count, total)) }} />}
      {total > sessions.length && (
        <button type="button" className="quiet polar-more" onClick={() => setCount(count + PAGE)}>Show more</button>
      )}
      {removed.length > 0 && (
        <section className="health-removed">
          <h3>Removed sessions <span className="muted">not in the history and the analysis</span></h3>
          <SessionTable sessions={removed} onOpen={onOpen} select={{ total: removed.length }} />
        </section>
      )}
    </>
  )
}

// the activities in list order: rename in place (saved when the field loses the focus), remove an unused one, add one
function ActivitiesEditor() {
  const forget = useForget()
  const list = useQuery({ queryKey: ['polar-activities'], queryFn: getPolarActivities })
  const [name, setName] = useState('')
  const add = useMutation({ mutationFn: () => addPolarActivity(name.trim()), onSuccess: () => { setName(''); forget() } })
  const remove = useMutation({ mutationFn: removePolarActivity, onSuccess: forget })
  return (
    <div className="polar-activities-editor">
      {list.isError && <p className="cap-error">{list.error.message}</p>}
      <ul className="polar-activities">
        {list.data?.activities.map(a => (
          <ActivityRow key={a.id} activity={a} busy={remove.isPending} onRemove={() => remove.mutate(a.id)} />
        ))}
      </ul>
      {remove.isError && <p className="cap-error">{remove.error.message}</p>}
      <form className="polar-activity-add" onSubmit={e => { e.preventDefault(); if (name.trim()) add.mutate() }}>
        <input value={name} maxLength={40} placeholder="New activity" aria-label="Name of the new activity"
               onChange={e => setName(e.target.value)} />
        <button type="submit" className="quiet" disabled={!name.trim() || add.isPending}>Add</button>
      </form>
      {add.isError && <p className="cap-error">{add.error.message}</p>}
    </div>
  )
}

function ActivityRow({ activity, busy, onRemove }: { activity: PolarActivity; busy: boolean; onRemove: () => void }) {
  const forget = useForget()
  const [name, setName] = useState(activity.name)
  const rename = useMutation({ mutationFn: () => renamePolarActivity(activity.id, name.trim()), onSuccess: forget })
  const save = () => {
    if (name.trim() && name.trim() !== activity.name) rename.mutate()
    else setName(activity.name)
  }
  return (
    <li>
      <input value={name} maxLength={40} aria-label={`Name of the activity ${activity.name}`} onChange={e => setName(e.target.value)}
             onBlur={save} onKeyDown={e => { if (e.key === 'Enter') e.currentTarget.blur() }} />
      <span className="muted">{activity.sessions} {activity.sessions === 1 ? 'session' : 'sessions'}</span>
      {activity.test ? <span className="muted">{TEST_TITLES[activity.test as PolarTestKind] ?? 'Test'}</span>
        : <button type="button" className="quiet" disabled={busy} onClick={onRemove}>Remove</button>}
      {rename.isError && <p className="cap-error">{rename.error.message}</p>}
    </li>
  )
}

// the selection of a list of the popup: how many sessions the list has, and how to load all of them when only a part shows
type Select = { total: number; onAll?: () => void }

function SessionTable({ sessions, select, onOpen }: { sessions: PolarSession[]; select?: Select; onOpen?: (id: number) => void }) {
  const forget = useForget()
  const mark = useMutation({ mutationFn: (s: PolarSession) => (s.removed ? showPolarSession(s.id) : removePolarSession(s.id)),
                             onSuccess: forget })
  const [picked, setPicked] = useState<Set<number>>(new Set())
  // Select all: also the sessions that come later (all of them loaded, or Show more) are selected
  const [all, setAll] = useState(false)
  useEffect(() => { if (all) setPicked(new Set(sessions.map(s => s.id))) }, [all, sessions])
  const chosen = sessions.filter(s => picked.has(s.id))
  const showing = sessions.every(s => s.removed)
  const many = useMutation({ mutationFn: (ids: number[]) => (showing ? showPolarSessions(ids) : removePolarSessions(ids)),
                             onSuccess: () => { setAll(false); setPicked(new Set()); forget() } })
  const loading = all && select !== undefined && sessions.length < select.total
  const everyOne = chosen.length > 0 && chosen.length === sessions.length && !loading
  const tickAll = (on: boolean) => {
    setAll(on)
    if (on) select?.onAll?.()
    else setPicked(new Set())
  }
  const tick = (id: number, on: boolean) => {
    setAll(false)
    setPicked(old => { const next = new Set(old); if (on) next.add(id); else next.delete(id); return next })
  }
  const n = chosen.length
  const sessionsWord = `session${n === 1 ? '' : 's'}`
  const label = showing ? (n ? `Show ${n} ${sessionsWord} again` : 'Show selected again')
    : (n ? `Remove ${n} ${sessionsWord}` : 'Remove selected')
  // a click on a row opens its detail, but not a click on its checkbox, its activity or its button
  const open = (e: React.MouseEvent, id: number) => {
    if (onOpen && !(e.target as HTMLElement).closest('input, select, button, textarea')) onOpen(id)
  }
  return (
    <>
      {select && (
        <div className="polar-selected">
          <button type="button" className="quiet" disabled={n === 0 || loading || many.isPending}
                  onClick={() => many.mutate(chosen.map(s => s.id))}>{label}</button>
          {many.isError && <p className="cap-error">{many.error.message}</p>}
        </div>
      )}
      <div className="health-table polar-sessions">
        <table>
          <thead>
            <tr>
              {select && (
                <th scope="col" className="tick">
                  <input type="checkbox" aria-label="Select all sessions" checked={everyOne} disabled={many.isPending}
                         ref={box => { if (box) box.indeterminate = n > 0 && !everyOne }} onChange={e => tickAll(e.target.checked)} />
                </th>
              )}
              <th scope="col">Start</th><th scope="col">Activity</th><th scope="col" className="num">Duration</th>
              <th scope="col" className="num">Mean HR</th>
              <th scope="col" className="num">Maximum HR</th><th scope="col"><span className="sr-only">Remove or show again</span></th>
            </tr>
          </thead>
          <tbody>
            {sessions.map(s => (
              <tr key={s.id} className={onOpen ? 'polar-row' : undefined} onClick={e => open(e, s.id)}>
                {select && (
                  <td className="tick">
                    <input type="checkbox" aria-label={`Select the session of ${started(s.start_at)}`}
                           checked={picked.has(s.id)} disabled={many.isPending} onChange={e => tick(s.id, e.target.checked)} />
                  </td>
                )}
                <td>
                  {onOpen ? <button type="button" className="link" onClick={() => onOpen(s.id)}>{started(s.start_at)}</button>
                    : started(s.start_at)}
                  <small className="muted polar-source">{s.source === 'logger' ? 'PC' : 'phone'}
                    {s.copy_of_start && `, copy of the PC session of ${clockOf(s.copy_of_start)}`}</small>
                </td>
                <td><ActivityChoice session={s} /></td>
                <td className="num">{minutes(s.seconds)}</td>
                <td className="num">{whole(s.mean_hr)}</td>
                <td className="num">{whole(s.max_hr)}</td>
                <td><button type="button" className="quiet" disabled={mark.isPending} onClick={() => mark.mutate(s)}>
                  {s.removed ? 'Show again' : 'Remove'}</button></td>
              </tr>
            ))}
          </tbody>
        </table>
        {mark.isError && <p className="cap-error">{mark.error.message}</p>}
      </div>
    </>
  )
}

// the sessions of one day, each with Remove or Show again, under a day of the history table and an outlier of the
// analysis; a click opens the detail in a popup of its own
export function DaySessions({ day }: { day: string }) {
  const found = useQuery({ queryKey: ['polar-day', day], queryFn: () => getPolarDay(day) })
  const [open, setOpen] = useState<number | null>(null)
  if (found.isError) return <p className="cap-error">{found.error.message}</p>
  if (!found.data || found.data.sessions.length === 0) return null
  return (
    <>
      <SessionTable sessions={found.data.sessions} onOpen={setOpen} />
      {open !== null && <DetailDialog id={open} onClose={() => setOpen(null)} />}
    </>
  )
}

function DetailDialog({ id, onClose }: { id: number; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null)
  useEffect(() => { dialog.current?.showModal() }, [])
  // React passes the close event on through the portal: inside the large view of a chart it would close that view too
  return createPortal(
    <dialog ref={dialog} className="health-regime health-sessions accent-health" onClose={e => { e.stopPropagation(); onClose() }}
            aria-labelledby="session-title" {...closeOutside}>
      <div className="analysis-head">
        <h2 id="session-title">Polar H10 session</h2>
        <button type="button" className="quiet" onClick={() => dialog.current?.close()}>Close</button>
      </div>
      <SessionDetail id={id} />
    </dialog>,
    document.body,
  )
}
