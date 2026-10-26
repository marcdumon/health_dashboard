# the old regime keeps the user's own text, with its en dashes and multiplication signs
# ruff: noqa: RUF001
"""The supplements: a dose unit and the amount of one dose, a plan per moment that changes from a day on, each entry with
its own amount, the amounts of a day as history values, the changes of the plan, and the one-time conversion of the old
list and regime."""

from datetime import date, timedelta

import pytest

from health_dashboard import ledger, supplements

DAY = date(2026, 10, 8)


def item(day: date, name: str) -> dict:
    """One supplement of the card of a day."""
    return next(s for s in supplements.day_view(day)['items'] if s['name'] == name)


def plan(day: date, name: str) -> dict[str, float]:
    """The plan of one supplement on a day, by moment, as the regime gives it."""
    return next(s for s in supplements.regime(day)['items'] if s['name'] == name)['plan']


def test_a_supplement_has_a_dose_unit_and_the_amount_of_one_dose() -> None:
    vit_e = supplements.add_supplement(DAY, ' Vitamin E ', 'µg', 200)
    tea = supplements.add_supplement(DAY, 'Chamomile', '', None)
    found = supplements.regime(DAY)['items']
    assert [(s['id'], s['name'], s['dose_unit'], s['step']) for s in found] == [(vit_e, 'Vitamin E', 'µg', 200),
                                                                               (tea, 'Chamomile', '', None)]
    assert found[0]['plan'] == dict.fromkeys(supplements.MOMENTS, 0), 'nothing planned yet'
    assert 'Vitamin E' not in [s['name'] for s in supplements.regime(DAY - timedelta(days=1))['items']], \
        'in the list from the day it was added'
    for name, unit, step, message in [('', 'mg', 1, 'name'), ('Zinc', 'mmol', 1, 'unit'), ('Zinc', 'mg', None, 'one dose'),
                                      ('Zinc', 'mg', 0, 'one dose'), ('vitamin e', 'µg', 100, 'already in the list')]:
        with pytest.raises(ValueError, match=message):
            supplements.add_supplement(DAY, name, unit, step)


def test_the_plan_changes_from_a_day_on_and_the_days_before_keep_theirs() -> None:
    vit_e = supplements.add_supplement(DAY, 'Vitamin E', 'µg', 200)
    fields = {'name': 'Vitamin E', 'dose_unit': 'µg', 'step': 200}
    supplements.change_supplement(vit_e, DAY, **fields, plan={'First meal': 200})
    later = DAY + timedelta(days=10)
    supplements.change_supplement(vit_e, later, **fields, plan={'First meal': 400, 'Evening meal': 400})
    assert plan(DAY, 'Vitamin E')['First meal'] == 200 and plan(later - timedelta(days=1), 'Vitamin E')['Evening meal'] == 0
    assert (plan(later, 'Vitamin E')['First meal'], plan(later, 'Vitamin E')['Evening meal']) == (400, 400)
    supplements.change_supplement(vit_e, later, **fields, plan={'First meal': 800})
    assert (plan(later, 'Vitamin E')['First meal'], plan(later, 'Vitamin E')['Evening meal']) == (800, 0), \
        'a second change on the same day replaces the first'
    with pytest.raises(ValueError, match='0 or more'):
        supplements.change_supplement(vit_e, DAY, **fields, plan={'First meal': -1})
    with pytest.raises(ValueError, match='moment'):
        supplements.change_supplement(vit_e, DAY, **fields, plan={'Brunch': 200})


def test_each_entry_keeps_its_own_amount() -> None:
    bran = supplements.add_supplement(DAY, 'Oat bran', 'g', 10)
    first = supplements.add_intake(DAY, bran, '10:00')
    supplements.add_intake(DAY, bran, '15:00', 5)
    assert [(e['taken_at'], e['amount']) for e in item(DAY, 'Oat bran')['entries']] == [('10:00', 10), ('15:00', 5)]
    assert item(DAY, 'Oat bran')['total'] == 15
    supplements.change_supplement(bran, DAY, name='Oat bran', dose_unit='g', step=5, plan={})
    assert item(DAY, 'Oat bran')['total'] == 15, 'a new pack size never changes what was taken'
    assert supplements.set_amount(first, 12.5) == DAY and item(DAY, 'Oat bran')['total'] == 17.5
    assert supplements.remove_intake(first) == DAY and item(DAY, 'Oat bran')['total'] == 5
    for change in (lambda: supplements.remove_intake(first), lambda: supplements.set_amount(first, 1),
                   lambda: supplements.add_intake(DAY, 999, '08:00')):
        with pytest.raises(KeyError):
            change()
    for amount in (0, -5):
        with pytest.raises(ValueError, match='more than 0'):
            supplements.add_intake(DAY, bran, '08:00', amount)
    with pytest.raises(ValueError, match='HH:MM'):
        supplements.add_intake(DAY, bran, '8h')


