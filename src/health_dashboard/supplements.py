# the old doses keep the user's own text, with its en dashes and multiplication signs
# ruff: noqa: RUF001, RUF003
"""The supplements of the Health page, kept as experiments. Each supplement has a dose unit (mg, µg, g, IU or FU; none for
a yes or no of the day), the amount of one dose (a pill, a capsule, a scoop) and a comment on how to take it (with a meal,
with fat). Its plan gives the amount at each moment of the day (Any time: whenever in the day)
and changes from a day on, so the days before keep theirs. Each entry of the log keeps its own amount, so a new pack never
changes the past. The amounts of a day are history values (supp:<id>), and each change of a day's planned total is an
experiment whose effect the analysis sets before against after (analysis.effect)."""

import re
from datetime import date, datetime, timedelta

from health_dashboard import store
from health_dashboard.ledger import connect

MOMENTS = ('First meal', 'Midday', 'Afternoon', 'Evening meal', 'Any time')
COMMENT_MAX = 200
DOSE_UNITS = ('mg', 'µg', 'g', 'IU', 'FU')
# in the list on a day: added on or before it, and not removed on or before it
LISTED = '(added IS NULL OR added <= :day) AND (removed IS NULL OR removed > :day)'
SAVE_PLAN = ('INSERT INTO health_plan (supplement_id, moment, from_day, amount) VALUES (?, ?, ?, ?) '
             'ON CONFLICT (supplement_id, moment, from_day) DO UPDATE SET amount = excluded.amount')
# the old list had a unit text and a number of units a day, the old regime a dose text per moment: converted once; and
# the old entries had no moment: they get theirs once
CONVERTED, TAGGED = 'supplement_doses', 'supplement_moments'
# the room for a rounding error when amounts are added up
EPSILON = 1e-9
# one pill of an old unit text: the numbers of its first unit added ('capsule 504 mg lutein + 378 mg zeaxanthin' is 882 mg)
AMOUNT = re.compile(r'(\d+(?:[.,]\d+)?)\s*(mg|µg|g|IU|FU)\b')
# the pills of an old dose: '2 pills (6 mg)' is 2, '1–2 × 750 mg' is 2
COUNT = re.compile(r'(\d+)(?:–(\d+))?')
# powders noted in mg in the old list where g was meant
GRAMS = ('Creatine', 'Inuline')


def _today() -> date:
    """Today on the Health page."""
    return store.health_day(datetime.now())


def _dose(text: str, name: str) -> tuple[str, float | None]:
    """The dose unit and one pill of an old unit text; no unit is a yes or no."""
    found = AMOUNT.findall(text)
    if not found:
        return '', None
    unit = found[0][1]
    return 'g' if name in GRAMS and unit == 'mg' else unit, sum(float(n.replace(',', '.')) for n, u in found if u == unit)


def _count(dose: str) -> int:
    """The number of pills of an old dose, the higher one of a range; 1 without a number."""
    found = COUNT.match(dose)
    return int(found[2] or found[1]) if found else 1


def convert() -> None:
    """Convert the old list and regime (_doses) and give the old entries their moments (_tag), each once."""
    settings = 'SELECT key FROM health_settings WHERE key IN (?, ?)'
    with connect() as db:
        if len(db.execute(settings, (CONVERTED, TAGGED)).fetchall()) == 2:
            return
    with connect(write=True) as db:
        done = {row[0] for row in db.execute(settings, (CONVERTED, TAGGED))}
        for key, step in ((CONVERTED, _doses), (TAGGED, _tag)):
            if key not in done:
                step(db)
                db.execute('INSERT INTO health_settings (key, value) VALUES (?, ?)',
                           (key, datetime.now().isoformat(timespec='seconds')))


