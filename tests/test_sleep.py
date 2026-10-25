"""Sleep from the Sleep as Android backup: the values of each sleep from its record, the values of a day (the sleeps that
end on it, added up), Import sleep that replaces every sleep, the card's look at the backup file, and the routes."""

import csv
import io
import os
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from health_dashboard import api, ledger, sleep
from health_dashboard import store as health_store

TZ = ZoneInfo('Europe/Brussels')
NOW = datetime(2026, 7, 4, 12, 0, tzinfo=TZ)


def at(text: str) -> datetime:
    """A local time in Brussels."""
    return datetime.fromisoformat(text).replace(tzinfo=TZ)


def ms(text: str) -> int:
    """A local time in ms since 1970, as the events of the app have it."""
    return int(at(text).timestamp() * 1000)


def app_time(text: str) -> str:
    """A local time as the app writes it: 24. 01. 2026 2:15."""
    moment = at(text)
    return f'{moment:%d. %m. %Y} {moment.hour}:{moment:%M}'


def stage(kind: str, start: str, end: str) -> list[str]:
    """The two events of a period of one stage (LIGHT, DEEP, REM or AWAKE)."""
    return [f'{kind}_START-{ms(start)}', f'{kind}_END-{ms(end)}']


def record(start: str, end: str, *, len_adjust: int = -10, deep: float = 0.2, cycles: int = 3, events: tuple[str, ...] = (),
           noise: bool = True) -> list[list[str]]:
    """The rows of one sleep as the app writes them: its header row, its values row and (for a tracked sleep) the noise
    row."""
    hours = (at(end) - at(start)).total_seconds() / 3600
    header = ['Id', 'Tz', 'From', 'To', 'Sched', 'Hours', 'Rating', 'Comment', 'Framerate', 'Snore', 'Noise', 'Cycles',
              'DeepSleep', 'LenAdjust', 'Geo', '2:20', '2:25', *['Event'] * len(events)]
    values = [str(ms(start)), 'Europe/Brussels', app_time(start), app_time(end), app_time(end), f'{hours:.3f}', '0.0', '',
              '10008', '0', '0.0019155252', str(cycles), str(deep), str(len_adjust), '', '1.5', '2.5', *events]
    return [header, values, *([[''] * 15 + ['435.07993', '758.1633']] if noise else [])]


def backup(path: Path, *records: list[list[str]]) -> Path:
    """A backup zip as SleepCloud writes it, with these sleeps."""
    text = io.StringIO()
    csv.writer(text).writerows([row for rows in records for row in rows])
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_STORED) as archive:
        archive.writestr('sleep-export.csv', text.getvalue())
        archive.writestr('prefs.xml', '<map />')
    return path


# a night with every value: REM over the light sleep, 4 minutes awake over the light sleep and 2 over the deep sleep (as
# the app draws them), a snore (left out) and an event with a value
NIGHT = record('2026-07-01T02:00', '2026-07-01T09:00', deep=0.15, events=(
    *stage('LIGHT', '2026-07-01T02:05', '2026-07-01T03:00'), *stage('DEEP', '2026-07-01T03:00', '2026-07-01T04:00'),
    *stage('AWAKE', '2026-07-01T03:30', '2026-07-01T03:32'),
    *stage('LIGHT', '2026-07-01T04:00', '2026-07-01T08:55'), *stage('REM', '2026-07-01T05:00', '2026-07-01T05:30'),
    *stage('AWAKE', '2026-07-01T06:00', '2026-07-01T06:04'), f'SNORING-{ms("2026-07-01T07:00")}',
    f'DHA-{ms("2026-07-01T02:00")}-1.8E-43'))
# a nap on the same day: no REM
NAP = record('2026-07-01T13:00', '2026-07-01T14:30', len_adjust=-5, cycles=1, events=(
    *stage('LIGHT', '2026-07-01T13:00', '2026-07-01T13:40'), *stage('DEEP', '2026-07-01T13:40', '2026-07-01T14:30')))
# a sleep the user added by hand: no stages, no cycles
MANUAL = record('2026-07-02T01:00', '2026-07-02T08:00', len_adjust=0, deep=-1.0, cycles=-1, noise=False)
# a long night with stages but no REM event
NO_REM = record('2026-07-03T01:00', '2026-07-03T07:00', len_adjust=0, events=(
    *stage('LIGHT', '2026-07-03T01:00', '2026-07-03T03:00'), *stage('DEEP', '2026-07-03T03:00', '2026-07-03T04:00'),
    *stage('LIGHT', '2026-07-03T04:00', '2026-07-03T07:00')))
