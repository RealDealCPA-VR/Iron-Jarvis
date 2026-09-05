"""Backup helpers: one shared tar routine + a scheduled auto-backup safety net.

A daily driver that runs for weeks needs a backup it never has to remember to
take. :func:`create_backup` is the shared archive routine (used by the
``ironjarvis backup`` CLI and the daemon's periodic loop); :func:`run_auto_backup`
writes a timestamped snapshot under ``<home>/backups`` and prunes old ones.
"""

import os
import shutil
import tarfile
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from .core.ids import utcnow

BACKUP_DIRNAME = "backups"
_DB_NAME = "ironjarvis.db"
_BACKUP_GLOB = "ironjarvis-backup-*.tar.gz"


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
    """Tar the ``.ironjarvis`` home (DB + memory + config + secrets + skills) to
    ``out_path``. When an ``engine`` is given, the DB is archived as a CONSISTENT
    ``VACUUM INTO`` snapshot (not the live ``.db``/``-wal``/``-shm``, which a
    concurrent checkpoint could leave internally inconsistent → a malformed restore).
    Excludes the Fernet keys unless ``include_keys``, ALWAYS excludes ``backups/``,
    ``workspaces/``, and the regenerable media library (``artifacts/`` +
    ``creative-thumbs/``), and writes the tar atomically (temp+os.replace). Returns
    ``(out_path, count)``."""
    home = Path(home)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    backups_dir = (home / BACKUP_DIRNAME).resolve()
    # Disposable per-session scratch — never in a backup (it grows without bound and
    # would multiply the archive ~keep× ; a backup captures DB + secrets + config +
    # memory, not regeneratable session workspaces).
    workspaces_dir = (home / "workspaces").resolve()
    # The generated-media library (gallery images/video/audio + their thumbnails)
    # dwarfs the state that actually matters (DB, config, secrets, skills,
    # terminals.json) — hundreds of MB per snapshot. It's the user's own,
    # regeneratable/gallery data, so it stays out of the archive.
    media_dirs = ((home / "artifacts").resolve(), (home / "creative-thumbs").resolve())
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
                if any(m in rp.parents for m in media_dirs):
                    continue  # skip regenerable media library (dwarfs the real state)
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


# --- v1.229.0 (audit Wave 3, CL7): list / boot-skip / restore ----------------


def list_backups(home: Path) -> list[dict]:
    """The auto/manual archives under ``<home>/backups``, newest first:
    ``{name, bytes, modified_at}`` (UTC ISO). Never raises — an unreadable dir
    lists as empty."""
    backups_dir = Path(home) / BACKUP_DIRNAME
    out: list[dict] = []
    try:
        for p in backups_dir.glob(_BACKUP_GLOB):
            try:
                st = p.stat()
            except OSError:
                continue
            out.append(
                {
                    "name": p.name,
                    "bytes": st.st_size,
                    "modified_at": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc)
                    .isoformat(timespec="seconds"),
                }
            )
    except OSError:
        return []
    out.sort(key=lambda r: (r["modified_at"], r["name"]), reverse=True)
    return out


def boot_backup_delay_s(home: Path, interval_s: float, *, now: "float | None" = None) -> float:
    """How long the daemon's auto-backup loop should wait before its FIRST
    snapshot: ``0`` when there is no archive yet or the newest is older than
    ``interval_s``, else the remainder of the interval. Before this every boot
    wrote a snapshot 60 s in, so a restart-heavy week filled ``keep=7`` with
    boot copies of the same day and pushed the older days out."""
    backups_dir = Path(home) / BACKUP_DIRNAME
    try:
        newest = max((p.stat().st_mtime for p in backups_dir.glob(_BACKUP_GLOB)), default=None)
    except OSError:
        return 0.0
    if newest is None:
        return 0.0
    age = (time.time() if now is None else now) - newest
    if age < 0:  # a clock that went backwards — treat the archive as fresh
        age = 0.0
    return max(0.0, float(interval_s) - age)


def extract_backup(archive: Path, dest: Path) -> None:
    """Extract ``archive`` into ``dest`` (the PARENT of the home — members are
    stored as ``.ironjarvis/...``). ``filter="data"`` rejects absolute paths,
    ``..`` traversal and link escapes. The ``ironjarvis restore`` CLI calls
    this directly over the live home (it runs with nothing open); the daemon
    goes through :func:`restore_backup_live`."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:gz") as tar:
        tar.extractall(path=dest, filter="data")


def restore_backup_live(home: Path, archive: Path) -> int:
    """Restore ``archive`` over a home the daemon has OPEN, safely enough to be
    followed by a restart: extract into a private staging dir first, then move
    files into place with ``os.replace`` — the DATABASE FIRST, after deleting
    the old ``-wal``/``-shm`` sidecars (a leftover WAL would be replayed into
    the restored file and corrupt it). On Windows a replace fails loudly with
    ``PermissionError`` while any connection still holds the file, so the
    caller disposes the engine first and a held-open DB aborts the restore
    BEFORE anything else moved — never a half-restored home. Files in the home
    that the archive does not carry are kept. Returns the file count."""
    home = Path(home)
    archive = Path(archive)
    staging = Path(tempfile.mkdtemp(prefix=".restore-", dir=str(home)))
    try:
        extract_backup(archive, staging)
        tops = [p for p in staging.iterdir() if p.is_dir()]
        if len(tops) != 1:
            raise ValueError(f"not an Iron Jarvis backup (top-level entries: {len(tops)})")
        src_home = tops[0]
        db_src = src_home / _DB_NAME
        moved = 0
        if db_src.exists():
            for side in (f"{_DB_NAME}-wal", f"{_DB_NAME}-shm"):
                (home / side).unlink(missing_ok=True)
            os.replace(db_src, home / _DB_NAME)
            moved += 1
        for p in sorted(src_home.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(src_home)
            target = home / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(p, target)
            moved += 1
        return moved
    finally:
        shutil.rmtree(staging, ignore_errors=True)
