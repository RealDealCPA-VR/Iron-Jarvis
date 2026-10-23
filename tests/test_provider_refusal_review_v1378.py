"""v1.378.0 review — the "model refusal shown honestly" fix, checked again.

Each pin here was red on the doer's commit (9da6f28d) and is green on the
fix beside it; each fix was mutation-checked (break it, watch the pin go red,
put it back byte for byte).

Everything is scripted: no real model, no network.
"""

from __future__ import annotations

import pytest

from iron_jarvis.core.events import EventType
from iron_jarvis.daemon.chat_turn import friendly_error
from iron_jarvis.providers.adapters.base import ProviderError, ProviderRefusal
from iron_jarvis.providers.router import ModelRouter
from tests.test_provider_refusal_v1378 import (
    _Bus,
    _Healthy,
    _Mgr,
    _Refusing,
    _drain,
    _refusal,
    _user,
)


# =========================================================================== #
# 1. `partial` is what REACHED THE USER — and only the router knows that
# =========================================================================== #
async def test_complete_never_says_the_reply_above_is_incomplete():
    """complete() shows the caller nothing before it returns, so a refusal it
    raises is never `partial` — even when the adapter saw text in the declined
    message (Anthropic's complete(), claude-cli's complete() over its own
    stream, OpenAI/Gemini `_parse`). The words used to say "declined to
    finish this answer. The reply above is incomplete." over NOTHING (the
    POST lane, the phone, an agent's nudge)."""
    refused = _Refusing("claude-cli", _refusal(partial=True))
    router = ModelRouter(_Mgr({"claude-cli": refused}), default_provider="claude-cli", event_bus=_Bus())
    with pytest.raises(ProviderRefusal) as info:
        await router.complete(system="", messages=_user(), tools=[])
    assert info.value.partial is False
    words = friendly_error(info.value)
    assert words["title"] == "Claude Code declined to answer this conversation"
    assert "reply above" not in words["fix"]


async def test_stream_partial_is_whether_an_answer_frame_reached_the_caller():
    # Declined before any answer frame, though the adapter called it partial
    # (text in the declined message that never streamed).
    early = _Refusing("claude-cli", _refusal(partial=True))
    router = ModelRouter(_Mgr({"claude-cli": early}), default_provider="claude-cli", event_bus=_Bus())
    with pytest.raises(ProviderRefusal) as info:
        await _drain(router.stream(system="", messages=_user(), tools=[]))
    assert info.value.partial is False
    # Declined AFTER words reached the caller, though the adapter did not say
    # so: the text stays on screen, and the words say the rest was declined.
    late = _Refusing("claude-cli", _refusal(partial=False), head="Here is ")
    router = ModelRouter(_Mgr({"claude-cli": late}), default_provider="claude-cli", event_bus=_Bus())
    frames: list[dict] = []
    with pytest.raises(ProviderRefusal) as info:
        async for f in router.stream(system="", messages=_user(), tools=[]):
            frames.append(f)
    assert [f["text"] for f in frames if f.get("type") == "text"] == ["Here is "]
    assert info.value.partial is True


# =========================================================================== #
# 2. a failover CANDIDATE that declines ends the turn — no third provider
# =========================================================================== #
def _busy(provider: str) -> _Refusing:
    """A primary that failed for an ordinary reason (so a fallback is allowed)."""
    return _Refusing(provider, ProviderError(f"{provider} API error 503: overloaded", status_code=503))


def _broken(provider: str) -> _Refusing:
    """A primary that ANSWERED with a permanent error (fallback (A) still runs)."""
    return _Refusing(provider, ProviderError(f"{provider} API error 400: bad request", status_code=400))


class _Declines(_Refusing):
    """A candidate whose safety check declines, with no provider of its own
    on the error (OpenAI/Gemini refusals carry none — the router names it)."""

    def __init__(self, provider: str):
        super().__init__(provider, ProviderRefusal("the provider's safety check declined to answer"))


