"""The sessions of the Polar H10 chest strap, read from two folders: the files of the phone app (Polar Sensor Logger) and
the sessions of the user's own logger on the PC. Only on an import: the button Import from phone and PC (Import from PC
without phone_ftp), or the end of a recording on the PC, which reads only the folders on the PC. A page read never reads
the folders, and the files on the PC are never changed. The button first moves the new files of the phone app from the
phone to the phone folder (polar_phone). A file is read once; a new or changed file reads its session again. The files
are read in parts of 100,000 lines, so a file of 1 GB needs little memory. The small results go to the app's database and
the raw ECG and acceleration to data/polar.db (polar_store)."""

import csv
import itertools
import json
import logging
import re
import sqlite3
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

from health_dashboard import polar_phone, polar_store, polar_values, store
from health_dashboard.config import config

log = logging.getLogger(__name__)
# the version of the calculations: a new version reads every session again, so the stored values follow the new rules
CALC_VERSION = 6
PHONE_FILE = re.compile(r'^Polar_H10_(?P<serial>[0-9A-Z]+)_(?P<start>\d{8}_\d{6})_(?P<kind>HR|RR|ECG|ACC)\.txt$')
# the marker file of the phone app, named after its first press; its labels as the app stores them
PHONE_MARKER_FILE = re.compile(r'^MARKER_(?P<start>\d{8}_\d{6})\.txt$')
PHONE_LABELS = {'MARKER_START': polar_values.PHONE_START, 'MARKER_STOP': polar_values.PHONE_STOP}
STREAMS = ('hr', 'rr', 'ecg', 'acc')
# the files of one phone session start a few seconds apart
SAME_SESSION_S = 60
MIN_SESSION_S = 120
# the logger takes markers only while it records: a marker more than an hour from the HR of its session is a broken row
MARKER_MARGIN_S = 3600
# the labels of buttons that were removed, whose old markers are left out: Cooldown 60 s (2026-10-16: a cooldown at the end
# of a session measured no effort), Angina onset and Angina stops (2026-10-17: angina is noted once a day), Exercise start
# and Exercise stop (2026-10-17: useless; the HR recovery test has Effort done)
OLD_LABELS = ('cooldown', 'angina onset', 'angina stops', 'exercise start', 'exercise stop')
PART_ROWS = 100_000
# per source and stream: the column of the sensor time (None: none) and the columns of the values; column 0 is the time
LAYOUT = {('phone', 'hr'): (None, [1]), ('phone', 'rr'): (None, [1]), ('phone', 'ecg'): (1, [3]), ('phone', 'acc'): (1, [2, 3, 4]),
          ('logger', 'hr'): (None, [1]), ('logger', 'rr'): (None, [2]), ('logger', 'ecg'): (1, [2]),
          ('logger', 'acc'): (1, [2, 3, 4])}
NOT_SET = ('Set the two Polar folders first: open health_dashboard.toml in the app folder, add a [polar] part with '
           'phone_dir and logger_dir (the two folders), and restart the app.')
_running = threading.Lock()
# the progress of an import in files: first the copy from the phone (phase 'copy'), then the reading ('read')
_progress = {'phase': 'read', 'done': 0, 'total': 0, 'saved': 0}


class PolarError(RuntimeError):
    """A folder cannot be read; the message says what to do."""


class PartlyRead(PolarError):
    """Some sessions could not be read: the others are stored, and these are tried again at the next import."""


@dataclass
class Found:
    """A session in a folder, not read yet: its source, strap, path (the phone HR file or the logger folder), the file of
    each stream it has, the logger's session.json, and its markers (the logger's markers.csv or the phone's MARKER file)."""

    source: str
    serial: str
    path: Path
    files: dict[str, Path]
    info: Path | None = None
    markers: Path | None = None

    @property
    def all_files(self) -> list[Path]:
        """Every file of the session; a change in one reads the session again."""
        return [*self.files.values(), *([self.info] if self.info else []), *([self.markers] if self.markers else [])]


@dataclass
class Part:
    """Some rows of a stream: their UTC time in ms, their sensor time in ns (ECG and acceleration) and their values."""

    at_ms: np.ndarray
    sensor_ns: np.ndarray | None
    values: np.ndarray


def phone_start(name: str) -> datetime | None:
    """The start in the name of a file of the phone app (a file of a stream of a session, or a marker file: its first press),
    in the time of the phone; None for any other file."""
    found = PHONE_FILE.match(name) or PHONE_MARKER_FILE.match(name)
    return datetime.strptime(found['start'], '%Y%m%d_%H%M%S') if found else None


