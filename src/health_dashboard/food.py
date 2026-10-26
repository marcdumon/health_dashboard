"""The food of a day, noted without counting (the user, 2026-10-26: counting calories and macros every day is too much
work): the time of the first and of the last food, scales from -3 to +3 against the usual day (the amount of all food,
protein, carbs and fat), the marks alcohol and much salt, and whether the day is a fast day. A fast day has no scales: it
has its kcal and its grams of protein, carbs and fat. A day is noted on the day itself or later.

A day without a note has no food values: an unknown day is not a usual day. On a noted day a scale not chosen is 0 (the
usual day) and a mark not set is no. A day goes from midnight to midnight, so food after midnight belongs to the next day.
Derived for the history: the eating window (from the first to the last food), the hours without food before the first
food of a day (from the last food of an earlier day; fast days without food between count, any other day without a
time stops it), the hours from the last food to the next sleep (Sleep as Android: a sleep that starts within
SLEEP_WITHIN_HOURS), the day number of a fast (the fast days in a row) and the refeed day (the REFEED_DAYS days after a
fast)."""

from datetime import date, datetime, time, timedelta

from health_dashboard.ledger import connect
from health_dashboard.store import checked_time

TIMES = ('first_food', 'last_food')
SCALES = ('amount', 'protein', 'carbs', 'fat')
MARKS = ('alcohol', 'salt', 'fast')
# the values of a fast day and the highest of each
FAST_VALUES = {'kcal': 10000, 'protein_g': 1000, 'carbs_g': 1000, 'fat_g': 1000}
COLUMNS = (*TIMES, *SCALES, *MARKS, *FAST_VALUES)
SCALE_MIN, SCALE_MAX = -3, 3
REFEED_DAYS = 7
SLEEP_WITHIN_HOURS = 12
# the history keys and their decimals
KEYS = {'food:amount': 0, 'food:protein': 0, 'food:carbs': 0, 'food:fat': 0, 'food:alcohol': 0, 'food:salt': 0,
        'food:window': 1, 'food:fasted': 1, 'food:to_sleep': 1, 'food:fast_day': 0, 'food:refeed_day': 0,
        'food:kcal': 0, 'food:protein_g': 0, 'food:carbs_g': 0, 'food:fat_g': 0}


def day(chosen: date) -> dict | None:
    """The food noted for a day, every field (None or False when not set); None when the day has no note."""
    with connect() as db:
        row = db.execute(f'SELECT {", ".join(COLUMNS)} FROM health_food WHERE day = ?', (chosen.isoformat(),)).fetchone()
    return None if row is None else {name: bool(row[name]) if name in MARKS else row[name] for name in COLUMNS}


def _checked(name: str, value: object) -> object:
    """The value of one field, checked, as it is stored."""
    if name in TIMES:
        return None if value is None else checked_time(str(value))
    if name in SCALES:
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or not SCALE_MIN <= value <= SCALE_MAX):
            raise ValueError(f'A scale goes from -3 to +3, not {value}.')
        return value
    if name in MARKS:
        if not isinstance(value, bool):
            raise ValueError(f'The mark {name} is yes or no, not {value}.')
        return int(value)
    if name in FAST_VALUES:
        highest = FAST_VALUES[name]
        if value is not None and (isinstance(value, bool) or not isinstance(value, int | float) or not 0 <= value <= highest):
            what = 'The kcal of a day go' if name == 'kcal' else f'The grams of {name.removesuffix("_g")} go'
            raise ValueError(f'{what} from 0 to {highest}, not {value}.')
        return value
    raise ValueError(f'"{name}" is not a part of the food of a day.')


def set_food(chosen: date, changes: dict[str, object]) -> None:
    """Change some fields of the food of a day; the other fields stay. A day whose fields are all empty again has no note.
    The last food is not before the first: food after midnight belongs to the next day."""
    checked = {name: _checked(name, value) for name, value in changes.items()}
    key = chosen.isoformat()
    with connect(write=True) as db:
        db.execute('INSERT OR IGNORE INTO health_food (day) VALUES (?)', (key,))
        if checked:
            db.execute(f'UPDATE health_food SET {", ".join(f"{name} = ?" for name in checked)} WHERE day = ?',
                       (*checked.values(), key))
        row = db.execute(f'SELECT {", ".join(COLUMNS)} FROM health_food WHERE day = ?', (key,)).fetchone()
        if row['first_food'] and row['last_food'] and row['last_food'] < row['first_food']:
            raise ValueError(f'The last food ({row["last_food"]}) is before the first food ({row["first_food"]}). Food after '
                             'midnight belongs to the next day: note it there.')
        if all(row[name] is None for name in (*TIMES, *SCALES, *FAST_VALUES)) and not any(row[name] for name in MARKS):
            db.execute('DELETE FROM health_food WHERE day = ?', (key,))


