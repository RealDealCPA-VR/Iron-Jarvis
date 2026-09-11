"""v1.229.0 (audit Wave 3, task 3D — OBS5 / D8 / CL7): the app tells the truth
about itself, and the user can act on it from Settings → Maintenance.

- OBS5: ``core/logging.RecentErrorsHandler`` keeps the last 50 WARNING+ records
  from the WHOLE tree (both app names and the libraries) in memory, and
  ``GET /diagnostics/errors`` serves them as ``{ts, level, logger, message}``.
- OBS5: ``/diagnostics`` names what it measures — ``db_liveness`` is the
  ``SELECT 1``; ``db_integrity`` stays as a compat alias and the docstring says
  which is which.
- CL7: the auto-backup loop skips the boot snapshot while the newest archive
  is younger than the interval (``maintenance.boot_backup_delay_s``, called
  from ``daemon/app.py`` between the 60 s boot sleep and the loop).
- CL7: ``GET /maintenance/backups`` lists the archives and
  ``POST /maintenance/restore`` restores one over the live home (DB first,
  sidecars removed, staged so a held-open DB aborts before anything moved)
  and schedules the daemon stop; refused while work is in flight.
"""

from __future__ import annotations

import inspect
import logging
import os
import re
import tarfile
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iron_jarvis import maintenance
from iron_jarvis.core.logging import (
    RECENT_ERRORS_CAPACITY,
    _RECENT_ERRORS,
    configure_logging,
    recent_errors,
)
from iron_jarvis.daemon import app as app_mod
from iron_jarvis.daemon.app import create_app

ISO_MS = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}\+00:00$")


# ---------------------------------------------------------------------------
# OBS5 — the ring buffer and its route
# ---------------------------------------------------------------------------


def test_ring_buffer_holds_warning_plus_from_every_namespace_once():
    configure_logging()
    _RECENT_ERRORS.clear()
    logging.getLogger("ironjarvis.daemon").warning("RING-A %s", 1)
    logging.getLogger("iron_jarvis.core.db").error("RING-B")  # propagates to root: ONCE
    logging.getLogger("apscheduler.executors.default").warning("RING-C")  # a library
    logging.getLogger("ironjarvis.daemon").info("RING-INFO")  # below the bar
    logging.getLogger("iron_jarvis.events").debug("RING-DEBUG")
    try:
        raise RuntimeError("kaboom")
    except RuntimeError:
        logging.getLogger("iron_jarvis.daemon").exception("RING-EXC")

    got = recent_errors()
    assert [r["message"] for r in got] == [
        "RING-A 1",
        "RING-B",
        "RING-C",
        "RING-EXC — RuntimeError: kaboom",
    ]
    assert [r["level"] for r in got] == ["WARNING", "ERROR", "WARNING", "ERROR"]
    assert [r["logger"] for r in got] == [
        "ironjarvis.daemon",
        "iron_jarvis.core.db",
        "apscheduler.executors.default",
        "iron_jarvis.daemon",
    ]
    for r in got:
        assert set(r) == {"ts", "level", "logger", "message"}
        assert ISO_MS.match(r["ts"]), r["ts"]


def test_ring_handler_never_raises_into_the_caller():
    """The callers are the daemon's except branches (``log.exception`` in the
    lifespan): an exception whose ``__str__`` raises must still be recorded,
    not abort boot."""

    class _Unprintable(Exception):
        def __str__(self):
            raise ValueError("no str for you")

    configure_logging()
    _RECENT_ERRORS.clear()
    try:
        raise _Unprintable()
    except _Unprintable:
        logging.getLogger("ironjarvis.daemon").exception("boot step failed")  # must not raise
    got = recent_errors()
    assert len(got) == 1
    assert got[0]["message"] == "boot step failed — _Unprintable: <exception str() failed>"


def test_ring_buffer_is_bounded_and_served_by_the_route(tmp_path):
    app = create_app(str(tmp_path))
    with TestClient(app) as c:
        _RECENT_ERRORS.clear()
        for i in range(RECENT_ERRORS_CAPACITY + 10):
            logging.getLogger("ironjarvis.test").warning("W%03d", i)
        r = c.get("/diagnostics/errors")
        assert r.status_code == 200
        body = r.json()
        assert body["capacity"] == RECENT_ERRORS_CAPACITY == 50
        msgs = [e["message"] for e in body["errors"]]
        assert len(msgs) == 50
        assert msgs[0] == "W010" and msgs[-1] == "W059"  # oldest first, oldest 10 evicted
        assert [e["message"] for e in c.get("/diagnostics/errors?limit=3").json()["errors"]] == [
            "W057",
            "W058",
            "W059",
        ]
        assert len(c.get("/diagnostics/errors?limit=500").json()["errors"]) == 50


