"""A saved preference does not keep the look-first check away (v1.482.0).

In the live runs "wait till others have tried the updates" was answered with
``remember_preference`` and "I've locked that in", while the update channel
stayed as it was: the check was never asked, because a tool that changes
something had run. A saved preference is a note for the model and changes no
setting, so such a reply is asked like a reply of words; a reply whose
preference WAS the request answers KEEP and stands as written.

Driven through the REAL stream lane with a scripted model, plus the gate on
its own.
"""

from __future__ import annotations

import pytest

from iron_jarvis.daemon import chat_turn as CT
from iron_jarvis.providers.adapters.base import LLMMessage
from tests.test_look_first_v1480 import NAME, Seen, _post, _stream
from tests import test_look_first_v1480 as _lf
from tests.test_script_power_lanes_v1473 import _body, _call

desk = _lf.desk  # the fixture, shared with the v1.480.0 tests

pytestmark = [pytest.mark.usefixtures("no_network"), pytest.mark.timeout(300)]

SAVED = "Got it, I've locked that in: you'll get updates later."
ASK = "dont give me the new updates right away, wait till others have tried them"


@pytest.mark.asyncio
async def test_a_saved_preference_is_asked_and_the_lookup_takes_the_reply_back(desk):
    app, ws = desk
    model = Seen([
        _call(1, "remember_preference", text="Wait until others have tried updates."),
        SAVED,                                              # round 1: the claim
        _call(2, NAME, query="get updates later / stable update channel / delay updates"),
        "There is a setting for this: the Stable channel. Shall I switch it?",
    ])
    frames, _cards, done = await _stream(app, _body(ws, ASK), model)
    assert model.asked() == [2], model.doors
    assert [d.get("reason") for ev, d in frames if ev == "reset"] == ["look_first"]
    assert SAVED not in done["reply"]


@pytest.mark.asyncio
async def test_a_preference_that_was_the_request_keeps_its_reply(desk):
    app, ws = desk
    model = Seen([
        _call(1, "remember_preference", text="Keep answers short."),
        "Got it: short answers from now on.",
        "KEEP",
    ])
    _frames, _cards, done = await _stream(app, _body(ws, "from now on keep your answers short"), model)
    assert model.asked() == [2]
    assert done["reply"] == "Got it: short answers from now on."


@pytest.mark.asyncio
async def test_the_post_lane_asks_too(desk):
    app, ws = desk
    model = Seen([
        _call(1, "remember_preference", text="Wait until others have tried updates."),
        SAVED,
        "KEEP",
    ])
    out = await _post(app, _body(ws, ASK), model)
    assert model.asked() == [2] and out["reply"] == SAVED


def _wanted(app, *msgs):
    d = type("D", (), {"platform": app.state.platform})()
    body = type("B", (), {"auto_tools": True, "turn_id": "t-1"})()
    return CT.look_first_wanted(
        d, body, armed=[NAME], msgs=[LLMMessage(role="user", content=ASK), *msgs], head=1,
        reply="Done.", round_index=1, rounds=6, trust_state={"low": False, "tainted": False},
    )


def test_only_a_saved_preference_is_let_through(desk):
    app, _ws = desk
    assert _wanted(app, LLMMessage(role="tool", name="remember_preference", content="ok")) is True
    # Every other tool that changes something still keeps the check away,
    # a note written to a memory base included (the user can see that one).
    for name in ("ltm_append", "write_file", "config_set", "memory_write"):
        assert _wanted(app, LLMMessage(role="tool", name=name, content="ok")) is False, name
    # A preference beside a real change: the change keeps it away.
    assert _wanted(app, LLMMessage(role="tool", name="remember_preference", content="ok"),
                   LLMMessage(role="tool", name="write_file", content="ok")) is False
    assert CT.LOOK_FIRST_NOT_A_CHANGE == frozenset({"remember_preference"})


def test_the_note_says_a_preference_is_not_a_setting():
    assert "preference changes no setting" in CT.LOOK_FIRST_NOTE
    assert "two other ways to name it" in CT.LOOK_FIRST_NOTE
    assert "two other ways to name" in CT.CAPABILITY_BLOCK
