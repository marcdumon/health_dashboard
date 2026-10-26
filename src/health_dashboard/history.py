"""The history of the Health page: per day the amount of each supplement taken (supplements: supp:<id>, on the days of the
log), the angina score, the highest Ketostix reading and how many, the day's average blood pressure and its pulse, the
day's average of every value of the scale, the tape measurements, the values of the HRV tests (with their band keys),
the HR recovery tests and the stairs tests, the sleep of the day (Sleep as Android: the sleeps that end on it, added
up), the food of the day (food: the scales, the marks, the times and the fast days), the Fitbit values of the day
(fitbit), and the values derived from the Withings readings (trends: the trend of each body composition value and the
home blood pressure), and the training load (polar_load: totals of a period, not values of a session); a value of a body
part is named kind:part ('muscle_mass:left_arm', 'tape:waist'). The values of the Polar H10 sessions have no value of a
day (a day mixes activities): each session is a point (chart_points), as is each recording. A day starts at
store.DAY_START_HOUR, for the Withings readings and the sessions too."""

from datetime import date, datetime, timedelta

from health_dashboard import (
    fitbit,
    food,
    ledger,
    polar_hrv,
    polar_load,
    polar_recovery,
    polar_stairs,
    polar_store,
    sleep,
    store,
    supplements,
    trends,
    withings,
)

PRESSURE = ('systolic', 'diastolic', 'pulse')


def _scale_key(kind: int, position: int) -> str:
    """The history key of a scale value: its kind, with its body part for an arm, a leg or the trunk."""
    name = withings.TYPES[kind]
    # the scale's own pulse (standing) is not the blood pressure monitor's: it is kept apart
    name = 'heart_rate' if name == 'pulse' else name
    return f'{name}:{withings.PARTS[position]}' if position in withings.PARTS else name


def days(count: int, today: date) -> list[dict]:
    """One row per day for the `count` days up to today, oldest first. A value missing on a day is not in its row."""
    first = today - timedelta(days=count - 1)
    rows = {(first + timedelta(days=n)).isoformat(): {
        'day': (first + timedelta(days=n)).isoformat(), 'angina': None,
        'angina_not_measured': False, 'ketones': None, 'ketone_readings': 0, **dict.fromkeys(PRESSURE), 'readings': 0}
        for n in range(count)}
    with ledger.connect() as db:
        for row in db.execute('SELECT day, score, not_measured FROM health_angina WHERE day >= ?', (first.isoformat(),)):
            if row[0] in rows:
                rows[row[0]] |= {'angina': row[1], 'angina_not_measured': bool(row[2])}
        for row in db.execute('SELECT day, MAX(level), COUNT(*) FROM health_ketones WHERE day >= ? GROUP BY day',
                              (first.isoformat(),)):
            if row[0] in rows:
                rows[row[0]] |= {'ketones': row[1], 'ketone_readings': row[2]}
        for day, site, cm in db.execute('SELECT day, site, cm FROM health_tape WHERE day >= ?', (first.isoformat(),)):
            if day in rows:
                rows[day][f'tape:{site}'] = cm
    readings: dict[str, list[dict]] = {}
    scale: dict[str, dict[str, list[float]]] = {}
    for group in _groups(first):
        if group['day'] not in rows:
            continue
        names = {withings.TYPES[kind]: value for kind, _, value in group['values']}
        if 'systolic' in names:
            readings.setdefault(group['day'], []).append(names)
            continue
        day_values = scale.setdefault(group['day'], {})
        for kind, position, value in group['values']:
            # every reading of the day counts: the user measures at any time, not only in the morning
            day_values.setdefault(_scale_key(kind, position), []).append(value)
        if (ratio := _muscle_ratio(group)) is not None:
            day_values.setdefault('muscle_ratio', []).append(ratio)
    for day, by_key in scale.items():
        for key, found in by_key.items():
            rows[day][key] = round(sum(found) / len(found), 3)
    for day, found in readings.items():
        rows[day]['readings'] = len(found)
        for name in PRESSURE:
            values = [r[name] for r in found if name in r]
            rows[day][name] = round(sum(values) / len(values), 1) if values else None
    for day, values in polar_hrv.day_values(first.isoformat(), today.isoformat()).items():
        if day in rows:
            rows[day] |= values
    for source in (polar_recovery, polar_stairs, sleep, food, fitbit, trends, polar_load, supplements):
        for day, values in source.day_values(first.isoformat(), today.isoformat()).items():
            if day in rows:
                rows[day] |= values
    return list(rows.values())


def _groups(first: date) -> list[dict]:
    """The Withings measurement groups from day `first` on, in time order, without the ones the user removed as wrong: the
    time (local, as stored), the day and the values (kind, body part, value)."""
    with ledger.connect() as db:
        start = f'{first.isoformat()}T{store.DAY_START_HOUR:02d}:00'
        measures = db.execute(f'SELECT grpid, type, position, value, measured_at FROM withings_values WHERE measured_at >= ? '
                              f'AND {withings.NOT_HIDDEN} ORDER BY measured_at, grpid', (start,)).fetchall()
    groups: dict[int, dict] = {}
    for grpid, kind, position, value, measured_at in measures:
        day = store.health_day(datetime.fromisoformat(measured_at)).isoformat()
        groups.setdefault(grpid, {'at': measured_at, 'day': day, 'values': []})['values'].append((kind, position, value))
    return list(groups.values())


def _muscle_ratio(group: dict) -> float | None:
    """The muscle % of a measurement group, from its whole-body muscle mass and weight; None without them."""
    return withings.muscle_ratio({withings.TYPES[kind]: value for kind, position, value in group['values']
                                  if position == withings.NO_PART})


def chart_points(first: date, last: date, activity: int | None = None) -> list[dict]:
    """Each recording from day `first` to day `last` as a point at its time, for the charts that show every recording of a
    day: a blood pressure reading, a measurement of the scale, a Ketostix reading, a Polar H10 session (with `activity`,
    only its sessions), an HRV test, an HR recovery test and a stairs test. Each point has its time (`at`: local as
    stored, or UTC with its offset), its day and its values under their history keys; the points of each kind are in time
    order."""
    points = []
    for group in _groups(first):
        if group['day'] > last.isoformat():
            continue
        names = {withings.TYPES[kind]: value for kind, _, value in group['values']}
        if 'systolic' in names:
            values = {name: names[name] for name in PRESSURE if name in names}
        else:
            values = {_scale_key(kind, position): value for kind, position, value in group['values']}
            if (ratio := _muscle_ratio(group)) is not None:
                values['muscle_ratio'] = round(ratio, 3)
        points.append({'at': group['at'], 'day': group['day'], **values})
    with ledger.connect() as db:
        points += [{'at': f'{day}T{taken_at}', 'day': day, 'ketones': level} for day, taken_at, level in db.execute(
            'SELECT day, taken_at, level FROM health_ketones WHERE day >= ? AND day <= ? ORDER BY day, taken_at, id',
            (first.isoformat(), last.isoformat()))]
    span = (first.isoformat(), last.isoformat())
    return [*points, *polar_store.session_points(*span, activity), *polar_hrv.points(*span), *polar_recovery.points(*span),
            *polar_stairs.points(*span)]
