"""The Polar H10 routes over HTTP, from made-up files: the import in the background, the sessions with Remove and Show
again, and the day values in the history and the chart analysis."""

from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pytest
from fastapi.testclient import TestClient

from health_dashboard import api, ledger, polar
from tests.polar_fixtures import phone_session, set_person, still, stored_session

TZ = ZoneInfo('Europe/Brussels')
NOW = datetime(2026, 7, 2, 12, 0, tzinfo=TZ)


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    """A client at noon on 2 July, with an exercise session on 1 July and a still session on the morning of 2 July in the
    phone folder; work in the background runs at once."""
    phone, logger = tmp_path / 'phone', tmp_path / 'logger'
    phone.mkdir()
    logger.mkdir()
    phone_session(phone, datetime(2026, 7, 1, 10, 0), 900)
    phone_session(phone, datetime(2026, 7, 2, 7, 30), 420, lambda s: 60.0, beats=[980, 1020] * 210, acc=still)
    monkeypatch.setattr(api, '_health_now', lambda: NOW)
    monkeypatch.setattr(api, '_in_background', lambda work, *args: work(*args))
    monkeypatch.setattr(polar, 'folders', lambda: (phone, logger))
    return TestClient(api.app)


def test_import_sessions_reads_both_sessions_and_the_page_shows_them(client) -> None:
    assert client.get('/api/health/polar/waiting').json() == {'sessions': 2, 'recalc': False}
    before = client.get('/api/health').json()['polar']
    assert (before['configured'], before['sessions'], before['running'], before['last_import']) == (True, 0, False, None)
    view = client.post('/api/health/polar/import?day=2026-07-02', json={}).json()
    assert (view['day'], view['polar']['sessions'], view['polar']['error']) == ('2026-07-02', 2, '')
    assert (view['polar']['done'], view['polar']['total']) == (6, 6) and view['polar']['last_import']
    listed = client.get('/api/health/polar/sessions').json()
    assert [s['kind'] for s in listed['sessions']] == ['exercise', 'exercise'] and listed['total'] == 2 and listed['removed'] == []
    assert client.get('/api/health/polar/waiting').json() == {'sessions': 0, 'recalc': False}


def label_the_morning_session_hrv(client: TestClient) -> None:
    """Give the session of the morning of 2 July the HRV activity."""
    morning = client.get('/api/health/polar/days/2026-07-02').json()['sessions'][0]
    client.post(f'/api/health/polar/sessions/{morning["id"]}/label', json={'activity_id': 6})


def test_the_card_shows_the_last_session_and_the_last_hrv_test(client) -> None:
    client.post('/api/health/polar/import', json={})
    label_the_morning_session_hrv(client)
    polar_view = client.get('/api/health').json()['polar']
    assert polar_view['last_exercise']['max_hr'] == 150.0 and 'recovery' not in polar_view['last_exercise']
    assert (polar_view['last_hrv']['rmssd'], polar_view['last_hrv']['status']['missing'], 'last_rest' in polar_view) == (40.0, 13,
                                                                                                                       False)
    assert client.get('/api/health?day=2026-07-01').json()['polar']['last_hrv'] is None, 'the test is on 2 July'


def test_each_session_is_a_point_of_the_history_and_a_day_has_only_the_values_of_the_tests(client) -> None:
    client.post('/api/health/polar/import', json={})
    label_the_morning_session_hrv(client)
    history = client.get('/api/health/history?days=7').json()
    days = {r['day']: r for r in history['days']}
    assert not [k for r in history['days'] for k in r if k in ('h10:max_hr', 'h10:mean_hr', 'h10:moving_hr')], \
        'no day value of the sessions: a day mixes activities and days without a recording'
    assert [(p['day'], p['h10:max_hr'], p['h10:mean_hr']) for p in history['points'] if 'h10:max_hr' in p] \
        == [('2026-07-01', 150.0, 94.1)], 'the HRV test of the morning is not an exercise session'
    morning = days['2026-07-02']
    assert {'h10:rest_hr': 60.0, 'h10:rmssd': 40.0, 'h10:ln_rmssd': 3.69, 'h10:ln_rmssd_7d': 3.69}.items() <= morning.items()
    assert 'h10:max_hr' not in morning and client.get('/api/health/analysis?key=h10:ln_rmssd').status_code == 200
    assert client.get('/api/health/analysis?key=h10:max_hr').status_code == 200
    assert client.get('/api/health/analysis?key=h10:above_limit').status_code == 404, 'no HR limit'
    assert 'limit' not in client.get('/api/health').json()['polar']


