"""v1.528.0: Iron Jarvis's connector for Claude desktop and Codex.

The owner connects an app with one press; the app's model then records its
chats (``chat_record``) and looks up the owner's work (``work_search``) over
``POST /mcp``, with a key of its own that opens that ONE box and nothing else.

Every test runs the REAL app factory with the install bearer set (the shipped
shape). Claude desktop's files live under ``mcpserver.apps.TEST_ROOT`` (set
for the whole session by ``tests/conftest.py``) and Codex's under the
session's ``CODEX_HOME``: no test touches the developer's own apps.
"""

from __future__ import annotations

import io
import json
import os
import tomllib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from iron_jarvis.browser.panetokens import INSTALL_TOKEN_ENV
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import ToolInvocation
from iron_jarvis.daemon import chat_turn
from iron_jarvis.daemon.app import create_app
from iron_jarvis.mcp.client import PROTOCOL_VERSION as CLIENT_PROTOCOL_VERSION
from iron_jarvis.mcpserver import apps, stdio_shim
from iron_jarvis.mcpserver.server import CAPABILITY_RECORD, MCP_APP_LINE, granted_capabilities
from iron_jarvis.mcpserver.session import SESSION_HEADER
from iron_jarvis.workmap import block as B
from iron_jarvis.workmap import outside, reported
from iron_jarvis.workmap.index import work_map
from iron_jarvis.workmap.models import KIND_OUTSIDE, ReportedChat, WorkEntry

INSTALL = "install-bearer-for-the-whole-app"
ACCEPT = "application/json, text/event-stream"
X1 = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"


@pytest.fixture()
def scene(tmp_path, monkeypatch):
    monkeypatch.setenv(INSTALL_TOKEN_ENV, INSTALL)
    monkeypatch.setattr(chat_turn, "GROUNDING_SOFT_S", 60.0)
    other = tmp_path / "other-apps"
    monkeypatch.setattr(apps, "TEST_ROOT", str(other))
    codex_home = tmp_path / "codex-home"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-home"))
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
    s.tmp, s.folder, s.codex_home = tmp_path, folder, codex_home
    s.claude_dir = other / "Claude"
    s.wm = work_map(s.engine, s.platform.config.home)
    yield s
    outside.forget_cache()


def _install(scene, app: str) -> Path:
    path = apps.config_path(app)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _connect(scene, app: str):
    return scene.client.post(f"/work-record/other-apps/{app}/connect")


def _key(scene, app: str) -> str:
    return scene.platform.app_connections.key_path(app).read_text(encoding="utf-8")


class Door:
    """One app's MCP session against the real /mcp door."""

    def __init__(self, scene, token: str) -> None:
        self.c = TestClient(scene.app)
        self.token = token
        self.n = 0
        r = self.post("initialize", {"protocolVersion": CLIENT_PROTOCOL_VERSION,
                                     "clientInfo": {"name": "pytest", "version": "1"}})
        assert r.status_code == 200, r.text
        self.sid = r.headers[SESSION_HEADER]
        self.instructions = r.json()["result"]["instructions"]

    def post(self, method: str, params: dict | None = None):
        self.n += 1
        headers = {"Accept": ACCEPT, "Authorization": f"Bearer {self.token}"}
        if getattr(self, "sid", ""):
            headers[SESSION_HEADER] = self.sid
        return self.c.post("/mcp", json={"jsonrpc": "2.0", "id": self.n, "method": method,
                                         "params": params or {}}, headers=headers)

    def names(self) -> list[str]:
        return sorted(t["name"] for t in self.post("tools/list").json()["result"]["tools"])

    def call(self, name: str, arguments: dict):
        return self.post("tools/call", {"name": name, "arguments": arguments})


