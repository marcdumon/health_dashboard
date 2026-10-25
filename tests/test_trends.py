"""The values derived from the Withings readings: the trend of each body composition value (10 % a day, gaps allowed
for), the rate of the weight
over 28 days with its 95 % interval, and the home blood pressure of the ESH method (the readings of the 7 days up to a day
without the first day) with the morning and the evening apart; a hidden reading counts nowhere."""

from datetime import date

import pytest

from health_dashboard import history, ledger, trends

KINDS = {'weight': 1, 'fat_ratio': 6, 'diastolic': 9, 'systolic': 10, 'pulse': 11, 'muscle_mass': 76,
         'pulse_wave_velocity': 91, 'vascular_age': 155, 'nerve_health': 167}


def reading(grpid: int, measured_at: str, hidden: bool = False, **values: float) -> None:
    """One Withings measurement group: scale values (weight, fat %, muscle mass), or a blood pressure reading (systolic
    and diastolic)."""
    with ledger.connect(write=True) as db:
        for name, value in values.items():
            db.execute('INSERT INTO withings_values (grpid, type, position, value, measured_at) VALUES (?, ?, -1, ?, ?)',
                       (grpid, KINDS[name], value, measured_at))
        if hidden:
            db.execute("INSERT INTO withings_hidden (grpid, hidden_at) VALUES (?, '2026-07-01T09:00')", (grpid,))


def test_the_trend_weight_moves_10_percent_a_day_towards_each_day_mean_and_more_after_a_gap() -> None:
    reading(1, '2026-06-30T07:00', weight=80.0)
    reading(2, '2026-07-01T07:00', weight=80.5)
    reading(3, '2026-07-01T19:00', weight=81.5)
    reading(4, '2026-07-04T07:00', weight=82.0)
    reading(5, '2026-07-04T08:00', hidden=True, weight=120.0)
    found = trends.day_values('2026-07-01', '2026-07-04')
    # 1 July: 80 + 0.1 x (81 - 80); 4 July, 3 days later: 80.1 + (1 - 0.9^3) x (82 - 80.1)
    assert {day: values['weight_trend'] for day, values in found.items() if 'weight_trend' in values} == {
        '2026-07-01': 80.1, '2026-07-04': 80.61}
    assert history.days(4, date(2026, 7, 4))[0]['weight_trend'] == 80.1, 'in the history, the day before is the start'


def test_each_body_composition_value_has_its_own_trend_and_the_muscle_percent_comes_from_each_measurement() -> None:
    reading(1, '2026-06-30T07:00', weight=80.0, fat_ratio=25.0, muscle_mass=32.0)
    reading(2, '2026-07-01T07:00', weight=80.0, fat_ratio=24.0, muscle_mass=34.0)
    reading(3, '2026-07-02T07:00', fat_ratio=22.0)
    reading(4, '2026-07-02T08:00', hidden=True, fat_ratio=40.0)
    found = trends.day_values('2026-07-01', '2026-07-02')
    # fat: 25 + 0.1 x (24 - 25), then 24.9 + 0.1 x (22 - 24.9); muscle %: 32 / 80 = 40 %, then 34 / 80 = 42.5 %
    assert found['2026-07-01'] == {'weight_trend': 80.0, 'fat_ratio_trend': 24.9, 'muscle_mass_trend': 32.2,
                                   'muscle_ratio_trend': 40.25}
    assert found['2026-07-02'] == {'fat_ratio_trend': 24.61}, 'a day with only a fat % has only its trend'


def test_the_rate_of_the_weight_is_the_slope_of_28_days_with_its_interval() -> None:
    for n in range(10):
        reading(n, f'2026-07-{n * 3 + 1:02d}T07:00', weight=90.0 - 0.3 * n)
    found = trends.rate(date(2026, 7, 28))
    # 0.3 kg every 3 days: 0.7 kg a week
    assert (found['kg_week'], found['days'], found['status']) == (-0.7, 10, 'falling')
    assert found['low'] == found['high'] == -0.7, 'on a straight line the interval has no width'


