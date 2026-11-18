"""Fourth review, group 24 (OT2, D1, MS9, OT3): what another app is told.

* OT2: a secret pasted into a chat's first words, a report's summary or an
  agent run's task is masked before the work record keeps it, before a chat
  or another app is told it, and before the reported-chat table, its key and
  the ledger row of ``chat_record`` keep it.
* D1: rows stored before the mask are masked once at start, and again when
  the mask itself changes.
* MS9: two same-day reports whose titles have no Latin letters are two rows.
* OT3: a coding tool's chat keeps its OWN report's summary when a later
  chat's report also falls in its time.

Every secret-shaped value is ASSEMBLED here at run time from pieces, never
one literal (the repository is public).
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, text
from sqlmodel import select

from iron_jarvis.browser.panetokens import INSTALL_TOKEN_ENV
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.ids import utcnow
from iron_jarvis.core.models import (
    AgentRun,
    ChatThreadRecord,
    Session,
    SessionStatus,
    ToolInvocation,
    UndoJournal,
)
from iron_jarvis.daemon import chat_turn
from iron_jarvis.daemon.app import create_app
from iron_jarvis.mcpserver import apps
from iron_jarvis.workmap import block as B
from iron_jarvis.workmap import keys, outside, reported
from iron_jarvis.workmap.connector_tools import ChatRecordTool
from iron_jarvis.workmap.index import work_map
from iron_jarvis.workmap.models import KIND_OUTSIDE, ReportedChat, WorkEntry

# --- fake values, assembled at run time ------------------------------------ #
FAKE = "sk-" + "test-" + "x" * 30
PW_VALUE = "example" + "-only"
PW = "pass" + "word=" + PW_VALUE
GOOGLE = "AI" + "za" + "Sy" + "q" * 33
#: Pieces that must never be kept or told: the whole key, its long tail, the
#: password's value, the Google-shaped key's tail, and the stub a cut at
#: character 120 leaves of a key that crosses it ("sk-t").
PIECES = (FAKE, "x" * 20, PW_VALUE, "q" * 20, FAKE[:4])

INSTALL = "install-bearer-for-the-whole-app"
ACCEPT = "application/json, text/event-stream"
C1 = "11111111-1111-4111-8111-111111111111"
C2 = "22222222-2222-4222-8222-222222222222"
X1 = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
XA = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
XB = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"


def _leaks(value: object) -> list[str]:
    blob = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return [p for p in PIECES if p in blob]


@pytest.fixture()
def scene(tmp_path, monkeypatch):
    monkeypatch.setenv(INSTALL_TOKEN_ENV, INSTALL)
    monkeypatch.setattr(chat_turn, "GROUNDING_SOFT_S", 60.0)
    monkeypatch.setattr(apps, "TEST_ROOT", str(tmp_path / "other-apps"))
    codex_home, claude_home = tmp_path / "codex-home", tmp_path / "claude-home"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude_home))
    monkeypatch.setattr(outside, "READS_DISK", True)
    temp = tmp_path / "pc-temp"
    temp.mkdir()
    import tempfile

    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(temp))
    monkeypatch.setattr(outside, "SCRATCH_MARKERS", ())
    outside.forget_cache()
    app = create_app(str(tmp_path / "home"))
    folder = tmp_path / "Parser"
    folder.mkdir()

    class Scene:
        pass

    s = Scene()
    s.app, s.platform = app, app.state.platform
    s.client = TestClient(app, headers={"Authorization": f"Bearer {INSTALL}"})
    s.engine = s.platform.engine
    s.tmp, s.folder, s.codex_home, s.claude_home = tmp_path, folder, codex_home, claude_home
    s.wm = work_map(s.engine, s.platform.config.home)
    yield s
    outside.forget_cache()


class Door:
    """One app's MCP session against the real /mcp door."""

    def __init__(self, scene, token: str) -> None:
        from iron_jarvis.mcp.client import PROTOCOL_VERSION
        from iron_jarvis.mcpserver.session import SESSION_HEADER

        self.c, self.token, self.n, self.sid = TestClient(scene.app), token, 0, ""
        self.header = SESSION_HEADER
        r = self.post("initialize", {"protocolVersion": PROTOCOL_VERSION,
                                     "clientInfo": {"name": "pytest", "version": "1"}})
        assert r.status_code == 200, r.text
        self.sid = r.headers[SESSION_HEADER]

    def post(self, method: str, params: dict | None = None):
        self.n += 1
        headers = {"Accept": ACCEPT, "Authorization": f"Bearer {self.token}"}
        if self.sid:
            headers[self.header] = self.sid
        return self.c.post("/mcp", json={"jsonrpc": "2.0", "id": self.n, "method": method,
                                         "params": params or {}}, headers=headers)

    def call(self, name: str, arguments: dict) -> dict:
        r = self.post("tools/call", {"name": name, "arguments": arguments})
        assert r.status_code == 200, r.text
        return r.json()["result"]


