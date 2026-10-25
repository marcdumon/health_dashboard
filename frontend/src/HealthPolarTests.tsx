import { useMutation, useQuery } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'

import {
  getPolarActivities, getPolarLastTest, markPolar, startPolarRecording, stopPolarRecording, type PolarHrv, type PolarLive,
  type PolarRecovery, type PolarStairs,
} from './api'
import { closeOutside } from './dialog'
import { RecoveryValues, StairsValues, started } from './HealthPolarDetail'
import {
  BigLive, isRecording, RecordedLine, SAID, TEST_TITLES, useRecordedSeconds, type PolarTestKind,
} from './HealthPolarRecord'

// the popup of a test of the Polar H10 card (Start recording on the card only records): its steps, its own Start
// recording with the activity of the test, while that recording runs a large view: a wide popup (the countdown of
// the test and the HR in large digits, what to do now, the metronome and Effort done of the HR recovery test, and Stop),
// then the result of the newest session of the test. Closing the popup does not stop the recording. The stairs test is
// recorded with the phone (the PC is too far from the stairs): its popup has the steps and the result of the newest stairs
// test only. The HRV test has a test time to choose, and a loud end sound (useHrvAlarm, on the card)

export const TEST_STEPS: Record<PolarTestKind, string[]> = {
  hrv: ['Choose the test time.', 'Press Start recording, then lie or sit still and breathe normally. The first minute is to settle.',
        'Stay still until the loud end sound: 1 minute after the test time.', 'Press Stop.'],
  recovery: ['Press Start recording.', 'Raise your HR in any way.', 'Press Effort done, and sit still.',
             'Press Stop when the countdown ends.'],
  stairs: ['Put on the strap.', 'At the bottom of the stairs, start the recording in the phone app.',
           'Press the marker when you start to climb.', 'At each pause, press the marker. Press it again when you continue.',
           'At the 8th floor, press the marker.', 'Sit down. Stay still for 2 minutes.', 'Stop the recording in the phone app.',
           'At the PC, press Import from phone and PC. In All sessions, set the activity of the new session to Stairs test.'],
}

