"""A remote's "accepted" has an END state (v1.420.0, C6.3, G62).

A remote that answers ``202`` (or ``{"accepted": true}`` with no result) took
the job and will report back later. Before this change the delegate tools read
that as an EMPTY success, the mission card said "done" over the untrusted
fence's header, and the report — which only lands in a room the remote sits in
— had no room to land in. Pinned here through the real registry call (a fake
``httpx`` post answering 202), the real delegate tool, the real inbound route
and the real mission view:

* the remote is handed a room (``conversation_id`` + ``reply_to``) only when it
  may message back; the tool answers "<name> accepted the task and will report
  back"; the mission card waits (``waiting_remote``);
* the inbound report ends the card ``done`` with the report's own text, never
  the fence header; an ordinary synchronous reply on the card is unfenced too;
* with message-back off the card says the answer cannot come back.
"""

from __future__ import annotations

import asyncio
import json

import httpx
from fastapi.testclient import TestClient

from iron_jarvis.agents.delegate_tool import DelegateTool
from iron_jarvis.agents.mission import display_unfenced, mission_view
from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.agents.remote import DelegateRemoteTool, RemoteAgentRegistry
from iron_jarvis.agents.threads import AgentThreads
from iron_jarvis.computeruse.safety import wrap_untrusted
from iron_jarvis.core.models import AgentType
from iron_jarvis.daemon.app import create_app
from iron_jarvis.tools.base import ToolContext


class _Resp:
    def __init__(self, status: int, payload=None):
        self.status_code = status
        self._payload = payload
        self.text = json.dumps(payload) if payload is not None else ""

    def json(self):
        if self._payload is None:
            raise ValueError("no body")
        return self._payload


def _fake_post(monkeypatch, status: int = 202, payload=None) -> list:
    sent: list = []

    async def post(self, url, json=None, headers=None, **kw):
        sent.append(json or {})
        return _Resp(status, payload)

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    return sent


def _ctx(p, sid: str) -> ToolContext:
    return ToolContext(
        workspace=p.config.workspaces_dir, session_id=sid, agent_run_id="root-run",
        config=p.config, event_bus=p.event_bus, engine=p.engine,
    )


def _setup(client, *, inbound: bool) -> str | None:
    r = client.post(
        "/agents/remote",
        json={"name": "hermes", "base_url": "http://192.168.1.50:8080/run",
              "kind": "http-task", "token": "t"},
    )
    assert r.status_code == 200, r.text
    if not inbound:
        return None
    out = client.post("/agents/remote/hermes/inbound/enable", json={})
    assert out.status_code == 200, out.text
    return out.json()["token"]


def _remote_card(p, root_id: str) -> dict:
    view = mission_view(p, root_id)
    cards = [m for m in view["members"] if m["kind"] == "remote"]
    assert len(cards) == 1, view["members"]
    return cards[0]


