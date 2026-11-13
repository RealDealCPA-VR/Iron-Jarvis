"""Iron Jarvis looks the message up ITSELF, before the model answers (v1.489.0,
``capability_search/route``, the setting "Feature lookup first").

Measured offline on the practice set: one small call that is shown the list of
what Iron Jarvis has and the user's message picked the right entry for 116 of
126 requests (92%); the answering model, left to decide for itself whether to
look and what to search for, reached the right tool far less often and not the
same way twice.

What is pinned here, through the REAL stream and POST lanes with a scripted
model and the real ``registry.invoke``:

* off (the default), nothing changes: no extra call, the same prompt;
* on, the call is made ONCE, on the turn's own provider and model, pinned,
  with no tools, and what it picks joins the FIRST round;
* a change it picked is still a forced, once-only card (also under "Don't
  ask"), and the POST lane, which cannot ask, does not get it;
* low trust, Auto tools off, a coding turn, a short message, the demo model, a
  lookup tool set to Never run, an unreadable answer and a failing call all
  leave the turn exactly as it was;
* the call is billed to the turn.
"""

from __future__ import annotations

import json

import pytest

from iron_jarvis.capability_search import join as J
from iron_jarvis.capability_search import route as RT
from iron_jarvis.capability_search import tool as CT_TOOL
from iron_jarvis.core.config import Config
from iron_jarvis.daemon.app import create_app
from iron_jarvis.providers.adapters.base import LLMResponse
from iron_jarvis.providers.router import RouteResult
from iron_jarvis.settings import schema
from tests.test_chat_approvals_v1187 import _asgi_post, _drive_stream
from tests.test_script_power_lanes_v1473 import Model, _answer, _body, _call, _rows, _text_of

pytestmark = [pytest.mark.usefixtures("no_network"), pytest.mark.timeout(300)]

NAME = CT_TOOL.NAME
#: Worded so the word tables arm nothing for it (seen live: d137).
ASK = "I have a little AI box in the closet of my classroom, could you start using that one too"


class Routing(Model):
    """The scripted model plus the routing call: a ``complete`` whose system
    prompt is the router's answers ``picks`` (as the provider *served*)."""

    def __init__(self, steps, picks=None, *, served="claude-cli", raw=None, fail=None) -> None:
        super().__init__(steps)
        self.picks = picks
        self.served = served
        self.raw = raw
        self.fail = fail
        self.route_calls: list[dict] = []

    async def complete(self, **kw):
        if str(kw.get("system") or "").startswith("You route one message"):
            self.route_calls.append(kw)
            if self.fail is not None:
                raise self.fail
            text = self.raw if self.raw is not None else json.dumps(self.picks or [])
            return RouteResult(
                LLMResponse(text=text, usage={"input_tokens": 400, "output_tokens": 6}), self.served, "haiku")
        return await super().complete(**kw)


@pytest.fixture()
def desk(tmp_path):
    app = create_app(str(tmp_path / "home"))
    ws = tmp_path / "work"
    ws.mkdir()
    cfg = app.state.platform.config
    cfg.capability_search = True
    cfg.capability_route = True
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


# =========================================================================== #
# the setting
# =========================================================================== #
def test_the_setting_is_off_by_default_and_its_label_is_no_sentence():
    assert Config.model_fields["capability_route"].default is False
    row = next(r for r in schema.SETTINGS if r.key == "capability_route")
    assert row.label == "Feature lookup first" and row.tier == "allow"
    assert "approval card" in row.help and "later" in row.help, "the cost and the card are said"
    from iron_jarvis.settings import trigger

    for everyday in ("Look this feature up first, then tell me", "first look up the lookup table for me",
                     "Put the feature list first in the report"):
        assert "config_set" not in trigger.settings_tools_for(everyday, auto_tools=True), everyday


