"""v1.446.0 (ease-of-use review, item 7): a restore can be undone, and it
refuses in plain words while anything the update warning counts is running.

Before: "Restore and restart" replaced the database and settings with no copy
of the state it replaced (restoring the wrong archive lost everything since),
its "something is running" check counted only sessions and workflow runs, and
its refusal read "cannot restore while work is in flight … out from under
them". Now the route:

- reads the update warning's busy reading (``activity_snapshot``'s ``busy`` and
  its parts) plus running night tasks, and names what runs;
- writes an ordinary ``ironjarvis-backup-<stamp>-before-restore.tar.gz`` of the
  current state BEFORE anything moves and names it (``saved_as``, ``undo``);
- refuses, changing nothing, when that backup cannot be written.
"""

from __future__ import annotations

import re
import tarfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iron_jarvis import maintenance
from iron_jarvis.core import home_lock
from iron_jarvis.daemon import app as app_mod
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import system as system_routes


@pytest.fixture
def stops(monkeypatch):
    seen: list[int] = []
    monkeypatch.setattr(app_mod, "_graceful_stop", lambda: seen.append(1))
    return seen


def _backup(c: TestClient) -> str:
    assert c.post("/diagnostics/repair", json={"action": "backup_now"}).json()["ok"] is True
    return c.get("/maintenance/backups").json()["backups"][0]["name"]


def _model_in_config(home: Path) -> str:
    text = (home / "config.toml").read_text(encoding="utf-8")
    m = re.search(r'^default_model\s*=\s*"([^"]*)"', text, re.M)
    return m.group(1) if m else ""


def _quiet(**over):
    base = {
        "active_sessions": 0, "running_workflow_runs": 0, "writing_workflow_runs": 0,
        "chat_replies": 0, "busy_panes": 0, "busy_pane_clis": [], "code_replies": 0,
        "code_tools": [], "code_serving": 0, "code_serving_tools": [], "busy": False,
    }
    base.update(over)
    return base


# --------------------------------------------------------------------------- #
# The safety backup
# --------------------------------------------------------------------------- #


def test_a_restore_saves_the_current_state_first_and_that_backup_undoes_it(tmp_path, stops):
    app = create_app(str(tmp_path))
    home = tmp_path / ".ironjarvis"
    with TestClient(app) as c:
        assert c.put("/settings", json={"values": {"default_model": "old-state"}}).status_code == 200
        old = _backup(c)
        # Work since that backup: the state a wrong restore used to lose.
        assert c.put("/settings", json={"values": {"default_model": "today-state"}}).status_code == 200
        assert c.post("/projects", json={"name": "made-today"}).status_code == 200

        r = c.post("/maintenance/restore", json={"name": old})
        assert r.status_code == 200, r.text
        body = r.json()
        saved = body["saved_as"]
        assert re.match(r"^ironjarvis-backup-\d{8}-\d{6}-before-restore\.tar\.gz$", saved), saved
        assert body["undo"] == f"Your previous state was saved as {saved}. Restore it to undo this."
        assert _model_in_config(home) == "old-state"
        assert not any(p["name"] == "made-today" for p in c.get("/projects").json()["projects"])

        # It is an ordinary backup: listed, and it holds TODAY's state.
        names = [b["name"] for b in c.get("/maintenance/backups").json()["backups"]]
        assert saved in names and old in names
        with tarfile.open(home / "backups" / saved, "r:gz") as tar:
            cfg = tar.extractfile(".ironjarvis/config.toml").read().decode("utf-8")
        assert "today-state" in cfg

        # Restoring it undoes the restore.
        r2 = c.post("/maintenance/restore", json={"name": saved})
        assert r2.status_code == 200, r2.text
        assert _model_in_config(home) == "today-state"
        assert any(p["name"] == "made-today" for p in c.get("/projects").json()["projects"])
        # The undo kept a copy of the state IT replaced too, under a new name.
        assert r2.json()["saved_as"] != saved


def test_the_safety_backup_leaves_out_the_live_home_lock(tmp_path, stops):
    app = create_app(str(tmp_path))
    home = tmp_path / ".ironjarvis"
    with TestClient(app) as c:
        old = _backup(c)
        before = home_lock._HELD
        fh = home_lock.acquire(home, port=8797, wait_s=0)  # held as the daemon holds it
        try:
            r = c.post("/maintenance/restore", json={"name": old})
        finally:
            fh.close()
            home_lock._HELD = before
        assert r.status_code == 200, r.text
        with tarfile.open(home / "backups" / r.json()["saved_as"], "r:gz") as tar:
            names = {Path(m.name).name for m in tar.getmembers()}
        assert home_lock.LOCK_NAME not in names and home_lock.OWNER_NAME not in names
        assert "ironjarvis.db" in names


