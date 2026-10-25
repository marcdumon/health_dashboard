"""The files of the phone app (Polar Sensor Logger), moved from the phone into the phone folder (phone_dir) on Import from
phone and PC, before the import reads that folder. The phone runs WiFi FTP Server; phone_ftp in health_dashboard.toml is its
folder of the app's files, and POLAR_PHONE_FTP_PASSWORD in .env its password. A file is copied when the folder lacks it or
has it with another size (a recording that went on). It comes in under a temporary name and gets its own name only when
complete, so the import never reads half a file. Once every copy is complete, the app's files that the folder has with the
size they have on the phone at that moment are deleted on the phone, except the files of a recording that may still run:
a file written in the last 15 minutes, and every file whose name starts at or after the start of its recording (its marker
file too). The phone's file times are read as UTC; a phone that gives its local time instead makes the files look newer,
which east of UTC only keeps more of them. A phone that gives no file times keeps all its files. Other files on the phone
are never touched."""

import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from ftplib import FTP, all_errors, error_perm
from pathlib import Path
from urllib.parse import unquote, urlsplit

from dotenv import dotenv_values

from health_dashboard.config import APP_DIR, config

PASSWORD_KEY = 'POLAR_PHONE_FTP_PASSWORD'
TIMEOUT_S = 15
START = 'On the phone, open WiFi FTP Server and press Start.'
# a file written in this time may belong to a recording that still runs; the files of a recording start within 60 s
RUNNING = timedelta(minutes=15)
SAME_RECORDING = timedelta(seconds=60)


@dataclass(frozen=True)
class Address:
    """The FTP folder of the phone app's files: host, port, user, password and folder."""

    host: str
    port: int
    user: str
    password: str
    folder: str


class PhoneError(RuntimeError):
    """The phone cannot be reached or read; the message says what to do."""


def _files(n: int) -> str:
    """A number of files, in words."""
    return f'{n} {"file" if n == 1 else "files"}'


@dataclass(frozen=True)
class Moved:
    """What a move from the phone did: the files copied to the PC, the files deleted on the phone, the app's files left on
    it, whether some of those are left because a recording may still run, and why files that the PC has were not deleted
    ('' when nothing went wrong)."""

    copied: int
    deleted: int
    kept: int
    running: bool = False
    problem: str = ''

    def note(self) -> str:
        """The move in words, for the line of the last import."""
        parts = [f'{_files(self.copied)} copied to the PC' if self.copied else '',
                 f'{_files(self.deleted)} deleted on the phone' if self.deleted else '',
                 f'{_files(self.kept)} left on the phone{" (a recording may still run)" if self.running else ""}' if self.kept
                 else '']
        return ', '.join(filter(None, parts)) or 'no files on the phone'


def address() -> Address | None:
    """The folder of phone_ftp in the [polar] part of health_dashboard.toml (ftp://user@host:port/folder/), with the password
    of .env, read at each call; None when not set."""
    url = config().polar.phone_ftp
    if not url:
        return None
    parts = urlsplit(url)
    password = dotenv_values(APP_DIR / '.env').get(PASSWORD_KEY) or os.environ.get(PASSWORD_KEY, '')
    return Address(parts.hostname or '', parts.port or 21, unquote(parts.username or 'anonymous'), password,
                   unquote(parts.path) or '/')


def _connect(ftp: FTP, found: Address) -> None:
    """Log in and open the folder; PhoneError with what to do."""
    try:
        ftp.connect(found.host, found.port, timeout=TIMEOUT_S)
    except OSError as error:
        raise PhoneError(f'The phone does not answer at {found.host}:{found.port}. {START} If it shows another address, set '
                         'that address in phone_ftp in the [polar] part of health_dashboard.toml, and restart the app.') from error
    try:
        ftp.login(found.user, found.password)
    except error_perm as error:
        raise PhoneError(f'The phone refused the login of {found.user} ({error}). Write the password of WiFi FTP Server in the '
                         f'.env file in the app folder, as {PASSWORD_KEY}=…') from error
    try:
        ftp.cwd(found.folder)
    except error_perm as error:
        raise PhoneError(f'The phone has no folder {found.folder} ({error}). Set the folder of the Polar Sensor Logger files '
                         'in phone_ftp in the [polar] part of health_dashboard.toml, and restart the app.') from error


def move_new(found: Address, folder: Path, started: Callable[[str], datetime | None],
             step: Callable[[int, int], None]) -> Moved:
    """Copy the phone app's files of the phone folder (those that `started` gives the start in their name of) that
    `folder` lacks or has with another size, then delete on the phone those that `folder` has (see the module). `step(done,
    total)` follows the copy, file by file. A copy that breaks off leaves no file and deletes nothing: PhoneError."""
    part: Path | None = None
    try:
        with FTP() as ftp:
            _connect(ftp, found)
            # sizes in bytes, as the files are copied
            ftp.voidcmd('TYPE I')
            sizes = {name: ftp.size(name) for name in ftp.nlst() if started(name) is not None}
            todo = [name for name, size in sorted(sizes.items())
                    if not (folder / name).is_file() or (size is not None and (folder / name).stat().st_size != size)]
            for done, name in enumerate(todo):
                step(done, len(todo))
                part = folder / f'.{name}.part'
                with part.open('wb') as out:
                    ftp.retrbinary(f'RETR {name}', out.write)
                part.replace(folder / name)
                part = None
            step(len(todo), len(todo))
            return _delete_copied(ftp, folder, started, sorted(sizes), len(todo))
    except PhoneError:
        raise
    except all_errors as error:
        raise PhoneError(f'The move from the phone stopped ({error}). {START} Then press Import from phone and PC '
                         'again.') from error
    finally:
        if part is not None:
            part.unlink(missing_ok=True)


def _written(ftp: FTP, name: str) -> datetime:
    """When the phone last wrote a file (MDTM), read as UTC; error_perm when the phone gives no file times."""
    return datetime.strptime(ftp.sendcmd(f'MDTM {name}')[4:18], '%Y%m%d%H%M%S').replace(tzinfo=UTC)


def _delete_copied(ftp: FTP, folder: Path, started: Callable[[str], datetime | None], names: list[str], copied: int) -> Moved:
    """Delete on the phone the app's files that `folder` has with their size on the phone now, except those of a
    recording that may still run (see the module)."""
    try:
        written = {name: _written(ftp, name) for name in names}
    except error_perm as error:
        return Moved(copied, 0, len(names), problem=f'The phone gives no file times ({error}), so the app cannot see whether '
                                                    'a recording on it still runs, and it deleted no file on the phone.')
    recent = [name for name in names if written[name] > datetime.now(UTC) - RUNNING]
    # the earliest start of a recording that may still run: its files and the files after them stay
    since = min((started(name) for name in recent), default=None)
    stay = {name for name in names if since is not None and started(name) >= since - SAME_RECORDING}
    deleted, refused, growing = 0, [], False
    for name in sorted(set(names) - stay):
        local = folder / name
        try:
            # a file that grew since its copy is still written: it stays
            if local.is_file() and local.stat().st_size == ftp.size(name):
                ftp.delete(name)
                deleted += 1
            else:
                growing = True
        except error_perm as error:
            refused.append(str(error))
    problem = (f'The phone did not let the app delete {_files(len(refused))} ({refused[0]}). The files are on the PC; the next '
               'import tries again.') if refused else ''
    return Moved(copied, deleted, len(names) - deleted, running=bool(recent) or growing, problem=problem)
