"""One-time move of the Health data from the Automation desk into this app.

It copies:
- the Health tables of automation.db (opened read-only; SQLite's backup gives a consistent copy, then the other tables go);
- data/polar.db (the raw Polar samples), data/google_health/ (the Fitbit files) and data/polar_record.log;
- secrets/google_health_token.json and secrets/client_secret.json, person.toml, and the Withings and phone FTP lines of
  .env (never printed).

Stop the Automation desk first, so nothing changes during the copy. It runs once: when data/health.db exists, it stops
and changes nothing (a second run would put back the old data). An existing data/polar.db of this app is kept in
data/backups/ first. The Automation desk is never written to.

Usage: uv run python scripts/move_from_automation_desk.py /path/to/automation_desk
"""

import argparse
import re
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from health_dashboard import ledger, polar_store
from health_dashboard.config import ROOT

ENV_KEYS = ('WITHINGS_CLIENT_ID', 'WITHINGS_CLIENT_SECRET', 'WITHINGS_CALLBACK_URL', 'POLAR_PHONE_FTP_PASSWORD')
FILES = ('secrets/google_health_token.json', 'secrets/client_secret.json', 'person.toml', 'data/polar_record.log')


def _keep_old(path: Path, stamp: str) -> None:
    """Move a file of this app aside to data/backups/ before it is replaced."""
    if path.exists():
        backups = ROOT / 'data' / 'backups'
        backups.mkdir(parents=True, exist_ok=True)
        path.replace(backups / f'{path.stem}-before-move-{stamp}{path.suffix}')
        for extra in ('-wal', '-shm'):
            path.with_name(path.name + extra).unlink(missing_ok=True)


def _copy_database(source: Path, target: Path) -> sqlite3.Connection:
    """A consistent copy of a database that another program may hold open; the copy, opened."""
    reader = sqlite3.connect(f'file:{source}?mode=ro', uri=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    writer = sqlite3.connect(target)
    try:
        reader.backup(writer)
    finally:
        reader.close()
    return writer


def tables(path: Path) -> set[str]:
    """The tables of a database, read-only."""
    db = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    try:
        return {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")}
    finally:
        db.close()


def counts(path: Path, names: set[str]) -> dict[str, int]:
    """The rows of each table, read-only."""
    db = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    try:
        return {name: db.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0] for name in sorted(names)}
    finally:
        db.close()


def report(prefix: str, before: dict[str, int], after: dict[str, int]) -> None:
    """The rows of each table in the source and in the copy."""
    for name in sorted(before):
        same = before[name] == after.get(name)
        print(f'{prefix + name:28} {before[name]:>9} {after.get(name, 0):>9}{"" if same else "  DIFFERENT"}')


def main() -> int:
    """Copy everything, then compare the rows of each table with the source."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('automation_desk', type=Path, help='the folder of the Automation desk')
    source = parser.parse_args().automation_desk.resolve()
    stamp = datetime.now().strftime('%Y-%m-%d-%H%M%S')
    health_tables = set(re.findall(r'CREATE TABLE IF NOT EXISTS (\w+)', ledger.SCHEMA))

    if ledger.DB.exists():
        print(f'{ledger.DB} exists: the move ran before, and this app has its own data now. Nothing was changed.')
        return 1
    db = _copy_database(source / 'data' / 'automation.db', ledger.DB)
    try:
        names = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")]
        for name in names:
            if name not in health_tables:
                db.execute(f'DROP TABLE "{name}"')
        if db.execute("SELECT 1 FROM sqlite_master WHERE name = 'sqlite_sequence'").fetchone():
            db.execute(f'DELETE FROM sqlite_sequence WHERE name NOT IN ({", ".join("?" * len(health_tables))})', sorted(health_tables))
        db.commit()
        db.execute('VACUUM')
    finally:
        db.close()

    _keep_old(polar_store.RAW_DB, stamp)
    _copy_database(source / 'data' / 'polar.db', polar_store.RAW_DB).close()
    if (source / 'data' / 'google_health').is_dir():
        shutil.copytree(source / 'data' / 'google_health', ROOT / 'data' / 'google_health', dirs_exist_ok=True)
    for name in FILES:
        if (source / name).is_file():
            (ROOT / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source / name, ROOT / name)
    env = ROOT / '.env'
    have = env.read_text() if env.is_file() else ''
    lines = [line for line in (source / '.env').read_text().splitlines()
             if line.split('=', 1)[0] in ENV_KEYS and not re.search(rf'^{re.escape(line.split("=", 1)[0])}=', have, re.M)]
    if lines:
        env.write_text(have + ('' if not have or have.endswith('\n') else '\n') + '\n'.join(lines) + '\n')
    if env.is_file():
        env.chmod(0o600)

    copied = health_tables & tables(ledger.DB)
    before, after = counts(source / 'data' / 'automation.db', copied), counts(ledger.DB, copied)
    report('', before, after)
    raw = tables(source / 'data' / 'polar.db')
    raw_before, raw_after = counts(source / 'data' / 'polar.db', raw), counts(polar_store.RAW_DB, raw)
    report('polar.db ', raw_before, raw_after)
    print(f'.env lines added: {len(lines)}; files copied: {sum((source / n).is_file() for n in FILES)}')
    return 0 if before == after and raw_before == raw_after else 1


if __name__ == '__main__':
    sys.exit(main())
