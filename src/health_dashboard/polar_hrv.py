"""The HRV tests of the Polar H10 sessions (the sessions with the HRV activity): their values from the stored beats, each
test as a point at its time for the charts, the mean of the tests of each day, and the normal range and status of ln RMSSD
after Plews: the 7-day mean against the mean of 60 days +/- 0.5 SD, with at least 14 tests, and the 7-day coefficient of
variation of ln RMSSD (Plews et al.: a second marker of how the body copes, from 3 tests); every test counts (the user,
2026-10-17: two tests on one day both show). Each value of the tests has a 7-day mean too."""

from datetime import date, timedelta

import numpy as np

from health_dashboard import polar_store, polar_values

MEAN_DAYS, RANGE_DAYS, RANGE_SD, RANGE_MIN_TESTS = 7, 60, 0.5, 14
CV_MIN_TESTS = 3
# the card shows a test of the page's day and of the 7 days before it
RECENT_DAYS = 7
# the history keys of the values of a test, with their decimals
DAY_KEYS = {'rest_hr': ('h10:rest_hr', 1), 'rmssd': ('h10:rmssd', 1), 'ln_rmssd': ('h10:ln_rmssd', 2), 'sdnn': ('h10:sdnn', 1),
            'pnn50': ('h10:pnn50', 1), 'lf': ('h10:lf', 0), 'hf': ('h10:hf', 0), 'lf_hf': ('h10:lf_hf', 2),
            'stress': ('h10:stress', 1)}
# the history keys of the 7-day mean of each value (the mean of the tests of the 7 days up to a day), with their decimals
MEAN_KEYS = {field: (f'{key}_7d', decimals) for field, (key, decimals) in DAY_KEYS.items()}


