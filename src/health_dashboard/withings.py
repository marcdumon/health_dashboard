"""The link with the user's Withings account: a sign-in through the browser (OAuth 2), its renewal, and the fetch of the
blood pressure monitor's and the scale's measurements into the ledger. Withings asks for at most one fetch per user in
10 minutes; a fetch asks only for what changed since the last one."""

import os
import secrets
import threading
import time
from collections.abc import Callable
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlsplit
from zoneinfo import ZoneInfo

import httpx
from dotenv import dotenv_values

from health_dashboard import ledger, store
from health_dashboard.config import ROOT, config

AUTHORIZE_URL = 'https://account.withings.com/oauth2_user/authorize2'
TOKEN_URL = 'https://wbsapi.withings.net/v2/oauth2'
MEASURE_URL = 'https://wbsapi.withings.net/measure'
# Withings' measure types the blood pressure monitor and the Body Scan give (developer.withings.com/openapi.yaml,
# 2026-10-08). 173 to 175 come per body part; the whole-body values have no part (or part 7, which Withings does not name)
TYPES = {1: 'weight', 5: 'fat_free_mass', 6: 'fat_ratio', 8: 'fat_mass', 9: 'diastolic', 10: 'systolic', 11: 'pulse',
         76: 'muscle_mass', 77: 'hydration', 88: 'bone_mass', 91: 'pulse_wave_velocity', 155: 'vascular_age',
         167: 'nerve_health', 168: 'extracellular_water', 169: 'intracellular_water', 170: 'visceral_fat',
         173: 'fat_free_mass', 174: 'fat_mass', 175: 'muscle_mass', 196: 'nerve_response', 226: 'basal_metabolic_rate',
         227: 'metabolic_age', 229: 'skin_conductance'}
SEGMENT_TYPES = (173, 174, 175)
# the SQL condition that leaves out the measurements the user removed (health.measurements)
NOT_HIDDEN = 'grpid NOT IN (SELECT grpid FROM withings_hidden)'
PARTS = {2: 'right_arm', 3: 'left_arm', 10: 'left_leg', 11: 'right_leg', 12: 'trunk'}
NO_PART = -1
MIN_FETCH_GAP_S = 600
# an access token lasts 3 hours: it is renewed 5 minutes before it ends, not by a refused request
RENEW_EARLY_S = 300
TOKEN_KEYS = ('withings_access_token', 'withings_refresh_token', 'withings_expires_at')
# how long the app waits on another port for the browser to come back from the Withings sign-in
LISTEN_S = 600
_fetching = threading.Lock()
_listener: HTTPServer | None = None
_listener_timer: threading.Timer | None = None
_listener_lock = threading.Lock()


class WithingsError(RuntimeError):
    """Withings could not be asked, or refused; the message says what to do."""


class NotConnected(WithingsError):
    """There is no valid sign-in: the user connects (again) on the Health page."""


class TooSoon(WithingsError):
    """The last fetch is less than 10 minutes old."""


def _env(name: str) -> str:
    """A setting of .env, read at every call (a new value works at once), else of the environment."""
    return dotenv_values(ROOT / '.env').get(name) or os.environ.get(name, '')


def credentials() -> tuple[str, str]:
    """The client id and secret of the user's Withings app."""
    return _env('WITHINGS_CLIENT_ID'), _env('WITHINGS_CLIENT_SECRET')


def own_callback() -> str:
    """The app's own route for the end of the Withings sign-in."""
    return f'http://localhost:{config().port}/api/health/withings/callback'


def callback_url() -> str:
    """Where Withings sends the browser back after the sign-in: the address registered in the user's Withings app, set as
    WITHINGS_CALLBACK_URL in .env when it is not the app's own route (the user's app had http://localhost:12345/redirect)."""
    return _env('WITHINGS_CALLBACK_URL') or own_callback()


def _stop_listening() -> None:
    """Stop waiting for the browser on the other port."""
    global _listener, _listener_timer
    with _listener_lock:
        server, _listener = _listener, None
        if _listener_timer:
            _listener_timer.cancel()
            _listener_timer = None
    if server:
        server.shutdown()
        server.server_close()


