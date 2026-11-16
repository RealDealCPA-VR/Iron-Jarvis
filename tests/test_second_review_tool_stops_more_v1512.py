"""v1.512.0 (second review, group 12 part B): four places around the service
that runs other coding tools where a Stop was not honoured or work started by
itself.

* H3: Emergency stop reaches a reply Iron Jarvis carried on BY ITSELF (or a
  hand-off helper's piece) after the code host carried it across a restart —
  never a reply the user started.
* H13: a tool whose stop the code host never CONFIRMED (the link broke during
  it) stays on the list the next link stops.
* H14: a rewind or a whole-conversation branch whose newest reply was cut off
  is never carried on by itself at the next start.
* A11: a Stop pressed on a Codex reply before Codex named the turn is sent
  once the name arrives; never with the previous turn's id; a remembered Stop
  never reaches a later turn.

No real coding tool is started anywhere here: the code host is a socket pair
or a stub link, Codex is a driver with a scripted ``write``.
"""

from __future__ import annotations

import asyncio
import json
import socket
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from iron_jarvis import codehost as CH
from iron_jarvis.daemon.app import create_app
from iron_jarvis.hosted import adopt as ADOPT
from iron_jarvis.hosted import branching as BR
from iron_jarvis.hosted import carry_on as CO
from iron_jarvis.hosted import codex as X
from iron_jarvis.hosted import hostlink as HL
from iron_jarvis.hosted import service as S
from iron_jarvis.hosted import store
from iron_jarvis.hosted import words as W
from iron_jarvis.hosted.events import session_dir
from tests._build_app_helpers_v1409 import stop_all, until


# --------------------------------------------------------------------------- #
# H3. Emergency stop and a reply carried across a restart
# --------------------------------------------------------------------------- #
class _KeptLink:
    """The code host as adoption sees it: it holds one tool and its kept
    state; every other request is answered "ok"."""

    alive = True
    deliberate = False
    tools: list = []

    def __init__(self, blob: bytes) -> None:
        self.blob = blob
        self.procs: dict = {}

    def register(self, tool, proc) -> None:
        self.procs[tool] = proc

    def unregister(self, tool) -> None:
        self.procs.pop(tool, None)

    def request(self, header, payload=b"", timeout=0.0):
        if header.get("t") == "attach":
            return {"ok": True, "pid": 0, "snap_seq": 0, "writes_upto": 0}, self.blob
        return {"ok": True}, b""

    def send(self, header, payload=b"") -> None:
        pass


def _iso(when: datetime) -> str:
    return when.astimezone(timezone.utc).isoformat()


async def _adopted(svc, folder: Path, turn_id: str, carry, *, handed_from: str = "") -> str:
    """A session whose reply ``turn_id`` was running when Iron Jarvis
    restarted, taken back from the code host the way the boot takes it
    (``adopt._adopt_one``). ``carry`` is what started the reply (None: the
    user's own message)."""
    row = store.create(svc.engine, harness="claude-code", folder=str(folder))
    if handed_from:
        row = store.update(svc.engine, row.id, handed_from=handed_from)
    began = datetime.now(timezone.utc) - timedelta(minutes=2)
    user = {"type": "user", "at": _iso(began), "text": "Write the weekly report", "turn": turn_id}
    if carry:
        user["carry"] = dict(carry)
    d = session_dir(svc.home, row.id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "events.jsonl").write_text(json.dumps(user) + "\n", encoding="utf-8")
    # The state the code host kept while the reply ran (before the restart).
    turn = S.HostedTurn(turn_id, row.id)
    turn.carry = dict(carry) if carry else None
    turn.started_at = began
    before = SimpleNamespace(turn=turn, asks={}, title="A report", caps={}, folder=str(folder), restart=False,
                             sid=row.id, kept={}, harness="claude-code", driver=None, background_tasks=0,
                             working_alone=False)
    blob, _busy = ADOPT.state_blob(svc, before)
    ok = await ADOPT._adopt_one(svc, _KeptLink(blob), {"sid": row.id, "tool": f"tool-{turn_id}"})
    assert ok, "the session was taken back"
    live = svc.live[row.id]
    assert live.turn is not None and live.turn.id == turn_id and live.turn.running
    assert live.turn.task is None, "an adopted reply has no task of this daemon's"
    return row.id


