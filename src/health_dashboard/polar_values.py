"""The values of a Polar H10 session, from its arrays: the HR and the movement of each second, the still seconds (the HR
recovery test sits still after its effort), the 5 heart rate zones, the clean beats, the values of an HRV test, the HR at a
marker; and the raw samples (ECG, acceleration) packed in blocks without loss. No split of a session in movement and still
(removed on 2026-10-21): an effort with the arms on a bench keeps the chest still."""

import warnings
import zlib

import numpy as np

# below this movement (mg) a second is still: sitting or standing still measured 4 to 13 mg, walking 150 mg and more
STILL_MG = 25
# a second needs this many acceleration samples for its movement
MIN_SAMPLES = 5
# a second further than this from an HR sample has no HR (a dropout)
HR_GAP_S = 5
# a real beat: 300 to 2000 ms, at most 20 % from the median of the 5 beats on each side
RR_MIN_MS, RR_MAX_MS, RR_AROUND, RR_OFF = 300, 2000, 5, 0.2
# a second is still when the median movement of the 11 seconds around it is below STILL_MG
STILL_AROUND_S = 5
# the HRV test: its first minute left out, then the test time chosen at its Start (5 minutes for the tests from before that
# choice); values only when the recording reaches the end of that window and at least 80 % of the beats in it are clean
HRV_SKIP_S, HRV_USE_S, HRV_MIN_CLEAN = 60, 300, 0.8
# the frequency values: the clean beats resampled at 4 Hz, a Welch spectrum of 120 s Hann segments with 50 % overlap; LF
# from 0.04 to 0.15 Hz, HF from 0.15 to 0.40 Hz
HRV_RESAMPLE_HZ, HRV_SEGMENT_S, LF_BAND, HF_BAND = 4, 120, (0.04, 0.15), (0.15, 0.40)
# the stress index (Baevsky): the beat intervals in bins of 50 ms
STRESS_BIN_MS = 50
# no LF/HF below this HF power (ms²): two near-zero powers give a ratio of noise
HF_MIN_MS2 = 1
# a beat time further than this (ms) from the sum of the intervals before it starts the sum again: beats are missing there
BEAT_GAP_MS = 2000
HRV_KEYS = ('rest_hr', 'rmssd', 'ln_rmssd', 'sdnn', 'pnn50', 'lf', 'hf', 'lf_hf', 'stress', 'wrong_pct', 'from_ms', 'to_ms', 'reason')
# the 5 Polar zones start at 50, 60, 70, 80 and 90 % of the maximum HR, 220 - age; zone 5 has no upper limit
ZONE_PERCENTS = (50, 60, 70, 80, 90)
# the HR recovery test: the marker of its Effort done button; 120 s after the stop; the HR at a time is the mean of the
# seconds from 2 s before to 2 s after it; at least 80 % of the seconds with movement data from 10 s after the stop (the
# time to sit down) to 120 s are still
EFFORT_DONE = 'effort done'
RECOVERY_S, AROUND_S, SIT_DOWN_S, STILL_SHARE = 120, 2, 10, 0.8
# the marker of the phone app: one switch, each press a start or a stop (MARKER_START, MARKER_STOP in its file). In the
# stairs test the marker is on while the user climbs
PHONE_START, PHONE_STOP = 'marker start', 'marker stop'
# a block of raw samples holds at most 60 s; a gap of more than 3 sample periods starts a new one
BLOCK_S, GAP_PERIODS = 60, 3


def per_second_hr(at_ms: np.ndarray, bpm: np.ndarray, start_ms: int, seconds: int) -> np.ndarray:
    """The HR at each whole second from the start, between the samples (sorted by time); NaN where the nearest sample is more
    than 5 s away."""
    t = start_ms + 1000 * np.arange(seconds)
    hr = np.interp(t, at_ms, bpm)
    after = np.clip(np.searchsorted(at_ms, t), 0, len(at_ms) - 1)
    before = np.clip(after - 1, 0, len(at_ms) - 1)
    nearest = np.minimum(np.abs(at_ms[after] - t), np.abs(at_ms[before] - t))
    hr[nearest > HR_GAP_S * 1000] = np.nan
    return hr


