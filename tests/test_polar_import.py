"""The import of Polar H10 sessions from made-up files of the phone app and the logger: which files make one session, what
is read again, the values of each session, its day, copies, the raw samples kept without loss, and the import in the
background."""

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pytest

from health_dashboard import ledger, polar, polar_store, polar_values, store
from tests.polar_fixtures import SERIAL, ecg_value, logger_session, phone_markers, phone_session, still, walking

TZ = ZoneInfo('Europe/Brussels')


def folders(tmp_path: Path) -> tuple[Path, Path]:
    """An empty phone folder and logger folder."""
    phone, logger = tmp_path / 'phone', tmp_path / 'logger'
    phone.mkdir()
    logger.mkdir()
    return phone, logger


def all_sessions() -> list[dict]:
    """Every stored session, in time order."""
    with ledger.connect() as db:
        return [dict(r) for r in db.execute('SELECT * FROM polar_sessions ORDER BY start_at')]


def test_phone_clock_times_become_utc_with_summer_and_winter_time() -> None:
    assert polar.phone_ms(['2026-07-01T10:00:00.000', '2026-07-01T10:00:00.250'], TZ).tolist() == [1782892800000, 1782892800250]
    assert polar.phone_ms(['2026-01-10T10:00:00.000'], TZ).tolist() == [1768035600000]
    across = polar.phone_ms(['2026-03-29T01:59:59.000', '2026-03-29T03:00:00.000'], TZ)
    assert (across[1] - across[0]).item() == 1000, 'the clock jumps an hour, the time only a second'


def test_the_files_of_a_phone_session_start_within_a_minute_and_each_session_gets_its_own(tmp_path) -> None:
    phone, logger = folders(tmp_path)
    phone_session(phone, datetime(2026, 7, 1, 10, 0), 900)
    phone_session(phone, datetime(2026, 7, 1, 10, 16), 900, acc=walking)
    assert polar.import_all(phone, logger, TZ) == 2
    first, _ = all_sessions()
    assert (first['start_at'], first['day'], first['source'], first['serial']) == ('2026-07-01T08:00:00+00:00', '2026-07-01',
                                                                                  'phone', SERIAL)
    assert (first['max_hr'], first['kind'], first['seconds']) == (150.0, 'exercise', 900)


def test_a_session_with_only_hr_is_read_without_movement_data(tmp_path) -> None:
    phone, logger = folders(tmp_path)
    phone_session(phone, datetime(2026, 1, 10, 10, 0), 900, rr=False, acc=None)
    assert polar.import_all(phone, logger, TZ) == 1
    (session,) = all_sessions()
    assert session['start_at'] == '2026-01-10T09:00:00+00:00' and 'moving_s' not in session
    with ledger.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM polar_seconds WHERE movement IS NOT NULL').fetchone()[0] == 0


def test_a_still_session_of_a_few_minutes_is_no_rest_session_any_more(tmp_path) -> None:
    phone, logger = folders(tmp_path)
    phone_session(phone, datetime(2026, 7, 2, 7, 30), 420, lambda s: 60.0, beats=[980, 1020] * 210, acc=still)
    polar.import_all(phone, logger, TZ)
    (session,) = all_sessions()
    assert (session['kind'], session['rest_hr'], session['rmssd'], session['wrong_beats_pct']) == ('exercise', None, None, None)
    with ledger.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM polar_beats WHERE session_id = ?', (session['id'],)).fetchone()[0] == 420


def test_a_logger_session_is_read_once_it_ended_also_with_hr_csv_txt(tmp_path) -> None:
    phone, logger = folders(tmp_path)
    path = logger_session(logger, datetime(2026, 7, 1, 18, 0, tzinfo=TZ), 900, ended=False, txt=True)
    assert polar.import_all(phone, logger, TZ) == 0, 'still recording'
    info = json.loads((path / 'session.json').read_text())
    (path / 'session.json').write_text(json.dumps(info | {'end': '2026-07-01T18:15:00+02:00'}))
    assert polar.import_all(phone, logger, TZ) == 1
    (session,) = all_sessions()
    assert (session['source'], session['start_at']) == ('logger', '2026-07-01T16:00:00+00:00')


def test_a_phone_session_that_overlaps_a_logger_session_is_a_copy_of_it(tmp_path) -> None:
    phone, logger = folders(tmp_path)
    phone_session(phone, datetime(2026, 7, 1, 18, 0), 900)
    logger_session(logger, datetime(2026, 7, 1, 18, 1, tzinfo=TZ), 900)
    polar.import_all(phone, logger, TZ)
    by_source = {s['source']: s for s in all_sessions()}
    assert by_source['phone']['copy_of'] == by_source['logger']['id'] and by_source['logger']['copy_of'] is None


