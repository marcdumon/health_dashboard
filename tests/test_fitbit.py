"""The Fitbit data on the Health page: the values of a day from its files (the band's values only), Download Fitbit that
computes only the days whose files changed, the exercises, the card, the charts of a day and the routes."""

import json
import os
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from google_api.auth import AuthError

from health_dashboard import analysis, api, fitbit, ledger

TZ = ZoneInfo('Europe/Brussels')
DAY = '2026-10-24'
BAND = {'recordingMethod': 'PASSIVELY_MEASURED', 'device': {'displayName': 'Fitbit Air'}, 'platform': 'FITBIT'}
PHONE = {'recordingMethod': 'PASSIVELY_MEASURED', 'device': {'displayName': 'MobileTrack'}, 'platform': 'FITBIT'}
DERIVED = {'recordingMethod': 'DERIVED', 'device': {'displayName': 'Fitbit Air'}, 'platform': 'FITBIT'}
DATE = {'year': 2026, 'month': 10, 'day': 24}


def save(folder: Path, kind: str, day: str, points: list[dict], *, rollup: bool = False) -> Path:
    """A file of one type and day as google_api saves it."""
    path = folder / kind / f'{day}.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    page = {'rollupDataPoints' if rollup else 'dataPoints': points}
    path.write_text(json.dumps({'type': kind, 'day': day, 'fetched_at': '2026-10-27T00:00:00+00:00', 'pages': [page]}))
    return path


def sample(body: str, at: str, source: dict = BAND, **values: object) -> dict:
    """A point at one moment."""
    return {'dataSource': source, body: {'sampleTime': {'physicalTime': at}, **values}}


def interval(body: str, start: str, end: str, source: dict = BAND, **values: object) -> dict:
    """A point of a period."""
    return {'dataSource': source, body: {'interval': {'startTime': start, 'endTime': end}, **values}}


def daily(body: str, **values: object) -> dict:
    """A value of the day."""
    return {'dataSource': DERIVED, body: {'date': DATE, **values}}


def rollup(body: str, start: str, end: str, **values: object) -> dict:
    """A window of a roll-up."""
    return {'startTime': start, 'endTime': end, body: values}


def night(start: str, end: str, asleep: int, awake: int, stages: list[tuple[str, int]], short: int = 0,
          deep_at: str | None = None) -> dict:
    """A sleep with its summary, a light stage period (up to `deep_at`, then a deep one) and some short awakenings."""
    periods = [(start, deep_at, 'LIGHT'), (deep_at, end, 'DEEP')] if deep_at else [(start, end, 'LIGHT')]
    return {'dataSource': DERIVED, 'sleep': {
        'interval': {'startTime': start, 'endTime': end}, 'type': 'STAGES',
        'stages': [{'startTime': a, 'endTime': b, 'type': kind} for a, b, kind in periods],
        'summary': {'minutesAsleep': str(asleep), 'minutesAwake': str(awake), 'minutesToFallAsleep': '0',
                    'stagesSummary': [{'type': kind, 'minutes': str(m), 'count': '3'} for kind, m in stages]},
        'shortAwakenings': [{'startTime': start, 'endTime': start, 'type': 'LIGHT'}] * short}}