def clear(chosen: date) -> None:
    """Remove the food note of a day at once."""
    with connect(write=True) as db:
        db.execute('DELETE FROM health_food WHERE day = ?', (chosen.isoformat(),))


def _minutes(clock: str) -> int:
    """The minutes of a clock time after midnight."""
    return int(clock[:2]) * 60 + int(clock[3:])


def _fast(rows: dict, on: date) -> bool:
    """Whether a day is a noted fast day."""
    row = rows.get(on.isoformat())
    return row is not None and bool(row['fast'])


def _fasted(on: date, rows: dict) -> float | None:
    """The hours from the last food of an earlier day to the first food of a day; fast days without food between count,
    any other day without a time stops it (None)."""
    first = rows[on.isoformat()]['first_food']
    if not first:
        return None
    back = on - timedelta(days=1)
    while (row := rows.get(back.isoformat())) is not None:
        if row['last_food']:
            return ((on - back).days * 24 * 60 + _minutes(first) - _minutes(row['last_food'])) / 60
        if not (row['fast'] and not row['first_food'] and not row['kcal']):
            return None
        back -= timedelta(days=1)
    return None


def _to_sleep(on: date, last: str | None, starts: list[datetime]) -> float | None:
    """The hours from the last food of a day to the next sleep that starts within SLEEP_WITHIN_HOURS."""
    if not last:
        return None
    at = datetime.combine(on, time(int(last[:2]), int(last[3:])))
    after = [s for s in starts if at <= s < at + timedelta(hours=SLEEP_WITHIN_HOURS)]
    return (min(after) - at).total_seconds() / 3600 if after else None


def _values(on: date, rows: dict, starts: list[datetime]) -> dict[str, float]:
    """The food values of one day (see the module)."""
    row = rows.get(on.isoformat())
    found: dict[str, float] = {}
    fast = _fast(rows, on)
    if row is not None:
        found |= {'food:alcohol': row['alcohol'], 'food:salt': row['salt']}
        if fast:
            found |= {f'food:{name}': row[name] for name in FAST_VALUES if row[name] is not None}
        else:
            found |= {f'food:{name}': row[name] or 0 for name in SCALES}
        if row['first_food'] and row['last_food']:
            found['food:window'] = (_minutes(row['last_food']) - _minutes(row['first_food'])) / 60
        if (hours := _fasted(on, rows)) is not None:
            found['food:fasted'] = hours
        if (hours := _to_sleep(on, row['last_food'], starts)) is not None:
            found['food:to_sleep'] = hours
    run = 0
    while _fast(rows, on - timedelta(days=run)):
        run += 1
    if run or row is not None:
        found['food:fast_day'] = run
    if not fast:
        refeed = next((n for n in range(1, REFEED_DAYS + 1) if _fast(rows, on - timedelta(days=n))), None)
        if refeed is not None:
            found['food:refeed_day'] = refeed
    return found


def day_values(first: str, last: str) -> dict[str, dict[str, float]]:
    """The food values of each day from `first` to `last` under their history keys (KEYS); a value missing on a day is not
    in its values, and a day without any is left out."""
    end = date.fromisoformat(last)
    with connect() as db:
        rows = {row['day']: row for row in db.execute(f'SELECT day, {", ".join(COLUMNS)} FROM health_food WHERE day <= ?',
                                                      (last,))}
        starts = [datetime.fromisoformat(row[0]).replace(tzinfo=None) for row in db.execute(
            'SELECT start_at FROM sleep_records WHERE day >= ? AND day <= ?', (first, (end + timedelta(days=1)).isoformat()))]
    found = {}
    on = date.fromisoformat(first)
    while on <= end:
        if values := _values(on, rows, starts):
            found[on.isoformat()] = {key: round(value, KEYS[key]) for key, value in values.items()}
        on += timedelta(days=1)
    return found