def test_an_unchanged_session_is_not_read_again_and_a_changed_one_keeps_its_removed_state(tmp_path) -> None:
    phone, logger = folders(tmp_path)
    files = phone_session(phone, datetime(2026, 7, 1, 10, 0), 900)
    polar.import_all(phone, logger, TZ)
    (session,) = all_sessions()
    polar_store.set_removed([session['id']], True)
    assert polar.import_all(phone, logger, TZ) == 0
    with files['HR'].open('a') as hr_file:
        hr_file.write('2026-07-01T10:15:00.000;118\n')
    assert polar.import_all(phone, logger, TZ) == 1
    (again,) = all_sessions()
    assert (again['id'], again['removed'], again['seconds']) == (session['id'], 1, 901)


def test_a_session_shorter_than_two_minutes_is_skipped_and_not_read_again(tmp_path) -> None:
    phone, logger = folders(tmp_path)
    phone_session(phone, datetime(2026, 7, 1, 10, 0), 60)
    assert polar.import_all(phone, logger, TZ) == 0
    assert all_sessions() == [] and len(polar_store.known_files()) == 3 and polar._progress['total'] == 3
    polar.import_all(phone, logger, TZ)
    assert polar._progress['total'] == 0, 'nothing to read'


def test_a_session_after_midnight_belongs_to_the_new_day(tmp_path) -> None:
    phone, logger = folders(tmp_path)
    phone_session(phone, datetime(2025, 6, 25, 0, 36), 300)
    polar.import_all(phone, logger, TZ)
    assert all_sessions()[0]['day'] == '2025-06-25'


def test_the_ecg_and_acceleration_are_kept_exactly_with_a_new_block_after_a_dropout(tmp_path) -> None:
    phone, logger = folders(tmp_path)
    phone_session(phone, datetime(2026, 7, 1, 10, 0), 200, ecg=True, ecg_gap=(100, 105), acc_rate=200)
    polar.import_all(phone, logger, TZ)
    (session,) = all_sessions()
    ecg = polar_store.blocks(session['id'], 'ecg')
    values = np.concatenate([polar_values.unpack(b['data'], b['channels']) for b in ecg])[:, 0]
    assert values.tolist() == [ecg_value(n) for n in range(200 * 130) if not 100 <= n / 130 < 105]
    assert [b['count'] for b in ecg] == [7800, 5200, 7800, 4550], 'at most 60 s, and a new block after the dropout'
    acc = polar_store.blocks(session['id'], 'acc')
    assert {b['rate'] for b in acc} == {200.0} and sum(b['count'] for b in acc) == 200 * 200


def test_a_cut_off_last_line_is_left_out(tmp_path) -> None:
    phone, logger = folders(tmp_path)
    files = phone_session(phone, datetime(2026, 7, 1, 10, 0), 900)
    with files['ACC'].open('a') as acc_file:
        acc_file.write('2026-07-01T10:15:00.000;5996160')
    with files['HR'].open('a') as hr_file:
        hr_file.write('2026-07-01T10:1')
    assert polar.import_all(phone, logger, TZ) == 1


def test_a_logger_session_with_a_half_written_session_json_waits_for_the_next_import(tmp_path) -> None:
    phone, logger = folders(tmp_path)
    path = logger_session(logger, datetime(2026, 7, 1, 18, 0, tzinfo=TZ), 900)
    (path / 'session.json').write_text('{"id": "2026-07-01_18-0')
    assert polar.import_all(phone, logger, TZ) == 0


