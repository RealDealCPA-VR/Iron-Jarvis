"""A reply that is about to end on words is asked ONCE to look it up first
(v1.480.0, capability search: "make the model look").

The Phase 2 live run: the model looked something up in 57 of 135 replies. In
34 of the others it ended on words with no tool call at all ("Got it, I'll
remember that"), and in 17 more it only read files. So the lane asks, once,
before such a reply is sent: look it up, or keep the reply.

Driven through the REAL chat lanes (stream and POST) with a scripted model,
and the real ``registry.invoke``. What must hold:

* only a ``find_capability`` answer is acted on; anything else (KEEP, other
  words, another tool, a failure) leaves the reply exactly as written;
* a reply that looked, or called a tool that changes something, is not asked;
* it is asked at most once per reply, never with the setting off, never at
  low trust, never with Auto tools off, never for the demo model;
* the check is billed, and counted as a nudge only when the model looked.
"""

from __future__ import annotations

import json

import pytest
from sqlmodel import select

from iron_jarvis.capability_search import join as J
from iron_jarvis.capability_search import tool as CT_TOOL
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import EventRecord
from iron_jarvis.daemon import chat_turn as CT
from iron_jarvis.daemon.app import create_app
from iron_jarvis.providers.adapters.base import LLMResponse
from iron_jarvis.providers.router import RouteResult
from tests.test_chat_approvals_v1187 import _asgi_post, _drive_stream
from tests.test_script_power_lanes_v1473 import Model, _answer, _body, _call, _rows, _text_of

pytestmark = [pytest.mark.usefixtures("no_network"), pytest.mark.timeout(300)]

NAME = CT_TOOL.NAME
CLAIM = "Got it, I'll remember that from now on."
#: v1.487.0: the reply promises to remember with nothing saved, so Iron
#: Jarvis says so under it (``change_claims``).
NOTED = "\n\n_Note: No preference was changed in this reply._"


def _is_check_note(content) -> bool:
    """The check's note, plain or led by what Iron Jarvis found in the reply
    (v1.487.0: the scripted reply promises "from now on", so the note now
    leads with "No preference was changed in this reply")."""
    tail = CT.LOOK_FIRST_NOTE[len("(From Iron Jarvis, not from the user. "):]
    content = str(content or "")
    return content.startswith("(From Iron Jarvis, not from the user.") and content.endswith(tail)


class Seen(Model):
    """The scripted model, also keeping every transcript it was shown and
    which door (stream / complete) each call came through."""

    def __init__(self, steps) -> None:
        super().__init__(steps)
        self.transcripts: list[list[tuple[str, str]]] = []
        self.doors: list[str] = []

    def _see(self, system, messages, tools) -> None:
        super()._see(system, messages, tools)
        self.transcripts.append([_text_of(m) for m in messages or []])

    async def stream(self, **kw):
        self.doors.append("stream")
        async for frame in super().stream(**kw):
            yield frame

    async def complete(self, **kw):
        self.doors.append("complete")
        return await super().complete(**kw)

    def asked(self) -> list[int]:
        """The calls (by index) that carried the look-first note."""
        return [i for i, t in enumerate(self.transcripts)
                if t and t[-1][0] == "user" and _is_check_note(t[-1][1])]


@pytest.fixture()
def desk(tmp_path):
    app = create_app(str(tmp_path / "home"))
    ws = tmp_path / "work"
    ws.mkdir()
    (ws / "notes.txt").write_text("hello\n", encoding="utf-8")
    app.state.platform.config.capability_search = True
    app.state.platform.config.capability_look_first = True
    J.FOUND.clear()
    yield app, ws
    J.FOUND.clear()


async def _stream(app, body, model, decision: str = "once"):
    router = app.state.platform.router
    router.stream = model.stream
    router.complete = model.complete
    cards: list = []
    frames = await _drive_stream(app, body, _answer(app, cards, decision))
    done = [d for ev, d in frames if ev == "done"]
    assert done, [ev for ev, _ in frames]
    return frames, cards, done[-1]


async def _post(app, body, model) -> dict:
    app.state.platform.router.complete = model.complete
    status, raw = await _asgi_post(app, "/chat", body)
    assert status == 200, raw
    return json.loads(raw)