# =========================================================================== #
# the pieces
# =========================================================================== #
def test_the_answer_is_read_as_known_ids_only():
    known = {"a": 1, "b": 2, "c": 3, "d": 4, "e": 5}
    assert RT.parse_ids('["a", "b"]', known) == ["a", "b"]
    assert RT.parse_ids('Here you go:\n["b","zz","b","a"]\nthanks', known) == ["b", "a"]
    assert RT.parse_ids('["a","b","c","d","e"]', known) == ["a", "b", "c", "d"], "at most four"
    assert RT.parse_ids("[]", known) == []
    assert RT.parse_ids("none of them", known) == []
    assert RT.parse_ids('["a", 5, null, {"x": 1}, "b"]', known) == ["a", "b"]  # only the text items
    assert RT.parse_ids('[1, "a"]', known) == ["a"]
    assert RT.parse_ids('["a", ["b"]]', known) == []  # a nested list is not an answer
    assert RT.parse_ids('{"ids": "a"}', known) == []
    assert RT.parse_ids(None, known) == []
    assert RT.parse_ids("[" * 100_000, known) == []  # bounded, never slow


def test_a_few_words_are_needed_in_any_script():
    assert RT.enough_words("add my model server") is True
    assert RT.enough_words("yes please") is False
    assert RT.enough_words("ok") is False
    assert RT.enough_words("Добавь мой сервер моделей") is True
    assert RT.enough_words("") is False


def test_the_list_holds_only_what_a_lookup_could_bring(desk):
    app, _ws = desk
    text, by_id = RT.catalog(app.state.platform)
    lines = text.splitlines()
    assert len(lines) == len(by_id) > 150
    ids = [line.split(":", 1)[0] for line in lines]
    assert len(set(ids)) == len(ids)
    can = J.joinable()
    for entry in by_id.values():
        if entry.kind == "tool":
            assert (entry.tool or entry.id) in can, entry.id
    for never in ("shell", "tool_script", "repl", "run_code", "browser_click", "process_start"):
        assert never not in by_id, never
    for must in ("model_server_add", "schedule_create", "guide_search", "config_list", "default_model"):
        assert must in by_id, must
    # Kept per index: the same object the second time.
    assert RT.catalog(app.state.platform)[1] is by_id


def test_when_a_turn_is_looked_up(desk):
    app, ws = desk
    d = type("D", (), {"platform": app.state.platform})()
    from iron_jarvis.daemon.schemas import ChatBody

    body = ChatBody(**_body(ws, ASK))
    assert RT.wanted(d, body) is True
    assert RT.wanted(d, body, low_trust=True) is False
    assert RT.wanted(d, body, plain_chat=False) is False
    assert RT.wanted(d, ChatBody(**_body(ws, ASK, auto_tools=False))) is False
    assert RT.wanted(d, ChatBody(**_body(ws, "yes please"))) is False
    assert RT.wanted(d, ChatBody(**{**_body(ws, ASK), "turn_id": ""})) is False
    app.state.platform.config.capability_route = False
    assert RT.wanted(d, body) is False
    app.state.platform.config.capability_route = True
    app.state.platform.config.capability_search = False
    assert RT.wanted(d, body) is False, "it needs Feature lookup"


def test_the_section_is_bounded_and_says_it_may_be_ignored():
    text = RT.section(["tool `x` — do x."], "(Now among your tools in this reply: `x`.)")
    assert text.startswith(RT.SECTION_HEAD) and "1. tool `x`" in text
    assert "ignore this list" in text and "Now among your tools" in text
    assert RT.section([], "anything") == ""
    assert len(RT.section(["y" * 5000], "")) <= RT.SECTION_CHARS


# =========================================================================== #
# the stream lane
# =========================================================================== #
@pytest.mark.asyncio
async def test_what_was_looked_up_is_there_in_the_first_round_and_a_change_still_cards(desk):
    app, ws = desk
    model = Routing([
        lambda m: _call(1, "model_server_add", name="Closet box", address="http://192.168.1.20:1234"),
        "Not added.",
    ], picks=["model_server_add", "local_model"])
    frames, cards, done = await _stream(app, _body(ws, ASK, provider="claude-cli", model="haiku"), model,
                                        decision="deny")
    # ONE routing call: the turn's own provider and model, pinned, no tools.
    (call,) = model.route_calls
    assert call["provider"] == "claude-cli" and call["model"] == "haiku"
    assert call["pin"] is True and call["tools"] == []
    assert "model_server_add: " in call["system"]
    said = str(call["messages"][-1].content)
    assert ASK in said and len(call["messages"]) == 1
    # The first round already holds the tool, and the prompt says what was looked up.
    assert "model_server_add" in model.offered[0], model.offered[0]
    assert RT.SECTION_HEAD in model.systems[0]
    assert "shows the user an approval card" in model.systems[0]
    assert "ignore this list" in model.systems[0]
    # A change a lookup brought is a forced, once-only card.
    assert [c["tool"] for c in cards] == ["model_server_add"]
    assert cards[0].get("once_only") is True and cards[0].get("once_reason") == "looked_up"
    assert cards[0].get("can_always") is False
    assert not [r for r in _rows(app, "model_server_add") if r.ok], "denied: nothing was added"
    assert done["reply"].startswith("Not added.")


