"""Readiness 0j.5 item 15 — a rule's Test and its last fire say what started
and link to it, never "Fired session · session <id>".

``POST /reflex/rules/{id}/test`` answers ``words`` (plain words, no id) and
``href`` (an app page: ``/sessions/<id>``, ``/workflows``, the team page), and
the rule's row keeps both as ``last_result`` / ``last_href``. Driven through
the real app and the real reflex router.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.reflex.router import result_words
from iron_jarvis.workflows.store import WorkflowStore


def _rule(client, **over) -> str:
    body = {"name": "wh", "source": "webhook", "match": "deploy", "action": "session", **over}
    r = client.post("/reflex/rules", json=body)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _row(client, rid: str) -> dict:
    return next(x for x in client.get("/reflex/rules").json()["rules"] if x["id"] == rid)


def test_a_session_rules_test_says_it_started_a_run_and_links_it(tmp_path):
    with TestClient(create_app(str(tmp_path))) as client:
        rid = _rule(client)
        out = client.post(f"/reflex/rules/{rid}/test").json()
        assert out["ok"] is True
        sid = out["session_id"]
        assert out["words"] == "Started an agent run."
        assert sid not in out["words"]
        assert out["href"] == f"/sessions/{sid}"
        row = _row(client, rid)
        assert row["last_result"] == "Started an agent run."
        assert row["last_href"] == f"/sessions/{sid}"


def test_a_workflow_rules_test_names_the_workflow(tmp_path):
    with TestClient(create_app(str(tmp_path))) as client:
        WorkflowStore(client.app.state.platform.engine).save(
            "nightly", [{"agent": "builder", "task": "say hi"}], description="t"
        )
        rid = _rule(client, action="workflow", target="nightly")
        out = client.post(f"/reflex/rules/{rid}/test").json()
        assert out["ok"] is True
        assert out["words"] == "Started the workflow “nightly”."
        assert out["run_id"] not in out["words"]
        assert out["href"] == "/workflows"
        assert _row(client, rid)["last_href"] == "/workflows"


def test_a_failed_start_says_no_place():
    words, href = result_words({"kind": "remote_agent", "agent": "hermes"})
    assert words == "Handed the task to hermes." and href == "/agents?view=team"
    assert result_words({"kind": "session"}) == ("Started an agent run.", "/sessions")


def test_a_rule_that_could_not_start_carries_no_words(tmp_path):
    with TestClient(create_app(str(tmp_path))) as client:
        rid = _rule(client, action="workflow", target="gone")
        out = client.post(f"/reflex/rules/{rid}/test").json()
        assert out["ok"] is False
        assert "words" not in out and "href" not in out
        assert _row(client, rid)["last_href"] is None