def test_a_yes_or_no_is_taken_once_a_day() -> None:
    tea = supplements.add_supplement(DAY, 'Chamomile', '', None)
    supplements.add_intake(DAY, tea, '21:00')
    assert item(DAY, 'Chamomile')['total'] == 1
    with pytest.raises(ValueError, match='already taken on this day'):
        supplements.add_intake(DAY, tea, '22:00')


def test_the_dose_unit_cannot_change_once_there_are_entries() -> None:
    zinc = supplements.add_supplement(DAY, 'Zinc', 'mg', 25)
    supplements.change_supplement(zinc, DAY, name='Zinc', dose_unit='µg', step=25000, plan={})
    supplements.add_intake(DAY, zinc, '08:00')
    with pytest.raises(ValueError, match='new supplement'):
        supplements.change_supplement(zinc, DAY, name='Zinc', dose_unit='mg', step=25, plan={})


def planned_day() -> tuple[int, int, int]:
    """Selenium at the first meal and the evening meal, Calcium at the first meal, valerian when needed."""
    sel = supplements.add_supplement(DAY, 'Selenium', 'mg', 3)
    calcium = supplements.add_supplement(DAY, 'Calcium', 'mg', 100)
    valerian = supplements.add_supplement(DAY, 'Valerian', 'mg', 5)
    supplements.change_supplement(sel, DAY, name='Selenium', dose_unit='mg', step=3, plan={'First meal': 6, 'Evening meal': 6})
    supplements.change_supplement(calcium, DAY, name='Calcium', dose_unit='mg', step=100, plan={'First meal': 200})
    return sel, calcium, valerian


def test_taken_logs_the_planned_amounts_of_a_moment_once() -> None:
    planned_day()
    view = supplements.day_view(DAY)
    assert [(m['name'], m['planned'], m['taken_at']) for m in view['moments']] == [
        ('First meal', True, None), ('Midday', False, None), ('Afternoon', False, None), ('Evening meal', True, None),
        ('Any time', False, None)], \
        'no bedtime: what is taken then is taken when needed, without a plan'
    assert [(s['name'], s['planned'], s['total']) for s in view['items']] == [('Selenium', 12, 0), ('Calcium', 200, 0),
                                                                              ('Valerian', 0, 0)], \
        'in the order of the day, a supplement without a plan last'
    assert supplements.take_moment(DAY, 'First meal', '09:12') == 2
    assert [(s['name'], s['total']) for s in supplements.day_view(DAY)['items']] == [('Selenium', 6), ('Calcium', 200),
                                                                                   ('Valerian', 0)]
    assert supplements.day_view(DAY)['moments'][0]['taken_at'] == '09:12'
    with pytest.raises(ValueError, match='already taken'):
        supplements.take_moment(DAY, 'First meal', '09:30')
    with pytest.raises(ValueError, match='Nothing is planned'):
        supplements.take_moment(DAY, 'Midday', '12:30')
    assert supplements.undo_moment(DAY, 'First meal') == 2
    assert supplements.day_view(DAY)['moments'][0]['taken_at'] is None and item(DAY, 'Calcium')['total'] == 0


