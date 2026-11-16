"""North-star plan 6.7: THE PHONE GETS THE READ-ONLY RECORD TOOLS, AND A TRUE
REASON (v1.506.0).

1. The POST lane (the phone, an API caller) never armed a record tool: the
   arming lived in the stream lane only (``chat_turn.settings_ask_tools``).
   "What is queued for the night shift tonight?" from the phone had no
   ``night_task_list``. Now the POST lane arms the ALLOW-TIER subset of the
   very same matcher (``chat_turn.post_record_tools``): readers and the two
   narrowing switches. Nothing that asks first, and no create tool (the
   stream lane makes those ask), is armed where nobody can be asked.
2. A lookup on the phone told the model that the reply "has read content that
   was flagged", which was false: the phone's chat starts at low trust by its
   DOOR. The two causes are passed apart and each has its own true sentence.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from iron_jarvis.capability_search import join as J
from iron_jarvis.capability_search import tool as CT_TOOL
from iron_jarvis.daemon import chat_turn as CT
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.schemas import ChatBody
from tests.test_script_power_lanes_v1473 import Model, _body, _call, _post

pytestmark = [pytest.mark.usefixtures("no_network"), pytest.mark.timeout(300)]

NAME = CT_TOOL.NAME


@pytest.fixture()
def desk(tmp_path):
    app = create_app(str(tmp_path / "home"))
    ws = tmp_path / "work"
    ws.mkdir()
    J.FOUND.clear()
    yield app, ws
    J.FOUND.clear()


def _run_low(app, body: dict, model: Model, *, trust: str = "low") -> dict:
    platform = app.state.platform
    platform.router.complete = model.complete
    return asyncio.run(CT.run_chat_turn(
        platform, {}, ChatBody(**body), trust=trust, trust_reason="started from an inbound telegram message",
    ))


# --------------------------------------------------------------------------- #
# 1. the allow-tier record tools in the POST lane
# --------------------------------------------------------------------------- #
CASES = [
    ("what is queued for the night shift tonight?", "night_task_list"),
    ("show my clients", "client_list"),
    ("show the coding defaults for this folder", "build_folder_show"),
    ("take back the always-allow for web_fetch", "grant_revoke"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("text,tool", CASES)
async def test_the_post_lane_arms_the_allow_tier_record_tool(desk, text, tool):
    app, ws = desk
    model = Model(["Here it is."])
    await _post(app, _body(ws, text), model)
    assert tool in model.offered[0], (tool, model.offered[0])


@pytest.mark.asyncio
async def test_the_post_lane_never_arms_a_record_tool_that_asks(desk):
    app, ws = desk
    model = Model(["Done."])
    await _post(app, _body(ws, "delete the Monday morning schedule"), model)
    offered = set(model.offered[0])
    assert "schedule_list" in offered, offered  # the reader comes
    assert not offered & {"schedule_update", "schedule_delete", "workflow_schedule"}, offered


def test_the_two_lanes_share_one_matcher(desk):
    app, _ws = desk
    d = SimpleNamespace(platform=app.state.platform)
    text = "delete the Monday morning schedule"
    both = CT.settings_ask_tools(d, text, [], [])
    post = CT.post_record_tools(d, text, [])
    assert set(post) <= set(both) and post == ["schedule_list"], (post, both)
    # A tool the user set to ask is not armed where nobody can be asked.
    app.state.platform.permissions._base["schedule_list"] = "ask"
    assert CT.post_record_tools(d, text, []) == []


def test_a_create_tool_is_never_armed_by_the_post_lane(desk):
    app, _ws = desk
    d = SimpleNamespace(platform=app.state.platform)
    text = "make a schedule that runs every weekday at 8am and sends me a digest"
    assert "schedule_create" in CT.settings_ask_tools(d, text, [], [])
    assert "schedule_create" not in CT.post_record_tools(d, text, [])


def test_on_the_phone_the_reader_still_comes_and_low_trust_still_holds(desk):
    app, ws = desk
    model = Model(["Here it is."])
    _run_low(app, _body(ws, "what is queued for the night shift tonight?"), model)
    assert "night_task_list" in model.offered[0], model.offered[0]
    model = Model(["Done."])
    _run_low(app, _body(ws, "keep this chat local only"), model)
    assert "chat_local_only" not in model.offered[0], "low trust keeps a switch away"


# --------------------------------------------------------------------------- #
# 2. the true reason
# --------------------------------------------------------------------------- #
def test_each_cause_has_its_own_sentence():
    assert "flagged" in J.LOW_TRUST_NOTE
    assert "flagged" not in J.DOOR_LOW_NOTE
    assert "low trust" in J.DOOR_LOW_NOTE


def test_a_lookup_on_the_phone_is_told_the_true_reason(desk):
    app, ws = desk
    app.state.platform.config.capability_search = True
    model = Model([_call(1, NAME, query="search past conversations history"), "Done."])
    _run_low(app, _body(ws, "hello there"), model)
    said = model.tool_texts[1][-1]
    assert J.DOOR_LOW_NOTE in said, said
    assert J.LOW_TRUST_NOTE not in said, "the reply read nothing flagged"


def test_a_reply_that_read_flagged_content_keeps_its_sentence(desk):
    app, _ws = desk
    app.state.platform.config.capability_search = True
    d = SimpleNamespace(platform=app.state.platform)
    for tainted, note in ((True, J.LOW_TRUST_NOTE), (False, J.DOOR_LOW_NOTE)):
        ctx = SimpleNamespace(session_id="chat", turn_id=f"t-{tainted}")
        J.FOUND.note(J.turn_key(ctx), [{"kind": "tool", "id": "history_search", "tool": "history_search"}])
        said = SimpleNamespace(role="tool", name=NAME, content="")
        J.join_found(d, SimpleNamespace(auto_tools=True, granted_tools=[]), tools_used=[], armed=[],
                     auto_armed=[], ask_armed=(), tool_specs=[], overrides={}, armed_grant=set(),
                     msgs=[said], ctx=ctx, low_trust=True, tainted=tainted)
        assert note in said.content, (tainted, said.content)
    # The lanes pass both causes from the turn's own trust state.
    assert CT.trust_causes({"low": True, "tainted": False}) == {"low_trust": True, "tainted": False}
    assert CT.trust_causes({"low": False, "tainted": True}) == {"low_trust": True, "tainted": True}
    assert CT.trust_causes({"low": False, "tainted": False}) == {"low_trust": False, "tainted": False}
