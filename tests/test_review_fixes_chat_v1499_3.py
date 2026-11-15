"""v1.499.3 — the chat review's fixes, one section per finding.

1. A steer note is never dropped when a stream turn FAILS: the error frame
   hands unread notes back like the done frame does.
2. A long chat's oldest messages are KEPT (moved to the chat's older
   messages), never deleted, and history search still finds them.
3. Every completion of a chat turn prices the tool definitions and the whole
   system prompt, round 0 included.
4. The compaction cache and the attachment checks run off the event loop.
5. The feature lookup's routing answer is read through ``core.jsonish``.
6. ``jsonish`` keeps looking past a balanced slice that is not JSON, and stays
   linear on hostile text.
7. Two sends with the same turn id start ONE turn, even when both arrive
   before the first has started.

No real provider is called anywhere here.
"""

from __future__ import annotations

import time

from iron_jarvis.capability_search import route as RT
from iron_jarvis.core import jsonish


# --------------------------------------------------------------------------- #
# 6. jsonish: past the first balanced slice, and linear
# --------------------------------------------------------------------------- #
def test_an_object_after_a_placeholder_in_braces_is_read():
    assert jsonish.loads_object('Set the {name} field: {"name": "Q3"}') == {"name": "Q3"}
    assert jsonish.loads_object(
        '<think>maybe {x}</think>\n{"tool": "read_file", "args": {"path": "a.txt"}}'
    ) == {"tool": "read_file", "args": {"path": "a.txt"}}
    # A placeholder in brackets before the real list.
    assert jsonish.loads_lenient('Picked [1 entry]: ["s1"]', want=list) == ["s1"]
    # The first balanced object still wins when it parses.
    assert jsonish.loads_object('Here: {"a": 1} and {"b": 2}') == {"a": 1}
    # Nothing is invented.
    assert jsonish.loads_object("Set the {name} field to {value}") is None


def test_a_deep_nest_is_no_value_and_never_an_exception():
    assert jsonish.loads_lenient("[" * 5000 + "]" * 5000) is None


def test_hostile_text_is_tried_a_bounded_number_of_times(monkeypatch):
    """However many braces a reply holds, at most MAX_BALANCED slices are
    parsed (twice each: as-is and repaired), plus the whole text and fences."""
    calls: list[int] = []
    real = jsonish._try

    def counting(text):
        calls.append(len(text))
        return real(text)

    monkeypatch.setattr(jsonish, "_try", counting)
    hostile = "{x} " * 20_000
    assert jsonish.loads_object(hostile) is None
    assert len(calls) <= 2 * (1 + jsonish.MAX_BALANCED)
    calls.clear()
    assert jsonish.loads_object("{" * 50_000) is None
    assert len(calls) <= 2


def test_the_scan_is_linear_in_the_text():
    """One pass per bracket kind: 8x the text costs about 8x the time, never
    64x. Generous bounds — this guards the shape, not the machine."""

    def cost(n: int) -> float:
        text = ("{a} [b] " * n) + "{" * n
        t0 = time.perf_counter()
        for _ in range(3):
            jsonish.loads_lenient(text)
        return time.perf_counter() - t0

    small, big = cost(2_000), cost(16_000)
    assert big < 2.0, big
    assert big < max(small, 0.002) * 30, (small, big)


# --------------------------------------------------------------------------- #
# 5. The routing answer goes through the one recovery ladder
# --------------------------------------------------------------------------- #
def test_near_json_routing_answers_are_read_not_taken_for_nothing():
    known = {"s1", "t2"}
    assert RT.parse_ids('["s1", "t2",]', known) == ["s1", "t2"]
    assert RT.parse_ids("['s1', 't2']", known) == ["s1", "t2"]
    assert RT.parse_ids('Picked [1 entry]: ["s1"]', known) == ["s1"]
    assert RT.parse_ids('```json\n["s1"]\n```', known) == ["s1"]
    assert RT.said_unasked('["unasked", "s1",]') is True
    assert RT.said_unasked("['unasked']") is True
    assert RT.answered_nothing("Answer: [ ],") is True
    # Still not an answer: a nested list, prose, a broken list.
    assert RT.parse_ids('["s1", ["t2"]]', known) == []
    assert RT.answered_nothing("[oops]") is False
    assert RT.parse_ids("[" * 100_000, known) == []