# a recording started and stopped at once: not a sleep
EMPTY = record('2026-07-02T12:00', '2026-07-02T12:00', len_adjust=0, deep=-1.0, cycles=-1, noise=False)
# a sleep over midnight
LATE = record('2026-07-03T23:30', '2026-07-04T07:00', len_adjust=0, events=(
    *stage('LIGHT', '2026-07-03T23:30', '2026-07-04T07:00'),))


@pytest.fixture
def file(tmp_path, monkeypatch) -> Path:
    """The place of the backup in this test, set as the backup file."""
    path = tmp_path / 'Sleep as Android Data.zip'
    monkeypatch.setattr(sleep, 'backup_file', lambda: path)
    return path


def stored() -> dict[str, dict]:
    """The stored sleeps by their start."""
    with ledger.connect() as db:
        return {row['start_at']: dict(row) for row in db.execute('SELECT * FROM sleep_records')}


def test_each_sleep_has_its_values_and_a_value_not_measured_is_none() -> None:
    text = io.StringIO()
    csv.writer(text).writerows([row for rows in (NIGHT, MANUAL, EMPTY, NO_REM, LATE) for row in rows])
    found = {s['start_at']: s for s in sleep.parse(text.getvalue())}
    assert '2026-07-02T12:00:00+02:00' not in found and len(found) == 4, 'no sleep time: not a sleep'
    night = found['2026-07-01T02:00:00+02:00']
    assert {k: v for k, v in night.items() if k not in ('id', 'tz', 'start_at')} == {
        'end_at': '2026-07-01T09:00:00+02:00', 'day': '2026-07-01', 'in_bed_min': 420.0, 'sleep_min': 410.0, 'awake_min': 6.0,
        'deep_min': 58.0, 'light_min': 316.0, 'rem_min': 30.0, 'cycles': 3}, 'each minute in one stage only; no snoring'
    assert night['id'] == ms('2026-07-01T02:00')
    manual = found['2026-07-02T01:00:00+02:00']
    assert (manual['sleep_min'], manual['awake_min'], manual['deep_min'], manual['light_min'], manual['rem_min'],
            manual['cycles']) == (420.0, None, None, None, None, None)
    no_rem = found['2026-07-03T01:00:00+02:00']
    assert (no_rem['deep_min'], no_rem['light_min'], no_rem['rem_min'], no_rem['awake_min']) == (
        60.0, 300.0, None, 0.0), 'no REM event: no REM value, not 0'
    assert found['2026-07-03T23:30:00+02:00']['day'] == '2026-07-04', 'a sleep belongs to the day on which it ends'


def test_a_day_adds_up_its_sleeps_and_its_deep_share_is_weighted_by_minutes(file) -> None:
    backup(file, NIGHT, NAP, MANUAL, NO_REM, LATE)
    sleep.import_backup()
    days = sleep.day_values('2026-07-01', '2026-07-04')
    assert days['2026-07-01'] == {
        'sleep:total': 8.25, 'sleep:count': 2, 'sleep:in_bed': 8.5, 'sleep:awake': 6.0, 'sleep:deep': 108.0,
        'sleep:light': 356.0, 'sleep:rem': 30.0, 'sleep:deep_pct': 21.9, 'sleep:cycles': 4}
    assert days['2026-07-02'] == {'sleep:total': 7.0, 'sleep:count': 1, 'sleep:in_bed': 7.0}, 'nothing measured: no value'
    assert days['2026-07-04']['sleep:total'] == 7.5 and '2026-07-04' not in sleep.day_values('2026-07-01', '2026-07-03')
    assert [(s['start_at'], s['day']) for s in sleep.sleeps('2026-07-03', '2026-07-04')] == [
        ('2026-07-03T01:00:00+02:00', '2026-07-03'), ('2026-07-03T23:30:00+02:00', '2026-07-04')]
    card = sleep.day_card('2026-07-01')
    assert card['values'] == days['2026-07-01'] and [(s['start_at'], s['end_at'], s['sleep_min']) for s in card['sleeps']] == [
        ('2026-07-01T02:00:00+02:00', '2026-07-01T09:00:00+02:00', 410.0),
        ('2026-07-01T13:00:00+02:00', '2026-07-01T14:30:00+02:00', 85.0)]
    assert sleep.day_card('2026-07-05') is None


