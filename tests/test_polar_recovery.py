"""The HR recovery tests: the values of a session with the HR recovery activity from its stored seconds and its Effort
done marker, the test of each day for the history, and the last test for the card."""

from datetime import datetime

import numpy as np

from health_dashboard import ledger, polar_recovery, polar_store
from tests.polar_fixtures import stored_session

RECOVERY = 7


def recovery_test(start_at: str, seconds: int = 330, fall: float = 0.5, still_after: bool = True) -> int:
    """An HR recovery test: 150 bpm up to the stop at 182 s, then `fall` bpm less each second; in movement up to 190 s, then
    still (or in movement throughout)."""
    t = np.arange(seconds, dtype=float)
    hr = np.where(t <= 182, 150.0, 150.0 - fall * (t - 182))
    movement = np.where(np.logical_or(t < 190, not still_after), 200.0, 8.0)
    session_id = stored_session(start_at, hr.tolist(), movement.tolist())
    polar_store.set_activity(session_id, RECOVERY)
    return session_id


def effort_done(session_id: int, start_at: str, second: int, label: str = 'effort done') -> None:
    """An Effort done marker (or another label) `second` seconds after the start of a session."""
    at_ms = (int(datetime.fromisoformat(start_at).timestamp()) + second) * 1000
    with ledger.connect(write=True) as db:
        db.execute('INSERT INTO polar_markers (session_id, t_ms, label, hr) VALUES (?, ?, ?, 150.0)', (session_id, at_ms, label))


def test_a_session_with_the_hr_recovery_activity_has_its_values_from_its_stored_seconds() -> None:
    found = polar_recovery.values_of(recovery_test('2026-07-01T08:00:00+00:00'))
    assert found == {'hr_stop': 149.7, 'hr_60': 120.0, 'hr_120': 90.0, 'drop_60': 29.7, 'drop_120': 59.7, 'stop_s': 182,
                     'stop_from': 'highest HR', 'reason': None}


def test_the_last_effort_done_marker_is_the_stop() -> None:
    start_at = '2026-07-01T08:00:00+00:00'
    session_id = recovery_test(start_at)
    effort_done(session_id, start_at, 100)
    effort_done(session_id, start_at, 180)
    found = polar_recovery.values_of(session_id)
    assert (found['stop_s'], found['stop_from'], found['drop_60']) == (180, 'marker', 29.0)


def test_in_a_phone_recording_the_last_press_of_the_marker_is_the_stop() -> None:
    start_at = '2026-07-01T08:00:00+00:00'
    session_id = recovery_test(start_at)
    effort_done(session_id, start_at, 100, 'marker start')
    effort_done(session_id, start_at, 180, 'marker stop')
    found = polar_recovery.values_of(session_id)
    assert (found['stop_s'], found['stop_from'], found['drop_60']) == (180, 'marker', 29.0)


def test_every_test_with_values_is_a_point_at_its_time_a_day_has_their_mean_and_the_card_the_newest_of_30_days() -> None:
    recovery_test('2026-07-01T06:00:00+00:00', seconds=250)
    recovery_test('2026-07-01T07:00:00+00:00')
    newest = recovery_test('2026-07-01T08:00:00+00:00', fall=1.0)
    assert polar_recovery.points('2026-07-01', '2026-07-02') == [
        {'at': '2026-07-01T07:00:00+00:00', 'day': '2026-07-01', 'h10:hr_stop': 149.7, 'h10:drop_60': 29.7, 'h10:drop_120': 59.7},
        {'at': '2026-07-01T08:00:00+00:00', 'day': '2026-07-01', 'h10:hr_stop': 149.4, 'h10:drop_60': 59.4, 'h10:drop_120': 119.4}]
    assert polar_recovery.day_values('2026-07-01', '2026-07-02') == {'2026-07-01': {
        'h10:hr_stop': round((149.7 + 149.4) / 2, 1), 'h10:drop_60': round((29.7 + 59.4) / 2, 1),
        'h10:drop_120': round((59.7 + 119.4) / 2, 1)}}
    card = polar_recovery.last_test('2026-07-31')
    assert (card['session_id'], card['drop_60'], card['start_at']) == (newest, 59.4, '2026-07-01T08:00:00+00:00')
    assert polar_recovery.last_test('2026-08-01') is None, 'more than 30 days before the day of the page'


def test_a_test_that_is_too_short_or_not_still_has_its_reason() -> None:
    short = recovery_test('2026-07-01T06:00:00+00:00', seconds=250)
    moving = recovery_test('2026-07-01T07:00:00+00:00', still_after=False)
    assert polar_recovery.values_of(short)['reason'] == 'The test needs 2 minutes after the stop; this recording has 67 s after it.'
    assert polar_recovery.values_of(moving)['reason'].startswith('After the stop the chest was not still')
    assert polar_recovery.day_values('2026-07-01', '2026-07-01') == {}
    assert polar_recovery.last_test('2026-07-01')['session_id'] == moving
