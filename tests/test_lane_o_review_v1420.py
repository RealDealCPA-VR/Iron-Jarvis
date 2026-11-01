"""Independent review of lane O (v1.420.0: C6.3, C6.5, C6.8, C6.9, C6.10).

RED on the lane's first cut:

* a remote's SYNCHRONOUS answer reached the mission card as ~25 characters:
  the delegation event cut the FENCED text to 240 chars, and the untrusted
  fence's own header is ~210 of them, so ``display_unfenced`` had almost
  nothing left to show (``publish_delegation_completed`` now cuts the body,
  never the wrapper; the model's tool result keeps the fence whole);
* a phone chat turn that crashed told the phone to "open the run to see the
  details" — a chat turn has no run (the v1.388.0 rule: with no run, never
  "open the run").

The rest pin what the review judged fine, so it stays fine.
"""

from __future__ import annotations

import asyncio
import json
import subprocess

import httpx
from fastapi.testclient import TestClient

from iron_jarvis.agents.delegate_tool import DelegateTool
from iron_jarvis.agents.mission import mission_view
from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.core.models import AgentType
from iron_jarvis.daemon.app import create_app
from iron_jarvis.platform import build_platform
from iron_jarvis.providers import cli_detect
from iron_jarvis.tools.base import ToolContext
from tests.test_comm_full_chat import CHAT_CFG, ChatMockChannel, _chat_poller, _msg


class _Resp:
    def __init__(self, status: int, payload=None):
        self.status_code = status
        self._payload = payload
        self.text = json.dumps(payload) if payload is not None else ""

    def json(self):
        if self._payload is None:
            raise ValueError("no body")
        return self._payload


def _ctx(p, sid: str) -> ToolContext:
    return ToolContext(
        workspace=p.config.workspaces_dir, session_id=sid, agent_run_id="root-run",
        config=p.config, event_bus=p.event_bus, engine=p.engine,
    )


REPORT = (
    "Revenue rose four percent in the third quarter, driven by the new wholesale "
    "accounts in Ohio and Indiana; margins held steady at thirty-one percent, and "
    "the two late filings were sent on Tuesday with no penalty assessed."
)


def test_a_synchronous_remote_answer_shows_its_report_not_a_sliver(tmp_path, monkeypatch):
    app = create_app(str(tmp_path))
    p = app.state.platform

    async def post(self, url, json=None, headers=None, **kw):
        return _Resp(200, {"result": REPORT})

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    with TestClient(app) as client:
        r = client.post(
            "/agents/remote",
            json={"name": "hermes", "base_url": "http://192.168.1.50:8080/run",
                  "kind": "http-task", "token": "t"},
        )
        assert r.status_code == 200, r.text

        async def drive():
            root = await Orchestrator(p).create_session(
                "objective", AgentType.SUPERVISOR, provider="mock", origin="job:mission"
            )
            res = await DelegateTool(p).execute(
                {"agent_type": "remote:hermes", "task": "summarise"}, _ctx(p, root.id)
            )
            return root.id, res

        root_id, res = asyncio.run(drive())
    # the MODEL's copy is still fenced, whole
    assert res.output.startswith("[UNTRUSTED CONTENT") and REPORT in res.output
    card = [m for m in mission_view(p, root_id)["members"] if m["kind"] == "remote"][0]
    assert card["status"] == "done"
    # the card reads (most of) the report — not the sliver the fence left
    assert card["result"].startswith("Revenue rose four percent"), card["result"]
    assert len(card["result"]) >= 200, card["result"]
    assert "UNTRUSTED" not in card["result"]


async def test_a_phone_chat_turn_crash_never_points_at_a_run(tmp_path):
    platform = build_platform(str(tmp_path / "home"))

    async def failing(platform_, personas, body):
        raise RuntimeError("boom")

    ch = ChatMockChannel(dict(CHAT_CFG))
    poller, _orch, store = _chat_poller(platform, ch, failing)
    res = await poller._handle("tg", ch, _msg("hello"))
    assert res["status"] == "chat_error"
    line = store.history_body(res["thread_id"])[-1]["content"]
    assert line.startswith("I hit a problem: ")
    assert "open the run" not in line, line
    assert "open the run" not in ch.sent[-1]


def test_the_grok_row_reads_a_file_and_never_starts_a_process(tmp_path, monkeypatch):
    home = tmp_path / "grok"
    home.mkdir()
    (home / "auth.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("IRONJARVIS_GROK_HOME", str(home))
    started: list = []

    def no_spawn(*a, **kw):
        started.append(a)
        raise AssertionError("a process was started")

    monkeypatch.setattr(subprocess, "Popen", no_spawn)
    monkeypatch.setattr(subprocess, "run", no_spawn)
    cli_detect._grok_status_memo.clear()
    monkeypatch.setattr(cli_detect, "_grok_binary", lambda: str(home / "grok.exe"))
    try:
        out = cli_detect.grok_login_status()
    finally:
        cli_detect._grok_status_memo.clear()
    assert started == []
    assert out["installed"] is True and out["signed_in"] is False


def test_a_fast_report_that_lands_before_the_tool_returns_still_ends_the_card(tmp_path, monkeypatch):
    """The remote posts its ``done`` line into the room BEFORE its ``202``
    reaches the tool (a fast remote): the card is done, once."""
    app = create_app(str(tmp_path))
    p = app.state.platform
    client = TestClient(app)
    holder: dict = {}

    async def post(self, url, json=None, headers=None, **kw):
        room = (json or {}).get("conversation_id")
        if room:  # report home first, then answer 202
            r = await asyncio.to_thread(
                client.post,
                "/agents/remote/hermes/inbound",
                json={"conversation_id": room, "message": "All done.", "kind": "done"},
                headers={"Authorization": f"Bearer {holder['token']}"},
            )
            assert r.status_code == 200, r.text
        return _Resp(202)

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    with client:
        r = client.post(
            "/agents/remote",
            json={"name": "hermes", "base_url": "http://192.168.1.50:8080/run",
                  "kind": "http-task", "token": "t"},
        )
        assert r.status_code == 200, r.text
        holder["token"] = client.post("/agents/remote/hermes/inbound/enable", json={}).json()["token"]

        async def drive():
            root = await Orchestrator(p).create_session(
                "objective", AgentType.SUPERVISOR, provider="mock", origin="job:mission"
            )
            await DelegateTool(p).execute(
                {"agent_type": "remote:hermes", "task": "go"}, _ctx(p, root.id)
            )
            return root.id

        root_id = asyncio.run(drive())
        view = mission_view(p, root_id)
    card = [m for m in view["members"] if m["kind"] == "remote"][0]
    assert card["status"] == "done" and card["result"] == "All done."
    reported = [a for a in view["activity"] if a["text"] == "hermes reported back to Jarvis"]
    assert len(reported) == 1