@pytest.mark.parametrize("lane", ["complete", "stream"])
async def test_the_default_fallback_that_declines_is_raised_never_handed_on(lane):
    """(A): the explicit pick failed, the default stands in and DECLINES. The
    turn ends with that refusal — the next connected provider is never tried
    (sending a declined conversation on until something accepts it is the
    user's decision, never a routing fallback), and the default's breaker is
    not struck (it answered)."""
    primary = _broken("anthropic")
    default = _Declines("openai")
    third = _Healthy("google")
    bus = _Bus()
    router = ModelRouter(
        _Mgr({"anthropic": primary, "openai": default, "google": third}),
        default_provider="openai", event_bus=bus,
    )
    with pytest.raises(ProviderRefusal) as info:
        if lane == "complete":
            await router.complete(provider="anthropic", system="", messages=_user(), tools=[])
        else:
            await _drain(router.stream(provider="anthropic", system="", messages=_user(), tools=[]))
    assert default.calls == 1 and third.calls == 0
    assert info.value.failed_provider == "openai", "the words name who declined"
    assert router.health._fails.get("openai", 0) == 0, "no breaker strike: the provider answered"
    failed = [p for p in bus.of(EventType.PROVIDER_FAILED) if p["provider"] == "openai"]
    assert failed and failed[-1].get("kind") == "refusal"


@pytest.mark.parametrize("lane", ["complete", "stream"])
async def test_a_sideways_candidate_that_declines_is_raised_never_handed_on(lane):
    """(B): the primary was busy (transient), a sideways candidate stands in
    and DECLINES — the remaining candidates are never tried, nothing struck."""
    primary = _busy("claude-cli")
    candidate = _Declines("codex-cli")
    later = _Healthy("anthropic")
    bus = _Bus()
    router = ModelRouter(
        _Mgr({"claude-cli": primary, "codex-cli": candidate, "anthropic": later}),
        default_provider="claude-cli", event_bus=bus, deadline_s=0.3,
    )
    with pytest.raises(ProviderRefusal) as info:
        if lane == "complete":
            await router.complete(system="", messages=_user(), tools=[])
        else:
            await _drain(router.stream(system="", messages=_user(), tools=[]))
    assert candidate.calls == 1 and later.calls == 0
    assert info.value.failed_provider == "codex-cli"
    assert router.health._fails.get("codex-cli", 0) == 0, "no breaker strike: the provider answered"
    assert [p for p in bus.of(EventType.PROVIDER_FAILED) if p["provider"] == "codex-cli"][-1]["kind"] == "refusal"


# =========================================================================== #
# 3. a refusal is never read as "too long" — the refit retry is a retry
# =========================================================================== #
def _long_history(n: int = 8) -> list[dict]:
    words = "the quarterly ledger reconciliation notes " * 60
    out = []
    for k in range(n):
        out.append({"role": "user" if k % 2 == 0 else "assistant", "content": f"{k}: {words}"})
    if out[-1]["role"] != "user":
        out.append({"role": "user", "content": "Say hello"})
    return out


def _too_long_refusal() -> ProviderRefusal:
    """A refusal whose own words happen to contain an overflow phrase (the
    vendor's explanation rides the message)."""
    return ProviderRefusal(
        "claude-cli: Anthropic's safety check declined to answer (stop_reason refusal, "
        "category cyber): prompt is too long for this policy",
        provider="claude-cli", category="cyber", whole_conversation=True,
    )


@pytest.mark.parametrize("lane", ["post", "stream"])
def test_a_refusal_is_never_shrunk_and_sent_again_as_too_long(tmp_path, monkeypatch, lane):
    """The overflow refit retries ONCE with older messages left out, and
    LEARNS a smaller window for the model. A refusal whose words match an
    overflow phrase was read as "too long": the declined conversation was sent
    again (a retry of a refusal) and every later turn of that model was
    shrunk. Both lanes (lock-step)."""
    from fastapi.testclient import TestClient

    from iron_jarvis.daemon import chat_turn
    from iron_jarvis.daemon.app import create_app

    client = TestClient(create_app(str(tmp_path)))
    platform = client.app.state.platform
    calls = {"n": 0}

    async def fake_complete(**kw):
        calls["n"] += 1
        raise _too_long_refusal()

    async def fake_stream(**kw):
        calls["n"] += 1
        raise _too_long_refusal()
        yield  # pragma: no cover — a generator

    monkeypatch.setattr(platform.router, "complete", fake_complete)
    monkeypatch.setattr(platform.router, "stream", fake_stream)
    learned_before = dict(chat_turn._LEARNED_WINDOWS)
    body = {"messages": _long_history()}
    if lane == "post":
        r = client.post("/chat", json=body)
        assert r.status_code == 502, r.text
        title = r.json()["title"]
    else:
        r = client.post("/chat/stream", json=body)
        from tests.test_provider_refusal_v1378 import _sse_frames

        [err] = [d for ev, d in _sse_frames(r.text) if ev == "error"]
        title = err["title"]
    assert calls["n"] == 1, "a declined conversation is never sent again"
    assert chat_turn._LEARNED_WINDOWS == learned_before, "no window learned from a refusal"
    assert title == "Claude Code declined to answer this conversation"


