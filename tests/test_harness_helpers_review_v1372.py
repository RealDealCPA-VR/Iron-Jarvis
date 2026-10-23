"""v1.372.0 REVIEW: a Build session's helper works with THE SESSION'S tools.

The first cut ran a helper on its built-in definition's roster, which was
written for office and team work: ``consult`` (a remote or cloud-pinned
teammate — another computer, another provider), ``webhook_add`` /
``schedule_create`` / ``workflow_create`` at the allow tier, ``mcp:*``, and the
web tools a Local only turn never arms — and none of the coding tools
(``glob``, ``multi_edit``, ``apply_patch``, ``process_*``). The runtime then
armed MORE off the task's words (a task naming the browser gained the
browser readers). A Build turn's folder rules never reached it, and a Local
only session's conversation grant (``shell``) ran in the helper without the
card the parent shows every time.

Now (``harness.helpers.helper_definition`` + ``chat_turn.helper_parent_now``
+ the runtime's helper branch): the builder gets the turn's tools, a
read-only type the turn's read-only tools, nothing is armed on top, the
folder rules ride along, and under Local only every tool that reaches
outside this PC asks in the helper too.

Driven through the REAL app factory and the REAL lanes (the scripted model
from ``test_harness_helpers_v1372``).
"""

from __future__ import annotations

import asyncio
import contextlib
import json

from iron_jarvis.harness import mode as M
from iron_jarvis.providers.adapters.base import ToolCall
from tests.test_build_sessions_v1367 import turn_frames, until
from tests.test_chat_turn_stop_v1241 import _asgi_post
from tests.test_desktop_reliability_v1249 import requires_node
from tests.test_harness_helpers_v1372 import (
    Model,
    _helper_children,
    _helper_frames,
    _listed_ask,
    _run,
    new_session,
    wire,
)
from tests.test_harness_helpers_v1372 import app as _app_fixture
from tests.test_harness_helpers_v1372 import work as _work_fixture

# The same fixtures the doer's file uses (the REAL app factory, a scratch
# folder with a config reader), under the names the tests below take.
app = _app_fixture
work = _work_fixture

#: Tools a built-in roster carries that a Build turn does not hold.
WIDER_THAN_THE_SESSION = {
    "consult", "message_agent", "blackboard_post", "webhook_add", "schedule_create",
    "workflow_create", "goal_add", "tool_create", "memory_write", "remember_preference",
    "ltm_append", "pane_send", "pane_spawn", "code_run", "browse", "web_extract",
}
#: The helper doors and the turn's own question / plan tools.
NEVER_FOR_A_HELPER = {"delegate", "spawn_agent", "list_agents", "exit_plan_mode", "ask_user"}


def _helper_tools(model: Model) -> set[str]:
    calls = [c for c in model.calls if not c["main"]]
    assert calls, "the helper never reached the model"
    out: set[str] = set()
    for c in calls:
        out |= set(c["tools"])
    return out


async def test_a_builder_helper_works_with_the_sessions_coding_tools_and_no_others(app, work):
    sid = new_session(app, work, approval_mode="yolo")
    model = wire(app, Model(main=[[ToolCall(id="b1", name="Agent", arguments={
        "description": "fix", "prompt": "Fix the config reader.", "subagent_type": "general-purpose",
    })], "ok"], helper=lambda t, r, rec: "fixed"))
    await _run(app, sid, "fix it", "hr-builder")
    tools = _helper_tools(model)
    # The session's coding tools (the gap the built-in builder roster had).
    assert {"glob", "multi_edit", "apply_patch", "edit_file", "write_file", "shell",
            "process_start"} <= tools, tools
    # …and nothing the session's own turn does not hold.
    assert not (tools & WIDER_THAN_THE_SESSION), tools & WIDER_THAN_THE_SESSION
    assert not (tools & NEVER_FOR_A_HELPER), tools & NEVER_FOR_A_HELPER
    assert tools <= set(M.CODING_TOOLS), tools - set(M.CODING_TOOLS)


async def test_an_explore_helper_gets_only_the_sessions_read_only_tools(app, work):
    sid = new_session(app, work, approval_mode="yolo")
    model = wire(app, Model(main=[[ToolCall(id="x1", name="Agent", arguments={
        "description": "look", "prompt": "Find the config reads.", "subagent_type": "Explore",
    })], "ok"], helper=lambda t, r, rec: "found"))
    await _run(app, sid, "look", "hr-explore")
    tools = _helper_tools(model)
    assert {"read_file", "glob", "grep"} <= tools, tools
    assert tools <= set(M.PLAN_MODE_TOOLS), tools - set(M.PLAN_MODE_TOOLS)
    assert not (tools & {"write_file", "edit_file", "apply_patch", "shell", "process_start"})


