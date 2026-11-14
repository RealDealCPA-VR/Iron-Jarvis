# ruff: noqa: F811 — the fixtures are imported from the routing tests and named as arguments.
"""The second look (v1.490.2).

v1.490.1 held back every tool that asks first when the routing call answered
"nothing asked". Measured on the owner's own model server, that call answers
an empty list for 4 to 11 of 138 REAL requests a run, so a request such as
"Don't open yourself every time I switch the computer on" got an offer in
words where it should have got its card.

Now, when the routing call answered an EMPTY list and the model's own lookup
is about to bring a tool that asks first, the routing question is asked once
more with only the entries that lookup found. What it picks there may join
(ask first, as always). An answer marked "unasked", an empty answer, a failure
or a timeout keeps the tools held, as in v1.490.1.
"""
from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace

import pytest

from iron_jarvis.capability_search import join as J
from iron_jarvis.capability_search import route as RT
from iron_jarvis.daemon import chat_turn as CT
from iron_jarvis.providers.adapters.base import LLMResponse
from iron_jarvis.providers.router import RouteResult
from tests.test_route_first_v1489 import (  # noqa: F401 — fixtures
    ASK,
    NAME,
    Routing,
    _body,
    _call,
    _stream,
    desk,
)

pytestmark = [pytest.mark.usefixtures("no_network"), pytest.mark.timeout(300)]

LOOKUP = "add my own model server"
SERVER = dict(name="Closet box", address="http://192.168.1.20:1234")


class Looks(Routing):
    """A routing call that answers each look in turn: ``answers[0]`` for the
    whole list, ``answers[1]`` for the second look (the last one repeats). An
    exception in the list is raised."""

    def __init__(self, steps, answers, **kw) -> None:
        super().__init__(steps, **kw)
        self.answers = list(answers)

    async def complete(self, **kw):
        if str(kw.get("system") or "").startswith("You route one message"):
            self.route_calls.append(kw)
            answer = self.answers[min(len(self.route_calls), len(self.answers)) - 1]
            if isinstance(answer, BaseException):
                raise answer
            text = answer if isinstance(answer, str) else json.dumps(answer)
            return RouteResult(
                LLMResponse(text=text, usage={"input_tokens": 400, "output_tokens": 6}), self.served, "haiku")
        return await super().complete(**kw)


def _steps():
    return [
        lambda m: _call(1, NAME, query=LOOKUP),
        lambda m: _call(2, "model_server_add", **SERVER),
        "Just words then.",
    ]


def _listed(system: str) -> list[str]:
    """The ids in the list a routing call was shown."""
    body = system.split("\n\n")[2] if system.count("\n\n") >= 2 else ""
    return [line.split(":", 1)[0] for line in body.splitlines() if ":" in line]


# --------------------------------------------------------------------------- #
# through the real stream lane
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_request_the_first_look_missed_gets_its_card_after_the_second(desk):
    app, ws = desk
    model = Looks(_steps(), [[], ["model_server_add"]])
    _frames, cards, _done = await _stream(app, _body(ws, ASK), model, decision="deny")
    assert len(model.route_calls) == 2
    assert "model_server_add" in model.offered[1], "the request stayed held after the second look said it was asked"
    assert [c["tool"] for c in cards] == ["model_server_add"]
    # A looked-up change keeps every rule it had: a forced once-only card.
    assert cards[0].get("once_only") is True and cards[0].get("once_reason") == "looked_up"


@pytest.mark.asyncio
async def test_the_second_look_is_shown_only_what_the_lookup_found(desk):
    app, ws = desk
    model = Looks(_steps(), [[], []])
    await _stream(app, _body(ws, ASK), model, decision="once")
    assert len(model.route_calls) == 2
    whole, short = (_listed(c["system"]) for c in model.route_calls)
    assert len(whole) > 100
    assert 0 < len(short) <= J.SECOND_LOOK_IDS
    assert "model_server_add" in short and set(short) <= set(whole)
    # The same question, the same message: only the list is shorter.
    assert model.route_calls[1]["messages"][0].content == model.route_calls[0]["messages"][0].content


@pytest.mark.asyncio
@pytest.mark.parametrize("second", [
    [],                                   # nothing asked, again
    ["unasked", "model_server_add"],      # a remark
    "I am not sure.",                     # unreadable
    RuntimeError("the model server is down"),
    ["guide_search"],                     # it picked something else
])
async def test_anything_but_a_plain_pick_keeps_the_tool_held(desk, second):
    app, ws = desk
    model = Looks(_steps(), [[], second])
    _frames, cards, done = await _stream(app, _body(ws, ASK), model, decision="once")
    assert len(model.route_calls) == 2
    assert "model_server_add" not in model.offered[1], second
    assert cards == [], cards
    assert "does not ask Iron Jarvis to do this" in " ".join(model.tool_texts[1])
    assert done["reply"].startswith("Just words then.")


@pytest.mark.asyncio
async def test_the_second_look_lets_through_only_what_it_named(desk):
    """It named ANOTHER entry of the short list: the tool it did not name
    stays held, though something was asked for."""
    app, ws = desk
    first = Looks(_steps(), [[], []])
    await _stream(app, _body(ws, ASK), first, decision="once")
    short = _listed(first.route_calls[1]["system"])
    others = [i for i in short if not i.startswith("model_server")]
    assert others, short
    J.FOUND.clear()
    model = Looks(_steps(), [[], [others[0]]])
    _frames, cards, _done = await _stream(app, _body(ws, ASK), model, decision="once")
    assert len(model.route_calls) == 2
    assert "model_server_add" not in model.offered[1], others[0]
    assert [c["tool"] for c in cards if c["tool"] == "model_server_add"] == []


