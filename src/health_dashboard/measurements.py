"""The Withings measurements of one day, each apart: a weigh-in (the scale sends it as several groups in the same minute)
and each blood pressure reading. A wrong one can be removed: the app hides its groups from the history and the latest
values, also after later fetches, and can put it back. Nothing is deleted at Withings."""

from datetime import date, datetime, timedelta

from health_dashboard import ledger, store, withings


def _merge(rows: list) -> list[dict]:
    """Groups of whole-body values as measurements, newest first: each blood pressure reading apart, the scale's groups of
    one minute as one weigh-in (its pulse named heart_rate, as in the history)."""
    groups: dict[int, dict] = {}
    for grpid, kind, value, measured_at in rows:
        groups.setdefault(grpid, {'measured_at': measured_at, 'values': {}})['values'][withings.TYPES[kind]] = value
    found: dict[tuple[str, str], dict] = {}
    for grpid, group in sorted(groups.items()):
        pressure = 'systolic' in group['values']
        key = ('pressure', str(grpid)) if pressure else ('scale', group['measured_at'])
        item = found.setdefault(key, {'kind': key[0], 'measured_at': group['measured_at'], 'grpids': [], 'values': {}})
        item['grpids'].append(grpid)
        for name, value in group['values'].items():
            item['values'][name if pressure or name != 'pulse' else 'heart_rate'] = value
    return sorted(found.values(), key=lambda m: (m['measured_at'], m['kind']), reverse=True)


def _rows(where: str, params: tuple) -> list:
    """Whole-body values of the groups that match."""
    with ledger.connect() as db:
        return db.execute(f'SELECT grpid, type, value, measured_at FROM withings_values WHERE position = ? AND {where} '
                          'ORDER BY grpid', (withings.NO_PART, *params)).fetchall()


def day(chosen: date) -> list[dict]:
    """The measurements of a day (from the start of the day to the start of the next), without the removed ones."""
    start = f'T{store.DAY_START_HOUR:02d}:00'
    after = chosen + timedelta(days=1)
    return _merge(_rows(f'measured_at >= ? AND measured_at < ? AND {withings.NOT_HIDDEN}',
                        (f'{chosen.isoformat()}{start}', f'{after.isoformat()}{start}')))


def removed() -> list[dict]:
    """The measurements the user removed, newest first, each with its day."""
    found = _merge(_rows('grpid IN (SELECT grpid FROM withings_hidden)', ()))
    return [m | {'day': store.health_day(datetime.fromisoformat(m['measured_at'])).isoformat()} for m in found]


def hide(grpids: list[int]) -> None:
    """Remove a measurement: its groups leave the history and the latest values."""
    now = datetime.now().astimezone().isoformat(timespec='seconds')
    with ledger.connect(write=True) as db:
        db.executemany('INSERT OR IGNORE INTO withings_hidden (grpid, hidden_at) VALUES (?, ?)', [(g, now) for g in grpids])


def show(grpids: list[int]) -> None:
    """Put a removed measurement back."""
    with ledger.connect(write=True) as db:
        db.executemany('DELETE FROM withings_hidden WHERE grpid = ?', [(g,) for g in grpids])
