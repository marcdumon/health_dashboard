"""The net energy expenditure of the Polar H10 sessions: what the exercise adds to the basal metabolic rate (BMR), which
the day counts already (the user, 2026-10-20); an estimate from the HR of each second. From energy_from_hr of person.toml
on (90 bpm at first), a second gives the equation of Keytel et al. (2005) without VO2max, from the HR, the weight, the age
and the sex, minus the BMR; below it, it gives nothing: the equation was made from exercise, and it gives far too much at
a low HR. A session never gives less than 0. The weight and the BMR are those of the Withings scale (not hidden), the
newest up to the day of a session, else its first after it (the scale gives the BMR since 2026-01); without the scale,
weight_kg of person.toml and the BMR of the Mifflin-St Jeor equation with height_cm. The age is the year of the session
minus the birth year, as for the zones. An HR estimate is rough: the equation comes from people aged 18 to 45 who train
regularly, it is about 15-25 % off for them in steady exercise, and it is too high for strength exercise (Weights), where
the strain raises the HR more than the use of oxygen; energy_factor of person.toml corrects every value with the user's
own reference."""

from bisect import bisect_right
from datetime import date, timedelta

from health_dashboard import ledger, person, polar_store, withings

# the equation of Keytel et al. (2005) without VO2max: kJ a minute = a + b * HR + c * weight (kg) + d * age (years)
KEYTEL = {'male': (-55.0969, 0.6309, 0.1988, 0.2017), 'female': (-20.4022, 0.4472, -0.1263, 0.074)}
KJ_PER_KCAL = 4.184
# the Mifflin-St Jeor equation: kcal a day = 10 * weight + 6.25 * height (cm) - 5 * age + this
MIFFLIN = {'male': 5, 'female': -161}
# the kinds of the Withings scale: the weight and the BMR
WEIGHT, BMR = 1, 226
# a correction factor outside this range is a typing error, not a correction
FACTOR_RANGE = (0.3, 2.0)


def _scale(kind: int) -> tuple[list[str], list[float]]:
    """The days and the values of one kind of the Withings scale, without the hidden ones, oldest first."""
    with ledger.connect() as db:
        rows = db.execute(f'SELECT substr(measured_at, 1, 10), value FROM withings_values WHERE type = ? AND position = ? AND '
                          f'{withings.NOT_HIDDEN} ORDER BY measured_at', (kind, withings.NO_PART)).fetchall()
    return [r[0] for r in rows], [r[1] for r in rows]


def _on(found: tuple[list[str], list[float]], day: str) -> float | None:
    """The newest value up to `day`, else the first one after it; None without a value."""
    days, values = found
    if not values:
        return None
    after = bisect_right(days, day)
    return values[after - 1] if after else values[0]


def _seconds(ids: list[int], from_hr: int) -> dict[int, tuple[int, float]]:
    """Of each session: its seconds with an HR from `from_hr` on, and the sum of their HR."""
    if not ids:
        return {}
    with ledger.connect() as db:
        rows = db.execute(f'SELECT session_id, SUM(hr >= ?), TOTAL(CASE WHEN hr >= ? THEN hr END) FROM polar_seconds '
                          f"WHERE hr IS NOT NULL AND session_id IN ({', '.join('?' * len(ids))}) GROUP BY session_id",
                          (from_hr, from_hr, *ids)).fetchall()
    return {r[0]: (r[1], r[2]) for r in rows}


def missing() -> str:
    """What the net energy expenditure needs and person.toml does not give, with what to do; '' when nothing is missing."""
    own = person.settings()
    lacking = [name for name, there in (('birth_date', person.birth_year() is not None), ('sex', person.sex() is not None),
                                        ('weight_kg', own.weight_kg is not None or bool(_scale(WEIGHT)[1])),
                                        ('height_cm', own.height_cm is not None or bool(_scale(BMR)[1]))) if not there]
    if not lacking:
        return ''
    names = lacking[0] if len(lacking) == 1 else f"{', '.join(lacking[:-1])} and {lacking[-1]}"
    scale = (' A Withings scale gives the weight and the BMR in place of weight_kg and height_cm.'
             if {'weight_kg', 'height_cm'} & set(lacking) else '')
    return f"The net energy expenditure needs {names}: set {'it' if len(lacking) == 1 else 'them'} {person.SET_IT}.{scale}"


def factor() -> tuple[float, str]:
    """The correction factor of person.toml (1 when it is outside FACTOR_RANGE) and a sentence about it ('' for 1)."""
    found = person.settings().energy_factor
    low, high = FACTOR_RANGE
    if not low <= found <= high:
        return 1.0, f'energy_factor {found:g} is outside {low:g}-{high:g}, so the app does not use it: change it {person.SET_IT}.'
    return found, '' if found == 1 else f'Corrected by your energy_factor {found:g}.'


def energy(sessions: list[dict]) -> dict[int, int | None]:
    """The net energy expenditure in kcal of each session (its id and day); None when a value it needs is missing."""
    born, sex = person.birth_year(), person.sex()
    if born is None or sex is None:
        return {s['id']: None for s in sessions}
    own = person.settings()
    weights, bmrs = _scale(WEIGHT), _scale(BMR)
    a, b, c, d = KEYTEL[sex]
    counts = _seconds([s['id'] for s in sessions], own.energy_from_hr)
    correction = factor()[0]
    found: dict[int, int | None] = {}
    for session in sessions:
        age = int(session['day'][:4]) - born
        weight = _on(weights, session['day']) or own.weight_kg
        bmr = _on(bmrs, session['day']) or (None if weight is None or own.height_cm is None
                                            else 10 * weight + 6.25 * own.height_cm - 5 * age + MIFFLIN[sex])
        high, hr_sum = counts.get(session['id'], (0, 0.0))
        if weight is None or bmr is None:
            found[session['id']] = None
            continue
        # kJ a minute of each second from the HR on, minus the BMR (kcal a day) of those seconds
        kcal = (high * (a + c * weight + d * age) + b * hr_sum) / 60 / KJ_PER_KCAL - high * bmr / 86400
        found[session['id']] = round(max(kcal, 0) * correction)
    return found


def weeks(first: date, last: date) -> list[dict]:
    """The net energy expenditure in kcal of the sessions that count and are not HRV tests (a rest), per week (its Monday;
    every week of the period, oldest first) and per activity name (polar_store.NO_ACTIVITY for a session without one)."""
    sessions = polar_store.exercise_sessions(first.isoformat(), last.isoformat())
    kcal = energy(sessions)
    found: dict[str, dict[str, int]] = {}
    monday = first - timedelta(days=first.weekday())
    while monday <= last:
        found[monday.isoformat()] = {}
        monday += timedelta(days=7)
    for session in sessions:
        if kcal[session['id']] is None:
            continue
        when = date.fromisoformat(session['day'])
        week = found[(when - timedelta(days=when.weekday())).isoformat()]
        name = session['activity'] or polar_store.NO_ACTIVITY
        week[name] = week.get(name, 0) + kcal[session['id']]
    return [{'week': week, 'kcal': by} for week, by in found.items()]