def full_day(folder: Path) -> None:
    """One day with a point of every type that has a value of the day."""
    save(folder, 'heart-rate', DAY, [sample('heartRate', '2026-10-24T08:00:01Z', beatsPerMinute='60'),
                                     sample('heartRate', '2026-10-24T08:00:30Z', beatsPerMinute='80'),
                                     sample('heartRate', '2026-10-24T09:00:00Z', beatsPerMinute='100')])
    save(folder, 'daily-resting-heart-rate', DAY, [daily('dailyRestingHeartRate', beatsPerMinute='58')])
    save(folder, 'daily-heart-rate-variability', DAY, [daily(
        'dailyHeartRateVariability', averageHeartRateVariabilityMilliseconds=33.5,
        deepSleepRootMeanSquareOfSuccessiveDifferencesMilliseconds=42.2, nonRemHeartRateBeatsPerMinute='61', entropy=3.3)])
    save(folder, 'daily-oxygen-saturation', DAY, [daily('dailyOxygenSaturation', averagePercentage=95.6,
                                                        lowerBoundPercentage=93.5, upperBoundPercentage=97.8)])
    save(folder, 'daily-respiratory-rate', DAY, [daily('dailyRespiratoryRate', breathsPerMinute=13.2)])
    save(folder, 'respiratory-rate-sleep-summary', DAY, [sample(
        'respiratoryRateSleepSummary', '2026-10-24T08:32:00Z', source=DERIVED,
        deepSleepStats={'breathsPerMinute': 13.4}, lightSleepStats={'breathsPerMinute': 13.6},
        remSleepStats={'breathsPerMinute': 10.2}, fullSleepStats={'breathsPerMinute': 13.4})])
    save(folder, 'daily-sleep-temperature-derivations', DAY, [daily(
        'dailySleepTemperatureDerivations', nightlyTemperatureCelsius=33.19, baselineTemperatureCelsius='NaN')])
    save(folder, 'sleep', DAY, [night('2026-10-23T23:01:00Z', '2026-10-24T08:31:00Z', 542, 28,
                                      [('AWAKE', 28), ('LIGHT', 299), ('DEEP', 89), ('REM', 154)], short=2,
                                      deep_at='2026-10-23T23:16:00Z')])
    save(folder, 'steps', DAY, [interval('steps', '2026-10-24T09:16:00Z', '2026-10-24T09:17:00Z', count='100'),
                                interval('steps', '2026-10-24T10:16:00Z', '2026-10-24T10:17:00Z', count='20'),
                                interval('steps', '2026-10-24T09:16:00Z', '2026-10-24T09:17:00Z', source=PHONE, count='70')])
    save(folder, 'distance', DAY, [interval('distance', '2026-10-24T09:16:00Z', '2026-10-24T09:17:00Z', millimeters='3700'),
                                   interval('distance', '2026-10-24T10:16:00Z', '2026-10-24T10:17:00Z', millimeters='6300')])
    save(folder, 'active-energy-burned', DAY, [interval('activeEnergyBurned', '2026-10-24T09:16:00Z', '2026-10-24T09:17:00Z',
                                                        kcal=2.5)])
    save(folder, 'active-zone-minutes', DAY, [interval('activeZoneMinutes', '2026-10-24T09:16:00Z', '2026-10-24T09:17:00Z',
                                                       heartRateZone='FAT_BURN', activeZoneMinutes='1')])
    save(folder, 'active-minutes', DAY, [
        interval('activeMinutes', '2026-10-24T09:16:00Z', '2026-10-24T09:17:00Z',
                 activeMinutesByActivityLevel=[{'activityLevel': 'LIGHT', 'activeMinutes': '1'}]),
        interval('activeMinutes', '2026-10-24T09:17:00Z', '2026-10-24T09:19:00Z',
                 activeMinutesByActivityLevel=[{'activityLevel': 'MODERATE', 'activeMinutes': '2'}]),
        interval('activeMinutes', '2026-10-24T09:20:00Z', '2026-10-24T09:21:00Z', source=PHONE,
                 activeMinutesByActivityLevel=[{'activityLevel': 'VIGOROUS', 'activeMinutes': '1'}])])
    save(folder, 'sedentary-period', DAY, [interval('sedentaryPeriod', '2026-10-24T08:34:00Z', '2026-10-24T09:24:00Z',
                                                    source={'recordingMethod': 'DERIVED', 'platform': 'FITBIT'})])
    save(folder, 'total-calories', DAY, [rollup('totalCalories', '2026-10-24T09:25:00Z', '2026-10-24T09:26:00Z', kcalSum=1.5),
                                         rollup('totalCalories', '2026-10-24T09:26:00Z', '2026-10-24T09:27:00Z', kcalSum=2.0)],
         rollup=True)
    save(folder, 'calories-in-heart-rate-zone', DAY, [rollup(
        'caloriesInHeartRateZone', '2026-10-24T09:25:00Z', '2026-10-24T09:26:00Z',
        caloriesInHeartRateZones=[{'heartRateZone': 'LIGHT', 'kcal': 1.2}, {'heartRateZone': 'MODERATE', 'kcal': 5.8}])],
         rollup=True)
    save(folder, 'oxygen-saturation', DAY, [sample('oxygenSaturation', '2026-10-24T03:00:00Z', percentage=94.6)])
    save(folder, 'heart-rate-variability', DAY, [sample('heartRateVariability', '2026-10-24T03:05:00Z', source=DERIVED,
                                                        rootMeanSquareOfSuccessiveDifferencesMilliseconds=33.9)])