def _door(scene, app="claude-desktop") -> Door:
    path = apps.config_path(app)
    path.parent.mkdir(parents=True, exist_ok=True)
    assert scene.client.post(f"/work-record/other-apps/{app}/connect").status_code == 200
    return Door(scene, scene.platform.app_connections.key_path(app).read_text(encoding="utf-8"))


def _claude_code_chat(scene, sid: str, words: str) -> None:
    home = scene.claude_home / "projects" / "some-slug"
    home.mkdir(parents=True, exist_ok=True)
    lines = [
        {"type": "user", "sessionId": sid, "cwd": str(scene.folder), "entrypoint": "claude-desktop",
         "timestamp": "2026-11-10T10:00:00.000Z", "message": {"role": "user", "content": words}},
        {"type": "assistant", "sessionId": sid, "cwd": str(scene.folder), "entrypoint": "claude-desktop",
         "timestamp": "2026-11-10T10:05:00.000Z",
         "message": {"role": "assistant", "content": [{"type": "text", "text": "Done."}]}},
    ]
    (home / f"{sid}.jsonl").write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")


def _codex_chat(scene, sid: str, words: str, start: str, end: str, day: int = 11) -> None:
    home = scene.codex_home / "sessions" / "2026" / "11" / f"{day:02d}"
    home.mkdir(parents=True, exist_ok=True)
    lines = [
        {"timestamp": f"2026-11-{day:02d}T{start}:00.000Z", "type": "session_meta",
         "payload": {"id": sid, "cwd": str(scene.folder), "originator": "Codex Desktop", "source": "vscode"}},
        {"timestamp": f"2026-11-{day:02d}T{start}:00.000Z", "type": "response_item",
         "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": words}]}},
        {"timestamp": f"2026-11-{day:02d}T{end}:00.000Z", "type": "response_item",
         "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Done"}]}},
    ]
    (home / f"rollout-2026-11-{day:02d}T{start.replace(':', '-')}-00-{sid}.jsonl").write_text(
        "\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")


def _own_chat(scene, title: str) -> str:
    messages = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "done"}]
    with session_scope(scene.engine) as db:
        rec = ChatThreadRecord(title=title, messages_json=json.dumps(messages), owner="user",
                               setup_json=json.dumps({"workspace_dir": str(scene.folder)}),
                               updated_at=utcnow() - timedelta(hours=1))
        db.add(rec)
        db.commit()
        return rec.id


def _agent_run(scene, task: str, summary: str) -> str:
    when = utcnow() - timedelta(hours=2)
    with session_scope(scene.engine) as db:
        s = Session(task=task, summary=summary, status=SessionStatus.COMPLETED, origin="chat",
                    workspace_path=str(scene.folder), created_at=when, finished_at=when,
                    options_json="{}")
        db.add(s)
        db.commit()
        db.refresh(s)
        db.add(AgentRun(session_id=s.id))
        db.commit()
        return s.id


def _entries(scene) -> list[WorkEntry]:
    with session_scope(scene.engine) as db:
        return list(db.exec(select(WorkEntry)))


def _reported_rows(scene) -> list[ReportedChat]:
    with session_scope(scene.engine) as db:
        return list(db.exec(select(ReportedChat)))