def button() -> str:
    """The name of the import button: where it imports from."""
    return 'Import from PC' if polar_phone.address() is None else 'Import from phone and PC'


def folders() -> tuple[Path | None, Path | None]:
    """The phone folder and the logger folder of health_dashboard.toml; None for one not set."""
    settings = config().polar
    return (Path(settings.phone_dir) if settings.phone_dir else None, Path(settings.logger_dir) if settings.logger_dir else None)


def phone_sessions(folder: Path) -> list[Found]:
    """The phone sessions: each HR file with the RR, ECG and acceleration files of the same strap that start within 60 s
    of it (the nearest, each file once); any of these can be missing. A marker file belongs to the session whose HR file
    started last before its first press."""
    named = [(m['serial'], datetime.strptime(m['start'], '%Y%m%d_%H%M%S'), m['kind'].lower(), path)
             for path in folder.glob('Polar_H10_*.txt') if (m := PHONE_FILE.match(path.name))]
    used: set[Path] = set()
    found = []
    for serial, start, _, path in sorted(n for n in named if n[2] == 'hr'):
        files = {'hr': path}
        for kind in ('rr', 'ecg', 'acc'):
            near = sorted((abs((s - start).total_seconds()), str(p), p) for se, s, k, p in named
                          if se == serial and k == kind and p not in used and abs((s - start).total_seconds()) <= SAME_SESSION_S)
            if near:
                files[kind] = near[0][2]
                used.add(near[0][2])
        found.append(Found('phone', serial, path, files))
    starts = [(datetime.strptime(PHONE_FILE.match(f.path.name)['start'], '%Y%m%d_%H%M%S'), f) for f in found]
    for path in sorted(folder.glob('MARKER_*.txt')):
        if (m := PHONE_MARKER_FILE.match(path.name)) is None:
            continue
        first = datetime.strptime(m['start'], '%Y%m%d_%H%M%S')
        if before := [f for start, f in starts if start <= first]:
            before[-1].markers = path
    return found


def logger_sessions(folder: Path) -> list[Found]:
    """The logger sessions that ended: a folder whose session.json has an end, with hr.csv (or hr.csv.txt). A session.json
    that is still being written waits for the next import."""
    found = []
    for info_file in sorted(folder.glob('*/session.json')):
        try:
            info = json.loads(info_file.read_text())
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if not isinstance(info, dict) or not info.get('end'):
            continue
        files = {}
        for kind in STREAMS:
            name = next((n for n in (f'{kind}.csv', f'{kind}.csv.txt') if (info_file.parent / n).is_file()), None)
            if name:
                files[kind] = info_file.parent / name
        markers = next((info_file.parent / n for n in ('markers.csv', 'markers.csv.txt') if (info_file.parent / n).is_file()),
                       None)
        if 'hr' in files:
            found.append(Found('logger', (info.get('device') or {}).get('serial', ''), info_file.parent, files, info_file, markers))
    return found


def phone_ms(stamps: list[str], tz: ZoneInfo) -> np.ndarray:
    """UTC ms of phone clock times (local, without a zone): each with the offset of the zone at its own time; in the
    repeated hour of autumn, the first one."""
    local = np.array(stamps, dtype='datetime64[ms]').astype(np.int64)

    def offset(stamp: str) -> int:
        """The offset of the zone at a clock time, in ms."""
        return int(datetime.fromisoformat(stamp).replace(tzinfo=tz).utcoffset().total_seconds() * 1000)

    first, last = offset(stamps[0]), offset(stamps[-1])
    if first == last:
        return local - first
    return local - np.array([offset(s) for s in stamps], dtype=np.int64)


def _rows(path: Path) -> Iterator[list[list[str]]]:
    """The data rows of a file in parts of 100,000, each row split at ';' (the phone app) or ',' (the logger); the header
    line is left out."""
    with path.open(encoding='utf-8', errors='replace') as lines:
        sep = ';' if ';' in next(lines, '') else ','
        part = []
        for line in lines:
            part.append(line.rstrip('\r\n').split(sep))
            if len(part) == PART_ROWS:
                yield part
                part = []
        if part:
            yield part


def _numbers(texts: tuple[str, ...]) -> np.ndarray:
    """Numbers written as text; a decimal comma is read as a point."""
    try:
        return np.asarray(texts).astype(float)
    except ValueError:
        return np.asarray([t.replace(',', '.') for t in texts]).astype(float)


