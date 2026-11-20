"""Build-out plan R0.1: the task door PLANS a paired computer's chat history.

The door used to keep the newest 20 rows of the sender's conversation and
then 8,000 characters of them (``task_door.py`` ``[-HISTORY_ROWS_IN_TASK:]``):
a count, not a size, and whatever fell off was gone without a word. "History
is budgeted, never sliced" (``docs/rules/context.md``): the conversation now
goes through ``context.budget.plan_history``, like every chat turn.

  P1 a chat that fits arrives whole (30 short rows, where 20 used to).
  P2 the room is a share of the window the RUN will be planned against (the
     run's own resolver, so a pinned 8,192 gives 2,048), never more than
     ``TASK_TOKENS_MAX``; what does not fit keeps the newest rows whole and
     says how many earlier ones were left out, and how they began.
  P3 nothing that rode along is dropped silently, past 80 rows included.
  P4 the shape of a task that fits is unchanged; a newest row too big for the
     room alone is cut and marked; no history is byte-identical to before.
  P5 the planning runs off the event loop (a body may be 2 MB).
  P6 (review) the count is the SENDER's: the rows it held back (its own
     80-row tail, the chat page's 600 rows, the phone's 60) ride to a task
     door as ``history_held_back`` and count as left out, so the task never
     states fewer than were; every other remote's body is unchanged.
  P7 (review) the room is priced at the run's MEASURED ratio, labels
     included, and nothing but the layout is held back from it.

Driven through the real door, pairing store, orchestrator and runtime; the
model is the one stand-in (a scripted stream). P6 drives the real sending
side too (``AgentThreads.remote_history``, ``RemoteAgentRegistry.run``, the
chat page's route, the phone's lane); the network is the other stand-in.
"""

from __future__ import annotations

import asyncio
import json
import re
import threading
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from iron_jarvis.agents import task_door as td
from iron_jarvis.agents.remote import RemoteAgentRegistry
from iron_jarvis.agents.threads import AgentThreads
from iron_jarvis.comm.inbound import InboundPoller
from iron_jarvis.comm.threads import CommThreadStore
from iron_jarvis.context.budget import RECAP_RESERVE, estimate_tokens
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import Session, SessionStatus
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.auth import TASK_DOOR_PATH
from iron_jarvis.peers import store as S
from iron_jarvis.providers.adapters.base import LLMResponse

DOOR_HOST = "b-pc:8787"
LAPTOP_IP = "10.0.0.1"
#: What the laptop shows the desk's door: a plain phrase, never shaped like a
#: real credential (the repository is public).
PAIRING_KEY = "the-laptops-pairing-key-for-this-test"

LEFT_OUT = re.compile(r"(\d+) earlier messages? (?:was|were) left out to fit")


# --------------------------------------------------------------------------- #
# harness
# --------------------------------------------------------------------------- #


def _desk(tmp_path, monkeypatch):
    """The desk, with the laptop PAIRED to it (the store's own ``add``)."""
    monkeypatch.setenv("IRONJARVIS_HOST_ALLOWLIST", "b-pc")
    app = create_app(str(tmp_path / "desk"))
    platform = app.state.platform
    if getattr(platform, "orchestrator", None) is None:
        platform.orchestrator = app.state.orchestrator
    S.store_for(platform).add(
        name="laptop",
        their_name="desk",
        address=f"http://{LAPTOP_IP}:8787",
        in_token=PAIRING_KEY,
        out_token="the-desks-key-for-the-laptop",
        my_address=f"http://{DOOR_HOST}",
    )
    return app, platform


