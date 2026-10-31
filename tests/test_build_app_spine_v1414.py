"""v1.414.0 — Build › Code is part of the app.

1. RUNNING WORK IS SEEN EVERYWHERE: a running Build › Code reply is in
   ``GET /chat/turns/running`` (Running now), ``/health``'s ``replies``
   (the orb), ``/system/activity`` (``busy`` + the update warning's words;
   ``working`` only while it is NOT parked on a card — the v1.391.0 rule),
   and a reply longer than a minute that ENDED is ``build.turn_finished``
   with ``surface: "build_app"`` (never for a Stop). A card left unanswered
   goes to the phone through the SAME path as Chat › Code's (``approval.
   requested`` with ``surface: "build"``), and the phone's approve answers it
   ONCE; never a question, a once-only card or any card in Full access.
2. IRON JARVIS'S OWN TOOLS, WITH CONSENT: the session's Capabilities (all off
   by default) hand the REAL tool Iron Jarvis's MCP connection per process —
   Claude Code ``--mcp-config`` (our own file, a token REFERENCE), Codex
   ``-c mcp_servers…``, Pi ``-e <extension>``, OpenCode ACP ``mcpServers``
   (the token on stdin, never its env) — and NOTHING without a ticked box.
   The token is live: a box turned off is refused on the next call; the
   process ending revokes it.

Every test replays RECORDED harness output through the fake harness (real
plumbing, routes and store); nothing calls a model.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

from iron_jarvis.core.events import EventType
from iron_jarvis.daemon.app import create_app
from iron_jarvis.hosted import jarvis_tools as JT
from iron_jarvis.hosted import news as NEWS
from iron_jarvis.hosted import service as S
from iron_jarvis.hosted import store
from tests._build_app_helpers_v1409 import FAKE, client, frames, new_session, sent, starts, stop_all, until, use_fake

FIX_PI = Path(__file__).parent / "fixtures" / "build_app_v1411"
FIX_OC = Path(__file__).parent / "fixtures" / "build_app_v1413"
ACCEPT = "application/json, text/event-stream"
#: The model the OpenCode recordings were made on (they expect it set).
OC_MODEL = "opencode/glm-5.3-flash-free"


@pytest.fixture()
def work(tmp_path) -> Path:
    folder = tmp_path / "work"
    folder.mkdir()
    return folder


@pytest.fixture()
def app(tmp_path):
    return create_app(str(tmp_path / "home"))


def _fake(monkeypatch, fixture_dir: Path, scenario: str, log: Path) -> None:
    monkeypatch.setattr(S, "binary_for", lambda harness: [sys.executable, str(FAKE)])
    monkeypatch.setenv("IJ_FAKE_SCENARIO", str(fixture_dir / f"{scenario}.json"))
    monkeypatch.setenv("IJ_FAKE_LOG", str(log))


def _events(app) -> list:
    got: list = []
    app.state.platform.event_bus.add_handler(got.append)
    return got


class _Clock:
    """The service's clock, moved by the test (never a sleep)."""

    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


# --------------------------------------------------------------------------- #
# 1. running work is seen everywhere
# --------------------------------------------------------------------------- #
async def test_a_running_reply_is_listed_counted_and_keeps_the_pc_awake(app, work, tmp_path, monkeypatch):
    use_fake(monkeypatch, "claude_interrupt", tmp_path / "fake.log")
    svc = S.service_for(app.state.platform)
    async with client(app) as c:
        s = await new_session(c, work)
        sid = s["id"]
        turn = asyncio.ensure_future(c.post(f"/build/app/sessions/{sid}/turn",
                                            json={"text": "fix the nav bar", "turn_id": "t"}))
        await until(lambda: svc.turn("t") and any(e["type"] == "text" for e in svc.turn("t").events))
        rows = (await c.get("/chat/turns/running")).json()["replies"]
        mine = [r for r in rows if r["thread_id"] == sid]
        assert len(mine) == 1, rows
        assert mine[0]["surface"] == "build_app" and mine[0]["harness"] == "claude-code"
        assert mine[0]["waiting"] is False and mine[0]["turn_id"] == "t"
        assert mine[0]["title"] == "fix the nav bar"
        health = (await c.get("/health")).json()
        assert health["replies"]["build_app"] == 1
        act = (await c.get("/system/activity")).json()
        assert act["code_replies"] == 1 and act["code_tools"] == ["Claude Code"]
        assert act["busy"] is True and act["working"] is True
        await c.post(f"/build/app/sessions/{sid}/interrupt")
        await asyncio.wait_for(turn, 30)
        act = (await c.get("/system/activity")).json()
        assert act["code_replies"] == 0 and act["busy"] is False
        assert (await c.get("/health")).json()["replies"]["build_app"] == 0
    await stop_all(app)


