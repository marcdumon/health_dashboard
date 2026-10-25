"""The net energy expenditure of the Polar H10 sessions (what the exercise adds to the BMR), an estimate from the HR of
each second: the equation of Keytel et al. (2005) minus the BMR from energy_from_hr on and nothing below it, never below 0,
with the weight and the BMR of the Withings scale (not hidden, the newest up to the day of the session, else the first
after it) or of person.toml; nothing without the birth date, the sex, a weight or a BMR; per week by activity without the
HRV tests; and in the routes of the card, the detail and the weeks."""

from datetime import date, datetime

from fastapi.testclient import TestClient

from health_dashboard import api, ledger, polar_energy, polar_store
from tests.polar_fixtures import set_person, stored_session

WALK, HRV = 1, 6
# 600 s at 120 bpm, then 300 s at 70 bpm
HR = [120.0] * 600 + [70.0] * 300


def scale(grpid: int, kind: int, value: float, measured_at: str, hidden: bool = False) -> None:
    """A whole-body value of the Withings scale (kind 1 the weight, 226 the BMR), hidden as wrong when asked."""
    with ledger.connect(write=True) as db:
        db.execute('INSERT INTO withings_values (grpid, type, position, value, measured_at) VALUES (?, ?, -1, ?, ?)',
                   (grpid, kind, value, measured_at))
        if hidden:
            db.execute("INSERT INTO withings_hidden (grpid, hidden_at) VALUES (?, '2026-07-01T09:00')", (grpid,))


def weighed() -> None:
    """80 kg and a BMR of 1800 kcal a day on 1 June, a wrong 200 kg hidden on 30 June, 90 kg on 1 August."""
    scale(1, 1, 80.0, '2026-06-01T07:00')
    scale(1, 226, 1800.0, '2026-06-01T07:00')
    scale(2, 1, 200.0, '2026-06-30T07:00', hidden=True)
    scale(3, 1, 90.0, '2026-08-01T07:00')


def test_a_man_burns_the_keytel_energy_minus_his_bmr_from_90_bpm_and_nothing_below_it(monkeypatch) -> None:
    set_person(monkeypatch, birth_date='1960-05-01', sex='male')
    weighed()
    walk = stored_session('2026-07-01T08:00:00+00:00', HR, None)
    early = stored_session('2026-05-01T08:00:00+00:00', HR, None)
    # age 66, 80 kg (not the hidden 200 kg of the day before): (-55.0969 + 0.6309 * 120 + 0.1988 * 80 + 0.2017 * 66) kJ
    # a minute for 10 minutes = 498.3 kJ = 119.1 kcal, minus 600 s of a BMR of 1800 kcal a day = 12.5 kcal; the 300 s at
    # 70 bpm add nothing
    assert polar_energy.energy([polar_store.session(walk)]) == {walk: 107}
    assert polar_energy.energy([polar_store.session(early)]) == {early: 107}, 'before the first weighing: its first one'
    assert polar_energy.missing() == ''


def test_a_woman_without_a_scale_uses_her_weight_and_the_bmr_of_her_height(monkeypatch) -> None:
    set_person(monkeypatch, birth_date='1960-05-01', sex='female', weight_kg=60, height_cm=165)
    walk = stored_session('2026-07-01T08:00:00+00:00', HR, None)
    # (-20.4022 + 0.4472 * 120 - 0.1263 * 60 + 0.074 * 66) kJ a minute for 10 minutes = 73.1 kcal; the BMR 10 * 60 +
    # 6.25 * 165 - 5 * 66 - 161 = 1140 kcal a day: 7.9 kcal in those 600 s
    assert polar_energy.energy([polar_store.session(walk)]) == {walk: 65}


def test_the_hr_from_which_the_energy_follows_the_hr_comes_from_person_toml(monkeypatch) -> None:
    set_person(monkeypatch, birth_date='1960-05-01', sex='male', energy_from_hr=130)
    weighed()
    walk = stored_session('2026-07-01T08:00:00+00:00', HR, None)
    assert polar_energy.energy([polar_store.session(walk)]) == {walk: 0}, 'every second is below 130 bpm'



