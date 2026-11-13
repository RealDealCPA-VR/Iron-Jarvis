"""The Feature lookup check, for a reply that ends by OFFERING a change its own
tools make (v1.488.0).

Seen live, with the settings tools in hand: "The best fit is auto mode. Would
you like me to set it to auto?" The user would answer twice (in words, then on
the card). The check (its own switch, off by default) now asks once: call the
tool, the card or the Undo is how the user decides; KEEP when the change needs
a detail only the user has. Sending and deleting stay the user's call
(``harness.closing_offer``): such an offer is never pushed. Driven through the
real chat lanes with a scripted model.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from iron_jarvis.daemon import chat_turn as CT
from iron_jarvis.providers.adapters.base import LLMMessage, LLMResponse
from tests.test_look_first_v1480 import NAME, Seen, _post, _stream, desk  # noqa: F401 — the fixture
from tests.test_script_power_lanes_v1473 import _body, _call

pytestmark = [pytest.mark.usefixtures("no_network"), pytest.mark.timeout(300)]

OFFER = "I can turn on follow-up suggestions for you. Want me to do that?"
ASK = "turn on the follow-up suggestions please"


def _offer_asked(model) -> list[int]:
    return [i for i, t in enumerate(model.transcripts)
            if t and t[-1] == ("user", CT.LOOK_FIRST_OFFER_NOTE)]


@pytest.mark.asyncio
async def test_an_offer_its_own_tool_makes_is_made_good_with_the_tool(desk):  # noqa: F811
    app, ws = desk
    model = Seen([
        OFFER,                                                        # round 0: offers in words
        _call(1, "config_set", key="chat_followups", value=True),     # the check: it calls the tool
        "Done: follow-up suggestions are on.",
    ])
    frames, _cards, done = await _stream(app, _body(ws, ASK), model)
    assert _offer_asked(model) == [1], model.transcripts
    ran = [d for ev, d in frames if ev == "tool_call" and d.get("name") == "config_set"
           and d.get("status") != "running"]
    assert ran and ran[-1].get("ok") is True, ran
    assert app.state.platform.config.chat_followups is True
    assert done["reply"].startswith("Done: follow-up suggestions are on.")


@pytest.mark.asyncio
async def test_keep_leaves_the_offer_as_written(desk):  # noqa: F811
    app, ws = desk
    model = Seen([OFFER, "KEEP"])
    _frames, _cards, done = await _stream(app, _body(ws, ASK), model)
    assert _offer_asked(model) == [1]
    assert done["reply"] == OFFER
    assert app.state.platform.config.chat_followups is False


@pytest.mark.asyncio
async def test_the_post_lane_makes_it_good_too(desk):  # noqa: F811
    app, ws = desk
    model = Seen([OFFER, _call(1, "config_set", key="chat_followups", value=True), "Done."])
    out = await _post(app, _body(ws, ASK), model)
    assert _offer_asked(model) == [1]
    assert app.state.platform.config.chat_followups is True
    assert out["reply"].startswith("Done.")


def _d(app):
    return SimpleNamespace(platform=app.state.platform)


def test_the_offer_reading(desk):  # noqa: F811
    app, _ws = desk
    d, msgs = _d(app), [LLMMessage(role="user", content="q")]
    assert CT.offer_a_tool_makes(d, "Shall I set it to auto?", ["config_set"], msgs, 1)
    # Sending and deleting are the user's call, never pushed.
    assert not CT.offer_a_tool_makes(d, "Want me to delete the old schedule too?", ["schedule_delete"], msgs, 1)
    # No tool that changes anything in hand: no offer to make good.
    assert not CT.offer_a_tool_makes(d, "Shall I set it to auto?", ["config_list", NAME], msgs, 1)
    # Already called in this reply.
    called = [*msgs, LLMMessage(role="tool", name="config_set", content="ok")]
    assert not CT.offer_a_tool_makes(d, "Shall I set it to auto?", ["config_set"], called, 1)
    # A question for a detail is no offer.
    assert not CT.offer_a_tool_makes(d, "What is the address of the machine?", ["config_set"], msgs, 1)


def test_after_a_lookup_only_an_offer_is_asked(desk):  # noqa: F811
    app, _ws = desk
    body = SimpleNamespace(auto_tools=True, turn_id="t-offer")
    looked = [LLMMessage(role="user", content="ask less often"), LLMMessage(role="tool", name=NAME, content="found")]
    kw = dict(armed=[NAME, "config_set"], msgs=looked, head=1, round_index=1, rounds=8,
              trust_state={"low": False, "tainted": False})
    assert CT.look_first_wanted(_d(app), body, reply="Shall I set it to auto?", **kw)
    assert not CT.look_first_wanted(_d(app), body, reply="Here are the levels: Ask first, Auto.", **kw)


@pytest.mark.asyncio
async def test_a_call_of_a_tool_the_reply_does_not_hold_is_not_acted_on(desk):  # noqa: F811
    app, _ws = desk
    p = app.state.platform

    async def complete(**kw):
        return SimpleNamespace(response=LLMResponse(text="", tool_calls=_call(9, "shell", command="dir")),
                               provider="mock", model="mock")

    p.router.complete = complete
    specs = p.registry.specs(["config_set", NAME])
    out = await CT.look_first_call(_d(app), msgs=[LLMMessage(role="user", content=ASK)], system="s",
                                   provider="mock", model="mock", head=1, tools=specs, reply=OFFER)
    assert out[0] is None
    assert out[1][-1].content == CT.LOOK_FIRST_OFFER_NOTE


def test_a_saved_preference_is_not_the_tool_that_makes_the_change(desk):  # noqa: F811
    """Review: "a saved preference changes no setting" (v1.482.0). With only
    `remember_preference` in hand the offer note would push the model to save
    a preference in place of the change it offered; the reply gets the plain
    look-first note instead (which says to look the change up)."""
    app, _ws = desk
    d, msgs = _d(app), [LLMMessage(role="user", content="q")]
    assert not CT.offer_a_tool_makes(d, "Shall I set it to weekly?", ["remember_preference", NAME], msgs, 1)
    assert CT.offer_a_tool_makes(d, "Shall I set it to weekly?", ["remember_preference", "config_set"], msgs, 1)


def test_the_offer_note_never_claims_a_tool_and_keeps_the_lookup_open():
    """Review: the gate knows the reply holds A tool that changes something,
    not that it is the one for the offered change. The note says "if", and
    names the lookup for the case where none of the reply's tools fits."""
    note = CT.LOOK_FIRST_OFFER_NOTE
    assert "If one of your tools makes that change" in note
    assert "and one of your tools makes it" not in note  # the old flat claim
    assert "If none of your tools makes it" in note
    assert "`find_capability`" in note


@pytest.mark.asyncio
async def test_a_lookup_answer_to_the_offer_note_is_acted_on(desk):  # noqa: F811
    app, _ws = desk
    p = app.state.platform

    async def complete(**kw):
        return SimpleNamespace(response=LLMResponse(text="", tool_calls=_call(9, NAME, query="update check weekly")),
                               provider="mock", model="mock")

    p.router.complete = complete
    specs = p.registry.specs(["config_set", NAME])
    out = await CT.look_first_call(_d(app), msgs=[LLMMessage(role="user", content=ASK)], system="s",
                                   provider="mock", model="mock", head=1, tools=specs, reply=OFFER)
    assert out[1][-1].content == CT.LOOK_FIRST_OFFER_NOTE
    assert out[0] is not None and [c.name for c in out[0].tool_calls] == [NAME]
