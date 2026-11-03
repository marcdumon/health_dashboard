"""The temperature of the room at each blood pressure reading, from Home Assistant (url in the [home_assistant] part of
health_dashboard.toml, its token HA_TOKEN in .env). The card chooses the sensor from the temperature sensors of Home Assistant;
the choice is kept in the database, and another sensor counts for the readings of the next download: the stored temperatures
stay.

Download room temperature asks Home Assistant once for the time of the readings that were not asked yet, and keeps only the
temperature at each of them in the database: the last state at or before the reading, which holds until the next state. A
reading without a value (the sensor was off or not there yet) is kept without one, so it is not asked again. Everything else
reads the database: only these two buttons ask Home Assistant. A day has the mean of its readings."""

import math
import os
from bisect import bisect_right
from datetime import date, datetime, timedelta, tzinfo
from urllib.parse import quote

import httpx
from dotenv import dotenv_values

from health_dashboard import ledger, store, trends, withings
from health_dashboard.config import APP_DIR, config

KEY = 'room_temperature'
# the history keys of the values of a day, with their decimals
KEYS = {KEY: 1}
# a download asks for the readings of this many days: Home Assistant keeps no more
REACH_DAYS = 365
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


def _held(states: list[dict], readings: list[str], zone: tzinfo) -> list[tuple[str, float | None]]:
    """The temperature at each reading (local time): the last state at or before it. None when that state is not a number (a
    sensor that is off), or there is no state before it."""
    moments = [datetime.fromisoformat(state['last_changed']).timestamp() for state in states]
    found = []
    for at in readings:
        index = bisect_right(moments, datetime.fromisoformat(at).replace(tzinfo=zone).timestamp()) - 1
        found.append((at, _number(states[index]['state']) if index >= 0 else None))
    return found


def download(now: datetime) -> int:
    """Get the temperature at the blood pressure readings that were not asked yet into the database, a removed one too (it can
    be put back); the number of readings with a value. RoomError when the settings or the sensor are not there, or Home
    Assistant gives nothing; then nothing is kept."""
    if not _configured():
        raise RoomError(NOT_SET)
    entity = store.setting(SENSOR_KEY)
    if not entity:
        raise RoomError(NO_SENSOR)
    since = (now - timedelta(days=REACH_DAYS)).strftime(TIME)
    with ledger.connect() as db:
        readings = [row[0] for row in db.execute(
            'SELECT DISTINCT measured_at FROM withings_values WHERE type = ? AND position = ? AND measured_at >= ? AND measured_at '
            'NOT IN (SELECT measured_at FROM reading_temperature) ORDER BY measured_at', (trends.SYSTOLIC, withings.NO_PART, since))]
    found = []
    if readings:
        start = datetime.fromisoformat(readings[0]).replace(tzinfo=now.tzinfo)
        states = _get(f'/api/history/period/{quote(start.isoformat())}', {
            'filter_entity_id': entity, 'end_time': now.isoformat(), 'minimal_response': '', 'no_attributes': ''})
        if not states or not states[0]:
            raise RoomError(f'Home Assistant has no history of {entity}. Choose another sensor.')
        found = _held(states[0], readings, now.tzinfo)
        with ledger.connect(write=True) as db:
            db.executemany('INSERT INTO reading_temperature (measured_at, celsius) VALUES (?, ?)', found)
    store.set_setting(DOWNLOADED_KEY, now.isoformat(timespec='minutes'))
    return sum(celsius is not None for _, celsius in found)


def at_readings(first: str, last: str) -> dict[str, float]:
    """The temperature at each blood pressure reading from day `first` to day `last`, by the time of the reading, without
    the readings the user removed; a reading without a temperature is left out."""
    start = f'{first}T{store.DAY_START_HOUR:02d}:00'
    end = f'{date.fromisoformat(last) + timedelta(days=1)}T{store.DAY_START_HOUR:02d}:00'
    with ledger.connect() as db:
        rows = db.execute('SELECT measured_at, celsius FROM reading_temperature WHERE celsius IS NOT NULL AND measured_at >= ? AND '
                          'measured_at < ? AND measured_at IN (SELECT measured_at FROM withings_values WHERE type = ? AND '
                          f'position = ? AND {withings.NOT_HIDDEN})', (start, end, trends.SYSTOLIC, withings.NO_PART)).fetchall()
    return {row[0]: row[1] for row in rows}


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
