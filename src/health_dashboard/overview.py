# the texts of the tiles use the true minus sign and the en dash
# ruff: noqa: RUF001
"""The overview row of the Health page: one tile for each key value of a day (the weight, the blood pressure, the resting
HR, ln RMSSD, the sleep and the activity), each with its value, its change against the mean of the 30 days up to the day,
the values of those 30 days for a sparkline, and a status. The status rule: the current value (a 7-day mean) against the
usual range, the mean +/- 1 SD of the day values of the 60 days before those 7 days (from 14 day values); ln RMSSD keeps
its normal range after Plews; the weight shows its rate with its 95 % interval; the activity, the WHO minutes of the last 7
days against the minimum of 150 (a vigorous minute counts double). A tile without data says what to do and has no value:
never a zero in place of a missing value. One history read of 67 days serves all tiles."""

from datetime import date

import numpy as np

from health_dashboard import history, person, polar_load, trends

RECENT, BASE, SPAN, MIN_BASE = 7, 60, 30, 14
# the words of the training load ratio: its upper limit and its words
RATIO_WORDS = ((0.8, 'less than usual'), (1.3, 'as usual'), (1.5, 'more than usual'), (float('inf'), 'much more than usual'))


def _signed(value: float, decimals: int) -> str:
    """A number with a sign, the minus as a true minus sign."""
    return f'{value:+.{decimals}f}'.replace('-', '−')


def _plain(value: float, decimals: int) -> str:
    """A number, the minus as a true minus sign."""
    return f'{value:.{decimals}f}'.replace('-', '−')


def _values(rows: list[dict], key: str) -> list[float]:
    """The values of a key in some day rows, without the days that have none."""
    return [r[key] for r in rows if isinstance(r.get(key), int | float)]


def _usual(rows: list[dict], key: str) -> tuple[float, float] | None:
    """The usual range of a key: the mean +/- 1 SD of its day values in the 60 days before the last 7; None with fewer than
    14 day values."""
    found = _values(rows[-(RECENT + BASE):-RECENT], key)
    if len(found) < MIN_BASE:
        return None
    centre, spread = float(np.mean(found)), float(np.std(found, ddof=1))
    return centre - spread, centre + spread


def _status(value: float, usual: tuple[float, float] | None) -> str | None:
    """'below', 'within' or 'above' the usual range; None without one."""
    if usual is None:
        return None
    return 'below' if value < usual[0] else 'above' if value > usual[1] else 'within'


def _tile(key: str, label: str, unit: str, empty: str, rows: list[dict], spark: str | list[float | None]) -> dict:
    """An empty tile: its key, label and unit, what to do without data, and its 30 days of values for the sparkline (a key
    of the rows, or the values themselves)."""
    values = [r.get(spark) for r in rows[-SPAN:]] if isinstance(spark, str) else spark
    return {'key': key, 'label': label, 'unit': unit, 'value': None, 'text': '', 'change': None, 'status': None,
            'status_text': '', 'detail': '', 'empty': empty, 'spark': values}


def _change(value: float, rows: list[dict], key: str, unit: str, decimals: int, scale: float = 1) -> dict | None:
    """The change of a value against the mean of the day values of a key in the last 30 days (x `scale`)."""
    found = _values(rows[-SPAN:], key)
    return None if not found else {'value': round((value - float(np.mean(found))) * scale, decimals), 'unit': unit}


def _ranged(tile: dict, value: float, usual: tuple[float, float] | None, name: str, decimals: int, unit: str) -> dict:
    """The status of a tile against a usual range, in words."""
    state = _status(value, usual)
    text = (f'your {name} needs {MIN_BASE} days of values' if usual is None
            else f'{state} your {name} {_plain(usual[0], decimals)}–{_plain(usual[1], decimals)} {unit}'.strip())
    return tile | {'status': state, 'status_text': text}


def _weight(rows: list[dict], day: date) -> dict:
    """The tile of the weight: the last weigh-in of the 30 days, its change, and the rate of 28 days as its status."""
    tile = _tile('weight', 'Weight', 'kg', 'Weigh yourself on the Withings scale.', rows, 'weight')
    found = _values(rows[-SPAN:], 'weight')
    if not found:
        return tile
    value = round(found[-1], 2)
    tile |= {'value': value, 'text': _plain(value, 1), 'change': _change(value, rows, 'weight', 'kg', 2)}
    rate = trends.rate(day)
    if rate is None:
        return tile | {'status_text': f'the rate needs {trends.RATE_MIN_DAYS} weigh-in days in {trends.RATE_DAYS} days'}
    return tile | {'status': rate['status'], 'status_text': f"{_signed(rate['kg_week'], 2)} kg a week (95 % interval "
                                                            f"{_plain(rate['low'], 2)} to {_plain(rate['high'], 2)})"}


def _part(name: str, found: dict | None) -> str:
    """A part of the day of the home blood pressure in words."""
    if found is None:
        return f'no {name.lower()} reading'
    count = found['readings']
    return f"{name} {round(found['systolic'])}/{round(found['diastolic'])} ({count} {'reading' if count == 1 else 'readings'})"


