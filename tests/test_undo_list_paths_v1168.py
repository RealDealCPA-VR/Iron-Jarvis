"""GET /undo rows name their TARGET (v1.168.0) — path + workspace + filter.

The dashboard's "Undo this write" (rail row / turn-receipt file chip) joins
undo-journal rows to the files the conversation shows by ABSOLUTE path:
``workspace`` (the v1.166.3 capture-time stamp) joined with ``path`` (the
envelope's workspace-relative target). Until this wave the listing carried
neither, so no client could ever say WHICH file a row would revert.

Contract pinned here, end-to-end against a real ``build_platform`` + real
registry invoke + real HTTP route:

* every row carries ``path``/``workspace``, and joining them resolves to the
  file the tool really wrote;
* ``?session_id=`` narrows additively (chat's writes all run as ``"chat"``);
* honesty of the nulls: a pre-stamp row reports ``workspace: null``, a
  pathless kind (``setting_restore``, multi-file ``files_delete``) reports
  ``path: null`` — never a guess, never ``paths[0]`` dressed up as the target;
* every pre-v1.168.0 response field is still present (ADDITIVE only).
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
from iron_jarvis.core.models import (
    PermissionMode,
    Session,
    ToolInvocation,
    UndoJournal,
)
from iron_jarvis.daemon.routes import undo as undo_routes
from iron_jarvis.platform import build_platform
from iron_jarvis.tools.base import Reversibility, ToolContext


def _app(platform) -> TestClient:
    app = FastAPI()
    undo_routes.register(app, SimpleNamespace(platform=platform))
    return TestClient(app)


def _ctx(platform, workspace: Path, session_id: str = "chat") -> ToolContext:
    """Chat's exact shape by default: session id "chat", NO Session row."""
    workspace.mkdir(parents=True, exist_ok=True)
    return ToolContext(
        workspace=workspace,
        session_id=session_id,
        agent_run_id=session_id,
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


def test_rows_carry_path_and_workspace_that_join_to_the_real_file(tmp_path):
    platform = build_platform(str(tmp_path))
    uploads = tmp_path / "home-uploads"
    ctx = _ctx(platform, uploads)
    target = uploads / "memo.txt"
    target.write_text("ORIGINAL", encoding="utf-8")

    res = _invoke(platform, ctx, "write_file", {"path": "memo.txt", "content": "NEW"})
    assert res.ok, res.error

    r = _app(platform).get("/undo")
    assert r.status_code == 200, r.text
    actions = r.json()["actions"]
    assert len(actions) == 1
    row = actions[0]
    # The new fields, by VALUE — and their join resolves to the actual file.
    assert row["path"] == "memo.txt"
    assert row["workspace"] == str(uploads.resolve())
    assert Path(row["workspace"], row["path"]) == target.resolve()
    # The row is a live undo candidate for that file.
    assert row["tool"] == "write_file"
    assert row["session_id"] == "chat"
    assert row["kind"] == "file_restore"
    assert row["undoable"] is True
    # ADDITIVE contract: every pre-v1.168.0 field is still present.
    for field in (
        "action_id",
        "session_id",
        "tool",
        "kind",
        "reversible",
        "reversibility",
        "undoable",
        "output",
        "created_at",
    ):
        assert field in row, f"pre-v1.168.0 field {field!r} went missing"


def test_created_file_in_subfolder_joins_to_where_it_really_landed(tmp_path):
    platform = build_platform(str(tmp_path))
    project = tmp_path / "grounded-project"
    ctx = _ctx(platform, project)

    res = _invoke(
        platform, ctx, "write_file", {"path": "out/new-report.md", "content": "x"}
    )
    assert res.ok, res.error
    made = project / "out" / "new-report.md"
    assert made.is_file()

    row = _app(platform).get("/undo").json()["actions"][0]
    assert row["kind"] == "file_delete"  # created → undo removes it
    assert row["path"] == "out/new-report.md"
    assert Path(row["workspace"], row["path"]).resolve() == made.resolve()


def test_session_id_filter_narrows_and_absent_filter_lists_everything(tmp_path):
    platform = build_platform(str(tmp_path))
    # One chat write and one real-session write.
    chat_ws = tmp_path / "chat-ws"
    chat_ctx = _ctx(platform, chat_ws, session_id="chat")
    res = _invoke(platform, chat_ctx, "write_file", {"path": "a.txt", "content": "A"})
    assert res.ok, res.error

    sess_ws = tmp_path / "sess-ws"
    sess_ws.mkdir()
    with session_scope(platform.engine) as db:
        db.add(Session(id="sess_1", task="t", workspace_path=str(sess_ws)))
        db.commit()
    sess_ctx = _ctx(platform, sess_ws, session_id="sess_1")
    res = _invoke(platform, sess_ctx, "write_file", {"path": "b.txt", "content": "B"})
    assert res.ok, res.error

    client = _app(platform)
    everything = client.get("/undo").json()["actions"]
    assert {a["session_id"] for a in everything} == {"chat", "sess_1"}

    chat_only = client.get("/undo", params={"session_id": "chat"}).json()["actions"]
    assert [a["session_id"] for a in chat_only] == ["chat"]
    assert chat_only[0]["path"] == "a.txt"

    sess_only = client.get("/undo", params={"session_id": "sess_1"}).json()["actions"]
    assert [a["path"] for a in sess_only] == ["b.txt"]

    assert client.get("/undo", params={"session_id": "nope"}).json()["actions"] == []


def test_pre_stamp_row_reports_null_workspace_but_keeps_the_path(tmp_path):
    """A row from before v1.166.3 has no workspace stamp — the listing must say
    null (the client then offers NO undo for it) instead of guessing one."""
    platform = build_platform(str(tmp_path))
    ws = tmp_path / "ws"
    ctx = _ctx(platform, ws)
    (ws.mkdir(parents=True, exist_ok=True))
    res = _invoke(platform, ctx, "write_file", {"path": "note.txt", "content": "x"})
    assert res.ok, res.error

    # Strip the stamp the registry just wrote, simulating an old row.
    with session_scope(platform.engine) as db:
        from sqlmodel import select

        j = db.exec(select(UndoJournal)).all()[-1]
        meta = json.loads(j.pre_inline or "{}")
        meta.pop("workspace", None)
        j.pre_inline = json.dumps(meta)
        db.add(j)
        db.commit()

    row = _app(platform).get("/undo").json()["actions"][0]
    assert row["path"] == "note.txt"
    assert row["workspace"] is None


def _insert_manual_row(platform, *, kind: str, pre_inline: str, tool: str) -> str:
    """A journal row the registry cannot produce in one call (settings undo,
    multi-file creation) — inserted the way the app writes it."""
    inv_id = f"tool_manual_{kind}"
    with session_scope(platform.engine) as db:
        db.add(
            ToolInvocation(
                id=inv_id,
                session_id="chat",
                agent_run_id="chat",
                tool=tool,
                args_json="{}",
                verdict=PermissionMode.ALLOW,
                ok=True,
                output="",
                reversibility=Reversibility.REVERSIBLE.value,
            )
        )
        db.add(
            UndoJournal(
                action_id=inv_id,
                session_id="chat",
                agent_run_id="chat",
                tool=tool,
                kind=kind,
                reversible=True,
                pre_inline=pre_inline,
            )
        )
        db.commit()
    return inv_id


def test_pathless_setting_row_reports_null_path_and_null_workspace(tmp_path):
    platform = build_platform(str(tmp_path))
    _insert_manual_row(
        platform,
        kind="setting_restore",
        pre_inline=json.dumps({"prior": {"default_provider": "mock"}}),
        tool="update_settings",
    )
    row = _app(platform).get("/undo").json()["actions"][0]
    assert row["kind"] == "setting_restore"
    assert row["path"] is None
    assert row["workspace"] is None
    assert row["undoable"] is True  # still undoable — just not path-joinable


def test_multi_file_envelope_never_fabricates_a_single_path(tmp_path):
    """files_delete carries {"paths": [...]} — reporting paths[0] as `path`
    would offer "Undo this write" on ONE file while the undo removes THREE."""
    platform = build_platform(str(tmp_path))
    ws = str((tmp_path / "ws").resolve())
    _insert_manual_row(
        platform,
        kind="files_delete",
        pre_inline=json.dumps(
            {"paths": ["a.png", "b.png", "c.png"], "mode": "raw", "workspace": ws}
        ),
        tool="repl",
    )
    row = _app(platform).get("/undo").json()["actions"][0]
    assert row["kind"] == "files_delete"
    assert row["path"] is None
    assert row["workspace"] == ws  # the stamp itself still reports honestly


def test_absolute_envelope_path_reports_null_never_a_misjoinable_value(tmp_path):
    """memory_* kinds journal the LTM store's ABSOLUTE file path (ltm/tools.py,
    memory/proposals.py). The field's contract is workspace-RELATIVE, and a
    client joining workspace + "/" + <absolute> builds a path that names no
    real file — so an absolute target reports ``path: null`` (no affordance is
    offered, per the never-a-guess rule), while the workspace stamp itself
    still reports honestly."""
    platform = build_platform(str(tmp_path))
    ws = str((tmp_path / "ws").resolve())
    note = str((tmp_path / "ltm-store" / "note.md").resolve())
    assert Path(note).is_absolute()  # the premise the honest-null keys off
    _insert_manual_row(
        platform,
        kind="memory_restore",
        pre_inline=json.dumps({"path": note, "mode": "text", "workspace": ws}),
        tool="memory_write",
    )
    row = _app(platform).get("/undo").json()["actions"][0]
    assert row["kind"] == "memory_restore"
    assert row["path"] is None
    assert row["workspace"] == ws
    assert row["undoable"] is True  # still undoable from the Timeline — just
    #                                 not path-joinable in the rail


def test_corrupt_envelope_reports_nulls_instead_of_failing_the_listing(tmp_path):
    platform = build_platform(str(tmp_path))
    _insert_manual_row(
        platform, kind="file_restore", pre_inline="{not json", tool="write_file"
    )
    r = _app(platform).get("/undo")
    assert r.status_code == 200
    row = r.json()["actions"][0]
    assert row["path"] is None
    assert row["workspace"] is None
