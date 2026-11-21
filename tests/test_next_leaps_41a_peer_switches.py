"""Next leaps 41-a (build-out plan R1.5): a switch for each thing a paired computer may do.

Until this release a paired computer's token got a task in by itself, even
with the task door off (the build-out plan's Stage 0 note). Now each pairing
keeps one switch per thing the OTHER computer may do here, every one OFF for a
new pairing, and only the card's press turns one on (D-41.2). A pairing made
before this version keeps "Send me tasks" on and the card says so once
(D-41.1).

Through the REAL code (the harness of ``tests/test_peers_v1497.py``): two
``create_app`` factories in one process, their middleware (Host guard, install
bearer), the real pairing, the real task door, the real runtime and the real
reply. Stand-ins: the network between the two computers and the desk's model.

  S1 a new pairing has every switch off, on both computers.
  S2 THE ACCEPTANCE: a task from the laptop is refused in words, with no run
     on the desk, until "Send me tasks" is on for it there; then it runs, at
     low trust, and the answer comes home.
  S3 turning it off stops the laptop's task still running on the desk, and
     the next one is refused.
  S4 what the desk lets the laptop do reaches the laptop's card: the desk's
     probe answers it (its Check), and the desk tells it when a switch moves.
  S5 the peer door: only a paired computer's own token opens it — never the
     install bearer, a pane's token or none — and a peer token never opens
     the switches.
  S6 a switch whose thing is not in this version cannot be turned on; a
     folder must be a usable folder on this PC.
  S7 D-41.1: a pairing from before this version keeps "Send me tasks", says
     so once, and everything else is off.
  S8 chat: it shows the switches and turns them OFF (with an Undo that only a
     press can use), never on.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from iron_jarvis.agents import task_door as td
from iron_jarvis.agents.remote import RemoteAgentRegistry
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import Session, SessionStatus
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.auth import PEER_ALLOWS_PATH, PEER_DOOR_PATHS, TASK_DOOR_PATH, _is_exempt
from iron_jarvis.daemon.routes import peers as R
from iron_jarvis.peers import allows as A
from iron_jarvis.peers import door as PD
from iron_jarvis.peers import link as L
from iron_jarvis.peers import store as S
from iron_jarvis.peers.models import PeerRecord
from iron_jarvis.providers.adapters.base import LLMResponse

INSTALL = "install-bearer-for-both-computers"
AUTH = {"Authorization": f"Bearer {INSTALL}"}
A_IP, B_IP = "10.0.0.1", "10.0.0.2"
A_HOST, B_HOST = f"{A_IP}:8787", f"{B_IP}:8787"


# --------------------------------------------------------------------------- #
# harness: two computers, one network (as tests/test_peers_v1497.py)
# --------------------------------------------------------------------------- #


class _Network(httpx.AsyncBaseTransport):
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
    return create_app(str(tmp_path / "laptop")), create_app(str(tmp_path / "desk"))


async def _pair(ca, cb) -> None:
    """The desk shows a code; the laptop types it. NO switch is touched."""
    shown = (await cb.post("/peers/code", json={"this_name": "desk"})).json()
    r = await ca.post("/peers/join", json={"address": B_HOST, "code": shown["code"]["code"],
                                            "name": "", "this_name": "laptop"})
    assert r.status_code == 200, r.text


def _peers(app) -> list[PeerRecord]:
    return S.store_for(app.state.platform).list()


def _secret(app, name: str) -> str | None:
    return app.state.platform.secrets.get(name)


def _remote_sessions(app) -> list[Session]:
    with session_scope(app.state.platform.engine) as db:
        rows = [s for s in db.exec(__import__("sqlmodel").select(Session))
                if str(s.origin or "").startswith("remote:")]
        for r in rows:
            db.expunge(r)
        return rows


async def _settled(app, *, count: int = 1, timeout_s: float = 30.0) -> list[Session]:
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout_s
    running = app.state.orchestrator._running
    while True:
        rows = await asyncio.to_thread(_remote_sessions, app)
        done = [r for r in rows
                if r.status in (SessionStatus.COMPLETED, SessionStatus.FAILED, SessionStatus.CANCELLED)
                and (running.get(r.id) is None or running[r.id].done())]
        if len(done) >= count or loop.time() > end:
            break
        await asyncio.sleep(0.05)
    await td.drain_replies()
    return await asyncio.to_thread(_remote_sessions, app)


def _scripted(answer: str):
    async def fake_stream(**kw):
        yield {"type": "final", "response": LLMResponse(text=answer),
               "provider": "openai", "model": "gpt-5"}

    return fake_stream


def _laptop_token(a) -> str:
    """The token the laptop shows at the desk's doors."""
    (pa,) = _peers(a)
    return _secret(a, pa.out_secret)


