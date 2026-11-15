"""Your other computers, an independent review (v1.497.0).

Each test pins one defect the review found in the first cut of pairing, and
goes red on that cut. Same harness as ``tests/test_peers_v1497.py``: two real
``create_app`` factories and a stand-in network between them.

  R1 the pairing door (no install token) never buffers more than its cap of a
     body before the code is checked: it stops reading.
  R2 the address a pairing keeps for the other computer is the address its
     request really came from, never one the request names; and a request
     that cannot be placed never uses the code up.
  R3 a remote agent a pairing made cannot be overwritten by "Connect a remote
     agent" with the same name (any case), nor its message-back rotated to an
     address the pairing did not choose.
  R4 a pairing is all or nothing: when one side cannot keep it, neither keeps
     anything (the other computer is told to drop its half; a half-written
     side cleans up after itself).
  R5 the "@name" a pairing gets is one chat really sends to the other
     computer: never a name an agent here already answers to.
  R6 nothing in the async pairing routes reads the database on the event loop.
  R7 Check says in true words when the other computer stops accepting the
     address it was paired at (no "type" advice: nothing is being typed).
  R8 neither door the other computer calls ever hands its caller an internal
     error's text (the app's own 500 envelope carries the exception message).
"""

# ruff: noqa: F811 — `lan` (the stand-in network) is the first pairing file's fixture
from __future__ import annotations

import asyncio
import threading

import httpx
import pytest

from iron_jarvis.agents.remote import RemoteAgentRegistry
from iron_jarvis.daemon.auth import PEER_PAIR_PATH
from iron_jarvis.daemon.routes import peers as R
from iron_jarvis.peers import store as S
from tests.test_peers_v1497 import (  # noqa: F401 — `lan` is a fixture
    A_HOST,
    A_IP,
    B_HOST,
    _client,
    _nothing_kept,
    _pair,
    _peers,
    _secret,
    _two,
    lan,
)


def _peer_secrets(app) -> list[str]:
    return [
        r["name"]
        for r in app.state.platform.secrets.list()
        if r["name"].startswith(("peer.", "remote_agent.inbound."))
    ]


# --------------------------------------------------------------------------- #
# R1 — the exempt door reads only up to its cap
# --------------------------------------------------------------------------- #


async def test_the_pairing_door_stops_reading_a_body_past_its_cap(tmp_path, lan):
    """Anyone on the network can reach this door (it takes no install
    token), so it must not hold a whole body in memory to find out it is too
    big. A body with no length (chunked) is read only up to the cap."""
    a, b = _two(tmp_path)
    lan({A_HOST: a, B_HOST: b})
    chunk = b" " * (64 * 1024)
    total_chunks = 64  # 4 MB, far past the 16 KB cap
    pulled = {"n": 0}

    async def body():
        yield b"{"
        for _ in range(total_chunks):
            pulled["n"] += 1
            yield chunk
        yield b"}"

    # Straight to the app (the stand-in network reads every body whole to
    # record it), from the other computer's address, with no length.
    door = httpx.ASGITransport(app=b, client=(A_IP, 50000))
    req = httpx.Request("POST", f"http://{B_HOST}{PEER_PAIR_PATH}", content=body(),
                        headers={"content-type": "application/json"})
    assert "content-length" not in req.headers
    r = await door.handle_async_request(req)
    await r.aread()
    assert r.status_code == 413
    # The cap is 16 KB: one 64 KB chunk is already past it. Allow a little
    # read-ahead from the transport, never the whole body.
    assert pulled["n"] <= 4, f"read {pulled['n']} of {total_chunks} chunks before refusing"


# --------------------------------------------------------------------------- #
# R2 — where the other computer is: the TCP peer, never the body
# --------------------------------------------------------------------------- #


async def test_the_kept_address_is_never_one_the_request_names(tmp_path, lan):
    """A request whose sender cannot be placed (no readable peer address) is
    refused. The first cut fell back to the address the body names, so the
    sender chose where this computer would later send its token and tasks."""
    a, b = _two(tmp_path)
    lan({A_HOST: a, B_HOST: b})
    async with _client(B_HOST) as cb:
        code = (await cb.post("/peers/code")).json()["code"]["code"]
    # Straight to the app (the stand-in network always names a real sender).
    nowhere = httpx.ASGITransport(app=b, client=("not-an-address", 0))
    req = httpx.Request(
        "POST",
        f"http://{B_HOST}{PEER_PAIR_PATH}",
        json={"protocol": 1, "code": code, "port": 8787, "door_token": "t" * 43,
              "address": "10.9.9.9", "name": "intruder"},
    )
    resp = await nowhere.handle_async_request(req)
    await resp.aread()
    assert resp.status_code == 400, resp.content
    assert all(p.address != "http://10.9.9.9:8787" for p in _peers(b))
    _nothing_kept(b)
    # A request that could not be placed did not use the code up.
    assert b.state.platform._pairing_codes.active() is not None