def _nudges(app) -> list[dict]:
    with session_scope(app.state.platform.engine) as db:
        rows = db.exec(select(EventRecord)).all()
        out = []
        for r in rows:
            if r.type == "autonomy.nudged":
                payload = json.loads(r.payload_json or "{}")
                if payload.get("kind") == "look_first":
                    out.append(payload)
        return out


def _resets(frames) -> list[dict]:
    return [d for ev, d in frames if ev == "reset"]


# --------------------------------------------------------------------------- #
# the stream lane
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_reply_of_words_is_asked_once_and_a_lookup_takes_it_back(desk):
    app, ws = desk
    model = Seen([
        CLAIM,                                                   # round 0: words only
        _call(1, NAME, query="search earlier chats history"),    # the check: it looks
        _call(2, "history_search", query="invoice"),             # the found reader
        "Nothing about invoices in your earlier chats.",
    ])
    frames, cards, done = await _stream(app, _body(ws, "find what we said about invoices"), model)

    assert model.doors == ["stream", "complete", "stream", "stream"], model.doors
    assert model.asked() == [1], "the check was not asked exactly once"
    # The check saw the reply as the model wrote it, then the note.
    assert model.transcripts[1][-2] == ("assistant", CLAIM)
    # The same tools as the round before (the provider's prompt cache holds).
    assert model.offered[1] == model.offered[0]
    # The words on screen were taken back, and the reply is the later one.
    assert [r.get("reason") for r in _resets(frames)] == ["look_first"]
    assert done["reply"].startswith("Nothing about invoices"), done["reply"]
    assert CLAIM not in done["reply"]
    assert len([r for r in _rows(app, NAME) if r.ok]) == 1
    assert len([r for r in _rows(app, "history_search") if r.ok]) == 1
    assert "history_search" in model.offered[2], "what the lookup found did not join"
    assert cards == []
    # The later rounds still carry the reply and the note, in order.
    roles = [r for r, _c in model.transcripts[2]]
    assert roles[-4:] == ["assistant", "user", "assistant", "tool"], roles
    assert len(_nudges(app)) == 1, "a reply that looked is counted as one nudge"


@pytest.mark.asyncio
async def test_keep_sends_the_reply_exactly_as_written(desk):
    app, ws = desk
    answer = "A trial balance lists every account with its debit or credit balance."
    model = Seen([answer, "KEEP"])
    frames, cards, done = await _stream(app, _body(ws, "what is a trial balance?"), model)

    assert model.doors == ["stream", "complete"]
    assert done["reply"] == answer
    assert _resets(frames) == [], "a kept reply was taken back"
    assert "".join(d.get("text", "") for ev, d in frames if ev == "token") == answer
    assert "KEEP" not in json.dumps(done)
    assert _rows(app, NAME) == []
    assert _nudges(app) == [], "a kept reply is not a nudge"


@pytest.mark.asyncio
async def test_the_check_is_billed(desk):
    app, ws = desk

    class Billed(Seen):
        async def stream(self, **kw):
            async for frame in super().stream(**kw):
                if frame.get("type") == "final":
                    frame["response"].usage = {"input_tokens": 100, "output_tokens": 10}
                yield frame

        async def complete(self, **kw):
            route = await super().complete(**kw)
            route.response.usage = {"input_tokens": 70, "output_tokens": 3}
            return route

    model = Billed(["An answer.", "KEEP"])
    _frames, _cards, done = await _stream(app, _body(ws, "what is a ledger?"), model)
    usage = done.get("usage") or {}
    assert usage.get("input_tokens") == 170 and usage.get("output_tokens") == 13, usage


@pytest.mark.asyncio
@pytest.mark.parametrize("second", [
    "Actually, here is another answer.",                 # other words
    [],                                                   # nothing at all
    "other-tool",                                         # a tool that is not the lookup
    "mixed",                                              # the lookup AND another tool
    "raise",                                              # the call fails
])
async def test_anything_but_a_lookup_leaves_the_reply_as_written(desk, second):
    app, ws = desk
    answer = "I'll set that up for you."
    if second == "other-tool":
        step = _call(1, "write_file", path="x.txt", content="planted")
    elif second == "mixed":
        step = [*_call(1, NAME, query="write a file"), *_call(2, "write_file", path="x.txt", content="p")]
    elif second == "raise":
        def step(_model):
            raise RuntimeError("the provider fell over")
    elif second == []:
        step = ""
    else:
        step = second
    model = Seen([answer, step])
    frames, cards, done = await _stream(app, _body(ws, "set up a weekly reminder for payroll"), model)

    assert done["reply"] == answer
    assert _resets(frames) == []
    assert not (ws / "x.txt").exists(), "a tool the check answered with was run"
    assert _rows(app, "write_file") == [] and _rows(app, NAME) == []
    assert model.doors == ["stream", "complete"]
    assert not any(ev == "error" for ev, _ in frames)
    assert _nudges(app) == []