def _doses(db) -> None:
    """Give the old list and the old regime dose units, amounts and a plan. Each old entry is one pill. The plan of a moment
    is the count of its old dose times one pill, from the day the supplement was added (else the first day of the log);
    'when needed' has no plan. Where the old units a day differ from the regime, they count from today. The old regime stays
    in its table."""
    start = db.execute('SELECT MIN(day) FROM health_units').fetchone()[0] or _today().isoformat()
    regime = db.execute('SELECT moment, moment_when, name, dose FROM health_supplements '
                        'ORDER BY position').fetchall()
    for row in db.execute('SELECT id, name, unit, per_day, added FROM health_supplement_list WHERE dose_unit IS NULL').fetchall():
        unit, step = _dose(row['unit'], row['name'])
        one = step or 1
        mine = [r for r in regime if r['name'].casefold() == row['name'].casefold()]
        db.execute('UPDATE health_supplement_list SET dose_unit = ?, step = ? WHERE id = ?', (unit, step, row['id']))
        db.execute('UPDATE health_units SET amount = ? WHERE supplement_id = ? AND amount IS NULL', (one, row['id']))
        counts = {r['moment']: _count(r['dose']) for r in mine if r['moment'] in MOMENTS and r['moment_when'] != 'when needed'}
        db.executemany(SAVE_PLAN, [(row['id'], moment, row['added'] or start, count * one) for moment, count in counts.items()])
        if counts and row['per_day'] and row['per_day'] != sum(counts.values()):
            moment = next(iter(counts))
            more = row['per_day'] - sum(counts.values())
            db.execute(SAVE_PLAN, (row['id'], moment, _today().isoformat(), (counts[moment] + more) * one))


def _tag(db) -> None:
    """Give each entry without a moment the moment it fills, in time order, as a new entry gets it."""
    rows = db.execute('SELECT id, day, supplement_id FROM health_units WHERE moment IS NULL ORDER BY day, taken_at, id').fetchall()
    for row in rows:
        moment = _waiting(db, row['day'], row['supplement_id'])
        if moment:
            db.execute('UPDATE health_units SET moment = ? WHERE id = ?', (moment, row['id']))


def _plans(db, day: str) -> dict[int, dict[str, float]]:
    """The plan of every supplement on a day: its amount at each moment, in the order of the day; a moment without an amount
    is not in it."""
    found: dict[int, dict[str, float]] = {}
    for supplement_id, moment, amount in db.execute(
            'SELECT supplement_id, moment, amount FROM health_plan p WHERE from_day = (SELECT MAX(from_day) FROM health_plan q '
            'WHERE q.supplement_id = p.supplement_id AND q.moment = p.moment AND q.from_day <= ?)', (day,)):
        if amount:
            found.setdefault(supplement_id, {})[moment] = amount
    return {key: dict(sorted(plan.items(), key=lambda m: MOMENTS.index(m[0]))) for key, plan in found.items()}


def day_view(day: date) -> dict:
    """The card of a day. The moments: whether something is planned, and the time it was taken (the first entry of the
    moment) once every supplement planned then has its planned amount. The supplements of the list that day, and a removed one with
    entries that day, in the order of the day (one without a plan last): the plan by moment, its total, the total taken and
    each entry (time, amount, moment)."""
    convert()
    key = day.isoformat()
    with connect() as db:
        plans = _plans(db, key)
        entries: dict[int, list[dict]] = {}
        for row in db.execute('SELECT id, supplement_id, taken_at, amount, moment FROM health_units WHERE day = ? '
                              'ORDER BY taken_at, id', (key,)):
            entries.setdefault(row['supplement_id'], []).append(
                {'id': row['id'], 'taken_at': row['taken_at'], 'amount': row['amount'], 'moment': row['moment']})
        rows = db.execute(f'SELECT id, name, dose_unit, step, comment, {LISTED} AS listed FROM health_supplement_list '
                          'ORDER BY position, id', {'day': key}).fetchall()
    items = []
    for row in rows:
        if not row['listed'] and row['id'] not in entries:
            continue
        plan = plans.get(row['id'], {}) if row['listed'] else {}
        mine = entries.get(row['id'], [])
        items.append({'id': row['id'], 'name': row['name'], 'dose_unit': row['dose_unit'], 'step': row['step'],
                      'comment': row['comment'], 'listed': bool(row['listed']), 'plan': plan, 'planned': sum(plan.values()),
                      'total': sum(e['amount'] for e in mine), 'entries': mine})
    items.sort(key=lambda s: min((MOMENTS.index(m) for m in s['plan']), default=len(MOMENTS)))
    moments = []
    for moment in MOMENTS:
        planned = [s for s in items if moment in s['plan']]
        tagged = [[e for e in s['entries'] if e['moment'] == moment] for s in planned]
        done = bool(planned) and all(sum(e['amount'] for e in mine) >= s['plan'][moment] - EPSILON
                                     for s, mine in zip(planned, tagged, strict=True))
        first = min((e['taken_at'] for mine in tagged for e in mine), default=None)
        moments.append({'name': moment, 'planned': bool(planned), 'taken_at': first if done else None})
    return {'moments': moments, 'items': items}