# --------------------------------------------------------------------------- #
# OT2: nothing keeps or tells the secret
# --------------------------------------------------------------------------- #
def test_a_secret_in_the_first_words_never_reaches_the_record_a_prompt_or_another_app(scene):
    door = _door(scene)
    # (a) a Claude Code chat whose first prompt holds a key and a password
    _claude_code_chat(scene, C1, f"Set up the site build, the key is {FAKE} and {PW} for the staging login")
    # ... and one whose key crosses character 120 of the first prompt
    filler = ("Tidy the report layout and " * 6)[:115].rstrip()
    _claude_code_chat(scene, C2, f"{filler} {FAKE} then check the totals")
    # (b) a Codex chat whose report's summary holds the secret
    _codex_chat(scene, X1, "Fix the import parser", "09:00", "09:20")
    reported.record(scene.engine, "codex", {"title": "Parser fix", "folders": [str(scene.folder)],
                                            "summary": f"Fixed the date column.\ntoken: {PW_VALUE} and {FAKE}"},
                    now=datetime(2026, 11, 11, 9, 25))
    # (c) an agent run whose task holds the key, and an Iron Jarvis chat
    _agent_run(scene, f"Deploy with {FAKE}\nthen report back", f"Used {PW}; deployed.")
    own = _own_chat(scene, f"Rotate the key {FAKE} for the website")
    # (d) Claude desktop reports a chat through the real door, with no chat id
    got = door.call("chat_record", {"title": f"Website login {FAKE}", "folders": [str(scene.folder)],
                                    "summary": f"Stored {PW} in the settings.",
                                    "link": "https://claude.ai/chat/abc"})
    assert got["isError"] is False, got
    assert _leaks(got["content"][0]["text"]) == [], got["content"][0]["text"]

    scene.wm.refresh(force=True)
    rows = _entries(scene)
    assert len(rows) >= 6, [(r.id, r.title) for r in rows]
    for row in rows:
        assert _leaks([row.title, row.note, row.stamp]) == [], (row.id, row.title, row.note, row.stamp)
    by_id = {r.id: r for r in rows}
    assert "***" in by_id[f"chat:{own}"].title
    assert "***" in by_id[f"{KIND_OUTSIDE}:claude-code:{C1}"].title
    assert "***" in by_id[f"{KIND_OUTSIDE}:codex:{X1}"].note
    assert any(r.kind == "run" and "***" in r.title and "***" in r.note for r in rows)
    assert any(r.id.startswith(f"{KIND_OUTSIDE}:claude-chat:r:") and "***" in r.title for r in rows)

    told = B.work_block(scene.platform, folder=str(scene.folder))
    assert "***" in told and _leaks(told) == [], told
    search = door.call("work_search", {"folder": str(scene.folder)})["content"][0]["text"]
    assert "***" in search and _leaks(search) == [], search
    listing = scene.client.get("/work-record/other-tools").json()
    assert "***" in json.dumps(listing) and _leaks(listing) == [], listing

    for rep in _reported_rows(scene):
        assert _leaks([rep.title, rep.summary, rep.link, rep.chat_key]) == [], (rep.title, rep.chat_key)
    with session_scope(scene.engine) as db:
        ledger = [r for r in db.exec(select(ToolInvocation)) if r.tool == "chat_record"]
        journal = [r for r in db.exec(select(UndoJournal)) if r.tool == "chat_record"]
    assert ledger, "the door call left no ledger row"
    for row in ledger:
        assert _leaks([row.args_json, row.output]) == [], (row.args_json, row.output)
    for row in journal:
        assert _leaks(row.pre_inline or "") == [], row.pre_inline


def test_a_report_with_a_secret_in_its_title_and_no_chat_id_is_undone(scene):
    ctx_kw = dict(workspace=scene.tmp, session_id="mcp:app:claude-desktop", agent_run_id="",
                  config=scene.platform.config, event_bus=scene.platform.event_bus, engine=scene.engine)
    from iron_jarvis.tools.base import ToolContext

    ctx = ToolContext(**ctx_kw)
    tool = ChatRecordTool(scene.platform)
    args = {"title": f"Website login {FAKE}", "summary": "Set it up."}
    undo = asyncio.run(tool.capture_undo(args, ctx))
    done = asyncio.run(tool.execute(args, ctx))
    assert done.ok and _leaks(done.output) == [], done.output
    assert len(reported.rows(scene.engine)) == 1
    assert _leaks(tool.redact_args(args)) == []
    asyncio.run(tool.revert(undo, ctx))
    assert reported.rows(scene.engine) == [], "Undo computed another key than the report"


