"""Second review F3 (v1.510.0): an answer from another computer is not lost,
and the asking side never waits for ever.

ANSWERING SIDE. ``agents/task_door.send_reply`` used to read the run's answer
key and DELETE it before the POST, allow one attempt per process, and write a
failure on its own run only. Now the key is removed only after the other
computer answered 2xx; a failed attempt is tried again on a fixed ladder
(``REPLY_RETRY_AFTER_S``) read through an injected clock and the daemon's
periodic pass; a refusal (a 4xx such as a wrong key) is final and recorded;
an answer still owed after the ladder is sent when that computer is next heard
from (a task, a probe, a Check press); "owed" is read from what is stored (the
reply spec in the run's options and the kept key), so a restart in between
still delivers. The receiving door ignores a repeat of an answer it already
took (``answer_id``).

ASKING SIDE. A "will report back" line for another computer ends after
``threads.REMOTE_REPORT_LIMIT_S`` with plain words in the room and the chat,
and the mission card's member moves to "needs you" with the same words.

Through the real code: two ``create_app`` factories, their middleware, the real
pairing, chat panel, door, runtime, reply and inbound door; the network and
the model are the stand-ins (the harness of ``tests/test_peers_v1497.py``).
"""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from iron_jarvis.agents import task_door as td
from iron_jarvis.agents import threads as T
from iron_jarvis.agents.threads import AgentThreads
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.ids import utcnow
from iron_jarvis.core.models import Session, SessionStatus
from iron_jarvis.daemon.app import create_app
from iron_jarvis.providers.adapters.base import LLMResponse

INSTALL = "install-bearer-for-both-computers"
AUTH = {"Authorization": f"Bearer {INSTALL}"}
A_IP, B_IP = "10.0.0.1", "10.0.0.2"
A_HOST, B_HOST = f"{A_IP}:8787", f"{B_IP}:8787"
INBOUND = f"http://{A_HOST}/agents/remote/desk/inbound"


# --------------------------------------------------------------------------- #
# harness: two computers, one network that can drop A's answer door
# --------------------------------------------------------------------------- #


class _Network(httpx.AsyncBaseTransport):
    def __init__(self, apps: dict[str, Any], senders: dict[str, str]) -> None:
        self.senders = senders
        self.apps = {
            host: httpx.ASGITransport(app=app, client=(senders[host], 50000))
            for host, app in apps.items()
        }
        self.calls: list[dict[str, Any]] = []
        #: What A's answer door does to the next POSTs: "down" (cannot be
        #: reached) or a status code; empty = the real door answers.
        self.inbound_plan: list[Any] = []

    def swap(self, host: str, app: Any) -> None:
        self.apps[host] = httpx.ASGITransport(app=app, client=(self.senders[host], 50000))

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        host = request.url.netloc.decode()
        url = str(request.url)
        self.calls.append({"method": request.method, "url": url,
                           "auth": request.headers.get("authorization", ""),
                           "body": request.content})
        if request.method == "POST" and url == INBOUND and self.inbound_plan:
            step = self.inbound_plan.pop(0)
            if step == "down":
                raise httpx.ConnectError(f"no route to {host}", request=request)
            return httpx.Response(int(step), json={"detail": "refused"}, request=request)
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


def _remote_sessions(app) -> list[Session]:
    with session_scope(app.state.platform.engine) as db:
        rows = [s for s in db.exec(__import__("sqlmodel").select(Session))
                if str(s.origin or "").startswith("remote:")]
        for r in rows:
            db.expunge(r)
        return rows


async def _settled(app, timeout_s: float = 30.0) -> Session:
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
    (row,) = await asyncio.to_thread(_remote_sessions, app)
    return row


def _scripted(answer: str):
    async def fake_stream(**kw):
        yield {"type": "final", "response": LLMResponse(text=answer),
               "provider": "openai", "model": "gpt-5"}

    return fake_stream


class _Clock:
    """The injected wall clock both sides read (``platform.reply_clock``)."""

    def __init__(self) -> None:
        self.now = utcnow()

    def __call__(self):
        return self.now

    def ahead(self, **kw) -> None:
        self.now = self.now + timedelta(**kw)


async def _two_paired(tmp_path, lan, answer: str = "Desk read the notes."):
    a = create_app(str(tmp_path / "laptop"))
    b = create_app(str(tmp_path / "desk"))
    net = lan({A_HOST: a, B_HOST: b})
    b.state.platform.router.stream = _scripted(answer)
    clock_a, clock_b = _Clock(), _Clock()
    a.state.platform.reply_clock = clock_a
    b.state.platform.reply_clock = clock_b
    return a, b, net, clock_a, clock_b


