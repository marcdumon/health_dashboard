"""The food of a day, noted without counting: the first and the last food, the scales from -3 to +3 against the usual day,
the marks, and a fast day with its kcal and grams; the values of the history (the eating window, the hours without food,
the last food to sleep, the fast day and the refeed day), and the routes."""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from health_dashboard import analysis, api, food, history, ledger

D = date(2026, 10, 1)
NOW = datetime(2026, 10, 8, 12, 41, tzinfo=ZoneInfo('Europe/Brussels'))


@pytest.fixture
def client(monkeypatch) -> TestClient:
    """A client at 12:41 on 8 October."""
    monkeypatch.setattr(api, '_health_now', lambda: NOW)
    return TestClient(api.app, follow_redirects=False)


def day(n: int) -> date:
    """The day n days after D."""
    return D + timedelta(days=n)


def values(first: int, last: int) -> dict[str, dict[str, float]]:
    """The food values of the days D+first to D+last."""
    return food.day_values(day(first).isoformat(), day(last).isoformat())


def sleep(start: datetime, hours: float = 8) -> None:
    """A sleep from `start` (local time) of `hours`."""
    end = start + timedelta(hours=hours)
    with ledger.connect(write=True) as db:
        number = db.execute('SELECT COUNT(*) FROM sleep_records').fetchone()[0] + 1
        db.execute('INSERT INTO sleep_records (id, tz, start_at, end_at, day, in_bed_min, sleep_min) '
                   "VALUES (?, 'Europe/Brussels', ?, ?, ?, ?, ?)",
                   (number, f'{start:%Y-%m-%dT%H:%M}:00+02:00', f'{end:%Y-%m-%dT%H:%M}:00+02:00', end.date().isoformat(),
                    hours * 60, hours * 60))


def test_a_day_without_a_note_has_no_food_values_and_on_a_noted_day_a_scale_not_chosen_is_the_usual_day() -> None:
    assert food.day(D) is None
    assert values(0, 0) == {}, 'an unknown day is not a usual day'
    food.set_food(D, {'first_food': '08:30'})
    assert food.day(D) == {'first_food': '08:30', 'last_food': None, 'amount': None, 'protein': None, 'carbs': None, 'fat': None,
                           'alcohol': False, 'salt': False, 'fast': False, 'kcal': None, 'protein_g': None, 'carbs_g': None,
                           'fat_g': None}
    assert values(0, 0) == {'2026-10-01': {'food:amount': 0, 'food:protein': 0, 'food:carbs': 0, 'food:fat': 0,
                                           'food:alcohol': 0, 'food:salt': 0, 'food:fast_day': 0}}
    food.set_food(D, {'protein': 2, 'carbs': -1, 'alcohol': True})
    found = values(0, 0)['2026-10-01']
    assert (found['food:protein'], found['food:carbs'], found['food:alcohol']) == (2, -1, 1)


def test_a_change_keeps_the_other_fields_and_a_day_whose_fields_are_all_empty_has_no_note() -> None:
    food.set_food(D, {'first_food': '08:30', 'protein': 2})
    food.set_food(D, {'protein': None})
    assert food.day(D)['first_food'] == '08:30' and food.day(D)['protein'] is None
    food.set_food(D, {'first_food': None})
    assert food.day(D) is None
    food.set_food(D, {'amount': 0})
    assert food.day(D)['amount'] == 0, 'a 0 chosen notes a usual day'
    food.clear(D)
    assert food.day(D) is None


def test_only_valid_values_are_taken_and_food_after_midnight_belongs_to_the_next_day() -> None:
    with pytest.raises(ValueError, match=r'-3 to \+3'):
        food.set_food(D, {'protein': 4})
    with pytest.raises(ValueError, match='HH:MM'):
        food.set_food(D, {'first_food': '8h'})
    with pytest.raises(ValueError, match='kcal'):
        food.set_food(D, {'kcal': -5})
    with pytest.raises(ValueError, match='grams'):
        food.set_food(D, {'fat_g': 2000})
    with pytest.raises(ValueError, match='yes or no'):
        food.set_food(D, {'alcohol': None})
    with pytest.raises(ValueError, match='sugar'):
        food.set_food(D, {'sugar': 1})
    assert food.day(D) is None
    food.set_food(D, {'first_food': '20:00'})
    with pytest.raises(ValueError, match='midnight'):
        food.set_food(D, {'last_food': '00:30'})
    assert food.day(D)['last_food'] is None, 'a refused change changes nothing'