class Movement:
    """The movement of each second of a session, gathered from the acceleration in parts: the standard deviation of the size
    of the acceleration in that second, in mg."""

    def __init__(self, start_ms: int, seconds: int) -> None:
        """Empty sums for each second."""
        self.start_ms, self.seconds = start_ms, seconds
        self.count = np.zeros(seconds)
        self.total = np.zeros(seconds)
        self.squares = np.zeros(seconds)

    def add(self, at_ms: np.ndarray, xyz: np.ndarray) -> None:
        """Add a part of the samples: their UTC times in ms and their X, Y and Z in mg."""
        second = (at_ms - self.start_ms) // 1000
        inside = (second >= 0) & (second < self.seconds)
        size = np.linalg.norm(xyz[inside].astype(float), axis=1)
        second = second[inside].astype(np.int64)
        self.count += np.bincount(second, minlength=self.seconds)
        self.total += np.bincount(second, size, minlength=self.seconds)
        self.squares += np.bincount(second, size ** 2, minlength=self.seconds)

    def values(self) -> np.ndarray:
        """The movement of each second; NaN for a second with fewer than 5 samples."""
        with np.errstate(invalid='ignore', divide='ignore'):
            mean = self.total / self.count
            spread = np.sqrt(np.maximum(self.squares / self.count - mean ** 2, 0))
        spread[self.count < MIN_SAMPLES] = np.nan
        return spread


def clean_beats(rr: np.ndarray) -> np.ndarray:
    """Whether each RR interval is a real beat: 300 to 2000 ms, and at most 20 % from the median of the 5 beats before and
    the 5 after it (a missed or an extra beat is far from it)."""
    if len(rr) == 0:
        return np.zeros(0, dtype=bool)
    padded = np.concatenate([np.full(RR_AROUND, np.nan), rr.astype(float), np.full(RR_AROUND, np.nan)])
    around = np.delete(np.lib.stride_tricks.sliding_window_view(padded, 2 * RR_AROUND + 1), RR_AROUND, axis=1)
    with warnings.catch_warnings():
        # a session of one beat has no beats around it
        warnings.simplefilter('ignore', RuntimeWarning)
        median = np.nanmedian(around, axis=1)
    near = np.isnan(median) | (np.abs(rr - median) <= RR_OFF * median)
    return (rr >= RR_MIN_MS) & (rr <= RR_MAX_MS) & near


def marker_values(hr: np.ndarray, start_ms: int, markers: list[tuple[int, str]]) -> list[tuple[int, str, float | None]]:
    """Each marker (UTC ms, label) with the HR of its second; None for a marker outside the session or on a second without
    HR."""
    found = []
    for t_ms, label in markers:
        second = round((t_ms - start_ms) / 1000)
        at = float(hr[second]) if 0 <= second < len(hr) and not np.isnan(hr[second]) else None
        found.append((t_ms, label, None if at is None else round(at, 1)))
    return found


def means_of(tests: list[dict], keys: dict[str, tuple[str, int]]) -> dict[str, float]:
    """The mean of each value of some tests (the tests of a day), under its history key and to its decimals (`keys`: field
    -> key, decimals); a value that no test has is left out."""
    found = {}
    for field, (key, decimals) in keys.items():
        values = [t[field] for t in tests if t.get(field) is not None]
        if values:
            found[key] = round(sum(values) / len(values), decimals)
    return found


def _hr_at(hr: np.ndarray, i: int) -> float | None:
    """The mean HR of the seconds from i - AROUND_S to i + AROUND_S that have one, to 0.1; None when none has."""
    found = hr[max(0, i - AROUND_S):i + AROUND_S + 1]
    found = found[~np.isnan(found)]
    return round(float(found.mean()), 1) if len(found) else None