async def _ask_desk(ca) -> tuple[str, str]:
    """On A: a saved chat, then "@desk ..." from its panel (the real door to
    B). Returns ``(chat id, room id)``."""
    made = await ca.put("/chat/threads/new",
                        json={"messages": [{"role": "user", "content": "@desk read the notes"}]})
    tid = made.json()["id"]
    r = await ca.post("/chat/panel", json={
        "message": "@desk read the notes", "thread_id": tid, "chat_thread_id": tid,
        "history": [{"who": "user", "content": "@desk read the notes"}],
    })
    assert r.status_code == 200, r.text
    panel = r.json()
    assert panel["entries"][-1]["pending"] is True
    return tid, panel["thread_id"]


def _answers(net) -> list[dict]:
    return [c for c in net.calls if c["method"] == "POST" and c["url"] == INBOUND]


def _key_kept(b, sid: str) -> bool:
    return bool(b.state.platform.secrets.get(td.REPLY_TOKEN_PREFIX + sid))


def _row(app, sid: str) -> Session:
    with session_scope(app.state.platform.engine) as db:
        row = db.get(Session, sid)
        db.expunge(row)
        return row


def _homed(a, tid: str) -> list[dict]:
    from iron_jarvis.core.models import ChatThreadRecord

    with session_scope(a.state.platform.engine) as db:
        rec = db.get(ChatThreadRecord, tid)
        msgs = json.loads(rec.messages_json or "[]")
    return [m for m in msgs if m.get("panelWho") == "remote:desk" and m.get("panelKind") == "done"]


async def _pass(app) -> None:
    await td.periodic_pass(app.state.platform)
    await td.drain_replies()


# --------------------------------------------------------------------------- #
# A. the answering side keeps the answer until it is delivered
# --------------------------------------------------------------------------- #


async def test_a_failed_answer_keeps_its_key_and_a_later_try_delivers_it_once(tmp_path, lan):
    a, b, net, _ca, clock_b = await _two_paired(tmp_path, lan)
    net.inbound_plan = ["down"]
    async with _client(A_HOST) as ca, _client(B_HOST) as cb:
        await _pair(ca, cb)
        tid, _room = await _ask_desk(ca)
        row = await _settled(b)
        assert row.status is SessionStatus.COMPLETED
        assert len(_answers(net)) == 1
        assert _key_kept(b, row.id), "the answer key was removed before the answer arrived"
        summary = _row(b, row.id).summary or ""
        assert td.retry_note("laptop", "").split(":")[0] in summary, summary
        assert "Could not send" not in summary

        # The run's terminal path asking again before the next rung sends nothing.
        await td.send_reply(b.state.platform, _row(b, row.id))
        assert len(_answers(net)) == 1

        # A pass before the first rung sends nothing either.
        clock_b.ahead(seconds=td.REPLY_RETRY_AFTER_S[0] - 5)
        await _pass(b)
        assert len(_answers(net)) == 1

        # At the first rung the answer goes, and arrives.
        clock_b.ahead(seconds=10)
        await _pass(b)
        assert len(_answers(net)) == 2
        assert [m["content"] for m in _homed(a, tid)] == ["Desk read the notes."]
        assert not _key_kept(b, row.id), "the key outlived the delivered answer"
        summary = _row(b, row.id).summary or ""
        assert td.retry_note("laptop", "").split(":")[0] not in summary
        assert "Could not send" not in summary

        # Delivered is delivered: no later pass sends it again.
        clock_b.ahead(hours=2)
        await _pass(b)
        await td.reply_pass(b.state.platform, force=True)
        await td.drain_replies()
        assert len(_answers(net)) == 2


async def test_a_refused_answer_is_final_and_recorded(tmp_path, lan):
    a, b, net, _ca, clock_b = await _two_paired(tmp_path, lan)
    net.inbound_plan = [401]
    async with _client(A_HOST) as ca, _client(B_HOST) as cb:
        await _pair(ca, cb)
        await _ask_desk(ca)
        row = await _settled(b)
        assert len(_answers(net)) == 1
        assert not _key_kept(b, row.id)
        summary = _row(b, row.id).summary or ""
        assert "Could not send this answer back to laptop" in summary
        assert "refused this computer's answer key" in summary
        for hours in (1, 5, 30):
            clock_b.ahead(hours=hours)
            await _pass(b)
        # ...and Check from the other computer does not bring it back.
        await ca.post("/peers/check")
        await td.drain_replies()
        assert len(_answers(net)) == 1


