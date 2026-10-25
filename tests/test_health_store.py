"""The Health page's own data: the day of a moment, the angina score, the tape measurements, settings."""

from datetime import date, datetime, timedelta

import pytest

from health_dashboard import store

DAY = date(2026, 10, 8)


@pytest.mark.parametrize('now, day', [(datetime(2026, 10, 8, 23, 59), DAY), (datetime(2026, 10, 9, 0, 0), date(2026, 10, 9)),
                                      (datetime(2026, 10, 9, 3, 35), date(2026, 10, 9))])
def test_a_moment_after_midnight_belongs_to_the_new_day(now: datetime, day: date) -> None:
    assert store.health_day(now) == day


def test_one_angina_score_a_day_with_a_note() -> None:
    assert store.angina(DAY) is None
    store.set_angina(DAY, 2, ' after the stairs ')
    assert store.angina(DAY) == {'score': 2, 'note': 'after the stairs', 'not_measured': False}
    store.set_angina(DAY, 3, 'after the stairs')
    assert store.angina(DAY) == {'score': 3, 'note': 'after the stairs', 'not_measured': False}
    store.set_angina(DAY, None, 'only a note')
    assert store.angina(DAY) == {'score': None, 'note': 'only a note', 'not_measured': False}
    store.set_angina(DAY, None, '')
    assert store.angina(DAY) is None, 'no score and no note: nothing is kept'
    with pytest.raises(ValueError, match='0 to 5'):
        store.set_angina(DAY, 6, '')


def test_settings_are_kept() -> None:
    assert store.setting('withings_refresh_token') is None
    store.set_setting('withings_refresh_token', 'r1')
    store.set_setting('withings_refresh_token', 'r2')
    assert store.setting('withings_refresh_token') == 'r2'
    store.set_setting('withings_refresh_token', None)
    assert store.setting('withings_refresh_token') is None


def test_a_day_without_exertion_has_angina_not_measured_and_no_score() -> None:
    store.set_angina(DAY, None, 'rest day', not_measured=True)
    assert store.angina(DAY) == {'score': None, 'note': 'rest day', 'not_measured': True}
    store.set_angina(DAY, 1, 'rest day')
    assert store.angina(DAY) == {'score': 1, 'note': 'rest day', 'not_measured': False}, 'a score replaces it'
    store.set_angina(DAY, None, '', not_measured=True)
    assert store.angina(DAY) == {'score': None, 'note': '', 'not_measured': True}, 'kept without a note too'
    with pytest.raises(ValueError, match='not both'):
        store.set_angina(DAY, 2, '', not_measured=True)


def test_the_tape_measurements_of_a_day() -> None:
    assert [site for site, _ in store.TAPE_SITES] == [
        'chest', 'waist', 'hips', 'upper_arm_left', 'upper_arm_right', 'thigh_left', 'thigh_right'], 'no neck, forearms, calves'
    assert (store.tape(DAY), store.last_tape(DAY)) == ({}, None)
    store.set_tape(DAY, 'waist', 92.34)
    store.set_tape(DAY, 'upper_arm_left', 33)
    assert store.tape(DAY) == {'waist': 92.3, 'upper_arm_left': 33.0}, 'one decimal'
    store.set_tape(DAY, 'waist', 91.8)
    assert store.tape(DAY)['waist'] == 91.8, 'a new value on the same day replaces the old one'
    store.set_tape(DAY, 'upper_arm_left', None)
    assert store.tape(DAY) == {'waist': 91.8}, 'an empty field removes its value'
    assert store.last_tape(DAY + timedelta(days=7)) == {'day': DAY.isoformat(), 'values': {'waist': 91.8}}
    assert store.last_tape(DAY) is None, 'only a day before counts'
    with pytest.raises(ValueError, match='from 10 to 250 cm'):
        store.set_tape(DAY, 'waist', 5)
    for gone in ('nose', 'neck'):
        with pytest.raises(ValueError, match='not a site of the tape measure'):
            store.set_tape(DAY, gone, 12)