async def test_a_helpers_task_arms_nothing_on_top_of_the_session_tools(app, work):
    """The runtime arms extra tools off a run's task words (``arm_for_task``):
    a helper whose task names the browser gained the readers of the user's
    live browser, which the Build turn never holds."""
    sid = new_session(app, work, approval_mode="yolo")
    model = wire(app, Model(main=[[ToolCall(id="w1", name="Agent", arguments={
        "description": "page", "prompt": "Check which page I have open in my browser and summarise it, "
                                         "then remember that I prefer tabs.",
        "subagent_type": "general-purpose",
    })], "ok"], helper=lambda t, r, rec: "checked"))
    await _run(app, sid, "go", "hr-arm")
    tools = _helper_tools(model)
    assert not {t for t in tools if t.startswith("browser_")}, tools
    assert not (tools & {"remember_preference", "ltm_append", "recall", "guide_search", "app_search"})


async def test_a_local_only_sessions_helper_keeps_off_the_web_and_asks_before_a_command(app, work):
    sid = new_session(app, work, who=("mock", "mock"), approval_mode="yolo", local_only=True,
                      granted_tools=["shell"])

    def helper(task, results, rec):
        if not results:
            return [ToolCall(id="lo-sh", name="shell", arguments={"command": "echo local-ok"})]
        return "done"

    # (A local model speaks the generic dialect: Jarvis's own names.)
    model = wire(app, Model(main=[[ToolCall(id="lo1", name="delegate", arguments={
        "task": "Run echo.", "agent_type": "builder",
    })], "ok"], helper=helper, who=("mock", "mock")))
    task = asyncio.create_task(_run(app, sid, "go", "hr-local"))
    # The parent cards a command EVERY time in a local-only turn: the
    # conversation's "shell" grant is not handed down, so the helper asks.
    waiting = await until(lambda: [d for e, d in turn_frames(app, "hr-local")
                                   if e == "helper" and d.get("state") == "waiting"], 20)
    assert waiting and waiting[0]["ask"]["tool"] == "shell"
    (kid,) = _helper_children(app)
    assert json.loads(kid.allow_tools_json or "[]") == []
    assert (await _asgi_post(app, f"/chat/approvals/{waiting[0]['ask']['id']}", {"decision": "deny"}))[0] == 200
    await asyncio.wait_for(task, 30)
    tools = _helper_tools(model)
    assert not (tools & {"web_search", "web_fetch", "browse", "web_extract"}), tools


async def test_always_ask_a_helper_asks_before_it_writes_a_file(app, work):
    """v1.372.0 review: the helper doors are granted, and the agent runtime
    cards only the engine's ask tier — so under "Ask before every action" a
    builder helper's write_file ran with no card while the same Write in the
    turn cards every time. The turn's strict tools now ask in the helper."""
    sid = new_session(app, work, approval_mode="always_ask")

    def helper(task, results, rec):
        if not results:
            return [ToolCall(id="aa-w", name="write_file",
                             arguments={"path": "made_by_helper.txt", "content": "x"})]
        return "done"

    wire(app, Model(main=[[ToolCall(id="aa1", name="Agent", arguments={
        "description": "d", "prompt": "Write the file.", "subagent_type": "general-purpose",
    })], "ok"], helper=helper))
    task = asyncio.create_task(_run(app, sid, "go", "hr-strict"))
    waiting = await until(lambda: [d for e, d in turn_frames(app, "hr-strict")
                                   if e == "helper" and d.get("state") == "waiting"], 20)
    assert waiting and waiting[0]["ask"]["tool"] == "write_file"
    assert not (work / "made_by_helper.txt").exists()
    assert (await _asgi_post(app, f"/chat/approvals/{waiting[0]['ask']['id']}", {"decision": "deny"}))[0] == 200
    await asyncio.wait_for(task, 30)
    assert not (work / "made_by_helper.txt").exists()