def recovery_values(hr: np.ndarray, movement: np.ndarray | None, marker_i: int | None) -> dict:
    """The values of an HR recovery test from the HR (NaN: none) and the movement of each second: the stop is the second
    `marker_i` of the last Effort done marker, else the last second with the highest HR; the HR at the stop and 60 and 120 s
    after it, the two drops, the stop (its second, and 'marker' or 'highest HR'); or the reason the test has no values."""
    found = dict.fromkeys(('hr_stop', 'hr_60', 'hr_120', 'drop_60', 'drop_120', 'stop_s', 'stop_from', 'reason'))
    if not len(hr) or np.isnan(hr).all():
        return found | {'reason': 'The strap sent no HR in this session.'}
    if marker_i is not None and 0 <= marker_i < len(hr):
        stop, how = marker_i, 'marker'
    else:
        stop, how = len(hr) - 1 - int(np.nanargmax(hr[::-1])), 'highest HR'
    found |= {'stop_s': stop, 'stop_from': how}
    after = len(hr) - 1 - stop
    if after < RECOVERY_S:
        return found | {'reason': f'The test needs 2 minutes after the stop; this recording has {after} s after it.'}
    at_stop, at_60, at_120 = _hr_at(hr, stop), _hr_at(hr, stop + 60), _hr_at(hr, stop + RECOVERY_S)
    if at_stop is None or at_60 is None or at_120 is None:
        return found | {'reason': 'The strap sent no HR at the stop or 60 s or 120 s after it.'}
    still = still_seconds(movement)
    if still is not None:
        window = still[stop + SIT_DOWN_S:stop + RECOVERY_S + 1]
        known = window[~np.isnan(window)]
        if len(known) and known.mean() < STILL_SHARE:
            return found | {'reason': 'After the stop the chest was not still for 2 minutes. Sit still for 2 minutes after '
                                      'the stop.'}
    return found | {'hr_stop': at_stop, 'hr_60': at_60, 'hr_120': at_120, 'drop_60': round(at_stop - at_60, 1),
                    'drop_120': round(at_stop - at_120, 1)}


def stairs_values(hr: np.ndarray, movement: np.ndarray | None, presses: list[tuple[int, str]]) -> dict:
    """The values of a stairs test from the HR (NaN: none) and the movement of each second and the presses of the phone
    marker (second, PHONE_START or PHONE_STOP) in time order. The climb is from the first start to the last stop; each stop
    with a start after it is a pause; a press outside the seconds or one that does not switch the marker changes nothing.
    The climb time, the pauses, the highest HR of the climb, and the HR at the top and 60 and 120 s after it with their drops
    (the HR recovery rules, the top as the stop; when these have none, recovery_reason says why); or the reason the test has
    no values."""
    found = dict.fromkeys(('climb_s', 'pauses', 'pause_s', 'max_hr', 'start_s', 'end_s', 'pause_list', 'hr_top', 'hr_60',
                           'hr_120', 'drop_60', 'drop_120', 'recovery_reason', 'reason'))
    if not len(hr) or np.isnan(hr).all():
        return found | {'reason': 'The strap sent no HR in this session.'}
    start = on = stop = None
    pauses = []
    for second, label in presses:
        if not 0 <= second < len(hr):
            continue
        if label == PHONE_START and not on:
            if start is None:
                start = second
            elif stop is not None:
                pauses.append({'at_s': stop, 'seconds': second - stop})
            on = True
        elif label == PHONE_STOP and on:
            stop, on = second, False
    if start is None:
        return found | {'reason': 'The recording has no marker presses. Press the marker at the start, at each pause and at '
                                  'the 8th floor.'}
    if on:
        return found | {'reason': 'The marker is still on: press it at the 8th floor.'}
    climb = hr[start:stop + 1]
    after = recovery_values(hr, movement, stop)
    return found | {'climb_s': stop - start, 'pauses': len(pauses), 'pause_s': sum(p['seconds'] for p in pauses),
                    'max_hr': None if np.isnan(climb).all() else round(float(np.nanmax(climb)), 1), 'start_s': start,
                    'end_s': stop, 'pause_list': pauses, 'hr_top': after['hr_stop'], 'hr_60': after['hr_60'],
                    'hr_120': after['hr_120'], 'drop_60': after['drop_60'], 'drop_120': after['drop_120'],
                    'recovery_reason': after['reason']}