def test_an_old_reported_row_is_masked_when_the_record_is_built(scene):
    """The guarantee at storage: a row stored before the mask (a reported
    chat's raw title) is masked by ``WorkMap._write`` itself."""
    with session_scope(scene.engine) as db:
        db.add(ReportedChat(id="rchat_old", app="claude-desktop", chat_key="id:old",
                            title=f"Old chat {FAKE}", summary="", folders_json=json.dumps([str(scene.folder)]),
                            files_json="[]", link="", reports=1, created_at=utcnow(), updated_at=utcnow()))
        db.commit()
    scene.wm.refresh(force=True)
    rows = [r for r in _entries(scene) if r.id.endswith("r:rchat_old")]
    assert len(rows) == 1
    assert "***" in rows[0].title and _leaks(rows[0].title) == [], rows[0].title


# --------------------------------------------------------------------------- #
# D1: rows stored before the mask, masked once at start
# --------------------------------------------------------------------------- #
def _seed_old_rows(engine, n: str) -> None:
    with session_scope(engine) as db:
        db.add(WorkEntry(id=f"chat:old{n}", kind="chat", ref=f"old{n}", title=f"Rotate {FAKE} now",
                         folder="C:/Work", folder_key="c:/work", note=f"Used {GOOGLE} there",
                         stamp=f"2026-11-01|C:/Work|0|0|0.0|Rotate {FAKE} now"))
        db.add(WorkEntry(id=f"chat:plain{n}", kind="chat", ref=f"plain{n}", title="Fix the parser",
                         folder="C:/Work", folder_key="c:/work", stamp="2026-11-01|C:/Work|0|0|0.0|Fix the parser"))
        old_key = "day:2026-11-01:" + " ".join(w for w in f"login {FAKE}".lower().replace("-", " ").split())
        db.add(ReportedChat(id=f"rchat_d1{n}", app="claude-desktop", chat_key=old_key,
                            title=f"Login {FAKE}", summary=f"Kept {PW} there", folders_json="[]",
                            files_json="[]", link="https://claude.ai/chat/abc?" + PW,
                            reports=1, created_at=utcnow(), updated_at=utcnow()))
        db.commit()


def _start(root: Path, *, count: list | None = None) -> None:
    app = create_app(str(root))
    if count is not None:
        def _seen(_conn, _cursor, statement, *_a):
            head = statement.lstrip().upper()
            if head.startswith(("UPDATE WORKENTRY", "UPDATE REPORTEDCHAT", "INSERT INTO _IRONJARVIS_META")):
                count.append(statement)
        event.listen(app.state.platform.engine, "before_cursor_execute", _seen)
    with TestClient(app):  # runs the start-up steps
        pass


def _marker_key() -> str:
    from iron_jarvis.workmap import masked_once

    return masked_once.MARKER


def _marker(engine) -> str:
    with engine.connect() as conn:
        row = conn.execute(text("SELECT value FROM _ironjarvis_meta WHERE key=:k"),
                           {"k": _marker_key()}).first()
    return row[0] if row else ""


