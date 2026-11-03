"""The temperature of the room where the blood pressure is measured, from Home Assistant (url in the [home_assistant] part of
health_dashboard.toml, its token HA_TOKEN in .env). The card chooses the sensor from the temperature sensors of Home Assistant;
the choice is kept in the database, and another sensor counts from the next download on: the stored temperatures stay.

Download room temperature gets the states of the sensor since the last download and keeps a value for every STEP_MIN minutes in
the database, in local time as the Withings readings. Everything else reads the database: only these two buttons ask Home
Assistant.

A blood pressure reading has the temperature of the last step at or before it, at most MAX_AGE_MIN old (a sensor that was off
gives no value), and a day the mean of its readings."""

import math
import os
from bisect import bisect_right
from datetime import date, datetime, timedelta
from urllib.parse import quote

import httpx
from dotenv import dotenv_values

from health_dashboard import ledger, store, trends, withings
from health_dashboard.config import APP_DIR, config

KEY = 'room_temperature'
# the history keys of the values of a day, with their decimals
KEYS = {KEY: 1}
STEP_MIN = 5
MAX_AGE_MIN = 15
# the first download asks this far back: Home Assistant gives what it has
FIRST_DAYS = 365
TIME = '%Y-%m-%dT%H:%M'
TOKEN_KEY = 'HA_TOKEN'
DOWNLOADED_KEY, SENSOR_KEY, SENSOR_NAME_KEY = 'room_downloaded_at', 'room_sensor', 'room_sensor_name'
NOT_SET = ('Set url in the [home_assistant] part of health_dashboard.toml, and restart the app. Put a long-lived access token '
           f'of your Home Assistant profile in the .env file in the app folder, as {TOKEN_KEY}=…')
NO_SENSOR = 'Choose the temperature sensor of the room first: press Choose sensor.'


class RoomError(RuntimeError):
    """Home Assistant could not give what was asked; the message says what to do."""


def _token() -> str:
    """The Home Assistant token of .env, read at every call (a new value works at once), else of the environment."""
    return dotenv_values(APP_DIR / '.env').get(TOKEN_KEY) or os.environ.get(TOKEN_KEY, '')


def _configured() -> bool:
    """Whether the address and the token are set."""
    return bool(config().home_assistant.url and _token())


def _get(path: str, params: dict | None = None) -> list:
    """An answer of the Home Assistant API; RoomError when the settings are not there or it gives none."""
    if not _configured():
        raise RoomError(NOT_SET)
    url = config().home_assistant.url
    try:
        with httpx.Client(timeout=120.0) as http:
            reply = http.get(f'{url.rstrip("/")}{path}', params=params, headers={'Authorization': f'Bearer {_token()}'})
    except httpx.HTTPError as error:
        raise RoomError(f'Home Assistant does not answer at {url}. Check that it runs, and check url in the [home_assistant] '
                        'part of health_dashboard.toml.') from error
    if reply.status_code in (401, 403):
        raise RoomError(f'Home Assistant refused the token. Make a new long-lived access token in your Home Assistant profile, '
                        f'and put it in the .env file in the app folder, as {TOKEN_KEY}=…')
    if reply.status_code != 200:
        raise RoomError(f'Home Assistant answered with error {reply.status_code}. Press the button again.')
    return reply.json()


def _number(state: str) -> float | None:
    """A state as a number; None when it is not one (unavailable, unknown)."""
    try:
        value = float(state)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def sensors() -> list[dict]:
    """The temperature sensors of Home Assistant by name: entity id, name and the temperature now (None when off)."""
    found = [{'entity': state['entity_id'], 'name': ' '.join(state['attributes'].get('friendly_name', state['entity_id']).split()),
              'celsius': _number(state['state'])}
             for state in _get('/api/states') if state['attributes'].get('device_class') == 'temperature']
    return sorted(found, key=lambda sensor: sensor['name'].lower())


def set_sensor(entity: str, name: str) -> None:
    """Choose the sensor of the room. It counts from the next download on: the stored temperatures stay."""
    store.set_setting(SENSOR_KEY, entity)
    store.set_setting(SENSOR_NAME_KEY, name)


