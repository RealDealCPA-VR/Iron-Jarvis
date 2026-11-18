"""Fourth fix plan, group 29 (MS8, and the refused key the bridge reads).

1. A TOOL A CONNECTED APP (OR A TERMINAL) MAY NOT USE IS REFUSED AND RECORDED.
   The door keeps its 403 (a fact about the key, in the status line), and the
   refusal is also written through ``registry.invoke(..., deny_reason=...)``:
   exactly one denied ``ToolInvocation`` row and one ``tool.denied`` event
   under the asker, in the door's own sentence, and the tool never runs
   (CLAUDE.md: "A tool the turn was not given is refused and recorded").
2. A KEY THE DOOR DOES NOT KNOW (made up, or an app's key after Disconnect)
   answers 401 with a JSON body that carries ``"reason": "unknown_token"``,
   which the bridge (``stdio_shim``) reads to tell the user what to do.

Every test drives the REAL app factory with the install bearer set (the
shipped shape) and the REAL registry; only the tool's own ``execute`` /
``capture_undo`` are watched, to prove nothing ran.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from iron_jarvis.browser.panetokens import INSTALL_TOKEN_ENV, REASON_UNKNOWN_TOKEN
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.events import EventType
from iron_jarvis.core.models import ToolInvocation
from iron_jarvis.daemon.app import create_app
from iron_jarvis.mcp.client import PROTOCOL_VERSION as CLIENT_PROTOCOL_VERSION
from iron_jarvis.mcpserver import apps
from iron_jarvis.mcpserver.server import capability_refused_message, harness_session_id
from iron_jarvis.mcpserver.session import SESSION_HEADER

INSTALL = "install-bearer-for-the-whole-app"
ACCEPT = "application/json, text/event-stream"
APP_PANE = "app:claude-desktop"
MEMORY_PANE = "term_g29d_memory"


@pytest.fixture()
def real(tmp_path, monkeypatch):
    monkeypatch.setenv(INSTALL_TOKEN_ENV, INSTALL)
    monkeypatch.setattr(apps, "TEST_ROOT", str(tmp_path / "other-apps"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-home"))
    app = create_app(str(tmp_path / "home"))
    return app


def _connect_claude_desktop(app) -> str:
    platform = app.state.platform
    path = apps.config_path("claude-desktop")
    path.parent.mkdir(parents=True, exist_ok=True)
    owner = TestClient(app, headers={"Authorization": f"Bearer {INSTALL}"})
    r = owner.post("/work-record/other-apps/claude-desktop/connect")
    assert r.status_code == 200, r.text
    return platform.app_connections.key_path("claude-desktop").read_text(encoding="utf-8").strip()


class Door:
    def __init__(self, app, token: str) -> None:
        self.c = TestClient(app)
        self.token = token
        self.n = 0
        self.sid = ""
        r = self.post("initialize", {"protocolVersion": CLIENT_PROTOCOL_VERSION,
                                     "clientInfo": {"name": "pytest", "version": "1"}})
        assert r.status_code == 200, r.text
        self.sid = r.headers[SESSION_HEADER]

    def post(self, method: str, params: dict | None = None):
        self.n += 1
        headers = {"Accept": ACCEPT, "Authorization": f"Bearer {self.token}"}
        if self.sid:
            headers[SESSION_HEADER] = self.sid
        return self.c.post("/mcp", json={"jsonrpc": "2.0", "id": self.n, "method": method,
                                         "params": params or {}}, headers=headers)

    def call(self, name: str, arguments: dict):
        return self.post("tools/call", {"name": name, "arguments": arguments})


def _rows(app, name: str) -> list[ToolInvocation]:
    with session_scope(app.state.platform.engine) as db:
        return [r for r in db.exec(select(ToolInvocation)).all() if r.tool == name]


def _denied_events(app, name: str) -> list:
    return [e for e in app.state.platform.event_bus.history
            if e.type == EventType.TOOL_DENIED and (e.payload or {}).get("tool") == name]


def _watch_tool(app, monkeypatch, name: str) -> list[str]:
    """Record every time the REAL tool would do anything (its own methods)."""
    tool = app.state.platform.registry.get(name)
    assert tool is not None, f"{name} is not registered in the real app"
    ran: list[str] = []
    real_execute = tool.execute

    async def execute(args, ctx):
        ran.append("execute")
        return await real_execute(args, ctx)

    monkeypatch.setattr(tool, "execute", execute)
    real_capture = getattr(tool, "capture_undo", None)
    if real_capture is not None:
        async def capture_undo(args, ctx):
            ran.append("capture_undo")
            return await real_capture(args, ctx)

        monkeypatch.setattr(tool, "capture_undo", capture_undo)
    return ran


# --------------------------------------------------------------------------- #
# 1. refused AND recorded
# --------------------------------------------------------------------------- #
def test_a_connected_apps_call_outside_its_grant_is_403_one_denied_row_and_nothing_runs(real, monkeypatch):
    door = Door(real, _connect_claude_desktop(real))
    ran = _watch_tool(real, monkeypatch, "browser_click")
    r = door.call("browser_click", {"selector": "#buy"})
    assert r.status_code == 403, r.text
    sentence = capability_refused_message("browser_click", APP_PANE)
    assert r.json()["error"]["message"] == sentence
    rows = _rows(real, "browser_click")
    assert len(rows) == 1, f"expected one denied row, got {len(rows)}"
    row = rows[0]
    assert row.ok is False
    assert row.session_id == harness_session_id(APP_PANE)
    assert sentence in row.output
    assert row.verdict != "allow"
    events = _denied_events(real, "browser_click")
    assert len(events) == 1
    assert events[0].session_id == harness_session_id(APP_PANE)
    assert events[0].payload["reason"] == sentence
    assert ran == [], f"the refused tool ran: {ran}"


def test_a_made_up_tool_name_from_an_app_is_403_and_one_failed_row(real):
    door = Door(real, _connect_claude_desktop(real))
    name = "g29d_no_such_tool"
    r = door.call(name, {})
    assert r.status_code == 403, r.text
    rows = _rows(real, name)
    assert len(rows) == 1 and rows[0].ok is False
    assert rows[0].session_id == harness_session_id(APP_PANE)


def test_a_memory_terminal_calling_memory_write_is_403_one_denied_row_and_memory_unchanged(real, monkeypatch):
    platform = real.state.platform

    class Pane:
        id = MEMORY_PANE
        capabilities = {"memory": True}
        cwd = ""
        alive = True

    platform.pane_tokens.pane_lookup = lambda pane_id: Pane() if pane_id == MEMORY_PANE else None
    token = platform.pane_tokens.mint(MEMORY_PANE, {"memory": True})
    door = Door(real, token)
    ran = _watch_tool(real, monkeypatch, "memory_write")
    before = platform.memory.read("user", "planted")
    r = door.call("memory_write", {"layer": "user", "key": "planted", "text": "a note"})
    assert r.status_code == 403, r.text
    sentence = capability_refused_message("memory_write", MEMORY_PANE)
    assert r.json()["error"]["message"] == sentence
    rows = _rows(real, "memory_write")
    assert len(rows) == 1 and rows[0].ok is False, rows
    assert rows[0].session_id == harness_session_id(MEMORY_PANE)
    assert sentence in rows[0].output
    assert len(_denied_events(real, "memory_write")) == 1
    assert platform.memory.read("user", "planted") == before
    assert ran == [], f"the refused tool ran: {ran}"


def test_a_granted_call_still_runs_and_is_one_row(real):
    """Control: the recording is for refusals only; an allowed call is one row."""
    door = Door(real, _connect_claude_desktop(real))
    r = door.call("work_search", {})
    assert r.status_code == 200, r.text
    assert r.json()["result"]["isError"] is False
    rows = _rows(real, "work_search")
    assert len(rows) == 1 and rows[0].ok is True


# --------------------------------------------------------------------------- #
# 2. a key the door does not know
# --------------------------------------------------------------------------- #
def _initialize_with(app, token: str):
    return TestClient(app).post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        headers={"Accept": ACCEPT, "Authorization": f"Bearer {token}"},
    )


def test_a_made_up_key_is_401_with_the_unknown_token_reason(real):
    made_up = "g29d-" + "x" * 40
    r = _initialize_with(real, made_up)
    assert r.status_code == 401, r.text
    body = r.json()
    assert body["reason"] == REASON_UNKNOWN_TOKEN == "unknown_token"
    assert isinstance(body.get("detail"), str) and body["detail"]
    assert made_up not in r.text


def test_an_apps_key_after_disconnect_is_401_with_the_unknown_token_reason(real):
    token = _connect_claude_desktop(real)
    assert _initialize_with(real, token).status_code == 200
    owner = TestClient(real, headers={"Authorization": f"Bearer {INSTALL}"})
    assert owner.post("/work-record/other-apps/claude-desktop/disconnect").status_code == 200
    r = _initialize_with(real, token)
    assert r.status_code == 401, r.text
    assert r.json()["reason"] == "unknown_token"
    assert token not in r.text