def still_seconds(movement: np.ndarray | None) -> np.ndarray | None:
    """Whether each second is still (1.0), in movement (0.0) or unknown (NaN): still when the median movement of the 11
    seconds around it is below STILL_MG, unknown when none of them has movement data. None without movement data."""
    if movement is None or np.isnan(movement).all():
        return None
    padded = np.concatenate([np.full(STILL_AROUND_S, np.nan), movement, np.full(STILL_AROUND_S, np.nan)])
    windows = np.lib.stride_tricks.sliding_window_view(padded, 2 * STILL_AROUND_S + 1)
    with warnings.catch_warnings():
        # a window without movement data has no median
        warnings.simplefilter('ignore', RuntimeWarning)
        median = np.nanmedian(windows, axis=1)
    return np.where(np.isnan(median), np.nan, (median < STILL_MG).astype(float))


def zone_limits(birth_year: int, year: int) -> tuple[int, list[int]]:
    """The maximum HR (220 - age, the age in `year`) and the lower limits of the 5 zones in whole bpm."""
    top = 220 - (year - birth_year)
    return top, [round(top * p / 100) for p in ZONE_PERCENTS]


def zone_seconds(hr: np.ndarray, limits: list[int]) -> list[int]:
    """The seconds below zone 1 and in each zone (from its limit up to the next one; zone 5 without an upper limit),
    without the seconds that have no HR."""
    found = hr[~np.isnan(hr)]
    return np.bincount(np.searchsorted(limits, found, side='right'), minlength=len(limits) + 1).tolist()


def stress_index(rr: np.ndarray) -> float | None:
    """The square root of Baevsky's stress index of some beat intervals (ms), as Kubios shows it: AMo / (2 * Mo * MxDMn)
    from a histogram in 50 ms bins (Mo: the centre of the fullest bin in s; AMo: the % of the beats in it; MxDMn: the
    longest interval minus the shortest in s); None when all intervals are equal."""
    spread = (float(rr.max()) - float(rr.min())) / 1000
    if spread <= 0:
        return None
    bins = np.floor(rr / STRESS_BIN_MS).astype(np.int64)
    counts = np.bincount(bins - bins.min())
    top = int(np.argmax(counts))
    mode = ((top + int(bins.min())) * STRESS_BIN_MS + STRESS_BIN_MS / 2) / 1000
    return round(float(np.sqrt(100 * counts[top] / len(rr) / (2 * mode * spread))), 1)