def test_the_route_module_has_no_parser_of_its_own():
    import inspect

    src = inspect.getsource(RT)
    assert "json.loads" not in src
    assert "loads_lenient" in src


# --------------------------------------------------------------------------- #
# 7. One turn id, one turn — even when both sends arrive before it started
# --------------------------------------------------------------------------- #
import asyncio  # noqa: E402

import pytest  # noqa: E402


@pytest.mark.asyncio
async def test_two_sends_of_one_turn_id_before_it_starts_start_it_once(tmp_path, monkeypatch):
    from iron_jarvis.daemon.app import create_app
    from iron_jarvis.daemon.routes import chat as chat_routes
    from tests.test_durable_chat_turns_v1336 import _open

    app = create_app(str(tmp_path))
    gate = asyncio.Event()
    calls: list[str] = []

    async def slow_start(platform, personas, body, **kw):
        # The preparation every named turn awaits before it is in the log.
        calls.append(str(body.turn_id))
        await gate.wait()

        async def frames():
            yield 'event: done\ndata: {"reply": "once"}\n\n'

        return frames()

    monkeypatch.setattr(chat_routes, "stream_chat_turn", slow_start)
    body = {"messages": [{"role": "user", "content": "explain it"}], "turn_id": "turn-twice"}
    a = asyncio.create_task(_open(app, body))
    b = asyncio.create_task(_open(app, body))
    for _ in range(500):  # let both requests reach the route (no clock involved)
        await asyncio.sleep(0)
        if len(calls) >= 2:
            break
    gate.set()
    ta, tb = await a, await b
    assert calls == ["turn-twice"], calls
    assert "once" in ta and "once" in tb


# --------------------------------------------------------------------------- #
# 4. The compaction cache and the attachment checks run OFF the event loop
# --------------------------------------------------------------------------- #
from types import SimpleNamespace  # noqa: E402

from iron_jarvis.daemon import chat_turn as ct  # noqa: E402
from iron_jarvis.providers.adapters.base import LLMMessage  # noqa: E402


def _on_loop() -> bool:
    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False


@pytest.mark.asyncio
async def test_the_compaction_cache_is_built_read_and_written_off_the_loop(monkeypatch):
    from iron_jarvis.context import compaction as C

    seen: dict[str, bool] = {}

    class Rec:
        summary, covers, stripped, trigger = "# Earlier\nGOAL: x", 4, 0, "auto"

    class Store:
        def get(self, key):
            seen["get"] = _on_loop()
            return None

        def standing(self, covered):
            return None

        def put(self, key, **kw):
            seen["put"] = _on_loop()
            return Rec()

    def build(platform):
        seen["build"] = _on_loop()
        return Store()

    async def fake_compact(pairs, *, complete, trigger):
        return SimpleNamespace(ok=True, summary="# Earlier\nGOAL: x", stripped=0,
                               provider="p", model="m")

    monkeypatch.setattr(ct, "_compaction_store", build)
    monkeypatch.setattr(ct, "_context_window", lambda d, p, m: 400)
    monkeypatch.setattr(C, "compact_messages", fake_compact)
    platform = SimpleNamespace(
        config=SimpleNamespace(), _compaction_complete=lambda p, m: (lambda *a, **k: None),
    )
    body = SimpleNamespace(messages=[
        LLMMessage(role="user" if i % 2 == 0 else "assistant", content="word " * 60)
        for i in range(14)
    ])
    _sys, _msgs, report = await ct._apply_compaction(
        SimpleNamespace(platform=platform), body, "system", "p", "m",
    )
    assert report["compacted"] is True
    assert seen == {"build": False, "get": False, "put": False}, seen


