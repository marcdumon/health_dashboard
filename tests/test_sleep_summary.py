"""The sleep pattern of a period from the main sleep of each day (its longest): the regularity of the bedtime and the wake
time, the weekdays against the weekend (by the wake day) with the social jetlag, and the main sleep against the ln RMSSD of
the HRV test of the same morning; and its route."""

from datetime import datetime

import numpy as np
from fastapi.testclient import TestClient

from health_dashboard import api, ledger, polar_hrv, sleep_summary


def night(start: str, end: str, minutes: float) -> None:
    """A sleep from `start` to `end` (local, Brussels summer time) with `minutes` of sleep; its day is the wake day."""
    with ledger.connect(write=True) as db:
        number = db.execute('SELECT COUNT(*) FROM sleep_records').fetchone()[0] + 1
        db.execute('INSERT INTO sleep_records (id, tz, start_at, end_at, day, in_bed_min, sleep_min, awake_min, deep_min, '
                   "light_min, rem_min, cycles) VALUES (?, 'Europe/Brussels', ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, NULL)",
                   (number, f'{start}:00+02:00', f'{end}:00+02:00', end[:10], minutes + 10, minutes))


def week() -> None:
    """Three weekday nights (Monday to Wednesday, a nap on Wednesday) and a weekend (Saturday and Sunday)."""
    night('2026-07-05T23:00', '2026-07-06T07:00', 450)
    night('2026-07-06T23:30', '2026-07-07T06:30', 400)
    night('2026-07-08T00:30', '2026-07-08T07:30', 420)
    night('2026-07-08T14:00', '2026-07-08T14:40', 40)
    night('2026-07-11T01:00', '2026-07-11T09:00', 470)
    night('2026-07-12T00:30', '2026-07-12T09:30', 500)


def test_the_pattern_of_the_main_sleeps_of_the_weekdays_and_the_weekend() -> None:
    week()
    found = sleep_summary.summary('2026-07-06', '2026-07-12')
    # bedtimes from noon 660, 690, 750, 780 and 750 minutes; wake times 420, 390, 450, 540 and 570
    assert (found['nights'], found['bedtime_sd'], found['wake_sd']) == (5, 49, 78), 'the nap is not a main sleep'
    assert found['weekday'] == {'nights': 3, 'sleep_min': 423, 'bedtime': '23:40', 'wake': '07:00', 'mid': '03:20'}
    assert found['weekend'] == {'nights': 2, 'sleep_min': 485, 'bedtime': '00:45', 'wake': '09:15', 'mid': '05:00'}
    assert found['jetlag_min'] == 100
    assert found['hrv'] == {'pairs': 0, 'r': None, 'slope': None}


def test_the_main_sleep_against_the_ln_rmssd_of_the_same_morning_needs_10_mornings(monkeypatch) -> None:
    days = [f'2026-07-{d:02d}' for d in range(1, 13)]
    hours = [6.0, 7.5, 8.0, 5.5, 7.0, 6.5, 8.5, 7.0, 6.0, 7.5, 9.0, 5.0]
    for day, h in zip(days, hours, strict=True):
        night(f'{day}T00:00', f'{day}T08:00', h * 60)
    ln = [3.0 + 0.1 * h for h in hours]
    monkeypatch.setattr(polar_hrv, 'tests', lambda first, last: [{'day': d, 'ln_rmssd': v} for d, v in zip(days, ln, strict=True)
                                                                 if first <= d <= last])
    found = sleep_summary.summary('2026-07-01', '2026-07-12')['hrv']
    assert (found['pairs'], found['r'], found['slope']) == (12, 1.0, 0.1), 'ln RMSSD 0.1 higher for each hour of sleep'
    assert sleep_summary.summary('2026-07-01', '2026-07-09')['hrv'] == {'pairs': 9, 'r': None, 'slope': None}
    assert np.isclose(found['r'], 1.0)


def test_a_period_without_sleep_has_no_pattern_and_the_route_gives_it(monkeypatch) -> None:
    assert sleep_summary.summary('2026-07-06', '2026-07-12') is None
    week()
    monkeypatch.setattr(api, '_health_now', lambda: datetime(2026, 7, 12, 20, 0).astimezone())
    assert TestClient(api.app).get('/api/health/sleep/summary?days=7').json()['nights'] == 5