def test_an_entry_fills_the_first_moment_whose_plan_is_not_taken_yet() -> None:
    sel, calcium, valerian = planned_day()
    supplements.add_intake(DAY, calcium, '09:05')
    supplements.add_intake(DAY, sel, '09:00', 6)
    assert supplements.day_view(DAY)['moments'][0]['taken_at'] is None, '100 of the 200 mg Calcium of the first meal'
    supplements.add_intake(DAY, calcium, '09:10')
    assert supplements.day_view(DAY)['moments'][0]['taken_at'] == '09:00', 'all of the first meal is taken'
    for at in ('19:00', '19:05', '21:00'):
        supplements.add_intake(DAY, sel, at)
    supplements.add_intake(DAY, calcium, '12:00')
    supplements.add_intake(DAY, valerian, '23:00')
    assert [e['moment'] for e in item(DAY, 'Selenium')['entries']] == ['First meal', 'Evening meal', 'Evening meal', None], \
        'two pills of 3 mg fill the 6 mg of the evening meal; a third one is extra'
    assert [e['moment'] for e in item(DAY, 'Calcium')['entries']] == ['First meal', 'First meal', None]
    assert [e['moment'] for e in item(DAY, 'Valerian')['entries']] == [None], 'no plan'
    with pytest.raises(ValueError, match='already taken'):
        supplements.take_moment(DAY, 'Evening meal', '19:30')


def test_taken_logs_what_a_moment_still_needs() -> None:
    sel, _, _ = planned_day()
    supplements.add_intake(DAY, sel, '09:00')
    assert supplements.take_moment(DAY, 'First meal', '09:15') == 2
    assert [(e['amount'], e['moment']) for e in item(DAY, 'Selenium')['entries']] == [(3, 'First meal'), (3, 'First meal')]
    assert item(DAY, 'Calcium')['total'] == 200


def test_the_amounts_of_a_day_are_its_history_values() -> None:
    sel, calcium, _ = planned_day()
    supplements.add_intake(DAY, sel, '09:00', 6)
    supplements.add_intake(DAY, sel, '19:00', 3)
    supplements.add_intake(DAY + timedelta(days=2), calcium, '09:00')
    found = supplements.day_values(DAY.isoformat(), (DAY + timedelta(days=2)).isoformat())
    assert found[DAY.isoformat()] == {f'supp:{sel}': 9, f'supp:{calcium}': 0, f'supp:{sel + 2}': 0}, \
        'a supplement without an entry on a day with entries: 0, not taken'
    assert (DAY + timedelta(days=1)).isoformat() not in found, 'a day without any entry: the log was not kept'
    assert found[(DAY + timedelta(days=2)).isoformat()][f'supp:{calcium}'] == 100
    assert [(k['key'], k['name'], k['unit']) for k in supplements.value_keys()] == [
        (f'supp:{sel}', 'Selenium', 'mg'), (f'supp:{calcium}', 'Calcium', 'mg'), (f'supp:{sel + 2}', 'Valerian', 'mg')]


def test_the_changes_of_the_plan_are_the_experiments() -> None:
    sel, calcium, _ = planned_day()
    supplements.add_intake(DAY, sel, '09:00')
    fields = {'dose_unit': 'mg'}
    supplements.change_supplement(calcium, DAY + timedelta(days=5), name='Calcium', step=100, **fields, plan={'First meal': 300})
    supplements.change_supplement(calcium, DAY + timedelta(days=6), name='Calcium', step=100, **fields,
                                  plan={'First meal': 100, 'Evening meal': 200})
    supplements.change_supplement(sel, DAY + timedelta(days=7), name='Selenium', step=3, **fields, plan={})
    supplements.remove_supplement(calcium, DAY + timedelta(days=9))
    assert [(c['name'], c['day'], c['before'], c['after']) for c in supplements.changes()] == [
        ('Calcium', '2026-10-17', 300, 0), ('Selenium', '2026-10-15', 12, 0), ('Calcium', '2026-10-13', 200, 300)], \
        'newest first; a move to another moment is no change; the plan of the first day of the log is no change'


def test_a_removed_supplement_stays_on_a_day_with_its_entries() -> None:
    _, calcium, _ = planned_day()
    supplements.add_intake(DAY, calcium, '09:00')
    supplements.remove_supplement(calcium, DAY)
    assert (item(DAY, 'Calcium')['listed'], item(DAY, 'Calcium')['total']) == (False, 100)
    assert 'Calcium' not in [s['name'] for s in supplements.day_view(DAY + timedelta(days=1))['items']]
    assert 'Calcium' not in [s['name'] for s in supplements.regime(DAY)['items']]
    for change in (lambda: supplements.remove_supplement(calcium, DAY), lambda: supplements.add_intake(DAY, calcium, '10:00'),
                   lambda: supplements.change_supplement(calcium, DAY, name='Calc', dose_unit='mg', step=1, plan={})):
        with pytest.raises(KeyError):
            change()


