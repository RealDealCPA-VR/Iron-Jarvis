"""v1.337.1 — a backup taken while the daemon holds its home lock.

v1.335.0 made ``serve`` hold a byte lock on ``<home>/daemon.lock`` for the
life of the process. ``create_backup`` archived every file in the home, and on
Windows a read of a locked byte fails, so EVERY automatic backup on every
install failed with a bare "PermissionError: [Errno 13] Permission denied"
from the day the update landed. The lock and its owner note stay out of the
archive now, and a file that cannot be read is named in the error.
"""

from __future__ import annotations

import tarfile
from pathlib import Path

import pytest

from iron_jarvis import maintenance
from iron_jarvis.core import home_lock


@pytest.fixture
def held_home(tmp_path: Path):
    """A home whose lock is held IN THIS PROCESS, exactly as the daemon holds it."""
    home = tmp_path / ".ironjarvis"
    home.mkdir()
    (home / "config.toml").write_text("default_provider = 'mock'\n", encoding="utf-8")
    (home / "memory").mkdir()
    (home / "memory" / "note.md").write_text("kept\n", encoding="utf-8")
    before = home_lock._HELD
    fh = home_lock.acquire(home, port=8797, wait_s=0)
    try:
        yield home
    finally:
        fh.close()
        home_lock._HELD = before


def _names(archive: Path) -> set[str]:
    with tarfile.open(archive, "r:gz") as tar:
        return {Path(m.name).name for m in tar.getmembers()}


def test_backup_succeeds_while_the_home_lock_is_held(held_home: Path) -> None:
    out, n = maintenance.create_backup(held_home, held_home / "backups" / "b.tar.gz")
    names = _names(out)
    assert {"config.toml", "note.md"} <= names
    assert home_lock.LOCK_NAME not in names
    assert home_lock.OWNER_NAME not in names
    assert n == len(names)


def test_the_automatic_backup_succeeds_while_the_home_lock_is_held(held_home: Path) -> None:
    out = maintenance.run_auto_backup(held_home, keep=7)
    assert out.exists()
    assert home_lock.LOCK_NAME not in _names(out)


def test_an_unreadable_file_is_named_in_the_error(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / ".ironjarvis"
    home.mkdir()
    (home / "config.toml").write_text("x = 1\n", encoding="utf-8")
    (home / "stuck.bin").write_bytes(b"\0")
    real_add = tarfile.TarFile.add

    def add(self, name, arcname=None, *a, **kw):
        if Path(name).name == "stuck.bin":
            raise PermissionError(13, "Permission denied")
        return real_add(self, name, arcname, *a, **kw)

    monkeypatch.setattr(tarfile.TarFile, "add", add)
    with pytest.raises(PermissionError) as info:
        maintenance.create_backup(home, home / "backups" / "b.tar.gz")
    assert "stuck.bin" in str(info.value)
    assert info.value.errno == 13
    assert not (home / "backups" / "b.tar.gz").exists()
    assert not (home / "backups" / "b.tar.gz.tmp").exists()
