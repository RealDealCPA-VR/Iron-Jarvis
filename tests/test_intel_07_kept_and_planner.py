"""The intelligence plan, release 7: the parts under the stable prefix.

* what a chat keeps between replies (``daemon/chat_kept``): tool NAMES only,
  tied to one chat, bounded, gone with the chat;
* the tool list's order and which tools stay (``context/prefix``);
* the history planner (``context/budget.plan_history``): what rides beside a
  message is counted with it, the tool definitions are counted before the
  history is chosen, and a history that sheds messages starts in the same
  place for several replies;
* the hint an adapter gets about where the part that comes back ends;
* a coding session is not touched.

Real database, real planner, real registry. Nothing here sleeps.
"""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from iron_jarvis.context import budget as B
from iron_jarvis.context import prefix as P
from iron_jarvis.core.db import open_db, session_scope
from iron_jarvis.core.ids import utcnow
from iron_jarvis.daemon import chat_kept as K
from iron_jarvis.daemon import chat_prefix as CP
from iron_jarvis.daemon.app import create_app
from iron_jarvis.providers.adapters.base import LLMMessage, LLMResponse
from iron_jarvis.providers.adapters.mock import MockLLMAdapter


@pytest.fixture()
def engine(tmp_path):
    e = open_db(str(tmp_path / "kept.db"))
    yield e
    e.dispose()


# --------------------------------------------------------------------------- #
# What a chat keeps
# --------------------------------------------------------------------------- #
def test_a_chat_keeps_its_tool_names_in_order(engine):
    assert K.load(engine, "t1") == K.Kept()
    assert K.save_tools(engine, "t1", "", ["b_tool", "a_tool", "c_tool"], held=["a_tool", "c_tool"])
    got = K.load(engine, "t1")
    assert got.tools == ["b_tool", "a_tool", "c_tool"], "the order first sent, not sorted"
    assert got.held == {"a_tool", "c_tool"}
    # A save replaces what the chat held (one row a chat).
    K.save_tools(engine, "t1", "", ["b_tool", "a_tool", "c_tool", "d_tool"], held=["d_tool"])
    got = K.load(engine, "t1")
    assert got.tools[-1] == "d_tool" and got.held == {"d_tool"}
    with session_scope(engine) as db:
        assert len(list(db.exec(select(K.ChatKept)))) == 1
    # Another chat holds nothing of it.
    assert K.load(engine, "t2") == K.Kept()
    # The table has no column that could hold a message's or a file's text.
    assert set(K.ChatKept.__table__.columns.keys()) == {"id", "thread_id", "turn_id", "names_json", "created_at"}


def test_only_names_are_kept_for_tools_and_they_are_bounded(engine):
    names = [f"tool_{i}" for i in range(K.MAX_TOOLS + 30)] + ["x" * 900, "", None, "tool_1"]
    K.save_tools(engine, "t1", "", names, held=names)
    got = K.load(engine, "t1")
    assert len(got.tools) == K.MAX_TOOLS and all(len(n) <= K.MAX_NAME for n in got.tools)
    with session_scope(engine) as db:
        row = db.exec(select(K.ChatKept)).one()
    saved = json.loads(row.names_json)
    assert set(saved) == {"names", "held"} and all(isinstance(n, str) for n in saved["names"] + saved["held"])


def test_nothing_is_kept_for_a_reply_nobody_can_find_again(engine):
    assert K.save_tools(engine, "", "", ["a_tool"]) is False
    with session_scope(engine) as db:
        assert list(db.exec(select(K.ChatKept))) == []
    assert K.load(engine, "", "") == K.Kept()


