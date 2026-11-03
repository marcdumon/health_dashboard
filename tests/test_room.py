"""The room temperature: the buttons of the card keep the sensor and the steps in the database, against a stand-in Home
Assistant; a blood pressure reading gets the temperature of its time, and nothing else asks Home Assistant."""

from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from health_dashboard import analysis, api, history, ledger, measurements, room, store
from health_dashboard import config as config_module

TZ = ZoneInfo('Europe/Brussels')
NOW = datetime(2026, 11, 3, 7, 12, tzinfo=TZ)
URL = 'http://ha.test'
ENTITY = 'sensor.test_room_temperature'
PERIOD = f'{URL}/api/history/period/'
STATES = f'{URL}/api/states'
EXAMPLE = Path(__file__).parents[1] / 'health_dashboard.example.toml'


@pytest.fixture
def home_assistant(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The address of a Home Assistant and a token, without a sensor."""
    path = tmp_path / 'health_dashboard.toml'
    path.write_text(EXAMPLE.read_text().replace("url = ''", f"url = '{URL}'"))
    monkeypatch.setattr(config_module, 'CONFIG_FILE', path)
    config_module.config.cache_clear()
    monkeypatch.setattr(room, '_token', lambda: 'test-token')


@pytest.fixture
def sensor(home_assistant: None) -> None:
    """The sensor of the room is chosen."""
    room.set_sensor(ENTITY, 'Test room')


def states(*found: tuple[str, str]) -> httpx.Response:
    """A history reply of Home Assistant: (value, time in UTC)."""
    return httpx.Response(200, json=[[{'state': value, 'last_changed': f'{at}+00:00'} for value, at in found]])


def stored() -> dict[str, float]:
    """The steps in the database."""
    with ledger.connect() as db:
        return {row[0]: row[1] for row in db.execute('SELECT at, celsius FROM room_temperature')}


def keep_steps(*steps: tuple[str, float]) -> None:
    """Steps in the database."""
    with ledger.connect(write=True) as db:
        db.executemany('INSERT INTO room_temperature (at, celsius) VALUES (?, ?)', steps)


def reading(grpid: int, at: str, systolic: float = 120, hidden: bool = False) -> None:
    """A blood pressure reading."""
    with ledger.connect(write=True) as db:
        db.executemany('INSERT INTO withings_values (grpid, type, position, value, measured_at) VALUES (?, ?, ?, ?, ?)',
                       [(grpid, 10, -1, systolic, at), (grpid, 9, -1, 80, at)])
        if hidden:
            db.execute('INSERT INTO withings_hidden (grpid, hidden_at) VALUES (?, ?)', (grpid, at))


@respx.mock
def test_a_download_keeps_the_mean_of_every_step_in_local_time(sensor: None) -> None:
    route = respx.get(url__startswith=PERIOD).mock(return_value=states(
        ('20.0', '2026-11-03T06:01:00'), ('21.0', '2026-11-03T06:04:59'), ('18.5', '2026-11-03T06:06:00')))
    assert room.download(NOW) == 3
    assert stored()['2026-11-03T07:00'] == 20.5
    request = route.calls[0].request
    assert request.headers['authorization'] == 'Bearer test-token' and request.url.params['filter_entity_id'] == ENTITY
    assert room.card('2026-11-03')['downloaded_at'] == '2026-11-03T07:12+01:00'


@respx.mock
def test_a_value_holds_until_the_next_state_and_a_sensor_that_is_off_holds_none(sensor: None) -> None:
    respx.get(url__startswith=PERIOD).mock(return_value=states(
        ('20.0', '2026-11-03T04:00:00'), ('unavailable', '2026-11-03T04:12:00'), ('22.0', '2026-11-03T04:26:00')))
    room.download(datetime(2026, 11, 3, 5, 36, tzinfo=TZ))
    assert stored() == {'2026-11-03T05:00': 20.0, '2026-11-03T05:05': 20.0, '2026-11-03T05:10': 20.0, '2026-11-03T05:25': 22.0,
                        '2026-11-03T05:30': 22.0, '2026-11-03T05:35': 22.0}


@respx.mock
def test_the_next_download_starts_at_the_newest_step_and_computes_it_again(sensor: None) -> None:
    keep_steps(('2026-11-03T07:00', 19.0), ('2026-11-03T07:05', 25.0))
    route = respx.get(url__startswith=PERIOD).mock(return_value=states(
        ('20.0', '2026-11-03T06:05:00'), ('22.0', '2026-11-03T06:10:00')))
    room.download(NOW)
    assert '2026-11-03T07:05:00' in str(route.calls[0].request.url.path)
    assert stored() == {'2026-11-03T07:00': 19.0, '2026-11-03T07:05': 20.0, '2026-11-03T07:10': 21.0}


def test_without_the_settings_the_card_says_what_to_set_and_a_download_refuses() -> None:
    found = room.card('2026-11-03')
    assert (found['configured'], found['sensor'], found['downloaded_at'], found['day']) == (False, None, None, None)
    assert found['message'] == room.NOT_SET
    with pytest.raises(room.RoomError, match='HA_TOKEN'):
        room.download(NOW)
    with pytest.raises(room.RoomError, match='HA_TOKEN'):
        room.sensors()


def test_without_a_sensor_a_download_asks_to_choose_one(home_assistant: None) -> None:
    assert room.card('2026-11-03')['configured'] is True
    with pytest.raises(room.RoomError, match='Choose sensor'):
        room.download(NOW)


@respx.mock
@pytest.mark.parametrize(('reply', 'said'), [
    (httpx.Response(401), 'refused the token'), (httpx.Response(500), 'error 500'), (httpx.Response(200, json=[]), 'no history of'),
    (httpx.ConnectError('down'), 'does not answer')])
def test_a_failure_says_what_to_do_and_keeps_nothing(sensor: None, reply: object, said: str) -> None:
    respx.get(url__startswith=PERIOD).mock(side_effect=[reply])
    with pytest.raises(room.RoomError, match=said):
        room.download(NOW)
    assert stored() == {} and room.card('2026-11-03')['downloaded_at'] is None


@respx.mock
def test_the_list_has_only_the_temperature_sensors_by_name(home_assistant: None) -> None:
    def state(entity: str, name: str, value: str, kind: str = 'temperature') -> dict:
        return {'entity_id': entity, 'state': value, 'attributes': {'friendly_name': name, 'device_class': kind}}

    respx.get(STATES).mock(return_value=httpx.Response(200, json=[
        state('sensor.b', 'Switch  B', '19.5'), state('sensor.a', 'a room', 'unavailable'),
        state('sensor.c', 'Humidity', '50', 'humidity')]))
    assert room.sensors() == [{'entity': 'sensor.a', 'name': 'a room', 'celsius': None},
                              {'entity': 'sensor.b', 'name': 'Switch B', 'celsius': 19.5}]


@respx.mock
def test_another_sensor_counts_from_the_next_download_and_the_stored_temperatures_stay(sensor: None) -> None:
    keep_steps(('2026-11-03T07:00', 19.0), ('2026-11-03T07:05', 25.0))
    room.set_sensor('sensor.other', 'Other')
    assert room.card('2026-11-03')['sensor'] == {'entity': 'sensor.other', 'name': 'Other'}
    route = respx.get(url__startswith=PERIOD).mock(return_value=states(('30.0', '2026-11-03T06:05:00')))
    room.download(NOW)
    assert route.calls[0].request.url.params['filter_entity_id'] == 'sensor.other'
    assert stored() == {'2026-11-03T07:00': 19.0, '2026-11-03T07:05': 30.0, '2026-11-03T07:10': 30.0}


def test_a_reading_has_the_last_step_before_it_not_too_old_and_not_a_removed_one() -> None:
    keep_steps(('2026-11-02T23:55', 15.0), ('2026-11-03T08:00', 18.0), ('2026-11-03T08:05', 19.0))
    reading(1, '2026-11-03T00:10')
    reading(2, '2026-11-03T08:07')
    reading(3, '2026-11-03T08:05')
    reading(4, '2026-11-03T08:21')
    reading(5, '2026-11-03T08:01', hidden=True)
    assert room.at_readings('2026-11-03', '2026-11-03') == {
        '2026-11-03T00:10': 15.0, '2026-11-03T08:07': 19.0, '2026-11-03T08:05': 19.0}


def test_a_day_of_the_history_has_the_mean_at_its_readings_and_each_reading_its_own() -> None:
    keep_steps(('2026-11-03T08:00', 16.0), ('2026-11-03T20:00', 20.0))
    reading(1, '2026-11-03T08:02', 130)
    reading(2, '2026-11-03T20:02', 120)
    [row] = [r for r in history.days(7, date(2026, 11, 3)) if r['day'] == '2026-11-03']
    assert row['room_temperature'] == 18.0
    points = history.chart_points(date(2026, 11, 3), date(2026, 11, 3))
    assert [p['room_temperature'] for p in points if 'systolic' in p] == [16.0, 20.0]
    assert sorted(m['values']['room_temperature'] for m in measurements.day(date(2026, 11, 3))) == [16.0, 20.0]
    card = room.card('2026-11-03')['day']
    assert (card['celsius'], card['readings']) == (18.0, 2)


def test_the_relation_chart_sets_the_blood_pressure_against_the_room_temperature() -> None:
    assert analysis.known('room_temperature')
    for n, (celsius, systolic) in enumerate([(16.0, 134), (18.0, 130), (20.0, 126), (22.0, 122)], 1):
        keep_steps((f'2026-11-0{n}T08:00', celsius))
        reading(n, f'2026-11-0{n}T08:02', systolic)
    found = analysis.relation('systolic', 'room_temperature', 7, date(2026, 11, 4))
    assert (found['n'], found['r'], found['slope']) == (4, -1.0, -2.0)


@respx.mock
def test_only_the_buttons_ask_home_assistant(sensor: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api, '_health_now', lambda: NOW)
    route = respx.get(url__startswith=PERIOD).mock(return_value=states(('19.0', '2026-11-03T06:01:00')))
    listed = respx.get(STATES).mock(return_value=httpx.Response(200, json=[]))
    reading(1, '2026-11-03T07:02')
    client = TestClient(api.app)
    assert client.get('/api/health').json()['room']['day'] is None
    client.get('/api/health/history?days=7')
    client.get('/api/health/days/2026-11-03/measurements')
    assert not route.called and not listed.called
    view = client.post('/api/health/room/download').json()
    assert route.call_count == 1 and view['room']['day'] == {'celsius': 19.0, 'readings': 1}
    assert client.get('/api/health').json()['room']['day'] == {'celsius': 19.0, 'readings': 1} and route.call_count == 1
    assert store.setting(room.DOWNLOADED_KEY) == '2026-11-03T07:12+01:00'
    assert client.get('/api/health/room/sensors').json() == {'sensors': []} and listed.call_count == 1


def test_the_routes_choose_a_sensor_and_say_what_to_do(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api, '_health_now', lambda: NOW)
    client = TestClient(api.app)
    for reply in (client.post('/api/health/room/download'), client.get('/api/health/room/sensors')):
        assert reply.status_code == 409 and 'HA_TOKEN' in reply.json()['detail']
    view = client.put('/api/health/room/sensor', json={'entity': ENTITY, 'name': 'Test room'}).json()
    assert view['room']['sensor'] == {'entity': ENTITY, 'name': 'Test room'}
    assert client.put('/api/health/room/sensor', json={'entity': ''}).status_code == 422
