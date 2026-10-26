"""The dashboard's data in SQLite: data/health.db inside the project.

A connection per operation, WAL journaling and a busy timeout let the app and other scripts write at the same time.
"""

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from health_dashboard.config import DATA_DIR

DB = DATA_DIR / 'health.db'

SCHEMA = '''
CREATE TABLE IF NOT EXISTS health_supplements (
    id INTEGER PRIMARY KEY AUTOINCREMENT, moment TEXT NOT NULL, moment_when TEXT NOT NULL, position INTEGER NOT NULL,
    name TEXT NOT NULL, dose TEXT NOT NULL, how TEXT NOT NULL DEFAULT '', why TEXT NOT NULL DEFAULT '',
    effects TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS health_intakes (
    day TEXT NOT NULL, supplement_id INTEGER NOT NULL REFERENCES health_supplements (id) ON DELETE CASCADE,
    taken_at TEXT NOT NULL, PRIMARY KEY (day, supplement_id)
);
CREATE TABLE IF NOT EXISTS health_supplement_list (
    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, unit TEXT NOT NULL, per_day INTEGER, position INTEGER NOT NULL,
    added TEXT, removed TEXT
);
CREATE TABLE IF NOT EXISTS health_units (
    id INTEGER PRIMARY KEY, day TEXT NOT NULL, supplement_id INTEGER NOT NULL REFERENCES health_supplement_list (id),
    taken_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS health_units_by_day ON health_units (day, taken_at);
CREATE TABLE IF NOT EXISTS health_plan (
    supplement_id INTEGER NOT NULL REFERENCES health_supplement_list (id), moment TEXT NOT NULL, from_day TEXT NOT NULL,
    amount REAL NOT NULL, PRIMARY KEY (supplement_id, moment, from_day)
);
CREATE TABLE IF NOT EXISTS health_angina (day TEXT PRIMARY KEY, score INTEGER, note TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS health_ketones (id INTEGER PRIMARY KEY, day TEXT NOT NULL, taken_at TEXT NOT NULL, level REAL NOT NULL);
CREATE INDEX IF NOT EXISTS health_ketones_by_day ON health_ketones (day, taken_at);
CREATE TABLE IF NOT EXISTS health_tape (day TEXT NOT NULL, site TEXT NOT NULL, cm REAL NOT NULL, PRIMARY KEY (day, site));
CREATE TABLE IF NOT EXISTS health_food (
    day TEXT PRIMARY KEY, first_food TEXT, last_food TEXT, amount INTEGER, protein INTEGER, carbs INTEGER, fat INTEGER,
    alcohol INTEGER NOT NULL DEFAULT 0, salt INTEGER NOT NULL DEFAULT 0, fast INTEGER NOT NULL DEFAULT 0, kcal REAL,
    protein_g REAL, carbs_g REAL, fat_g REAL
);
CREATE TABLE IF NOT EXISTS withings_values (
    grpid INTEGER NOT NULL, type INTEGER NOT NULL, position INTEGER NOT NULL, value REAL NOT NULL, measured_at TEXT NOT NULL,
    PRIMARY KEY (grpid, type, position)
);
CREATE INDEX IF NOT EXISTS withings_values_by_type ON withings_values (type, position, measured_at);
CREATE INDEX IF NOT EXISTS withings_values_by_time ON withings_values (measured_at);
CREATE TABLE IF NOT EXISTS withings_hidden (grpid INTEGER PRIMARY KEY, hidden_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS health_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS polar_sessions (
    id INTEGER PRIMARY KEY, source TEXT NOT NULL, serial TEXT NOT NULL, path TEXT NOT NULL, start_at TEXT NOT NULL,
    end_at TEXT NOT NULL, day TEXT NOT NULL, kind TEXT NOT NULL, seconds INTEGER NOT NULL, mean_hr REAL, max_hr REAL,
    recovery REAL, recovery_state TEXT, rest_hr REAL, rmssd REAL, wrong_beats_pct REAL, copy_of INTEGER,
    removed INTEGER NOT NULL DEFAULT 0, imported_at TEXT NOT NULL, UNIQUE (source, serial, start_at)
);
CREATE INDEX IF NOT EXISTS polar_sessions_by_day ON polar_sessions (day, start_at);
CREATE TABLE IF NOT EXISTS polar_files (path TEXT PRIMARY KEY, size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL, session_id INTEGER);
CREATE TABLE IF NOT EXISTS polar_beats (
    session_id INTEGER NOT NULL, t_ms INTEGER NOT NULL, rr_ms REAL NOT NULL, clean INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS polar_beats_by_session ON polar_beats (session_id, t_ms);
CREATE TABLE IF NOT EXISTS polar_seconds (
    session_id INTEGER NOT NULL, t INTEGER NOT NULL, hr REAL, movement REAL, PRIMARY KEY (session_id, t)
);
CREATE TABLE IF NOT EXISTS polar_markers (
    session_id INTEGER NOT NULL, t_ms INTEGER NOT NULL, label TEXT NOT NULL, hr REAL, recovery REAL, recovery_state TEXT
);
CREATE INDEX IF NOT EXISTS polar_markers_by_session ON polar_markers (session_id, t_ms);
CREATE TABLE IF NOT EXISTS polar_activities (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE, position INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS sleep_records (
    id INTEGER PRIMARY KEY, tz TEXT NOT NULL, start_at TEXT NOT NULL, end_at TEXT NOT NULL, day TEXT NOT NULL,
    in_bed_min REAL NOT NULL, sleep_min REAL NOT NULL, awake_min REAL, deep_min REAL, light_min REAL, rem_min REAL,
    cycles INTEGER
);
CREATE INDEX IF NOT EXISTS sleep_records_by_day ON sleep_records (day, start_at);
CREATE TABLE IF NOT EXISTS fitbit_days (
    day TEXT PRIMARY KEY, stamp TEXT NOT NULL, vals TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS fitbit_exercises (
    start_at TEXT PRIMARY KEY, end_at TEXT NOT NULL, day TEXT NOT NULL, name TEXT NOT NULL, active_min REAL, mean_hr REAL,
    kcal REAL, azm REAL
);
CREATE INDEX IF NOT EXISTS fitbit_exercises_by_day ON fitbit_exercises (day);
'''