def test_a_first_replys_row_gets_its_chat_and_orphans_are_swept(engine):
    K.save_tools(engine, "", "turn-1", ["a_tool"], held=["a_tool"])
    assert K.load(engine, "t-new") == K.Kept()
    assert K.home_turn(engine, "turn-1", "t-new") == 1
    assert K.load(engine, "t-new").tools == ["a_tool"]
    assert K.home_turn(engine, "turn-1", "t-other") == 0, "a turn is given its chat once"
    # A chat that already holds a row keeps it: a second first-reply row is dropped.
    K.save_tools(engine, "", "turn-2", ["z_tool"], held=["z_tool"])
    assert K.home_turn(engine, "turn-2", "t-new") == 0 and K.load(engine, "t-new").tools == ["a_tool"]
    with session_scope(engine) as db:
        assert [r.turn_id for r in db.exec(select(K.ChatKept))] == ["turn-1"]
    # A row whose turn never became a chat goes with the next write, after its time.
    K.save_tools(engine, "", "turn-lost", ["q_tool"])
    with engine.begin() as conn:
        conn.execute(K.ChatKept.__table__.update().where(K.ChatKept.turn_id == "turn-lost")
                     .values(created_at=utcnow() - timedelta(days=K.ORPHAN_DAYS, minutes=1)))
    K.save_tools(engine, "t-new", "", ["a_tool", "b_tool"], held=["b_tool"])
    with session_scope(engine) as db:
        assert [r.thread_id for r in db.exec(select(K.ChatKept))] == ["t-new"]


def test_a_deleted_chats_row_goes_and_no_other(engine):
    K.save_tools(engine, "t1", "", ["a_tool"])
    K.save_tools(engine, "t2", "", ["b_tool"])
    with session_scope(engine) as db:
        K.drop_thread(db, "t1")
        db.commit()
    assert K.load(engine, "t1") == K.Kept() and K.load(engine, "t2").tools == ["b_tool"]


# --------------------------------------------------------------------------- #
# The tool list: same tools, same order; what stays
# --------------------------------------------------------------------------- #
def _specs(*names):
    return [{"name": n, "description": f"about {n}", "input_schema": {"type": "object"}} for n in names]


def test_a_tool_the_chat_already_sent_keeps_its_place_and_a_new_one_goes_at_the_end():
    kept = ["find", "escalate", "read", "list"]
    assert P.order_names(["list", "write", "read", "find", "search"], kept) == ["find", "read", "list", "write", "search"]
    ordered = P.order_specs(_specs("list", "write", "read", "find", "search"), kept)
    assert [P.spec_name(s) for s in ordered] == ["find", "read", "list", "write", "search"]
    assert ordered[0] == _specs("find")[0], "a definition is moved, never changed"
    assert P.order_specs(_specs("b", "a"), []) == _specs("b", "a"), "a new chat: the order given"
    assert P.spec_name({"type": "function", "function": {"name": "fn_shape"}}) == "fn_shape"


def test_a_tool_that_joins_in_the_middle_of_a_reply_goes_at_the_end():
    from iron_jarvis.daemon.chat_turn import _keep_spec_order

    before = _specs("find", "read", "escalate")
    rebuilt = _specs("list", "read", "find", "escalate")   # the registry's own order
    assert [P.spec_name(s) for s in _keep_spec_order(before, rebuilt)] == ["find", "read", "escalate", "list"]
    assert _keep_spec_order(None, rebuilt) == rebuilt


def test_kept_readers_stay_under_the_cap_and_a_tool_the_message_asked_for_never_leaves():
    reads = lambda n: n.startswith("r")  # noqa: E731
    # The chat's kept readers first, in their order, then this message's picks.
    assert P.keep_auto([], ["w1", "r3"], ["r1", "r2", "w0"], may_stay=reads, ceiling=6) == ["r1", "r2", "w1", "r3"]
    # A kept tool that is not a reader stays only when THIS message asks for it.
    assert P.keep_auto([], ["w0"], ["r1", "w0"], may_stay=reads, ceiling=6) == ["r1", "w0"]
    # No room: a kept tool the message did not ask for leaves, the newest first.
    assert P.keep_auto(["pick"], ["r9"], ["r1", "r2", "r3"], may_stay=reads, ceiling=3) == ["r1", "r9"]
    # What the message asked for is never the one that leaves, and a ticked tool is not listed twice.
    assert P.keep_auto(["pick"], ["a", "b"], ["r1", "pick"], may_stay=reads, ceiling=3) == ["a", "b"]
    # A check that breaks means the tool does not stay.
    assert P.keep_auto([], [], ["r1"], may_stay=lambda n: 1 / 0, ceiling=6) == []