@pytest.mark.asyncio
async def test_the_attachment_gate_and_disk_check_run_off_the_loop(tmp_path, monkeypatch):
    seen: list[bool] = []

    def gate(path):
        seen.append(_on_loop())
        return False, "outside"

    monkeypatch.setattr(ct, "fs_read_ok", gate)
    d = SimpleNamespace(platform=SimpleNamespace(config=SimpleNamespace(home=tmp_path)))
    from iron_jarvis.daemon.schemas import ChatBody

    body = ChatBody(
        messages=[{"role": "user", "content": "read these"}],
        attachments=[str(tmp_path / "a.txt")], thread_files=[str(tmp_path / "b.txt")],
    )
    images, block = await ct._prepare_attachments(
        d, body, inline_budget=1000, rag_budget=1000, rag_k=2,
    )
    assert images == [] and seen == [False, False], seen


# --------------------------------------------------------------------------- #
# 1. A steer note is never dropped when the turn FAILS
# --------------------------------------------------------------------------- #
def test_owed_notes_are_handed_over_once_even_after_the_queue_closed():
    from iron_jarvis.core.turns import TurnRegistry

    reg = TurnRegistry()
    handle = reg.register("t-owed")
    assert reg.steer("t-owed", "shorter") is True
    assert handle.close_steers() == ["shorter"]  # the done path closes early
    # ... then fails before its frame: the note is still owed, once.
    assert handle.owed_steers() == ["shorter"]
    assert handle.owed_steers() == []
    assert reg.steer("t-owed", "later") is False
    reg.release("t-owed", handle)


@pytest.mark.asyncio
async def test_a_note_sent_before_the_turn_fails_comes_back_on_the_error_frame(tmp_path):
    from iron_jarvis.core.turns import TURNS
    from iron_jarvis.daemon.app import create_app
    from tests.test_chat_turn_stop_v1241 import _asgi_post, _body, _drive_stream

    app = create_app(str(tmp_path))
    gate = asyncio.Event()

    async def failing_stream(**kw):
        yield {"type": "text", "text": "Here "}
        await gate.wait()
        raise RuntimeError("the model server went away")

    app.state.platform.router.stream = failing_stream
    posted: dict = {}

    async def steer_mid_answer(ev: str, data: dict) -> None:
        if ev == "token" and "status" not in posted:
            posted["status"], _ = await _asgi_post(
                app, "/chat/turns/turn-F/steer", {"text": "make it shorter"}
            )
            gate.set()

    frames = await _drive_stream(app, _body(tools=[], turn_id="turn-F"), steer_mid_answer)
    assert posted.get("status") == 200, posted
    errors = [d for ev, d in frames if ev == "error"]
    assert len(errors) == 1 and not [d for ev, d in frames if ev == "done"], frames
    assert errors[0].get("unread_steers") == ["make it shorter"], errors[0]
    assert not TURNS.is_running("turn-F")
    status, _ = await _asgi_post(app, "/chat/turns/turn-F/steer", {"text": "later"})
    assert status == 404


@pytest.mark.asyncio
async def test_a_failed_turn_with_no_note_carries_no_unread_key(tmp_path):
    from iron_jarvis.daemon.app import create_app
    from tests.test_chat_turn_stop_v1241 import _body, _drive_stream

    app = create_app(str(tmp_path))

    async def failing_stream(**kw):
        raise RuntimeError("down")
        yield  # pragma: no cover — an async generator

    app.state.platform.router.stream = failing_stream
    frames = await _drive_stream(app, _body(tools=[], turn_id="turn-G"))
    errors = [d for ev, d in frames if ev == "error"]
    assert len(errors) == 1 and "unread_steers" not in errors[0], frames