async def test_a_reply_parked_on_a_card_is_busy_but_not_working(app, work, tmp_path, monkeypatch):
    use_fake(monkeypatch, "claude_edit", tmp_path / "fake.log")
    svc = S.service_for(app.state.platform)
    svc.phone_after_s = 3600.0
    async with client(app) as c:
        sid = (await new_session(c, work))["id"]
        turn = asyncio.ensure_future(c.post(f"/build/app/sessions/{sid}/turn", json={"text": "edit it", "turn_id": "t"}))
        ask = await until(lambda: next(iter(svc.live[sid].asks), None) if svc.live.get(sid) else None)
        rows = [r for r in (await c.get("/chat/turns/running")).json()["replies"] if r["thread_id"] == sid]
        assert rows and rows[0]["waiting"] is True
        act = (await c.get("/system/activity")).json()
        assert act["busy"] is True and act["code_replies"] == 1
        assert act["working"] is False, "a reply parked on the user must let the PC sleep"
        await c.post(f"/build/app/sessions/{sid}/answer", json={"request_id": ask, "decision": "allow"})
        await asyncio.wait_for(turn, 30)
    await stop_all(app)


async def test_the_status_line_and_the_map_count_a_build_code_reply(app, work, tmp_path, monkeypatch):
    use_fake(monkeypatch, "claude_interrupt", tmp_path / "fake.log")
    svc = S.service_for(app.state.platform)
    from iron_jarvis.daemon.routes.ui import build_replies_running

    class _D:
        platform = app.state.platform

    async with client(app) as c:
        sid = (await new_session(c, work))["id"]
        turn = asyncio.ensure_future(c.post(f"/build/app/sessions/{sid}/turn", json={"text": "go", "turn_id": "t"}))
        await until(lambda: svc.turn("t") and any(e["type"] == "text" for e in svc.turn("t").events))
        assert build_replies_running(_D()) == 1
        await c.post(f"/build/app/sessions/{sid}/interrupt")
        await asyncio.wait_for(turn, 30)
    await stop_all(app)


async def _finished_run(app, work, tmp_path, monkeypatch, *, seconds: float, interrupt: bool = False):
    use_fake(monkeypatch, "claude_interrupt" if interrupt else "claude_edit", tmp_path / "fake.log")
    svc = S.service_for(app.state.platform)
    svc.phone_after_s = 3600.0
    clock = _Clock()
    svc.clock = clock
    got = _events(app)
    async with client(app) as c:
        sid = (await new_session(c, work))["id"]
        turn = asyncio.ensure_future(c.post(f"/build/app/sessions/{sid}/turn", json={"text": "Fix the nav bar", "turn_id": "t"}))
        if interrupt:
            await until(lambda: svc.turn("t") and any(e["type"] == "text" for e in svc.turn("t").events))
            clock.t += seconds
            await c.post(f"/build/app/sessions/{sid}/interrupt")
        else:
            ask = await until(lambda: next(iter(svc.live[sid].asks), None) if svc.live.get(sid) else None)
            clock.t += seconds
            await c.post(f"/build/app/sessions/{sid}/answer", json={"request_id": ask, "decision": "allow"})
        evs = frames((await asyncio.wait_for(turn, 30)).text)
        await asyncio.sleep(0.3)  # the notice is published after the done
    await stop_all(app)
    finished = [e for e in got if getattr(e, "type", "") == EventType.BUILD_TURN_FINISHED]
    return sid, evs, finished


