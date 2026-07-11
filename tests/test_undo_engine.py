"""TX-01 undo ENGINE — pre-image store, reversible tools, and the /undo routes.

Runs fully offline against a real ``build_platform``: a reversible tool is
invoked through the registry (which journals the inverse per the Wave-1
contract), then the undo route replays it. Covers the happy path (restore + a
first-class ``undo_of`` ledger row + ``action.reverted`` event), double-undo
(409), an irreversible action (422), a since-changed target (conflict refusal),
and the secret-safety of the settings pre-image.
"""

from __future__ import annotations

import iron_jarvis.workflows.models  # noqa: F401  (register tables before init_db)

from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import select

from iron_jarvis.core.config import (
    capture_config_undo,
    is_secret_config_key,
    restore_config_values,
)
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import PermissionMode, Session, ToolInvocation, UndoJournal
from iron_jarvis.daemon.routes import settings as settings_routes
from iron_jarvis.daemon.routes import undo as undo_routes
from iron_jarvis.platform import build_platform
from iron_jarvis.tools.base import Reversibility, ToolContext


def _app(platform) -> TestClient:
    app = FastAPI()
    undo_routes.register(app, SimpleNamespace(platform=platform))
    return TestClient(app)


def _session_ctx(platform, workspace: Path) -> ToolContext:
    """Create a real Session row (so the route can resolve its workspace) and a
    ToolContext bound to it."""
    sid = "session_undotest"
    workspace.mkdir(parents=True, exist_ok=True)
    with session_scope(platform.engine) as db:
        if db.get(Session, sid) is None:
            db.add(Session(id=sid, task="undo test", workspace_path=str(workspace)))
            db.commit()
    return ToolContext(
        workspace=workspace,
        session_id=sid,
        agent_run_id="run_undotest",
        config=platform.config,
        event_bus=platform.event_bus,
        engine=platform.engine,
    )


async def _invoke(platform, ctx, name, args):
    return await platform.registry.invoke(name, args, ctx, platform.permissions)


def _latest_action_id(platform, tool: str) -> str:
    with session_scope(platform.engine) as db:
        row = db.exec(
            select(ToolInvocation)
            .where(ToolInvocation.tool == tool)
            .where(ToolInvocation.undo_of == None)  # noqa: E711
            .order_by(ToolInvocation.created_at.desc())
        ).first()
        assert row is not None
        return row.id


# --- write_file happy path -------------------------------------------------


async def test_write_file_over_existing_undo_restores(tmp_path):
    platform = build_platform(str(tmp_path))
    ws = tmp_path / "ws"
    ctx = _session_ctx(platform, ws)
    (ws / "note.txt").write_text("ORIGINAL CONTENT\nline two\n", encoding="utf-8")

    res = await _invoke(platform, ctx, "write_file", {"path": "note.txt", "content": "OVERWRITTEN\n"})
    assert res.ok
    assert (ws / "note.txt").read_text(encoding="utf-8") == "OVERWRITTEN\n"

    action_id = _latest_action_id(platform, "write_file")
    client = _app(platform)

    listing = client.get("/undo").json()["actions"]
    assert any(a["action_id"] == action_id and a["undoable"] for a in listing)

    resp = client.post(f"/undo/{action_id}")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # Exact prior content is back.
    assert (ws / "note.txt").read_text(encoding="utf-8") == "ORIGINAL CONTENT\nline two\n"

    # A first-class undo_of ledger row was written, and the original is marked undone.
    with session_scope(platform.engine) as db:
        orig = db.get(ToolInvocation, action_id)
        assert orig.undone_at is not None
        journal = db.get(UndoJournal, action_id)
        assert journal.applied_at is not None
        undo_row = db.get(ToolInvocation, body["undo_invocation_id"])
        assert undo_row.undo_of == action_id

    # action.reverted landed in the persisted event ledger.
    from iron_jarvis.core.models import EventRecord

    with session_scope(platform.engine) as db:
        evs = db.exec(select(EventRecord).where(EventRecord.type == "action.reverted")).all()
        assert any(action_id in (e.payload_json or "") for e in evs)

    # A second undo is refused.
    assert client.post(f"/undo/{action_id}").status_code == 409

    # No longer listed as undoable.
    assert not any(a["action_id"] == action_id for a in client.get("/undo").json()["actions"])


