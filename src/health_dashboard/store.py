"""The Health page's own data in the ledger: one angina score a day with a note, the Ketostix readings (several a day, each
with its time), the tape measurements of the body (one value per site and day), and settings (the Withings sign-in). The
supplements are in supplements.py."""

import re
from datetime import date, datetime, timedelta

from health_dashboard.ledger import connect

# the day starts at midnight: a reading at 03:35 on a Wednesday is Wednesday's (the user, 2026-10-14)
DAY_START_HOUR = 0
TIME = re.compile(r'^([01]\d|2[0-3]):[0-5]\d$')


def health_day(now: datetime) -> date:
    """The day a moment belongs to: the day starts at DAY_START_HOUR."""
    return (now - timedelta(hours=DAY_START_HOUR)).date()


def checked_time(taken_at: str) -> str:
    """A clock time, checked."""
    if not TIME.match(taken_at or ''):
        raise ValueError(f'"{taken_at}" is not a time. Write it as HH:MM, for example 08:05.')
    return taken_at


def day_of(db, table: str, row_id: int) -> date:
    """The day of an entry or a reading; KeyError when there is no such row."""
    row = db.execute(f'SELECT day FROM {table} WHERE id = ?', (row_id,)).fetchone()
    if row is None:
        raise KeyError(row_id)
    return date.fromisoformat(row[0])


def angina(day: date) -> dict | None:
    """The angina score (0 to 5, or None), the note, and whether angina was not measured (no exertion that day); None when
    nothing was noted."""
    with connect() as db:
        row = db.execute('SELECT score, note, not_measured FROM health_angina WHERE day = ?', (day.isoformat(),)).fetchone()
    return {'score': row['score'], 'note': row['note'], 'not_measured': bool(row['not_measured'])} if row else None


def set_angina(day: date, score: int | None, note: str, not_measured: bool = False) -> None:
    """Note the day's angina score and note, or that angina was not measured: a day without exertion has no score, not a 0.
    Without any of these, the day has none."""
    if score is not None and not 0 <= score <= 5:
        raise ValueError(f'The angina score goes from 0 to 5, not {score}.')
    if score is not None and not_measured:
        raise ValueError('Choose an angina score or Not measured, not both.')
    note = note.strip()
    with connect(write=True) as db:
        if score is None and not note and not not_measured:
            db.execute('DELETE FROM health_angina WHERE day = ?', (day.isoformat(),))
            return
        db.execute('INSERT INTO health_angina (day, score, note, not_measured) VALUES (?, ?, ?, ?) ON CONFLICT (day) DO UPDATE '
                   'SET score = excluded.score, note = excluded.note, not_measured = excluded.not_measured',
                   (day.isoformat(), score, note, int(not_measured)))


# the six colours of a Ketostix strip (acetoacetate in urine, read after 15 seconds): mmol/L, name, mg/dL
KETOSTIX = [{'level': 0, 'label': 'Negative', 'mg_dl': 0}, {'level': 0.5, 'label': 'Trace', 'mg_dl': 5},
            {'level': 1.5, 'label': 'Small', 'mg_dl': 15}, {'level': 4, 'label': 'Moderate', 'mg_dl': 40},
            {'level': 8, 'label': 'Large', 'mg_dl': 80}, {'level': 16, 'label': 'Large', 'mg_dl': 160}]


def ketone_readings(day: date) -> list[dict]:
    """The Ketostix readings of a day in time order: id, time and level in mmol/L."""
    with connect() as db:
        rows = db.execute('SELECT id, taken_at, level FROM health_ketones WHERE day = ? ORDER BY taken_at, id', (day.isoformat(),))
        return [dict(row) for row in rows]


def add_ketone_reading(day: date, taken_at: str, level: float) -> int:
    """Note a Ketostix reading of a day at a time; its id."""
    if level not in {k['level'] for k in KETOSTIX}:
        raise ValueError(f'{level} mmol/L is not a colour of a Ketostix strip: choose 0, 0.5, 1.5, 4, 8 or 16.')
    with connect(write=True) as db:
        return db.execute('INSERT INTO health_ketones (day, taken_at, level) VALUES (?, ?, ?)',
                          (day.isoformat(), checked_time(taken_at), float(level))).lastrowid


def set_ketone_time(reading_id: int, taken_at: str) -> date:
    """Change the time of a reading; the day it belongs to."""
    with connect(write=True) as db:
        day = day_of(db, 'health_ketones', reading_id)
        db.execute('UPDATE health_ketones SET taken_at = ? WHERE id = ?', (checked_time(taken_at), reading_id))
    return day


def remove_ketone_reading(reading_id: int) -> date:
    """Remove a reading at once; the day it belonged to."""
    with connect(write=True) as db:
        day = day_of(db, 'health_ketones', reading_id)
        db.execute('DELETE FROM health_ketones WHERE id = ?', (reading_id,))
    return day


# the sites of the weekly tape measurement, in the order of the form: key, label; the arms relaxed. No neck, forearms or
# calves (the user, 2026-10-11)
TAPE_SITES = [('chest', 'Chest'), ('waist', 'Waist (navel)'), ('hips', 'Hips'),
              ('upper_arm_left', 'Upper arm, left'), ('upper_arm_right', 'Upper arm, right'),
              ('thigh_left', 'Thigh, left'), ('thigh_right', 'Thigh, right')]
TAPE_MIN_CM, TAPE_MAX_CM = 10, 250


def tape(day: date) -> dict[str, float]:
    """The tape measurements of a day: cm by site."""
    with connect() as db:
        return dict(db.execute('SELECT site, cm FROM health_tape WHERE day = ?', (day.isoformat(),)).fetchall())


def set_tape(day: date, site: str, cm: float | None) -> None:
    """Note the circumference of one site on a day, in cm with one decimal; None removes it. A new value on the same day
    replaces the old one."""
    if site not in dict(TAPE_SITES):
        raise ValueError(f'"{site}" is not a site of the tape measure.')
    with connect(write=True) as db:
        if cm is None:
            db.execute('DELETE FROM health_tape WHERE day = ? AND site = ?', (day.isoformat(), site))
            return
        if not TAPE_MIN_CM <= cm <= TAPE_MAX_CM:
            raise ValueError(f'A circumference goes from {TAPE_MIN_CM} to {TAPE_MAX_CM} cm, not {cm}.')
        db.execute('INSERT INTO health_tape (day, site, cm) VALUES (?, ?, ?) ON CONFLICT (day, site) DO UPDATE SET cm = excluded.cm',
                   (day.isoformat(), site, round(cm, 1)))


def last_tape(before: date) -> dict | None:
    """The last day with tape measurements before a day, with its values; None without one."""
    with connect() as db:
        row = db.execute('SELECT MAX(day) FROM health_tape WHERE day < ?', (before.isoformat(),)).fetchone()
    return None if row[0] is None else {'day': row[0], 'values': tape(date.fromisoformat(row[0]))}


def setting(key: str) -> str | None:
    """One stored setting."""
    with connect() as db:
        row = db.execute('SELECT value FROM health_settings WHERE key = ?', (key,)).fetchone()
    return row[0] if row else None


def set_setting(key: str, value: str | None) -> None:
    """Keep a setting, or remove it (None)."""
    with connect(write=True) as db:
        if value is None:
            db.execute('DELETE FROM health_settings WHERE key = ?', (key,))
        else:
            db.execute('INSERT INTO health_settings (key, value) VALUES (?, ?) '
                       'ON CONFLICT (key) DO UPDATE SET value = excluded.value', (key, value))
