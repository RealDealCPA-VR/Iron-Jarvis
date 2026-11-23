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


# --------------------------------------------------------------------------- #
# Review (05-a): the sentence reaches the rule through the chat's own arming
# --------------------------------------------------------------------------- #
#: The phase's sentences, said the everyday ways: each must reach the RULE's
#: writer (one card) through the arming both chat lanes call, never only the
#: suggestion watcher.
RULE_SENTENCES = [
    "When a file lands in Intake, sort it by sender.",
    "When a PDF lands in my Intake folder, read it and file it by sender.",
    "Whenever a new PDF shows up in my Downloads folder, rename it by client name.",
    "Watch my Downloads folder and file new PDFs into the right client folder.",
    "When a download finishes from irs.gov, file it in my Tax folder.",
    "Whenever I download a PDF from my bank, rename it by month.",
    "When a new statement is downloaded, file it in my Bank folder.",
]
#: One download now, a question, a habit: no rule.
NOT_RULE_SENTENCES = [
    "When the download finishes, open it.",
    "Did my download finish?",
    "When did my download finish?",
    "Whenever I download something it goes to my Downloads folder.",
    "Download the W-9 from irs.gov and file it in my Tax folder.",
]


def _armed(p, text: str) -> set[str]:
    """What both chat lanes offer for ``text`` (the arming table's reading:
    the POST lane's armed set plus the stream lane's ask tier)."""
    from types import SimpleNamespace

    from iron_jarvis.daemon import chat_turn as CT
    from iron_jarvis.daemon.schemas import ChatBody
    from iron_jarvis.tools.autoselect import select_ask_tools

    d = SimpleNamespace(platform=p)
    body = ChatBody(messages=[{"role": "user", "content": text}], auto_tools=True)
    armed = list(CT._resolve_armed_tools(d, body)[0])
    ask = [t for t in select_ask_tools(text) if t not in armed]
    ask += CT.settings_ask_tools(d, text, armed, ask)
    return set(armed) | set(ask)


def test_the_sentence_arms_the_rule_and_the_model_is_told_which_does_the_work(desk):
    """MUTATION (each red): drop ``file_rule`` from the rule arming in
    ``records._record_tools_named`` -> the folder and download sentences lose
    reflex_create; drop ``download_finishes`` from ``file_rule`` -> the
    download sentences lose it; arm sentinel_add on a bare folder arrival
    again -> the watcher is offered beside the rule; drop the rule from the
    ``watcher_suggests`` line -> the model is told a watcher is all there is."""
    from iron_jarvis.daemon import page_doors as P
    from iron_jarvis.settings import setup_words as W

    _c, p, _router = desk
    for text in RULE_SENTENCES:
        got = _armed(p, text)
        assert "reflex_create" in got, (text, sorted(got))
        named_watch = bool(W.WATCH_FOLDER.search(text))
        assert ("sentinel_add" in got) is named_watch, (text, sorted(got))
    for text in NOT_RULE_SENTENCES:
        assert "reflex_create" not in _armed(p, text), text
    # The line the model reads names the rule for work done by itself, and
    # says what a watcher really does.
    for text in RULE_SENTENCES[1:]:
        (line,) = [n.line for n in P.not_offered(text) if n.id == "watcher_suggests"]
        assert "reflex_create" in line and "only notices new or changed files" in line, text
    for text in NOT_RULE_SENTENCES:
        assert "watcher_suggests" not in {n.id for n in P.not_offered(text)}, text