# --------------------------------------------------------------------------- #
# 3. Everything a completion sends is priced: the tools, the whole prompt,
#    round 0 included — in BOTH lanes
# --------------------------------------------------------------------------- #
import json as _json  # noqa: E402
import random  # noqa: E402

from iron_jarvis.context.budget import estimate_tokens, output_reserve  # noqa: E402
from iron_jarvis.providers.adapters.base import ToolCall  # noqa: E402

_FIT_WINDOW = 4000
_TOOLS = [{"name": f"tool_{i}", "description": "d" * 400, "parameters": {"type": "object"}}
          for i in range(6)]


def _cost_of(msgs, system: str) -> int:
    return estimate_tokens(system) + sum(estimate_tokens(m.content or "") + 4 for m in msgs)


def _tools_cost(tools) -> int:
    return estimate_tokens(_json.dumps(list(tools), ensure_ascii=False, sort_keys=True, default=str))


def _stub_window(monkeypatch, window: int | None = _FIT_WINDOW) -> SimpleNamespace:
    monkeypatch.setattr(ct, "_context_window", lambda d, p, m: window)
    monkeypatch.setattr(ct, "_history_ratio", lambda d, p, m: None)
    monkeypatch.setattr(ct, "_answer_reserve", lambda d, p, m, w: None)
    monkeypatch.setattr(ct, "_reserve_kw", lambda *a, **k: {})
    return SimpleNamespace(platform=SimpleNamespace())


def _history(n: int, size: int) -> list:
    out = []
    for i in range(n):
        out.append(LLMMessage(role="user", content=f"u{i} " + "x" * size))
        out.append(LLMMessage(role="assistant", content=f"a{i} " + "y" * size))
    return out


def test_round_zero_is_fitted_with_the_tools_priced(monkeypatch):
    d = _stub_window(monkeypatch)
    q = LLMMessage(role="user", content="the question")
    # A history the planner fitted WITHOUT the tools: it fills the budget.
    msgs = [*_history(10, 480), q]
    system = "S" * 400
    budget = _FIT_WINDOW - output_reserve(_FIT_WINDOW)
    assert _cost_of(msgs, system) <= budget < _cost_of(msgs, system) + _tools_cost(_TOOLS)
    sent, sent_system = ct._fit_turn_transcript(
        d, msgs, system, "p", "m", head=len(msgs), tools=_TOOLS,
    )
    assert _cost_of(sent, sent_system) + _tools_cost(_TOOLS) <= budget
    assert sent[-1] is q, "the question is never left out"
    assert sent[0].role == "user" and sent == msgs[len(msgs) - len(sent):]
    assert ct.OVERFLOW_NOTE in sent_system, "the model is told older messages were left out"
    # Without tools the same request already fit, and is sent as it was.
    same, same_system = ct._fit_turn_transcript(d, msgs, system, "p", "m", head=len(msgs))
    assert same is msgs and same_system == system


def test_a_prompt_grown_after_the_planner_is_priced_on_round_zero(monkeypatch):
    d = _stub_window(monkeypatch)
    q = LLMMessage(role="user", content="the question")
    msgs = [*_history(10, 520), q]
    grown = "S" * 400 + "G" * 3000  # added to the system prompt after planning
    budget = _FIT_WINDOW - output_reserve(_FIT_WINDOW)
    sent, sent_system = ct._fit_turn_transcript(d, msgs, grown, "p", "m", head=len(msgs))
    assert _cost_of(sent, sent_system) <= budget and sent[-1] is q


def test_an_unknown_window_still_sends_round_zero_as_is(monkeypatch):
    d = _stub_window(monkeypatch, window=None)
    msgs = [*_history(10, 520), LLMMessage(role="user", content="q")]
    sent, system = ct._fit_turn_transcript(d, msgs, "S" * 9000, "p", "m", head=len(msgs), tools=_TOOLS)
    assert sent is msgs and system == "S" * 9000