def test_the_readers_that_stay_are_autos_own_and_never_reach_the_internet(tmp_path):
    from iron_jarvis.platform import build_platform
    from iron_jarvis.providers import egress
    from iron_jarvis.tools.autoselect import AUTO_SAFE_TOOLS
    from iron_jarvis.tools.base import Reversibility

    registry = build_platform(str(tmp_path / "home")).registry
    assert CP.UNDECLARED_READERS <= AUTO_SAFE_TOOLS
    assert not (CP.UNDECLARED_READERS & egress.INTERNET_TOOLS)
    assert not ({"web_search", "web_fetch", "view_image"} & CP.UNDECLARED_READERS)
    for name in CP.UNDECLARED_READERS:
        assert registry.get(name) is not None, name
    # A tool that changes something declares it, and so can never stay.
    for name in ("write_file", "write_document", "excel_edit", "docx_edit", "ltm_append", "remember_preference"):
        tool = registry.get(name)
        assert tool is not None and tool.reversibility != Reversibility.READONLY
        assert name not in CP.UNDECLARED_READERS


def test_the_shared_prefix_is_counted_to_the_character():
    assert P.shared_prefix("abcdef", "abcxyz") == 3 and P.shared_prefix("abc", "abc") == 3 and P.shared_prefix("", "a") == 0
    assert P.shared_prefix("abc", "abcd") == 3


# --------------------------------------------------------------------------- #
# The planner
# --------------------------------------------------------------------------- #
def _chat(n: int, size: int = 400) -> list[dict]:
    return [{"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i} " + "x" * size} for i in range(n)]


def test_what_rides_beside_a_message_is_counted_with_it_and_never_cut():
    msgs = _chat(3, size=40)
    big = "R" * 20_000  # more than the per-message cap
    plan = B.plan_history(msgs, window=200_000, riders=[big, "", "ONCE"])
    assert plan.messages[0]["rider"] == big, "a rider is never cut by the per-message cap"
    assert "rider" not in plan.messages[1] and plan.messages[2]["rider"] == "ONCE"
    assert plan.raw_tokens > B.estimate_tokens(big)
    # No riders: the plan, and its messages' keys, are what they always were.
    plain = B.plan_history(msgs, window=200_000)
    assert plain.messages == [{"role": m["role"], "content": m["content"]} for m in msgs]
    assert plain.used_tokens < plan.used_tokens
    # A rider that does not fit leaves WITH its message (never without it).
    tight = B.plan_history(msgs, window=3_000, riders=[big, "", "ONCE"])
    assert [m["content"][:2] for m in tight.messages] == ["m1", "m2"] and tight.dropped == 1
    assert all(m.get("rider") != big for m in tight.messages)
    assert tight.used_tokens <= 3_000 - B.output_reserve(3_000)


def test_the_newest_messages_rider_is_always_sent_whole():
    once = "Z" * 9_000
    # Even when the window cannot hold it: where it rode before (the
    # instructions) it was always sent too, and a fenced block is never cut.
    for msgs in ([{"role": "user", "content": "q"}], _chat(5)):
        plan = B.plan_history(msgs, window=1_200, riders=[""] * (len(msgs) - 1) + [once])
        assert plan.messages[-1]["rider"] == once and plan.messages[-1]["role"] == "user"
    # It is room held back FIRST: the history sheds messages to make room for it.
    msgs = _chat(21)
    without = B.plan_history(msgs, window=4_000)
    with_rider = B.plan_history(msgs, window=4_000, riders=[""] * 20 + ["Z" * 3_600])
    assert len(with_rider.messages) < len(without.messages)
    assert with_rider.used_tokens <= 4_000 - B.output_reserve(4_000)