def test_a_removed_session_leaves_the_history_and_comes_back_with_show_again(client) -> None:
    client.post('/api/health/polar/import', json={})
    (exercise,) = client.get('/api/health/polar/days/2026-07-01').json()['sessions']
    assert client.post(f'/api/health/polar/sessions/{exercise["id"]}/remove').json() == {'ok': True}
    def first_day() -> list[float]:
        """The maximum HR of the sessions of 1 July in the history."""
        return [p['h10:max_hr'] for p in client.get('/api/health/history?days=7').json()['points']
                if p['day'] == '2026-07-01' and 'h10:max_hr' in p]

    assert first_day() == []
    assert [s['id'] for s in client.get('/api/health/polar/sessions').json()['removed']] == [exercise['id']]
    client.post(f'/api/health/polar/sessions/{exercise["id"]}/show')
    assert first_day() == [150.0]
    assert client.post('/api/health/polar/sessions/999/remove').status_code == 404


def test_many_sessions_leave_the_history_with_one_request_and_come_back_with_one_request(client) -> None:
    client.post('/api/health/polar/import', json={})
    ids = [s['id'] for s in client.get('/api/health/polar/sessions').json()['sessions']]
    assert client.post('/api/health/polar/sessions/remove', json={'ids': ids}).json() == {'ok': True}
    listed = client.get('/api/health/polar/sessions').json()
    assert (listed['sessions'], sorted(s['id'] for s in listed['removed'])) == ([], sorted(ids))
    assert not any(k.startswith('h10:') for r in client.get('/api/health/history?days=7').json()['days'] for k in r)
    assert client.post('/api/health/polar/sessions/show', json={'ids': ids}).json() == {'ok': True}
    assert client.get('/api/health/polar/sessions').json()['total'] == 2
    assert client.post('/api/health/polar/sessions/remove', json={'ids': [999]}).status_code == 404
    assert client.post('/api/health/polar/sessions/remove', json={'ids': []}).status_code == 422


def test_a_failure_before_the_import_starts_leaves_the_import_free(client, monkeypatch) -> None:
    def no_clock() -> datetime:
        """The user's timezone cannot be asked."""
        raise RuntimeError('no network')

    monkeypatch.setattr(api, '_health_now', no_clock)
    with pytest.raises(RuntimeError):
        client.post('/api/health/polar/import', json={})
    assert polar.begin(), 'the import is not taken'
    polar._running.release()


def test_import_without_folders_says_how_to_set_them(client, monkeypatch) -> None:
    monkeypatch.setattr(polar, 'folders', lambda: (None, None))
    answer = client.post('/api/health/polar/import', json={})
    assert answer.status_code == 409 and 'health_dashboard.toml' in answer.json()['detail']