@pytest.mark.asyncio
async def test_the_card_is_forced_even_under_dont_ask(desk):
    app, ws = desk
    model = Routing([
        lambda m: _call(1, "model_server_add", name="Closet box", address="http://192.168.1.20:1234"),
        "Not added.",
    ], picks=["model_server_add"])
    _frames, cards, _done = await _stream(app, _body(ws, ASK, approval_mode="yolo"), model, decision="deny")
    assert [c["tool"] for c in cards] == ["model_server_add"], "Don't ask must not skip a looked-up change"


@pytest.mark.asyncio
async def test_a_reader_it_picked_joins_granted(desk):
    app, ws = desk
    model = Routing([
        lambda m: _call(1, "history_search", query="invoice"),
        "Nothing found.",
    ], picks=["history_search"])
    _frames, cards, _done = await _stream(app, _body(ws, "find what we said about the invoices earlier"), model)
    assert "history_search" in model.offered[0]
    assert cards == [], "a reader on this PC needs no card"
    assert len([r for r in _rows(app, "history_search") if r.ok]) == 1


@pytest.mark.asyncio
async def test_off_makes_no_call_and_leaves_the_prompt_alone(desk):
    app, ws = desk
    on = Routing(["Hello."], picks=[])
    await _stream(app, _body(ws, ASK), on)
    assert len(on.route_calls) == 1
    app.state.platform.config.capability_route = False
    off = Routing(["Hello."], picks=["model_server_add"])
    await _stream(app, _body(ws, ASK), off)
    assert off.route_calls == []
    assert RT.SECTION_HEAD not in off.systems[0]
    assert "model_server_add" not in off.offered[0]
    # Nothing picked: the same prompt and tools as with the setting off.
    assert on.systems[0] == off.systems[0] and on.offered[0] == off.offered[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["low_trust", "auto_off", "short", "no_turn"])
async def test_turns_that_are_never_looked_up(desk, change):
    app, ws = desk
    model = Routing(["Hello."], picks=["model_server_add"])
    body = _body(ws, ASK)
    router = app.state.platform.router
    router.stream = model.stream
    router.complete = model.complete
    if change == "auto_off":
        body["auto_tools"] = False
    elif change == "short":
        body["messages"][0]["content"] = "yes please"
    elif change == "no_turn":
        body["turn_id"] = ""
    if change == "low_trust":
        from iron_jarvis.daemon.chat_turn import run_chat_turn
        from iron_jarvis.daemon.schemas import ChatBody

        await run_chat_turn(app.state.platform, {}, ChatBody(**body), trust="low")
    else:
        await _drive_stream(app, body, _answer(app, []))
    assert model.route_calls == [], change
    assert all(RT.SECTION_HEAD not in s for s in model.systems)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["demo", "garbage", "raises", "unknown_ids"])
async def test_an_answer_that_cannot_be_used_changes_nothing(desk, kind):
    app, ws = desk
    kw: dict = {"picks": ["model_server_add"]}
    if kind == "demo":
        kw["served"] = "mock"
    elif kind == "garbage":
        kw["raw"] = "I think the user wants a model server."
    elif kind == "raises":
        kw["fail"] = RuntimeError("provider down")
    elif kind == "unknown_ids":
        kw["picks"] = ["not_a_real_entry", "another_one"]
    model = Routing(["Hello."], **kw)
    _frames, cards, done = await _stream(app, _body(ws, ASK), model)
    assert len(model.route_calls) == 1
    assert RT.SECTION_HEAD not in model.systems[0]
    assert "model_server_add" not in model.offered[0]
    assert cards == [] and done["reply"] == "Hello."


@pytest.mark.asyncio
async def test_a_lookup_tool_set_to_never_run_brings_nothing(desk):
    app, ws = desk
    p = app.state.platform
    before = dict(p.permissions._base)
    p.permissions._base[NAME] = "deny"
    try:
        model = Routing(["Hello."], picks=["model_server_add"])
        await _stream(app, _body(ws, ASK), model)
    finally:
        p.permissions._base.clear()
        p.permissions._base.update(before)
    assert NAME not in model.offered[0]
    assert "model_server_add" not in model.offered[0]
    assert RT.SECTION_HEAD not in model.systems[0]


