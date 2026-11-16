"""v1.504.0 (north-star 4.7): the approval card tells the truth about Undo.

The card's undo line came from the tool's CLASS (``Tool.reversibility``), so
a note saved to a cloud memory base (Notion, OneDrive: no local file, journaled
``{"kind": "memory_external", "reversible": False}``) was asked about with
"You can undo this." beside an Undo the journal could never perform. Now both
lanes read what THIS call can do (``Tool.can_undo(args, ctx)`` through
``tools.reversible.call_reversible_flag``): such a call reads ``reversible:
false``, never the promise. And a call whose before-copy could not be taken
(its capture raised) says in its answer that it cannot be undone.

Through the real runtime pause, the real stream lane and the real registry.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.ltm.tools import LTMAppendTool
from iron_jarvis.tools.base import Reversibility, ToolContext, ToolResult
from iron_jarvis.tools.builtins import WriteFileTool
from iron_jarvis.tools.permissions import PermissionEngine
from iron_jarvis.tools.reversible import call_reversible_flag
from tests.test_approval_modes_v1188 import _body, _calls_stream
from tests.test_approval_words_v13520 import _ask_and_list, _row, rt  # noqa: F401 — the fixture
from tests.test_chat_approvals_v1187 import _asgi_post, _drive_stream

CLOUD = "cloud_notes_v1504"


class _CloudBase:
    """A memory base with no local file (Notion / OneDrive shape: no ``dir``)."""

    name = CLOUD

    def search(self, query, k=5):
        return []

    def append(self, title, content):
        return "page-1"


def _ctx(platform, ws: Path) -> ToolContext:
    ws.mkdir(parents=True, exist_ok=True)
    return ToolContext(
        workspace=ws, session_id="s-1504-card", agent_run_id="r-1504",
        config=platform.config, event_bus=platform.event_bus, engine=platform.engine,
    )


@pytest.mark.asyncio
async def test_a_note_to_a_cloud_base_cannot_be_undone_and_a_folder_note_can(tmp_path):
    platform = create_app(str(tmp_path)).state.platform
    platform.ltm.register(_CloudBase())
    tool = platform.registry.get("ltm_append")
    assert isinstance(tool, LTMAppendTool) and tool.reversibility == Reversibility.REVERSIBLE
    ctx = _ctx(platform, tmp_path / "ws")
    cloud = {"title": "Lunch", "content": "with Sam", "source": CLOUD}
    assert await call_reversible_flag(tool, cloud, ctx) is False
    # The same call's journal entry says so too (the two must agree).
    desc = await tool.capture_undo(cloud, ctx)
    assert desc["reversible"] is False
    # A folder base (the built-in brain) keeps its promise.
    local = {"title": "Lunch", "content": "with Sam", "source": "brain"}
    assert getattr(platform.ltm.get("brain"), "dir", None) is not None
    assert await call_reversible_flag(tool, local, ctx) is True


@pytest.mark.asyncio
async def test_brain_save_to_a_cloud_base_cannot_be_undone(tmp_path):
    platform = create_app(str(tmp_path)).state.platform
    platform.ltm.register(_CloudBase())
    tool = platform.registry.get("brain_save")
    ctx = _ctx(platform, tmp_path / "ws")
    tool._active = lambda _ctx: SimpleNamespace(base=CLOUD, profile={}, name="cloud")
    args = {"title": "Lunch", "content": "with Sam"}
    assert await call_reversible_flag(tool, args, ctx) is False
    assert (await tool.capture_undo(args, ctx))["reversible"] is False


@pytest.mark.asyncio
async def test_a_tool_without_an_opinion_keeps_its_class_verdict_and_a_fault_promises_nothing(tmp_path):
    platform = create_app(str(tmp_path)).state.platform
    ctx = _ctx(platform, tmp_path / "ws")
    assert await call_reversible_flag(platform.registry.get("write_file"), {"path": "a", "content": ""}, ctx) is True
    assert await call_reversible_flag(platform.registry.get("shell"), {"command": "dir"}, ctx) is False
    assert await call_reversible_flag(None, {}, ctx) is None

    class _Broken(WriteFileTool):
        name = "broken_can_undo_v1504"

        async def can_undo(self, args, ctx):
            raise RuntimeError("cannot tell")

    # Wording only: a fault answers "no line", never "You can undo this".
    assert await call_reversible_flag(_Broken(), {}, ctx) is None


# --------------------------------------------------------------------------- #
# the two lanes
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_the_runtime_ask_for_a_cloud_note_does_not_promise_undo(rt):  # noqa: F811
    rt.platform.ltm.register(_CloudBase())
    rt.platform.permissions._base["ltm_append"] = "ask"
    _row(rt.platform, "session_cloud", "schedule:notes", "Keep the lunch note")
    payload, rows = await _ask_and_list(
        rt, "session_cloud", "schedule:notes", "ltm_append",
        {"title": "Lunch", "content": "with Sam", "source": CLOUD},
    )
    assert payload["reversible"] is False
    assert rows[0]["reversible"] is False


@pytest.mark.asyncio
async def test_the_runtime_ask_for_a_folder_note_still_says_it_can_be_undone(rt):  # noqa: F811
    rt.platform.permissions._base["ltm_append"] = "ask"
    _row(rt.platform, "session_brain", "schedule:notes", "Keep the lunch note")
    payload, _rows = await _ask_and_list(
        rt, "session_brain", "schedule:notes", "ltm_append",
        {"title": "Lunch", "content": "with Sam", "source": "brain"},
    )
    assert payload["reversible"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("path, promised", [("kept.md", True), ("not-kept.md", False)])
async def test_the_chat_card_reads_this_calls_answer(tmp_path, path, promised):
    """The stream lane's card asks the tool about THIS call's arguments. (A
    ticked memory tool runs unasked in chat, so the card is driven through
    ``write_file``, which always cards under "Ask before every action", with
    its ``can_undo`` answering by path.)"""
    app = create_app(str(tmp_path))
    tool = app.state.platform.registry.get("write_file")
    asked: list[dict] = []

    async def can_undo(args, ctx):
        asked.append(dict(args))
        return None if args.get("path") == "kept.md" else False

    tool.can_undo = can_undo
    app.state.platform.router.stream = _calls_stream([("write_file", {"path": path, "content": "# hi"})])
    seen: list[dict] = []

    async def deny(data):
        seen.append(data)
        status, _ = await _asgi_post(app, f"/chat/approvals/{data['id']}", {"decision": "deny"})
        assert status == 200

    await _drive_stream(app, _body("write a note file for me", "always_ask", tools=["write_file"]), deny)
    assert [d["tool"] for d in seen] == ["write_file"], "anti-vacuity: the card must come"
    assert seen[0]["reversible"] is promised
    assert asked and asked[0]["path"] == path


# --------------------------------------------------------------------------- #
# a capture that raised
# --------------------------------------------------------------------------- #


class _CaptureFails(WriteFileTool):
    name = "capture_fails_v1504"

    async def capture_undo(self, args, ctx):
        raise OSError("the before-copy store is full")


@pytest.mark.asyncio
async def test_a_change_whose_before_copy_failed_says_it_cannot_be_undone(tmp_path):
    platform = create_app(str(tmp_path)).state.platform
    tool = _CaptureFails()
    platform.registry.register(tool)
    perms = PermissionEngine({**platform.config.permissions, tool.name: "allow"})
    ctx = _ctx(platform, tmp_path / "ws")
    (ctx.workspace / "a.txt").write_text("before", encoding="utf-8")
    res = await platform.registry.invoke(tool.name, {"path": "a.txt", "content": "after"}, ctx, perms)
    assert res.ok
    # VOCABULARY.md: "can't be undone" is the one name.
    assert "can't be undone" in res.output
    # A tool whose capture worked says nothing of the kind.
    perms2 = PermissionEngine({**platform.config.permissions, "write_file": "allow"})
    ok = await platform.registry.invoke("write_file", {"path": "b.txt", "content": "x"}, ctx, perms2)
    assert ok.ok and "can't be undone" not in ok.output