# the old list (name, unit text, units a day, added) and the old regime (moment, when, name, dose, why, effects) of the user
OLD_LIST = [('Selenium', 'pill 3 mg', 4, None), ('Vitamin E', 'pill 200 µg', 4, None),
            ('Vitamin B12', 'pill 100 µg (4000 units)', 1, None), ('Lutein', 'capsule 504 mg lutein + 378 mg zeaxanthin', 4, None),
            ('Creatine', '5 mg', None, '2026-10-12'), ('Biotin', '750 mg', None, '2026-10-12'),
            ('Valeriane', 'pill 5mg', None, '2026-10-11'), ('Herbal tea', '', None, '2026-10-20')]
OLD_REGIME = [('First meal', 'about 08:30–12:30', 'Selenium', '2 pills (6 mg)', 'for the test', 'Muscle pain.'),
              ('First meal', 'about 08:30–12:30', 'Vitamin E', '1 pill (200 µg)', 'second reason', 'None known.'),
              ('First meal', 'about 08:30–12:30', 'Vitamin B12', '1 pill (4000 units)', '', ''),
              ('First meal', 'about 08:30–12:30', 'Lutein', '2 capsules (1.8 g lutein+zeaxanthin)', '', ''),
              ('Afternoon', 'about 13:30–16:00', 'Creatine', '2 × 5 mg', '', ''),
              ('Afternoon', 'about 13:30–16:00', 'Biotin', '1–2 × 750 mg', '', ''),
              ('Evening meal', 'about 18:30–20:30', 'Selenium', '2 pills (6 mg)', 'for the test', 'See First meal.'),
              ('Evening meal', 'about 18:30–20:30', 'Lutein', '2 capsules (1.8 g lutein+zeaxanthin)', '', ''),
              ('Bedtime', 'when needed', 'Valeriane', '1 pill (5 mg)', 'sleep', '')]