def test_the_download_words_are_linear():
    """Every new pattern is linear on 128,000 characters of its own words."""
    import time

    from iron_jarvis.settings import setup_words as W

    for chunk in ("whenever download ", "when a a a download ", "when i download , ", "download finishes "):
        text = chunk * (128_000 // len(chunk))
        started = time.perf_counter()
        W.download_finishes(text)
        assert time.perf_counter() - started < 2.0, chunk


def test_a_file_dropped_right_after_the_rule_is_made_starts_it(desk, tmp_path):
    """The phase's done-when, as a user does it: make the rule, drop a file
    at once (before the watch loop's first scan), and it starts. Files that
    were there when the rule was made are left alone; a changed folder takes
    its own baseline when it is changed.

    MUTATION (each red): drop ``take_baseline`` from ``ensure_file_watcher``
    -> the dropped file is taken as "already there"; drop it from
    ``sync_rule_watcher``'s folder change -> the new folder's first file is."""
    c, p, router = desk
    folder = tmp_path / "Intake"
    folder.mkdir()
    (folder / "was here.pdf").write_bytes(b"old")
    made = _invoke(p, "reflex_create", {**SENTENCE_ARGS, "path": str(folder)}, _ctx(p, tmp_path))
    assert made.ok, made.error
    (folder / "dropped right after.pdf").write_bytes(b"%PDF-1.4")
    _scan(p, router)
    _scan(p, router)
    tasks = [s.task for s in _rule_runs(p)]
    assert len(tasks) == 1 and "dropped right after.pdf" in tasks[0] and "was here.pdf" not in tasks[0], tasks
    # The rule's folder changes on its row: the same holds for the new one.
    other = tmp_path / "Intake2"
    other.mkdir()
    (other / "already.pdf").write_bytes(b"old")
    assert c.patch(f"/reflex/rules/{made.data['id']}", json={"path": str(other)}).status_code == 200
    (other / "new one.pdf").write_bytes(b"%PDF-1.4")
    _scan(p, router)
    _scan(p, router)
    tasks = [s.task for s in _rule_runs(p)]
    assert len(tasks) == 2 and "new one.pdf" in tasks[1] and "already.pdf" not in tasks[1], tasks


def test_a_rules_own_watcher_is_changed_only_with_its_rule(desk, tmp_path):
    """The old watcher doors (chat's sentinel_update / sentinel_delete, the
    Watchers page's PATCH and DELETE) refuse a file rule's own watcher in
    words: a lone change left the rule watching nothing, and the delete's
    Undo brought it back as a plain suggestion watcher. A snapshot carries
    the watcher's mode and rule, so any Undo keeps it the rule's.

    MUTATION (each red): drop the ``_rule_owns`` check in either chat tool,
    or ``_refuse_a_rules_watcher`` in either route -> the watcher is changed
    or gone; drop ``mode``/``rule_id`` from ``restore_sentinel`` -> it comes
    back as a suggestion watcher."""
    from iron_jarvis.settings import records as R

    c, p, router = desk
    folder = tmp_path / "Intake"
    folder.mkdir()
    made = _invoke(p, "reflex_create", {**SENTENCE_ARGS, "path": str(folder)}, _ctx(p, tmp_path))
    assert made.ok, made.error
    (w,) = _rule_watchers(p)
    for name, args in (("sentinel_update", {"name": w.name, "enabled": False}),
                       ("sentinel_delete", {"name": w.name})):
        r = _invoke(p, name, args, _ctx(p, tmp_path))
        assert not r.ok and "belongs to the rule “Sort intake”" in r.error and "Rules page" in r.error, name
    bad = c.patch(f"/sentinels/{w.name}", json={"enabled": False})
    assert bad.status_code == 409 and "belongs to the rule “Sort intake”" in bad.json()["detail"]
    assert c.delete(f"/sentinels/{w.name}").status_code == 409
    (still,) = _rule_watchers(p)
    assert still.enabled and still.rule_id == made.data["id"]
    # A snapshot of it, put back, is still the rule's own watcher.
    snap = R.snap_sentinel(p, w.name)
    assert (snap["mode"], snap["rule_id"]) == ("rule", made.data["id"])
    p.sentinels.remove(w.name)
    R.restore_sentinel(p, w.name, snap)
    (back,) = _rule_watchers(p)
    assert back.rule_id == made.data["id"]
    # A rule whose watcher is missing says so on its row (watcher: null).
    p.sentinels.remove_for_rule(made.data["id"])
    (row,) = c.get("/reflex/rules").json()["rules"]
    assert row["source"] == "file" and row["watcher"] is None
    # Once its rule is gone, a left-over watcher is the user's to remove.
    p.sentinels.add("Rule: orphan", path=str(folder), mode="rule", rule_id="rule_gone")
    assert c.delete("/sentinels/Rule: orphan").status_code == 200


def test_chat_can_read_why_a_rule_stopped(desk, tmp_path):
    """"Why did my Intake rule stop?" is answered from the rule's own row:
    chat's listing of rules (app_search) says its When and Do, and that it
    paused itself and how it starts again, or that it is off; the change
    card says a paused rule was resumed.

    MUTATION (each red): put the old one-line blurb back in
    ``guide.tools.AppSearchTool._rows`` -> no pause, no folder; drop the
    pause from ``_describe_reflex`` -> the card does not say it resumed."""
    c, p, router = desk
    folder = tmp_path / "Intake"
    folder.mkdir()
    made = _invoke(p, "reflex_create", {**SENTENCE_ARGS, "path": str(folder)}, _ctx(p, tmp_path))
    rid = made.data["id"]
    tool = p.registry.get("app_search")

    def detail() -> str:
        res = asyncio.run(tool.execute({"query": "Sort intake", "kinds": ["reflex"]}, None))
        (hit,) = res.data["hits"]
        return hit["detail"]

    assert detail().startswith("When a new file lands in Intake.") and "It is on." in detail()
    for _ in range(12):
        p.reflex.note_start(rid)
    assert p.reflex.start_gate(rid)["tripped"] is True
    got = detail()
    assert "It paused itself (Paused: it started 12 times in an hour)" in got and "Resume" in got, got
    r = _invoke(p, "reflex_update", {"rule": "Sort intake", "enabled": True}, _ctx(p, tmp_path))
    assert r.ok, r.error
    card = r.data["record_change"]
    assert card["old"].endswith("(paused itself)") and not card["new"].endswith("(paused itself)"), card
    p.reflex.keep_paused(rid)
    assert "It is off." in detail()


def test_an_edit_on_the_rules_page_never_resumes_a_pause_by_itself(desk, tmp_path):
    """The Rules page's form sends every field, ``enabled`` included: saving
    an edit of a rule that paused itself keeps the pause (only Resume clears
    it); switching a rule that is OFF back on starts it fresh.

    MUTATION (red): resume on every ``enabled: true`` in the PATCH route ->
    the edit clears the pause and its hour's count."""
    c, p, router = desk
    rule = p.reflex.add(name="ping", source="webhook", match="ping", action="session", task_template="Note it")
    for _ in range(12):
        p.reflex.note_start(rule.id)
    assert p.reflex.start_gate(rule.id)["tripped"] is True
    out = c.patch(f"/reflex/rules/{rule.id}", json={"task_template": "Note it again", "enabled": True})
    assert out.status_code == 200, out.text
    row = p.reflex.get(rule.id)
    assert row.paused_at is not None and row.task_template == "Note it again", "an edit kept the pause"
    assert c.patch(f"/reflex/rules/{rule.id}", json={"enabled": False}).status_code == 200
    assert c.patch(f"/reflex/rules/{rule.id}", json={"enabled": True}).status_code == 200
    row = p.reflex.get(rule.id)
    assert row.enabled and row.paused_at is None and not row.recent_starts_json, "on again: fresh"
