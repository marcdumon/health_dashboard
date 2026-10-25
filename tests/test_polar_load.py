"""The training load of the Polar H10 sessions that are not HRV tests: Edwards TRIMP from the zones, the acute (7 days) and
chronic (28 days, a week) load and their ratio once 28 days have sessions, the minutes of moderate (64-77 % HRmax) and
vigorous (from 77 %) activity per day and per week; nothing without a birth date; in the history, the detail and a route."""

from datetime import date, datetime

from fastapi.testclient import TestClient

from health_dashboard import api, history, polar_detail, polar_load, polar_store
from tests.polar_fixtures import set_person, stored_session

HRV = 6


def born_1966(monkeypatch) -> None:
    """Born in 1966: in 2026 HRmax 160, zone limits 80, 96, 112, 128 and 144, moderate from 102.4 and vigorous from 123.2."""
    set_person(monkeypatch, birth_date='1966-01-01')


def two_sessions() -> None:
    """On 1 July: 10 min at 100 (zone 2, light) and 5 min at 130 (zone 4, vigorous), then 20 min at 110 (zone 2,
    moderate); on 2 July an HRV test at 110 that counts nowhere."""
    stored_session('2026-07-01T08:00:00+00:00', [100.0] * 600 + [130.0] * 300, None)
    stored_session('2026-07-01T17:00:00+00:00', [110.0] * 1200, None)
    polar_store.set_activity(stored_session('2026-07-02T06:00:00+00:00', [110.0] * 600, None), HRV)


def test_the_day_load_is_edwards_trimp_with_the_minutes_of_moderate_and_vigorous_activity(monkeypatch) -> None:
    born_1966(monkeypatch)
    two_sessions()
    found = polar_load.day_values('2026-07-01', '2026-07-02')
    # TRIMP: 10 min x zone 2 + 5 min x zone 4 + 20 min x zone 2 = 80; the chronic load is 28 days / 4
    assert found['2026-07-01'] == {'load:trimp': 80.0, 'activity:moderate': 20.0, 'activity:vigorous': 5.0,
                                   'load:acute': 80.0, 'load:chronic': 20.0}
    assert found['2026-07-02'] == {'load:acute': 80.0, 'load:chronic': 20.0}, 'the HRV test counts nowhere'


def test_the_ratio_of_the_acute_and_chronic_load_needs_28_days_of_sessions(monkeypatch) -> None:
    born_1966(monkeypatch)
    stored_session('2026-06-01T08:00:00+00:00', [110.0] * 1200, None)
    stored_session('2026-06-28T08:00:00+00:00', [110.0] * 1200, None)
    found = polar_load.day_values('2026-06-27', '2026-06-28')
    assert 'load:ratio' not in found['2026-06-27'], 'the first session is only 26 days before'
    # acute 40 (the last 7 days), chronic (40 + 40) / 4 = 20
    assert (found['2026-06-28']['load:acute'], found['2026-06-28']['load:chronic'], found['2026-06-28']['load:ratio']) == (
        40.0, 20.0, 2.0)
    assert polar_load.day_values('2026-05-30', '2026-05-31') == {}, 'before the first session: no load'


def test_the_weeks_give_the_moderate_and_vigorous_minutes_and_no_birth_date_gives_nothing(monkeypatch) -> None:
    two_sessions()
    assert polar_load.day_values('2026-07-01', '2026-07-01') == {}
    assert polar_load.missing().startswith('The training load needs birth_date: set it in the [person] part of person.toml')
    born_1966(monkeypatch)
    assert polar_load.weeks(date(2026, 7, 1), date(2026, 7, 7)) == [
        {'week': '2026-06-29', 'moderate': 20, 'vigorous': 5}, {'week': '2026-07-06', 'moderate': 0, 'vigorous': 0}]
    assert polar_load.missing() == ''


def test_the_history_the_detail_and_the_route_give_the_load(monkeypatch) -> None:
    born_1966(monkeypatch)
    two_sessions()
    rows = history.days(2, date(2026, 7, 2))
    assert (rows[0]['load:trimp'], rows[1]['load:acute']) == (80.0, 80.0)
    first = polar_store.sessions(0, 5)['sessions'][-1]
    assert polar_detail.detail(first['id'])['trimp'] == 40.0
    monkeypatch.setattr(api, '_health_now', lambda: datetime(2026, 7, 2, 12, 0).astimezone())
    found = TestClient(api.app).get('/api/health/polar/activity?days=7').json()
    assert (found['weeks'][-1], found['target'], found['missing']) == (
        {'week': '2026-06-29', 'moderate': 20, 'vigorous': 5}, 150, '')
