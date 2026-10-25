"""The stairs tests: the values of a session with the stairs test activity from its stored seconds and the presses of the
phone marker, the test of each day for the history, and the last test for the card."""

from datetime import datetime

import numpy as np

from health_dashboard import ledger, polar_stairs, polar_store
from tests.polar_fixtures import stored_session

STAIRS = 8


def stairs_test(start_at: str, presses: list[tuple[int, str]], seconds: int = 360) -> int:
    """A stairs test: the HR climbs from 100 bpm to 160 at the top (second 200), then falls 0.5 bpm a second; no movement
    data; the presses of the phone marker (second, label)."""
    t = np.arange(seconds, dtype=float)
    hr = np.where(t <= 200, 100.0 + t * 0.3, 160.0 - 0.5 * (t - 200))
    session_id = stored_session(start_at, hr.tolist(), None)
    polar_store.set_activity(session_id, STAIRS)
    start_s = int(datetime.fromisoformat(start_at).timestamp())
    with ledger.connect(write=True) as db:
        db.executemany('INSERT INTO polar_markers (session_id, t_ms, label, hr) VALUES (?, ?, ?, NULL)',
                       [(session_id, (start_s + second) * 1000, label) for second, label in presses])
    return session_id


TWO_PAUSES = [(20, 'marker start'), (60, 'marker stop'), (75, 'marker start'), (120, 'marker stop'), (150, 'marker start'),
              (200, 'marker stop')]


def test_a_session_with_the_stairs_activity_has_its_values_from_its_seconds_and_presses() -> None:
    found = polar_stairs.values_of(stairs_test('2026-07-01T08:00:00+00:00', [*TWO_PAUSES, (250, 'effort done')]))
    assert (found['climb_s'], found['pauses'], found['pause_s'], found['max_hr'], found['drop_60'], found['reason']) == (
        180, 2, 45, 160.0, 29.5, None)


def test_each_climb_is_a_point_at_its_time_a_day_has_their_mean_and_the_card_the_newest_of_30_days() -> None:
    stairs_test('2026-07-01T06:00:00+00:00', [(20, 'marker start')])
    stairs_test('2026-07-01T07:00:00+00:00', TWO_PAUSES)
    newest = stairs_test('2026-07-01T08:00:00+00:00', [(20, 'marker start'), (200, 'marker stop')], seconds=260)
    assert polar_stairs.points('2026-07-01', '2026-07-02') == [
        {'at': '2026-07-01T07:00:00+00:00', 'day': '2026-07-01', 'h10:stairs_time': 180, 'h10:stairs_pauses': 2,
         'h10:stairs_pause_time': 45, 'h10:stairs_max_hr': 160.0, 'h10:stairs_hr_top': 159.5, 'h10:stairs_drop_60': 29.5,
         'h10:stairs_drop_120': 59.5},
        {'at': '2026-07-01T08:00:00+00:00', 'day': '2026-07-01', 'h10:stairs_time': 180, 'h10:stairs_pauses': 0,
         'h10:stairs_pause_time': 0, 'h10:stairs_max_hr': 160.0}], 'the marker left on: no values; too short a rest: no drops'
    assert polar_stairs.day_values('2026-07-01', '2026-07-02') == {'2026-07-01': {
        'h10:stairs_time': 180, 'h10:stairs_pauses': 1.0, 'h10:stairs_pause_time': 22, 'h10:stairs_max_hr': 160.0,
        'h10:stairs_hr_top': 159.5, 'h10:stairs_drop_60': 29.5, 'h10:stairs_drop_120': 59.5}}
    card = polar_stairs.last_test('2026-07-31')
    assert (card['session_id'], card['climb_s'], card['recovery_reason'], card['start_at']) == (
        newest, 180, 'The test needs 2 minutes after the stop; this recording has 59 s after it.', '2026-07-01T08:00:00+00:00')
    assert polar_stairs.last_test('2026-09-01') is None, 'nothing in the 30 days before'