def test_the_tool_list_is_counted_before_the_history_is_chosen():
    msgs = _chat(41)
    window = 8_000
    free = B.plan_history(msgs, window=window, system_text="S" * 2_000)
    priced = B.plan_history(msgs, window=window, system_text="S" * 2_000, fixed_tokens=2_500)
    assert len(priced.messages) < len(free.messages), "the tools took room the history gave up"
    sent = sum(B.estimate_tokens(m["content"]) + 4 for m in priced.messages)
    assert B.estimate_tokens("S" * 2_000) + 2_500 + sent + B.estimate_tokens(priced.recap) <= window - B.output_reserve(window)
    # Without the count, the same request would not fit once the tools ride along.
    unpriced = sum(B.estimate_tokens(m["content"]) + 4 for m in free.messages)
    assert B.estimate_tokens("S" * 2_000) + 2_500 + unpriced > window - B.output_reserve(window)


def _starts(step: int, replies: int = 14) -> list[str]:
    """The first message sent on each reply of a chat that grows by a
    question and an answer a reply, on a model that holds about 20 messages."""
    out = []
    for n in range(replies):
        msgs = _chat(41 + 2 * n)
        plan = B.plan_history(msgs, window=4_300, step=step)
        assert plan.messages[-1]["content"] == msgs[-1]["content"], "the newest message is always sent"
        out.append(plan.messages[0]["content"].split(" ")[0])
    return out


def test_a_history_that_sheds_messages_starts_in_the_same_place_for_several_replies():
    moving = _starts(step=0)
    coarse = _starts(step=B.CUT_STEP)
    assert len(set(moving)) == len(moving), "before: the start moved with every reply"
    # (Two messages a reply, a step of eight: it moves every fourth reply.)
    assert len(set(coarse)) <= len(coarse) // 4 + 2, f"the start moved too often: {coarse}"
    # It only ever moves forward, by whole steps, and holds for several replies in a row.
    at = [int(s[1:]) for s in coarse]
    assert at == sorted(at) and all(a % B.CUT_STEP == 0 for a in at)
    runs = [sum(1 for x in at if x == v) for v in dict.fromkeys(at)]
    assert max(runs) == B.CUT_STEP // 2
    # And a chat that fits is not touched by it.
    small = _chat(7)
    assert B.plan_history(small, window=32_000, step=B.CUT_STEP).messages == B.plan_history(small, window=32_000).messages


def test_the_coarse_start_never_costs_more_than_half_of_what_fits():
    assert B._coarse_cut(16, 40, 8) == 16, "already on a step"
    assert B._coarse_cut(17, 41, 8) == 24
    assert B._coarse_cut(37, 41, 8) == 38, "8 and 4 would leave too little: the smallest step that keeps half"
    assert B._coarse_cut(39, 41, 8) == 39, "two messages fit: both stay"
    assert B._coarse_cut(13, 41, 0) == 13 and B._coarse_cut(13, 41, 1) == 13, "off"
    for first in range(0, 60):
        for total in range(first + 1, first + 30):
            cut = B._coarse_cut(first, total, B.CUT_STEP)
            fits = total - first
            assert first <= cut <= total - min(2, fits), (first, total, cut)
            assert cut - first <= fits // 2, (first, total, cut)


