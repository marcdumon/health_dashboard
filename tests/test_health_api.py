"""The Health routes over HTTP: the day view, the supplements (their entries, moments, regime and the effect of a change), the
angina score, the history, and the Withings sign-in and fetch against a stand-in Withings."""

from datetime import datetime
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from health_dashboard import api, store, withings

TZ = ZoneInfo('Europe/Brussels')
NOW = datetime(2026, 10, 8, 12, 41, tzinfo=TZ)
TOKEN = 'https://wbsapi.withings.net/v2/oauth2'
MEASURE = 'https://wbsapi.withings.net/measure'


@pytest.fixture
def client(monkeypatch) -> TestClient:
    """A client at 12:41 on 8 October, with the Withings codes set and background work done at once."""
    monkeypatch.setattr(api, '_health_now', lambda: NOW)
    monkeypatch.setattr(api, '_in_background', lambda work, *args: work(*args))
    monkeypatch.setattr(withings, 'credentials', lambda: ('cid', 'secret'))
    return TestClient(api.app, follow_redirects=False)


def supplement(view: dict, name: str) -> dict:
    """One supplement of the card of the day view."""
    return next(s for s in view['supplements']['items'] if s['name'] == name)


def regime(answer: dict, name: str) -> dict:
    """One supplement of the regime an answer gives."""
    return next(s for s in answer['regime']['items'] if s['name'] == name)


def test_the_day_shows_the_supplements_the_regime_and_the_angina_score(client) -> None:
    view = client.get('/api/health').json()
    assert (view['day'], view['today'], view['now']) == ('2026-10-08', '2026-10-08', '12:41')
    assert [m['name'] for m in view['supplements']['moments']][:3] == ['First meal', 'Midday', 'Afternoon']
    view = client.post('/api/health/supplements', json={'day': '2026-10-08', 'name': 'Calcium', 'dose_unit': 'mg', 'step': 100}).json()
    calcium = regime(view, 'Calcium')
    answer = client.put(f'/api/health/supplements/{calcium["id"]}', json={
        'day': '2026-10-08', 'name': 'Calcium', 'dose_unit': 'mg', 'step': 100, 'plan': {'First meal': 200}}).json()
    assert regime(answer, 'Calcium')['plan']['First meal'] == 200, 'a change of the regime gives the regime of its day'
    assert supplement(client.get('/api/health').json(), 'Calcium')['planned'] == 200
    view = client.post('/api/health/moments', json={'day': '2026-10-08', 'moment': 'First meal', 'taken_at': '09:10'}).json()
    assert view['supplements']['moments'][0]['taken_at'] == '09:10' and supplement(view, 'Calcium')['total'] == 200
    view = client.delete('/api/health/moments', params={'day': '2026-10-08', 'moment': 'First meal'}).json()
    assert supplement(view, 'Calcium')['entries'] == []
    view = client.post('/api/health/intakes', json={'day': '2026-10-08', 'supplement_id': calcium['id'], 'taken_at': '12:23'}).json()
    entry = supplement(view, 'Calcium')['entries'][0]
    assert (entry['taken_at'], entry['amount']) == ('12:23', 100), 'one dose'
    view = client.put(f'/api/health/intakes/{entry["id"]}', json={'amount': 300}).json()
    assert supplement(view, 'Calcium')['total'] == 300 and view['day'] == '2026-10-08'
    assert supplement(client.delete(f'/api/health/intakes/{entry["id"]}').json(), 'Calcium')['entries'] == []
    view = client.put('/api/health/angina', json={'day': '2026-10-08', 'score': 2, 'note': 'stairs'}).json()
    assert view['angina'] == {'score': 2, 'note': 'stairs', 'not_measured': False}
    view = client.put('/api/health/angina', json={'day': '2026-10-08', 'note': '', 'not_measured': True}).json()
    assert view['angina'] == {'score': None, 'note': '', 'not_measured': True}
    assert view['withings']['connected'] is False and view['latest']['pressure'] is None