@pytest.mark.asyncio
async def test_a_tool_set_to_never_run_does_not_join_and_the_section_says_so(desk):
    app, ws = desk
    p = app.state.platform
    before = dict(p.permissions._base)
    p.permissions._base["model_server_add"] = "deny"
    try:
        model = Routing(["Hello."], picks=["model_server_add"])
        await _stream(app, _body(ws, ASK), model)
    finally:
        p.permissions._base.clear()
        p.permissions._base.update(before)
    assert "model_server_add" not in model.offered[0]
    assert "`model_server_add` did not join" in model.systems[0]


@pytest.mark.asyncio
async def test_the_routing_call_is_billed_to_the_turn(desk):
    app, ws = desk

    class Billed(Routing):
        async def stream(self, **kw):
            async for frame in super().stream(**kw):
                if frame.get("type") == "final":
                    frame["response"].usage = {"input_tokens": 100, "output_tokens": 10}
                yield frame

    model = Billed(["An answer."], picks=[])
    _frames, _cards, done = await _stream(app, _body(ws, ASK), model)
    usage = done.get("usage") or {}
    assert usage.get("input_tokens") == 500 and usage.get("output_tokens") == 16, usage


@pytest.mark.asyncio
async def test_it_uses_one_of_the_replys_two_joining_lookups(desk):
    app, ws = desk
    model = Routing([
        lambda m: _call(1, NAME, query="search earlier chats history"),
        lambda m: _call(2, NAME, query="make a schedule"),
        "Done.",
    ], picks=["model_server_add"])
    await _stream(app, _body(ws, ASK), model, decision="deny")
    assert "model_server_add" in model.offered[0]
    assert "history_search" in model.offered[1], "the model's own first lookup still joins"
    assert "schedule_create" not in model.offered[2], "the third lookup of the reply brings nothing"
    assert "No more tools join" in " ".join(model.tool_texts[2]) or "no more" in " ".join(model.tool_texts[2]).lower()


# =========================================================================== #
# the POST lane (the phone, an API caller): it cannot ask
# =========================================================================== #
@pytest.mark.asyncio
async def test_the_post_lane_gets_readers_only(desk):
    app, ws = desk
    model = Routing(["Hello."], picks=["model_server_add", "history_search"])
    out = await _post(app, _body(ws, ASK), model)
    assert out["reply"] == "Hello."
    assert len(model.route_calls) == 1
    assert "history_search" in model.offered[0]
    assert "model_server_add" not in model.offered[0], "a change cannot join where nobody can be asked"
    assert RT.SECTION_HEAD in model.systems[0]
    assert "`model_server_add` did not join" in model.systems[0]


@pytest.mark.asyncio
async def test_the_post_lane_is_untouched_with_the_setting_off(desk):
    app, ws = desk
    app.state.platform.config.capability_route = False
    model = Routing(["Hello."], picks=["history_search"])
    await _post(app, _body(ws, ASK), model)
    assert model.route_calls == []
    assert RT.SECTION_HEAD not in model.systems[0]


# =========================================================================== #
# A slow routing call never holds up the reply: its picks join later
# =========================================================================== #
class Slow(Routing):
    """A routing call that answers only once a scripted step opens the gate,
    the way a `claude` process per call behaves."""

    def __init__(self, steps, picks=None, **kw) -> None:
        super().__init__(steps, picks, **kw)
        import asyncio

        self.gate = asyncio.Event()
        self.answered = False
        self.pending_at_round: list[bool] = []
        self.asked_with: list[str] = []
        self.user_texts: list[list[str]] = []

    def _see(self, system, messages, tools) -> None:
        super()._see(system, messages, tools)
        self.pending_at_round.append(not self.answered)
        self.user_texts.append([c for r, c in map(_text_of, messages or []) if r == "user"])

    async def complete(self, **kw):
        if str(kw.get("system") or "").startswith("You route one message"):
            self.route_calls.append(kw)
            await self.gate.wait()
            self.answered = True
            return RouteResult(
                LLMResponse(text=json.dumps(self.picks or []), usage={"input_tokens": 400, "output_tokens": 6}),
                self.served, "haiku")
        self.asked_with.append(str(kw["messages"][-1].content))
        return await Model.complete(self, **kw)