def _beats(session_id: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The stored beats of a session: their times (ms), their RR (ms) and whether each is clean."""
    rows = polar_store.beats(session_id)
    return (np.array([r['t_ms'] for r in rows], dtype=np.int64), np.array([r['rr_ms'] for r in rows], dtype=float),
            np.array([bool(r['clean']) for r in rows], dtype=bool))


def _use_s(session_id: int) -> int:
    """The test time of an HRV test: the one chosen at its Start, else the 5 minutes of the tests from before that choice."""
    return polar_store.measure_s(session_id) or polar_values.HRV_USE_S


def values_of(session_id: int) -> dict:
    """The values of one HRV test (polar_values.hrv_values) from its stored beats and its test time."""
    return polar_values.hrv_values(*_beats(session_id), _use_s(session_id))


def detail_of(session_id: int, start_s: int) -> dict:
    """The values of one HRV test with the beats of its window for a chart: seconds from `start_s` (to 0.01), RR (ms) and
    clean (1 or 0)."""
    at_ms, rr, clean = _beats(session_id)
    found = polar_values.hrv_values(at_ms, rr, clean, _use_s(session_id))
    if found['from_ms'] is None:
        return found | {'beats': []}
    inside = (at_ms >= found['from_ms']) & (at_ms < found['to_ms'])
    return found | {'beats': [[round(int(t) / 1000 - start_s, 2), float(v), int(c)]
                              for t, v, c in zip(at_ms[inside], rr[inside], clean[inside], strict=True)]}


def tests(first: str, last: str) -> list[dict]:
    """Every HRV test with values from day `first` to day `last`, in time order: its values with its session_id, day and
    start_at."""
    found = []
    for session in polar_store.sessions_of_test(polar_store.HRV_TEST, first, last):
        if (values := values_of(session['id']))['reason'] is None:
            found.append(values | {'session_id': session['id'], 'day': session['day'], 'start_at': session['start_at']})
    return found


def points(first: str, last: str) -> list[dict]:
    """Each HRV test with values from day `first` to day `last` for the charts, at its time: at (its start), day and its
    values under their history keys."""
    return [{'at': t['start_at'], 'day': t['day'], **{key: t[field] for field, (key, _) in DAY_KEYS.items() if t[field] is not None}}
            for t in tests(first, last)]


def status(day: str, found: list[dict]) -> dict:
    """The 7-day mean of ln RMSSD of the tests `found` up to `day` (None without a test in those days), its coefficient of
    variation in % (None with fewer than 3 tests), the normal range of the tests of the 60 days up to it (their mean +/- 0.5
    SD; None with fewer than 14 tests, and then `missing` says how many more are needed) and the status: 'below', 'within'
    or 'above' (None without a range or a 7-day mean)."""
    end = date.fromisoformat(day)

    def last_days(count: int) -> list[float]:
        """The ln RMSSD of the tests of the `count` days up to `day`."""
        since = (end - timedelta(days=count - 1)).isoformat()
        return [t['ln_rmssd'] for t in found if since <= t['day'] <= day and t['ln_rmssd'] is not None]

    week, period = last_days(MEAN_DAYS), last_days(RANGE_DAYS)
    mean_7d = round(float(np.mean(week)), 2) if week else None
    cv_7d = round(float(np.std(week, ddof=1)) / float(np.mean(week)) * 100, 1) if len(week) >= CV_MIN_TESTS else None
    if len(period) < RANGE_MIN_TESTS:
        return {'mean_7d': mean_7d, 'cv_7d': cv_7d, 'low': None, 'high': None, 'status': None,
                'missing': RANGE_MIN_TESTS - len(period)}
    centre, spread = float(np.mean(period)), float(np.std(period, ddof=1))
    low, high = round(centre - RANGE_SD * spread, 2), round(centre + RANGE_SD * spread, 2)
    state = None if mean_7d is None else 'below' if mean_7d < low else 'above' if mean_7d > high else 'within'
    return {'mean_7d': mean_7d, 'cv_7d': cv_7d, 'low': low, 'high': high, 'status': state, 'missing': 0}


def day_values(first: str, last: str) -> dict[str, dict[str, float]]:
    """The HRV values of each day from `first` to `last` for the history: the mean of the tests of the day (DAY_KEYS), the
    mean of the tests of the 7 days up to the day (MEAN_KEYS), from 3 tests h10:ln_rmssd_cv7, and with a normal range
    h10:ln_rmssd_low and h10:ln_rmssd_high."""
    end = date.fromisoformat(last)
    found_tests = tests((date.fromisoformat(first) - timedelta(days=RANGE_DAYS - 1)).isoformat(), last)
    by_day: dict[str, list[dict]] = {}
    for test in found_tests:
        by_day.setdefault(test['day'], []).append(test)
    found: dict[str, dict[str, float]] = {}
    day = date.fromisoformat(first)
    while day <= end:
        key = day.isoformat()
        values = polar_values.means_of(by_day.get(key, []), DAY_KEYS)
        since = (day - timedelta(days=MEAN_DAYS - 1)).isoformat()
        values |= polar_values.means_of([t for t in found_tests if since <= t['day'] <= key], MEAN_KEYS)
        band = status(key, found_tests)
        if band['cv_7d'] is not None:
            values['h10:ln_rmssd_cv7'] = band['cv_7d']
        if band['low'] is not None:
            values |= {'h10:ln_rmssd_low': band['low'], 'h10:ln_rmssd_high': band['high']}
        if values:
            found[key] = values
        day += timedelta(days=1)
    return found


def last_test(day: str) -> dict | None:
    """For the card of `day`: the newest HRV test of that day and the RECENT_DAYS before it (its values, or the reason it has
    none) with the status of `day`; None without one."""
    end = date.fromisoformat(day)
    recent = polar_store.sessions_of_test(polar_store.HRV_TEST, (end - timedelta(days=RECENT_DAYS)).isoformat(), day)
    if not recent:
        return None
    newest = recent[-1]
    found = tests((end - timedelta(days=RANGE_DAYS - 1)).isoformat(), day)
    return values_of(newest['id']) | {'session_id': newest['id'], 'start_at': newest['start_at'], 'status': status(day, found)}