def test_a_wrong_entry_an_unknown_supplement_or_a_future_day_is_refused(client) -> None:
    view = client.post('/api/health/supplements', json={'day': '2026-10-08', 'name': 'Calcium', 'dose_unit': 'mg', 'step': 100}).json()
    entry = {'day': '2026-10-08', 'supplement_id': regime(view, 'Calcium')['id'], 'taken_at': '08:00'}
    assert client.post('/api/health/intakes', json=entry | {'taken_at': '8h'}).status_code == 400
    assert client.post('/api/health/intakes', json=entry | {'amount': 0}).status_code == 400
    assert client.post('/api/health/intakes', json=entry | {'supplement_id': 999}).status_code == 404
    assert client.post('/api/health/intakes', json=entry | {'day': '2026-10-09'}).status_code == 400
    assert client.put('/api/health/intakes/999', json={'amount': 1}).status_code == 404
    assert client.delete('/api/health/intakes/999').status_code == 404
    moment = {'day': '2026-10-08', 'moment': 'Midday', 'taken_at': '12:00'}
    assert client.post('/api/health/moments', json=moment).status_code == 400, 'nothing planned'
    assert client.get('/api/health', params={'day': '2026-10-09'}).status_code == 400
    assert client.put('/api/health/angina', json={'day': '2026-10-08', 'score': 6, 'note': ''}).status_code == 400
    both = {'day': '2026-10-08', 'score': 2, 'note': '', 'not_measured': True}
    assert client.put('/api/health/angina', json=both).status_code == 400


def test_a_supplement_is_added_changed_and_removed(client) -> None:
    given = {'day': '2026-10-08', 'name': 'Vitamin C', 'dose_unit': 'mg', 'step': 500}
    added = regime(client.post('/api/health/supplements', json=given).json(), 'Vitamin C')
    assert (added['dose_unit'], added['step'], added['plan']['Afternoon']) == ('mg', 500, 0)
    change = given | {'step': 1000, 'plan': {'Afternoon': 1000}}
    view = client.put(f'/api/health/supplements/{added["id"]}', json=change).json()
    assert (regime(view, 'Vitamin C')['step'], regime(view, 'Vitamin C')['plan']['Afternoon']) == (1000, 1000)
    view = client.delete(f'/api/health/supplements/{added["id"]}', params={'day': '2026-10-08'}).json()
    assert 'Vitamin C' not in [s['name'] for s in view['regime']['items']]
    assert client.post('/api/health/supplements', json=given | {'dose_unit': 'mmol'}).status_code == 400
    assert client.post('/api/health/supplements', json=given | {'day': 'tomorrow'}).status_code == 400
    assert client.put('/api/health/supplements/999', json=change).status_code == 404
    assert client.put(f'/api/health/supplements/{added["id"]}', json=change | {'plan': {'Brunch': 1}}).status_code == 404, \
        'removed from the list'
    assert client.delete('/api/health/supplements/999', params={'day': '2026-10-08'}).status_code == 404


def test_the_supplement_values_of_the_history_and_each_against_a_value(client) -> None:
    view = client.post('/api/health/supplements', json={'day': '2026-10-01', 'name': 'Biotin', 'dose_unit': 'mg',
                                                         'step': 750}).json()
    biotin = regime(view, 'Biotin')['id']
    client.post('/api/health/intakes', json={'day': '2026-10-01', 'supplement_id': biotin, 'taken_at': '15:00'})
    history = client.get('/api/health/history', params={'days': 30}).json()
    assert history['supplements'] == [{'key': f'supp:{biotin}', 'name': 'Biotin', 'unit': 'mg'}]
    assert next(r for r in history['days'] if r['day'] == '2026-10-01')[f'supp:{biotin}'] == 750
    assert client.get('/api/health/analysis', params={'key': f'supp:{biotin}', 'days': 30}).json()['stats']['count'] == 1
    params = {'key': 'systolic', 'days': 30, 'lag': 1}
    found = client.get('/api/health/supplement-relations', params=params).json()['relations']
    assert [(f['name'], f['n']) for f in found] == [('Biotin', 0)], 'taken, but no blood pressure yet'
    assert client.get('/api/health/supplement-relations', params=params | {'key': 'nothing'}).status_code == 404
    assert client.get('/api/health/supplement-relations', params=params | {'key': 'h10:mean_hr'}).status_code == 400


