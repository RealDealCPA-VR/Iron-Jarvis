"""Chat undo resolves against the CAPTURE-TIME workspace (v1.166.3).

The journal envelope records a workspace-RELATIVE path, and until this fix it
never recorded which workspace. POST /undo reconstructed the workspace from
the Session table — but chat runs every turn as session id ``"chat"``, which
has NO row, so the route guessed ``workspaces_dir/chat`` and every chat
file-write undo either 409'd ("target changed" — the guessed path has no
file) or targeted the wrong tree. Chat's tool workspace also varies per turn
(``home/uploads`` vs. the grounded project folder), so no table lookup can
ever be right: only capture-time truth works.

The fix is two-sided and both sides are pinned here end-to-end (real
``build_platform``, real registry invoke, real HTTP route):
  * ``registry._record`` stamps ``workspace`` into every journal envelope;
  * ``POST /undo`` prefers that stamp, falling back to the historical
    Session-table reconstruction for rows from before the stamp existed.
"""

from __future__ import annotations

import iron_jarvis.workflows.models  # noqa: F401  (register tables before init_db)

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import Session, UndoJournal
from iron_jarvis.daemon.routes import undo as undo_routes
from iron_jarvis.platform import build_platform
from iron_jarvis.tools.base import ToolContext


def _app(platform) -> TestClient:
    app = FastAPI()
    undo_routes.register(app, SimpleNamespace(platform=platform))
    return TestClient(app)


def _chat_ctx(platform, workspace: Path) -> ToolContext:
    """Chat's exact shape: session id "chat", NO Session row, a per-turn
    workspace that is nowhere near ``workspaces_dir/chat``."""
    workspace.mkdir(parents=True, exist_ok=True)
    return ToolContext(
        workspace=workspace,
        session_id="chat",
        agent_run_id="chat",
        config=platform.config,
        event_bus=platform.event_bus,
        engine=platform.engine,
    )


def _invoke(platform, ctx, name, args):
    return asyncio.run(
        platform.registry.invoke(
            name, args, ctx, platform.permissions, session_allow=[name]
        )
    )


def _last_action_id(platform) -> str:
    from sqlmodel import select

    from iron_jarvis.core.models import ToolInvocation

    with session_scope(platform.engine) as db:
        rows = list(db.exec(select(ToolInvocation)))
        return rows[-1].id


def test_chat_overwrite_undo_restores_in_the_real_workspace(tmp_path):
    platform = build_platform(str(tmp_path))
    uploads = tmp_path / "home-uploads"  # chat's per-turn tool workspace
    ctx = _chat_ctx(platform, uploads)
    target = uploads / "memo.txt"
    target.write_text("ORIGINAL CLIENT MEMO", encoding="utf-8")

    res = _invoke(platform, ctx, "write_file", {"path": "memo.txt", "content": "NEW"})
    assert res.ok, res.error
    assert target.read_text(encoding="utf-8") == "NEW"

    action_id = _last_action_id(platform)
    # The envelope must carry the capture-time workspace, absolutely.
    with session_scope(platform.engine) as db:
        j = db.get(UndoJournal, action_id)
        meta = json.loads(j.pre_inline or "{}")
        assert meta.get("workspace") == str(uploads.resolve())

    # No Session row for "chat" exists — the old code guessed
    # workspaces_dir/chat here and 409'd. Now the stamp wins.
    r = _app(platform).post(f"/undo/{action_id}")
    assert r.status_code == 200, r.text
    assert target.read_text(encoding="utf-8") == "ORIGINAL CLIENT MEMO"


def test_chat_created_file_undo_removes_from_the_real_workspace(tmp_path):
    platform = build_platform(str(tmp_path))
    project = tmp_path / "grounded-project"  # the OTHER chat workspace shape
    ctx = _chat_ctx(platform, project)

    res = _invoke(
        platform, ctx, "write_file", {"path": "out/new-report.md", "content": "x"}
    )
    assert res.ok, res.error
    made = project / "out" / "new-report.md"
    assert made.is_file()

    action_id = _last_action_id(platform)
    r = _app(platform).post(f"/undo/{action_id}")
    assert r.status_code == 200, r.text
    assert not made.exists(), "undo must remove the file where it really is"
    # And nothing was conjured under the guessed fallback tree.
    assert not (platform.config.workspaces_dir / "chat").exists()


def test_pre_stamp_rows_still_revert_via_the_session_table(tmp_path):
    """Back-compat: a journal row WITHOUT the workspace stamp (written before
    v1.166.3) must keep reverting through the Session-table reconstruction."""
    platform = build_platform(str(tmp_path))
    ws = tmp_path / "session-ws"
    ws.mkdir()
    sid = "session_oldrow"
    with session_scope(platform.engine) as db:
        db.add(Session(id=sid, task="old row", workspace_path=str(ws)))
        db.commit()
    ctx = ToolContext(
        workspace=ws,
        session_id=sid,
        agent_run_id="r1",
        config=platform.config,
        event_bus=platform.event_bus,
        engine=platform.engine,
    )
    target = ws / "note.txt"
    target.write_text("BEFORE", encoding="utf-8")
    res = _invoke(platform, ctx, "write_file", {"path": "note.txt", "content": "AFTER"})
    assert res.ok, res.error

    action_id = _last_action_id(platform)
    # Simulate a pre-v1.166.3 row: strip the stamp the registry just wrote.
    with session_scope(platform.engine) as db:
        j = db.get(UndoJournal, action_id)
        meta = json.loads(j.pre_inline or "{}")
        meta.pop("workspace", None)
        j.pre_inline = json.dumps(meta)
        db.add(j)
        db.commit()

    r = _app(platform).post(f"/undo/{action_id}")
    assert r.status_code == 200, r.text
    assert target.read_text(encoding="utf-8") == "BEFORE"
