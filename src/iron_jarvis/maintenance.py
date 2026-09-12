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
#: Where a cleared media library waits (v1.256.0, R-01). Clearing MOVES files
#: here so the press is recoverable; `purge_trash` is the deliberate second
#: press that actually frees the disk.
TRASH_DIRNAME = "trash"
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
    home: Path,
    *,
    engine=None,
    keep: int = 7,
    include_keys: bool = True,
    config=None,
) -> Path:
    """Write a timestamped snapshot under ``<home>/backups`` and prune to ``keep``.

    Keys are INCLUDED by default: a local automatic backup is the disaster-recovery
    net, and a snapshot that omits the Fernet keys silently fails its one job —
    restoring it regenerates a fresh key that cannot decrypt any stored secret, so
    every API key / OAuth login is lost while the UI still shows them "present".
    The home is already local + private; pass ``include_keys=False`` only for a
    portable export you intend to move off-machine. Returns the archive path.

    ``config`` (v1.249.0, R-05): when it carries a ``backup_mirror_dir``, the new
    archive (and, with ``backup_mirror_media``, the media library) is ALSO copied
    there — read live, so a Settings change applies to the very next backup. A
    mirror that fails is RECORDED (:func:`mirror_status`), never raised: the local
    backup already succeeded and must not be reported as a failure."""
    home = Path(home)
    backups_dir = home / BACKUP_DIRNAME
    stamp = utcnow().strftime("%Y%m%d-%H%M%S")
    out = backups_dir / f"ironjarvis-backup-{stamp}.tar.gz"
    create_backup(home, out, engine=engine, include_keys=include_keys)
    prune_backups(backups_dir, keep)
    mirror = (getattr(config, "backup_mirror_dir", "") or "").strip() if config is not None else ""
    if mirror:
        try:
            mirror_backup(
                home,
                out,
                mirror,
                keep=keep,
                media=bool(getattr(config, "backup_mirror_media", True)),
            )
        except Exception as exc:  # noqa: BLE001 — the local backup stands regardless
            _write_mirror_state(
                home,
                {
                    "dir": mirror,
                    "at": utcnow().isoformat(timespec="seconds"),
                    "ok": False,
                    "archive": None,
                    "media": None,
                    "error": f"couldn't copy the backup to {mirror}: {type(exc).__name__}: {exc}"[:400],
                },
            )
    return out


# --- v1.249.0 (R-05): a second copy on another drive -------------------------
#
# Every archive lives inside the home, on the same disk as the data it protects,
# so one failed drive (or one deleted %APPDATA% folder) loses the data and all
# seven backups together — and the generated-media library (``artifacts/`` +
# ``creative-thumbs/``: paid generations that cannot be recreated exactly) was
# in no backup at all. A MIRROR folder, ideally on another drive, gets a copy of
# each new archive (pruned the same way) and an incremental copy of the media.

#: Where the last mirror run's outcome is kept — inside ``backups/``, which no
#: archive ever includes.
MIRROR_STATE_NAME = "mirror-status.json"
#: The media library is copied under this sub-folder of the mirror.
MIRROR_MEDIA_DIRNAME = "media"
#: The media folders (relative to the home) — the same ones every archive skips.
_MEDIA_DIRS = ("artifacts", "creative-thumbs")
#: One media pass is BOUNDED: a first copy of a large library finishes over
#: several backups instead of holding the backup thread for hours, and what was
#: left for next time is REPORTED, never implied complete.
MIRROR_MEDIA_MAX_FILES = 5000
MIRROR_MEDIA_DEADLINE_S = 600.0
#: A copy counts as unchanged when its size matches and its modified time is
#: within this many seconds — FAT-formatted drives store times to 2 s.
_MTIME_SLACK_S = 2.0


