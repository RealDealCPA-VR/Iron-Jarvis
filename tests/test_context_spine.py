"""Context spine: chat threads + workflow runs carry the active project."""

from __future__ import annotations

from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app


def _mk(tmp_path):
    return TestClient(create_app(str(tmp_path)))


def test_new_chat_thread_inherits_active_project(tmp_path):
    client = _mk(tmp_path)
    p = client.post("/projects", json={"name": "Acme"}).json()
    pid = p.get("id") or p.get("project", {}).get("id")
    assert pid  # first project auto-activates

    saved = client.put(
        "/chat/threads/new", json={"messages": [{"role": "user", "content": "hi"}]}
    ).json()
    assert saved["project_id"] == pid

    listed = client.get("/chat/threads").json()["threads"]
    assert listed[0]["project_id"] == pid
    detail = client.get(f"/chat/threads/{saved['id']}").json()
    assert detail["project_id"] == pid


def test_thread_explicit_project_tag_and_clear(tmp_path):
    client = _mk(tmp_path)
    client.post("/projects", json={"name": "Acme"})
    saved = client.put(
        "/chat/threads/new",
        json={"messages": [{"role": "user", "content": "hi"}], "project_id": None},
    ).json()
    assert saved["project_id"] is None  # explicit null beats the active default

    # Updating WITHOUT project_id must not clobber an existing tag.
    client.put(
        f"/chat/threads/{saved['id']}",
        json={"messages": [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "yo"}]},
    )
    assert client.get(f"/chat/threads/{saved['id']}").json()["project_id"] is None


def test_workflow_run_stamped_with_active_project(tmp_path):
    client = _mk(tmp_path)
    p = client.post("/projects", json={"name": "Acme"}).json()
    pid = p.get("id") or p.get("project", {}).get("id")

    r = client.post(
        "/workflows/run",
        json={"name": "spine-test", "steps": [{"name": "s1", "agent": "builder", "task": "say hi"}]},
    )
    assert r.status_code == 200
    runs = client.get("/workflows/runs").json()["runs"]
    assert runs and any(run.get("project_id") == pid for run in runs)
