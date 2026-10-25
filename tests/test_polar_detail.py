"""The detail of one Polar H10 session: the HR of each second, the zones from the birth date, and the comparison with the
earlier sessions of the same activity; and its route. No split in movement and still (removed on 2026-10-21)."""

from datetime import date

from fastapi.testclient import TestClient

from health_dashboard import api, polar_detail, polar_store
from tests.polar_fixtures import set_person, stored_session

WALK, BIKE = 1, 4


def walk_with_a_stop() -> tuple[list[float], list[float]]:
    """600 s: 300 s at 100 bpm walking, 120 s at 80 bpm standing still, 180 s at 120 bpm walking."""
    return [100.0] * 300 + [80.0] * 120 + [120.0] * 180, [200.0] * 300 + [8.0] * 120 + [200.0] * 180


def test_the_detail_has_each_second_no_split_in_movement_and_still_and_no_zones_without_a_birth_year() -> None:
    session_id = stored_session('2026-07-01T08:00:00+00:00', *walk_with_a_stop())
    found = polar_detail.detail(session_id)
    assert (found['start'], len(found['hr']), found['hr'][0], found['hr'][350]) == (1782892800, 600, 100.0, 80.0)
    assert not {'still', 'parts'} & found.keys() and not {'moving_s', 'still_s', 'moving_hr', 'still_hr'} & found['session'].keys()
    assert (found['zones'], found['birth_year'], found['compare']) == (None, None, None)
    assert polar_detail.detail(999) is None


def test_the_zones_use_the_birth_date_of_person_toml_and_the_year_of_the_session(monkeypatch) -> None:
    session_id = stored_session('2026-07-01T08:00:00+00:00', *walk_with_a_stop())
    set_person(monkeypatch, birth_date='1960-10-01')
    assert polar_detail.detail(session_id)['zones'] == {'max_hr': 154, 'limits': [77, 92, 108, 123, 139],
                                                        'seconds': [0, 120, 300, 180, 0, 0]}




def test_the_comparison_takes_the_mean_of_the_last_10_earlier_sessions_of_the_same_activity_that_count(monkeypatch) -> None:
    set_person(monkeypatch, birth_date='1960-10-01')
    this = stored_session('2026-07-20T08:00:00+00:00', *walk_with_a_stop())
    assert polar_detail.detail(this)['compare'] is None, 'no activity'
    polar_store.set_activity(this, WALK)
    assert polar_detail.detail(this)['compare'] is None, 'no earlier walk'
    for day in range(1, 13):
        polar_store.set_activity(stored_session(f'2026-07-{day:02d}T08:00:00+00:00', [110.0] * 300, [200.0] * 300), WALK)
    polar_store.set_activity(stored_session('2026-07-15T08:00:00+00:00', [150.0] * 300, [200.0] * 300), BIKE)
    removed = stored_session('2026-07-16T08:00:00+00:00', [150.0] * 300, [200.0] * 300)
    polar_store.set_activity(removed, WALK)
    polar_store.set_removed([removed], True)
    polar_store.set_activity(stored_session('2026-07-21T08:00:00+00:00', [150.0] * 300, [200.0] * 300), WALK)
    compare = polar_detail.detail(this)['compare']
    assert compare['count'] == 10
    assert compare['this'] == {'seconds': 600, 'mean_hr': 102.0, 'max_hr': 120.0, 'high_zone_min': 3.0}
    assert compare['usual'] == {'seconds': 300.0, 'mean_hr': 110.0, 'max_hr': 110.0, 'high_zone_min': 5.0}


def test_the_route_gives_the_detail_or_404() -> None:
    client = TestClient(api.app)
    session_id = stored_session('2026-07-01T08:00:00+00:00', *walk_with_a_stop())
    assert client.get(f'/api/health/polar/sessions/{session_id}').json()['session']['id'] == session_id
    assert client.get('/api/health/polar/sessions/999').status_code == 404


def test_the_detail_of_an_hrv_test_has_its_values_and_the_beats_of_its_window() -> None:
    plain = stored_session('2026-07-01T08:00:00+00:00', *walk_with_a_stop())
    assert polar_detail.detail(plain)['hrv'] is None
    test = stored_session('2026-07-02T06:00:00+00:00', [60.0] * 420, [8.0] * 420, rr=[980.0, 1020.0] * 210)
    polar_store.set_activity(test, 6)
    hrv = polar_detail.detail(test)['hrv']
    assert (hrv['rmssd'], len(hrv['beats']), hrv['reason']) == (40.0, 300, None)




def test_the_minutes_per_week_in_each_zone_count_the_exercise_sessions_and_not_the_hrv_tests(monkeypatch) -> None:
    assert polar_detail.zone_weeks(date(2026, 7, 1), date(2026, 7, 7)) is None, 'no birth date'
    set_person(monkeypatch, birth_date='1966-12-31')
    stored_session('2026-07-01T08:00:00+00:00', [100.0] * 120 + [130.0] * 180, None)
    hrv = stored_session('2026-07-02T06:00:00+00:00', [100.0] * 600, None)
    polar_store.set_activity(hrv, 6)
    assert polar_detail.zone_weeks(date(2026, 7, 1), date(2026, 7, 7)) == {
        'limits': [80, 96, 112, 128, 144],
        'weeks': [{'week': '2026-06-29', 'minutes': [0, 2, 0, 3, 0]}, {'week': '2026-07-06', 'minutes': [0, 0, 0, 0, 0]}]}