async def _task(token: str, task: str = "read the notes") -> httpx.Response:
    async with httpx.AsyncClient(base_url=f"http://{B_HOST}") as raw:
        return await raw.post(TASK_DOOR_PATH, json={"task": task},
                              headers={"Authorization": f"Bearer {token}"})


def _view_of(view: dict, name: str) -> dict:
    (row,) = [p for p in view["peers"] if p["name"] == name]
    return row


# --------------------------------------------------------------------------- #
# S1 — a new pairing has every switch off
# --------------------------------------------------------------------------- #


async def test_a_new_pairing_has_every_switch_off_on_both_computers(tmp_path, lan):
    a, b = _two(tmp_path)
    lan({A_HOST: a, B_HOST: b})
    async with _client(A_HOST) as ca, _client(B_HOST) as cb:
        await _pair(ca, cb)
        for client, other in ((ca, "desk"), (cb, "laptop")):
            view = (await client.get("/peers")).json()
            row = _view_of(view, other)
            assert {k: row["allows"][k] for k in A.KEYS} == {k: False for k in A.KEYS}
            assert row["allows"][A.RECORD_FOLDERS] == [] and row["allows"][A.CODE_FOLDERS] == []
            assert row["may_line"] == f"{other} may do nothing here yet."
            assert row["kept_note"] == "", "a new pairing is no pairing from before"
            # The card draws from the daemon's one table, in its order.
            assert [s["key"] for s in view["switches"]] == list(A.KEYS)
            assert [s["key"] for s in view["switches"] if s["ready"]] == [A.SEND_TASKS]
    for app in (a, b):
        (peer,) = _peers(app)
        assert all(getattr(peer, k) is False for k in A.KEYS)


# --------------------------------------------------------------------------- #
# S2 — THE ACCEPTANCE
# --------------------------------------------------------------------------- #


async def test_a_task_is_refused_until_send_me_tasks_is_on_and_runs_once_it_is(tmp_path, lan):
    a, b = _two(tmp_path)
    lan({A_HOST: a, B_HOST: b})
    b.state.platform.router.stream = _scripted("Desk read the notes.")
    async with _client(A_HOST) as ca, _client(B_HOST) as cb:
        await _pair(ca, cb)
        assert (await cb.get("/agents/task-door")).json()["enabled"] is False
        token = _laptop_token(a)

        # Refused at the door, in words that name the desk and the switch.
        r = await _task(token)
        assert r.status_code == 403, r.text
        assert r.json()["detail"] == A.tasks_off_words("desk", "laptop")
        assert "“Send me tasks” is off for this computer on desk" in r.json()["detail"]
        assert await asyncio.to_thread(_remote_sessions, b) == [], "nothing ran, nothing was kept"

        # The laptop's own chat says why, in words (the remote agent's run).
        reg = RemoteAgentRegistry(a.state.platform.engine)
        out = await reg.run(reg.get("desk"), "read the notes", a.state.platform.secrets.get)
        assert out["ok"] is False and out["kind"] == "door" and out["status"] == 403
        assert out["detail"] == "desk turned the task away: " + A.tasks_off_words("desk", "laptop")

        # The desk's card turns it on: the next task runs, at low trust.
        on = await cb.patch("/peers/laptop/allows", json={"may_send_tasks": True})
        assert on.status_code == 200, on.text
        assert on.json()["after"][A.SEND_TASKS] is True
        assert on.json()["said"] == "laptop may: send tasks."
        r = await _task(token)
        assert r.status_code == 202, r.text
        rows = await _settled(b)
        assert len(rows) == 1 and rows[0].status is SessionStatus.COMPLETED
        assert rows[0].origin == "remote:laptop" and rows[0].trust == "low"
        # Only the desk's switch for the laptop was turned on: the desk still
        # takes nothing it was not asked for, and the laptop takes nothing
        # from the desk.
        (pa,) = _peers(a)
        assert pa.may_send_tasks is False


async def test_the_hand_made_door_does_not_open_for_a_paired_computer_whose_switch_is_off(tmp_path, lan):
    """The hand-made door ON is no way round the switch: a paired computer's
    token is refused by its switch, never let through as the hand door."""
    a, b = _two(tmp_path)
    lan({A_HOST: a, B_HOST: b})
    async with _client(A_HOST) as ca, _client(B_HOST) as cb:
        await _pair(ca, cb)
        await cb.post("/agents/task-door/enable", json={"name": "phone"})
        r = await _task(_laptop_token(a))
        assert r.status_code == 403 and r.json()["detail"] == A.tasks_off_words("desk", "laptop")
        assert await asyncio.to_thread(_remote_sessions, b) == []


