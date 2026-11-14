"""Two readings must agree before a card (v1.490.1).

Seen live with the three Feature lookup settings on by default, on the
owner's own model server (GLM 5.3 Flash) and on a FRESH set of look-alike
messages: Iron Jarvis's own routing call read "my goal this year is to get my
Year 10s to stop guessing at units" and answered that it needs none of Iron
Jarvis's features, and the model then looked `goal_add` up BY ITSELF and put
up its approval card. The routing call was right in every such case.

The rule: when the routing call ANSWERED "nothing", a tool that asks first (a
changer, or a reader that reaches outside this PC) does not join from the
model's own lookup. The model is told why and may offer it in words. Nothing
changes when the routing call picked something, failed, could not be read,
is off, or was never made.
"""
# ruff: noqa: F811 — the fixtures are imported from the routing tests and named as arguments.
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

from iron_jarvis.capability_search import join as J
from iron_jarvis.capability_search import route as RT
from iron_jarvis.daemon import chat_turn as CT
from tests.test_route_first_v1489 import (  # noqa: F401 — fixtures
    ASK,
    NAME,
    Routing,
    Slow,
    _body,
    _call,
    _stream,
    desk,
    quick_head_start,
)

pytestmark = [pytest.mark.usefixtures("no_network"), pytest.mark.timeout(300)]

LOOKUP = "add my own model server"
SERVER = dict(name="Closet box", address="http://192.168.1.20:1234")


def _steps():
    return [
        lambda m: _call(1, NAME, query=LOOKUP),
        lambda m: _call(2, "model_server_add", **SERVER),
        "Just words then.",
    ]


# --------------------------------------------------------------------------- #
# the rule, through the real stream lane
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_change_the_model_looked_up_does_not_join_when_the_message_asked_for_nothing(desk):
    app, ws = desk
    model = Routing(_steps(), picks=[])
    _frames, cards, done = await _stream(app, _body(ws, ASK), model, decision="once")
    assert len(model.route_calls) == 1
    assert "model_server_add" not in model.offered[1], "a card could be put up for a message that asked for nothing"
    assert cards == [], cards
    said = " ".join(model.tool_texts[1])
    assert "`model_server_add` did not join" in said
    assert "does not ask Iron Jarvis to do this" in said and "offer it" in said
    assert "look once more" not in said, "the model is not sent looking again for something nobody asked for"
    assert done["reply"].startswith("Just words then.")


@pytest.mark.asyncio
async def test_a_reader_that_stays_on_this_pc_still_joins(desk):
    """Only tools that would ASK are held back: reading the guide, a setting
    or an earlier chat needs no card and changes nothing."""
    app, ws = desk
    model = Routing([lambda m: _call(1, NAME, query="search the Iron Jarvis guide"), "Words."], picks=[])
    _frames, cards, _done = await _stream(app, _body(ws, ASK), model)
    assert "guide_search" in model.offered[1]
    assert cards == []


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["picked", "unreadable", "failed", "off"])
async def test_everything_else_is_as_before(desk, kind):
    """The routing call picked something (the user asked for something), could
    not be read, failed, or the setting is off: the model's own lookup brings
    the changer as an ask-first tool, with its forced card, exactly as in
    v1.489.0."""
    app, ws = desk
    if kind == "picked":
        model = Routing(_steps(), picks=["guide_search"])
    elif kind == "unreadable":
        model = Routing(_steps(), raw="I think it needs nothing at all.")
    elif kind == "failed":
        model = Routing(_steps(), fail=RuntimeError("the model server is down"))
    else:
        app.state.platform.config.capability_route = False
        model = Routing(_steps(), picks=[])
    _frames, cards, _done = await _stream(app, _body(ws, ASK), model, decision="deny")
    assert "model_server_add" in model.offered[1], kind
    assert [c["tool"] for c in cards] == ["model_server_add"], kind
    assert cards[0].get("once_only") is True and cards[0].get("once_reason") == "looked_up"
    if kind == "off":
        assert model.route_calls == []


@pytest.mark.asyncio
async def test_the_rounds_end_waits_for_a_verdict_that_is_still_coming(desk, quick_head_start, monkeypatch):
    """The routing call is slower than the model's first round: the join
    waits for it (bounded), so the slow answer still decides."""
    app, ws = desk
    monkeypatch.setattr(CT, "ROUTE_VERDICT_WAIT_S", 20.0)

    def first(m):
        asyncio.get_running_loop().call_later(0.3, m.gate.set)
        return _call(1, NAME, query=LOOKUP)

    model = Slow([first, lambda m: _call(2, "model_server_add", **SERVER), "Just words then."], picks=[])
    _frames, cards, _done = await _stream(app, _body(ws, ASK), model, decision="once")
    assert model.pending_at_round[0] is True, "the routing call answered before the first round"
    assert "model_server_add" not in model.offered[1]
    assert cards == []