def _spectrum(at_ms: np.ndarray, rr: np.ndarray, start_ms: int, use_s: int) -> tuple[float | None, float | None]:
    """The LF and HF power (ms²) of the beats of the window of `use_s` from `start_ms`: their RR (ms) at their times,
    resampled at 4 Hz over the whole window by linear interpolation (the first and last value held at the edges), the linear
    trend removed, in a one-sided Welch spectrum (ms²/Hz) of 120 s Hann segments with 50 % overlap; None without two
    beats."""
    if len(at_ms) < 2:
        return None, None
    t = (at_ms - start_ms) / 1000
    grid = np.arange(0, use_s, 1 / HRV_RESAMPLE_HZ)
    series = np.interp(grid, t, rr)
    series = series - np.polyval(np.polyfit(grid, series, 1), grid)
    size = HRV_SEGMENT_S * HRV_RESAMPLE_HZ
    window = np.hanning(size)
    spectra = [np.abs(np.fft.rfft(series[n:n + size] * window)) ** 2 for n in range(0, len(series) - size + 1, size // 2)]
    density = 2 * np.mean(spectra, axis=0) / (HRV_RESAMPLE_HZ * np.sum(window ** 2))
    freqs = np.fft.rfftfreq(size, 1 / HRV_RESAMPLE_HZ)

    def band(low: float, high: float) -> float:
        """The power from `low` to `high` Hz: the sum of its steps of the spectrum, each a full step wide."""
        inside = (freqs >= low) & (freqs < high)
        return float(density[inside].sum() * (freqs[1] - freqs[0]))

    return band(*LF_BAND), band(*HF_BAND)


def beat_times(stored_ms: np.ndarray, rr: np.ndarray) -> np.ndarray:
    """The time of each beat (ms) from the sum of the intervals, which the strap measures exactly: the PC logger gives all
    beats of one Bluetooth message the time of that message. Where a stored time is more than BEAT_GAP_MS from that sum,
    beats are missing, and the sum starts again from the stored time."""
    times = np.empty(len(rr))
    for n in range(len(rr)):
        guess = times[n - 1] + rr[n] if n else float(stored_ms[0])
        times[n] = float(stored_ms[n]) if abs(stored_ms[n] - guess) > BEAT_GAP_MS else guess
    return times


def hrv_values(at_ms: np.ndarray, rr: np.ndarray, clean: np.ndarray, use_s: int = HRV_USE_S) -> dict:
    """The values of an HRV test from its beats (their times and RR in ms, whether each is clean), with the window used
    (from_ms, to_ms): the test time `use_s` after the first minute. `reason` says why there are no values: no beats, a
    recording shorter than the first minute and the test time, or less than 80 % of clean beats in the window (a gap without
    beats counts as beats that are not clean). Successive differences only between two clean beats."""
    found = dict.fromkeys(HRV_KEYS)
    if len(rr) == 0:
        return found | {'reason': 'The session has no beat intervals (no RR file), so it has no HRV values.'}
    start = int(at_ms[0]) + HRV_SKIP_S * 1000
    end = start + use_s * 1000
    found |= {'from_ms': start, 'to_ms': end}
    length_s = int((at_ms[-1] - at_ms[0]) // 1000)
    if length_s < HRV_SKIP_S + use_s:
        return found | {'reason': f'The test needs {(HRV_SKIP_S + use_s) // 60} minutes; this recording has {length_s // 60} min '
                                  f'{length_s % 60} s.'}
    inside = (at_ms >= start) & (at_ms < end)
    window, good, times = rr[inside], clean[inside], at_ms[inside]
    # a gap without beats counts as beats that are not clean: the window holds 5 minutes of beats at their median interval
    expected = use_s * 1000 / float(np.median(window[good])) if good.any() else 0.0
    share = float(good.sum()) / max(len(good), expected) if good.any() else 0.0
    found['wrong_pct'] = round(100 * (1 - share), 1)
    if share < HRV_MIN_CLEAN:
        return found | {'reason': f'Only {int(100 * share)} % of the beats are clean; the test needs 80 %.'}
    beats = window[good]
    diffs = np.diff(window)[good[1:] & good[:-1]]
    rmssd = float(np.sqrt(np.mean(diffs ** 2)))
    lf, hf = _spectrum(beat_times(times, window)[good], beats, start, use_s)
    ratio = round(lf / hf, 2) if lf is not None and hf is not None and hf >= HF_MIN_MS2 else None
    return found | {
        'rest_hr': round(60000 / float(beats.mean()), 1), 'rmssd': round(rmssd, 1),
        'ln_rmssd': round(float(np.log(rmssd)), 2) if rmssd > 0 else None, 'sdnn': round(float(np.std(beats, ddof=1)), 1),
        'pnn50': round(100 * float(np.mean(np.abs(diffs) > 50)), 1), 'lf': None if lf is None else round(lf),
        'hf': None if hf is None else round(hf), 'lf_hf': ratio,
        'stress': stress_index(beats)}


def pack(samples: np.ndarray) -> bytes:
    """Samples (rows of 1 or 3 whole numbers) without loss: per channel the first value and then the differences, as
    32-bit integers, compressed."""
    values = np.asarray(samples, dtype=np.int64).reshape(len(samples), -1)
    steps = np.diff(values, axis=0, prepend=np.zeros((1, values.shape[1]), dtype=np.int64))
    if len(steps) and np.abs(steps).max() >= 2 ** 31:
        raise ValueError('a step between two samples is too large to pack')
    return zlib.compress(steps.astype('<i4').tobytes(), 6)


def unpack(data: bytes, channels: int) -> np.ndarray:
    """The samples of a packed block, rows of `channels` whole numbers."""
    steps = np.frombuffer(zlib.decompress(data), dtype='<i4').reshape(-1, channels).astype(np.int64)
    return np.cumsum(steps, axis=0)


def cut_points(sensor_ns: np.ndarray, period_ns: float, max_count: int) -> list[int]:
    """Where blocks start: at the first sample, after each gap of more than 3 sample periods, and every `max_count`
    samples."""
    gaps = (np.flatnonzero(np.diff(sensor_ns) > GAP_PERIODS * period_ns) + 1).tolist()
    points: list[int] = []
    for start, end in zip([0, *gaps], [*gaps, len(sensor_ns)], strict=True):
        points.extend(range(start, end, max_count))
    return points


class Blocks:
    """The raw samples of one stream as packed blocks of at most 60 s, a new block after each gap; the samples come in parts,
    and the last block of a part waits for the next part."""

    def __init__(self, stream: str) -> None:
        """No samples yet."""
        self.stream = stream
        self.rows: list[tuple[str, int, int, float, int, int, bytes]] = []
        self._waiting: list[tuple[np.ndarray, np.ndarray, np.ndarray]] = []
        self._period: float | None = None

    def add(self, at_ms: np.ndarray, sensor_ns: np.ndarray, values: np.ndarray) -> None:
        """Add a part: UTC times in ms, sensor times in ns and the values (one row per sample)."""
        self._waiting.append((at_ms, sensor_ns, np.asarray(values).reshape(len(values), -1)))
        self._cut(final=False)

    def close(self) -> list[tuple[str, int, int, float, int, int, bytes]]:
        """Pack the last samples; the blocks as (stream, host_ms, sensor_ns, rate, count, channels, data)."""
        if self._waiting:
            self._cut(final=True)
        return self.rows

    def _cut(self, final: bool) -> None:
        """Pack every block that is complete: followed by a gap or by more samples, or the last one at the close."""
        at, sensor, values = (np.concatenate([part[i] for part in self._waiting]) for i in range(3))
        self._waiting = []
        if self._period is None:
            steps = np.diff(sensor[:1001])
            steps = steps[steps > 0]
            if len(steps) == 0 and not final:
                self._waiting = [(at, sensor, values)]
                return
            self._period = float(np.median(steps)) if len(steps) else 1e9
        starts = cut_points(sensor, self._period, max(1, round(BLOCK_S * 1e9 / self._period)))
        keep = len(sensor) if final else starts[-1]
        for start, end in zip(starts, [*starts[1:], len(sensor)], strict=True):
            if start >= keep:
                break
            self.rows.append((self.stream, int(at[start]), int(sensor[start]), round(1e9 / self._period, 3), end - start,
                              values.shape[1], pack(values[start:end])))
        if keep < len(sensor):
            self._waiting = [(at[keep:], sensor[keep:], values[keep:])]