@pytest.mark.asyncio
async def test_a_reply_that_already_looked_is_not_asked(desk):
    app, ws = desk
    model = Seen([
        _call(1, NAME, query="search earlier chats history"),
        "I looked, and there is a way to search earlier chats.",
    ])
    _frames, _cards, done = await _stream(app, _body(ws, "can you search old chats?"), model)
    assert model.asked() == [] and model.doors == ["stream", "stream"]
    assert done["reply"].startswith("I looked")


@pytest.mark.asyncio
async def test_a_reply_asked_once_is_not_asked_again_after_its_lookup(desk):
    app, ws = desk
    model = Seen([
        CLAIM,
        _call(1, NAME, query="zzqx unknown words"),
        "Iron Jarvis has nothing for that.",
        "KEEP",  # never reached: a second check would consume it
    ])
    _frames, _cards, done = await _stream(app, _body(ws, "remember my zzqx"), model)
    assert model.asked() == [1]
    assert model.doors == ["stream", "complete", "stream"], model.doors
    assert done["reply"] == "Iron Jarvis has nothing for that."


@pytest.mark.asyncio
@pytest.mark.parametrize("word", ["KEEP", "keep.", "**KEEP**", " Keep\n"])
async def test_the_checks_own_word_is_never_sent_as_the_reply(desk, word):
    """The model looked when asked, then answered only "KEEP" instead of
    writing its reply again. The user gets the reply it had written before
    the check, never the word."""
    app, ws = desk
    model = Seen([CLAIM, _call(1, NAME, query="zzqx unknown words"), word])
    frames, _cards, done = await _stream(app, _body(ws, "remember my zzqx"), model)
    assert done["reply"] == CLAIM + NOTED, done["reply"]
    shown = ""
    for ev, d in frames:
        if ev == "reset":
            shown = ""
        elif ev == "token":
            shown += d.get("text", "")
    assert shown.startswith(CLAIM), "what is on screen at the end is not the reply"

    J.FOUND.clear()
    model = Seen([CLAIM, _call(1, NAME, query="zzqx unknown words"), word])
    out = await _post(app, _body(ws, "remember my zzqx"), model)
    assert out["reply"] == CLAIM + NOTED, out["reply"]


def test_only_the_bare_word_counts_as_keep():
    assert CT.look_first_kept_word("KEEP") and CT.look_first_kept_word(" keep. ")
    assert not CT.look_first_kept_word("Keep the receipts for seven years.")
    assert not CT.look_first_kept_word("") and not CT.look_first_kept_word(None)


@pytest.mark.asyncio
async def test_a_reply_that_says_keep_with_no_check_before_it_is_left_alone(desk):
    """Only a reply the check took back is ever restored: an ordinary
    answer that happens to be the word is the user's answer."""
    app, ws = desk
    app.state.platform.config.capability_search = False
    model = Seen(["KEEP"])
    _frames, _cards, done = await _stream(app, _body(ws, "one word: keep or toss?"), model)
    assert done["reply"] == "KEEP"


@pytest.mark.asyncio
async def test_a_reply_that_only_read_is_asked_and_one_that_tried_a_change_is_not(desk):
    app, ws = desk
    # Reads only: asked.
    model = Seen([
        _call(1, "read_file", path="notes.txt"),
        "The file says hello.",
        "KEEP",
    ])
    body = _body(ws, "what does notes.txt say?", tools=["read_file"])
    _frames, _cards, done = await _stream(app, body, model)
    assert model.asked() == [2], model.doors
    assert done["reply"] == "The file says hello."

    # A change was tried and DECLINED on its card: the reply is the model's
    # own account of that, and is not asked.
    J.FOUND.clear()
    model = Seen([
        _call(1, "write_file", path="new.txt", content="x"),
        "You declined the change, so nothing was written.",
        "KEEP",
    ])
    body = _body(ws, "write new.txt", tools=["write_file"], approval_mode="always_ask")
    _frames, cards, done = await _stream(app, body, model, decision="deny")
    assert [c.get("tool") for c in cards] == ["write_file"]
    assert model.asked() == [], "a reply that tried a change was asked to look"
    assert model.doors == ["stream", "stream"]


