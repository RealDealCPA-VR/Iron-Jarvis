"""Your other computers: pairing two PCs running Iron Jarvis (v1.497.0).

The vision plan's leap 7, Phase 1. Pinned through the REAL code: two real
``create_app`` factories in one process, their real middleware (Host guard,
install bearer), the real routes, the real vault and the real task door. One
stand-in only: the network between the two "computers" (an httpx transport that
hands each request to the app whose address it names, from the other one's
address, the way the LAN would), and the desk's model (a scripted stream).

  P1 the two doors the other computer calls are exempt for exactly
     themselves; everything else under /peers needs the install token.
  P2 the code's rules: six digits, ten minutes, once, five wrong tries end it,
     ten tries a minute, a new one replaces the old, memory only.
  P3 pairing makes both sides at once: a record, two tokens that cross-match,
     a remote agent with message-back on; nothing secret is ever shown.
  P4 THE ACCEPTANCE: "@desk do X" on the laptop runs on the desk, at low trust,
     and the answer comes home to the laptop's chat. Both directions.
  P5 every refusal is in words and keeps nothing.
  P6 Remove ends it on both sides (or says it could not tell the other one),
     and the old tokens stop working at once.
  P7 Check says whether the other computer still answers, in words.
  P8 a paired remote agent is changed or removed only where the pairing lives.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import pytest

from iron_jarvis.agents import task_door as td
from iron_jarvis.agents.remote import RemoteAgentRegistry
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import Session, SessionStatus
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.auth import PEER_PAIR_PATH, PEER_UNPAIR_PATH, TASK_DOOR_PATH, _is_exempt
from iron_jarvis.daemon.routes import peers as R
from iron_jarvis.peers import codes as C
from iron_jarvis.peers import link as L
from iron_jarvis.peers import store as S
from iron_jarvis.peers.models import PeerRecord
from iron_jarvis.providers.adapters.base import LLMResponse

INSTALL = "install-bearer-for-both-computers"
AUTH = {"Authorization": f"Bearer {INSTALL}"}
A_IP, B_IP = "10.0.0.1", "10.0.0.2"
A_HOST, B_HOST = f"{A_IP}:8787", f"{B_IP}:8787"


# --------------------------------------------------------------------------- #
# harness: two computers, one network
# --------------------------------------------------------------------------- #


class _Network(httpx.AsyncBaseTransport):
    """The LAN between two computers: a request goes to the app whose address
    it names and arrives FROM the other computer's address (with two
    computers, a request to one always comes from the other). Any other
    address cannot be reached (a ConnectError, what a dead address gives)."""

    def __init__(self, apps: dict[str, Any], senders: dict[str, str]) -> None:
        self.apps = {
            host: httpx.ASGITransport(app=app, client=(senders[host], 50000))
            for host, app in apps.items()
        }
        self.calls: list[dict[str, Any]] = []
        self.down: set[str] = set()

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
        if transport is None or host in self.down:
            raise httpx.ConnectError(f"no route to {host}", request=request)
        return await transport.handle_async_request(request)


@pytest.fixture()
def lan(monkeypatch):
    """Both computers open to each other ON PURPOSE (the allowlist the
    desktop app sets while "Let other computers…" is on), the install bearer
    on (as the packaged app runs), and every ``httpx.AsyncClient`` riding the
    stand-in network."""
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


def _two(tmp_path) -> tuple[Any, Any]:
    a = create_app(str(tmp_path / "laptop"))  # the computer the user types the code on
    b = create_app(str(tmp_path / "desk"))  # the computer that shows the code
    return a, b


async def _pair(ca, cb, *, a_name="laptop", b_name="desk", call_it="") -> dict[str, Any]:
    """B shows a code; A types B's address and the code. Returns A's answer."""
    shown = (await cb.post("/peers/code", json={"this_name": b_name})).json()
    code = shown["code"]["code"]
    r = await ca.post(
        "/peers/join",
        json={"address": B_HOST, "code": code, "name": call_it, "this_name": a_name},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _peers(app) -> list[PeerRecord]:
    return S.store_for(app.state.platform).list()


def _secret(app, name: str) -> str | None:
    return app.state.platform.secrets.get(name)


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


def _scripted(answer: str):
    async def fake_stream(**kw):
        yield {
            "type": "final",
            "response": LLMResponse(text=answer),
            "provider": "openai",
            "model": "gpt-5",
        }

    return fake_stream


def _all_secret_values(*apps) -> list[str]:
    values: list[str] = []
    for app in apps:
        sec = app.state.platform.secrets
        for row in sec.list():
            v = sec.get(row["name"])
            if v and len(v) >= 20:
                values.append(v)
    return values


# --------------------------------------------------------------------------- #
# P1 — the exempt doors
# --------------------------------------------------------------------------- #


def test_the_two_doors_are_exempt_and_nothing_else_under_peers_is():
    assert PEER_PAIR_PATH == "/peers/pair" and PEER_UNPAIR_PATH == "/peers/unpair"
    assert L.PAIR_PATH == PEER_PAIR_PATH and L.UNPAIR_PATH == PEER_UNPAIR_PATH
    assert _is_exempt(PEER_PAIR_PATH) and _is_exempt(PEER_UNPAIR_PATH)
    for guarded in (
        "/peers",
        "/peers/code",
        "/peers/code/cancel",
        "/peers/join",
        "/peers/check",
        "/peers/desk",
        "/peers/desk/check",
        "/peers/pair/",
        "/peers/pairx",
        "/peers/unpair/x",
        "/x/peers/pair",
    ):
        assert not _is_exempt(guarded), guarded


async def test_the_controls_need_the_install_token(tmp_path, lan):
    a, b = _two(tmp_path)
    lan({A_HOST: a, B_HOST: b})
    async with httpx.AsyncClient(base_url=f"http://{B_HOST}") as bare:
        for method, path in (
            ("GET", "/peers"),
            ("POST", "/peers/code"),
            ("POST", "/peers/code/cancel"),
            ("POST", "/peers/join"),
            ("POST", "/peers/check"),
            ("POST", "/peers/desk/check"),
            ("DELETE", "/peers/desk"),
            # The two doors are exempt for POST only: a DELETE there would
            # reach DELETE /peers/{name} for a pairing named "pair" (v1.499.1).
            ("DELETE", PEER_PAIR_PATH),
            ("DELETE", PEER_UNPAIR_PATH),
            ("GET", PEER_PAIR_PATH),
            ("PATCH", PEER_UNPAIR_PATH),
        ):
            r = await bare.request(method, path, json={})
            assert r.status_code == 401, (method, path, r.status_code)
        # The door the other computer calls answers WITHOUT it (and refuses
        # by the code instead): no code is showing.
        r = await bare.post(PEER_PAIR_PATH, json={"protocol": 1, "code": "123456", "port": 8787,
                                                  "door_token": "x" * 43})
        assert r.status_code == 403 and r.json()["detail"] == R.NOT_SHOWING


# --------------------------------------------------------------------------- #
# P2 — the code's rules
# --------------------------------------------------------------------------- #


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_a_code_is_six_digits_shown_in_two_groups_and_typed_loosely():
    codes = C.PairingCodes()
    code, ends = codes.make()
    assert len(code) == 6 and code.isdigit()
    assert C.shown(code) == f"{code[:3]} {code[3:]}"
    assert ends > datetime.now(timezone.utc) + timedelta(minutes=9)
    for typed in (code, f"{code[:3]} {code[3:]}", f"{code[:3]}-{code[3:]}", f" {code} "):
        assert C.normalize(typed) == code
    for bad in ("", "12345", "1234567", "abcdef", "１２３４５６", None, 123456):
        assert C.normalize(bad) == "" or bad == 123456  # an int is typed as its digits
    assert C.normalize(123456) == "123456"


def test_a_code_works_once():
    codes = C.PairingCodes()
    code, _ = codes.make()
    assert codes.check(code) == C.OK
    assert codes.check(code) == C.NO_CODE
    assert codes.active() is None


def test_five_wrong_tries_end_a_code():
    codes = C.PairingCodes()
    code, _ = codes.make()
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(C.MAX_WRONG - 1):
        assert codes.check(wrong) == C.WRONG
    assert codes.check(wrong) == C.USED_UP
    assert codes.check(code) == C.NO_CODE, "the right code no longer works after five wrong ones"


def test_a_malformed_try_counts_as_wrong_and_never_matches():
    codes = C.PairingCodes()
    code, _ = codes.make()
    assert codes.check("letters") == C.WRONG
    assert codes.check("") == C.WRONG
    assert codes.check(code) == C.OK


def test_a_code_runs_out_after_ten_minutes():
    clock = _Clock()
    codes = C.PairingCodes(clock=clock)
    code, _ = codes.make()
    clock.now += C.CODE_TTL_S - 1
    assert codes.active() is not None
    clock.now += 1
    assert codes.active() is None
    assert codes.check(code) == C.EXPIRED
    assert codes.check(code) == C.NO_CODE


def test_a_new_code_replaces_the_old_and_cancel_drops_it():
    codes = C.PairingCodes()
    first, _ = codes.make()
    second, _ = codes.make()
    if first != second:
        assert codes.check(first) == C.WRONG
    assert codes.check(second) == C.OK
    codes.make()
    codes.cancel()
    assert codes.active() is None


def test_ten_tries_a_minute_from_anyone():
    clock = _Clock()
    codes = C.PairingCodes(clock=clock)
    for _ in range(C.TRIES_PER_MINUTE):
        assert codes.take_try()
    assert not codes.take_try()
    clock.now += 61
    assert codes.take_try()


async def test_a_restart_forgets_the_code(tmp_path, lan):
    a, b = _two(tmp_path)
    lan({A_HOST: a, B_HOST: b})
    async with _client(B_HOST) as cb:
        made = (await cb.post("/peers/code")).json()
        assert made["code"] and len(made["code"]["code"]) == 6
    again = create_app(str(tmp_path / "desk"))
    net = lan({A_HOST: a, B_HOST: again})
    assert net
    async with _client(B_HOST) as cb:
        assert (await cb.get("/peers")).json()["code"] is None


# --------------------------------------------------------------------------- #
# P3 — both sides at once
# --------------------------------------------------------------------------- #


async def test_pairing_makes_both_sides_and_the_tokens_cross_match(tmp_path, lan):
    a, b = _two(tmp_path)
    lan({A_HOST: a, B_HOST: b})
    async with _client(A_HOST) as ca, _client(B_HOST) as cb:
        out = await _pair(ca, cb)
        assert out["said"] == R.paired_words("desk")
        assert out["peer"]["name"] == "desk" and out["peer"]["their_name"] == "desk"
        assert out["peer"]["address"] == f"http://{B_HOST}"

        (pa,) = _peers(a)
        (pb,) = _peers(b)
        assert (pa.name, pa.address) == ("desk", f"http://{B_HOST}")
        assert (pb.name, pb.address) == ("laptop", f"http://{A_HOST}")
        # Each side's "in" token is the other side's "out" token.
        assert _secret(a, pa.in_secret) and _secret(a, pa.in_secret) == _secret(b, pb.out_secret)
        assert _secret(b, pb.in_secret) and _secret(b, pb.in_secret) == _secret(a, pa.out_secret)
        assert _secret(a, pa.in_secret) != _secret(b, pb.in_secret), "two tokens, one each way"

        # Each side has a remote agent for the other: its door, the out token,
        # message-back on with answers posted to ITS OWN address as the other
        # one reaches it.
        ra = RemoteAgentRegistry(a.state.platform.engine).get("desk")
        rb = RemoteAgentRegistry(b.state.platform.engine).get("laptop")
        assert ra.base_url == f"http://{B_HOST}{TASK_DOOR_PATH}" and ra.kind == "http-task"
        assert rb.base_url == f"http://{A_HOST}{TASK_DOOR_PATH}"
        assert ra.secret_name == pa.out_secret and rb.secret_name == pb.out_secret
        assert ra.inbound_enabled and ra.inbound_url == f"http://{A_HOST}/agents/remote/desk/inbound"
        assert rb.inbound_enabled and rb.inbound_url == f"http://{B_HOST}/agents/remote/laptop/inbound"

        # The code is used up on B, and B's screen lists the laptop.
        view = (await cb.get("/peers")).json()
        assert view["code"] is None
        assert [p["name"] for p in view["peers"]] == ["laptop"]
        assert view["this_name"] == "desk"
        assert (await ca.get("/peers")).json()["this_name"] == "laptop"


async def test_no_answer_ever_carries_a_token(tmp_path, lan, caplog):
    a, b = _two(tmp_path)
    net = lan({A_HOST: a, B_HOST: b})
    caplog.set_level(logging.DEBUG)
    async with _client(A_HOST) as ca, _client(B_HOST) as cb:
        out = await _pair(ca, cb)
        listed_a = (await ca.get("/peers")).text
        listed_b = (await cb.get("/peers")).text
        checked = (await ca.post("/peers/check")).text
        remotes = (await ca.get("/agents/remote")).text
    secrets_now = _all_secret_values(a, b)
    assert len(secrets_now) >= 4
    shown = json.dumps(out) + listed_a + listed_b + checked + remotes + caplog.text
    events = json.dumps(
        [e.payload for app in (a, b) for e in app.state.platform.event_bus.history]
    )
    for value in secrets_now:
        assert value not in shown, "a token reached an answer or the log"
        assert value not in events, "a token reached an event"
    # The tokens travel only where they must: in the pairing call and its answer.
    pair_calls = [c for c in net.calls if c["url"].endswith(PEER_PAIR_PATH)]
    assert len(pair_calls) == 1 and pair_calls[0]["auth"] == ""


async def test_pairing_is_said_on_both_computers(tmp_path, lan):
    a, b = _two(tmp_path)
    lan({A_HOST: a, B_HOST: b})
    async with _client(A_HOST) as ca, _client(B_HOST) as cb:
        await _pair(ca, cb)
    for app, other in ((a, "desk"), (b, "laptop")):
        paired = [e for e in app.state.platform.event_bus.history if e.type == "peer.paired"]
        assert [e.payload for e in paired] == [{"name": other}]


async def test_what_to_call_the_other_computer_can_be_typed(tmp_path, lan):
    a, b = _two(tmp_path)
    lan({A_HOST: a, B_HOST: b})
    async with _client(A_HOST) as ca, _client(B_HOST) as cb:
        out = await _pair(ca, cb, b_name="DESKTOP-4HQ2", call_it="Office PC")
        assert out["peer"]["name"] == "Office-PC"
        assert out["peer"]["their_name"] == "DESKTOP-4HQ2"
        assert RemoteAgentRegistry(a.state.platform.engine).get("Office-PC") is not None


# --------------------------------------------------------------------------- #
# P4 — THE ACCEPTANCE, both directions
# --------------------------------------------------------------------------- #


async def test_desk_does_the_laptops_task_and_the_answer_comes_home(tmp_path, lan):
    a, b = _two(tmp_path)
    net = lan({A_HOST: a, B_HOST: b})
    b.state.platform.router.stream = _scripted("Desk read the notes: three action items.")
    async with _client(A_HOST) as ca, _client(B_HOST) as cb:
        await _pair(ca, cb)
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
        assert r.json()["spoke"] == ["remote:desk"]

        # The task went to B's door with the token B made for the laptop.
        (pa,) = _peers(a)
        to_door = [c for c in net.calls if c["method"] == "POST" and c["url"].endswith(TASK_DOOR_PATH)]
        assert len(to_door) == 1 and to_door[0]["auth"] == f"Bearer {_secret(a, pa.out_secret)}"

        rows = await _settled(b)
        assert len(rows) == 1 and rows[0].status is SessionStatus.COMPLETED
        assert rows[0].origin == "remote:laptop" and rows[0].trust == "low"
        assert "laptop" in (rows[0].trust_reason or "")

        thread = (await ca.get(f"/chat/threads/{tid}")).json()
        homed = [m for m in thread["messages"] if m.get("panelWho") == "remote:desk"]
        assert homed and homed[-1]["content"] == "Desk read the notes: three action items."

    # Each computer heard from the other.
    (pa,) = _peers(a)
    (pb,) = _peers(b)
    assert pb.last_heard_at is not None, "the desk heard the laptop's task"
    assert pa.last_heard_at is not None, "the laptop heard the desk's answer"


async def test_the_desk_can_hand_the_laptop_a_task_too(tmp_path, lan):
    a, b = _two(tmp_path)
    lan({A_HOST: a, B_HOST: b})
    a.state.platform.router.stream = _scripted("Laptop did it.")
    async with _client(A_HOST) as ca, _client(B_HOST) as cb:
        await _pair(ca, cb)
        reg = RemoteAgentRegistry(b.state.platform.engine)
        rec = reg.get("laptop")
        out = await reg.run(rec, "list the folders", b.state.platform.secrets.get)
        assert out.get("ok") is True and out.get("accepted") is True, out
        rows = await _settled(a)
        assert len(rows) == 1 and rows[0].origin == "remote:desk" and rows[0].trust == "low"


async def test_a_paired_computer_gets_in_with_the_hand_made_door_off(tmp_path, lan):
    a, b = _two(tmp_path)
    lan({A_HOST: a, B_HOST: b})
    async with _client(A_HOST) as ca, _client(B_HOST) as cb:
        await _pair(ca, cb)
        assert (await cb.get("/agents/task-door")).json()["enabled"] is False
        (pa,) = _peers(a)
        token = _secret(a, pa.out_secret)
        async with httpx.AsyncClient(base_url=f"http://{B_HOST}") as raw:
            probe = await raw.get(TASK_DOOR_PATH, headers={"Authorization": f"Bearer {token}"})
            assert probe.status_code == 200 and probe.json()["takes_tasks"] is True
            # Anyone else still meets the hand-made door's rules: off = 403.
            other = await raw.get(TASK_DOOR_PATH, headers={"Authorization": "Bearer nope-not-a-peer"})
            assert other.status_code == 403 and other.json()["detail"] == td.DOOR_OFF
            assert "Your other computers" in td.DOOR_OFF


async def test_the_hand_made_door_still_works_beside_a_pairing(tmp_path, lan):
    a, b = _two(tmp_path)
    lan({A_HOST: a, B_HOST: b})
    async with _client(A_HOST) as ca, _client(B_HOST) as cb:
        await _pair(ca, cb)
        on = (await cb.post("/agents/task-door/enable", json={"name": "phone"})).json()
        async with httpx.AsyncClient(base_url=f"http://{B_HOST}") as raw:
            r = await raw.post(TASK_DOOR_PATH, json={"task": "x"},
                               headers={"Authorization": f"Bearer {on['token']}"})
            assert r.status_code == 202
        rows = await _settled(b)
        assert [row.origin for row in rows] == ["remote:phone"], "the hand-made door keeps its own name"


# --------------------------------------------------------------------------- #
# P5 — every refusal in words, nothing kept
# --------------------------------------------------------------------------- #


def _nothing_kept(*apps) -> None:
    for app in apps:
        assert _peers(app) == []
        assert RemoteAgentRegistry(app.state.platform.engine).list() == []
        assert not [
            r for r in app.state.platform.secrets.list()
            if r["name"].startswith(("peer.", "remote_agent.inbound."))
        ]


async def test_a_wrong_code_is_refused_in_words_and_keeps_nothing(tmp_path, lan):
    a, b = _two(tmp_path)
    lan({A_HOST: a, B_HOST: b})
    async with _client(A_HOST) as ca, _client(B_HOST) as cb:
        code = (await cb.post("/peers/code")).json()["code"]["code"]
        wrong = "000000" if code != "000000" else "111111"
        r = await ca.post("/peers/join", json={"address": B_HOST, "code": wrong})
        assert r.status_code == 409 and r.json()["detail"] == R.CODE_WRONG
        _nothing_kept(a, b)
        # The right code still works after one wrong try.
        ok = await ca.post("/peers/join", json={"address": B_HOST, "code": code})
        assert ok.status_code == 200


async def test_too_many_wrong_tries_end_the_code_and_say_so(tmp_path, lan):
    a, b = _two(tmp_path)
    lan({A_HOST: a, B_HOST: b})
    async with _client(A_HOST) as ca, _client(B_HOST) as cb:
        code = (await cb.post("/peers/code")).json()["code"]["code"]
        wrong = "000000" if code != "000000" else "111111"
        for _ in range(C.MAX_WRONG - 1):
            r = await ca.post("/peers/join", json={"address": B_HOST, "code": wrong})
            assert r.json()["detail"] == R.CODE_WRONG
        r = await ca.post("/peers/join", json={"address": B_HOST, "code": wrong})
        assert r.status_code == 409 and r.json()["detail"] == R.CODE_USED_UP
        r = await ca.post("/peers/join", json={"address": B_HOST, "code": code})
        assert r.json()["detail"] == R.NOT_SHOWING
        _nothing_kept(a, b)


async def test_a_code_that_ran_out_says_so(tmp_path, lan):
    a, b = _two(tmp_path)
    lan({A_HOST: a, B_HOST: b})
    clock = _Clock()
    b.state.platform._pairing_codes = C.PairingCodes(clock=clock)
    async with _client(A_HOST) as ca, _client(B_HOST) as cb:
        code = (await cb.post("/peers/code")).json()["code"]["code"]
        clock.now += C.CODE_TTL_S
        r = await ca.post("/peers/join", json={"address": B_HOST, "code": code})
        assert r.status_code == 409 and r.json()["detail"] == R.CODE_EXPIRED
        _nothing_kept(a, b)


async def test_pairing_tries_are_limited_from_anyone(tmp_path, lan):
    a, b = _two(tmp_path)
    lan({A_HOST: a, B_HOST: b})
    async with _client(B_HOST) as cb, httpx.AsyncClient(base_url=f"http://{B_HOST}") as raw:
        await cb.post("/peers/code")
        statuses = []
        for _ in range(C.TRIES_PER_MINUTE + 1):
            r = await raw.post(PEER_PAIR_PATH, json={"protocol": 1, "code": "x"})
            statuses.append(r.status_code)
        assert statuses[-1] == 429 and 429 not in statuses[:-1]
        assert r.json()["detail"] == R.TOO_MANY_TRIES


@pytest.mark.parametrize(
    "body",
    [
        {"code": "123456", "port": 8787, "door_token": "t" * 43},
        {"protocol": 2, "code": "123456", "port": 8787, "door_token": "t" * 43},
        {"protocol": 1, "code": "123456", "port": 8787, "door_token": "short"},
        {"protocol": 1, "code": "123456", "port": 8787, "door_token": "bad token with spaces!!"},
        {"protocol": 1, "code": "123456", "port": 0, "door_token": "t" * 43},
        {"protocol": 1, "code": "123456", "port": "8787", "door_token": "t" * 43},
        {"protocol": 1, "code": "123456", "port": True, "door_token": "t" * 43},
    ],
)
async def test_a_malformed_pairing_is_refused_before_the_code_is_read(tmp_path, lan, body):
    a, b = _two(tmp_path)
    lan({A_HOST: a, B_HOST: b})
    async with _client(B_HOST) as cb, httpx.AsyncClient(base_url=f"http://{B_HOST}") as raw:
        code = (await cb.post("/peers/code")).json()["code"]["code"]
        r = await raw.post(PEER_PAIR_PATH, json={**body, "code": code})
        assert r.status_code == 400 and r.json()["detail"] == R.OLD_VERSION
        assert b.state.platform._pairing_codes.active() is not None, "the code was not used up"
        _nothing_kept(b)


async def test_a_large_pairing_body_is_refused(tmp_path, lan):
    a, b = _two(tmp_path)
    lan({A_HOST: a, B_HOST: b})
    async with httpx.AsyncClient(base_url=f"http://{B_HOST}") as raw:
        r = await raw.post(PEER_PAIR_PATH, content=b"{" + b" " * (R.PAIR_MAX_BODY_BYTES + 10) + b"}",
                           headers={"content-type": "application/json"})
        assert r.status_code == 413


@pytest.mark.parametrize(
    "typed,want",
    [
        ("192.168.1.20", "http://192.168.1.20:8787"),
        (" 192.168.1.20:9000 ", "http://192.168.1.20:9000"),
        ("http://192.168.1.20:8787/", "http://192.168.1.20:8787"),
        ("https://desk.example.test", "https://desk.example.test:8787"),
        ("100.101.102.103", "http://100.101.102.103:8787"),
        ("[fd00::1]:8787", "http://[fd00::1]:8787"),
        ("http://[::ffff:10.0.0.2]:8787", "http://10.0.0.2:8787"),
    ],
)
def test_an_address_is_read_as_typed(typed, want):
    assert S.normalize_address(typed) == want


@pytest.mark.parametrize(
    "typed",
    ["", "   ", "ftp://desk", "http://me:pw@desk", "http://desk/agents", "desk?x=1",
     "\\\\desk\\share", "http://desk:notaport", "http://desk#frag", "de\nsk"],
)
def test_an_address_that_is_not_one_is_refused_in_words(typed):
    with pytest.raises(S.AddressProblem) as err:
        S.normalize_address(typed)
    assert str(err.value).endswith(".")


async def test_join_says_in_words_what_went_wrong(tmp_path, lan):
    a, b = _two(tmp_path)
    net = lan({A_HOST: a, B_HOST: b})
    async with _client(A_HOST) as ca:
        r = await ca.post("/peers/join", json={"address": "", "code": "123456"})
        assert r.status_code == 400 and "address" in r.json()["detail"]
        r = await ca.post("/peers/join", json={"address": B_HOST, "code": "12"})
        assert r.status_code == 400 and "six digits" in r.json()["detail"]
        # Nobody at that address: said, with the address.
        r = await ca.post("/peers/join", json={"address": "10.0.0.9", "code": "123456"})
        assert r.status_code == 409
        assert "No computer answered at http://10.0.0.9:8787" in r.json()["detail"]
        net.down.add(B_HOST)
        r = await ca.post("/peers/join", json={"address": B_HOST, "code": "123456"})
        assert "No computer answered" in r.json()["detail"]
    _nothing_kept(a)


async def test_an_older_iron_jarvis_that_cannot_pair_is_named(tmp_path, lan, monkeypatch):
    async def old(method, url, **kw):
        return 404, '{"detail": "Not Found"}'

    monkeypatch.setattr(L, "_request", old)
    with pytest.raises(L.LinkFailed) as err:
        await L.pair("http://10.0.0.2:8787", code="123456", name="laptop", port=8787,
                     door_token="t" * 43, guess="")
    assert "Update Iron Jarvis there" in err.value.words


async def test_an_answer_that_is_not_iron_jarvis_is_refused(monkeypatch):
    async def odd(method, url, **kw):
        return 200, '{"name": "desk"}'

    monkeypatch.setattr(L, "_request", odd)
    with pytest.raises(L.LinkFailed) as err:
        await L.pair("http://10.0.0.2:8787", code="123456", name="laptop", port=8787,
                     door_token="t" * 43, guess="")
    assert "not as Iron Jarvis does" in err.value.words


# --------------------------------------------------------------------------- #
# P6 — Remove
# --------------------------------------------------------------------------- #


async def test_remove_ends_it_on_both_computers(tmp_path, lan):
    a, b = _two(tmp_path)
    lan({A_HOST: a, B_HOST: b})
    async with _client(A_HOST) as ca, _client(B_HOST) as cb:
        await _pair(ca, cb)
        (pa,) = _peers(a)
        a_to_b = _secret(a, pa.out_secret)
        (pb,) = _peers(b)
        b_to_a = _secret(b, pb.out_secret)

        out = (await ca.delete("/peers/desk")).json()
        # v1.509.0 (F5): the answer also says how many of its tasks stopped.
        assert out == {"removed": True, "told": True, "stopped": 0,
                       "said": R.removed_words("desk", True)}
        _nothing_kept(a, b)
        removed_b = [e.payload for e in b.state.platform.event_bus.history if e.type == "peer.removed"]
        assert removed_b == [{"name": "laptop", "by": "them"}]

        async with httpx.AsyncClient(base_url=f"http://{B_HOST}") as raw:
            r = await raw.get(TASK_DOOR_PATH, headers={"Authorization": f"Bearer {a_to_b}"})
            assert r.status_code == 403, "the desk no longer takes the laptop's token"
        async with httpx.AsyncClient(base_url=f"http://{A_HOST}") as raw:
            r = await raw.get(TASK_DOOR_PATH, headers={"Authorization": f"Bearer {b_to_a}"})
            assert r.status_code == 403, "the laptop no longer takes the desk's token"


async def test_remove_says_when_the_other_computer_could_not_be_told(tmp_path, lan):
    a, b = _two(tmp_path)
    net = lan({A_HOST: a, B_HOST: b})
    async with _client(A_HOST) as ca, _client(B_HOST) as cb:
        await _pair(ca, cb)
        (pb,) = _peers(b)
        b_to_a = _secret(b, pb.out_secret)
        net.down.add(B_HOST)
        out = (await ca.delete("/peers/desk")).json()
        assert out["removed"] is True and out["told"] is False
        assert out["said"] == R.removed_words("desk", False)
        _nothing_kept(a)
        assert [p.name for p in _peers(b)] == ["laptop"], "the desk still lists it (it was not told)"
        net.down.clear()
        async with httpx.AsyncClient(base_url=f"http://{A_HOST}") as raw:
            r = await raw.get(TASK_DOOR_PATH, headers={"Authorization": f"Bearer {b_to_a}"})
            assert r.status_code == 403, "either way the desk can no longer reach the laptop"


async def test_unpair_needs_that_peers_own_token(tmp_path, lan):
    a, b = _two(tmp_path)
    lan({A_HOST: a, B_HOST: b})
    async with _client(A_HOST) as ca, _client(B_HOST) as cb:
        await _pair(ca, cb)
        async with httpx.AsyncClient(base_url=f"http://{B_HOST}") as raw:
            for header in ({}, {"Authorization": "Bearer wrong"}, {"Authorization": f"Bearer {INSTALL}"}):
                r = await raw.post(PEER_UNPAIR_PATH, json={}, headers=header)
                assert r.status_code == 401 and r.json()["detail"] == R.UNKNOWN_TOKEN
        assert [p.name for p in _peers(b)] == ["laptop"]


async def test_remove_of_an_unknown_name_is_a_404_in_words(tmp_path, lan):
    a, b = _two(tmp_path)
    lan({A_HOST: a, B_HOST: b})
    async with _client(A_HOST) as ca:
        r = await ca.delete("/peers/nobody")
        assert r.status_code == 404 and r.json()["detail"].endswith(".")


# --------------------------------------------------------------------------- #
# P7 — Check
# --------------------------------------------------------------------------- #


async def test_check_says_whether_the_other_computer_answers(tmp_path, lan):
    a, b = _two(tmp_path)
    net = lan({A_HOST: a, B_HOST: b})
    async with _client(A_HOST) as ca, _client(B_HOST) as cb:
        await _pair(ca, cb)
        ok = (await ca.post("/peers/desk/check")).json()
        assert ok["ok"] is True and ok["peer"]["last_ok_at"] and ok["peer"]["last_error"] == ""
        heard = [p for p in _peers(b)][0]
        assert heard.last_heard_at is not None, "a check counts as the desk hearing the laptop"

        net.down.add(B_HOST)
        down = (await ca.post("/peers/desk/check")).json()
        assert down["ok"] is False and "No computer answered" in down["peer"]["last_error"]
        assert down["peer"]["last_error_at"]
        net.down.clear()

        # The desk removed the laptop without being able to say so: the check
        # says the pairing is gone there.
        await asyncio.to_thread(S.store_for(b.state.platform).remove, "laptop")
        gone = (await ca.post("/peers/desk/check")).json()
        assert gone["ok"] is False and "no longer knows this one" in gone["peer"]["last_error"]
        back = (await ca.post("/peers/check")).json()
        assert back["peers"][0]["last_error"]


# --------------------------------------------------------------------------- #
# P8 — a paired remote agent is the pairing's
# --------------------------------------------------------------------------- #


async def test_a_paired_remote_agent_is_changed_only_where_the_pairing_lives(tmp_path, lan):
    from iron_jarvis.daemon.routes.agents import PAIRED_REMOTE_REFUSAL

    a, b = _two(tmp_path)
    lan({A_HOST: a, B_HOST: b})
    async with _client(A_HOST) as ca, _client(B_HOST) as cb:
        await _pair(ca, cb)
        listed = (await ca.get("/agents/remote")).json()["agents"]
        assert [(r["name"], r["paired"]) for r in listed] == [("desk", True)]
        for method, path, body in (
            ("DELETE", "/agents/remote/desk", None),
            ("PATCH", "/agents/remote/desk", {"base_url": "http://10.0.0.7:8787/x"}),
            ("PATCH", "/agents/remote/desk", {"token": "a-new-one"}),
            ("PATCH", "/agents/remote/desk", {"clear_token": True}),
            ("PATCH", "/agents/remote/desk", {"kind": "openai-chat"}),
            ("POST", "/agents/remote/desk/inbound/disable", None),
        ):
            r = await ca.request(method, path, json=body)
            assert r.status_code == 409, (method, path, r.text)
            assert r.json()["detail"] == PAIRED_REMOTE_REFUSAL
        ok = await ca.patch("/agents/remote/desk", json={"timeout_s": 300})
        assert ok.status_code == 200 and ok.json()["timeout_s"] == 300
        # Nothing the refused calls asked for happened.
        (pa,) = _peers(a)
        rec = RemoteAgentRegistry(a.state.platform.engine).get("desk")
        assert rec.base_url.endswith(TASK_DOOR_PATH) and rec.secret_name == pa.out_secret
        assert _secret(a, pa.out_secret) and rec.inbound_enabled


async def test_a_hand_made_remote_agent_is_never_overwritten_by_a_pairing(tmp_path, lan):
    a, b = _two(tmp_path)
    lan({A_HOST: a, B_HOST: b})
    async with _client(A_HOST) as ca, _client(B_HOST) as cb:
        made = await ca.post("/agents/remote", json={"name": "desk", "base_url": "http://hermes.test",
                                                     "kind": "http-task"})
        assert made.status_code == 200
        out = await _pair(ca, cb)
        assert out["peer"]["name"] == "desk-2"
        assert RemoteAgentRegistry(a.state.platform.engine).get("desk").base_url == "http://hermes.test"
        listed = {r["name"]: r["paired"] for r in (await ca.get("/agents/remote")).json()["agents"]}
        assert listed == {"desk": False, "desk-2": True}


async def test_the_same_computer_pairing_again_replaces_its_old_pairing(tmp_path, lan):
    a, b = _two(tmp_path)
    lan({A_HOST: a, B_HOST: b})
    async with _client(A_HOST) as ca, _client(B_HOST) as cb:
        await _pair(ca, cb)
        (old_a,) = _peers(a)
        (old_b,) = _peers(b)
        await _pair(ca, cb)
        (new_a,) = _peers(a)
        (new_b,) = _peers(b)
        assert new_a.id != old_a.id and new_b.id != old_b.id
        for app, old in ((a, old_a), (b, old_b)):
            assert _secret(app, old.in_secret) is None and _secret(app, old.out_secret) is None
        assert [r.name for r in RemoteAgentRegistry(a.state.platform.engine).list()] == ["desk"]


@pytest.mark.parametrize(
    "raw,want",
    [
        ("DESKTOP-4HQ2LMN", "DESKTOP-4HQ2LMN"),
        ("Valentino's Laptop", "Valentino-s-Laptop"),
        ("  desk  ", "desk"),
        ("123office", "office"),
        ("", "computer"),
        ("----", "computer"),
        ("é", "computer"),
        ("x" * 80, "x" * 40),
    ],
)
def test_a_name_is_one_a_person_can_write_after_an_at(raw, want):
    assert S.peer_name(raw) == want


async def test_this_computers_name_defaults_to_its_own_and_is_kept(tmp_path, lan, monkeypatch):
    monkeypatch.setattr(S.socket, "gethostname", lambda: "OFFICE-PC")
    a, b = _two(tmp_path)
    lan({A_HOST: a, B_HOST: b})
    async with _client(B_HOST) as cb:
        assert (await cb.get("/peers")).json()["this_name"] == "OFFICE-PC"
        await cb.post("/peers/code", json={"this_name": "front desk"})
        assert (await cb.get("/peers")).json()["this_name"] == "front-desk"


async def test_the_network_says_which_addresses_another_computer_can_type(tmp_path, lan, monkeypatch):
    a, b = _two(tmp_path)
    lan({A_HOST: a, B_HOST: b})
    async with _client(B_HOST) as cb:
        net = (await cb.get("/peers")).json()["network"]
        assert net["applied"] is True and net["addresses"] == [A_HOST, B_HOST]
    monkeypatch.setenv("IRONJARVIS_HOST_ALLOWLIST", "")
    lan({"127.0.0.1:8787": b, A_HOST: a})
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8787", headers=AUTH) as local:
        net = (await local.get("/peers")).json()["network"]
        assert net == {"saved": False, "applied": False, "addresses": []}