async def test_a_folder_rule_reaches_the_helper(app, work):
    """``.ironjarvis/harness/plugins.toml``: a denied tool is not the helper's
    either, and an "ask" rule makes the helper ask before that tool."""
    hdir = work / ".ironjarvis" / "harness"
    hdir.mkdir(parents=True)
    (hdir / "plugins.toml").write_text(
        '[[rules]]\ntool = "process_start"\naction = "deny"\n\n'
        '[[rules]]\ntool = "write_file"\naction = "ask"\n',
        encoding="utf-8",
    )
    sid = new_session(app, work)  # the default posture: approve-for-me in the helper

    def helper(task, results, rec):
        if not results:
            return [ToolCall(id="fr-w", name="write_file", arguments={"path": "notes.txt", "content": "x"})]
        return "done"

    model = wire(app, Model(main=[[ToolCall(id="fr1", name="Agent", arguments={
        "description": "write", "prompt": "Write notes.txt.", "subagent_type": "general-purpose",
    })], "ok"], helper=helper))
    task = asyncio.create_task(_run(app, sid, "go", "hr-rules"))
    waiting = await until(lambda: [d for e, d in turn_frames(app, "hr-rules")
                                   if e == "helper" and d.get("state") == "waiting"], 20)
    assert waiting and waiting[0]["ask"]["tool"] == "write_file"
    assert (await _asgi_post(app, f"/chat/approvals/{waiting[0]['ask']['id']}", {"decision": "deny"}))[0] == 200
    await asyncio.wait_for(task, 30)
    assert not (work / "notes.txt").exists()
    tools = _helper_tools(model)
    assert "process_start" not in tools and "write_file" in tools
    ends = [d for d in _helper_frames(turn_frames(app, "hr-rules")) if d.get("ended")]
    assert ends and ends[-1]["state"] in ("done", "failed")


async def test_the_post_lane_bounds_its_helper_to_the_turns_tools_too(app, work, monkeypatch):
    """Lock-step: the POST lane hands its helper ITS armed set."""
    from iron_jarvis.daemon.chat_turn import run_chat_turn
    from iron_jarvis.daemon.schemas import ChatBody, ChatMessageBody
    from iron_jarvis.personas.builtins import BUILTIN_PERSONAS

    platform = app.state.platform
    # The POST lane arms no ask-tier tool: a user who set delegate to allow.
    monkeypatch.setitem(platform.permissions._base, "delegate", "allow")
    model = wire(app, Model(main=[[ToolCall(id="pp1", name="Agent", arguments={
        "description": "d", "prompt": "Look.", "subagent_type": "general-purpose",
    })], "ok"], helper=lambda t, r, rec: "seen"))
    body = ChatBody(messages=[ChatMessageBody(role="user", content="go")], provider="anthropic",
                    model="claude-sonnet-4-5", workspace_dir=str(work), approval_mode="yolo")
    await run_chat_turn(platform, BUILTIN_PERSONAS, body, harness="auto", session_id="build:post-review")
    tools = _helper_tools(model)
    assert "glob" in tools, tools
    # The POST lane arms no ask-tier tool, so neither does its helper.
    assert not (tools & {"shell", "process_start", "process_write"}), tools
    assert not (tools & WIDER_THAN_THE_SESSION), tools & WIDER_THAN_THE_SESSION


# --------------------------------------------------------------------------- #
# a helper's ask opens ITS Build session (the bell, the desktop)
# --------------------------------------------------------------------------- #


async def test_a_helpers_ask_names_its_build_session_in_the_pending_listing(app, work, monkeypatch):
    import time

    from iron_jarvis.core.events import EventBus, EventType

    # v1.373.1: the helper's row turns "waiting" as soon as the ask is in the
    # registry (`HelperCall` reads `approvals.pending_for`), but the pending
    # listing lists only an ask whose `approval.requested` event row has been
    # written — by the event bus's handler hop, which runs AFTER the registry
    # filed the ask. On a loaded runner that hop lagged and the listing was
    # read too early (v1.373.0's Release gate: "not enough values to unpack").
    # Hold the hop back here so the order the runner saw happens every run.
    real_run_handlers = EventBus._run_handlers

    def slow_run_handlers(handlers, event):
        if event.type == EventType.APPROVAL_REQUESTED:
            time.sleep(0.5)  # a worker thread: the loop keeps running
        real_run_handlers(handlers, event)

    monkeypatch.setattr(EventBus, "_run_handlers", staticmethod(slow_run_handlers))

    sid = new_session(app, work)  # the default posture: the helper's shell asks

    def helper(task, results, rec):
        if not results:
            return [ToolCall(id="bl-sh", name="shell", arguments={"command": "echo listed"})]
        return "done"

    wire(app, Model(main=[[ToolCall(id="bl1", name="Agent", arguments={
        "description": "d", "prompt": "Run echo.",
    })], "ok"], helper=helper))
    task = asyncio.create_task(_run(app, sid, "go", "hr-listing"))
    waiting = await until(lambda: [d for e, d in turn_frames(app, "hr-listing")
                                   if e == "helper" and d.get("state") == "waiting"], 20)
    ask_id = waiting[0]["ask"]["id"]
    try:
        (kid,) = _helper_children(app)
        assert json.loads(kid.options_json or "{}").get("build_session") == sid
        # Wait for the THING asserted — the listing's row — not for the frame
        # that turns "waiting" earlier (the CLAUDE.md waitFor rule).
        (row,) = await _listed_ask(app, ask_id)
        assert row["build_session"] == sid and row["origin"] == "job:build-helper"
    except BaseException:
        # Answer the ask so the turn ends; the failure above is the report.
        with contextlib.suppress(Exception):
            await _asgi_post(app, f"/chat/approvals/{ask_id}", {"decision": "deny"})
            await asyncio.wait_for(task, 30)
        raise
    assert (await _asgi_post(app, f"/chat/approvals/{ask_id}", {"decision": "deny"}))[0] == 200
    await asyncio.wait_for(task, 30)


