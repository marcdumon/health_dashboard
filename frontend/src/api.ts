// the calls of the health dashboard to its own server; an error carries what to do

export class ApiError extends Error {
  constructor(message: string, readonly needsLogin: boolean, readonly setup: string = '') {
    super(message)
  }
}

// the browser's own words for an unreachable server ('Failed to fetch') tell the user nothing
export const APP_NOT_RUNNING = 'The health dashboard is not running, so this could not be done. Start it again: '
  + 'type md_run_health_dashboard in a terminal, then press the button again.'

const reach = (path: string, init?: RequestInit) => fetch(path, init).catch(() => { throw new ApiError(APP_NOT_RUNNING, false, '') })

async function call<T>(path: string, body?: unknown): Promise<T> {
  const response = await reach(path, body === undefined ? undefined : {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  const data = await response.json().catch(() => ({}))
  if (!response.ok) {
    const detail = typeof data.detail === 'string' ? data.detail : `Request failed (${response.status})`
    throw new ApiError(detail, Boolean(data.auth), typeof data.setup === 'string' ? data.setup : '')
  }
  return data as T
}

// a PATCH, PUT or DELETE, with the same error as call
async function send<T>(path: string, method: 'PATCH' | 'PUT' | 'DELETE', body?: unknown): Promise<T> {
  const response = await reach(path, { method, headers: { 'Content-Type': 'application/json' },
                                       body: body === undefined ? undefined : JSON.stringify(body) })
  const data = await response.json().catch(() => ({}))
  if (!response.ok) throw new ApiError(typeof data.detail === 'string' ? data.detail : `Request failed (${response.status})`, false)
  return data as T
}

export const getVersion = () => call<{ build: string; restart_needed?: boolean }>('/api/version')

// the Health page: the supplements of the day with their entries, the angina score, the
// Withings measurements
// a moment of the day: whether something is planned, and the time it was taken once all of it is
export type SupplementMoment = { name: string; planned: boolean; taken_at: string | null }
// an amount taken, in the dose unit of its supplement (1 for a yes or no); moment: the moment it counts for
export type SupplementEntry = { id: number; taken_at: string; amount: number; moment: string | null }
// a supplement of the day: its dose unit ('' for a yes or no), one dose, the plan by moment, the planned and the taken
// totals, and each entry; listed false: removed from the list, shown for the entries it has that day
export type HealthSupplement = {
  id: number; name: string; dose_unit: string; step: number | null; comment: string; listed: boolean
  plan: Record<string, number>; planned: number; total: number; entries: SupplementEntry[]
}
// a change of a supplement's planned amount a day (a start is 0 before, a stop 0 after)
export type SupplementChange = { supplement_id: number; name: string; unit: string; day: string; before: number; after: number }
// the list on the day of the page, to change it: the amount at each moment; used: it has entries, so its unit stays
export type RegimeItem = {
  id: number; name: string; dose_unit: string; step: number | null; comment: string; plan: Record<string, number>
  used: boolean
}
export type Regime = { moments: string[]; items: RegimeItem[]; changes: SupplementChange[] }
export type WithingsValue = { value: number; measured_at: string }
export type HealthPressure = { systolic: number | null; diastolic: number | null; pulse: number | null; measured_at: string }
// segments: per kind (muscle_mass, fat_mass, fat_free_mass) the newest value of each body part
export type HealthLatest = {
  pressure: HealthPressure | null; values: Record<string, WithingsValue | undefined>
  segments: Record<string, Record<string, WithingsValue | undefined> | undefined>
}
export type WithingsStatus = {
  configured: boolean; connected: boolean; last_fetch: string | null; error: string; fetching: boolean; callback_url: string
}
// a Ketostix reading (level in mmol/L), and the six colours of the strip the server takes
export type KetoneReading = { id: number; taken_at: string; level: number }
export type KetostixLevel = { level: number; label: string; mg_dl: number }
// the food noted for a day (none: no note): the first and the last food, the scales from -3 to +3 against the usual day
// (none: not chosen, which counts as 0 on a noted day), the marks, and the kcal and grams of a fast day
export type FoodDay = {
  first_food: string | null; last_food: string | null
  amount: number | null; protein: number | null; carbs: number | null; fat: number | null
  alcohol: boolean; salt: boolean; fast: boolean
  kcal: number | null; protein_g: number | null; carbs_g: number | null; fat_g: number | null
}
export type HealthView = {
  day: string; today: string; now: string
  supplements: { moments: SupplementMoment[]; items: HealthSupplement[] }
  angina: { score: number | null; note: string; not_measured: boolean } | null
  ketones: KetoneReading[]; ketostix: KetostixLevel[]; latest: HealthLatest; withings: WithingsStatus; polar: PolarStatus
  sleep: SleepStatus
  fitbit: FitbitStatus
  // the food of the day and of the day before: both are noted in the morning
  food: FoodDay | null
  // the tape measurements of the day by site, and the last day with any before it
  tape: { values: Record<string, number>; last: { day: string; values: Record<string, number> } | null }
}
// a day of the history: the fixed parts, and every other value of that day by name ('weight', 'muscle_mass:left_arm', the
// amount of a supplement taken 'supp:3')
export type HealthDayRow = {
  day: string; angina: number | null; angina_not_measured: boolean; ketones: number | null
  ketone_readings: number
  systolic: number | null; diastolic: number | null; pulse: number | null; readings: number
} & { [value: string]: string | number | null | undefined }

export const getHealth = (day?: string) => call<HealthView>(`/api/health${day ? `?day=${day}` : ''}`)
// one weigh-in (its groups of one minute) or one blood pressure reading; day is set for a removed one
export type HealthMeasurement = {
  kind: 'scale' | 'pressure'; measured_at: string; grpids: number[]; values: Record<string, number>; day?: string
}
// the Polar H10 values of one activity only; nothing for all sessions
const activityParam = (activity: number | null) => (activity === null ? '' : `&activity=${activity}`)
// a recording at its time, for the charts that show every recording of a day (a blood pressure reading, a measurement of
// the scale, a Ketostix reading, a Polar H10 session, an HRV or HR recovery test): its time (local, or UTC with its
// offset), its day and its values under their history keys
export type RecordingPoint = { at: string; day: string } & Record<string, number | string>
// the history value of each supplement: its key (supp:<id>), name and dose unit
export type SupplementKey = { key: string; name: string; unit: string }
export const getHealthHistory = (days: number, activity: number | null = null) =>
  call<{
    days: HealthDayRow[]; points: RecordingPoint[]; sleeps: (SleepTime & { day: string })[]; removed: HealthMeasurement[]
    supplements: SupplementKey[]
  }>(`/api/health/history?days=${days}${activityParam(activity)}`)
export const getDayMeasurements = (day: string) => call<{ measurements: HealthMeasurement[] }>(`/api/health/days/${day}/measurements`)
export const hideMeasurement = (grpids: number[]) => call<{ removed: HealthMeasurement[] }>('/api/health/measurements/hide', { grpids })
export const showMeasurement = (grpids: number[]) => call<{ removed: HealthMeasurement[] }>('/api/health/measurements/show', { grpids })
// the large view of a chart: one value with its rolling mean and band, numbers, histogram, outliers, the change from
// one day to the next, the last week against earlier weeks, years; and a second value against it by day
export type HealthAnalysis = {
  key: string; start: string; end: string; window: number
  points: { day: string; value: number; mean: number; std: number | null }[]
  stats: {
    count: number; mean: number; median: number; std: number | null; min: { day: string; value: number }
    max: { day: string; value: number }; trend_per_month: number | null
  } | null
  histogram: { edges: number[]; counts: number[] } | null
  outliers: { day: string; value: number; expected: number; z: number }[]
  steps: { count: number; median_size: number; p90_size: number; histogram: { edges: number[]; counts: number[] } } | null
  earlier: { ago: number; from: string; to: string; count: number; mean: number | null; diff: number | null }[]
  years: { year: number; months: (number | null)[] }[]
  // each recording of the value at its time (none for a value of the day: angina, a supplement, the tape)
  readings: { at: string; day: string; value: number }[]
  // what the numbers count: the values of the days, or each session (a value of the Polar H10 sessions has no value of a
  // day, so no change from day to day and no correlation by day)
  per: 'day' | 'session'
}
export const getHealthAnalysis = (key: string, days: number | null, window: number, activity: number | null = null) =>
  call<HealthAnalysis>(`/api/health/analysis?key=${encodeURIComponent(key)}&window=${window}${days ? `&days=${days}` : ''}`
    + activityParam(activity))
// the second value on day D - lag against the first value on day D; by_lag: r and n of every delay from 0 to 30 days
export type HealthRelation = {
  key: string; other: string; lag: number; points: { day: string; x_day: string; x: number; y: number }[]; n: number
  r: number | null; slope: number | null; intercept: number | null; by_lag: { lag: number; r: number | null; n: number }[]
}
// each supplement taken in the period against a value, by day: the amount `lag` days earlier against the value
export type SupplementRelation = Omit<HealthRelation, 'by_lag'> & { name: string; unit: string }
export const getSupplementRelations = (key: string, days: number, lag: number) =>
  call<{ relations: SupplementRelation[] }>(`/api/health/supplement-relations?key=${encodeURIComponent(key)}&days=${days}&lag=${lag}`)
export const getHealthRelation = (key: string, other: string, days: number | null, lag = 0, activity: number | null = null) =>
  call<HealthRelation>(`/api/health/relation?key=${encodeURIComponent(key)}&other=${encodeURIComponent(other)}`
    + (days ? `&days=${days}` : '') + `&lag=${lag}` + activityParam(activity))
export const addIntake = (day: string, supplementId: number, takenAt: string, amount: number | null = null) =>
  call<HealthView>('/api/health/intakes', { day, supplement_id: supplementId, taken_at: takenAt, amount })
export const setIntakeAmount = (id: number, amount: number) => send<HealthView>(`/api/health/intakes/${id}`, 'PUT', { amount })
export const setIntakeTime = (id: number, takenAt: string) =>
  send<HealthView>(`/api/health/intakes/${id}/time`, 'PUT', { taken_at: takenAt })
export const removeIntake = (id: number) => send<HealthView>(`/api/health/intakes/${id}`, 'DELETE')
export const takeMoment = (day: string, moment: string, takenAt: string) =>
  call<HealthView>('/api/health/moments', { day, moment, taken_at: takenAt })
export const undoMoment = (day: string, moment: string) =>
  send<HealthView>(`/api/health/moments?day=${day}&moment=${encodeURIComponent(moment)}`, 'DELETE')
// a comment says how to take it: with a meal, with fat
export type SupplementFields = { name: string; dose_unit: string; step: number | null; comment: string }
export type SupplementChangeFields = SupplementFields & { plan: Record<string, number> }
// the regime of any day, also one that has not come yet; a change of it gives the regime of its day
export const getRegime = (day: string) => call<{ regime: Regime }>(`/api/health/regime?day=${day}`)
// the regime of one day copied to another, from which it counts; the answer is the regime of that day
export const copyRegime = (fromDay: string, toDay: string) =>
  call<{ regime: Regime }>('/api/health/regime/copy', { from_day: fromDay, to_day: toDay })
export const addSupplement = (day: string, fields: SupplementFields) =>
  call<{ regime: Regime }>('/api/health/supplements', { day, ...fields })
export const changeSupplement = (id: number, day: string, fields: SupplementChangeFields) =>
  send<{ regime: Regime }>(`/api/health/supplements/${id}`, 'PUT', { day, ...fields })
export const removeSupplement = (id: number, day: string) =>
  send<{ regime: Regime }>(`/api/health/supplements/${id}?day=${day}`, 'DELETE')
export const setAngina = (day: string, score: number | null, note: string, notMeasured = false) =>
  send<HealthView>('/api/health/angina', 'PUT', { day, score, note, not_measured: notMeasured })
// some fields of the food of a day
export const setFood = (day: string, changes: Partial<FoodDay>) =>
  send<HealthView>('/api/health/food', 'PUT', { day, ...changes })
export const clearFood = (day: string) => send<HealthView>(`/api/health/food?day=${day}`, 'DELETE')
export const setTape = (day: string, site: string, cm: number | null) =>
  send<HealthView>('/api/health/tape', 'PUT', { day, site, cm })
export const addKetoneReading = (day: string, level: number, takenAt: string) =>
  call<HealthView>('/api/health/ketones', { day, level, taken_at: takenAt })
export const setKetoneTime = (id: number, takenAt: string) =>
  send<HealthView>(`/api/health/ketones/${id}`, 'PUT', { taken_at: takenAt })
export const removeKetoneReading = (id: number) => send<HealthView>(`/api/health/ketones/${id}`, 'DELETE')
export const fetchWithings = (day: string) => call<HealthView>(`/api/health/withings/fetch?day=${day}`, {})
// one sleep of Sleep as Android: its bedtime and wake time (with their offset) and its sleep time
export type SleepTime = { start_at: string; end_at: string; sleep_min: number }
// the sleep of a day (the sleeps that end on it): its values under their history keys ('sleep:total', …), a value not
// measured left out, and its sleeps in time order
export type SleepDay = { values: Record<string, number>; sleeps: SleepTime[] }
// the Sleep as Android backup that Dropbox syncs to the PC: its place set, its time, the last import, a newer backup
// waiting, what to do (not set, not there, old); and the sleep of the page's day
export type SleepStatus = {
  configured: boolean; backup_at: string | null; imported_at: string | null; waiting: boolean; message: string
  day: SleepDay | null
}
// a tile of the overview row: its value as text ('' without data: then `empty` says what to do), its change against the
// 30-day mean, its status (within, above, below; falling, steady, rising; met, open) in words, a detail line, and 30 days
// of values for the sparkline
export type OverviewTile = {
  key: string; label: string; unit: string; value: number | null; text: string; change: { value: number; unit: string } | null
  status: string | null; status_text: string; detail: string; empty: string; spark: (number | null)[]
}
export const getHealthOverview = (day: string) => call<{ tiles: OverviewTile[] }>(`/api/health/overview?day=${day}`)
export const importSleep = (day: string) => call<HealthView>(`/api/health/sleep/import?day=${day}`, {})
// the sleep pattern of the last `days` days from the main sleep of each day (its longest): the standard deviation of the
// bedtime and the wake time in minutes (null below 3 nights), the weekdays and the weekend (by the wake day; clock times),
// the social jetlag in minutes, and the main sleep against the ln RMSSD of the same morning (r null below 10 mornings;
// slope: ln RMSSD per hour of sleep); null without a sleep
export type SleepGroup = { nights: number; sleep_min: number; bedtime: string; wake: string; mid: string }
export type SleepSummary = {
  nights: number; bedtime_sd: number | null; wake_sd: number | null; weekday: SleepGroup | null; weekend: SleepGroup | null
  jetlag_min: number | null; hrv: { pairs: number; r: number | null; slope: number | null }
}
export const getSleepSummary = (days: number) => call<SleepSummary | null>(`/api/health/sleep/summary?days=${days}`)
// each sleep of the last `days` days (null: from the first sleep on), for the large view of the sleep times
export const getSleepTimes = (days: number | null) =>
  call<{ first: string; last: string; sleeps: (SleepTime & { day: string })[] }>(`/api/health/sleep/times${days ? `?days=${days}` : ''}`)
// the Fitbit card: whether the first day is set and a token is there, the time of the last download, what to do, and the
// values of the page's day by their history keys (fitbit:...); null without any
export type FitbitStatus = {
  configured: boolean; token: boolean; downloaded_at: string | null; message: string; day: Record<string, number> | null
}
export type FitbitExercise = {
  start_at: string; end_at: string; name: string; active_min: number | null; mean_hr: number | null; kcal: number | null
  azm: number | null
}
// the charts of a day: [time in ms, value] pairs (the HR as the mean of each minute, the steps of each period), the sleep
// stages of the nights that touch the day, and the exercises that start on it
export type FitbitDay = {
  hr: [number, number][]; spo2: [number, number][]; hrv: [number, number][]; steps: [number, number][]
  stages: { start: string; end: string; type: string }[]; exercises: FitbitExercise[]
}
export const downloadFitbit = (day: string) => call<HealthView>(`/api/health/fitbit/download?day=${day}`, {})
export const loginFitbit = (day: string) => call<HealthView>(`/api/health/fitbit/login?day=${day}`, {})
export const getFitbitDay = (day: string) => call<FitbitDay>(`/api/health/fitbit/day?day=${day}`)
// the Polar H10 import: set up (the two folders in health_dashboard.toml), running with its progress in files (first the
// copy from the phone, then the reading), the last import, a failure, the number of sessions
// button: the name of the import button, where it imports from; phase: moving the files from the phone, or reading the
// files on the PC; last_note: where the last import read from, and what the move from the phone did; phone_error: why the
// last import did not read the phone, with what to do (no import error: the phone's FTP server is often off)
export type PolarStatus = {
  configured: boolean; button: string; running: boolean; phase: 'copy' | 'read'; done: number; total: number; saved: number
  last_import: string | null; last_note: string
  error: string; phone_error: string
  sessions: number
  // the newest session that is not an HRV test, up to the day of the page, the newest HRV test of the page's day and the 7
  // days before, and the newest HR recovery test and stairs test of the page's day and the 30 days before
  // last_exercise.energy: its net energy expenditure in kcal, an estimate (null when person.toml lacks a value it needs)
  last_exercise: (PolarSession & { energy: number | null }) | null; last_hrv: PolarHrvCard | null
  last_recovery: PolarRecoveryCard | null
  last_stairs: PolarStairsCard | null
}
// one session, times in UTC: its activity and note. kind, rest_hr, rmssd and wrong_beats_pct are old fields that the
// import no longer fills (the HRV test has its own values)
export type PolarSession = {
  id: number; source: 'phone' | 'logger'; serial: string; start_at: string; end_at: string; day: string
  kind: 'exercise' | 'rest'; seconds: number; mean_hr: number | null; max_hr: number | null; rest_hr: number | null
  rmssd: number | null
  wrong_beats_pct: number | null; copy_of: number | null; copy_of_start: string | null; removed: boolean
  markers: PolarMarker[]; activity_id: number | null; activity: string | null; activity_test: string | null
  note: string | null
  // the recordings it joins: 1 for a session that is not joined
  recordings: number
}
export const importPolar = (day: string) => call<HealthView>(`/api/health/polar/import?day=${day}`, {})
export const getPolarSessions = (offset: number, limit: number) =>
  call<{ sessions: PolarSession[]; total: number; removed: PolarSession[] }>(`/api/health/polar/sessions?offset=${offset}&limit=${limit}`)
export const getPolarDay = (day: string) => call<{ sessions: PolarSession[] }>(`/api/health/polar/days/${day}`)
export const removePolarSession = (id: number) => call<{ ok: boolean }>(`/api/health/polar/sessions/${id}/remove`, {})
export const showPolarSession = (id: number) => call<{ ok: boolean }>(`/api/health/polar/sessions/${id}/show`, {})
export const removePolarSessions = (ids: number[]) => call<{ ok: boolean }>('/api/health/polar/sessions/remove', { ids })
export const showPolarSessions = (ids: number[]) => call<{ ok: boolean }>('/api/health/polar/sessions/show', { ids })
// sessions that a glitch of the logger app cut apart, joined into the first of them; Split takes them apart again
export const joinPolarSessions = (ids: number[]) => call<{ ok: boolean }>('/api/health/polar/sessions/join', { ids })
export const splitPolarSession = (id: number) => call<{ ok: boolean }>(`/api/health/polar/sessions/${id}/split`, {})
// the activities of the sessions, in list order, each with how many sessions have it; last_used: the activity of the
// last recording started from the card
// last_at: the start of the newest session that is not removed (null without one)
export type PolarActivity = {
  id: number; name: string; test: string | null; sessions: number; last_at: string | null
}
export const getPolarActivities = () =>
  call<{ activities: PolarActivity[]; last_used: number | null }>('/api/health/polar/activities')
export const addPolarActivity = (name: string) => call<{ id: number }>('/api/health/polar/activities', { name })
export const renamePolarActivity = (id: number, name: string) =>
  call<{ ok: boolean }>(`/api/health/polar/activities/${id}`, { name })
export const removePolarActivity = (id: number) => call<{ ok: boolean }>(`/api/health/polar/activities/${id}/remove`, {})
// only the fields given change
export const labelPolarSession = (id: number, fields: { activity_id?: number | null; note?: string }) =>
  call<{ ok: boolean }>(`/api/health/polar/sessions/${id}/label`, fields)
// the popup of one session: each second from start (epoch s) with its HR, the zones (null without a birth year: below
// zone 1, then zone 1 to 5, in seconds) and the comparison with the last earlier sessions of the same activity
export type PolarCompareValues = {
  seconds: number | null; mean_hr: number | null; max_hr: number | null; high_zone_min: number | null
}
export type PolarDetail = {
  session: PolarSession; start: number; hr: (number | null)[]; birth_year: number | null
  zones: { max_hr: number; limits: number[]; seconds: number[] } | null
  compare: { count: number; this: PolarCompareValues; usual: PolarCompareValues } | null
  hrv: (PolarHrv & { beats: [number, number, number][] }) | null
  recovery: PolarRecovery | null
  stairs: PolarStairs | null
  // the net energy expenditure in kcal (without the BMR), an estimate (null when person.toml lacks a value it needs:
  // energy_missing says which);
  // energy_note: the correction factor of person.toml, or why it is not used ('' without one); trimp: the Edwards TRIMP
  // (null without a birth date)
  energy: number | null; energy_missing: string; energy_note: string; trimp: number | null
}
// the values of an HRV test (the 5 minutes after the first minute, the window in UTC ms), or the reason it has none
export type PolarHrv = {
  reason: string | null; rest_hr: number | null; rmssd: number | null; ln_rmssd: number | null; sdnn: number | null
  pnn50: number | null; lf: number | null; hf: number | null; lf_hf: number | null; stress: number | null
  wrong_pct: number | null; from_ms: number | null; to_ms: number | null
}
// the 7-day mean of ln RMSSD against the normal range of 60 days, and its 7-day coefficient of variation in % (null below
// 3 tests); without a range (fewer than 14 tests) missing says how many more tests are needed
export type PolarHrvStatus = {
  mean_7d: number | null; cv_7d: number | null; low: number | null; high: number | null
  status: 'below' | 'within' | 'above' | null; missing: number
}
// the values of an HR recovery test (the stop: the last Effort done marker, else the last second with the highest HR;
// stop_s: its second from the start of the session), or the reason it has none
export type PolarRecovery = {
  reason: string | null; hr_stop: number | null; hr_60: number | null; hr_120: number | null; drop_60: number | null
  drop_120: number | null; stop_s: number | null; stop_from: 'marker' | 'highest HR' | null
}
export type PolarRecoveryCard = PolarRecovery & { session_id: number; start_at: string }
// the values of a stairs test (the climb: from the first press of the phone marker to the last; each stop with a start
// after it is a pause; the seconds from the start of the session), or the reason it has none. The HR at the top and after
// it follow the HR recovery rules, with their own reason
export type PolarStairs = {
  reason: string | null; climb_s: number | null; pauses: number | null; pause_s: number | null; max_hr: number | null
  start_s: number | null; end_s: number | null; pause_list: { at_s: number; seconds: number }[] | null
  hr_top: number | null; hr_60: number | null; hr_120: number | null; drop_60: number | null; drop_120: number | null
  recovery_reason: string | null
}
export type PolarStairsCard = PolarStairs & { session_id: number; start_at: string }
// the newest session of a test with its values, for the result in its popup
export type PolarLastTest = (PolarHrv | PolarRecovery | PolarStairs) & { session_id: number; start_at: string }
export const getPolarLastTest = (test: 'hrv' | 'recovery' | 'stairs') =>
  call<PolarLastTest | null>(`/api/health/polar/tests/${test}/last`)
export type PolarHrvCard = PolarHrv & { session_id: number; start_at: string; status: PolarHrvStatus }
export const getPolarDetail = (id: number) => call<PolarDetail>(`/api/health/polar/sessions/${id}`)
// how many sessions in the folders on the PC the next import reads; recalc: because the calculations changed
export const getPolarWaiting = () => call<{ sessions: number; recalc: boolean }>('/api/health/polar/waiting')
// the minutes of the sessions per week (its Monday) by activity name, and the activity names in list order
export type PolarWeek = { week: string; minutes: Record<string, number> }
// the minutes per week in each of the 5 HR zones (zone 1 first) and the lower limits of the zones this year; both null
// without a birth date in person.toml
export const getPolarZones = (days: number) =>
  call<{ limits: number[] | null; weeks: { week: string; minutes: number[] }[] | null }>(`/api/health/polar/zones?days=${days}`)
export const getPolarWeeks = (days: number, activity: number | null) =>
  call<{ weeks: PolarWeek[]; activities: string[] }>(`/api/health/polar/weeks?days=${days}${activityParam(activity)}`)
// the net energy expenditure in kcal per week (its Monday) by activity name, of the sessions that are not HRV tests; the activity
// names in list order; what person.toml must give when a value is missing ('' when none is)
// the minutes of moderate and of vigorous activity per week (its Monday), the WHO minimum a week (a vigorous minute counts
// double), and what person.toml must give when it is missing ('' when nothing is)
export const getPolarActivity = (days: number) =>
  call<{ weeks: { week: string; moderate: number; vigorous: number }[]; target: number; missing: string }>(
    `/api/health/polar/activity?days=${days}`)
export const getPolarEnergy = (days: number) =>
  call<{ weeks: { week: string; kcal: Record<string, number> }[]; activities: string[]; missing: string }>(
    `/api/health/polar/energy?days=${days}`)
// a marker of a logger session: its time (UTC), its label and the HR of its second
export type PolarMarker = { at: string; label: string; hr: number | null }
// the recording with the user's own logger now: its state, the live values, the last marker (Effort done of the HR
// recovery test), the last error and the activity chosen at Start
export type PolarLive = {
  configured: boolean
  state: 'off' | 'connecting' | 'recording' | 'connection lost' | 'stopping' | 'importing'
  seconds: number | null; hr: number | null; battery: number | null
  last_marker: { label: string; at: string } | null
  error: string
  // the activity chosen at Start recording (null for a recording started elsewhere)
  activity: number | null
  // the test time of an HRV test chosen at Start, in seconds (null for any other recording)
  measure_s: number | null
}
export const getPolarLive = () => call<PolarLive>('/api/health/polar/live')
export const startPolarRecording = (activityId: number | null, measureS: number | null = null) =>
  call<PolarLive>('/api/health/polar/live/start', { activity_id: activityId, measure_s: measureS })
export const stopPolarRecording = () => call<PolarLive>('/api/health/polar/live/stop', {})
export const markPolar = (label: string) => call<PolarLive>('/api/health/polar/live/markers', { label })