@pytest.mark.asyncio
async def test_the_gates(desk):
    """Each fact that must hold for the check, one at a time."""
    app, ws = desk
    p = app.state.platform

    async def asked(body, steps=("An answer.", "KEEP"), before=None):
        J.FOUND.clear()
        model = Seen(list(steps))
        if before:
            before()
        await _stream(app, body, model)
        return bool(model.asked())

    assert await asked(_body(ws, "set up a weekly payroll reminder")), "the control: it is asked"
    # The setting off.
    p.config.capability_search = False
    assert not await asked(_body(ws, "set up a weekly payroll reminder"))
    p.config.capability_search = True
    # Auto tools off: nothing a lookup finds could join.
    assert not await asked(_body(ws, "set up a weekly payroll reminder", auto_tools=False))
    # No turn id: what a lookup finds is kept per turn.
    body = _body(ws, "set up a weekly payroll reminder")
    body.pop("turn_id")
    assert not await asked(body)
    # A message with nothing to look up.
    assert not await asked(_body(ws, "?!"))
    # An empty reply is another path's business.
    assert not await asked(_body(ws, "set up a weekly payroll reminder"), steps=("", "KEEP"))


def _wanted(app, ws, **over):
    from iron_jarvis.providers.adapters.base import LLMMessage

    d = type("D", (), {"platform": app.state.platform})()
    body = type("B", (), {"auto_tools": True, "turn_id": "t-1"})()
    kw = dict(
        armed=[NAME], msgs=[LLMMessage(role="user", content="set up a weekly reminder")], head=1,
        reply="I'll do that.", round_index=0, rounds=6, trust_state={"low": False, "tainted": False},
    )
    kw.update(over)
    return CT.look_first_wanted(d, body, **kw)


def test_the_gates_that_need_no_lane(desk):
    from iron_jarvis.providers.adapters.base import LLMMessage

    app, ws = desk
    assert _wanted(app, ws) is True
    assert _wanted(app, ws, armed=["read_file"]) is False, "the lookup is not among the reply's tools"
    assert _wanted(app, ws, demo=True) is False
    assert _wanted(app, ws, truncated=True) is False
    assert _wanted(app, ws, trust_state={"low": True}) is False
    assert _wanted(app, ws, trust_state={"low": False, "tainted": True}) is False, (
        "a reply that read flagged content gains no tools, so it is not asked to look")
    # Three rounds are needed after the check: the lookup, the tool, the answer.
    assert _wanted(app, ws, round_index=3, rounds=6) is True
    assert _wanted(app, ws, round_index=4, rounds=6) is False
    # A tool nobody knows is not a reader.
    msgs = [LLMMessage(role="user", content="set up a weekly reminder"),
            LLMMessage(role="tool", name="no_such_tool", content="x")]
    assert _wanted(app, ws, msgs=msgs) is False
    # A reader that ran is fine; an earlier turn's tool message is not this reply's.
    msgs = [LLMMessage(role="user", content="set up a weekly reminder"),
            LLMMessage(role="tool", name="read_file", content="x")]
    assert _wanted(app, ws, msgs=msgs) is True
    # An EARLIER turn's tool message (before the user's question) is not this
    # reply's: a change made two turns ago does not stop today's check.
    msgs = [LLMMessage(role="tool", name="write_file", content="saved"),
            LLMMessage(role="user", content="set up a weekly reminder")]
    assert _wanted(app, ws, msgs=msgs, head=2) is True
    msgs = [LLMMessage(role="tool", name=NAME, content="found"),
            LLMMessage(role="user", content="set up a weekly reminder")]
    assert _wanted(app, ws, msgs=msgs, head=2) is True
    # The setting is the switch, also for a caller that armed the tool itself.
    app.state.platform.config.capability_search = False
    assert _wanted(app, ws) is False
    app.state.platform.config.capability_search = True
    # The check has its OWN switch (it is one more model call on every reply
    # that ends without a tool): the lookup on, the check off = never asked.
    app.state.platform.config.capability_look_first = False
    assert _wanted(app, ws) is False
    app.state.platform.config.capability_look_first = True
    assert _wanted(app, ws) is True
    # Cannot tell = not asked.
    assert CT.look_first_wanted(object(), object(), armed=None, msgs=None, head=0, reply="x",
                                round_index=0, rounds=6, trust_state=None) is False