# --------------------------------------------------------------------------- #
# S3 — off stops a running task
# --------------------------------------------------------------------------- #


async def test_turning_send_me_tasks_off_stops_the_running_task_and_refuses_the_next(tmp_path, lan):
    a, b = _two(tmp_path)
    net = lan({A_HOST: a, B_HOST: b})
    started = asyncio.Event()

    async def held_stream(**kw):
        started.set()
        await asyncio.Event().wait()  # held until the run is stopped
        yield {}  # pragma: no cover

    b.state.platform.router.stream = held_stream
    async with _client(A_HOST) as ca, _client(B_HOST) as cb:
        await _pair(ca, cb)
        await cb.patch("/peers/laptop/allows", json={"may_send_tasks": True})
        token = _laptop_token(a)
        assert (await _task(token)).status_code == 202
        await asyncio.wait_for(started.wait(), 30)
        off = (await cb.patch("/peers/laptop/allows", json={"may_send_tasks": False})).json()
        assert off["stopped"] == 1
        assert off["said"] == "laptop may do nothing here yet." + R.stopped_words("laptop", 1)
        rows = await _settled(b)
        assert rows[0].status is SessionStatus.CANCELLED
        assert R.tasks_off_stop_summary("laptop") in (rows[0].summary or "")
        r = await _task(token)
        assert r.status_code == 403
        await R.drain_tells()
    changed = [e.payload for e in b.state.platform.event_bus.history if e.type == "peer.allows_changed"]
    assert changed == [{"name": "laptop", "by": "here"}] * 2
    assert all(INSTALL not in c["auth"] for c in net.calls if c["url"].endswith(PEER_ALLOWS_PATH))


async def test_turning_another_switch_off_leaves_running_tasks_alone(tmp_path, lan):
    """Only "Send me tasks" going off stops tasks: a folder change, or a
    switch that is already off, stops nothing."""
    a, b = _two(tmp_path)
    lan({A_HOST: a, B_HOST: b})
    started = asyncio.Event()

    async def held_stream(**kw):
        started.set()
        await asyncio.Event().wait()
        yield {}  # pragma: no cover

    b.state.platform.router.stream = held_stream
    async with _client(A_HOST) as ca, _client(B_HOST) as cb:
        await _pair(ca, cb)
        await cb.patch("/peers/laptop/allows", json={"may_send_tasks": True})
        assert (await _task(_laptop_token(a))).status_code == 202
        await asyncio.wait_for(started.wait(), 30)
        out = (await cb.patch("/peers/laptop/allows", json={"may_use_models": False})).json()
        assert out["stopped"] == 0
        (row,) = await asyncio.to_thread(_remote_sessions, b)
        assert row.status not in (SessionStatus.CANCELLED, SessionStatus.FAILED)
        await cb.delete("/peers/laptop")  # ends it (Remove stops its task)
        await _settled(b)


# --------------------------------------------------------------------------- #
# S4 — what the desk lets the laptop do reaches the laptop's card
# --------------------------------------------------------------------------- #


async def test_the_desks_switches_reach_the_laptops_card(tmp_path, lan):
    a, b = _two(tmp_path)
    lan({A_HOST: a, B_HOST: b})
    async with _client(A_HOST) as ca, _client(B_HOST) as cb:
        await _pair(ca, cb)
        before = _view_of((await ca.get("/peers")).json(), "desk")
        assert before["they_allow"]["known"] is False
        assert before["they_line"] == "desk has not said yet what it lets this computer do. Press Check."

        # The desk's probe (the laptop's Check) says what it allows: nothing.
        checked = (await ca.post("/peers/desk/check")).json()
        assert checked["ok"] is True
        assert checked["peer"]["they_allow"]["known"] is True
        assert checked["peer"]["they_allow"]["allows"] == []
        assert checked["peer"]["they_line"] == "desk lets this computer do nothing yet."

        # The desk turns "Send me tasks" on: it TELLS the laptop (no Check).
        await cb.patch("/peers/laptop/allows", json={"may_send_tasks": True})
        await R.drain_tells()
        told = _view_of((await ca.get("/peers")).json(), "desk")
        assert told["they_allow"]["allows"] == [A.SEND_TASKS]
        assert told["they_line"] == "desk lets this computer: send tasks."
        changed = [e.payload for e in a.state.platform.event_bus.history if e.type == "peer.allows_changed"]
        assert changed == [{"name": "desk", "by": "them"}]

        # And the probe agrees, with takes_tasks true now.
        async with httpx.AsyncClient(base_url=f"http://{B_HOST}") as raw:
            probe = await raw.get(TASK_DOOR_PATH, headers={"Authorization": f"Bearer {_laptop_token(a)}"})
        assert probe.status_code == 200
        assert probe.json() == {"ok": True, "takes_tasks": True, "allows": [A.SEND_TASKS]}

    # What the other computer says is only SHOWN: the laptop's own switches
    # for the desk did not move.
    (pa,) = _peers(a)
    assert pa.may_send_tasks is False


