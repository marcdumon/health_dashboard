"""Recording from the Polar H10 card with the user's own logger: `uv run polar-h10 record` in logger_project, started as a
process of its own; its API (logger_api) gives the status, takes the markers, and stops it. The logger is
a separate app and knows nothing of this one. A background thread follows each recording that the app knows of (one it
started, or one that a status read found: started in a terminal, or before an app restart) and imports the sessions when
it ends: after Stop, an error, an empty strap battery or a stop in the terminal."""

import os
import signal
import subprocess
import threading
import time
from collections import deque
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from health_dashboard import polar, polar_store, polar_values, store
from health_dashboard.config import ROOT, config

LOG = ROOT / 'data' / 'polar_record.log'
COMMAND = ['uv', 'run', 'polar-h10', 'record']
MARKERS = (polar_values.EFFORT_DONE,)
# the logger opens its API only once the strap is connected, and the scan alone can take 20 s
API_WAIT_S = 60
WATCH_S = 2
# a recording that the app did not start has ended when its API does not answer 3 times in a row
GONE_TRIES = 3
# the activity chosen at Start goes to the logger sessions that end after the start; this margin covers the whole seconds
# of both times and a last HR sample just before the end
SINCE_MARGIN_S = 5
NOT_SET = ('Set the logger folder first: open health_dashboard.toml in the app folder, add logger_project = '
           "'/folder/of/polar_h10_logger' to the [polar] part, and restart the app.")
NOT_STARTED = 'Close the phone app that uses the strap, wet the electrodes, then press Start recording again.'
NOT_RUNNING = 'No recording runs. Press Start recording first.'
# the HR on the card: that of the last 5 beats (60000 / their mean RR), about 1 s sooner than the strap's own HR, which is
# a mean of more beats. A beat outside 280-1700 ms (a missed or an extra beat) is left out, and so are the beats of
# notifications 10 or more before the newest one (no status read saw the ones between: the card was closed)
BEATS = 5
RR_MS = (280, 1700)
RECENT = 10
# the logger's states as the card says them
STATES = {'idle': 'connecting', 'connecting': 'connecting', 'connected': 'recording', 'dropout': 'connection lost',
          'stopping': 'stopping'}
_lock = threading.Lock()


class RecordError(RuntimeError):
    """A recording cannot start, stop or take a marker; the message says what to do."""


class Busy(RecordError):
    """A recording runs already, or the last one is being imported."""


def _fresh() -> dict:
    """The app's side of a recording: its own process and when it started, whether the logger answered, the thread that
    follows it, a stop asked, the import after it, the last marker, the activity and the test time chosen at Start and when
    that was, the last error, the timezone, the number of the last HR notification seen and the last beats (that number, RR
    in ms)."""
    return {'process': None, 'started': 0.0, 'answered': False, 'following': False, 'stopping': False, 'importing': False,
            'last_marker': None, 'activity': None, 'measure_s': None, 'since': '', 'error': '', 'tz': None, 'notice': None,
            'beats': deque(maxlen=BEATS)}


_state = _fresh()


def settings() -> tuple[Path | None, str]:
    """The logger's project folder (None when not set) and the address of its API, from health_dashboard.toml."""
    found = config().polar
    return (Path(found.logger_project) if found.logger_project else None), found.logger_api.rstrip('/')


def _iso(epoch: float) -> str:
    """A time in seconds since 1970 as UTC ISO text, to the ms."""
    return datetime.fromtimestamp(epoch, UTC).isoformat(timespec='milliseconds')


def _iso_s(epoch: float) -> str:
    """A time in seconds since 1970 as UTC ISO text, to the second, as the sessions keep their start and end."""
    return datetime.fromtimestamp(epoch, UTC).isoformat(timespec='seconds')


def _ask(method: str, path: str, body: dict | None = None, timeout: float = 1.0) -> httpx.Response | None:
    """A request to the logger's API; None when it does not answer (no recording, or the strap still connects) or when
    logger_api is not a valid address."""
    try:
        return httpx.request(method, f'{settings()[1]}{path}', json=body, timeout=timeout)
    except (httpx.HTTPError, httpx.InvalidURL):
        return None