def listen(on_callback: Callable[[dict[str, str]], str]) -> bool:
    """When the registered callback is another port on this PC, wait there for the one sign-in: its query goes to
    `on_callback`, whose answer is the page the browser goes to next; the waiting ends after it, or after 10 minutes.
    False when the callback is the app's own route."""
    if callback_url() == own_callback():
        return False
    url = urlsplit(callback_url())
    if url.scheme != 'http' or url.hostname not in ('localhost', '127.0.0.1') or not url.port:
        raise WithingsError(f'The callback {callback_url()} is not an address on this PC. Set WITHINGS_CALLBACK_URL in .env to '
                            'the http://localhost:PORT/... address of your Withings app.')
    global _listener, _listener_timer
    with _listener_lock:
        if _listener is not None:
            # a new press on Connect gives the sign-in its full time again
            _restart_timer()
            return True

        class Handler(BaseHTTPRequestHandler):
            """The browser coming back from Withings."""

            def do_GET(self) -> None:
                """Pass the sign-in on, send the browser to the page, and stop waiting."""
                parts = urlsplit(self.path)
                if parts.path != url.path:
                    self.send_response(404)
                    self.end_headers()
                    return
                location = on_callback({k: v[0] for k, v in parse_qs(parts.query).items()})
                self.send_response(302)
                self.send_header('Location', location)
                self.end_headers()
                # shut down from another thread: this one serves the request
                threading.Thread(target=_stop_listening, daemon=True).start()

            def log_message(self, format: str, *args: object) -> None:
                """No access log on the terminal."""

        try:
            server = HTTPServer(('127.0.0.1', url.port), Handler)
        except OSError as error:
            raise WithingsError(f'Port {url.port} is in use by another program, so the Withings sign-in cannot come back to '
                                'the app. Close that program, then press Connect Withings again.') from error
        _listener = server
        threading.Thread(target=server.serve_forever, daemon=True).start()
        _restart_timer()
    return True


def _restart_timer() -> None:
    """Stop waiting LISTEN_S seconds from now (called with the listener lock held)."""
    global _listener_timer
    if _listener_timer:
        _listener_timer.cancel()
    _listener_timer = threading.Timer(LISTEN_S, _stop_listening)
    _listener_timer.daemon = True
    _listener_timer.start()


def _codes() -> tuple[str, str]:
    """The client id and secret, or what to do when they are missing."""
    client_id, secret = credentials()
    if not client_id or not secret:
        raise WithingsError('Put WITHINGS_CLIENT_ID and WITHINGS_CLIENT_SECRET of your Withings app in the .env file in the app '
                            'folder, then press Connect Withings again.')
    return client_id, secret


def authorize_url() -> str:
    """The Withings sign-in page, with a new random state that the callback must bring back."""
    client_id, _ = _codes()
    state = secrets.token_urlsafe(16)
    store.set_setting('withings_state', state)
    return f'{AUTHORIZE_URL}?' + urlencode({'response_type': 'code', 'client_id': client_id, 'scope': 'user.metrics',
                                            'redirect_uri': callback_url(), 'state': state})


def _token(http: httpx.Client, params: dict) -> None:
    """Ask Withings for tokens and keep them; the new refresh token replaces the old one, which stops working."""
    client_id, secret = _codes()
    try:
        answer = http.post(TOKEN_URL, data={'action': 'requesttoken', 'client_id': client_id, 'client_secret': secret, **params})
        data = answer.json()
    except (httpx.HTTPError, ValueError) as error:
        raise WithingsError(f'Withings could not be reached: {error}') from error
    if data.get('status') != 0:
        raise WithingsError(f'Withings refused the sign-in: {data.get("error") or data.get("status")}.')
    body = data['body']
    store.set_setting('withings_access_token', body['access_token'])
    store.set_setting('withings_refresh_token', body['refresh_token'])
    store.set_setting('withings_expires_at', str(time.time() + float(body['expires_in'])))


def connect(code: str, state: str, http: httpx.Client) -> None:
    """The callback of the sign-in: the state must be the one this app made; the code lives 30 seconds, so it is used now."""
    if not state or state != store.setting('withings_state'):
        raise WithingsError('This Withings sign-in did not start in this app, or it is old. Press Connect Withings on the '
                            'Health page again.')
    _token(http, {'grant_type': 'authorization_code', 'code': code, 'redirect_uri': callback_url()})
    store.set_setting('withings_state', None)
    store.set_setting('withings_error', None)