def _spy_interrupt(svc):
    loop = asyncio.get_running_loop()
    stopped: list = []

    async def spy(sid):
        stopped.append((sid, asyncio.get_running_loop() is loop))
        return {"ok": True}

    svc.interrupt = spy  # type: ignore[method-assign]
    return stopped


async def test_emergency_stop_stops_an_adopted_reply_jarvis_carried_on_but_not_one_the_user_started(tmp_path):
    app = create_app(str(tmp_path / "home"))
    try:
        svc = S.service_for(app.state.platform)
        carried = await _adopted(svc, tmp_path, "turn-carried", {"why": "restart", "from": "turn-0"})
        users = await _adopted(svc, tmp_path, "turn-user", None)
        pressed = await _adopted(svc, tmp_path, "turn-pressed",
                                 {"why": "restart", "from": "turn-1", "pressed": True})
        stopped = _spy_interrupt(svc)
        # Emergency stop runs off the loop (a threadpool route, the switch's watcher).
        got = await asyncio.to_thread(CO.stop_unattended_turns, app.state.platform)
        assert got == ["build_app:turn-carried"], got
        await until(lambda: stopped, timeout=10)
        assert stopped == [(carried, True)], "stopped on the service's own loop"
        assert users not in [s for s, _ in stopped] and pressed not in [s for s, _ in stopped]
    finally:
        await stop_all(app)


async def test_a_kept_state_from_before_says_nothing_so_the_reply_is_never_stopped(tmp_path):
    """An adopted reply whose kept state does not say Iron Jarvis started it
    (a state kept by an older version) is the user's: never stopped."""
    app = create_app(str(tmp_path / "home"))
    try:
        svc = S.service_for(app.state.platform)
        sid = await _adopted(svc, tmp_path, "turn-old-state", None)
        svc.live[sid].turn.__dict__.pop("carry", None)
        stopped = _spy_interrupt(svc)
        assert await asyncio.to_thread(CO.stop_unattended_turns, app.state.platform) == []
        assert stopped == []
    finally:
        await stop_all(app)


async def test_emergency_stop_stops_an_adopted_helpers_piece_after_a_restart(tmp_path):
    """``svc.handoff_turns`` is memory only: after a restart a helper's piece
    is known by the session row's ``handed_from`` and the piece's own id."""
    app = create_app(str(tmp_path / "home"))
    try:
        svc = S.service_for(app.state.platform)
        parent = store.create(svc.engine, harness="claude-code", folder=str(tmp_path))
        helper = await _adopted(svc, tmp_path, "handoff-0123abcd4567", None, handed_from=parent.id)
        typed = await _adopted(svc, tmp_path, "turn-typed-in-helper", None, handed_from=parent.id)
        assert svc.handoff_turns == {}, "a restart forgot the helpers' pieces"
        stopped = _spy_interrupt(svc)
        got = await asyncio.to_thread(CO.stop_unattended_turns, app.state.platform)
        assert got == ["build_app:handoff-0123abcd4567"], got
        await until(lambda: stopped, timeout=10)
        assert [s for s, _ in stopped] == [helper]
        assert typed not in [s for s, _ in stopped], "a message the user typed into a helper keeps going"
    finally:
        await stop_all(app)


# --------------------------------------------------------------------------- #
# H13. A stop the code host never confirmed
# --------------------------------------------------------------------------- #
def _link(home: Path):
    """A real HostLink on one end of a socket pair; the test is the host."""
    a, b = socket.socketpair()
    b.settimeout(15)
    lk = HL.HostLink(home, a, a.makefile("rb"), {"pid": 0, "tools": []})
    gone = threading.Event()
    real = lk._lost

    def lost() -> None:
        real()
        gone.set()

    lk._lost = lost  # type: ignore[method-assign]
    return lk, b, gone


def _proc(home: Path, lk, tool: str):
    proc = HL.RemoteProcess([], env={}, cwd="", name="t", home=home, sid="s1", tool_id=tool)
    proc.link = lk
    lk.register(tool, proc)
    return proc


def _host_reads_stop(host: socket.socket) -> dict:
    got = CH.read_frame(host.makefile("rb"))
    assert got is not None and got[0].get("t") == "stop", got
    return got[0]


