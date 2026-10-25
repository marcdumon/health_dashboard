"""Values derived from the Withings readings, for the history and the overview. The trend of each body composition value
(the weight, fat, muscle, bone, water, visceral fat, BMR and metabolic age) on each day it was measured: an exponential
moving average of the day means with 10 % a day (the Hacker's Diet), 1 - 0.9^n after a gap of n days, from WARMUP_DAYS
before the period so it has settled; the muscle % of each measurement comes from its muscle mass and weight. The rate of
the weight: the least-squares slope of the day means of the 28 days up to a day, in kg a week, with its 95 % interval; a
change counts only when the interval leaves out 0 (else "steady"). The home blood pressure of the ESH method: the mean of
the readings of the 7 days up to a day without the first of them (day d-5 to d), with the morning (before 12:00) and the
evening apart; no diagnostic thresholds. The 7-day mean of the other heart and vessel values (the pulse of the blood
pressure monitor, the heart rate of the scale, the pulse wave velocity, the vascular age and the nerve health score): the
mean of the readings of the 7 days up to a day, with at least ROLLING_MIN_READINGS. A hidden reading counts nowhere. A day
is the calendar day of the local time (store.health_day)."""

import math
from datetime import date, datetime, timedelta

from health_dashboard import ledger, store, withings

WEIGHT, DIASTOLIC, SYSTOLIC, PULSE = 1, 9, 10, 11
# the whole-body scale kinds with a trend: weight, fat %, fat mass, muscle mass, bone mass, water, extracellular and
# intracellular water, visceral fat, BMR and metabolic age; and the muscle %, from the muscle mass and the weight
TREND_KINDS = (WEIGHT, 6, 8, 76, 88, 77, 168, 169, 170, 226, 227)
TREND_NAMES = (*(withings.TYPES[kind] for kind in TREND_KINDS), 'muscle_ratio')
# the heart and vessel values with a 7-day mean: the pulse of the blood pressure monitor, the heart rate (the pulse) of the
# scale, the pulse wave velocity, the vascular age and the nerve health score
ROLLING_NAMES = ('pulse', 'heart_rate', 'pulse_wave_velocity', 'vascular_age', 'nerve_health')
ROLLING_KINDS = (SYSTOLIC, PULSE, 91, 155, 167)
# the history keys of the derived values
KEYS = (*(f'{name}_trend' for name in TREND_NAMES), 'systolic_7d', 'diastolic_7d', *(f'{name}_7d' for name in ROLLING_NAMES))
TREND_PER_DAY, WARMUP_DAYS = 0.1, 120
RATE_DAYS, RATE_MIN_DAYS = 28, 8
ESH_DAYS, ESH_MIN_READINGS, MORNING_END_HOUR = 7, 4, 12
ROLLING_DAYS, ROLLING_MIN_READINGS = 7, 3
# the two-sided 95 % t value for 1 to 30 degrees of freedom; 1.96 beyond
T95 = (12.706, 4.303, 3.182, 2.776, 2.571, 2.447, 2.365, 2.306, 2.262, 2.228, 2.201, 2.179, 2.160, 2.145, 2.131, 2.120,
       2.110, 2.101, 2.093, 2.086, 2.080, 2.074, 2.069, 2.064, 2.060, 2.056, 2.052, 2.048, 2.045, 2.042)


def t95(df: int) -> float:
    """The two-sided 95 % t value for `df` degrees of freedom."""
    return T95[df - 1] if df <= len(T95) else 1.96


def _rows(kinds: tuple[int, ...], since: date) -> list[tuple[int, int, float, datetime]]:
    """The whole-body Withings values of these kinds from day `since` on, without the hidden ones: grpid, kind, value and
    local time, oldest first."""
    with ledger.connect() as db:
        rows = db.execute(f"SELECT grpid, type, value, measured_at FROM withings_values WHERE type IN ({', '.join('?' * len(kinds))}) "
                          f'AND position = ? AND measured_at >= ? AND {withings.NOT_HIDDEN} ORDER BY measured_at, grpid',
                          (*kinds, withings.NO_PART, since.isoformat())).fetchall()
    return [(grpid, kind, value, datetime.fromisoformat(at)) for grpid, kind, value, at in rows]