def _steps(states: list[dict], start: datetime, now: datetime) -> dict[str, float]:
    """The mean temperature of every STEP_MIN minutes from `start` to `now`, by the local start of the step. Home Assistant
    gives a state only when it changes, so a value holds until the next state, into the steps after it; a state that is not a
    number (a sensor that is off) holds no value."""
    width = STEP_MIN * 60
    changes = [(datetime.fromisoformat(state['last_changed']).timestamp(), _number(state['state'])) for state in states]
    held, index, found = None, 0, {}
    for step in range(int(max(start.timestamp(), changes[0][0]) // width * width), int(now.timestamp()) + 1, width):
        values = [] if held is None else [held]
        while index < len(changes) and changes[index][0] < step + width:
            held = changes[index][1]
            index += 1
            if held is not None:
                values.append(held)
        if values:
            found[datetime.fromtimestamp(step, start.tzinfo).strftime(TIME)] = round(sum(values) / len(values), 2)
    return found


def download(now: datetime) -> int:
    """Get the states of the sensor from the newest step on (it is computed again) into the database; the number of steps
    kept. RoomError when the settings or the sensor are not there, or Home Assistant gives nothing."""
    entity = store.setting(SENSOR_KEY)
    if not entity:
        raise RoomError(NOT_SET if not _configured() else NO_SENSOR)
    with ledger.connect() as db:
        newest = db.execute('SELECT MAX(at) FROM room_temperature').fetchone()[0]
    start = datetime.fromisoformat(newest).replace(tzinfo=now.tzinfo) if newest else now - timedelta(days=FIRST_DAYS)
    found = _get(f'/api/history/period/{quote(start.isoformat())}', {
        'filter_entity_id': entity, 'end_time': now.isoformat(), 'minimal_response': '', 'no_attributes': ''})
    if not found or not found[0]:
        raise RoomError(f'Home Assistant has no history of {entity}. Choose another sensor.')
    steps = _steps(found[0], start, now)
    with ledger.connect(write=True) as db:
        db.executemany('INSERT INTO room_temperature (at, celsius) VALUES (?, ?) ON CONFLICT (at) DO UPDATE SET '
                       'celsius = excluded.celsius', list(steps.items()))
    store.set_setting(DOWNLOADED_KEY, now.isoformat(timespec='minutes'))
    return len(steps)


def at_readings(first: str, last: str) -> dict[str, float]:
    """The temperature at each blood pressure reading from day `first` to day `last`, by the time of the reading, without
    the readings the user removed; a reading without a temperature is left out."""
    start = f'{first}T{store.DAY_START_HOUR:02d}:00'
    end = f'{date.fromisoformat(last) + timedelta(days=1)}T{store.DAY_START_HOUR:02d}:00'
    since = (datetime.fromisoformat(start) - timedelta(minutes=MAX_AGE_MIN)).strftime(TIME)
    with ledger.connect() as db:
        readings = db.execute('SELECT measured_at FROM withings_values WHERE type = ? AND position = ? AND measured_at >= ? AND '
                              f'measured_at < ? AND {withings.NOT_HIDDEN}', (trends.SYSTOLIC, withings.NO_PART, start, end)).fetchall()
        steps = db.execute('SELECT at, celsius FROM room_temperature WHERE at >= ? AND at < ? ORDER BY at', (since, end)).fetchall()
    times = [step[0] for step in steps]
    found = {}
    for (at,) in readings:
        index = bisect_right(times, at) - 1
        if index >= 0 and datetime.fromisoformat(at) - datetime.fromisoformat(times[index]) <= timedelta(minutes=MAX_AGE_MIN):
            found[at] = steps[index][1]
    return found


def day_values(first: str, last: str) -> dict[str, dict[str, float]]:
    """The mean temperature at the blood pressure readings of each day from `first` to `last` that has one, for the history."""
    by_day: dict[str, list[float]] = {}
    for at, celsius in at_readings(first, last).items():
        by_day.setdefault(store.health_day(datetime.fromisoformat(at)).isoformat(), []).append(celsius)
    return {day: {KEY: round(sum(found) / len(found), KEYS[KEY])} for day, found in by_day.items()}


def card(day: str) -> dict:
    """What the Room temperature card shows: whether the settings are there and what to set when not, the chosen sensor, the
    time of the last download, and the mean temperature at the blood pressure readings of the day with their number (None
    without one)."""
    found = list(at_readings(day, day).values())
    entity = store.setting(SENSOR_KEY)
    return {'configured': _configured(), 'message': '' if _configured() else NOT_SET,
            'sensor': {'entity': entity, 'name': store.setting(SENSOR_NAME_KEY) or entity} if entity else None,
            'downloaded_at': store.setting(DOWNLOADED_KEY),
            'day': {'celsius': round(sum(found) / len(found), KEYS[KEY]), 'readings': len(found)} if found else None}