# --------------------------------------------------------------------------- #
# Through the lanes: the count the reply reports is what was left out
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("lane", ("/chat", "/chat/stream"))
def test_a_long_chat_with_a_tool_list_still_fits_and_says_how_much_it_left_out(tmp_path, lane):
    app = create_app(str(tmp_path / "home"))
    platform = app.state.platform
    platform.config.model_context_windows = {"mock": 9_000}
    platform.config.context_compaction = {"enabled": False}
    seen: list = []

    class _Answer(MockLLMAdapter):
        async def complete(self, *, system, messages, tools, **kw):
            seen.append({"system": system, "messages": [(m.role, m.content) for m in messages], "tools": list(tools or [])})
            return LLMResponse(text="It adds up.", usage={"input_tokens": 10, "output_tokens": 2})

    platform.providers.register("mock", lambda model=None: _Answer())
    history = []
    for i in range(30):
        history.append({"role": "user", "content": f"question {i} " + "about the ledger " * 40})
        history.append({"role": "assistant", "content": f"answer {i} " + "it adds up " * 40})
    history.append({"role": "user", "content": "and the total?"})
    tools = ["read_file", "list_files", "read_document", "file_search", "excel_query", "excel_profile"]
    r = TestClient(app).post(lane, json={"messages": history, "thread_id": "t-fit", "tools": tools})
    assert r.status_code == 200, r.text[:400]
    if lane == "/chat":
        report = r.json()["context"]
    else:
        done = [b for b in r.text.split("\n\n") if b.startswith("event: done")]
        report = json.loads(done[-1].split("data: ", 1)[1])["context"]
    sent = seen[0]
    from iron_jarvis.daemon.chat_turn import _tool_spec_tokens

    spec_tokens = _tool_spec_tokens(sent["tools"])
    assert spec_tokens > 1_500, "the tool list is a real share of this model's room"
    total = B.estimate_tokens(sent["system"]) + spec_tokens + sum(B.estimate_tokens(c) + 4 for _r, c in sent["messages"])
    assert total <= 9_000 - B.output_reserve(9_000), "the whole request fits the model"
    # The planner made room for the tools itself: the count the reply reports
    # is exactly what was left out, not a floor under it.
    assert report["dropped"] == len(history) - len(sent["messages"]) > 0, report
    # The start is on a step, so the next reply starts in the same place.
    first = sent["messages"][0][1]
    assert first.startswith("question ") and (history.index({"role": "user", "content": first})) % B.CUT_STEP == 0


# --------------------------------------------------------------------------- #
# The hint an adapter gets, and a coding session
# --------------------------------------------------------------------------- #
def test_the_anthropic_adapter_marks_the_end_of_the_part_that_comes_back():
    from iron_jarvis.providers.adapters.anthropic import AnthropicAdapter

    own = "what does the second line say?"
    once = "\n\n" + P.ONCE_TOP + "\nToday's date is 2031-02-03.\n" + P.ONCE_BOTTOM
    newest = LLMMessage(role="user", content=own + once, stable_chars=len(own))
    earlier = [LLMMessage(role="user", content="hello"), LLMMessage(role="assistant", content="Hi.")]
    out = AnthropicAdapter._to_anthropic_messages([*earlier, newest])
    assert out[:2] == [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "Hi."}]
    assert out[2] == {"role": "user", "content": [
        {"type": "text", "text": own, "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": once},
    ]}
    # The next reply sends that message as its first block's text alone:
    # everything up to the mark is the same bytes.
    again = AnthropicAdapter._to_anthropic_messages([*earlier, LLMMessage(role="user", content=own)])
    assert again[2]["content"] == out[2]["content"][0]["text"]
    # No hint, a hint with nothing after it, or nothing before it: the message goes whole.
    for msg in (LLMMessage(role="user", content=own + once),
                LLMMessage(role="user", content=own, stable_chars=len(own)),
                LLMMessage(role="user", content="  " + once, stable_chars=2),
                LLMMessage(role="assistant", content=own + once, stable_chars=len(own))):
        assert AnthropicAdapter._to_anthropic_messages([msg]) == [{"role": msg.role, "content": msg.content}]
    # With a picture, the words keep their mark and the picture follows.
    pictured = LLMMessage(role="user", content=own + once, stable_chars=len(own),
                          images=[{"media_type": "image/png", "data_b64": "AAAA"}])
    blocks = AnthropicAdapter._to_anthropic_messages([pictured])[0]["content"]
    assert [b["type"] for b in blocks] == ["text", "text", "image"] and "cache_control" in blocks[0]