export function TestDialog({ test, live, keep, onClose, onOpenSession }: {
  test: PolarTestKind; live: PolarLive | undefined; keep: (v: PolarLive) => void; onClose: () => void
  onOpenSession: (id: number) => void
}) {
  const dialog = useRef<HTMLDialogElement>(null)
  useEffect(() => { dialog.current?.showModal() }, [])
  const list = useQuery({ queryKey: ['polar-activities'], queryFn: getPolarActivities })
  const activity = list.data?.activities.find(a => a.test === test) ?? null
  const [hrvMinutes, setHrvMinutes] = useState(() => {
    try {
      const kept = Number(localStorage.getItem(HRV_KEY))
      return HRV_MINUTES.includes(kept) ? kept : HRV_FIRST
    } catch { return HRV_FIRST }
  })
  const chooseHrv = (next: number) => {
    setHrvMinutes(next)
    try { localStorage.setItem(HRV_KEY, String(next)) } catch { /* no storage: the choice lasts this visit */ }
  }
  const start = useMutation({ mutationFn: () => startPolarRecording(activity?.id ?? null, test === 'hrv' ? hrvMinutes * 60 : null),
                              onSuccess: keep })
  const stop = useMutation({ mutationFn: stopPolarRecording, onSuccess: keep })
  const recording = isRecording(live)
  const mine = recording && activity !== null && live?.activity === activity.id
  // the result shows once a recording of this test that this popup saw has ended and is imported
  const [ran, setRan] = useState(false)
  useEffect(() => { if (mine) setRan(true) }, [mine])
  const ending = live?.state === 'stopping' || live?.state === 'importing'
  const failed = start.error ?? stop.error
  // the live values of this test's recording: the popup is its large view
  const own = mine ? live : undefined
  return createPortal(
    <dialog ref={dialog} className={`health-regime polar-test accent-health${own ? ' polar-big' : ''}`} onClose={onClose}
            aria-labelledby="test-title" {...closeOutside}>
      <div className="analysis-head">
        <h2 id="test-title">{TEST_TITLES[test]}</h2>
        {own && <RecordedLine live={own} />}
        <button type="button" className="quiet" onClick={() => dialog.current?.close()}>Close</button>
      </div>
      {!own && <ol className="polar-test-steps">{TEST_STEPS[test].map(step => <li key={step}>{step}</li>)}</ol>}
      {test === 'stairs' && <TestResult test={test} onOpen={onOpenSession} />}
      {test === 'hrv' && !recording && (
        <div className="polar-hrv-time">
          <label>Test time <select value={hrvMinutes} onChange={e => chooseHrv(Number(e.target.value))}>
            {HRV_MINUTES.map(m => <option key={m} value={m}>{m} min</option>)}
          </select></label>
          <span className="muted">with 1 minute before and 1 after: {hrvMinutes + 2} min in all</span>
          <button type="button" className="quiet" onClick={playAlarm}>Play the end sound</button>
        </div>
      )}
      {test !== 'stairs' && !recording && (
        <button type="button" className="primary" disabled={!live?.configured || activity === null || start.isPending}
                onClick={() => start.mutate()}>Start recording</button>
      )}
      {test !== 'stairs' && recording && !mine && <p className="muted">A recording runs. Stop it on the card first.</p>}
      {own && (
        <section className="polar-live">
          {ending ? <p className="polar-prompt">{SAID[own.state]}</p>
            : test === 'hrv' ? <HrvParts live={own} /> : <RecoveryParts live={own} keep={keep} />}
          {/* quiet: Effort done is the one green button, so a press from a few metres away does not end the test */}
          {!ending && (
            <div className="polar-live-actions">
              <span />
              <button type="button" className="quiet" disabled={stop.isPending} onClick={() => stop.mutate()}>Stop</button>
            </div>
          )}
        </section>
      )}
      {ran && !recording && <TestResult test={test} onOpen={onOpenSession} />}
      {failed && <p className="cap-error">{failed.message}</p>}
    </dialog>,
    document.body,
  )
}

// the test time of the HRV test in minutes, 5 to 60 in steps of 5: 10 at first, then the last one set, in this browser;
// the server takes whole minutes from 5 to 60
const HRV_KEY = 'polar-hrv-minutes'
const HRV_MINUTES = Array.from({ length: 12 }, (_, n) => 5 * (n + 1))
const HRV_FIRST = 10
// the HRV test while it records: 1 minute to settle, the test time measured, then 1 minute more, so standing up never
// touches the measured beats (the logger also counts its seconds from a few seconds before the first beat). A recording
// without a test time measures 5 minutes, as the tests from before the choice
const SETTLE_S = 60
const AFTER_S = 60
const measuredUntil = (live: PolarLive) => SETTLE_S + (live.measure_s ?? 300)

function HrvParts({ live }: { live: PolarLive }) {
  const seconds = useRecordedSeconds(live.seconds)
  if (seconds === null) return <BigLive live={live} label="Settle" left={null} />
  const measured = measuredUntil(live)
  const done = measured + AFTER_S
  const [label, end, prompt] = seconds < SETTLE_S ? ['Settle', SETTLE_S, 'Stay still and breathe normally.']
    : seconds < measured ? ['Measure', measured, 'Stay still.']
    : ['Last minute', done, seconds < done ? 'Stay still.' : 'Test complete: press Stop.']
  return <BigLive live={live} label={label} left={Math.max(0, end - seconds)} prompt={prompt} act={seconds >= done} />
}