@pytest.fixture()
def home(tmp_path):
    h = tmp_path / "home"
    h.mkdir()
    HL._LOST.pop(HL._key(h), None)
    yield h
    HL._LOST.pop(HL._key(h), None)


def test_a_stop_the_link_broke_during_stays_on_the_next_links_list(home):
    lk, host, gone = _link(home)
    proc = _proc(home, lk, "tool-a")
    t = threading.Thread(target=proc.stop, kwargs={"graceful": True})
    t.start()
    _host_reads_stop(host)  # the host heard the stop ...
    host.shutdown(socket.SHUT_RDWR)  # ... and the link broke before it answered
    host.close()
    assert gone.wait(15)
    t.join(15)
    assert not t.is_alive()
    assert "tool-a" in HL._LOST.get(HL._key(home), set()), "the next link stops it"
    assert proc.stop_confirmed is False


def test_the_broken_links_own_list_names_a_tool_whose_stop_was_in_flight(home, monkeypatch):
    """The link's list alone (``HostLink._lost``), without the stop's own
    failure path: a stop asked for is not a stop confirmed."""
    monkeypatch.setattr(HL, "_still_to_stop", lambda lk, tool: None)
    lk, host, gone = _link(home)
    proc = _proc(home, lk, "tool-d")
    t = threading.Thread(target=proc.stop, kwargs={"graceful": True})
    t.start()
    _host_reads_stop(host)
    host.shutdown(socket.SHUT_RDWR)
    host.close()
    assert gone.wait(15)
    t.join(15)
    assert "tool-d" in HL._LOST.get(HL._key(home), set())


def test_a_stop_the_host_never_answered_stays_on_the_list(home, monkeypatch):
    """The link stays up but the host does not answer the stop in time: the
    tool may still run there, so the next link stops it."""
    monkeypatch.setattr(HL, "STOP_REPLY_S", 0.3)
    lk, host, gone = _link(home)
    proc = _proc(home, lk, "tool-e")
    t = threading.Thread(target=proc.stop, kwargs={"graceful": True})
    t.start()
    _host_reads_stop(host)  # heard, never answered
    t.join(15)
    assert not t.is_alive() and lk.alive and proc.stop_confirmed is False
    assert "tool-e" in HL._LOST.get(HL._key(home), set())
    lk.close()
    host.close()


def test_a_stop_the_host_confirmed_is_not_stopped_again(home):
    lk, host, gone = _link(home)
    proc = _proc(home, lk, "tool-b")
    t = threading.Thread(target=proc.stop, kwargs={"graceful": True})
    t.start()
    header = _host_reads_stop(host)
    CH.send_frame(host, {"t": "re", "re": header["re"], "ok": True, "code": 0})
    t.join(15)
    assert not t.is_alive() and proc.stop_confirmed is True
    host.shutdown(socket.SHUT_RDWR)
    host.close()
    assert gone.wait(15)
    assert "tool-b" not in HL._LOST.get(HL._key(home), set())


def test_a_deliberate_close_during_a_stop_leaves_nothing_to_stop(home):
    """A restart's detach keeps the tools running: a stop that failed because
    the link was closed ON PURPOSE is not put on the list."""
    lk, host, gone = _link(home)
    proc = _proc(home, lk, "tool-c")
    t = threading.Thread(target=proc.stop, kwargs={"graceful": True})
    t.start()
    _host_reads_stop(host)
    lk.close()
    assert gone.wait(15)
    t.join(15)
    host.close()
    assert not HL._LOST.get(HL._key(home))


# --------------------------------------------------------------------------- #
# H14. A rewind or a branch whose newest reply was cut off
# --------------------------------------------------------------------------- #
@pytest.fixture()
def claude_home(tmp_path, monkeypatch) -> Path:
    h = tmp_path / "claude-home"
    h.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(h))
    return h


def _cut_reply(turn: str, at: datetime, text: str) -> list[dict]:
    """A reply Iron Jarvis's own stop ended (a restart cut it off)."""
    return [
        {"type": "user", "at": _iso(at), "text": text, "turn": turn},
        {"type": "text", "at": _iso(at + timedelta(seconds=1)), "item": "i1", "delta": "Working on it", "turn": turn},
        {**W.error(S.STOPPED_TITLE, S.STOPPED_FIX, ""), "at": _iso(at + timedelta(seconds=2)), "turn": turn},
        {"type": "done", "at": _iso(at + timedelta(seconds=2)), "stop": "interrupted", "turn": turn},
    ]


