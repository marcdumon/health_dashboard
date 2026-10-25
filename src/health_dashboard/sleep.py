"""Sleep from Sleep as Android. The phone's SleepCloud add-on writes a full backup to Dropbox (a zip with sleep-export.csv),
and the Dropbox app syncs it to the PC. Import sleep reads it and replaces every stored sleep with its sleeps; a page read
only compares the file's size and time with the last import. A sleep belongs to the calendar day on which it ends (its wake
time), and the values of a day add up its sleeps; a record without sleep time (a recording started and stopped at once) is
not a sleep. A value that was not measured is None, never 0: the stages and the time awake of a sleep without a
hypnogram (a sleep added by hand), and REM of a sleep without a REM event (the app found REM in few nights before December
2024). Snoring is left out: the user's sleeps have almost no snore data.

The app draws a night in two layers: light and deep sleep fill it and never overlap; the awake and REM periods lie over
them. Each minute counts once here, in this order: awake, REM, then deep or light. So the stages add up to the time in a
stage, and the deep % is the share of deep sleep in it."""

import csv
import io
import zipfile
from datetime import datetime, timedelta
from itertools import pairwise
from pathlib import Path
from zoneinfo import ZoneInfo

from health_dashboard import ledger, store
from health_dashboard.config import config

CSV_NAME = 'sleep-export.csv'
# the local times of a record: 24. 01. 2026 2:15
TIME_FORMAT = '%d. %m. %Y %H:%M'
# the free SleepCloud backs up once a week: an older backup has stopped
OLD_DAYS = 8
# the size and time of the file at the last import, and the time of that import
BACKUP_KEY, IMPORTED_KEY = 'sleep_backup', 'sleep_imported_at'
STEPS = 'On the phone, open Sleep as Android > Settings > Services > Cloud backup, and choose Dropbox.'
NOT_SET = ('Set backup_file in the [sleep] part of health_dashboard.toml to the Sleep as Android backup in Dropbox '
           '(Dropbox/Apps/Sleep Cloud Backup/Sleep as Android Data.zip), and restart the app.')
UNREADABLE = 'The backup file cannot be read: Dropbox may still be writing it. Wait a minute, then press Import sleep again.'
EMPTY = ('The backup holds no sleep, so Import sleep changed nothing. Open Sleep as Android on the phone and check that it '
         'shows your sleep.')
# the history keys of the values of a day, with their decimals
KEYS = {'sleep:total': 2, 'sleep:count': 0, 'sleep:in_bed': 2, 'sleep:awake': 0, 'sleep:deep': 0, 'sleep:light': 0,
        'sleep:rem': 0, 'sleep:deep_pct': 1, 'sleep:cycles': 0}
COLUMNS = ('id', 'tz', 'start_at', 'end_at', 'day', 'in_bed_min', 'sleep_min', 'awake_min', 'deep_min', 'light_min', 'rem_min',
           'cycles')


class SleepError(Exception):
    """A backup that cannot be imported, with what to do."""


def backup_file() -> Path | None:
    """The backup of health_dashboard.toml; None when not set."""
    path = config().sleep.backup_file
    return Path(path) if path else None


def _events(header: list[str], values: list[str]) -> list[tuple[str, int]]:
    """The events of a record in time order: kind and time (ms). An event is KIND-<ms> or KIND-<ms>-<value>, and a value
    can hold a dash (1.8E-43); the value is left out."""
    found = []
    for name, cell in zip(header, values, strict=False):
        parts = cell.split('-', 2)
        if name == 'Event' and len(parts) > 1 and parts[1].isdigit():
            found.append((parts[0], int(parts[1])))
    return sorted(found, key=lambda event: event[1])


def _periods(events: list[tuple[str, int]], kind: str) -> list[tuple[int, int]]:
    """The periods of one stage (LIGHT, DEEP, REM or AWAKE): from each KIND_START to the KIND_END after it, in ms."""
    found, opened = [], None
    for name, at in events:
        if name == f'{kind}_START':
            opened = at
        elif name == f'{kind}_END' and opened is not None:
            found.append((opened, at))
            opened = None
    return _merged(found)


