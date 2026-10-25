"""The history of the Health page: one row per day with the amount of each supplement taken, the angina score and the
Withings values."""

from datetime import date

from health_dashboard import history, ledger, measurements, store, supplements

TODAY = date(2026, 10, 8)


def measure(grpid: int, measured_at: str, **values: float) -> None:
    """A Withings measurement group in the ledger; a value of a body part is named kind__part (muscle_mass__left_arm)."""
    types = {name: code for code, name in history.withings.TYPES.items()}
    parts = {name: code for code, name in history.withings.PARTS.items()}
    rows = []
    for key, value in values.items():
        name, _, part = key.partition('__')
        rows.append((grpid, types[name], parts[part] if part else -1, value, measured_at))
    with ledger.connect(write=True) as db:
        db.executemany('INSERT INTO withings_values (grpid, type, position, value, measured_at) VALUES (?, ?, ?, ?, ?)', rows)


def test_each_day_has_its_supplements_angina_pressure_and_scale_values() -> None:
    calcium = supplements.add_supplement(date(2026, 10, 6), 'Calcium', 'mg', 100)
    supplements.add_intake(date(2026, 10, 6), calcium, '08:00')
    supplements.add_intake(TODAY, calcium, '08:00', 200)
    store.set_angina(date(2026, 10, 7), 2, 'stairs')
    measure(1, '2026-10-08T07:30', systolic=140, diastolic=90, pulse=70)
    measure(2, '2026-10-08T07:33', systolic=134, diastolic=86, pulse=66)
    measure(3, '2026-10-08T07:45', weight=82.4, fat_ratio=24.2, muscle_mass=61.0, pulse=58, visceral_fat=4.4,
            muscle_mass__left_arm=4.5, muscle_mass__right_arm=4.2)
    measure(4, '2026-10-08T21:10', weight=83.0, fat_ratio=24.6)
    measure(5, '2026-10-07T01:20', systolic=150, diastolic=95, pulse=80)
    rows = history.days(4, TODAY)
    assert [r['day'] for r in rows] == ['2026-10-05', '2026-10-06', '2026-10-07', '2026-10-08']
    assert rows[0] == {'day': '2026-10-05', 'angina': None, 'angina_not_measured': False, 'ketones': None, 'ketone_readings': 0,
                       'systolic': None, 'diastolic': None, 'pulse': None, 'readings': 0}, \
        'a day without scale values or supplements has none of them'
    assert [r.get(f'supp:{calcium}') for r in rows[1:]] == [100, None, 200], 'a day without any entry has no supplement values'
    assert (rows[2]['angina'], rows[2]['systolic'], rows[2]['readings']) == (2, 150.0, 1), 'a reading at 01:20 belongs to the new day'
    assert (rows[3]['systolic'], rows[3]['diastolic'], rows[3]['pulse'], rows[3]['readings']) == (137.0, 88.0, 68.0, 2), 'day average'
    assert (rows[3]['weight'], rows[3]['fat_ratio'], rows[3]['muscle_mass']) == (82.7, 24.4, 61.0), 'the day average of the scale'
    assert rows[3]['muscle_ratio'] == round(61.0 / 82.4 * 100, 3), 'muscle % from the weight of the same measurement'
    assert (rows[3]['visceral_fat'], rows[3]['muscle_mass:left_arm'], rows[3]['muscle_mass:right_arm']) == (4.4, 4.5, 4.2)
    assert rows[3]['heart_rate'] == 58.0, "the scale's pulse is its own value, not mixed with the blood pressure pulse"


def test_a_day_with_angina_not_measured_has_no_score_in_the_history() -> None:
    store.set_angina(TODAY, None, '', not_measured=True)
    row = history.days(7, TODAY)[-1]
    assert (row['angina'], row['angina_not_measured']) == (None, True), 'not a score of 0'


def test_the_tape_measurements_are_values_of_their_day() -> None:
    store.set_tape(TODAY, 'waist', 91.8)
    store.set_tape(TODAY, 'thigh_right', 56.2)
    rows = history.days(7, TODAY)
    assert (rows[-1]['tape:waist'], rows[-1]['tape:thigh_right']) == (91.8, 56.2)
    assert 'tape:waist' not in rows[-2], 'a day without a measurement has no value'


def test_each_recording_is_a_point_at_its_time_and_a_removed_one_is_not() -> None:
    measure(1, '2026-10-08T07:30', systolic=140, diastolic=90, pulse=70)
    measure(2, '2026-10-08T07:33', systolic=134, diastolic=86, pulse=66)
    measure(3, '2026-10-08T07:45', weight=82.4, muscle_mass=61.0, pulse=58, muscle_mass__left_arm=4.5)
    measure(5, '2026-10-07T01:20', systolic=150, diastolic=95, pulse=80)
    measure(6, '2026-10-06T08:00', systolic=200, diastolic=120)
    measurements.hide([6])
    store.add_ketone_reading(TODAY, '22:10', 4)
    store.add_ketone_reading(TODAY, '09:05', 0.5)
    points = history.chart_points(date(2026, 10, 5), TODAY)
    assert [p for p in points if 'systolic' in p] == [
        {'at': '2026-10-07T01:20', 'day': '2026-10-07', 'systolic': 150.0, 'diastolic': 95.0, 'pulse': 80.0},
        {'at': '2026-10-08T07:30', 'day': '2026-10-08', 'systolic': 140.0, 'diastolic': 90.0, 'pulse': 70.0},
        {'at': '2026-10-08T07:33', 'day': '2026-10-08', 'systolic': 134.0, 'diastolic': 86.0, 'pulse': 66.0}], \
        'each reading, a removed one not'
    assert [p for p in points if 'weight' in p] == [
        {'at': '2026-10-08T07:45', 'day': '2026-10-08', 'weight': 82.4, 'muscle_mass': 61.0, 'heart_rate': 58.0,
         'muscle_mass:left_arm': 4.5, 'muscle_ratio': round(61.0 / 82.4 * 100, 3)}]
    assert [p for p in points if 'ketones' in p] == [{'at': '2026-10-08T09:05', 'day': '2026-10-08', 'ketones': 0.5},
                                                     {'at': '2026-10-08T22:10', 'day': '2026-10-08', 'ketones': 4.0}]
