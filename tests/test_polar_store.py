"""The Polar H10 sessions in the databases: a session saved again keeps its number and its removed state, copies of a
logger session, and the day values for the history."""

import sqlite3
from datetime import date

import pytest

from health_dashboard import ledger, polar_store

START_S = 1_782_892_800


def session(**fields: object) -> dict:
    """A session row as the import makes it; the fields given replace these."""
    return {'source': 'phone', 'serial': 'E813952E', 'path': '/phone/a_HR.txt', 'start_at': '2026-07-01T08:00:00+00:00',
            'end_at': '2026-07-01T08:15:00+00:00', 'day': '2026-07-01', 'kind': 'exercise', 'seconds': 900,
            'mean_hr': 100.0, 'max_hr': 150.0, 'rest_hr': None,
            'rmssd': None, 'wrong_beats_pct': None, 'imported_at': '2026-07-02T10:00:00+00:00'} | fields


def save(markers: list[tuple] = (), **fields: object) -> int:
    """Save a session with 120 s at 150 bpm and 780 s at 100 bpm, one beat, one ECG block, its HR file and its markers."""
    seconds = [(START_S + n, 150.0 if n < 120 else 100.0, 8.0) for n in range(900)]
    path = str(fields.get('path', '/phone/a_HR.txt'))
    return polar_store.save(session(**fields), seconds, [(START_S * 1000, 800.0, 1)],
                            [('ecg', START_S * 1000, 5, 130.0, 2, 1, b'x')], [(path, 10, 20)], markers)


def test_a_session_saved_again_keeps_its_number_and_its_removed_state() -> None:
    first = save()
    assert polar_store.set_removed([first], True) == 1
    again = save(max_hr=152.0)
    assert again == first
    (row,) = polar_store.removed_sessions()
    assert (row['max_hr'], row['removed'], 'above_limit' in row) == (152.0, True, False)
    assert len(polar_store.blocks(first, 'ecg')) == 1, 'the earlier blocks are replaced'
    assert polar_store.known_files() == {'/phone/a_HR.txt': (10, 20)}
    assert polar_store.set_removed([999], True) == 0


def test_many_sessions_are_removed_and_shown_again_at_once() -> None:
    early = save(path='/p/1', start_at='2026-07-01T08:00:00+00:00')
    late = save(path='/p/2', start_at='2026-07-01T12:00:00+00:00')
    kept = save(path='/p/3', start_at='2026-07-01T14:00:00+00:00')
    assert polar_store.set_removed([early, late, 999], True) == 2, 'a number that is not there changes nothing'
    assert [s['id'] for s in polar_store.removed_sessions()] == [late, early]
    assert [s['id'] for s in polar_store.sessions(0, 20)['sessions']] == [kept]
    assert polar_store.set_removed([early, late], False) == 2 and polar_store.removed_sessions() == []


def test_the_values_of_the_sessions_are_values_of_each_session_never_of_a_day() -> None:
    assert polar_store.SESSION_KEYS == ('h10:max_hr', 'h10:mean_hr'), 'no mean HR in movement (removed on 2026-10-21)'
    assert not hasattr(polar_store, 'day_values'), 'a day mixes activities and days without a recording: no day value'
    assert 'h10:recovery' not in polar_store.KEYS, 'the Cooldown 60 s button is gone'
    assert 'h10:angina_hr' not in polar_store.KEYS, 'the angina buttons are gone'


def test_a_phone_session_inside_a_logger_session_of_the_same_strap_is_a_copy() -> None:
    logger = save(source='logger', path='/logger/a', start_at='2026-07-01T08:01:00+00:00', end_at='2026-07-01T08:16:00+00:00')
    phone = save(path='/phone/b', start_at='2026-07-01T08:00:00+00:00', end_at='2026-07-01T08:15:00+00:00')
    other = save(path='/phone/c', serial='OTHER', start_at='2026-07-01T08:00:30+00:00')
    later = save(path='/phone/d', start_at='2026-07-01T08:12:00+00:00', end_at='2026-07-01T08:30:00+00:00')
    polar_store.mark_copies()
    listed = polar_store.day_sessions('2026-07-01')
    assert {s['id']: s['copy_of'] for s in listed} == {logger: None, phone: logger, other: None, later: None}
    assert next(s for s in listed if s['id'] == phone)['copy_of_start'] == '2026-07-01T08:01:00+00:00'
    assert polar_store.sessions(0, 2)['total'] == 4 and polar_store.count() == 4