def test_the_regime_of_any_day_and_a_change_planned_for_a_later_day(client) -> None:
    answer = client.post('/api/health/supplements', json={'day': '2026-10-01', 'name': 'Vitamin E', 'dose_unit': 'µg',
                                                           'step': 200}).json()
    vit_e = regime(answer, 'Vitamin E')['id']
    change = {'name': 'Vitamin E', 'dose_unit': 'µg', 'step': 200}
    client.put(f'/api/health/supplements/{vit_e}', json=change | {'day': '2026-10-01', 'plan': {'First meal': 200}})
    answer = client.put(f'/api/health/supplements/{vit_e}', json=change | {'day': '2026-10-20', 'plan': {'First meal': 800}}).json()
    assert regime(answer, 'Vitamin E')['plan']['First meal'] == 800, 'a day that has not come yet'
    assert regime(client.get('/api/health/regime', params={'day': '2026-10-19'}).json(), 'Vitamin E')['plan']['First meal'] == 200
    assert regime(client.get('/api/health/regime', params={'day': '2026-10-03'}).json(), 'Vitamin E')['plan']['First meal'] == 200
    assert regime(client.get('/api/health/regime').json(), 'Vitamin E')['plan']['First meal'] == 200, 'today without a day'
    assert supplement(client.get('/api/health').json(), 'Vitamin E')['planned'] == 200, 'today keeps its plan'
    assert [(c['day'], c['after']) for c in client.get('/api/health/regime').json()['regime']['changes']] == [
        ('2026-10-20', 800), ('2026-10-01', 200)], 'the planned change too, newest first'
    added = client.post('/api/health/supplements', json={'day': '2026-10-15', 'name': 'Zinc', 'dose_unit': 'mg', 'step': 25}).json()
    assert 'Zinc' in [s['name'] for s in added['regime']['items']]
    assert 'Zinc' not in [s['name'] for s in client.get('/api/health/regime').json()['regime']['items']], 'from its day on'
    assert client.get('/api/health/regime', params={'day': 'soon'}).status_code == 400


def test_an_earlier_day_shows_the_withings_values_up_to_its_end(client) -> None:
    from health_dashboard import ledger

    with ledger.connect(write=True) as db:
        db.executemany('INSERT INTO withings_values (grpid, type, position, value, measured_at) VALUES (?, 1, -1, ?, ?)',
                       [(1, 85, '2026-10-06T08:00'), (2, 84, '2026-10-08T03:30'), (3, 83, '2026-10-08T07:00')])
    assert client.get('/api/health', params={'day': '2026-10-06'}).json()['latest']['values']['weight']['value'] == 85.0
    assert client.get('/api/health', params={'day': '2026-10-07'}).json()['latest']['values']['weight']['value'] == 85.0, \
        'a weigh-in at 03:30 belongs to the new day'
    assert client.get('/api/health').json()['latest']['values']['weight']['value'] == 83.0


def test_the_history_has_a_row_per_day(client) -> None:
    rows = client.get('/api/health/history', params={'days': 30}).json()['days']
    assert len(rows) == 30 and rows[-1]['day'] == '2026-10-08'
    assert client.get('/api/health/history', params={'days': 2000}).status_code == 422


@respx.mock
def test_connect_signs_in_at_withings_and_comes_back_to_the_page(client) -> None:
    respx.post(TOKEN).mock(return_value=httpx.Response(200, json={'status': 0, 'body': {
        'access_token': 'a1', 'refresh_token': 'r1', 'expires_in': 10800}}))
    respx.post(MEASURE).mock(return_value=httpx.Response(200, json={'status': 0, 'body': {
        'updatetime': 1, 'more': 0, 'timezone': 'Europe/Brussels',
        'measuregrps': [{'grpid': 7, 'date': 1_791_400_000, 'measures': [{'type': 10, 'value': 131, 'unit': 0},
                                                                         {'type': 9, 'value': 84, 'unit': 0}]}]}}))
    away = client.get('/api/health/withings/connect')
    assert away.status_code == 307 and away.headers['location'].startswith(withings.AUTHORIZE_URL)
    state = parse_qs(urlsplit(away.headers['location']).query)['state'][0]
    back = client.get('/api/health/withings/callback', params={'code': 'c1', 'state': state})
    assert back.status_code == 307 and back.headers['location'] == 'http://127.0.0.1:8766/'
    view = client.get('/api/health').json()
    assert view['withings']['connected'] is True and view['latest']['pressure'] is None, 'the sign-in downloads nothing'
    view = client.post('/api/health/withings/fetch').json()
    assert view['latest']['pressure']['systolic'] == 131.0, 'only Fetch now downloads'