def _day_means(kinds: tuple[int, ...], since: date, last: date) -> dict[str, dict[date, float]]:
    """The mean of each scale value of these kinds on each day from `since` to `last` it was measured, by name, oldest day
    first; with the weight and the muscle mass, also the muscle % of each measurement."""
    groups: dict[int, dict] = {}
    for grpid, kind, value, at in _rows(kinds, since):
        groups.setdefault(grpid, {'at': at, 'values': {}})['values'][withings.TYPES[kind]] = value
    found: dict[str, dict[date, list[float]]] = {}
    for group in groups.values():
        day = store.health_day(group['at'])
        if day > last:
            continue
        values = group['values']
        if (ratio := withings.muscle_ratio(values)) is not None:
            values = values | {'muscle_ratio': ratio}
        for name, value in values.items():
            found.setdefault(name, {}).setdefault(day, []).append(value)
    return {name: {day: sum(values) / len(values) for day, values in days.items()} for name, days in found.items()}


def _weights(since: date, last: date) -> dict[date, float]:
    """The mean weight of each weigh-in day from `since` to `last`, oldest first."""
    return _day_means((WEIGHT,), since, last).get('weight', {})


def _trend(means: dict[date, float]) -> dict[date, float]:
    """The trend of each day with a mean: 10 % a day towards the day mean, 1 - 0.9^n after a gap of n days."""
    found: dict[date, float] = {}
    trend, before = None, None
    for day, mean in means.items():
        trend = mean if trend is None else trend + (1 - (1 - TREND_PER_DAY) ** (day - before).days) * (mean - trend)
        found[day], before = trend, day
    return found


def slope(xs: list[float], ys: list[float]) -> tuple[float, float]:
    """The least-squares slope of y on x and the half width of its 95 % interval; at least 3 points with 2 or more x."""
    n = len(xs)
    mean_x, mean_y = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mean_x) ** 2 for x in xs)
    found = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True)) / sxx
    residual = sum((y - mean_y - found * (x - mean_x)) ** 2 for x, y in zip(xs, ys, strict=True))
    return found, t95(n - 2) * math.sqrt(residual / (n - 2) / sxx)


def rate(day: date) -> dict | None:
    """The rate of the weight over the RATE_DAYS days up to `day`: kg a week, its 95 % interval (low, high), the number of
    weigh-in days, and its status ('falling', 'rising' or 'steady'); None with fewer than RATE_MIN_DAYS weigh-in days."""
    weights = _weights(day - timedelta(days=RATE_DAYS - 1), day)
    n = len(weights)
    if n < RATE_MIN_DAYS:
        return None
    per_day, margin = slope([(d - day).days for d in weights], list(weights.values()))
    low, high = round((per_day - margin) * 7, 2), round((per_day + margin) * 7, 2)
    status = 'falling' if high < 0 else 'rising' if low > 0 else 'steady'
    return {'kg_week': round(per_day * 7, 2), 'low': low, 'high': high, 'days': n, 'status': status}


def _pressure(since: date, last: date) -> list[tuple[date, int, float, float]]:
    """The blood pressure readings from day `since` to `last`: day, hour, systolic and diastolic, oldest first."""
    groups: dict[int, dict] = {}
    for grpid, kind, value, at in _rows((SYSTOLIC, DIASTOLIC), since):
        groups.setdefault(grpid, {'at': at})[kind] = value
    return [(store.health_day(g['at']), g['at'].hour, g[SYSTOLIC], g[DIASTOLIC]) for g in groups.values()
            if SYSTOLIC in g and DIASTOLIC in g and store.health_day(g['at']) <= last]


