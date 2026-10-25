"""Ketostix readings: several a day, each with its time and one of the six colours of the strip; the history shows the
highest reading of the day, and the analysis has them without an outlier list."""

from datetime import date

import pytest

from health_dashboard import analysis, history, store

DAY = date(2026, 10, 8)


def test_a_day_keeps_each_reading_with_its_time_in_time_order() -> None:
    late = store.add_ketone_reading(DAY, '21:30', 1.5)
    store.add_ketone_reading(DAY, '07:10', 0.5)
    assert [(r['taken_at'], r['level']) for r in store.ketone_readings(DAY)] == [('07:10', 0.5), ('21:30', 1.5)]
    assert store.set_ketone_time(late, '20:45') == DAY
    assert [r['taken_at'] for r in store.ketone_readings(DAY)] == ['07:10', '20:45']
    assert store.remove_ketone_reading(late) == DAY
    assert [r['level'] for r in store.ketone_readings(DAY)] == [0.5]
    assert store.ketone_readings(date(2026, 10, 7)) == []


def test_only_a_colour_of_the_strip_and_a_clock_time_are_taken() -> None:
    assert [k['level'] for k in store.KETOSTIX] == [0, 0.5, 1.5, 4, 8, 16]
    with pytest.raises(ValueError, match='Ketostix'):
        store.add_ketone_reading(DAY, '07:10', 2)
    with pytest.raises(ValueError, match='HH:MM'):
        store.add_ketone_reading(DAY, '7h', 0.5)
    with pytest.raises(KeyError):
        store.set_ketone_time(999, '08:00')
    with pytest.raises(KeyError):
        store.remove_ketone_reading(999)


def test_the_history_shows_the_highest_reading_of_the_day_and_how_many() -> None:
    for taken_at, level in [('07:10', 0.5), ('13:00', 4), ('21:30', 1.5)]:
        store.add_ketone_reading(DAY, taken_at, level)
    rows = {r['day']: r for r in history.days(3, DAY)}
    assert (rows['2026-10-08']['ketones'], rows['2026-10-08']['ketone_readings']) == (4.0, 3)
    assert (rows['2026-10-07']['ketones'], rows['2026-10-07']['ketone_readings']) == (None, 0)


def test_the_analysis_of_the_ketones_has_no_outliers() -> None:
    for n in range(20):
        store.add_ketone_reading(date(2026, 9, 19 + n) if n < 12 else date(2026, 10, n - 11), '08:00', 16 if n == 5 else 0.5)
    found = analysis.analyse('ketones', 30, 30, DAY)
    assert analysis.known('ketones') and found['stats']['count'] == 20 and found['outliers'] == []
    assert analysis.analyse('ketones', None, 30, DAY)['start'] == '2026-09-19', 'all data starts at the first reading'