def test_the_activities_and_the_label_of_a_session_over_http(client) -> None:
    client.post('/api/health/polar/import', json={})
    listed = client.get('/api/health/polar/activities').json()
    assert [a['name'] for a in listed['activities']] == ['Walk', 'Gym cardio', 'Weights', 'Bike', 'Rest', 'HRV',
                                                         'HR recovery', 'Stairs test']
    assert listed['last_used'] is None
    swim = client.post('/api/health/polar/activities', json={'name': ' Swim '}).json()['id']
    assert client.post('/api/health/polar/activities', json={'name': 'swim'}).status_code == 409
    assert client.post('/api/health/polar/activities', json={'name': '   '}).status_code == 422
    assert client.post(f'/api/health/polar/activities/{swim}', json={'name': 'Swimming'}).json() == {'ok': True}
    assert client.post('/api/health/polar/activities/999', json={'name': 'X'}).status_code == 404
    (exercise,) = client.get('/api/health/polar/days/2026-07-01').json()['sessions']
    path = f'/api/health/polar/sessions/{exercise["id"]}/label'
    assert client.post(path, json={'activity_id': swim}).json() == {'ok': True}
    assert client.post(path, json={'note': 'hills'}).json() == {'ok': True}
    (labelled,) = client.get('/api/health/polar/days/2026-07-01').json()['sessions']
    assert (labelled['activity'], labelled['note']) == ('Swimming', 'hills'), 'a field not sent stays as it is'
    assert client.post(f'/api/health/polar/activities/{swim}/remove').status_code == 409
    assert client.post(path, json={'activity_id': None}).json() == {'ok': True}
    assert client.post(f'/api/health/polar/activities/{swim}/remove').json() == {'ok': True}
    assert client.post(path, json={'activity_id': 999}).status_code == 404
    assert client.post('/api/health/polar/sessions/999/label', json={'note': 'x'}).status_code == 404
    assert client.post(path, json={'note': 'x' * 2001}).status_code == 422


def test_the_activity_chooses_the_sessions_of_the_history_the_analysis_and_the_weeks(client, monkeypatch) -> None:
    client.post('/api/health/polar/import', json={})
    (exercise,) = client.get('/api/health/polar/days/2026-07-01').json()['sessions']
    client.post(f'/api/health/polar/sessions/{exercise["id"]}/label', json={'activity_id': 1})
    walks = client.get('/api/health/history?days=7&activity=1').json()['points']
    assert [p['h10:max_hr'] for p in walks if 'h10:max_hr' in p] == [150.0]
    assert not [p for p in client.get('/api/health/history?days=7&activity=4').json()['points'] if 'h10:max_hr' in p]
    walk = client.get('/api/health/analysis?key=h10:max_hr&activity=1').json()
    assert (walk['stats']['count'], walk['per']) == (1, 'session')
    assert client.get('/api/health/analysis?key=h10:max_hr&activity=4').json()['stats'] is None
    assert client.get('/api/health/analysis?key=weight').json()['per'] == 'day'
    for key, other in (('h10:max_hr', 'weight'), ('weight', 'h10:mean_hr')):
        answer = client.get(f'/api/health/relation?key={key}&other={other}&activity=1')
        assert answer.status_code == 400 and 'session' in answer.json()['detail'], 'a session value has no value of a day'
    zones = client.get('/api/health/polar/zones?days=7').json()
    assert zones == {'limits': None, 'weeks': None}, 'no birth date'
    set_person(monkeypatch, birth_date='1966-06-01')
    zones = client.get('/api/health/polar/zones?days=7').json()
    assert (zones['limits'], zones['weeks'][-1]) == ([80, 96, 112, 128, 144], {'week': '2026-06-29', 'minutes': [10, 0, 5, 0, 0]}), \
        '590 s at 80 bpm (zone 1) and 300 s at 120 bpm (zone 3); the still morning at 60 bpm is below zone 1'
    weeks = client.get('/api/health/polar/weeks?days=7').json()
    assert weeks['activities'][:2] == ['Walk', 'Gym cardio']
    assert weeks['weeks'][-1] == {'week': '2026-06-29', 'minutes': {'Walk': 15, 'No activity': 7}}
    assert client.get('/api/health/polar/weeks?days=7&activity=1').json()['weeks'][-1]['minutes'] == {'Walk': 15}


def test_the_setting_without_split_is_gone(client) -> None:
    assert client.post('/api/health/polar/activities/1/no-split', json={'on': True}).status_code in (404, 405)