class _Model:
    """The scripted model: records what each call was sent, then answers."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def stream(self, *, system=None, messages=None, tools=None, **kw):
        self.sent.append(
            "\n".join(
                str(getattr(m, "content", None) or (m.get("content") if isinstance(m, dict) else ""))
                for m in (messages or [])
            )
        )
        yield {"type": "final", "response": LLMResponse(text="Done here."),
               "provider": "openai", "model": "gpt-5"}


def _remote_rows(engine) -> list[Session]:
    with session_scope(engine) as db:
        rows = [s for s in db.exec(select(Session)) if td.is_remote_task_origin(s.origin)]
        for r in rows:
            db.expunge(r)
        return rows


async def _settled(app, timeout_s: float = 30.0) -> Session:
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout_s
    running = app.state.orchestrator._running
    while True:
        rows = await asyncio.to_thread(_remote_rows, app.state.platform.engine)
        done = [
            r for r in rows
            if r.status in (SessionStatus.COMPLETED, SessionStatus.FAILED, SessionStatus.CANCELLED)
            and (running.get(r.id) is None or running[r.id].done())
        ]
        if done or loop.time() > end:
            break
        await asyncio.sleep(0.05)
    await td.drain_replies()
    (row,) = await asyncio.to_thread(_remote_rows, app.state.platform.engine)
    return row


async def _send(app, history: list[dict], task: str = "pick up where we left off") -> httpx.Response:
    transport = httpx.ASGITransport(app=app, client=(LAPTOP_IP, 50000))
    async with httpx.AsyncClient(transport=transport, base_url=f"http://{DOOR_HOST}") as c:
        return await c.post(
            TASK_DOOR_PATH,
            json={"task": task, "conversation_id": "athr_1", "history": history},
            headers={"Authorization": f"Bearer {PAIRING_KEY}"},
        )


def _chat(n: int, words: int = 3) -> list[dict]:
    """``n`` rows, the person first, each opening with its own marker."""
    return [
        {"role": "user" if i % 2 == 0 else "assistant",
         "content": f"m{i:02d} " + " ".join(["word"] * words)}
        for i in range(n)
    ]


def _line(row: dict) -> str:
    who = "The person" if row["role"] == "user" else "Assistant"
    return f"{who}: {row['content']}"


def _kept_markers(text: str, n: int) -> list[int]:
    """Which rows arrived as a whole line of the conversation (a left-out
    row's opening is a "- " line, never one of these)."""
    return [i for i in range(n) if re.search(rf"^(?:The person|Assistant): m{i:02d} ", text, re.M)]


# --------------------------------------------------------------------------- #
# P1 — a chat that fits arrives whole
# --------------------------------------------------------------------------- #


async def test_a_paired_computers_chat_that_fits_reaches_the_run_whole(tmp_path, monkeypatch):
    app, platform = _desk(tmp_path, monkeypatch)
    model = _Model()
    platform.router.stream = model.stream
    history = _chat(30)

    r = await _send(app, history)
    assert r.status_code == 202, r.text
    row = await _settled(app)

    assert _kept_markers(row.task, 30) == list(range(30)), (
        "the oldest rows of a chat that fits were cut off (the old 20-row tail)"
    )
    for h in history:
        assert _line(h) in row.task
    assert not LEFT_OUT.search(row.task), "nothing was left out, so nothing may say so"
    # what the run really sent its model carries them too
    assert model.sent and "m00 word word word" in model.sent[0]


# --------------------------------------------------------------------------- #
# P2 — the room follows the run's window; the newest kept whole; said
# --------------------------------------------------------------------------- #


async def test_a_chat_too_big_keeps_the_newest_whole_and_says_what_was_left_out(
    tmp_path, monkeypatch
):
    app, platform = _desk(tmp_path, monkeypatch)
    platform.router.stream = _Model().stream
    provider = str(platform.config.default_provider or "")
    assert provider, "the test app has no default provider to pin a window on"
    # The window the RUN will be planned against: a quarter of it is the room.
    platform.config.model_context_windows = {provider: 8_192}
    room = 8_192 // 4
    history = _chat(40, words=400)  # ~2,000 characters a row, ~80,000 in all

    r = await _send(app, history)
    assert r.status_code == 202, r.text
    row = await _settled(app)

    assert estimate_tokens(row.task) <= room, (
        f"the task is {estimate_tokens(row.task)} tokens; a quarter of the run's "
        f"8,192 window is {room}"
    )
    kept = _kept_markers(row.task, 40)
    assert kept and kept == list(range(40 - len(kept), 40)), (
        f"the kept rows must be the newest, in order: {kept}"
    )
    for i in kept:
        assert _line(history[i]) in row.task, f"row {i} was kept but not whole"
    said = LEFT_OUT.search(row.task)
    assert said, "rows were left out and the task does not say so"
    assert int(said.group(1)) == 40 - len(kept)
    # how they began: the oldest left-out row's opening words, as data
    assert "- The person: m00 word" in row.task


# --------------------------------------------------------------------------- #
# P3 — nothing dropped silently, past the wire's 80 rows too
# --------------------------------------------------------------------------- #


async def test_every_row_that_rode_along_is_kept_or_counted(tmp_path, monkeypatch):
    app, platform = _desk(tmp_path, monkeypatch)
    platform.router.stream = _Model().stream
    platform.config.model_context_windows = {}
    history = _chat(100)

    r = await _send(app, history)
    assert r.status_code == 202, r.text
    row = await _settled(app)

    kept = _kept_markers(row.task, 100)
    assert kept == list(range(100 - len(kept), 100)) and len(kept) > 20
    said = LEFT_OUT.search(row.task)
    assert said and int(said.group(1)) + len(kept) == 100, (
        "a row that rode along was neither kept nor counted as left out"
    )


# --------------------------------------------------------------------------- #
# P4 — the text itself
# --------------------------------------------------------------------------- #


def test_no_history_is_the_task_alone_and_a_fitting_one_keeps_its_shape():
    bare = td.build_task_text("summarise the notes", None, "laptop")
    assert bare == td.TASK_INTRO.format(caller="laptop") + "\n\nsummarise the notes"
    assert td.build_task_text("summarise the notes", [], "laptop") == bare
    assert td.build_task_text("summarise the notes", [{"role": "user", "content": "  "}], "laptop") == bare

    text = td.build_task_text(
        "summarise the notes",
        [{"role": "user", "content": "we need the Q3 notes"},
         {"role": "assistant", "content": "on it"}],
        "laptop",
        window=200_000,
    )
    assert text == (
        bare
        + "\n\nThe conversation so far on that computer:\n"
        + "The person: we need the Q3 notes\nAssistant: on it"
    )


def test_the_room_is_a_quarter_of_the_runs_window_and_never_more_than_the_cap():
    assert td.task_room(8_192) == 2_048
    assert td.task_room(4_096) == 1_024
    assert td.task_room(None) == td.task_room(0) == td.task_room(-5) == td.TASK_TOKENS_MAX
    assert td.task_room(1_000_000) == td.TASK_TOKENS_MAX


def test_a_newest_row_too_big_for_the_room_alone_is_cut_and_marked():
    row = {"role": "user", "content": "start " + "z" * 11_000}
    text = td.build_task_text("look at this", [row], "laptop", window=4_000)
    assert estimate_tokens(text) <= td.task_room(4_000) + 1
    assert "The person: start zzz" in text
    assert text.endswith(" …"), "a cut row must say it was cut"
    assert not LEFT_OUT.search(text)


@pytest.mark.parametrize("task", ["go on", "read every line of this: " + "detail " * 1_400])
def test_the_task_and_the_left_out_lines_stay_inside_the_room(task):
    # A long task (~10,000 characters) is priced FIRST: the conversation gets
    # what it leaves, so the whole task still fits the room. Two rooms that
    # differ (4,000 and 8,000 tokens: 32,000 and up all give 8,000).
    history = _chat(60, words=200)  # ~1,000 characters a row: past every room
    assert td.task_room(16_000) != td.task_room(32_000)
    for window in (16_000, 32_000):
        text = td.build_task_text(task, history, "laptop", window=window)
        assert estimate_tokens(text) <= td.task_room(window) + 1, window
        # ...and the room is the TASK's: nothing is held back for a reply
        # (the run keeps its own), so a long chat fills it, short of one row
        # (~285 tokens) and one left-out line.
        assert estimate_tokens(text) >= td.task_room(window) - 450, (
            window, estimate_tokens(text))
        assert LEFT_OUT.search(text), window
        assert "The person: m58 " in text or "Assistant: m59 " in text, "the newest went"


# --------------------------------------------------------------------------- #
# P5 — planned off the event loop
# --------------------------------------------------------------------------- #


async def test_the_door_plans_the_task_off_the_event_loop(tmp_path, monkeypatch):
    app, platform = _desk(tmp_path, monkeypatch)
    platform.router.stream = _Model().stream
    where: list[bool] = []
    real = td.planned_task_text

    def recording(*a, **kw):
        where.append(threading.current_thread() is threading.main_thread())
        return real(*a, **kw)

    monkeypatch.setattr(td, "planned_task_text", recording)
    r = await _send(app, _chat(4))
    assert r.status_code == 202, r.text
    await _settled(app)
    assert where == [False], "the conversation was planned on the event loop"


@pytest.mark.parametrize("bad", ["not a list", {"role": "user"}, [1, None, "x", {"content": 5}]])
def test_a_history_of_the_wrong_shape_never_raises(bad):
    text = td.build_task_text("t", bad, "laptop")
    assert text.startswith(td.TASK_INTRO.format(caller="laptop"))


# --------------------------------------------------------------------------- #
# P6 — the count is the sender's, end to end
# --------------------------------------------------------------------------- #

DOOR_URL = f"http://{DOOR_HOST}{TASK_DOOR_PATH}"


class _ToTheDesk(httpx.AsyncBaseTransport):
    """The network between the laptop and the desk: every request the
    laptop's code makes reaches the desk's app, from the laptop's address;
    each body is kept."""

    def __init__(self, app) -> None:
        self.inner = httpx.ASGITransport(app=app, client=(LAPTOP_IP, 50000))
        self.bodies: list[dict] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.bodies.append(json.loads(request.content or b"{}"))
        return await self.inner.handle_async_request(request)


def _wire_to(monkeypatch, net: httpx.AsyncBaseTransport) -> None:
    real_init = httpx.AsyncClient.__init__

    def _init(self, *a, **kw):
        if kw.get("transport") is None:
            kw["transport"] = net
        real_init(self, *a, **kw)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", _init)


async def test_a_chat_longer_than_the_sender_sends_is_counted_whole(tmp_path, monkeypatch):
    # The real sending side: the room's rows as the remote reads them, the
    # registry's own transport (its 80-row tail), then the desk's real door.
    app, platform = _desk(tmp_path, monkeypatch)
    platform.router.stream = _Model().stream
    platform.config.model_context_windows = {}
    net = _ToTheDesk(app)
    _wire_to(monkeypatch, net)
    seat = {"key": "remote:desk", "name": "desk", "role": "participant"}
    entries = [
        {"who": "user" if i % 2 == 0 else "remote:desk", "content": f"m{i:02d} word word word"}
        for i in range(150)
    ]
    rows = AgentThreads.remote_history(entries, seat, [seat])
    assert len(rows) == 150, "the room's rows reach the transport whole; it alone cuts"
    record = SimpleNamespace(
        name="desk", kind="http-task", base_url=DOOR_URL,
        secret_name="desk-key", timeout_s=30, model="",
    )

    out = await RemoteAgentRegistry.run(
        None, record, "carry on from here", lambda name: PAIRING_KEY,
        history=rows, conversation_id="athr_1",
    )
    assert out["ok"] and out.get("accepted"), out
    (sent,) = net.bodies
    assert len(sent["history"]) == 80 and sent["history"] == rows[-80:]
    assert sent[td.HISTORY_HELD_BACK_FIELD] == 70
    row = await _settled(app)

    kept = _kept_markers(row.task, 150)
    assert kept and kept == list(range(150 - len(kept), 150))
    said = LEFT_OUT.search(row.task)
    assert said, "rows were left out and the task does not say so"
    assert int(said.group(1)) == 150 - len(kept), (
        f"the task says {said.group(1)} were left out; {150 - len(kept)} were"
    )
    # The list can only show rows that arrived: it says which ones it shows.
    assert f"How the newest {80 - len(kept)} of them began:" in row.task
    assert "- The person: m70 word" in row.task and "m00 " not in row.task


async def test_rows_the_sender_held_back_count_even_when_every_row_that_came_fits(
    tmp_path, monkeypatch
):
    app, platform = _desk(tmp_path, monkeypatch)
    platform.router.stream = _Model().stream
    transport = httpx.ASGITransport(app=app, client=(LAPTOP_IP, 50000))
    async with httpx.AsyncClient(transport=transport, base_url=f"http://{DOOR_HOST}") as c:
        r = await c.post(
            TASK_DOOR_PATH,
            json={"task": "go on", "history": _chat(10), td.HISTORY_HELD_BACK_FIELD: 70},
            headers={"Authorization": f"Bearer {PAIRING_KEY}"},
        )
    assert r.status_code == 202, r.text
    row = await _settled(app)
    assert _kept_markers(row.task, 10) == list(range(10))
    assert "70 earlier messages were left out to fit." in row.task
    assert "began:" not in row.task, "none of them is here, so no list"


def test_the_door_reads_only_a_whole_count_and_states_it():
    assert td.held_back_of(70) == 70
    for bad in ("70", -3, True, 2.5, None, [5], {"n": 5}):
        assert td.held_back_of(bad) == 0, bad
    assert td.held_back_of(10**12) == td.HISTORY_HELD_BACK_MAX

    # Nothing arrived but a count: the count is still said.
    bare = td.build_task_text("go on", None, "laptop")
    assert td.build_task_text("go on", [], "laptop", held_back=5) == (
        bare + "\n\n" + td.CONVERSATION_HEAD + "\n5 earlier messages were left out to fit."
    )
    # The door leaves rows out too: one count, one list of what is here.
    text = td.build_task_text("go on", _chat(80), "laptop", window=200_000, held_back=70)
    kept = _kept_markers(text, 80)
    assert int(LEFT_OUT.search(text).group(1)) == 70 + 80 - len(kept)
    assert f"How the newest {80 - len(kept)} of them began:" in text
    assert estimate_tokens(text) <= td.task_room(200_000) + 1
    one = td.build_task_text("go on", _chat(61), "laptop", window=200_000, held_back=3)
    assert "4 earlier messages were left out to fit. How the newest of them began:" in one
    # Nothing held back: the words are as before.
    assert "How they began:" in td.build_task_text("go on", _chat(80), "laptop")


def test_only_a_task_door_is_told_and_only_when_rows_were_held_back(monkeypatch):
    bodies: list[dict] = []

    class _Took(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            bodies.append(json.loads(request.content or b"{}"))
            return httpx.Response(202, json={"accepted": True})

    _wire_to(monkeypatch, _Took())
    rows = [{"role": "user", "content": f"r{i}"} for i in range(100)]

    def send(base_url, history, **kw):
        record = SimpleNamespace(
            name="x", kind="http-task", base_url=base_url, secret_name="", timeout_s=5, model=""
        )
        asyncio.run(RemoteAgentRegistry.run(None, record, "t", None, history=history, **kw))
        return bodies[-1]

    other = send("http://10.0.0.9:9000/run", rows, history_earlier=5)
    assert len(other["history"]) == 80 and set(other) == {"task", "history"}, (
        "another kind of remote gets the body it always got"
    )
    assert set(send(DOOR_URL, rows[:80])) == {"task", "history"}, "nothing held back: no field"
    assert send(DOOR_URL, rows[:10], history_earlier=4)[td.HISTORY_HELD_BACK_FIELD] == 4
    assert send(DOOR_URL, rows, history_earlier=5)[td.HISTORY_HELD_BACK_FIELD] == 25


def test_the_chat_page_route_counts_the_rows_it_does_not_pass_on(tmp_path, monkeypatch):
    # The laptop: its chat page sends the whole chat; the route passes on the
    # newest 600 rows, the transport sends the newest 80 of those.
    monkeypatch.delenv("IRONJARVIS_TOKEN", raising=False)
    app = create_app(str(tmp_path / "laptop"))
    RemoteAgentRegistry(app.state.platform.engine).upsert("desk", DOOR_URL, "http-task")
    bodies: list[dict] = []

    class _Took(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            bodies.append(json.loads(request.content or b"{}"))
            return httpx.Response(202, json={"accepted": True})

    _wire_to(monkeypatch, _Took())
    history = [
        {"who": "user" if i % 2 == 0 else "jarvis", "content": f"c{i:03d} words"}
        for i in range(700)
    ]
    history.insert(5, {"who": "user", "content": "   "})  # no words: not a message
    r = TestClient(app).post(
        "/chat/panel",
        json={"message": "@desk carry on", "chat_thread_id": "c1", "history": history},
    )
    assert r.status_code == 200, r.text
    assert r.json()["spoke"] == ["remote:desk"]
    (sent,) = bodies
    assert len(sent["history"]) == 80
    assert sent["history"][-1]["content"].endswith("c699 words")
    assert sent[td.HISTORY_HELD_BACK_FIELD] == 700 - 80


def _phone(app):
    platform = app.state.platform
    store = CommThreadStore(platform.engine, event_bus=platform.event_bus)

    async def _turn(platform_, personas, body):
        return {"reply": "Jarvis here.", "escalate": False}

    poller = InboundPoller(
        platform.notifier,
        SimpleNamespace(),
        platform.engine,
        event_bus=platform.event_bus,
        thread_store=store,
        chat_turn=_turn,
        personas={},
        platform=platform,
    )
    return poller, store


class _Phone:
    reflex_source = "comm"
    inbound_enabled = True
    chat_enabled = True

    def __init__(self) -> None:
        self.sent: list[str] = []

    def is_authorized(self, sender_id):
        return True

    def send(self, message, **kw):
        self.sent.append(message)
        return {"ok": True}


def _said(text: str):
    return SimpleNamespace(sender_id="u1", reply_to="u1", text=text, is_bot=False, sender_name="Val")


def test_a_phone_chat_counts_the_rows_it_does_not_pass_on(tmp_path, monkeypatch):
    # The phone's lane shows a remote the newest 60 lines of the phone chat.
    monkeypatch.delenv("IRONJARVIS_TOKEN", raising=False)
    app = create_app(str(tmp_path / "laptop"))
    RemoteAgentRegistry(app.state.platform.engine).upsert("desk", DOOR_URL, "http-task")
    bodies: list[dict] = []

    class _Answered(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            bodies.append(json.loads(request.content or b"{}"))
            return httpx.Response(200, json={"result": "noted"})

    _wire_to(monkeypatch, _Answered())
    poller, store = _phone(app)
    ch = _Phone()
    out = asyncio.run(poller._handle_chat("telegram", ch, _said("@desk hello"), "@desk hello", "Val"))
    assert out["status"] == "remote_chat", out
    tid = out["thread_id"]
    assert td.HISTORY_HELD_BACK_FIELD not in bodies[-1]
    for i in range(98):
        store.append(tid, "user" if i % 2 == 0 else "assistant", f"p{i:02d} words")
    before = len(store.history_rows(tid, limit=10_000))
    assert before > 60

    out = asyncio.run(poller._handle_chat("telegram", ch, _said("and the rest?"), "and the rest?", "Val"))
    assert out["status"] == "remote_chat", out
    sent = bodies[-1]
    assert len(sent["history"]) == 59, "the newest 60 lines, the message itself not among them"
    assert sent[td.HISTORY_HELD_BACK_FIELD] == before - 59
    assert len(sent["history"]) + sent[td.HISTORY_HELD_BACK_FIELD] == before


def test_a_project_rooms_remote_is_told_only_of_rows_it_may_see():
    earlier = {"user": 3, "jarvis": 4, "remote:desk": 2, "builtin:builder": 1}
    assert AgentThreads._earlier_for(earlier, None) == 10
    assert AgentThreads._earlier_for(earlier, ("user", "remote:desk")) == 5
    assert AgentThreads._earlier_for(None, None) == 0
    assert AgentThreads._earlier_for({"user": "3", "jarvis": -1, "x": True}, None) == 0


async def test_a_round_tells_the_remote_seat_what_it_may_see_of_the_earlier_rows(
    tmp_path, monkeypatch
):
    # Through ``run_round``: a project room's remote sees only the user's
    # lines and its own (v1.304.0), so only those earlier rows are counted;
    # a general room's remote is told of them all.
    from iron_jarvis.agents.threads import clean_participants

    monkeypatch.delenv("IRONJARVIS_TOKEN", raising=False)
    app = create_app(str(tmp_path / "laptop"))
    platform = app.state.platform
    RemoteAgentRegistry(platform.engine).upsert("desk", DOOR_URL, "http-task")
    made = TestClient(app).post("/projects", json={"name": "Acme"})
    assert made.status_code == 200, made.text
    seen: list[dict] = []

    async def fake_run(self, record, task, secret_get, **kw):
        seen.append(kw)
        return {"ok": True, "result": "noted"}

    monkeypatch.setattr(RemoteAgentRegistry, "run", fake_run)
    threads = AgentThreads(platform.engine)
    seat = clean_participants([{"source": "remote", "name": "desk", "role": "participant"}])
    earlier = {"user": 3, "jarvis": 4, "remote:desk": 2}
    shim = SimpleNamespace(platform=platform)
    for project_id, told in ((made.json()["id"], 5), ("", 9)):
        room = threads.create("room", seat, project_id=project_id)
        await threads.run_round(
            room.id, "@desk go on", shim, directed=["desk"],
            chat_history=[{"who": "user", "content": "hello"}], chat_earlier=earlier,
        )
        assert seen and seen[-1].get("history_earlier") == told, (project_id, seen[-1])


# --------------------------------------------------------------------------- #
# P7 — priced at the run's measured ratio
# --------------------------------------------------------------------------- #


async def test_the_door_prices_the_task_at_the_runs_measured_ratio(tmp_path, monkeypatch):
    app, platform = _desk(tmp_path, monkeypatch)
    platform.router.stream = _Model().stream
    provider = str(platform.config.default_provider or "")
    platform.config.model_context_windows = {provider: 32_000}
    room = td.task_room(32_000)
    real = platform.providers.capability_profile

    def measured(p, m):
        prof = real(p, m).copy()
        prof.chars_per_token = 2.0  # a model that reads 2 characters a token
        prof.measured_fields = [*prof.measured_fields, "chars_per_token"]
        return prof

    monkeypatch.setattr(platform.providers, "capability_profile", measured)
    r = await _send(app, _chat(60, words=200))
    assert r.status_code == 202, r.text
    row = await _settled(app)

    assert LEFT_OUT.search(row.task), "the chat must be past the room for this to test it"
    assert estimate_tokens(row.task, 2.0) <= room + 1, (
        f"at the run's own 2 characters a token the task is "
        f"{estimate_tokens(row.task, 2.0)} tokens; its room is {room}"
    )
    assert estimate_tokens(row.task, 2.0) >= room - RECAP_RESERVE - 600, "the room went unused"


def test_the_speaker_labels_are_priced_at_a_low_measured_ratio():
    # The planner charges a row 4 tokens past its words; "The person: " and
    # its line break are 13 characters: 13 tokens at 1 character a token, on
    # every one of up to 60 lines. 70 rows of 120 characters fill the room at
    # about 60 rows, so an unpriced label would carry the task past it.
    rows = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i:02d} " + "x" * 116}
        for i in range(70)
    ]
    for ratio in (1.0, 2.0):
        text = td.build_task_text("go on", rows, "laptop", window=32_000, chars_per_token=ratio)
        assert LEFT_OUT.search(text), ratio
        assert estimate_tokens(text, ratio) <= td.task_room(32_000) + 1, (
            ratio, estimate_tokens(text, ratio))


@pytest.mark.parametrize("ratio", [None, 1.0, 2.0])
def test_the_task_fits_its_room_whatever_the_chat_looks_like(ratio):
    # Many shapes at once: few long rows, many short ones, rows the sender
    # held back or not. Every one fits the room at the run's own ratio.
    room = td.task_room(8_192)
    over = []
    for n in (1, 2, 3, 5, 12, 61):
        for size in range(10, 2_400, 37):
            rows = [
                {"role": "user" if i % 2 else "assistant", "content": f"m{i:02d} " + "y" * size}
                for i in range(n)
            ]
            for held in (0, 70):
                text = td.build_task_text(
                    "go on", rows, "laptop", window=8_192, chars_per_token=ratio, held_back=held
                )
                if estimate_tokens(text, ratio) > room + 1:
                    over.append((n, size, held, estimate_tokens(text, ratio)))
    assert not over, f"past the {room}-token room: {over[:5]}"