def test_ring_handler_is_installed_by_configure_logging_on_both_trees_and_root():
    """A handler nobody installs records nothing."""
    from iron_jarvis.core.logging import RecentErrorsHandler

    configure_logging()
    for name in ("ironjarvis", "iron_jarvis"):
        rings = [h for h in logging.getLogger(name).handlers if isinstance(h, RecentErrorsHandler)]
        assert len(rings) == 1 and rings[0].store is _RECENT_ERRORS, name
    root_rings = [h for h in logging.getLogger().handlers if isinstance(h, RecentErrorsHandler)]
    assert len(root_rings) == 1 and root_rings[0].store is _RECENT_ERRORS
    assert root_rings[0].filters, "the root ring must skip the app trees or WARNINGs land twice"


# ---------------------------------------------------------------------------
# OBS5 — db_liveness says what /diagnostics measures
# ---------------------------------------------------------------------------


def test_diagnostics_reports_liveness_and_keeps_the_integrity_alias(tmp_path, monkeypatch):
    app = create_app(str(tmp_path))
    with TestClient(app) as c:
        d = c.get("/diagnostics").json()
        assert d["db_liveness"] == "ok"
        assert d["db_integrity"] == "ok"

        class _Dead:
            def connect(self):
                raise RuntimeError("no db")

        monkeypatch.setattr(app.state.platform, "engine", _Dead())
        d2 = c.get("/diagnostics").json()
        assert d2["db_liveness"] == "error: no db"
        assert d2["db_integrity"] == d2["db_liveness"]  # the alias never disagrees

    route = next(r for r in app.routes if getattr(r, "path", "") == "/diagnostics")
    doc = route.endpoint.__doc__ or ""
    assert "db_liveness" in doc and "SELECT 1" in doc
    assert "db_integrity" in doc and "integrity_check" in doc and "compatibility" in doc


# ---------------------------------------------------------------------------
# CL7 — the boot snapshot is skipped while a fresh archive exists
# ---------------------------------------------------------------------------


def _archive(home: Path, stamp: str, age_s: float, now: float) -> Path:
    p = home / "backups" / f"ironjarvis-backup-{stamp}.tar.gz"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")
    os.utime(p, (now - age_s, now - age_s))
    return p


def test_boot_backup_delay_is_the_remainder_of_the_interval(tmp_path):
    home = tmp_path / ".ironjarvis"
    now = time.time()
    assert maintenance.boot_backup_delay_s(home, 3600, now=now) == 0.0  # no dir at all
    (home / "backups").mkdir(parents=True)
    assert maintenance.boot_backup_delay_s(home, 3600, now=now) == 0.0  # nothing yet
    _archive(home, "20260901-000000", 7200, now)  # older than the interval: due
    assert maintenance.boot_backup_delay_s(home, 3600, now=now) == 0.0
    _archive(home, "20260904-000000", 600, now)  # 10 min old: wait the other 50
    assert abs(maintenance.boot_backup_delay_s(home, 3600, now=now) - 3000) < 2
    # a foreign file in the folder is not a backup
    (home / "backups" / "notes.txt").write_text("x")
    os.utime(home / "backups" / "notes.txt", (now, now))
    assert abs(maintenance.boot_backup_delay_s(home, 3600, now=now) - 3000) < 2
    # an archive from the future (clock went back) counts as fresh, never negative
    _archive(home, "20260905-000000", -900, now)
    assert abs(maintenance.boot_backup_delay_s(home, 3600, now=now) - 3600) < 2


def test_app_boot_loop_waits_for_the_delay_before_the_first_snapshot():
    """The CALL SITE is the feature: the delay is consulted after the 60 s
    boot sleep and slept BEFORE the loop's first run_auto_backup."""
    src = inspect.getsource(app_mod)
    i = src.index("await asyncio.sleep(60)")
    j = src.index("boot_backup_delay_s, platform.config.home, interval", i)
    k = src.index("await asyncio.sleep(delay)", j)
    w = src.index("while True:", k)
    first_run = src.index("run_auto_backup,", w)
    assert i < j < k < w < first_run


# ---------------------------------------------------------------------------
# CL7 — list + restore
# ---------------------------------------------------------------------------


