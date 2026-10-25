"""The analysis of one value of the Health history: the rolling mean with its band, the numbers, the histogram, the days
far from the days around them, the change from one day to the next, the last week against earlier weeks, each year by month,
and the relation with a second value; for any period, all data too."""

import random
from datetime import date, timedelta

import pytest

from health_dashboard import analysis, ledger, store, supplements, withings
from tests.polar_fixtures import stored_session

TODAY = date(2026, 10, 8)
CODES = {name: code for code, name in withings.TYPES.items()}


def measure(day: date, grpid: int, **values: float) -> None:
    """A Withings group of whole-body values at 08:00 on a day."""
    with ledger.connect(write=True) as db:
        db.executemany('INSERT INTO withings_values (grpid, type, position, value, measured_at) VALUES (?, ?, -1, ?, ?)',
                       [(grpid, CODES[name], value, f'{day.isoformat()}T08:00') for name, value in values.items()])


def test_the_rolling_mean_and_its_band_take_the_days_around_each_value() -> None:
    for n in range(10):
        measure(TODAY - timedelta(days=9 - n), n + 1, weight=80 + n)
    found = analysis.analyse('weight', 10, 7, TODAY)
    points = {p['day']: p for p in found['points']}
    middle = points['2026-10-03']
    assert (middle['value'], middle['mean']) == (84.0, 84.0), 'the 3 days before and the 3 days after'
    assert middle['std'] == pytest.approx(2.1602, abs=1e-4)
    assert points['2026-09-29']['mean'] == 81.5, 'the first day has only the days after it'


def test_a_value_far_from_the_days_around_it_is_an_outlier() -> None:
    for n in range(21):
        day = TODAY - timedelta(days=20 - n)
        measure(day, n + 1, fat_ratio=9.7 if n == 10 else 18 + (n % 3) * 0.5)
    found = analysis.analyse('fat_ratio', 30, 30, TODAY)
    assert [(o['day'], o['value']) for o in found['outliers']] == [('2026-09-28', 9.7)], 'only the wrong weigh-in'
    assert found['outliers'][0]['z'] < -3.5 and found['outliers'][0]['expected'] == 18.5, 'the median of the days around'


def test_the_numbers_the_histogram_and_the_years() -> None:
    for n, value in enumerate([10, 20, 30, 40]):
        measure(date(2026, 10, 5) + timedelta(days=n), n + 1, weight=value)
    found = analysis.analyse('weight', 30, 30, TODAY)
    stats = found['stats']
    assert (stats['count'], stats['mean'], stats['median']) == (4, 25.0, 25.0)
    assert stats['std'] == pytest.approx(12.9099, abs=1e-4)
    assert stats['min'] == {'day': '2026-10-05', 'value': 10.0} and stats['max'] == {'day': '2026-10-08', 'value': 40.0}
    assert stats['trend_per_month'] == pytest.approx(10 * 30.4375), '10 more each day'
    assert sum(found['histogram']['counts']) == 4
    edges = found['histogram']['edges']
    assert edges[0] <= 10 and edges[-1] >= 40 and len(edges) == len(found['histogram']['counts']) + 1
    assert found['years'] == [{'year': 2026, 'months': [None] * 9 + [25.0, None, None]}]


def test_all_data_starts_at_the_first_measurement() -> None:
    measure(date(2015, 3, 2), 1, weight=85)
    measure(TODAY, 2, weight=89)
    found = analysis.analyse('weight', None, 30, TODAY)
    assert [p['day'] for p in found['points']] == ['2015-03-02', '2026-10-08']
    assert [y['year'] for y in found['years']] == [2015, 2026]


def test_a_removed_measurement_is_not_in_the_analysis() -> None:
    measure(TODAY - timedelta(days=1), 1, weight=88.9)
    measure(TODAY, 2, weight=89.5)
    with ledger.connect(write=True) as db:
        db.execute("INSERT INTO withings_hidden (grpid, hidden_at) VALUES (1, '2026-10-08T21:00')")
    assert [p['day'] for p in analysis.analyse('weight', 30, 30, TODAY)['points']] == ['2026-10-08']