def test_no_safety_backup_means_no_restore_and_nothing_changes(tmp_path, stops, monkeypatch):
    app = create_app(str(tmp_path))
    home = tmp_path / ".ironjarvis"
    restores: list[int] = []
    monkeypatch.setattr(maintenance, "restore_backup_live", lambda *a: restores.append(1) or 0)

    def full(home_, *, engine=None):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(maintenance, "write_safety_backup", full)
    with TestClient(app) as c:
        assert c.put("/settings", json={"values": {"default_model": "old-state"}}).status_code == 200
        old = _backup(c)
        assert c.put("/settings", json={"values": {"default_model": "today-state"}}).status_code == 200
        r = c.post("/maintenance/restore", json={"name": old})
        assert r.status_code == 409
        detail = r.json()["detail"]
        assert detail.startswith("Nothing was restored. Iron Jarvis couldn't save a backup of your current state first")
        assert "The disk is full" in detail
    assert restores == []
    assert _model_in_config(home) == "today-state"
    assert stops == []


def test_a_refusal_for_running_work_writes_no_backup(tmp_path, stops, monkeypatch):
    app = create_app(str(tmp_path))
    written: list[int] = []
    real = maintenance.write_safety_backup
    monkeypatch.setattr(
        maintenance, "write_safety_backup", lambda *a, **k: (written.append(1), real(*a, **k))[1]
    )
    with TestClient(app) as c:
        old = _backup(c)
        monkeypatch.setattr(system_routes, "activity_snapshot", lambda d: _quiet(chat_replies=1, busy=True))
        assert c.post("/maintenance/restore", json={"name": old}).status_code == 409
    assert written == []
    assert stops == []


def test_the_scheduled_backup_is_not_put_off_by_a_safety_backup(tmp_path):
    home = tmp_path / ".ironjarvis"
    out = maintenance.write_safety_backup(home)
    assert maintenance.is_safety_backup(out.name)
    # Only the safety backup exists: the daily snapshot is still due now.
    assert maintenance.boot_backup_delay_s(home, 86_400.0) == 0.0
    # A second one the same second gets its own name, never overwriting.
    again = maintenance.write_safety_backup(home)
    assert again != out and again.exists() and out.exists()


# --------------------------------------------------------------------------- #
# The busy reading and its words
# --------------------------------------------------------------------------- #


def test_a_chat_reply_and_a_coding_session_are_named(tmp_path, stops, monkeypatch):
    app = create_app(str(tmp_path))
    with TestClient(app) as c:
        old = _backup(c)
        # CHAT_INFLIGHT counts both; the durable registry says one is Chat › Code.
        monkeypatch.setattr(system_routes, "activity_snapshot", lambda d: _quiet(chat_replies=2, busy=True))
        monkeypatch.setattr(system_routes, "_replies_running", lambda d: {"chat": 1, "build": 1})
        r = c.post("/maintenance/restore", json={"name": old})
    assert r.status_code == 409
    assert r.json()["detail"] == (
        "1 chat reply and 1 coding session are still running. "
        "Wait for them to finish, or stop them, then restore."
    )
    assert stops == []


def test_what_the_old_check_missed_now_refuses(tmp_path, stops, monkeypatch):
    app = create_app(str(tmp_path))
    cases = [
        (_quiet(code_replies=1, busy=True), "1 coding session in Build › Code is still running."),
        (_quiet(busy_panes=2, busy=True), "2 Build terminals are still running."),
        (_quiet(code_serving=1, busy=True),
         "1 coding session in Build › Code is running a program it started."),
        # a workflow run waiting for an answer is replaced by a restore too
        (_quiet(running_workflow_runs=1, busy=True), "1 workflow run is still running."),
        # busy with nothing named still refuses, in plain words
        (_quiet(busy=True), "Something is still running."),
    ]
    with TestClient(app) as c:
        old = _backup(c)
        for act, words in cases:
            monkeypatch.setattr(system_routes, "activity_snapshot", lambda d, act=act: act)
            r = c.post("/maintenance/restore", json={"name": old})
            assert r.status_code == 409, act
            assert r.json()["detail"].startswith(words), r.json()["detail"]
            assert "in flight" not in r.json()["detail"]
    assert stops == []


def test_a_running_night_task_refuses(tmp_path, stops, monkeypatch):
    from iron_jarvis.harness import night

    app = create_app(str(tmp_path))
    with TestClient(app) as c:
        old = _backup(c)
        monkeypatch.setattr(system_routes, "activity_snapshot", lambda d: _quiet(chat_replies=1, busy=True))
        monkeypatch.setattr(system_routes, "_replies_running", lambda d: {"chat": 0, "build": 1})
        monkeypatch.setattr(night, "rows_in", lambda engine, state: [{"id": "n1"}] if state == night.RUNNING else [])
        r = c.post("/maintenance/restore", json={"name": old})
    assert r.status_code == 409
    # Its turn is a Chat › Code reply, named once as the night task.
    assert r.json()["detail"].startswith("1 night task is still running.")


def test_the_busy_words_are_plain():
    w = maintenance.restore_busy_words
    assert w({}) == ""
    assert w({"chat_replies": 1}) == (
        "1 chat reply is still running. Wait for it to finish, or stop it, then restore."
    )
    assert w({"chat_replies": 2, "terminals": 1, "background_tasks": 3}) == (
        "2 chat replies, 1 Build terminal and 3 background tasks are still running. "
        "Wait for them to finish, or stop them, then restore."
    )
    assert w({"code_serving": 2}) == (
        "2 coding sessions in Build › Code are running programs they started. "
        "Wait for them to finish, or stop them, then restore."
    )
    assert w({"workflow_runs": "x", "night_tasks": -1}) == ""