def changes() -> list[dict]:
    """Each change of a supplement's planned total a day, newest first: the day, and the amount a day before and from it (a
    start is 0 before, a stop 0 after; a supplement taken out of the list stops). A move to another moment is no change, and
    the plan up to the first day of the log is its start, no change."""
    convert()
    with connect() as db:
        start = db.execute('SELECT MIN(day) FROM health_units').fetchone()[0]
        rows = db.execute('SELECT id, name, dose_unit, removed FROM health_supplement_list ORDER BY position, id').fetchall()
        plan = db.execute('SELECT supplement_id, moment, from_day, amount FROM health_plan ORDER BY from_day').fetchall()

    def total(supplement_id: int, removed: str | None, day: str) -> float:
        """The planned amount of a supplement on a day."""
        if removed and day >= removed:
            return 0
        latest = {r['moment']: r['amount'] for r in plan if r['supplement_id'] == supplement_id and r['from_day'] <= day}
        return sum(latest.values())

    found = []
    for row in rows:
        days = {r['from_day'] for r in plan if r['supplement_id'] == row['id']} | ({row['removed']} if row['removed'] else set())
        for day in sorted(days):
            if start and day <= start:
                continue
            before = total(row['id'], row['removed'], (date.fromisoformat(day) - timedelta(days=1)).isoformat())
            after = total(row['id'], row['removed'], day)
            if before != after:
                found.append({'supplement_id': row['id'], 'name': row['name'], 'unit': row['dose_unit'], 'day': day,
                              'before': before, 'after': after})
    return sorted(found, key=lambda c: c['day'], reverse=True)


def regime(day: date) -> dict:
    """The list on a day, to change it: each supplement with its dose unit, one dose, its comment, the amount at each
    moment, and whether it has entries (then its unit stays); and every change of the plan."""
    convert()
    with connect() as db:
        plans = _plans(db, day.isoformat())
        used = {row[0] for row in db.execute('SELECT DISTINCT supplement_id FROM health_units')}
        rows = db.execute(f'SELECT id, name, dose_unit, step, comment FROM health_supplement_list WHERE {LISTED} '
                          'ORDER BY position, id', {'day': day.isoformat()}).fetchall()
    items = [dict(row) | {'plan': {m: plans.get(row['id'], {}).get(m, 0) for m in MOMENTS}, 'used': row['id'] in used}
             for row in rows]
    return {'moments': list(MOMENTS), 'items': items, 'changes': changes()}


def _checked(db, name: str, dose_unit: str, step: float | None, keeps: int | None = None) -> tuple[str, str, float | None]:
    """A name, a dose unit and one dose, checked; no other supplement in the list has the name (keeps: the one that may)."""
    name, dose_unit = name.strip(), dose_unit.strip()
    if not name:
        raise ValueError('Give the supplement a name.')
    if dose_unit and dose_unit not in DOSE_UNITS:
        raise ValueError(f'"{dose_unit}" is not a unit: choose mg, µg, g, IU or FU, or none for a yes or no of the day.')
    if dose_unit and not (step and step > 0):
        raise ValueError('Give the amount of one dose: more than 0.')
    if db.execute('SELECT 1 FROM health_supplement_list WHERE removed IS NULL AND lower(name) = lower(?) AND id IS NOT ?',
                  (name, keeps)).fetchone():
        raise ValueError(f'"{name}" is already in the list.')
    return name, dose_unit, step if dose_unit else None


def _comment(text: str) -> str:
    """A comment on how to take a supplement, checked."""
    text = text.strip()
    if len(text) > COMMENT_MAX:
        raise ValueError(f'A comment has at most {COMMENT_MAX} characters, not {len(text)}.')
    return text


def _listed(db, supplement_id: int, day: str):
    """The supplement in the list on a day; KeyError when it is not (any more)."""
    row = db.execute(f'SELECT name, dose_unit, step FROM health_supplement_list WHERE id = :id AND {LISTED}',
                     {'id': supplement_id, 'day': day}).fetchone()
    if row is None:
        raise KeyError(supplement_id)
    return row


def add_supplement(day: date, name: str, dose_unit: str, step: float | None, comment: str = '') -> int:
    """Add a supplement at the end of the list, in it from that day on, without a plan; its id."""
    convert()
    comment = _comment(comment)
    with connect(write=True) as db:
        name, dose_unit, step = _checked(db, name, dose_unit, step)
        return db.execute("INSERT INTO health_supplement_list (name, unit, dose_unit, step, comment, position, added) "
                          "SELECT ?, '', ?, ?, ?, COALESCE(MAX(position), 0) + 1, ? FROM health_supplement_list",
                          (name, dose_unit, step, comment, day.isoformat())).lastrowid