def _status() -> dict | None:
    """The logger's status, or None when its API does not answer, or another program on its port answers something else."""
    response = _ask('GET', '/status')
    if response is None or response.status_code != 200:
        return None
    try:
        found = response.json()
    except ValueError:
        return None
    return found if isinstance(found, dict) else None


def _own_running() -> bool:
    """Whether the process that the app started still runs."""
    process = _state['process']
    return process is not None and process.poll() is None


def _end(process: subprocess.Popen) -> None:
    """Stop the app's own logger process and its children (its own process group), by its number."""
    with suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGTERM)


def _log_tail(lines: int = 3) -> str:
    """The last lines of the log of the app's recording, without empty lines."""
    try:
        with LOG.open(encoding='utf-8', errors='replace') as log:
            return ' '.join(line.strip() for line in deque((line for line in log if line.strip()), maxlen=lines))
    except OSError:
        return ''


def _follow(tz: ZoneInfo) -> None:
    """Follow the recording until it ends, then import the sessions. The app's own process is followed until it ends (and
    stopped when its API does not answer within API_WAIT_S); another one until its API does not answer GONE_TRIES times."""
    # any failure frees the recording, so Start works again without an app restart
    try:
        process, last_error, misses = _state['process'], None, 0
        while True:
            answer = _status()
            if answer is not None:
                _state['answered'], misses = True, 0
                last_error = answer.get('error') or last_error
            if process is not None:
                if process.poll() is not None:
                    break
                if not _state['answered'] and not _state['stopping'] and time.monotonic() - _state['started'] > API_WAIT_S:
                    _state['error'] = (f'The logger did not answer at {settings()[1]} within {API_WAIT_S} s, so the app stopped '
                                       'it. Another program may use that port: close it, then press Start recording again. '
                                       f'{_log_tail()}').strip()
                    _end(process)
                    process.wait()
                    break
            elif answer is None:
                misses += 1
                if misses >= GONE_TRIES:
                    break
            time.sleep(WATCH_S)
        if process is not None and not _state['stopping'] and not _state['error']:
            if not _state['answered']:
                _state['error'] = f'The recording did not start: {_log_tail()} {NOT_STARTED}'
            elif process.returncode != 0:
                _state['error'] = f'The recording ended with an error: {last_error or _log_tail()}'
        with _lock:
            _state.update(process=None, importing=True, last_marker=None)
        while not polar.begin():
            time.sleep(WATCH_S)
        # only the folders on the PC: the phone is moved only on the import button
        polar.run_import(tz, phone=False)
        if _state['activity'] is not None:
            polar_store.give_activity(_state['since'], _state['activity'], _state['measure_s'])
    finally:
        with _lock:
            _state.update(importing=False, stopping=False, following=False, answered=False, activity=None, measure_s=None)


def _watch(tz: ZoneInfo) -> None:
    """Start the thread that follows the recording, once."""
    with _lock:
        if _state['following']:
            return
        _state.update(following=True, tz=tz)
    threading.Thread(target=_follow, args=(tz,), daemon=True).start()


def start(tz: ZoneInfo, activity_id: int | None = None, measure_s: int | None = None) -> None:
    """Start `uv run polar-h10 record` in the logger folder as a process of its own (its own process group, so it goes on
    when the app stops), without the app's VIRTUAL_ENV so uv uses the logger's environment; the output goes to
    data/polar_record.log. The activity chosen goes to the session of this recording and is kept as the next default; the
    test time of an HRV test (`measure_s`) goes to the session with it."""
    project, _ = settings()
    if project is None:
        raise RecordError(NOT_SET)
    if _state['importing']:
        raise Busy('The app imports the last session. Press Start recording again when it is done.')
    running = _status() is not None
    with _lock:
        if running or _own_running() or _state['following']:
            raise Busy('A recording runs already. Stop it first.')
        LOG.parent.mkdir(parents=True, exist_ok=True)
        env = {key: value for key, value in os.environ.items() if key != 'VIRTUAL_ENV'}
        try:
            with LOG.open('w') as log:
                process = subprocess.Popen(COMMAND, cwd=project, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                           start_new_session=True, env=env)
        except OSError as error:
            raise RecordError(f'The logger cannot start in {project} ({error.strerror}). Check logger_project in the [polar] '
                              'part of health_dashboard.toml, then restart the app.') from error
        _state.update(process=process, started=time.monotonic(), answered=False, stopping=False, error='', last_marker=None,
                      activity=activity_id, measure_s=measure_s, since=_iso_s(time.time() - SINCE_MARGIN_S))
    store.set_setting('polar_last_activity', None if activity_id is None else str(activity_id))
    _watch(tz)