async def test_a_long_reply_that_ended_is_build_turn_finished_for_build_code(app, work, tmp_path, monkeypatch):
    sid, evs, finished = await _finished_run(app, work, tmp_path, monkeypatch, seconds=150)
    assert evs[-1]["type"] == "done" and evs[-1]["stop"] == "completed"
    assert len(finished) == 1, finished
    ev = finished[0]
    assert ev.session_id == f"build_app:{sid}"
    assert ev.payload["thread_id"] == sid and ev.payload["surface"] == "build_app"
    assert ev.payload["outcome"] == "done" and ev.payload["seconds"] == 150
    assert ev.payload["title"] == "Fix the nav bar" and ev.payload["harness"] == "claude-code"


async def test_a_short_reply_is_quiet(app, work, tmp_path, monkeypatch):
    _sid, _evs, finished = await _finished_run(app, work, tmp_path, monkeypatch, seconds=30)
    assert finished == []


async def test_a_stopped_reply_is_never_announced(app, work, tmp_path, monkeypatch):
    _sid, evs, finished = await _finished_run(app, work, tmp_path, monkeypatch, seconds=600, interrupt=True)
    assert evs[-1]["stop"] == "interrupted"
    assert finished == []


def test_the_notifier_rings_the_bell_for_it_and_keeps_this_pc_quiet():
    from iron_jarvis.comm.notifier import bell_attention, bell_pings

    ev = {"type": EventType.BUILD_TURN_FINISHED, "session_id": "build_app:app_x",
          "payload": {"thread_id": "app_x", "surface": "build_app", "outcome": "done", "seconds": 90}}
    assert bell_pings(ev) is True and bell_attention(ev) is True


# --------------------------------------------------------------------------- #
# the phone
# --------------------------------------------------------------------------- #
async def test_an_unanswered_card_goes_to_the_phone_and_its_approve_answers_once(app, work, tmp_path, monkeypatch):
    use_fake(monkeypatch, "claude_edit", tmp_path / "fake.log")
    svc = S.service_for(app.state.platform)
    svc.phone_after_s = 0.05
    got = _events(app)
    approvals = app.state.platform.approvals
    async with client(app) as c:
        sid = (await new_session(c, work))["id"]
        turn = asyncio.ensure_future(c.post(f"/build/app/sessions/{sid}/turn", json={"text": "Fix the nav bar", "turn_id": "t"}))
        ask = await until(lambda: next(iter(svc.live[sid].asks), None) if svc.live.get(sid) else None)
        req = await until(lambda: next((e for e in got if getattr(e, "type", "") == EventType.APPROVAL_REQUESTED), None))
        p = req.payload
        assert p["surface"] == "build" and p["thread_id"] == sid and p["title"] == "Fix the nav bar"
        assert p["tool"] and "args" not in p, "names only on the phone's event"
        assert p["approval_id"] in approvals.pending_ids()
        # The pending listing keeps ONE row (the session's own waiting row).
        listed = (await c.get("/chat/approvals/pending")).json()["approvals"]
        assert [r for r in listed if r.get("id") == p["approval_id"]] == []
        from iron_jarvis.core.approvals import resolve_as

        assert resolve_as(approvals, p["approval_id"], "once", actor="phone")
        evs = frames((await asyncio.wait_for(turn, 30)).text)
    await stop_all(app)
    resolved = [e for e in evs if e["type"] == "resolved" and e["request_id"] == ask]
    assert [e["decision"] for e in resolved] == ["allow"], "a phone answer is ONCE, never for the session"
    assert evs[-1]["stop"] == "completed"
    assert p["approval_id"] not in approvals.pending_ids()
    closed = [e for e in got if getattr(e, "type", "") == EventType.APPROVAL_RESOLVED]
    assert closed and closed[-1].payload["approval_id"] == p["approval_id"]


async def test_a_card_answered_in_the_app_first_never_reaches_the_phone(app, work, tmp_path, monkeypatch):
    use_fake(monkeypatch, "claude_edit", tmp_path / "fake.log")
    svc = S.service_for(app.state.platform)
    svc.phone_after_s = 0.4
    got = _events(app)
    async with client(app) as c:
        sid = (await new_session(c, work))["id"]
        turn = asyncio.ensure_future(c.post(f"/build/app/sessions/{sid}/turn", json={"text": "edit", "turn_id": "t"}))
        ask = await until(lambda: next(iter(svc.live[sid].asks), None) if svc.live.get(sid) else None)
        await c.post(f"/build/app/sessions/{sid}/answer", json={"request_id": ask, "decision": "allow"})
        await asyncio.wait_for(turn, 30)
        await asyncio.sleep(0.8)
    await stop_all(app)
    assert [e for e in got if getattr(e, "type", "") == EventType.APPROVAL_REQUESTED] == []
    assert app.state.platform.approvals.pending_ids() == []