def _session_with_log(svc, folder: Path, events: list[dict], hid: str) -> str:
    row = store.create(svc.engine, harness="claude-code", folder=str(folder), harness_session_id=hid)
    d = session_dir(svc.home, row.id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    return row.id


def _offered(home: Path, sid: str, turn: str) -> None:
    """The boot found ``turn`` cut and the pass OFFERED it (Continue)."""
    CO.note_cut(home, sid, {"turn": turn, "cut_at": _iso(datetime.now(timezone.utc))})
    assert CO._amend_cut(home, sid, turn, auto="offered", why_not=CO.WHY_POOL)


def _carrying_since_long_ago(home: Path) -> None:
    CO._write(CO.since_path(home), {"since": _iso(datetime.now(timezone.utc) - timedelta(days=30))})


async def _next_start(svc) -> list[str]:
    """The next start of Iron Jarvis: the boot reconcile, then the carry-on
    pass — with every start it makes recorded instead of run."""
    await asyncio.to_thread(S.reconcile_cut_turns, svc.home)
    started: list[str] = []

    async def start_turn(sid, text, turn_id, carry=None, **kw):
        started.append(sid)
        return SimpleNamespace(id=turn_id)

    svc.start_turn = start_turn  # type: ignore[method-assign]
    await CO.carry_on_after_restart(svc)
    return started


async def test_a_whole_branch_whose_newest_reply_was_cut_is_not_carried_on_by_itself(tmp_path, claude_home):
    app = create_app(str(tmp_path / "home"))
    try:
        svc = S.service_for(app.state.platform)
        _carrying_since_long_ago(svc.home)
        at = datetime.now(timezone.utc) - timedelta(hours=1)
        sid = _session_with_log(svc, tmp_path, _cut_reply("turn-a", at, "Tidy the folder"), str(uuid.uuid4()))
        _offered(svc.home, sid, "turn-a")
        made = (await BR.fork(svc, sid))["session"]["id"]
        assert made != sid
        started = await _next_start(svc)
        assert made not in started, "the branch's cut reply was carried on with nobody asking"
        assert sid not in started
        rec = CO.read_cut(svc.home, made)
        assert rec.get("turn") == "turn-a" and rec.get("auto") == "offered" and rec.get("why_not")
    finally:
        await stop_all(app)


def _claude_transcript(home: Path, hid: str, uuids: list[str]) -> None:
    path = home / "projects" / "C--work" / f"{hid}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    parent = None
    lines = []
    for i, u in enumerate(uuids):
        lines.append({"type": "user", "uuid": u, "parentUuid": parent, "sessionId": hid,
                      "message": {"role": "user", "content": f"m{i}"}})
        lines.append({"type": "assistant", "uuid": f"asst-{i}", "parentUuid": u, "sessionId": hid,
                      "message": {"role": "assistant", "content": [{"type": "text", "text": f"r{i}"}]}})
        parent = f"asst-{i}"
    path.write_text("".join(json.dumps(x) + "\n" for x in lines), encoding="utf-8")


async def test_a_rewind_back_to_a_cut_reply_is_not_carried_on_by_itself(tmp_path, claude_home):
    """Reply A was cut and only offered; the user sent B (also cut, offered:
    the record now names B); the user rewinds to B. The log ends on A again,
    and the next start must not send "carry on" into it."""
    app = create_app(str(tmp_path / "home"))
    try:
        svc = S.service_for(app.state.platform)
        _carrying_since_long_ago(svc.home)
        hid = str(uuid.uuid4())
        at = datetime.now(timezone.utc) - timedelta(hours=2)
        events = _cut_reply("turn-a", at, "Tidy the folder") + _cut_reply(
            "turn-b", at + timedelta(minutes=30), "Now list what moved")
        sid = _session_with_log(svc, tmp_path, events, hid)
        _offered(svc.home, sid, "turn-b")
        a_at, b_at = events[0]["at"], events[4]["at"]
        BR.note_sent(svc.home, sid, a_at, "uuid-a", hid, "Tidy the folder")
        BR.note_sent(svc.home, sid, b_at, "uuid-b", hid, "Now list what moved")
        _claude_transcript(claude_home, hid, ["uuid-a", "uuid-b"])
        await BR.rewind(svc, sid, b_at)
        assert CO.newest_turn(CO._tail_events(session_dir(svc.home, sid) / "events.jsonl")) == "turn-a"
        started = await _next_start(svc)
        assert sid not in started, "the rewound conversation was carried on with nobody asking"
        rec = CO.read_cut(svc.home, sid)
        assert rec.get("turn") == "turn-a" and rec.get("auto") == "offered"
    finally:
        await stop_all(app)


# --------------------------------------------------------------------------- #
# A11. A Stop before Codex has named the turn
# --------------------------------------------------------------------------- #
def _codex():
    wrote: list = []
    held: dict = {}

    async def write(obj):
        wrote.append(obj)
        if obj.get("method") == "turn/start":
            held["id"] = obj["id"]  # its answer comes when the test says
        elif "id" in obj and obj.get("method"):
            d._replies.settle(obj["id"], {})

    d = X.CodexDriver(write=write, emit=lambda e: None, ask=lambda a: None, on_session_id=lambda s: None,
                      mode="ask")
    d.session_id = "thread-1"
    return d, wrote, held


def _interrupts(wrote: list) -> list[str]:
    return [w["params"]["turnId"] for w in wrote if w.get("method") == "turn/interrupt"]


async def _sent(d, held: dict, text: str):
    held.pop("id", None)
    task = asyncio.ensure_future(d.send_user(text, []))
    await until(lambda: "id" in held, timeout=10)  # turn/start is out, its answer is not in
    return task


async def test_a_codex_stop_before_the_turn_is_named_is_sent_when_turn_start_names_it():
    d, wrote, held = _codex()
    task = await _sent(d, held, "Write the weekly report")
    await d.interrupt()  # the user's Stop
    assert _interrupts(wrote) == [], "nothing to name yet"
    d._replies.settle(held["id"], {"turn": {"id": "turn-a"}})
    await task
    await until(lambda: _interrupts(wrote), timeout=5)
    assert _interrupts(wrote) == ["turn-a"]


async def test_a_codex_stop_before_the_turn_is_named_is_sent_when_turn_started_names_it():
    d, wrote, held = _codex()
    task = await _sent(d, held, "Write the weekly report")
    await d.interrupt()
    # Another thread's turn (a Codex sub-agent) never takes the Stop.
    d.on_message({"method": "turn/started", "params": {"threadId": "thread-sub", "turn": {"id": "turn-sub"}}})
    await asyncio.sleep(0)
    assert _interrupts(wrote) == []
    d.on_message({"method": "turn/started", "params": {"threadId": "thread-1", "turn": {"id": "turn-a"}}})
    await until(lambda: _interrupts(wrote), timeout=5)
    d._replies.settle(held["id"], {"turn": {"id": "turn-a"}})
    await task
    assert _interrupts(wrote) == ["turn-a"], "sent once"


async def test_a_codex_stop_on_a_second_message_never_uses_the_first_turns_id():
    d, wrote, held = _codex()
    d.turn_id = "turn-first"  # the first message's turn, ended
    task = await _sent(d, held, "And now the summary")
    await d.interrupt()
    assert "turn-first" not in _interrupts(wrote), "Codex refuses an old turn's id: the reply ran on"
    d._replies.settle(held["id"], {"turn": {"id": "turn-second"}})
    await task
    await until(lambda: _interrupts(wrote), timeout=5)
    assert _interrupts(wrote) == ["turn-second"]


async def test_a_remembered_codex_stop_never_reaches_a_later_turn():
    d, wrote, held = _codex()
    task = await _sent(d, held, "Write the weekly report")
    await d.interrupt()
    # Codex ended that turn by itself before Iron Jarvis heard its name.
    d.on_message({"method": "turn/completed", "params": {"threadId": "thread-1", "turn": {"status": "completed"}}})
    d._replies.settle(held["id"], {})
    await task
    assert d.turn_open is False
    task2 = await _sent(d, held, "And now the summary")
    d._replies.settle(held["id"], {"turn": {"id": "turn-later"}})
    await task2
    d.on_message({"method": "turn/started", "params": {"threadId": "thread-1", "turn": {"id": "turn-later"}}})
    await asyncio.sleep(0)
    assert _interrupts(wrote) == [], "a Stop for an ended turn is dropped"