def change_supplement(supplement_id: int, day: date, name: str, dose_unit: str, step: float | None,
                      plan: dict[str, float], comment: str | None = None) -> None:
    """Change a supplement of the list: its name, dose unit, one dose and comment (None: it stays) for every day, and its plan
    (the amount at each moment; a moment not given is 0) from the day on, so the days before keep theirs. The unit of a
    supplement with entries stays: their amounts are in it."""
    key = day.isoformat()
    with connect(write=True) as db:
        old = _listed(db, supplement_id, key)
        name, dose_unit, step = _checked(db, name, dose_unit, step, supplement_id)
        for moment, amount in plan.items():
            if moment not in MOMENTS:
                raise ValueError(f'"{moment}" is not a moment of the day: choose {", ".join(MOMENTS)}.')
            if amount < 0:
                raise ValueError('An amount is 0 or more.')
            if not dose_unit and amount not in (0, 1):
                raise ValueError('A yes or no is planned as 1, or not at all.')
        if dose_unit != old['dose_unit'] and db.execute('SELECT 1 FROM health_units WHERE supplement_id = ?',
                                                        (supplement_id,)).fetchone():
            raise ValueError(f'{name} has entries in {old["dose_unit"] or "yes or no"}, so its unit stays. Add a new supplement '
                             'for another unit.')
        db.execute('UPDATE health_supplement_list SET name = ?, dose_unit = ?, step = ? WHERE id = ?',
                   (name, dose_unit, step, supplement_id))
        if comment is not None:
            db.execute('UPDATE health_supplement_list SET comment = ? WHERE id = ?', (_comment(comment), supplement_id))
        current = _plans(db, key).get(supplement_id, {})
        db.executemany(SAVE_PLAN, [(supplement_id, moment, key, float(plan.get(moment, 0))) for moment in MOMENTS
                                   if plan.get(moment, 0) != current.get(moment, 0)])


def remove_supplement(supplement_id: int, day: date) -> None:
    """Take a supplement out of the list from a day on, at once; its entries stay."""
    with connect(write=True) as db:
        _listed(db, supplement_id, day.isoformat())
        db.execute('UPDATE health_supplement_list SET removed = ? WHERE id = ?', (day.isoformat(), supplement_id))


def _filled(db, day: str, supplement_id: int) -> dict[str, float]:
    """The amount of a supplement taken for each moment of a day."""
    return {row[0]: row[1] for row in db.execute('SELECT moment, SUM(amount) FROM health_units WHERE day = ? AND supplement_id = ? '
                                                 'AND moment IS NOT NULL GROUP BY moment', (day, supplement_id))}


def _waiting(db, day: str, supplement_id: int) -> str | None:
    """The first moment of a supplement's plan on a day whose planned amount is not taken yet."""
    filled = _filled(db, day, supplement_id)
    return next((m for m, amount in _plans(db, day).get(supplement_id, {}).items() if filled.get(m, 0) < amount - EPSILON), None)


def add_intake(day: date, supplement_id: int, taken_at: str, amount: float | None = None) -> int:
    """Log an amount of a supplement taken on a day at a time (none: one dose); its id. The entry goes to the first moment
    of the plan whose amount is not taken yet, so that moment can show as taken. A yes or no is taken once a day."""
    taken_at = store.checked_time(taken_at)
    if amount is not None and amount <= 0:
        raise ValueError('An amount is more than 0.')
    key = day.isoformat()
    with connect(write=True) as db:
        found = _listed(db, supplement_id, key)
        if not found['dose_unit']:
            if db.execute('SELECT 1 FROM health_units WHERE day = ? AND supplement_id = ?', (key, supplement_id)).fetchone():
                raise ValueError(f'{found["name"]} is already taken on this day.')
            amount = 1
        return db.execute('INSERT INTO health_units (day, supplement_id, taken_at, amount, moment) VALUES (?, ?, ?, ?, ?)',
                          (key, supplement_id, taken_at, amount or found['step'], _waiting(db, key, supplement_id))).lastrowid