def test_a_session_carries_its_markers_and_the_card_no_angina_onset() -> None:
    first = save(path='/l/1', markers=[((START_S + 90) * 1000, 'exercise stop', 150.0),
                                       ((START_S + 60) * 1000, 'exercise start', 128.0)])
    (listed,) = polar_store.sessions(0, 5)['sessions']
    assert listed['markers'] == [{'at': '2026-07-01T08:01:00+00:00', 'label': 'exercise start', 'hr': 128.0},
                                 {'at': '2026-07-01T08:01:30+00:00', 'label': 'exercise stop', 'hr': 150.0}]
    assert 'recovery' not in listed and 'recovery_state' not in listed
    assert 'last_angina' not in polar_store.latest('2026-07-01'), 'the angina buttons are gone'
    assert save(path='/l/1', markers=[]) == first
    assert polar_store.sessions(0, 5)['sessions'][0]['markers'] == [], 'a session saved again replaces its markers'


def test_the_first_activities_come_once_and_a_removed_one_does_not_come_back() -> None:
    def names() -> list[str]:
        """The activities in list order."""
        with ledger.connect() as db:
            return [r[0] for r in db.execute('SELECT name FROM polar_activities ORDER BY position')]

    assert names() == [*ledger.ACTIVITIES, 'HRV', 'HR recovery', 'Stairs test'] == [
        'Walk', 'Gym cardio', 'Weights', 'Bike', 'Rest', 'HRV', 'HR recovery', 'Stairs test']
    with ledger.connect(write=True) as db:
        db.execute("DELETE FROM polar_activities WHERE name = 'Bike'")
    ledger._ready.clear()
    assert names() == ['Walk', 'Gym cardio', 'Weights', 'Rest', 'HRV', 'HR recovery', 'Stairs test']


def test_a_listed_session_has_no_activity_yet_and_no_split_in_movement_and_still() -> None:
    save()
    (row,) = polar_store.sessions(0, 5)['sessions']
    assert (row['activity_id'], row['activity'], row['note']) == (None, None, None)
    assert not {'moving_s', 'still_s', 'moving_hr', 'still_hr', 'activity_no_split'} & row.keys()


def test_the_activity_list_adds_renames_and_removes_only_an_unused_activity() -> None:
    added = polar_store.add_activity('Swim')
    assert [a['name'] for a in polar_store.activities()][-1] == 'Swim'
    with pytest.raises(polar_store.ActivityError, match='already'):
        polar_store.add_activity('swim')
    with pytest.raises(polar_store.ActivityError, match='already'):
        polar_store.rename_activity(added, 'WALK')
    assert polar_store.rename_activity(added, 'Swimming') and not polar_store.rename_activity(999, 'X')
    session_id = save()
    assert polar_store.set_activity(session_id, 1) and not polar_store.set_activity(session_id, 999)
    assert not polar_store.set_activity(999, 1)
    with pytest.raises(polar_store.ActivityError, match=r'Walk is the activity of 1 session\. Give those sessions'):
        polar_store.remove_activity(1)
    assert polar_store.remove_activity(added) and not polar_store.remove_activity(added)
    assert {a['name']: a['sessions'] for a in polar_store.activities()} == {'Walk': 1, 'Gym cardio': 0, 'Weights': 0,
                                                                            'Bike': 0, 'Rest': 0, 'HRV': 0, 'HR recovery': 0,
                                                                            'Stairs test': 0}


def test_each_activity_gives_the_start_of_its_newest_session_that_is_not_removed() -> None:
    older = save(path='/n/1', start_at='2026-07-01T08:00:00+00:00')
    newer = save(path='/n/2', start_at='2026-07-03T08:00:00+00:00')
    removed = save(path='/n/3', start_at='2026-07-05T08:00:00+00:00')
    for found in (older, newer, removed):
        polar_store.set_activity(found, 1)
    polar_store.set_removed([removed], True)
    last = {a['name']: a['last_at'] for a in polar_store.activities()}
    assert (last['Walk'], last['Bike']) == ('2026-07-03T08:00:00+00:00', None)