@pytest.mark.parametrize("lane", ("/chat", "/chat/stream"))
def test_the_lanes_hand_the_adapter_the_hint_on_the_newest_message_only(tmp_path, lane):
    from tests.test_intel_07_stable_prefix import Chat

    chat = Chat(tmp_path / "home", lane)
    chat.say("hello there, good morning")
    call = chat.say("summarize the attached file please", attachments=["ledger.txt"])
    assert call["stable_chars"][:-1] == [0, 0]
    # The part that comes back is the person's own words, with or without a file.
    assert call["stable_chars"][-1] == len("summarize the attached file please")


def test_a_coding_session_is_not_touched():
    turn = asyncio.run(CP.begin(SimpleNamespace(platform=None), SimpleNamespace(thread_id="t", turn_id=""), harness="auto"))
    assert turn.active is False
    block = CP.AttachBlock("\n\n## Attached file: a.txt\nTEXT\n(LIVE)", files="\n\n## Attached file: a.txt\nTEXT", once="(LIVE)")
    tail = P.own("\n\nRULES") + turn.attached(block)
    assert str(tail) == "\n\nRULES" + CP.ATTACH_HEAD + str(block), "the attachments section is the bytes it was"
    assert turn.later("\n\nSKILLS") == "\n\nSKILLS", "it stays in the instructions"
    assert turn.keep_auto(None, None, ["a"], ["a"], None, 6) == (["a"], ["a"])
    assert turn.kept_app_tools() == [] and turn.keep_conn(None, ["mcp__a__b"], 24) == ["mcp__a__b"]
    specs = _specs("b", "a")
    assert turn.settle(["b", "a"], ["a"], specs) == (["b", "a"], ["a"], specs)
    msgs, plan = [LLMMessage(role="user", content="q")], object()
    out = asyncio.run(turn.place(None, None, system="SYSTEM", tail=tail, plan=plan, msgs=msgs, images=None,
                                 tool_specs=specs, provider="", model="", ctx_messages=None))
    assert out == ("SYSTEM" + str(tail), msgs, plan), "the tail joins the instructions, as before"


def test_an_attached_files_text_is_what_was_found_and_the_lines_about_it_are_read_last():
    turn = CP.StableTurn(active=True)
    block = CP.AttachBlock("whole", files="\n\n## Attached file: a.txt\nFILETEXT", once="\n\n## Attached file: a.txt (LIVE)")
    got = turn.attached(block)
    assert got.of(P.FOUND) == CP.ATTACH_HEAD + block.files and got.of(P.LAST) == block.once and got.of(P.OWN) == ""
    rendered = P.once_block(got)
    assert rendered.index(P.FOUND_TOP) < rendered.index("FILETEXT") < rendered.index(P.FOUND_BOTTOM) < rendered.index("(LIVE)")
    # Only the conversation's earlier files, by name: Iron Jarvis's own lines, no fence needed.
    named = turn.attached(CP.AttachBlock("carried", files="", once="\n\n## Files in this conversation\n- a.txt"))
    assert named.of(P.LAST) == CP.ATTACH_HEAD + "\n\n## Files in this conversation\n- a.txt" and named.of(P.FOUND) == ""
    # A plain string (an older caller) is all information.
    assert turn.attached("plain").of(P.FOUND) == CP.ATTACH_HEAD + "plain"
    # Every form is the same section for the planner and the reply's record: nothing is lost.
    assert "FILETEXT" in str(got) and "(LIVE)" in str(got)


