"""MC5 (third fix plan, group 17): a call through the door is bounded, and an
approved call is checked again after its card.

The door (``mcpserver/server.py``) lets a coding tool in a Build terminal or a
Build › Code session call a few of Iron Jarvis's tools. Two holes:

1. **No time limit.** ``_call_tool`` handed ``registry.invoke`` no
   ``deadline_s``, so a tool that never answered held the coding tool's request
   for ever, while every other lane (agent runs, both chat lanes, workflow
   steps) passes ``agents/runtime._tool_deadline(config)``.
2. **Not re-checked after the card.** The grant was read once, before the
   card. While the card waited (up to 45 s), the user could untick the box,
   stop the terminal or the Code session, or the door's session could be
   closed - and pressing Allow afterwards still ran the tool.

Everything runs through the REAL ``/mcp`` route, the REAL ``ToolRegistry``, the
REAL permission engine and the REAL approvals registry; the only stand-ins are
the panes (as ``PaneTokenStore`` reads them), a Code session's live state (as
``HostedTokens`` reads it) and the user's answer, given from another thread as
the existing door tests do.
"""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import pytest
from sqlmodel import select

import iron_jarvis.mcpserver.server as server_mod
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import ToolInvocation
from iron_jarvis.hosted.jarvis_tools import HostedTokens, grant_id
from iron_jarvis.mcpserver.jsonrpc import JsonRpcRequest
from iron_jarvis.tools.base import Tool, ToolResult
from iron_jarvis.tools.permissions import PermissionEngine
from tests.test_mcp_server_v1238 import (
    INSTALL_TOKEN,
    OTHER_PANE_ID,
    PANE_ID,
    FakePane,
    Harness,
)

ASK_TOOL = "browser_click"
ASK_ARGS = {"target": "e12"}  # passes the tool's own argument check, so Allow reaches execute


@pytest.fixture()
def door(platform, monkeypatch):
    """A Build terminal holding Browser, the install bearer set, interactive access."""
    monkeypatch.setenv("IRONJARVIS_TOKEN", INSTALL_TOKEN)
    platform.config.browser_access = "interactive"
    panes = {
        PANE_ID: FakePane(PANE_ID, {"browser": True}, cwd=str(platform.config.home)),
        OTHER_PANE_ID: FakePane(OTHER_PANE_ID, {"browser": True}),
    }
    return Harness(platform, panes, {"browser": True})


def _record_runs(platform, name: str) -> list[str]:
    """Record every time the REAL tool's ``execute`` is reached (and call through)."""
    ran: list[str] = []
    tool = platform.registry.get(name)
    real_execute = tool.execute

    async def spy_execute(*args, **kw):  # spies take (*args, **kw)
        ran.append(name)
        return await real_execute(*args, **kw)

    tool.execute = spy_execute
    return ran


def _answer_after(approvals, change, decision: str = "once") -> threading.Thread:
    """When the card is on screen: make ``change`` (the user stops/unticks
    something), THEN answer the card. From another thread, as in life - the
    request blocks the test's thread while the door waits on the card."""
    stop = threading.Event()
    done: list[str] = []

    def _run() -> None:
        while not stop.wait(0.01):
            for approval_id in list(approvals.pending_ids()):
                change()
                if approvals.resolve(approval_id, decision):
                    done.append(approval_id)
                    return

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    thread.stop = stop  # type: ignore[attr-defined]
    thread.done = done  # type: ignore[attr-defined]
    return thread


def _call(harness, session_id: str, *, token: str | None = None, request_id: int = 70):
    return harness.post(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": ASK_TOOL, "arguments": dict(ASK_ARGS)},
        },
        session_id=session_id,
        token=token,
    )


def _approved_after(harness, change, *, token: str | None = None):
    """Handshake, call the ask-tier tool, change the grant while its card waits,
    then press Allow. Returns (response, ran)."""
    ran = _record_runs(harness.platform, ASK_TOOL)
    session_id = harness.initialize(token=token)
    thread = _answer_after(harness.platform.approvals, change)
    try:
        response = _call(harness, session_id, token=token)
    finally:
        thread.stop.set()  # type: ignore[attr-defined]
    assert thread.done, "the call never put a card in front of the user"  # type: ignore[attr-defined]
    return response, ran


def _assert_refused_and_recorded(harness, response, ran) -> None:
    assert response.status_code == 200, response.text
    assert not ran, (
        "the user pressed Allow on a card whose grant no longer held, and the "
        "tool RAN - the call must be checked again after the card"
    )
    body = response.json()["result"]
    assert body["isError"] is True, body
    text = body["content"][0]["text"]
    assert server_mod.MCP_ASK_NO_LONGER_ALLOWED_MESSAGE in text, text
    with session_scope(harness.platform.engine) as db:
        rows = [r for r in db.exec(select(ToolInvocation)).all() if r.tool == ASK_TOOL]
    assert rows and rows[-1].ok is False, "the refusal after the card was not recorded"


# --------------------------------------------------------------------------- #
# The control: nothing changes while the card waits -> Allow runs the tool.
# --------------------------------------------------------------------------- #
def test_allow_with_nothing_changed_still_runs_the_tool(door):
    response, ran = _approved_after(door, lambda: None)
    assert response.status_code == 200, response.text
    assert ran == [ASK_TOOL], "an approved call whose grant still holds must run"


# --------------------------------------------------------------------------- #
# A Build terminal.
# --------------------------------------------------------------------------- #
def test_unticking_browser_while_the_card_waits_refuses_the_call(door):
    def untick() -> None:
        door.panes[PANE_ID].capabilities = {"browser": False}

    response, ran = _approved_after(door, untick)
    _assert_refused_and_recorded(door, response, ran)