# --------------------------------------------------------------------------- #
# R3 — a paired remote agent cannot be replaced by hand
# --------------------------------------------------------------------------- #


async def test_connect_a_remote_agent_cannot_overwrite_a_paired_one(tmp_path, lan):
    from iron_jarvis.daemon.routes.agents import PAIRED_REMOTE_REFUSAL

    a, b = _two(tmp_path)
    lan({A_HOST: a, B_HOST: b})
    async with _client(A_HOST) as ca, _client(B_HOST) as cb:
        await _pair(ca, cb)
        (pa,) = _peers(a)
        before = RemoteAgentRegistry(a.state.platform.engine).get("desk")
        for name in ("desk", "DESK", "Desk"):
            r = await ca.post(
                "/agents/remote",
                json={"name": name, "base_url": "http://hermes.test", "kind": "http-task",
                      "token": "a-hand-typed-one"},
            )
            assert r.status_code == 409, (name, r.text)
            assert r.json()["detail"] == PAIRED_REMOTE_REFUSAL
        rot = await ca.post("/agents/remote/desk/inbound/enable", json={})
        assert rot.status_code == 409 and rot.json()["detail"] == PAIRED_REMOTE_REFUSAL
        after = RemoteAgentRegistry(a.state.platform.engine).get("desk")
        assert (after.base_url, after.kind, after.secret_name, after.inbound_url) == (
            before.base_url, before.kind, before.secret_name, before.inbound_url
        )
        assert after.secret_name == pa.out_secret and _secret(a, pa.out_secret)
        assert [r.name for r in RemoteAgentRegistry(a.state.platform.engine).list()] == ["desk"]
        # An ordinary hand-made remote is still made as before.
        ok = await ca.post("/agents/remote", json={"name": "hermes", "base_url": "http://hermes.test",
                                                   "kind": "http-task"})
        assert ok.status_code == 200


# --------------------------------------------------------------------------- #
# R4 — all or nothing
# --------------------------------------------------------------------------- #


async def test_when_this_side_cannot_keep_the_pairing_the_other_drops_its_half(tmp_path, lan, monkeypatch):
    a, b = _two(tmp_path)
    lan({A_HOST: a, B_HOST: b})
    store_a = S.store_for(a.state.platform)

    def broken_add(**kw):
        raise RuntimeError("the vault is locked")

    monkeypatch.setattr(store_a, "add", broken_add)
    async with _client(A_HOST) as ca, _client(B_HOST) as cb:
        code = (await cb.post("/peers/code", json={"this_name": "desk"})).json()["code"]["code"]
        r = await ca.post("/peers/join", json={"address": B_HOST, "code": code, "this_name": "laptop"})
        assert r.status_code >= 400
        detail = r.json()["detail"]
        assert isinstance(detail, str) and detail.endswith(".") and "vault" not in detail
        assert "nothing was kept" in detail.lower()
    _nothing_kept(a, b)


async def test_a_side_that_fails_half_way_keeps_nothing_of_it(tmp_path, lan, monkeypatch):
    """The pairing side writes two tokens, a remote agent and a record; a
    failure after the first of them must not leave the rest behind (an
    orphaned remote agent holding a working token)."""
    a, b = _two(tmp_path)
    lan({A_HOST: a, B_HOST: b})
    real = RemoteAgentRegistry.enable_inbound

    def failing(self, *args, **kw):
        raise RuntimeError("disk full")

    monkeypatch.setattr(RemoteAgentRegistry, "enable_inbound", failing)
    async with _client(A_HOST) as ca, _client(B_HOST) as cb:
        code = (await cb.post("/peers/code")).json()["code"]["code"]
        r = await ca.post("/peers/join", json={"address": B_HOST, "code": code})
        assert r.status_code >= 400
        assert "disk full" not in r.text
    monkeypatch.setattr(RemoteAgentRegistry, "enable_inbound", real)
    _nothing_kept(a, b)
    assert _peer_secrets(a) == [] and _peer_secrets(b) == []


