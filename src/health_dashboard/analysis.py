"""The analysis of one value of the Health history, for the large view of a chart: the day values with a rolling mean and a
band of one standard deviation, each recording of the value at its time, the numbers of the period, a histogram, the days
far from the days around them (a wrong measurement, to remove), the change from one day to the next, the last week against
the same week 7, 30, 90 and 365 days earlier (widened when it has no value), each year by month, and the relation with a
second value. The numbers use the day averages of the history; a removed measurement is not in it."""

import math
import statistics
from bisect import bisect_left, bisect_right
from datetime import date, datetime, timedelta
from itertools import pairwise

from health_dashboard import (
    fitbit,
    food,
    history,
    ledger,
    polar_hrv,
    polar_load,
    polar_store,
    sleep,
    store,
    supplements,
    trends,
    withings,
)

# the user's own scores: never a wrong measurement, so never an outlier; nor is an amount of a supplement taken or a food value
DAILY = ('angina', 'ketones')
# values calculated from others (means, trends, loads): never a wrong measurement either
DERIVED = (*trends.KEYS, *polar_load.KEYS, *(key for key, _ in polar_hrv.MEAN_KEYS.values()), 'h10:ln_rmssd_cv7')
DAYS_PER_MONTH = 30.4375
# a robust z of 3.5 against the median and the median absolute deviation of the days around (Iglewicz and Hoaglin)
OUTLIER_Z = 3.5
MAD_TO_STD = 1.4826
MIN_AROUND = 4
MAX_OUTLIERS = 50
# the last week with a value against the same week this many days earlier; these reach back past a short period
WEEK = 7
EARLIER = (7, 30, 90, 365)
# an earlier week without a value is widened by a day on each side until it has one, up to this many days on each side
WIDEN = 14
# how far back the earlier weeks reach
REACH = max(EARLIER) + WEEK + WIDEN
# the longest delay the relation checks, in days
MAX_LAG = 30


def known(key: str) -> bool:
    """Whether a value of the history has this name: a Withings value (of a body part too), the angina score, the highest
    Ketostix reading, the amount of a supplement taken, a site of the tape measure, a value of the Polar H10 sessions, a
    sleep value of a day, a food value of a day, a Fitbit value of a day or a derived value."""
    name, _, part = key.partition(':')
    if name == 'tape':
        return part in dict(store.TAPE_SITES)
    if name == 'supp':
        return any(k['key'] == key for k in supplements.value_keys())
    names = set(withings.TYPES.values()) | {'heart_rate', 'muscle_ratio'}
    return (key in DAILY or key in DERIVED or key in polar_store.KEYS or key in sleep.KEYS or key in food.KEYS or key in fitbit.KEYS
            or (name in names and (not part or part in withings.PARTS.values())))


def _first_day(today: date) -> date:
    """The first day with data of any kind: the first measurement, entry of a supplement, angina score, Ketostix reading or
    tape measurement, food note, Polar H10 session, sleep or Fitbit day."""
    with ledger.connect() as db:
        measured = db.execute('SELECT MIN(measured_at) FROM withings_values').fetchone()[0]
        days = [db.execute(f'SELECT MIN(day) FROM {table}').fetchone()[0]
                for table in ('health_units', 'health_angina', 'health_ketones', 'health_tape', 'health_food', 'polar_sessions',
                              'sleep_records', 'fitbit_days')]
    found = [date.fromisoformat(d) for d in days if d] + ([store.health_day(datetime.fromisoformat(measured))] if measured else [])
    return min([*found, today])


def _value(row: dict, key: str) -> float | None:
    """The value of a day of that name."""
    value = row.get(key)
    return float(value) if isinstance(value, int | float) else None


def _around(ordinals: list[int], at: int, half: int) -> tuple[int, int]:
    """The index range of the values from `half` days before to `half` days after a day."""
    return bisect_left(ordinals, at - half), bisect_right(ordinals, at + half)