def test_a_refused_or_strange_callback_is_said_on_the_page(client) -> None:
    refused = client.get('/api/health/withings/callback', params={'error': 'access_denied', 'state': 'x'})
    assert refused.headers['location'] == 'http://127.0.0.1:8766/'
    assert 'did not allow' in client.get('/api/health').json()['withings']['error']
    client.get('/api/health/withings/callback', params={'code': 'c1', 'state': 'not-ours'})
    assert 'Connect Withings' in client.get('/api/health').json()['withings']['error']


@respx.mock
def test_fetch_now_needs_a_sign_in_and_waits_ten_minutes(client, monkeypatch) -> None:
    assert client.post('/api/health/withings/fetch').status_code == 409
    store.set_setting('withings_access_token', 'a1')
    store.set_setting('withings_refresh_token', 'r1')
    store.set_setting('withings_expires_at', '9e18')
    route = respx.post(MEASURE).mock(return_value=httpx.Response(200, json={'status': 0, 'body': {'updatetime': 1, 'more': 0,
                                                                                                    'measuregrps': []}}))
    assert client.post('/api/health/withings/fetch').status_code == 200 and route.call_count == 1
    again = client.post('/api/health/withings/fetch')
    assert again.status_code == 429 and '10 minutes' in again.json()['detail']


def test_a_wrong_weigh_in_is_removed_from_its_day_and_put_back(client) -> None:
    from health_dashboard import ledger

    with ledger.connect(write=True) as db:
        db.executemany('INSERT INTO withings_values (grpid, type, position, value, measured_at) VALUES (?, ?, -1, ?, ?)',
                       [(1, 10, 117, '2026-10-07T07:33'), (1, 9, 78, '2026-10-07T07:33'), (2, 1, 88.9, '2026-10-07T14:43'),
                        (2, 6, 9.7, '2026-10-07T14:43'), (3, 11, 96, '2026-10-07T14:43')])
    found = client.get('/api/health/days/2026-10-07/measurements').json()['measurements']
    assert [(m['kind'], m['grpids']) for m in found] == [('scale', [2, 3]), ('pressure', [1])]
    removed = client.post('/api/health/measurements/hide', json={'grpids': [2, 3]}).json()['removed']
    assert [(m['day'], m['measured_at']) for m in removed] == [('2026-10-07', '2026-10-07T14:43')]
    history = client.get('/api/health/history', params={'days': 7}).json()
    row = next(r for r in history['days'] if r['day'] == '2026-10-07')
    assert 'weight' not in row and row['systolic'] == 117.0 and len(history['removed']) == 1
    assert client.post('/api/health/measurements/show', json={'grpids': [2, 3]}).json()['removed'] == []
    assert client.get('/api/health/days/2026-10-09/measurements').status_code == 400, 'a day to come'


def test_the_analysis_of_a_value_over_all_data_and_a_strange_name(client) -> None:
    from health_dashboard import ledger

    with ledger.connect(write=True) as db:
        db.executemany('INSERT INTO withings_values (grpid, type, position, value, measured_at) VALUES (?, 1, -1, ?, ?)',
                       [(1, 85, '2015-03-02T08:00'), (2, 89, '2026-10-08T08:00')])
    found = client.get('/api/health/analysis', params={'key': 'weight', 'window': 30}).json()
    assert found['start'] == '2015-03-02' and [p['value'] for p in found['points']] == [85.0, 89.0], 'all data'
    assert client.get('/api/health/analysis', params={'key': 'weight', 'days': 90}).json()['start'] == '2026-07-11'
    assert client.get('/api/health/analysis', params={'key': 'nothing'}).status_code == 404
    assert client.get('/api/health/analysis', params={'key': 'weight', 'window': 1}).status_code == 422


