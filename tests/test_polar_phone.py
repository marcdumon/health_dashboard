"""The move of the phone app's files from the phone (WiFi FTP Server) to the PC on Import from phone and PC: only the new
and changed files of the phone app are copied, under a temporary name until complete; then the phone app's files that the PC
has with the same size are deleted on the phone, but not those of a recording that may still run, nor any file when the phone
gives no file times; a phone that does not answer leaves the import of the files on the PC. The import at the end of a
recording on the PC never reaches the phone."""

from datetime import UTC, datetime, timedelta
from ftplib import error_perm
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from health_dashboard import api, polar, polar_phone
from tests.polar_fixtures import phone_markers, phone_session

NOW = datetime(2026, 7, 2, 12, 0).astimezone()
PHONE = polar_phone.Address('192.168.1.5', 2221, 'md', 'secret', '/Documents/Polar_hr_data')
# the real function: every test runs with no phone set (conftest)
address = polar_phone.address
# when the phone app last wrote a file, unless a test says otherwise: long ago
WRITTEN = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)


class Phone:
    """The phone's FTP folder: its files with the times they were last written, and what a test makes go wrong (a refused
    connection, a wrong password, a transfer that breaks off, no file times, refused deletes, a file that grows after its
    copy); every file sent and deleted is recorded."""

    def __init__(self, files: dict[str, bytes]) -> None:
        """The files of the folder."""
        self.files, self.sent, self.deleted = dict(files), [], []
        self.times = dict.fromkeys(files, WRITTEN)
        self.refuse = self.wrong_password = self.no_times = self.refuse_delete = False
        self.break_off: str | None = None
        self.grows: str | None = None

    def ftp(self) -> object:
        """A stand-in for ftplib.FTP on this phone."""
        phone = self

        class FakeFTP:
            """The commands the copy uses."""

            def __enter__(self) -> 'FakeFTP':
                return self

            def __exit__(self, *exc: object) -> None:
                return None

            def connect(self, host: str, port: int, timeout: float) -> None:
                if phone.refuse or (host, port) != (PHONE.host, PHONE.port):
                    raise TimeoutError('timed out')

            def login(self, user: str, passwd: str) -> None:
                if phone.wrong_password or (user, passwd) != (PHONE.user, PHONE.password):
                    raise error_perm('530 Login incorrect.')

            def cwd(self, path: str) -> None:
                if path != PHONE.folder:
                    raise error_perm('550 No such directory.')

            def voidcmd(self, command: str) -> None:
                return None

            def nlst(self) -> list[str]:
                return list(phone.files)

            def size(self, name: str) -> int:
                # the file that grows is longer once it has been sent
                return len(phone.files[name]) + (100 if name == phone.grows and name in phone.sent else 0)

            def sendcmd(self, command: str) -> str:
                assert command.startswith('MDTM ')
                if phone.no_times:
                    raise error_perm('500 Unknown command.')
                return f"213 {phone.times[command.removeprefix('MDTM ')]:%Y%m%d%H%M%S}"

            def delete(self, name: str) -> None:
                if phone.refuse_delete:
                    raise error_perm('550 Permission denied.')
                del phone.files[name]
                phone.deleted.append(name)

            def retrbinary(self, command: str, callback) -> None:
                name = command.removeprefix('RETR ')
                data = phone.files[name]
                for start in range(0, len(data), 1000):
                    if name == phone.break_off and start > 0:
                        raise TimeoutError('timed out')
                    callback(data[start:start + 1000])
                phone.sent.append(name)

        return FakeFTP


@pytest.fixture
def made(tmp_path) -> dict[str, bytes]:
    """The files of the phone app for one session of 15 minutes on 1 July with a marker file, as the phone holds them, with
    the files of the phone that are not the app's (its logs, a file in the bin)."""
    folder = tmp_path / 'made'
    folder.mkdir()
    phone_session(folder, datetime(2026, 7, 1, 10, 0), 900)
    phone_markers(folder, [(datetime(2026, 7, 1, 10, 1), True), (datetime(2026, 7, 1, 10, 5), False)])
    files = {p.name: p.read_bytes() for p in folder.iterdir()}
    return files | {'syslog.txt': b'log', '.trashed-1794241797-Polar_H10_E813952E_20261010_182746_HR.txt': b'old'}