def _rolling(days: list[date], values: list[float], half: int) -> list[dict]:
    """Each day value with the mean and the standard deviation of the values of the days around it, its own included."""
    ordinals = [d.toordinal() for d in days]
    points = []
    for day, value, at in zip(days, values, ordinals, strict=True):
        lo, hi = _around(ordinals, at, half)
        near = values[lo:hi]
        points.append({'day': day.isoformat(), 'value': round(value, 4), 'mean': round(statistics.fmean(near), 4),
                       'std': round(statistics.stdev(near), 4) if len(near) > 1 else None})
    return points


def _outliers(days: list[date], values: list[float], half: int) -> list[dict]:
    """The days far from the days around them, the farthest first: against the median of the others, so one wrong value does
    not hide a second one near it."""
    ordinals = [d.toordinal() for d in days]
    found = []
    for i, (day, value, at) in enumerate(zip(days, values, ordinals, strict=True)):
        lo, hi = _around(ordinals, at, half)
        others = values[lo:i] + values[i + 1:hi]
        if len(others) < MIN_AROUND:
            continue
        middle = statistics.median(others)
        spread = MAD_TO_STD * statistics.median(abs(v - middle) for v in others) or statistics.stdev(others)
        if spread and abs(z := (value - middle) / spread) > OUTLIER_Z:
            found.append({'day': day.isoformat(), 'value': round(value, 4), 'expected': round(middle, 4), 'z': round(z, 1)})
    return sorted(found, key=lambda o: -abs(o['z']))[:MAX_OUTLIERS]


def _nice(step: float) -> float:
    """A round bin width at least as large as the step: 1, 2, 2.5 or 5 times a power of ten."""
    power = 10 ** math.floor(math.log10(step))
    return next(m * power for m in (1, 2, 2.5, 5, 10) if m * power >= step * (1 - 1e-9))


def _histogram(values: list[float]) -> dict:
    """The values in bins of a round width; all values the same make one bin."""
    lo, hi = min(values), max(values)
    if lo == hi:
        return {'edges': [lo, hi], 'counts': [len(values)]}
    width = _nice((hi - lo) / min(30, max(5, round(math.sqrt(len(values))))))
    start = math.floor(round(lo / width, 9)) * width
    bins = math.floor(round((hi - start) / width, 9)) + 1
    counts = [0] * bins
    for v in values:
        counts[min(bins - 1, math.floor(round((v - start) / width, 9)))] += 1
    return {'edges': [round(start + n * width, 9) for n in range(bins + 1)], 'counts': counts}


def _stats(days: list[date], values: list[float]) -> dict:
    """The numbers of the period; the trend is the slope of a straight line through the day values, per month."""
    low, high = values.index(min(values)), values.index(max(values))
    trend = None
    if len(set(days)) > 1:
        slope = statistics.linear_regression([d.toordinal() for d in days], values).slope
        trend = round(slope * DAYS_PER_MONTH, 4)
    return {'count': len(values), 'mean': round(statistics.fmean(values), 4), 'median': round(statistics.median(values), 4),
            'std': round(statistics.stdev(values), 4) if len(values) > 1 else None,
            'min': {'day': days[low].isoformat(), 'value': round(values[low], 4)},
            'max': {'day': days[high].isoformat(), 'value': round(values[high], 4)}, 'trend_per_month': trend}


def _steps(days: list[date], values: list[float]) -> dict | None:
    """The change from one day to the next, only between days that follow each other: how many, the typical size (median)
    and the size 9 of 10 changes stay under; a smaller change is the normal spread, not a real change."""
    changes = [b - a for (d0, a), (d1, b) in pairwise(zip(days, values, strict=True)) if (d1 - d0).days == 1]
    if not changes:
        return None
    sizes = sorted(abs(c) for c in changes)
    return {'count': len(changes), 'median_size': round(statistics.median(sizes), 4),
            'p90_size': round(sizes[math.ceil(0.9 * len(sizes)) - 1], 4), 'histogram': _histogram(changes)}