def _disconnect() -> None:
    """Forget the tokens: the sign-in no longer works."""
    for key in TOKEN_KEYS:
        store.set_setting(key, None)


def access_token(http: httpx.Client, now: float | None = None) -> str:
    """A valid access token: the kept one, or a renewed one when it ends within 5 minutes."""
    now = time.time() if now is None else now
    refresh = store.setting('withings_refresh_token')
    if not refresh:
        raise NotConnected('Withings is not connected. Press Connect Withings on the Health page.')
    if now < float(store.setting('withings_expires_at') or 0) - RENEW_EARLY_S:
        return store.setting('withings_access_token') or ''
    try:
        _token(http, {'grant_type': 'refresh_token', 'refresh_token': refresh})
    except WithingsError as error:
        if 'could not be reached' in str(error):
            raise
        _disconnect()
        raise NotConnected('The Withings sign-in has ended. Press Connect again on the Health page.') from error
    return store.setting('withings_access_token') or ''


def _value(measure: dict) -> float:
    """A measure's real value: Withings sends value x 10^unit."""
    value, unit = measure['value'], measure['unit']
    return float(value) / 10 ** -unit if unit < 0 else float(value) * 10 ** unit


def _part(measure: dict) -> int:
    """The body part of a value of an arm, a leg or the trunk; NO_PART for a value of the whole body."""
    position = measure.get('position')
    return position if measure['type'] in SEGMENT_TYPES and position in PARTS else NO_PART


def fetch(http: httpx.Client, now: float | None = None) -> int:
    """Fetch the measurements changed since the last fetch (the first time: all), every page; how many groups came."""
    token = access_token(http, now)
    params = {'action': 'getmeas', 'meastypes': ','.join(str(t) for t in TYPES), 'category': '1'}
    if since := store.setting('withings_lastupdate'):
        params['lastupdate'] = since
    groups, updatetime = 0, None
    while True:
        try:
            data = http.post(MEASURE_URL, data=params, headers={'Authorization': f'Bearer {token}'}).json()
        except (httpx.HTTPError, ValueError) as error:
            raise WithingsError(f'Withings could not be reached: {error}') from error
        if data.get('status') == 401:
            _disconnect()
            raise NotConnected('The Withings sign-in has ended. Press Connect again on the Health page.')
        if data.get('status') != 0:
            raise WithingsError(f'Withings gave an error: {data.get("error") or data.get("status")}.')
        body = data['body']
        zone = ZoneInfo(body.get('timezone') or 'Europe/Brussels')
        rows = [(g['grpid'], m['type'], _part(m), _value(m), datetime.fromtimestamp(g['date'], zone).strftime('%Y-%m-%dT%H:%M'))
                for g in body.get('measuregrps') or [] for m in g['measures'] if m['type'] in TYPES]
        with ledger.connect(write=True) as db:
            db.executemany('INSERT INTO withings_values (grpid, type, position, value, measured_at) VALUES (?, ?, ?, ?, ?) '
                           'ON CONFLICT (grpid, type, position) DO UPDATE SET value = excluded.value, '
                           'measured_at = excluded.measured_at', rows)
        groups += len(body.get('measuregrps') or [])
        updatetime = body.get('updatetime', updatetime)
        if not body.get('more'):
            break
        params['offset'] = str(body['offset'])
    if updatetime is not None:
        store.set_setting('withings_lastupdate', str(updatetime))
    return groups


def due(now: float | None = None) -> bool:
    """Whether a fetch may start now: set up, connected, none running, and the last try 10 minutes old or more."""
    now = time.time() if now is None else now
    return (all(credentials()) and bool(store.setting('withings_refresh_token')) and not _fetching.locked()
            and now - float(store.setting('withings_last_try') or 0) >= MIN_FETCH_GAP_S)


