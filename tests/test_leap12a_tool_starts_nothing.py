"""12-a (build-out plan R2.5): the phone chat's ``code_start`` WRITES NO TASK.

Driven through the real POST lane (the phone's chat turn) at LOW trust — the
posture every phone message runs at — with the phone door's own sender
armed (``phone_code.set_asking``) and the phone surface on: the model is
offered ``code_start`` (the one arming table both chat lanes read), calls
it, and the card is asked; no night task exists afterwards. ``night_task_add``
stays on the low-trust list and is refused there. Off the phone (the PC's
chat, the stream lane, a tool call with no proven sender) it is never armed
and asks nothing.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from iron_jarvis.comm.prompts import CODE_START_KIND, PendingPromptStore
from iron_jarvis.core.trust import LOW_TRUST_DENY
from iron_jarvis.daemon import chat_turn as CT
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.schemas import ChatBody
from iron_jarvis.harness import phone_code as PC
from iron_jarvis.providers.failure_words import phone_surface
from iron_jarvis.tools.base import ToolContext
from tests._fakes.phone_code import prompts, tasks, used_repo
from tests.test_script_power_lanes_v1473 import Model, _body, _call

pytestmark = [pytest.mark.usefixtures("no_network"), pytest.mark.timeout(300)]

ASKED = "in the projA folder, fix the broken contact form"


@pytest.fixture()
def desk(tmp_path):
    app = create_app(str(tmp_path / "home"))
    used_repo(app.state.platform, tmp_path / "work" / "projA")
    ws = tmp_path / "chat"
    ws.mkdir()
    return app, ws


def _phone_turn(app, ws, model: Model, text: str = ASKED, *, who: "PC.Asking | None" = None) -> dict:
    platform = app.state.platform
    platform.router.complete = model.complete
    who = who or PC.Asking(channel="tg", sender_id="777", reply_to="777",
                           store=PendingPromptStore(platform.engine))

    async def go():
        token = PC.set_asking(who)
        try:
            with phone_surface():
                return await CT.run_chat_turn(
                    platform, {}, ChatBody(**_body(ws, text)), trust="low",
                    trust_reason="started from an inbound tg message",
                )
        finally:
            PC.reset_asking(token)

    out = asyncio.run(go())
    out["_who"] = who
    return out


def test_at_low_trust_the_phone_is_offered_the_card_and_no_task_is_written(desk):
    app, ws = desk
    platform = app.state.platform
    model = Model([
        _call(1, "code_start", folder="projA", words="fix the broken contact form"),
        "It's waiting for your Start.",
    ])
    out = _phone_turn(app, ws, model)
    offered = set(model.offered[0])
    assert "code_start" in offered, sorted(offered)
    assert "night_task_add" not in offered  # kept away at low trust
    assert "code_start" in out.get("tools_used", []), out.get("tools_used")
    # The card is asked of the sender the door proved, and follows the reply.
    [card] = out["_who"].cards
    assert card.ok and card.kind == CODE_START_KIND
    assert card.words.startswith("Start coding work in projA")
    assert "What it will do: fix the broken contact form" in card.words
    [rec] = prompts(platform, CODE_START_KIND)
    assert rec.channel == "tg" and rec.sender_id == "777" and rec.status == "open"
    # The model was told nothing starts until the user presses Start.
    assert "nothing starts until they press Start" in model.tool_texts[1][-1]
    # NOTHING was queued: only the sender's own Start does that.
    assert tasks(platform) == []


def test_night_task_add_stays_refused_at_low_trust_and_code_start_is_not_kept_away():
    assert "night_task_add" in LOW_TRUST_DENY
    assert "code_start" not in LOW_TRUST_DENY


def test_a_called_night_task_add_on_the_phone_is_refused_and_queues_nothing(desk):
    app, ws = desk
    platform = app.state.platform
    model = Model([
        _call(1, "night_task_add", words="fix the broken contact form", folder=str(ws)),
        "Done.",
    ])
    _phone_turn(app, ws, model, "queue fixing the contact form in projA for tonight")
    assert "night_task_add" not in set(model.offered[0])
    assert tasks(platform) == []


def test_off_the_phone_it_is_never_armed(desk):
    app, ws = desk
    platform = app.state.platform
    d = SimpleNamespace(platform=platform)
    # The PC's own chat (both lanes' matcher): no phone door, no card tool.
    assert "code_start" not in CT.settings_ask_tools(d, ASKED, [], [])
    assert "code_start" not in CT.post_record_tools(d, ASKED, [])
    # The phone surface WITHOUT a sender the door proved (a desktop reply
    # into a phone chat): still not armed.
    with phone_surface():
        assert "code_start" not in CT.post_record_tools(d, ASKED, [])
    # The phone's own turn arms it; a question never does.
    token = PC.set_asking(PC.Asking(channel="tg", sender_id="777", store=PendingPromptStore(platform.engine)))
    try:
        with phone_surface():
            assert "code_start" in CT.post_record_tools(d, ASKED, [])
            # A folder the user has used, named without the word "folder".
            assert "code_start" in CT.post_record_tools(d, "in projA, fix the broken contact form", [])
            assert "code_start" not in CT.post_record_tools(d, "what did you fix in projA yesterday?", [])
            assert "code_start" not in CT.post_record_tools(d, "thanks, that's great", [])
    finally:
        PC.reset_asking(token)


def test_a_call_with_no_proven_sender_asks_nothing(desk):
    app, ws = desk
    platform = app.state.platform
    ctx = ToolContext(workspace=ws, session_id="chat", agent_run_id="chat",
                      config=platform.config, event_bus=platform.event_bus, engine=platform.engine)

    async def go():
        return await platform.registry.invoke(
            "code_start", {"folder": "projA", "words": "fix it"}, ctx, platform.permissions,
            allowed_names={"code_start"}, session_allow=set(),
        )

    r = asyncio.run(go())
    assert not r.ok and r.error == PC.NOT_ON_PHONE
    assert prompts(platform, CODE_START_KIND) == [] and tasks(platform) == []
