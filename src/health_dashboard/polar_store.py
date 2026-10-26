"""The Polar H10 sessions in two databases. data/health.db holds the small results: each session with its summary,
its HR and movement of each second, its beats, its markers, and the files read (so a file is read once). data/polar.db holds the raw
ECG and acceleration as packed blocks (polar_values.pack), so the app's database stays small."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise

from health_dashboard import ledger
from health_dashboard.config import DATA_DIR

RAW_DB = DATA_DIR / 'polar.db'
RAW_SCHEMA = '''
CREATE TABLE IF NOT EXISTS blocks (
    session_id INTEGER NOT NULL, stream TEXT NOT NULL, host_ms INTEGER NOT NULL, sensor_ns INTEGER NOT NULL, rate REAL NOT NULL,
    count INTEGER NOT NULL, channels INTEGER NOT NULL, data BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS blocks_by_session ON blocks (session_id, stream, host_ms);
'''
# the values of the sessions that the history and the analysis know: of each session (SESSION_KEYS), and of the tests
# (HRV, HR recovery). A value of the sessions has no value of a day: a day mixes activities, and days with and without a
# recording (the user, 2026-10-17)
SESSION_KEYS = ('h10:max_hr', 'h10:mean_hr')
KEYS = ('h10:max_hr', 'h10:mean_hr', 'h10:rest_hr', 'h10:rmssd', 'h10:ln_rmssd', 'h10:ln_rmssd_7d',
        'h10:ln_rmssd_cv7', 'h10:rest_hr_7d',
        'h10:sdnn', 'h10:pnn50', 'h10:lf', 'h10:hf', 'h10:lf_hf', 'h10:stress', 'h10:hr_stop',
        'h10:drop_60', 'h10:drop_120', 'h10:stairs_time', 'h10:stairs_pauses', 'h10:stairs_pause_time', 'h10:stairs_max_hr',
        'h10:stairs_hr_top', 'h10:stairs_drop_60', 'h10:stairs_drop_120')
# the columns recovery and recovery_state (of the Cooldown 60 s button, removed on 2026-10-16) stay empty in the database;
# moving_s, still_s, moving_hr and still_hr (the split in movement and still, removed on 2026-10-21) keep their old values
# in an older database, unused
FIELDS = ('source', 'serial', 'path', 'start_at', 'end_at', 'day', 'kind', 'seconds', 'mean_hr', 'max_hr', 'rest_hr', 'rmssd',
          'wrong_beats_pct', 'imported_at')
SHOWN = ('id, source, serial, start_at, end_at, day, kind, seconds, mean_hr, max_hr, rest_hr, rmssd, '
         'wrong_beats_pct, copy_of, removed, activity_id, note')
# the sessions that count in the history: not removed, not a copy of a logger session, not a recording joined to the session
# before it. A glitch of the logger app can cut one session in two; the user joins them, and the first one (joined_to of
# the others) counts for all of them. Each recording keeps its own rows: the reads of a session take those of all its
# recordings (FAMILY), so a join stays when a file is read again, and Split takes it apart
COUNTED = 's.removed = 0 AND s.copy_of IS NULL AND s.joined_to IS NULL'
FAMILY = '(SELECT id FROM polar_sessions WHERE id = :id OR joined_to = :id)'
# the name in the weekly minutes of the sessions without an activity
NO_ACTIVITY = 'No activity'
# the activities of the tests, and the SQL conditions (on the alias s) for the sessions that are HRV tests and those that
# are not: an HRV test is a rest, so it stays out of the exercise values; an HR recovery test and a stairs test are
# exercise
HRV_TEST, RECOVERY_TEST, STAIRS_TEST = 'hrv', 'recovery', 'stairs'
TEST_NAMES = {HRV_TEST: 'the HRV test', RECOVERY_TEST: 'the HR recovery test', STAIRS_TEST: 'the stairs test'}
IS_HRV = f"s.activity_id IN (SELECT id FROM polar_activities WHERE test = '{HRV_TEST}')"
NOT_HRV = f'(s.activity_id IS NULL OR NOT {IS_HRV})'


@contextmanager
def raw(write: bool = False) -> Iterator[sqlite3.Connection]:
    """A connection to data/polar.db, made with its table on first use; a writing one takes the write lock up front."""
    RAW_DB.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(RAW_DB, timeout=30, isolation_level=None)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute('PRAGMA journal_mode=WAL')
        connection.executescript(RAW_SCHEMA)
        connection.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
        try:
            yield connection
        except BaseException:
            connection.execute('ROLLBACK')
            raise
        connection.execute('COMMIT')
    finally:
        connection.close()


def known_files() -> dict[str, tuple[int, int]]:
    """Each file read before, with its size and change time (ns) then."""
    with ledger.connect() as db:
        return {row['path']: (row['size'], row['mtime_ns']) for row in db.execute('SELECT path, size, mtime_ns FROM polar_files')}


def remember_files(files: list[tuple[str, int, int]], session_id: int | None = None) -> None:
    """Note files as read, with their size and change time: path, size, mtime_ns."""
    with ledger.connect(write=True) as db:
        db.executemany('INSERT OR REPLACE INTO polar_files (path, size, mtime_ns, session_id) VALUES (?, ?, ?, ?)',
                       [(*f, session_id) for f in files])


def save(row: dict, seconds: list[tuple], beats: list[tuple], blocks: list[tuple], files: list[tuple[str, int, int]],
         markers: list[tuple] = ()) -> int:
    """Store one session: its row, its seconds (t, hr, movement), its beats (t_ms, rr_ms, clean), its raw blocks and its
    markers (t_ms, label, hr). A session read before (the same source and path, or the same strap
    and start) keeps its number, its removed state and its join; its earlier seconds, beats, markers and blocks are
    replaced, and a joined session gets its row from all its recordings again. The files are noted last, so a failure reads
    them again next time."""
    values = [row[f] for f in FIELDS]
    with ledger.connect(write=True) as db:
        old = db.execute('SELECT id FROM polar_sessions WHERE source = ? AND (path = ? OR (serial = ? AND start_at = ?))',
                         (row['source'], row['path'], row['serial'], row['start_at'])).fetchone()
        if old:
            session_id = old['id']
            db.execute(f"UPDATE polar_sessions SET {', '.join(f'{f} = ?' for f in FIELDS)} WHERE id = ?", (*values, session_id))
            db.execute('DELETE FROM polar_seconds WHERE session_id = ?', (session_id,))
            db.execute('DELETE FROM polar_beats WHERE session_id = ?', (session_id,))
            db.execute('DELETE FROM polar_markers WHERE session_id = ?', (session_id,))
        else:
            session_id = db.execute(f"INSERT INTO polar_sessions ({', '.join(FIELDS)}) VALUES ({', '.join('?' * len(FIELDS))})",
                                    values).lastrowid
        db.executemany('INSERT INTO polar_seconds (session_id, t, hr, movement) VALUES (?, ?, ?, ?)',
                       [(session_id, *s) for s in seconds])
        db.executemany('INSERT INTO polar_beats (session_id, t_ms, rr_ms, clean) VALUES (?, ?, ?, ?)',
                       [(session_id, *b) for b in beats])
        db.executemany('INSERT INTO polar_markers (session_id, t_ms, label, hr) VALUES (?, ?, ?, ?)',
                       [(session_id, *m) for m in markers])
        joined = db.execute('SELECT COALESCE(joined_to, id) FROM polar_sessions WHERE id = ?', (session_id,)).fetchone()[0]
        if joined != session_id or db.execute('SELECT 1 FROM polar_sessions WHERE joined_to = ?', (session_id,)).fetchone():
            _summarise(db, joined)
    with raw(write=True) as db:
        db.execute('DELETE FROM blocks WHERE session_id = ?', (session_id,))
        db.executemany('INSERT INTO blocks (session_id, stream, host_ms, sensor_ns, rate, count, channels, data) '
                       'VALUES (?, ?, ?, ?, ?, ?, ?, ?)', [(session_id, *b) for b in blocks])
    remember_files(files, session_id)
    return session_id


def mark_copies() -> None:
    """A phone session that overlaps a logger session of the same strap for more than half of the shorter one is a copy of
    it: the logger copy stays (its RR has steps of 1/1024 s)."""
    with ledger.connect(write=True) as db:
        db.execute("UPDATE polar_sessions SET copy_of = NULL WHERE source = 'phone'")
        rows = db.execute('SELECT id, source, serial, start_at, end_at FROM polar_sessions').fetchall()
        span = {r['id']: (datetime.fromisoformat(r['start_at']), datetime.fromisoformat(r['end_at'])) for r in rows}
        loggers = [r for r in rows if r['source'] == 'logger']
        for phone in (r for r in rows if r['source'] == 'phone'):
            (start, end) = span[phone['id']]
            for logger in (r for r in loggers if r['serial'] == phone['serial']):
                (other_start, other_end) = span[logger['id']]
                overlap = (min(end, other_end) - max(start, other_start)).total_seconds()
                if overlap > min((end - start).total_seconds(), (other_end - other_start).total_seconds()) / 2:
                    db.execute('UPDATE polar_sessions SET copy_of = ? WHERE id = ?', (logger['id'], phone['id']))
                    break


def _at(t_ms: int) -> str:
    """A UTC time in ms as ISO text, to the second."""
    return datetime.fromtimestamp(t_ms / 1000, UTC).isoformat(timespec='seconds')


def _dicts(db: sqlite3.Connection, rows: list[sqlite3.Row]) -> list[dict]:
    """The sessions as dicts, each with its activity's name, the start of the session it is a copy of, its markers in time
    order (of all its recordings) and how many recordings it joins (1: none joined)."""
    copies = sorted({r['copy_of'] for r in rows if r['copy_of']})
    starts = dict(db.execute(f"SELECT id, start_at FROM polar_sessions WHERE id IN ({', '.join('?' * len(copies))})",
                             copies).fetchall()) if copies else {}
    ids = [r['id'] for r in rows]
    marks: dict[int, list[dict]] = {}
    parts: dict[int, int] = {}
    if ids:
        holes = ', '.join('?' * len(ids))
        parts = dict(db.execute(f'SELECT joined_to, COUNT(*) FROM polar_sessions WHERE joined_to IN ({holes}) GROUP BY joined_to',
                                ids).fetchall())
        for m in db.execute('SELECT m.*, COALESCE(s.joined_to, s.id) AS owner FROM polar_markers m JOIN polar_sessions s ON '
                            f's.id = m.session_id WHERE s.id IN ({holes}) OR s.joined_to IN ({holes}) ORDER BY m.t_ms', ids + ids):
            marks.setdefault(m['owner'], []).append({'at': _at(m['t_ms']), 'label': m['label'], 'hr': m['hr']})
    kinds = {r['id']: (r['name'], r['test']) for r in db.execute('SELECT id, name, test FROM polar_activities')}
    return [dict(r) | {'removed': bool(r['removed']), 'copy_of_start': starts.get(r['copy_of']), 'markers': marks.get(r['id'], []),
                       'activity': kinds.get(r['activity_id'], (None, None))[0],
                       'activity_test': kinds.get(r['activity_id'], (None, None))[1],
                       'recordings': parts.get(r['id'], 0) + 1} for r in rows]


def sessions(offset: int, count: int) -> dict:
    """The sessions that are not removed, newest first, `count` from `offset`, and how many there are."""
    with ledger.connect() as db:
        rows = db.execute(f'SELECT {SHOWN} FROM polar_sessions WHERE removed = 0 AND joined_to IS NULL ORDER BY start_at DESC '
                          'LIMIT ? OFFSET ?', (count, offset)).fetchall()
        total = db.execute('SELECT COUNT(*) FROM polar_sessions WHERE removed = 0 AND joined_to IS NULL').fetchone()[0]
        return {'sessions': _dicts(db, rows), 'total': total}


def removed_sessions() -> list[dict]:
    """The sessions the user removed, newest first."""
    with ledger.connect() as db:
        rows = db.execute(f'SELECT {SHOWN} FROM polar_sessions WHERE removed = 1 AND joined_to IS NULL ORDER BY start_at DESC'
                          ).fetchall()
        return _dicts(db, rows)


def day_sessions(day: str) -> list[dict]:
    """Every session of a day, removed ones too, in time order; a joined recording is in the session it is joined to."""
    with ledger.connect() as db:
        rows = db.execute(f'SELECT {SHOWN} FROM polar_sessions WHERE day = ? AND joined_to IS NULL ORDER BY start_at',
                          (day,)).fetchall()
        return _dicts(db, rows)


def session(session_id: int) -> dict | None:
    """One session as the lists show it, with its activity and its markers; None when there is no such session."""
    with ledger.connect() as db:
        row = db.execute(f'SELECT {SHOWN} FROM polar_sessions WHERE id = ?', (session_id,)).fetchone()
        return _dicts(db, [row])[0] if row else None


def seconds(session_id: int) -> list[dict]:
    """The seconds of a session in time order, one for each second: t (epoch s), hr and movement (None where there is none,
    as between the recordings of a joined session)."""
    with ledger.connect() as db:
        rows = db.execute(f'SELECT t, hr, movement FROM polar_seconds WHERE session_id IN {FAMILY} ORDER BY t',
                          {'id': session_id}).fetchall()
    found: list[dict] = []
    for row in rows:
        if found and row['t'] > found[-1]['t'] + 1:
            found += [{'t': t, 'hr': None, 'movement': None} for t in range(found[-1]['t'] + 1, row['t'])]
        found.append(dict(row))
    return found


def beats(session_id: int) -> list[sqlite3.Row]:
    """The beats of a session (of all its recordings) in time order: t_ms, rr_ms and clean."""
    with ledger.connect() as db:
        return db.execute(f'SELECT t_ms, rr_ms, clean FROM polar_beats WHERE session_id IN {FAMILY} ORDER BY t_ms, rowid',
                          {'id': session_id}).fetchall()


def sessions_of_test(test: str, first: str, last: str) -> list[dict]:
    """The sessions of a test ('hrv', 'recovery' or 'stairs') that count from day `first` to day `last`, in time order: id, day and
    start_at."""
    with ledger.connect() as db:
        return [dict(r) for r in db.execute('SELECT s.id, s.day, s.start_at FROM polar_sessions s JOIN polar_activities a ON '
                                            f'a.id = s.activity_id WHERE a.test = ? AND s.day >= ? AND s.day <= ? AND {COUNTED} '
                                            'ORDER BY s.start_at', (test, first, last))]


def last_session_of_test(test: str) -> dict | None:
    """The newest session of a test that counts, any day: id, day and start_at; None without one."""
    with ledger.connect() as db:
        row = db.execute('SELECT s.id, s.day, s.start_at FROM polar_sessions s JOIN polar_activities a ON a.id = s.activity_id '
                         f'WHERE a.test = ? AND {COUNTED} ORDER BY s.start_at DESC LIMIT 1', (test,)).fetchone()
    return dict(row) if row else None


def marker_times(session_id: int, *labels: str) -> list[int]:
    """The times (UTC ms) of the markers of a session with one of these labels, in time order."""
    return [t_ms for t_ms, label in markers(session_id) if label in labels]


def markers(session_id: int) -> list[tuple[int, str]]:
    """The markers of a session (of all its recordings: UTC ms, label), in time order."""
    with ledger.connect() as db:
        return [(r[0], r[1]) for r in db.execute(f'SELECT t_ms, label FROM polar_markers WHERE session_id IN {FAMILY} '
                                                 'ORDER BY t_ms', {'id': session_id})]


def earlier(found: dict, count: int) -> list[dict]:
    """The last `count` sessions that count with the same activity as `found`, started before it, newest first."""
    with ledger.connect() as db:
        rows = db.execute(f'SELECT {SHOWN} FROM polar_sessions s WHERE s.activity_id = ? AND s.start_at < ? AND {COUNTED} '
                          'ORDER BY s.start_at DESC LIMIT ?', (found['activity_id'], found['start_at'], count)).fetchall()
        return [dict(r) for r in rows]


def latest(day: str) -> dict:
    """For the card of a day: the newest session up to that day that counts and is not an HRV test (`last_exercise`); None
    when there is none."""
    with ledger.connect() as db:
        row = db.execute(f'SELECT {SHOWN} FROM polar_sessions s WHERE s.day <= ? AND {NOT_HRV} AND {COUNTED} '
                         'ORDER BY s.start_at DESC LIMIT 1', (day,)).fetchone()
        last = _dicts(db, [row])[0] if row else None
    return {'last_exercise': last}


def set_removed(ids: list[int], removed: bool) -> int:
    """Take sessions out of the history and the analysis, or put them back, in one write; how many of them there are."""
    with ledger.connect(write=True) as db:
        marks = ','.join('?' * len(ids))
        return db.execute(f'UPDATE polar_sessions SET removed = ? WHERE id IN ({marks})', (int(removed), *ids)).rowcount


class JoinError(ValueError):
    """Sessions that cannot be joined or split, with what to do."""


def owners(ids: list[int]) -> dict[int, int]:
    """Each of these sessions and each recording joined to one of them, with the session it counts in."""
    if not ids:
        return {}
    with ledger.connect() as db:
        parts = db.execute(f"SELECT id, joined_to FROM polar_sessions WHERE joined_to IN ({', '.join('?' * len(ids))})",
                           ids).fetchall()
    return {i: i for i in ids} | {r[0]: r[1] for r in parts}


def _summarise(db: sqlite3.Connection, session_id: int) -> None:
    """The row of a session from the seconds of all its recordings: its end at its last second, its length from its first
    to its last second, and the mean and the maximum of its HR of each second. Its read time changes, so the training load
    of the session is made again."""
    first, last, mean, top = db.execute(f'SELECT MIN(t), MAX(t), AVG(hr), MAX(hr) FROM polar_seconds WHERE session_id IN {FAMILY}',
                                        {'id': session_id}).fetchone()
    if first is None:
        return
    db.execute('UPDATE polar_sessions SET end_at = ?, seconds = ?, mean_hr = ?, max_hr = ?, imported_at = ? WHERE id = ?',
               (_at(last * 1000), last - first + 1, None if mean is None else round(mean, 1), top,
                datetime.now(UTC).isoformat(timespec='seconds'), session_id))


def join(ids: list[int]) -> int:
    """Join sessions that a glitch cut apart into the first of them; its id. It keeps its activity (or takes the first
    activity of the others) and its note. Refused: fewer than two sessions, a removed one, a copy, one joined before, a
    test, sessions that overlap, and a session that counts between them but is not chosen. KeyError: no such session."""
    chosen = sorted(set(ids))
    if len(chosen) < 2:
        raise JoinError('Select at least two sessions to join.')
    holes = ', '.join('?' * len(chosen))
    with ledger.connect(write=True) as db:
        rows = db.execute(f'SELECT s.*, a.test FROM polar_sessions s LEFT JOIN polar_activities a ON a.id = s.activity_id '
                          f'WHERE s.id IN ({holes}) ORDER BY s.start_at', chosen).fetchall()
        if len(rows) < len(chosen):
            raise KeyError(set(chosen) - {r['id'] for r in rows})
        if any(r['removed'] for r in rows):
            raise JoinError('A selected session is removed. Show it again first, or leave it out.')
        if any(r['copy_of'] for r in rows):
            raise JoinError('A selected session is a phone copy of a PC session. Join the PC sessions.')
        if any(r['joined_to'] for r in rows):
            raise JoinError('A selected session is already part of a joined session. Split that session first.')
        if any(r['test'] for r in rows):
            raise JoinError('A selected session is a test (HRV, HR recovery or stairs). Only other sessions can be joined.')
        if any(after['start_at'] <= before['end_at'] for before, after in pairwise(rows)):
            raise JoinError('The selected sessions overlap. Only sessions that follow each other can be joined.')
        if db.execute(f'SELECT 1 FROM polar_sessions s WHERE {COUNTED} AND s.start_at > ? AND s.start_at < ? AND s.id NOT IN '
                      f'({holes})', (rows[0]['start_at'], rows[-1]['end_at'], *chosen)).fetchone():
            raise JoinError('Another session lies between the selected ones. Select it too, or remove it first.')
        owner, others = rows[0]['id'], [r['id'] for r in rows[1:]]
        activity = next((r['activity_id'] for r in rows if r['activity_id'] is not None), None)
        db.execute('UPDATE polar_sessions SET activity_id = ? WHERE id = ?', (activity, owner))
        db.execute(f"UPDATE polar_sessions SET joined_to = ? WHERE id IN ({', '.join('?' * len(others))})", (owner, *others))
        _summarise(db, owner)
    return owner


def split(session_id: int) -> None:
    """Take a joined session apart again, at once: each recording is a session of its own. KeyError: no such session."""
    with ledger.connect(write=True) as db:
        if db.execute('SELECT 1 FROM polar_sessions WHERE id = ?', (session_id,)).fetchone() is None:
            raise KeyError(session_id)
        if not db.execute('UPDATE polar_sessions SET joined_to = NULL WHERE joined_to = ?', (session_id,)).rowcount:
            raise JoinError('This session is not joined, so it cannot be split.')
        _summarise(db, session_id)


def count() -> int:
    """How many sessions are stored."""
    with ledger.connect() as db:
        return db.execute('SELECT COUNT(*) FROM polar_sessions').fetchone()[0]


class ActivityError(ValueError):
    """An activity cannot be added, renamed or removed; the message says what to do."""


def activities() -> list[dict]:
    """The activities in list order, each with how many sessions have it, the start of its newest session that is not
    removed (None without one) and its test."""
    with ledger.connect() as db:
        return [dict(r)
                for r in db.execute('SELECT a.id, a.name, a.test, COUNT(s.id) AS sessions, '
                                    'MAX(CASE WHEN s.removed = 0 THEN s.start_at END) AS last_at FROM polar_activities a '
                                    'LEFT JOIN polar_sessions s ON s.activity_id = a.id GROUP BY a.id ORDER BY a.position, a.id')]


def _taken(db: sqlite3.Connection, name: str, other_than: int = 0) -> None:
    """Refuse a name that another activity has, in any case."""
    if db.execute('SELECT 1 FROM polar_activities WHERE name = ? AND id != ?', (name, other_than)).fetchone():
        raise ActivityError(f'There is an activity {name} already. Choose another name.')


def add_activity(name: str) -> int:
    """Add an activity at the end of the list; its number."""
    with ledger.connect(write=True) as db:
        _taken(db, name)
        position = db.execute('SELECT COALESCE(MAX(position), -1) + 1 FROM polar_activities').fetchone()[0]
        return db.execute('INSERT INTO polar_activities (name, position) VALUES (?, ?)', (name, position)).lastrowid


def rename_activity(activity_id: int, name: str) -> bool:
    """Give an activity another name; False when there is no such activity."""
    with ledger.connect(write=True) as db:
        _taken(db, name, activity_id)
        return db.execute('UPDATE polar_activities SET name = ? WHERE id = ?', (name, activity_id)).rowcount > 0


def remove_activity(activity_id: int) -> bool:
    """Remove an activity that no session has and that is not the HRV test; False when there is no such activity."""
    with ledger.connect(write=True) as db:
        row = db.execute('SELECT name, test FROM polar_activities WHERE id = ?', (activity_id,)).fetchone()
        if row is None:
            return False
        if row['test']:
            raise ActivityError(f"{row['name']} is the activity of {TEST_NAMES.get(row['test'], 'a test')}. Rename it if you "
                                'want another name.')
        used = db.execute('SELECT COUNT(*) FROM polar_sessions WHERE activity_id = ?', (activity_id,)).fetchone()[0]
        if used:
            raise ActivityError(f"{row['name']} is the activity of {used} session{'' if used == 1 else 's'}. Give those "
                                f"sessions another activity in All sessions, then remove {row['name']}.")
        db.execute('DELETE FROM polar_activities WHERE id = ?', (activity_id,))
        return True


def set_activity(session_id: int, activity_id: int | None) -> bool:
    """Give a session an activity (None: none); False when the session or the activity is not there."""
    with ledger.connect(write=True) as db:
        if activity_id is not None and not db.execute('SELECT 1 FROM polar_activities WHERE id = ?', (activity_id,)).fetchone():
            return False
        return db.execute('UPDATE polar_sessions SET activity_id = ? WHERE id = ?', (activity_id, session_id)).rowcount > 0


def set_note(session_id: int, note: str) -> bool:
    """Give a session a note, without spaces around it (empty: none); False when there is no such session."""
    with ledger.connect(write=True) as db:
        return db.execute('UPDATE polar_sessions SET note = ? WHERE id = ?', (note.strip() or None, session_id)).rowcount > 0


def give_activity(since: str, activity_id: int, measure_s: int | None = None) -> int:
    """Give the logger sessions that ended at or after `since` (UTC ISO, to the second) and have no activity this
    activity, and the test time of an HRV test (`measure_s`): the session of a recording started from the card; how many."""
    with ledger.connect(write=True) as db:
        return db.execute("UPDATE polar_sessions SET activity_id = ?, measure_s = ? WHERE source = 'logger' AND end_at >= ? AND "
                          'activity_id IS NULL AND EXISTS (SELECT 1 FROM polar_activities WHERE id = ?)',
                          (activity_id, measure_s, since, activity_id)).rowcount


def measure_s(session_id: int) -> int | None:
    """The test time (s) chosen at the Start of an HRV test; None for a session recorded before that choice or elsewhere."""
    with ledger.connect() as db:
        row = db.execute('SELECT measure_s FROM polar_sessions WHERE id = ?', (session_id,)).fetchone()
    return row[0] if row else None


def blocks(session_id: int, stream: str) -> list[sqlite3.Row]:
    """The raw blocks of a stream ('ecg' or 'acc') of a session (of all its recordings), in time order."""
    ids = list(owners([session_id]))
    with raw() as db:
        return db.execute(f"SELECT * FROM blocks WHERE session_id IN ({', '.join('?' * len(ids))}) AND stream = ? "
                          'ORDER BY host_ms, sensor_ns', (*ids, stream)).fetchall()


def _chosen(activity: int | None) -> tuple[str, tuple]:
    """The SQL condition and its parameter for the sessions of one activity; nothing for all sessions."""
    return ('', ()) if activity is None else (' AND s.activity_id = ?', (activity,))


def session_points(first: str, last: str, activity: int | None = None) -> list[dict]:
    """Each session that counts and is not an HRV test from day `first` to day `last` (with `activity`, only its sessions),
    in time order, as a point at its start for the charts: its maximum HR and mean HR under their history keys; a value it
    has not is left out."""
    where, chosen = _chosen(activity)
    with ledger.connect() as db:
        rows = db.execute(f'SELECT s.start_at, s.day, s.seconds, s.max_hr, s.mean_hr FROM polar_sessions s WHERE s.day >= ? '
                          f'AND s.day <= ? AND {COUNTED} AND {NOT_HRV}{where} ORDER BY s.start_at', (first, last, *chosen)).fetchall()
    found = []
    for row in rows:
        values = {'h10:max_hr': row['max_hr'], 'h10:mean_hr': row['mean_hr'] if row['seconds'] else None}
        found.append({'at': row['start_at'], 'day': row['day'], **{k: v for k, v in values.items() if v is not None}})
    return found


def exercise_sessions(first: str, last: str) -> list[dict]:
    """The sessions that count and are not HRV tests (a rest) from day `first` to day `last`, in time order: id, day, the
    name of the activity (None without one) and when the session was read (it changes when the session is read again)."""
    with ledger.connect() as db:
        return [dict(r) for r in db.execute('SELECT s.id, s.day, a.name AS activity, s.imported_at FROM polar_sessions s LEFT JOIN '
                                            'polar_activities a ON a.id = s.activity_id WHERE s.day >= ? AND s.day <= ? AND '
                                            f'{COUNTED} AND {NOT_HRV} ORDER BY s.start_at', (first, last))]


def weeks(first: date, last: date, activity: int | None = None) -> list[dict]:
    """The minutes of the sessions that count from `first` to `last` (with `activity`, only its sessions), per week (its
    Monday; every week of the period, oldest first) and per activity name (NO_ACTIVITY for a session without one)."""
    where, chosen = _chosen(activity)
    with ledger.connect() as db:
        rows = db.execute('SELECT s.day, s.seconds, a.name FROM polar_sessions s LEFT JOIN polar_activities a ON '
                          f'a.id = s.activity_id WHERE s.day >= ? AND s.day <= ? AND {COUNTED}{where}',
                          (first.isoformat(), last.isoformat(), *chosen)).fetchall()
    found: dict[str, dict[str, float]] = {}
    monday = first - timedelta(days=first.weekday())
    while monday <= last:
        found[monday.isoformat()] = {}
        monday += timedelta(days=7)
    for day, seconds_long, name in rows:
        when = date.fromisoformat(day)
        week = found[(when - timedelta(days=when.weekday())).isoformat()]
        key = name or NO_ACTIVITY
        week[key] = week.get(key, 0) + seconds_long / 60
    return [{'week': week, 'minutes': {key: round(m) for key, m in by.items()}} for week, by in found.items()]
