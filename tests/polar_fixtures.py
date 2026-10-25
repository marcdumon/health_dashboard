"""Made-up Polar H10 files for the tests, laid out as the phone app (Polar Sensor Logger) and the user's logger write them.
Every number is invented; none is the user's data."""

import csv
import json
import math
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from itertools import accumulate
from pathlib import Path

import numpy as np
import pytest

from health_dashboard import person, polar_store, polar_values
from health_dashboard.config import PersonSettings

SERIAL = 'E813952E'
SENSOR_NS = 599_616_000_000_000_000
ECG_RATE = 130
HEADERS = {'HR': 'Phone timestamp;HR [bpm];HRV [ms];Breathing interval [rpm];', 'RR': 'Phone timestamp;RR-interval [ms]',
           'ECG': 'Phone timestamp;sensor timestamp [ns];timestamp [ms];ecg [uV]',
           'ACC': 'Phone timestamp;sensor timestamp [ns];X [mg];Y [mg];Z [mg]'}


def step_hr(s: float) -> float:
    """80 bpm, 10 s at 150 (the hardest effort, its last second at 599 s), then 120 bpm."""
    return 80.0 if s < 590 else 150.0 if s < 600 else 120.0


def still(s: float) -> float:
    """No movement: the acceleration stays at 1 g."""
    return 0.0


def walking(s: float) -> float:
    """Steps: 300 mg up and down, 1.9 times a second."""
    return 300.0 * math.sin(2 * math.pi * 1.9 * s)


def effort(s: float) -> float:
    """An exercise session: steps all the time, but still in the minute after the hardest effort (600 to 680 s)."""
    return 0.0 if 600 <= s < 680 else walking(s)


def ecg_value(n: int) -> int:
    """The ECG sample number n, in microvolts."""
    return round(600 * math.sin(n / 9)) - 40


def _phone_time(at: datetime) -> str:
    """A clock time as the phone app writes it: local, no zone, to the ms."""
    return f'{at:%Y-%m-%dT%H:%M:%S}.{at.microsecond // 1000:03d}'


def _write(path: Path, header: str, rows: list[str]) -> Path:
    """A text file: the header, then one line per row."""
    path.write_text('\n'.join([header, *rows]) + '\n')
    return path


def phone_session(folder: Path, start: datetime, seconds: int, hr: Callable[[float], float] = step_hr, *,
                  beats: list[int] | None = None, rr: bool = True, acc: Callable[[float], float] | None = effort,
                  acc_rate: int = 25, ecg: bool = False, ecg_gap: tuple[int, int] | None = None) -> dict[str, Path]:
    """The files of one phone session from a local clock time: HR each second (from the fourth second with an HRV value
    with a decimal comma, as the phone app writes it), the RR of each beat (from the HR, or `beats`), the ECG (130/s,
    without the samples in `ecg_gap`, in seconds: a dropout) and the acceleration. Each file is named a second after the
    one before, as the phone app names them."""
    def name(kind: str, offset: int) -> Path:
        """The file name of a stream."""
        return folder / f'Polar_H10_{SERIAL}_{start + timedelta(seconds=offset):%Y%m%d_%H%M%S}_{kind}.txt'

    rows = [f'{_phone_time(start + timedelta(seconds=s))};{round(hr(s))}' + (f';{7 + s % 3},{s % 10}' if s >= 3 else '')
            for s in range(seconds)]
    files = {'HR': _write(name('HR', 0), HEADERS['HR'], rows)}
    if rr:
        intervals = list(beats or [])
        if beats is None:
            t = 0
            while t < seconds * 1000:
                intervals.append(round(60000 / hr(t / 1000)))
                t += intervals[-1]
        files['RR'] = _write(name('RR', 1), HEADERS['RR'], [f'{_phone_time(start + timedelta(milliseconds=t))};{v}'
                                                            for t, v in zip(accumulate(intervals), intervals, strict=True)])
    if ecg:
        rows = [f'{_phone_time(start + timedelta(seconds=n / ECG_RATE))};{SENSOR_NS + round(n * 1e9 / ECG_RATE)};'
                f'{n * 1000 / ECG_RATE:.1f};{ecg_value(n)}'
                for n in range(seconds * ECG_RATE) if not (ecg_gap and ecg_gap[0] <= n / ECG_RATE < ecg_gap[1])]
        files['ECG'] = _write(name('ECG', 2), HEADERS['ECG'], rows)
    if acc is not None:
        rows = [f'{_phone_time(start + timedelta(seconds=n / acc_rate))};{SENSOR_NS + round(n * 1e9 / acc_rate)};'
                f'{round(1000 + acc(n / acc_rate))};0;0' for n in range(seconds * acc_rate)]
        files['ACC'] = _write(name('ACC', 3), HEADERS['ACC'], rows)
    return files