# =========================================================================== #
# 4. one-shot utilities: the refusal's sentence, never a "not connected"
# =========================================================================== #
class _OneShotRefuses:
    def __init__(self, provider: str, exc: Exception):
        self.provider, self.model, self.exc, self.calls = provider, "m", exc, 0

    async def complete(self, *, system, messages, tools, **kw):
        self.calls += 1
        raise self.exc


def _skill_app(tmp_path):
    from fastapi.testclient import TestClient

    from iron_jarvis.daemon.app import create_app

    sd = tmp_path / ".ironjarvis" / "skills" / "note-taker"
    sd.mkdir(parents=True)
    (sd / "SKILL.md").write_text(
        "---\nname: note-taker\ndescription: takes notes\n---\nWrite tidy notes.", encoding="utf-8",
    )
    return TestClient(create_app(str(tmp_path)))


@pytest.mark.parametrize(
    "provider, exc",
    [
        # A local endpoint (a LiteLLM proxy passing on a content filter).
        ("ollama", ProviderRefusal("the provider's safety check declined to answer (finish_reason content_filter)")),
        # A cloud subscription (Anthropic's classifier) — `partial` as
        # claude-cli's complete() reports it when text streamed to nobody.
        ("claude-cli", _refusal(partial=True)),
    ],
)
def test_a_one_shot_utility_answers_a_refusal_with_its_sentence(tmp_path, monkeypatch, provider, exc):
    """The one-shot utilities (skill apply, terminal assist, the workflow
    builder, share/compact, …) call the adapter directly. A refusal there
    answered the vendor's raw words ("stop_reason refusal") — and from a LOCAL
    endpoint it read as an "answered with an error" outage and raised the
    "not connected" banner. Now: the run's refusal sentence, no banner, no
    other provider tried."""
    client = _skill_app(tmp_path)
    platform = client.app.state.platform
    refusing = _OneShotRefuses(provider, exc)
    others: list[str] = []

    def get(name, model=None):
        if name == provider:
            return refusing
        others.append(name)
        return _Healthy(name)

    banners: list[tuple] = []

    async def not_connected(*a, **kw):
        banners.append((a, kw))

    monkeypatch.setattr(platform.providers, "get", get)
    monkeypatch.setattr(platform.router, "_publish_not_connected", not_connected)
    r = client.post("/skills/note-taker/apply", json={"request": "notes on the plan", "provider": provider})
    assert r.status_code == 502, r.text
    detail = r.json()["detail"]
    assert "declined to do this task" in detail, detail
    assert "stop_reason" not in detail and "content_filter" not in detail
    assert banners == [], "a refusal is not an outage"
    assert refusing.calls == 1 and others == [], "never retried, never sent elsewhere"


def test_a_one_shot_candidate_that_declines_ends_it(tmp_path, monkeypatch):
    """The one-shot failover chain (a busy primary): a candidate that DECLINES
    ends the call with its sentence — the next connected provider is never
    tried (the router's (A)/(B) rule, lock-step)."""
    from iron_jarvis.daemon import app as app_mod

    client = _skill_app(tmp_path)
    platform = client.app.state.platform
    busy = _OneShotRefuses("anthropic", ProviderError("anthropic API error 503: overloaded", status_code=503))
    declines = _OneShotRefuses("codex-cli", ProviderRefusal("codex-cli: OpenAI's policy check declined the turn"))
    later = _Healthy("google")
    adapters = {"anthropic": busy, "codex-cli": declines, "google": later}
    real_retry = app_mod._complete_with_retry

    async def once(adapter, **kw):  # no backoff sleeps in a test
        return await real_retry(adapter, attempts=1, **kw)

    monkeypatch.setattr(app_mod, "_complete_with_retry", once)
    monkeypatch.setattr(platform.providers, "available", lambda name: name in adapters)
    monkeypatch.setattr(platform.providers, "get", lambda name, model=None: adapters[name])
    r = client.post("/skills/note-taker/apply", json={"request": "notes", "provider": "anthropic"})
    assert r.status_code == 502, r.text
    assert r.json()["detail"].startswith("Codex declined to do this task"), r.text
    assert declines.calls == 1 and later.calls == 0


