"""North-star plan 4.10: backups carry the runs' own folders, bounded.

Backups skipped ``<home>/workspaces/`` whole ("disposable session scratch"),
but that is where a run with no folder chosen puts the files it produces, so
those files were in no backup. Now they are carried, newest first, up to
``WORKSPACE_BACKUP_MAX_BYTES`` in all, leaving out any single file over
``WORKSPACE_BACKUP_MAX_FILE`` and anything inside a ``.git`` folder.
"""

from __future__ import annotations

import os
import tarfile
import time
from pathlib import Path

from iron_jarvis import maintenance
from iron_jarvis.maintenance import create_backup


def _file(path: Path, size: int, age_s: float = 0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"w" * size)
    t = time.time() - age_s
    os.utime(path, (t, t))
    return path


def _names(archive: Path) -> list[str]:
    with tarfile.open(archive) as tar:
        return [n.replace("\\", "/") for n in tar.getnames()]


def test_a_runs_own_files_are_in_the_backup(platform, tmp_path):
    home = platform.config.home
    _file(home / "workspaces" / "session_1" / "summary.docx", 2000)

    out, _n = create_backup(home, tmp_path / "b.tar.gz", engine=platform.engine, include_keys=True)

    assert any(n.endswith("/workspaces/session_1/summary.docx") for n in _names(out))


def test_the_runs_folders_are_bounded_newest_first(platform, tmp_path):
    home = platform.config.home
    ws = home / "workspaces"
    _file(ws / "session_new" / "new.bin", 600, age_s=10)
    _file(ws / "session_mid" / "mid.bin", 600, age_s=100)
    _file(ws / "session_old" / "old.bin", 600, age_s=1000)
    _file(ws / "session_big" / "huge.bin", 5000, age_s=1)
    _file(ws / "session_git" / ".git" / "objects" / "pack.bin", 10, age_s=1)
    _file(ws / "code-index" / "s1.json", 10, age_s=1)  # a cache, not a run's folder

    out, _n = create_backup(
        home, tmp_path / "b.tar.gz", engine=platform.engine, include_keys=True,
        workspace_max_bytes=1300, workspace_max_file=1000,
    )

    names = " ".join(_names(out))
    assert "session_new/new.bin" in names and "session_mid/mid.bin" in names
    assert "session_old/old.bin" not in names  # past the total: the oldest is left out
    assert "huge.bin" not in names  # one file over the per-file limit
    assert "/.git/" not in names
    assert "code-index" not in names


def test_the_limits_are_the_ones_the_settings_card_states():
    assert maintenance.WORKSPACE_BACKUP_MAX_BYTES == 100 * 1024 * 1024
    assert maintenance.WORKSPACE_BACKUP_MAX_FILE == 25 * 1024 * 1024
    card = (Path(__file__).resolve().parents[1] / "dashboard" / "components" / "settings" / "MaintenanceCard.tsx").read_text(encoding="utf-8")
    assert "100 MB" in card and "25 MB" in card