@pytest.fixture
def phone(made, monkeypatch) -> Phone:
    """The phone with these files, set as the place of the phone app's files."""
    found = Phone(made)
    monkeypatch.setattr(polar_phone, 'FTP', found.ftp())
    monkeypatch.setattr(polar_phone, 'address', lambda: PHONE)
    return found


@pytest.fixture
def client(tmp_path, phone, monkeypatch) -> TestClient:
    """A client at noon on 2 July with an empty phone folder on the PC; work in the background runs at once."""
    folder, logger = tmp_path / 'phone', tmp_path / 'logger'
    folder.mkdir()
    logger.mkdir()
    monkeypatch.setattr(api, '_health_now', lambda: NOW)
    monkeypatch.setattr(api, '_in_background', lambda work, *args: work(*args))
    monkeypatch.setattr(polar, 'folders', lambda: (folder, logger))
    return TestClient(api.app)


def app_files(made: dict[str, bytes]) -> set[str]:
    """The names of the phone app's own files."""
    return {name for name in made if name.startswith(('Polar_H10_', 'MARKER_'))}


def move(folder, step=lambda done, total: None) -> polar_phone.Moved:
    """Move the phone app's files from the phone to `folder`."""
    return polar_phone.move_new(PHONE, folder, polar.phone_start, step)


def test_the_import_from_phone_and_pc_moves_the_files_of_the_phone_app_first(client, phone, made, tmp_path) -> None:
    view = client.post('/api/health/polar/import?day=2026-07-02', json={}).json()['polar']
    assert (view['sessions'], view['error'], view['phase'], view['button']) == (1, '', 'read', 'Import from phone and PC')
    n = len(app_files(made))
    assert view['last_note'] == f'from the phone and the PC: {n} files copied to the PC, {n} files deleted on the phone'
    folder = tmp_path / 'phone'
    assert {p.name for p in folder.iterdir()} == app_files(made), 'not the logs, not the bin, no temporary file'
    assert all((folder / name).read_bytes() == made[name] for name in app_files(made))
    assert set(phone.sent) == set(phone.deleted) == app_files(made)
    assert set(phone.files) == set(made) - app_files(made), 'the logs and the bin of the phone stay'


def test_a_file_the_pc_has_is_not_copied_again_and_a_changed_one_is(phone, made, tmp_path) -> None:
    folder = tmp_path / 'phone'
    folder.mkdir()
    hr, rr = sorted(n for n in app_files(made) if n.endswith(('_HR.txt', '_RR.txt')))
    (folder / hr).write_bytes(made[hr])
    (folder / rr).write_bytes(made[rr][:100])
    steps = []
    moved = move(folder, lambda done, total: steps.append((done, total)))
    n = len(app_files(made))
    assert (moved.copied, moved.deleted, moved.kept) == (n - 1, n, 0), 'the file the PC has is deleted on the phone too'
    assert (hr in phone.sent, (folder / rr).read_bytes() == made[rr]) == (False, True)
    assert steps[0] == (0, n - 1) and steps[-1] == (n - 1, n - 1)
    assert move(folder).note() == 'no files on the phone'


def test_a_transfer_that_breaks_off_leaves_no_half_file_and_deletes_nothing(phone, made, tmp_path) -> None:
    folder = tmp_path / 'phone'
    folder.mkdir()
    phone.break_off = next(n for n in sorted(app_files(made)) if n.endswith('_HR.txt'))
    with pytest.raises(polar_phone.PhoneError, match='press Import from phone and PC again'):
        move(folder)
    assert not (folder / phone.break_off).exists() and not list(folder.glob('.*'))
    assert phone.deleted == []


