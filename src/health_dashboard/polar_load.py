"""The training load of the Polar H10 sessions that count and are not HRV tests (a rest; the HR recovery and stairs tests
count), from their HR of each second and HRmax = 220 - age (age = the year minus the birth year, as the zones). Edwards
TRIMP of a session: the minutes in each zone (polar_values.zone_limits) x the zone number. Per day: the TRIMP of its
sessions, the acute load (the TRIMP of the 7 days up to the day), the chronic load (the TRIMP of the 28 days up to it / 4,
a week) and their ratio once 28 days have passed since the first session. The minutes of moderate (64 % <= HR < 77 %
HRmax) and vigorous (from 77 %) activity of a day, and per week (a week from Monday): the WHO minimum is 150 minutes of
moderate activity a week, a vigorous minute counting double. The day values are totals of a period, not values of a session
(the rule that the charts show each recording is about the values of a session). One SQL aggregate per year (the limits
depend on the age) for the sessions not in the memory yet: the values of a session do not change until it is read again
or the birth year changes."""

from datetime import date, timedelta

from health_dashboard import ledger, person, polar_store, polar_values

KEYS = ('load:trimp', 'load:acute', 'load:chronic', 'load:ratio', 'activity:moderate', 'activity:vigorous')
MODERATE, VIGOROUS = 0.64, 0.77
ACUTE_DAYS, CHRONIC_DAYS = 7, 28
# the WHO minimum of moderate activity a week (a vigorous minute counts double)
WHO_WEEK = 150
# the values of each session: (database, session, when it was read, birth year) -> trimp, moderate and vigorous minutes
_memory: dict[tuple[str, int, str, int], dict[str, float]] = {}


def missing() -> str:
    """What the training load needs and person.toml does not give, with what to do; '' when nothing is missing."""
    return '' if person.birth_year() is not None else f'The training load needs birth_date: set it {person.SET_IT}.'


def _sessions(first: date, last: date) -> list[dict]:
    """The sessions that count and are not HRV tests from `first` to `last`, each with its TRIMP and its minutes of moderate
    and vigorous activity; [] without a birth year."""
    born = person.birth_year()
    if born is None:
        return []
    sessions = polar_store.exercise_sessions(first.isoformat(), last.isoformat())
    key = {s['id']: (str(ledger.DB), s['id'], s['imported_at'], born) for s in sessions}
    by_year: dict[int, list[int]] = {}
    for session in sessions:
        if key[session['id']] not in _memory:
            by_year.setdefault(int(session['day'][:4]), []).append(session['id'])
    for year, ids in by_year.items():
        top, limits = polar_values.zone_limits(born, year)
        edges = [*limits, None]
        zones = ', '.join(f'SUM(hr >= {low}' + (f' AND hr < {high})' if high is not None else ')') for low, high in
                          zip(limits, edges[1:], strict=True))
        # a joined session counts the seconds of all its recordings
        owner = polar_store.owners(ids)
        with ledger.connect() as db:
            rows = db.execute(f"SELECT session_id, {zones}, SUM(hr >= ? AND hr < ?), SUM(hr >= ?) FROM polar_seconds WHERE "
                              f"hr IS NOT NULL AND session_id IN ({', '.join('?' * len(owner))}) GROUP BY session_id",
                              (MODERATE * top, VIGOROUS * top, VIGOROUS * top, *owner)).fetchall()
        totals: dict[int, list[int]] = {}
        for session_id, *seconds in rows:
            counts = [s or 0 for s in seconds]
            before = totals.get(owner[session_id], [0] * len(counts))
            totals[owner[session_id]] = [a + b for a, b in zip(before, counts, strict=True)]
        for session_id, seconds in totals.items():
            *zone, moderate, vigorous = seconds
            _memory[key[session_id]] = {'trimp': sum(s / 60 * (k + 1) for k, s in enumerate(zone)), 'moderate': moderate / 60,
                                        'vigorous': vigorous / 60}
    return [s | _memory[key[s['id']]] for s in sessions if key[s['id']] in _memory]


def _days(first: date, last: date) -> dict[date, dict[str, float]]:
    """The TRIMP and the minutes of moderate and vigorous activity of each day with a session from `first` to `last`."""
    found: dict[date, dict[str, float]] = {}
    for session in _sessions(first, last):
        day = found.setdefault(date.fromisoformat(session['day']), {'trimp': 0.0, 'moderate': 0.0, 'vigorous': 0.0})
        for name in day:
            day[name] += session[name]
    return found


def day_values(first: str, last: str) -> dict[str, dict[str, float]]:
    """The training load of each day from `first` to `last` for the history (see the module), from the day of the first
    session on: load:trimp, activity:moderate and activity:vigorous on a day with a session, load:acute and load:chronic on
    every day, load:ratio from 28 days after the first session (and only when the chronic load is not 0)."""
    start, end = date.fromisoformat(first), date.fromisoformat(last)
    days = _days(start - timedelta(days=CHRONIC_DAYS - 1), end)
    with ledger.connect() as db:
        oldest = db.execute(f'SELECT MIN(s.day) FROM polar_sessions s WHERE {polar_store.COUNTED} AND '
                            f'{polar_store.NOT_HRV}').fetchone()[0]
    if oldest is None or person.birth_year() is None:
        return {}
    found: dict[str, dict[str, float]] = {}
    day = max(start, date.fromisoformat(oldest))
    while day <= end:
        values = {}
        if day in days:
            values = {'load:trimp': round(days[day]['trimp'], 1), 'activity:moderate': round(days[day]['moderate'], 1),
                      'activity:vigorous': round(days[day]['vigorous'], 1)}
        acute = sum(v['trimp'] for d, v in days.items() if day - timedelta(days=ACUTE_DAYS) < d <= day)
        chronic = sum(v['trimp'] for d, v in days.items() if day - timedelta(days=CHRONIC_DAYS) < d <= day) / (CHRONIC_DAYS / 7)
        values |= {'load:acute': round(acute, 1), 'load:chronic': round(chronic, 1)}
        if chronic and date.fromisoformat(oldest) <= day - timedelta(days=CHRONIC_DAYS - 1):
            values['load:ratio'] = round(acute / chronic, 2)
        found[day.isoformat()] = values
        day += timedelta(days=1)
    return found


def weeks(first: date, last: date) -> list[dict]:
    """The minutes of moderate and of vigorous activity per week (its Monday; every week of the period, oldest first), of
    the sessions from `first` to `last`."""
    found: dict[str, dict] = {}
    monday = first - timedelta(days=first.weekday())
    while monday <= last:
        found[monday.isoformat()] = {'week': monday.isoformat(), 'moderate': 0.0, 'vigorous': 0.0}
        monday += timedelta(days=7)
    for day, values in _days(first, last).items():
        week = found[(day - timedelta(days=day.weekday())).isoformat()]
        week['moderate'] += values['moderate']
        week['vigorous'] += values['vigorous']
    return [w | {'moderate': round(w['moderate']), 'vigorous': round(w['vigorous'])} for w in found.values()]


def trimp(zone_seconds: list[int]) -> float:
    """The Edwards TRIMP of a session from its seconds below zone 1 and in each zone (polar_values.zone_seconds)."""
    return round(sum(s / 60 * k for k, s in enumerate(zone_seconds)), 1)