def test_the_phone_never_gets_a_question_a_once_only_card_or_a_full_access_card():
    from iron_jarvis.hosted.driver import Ask

    approval = Ask("a1", "approval", "Edit", event={"type": "approval"})
    assert NEWS.phone_may_send(approval, "ask") is True
    assert NEWS.phone_may_send(approval, "full") is False
    assert NEWS.phone_may_send(Ask("a2", "question", "AskUserQuestion", event={}), "ask") is False
    once = Ask("a3", "approval", "shell", event={"type": "approval", "once_only": True})
    assert NEWS.phone_may_send(once, "ask") is False
    priced = Ask("a4", "approval", "x", event={"type": "approval", "cost_note": "$1"})
    assert NEWS.phone_may_send(priced, "ask") is False


# --------------------------------------------------------------------------- #
# 2. Iron Jarvis's own tools, with consent
# --------------------------------------------------------------------------- #
async def _say(c, sid, turn_id="t1", text="ready?"):
    r = await c.post(f"/build/app/sessions/{sid}/turn", json={"text": text, "turn_id": turn_id})
    return frames(r.text)


async def test_a_new_session_has_every_box_off_and_gets_no_connection(app, work, tmp_path, monkeypatch):
    log = tmp_path / "fake.log"
    use_fake(monkeypatch, "claude_modes", log)
    async with client(app) as c:
        s = await new_session(c, work)
        assert s["capabilities"] == {k: False for k in ("files", "shell", "browser", "extensions", "memory")}
        await _say(c, s["id"])
    start = starts(log)[0]
    assert "--mcp-config" not in start["argv"]
    assert "IRONJARVIS_MCP_TOKEN" not in start["env"] and "IRONJARVIS_MCP_URL" not in start["env"]
    assert not (Path(app.state.platform.config.home) / "hosted" / s["id"] / JT.CLAUDE_FILE).exists()
    await stop_all(app)


async def test_claude_code_gets_its_own_mcp_config_with_a_token_reference(app, work, tmp_path, monkeypatch):
    log = tmp_path / "fake.log"
    use_fake(monkeypatch, "claude_modes", log)
    async with client(app) as c:
        sid = (await new_session(c, work))["id"]
        r = await c.patch(f"/build/app/sessions/{sid}", json={"capabilities": {"memory": True}})
        assert r.status_code == 200, r.text
        assert r.json()["session"]["capabilities"]["memory"] is True
        assert r.json()["session"]["capabilities"]["browser"] is False
        svc = S.service_for(app.state.platform)
        await c.post(f"/build/app/sessions/{sid}/turn", json={"text": "ready?", "turn_id": "t1"})
        start = starts(log)[0]
        i = start["argv"].index("--mcp-config")
        cfg_path = Path(start["argv"][i + 1])
        assert cfg_path.parent == Path(app.state.platform.config.home) / "hosted" / sid
        assert "--strict-mcp-config" not in start["argv"], "the user's own MCP servers stay on"
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        server = cfg["mcpServers"][JT.SERVER_NAME]
        token = start["env"]["IRONJARVIS_MCP_TOKEN"]
        assert server["url"] == app.state.platform.terminals.mcp_url == start["env"]["IRONJARVIS_MCP_URL"]
        assert server["headers"]["Authorization"] == "Bearer ${IRONJARVIS_MCP_TOKEN}"
        assert token not in cfg_path.read_text(encoding="utf-8"), "no credential on disk"
        assert svc.tokens.resolve(token) is not None
        await svc._stop_process(svc.live[sid])
        assert not svc.tokens.store.has_pane(JT.grant_id(sid)), "the token is revoked with the process"
        assert svc.tokens.resolve(token) is None, "the token dies with the process"
        assert not cfg_path.exists()
    await stop_all(app)