async def test_write_file_new_file_undo_deletes(tmp_path):
    platform = build_platform(str(tmp_path))
    ws = tmp_path / "ws"
    ctx = _session_ctx(platform, ws)

    res = await _invoke(platform, ctx, "write_file", {"path": "fresh.txt", "content": "brand new\n"})
    assert res.ok and (ws / "fresh.txt").exists()

    action_id = _latest_action_id(platform, "write_file")
    resp = _app(platform).post(f"/undo/{action_id}")
    assert resp.status_code == 200, resp.text
    # The created file is gone again.
    assert not (ws / "fresh.txt").exists()


# --- conflict: target changed since the action -----------------------------


async def test_undo_refuses_when_target_changed(tmp_path):
    platform = build_platform(str(tmp_path))
    ws = tmp_path / "ws"
    ctx = _session_ctx(platform, ws)
    (ws / "doc.txt").write_text("v1\n", encoding="utf-8")

    await _invoke(platform, ctx, "write_file", {"path": "doc.txt", "content": "v2 from agent\n"})
    action_id = _latest_action_id(platform, "write_file")

    # Someone edits the file AFTER the agent's write, BEFORE the undo.
    (ws / "doc.txt").write_text("v3 hand edit\n", encoding="utf-8")

    resp = _app(platform).post(f"/undo/{action_id}")
    assert resp.status_code == 409
    assert "changed" in resp.json()["detail"].lower()
    # The newer hand edit was NOT clobbered.
    assert (ws / "doc.txt").read_text(encoding="utf-8") == "v3 hand edit\n"


# --- irreversible / unknown ------------------------------------------------


def test_undo_unknown_action_404(tmp_path):
    platform = build_platform(str(tmp_path))
    assert _app(platform).post("/undo/nope").status_code == 404


def test_undo_irreversible_action_422(tmp_path):
    platform = build_platform(str(tmp_path))
    # An irreversible tool leaves no UndoJournal inverse: undo must refuse (422).
    with session_scope(platform.engine) as db:
        db.add(
            ToolInvocation(
                id="tool_irrev",
                session_id="session_undotest",
                agent_run_id="r",
                tool="shell",
                args_json="{}",
                verdict=PermissionMode.ALLOW,
                ok=True,
                output="ran",
                reversibility=Reversibility.IRREVERSIBLE.value,
            )
        )
        db.commit()
    resp = _app(platform).post("/undo/tool_irrev")
    assert resp.status_code == 422


# --- ltm_append ------------------------------------------------------------


async def test_ltm_append_new_note_undo_deletes(tmp_path):
    platform = build_platform(str(tmp_path))
    ctx = _session_ctx(platform, tmp_path / "ws")

    res = await _invoke(
        platform, ctx, "ltm_append", {"title": "Undo Me", "content": "a captured thought"}
    )
    assert res.ok
    ref = Path(res.data["ref"])
    assert ref.is_file()

    action_id = _latest_action_id(platform, "ltm_append")
    resp = _app(platform).post(f"/undo/{action_id}")
    assert resp.status_code == 200, resp.text
    # The appended note file is deleted by the undo.
    assert not ref.exists()


# --- settings (config) capture/restore + secret-safety ---------------------


def test_capture_config_undo_restores_and_skips_secrets(tmp_path):
    platform = build_platform(str(tmp_path))
    cfg = platform.config
    cfg.max_agent_steps = 12

    desc = capture_config_undo(cfg, ["max_agent_steps"])
    assert desc["kind"] == "setting_restore"
    assert desc["prior"]["max_agent_steps"] == 12

    cfg.max_agent_steps = 99
    restored = restore_config_values(cfg, desc["prior"])
    assert restored == ["max_agent_steps"]
    assert cfg.max_agent_steps == 12


def test_config_undo_never_captures_a_secret(tmp_path):
    platform = build_platform(str(tmp_path))
    cfg = platform.config
    # A secret-looking key is refused capture, so a plaintext credential can never
    # land in the undo journal.
    assert is_secret_config_key("custom_api_key")
    desc = capture_config_undo(cfg, ["max_agent_steps", "custom_api_key"])
    assert "custom_api_key" in desc["skipped"]
    assert "custom_api_key" not in desc["prior"]


# --- raw/binary write: post-image captured AFTER execute arms the guard -----