def test_a_fast_day_has_its_kcal_and_grams_and_no_scales_and_the_days_count_in_a_row_with_the_refeed_days_after() -> None:
    food.set_food(day(0), {'protein': 2})
    for n, kcal in ((0, 1100), (1, 750), (2, 750)):
        food.set_food(day(n), {'fast': True, 'kcal': kcal, 'protein_g': 40, 'carbs_g': 90, 'fat_g': 45})
    food.set_food(day(3), {'first_food': '09:00'})
    food.set_food(day(12), {'fast': True})
    found = values(0, 12)
    assert found['2026-10-01'] == {'food:alcohol': 0, 'food:salt': 0, 'food:kcal': 1100, 'food:protein_g': 40, 'food:carbs_g': 90,
                                   'food:fat_g': 45, 'food:fast_day': 1}, 'no scales on a fast day'
    assert [found[day(n).isoformat()]['food:fast_day'] for n in (1, 2)] == [2, 3]
    assert found['2026-10-04']['food:fast_day'] == 0 and found['2026-10-04']['food:refeed_day'] == 1
    assert [found.get(day(n).isoformat(), {}).get('food:refeed_day') for n in range(4, 11)] == [2, 3, 4, 5, 6, 7, None]
    assert 'food:fast_day' not in found.get('2026-10-06', {}), 'a day without a note is no fast day and no usual day'
    assert found['2026-10-13'] == {'food:alcohol': 0, 'food:salt': 0, 'food:fast_day': 1}, 'a new fast starts at day 1'
    assert food.REFEED_DAYS == 7


def test_the_eating_window_the_hours_without_food_and_the_last_food_to_sleep() -> None:
    food.set_food(day(0), {'first_food': '08:00', 'last_food': '19:30'})
    food.set_food(day(1), {'first_food': '10:00', 'last_food': '18:00'})
    sleep(datetime(2026, 10, 1, 15, 0), 1)
    sleep(datetime(2026, 10, 1, 23, 0))
    for n in (2, 3):
        food.set_food(day(n), {'fast': True})
    food.set_food(day(4), {'first_food': '12:00'})
    food.set_food(day(6), {'first_food': '09:00'})
    found = values(0, 6)
    assert (found['2026-10-01']['food:window'], found['2026-10-01']['food:to_sleep']) == (11.5, 3.5), 'not the nap before it'
    assert 'food:fasted' not in found['2026-10-01'], 'the day before has no note'
    assert (found['2026-10-02']['food:window'], found['2026-10-02']['food:fasted']) == (8.0, 14.5)
    assert 'food:to_sleep' not in found['2026-10-02'], 'no sleep starts in the 12 hours after the last food'
    assert found['2026-10-05']['food:fasted'] == 66.0, 'from 18:00 on 2 October, over two fast days without food'
    assert 'food:fasted' not in found['2026-10-07'], 'a day without a note stops it'
    food.set_food(day(2), {'kcal': 750})
    assert 'food:fasted' not in values(4, 4)['2026-10-05'], 'a fast day with food but without its times stops it'


def test_the_history_and_the_analysis_have_the_food_values_without_an_outlier_list() -> None:
    for n in range(10):
        food.set_food(day(n), {'protein': 3 if n == 5 else 0})
    rows = {r['day']: r for r in history.days(10, day(9))}
    assert rows['2026-10-06']['food:protein'] == 3 and rows['2026-10-05']['food:fast_day'] == 0
    assert analysis.known('food:protein') and analysis.known('food:fasted') and not analysis.known('food:sugar')
    assert analysis.analyse('food:protein', None, 30, day(9), None)['outliers'] == []


def test_the_routes_note_the_day_or_the_day_before_and_answer_with_the_view_of_the_page(client) -> None:
    view = client.put('/api/health/food', json={'day': '2026-10-07', 'page_day': '2026-10-08', 'first_food': '08:00'}).json()
    assert view['day'] == '2026-10-08'
    assert view['food']['day'] is None and view['food']['day_before']['first_food'] == '08:00'
    view = client.put('/api/health/food', json={'day': '2026-10-08', 'protein': 1}).json()
    assert view['food']['day']['protein'] == 1
    answer = client.put('/api/health/food', json={'day': '2026-10-08', 'protein': 5})
    assert answer.status_code == 400 and '-3 to +3' in answer.json()['detail']
    assert client.put('/api/health/food', json={'day': '2026-10-09', 'protein': 1}).status_code == 400, 'not a day to come'
    view = client.delete('/api/health/food', params={'day': '2026-10-07', 'page_day': '2026-10-08'}).json()
    assert view['day'] == '2026-10-08' and view['food']['day_before'] is None