@pytest.mark.asyncio
async def test_no_second_look_after_a_remark_or_a_pick(desk):
    """The routing call read a remark ("unasked"), or picked something: the
    first answer stands and no second call is made."""
    app, ws = desk
    remark = Looks(_steps(), [["unasked", "model_server_add"], ["model_server_add"]])
    _f, cards, _d = await _stream(app, _body(ws, ASK), remark, decision="once")
    assert len(remark.route_calls) == 1 and cards == []
    J.FOUND.clear()
    picked = Looks(_steps(), [["guide_search"], []])
    _f, cards, _d = await _stream(app, _body(ws, ASK), picked, decision="deny")
    assert len(picked.route_calls) == 1
    assert [c["tool"] for c in cards] == ["model_server_add"]


@pytest.mark.asyncio
async def test_no_second_look_when_the_model_looked_nothing_up(desk):
    """An ordinary reply (no lookup of the model's own) costs one routing
    call, as before."""
    app, ws = desk
    model = Looks(["Just an answer."], [[], ["model_server_add"]])
    await _stream(app, _body(ws, ASK), model)
    assert len(model.route_calls) == 1


@pytest.mark.asyncio
async def test_the_second_look_is_billed_to_the_turn(desk):
    """The same reply twice: once the first answer was a remark (no second
    look), once an empty list (a second look). The difference in what the
    turn is billed is exactly the second look's own call."""
    app, ws = desk
    one = Looks(_steps(), [["unasked"]])
    _f, _c, without = await _stream(app, _body(ws, ASK), one, decision="once")
    J.FOUND.clear()
    two = Looks(_steps(), [[], []])
    _f, _c, done = await _stream(app, _body(ws, ASK), two, decision="once")
    assert (len(one.route_calls), len(two.route_calls)) == (1, 2)
    assert done["usage"]["input_tokens"] - without["usage"]["input_tokens"] == 400
    assert done["usage"]["output_tokens"] - without["usage"]["output_tokens"] == 6


# --------------------------------------------------------------------------- #
# the pieces
# --------------------------------------------------------------------------- #
def test_the_short_list_keeps_the_lists_own_order_and_drops_unknown_ids():
    text = "a: one\nb: two\nc: three"
    by_id = {"a": 1, "b": 2, "c": 3}
    assert RT.short_list(text, by_id, ["c", "a", "zzz"]) == ("a: one\nc: three", {"a": 1, "c": 3})
    assert RT.short_list(text, by_id, []) == ("", {})


def _route(first, again):
    async def answer():
        return first

    task = asyncio.ensure_future(answer())
    route = CT.RouteFirst(task)
    route.again = again
    return route, task


@pytest.mark.asyncio
async def test_a_stop_ends_the_second_look(monkeypatch):
    monkeypatch.setattr(CT, "SECOND_LOOK_WAIT_S", 30.0)

    async def slow(_only):
        await asyncio.sleep(30)

    route, task = _route(RT.Routed(entries=[], nothing=True), slow)
    await task
    stopped = asyncio.Event()
    asyncio.get_running_loop().call_later(0.1, stopped.set)
    started = time.perf_counter()
    assert await CT._second_look(route, ["goal_add"], stopped.wait) == set()
    assert time.perf_counter() - started < 5.0, "a Stop waited for the second look"


@pytest.mark.asyncio
async def test_a_second_look_that_does_not_come_in_time_asks_for_nothing(monkeypatch):
    monkeypatch.setattr(CT, "SECOND_LOOK_WAIT_S", 0.05)
    seen = {}

    async def slow(_only):
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            seen["cancelled"] = True
            raise

    route, task = _route(RT.Routed(entries=[], nothing=True), slow)
    await task
    assert await CT._second_look(route, ["goal_add"], None) == set()
    await asyncio.sleep(0)
    assert seen.get("cancelled") is True, "the late call was left running"


@pytest.mark.asyncio
async def test_the_second_look_answers_with_the_entries_it_picked_and_owes_its_usage():
    asked_with = []

    async def again(only):
        asked_with.append(list(only))
        return RT.Routed(entries=[SimpleNamespace(id="goal_add")], provider="p", model="m",
                         usage={"input_tokens": 9, "output_tokens": 2})

    route, task = _route(RT.Routed(entries=[], nothing=True), again)
    await task
    assert await CT._second_look(route, ["goal_add", "config_list"], None) == {"goal_add"}
    assert asked_with == [["goal_add", "config_list"]]
    added = []
    tally = SimpleNamespace(add=lambda *a: added.append(a))
    assert route.take_owed(tally) == (9, 2, 1)
    assert added == [("p", "m", {"input_tokens": 9, "output_tokens": 2})]
    assert route.take_owed(tally) == (0, 0, 0), "billed twice"


def test_only_the_entries_the_second_look_named_are_let_through():
    J.FOUND.clear()
    try:
        key = "chat:t-asked"
        J.FOUND.note(key, [{"kind": "tool", "id": "goal_add", "tool": "goal_add"},
                           {"kind": "tool", "id": "schedule_create", "tool": "schedule_create"}])
        assert J.FOUND.pending_ids(key) == ["goal_add", "schedule_create"]
        J.FOUND.mark_not_asked(key, asked_ids={"goal_add"})
        assert J.FOUND.not_asked(key) is True and J.FOUND.asked_ids(key) == frozenset({"goal_add"})
        # ...and with no second look, nothing is let through.
        J.FOUND.mark_not_asked(key)
        assert J.FOUND.asked_ids(key) == frozenset()
    finally:
        J.FOUND.clear()
