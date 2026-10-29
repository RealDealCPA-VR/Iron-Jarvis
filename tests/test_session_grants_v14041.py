"""v1.404.1 — ONE APPROVAL PER CODING SESSION (a USER BUG).

The user: "i just started off a coding agent and it has helper subagents, and
it keeps asking for approval. it should be one approval per command. It
stalled out 4 times waiting additional approvals." The live record: four
helper runs of ONE coding session, each parked on its own card for running a
command (one for 11 hours), each answered "Allow for this run" — kept only on
that helper's own run, so the next helper asked again.

Pinned here:

* a helper's "for this session" answer is the SESSION's: the other helpers
  parked on a card it covers wake with it (settled by the grant), the helpers
  started later inherit it (``helper_parent_now``), and the session's next
  reply reads it (``start_build_turn``'s ``granted_tools``);
* running a command is ONE approval (shell, a background command, typing
  into one);
* a card answered ONCE EVERY TIME (a commit or push, a project command's
  lines, a priced call) is never woken by a wider answer on another card —
  across helpers, and within one run (a latent hole the old sibling release
  had);
* another coding session is never touched; "Ask before every action" still
  hands a helper nothing; Local only never hands down a command.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from iron_jarvis.agents import runtime as runtime_mod
from iron_jarvis.agents.types import get_agent_definition
from iron_jarvis.agents.runtime import AgentRuntime
from iron_jarvis.core.models import AgentType
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.chat_turn import helper_parent_now
from iron_jarvis.harness import helpers as H
from iron_jarvis.harness import session_grants as SG
from iron_jarvis.harness import sessions as S
from iron_jarvis.tools.base import SpendCheck
from tests.test_build_sessions_v1367 import new_session


@pytest.fixture(autouse=True)
def _clean():
    SG.forget()
    saved = dict(H._LIVE)
    yield
    SG.forget()
    with H._LIVE_LOCK:
        H._LIVE.clear()
        H._LIVE.update(saved)


@pytest.fixture
def rt(tmp_path, monkeypatch):
    # Attended helpers wait with no clock; bound every wait here so a card the
    # grant must NOT wake ends the test instead of hanging it.
    monkeypatch.setattr(runtime_mod, "ATTENDED_APPROVAL_TIMEOUT_S", 1.5)
    monkeypatch.setattr(runtime_mod, "SESSION_APPROVAL_TIMEOUT_S", 1.5)
    app = create_app(str(tmp_path / "home"))
    work = tmp_path / "work"
    work.mkdir()
    return SimpleNamespace(app=app, platform=app.state.platform, runtime=AgentRuntime(app.state.platform), work=work)


def _helper(sid: str, thread: str):
    return SimpleNamespace(
        id=sid, origin=H.HELPER_ORIGIN, options_json=json.dumps({H.BUILD_SESSION_OPTION: thread}),
        allow_tools_json="[]",
    )


def _parent(thread: str, **kw):
    kw.setdefault("approval_mode", "approve_for_me")
    return H.HelperParent(session_id=f"build:{thread}", folder="", provider="", model="", **kw)


def _live(thread: str, *sids: str, **parent_kw) -> None:
    """Register live helpers of the session, each with the parent it started
    from (as `helper_call` records it)."""
    with H._LIVE_LOCK:
        H._LIVE.setdefault(f"build:{thread}", {}).update(
            {s: SimpleNamespace(parent=_parent(thread, **parent_kw)) for s in sids}
        )


def _tc(name: str, **args):
    return SimpleNamespace(name=name, arguments=args or {"command": "echo hi"})


async def _until(pred, tries: int = 400):
    for _ in range(tries):
        if pred():
            return True
        await asyncio.sleep(0.01)
    return pred()


def _pending(platform, sid: str) -> list[dict]:
    return platform.approvals.pending_for(sid)


# --------------------------------------------------------------------------- #
# the helpers of one coding session share one approval
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_one_helpers_grant_wakes_the_sessions_other_helpers_and_never_a_once_only_card(rt):
    thread = new_session(rt.app, rt.work)["id"]
    other_thread = new_session(rt.app, rt.work)["id"]
    a, b, c = (_helper(f"session_{x}", thread) for x in "abc")
    stranger = _helper("session_z", other_thread)
    _live(thread, a.id, b.id, c.id)
    _live(other_thread, stranger.id)
    agent_def = get_agent_definition(AgentType.BUILDER)
    allows = {s.id: set() for s in (a, b, c, stranger)}

    async def answer_a():
        assert await _until(lambda: all(_pending(rt.platform, s.id) for s in (a, b, c, stranger)))
        first = _pending(rt.platform, a.id)[0]
        assert rt.platform.approvals.resolve(first["approval_id"], "conversation")

    answerer = asyncio.create_task(answer_a())
    results = await asyncio.gather(
        rt.runtime._pause_for_approval(a, _tc("shell", command="dir"), agent_def, allows[a.id]),
        # A background command: the same kind of approval.
        rt.runtime._pause_for_approval(b, _tc("process_start", command="pnpm lint"), agent_def, allows[b.id]),
        # A push on the session's branch: answered once, every time.
        rt.runtime._pause_for_approval(
            c, _tc("shell", command="git push"), agent_def, allows[c.id],
            spend=SpendCheck(ask=True, once_only=True, reason="git"),
        ),
        # Another coding session's helper: never this session's grant.
        rt.runtime._pause_for_approval(stranger, _tc("shell", command="dir"), agent_def, allows[stranger.id]),
    )
    await answerer
    (ra, _), (rb, _), (rc, _), (rz, _) = results
    assert ra == "" and rb == "", (ra, rb)
    assert rc != "", "a once-only card is never woken by another card's wider answer"
    assert rz != "", "another coding session's helper keeps its own card"
    assert {"shell", "process_start", "process_write"} <= allows[a.id], "running a command is one approval"
    assert "process_start" in allows[b.id]
    assert SG.granted(thread) >= {"shell", "process_start", "process_write"}
    assert SG.granted(other_thread) == set()
    # Saved on the session: its next reply reads it.
    saved = json.loads(S.get_row(rt.platform.engine, thread).setup_json or "{}").get("granted_tools")
    assert "shell" in saved, saved


@pytest.mark.asyncio
async def test_within_one_run_a_wider_answer_never_wakes_a_once_only_sibling(rt):
    run = SimpleNamespace(id="session_solo", origin="chat")
    agent_def = get_agent_definition(AgentType.BUILDER)
    allow: set = set()

    async def answer_first():
        assert await _until(lambda: len(_pending(rt.platform, run.id)) >= 2)
        plain = [p for p in _pending(rt.platform, run.id) if not p.get("once_only")][0]
        assert rt.platform.approvals.resolve(plain["approval_id"], "conversation")

    answerer = asyncio.create_task(answer_first())
    (r1, _), (r2, _) = await asyncio.gather(
        rt.runtime._pause_for_approval(run, _tc("shell", command="dir"), agent_def, allow),
        rt.runtime._pause_for_approval(
            run, _tc("shell", command="git push -f"), agent_def, allow,
            spend=SpendCheck(ask=True, once_only=True, reason="git"),
        ),
    )
    await answerer
    assert r1 == ""
    assert r2 != "", "the push keeps its own card"


@pytest.mark.asyncio
async def test_a_run_that_is_not_a_coding_sessions_helper_keeps_its_grant_to_itself(rt):
    thread = new_session(rt.app, rt.work)["id"]
    plain = SimpleNamespace(id="session_plain", origin="chat", options_json=json.dumps({H.BUILD_SESSION_OPTION: thread}))
    agent_def = get_agent_definition(AgentType.BUILDER)
    allow: set = set()

    async def answer():
        assert await _until(lambda: _pending(rt.platform, plain.id))
        rt.platform.approvals.resolve(_pending(rt.platform, plain.id)[0]["approval_id"], "conversation")

    t = asyncio.create_task(answer())
    await rt.runtime._pause_for_approval(plain, _tc("shell"), agent_def, allow)
    await t
    assert SG.granted(thread) == set(), "only a helper (its origin) speaks for the session"
    assert "process_start" not in allow


# --------------------------------------------------------------------------- #
# what a helper started later inherits
# --------------------------------------------------------------------------- #


def _base(session_id: str, **kw):
    return H.HelperParent(session_id=session_id, folder="", provider="", model="", **kw)


def test_a_helper_started_later_inherits_the_sessions_grant_and_the_whole_command_family(rt):
    SG.note("t1", {"shell"})
    p = helper_parent_now(_base("build:t1"), rt.platform, provider="anthropic", model="m",
                          approval_mode="approve_for_me", grants=(), overrides={}, trust_state={})
    assert {"shell", "process_start", "process_write"} <= set(p.allow_tools)
    # A session granted nothing hands nothing down.
    q = helper_parent_now(_base("build:t2"), rt.platform, provider="anthropic", model="m",
                          approval_mode="approve_for_me", grants=(), overrides={}, trust_state={})
    assert not ({"shell", "process_start"} & set(q.allow_tools))
    # A grant the session's own card made (body.granted_tools) widens too.
    r = helper_parent_now(_base("build:t3"), rt.platform, provider="anthropic", model="m",
                          approval_mode="approve_for_me", grants={"process_start"}, overrides={}, trust_state={})
    assert "shell" in r.allow_tools


def test_the_narrowing_rules_still_win(rt):
    SG.note("t1", {"shell"})
    # "Ask before every action": the helper gets no grant at all.
    strict = helper_parent_now(_base("build:t1"), rt.platform, provider="anthropic", model="m",
                               approval_mode="always_ask", grants=(), overrides={}, trust_state={})
    assert strict.grants()[0] == []
    # A deny on shell is never handed down as a grant.
    denied = helper_parent_now(_base("build:t1"), rt.platform, provider="anthropic", model="m",
                               approval_mode="approve_for_me", grants=(), overrides={"shell": "deny"},
                               trust_state={})
    assert "shell" not in denied.allow_tools
    # Local only: a command reaches outside this PC; it asks every time.
    local = helper_parent_now(_base("build:t1", local_only=True), rt.platform, provider="ollama", model="m",
                              approval_mode="approve_for_me", grants=(), overrides={}, trust_state={},
                              tools=("shell", "process_start", "read_file"))
    assert not ({"shell", "process_start"} & set(local.allow_tools))
    # A chat turn's helper (not a coding session) is unchanged.
    chat = helper_parent_now(_base("chat"), rt.platform, provider="anthropic", model="m",
                             approval_mode="approve_for_me", grants={"shell"}, overrides={}, trust_state={})
    assert "process_start" not in chat.allow_tools


# --------------------------------------------------------------------------- #
# the session's next reply reads it
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_the_sessions_next_reply_reads_the_grant_and_the_family(rt, monkeypatch):
    from iron_jarvis.daemon.routes import build as build_routes
    from tests.test_build_sessions_v1367 import Script, wire
    from tests.test_build_turns_v1367 import _run, _spy_lane

    thread = new_session(rt.app, rt.work)["id"]
    S.add_granted_tools(rt.platform.engine, thread, ["process_start"])
    seen = _spy_lane(monkeypatch)
    wire(rt.app, Script(["ok"]))
    await _run(rt.app, thread, "hi", "sg-next")
    granted = set(seen[0]["body"].granted_tools)
    assert {"shell", "process_start", "process_write"} <= granted, granted
    assert build_routes._session_grants is SG


def test_add_granted_tools_keeps_what_is_there_and_adds_once(rt):
    thread = new_session(rt.app, rt.work)["id"]
    assert S.add_granted_tools(rt.platform.engine, thread, ["shell"]) == ["shell"]
    assert S.add_granted_tools(rt.platform.engine, thread, ["shell", "web_fetch"]) == ["shell", "web_fetch"]
    assert S.add_granted_tools(rt.platform.engine, "nope", ["shell"]) is None


def test_widen_is_the_command_family_only():
    assert SG.widen({"shell"}) == {"shell", "process_start", "process_write"}
    assert SG.widen({"read_file"}) == {"read_file"}
    assert SG.widen({"web_fetch", "process_write"}) == {"web_fetch", "shell", "process_start", "process_write"}
    assert SG.note("", {"shell"}) == set()


# --------------------------------------------------------------------------- #
# the session's OWN card is the session's too
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_the_sessions_own_card_wakes_a_waiting_helper_and_covers_the_command_family(rt, monkeypatch):
    from iron_jarvis.providers.adapters.base import ToolCall
    from iron_jarvis.tools.base import ToolResult
    from tests.test_build_plan_ask_v1367 import _finish, _start
    from tests.test_build_sessions_v1367 import Script, turn_frames, until, wire
    from tests.test_chat_turn_stop_v1241 import _asgi_post

    app, platform = rt.app, rt.platform
    sid = new_session(app, rt.work, approval_mode="approve_for_me")["id"]

    async def ran(args, ctx):
        return ToolResult(ok=True, output="ran")

    monkeypatch.setattr(platform.registry.get("shell"), "execute", ran)
    # A helper of this session, parked on its own card for a background command.
    helper = "session_helper1"
    _live(sid, helper)
    waiting_id, waiting = platform.approvals.request("process_start", {"command": "pnpm lint"}, helper)
    # ...and a once-only one (a push) that must stay parked.
    push_id, push = platform.approvals.request("shell", {"command": "git push"}, helper, once_only=True,
                                              once_reason="git")
    wire(app, Script([[ToolCall(id="s1", name="Bash", arguments={"command": "echo hi"})], "Done."]))
    drop, task = await _start(app, sid, "check it", "sg-own")
    card = (await until(lambda: [d for e, d in turn_frames(app, "sg-own") if e == "approval"]))[0]
    assert card["tool"] == "shell"
    assert (await _asgi_post(app, f"/chat/approvals/{card['id']}", {"decision": "conversation"}))[0] == 200
    await _finish(app, "sg-own", drop, task)
    assert waiting.done() and waiting.result() == "conversation", "the helper's card woke with the same answer"
    assert platform.approvals.actor_of(waiting_id) == "grant"
    assert not push.done(), "a once-only card keeps waiting"
    assert SG.granted(sid) >= {"shell", "process_start", "process_write"}
    platform.approvals.resolve(push_id, "deny")



# --------------------------------------------------------------------------- #
# the review's findings (v1.404.1)
# --------------------------------------------------------------------------- #


class _Tool:
    def __init__(self, name: str, perm: str, quarantined: bool = False):
        self.name, self._perm, self.quarantined = name, perm, quarantined

    def perm_key(self) -> str:
        return self._perm


class _Registry:
    def __init__(self, *tools):
        self._t = {t.name: t for t in tools}

    def get(self, name):
        return self._t.get(name)


@pytest.mark.asyncio
async def test_one_apps_grant_never_wakes_another_apps_card():
    """Every app (MCP) tool shares the `mcp_call` key: matching by it woke
    another app's card on one app's grant (the review's HIGH finding)."""
    from iron_jarvis.core.approvals import ChatApprovals

    reg = _Registry(_Tool("mcp__github__list_issues", "mcp_call"), _Tool("mcp__slack__post_message", "mcp_call"),
                    _Tool("shell", "shell"))
    ap = ChatApprovals()
    _live("t1", "session_b")
    slack_id, slack = ap.request("mcp__slack__post_message", {}, "session_b")
    shell_id, shell = ap.request("shell", {}, "session_b")
    assert SG.release_helpers(ap, reg, "t1", {"mcp__github__list_issues", "mcp_call"}) == 0
    assert not slack.done(), "another app's card keeps waiting"
    assert SG.note("t1", {"mcp__github__list_issues", "mcp_call"}, reg) == set(), "an app tool is never shared"
    assert SG.release_helpers(ap, reg, "t1", {"shell"}) == 1 and shell.done()
    ap.resolve(slack_id, "deny")


def test_a_quarantined_or_unknown_tool_is_never_shared():
    reg = _Registry(_Tool("mcp_new_write", "mcp_new_write", quarantined=True), _Tool("shell", "shell"))
    assert not SG.shareable(reg, "mcp_new_write")
    assert not SG.shareable(reg, "nope")
    assert SG.shareable(reg, "shell")


@pytest.mark.asyncio
@pytest.mark.parametrize("narrowing", [
    {"approval_mode": "always_ask"},
    {"local_only": True},
    {"low_trust": True},
    {"rules": (("shell", "ask"),)},
])
async def test_a_helper_whose_own_rules_ask_is_never_woken_or_covered(narrowing):
    from iron_jarvis.core.approvals import ChatApprovals

    reg = _Registry(_Tool("shell", "shell"))
    ap = ChatApprovals()
    _live("t1", "session_strict", **narrowing)
    _, fut = ap.request("shell", {}, "session_strict")
    SG.note("t1", {"shell"}, reg)
    assert SG.release_helpers(ap, reg, "t1", {"shell"}) == 0
    assert not fut.done(), f"woken under {narrowing}"
    assert not SG.covers(reg, "t1", "session_strict", {"shell"})
    fut.cancel()


@pytest.mark.asyncio
async def test_a_helper_that_cannot_share_keeps_its_grant_to_its_own_run(rt):
    thread = new_session(rt.app, rt.work)["id"]
    strict = _helper("session_strict", thread)
    _live(thread, strict.id, approval_mode="always_ask")
    agent_def = get_agent_definition(AgentType.BUILDER)
    allow: set = set()

    async def answer():
        assert await _until(lambda: _pending(rt.platform, strict.id))
        rt.platform.approvals.resolve(_pending(rt.platform, strict.id)[0]["approval_id"], "conversation")

    t = asyncio.create_task(answer())
    (r, _) = await rt.runtime._pause_for_approval(strict, _tc("shell"), agent_def, allow)
    await t
    assert r == "" and "shell" in allow
    assert SG.granted(thread) == set(), "an Ask-first helper never speaks for the session"
    assert "process_start" not in allow


@pytest.mark.asyncio
async def test_a_helper_already_running_is_covered_by_a_later_grant_with_no_card(rt):
    thread = new_session(rt.app, rt.work)["id"]
    later = _helper("session_later", thread)
    _live(thread, later.id)
    SG.note(thread, {"shell"}, rt.platform.registry)
    agent_def = get_agent_definition(AgentType.BUILDER)
    allow: set = set()
    r, _ = await asyncio.wait_for(
        rt.runtime._pause_for_approval(later, _tc("process_start", command="pnpm lint"), agent_def, allow), 5,
    )
    assert r == "" and "process_start" in allow
    assert _pending(rt.platform, later.id) == [], "no card was filed"
    # ...but a once-only card still asks.
    async def deny_push():
        assert await _until(lambda: _pending(rt.platform, later.id))
        rt.platform.approvals.resolve(_pending(rt.platform, later.id)[0]["approval_id"], "deny")

    t = asyncio.create_task(deny_push())
    r2, _ = await rt.runtime._pause_for_approval(
        later, _tc("shell", command="git push"), agent_def, allow,
        spend=SpendCheck(ask=True, once_only=True, reason="git"),
    )
    await t
    assert r2 != "", "the push still had its own card"


def test_a_saved_grant_is_merged_under_the_lock_not_overwritten(rt):
    thread = new_session(rt.app, rt.work)["id"]
    S.add_granted_tools(rt.platform.engine, thread, ["shell"])
    S.add_granted_tools(rt.platform.engine, thread, ["web_fetch"])
    saved = json.loads(S.get_row(rt.platform.engine, thread).setup_json or "{}")["granted_tools"]
    assert saved == ["shell", "web_fetch"]


@pytest.mark.asyncio
@pytest.mark.parametrize("trust", ["low", None])
async def test_a_low_trust_turns_grant_is_never_the_sessions(rt, trust, monkeypatch):
    from iron_jarvis.daemon.schemas import ChatBody, ChatMessageBody
    from iron_jarvis.providers.adapters.base import ToolCall
    from iron_jarvis.tools.base import ToolResult
    from tests.test_build_sessions_v1367 import Script, wire
    from tests.test_harness_mode_lanes_v1366 import _stream_answering

    platform = rt.platform
    thread = new_session(rt.app, rt.work)["id"]

    async def ran(args, ctx):
        return ToolResult(ok=True, output="ran")

    monkeypatch.setattr(platform.registry.get("shell"), "execute", ran)
    # The demo model speaks the generic dialect: Jarvis's own tool names.
    wire(rt.app, Script([[ToolCall(id="l1", name="shell", arguments={"command": "echo hi"})], "Done."],
                        who=("mock", "mock")))
    body = ChatBody(messages=[ChatMessageBody(role="user", content="check")], provider="mock", model="mock",
                    workspace_dir=str(rt.work), approval_mode="approve_for_me")
    kw = {"trust": trust} if trust else {}
    frames = await _stream_answering(platform, body, "conversation", harness=True,
                                     session_id=f"build:{thread}", **kw)
    assert [d["tool"] for e, d in frames if e == "approval"] == ["shell"]
    if trust == "low":
        assert SG.granted(thread) == set(), "a low-trust turn's grant stays in that turn"
    else:
        assert "shell" in SG.granted(thread)
