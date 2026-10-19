"""Another computer running Iron Jarvis can be connected (v1.357.0, G61).

ACCEPTANCE (the readiness audit's G61): on two PCs, a user registers the second
Jarvis by address and token in Agents › Your team, "@desk do X" runs there, and
the answer comes home to the chat.

Pinned here through the REAL code — two real ``create_app`` factories in one
process, their real middleware (Host guard, install bearer), the real routes,
the real orchestrator and the real vault — with one stand-in only: the network
between the two "computers" (an httpx transport that hands each request to the
app whose host it names, the way the LAN would), and B's model (a scripted
stream: the run itself is real).

  D1 the door's leaf is exempt for exactly itself; its controls are not.
  D2 turning the door on mints a token ONCE (vault only, rotate on re-enable,
     never in a status read); off forgets it.
  D3 the door is fail-closed: off 403, no/wrong token 401, past the per-minute
     limit 429, too large 413, a reply address that is not another Jarvis's
     inbound door 400, Emergency stop 409 in its own words.
  D4 a task becomes ONE session: origin ``remote:<caller>`` (the one string),
     LOW trust, unattended (no asks, background lane, Emergency stop covers
     and stops it), answered 202 at once, the reply key in the vault.
  D5 THE ACCEPTANCE, end to end on two app factories.
  D6 a failed answer is recorded on the run and the door, named, attempted
     ONCE; a restart-cut run still tells the sender; a stopped run says so.
  D7 the sending side: ``reply_token`` rides only to a task door; "Test" asks
     the door and starts nothing; a door's refusal is said in words; the form
     refuses a chat dialect for a door; the reachability sentence says "Iron
     Jarvis", never "daemon", and opens nothing.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from iron_jarvis.agents import runtime as runtime_mod
from iron_jarvis.agents import task_door as td
from iron_jarvis.agents.orchestrator import (
    EMERGENCY_STOP_REFUSAL,
    RESTART_SUMMARY,
    emergency_stop_covers,
    is_unattended_origin,
)
from iron_jarvis.agents.remote import RemoteAgentRegistry
from iron_jarvis.agents.threads import AgentThreads
from iron_jarvis.core import procs
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import AgentType, Session, SessionStatus
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.auth import TASK_DOOR_PATH, _is_exempt
from iron_jarvis.providers.adapters.base import LLMResponse

INSTALL = "install-bearer-for-both"
AUTH = {"Authorization": f"Bearer {INSTALL}"}
A_HOST = "a-pc:8787"
B_HOST = "b-pc:8787"


# --------------------------------------------------------------------------- #
# harness: two computers, one network
# --------------------------------------------------------------------------- #


class _Network(httpx.AsyncBaseTransport):
    """The LAN: a request goes to the app whose host it names; any other host
    cannot be reached (a ConnectError, exactly what a dead address gives)."""

    def __init__(self, apps: dict[str, Any]) -> None:
        self.apps = {host: httpx.ASGITransport(app=app) for host, app in apps.items()}
        self.calls: list[dict[str, Any]] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        host = request.url.netloc.decode()
        self.calls.append(
            {
                "method": request.method,
                "url": str(request.url),
                "auth": request.headers.get("authorization", ""),
                "body": request.content,
            }
        )
        transport = self.apps.get(host)
        if transport is None:
            raise httpx.ConnectError(f"no route to {host}", request=request)
        return await transport.handle_async_request(request)


@pytest.fixture()
def lan(monkeypatch):
    """Both computers open to each other ON PURPOSE (the allowlist a user
    sets), the install bearer on (as the packaged app runs), and every
    ``httpx.AsyncClient`` riding the stand-in network."""
    monkeypatch.setenv("IRONJARVIS_HOST_ALLOWLIST", "a-pc,b-pc")
    monkeypatch.setenv("IRONJARVIS_TOKEN", INSTALL)
    holder: dict[str, Any] = {}
    real_init = httpx.AsyncClient.__init__

    def _init(self, *a, **kw):
        kw.pop("transport", None)
        kw["transport"] = holder["net"]
        real_init(self, *a, **kw)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", _init)

    def make(apps: dict[str, Any]) -> _Network:
        holder["net"] = _Network(apps)
        return holder["net"]

    return make


def _scripted(answer: str, seen: list | None = None):
    """B's model: one final answer (the run itself is the real runtime)."""

    async def fake_stream(**kw):
        if seen is not None:
            seen.append(kw)
        yield {
            "type": "final",
            "response": LLMResponse(text=answer),
            "provider": "mock",
            "model": "mock",
        }

    return fake_stream


def _row(app, sid: str) -> Session:
    with session_scope(app.state.platform.engine) as db:
        row = db.get(Session, sid)
        assert row is not None
        db.expunge(row)
        return row


def _remote_sessions(app) -> list[Session]:
    with session_scope(app.state.platform.engine) as db:
        rows = [
            s
            for s in db.exec(__import__("sqlmodel").select(Session))
            if str(s.origin or "").startswith("remote:")
        ]
        for r in rows:
            db.expunge(r)
        return rows


async def _settled(app, *, count: int = 1, timeout_s: float = 30.0) -> list[Session]:
    """Wait (bounded) for ``count`` remote-task runs to reach a terminal state
    AND for their run tasks to have finished (a cross-thread cancel stamps the
    row before the run unwinds — the finalizer is what starts the reply),
    then for every reply in flight."""
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout_s
    running = app.state.orchestrator._running
    while True:
        rows = await asyncio.to_thread(_remote_sessions, app)
        done = [
            r
            for r in rows
            if r.status in (SessionStatus.COMPLETED, SessionStatus.FAILED, SessionStatus.CANCELLED)
            and (running.get(r.id) is None or running[r.id].done())
        ]
        if len(done) >= count or loop.time() > end:
            break
        await asyncio.sleep(0.05)
    await td.drain_replies()
    return await asyncio.to_thread(_remote_sessions, app)