# --------------------------------------------------------------------------- #
# connecting Claude desktop
# --------------------------------------------------------------------------- #
def test_connect_claude_adds_one_entry_keeps_the_rest_and_never_writes_the_key(scene):
    path = _install(scene, "claude-desktop")
    before = {"mcpServers": {"quickbooks": {"command": "node.exe", "args": ["x.js"], "env": {"QB_LIVE": "0"}}},
              "preferences": {"menuBar": True}, "coworkUserFilesPath": "D:/Cowork"}
    path.write_text(json.dumps(before), encoding="utf-8")
    r = _connect(scene, "claude-desktop")
    assert r.status_code == 200, r.text
    assert r.json()["connected"] is True
    assert "Restart Claude desktop" in r.json()["words"]
    after = json.loads(path.read_text(encoding="utf-8"))
    assert after["preferences"] == before["preferences"]
    assert after["coworkUserFilesPath"] == before["coworkUserFilesPath"]
    assert after["mcpServers"]["quickbooks"] == before["mcpServers"]["quickbooks"]
    entry = after["mcpServers"]["iron-jarvis"]
    key_path = scene.platform.app_connections.key_path("claude-desktop")
    assert entry["env"] == {"IRONJARVIS_MCP_TOKEN_FILE": str(key_path)}
    assert entry["args"][-1] == "http://127.0.0.1:8787/mcp"
    token = _key(scene, "claude-desktop")
    assert len(token) >= 40
    assert token not in path.read_text(encoding="utf-8")
    state = (scene.platform.config.home / "secrets" / "app-connections.json").read_text(encoding="utf-8")
    assert token not in state  # only its digest
    kept = list((scene.platform.config.home / "backups" / "other-apps").iterdir())
    assert len(kept) == 1 and json.loads(kept[0].read_text(encoding="utf-8")) == before


def test_connect_needs_the_app_on_this_pc_and_changes_nothing_without_it(scene):
    r = _connect(scene, "claude-desktop")
    assert r.status_code == 409
    assert r.json()["detail"] == "Claude desktop is not set up on this PC, so there is nothing to connect."
    assert not scene.platform.app_connections.key_path("claude-desktop").exists()
    assert scene.platform.app_connections.connected() == set()


@pytest.mark.parametrize("text,why", [
    ('{"mcpServers": {"iron-jarvis": {"command": "someone-elses.exe"}}}', "did not add"),
    ("{not json", "not valid JSON"),
    ('{"mcpServers": ["a"]}', "list of tools"),
])
def test_connect_claude_refuses_a_file_it_cannot_safely_change(scene, text, why):
    path = _install(scene, "claude-desktop")
    path.write_text(text, encoding="utf-8")
    r = _connect(scene, "claude-desktop")
    assert r.status_code == 409 and why in r.json()["detail"]
    assert path.read_text(encoding="utf-8") == text
    assert not scene.platform.app_connections.key_path("claude-desktop").exists()


def test_disconnect_claude_removes_only_its_entry_and_ends_the_key_at_once(scene):
    path = _install(scene, "claude-desktop")
    path.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}, "preferences": {"a": 1}}), encoding="utf-8")
    assert _connect(scene, "claude-desktop").status_code == 200
    token = _key(scene, "claude-desktop")
    assert scene.platform.app_connections.resolve(token) is not None
    r = scene.client.post("/work-record/other-apps/claude-desktop/disconnect")
    assert r.status_code == 200 and r.json()["connected"] is False
    assert json.loads(path.read_text(encoding="utf-8")) == {"mcpServers": {"other": {"command": "x"}}, "preferences": {"a": 1}}
    assert not scene.platform.app_connections.key_path("claude-desktop").exists()
    assert scene.platform.app_connections.resolve(token) is None
    door = TestClient(scene.app).post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                                      headers={"Accept": ACCEPT, "Authorization": f"Bearer {token}"})
    assert door.status_code == 401


def test_the_key_outlives_a_restart(scene):
    _install(scene, "claude-desktop")
    _connect(scene, "claude-desktop")
    token = _key(scene, "claude-desktop")
    again = apps.AppConnections(scene.platform.config.home)
    grant = again.resolve(token)
    assert grant is not None and grant.pane_id == "app:claude-desktop"
    assert again.resolve(token[:-1] + ("A" if token[-1] != "A" else "B")) is None