def test_import_replaces_every_sleep_and_a_page_read_only_looks_at_the_file(file) -> None:
    backup(file, NIGHT, NAP, MANUAL)
    assert sleep.status(NOW)['waiting'] is True and health_store.setting(sleep.BACKUP_KEY) is None, 'a read changes nothing'
    assert sleep.import_backup() == 3
    after = sleep.status(NOW)
    assert (after['waiting'], after['message'], after['configured']) == (False, '', True) and after['imported_at']
    # the user changed the night in the app and deleted the sleep added by hand
    backup(file, record('2026-07-01T02:00', '2026-07-01T09:00', len_adjust=-20), NAP)
    later = os.stat(file).st_mtime + 60
    os.utime(file, (later, later))
    assert sleep.status(NOW)['waiting'] is True
    assert sleep.import_backup() == 2
    assert {start: s['sleep_min'] for start, s in stored().items()} == {
        '2026-07-01T02:00:00+02:00': 400.0, '2026-07-01T13:00:00+02:00': 85.0}


def test_a_backup_that_cannot_be_read_or_holds_no_sleep_changes_nothing(file) -> None:
    backup(file, NIGHT)
    sleep.import_backup()
    backup(file, NIGHT, NAP)
    data = file.read_bytes()
    file.write_bytes(data.replace(b'Europe/Brussels', b'Europe/Brusselz', 1))
    with pytest.raises(sleep.SleepError, match='Dropbox may still be writing it'):
        sleep.import_backup()
    backup(file)
    with pytest.raises(sleep.SleepError, match='holds no sleep'):
        sleep.import_backup()
    assert list(stored()) == ['2026-07-01T02:00:00+02:00'] and sleep.status(NOW)['waiting'] is True


def test_the_card_says_what_to_do_when_the_backup_is_not_set_missing_or_old(file, monkeypatch) -> None:
    missing = sleep.status(NOW)
    assert (missing['configured'], missing['backup_at']) == (True, None) and str(file) in missing['message']
    backup(file, NIGHT)
    old = (NOW - timedelta(days=10)).timestamp()
    os.utime(file, (old, old))
    assert sleep.status(NOW)['message'].startswith('The newest backup is from 24 June 2026. On the phone, open Sleep as Android')
    monkeypatch.setattr(sleep, 'backup_file', lambda: None)
    assert sleep.status(NOW)['message'] == sleep.NOT_SET and sleep.status(NOW)['configured'] is False
    with pytest.raises(sleep.SleepError, match='backup_file'):
        sleep.import_backup()


@pytest.fixture
def client(file, monkeypatch) -> TestClient:
    """A client at noon on 4 July, with a backup of every kind of sleep made an hour before."""
    backup(file, NIGHT, NAP, MANUAL, NO_REM, LATE)
    made = (NOW - timedelta(hours=1)).timestamp()
    os.utime(file, (made, made))
    monkeypatch.setattr(api, '_health_now', lambda: NOW)
    return TestClient(api.app)


def test_import_sleep_fills_the_card_the_history_and_the_analysis(client, monkeypatch) -> None:
    before = client.get('/api/health?day=2026-07-01').json()['sleep']
    assert (before['waiting'], before['day'], before['backup_at']) == (True, None, '2026-07-04T11:00+02:00')
    view = client.post('/api/health/sleep/import?day=2026-07-01', json={}).json()
    assert (view['day'], view['sleep']['waiting'], view['sleep']['day']['values']['sleep:total']) == ('2026-07-01', False, 8.25)
    history = client.get('/api/health/history?days=7').json()
    row = next(r for r in history['days'] if r['day'] == '2026-07-01')
    assert (row['sleep:deep_pct'], row['sleep:count']) == (21.9, 2)
    assert [s['day'] for s in history['sleeps']] == ['2026-07-01', '2026-07-01', '2026-07-02', '2026-07-03', '2026-07-04']
    analysis = client.get('/api/health/analysis?key=sleep:total&days=7').json()
    assert analysis['outliers'] == [] and analysis['stats']['count'] == 4
    assert client.get('/api/health/relation?key=sleep:total&other=sleep:deep&days=7').status_code == 200
    week = client.get('/api/health/sleep/times?days=7').json()
    assert (week['first'], week['last'], len(week['sleeps'])) == ('2026-06-28', '2026-07-04', 5)
    every = client.get('/api/health/sleep/times').json()
    assert (every['first'], every['last'], every['sleeps'][-1]['end_at']) == (
        '2026-07-01', '2026-07-04', '2026-07-04T07:00:00+02:00'), 'all data: from the first sleep on'
    monkeypatch.setattr(sleep, 'backup_file', lambda: None)
    refused = client.post('/api/health/sleep/import', json={})
    assert (refused.status_code, refused.json()['detail']) == (409, sleep.NOT_SET)
