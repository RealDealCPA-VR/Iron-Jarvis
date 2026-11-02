"""v1.426.0 (Wave 6 lane B, independent review): defects the builder's pins
did not catch, each pinned red on the builder's code first.

1. A folder that is an active PROJECT's root keeps its defaults when it is
   renamed. ``harness/sessions.rename_folder`` renames the project and DELETED
   the folder's ``BuildFolderRecord`` row unless it held a team — the row now
   also holds the folder's defaults (``defaults_json``), which went with it.
2. Codex's ``turn/started`` names THE SESSION'S OWN turn only: a turn of
   another thread (Codex 0.157.0 has sub-agent threads — ``collabAgentToolCall``
   carries ``receiverThreadIds``) must never become the turn Stop and a note
   are sent to. NOT measured that 0.157.0 streams a sub-agent's ``turn/started``
   on this connection; the guard costs nothing when the ids match.
3. Codex's ``skills/list`` NEVER holds a start: the builder's pin set the wait
   to 0.05 s, so a ``_read_skills`` that awaited the answer inline (up to
   ``SKILLS_WAIT_S``, 15 s, on every start) still passed. Here the wait stays
   long and the call must return at once.
"""

from __future__ import annotations

import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.harness import folder_defaults as FD
from iron_jarvis.harness import sessions as S
from iron_jarvis.hosted import codex as X
from iron_jarvis.hosted import tool_setup as T

CODE = {"provider": "claude-cli", "model": "claude-opus-5-5", "approval_mode": "accept_edits"}
TOOL = {"harness": "codex", "model": "gpt-6-astra", "mode": "ask"}


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(str(tmp_path / "home")))


def test_renaming_a_project_roots_folder_keeps_its_defaults(client, tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    pid = client.post("/projects", json={"name": "Acme Site", "root": str(root)}).json()["id"]
    put = lambda view, d: client.put("/build/folders/defaults",  # noqa: E731
                                     json={"folder": str(root), "view": view, "defaults": d})
    assert put("code", CODE).status_code == 200
    assert put("tool", TOOL).status_code == 200
    r = client.post("/build/folders/rename", json={"path": str(root), "name": "Acme Website"})
    assert r.status_code == 200 and r.json()["is_project"] is True
    # The project is renamed (one name, never two) …
    assert client.get(f"/projects/{pid}").json()["project"]["name"] == "Acme Website"
    # … and the folder's defaults for new sessions are still there.
    got = client.get("/build/folders/defaults", params={"folder": str(root)}).json()["defaults"]
    assert got == {"code": CODE, "tool": TOOL}
    # No second display name is kept beside the project's.
    from iron_jarvis.core.db import session_scope
    from iron_jarvis.core.models import BuildFolderRecord

    with session_scope(client.app.state.platform.engine) as db:
        row = db.get(BuildFolderRecord, S.folder_key(str(root)))
        assert row is not None and row.name == ""
    assert S.folder_names(client.app.state.platform.engine, [str(root)])[S.folder_key(str(root))]["name"] == "Acme Website"


def test_renaming_a_project_roots_folder_with_nothing_kept_still_drops_its_row(client, tmp_path):
    """The old rule holds when the row keeps nothing but a name."""
    root = tmp_path / "proj"
    root.mkdir()
    S.rename_folder(client.app.state.platform.engine, str(root), "Before")  # a plain folder's name
    client.post("/projects", json={"name": "Acme", "root": str(root)})
    client.post("/build/folders/rename", json={"path": str(root), "name": "Acme 2"})
    from iron_jarvis.core.db import session_scope
    from iron_jarvis.core.models import BuildFolderRecord

    with session_scope(client.app.state.platform.engine) as db:
        assert db.get(BuildFolderRecord, S.folder_key(str(root))) is None
    assert FD.read(client.app.state.platform.engine, str(root)) == {}


def _driver(wrote: list) -> X.CodexDriver:
    async def write(obj):
        wrote.append(obj)

    return X.CodexDriver(write=write, emit=lambda e: None, ask=lambda a: None,
                         on_session_id=lambda s: None, mode="ask", cwd="C:\\w")


async def test_a_turn_started_of_another_thread_never_becomes_the_turn_stop_names():
    d = _driver([])
    d.session_id = "th-main"
    d.turn_open = True
    d.turn_id = "turn-main"
    d._on_notification("turn/started", {"threadId": "th-child", "turn": {"id": "turn-child"}})
    assert d.turn_id == "turn-main"
    # Its own thread's (and a frame that names no thread) still names the turn.
    d._on_notification("turn/started", {"threadId": "th-main", "turn": {"id": "turn-2"}})
    assert d.turn_id == "turn-2"
    d._on_notification("turn/started", {"turn": {"id": "turn-3"}})
    assert d.turn_id == "turn-3"


async def test_skills_list_is_written_and_never_awaited_by_the_start(monkeypatch):
    wrote: list = []
    d = _driver(wrote)
    monkeypatch.setattr(X, "SKILLS_WAIT_S", 30.0)
    t0 = time.monotonic()
    await asyncio.wait_for(d._read_skills(), 5)
    took = time.monotonic() - t0
    try:
        assert took < 2.0, f"the start waited {took:.1f}s on skills/list"
        assert wrote[-1]["method"] == "skills/list"
        assert d.commands == [T.CODEX_COMPACT_ROW]
        # The answer is taken in the background: one task, still waiting.
        assert len(d._bg) == 1 and not next(iter(d._bg)).done()
        # When it comes, the menu gains the skills.
        rid = wrote[-1]["id"]
        d.on_message({"jsonrpc": "2.0", "id": rid, "result": {"data": [{"skills": [{"name": "notes", "enabled": True}]}]}})
        for _ in range(50):
            if not d._bg:
                break
            await asyncio.sleep(0.01)
        assert [c["name"] for c in d.commands] == ["compact", "notes"]
    finally:
        for task in list(d._bg):
            task.cancel()


def test_codexs_sign_in_words_leave_codex_before_its_shell_command():
    """``codex mcp login`` is a SHELL command, and "Open in terminal" opens
    the conversation in Codex's own screen (``codex resume``): typed there it
    is a message to the model. The words say to leave Codex first (its own
    ``/exit``; measured: codex 0.157.0's command list says "exit Codex"; its
    ``/mcp`` only LISTS servers). Claude Code's ``/mcp`` is typed inside it."""
    words = T.view("codex", [{"type": "setup", "at": "t",
                              "mcp_servers": [{"name": "tracker", "state": T.NEEDS_SIGN_IN}]}])
    said = words["servers"][0]["sign_in"]
    assert "/exit" in said and "codex mcp login tracker" in said
    assert said.index("/exit") < said.index("codex mcp login tracker"), said
    claude = T.view("claude-code", [{"type": "setup", "at": "t",
                                     "mcp_servers": [{"name": "tracker", "state": T.NEEDS_SIGN_IN}]}])
    assert "/mcp" in claude["servers"][0]["sign_in"] and "/exit" not in claude["servers"][0]["sign_in"]
