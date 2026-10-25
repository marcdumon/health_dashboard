"""The user's own values in the [person] part of person.toml: read at the start of the app when the file is there, a birth
date as text or as a TOML date, and the sex in either case; a missing or wrong value is not set."""

import pytest

from health_dashboard import config as config_module
from health_dashboard import person
from tests.polar_fixtures import set_person


def test_the_person_part_is_read_from_person_toml_when_it_is_there(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(config_module, 'PERSON_FILE', tmp_path / 'person.toml')
    assert config_module.config.__wrapped__().person == config_module.PersonSettings(), 'no file: nothing set'
    (tmp_path / 'person.toml').write_text("[person]\nbirth_date = 1970-01-31\nsex = 'Male'\nweight_kg = 70.5\n")
    found = config_module.config.__wrapped__().person
    assert (found.sex, found.weight_kg, found.height_cm, found.energy_from_hr) == ('Male', 70.5, None, 90)


@pytest.mark.parametrize(('birth_date', 'sex', 'expected'), [
    ('1970-01-31', 'male', (1970,'male')), ('  1970-01-31 ', ' Female ', (1970,'female')),
    ('31/01/1970', 'man', (None, None)), ('', '', (None, None)),
])
def test_a_wrong_or_missing_value_is_not_set(monkeypatch, birth_date: str, sex: str, expected: tuple) -> None:
    set_person(monkeypatch, birth_date=birth_date, sex=sex)
    assert (person.birth_year(), person.sex()) == expected


def test_a_birth_date_written_as_a_toml_date_counts(monkeypatch) -> None:
    from datetime import date

    set_person(monkeypatch, birth_date=date(1970, 1, 31))
    assert person.birth_year() == 1970


def test_a_missing_settings_file_says_how_to_make_it(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(config_module, 'CONFIG_FILE', tmp_path / 'health_dashboard.toml')
    with pytest.raises(SystemExit, match=r'copy health_dashboard\.example\.toml to health_dashboard\.toml'):
        config_module.config.__wrapped__()
