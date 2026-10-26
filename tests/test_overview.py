# the texts of the tiles use the true minus sign and the en dash
# ruff: noqa: RUF001
"""The overview row of the Health page: a tile for the weight, the blood pressure, the resting HR, ln RMSSD, the sleep and
the activity of the week, each with its value, its change against the 30-day mean, 30 days of values for a sparkline and a
status; a tile without data says what to do and never shows a zero; and its route."""

from datetime import date, datetime, timedelta

from fastapi.testclient import TestClient

from health_dashboard import api, ledger, overview
from tests.polar_fixtures import set_person, stored_session
from tests.test_trends import reading

DAY = date(2026, 7, 31)


def tiles(day: date = DAY) -> dict[str, dict]:
    """The tiles of the overview of `day`, by key."""
    return {tile['key']: tile for tile in overview.overview(day)}


def sleep(day: date, hours: float) -> None:
    """A night that ends at 07:00 on `day` with `hours` of sleep."""
    with ledger.connect(write=True) as db:
        number = db.execute('SELECT COUNT(*) FROM sleep_records').fetchone()[0] + 1
        end = datetime(day.year, day.month, day.day, 7, 0)
        db.execute('INSERT INTO sleep_records (id, tz, start_at, end_at, day, in_bed_min, sleep_min, awake_min, deep_min, '
                   "light_min, rem_min, cycles) VALUES (?, 'Europe/Brussels', ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, NULL)",
                   (number, f'{end - timedelta(hours=hours):%Y-%m-%dT%H:%M}:00+02:00', f'{end:%Y-%m-%dT%H:%M}:00+02:00',
                    day.isoformat(), hours * 60, hours * 60))


def test_without_data_every_tile_says_what_to_do_and_shows_no_zero() -> None:
    found = tiles()
    assert list(found) == ['weight', 'pressure', 'rest_hr', 'ln_rmssd', 'sleep', 'activity']
    for tile in found.values():
        assert (tile['value'], tile['text'], tile['change'], tile['status']) == (None, '', None, None), tile['key']
        assert tile['empty'] and tile['spark'] == [None] * 30
    assert found['activity']['empty'].startswith('The activity needs birth_date')


def test_the_weight_tile_has_the_last_weigh_in_its_change_and_the_rate_as_its_status() -> None:
    for n in range(30):
        reading(n, f'{DAY - timedelta(days=29 - n)}T07:00', weight=90.0 - 0.1 * n)
    weight = tiles()['weight']
    # the last weigh-in 87.1 kg against the 30-day mean 88.55 kg; 0.1 kg a day is 0.7 kg a week
    assert (weight['value'], weight['text'], weight['change'], weight['unit']) == (87.1, '87.1', {'value': -1.45, 'unit': 'kg'},
                                                                                   'kg')
    assert (weight['status'], weight['status_text']) == ('falling', '−0.70 kg a week (95 % interval −0.70 to −0.70)')
    assert weight['spark'][0] == 90.0 and weight['spark'][-1] == 87.1 and len(weight['spark']) == 30


def test_the_pressure_tile_has_the_home_mean_and_is_above_the_usual_range_after_a_rise() -> None:
    for n in range(67):
        day = DAY - timedelta(days=66 - n)
        high = n >= 60
        reading(2 * n, f'{day}T08:00', systolic=140.0 if high else 120.0 + n % 3, diastolic=90.0 if high else 80.0)
        reading(2 * n + 1, f'{day}T20:00', systolic=136.0 if high else 118.0 + n % 3, diastolic=86.0 if high else 78.0)
    pressure = tiles()['pressure']
    assert (pressure['text'], pressure['unit'], pressure['status']) == ('138/88', 'mmHg', 'above')
    assert pressure['detail'] == 'Morning 140/90 (7 readings), evening 136/86 (7 readings); 14 readings'
    # the systolic day means of the 60 days before: 119, 120 and 121 in turn, SD 0.82
    assert pressure['status_text'] == 'above your usual systolic range 119–121 mmHg'


def test_the_sleep_tile_has_the_7_day_mean_and_the_activity_tile_the_who_minutes_of_the_week(monkeypatch) -> None:
    for n in range(30):
        sleep(DAY - timedelta(days=29 - n), 6.0 if n < 23 else 8.0)
    found = tiles()['sleep']
    assert (found['value'], found['text'], found['unit']) == (8.0, '8 h 00 min', '')
    assert found['change'] == {'value': round(8 * 60 - (23 * 6 + 7 * 8) / 30 * 60), 'unit': 'min'}
    set_person(monkeypatch, birth_date='1966-01-01')
    # 4 days before 31 July: 20 min at 110 bpm (moderate) and 10 min at 130 (vigorous): 20 + 2 x 10 = 40 in the last 7 days
    stored_session('2026-07-27T08:00:00+00:00', [110.0] * 1200 + [130.0] * 600, None)
    activity = tiles()['activity']
    assert (activity['value'], activity['text'], activity['unit'], activity['status']) == (40, '40', 'min', 'open')
    assert activity['status_text'] == '110 min to the WHO minimum of 150'
    assert activity['detail'] == 'Training load ratio: not yet, it needs 28 days of sessions'


def test_the_route_gives_the_tiles_of_the_day() -> None:
    reading(1, '2026-07-31T07:00', weight=80.0)
    found = TestClient(api.app).get('/api/health/overview?day=2026-07-31').json()
    assert [tile['key'] for tile in found['tiles']] == ['weight', 'pressure', 'rest_hr', 'ln_rmssd', 'sleep', 'activity']
    assert found['tiles'][0]['text'] == '80.0'
