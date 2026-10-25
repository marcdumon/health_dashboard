"""The detail of one Polar H10 session for its popup: the HR of each second, the 5 zones from the user's birth year (the
birth date in person.toml), a comparison with the earlier sessions of the same activity, the net energy expenditure, for a
session with the HRV activity the HRV test (its values and the beats of its window), and for one with the HR recovery
activity or the stairs test activity its values. No split in movement and still (removed on 2026-10-21)."""

from datetime import date, timedelta

import numpy as np

from health_dashboard import (
    person,
    polar_energy,
    polar_hrv,
    polar_load,
    polar_recovery,
    polar_stairs,
    polar_store,
    polar_values,
)

# the comparison: the mean of at most this many earlier sessions of the same activity, and the minutes from zone 3 on
COMPARE_COUNT, HIGH_ZONE = 10, 3


def birth_year() -> int | None:
    """The user's birth year for the zones, from the birth date in person.toml; None when not set."""
    return person.birth_year()


def _arrays(session_id: int) -> tuple[int, np.ndarray]:
    """The first second (epoch s) of a session and its HR of each second (NaN without one)."""
    rows = polar_store.seconds(session_id)
    hr = np.array([np.nan if r['hr'] is None else r['hr'] for r in rows], dtype=float)
    return (rows[0]['t'] if rows else 0), hr


def _limits(found: dict, born: int | None) -> tuple[int, list[int]] | None:
    """The maximum HR and the zone limits in the year of a session; None without a birth year."""
    return None if born is None else polar_values.zone_limits(born, date.fromisoformat(found['day']).year)


def _values(found: dict, hr: np.ndarray, born: int | None) -> dict:
    """The four values of a session that the comparison uses."""
    zones = _limits(found, born)
    high = None if zones is None else round(sum(polar_values.zone_seconds(hr, zones[1])[HIGH_ZONE:]) / 60, 1)
    return {'seconds': found['seconds'], 'mean_hr': found['mean_hr'], 'max_hr': found['max_hr'], 'high_zone_min': high}


def _mean(values: list[float | None]) -> float | None:
    """The mean of the values that are there, to 0.1; None without one."""
    found = [v for v in values if v is not None]
    return round(sum(found) / len(found), 1) if found else None


def _compare(found: dict, hr: np.ndarray, born: int | None) -> dict | None:
    """This session's four values against the mean of the last COMPARE_COUNT earlier sessions of its activity; None
    without an activity or without an earlier session."""
    if found['activity_id'] is None:
        return None
    others = polar_store.earlier(found, COMPARE_COUNT)
    if not others:
        return None
    usual = [_values(other, _arrays(other['id'])[1], born) for other in others]
    return {'count': len(others), 'this': _values(found, hr, born),
            'usual': {key: _mean([u[key] for u in usual]) for key in usual[0]}}


def zone_weeks(first: date, last: date) -> dict | None:
    """The minutes in each of the 5 zones (from the birth year and the year of each session) per week (its Monday; every
    week of the period, oldest first) of the sessions that count and are not HRV tests (a rest), with the zone limits of
    the year of `last`; None without a birth year. Every activity counts: the minutes add up, whatever the activity."""
    born = birth_year()
    if born is None:
        return None
    found: dict[str, list[float]] = {}
    monday = first - timedelta(days=first.weekday())
    while monday <= last:
        found[monday.isoformat()] = [0.0] * len(polar_values.ZONE_PERCENTS)
        monday += timedelta(days=7)
    for session in polar_store.exercise_sessions(first.isoformat(), last.isoformat()):
        day = date.fromisoformat(session['day'])
        seconds = polar_values.zone_seconds(_arrays(session['id'])[1], polar_values.zone_limits(born, day.year)[1])
        week = found[(day - timedelta(days=day.weekday())).isoformat()]
        for zone in range(len(week)):
            week[zone] += seconds[zone + 1]
    return {'limits': polar_values.zone_limits(born, last.year)[1],
            'weeks': [{'week': week, 'minutes': [round(s / 60) for s in zones]} for week, zones in found.items()]}


def detail(session_id: int) -> dict | None:
    """Everything the popup of a session shows; None when there is no such session."""
    found = polar_store.session(session_id)
    if found is None:
        return None
    start, hr = _arrays(session_id)
    born = birth_year()
    zones = _limits(found, born)
    return {'session': found, 'start': start, 'hr': [None if np.isnan(v) else round(float(v), 1) for v in hr],
            'birth_year': born,
            'zones': None if zones is None else {'max_hr': zones[0], 'limits': zones[1],
                                                 'seconds': polar_values.zone_seconds(hr, zones[1])},
            'compare': _compare(found, hr, born),
            'energy': polar_energy.energy([found])[session_id], 'energy_missing': polar_energy.missing(),
            'energy_note': polar_energy.factor()[1],
            'trimp': None if zones is None else polar_load.trimp(polar_values.zone_seconds(hr, zones[1])),
            'hrv': polar_hrv.detail_of(session_id, start) if found['activity_test'] == polar_store.HRV_TEST else None,
            'recovery': polar_recovery.values_of(session_id) if found['activity_test'] == polar_store.RECOVERY_TEST else None,
            'stairs': polar_stairs.values_of(session_id) if found['activity_test'] == polar_store.STAIRS_TEST else None}
