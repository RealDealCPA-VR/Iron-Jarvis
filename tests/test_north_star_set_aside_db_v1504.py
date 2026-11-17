"""North-star plan 4.3: a database set aside at start is shown, and backups wait.

``open_db`` renames a damaged database aside and starts EMPTY, and said so only
in the log; the daily backup then kept the newest 7 archives, so within a week
every archive that held the real data was rotated out. Now a marker is kept
(which file, when, why), Everything › Status, Settings and ``doctor`` show it
with one press to restore the backup from before it, and no archive older than
the marker is pruned while it is unanswered.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from iron_jarvis import maintenance
from iron_jarvis.core.db import (
    SET_ASIDE_MARKER,
    answer_set_aside_marker,
    open_db,
    read_set_aside_marker,
    write_set_aside_marker,
)
from iron_jarvis.daemon import app as app_mod
from iron_jarvis.daemon.app import create_app


def _archives(home: Path, ages_days: list[float]) -> list[Path]:
    """Fake archives (the rotation reads only names and modified times)."""
    out = []
    now = time.time()
    backups = home / maintenance.BACKUP_DIRNAME
    backups.mkdir(parents=True, exist_ok=True)
    for i, age in enumerate(ages_days):
        p = backups / f"ironjarvis-backup-2026100{i}-000000.tar.gz"
        p.write_bytes(b"x")
        os.utime(p, (now - age * 86400, now - age * 86400))
        out.append(p)
    return out


def test_a_damaged_database_set_aside_leaves_a_marker(tmp_path):
    db = tmp_path / "ironjarvis.db"
    db.write_bytes(b"this is not a database at all, it is damaged")

    engine = open_db(db)
    engine.dispose()

    marker = read_set_aside_marker(tmp_path)
    assert marker is not None, "the set-aside was said only in the log"
    assert Path(marker["file"]).name.startswith("ironjarvis.db.corrupt-")
    assert Path(marker["file"]).is_file()
    assert "bad header" in marker["reason"]
    assert datetime.fromisoformat(marker["at"]).tzinfo is not None


def test_backups_older_than_an_unanswered_set_aside_are_never_pruned(tmp_path):
    home = tmp_path
    old = _archives(home, [12, 11, 10, 9, 8, 7, 6, 5])  # the real data
    write_set_aside_marker(home, home / "ironjarvis.db.corrupt-x", "test", at=datetime.now(timezone.utc) - timedelta(days=4))

    maintenance.run_auto_backup(home, keep=3, include_keys=False)

    assert all(p.exists() for p in old), "an archive holding the real data was pruned"


def test_once_answered_the_rotation_prunes_as_usual(tmp_path):
    home = tmp_path
    old = _archives(home, [12, 11, 10, 9, 8])
    write_set_aside_marker(home, home / "ironjarvis.db.corrupt-x", "test", at=datetime.now(timezone.utc) - timedelta(days=4))
    assert answer_set_aside_marker(home, "kept_fresh") is True

    maintenance.run_auto_backup(home, keep=3, include_keys=False)

    assert sum(p.exists() for p in old) == 2  # 3 kept = the new one + the 2 newest
    # The answer stays on record.
    assert '"answer": "kept_fresh"' in (home / SET_ASIDE_MARKER).read_text(encoding="utf-8")


def test_the_mirror_keeps_them_too(tmp_path):
    home = tmp_path / "home"
    mirror = tmp_path / "mirror"
    mirror.mkdir()
    now = time.time()
    held = []
    for i in range(5):
        p = mirror / f"ironjarvis-backup-2026090{i}-000000.tar.gz"
        p.write_bytes(b"x")
        os.utime(p, (now - (10 + i) * 86400,) * 2)
        held.append(p)
    archive = tmp_path / "ironjarvis-backup-20261115-000000.tar.gz"
    archive.write_bytes(b"new")

    maintenance.mirror_backup(home, archive, str(mirror), keep=2, media=False, hold_before=now - 86400)

    assert all(p.exists() for p in held)


def test_status_names_the_backup_from_before_it_and_one_press_restores_it(tmp_path, monkeypatch):
    stops: list[int] = []
    monkeypatch.setattr(app_mod, "_graceful_stop", lambda: stops.append(1))
    home = tmp_path / ".ironjarvis"
    with TestClient(create_app(str(tmp_path))) as c:
        assert c.get("/maintenance/set-aside").json() == {"set_aside": None, "latest_good": None}
        assert c.post("/projects", json={"name": "real-work"}).status_code == 200
        assert c.post("/diagnostics/repair", json={"action": "backup_now"}).json()["ok"] is True
        good = c.get("/maintenance/backups").json()["backups"][0]["name"]
        an_hour_ago = time.time() - 3600
        os.utime(home / "backups" / good, (an_hour_ago, an_hour_ago))
        # The damage: set aside now; the app went on with an empty database.
        write_set_aside_marker(home, home / "ironjarvis.db.corrupt-20261115-000000", "confirmed corrupt database at init")
        c.post("/projects", json={"name": "made-after"})

        status = c.get("/maintenance/set-aside").json()
        assert status["set_aside"]["reason"] == "confirmed corrupt database at init"
        assert status["latest_good"]["name"] == good

        r = c.post("/maintenance/set-aside/restore")
        assert r.status_code == 200, r.text
        assert r.json()["restored_from"] == good
        names = [p["name"] for p in c.get("/projects").json()["projects"]]
        assert "real-work" in names and "made-after" not in names
        assert c.get("/maintenance/set-aside").json()["set_aside"] is None
        deadline = time.time() + 5
        while not stops and time.time() < deadline:
            time.sleep(0.05)
        assert stops == [1]


def test_keep_the_fresh_start_answers_it(tmp_path):
    home = tmp_path / ".ironjarvis"
    with TestClient(create_app(str(tmp_path))) as c:
        r = c.post("/maintenance/set-aside/restore")
        assert r.status_code == 409 and "nothing to restore" in r.json()["detail"]
        write_set_aside_marker(home, home / "ironjarvis.db.corrupt-x", "test")
        r = c.post("/maintenance/set-aside/restore")
        assert r.status_code == 409 and "no backup from before" in r.json()["detail"]
        assert c.post("/maintenance/set-aside/keep").json() == {"answered": True}
        assert c.get("/maintenance/set-aside").json()["set_aside"] is None


def test_doctor_says_it_and_names_the_backup(tmp_path, monkeypatch):
    from iron_jarvis import onboarding
    from iron_jarvis.daemon import cli

    monkeypatch.setattr(onboarding, "doctor", lambda: {"ok": True, "checks": []})
    monkeypatch.setenv("IRONJARVIS_HOME", str(tmp_path / "home"))
    home = tmp_path / "home"
    (good,) = _archives(home, [3])
    write_set_aside_marker(home, home / "ironjarvis.db.corrupt-y", "test", at=datetime.now(timezone.utc) - timedelta(days=1))

    res = CliRunner().invoke(cli.app, ["doctor", "--root", str(tmp_path)], env={"COLUMNS": "400"})

    assert res.exit_code == 0, res.output
    out = " ".join(res.output.split())
    assert "database set aside" in out
    # The file name whatever width the table was drawn at: on GitHub's Linux
    # machines it came out 80 wide whatever COLUMNS said, and the name was
    # cut across two rows.
    flat = "".join(ch for ch in res.output if not ch.isspace() and ch not in "\u2502\u2503")
    assert good.name in flat
    assert "Some required checks failed" in out


def test_the_home_line_counts_an_unanswered_set_aside_and_stops_once_answered(tmp_path):
    """The Home page's one line (``GET /ui/status-line``) says it first: the
    Status page alone is a page nobody opens on a morning the app is empty."""
    with TestClient(create_app(str(tmp_path))) as c:
        home = Path(c.app.state.platform.config.home)
        assert c.get("/ui/status-line").json()["database_set_aside"] == 0
        write_set_aside_marker(home, home / "ironjarvis.db.corrupt-x", "test")
        assert c.get("/ui/status-line").json()["database_set_aside"] == 1
        assert c.post("/maintenance/set-aside/keep").status_code == 200
        assert c.get("/ui/status-line").json()["database_set_aside"] == 0


def test_repair_restores_the_backup_from_before_the_set_aside_not_a_later_empty_one(tmp_path, monkeypatch):
    """While a set-aside is unanswered, the NEWEST archive may be a backup of
    the empty database Iron Jarvis started with since. ``repair`` restores the
    newest one made BEFORE it."""
    import sqlite3

    from iron_jarvis.daemon import cli

    home = tmp_path / "home"
    (home / "backups").mkdir(parents=True)
    db = home / "ironjarvis.db"

    def write_db(word: str) -> None:
        if db.exists():
            db.unlink()
        con = sqlite3.connect(str(db))
        con.execute("create table kept (word text)")
        con.execute("insert into kept values (?)", (word,))
        con.commit()
        con.close()

    def archive(name: str, word: str, age_days: float) -> Path:
        write_db(word)
        out, _n = maintenance.create_backup(home, home / "backups" / f"ironjarvis-backup-{name}.tar.gz")
        when = time.time() - age_days * 86400
        os.utime(out, (when, when))
        return out

    good = archive("20261110-000000", "the owner's work", 3)
    archive("20261114-000000", "empty", 0.05)
    write_set_aside_marker(home, home / "ironjarvis.db.corrupt-y", "test",
                           at=datetime.now(timezone.utc) - timedelta(days=1))
    db.write_bytes(b"this is not a database at all" * 200)  # damaged again, now
    monkeypatch.setenv("IRONJARVIS_HOME", str(home))

    res = CliRunner().invoke(cli.app, ["repair", "--root", str(tmp_path), "--no-sync"], env={"COLUMNS": "400"})

    assert res.exit_code == 0, res.output
    assert good.name in " ".join(res.output.split()), res.output
    con = sqlite3.connect(str(db))
    try:
        assert con.execute("select word from kept").fetchone() == ("the owner's work",)
    finally:
        con.close()
