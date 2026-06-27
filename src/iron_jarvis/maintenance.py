"""Backup helpers: one shared tar routine + a scheduled auto-backup safety net.

A daily driver that runs for weeks needs a backup it never has to remember to
take. :func:`create_backup` is the shared archive routine (used by the
``ironjarvis backup`` CLI and the daemon's periodic loop); :func:`run_auto_backup`
writes a timestamped snapshot under ``<home>/backups`` and prunes old ones.
"""

from __future__ import annotations

import tarfile
from pathlib import Path

from .core.ids import utcnow

BACKUP_DIRNAME = "backups"


def _checkpoint_wal(engine) -> None:
    """Fold the WAL into the .db file so the archived DB is self-contained."""
    try:
        with engine.connect() as conn:
            conn.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)")
    except Exception:  # noqa: BLE001 — best effort; backup proceeds regardless
        pass


def create_backup(
    home: Path,
    out_path: Path,
    *,
    engine=None,
    include_keys: bool = False,
) -> tuple[Path, int]:
    """Tar the ``.ironjarvis`` home (DB + memory + artifacts + config) to
    ``out_path``. Excludes the Fernet keys (and their ``.bak`` rotation siblings)
    unless ``include_keys``, and ALWAYS excludes the ``backups/`` dir so snapshots
    never nest. Returns ``(out_path, file_count)``."""
    if engine is not None:
        _checkpoint_wal(engine)
    home = Path(home)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    backups_dir = (home / BACKUP_DIRNAME).resolve()
    n = 0
    with tarfile.open(out_path, "w:gz") as tar:
        for p in home.rglob("*"):
            if not p.is_file():
                continue
            rp = p.resolve()
            if rp == out_path.resolve() or backups_dir in rp.parents:
                continue  # never archive the backups themselves
            if not include_keys and (
                p.name.startswith(".secrets.key") or p.name.startswith(".vault.key")
            ):
                continue
            tar.add(p, arcname=str(p.relative_to(home.parent)))
            n += 1
    return out_path, n


def prune_backups(backups_dir: Path, keep: int) -> int:
    """Keep the newest ``keep`` auto-backup archives; delete the rest. Returns the
    number deleted."""
    backups_dir = Path(backups_dir)
    if keep <= 0 or not backups_dir.exists():
        return 0
    snaps = sorted(
        backups_dir.glob("ironjarvis-backup-*.tar.gz"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    removed = 0
    for p in snaps[keep:]:
        try:
            p.unlink()
            removed += 1
        except OSError:
            pass
    return removed


def run_auto_backup(home: Path, *, engine=None, keep: int = 7) -> Path:
    """Write a timestamped snapshot under ``<home>/backups`` and prune to ``keep``.
    Keys are never included (an automatic backup is for data recovery, not key
    escrow). Returns the archive path."""
    home = Path(home)
    backups_dir = home / BACKUP_DIRNAME
    stamp = utcnow().strftime("%Y%m%d-%H%M%S")
    out = backups_dir / f"ironjarvis-backup-{stamp}.tar.gz"
    create_backup(home, out, engine=engine, include_keys=False)
    prune_backups(backups_dir, keep)
    return out