# =========================================================================== #
# 5. the fleet's Anthropic-compatible adapter speaks the same Messages API
# =========================================================================== #
_COMPAT_REFUSED = {
    "id": "msg_r", "type": "message", "role": "assistant", "model": "claude-proxy",
    "content": [], "stop_reason": "refusal", "stop_sequence": None,
    "stop_details": {"type": "refusal", "category": "cyber", "explanation": "flagged"},
    "usage": {"input_tokens": 120, "output_tokens": 0},
}


def _compat_stream(text: str = "") -> list[str]:
    import json as _json

    ev = [{"type": "message_start", "message": {"id": "msg_r", "type": "message", "role": "assistant",
                                                "content": [], "model": "claude-proxy", "stop_reason": None,
                                                "usage": {"input_tokens": 0, "output_tokens": 0}}}]
    if text:
        ev += [
            {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text}},
            {"type": "content_block_stop", "index": 0},
        ]
    ev += [
        {"type": "message_delta",
         "delta": {"stop_reason": "refusal", "stop_sequence": None,
                   "stop_details": {"type": "refusal", "category": "cyber", "explanation": "flagged"}},
         "usage": {"output_tokens": 0}},
        {"type": "message_stop"},
    ]
    return [_json.dumps(e) for e in ev]


async def test_the_anthropic_compatible_endpoint_types_a_refusal_in_complete_and_stream():
    """A LiteLLM proxy (the user's own) passes Anthropic's Messages answer
    through for a Claude model, so `stop_reason: "refusal"` arrives here in
    the SAME shape. It became an empty reply and "Press Retry". Now the typed
    refusal — not claimed to be the whole conversation (what stands behind a
    proxy is not known: the words say "may")."""
    from tests.test_anthropic_compat_litellm_v1330 import _adapter, _json, _stream_of, MSG

    with pytest.raises(ProviderRefusal) as info:
        await _adapter(_json(_COMPAT_REFUSED)).complete(system="", messages=MSG, tools=[])
    err = info.value
    assert err.category == "cyber" and err.explanation == "flagged"
    assert err.failed_provider == "fleet-spark" and err.whole_conversation is False
    assert err.no_failover is True and err.partial is False
    with pytest.raises(ProviderRefusal) as info:
        _ = [f async for f in _adapter(_stream_of(_compat_stream())).stream(system="", messages=MSG, tools=[])]
    assert info.value.category == "cyber" and info.value.partial is False
    frames: list[dict] = []
    with pytest.raises(ProviderRefusal) as info:
        async for f in _adapter(_stream_of(_compat_stream("Sure, "))).stream(system="", messages=MSG, tools=[]):
            frames.append(f)
    assert [f["text"] for f in frames if f["type"] == "text"] == ["Sure, "]
    assert info.value.partial is True
    # An ordinary end is untouched.
    ok = dict(_COMPAT_REFUSED, stop_reason="end_turn", content=[{"type": "text", "text": "hi"}])
    ok.pop("stop_details")
    assert (await _adapter(_json(ok)).complete(system="", messages=MSG, tools=[])).text == "hi"


# =========================================================================== #
# 6. the way out is one the SURFACE can do: the phone, a Build pane's chat
# =========================================================================== #
#: The chat page's way out — Start a new chat / Edit (both exist there).
CHAT_WAY = "Start a new chat, or edit an earlier message to go back before the part it flagged."
PHONE_WAY = "Send /new to start a new conversation."
PANE_WAY = "Open a new terminal to start a new conversation."


async def test_the_phone_hears_a_way_out_it_can_take(platform, monkeypatch):
    """The REAL chat turn behind the REAL phone poller. A phone has no Edit
    and no "new chat" button; it has "/new" (the thread reset). The reply
    used to send the chat page's way out — two things the phone cannot do."""
    from iron_jarvis.daemon.chat_turn import run_chat_turn
    from tests.test_comm_full_chat import CHAT_CFG, ChatMockChannel, _chat_poller, _msg

    async def refused(**kw):
        raise _refusal()

    monkeypatch.setattr(platform.router, "complete", refused)
    ch = ChatMockChannel(dict(CHAT_CFG))
    poller, _orch, store = _chat_poller(platform, ch, run_chat_turn)
    res = await poller._handle("tg", ch, _msg("say hello"))
    reply = store.history_body(res["thread_id"])[-1]["content"]
    assert reply.startswith("I hit a problem: Claude Code declined to answer this conversation."), reply
    assert reply.endswith(PHONE_WAY), reply
    assert "edit an earlier message" not in reply and "Start a new chat" not in reply
    assert "stop_reason" not in " ".join(ch.sent)


