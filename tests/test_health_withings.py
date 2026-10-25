"""The Withings link, against a stand-in Withings: the sign-in, the token renewal, the fetch of measurements and its pace."""

import time
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import respx

from health_dashboard import store, withings

TOKEN = 'https://wbsapi.withings.net/v2/oauth2'
MEASURE = 'https://wbsapi.withings.net/measure'
NOW = 1_791_500_000.0


@pytest.fixture(autouse=True)
def app_codes(monkeypatch) -> None:
    """The codes of the user's Withings app, as in .env."""
    monkeypatch.setattr(withings, 'credentials', lambda: ('cid', 'secret'))


def tokens(access: str = 'a1', refresh: str = 'r1', expires_in: int = 10800) -> httpx.Response:
    """A token answer of Withings."""
    return httpx.Response(200, json={'status': 0, 'body': {'userid': 42, 'access_token': access, 'refresh_token': refresh,
                                                           'expires_in': expires_in, 'scope': 'user.metrics', 'token_type': 'Bearer'}})


def sent(route: respx.Route, n: int = -1) -> dict[str, str]:
    """The form a request to the stand-in carried."""
    return {k: v[0] for k, v in parse_qs(route.calls[n].request.content.decode()).items()}


def connected(expires_at: float = NOW + 3600) -> None:
    """A user who signed in before."""
    store.set_setting('withings_access_token', 'a1')
    store.set_setting('withings_refresh_token', 'r1')
    store.set_setting('withings_expires_at', str(expires_at))


def test_the_sign_in_link_asks_for_the_measurements_and_remembers_its_state() -> None:
    url = urlsplit(withings.authorize_url())
    query = {k: v[0] for k, v in parse_qs(url.query).items()}
    assert f'{url.scheme}://{url.netloc}{url.path}' == 'https://account.withings.com/oauth2_user/authorize2'
    assert query['response_type'] == 'code' and query['client_id'] == 'cid' and query['scope'] == 'user.metrics'
    assert query['redirect_uri'] == 'http://localhost:8766/api/health/withings/callback'
    assert query['state'] == store.setting('withings_state') and len(query['state']) > 10


@respx.mock
def test_the_callback_with_the_right_state_stores_the_tokens(monkeypatch) -> None:
    monkeypatch.setattr(withings.time, 'time', lambda: NOW)
    route = respx.post(TOKEN).mock(return_value=tokens())
    state = parse_qs(urlsplit(withings.authorize_url()).query)['state'][0]
    with httpx.Client() as http:
        withings.connect('code-1', state, http)
    assert sent(route) == {'action': 'requesttoken', 'grant_type': 'authorization_code', 'client_id': 'cid',
                           'client_secret': 'secret', 'code': 'code-1',
                           'redirect_uri': 'http://localhost:8766/api/health/withings/callback'}
    assert (store.setting('withings_access_token'), store.setting('withings_refresh_token')) == ('a1', 'r1')
    assert float(store.setting('withings_expires_at')) == NOW + 10800 and store.setting('withings_state') is None


@respx.mock
def test_a_callback_with_another_state_is_refused_without_asking_withings() -> None:
    route = respx.post(TOKEN).mock(return_value=tokens())
    withings.authorize_url()
    with httpx.Client() as http, pytest.raises(withings.WithingsError, match='Connect Withings'):
        withings.connect('code-1', 'someone-else', http)
    assert route.call_count == 0 and store.setting('withings_refresh_token') is None


@respx.mock
def test_the_sign_in_is_renewed_before_it_ends_and_the_new_renewal_code_is_kept() -> None:
    route = respx.post(TOKEN).mock(return_value=tokens('a2', 'r2'))
    connected(expires_at=NOW + 3600)
    with httpx.Client() as http:
        assert withings.access_token(http, now=NOW) == 'a1' and route.call_count == 0
        assert withings.access_token(http, now=NOW + 3500) == 'a2'
    assert sent(route) == {'action': 'requesttoken', 'grant_type': 'refresh_token', 'client_id': 'cid', 'client_secret': 'secret',
                           'refresh_token': 'r1'}
    assert store.setting('withings_refresh_token') == 'r2', 'the old renewal code stops working: the new one is kept'