def test_connecting_again_replaces_the_key(scene):
    _install(scene, "claude-desktop")
    _connect(scene, "claude-desktop")
    first = _key(scene, "claude-desktop")
    _connect(scene, "claude-desktop")
    assert scene.platform.app_connections.resolve(first) is None
    assert scene.platform.app_connections.resolve(_key(scene, "claude-desktop")) is not None


# --------------------------------------------------------------------------- #
# connecting Codex
# --------------------------------------------------------------------------- #
CODEX_CONFIG = """# my Codex settings
model = "gpt-5.3-codex"

[mcp_servers.node_repl]
command = "node"
args = ["repl.js"]
"""


def test_connect_codex_adds_one_marked_block_and_disconnect_gives_the_file_back_exactly(scene):
    path = _install(scene, "codex")
    path.write_bytes(CODEX_CONFIG.encode("utf-8"))
    assert _connect(scene, "codex").status_code == 200
    text = path.read_text(encoding="utf-8")
    assert text.startswith(CODEX_CONFIG)
    data = tomllib.loads(text)
    assert data["mcp_servers"]["node_repl"] == {"command": "node", "args": ["repl.js"]}
    entry = data["mcp_servers"]["iron-jarvis"]
    assert entry["env"] == {"IRONJARVIS_MCP_TOKEN_FILE": str(scene.platform.app_connections.key_path("codex"))}
    assert _key(scene, "codex") not in text
    assert scene.client.post("/work-record/other-apps/codex/disconnect").status_code == 200
    assert path.read_bytes() == CODEX_CONFIG.encode("utf-8")


def test_connect_codex_twice_keeps_one_block(scene):
    path = _install(scene, "codex")
    path.write_text(CODEX_CONFIG, encoding="utf-8")
    _connect(scene, "codex")
    _connect(scene, "codex")
    assert path.read_text(encoding="utf-8").count(apps.CODEX_BEGIN) == 1


@pytest.mark.parametrize("text,why", [
    ('[mcp_servers.iron-jarvis]\ncommand = "theirs"\n', "did not add"),
    ("model = = broken\n", "not valid TOML"),
])
def test_connect_codex_refuses_what_it_cannot_safely_change(scene, text, why):
    path = _install(scene, "codex")
    path.write_text(text, encoding="utf-8")
    r = _connect(scene, "codex")
    assert r.status_code == 409 and why in r.json()["detail"]
    assert path.read_text(encoding="utf-8") == text


def test_the_list_says_which_apps_can_be_connected(scene):
    _install(scene, "claude-desktop")
    got = {a["app"]: a for a in scene.client.get("/work-record/other-tools").json()["apps"]}
    assert got["claude-desktop"]["installed"] is True and got["claude-desktop"]["connected"] is False
    assert got["codex"]["installed"] is False
    _connect(scene, "claude-desktop")
    got = {a["app"]: a for a in scene.client.get("/work-record/other-tools").json()["apps"]}
    assert got["claude-desktop"]["connected"] is True and got["claude-desktop"]["count"] == 0


# --------------------------------------------------------------------------- #
# the door: one box, two tools
# --------------------------------------------------------------------------- #
def _door(scene, app="claude-desktop") -> Door:
    _install(scene, app)
    assert _connect(scene, app).status_code == 200
    return Door(scene, _key(scene, app))


def test_an_apps_key_lists_exactly_the_two_tools_and_is_told_what_they_are_for(scene):
    door = _door(scene)
    assert door.names() == ["chat_record", "work_search"]
    assert door.instructions.startswith("Iron Jarvis connector for Claude desktop.")
    assert MCP_APP_LINE in door.instructions