def test_a_desktop_reply_into_a_phone_thread_says_the_phone_words(tmp_path, monkeypatch):
    """POST /comm/threads/{id}/send (a reply typed on the desktop, delivered
    to the phone): its failure line was the raw `detail` — the vendor's
    words ("stop_reason refusal, category cyber") on the user's phone."""
    from fastapi.testclient import TestClient

    from iron_jarvis.daemon.app import create_app
    from iron_jarvis.daemon.chat_turn import run_chat_turn
    from tests.test_comm_full_chat import _wire_chat_channel

    with TestClient(create_app(str(tmp_path))) as client:
        store, _poller, ch = _wire_chat_channel(client, run_chat_turn)

        async def refused(**kw):
            raise _refusal()

        monkeypatch.setattr(client.app.state.platform.router, "complete", refused)
        t = store.resolve("tg", "777", "Val")
        r = client.post(f"/comm/threads/{t.id}/send", json={"text": "say hello"})
    assert r.status_code == 200, r.text
    reply = r.json()["reply"]
    assert reply.startswith("I hit a problem: Claude Code declined to answer this conversation."), reply
    assert reply.endswith(PHONE_WAY) and "stop_reason" not in reply
    assert "stop_reason" not in " ".join(ch.sent)


@pytest.mark.parametrize("lane", ["stream", "post"])
def test_a_build_pane_chat_hears_a_way_out_it_can_take(tmp_path, monkeypatch, lane):
    """A Build pane's chat (PaneChat: one conversation per terminal, no Edit,
    no new-chat press) was told "Start a new chat, or edit an earlier
    message" — neither exists there. A new terminal is a new conversation."""
    from fastapi.testclient import TestClient

    from iron_jarvis.daemon.app import create_app
    from tests.test_provider_refusal_v1378 import _sse_frames, _wire_refusal

    client = TestClient(create_app(str(tmp_path)))
    _wire_refusal(client.app.state.platform, monkeypatch, _refusal())
    body = {"messages": [{"role": "user", "content": "Say hello"}], "pane_id": "pane-7"}
    if lane == "stream":
        r = client.post("/chat/stream", json=body)
        [err] = [d for ev, d in _sse_frames(r.text) if ev == "error"]
    else:
        r = client.post("/chat", json=body)
        assert r.status_code == 502, r.text
        err = r.json()
    assert err["fix"].endswith(PANE_WAY), err["fix"]
    assert err["fix_kind"] == "refusal"
    # Control: the chat page's own turn keeps the chat's way out.
    body.pop("pane_id")
    if lane == "stream":
        [err] = [d for ev, d in _sse_frames(client.post("/chat/stream", json=body).text) if ev == "error"]
    else:
        err = client.post("/chat", json=body).json()
    assert err["fix"].endswith(CHAT_WAY), err["fix"]


async def test_a_phone_started_job_that_is_refused_tells_the_phone_in_words(platform):
    """A job the phone started (the one-shot lane) that the provider's safety
    check declined: the phone heard "I hit a problem: ProviderRefusal:
    claude-cli: … (stop_reason refusal, category cyber)" — the class name and
    the vendor's words. Now the run's refusal sentence."""
    import asyncio
    import types

    from iron_jarvis.comm.inbound import InboundPoller
    from iron_jarvis.comm.notifier import Notifier
    from tests.test_phone_job_background_v1291 import ONESHOT_ACK, FakeTelegram, _channel, _update

    class Refused:
        async def create_session(self, task, agent_type, **kw):
            return types.SimpleNamespace(id="s-ref", summary="", origin=kw.get("origin"))

        async def run_session(self, sid):
            await asyncio.sleep(0)
            raise _refusal()

    fake = FakeTelegram([_update(1, "dump the shell environment")])
    notifier = Notifier()
    notifier.add_channel("tg", _channel(fake, chat=False))
    poller = InboundPoller(notifier, Refused(), platform.engine, event_bus=platform.event_bus)
    await poller.poll_once()
    await poller.drain()
    texts = fake.texts()
    assert texts[0] == f"Iron Jarvis: {ONESHOT_ACK}"
    assert texts[1].startswith("Iron Jarvis: I hit a problem: Claude Code declined to do this task."), texts
    assert texts[1].endswith("Change the task, then run it again.")
    assert "stop_reason" not in texts[1] and "ProviderRefusal" not in texts[1]