@respx.mock
def test_a_refused_renewal_means_connect_again() -> None:
    respx.post(TOKEN).mock(return_value=httpx.Response(200, json={'status': 503, 'error': 'Invalid refresh token'}))
    connected(expires_at=NOW - 1)
    with httpx.Client() as http, pytest.raises(withings.NotConnected, match='Connect again'):
        withings.access_token(http, now=NOW)
    assert store.setting('withings_refresh_token') is None


def group(grpid: int, when: int, *measures: tuple) -> dict:
    """A measurement group as Withings sends it: (type, value, unit) per measure, and the body part for a segment."""
    return {'grpid': grpid, 'date': when, 'category': 1,
            'measures': [{'type': m[0], 'value': m[1], 'unit': m[2], **({'position': m[3]} if len(m) > 3 else {})} for m in measures]}


@respx.mock
def test_a_fetch_reads_every_page_decodes_the_values_and_remembers_where_it_was() -> None:
    connected()
    pages = [
        {'status': 0, 'body': {'updatetime': 1_791_499_000, 'timezone': 'Europe/Brussels', 'more': 1, 'offset': 1,
                               'measuregrps': [group(1, 1_791_400_000, (10, 142, 0), (9, 88, 0), (11, 71, 0))]}},
        {'status': 0, 'body': {'updatetime': 1_791_499_000, 'timezone': 'Europe/Brussels', 'more': 0, 'offset': 0,
                               'measuregrps': [group(2, 1_791_450_000, (1, 82350, -3), (6, 2415, -2), (76, 6110, -2, 7),
                                                     (175, 423, -2, 2), (175, 448, -2, 3), (175, 3532, -2, 12), (170, 44, -1, 7),
                                                     (155, 569, -1))]}},
        {'status': 0, 'body': {'updatetime': 1_791_499_900, 'timezone': 'Europe/Brussels', 'more': 0,
                               'measuregrps': [group(2, 1_791_450_000, (1, 82300, -3), (6, 2410, -2), (76, 6110, -2, 7),
                                                     (175, 423, -2, 2), (175, 448, -2, 3), (175, 3532, -2, 12))]}},
    ]
    route = respx.post(MEASURE).mock(side_effect=[httpx.Response(200, json=p) for p in pages])
    with httpx.Client() as http:
        assert withings.fetch(http, now=NOW) == 2
        assert withings.fetch(http, now=NOW) == 1
    first, second, third = (sent(route, n) for n in range(3))
    assert first['action'] == 'getmeas' and first['category'] == '1' and 'lastupdate' not in first, 'the first time: everything'
    assert set(first['meastypes'].split(',')) == {str(t) for t in withings.TYPES}
    assert second['offset'] == '1' and third['lastupdate'] == '1791499000'
    assert route.calls[0].request.headers['authorization'] == 'Bearer a1'
    latest = withings.latest()
    assert latest['pressure'] == {'systolic': 142.0, 'diastolic': 88.0, 'pulse': 71.0, 'measured_at': '2026-10-07T21:06'}
    assert latest['values']['weight'] == {'value': 82.3, 'measured_at': '2026-10-08T11:00'}, 'a group sent again replaces it'
    assert latest['values']['fat_ratio']['value'] == 24.1 and latest['values']['muscle_mass']['value'] == 61.1
    assert (latest['values']['visceral_fat']['value'], latest['values']['vascular_age']['value']) == (4.4, 56.9)
    assert latest['values']['muscle_ratio'] == {'value': round(61.1 / 82.3 * 100, 3), 'measured_at': '2026-10-08T11:00'}
    assert latest['segments']['muscle_mass'] == {
        'right_arm': {'value': 4.23, 'measured_at': '2026-10-08T11:00'},
        'left_arm': {'value': 4.48, 'measured_at': '2026-10-08T11:00'},
        'trunk': {'value': 35.32, 'measured_at': '2026-10-08T11:00'}}, 'each body part apart, not in the whole-body value'