def phone_markers(folder: Path, presses: list[tuple[datetime, bool]]) -> Path:
    """The marker file of the phone app: each press (local clock time; True: the marker goes on, False: off), the file
    named after the first press, each label with a space after it as the phone app writes it."""
    rows = [f"{_phone_time(at)};{'MARKER_START' if on else 'MARKER_STOP'} " for at, on in presses]
    return _write(folder / f'MARKER_{presses[0][0]:%Y%m%d_%H%M%S}.txt', 'Phone timestamp;Marker start/stop', rows)


def logger_session(folder: Path, start: datetime, seconds: int, hr: Callable[[float], float] = step_hr, *,
                   ended: bool = True, txt: bool = False, acc: Callable[[float], float] | None = effort,
                   markers: list[tuple[float, str]] = ()) -> Path:
    """A session folder of the user's logger from a time with a zone (whole seconds): session.json (with its end once it
    ended), hr.csv (hr.csv.txt with txt), rr.csv with RR in steps of 1/1024 s, acc.csv at 25/s, and markers.csv with the
    markers given (seconds after the start, label), as the logger's csv writer writes them."""
    path = folder / f'{start:%Y-%m-%d_%H-%M-%S}'
    path.mkdir(parents=True)

    def ns(s: float) -> int:
        """The logger's time (UTC ns) s seconds after the start."""
        return int(start.timestamp()) * 10 ** 9 + round(s * 1e9)

    info = {'id': path.name, 'start': start.isoformat(), 'device': {'serial': SERIAL}}
    if ended:
        info['end'] = (start + timedelta(seconds=seconds)).isoformat()
    (path / 'session.json').write_text(json.dumps(info))
    _write(path / ('hr.csv.txt' if txt else 'hr.csv'), 'host_ns,hr_bpm,contact',
           [f'{ns(s)},{round(hr(s))},1' for s in range(seconds)])
    beats, t = [], 0.0
    while t < seconds:
        raw = round(60 / hr(t) * 1024)
        t += raw / 1024
        beats.append(f'{ns(t)},{raw},{raw * 1000 / 1024}')
    _write(path / 'rr.csv', 'host_ns,rr_raw,rr_ms', beats)
    if acc is not None:
        _write(path / 'acc.csv', 'host_ns,sensor_ns,x_mg,y_mg,z_mg',
               [f'{ns(n / 25)},{SENSOR_NS + n * 40_000_000},{round(1000 + acc(n / 25))},0,0' for n in range(seconds * 25)])
    if markers:
        with (path / 'markers.csv').open('w', newline='') as out:
            writer = csv.writer(out, lineterminator='\n')
            writer.writerow(['host_ns', 'label', 'source'])
            writer.writerows([ns(s), label, 'key'] for s, label in markers)
    return path


def stored_session(start_at: str, hr: list[float], movement: list[float] | None, rr: list[float] | None = None,
                   **fields: object) -> int:
    """A session saved straight into the store, without files: its HR and movement of each second from start_at (UTC
    ISO), its beats from `rr` (ms, from start_at), its movement values computed as the import does them; the fields given
    replace the others."""
    start_s = int(datetime.fromisoformat(start_at).timestamp())
    beats = np.array(hr, dtype=float)
    row = {'source': 'phone', 'serial': SERIAL, 'path': f'/phone/{start_at}_HR.txt', 'start_at': start_at,
           'end_at': datetime.fromtimestamp(start_s + len(hr) - 1, UTC).isoformat(timespec='seconds'), 'day': start_at[:10],
           'kind': 'exercise', 'seconds': len(hr), 'mean_hr': round(float(beats.mean()), 1), 'max_hr': float(beats.max()),
           'rest_hr': None, 'rmssd': None, 'wrong_beats_pct': None,
           'imported_at': start_at} | fields
    seconds = [(start_s + n, hr[n], None if movement is None else movement[n]) for n in range(len(hr))]
    beats = []
    if rr:
        intervals = np.array(rr, dtype=float)
        times = start_s * 1000 + np.cumsum(intervals)
        beats = [(int(t), float(v), int(c)) for t, v, c in zip(times, intervals, polar_values.clean_beats(intervals), strict=True)]
    return polar_store.save(row, seconds, beats, [], [(str(row['path']), 1, 1)])


def set_person(monkeypatch: pytest.MonkeyPatch, **values: object) -> None:
    """The [person] part of person.toml for a test: these values, the others not set."""
    monkeypatch.setattr(person, 'settings', lambda: PersonSettings(**values))