@pytest.mark.parametrize("name", ["browser_click", "memory_search", "code_handoff_start", "write_file"])
def test_an_apps_key_reaches_no_other_tool(scene, name):
    door = _door(scene)
    r = door.call(name, {})
    assert r.status_code == 403
    assert r.json()["error"]["message"] == (
        f"{name} is not one of the tools Iron Jarvis gives Claude desktop: it has chat_record and work_search only."
    )
    with session_scope(scene.engine) as db:
        assert [x for x in db.exec(select(ToolInvocation)) if x.tool == name] == []


def test_a_pane_that_claims_the_record_box_gets_nothing_for_it():
    from types import MappingProxyType

    from iron_jarvis.browser.panetokens import PaneGrant
    from iron_jarvis.core.ids import utcnow

    pane = PaneGrant(pane_id="term_x", capabilities=MappingProxyType({"record": True, "memory": True}),
                     minted_at=utcnow())
    assert granted_capabilities(pane) == ["memory"]
    app = PaneGrant(pane_id="app:codex", capabilities=MappingProxyType({"record": True, "browser": True}),
                    minted_at=utcnow())
    assert granted_capabilities(app) == [CAPABILITY_RECORD]


def test_chat_record_keeps_the_chat_for_the_keys_app_and_a_second_report_updates_it(scene):
    door = _door(scene)
    r = door.call("chat_record", {"title": "Bosch 2026 projection", "summary": "Built the workpaper.\nOpen: depreciation",
                                  "folders": [str(scene.folder), "relative/not/kept"],
                                  "files": [str(scene.folder / "Bosch.xlsx")],
                                  "link": "https://claude.ai/chat/abc", "app": "codex"})
    assert r.status_code == 200, r.text
    assert r.json()["result"]["isError"] is False
    assert "Recorded" in r.json()["result"]["content"][0]["text"]
    rows = reported.rows(scene.engine)
    assert len(rows) == 1 and rows[0]["app"] == "claude-desktop"
    assert rows[0]["folders"] == [str(scene.folder)]
    assert rows[0]["summary"] == "Built the workpaper.\nOpen: depreciation"
    door.call("chat_record", {"title": "Bosch 2026 projection", "summary": "Added the estimate letter."})
    rows = reported.rows(scene.engine)
    assert len(rows) == 1 and rows[0]["reports"] == 2
    assert rows[0]["summary"] == "Added the estimate letter."
    assert rows[0]["folders"] == [str(scene.folder)]  # kept from the first report


@pytest.mark.parametrize("link,kept", [("https://claude.ai/chat/1", "https://claude.ai/chat/1"),
                                       ("file:///C:/x.exe", ""), ("javascript:alert(1)", "")])
def test_only_a_web_link_is_kept(scene, link, kept):
    got = reported.record(scene.engine, "claude-desktop", {"title": "A chat", "link": link})
    assert got["link"] == kept


def test_chat_record_with_no_title_is_refused_in_words(scene):
    door = _door(scene)
    r = door.call("chat_record", {"summary": "x"})
    assert r.json()["result"]["isError"] is True
    assert "title" in r.json()["result"]["content"][0]["text"]  # the registry's own check
    r = door.call("chat_record", {"title": "   ", "summary": "x"})
    assert r.json()["result"]["content"][0]["text"] == reported.NO_TITLE
    assert reported.rows(scene.engine) == []


def test_the_tools_refuse_a_caller_that_is_not_a_connected_app(scene):
    import asyncio

    from iron_jarvis.tools.base import ToolContext
    from iron_jarvis.workmap.connector_tools import connector_tools

    ctx = ToolContext(workspace=scene.tmp, session_id="chat", agent_run_id="", config=scene.platform.config,
                      event_bus=scene.platform.event_bus, engine=scene.engine)
    for tool in connector_tools(scene.platform):
        got = asyncio.run(tool.execute({"title": "x", "query": "x"}, ctx))
        assert got.ok is False and "only for an app connected" in got.error
    assert reported.rows(scene.engine) == []


