"""v1.447.0 — OPT-IN: the REAL Antigravity (``agy``) through the real Code-view
service: one of the user's own agents, Iron Jarvis's own tools and its own MCP
list. Skipped unless ``IJ_LIVE_AGY`` names the binary.

The same safety as the v1.439.0 live test (whose ``live`` fixture this uses):
a scratch ``USERPROFILE``/``HOME``, a saved ``gemini-api-key`` method and a
fake key, every model call to a LOCAL stand-in Gemini API. Iron Jarvis's MCP
address is pointed at a LOCAL stand-in MCP server (``mcp_url``), so what agy
does with the session's token is recorded and nothing else is reached.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.hosted import antigravity_agents as AGA
from iron_jarvis.hosted import jarvis_tools as JT
from iron_jarvis.hosted import service as S
from tests._build_app_helpers_v1409 import client, frames, stop_all, until
from tests.test_build_app_antigravity_live_v1439 import AGY, live  # noqa: F401 — the fixture

pytestmark = pytest.mark.skipif(not AGY or not Path(AGY).is_file(), reason="IJ_LIVE_AGY names no agy binary")


class _Mcp(BaseHTTPRequestHandler):
    seen: list[dict] = []
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, code: int, body: bytes = b"", ctype: str = "application/json") -> None:
        self.send_response(code)
        if body:
            self.send_header("content-type", ctype)
        self.send_header("mcp-session-id", "s1")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_GET(self):  # the OAuth metadata look-up: the daemon answers 401 without its token
        _Mcp.seen.append({"m": "GET", "path": self.path})
        self._send(401, b'{"detail":"missing or invalid token"}')

    def do_DELETE(self):
        _Mcp.seen.append({"m": "DELETE"})
        self._send(200)

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("content-length") or 0)).decode("utf-8", "replace")
        msg = json.loads(body) if body else {}
        _Mcp.seen.append({"m": "POST", "method": msg.get("method"), "bearer": bool(self.headers.get("authorization"))})
        if "id" not in msg:
            return self._send(202)
        meth = msg.get("method")
        if meth == "initialize":
            res = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                   "serverInfo": {"name": "stand-in", "version": "1"}}
        elif meth == "tools/list":
            res = {"tools": [{"name": "memory_search", "description": "LIVETOOLSENTINEL",
                              "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}}}]}
        else:
            out = {"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32601, "message": "Method not found"}}
            return self._send(200, json.dumps(out).encode())
        self._send(200, json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": res}).encode())


async def _turn(c, sid, text):
    r = await asyncio.wait_for(c.post(f"/build/app/sessions/{sid}/turn", json={"text": text, "turn_id": text}), 120)
    assert r.status_code == 200, r.text
    return frames(r.text)


async def test_the_real_antigravity_runs_an_agent_takes_jarvis_tools_and_lists_its_mcp(live, monkeypatch):  # noqa: F811
    home = Path(os.environ["USERPROFILE"])
    agents = home / ".gemini" / "config" / "agents"
    agents.mkdir(parents=True)
    (agents / "tax-reviewer.md").write_text(
        "---\nname: tax-reviewer\ndescription: Reviews workpapers\nmainAgent: true\n---\n"
        "# System\nYou review workpapers. LIVEAGENTSENTINEL.\n", encoding="utf-8")
    # Its own MCP settings, with a key in the URL (never read by Iron Jarvis).
    subprocess.run([AGY, "mcp", "add", "--header", "Authorization: Bearer LIVEHDR", "ownapi",
                    "https://example.com/mcp?key=LIVEURLKEY"], env={**os.environ, "AGY_CLI_DISABLE_AUTO_UPDATE": "true"},
                   cwd=str(live.work), capture_output=True, timeout=60, check=True)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Mcp)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    _Mcp.seen = []
    monkeypatch.setattr(AGA, "AUTO_PROBE", True)
    saved = dict(JT._TAKES)
    app = create_app(str(live.root / "ij"))
    app.state.platform.terminals.mcp_url = f"http://127.0.0.1:{srv.server_address[1]}/mcp"
    svc = S.service_for(app.state.platform)
    try:
        async with client(app) as c:
            # The user's own agents are read in the background (agy agents).
            rows = (await c.get("/build/app/harnesses")).json()

            async def listed():
                if "tax-reviewer" in AGA.known_agents():
                    return True
                AGA._FAILED_AT.clear()  # a cold first start of agy may miss its bound: ask again
                await c.get("/build/app/harnesses")
                await asyncio.sleep(1)
                return False

            await until(listed, 180)
            r = await c.post("/build/app/sessions", json={"harness": "antigravity", "folder": str(live.work),
                                                          "mode": "agent:tax-reviewer"})
            assert r.status_code == 201, r.text
            sid = r.json()["id"]
            r = await c.patch(f"/build/app/sessions/{sid}", json={"capabilities": {"memory": True}})
            assert r.status_code == 200, r.text
            live.answers.write_text(json.dumps([[{"text": "live agent reply"}]]), encoding="utf-8")
            evs = await _turn(c, sid, "hello")
            assert evs[-1]["type"] == "done" and evs[-1]["stop"] == "completed", evs[-3:]
            req = live.req.read_text(encoding="utf-8")
            assert "LIVEAGENTSENTINEL" in req, "the agent's own prompt reached the model"
            assert "mcp_iron-jarvis-code_memory_search" in req and "LIVETOOLSENTINEL" in req
            assert JT.takes("antigravity") is True
            methods = [s.get("method") for s in _Mcp.seen if s["m"] == "POST"]
            assert "initialize" in methods and "tools/list" in methods
            assert all(s["bearer"] for s in _Mcp.seen if s["m"] == "POST")
            # Its own MCP list reached the Session tab: names and on/off only.
            over = await until(lambda: svc.live[sid].driver.reported_mcp, 30)
            assert over == [{"name": "ownapi", "state": "on"}]
            view = json.dumps((await c.get(f"/build/app/sessions/{sid}/overview")).json())
            assert "ownapi" in view and "LIVEURLKEY" not in view and "LIVEHDR" not in view
            # The conversation keeps its agent: a change is refused in words.
            r = await c.patch(f"/build/app/sessions/{sid}", json={"mode": "own"})
            assert r.status_code == 409 and "keeps the agent" in r.json()["detail"]
            assert rows  # (the harnesses answer itself)
    finally:
        JT._TAKES.clear()
        JT._TAKES.update(saved)
        srv.shutdown()
        await stop_all(app)


async def test_cards_questions_and_reopening_still_work_under_process_per_session(live, monkeypatch):  # noqa: F811
    """The v1.439.0 live walk (a command card, a question, a reopened
    conversation), with every start given ``--process-per-session`` — the
    flag a session with Iron Jarvis's tools now starts with."""
    from iron_jarvis.hosted import antigravity as A
    from tests.test_build_app_antigravity_live_v1439 import (
        test_the_real_antigravity_answers_asks_and_reopens_through_the_service as walk,
    )

    argvs: list[list[str]] = []
    orig = A.AntigravityDriver.argv

    def argv(self, **kw):
        out = orig(self, **kw)
        argvs.append(out)
        return out

    monkeypatch.setattr(A.AntigravityDriver, "per_session", lambda self: True)
    monkeypatch.setattr(A.AntigravityDriver, "argv", argv)
    await walk(live)
    assert argvs and all(A.PER_SESSION_FLAG in a for a in argvs)
