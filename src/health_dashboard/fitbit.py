"""The Fitbit data through the Google Health API (the google_api library). Download Fitbit gets every data type of every
day from the first day (the [fitbit] part of health_dashboard.toml) into data/google_health/<type>/<day>.json, one file per
type and local day; google_api fetches a day again until two days after it ended, because the band syncs late. A page read
never downloads.

The values of a day come from its files, and are computed again only when one of its files changed (the newest file time
is kept with them in fitbit_days). The exercises are one list for all days (exercise/all.json): each download replaces
them, and an exercise belongs to the local day on which it starts.

Only the band's own values count: the phone (MobileTrack) also counts steps and active minutes, and the two would count
the same walk twice. A sleep belongs to the day on which it ends (its wake day), as the Sleep as Android sleeps, and the
values of a day add up its sleeps. A value without data is left out, never 0."""

import json
import math
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from google_api import auth, health

from health_dashboard import ledger, store
from health_dashboard.config import APP_DIR, DATA_DIR, config

TOKEN_FILE = APP_DIR / 'secrets' / 'google_health_token.json'
CLIENT_FILE = APP_DIR / 'secrets' / 'client_secret.json'
FOLDER = DATA_DIR / 'google_health'
PHONE = 'MobileTrack'
# the time of the last download, and the number of requests that failed in it
DOWNLOADED_KEY, FAILED_KEY = 'fitbit_downloaded_at', 'fitbit_failed'
NOT_SET = ('Set first_day in the [fitbit] part of health_dashboard.toml to the first day you wore the Fitbit (YYYY-MM-DD), '
           'and restart the app.')
NO_TOKEN = 'There is no Google Health login yet. Press Log in to Google Health, choose your Google account and tick every box.'
ZONES = ('LIGHT', 'MODERATE', 'VIGOROUS', 'PEAK')
LEVELS = ('LIGHT', 'MODERATE', 'VIGOROUS')
# the history keys of the values of a day, with their decimals
KEYS = {
    'fitbit:resting_hr': 0, 'fitbit:mean_hr': 0, 'fitbit:max_hr': 0, 'fitbit:hrv': 1, 'fitbit:hrv_deep': 1,
    'fitbit:non_rem_hr': 0,
    'fitbit:spo2': 1, 'fitbit:spo2_low': 1, 'fitbit:spo2_high': 1, 'fitbit:breathing': 1, 'fitbit:breathing_deep': 1,
    'fitbit:breathing_light': 1, 'fitbit:breathing_rem': 1, 'fitbit:skin_temp': 2,
    'fitbit:asleep': 2, 'fitbit:awake': 0, 'fitbit:deep': 0, 'fitbit:light': 0, 'fitbit:rem': 0, 'fitbit:fall_asleep': 0,
    'fitbit:awakenings': 0,
    'fitbit:steps': 0, 'fitbit:distance': 2, 'fitbit:floors': 0, 'fitbit:kcal': 0, 'fitbit:active_kcal': 0, 'fitbit:azm': 0,
    **{f'fitbit:active_{level.lower()}': 0 for level in LEVELS}, 'fitbit:sedentary': 1,
    **{f'fitbit:kcal_{zone.lower()}': 0 for zone in ZONES},
    'fitbit:exercises': 0, 'fitbit:exercise_min': 0,
}


class FitbitError(Exception):
    """A download that cannot start, with what to do."""


def first_day() -> date | None:
    """The first day to download, from health_dashboard.toml; None when not set."""
    text = config().fitbit.first_day
    return date.fromisoformat(text) if text else None


def _ok(value: float | None) -> bool:
    """Whether a value is a number (the API writes 'NaN' for a value it has not found yet)."""
    return value is not None and not math.isnan(value)


def _numbers(rows: list[dict], kind: str, field: str) -> list[float]:
    """The numbers of one field of one type."""
    return [r['value'] for r in rows if r['type'] == kind and r['field'] == field and _ok(r['value'])]