def test_undo_of_a_report_removes_the_new_row(scene):
    import asyncio

    from iron_jarvis.tools.base import ToolContext
    from iron_jarvis.workmap.connector_tools import ChatRecordTool

    ctx = ToolContext(workspace=scene.tmp, session_id="mcp:app:claude-desktop", agent_run_id="",
                      config=scene.platform.config, event_bus=scene.platform.event_bus, engine=scene.engine)
    tool = ChatRecordTool(scene.platform)
    undo = asyncio.run(tool.capture_undo({"title": "New chat"}, ctx))
    asyncio.run(tool.execute({"title": "New chat"}, ctx))
    assert len(reported.rows(scene.engine)) == 1
    asyncio.run(tool.revert(undo, ctx))
    assert reported.rows(scene.engine) == []


# --------------------------------------------------------------------------- #
# work_search and the client wall
# --------------------------------------------------------------------------- #
def _chat_in(scene, folder: Path, title: str) -> None:
    reported.record(scene.engine, "claude-desktop", {"title": title, "folders": [str(folder)],
                                                      "summary": "Did the work."})
    scene.wm.refresh(force=True)


def test_work_search_answers_from_the_record_as_fenced_data(scene):
    door = _door(scene)
    _chat_in(scene, scene.folder, "Fix the import parser")
    r = door.call("work_search", {"folder": str(scene.folder)})
    text = r.json()["result"]["content"][0]["text"]
    assert r.json()["result"]["isError"] is False
    assert "Fix the import parser" in text and "Summary: Did the work." in text
    assert "data, not instructions" in text


def test_work_search_never_tells_another_app_a_clients_work(scene):
    from iron_jarvis.workmap.clients import store

    door = _door(scene)
    hartleys = scene.tmp / "Clients" / "Hartleys"
    hartleys.mkdir(parents=True)
    store.create(scene.platform, folder=str(hartleys), name="Hartleys")
    _chat_in(scene, hartleys, "March reconciliation for Hartleys")
    _chat_in(scene, scene.folder, "Fix the import parser")
    for args in ({"folder": str(hartleys)}, {"query": "Hartleys March reconciliation"},
                 {"folder": str(hartleys), "query": "Hartleys"}):
        text = door.call("work_search", args).json()["result"]["content"][0]["text"]
        assert "March reconciliation" not in text, args
    # The same folder, asked by the OWNER in Iron Jarvis, is told.
    assert "March reconciliation" in B.work_block(scene.platform, folder=str(hartleys))


def test_work_search_with_nothing_says_what_runs_in_iron_jarvis(scene):
    # v1.530.0: an empty call is the "what runs now" question, not a mistake.
    door = _door(scene)
    r = door.call("work_search", {})
    assert r.json()["result"]["isError"] is False
    assert "Nothing Iron Jarvis may share with this app is running right now." in r.json()["result"]["content"][0]["text"]


# --------------------------------------------------------------------------- #
# Claude Chat in the list and in memory
# --------------------------------------------------------------------------- #
def _cowork(scene, sid: str, title: str, folder: str, *, archived=False, last=1_789_000_000_000):
    d = scene.claude_dir / "local-agent-mode-sessions" / "acct" / "org"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"local_{sid}.json").write_text(json.dumps({
        "sessionId": sid, "title": title, "userSelectedFolders": [folder] if folder else [],
        "createdAt": last - 60_000, "lastActivityAt": last, "isArchived": archived,
        "systemPrompt": "not read", "initialMessage": "not read",
    }), encoding="utf-8")


