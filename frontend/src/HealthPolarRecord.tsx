import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'

import {
  getPolarActivities, getPolarLive, startPolarRecording, stopPolarRecording, type PolarActivity, type PolarLive,
} from './api'
import { closeOutside } from './dialog'
import { useForget } from './HealthMeasures'
import { ActivitySelect } from './HealthPolarDetail'

// a recording with the user's own logger, on the Polar H10 card: Start recording (it only records), then while it records
// the live values and Stop; a recording of a test also has the button that opens the popup of the test
// (HealthPolarTests), any other recording the button of its large view (its time and HR in large digits). The page asks 4
// times a second while a recording starts, runs, stops or is imported, so a new HR shows at most 0.25 s after the logger has
// it; at the end the sessions and the history are read again

const ACTIVE: PolarLive['state'][] = ['connecting', 'recording', 'connection lost', 'stopping', 'importing']
export const SAID: Record<PolarLive['state'], string> = {
  off: '', connecting: 'Connecting to the strap…', recording: 'Recording',
  'connection lost': 'Connection lost, the logger connects again', stopping: 'Stopping…', importing: 'Importing the recording…',
}
export const duration = (s: number) => `${Math.floor(s / 60)} min ${String(Math.floor(s % 60)).padStart(2, '0')} s`
// whole seconds as m:ss
export const mss = (whole: number) => `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, '0')}`

export type PolarTestKind = 'hrv' | 'recovery' | 'stairs'
export const TEST_TITLES: Record<PolarTestKind, string> = { hrv: 'HRV test', recovery: 'HR recovery test', stairs: 'Stairs test' }
// the test of an activity (the activity of a recording): 'hrv', 'recovery' or null
export const testOf = (activities: PolarActivity[] | undefined, id: number | null): PolarTestKind | null => {
  const test = activities?.find(a => a.id === id)?.test
  return test === 'hrv' || test === 'recovery' ? test : null
}

export const isRecording = (live: PolarLive | undefined): boolean => Boolean(live && ACTIVE.includes(live.state))

export function usePolarLive() {
  const client = useQueryClient()
  const forget = useForget()
  const found = useQuery({ queryKey: ['polar-live'], queryFn: getPolarLive,
                           refetchInterval: q => (isRecording(q.state.data) ? 250 : false) })
  const was = useRef(found.data?.state)
  useEffect(() => {
    if (was.current && was.current !== 'off' && found.data?.state === 'off') forget()
    was.current = found.data?.state
  }, [found.data?.state])
  return { live: found.data, keep: (data: PolarLive) => client.setQueryData(['polar-live'], data) }
}

// Start recording, in the status row of the card, after the choice of the activity of the session (the last one used
// first); the test activities are not in the list: a test starts from its own button
export function StartRecording({ keep }: { keep: (v: PolarLive) => void }) {
  const list = useQuery({ queryKey: ['polar-activities'], queryFn: getPolarActivities })
  const [chosen, setChosen] = useState<number | null | undefined>(undefined)
  const last = list.data?.last_used ?? null
  // the last activity used, unless it is a test
  const fallback = list.data?.activities.some(a => a.id === last && a.test === null) ? last : null
  const activity = chosen === undefined ? fallback : chosen
  const start = useMutation({ mutationFn: () => startPolarRecording(activity), onSuccess: keep })
  return (
    <>
      <ActivitySelect value={activity} onPick={setChosen} label="Activity of the recording" tests={false} />
      <button type="button" className="quiet" disabled={start.isPending} onClick={() => start.mutate()}>Start recording</button>
      {start.isError && <p className="cap-error">{start.error.message}</p>}
    </>
  )
}