def test_the_old_list_and_regime_become_doses_and_a_plan_once(monkeypatch: pytest.MonkeyPatch) -> None:
    today = date(2026, 10, 24)
    monkeypatch.setattr(supplements, '_today', lambda: today)
    with ledger.connect(write=True) as db:
        db.executemany('INSERT INTO health_supplement_list (name, unit, per_day, position, added) VALUES (?, ?, ?, ?, ?)',
                       [(name, unit, per_day, n, added) for n, (name, unit, per_day, added) in enumerate(OLD_LIST, 1)])
        db.executemany('INSERT INTO health_supplements (moment, moment_when, position, name, dose, why, effects) '
                       'VALUES (?, ?, ?, ?, ?, ?, ?)', [(m, w, n, *rest) for n, (m, w, *rest) in enumerate(OLD_REGIME, 1)])
        ids = {name: n for n, (name, *_) in enumerate(OLD_LIST, 1)}
        db.executemany('INSERT INTO health_units (day, supplement_id, taken_at) VALUES (?, ?, ?)',
                       [('2026-10-08', ids['Selenium'], '09:00'), ('2026-10-08', ids['Selenium'], '09:00'),
                        ('2026-10-12', ids['Creatine'], '15:00'), ('2026-10-12', ids['Lutein'], '09:00'),
                        ('2026-10-20', ids['Herbal tea'], '21:00')])
    found = {s['name']: s for s in supplements.regime(today)['items']}
    assert {name: (s['dose_unit'], s['step']) for name, s in found.items()} == {
        'Selenium': ('mg', 3), 'Vitamin E': ('µg', 200), 'Vitamin B12': ('µg', 100), 'Lutein': ('mg', 882),
        'Creatine': ('g', 5), 'Biotin': ('mg', 750), 'Valeriane': ('mg', 5), 'Herbal tea': ('', None)}, \
        'the numbers of the first unit added; creatine is 5 g; no unit is a yes or no'
    assert set(found['Selenium']) == {'id', 'name', 'dose_unit', 'step', 'comment', 'plan', 'used'}, \
        'no why and no side effects'
    first = date(2026, 10, 8)
    assert plan(first, 'Selenium') == {'First meal': 6, 'Midday': 0, 'Afternoon': 0, 'Evening meal': 6,
                                          'Any time': 0}
    assert (plan(first, 'Lutein')['First meal'], plan(date(2026, 10, 12), 'Creatine')['Afternoon']) == (1764, 10), \
        'the count of the dose times one pill; from the day the supplement was added'
    assert plan(date(2026, 10, 12), 'Biotin')['Afternoon'] == 1500, '1–2 is 2'
    assert sum(plan(today, 'Valeriane').values()) == 0 and sum(plan(today, 'Herbal tea').values()) == 0
    assert plan(today - timedelta(days=1), 'Vitamin E')['First meal'] == 200
    assert plan(today, 'Vitamin E')['First meal'] == 800, 'the 4 a day of the list count from the conversion'
    values = supplements.day_values('2026-10-08', '2026-10-20')
    assert (values['2026-10-08'][f'supp:{ids["Selenium"]}'], values['2026-10-12'][f'supp:{ids["Creatine"]}'],
            values['2026-10-12'][f'supp:{ids["Lutein"]}'], values['2026-10-20'][f'supp:{ids["Herbal tea"]}']) == (6, 5, 882, 1)
    moments = {name: [e['moment'] for e in item(day, name)['entries']] for day, name in [
        (first, 'Selenium'), (date(2026, 10, 12), 'Creatine'), (date(2026, 10, 20), 'Herbal tea')]}
    assert moments == {'Selenium': ['First meal', 'First meal'], 'Creatine': ['Afternoon'], 'Herbal tea': [None]}, \
        'the old entries fill the moments of their plan in time order'
    supplements.regime(today)
    with ledger.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM health_plan').fetchone()[0] == 9, 'once'
        assert db.execute('SELECT COUNT(*) FROM health_supplements').fetchone()[0] == 9, 'the old regime stays'


def test_a_new_database_starts_with_an_empty_list() -> None:
    assert supplements.day_view(DAY) == {'moments': [{'name': m, 'planned': False, 'taken_at': None} for m in supplements.MOMENTS],
                                         'items': []}


def test_a_supplement_can_be_planned_at_any_time_of_the_day() -> None:
    psyllium = supplements.add_supplement(DAY, 'Psyllium', 'g', 5)
    supplements.change_supplement(psyllium, DAY, 'Psyllium', 'g', 5, {'Any time': 10})
    assert supplements.regime(DAY)['moments'][-1] == 'Any time' and plan(DAY, 'Psyllium')['Any time'] == 10
    assert [m['name'] for m in supplements.day_view(DAY)['moments'] if m['planned']] == ['Any time']
    assert supplements.take_moment(DAY, 'Any time', '15:20') == 1
    assert supplements.day_view(DAY)['moments'][-1]['taken_at'] == '15:20' and item(DAY, 'Psyllium')['total'] == 10


def test_a_comment_says_how_to_take_a_supplement_and_a_change_without_one_keeps_it() -> None:
    coq10 = supplements.add_supplement(DAY, 'CoQ10', 'mg', 100, ' with fat ')
    assert next(s for s in supplements.regime(DAY)['items'] if s['id'] == coq10)['comment'] == 'with fat'
    supplements.change_supplement(coq10, DAY, 'CoQ10', 'mg', 100, {'First meal': 100}, 'with a meal')
    assert item(DAY, 'CoQ10')['comment'] == 'with a meal'
    supplements.change_supplement(coq10, DAY, 'CoQ10', 'mg', 100, {'First meal': 100})
    assert item(DAY, 'CoQ10')['comment'] == 'with a meal', 'no comment given: it stays'
    supplements.change_supplement(coq10, DAY, 'CoQ10', 'mg', 100, {'First meal': 100}, '')
    assert item(DAY, 'CoQ10')['comment'] == ''
    with pytest.raises(ValueError, match='200'):
        supplements.change_supplement(coq10, DAY, 'CoQ10', 'mg', 100, {}, 'x' * 201)