async def test_a_switch_that_is_off_is_a_200_at_the_probe_never_a_gone_pairing(tmp_path, lan):
    """The probe is the other computer's Check: a 403 there reads "the
    pairing was removed". A switch that is off answers 200 and says so."""
    a, b = _two(tmp_path)
    lan({A_HOST: a, B_HOST: b})
    async with _client(A_HOST) as ca, _client(B_HOST) as cb:
        await _pair(ca, cb)
        async with httpx.AsyncClient(base_url=f"http://{B_HOST}") as raw:
            probe = await raw.get(TASK_DOOR_PATH, headers={"Authorization": f"Bearer {_laptop_token(a)}"})
        assert probe.status_code == 200
        assert probe.json() == {"ok": True, "takes_tasks": False, "allows": []}
        checked = (await ca.post("/peers/desk/check")).json()
        assert checked["ok"] is True and checked["peer"]["last_error"] == ""


def test_an_older_iron_jarvis_probe_reads_as_taking_tasks():
    """An Iron Jarvis from before the switches answers no ``allows``."""
    assert L.they_allow('{"ok": true, "takes_tasks": true}') == {"allows": [A.SEND_TASKS], "older": True}
    assert L.they_allow('{"ok": true, "takes_tasks": true, "allows": ["may_send_tasks", "junk"]}') == {
        "allows": [A.SEND_TASKS], "older": False,
    }
    assert L.they_allow("not json") == {"allows": [], "older": True}
    they = A.they_allow_of(json.dumps({"allows": [A.SEND_TASKS], "older": True, "at": "x"}))
    assert A.they_line("desk", they) == "desk runs an older Iron Jarvis: it takes tasks from this computer."


# --------------------------------------------------------------------------- #
# S5 — the peer door
# --------------------------------------------------------------------------- #


def test_the_peer_door_is_exempt_for_exactly_its_own_paths():
    assert PEER_DOOR_PATHS == frozenset({PEER_ALLOWS_PATH})
    for path in PEER_DOOR_PATHS:
        assert path.startswith(PD.PREFIX) and _is_exempt(path)
    for guarded in ("/peer/v1", "/peer/v1/", "/peer/v1/allows/", "/peer/v1/allowsx", "/peer/v1/allows/x",
                    "/peer/v2/allows", "/x/peer/v1/allows", "/peer/v1/hand-over"):
        assert not _is_exempt(guarded), guarded


async def test_only_a_paired_computers_own_token_opens_the_peer_door(tmp_path, lan):
    a, b = _two(tmp_path)
    lan({A_HOST: a, B_HOST: b})
    async with _client(A_HOST) as ca, _client(B_HOST) as cb:
        await _pair(ca, cb)
        pane = b.state.platform.pane_tokens.mint("pane-41a", {})
        body = {"protocol": L.PROTOCOL, "allows": [A.SEND_TASKS]}
        async with httpx.AsyncClient(base_url=f"http://{B_HOST}") as raw:
            for path in sorted(PEER_DOOR_PATHS):
                for header in ({}, {"Authorization": f"Bearer {INSTALL}"},
                               {"Authorization": f"Bearer {pane}"},
                               {"Authorization": "Bearer nope-not-a-peer"}):
                    r = await raw.post(path, json=body, headers=header)
                    assert r.status_code == 401, (path, header, r.text)
                    assert r.json()["detail"] == PD.UNKNOWN_TOKEN
                # Another method there is the install guard's, not the door's.
                r = await raw.get(path, headers={"Authorization": f"Bearer {_laptop_token(a)}"})
                assert r.status_code == 401 and r.json()["detail"] == "missing or invalid token"
                # The laptop's own token opens it.
                r = await raw.post(path, json=body, headers={"Authorization": f"Bearer {_laptop_token(a)}"})
                assert r.status_code == 200, r.text
        assert _view_of((await cb.get("/peers")).json(), "laptop")["they_allow"]["allows"] == [A.SEND_TASKS]