def test_angina_has_no_outliers_and_a_strange_name_is_not_known() -> None:
    with ledger.connect(write=True) as db:
        db.executemany('INSERT INTO health_angina (day, score) VALUES (?, ?)',
                       [((TODAY - timedelta(days=n)).isoformat(), 5 if n == 3 else 0) for n in range(20)])
    found = analysis.analyse('angina', 30, 30, TODAY)
    assert found['stats']['count'] == 20 and found['outliers'] == [], 'a score the user gave is not a wrong measurement'
    assert all(analysis.known(k) for k in ('weight', 'systolic', 'heart_rate', 'muscle_mass:left_arm', 'muscle_ratio',
                                           'angina'))
    assert not any(analysis.known(k) for k in ('day', 'readings', 'weight:nose', 'nothing', 'tape:nose', 'tape'))


def test_a_tape_site_is_a_value_of_the_analysis() -> None:
    store.set_tape(date(2024, 5, 5), 'waist', 95.0)
    store.set_tape(TODAY, 'waist', 91.5)
    assert analysis.known('tape:waist') and analysis.known('tape:upper_arm_left')
    found = analysis.analyse('tape:waist', None, 30, TODAY)
    assert [p['day'] for p in found['points']] == ['2024-05-05', '2026-10-08'], 'all data starts at the first tape measurement'


def test_the_day_to_day_change_takes_only_days_that_follow_each_other() -> None:
    for n, (day, value) in enumerate([(date(2026, 10, 1), 80), (date(2026, 10, 2), 81), (date(2026, 10, 3), 80.5),
                                      (date(2026, 10, 5), 82)]):
        measure(day, n + 1, weight=value)
    steps = analysis.analyse('weight', 30, 30, TODAY)['steps']
    assert (steps['count'], steps['median_size'], steps['p90_size']) == (2, 0.75, 1.0), '3 to 5 October is two days apart'
    assert sum(steps['histogram']['counts']) == 2


def test_the_last_week_against_the_same_week_30_90_and_365_days_earlier() -> None:
    def weight(day: date) -> float:
        if day > TODAY - timedelta(days=7):
            return 82
        return 79 if date(2026, 7, 4) <= day <= date(2026, 7, 10) else 80

    days = [TODAY - timedelta(days=n) for n in range(400)]
    with ledger.connect(write=True) as db:
        db.executemany('INSERT INTO withings_values (grpid, type, position, value, measured_at) VALUES (?, 1, -1, ?, ?)',
                       [(n + 1, weight(day), f'{day.isoformat()}T08:00') for n, day in enumerate(days)])
    earlier = analysis.analyse('weight', 90, 30, TODAY)['earlier']
    assert [(e['ago'], e['mean'], e['diff']) for e in earlier] == [(0, 82.0, None), (30, 80.0, 2.0), (90, 79.0, 3.0),
                                                                   (365, 80.0, 2.0)], 'a year back, also in a period of 90 days'
    assert (earlier[0]['from'], earlier[0]['to'], earlier[3]['from']) == ('2026-10-02', '2026-10-08', '2025-10-02')


def test_the_relation_of_two_values_takes_the_days_with_both() -> None:
    for n in range(10):
        measure(TODAY - timedelta(days=n), n + 1, weight=80 + n, fat_ratio=20 + 2 * n)
    measure(TODAY - timedelta(days=12), 50, weight=70)
    found = analysis.relation('fat_ratio', 'weight', 30, TODAY)
    assert found['n'] == 10 and found['r'] == pytest.approx(1.0), 'the day with a weight and no fat % is not a pair'
    assert (found['slope'], found['intercept']) == (pytest.approx(2.0), pytest.approx(-140.0))
    assert found['points'][0] == {'day': '2026-09-29', 'x_day': '2026-09-29', 'x': 89.0, 'y': 38.0}
    empty = analysis.relation('angina', 'weight', 30, TODAY)
    assert (empty['n'], empty['r'], empty['slope'], empty['lag']) == (0, None, None, 0)
    assert all(d['r'] is None and d['n'] == 0 for d in empty['by_lag'])


