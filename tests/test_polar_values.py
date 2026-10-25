"""The values of a Polar H10 session from its arrays: HR and movement per second, clean beats, the HR at a marker, the
values of the HRV test, and the raw samples packed in blocks without loss."""


import numpy as np

from health_dashboard import polar_values as pv


def step_hr() -> np.ndarray:
    """80 bpm, 10 s at 150 (the hardest effort, its last second at 599), then 120 bpm."""
    hr = np.full(900, 120.0)
    hr[:590] = 80
    hr[590:600] = 150
    return hr


def test_the_hr_of_each_second_lies_between_the_samples_and_a_dropout_has_none() -> None:
    hr = pv.per_second_hr(np.array([0, 1000, 2000, 20000]), np.array([60.0, 62, 64, 80]), 0, 21)
    assert list(hr[:3]) == [60, 62, 64] and hr[20] == 80
    assert not np.isnan(hr[3]) and np.isnan(hr[8]), 'a second 6 s from the nearest sample has no HR'


def test_the_movement_of_a_second_is_the_spread_of_the_size_of_the_acceleration() -> None:
    at = np.concatenate([np.arange(0, 1000, 40), np.arange(1000, 2000, 40), [2100, 2200]])
    x = np.concatenate([np.full(25, 1000), 1000 + 100 * (-1) ** np.arange(25), [1000, 1000]])
    xyz = np.column_stack([x, np.zeros(len(x)), np.zeros(len(x))])
    moves = pv.Movement(0, 3)
    moves.add(at[:30], xyz[:30])
    moves.add(at[30:], xyz[30:])
    values = moves.values()
    assert values[0] == 0 and abs(values[1] - 100) < 0.5
    assert np.isnan(values[2]), 'fewer than 5 samples: no movement'


def test_a_missed_beat_and_a_too_short_interval_are_not_clean() -> None:
    rr = np.full(20, 800.0)
    rr[10], rr[15] = 1600, 250
    assert list(np.flatnonzero(~pv.clean_beats(rr))) == [10, 15]


def test_packed_samples_come_back_exactly_also_after_a_spike() -> None:
    samples = np.array([[0, 5, -3], [100_000, -100_000, 7], [-2_000_000, 3, 3], [12, 0, -1]])
    assert np.array_equal(pv.unpack(pv.pack(samples), 3), samples)
    ecg = np.array([-50, -48, 30000, -30000, 0]).reshape(-1, 1)
    assert np.array_equal(pv.unpack(pv.pack(ecg), 1), ecg)


def test_a_block_starts_at_each_gap_and_holds_at_most_its_number_of_samples() -> None:
    sensor = np.delete(np.arange(300) * 10_000_000, range(100, 150))
    assert pv.cut_points(sensor, 10_000_000, 100) == [0, 100, 200]


def test_blocks_from_parts_hold_every_sample_once_in_order() -> None:
    sensor = np.delete(np.arange(10_000) * 7_692_308, range(5000, 5650))
    values = (np.arange(len(sensor)) % 97 - 48).reshape(-1, 1)
    at = sensor // 1_000_000
    cutter = pv.Blocks('ecg')
    for start in range(0, len(sensor), 3000):
        cutter.add(at[start:start + 3000], sensor[start:start + 3000], values[start:start + 3000])
    rows = cutter.close()
    assert np.array_equal(np.concatenate([pv.unpack(r[6], r[5]) for r in rows]), values)
    assert all(r[4] <= 7800 for r in rows), 'at most 60 s of samples'
    assert any(r[2] == sensor[5000] for r in rows), 'a block starts after the dropout'


START_MS = 1_782_892_800_000