async def test_a_peer_token_never_opens_the_switches(tmp_path, lan):
    a, b = _two(tmp_path)
    lan({A_HOST: a, B_HOST: b})
    async with _client(A_HOST) as ca, _client(B_HOST) as cb:
        await _pair(ca, cb)
        async with httpx.AsyncClient(base_url=f"http://{B_HOST}") as raw:
            r = await raw.patch("/peers/laptop/allows", json={"may_send_tasks": True},
                                headers={"Authorization": f"Bearer {_laptop_token(a)}"})
        assert r.status_code == 401
        (pb,) = _peers(b)
        assert pb.may_send_tasks is False, "a computer turned its own switch on"


async def test_a_peer_door_with_a_switch_refuses_in_words_when_it_is_off(tmp_path, lan):
    """``peer_for(request, switch)``: the switch decides every time."""
    from fastapi import HTTPException
    from starlette.requests import Request

    a, b = _two(tmp_path)
    lan({A_HOST: a, B_HOST: b})
    async with _client(A_HOST) as ca, _client(B_HOST) as cb:
        await _pair(ca, cb)
    token = _laptop_token(a)
    scope = {"type": "http", "method": "POST", "path": "/peer/v1/x", "headers": [
        (b"authorization", f"Bearer {token}".encode())], "client": (A_IP, 1)}
    with pytest.raises(HTTPException) as err:
        await PD.peer_for(b.state.platform, Request(scope), A.USE_MODELS)
    assert err.value.status_code == 403
    assert err.value.detail == A.switch_off_words(A.USE_MODELS, "desk", "laptop")
    with pytest.raises(HTTPException) as err:
        await PD.peer_for(b.state.platform, Request(scope), "may_do_anything")
    assert err.value.status_code == 403, "a switch nobody knows is never on"
    assert (await PD.peer_for(b.state.platform, Request(scope))).name == "laptop"


# --------------------------------------------------------------------------- #
# S6 — not-ready switches; folders
# --------------------------------------------------------------------------- #


async def test_a_switch_that_is_not_in_this_version_cannot_be_turned_on(tmp_path, lan):
    a, b = _two(tmp_path)
    lan({A_HOST: a, B_HOST: b})
    async with _client(A_HOST) as ca, _client(B_HOST) as cb:
        await _pair(ca, cb)
        for key in [s.key for s in A.SWITCHES if not s.ready]:
            r = await cb.patch("/peers/laptop/allows", json={key: True})
            assert r.status_code == 409 and r.json()["detail"] == A.not_ready_words(key)
            assert (await cb.patch("/peers/laptop/allows", json={key: False})).status_code == 200
        for bad in ({"may_send_tasks": "yes"}, {"may_do_anything": True}, {}):
            r = await cb.patch("/peers/laptop/allows", json=bad)
            assert r.status_code in (400, 422), (bad, r.text)
        assert (await cb.patch("/peers/nobody/allows", json={"may_send_tasks": True})).status_code == 404
    (pb,) = _peers(b)
    assert not any(getattr(pb, k) for k in A.KEYS)


def test_a_folder_for_a_switch_must_be_a_usable_folder_on_this_pc(tmp_path):
    good = tmp_path / "work"
    good.mkdir()
    assert S._checked_folders([str(good), str(good), "  "], writable=False) == [str(good.resolve())]
    for bad, words in (
        (["relative/folder"], "absolute"),
        ([r"\\desk\share"], "on another computer"),
        (["//desk/share"], "on another computer"),
        ([str(tmp_path / "missing")], "does not exist"),
        ("not a list", "list"),
    ):
        with pytest.raises(S.AllowsProblem) as err:
            S._checked_folders(bad, writable=False)
        assert words in str(err.value), (bad, str(err.value))


# --------------------------------------------------------------------------- #
# S7 — D-41.1: pairings from before this version
# --------------------------------------------------------------------------- #


def _as_before_the_switches(app) -> None:
    """The table an older version left: no switch columns at all. Then the
    boot's reconciler adds them, as it does on an upgrade: empty (NULL)."""
    from iron_jarvis.core.db import _reconcile_additive_columns

    cols = [*A.KEYS, "record_folders_json", "code_folders_json", "they_allow_json", "kept_note"]
    engine = app.state.platform.engine
    with engine.begin() as conn:
        for col in cols:
            conn.execute(text(f"ALTER TABLE peerrecord DROP COLUMN {col}"))
    _reconcile_additive_columns(engine)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT may_send_tasks, kept_note FROM peerrecord")).all() == [(None, None)]