def test_a_delayed_relation_is_strongest_at_its_delay() -> None:
    rng = random.Random(7)
    fat = {TODAY - timedelta(days=n): round(rng.uniform(15, 25), 2) for n in range(45)}
    for n, (day, value) in enumerate(sorted(fat.items())):
        earlier = fat.get(day - timedelta(days=3))
        measure(day, n + 1, fat_ratio=value, **({'weight': 60 + earlier} if earlier is not None else {}))
    found = analysis.relation('weight', 'fat_ratio', 60, TODAY, lag=3)
    assert (found['lag'], found['n'], found['r']) == (3, 42, pytest.approx(1.0)), 'the weight follows fat % 3 days later'
    first = found['points'][0]
    assert (first['day'], first['x_day']) == ((TODAY - timedelta(days=41)).isoformat(), (TODAY - timedelta(days=44)).isoformat())
    by_lag = found['by_lag']
    assert [d['lag'] for d in by_lag] == list(range(31))
    assert max(by_lag, key=lambda d: abs(d['r'] or 0))['lag'] == 3 and by_lag[3]['n'] == 42
    assert by_lag[30]['n'] == 15
    short = analysis.relation('weight', 'fat_ratio', 20, TODAY, lag=30)
    assert short['n'] == 15 and short['points'][0]['x_day'] < (TODAY - timedelta(days=19)).isoformat(), \
        'the earlier value may lie before the period'


def test_the_large_view_has_each_reading_of_its_value_at_its_time() -> None:
    measure(TODAY, 1, weight=82.0)
    with ledger.connect(write=True) as db:
        db.execute("INSERT INTO withings_values (grpid, type, position, value, measured_at) VALUES (2, ?, -1, 83.0, ?)",
                   (CODES['weight'], f'{TODAY.isoformat()}T21:30'))
    found = analysis.analyse('weight', 30, 7, TODAY)
    assert found['readings'] == [{'at': '2026-10-08T08:00', 'day': '2026-10-08', 'value': 82.0},
                                 {'at': '2026-10-08T21:30', 'day': '2026-10-08', 'value': 83.0}]
    assert [p['value'] for p in found['points']] == [82.5], 'the analysis keeps the day mean'
    assert analysis.analyse('angina', 30, 7, TODAY)['readings'] == [], 'a value of the day has no readings'


def test_a_value_of_the_sessions_is_analysed_session_by_session_without_day_to_day_change() -> None:
    stored_session('2026-10-07T08:00:00+00:00', [120.0] * 300, None)
    stored_session('2026-10-07T18:00:00+00:00', [140.0] * 300, None)
    found = analysis.analyse('h10:max_hr', 30, 7, TODAY)
    assert (found['per'], found['stats']['count'], found['stats']['mean'], found['steps']) == ('session', 2, 130.0, None)
    assert [r['value'] for r in found['readings']] == [120.0, 140.0]
    assert [p['value'] for p in found['points']] == [120.0, 140.0], 'each session, not the mean of the day'


def test_each_supplement_taken_against_a_value_by_day() -> None:
    first = TODAY - timedelta(days=9)
    zinc = supplements.add_supplement(first, 'Zinc', 'mg', 25)
    iron = supplements.add_supplement(first, 'Iron', 'mg', 225)
    supplements.add_supplement(first, 'Never taken', 'mg', 5)
    taken = {first + timedelta(days=n): 25 * (n % 3 + 1) for n in range(10)}
    for n, (day, amount) in enumerate(sorted(taken.items())):
        supplements.add_intake(day, zinc, '09:00', amount)
        supplements.add_intake(day, iron, '19:00')
        before = taken.get(day - timedelta(days=1))
        if before is not None:
            measure(day, n + 1, systolic=100 + before / 5)
    found = analysis.supplement_relations('systolic', 30, TODAY, lag=1)
    assert [(f['name'], f['unit'], f['other']) for f in found] == [('Zinc', 'mg', f'supp:{zinc}'),
                                                                   ('Iron', 'mg', f'supp:{iron}')], \
        'a supplement not taken in these days has no chart'
    by_name = {f['name']: f for f in found}
    assert (by_name['Zinc']['n'], by_name['Zinc']['r'], by_name['Zinc']['lag']) == (9, pytest.approx(1.0), 1), \
        'the pressure follows the zinc of the day before'
    assert by_name['Zinc']['points'][0] == {'day': (first + timedelta(days=1)).isoformat(), 'x_day': first.isoformat(),
                                            'x': 25, 'y': 105}
    assert (by_name['Iron']['n'], by_name['Iron']['r']) == (9, None), 'the same amount every day: no correlation'
    assert analysis.supplement_relations('systolic', 30, TODAY, lag=0)[0]['r'] != pytest.approx(1.0)
    with pytest.raises(ValueError, match='session'):
        analysis.supplement_relations('h10:mean_hr', 30, TODAY)