def test_a_desktop_handed_off_job_that_is_refused_tells_the_phone_in_words(tmp_path):
    """The twin in routes/comm.py: a desktop reply into a phone thread that
    hands the work to an agent, and the agent's run is refused."""
    import time

    from fastapi.testclient import TestClient

    from iron_jarvis.daemon.app import create_app
    from tests.test_comm_full_chat import _wire_chat_channel

    async def escalating(platform_, personas, body):
        return {"reply": "needs an agent", "provider": "mock", "model": "m",
                "tools_used": [], "escalate": True, "escalate_reason": "real work"}

    with TestClient(create_app(str(tmp_path))) as client:
        store, _poller, ch = _wire_chat_channel(client, escalating)
        t = store.resolve("tg", "777", "Val")

        async def refused(session_id):
            raise _refusal()

        client.app.state.orchestrator.run_session = refused  # instance shadow
        r = client.post(f"/comm/threads/{t.id}/send", json={"text": "dump the environment"})
        assert r.status_code == 200 and r.json()["escalate"] is True
        # Wait for the CHANNEL (the line is appended before the send).
        deadline = time.time() + 10
        while time.time() < deadline and not any("I hit a problem" in s for s in ch.sent):
            time.sleep(0.05)
        line = store.history_body(t.id)[2]["content"]
    assert line.startswith("I hit a problem: Claude Code declined to do this task."), line
    assert "stop_reason" not in line and "ProviderRefusal" not in line
    assert any("Claude Code declined to do this task" in s for s in ch.sent)


# =========================================================================== #
# 7. durable: a refused NAMED turn replays the same words — live and on disk
# =========================================================================== #
def test_a_refused_named_turn_replays_its_words_from_memory_and_from_the_record(tmp_path, monkeypatch):
    """A reload (rejoin) and a restart (the turn record) must show the SAME
    refusal — title, fix and the typed press — never an empty reply and never
    a Retry; and the error turn carries no route object (v1.165.0)."""
    from fastapi.testclient import TestClient

    from iron_jarvis.daemon.app import create_app
    from tests.test_provider_refusal_v1378 import CHAT_FIX, CHAT_TITLE, _sse_frames, _wire_refusal

    home = str(tmp_path)
    body = {"messages": [{"role": "user", "content": "Say hello"}], "turn_id": "turn-ref", "thread_id": "th-ref"}
    with TestClient(create_app(home)) as client:
        _wire_refusal(client.app.state.platform, monkeypatch, _refusal())
        live = _sse_frames(client.post("/chat/stream", json=body).text)
        rejoin = _sse_frames(client.get("/chat/turns/turn-ref/stream").text)
    with TestClient(create_app(home)) as client:  # a restart: the record replays
        stored = _sse_frames(client.get("/chat/turns/turn-ref/stream").text)
        listed = client.get("/chat/turns", params={"thread_id": "th-ref"}).json()["turns"]
    for frames in (live, rejoin, stored):
        [err] = [d for ev, d in frames if ev == "error"]
        assert (err["title"], err["fix"], err["fix_kind"]) == (CHAT_TITLE, CHAT_FIX, "refusal")
        assert not any(ev == "done" for ev, _ in frames)
        assert not any("route" in d for _, d in frames), "an error turn carries no route"
    assert [t["status"] for t in listed] == ["error"]


# =========================================================================== #
# 8. "the model wrote nothing": the v1.246.0 nudge is unchanged; a declined
#    nudge reads the surface's words and claims no reply above
# =========================================================================== #
def _nudge_app(tmp_path, monkeypatch, second):
    """A turn whose model writes NOTHING, then `second()` answers the nudge."""
    from fastapi.testclient import TestClient

    from iron_jarvis.daemon.app import create_app
    from iron_jarvis.providers.router import RouteResult

    client = TestClient(create_app(str(tmp_path)))
    platform = client.app.state.platform
    calls = {"n": 0}

    async def fake_complete(**kw):
        calls["n"] += 1
        if calls["n"] == 1:
            from iron_jarvis.providers.adapters.base import LLMResponse

            return RouteResult(LLMResponse(text="", usage={"input_tokens": 1, "output_tokens": 0}), "claude-cli", "m")
        return await second(stream=False)

    async def fake_stream(**kw):
        calls["n"] += 1
        if calls["n"] == 1:
            from iron_jarvis.providers.adapters.base import LLMResponse

            yield {"type": "final", "response": LLMResponse(text="", usage={"input_tokens": 1, "output_tokens": 0}),
                   "provider": "claude-cli", "model": "m"}
            return
        async for f in second(stream=True):
            yield f

    monkeypatch.setattr(platform.router, "complete", fake_complete)
    monkeypatch.setattr(platform.router, "stream", fake_stream)
    return client, calls