@pytest.mark.asyncio
async def test_the_demo_model_is_never_asked(desk):
    """The real router, the real offline demo: one call, no check."""
    app, ws = desk
    router = app.state.platform.router
    calls = {"complete": 0}
    real = router.complete

    async def counted(**kw):
        calls["complete"] += 1
        return await real(**kw)

    router.complete = counted
    frames = await _drive_stream(app, _body(ws, "set up a weekly payroll reminder"), _answer(app, []))
    done = [d for ev, d in frames if ev == "done"][-1]
    assert (done.get("route") or {}).get("reason") == "mock"
    assert calls["complete"] == 0, "the demo model was asked to look"


# --------------------------------------------------------------------------- #
# the POST lane (lock-step)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_the_post_lane_asks_too_and_a_found_reader_runs(desk):
    app, ws = desk
    model = Seen([
        CLAIM,
        _call(1, NAME, query="search earlier chats history"),
        _call(2, "history_search", query="invoice"),
        "Nothing about invoices in your earlier chats.",
    ])
    out = await _post(app, _body(ws, "find what we said about invoices"), model)
    assert model.asked() == [1]
    # The later rounds carry the reply as written and the note, in order.
    assert model.transcripts[2][-4][0:2] == ("assistant", CLAIM), model.transcripts[2][-4:]
    assert model.transcripts[2][-3][0] == "user" and _is_check_note(model.transcripts[2][-3][1])
    assert [r for r, _c in model.transcripts[2][-2:]] == ["assistant", "tool"]
    assert out["reply"].startswith("Nothing about invoices"), out["reply"]
    assert CLAIM not in out["reply"]
    assert len([r for r in _rows(app, "history_search") if r.ok]) == 1
    assert len(_nudges(app)) == 1


@pytest.mark.asyncio
async def test_the_post_lane_keeps_a_reply_too(desk):
    app, ws = desk
    model = Seen(["A ledger is the book of accounts.", "KEEP"])
    out = await _post(app, _body(ws, "what is a ledger?"), model)
    assert out["reply"] == "A ledger is the book of accounts."
    assert model.asked() == [1] and _rows(app, NAME) == []


@pytest.mark.asyncio
async def test_with_the_setting_off_neither_lane_makes_a_second_call(desk):
    app, ws = desk
    app.state.platform.config.capability_search = False
    model = Seen(["An answer.", "KEEP"])
    _frames, _cards, done = await _stream(app, _body(ws, "set up a weekly reminder"), model)
    assert model.doors == ["stream"] and done["reply"] == "An answer."
    model = Seen(["An answer.", "KEEP"])
    out = await _post(app, _body(ws, "set up a weekly reminder"), model)
    assert model.doors == ["complete"] and out["reply"] == "An answer."


@pytest.mark.asyncio
async def test_the_check_is_off_by_default_and_the_clearer_words_are_not(desk):
    """Feature lookup alone: no second call in either lane, and the map and
    the card words (which cost nothing) are still there."""
    from iron_jarvis.core.config import Config
    from iron_jarvis.settings import schema

    app, ws = desk
    assert Config.model_fields["capability_look_first"].default is False
    row = next(r for r in schema.SETTINGS if r.key == "capability_look_first")
    assert row.type == "bool" and row.tier == "allow" and row.group == "models"
    assert "Feature lookup" in row.help and "later" in row.help, "the row must say what it costs"
    app.state.platform.config.capability_look_first = False
    model = Seen(["An answer.", "KEEP"])
    _frames, _cards, done = await _stream(app, _body(ws, "set up a weekly reminder"), model)
    assert model.doors == ["stream"] and done["reply"] == "An answer."
    assert "Words alone change nothing" in model.systems[0], "the map is part of Feature lookup itself"
    model = Seen(["An answer.", "KEEP"])
    out = await _post(app, _body(ws, "set up a weekly reminder"), model)
    assert model.doors == ["complete"] and out["reply"] == "An answer."