def test_closing_the_terminal_while_the_card_waits_refuses_the_call(door):
    """What ``TerminalManager.kill`` does: both the token and the door's sessions go."""

    def close() -> None:
        door.tokens.revoke_pane(PANE_ID)
        door.sessions.revoke_pane(PANE_ID)

    response, ran = _approved_after(door, close)
    _assert_refused_and_recorded(door, response, ran)


def test_a_revoked_token_alone_refuses_the_call(door):
    response, ran = _approved_after(door, lambda: door.tokens.revoke_pane(PANE_ID))
    _assert_refused_and_recorded(door, response, ran)


def test_a_terminal_whose_shell_died_refuses_the_call(door):
    def die() -> None:
        door.panes[PANE_ID].alive = False

    response, ran = _approved_after(door, die)
    _assert_refused_and_recorded(door, response, ran)


def test_a_closed_door_session_alone_refuses_the_call(door):
    """The coding tool's MCP session is closed (DELETE /mcp, or revoked) while
    its token still works."""
    response, ran = _approved_after(door, lambda: door.sessions.revoke_pane(PANE_ID))
    _assert_refused_and_recorded(door, response, ran)


# --------------------------------------------------------------------------- #
# A Build › Code session (its own token store, read LIVE from the service).
# --------------------------------------------------------------------------- #
SID = "sess_door_call"


@pytest.fixture()
def code_door(door):
    """``platform.hosted`` as ``HostedTokens`` reads it: a live session with a
    ``running`` flag, its boxes (``caps``) and its folder. The token store is
    the real one."""
    platform = door.platform
    live = SimpleNamespace(running=True, caps={"browser": True},
                           folder=str(platform.config.home))
    svc = SimpleNamespace(live={SID: live}, platform=platform)
    svc.tokens = HostedTokens(svc)
    platform.hosted = svc
    token = svc.tokens.mint(SID, {"browser": True})
    return SimpleNamespace(harness=door, live=live, svc=svc, token=token)


def test_a_code_session_control_still_runs_when_nothing_changed(code_door):
    response, ran = _approved_after(code_door.harness, lambda: None, token=code_door.token)
    assert response.status_code == 200, response.text
    assert ran == [ASK_TOOL]


def test_stopping_the_code_session_while_the_card_waits_refuses_the_call(code_door):
    def stop() -> None:
        code_door.live.running = False

    response, ran = _approved_after(code_door.harness, stop, token=code_door.token)
    _assert_refused_and_recorded(code_door.harness, response, ran)


def test_turning_the_code_sessions_browser_box_off_refuses_the_call(code_door):
    def untick() -> None:
        code_door.live.caps = {"browser": False}

    response, ran = _approved_after(code_door.harness, untick, token=code_door.token)
    _assert_refused_and_recorded(code_door.harness, response, ran)


def test_revoking_the_code_sessions_token_refuses_the_call(code_door):
    """``HostedTokens.revoke``: the token AND every MCP session it opened."""
    response, ran = _approved_after(
        code_door.harness, lambda: code_door.svc.tokens.revoke(SID), token=code_door.token
    )
    assert not code_door.svc.tokens.store.has_pane(grant_id(SID))
    _assert_refused_and_recorded(code_door.harness, response, ran)


# --------------------------------------------------------------------------- #
# The time limit.
# --------------------------------------------------------------------------- #
NEVER = "browser_test_never_answers"


class _NeverAnswers(Tool):
    """A stand-in tool that never returns (a wedged page, a hung helper)."""

    name = NEVER
    description = "A test tool that never answers."
    input_schema = {"type": "object", "properties": {}}

    async def execute(self, args, ctx) -> ToolResult:  # noqa: ARG002
        await asyncio.Event().wait()
        return ToolResult(ok=True, output="unreachable")


async def test_a_call_that_never_answers_is_a_failed_call_within_the_deadline(door):
    platform = door.platform
    platform.registry.register(_NeverAnswers())
    # The REAL engine, with this one tool on allow (no card in this test).
    platform.permissions = PermissionEngine({**platform.config.permissions, NEVER: "allow"})
    # ``tool_call_timeout_s`` is an int setting; a tiny value keeps the test
    # short. Set past validation on purpose: the registry's own deadline
    # mechanism (and ``_tool_deadline``) read it as a float.
    object.__setattr__(platform.config, "tool_call_timeout_s", 0.05)
    from iron_jarvis.agents.runtime import _tool_deadline

    seen: list[dict] = []
    real_invoke = platform.registry.invoke

    async def spy_invoke(*args, **kw):  # spies take (*args, **kw)
        seen.append(kw)
        return await real_invoke(*args, **kw)

    platform.registry.invoke = spy_invoke
    grant = door.tokens.resolve(door.token)
    session = door.sessions.open(PANE_ID)
    request = JsonRpcRequest(method="tools/call", params={"name": NEVER, "arguments": {}}, id=7)
    try:
        # A GUARD, not a timing assertion: before the fix this call waits for
        # ever, and the guard turns that into a red line instead of a hung run.
        reply = await asyncio.wait_for(
            server_mod.dispatch(request, d=door.d, grant=grant, sessions=door.sessions,
                                session_id=session.id),
            timeout=10,
        )
    except asyncio.TimeoutError:
        pytest.fail("a door call into a tool that never answers was held with no time limit")
    assert seen and seen[-1].get("deadline_s") == _tool_deadline(platform.config), seen
    result = reply.payload["result"]
    assert result["isError"] is True, result
    assert "did not finish within" in result["content"][0]["text"], result
    with session_scope(platform.engine) as db:
        rows = [r for r in db.exec(select(ToolInvocation)).all() if r.tool == NEVER]
    assert rows and rows[-1].ok is False, "the cut-off call was not recorded"
