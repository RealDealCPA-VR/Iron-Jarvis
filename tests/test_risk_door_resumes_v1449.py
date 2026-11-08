"""v1.449.0 (G54, Wave B "Did it work?, everywhere") — the website RISK DOOR
has a way to the user from every surface, and no surface prints a dead end.

Before: a browser action that read as a payment, a password, a deletion or a
flagged page stopped inside its tool and — from the CHAT PAGE and from an
AGENT RUN — refused with "approve it in Iron Jarvis, then make the identical
call again. Pending approval id=apr_…". Only the browser SIDEBAR (v1.276.0)
could answer it. Now (``core/risk_door``):

* the chat STREAM lane registers a resolver for its turn: the door's ask is
  filed in the one ``ChatApprovals`` registry, shown as the lane's own
  ``approval`` frame marked as the floor's (``once_only``, reason "page",
  ``floor: true``, ``why`` = the door's reason), waits like any attended card
  (Stop ends it), and Allow lets the call go on — ONE call;
* the POST lane cannot card: the door refuses in plain words with NO id;
* the agent runtime registers a resolver for its run that asks through the
  runtime's own pause: an attended origin waits, an unattended one is refused
  in words; Deny → the tool answers a refusal and the run goes on;
* a once-only SAFETY card never goes to the phone — ``core/approvals.
  SAFETY_REASONS`` is read by ``comm/prompts`` and ``comm/notifier`` too.

Every case drives the REAL stream lane (`/chat/stream` consumed as it
streams), the REAL door (``browser/tools._ActingTool._require_approval``)
over a fake browser transport whose page holds a "Submit payment" button, and
the REAL runtime pause.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.agents.runtime import AgentRuntime
from iron_jarvis.agents.types import get_agent_definition
from iron_jarvis.browser import protocol as P
from iron_jarvis.comm import MockChannel, Notifier
from iron_jarvis.comm.prompts import PendingPromptStore, handle_approval_requested
from iron_jarvis.core import risk_door
from iron_jarvis.core.approvals import SAFETY_REASONS
from iron_jarvis.core.models import AgentType
from iron_jarvis.core.turns import TURNS
from iron_jarvis.daemon.app import create_app
from iron_jarvis.harness import stall
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall

from .test_approvals_office_v1247 import _asgi_post, _drive_stream
from .test_browser_actions_v1237 import _form_page, _peer
from .test_browser_tools_v1235 import FakeBackend

SRC = Path(__file__).resolve().parents[1] / "src" / "iron_jarvis"
DEAD_END = re.compile(r"Pending approval id|identical call again")


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #
def _app(tmp_path):
    """The real app whose browser runtime talks to a scripted page."""
    app = create_app(str(tmp_path))
    platform = app.state.platform
    browser = platform.browser
    browser.backend = FakeBackend(_peer(_form_page()))
    platform.config.browser_access = "interactive"
    return app, platform, browser


def _click_payment_stream(seen: list):
    """A router.stream stub: read the page, click "Submit payment" (the risk
    door's payment vocabulary), then answer. ``seen`` collects the messages
    the third round was shown, so a test reads the tool's own words."""
    rounds = {"n": 0}

    async def fake_stream(*, provider=None, model=None, system, messages, tools,
                          session_id=None, task_class=None, **kw):
        i = rounds["n"]
        rounds["n"] += 1
        if i == 0:
            resp = LLMResponse(text="", tool_calls=[
                ToolCall(id="r1", name="browser_read_page", arguments={}),
            ])
        elif i == 1:
            resp = LLMResponse(text="", tool_calls=[
                ToolCall(id="c1", name="browser_click", arguments={"target": {"element_id": "e6"}}),
            ])
        else:
            seen.extend(list(messages))
            resp = LLMResponse(text="Done.")
            yield {"type": "text", "text": "Done."}
        yield {"type": "final", "response": resp, "provider": "mock", "model": "mock"}

    return fake_stream


def _body(turn_id: str) -> dict:
    return {
        "messages": [{"role": "user", "content": "pay the invoice on this page"}],
        "tools": ["browser_read_page", "browser_click"],
        "turn_id": turn_id,
    }


def _clicks(browser) -> list[dict]:
    return [params for name, params in browser.backend.calls if name == P.METHOD_CLICK]


def _tool_text(seen: list) -> str:
    return "\n".join(str(getattr(m, "content", "") or "") for m in seen if getattr(m, "role", "") == "tool")


def _door_cards(frames) -> list[dict]:
    return [d for ev, d in frames if ev == "approval" and d.get("floor") is True]


# --------------------------------------------------------------------------- #
# the stream lane
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_allow_on_the_chat_card_lets_the_risky_click_go_on(tmp_path):
    app, platform, browser = _app(tmp_path)
    seen: list = []
    platform.router.stream = _click_payment_stream(seen)

    async def allow(data):
        assert await _asgi_post(app, f"/chat/approvals/{data['id']}", {"decision": "once"}) == 200

    frames = await asyncio.wait_for(_drive_stream(app, _body("t-risk-allow"), allow), timeout=60)
    cards = _door_cards(frames)
    assert len(cards) == 1, [d for ev, d in frames if ev == "approval"]
    card = cards[0]
    assert card["tool"] == "browser_click"
    assert card["once_only"] is True and card["once_reason"] == risk_door.REASON
    assert card["can_always"] is False and card["timeout_s"] == 0
    assert "pay" in card["why"].lower(), card["why"]
    resolved = [d for ev, d in frames if ev == "approval_resolved" and d["id"] == card["id"]]
    assert resolved and resolved[0]["decision"] == "once"
    assert len(_clicks(browser)) == 1, "the approved click never reached the page"
    finished = [d for ev, d in frames if ev == "tool_call" and d.get("status") == "finished" and d.get("name") == "browser_click"]
    assert finished and finished[0]["ok"] is True
    assert frames[-1][0] == "done"
    assert platform.approvals.pending_count() == 0
    assert risk_door.registered_turns() == [], "the turn's resolver outlived the turn"


@pytest.mark.asyncio
async def test_deny_on_the_chat_card_refuses_in_words_with_no_id(tmp_path):
    app, platform, browser = _app(tmp_path)
    seen: list = []
    platform.router.stream = _click_payment_stream(seen)

    async def deny(data):
        assert await _asgi_post(app, f"/chat/approvals/{data['id']}", {"decision": "deny"}) == 200

    frames = await asyncio.wait_for(_drive_stream(app, _body("t-risk-deny"), deny), timeout=60)
    assert len(_door_cards(frames)) == 1
    assert _clicks(browser) == [], "a declined click reached the page"
    finished = [d for ev, d in frames if ev == "tool_call" and d.get("status") == "finished" and d.get("name") == "browser_click"]
    assert finished and finished[0]["ok"] is False
    words = _tool_text(seen)
    assert "approval required" in words and risk_door.DECLINED_TAIL in words, words
    assert not DEAD_END.search(words), words
    assert "apr_" not in words, "a queue id reached the model"
    assert frames[-1][0] == "done"


@pytest.mark.asyncio
async def test_a_wider_answer_on_the_risk_card_is_read_as_once(tmp_path):
    """A risk card is never a grant: "for this conversation" runs THIS call,
    and the very next identical call asks again."""
    app, platform, browser = _app(tmp_path)
    seen: list = []
    platform.router.stream = _click_payment_stream(seen)

    async def widen(data):
        assert await _asgi_post(app, f"/chat/approvals/{data['id']}", {"decision": "conversation"}) == 200

    frames = await asyncio.wait_for(_drive_stream(app, _body("t-risk-wide"), widen), timeout=60)
    card = _door_cards(frames)[0]
    resolved = next(d for ev, d in frames if ev == "approval_resolved" and d["id"] == card["id"])
    assert resolved["decision"] == "once"
    assert len(_clicks(browser)) == 1
    # Nothing standing was written: the queue holds no unconsumed approval.
    assert browser.approvals.approved_unconsumed("chat", _any_action()) is None


def _any_action():
    from iron_jarvis.computeruse.base import Action, Selector

    return Action(kind="click", selector=Selector(css="e6"))


@pytest.mark.asyncio
async def test_stop_while_the_risk_card_waits_ends_the_turn(tmp_path):
    app, platform, browser = _app(tmp_path)
    seen: list = []
    platform.router.stream = _click_payment_stream(seen)
    turn_id = "t-risk-stop"

    async def press_stop(data):
        if data.get("floor") is True:
            assert TURNS.stop(turn_id), "the parked turn was not stoppable"

    frames = await asyncio.wait_for(_drive_stream(app, _body(turn_id), press_stop), timeout=60)
    assert _door_cards(frames), "no risk card was shown"
    assert _clicks(browser) == [], "a stopped turn's click reached the page"
    assert "done" not in [ev for ev, _ in frames], [ev for ev, _ in frames]
    assert platform.approvals.pending_count() == 0
    assert risk_door.registered_turns() == []


@pytest.mark.asyncio
async def test_the_sidebar_turn_registers_no_lane_resolver(tmp_path, monkeypatch):
    """The browser sidebar's turns pass ``arm_family``; their door is the
    panel's own ``resolve_risk_ask`` (v1.276.0), byte-identical."""
    import iron_jarvis.daemon.routes.chat as chat_routes

    app, platform, browser = _app(tmp_path)
    seen_turns: list[list[str]] = []
    real = chat_routes._run_tool_round

    def spy(*a, **kw):
        seen_turns.append(risk_door.registered_turns())
        return real(*a, **kw)

    monkeypatch.setattr(chat_routes, "_run_tool_round", spy)

    async def fake_stream(*, provider=None, model=None, system, messages, tools,
                          session_id=None, task_class=None, **kw):
        yield {"type": "text", "text": "ok"}
        yield {"type": "final", "response": LLMResponse(text="ok"), "provider": "mock", "model": "mock"}

    platform.router.stream = fake_stream
    body = chat_routes.ChatBody(messages=[{"role": "user", "content": "hi"}], turn_id="t-sidebar")
    # Through the entry the sidebar uses (browser/panel.py), with its arm family:
    gen = await chat_routes.stream_chat_turn(
        platform, app.state.d._PERSONAS, body, arm_family=frozenset({"browser_read_page"}),
    )
    frames = []
    async for chunk in gen:
        frames.append(chunk)
        assert "t-sidebar" not in risk_door.registered_turns()
    assert frames, "the sidebar turn produced nothing"
    assert risk_door.registered_turns() == []
    # ...and the same turn WITHOUT the family registers itself while it runs.
    body2 = chat_routes.ChatBody(messages=[{"role": "user", "content": "hi"}], turn_id="t-page")
    gen2 = await chat_routes.stream_chat_turn(platform, app.state.d._PERSONAS, body2)
    saw = []
    async for _chunk in gen2:
        saw.append(risk_door.registered_turns())
    assert any("t-page" in s for s in saw), saw
    assert risk_door.registered_turns() == []


# --------------------------------------------------------------------------- #
# the POST lane — the same words minus the card (MIRROR NOTE)
# --------------------------------------------------------------------------- #
def test_the_post_lane_refuses_the_risky_click_in_plain_words_with_no_id(tmp_path):
    app, platform, browser = _app(tmp_path)
    seen: list = []
    stream = _click_payment_stream(seen)

    async def fake_complete(*, provider=None, model=None, system, messages, tools=None, **kw):
        from iron_jarvis.providers.router import RouteResult

        final = None
        async for frame in stream(provider=provider, model=model, system=system,
                                  messages=messages, tools=tools, **kw):
            if frame.get("type") == "final":
                final = frame
        return RouteResult(final["response"], "mock", "mock")

    platform.router.complete = fake_complete
    with TestClient(app) as client:
        r = client.post("/chat", json=_body("t-risk-post"))
    assert r.status_code == 200, r.text
    assert _clicks(browser) == [], "the POST lane's click reached the page with nobody asked"
    words = _tool_text(seen)
    assert "approval required" in words, words
    assert risk_door.NO_RESOLVER_TAIL in words, words
    assert not DEAD_END.search(words) and "apr_" not in words, words
    assert platform.approvals.pending_count() == 0
    assert not DEAD_END.search(r.text)


# --------------------------------------------------------------------------- #
# the agent runtime
# --------------------------------------------------------------------------- #
def _req(reason: str = "destructive/transactional action ('payment')"):
    return SimpleNamespace(id="apr_x", reason=reason)


@pytest.fixture
def rt(tmp_path):
    app = create_app(str(tmp_path))
    platform = app.state.platform
    published: list[dict] = []
    real_publish = platform.event_bus.publish

    async def spy(type, payload=None, session_id=None, **kw):
        published.append({"type": str(getattr(type, "value", type)), "payload": payload or {}, "session_id": session_id})
        return await real_publish(type, payload, session_id=session_id, **kw)

    platform.event_bus.publish = spy
    return SimpleNamespace(runtime=AgentRuntime(platform), platform=platform, published=published)


def _asks(rt):
    return [p["payload"] for p in rt.published if p["type"] == "approval.requested"]


async def _wait_for(pred, tries=500):
    for _ in range(tries):
        if pred():
            return True
        await asyncio.sleep(0.01)
    return False


@pytest.mark.asyncio
@pytest.mark.parametrize("decision,granted", [("once", True), ("deny", False), ("conversation", True)])
async def test_an_attended_run_asks_once_only_and_the_answer_decides(rt, decision, granted):
    session = SimpleNamespace(id="session_risk", origin="chat", approval_mode="", allow_tools_json="[]")
    resolver = rt.runtime._risk_door_resolver(None, session, get_agent_definition(AgentType.BUILDER), set())
    assert resolver is not None
    task = asyncio.create_task(resolver(_req(), SimpleNamespace(session_id=session.id), name="browser_click", args={"target": {}}))
    assert await _wait_for(lambda: bool(_asks(rt))), "no card was filed for the attended run"
    ask = _asks(rt)[0]
    assert ask["tool"] == "browser_click"
    assert ask["once_only"] is True and ask["once_reason"] == risk_door.REASON
    assert ask["can_always"] is False and ask["timeout_s"] == 0, ask
    assert "payment" in ask["cost_note"], ask
    await asyncio.sleep(0.2)
    assert not task.done(), "an attended ask expired into a refusal"
    assert rt.platform.approvals.resolve(ask["approval_id"], decision)
    verdict = await task
    assert verdict.granted is granted
    if not granted:
        assert verdict.tail == risk_door.DECLINED_TAIL
    resolved = [p["payload"] for p in rt.published if p["type"] == "approval.resolved"]
    assert resolved and resolved[-1]["decision"] == ("once" if granted else "deny")
    assert rt.platform.approvals.pending_count() == 0


@pytest.mark.asyncio
async def test_an_unattended_run_is_refused_in_words_and_asks_nobody(rt):
    session = SimpleNamespace(id="session_night", origin="schedule:nightly", approval_mode="", allow_tools_json="[]")
    resolver = rt.runtime._risk_door_resolver(None, session, get_agent_definition(AgentType.BUILDER), set())
    verdict = await resolver(_req(), SimpleNamespace(session_id=session.id), name="browser_click", args={})
    assert verdict.granted is False
    assert "nobody is watching" in verdict.tail, verdict.tail
    assert _asks(rt) == [], "an unattended run filed a card nobody could answer"
    words = risk_door.refusal_words(_req().reason, resolver=resolver, tail=verdict.tail)
    assert not DEAD_END.search(words) and "apr_" not in words


def test_the_runtime_registers_its_run_while_the_body_runs_and_not_after(rt, monkeypatch):
    seen: list[list[str]] = []

    async def body(self, run, session, agent_def, **kw):
        seen.append(risk_door.registered_sessions())
        return True, "ok"

    monkeypatch.setattr(AgentRuntime, "_perceive_act_body", body)
    session = SimpleNamespace(id="session_reg", origin="chat")
    out = asyncio.run(rt.runtime.perceive_act(
        SimpleNamespace(id="run_reg"), session, get_agent_definition(AgentType.BUILDER),
        system_prompt="", messages=[], tool_specs=[], session_allow=set(), sink=None, max_steps=1,
    ))
    assert out == (True, "ok")
    assert seen == [["session_reg"]], seen
    assert "session_reg" not in risk_door.registered_sessions()
    # The chat lanes' own session id is never a run's.
    assert rt.runtime._risk_door_resolver(None, SimpleNamespace(id="chat", origin="chat"), get_agent_definition(AgentType.BUILDER), set()) is None


def test_a_nested_registration_puts_the_outer_resolver_back():
    sid = "session_nest"
    outer, inner = (lambda r: True), (lambda r: False)
    try:
        assert risk_door.register_session(sid, outer) is None
        prev = risk_door.register_session(sid, inner)
        assert prev is outer
        assert risk_door.resolver_for(SimpleNamespace(session_id=sid)) is inner
        risk_door.unregister_session(sid, inner, restore=prev)
        assert risk_door.resolver_for(SimpleNamespace(session_id=sid)) is outer
        # A turn's resolver wins over the run's; a stranger's unregister is a no-op.
        risk_door.register_turn("t1", inner)
        assert risk_door.resolver_for(SimpleNamespace(turn_id="t1", session_id=sid)) is inner
        risk_door.unregister_turn("t1", outer)
        assert risk_door.resolver_for(SimpleNamespace(turn_id="t1", session_id=sid)) is inner
        risk_door.unregister_turn("t1", inner)
        assert risk_door.resolver_for(SimpleNamespace(turn_id="t1", session_id="other"), "fb") == "fb"
    finally:
        risk_door.unregister_session(sid)
        risk_door.unregister_turn("t1")


# --------------------------------------------------------------------------- #
# the phone never hears a safety card
# --------------------------------------------------------------------------- #
def _event(**payload):
    base = {"approval_id": "apr_1", "tool": "browser_click", "args": {}, "timeout_s": 0}
    base.update(payload)
    return {"type": "approval.requested", "payload": base}


def test_safety_reasons_are_one_set_and_hold_the_page():
    assert stall.SAFETY_REASONS is SAFETY_REASONS
    assert risk_door.REASON in SAFETY_REASONS


@pytest.mark.parametrize("reason", sorted(SAFETY_REASONS))
def test_the_notifier_sends_no_safety_card_to_any_destination(reason):
    mock = MockChannel()
    notifier = Notifier()
    notifier.add_channel("mock", mock)
    assert notifier.on_event(_event(once_only=True, once_reason=reason)) == {}
    assert mock.sent == []
    # The control: an ordinary ask still goes out.
    assert notifier.on_event(_event())["mock"]["ok"]
    assert len(mock.sent) == 1


def test_the_phone_registers_no_answerable_prompt_for_a_safety_card(tmp_path):
    app = create_app(str(tmp_path))
    platform = app.state.platform
    from .test_comm_approvals_v1200 import CHAT_CFG, ChatMockChannel
    from iron_jarvis.comm.threads import CommThreadStore

    ch = ChatMockChannel(CHAT_CFG)
    notifier = Notifier()
    notifier.add_channel("tg", ch)
    tstore = CommThreadStore(platform.engine)
    pstore = PendingPromptStore(platform.engine)
    tstore.resolve("tg", "777", "Val")
    assert handle_approval_requested(
        _event(once_only=True, once_reason="page"), store=pstore, notifier=notifier, thread_store=tstore
    ) == []
    assert pstore.newest_open("tg", "777") is None
    # The control: the same ask without the safety reason registers.
    out = handle_approval_requested(_event(), store=pstore, notifier=notifier, thread_store=tstore)
    assert len(out) == 1 and pstore.newest_open("tg", "777") is not None


# --------------------------------------------------------------------------- #
# no user-reachable path prints the dead end
# --------------------------------------------------------------------------- #
def test_no_source_path_prints_the_dead_end_words():
    """The two sentences that used to end a refusal — a queue id the user
    cannot act on, and "make the identical call again". Allowed: the
    repeated-call refusal (a different rule), a regex that REMOVES the words
    from an older sub-step's output, and the comments that remember them."""
    hits: list[str] = []
    for path in SRC.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for n, line in enumerate(text.splitlines(), 1):
            if not DEAD_END.search(line):
                continue
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("in Iron Jarvis") or "re.sub(" in line:
                continue
            if path.name == "fix_request.py":  # the repeated-call rule's own sentence
                continue
            # A docstring line (no code on it): the memory of the old words.
            if not any(ch in line for ch in ("=", "(", "f\"", "f'")) :
                continue
            hits.append(f"{path.relative_to(SRC)}:{n}: {stripped}")
    assert hits == [], hits


def test_the_doors_words_name_what_happened_and_no_id():
    for resolver, tail, end in (
        (None, "", risk_door.NO_RESOLVER_TAIL),
        (lambda r: False, "", risk_door.NOT_ALLOWED_TAIL),
        (lambda r: False, risk_door.DECLINED_TAIL, risk_door.DECLINED_TAIL),
        (lambda r: False, risk_door.STOPPED_TAIL, risk_door.STOPPED_TAIL),
    ):
        words = risk_door.refusal_words("a  payment\n('pay')", resolver=resolver, tail=tail)
        assert words == f"approval required: a payment ('pay'). Nothing has been done to the page. {end}"
        assert not DEAD_END.search(words)
    assert json.dumps(risk_door.NO_RESOLVER_TAIL)  # plain text, serialisable