def test_restore_replaces_config_and_db_then_schedules_the_stop(tmp_path, monkeypatch):
    stops: list[int] = []
    monkeypatch.setattr(app_mod, "_graceful_stop", lambda: stops.append(1))
    app = create_app(str(tmp_path))
    home = tmp_path / ".ironjarvis"
    with TestClient(app) as c:
        # v1.249.0 (R-05) made this response ADDITIVE: `mirror` rides along,
        # reading "off" until a backup copy folder is set in Settings.
        listing = c.get("/maintenance/backups").json()
        assert listing["dir"] == str(home / "backups") and listing["backups"] == []
        assert listing["mirror"] == {
            "dir": "", "media": True, "configured": False, "missing": False, "last": None,
        }
        assert c.put("/settings", json={"values": {"default_model": "before-restore"}}).status_code == 200
        assert c.post("/diagnostics/repair", json={"action": "backup_now"}).json()["ok"] is True
        listed = c.get("/maintenance/backups").json()["backups"]
        assert len(listed) == 1
        name = listed[0]["name"]
        assert re.match(r"^ironjarvis-backup-\d{8}-\d{6}\.tar\.gz$", name)
        assert listed[0]["bytes"] > 0 and listed[0]["modified_at"].endswith("+00:00")

        # drift after the snapshot: a setting on disk and a row in the DB
        c.put("/settings", json={"values": {"default_model": "after-backup"}})
        assert "after-backup" in (home / "config.toml").read_text(encoding="utf-8")
        assert c.post("/projects", json={"name": "ghost-after-backup"}).status_code == 200
        assert any(
            p["name"] == "ghost-after-backup" for p in c.get("/projects").json()["projects"]
        )

        r = c.post("/maintenance/restore", json={"name": name})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is True and body["restored_from"] == name
        assert body["files"] > 0 and body["restart"] == "scheduled"
        assert "before-restore" in (home / "config.toml").read_text(encoding="utf-8")
        # the DB is the snapshot again: the row born after the backup is gone
        assert not any(
            p["name"] == "ghost-after-backup" for p in c.get("/projects").json()["projects"]
        )
        assert not list(home.glob(".restore-*")), "staging dir must not linger"
        deadline = time.time() + 5
        while not stops and time.time() < deadline:
            time.sleep(0.05)
        assert stops == [1], "the stop that the confirm dialog promises never came"


def test_restore_refuses_bad_names_and_in_flight_work(tmp_path, monkeypatch):
    stops: list[int] = []
    monkeypatch.setattr(app_mod, "_graceful_stop", lambda: stops.append(1))
    app = create_app(str(tmp_path))
    with TestClient(app) as c:
        assert c.post("/diagnostics/repair", json={"action": "backup_now"}).json()["ok"] is True
        name = c.get("/maintenance/backups").json()["backups"][0]["name"]
        for bad in ("", "../x.tar.gz", "C:\\x\\ironjarvis-backup-1.tar.gz", "notes.txt",
                    "backups/" + name):
            assert c.post("/maintenance/restore", json={"name": bad}).status_code == 400, bad
        assert c.post("/maintenance/restore", json={"name": "ironjarvis-backup-none.tar.gz"}).status_code == 404

        from iron_jarvis.daemon.routes import system as system_routes

        monkeypatch.setattr(
            system_routes,
            "activity_snapshot",
            lambda d: {"active_sessions": 2, "running_workflow_runs": 0, "writing_workflow_runs": 0, "busy": True},
        )
        r = c.post("/maintenance/restore", json={"name": name})
        assert r.status_code == 409
        assert "2 session(s) running" in r.json()["detail"]
    assert stops == [], "a refused restore must not stop the daemon"


def test_restore_backup_live_stages_and_drops_the_old_wal(tmp_path):
    home = tmp_path / ".ironjarvis"
    home.mkdir()
    (home / "ironjarvis.db").write_bytes(b"live-db")
    (home / "ironjarvis.db-wal").write_bytes(b"stale-wal")
    (home / "ironjarvis.db-shm").write_bytes(b"stale-shm")
    (home / "config.toml").write_text("live", encoding="utf-8")
    (home / "keep-me.txt").write_text("not in archive", encoding="utf-8")
    src = tmp_path / "src" / ".ironjarvis"
    (src / "skills").mkdir(parents=True)
    (src / "ironjarvis.db").write_bytes(b"snapshot-db")
    (src / "config.toml").write_text("snapshot", encoding="utf-8")
    (src / "skills" / "a.md").write_text("skill", encoding="utf-8")
    archive = tmp_path / "ironjarvis-backup-20260905-000000.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        for p in src.rglob("*"):
            if p.is_file():
                tar.add(p, arcname=str(p.relative_to(src.parent)))

    moved = maintenance.restore_backup_live(home, archive)
    assert moved == 3
    assert (home / "ironjarvis.db").read_bytes() == b"snapshot-db"
    assert not (home / "ironjarvis.db-wal").exists() and not (home / "ironjarvis.db-shm").exists()
    assert (home / "config.toml").read_text(encoding="utf-8") == "snapshot"
    assert (home / "skills" / "a.md").read_text(encoding="utf-8") == "skill"
    assert (home / "keep-me.txt").exists()  # files the archive lacks are kept
    assert not list(home.glob(".restore-*"))

    # not a home archive (two top-level dirs): refused, nothing moved
    bad = tmp_path / "ironjarvis-backup-20260905-000001.tar.gz"
    with tarfile.open(bad, "w:gz") as tar:
        tar.add(src / "config.toml", arcname="a/config.toml")
        tar.add(src / "config.toml", arcname="b/config.toml")
    (home / "config.toml").write_text("untouched", encoding="utf-8")
    with pytest.raises(ValueError):
        maintenance.restore_backup_live(home, bad)
    assert (home / "config.toml").read_text(encoding="utf-8") == "untouched"


def test_cli_restore_uses_the_shared_extractor():
    from iron_jarvis.daemon import cli

    src = inspect.getsource(cli.restore)
    assert "extract_backup(Path(file), home.parent)" in src
    assert "extractall" not in src