_ready: set[Path] = set()
_ready_lock = threading.Lock()


# columns added after a table first shipped: (table, column, type); a database made before gets them on start-up
ADDED_COLUMNS = [('health_angina', 'not_measured', 'INTEGER NOT NULL DEFAULT 0'),
                 ('polar_sessions', 'activity_id', 'INTEGER'), ('polar_sessions', 'note', 'TEXT'),
                 ('polar_activities', 'test', 'TEXT'), ('polar_sessions', 'measure_s', 'INTEGER'),
                 ('health_supplement_list', 'dose_unit', 'TEXT'), ('health_supplement_list', 'step', 'REAL'),
                 ('health_units', 'amount', 'REAL'),
                 ('health_units', 'moment', 'TEXT')]
# the supplements had a why and an effects column from 2026-10-24 to their removal the same day: an older database keeps
# them, unused
# the split of the Polar H10 sessions in movement and still (removed on 2026-10-21) had the columns moving_s, still_s,
# moving_hr and still_hr of polar_sessions and no_split of polar_activities: an older database keeps them, unused
# the first activities of the Polar H10 sessions, given once: when the sessions get their activity column (in a new
# database too), so a default that the user removed does not come back
ACTIVITIES = ('Walk', 'Gym cardio', 'Weights', 'Bike', 'Rest')


def _ensure_schema(target: Path) -> None:
    """Create the tables once per database file per process."""
    with _ready_lock:
        if target in _ready:
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(target, timeout=30)
        try:
            connection.execute('PRAGMA journal_mode=WAL')
            connection.executescript(SCHEMA)
            for table, column, kind in ADDED_COLUMNS:
                if column not in {row[1] for row in connection.execute(f'PRAGMA table_info({table})')}:
                    connection.execute(f'ALTER TABLE {table} ADD COLUMN {column} {kind}')
                    if (table, column) == ('polar_sessions', 'activity_id'):
                        connection.executemany('INSERT OR IGNORE INTO polar_activities (name, position) VALUES (?, ?)',
                                               [(name, n) for n, name in enumerate(ACTIVITIES)])
                        connection.commit()
                    if (table, column) == ('polar_activities', 'test'):
                        # the HRV test: an activity named HRV gets the flag, else the activity is added at the end, once
                        if not connection.execute("UPDATE polar_activities SET test = 'hrv' WHERE name = 'HRV'").rowcount:
                            connection.execute("INSERT INTO polar_activities (name, position, test) VALUES ('HRV', "
                                               "(SELECT COALESCE(MAX(position), -1) + 1 FROM polar_activities), 'hrv')")
                        connection.commit()
            # the HR recovery test (2026-10-17): an activity named HR recovery gets the flag, else the activity is added at
            # the end; once, as a test activity cannot be removed
            if not connection.execute("SELECT 1 FROM polar_activities WHERE test = 'recovery'").fetchone():
                if not connection.execute("UPDATE polar_activities SET test = 'recovery' WHERE name = 'HR recovery'").rowcount:
                    connection.execute("INSERT INTO polar_activities (name, position, test) VALUES ('HR recovery', "
                                       "(SELECT COALESCE(MAX(position), -1) + 1 FROM polar_activities), 'recovery')")
                connection.commit()
            # the stairs test (2026-10-17): the same, for an activity named Stairs test
            if not connection.execute("SELECT 1 FROM polar_activities WHERE test = 'stairs'").fetchone():
                if not connection.execute("UPDATE polar_activities SET test = 'stairs' WHERE name = 'Stairs test'").rowcount:
                    connection.execute("INSERT INTO polar_activities (name, position, test) VALUES ('Stairs test', "
                                       "(SELECT COALESCE(MAX(position), -1) + 1 FROM polar_activities), 'stairs')")
                connection.commit()
            # the first Withings table had no body part, so the values per arm, leg and trunk did not fit: it goes,
            # and the next fetch gets the whole history again (Withings keeps it); the sign-in stays
            if connection.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'withings_measures'").fetchone():
                connection.execute('DROP TABLE withings_measures')
                connection.execute("DELETE FROM health_settings WHERE key = 'withings_lastupdate'")
                connection.commit()
        finally:
            connection.close()
        _ready.add(target)


@contextmanager
def connect(write: bool = False) -> Iterator[sqlite3.Connection]:
    """A connection; a writing one takes the write lock up front, so a second writer waits instead of failing.

    Commits on success, rolls back on error.
    """
    _ensure_schema(DB)
    # autocommit mode, transactions started by hand: a deferred transaction that upgrades from read to write
    # fails at once with 'database is locked' when another writer is busy, without waiting for the timeout
    connection = sqlite3.connect(DB, timeout=30, isolation_level=None)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute('PRAGMA foreign_keys=ON')
        connection.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
        try:
            yield connection
        except BaseException:
            connection.execute('ROLLBACK')
            raise
        connection.execute('COMMIT')
    finally:
        connection.close()