def sync(http: httpx.Client | None = None, now: float | None = None, asked: bool = False) -> bool:
    """Fetch when the last try is 10 minutes old or more: True when it fetched. Asked by the user (Fetch now), a fetch that
    must wait says until when; opening the page just skips it. A failure is kept for the page."""
    now = time.time() if now is None else now
    if not all(credentials()) or not store.setting('withings_refresh_token'):
        return False
    last = float(store.setting('withings_last_try') or 0)
    if now - last < MIN_FETCH_GAP_S:
        if asked:
            at = datetime.fromtimestamp(last + MIN_FETCH_GAP_S).strftime('%H:%M')
            raise TooSoon(f'Withings asks for at most one fetch in 10 minutes. The next fetch can start at {at}.')
        return False
    if not _fetching.acquire(blocking=False):
        return False
    client = http or httpx.Client(timeout=30.0)
    try:
        store.set_setting('withings_last_try', str(now))
        fetch(client, now)
        store.set_setting('withings_last_ok', str(now))
        store.set_setting('withings_error', None)
        return True
    except WithingsError as error:
        store.set_setting('withings_error', str(error))
        return False
    finally:
        _fetching.release()
        if http is None:
            client.close()


def status(now: float | None = None) -> dict:
    """What the page says about the link: set up, connected, the last fetch, an error, whether a fetch runs."""
    last = store.setting('withings_last_ok')
    return {'configured': all(credentials()), 'connected': bool(store.setting('withings_refresh_token')),
            'last_fetch': datetime.fromtimestamp(float(last)).astimezone().isoformat(timespec='minutes') if last else None,
            'error': store.setting('withings_error') or '', 'fetching': _fetching.locked(), 'callback_url': callback_url()}


def muscle_ratio(values: dict[str, float]) -> float | None:
    """Muscle as a % of the weight of the same measurement (Withings sends muscle only in kg); None without both."""
    weight, muscle = values.get('weight'), values.get('muscle_mass')
    return round(muscle / weight * 100, 3) if weight and muscle else None


def latest(before: str | None = None) -> dict:
    """The newest blood pressure reading as one group, the newest whole-body value of each other kind ({value,
    measured_at}), and the newest value of each body part per kind; with `before` (a local time, 2026-10-09T04:00) only
    the ones measured before it."""
    # no time: every measurement, as all times come before this one
    before = before or '9999'
    with ledger.connect() as db:
        # the newest time per kind and part first, then its values: a lookup per row took 4.7 s on years of measurements
        rows = db.execute(f'SELECT m.type, m.position, m.value, m.measured_at FROM withings_values m JOIN (SELECT type, position, '
                          f'MAX(measured_at) AS at FROM withings_values WHERE {NOT_HIDDEN} AND measured_at < ? '
                          f'GROUP BY type, position) newest '
                          f'ON newest.type = m.type AND newest.position = m.position AND newest.at = m.measured_at '
                          f'WHERE m.{NOT_HIDDEN} ORDER BY m.grpid DESC', (before,)).fetchall()
        newest: dict = {}
        segments: dict = {}
        for row in rows:
            value = {'value': row['value'], 'measured_at': row['measured_at']}
            if row['position'] in PARTS:
                segments.setdefault(TYPES[row['type']], {}).setdefault(PARTS[row['position']], value)
            else:
                newest.setdefault(TYPES[row['type']], value)
        group = db.execute(f'SELECT grpid, measured_at FROM withings_values WHERE type = 10 AND {NOT_HIDDEN} AND measured_at < ? '
                           'ORDER BY measured_at DESC, grpid DESC LIMIT 1', (before,)).fetchone()
        pressure = None
        if group:
            values = {TYPES[r['type']]: r['value'] for r in db.execute(
                'SELECT type, value FROM withings_values WHERE grpid = ? AND type IN (9, 10, 11)', (group['grpid'],))}
            pressure = {'systolic': values.get('systolic'), 'diastolic': values.get('diastolic'), 'pulse': values.get('pulse'),
                        'measured_at': group['measured_at']}
    weight, muscle = newest.get('weight'), newest.get('muscle_mass')
    # only from one measurement: a weight and a muscle mass of different days give no true %
    if weight and muscle and weight['measured_at'] == muscle['measured_at']:
        newest['muscle_ratio'] = {'value': muscle_ratio({'weight': weight['value'], 'muscle_mass': muscle['value']}),
                                  'measured_at': muscle['measured_at']}
    return {'pressure': pressure, 'values': {k: v for k, v in newest.items() if k not in ('systolic', 'diastolic')},
            'segments': segments}