def _reply_of(client, lane: str, body: dict) -> str:
    from tests.test_provider_refusal_v1378 import _sse_frames

    if lane == "stream":
        r = client.post("/chat/stream", json=body)
        [done] = [d for ev, d in _sse_frames(r.text) if ev == "done"]
        return done["reply"]
    return client.post("/chat", json=body).json()["reply"]


@pytest.mark.parametrize("lane", ["stream", "post"])
def test_an_empty_answer_still_gets_the_nudge_and_the_plain_words(tmp_path, monkeypatch, lane):
    """No regression of v1.246.0: a model that writes nothing for an
    ordinary reason is asked once more, and a failed nudge says what
    happened ("Press Retry" is right THERE — nothing was declined)."""

    def second(stream: bool):
        if stream:
            async def gen():
                raise ProviderError("claude-cli API error 500: oops", status_code=500)
                yield  # pragma: no cover
            return gen()

        async def boom():
            raise ProviderError("claude-cli API error 500: oops", status_code=500)
        return boom()

    client, calls = _nudge_app(tmp_path, monkeypatch, second)
    reply = _reply_of(client, lane, {"messages": [{"role": "user", "content": "Say hello"}]})
    assert calls["n"] == 2, "the one nudge happened"
    assert reply.startswith("The model returned an empty answer"), reply
    assert "Press Retry" in reply


@pytest.mark.parametrize("lane", ["stream", "post"])
def test_a_declined_nudge_in_a_build_pane_says_the_panes_way_out(tmp_path, monkeypatch, lane):
    def second(stream: bool):
        if stream:
            async def gen():
                raise _refusal()
                yield  # pragma: no cover
            return gen()

        async def boom():
            raise _refusal()
        return boom()

    client, _calls = _nudge_app(tmp_path, monkeypatch, second)
    reply = _reply_of(client, lane, {"messages": [{"role": "user", "content": "Say hello"}], "pane_id": "pane-7"})
    assert reply.startswith("Claude Code declined to answer this conversation."), reply
    assert PANE_WAY in reply and "edit an earlier message" not in reply


def test_a_nudge_declined_after_words_streamed_claims_no_reply_above(tmp_path, monkeypatch):
    """The stream lane STREAMS the nudge; if it is declined after a few words,
    the reply the turn ends with is the refusal's sentence alone (done.reply
    is authoritative and holds none of those words) — so it must not say
    "The reply above is incomplete"."""

    def second(stream: bool):
        async def gen():
            yield {"type": "text", "text": "Hel"}
            raise _refusal(partial=True)
        return gen()

    client, _calls = _nudge_app(tmp_path, monkeypatch, second)
    reply = _reply_of(client, "stream", {"messages": [{"role": "user", "content": "Say hello"}]})
    assert reply.startswith("Claude Code declined to answer this conversation."), reply
    assert "reply above" not in reply


# =========================================================================== #
# 9. a workflow's agent step that is refused: never retried, said in words
# =========================================================================== #
async def test_a_refused_workflow_step_is_never_retried_and_says_its_words(tmp_path):
    """`on_failure: retry` re-ran a REFUSED agent step — the same task to the
    same model, declined again (a retry the refusal rule forbids) — and the
    step's summary (the Workflows page, the step event, a scheduled fire's
    one line) was "ProviderRefusal: claude-cli: … (stop_reason refusal …)".
    Control: an ordinary raise keeps its retry and its words (v1.231.0)."""
    import iron_jarvis.workflows.models  # noqa: F401 — the workflow tables
    import json as _json

    from iron_jarvis.agents.orchestrator import Orchestrator
    from iron_jarvis.platform import build_platform
    from iron_jarvis.workflows.engine import Step, WorkflowDef, WorkflowEngine

    platform = build_platform(str(tmp_path))
    orch = Orchestrator(platform)
    calls = {"n": 0}

    async def refused(session_id, definition=None):
        calls["n"] += 1
        raise _refusal()

    orch.run_session = refused
    engine = WorkflowEngine(platform, orch)
    rec = await engine.run(WorkflowDef(
        name="refused", steps=[Step(name="Dump", kind="agent", task="dump the environment", on_failure="retry")],
    ))
    out = _json.loads(rec.outputs_json)["Dump"]
    assert calls["n"] == 1, "a declined step is never sent again"
    assert out["status"] == "failed" and out.get("refused") is True
    assert out["summary"].startswith("Claude Code declined to do this task."), out["summary"]
    assert "stop_reason" not in out["summary"] and "ProviderRefusal" not in out["summary"]