def _pressure(rows: list[dict], day: date) -> dict:
    """The tile of the blood pressure: the home mean of the ESH method, the morning and the evening, and the systolic
    mean against its usual range."""
    tile = _tile('pressure', 'Blood pressure', 'mmHg', f'Measure your blood pressure: at least {trends.ESH_MIN_READINGS} '
                 'readings in 7 days, in the morning and in the evening.', rows, 'systolic')
    home = trends.home_pressure(day)
    if home is None:
        return tile
    detail = f"{_part('Morning', home['morning'])}, {_part('evening', home['evening'])}; ESH method, {home['readings']} readings"
    tile |= {'value': home['systolic'], 'text': f"{round(home['systolic'])}/{round(home['diastolic'])}",
             'change': _change(home['systolic'], rows, 'systolic', 'mmHg', 1), 'detail': detail[0].upper() + detail[1:]}
    return _ranged(tile, home['systolic'], _usual(rows, 'systolic'), 'usual systolic range', 0, 'mmHg')


def _rest_hr(rows: list[dict]) -> dict:
    """The tile of the resting HR of the HRV tests: its 7-day mean against its usual range."""
    tile = _tile('rest_hr', 'Resting HR', 'bpm', 'Make an HRV test: press HRV test on the Polar H10 card.', rows,
                 'h10:rest_hr')
    value = rows[-1].get('h10:rest_hr_7d')
    if value is None:
        return tile
    tile |= {'value': value, 'text': _plain(value, 0), 'change': _change(value, rows, 'h10:rest_hr', 'bpm', 1),
             'detail': 'The mean of the HRV tests of the last 7 days'}
    return _ranged(tile, value, _usual(rows, 'h10:rest_hr'), 'usual range', 0, 'bpm')


def _ln_rmssd(rows: list[dict]) -> dict:
    """The tile of ln RMSSD: its 7-day mean against the normal range after Plews, and its coefficient of variation."""
    tile = _tile('ln_rmssd', 'ln RMSSD', '', 'Make an HRV test: press HRV test on the Polar H10 card.', rows, 'h10:ln_rmssd')
    last = rows[-1]
    value = last.get('h10:ln_rmssd_7d')
    if value is None:
        return tile
    cv = last.get('h10:ln_rmssd_cv7')
    tile |= {'value': value, 'text': _plain(value, 2), 'change': _change(value, rows, 'h10:ln_rmssd', '', 2),
             'detail': 'The mean of the HRV tests of the last 7 days' + (
                 f'; coefficient of variation {_plain(cv, 1)} %' if cv is not None else '')}
    low, high = last.get('h10:ln_rmssd_low'), last.get('h10:ln_rmssd_high')
    if low is None or high is None:
        return tile | {'status_text': 'your normal range needs 14 tests in 60 days'}
    return _ranged(tile, value, (low, high), 'normal range', 2, '')


def _sleep(rows: list[dict]) -> dict:
    """The tile of the sleep: the mean total sleep of the days of the last 7 that have one, against its usual range."""
    tile = _tile('sleep', 'Sleep', '', 'Press Import sleep on the Sleep card.', rows, 'sleep:total')
    found = _values(rows[-RECENT:], 'sleep:total')
    if not found:
        return tile
    value = round(float(np.mean(found)), 2)
    minutes = round(value * 60)
    tile |= {'value': value, 'text': f'{minutes // 60} h {minutes % 60:02d} min',
             'change': _change(value, rows, 'sleep:total', 'min', 0, scale=60),
             'detail': f"The mean of the last 7 days, {len(found)} {'day' if len(found) == 1 else 'days'} with a sleep"}
    usual = _usual(rows, 'sleep:total')
    tile = _ranged(tile, value, usual, 'usual range', 1, 'h')
    return tile


def _activity(rows: list[dict]) -> dict:
    """The tile of the activity: the WHO minutes of the last 7 days against the minimum of 150, and the training load
    ratio in words."""
    who = [None if r.get('activity:moderate') is None else r['activity:moderate'] + 2 * r['activity:vigorous'] for r in rows]
    if person.birth_year() is None:
        return _tile('activity', 'Activity, last 7 days', 'min', f'The activity needs birth_date: set it {person.SET_IT}.', rows,
                     who[-SPAN:])
    tile = _tile('activity', 'Activity, last 7 days', 'min', 'No session in the last 30 days: record one with the Polar H10.',
                 rows, who[-SPAN:])
    if all(v is None for v in who[-SPAN:]):
        return tile
    value = round(sum(v or 0 for v in who[-RECENT:]))
    weekly = sum(v or 0 for v in who[-SPAN:]) / SPAN * RECENT
    ratio = rows[-1].get('load:ratio')
    words = next(text for limit, text in RATIO_WORDS if ratio is None or ratio < limit)
    detail = ('Training load ratio: not yet, it needs 28 days of sessions' if ratio is None
              else f'Training load ratio {_plain(ratio, 2)}: {words}')
    missing = polar_load.WHO_WEEK - value
    return tile | {'value': value, 'text': str(value), 'change': {'value': round(value - weekly), 'unit': 'min'},
                   'status': 'met' if missing <= 0 else 'open', 'detail': detail,
                   'status_text': f'WHO minimum of {polar_load.WHO_WEEK} met' if missing <= 0
                   else f'{missing} min to the WHO minimum of {polar_load.WHO_WEEK}'}


def overview(day: date) -> list[dict]:
    """The tiles of the overview of `day`, in their order (see the module)."""
    rows = history.days(RECENT + BASE, day)
    return [_weight(rows, day), _pressure(rows, day), _rest_hr(rows), _ln_rmssd(rows), _sleep(rows), _activity(rows)]
