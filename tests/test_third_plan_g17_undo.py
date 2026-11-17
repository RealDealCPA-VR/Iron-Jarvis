"""Third fix plan, group 17, PG4 (decision K4): the Undo press is the consent.

``POST /undo/{id}`` asked the permission gate about the tool that made the
change with nobody there to ask, so a tool set to ask (on a default install
every rename, move or copy of a download, whose own answer says "Reversible
from the receipt"; and every file edit for a person who set edits to ask)
was refused with 403 and engine words ("permission denied: … allow_tools …").

Now a person's press (the Undo button's route, a task card's Revert, a coding
session's Rewind) hands the gate the consent for THAT tool for THAT one
revert. A tool set to never is still refused, in plain words. An undo a
MODEL asks for (``change_undo``, ``config_undo``: they call ``perform_undo``
with a plain id) gets no such consent: its gate is left as it was.

Through the real app factory, the real registry, the real permission engine
(the daemon's own headless resolver) and the real undo journal.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlmodel import select

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import PermissionMode, ToolInvocation
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import undo as undo_routes
from iron_jarvis.tools.base import ToolContext
from tests.test_build_sessions_v1367 import asgi
from tests.test_harness_rewind_v1370 import _ledger, _session_with_two_turns, messages_of

#: Engine words no refusal a person reads may carry.
ENGINE_WORDS = ("permission denied", "denied by policy", "allow_tools", "headless",
                "resolver", "nothing here could ask", "permissions.", "registered")


@pytest.fixture()
def app(tmp_path):
    return create_app(str(tmp_path / "home"))


@pytest.fixture()
def work(tmp_path):
    folder = tmp_path / "work"
    folder.mkdir()
    (folder / "app.py").write_bytes(b"x = 1\n")
    (folder / "keep.txt").write_bytes(b"untouched\n")
    return folder


def _ctx(platform, ws) -> ToolContext:
    ws.mkdir(parents=True, exist_ok=True)
    return ToolContext(
        workspace=ws, session_id="chat", agent_run_id="chat",
        config=platform.config, event_bus=platform.event_bus, engine=platform.engine,
    )


async def _approved(platform, ctx, name: str, args: dict):
    """The forward change, as a chat turn runs it after the person's Allow."""
    res = await platform.registry.invoke(name, args, ctx, platform.permissions, session_allow=[name])
    assert res.ok, res.error
    return res


def _last_id(platform, tool: str) -> str:
    with session_scope(platform.engine) as db:
        rows = [r for r in db.exec(select(ToolInvocation)).all() if r.tool == tool and not r.undo_of]
        return rows[-1].id


def _undone(platform, action_id: str) -> bool:
    with session_scope(platform.engine) as db:
        return db.get(ToolInvocation, action_id).undone_at is not None


def _plain(sentence: str) -> None:
    low = str(sentence).lower()
    for word in ENGINE_WORDS:
        assert word not in low, f"engine words {word!r} in: {sentence}"
    assert "write_file" not in low and "download_manage" not in low, sentence


def _download(platform, folder, name="report.pdf", body=b"%PDF-1.4 a report"):
    folder.mkdir(parents=True, exist_ok=True)
    f = folder / name
    f.write_bytes(body)
    entry = platform.registry.get("download_manage").ledger.record({
        "download_id": 11, "local_path": str(f), "final_url": "https://docs.example.com/r.pdf",
        "bytes": len(body), "mime": "application/pdf", "timestamp": "2026-11-16T10:00:00+00:00",
    })
    return entry, f


