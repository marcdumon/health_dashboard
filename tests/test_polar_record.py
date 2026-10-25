"""Recording from the Polar H10 card with a fake logger in place of `uv run polar-h10 record` (tests/fake_polar_logger.py,
its own process on a free port): start, live values, markers, stop and the import; a start that fails, a
logger without its API, a stop while the strap connects, a second start, a recording the app did not start, and an end
with an error. No strap and no real logger are used."""

import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from health_dashboard import api, polar, polar_phone, polar_record, polar_store, store

TZ = ZoneInfo('Europe/Brussels')
FAKE = Path(__file__).with_name('fake_polar_logger.py')


@dataclass
class Fake:
    """The fake logger of a test: its port, the folder of its sessions, and the mode of the next start."""

    port: int
    folder: Path
    use: Callable[[str], None]

    def command(self, mode: str) -> list[str]:
        """The command line of the fake logger in a mode."""
        return [sys.executable, str(FAKE), mode, str(self.port), str(self.folder)]


def free_port() -> int:
    """A port no program listens on now."""
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


def until(check: Callable[[], bool], seconds: float = 15) -> None:
    """Wait until check() is true; fail after `seconds`."""
    end = time.monotonic() + seconds
    while not check():
        assert time.monotonic() < end, 'the state did not come'
        time.sleep(0.02)


def state() -> str:
    """The state of the recording on the card."""
    return polar_record.live(TZ)['state']


@pytest.fixture
def fake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Fake]:
    """The fake logger in mode 'ok' in place of the real one, its sessions imported from tmp/logger, short waits; its
    process is stopped at the end of the test."""
    folder = tmp_path / 'logger'
    folder.mkdir()
    port = free_port()
    monkeypatch.setattr(polar_record, 'settings', lambda: (tmp_path, f'http://127.0.0.1:{port}'))
    monkeypatch.setattr(polar, 'folders', lambda: (None, folder))
    monkeypatch.setattr(polar_record, 'WATCH_S', 0.05)
    found = Fake(port, folder, lambda mode: monkeypatch.setattr(polar_record, 'COMMAND', found.command(mode)))
    found.use('ok')
    yield found
    process = polar_record._state['process']
    if process is not None and process.poll() is None:
        process.kill()
        process.wait()
    until(lambda: not polar_record._state['following'])


def test_start_marks_and_stop_give_an_imported_session_with_its_markers(fake: Fake) -> None:
    assert polar_record.live(TZ) | {'error': ''} == {'configured': True, 'state': 'off', 'seconds': None, 'hr': None,
                                                     'battery': None, 'last_marker': None, 'error': '', 'activity': None,
                                                     'measure_s': None}
    polar_record.start(TZ)
    until(lambda: state() == 'recording')
    live = polar_record.live(TZ)
    assert (live['hr'], live['battery'], live['seconds'] > 170) == (128, 80, True)
    polar_record.mark('effort done')
    assert polar_record.live(TZ)['last_marker']['label'] == 'effort done'
    polar_record.stop(TZ)
    until(lambda: state() == 'off')
    (session,) = polar_store.sessions(0, 5)['sessions']
    assert [m['label'] for m in session['markers']] == ['effort done'] and session['source'] == 'logger'
    assert polar_record.live(TZ)['error'] == '' and polar_record.live(TZ)['last_marker'] is None