def test_a_ketostix_reading_is_added_at_a_time_changed_and_removed(client) -> None:
    view = client.get('/api/health').json()
    assert [k['label'] for k in view['ketostix']][:2] == ['Negative', 'Trace'] and view['ketones'] == []
    view = client.post('/api/health/ketones', json={'day': '2026-10-08', 'level': 1.5, 'taken_at': '07:10'}).json()
    reading = view['ketones'][0]
    assert (reading['taken_at'], reading['level']) == ('07:10', 1.5)
    view = client.put(f'/api/health/ketones/{reading["id"]}', json={'taken_at': '07:40'}).json()
    assert view['ketones'][0]['taken_at'] == '07:40' and view['day'] == '2026-10-08'
    assert client.delete(f'/api/health/ketones/{reading["id"]}').json()['ketones'] == []
    assert client.post('/api/health/ketones', json={'day': '2026-10-08', 'level': 3, 'taken_at': '07:10'}).status_code == 400
    assert client.post('/api/health/ketones', json={'day': '2026-10-09', 'level': 0.5, 'taken_at': '07:10'}).status_code == 400
    assert client.delete('/api/health/ketones/999').status_code == 404


def test_the_relation_of_two_values_and_a_strange_name(client) -> None:
    assert client.get('/api/health/relation', params={'key': 'angina', 'other': 'systolic', 'days': 90}).json()['n'] == 0
    assert client.get('/api/health/relation', params={'key': 'angina', 'other': 'nothing'}).status_code == 404
    assert client.get('/api/health/relation', params={'key': 'angina', 'other': 'systolic', 'lag': 30}).json()['lag'] == 30
    assert client.get('/api/health/relation', params={'key': 'angina', 'other': 'systolic', 'lag': 31}).status_code == 422


@respx.mock
def test_opening_the_page_never_downloads_from_withings(client) -> None:
    store.set_setting('withings_access_token', 'a1')
    store.set_setting('withings_refresh_token', 'r1')
    store.set_setting('withings_expires_at', '9e18')
    route = respx.post(MEASURE).mock(return_value=httpx.Response(200, json={'status': 0, 'body': {'updatetime': 1, 'more': 0,
                                                                                                    'measuregrps': []}}))
    for _ in range(3):
        view = client.get('/api/health').json()
    assert route.call_count == 0 and not view['withings'].get('fetching'), 'a page read has no side effects'


def test_the_tape_measurements_are_set_and_removed(client) -> None:
    view = client.put('/api/health/tape', json={'day': '2026-10-01', 'site': 'waist', 'cm': 91.8}).json()
    assert view['tape'] == {'values': {'waist': 91.8}, 'last': None}
    assert client.put('/api/health/tape', json={'day': '2026-10-01', 'site': 'waist', 'cm': 300}).status_code == 400
    assert client.put('/api/health/tape', json={'day': '2026-10-01', 'site': 'nose', 'cm': 12}).status_code == 400
    assert client.get('/api/health').json()['tape'] == {'values': {}, 'last': {'day': '2026-10-01', 'values': {'waist': 91.8}}}
    view = client.put('/api/health/tape', json={'day': '2026-10-01', 'site': 'waist', 'cm': None}).json()
    assert view['tape']['values'] == {}


def test_the_regime_of_a_day_is_copied_to_another_day(client) -> None:
    view = client.post('/api/health/supplements', json={'day': '2026-10-01', 'name': 'Calcium', 'dose_unit': 'mg', 'step': 100}).json()
    calcium = regime(view, 'Calcium')
    client.put(f'/api/health/supplements/{calcium["id"]}', json={
        'day': '2026-10-01', 'name': 'Calcium', 'dose_unit': 'mg', 'step': 100, 'plan': {'First meal': 200}})
    client.put(f'/api/health/supplements/{calcium["id"]}', json={
        'day': '2026-10-05', 'name': 'Calcium', 'dose_unit': 'mg', 'step': 100, 'plan': {'First meal': 400}})
    answer = client.post('/api/health/regime/copy', json={'from_day': '2026-10-01', 'to_day': '2026-10-10'}).json()
    assert regime(answer, 'Calcium')['plan']['First meal'] == 200, 'the answer is the regime of the day copied to'
    answer = client.post('/api/health/regime/copy', json={'from_day': '2026-10-10', 'to_day': '2026-10-10'})
    assert answer.status_code == 400 and 'two different days' in answer.json()['detail']