# --------------------------------------------------------------------------- #
# R5 — the @name reaches the other computer
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("their_name", ["builder", "Guide", "jarvis"])
async def test_a_computer_named_like_an_agent_here_gets_a_name_chat_sends_to_it(tmp_path, lan, their_name):
    """"Paired with builder. In chat, write @builder" would be untrue: chat
    sends "@builder" to the builder agent on this computer. The pairing gets
    a name nothing here answers to, on both sides."""
    from iron_jarvis.agents.roster import resolve_target

    a, b = _two(tmp_path)
    lan({A_HOST: a, B_HOST: b})
    async with _client(A_HOST) as ca, _client(B_HOST) as cb:
        out = await _pair(ca, cb, b_name=their_name, a_name=their_name)
    for app in (a, b):
        (peer,) = _peers(app)
        assert peer.name.lower() != their_name.lower(), peer.name
        entry = await asyncio.to_thread(resolve_target, app.state.platform, peer.name, require_delegable=False)
        assert entry is not None and entry.name == f"remote:{peer.name}", (peer.name, entry)
    assert out["said"] == R.paired_words(_peers(a)[0].name)


@pytest.mark.parametrize("taken_by", ["a hand-made remote", "an agent here"])
async def test_a_computer_that_got_a_suffixed_name_pairing_again_replaces_its_pairing(tmp_path, lan, taken_by):
    """The first pairing got "desk-2" (a name here was taken). The same
    computer pairing again must replace THAT pairing, not make "desk-3" and
    leave a dead "desk-2" listed (both sides)."""
    a, b = _two(tmp_path)
    lan({A_HOST: a, B_HOST: b})
    b_name = "desk" if taken_by == "a hand-made remote" else "builder"
    async with _client(A_HOST) as ca, _client(B_HOST) as cb:
        if taken_by == "a hand-made remote":
            hand = await ca.post("/agents/remote", json={"name": "desk", "base_url": "http://hermes.test",
                                                         "kind": "http-task"})
            assert hand.status_code == 200
        first = (await _pair(ca, cb, b_name=b_name, a_name="builder"))["peer"]["name"]
        second = (await _pair(ca, cb, b_name=b_name, a_name="builder"))["peer"]["name"]
    assert first == second == f"{b_name}-2"
    for app in (a, b):
        assert len(_peers(app)) == 1, [p.name for p in _peers(app)]
    (pa,) = _peers(a)
    (pb,) = _peers(b)
    assert _secret(a, pa.in_secret) == _secret(b, pb.out_secret)
    assert _secret(b, pb.in_secret) == _secret(a, pa.out_secret)
    paired = [r.name for r in RemoteAgentRegistry(a.state.platform.engine).list() if r.name != "desk"]
    assert paired == [first]


async def test_a_custom_agent_or_a_remote_in_another_case_keeps_its_name(tmp_path, lan):
    a, b = _two(tmp_path)
    lan({A_HOST: a, B_HOST: b})
    async with _client(A_HOST) as ca, _client(B_HOST) as cb:
        made = await ca.post("/agents", json={"name": "desk", "base_type": "builder", "system_prompt": "You keep the books."})
        assert made.status_code == 200, made.text
        hand = await ca.post("/agents/remote", json={"name": "Desk-2", "base_url": "http://hermes.test",
                                                     "kind": "http-task"})
        assert hand.status_code == 200
        out = await _pair(ca, cb)
    name = out["peer"]["name"]
    assert name.lower() not in ("desk", "desk-2"), name
    assert RemoteAgentRegistry(a.state.platform.engine).get("Desk-2").base_url == "http://hermes.test"


# --------------------------------------------------------------------------- #
# R6 — nothing blocking on the loop
# --------------------------------------------------------------------------- #


async def test_the_async_routes_never_read_the_list_on_the_event_loop(tmp_path, lan, monkeypatch):
    a, b = _two(tmp_path)
    lan({A_HOST: a, B_HOST: b})
    loop_thread = threading.get_ident()
    on_loop: list[str] = []
    real_list = S.PeerStore.list
    real_name = S.PeerStore.this_name

    def watched_list(self):
        if threading.get_ident() == loop_thread:
            on_loop.append("list")
        return real_list(self)

    def watched_name(self):
        if threading.get_ident() == loop_thread:
            on_loop.append("this_name")
        return real_name(self)

    async with _client(A_HOST) as ca, _client(B_HOST) as cb:
        # Make each store once before watching (its first use makes its table).
        await ca.get("/peers")
        await cb.get("/peers")
        monkeypatch.setattr(S.PeerStore, "list", watched_list)
        monkeypatch.setattr(S.PeerStore, "this_name", watched_name)
        await _pair(ca, cb)
        await ca.post("/peers/check")
        await ca.post("/peers/desk/check")
    assert on_loop == [], f"read on the event loop: {on_loop}"


