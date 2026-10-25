"""The user's own values for the Health page, from the [person] part of person.toml in the app folder (git ignores that
file; person.example.toml shows the part): the birth date, the sex, the HR from which a second adds to the net energy
expenditure, its correction factor, and the weight and height for when the Withings scale has none. The app reads them at
its start."""

from datetime import date

from health_dashboard.config import PersonSettings, config

SEXES = ('male', 'female')
# where a message sends the user to set a value
SET_IT = ('in the [person] part of person.toml in the app folder (person.example.toml shows how: copy it to person.toml the '
          'first time), then restart the app')


def settings() -> PersonSettings:
    """The [person] part as the app read it at its start."""
    return config().person


def birth_year() -> int | None:
    """The year of the birth date; None when it is not set or not a date (YYYY-MM-DD)."""
    found = settings().birth_date
    if isinstance(found, date):
        return found.year
    try:
        return date.fromisoformat(found.strip()).year
    except ValueError:
        return None


def sex() -> str | None:
    """'male' or 'female'; None when not set or another word."""
    found = settings().sex.strip().lower()
    return found if found in SEXES else None