def _client(host: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=f"http://{host}", headers=AUTH)


# --------------------------------------------------------------------------- #
# D1 — the exempt leaf
# --------------------------------------------------------------------------- #


def test_the_door_leaf_is_exempt_and_its_controls_are_not():
    assert TASK_DOOR_PATH == "/agents/task-door/task"
    assert _is_exempt(TASK_DOOR_PATH)
    for guarded in (
        "/agents/task-door",
        "/agents/task-door/enable",
        "/agents/task-door/disable",
        "/agents/task-door/task/x",
        "/agents/task-door/taskx",
        "/agents/task-door/task/",
        "/x/agents/task-door/task",
    ):
        assert not _is_exempt(guarded), guarded


# --------------------------------------------------------------------------- #
# D2 — on, token once, rotate, off
# --------------------------------------------------------------------------- #


def test_turning_the_door_on_mints_a_token_once_and_status_never_carries_it(tmp_path):
    app = create_app(str(tmp_path))
    client = TestClient(app)
    first = client.get("/agents/task-door").json()
    assert first["enabled"] is False and "token" not in first
    on = client.post("/agents/task-door/enable", json={"name": "laptop"}).json()
    assert on["enabled"] is True and on["caller"] == "laptop"
    assert len(on["token"]) >= 32
    assert on["url"].endswith(TASK_DOOR_PATH)
    assert set(on["reachable"]) == {"host_allowed", "note"}
    secrets = app.state.platform.secrets
    assert secrets.get(td.DOOR_TOKEN_SECRET) == on["token"]
    listed = client.get("/agents/task-door").json()
    assert listed["enabled"] is True and "token" not in listed
    assert on["token"] not in json.dumps(listed)
    # the state file holds no secret either
    state_path = td.door_for(app.state.platform).state_path
    assert state_path.parent == app.state.platform.config.home
    assert on["token"] not in state_path.read_text(encoding="utf-8")
    # re-enable ROTATES (and keeps the name when none is given)
    again = client.post("/agents/task-door/enable", json={}).json()
    assert again["token"] != on["token"] and again["caller"] == "laptop"
    door = td.door_for(app.state.platform)
    assert door.verify(again["token"]) and not door.verify(on["token"])
    # the state file is a lock of its own: marked off with a token still in
    # the vault (a disable that could not delete it), the door refuses
    door._update(enabled=False)
    assert secrets.get(td.DOOR_TOKEN_SECRET) == again["token"]
    assert not door.verify(again["token"])
    door._update(enabled=True)
    assert door.verify(again["token"])
    # off forgets the token
    off = client.post("/agents/task-door/disable").json()
    assert off["enabled"] is False and off["reachable"] is None
    assert secrets.get(td.DOOR_TOKEN_SECRET) is None
    assert not door.verify(again["token"])


def test_the_door_controls_need_the_install_bearer(tmp_path, monkeypatch):
    monkeypatch.setenv("IRONJARVIS_TOKEN", INSTALL)
    client = TestClient(create_app(str(tmp_path)))
    assert client.get("/agents/task-door").status_code == 401
    assert client.post("/agents/task-door/enable", json={}).status_code == 401
    assert client.get("/agents/task-door", headers=AUTH).status_code == 200


# --------------------------------------------------------------------------- #
# D3 — fail-closed
# --------------------------------------------------------------------------- #