# --------------------------------------------------------------------------- #
# 1. a download renamed / moved at the DEFAULT settings
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("action", ["rename", "move"])
async def test_undo_puts_back_a_download_changed_at_the_default_settings(app, tmp_path, action):
    platform = app.state.platform
    # anti-vacuity: the tool really is "ask" on a default install
    assert platform.permissions.mode_for("download_manage") is PermissionMode.ASK
    entry, f = _download(platform, tmp_path / "Downloads")
    dest = tmp_path / "Filed"
    dest.mkdir()
    to = "October report.pdf" if action == "rename" else str(dest)
    res = await _approved(platform, _ctx(platform, tmp_path / "ws"), "download_manage",
                          {"download": entry["id"], "action": action, "to": to})
    assert "Reversible from the receipt" in res.output
    assert not f.exists()
    aid = _last_id(platform, "download_manage")

    status, out = await asgi(app, "POST", f"/undo/{aid}")
    assert status == 200, out
    assert f.is_file(), "the download is back where it was"
    assert not (tmp_path / "Downloads" / "October report.pdf").exists()
    assert not (dest / "report.pdf").exists()
    assert _undone(platform, aid)


async def test_revert_on_a_task_card_puts_back_an_ask_tier_change_too(app, tmp_path):
    """``POST /sessions/{id}/revert`` (a person's Revert) runs the route's
    own undo per action, so it carries the same consent."""
    platform = app.state.platform
    entry, f = _download(platform, tmp_path / "Downloads")
    await _approved(platform, _ctx(platform, tmp_path / "ws"), "download_manage",
                    {"download": entry["id"], "action": "rename", "to": "renamed.pdf"})
    from iron_jarvis.core.models import Session

    with session_scope(platform.engine) as db:  # the revert route needs a Session row
        db.add(Session(id="chat", workspace_path=str(tmp_path / "ws")))
        db.commit()
    status, out = await asgi(app, "POST", "/sessions/chat/revert")
    assert status == 200, out
    assert out["skipped"] == [] and len(out["reverted"]) == 1, out
    assert f.is_file()


# --------------------------------------------------------------------------- #
# 2. file edits set to ask
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("tool", ["write_file", "edit_file"])
async def test_undo_of_a_file_edit_works_when_file_edits_ask_first(app, tmp_path, tool):
    platform = app.state.platform
    platform.permissions._base[tool] = "ask"
    ws = tmp_path / "ws"
    ctx = _ctx(platform, ws)
    (ws / "notes.md").write_text("before\n", encoding="utf-8")
    args = ({"path": "notes.md", "content": "after\n"} if tool == "write_file"
            else {"path": "notes.md", "old": "before", "new": "after"})
    await _approved(platform, ctx, tool, args)
    assert (ws / "notes.md").read_text(encoding="utf-8") == "after\n"
    aid = _last_id(platform, tool)

    status, out = await asgi(app, "POST", f"/undo/{aid}")
    assert status == 200, out
    assert (ws / "notes.md").read_text(encoding="utf-8") == "before\n"


# --------------------------------------------------------------------------- #
# 3. a tool set to never stays refused, in plain words
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("tool", ["write_file", "download_manage"])
async def test_a_tool_set_to_never_is_still_refused_in_plain_words(app, tmp_path, tool):
    platform = app.state.platform
    ws = tmp_path / "ws"
    ctx = _ctx(platform, ws)
    if tool == "write_file":
        (ws / "notes.md").write_text("before\n", encoding="utf-8")
        await _approved(platform, ctx, tool, {"path": "notes.md", "content": "after\n"})
        changed = ws / "notes.md"
    else:
        entry, _f = _download(platform, tmp_path / "Downloads")
        await _approved(platform, ctx, tool, {"download": entry["id"], "action": "rename", "to": "x.pdf"})
        changed = tmp_path / "Downloads" / "x.pdf"
    before = changed.read_bytes()
    aid = _last_id(platform, tool)
    platform.permissions._base[tool] = "deny"

    status, out = await asgi(app, "POST", f"/undo/{aid}")
    assert status == 403, out
    _plain(out["detail"])
    assert out["detail"].startswith("This change was not undone: ") and "never" in out["detail"], out["detail"]
    assert changed.read_bytes() == before, "nothing was touched"
    assert not _undone(platform, aid)


