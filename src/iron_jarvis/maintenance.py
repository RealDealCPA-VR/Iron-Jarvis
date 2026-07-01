"""Backup helpers: one shared tar routine + a scheduled auto-backup safety net.

A daily driver that runs for weeks needs a backup it never has to remember to
take. :func:`create_backup` is the shared archive routine (used by the
``ironjarvis backup`` CLI and the daemon's periodic loop); :func:`run_auto_backup`
writes a timestamped snapshot under ``<home>/backups`` and prunes old ones.
"""

import os
import tarfile
import tempfile
from pathlib import Path

from .core.ids import utcnow

BACKUP_DIRNAME = "backups"
_DB_NAME = "ironjarvis.db"


def _consistent_db_snapshot(engine, home: Path) -> "Path | None":
    """Produce a point-in-time, internally-consistent copy of the SQLite DB via
    ``VACUUM INTO`` (folds the WAL, takes a read lock — writers continue), then
    ``integrity_check`` it. Returns the snapshot path (caller deletes it), or None
    on failure (the caller then falls back to copying the live files)."""
    snap = home / f".snapshot-{utcnow().strftime('%Y%m%d-%H%M%S-%f')}.db"
    try:
        if snap.exists():
            snap.unlink()  # VACUUM INTO requires the target not to exist
        with engine.connect() as conn:
            target = str(snap).replace("'", "''")
            conn.exec_driver_sql(f"VACUUM INTO '{target}'")
        import sqlite3

        con = sqlite3.connect(str(snap))
        try:
            ok = con.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            con.close()
        if ok != "ok":
            snap.unlink(missing_ok=True)
            return None
        return snap
    except Exception:  # noqa: BLE001 — fall back to live-file copy on any failure
        try:
            snap.unlink(missing_ok=True)
        except OSError:
            pass
        return None


def create_backup(
    home: Path,
    out_path: Path,
    *,
    engine=None,
    include_keys: bool = False,
) -> tuple[Path, int]:
    """Tar the ``.ironjarvis`` home (DB + memory + artifacts + config) to
    ``out_path``. When an ``engine`` is given, the DB is archived as a CONSISTENT
    ``VACUUM INTO`` snapshot (not the live ``.db``/``-wal``/``-shm``, which a
    concurrent checkpoint could leave internally inconsistent → a malformed restore).
    Excludes the Fernet keys unless ``include_keys``, ALWAYS excludes ``backups/``,
    and writes the tar atomically (temp+os.replace). Returns ``(out_path, count)``."""
    home = Path(home)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    backups_dir = (home / BACKUP_DIRNAME).resolve()
    # Disposable per-session scratch — never in a backup (it grows without bound and
    # would multiply the archive ~keep× ; a backup captures DB + secrets + config +
    # memory, not regeneratable session workspaces).
    workspaces_dir = (home / "workspaces").resolve()
    db_path = (home / _DB_NAME).resolve()
    snapshot = _consistent_db_snapshot(engine, home) if (engine is not None and db_path.exists()) else None

    n = 0
    tmp_out = out_path.with_name(out_path.name + ".tmp")
    try:
        with tarfile.open(tmp_out, "w:gz") as tar:
            for p in home.rglob("*"):
                if not p.is_file():
                    continue
                rp = p.resolve()
                if rp in (out_path.resolve(), tmp_out.resolve()) or backups_dir in rp.parents:
                    continue  # never archive the backups themselves
                if workspaces_dir in rp.parents:
                    continue  # skip disposable session scratch (unbounded growth)
                if snapshot is not None and rp == snapshot.resolve():
                    continue  # the snapshot temp is added below, not as itself
                if snapshot is not None and (
                    rp == db_path or p.name in (f"{_DB_NAME}-wal", f"{_DB_NAME}-shm")
                ):
                    continue  # skip live DB + sidecars; the snapshot stands in
                if not include_keys and (
                    p.name.startswith(".secrets.key") or p.name.startswith(".vault.key")
                ):
                    continue
                tar.add(p, arcname=str(p.relative_to(home.parent)))
                n += 1
            if snapshot is not None:  # add the consistent snapshot AS ironjarvis.db
                tar.add(snapshot, arcname=str((home / _DB_NAME).relative_to(home.parent)))
                n += 1
        os.replace(tmp_out, out_path)
    finally:
        if snapshot is not None:
            try:
                snapshot.unlink()
            except OSError:
                pass
        try:
            if tmp_out.exists():
                tmp_out.unlink()
        except OSError:
            pass
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


def run_auto_backup(
    home: Path, *, engine=None, keep: int = 7, include_keys: bool = True
) -> Path:
    """Write a timestamped snapshot under ``<home>/backups`` and prune to ``keep``.

    Keys are INCLUDED by default: a local automatic backup is the disaster-recovery
    net, and a snapshot that omits the Fernet keys silently fails its one job —
    restoring it regenerates a fresh key that cannot decrypt any stored secret, so
    every API key / OAuth login is lost while the UI still shows them "present".
    The home is already local + private; pass ``include_keys=False`` only for a
    portable export you intend to move off-machine. Returns the archive path."""
    home = Path(home)
    backups_dir = home / BACKUP_DIRNAME
    stamp = utcnow().strftime("%Y%m%d-%H%M%S")
    out = backups_dir / f"ironjarvis-backup-{stamp}.tar.gz"
    create_backup(home, out, engine=engine, include_keys=include_keys)
    prune_backups(backups_dir, keep)
    return out