def test_an_old_ledger_drops_its_measurements_without_body_parts_and_fetches_them_again(tmp_path, monkeypatch) -> None:
    """The first table kept one value per kind and group: the values per body part did not fit. Withings keeps everything."""
    import sqlite3

    from health_dashboard import ledger

    old = tmp_path / 'old.db'
    with sqlite3.connect(old) as db:
        db.execute('CREATE TABLE withings_measures (grpid INTEGER, type INTEGER, value REAL, measured_at TEXT, '
                   'PRIMARY KEY (grpid, type))')
        db.execute('CREATE TABLE health_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
        db.executemany('INSERT INTO health_settings VALUES (?, ?)',
                       [('withings_lastupdate', '1791499000'), ('withings_refresh_token', 'r1')])
    monkeypatch.setattr(ledger, 'DB', old)
    with ledger.connect() as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        kept = dict(db.execute('SELECT key, value FROM health_settings').fetchall())
    assert 'withings_measures' not in tables and 'withings_values' in tables
    assert kept == {'withings_refresh_token': 'r1'}, 'the sign-in stays; the next fetch gets the whole history again'


@respx.mock
def test_withings_is_asked_at_most_once_in_ten_minutes(monkeypatch) -> None:
    connected(expires_at=NOW + 99_999)
    route = respx.post(MEASURE).mock(return_value=httpx.Response(200, json={'status': 0, 'body': {
        'updatetime': 1, 'more': 0, 'measuregrps': []}}))
    with httpx.Client() as http:
        assert withings.due(now=NOW) is True and withings.sync(http, now=NOW) is True
        assert withings.due(now=NOW + 300) is False
        assert withings.sync(http, now=NOW + 300) is False and route.call_count == 1, 'the page opened again: no fetch'
        with pytest.raises(withings.TooSoon, match='10 minutes'):
            withings.sync(http, now=NOW + 300, asked=True)
        assert withings.sync(http, now=NOW + 601) is True and route.call_count == 2
    assert withings.status(now=NOW + 601)['last_fetch'] is not None and withings.status(now=NOW + 601)['error'] == ''


@respx.mock
def test_a_failed_fetch_is_remembered_and_said() -> None:
    connected(expires_at=NOW + 99_999)
    respx.post(MEASURE).mock(return_value=httpx.Response(200, json={'status': 401, 'error': 'invalid_token'}))
    with httpx.Client() as http:
        assert withings.sync(http, now=NOW) is False
    status = withings.status(now=NOW)
    assert 'Connect again' in status['error'] and status['connected'] is False


def test_without_codes_the_page_says_how_to_set_them_up(monkeypatch) -> None:
    monkeypatch.setattr(withings, 'credentials', lambda: ('', ''))
    status = withings.status(now=NOW)
    assert status == status | {'configured': False, 'connected': False}
    with pytest.raises(withings.WithingsError, match='WITHINGS_CLIENT_ID'):
        withings.authorize_url()


def test_the_callback_address_can_be_set_in_env(monkeypatch) -> None:
    assert withings.callback_url() == 'http://localhost:8766/api/health/withings/callback'
    monkeypatch.setattr(withings, '_env', lambda name: {'WITHINGS_CALLBACK_URL': 'http://localhost:12345/redirect'}.get(name, ''))
    assert withings.callback_url() == 'http://localhost:12345/redirect'
    query = parse_qs(urlsplit(withings.authorize_url()).query)
    assert query['redirect_uri'] == ['http://localhost:12345/redirect']


def free_port() -> int:
    """A port no program listens on."""
    import socket

    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


def test_a_callback_on_another_port_is_caught_once_and_sent_to_the_page(monkeypatch) -> None:
    """The user's Withings app was registered with http://localhost:12345/redirect: the app listens there during the sign-in."""
    port = free_port()
    monkeypatch.setattr(withings, '_env', lambda name: {'WITHINGS_CALLBACK_URL': f'http://localhost:{port}/redirect'}.get(name, ''))
    got = []
    assert withings.listen(lambda query: got.append(query) or 'http://127.0.0.1:8766/') is True
    assert withings.listen(lambda query: 'x') is True, 'pressing Connect twice keeps the one listener'
    answer = httpx.get(f'http://127.0.0.1:{port}/redirect?code=c1&state=s1')
    assert answer.status_code == 302 and answer.headers['location'] == 'http://127.0.0.1:8766/'
    assert got == [{'code': 'c1', 'state': 's1'}]
    for _ in range(50):
        try:
            httpx.get(f'http://127.0.0.1:{port}/redirect', timeout=0.2)
        except httpx.HTTPError:
            break
        time.sleep(0.05)
    else:
        pytest.fail('the listener must stop after the sign-in')


def test_the_app_s_own_callback_needs_no_listener() -> None:
    assert withings.listen(lambda query: 'x') is False


def test_a_port_in_use_is_said(monkeypatch) -> None:
    import socket

    with socket.socket() as busy:
        busy.bind(('127.0.0.1', 0))
        busy.listen()
        port = busy.getsockname()[1]
        callback = {'WITHINGS_CALLBACK_URL': f'http://localhost:{port}/redirect'}
        monkeypatch.setattr(withings, '_env', lambda name: callback.get(name, ''))
        with pytest.raises(withings.WithingsError, match=f'Port {port} is in use'):
            withings.listen(lambda query: 'x')


def test_pressing_connect_again_gives_the_sign_in_its_full_time(monkeypatch) -> None:
    """The waiting started by the first press ended while the user was still signing in after a later press."""
    port = free_port()
    callback = {'WITHINGS_CALLBACK_URL': f'http://localhost:{port}/redirect'}
    monkeypatch.setattr(withings, '_env', lambda name: callback.get(name, ''))
    monkeypatch.setattr(withings, 'LISTEN_S', 0.6)
    withings.listen(lambda query: 'http://127.0.0.1:8766/')
    time.sleep(0.4)
    withings.listen(lambda query: 'http://127.0.0.1:8766/')
    time.sleep(0.4)
    assert httpx.get(f'http://127.0.0.1:{port}/other').status_code == 404, 'still waiting: the second press counts from then'
    time.sleep(0.5)
    with pytest.raises(httpx.HTTPError):
        httpx.get(f'http://127.0.0.1:{port}/other', timeout=0.3)


def test_the_latest_values_come_quickly_from_years_of_measurements() -> None:
    """The user's account held 10,554 values since 2014: the page waited 4.7 s for them."""
    from health_dashboard import ledger

    rows = [(n, kind, 80 + n % 7, f'2014-11-12T07:{n % 60:02d}' if n < 11_990 else f'2026-10-08T11:{n % 60:02d}')
            for n in range(12_000) for kind in (1, 6, 10)]
    with ledger.connect(write=True) as db:
        db.executemany('INSERT INTO withings_values (grpid, type, position, value, measured_at) VALUES (?, ?, -1, ?, ?)', rows)
    started = time.perf_counter()
    latest = withings.latest()
    assert time.perf_counter() - started < 0.3
    assert latest['values']['weight']['measured_at'].startswith('2026-10-08')
    assert latest['pressure']['measured_at'].startswith('2026-10-08')


def test_the_latest_values_up_to_a_time_leave_out_the_later_ones() -> None:
    from health_dashboard import ledger

    with ledger.connect(write=True) as db:
        db.executemany('INSERT INTO withings_values (grpid, type, position, value, measured_at) VALUES (?, ?, ?, ?, ?)', [
            (1, 10, -1, 131, '2026-10-07T07:30'), (1, 9, -1, 84, '2026-10-07T07:30'), (1, 11, -1, 66, '2026-10-07T07:30'),
            (2, 1, -1, 85.0, '2026-10-06T08:00'), (2, 175, 3, 4.4, '2026-10-06T08:00'),
            (3, 10, -1, 140, '2026-10-09T01:10'), (3, 9, -1, 90, '2026-10-09T01:10'),
            (4, 1, -1, 84.0, '2026-10-09T07:00'), (4, 175, 3, 4.6, '2026-10-09T07:00')])
    latest = withings.latest(before='2026-10-09T04:00')
    assert latest['pressure'] == {'systolic': 140.0, 'diastolic': 90.0, 'pulse': None, 'measured_at': '2026-10-09T01:10'}, \
        'a reading before that time counts'
    assert latest['values']['weight'] == {'value': 85.0, 'measured_at': '2026-10-06T08:00'}, 'the newest before that time'
    assert latest['segments']['muscle_mass']['left_arm']['value'] == 4.4
    assert withings.latest()['values']['weight']['value'] == 84.0, 'without a time: the newest of all'
    assert withings.latest(before='2026-10-06T04:00') == {'pressure': None, 'values': {}, 'segments': {}}