@requires_node
def test_the_desktop_opens_a_helpers_ask_in_its_build_session(tmp_path):
    from tests.test_chat_lanes_v13510 import _CLICK_HARNESS, _PLAN_HARNESS
    from tests.test_desktop_reliability_v1249 import _lift, _run

    plan = _PLAN_HARNESS.replace("__LIFTED__", _lift("const ASK_WATCH_MS", "function planAskNotifications"))
    helper = {"id": "apr_h1", "tool": "shell", "session_id": "session_h1", "task": "Run the tests",
              "origin": "job:build-helper", "build_session": "bs 9"}
    plain = {k: v for k, v in helper.items() if k != "build_session"}
    out = _run(plan, tmp_path, "plan1372", json.dumps({"steps": [{"approvals": [helper, plain], "now": 0}]}))["out"]
    first, second = out[0]["notify"]
    assert first["href"] == "/terminals?session=bs%209"
    assert first["body"].endswith("click to open the session.")
    assert "href" not in second and second["body"].endswith("click to open the run.")
    click = _CLICK_HARNESS.replace("__LIFTED__", _lift("function showAskNotification", "function showAskNotification"))
    loaded = _run(click, tmp_path, "click1372", json.dumps([first]))["loaded"]
    assert loaded == ["http://127.0.0.1:8788/terminals?session=bs%209"]


def test_a_custom_helper_keeps_only_the_part_of_its_own_roster_the_turn_holds():
    from iron_jarvis.agents.types import AgentDefinition, get_agent_definition
    from iron_jarvis.core.models import AgentType
    from iron_jarvis.harness import helpers as H

    parent = H.HelperParent(
        session_id="build:z", folder="", provider="anthropic", model="m",
        tools=("read_file", "write_file", "glob", "shell", "delegate", "ask_user"),
        rules=(("shell", "ask"), ("write_file", "deny"), ("read_file", "ask")),
    )
    custom = AgentDefinition(
        type=AgentType.BUILDER, system_prompt="You scout.",
        tools=["read_file", "consult", "web_search", "shell", "delegate"],
        permission_overrides={"read_file": "deny"},
    )
    out = H.helper_definition(parent, custom, custom=True)
    # Its own roster ∩ the turn's, never a helper door or the question tool.
    assert out.tools == ["read_file", "shell"]
    # Deny wins; "ask" never lands on a key the definition denies.
    assert out.permission_overrides == {"read_file": "deny", "shell": "ask", "write_file": "deny"}
    # The shared built-in definition is never mutated.
    builder = get_agent_definition(AgentType.BUILDER)
    before = list(builder.tools)
    H.helper_definition(parent, builder)
    assert builder.tools == before
    # No parent, or a parent with no tool set: the definition unchanged.
    assert H.helper_definition(None, custom) is custom
    assert H.helper_definition(H.HelperParent(session_id="build:z", folder="", provider="p", model="m"),
                               custom) is custom


async def test_a_custom_agent_started_in_the_background_is_found_by_its_name(app, work):
    """Claude Code's ``Agent`` with ``run_in_background`` goes to
    ``spawn_agent``, which knows a custom agent by its BARE name — the
    roster's "custom:scout" used to answer "unknown agent"."""
    platform = app.state.platform
    platform.agents_registry.register("scout", "You scout.", ["read_file", "glob"], base_type="researcher")
    sid = new_session(app, work, approval_mode="yolo")
    model = wire(app, Model(main=[[ToolCall(id="cs1", name="Agent", arguments={
        "description": "scout", "prompt": "Look around.", "subagent_type": "custom:scout",
        "run_in_background": True,
    })], "ok"], helper=lambda t, r, rec: "scouted"))
    frames = await _run(app, sid, "go", "hr-custom-bg")
    ends = [d for d in _helper_frames(frames) if d.get("ended")]
    assert ends and ends[-1]["state"] == "done", ends
    (kid,) = _helper_children(app)
    assert kid.agent_type.value == "researcher"
    # …and it works with its own tools the session also holds (plus its own
    # notebook, the agent's file in the app's home — v1.297.0).
    assert _helper_tools(model) == {"read_file", "glob", "notebook"}
