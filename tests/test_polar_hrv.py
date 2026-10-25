"""The HRV tests: the values of a session with the HRV activity from its beats, the test of each day, the normal range and
the status of ln RMSSD, the history values and the card; HRV tests stay out of the exercise values."""

from datetime import date, timedelta

import numpy as np

from health_dashboard import polar_hrv, polar_store
from tests.polar_fixtures import stored_session

HRV = 6


def alternating(start_at: str, seconds: int, swing: float = 20.0) -> int:
    """An HRV test of `seconds` with beats of 1000 - swing and 1000 + swing ms in turn (RMSSD 2 x swing), still, 60 bpm."""
    session_id = stored_session(start_at, [60.0] * seconds, [8.0] * seconds, rr=[1000 - swing, 1000 + swing] * (seconds // 2))
    polar_store.set_activity(session_id, HRV)
    return session_id


def days_of(values: list[float], last: str = '2026-07-30') -> list[dict]:
    """A test with this ln RMSSD on each day up to `last`."""
    end = date.fromisoformat(last)
    return [{'day': (end - timedelta(days=len(values) - 1 - n)).isoformat(), 'ln_rmssd': v} for n, v in enumerate(values)]


def test_a_session_with_the_hrv_activity_has_its_values_and_beats_from_its_stored_beats() -> None:
    session_id = alternating('2026-07-01T06:00:00+00:00', 420)
    found = polar_hrv.values_of(session_id)
    assert (found['reason'], found['rest_hr'], found['rmssd'], found['ln_rmssd']) == (None, 60.0, 40.0, 3.69)
    detail = polar_hrv.detail_of(session_id, 1782885600)
    assert (len(detail['beats']), detail['beats'][0], detail['rmssd']) == (300, [60.98, 980.0, 1], 40.0)


def test_a_test_uses_the_time_chosen_at_its_start_and_a_test_from_before_the_choice_5_minutes() -> None:
    old = alternating('2026-07-01T06:00:00+00:00', 420)
    new = stored_session('2026-07-02T06:00:00+00:00', [60.0] * 420, [8.0] * 420, rr=[980.0, 1020.0] * 210, source='logger')
    polar_store.give_activity('2026-07-02T05:59:00+00:00', HRV, 180)
    assert [polar_hrv.values_of(i)['to_ms'] - polar_hrv.values_of(i)['from_ms'] for i in (old, new)] == [300_000, 180_000]
    assert len(polar_hrv.detail_of(new, 0)['beats']) == 180


def test_every_test_with_values_is_a_point_at_its_time_and_a_day_has_the_mean_of_its_tests() -> None:
    short = alternating('2026-07-01T06:00:00+00:00', 300)
    first = alternating('2026-07-01T06:30:00+00:00', 420, swing=30.0)
    second = alternating('2026-07-01T07:30:00+00:00', 420, swing=10.0)
    assert [(t['session_id'], t['rmssd']) for t in polar_hrv.tests('2026-07-01', '2026-07-01')] == [(first, 60.0), (second, 20.0)]
    assert [(p['at'], p['day'], p['h10:rmssd']) for p in polar_hrv.points('2026-07-01', '2026-07-01')] == [
        ('2026-07-01T06:30:00+00:00', '2026-07-01', 60.0), ('2026-07-01T07:30:00+00:00', '2026-07-01', 20.0)]
    day = polar_hrv.day_values('2026-07-01', '2026-07-01')['2026-07-01']
    assert (day['h10:rmssd'], day['h10:ln_rmssd_7d']) == (40.0, day['h10:ln_rmssd']), 'the mean of the two tests'
    assert polar_hrv.values_of(short)['reason'] == 'The test needs 6 minutes; this recording has 4 min 59 s.'


def test_the_normal_range_needs_14_tests_and_the_status_compares_the_7_day_mean() -> None:
    assert polar_hrv.status('2026-07-30', days_of([3.5] * 10)) == {'mean_7d': 3.5, 'cv_7d': 0.0, 'low': None, 'high': None,
                                                                    'status': None, 'missing': 4}
    steady = [3.4, 3.6] * 10
    spread = float(np.std(steady, ddof=1))
    within = polar_hrv.status('2026-07-30', days_of(steady))
    assert (within['low'], within['high'], within['status'], within['missing']) == (round(3.5 - 0.5 * spread, 2),
                                                                                    round(3.5 + 0.5 * spread, 2), 'within', 0)
    dropped = polar_hrv.status('2026-07-30', days_of(steady + [3.0] * 7))
    assert (dropped['mean_7d'], dropped['status']) == (3.0, 'below')
    assert polar_hrv.status('2026-09-30', days_of(steady))['mean_7d'] is None, 'no test in the last 7 days'


def test_the_history_gets_the_test_values_and_the_7_day_mean_and_the_card_the_last_test() -> None:
    alternating('2026-07-01T06:00:00+00:00', 420)
    last = alternating('2026-07-03T06:00:00+00:00', 420, swing=30.0)
    values = polar_hrv.day_values('2026-07-01', '2026-07-04')
    assert (values['2026-07-01']['h10:rmssd'], values['2026-07-03']['h10:rmssd']) == (40.0, 60.0)
    assert {'h10:ln_rmssd_7d': 3.69, 'h10:rest_hr_7d': 60.0}.items() <= values['2026-07-02'].items()
    fourth = values['2026-07-04']
    assert {'h10:ln_rmssd_7d': round((3.69 + 4.09) / 2, 2), 'h10:rest_hr_7d': 60.0}.items() <= fourth.items()
    assert not {'h10:rmssd', 'h10:ln_rmssd_cv7', 'h10:ln_rmssd_low'} & set(fourth), \
        'no test on the day, no normal range below 14 tests, and no coefficient of variation below 3 tests'
    card = polar_hrv.last_test('2026-07-10')
    assert (card['session_id'], card['rmssd'], card['status']['missing']) == (last, 60.0, 12)
    assert polar_hrv.last_test('2026-07-11') is None, 'more than 7 days before the day of the page'


def test_each_value_of_the_tests_has_a_7_day_mean_of_the_tests_of_the_7_days_up_to_each_day() -> None:
    first = alternating('2026-07-01T06:00:00+00:00', 420)
    alternating('2026-07-03T06:00:00+00:00', 420, swing=30.0)
    values = polar_hrv.day_values('2026-07-01', '2026-07-10')
    tested = {key for field, (key, _) in polar_hrv.DAY_KEYS.items() if polar_hrv.values_of(first)[field] is not None}
    assert {'h10:rmssd', 'h10:sdnn', 'h10:pnn50', 'h10:stress'} <= tested
    assert set(values['2026-07-02']) == {f'{key}_7d' for key in tested}, 'a day without a test has the means only'
    assert [values[day]['h10:rmssd_7d'] for day in ('2026-07-01', '2026-07-03', '2026-07-07', '2026-07-08')] == [
        40.0, 50.0, 50.0, 60.0], 'on 8 July the test of 1 July is more than 7 days back'
    assert '2026-07-10' not in values, 'no test in the 7 days up to it'


def test_the_7_day_coefficient_of_variation_of_ln_rmssd_needs_3_tests() -> None:
    # SD 0.1 (ddof 1) / mean 3.5 = 2.86 %
    assert polar_hrv.status('2026-07-30', days_of([3.4, 3.6, 3.5]))['cv_7d'] == 2.9
    assert polar_hrv.status('2026-07-30', days_of([3.4, 3.6]))['cv_7d'] is None
    for day in (1, 2, 3):
        alternating(f'2026-07-0{day}T06:00:00+00:00', 420, swing=10.0 * day)
    values = polar_hrv.day_values('2026-07-03', '2026-07-03')['2026-07-03']
    found = [round(float(np.log(20.0 * day)), 2) for day in (1, 2, 3)]
    assert values['h10:ln_rmssd_cv7'] == round(float(np.std(found, ddof=1)) / float(np.mean(found)) * 100, 1)


def test_hrv_tests_are_not_the_last_session_and_not_in_the_exercise_values() -> None:
    walk = stored_session('2026-07-01T08:00:00+00:00', [120.0] * 600, [200.0] * 600)
    alternating('2026-07-01T09:00:00+00:00', 420)
    assert polar_store.latest('2026-07-01')['last_exercise']['id'] == walk
    assert [p['h10:mean_hr'] for p in polar_store.session_points('2026-07-01', '2026-07-01')] == [120.0]
    assert polar_store.session_points('2026-07-01', '2026-07-01', activity=HRV) == [], 'the HRV activity as a filter: no values'


def test_a_test_with_a_dropout_has_a_reason_and_breaks_nothing() -> None:
    dropout = [1000.0] * 160 + [240_000.0] + [1000.0] * 20
    session_id = stored_session('2026-07-01T06:00:00+00:00', [60.0] * 420, [8.0] * 420, rr=dropout)
    polar_store.set_activity(session_id, HRV)
    assert polar_hrv.last_test('2026-07-01')['reason'] == 'Only 33 % of the beats are clean; the test needs 80 %.'
    assert polar_hrv.day_values('2026-07-01', '2026-07-01') == {}
    assert polar_hrv.detail_of(session_id, 1782885600)['reason'].startswith('Only 33 %')