async def test_codex_pi_and_opencode_get_the_connection_their_own_way(app, work, tmp_path, monkeypatch):
    home = Path(app.state.platform.config.home)
    async with client(app) as c:
        # Codex: -c overrides on the app-server's own command line.
        log = tmp_path / "codex.log"
        use_fake(monkeypatch, "codex_allow", log)
        sid = (await new_session(c, work, harness="codex"))["id"]
        await c.patch(f"/build/app/sessions/{sid}", json={"capabilities": {"browser": True}})
        asyncio.ensure_future(c.post(f"/build/app/sessions/{sid}/turn", json={"text": "go", "turn_id": "tc"}))
        await until(lambda: starts(log))
        argv = starts(log)[0]["argv"]
        assert f'mcp_servers.{JT.SERVER_NAME}.url="{app.state.platform.terminals.mcp_url}"' in argv
        assert f'mcp_servers.{JT.SERVER_NAME}.bearer_token_env_var="IRONJARVIS_MCP_TOKEN"' in argv
        assert starts(log)[0]["env"].get("IRONJARVIS_MCP_TOKEN")
        await S.service_for(app.state.platform)._stop_process(S.service_for(app.state.platform).live[sid])

        # Pi: an extension of our own, loaded for this run with -e.
        log = tmp_path / "pi.log"
        _fake(monkeypatch, FIX_PI, "pi_read", log)
        sid = (await new_session(c, work, harness="pi"))["id"]
        await c.patch(f"/build/app/sessions/{sid}", json={"capabilities": {"memory": True}})
        await _say(c, sid, "tp")
        argv = starts(log)[0]["argv"]
        ext = Path(argv[argv.index("-e") + 1])
        assert ext == home / "hosted" / sid / JT.PI_FILE
        text = ext.read_text(encoding="utf-8")
        assert "registerMcpServer" in text and "${IRONJARVIS_MCP_TOKEN}" in text
        assert starts(log)[0]["env"]["IRONJARVIS_MCP_TOKEN"] not in text

        # OpenCode: ACP mcpServers on the process's stdin, never its env.
        log = tmp_path / "oc.log"
        _fake(monkeypatch, FIX_OC, "oc_read", log)
        sid = (await new_session(c, work, harness="opencode", mode="ask", model=OC_MODEL))["id"]
        await c.patch(f"/build/app/sessions/{sid}", json={"capabilities": {"memory": True}})
        await _say(c, sid, "to")
        assert "IRONJARVIS_MCP_TOKEN" not in starts(log)[0]["env"]
        new = next(m for m in sent(log) if m.get("method") == "session/new")
        servers = new["params"]["mcpServers"]
        assert len(servers) == 1 and servers[0]["type"] == "http" and servers[0]["name"] == JT.SERVER_NAME
        auth = servers[0]["headers"][0]
        assert auth["name"] == "Authorization" and auth["value"].startswith("Bearer ")
        assert S.service_for(app.state.platform).tokens.resolve(auth["value"][len("Bearer "):]) is not None
    await stop_all(app)


async def test_an_opencode_session_with_nothing_ticked_sends_no_server(app, work, tmp_path, monkeypatch):
    log = tmp_path / "oc.log"
    _fake(monkeypatch, FIX_OC, "oc_read", log)
    async with client(app) as c:
        sid = (await new_session(c, work, harness="opencode", mode="ask", model=OC_MODEL))["id"]
        await _say(c, sid)
    new = next(m for m in sent(log) if m.get("method") == "session/new")
    assert new["params"]["mcpServers"] == []
    await stop_all(app)


