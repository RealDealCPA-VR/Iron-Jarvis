"""A project applies ONLY inside the Projects module: chat threads and workflow
runs started elsewhere are project-agnostic and never inherit the globally-active
project. Only an EXPLICIT project_id tags a thread."""

from __future__ import annotations

from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app


def _mk(tmp_path):
    return TestClient(create_app(str(tmp_path)))


def test_new_chat_thread_is_project_agnostic(tmp_path):
    client = _mk(tmp_path)
    p = client.post("/projects", json={"name": "Acme"}).json()
    pid = p.get("id") or p.get("project", {}).get("id")
    assert pid  # first project auto-activates

    # A main-chat thread (no explicit project_id) is NOT tagged to the active one.
    saved = client.put(
        "/chat/threads/new", json={"messages": [{"role": "user", "content": "hi"}]}
    ).json()
    assert saved["project_id"] is None
    detail = client.get(f"/chat/threads/{saved['id']}").json()
    assert detail["project_id"] is None


def test_thread_tagged_only_when_project_id_is_explicit(tmp_path):
    """The in-project chat sends an explicit project_id — that DOES tag the
    thread. An explicit null clears it; an update without the key preserves it."""
    client = _mk(tmp_path)
    pid = client.post("/projects", json={"name": "Acme"}).json()["id"]

    tagged = client.put(
        "/chat/threads/new",
        json={"messages": [{"role": "user", "content": "hi"}], "project_id": pid},
    ).json()
    assert tagged["project_id"] == pid

    # Explicit null clears; a later update WITHOUT project_id doesn't clobber.
    cleared = client.put(
        "/chat/threads/new",
        json={"messages": [{"role": "user", "content": "hi"}], "project_id": None},
    ).json()
    assert cleared["project_id"] is None
    client.put(
        f"/chat/threads/{tagged['id']}",
        json={"messages": [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "yo"}]},
    )
    assert client.get(f"/chat/threads/{tagged['id']}").json()["project_id"] == pid


def test_workflow_run_is_project_agnostic(tmp_path):
    client = _mk(tmp_path)
    client.post("/projects", json={"name": "Acme"})  # auto-active

    r = client.post(
        "/workflows/run",
        json={"name": "spine-test", "steps": [{"name": "s1", "agent": "builder", "task": "say hi"}]},
    )
    assert r.status_code == 200
    runs = client.get("/workflows/runs").json()["runs"]
    # A workflow run is NOT stamped with whatever project happens to be active.
    assert runs and all(run.get("project_id") is None for run in runs)
