"""v1.449.0 review of lane B4 (the website risk door reaches the user) — the
adversarial pins the builder's file did not hold, each red first on b2574966
or on a revert of its fix.

* TWO TURNS, TWO CARDS: an Allow on one turn's risk card releases THAT call
  only; the other turn's door stays parked until its own answer.
* NEVER A GRANT, from the lane's side: under "Don't ask" (yolo) and under a
  conversation grant that already covers ``browser_click`` the lane shows no
  card of its own — and the door still shows exactly one.
* A CANCEL denies the queue row (a fix): the door's row used to stay PENDING
  when the resolver's wait was cancelled; the approvals page could approve it
  later and the next identical call ran with no card (``approved_unconsumed``;
  every chat turn shares run id "ad-hoc").
* ``web_action`` APPROVES AND CONSUMES (a fix): it approved without
  consuming, so a second identical click found the approved row and ran with
  no card — one Allow, two clicks.
* The runtime: "always" / "tab" grant this one call and MINT nothing; a run
  whose grants already cover the tool still cards.
* A HELD coding session drops "Send once" on the daemon side, not only on the
  screen.
* Every Python safety reason is a reason the dashboard knows (Enter never
  allows it).
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from iron_jarvis.agents.types import get_agent_definition
from iron_jarvis.browser.tools import BrowserClickTool, BrowserReadPageTool
from iron_jarvis.computeruse.policy import ComputerUsePolicy
from iron_jarvis.core import risk_door
from iron_jarvis.core.approvals import SAFETY_REASONS
from iron_jarvis.core.models import AgentType
from iron_jarvis.daemon.schemas import ChatBody
from iron_jarvis.harness import sessions as S
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from iron_jarvis.tools.base import ToolContext

from .test_approvals_office_v1247 import _asgi_post, _drive_stream
from .test_browser_actions_v1237 import ActRuntime, FakeApprovals, _form_page, _peer
from .test_risk_door_resumes_v1449 import (  # noqa: F401 — fixtures by name
    _app, _asks, _clicks, _door_cards, _req, _tool_text, _wait_for, rt,
)

ROOT = Path(__file__).resolve().parents[1]


def _streams_by_question():
    """A router.stream stub serving SEVERAL concurrent turns: each turn's
    rounds are counted by its own question, so two turns never share a
    round counter (read, then click "Submit payment", then answer)."""
    rounds: dict[str, int] = {}
    seen: dict[str, list] = {}

    async def fake_stream(*, provider=None, model=None, system, messages, tools,
                          session_id=None, task_class=None, **kw):
        key = next((str(m.content) for m in messages if getattr(m, "role", "") == "user"), "")
        i = rounds.get(key, 0)
        rounds[key] = i + 1
        if i == 0:
            resp = LLMResponse(text="", tool_calls=[ToolCall(id=f"r-{key}", name="browser_read_page", arguments={})])
        elif i == 1:
            resp = LLMResponse(text="", tool_calls=[
                ToolCall(id=f"c-{key}", name="browser_click", arguments={"target": {"element_id": "e6"}}),
            ])
        else:
            seen.setdefault(key, []).extend(list(messages))
            resp = LLMResponse(text="Done.")
            yield {"type": "text", "text": "Done."}
        yield {"type": "final", "response": resp, "provider": "mock", "model": "mock"}

    return fake_stream, seen


def _body(turn_id: str, question: str = "pay the invoice on this page", **extra) -> dict:
    return {
        "messages": [{"role": "user", "content": question}],
        "tools": ["browser_read_page", "browser_click"],
        "turn_id": turn_id,
        **extra,
    }


# --------------------------------------------------------------------------- #
# two turns, two cards
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_an_allow_on_one_turns_risk_card_releases_that_turn_only(tmp_path):
    app, platform, browser = _app(tmp_path)
    stream, seen = _streams_by_question()
    platform.router.stream = stream
    ids: dict[str, str] = {}
    both = asyncio.Event()
    a_done = asyncio.Event()

    def _note(which: str, data: dict) -> None:
        ids[which] = data["id"]
        if len(ids) == 2:
            both.set()

    async def decide_a(data):
        if data.get("floor") is not True:
            assert await _asgi_post(app, f"/chat/approvals/{data['id']}", {"decision": "once"}) == 200
            return
        _note("a", data)
        await asyncio.wait_for(both.wait(), 30)
        assert await _asgi_post(app, f"/chat/approvals/{ids['a']}", {"decision": "once"}) == 200

    async def decide_b(data):
        if data.get("floor") is not True:
            assert await _asgi_post(app, f"/chat/approvals/{data['id']}", {"decision": "once"}) == 200
            return
        _note("b", data)
        await asyncio.wait_for(both.wait(), 30)
        await asyncio.wait_for(a_done.wait(), 30)
        # A finished on its Allow; B's own card is still the one thing pending,
        # and exactly ONE click reached the page.
        assert platform.approvals.pending_count() == 1
        assert len(_clicks(browser)) == 1
        assert await _asgi_post(app, f"/chat/approvals/{ids['b']}", {"decision": "deny"}) == 200

    task_a = asyncio.create_task(_drive_stream(app, _body("t-two-a", "pay invoice A"), decide_a))
    task_b = asyncio.create_task(_drive_stream(app, _body("t-two-b", "pay invoice B"), decide_b))
    frames_a = await asyncio.wait_for(task_a, 60)
    a_done.set()
    frames_b = await asyncio.wait_for(task_b, 60)
    assert len(_door_cards(frames_a)) == 1 and len(_door_cards(frames_b)) == 1
    assert ids["a"] != ids["b"]
    assert frames_a[-1][0] == "done" and frames_b[-1][0] == "done"
    assert len(_clicks(browser)) == 1, "the other turn's click reached the page"
    words_b = _tool_text(seen["pay invoice B"])
    assert risk_door.DECLINED_TAIL in words_b, words_b
    assert risk_door.DECLINED_TAIL not in _tool_text(seen["pay invoice A"])
    assert platform.approvals.pending_count() == 0
    assert risk_door.registered_turns() == []


# --------------------------------------------------------------------------- #
# never a grant, from the lane's side
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
@pytest.mark.parametrize("extra", [
    {"approval_mode": "yolo"},
    {"approval_mode": "approve_for_me", "granted_tools": ["browser_read_page", "browser_click"]},
], ids=["dont-ask", "conversation-grant"])
async def test_a_posture_or_grant_that_skips_the_lanes_card_never_skips_the_door(tmp_path, extra):
    app, platform, browser = _app(tmp_path)
    stream, seen = _streams_by_question()
    platform.router.stream = stream

    async def allow(data):
        assert data.get("floor") is True, f"the lane showed its own card under {extra}: {data}"
        assert await _asgi_post(app, f"/chat/approvals/{data['id']}", {"decision": "once"}) == 200

    frames = await asyncio.wait_for(_drive_stream(app, _body("t-skip", **extra), allow), timeout=60)
    cards = [d for ev, d in frames if ev == "approval"]
    assert len(cards) == 1 and cards[0]["floor"] is True, cards
    assert cards[0]["once_only"] is True and cards[0]["can_always"] is False
    assert len(_clicks(browser)) == 1
    assert frames[-1][0] == "done"


# --------------------------------------------------------------------------- #
# a cancel denies the queue row (fix)
# --------------------------------------------------------------------------- #
def _turn_ctx(tmp_path: Path, turn_id: str) -> ToolContext:
    return ToolContext(
        workspace=tmp_path, session_id="chat", agent_run_id="", config=SimpleNamespace(),  # type: ignore[arg-type]
        event_bus=SimpleNamespace(), engine=SimpleNamespace(), turn_id=turn_id,  # type: ignore[arg-type]
    )


def test_a_cancel_while_the_browser_door_waits_denies_its_queue_row(tmp_path):
    approvals = FakeApprovals()
    runtime = ActRuntime(_peer(_form_page()), policy=ComputerUsePolicy(), approvals=approvals)
    turn = "t-cancel"
    asked = asyncio.Event()

    async def never(req, ctx, *, name, args):
        asked.set()
        await asyncio.sleep(3600)
        return risk_door.Verdict(True)

    async def main():
        ctx = _turn_ctx(tmp_path, turn)
        assert (await BrowserReadPageTool(runtime).execute({}, ctx)).ok
        risk_door.register_turn(turn, never)
        try:
            task = asyncio.create_task(
                BrowserClickTool(runtime).execute({"target": {"element_id": "e5"}}, ctx)
            )
            await asyncio.wait_for(asked.wait(), 10)
            assert approvals.rows[-1].status == "pending"
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            for _ in range(200):
                if approvals.rows[-1].status == "denied":
                    break
                await asyncio.sleep(0.01)
            assert approvals.rows[-1].status == "denied", approvals.rows[-1].status
        finally:
            risk_door.unregister_turn(turn, never)
        # The row cannot be found and spent by the next identical call.
        granted: list = []

        async def grant(req, ctx, *, name, args):
            granted.append(req.id)
            return risk_door.Verdict(True)

        risk_door.register_turn(turn, grant)
        try:
            res = await BrowserClickTool(runtime).execute({"target": {"element_id": "e5"}}, ctx)
        finally:
            risk_door.unregister_turn(turn, grant)
        assert res.ok and len(granted) == 1, "the second click spent the cancelled row with no card"

    asyncio.run(main())


# --------------------------------------------------------------------------- #
# web_action approves AND consumes (fix)
# --------------------------------------------------------------------------- #
async def test_web_action_spends_the_approval_it_was_given_so_the_next_click_asks_again(tmp_path):
    from sqlmodel import select

    from iron_jarvis.computeruse.approvals import ApprovalQueue, ApprovalRequest
    from iron_jarvis.computeruse.browser import FakeBrowser
    from iron_jarvis.core.db import init_db, make_engine, session_scope

    from .test_computeruse import CUContext, computeruse_tools, make_pages, make_policy

    engine = make_engine(str(tmp_path / "cu.db"))
    init_db(engine)
    queue = ApprovalQueue(engine)
    cu = CUContext(policy=make_policy(), browser=FakeBrowser(make_pages()), approvals=queue, approval_resolver=None)
    tools = {t.name: t for t in computeruse_tools(cu)}
    ctx = ToolContext(workspace=tmp_path, session_id="s-web", agent_run_id="r", config=None, event_bus=None, engine=engine)  # type: ignore[arg-type]
    await cu.browser.navigate("https://example.com/settings")
    asked: list[str] = []

    async def grant(req, ctx, *, name, args):
        asked.append(req.id)
        return risk_door.Verdict(True)

    risk_door.register_session("s-web", grant)
    try:
        for _ in range(2):
            res = await tools["web_action"].execute({"kind": "click", "role": "button", "name": "Delete account"}, ctx)
            assert res.ok, res.error
    finally:
        risk_door.unregister_session("s-web", grant)
    assert len(asked) == 2, "the second identical click ran on the first Allow"
    with session_scope(engine) as db:
        statuses = sorted(r.status for r in db.exec(select(ApprovalRequest)))
    assert statuses == ["consumed", "consumed"], statuses


# --------------------------------------------------------------------------- #
# the runtime: wider answers mint nothing; grants never skip the card
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["always", "tab"])
async def test_a_wider_answer_on_a_runs_risk_card_runs_once_and_mints_nothing(rt, decision, monkeypatch):
    minted: list = []
    monkeypatch.setattr(rt.runtime, "_mint_always", lambda *a, **k: minted.append(a))
    session = SimpleNamespace(id="session_wide", origin="chat", approval_mode="", allow_tools_json="[]")
    resolver = rt.runtime._risk_door_resolver(None, session, get_agent_definition(AgentType.BUILDER), set())
    task = asyncio.create_task(resolver(_req(), SimpleNamespace(session_id=session.id), name="browser_click", args={}))
    assert await _wait_for(lambda: bool(_asks(rt)))
    assert rt.platform.approvals.resolve(_asks(rt)[0]["approval_id"], decision)
    verdict = await task
    assert verdict.granted is True
    assert minted == [], "a risk card's 'always' wrote a standing grant"
    resolved = [p["payload"] for p in rt.published if p["type"] == "approval.resolved"]
    assert resolved[-1]["decision"] == "once"


@pytest.mark.asyncio
async def test_a_run_whose_grants_already_cover_the_tool_still_sees_the_card(rt):
    session = SimpleNamespace(id="session_granted", origin="job:build-helper", approval_mode="approve_for_me",
                              allow_tools_json='["browser_click"]')
    allow = {"browser_click", "browser"}
    resolver = rt.runtime._risk_door_resolver(None, session, get_agent_definition(AgentType.BUILDER), allow)
    task = asyncio.create_task(resolver(_req(), SimpleNamespace(session_id=session.id), name="browser_click", args={}))
    assert await _wait_for(lambda: bool(_asks(rt))), "a covered run skipped the risk card"
    await asyncio.sleep(0.1)
    assert not task.done()
    assert rt.platform.approvals.resolve(_asks(rt)[0]["approval_id"], "deny")
    assert (await task).granted is False
    assert allow == {"browser_click", "browser"}, "the risk card widened the run's grants"


# --------------------------------------------------------------------------- #
# a held coding session drops "Send once" on the daemon side
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_held_coding_sessions_send_once_is_dropped_by_the_lane(tmp_path):
    from iron_jarvis.daemon.app import create_app
    from iron_jarvis.daemon.chat_turn import local_only_scope_for

    app = create_app(str(tmp_path / "home"))
    platform = app.state.platform
    folder = tmp_path / "work"
    folder.mkdir()
    row = S.create(platform.engine, str(folder), "", {"provider": "openai", "model": "gpt-6", "local_only": True})
    body = ChatBody(messages=[{"role": "user", "content": "hi"}], provider="openai", thread_id=row.id,
                    allow_cloud_once="openai")
    assert (await local_only_scope_for(platform, body)).allowed_once == "openai"
    S.update(platform.engine, row.id, setup={"local_only_holds": ["microsoft_365"]})
    scope = await local_only_scope_for(platform, body)
    assert scope.on is True and scope.allowed_once == "", "an app holding the session local was overridden"
    # (The view's `local_only_holds`, which hides the press, is the builder's
    # pin: test_harness_local_only_ways_out_v1449.)


# --------------------------------------------------------------------------- #
# one vocabulary
# --------------------------------------------------------------------------- #
def test_every_python_safety_reason_is_a_reason_the_dashboard_knows():
    ts = (ROOT / "dashboard" / "lib" / "onceReason.ts").read_text(encoding="utf-8")
    known = set(re.findall(r'"([a-z_]+)"', ts.split("const REASONS")[1].split(";")[0]))
    assert SAFETY_REASONS <= known, SAFETY_REASONS - known
    # The stream lane's and the runtime's door reason is one of them.
    assert risk_door.REASON in SAFETY_REASONS
