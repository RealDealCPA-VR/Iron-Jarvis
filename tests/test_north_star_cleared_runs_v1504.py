"""North-star plan 4.2 (D7): "Clear completed" and a run's Delete MOVE.

Before v1.504.0 ``POST /sessions/clear`` looped ``delete_session`` over up to
1,000 runs: their rows were deleted for good and the folder holding the files
each run wrote was ``rmtree``'d, while the button said "Remove every completed
session from the board". Now the folder moves to the app's trash, the run's
record is written beside it (kept 30 days), and the user's own folder is never
touched.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from fastapi.testclient import TestClient

from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.core.models import AgentType
from iron_jarvis.daemon.app import create_app
from iron_jarvis.maintenance import (
    CLEARED_RECORD_NAME,
    TRASH_DIRNAME,
    sweep_cleared_records,
)


def _run(client: TestClient, task: str) -> tuple[str, Path]:
    s = client.post("/sessions", json={"task": task, "wait": True}).json()
    sid = s["id"]
    body = client.get(f"/sessions/{sid}").json()
    ws = Path(body["session"]["workspace_path"])
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "report.txt").write_text(f"what {task} wrote", encoding="utf-8")
    return sid, ws


def test_clear_completed_moves_each_runs_folder_to_the_trash(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    a, ws_a = _run(client, "a")
    b, ws_b = _run(client, "b")

    res = client.post("/sessions/clear", json={"statuses": ["completed"]}).json()

    assert res["cleared"] >= 2
    trash = Path(res["trash"])
    assert trash.is_dir() and trash.parent.name == TRASH_DIRNAME
    # The folders are gone from where they were…
    assert not ws_a.exists() and not ws_b.exists()
    # …and every file the runs wrote is in the trash, unchanged.
    found = {p.read_text(encoding="utf-8") for p in trash.rglob("report.txt")}
    assert found == {"what a wrote", "what b wrote"}
    assert res["folders_moved"] >= 2
    # The board no longer lists them.
    left = {s["id"] for s in client.get("/sessions").json()["sessions"]}
    assert a not in left and b not in left


def test_clear_keeps_each_runs_record_in_the_trash(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    a, _ws = _run(client, "write the summary")

    res = client.post("/sessions/clear", json={"statuses": ["completed"]}).json()

    records = list(Path(res["trash"]).rglob(CLEARED_RECORD_NAME))
    assert len(records) == res["cleared"]
    mine = [json.loads(r.read_text(encoding="utf-8")) for r in records]
    rec = next(r for r in mine if (r["session"] or {}).get("id") == a)
    assert rec["session"]["task"] == "write the summary"
    assert rec["runs"], "the run rows are kept in the record"
    assert all(run["session_id"] == a for run in rec["runs"])
    assert res["record_days"] == 30


def test_a_runs_own_delete_moves_its_folder_too(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    a, ws = _run(client, "one run")

    res = client.delete(f"/sessions/{a}").json()

    assert res["deleted"] == a
    assert not ws.exists()
    moved = Path(res["files"]) / "report.txt"
    assert moved.read_text(encoding="utf-8") == "what one run wrote"
    assert (Path(res["trash"]) / CLEARED_RECORD_NAME).is_file()


async def test_the_users_own_folder_is_never_moved(platform, tmp_path):
    folder = tmp_path / "client-folder"
    folder.mkdir()
    (folder / "return.txt").write_text("the user's own file", encoding="utf-8")
    orch = Orchestrator(platform)
    sess = await orch.create_session("read it", AgentType.BUILDER, workspace_root=str(folder))

    out = orch.delete_session(sess.id)

    assert (folder / "return.txt").read_text(encoding="utf-8") == "the user's own file"
    assert out["files"] == ""
    # The record is still kept in the trash.
    assert (Path(out["trash"]) / CLEARED_RECORD_NAME).is_file()


async def test_a_record_that_cannot_be_kept_stops_the_delete(platform, monkeypatch):
    orch = Orchestrator(platform)
    sess = await orch.run("scratch task", AgentType.BUILDER)
    ws = Path(sess.workspace_path)
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "kept.txt").write_text("still here", encoding="utf-8")

    def _full_disk(self, *a, **k):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(Path, "write_text", _full_disk)
    try:
        orch.delete_session(sess.id)
    except OSError:
        pass
    else:  # pragma: no cover - the assertion below says why
        raise AssertionError("the delete went ahead without keeping the record")
    monkeypatch.undo()
    assert orch.get_session(sess.id) is not None
    assert (ws / "kept.txt").read_text(encoding="utf-8") == "still here"


def test_cleared_records_are_removed_after_30_days_and_files_stay(tmp_path):
    home = tmp_path
    run = home / TRASH_DIRNAME / "20261001-000000" / "cleared-runs" / "sess_old"
    (run / "files").mkdir(parents=True)
    (run / "files" / "out.txt").write_text("a file the run wrote", encoding="utf-8")
    rec = run / CLEARED_RECORD_NAME
    rec.write_text("{}", encoding="utf-8")
    young = home / TRASH_DIRNAME / "20261101-000000" / "cleared-runs" / "sess_new" / CLEARED_RECORD_NAME
    young.parent.mkdir(parents=True)
    young.write_text("{}", encoding="utf-8")
    now = time.time()
    os.utime(rec, (now - 31 * 86400, now - 31 * 86400))
    os.utime(young, (now - 29 * 86400, now - 29 * 86400))

    assert sweep_cleared_records(home, now=now) == 1

    assert not rec.exists()
    assert young.exists()
    assert (run / "files" / "out.txt").read_text(encoding="utf-8") == "a file the run wrote"


async def test_the_daily_sweep_removes_old_cleared_records(platform, monkeypatch):
    """The wiring: ``daemon/app._event_prune_loop`` runs the 30-day sweep."""
    import asyncio

    from iron_jarvis import maintenance as maint
    from iron_jarvis.daemon import app as app_mod

    seen: list[str] = []
    monkeypatch.setattr(maint, "sweep_cleared_records", lambda home, **k: seen.append(str(home)) or 0)
    task = asyncio.create_task(app_mod._event_prune_loop(platform, interval_s=0))
    try:
        deadline = time.monotonic() + 10  # a liveness cap, not a speed bar
        while not seen:
            assert time.monotonic() < deadline, "the daily sweep never ran the cleared-record sweep"
            await asyncio.sleep(0.01)
    finally:
        task.cancel()
    assert seen[0] == str(platform.config.home)