@pytest.mark.asyncio
async def test_a_verdict_that_does_not_come_in_time_changes_nothing(desk, quick_head_start, monkeypatch):
    app, ws = desk
    monkeypatch.setattr(CT, "ROUTE_VERDICT_WAIT_S", 0.05)

    def second(m):
        m.gate.set()
        return _call(2, "model_server_add", **SERVER)

    model = Slow([lambda m: _call(1, NAME, query=LOOKUP), second, "Not added."], picks=[])
    _frames, cards, _done = await _stream(app, _body(ws, ASK), model, decision="deny")
    assert "model_server_add" in model.offered[1], "an unknown verdict held a tool back"
    assert [c["tool"] for c in cards] == ["model_server_add"]


# --------------------------------------------------------------------------- #
# "unasked": the routing call picked features the message only touches on
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_pick_marked_unasked_joins_nothing_and_the_model_is_told_nothing(desk):
    """Seen live on the owner's model server: "my eyes are not what they
    were…" was read as text size, the setting's tool joined, and the model
    put up its card. With the mark, the reply is what it would have been."""
    app, ws = desk
    plain = Routing(["Just an answer."], picks=[])
    _f, _c, plain_done = await _stream(app, _body(ws, ASK), plain)
    J.FOUND.clear()

    model = Routing(["Just an answer."], picks=[RT.UNASKED, "model_server_add", "guide_search"])
    _frames, cards, done = await _stream(app, _body(ws, ASK), model, decision="once")
    assert len(model.route_calls) == 1
    assert "model_server_add" not in model.offered[0] and "guide_search" not in model.offered[0]
    assert RT.SECTION_HEAD not in model.systems[0]
    assert model.systems[0] == plain.systems[0] and model.offered[0] == plain.offered[0]
    assert cards == []
    assert len(model.systems) == 1, "the model was asked again about something nobody asked for"
    assert done["reply"] == plain_done["reply"]


@pytest.mark.asyncio
async def test_after_an_unasked_pick_the_models_own_lookup_brings_no_change(desk):
    app, ws = desk
    model = Routing(_steps(), picks=[RT.UNASKED, "model_server_add"])
    _frames, cards, _done = await _stream(app, _body(ws, ASK), model, decision="once")
    assert "model_server_add" not in model.offered[1]
    assert cards == [], cards
    assert "does not ask Iron Jarvis to do this" in " ".join(model.tool_texts[1])


@pytest.mark.asyncio
async def test_the_same_pick_without_the_mark_joins_as_before(desk):
    app, ws = desk
    model = Routing([lambda m: _call(1, "model_server_add", **SERVER), "Not added."], picks=["model_server_add"])
    _frames, cards, _done = await _stream(app, _body(ws, ASK), model, decision="deny")
    assert "model_server_add" in model.offered[0]
    assert RT.SECTION_HEAD in model.systems[0]
    assert [c["tool"] for c in cards] == ["model_server_add"]


def test_the_mark_is_read_only_from_a_readable_list():
    for text in ('["unasked"]', '["unasked", "goal_add"]', 'Answer: ["Unasked","goal_add"]', '[" unasked ", "x"]'):
        assert RT.said_unasked(text) is True, text
    for text in ("", "[]", '["goal_add"]', "unasked", '["unasked"', '["not unasked"]', None, 7, '[["unasked"]]'):
        assert RT.said_unasked(text) is False, text
    # The mark is never an id, and a list holding only the mark is not "nothing was answered".
    assert RT.parse_ids('["unasked", "goal_add"]', {"goal_add": 1}) == ["goal_add"]
    assert RT.answered_nothing('["unasked"]') is False


def test_the_routing_call_is_told_about_the_mark_and_keeps_one_field():
    system = RT.ROUTE_SYSTEM
    assert '"unasked" first in the list' in system
    assert "asks the assistant for nothing" in system
    assert system.count("{") == 1 and system.count("}") == 1 and "{list}" in system


# --------------------------------------------------------------------------- #
# the pieces
# --------------------------------------------------------------------------- #
def test_only_a_readable_empty_list_is_nothing():
    for text in ("[]", " [ ] ", "Answer: []", "```json\n[]\n```"):
        assert RT.answered_nothing(text) is True, text
    for text in ("", "none", "I think nothing fits.", '["goal_add"]', '["not_a_known_id"]', "[", "[oops]", None, 7):
        assert RT.answered_nothing(text) is False, text


class _Route:
    def __init__(self, task) -> None:
        self.task = task

    nothing_asked = CT.RouteFirst.nothing_asked


def _ctx(turn="t-settle"):
    return SimpleNamespace(session_id="chat", turn_id=turn)