def _items(rows: list[dict], kind: str, prefix: str) -> list[dict]:
    """The items of a list field (prefix[0].minutes, prefix[0].type, ...) of the points of a type, each as {key: value or
    text}; the items of one point share its times."""
    pattern = re.compile(rf'{re.escape(prefix)}\[(\d+)\]\.(\w+)')
    found: dict[tuple, dict] = {}
    for r in rows:
        if r['type'] == kind and (m := pattern.fullmatch(r['field'])):
            found.setdefault((r['start'], r['end'], r['device'], m[1]), {})[m[2]] = r['text'] if r['value'] is None else r['value']
    return list(found.values())


def _seconds(start: str, end: str) -> float:
    """The length of a period."""
    return (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds()


def values(rows: list[dict]) -> dict[str, float]:
    """The values of a day from the rows of its files, under their history keys (KEYS); the phone's rows are left out."""
    band = [r for r in rows if r['device'] != PHONE]

    def mean(kind: str, field: str, above: float | None = None) -> float | None:
        found = [v for v in _numbers(band, kind, field) if above is None or v > above]
        return sum(found) / len(found) if found else None

    def total(kind: str, field: str | None = None) -> float | None:
        found = [r['value'] for r in band if r['type'] == kind and (field is None or r['field'] == field) and _ok(r['value'])]
        return sum(found) if found else None

    def items_total(found: list[dict], key: str, label: str, name: str) -> float | None:
        chosen = [i[name] for i in found if i.get(key) == label and _ok(i.get(name))]
        return sum(chosen) if chosen else None

    hr = _numbers(band, 'heart-rate', 'beatsPerMinute')
    slept = _numbers(band, 'sleep', 'summary.minutesAsleep')
    stages = _items(band, 'sleep', 'summary.stagesSummary')
    levels = _items(band, 'active-minutes', 'activeMinutesByActivityLevel')
    sedentary = [_seconds(r['start'], r['end']) for r in band if r['type'] == 'sedentary-period' and r['start'] and r['end']]
    distance = total('distance', 'millimeters')
    found = {
        'fitbit:resting_hr': mean('daily-resting-heart-rate', 'beatsPerMinute'),
        'fitbit:mean_hr': sum(hr) / len(hr) if hr else None, 'fitbit:max_hr': max(hr) if hr else None,
        'fitbit:hrv': mean('daily-heart-rate-variability', 'averageHeartRateVariabilityMilliseconds'),
        'fitbit:hrv_deep': mean('daily-heart-rate-variability', 'deepSleepRootMeanSquareOfSuccessiveDifferencesMilliseconds'),
        'fitbit:non_rem_hr': mean('daily-heart-rate-variability', 'nonRemHeartRateBeatsPerMinute'),
        'fitbit:spo2': mean('daily-oxygen-saturation', 'averagePercentage'),
        'fitbit:spo2_low': mean('daily-oxygen-saturation', 'lowerBoundPercentage'),
        'fitbit:spo2_high': mean('daily-oxygen-saturation', 'upperBoundPercentage'),
        'fitbit:breathing': mean('daily-respiratory-rate', 'breathsPerMinute'),
        # a stage with 0 breaths a minute was not measured (a night without REM, or a second version of the night)
        **{f'fitbit:breathing_{stage}': mean('respiratory-rate-sleep-summary', f'{stage}SleepStats.breathsPerMinute', above=0)
           for stage in ('deep', 'light', 'rem')},
        'fitbit:skin_temp': mean('daily-sleep-temperature-derivations', 'nightlyTemperatureCelsius'),
        'fitbit:asleep': sum(slept) / 60 if slept else None,
        'fitbit:awake': total('sleep', 'summary.minutesAwake'),
        **{f'fitbit:{stage.lower()}': items_total(stages, 'type', stage, 'minutes') for stage in ('DEEP', 'LIGHT', 'REM')},
        'fitbit:fall_asleep': total('sleep', 'summary.minutesToFallAsleep'),
        'fitbit:awakenings': len(_items(band, 'sleep', 'shortAwakenings')) if slept else None,
        'fitbit:steps': total('steps', 'count'),
        'fitbit:distance': distance / 1_000_000 if distance is not None else None,
        'fitbit:floors': total('floors'),
        'fitbit:kcal': total('total-calories', 'kcalSum'),
        'fitbit:active_kcal': total('active-energy-burned', 'kcal'),
        'fitbit:azm': total('active-zone-minutes', 'activeZoneMinutes'),
        **{f'fitbit:active_{level.lower()}': items_total(levels, 'activityLevel', level, 'activeMinutes') for level in LEVELS},
        'fitbit:sedentary': sum(sedentary) / 3600 if sedentary else None,
        **{f'fitbit:kcal_{zone.lower()}': total('calories-in-heart-rate-zone', f'caloriesInHeartRateZones[{zone}].kcal')
           for zone in ZONES},
    }
    return {key: round(value, KEYS[key]) for key, value in found.items() if value is not None}


def _exercises(tz: ZoneInfo) -> list[tuple] | None:
    """The band's exercises of exercise/all.json as rows of fitbit_exercises; None without the file."""
    path = FOLDER / 'exercise' / 'all.json'
    if not path.is_file():
        return None
    rows = [r for page in json.loads(path.read_text())['pages'] for r in health.point_rows('exercise', page.get('dataPoints', []))
            if r['device'] != PHONE and r['start'] and r['end']]
    by_time: dict[tuple[str, str], dict] = {}
    for r in rows:
        by_time.setdefault((r['start'], r['end']), {})[r['field']] = r['text'] if r['value'] is None else r['value']
    found = []
    for (start, end), fields in by_time.items():
        duration = fields.get('activeDuration')
        found.append((start, end, datetime.fromisoformat(start).astimezone(tz).date().isoformat(),
                      fields.get('displayName') or fields.get('exerciseType') or 'Exercise',
                      round(float(duration.rstrip('s')) / 60, 1) if duration else None,
                      fields.get('metricsSummary.averageHeartRateBeatsPerMinute'), fields.get('metricsSummary.caloriesKcal'),
                      fields.get('metricsSummary.activeZoneMinutes')))
    return found


def _compute(first: date, last: date) -> int:
    """Compute the values of each day from `first` to `last` whose files changed since their values were computed; how many
    days."""
    with ledger.connect() as db:
        stamps = {row['day']: row['stamp'] for row in db.execute('SELECT day, stamp FROM fitbit_days')}
    changed = []
    day = first
    while day <= last:
        files = list(FOLDER.glob(f'*/{day}.json'))
        if files and stamps.get(day.isoformat()) != (stamp := str(max(f.stat().st_mtime_ns for f in files))):
            changed.append((day.isoformat(), stamp, json.dumps(values(health.rows(FOLDER, day)))))
        day += timedelta(days=1)
    with ledger.connect(write=True) as db:
        db.executemany('INSERT INTO fitbit_days (day, stamp, vals) VALUES (?, ?, ?) '
                       'ON CONFLICT (day) DO UPDATE SET stamp = excluded.stamp, vals = excluded.vals', changed)
    return len(changed)


def download(today: date, tz: ZoneInfo) -> dict:
    """Get the Fitbit data from the first day to `today`, compute the days whose files changed and replace the exercises;
    what google_api wrote and how many requests failed, and the days computed. A token that is missing or refused raises
    AuthError (the card then offers the login)."""
    first = first_day()
    if first is None:
        raise FitbitError(NOT_SET)
    counts = health.collect(auth.creds(TOKEN_FILE), FOLDER, first, today, tz)
    computed = _compute(first, today)
    if (exercises := _exercises(tz)) is not None:
        with ledger.connect(write=True) as db:
            db.execute('DELETE FROM fitbit_exercises')
            db.executemany('INSERT INTO fitbit_exercises (start_at, end_at, day, name, active_min, mean_hr, kcal, azm) '
                           'VALUES (?, ?, ?, ?, ?, ?, ?, ?)', exercises)
    store.set_setting(DOWNLOADED_KEY, datetime.now(tz).isoformat(timespec='minutes'))
    store.set_setting(FAILED_KEY, str(counts['failed']) if counts['failed'] else None)
    return counts | {'days': computed}


def login() -> None:
    """The browser login of Google Health, with its read scopes; the token goes to TOKEN_FILE."""
    auth.login(CLIENT_FILE, TOKEN_FILE, health.SCOPES)


def status() -> dict:
    """What the Fitbit card says, from a look only: whether the first day is set, whether there is a token, the time of the
    last download, and what to do when something is missing or failed."""
    configured, token, failed = first_day() is not None, TOKEN_FILE.is_file(), store.setting(FAILED_KEY)
    message = (NOT_SET if not configured else NO_TOKEN if not token else
               f'{failed} parts of the last download failed: Google Health did not answer. Press Download Fitbit again; '
               'it gets only what is missing.' if failed else '')
    return {'configured': configured, 'token': token, 'downloaded_at': store.setting(DOWNLOADED_KEY), 'message': message}


def day_values(first: str, last: str) -> dict[str, dict[str, float]]:
    """The values of each day from `first` to `last` that has some, for the history: those of its files, and the number and
    the minutes of its exercises."""
    with ledger.connect() as db:
        found = {row['day']: json.loads(row['vals']) for row in
                 db.execute('SELECT day, vals FROM fitbit_days WHERE day >= ? AND day <= ?', (first, last))}
        for row in db.execute('SELECT day, COUNT(*) AS n, SUM(active_min) AS minutes FROM fitbit_exercises '
                              'WHERE day >= ? AND day <= ? GROUP BY day', (first, last)):
            minutes = {} if row['minutes'] is None else {'fitbit:exercise_min': round(row['minutes'], KEYS['fitbit:exercise_min'])}
            found[row['day']] = found.get(row['day'], {}) | {'fitbit:exercises': row['n']} | minutes
    return {day: values for day, values in found.items() if values}


def day_card(day: str) -> dict[str, float] | None:
    """The values of a day for the card; None without any."""
    return day_values(day, day).get(day)


def _ms(iso: str) -> int:
    """A time in ms since 1970."""
    return int(datetime.fromisoformat(iso).timestamp() * 1000)


def _series(rows: list[dict], kind: str, field: str) -> list[list[float]]:
    """The values of one field in time order, each with its time (ms)."""
    return sorted([_ms(r['start']), r['value']] for r in rows if r['type'] == kind and r['field'] == field and _ok(r['value'])
                  and r['start'])


def day_detail(day: str) -> dict:
    """The charts of a day: the HR as the mean of each minute, the SpO2 and HRV readings, the steps of each period, the sleep
    stages of the sleeps that end on the day or on the next day (the night that starts in the evening), and the exercises
    that start on the day."""
    current = date.fromisoformat(day)
    rows = [r for r in health.rows(FOLDER, current) if r['device'] != PHONE]
    following = FOLDER / 'sleep' / f'{current + timedelta(days=1)}.json'
    if following.is_file():
        rows += [r for page in json.loads(following.read_text())['pages']
                 for r in health.point_rows('sleep', page.get('dataPoints', [])) if r['device'] != PHONE]
    minutes: dict[int, list[float]] = {}
    for at, bpm in _series(rows, 'heart-rate', 'beatsPerMinute'):
        minutes.setdefault(at // 60_000 * 60_000, []).append(bpm)
    stages = sorted(({'start': s['startTime'], 'end': s['endTime'], 'type': s['type']}
                     for s in _items(rows, 'sleep', 'stages') if {'startTime', 'endTime', 'type'} <= s.keys()),
                    key=lambda s: s['start'])
    with ledger.connect() as db:
        exercises = [dict(row) for row in db.execute('SELECT start_at, end_at, name, active_min, mean_hr, kcal, azm '
                                                     'FROM fitbit_exercises WHERE day = ? ORDER BY start_at', (day,))]
    return {'hr': [[at, round(sum(found) / len(found), 1)] for at, found in sorted(minutes.items())],
            'spo2': _series(rows, 'oxygen-saturation', 'percentage'),
            'hrv': _series(rows, 'heart-rate-variability', 'rootMeanSquareOfSuccessiveDifferencesMilliseconds'),
            'steps': _series(rows, 'steps', 'count'), 'stages': stages, 'exercises': exercises}