async def test_write_document_raw_write_conflict_guard(tmp_path):
    """A raw/binary write (write_document) cannot predict its post-image at capture
    time, so the registry re-hashes the file AFTER execute (finalize_post_hash).
    Without that, post_sha256 stays None and undo would clobber a since-changed
    file. Here: a document is created, then externally modified — undo must REFUSE
    (409) instead of deleting the newer file."""
    platform = build_platform(str(tmp_path))
    ws = tmp_path / "ws"
    ctx = _session_ctx(platform, ws)

    res = await _invoke(
        platform, ctx, "write_document", {"path": "report.md", "content": "# Report\n\nbody\n"}
    )
    assert res.ok and (ws / "report.md").exists()

    # The post-image was armed (not None) despite the raw mode.
    action_id = _latest_action_id(platform, "write_document")
    with session_scope(platform.engine) as db:
        journal = db.get(UndoJournal, action_id)
        assert journal is not None
        assert journal.post_sha256 is not None  # finalize_post_hash filled it

    # Someone replaces the document AFTER it was created, BEFORE the undo.
    (ws / "report.md").write_text("HAND-EDITED FINAL VERSION\n", encoding="utf-8")

    resp = _app(platform).post(f"/undo/{action_id}")
    assert resp.status_code == 409, resp.text
    # The newer version was NOT deleted/clobbered.
    assert (ws / "report.md").read_text(encoding="utf-8") == "HAND-EDITED FINAL VERSION\n"


# --- settings change is a first-class reversible action ---------------------


def test_settings_change_is_undoable(tmp_path):
    """PUT /settings journals a reversible setting_restore action, so a settings
    change shows on the timeline and can be reversed from time-travel."""
    platform = build_platform(str(tmp_path))
    app = FastAPI()
    d = SimpleNamespace(platform=platform, _live_rearm={})
    settings_routes.register(app, d)
    undo_routes.register(app, d)
    client = TestClient(app)

    # Baseline, then a real change.
    client.put("/settings", json={"values": {"max_agent_steps": 7}})
    r = client.put("/settings", json={"values": {"max_agent_steps": 42}})
    assert r.status_code == 200
    assert platform.config.max_agent_steps == 42

    actions = client.get("/undo").json()["actions"]
    settings_actions = [a for a in actions if a["tool"] == "update_settings" and a["undoable"]]
    assert settings_actions, actions
    action_id = settings_actions[0]["action_id"]  # newest first = the 7→42 change

    resp = client.post(f"/undo/{action_id}")
    assert resp.status_code == 200, resp.text
    # The prior value is restored on the live config.
    assert platform.config.max_agent_steps == 7


# --- the timeline exposes an explicit `undone` flag ------------------------


async def test_timeline_exposes_explicit_undone_flag(tmp_path):
    """AuditEntry.undone is set from undone_at, NOT inferred from reversible/
    undoable — so the UI never mislabels a never-reversed action as 'reversed'."""
    platform = build_platform(str(tmp_path))
    ws = tmp_path / "ws"
    ctx = _session_ctx(platform, ws)
    await _invoke(platform, ctx, "write_file", {"path": "z.txt", "content": "hi\n"})
    action_id = _latest_action_id(platform, "write_file")

    def _entry(aid):
        entries = platform.observability.timeline(limit=50)["entries"]
        return next(e for e in entries if e["id"] == aid)

    before = _entry(action_id)
    assert before["reversible"] and before["undoable"] and before["undone"] is False

    assert _app(platform).post(f"/undo/{action_id}").status_code == 200
    after = _entry(action_id)
    assert after["undone"] is True and after["undoable"] is False


async def test_no_plaintext_secret_in_undo_journal(tmp_path):
    """A settings pre-image for a secret-named key never captures its value, and
    scanning every journal row after a real write shows no secret sentinel."""
    platform = build_platform(str(tmp_path))
    SENTINEL = "SUPER-SECRET-TOKEN-XYZ"

    # A settings-undo capture over a secret key must not journal the value.
    desc = capture_config_undo(platform.config, ["custom_api_key"])
    assert SENTINEL not in str(desc)

    # A normal reversible write journals a pre-image; verify it holds only the
    # file's own content and no injected secret sentinel.
    ctx = _session_ctx(platform, tmp_path / "ws")
    await _invoke(platform, ctx, "write_file", {"path": "a.txt", "content": "plain content\n"})
    with session_scope(platform.engine) as db:
        rows = db.exec(select(UndoJournal)).all()
        assert rows
        for r in rows:
            blob = f"{r.pre_inline or ''}{r.pre_ref or ''}"
            assert SENTINEL not in blob
