"""Leap 05-a (R1.3): one chat sentence makes one rule, and a folder starts it.

The done-when of the phase: one ``reflex_create`` call (what the model makes
of "when a file lands in Intake, sort it by sender") makes the rule AND the
watcher of its folder; its card says When, Do, Where it works and Limits with
an Undo; ``GET /reflex/rules`` returns the row the card names; a real file
dropped in the folder starts it, and its run reports in the chat it was made
in; Undo removes the rule and its watcher. Through the registry (ledger and
Undo journal), the real app and its watcher service.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from iron_jarvis.agents import chat_delivery as cd
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import ChatThreadRecord, Session
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.chat_turn import config_cards_from_result
from iron_jarvis.scheduling.knobs import session_options
from iron_jarvis.tools.base import ToolContext
from iron_jarvis.triggers import starts

pytestmark = [pytest.mark.usefixtures("no_network"), pytest.mark.timeout(300)]

SENTENCE_ARGS = {
    "name": "Sort intake",
    "source": "file",
    "action": "session",
    "task_template": "Read {file} and file it in a folder named after its sender",
}


@pytest.fixture()
def desk(tmp_path):
    c = TestClient(create_app(str(tmp_path / "home")))
    p = c.app.state.platform
    router = c.app.state.reflex_router
    router._launch = lambda coro, _sid: coro.close()
    yield c, p, router
    try:
        p.scheduler.shutdown()
    except Exception:  # noqa: BLE001
        pass


def _chat(p, title="Intake") -> str:
    rec = ChatThreadRecord(title=title, messages_json=json.dumps(
        [{"role": "user", "content": "When a file lands in Intake, sort it by sender"}]))
    with session_scope(p.engine) as db:
        db.add(rec)
        db.commit()
        db.refresh(rec)
        return rec.id


def _ctx(p, tmp_path, thread_id: str = "") -> ToolContext:
    return ToolContext(workspace=tmp_path, session_id="chat", agent_run_id="chat",
                       config=p.config, event_bus=p.event_bus, engine=p.engine, thread_id=thread_id)


def _invoke(p, name: str, args: dict, ctx: ToolContext):
    """As a chat turn runs it once the user approved its card."""
    return asyncio.run(p.registry.invoke(name, args, ctx, p.permissions, session_allow=[name]))


def _rule_watchers(p) -> list:
    return [w for w in p.sentinels.list() if getattr(w, "mode", "") == "rule"]


def _scan(p, router) -> None:
    async def go():
        loop = asyncio.get_running_loop()
        p.sentinels.rule_fire = starts.file_rule_fire(lambda: starts.hub_for(router), lambda: loop)
        await asyncio.to_thread(p.sentinels.poll_once, None, rules_only=True)

    asyncio.run(go())


def _rule_runs(p) -> list[Session]:
    with session_scope(p.engine) as db:
        return [s for s in db.exec(select(Session).order_by(Session.created_at))
                if str(s.origin or "").startswith("reflex:")]


def test_one_sentence_makes_one_rule_one_watcher_one_card_and_a_file_starts_it(desk, tmp_path):
    c, p, router = desk
    folder = tmp_path / "Intake"
    folder.mkdir()
    chat = _chat(p)
    r = _invoke(p, "reflex_create", {**SENTENCE_ARGS, "path": str(folder), "glob": "*.pdf"},
                _ctx(p, tmp_path, chat))
    assert r.ok, r.error
    rule = p.reflex.get(r.data["id"])
    # ONE rule and ONE watcher of its folder, made by the one call.
    assert len(p.reflex.list()) == 1 and rule.source == "file"
    (watcher,) = _rule_watchers(p)
    assert watcher.rule_id == rule.id and watcher.decoded_config() == {"path": str(folder), "glob": "*.pdf"}
    assert rule.chat_thread_id == chat, "made in a chat, it reports there"
    assert Path(rule.workspace_root).resolve() == folder.resolve(), "it works in the folder it watches"
    # The card: When, Do, Where it works, Limits — and an Undo (a change id).
    card = r.data["record_change"]
    assert card["change_id"] and card["key"] == f"reflex.{rule.id}"
    lines = card["rule"]
    assert lines["when"].startswith("When a new file lands in Intake (matching *.pdf).")
    assert "every 5 minutes" in lines["when"]
    assert lines["does"] == "Jarvis does: Read {file} and file it in a folder named after its sender."
    assert lines["where"] == f"In {rule.workspace_root}."
    assert lines["limits"].startswith("Starts at most 12 times an hour, then pauses and tells you.")
    assert "Reports here" in card["new"]
    # Both chat lanes carry the four lines to the page (one helper).
    (page_card,) = [x for x in config_cards_from_result("reflex_create", r) if x["kind"] == "change"]
    assert page_card["rule"] == {"when": lines["when"], "does": lines["does"],
                                 "where": lines["where"], "limits": lines["limits"]}
    # The Rules page's row is the rule the card names, with the same words.
    (row,) = c.get("/reflex/rules").json()["rules"]
    assert row["id"] == rule.id
    assert (row["when_words"], row["does_words"], row["where_words"], row["limits_words"]) == (
        lines["when"], lines["does"], lines["where"], lines["limits"])
    assert row["reports_to"] == {"thread_id": chat, "title": "Intake"}
    assert row["watcher"]["name"] == watcher.name
    # A real file dropped in the folder starts it (once it has settled).
    _scan(p, router)
    (folder / "notes.txt").write_text("not a pdf")
    (folder / "Invoice from Acme.pdf").write_bytes(b"%PDF-1.4")
    _scan(p, router)
    _scan(p, router)
    (run,) = _rule_runs(p)
    assert "Invoice from Acme.pdf" in run.task and "notes.txt" not in run.task
    opts = session_options(run)
    assert opts["rule_id"] == rule.id
    assert opts["chat_thread_id"] == chat
    assert opts[cd.REPORT_OPTION] == {"kind": "rule", "name": "Sort intake", "id": rule.id, "href": "/reflex"}


def test_undo_removes_the_rule_and_its_watcher(desk, tmp_path):
    c, p, router = desk
    folder = tmp_path / "Intake"
    folder.mkdir()
    r = _invoke(p, "reflex_create", {**SENTENCE_ARGS, "path": str(folder)}, _ctx(p, tmp_path))
    assert r.ok, r.error
    assert len(_rule_watchers(p)) == 1
    action = c.get(f"/config/changes/{r.data['record_change']['change_id']}").json()["action_id"]
    out = c.post(f"/undo/{action}")
    assert out.status_code == 200, out.text
    assert p.reflex.list() == []
    assert _rule_watchers(p) == [], "the watcher went with the rule"


def test_removing_the_rule_removes_its_watcher_and_undo_brings_both_back(desk, tmp_path):
    c, p, router = desk
    folder = tmp_path / "Intake"
    folder.mkdir()
    made = _invoke(p, "reflex_create", {**SENTENCE_ARGS, "path": str(folder)}, _ctx(p, tmp_path))
    rid = made.data["id"]
    gone = _invoke(p, "reflex_delete", {"rule": "Sort intake"}, _ctx(p, tmp_path))
    assert gone.ok, gone.error
    assert p.reflex.get(rid) is None and _rule_watchers(p) == []
    action = c.get(f"/config/changes/{gone.data['record_change']['change_id']}").json()["action_id"]
    assert c.post(f"/undo/{action}").status_code == 200
    back = p.reflex.get(rid)
    assert back is not None and back.decoded_start()["path"] == str(folder)
    (watcher,) = _rule_watchers(p)
    assert watcher.rule_id == rid
    # The page's delete takes the watcher too.
    assert c.delete(f"/reflex/rules/{rid}").status_code == 200
    assert _rule_watchers(p) == []


def test_a_file_rule_needs_a_folder_on_this_pc(desk, tmp_path):
    c, p, router = desk
    tool = p.registry.get("reflex_create")
    # No folder, or a bare name: the user is asked for the full location first.
    assert "path" in tool.missing_details({"source": "file", "action": "session"})
    assert "path" in tool.missing_details({"source": "file", "action": "session", "path": "Intake"})
    assert tool.missing_details({"source": "email", "action": "session"}) == {}
    # A network location is refused in words, and nothing is made.
    r = _invoke(p, "reflex_create", {**SENTENCE_ARGS, "path": r"\\server\share\Intake"}, _ctx(p, tmp_path))
    assert not r.ok and "network location" in r.error
    assert p.reflex.list() == [] and _rule_watchers(p) == []


def test_a_download_rule_from_chat_and_the_page_can_make_a_file_rule(desk, tmp_path):
    c, p, router = desk
    r = _invoke(p, "reflex_create", {"name": "Tax forms", "source": "download", "action": "session",
                                     "match_site": "irs.gov", "task_template": "File {file}"}, _ctx(p, tmp_path))
    assert r.ok, r.error
    lines = r.data["record_change"]["rule"]
    assert lines["when"] == "When a download finishes in your browser from irs.gov."
    assert lines["limits"].endswith("It starts carefully: the file came from a website.")
    assert _rule_watchers(p) == [], "a download rule watches no folder"
    # The Rules page makes a file rule with its watcher too.
    folder = tmp_path / "Scans"
    folder.mkdir()
    out = c.post("/reflex/rules", json={"name": "Scans", "source": "file", "action": "session",
                                        "path": str(folder), "task_template": "OCR {file}"})
    assert out.status_code == 200, out.text
    row = out.json()
    assert row["start"] == {"path": str(folder)} and row["watcher"]
    assert row["reports_to"] is None, "a page-made rule reports nowhere"
    # Its folder can be changed: the watcher follows.
    other = tmp_path / "Scans2"
    other.mkdir()
    assert c.patch(f"/reflex/rules/{row['id']}", json={"path": str(other)}).status_code == 200
    (w,) = [w for w in _rule_watchers(p) if w.rule_id == row["id"]]
    assert w.decoded_config()["path"] == str(other)
    # A file rule with no folder is refused in words.
    bad = c.post("/reflex/rules", json={"source": "file", "action": "session"})
    assert bad.status_code == 400 and "folder" in bad.json()["detail"]


def test_a_rule_names_who_does_the_work_and_a_gone_agent_is_refused_in_words(desk, tmp_path):
    c, p, router = desk
    gone = _invoke(p, "reflex_create", {"name": "Ghost", "source": "webhook", "match": "g", "action": "session",
                                        "task_template": "x", "agent": "nobody-here"}, _ctx(p, tmp_path))
    assert not gone.ok and "the agent 'nobody-here' this rule names no longer exists" in gone.error
    assert p.reflex.list() == []
    made = _invoke(p, "reflex_create", {"name": "Build it", "source": "webhook", "match": "b", "action": "session",
                                        "task_template": "x", "agent": "builder"}, _ctx(p, tmp_path))
    assert made.ok, made.error
    out = asyncio.run(starts.hub_for(router).signal("webhook", fields={"slug": "b", "body": ""}))
    with session_scope(p.engine) as db:
        run = db.get(Session, out[0]["session_id"])
    assert getattr(run.agent_type, "value", run.agent_type) == "builder", "the rule's agent does the work"
    # Without one, the supervisor, as before.
    plain = p.reflex.add(name="Plain", source="webhook", match="p", action="session", task_template="x")
    out = asyncio.run(starts.hub_for(router).signal("webhook", fields={"slug": "p", "body": ""}))
    with session_scope(p.engine) as db:
        run = db.get(Session, out[0]["session_id"])
    assert getattr(run.agent_type, "value", run.agent_type) == "supervisor" and plain.agent_name == ""


def test_reflex_update_changes_the_limit_and_resumes_a_paused_rule(desk, tmp_path):
    c, p, router = desk
    folder = tmp_path / "Intake"
    folder.mkdir()
    made = _invoke(p, "reflex_create", {**SENTENCE_ARGS, "path": str(folder)}, _ctx(p, tmp_path))
    rid = made.data["id"]
    p.reflex.start_gate(rid)  # the limit is not reached: nothing changes
    for _ in range(12):
        p.reflex.note_start(rid)
    assert p.reflex.start_gate(rid)["tripped"] is True
    r = _invoke(p, "reflex_update", {"rule": "Sort intake", "enabled": True, "max_fires_per_hour": 30},
                _ctx(p, tmp_path))
    assert r.ok, r.error
    row = p.reflex.get(rid)
    assert row.paused_at is None and row.enabled and row.max_fires_per_hour == 30