// the loud end sound of an HRV test, planned ahead in Web Audio once its recording runs, so it sounds on time with the test
// popup closed or the page in the background; a stop or a lost connection before the end cancels it
export function useHrvAlarm(live: PolarLive | undefined) {
  const measure = live?.measure_s ?? null
  const running = live?.state === 'recording' && measure !== null && live.seconds !== null
  const seconds = useRef<number | null>(null)
  seconds.current = live?.seconds ?? null
  useEffect(() => {
    if (!running || live === undefined || seconds.current === null) return undefined
    const left = measuredUntil(live) + AFTER_S - seconds.current
    if (left <= 0) return undefined
    const audio = new AudioContext()
    void audio.resume()
    alarm(audio, audio.currentTime + left)
    return () => void audio.close()
  }, [running, measure])
}

// the HR recovery test while it records: the time recorded, the metronome for an effort on a step, with the time of the
// effort set before it starts and a countdown of that time (at its end one beep, and the metronome stops), Effort done (the
// stop), then a countdown of the 2 minutes from the time of the marker, with one beep at its end
const RECOVERY_S = 120
// the time of the effort in minutes: 3 at first (the YMCA step test), then the last one set, in this browser
const EFFORT_KEY = 'polar-effort-minutes'
const EFFORT_MINUTES = [1, 2, 3, 4, 5, 6, 8, 10, 15, 20]

function RecoveryParts({ live, keep }: { live: PolarLive; keep: (v: PolarLive) => void }) {
  const mark = useMutation({ mutationFn: () => markPolar('effort done'), onSuccess: keep })
  const done = live.last_marker?.label === 'effort done' ? new Date(live.last_marker.at).getTime() : null
  const [minutes, setMinutes] = useState(() => {
    try { return Number(localStorage.getItem(EFFORT_KEY)) || 3 } catch { return 3 }
  })
  const [ticking, setTicking] = useState(false)
  // when the metronome was switched on: the countdown of the effort runs from then
  const [since, setSince] = useState(() => Date.now())
  const [now, setNow] = useState(() => Date.now())
  const effort = ticking ? Math.max(0, minutes * 60 - (now - since) / 1000) : null
  const timeUp = effort === 0
  useMetronome(ticking && !timeUp && done === null)
  useEffect(() => {
    if (done === null && (!ticking || timeUp)) return undefined
    const timer = setInterval(() => setNow(Date.now()), 250)
    return () => clearInterval(timer)
  }, [done, ticking, timeUp])
  useEffect(() => { if (timeUp) beep() }, [timeUp])
  const choose = (next: number) => {
    setMinutes(next)
    try { localStorage.setItem(EFFORT_KEY, String(next)) } catch { /* no storage: the choice lasts this visit */ }
  }
  const metronome = () => {
    setSince(Date.now())
    setNow(Date.now())
    setTicking(!ticking)
  }
  const rest = done === null ? null : Math.max(0, RECOVERY_S - (now - done) / 1000)
  const complete = rest === 0
  useEffect(() => { if (complete) beep() }, [complete])
  if (rest === null) {
    const prompt = timeUp ? 'Time is up: press Effort done.' : 'Raise your HR in any way.'
    return (
      <>
        {effort === null ? <BigLive live={live} prompt={prompt} />
          : <BigLive live={live} label="Effort" left={effort} prompt={prompt} act={timeUp} />}
        <div className="polar-live-actions">
          <span className="polar-metronome">
            <label>Effort <select value={minutes} disabled={ticking} onChange={e => choose(Number(e.target.value))}>
              {EFFORT_MINUTES.map(m => <option key={m} value={m}>{m} min</option>)}
            </select></label>
            <button type="button" className="quiet" aria-pressed={ticking} onClick={metronome}>
              Metronome {ticking ? 'on' : 'off'} <small className="muted">96 a minute</small></button>
          </span>
          <button type="button" className="primary" disabled={mark.isPending} onClick={() => mark.mutate()}>Effort done</button>
          {mark.error && <p className="cap-error">{mark.error.message}</p>}
        </div>
      </>
    )
  }
  return <BigLive live={live} label="Recovery" left={rest} prompt={complete ? 'Test complete: press Stop.' : 'Sit still.'}
                  act={complete} />
}

