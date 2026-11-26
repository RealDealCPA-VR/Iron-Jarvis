"""The intelligence plan, release 6: a chat reply's own record says what the
reply USED, on BOTH chat lanes.

Since v1.572.0 a reply's usage row (``AgentRun``, ``session_id="chat"``) keeps
the provider's cache counts and the NAMES of the tools sent. It now also
keeps, as numbers and names only:

* ``parts_json``: the size of each part of what the reply was sent when it
  began (your messages, attached files, what was remembered, the folder,
  instructions, a skill), the room the model has, and where the tool
  definitions came from. Every size is an estimate; a part that held nothing
  is absent, never 0;
* ``model_ms`` / ``model_calls``: how long the model took, from the router's
  own clock, and how many calls answered;
* ``first_word_ms``: from the reply's start to its first shown word (the
  stream lane only; None on the POST lane, which shows no word before the end).

Never the text of any part. Through the real app, the real lanes, the real
router and the real end-row write; only the model is a stand-in, and time is
an injected clock (nothing here sleeps).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from iron_jarvis.context import breakdown as B
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import AgentRun, EventRecord
from iron_jarvis.daemon import chat_turn
from iron_jarvis.daemon.app import create_app
from iron_jarvis.eval import reply_facts
from iron_jarvis.eval.pricing import UsageTally
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from iron_jarvis.providers.adapters.mock import MockLLMAdapter

LANES = ("/chat", "/chat/stream")
CACHED = {"input_tokens": 900, "output_tokens": 20,
          "cache_read_input_tokens": 600, "cache_creation_input_tokens": 100}
SRC = Path(chat_turn.__file__).resolve().parent
#: One model call takes this long on the injected clock.
CALL_S = 1.5

MARK_MESSAGE = "ZQXMESSAGEMARK"
MARK_FILE = "ZQXFILEMARK"
MARK_MEMORY = "ZQXMEMORYMARK"
MARK_ARGUMENT = "ZQXARGUMENTMARK"
MARKS = (MARK_MESSAGE, MARK_FILE, MARK_MEMORY, MARK_ARGUMENT)


class Clock:
    """The injected clock: it moves only when a stand-in model call says so."""

    def __init__(self) -> None:
        self.t = 5_000.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture()
def clock(monkeypatch) -> Clock:
    c = Clock()
    monkeypatch.setattr(reply_facts, "clock", c)
    return c


def _app(tmp_path, clock: Clock):
    app = create_app(str(tmp_path / "home"))
    app.state.platform.router._clock = clock
    return app


def _stand_in(platform, clock: Clock, replies: "list[LLMResponse] | None" = None) -> list:
    """The model: each call takes ``CALL_S`` on the clock and answers the next
    scripted reply (the last one again when the script runs out). Returns the
    list that fills with what each call was really sent."""
    seen: list = []
    script = list(replies or [LLMResponse(text="Here is the report.", usage=dict(CACHED))])

    class _Answer(MockLLMAdapter):
        async def complete(self, *, system, messages, tools, **kw):
            seen.append({
                "system": system,
                "messages": [(m.role, m.content) for m in messages],
                "calls": [c.arguments for m in messages for c in (getattr(m, "tool_calls", None) or [])],
                "tools": json.dumps(list(tools or []), sort_keys=True, default=str),
            })
            clock.t += CALL_S
            return script[min(len(seen), len(script)) - 1]

    platform.providers.register("mock", lambda model=None: _Answer())
    return seen


def _rows(engine, thread: str) -> list[AgentRun]:
    with session_scope(engine) as db:
        return [r for r in db.exec(select(AgentRun)).all()
                if r.session_id == "chat" and r.thread_id == thread]


def _row(engine, thread: str) -> AgentRun:
    rows = [r for r in _rows(engine, thread) if r.parts_json is not None or r.model_ms is not None]
    assert len(rows) == 1, _rows(engine, thread)
    return rows[0]


def _ask(app, lane: str, thread: str, text: str = "write the report", **more) -> dict:
    body = {"messages": [{"role": "user", "content": text}],
            "thread_id": thread, "tools": ["read_file", "list_files"], **more}
    r = TestClient(app).post(lane, json=body)
    assert r.status_code == 200, r.text
    return {"text": r.text}


# --------------------------------------------------------------------------- #
# Both lanes, through the real end row
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("lane", LANES)
def test_the_row_saves_the_size_of_each_part_and_where_the_tools_came_from(tmp_path, clock, lane):
    app = _app(tmp_path, clock)
    seen = _stand_in(app.state.platform, clock)
    _ask(app, lane, "t-parts")
    row = _row(app.state.platform.engine, "t-parts")

    record = json.loads(row.parts_json)
    assert record["v"] == B.RECORD_VERSION
    parts = record["parts"]
    # Your message and Iron Jarvis's own instructions are always there...
    assert parts["messages"] > 0 and parts["system"] > 0
    # ...and they are sizes of what the model was really sent: the instructions
    # part is never larger than the whole prompt the stand-in received.
    assert len(seen) == 1
    assert parts["system"] <= B.estimate_tokens(seen[0]["system"])
    assert record["used"] == sum(parts.values())
    assert record["window"] > 0 and 0 <= record["percent"] <= 100
    # A part that held nothing is ABSENT (never a made-up 0).
    assert "attachments" not in parts and "summary" not in parts and "skills" not in parts
    assert all(isinstance(v, int) and v > 0 for v in parts.values()), parts

    # The tool definitions: Iron Jarvis's own, counted and sized; no extension.
    sent = json.loads(seen[0]["tools"])
    count, size = record["tools"]["builtin"]
    assert count == len(sent) >= 2 and size > 0
    assert record["tools"]["extensions"] == {}
    # The input the provider's cache counts are parts of (every call reported one).
    assert record["cache_input"] == row.input_tokens == 900


@pytest.mark.parametrize("lane", LANES)
def test_the_row_saves_how_long_the_model_took_from_the_injected_clock(tmp_path, clock, lane):
    app = _app(tmp_path, clock)
    seen = _stand_in(app.state.platform, clock)
    _ask(app, lane, "t-time")
    row = _row(app.state.platform.engine, "t-time")
    assert len(seen) == 1
    assert (row.model_calls, row.model_ms) == (1, int(CALL_S * 1000))


def test_only_the_stream_lane_knows_the_first_word(tmp_path, clock):
    app = _app(tmp_path, clock)
    _stand_in(app.state.platform, clock)
    _ask(app, "/chat", "t-post")
    _ask(app, "/chat/stream", "t-stream")
    post, stream = _row(app.state.platform.engine, "t-post"), _row(app.state.platform.engine, "t-stream")
    # The POST lane shows no word before its end: not known, never 0.
    assert post.first_word_ms is None
    # The stream lane's first word left after the one model call the clock moved for.
    assert stream.first_word_ms == int(CALL_S * 1000)


def test_the_two_lanes_save_the_same_parts_for_the_same_reply(tmp_path, clock):
    app = _app(tmp_path, clock)
    _stand_in(app.state.platform, clock)
    TestClient(app).put("/profile", json={"values": {"about": "I keep the books for a bakery."}})
    for lane, thread in (("/chat", "t-a"), ("/chat/stream", "t-b")):
        _ask(app, lane, thread)
    a, b = (json.loads(_row(app.state.platform.engine, t).parts_json) for t in ("t-a", "t-b"))
    assert a == b
    assert a["parts"]["memory"] > 0, "the profile is what Iron Jarvis remembered"
    ra, rb = _row(app.state.platform.engine, "t-a"), _row(app.state.platform.engine, "t-b")
    assert (ra.model_ms, ra.model_calls, ra.tools_sent_json) == (rb.model_ms, rb.model_calls, rb.tools_sent_json)


@pytest.mark.parametrize("lane", LANES)
def test_an_attached_file_is_its_own_part(tmp_path, clock, lane):
    app = _app(tmp_path, clock)
    _stand_in(app.state.platform, clock)
    uploads = Path(app.state.platform.config.home) / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    (uploads / "notes.txt").write_text("quarterly figures\n" * 200, encoding="utf-8")
    _ask(app, lane, "t-file", attachments=["notes.txt"])
    parts = json.loads(_row(app.state.platform.engine, "t-file").parts_json)["parts"]
    assert parts["attachments"] > 200, parts


@pytest.mark.parametrize("lane", LANES)
def test_messages_left_out_to_fit_are_counted_and_the_size_is_what_the_chat_holds(tmp_path, clock, lane):
    """A long chat on a model with little room: the planner sends the newest
    messages only. The record keeps how many the planner left out (the very
    count the reply's fullness report carries), the size of ALL the chat's
    messages (what the meter shows), and the meter's own percent."""
    app = _app(tmp_path, clock)
    platform = app.state.platform
    platform.config.model_context_windows = {"mock": 6_000}
    platform.config.context_compaction = {"enabled": False}     # the user's switch: no summary call
    seen = _stand_in(platform, clock)
    history = []
    for i in range(30):
        history.append({"role": "user", "content": f"question {i} " + "about the ledger " * 40})
        history.append({"role": "assistant", "content": f"answer {i} " + "it adds up " * 40})
    history.append({"role": "user", "content": "and the total?"})
    r = TestClient(app).post(lane, json={"messages": history, "thread_id": "t-long", "tools": []})
    assert r.status_code == 200, r.text
    if lane == "/chat":
        report = r.json()["context"]
    else:
        done = [b for b in r.text.split(chr(10) + chr(10)) if b.startswith("event: done")]
        report = json.loads(done[-1].split("data: ", 1)[1])["context"]

    record = json.loads(_row(platform.engine, "t-long").parts_json)
    assert record["window"] == 6_000 and record["window_known"] is True
    sent = seen[0]["messages"]
    assert len(sent) < len(history), "earlier messages were left out"
    # The planner's own count, the one the reply's fullness report carries...
    assert record["left_out"] == report["dropped"] > 0
    # ...which is a floor: the fit made for the model call may leave out more.
    assert record["left_out"] <= len(history) - len(sent)
    assert record["percent"] == report["percent"]
    # The size is the whole chat's, so it is more than the room: the meter says over 100%.
    assert record["parts"]["messages"] > 6_000 and record["percent"] > 100
    assert record["used"] == sum(record["parts"].values())


async def test_a_coding_sessions_reply_saves_the_very_parts_its_own_list_shows(tmp_path):
    """A coding session already SHOWS what fills it (v1.415.0). Its reply's
    row now saves the same parts, counted by the same counter from the same
    strings, and is marked as a coding reply so it is read apart from plain
    chat."""
    from tests.test_build_sessions_v1367 import CLAUDE, Script, frames_of, new_session, open_turn, wire

    app = create_app(str(tmp_path / "home"))
    app.state.platform.config.model_context_windows = {f"{CLAUDE[0]}::{CLAUDE[1]}": 200_000}
    folder = tmp_path / "work"
    folder.mkdir()
    (folder / "app.py").write_text("x = 1" + chr(10), encoding="utf-8")
    (folder / "AGENTS.md").write_text("# House rules" + chr(10) + ("Keep functions short." + chr(10)) * 40,
                                      encoding="utf-8")
    sid = new_session(app, folder)["id"]
    wire(app, Script(["Done."]))
    status, raw = await open_turn(app, sid, {"text": "add a docstring", "turn_id": "bt-1"})
    assert status == 200, raw
    shown = dict(frames_of(raw))["done"]["context"]
    with session_scope(app.state.platform.engine) as db:
        rows = [r for r in db.exec(select(AgentRun)).all() if r.session_id == "chat" and r.parts_json]
    assert len(rows) == 1, rows
    record = json.loads(rows[0].parts_json)
    assert record["kind"] == "code"
    assert record["parts"] == {p["key"]: p["tokens"] for p in shown["breakdown"]["parts"] if p["kind"] == "used"}
    assert record["parts"]["rules"] > 0, "the folder's instruction file is its own part"
    assert (record["used"], record["window"], record["percent"]) == (
        shown["breakdown"]["used"], shown["window"], shown["percent"])
    # The list the coding session shows is what it was: this release only reads it.
    assert shown["breakdown"]["source"] == "app" and "kind" not in shown["breakdown"]


# --------------------------------------------------------------------------- #
# Sizes and names only: never the text
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("lane", LANES)
def test_no_text_of_any_part_is_saved_on_the_row(tmp_path, clock, lane):
    app = _app(tmp_path, clock)
    platform = app.state.platform
    TestClient(app).put("/profile", json={"values": {"about": f"My codeword is {MARK_MEMORY}."}})
    uploads = Path(platform.config.home) / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    (uploads / "notes.txt").write_text(f"the figure is {MARK_FILE}\n" * 20, encoding="utf-8")
    seen = _stand_in(platform, clock, [
        LLMResponse(text="", finish_reason="tool_use", usage=dict(CACHED),
                    tool_calls=[ToolCall(id="c1", name="list_files", arguments={"path": MARK_ARGUMENT})]),
        LLMResponse(text="Done.", usage=dict(CACHED)),
    ])
    _ask(app, lane, "t-marks", text=f"please file {MARK_MESSAGE}", attachments=["notes.txt"])

    # The marks really were sent to the model, and the tool call really ran...
    assert len(seen) == 2
    first = seen[0]["system"] + json.dumps(seen[0]["messages"])
    assert all(m in first for m in (MARK_MESSAGE, MARK_FILE, MARK_MEMORY))
    assert seen[1]["calls"] == [{"path": MARK_ARGUMENT}] and ("tool", "no such directory") in seen[1]["messages"]
    # ...and not one of them is in any column of any usage row of this chat.
    rows = _rows(platform.engine, "t-marks")
    assert rows
    for row in rows:
        blob = json.dumps(row.model_dump(mode="json"), default=str)
        for mark in MARKS:
            assert mark not in blob, (mark, row.id)
    record = json.loads(_row(platform.engine, "t-marks").parts_json)
    assert record["parts"]["attachments"] > 0 and record["parts"]["memory"] > 0
    # Only numbers, the fixed part names and tool origins.
    assert set(record) <= {"v", "parts", "used", "window", "window_known", "percent", "left_out", "tools",
                           "cache_input"}
    assert set(record["parts"]) <= set(B.RECORD_PARTS)
    assert _row(platform.engine, "t-marks").model_calls == 2


def test_the_reply_writes_no_new_event(tmp_path, clock):
    """Nothing here publishes: a chat reply's events are what they were."""
    app = _app(tmp_path, clock)
    _stand_in(app.state.platform, clock)
    _ask(app, "/chat/stream", "t-events", text=f"hello {MARK_MESSAGE}")
    with session_scope(app.state.platform.engine) as db:
        events = db.exec(select(EventRecord)).all()
    for e in events:
        assert "parts_json" not in (e.payload_json or "") and "model_ms" not in (e.payload_json or ""), e.type


# --------------------------------------------------------------------------- #
# It only looks: what the model is sent is unchanged
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("lane", LANES)
def test_what_the_model_is_sent_is_the_same_with_and_without_the_record(tmp_path, clock, lane, monkeypatch):
    def run(home: str, record: bool) -> dict:
        with monkeypatch.context() as m:
            if not record:
                m.setattr(chat_turn, "note_reply_parts", lambda *a, **k: None)
                m.setattr(reply_facts, "note_model_time", lambda *a, **k: None)
                m.setattr(B, "tool_origins", lambda *a, **k: None)
            app = create_app(str(tmp_path / home))
            app.state.platform.router._clock = clock
            TestClient(app).put("/profile", json={"values": {"about": "I keep the books for a bakery."}})
            seen = _stand_in(app.state.platform, clock)
            _ask(app, lane, "t-same")
            assert len(seen) == 1
            row = _rows(app.state.platform.engine, "t-same")[0]
            assert (row.parts_json is not None) is record
            return seen[0]

    with_record, without = run("a", True), run("b", False)
    assert with_record["messages"] == without["messages"]
    assert with_record["tools"] == without["tools"]
    # The two homes differ only in their own folder's path.
    a = with_record["system"].replace(str(tmp_path / "a"), "<home>")
    b = without["system"].replace(str(tmp_path / "b"), "<home>")
    assert a == b


@pytest.mark.parametrize("lane", LANES)
def test_the_parts_are_measured_off_the_event_loop(tmp_path, clock, lane, monkeypatch):
    """The measure estimates the whole prompt: it runs on a worker thread,
    where no event loop is running, on both lanes."""
    where: list[str] = []
    real = chat_turn.note_reply_parts

    def spy(*a, **k):
        try:
            asyncio.get_running_loop()
            where.append("on the event loop")
        except RuntimeError:
            where.append("on a worker thread")
        return real(*a, **k)

    monkeypatch.setattr(chat_turn, "note_reply_parts", spy)
    app = _app(tmp_path, clock)
    _stand_in(app.state.platform, clock)
    _ask(app, lane, "t-loop")
    assert where == ["on a worker thread"]
    assert _row(app.state.platform.engine, "t-loop").parts_json is not None


# --------------------------------------------------------------------------- #
# A record never costs the reply
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("lane", LANES)
def test_a_measure_that_breaks_does_not_fail_the_reply_or_the_other_columns(tmp_path, clock, lane, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("the measure broke")

    monkeypatch.setattr(B, "sent_record", boom)
    monkeypatch.setattr(B, "tool_origins", boom)
    app = _app(tmp_path, clock)
    _stand_in(app.state.platform, clock)
    out = _ask(app, lane, "t-boom")
    assert "Here is the report." in out["text"]
    rows = _rows(app.state.platform.engine, "t-boom")
    assert len(rows) == 1
    # Not measured is None, and the model's time is still there.
    assert rows[0].parts_json is None
    assert rows[0].model_ms == int(CALL_S * 1000)


@pytest.mark.parametrize("lane", LANES)
def test_a_row_that_cannot_be_written_does_not_fail_the_reply(tmp_path, clock, lane, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("the database is away")

    monkeypatch.setattr(chat_turn, "_write_chat_usage", boom)
    app = _app(tmp_path, clock)
    _stand_in(app.state.platform, clock)
    out = _ask(app, lane, "t-nowrite")
    assert "Here is the report." in out["text"]
    assert _rows(app.state.platform.engine, "t-nowrite") == []


def test_used_fields_fault_keeps_the_v1572_columns():
    class Bad:
        parts = {"v": 1, "parts": {"messages": object()}}  # not JSON

        def fields(self):
            raise RuntimeError("no")

    t = UsageTally()
    t.add("mock", "m", dict(CACHED))
    t.note_tools([{"name": "read_file"}], 30)
    t.facts = Bad()
    out = chat_turn._sent_fields(t)
    assert out["cache_read_input_tokens"] == 600 and json.loads(out["tools_sent_json"]) == ["read_file"]
    assert "parts_json" not in out and "model_ms" not in out


# --------------------------------------------------------------------------- #
# Not measured is absent
# --------------------------------------------------------------------------- #
def test_a_reply_whose_calls_were_never_timed_has_no_time():
    facts = reply_facts.ReplyFacts()
    assert facts.fields() == {}
    t = UsageTally()
    t.facts = facts
    assert chat_turn._used_fields(t) == {}
    assert chat_turn._used_fields(UsageTally()) == {}
    assert chat_turn._used_fields(None) == {}


def test_a_router_double_that_times_nothing_leaves_the_time_not_known(tmp_path, clock):
    """A lane whose model calls did not pass the router's timing seam saves
    no time: None on the row, never 0."""
    app = _app(tmp_path, clock)
    _stand_in(app.state.platform, clock)
    import iron_jarvis.providers.router as router_mod

    orig = router_mod._note_time
    router_mod._note_time = lambda seconds: None
    try:
        _ask(app, "/chat", "t-untimed")
    finally:
        router_mod._note_time = orig
    row = _row(app.state.platform.engine, "t-untimed")
    assert row.model_ms is None and row.model_calls is None
    assert row.parts_json is not None


def test_the_record_says_how_many_earlier_messages_were_left_out():
    rec = B.sent_record(system="s" * 40, messages=[{"role": "user", "content": "hi"}], window=1000,
                        pieces={}, left_out=3)
    assert rec["left_out"] == 3
    assert "left_out" not in B.sent_record(system="s" * 40, messages=[{"role": "user", "content": "hi"}],
                                           window=1000, pieces={}, left_out=0)
    # No room known: the sizes are kept, and no share is claimed.
    blind = B.sent_record(system="s" * 40, messages=[{"role": "user", "content": "hi"}], window=0, pieces={})
    assert "window" not in blind and "percent" not in blind and blind["parts"]["messages"] > 0


def test_tool_origins_names_extensions_and_never_a_definition():
    specs = [
        {"name": "read_file", "description": "SECRETWORDS " * 30},
        {"name": "mcp__ledger__post_entry", "description": "SECRETWORDS " * 90},
        {"name": "mcp__ledger__list_accounts", "description": "x"},
        {"type": "function", "function": {"name": "mcp__mail__send", "description": "y"}},
    ]
    got = B.tool_origins(specs)
    assert got["builtin"][0] == 1 and got["builtin"][1] > 0
    assert {k: v[0] for k, v in got["extensions"].items()} == {"ledger": 2, "mail": 1}
    assert got["extensions"]["ledger"][1] > got["builtin"][1]
    assert "SECRETWORDS" not in json.dumps(got)
    assert B.tool_origins([]) is None and B.tool_origins(None) is None


# --------------------------------------------------------------------------- #
# The router's one timing seam: a completion, and a stream's final frame
# --------------------------------------------------------------------------- #
async def test_the_router_times_a_completion_and_a_streams_final_frame_on_both_paths(clock):
    from iron_jarvis.core.events import EventBus
    from iron_jarvis.providers.manager import ProviderManager
    from iron_jarvis.providers.router import ModelRouter

    class _Answer(MockLLMAdapter):
        async def complete(self, *, system, messages, tools, **kw):
            clock.t += CALL_S
            return LLMResponse(text="ok", usage=dict(CACHED))

    manager = ProviderManager()
    manager.register("mock", lambda model=None: _Answer())
    router = ModelRouter(manager, "mock", EventBus())
    router._clock = clock
    adapter = manager.get("mock")

    facts = reply_facts.begin()
    await router._timed_complete(adapter, system="s", messages=[], tools=[])
    assert facts.fields() == {"model_calls": 1, "model_ms": 1500}
    # The first try of a stream (it may be tried again), then a stream that
    # took over after a failure: each is timed once, at its final frame.
    for n, retry in ((2, True), (3, False)):
        frames = [f async for f in router._stream_one(
            adapter, system="s", messages=[], tools=[], deadline=clock.t + 60, retry=retry)]
        assert frames[-1]["type"] == "final"
        assert facts.fields() == {"model_calls": n, "model_ms": n * 1500}
    # No reply is running: the seam is a no-op, and never raises.
    reply_facts._FACTS.set(None)
    await router._timed_complete(adapter, system="s", messages=[], tools=[])
    assert facts.fields()["model_calls"] == 3


# --------------------------------------------------------------------------- #
# The lanes stay in step (source pins)
# --------------------------------------------------------------------------- #
def test_both_lanes_begin_the_facts_measure_the_parts_and_hand_them_to_the_row():
    post = (SRC / "chat_turn.py").read_text(encoding="utf-8").replace("\r\n", "\n")
    stream = (SRC / "routes" / "chat.py").read_text(encoding="utf-8").replace("\r\n", "\n")
    for src in (post, stream):
        assert src.count("_facts = _reply_facts.begin()") == 1
        assert src.count("_tally.facts = _facts  # release 6: the row saves them (lock-step)") == 1
    assert post.count("note_reply_parts, d, _facts, context_report,") == 1
    assert stream.count("_chat_turn.note_reply_parts, d, _facts, context_report,") == 1
    # The measure runs off the event loop on both lanes.
    for src, call in ((post, "note_reply_parts, d, _facts"), (stream, "_chat_turn.note_reply_parts, d, _facts")):
        at = src.index(call)
        assert src[at - 60:at].rstrip().endswith("await asyncio.to_thread(")
    # The facts begin before the lane's first possible model call (the
    # routing call started with the preparation).
    assert post.index("_facts = _reply_facts.begin()") < post.index("_route = start_route_first(")
    assert stream.index("_facts = _reply_facts.begin()") < stream.index("start_route_first(")
    assert "_facts.first_word()" in stream and "_facts.first_word()" not in post
