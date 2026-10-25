"""The HR recovery tests of the Polar H10 sessions (the sessions with the HR recovery activity): their values from the
stored seconds and the last Effort done marker or press of the phone marker (polar_values.recovery_values), each test as a
point at its time for the charts, the mean of the tests of each day for the history, and the last test for the card. No
normal range: the values are a personal trend."""

from datetime import date, timedelta

import numpy as np

from health_dashboard import polar_store, polar_values

# the card shows a test of the page's day and of the 30 days before it
RECENT_DAYS = 30
# the history keys of the values of a test, with their decimals
DAY_KEYS = {'hr_stop': ('h10:hr_stop', 1), 'drop_60': ('h10:drop_60', 1), 'drop_120': ('h10:drop_120', 1)}


def values_of(session_id: int) -> dict:
    """The values of one HR recovery test, or the reason it has none, from its stored seconds and its last Effort done
    marker or press of the phone marker (a phone recording has no Effort done button)."""
    rows = polar_store.seconds(session_id)
    hr = np.array([np.nan if r['hr'] is None else r['hr'] for r in rows], dtype=float)
    movement = np.array([np.nan if r['movement'] is None else r['movement'] for r in rows], dtype=float)
    marks = polar_store.marker_times(session_id, polar_values.EFFORT_DONE, polar_values.PHONE_START, polar_values.PHONE_STOP)
    marker_i = round(marks[-1] / 1000 - rows[0]['t']) if marks and rows else None
    return polar_values.recovery_values(hr, None if np.isnan(movement).all() else movement, marker_i)


def tests(first: str, last: str) -> list[dict]:
    """Every HR recovery test with values from day `first` to day `last`, in time order: its values with its session_id,
    day and start_at."""
    found = []
    for session in polar_store.sessions_of_test(polar_store.RECOVERY_TEST, first, last):
        if (values := values_of(session['id']))['reason'] is None:
            found.append(values | {'session_id': session['id'], 'day': session['day'], 'start_at': session['start_at']})
    return found


def points(first: str, last: str) -> list[dict]:
    """Each HR recovery test with values from day `first` to day `last` for the charts, at its time: at (its start), day
    and its values under their history keys."""
    return [{'at': t['start_at'], 'day': t['day'], **{key: t[field] for field, (key, _) in DAY_KEYS.items()}}
            for t in tests(first, last)]


def day_values(first: str, last: str) -> dict[str, dict[str, float]]:
    """The values of each day from `first` to `last` for the history: the mean of the HR recovery tests of the day with
    values (DAY_KEYS)."""
    by_day: dict[str, list[dict]] = {}
    for test in tests(first, last):
        by_day.setdefault(test['day'], []).append(test)
    return {day: polar_values.means_of(found, DAY_KEYS) for day, found in by_day.items()}


def last_test(day: str) -> dict | None:
    """For the card of `day`: the newest HR recovery test of that day and the RECENT_DAYS before it, with its values or the
    reason it has none; None without one."""
    end = date.fromisoformat(day)
    recent = polar_store.sessions_of_test(polar_store.RECOVERY_TEST, (end - timedelta(days=RECENT_DAYS)).isoformat(), day)
    if not recent:
        return None
    newest = recent[-1]
    return values_of(newest['id']) | {'session_id': newest['id'], 'start_at': newest['start_at']}