def _earlier(found: list[tuple[date, float]]) -> list[dict]:
    """The mean of the last 7 days up to the last value, and of the same 7 days 7, 30, 90 and 365 days earlier, with how
    much the last week is above (+) or below (-) each. An earlier week without a value is widened (WIDEN), never into the
    last week, so that a value measured now and then still has a difference; its from and to say which days it took."""
    if not found:
        return []
    end = found[-1][0]

    def week(ago: int) -> dict:
        to = end - timedelta(days=ago)
        start = to - timedelta(days=WEEK - 1)
        for wider in range(WIDEN + 1):
            first, last = start - timedelta(days=wider), to + timedelta(days=min(wider, max(ago - WEEK, 0)))
            if values := [v for d, v in found if first <= d <= last]:
                break
        return {'ago': ago, 'from': first.isoformat(), 'to': last.isoformat(), 'count': len(values),
                'mean': round(statistics.fmean(values), 4) if values else None, 'diff': None}

    now = week(0)
    then = [week(ago) for ago in EARLIER]
    return [now] + [w | {'diff': round(now['mean'] - w['mean'], 4)} if w['mean'] is not None else w for w in then]


def _years(days: list[date], values: list[float]) -> list[dict]:
    """Each year with data: the mean of each month, None for a month without a value."""
    months: dict[int, list[list[float]]] = {}
    for day, value in zip(days, values, strict=True):
        months.setdefault(day.year, [[] for _ in range(12)])[day.month - 1].append(value)
    return [{'year': year, 'months': [round(statistics.fmean(m), 4) if m else None for m in by_month]}
            for year, by_month in sorted(months.items())]


def _rows(days: int | None, today: date, at_least: int = 0) -> tuple[date, list[dict]]:
    """The first day of the period (None: all data) and the history rows of at least `at_least` days up to today."""
    first = today - timedelta(days=days - 1) if days else _first_day(today)
    return first, history.days(max((today - first).days + 1, at_least), today)


def analyse(key: str, days: int | None, window: int, today: date, activity: int | None = None) -> dict:
    """The analysis of a value over the last `days` days (None: all data), with a rolling window of `window` days centred
    on each day; the earlier weeks reach back a year, also for a shorter period. The readings are the recordings of the
    value at their time (history.chart_points), for the chart; a value of the day (angina, a supplement, the tape) has none.
    The numbers use the day values (`per` 'day'), but a value of the Polar H10 sessions has none: its numbers use each
    session (`per` 'session'; with `activity`, only the sessions of that activity), without the change from day to day."""
    first, rows = _rows(days, today, REACH)
    per_session = key in polar_store.SESSION_KEYS
    if per_session:
        since = min(first, today - timedelta(days=REACH - 1)).isoformat()
        every = [(date.fromisoformat(p['day']), float(p[key])) for p in polar_store.session_points(since, today.isoformat(), activity)
                 if key in p]
    else:
        every = [(date.fromisoformat(row['day']), v) for row in rows if (v := _value(row, key)) is not None]
    found = [(d, v) for d, v in every if d >= first]
    readings = [{'at': p['at'], 'day': p['day'], 'value': p[key]} for p in history.chart_points(first, today, activity) if key in p]
    result = {'key': key, 'start': first.isoformat(), 'end': today.isoformat(), 'window': window, 'points': [], 'stats': None,
              'histogram': None, 'outliers': [], 'steps': None, 'earlier': _earlier(every), 'years': [], 'readings': readings,
              'per': 'session' if per_session else 'day'}
    if not found:
        return result
    day_list, values = [d for d, _ in found], [v for _, v in found]
    half = int(window / 2)
    return result | {
        'points': _rolling(day_list, values, half), 'stats': _stats(day_list, values), 'histogram': _histogram(values),
        # the user's own scores, the supplements, the sleep and the Fitbit values are never a measurement the user can remove
        'outliers': [] if key in DAILY or key in DERIVED or key in sleep.KEYS or key in food.KEYS or key in fitbit.KEYS
        or key.startswith('supp:') else _outliers(day_list, values, half),
        'steps': None if per_session else _steps(day_list, values),
        'years': _years(day_list, values)}


