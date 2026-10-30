// every Withings value the page shows, by group: key, label, unit, decimals. The Withings card shows the newest of
// each; the history draws each in its own chart, with its own scale
export type Value = [string, string, string, number]
export const COMPOSITION: Value[] = [
  ['weight', 'Weight', 'kg', 1], ['fat_ratio', 'Fat', '%', 1], ['fat_mass', 'Fat mass', 'kg', 1],
  ['visceral_fat', 'Visceral fat', 'index', 1],
  ['muscle_ratio', 'Muscle', '%', 1], ['muscle_mass', 'Muscle mass', 'kg', 1], ['bone_mass', 'Bone mass', 'kg', 2],
  ['hydration', 'Water', 'kg', 1],
  ['intracellular_water', 'Intracellular water', 'kg', 1], ['extracellular_water', 'Extracellular water', 'kg', 1],
  ['basal_metabolic_rate', 'Basal metabolic rate', 'kcal a day', 0],
  ['metabolic_age', 'Metabolic age', 'years', 0],
]
export const VESSELS: Value[] = [
  ['pulse_wave_velocity', 'Pulse wave velocity', 'm/s', 1], ['vascular_age', 'Vascular age', 'years', 0],
  ['nerve_health', 'Nerve health score (feet)', '', 0],
]
// the sites of the tape measure as the history names them (the arms relaxed); the server checks the same 7 sites
export const TAPE: Value[] = [
  ['tape:chest', 'Chest', 'cm', 1], ['tape:waist', 'Waist (navel)', 'cm', 1], ['tape:hips', 'Hips', 'cm', 1],
  ['tape:upper_arm_left', 'Upper arm, left', 'cm', 1], ['tape:upper_arm_right', 'Upper arm, right', 'cm', 1],
  ['tape:thigh_left', 'Thigh, left', 'cm', 1], ['tape:thigh_right', 'Thigh, right', 'cm', 1],
]
// the tape sites with a left and a right side, drawn in one chart each: site, label
export const TAPE_PAIRS: [string, string][] = [['upper_arm', 'Upper arm'], ['thigh', 'Thigh']]
// a label inside a sentence: its first letter in lower case, but an abbreviation at the start stays (HR, HRV)
export const inText = (label: string) => (/^[A-Z]{2}/.test(label) ? label : label.charAt(0).toLowerCase() + label.slice(1))
// the values of each Polar H10 session, in their own tab of the history: one activity at a time, never a value of a day
export const H10: Value[] = [
  ['h10:mean_hr', 'Mean HR', 'bpm', 0], ['h10:max_hr', 'Maximum HR', 'bpm', 0],
]
// the values of the HRV test of each day, in their own tab of the history: the HRV values, then the resting HR
export const HRV: Value[] = [
  ['h10:rmssd', 'RMSSD', 'ms', 1], ['h10:ln_rmssd', 'ln RMSSD', '', 2], ['h10:sdnn', 'SDNN', 'ms', 1],
  ['h10:pnn50', 'pNN50', '%', 1], ['h10:lf', 'LF', 'ms²', 0], ['h10:hf', 'HF', 'ms²', 0], ['h10:lf_hf', 'LF/HF', '', 2],
  ['h10:stress', 'Stress index (Baevsky)', '', 1], ['h10:rest_hr', 'Resting HR', 'bpm', 0],
]
// which way each HRV test value is better, for the same person at rest. LF has none: it mixes both branches of the
// nervous system and the baroreflex. LF/HF has none: its reading as a balance of the two branches is disputed
export const HRV_BETTER: Record<string, string> = {
  'h10:ln_rmssd': 'higher is better', 'h10:rmssd': 'higher is better', 'h10:rest_hr': 'lower is better',
  'h10:sdnn': 'higher is better', 'h10:pnn50': 'higher is better', 'h10:lf': 'no good or bad direction',
  'h10:hf': 'higher is better, with normal breathing', 'h10:lf_hf': 'no good or bad direction',
  'h10:stress': 'lower is better',
}
// the values of the HR recovery test of each day, in their own tab of the history
export const RECOVERY: Value[] = [
  ['h10:hr_stop', 'HR at the stop', 'bpm', 0], ['h10:drop_60', 'HR drop in 60 s', 'bpm', 0],
  ['h10:drop_120', 'HR drop in 120 s', 'bpm', 0],
]
// the values of the stairs test (the 8th floor, recorded with the phone), in their own tab of the history
export const STAIRS: Value[] = [
  ['h10:stairs_time', 'Climb time', 's', 0], ['h10:stairs_pauses', 'Pauses', '', 1],
  ['h10:stairs_pause_time', 'Pause time', 's', 0], ['h10:stairs_max_hr', 'Highest HR in the climb', 'bpm', 0],
  ['h10:stairs_hr_top', 'HR at the 8th floor', 'bpm', 0], ['h10:stairs_drop_60', 'HR drop in 60 s', 'bpm', 0],
  ['h10:stairs_drop_120', 'HR drop in 120 s', 'bpm', 0],
]
// the sleep of each day (Sleep as Android: the sleeps that end on the day, added up), in its own tab of the history
export const SLEEP: Value[] = [
  ['sleep:total', 'Total sleep time', 'h', 1], ['sleep:count', 'Number of sleeps', '', 0], ['sleep:in_bed', 'Time in bed', 'h', 1],
  ['sleep:awake', 'Time awake', 'min', 0], ['sleep:deep', 'Deep sleep', 'min', 0], ['sleep:deep_pct', 'Deep sleep share', '%', 1],
  ['sleep:light', 'Light sleep', 'min', 0], ['sleep:rem', 'REM sleep', 'min', 0], ['sleep:cycles', 'Sleep cycles', '', 0],
]
// the food of each day, noted without counting, in its own tab of the history: the scales against the usual day (0),
// the marks (1 = yes), the times from the first and the last food, and the fast days with their kcal and grams
export const FOOD: Value[] = [
  ['food:amount', 'Amount of food', '', 0], ['food:protein', 'Protein', '', 0], ['food:carbs', 'Carbs', '', 0],
  ['food:fat', 'Fat', '', 0], ['food:alcohol', 'Alcohol', '', 0], ['food:salt', 'Much salt', '', 0],
  ['food:window', 'Eating window', 'h', 1], ['food:fasted', 'Hours without food', 'h', 1],
  ['food:to_sleep', 'Last food to sleep', 'h', 1], ['food:fast_day', 'Fast day', '', 0], ['food:refeed_day', 'Refeed day', '', 0],
  ['food:kcal', 'Energy of a fast day', 'kcal', 0], ['food:protein_g', 'Protein of a fast day', 'g', 0],
  ['food:carbs_g', 'Carbs of a fast day', 'g', 0], ['food:fat_g', 'Fat of a fast day', 'g', 0],
]
// the Fitbit values of each day (the band's own values; a sleep belongs to the day on which it ends), in groups in the
// Fitbit tab of the history
export const FITBIT_GROUPS: [string, Value[]][] = [
  ['Heart', [
    ['fitbit:resting_hr', 'Resting HR', 'bpm', 0], ['fitbit:mean_hr', 'Mean HR', 'bpm', 0], ['fitbit:max_hr', 'Maximum HR', 'bpm', 0],
    ['fitbit:hrv', 'HRV, night mean RMSSD', 'ms', 1], ['fitbit:hrv_deep', 'HRV, deep sleep RMSSD', 'ms', 1],
    ['fitbit:non_rem_hr', 'Non-REM HR', 'bpm', 0]]],
  ['Breath and blood', [
    ['fitbit:spo2', 'SpO2, night mean', '%', 1], ['fitbit:spo2_low', 'SpO2, night low', '%', 1],
    ['fitbit:spo2_high', 'SpO2, night high', '%', 1], ['fitbit:breathing', 'Breathing rate', 'breaths/min', 1],
    ['fitbit:breathing_deep', 'Breathing rate, deep sleep', 'breaths/min', 1],
    ['fitbit:breathing_light', 'Breathing rate, light sleep', 'breaths/min', 1],
    ['fitbit:breathing_rem', 'Breathing rate, REM sleep', 'breaths/min', 1],
    ['fitbit:skin_temp', 'Skin temperature, night', '°C', 2]]],
  ['Sleep', [
    ['fitbit:asleep', 'Time asleep', 'h', 1], ['fitbit:awake', 'Time awake', 'min', 0], ['fitbit:deep', 'Deep sleep', 'min', 0],
    ['fitbit:light', 'Light sleep', 'min', 0], ['fitbit:rem', 'REM sleep', 'min', 0],
    ['fitbit:to_deep_sleep', 'Time to deep sleep', 'min', 0], ['fitbit:awakenings', 'Short awakenings', '', 0]]],
  ['Activity', [
    ['fitbit:steps', 'Steps', '', 0], ['fitbit:distance', 'Distance', 'km', 2], ['fitbit:floors', 'Floors', '', 0],
    ['fitbit:kcal', 'Total energy expenditure', 'kcal', 0], ['fitbit:active_kcal', 'Active energy expenditure', 'kcal', 0],
    ['fitbit:azm', 'Active zone minutes', 'min', 0], ['fitbit:active_light', 'Light activity', 'min', 0],
    ['fitbit:active_moderate', 'Moderate activity', 'min', 0], ['fitbit:active_vigorous', 'Vigorous activity', 'min', 0],
    ['fitbit:sedentary', 'Sedentary time', 'h', 1], ['fitbit:kcal_light', 'Energy in the light HR zone', 'kcal', 0],
    ['fitbit:kcal_moderate', 'Energy in the moderate HR zone', 'kcal', 0],
    ['fitbit:kcal_vigorous', 'Energy in the vigorous HR zone', 'kcal', 0],
    ['fitbit:kcal_peak', 'Energy in the peak HR zone', 'kcal', 0]]],
  ['Exercise', [['fitbit:exercises', 'Exercises', '', 0], ['fitbit:exercise_min', 'Exercise time', 'min', 0]]],
]
export const FITBIT: Value[] = FITBIT_GROUPS.flatMap(([, values]) => values)
// the training load of each day (totals of a period of the sessions, not values of a session)
export const LOAD: Value[] = [
  ['load:trimp', 'Training load of the day (TRIMP)', '', 0], ['load:acute', 'Training load, 7 days (TRIMP)', '', 0],
  ['activity:moderate', 'Moderate activity', 'min', 0], ['activity:vigorous', 'Vigorous activity', 'min', 0],
]
// the values the large view of a chart can set against its own value, by day: blood pressure, pulse, the vessels,
// the body composition, the user's own angina score and ketones, the tape measure, the HRV, HR recovery
// and stairs tests, the sleep, the food, the training load and the Fitbit values. Not the values of the sessions: a day mixes activities, so they have
// no value of a day
export const RELATION_CHOICES: Value[] = [
  ['systolic', 'Systolic blood pressure', 'mmHg', 0], ['diastolic', 'Diastolic blood pressure', 'mmHg', 0],
  ['pulse', 'Pulse (blood pressure monitor)', 'bpm', 0], ['heart_rate', 'Heart rate (scale)', 'bpm', 0], ...VESSELS, ...COMPOSITION,
  ['angina', 'Angina', 'score', 1], ['ketones', 'Ketones', 'mmol/L', 1],
  ...TAPE,
  ...HRV.map(([key, label, unit, decimals]): Value => [key, `${label} (HRV test)`, unit, decimals]),
  ...RECOVERY.map(([key, label, unit, decimals]): Value => [key, `${label} (HR recovery test)`, unit, decimals]),
  ...STAIRS.map(([key, label, unit, decimals]): Value => [key, `${label} (stairs test)`, unit, decimals]),
  ...SLEEP,
  ...FOOD.map(([key, label, unit, decimals]): Value => [key, /food/i.test(label) ? label : `${label} (food)`, unit, decimals]),
  ...LOAD,
  ...FITBIT.map(([key, label, unit, decimals]): Value => [key, `${label} (Fitbit)`, unit, decimals]),
]