async def test_a_pairing_from_before_keeps_send_me_tasks_and_the_card_says_so_once(tmp_path, lan):
    a, b = _two(tmp_path)
    lan({A_HOST: a, B_HOST: b})
    b.state.platform.router.stream = _scripted("Done here.")
    async with _client(A_HOST) as ca, _client(B_HOST) as cb:
        await _pair(ca, cb)
        _as_before_the_switches(b)
        # The next start of the desk's store settles it (the app's boot does).
        settled = S.PeerStore(b.state.platform.engine, b.state.platform.secrets, b.state.platform.config.home)
        (pb,) = settled.list()
        assert pb.may_send_tasks is True and pb.kept_note == A.KEPT_TASKS
        assert not any(getattr(pb, k) for k in A.KEYS if k != A.SEND_TASKS)
        assert settled.settle_older() == 0, "settled once"

        row = _view_of((await cb.get("/peers")).json(), "laptop")
        assert row["kept_note"] == A.KEPT_TASKS_WORDS
        assert row["may_line"] == "laptop may: send tasks."
        # Its tasks still run, as before this version.
        assert (await _task(_laptop_token(a))).status_code == 202
        await _settled(b)
        # Read once: the note goes and does not come back.
        seen = await cb.patch("/peers/laptop/allows", json={"seen_note": True})
        assert seen.status_code == 200 and seen.json()["after"][A.SEND_TASKS] is True
        assert _view_of((await cb.get("/peers")).json(), "laptop")["kept_note"] == ""
        S.PeerStore(b.state.platform.engine, b.state.platform.secrets, b.state.platform.config.home)
        assert _view_of((await cb.get("/peers")).json(), "laptop")["kept_note"] == ""


def test_a_row_that_could_not_be_settled_is_read_the_way_settling_writes_it():
    old = PeerRecord(name="laptop")
    for key in A.KEYS:
        setattr(old, key, None)
    assert A.may(old, A.SEND_TASKS) is True
    assert not any(A.may(old, k) for k in A.KEYS if k != A.SEND_TASKS)


# --------------------------------------------------------------------------- #
# S8 — chat shows the switches and turns them OFF, never on
# --------------------------------------------------------------------------- #


def _tool_ctx(p):
    from iron_jarvis.tools.base import ToolContext

    return ToolContext(workspace=p.config.home, session_id="chat", agent_run_id="chat", config=p.config,
                       event_bus=p.event_bus, engine=p.engine, thread_id="chat_t1")


async def _invoke(p, name: str, args: dict):
    return await p.registry.invoke(name, args, _tool_ctx(p), p.permissions)


async def test_chat_lists_what_each_paired_computer_may_do(tmp_path, lan):
    from iron_jarvis.settings import records_peers as RP

    a, b = _two(tmp_path)
    lan({A_HOST: a, B_HOST: b})
    async with _client(A_HOST) as ca, _client(B_HOST) as cb:
        await _pair(ca, cb)
        await cb.patch("/peers/laptop/allows", json={"may_send_tasks": True})
        res = await _invoke(b.state.platform, RP.READER, {})
        await R.drain_tells()
    assert res.ok, res.error
    assert "laptop may: send tasks." in res.output
    assert "Coming in a later version: Look up my work record" in res.output
    assert RP.ON_IS_A_PRESS in res.output
    assert res.data["computers"] == [{"name": "laptop", "may": [A.SEND_TASKS], "lets_this_computer": []}]


async def test_chat_turns_a_switch_off_and_only_a_press_turns_it_back_on(tmp_path, lan):
    from fastapi import HTTPException

    from iron_jarvis.daemon.routes.undo import perform_undo
    from iron_jarvis.settings import records_peers as RP

    a, b = _two(tmp_path)
    lan({A_HOST: a, B_HOST: b})
    p = b.state.platform
    async with _client(A_HOST) as ca, _client(B_HOST) as cb:
        await _pair(ca, cb)
        await cb.patch("/peers/laptop/allows", json={"may_send_tasks": True})
        res = await _invoke(p, RP.WRITER, {"computer": "@Laptop", "switch": A.SEND_TASKS})
        assert res.ok, res.error
        card = res.data["record_change"]
        assert (card["label"], card["old"], card["new"]) == ("“Send me tasks” for laptop", "on", "off")
        # Through the layer that refuses: the door turns the laptop away now.
        assert (await _task(_laptop_token(a))).status_code == 403

        action = (await cb.get(f"/config/changes/{card['change_id']}")).json()["action_id"]
        # A model's undo ("undo that" in chat) never turns a switch on.
        with pytest.raises(HTTPException) as err:
            await perform_undo(p.config_writer.d, action)
        assert err.value.status_code == 409 and err.value.detail == RP.PRESS_TO_UNDO
        assert _peers(b)[0].may_send_tasks is False
        # The person's Undo press does.
        pressed = await cb.post(f"/undo/{action}")
        assert pressed.status_code == 200, pressed.text
        assert _peers(b)[0].may_send_tasks is True
        assert (await _task(_laptop_token(a))).status_code == 202
        await _settled(b)
        await R.drain_tells()