def _part(rows: list[list[str]], source: str, sensor: int | None, tz: ZoneInfo) -> Part:
    """Rows of the wanted fields (the time, the sensor time when there is one, the values) as a part; ValueError or
    OverflowError when a field is not a time or a number."""
    table = list(zip(*rows, strict=True))
    at = phone_ms(list(table[0]), tz) if source == 'phone' else np.asarray(table[0]).astype(np.int64) // 1_000_000
    first = 1 if sensor is None else 2
    return Part(at, None if sensor is None else np.asarray(table[1]).astype(np.int64),
                np.column_stack([_numbers(column) for column in table[first:]]))


def _readable(row: list[str], source: str, sensor: int | None, tz: ZoneInfo) -> bool:
    """Whether every wanted field of a row is a time or a number."""
    try:
        _part([row], source, sensor, tz)
    except (ValueError, OverflowError):
        return False
    return True


def _parts(found: Found, stream: str, tz: ZoneInfo) -> Iterator[Part]:
    """A stream of a session in parts. A row with an empty, missing or unreadable field is left out: a phone HR row can stop
    after the HR, the last line of a file can be cut off, and a line can be damaged."""
    sensor, columns = LAYOUT[(found.source, stream)]
    wanted = [0, *([sensor] if sensor is not None else []), *columns]
    need = max(wanted)
    for part in _rows(found.files[stream]):
        rows = [[row[i] for i in wanted] for row in part if len(row) > need and all(row[i] for i in wanted)]
        if not rows:
            continue
        try:
            read = _part(rows, found.source, sensor, tz)
        except (ValueError, OverflowError):
            rows = [row for row in rows if _readable(row, found.source, sensor, tz)]
            if not rows:
                continue
            read = _part(rows, found.source, sensor, tz)
        yield read