@pytest.mark.asyncio
async def test_the_verdict_is_read_whether_or_not_the_answer_was_used():
    async def answer(routed):
        return routed

    for routed, want in (
        (RT.Routed(entries=[], nothing=True), True),
        (RT.Routed(entries=[], nothing=False), None),  # unreadable: not known
        (RT.Routed(entries=[object()], nothing=False), False),
        (RT.Routed(entries=[object()], unasked=True), True),  # picked, but marked "unasked"
        (RT.Routed(entries=[], unasked=True), True),
        (None, None),
    ):
        task = asyncio.ensure_future(answer(routed))
        await task
        route = CT.RouteFirst(task)
        route.used = True
        assert route.nothing_asked() is want, routed
    pending = asyncio.ensure_future(asyncio.sleep(30))
    try:
        assert CT.RouteFirst(pending).nothing_asked() is None
    finally:
        pending.cancel()


@pytest.mark.asyncio
async def test_nothing_waits_when_no_lookup_is_about_to_join(monkeypatch):
    """A round with no lookup of the model's own never waits for the routing
    call (it would slow every ordinary tool round)."""
    monkeypatch.setattr(CT, "ROUTE_VERDICT_WAIT_S", 30.0)
    J.FOUND.clear()
    pending = asyncio.ensure_future(asyncio.sleep(30))
    try:
        started = time.perf_counter()
        await CT.settle_route_for_join(_Route(pending), _ctx())
        assert time.perf_counter() - started < 2.0
        assert J.FOUND.not_asked(J.turn_key(_ctx())) is False
    finally:
        pending.cancel()
        J.FOUND.clear()


@pytest.mark.asyncio
async def test_a_stop_ends_the_wait(monkeypatch):
    monkeypatch.setattr(CT, "ROUTE_VERDICT_WAIT_S", 30.0)
    J.FOUND.clear()
    ctx = _ctx("t-stop")
    J.FOUND.note(J.turn_key(ctx), [{"tool": "goal_add"}])
    pending = asyncio.ensure_future(asyncio.sleep(30))
    stopped = asyncio.Event()
    asyncio.get_running_loop().call_later(0.1, stopped.set)
    try:
        started = time.perf_counter()
        await CT.settle_route_for_join(_Route(pending), ctx, stopped=stopped.wait)
        assert time.perf_counter() - started < 5.0, "a Stop waited for the routing call"
        assert J.FOUND.not_asked(J.turn_key(ctx)) is False
    finally:
        pending.cancel()
        J.FOUND.clear()


@pytest.mark.asyncio
async def test_a_verdict_of_nothing_is_recorded_for_the_join():
    J.FOUND.clear()
    ctx = _ctx("t-mark")
    J.FOUND.note(J.turn_key(ctx), [{"tool": "goal_add"}])

    async def answer():
        return RT.Routed(entries=[], nothing=True)

    task = asyncio.ensure_future(answer())
    await task
    try:
        await CT.settle_route_for_join(_Route(task), ctx)
        assert J.FOUND.not_asked(J.turn_key(ctx)) is True
        # ...and no routing call at all leaves the turn as it was.
        other = _ctx("t-none")
        J.FOUND.note(J.turn_key(other), [{"tool": "goal_add"}])
        await CT.settle_route_for_join(None, other)
        assert J.FOUND.not_asked(J.turn_key(other)) is False
    finally:
        J.FOUND.clear()


def test_both_lanes_settle_the_verdict_right_before_the_join():
    """MIRROR NOTE: the POST lane and the stream lane call the same function
    at the same place (before ``note_route_late`` and the join)."""
    import inspect

    from iron_jarvis.daemon.routes import chat as stream_lane

    for module, call in ((CT, "await settle_route_for_join(_route, ctx)"),
                         (stream_lane, "await _chat_turn.settle_route_for_join(")):
        source = " ".join(inspect.getsource(module).split())
        assert source.count(call) == 1, module.__name__
        at = source.index(call)
        assert 0 < source.index("note_route_late(", at) - at < 600, module.__name__
    stream = " ".join(inspect.getsource(stream_lane).split())
    assert "stopped=(handle.wait_stopped if handle is not None else None)" in stream


def test_the_note_for_a_held_back_tool_does_not_send_the_model_looking_again():
    """ "Look once more with other words" is for a lookup that found nothing
    usable; a tool held back because nobody asked for it must not start a
    second search for the same thing."""
    held = J._note([], [], {"goal_add": J.WHY_NOT_ASKED}, False)
    assert "`goal_add` did not join" in held and J.LOOK_AGAIN not in held
    # ...while any other reason still gets the advice.
    other = J._note([], [], {"goal_add": J.WHY_DENIED}, False)
    assert J.LOOK_AGAIN in other
    mixed = J._note([], [], {"goal_add": J.WHY_NOT_ASKED, "shell": J.WHY_NEVER}, False)
    assert J.LOOK_AGAIN in mixed