async def test_an_undo_never_writes_over_a_switch_changed_again_since(tmp_path, lan):
    from iron_jarvis.settings import records as RR
    from iron_jarvis.settings import records_peers as RP

    a, b = _two(tmp_path)
    lan({A_HOST: a, B_HOST: b})
    p = b.state.platform
    async with _client(A_HOST) as ca, _client(B_HOST) as cb:
        await _pair(ca, cb)
        await cb.patch("/peers/laptop/allows", json={"may_send_tasks": True})
        tool = p.registry.get(RP.WRITER)
        args = {"computer": "laptop", "switch": RP.ALL}
        desc = await tool.capture_undo(args, _tool_ctx(p))
        res = await tool.execute(args, _tool_ctx(p))
        assert res.ok and _peers(b)[0].may_send_tasks is False
        stored = json.loads(desc["pre_inline"])
        assert "pressed" not in stored, "only perform_undo says a person pressed"
        assert json.loads(stored["now"]) == [A.SEND_TASKS]
        # Not pressed: refused, nothing changed.
        with pytest.raises(RR.RecordError) as err:
            RR.restore(p, stored)
        assert str(err.value) == RP.PRESS_TO_UNDO and _peers(b)[0].may_send_tasks is False
        # On again on the card since: the Undo refuses to write over it.
        await cb.patch("/peers/laptop/allows", json={"may_send_tasks": True})
        with pytest.raises(RR.RecordError) as err:
            RR.restore(p, {**stored, "pressed": True})
        assert "changed again since" in str(err.value)
        # Off again: pressed, it puts back what it turned off.
        await cb.patch("/peers/laptop/allows", json={"may_send_tasks": False})
        assert RR.restore(p, {**stored, "pressed": True}) == "undo: laptop may: send tasks."
        assert _peers(b)[0].may_send_tasks is True
        await R.drain_tells()


async def test_chat_has_no_way_to_turn_a_switch_on(tmp_path, lan):
    from iron_jarvis.settings import records_peers as RP

    a, b = _two(tmp_path)
    lan({A_HOST: a, B_HOST: b})
    p = b.state.platform
    async with _client(A_HOST) as ca, _client(B_HOST) as cb:
        await _pair(ca, cb)
    for name in (RP.READER, RP.WRITER):
        props = p.registry.get(name).input_schema.get("properties", {})
        assert not any(v.get("type") == "boolean" for v in props.values()), name
    res = await _invoke(p, RP.WRITER, {"computer": "laptop", "switch": A.SEND_TASKS})
    assert res.ok is False and "already off" in res.error
    res = await _invoke(p, RP.WRITER, {"computer": "nobody", "switch": A.SEND_TASKS})
    assert res.ok is False and res.error == "Say which computer: laptop."
    assert not any(getattr(_peers(b)[0], k) for k in A.KEYS)


def test_the_writer_is_kept_from_a_low_run_and_neither_reaches_a_task_from_another_computer():
    from iron_jarvis.core.trust import LOW_TRUST_DENY, REMOTE_RUN_DENY, remote_run_keeps
    from iron_jarvis.settings import records as RR
    from iron_jarvis.settings import records_peers as RP

    assert RP.WRITER in LOW_TRUST_DENY and RP.READER not in LOW_TRUST_DENY
    assert RP.READER in REMOTE_RUN_DENY
    assert not remote_run_keeps(RP.READER) and not remote_run_keeps(RP.WRITER)
    assert RR.RECORD_TOOL_TIERS[RP.WRITER] == "allow" and RP.READER not in RR.RECORD_TOOL_TIERS


ARMS = [
    ("what may my desk do?", {"peer_allows"}),
    ("can my laptop send me tasks?", {"peer_allows"}),
    ("show the switches for my other computer", {"peer_allows"}),
    ("stop desk sending me tasks", {"peer_allows", "peer_allow_off"}),
    ("don't let the other computer use my models", {"peer_allows", "peer_allow_off"}),
    ("turn off Send me tasks for laptop", {"peer_allows", "peer_allow_off"}),
    ("block tasks from my laptop", {"peer_allows", "peer_allow_off"}),
    # Turning on is the card's press: the reader only (the page door says it).
    ("let desk send me tasks", {"peer_allows"}),
]
NEAR = [
    "the desk is noisy today",
    "stop the music on my desk",
    "stop sending tasks to the desk",
    "send me the report on the desk's budget",
    "my laptop battery is low",
    "what may I do about the late filing?",
    "turn off the lights",
]