async def test_the_session_token_is_served_by_mcp_and_read_live(app, work, tmp_path, monkeypatch):
    """The token reaches /mcp through the SAME door as a terminal's: Memory's
    read-only tools (the memory profile's included) are listed; turning
    Memory OFF is refused on the very next call; a stopped process is 401."""
    from fastapi.testclient import TestClient

    from iron_jarvis.mcpserver.server import MEMORY_READ_TOOLS
    from iron_jarvis.mcpserver.session import SESSION_HEADER

    log = tmp_path / "fake.log"
    use_fake(monkeypatch, "claude_interrupt", log)
    svc = S.service_for(app.state.platform)
    async with client(app) as c:
        sid = (await new_session(c, work))["id"]
        await c.patch(f"/build/app/sessions/{sid}", json={"capabilities": {"memory": True}})
        turn = asyncio.ensure_future(c.post(f"/build/app/sessions/{sid}/turn", json={"text": "go", "turn_id": "t"}))
        await until(lambda: svc.turn("t") and any(e["type"] == "text" for e in svc.turn("t").events))
        token = starts(log)[0]["env"]["IRONJARVIS_MCP_TOKEN"]
        headers = {"Accept": ACCEPT, "Authorization": f"Bearer {token}"}

        async def rpc(method, params=None, session=""):
            h = dict(headers)
            if session:
                h[SESSION_HEADER] = session
            return await c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
                                headers=h)

        init = await rpc("initialize", {"protocolVersion": "2025-03-26", "clientInfo": {"name": "t", "version": "1"}})
        assert init.status_code == 200, init.text
        mcp_session = init.headers[SESSION_HEADER]
        assert f"Build › Code session {sid}" in init.json()["result"]["instructions"]
        listed = await rpc("tools/list", session=mcp_session)
        names = sorted(t["name"] for t in listed.json()["result"]["tools"])
        assert names == sorted(n for n in MEMORY_READ_TOOLS if n in app.state.platform.registry.names())
        assert {"brain_search", "brain_read"} <= set(names)
        await c.patch(f"/build/app/sessions/{sid}", json={"capabilities": {"memory": False}})
        again = await rpc("tools/list", session=mcp_session)
        assert again.status_code == 403, again.text
        await c.post(f"/build/app/sessions/{sid}/interrupt")
        await asyncio.wait_for(turn, 30)
        await svc._stop_process(svc.live[sid])
        gone = await rpc("tools/list", session=mcp_session)
        assert gone.status_code == 401, gone.text
    await stop_all(app)


async def test_turning_the_first_box_on_restarts_before_the_next_message(app, work, tmp_path, monkeypatch):
    log = tmp_path / "fake.log"
    use_fake(monkeypatch, "claude_modes", log)
    async with client(app) as c:
        sid = (await new_session(c, work))["id"]
        await _say(c, sid, "t1")
        svc = S.service_for(app.state.platform)
        assert svc.live[sid].running
        r = await c.patch(f"/build/app/sessions/{sid}", json={"capabilities": {"memory": True}})
        assert r.json()["applied"]["capabilities"] == "next"
        assert svc.live[sid].restart is True
        # Switching between two SERVED boxes keeps the connection: no restart.
        svc.live[sid].restart = False
        r = await c.patch(f"/build/app/sessions/{sid}", json={"capabilities": {"browser": True}})
        assert r.json()["applied"]["capabilities"] == "now" and svc.live[sid].restart is False
    await stop_all(app)


def test_capabilities_merge_and_ignore_unknown_keys():
    caps = JT.normalise({"memory": "false", "browser": True, "bogus": True})
    assert caps["memory"] is False and caps["browser"] is True and "bogus" not in caps
    assert JT.wanted({"files": True, "shell": True}) is False, "only served boxes need a connection"
    assert JT.wanted({"memory": True}) is True


def test_the_grant_prefix_never_collides_with_a_terminal():
    assert JT.session_of("code:app_1") == "app_1"
    assert JT.session_of("term_abc") == ""


def test_memory_output_crosses_mcp_inside_the_untrusted_fence(tmp_path, monkeypatch):
    """What a memory tool returns from STORED text (memory, the memory
    profile's base) reaches a coding tool's model inside the same fence the
    chat lanes use; a tool that does not return such text keeps its shape."""
    from tests.test_mcp_memory_capability_v1290 import _app

    a = _app(tmp_path, monkeypatch, {"memory": True})
    a.platform.memory.write("user", "fav", "The user's favourite colour is teal.")
    mcp_session = a.initialize()
    found = a.call(mcp_session, "memory_search", {"query": "favourite colour", "k": 3}).json()["result"]
    text = found["content"][0]["text"]
    assert found["isError"] is False
    assert text.startswith("[UNTRUSTED CONTENT") and "teal" in text
    read = a.call(mcp_session, "memory_read", {"layer": "user", "key": "fav"}).json()["result"]
    assert read["content"][0]["text"] == "The user's favourite colour is teal."


def test_the_memory_capability_serves_the_memory_profile_read_only():
    from iron_jarvis.mcpserver.server import MEMORY_READ_TOOLS

    assert {"brain_search", "brain_read"} <= MEMORY_READ_TOOLS
    assert not ({"brain_save", "brain_change"} & MEMORY_READ_TOOLS), "never a write tool"