def _merged(periods: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Periods in time order, joined where they overlap, so no minute counts twice."""
    found: list[tuple[int, int]] = []
    for start, end in sorted(periods):
        if found and start <= found[-1][1]:
            found[-1] = (found[-1][0], max(found[-1][1], end))
        else:
            found.append((start, end))
    return found


def _minutes(periods: list[tuple[int, int]]) -> float:
    """The minutes of some periods."""
    return sum(end - start for start, end in periods) / 60_000


def _overlap(periods: list[tuple[int, int]], others: list[tuple[int, int]]) -> float:
    """The minutes of `periods` that lie in `others`."""
    return sum(max(0, min(end, b) - max(start, a)) for start, end in periods for a, b in others) / 60_000


def _sleep(header: list[str], values: list[str]) -> dict:
    """One sleep from its header row and values row. Hours is the time in bed; LenAdjust (0 or less) is the app's own
    correction for the time awake and the pauses, in minutes. DeepSleep below 0: no hypnogram; Cycles -1: added by hand.
    Each minute is in one stage only: awake, then REM, then deep or light."""
    cell = dict(zip(header, values, strict=False))
    tz = ZoneInfo(cell['Tz'])
    start = datetime.strptime(cell['From'], TIME_FORMAT).replace(tzinfo=tz)
    end = datetime.strptime(cell['To'], TIME_FORMAT).replace(tzinfo=tz)
    in_bed = float(cell['Hours']) * 60
    cycles = int(float(cell.get('Cycles') or -1))
    found = {'id': int(cell['Id']), 'tz': cell['Tz'], 'start_at': start.isoformat(), 'end_at': end.isoformat(),
             'day': end.date().isoformat(), 'in_bed_min': round(in_bed, 1),
             'sleep_min': round(max(0.0, in_bed + float(cell.get('LenAdjust') or 0)), 1), 'awake_min': None, 'deep_min': None,
             'light_min': None, 'rem_min': None, 'cycles': cycles if cycles >= 0 else None}
    if float(cell.get('DeepSleep') or -1) >= 0:
        events = _events(header, values)
        light, deep, rem, awake = (_periods(events, kind) for kind in ('LIGHT', 'DEEP', 'REM', 'AWAKE'))
        over = _merged(rem + awake)
        found |= {'awake_min': round(_minutes(awake), 1),
                  'deep_min': round(_minutes(deep) - _overlap(deep, over), 1),
                  'light_min': round(_minutes(light) - _overlap(light, over), 1),
                  'rem_min': round(_minutes(rem) - _overlap(rem, awake), 1) if rem else None}
    return found


def parse(text: str) -> list[dict]:
    """The sleeps of sleep-export.csv. A record is a header row (Id, Tz, From, ..., its clock times and Event columns) and a
    values row; a tracked record has a third row of noise values, which is left out, and a record without sleep time is not
    a sleep."""
    rows = list(csv.reader(io.StringIO(text)))
    found = []
    for header, values in pairwise(rows):
        if header and header[0] == 'Id' and values:
            try:
                one = _sleep(header, values)
            except (KeyError, ValueError) as error:
                raise SleepError(f'A sleep in the backup cannot be read ({dict(zip(header, values, strict=False)).get("From")}): '
                                 f'{error}. Open that sleep in Sleep as Android on the phone and check its times.') from error
            if one['sleep_min'] > 0:
                found.append(one)
    return found


def _existing() -> Path:
    """The backup file; SleepError with what to do when it is not set or not there."""
    path = backup_file()
    if path is None:
        raise SleepError(NOT_SET)
    if not path.is_file():
        raise SleepError(_missing(path))
    return path


def _missing(path: Path) -> str:
    """What to do when the backup file is not there."""
    return (f'The backup file {path} does not exist. {STEPS} If the file is in another place, set backup_file in the [sleep] '
            'part of health_dashboard.toml and restart the app.')


def _stamp(path: Path) -> str:
    """The size and time of the file, to see a new backup."""
    info = path.stat()
    return f'{info.st_size}:{info.st_mtime_ns}'


def import_backup() -> int:
    """Read the backup and replace every stored sleep with its sleeps, in one transaction (the backup is the full list: a
    sleep deleted in the app goes); how many. A file that cannot be read (a cut-off zip fails its CRC check) or holds no
    sleep changes nothing: SleepError."""
    path = _existing()
    stamp = _stamp(path)
    try:
        with zipfile.ZipFile(path) as archive:
            text = archive.read(CSV_NAME).decode('utf-8')
    except (OSError, KeyError, UnicodeDecodeError, zipfile.BadZipFile) as error:
        raise SleepError(UNREADABLE) from error
    found = parse(text)
    if not found:
        raise SleepError(EMPTY)
    with ledger.connect(write=True) as db:
        db.execute('DELETE FROM sleep_records')
        db.executemany(f'INSERT OR REPLACE INTO sleep_records ({", ".join(COLUMNS)}) VALUES ({", ".join("?" * len(COLUMNS))})',
                       [tuple(s[c] for c in COLUMNS) for s in found])
        db.executemany('INSERT INTO health_settings (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value',
                       [(BACKUP_KEY, stamp), (IMPORTED_KEY, datetime.now().astimezone().isoformat(timespec='minutes'))])
    return len(found)


def status(now: datetime) -> dict:
    """What the Sleep card says about the backup, from a look at the file only: whether its place is set, the time of the
    backup, the time of the last import, whether a newer backup waits (another size or time), and what to do when the
    backup is not set, not there or more than OLD_DAYS old."""
    path = backup_file()
    found = {'configured': path is not None, 'backup_at': None, 'imported_at': store.setting(IMPORTED_KEY), 'waiting': False,
             'message': ''}
    if path is None:
        return found | {'message': NOT_SET}
    try:
        stamp, made = _stamp(path), datetime.fromtimestamp(path.stat().st_mtime).astimezone(now.tzinfo)
    except OSError:
        return found | {'message': _missing(path)}
    old = now - made > timedelta(days=OLD_DAYS)
    return found | {'backup_at': made.isoformat(timespec='minutes'), 'waiting': stamp != store.setting(BACKUP_KEY),
                    'message': f'The newest backup is from {made:%-d %B %Y}. {STEPS}' if old else ''}


def _stored(first: str, last: str) -> list[dict]:
    """The stored sleeps that end from day `first` to day `last`, in time order."""
    with ledger.connect() as db:
        return [dict(row) for row in db.execute('SELECT * FROM sleep_records WHERE day >= ? AND day <= ? ORDER BY id',
                                                (first, last))]


def _values(sleeps: list[dict]) -> dict[str, float]:
    """The values of a day from its sleeps, under their history keys (KEYS). Each value adds up the sleeps that have it,
    and a value that no sleep has is left out; the deep % is the deep minutes of the day over its minutes in a stage, not
    a mean of the sleeps' %."""
    def total(field: str) -> float | None:
        found = [s[field] for s in sleeps if s[field] is not None]
        return sum(found) if found else None

    staged = [s for s in sleeps if s['deep_min'] is not None]
    in_stage = sum(s['deep_min'] + s['light_min'] + (s['rem_min'] or 0) for s in staged)
    values = {'sleep:total': sum(s['sleep_min'] for s in sleeps) / 60, 'sleep:count': len(sleeps),
              'sleep:in_bed': sum(s['in_bed_min'] for s in sleeps) / 60, 'sleep:awake': total('awake_min'),
              'sleep:deep': total('deep_min'), 'sleep:light': total('light_min'), 'sleep:rem': total('rem_min'),
              'sleep:deep_pct': 100 * total('deep_min') / in_stage if in_stage else None, 'sleep:cycles': total('cycles')}
    return {key: round(value, KEYS[key]) for key, value in values.items() if value is not None}


def day_values(first: str, last: str) -> dict[str, dict[str, float]]:
    """The values of each day from `first` to `last` that has a sleep, for the history."""
    by_day: dict[str, list[dict]] = {}
    for found in _stored(first, last):
        by_day.setdefault(found['day'], []).append(found)
    return {day: _values(found) for day, found in by_day.items()}


def sleeps(first: str, last: str) -> list[dict]:
    """Each sleep that ends from day `first` to day `last`, for the chart of the sleep times: start_at, end_at, day and
    sleep_min."""
    return [{key: s[key] for key in ('start_at', 'end_at', 'day', 'sleep_min')} for s in _stored(first, last)]


def first_day() -> str | None:
    """The first day with a stored sleep; None without one."""
    with ledger.connect() as db:
        return db.execute('SELECT MIN(day) FROM sleep_records').fetchone()[0]


def day_card(day: str) -> dict | None:
    """The sleep of a day for the card: the values of the day and its sleeps (start_at, end_at, sleep_min) in time order;
    None without a sleep that ends on it."""
    found = _stored(day, day)
    if not found:
        return None
    return {'values': _values(found), 'sleeps': [{key: s[key] for key in ('start_at', 'end_at', 'sleep_min')} for s in found]}