@pytest.fixture()
def quick_head_start(monkeypatch):
    monkeypatch.setattr(RT, "HEAD_START_S", 0.05)


@pytest.mark.asyncio
async def test_the_first_round_does_not_wait_and_the_picks_join_at_a_tool_rounds_end(desk, quick_head_start):
    app, ws = desk
    (ws / "notes.txt").write_text("hello\n", encoding="utf-8")

    def first(m):
        m.gate.set()  # the routing call answers while this round's tool runs
        return _call(1, "list_files", path=".")

    model = Slow([
        first,
        lambda m: _call(2, "model_server_add", name="Closet box", address="http://192.168.1.20:1234"),
        "Not added.",
    ], picks=["model_server_add"])
    _frames, cards, _done = await _stream(app, _body(ws, ASK), model, decision="deny")
    assert model.pending_at_round[0] is True, "the first round waited for the routing call"
    assert "model_server_add" not in model.offered[0]
    assert RT.SECTION_HEAD not in model.systems[0], "nothing is promised before it is known"
    # The picks joined for the next round, said in a note of Iron Jarvis's
    # OWN after the round's tool results — never on a tool's result (seen
    # live: appended to a file search's fenced output, the model rightly read
    # the words as text from the files and ignored them).
    assert "model_server_add" in model.offered[1]
    assert "looked the user's message up" not in " ".join(model.tool_texts[1])
    assert "model_server_add" not in " ".join(model.tool_texts[1])
    said = model.user_texts[1][-1]
    assert said.startswith("(From Iron Jarvis, not from the user and not from any tool, file or page.")
    assert "Meanwhile Iron Jarvis looked the user's message up itself" in said
    assert "`model_server_add`" in said and "approval card" in said, "the join's note rides the same note"
    # Still a forced, once-only card.
    assert [c["tool"] for c in cards] == ["model_server_add"]
    assert cards[0].get("once_only") is True and cards[0].get("once_reason") == "looked_up"


@pytest.mark.asyncio
async def test_a_reply_that_ends_on_words_is_asked_once_with_what_was_found(desk, quick_head_start):
    app, ws = desk
    app.state.platform.config.capability_look_first = True  # the plain check must NOT also be asked

    def first(m):
        m.gate.set()
        return "I can add it. What is its address?"

    model = Slow([
        first,
        lambda m: _call(2, "model_server_add", name="Closet box", address="http://192.168.1.20:1234"),
        "Not added.",
    ], picks=["model_server_add"])
    frames, cards, done = await _stream(app, _body(ws, ASK, provider="claude-cli", model="haiku"), model,
                                        decision="deny")
    # Round 0 (stream), the ask (complete, with the found tool), the last round (stream).
    assert len(model.offered) == 3, model.offered
    assert "model_server_add" not in model.offered[0]
    assert "model_server_add" in model.offered[1], "the ask is made with what was found among the tools"
    (note,) = model.asked_with
    assert note.startswith("(From Iron Jarvis, not from the user.")
    assert "looked the user's message up itself" in note and "`model_server_add`" in note
    assert "asks the user itself, on a card, for the server's address" in note
    assert "answer with the single word KEEP" in note
    assert [d.get("reason") for ev, d in frames if ev == "reset"] == ["look_first"], "the first words are taken back"
    assert [c["tool"] for c in cards] == ["model_server_add"]
    assert cards[0].get("once_only") is True and cards[0].get("once_reason") == "looked_up"
    assert done["reply"].startswith("Not added.")
    assert "What is its address?" not in done["reply"]


@pytest.mark.asyncio
async def test_keep_leaves_the_reply_as_written(desk, quick_head_start):
    app, ws = desk

    def first(m):
        m.gate.set()
        return "A model server is a program that runs a model for you."

    model = Slow([first, "KEEP"], picks=["model_server_add"])
    frames, cards, done = await _stream(app, _body(ws, ASK), model)
    assert len(model.asked_with) == 1
    assert done["reply"] == "A model server is a program that runs a model for you."
    assert [ev for ev, _ in frames if ev == "reset"] == [], "a kept reply is not taken back"
    assert cards == [] and "KEEP" not in json.dumps(done)