// the live part of the card while a recording runs: the state, the duration, the HR, the battery and Stop; for a test also
// the button that opens its popup again, for any other recording the button of its large view
export function LivePart({ live, keep, onOpenTest }: {
  live: PolarLive; keep: (v: PolarLive) => void; onOpenTest: (test: PolarTestKind) => void
}) {
  const stop = useMutation({ mutationFn: stopPolarRecording, onSuccess: keep })
  const ending = live.state === 'stopping' || live.state === 'importing'
  const list = useQuery({ queryKey: ['polar-activities'], queryFn: getPolarActivities })
  const test = testOf(list.data?.activities, live.activity)
  const [large, setLarge] = useState(false)
  return (
    <section className="polar-live">
      <p className="polar-live-line">
        <strong>{SAID[live.state]}</strong>
        {live.seconds !== null && <span>{duration(live.seconds)}</span>}
        {!ending && <span>HR {live.hr ?? '—'} <small>bpm</small></span>}
        {live.battery !== null && <span>battery {live.battery} %</span>}
      </p>
      {!ending && (
        <div className="polar-live-actions">
          {test ? <button type="button" className="quiet" onClick={() => onOpenTest(test)}>{TEST_TITLES[test]}</button>
            : <button type="button" className="quiet" onClick={() => setLarge(true)}>Large view</button>}
          <button type="button" className="primary" disabled={stop.isPending} onClick={() => stop.mutate()}>Stop</button>
        </div>
      )}
      {stop.error && <p className="cap-error">{stop.error.message}</p>}
      {large && !test && (
        <LargeView title={list.data?.activities.find(a => a.id === live.activity)?.name ?? 'Recording'} live={live}
                   onClose={() => setLarge(false)}>
          {ending ? <p className="polar-prompt">{SAID[live.state]}</p> : <BigLive live={live} />}
          {!ending && (
            <div className="polar-live-actions">
              <span />
              <button type="button" className="primary" disabled={stop.isPending} onClick={() => stop.mutate()}>Stop</button>
            </div>
          )}
          {stop.error && <p className="cap-error">{stop.error.message}</p>}
        </LargeView>
      )}
    </section>
  )
}

// the seconds of a recording now: the logger's seconds go on with the clock of the PC between its answers, which come 4 times a
// second but each a little late, so the large digits count each second once and never go back. Of the starts that the
// answers give, the earliest is kept: that of the answer that came quickest
export function useRecordedSeconds(seconds: number | null): number | null {
  const start = useRef<number | null>(null)
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 250)
    return () => clearInterval(timer)
  }, [])
  useEffect(() => {
    const by = seconds === null ? null : Date.now() - seconds * 1000
    start.current = by === null || start.current === null ? by : Math.min(start.current, by)
  }, [seconds])
  if (seconds === null) return null
  return start.current === null ? seconds : Math.max(0, (now - start.current) / 1000)
}

// the large digits of a recording, to read from a few metres away: a countdown of the test (`left` seconds, named by
// `label`; null before the recording runs) or else the time recorded, and the HR; under them what to do now in large type,
// or the state when the recording does not run (a lost connection in red). An HR from before a lost connection is not shown
export function BigLive({ live, label = 'Time', left, prompt = '', act = false }: {
  live: PolarLive; label?: string; left?: number | null; prompt?: string; act?: boolean
}) {
  const recorded = useRecordedSeconds(live.seconds)
  const countdown = left !== undefined
  const shown = countdown ? left : recorded
  const running = live.state === 'recording'
  const hr = running ? live.hr : null
  return (
    <>
      <div className="polar-digits">
        <p>
          <span>{label}</span>
          <strong>{shown === null ? '—' : mss(countdown ? Math.ceil(shown) : Math.floor(shown))}</strong>
          <span>{countdown ? 'left' : 'recorded'}</span>
        </p>
        <p><span>HR</span><strong>{hr ?? '—'}</strong><span>bpm</span></p>
      </div>
      <p className={`polar-prompt${live.state === 'connection lost' ? ' lost' : act && running ? ' act' : ''}`} aria-live="polite">
        {running ? prompt : SAID[live.state]}
      </p>
    </>
  )
}

// the small line of the large view: the time recorded (not when the large digits show it) and the strap battery
export const RecordedLine = ({ live, time = true }: { live: PolarLive; time?: boolean }) => (
  <p className="polar-live-line muted">
    {time && live.seconds !== null && <span>{duration(live.seconds)} recorded</span>}
    {live.battery !== null && <span>battery {live.battery} %</span>}
  </p>
)

// the large view of a recording without a test: a wide popup, with the large digits and the buttons in
// `children`; closing it does not stop the recording
function LargeView({ title, live, onClose, children }: {
  title: string; live: PolarLive; onClose: () => void; children: ReactNode
}) {
  const dialog = useRef<HTMLDialogElement>(null)
  useEffect(() => { dialog.current?.showModal() }, [])
  return createPortal(
    <dialog ref={dialog} className="health-regime polar-big accent-health" onClose={onClose} aria-labelledby="large-title"
            {...closeOutside}>
      <div className="analysis-head">
        <h2 id="large-title">{title}</h2>
        <RecordedLine live={live} time={false} />
        <button type="button" className="quiet" onClick={() => dialog.current?.close()}>Close</button>
      </div>
      <section className="polar-live">{children}</section>
    </dialog>,
    document.body,
  )
}