def stop(tz: ZoneInfo) -> None:
    """Stop the recording: the logger's POST /stop; while the strap still connects (no API yet), the app's own process."""
    response = _ask('POST', '/stop', timeout=5)
    with _lock:
        if response is not None and response.status_code == 202:
            _state['stopping'] = True
        elif _own_running():
            _state['stopping'] = True
            _end(_state['process'])
        else:
            raise RecordError(NOT_RUNNING)
    _watch(tz)


def _marker(label: str) -> None:
    """A marker in the running recording, through the logger's API."""
    at = time.time()
    response = _ask('POST', '/markers', {'label': label, 'source': 'app'}, timeout=5)
    if response is None:
        raise RecordError(NOT_RUNNING)
    if response.status_code != 201:
        try:
            detail = response.json().get('detail')
        except ValueError:
            detail = None
        raise RecordError(detail or f'The logger refused the marker ({response.status_code}).')
    _state['last_marker'] = {'label': label, 'at': _iso(at)}


def mark(label: str) -> None:
    """One of the four markers."""
    if label not in MARKERS:
        raise ValueError(f'"{label}" is not a marker. Choose one of: {", ".join(MARKERS)}.')
    _marker(label)


def _beat_hr(answer: dict | None) -> int | None:
    """The HR of the last beats (see BEATS) from a status of the logger, else the strap's HR; None without a status. Each
    HR notification adds its beats once: the logger counts the notifications. Called under _lock."""
    if answer is None:
        return None
    last = answer.get('last_hr') or {}
    count = ((answer.get('session') or {}).get('samples') or {}).get('hr')
    if count is None or (_state['notice'] is not None and count < _state['notice']):
        # no recording, or a new one: its beats start again
        _state['beats'].clear()
    if count is not None and count != _state['notice']:
        _state['beats'].extend((count, rr) for raw in last.get('rr_raw') or []
                               if RR_MS[0] <= (rr := raw * 1000 / 1024) <= RR_MS[1])
    _state['notice'] = count
    beats = [rr for seen, rr in _state['beats'] if count is not None and count - seen < RECENT]
    return round(60000 * len(beats) / sum(beats)) if beats else last.get('hr_bpm')


def live(tz: ZoneInfo) -> dict:
    """What the card shows of a recording: its state (off, connecting, recording, connection lost, stopping, importing),
    the seconds, the HR of the last beats, the strap battery, the last marker, the last error, and the activity and the test
    time of an HRV test chosen at Start (None for a recording started elsewhere). A recording that this read finds and the
    app does not follow yet is followed from now on, so its session is imported when it ends."""
    project, _ = settings()
    answer = _status()
    if answer is not None:
        _watch(tz)
    with _lock:
        if _state['importing']:
            state = 'importing'
        elif _state['stopping']:
            state = 'stopping'
        elif answer is not None:
            state = STATES.get(answer.get('state'), 'recording')
        elif _own_running():
            state = 'connecting'
        elif _state['following']:
            # the logger has ended and the import has not begun yet: the card waits for the session
            state = 'stopping'
        else:
            state = 'off'
        return {'configured': project is not None, 'state': state, 'seconds': ((answer or {}).get('session') or {}).get('seconds'),
                'hr': _beat_hr(answer),
                'battery': ((answer or {}).get('device') or {}).get('battery'), 'last_marker': _state['last_marker'],
                'error': _state['error'], 'activity': _state['activity'], 'measure_s': _state['measure_s']}