def test_the_door_is_fail_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("IRONJARVIS_TOKEN", INSTALL)
    app = create_app(str(tmp_path))
    app.state.platform.router.stream = _scripted("ok")
    client = TestClient(app)
    task = {"task": "summarise the notes"}
    # off: 403 whatever the header says — and in words the sender can show
    r = client.post(TASK_DOOR_PATH, json=task)
    assert r.status_code == 403 and r.json()["detail"] == td.DOOR_OFF
    assert client.post(TASK_DOOR_PATH, json=task, headers=AUTH).status_code == 403
    token = client.post("/agents/task-door/enable", json={"name": "laptop"}, headers=AUTH).json()[
        "token"
    ]
    door_auth = {"Authorization": f"Bearer {token}"}
    # on: no token / wrong token / the INSTALL bearer → 401 (its own key only)
    assert client.post(TASK_DOOR_PATH, json=task).status_code == 401
    assert client.post(TASK_DOOR_PATH, json=task, headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.post(TASK_DOOR_PATH, json=task, headers=AUTH).status_code == 401
    assert (
        client.post(
            TASK_DOOR_PATH, json=task, headers={"Authorization": "Bearer café".encode("utf-8")}
        ).status_code
        == 401
    ), "a non-ASCII token is a plain refusal, never a crash"
    # too large: the task text and the whole body
    big = {"task": "x" * (td.TASK_MAX_CHARS + 1)}
    assert client.post(TASK_DOOR_PATH, json=big, headers=door_auth).status_code == 413
    monkeypatch.setattr(td, "TASK_MAX_BODY_BYTES", 100)
    padded = {"task": "short", "history": [{"role": "user", "content": "y" * 500}]}
    assert client.post(TASK_DOOR_PATH, json=padded, headers=door_auth).status_code == 413
    monkeypatch.setattr(td, "TASK_MAX_BODY_BYTES", 2 * 1024 * 1024)
    # a reply address that is not another Iron Jarvis's inbound door
    for bad in (
        "http://192.168.1.9:8787/agents/remote/desk/run",
        "file:///etc/passwd",
        "http://user:pw@a-pc:8787/agents/remote/desk/inbound",
    ):
        r = client.post(TASK_DOOR_PATH, json={**task, "reply_to": bad}, headers=door_auth)
        assert r.status_code == 400, bad
    # nothing above started a run
    assert _remote_sessions(app) == []
    # Emergency stop: 409 in the switch's own words, nothing created
    app.state.platform.config.autonomy_kill_switch = True
    r = client.post(TASK_DOOR_PATH, json=task, headers=door_auth)
    assert r.status_code == 409
    assert r.json() == {"detail": EMERGENCY_STOP_REFUSAL, "code": "emergency_stop"}
    assert _remote_sessions(app) == []
    app.state.platform.config.autonomy_kill_switch = False
    # the per-minute limit
    monkeypatch.setattr(td, "TASK_RATE_MAX", 2)
    td.door_for(app.state.platform)._hits.clear()
    codes = [client.post(TASK_DOOR_PATH, json=task, headers=door_auth).status_code for _ in range(3)]
    assert codes == [202, 202, 429]


# --------------------------------------------------------------------------- #
# D4 — one unattended, low-trust session; 202 at once
# --------------------------------------------------------------------------- #


async def test_a_task_becomes_one_unattended_low_trust_session(tmp_path, monkeypatch):
    monkeypatch.setenv("IRONJARVIS_HOST_ALLOWLIST", "b-pc")
    app = create_app(str(tmp_path))
    seen: list = []
    app.state.platform.router.stream = _scripted("Here is the summary.", seen)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=f"http://{B_HOST}"
    ) as client:
        token = (await client.post("/agents/task-door/enable", json={"name": "laptop"})).json()[
            "token"
        ]
        r = await client.post(
            TASK_DOOR_PATH,
            json={
                "task": "summarise the notes",
                "conversation_id": "athr_1",
                "history": [
                    {"role": "user", "content": "we need the Q3 notes"},
                    {"role": "assistant", "content": "Iron Jarvis: on it"},
                ],
                "reply_to": "http://a-pc:8787/agents/remote/desk/inbound",
                "reply_token": "the-callers-inbound-key",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 202 and r.json() == {"accepted": True}
        rows = await _settled(app)
    assert len(rows) == 1
    row = rows[0]
    # the ONE origin string, defined once
    assert row.origin == td.remote_origin("laptop") == "remote:laptop"
    assert td.is_remote_task_origin(row.origin)
    # LOW trust from the door, named
    assert row.trust == "low" and "laptop" in row.trust_reason
    assert row.agent_type is AgentType.SUPERVISOR
    # the task says where it came from and carries the conversation's tail
    assert row.task.startswith(td.TASK_INTRO.format(caller="laptop"))
    assert "summarise the notes" in row.task and "we need the Q3 notes" in row.task
    # the reply address rides on the row — never the key
    opts = json.loads(row.options_json)
    assert opts[td.REPLY_OPTION] == {
        "to": "http://a-pc:8787/agents/remote/desk/inbound",
        "conversation_id": "athr_1",
        "caller": "laptop",
    }
    assert "the-callers-inbound-key" not in row.options_json
    # the model saw the task (the real runtime ran it)
    assert seen and "summarise the notes" in json.dumps(
        [str(m) for m in seen[0].get("messages", [])]
    )


def test_a_remote_task_origin_is_unattended_everywhere_it_matters():
    origin = td.remote_origin("laptop")
    # nobody here answers its asks: not attended, not an asking origin
    assert not origin.startswith(runtime_mod.ATTENDED_ORIGINS)
    assert not origin.startswith(runtime_mod.ASKING_ORIGINS)
    # the background lane and Emergency stop both cover it
    assert is_unattended_origin(origin)
    assert emergency_stop_covers(origin)
    assert runtime_mod._ask_timeout_for(origin) == runtime_mod.SESSION_APPROVAL_TIMEOUT_S
    # the ONE string: every caller name lands under the same prefix
    for raw in ("laptop", "  Office  PC ", "", "../../x", "a" * 99):
        o = td.remote_origin(raw)
        assert o.startswith(f"{td.REMOTE_TASK_ORIGIN}:") and len(o) <= 47
    assert td.remote_origin("") == "remote:another computer"


async def test_an_ask_from_a_remote_task_is_refused_headless_and_the_run_is_low_trust(
    tmp_path,
):
    """Through the REAL runtime and registry: an ask-tier tool on the run's
    own roster (``notify``) takes the instant headless refusal — nobody at
    this computer answers a remote task's asks, so no card is ever put up —
    a low-trust-denied one (``worklist_add``) is refused as low trust, and
    the run works in the background lane."""
    from iron_jarvis.providers.adapters.base import ToolCall

    app = create_app(str(tmp_path))
    rounds = {"n": 0}
    lanes: list[bool] = []

    async def fake_stream(**kw):
        lanes.append(procs.in_background_lane())
        i = rounds["n"]
        rounds["n"] += 1
        if i == 0:
            resp = LLMResponse(
                text="",
                tool_calls=[
                    ToolCall(id="c0", name="notify", arguments={"message": "hello"}),
                    ToolCall(id="c1", name="worklist_add", arguments={"items": ["a"]}),
                ],
            )
        else:
            resp = LLMResponse(text="I could not do that from here.")
        yield {"type": "final", "response": resp, "provider": "mock", "model": "mock"}

    app.state.platform.router.stream = fake_stream
    published: list[str] = []
    denied: list[dict] = []
    real_publish = app.state.platform.event_bus.publish

    async def spy(etype, payload=None, session_id=None, **kw):
        published.append(str(getattr(etype, "value", etype)))
        if str(getattr(etype, "value", etype)) == "tool.denied":
            denied.append(dict(payload or {}))
        return await real_publish(etype, payload, session_id=session_id, **kw)

    app.state.platform.event_bus.publish = spy
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        token = (await client.post("/agents/task-door/enable", json={"name": "laptop"})).json()["token"]
        r = await client.post(
            TASK_DOOR_PATH,
            json={"task": "list my files with the shell"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 202
        rows = await _settled(app)
    assert rows and rows[0].status is SessionStatus.COMPLETED
    assert "approval.requested" not in published, "nobody here may be asked"
    by_tool = {d.get("tool"): d for d in denied}
    assert "notify" in by_tool and "worklist_add" in by_tool, denied
    assert by_tool["notify"]["mode"] == "ask", "the ask tier, refused headless — not a card"
    assert "low trust" in str(by_tool["worklist_add"].get("kind", "")).lower(), by_tool["worklist_add"]
    assert lanes and all(lanes), "a task from another computer runs in the background lane"


async def test_emergency_stop_stops_a_running_remote_task_and_the_sender_hears_it(
    tmp_path, lan
):
    b = create_app(str(tmp_path / "b"))
    a = create_app(str(tmp_path / "a"))
    net = lan({A_HOST: a, B_HOST: b})
    started = asyncio.Event()

    async def hanging(**kw):
        started.set()
        await asyncio.Event().wait()
        yield  # pragma: no cover

    b.state.platform.router.stream = hanging
    async with _client(B_HOST) as cb:
        token = (await cb.post("/agents/task-door/enable", json={"name": "laptop"})).json()["token"]
        r = await cb.post(
            TASK_DOOR_PATH,
            json={
                "task": "a long job",
                "conversation_id": "athr_x",
                "reply_to": f"http://{A_HOST}/agents/remote/desk/inbound",
                "reply_token": "k",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 202
        await asyncio.wait_for(started.wait(), 10)
        b.state.platform.config.autonomy_kill_switch = True
        stopped = await asyncio.to_thread(b.state.orchestrator.stop_unattended)
        assert len(stopped) == 1
        rows = await _settled(b)
    assert rows[0].status is SessionStatus.CANCELLED
    replies = [c for c in net.calls if c["url"].endswith("/agents/remote/desk/inbound")]
    assert len(replies) == 1
    sent = json.loads(replies[0]["body"])
    assert sent["kind"] == "done" and "Emergency stop" in sent["message"]


# --------------------------------------------------------------------------- #
# D5 — THE ACCEPTANCE: two computers, one chat
# --------------------------------------------------------------------------- #


async def test_two_computers_desk_does_the_task_and_the_answer_comes_home(tmp_path, lan):
    a = create_app(str(tmp_path / "a"))  # the computer the user chats on
    b = create_app(str(tmp_path / "b"))  # "desk", the second computer
    net = lan({A_HOST: a, B_HOST: b})
    seen: list = []
    b.state.platform.router.stream = _scripted("Desk read the notes: three action items.", seen)

    async with _client(A_HOST) as ca, _client(B_HOST) as cb:
        # On B: Agents › Your team › Another computer running Jarvis › Turn on.
        on = (await cb.post("/agents/task-door/enable", json={"name": "laptop"})).json()
        assert on["reachable"]["host_allowed"] is True, on["reachable"]
        door_url, door_token = on["url"], on["token"]
        assert door_url == f"http://{B_HOST}{TASK_DOOR_PATH}"

        # On A: the EXISTING remote-agent form — name, http-task, address, token.
        added = await ca.post(
            "/agents/remote",
            json={"name": "desk", "base_url": door_url, "kind": "http-task", "token": door_token},
        )
        assert added.status_code == 200, added.text
        reg = added.json()
        assert reg["another_jarvis"] is True
        assert reg["inbound_enabled"] is True, "its answer path is turned on with it"
        assert reg["inbound_url"] == f"http://{A_HOST}/agents/remote/desk/inbound"
        assert reg["reachable"]["host_allowed"] is True
        assert "token" not in reg

        # A's Test asks the door — and starts nothing on B.
        test = (await ca.post("/agents/remote/desk/test")).json()
        assert test["ok"] is True and "takes tasks" in test["detail"]
        assert _remote_sessions(b) == []

        # On A: a saved chat, then "@desk do X" from the chat's panel.
        made = await ca.put(
            "/chat/threads/new",
            json={"messages": [{"role": "user", "content": "@desk read the Q3 notes"}]},
        )
        tid = made.json()["id"]
        r = await ca.post(
            "/chat/panel",
            json={
                "message": "@desk read the Q3 notes",
                "thread_id": tid,
                "chat_thread_id": tid,
                "history": [{"who": "user", "content": "@desk read the Q3 notes"}],
            },
        )
        assert r.status_code == 200, r.text
        panel = r.json()
        assert panel["mode"] == "panel" and panel["spoke"] == ["remote:desk"]
        pending = panel["entries"][-1]
        assert pending["pending"] is True and "will report back" in pending["content"]
        room_id = panel["thread_id"]

        # The task reached B's door with B's token, the http-task body, and
        # A's answer address + key.
        to_door = [c for c in net.calls if c["method"] == "POST" and c["url"] == door_url]
        assert len(to_door) == 1
        assert to_door[0]["auth"] == f"Bearer {door_token}"
        sent = json.loads(to_door[0]["body"])
        assert set(sent) == {"task", "conversation_id", "history", "reply_to", "reply_token"}
        assert sent["conversation_id"] == room_id
        assert sent["reply_to"] == reg["inbound_url"]
        desk = RemoteAgentRegistry(a.state.platform.engine).get("desk")
        assert sent["reply_token"] == a.state.platform.secrets.get(desk.inbound_secret_name)

        # B runs it (on its scripted model) and the answer goes home.
        rows = await _settled(b)
        assert len(rows) == 1 and rows[0].status is SessionStatus.COMPLETED
        assert rows[0].origin == "remote:laptop" and rows[0].trust == "low"
        assert "read the Q3 notes" in rows[0].task

        # A's chat has desk's answer — attributed, in the chat it was asked in.
        thread = (await ca.get(f"/chat/threads/{tid}")).json()
        homed = [m for m in thread["messages"] if m.get("panelWho") == "remote:desk"]
        assert homed, thread["messages"]
        assert homed[-1]["content"] == "Desk read the notes: three action items."
        assert homed[-1]["panelKind"] == "done"
        # ...and the room has the inbound line.
        room = AgentThreads(a.state.platform.engine).get(room_id)
        entries = json.loads(room.messages_json)
        assert entries[-1]["inbound"] is True and entries[-1]["who"] == "remote:desk"

        # The reply went with A's inbound key; B no longer holds it; B's door
        # says the answer was sent.
        replies = [c for c in net.calls if c["url"] == reg["inbound_url"]]
        assert len(replies) == 1 and replies[0]["auth"] == f"Bearer {sent['reply_token']}"
        assert b.state.platform.secrets.get(td.REPLY_TOKEN_PREFIX + rows[0].id) is None
        status = (await cb.get("/agents/task-door")).json()
        assert status["last_reply"]["ok"] is True and status["last_task_at"]
        assert "Could not send" not in (rows[0].summary or "")


# --------------------------------------------------------------------------- #
# D6 — a failed answer is recorded, named, attempted once
# --------------------------------------------------------------------------- #


async def test_a_failed_answer_is_recorded_and_named_and_never_retried(tmp_path, lan):
    b = create_app(str(tmp_path / "b"))
    net = lan({B_HOST: b})  # the sender's computer cannot be reached
    b.state.platform.router.stream = _scripted("Done here.")
    async with _client(B_HOST) as cb:
        token = (await cb.post("/agents/task-door/enable", json={"name": "laptop"})).json()["token"]
        r = await cb.post(
            TASK_DOOR_PATH,
            json={
                "task": "do it",
                "conversation_id": "athr_9",
                "reply_to": "http://gone-pc:8787/agents/remote/desk/inbound",
                "reply_token": "secret-reply-key-123",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 202
        rows = await _settled(b)
        status = (await cb.get("/agents/task-door")).json()
    row = rows[0]
    assert row.status is SessionStatus.COMPLETED
    note = td.reply_failed_note("laptop", "")
    assert note.split(":")[0] in (row.summary or ""), row.summary
    assert "laptop" in row.summary and "could not be reached" in row.summary
    assert "secret-reply-key-123" not in (row.summary or "")
    assert status["last_reply"]["ok"] is False
    assert "could not be reached" in status["last_reply"]["detail"]
    # ONE attempt
    attempts = [c for c in net.calls if "gone-pc" in c["url"]]
    assert len(attempts) == 1
    # a second terminal path asking again records nothing new
    again = await td.send_reply(b.state.platform, row)
    assert again == {"ok": False, "sent": False, "detail": "already attempted"}
    assert len([c for c in net.calls if "gone-pc" in c["url"]]) == 1


async def test_a_refused_answer_is_named_in_plain_words(tmp_path, lan):
    a = create_app(str(tmp_path / "a"))
    b = create_app(str(tmp_path / "b"))
    lan({A_HOST: a, B_HOST: b})
    b.state.platform.router.stream = _scripted("Done.")
    async with _client(B_HOST) as cb:
        token = (await cb.post("/agents/task-door/enable", json={"name": "laptop"})).json()["token"]
        r = await cb.post(
            TASK_DOOR_PATH,
            json={
                "task": "do it",
                "conversation_id": "athr_9",
                # A has no remote called desk with message-back on: refused there
                "reply_to": f"http://{A_HOST}/agents/remote/desk/inbound",
                "reply_token": "wrong",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 202
        rows = await _settled(b)
    summary = rows[0].summary or ""
    assert "Could not send this answer back to laptop" in summary
    assert "no longer letting this one message back" in summary
    assert "403" not in summary and "HTTP" not in summary


def test_a_restart_cut_task_still_tells_the_sender(tmp_path, lan):
    a = create_app(str(tmp_path / "a"))
    b = create_app(str(tmp_path / "b"))
    net = lan({A_HOST: a, B_HOST: b})
    orch = b.state.orchestrator
    session = asyncio.run(
        orch.create_session(
            "a task from laptop",
            AgentType.SUPERVISOR,
            origin=td.remote_origin("laptop"),
            trust="low",
            options={
                td.REPLY_OPTION: td.reply_spec(
                    reply_to=f"http://{A_HOST}/agents/remote/desk/inbound",
                    conversation_id="athr_r",
                    caller="laptop",
                )
            },
        )
    )
    td.store_reply_token(b.state.platform.secrets, session.id, "k", "laptop")
    assert orch.reconcile_interrupted_sessions() >= 1
    asyncio.run(td.drain_replies())
    replies = [c for c in net.calls if c["url"].endswith("/agents/remote/desk/inbound")]
    assert len(replies) == 1
    assert json.loads(replies[0]["body"])["message"] == RESTART_SUMMARY


async def test_a_run_with_no_reply_address_sends_nothing(tmp_path, lan):
    b = create_app(str(tmp_path / "b"))
    net = lan({B_HOST: b})
    b.state.platform.router.stream = _scripted("Done.")
    async with _client(B_HOST) as cb:
        token = (await cb.post("/agents/task-door/enable", json={})).json()["token"]
        r = await cb.post(
            TASK_DOOR_PATH, json={"task": "fire and forget"}, headers={"Authorization": f"Bearer {token}"}
        )
        assert r.status_code == 202
        rows = await _settled(b)
    assert rows[0].origin == "remote:another computer"
    assert [c for c in net.calls if "/inbound" in c["url"]] == []
    assert "Could not send" not in (rows[0].summary or "")


# --------------------------------------------------------------------------- #
# D7 — the sending side
# --------------------------------------------------------------------------- #


def _capture_posts(monkeypatch, responder) -> list:
    captured: list = []

    class _Resp:
        def __init__(self, status, payload=None):
            self.status_code = status
            self._payload = payload
            self.text = json.dumps(payload) if payload is not None else ""

        def json(self):
            return self._payload

    async def fake_post(self, url, json=None, headers=None, **kw):
        captured.append({"url": url, "json": json, "headers": headers or {}})
        status, payload = responder(url, json)
        return _Resp(status, payload)

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    return captured


def test_the_reply_key_rides_only_to_another_jarvis(tmp_path, monkeypatch):
    app = create_app(str(tmp_path))
    client = TestClient(app)
    captured = _capture_posts(monkeypatch, lambda url, body: (202, None))
    client.post(
        "/agents/remote",
        json={"name": "hermes", "base_url": "http://192.168.1.50:8080/run", "kind": "http-task"},
    )
    client.post("/agents/remote/hermes/inbound/enable", json={})
    client.post(
        "/agents/remote",
        json={"name": "desk", "base_url": f"http://192.168.1.60:8787{TASK_DOOR_PATH}", "token": "t"},
    )
    for who in ("hermes", "desk"):
        client.post("/chat/panel", json={"message": f"@{who} go", "chat_thread_id": f"c-{who}"})
    by_url = {c["url"]: c["json"] for c in captured}
    hermes = by_url["http://192.168.1.50:8080/run"]
    assert "reply_to" in hermes and "reply_token" not in hermes, "a non-Jarvis remote's body is unchanged"
    desk = by_url[f"http://192.168.1.60:8787{TASK_DOOR_PATH}"]
    rec = RemoteAgentRegistry(app.state.platform.engine).get("desk")
    assert desk["reply_token"] == app.state.platform.secrets.get(rec.inbound_secret_name)


def test_a_local_only_chat_tells_the_other_jarvis_and_it_refuses_a_cloud_model(
    tmp_path, monkeypatch
):
    """A local-only chat (v1.332.0) may @-mention a Jarvis on this network —
    but the model THAT computer answers with might not be. The sender says
    ``local_only`` (to a task door only); the door makes the run local only,
    so ``create_session`` refuses a model off its network, in words, and
    nothing is created."""
    a = create_app(str(tmp_path / "a"))
    client = TestClient(a)
    captured = _capture_posts(monkeypatch, lambda url, body: (202, None))
    client.post(
        "/agents/remote",
        json={"name": "desk", "base_url": f"http://192.168.1.60:8787{TASK_DOOR_PATH}", "token": "t"},
    )
    client.post(
        "/agents/remote",
        json={"name": "hermes", "base_url": "http://192.168.1.50:8080/run", "kind": "http-task"},
    )
    for who in ("desk", "hermes"):
        client.post(
            "/chat/panel",
            json={"message": f"@{who} go", "chat_thread_id": f"c-{who}", "local_only": True},
        )
    by_url = {c["url"]: c["json"] for c in captured}
    assert by_url[f"http://192.168.1.60:8787{TASK_DOOR_PATH}"]["local_only"] is True
    assert "local_only" not in by_url["http://192.168.1.50:8080/run"], "only a door hears it"
    # not local only → no key, even for a door
    captured.clear()
    client.post("/chat/panel", json={"message": "@desk again", "chat_thread_id": "c-desk2"})
    assert "local_only" not in captured[-1]["json"]
    monkeypatch.undo()

    # THE DOOR: a run that would leave this computer's network is refused
    b = create_app(str(tmp_path / "b"))
    b.state.platform.router.stream = _scripted("ok")
    bc = TestClient(b)
    token = bc.post("/agents/task-door/enable", json={"name": "laptop"}).json()["token"]
    auth = {"Authorization": f"Bearer {token}"}
    b.state.platform.config.default_provider = "openai"
    r = bc.post(TASK_DOOR_PATH, json={"task": "x", "local_only": True}, headers=auth)
    assert r.status_code == 409, r.text
    assert r.json()["code"] == "local_only"
    assert "local only" in r.json()["detail"] and "was not run here" in r.json()["detail"]
    assert _remote_sessions(b) == []
    # the same task NOT local only is taken (the sender's chat decides)
    r = bc.post(TASK_DOOR_PATH, json={"task": "x"}, headers=auth)
    assert r.status_code == 202
    # a local model takes a local-only task, and the run carries the scope
    b.state.platform.config.default_provider = "mock"
    r = bc.post(TASK_DOOR_PATH, json={"task": "y", "local_only": True}, headers=auth)
    assert r.status_code == 202
    rows = [s for s in _remote_sessions(b) if s.task.endswith("y")]
    assert rows and json.loads(rows[0].options_json).get("local_only") is True


def test_the_body_without_a_reply_is_still_the_old_body(tmp_path, monkeypatch):
    app = create_app(str(tmp_path))
    captured = _capture_posts(monkeypatch, lambda url, body: (200, {"result": "ok"}))
    reg = RemoteAgentRegistry(app.state.platform.engine)
    rec = reg.upsert("desk", f"http://192.168.1.60:8787{TASK_DOOR_PATH}", "http-task")
    asyncio.run(reg.run(rec, "do it", lambda n: None, reply_token="k"))
    assert captured[-1]["json"] == {"task": "do it"}, "no reply_to → no key, ever"


def test_a_doors_refusal_is_said_in_words(tmp_path, monkeypatch):
    app = create_app(str(tmp_path))
    reg = RemoteAgentRegistry(app.state.platform.engine)
    rec = reg.upsert("desk", f"http://192.168.1.60:8787{TASK_DOOR_PATH}", "http-task")
    cases = {
        403: ({"detail": td.DOOR_OFF}, f"desk turned the task away: {td.DOOR_OFF}"),
        409: (
            {"detail": EMERGENCY_STOP_REFUSAL, "code": "emergency_stop"},
            f"desk turned the task away: {EMERGENCY_STOP_REFUSAL}",
        ),
    }
    for status, (payload, words) in cases.items():
        _capture_posts(monkeypatch, lambda url, body, s=status, p=payload: (s, p))
        out = asyncio.run(reg.run(rec, "x", lambda n: None))
        assert out == {"ok": False, "result": "", "detail": words}
    _capture_posts(monkeypatch, lambda url, body: (401, {"detail": "nope"}))
    out = asyncio.run(reg.run(rec, "x", lambda n: None))
    assert "token does not match" in out["detail"] and "401" not in out["detail"]
    # a NON-door remote keeps its old wording, byte for byte
    other = reg.upsert("hermes", "http://192.168.1.50:8080/run", "http-task")
    _capture_posts(monkeypatch, lambda url, body: (403, {"detail": "x"}))
    out = asyncio.run(reg.run(other, "x", lambda n: None))
    assert out["detail"].startswith("remote returned HTTP 403")


async def test_test_asks_the_door_and_starts_nothing(tmp_path, lan):
    a = create_app(str(tmp_path / "a"))
    b = create_app(str(tmp_path / "b"))
    net = lan({A_HOST: a, B_HOST: b})
    async with _client(A_HOST) as ca, _client(B_HOST) as cb:
        await ca.post(
            "/agents/remote",
            json={"name": "desk", "base_url": f"http://{B_HOST}{TASK_DOOR_PATH}", "token": "wrong"},
        )
        off = (await ca.post("/agents/remote/desk/test")).json()
        assert off["ok"] is False and td.DOOR_OFF in off["detail"]
        await cb.post("/agents/task-door/enable", json={})
        bad = (await ca.post("/agents/remote/desk/test")).json()
        assert bad["ok"] is False and "token does not match" in bad["detail"]
    assert [c["method"] for c in net.calls if TASK_DOOR_PATH in c["url"]] == ["GET", "GET"]
    assert _remote_sessions(b) == []


def test_the_form_refuses_a_chat_dialect_for_a_door(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    r = client.post(
        "/agents/remote",
        json={"name": "desk", "base_url": f"http://b:8787{TASK_DOOR_PATH}", "kind": "openai-chat"},
    )
    assert r.status_code == 400 and "http-task" in r.json()["detail"]
    ok = client.post(
        "/agents/remote",
        json={"name": "desk", "base_url": f"http://b:8787{TASK_DOOR_PATH}", "token": "orig"},
    )
    assert ok.status_code == 200
    refused = client.patch(
        "/agents/remote/desk", json={"kind": "openai-responses", "token": "replaced"}
    )
    assert refused.status_code == 400
    # a refused edit never replaced the stored token
    vault = client.app.state.platform.secrets
    assert vault.get("remote_agent_desk") == "orig"
    # a plain remote is untouched by the rule, and gets no Jarvis keys
    plain = client.post(
        "/agents/remote", json={"name": "gpt", "base_url": "http://x:1/v1", "kind": "openai-chat"}
    ).json()
    assert "another_jarvis" not in plain and plain["inbound_enabled"] is False


def test_the_reachability_sentence_says_iron_jarvis_and_opens_nothing(tmp_path, monkeypatch):
    monkeypatch.delenv("IRONJARVIS_HOST_ALLOWLIST", raising=False)
    client = TestClient(create_app(str(tmp_path)))
    out = client.post(
        "/agents/task-door/enable", json={"url": f"http://192.168.1.20:8787{TASK_DOOR_PATH}"}
    ).json()
    reach = out["reachable"]
    assert reach["host_allowed"] is False
    note = reach["note"]
    assert note.startswith("Iron Jarvis on this computer")
    assert "daemon" not in note.lower()
    assert "IRONJARVIS_HOST_ALLOWLIST=192.168.1.20" in note and "--host 192.168.1.20" in note
    assert "the other computer needs the same" in note
    import os

    assert "IRONJARVIS_HOST_ALLOWLIST" not in os.environ, "nothing was opened on the user's behalf"
    monkeypatch.setenv("IRONJARVIS_HOST_ALLOWLIST", "192.168.1.20")
    again = client.post(
        "/agents/task-door/enable", json={"url": f"http://192.168.1.20:8787{TASK_DOOR_PATH}"}
    ).json()
    assert again["reachable"] == {"host_allowed": True, "note": ""}


# --------------------------------------------------------------------------- #
# Review (v1.357.0 lane 7A review): the paths the first pins left open
# --------------------------------------------------------------------------- #


async def _door_task(cb: httpx.AsyncClient, token: str, **body: Any) -> None:
    r = await cb.post(TASK_DOOR_PATH, json=body, headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 202, r.text


async def test_a_run_that_crashes_still_tells_the_sender(tmp_path, lan, monkeypatch):
    """The FAILURE finalize (an exception escaping the run, not a model
    answer that failed) answers home too, with the run's failure sentence."""
    from iron_jarvis.agents import orchestrator as orch_mod

    a = create_app(str(tmp_path / "a"))
    b = create_app(str(tmp_path / "b"))
    net = lan({A_HOST: a, B_HOST: b})

    async def boom(platform, session):
        raise RuntimeError("the supervisor fell over")

    monkeypatch.setattr(orch_mod, "run_supervised", boom)
    async with _client(B_HOST) as cb:
        token = (await cb.post("/agents/task-door/enable", json={"name": "laptop"})).json()["token"]
        await _door_task(
            cb,
            token,
            task="do it",
            conversation_id="athr_f",
            reply_to=f"http://{A_HOST}/agents/remote/desk/inbound",
            reply_token="k",
        )
        rows = await _settled(b)
    assert rows[0].status is SessionStatus.FAILED
    replies = [c for c in net.calls if c["url"].endswith("/agents/remote/desk/inbound")]
    assert len(replies) == 1, "a crashed run still answers the computer that asked"
    sent = json.loads(replies[0]["body"])
    assert sent["kind"] == "done" and sent["conversation_id"] == "athr_f"
    assert sent["message"] and (rows[0].summary or "").startswith(sent["message"])


async def test_the_answer_is_masked_on_the_way_home(tmp_path, lan):
    """A secret in the run's answer never crosses to the other computer."""
    b = create_app(str(tmp_path / "b"))
    a = create_app(str(tmp_path / "a"))
    net = lan({A_HOST: a, B_HOST: b})
    orch = b.state.orchestrator
    key = "sk-ant-api03-ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789abcd"
    session = await orch.create_session(
        "a task from laptop",
        AgentType.SUPERVISOR,
        origin=td.remote_origin("laptop"),
        trust="low",
        options={
            td.REPLY_OPTION: td.reply_spec(
                reply_to=f"http://{A_HOST}/agents/remote/desk/inbound",
                conversation_id="athr_m",
                caller="laptop",
            )
        },
    )
    session.status = SessionStatus.COMPLETED
    session.summary = f"Here is the key you asked for: {key}"
    await asyncio.to_thread(td.store_reply_token, b.state.platform.secrets, session.id, "k", "laptop")
    await td.send_reply(b.state.platform, session)
    replies = [c for c in net.calls if c["url"].endswith("/agents/remote/desk/inbound")]
    assert len(replies) == 1
    message = json.loads(replies[0]["body"])["message"]
    assert key not in message and "ABCDEFGHIJKLMNOP" not in message
    assert "sk-ant-***" in message


async def test_a_follow_up_on_a_remote_task_never_reuses_its_answer_address(tmp_path, lan):
    """The answer key a task brought is single-use and belongs to THAT task:
    a rerun or a Continue on this computer (the restart Continue included)
    starts no second answer and records no failure the user did not cause."""
    b = create_app(str(tmp_path / "b"))
    net = lan({B_HOST: b})
    b.state.platform.router.stream = _scripted("Done.")
    async with _client(B_HOST) as cb:
        token = (await cb.post("/agents/task-door/enable", json={"name": "laptop"})).json()["token"]
        await _door_task(
            cb,
            token,
            task="do it",
            conversation_id="athr_c",
            reply_to="http://gone-pc:8787/agents/remote/desk/inbound",
            reply_token="k",
        )
        first = (await _settled(b))[0]
        before = (await cb.get("/agents/task-door")).json()["last_reply"]
        orch = b.state.orchestrator
        again = await orch.rerun_session(first.id)
        follow = await orch.continue_session(first.id, "and one more thing")
        for s in (again, follow):
            assert td.REPLY_OPTION not in json.loads(s.options_json or "{}")
            # still the same door's task: its origin and trust carry
            assert s.origin == first.origin and s.trust == "low"
        await orch.run_session(again.id)
        await orch.run_session(follow.id)
        await td.drain_replies()
        after = (await cb.get("/agents/task-door")).json()["last_reply"]
    assert len([c for c in net.calls if "gone-pc" in c["url"]]) == 1
    assert after == before, "the tile's last answer is still the task's own"
    for sid in (again.id, follow.id):
        assert "Could not send" not in (_row(b, sid).summary or "")


async def test_a_queued_remote_task_that_is_cancelled_still_tells_the_sender(tmp_path, lan):
    """With a cap set, a second task waits QUEUED; cancelling it there (the
    user's Cancel, or Emergency stop) still answers the computer that asked."""
    a = create_app(str(tmp_path / "a"))
    b = create_app(str(tmp_path / "b"))
    net = lan({A_HOST: a, B_HOST: b})
    b.state.platform.config.max_concurrent_sessions = 1
    started = asyncio.Event()

    async def hanging(**kw):
        started.set()
        await asyncio.Event().wait()
        yield  # pragma: no cover

    b.state.platform.router.stream = hanging
    orch = b.state.orchestrator
    inbound = f"http://{A_HOST}/agents/remote/desk/inbound"
    async with _client(B_HOST) as cb:
        token = (await cb.post("/agents/task-door/enable", json={"name": "laptop"})).json()["token"]
        await _door_task(
            cb, token, task="first", conversation_id="athr_1", reply_to=inbound, reply_token="k1"
        )
        await asyncio.wait_for(started.wait(), 10)
        await _door_task(
            cb, token, task="second", conversation_id="athr_2", reply_to=inbound, reply_token="k2"
        )
        queued = None
        for _ in range(200):
            rows = await asyncio.to_thread(_remote_sessions, b)
            queued = next((r for r in rows if r.status is SessionStatus.QUEUED), None)
            if queued is not None:
                break
            await asyncio.sleep(0.05)
        assert queued is not None, "the cap parks the second task"
        running = next(r for r in rows if r.status is SessionStatus.ACTIVE)
        await asyncio.to_thread(orch.cancel_session, queued.id)
        orch.cancel_session(running.id)
        await _settled(b, count=2)
    by_conv = {
        json.loads(c["body"])["conversation_id"]: json.loads(c["body"])
        for c in net.calls
        if c["url"] == inbound
    }
    assert set(by_conv) == {"athr_1", "athr_2"}, by_conv
    assert by_conv["athr_2"]["kind"] == "done"
    assert "never started" in by_conv["athr_2"]["message"]


def test_the_directions_name_the_tab_the_tile_and_the_form_live_on(tmp_path, monkeypatch):
    """Agents › Your team opens on the roster; the tile and the connect form
    sit on its "New & manage" tab. Every sentence that sends a user there
    names that tab by the label the page really draws (read from the page's
    source, so a renamed tab breaks this, not a user's search)."""
    import re as _re
    from pathlib import Path

    src = (
        Path(__file__).resolve().parents[1] / "dashboard/components/agents/AgentsModal.tsx"
    ).read_text(encoding="utf-8").replace("\r\n", "\n")
    label = _re.search(r'tabBtn\("manage", "([^"]+)"\)', src).group(1)
    assert td.DOOR_OFF.count(f"Your team, {label}, Another computer running Jarvis") == 1
    reg = RemoteAgentRegistry(create_app(str(tmp_path)).state.platform.engine)
    rec = reg.upsert("desk", f"http://192.168.1.60:8787{TASK_DOOR_PATH}", "http-task")
    _capture_posts(monkeypatch, lambda url, body: (401, {"detail": "nope"}))
    out = asyncio.run(reg.run(rec, "x", lambda n: None))
    assert f"Your team, {label}, Another computer running Jarvis" in out["detail"]