def test_a_folder_that_does_not_exist_stops_the_import_with_its_name(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(polar, 'folders', lambda: (tmp_path / 'nothing', None))
    assert polar.begin()
    polar.run_import(TZ)
    error = store.setting('polar_error') or ''
    assert 'nothing' in error and 'restart the app' in error and not polar.status()['running']


def test_a_row_that_does_not_parse_is_left_out(tmp_path) -> None:
    phone, logger = folders(tmp_path)
    files = phone_session(phone, datetime(2026, 7, 1, 10, 0), 900)
    lines = files['RR'].read_text().splitlines()
    files['RR'].write_text('\n'.join([*lines[:100], '2026-07-01T10:01:00.000;7x5', *lines[100:]]) + '\n')
    assert polar.import_all(phone, logger, TZ) == 1
    with ledger.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM polar_beats').fetchone()[0] == len(lines) - 1


def test_a_session_that_cannot_be_read_is_named_and_the_others_are_read(tmp_path) -> None:
    phone, logger = folders(tmp_path)
    phone_session(phone, datetime(2026, 7, 1, 10, 0), 900)
    (phone / f'Polar_H10_{SERIAL}_20260702_100000_HR.txt').mkdir()
    with pytest.raises(polar.PartlyRead, match=r'Polar_H10_E813952E_20260702_100000_HR\.txt'):
        polar.import_all(phone, logger, TZ)
    assert len(all_sessions()) == 1
    assert not any('20260702' in path for path in polar_store.known_files()), 'tried again at the next import'


def test_a_logger_session_json_without_a_device_is_read(tmp_path) -> None:
    phone, logger = folders(tmp_path)
    path = logger_session(logger, datetime(2026, 7, 1, 18, 0, tzinfo=TZ), 900)
    info = json.loads((path / 'session.json').read_text())
    (path / 'session.json').write_text(json.dumps(info | {'device': None}))
    assert polar.import_all(phone, logger, TZ) == 1
    assert all_sessions()[0]['serial'] == ''


def test_an_unexpected_failure_is_shown_on_the_page(monkeypatch) -> None:
    def broken(*args: object) -> int:
        """An import that fails in a way nobody planned for."""
        raise RuntimeError('boom')

    monkeypatch.setattr(polar, 'import_all', broken)
    assert polar.begin()
    polar.run_import(TZ)
    assert 'boom' in (store.setting('polar_error') or '') and not polar.status()['running']


def test_new_calculations_read_every_session_again_and_keep_the_removed_state(tmp_path, monkeypatch) -> None:
    phone, logger = folders(tmp_path)
    phone_session(phone, datetime(2026, 7, 1, 10, 0), 900)
    polar.import_all(phone, logger, TZ)
    (session,) = all_sessions()
    polar_store.set_removed([session['id']], True)
    assert polar.import_all(phone, logger, TZ) == 0
    monkeypatch.setattr(polar, 'CALC_VERSION', polar.CALC_VERSION + 1)
    assert polar.import_all(phone, logger, TZ) == 1
    assert polar.import_all(phone, logger, TZ) == 0
    assert all_sessions()[0]['removed'] == 1


def test_only_one_import_runs_at_a_time() -> None:
    assert polar.begin()
    try:
        assert not polar.begin()
    finally:
        polar._running.release()


def markers_of(session_id: int) -> list[tuple]:
    """The markers of a session: label and HR, in time order."""
    with ledger.connect() as db:
        return [tuple(r) for r in db.execute('SELECT label, hr FROM polar_markers WHERE session_id = ? ORDER BY t_ms',
                                             (session_id,))]


def test_the_markers_of_a_logger_session_come_with_their_hr_and_old_labels_are_left_out(tmp_path) -> None:
    phone, logger = folders(tmp_path)
    logger_session(logger, datetime(2026, 7, 1, 18, 0, tzinfo=TZ), 900,
                   markers=[(599, 'cooldown'), (100, 'effort done'), (300, 'angina onset'), (595, 'effort done'),
                            (400, 'exercise start'), (500, 'exercise stop')])
    assert polar.import_all(phone, logger, TZ) == 1
    (session,) = all_sessions()
    assert markers_of(session['id']) == [('effort done', 80.0), ('effort done', 150.0)], 'the removed buttons'
    assert (session['recovery'], session['recovery_state']) == (None, None)


def test_a_marker_label_with_a_comma_is_read_whole_and_a_bad_row_is_left_out(tmp_path) -> None:
    phone, logger = folders(tmp_path)
    path = logger_session(logger, datetime(2026, 7, 1, 18, 0, tzinfo=TZ), 900, markers=[(100, 'exercise start, stairs')])
    with (path / 'markers.csv').open('a') as out:
        out.write('not a time,exercise stop,key\n')
    polar.import_all(phone, logger, TZ)
    assert [m[0] for m in markers_of(all_sessions()[0]['id'])] == ['exercise start, stairs']


def test_the_presses_of_the_phone_marker_go_to_the_phone_session_that_started_last_before_them(tmp_path) -> None:
    phone, logger = folders(tmp_path)
    phone_session(phone, datetime(2026, 7, 1, 10, 0), 900)
    phone_session(phone, datetime(2026, 7, 1, 10, 16), 900)
    phone_markers(phone, [(datetime(2026, 7, 1, 10, 1), True), (datetime(2026, 7, 1, 10, 9, 59), False)])
    phone_markers(phone, [(datetime(2026, 7, 1, 10, 17), True)])
    phone_markers(phone, [(datetime(2026, 7, 1, 9, 0), True)])
    assert polar.import_all(phone, logger, TZ) == 2
    first, second = all_sessions()
    assert markers_of(first['id']) == [('marker start', 80.0), ('marker stop', 150.0)]
    assert markers_of(second['id']) == [('marker start', 80.0)], 'a file before every session belongs to none'


def test_a_new_phone_marker_file_reads_its_session_again(tmp_path, monkeypatch) -> None:
    phone, logger = folders(tmp_path)
    monkeypatch.setattr(polar, 'folders', lambda: (phone, logger))
    phone_session(phone, datetime(2026, 7, 1, 10, 0), 900)
    polar.import_all(phone, logger, TZ)
    phone_markers(phone, [(datetime(2026, 7, 1, 10, 1), True), (datetime(2026, 7, 1, 10, 2), False)])
    assert polar.waiting()['sessions'] == 1
    polar.import_all(phone, logger, TZ)
    assert [m[0] for m in markers_of(all_sessions()[0]['id'])] == ['marker start', 'marker stop']


def test_a_changed_markers_file_reads_its_session_again(tmp_path) -> None:
    phone, logger = folders(tmp_path)
    path = logger_session(logger, datetime(2026, 7, 1, 18, 0, tzinfo=TZ), 900, markers=[(100, 'effort done')])
    polar.import_all(phone, logger, TZ)
    with (path / 'markers.csv').open('a') as out:
        out.write(f'{int(datetime(2026, 7, 1, 18, 5, tzinfo=TZ).timestamp()) * 10 ** 9},effort done,key\n')
    assert polar.import_all(phone, logger, TZ) == 1
    assert [m[0] for m in markers_of(all_sessions()[0]['id'])] == ['effort done', 'effort done']


def test_a_marker_far_from_its_session_is_left_out_so_the_page_still_shows_the_session(tmp_path) -> None:
    phone, logger = folders(tmp_path)
    path = logger_session(logger, datetime(2026, 7, 1, 18, 0, tzinfo=TZ), 900, markers=[(100, 'effort done')])
    with (path / 'markers.csv').open('a') as out:
        out.write('300000000000000000000,effort done,key\n')
    polar.import_all(phone, logger, TZ)
    (listed,) = polar_store.sessions(0, 5)['sessions']
    assert [(m['label'], m['hr']) for m in listed['markers']] == [('effort done', 80.0)]


def test_a_markers_file_that_cannot_be_read_is_named_and_the_other_sessions_are_read(tmp_path) -> None:
    phone, logger = folders(tmp_path)
    logger_session(logger, datetime(2026, 7, 1, 18, 0, tzinfo=TZ), 900, markers=[(100, 'x' * 200_000)])
    logger_session(logger, datetime(2026, 7, 2, 18, 0, tzinfo=TZ), 900, markers=[(100, 'effort done')])
    with pytest.raises(polar.PartlyRead, match=r'2026-07-01_18-00-00: markers\.csv'):
        polar.import_all(phone, logger, TZ)
    assert [s['day'] for s in all_sessions()] == ['2026-07-02']




def test_the_waiting_sessions_are_those_the_next_import_reads(tmp_path, monkeypatch) -> None:
    phone, logger = folders(tmp_path)
    monkeypatch.setattr(polar, 'folders', lambda: (phone, logger))
    phone_session(phone, datetime(2026, 7, 1, 10, 0), 300)
    logger_session(logger, datetime(2026, 7, 1, 18, 0, tzinfo=TZ), 300)
    logger_session(logger, datetime(2026, 7, 2, 18, 0, tzinfo=TZ), 300, ended=False)
    assert polar.waiting() == {'sessions': 2, 'recalc': False}, 'a logger session waits for its end'
    polar.import_all(phone, logger, TZ)
    assert polar.waiting() == {'sessions': 0, 'recalc': False}
    phone_session(phone, datetime(2026, 7, 3, 10, 0), 300)
    assert polar.waiting() == {'sessions': 1, 'recalc': False}
    store.set_setting('polar_calc_version', '4')
    assert polar.waiting() == {'sessions': 3, 'recalc': True}
    monkeypatch.setattr(polar, 'folders', lambda: (tmp_path / 'gone', None))
    assert polar.waiting()['sessions'] == 0, 'a folder that is not there has no sessions'


def test_the_import_counts_the_saved_sessions_for_the_page(tmp_path) -> None:
    phone, logger = folders(tmp_path)
    phone_session(phone, datetime(2026, 7, 1, 10, 0), 300)
    phone_session(phone, datetime(2026, 7, 1, 11, 0), 300)
    polar.import_all(phone, logger, TZ)
    assert polar.status()['saved'] == 2