def test_the_tools_are_priced_when_rounds_are_trimmed(monkeypatch):
    d = _stub_window(monkeypatch)
    q = LLMMessage(role="user", content="q")
    msgs = [q]
    for i in range(4):
        c = ToolCall(id=f"c{i}", name="t")
        msgs += [LLMMessage(role="assistant", content="", tool_calls=[c]),
                 LLMMessage(role="tool", content="r" * 2600, tool_call_id=c.id, name="t")]
    budget = _FIT_WINDOW - output_reserve(_FIT_WINDOW)
    sent, sent_system = ct._fit_turn_transcript(d, msgs, "S", "p", "m", head=1, tools=_TOOLS)
    assert _cost_of(sent, sent_system) + _tools_cost(_TOOLS) <= budget
    assert sent[-1] is msgs[-1], "the newest round arrives whole"


def test_the_overflow_refit_prices_the_tools(monkeypatch):
    d = _stub_window(monkeypatch)
    monkeypatch.setattr(ct, "learn_window", lambda *a, **k: None)
    q = LLMMessage(role="user", content="q")
    msgs = [*_history(6, 900), q]
    refit = ct._overflow_refit(d, msgs, "S", "p", "m", head=len(msgs), tools=_TOOLS)

    class TooLong(Exception):
        pass

    out = refit(TooLong("maximum context length is 4000 tokens"), msgs, "S")
    assert out is not None
    sent, system = out
    assert _cost_of(sent, system) + _tools_cost(_TOOLS) <= int(4000 * 0.98)
    assert sent[-1] is q


def _pairs_ok(sent, q) -> list[str]:
    errs: list[str] = []
    pending: set = set()
    for m in sent:
        if m.role == "tool":
            if m.tool_call_id not in pending:
                errs.append("orphan tool " + str(m.tool_call_id))
            else:
                pending.discard(m.tool_call_id)
        else:
            if pending:
                errs.append("missing results " + str(pending))
            pending = set()
            if m.role == "assistant" and m.tool_calls:
                pending = {c.id for c in m.tool_calls}
    if pending:
        errs.append("missing results at end")
    if q not in sent:
        errs.append("question lost")
    return errs


def test_no_fit_ever_splits_a_tool_turn_or_loses_the_question(monkeypatch):
    """The reviewer's property run (fit.py), now with tools and history
    that round 0 may drop: 1,500 random turns, no broken pair, no lost
    question."""
    rng = random.Random(1)
    bad = []
    for trial in range(1500):
        d = _stub_window(monkeypatch, window=rng.choice([4000, 8000, 16000]))
        hist = []
        for _ in range(rng.randint(0, 4)):
            hist.append(LLMMessage(role="user", content="u" * rng.randint(10, 3000)))
            hist.append(LLMMessage(role="assistant", content="a" * rng.randint(10, 3000)))
        q = LLMMessage(role="user", content="question " * rng.randint(1, 300))
        msgs = [*hist, q]
        head = len(msgs)
        n = 0
        for _ in range(rng.randint(0, 8)):
            k = rng.randint(1, 3)
            calls = [ToolCall(id=f"c{n + j}", name="t") for j in range(k)]
            n += k
            msgs.append(LLMMessage(role="assistant", content="x" * rng.randint(0, 500), tool_calls=calls))
            for c in calls:
                msgs.append(LLMMessage(role="tool", content="r" * rng.randint(10, 12000),
                                       tool_call_id=c.id, name="t"))
            if rng.random() < 0.3:
                msgs.append(LLMMessage(role="user", content="steer note " * rng.randint(1, 50)))
        tools = _TOOLS[: rng.randint(0, len(_TOOLS))]
        sent, _sys = ct._fit_turn_transcript(
            d, msgs, "S" * rng.randint(100, 8000), "p", "m", head=head, tools=tools,
        )
        errs = _pairs_ok(sent, q)
        if errs:
            bad.append((trial, errs))
    assert not bad, bad[:5]


