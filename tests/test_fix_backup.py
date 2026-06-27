"""Auto-backup safety net: shared tar routine + timestamped snapshots + prune."""

from __future__ import annotations

import tarfile

from iron_jarvis.maintenance import (
    BACKUP_DIRNAME,
    create_backup,
    prune_backups,
    run_auto_backup,
)


def test_create_backup_excludes_keys(platform, tmp_path):
    home = platform.config.home
    (home / "secrets").mkdir(parents=True, exist_ok=True)
    (home / "secrets" / ".secrets.key").write_text("KEY")
    (home / "data.txt").write_text("hello")

    out = tmp_path / "b.tar.gz"
    _, n = create_backup(home, out, engine=platform.engine, include_keys=False)
    names = tarfile.open(out).getnames()
    assert any(x.endswith("data.txt") for x in names)
    assert not any(".secrets.key" in x for x in names)  # key excluded by default
    assert n >= 1

    out2 = tmp_path / "b2.tar.gz"
    create_backup(home, out2, include_keys=True)
    assert any(".secrets.key" in x for x in tarfile.open(out2).getnames())


def test_run_auto_backup_writes_and_self_excludes(platform):
    home = platform.config.home
    (home / "data.txt").write_text("hi")
    p = run_auto_backup(home, engine=platform.engine, keep=3)
    assert p.exists() and p.parent.name == BACKUP_DIRNAME
    # the backups dir is never archived into a backup (no nesting/growth)
    assert not any(f"/{BACKUP_DIRNAME}/" in x for x in tarfile.open(p).getnames())


def test_prune_backups_keeps_newest(tmp_path):
    d = tmp_path / "backups"
    d.mkdir()
    for i in range(5):
        (d / f"ironjarvis-backup-2026010{i}-000000.tar.gz").write_text("x")
    removed = prune_backups(d, keep=2)
    assert removed == 3
    assert len(list(d.glob("ironjarvis-backup-*.tar.gz"))) == 2