def take_moment(day: date, moment: str, taken_at: str) -> int:
    """Log what each supplement of a moment still needs of its planned amount; how many entries."""
    if moment not in MOMENTS:
        raise ValueError(f'"{moment}" is not a moment of the day.')
    taken_at = store.checked_time(taken_at)
    key = day.isoformat()
    with connect(write=True) as db:
        listed = {row[0] for row in db.execute(f'SELECT id FROM health_supplement_list WHERE {LISTED}', {'day': key})}
        planned = {sid: plan[moment] for sid, plan in _plans(db, key).items() if moment in plan and sid in listed}
        if not planned:
            raise ValueError(f'Nothing is planned at {moment.lower()}.')
        rest = {sid: amount - _filled(db, key, sid).get(moment, 0) for sid, amount in planned.items()}
        rows = [(key, sid, taken_at, amount, moment) for sid, amount in rest.items() if amount > EPSILON]
        if not rows:
            raise ValueError(f'{moment} is already taken on this day.')
        db.executemany('INSERT INTO health_units (day, supplement_id, taken_at, amount, moment) VALUES (?, ?, ?, ?, ?)', rows)
    return len(rows)


def copy_day(source: date, target: date) -> int:
    """Replace the entries of a day with copies of those of another day: each supplement, time, amount and moment, but not
    of a supplement that is not in the list on that day; how many entries were copied."""
    if source == target:
        raise ValueError('Choose two different days: the supplements of the first day are copied to the second.')
    with connect(write=True) as db:
        rows = db.execute('SELECT supplement_id, taken_at, amount, moment FROM health_units WHERE day = ? ORDER BY taken_at, id',
                          (source.isoformat(),)).fetchall()
        if not rows:
            raise ValueError(f'No supplements were logged on {source.day} {source:%B %Y}. Choose a day with entries to copy.')
        listed = {row[0] for row in db.execute(f'SELECT id FROM health_supplement_list WHERE {LISTED}', {'day': target.isoformat()})}
        copies = [(target.isoformat(), *row) for row in rows if row['supplement_id'] in listed]
        db.execute('DELETE FROM health_units WHERE day = ?', (target.isoformat(),))
        db.executemany('INSERT INTO health_units (day, supplement_id, taken_at, amount, moment) VALUES (?, ?, ?, ?, ?)', copies)
    return len(copies)


def undo_moment(day: date, moment: str) -> int:
    """Remove the entries of a moment of a day at once; how many."""
    with connect(write=True) as db:
        return db.execute('DELETE FROM health_units WHERE day = ? AND moment = ?', (day.isoformat(), moment)).rowcount


def set_amount(intake_id: int, amount: float) -> date:
    """Change the amount of an entry; the day it belongs to. A yes or no has no amount to change."""
    if amount <= 0:
        raise ValueError('An amount is more than 0.')
    with connect(write=True) as db:
        day = store.day_of(db, 'health_units', intake_id)
        unit = db.execute('SELECT l.dose_unit FROM health_units u JOIN health_supplement_list l ON l.id = u.supplement_id '
                          'WHERE u.id = ?', (intake_id,)).fetchone()[0]
        if not unit:
            raise ValueError('A yes or no has no amount: remove the entry instead.')
        db.execute('UPDATE health_units SET amount = ? WHERE id = ?', (amount, intake_id))
    return day


def remove_intake(intake_id: int) -> date:
    """Remove an entry at once; the day it belonged to."""
    with connect(write=True) as db:
        day = store.day_of(db, 'health_units', intake_id)
        db.execute('DELETE FROM health_units WHERE id = ?', (intake_id,))
    return day


def value_keys() -> list[dict]:
    """The history value of each supplement that was ever in the list: its key (supp:<id>), name and dose unit."""
    convert()
    with connect() as db:
        return [{'key': f'supp:{row[0]}', 'name': row[1], 'unit': row[2]}
                for row in db.execute('SELECT id, name, dose_unit FROM health_supplement_list ORDER BY position, id')]


def day_values(first: str, last: str) -> dict[str, dict[str, float]]:
    """The amount of each supplement taken on each day from `first` to `last` with an entry: 0 for a supplement without
    one. A day without any entry has no values: the log was not kept that day."""
    convert()
    with connect() as db:
        names = [f'supp:{row[0]}' for row in db.execute('SELECT id FROM health_supplement_list ORDER BY position, id')]
        found: dict[str, dict[str, float]] = {}
        for day, supplement_id, amount in db.execute('SELECT day, supplement_id, SUM(amount) FROM health_units WHERE day >= ? '
                                                     'AND day <= ? GROUP BY day, supplement_id', (first, last)):
            found.setdefault(day, dict.fromkeys(names, 0))[f'supp:{supplement_id}'] = amount
    return found