def test_a_weight_that_goes_up_and_down_is_steady_and_too_few_days_give_no_rate() -> None:
    for n in range(10):
        reading(n, f'2026-07-{n + 1:02d}T07:00', weight=80.0 + (n % 2))
    found = trends.rate(date(2026, 7, 10))
    assert found['status'] == 'steady' and found['low'] < 0 < found['high']
    assert trends.rate(date(2026, 7, 5)) is None, '5 weigh-in days: fewer than 8'


def test_the_home_blood_pressure_leaves_out_the_first_of_the_7_days_and_splits_the_morning_and_the_evening() -> None:
    reading(1, '2026-07-01T08:00', systolic=180.0, diastolic=110.0)
    reading(2, '2026-07-02T08:00', systolic=130.0, diastolic=80.0)
    reading(3, '2026-07-03T21:00', systolic=120.0, diastolic=76.0)
    reading(4, '2026-07-05T07:30', systolic=128.0, diastolic=82.0)
    reading(5, '2026-07-07T19:00', systolic=118.0, diastolic=74.0)
    reading(6, '2026-07-07T20:00', hidden=True, systolic=200.0, diastolic=120.0)
    found = trends.home_pressure(date(2026, 7, 7))
    assert found == {'systolic': 124.0, 'diastolic': 78.0, 'readings': 4,
                     'morning': {'systolic': 129.0, 'diastolic': 81.0, 'readings': 2},
                     'evening': {'systolic': 119.0, 'diastolic': 75.0, 'readings': 2}}
    days = trends.day_values('2026-07-06', '2026-07-07')
    assert days['2026-07-07'] == {'systolic_7d': 124.0, 'diastolic_7d': 78.0}, 'without the 180/110 of 1 July'
    assert days['2026-07-06'] == {'systolic_7d': 139.5, 'diastolic_7d': 87.0}, 'from 1 to 6 July: with it'
    assert trends.home_pressure(date(2026, 7, 4)) is None


def test_the_heart_and_vessel_values_have_a_7_day_mean_of_at_least_3_readings() -> None:
    reading(1, '2026-07-01T08:00', systolic=130.0, diastolic=80.0, pulse=60.0)
    reading(2, '2026-07-04T08:00', systolic=128.0, diastolic=82.0, pulse=70.0)
    reading(3, '2026-07-07T08:00', systolic=126.0, diastolic=78.0, pulse=80.0)
    reading(4, '2026-07-05T09:00', weight=80.0, pulse=95.0, pulse_wave_velocity=7.1, vascular_age=61.0, nerve_health=76.0)
    reading(5, '2026-07-06T09:00', weight=80.0, pulse=94.0, pulse_wave_velocity=7.4, vascular_age=62.0, nerve_health=78.0)
    reading(6, '2026-07-07T09:00', weight=80.0, pulse=90.0, pulse_wave_velocity=7.0, vascular_age=60.0, nerve_health=80.0)
    reading(7, '2026-07-07T10:00', hidden=True, weight=90.0, pulse=200.0, pulse_wave_velocity=20.0, vascular_age=99.0,
            nerve_health=1.0)
    days = trends.day_values('2026-07-06', '2026-07-08')
    seventh = days['2026-07-07']
    assert seventh['pulse_7d'] == 70.0, 'the pulse of the blood pressure monitor: 1, 4 and 7 July'
    assert seventh['heart_rate_7d'] == 93.0, 'the pulse of the scale apart, without the hidden 200'
    assert (seventh['pulse_wave_velocity_7d'], seventh['vascular_age_7d'], seventh['nerve_health_7d']) == (7.17, 61.0, 78.0)
    assert 'pulse_7d' not in days.get('2026-07-06', {}) and 'heart_rate_7d' not in days.get('2026-07-06', {}), 'only 2 readings'
    assert days['2026-07-08']['heart_rate_7d'] == 93.0 and 'pulse_7d' not in days['2026-07-08'], '1 July is out of the 7 days'


@pytest.mark.parametrize(('df', 't'), [(1, 12.706), (8, 2.306), (30, 2.042), (200, 1.96)])
def test_the_t_value_of_the_interval(df: int, t: float) -> None:
    assert trends.t95(df) == t