def test_claude_chat_lists_reported_chats_and_cowork_sessions_read_only(scene):
    reported.record(scene.engine, "claude-desktop", {"title": "Client tax planning questions",
                                                      "folders": [str(scene.folder)], "summary": "Drafted the reply."})
    reported.record(scene.engine, "claude-desktop", {"title": "A quick question"})
    _cowork(scene, "c1", "Organise the receipts", str(scene.folder))
    _cowork(scene, "c2", "Old archived one", str(scene.folder), archived=True)
    view = scene.client.get("/work-record/other-tools").json()
    chat = next(t for t in view["tools"] if t["tool"] == "claude-chat")
    assert chat["label"] == "Claude Chat" and chat["openable"] is False and chat["total"] == 3
    titles = {c["title"]: c for f in chat["folders"] for c in f["chats"]}
    assert set(titles) == {"Client tax planning questions", "A quick question", "Organise the receipts"}
    assert titles["Client tax planning questions"]["summary"] == "Drafted the reply."
    assert {f["name"] for f in chat["folders"]} == {"Parser", "No folder"}


def test_claude_chat_rows_enter_memory_with_their_summary_under_the_switch(scene):
    reported.record(scene.engine, "claude-desktop", {"title": "Client tax planning questions",
                                                      "folders": [str(scene.folder)], "summary": "Drafted the reply."})
    reported.record(scene.engine, "claude-desktop", {"title": "No folder chat"})
    _cowork(scene, "c1", "Organise the receipts", str(scene.folder))
    scene.wm.refresh(force=True)
    with session_scope(scene.engine) as db:
        rows = {r.title: r for r in db.exec(select(WorkEntry).where(WorkEntry.kind == KIND_OUTSIDE))}
    assert set(rows) == {"Client tax planning questions", "Organise the receipts"}  # a row needs a folder
    assert rows["Client tax planning questions"].tool == "Claude Chat"
    assert rows["Client tax planning questions"].note == "Drafted the reply."
    text = B.work_block(scene.platform, folder=str(scene.folder))
    assert "Client tax planning questions" in text and "Summary: Drafted the reply." in text
    scene.platform.config.other_tools_in_memory = "codex"
    assert "Client tax planning questions" not in B.work_block(scene.platform, folder=str(scene.folder))
    scene.wm.refresh(force=True)
    with session_scope(scene.engine) as db:
        assert list(db.exec(select(WorkEntry).where(WorkEntry.kind == KIND_OUTSIDE))) == []


def test_a_codex_report_adds_its_summary_to_the_codex_chat_it_belongs_to(scene):
    home = scene.codex_home / "sessions" / "2026" / "11" / "11"
    home.mkdir(parents=True)
    lines = [
        {"timestamp": "2026-11-11T09:00:00.000Z", "type": "session_meta",
         "payload": {"id": X1, "cwd": str(scene.folder), "originator": "Codex Desktop", "source": "vscode"}},
        {"timestamp": "2026-11-11T09:00:00.000Z", "type": "response_item",
         "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "Fix the parser"}]}},
        {"timestamp": "2026-11-11T09:20:00.000Z", "type": "response_item",
         "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Done"}]}},
    ]
    (home / f"rollout-2026-11-11T09-00-00-{X1}.jsonl").write_text(
        "\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")
    from datetime import datetime

    reported.record(scene.engine, "codex", {"title": "Parser fix", "folders": [str(scene.folder)],
                                            "summary": "Fixed the date column."},
                    now=datetime(2026, 11, 11, 9, 25))
    reported.record(scene.engine, "codex", {"title": "Unrelated", "folders": [str(scene.tmp / "Elsewhere")],
                                            "summary": "Nowhere to go."}, now=datetime(2026, 11, 11, 9, 25))
    view = scene.client.get("/work-record/other-tools").json()
    codex = next(t for t in view["tools"] if t["tool"] == "codex")
    only = codex["folders"][0]["chats"]
    assert [(c["title"], c.get("summary")) for c in only] == [("Fix the parser", "Fixed the date column.")]
    assert not any(t["tool"] == "claude-chat" for t in view["tools"])  # a Codex report is never its own row
    scene.wm.refresh(force=True)
    with session_scope(scene.engine) as db:
        rows = list(db.exec(select(WorkEntry).where(WorkEntry.kind == KIND_OUTSIDE)))
    assert [(r.title, r.note) for r in rows] == [("Fix the parser", "Fixed the date column.")]