// a click at 96 a minute (the pace of the YMCA step test: up, up, down, down) while `on`; Web Audio plans each click a
// little ahead, so the pace stays even when the page is busy
const CLICK_S = 60 / 96

function useMetronome(on: boolean) {
  useEffect(() => {
    if (!on) return undefined
    const audio = new AudioContext()
    let next = audio.currentTime + 0.1
    const timer = setInterval(() => {
      while (next < audio.currentTime + 0.2) {
        tone(audio, next, 1000, 0.03)
        next += CLICK_S
      }
    }, 50)
    return () => {
      clearInterval(timer)
      void audio.close()
    }
  }, [on])
}

function tone(audio: AudioContext, at: number, hz: number, seconds: number) {
  const sound = audio.createOscillator()
  const volume = audio.createGain()
  sound.frequency.value = hz
  volume.gain.setValueAtTime(0.3, at)
  volume.gain.exponentialRampToValueAtTime(0.001, at + seconds)
  sound.connect(volume).connect(audio.destination)
  sound.start(at)
  sound.stop(at + seconds)
}

// the end of the HRV test, to hear from a bed: 3 groups of 3 beeps of a square wave at 880 Hz, near full volume
function alarm(audio: AudioContext, at: number) {
  for (let group = 0; group < 3; group++) {
    for (let n = 0; n < 3; n++) {
      const from = at + group * 1.6 + n * 0.35
      const sound = audio.createOscillator()
      const volume = audio.createGain()
      sound.type = 'square'
      sound.frequency.value = 880
      volume.gain.setValueAtTime(0, from)
      volume.gain.linearRampToValueAtTime(0.8, from + 0.01)
      volume.gain.setValueAtTime(0.8, from + 0.23)
      volume.gain.linearRampToValueAtTime(0, from + 0.25)
      sound.connect(volume).connect(audio.destination)
      sound.start(from)
      sound.stop(from + 0.25)
    }
  }
}

// the end sound of the HRV test once now, to set the volume before a test
function playAlarm() {
  const audio = new AudioContext()
  alarm(audio, audio.currentTime + 0.05)
  setTimeout(() => void audio.close(), 5000)
}

// one beep at the end of the countdown
function beep() {
  const audio = new AudioContext()
  tone(audio, audio.currentTime + 0.05, 880, 0.4)
  setTimeout(() => void audio.close(), 1000)
}

// the newest session of the test, after its recording: its values or why it has none, and a way to its detail
function TestResult({ test, onOpen }: { test: PolarTestKind; onOpen: (id: number) => void }) {
  const found = useQuery({ queryKey: ['polar-test-last', test], queryFn: () => getPolarLastTest(test) })
  if (found.isError) return <p className="cap-error">{found.error.message}</p>
  if (found.data === undefined) return null
  if (found.data === null) return <p className="muted">There is no session of this test yet.</p>
  const result = found.data
  const hrv = result as PolarHrv
  return (
    <section className="health-group">
      <h3>Result <span className="muted">{started(result.start_at)}</span></h3>
      {result.reason ? <p className="muted">{result.reason}</p> : test === 'stairs'
        ? <StairsValues stairs={result as PolarStairs} /> : test === 'recovery'
        ? <RecoveryValues recovery={result as PolarRecovery} /> : (
          <dl className="health-values">
            <div><dt>RMSSD</dt><dd>{hrv.rmssd?.toFixed(1) ?? '—'} <small>ms</small></dd></div>
            <div><dt>ln RMSSD</dt><dd>{hrv.ln_rmssd?.toFixed(2) ?? '—'}</dd></div>
            <div><dt>Resting HR</dt><dd>{hrv.rest_hr === null ? '—' : Math.round(hrv.rest_hr)} <small>bpm</small></dd></div>
          </dl>
        )}
      <button type="button" className="quiet" onClick={() => onOpen(result.session_id)}>Open the session</button>
    </section>
  )
}
