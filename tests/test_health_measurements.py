"""The measurements of one day, each weigh-in and each blood pressure reading apart, and removing a wrong one: it leaves the
history and the latest values, and it can be put back. Nothing is deleted at Withings."""

from datetime import date

from health_dashboard import history, ledger, measurements, withings

DAY = date(2026, 6, 26)


def group(grpid: int, measured_at: str, **values: float) -> None:
    """A Withings measurement group in the ledger."""
    types = {name: code for code, name in withings.TYPES.items()}
    with ledger.connect(write=True) as db:
        db.executemany('INSERT INTO withings_values (grpid, type, position, value, measured_at) VALUES (?, ?, -1, ?, ?)',
                       [(grpid, types[name], value, measured_at) for name, value in values.items()])


def a_day() -> None:
    """26 June: a good blood pressure reading, and a wrong weigh-in that Withings sent as three groups in one minute."""
    group(1, '2026-06-26T07:33', systolic=117, diastolic=78, pulse=65)
    group(2, '2026-06-26T14:43', weight=88.9, fat_ratio=9.7, muscle_mass=76.4, visceral_fat=2.8)
    group(3, '2026-06-26T14:43', pulse=96)
    group(4, '2026-06-26T14:43', hydration=50.1)
    group(5, '2026-06-27T07:17', weight=89.5, fat_ratio=18.5, muscle_mass=69.3)


def test_a_day_lists_each_weigh_in_and_each_blood_pressure_reading_apart() -> None:
    a_day()
    found = measurements.day(DAY)
    assert [(m['kind'], m['measured_at'], m['grpids']) for m in found] == [
        ('scale', '2026-06-26T14:43', [2, 3, 4]), ('pressure', '2026-06-26T07:33', [1])], 'newest first; one weigh-in, one reading'
    assert found[0]['values'] == {'weight': 88.9, 'fat_ratio': 9.7, 'muscle_mass': 76.4, 'visceral_fat': 2.8, 'heart_rate': 96.0,
                                  'hydration': 50.1}
    assert found[1]['values'] == {'systolic': 117.0, 'diastolic': 78.0, 'pulse': 65.0}


def test_a_removed_weigh_in_leaves_the_history_and_the_latest_values_but_not_the_blood_pressure() -> None:
    a_day()
    measurements.hide([2, 3, 4])
    row = next(r for r in history.days(3, date(2026, 6, 27)) if r['day'] == '2026-06-26')
    assert 'weight' not in row and row['systolic'] == 117.0, 'the good blood pressure reading of that day stays'
    assert [m['grpids'] for m in measurements.day(DAY)] == [[1]]
    assert withings.latest()['values']['fat_ratio']['measured_at'] == '2026-06-27T07:17'
    removed = measurements.removed()
    assert [(m['kind'], m['measured_at'], m['values']['fat_ratio']) for m in removed] == [('scale', '2026-06-26T14:43', 9.7)]
    measurements.show([2, 3, 4])
    assert measurements.removed() == [] and history.days(3, date(2026, 6, 27))[1]['weight'] == 88.9


def test_a_removal_stays_after_a_new_fetch_of_the_same_measurement() -> None:
    a_day()
    measurements.hide([2, 3, 4])
    with ledger.connect(write=True) as db:
        db.execute('DELETE FROM withings_values WHERE grpid = 2')
    group(2, '2026-06-26T14:43', weight=88.9, fat_ratio=9.7)
    assert [m['grpids'] for m in measurements.day(DAY)] == [[1]]
