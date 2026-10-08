"""v1.315.0 (UX wave 3, T3 review fix): the SHAPE of a project task's stored
prompt is a contract with the dashboard.

``POST /projects/{id}/task`` does not store what the user typed as
``Session.task``: it stores the prompt it builds (a folder preamble line when
the project has a folder, ``Task: <text>``, a blank line, ``Deliverable: …``,
``Work autonomously…``), and a QUEUED run's assignment row carries the same
text. The Projects page's "Recent runs" list reads the user's words back out of
that prompt (``dashboard/components/project/ProjectTasks.tsx::runAsk`` — the
text after the first line-initial ``Task: `` up to the last
``\\n\\nDeliverable:``). If this prompt changes shape, every run in that list
silently goes back to showing two lines of machine preamble instead of the
user's request — so this file goes red first. Change ``runAsk`` and its vitest
cases (``dashboard/__tests__/ux-wave3-projects-v1315.test.tsx``) in the same
change set.

Driven through the REAL app factory, read back through ``GET /sessions`` (the
endpoint the page reads), never by asking the route's locals.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app

ASK = "tidy the folder"


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Hold the assignment dispatcher still: the queued case must stay a ROW.
    try:
        from iron_jarvis.assignments.dispatcher import AssignmentDispatcher

        async def _no_tick(self):
            return None

        monkeypatch.setattr(AssignmentDispatcher, "tick", _no_tick)
    except ImportError:
        pass
    with TestClient(create_app(str(tmp_path))) as c:
        yield c


def _mk_project(client, tmp_path, *, with_root: bool) -> dict:
    body: dict = {"name": "Acme" if with_root else "Rootless"}
    if with_root:
        root = tmp_path / "acme"
        root.mkdir(exist_ok=True)
        body["root"] = str(root)
    r = client.post("/projects", json=body)
    assert r.status_code in (200, 201), r.text
    return r.json()


def _wait_done(client, sid: str, seconds: float = 15) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        res = client.get(f"/sessions/{sid}").json()
        if (res.get("session") or res).get("status") in ("completed", "failed", "cancelled"):
            return
        time.sleep(0.2)


def _stored_task(client, project_id: str, sid: str) -> str:
    rows = client.get(f"/sessions?project_id={project_id}").json()["sessions"]
    mine = [s for s in rows if s["id"] == sid]
    assert mine, "the started run must be listed for its project"
    return mine[0]["task"]


def _run_ask(task: str) -> str:
    """The dashboard's ``runAsk``, restated: proves the stored shape yields the ask."""
    t = task.replace("\r\n", "\n")
    if t.startswith("Task: "):
        start = len("Task: ")
    else:
        i = t.find("\nTask: ")
        assert i >= 0, f"no line-initial 'Task: ' in {task!r}"
        start = i + len("\nTask: ")
    end = t.rfind("\n\nDeliverable:")
    assert end >= start, f"no '\\n\\nDeliverable:' after the ask in {task!r}"
    return t[start:end].strip()


def test_folder_project_task_stores_task_then_deliverable(client, tmp_path):
    p = _mk_project(client, tmp_path, with_root=True)
    r = client.post(f"/projects/{p['id']}/task", json={"text": ASK, "output": "chat"})
    assert r.status_code == 200, r.text
    sid = r.json()["id"]
    task = _stored_task(client, p["id"], sid)
    # The folder preamble comes FIRST, then the user's words on their own line.
    assert not task.startswith("Task: ")
    assert f"\nTask: {ASK}\n\nDeliverable:" in task
    assert _run_ask(task) == ASK
    _wait_done(client, sid)


def test_folder_project_file_deliverable_keeps_the_same_shape(client, tmp_path):
    p = _mk_project(client, tmp_path, with_root=True)
    r = client.post(f"/projects/{p['id']}/task", json={"text": ASK, "output": "md"})
    assert r.status_code == 200, r.text
    sid = r.json()["id"]
    task = _stored_task(client, p["id"], sid)
    assert f"\nTask: {ASK}\n\nDeliverable:" in task
    assert _run_ask(task) == ASK
    _wait_done(client, sid)


def test_folderless_project_task_starts_with_task(client, tmp_path):
    p = _mk_project(client, tmp_path, with_root=False)
    r = client.post(f"/projects/{p['id']}/task", json={"text": ASK, "output": "chat"})
    assert r.status_code == 200, r.text
    sid = r.json()["id"]
    task = _stored_task(client, p["id"], sid)
    assert task.startswith(f"Task: {ASK}\n\nDeliverable:")
    assert _run_ask(task) == ASK
    _wait_done(client, sid)


def test_queued_project_task_row_has_the_same_shape(client, tmp_path):
    p = _mk_project(client, tmp_path, with_root=True)
    r = client.post(
        f"/projects/{p['id']}/task",
        json={"text": ASK, "output": "chat", "assignee": "builder"},
    )
    assert r.status_code == 202, r.text
    row = r.json()["assignment"]
    # The dispatcher appends "\n\nWhy this was assigned: <reason>" when it
    # starts the session; the ask still sits before the LAST Deliverable line.
    assert row["reason"] == "project task"
    assert f"\nTask: {ASK}\n\nDeliverable:" in row["task"]
    started_as = f"{row['task']}\n\nWhy this was assigned: {row['reason']}"
    assert _run_ask(started_as) == ASK