def mirror_dir_problem(home: Path, value: str) -> "str | None":
    """Why ``value`` cannot hold the backup copies, or None when it can (v1.249.0).

    ``fs_policy.root_problem`` answers first — absolute, a folder on this
    machine, allowed, and WRITABLE (the one definition every "work in this
    folder" door uses) — then one rule of its own: not inside Iron Jarvis's own
    data folder, because a copy in the same tree survives nothing the original
    would not. ``""`` (switched off) is never a problem. BLOCKING (the
    writability probe creates a file): call it off the event loop."""
    from .core.fs_policy import root_problem

    raw = (value or "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    problem = root_problem(path)
    if problem:
        return problem
    try:
        folder = path.resolve()
        own = Path(home).resolve()
    except OSError as exc:
        return f"folder cannot be read: {exc}"
    if folder == own or own in folder.parents:
        return (
            "that folder is inside Iron Jarvis's own data folder — pick a folder "
            "outside it, ideally on another drive"
        )
    return None


def _state_path(home: Path) -> Path:
    return Path(home) / BACKUP_DIRNAME / MIRROR_STATE_NAME


def _read_mirror_state(home: Path) -> "dict | None":
    import json

    try:
        data = json.loads(_state_path(home).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write_mirror_state(home: Path, state: dict) -> None:
    """Atomic write of the last run's outcome; never raises (a status file that
    cannot be written must not fail a backup that succeeded)."""
    import json

    path = _state_path(home)
    tmp = path.with_name(path.name + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def mirror_media(
    home: Path,
    dest_root: Path,
    *,
    max_files: int = MIRROR_MEDIA_MAX_FILES,
    deadline_s: float = MIRROR_MEDIA_DEADLINE_S,
) -> dict:
    """Copy NEW or CHANGED media files (size + modified time) from the home's
    media folders into ``dest_root``, keeping their relative paths. Additive
    only — nothing in the mirror is ever deleted here. Bounded by ``max_files``
    copies and ``deadline_s``; a pass that stopped early says so
    (``truncated``) and the rest is picked up by the next backup."""
    home = Path(home)
    dest_root = Path(dest_root)
    start = time.monotonic()
    copied = unchanged = failed = 0
    copied_bytes = 0
    errors: list[str] = []
    truncated = False
    for name in _MEDIA_DIRS:
        src_root = home / name
        if not src_root.is_dir():
            continue
        for dirpath, _dirnames, filenames in os.walk(src_root):
            for fn in filenames:
                if copied >= max_files or time.monotonic() - start > deadline_s:
                    truncated = True
                    break
                src = Path(dirpath) / fn
                rel = src.relative_to(home)
                dst = dest_root / rel
                tmp = dst.with_name(dst.name + ".ij-tmp")
                try:
                    st = src.stat()
                    try:
                        dt = dst.stat()
                        if (
                            dt.st_size == st.st_size
                            and abs(dt.st_mtime - st.st_mtime) <= _MTIME_SLACK_S
                        ):
                            unchanged += 1
                            continue
                    except FileNotFoundError:
                        pass
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, tmp)
                    os.replace(tmp, dst)
                    copied += 1
                    copied_bytes += st.st_size
                except OSError as exc:
                    failed += 1
                    if len(errors) < 5:
                        errors.append(f"{rel}: {exc}")
                    try:
                        tmp.unlink(missing_ok=True)
                    except OSError:
                        pass
            if truncated:
                break
        if truncated:
            break
    return {
        "copied": copied,
        "unchanged": unchanged,
        "bytes": copied_bytes,
        "failed": failed,
        "errors": errors,
        "truncated": truncated,
    }


def mirror_backup(
    home: Path,
    archive: Path,
    mirror_dir: str,
    *,
    keep: int = 7,
    media: bool = True,
    max_files: int = MIRROR_MEDIA_MAX_FILES,
    deadline_s: float = MIRROR_MEDIA_DEADLINE_S,
) -> dict:
    """Copy ``archive`` into ``mirror_dir`` (temp + ``os.replace``), prune the
    mirror to ``keep`` — ONLY files named like the app's own archives, so a
    user's own files in that folder are never touched — then, with ``media``,
    copy the media library incrementally under ``<mirror>/media``. Records and
    returns the outcome (:func:`mirror_status` reads it back). BLOCKING: runs
    inside the backup thread, never on the event loop."""
    home = Path(home)
    archive = Path(archive)
    state: dict = {
        "dir": mirror_dir,
        "at": utcnow().isoformat(timespec="seconds"),
        "ok": False,
        "archive": None,
        "pruned": 0,
        "media": None,
        "error": None,
    }
    problem = mirror_dir_problem(home, mirror_dir)
    if problem:
        state["error"] = f"the backup copy folder can't be used: {problem}"
        state["missing"] = not Path(mirror_dir).expanduser().is_dir()
        _write_mirror_state(home, state)
        return state
    dest_dir = Path(mirror_dir).expanduser()
    tmp = dest_dir / (archive.name + ".tmp")
    try:
        shutil.copy2(archive, tmp)
        os.replace(tmp, dest_dir / archive.name)
    except OSError as exc:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        state["error"] = f"couldn't copy the backup to {dest_dir}: {exc}"
        _write_mirror_state(home, state)
        return state
    state["archive"] = archive.name
    state["pruned"] = prune_backups(dest_dir, keep)
    state["ok"] = True
    if media:
        report = mirror_media(
            home, dest_dir / MIRROR_MEDIA_DIRNAME, max_files=max_files, deadline_s=deadline_s
        )
        state["media"] = report
        if report["failed"]:
            state["error"] = (
                f"{report['failed']} media file(s) could not be copied — "
                + "; ".join(report["errors"][:2])
            )
    _write_mirror_state(home, state)
    return state


def mirror_status(home: Path, config=None) -> dict:
    """What Settings → Maintenance and the health check show about the mirror
    (v1.249.0): the configured folder, whether media is copied, whether the
    folder is there right now (an unplugged drive), and the last run's outcome
    — only when that run was for THIS folder. Never raises. Touches the
    filesystem (``is_dir``): call it off the event loop."""
    folder = (getattr(config, "backup_mirror_dir", "") or "").strip() if config is not None else ""
    media = bool(getattr(config, "backup_mirror_media", True)) if config is not None else True
    missing = False
    if folder:
        try:
            missing = not Path(folder).expanduser().is_dir()
        except OSError:
            missing = True
    last = _read_mirror_state(home)
    if not folder or not last or last.get("dir") != folder:
        last = None
    return {"dir": folder, "media": media, "configured": bool(folder), "missing": missing, "last": last}


def mirror_loop_health(status: dict) -> "dict | None":
    """The ``/diagnostics`` background-loop entry for the mirror, in the one
    shape every loop reports (``ok`` + ``last_success_at``, or ``last_error`` +
    ``at``) — so a failing copy is NAMED on the Overview and in the bell like
    any other loop. None when no mirror is configured."""
    if not status.get("configured"):
        return None
    last = status.get("last") or {}
    if status.get("missing"):
        return {
            "ok": False,
            "last_error": f"backup copy folder {status['dir']} is missing — is the drive plugged in?",
            "at": last.get("at") or utcnow().isoformat(timespec="seconds"),
        }
    if last and not last.get("ok"):
        return {"ok": False, "last_error": str(last.get("error") or "copy failed"), "at": last.get("at")}
    if last:
        return {"ok": True, "last_success_at": last.get("at")}
    return None


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


# --------------------------------------------------------------------------- #
# What the app is keeping on disk, and clearing it (v1.256.0, R-01)
# --------------------------------------------------------------------------- #
#: The state folders the report accounts for, as (label, dirname) pairs. The
#: labels are what the user reads on Settings -> Maintenance, so each says what
#: the folder IS rather than what it happens to be called on disk.
_REPORT_DIRS = (
    ("Generated media", "artifacts"),
    ("Creative thumbnails", "creative-thumbs"),
    ("Backups", BACKUP_DIRNAME),
    ("Undo history", "undo"),
    ("Scan text cache", "ocr"),
    ("Code workspaces", "codelab"),
    ("Cleared, awaiting deletion", TRASH_DIRNAME),
)


def _walk_dir(root: Path) -> "tuple[int, int, float]":
    """``(files, bytes, newest mtime)`` under ``root``; zeros when it is absent.

    The same ``os.walk`` + ``stat`` accounting :func:`mirror_media` uses, so the
    report and the mirror can never disagree about the same folder. A file that
    vanishes mid-walk is skipped rather than raising: this runs while the app is
    still writing.
    """
    files = 0
    total = 0
    newest = 0.0
    if not root.is_dir():
        return (0, 0, 0.0)
    for dirpath, _dirnames, filenames in os.walk(root):
        for fn in filenames:
            try:
                st = (Path(dirpath) / fn).stat()
            except OSError:
                continue
            files += 1
            total += st.st_size
            if st.st_mtime > newest:
                newest = st.st_mtime
    return (files, total, newest)


def storage_report(home: Path) -> dict:
    """What Iron Jarvis is keeping under ``home``, by category (R-01).

    THE SILENT GROWTH THIS SURFACES. Measured on the live install before this
    shipped: 814 MB of state, of which ``artifacts/`` was 783 MB — 186 files, 57
    of them videos totalling 676 MB, the largest 71.6 MB, none newer than
    August. Nothing pruned it and no screen reported it, so the only way to find
    it was to go looking with a file manager.

    Read-only and never raises. ``clearable`` marks the categories
    :func:`clear_media` will move — generated media and thumbnails only, never
    backups, never undo history, never a code workspace.
    """
    home = Path(home)
    rows: list[dict] = []
    for label, dirname in _REPORT_DIRS:
        files, total, newest = _walk_dir(home / dirname)
        rows.append(
            {
                "label": label,
                "dir": dirname,
                "path": str(home / dirname),
                "files": files,
                "bytes": total,
                "newest": (
                    datetime.fromtimestamp(newest, timezone.utc).isoformat()
                    if newest
                    else None
                ),
                "clearable": dirname in _MEDIA_DIRS,
            }
        )
    db_bytes = 0
    for side in (_DB_NAME, _DB_NAME + "-wal", _DB_NAME + "-shm"):
        try:
            db_bytes += (home / side).stat().st_size
        except OSError:
            pass
    rows.append(
        {
            "label": "Database",
            "dir": _DB_NAME,
            "path": str(home / _DB_NAME),
            "files": 1 if db_bytes else 0,
            "bytes": db_bytes,
            "newest": None,
            "clearable": False,
        }
    )
    return {
        "home": str(home),
        "total_bytes": sum(r["bytes"] for r in rows),
        "categories": rows,
    }


def media_candidates(home: Path, older_than_days: int) -> dict:
    """What :func:`clear_media` WOULD move, without moving anything (R-01).

    The confirm card is built from this, so the count and the size the user
    agrees to are the numbers the move then reports — a dry run that shares the
    real thing's rules rather than estimating them separately.
    """
    home = Path(home)
    cutoff = time.time() - max(0, int(older_than_days)) * 86400.0
    files: list[dict] = []
    total = 0
    for dirname in _MEDIA_DIRS:
        root = home / dirname
        if not root.is_dir():
            continue
        for dirpath, _dirnames, filenames in os.walk(root):
            for fn in filenames:
                src = Path(dirpath) / fn
                try:
                    st = src.stat()
                except OSError:
                    continue
                if st.st_mtime > cutoff:
                    continue
                total += st.st_size
                files.append(
                    {
                        "rel": src.relative_to(home).as_posix(),
                        "bytes": st.st_size,
                    }
                )
    files.sort(key=lambda f: -f["bytes"])
    return {"files": len(files), "bytes": total, "largest": files[:10]}


def clear_media(home: Path, older_than_days: int) -> dict:
    """MOVE generated media older than ``older_than_days`` into the trash (R-01).

    IT MOVES, IT DOES NOT DELETE, and that is the whole design. The undo journal
    has exactly two file kinds — ``file_restore`` (needs the prior bytes) and
    ``file_delete`` (created new -> unlink on undo). Neither can reverse "remove
    700 MB of video that already existed": the first would demand a pre-image of
    the very bytes being freed, and the second inverts to UNLINKING, which would
    destroy rather than restore. So the files move to ``<home>/trash/<stamp>/``
    keeping their relative paths and the manifest names them. Getting them back
    is a move; freeing the disk for real is a second, deliberate press
    (:func:`purge_trash`).

    Only the two media directories every archive already skips are touched —
    never backups, never undo history, never a code workspace, never a project
    folder.
    """
    home = Path(home)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    dest_root = home / TRASH_DIRNAME / stamp
    cutoff = time.time() - max(0, int(older_than_days)) * 86400.0
    moved = 0
    moved_bytes = 0
    failed = 0
    errors: list[str] = []
    names: list[str] = []
    for dirname in _MEDIA_DIRS:
        root = home / dirname
        if not root.is_dir():
            continue
        for dirpath, _dirnames, filenames in os.walk(root):
            for fn in filenames:
                src = Path(dirpath) / fn
                try:
                    st = src.stat()
                    if st.st_mtime > cutoff:
                        continue
                    rel = src.relative_to(home)
                    dst = dest_root / rel
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(src, dst)
                except OSError as exc:
                    failed += 1
                    if len(errors) < 5:
                        errors.append(str(fn) + ": " + str(exc))
                    continue
                moved += 1
                moved_bytes += st.st_size
                if len(names) < 200:
                    names.append(rel.as_posix())
    return {
        "moved": moved,
        "bytes": moved_bytes,
        "failed": failed,
        "errors": errors,
        "trash": str(dest_root) if moved else "",
        "names": names,
    }


def purge_trash(home: Path) -> dict:
    """Delete everything under ``<home>/trash`` for good (R-01).

    The second press. Until this runs, a cleared library is recoverable by
    moving it back, which is what lets :func:`clear_media` be one confident
    click rather than a warning nobody reads.
    """
    home = Path(home)
    root = home / TRASH_DIRNAME
    if not root.is_dir():
        return {"deleted": 0, "bytes": 0}
    files, total, _newest = _walk_dir(root)
    shutil.rmtree(root, ignore_errors=True)
    return {"deleted": files, "bytes": total}