@pytest.mark.asyncio
async def test_nothing_found_means_no_ask_and_no_second_check(desk, quick_head_start):
    app, ws = desk
    app.state.platform.config.capability_look_first = True

    def first(m):
        m.gate.set()
        return "A trial balance lists every account with its balance."

    model = Slow([first], picks=[])
    frames, _cards, done = await _stream(app, _body(ws, "what is a trial balance, in a few words?"), model)
    assert model.asked_with == [], "Iron Jarvis looked and found nothing: the model is not asked again"
    assert done["reply"] == "A trial balance lists every account with its balance."
    assert [ev for ev, _ in frames if ev == "reset"] == []


@pytest.mark.asyncio
async def test_a_reply_that_changed_something_is_left_alone(desk, quick_head_start):
    app, ws = desk

    def second(m):
        m.gate.set()
        return "Saved note.txt."

    model = Slow([
        lambda m: _call(1, "write_file", path="note.txt", content="hi"),
        second,
    ], picks=["model_server_add"])
    body = _body(ws, "save a note called note.txt that says hi, in this folder please")
    frames, _cards, done = await _stream(app, body, model)
    assert done["reply"].startswith("Saved note.txt.")
    assert model.asked_with == [], "a reply that made its change is not asked again"
    assert [ev for ev, _ in frames if ev == "reset"] == []


@pytest.mark.asyncio
async def test_the_late_call_is_billed_once(desk, quick_head_start):
    app, ws = desk

    class Billed(Slow):
        async def stream(self, **kw):
            async for frame in super().stream(**kw):
                if frame.get("type") == "final":
                    frame["response"].usage = {"input_tokens": 100, "output_tokens": 10}
                yield frame

    def first(m):
        m.gate.set()
        return "Hello there."

    model = Billed([first], picks=[])
    _frames, _cards, done = await _stream(app, _body(ws, ASK), model)
    usage = done.get("usage") or {}
    assert usage.get("input_tokens") == 500 and usage.get("output_tokens") == 16, usage


@pytest.mark.asyncio
async def test_the_post_lane_asks_once_with_what_was_found(desk, quick_head_start):
    app, ws = desk

    def first(m):
        m.gate.set()
        return "I could search your earlier chats."

    model = Slow([
        first,
        lambda m: _call(2, "history_search", query="invoice"),
        "Nothing found.",
    ], picks=["history_search", "model_server_add"])
    out = await _post(app, _body(ws, "find what we said about the invoices in our earlier chats"), model)
    assert out["reply"].startswith("Nothing found.")
    assert "history_search" in model.offered[1]
    assert "model_server_add" not in model.offered[1], "this lane cannot ask, so a change does not join"
    assert len([r for r in _rows(app, "history_search") if r.ok]) == 1


@pytest.mark.asyncio
async def test_a_reply_that_looked_for_itself_is_still_asked_with_what_iron_jarvis_found(desk, quick_head_start):
    # The model's own lookup found other things and it then ended on words:
    # what Iron Jarvis found is still put to it (the plain check would not
    # ask a reply that already looked).
    app, ws = desk

    def second(m):
        m.gate.set()
        return "I could not find a way to do that."

    model = Slow([
        lambda m: _call(1, NAME, query="zebra crossing paint"),
        second,
        lambda m: _call(3, "model_server_add", name="Closet box", address="http://192.168.1.20:1234"),
        "Not added.",
    ], picks=["model_server_add"])
    _frames, cards, done = await _stream(app, _body(ws, ASK), model, decision="deny")
    assert len(model.asked_with) == 1, "what Iron Jarvis found was not put to the reply"
    assert [c["tool"] for c in cards] == ["model_server_add"]
    assert done["reply"].startswith("Not added.")


def test_the_routing_words_say_which_kind_of_thing_a_request_is():
    """Each seen live on the practice set: "remember what is in my Shop Notes
    folder" was routed to a single saved note (never the memory base), and a
    file in Downloads to the browser's download features."""
    text = RT.ROUTE_SYSTEM
    for words in (
        "is a memory base and not a single saved note",
        "together is a project",
        "where a button is or how the app works is the guide",
        "a file in the user's Downloads folder is a file like any other",
        "is a schedule",
        "is a goal",
    ):
        assert words in " ".join(text.split()), words
    # The list is put in with str.format: exactly one field, and no stray brace.
    assert text.count("{list}") == 1 and text.replace("{list}", "").count("{") == 0
