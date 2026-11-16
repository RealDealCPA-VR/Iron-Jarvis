"""North-star plan 4.8: a delete on a page is recorded and can be undone.

The same removal was recorded and undoable from chat (the record tools'
snapshot + ``record_restore`` journal) but unrecorded and permanent from its
page. Each page delete below now lands in the change history
(``GET /config/ledger``) and comes back through the REAL ``POST /undo/{id}``.
A key removed on the Secrets page is recorded as a change that can't be
undone, and its value is never in the journal.
"""

from __future__ import annotations

import json
import sys

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import UndoJournal
from iron_jarvis.daemon.app import create_app


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(str(tmp_path))) as c:
        yield c


def _undo_newest_here(client: TestClient, word: str) -> dict:
    """The newest "Changed here" row whose summary names ``word``, undone
    through the real route."""
    rows = client.get("/config/ledger").json()["changes"]
    row = next((r for r in rows if word in r["summary"]), None)
    assert row is not None, f"the page delete of {word!r} is not in the change history: {rows}"
    assert row["where"] == "here" and row["undoable"] is True, row
    r = client.post(f"/undo/{row['action_id']}")
    assert r.status_code == 200, r.text
    return r.json()


def test_a_schedule_removed_on_its_page_comes_back(client):
    assert client.post("/schedules", json={"name": "nightly", "cron": "0 9 * * *", "kind": "task",
                                           "payload": {"task": "summarize the day"}}).status_code == 200
    assert client.delete("/schedules/nightly").json()["removed"] is True
    assert all(t["name"] != "nightly" for t in client.get("/schedules").json()["schedules"])

    _undo_newest_here(client, "nightly")

    back = [t for t in client.get("/schedules").json()["schedules"] if t["name"] == "nightly"]
    assert back and back[0]["cron"] == "0 9 * * *"


def test_a_workflow_removed_on_its_page_comes_back_with_its_steps(client):
    steps = [{"name": "s1", "agent": "builder", "task": "close the books"}]
    assert client.post("/workflows", json={"name": "month-end", "steps": steps}).status_code == 200
    assert client.delete("/workflows/month-end").status_code == 200

    _undo_newest_here(client, "month-end")

    from iron_jarvis.settings.records import snap_workflow

    wf = snap_workflow(client.app.state.platform, "month-end")
    assert wf is not None and [s["task"] for s in wf["steps"]] == ["close the books"]


def test_a_custom_agent_removed_on_its_page_comes_back(client):
    assert client.post("/agents", json={"name": "tax-reader", "system_prompt": "Read the forms."}).status_code == 200
    assert client.delete("/agents/tax-reader").json()["removed"] == "tax-reader"
    assert client.app.state.platform.agents_registry.get("tax-reader") is None

    _undo_newest_here(client, "tax-reader")

    assert client.app.state.platform.agents_registry.get("tax-reader") is not None


def test_a_webhook_removed_on_its_page_comes_back(client):
    assert client.post("/webhooks", json={"slug": "gh"}).status_code == 200
    assert client.delete("/webhooks/gh").json()["ok"] is True

    _undo_newest_here(client, "gh")

    assert any(w["slug"] == "gh" for w in client.get("/webhooks").json()["webhooks"])


def test_a_watcher_removed_on_its_page_comes_back(client, tmp_path):
    folder = tmp_path / "watched"
    folder.mkdir()
    assert client.post("/sentinels", json={"name": "w", "path": str(folder)}).status_code == 200
    assert client.delete("/sentinels/w").json()["deleted"] == "w"

    _undo_newest_here(client, "w")

    assert any(s["name"] == "w" for s in client.get("/sentinels").json()["sentinels"])


def test_a_rule_removed_on_its_page_comes_back(client):
    r = client.post("/reflex/rules", json={"name": "on-1099", "source": "comm", "match": "1099", "action": "session"})
    assert r.status_code == 200, r.text
    rid = r.json().get("id") or r.json()["rule"]["id"]
    assert client.delete(f"/reflex/rules/{rid}").status_code == 200

    _undo_newest_here(client, "on-1099")

    rules = client.get("/reflex/rules").json()["rules"]
    assert any(x["id"] == rid for x in rules)


def test_a_goal_removed_on_its_page_comes_back(client):
    r = client.post("/goals", json={"name": "inbox zero", "contract_text": "Create a file summarizing the task.",
                                    "budget": {"max_tokens": 1000}})
    assert r.status_code == 200, r.text
    gid = r.json().get("id") or r.json()["goal"]["id"]
    assert client.delete(f"/goals/{gid}").status_code == 200

    _undo_newest_here(client, "inbox zero")

    assert client.get(f"/goals/{gid}").status_code == 200


def test_a_lesson_removed_on_its_page_comes_back(client):
    lid = client.post("/lessons", json={"text": "Prefer small verified steps"}).json()["id"]
    assert client.delete(f"/lessons/{lid}").json()["deleted"] == lid

    _undo_newest_here(client, "Prefer small verified steps")

    assert any(x["id"] == lid for x in client.get("/lessons").json()["lessons"])


def test_a_custom_tool_removed_on_its_page_comes_back(client):
    cmd = [sys.executable, "-c", "print(1)"]
    assert client.post("/tools/custom", json={"name": "say_one", "description": "prints one", "command": cmd}).status_code == 200
    assert client.delete("/tools/custom/say_one").json()["removed"] is True

    _undo_newest_here(client, "say_one")

    tools = client.get("/tools/custom").json()["tools"]
    assert [t["command"] for t in tools if t["name"] == "say_one"] == [cmd]
    assert client.app.state.platform.registry.get("say_one") is not None


def test_a_removed_key_is_recorded_and_says_it_cannot_be_undone(client):
    value = "plain-test-value-not-a-credential"
    assert client.post("/secrets", json={"name": "my_note_key", "value": value, "kind": "password"}).status_code == 200
    r = client.delete("/secrets/my_note_key").json()
    assert r["deleted"] is True
    assert "can't be undone" in r["undo"]

    rows = client.get("/config/ledger").json()["changes"]
    row = next(x for x in rows if "my_note_key" in x["summary"] and x["summary"].startswith("removed"))
    assert row["undoable"] is False
    assert client.post(f"/undo/{row['action_id']}").status_code == 422
    # The value is nowhere in the journal.
    with session_scope(client.app.state.platform.engine) as db:
        inlines = [j.pre_inline or "" for j in db.exec(select(UndoJournal))]
    assert not any(value in s for s in inlines)
    assert json.dumps(value)[1:-1] not in " ".join(inlines)


def test_a_persona_removed_on_its_page_comes_back(client):
    made = client.post("/chat/personas", json={
        "name": "ledger-clerk", "title": "Ledger clerk",
        "description": "Careful with numbers", "prompt": "Check every total twice.",
    })
    assert made.status_code == 200, made.text
    gone = client.delete("/chat/personas/ledger-clerk").json()
    assert gone["deleted"] == "ledger-clerk" and gone["action_id"]

    _undo_newest_here(client, "Ledger clerk")

    back = [p for p in client.get("/chat/personas").json()["personas"] if p["name"] == "ledger-clerk"]
    assert back and back[0]["prompt"] == "Check every total twice."