async def test_a_tool_no_longer_there_is_said_in_plain_words(app, tmp_path):
    platform = app.state.platform
    ws = tmp_path / "ws"
    (ws).mkdir()
    (ws / "notes.md").write_text("before\n", encoding="utf-8")
    await _approved(platform, _ctx(platform, ws), "write_file", {"path": "notes.md", "content": "after\n"})
    aid = _last_id(platform, "write_file")
    platform.registry._tools.pop("write_file")

    status, out = await asgi(app, "POST", f"/undo/{aid}")
    assert status == 422, out
    _plain(out["detail"])
    assert out["detail"] == undo_routes.TOOL_GONE


# --------------------------------------------------------------------------- #
# 4. Rewind of a coding session with file edits set to ask
# --------------------------------------------------------------------------- #


async def test_rewind_works_when_file_edits_ask_first(app, work):
    sid = await _session_with_two_turns(app, work)
    platform = app.state.platform
    changed = {r.tool for r in _ledger(app) if r.reversibility == "reversible"}
    assert changed, "anti-vacuity: the turns changed files"
    for name in changed:
        platform.permissions._base[platform.registry.get(name).perm_key()] = "ask"

    status, preview = await asgi(app, "GET", f"/build/sessions/{sid}/rewind?to=t1")
    assert status == 200 and preview["problems"] == [], preview
    status, out = await asgi(app, "POST", f"/build/sessions/{sid}/rewind", {"to": "t1"})
    assert status == 200, out
    assert out["rewound"] is True
    assert (work / "app.py").read_bytes() == b"x = 1\n"
    assert not (work / "hello.py").exists() and not (work / "notes.md").exists()
    assert messages_of(app, sid) == []


async def test_rewind_still_refuses_a_tool_set_to_never_in_plain_words(app, work):
    sid = await _session_with_two_turns(app, work)
    platform = app.state.platform
    for name in {r.tool for r in _ledger(app) if r.reversibility == "reversible"}:
        platform.permissions._base[platform.registry.get(name).perm_key()] = "deny"

    status, out = await asgi(app, "POST", f"/build/sessions/{sid}/rewind", {"to": "t1"})
    assert status == 409, out
    assert out["rewound"] is False and out["problems"], out
    for problem in out["problems"]:
        _plain(problem["reason"])
        assert "never" in problem["reason"], problem
    _plain(out["detail"])
    assert (work / "app.py").read_bytes() == b"x = 3\n", "nothing was touched"


# --------------------------------------------------------------------------- #
# 5. an undo a model asks for is NOT widened
# --------------------------------------------------------------------------- #


async def test_an_undo_a_model_asks_for_gets_no_consent(app, tmp_path):
    """``change_undo`` / ``config_undo`` call ``perform_undo`` with a plain
    id: an ask-tier tool's revert is refused there exactly as before (only
    the words are plain), and nothing is put back."""
    platform = app.state.platform
    entry, f = _download(platform, tmp_path / "Downloads")
    await _approved(platform, _ctx(platform, tmp_path / "ws"), "download_manage",
                    {"download": entry["id"], "action": "rename", "to": "renamed.pdf"})
    aid = _last_id(platform, "download_manage")
    d = SimpleNamespace(platform=platform)

    with pytest.raises(HTTPException) as caught:
        await undo_routes.perform_undo(d, aid)
    assert caught.value.status_code == 403
    _plain(caught.value.detail)
    assert "asks you first" in caught.value.detail, caught.value.detail
    assert not f.exists() and not _undone(platform, aid)

    # Through the real change_undo tool (the model's way in), on the Chat page.
    tool = platform.registry.get("change_undo")
    res = await tool.execute({"action_id": aid}, _ctx(platform, tmp_path / "ws"))
    assert res.ok is False, res.output
    _plain(res.error)
    assert not f.exists() and not _undone(platform, aid)


def test_a_plain_string_carries_no_consent_and_only_a_press_mints_it():
    pressed = undo_routes.PressedUndo("tool_1")
    assert pressed == "tool_1" and json.dumps([pressed]) == '["tool_1"]'
    assert undo_routes._pressed("tool_1", False) is False
    assert undo_routes._pressed(pressed, False) is True
    assert undo_routes._pressed("tool_1", True) is True
