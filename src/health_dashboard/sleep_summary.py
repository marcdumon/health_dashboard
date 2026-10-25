"""The sleep pattern of a period, from the main sleep of each day: its longest sleep, so a nap does not count (the user often
sleeps in two parts). The regularity: the standard deviation of the bedtime (counted from noon, so a bedtime after midnight
follows one before it) and of the wake time. The weekdays against the weekend, by the wake day (Saturday and Sunday: the
nights of Friday and Saturday): the mean sleep, bedtime, wake time and mid-sleep, and the social jetlag (the mid-sleep of
the weekend minus that of the weekdays). The main sleep against the ln RMSSD of the HRV test of the same morning (a sleep's
day is its wake day): Pearson r and the slope, from 10 mornings."""

from datetime import date, datetime

import numpy as np

from health_dashboard import ledger, polar_hrv

DAY_MIN, NOON_MIN = 1440, 720
MIN_NIGHTS, MIN_PAIRS = 3, 10


def _clock(minutes: float) -> str:
    """Minutes from midnight (any number) as a clock time HH:MM."""
    whole = round(minutes) % DAY_MIN
    return f'{whole // 60:02d}:{whole % 60:02d}'


def _main_sleeps(first: str, last: str) -> list[dict]:
    """The longest sleep of each day from `first` to `last`, oldest first: day, sleep minutes, bedtime (minutes from noon),
    wake time (minutes from midnight) and mid-sleep (minutes from noon), from the local clock of each sleep."""
    with ledger.connect() as db:
        rows = db.execute('SELECT day, start_at, end_at, sleep_min FROM sleep_records WHERE day >= ? AND day <= ? '
                          'ORDER BY day, sleep_min DESC', (first, last)).fetchall()
    found: dict[str, dict] = {}
    for day, start_at, end_at, minutes in rows:
        if day in found:
            continue
        start, end = datetime.fromisoformat(start_at), datetime.fromisoformat(end_at)
        bedtime = (start.hour * 60 + start.minute - NOON_MIN) % DAY_MIN
        found[day] = {'day': day, 'sleep_min': minutes, 'bedtime': bedtime, 'wake': end.hour * 60 + end.minute,
                      'mid': bedtime + (end - start).total_seconds() / 120}
    return list(found.values())


def _group(sleeps: list[dict]) -> dict | None:
    """The number of nights and the mean sleep, bedtime, wake time and mid-sleep of some main sleeps; None without one."""
    if not sleeps:
        return None
    mean = {key: float(np.mean([s[key] for s in sleeps])) for key in ('sleep_min', 'bedtime', 'wake', 'mid')}
    return {'nights': len(sleeps), 'sleep_min': round(mean['sleep_min']), 'bedtime': _clock(mean['bedtime'] + NOON_MIN),
            'wake': _clock(mean['wake']), 'mid': _clock(mean['mid'] + NOON_MIN), '_mid': mean['mid']}


def _relation(pairs: list[tuple[float, float]]) -> dict:
    """Pearson r of (hours of sleep, ln RMSSD) and the slope (ln RMSSD per hour of sleep), from MIN_PAIRS pairs."""
    if len(pairs) < MIN_PAIRS:
        return {'pairs': len(pairs), 'r': None, 'slope': None}
    hours, ln = np.array(pairs).T
    if np.std(hours) == 0 or np.std(ln) == 0:
        return {'pairs': len(pairs), 'r': None, 'slope': None}
    return {'pairs': len(pairs), 'r': round(float(np.corrcoef(hours, ln)[0, 1]), 2),
            'slope': round(float(np.polyfit(hours, ln, 1)[0]), 2)}


def summary(first: str, last: str) -> dict | None:
    """The sleep pattern of the main sleeps that end from `first` to `last` (see the module); None without a sleep. The
    standard deviations need 3 nights, the social jetlag a weekday and a weekend night."""
    sleeps = _main_sleeps(first, last)
    if not sleeps:
        return None
    many = len(sleeps) >= MIN_NIGHTS
    weekday = _group([s for s in sleeps if date.fromisoformat(s['day']).weekday() < 5])
    weekend = _group([s for s in sleeps if date.fromisoformat(s['day']).weekday() >= 5])
    jetlag = round(weekend['_mid'] - weekday['_mid']) if weekday and weekend else None
    tests: dict[str, list[float]] = {}
    for test in polar_hrv.tests(first, last):
        if test['ln_rmssd'] is not None:
            tests.setdefault(test['day'], []).append(test['ln_rmssd'])
    pairs = [(s['sleep_min'] / 60, float(np.mean(tests[s['day']]))) for s in sleeps if s['day'] in tests]
    return {'nights': len(sleeps),
            'bedtime_sd': round(float(np.std([s['bedtime'] for s in sleeps], ddof=1))) if many else None,
            'wake_sd': round(float(np.std([s['wake'] for s in sleeps], ddof=1))) if many else None,
            'weekday': None if weekday is None else {k: v for k, v in weekday.items() if k != '_mid'},
            'weekend': None if weekend is None else {k: v for k, v in weekend.items() if k != '_mid'},
            'jetlag_min': jetlag, 'hrv': _relation(pairs)}
