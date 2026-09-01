"""Projects-module review (v1.220.0): the gaps a code review of the module
found, each pinned by the behaviour that used to be wrong.

1. DELETE left every automation bound to the project still bound — task
   schedules (payload project_id), goal contracts and reflex rules kept
   spawning sessions tagged to the deleted id, and the runtime grounded
   them in nothing, silently.
2. An ARCHIVED project accepted new tasks through the API; the rule lived in
   one page's JSX only (the chat module's Tasks surface bypassed it).
3. A project ROOT was never checked against the file policy the
   ``POST /sessions`` workspace_root door applies — a protected folder was
   accepted and every task in it ran with every write refused.
4. The DELIVERABLE filename kept Windows-reserved characters and ``..``; the
   agent worked the whole task and failed on the final write.
5. Knowledge FILE uploads were staged at ``<home>/uploads/<name>`` — the same
   path ``POST /documents/upload`` stores the user's documents at — so a
   knowledge upload overwrote a same-named document, and the staged copy was
   never removed.
"""

from __future__ import annotations

import base64
import json

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app


def _client(tmp_path):
    return TestClient(create_app(str(tmp_path)))


def _project(client, tmp_path, name="Acme", with_root=False):
    body = {"name": name}
    if with_root:
        root = tmp_path / "acme"
        root.mkdir(exist_ok=True)
        body["root"] = str(root)
    return client.post("/projects", json=body).json()


# --- 1. delete untags the automation that would keep spawning into the ghost --


def test_delete_untags_schedules_goals_and_reflex_rules(tmp_path):
    with _client(tmp_path) as client:
        pid = _project(client, tmp_path)["id"]
        other = _project(client, tmp_path, name="Other")["id"]

        # A task schedule bound to the project (project_id rides in the payload).
        r = client.post(
            "/schedules",
            json={
                "name": "nightly",
                "cron": "0 9 * * *",
                "kind": "task",
                "payload": {"task": "summarize the day", "project_id": pid},
            },
        )
        assert r.status_code == 200, r.text
        # One bound to ANOTHER project must be left alone.
        r = client.post(
            "/schedules",
            json={
                "name": "theirs",
                "cron": "0 9 * * *",
                "kind": "task",
                "payload": {"task": "x", "project_id": other},
            },
        )
        assert r.status_code == 200, r.text
        # A goal contract grounded in the project.
        r = client.post(
            "/goals",
            json={
                "name": "inbox zero",
                "contract_text": "Create a file summarizing the task.",
                "budget": {"max_tokens": 1_000_000},
                "project_id": pid,
            },
        )
        assert r.status_code == 200, r.text
        gid = r.json()["goal"]["id"]
        assert r.json()["goal"]["project_id"] == pid
        # A reflex rule grounded in the project.
        r = client.post(
            "/reflex/rules",
            json={"name": "n", "source": "comm", "match": "x", "action": "session",
                  "project_id": pid},
        )
        assert r.status_code == 200, r.text
        rid = r.json()["id"]

        out = client.delete(f"/projects/{pid}").json()
        assert out["deleted"] == pid
        assert out["untagged"]["schedules"] == 1
        assert out["untagged"]["goals"] == 1
        assert out["untagged"]["reflex_rules"] == 1

        rows = {t["name"]: t for t in client.get("/schedules").json()["schedules"]}
        assert rows["nightly"]["project_id"] == ""
        assert "project_id" not in json.loads(rows["nightly"]["payload_json"])
        # The task itself survives the untag — only the dead pin is gone.
        assert json.loads(rows["nightly"]["payload_json"])["task"] == "summarize the day"
        assert rows["theirs"]["project_id"] == other

        assert client.get(f"/goals/{gid}").json()["goal"]["project_id"] is None
        rule = next(x for x in client.get("/reflex/rules").json()["rules"] if x["id"] == rid)
        assert rule["project_id"] is None


def test_delete_reports_untag_counts_for_sessions_and_threads(tmp_path):
    with _client(tmp_path) as client:
        pid = _project(client, tmp_path)["id"]
        client.post("/sessions", json={"task": "x", "wait": True, "project_id": pid})
        client.put("/chat/threads/new", json={"messages": [], "title": "t", "project_id": pid})
        out = client.delete(f"/projects/{pid}").json()
        assert out["untagged"]["sessions"] == 1
        assert out["untagged"]["threads"] == 1
        assert out["untagged"]["workflow_runs"] == 0


# --- 2. an archived project refuses new tasks at the API ------------------------