def test_the_hr_at_a_marker_is_the_hr_of_its_second() -> None:
    hr = np.arange(100.0)
    hr[20] = np.nan
    marks = pv.marker_values(hr, START_MS, [(START_MS + 10_400, 'exercise start'), (START_MS + 20_000, 'exercise stop'),
                                           (START_MS + 200_000, 'exercise stop')])
    assert marks == [(START_MS + 10_400, 'exercise start', 10.0), (START_MS + 20_000, 'exercise stop', None),
                     (START_MS + 200_000, 'exercise stop', None)]


def test_a_second_is_still_by_the_median_movement_of_the_11_seconds_around_it() -> None:
    movement = np.concatenate([np.full(100, 8.0), np.full(100, 200.0)])
    movement[150] = 5.0
    still = pv.still_seconds(movement)
    assert list(still[:100]) == [1.0] * 100 and list(still[100:]) == [0.0] * 100, 'one quiet second in a walk is no stop'
    movement[60:90] = np.nan
    assert np.isnan(pv.still_seconds(movement)[75]), 'no movement data around it: unknown'
    assert pv.still_seconds(None) is None and pv.still_seconds(np.full(10, np.nan)) is None


def test_zones_without_any_hr_have_no_values() -> None:
    assert pv.zone_seconds(np.full(300, np.nan), [77, 92, 108, 123, 139]) == [0, 0, 0, 0, 0, 0]


def test_the_split_in_movement_and_still_is_gone() -> None:
    assert not [name for name in ('movement_values', 'parts', 'rising_hr') if hasattr(pv, name)]


def test_the_zones_come_from_220_minus_the_age_in_the_year_of_the_session() -> None:
    assert pv.zone_limits(1960, 2026) == (154, [77, 92, 108, 123, 139])
    assert pv.zone_limits(1960, 2020) == (160, [80, 96, 112, 128, 144])
    hr = np.array([70.0, 77, 91, 92, 140, np.nan])
    assert pv.zone_seconds(hr, [77, 92, 108, 123, 139]) == [1, 2, 1, 0, 0, 1]