def test_an_hr_recovery_test_shows_on_the_card_in_the_detail_the_history_and_the_last_test_route(client) -> None:
    assert client.get('/api/health').json()['polar']['last_recovery'] is None
    assert client.get('/api/health/polar/tests/recovery/last').json() is None
    assert client.get('/api/health/polar/tests/walk/last').status_code == 404
    t = np.arange(330, dtype=float)
    session_id = stored_session('2026-07-02T08:00:00+00:00', np.where(t <= 182, 150.0, 150.0 - 0.5 * (t - 182)).tolist(),
                                np.where(t < 190, 200.0, 8.0).tolist())
    client.post(f'/api/health/polar/sessions/{session_id}/label', json={'activity_id': 7})
    card = client.get('/api/health').json()['polar']['last_recovery']
    assert (card['session_id'], card['hr_stop'], card['drop_60'], card['drop_120']) == (session_id, 149.7, 29.7, 59.7)
    assert client.get(f'/api/health/polar/sessions/{session_id}').json()['recovery']['stop_from'] == 'highest HR'
    days = {r['day']: r for r in client.get('/api/health/history?days=7').json()['days']}
    assert {'h10:hr_stop': 149.7, 'h10:drop_60': 29.7, 'h10:drop_120': 59.7}.items() <= days['2026-07-02'].items()
    assert [p for p in client.get('/api/health/history?days=7').json()['points'] if 'h10:drop_60' in p] == [
        {'at': '2026-07-02T08:00:00+00:00', 'day': '2026-07-02', 'h10:hr_stop': 149.7, 'h10:drop_60': 29.7, 'h10:drop_120': 59.7}]
    assert client.get('/api/health/polar/tests/recovery/last').json()['session_id'] == session_id
    assert client.get('/api/health/analysis?key=h10:drop_60').status_code == 200
    assert client.get('/api/health/polar/tests/hrv/last').json() is None


def test_a_stairs_test_shows_on_the_card_in_the_detail_the_history_and_the_last_test_route(client) -> None:
    assert client.get('/api/health').json()['polar']['last_stairs'] is None
    assert client.get('/api/health/polar/tests/stairs/last').json() is None
    t = np.arange(360, dtype=float)
    start_at = '2026-07-02T08:00:00+00:00'
    session_id = stored_session(start_at, np.where(t <= 200, 100.0 + t * 0.3, 160.0 - 0.5 * (t - 200)).tolist(), None)
    start_s = int(datetime.fromisoformat(start_at).timestamp())
    with ledger.connect(write=True) as db:
        db.executemany('INSERT INTO polar_markers (session_id, t_ms, label, hr) VALUES (?, ?, ?, NULL)',
                       [(session_id, (start_s + second) * 1000, label) for second, label in (
                           (20, 'marker start'), (60, 'marker stop'), (75, 'marker start'), (200, 'marker stop'))])
    client.post(f'/api/health/polar/sessions/{session_id}/label', json={'activity_id': 8})
    card = client.get('/api/health').json()['polar']['last_stairs']
    assert (card['session_id'], card['climb_s'], card['pauses'], card['pause_s'], card['drop_60']) == (session_id, 180, 1, 15, 29.5)
    detail = client.get(f'/api/health/polar/sessions/{session_id}').json()
    assert detail['stairs']['pause_list'] == [{'at_s': 60, 'seconds': 15}] and detail['recovery'] is None
    days = {r['day']: r for r in client.get('/api/health/history?days=7').json()['days']}
    assert {'h10:stairs_time': 180, 'h10:stairs_pauses': 1, 'h10:stairs_drop_60': 29.5}.items() <= days['2026-07-02'].items()
    assert [p['h10:stairs_pauses'] for p in client.get('/api/health/history?days=7').json()['points']
            if 'h10:stairs_pauses' in p] == [1]
    assert client.get('/api/health/polar/tests/stairs/last').json()['session_id'] == session_id
    assert client.get('/api/health/analysis?key=h10:stairs_time').status_code == 200