def _correlate(pairs: list[tuple[float, float]]) -> tuple[float | None, float | None, float | None]:
    """r, slope and intercept of (x, y) pairs; None without 3 pairs and some spread on both sides."""
    xs, ys = [x for x, _ in pairs], [y for _, y in pairs]
    if len(pairs) < 3 or len(set(xs)) < 2 or len(set(ys)) < 2:
        return None, None, None
    line = statistics.linear_regression(xs, ys)
    return statistics.correlation(xs, ys), line.slope, line.intercept


def relation(key: str, other: str, days: int | None, today: date, lag: int = 0) -> dict:
    """The days of the period with both values: `other` (x) on day D - lag against `key` (y) on day D, with the correlation
    and a straight line through them; and r and n for every delay from 0 to MAX_LAG days, to find a delayed effect. The
    earlier value may lie before the period. A value of the Polar H10 sessions has no value of a day: ValueError."""
    for name in (key, other):
        if name in polar_store.SESSION_KEYS:
            raise ValueError('A value of the Polar H10 sessions has no value of a day, so it has no correlation by day. Compare '
                             'it session by session in its own chart.')
    first = today - timedelta(days=days - 1) if days else _first_day(today)
    rows = history.days((today - first).days + 1 + MAX_LAG, today)
    by_lag = []
    for delay in range(MAX_LAG + 1):
        found = _pairs(rows, first, key, other, delay)
        r = _correlate([(x, y) for _, _, x, y in found])[0]
        by_lag.append({'lag': delay, 'r': None if r is None else round(r, 4), 'n': len(found)})
    return _related(key, other, lag, _pairs(rows, first, key, other, lag)) | {'by_lag': by_lag}


def _pairs(rows: list[dict], first: date, key: str, other: str, delay: int) -> list[tuple[date, date, float, float]]:
    """The days from `first` on with a value of `key` (y) and a value of `other` `delay` days earlier (x): day of y, day of
    x, x and y."""
    ys = {d: v for row in rows if (d := date.fromisoformat(row['day'])) >= first and (v := _value(row, key)) is not None}
    xs = {date.fromisoformat(row['day']): v for row in rows if (v := _value(row, other)) is not None}
    return [(d, d - timedelta(days=delay), xs[d - timedelta(days=delay)], y) for d, y in sorted(ys.items())
            if d - timedelta(days=delay) in xs]


def _related(key: str, other: str, lag: int, pairs: list[tuple[date, date, float, float]]) -> dict:
    """The points of two values by day with their correlation and the straight line through them."""
    r, slope, intercept = _correlate([(x, y) for _, _, x, y in pairs])
    points = [{'day': d.isoformat(), 'x_day': x_day.isoformat(), 'x': round(x, 4), 'y': round(y, 4)} for d, x_day, x, y in pairs]
    return {'key': key, 'other': other, 'lag': lag, 'points': points, 'n': len(points),
            'r': None if r is None else round(r, 4), 'slope': None if slope is None else round(slope, 6),
            'intercept': None if intercept is None else round(intercept, 6)}


def supplement_relations(key: str, days: int | None, today: date, lag: int = 1) -> list[dict]:
    """Each supplement taken in the period against a value of the history, by day: the amount taken `lag` days earlier (x)
    against the value (y), with the correlation and a straight line, as relation gives them, and the supplement's name and
    dose unit; in the order of the list. A supplement not taken in the period has none. A value of the Polar H10 sessions
    has no value of a day: ValueError."""
    if key in polar_store.SESSION_KEYS:
        raise ValueError('A value of the Polar H10 sessions has no value of a day, so it has no correlation by day.')
    first = today - timedelta(days=days - 1) if days else _first_day(today)
    rows = history.days((today - first).days + 1 + lag, today)
    found = []
    for supplement in supplements.value_keys():
        if not any(row.get(supplement['key']) for row in rows if row['day'] >= (first - timedelta(days=lag)).isoformat()):
            continue
        found.append(_related(key, supplement['key'], lag, _pairs(rows, first, key, supplement['key'], lag))
                     | {'name': supplement['name'], 'unit': supplement['unit']})
    return found

