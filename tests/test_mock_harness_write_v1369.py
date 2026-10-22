"""v1.369.0 (IJ harness Phase 6): the DEMO model takes its one step in a
Build session too.

A Build session's turn is a harness turn: the model is shown its FAMILY's tool
names (the demo model reads as the Claude family, so ``Write``, not
``write_file``) and the call is translated back at the boundary. The demo
model only knew ``write_file``, so on a fresh install a Build session answered
"nothing was done" — and the screen's end-to-end run (open Build, start a
session, see a reply with a change and its Undo) had nothing to show. It now
writes RESULT.md through whichever of the two names it is offered.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.providers.adapters.base import LLMMessage
from iron_jarvis.providers.adapters.mock import (
    DEMO_NOT_SAVED_LINE,
    DEMO_NOTHING_DONE_LINE,
    DEMO_SAVED_LINE,
    MockLLMAdapter,
)
from tests.test_build_sessions_v1367 import asgi, frames_of, open_turn, until


def _tools(*names: str) -> list[dict]:
    return [{"name": n, "description": n, "input_schema": {"type": "object"}} for n in names]


async def _complete(tools: list[dict], messages: list[LLMMessage]):
    return await MockLLMAdapter().complete(system="", messages=messages, tools=tools)


async def test_the_claude_dialects_write_is_the_demo_step():
    resp = await _complete(_tools("Read", "Write", "Edit"), [LLMMessage(role="user", content="hi")])
    assert [c.name for c in resp.tool_calls] == ["Write"]
    args = resp.tool_calls[0].arguments
    assert args["file_path"] == "RESULT.md" and "Task: hi" in args["content"]
    assert "path" not in args


async def test_jarvis_names_are_unchanged():
    resp = await _complete(_tools("write_file"), [LLMMessage(role="user", content="hi")])
    assert [c.name for c in resp.tool_calls] == ["write_file"]
    assert resp.tool_calls[0].arguments["path"] == "RESULT.md"


@pytest.mark.parametrize("name", ["Write", "write_file"])
async def test_the_closing_line_reads_the_step_under_either_name(name):
    tools = _tools(name)
    done = [
        LLMMessage(role="user", content="hi"),
        LLMMessage(role="tool", content="wrote 12 bytes to RESULT.md", name=name, tool_call_id="call_1"),
    ]
    assert (await _complete(tools, done)).text == DEMO_SAVED_LINE
    refused = [done[0], LLMMessage(role="tool", content="error: refused", name=name, tool_call_id="call_1")]
    assert (await _complete(tools, refused)).text == DEMO_NOT_SAVED_LINE


async def test_no_write_tool_still_does_nothing():
    resp = await _complete(_tools("Read", "Glob"), [LLMMessage(role="user", content="hi")])
    assert not resp.tool_calls and resp.text == DEMO_NOTHING_DONE_LINE


async def test_a_build_session_on_the_demo_model_writes_and_can_be_undone(tmp_path: Path):
    """End to end through the REAL app factory and the REAL demo model (no
    scripted router): the session's folder gets RESULT.md, the reply lists
    it, and the undo journal holds it under the session's own id."""
    app = create_app(str(tmp_path / "home"))
    work = tmp_path / "proj"
    work.mkdir()
    status, made = await asgi(app, "POST", "/build/sessions", {"folder": str(work)})
    assert status == 201, made
    sid = made["id"]
    status, raw = await open_turn(app, sid, {"text": "Write a hello file", "turn_id": "t-demo-1"})
    assert status == 200
    frames = frames_of(raw)
    done = [d for e, d in frames if e == "done"]
    assert done and done[0]["tools_used"] == ["write_file"]
    assert (work / "RESULT.md").is_file()
    assert any(Path(p).name == "RESULT.md" for p in done[0]["documents"])

    async def journal():
        st, body = await asgi(app, "GET", f"/undo?session_id=build:{sid}")
        rows = body.get("actions", []) if st == 200 and isinstance(body, dict) else []
        return rows if any(r.get("path") == "RESULT.md" for r in rows) else None

    rows = None
    for _ in range(50):
        rows = await journal()
        if rows:
            break
        await until(lambda: False, 0.05)
    assert rows and rows[0]["undoable"] is True