def _mean(readings: list[tuple[date, int, float, float]]) -> dict:
    """The mean systolic and diastolic of readings, to 0.1, with their number."""
    n = len(readings)
    return {'systolic': round(sum(r[2] for r in readings) / n, 1), 'diastolic': round(sum(r[3] for r in readings) / n, 1),
            'readings': n}


def _esh(day: date, readings: list[tuple[date, int, float, float]]) -> dict | None:
    """The home blood pressure of `day` from these readings (see the module); None with fewer than ESH_MIN_READINGS."""
    since = day - timedelta(days=ESH_DAYS - 2)
    found = [r for r in readings if since <= r[0] <= day]
    if len(found) < ESH_MIN_READINGS:
        return None
    morning = [r for r in found if r[1] < MORNING_END_HOUR]
    evening = [r for r in found if r[1] >= MORNING_END_HOUR]
    return _mean(found) | {'morning': _mean(morning) if morning else None, 'evening': _mean(evening) if evening else None}


def home_pressure(day: date) -> dict | None:
    """The home blood pressure of the ESH method on `day`: the mean systolic and diastolic, the number of readings, and the
    same for the morning and the evening (None for a part without a reading); None with too few readings."""
    return _esh(day, _pressure(day - timedelta(days=ESH_DAYS), day))


def _heart_readings(since: date, last: date) -> dict[str, list[tuple[date, float]]]:
    """The readings of the heart and vessel values with a 7-day mean from day `since` to `last`, by name (ROLLING_NAMES):
    day and value. The pulse of a blood pressure reading is the monitor's; any other pulse is the heart rate of the scale."""
    groups: dict[int, dict] = {}
    for grpid, kind, value, at in _rows(ROLLING_KINDS, since):
        groups.setdefault(grpid, {'at': at, 'values': {}})['values'][kind] = value
    found: dict[str, list[tuple[date, float]]] = {name: [] for name in ROLLING_NAMES}
    for group in groups.values():
        day = store.health_day(group['at'])
        if day > last:
            continue
        for kind, value in group['values'].items():
            if kind == SYSTOLIC:
                continue
            name = withings.TYPES[kind] if kind != PULSE or SYSTOLIC in group['values'] else 'heart_rate'
            found[name].append((day, value))
    return found


def _rolling(day: date, readings: list[tuple[date, float]]) -> float | None:
    """The mean of the readings of the ROLLING_DAYS up to `day`, to 0.01; None with fewer than ROLLING_MIN_READINGS."""
    since = day - timedelta(days=ROLLING_DAYS - 1)
    values = [value for when, value in readings if since <= when <= day]
    return round(sum(values) / len(values), 2) if len(values) >= ROLLING_MIN_READINGS else None


def day_values(first: str, last: str) -> dict[str, dict[str, float]]:
    """The derived values of each day from `first` to `last` for the history: the trend of each body composition value
    on each day it was measured (weight_trend, fat_ratio_trend, ...), systolic_7d and diastolic_7d (the home blood
    pressure) and the 7-day mean of the other heart and vessel values (pulse_7d, heart_rate_7d, ...) on each day with
    enough readings."""
    start, end = date.fromisoformat(first), date.fromisoformat(last)
    found: dict[str, dict[str, float]] = {}
    for name, means in _day_means(TREND_KINDS, start - timedelta(days=WARMUP_DAYS), end).items():
        for day, trend in _trend(means).items():
            if day >= start:
                found.setdefault(day.isoformat(), {})[f'{name}_trend'] = round(trend, 2)
    readings = _pressure(start - timedelta(days=ESH_DAYS), end)
    heart = _heart_readings(start - timedelta(days=ROLLING_DAYS), end)
    day = start
    while day <= end:
        if (home := _esh(day, readings)) is not None:
            found.setdefault(day.isoformat(), {}).update(systolic_7d=home['systolic'], diastolic_7d=home['diastolic'])
        for name, values in heart.items():
            if (mean := _rolling(day, values)) is not None:
                found.setdefault(day.isoformat(), {})[f'{name}_7d'] = mean
        day += timedelta(days=1)
    return found