def test_the_activity_and_the_note_stay_when_a_session_is_read_again() -> None:
    first = save()
    polar_store.set_activity(first, 2)
    polar_store.set_note(first, '  intervals on the bike  ')
    assert save(max_hr=152.0) == first
    (row,) = polar_store.sessions(0, 5)['sessions']
    assert (row['activity_id'], row['activity'], row['note'], row['max_hr']) == (2, 'Gym cardio', 'intervals on the bike', 152.0)
    assert polar_store.set_note(first, '   ') and polar_store.sessions(0, 5)['sessions'][0]['note'] is None
    assert not polar_store.set_note(999, 'x')


def test_the_points_of_the_sessions_leave_out_the_hrv_test_and_the_activity_chooses_the_sessions() -> None:
    walk = save(path='/w/1', start_at='2026-07-01T08:00:00+00:00', seconds=600, mean_hr=100.0, max_hr=130.0)
    bike = save(path='/w/2', start_at='2026-07-01T12:00:00+00:00', seconds=1200, mean_hr=130.0, max_hr=160.0)
    test = save(path='/w/3', start_at='2026-07-01T06:00:00+00:00', seconds=300, mean_hr=60.0, max_hr=70.0)
    polar_store.set_activity(test, 6)
    polar_store.set_activity(walk, 1)
    polar_store.set_activity(bike, 4)
    assert [p['h10:mean_hr'] for p in polar_store.session_points('2026-07-01', '2026-07-01')] == [100.0, 130.0], \
        'the HRV test does not count'
    assert polar_store.session_points('2026-07-01', '2026-07-01', activity=1) == [
        {'at': '2026-07-01T08:00:00+00:00', 'day': '2026-07-01', 'h10:max_hr': 130.0, 'h10:mean_hr': 100.0}]
    assert polar_store.session_points('2026-07-01', '2026-07-01', activity=5) == []


def test_the_minutes_per_week_go_by_activity_from_monday() -> None:
    walk = save(path='/k/1', start_at='2026-07-06T08:00:00+00:00', day='2026-07-06', seconds=1800)
    save(path='/k/2', start_at='2026-07-12T08:00:00+00:00', day='2026-07-12', seconds=600)
    bike = save(path='/k/3', start_at='2026-07-13T08:00:00+00:00', day='2026-07-13', seconds=3600)
    polar_store.set_activity(walk, 1)
    polar_store.set_activity(bike, 4)
    assert polar_store.weeks(date(2026, 7, 1), date(2026, 7, 14)) == [
        {'week': '2026-06-29', 'minutes': {}}, {'week': '2026-07-06', 'minutes': {'Walk': 30, 'No activity': 10}},
        {'week': '2026-07-13', 'minutes': {'Bike': 60}}]
    assert polar_store.weeks(date(2026, 7, 1), date(2026, 7, 14), activity=1)[1:] == [
        {'week': '2026-07-06', 'minutes': {'Walk': 30}}, {'week': '2026-07-13', 'minutes': {}}]


def test_the_activity_of_a_recording_goes_to_the_logger_sessions_that_ended_after_its_start_and_have_none() -> None:
    old = save(source='logger', path='/l/old', start_at='2026-07-01T08:00:00+00:00', end_at='2026-07-01T08:15:00+00:00')
    new = save(source='logger', path='/l/new', start_at='2026-07-01T09:57:00+00:00', end_at='2026-07-01T10:20:00+00:00')
    named = save(source='logger', path='/l/named', start_at='2026-07-01T10:30:00+00:00', end_at='2026-07-01T10:40:00+00:00')
    phone = save(path='/p/x', start_at='2026-07-01T10:00:00+00:00', end_at='2026-07-01T10:15:00+00:00')
    polar_store.set_activity(named, 1)
    assert polar_store.give_activity('2026-07-01T10:00:00+00:00', 2) == 1
    assert [polar_store.session(i)['activity_id'] for i in (old, new, named, phone)] == [None, 2, 1, None]