async def test_after_the_last_try_the_answer_waits_and_goes_when_the_other_computer_checks(
    tmp_path, lan
):
    a, b, net, _ca, clock_b = await _two_paired(tmp_path, lan)
    net.inbound_plan = ["down"] * td.REPLY_TRIES
    async with _client(A_HOST) as ca, _client(B_HOST) as cb:
        await _pair(ca, cb)
        tid, _room = await _ask_desk(ca)
        row = await _settled(b)
        for wait in td.REPLY_RETRY_AFTER_S:
            clock_b.ahead(seconds=wait + 1)
            await _pass(b)
        assert len(_answers(net)) == td.REPLY_TRIES
        summary = _row(b, row.id).summary or ""
        assert td.waiting_note("laptop", "").split(":")[0] in summary, summary
        assert "next connects" in summary
        assert _key_kept(b, row.id)

        # The ladder is over: hours of passes send nothing more.
        clock_b.ahead(hours=6)
        await _pass(b)
        assert len(_answers(net)) == td.REPLY_TRIES

        # The other computer presses Check: it reaches this one's door, so this
        # one sends what it still owes it, to the pairing's stored address.
        r = await ca.post("/peers/check")
        assert r.status_code == 200, r.text
        await td.drain_replies()
        assert len(_answers(net)) == td.REPLY_TRIES + 1
        assert [m["content"] for m in _homed(a, tid)] == ["Desk read the notes."]
        assert not _key_kept(b, row.id)
        summary = _row(b, row.id).summary or ""
        assert td.waiting_note("laptop", "").split(":")[0] not in summary


async def test_an_owed_answer_goes_when_this_computer_presses_check_too(tmp_path, lan):
    a, b, net, _ca, clock_b = await _two_paired(tmp_path, lan)
    net.inbound_plan = ["down"]
    async with _client(A_HOST) as ca, _client(B_HOST) as cb:
        await _pair(ca, cb)
        tid, _room = await _ask_desk(ca)
        row = await _settled(b)
        assert len(_answers(net)) == 1
        # Check pressed on the ANSWERING computer: the other one answered it,
        # so the answer owed to it goes now (no rung waited for).
        r = await cb.post("/peers/check")
        assert r.status_code == 200, r.text
        await td.drain_replies()
        assert len(_answers(net)) == 2
        assert [m["content"] for m in _homed(a, tid)] == ["Desk read the notes."]
        assert not _key_kept(b, row.id)


async def test_a_restart_in_between_still_delivers(tmp_path, lan):
    a, b, net, _ca, clock_b = await _two_paired(tmp_path, lan)
    net.inbound_plan = ["down"]
    async with _client(A_HOST) as ca, _client(B_HOST) as cb:
        await _pair(ca, cb)
        tid, _room = await _ask_desk(ca)
        row = await _settled(b)
        assert len(_answers(net)) == 1
    # B restarts: a fresh app on the same folder; nothing kept in memory.
    b2 = create_app(str(tmp_path / "desk"))
    net.swap(B_HOST, b2)
    clock_b2 = _Clock()
    clock_b2.now = clock_b.now + timedelta(seconds=td.REPLY_RETRY_AFTER_S[0] + 1)
    b2.state.platform.reply_clock = clock_b2
    await _pass(b2)
    assert len(_answers(net)) == 2
    assert [m["content"] for m in _homed(a, tid)] == ["Desk read the notes."]
    assert not _key_kept(b2, row.id)


async def test_the_answer_door_ignores_a_repeat_of_an_answer_it_took(tmp_path, lan):
    a, b, net, _ca, _cb = await _two_paired(tmp_path, lan)
    async with _client(A_HOST) as ca, _client(B_HOST) as cb:
        await _pair(ca, cb)
        tid, room = await _ask_desk(ca)
        await _settled(b)
        (sent,) = _answers(net)
        body = json.loads(sent["body"])
        assert body["answer_id"], "the answer names the run it answers"
        before = json.loads(AgentThreads(a.state.platform.engine).get(room).messages_json)
        # A 2xx that was lost on the way back: the same answer arrives again.
        async with httpx.AsyncClient() as raw:
            again = await raw.post(INBOUND, json=body, headers={"Authorization": sent["auth"]})
        assert again.status_code == 200, again.text
        assert again.json()["repeat"] is True
        after = json.loads(AgentThreads(a.state.platform.engine).get(room).messages_json)
        assert len(after) == len(before), "the repeat landed in the room twice"
        assert len(_homed(a, tid)) == 1, "the repeat landed in the chat twice"
        # Another run's answer into the same conversation still lands.
        other = dict(body, answer_id="another-run", message="A second answer.")
        async with httpx.AsyncClient() as raw:
            r = await raw.post(INBOUND, json=other, headers={"Authorization": sent["auth"]})
        assert r.status_code == 200 and not r.json().get("repeat")
        assert len(_homed(a, tid)) == 2


