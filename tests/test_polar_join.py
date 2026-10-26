"""Polar H10 sessions that a glitch of the logger app cut in two, joined into one: the first one counts for all of them, with
the seconds of each recording, the span as its length, and the mean and maximum HR of its seconds; the minutes, the energy
and the load cover every recording, a new read keeps the join, Split takes it apart, and joins that do not fit are refused."""

from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from health_dashboard import api, polar_energy, polar_load, polar_store
from tests.polar_fixtures import set_person, stored_session

DAY = date(2026, 10, 26)
A_START = datetime(2026, 10, 26, 11, 24, 4, tzinfo=UTC)


def at(start: datetime, seconds: int = 0) -> str:
    """A UTC time as the store keeps it."""
    return (start + timedelta(seconds=seconds)).isoformat(timespec='seconds')


def activity(name: str) -> int:
    """The id of an activity of the sessions."""
    return next(a['id'] for a in polar_store.activities() if a['name'] == name)


def two_parts() -> tuple[int, int]:
    """10 minutes at 100 bpm, 20 s without a recording, then 5 minutes at 120 bpm; the first one is Gym cardio."""
    first = stored_session(at(A_START), [100.0] * 600, None)
    second = stored_session(at(A_START, 620), [120.0] * 300, None)
    polar_store.set_activity(first, activity('Gym cardio'))
    return first, second


def test_joined_sessions_count_as_the_first_one_with_the_seconds_of_each_recording() -> None:
    first, second = two_parts()
    assert polar_store.join([second, first]) == first
    found = polar_store.session(first)
    assert (found['seconds'], found['end_at'], found['max_hr'], found['recordings']) == (920, at(A_START, 919), 120.0, 2)
    assert found['mean_hr'] == round((600 * 100 + 300 * 120) / 900, 1), 'the mean of the seconds with an HR'
    assert found['activity'] == 'Gym cardio'
    assert [s['id'] for s in polar_store.day_sessions(DAY.isoformat())] == [first]
    assert polar_store.sessions(0, 10)['total'] == 1
    seconds = polar_store.seconds(first)
    assert [s['t'] for s in seconds] == list(range(seconds[0]['t'], seconds[0]['t'] + 920)), 'one row for each second'
    assert {s['hr'] for s in seconds[600:620]} == {None}, 'the seconds between the recordings have no HR'
    assert polar_store.weeks(DAY, DAY)[0]['minutes'] == {'Gym cardio': 15}


def test_the_energy_and_the_load_cover_every_recording(monkeypatch) -> None:
    set_person(monkeypatch, birth_date='1960-05-01', sex='male', weight_kg=80, height_cm=180)
    first, second = two_parts()
    before = {s['id']: s for s in polar_load._sessions(DAY, DAY)}
    polar_store.join([first, second])
    assert polar_energy._seconds([first], 0) == {first: (900, 600 * 100 + 300 * 120)}
    after = polar_load._sessions(DAY, DAY)
    assert [s['id'] for s in after] == [first]
    assert after[0]['trimp'] == pytest.approx(before[first]['trimp'] + before[second]['trimp'])


def test_a_new_read_keeps_the_join_and_split_gives_back_each_session() -> None:
    first, second = two_parts()
    polar_store.join([first, second])
    assert stored_session(at(A_START, 620), [120.0] * 300, None) == second
    assert stored_session(at(A_START), [100.0] * 600, None) == first
    assert (polar_store.session(first)['seconds'], polar_store.session(first)['recordings']) == (920, 2)
    polar_store.split(first)
    found = polar_store.session(first)
    assert (found['seconds'], found['end_at'], found['mean_hr'], found['max_hr'], found['recordings']) == (
        600, at(A_START, 599), 100.0, 100.0, 1)
    assert polar_store.sessions(0, 10)['total'] == 2 and polar_store.session(second)['mean_hr'] == 120.0
    with pytest.raises(polar_store.JoinError, match='not joined'):
        polar_store.split(first)


def test_a_join_that_does_not_fit_is_refused_and_changes_nothing() -> None:
    first, second = two_parts()
    later = stored_session(at(A_START, 3600), [90.0] * 300, None)
    with pytest.raises(polar_store.JoinError, match='at least two'):
        polar_store.join([first])
    with pytest.raises(polar_store.JoinError, match='between'):
        polar_store.join([first, later])
    overlap = stored_session(at(A_START, 300), [95.0] * 200, None, path='/phone/overlap_HR.txt', serial='OTHER')
    with pytest.raises(polar_store.JoinError, match='overlap'):
        polar_store.join([first, overlap])
    polar_store.set_removed([overlap], True)
    with pytest.raises(polar_store.JoinError, match='removed'):
        polar_store.join([first, overlap])
    test = stored_session(at(A_START, 7200), [70.0] * 300, None)
    polar_store.set_activity(test, activity('HRV'))
    with pytest.raises(polar_store.JoinError, match='test'):
        polar_store.join([later, test])
    with pytest.raises(KeyError):
        polar_store.join([first, 999])
    assert polar_store.session(first)['recordings'] == 1 and polar_store.session(second)['seconds'] == 300
    polar_store.join([first, second])
    with pytest.raises(polar_store.JoinError, match='already'):
        polar_store.join([second, later])


def test_the_routes_join_and_split(monkeypatch) -> None:
    client = TestClient(api.app)
    first, second = two_parts()
    assert client.post('/api/health/polar/sessions/join', json={'ids': [first, second]}).json() == {'ok': True}
    assert client.get('/api/health/polar/sessions').json()['sessions'][0]['recordings'] == 2
    answer = client.post('/api/health/polar/sessions/join', json={'ids': [first, second]})
    assert answer.status_code == 400 and 'already' in answer.json()['detail']
    assert client.post(f'/api/health/polar/sessions/{first}/split').json() == {'ok': True}
    assert client.post(f'/api/health/polar/sessions/{first}/split').status_code == 400
    assert client.post('/api/health/polar/sessions/999/split').status_code == 404