def test_rows_stored_before_the_mask_are_masked_once_at_start(tmp_path, monkeypatch):
    from iron_jarvis.platform import build_platform

    root = tmp_path / "home"
    p = build_platform(str(root))
    _seed_old_rows(p.engine, "1")
    p.engine.dispose()

    _start(root)
    engine = build_platform(str(root)).engine
    with session_scope(engine) as db:
        entries = {e.id: e for e in db.exec(select(WorkEntry))}
        reps = list(db.exec(select(ReportedChat)))
    old = entries["chat:old1"]
    assert _leaks([old.title, old.note, old.stamp]) == [], (old.title, old.note, old.stamp)
    assert "***" in old.title and "***" in old.note and "***" in old.stamp
    assert entries["chat:plain1"].title == "Fix the parser"  # a row the mask does not change is left
    assert len(reps) == 1
    rep = reps[0]
    assert _leaks([rep.title, rep.summary, rep.link, rep.chat_key]) == [], (rep.title, rep.chat_key, rep.link)
    assert rep.chat_key.startswith("day:2026-11-01:")
    assert _marker(engine) == keys.mask_id()
    engine.dispose()

    # A second start writes nothing.
    writes: list = []
    _start(root, count=writes)
    assert writes == [], writes

    # A marker left by another (narrower) mask makes the pass run again.
    engine = build_platform(str(root)).engine
    _seed_old_rows(engine, "2")
    with engine.begin() as conn:
        conn.execute(text("UPDATE _ironjarvis_meta SET value='an-older-mask' WHERE key=:k"),
                     {"k": _marker_key()})
    engine.dispose()
    _start(root)
    engine = build_platform(str(root)).engine
    with session_scope(engine) as db:
        again = db.get(WorkEntry, "chat:old2")
        rep2 = db.get(ReportedChat, "rchat_d12")
    assert _leaks([again.title, again.note, again.stamp]) == []
    assert _leaks([rep2.title, rep2.summary, rep2.link, rep2.chat_key]) == []
    assert _marker(engine) == keys.mask_id()
    engine.dispose()

    # The marker names the mask itself: one more rule is another marker, so a
    # wider mask in a later release reaches these rows once more.
    import re

    from iron_jarvis.providers import failure_words

    before = keys.mask_id()
    wider = (re.compile(r"wider_[a-z]{9}"), "wider_***")
    monkeypatch.setattr(failure_words, "_FAILURE_MASKS", (*failure_words._FAILURE_MASKS, wider))
    assert keys.mask_id() != before


# --------------------------------------------------------------------------- #
# MS9: a title's own words make its key, whatever its letters
# --------------------------------------------------------------------------- #
def test_same_day_reports_with_titles_in_other_letters_are_two_rows(scene):
    t = datetime(2026, 11, 12, 10, 0)
    for title in ("Отчёт для банка", "План продаж", "Отчёт 2024", "План 2024"):
        reported.record(scene.engine, "claude-desktop", {"title": title}, now=t)
    reported.record(scene.engine, "claude-desktop", {"title": "Fix the parser"}, now=t)
    reported.record(scene.engine, "claude-desktop", {"title": "Fix the parser"}, now=t + timedelta(hours=1))
    rows = {r.title: r for r in _reported_rows(scene)}
    assert set(rows) == {"Отчёт для банка", "План продаж", "Отчёт 2024", "План 2024", "Fix the parser"}
    assert rows["Fix the parser"].reports == 2
    assert reported.chat_key("claude-desktop", "Résumé draft", "", t) == "day:2026-11-12:résumé draft"
    assert reported.chat_key("claude-desktop", "→ ★", "", t) == "day:2026-11-12:→ ★"


# --------------------------------------------------------------------------- #
# OT3: a coding chat keeps its own report's summary
# --------------------------------------------------------------------------- #
def test_a_coding_chat_keeps_its_own_summary_when_a_later_chats_report_falls_in_its_time(scene):
    _codex_chat(scene, XA, "Fix the import parser", "10:00", "10:20")
    _codex_chat(scene, XB, "Add the export button", "10:25", "10:40")
    reported.record(scene.engine, "codex", {"title": "A's work", "folders": [str(scene.folder)],
                                            "summary": "A's summary"}, now=datetime(2026, 11, 11, 10, 21))
    reported.record(scene.engine, "codex", {"title": "B's work", "folders": [str(scene.folder)],
                                            "summary": "B's summary"}, now=datetime(2026, 11, 11, 10, 41))
    scene.wm.refresh(force=True)
    notes = {r.ref: r.note for r in _entries(scene) if r.kind == KIND_OUTSIDE}
    assert notes == {f"codex:{XA}": "A's summary", f"codex:{XB}": "B's summary"}
    listing = scene.client.get("/work-record/other-tools").json()
    codex = next(t for t in listing["tools"] if t["tool"] == "codex")
    shown = {c["id"]: c.get("summary") for f in codex["folders"] for c in f["chats"]}
    assert shown == {XA: "A's summary", XB: "B's summary"}  # the sidebar gives the same answer