def test_the_files_of_a_recording_that_may_still_run_stay_on_the_phone_with_its_marker(phone, made, tmp_path) -> None:
    older = tmp_path / 'older'
    older.mkdir()
    phone_session(older, datetime(2026, 6, 30, 9, 0), 600)
    phone.files |= {p.name: p.read_bytes() for p in older.iterdir()}
    phone.times |= {p.name: WRITTEN for p in older.iterdir()}
    # the HR file of the session of 1 July was written 5 minutes ago: its recording may still run
    hr = next(n for n in app_files(made) if n.endswith('_HR.txt'))
    phone.times[hr] = datetime.now(UTC) - timedelta(minutes=5)
    folder = tmp_path / 'phone'
    folder.mkdir()
    moved = move(folder)
    assert set(phone.deleted) == {p.name for p in older.iterdir()}
    assert app_files(made) <= set(phone.files), 'all its files and its marker file stay'
    assert (moved.copied, moved.kept) == (len(app_files(made)) + len(list(older.iterdir())), len(app_files(made)))
    assert moved.note().endswith(f'{len(app_files(made))} files left on the phone (a recording may still run)')


def test_a_file_that_grew_after_its_copy_stays_on_the_phone(phone, made, tmp_path) -> None:
    phone.grows = next(n for n in app_files(made) if n.endswith('_HR.txt'))
    folder = tmp_path / 'phone'
    folder.mkdir()
    moved = move(folder)
    assert set(phone.deleted) == app_files(made) - {phone.grows} and moved.kept == 1


def test_a_phone_without_file_times_keeps_all_its_files(phone, made, tmp_path) -> None:
    phone.no_times = True
    folder = tmp_path / 'phone'
    folder.mkdir()
    moved = move(folder)
    assert (phone.deleted, moved.copied, moved.kept) == ([], len(app_files(made)), len(app_files(made)))
    assert moved.problem.startswith('The phone gives no file times (500 Unknown command.)')


def test_a_refused_delete_gives_a_message_and_the_import_goes_on(client, phone, made) -> None:
    phone.refuse_delete = True
    view = client.post('/api/health/polar/import?day=2026-07-02', json={}).json()['polar']
    assert view['sessions'] == 1 and set(phone.files) == set(made)
    assert view['error'].startswith(f'The phone did not let the app delete {len(app_files(made))} files (550 Permission denied.)')


def test_a_phone_that_does_not_answer_leaves_the_import_of_the_files_on_the_pc_and_no_import_error(client, phone,
                                                                                                    tmp_path) -> None:
    phone_session(tmp_path / 'phone', datetime(2026, 7, 1, 18, 0), 600)
    phone.refuse = True
    view = client.post('/api/health/polar/import?day=2026-07-02', json={}).json()['polar']
    assert view['sessions'] == 1 and view['last_import'] and view['error'] == '', 'a phone that is off is no import error'
    assert view['last_note'] == 'from the PC only: no files moved from the phone'
    # what to do, for the page that pressed the button
    assert view['phone_error'].startswith('The phone does not answer at 192.168.1.5:2221. On the phone, open WiFi FTP Server')
    phone.refuse, phone.wrong_password = False, True
    refused = client.post('/api/health/polar/import?day=2026-07-02', json={}).json()['polar']['phone_error']
    assert 'POLAR_PHONE_FTP_PASSWORD' in refused
    phone.wrong_password = False
    assert client.post('/api/health/polar/import?day=2026-07-02', json={}).json()['polar']['phone_error'] == ''


def test_the_place_on_the_phone_comes_from_the_config_and_the_password_from_env(monkeypatch, tmp_path) -> None:
    settings = SimpleNamespace(polar=SimpleNamespace(phone_ftp='ftp://user@192.168.1.20:2221/Documents/Polar_hr_data/'))
    monkeypatch.setattr(polar_phone, 'config', lambda: settings)
    monkeypatch.setattr(polar_phone, 'APP_DIR', tmp_path)
    (tmp_path / '.env').write_text('POLAR_PHONE_FTP_PASSWORD=0000\n')
    assert address() == polar_phone.Address('192.168.1.20', 2221, 'user', '0000', '/Documents/Polar_hr_data/')
    settings.polar.phone_ftp = ''
    assert address() is None