def test_archived_project_refuses_new_tasks(tmp_path):
    with _client(tmp_path) as client:
        pid = _project(client, tmp_path)["id"]
        assert client.patch(f"/projects/{pid}", json={"status": "archived"}).status_code == 200
        r = client.post(f"/projects/{pid}/task", json={"text": "hello", "output": "chat"})
        assert r.status_code == 400
        assert "unarchive" in r.json()["detail"]
        # Unarchiving reopens the door.
        client.patch(f"/projects/{pid}", json={"status": "active"})
        ok = client.post(f"/projects/{pid}/task", json={"text": "hello", "output": "chat"})
        assert ok.status_code == 200
        client.post(f"/sessions/{ok.json()['id']}/cancel")


# --- 3. a protected folder is refused as a project root -------------------------


def test_protected_folder_is_refused_as_root(tmp_path):
    from iron_jarvis.core.fs_policy import register_protected_root

    vault = tmp_path / "vault"
    vault.mkdir()
    register_protected_root(vault)
    with _client(tmp_path) as client:
        r = client.post("/projects", json={"name": "P", "root": str(vault)})
        assert r.status_code == 400
        assert "protected" in r.json()["detail"]
        # Same door on PATCH.
        pid = _project(client, tmp_path)["id"]
        r = client.patch(f"/projects/{pid}", json={"root": str(vault)})
        assert r.status_code == 400 and "protected" in r.json()["detail"]
        # A row that predates the check (hand-edited / older build) is refused
        # at TASK time with the same honest reason, for a chat task too —
        # the session would otherwise start inside the protected folder.
        from iron_jarvis.core.db import session_scope
        from iron_jarvis.core.models import Project

        with session_scope(client.app.state.platform.engine) as db:
            row = db.get(Project, pid)
            row.root = str(vault)
            db.add(row)
            db.commit()
        r = client.post(f"/projects/{pid}/task", json={"text": "hello", "output": "chat"})
        assert r.status_code == 400 and "protected" in r.json()["detail"]


# --- 4. deliverable filenames are made safe ------------------------------------


@pytest.mark.parametrize(
    "filename, expected",
    [
        ("inventory", "inventory"),
        ("inventory.xlsx", "inventory"),
        ("re:port*?.docx", "re-port"),
        ("..", "summarize-every-pdf"),
        ("sub/dir/report", "report"),
        ("  .hidden  ", "hidden"),
        ("", "summarize-every-pdf"),
    ],
)
def test_deliverable_stem_is_filesystem_safe(filename, expected):
    # Imported here so the rest of this file still collects (and goes RED)
    # against a build that predates the helper — the mutation check.
    from iron_jarvis.daemon.routes.projects import _deliverable_stem

    assert _deliverable_stem(filename, "Summarize every PDF") == expected


def test_task_route_uses_the_safe_stem(tmp_path):
    with _client(tmp_path) as client:
        p = _project(client, tmp_path, with_root=True)
        r = client.post(
            f"/projects/{p['id']}/task",
            json={"text": "inventory", "output": "xlsx", "filename": "re:port*?"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["target_path"].endswith("re-port.xlsx")
        assert "re-port.xlsx" in r.json()["task"]
        client.post(f"/sessions/{r.json()['id']}/cancel")


# --- 5. knowledge uploads never touch the documents upload path ----------------


def test_knowledge_upload_does_not_clobber_a_document_upload(tmp_path):
    with _client(tmp_path) as client:
        pid = _project(client, tmp_path)["id"]
        home = client.app.state.platform.config.home
        # The user uploaded a document called facts.txt.
        original = base64.b64encode(b"original document").decode()
        r = client.post("/documents/upload", json={"filename": "facts.txt", "content_b64": original})
        assert r.status_code == 200, r.text
        doc_path = home / "uploads" / "facts.txt"
        assert doc_path.read_bytes() == b"original document"
        # Then added a DIFFERENT facts.txt to a project's knowledge.
        other = base64.b64encode(b"launch is Q3 2026").decode()
        r = client.post(
            f"/projects/{pid}/knowledge",
            json={"filename": "facts.txt", "content_b64": other},
        )
        assert r.status_code == 200, r.text
        item = client.get(f"/projects/{pid}/knowledge/{r.json()['id']}").json()
        assert item["text"] == "launch is Q3 2026"
        # The document is untouched and the staged copy is gone.
        assert doc_path.read_bytes() == b"original document"
        staging = home / "uploads" / ".knowledge-staging"
        assert not any(staging.iterdir())
