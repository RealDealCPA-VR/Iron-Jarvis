"""v1.332.0 (L3) — in a local-only chat, a tool that reaches the internet ASKS.

THE USER'S WORDS: "nothing hits the cloud in a specific session without user
permission." The local-only guard already keeps cloud MODELS away and drops the
web tools; this file pins the rest of the tools that can carry the chat's words
past this PC by themselves (``egress.ask_first_tools``): a browser tool that
acts or loads an address, a channel send, a command runner, a pack tool.

In a LOCAL-ONLY stream turn each such call shows its own card in EVERY approval
mode (yolo and approve-for-me included); no tab grant, standing grant or
conversation grant skips it; the card offers "Allow once" only. The POST lane
cannot ask anyone, so it does not arm them at all. Outside local only nothing
changes (each test carries that control).

Driven through the REAL app and the REAL ``registry.invoke`` (the v1.270.1
lesson: a stub of invoke proves intent, not outcome) — only the model and each
tool's ``execute`` are doubles. No test asserts a duration.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest

import iron_jarvis.daemon.routes.chat as chat_routes
from iron_jarvis.core.grants import args_hash
from iron_jarvis.core.turns import TURNS
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.schemas import ChatBody
from iron_jarvis.mcp.tools import MCPRemoteTool
from iron_jarvis.providers import egress
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from iron_jarvis.providers.router import RouteResult
from iron_jarvis.tools.base import ToolResult

NAV = {"url": "https://example.com/"}
PACK_TOOL = "mcp__fakepack__lookup"


@pytest.fixture(autouse=True)
def _clean_turns():
    for tid in TURNS.running_ids():
        TURNS.release(tid)
    yield
    for tid in TURNS.running_ids():
        TURNS.release(tid)


@pytest.fixture()
def app(tmp_path, monkeypatch):
    app = create_app(str(tmp_path))
    platform = app.state.platform
    # The browser tools are armable only at this level (it ships "off").
    platform.config.browser_access = "interactive"
    # A pack tool registered the way the boot loader does (mcp=True).
    platform.registry.register(
        MCPRemoteTool.from_spec(SimpleNamespace(), "fakepack", {"name": "lookup"}), mcp=True
    )
    monkeypatch.setattr(chat_routes, "_ASK_POLL_S", 0.05)
    return app


# --------------------------------------------------------------------------- #
# Drivers
# --------------------------------------------------------------------------- #
def _executes(app, monkeypatch, *names: str) -> dict[str, list[dict]]:
    """Stub ONLY each tool's ``execute``; the registry's gate stays real."""
    ran: dict[str, list[dict]] = {n: [] for n in names}
    for name in names:
        tool = app.state.platform.registry.get(name)
        assert tool is not None, f"{name} is not registered"

        async def _fake(args, ctx, _n=name):
            ran[_n].append(dict(args))
            return ToolResult(ok=True, output=f"{_n} ran")

        monkeypatch.setattr(tool, "execute", _fake)
    return ran


def _script(app, calls: list[ToolCall]) -> None:
    """Round 0 makes *calls*; round 1 answers in words."""
    state = {"n": 0}

    async def fake_stream(*args: Any, **kw: Any):
        n = state["n"]
        state["n"] += 1
        if n == 0:
            yield {"type": "final", "response": LLMResponse(text="", tool_calls=calls, usage={}),
                   "provider": "mock", "model": "mock"}
        else:
            yield {"type": "text", "text": "Done."}
            yield {"type": "final", "response": LLMResponse(text="Done.", usage={}),
                   "provider": "mock", "model": "mock"}

    app.state.platform.router.stream = fake_stream


def _frames(chunk: Any) -> list[tuple[str, dict]]:
    text = chunk.decode() if isinstance(chunk, bytes) else str(chunk)
    out = []
    for block in text.split("\n\n"):
        ev, data = "", None
        for line in block.splitlines():
            if line.startswith("event: "):
                ev = line[7:]
            elif line.startswith("data: "):
                data = json.loads(line[6:])
        if ev:
            out.append((ev, data or {}))
    return out


def _stream(app, body: dict, *, answer: str = "once", on_card=None, family=None) -> list[tuple[str, dict]]:
    """Run one stream turn; every card is answered with *answer*. *on_card*
    sees each card frame BEFORE it is answered. *family* arms tools the way
    the browser sidebar does (``arm_family``): an acting tool is ask-armed,
    visible and never granted, so outside local only it still cards."""
    platform = app.state.platform
    approvals = chat_routes._approvals(SimpleNamespace(platform=platform))

    async def go():
        kw = {"arm_family": frozenset(family)} if family else {}
        gen = await chat_routes.stream_chat_turn(platform, {}, ChatBody(**body), **kw)
        seen = []
        async for chunk in gen:
            for ev, data in _frames(chunk):
                seen.append((ev, data))
                if ev == "approval":
                    if on_card is not None:
                        on_card(data)
                    approvals.resolve(data["id"], answer)
        return seen

    return asyncio.run(asyncio.wait_for(go(), 30))


def _cards(frames) -> list[dict]:
    return [d for ev, d in frames if ev == "approval"]


def _body(tools: list[str], *, mode: str, local_only: bool, **over) -> dict:
    return {
        "messages": [{"role": "user", "content": "please do it"}],
        "tools": tools, "approval_mode": mode, "local_only": local_only, **over,
    }


# --------------------------------------------------------------------------- #
# The tool set itself
# --------------------------------------------------------------------------- #
def test_the_ask_first_set_is_read_off_the_registry_not_a_name_prefix(app):
    reg = app.state.platform.registry
    found = egress.ask_first_tools(reg)
    for name in ("browser_navigate", "browser_create_tab", "browser_click", "browser_type",
                 "browser_press_key", "shell", "run_code", "notify", PACK_TOOL):
        assert name in found, name
    # The browser READ tier stays out (min_access read_only, no url argument),
    # and so does a plain local tool.
    for name in ("browser_read_page", "browser_list_tabs", "browser_screenshot", "read_file"):
        assert name not in found, name


def test_a_pack_tool_is_known_by_its_class_or_by_the_registrys_pack_list(app):
    from iron_jarvis.tools.base import Tool

    # By class, with no registry to ask.
    pack = MCPRemoteTool.from_spec(SimpleNamespace(), "otherpack", {"name": "find"})
    assert egress.reaches_outside(pack)

    # By the registry's own pack list (a pack tool of another class).
    class _Wrapped(Tool):
        name = "mcp__wrapped__thing"
        description = "a pack tool behind a wrapper"
        input_schema = {"type": "object", "properties": {}}

        async def execute(self, args, ctx):  # pragma: no cover - never run
            return ToolResult(ok=True)

    reg = app.state.platform.registry
    reg.register(_Wrapped(), mcp=True)
    assert "mcp__wrapped__thing" in egress.ask_first_tools(reg)
    assert not egress.reaches_outside(_Wrapped())  # control: not by its name


def test_asks_first_holds_only_inside_a_local_only_scope(app):
    reg = app.state.platform.registry
    assert not egress.asks_first(reg, "browser_navigate")
    with egress.local_only_scope(True):
        assert egress.asks_first(reg, "browser_navigate")
        assert not egress.asks_first(reg, "read_file")
    # A one-message OK for a cloud MODEL does not cover a tool.
    with egress.local_only_scope(True, "claude-cli"):
        assert egress.asks_first(reg, "shell")


# --------------------------------------------------------------------------- #
# The stream lane cards, in every mode
# --------------------------------------------------------------------------- #
def test_in_yolo_a_local_only_browser_action_cards_and_runs_only_after_the_answer(app, monkeypatch):
    ran = _executes(app, monkeypatch, "browser_navigate")
    _script(app, [ToolCall(id="c1", name="browser_navigate", arguments=dict(NAV))])
    before: list[int] = []
    frames = _stream(
        app, _body(["browser_navigate"], mode="yolo", local_only=True),
        on_card=lambda d: before.append(len(ran["browser_navigate"])),
    )
    cards = _cards(frames)
    assert [c["tool"] for c in cards] == ["browser_navigate"]
    assert before == [0], "the tool ran before the card was answered"
    # Allow once: the REAL registry ran it (the answer carried the grant).
    assert ran["browser_navigate"] == [NAV]


def test_outside_local_only_yolo_runs_the_same_call_without_a_card(app, monkeypatch):
    ran = _executes(app, monkeypatch, "browser_navigate")
    _script(app, [ToolCall(id="c1", name="browser_navigate", arguments=dict(NAV))])
    frames = _stream(app, _body(["browser_navigate"], mode="yolo", local_only=False))
    assert _cards(frames) == []
    assert ran["browser_navigate"] == [NAV]


def test_a_denied_local_only_card_never_runs_the_tool(app, monkeypatch):
    ran = _executes(app, monkeypatch, "browser_navigate")
    _script(app, [ToolCall(id="c1", name="browser_navigate", arguments=dict(NAV))])
    frames = _stream(app, _body(["browser_navigate"], mode="yolo", local_only=True), answer="deny")
    assert len(_cards(frames)) == 1
    assert ran["browser_navigate"] == []


def test_the_card_offers_allow_once_only_and_a_wider_answer_is_read_as_once(app, monkeypatch):
    ran = _executes(app, monkeypatch, "browser_navigate")
    _script(app, [ToolCall(id="c1", name="browser_navigate", arguments=dict(NAV))])
    frames = _stream(app, _body(["browser_navigate"], mode="yolo", local_only=True), answer="always")
    (card,) = _cards(frames)
    assert card["can_always"] is False
    assert card["once_only"] is True
    resolved = [d for ev, d in frames if ev == "approval_resolved"]
    assert [r["decision"] for r in resolved] == ["once"]
    assert ran["browser_navigate"] == [NAV]
    # No standing grant was minted from an "always" sent anyway.
    assert app.state.platform.grants.list() == []


def _family_turn(app, local_only: bool, *, answer: str = "once", args=None, **over):
    """One sidebar-shaped turn (approve-for-me, the acting tool ask-armed)."""
    _script(app, [ToolCall(id="c1", name="browser_navigate", arguments=dict(args or NAV))])
    return _stream(app, _body([], mode="approve_for_me", local_only=local_only, **over),
                   answer=answer, family={"browser_navigate"})


def test_on_approve_for_me_a_plus_armed_browser_action_cards_only_in_local_only(app, monkeypatch):
    ran = _executes(app, monkeypatch, "browser_navigate")

    def turn(local_only):
        _script(app, [ToolCall(id="c1", name="browser_navigate", arguments=dict(NAV))])
        return _stream(app, _body(["browser_navigate"], mode="approve_for_me", local_only=local_only))

    # Control: arming it from "+" is the consent outside local only.
    assert _cards(turn(False)) == []
    assert [c["tool"] for c in _cards(turn(True))] == ["browser_navigate"]
    assert len(ran["browser_navigate"]) == 2


def test_outside_local_only_the_same_card_still_offers_always(app, monkeypatch):
    _executes(app, monkeypatch, "browser_navigate")
    (card,) = _cards(_family_turn(app, False))
    assert card["can_always"] is True
    assert "once_only" not in card


def test_a_conversation_grant_does_not_skip_the_local_only_card(app, monkeypatch):
    ran = _executes(app, monkeypatch, "browser_navigate")
    grant = {"granted_tools": ["browser_navigate"]}
    assert len(_cards(_family_turn(app, False))) == 1  # it asks with no grant
    assert _cards(_family_turn(app, False, **grant)) == []  # control: the grant covers it
    assert len(_cards(_family_turn(app, True, **grant))) == 1
    assert len(ran["browser_navigate"]) == 3


def test_a_standing_grant_does_not_skip_the_local_only_card(app, monkeypatch):
    _executes(app, monkeypatch, "browser_navigate")
    assert len(_cards(_family_turn(app, False))) == 1  # it asks with no grant
    app.state.platform.grants.create(
        "chat", "chat", "browser_navigate", args_hash("browser_navigate", NAV), "example.com"
    )
    assert _cards(_family_turn(app, False)) == []  # control: the grant covers it
    assert len(_cards(_family_turn(app, True))) == 1


def test_the_tab_grant_does_not_skip_the_local_only_card(app, monkeypatch):
    _executes(app, monkeypatch, "browser_navigate")
    assert len(_cards(_family_turn(app, False))) == 1  # it asks with no grant
    app.state.platform.browser.set_auto_allow(True)  # the sidebar's switch: every tab
    assert _cards(_family_turn(app, False)) == []  # control: the switch covers it
    frames = _family_turn(app, True, answer="tab")
    assert len(_cards(frames)) == 1
    assert [d["decision"] for ev, d in frames if ev == "approval_resolved"] == ["once"]


def test_a_tab_answer_in_a_local_only_turn_grants_no_tab(app, monkeypatch):
    _executes(app, monkeypatch, "browser_navigate")
    tabs = app.state.platform.browser.tab_grants
    _family_turn(app, True, answer="tab", args={**NAV, "tab_id": 7})
    assert not tabs.covers(7)
    # Control: the same answer outside local only grants the tab.
    _family_turn(app, False, answer="tab", args={**NAV, "tab_id": 7})
    assert tabs.covers(7)


def test_a_pack_tool_and_shell_card_in_yolo_each_call_on_its_own_card(app, monkeypatch):
    ran = _executes(app, monkeypatch, PACK_TOOL, "shell")
    calls = [
        ToolCall(id="m1", name=PACK_TOOL, arguments={}),
        ToolCall(id="m2", name=PACK_TOOL, arguments={}),
        ToolCall(id="s1", name="shell", arguments={"command": "echo hi"}),
    ]
    _script(app, calls)
    frames = _stream(app, _body([PACK_TOOL, "shell"], mode="yolo", local_only=True))
    cards = _cards(frames)
    # One card per call: the shared `mcp_call` key never folds two into one.
    assert [c["tool"] for c in cards] == [PACK_TOOL, PACK_TOOL, "shell"]
    assert all("count" not in c for c in cards)
    assert len(ran[PACK_TOOL]) == 2 and len(ran["shell"]) == 1

    # Control: outside local only, yolo runs all three with no card.
    ran[PACK_TOOL].clear()
    ran["shell"].clear()
    _script(app, calls)
    frames = _stream(app, _body([PACK_TOOL, "shell"], mode="yolo", local_only=False))
    assert _cards(frames) == []
    assert len(ran[PACK_TOOL]) == 2 and len(ran["shell"]) == 1


def test_a_local_tool_in_a_local_only_yolo_turn_still_runs_without_a_card(app, monkeypatch):
    ran = _executes(app, monkeypatch, "read_file")
    _script(app, [ToolCall(id="r1", name="read_file", arguments={"path": "notes.txt"})])
    frames = _stream(app, _body(["read_file"], mode="yolo", local_only=True))
    assert _cards(frames) == []
    assert len(ran["read_file"]) == 1


# --------------------------------------------------------------------------- #
# The POST lane cannot ask, so it does not arm them
# --------------------------------------------------------------------------- #
class _ToolRouter:
    default_provider = "mock"

    def __init__(self):
        self.tools: list[list[str]] = []

    async def complete(self, **kw):
        self.tools.append([t.get("name") for t in kw.get("tools") or []])
        return RouteResult(LLMResponse(text="done", tool_calls=[], usage={}), "mock", "mock")


def test_the_post_lane_does_not_arm_reaching_out_tools_in_a_local_only_turn(app):
    from iron_jarvis.daemon.chat_turn import run_chat_turn

    router = _ToolRouter()
    app.state.platform.router = router
    picked = ["browser_navigate", "shell", PACK_TOOL, "notify", "read_file"]

    def offered(local_only):
        body = ChatBody(messages=[{"role": "user", "content": "please do it"}],
                        tools=picked, local_only=local_only)
        asyncio.run(run_chat_turn(app.state.platform, {}, body))
        return set(router.tools[-1])

    names = offered(True)
    assert not names & {"browser_navigate", "shell", PACK_TOOL, "notify"}, names
    assert "read_file" in names
    # Anti-vacuity: the same turn, not local only, arms them.
    names = offered(False)
    assert {"browser_navigate", "shell", PACK_TOOL, "notify", "read_file"} <= names, names


# --------------------------------------------------------------------------- #
# Review round (v1.332.0): the rest of the ask-first set, and what stays put
# --------------------------------------------------------------------------- #
HOOK = {
    "slug": "out1", "direction": "outbound",
    "target_url": "https://hooks.example.com/in", "event_types": ["session.completed"],
}


def test_an_outbound_webhook_asks_first_in_a_local_only_turn(app, monkeypatch):
    # It sets an address outside this PC that later events are posted to.
    reg = app.state.platform.registry
    assert {"webhook_add", "webhook_update"} <= egress.ask_first_tools(reg)
    ran = _executes(app, monkeypatch, "webhook_add")
    _script(app, [ToolCall(id="w1", name="webhook_add", arguments=dict(HOOK))])
    before: list[int] = []
    frames = _stream(
        app, _body(["webhook_add"], mode="yolo", local_only=True),
        on_card=lambda d: before.append(len(ran["webhook_add"])),
    )
    assert [c["tool"] for c in _cards(frames)] == ["webhook_add"]
    assert before == [0] and ran["webhook_add"] == [HOOK]
    # Control: outside local only, yolo runs it with no card.
    _script(app, [ToolCall(id="w1", name="webhook_add", arguments=dict(HOOK))])
    assert _cards(_stream(app, _body(["webhook_add"], mode="yolo", local_only=False))) == []
    assert len(ran["webhook_add"]) == 2


def test_the_post_lane_does_not_arm_webhook_add_in_a_local_only_turn(app):
    from iron_jarvis.daemon.chat_turn import run_chat_turn

    router = _ToolRouter()
    app.state.platform.router = router

    def offered(local_only):
        body = ChatBody(messages=[{"role": "user", "content": "please do it"}],
                        tools=["webhook_add", "read_file"], local_only=local_only)
        asyncio.run(run_chat_turn(app.state.platform, {}, body))
        return set(router.tools[-1])

    assert "webhook_add" not in offered(True)
    assert "webhook_add" in offered(False)  # anti-vacuity


def test_a_fault_in_the_ask_first_check_cards_only_inside_local_only(app, monkeypatch):
    # Fail closed where the guard is on; outside local only a fault changes
    # nothing (the turn behaves exactly as before v1.332.0).
    ran = _executes(app, monkeypatch, "browser_navigate")

    def _broken(*a, **kw):
        raise RuntimeError("registry fault")

    monkeypatch.setattr(egress, "asks_first", _broken)
    _script(app, [ToolCall(id="c1", name="browser_navigate", arguments=dict(NAV))])
    assert len(_cards(_stream(app, _body(["browser_navigate"], mode="yolo", local_only=True)))) == 1
    _script(app, [ToolCall(id="c1", name="browser_navigate", arguments=dict(NAV))])
    assert _cards(_stream(app, _body(["browser_navigate"], mode="yolo", local_only=False))) == []
    assert len(ran["browser_navigate"]) == 2


def test_calls_of_a_tool_that_stays_on_this_pc_still_share_one_card(app, monkeypatch):
    # Only a reaching-out call gets a card of its own: two asks of a local
    # ask-tier tool in a local-only round stay ONE card for the batch.
    from iron_jarvis.tools.base import Tool

    class _TidyNote(Tool):
        name = "tidy_local_note"
        description = "tidy a note on this PC"
        input_schema = {"type": "object", "properties": {"name": {"type": "string"}}}

        async def execute(self, args, ctx):
            return ToolResult(ok=True, output="tidied")

    import iron_jarvis.tools.autoselect as autoselect

    app.state.platform.registry.register(_TidyNote())
    # Ask-armed by the sentence (visible, never granted), like `shell` is.
    monkeypatch.setattr(autoselect, "select_ask_tools", lambda text: ["tidy_local_note"])
    ran = _executes(app, monkeypatch, "tidy_local_note")
    calls = [ToolCall(id=f"t{i}", name="tidy_local_note", arguments={"name": f"n{i}"}) for i in (1, 2)]
    _script(app, calls)
    cards = _cards(_stream(app, _body([], mode="approve_for_me", local_only=True, auto_tools=True)))
    assert len(cards) == 1 and cards[0]["count"] == 2
    assert "once_only" not in cards[0]
    assert len(ran["tidy_local_note"]) == 2


class _RemoteBase:
    """A memory base that is not on this PC (only the markdown kinds are)."""

    name = "notion"

    def __init__(self):
        self.calls: list[str] = []

    def search(self, query, k=5):
        self.calls.append(query)
        return [{"source": "notion", "title": "remote", "snippet": "plan in the cloud", "score": 9.0}]

    def append(self, title, content):
        self.calls.append(title)
        return "notion-page"


def _memory(tmp_path):
    from iron_jarvis.ltm.base import MarkdownDirConnector
    from iron_jarvis.ltm.manager import LongTermMemory

    brain = MarkdownDirConnector(tmp_path / "brain")
    brain.name = "brain"
    (tmp_path / "brain" / "plan.md").write_text("the quarterly plan", encoding="utf-8")
    remote = _RemoteBase()
    ltm = LongTermMemory()
    ltm.register(brain)
    ltm.register(remote)
    return ltm, remote


def test_ltm_search_in_a_local_only_turn_searches_only_bases_on_this_pc(tmp_path):
    from iron_jarvis.ltm.tools import LTMSearchTool

    ltm, remote = _memory(tmp_path)
    tool = LTMSearchTool(ltm)

    async def search(**args):
        return await tool.execute({"query": "plan", **args}, None)

    async def inside():
        with egress.local_only_scope(True):
            return await search(), await search(source="notion")

    merged, named = asyncio.run(inside())
    assert merged.ok and {h["source"] for h in merged.data["results"]} == {"brain"}
    assert not named.ok and "local only" in named.error and "notion" in named.error
    assert remote.calls == [], "a remote base was searched in a local-only turn"
    # Control: outside local only the same search reaches every base.
    out = asyncio.run(search())
    assert "notion" in {h["source"] for h in out.data["results"]}
    assert remote.calls


def test_ltm_append_in_a_local_only_turn_writes_only_to_a_base_on_this_pc(tmp_path):
    from iron_jarvis.ltm.tools import LTMAppendTool

    ltm, remote = _memory(tmp_path)
    tool = LTMAppendTool(ltm)

    async def append(source):
        return await tool.execute({"title": "note", "content": "words", "source": source}, None)

    async def inside():
        with egress.local_only_scope(True):
            return await append("notion"), await append("brain")

    refused, local = asyncio.run(inside())
    assert not refused.ok and "local only" in refused.error
    assert local.ok
    assert remote.calls == []
    assert asyncio.run(append("notion")).ok and remote.calls == ["note"]  # control