# --------------------------------------------------------------------------- #
# B. the asking side never waits for ever
# --------------------------------------------------------------------------- #


async def test_a_wait_for_another_computer_ends_in_plain_words_at_its_limit(tmp_path, lan):
    a, b, net, clock_a, _cb = await _two_paired(tmp_path, lan)
    net.inbound_plan = ["down"] * 50  # desk's answer never reaches the laptop
    limit = T.REMOTE_REPORT_LIMIT_S
    async with _client(A_HOST) as ca, _client(B_HOST) as cb:
        await _pair(ca, cb)
        tid, room = await _ask_desk(ca)
        await _settled(b)

        def room_msgs():
            return json.loads(AgentThreads(a.state.platform.engine).get(room).messages_json)

        clock_a.ahead(seconds=limit - 20 * 60)
        await _pass(a)
        assert not [m for m in room_msgs() if m.get(T.NOT_REPORTED_FLAG)]

        clock_a.ahead(seconds=21 * 60)
        await _pass(a)
        ended = [m for m in room_msgs() if m.get(T.NOT_REPORTED_FLAG)]
        words = T.not_reported_words("desk", paired=True)
        assert len(ended) == 1 and ended[0]["content"] == words
        assert "has not reported back" in words and "Check" in words
        thread = (await ca.get(f"/chat/threads/{tid}")).json()
        assert [m for m in thread["messages"] if m.get("content") == words], thread["messages"]

        # Said once: later passes add nothing.
        clock_a.ahead(hours=3)
        await _pass(a)
        assert len([m for m in room_msgs() if m.get(T.NOT_REPORTED_FLAG)]) == 1
        thread = (await ca.get(f"/chat/threads/{tid}")).json()
        assert len([m for m in thread["messages"] if m.get("content") == words]) == 1
        # The words are Jarvis's, never handed back to desk as its own.
        rows = AgentThreads.remote_history(room_msgs(), {"key": "remote:desk"}, [])
        assert all(words not in r["content"] for r in rows)


def test_the_mission_card_ends_with_the_same_words_at_the_limit(tmp_path, monkeypatch):
    from iron_jarvis.agents.delegate_tool import DelegateTool
    from iron_jarvis.agents.mission import mission_view
    from iron_jarvis.agents.orchestrator import Orchestrator
    from iron_jarvis.core.models import AgentType
    from iron_jarvis.tools.base import ToolContext

    class _Resp:
        status_code = 202
        text = ""

        def json(self):
            raise ValueError("no body")

    async def post(self, url, json=None, headers=None, **kw):
        return _Resp()

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    app = create_app(str(tmp_path))
    p = app.state.platform
    clock = _Clock()
    p.reply_clock = clock
    with TestClient(app) as client:
        r = client.post("/agents/remote", json={"name": "hermes", "base_url": "http://192.168.1.50:8080/run",
                                                "kind": "http-task", "token": "t"})
        assert r.status_code == 200, r.text
        assert client.post("/agents/remote/hermes/inbound/enable", json={}).status_code == 200

        async def drive():
            root = await Orchestrator(p).create_session(
                "objective", AgentType.SUPERVISOR, provider="mock", origin="job:mission"
            )
            ctx = ToolContext(workspace=p.config.workspaces_dir, session_id=root.id,
                              agent_run_id="root-run", config=p.config, event_bus=p.event_bus,
                              engine=p.engine)
            res = await DelegateTool(p).execute(
                {"agent_type": "remote:hermes", "task": "summarise the notes"}, ctx
            )
            return root.id, res

        root_id, res = asyncio.run(drive())
        assert res.ok is True and res.data["state"] == "accepted"

        def card():
            (c,) = [m for m in mission_view(p, root_id)["members"] if m["kind"] == "remote"]
            return c

        clock.ahead(seconds=T.REMOTE_REPORT_LIMIT_S - 60)
        assert card()["status"] == "waiting_remote"
        clock.ahead(seconds=120)
        c = card()
        assert c["status"] == "waiting_you", c
        assert c["progress"]["label"] == T.NOT_REPORTED_LABEL
        assert c["result"] == T.not_reported_words("hermes", paired=False)
        texts = [x["text"] for x in mission_view(p, root_id)["activity"]]
        assert any("has not reported back" in t for t in texts), texts