def test_the_test_time_chosen_at_start_goes_with_the_activity_to_the_session() -> None:
    old = save(source='logger', path='/l/old', start_at='2026-07-01T08:00:00+00:00', end_at='2026-07-01T08:15:00+00:00')
    new = save(source='logger', path='/l/new', start_at='2026-07-01T09:57:00+00:00', end_at='2026-07-01T10:20:00+00:00')
    assert polar_store.give_activity('2026-07-01T10:00:00+00:00', 6, 180) == 1
    assert [(polar_store.session(i)['activity_id'], polar_store.measure_s(i)) for i in (old, new)] == [(None, None), (6, 180)]


def test_the_hrv_activity_is_added_once_and_cannot_be_removed() -> None:
    (hrv,) = [a for a in polar_store.activities() if a['test'] == 'hrv']
    assert (hrv['id'], hrv['name']) == (6, 'HRV')
    with pytest.raises(polar_store.ActivityError, match='activity of the HRV test'):
        polar_store.remove_activity(6)
    assert polar_store.rename_activity(6, 'Morning HRV')
    session_id = save()
    polar_store.set_activity(session_id, 6)
    assert polar_store.session(session_id)['activity_test'] == 'hrv'


def test_an_activity_named_hrv_gets_the_test_flag_instead_of_a_second_one(tmp_path, monkeypatch) -> None:
    old = tmp_path / 'old.db'
    with sqlite3.connect(old) as db:
        db.execute('CREATE TABLE polar_activities (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE, '
                   'position INTEGER NOT NULL)')
        db.execute("INSERT INTO polar_activities (name, position) VALUES ('hrv', 0)")
    monkeypatch.setattr(ledger, 'DB', old)
    with ledger.connect() as db:
        assert [tuple(r) for r in db.execute("SELECT name, test FROM polar_activities WHERE name = 'HRV'")] == [('hrv', 'hrv')]


def test_an_activity_has_no_setting_for_a_split_in_movement_and_still() -> None:
    assert not [a for a in polar_store.activities() if 'no_split' in a]
    assert not hasattr(polar_store, 'set_no_split')


def test_the_hr_recovery_activity_is_added_once_and_cannot_be_removed() -> None:
    (recovery,) = [a for a in polar_store.activities() if a['test'] == 'recovery']
    assert (recovery['id'], recovery['name']) == (7, 'HR recovery')
    with pytest.raises(polar_store.ActivityError, match='activity of the HR recovery test'):
        polar_store.remove_activity(7)
    assert polar_store.rename_activity(7, 'Recovery')
    ledger._ready.clear()
    assert [a['name'] for a in polar_store.activities() if a['test'] == 'recovery'] == ['Recovery'], 'not added again'


def test_the_stairs_test_activity_is_added_once_and_cannot_be_removed() -> None:
    (stairs,) = [a for a in polar_store.activities() if a['test'] == 'stairs']
    assert (stairs['id'], stairs['name']) == (8, 'Stairs test')
    with pytest.raises(polar_store.ActivityError, match='activity of the stairs test'):
        polar_store.remove_activity(8)
    assert polar_store.rename_activity(8, 'Stairs')
    ledger._ready.clear()
    assert [a['name'] for a in polar_store.activities() if a['test'] == 'stairs'] == ['Stairs'], 'not added again'


def test_a_stairs_test_is_exercise_with_its_presses_in_time_order() -> None:
    climb = save(path='/s/1', start_at='2026-07-01T08:00:00+00:00', max_hr=165.0,
                 markers=[((START_S + 200) * 1000, 'marker stop', 160.0), ((START_S + 20) * 1000, 'marker start', 100.0),
                          ((START_S + 250) * 1000, 'effort done', 140.0)])
    polar_store.set_activity(climb, 8)
    assert [p['h10:max_hr'] for p in polar_store.session_points('2026-07-01', '2026-07-01')] == [165.0]
    assert [s['id'] for s in polar_store.sessions_of_test('stairs', '2026-07-01', '2026-07-01')] == [climb]
    assert polar_store.markers(climb) == [((START_S + 20) * 1000, 'marker start'), ((START_S + 200) * 1000, 'marker stop'),
                                          ((START_S + 250) * 1000, 'effort done')]
    assert polar_store.marker_times(climb, 'effort done', 'marker stop') == [(START_S + 200) * 1000, (START_S + 250) * 1000]
    assert {'h10:stairs_time', 'h10:stairs_pauses', 'h10:stairs_drop_60'} <= set(polar_store.KEYS)


