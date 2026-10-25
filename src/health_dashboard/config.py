"""Settings from `health_dashboard.toml`, the user's own values from `person.toml` (not in git) and secrets from `.env`.

These files are in the app folder: the folder of the Python environment (the repo in development, the install folder of an
installed app). The database, the downloads and the logs are in the data folder: `data_dir` of `health_dashboard.toml`."""

import sys
import tomllib
from dataclasses import dataclass
from datetime import date
from functools import cache
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

APP_DIR = Path(sys.prefix).parent
CONFIG_FILE = APP_DIR / 'health_dashboard.toml'
# the user's own values: git ignores this file, person.example.toml shows its part
PERSON_FILE = APP_DIR / 'person.toml'


def data_dir() -> Path:
    """The data folder: data_dir of health_dashboard.toml, or the folder data in the app folder when it is not set."""
    value = tomllib.loads(CONFIG_FILE.read_text()).get('data_dir', '') if CONFIG_FILE.is_file() else ''
    return Path(value).expanduser() if value else APP_DIR / 'data'


DATA_DIR = data_dir()


@dataclass(frozen=True)
class PolarSettings:
    """The Polar H10 settings: the folders of the phone app's files and the logger's sessions, the logger's project folder
    (where `uv run polar-h10 record` runs), the address of its API, and the folder of the phone app's files on the phone
    (ftp://user@host:port/folder/, through WiFi FTP Server); '' when not set."""

    phone_dir: str = ''
    logger_dir: str = ''
    logger_project: str = ''
    logger_api: str = 'http://127.0.0.1:8770'
    phone_ftp: str = ''


@dataclass(frozen=True)
class SleepSettings:
    """The sleep settings: the Sleep as Android backup that Dropbox syncs to the PC (a zip); '' when not set."""

    backup_file: str = ''


@dataclass(frozen=True)
class FitbitSettings:
    """The Fitbit settings: the first day to download from Google Health (YYYY-MM-DD, the day the band was first worn);
    '' when not set."""

    first_day: str = ''


@dataclass(frozen=True)
class PersonSettings:
    """The user's own values, from the [person] part of person.toml: the birth date (YYYY-MM-DD, or a TOML date), the sex
    ('male' or 'female'), the HR from which a second adds to the net energy expenditure, the personal correction factor of
    the net energy expenditure, and the weight (kg) and height (cm) for when the Withings scale has none; '' or None when
    not set."""

    birth_date: str | date = ''
    sex: str = ''
    energy_from_hr: int = 90
    energy_factor: float = 1.0
    weight_kg: float | None = None
    height_cm: float | None = None


@dataclass(frozen=True)
class Config:
    """Everything configurable, in one place."""

    port: int
    timezone: str
    polar: PolarSettings = PolarSettings()
    sleep: SleepSettings = SleepSettings()
    fitbit: FitbitSettings = FitbitSettings()
    person: PersonSettings = PersonSettings()

    @property
    def tz(self) -> ZoneInfo:
        """The time zone of the days."""
        return ZoneInfo(self.timezone)


@cache
def config() -> Config:
    """Load the config file once."""
    load_dotenv(APP_DIR / '.env')
    if not CONFIG_FILE.is_file():
        raise SystemExit(f'No {CONFIG_FILE.name} in {APP_DIR}: copy health_dashboard.example.toml to {CONFIG_FILE.name}, '
                         'then set your own folders in it.')
    raw = tomllib.loads(CONFIG_FILE.read_text())
    return Config(
        port=int(raw['server']['port']),
        timezone=raw['time']['timezone'],
        polar=PolarSettings(**raw.get('polar', {})),
        sleep=SleepSettings(**raw.get('sleep', {})),
        fitbit=FitbitSettings(**raw.get('fitbit', {})),
        person=PersonSettings(**(tomllib.loads(PERSON_FILE.read_text()).get('person', {}) if PERSON_FILE.is_file() else {})),
    )