def test_both_lanes_ask_at_their_final_exit():
    """MIRROR NOTE: one check, called by both lanes, plain chat only."""
    import inspect

    from iron_jarvis.daemon.routes import chat as RC

    post = inspect.getsource(CT.run_chat_turn)
    stream = inspect.getsource(RC)
    for src, mod in ((post, ""), (stream, "_chat_turn.")):
        assert f"{mod}look_first_wanted(" in src and f"{mod}look_first_call(" in src
        at = src.index(f"{mod}look_first_wanted(")
        gate = src[src.rfind("if (", 0, at): at]
        assert "not calls" in gate and "_harness is None" in gate and "not text_only_pick" in gate
        assert "not _looked_first" in gate
        assert f"{mod}note_look_first(" in src
    assert 'yield _ev("reset", {"reason": "look_first"})' in stream
    assert "_prep_step(_chat_turn.look_first_call(" in stream, "the check is not raced against Stop"


# --------------------------------------------------------------------------- #
# the words
# --------------------------------------------------------------------------- #
def test_the_note_and_the_map_say_words_alone_change_nothing(desk):
    app, _ws = desk
    assert "words alone change nothing" in CT.LOOK_FIRST_NOTE
    assert "`find_capability`" in CT.LOOK_FIRST_NOTE and "KEEP" in CT.LOOK_FIRST_NOTE
    assert "English" in CT.LOOK_FIRST_NOTE
    block = CT.capability_block([NAME], app.state.platform.config)
    assert "Words alone change nothing" in block
    assert "approval card" in block and "ask the user first" not in block
    # Bounded: these ride every turn / every check. v1.489.0 (moved on
    # purpose, 240 / 260 before): the note also says to look before asking for
    # setup details or answering about Iron Jarvis's own state, and the map
    # says which kind of thing a request is (schedule, rule, goal, skill).
    assert len(CT.LOOK_FIRST_NOTE.split()) < 300 and len(block.split()) < 360
    # No dashes that read as jargon, and no retired word.
    for text in (CT.LOOK_FIRST_NOTE, block):
        assert "job" not in text.lower().split()


@pytest.mark.asyncio
async def test_a_tool_that_asks_is_described_by_its_card_and_the_model_is_told_to_call_it(desk):
    """"asks the user first" was read as an instruction: in 15 live replies
    the right tool had joined and the model asked "Would you like me to…?"
    instead of calling it."""
    app, ws = desk
    model = Seen([
        _call(1, NAME, query="create a new agent"),
        "Shall I?",
    ])
    await _stream(app, _body(ws, "hello there"), model)
    said = model.tool_texts[1][-1]
    assert f"`agent_create` ({J.CARD_WORDS})" in said, said
    assert J.CALL_IT_NOTE in said
    assert "asks the user first" not in said
    assert "do not ask again in words" in J.CALL_IT_NOTE
    assert "a detail the call needs" in J.CALL_IT_NOTE

    # A reader that joins granted: no card words, no "call it now" line.
    J.FOUND.clear()
    model = Seen([_call(1, NAME, query="search earlier chats history"), "ok"])
    await _stream(app, _body(ws, "hello there"), model)
    said = model.tool_texts[1][-1]
    assert "`history_search`" in said and f"`history_search` ({J.CARD_WORDS})" not in said
    assert J.CALL_IT_NOTE not in J._note(["history_search"], [], {}, False)
    assert J.CALL_IT_NOTE in J._note(["agent_create"], ["agent_create"], {}, False)


def test_the_answer_lines_say_the_card(desk):
    app, _ws = desk
    text = CT_TOOL.answer(app.state.platform, "make a new agent")[0]
    assert CT_TOOL.ASKS_LINE in text and "It asks the user first" not in text
    assert "approval card" in CT_TOOL.ASKS_LINE and "approval card" in CT_TOOL.ASKS_WHEN_FOUND_LINE
    assert "approval card" in CT_TOOL.FindCapabilityTool.description if hasattr(
        CT_TOOL, "FindCapabilityTool") else True


def test_the_nudge_kind_is_known_to_the_dashboard():
    from pathlib import Path

    from iron_jarvis.core import nudges

    assert nudges.LOOK_FIRST in nudges.NUDGE_KINDS
    ts = (Path(__file__).parent.parent / "dashboard" / "lib" / "autonomyMetrics.ts").read_text(
        encoding="utf-8").replace("\r\n", "\n")
    assert "\n  look_first: [" in ts