def _markers(path: Path | None) -> list[tuple[int, str]]:
    """The markers of a logger session (markers.csv: host_ns,label,source) as (UTC ms, label), in time order; a row whose
    time is not a number or whose label is empty is left out, and so is an old press of Cooldown 60 s (OLD_LABELS). A file
    the csv reader refuses stops this session only."""
    if path is None:
        return []
    with path.open(encoding='utf-8', errors='replace', newline='') as lines:
        try:
            rows = list(itertools.islice(csv.reader(lines), 1, None))
        except csv.Error as error:
            raise ValueError(f'{path.name}: {error}') from error
    return sorted((int(row[0]) // 1_000_000, row[1]) for row in rows
                  if len(row) >= 2 and row[0].isdigit() and row[1] and row[1] not in OLD_LABELS)


def _phone_markers(path: Path | None, tz: ZoneInfo) -> list[tuple[int, str]]:
    """The presses of the phone marker (its file: phone time;MARKER_START or MARKER_STOP) as (UTC ms, PHONE_START or
    PHONE_STOP), in time order; a row with another label or a time that does not parse is left out."""
    if path is None:
        return []
    rows = [line.split(';') for line in path.read_text(encoding='utf-8', errors='replace').splitlines()[1:]]
    rows = [(row[0].strip(), PHONE_LABELS[row[1].strip()]) for row in rows if len(row) >= 2 and row[1].strip() in PHONE_LABELS]
    found = []
    for stamp, label in rows:
        try:
            found.append((int(phone_ms([stamp], tz)[0]), label))
        except ValueError:
            continue
    return sorted(found)


def _utc(ms: int) -> str:
    """A UTC time in ms as ISO text."""
    return datetime.fromtimestamp(ms / 1000, UTC).isoformat(timespec='seconds')


def _read(found: Found, tz: ZoneInfo, step: Callable[[], None]) -> tuple[dict, list, list, list, list] | None:
    """One session read from its files: its row, its seconds, its beats, its raw blocks and its markers (those within an
    hour of its HR); None when it is shorter than 2 minutes. `step` is called after each file."""
    hr_parts = list(_parts(found, 'hr', tz))
    step()
    if not hr_parts:
        return None
    hr_at = np.concatenate([p.at_ms for p in hr_parts])
    bpm = np.concatenate([p.values[:, 0] for p in hr_parts])
    order = np.argsort(hr_at, kind='stable')
    hr_at, bpm = hr_at[order], bpm[order]
    start_ms, end_ms = int(hr_at[0]), int(hr_at[-1])
    if end_ms - start_ms < MIN_SESSION_S * 1000:
        return None
    seconds = (end_ms - start_ms) // 1000 + 1
    hr = polar_values.per_second_hr(hr_at, bpm, start_ms, seconds)
    rr_at, rr = np.zeros(0, dtype=np.int64), np.zeros(0)
    if 'rr' in found.files:
        rr_parts = list(_parts(found, 'rr', tz))
        step()
        if rr_parts:
            rr_at = np.concatenate([p.at_ms for p in rr_parts])
            rr = np.concatenate([p.values[:, 0] for p in rr_parts])
    clean = polar_values.clean_beats(rr)
    movement, blocks = None, []
    for stream in ('acc', 'ecg'):
        if stream not in found.files:
            continue
        cutter = polar_values.Blocks(stream)
        moves = polar_values.Movement(start_ms, seconds)
        for part in _parts(found, stream, tz):
            cutter.add(part.at_ms, part.sensor_ns, np.rint(part.values).astype(np.int64))
            if stream == 'acc':
                moves.add(part.at_ms, part.values)
        blocks += cutter.close()
        step()
        if stream == 'acc' and not np.isnan(values := moves.values()).all():
            movement = values
    margin = MARKER_MARGIN_S * 1000
    markers = _phone_markers(found.markers, tz) if found.source == 'phone' else _markers(found.markers)
    near = [m for m in markers if start_ms - margin <= m[0] <= end_ms + margin]
    marks = polar_values.marker_values(hr, start_ms, near)
    row = {'source': found.source, 'serial': found.serial, 'path': str(found.path), 'start_at': _utc(start_ms),
           'end_at': _utc(end_ms), 'day': store.health_day(datetime.fromtimestamp(start_ms / 1000, tz)).isoformat(),
           'kind': 'exercise', 'seconds': seconds, 'mean_hr': round(float(bpm.mean()), 1), 'max_hr': float(bpm.max()),
           'rest_hr': None, 'rmssd': None, 'wrong_beats_pct': None,
           'imported_at': datetime.now(UTC).isoformat(timespec='seconds')}
    second_rows = [(start_ms // 1000 + n, None if np.isnan(hr[n]) else round(float(hr[n]), 1),
                    None if movement is None or np.isnan(movement[n]) else round(float(movement[n]), 1)) for n in range(seconds)]
    beat_rows = [(int(t), float(v), int(c)) for t, v, c in zip(rr_at, rr, clean, strict=True)]
    return row, second_rows, beat_rows, blocks, marks


def _stamp(path: Path) -> tuple[int, int]:
    """The size and the change time (ns) of a file."""
    info = path.stat()
    return info.st_size, info.st_mtime_ns


def _todo(phone_dir: Path | None, logger_dir: Path | None) -> list[Found]:
    """The sessions of the two folders that the next import reads: those with a new or changed file, or every session
    after a change of the calculations."""
    found = [*(phone_sessions(phone_dir) if phone_dir else []), *(logger_sessions(logger_dir) if logger_dir else [])]
    known = polar_store.known_files() if store.setting('polar_calc_version') == str(CALC_VERSION) else {}
    return [f for f in found if any(known.get(str(p)) != _stamp(p) for p in f.all_files)]


def waiting() -> dict:
    """How many sessions the next import reads, and whether that is because the calculations changed. It only
    lists the folders and compares them with the files read before; a folder that is not there has no sessions."""
    phone, logger = (folder if folder is not None and folder.is_dir() else None for folder in folders())
    recalc = store.setting('polar_calc_version') not in (None, str(CALC_VERSION))
    return {'sessions': len(_todo(phone, logger)), 'recalc': recalc}


def import_all(phone_dir: Path | None, logger_dir: Path | None, tz: ZoneInfo) -> int:
    """Read the new and changed sessions of the two folders; the number of sessions read. Copies are marked after."""
    for folder in (phone_dir, logger_dir):
        if folder is not None and not folder.is_dir():
            raise PolarError(f'The folder {folder} does not exist. Check phone_dir and logger_dir in the [polar] part of '
                             'health_dashboard.toml, then restart the app.')
    # after a change of the calculations every session is read again
    todo = _todo(phone_dir, logger_dir)
    _progress.update(done=0, total=sum(len(f.files) for f in todo), saved=0)

    def step() -> None:
        """One more file read."""
        _progress['done'] += 1

    count, problems = 0, []
    for session in todo:
        before = _progress['done']
        try:
            read = _read(session, tz, step)
            files = [(str(p), *_stamp(p)) for p in session.all_files]
            if read is None:
                polar_store.remember_files(files)
            else:
                row, second_rows, beat_rows, blocks, marks = read
                polar_store.save(row, second_rows, beat_rows, blocks, files, marks)
                count += 1
                _progress['saved'] = count
        except (OSError, ValueError, OverflowError, sqlite3.Error) as error:
            # this session waits for the next import; the others are read
            log.warning('Polar session %s not read: %s', session.path, error)
            problems.append(f'{session.path.name}: {error}')
        _progress['done'] = before + len(session.files)
    polar_store.mark_copies()
    store.set_setting('polar_calc_version', str(CALC_VERSION))
    if problems:
        more = f' and {len(problems) - 3} more' if len(problems) > 3 else ''
        raise PartlyRead(f"{len(problems)} of {len(todo)} sessions could not be read: {'; '.join(problems[:3])}{more}. Check "
                         'or remove these files in the folder; the next import tries them again.')
    return count


def begin() -> bool:
    """Take the import for one run; False when an import runs already. run_import gives it back."""
    return _running.acquire(blocking=False)


def _from_phone(phone_dir: Path | None) -> tuple[str, str, str]:
    """Move the new files of the phone app from the phone into the phone folder, when phone_ftp is set: where the import
    reads from, with what the move did; why the phone was not read, with what to do (''); and what went wrong in the move
    (''). Without the phone the import goes on: it reads the files on the PC. A phone that cannot be read (its FTP server
    is often off) is no import error: only the page that pressed the button shows why."""
    found = polar_phone.address()
    if found is None or phone_dir is None or not phone_dir.is_dir():
        return 'from the PC', '', ''
    _progress.update(phase='copy', done=0, total=0, saved=0)
    try:
        moved = polar_phone.move_new(found, phone_dir, phone_start, lambda done, total: _progress.update(done=done, total=total))
        return f'from the phone and the PC: {moved.note()}', '', moved.problem
    except polar_phone.PhoneError as error:
        return 'from the PC only: no files moved from the phone', f'{error} The files on the PC are imported.', ''
    finally:
        _progress['phase'] = 'read'


def _imported(note: str) -> None:
    """Keep the time of the import that ended, and where it read from."""
    store.set_setting('polar_last_import', datetime.now(UTC).isoformat(timespec='seconds'))
    store.set_setting('polar_last_note', note)


def run_import(tz: ZoneInfo, phone: bool = True) -> None:
    """The import, in the background after begin(): with `phone` (the button) the move from the phone first, then the
    reading of the folders on the PC; without it (the end of a recording on the PC) only the reading. A failure is kept for
    the page."""
    note, unread, missed = 'from the PC', '', ''
    try:
        phone_dir, logger = folders()
        if phone:
            note, unread, missed = _from_phone(phone_dir)
        store.set_setting('polar_phone_error', unread or None)
        import_all(phone_dir, logger, tz)
        _imported(note)
        store.set_setting('polar_error', missed or None)
    except PartlyRead as problem:
        _imported(note)
        store.set_setting('polar_error', ' '.join(filter(None, [missed, str(problem)])))
    except PolarError as error:
        store.set_setting('polar_error', f'The import stopped: {error}')
    except Exception as error:
        # any other failure still reaches the page, and the next press tries again
        log.exception('the Polar import failed')
        store.set_setting('polar_error', f'The import stopped because of an error in the app ({error!r}); the app log has the '
                                         f'details. Press {button()} to try again.')
    finally:
        _running.release()


def status() -> dict:
    """What the page says about the import: set up, the name of the import button (from phone and PC when the phone is
    set: the button moves its files first), running with its progress in files (moving from the phone or reading), the
    sessions saved by it, the last import and where it read from, a failure, why the phone was not read in the last
    import (for the page that pressed the button), the number of sessions."""
    phone, logger = folders()
    return {'configured': bool(phone or logger), 'button': button(), 'running': _running.locked(),
            'phase': _progress['phase'], 'done': _progress['done'], 'total': _progress['total'], 'saved': _progress['saved'],
            'last_import': store.setting('polar_last_import'), 'last_note': store.setting('polar_last_note') or '',
            'error': store.setting('polar_error') or '', 'phone_error': store.setting('polar_phone_error') or '',
            'sessions': polar_store.count()}