def test_a_report_the_owner_deleted_is_not_told_from_a_row_not_yet_rebuilt(scene):
    got = reported.record(scene.engine, "claude-desktop", {"title": "Client tax planning questions",
                                                            "folders": [str(scene.folder)]})
    scene.wm.refresh(force=True)
    assert "Client tax planning questions" in B.work_block(scene.platform, folder=str(scene.folder))
    with session_scope(scene.engine) as db:
        db.delete(db.get(ReportedChat, got["id"]))
        db.commit()
    assert "Client tax planning questions" not in B.work_block(scene.platform, folder=str(scene.folder))


# --------------------------------------------------------------------------- #
# the bridge reads the key from Iron Jarvis's own file
# --------------------------------------------------------------------------- #
def test_the_bridge_reads_the_key_file_and_says_so_when_it_is_gone(tmp_path):
    key = tmp_path / "app-claude-desktop.key"
    key.write_text("k" * 43 + "\n", encoding="utf-8")
    seen = {}

    class Shim:
        def __init__(self, url, token):
            seen["url"], seen["token"] = url, token

        def run(self, stdin, stdout):
            return 0

    env = {stdio_shim.MCP_TOKEN_FILE_ENV: str(key)}
    io_kw = {"stdin": io.StringIO(), "stdout": io.StringIO()}
    assert stdio_shim.main(["mcp-stdio", "http://127.0.0.1:8787/mcp"], env=env, stderr=io.StringIO(),
                           shim_factory=Shim, **io_kw) == 0
    assert seen == {"url": "http://127.0.0.1:8787/mcp", "token": "k" * 43}
    key.unlink()
    err = io.StringIO()
    assert stdio_shim.main(["mcp-stdio", "http://127.0.0.1:8787/mcp"], env=env, stderr=err, shim_factory=Shim,
                           **io_kw) == 2
    assert "connect this app again in Iron Jarvis" in err.getvalue()


def test_no_test_reaches_the_developers_own_apps():
    assert apps.TEST_ROOT
    assert str(apps.claude_config_path()).startswith(str(apps.TEST_ROOT))
    assert os.environ.get("CODEX_HOME")


def test_a_codex_report_outside_the_chats_time_goes_nowhere():
    from datetime import datetime

    chat = {"id": X1, "folder": "C:/Work/Parser", "started": "2026-11-11T09:00:00+00:00",
            "ended": "2026-11-11T09:20:00+00:00"}

    def rep(when: datetime, folder="C:/Work/Parser"):
        return {"folder": folder, "ended": when.isoformat(), "summary": "s"}

    assert reported.match_codex([rep(datetime(2026, 11, 11, 9, 49))], [chat]) == {X1: rep(datetime(2026, 11, 11, 9, 49))}
    assert reported.match_codex([rep(datetime(2026, 11, 11, 9, 51))], [chat]) == {}  # 31 minutes after its end
    assert reported.match_codex([rep(datetime(2026, 11, 11, 8, 58))], [chat]) == {}  # before it began
    assert reported.match_codex([rep(datetime(2026, 11, 12, 9, 10))], [chat]) == {}  # the next day
    assert reported.match_codex([rep(datetime(2026, 11, 11, 9, 10), "C:/Work/Other")], [chat]) == {}


def test_disconnect_gives_back_a_claude_file_that_had_no_connectors_exactly(scene):
    path = _install(scene, "claude-desktop")
    before = {"preferences": {"sidebarMode": "chat"}}
    path.write_text(json.dumps(before), encoding="utf-8")
    _connect(scene, "claude-desktop")
    assert "iron-jarvis" in json.loads(path.read_text(encoding="utf-8"))["mcpServers"]
    scene.client.post("/work-record/other-apps/claude-desktop/disconnect")
    assert json.loads(path.read_text(encoding="utf-8")) == before