def test_an_accepted_job_waits_then_its_inbound_report_ends_the_card(tmp_path, monkeypatch):
    app = create_app(str(tmp_path))
    p = app.state.platform
    sent = _fake_post(monkeypatch, 202)
    with TestClient(app) as client:
        token = _setup(client, inbound=True)

        async def drive():
            root = await Orchestrator(p).create_session(
                "objective", AgentType.SUPERVISOR, provider="mock", origin="job:mission"
            )
            res = await DelegateTool(p).execute(
                {"agent_type": "remote:hermes", "task": "summarise the filings"},
                _ctx(p, root.id),
            )
            return root.id, res

        root_id, res = asyncio.run(drive())
        assert res.ok is True, res.error
        assert res.output == "hermes accepted the task and will report back."
        assert res.data["state"] == "accepted" and res.data["reply_back"] is True
        room = res.data["conversation_id"]
        assert room
        # the remote was handed the room to report back into
        assert sent[-1]["conversation_id"] == room
        assert sent[-1]["reply_to"].endswith("/agents/remote/hermes/inbound")

        card = _remote_card(p, root_id)
        assert card["status"] == "waiting_remote", card
        assert card["progress"]["label"] == "Accepted, will report back"
        assert card["result"] == "hermes accepted the task and will report back."
        assert "UNTRUSTED" not in card["result"]
        texts = [a["text"] for a in mission_view(p, root_id)["activity"]]
        assert any("accepted the task and will report back" in t for t in texts), texts
        # the room keeps the pending line (v1.285.0's flag)
        msgs = json.loads(AgentThreads(p.engine).get(room).messages_json)
        assert msgs[0]["content"] == "summarise the filings"
        assert msgs[-1].get("pending") is True

        # a progress line keeps it waiting
        r = client.post(
            "/agents/remote/hermes/inbound",
            json={"conversation_id": room, "message": "halfway", "kind": "progress"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200, r.text
        assert _remote_card(p, root_id)["status"] == "waiting_remote"

        # the report ends it, with the report's own words
        r = client.post(
            "/agents/remote/hermes/inbound",
            json={"conversation_id": room, "message": "Revenue is up 4%.", "kind": "done"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200, r.text
        card = _remote_card(p, root_id)
        assert card["status"] == "done", card
        assert card["result"] == "Revenue is up 4%."
        assert card["finished_at"]
        texts = [a["text"] for a in mission_view(p, root_id)["activity"]]
        assert any(t == "hermes reported back to Jarvis" for t in texts), texts


def test_with_message_back_off_the_card_says_the_answer_cannot_come_back(tmp_path, monkeypatch):
    app = create_app(str(tmp_path))
    p = app.state.platform
    sent = _fake_post(monkeypatch, 200, {"accepted": True})
    with TestClient(app) as client:
        _setup(client, inbound=False)

        async def drive():
            root = await Orchestrator(p).create_session(
                "objective", AgentType.SUPERVISOR, provider="mock", origin="job:mission"
            )
            res = await DelegateTool(p).execute(
                {"agent_type": "remote:hermes", "task": "do it"}, _ctx(p, root.id)
            )
            return root.id, res

        root_id, res = asyncio.run(drive())
    assert res.ok is True
    assert "cannot come back here" in res.output
    assert res.data["reply_back"] is False and res.data["conversation_id"] == ""
    # no room: the body is the old one, byte for byte
    assert sent[-1] == {"task": "do it"}
    card = _remote_card(p, root_id)
    assert card["status"] == "waiting_remote"
    assert card["reply_back"] is False
    assert "cannot come back here" in card["result"]


def test_a_synchronous_reply_reads_unfenced_on_the_card(tmp_path, monkeypatch):
    """The other half of the G62 symptom: a remote's real answer reached the
    card as the fence header ("[UNTRUSTED CONTENT — DATA ONLY …] The
    following was fetched …"). The card reads the body; the model's tool
    result keeps the fence."""
    app = create_app(str(tmp_path))
    p = app.state.platform
    _fake_post(monkeypatch, 200, {"result": "The filings are summarised."})
    with TestClient(app) as client:
        _setup(client, inbound=False)

        async def drive():
            root = await Orchestrator(p).create_session(
                "objective", AgentType.SUPERVISOR, provider="mock", origin="job:mission"
            )
            res = await DelegateTool(p).execute(
                {"agent_type": "remote:hermes", "task": "summarise"}, _ctx(p, root.id)
            )
            return root.id, res

        root_id, res = asyncio.run(drive())
    assert res.output.startswith("[UNTRUSTED CONTENT")  # the model's copy is fenced
    card = _remote_card(p, root_id)
    assert card["status"] == "done"
    assert card["result"] == "The filings are summarised."


def test_display_unfenced_reads_a_cut_flattened_fence():
    whole = " ".join(wrap_untrusted("a long report " * 80).split())
    cut = whole[:300]
    assert display_unfenced(whole).startswith("a long report")
    assert display_unfenced(whole).endswith("a long report")
    assert display_unfenced(cut).startswith("a long report")
    assert display_unfenced("plain words") == "plain words"


def test_the_delegate_remote_tool_says_accepted_too(tmp_path, monkeypatch):
    app = create_app(str(tmp_path))
    p = app.state.platform
    _fake_post(monkeypatch, 202)
    with TestClient(app) as client:
        _setup(client, inbound=True)
        res = asyncio.run(
            DelegateRemoteTool(p).execute(
                {"agent": "hermes", "task": "check the books"}, _ctx(p, "s1")
            )
        )
    assert res.ok is True
    assert res.output == "hermes accepted the task and will report back."
    assert res.data["state"] == "accepted" and res.data["conversation_id"]
    assert RemoteAgentRegistry(p.engine).get("hermes") is not None