def test_an_activity_named_hr_recovery_gets_the_test_flag_instead_of_a_second_one(tmp_path, monkeypatch) -> None:
    old = tmp_path / 'old.db'
    with sqlite3.connect(old) as db:
        db.execute('CREATE TABLE polar_activities (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE, '
                   'position INTEGER NOT NULL)')
        db.execute("INSERT INTO polar_activities (name, position) VALUES ('hr recovery', 0)")
    monkeypatch.setattr(ledger, 'DB', old)
    with ledger.connect() as db:
        assert [tuple(r) for r in db.execute("SELECT name, test FROM polar_activities WHERE name = 'HR recovery'")] == [
            ('hr recovery', 'recovery')]


def test_a_recovery_test_is_exercise_and_its_sessions_and_markers_are_found() -> None:
    hrv = save(path='/t/1', start_at='2026-07-01T06:00:00+00:00', mean_hr=60.0, max_hr=70.0)
    first = save(path='/t/2', start_at='2026-07-01T08:00:00+00:00', max_hr=170.0,
                 markers=[((START_S + 200) * 1000, 'effort done', 150.0), ((START_S + 100) * 1000, 'effort done', 140.0)])
    second = save(path='/t/3', start_at='2026-07-02T08:00:00+00:00', day='2026-07-02')
    polar_store.set_activity(hrv, 6)
    for session_id in (first, second):
        polar_store.set_activity(session_id, 7)
    assert polar_store.latest('2026-07-01')['last_exercise']['id'] == first
    assert [p['h10:max_hr'] for p in polar_store.session_points('2026-07-01', '2026-07-01')] == [170.0], \
        'a recovery test is an exercise session, the HRV test is not'
    assert [s['id'] for s in polar_store.exercise_sessions('2026-07-01', '2026-07-02')] == [first, second]
    assert [s['id'] for s in polar_store.sessions_of_test('recovery', '2026-07-01', '2026-07-02')] == [first, second]
    assert [s['id'] for s in polar_store.sessions_of_test('hrv', '2026-07-01', '2026-07-02')] == [hrv]
    assert polar_store.last_session_of_test('recovery')['id'] == second and polar_store.last_session_of_test('x') is None
    assert polar_store.marker_times(first, 'effort done') == [(START_S + 100) * 1000, (START_S + 200) * 1000]
    assert {'h10:hr_stop', 'h10:drop_60', 'h10:drop_120'} <= set(polar_store.KEYS)


def test_each_session_that_counts_and_is_not_an_hrv_test_is_a_point_at_its_start() -> None:
    save(path='/c/1', start_at='2026-07-01T08:00:00+00:00')
    lifting = save(path='/c/2', start_at='2026-07-01T12:00:00+00:00', max_hr=140.0, mean_hr=110.0)
    hrv = save(path='/c/3', start_at='2026-07-01T06:00:00+00:00')
    removed = save(path='/c/4', start_at='2026-07-01T14:00:00+00:00')
    polar_store.set_activity(lifting, 3)
    polar_store.set_activity(hrv, 6)
    polar_store.set_removed([removed], True)
    assert polar_store.session_points('2026-07-01', '2026-07-01') == [
        {'at': '2026-07-01T08:00:00+00:00', 'day': '2026-07-01', 'h10:max_hr': 150.0, 'h10:mean_hr': 100.0},
        {'at': '2026-07-01T12:00:00+00:00', 'day': '2026-07-01', 'h10:max_hr': 140.0, 'h10:mean_hr': 110.0}]
    assert [p['h10:max_hr'] for p in polar_store.session_points('2026-07-01', '2026-07-01', activity=3)] == [140.0]
