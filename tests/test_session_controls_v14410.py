"""v1.441.0 (lane g1): the Session tab's live controls in Build › Code — the
TOOL's own requests, in the shapes MEASURED on this PC (scratch homes and
stand-in model servers; the drivers' docstrings say how):

* Claude Code 2.1.316: ``apply_flag_settings {effortLevel}`` changes the
  running effort (read back through ``get_settings`` ``applied.effort``);
  ``stop_task {task_id}`` stops one background task; ``mcp_reconnect`` and
  ``mcp_toggle`` act on one MCP server, ``mcp_status`` reads the list again.
* Codex 0.157.0: ``thread/backgroundTerminals/list`` / ``terminate`` (the
  experimental API), and ``mcpServer/oauth/login`` answering
  ``{authorizationUrl}`` with ``mcpServer/oauthLogin/completed`` after.

These pins drive the REAL drivers with a scripted peer that answers exactly
those shapes (never a real CLI), then the real routes through the fake
harness process.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable

import pytest

from iron_jarvis.hosted import claude as C
from iron_jarvis.hosted import codex as X
from iron_jarvis.hosted import session_controls as SC
from iron_jarvis.hosted import tool_setup as TS
from iron_jarvis.hosted.driver import ControlRefused


class Peer:
    """Plays the tool's side: every message the driver writes is recorded,
    and ``answer(msg)`` (when it returns a frame) is fed back on the loop."""

    def __init__(self, cls: type, answer: Callable[[dict[str, Any]], Any]) -> None:
        self.sent: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []
        self.answer = answer
        self.driver = cls(write=self.write, emit=self.events.append, ask=lambda a: None,
                          on_session_id=lambda s: None, mode="ask")

    async def write(self, obj: dict[str, Any]) -> None:
        self.sent.append(obj)
        reply = self.answer(obj)
        if reply is not None:
            asyncio.get_running_loop().call_soon(self.driver.on_message, reply)

    def notices(self) -> list[str]:
        return [e["text"] for e in self.events if e.get("type") == "notice"]

    def requests(self, key: str = "subtype") -> list[Any]:
        out = []
        for m in self.sent:
            if m.get("type") == "control_request":
                out.append(m["request"])
            elif "method" in m:
                out.append({"method": m["method"], **(m.get("params") or {})})
        return out


def claude_ok(response: dict[str, Any] | None = None, error: str = ""):
    def answer(msg: dict[str, Any]) -> Any:
        if msg.get("type") != "control_request":
            return None
        rid = msg["request_id"]
        if error:
            return {"type": "control_response", "response": {"subtype": "error", "request_id": rid, "error": error}}
        return {"type": "control_response", "response": {"subtype": "success", "request_id": rid,
                                                         "response": response or {}}}
    return answer


def claude_settings(effort_after: str | None):
    """apply_flag_settings answers success; get_settings answers the effort
    the session now uses (as 2.1.316 does, measured)."""
    def answer(msg: dict[str, Any]) -> Any:
        if msg.get("type") != "control_request":
            return None
        rid, sub = msg["request_id"], msg["request"]["subtype"]
        resp: dict[str, Any] = {}
        if sub == "get_settings":
            resp = {"effective": {}, "sources": [], "applied": {"model": "claude-haiku-5-5", "effort": effort_after}}
        return {"type": "control_response", "response": {"subtype": "success", "request_id": rid, "response": resp}}
    return answer


def _task(d: Any, sub: str, tid: str, **extra: Any) -> None:
    d.on_message({"type": "system", "subtype": sub, "task_id": tid, **extra})


# --------------------------------------------------------------------------- #
# Claude Code: live effort
# --------------------------------------------------------------------------- #


async def test_claude_effort_is_applied_live_and_read_back():
    p = Peer(C.ClaudeDriver, claude_settings("high"))
    assert await p.driver.set_effort("high") == "now"
    reqs = p.requests()
    assert reqs[0] == {"subtype": "apply_flag_settings", "settings": {"effortLevel": "high"}}
    assert reqs[1] == {"subtype": "get_settings"}
    assert p.driver.effort == "high"
    assert any("high effort" in n for n in p.notices())


async def test_claude_effort_back_to_default_sends_null():
    p = Peer(C.ClaudeDriver, claude_settings("medium"))
    assert await p.driver.set_effort("") == "now"
    assert p.requests()[0]["settings"] == {"effortLevel": None}
    assert any("its default (medium)" in n for n in p.notices())


@pytest.mark.parametrize("answer", [claude_settings("low"), claude_settings(None),
                                    claude_ok(error="Unknown subtype: apply_flag_settings")])
async def test_claude_effort_not_taken_live_falls_back_to_a_restart(answer):
    p = Peer(C.ClaudeDriver, answer)
    assert await p.driver.set_effort("high") == "next"
    # The restart still starts it on the new effort.
    assert p.driver.effort == "high"
    assert "--effort" in p.driver.argv(session_id="s", resume=True)
    assert not p.notices()


# --------------------------------------------------------------------------- #
# Claude Code: background tasks with Stop
# --------------------------------------------------------------------------- #


async def test_claude_lists_open_tasks_and_stops_one_by_its_own_stop_task():
    p = Peer(C.ClaudeDriver, claude_ok())
    _task(p.driver, "task_started", "bztz1", task_type="local_bash", description="Long ping",
          is_backgrounded=True, tool_use_id="toolu_1")
    _task(p.driver, "task_started", "ag1", task_type="local_agent", description="builder")
    rows = await p.driver.background_work()
    assert rows == [
        {"id": "bztz1", "title": "Long ping", "kind": "command", "can_stop": True},
        {"id": "ag1", "title": "builder", "kind": "helper", "can_stop": True},
    ]
    assert await p.driver.stop_background("bztz1") == "Long ping"
    assert p.requests()[-1] == {"subtype": "stop_task", "task_id": "bztz1"}
    before = list(p.notices())
    # Claude Code's own end of it (measured): said already by the service — no
    # second line, and it does not count as Claude Code waking itself.
    _task(p.driver, "task_notification", "bztz1", status="stopped", summary="Long ping")
    assert p.notices() == before
    assert not p.driver._alone
    assert p.driver.background_tasks == 1
    # A task that ended on its own is still said, as before.
    _task(p.driver, "task_notification", "ag1", status="completed", summary="built it")
    assert p.notices()[-1] == "A helper finished: built it"


async def test_claude_stop_of_an_ended_task_sends_nothing():
    p = Peer(C.ClaudeDriver, claude_ok())
    with pytest.raises(ControlRefused, match="already ended"):
        await p.driver.stop_background("nope")
    assert p.sent == []


async def test_claude_level_signal_names_known_tasks_only():
    p = Peer(C.ClaudeDriver, claude_ok())
    _task(p.driver, "task_started", "t1", task_type="local_bash")
    p.driver.on_message({"type": "system", "subtype": "background_tasks_changed",
                         "tasks": [{"task_id": "t1", "task_type": "local_bash", "description": "npm run dev"},
                                   {"task_id": "ghost", "task_type": "local_bash", "description": "x"}]})
    rows = await p.driver.background_work()
    assert [r["title"] for r in rows] == ["npm run dev"]
    assert p.driver.background_tasks == 1


# --------------------------------------------------------------------------- #
# Claude Code: MCP reconnect and turn off / on
# --------------------------------------------------------------------------- #


def claude_mcp(status: dict[str, str], fail: dict[str, str] | None = None):
    fail = fail or {}

    def answer(msg: dict[str, Any]) -> Any:
        if msg.get("type") != "control_request":
            return None
        rid, req = msg["request_id"], msg["request"]
        sub = req["subtype"]
        if sub == "mcp_toggle":
            status[req["serverName"]] = "connected" if req["enabled"] else "disabled"
        if sub in ("mcp_toggle", "mcp_reconnect") and req["serverName"] in fail:
            return {"type": "control_response",
                    "response": {"subtype": "error", "request_id": rid, "error": fail[req["serverName"]]}}
        resp: dict[str, Any] = {}
        if sub == "mcp_status":
            resp = {"mcpServers": [{"name": n, "status": s, **({"error": "Connection closed"} if s == "failed" else {}),
                                    "source": "dynamic"} for n, s in status.items()]}
        return {"type": "control_response", "response": {"subtype": "success", "request_id": rid, "response": resp}}
    return answer


async def test_claude_mcp_off_on_and_reconnect_use_its_own_requests_and_read_the_list_again():
    status = {"echo": "connected", "broken": "failed"}
    p = Peer(C.ClaudeDriver, claude_mcp(status, fail={"broken": "Connection closed"}))
    await p.driver.mcp_action("echo", "off")
    assert p.requests()[0] == {"subtype": "mcp_toggle", "serverName": "echo", "enabled": False}
    assert p.requests()[1] == {"subtype": "mcp_status"}
    assert {s["name"]: s["state"] for s in p.driver.mcp_servers} == {"echo": TS.OFF, "broken": TS.FAILED}
    setups = [e for e in p.events if e["type"] == "setup"]
    assert setups and setups[-1]["mcp_servers"][1]["error"] == "Connection closed"
    await p.driver.mcp_action("echo", "on")
    assert p.requests()[2] == {"subtype": "mcp_toggle", "serverName": "echo", "enabled": True}
    with pytest.raises(ControlRefused, match="could not reconnect broken: Connection closed"):
        await p.driver.mcp_action("broken", "reconnect")
    assert p.requests()[4] == {"subtype": "mcp_reconnect", "serverName": "broken"}
    assert p.requests()[5] == {"subtype": "mcp_status"}  # read again even after a refusal


def test_claude_servers_keep_the_error_mcp_status_carries():
    rows = TS.claude_servers([{"name": "a", "status": "failed", "error": "Connection closed"},
                              {"name": "b", "status": "connected", "error": "ignored"}])
    assert rows[0]["error"] == "Connection closed" and "error" not in rows[1]


# --------------------------------------------------------------------------- #
# Codex: background terminals and MCP sign-in
# --------------------------------------------------------------------------- #

MEASURED_LIST = {"data": [{"itemId": "call_bg1", "processId": "87917", "command": "ping -n 120 127.0.0.1",
                           "cwd": "C:\\work", "osPid": None, "cpuPercent": None, "rssKb": None}], "nextCursor": None}


def codex_peer(results: dict[str, Any]):
    def answer(msg: dict[str, Any]) -> Any:
        if "id" not in msg or "method" not in msg:
            return None
        res = results.get(msg["method"], {})
        if callable(res):
            res = res(msg.get("params") or {})
        if isinstance(res, Exception):
            return {"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32600, "message": str(res)}}
        return {"jsonrpc": "2.0", "id": msg["id"], "result": res}
    return answer


async def test_codex_lists_background_terminals_and_counts_them_as_background_work():
    live = {"data": list(MEASURED_LIST["data"])}

    def terminate(params):
        live["data"] = []
        return {"terminated": True}

    p = Peer(X.CodexDriver, codex_peer({"thread/backgroundTerminals/list": lambda _p: dict(live),
                                        "thread/backgroundTerminals/terminate": terminate}))
    p.driver.session_id = "thr1"
    rows = await p.driver.background_work()
    assert rows == [{"id": "87917", "title": "ping -n 120 127.0.0.1", "kind": "command", "can_stop": True}]
    assert p.driver.background_tasks == 1
    assert p.requests()[0] == {"method": "thread/backgroundTerminals/list", "threadId": "thr1",
                               "limit": X.TERMINALS_MAX}
    assert await p.driver.stop_background("87917") == "ping -n 120 127.0.0.1"
    assert {"method": "thread/backgroundTerminals/terminate", "threadId": "thr1", "processId": "87917"} in p.requests()
    assert p.driver.background_tasks == 0
    for t in list(p.driver._bg):
        t.cancel()


async def test_codex_terminate_that_ended_is_refused_in_words():
    p = Peer(X.CodexDriver, codex_peer({"thread/backgroundTerminals/terminate": {"terminated": False}}))
    p.driver.session_id = "thr1"
    with pytest.raises(ControlRefused, match="already ended"):
        await p.driver.stop_background("1")


async def test_codex_never_writes_a_terminal_read_mid_turn():
    p = Peer(X.CodexDriver, codex_peer({"thread/backgroundTerminals/list": MEASURED_LIST}))
    p.driver.session_id = "thr1"
    await p.driver.background_work()
    n = len(p.sent)
    p.driver.turn_open = True
    rows = await p.driver.background_work()
    assert len(p.sent) == n and rows and rows[0]["id"] == "87917"
    for t in list(p.driver._bg):
        t.cancel()


async def test_codex_reads_its_terminals_when_a_turn_ends_and_follows_them(monkeypatch):
    monkeypatch.setattr(X, "TERMINALS_POLL_S", 0.05)
    state = {"data": list(MEASURED_LIST["data"])}
    p = Peer(X.CodexDriver, codex_peer({"thread/backgroundTerminals/list": lambda _p: dict(state)}))
    p.driver.session_id = "thr1"
    p.driver.turn_open = True
    p.driver.on_message({"jsonrpc": "2.0", "method": "turn/completed",
                         "params": {"threadId": "thr1", "turn": {"id": "t", "status": "completed"}}})
    for _ in range(100):
        if p.driver.background_tasks == 1:
            break
        await asyncio.sleep(0.01)
    assert p.driver.background_tasks == 1
    # It ends by itself (Codex says nothing — measured): the next read sees it.
    state["data"] = []
    for _ in range(200):
        if p.driver.background_tasks == 0:
            break
        await asyncio.sleep(0.01)
    assert p.driver.background_tasks == 0
    await asyncio.sleep(0.1)
    assert all(t.done() for t in p.driver._bg)


async def test_codex_sign_in_answers_its_own_page_and_says_how_it_ended():
    url = ("http://127.0.0.1:47132/authorize?response_type=code&client_id=fake-client&state=s"
           "&redirect_uri=http%3A%2F%2F127.0.0.1%3A64347%2Fcallback%2Fx")
    p = Peer(X.CodexDriver, codex_peer({"mcpServer/oauth/login": {"authorizationUrl": url}}))
    p.driver.session_id = "thr1"
    assert await p.driver.mcp_action("authy", "sign_in") == {"url": url}
    assert p.requests()[0] == {"method": "mcpServer/oauth/login", "name": "authy", "threadId": "thr1"}
    p.driver.on_message({"jsonrpc": "2.0", "method": "mcpServer/oauthLogin/completed",
                         "params": {"name": "authy", "threadId": "thr1", "success": True}})
    p.driver.on_message({"jsonrpc": "2.0", "method": "mcpServer/oauthLogin/completed",
                         "params": {"name": "other", "threadId": "elsewhere", "success": True}})
    p.driver.on_message({"jsonrpc": "2.0", "method": "mcpServer/oauthLogin/completed",
                         "params": {"name": "authy", "success": False, "error": "denied"}})
    assert p.notices() == ["Signed in to authy. Codex starts it again now.",
                           "Signing in to authy did not finish: denied"]


@pytest.mark.parametrize("bad", ["javascript:alert(1)", "file:///C:/x", "http://user:pw@host/a",
                                 "https://", "ftp://host/x", "http://host/a b", ""])
async def test_codex_sign_in_never_hands_over_an_unsafe_address(bad):
    p = Peer(X.CodexDriver, codex_peer({"mcpServer/oauth/login": {"authorizationUrl": bad}}))
    p.driver.session_id = "thr1"
    with pytest.raises(ControlRefused, match="sign-in page"):
        await p.driver.mcp_action("authy", "sign_in")


async def test_codex_refusal_of_a_stdio_server_is_said_in_its_own_words():
    p = Peer(X.CodexDriver, codex_peer({"mcpServer/oauth/login": RuntimeError(
        "OAuth login is only supported for streamable HTTP servers.")}))
    p.driver.session_id = "thr1"
    with pytest.raises(ControlRefused, match="only supported for streamable HTTP"):
        await p.driver.mcp_action("echo", "sign_in")


# --------------------------------------------------------------------------- #
# the shared shape
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("state, offered, want", [
    (TS.CONNECTED, {"reconnect", "off", "on"}, ["reconnect", "off"]),
    (TS.FAILED, {"reconnect", "off", "on"}, ["reconnect", "off"]),
    (TS.OFF, {"reconnect", "off", "on"}, ["on"]),
    (TS.NEEDS_SIGN_IN, {"sign_in"}, ["sign_in"]),
    (TS.CONNECTED, {"sign_in"}, []),
    (TS.NEEDS_SIGN_IN, set(), []),
])
def test_actions_fit_the_server_state_and_the_tool(state, offered, want):
    assert SC.actions_for(state, offered) == want


class _Live:
    def __init__(self, driver, running=True):
        self.driver, self.running = driver, running


async def test_overview_carries_the_controls_only_when_the_tool_offers_them():
    p = Peer(C.ClaudeDriver, claude_ok())
    _task(p.driver, "task_started", "t1", task_type="local_bash", description="dev server")
    view = {"mcp": {"known": True, "servers": [{"name": "a", "state": "connected"}, {"name": "b", "state": "off"}]}}
    out = await SC.decorate_overview(view, _Live(p.driver))
    assert out["background_work"] == [{"id": "t1", "title": "dev server", "kind": "command", "can_stop": True}]
    assert [s.get("actions") for s in out["mcp"]["servers"]] == [["reconnect", "off"], ["on"]]

    class Plain:  # a driver with neither (Pi, OpenCode …): the tab as before
        mcp_servers = None

    plain = {"mcp": {"known": False, "servers": []}}
    assert await SC.decorate_overview(dict(plain), _Live(Plain())) == plain
    stopped = {"mcp": {"known": True, "servers": [{"name": "a", "state": "connected"}]}}
    assert "background_work" not in await SC.decorate_overview(stopped, _Live(p.driver, running=False))
    assert "actions" not in stopped["mcp"]["servers"][0]