# --------------------------------------------------------------------------- #
# R7 — Check's words when the address stops being accepted
# --------------------------------------------------------------------------- #


async def test_check_says_truly_when_the_other_computer_no_longer_accepts_its_address(tmp_path, lan, monkeypatch):
    a, b = _two(tmp_path)
    lan({A_HOST: a, B_HOST: b})
    async with _client(A_HOST) as ca, _client(B_HOST) as cb:
        await _pair(ca, cb)
        # The desk now answers only to the laptop's address list: its own
        # address as the laptop dials it is turned away by its Host guard.
        monkeypatch.setenv("IRONJARVIS_HOST_ALLOWLIST", A_IP)
        out = (await ca.post("/peers/desk/check")).json()
    words = out["peer"]["last_error"]
    assert out["ok"] is False and words
    assert "Type" not in words and "type" not in words, words
    assert "pair again" in words


async def test_the_unpair_door_never_hands_its_caller_an_internal_error(tmp_path, lan, monkeypatch):
    """This door takes no install token, so whoever calls it is a stranger
    until the token checks out: the app's own 500 envelope would have handed
    them the exception's class and message (a path, a SQL text)."""
    from iron_jarvis.daemon.auth import PEER_UNPAIR_PATH

    a, b = _two(tmp_path)
    lan({A_HOST: a, B_HOST: b})
    async with _client(A_HOST) as ca, _client(B_HOST) as cb:
        await _pair(ca, cb)

    def broken(self, candidate):
        raise RuntimeError("database is locked at C:/Users/someone/AppData/ironjarvis.db")

    monkeypatch.setattr(S.PeerStore, "verify", broken)
    async with httpx.AsyncClient(base_url=f"http://{B_HOST}") as raw:
        r = await raw.post(PEER_UNPAIR_PATH, json={"protocol": 1},
                           headers={"Authorization": "Bearer " + "q" * 43})
    assert r.status_code == 503, r.text
    assert "RuntimeError" not in r.text and "ironjarvis.db" not in r.text
    assert r.json()["detail"] == R.UNPAIR_FAILED
    assert [p.name for p in _peers(b)] == ["laptop"], "nothing was removed"


async def test_keeping_a_pairing_never_overwrites_a_remote_agent_made_meanwhile(tmp_path, lan):
    """Between choosing a name and keeping the pairing, the user may make a
    remote agent under that very name by hand: the pairing is refused, never
    written over it (``upsert`` replaces the address and the credential)."""
    a, b = _two(tmp_path)
    lan({A_HOST: a, B_HOST: b})
    async with _client(A_HOST) as ca:
        hand = await ca.post("/agents/remote", json={"name": "hermes", "base_url": "http://hermes.test",
                                                     "kind": "http-task", "token": "the-users-own-token-1"})
        assert hand.status_code == 200
    store = S.store_for(a.state.platform)
    with pytest.raises(S.NameTaken):
        await asyncio.to_thread(
            lambda: store.add(name="hermes", their_name="hermes", address=f"http://{B_HOST}",
                              in_token="i" * 43, out_token="o" * 43, my_address=f"http://{A_HOST}")
        )
    rec = RemoteAgentRegistry(a.state.platform.engine).get("hermes")
    assert rec.base_url == "http://hermes.test" and rec.secret_name == "remote_agent_hermes"
    assert _secret(a, "remote_agent_hermes") == "the-users-own-token-1"
    assert _peers(a) == [] and _peer_secrets(a) == []


async def test_a_code_typed_wrong_too_often_is_said_on_the_computer_showing_it(tmp_path, lan):
    """Five wrong tries end the code, but nothing told the computer SHOWING it:
    its card kept a code on screen that no longer works (until the ten
    minutes ran out). One names-only event says it ended, and why."""
    from iron_jarvis.peers import codes as C

    a, b = _two(tmp_path)
    lan({A_HOST: a, B_HOST: b})
    async with _client(A_HOST) as ca, _client(B_HOST) as cb:
        code = (await cb.post("/peers/code")).json()["code"]["code"]
        wrong = "000000" if code != "000000" else "111111"
        for _ in range(C.MAX_WRONG - 1):
            await ca.post("/peers/join", json={"address": B_HOST, "code": wrong})
        assert not [e for e in b.state.platform.event_bus.history if e.type == "peer.code_ended"]
        await ca.post("/peers/join", json={"address": B_HOST, "code": wrong})
    ended = [e.payload for e in b.state.platform.event_bus.history if e.type == "peer.code_ended"]
    assert ended == [{"why": "wrong_tries"}]