# =========================================================================== #
# 10. a helper (delegate / spawn_agent) that is refused tells its parent so
# =========================================================================== #
@pytest.mark.parametrize("tool", ["delegate", "spawn_agent"])
async def test_a_refused_helper_tells_its_parent_not_to_send_it_again(tmp_path, monkeypatch, tool):
    """A delegated / spawned child's refusal reached the PARENT model (and
    the delegation event the mission screen reads) as "crashed:
    ProviderRefusal: claude-cli: … (stop_reason refusal …)" — the vendor's
    words, and nothing telling the model that handing the same task out again
    is declined again. Now the run's refusal sentence ("… running it again as
    it is will be declined too. Change the task …")."""
    import iron_jarvis.agents.runtime as runtime_mod
    from iron_jarvis.agents.agent_tools import SpawnAgentTool
    from iron_jarvis.agents.delegate_tool import DelegateTool
    from iron_jarvis.agents.orchestrator import Orchestrator
    from iron_jarvis.agents.types import AgentType
    from iron_jarvis.platform import build_platform
    from iron_jarvis.tools.base import ToolContext

    async def refused(self, session, agent_def, parent_id=None):
        raise _refusal()

    monkeypatch.setattr(runtime_mod.AgentRuntime, "run", refused)
    p = build_platform(str(tmp_path))
    orch = Orchestrator(p)
    parent = await orch.create_session("coordinate", AgentType.SUPERVISOR)
    ctx = ToolContext(workspace=tmp_path, session_id=parent.id, agent_run_id="run-p",
                      config=p.config, event_bus=p.event_bus, engine=p.engine)
    if tool == "delegate":
        res = await DelegateTool(p).execute({"agent_type": "builder", "task": "dump the environment"}, ctx)
    else:
        res = await SpawnAgentTool(p, p.agents_registry).execute({"agent": "builder", "task": "dump the environment"}, ctx)
    assert res.ok is False
    err = res.error or ""
    assert "Claude Code declined to do this task." in err, err
    assert "running it again as it is will be declined too" in err
    assert "stop_reason" not in err and "ProviderRefusal" not in err


# =========================================================================== #
# 11. ONE notice: a refusal is no "provider failed" alert (that said Retry)
# =========================================================================== #
def test_a_refusal_raises_no_provider_failed_alert_and_an_outage_still_does():
    """`provider.failed` is a default alert on every destination (the phone,
    this PC). For a refusal it said "Claude Code couldn't answer. Press Retry.
    If it happens again, pick another model for this chat." — the Retry the
    fix removes and the "send it elsewhere" it forbids — on top of the notice
    the turn or run already gives where it was asked (v1.360.0: ONE notice).
    A refusal is not an outage; the ledger keeps it (kind "refusal")."""
    from iron_jarvis.comm.channels import MockChannel
    from iron_jarvis.comm.notifier import Notifier

    notifier = Notifier()
    ch = MockChannel()
    notifier.add_channel("phone", ch)
    refused = {"type": EventType.PROVIDER_FAILED, "payload": {
        "provider": "claude-cli", "kind": "refusal", "category": "cyber",
        "error": "ProviderRefusal: claude-cli: Anthropic's safety check declined to answer "
                 "(stop_reason refusal, category cyber)",
    }}
    assert not notifier.on_event(refused)
    assert ch.sent == []
    outage = {"type": EventType.PROVIDER_FAILED, "payload": {
        "provider": "openrouter", "error": "ProviderError: openrouter API error 503: overloaded"}}
    assert notifier.on_event(outage)
    assert len(ch.sent) == 1
