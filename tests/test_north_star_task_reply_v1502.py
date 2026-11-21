"""North-star 2.16 (v1.502.0): a paired computer's answer goes only to its stored address.

``agents/task_door.send_reply`` POSTs a task's answer — its words and the
files the run made, base64 — with a bearer key to the ``reply_to`` the task
named. ``valid_reply_to`` checks only the path's shape, so a task from a
paired computer could send this computer's answer (and files) to any address
on the network. Now a paired computer's answer goes to the address this
computer stored when the two were paired (its path kept from ``reply_to``),
whatever host ``reply_to`` names.

Pinned through the real code (two ``create_app`` factories, their middleware,
the real pairing, door and reply), with the network as the one stand-in — the
harness of ``tests/test_peers_v1497.py``.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from iron_jarvis.agents import task_door as td
from iron_jarvis.agents.remote import RemoteAgentRegistry
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import Session, SessionStatus
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.auth import TASK_DOOR_PATH
from iron_jarvis.peers import store as S
from iron_jarvis.providers.adapters.base import LLMResponse

INSTALL = "install-bearer-for-both-computers"
AUTH = {"Authorization": f"Bearer {INSTALL}"}
A_IP, B_IP, ELSEWHERE_IP = "10.0.0.1", "10.0.0.2", "10.0.0.66"
A_HOST, B_HOST, ELSEWHERE = f"{A_IP}:8787", f"{B_IP}:8787", f"{ELSEWHERE_IP}:8787"


class _Network(httpx.AsyncBaseTransport):
    def __init__(self, apps: dict[str, Any], senders: dict[str, str]) -> None:
        self.apps = {
            host: httpx.ASGITransport(app=app, client=(senders[host], 50000))
            for host, app in apps.items()
        }
        self.calls: list[dict[str, Any]] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        host = request.url.netloc.decode()
        self.calls.append({"method": request.method, "url": str(request.url),
                           "auth": request.headers.get("authorization", ""),
                           "body": request.content})
        transport = self.apps.get(host)
        if transport is None:
            raise httpx.ConnectError(f"no route to {host}", request=request)
        return await transport.handle_async_request(request)


@pytest.fixture()
def lan(monkeypatch):
    monkeypatch.setenv("IRONJARVIS_HOST_ALLOWLIST", f"{A_IP},{B_IP}")
    monkeypatch.setenv("IRONJARVIS_TOKEN", INSTALL)
    holder: dict[str, Any] = {}
    real_init = httpx.AsyncClient.__init__

    def _init(self, *a, **kw):
        kw.pop("transport", None)
        kw["transport"] = holder["net"]
        real_init(self, *a, **kw)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", _init)

    def make(apps: dict[str, Any]) -> _Network:
        hosts = list(apps)
        senders = {hosts[0]: hosts[1].split(":")[0], hosts[1]: hosts[0].split(":")[0]}
        holder["net"] = _Network(apps, senders)
        return holder["net"]

    return make


def _client(host: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=f"http://{host}", headers=AUTH)


async def _pair(ca, cb) -> None:
    shown = (await cb.post("/peers/code", json={"this_name": "desk"})).json()
    r = await ca.post("/peers/join", json={"address": B_HOST, "code": shown["code"]["code"],
                                            "name": "", "this_name": "laptop"})
    assert r.status_code == 200, r.text
    await _let_tasks(ca, cb)


async def _let_tasks(*clients) -> None:
    """Next leaps 41-a: a new pairing takes no tasks until its card's "Send
    me tasks" is pressed. These tests hand tasks both ways, so each side's
    card turns it on for every computer it lists (the real PATCH route)."""
    from iron_jarvis.daemon.routes import peers as _routes

    for client in clients:
        for row in (await client.get("/peers")).json()["peers"]:
            r = await client.patch(f"/peers/{row['name']}/allows", json={"may_send_tasks": True})
            assert r.status_code == 200, r.text
    await _routes.drain_tells()


def _remote_sessions(app) -> list[Session]:
    with session_scope(app.state.platform.engine) as db:
        rows = [s for s in db.exec(__import__("sqlmodel").select(Session))
                if str(s.origin or "").startswith("remote:")]
        for r in rows:
            db.expunge(r)
        return rows


async def _settled(app, timeout_s: float = 30.0) -> list[Session]:
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout_s
    running = app.state.orchestrator._running
    while True:
        rows = await asyncio.to_thread(_remote_sessions, app)
        done = [r for r in rows
                if r.status in (SessionStatus.COMPLETED, SessionStatus.FAILED, SessionStatus.CANCELLED)
                and (running.get(r.id) is None or running[r.id].done())]
        if done or loop.time() > end:
            break
        await asyncio.sleep(0.05)
    await td.drain_replies()
    return await asyncio.to_thread(_remote_sessions, app)


def _scripted(answer: str):
    async def fake_stream(**kw):
        yield {"type": "final", "response": LLMResponse(text=answer),
               "provider": "openai", "model": "gpt-5"}

    return fake_stream


async def test_a_paired_computers_answer_goes_to_its_stored_address_not_the_one_the_task_named(
    tmp_path, lan
):
    a = create_app(str(tmp_path / "laptop"))
    b = create_app(str(tmp_path / "desk"))
    net = lan({A_HOST: a, B_HOST: b})
    b.state.platform.router.stream = _scripted("Desk read the notes.")
    async with _client(A_HOST) as ca, _client(B_HOST) as cb:
        await _pair(ca, cb)
        (pa,) = S.store_for(a.state.platform).list()
        (pb,) = S.store_for(b.state.platform).list()
        assert pb.address == f"http://{A_HOST}"
        door_token = a.state.platform.secrets.get(pa.out_secret)
        desk = RemoteAgentRegistry(a.state.platform.engine).get("desk")
        reply_key = a.state.platform.secrets.get(desk.inbound_secret_name)
        # A task that came in with the paired computer's token but names
        # ANOTHER address for the answer.
        async with httpx.AsyncClient(base_url=f"http://{B_HOST}") as raw:
            r = await raw.post(
                TASK_DOOR_PATH,
                json={"task": "read the notes", "conversation_id": "athr_1",
                      "reply_to": f"http://{ELSEWHERE}/agents/remote/desk/inbound",
                      "reply_token": reply_key},
                headers={"Authorization": f"Bearer {door_token}"},
            )
            assert r.status_code == 202, r.text
        rows = await _settled(b)
    assert len(rows) == 1 and rows[0].status is SessionStatus.COMPLETED
    elsewhere = [c for c in net.calls if ELSEWHERE in c["url"]]
    assert elsewhere == [], "the answer (and its files) went to an address nobody paired"
    home = [c for c in net.calls if c["url"] == f"http://{A_HOST}/agents/remote/desk/inbound"]
    assert len(home) == 1
    assert json.loads(home[0]["body"])["message"] == "Desk read the notes."
    assert home[0]["auth"] == f"Bearer {reply_key}"


def test_the_pinned_address_keeps_only_the_path_of_reply_to():
    """Unit view of the one rule: stored scheme+host+port, the door's path."""
    peer = S.PeerRecord(name="laptop", address="http://10.0.0.1:8787")
    assert td.pinned_reply_to(peer, "https://evil.example.com:9/agents/remote/desk/inbound") == (
        "http://10.0.0.1:8787/agents/remote/desk/inbound"
    )
    assert td.pinned_reply_to(S.PeerRecord(name="x", address=""), "http://a/agents/remote/d/inbound") == ""