EXERCISE = {'dataSource': DERIVED, 'exercise': {
    'interval': {'startTime': '2026-10-23T21:22:04Z', 'endTime': '2026-10-23T21:37:28Z'}, 'exerciseType': 'YOGA',
    'displayName': 'Yoga', 'activeDuration': '921.267s',
    'metricsSummary': {'caloriesKcal': 27, 'averageHeartRateBeatsPerMinute': '77', 'activeZoneMinutes': '3'}}}


def exercise_file(folder: Path, *points: dict) -> None:
    """The list of all exercises, as google_api saves it."""
    (folder / 'exercise').mkdir(parents=True, exist_ok=True)
    (folder / 'exercise' / 'all.json').write_text(json.dumps({'pages': [{'dataPoints': list(points)}]}))


@pytest.fixture
def folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The Fitbit files of a test, from 23 Oct on."""
    monkeypatch.setattr(fitbit, 'first_day', lambda: date(2026, 10, 23))
    return fitbit.FOLDER


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """The app on 24 Oct 2026, noon."""
    monkeypatch.setattr(api, '_health_now', lambda: datetime(2026, 10, 24, 12, 0, tzinfo=TZ))
    monkeypatch.setattr(api, 'user_timezone', lambda: TZ)
    return TestClient(api.app)


def downloads(monkeypatch: pytest.MonkeyPatch, write: callable = lambda folder: None) -> list[tuple]:
    """A stand-in for the Google Health download that writes files with `write` on its first call (a later call finds
    nothing new); the calls it got."""
    calls = []

    def collect(creds: object, folder: Path, first: date, last: date, tz: ZoneInfo) -> dict:
        calls.append((creds, folder, first, last, tz))
        if len(calls) == 1:
            write(folder)
        return {'written': 3, 'failed': 0}

    monkeypatch.setattr(fitbit.health, 'collect', collect)
    monkeypatch.setattr(fitbit.auth, 'creds', lambda token_file: 'the token')
    return calls


def test_the_values_of_a_day_come_from_its_files_and_only_from_the_band(folder: Path) -> None:
    full_day(folder)
    values = fitbit.values(fitbit.health.rows(folder, date(2026, 10, 24)))
    assert values == {
        'fitbit:resting_hr': 58, 'fitbit:mean_hr': 80, 'fitbit:max_hr': 100, 'fitbit:hrv': 33.5, 'fitbit:hrv_deep': 42.2,
        'fitbit:non_rem_hr': 61, 'fitbit:spo2': 95.6, 'fitbit:spo2_low': 93.5, 'fitbit:spo2_high': 97.8,
        'fitbit:breathing': 13.2, 'fitbit:breathing_deep': 13.4, 'fitbit:breathing_light': 13.6, 'fitbit:breathing_rem': 10.2,
        'fitbit:skin_temp': 33.19, 'fitbit:asleep': 9.03, 'fitbit:awake': 28, 'fitbit:deep': 89, 'fitbit:light': 299,
        'fitbit:rem': 154, 'fitbit:to_deep_sleep': 15, 'fitbit:awakenings': 2, 'fitbit:steps': 120, 'fitbit:distance': 0.01,
        'fitbit:kcal': 4, 'fitbit:active_kcal': 2, 'fitbit:azm': 1, 'fitbit:active_light': 1, 'fitbit:active_moderate': 2,
        'fitbit:sedentary': 0.8, 'fitbit:kcal_light': 1, 'fitbit:kcal_moderate': 6}


def test_a_day_without_data_has_no_values_and_two_sleeps_add_up(folder: Path) -> None:
    assert fitbit.values([]) == {}
    save(folder, 'sleep', DAY, [night('2026-10-23T23:00:00Z', '2026-10-24T05:00:00Z', 300, 20, [('DEEP', 60)], short=1),
                                night('2026-10-24T12:00:00Z', '2026-10-24T13:00:00Z', 50, 5, [('DEEP', 0)], short=1)])
    values = fitbit.values(fitbit.health.rows(folder, date(2026, 10, 24)))
    assert (values['fitbit:asleep'], values['fitbit:awake'], values['fitbit:deep'], values['fitbit:awakenings']) == (5.83, 25, 60, 2)
    assert 'fitbit:rem' not in values and 'fitbit:to_deep_sleep' not in values


def test_the_time_to_deep_sleep_is_that_of_the_longest_sleep(folder: Path) -> None:
    save(folder, 'sleep', DAY, [night('2026-10-24T12:00:00Z', '2026-10-24T13:00:00Z', 50, 5, [('DEEP', 10)],
                                      deep_at='2026-10-24T12:05:00Z'),
                                night('2026-10-23T22:32:00Z', '2026-10-24T08:07:00Z', 543, 32, [('DEEP', 60)],
                                      deep_at='2026-10-23T23:41:00Z')])
    assert fitbit.values(fitbit.health.rows(folder, date(2026, 10, 24)))['fitbit:to_deep_sleep'] == 69


def test_a_download_gets_every_day_from_the_first_and_computes_only_the_days_whose_files_changed(
        folder: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def write(target: Path) -> None:
        full_day(target)
        save(target, 'steps', '2026-10-23', [interval('steps', '2026-10-23T09:16:00Z', '2026-10-23T09:17:00Z', count='7')])
        exercise_file(target, EXERCISE)

    calls = downloads(monkeypatch, write)
    found = fitbit.download(date(2026, 10, 24), TZ)
    assert calls == [('the token', folder, date(2026, 10, 23), date(2026, 10, 24), TZ)]
    assert found == {'written': 3, 'failed': 0, 'days': 2}
    assert fitbit.day_values('2026-10-23', '2026-10-24')['2026-10-23'] == {'fitbit:steps': 7, 'fitbit:exercises': 1,
                                                                           'fitbit:exercise_min': 15}
    assert fitbit.download(date(2026, 10, 24), TZ)['days'] == 0
    path = folder / 'steps' / '2026-10-23.json'
    path.write_text(path.read_text().replace('"7"', '"9"'))
    os.utime(path, ns=(path.stat().st_mtime_ns + 10**9, path.stat().st_mtime_ns + 10**9))
    assert fitbit.download(date(2026, 10, 24), TZ)['days'] == 1
    assert fitbit.day_values('2026-10-23', '2026-10-23')['2026-10-23']['fitbit:steps'] == 9
    monkeypatch.setattr(fitbit, 'VALUES_VERSION', fitbit.VALUES_VERSION + 1)
    assert fitbit.download(date(2026, 10, 24), TZ)['days'] == 2, 'a new way to compute computes every day again'


def test_the_exercises_belong_to_the_local_day_they_start(folder: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    late = {**EXERCISE, 'exercise': {**EXERCISE['exercise'], 'interval': {'startTime': '2026-10-23T22:10:00Z',
                                                                          'endTime': '2026-10-23T22:40:00Z'}}}
    downloads(monkeypatch, lambda target: exercise_file(target, EXERCISE, late))
    fitbit.download(date(2026, 10, 24), TZ)
    with ledger.connect() as db:
        stored = [dict(r) for r in db.execute('SELECT * FROM fitbit_exercises ORDER BY start_at')]
    assert [(e['day'], e['name'], e['active_min'], e['mean_hr'], e['kcal'], e['azm']) for e in stored] == [
        ('2026-10-23', 'Yoga', 15.4, 77, 27, 3), ('2026-10-24', 'Yoga', 15.4, 77, 27, 3)]


def test_the_status_is_a_look_only(folder: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    status = fitbit.status()
    assert (status['configured'], status['token'], status['downloaded_at']) == (True, False, None)
    assert 'Log in to Google Health' in status['message']
    fitbit.TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    fitbit.TOKEN_FILE.write_text('{}')
    assert fitbit.status()['message'] == ''
    monkeypatch.setattr(fitbit, 'first_day', lambda: None)
    assert 'first_day' in fitbit.status()['message']


def test_the_charts_of_a_day_have_the_hr_per_minute_and_the_sleep_that_touches_it(folder: Path,
                                                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    full_day(folder)
    save(folder, 'sleep', '2026-10-25', [night('2026-10-24T21:30:00Z', '2026-10-25T06:00:00Z', 480, 30, [('DEEP', 80)])])
    downloads(monkeypatch, lambda target: exercise_file(target, EXERCISE))
    fitbit.download(date(2026, 10, 24), TZ)
    detail = fitbit.day_detail('2026-10-23')
    assert detail['exercises'][0]['name'] == 'Yoga'
    detail = fitbit.day_detail(DAY)
    minute = int(datetime.fromisoformat('2026-10-24T08:00:00+00:00').timestamp() * 1000)
    assert detail['hr'][0] == [minute, 70.0]
    assert detail['spo2'] == [[int(datetime.fromisoformat('2026-10-24T03:00:00+00:00').timestamp() * 1000), 94.6]]
    assert len(detail['hrv']) == 1
    assert [s[1] for s in detail['steps']] == [100, 20]
    assert [(s['start'], s['type']) for s in detail['stages']] == [('2026-10-23T23:01:00Z', 'LIGHT'),
                                                                  ('2026-10-23T23:16:00Z', 'DEEP'),
                                                                  ('2026-10-24T21:30:00Z', 'LIGHT')]
    assert detail['exercises'] == []


def test_the_health_page_has_the_fitbit_card_and_the_history_its_values(folder: Path, client: TestClient,
                                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    calls = downloads(monkeypatch, full_day)
    view = client.post(f'/api/health/fitbit/download?day={DAY}').json()
    assert calls[0][3:] == (date(2026, 10, 24), TZ)
    card = view['fitbit']
    assert card['downloaded_at'] is not None
    assert card['day']['fitbit:resting_hr'] == 58
    assert client.get('/api/health?day=2026-10-23').json()['fitbit']['day'] is None
    history = client.get('/api/health/history?days=7').json()
    row = next(r for r in history['days'] if r['day'] == DAY)
    assert row['fitbit:steps'] == 120
    assert analysis.known('fitbit:steps')
    found = client.get('/api/health/analysis?key=fitbit:steps&days=30').json()
    assert found['outliers'] == []
    detail = client.get(f'/api/health/fitbit/day?day={DAY}').json()
    assert detail['hr'][0][1] == 70.0


def test_a_refused_token_asks_for_the_health_login(folder: Path, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(token_file: Path) -> None:
        raise AuthError('Google login expired: invalid_grant')

    monkeypatch.setattr(fitbit.auth, 'creds', refuse)
    answer = client.post(f'/api/health/fitbit/download?day={DAY}')
    assert (answer.status_code, answer.json()['auth']) == (401, True)
    logins = []
    monkeypatch.setattr(fitbit.auth, 'login', lambda client_file, token_file, scopes: logins.append((token_file, scopes)))
    assert client.post(f'/api/health/fitbit/login?day={DAY}').status_code == 200
    assert logins == [(fitbit.TOKEN_FILE, fitbit.health.SCOPES)]


def test_failed_requests_are_told_on_the_card(folder: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    downloads(monkeypatch)
    monkeypatch.setattr(fitbit.health, 'collect', lambda *args: {'written': 0, 'failed': 4})
    fitbit.TOKEN_FILE.parent.mkdir(parents=True)
    fitbit.TOKEN_FILE.write_text('{}')
    fitbit.download(date(2026, 10, 24), TZ)
    assert '4' in fitbit.status()['message']


def test_a_breathing_rate_of_0_is_a_stage_that_was_not_measured(folder: Path) -> None:
    save(folder, 'respiratory-rate-sleep-summary', DAY, [
        sample('respiratoryRateSleepSummary', '2026-10-24T08:32:00Z', source=DERIVED, deepSleepStats={'breathsPerMinute': 13.4},
               remSleepStats={'breathsPerMinute': 10.2}),
        sample('respiratoryRateSleepSummary', '2026-10-24T08:31:00Z', source=DERIVED, deepSleepStats={'breathsPerMinute': 12.6},
               remSleepStats={'breathsPerMinute': 0})])
    values = fitbit.values(fitbit.health.rows(folder, date(2026, 10, 24)))
    assert (values['fitbit:breathing_deep'], values['fitbit:breathing_rem']) == (13.0, 10.2)
