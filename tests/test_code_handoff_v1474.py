"""v1.474.0 — Hand off: a Build › Code session's coding tool starts another
coding tool on a piece of its work (``hosted/handoff``).

Driven through the real service, store, MCP grant and the recorded stand-in
coding tool the Code-view tests use (``tests/fixtures/fake_harness_v1409.py``);
never a real Claude Code or Codex.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.hosted import handoff as HO
from iron_jarvis.hosted import jarvis_tools as JT
from iron_jarvis.hosted import service as S
from iron_jarvis.hosted import store
from iron_jarvis.tools.base import ToolContext
from tests._build_app_helpers_v1409 import client, new_session, sent, stop_all, until, use_fake

DONE_SCENARIO = "claude_full_mode_edit_v1418"


def _ctx(app, parent: str, work: Path) -> ToolContext:
    p = app.state.platform
    return ToolContext(workspace=work, session_id=f"mcp:code:{parent}", agent_run_id="",
                       config=p.config, event_bus=p.event_bus, engine=p.engine)


async def _setup(tmp_path: Path, monkeypatch, *, handoff: bool = True, mode: str = "ask"):
    work = tmp_path / "work"
    work.mkdir()
    use_fake(monkeypatch, DONE_SCENARIO, tmp_path / "fake.log")
    app = create_app(str(tmp_path / "home"))
    svc = S.service_for(app.state.platform)
    async with client(app) as c:
        body = {"mode": mode, **({"confirm": True} if mode == "full" else {})}
        parent = (await new_session(c, work, **body))["id"]
        if handoff:
            r = await c.patch(f"/build/app/sessions/{parent}", json={"capabilities": {"handoff": True}})
            assert r.status_code == 200, r.text
    return app, svc, parent, work


def _tool(app, name: str):
    return app.state.platform.registry.get(name)


@pytest.mark.asyncio
async def test_a_helper_is_a_real_session_in_the_same_folder_and_its_result_comes_back(tmp_path, monkeypatch):
    app, svc, parent, work = await _setup(tmp_path, monkeypatch)
    try:
        ctx = _ctx(app, parent, work)
        r = await _tool(app, HO.TOOL_START).execute({"tool": "claude-code", "task": "Fix cart.py"}, ctx)
        assert r.ok, r.error
        helper = r.data["helper"]
        row = store.get(app.state.platform.engine, helper)
        assert row.handed_from == parent
        assert Path(row.folder) == work
        assert row.title.startswith("Helper: ")
        # The helper got the task with the words that say where it stands.
        await until(lambda: any(m.get("type") == "user" for m in sent(tmp_path / "fake.log")))
        first = next(m for m in sent(tmp_path / "fake.log") if m.get("type") == "user")
        text = json.dumps(first)
        assert "Fix cart.py" in text and "handed you one piece" in text
        # Check waits for the end and brings back what it wrote, fenced.
        got = None
        for _ in range(8):
            got = await _tool(app, HO.TOOL_CHECK).execute({"helper": helper, "wait_s": 5}, ctx)
            assert got.ok, got.error
            if got.data.get("status"):
                break
        assert got.data["status"] == "completed"
        assert got.data["reply"]
        assert "UNTRUSTED" in got.output.upper() or "untrusted" in got.output
        # The starting session's view lists it; the helper's view links back.
        async with client(app) as c:
            pv = (await c.get(f"/build/app/sessions/{parent}")).json()
            assert [h["id"] for h in pv["helpers"]] == [helper]
            hv = (await c.get(f"/build/app/sessions/{helper}")).json()
            assert hv["handed_from"]["id"] == parent
            assert hv["session"]["handed_from"] == parent
    finally:
        await stop_all(app)


@pytest.mark.asyncio
async def test_nothing_is_handed_off_while_the_box_is_off(tmp_path, monkeypatch):
    app, svc, parent, work = await _setup(tmp_path, monkeypatch, handoff=False)
    try:
        r = await _tool(app, HO.TOOL_START).execute({"task": "x"}, _ctx(app, parent, work))
        assert not r.ok and r.error == HO.OFF
        assert store.helpers_of(app.state.platform.engine, parent) == []
    finally:
        await stop_all(app)


@pytest.mark.asyncio
async def test_a_helper_can_never_hand_off_again(tmp_path, monkeypatch):
    app, svc, parent, work = await _setup(tmp_path, monkeypatch)
    try:
        r = await _tool(app, HO.TOOL_START).execute({"task": "x"}, _ctx(app, parent, work))
        helper = r.data["helper"]
        # The box cannot be turned on in a helper...
        async with client(app) as c:
            bad = await c.patch(f"/build/app/sessions/{helper}", json={"capabilities": {"handoff": True}})
            assert bad.status_code == 400 and HO.HELPER_CANNOT_BOX in bad.text
        # ...and the tool refuses from a helper even if the row said otherwise.
        store.update(app.state.platform.engine, helper,
                     capabilities_json=json.dumps({"handoff": True}))
        again = await _tool(app, HO.TOOL_START).execute({"task": "y"}, _ctx(app, helper, work))
        assert not again.ok and again.error == HO.HELPER_CANNOT
    finally:
        await stop_all(app)


@pytest.mark.asyncio
async def test_a_helper_never_gets_full_access(tmp_path, monkeypatch):
    app, svc, parent, work = await _setup(tmp_path, monkeypatch, mode="full")
    try:
        r = await _tool(app, HO.TOOL_START).execute({"task": "x"}, _ctx(app, parent, work))
        assert r.ok, r.error
        assert r.data["mode"] == S.default_mode("claude-code") != "full"
    finally:
        await stop_all(app)


@pytest.mark.asyncio
async def test_the_mode_carries_over_when_it_is_not_dangerous(tmp_path, monkeypatch):
    app, svc, parent, work = await _setup(tmp_path, monkeypatch, mode="edits")
    try:
        r = await _tool(app, HO.TOOL_START).execute({"task": "x"}, _ctx(app, parent, work))
        assert r.data["mode"] == "edits"
    finally:
        await stop_all(app)


@pytest.mark.asyncio
async def test_unknown_and_missing_tools_are_refused_in_words(tmp_path, monkeypatch):
    app, svc, parent, work = await _setup(tmp_path, monkeypatch)
    try:
        ctx = _ctx(app, parent, work)
        r = await _tool(app, HO.TOOL_START).execute({"tool": "notepad", "task": "x"}, ctx)
        assert not r.ok and "not a coding tool" in r.error and "Claude Code" in r.error
        before = S.binary_for
        monkeypatch.setattr(S, "binary_for", lambda h: None if h == "codex" else before(h))
        r2 = await _tool(app, HO.TOOL_START).execute({"tool": "codex", "task": "x"}, ctx)
        assert not r2.ok and "not installed" in r2.error
        assert store.helpers_of(app.state.platform.engine, parent) == []
    finally:
        await stop_all(app)


@pytest.mark.asyncio
async def test_at_most_three_helpers_work_at_once(tmp_path, monkeypatch):
    app, svc, parent, work = await _setup(tmp_path, monkeypatch)
    try:
        ctx = _ctx(app, parent, work)
        monkeypatch.setattr(HO, "_running", lambda svc, sid: True)
        for i in range(HO.MAX_RUNNING):
            r = await _tool(app, HO.TOOL_START).execute({"task": f"t{i}"}, ctx)
            assert r.ok, r.error
        over = await _tool(app, HO.TOOL_START).execute({"task": "one more"}, ctx)
        assert not over.ok and "already working" in over.error
    finally:
        monkeypatch.undo()
        await stop_all(app)


@pytest.mark.asyncio
async def test_a_helper_of_another_session_is_not_yours(tmp_path, monkeypatch):
    app, svc, parent, work = await _setup(tmp_path, monkeypatch)
    try:
        r = await _tool(app, HO.TOOL_START).execute({"task": "x"}, _ctx(app, parent, work))
        async with client(app) as c:
            other = (await new_session(c, work))["id"]
        got = await _tool(app, HO.TOOL_CHECK).execute({"helper": r.data["helper"]}, _ctx(app, other, work))
        assert not got.ok and got.error == HO.NOT_YOURS
        stop = await _tool(app, HO.TOOL_STOP).execute({"helper": r.data["helper"]}, _ctx(app, other, work))
        assert not stop.ok and stop.error == HO.NOT_YOURS
    finally:
        await stop_all(app)


@pytest.mark.asyncio
async def test_the_users_stop_stops_its_helpers(tmp_path, monkeypatch):
    app, svc, parent, work = await _setup(tmp_path, monkeypatch)
    try:
        stopped: list[str] = []
        real = svc.interrupt

        async def spy(sid):
            stopped.append(sid)
            return await real(sid)

        r = await _tool(app, HO.TOOL_START).execute({"task": "x"}, _ctx(app, parent, work))
        helper = r.data["helper"]
        # The starting session has a reply running; the helper too.
        turn = S.HostedTurn("p-turn", parent)
        live = svc._live(store.get(app.state.platform.engine, parent))
        live.turn = turn
        monkeypatch.setattr(svc, "interrupt", spy)
        monkeypatch.setattr(HO, "_running", lambda s, sid: sid == helper)
        await real(parent)
        await until(lambda: helper in stopped)
    finally:
        monkeypatch.undo()
        await stop_all(app)


def test_the_handoff_box_reaches_only_its_four_tools_and_never_a_terminal():
    from iron_jarvis.mcpserver.server import CAPABILITY_TOOLS
    from iron_jarvis.terminals.session import normalise_pane_capabilities

    fam = CAPABILITY_TOOLS["handoff"]
    assert {n for n in HO.TOOL_NAMES if fam.admits(n)} == set(HO.TOOL_NAMES)
    assert not fam.admits("shell") and not fam.admits("memory_search")
    # A Build terminal's boxes never hold it; a Code session's do.
    assert "handoff" not in normalise_pane_capabilities({"handoff": True})
    assert JT.load(json.dumps({"handoff": True}))["handoff"] is True
    assert JT.wanted({"handoff": True})


def test_summarize_reads_only_the_turns_own_finished_work():
    evs = [
        {"type": "user", "turn": "a", "text": "go"},
        {"type": "tool", "turn": "a", "kind": "edit", "status": "done", "path": "cart.py", "title": "Edit cart.py"},
        {"type": "tool", "turn": "a", "kind": "edit", "status": "running", "path": "half.py", "title": "Edit"},
        {"type": "tool", "turn": "a", "kind": "edit", "status": "done", "path": "x.py", "parent": "h1"},
        {"type": "text_done", "turn": "a", "text": "Changed cart.py."},
        {"type": "text_done", "turn": "b", "text": "another turn"},
        {"type": "done", "turn": "a", "stop": "completed"},
    ]
    got = HO.summarize(evs, "a")
    assert got["files"] == ["cart.py"]
    assert got["reply"] == "Changed cart.py."
    assert got["status"] == "completed" and got["steps"] == 1


def test_a_long_reply_keeps_its_end():
    evs = [{"type": "text_done", "turn": "a", "text": "x" * (HO.RESULT_CHARS + 50) + "END"}]
    got = HO.summarize(evs, "a")
    assert got["reply"].endswith("END") and len(got["reply"]) <= HO.RESULT_CHARS + 1


def test_tool_names_are_read_the_way_a_model_writes_them():
    assert HO.tool_id("Claude Code") == "claude-code"
    assert HO.tool_id("OpenAI Codex") == "codex"
    assert HO.tool_id("open_code") == "opencode"
    assert HO.tool_id("notepad") == ""


@pytest.mark.asyncio
async def test_a_call_from_outside_a_code_session_is_refused(tmp_path):
    app = create_app(str(tmp_path / "home"))
    try:
        p = app.state.platform
        ctx = ToolContext(workspace=tmp_path, session_id="chat", agent_run_id="", config=p.config,
                          event_bus=p.event_bus, engine=p.engine)
        r = await p.registry.get(HO.TOOL_START).execute({"task": "x"}, ctx)
        assert not r.ok and r.error == HO.NOT_A_CODE_SESSION
    finally:
        await stop_all(app)