def test_a_light_effort_never_gives_less_than_0_and_without_a_bmr_there_is_no_energy(monkeypatch) -> None:
    set_person(monkeypatch, birth_date='1996-05-01', sex='male', energy_from_hr=50, weight_kg=60, height_cm=170)
    rest = stored_session('2026-07-01T08:00:00+00:00', [60.0] * 600, None)
    # at 60 bpm the equation gives 0.18 kcal a minute, less than the BMR of 1.05 kcal a minute
    assert polar_energy.energy([polar_store.session(rest)]) == {rest: 0}
    set_person(monkeypatch, birth_date='1996-05-01', sex='male', weight_kg=60)
    walk = stored_session('2026-07-02T08:00:00+00:00', HR, None)
    assert polar_energy.energy([polar_store.session(walk)]) == {walk: None}, 'no height and no scale: no BMR to take off'
    assert polar_energy.missing().startswith('The net energy expenditure needs height_cm')

def test_without_the_birth_date_the_sex_or_a_weight_there_is_no_energy_and_the_message_says_what_to_set(monkeypatch) -> None:
    walk = stored_session('2026-07-01T08:00:00+00:00', HR, None)
    assert polar_energy.energy([polar_store.session(walk)]) == {walk: None}
    message = polar_energy.missing()
    assert message.startswith('The net energy expenditure needs birth_date, sex, weight_kg and height_cm: set them in the '
                              '[person] part of person.toml in the app folder')
    assert 'A Withings scale gives the weight and the BMR' in message
    set_person(monkeypatch, birth_date='1960-05-01', sex='male')
    weighed()
    assert polar_energy.missing() == ''


def test_the_energy_per_week_adds_up_the_sessions_by_activity_without_the_hrv_tests(monkeypatch) -> None:
    set_person(monkeypatch, birth_date='1960-05-01', sex='male')
    weighed()
    polar_store.set_activity(stored_session('2026-07-01T08:00:00+00:00', HR, None), WALK)
    stored_session('2026-07-02T08:00:00+00:00', HR, None)
    polar_store.set_activity(stored_session('2026-07-03T06:00:00+00:00', HR, None), HRV)
    assert polar_energy.weeks(date(2026, 7, 1), date(2026, 7, 7)) == [
        {'week': '2026-06-29', 'kcal': {'Walk': 107, 'No activity': 107}}, {'week': '2026-07-06', 'kcal': {}}]


def test_the_card_the_detail_and_the_weeks_give_the_energy(monkeypatch) -> None:
    client = TestClient(api.app)
    walk = stored_session('2026-07-01T08:00:00+00:00', HR, None)
    weeks = client.get('/api/health/polar/energy?days=7').json()
    assert weeks['missing'].startswith('The net energy expenditure needs birth_date')
    set_person(monkeypatch, birth_date='1960-05-01', sex='male')
    weighed()
    detail = client.get(f'/api/health/polar/sessions/{walk}').json()
    assert (detail['energy'], detail['energy_missing']) == (107, '')
    card = client.get('/api/health?day=2026-07-01').json()['polar']['last_exercise']
    assert (card['id'], card['energy']) == (walk, 107)
    monkeypatch.setattr(api, '_health_now', lambda: datetime(2026, 7, 2, 12, 0).astimezone())
    weeks = client.get('/api/health/polar/energy?days=7').json()
    assert (weeks['missing'], weeks['weeks'][-1], weeks['activities'][0]) == (
        '', {'week': '2026-06-29', 'kcal': {'No activity': 107}}, 'Walk')


def test_the_energy_factor_of_person_toml_corrects_every_value_and_a_wrong_one_is_not_used(monkeypatch) -> None:
    weighed()
    walk = stored_session('2026-07-01T08:00:00+00:00', HR, None)
    set_person(monkeypatch, birth_date='1960-05-01', sex='male', energy_factor=0.7)
    # 106.59 kcal x 0.7
    assert polar_energy.energy([polar_store.session(walk)]) == {walk: 75}
    assert polar_energy.factor() == (0.7, 'Corrected by your energy_factor 0.7.')
    set_person(monkeypatch, birth_date='1960-05-01', sex='male', energy_factor=5)
    assert polar_energy.energy([polar_store.session(walk)]) == {walk: 107}
    assert polar_energy.factor()[1].startswith('energy_factor 5 is outside 0.3-2, so the app does not use it: change it in '
                                               'the [person] part of person.toml')
    set_person(monkeypatch, birth_date='1960-05-01', sex='male')
    assert polar_energy.factor() == (1.0, '')
    client = TestClient(api.app)
    assert client.get(f'/api/health/polar/sessions/{walk}').json()['energy_note'] == ''
