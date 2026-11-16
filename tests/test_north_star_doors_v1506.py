"""North-star plan 6.4: A THING MADE IN CHAT HAS A DOOR (v1.506.0).

``daemon/doors._CATALOG`` was keyed on ``create_agent``, the agent-run tool no
chat path brings, so an agent made in chat (``agent_create``) left no door,
and neither did a project, rule, goal, watcher, persona, skill, night task,
model server, memory base or app. The catalog is keyed on chat's record tools
now. This file pins:

* EVERY chat tool that MAKES a record has a door, the list DERIVED from the
  record-tool table (``settings.records``), never hand-listed here;
* every door leads to a page that exists;
* the door rides both lanes' real replies.
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest

from iron_jarvis.daemon import doors as D
from iron_jarvis.daemon.app import create_app
from iron_jarvis.settings import records as R
from tests.test_script_power_lanes_v1473 import Model, _body, _call, _post, _rows, _stream

pytestmark = [pytest.mark.usefixtures("no_network"), pytest.mark.timeout(300)]

DASH = Path(__file__).resolve().parent.parent / "dashboard"

#: The verbs a record tool's name ends in when it MAKES a record.
MAKES = ("create", "add", "connect", "import")


def makers() -> list[str]:
    """Every chat tool that makes a record: the record-tool table, the create
    tools that gained an Undo, and chat's skill maker."""
    names = set(R.RECORD_CARD_TOOLS) | set(R.ASK_FIRST_CREATE_TOOLS) | {"skill_create"}
    return sorted(n for n in names if n.rsplit("_", 1)[-1] in MAKES)


def _ok(data=None):
    return SimpleNamespace(ok=True, data=data or {}, output="")


def test_the_list_of_makers_is_not_empty_and_holds_the_ones_the_plan_names():
    got = set(makers())
    assert {"agent_create", "project_create", "reflex_create", "goal_add", "sentinel_add", "persona_create",
            "skill_create", "night_task_add", "model_server_add", "memory_base_add", "app_connect"} <= got, got
    assert "create_agent" not in got


@pytest.mark.parametrize("tool", makers())
def test_every_chat_tool_that_makes_a_record_has_a_door(tool):
    if tool in D.OWN_DOOR_TOOLS:
        # Decided from what the call carried, or by the page step to the same
        # place (doors.OWN_DOOR_TOOLS says which and why).
        assert D.door_for("app_connect", _ok({"app": "gmail", "not_started": True, "needs_setup": True}))
        assert D.door_for("account_add", _ok({"door": {"href": "/connections", "label": "Sign in"}}))
        return
    door = D.door_for(tool, _ok({}))
    if tool in D.CLIENT_DOOR_TOOLS:
        assert door and door["href"].startswith("/clients"), door
        return
    assert door and door.get("href") and door.get("label"), (tool, door)


def _routes() -> set[str]:
    return {p.name for p in (DASH / "app").iterdir() if (p / "page.tsx").exists()}


def test_every_catalog_door_leads_to_a_page_that_exists():
    routes = _routes()
    src = (DASH / "components" / "settings" / "SettingsHome.tsx").read_text(encoding="utf-8")
    sections = set(re.findall(r'^\s+id: "([a-z]+-[a-z-]+)",$', src, re.M))
    for tool, spec in D._CATALOG.items():
        u = urlparse(spec["href"])
        assert u.path.strip("/").split("/")[0] in routes, (tool, spec["href"])
        q = parse_qs(u.query)
        if "section" in q:
            assert q["section"][0] in sections, (tool, spec["href"])


def test_the_agent_door_is_keyed_on_chats_own_tool():
    assert D.door_for("agent_create", _ok({"name": "penny"})) == {
        "href": "/agents", "label": "Meet your new agent — 'penny'"}
    assert D.door_for("create_agent", _ok({"name": "penny"})) is None


# --------------------------------------------------------------------------- #
# through the real lanes
# --------------------------------------------------------------------------- #
@pytest.fixture()
def desk(tmp_path):
    app = create_app(str(tmp_path / "home"))
    ws = tmp_path / "work"
    ws.mkdir()
    yield app, ws


@pytest.mark.asyncio
async def test_an_agent_made_in_chat_leaves_its_door_on_the_stream_lane(desk):
    app, ws = desk
    model = Model([_call(1, "agent_create", name="penny", role="Looks after refills."), "Penny is set up."])
    body = _body(ws, "make a new agent called penny who looks after my refills")
    body.pop("workspace_dir", None)
    _f, cards, done = await _stream(app, body, model, decision="once")
    assert [c.get("tool") for c in cards] == ["agent_create"], cards
    assert [r for r in _rows(app, "agent_create") if r.ok], "the agent was made"
    assert {"href": "/agents", "label": "Meet your new agent — 'penny'"} in done["doors"], done["doors"]


@pytest.mark.asyncio
async def test_a_goal_made_in_chat_leaves_its_door_on_the_post_lane(desk):
    app, ws = desk
    model = Model([_call(1, "goal_add", text="Answer every client email within a day"), "Saved."])
    body = _body(ws, "add a goal: answer every client email within a day", tools=["goal_add"])
    body.pop("workspace_dir", None)
    done = await _post(app, body, model)
    assert [r for r in _rows(app, "goal_add") if r.ok], "the goal was made"
    assert {"href": "/autonomy", "label": "See your goal"} in done["doors"], done["doors"]