def test_the_import_after_a_recording_reads_only_the_folders_on_the_pc(fake: Fake, tmp_path: Path,
                                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    phone = tmp_path / 'phone'
    phone.mkdir()
    monkeypatch.setattr(polar, 'folders', lambda: (phone, fake.folder))
    monkeypatch.setattr(polar_phone, 'address', lambda: polar_phone.Address('192.168.1.5', 2221, 'md', 'secret', '/'))
    reached: list[str] = []

    class Phone:
        """An FTP connection that only notes that the phone was reached."""

        def __enter__(self) -> 'Phone':
            return self

        def __exit__(self, *exc: object) -> None:
            return None

        def connect(self, host: str, port: int, timeout: float) -> None:
            reached.append(host)
            raise TimeoutError('timed out')

    monkeypatch.setattr(polar_phone, 'FTP', Phone)
    polar_record.start(TZ)
    until(lambda: state() == 'recording')
    polar_record.stop(TZ)
    until(lambda: state() == 'off')
    assert (reached, polar.status()['last_note'], polar.status()['error']) == ([], 'from the PC', '')
    assert polar_store.count() == 1


def test_the_live_hr_is_that_of_the_last_5_beats_each_beat_counted_once(monkeypatch: pytest.MonkeyPatch) -> None:
    answers: list[dict] = []
    monkeypatch.setattr(polar_record, '_status', lambda: answers[-1])
    monkeypatch.setattr(polar_record, '_watch', lambda tz: None)

    def notice(count: int, *rr_ms: int) -> int:
        """The HR on the card after the logger's notification number `count`, with these beats (RR in ms)."""
        answers.append({'state': 'connected', 'session': {'seconds': 10.0, 'samples': {'hr': count}},
                        'last_hr': {'hr_bpm': 70, 'rr_raw': [ms * 1024 // 1000 for ms in rr_ms]}})
        return polar_record.live(TZ)['hr']

    assert notice(1) == 70, 'no beats yet: the HR of the strap'
    assert notice(2, 1000, 1000) == 60
    assert notice(3, 500) == 72
    assert notice(3, 500) == 72, 'the same notification read again adds no beat'
    assert notice(4, 500, 500, 500) == 100, 'the last 5 beats: 1000 ms and four of 500 ms'
    assert notice(5, 3000) == 100, 'a beat outside 280-1700 ms (a missed beat) is left out'
    assert notice(20, 1000) == 60, 'after 10 notifications not read, the older beats are left out'


def test_a_start_that_fails_shows_the_reason_and_what_to_do(fake: Fake) -> None:
    fake.use('fail')
    polar_record.start(TZ)
    until(lambda: state() == 'off' and polar_record.live(TZ)['error'] != '')
    error = polar_record.live(TZ)['error']
    assert 'The recording did not start: No Polar H10 was found.' in error and polar_record.NOT_STARTED in error


def test_a_logger_without_its_api_is_stopped_after_the_wait(fake: Fake, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(polar_record, 'API_WAIT_S', 0.5)
    fake.use('no-api')
    polar_record.start(TZ)
    assert state() == 'connecting'
    until(lambda: state() == 'off')
    assert f'did not answer at http://127.0.0.1:{fake.port}' in polar_record.live(TZ)['error']


def test_stop_while_the_strap_connects_stops_the_own_process_without_an_error(fake: Fake) -> None:
    fake.use('no-api')
    polar_record.start(TZ)
    assert state() == 'connecting'
    polar_record.stop(TZ)
    until(lambda: state() == 'off')
    assert polar_record.live(TZ)['error'] == ''


def test_a_second_start_is_refused(fake: Fake) -> None:
    fake.use('no-api')
    polar_record.start(TZ)
    with pytest.raises(polar_record.Busy):
        polar_record.start(TZ)
    polar_record.stop(TZ)
    until(lambda: state() == 'off')


def test_a_recording_the_app_did_not_start_is_followed_marked_stopped_and_imported(fake: Fake) -> None:
    other = subprocess.Popen(fake.command('ok'))
    try:
        until(lambda: state() == 'recording')
        polar_record.mark('effort done')
        polar_record.stop(TZ)
        assert other.wait(10) == 0
        until(lambda: state() == 'off')
    finally:
        if other.poll() is None:
            other.kill()
    (session,) = polar_store.sessions(0, 5)['sessions']
    assert [m['label'] for m in session['markers']] == ['effort done']


def test_a_recording_that_ends_with_an_error_shows_it_and_is_imported(fake: Fake) -> None:
    fake.use('ends')
    polar_record.start(TZ)
    until(lambda: state() == 'off' and polar_record.live(TZ)['error'] != '')
    assert polar_record.live(TZ)['error'] == 'The recording ended with an error: The strap stopped sending.'
    assert polar_store.count() == 1


def test_a_marker_needs_a_recording_and_is_effort_done() -> None:
    with pytest.raises(polar_record.RecordError, match='Press Start recording first'):
        polar_record.mark('effort done')
    for label in ('lunch', 'angina onset', 'exercise start', 'exercise stop'):
        with pytest.raises(ValueError, match='is not a marker'):
            polar_record.mark(label)


def test_start_without_the_logger_folder_says_how_to_set_it() -> None:
    with pytest.raises(polar_record.RecordError, match='logger_project'):
        polar_record.start(TZ)


def test_the_routes_start_mark_and_stop(fake: Fake, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api, '_health_now', lambda: datetime.now(TZ))
    client = TestClient(api.app)
    assert client.get('/api/health/polar/live').json()['state'] == 'off'
    assert client.post('/api/health/polar/live/start', json={}).json()['state'] in ('connecting', 'recording')
    until(lambda: client.get('/api/health/polar/live').json()['state'] == 'recording')
    assert client.post('/api/health/polar/live/markers', json={'label': 'lunch'}).status_code == 400
    assert client.post('/api/health/polar/live/markers', json={'label': 'exercise stop'}).status_code == 400, \
        'the angina and exercise buttons are gone'
    assert client.post('/api/health/polar/live/markers', json={'label': 'effort done'}).json()['last_marker']['label'] \
        == 'effort done'
    assert client.post('/api/health/polar/live/cooldown', json={}).status_code in (404, 405), 'the Cooldown 60 s button is gone'
    assert client.post('/api/health/polar/live/start', json={}).status_code == 409
    client.post('/api/health/polar/live/stop', json={})
    until(lambda: client.get('/api/health/polar/live').json()['state'] == 'off')
    assert client.post('/api/health/polar/live/stop', json={}).status_code == 409


def test_the_card_says_stopping_until_the_import_of_an_ended_recording_has_begun() -> None:
    polar_record._state['following'] = True
    assert state() == 'stopping', 'the logger has ended, its session is not imported yet'
    polar_record._state['following'] = False
    assert state() == 'off'


class NotJson(BaseHTTPRequestHandler):
    """Another program on the logger's port: it answers 200 with a text that is not JSON."""

    def log_message(self, *args: object) -> None:
        """No request log."""

    def do_GET(self) -> None:
        """Any GET: 200 and a text."""
        self.send_response(200)
        self.send_header('Content-Length', '5')
        self.end_headers()
        self.wfile.write(b'hello')


def test_another_program_on_the_logger_port_or_a_wrong_address_is_no_recording(monkeypatch: pytest.MonkeyPatch) -> None:
    server = ThreadingHTTPServer(('127.0.0.1', 0), NotJson)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        monkeypatch.setattr(polar_record, 'settings', lambda: (None, f'http://127.0.0.1:{server.server_address[1]}'))
        assert state() == 'off'
        monkeypatch.setattr(polar_record, 'settings', lambda: (None, 'http://127.0.0.1:87 70'))
        assert state() == 'off'
    finally:
        server.shutdown()


def test_a_failure_while_following_frees_the_recording(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken() -> dict | None:
        """A status read that fails in a way nobody foresaw."""
        raise RuntimeError('a failure')

    monkeypatch.setattr(polar_record, '_status', broken)
    polar_record._state.update(following=True, stopping=True)
    with pytest.raises(RuntimeError):
        polar_record._follow(TZ)
    assert (polar_record._state['following'], polar_record._state['stopping']) == (False, False)


def test_a_recording_started_with_an_activity_gives_it_to_its_session(fake: Fake) -> None:
    polar_record.start(TZ, 2)
    assert polar_record.live(TZ)['activity'] == 2
    assert store.setting('polar_last_activity') == '2'
    until(lambda: state() == 'recording')
    polar_record.stop(TZ)
    until(lambda: state() == 'off')
    (session,) = polar_store.sessions(0, 5)['sessions']
    assert (session['activity_id'], session['activity']) == (2, 'Gym cardio')



def test_an_hrv_test_with_a_test_time_shows_it_and_gives_it_to_its_session(fake: Fake) -> None:
    polar_record.start(TZ, 6, 180)
    assert polar_record.live(TZ)['measure_s'] == 180
    until(lambda: state() == 'recording')
    polar_record.stop(TZ)
    until(lambda: state() == 'off')
    (session,) = polar_store.sessions(0, 5)['sessions']
    assert (session['activity_id'], polar_store.measure_s(session['id'])) == (6, 180)
    assert polar_record.live(TZ)['measure_s'] is None


def test_the_start_route_takes_a_test_time_of_whole_minutes_from_5_to_60() -> None:
    client = TestClient(api.app)
    for wrong in (60, 240, 330, 3660):
        assert client.post('/api/health/polar/live/start', json={'activity_id': 6, 'measure_s': wrong}).status_code == 422
