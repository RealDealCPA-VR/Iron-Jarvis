"""v1.222.0 — deleting a saved workflow tells the truth about what still
fires it.

The user could not delete a workflow: the only door was a hover-only icon
inside the canvas's Load dropdown. The page now lists saved workflows with a
visible Delete, and the confirm step asks the daemon what still names the
workflow — a ``kind="workflow"`` schedule or a reflex rule fires it BY NAME
and fails with "no saved workflow" the moment it is gone. That preflight is
``GET /workflows/{name}/references``; ``DELETE`` echoes the same list as
``referenced_by`` for API callers.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app

STEPS = [{"name": "s", "agent": "builder", "task": "t"}]


def _save(client, name):
    r = client.post("/workflows", json={"name": name, "steps": STEPS})
    assert r.status_code == 200, r.text


def test_references_name_schedules_and_reflex_rules(tmp_path):
    with TestClient(create_app(str(tmp_path))) as client:
        _save(client, "nightly-close")
        _save(client, "other")
        # A schedule that fires it by name.
        r = client.post(
            "/schedules",
            json={
                "name": "close-books",
                "cron": "0 3 * * *",
                "kind": "workflow",
                "payload": {"workflow": "nightly-close"},
            },
        )
        assert r.status_code == 200, r.text
        # A schedule that carries INLINE steps only borrows the name as a label.
        r = client.post(
            "/schedules",
            json={
                "name": "inline-copy",
                "cron": "0 4 * * *",
                "kind": "workflow",
                "payload": {"name": "nightly-close", "steps": STEPS},
            },
        )
        assert r.status_code == 200, r.text
        # A reflex rule that fires it, and one that fires something else.
        r = client.post(
            "/reflex/rules",
            json={"name": "on-upload", "source": "comm", "match": "closed",
                  "action": "workflow", "target": "nightly-close"},
        )
        assert r.status_code == 200, r.text
        rid = r.json()["id"]
        client.post(
            "/reflex/rules",
            json={"name": "elsewhere", "source": "comm", "match": "x",
                  "action": "workflow", "target": "other"},
        )

        refs = client.get("/workflows/nightly-close/references").json()
        assert refs["name"] == "nightly-close"
        assert refs["references"] == [
            {"kind": "schedule", "name": "close-books", "enabled": True},
            {"kind": "reflex", "id": rid, "name": "on-upload", "enabled": True},
        ]
        # The other workflow sees only its own rule.
        other = client.get("/workflows/other/references").json()["references"]
        assert [x["name"] for x in other] == ["elsewhere"]
        assert client.get("/workflows/nope/references").status_code == 404


def test_delete_reports_referenced_by_and_leaves_the_automations_alone(tmp_path):
    with TestClient(create_app(str(tmp_path))) as client:
        _save(client, "nightly-close")
        client.post(
            "/schedules",
            json={
                "name": "close-books",
                "cron": "0 3 * * *",
                "kind": "workflow",
                "payload": {"workflow": "nightly-close"},
            },
        )
        r = client.delete("/workflows/nightly-close")
        assert r.status_code == 200, r.text
        assert r.json()["deleted"] == "nightly-close"
        assert [x["name"] for x in r.json()["referenced_by"]] == ["close-books"]
        # The workflow is gone; the schedule is the user's and stays put.
        assert client.get("/workflows/nightly-close").status_code == 404
        names = [s["name"] for s in client.get("/schedules").json()["schedules"]]
        assert "close-books" in names
        # Deleting again: 404, no phantom references.
        assert client.delete("/workflows/nightly-close").status_code == 404