def test_both_lanes_hand_the_round_its_tools_to_price():
    """Lock-step pin (the two chat lanes change together): every round call
    of both lanes passes the tool definitions to the fitter and the refit."""
    import inspect

    from iron_jarvis.daemon.routes import chat as chat_routes

    for src in (inspect.getsource(ct.run_chat_turn), inspect.getsource(chat_routes.chat_stream)):
        assert src.count("record=_refit_note, tools=tool_specs") == 1
        assert "head=_head,\n" in src and "tools=tool_specs,\n" in src


# --------------------------------------------------------------------------- #
# 2. A long chat's oldest messages are KEPT, never deleted
# --------------------------------------------------------------------------- #
from fastapi.testclient import TestClient  # noqa: E402

from sqlmodel import select  # noqa: E402

from iron_jarvis.core.db import session_scope  # noqa: E402
from iron_jarvis.daemon import chat_append as CA  # noqa: E402
from iron_jarvis.daemon.app import create_app  # noqa: E402
from iron_jarvis.search.models import SearchDocRecord  # noqa: E402


def _m(i: int, role: str = "user") -> dict:
    return {"role": role, "content": f"message number {i}"}


def test_what_leaves_the_live_window_is_found_once():
    keep = 5
    # A first save of more than the window: the extra leaves.
    assert CA.leaving_live_window([], [_m(i) for i in range(8)], keep) == [_m(i) for i in range(3)]
    # A reopened chat (the page holds only the live window) plus two new ones.
    stored = [_m(i) for i in range(3, 8)]
    assert CA.leaving_live_window(stored, [*stored, _m(8), _m(9)], keep) == [_m(3), _m(4)]
    # The page still holds what already moved: those are not moved again.
    sent = [_m(i) for i in range(10)]  # 0..2 already moved; 3..7 live; 8, 9 new
    assert CA.leaving_live_window(stored, sent, keep) == [_m(3), _m(4)]
    # The stored copy (with its server stamp) is the one kept.
    stamped = [{**m, "at": "2026-01-01T00:00:00+00:00"} for m in stored]
    got = CA.leaving_live_window(stamped, sent, keep)
    assert got == stamped[:2]
    # Nothing leaves while the window is not full.
    assert CA.leaving_live_window(stored, stored, keep) == []
    # A page that rewrote the start: whatever the cap drops leaves.
    other = [_m(100 + i) for i in range(7)]
    assert CA.leaving_live_window(stored, other, keep) == other[:2]