def test_an_answer_of_lookup_calls_only_is_the_only_one_handed_back(desk):
    """``look_first_call`` itself: what it returns for each shape of answer."""
    import asyncio

    from iron_jarvis.providers.adapters.base import LLMMessage, ToolCall

    app, _ws = desk
    d = type("D", (), {"platform": app.state.platform})()
    msgs = [LLMMessage(role="user", content="set up a reminder")]

    def run(response=None, exc=None):
        async def complete(**kw):
            if exc is not None:
                raise exc
            assert kw["messages"][-1].content == CT.LOOK_FIRST_NOTE
            assert kw["messages"][-2].role == "assistant" and kw["messages"][-2].content == "draft"
            assert kw["tools"] == [{"name": NAME}]
            return RouteResult(response, "p", "m")

        app.state.platform.router.complete = complete
        return asyncio.run(CT.look_first_call(
            d, msgs=msgs, system="s", provider="", model="", head=1, tools=[{"name": NAME}],
            reply="draft",
        ))

    look = ToolCall(id="a", name=NAME, arguments={"query": "reminder"})
    other = ToolCall(id="b", name="write_file", arguments={})
    got = run(LLMResponse(text="", tool_calls=[look], usage={"input_tokens": 5, "output_tokens": 2}))
    assert got[0] is not None and got[2:] == (5, 2, 1)
    assert [m.role for m in got[1]] == ["assistant", "user"]
    assert run(LLMResponse(text="KEEP"))[0] is None
    assert run(LLMResponse(text="", tool_calls=[other]))[0] is None
    assert run(LLMResponse(text="", tool_calls=[look, other]))[0] is None
    assert run(exc=RuntimeError("down"))[0] is None
    assert run(exc=RuntimeError("down"))[4] == 0, "a call that failed is not counted as billed"

    # The check's use is added to the turn's own tally (its cost on the receipt).
    class Tally:
        def __init__(self):
            self.rows = []

        def add(self, provider, model, usage):
            self.rows.append((provider, model, dict(usage)))

    tally = Tally()

    async def complete(**kw):
        return RouteResult(LLMResponse(text="KEEP", usage={"input_tokens": 9, "output_tokens": 1}), "p", "m")

    app.state.platform.router.complete = complete
    asyncio.run(CT.look_first_call(
        d, msgs=msgs, system="s", provider="", model="", head=1, tools=[], reply="draft", tally=tally,
    ))
    assert tally.rows == [("p", "m", {"input_tokens": 9, "output_tokens": 1})]
    assert len(msgs) == 1, "the lane's own transcript was changed by the check"


def test_the_check_is_fitted_to_the_models_window_like_every_call(desk, monkeypatch):
    """v1.287.0: a tool loop never hands its raw transcript to a completion."""
    import asyncio

    from iron_jarvis.providers.adapters.base import LLMMessage

    app, _ws = desk
    d = type("D", (), {"platform": app.state.platform})()
    seen = {}

    def fit(_d, msgs, system, provider, model, *, head):
        seen["fit"] = (len(msgs), head, provider, model)
        return ["FITTED"], "FITTED SYSTEM"

    async def complete(**kw):
        seen["sent"] = (kw["messages"], kw["system"])
        return RouteResult(LLMResponse(text="KEEP"), "p", "m")

    monkeypatch.setattr(CT, "_fit_turn_transcript", fit)
    app.state.platform.router.complete = complete
    asyncio.run(CT.look_first_call(
        d, msgs=[LLMMessage(role="user", content="q")], system="s", provider="pp", model="mm",
        head=1, tools=[], reply="draft",
    ))
    assert seen["fit"] == (3, 1, "pp", "mm"), seen
    assert seen["sent"] == (["FITTED"], "FITTED SYSTEM")


def test_the_settings_words_are_not_everyday_sentences():
    """A setting's label and aliases become phrases the chat listens for.
    The first alias, "look first check", matched "look first, check the
    invoice…" and brought the settings tools into ordinary messages (found
    in review). The request itself must still be heard."""
    from iron_jarvis.settings import schema
    from iron_jarvis.settings.trigger import settings_tools_for

    row = next(r for r in schema.SETTINGS if r.key == "capability_look_first")
    assert "look first check" not in row.aliases
    for everyday in (
        "Look first, check the invoice total, then change the due date",
        "Change the due date after you look first and check the invoice",
        "Before you turn anything on, look first and check with me",
        "Turn on the lookup for the vendor and check the feature list",
    ):
        assert settings_tools_for(everyday, auto_tools=True) == [], everyday
    assert "config_set" in settings_tools_for("turn on the Feature lookup check", auto_tools=True)