@pytest.mark.parametrize("text,want", ARMS)
def test_the_words_arm_the_tools(text, want):
    from iron_jarvis.settings import records_peers as RP

    assert set(RP.peers_tools_for(text, ("desk", "laptop"))) == want


@pytest.mark.parametrize("text", NEAR)
def test_everyday_words_arm_no_writer(text):
    from iron_jarvis.settings import records_peers as RP

    assert RP.WRITER not in RP.peers_tools_for(text, ("desk", "laptop")), text


def test_with_no_pairing_a_name_arms_nothing():
    from iron_jarvis.settings import records_peers as RP

    assert RP.peers_tools_for("what may my desk do?", ()) == []


async def test_both_chat_lanes_arm_them_from_the_pairings_names(tmp_path, lan):
    """The one function both lanes run off the loop (chat_turn.
    settings_ask_tools; the POST lane keeps its allow-tier subset)."""
    from iron_jarvis.daemon import chat_turn as CT

    a, b = _two(tmp_path)
    lan({A_HOST: a, B_HOST: b})
    async with _client(A_HOST) as ca, _client(B_HOST) as cb:
        await _pair(ca, cb)
    d = b.state.platform.config_writer.d
    stream = await asyncio.to_thread(CT.settings_ask_tools, d, "stop laptop sending me tasks", [], [])
    assert {"peer_allows", "peer_allow_off"} <= set(stream)
    post = await asyncio.to_thread(CT.post_record_tools, d, "what may my laptop do?", [])
    assert "peer_allows" in post


def test_turning_one_on_gets_the_cards_door():
    from iron_jarvis.daemon import page_doors as P

    for words in ("let desk send me tasks", "allow my other computer to send work here",
                 "turn on send me tasks for my laptop"):
        step = P.page_step(words)
        assert step is not None and step.id == "peer_switches", words
        assert step.href == P.PAIRING_HREF
    for words in ("don't let desk send me tasks", "stop sending tasks to the desk", "let me know when it is done"):
        step = P.page_step(words)
        assert step is None or step.id != "peer_switches", words


def test_the_patterns_are_linear_on_pathological_input():
    """A new regex over user text gets a pathological-input pin, in a child
    process with a deadline."""
    import subprocess
    import sys

    code = (
        "import time\n"
        "from iron_jarvis.settings import records_peers as RP\n"
        "bad = ['send ' * 20000, 'send me ' + 'x ' * 40000, 'tasks from ' * 15000, 'dont ' * 30000,"
        " '@' * 60000, 'other ' * 30000 + 'computer', 'let ' * 30000 + 'send me tasks']\n"
        "t = time.perf_counter()\n"
        "for s in bad:\n"
        "    RP.peers_tools_for(s, ('desk',)); RP.switch_on_asked(s); RP.might_ask(s)\n"
        "print(time.perf_counter() - t)\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert float(out.stdout.strip()) < 5.0


async def test_a_switch_turned_off_while_a_task_was_being_made_stops_it_before_it_starts(tmp_path, lan):
    """The card's stop pass reads only the rows already there: a task that
    passed the switch a moment before it went off, and whose row was made a
    moment after, is caught by the door's second read, stopped before it
    starts, and refused in words."""
    a, b = _two(tmp_path)
    lan({A_HOST: a, B_HOST: b})
    ran = []

    async def never(**kw):
        ran.append(1)
        yield {"type": "final", "response": LLMResponse(text="ran"), "provider": "openai", "model": "gpt-5"}

    b.state.platform.router.stream = never
    async with _client(A_HOST) as ca, _client(B_HOST) as cb:
        await _pair(ca, cb)
        await cb.patch("/peers/laptop/allows", json={"may_send_tasks": True})
        orch = b.state.orchestrator
        real = orch.create_session
        store = S.store_for(b.state.platform)

        async def made_then_turned_off(*args, **kw):
            session = await real(*args, **kw)
            (peer,) = await asyncio.to_thread(store.list)
            await asyncio.to_thread(store.set_allows, peer.id, {A.SEND_TASKS: False})
            return session

        orch.create_session = made_then_turned_off
        try:
            r = await _task(_laptop_token(a))
        finally:
            orch.create_session = real
        assert r.status_code == 403 and r.json()["detail"] == A.tasks_off_words("desk", "laptop")
        (row,) = await asyncio.to_thread(_remote_sessions, b)
        assert row.status is SessionStatus.CANCELLED
        assert R.tasks_off_stop_summary("laptop") in (row.summary or "")
        await R.drain_tells()
    assert ran == [], "the task ran after its switch went off"