def alternating_beats(seconds: int, swing: float = 20.0) -> tuple[np.ndarray, np.ndarray]:
    """Beats of 1000 - swing and 1000 + swing ms in turn for `seconds`: their times (ms from 0) and RR."""
    rr = np.array([1000 - swing, 1000 + swing] * (seconds // 2))
    return np.cumsum(rr).astype(np.int64), rr


def swinging_beats(freq_hz: float, seconds: int = 420, base: float = 1000.0) -> tuple[np.ndarray, np.ndarray]:
    """Beats of `base` ms that swing 50 ms up and down `freq_hz` times a second: their times (ms) and RR."""
    times, rr, t = [], [], 0.0
    while t < seconds * 1000:
        interval = base + 50 * np.sin(2 * np.pi * freq_hz * t / 1000)
        t += interval
        times.append(t)
        rr.append(interval)
    return np.array(times).astype(np.int64), np.array(rr)


def test_an_hrv_test_uses_the_5_minutes_after_the_first_minute() -> None:
    at, rr = alternating_beats(420)
    found = pv.hrv_values(at, rr, pv.clean_beats(rr))
    assert (found['reason'], found['rest_hr'], found['rmssd'], found['ln_rmssd'], found['sdnn'], found['pnn50']) == (
        None, 60.0, 40.0, 3.69, 20.0, 0.0)
    assert (found['wrong_pct'], found['from_ms'], found['to_ms']) == (0.0, 980 + 60_000, 980 + 360_000)
    assert set(found) == set(pv.HRV_KEYS)


def test_a_test_time_of_3_minutes_uses_the_3_minutes_after_the_first_minute() -> None:
    at, rr = alternating_beats(420)
    found = pv.hrv_values(at, rr, pv.clean_beats(rr), 180)
    assert (found['reason'], found['rmssd'], found['from_ms'], found['to_ms']) == (None, 40.0, 980 + 60_000, 980 + 240_000)
    assert found['lf'] is not None, 'a 3-minute window holds two 120 s segments'
    at, rr = alternating_beats(200)
    assert pv.hrv_values(at, rr, pv.clean_beats(rr), 180)['reason'] == 'The test needs 4 minutes; this recording has 3 min 19 s.'


def test_a_slow_swing_is_lf_and_a_fast_one_hf() -> None:
    at, rr = swinging_beats(0.1)
    slow = pv.hrv_values(at, rr, pv.clean_beats(rr))
    assert slow['lf'] > 5 * max(slow['hf'], 1) and 800 < slow['lf'] < 1700, 'about 50² / 2 ms² at 0.1 Hz'
    assert slow['lf_hf'] is None, 'a pure 0.1 Hz swing has no HF to divide by'
    at, rr = swinging_beats(0.25)
    fast = pv.hrv_values(at, rr, pv.clean_beats(rr))
    assert fast['lf_hf'] < 0.2 and 800 < fast['hf'] < 1700


def test_the_stress_index_is_the_root_of_baevskys_index() -> None:
    rr = np.array([1000.0] * 80 + [900.0] * 10 + [1100.0] * 10)
    assert pv.stress_index(rr) == round((80 / (2 * 1.025 * 0.2)) ** 0.5, 1)
    assert pv.stress_index(np.full(10, 1000.0)) is None


def test_a_short_recording_or_too_few_clean_beats_give_a_reason() -> None:
    at, rr = alternating_beats(300)
    assert pv.hrv_values(at, rr, pv.clean_beats(rr))['reason'] == 'The test needs 6 minutes; this recording has 4 min 59 s.'
    rr = np.array([1000.0, 1000.0, 1000.0, 2500.0] * 120)
    found = pv.hrv_values(np.cumsum(rr).astype(np.int64), rr, pv.clean_beats(rr))
    assert found['reason'] == 'Only 55 % of the beats are clean; the test needs 80 %.', 'a 2.5 s interval also hides missed beats'
    assert found['rmssd'] is None and 40 < found['wrong_pct'] < 50


def test_hrv_values_without_beats_give_a_reason() -> None:
    empty = np.zeros(0)
    found = pv.hrv_values(empty.astype(np.int64), empty, empty.astype(bool))
    assert found['reason'] == 'The session has no beat intervals (no RR file), so it has no HRV values.'


def test_a_window_without_beats_gives_the_clean_share_reason() -> None:
    at = np.array([0, 1000, 400_000, 401_000], dtype=np.int64)
    rr = np.array([1000.0, 1000.0, 1000.0, 1000.0])
    assert pv.hrv_values(at, rr, pv.clean_beats(rr))['reason'] == 'Only 0 % of the beats are clean; the test needs 80 %.'


def test_a_dropout_in_the_window_counts_as_beats_that_are_not_clean() -> None:
    rr = np.array([1000.0] * 160 + [240_000.0] + [1000.0] * 20)
    found = pv.hrv_values(np.cumsum(rr).astype(np.int64), rr, pv.clean_beats(rr))
    assert found['reason'] == 'Only 33 % of the beats are clean; the test needs 80 %.', 'about 200 of 300 beats are missing'
    rr = np.array([1000.0] * 200 + [51_000.0] + [1000.0] * 170)
    found = pv.hrv_values(np.cumsum(rr).astype(np.int64), rr, pv.clean_beats(rr))
    assert (found['reason'], found['wrong_pct'], found['lf'] is not None) == (None, 17.0, True), 'a gap of 50 s: 51 beats not there'


def test_the_frequency_values_use_the_whole_5_minutes() -> None:
    # a swing in the last minute of the window only; one interval of 2.5 s (not clean) at 357 s, so the clean beats end a few
    # seconds before the end of the window, as real beats can
    times, rr, t = [1500.0], [1500.0], 1500.0
    while t < 420_000:
        swing = 50 * np.sin(2 * np.pi * 0.1 * t / 1000) if 301_500 <= t < 357_000 else 0
        interval = 2500.0 if 357_000 <= t < 358_000 else 1000 + swing
        t += interval
        times.append(t)
        rr.append(interval)
    found = pv.hrv_values(np.array(times).astype(np.int64), np.array(rr), pv.clean_beats(np.array(rr)))
    assert found['lf'] > 50, 'a swing in the last minute of the window only'


def test_beats_that_share_the_time_of_their_bluetooth_message_give_the_same_frequency_values() -> None:
    at, rr = swinging_beats(0.25, base=800.0)
    exact = pv.hrv_values(at, rr, pv.clean_beats(rr))
    message = (np.ceil(at / 1000) * 1000).astype(np.int64)
    shared = pv.hrv_values(message, rr, pv.clean_beats(rr))
    assert abs(shared['hf'] - exact['hf']) <= 0.03 * exact['hf'], 'the PC logger gives the beats of one message its time'


def test_a_swing_on_the_border_of_lf_and_hf_keeps_its_power() -> None:
    at, rr = swinging_beats(0.15)
    found = pv.hrv_values(at, rr, pv.clean_beats(rr))
    assert found['lf'] + found['hf'] > 900, 'about 50² / 2 ms², split between the two bands'


def test_a_constant_beat_has_no_lf_hf() -> None:
    rr = np.full(420, 1000.0)
    found = pv.hrv_values(np.cumsum(rr).astype(np.int64), rr, pv.clean_beats(rr))
    assert (found['reason'], found['lf_hf']) == (None, None)


def test_the_share_of_clean_beats_is_rounded_down() -> None:
    rr = np.full(420, 1000.0)
    at = np.cumsum(rr).astype(np.int64)
    clean = pv.clean_beats(rr)
    clean[100:161] = False
    assert pv.hrv_values(at, rr, clean)['reason'] == 'Only 79 % of the beats are clean; the test needs 80 %.'


def effort_then_rest(seconds: int = 330, stop: int = 182, fall: float = 0.5) -> np.ndarray:
    """150 bpm up to the stop (its last second at 150), then `fall` bpm less each second."""
    t = np.arange(seconds, dtype=float)
    return np.where(t <= stop, 150.0, 150.0 - fall * (t - stop))


def test_the_stop_of_a_recovery_is_the_last_highest_hr_and_the_drops_use_the_5_seconds_around() -> None:
    assert pv.recovery_values(effort_then_rest(), None, None) == {
        'hr_stop': 149.7, 'hr_60': 120.0, 'hr_120': 90.0, 'drop_60': 29.7, 'drop_120': 59.7, 'stop_s': 182,
        'stop_from': 'highest HR', 'reason': None}


def test_the_effort_done_marker_is_the_stop() -> None:
    found = pv.recovery_values(effort_then_rest(), None, 180)
    assert (found['stop_s'], found['stop_from'], found['hr_stop'], found['drop_60'], found['drop_120']) == (
        180, 'marker', 150.0, 29.0, 59.0)


def test_a_marker_outside_the_seconds_falls_back_to_the_highest_hr() -> None:
    assert pv.recovery_values(effort_then_rest(), None, 999)['stop_from'] == 'highest HR'


def test_a_recovery_needs_2_minutes_an_hr_at_its_points_and_a_still_chest() -> None:
    assert pv.recovery_values(effort_then_rest(seconds=250), None, None)['reason'] == \
        'The test needs 2 minutes after the stop; this recording has 67 s after it.'
    gap = effort_then_rest()
    gap[238:247] = np.nan
    assert pv.recovery_values(gap, None, None)['reason'] == 'The strap sent no HR at the stop or 60 s or 120 s after it.'
    moving = np.full(330, 200.0)
    assert pv.recovery_values(effort_then_rest(), moving, None)['reason'] == \
        'After the stop the chest was not still for 2 minutes. Sit still for 2 minutes after the stop.'
    sitting = np.where(np.arange(330) < 190, 200.0, 8.0)
    assert pv.recovery_values(effort_then_rest(), sitting, None)['reason'] is None, '10 s to sit down'
    unknown = np.full(330, np.nan)
    assert pv.recovery_values(effort_then_rest(), unknown, None)['reason'] is None, 'no movement data: no still rule'


def test_a_recovery_without_hr_has_a_reason() -> None:
    found = pv.recovery_values(np.full(300, np.nan), None, None)
    assert (found['reason'], found['stop_s']) == ('The strap sent no HR in this session.', None)
    assert pv.recovery_values(np.array([]), None, None)['reason'] == 'The strap sent no HR in this session.'


def climb_with_two_pauses() -> tuple[np.ndarray, list[tuple[int, str]]]:
    """A climb: start at second 20, pauses from 60 to 75 and from 120 to 150, at the 8th floor at second 200, then 150 s
    of rest; the HR climbs to 160 at the top and then falls 0.5 bpm a second."""
    hr = np.where(np.arange(360) <= 200, 100.0 + np.arange(360) * 0.3, 160.0 - 0.5 * (np.arange(360) - 200))
    on, off = pv.PHONE_START, pv.PHONE_STOP
    return hr, [(20, on), (60, off), (75, on), (120, off), (150, on), (200, off)]


def test_a_stairs_test_has_its_climb_time_its_pauses_its_highest_hr_and_the_hr_after_the_top() -> None:
    hr, presses = climb_with_two_pauses()
    assert pv.stairs_values(hr, None, presses) == {
        'climb_s': 180, 'pauses': 2, 'pause_s': 45, 'max_hr': 160.0, 'start_s': 20, 'end_s': 200,
        'pause_list': [{'at_s': 60, 'seconds': 15}, {'at_s': 120, 'seconds': 30}],
        'hr_top': 159.5, 'hr_60': 130.0, 'hr_120': 100.0, 'drop_60': 29.5, 'drop_120': 59.5, 'recovery_reason': None,
        'reason': None}


def test_a_climb_without_a_pause_and_presses_before_the_start_or_outside_the_seconds() -> None:
    hr, _ = climb_with_two_pauses()
    found = pv.stairs_values(hr, None, [(-30, pv.PHONE_START), (5, pv.PHONE_STOP), (20, pv.PHONE_START), (200, pv.PHONE_STOP),
                                       (999, pv.PHONE_START)])
    assert (found['climb_s'], found['pauses'], found['pause_s'], found['pause_list']) == (180, 0, 0, [])
    twice = pv.stairs_values(hr, None, [(20, pv.PHONE_START), (30, pv.PHONE_START), (200, pv.PHONE_STOP)])
    assert (twice['start_s'], twice['pauses']) == (20, 0), 'a second start while the marker is on changes nothing'


def test_a_stairs_test_without_presses_or_with_the_marker_still_on_has_a_reason() -> None:
    hr, _ = climb_with_two_pauses()
    assert pv.stairs_values(hr, None, [])['reason'] == \
        'The recording has no marker presses. Press the marker at the start, at each pause and at the 8th floor.'
    assert pv.stairs_values(hr, None, [(20, pv.PHONE_START), (60, pv.PHONE_STOP), (75, pv.PHONE_START)])['reason'] == \
        'The marker is still on: press it at the 8th floor.'
    found = pv.stairs_values(np.full(300, np.nan), None, [(20, pv.PHONE_START), (200, pv.PHONE_STOP)])
    assert (found['reason'], found['climb_s']) == ('The strap sent no HR in this session.', None)


def test_the_climb_values_stay_when_the_hr_after_the_top_has_none() -> None:
    hr, presses = climb_with_two_pauses()
    found = pv.stairs_values(hr[:260], None, presses)
    assert (found['reason'], found['climb_s'], found['drop_60']) == (None, 180, None)
    assert found['recovery_reason'] == 'The test needs 2 minutes after the stop; this recording has 59 s after it.'