def test_a_long_chat_keeps_every_message_and_search_finds_the_oldest(tmp_path):
    from iron_jarvis.core.db import search_index

    with TestClient(create_app(str(tmp_path))) as client:
        engine = client.app.state.platform.engine
        r = client.put("/chat/threads/new", json={"messages": [_m(i) for i in range(250)]})
        assert r.status_code == 200, r.text
        tid = r.json()["id"]
        got = client.get(f"/chat/threads/{tid}").json()
        # The live window is the same size as before.
        assert len(got["messages"]) == 200 and got["messages"][0]["content"] == "message number 50"
        assert got["older"] == 50
        older = client.get(f"/chat/threads/{tid}/older").json()
        assert [m["content"] for m in older["messages"]] == [f"message number {i}" for i in range(50)]
        assert all(m.get("at") for m in older["messages"]), "kept messages carry their time"
        # The page keeps chatting with all 250 in memory: nothing is moved twice.
        held = [_m(i) for i in range(250)]
        for n in range(3):
            held += [_m(250 + 2 * n, "user"), _m(251 + 2 * n, "assistant")]
            r = client.put(f"/chat/threads/{tid}", json={"messages": held})
            assert r.status_code == 200, r.text
        older = client.get(f"/chat/threads/{tid}/older?limit=1000").json()
        assert older["total"] == 56
        assert [m["content"] for m in older["messages"]] == [f"message number {i}" for i in range(56)]
        live = client.get(f"/chat/threads/{tid}").json()["messages"]
        assert [m["content"] for m in live] == [f"message number {i}" for i in range(56, 256)]
        # Reopened (the page loads only the live window) and two more: two move.
        r = client.put(f"/chat/threads/{tid}", json={"messages": [*live, _m(256), _m(257)]})
        assert r.status_code == 200
        assert client.get(f"/chat/threads/{tid}").json()["older"] == 58
        # History search still finds the oldest words, under this chat.
        hits = search_index(engine).search('"message number 3"')
        assert any(h.thread_id == tid for h in hits), hits
        # A rename follows into the older messages' search entries.
        client.put(f"/chat/threads/{tid}", json={"messages": [*live, _m(256), _m(257)], "title": "Renamed"})
        with session_scope(engine) as db:
            older_docs = [d for d in db.exec(select(SearchDocRecord)) if d.thread_id == tid and d.seq < 0]
        assert len(older_docs) == 58 and {d.title for d in older_docs} == {"Renamed"}
        # Delete means all of it.
        assert client.delete(f"/chat/threads/{tid}").status_code == 200
        assert CA.older_count(engine, tid) == 0
        with session_scope(engine) as db:
            assert not [d for d in db.exec(select(SearchDocRecord)) if d.thread_id == tid]


def test_an_append_past_the_window_keeps_the_oldest_line(tmp_path):
    from iron_jarvis.core.models import ChatThreadRecord

    app = create_app(str(tmp_path))
    engine = app.state.platform.engine
    rec = ChatThreadRecord(
        title="t", messages_json=_json.dumps([_m(i) for i in range(CA.MAX_MESSAGES)])
    )
    with session_scope(engine) as db:
        db.add(rec)
        db.commit()
        tid = rec.id
    out = CA.append_to_chat_thread(engine, tid, [{"role": "assistant", "content": "last"}])
    assert out["ok"] and out["appended"] == 1
    assert [m["content"] for m in CA.read_older(engine, tid)] == ["message number 0"]


def test_the_compaction_header_promises_only_what_is_true():
    from iron_jarvis.context import compaction as C

    assert "can be reopened" not in C._SUMMARY_HEADER
    assert C._SUMMARY_HEADER.startswith("# Earlier in this conversation (compacted")


def test_the_older_messages_table_is_made_at_boot(tmp_path):
    """init_db makes the table on every install's database, not only on the
    first save that overflows (core.db._LATE_MODEL_MODULES, v1.499.3). Run in
    a FRESH interpreter: in this one the test file already imported the model,
    which would make the check pass with the entry gone."""
    import subprocess
    import sys

    code = (
        "import sys; from sqlalchemy import inspect; "
        "from iron_jarvis.core.db import init_db, make_engine; "
        "assert 'iron_jarvis.daemon.chat_append' not in sys.modules; "
        "e = make_engine(sys.argv[1]); init_db(e); "
        "print('chatoldermessage' in inspect(e).get_table_names()); e.dispose()"
    )
    out = subprocess.run([sys.executable, "-c", code, str(tmp_path / "boot.db")],
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stdout.strip().splitlines()[-1] == "True"


def test_a_torn_value_never_reads_as_its_inner_fragment():
    """Later openers are tried only at the TOP level (v1.499.3 CI): a
    truncated config whose outer object never closes is unreadable, never
    its inner server entry; two top-level candidates still work."""
    from iron_jarvis.core.jsonish import loads_object

    torn = '{"mcpServers": {"brave": {"command": "x", "env": {"K": "v"}}'
    assert loads_object(torn) is None
    assert loads_object('Set the {name} field like this: {"name": "Q3 report"}') == {"name": "Q3 report"}
