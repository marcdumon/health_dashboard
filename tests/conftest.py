"""Shared test fixtures: every test has its own database and folders, and no test reaches Withings, Google Health, the
Polar logger, the phone, the Sleep as Android backup or the user's own values."""

from pathlib import Path

import pytest

from health_dashboard import config as config_module
from health_dashboard import ledger as ledger_store


@pytest.fixture(autouse=True)
def example_settings(monkeypatch: pytest.MonkeyPatch):
    """Every test reads health_dashboard.example.toml, never the user's own health_dashboard.toml (not in git)."""
    monkeypatch.setattr(config_module, 'CONFIG_FILE', Path(__file__).parents[1] / 'health_dashboard.example.toml')
    config_module.config.cache_clear()
    yield
    config_module.config.cache_clear()


@pytest.fixture(autouse=True)
def no_real_withings_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """The user's Withings codes and callback in .env never reach a test; tests that need codes set their own."""
    from health_dashboard import withings

    monkeypatch.setattr(withings, '_env', lambda name: '')


@pytest.fixture(autouse=True)
def ledger(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """Keep every test's data out of the real database."""
    path = tmp_path / 'health.db'
    monkeypatch.setattr(ledger_store, 'DB', path)
    return path


@pytest.fixture(autouse=True)
def polar_in_tmp(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The raw Polar samples of a test go to its own folder, and no test reads the user's Polar folders or reaches the user's
    logger or phone."""
    from health_dashboard import polar, polar_phone, polar_record, polar_store

    monkeypatch.setattr(polar_store, 'RAW_DB', tmp_path / 'polar.db')
    monkeypatch.setattr(polar, 'folders', lambda: (None, None))
    monkeypatch.setattr(polar_phone, 'address', lambda: None)
    monkeypatch.setattr(polar_record, 'settings', lambda: (None, 'http://127.0.0.1:9'))
    monkeypatch.setattr(polar_record, 'LOG', tmp_path / 'polar_record.log')
    monkeypatch.setattr(polar_record, '_state', polar_record._fresh())


@pytest.fixture(autouse=True)
def no_real_person(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test reads the user's own values in person.toml; tests that need values set their own (polar_fixtures.set_person)."""
    from health_dashboard import person
    from health_dashboard.config import PersonSettings

    monkeypatch.setattr(person, 'settings', lambda: PersonSettings())


@pytest.fixture(autouse=True)
def no_real_sleep_backup(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test reads the user's Sleep as Android backup in Dropbox; tests of the import set their own file."""
    from health_dashboard import sleep

    monkeypatch.setattr(sleep, 'backup_file', lambda: None)


@pytest.fixture(autouse=True)
def fitbit_in_tmp(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The Fitbit files and token of a test are in its own folder, and no test reaches Google Health or opens a login."""
    from google_api import auth, health

    from health_dashboard import fitbit

    def refuse(*args: object) -> None:
        raise AssertionError('test tried to reach Google Health for real')

    monkeypatch.setattr(fitbit, 'FOLDER', tmp_path / 'google_health')
    monkeypatch.setattr(fitbit, 'TOKEN_FILE', tmp_path / 'secrets' / 'google_health_token.json')
    monkeypatch.setattr(fitbit, 'CLIENT_FILE', tmp_path / 'secrets' / 'client_secret.json')
    monkeypatch.setattr(fitbit, 'first_day', lambda: None)
    monkeypatch.setattr(health, 'collect', refuse)
    monkeypatch.setattr(auth, 'login', refuse)
