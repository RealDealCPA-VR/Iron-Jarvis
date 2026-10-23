"""v1.378.0 — a provider's SAFETY REFUSAL is a typed error the user can act on.

THE DEFECT (IJ harness part 2, Phase 6 review, 2026-10-23): when Anthropic's
safety classifier declines a turn, the Messages API answers
``stop_reason: "refusal"`` (``stop_details.category``, e.g. "cyber") with NO
content. ``claude_native/transport.assemble`` treated that as handled and
returned text="", ``adapters/anthropic._finish`` mapped it to "stop", and the
chat lanes then said "The model returned an empty answer … Press Retry" —
which can never help: the classifier judges the WHOLE conversation, so every
later message (even "Say hello") is declined too, until the user goes back to
before what set it off or starts again.

NOW: every adapter that can SEE a refusal raises
:class:`~iron_jarvis.providers.adapters.base.ProviderRefusal` (kind
"refusal", the vendor's category, ``no_failover``, never transient); the
router never retries it, never falls back, never fails over — Auto included —
and never strikes the breaker, and its ledger row says ``kind: "refusal"``;
both chat lanes answer with ONE plain sentence (no "Retry") and the press
``fix_kind: "refusal"``; a Build turn's line says Rewind and is marked
``errorKind``; an agent run ends FAILED with the run's words; text that had
streamed stays, and the words say the rest was declined.

Everything is scripted: no real model, no network (the claude-cli pin runs
the REAL transport and relay against a loopback fake upstream).
"""

from __future__ import annotations

import asyncio
import json
import threading
from contextlib import asynccontextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.events import EventBus, EventType
from iron_jarvis.core.models import Session, SessionStatus
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.chat_turn import fix_action, friendly_error
from iron_jarvis.platform import build_platform
from iron_jarvis.providers import failure_words as FW
from iron_jarvis.providers.adapters.anthropic import AnthropicAdapter
from iron_jarvis.providers.adapters.base import (
    LLMAdapter,
    LLMMessage,
    LLMResponse,
    ProviderError,
    ProviderRefusal,
    refusal_of,
)
from iron_jarvis.providers.adapters.claude_native import transport
from iron_jarvis.providers.adapters.codex_native import protocol as codex_protocol
from iron_jarvis.providers.adapters.google import GoogleAdapter
from iron_jarvis.providers.adapters.mock import MockLLMAdapter
from iron_jarvis.providers.adapters.openai import OpenAIAdapter
from iron_jarvis.providers.router import ModelRouter, is_no_failover, is_transient_error
from tests.test_build_sessions_v1367 import Script, frames_of, messages_of, new_session, open_turn, wire
from tests.test_claude_native_errors_v1300 import Fake, sse
from tests.test_claude_native_errors_v1300 import _clean as _claude_env_guard  # noqa: F401 — a fixture
from tests.test_gemini_blocked_200_v1192 import _StreamHTTP

#: The words that must never answer a refusal: Retry is judged again.
RETRY_WORDS = ("Retry", "retry", "Try again", "try again")

#: The reviewer's live refusal, as the Messages API answered it.
CYBER = {"type": "refusal", "category": "cyber", "explanation": "The request was flagged."}

#: Exactly what the user reads (chat): the plain sentence, title then fix.
CHAT_TITLE = "Claude Code declined to answer this conversation"
CHAT_FIX = (
    "Its safety check on computer security flagged something in this conversation, so every new "
    "message here will be declined too. Start a new chat, or edit an earlier message to go back "
    "before the part it flagged."
)
BUILD_FIX = (
    "Its safety check on computer security flagged something in this conversation, so every new "
    "message here will be declined too. Rewind to before the message that set it off, or start a "
    "new session."
)
RUN_SUMMARY = (
    "The run failed: Claude Code declined to do this task. Its safety check on computer security "
    "flagged something in this task, so running it again as it is will be declined too. Change "
    "the task, then run it again."
)


def _refusal(*, partial: bool = False, category: str = "cyber") -> ProviderRefusal:
    return ProviderRefusal(
        "claude-cli: Anthropic's safety check declined to answer (stop_reason refusal, category cyber)",
        provider="claude-cli", category=category, explanation="The request was flagged.",
        partial=partial, whole_conversation=True,
    )


def _user(text: str = "Say hello") -> list[LLMMessage]:
    return [LLMMessage(role="user", content=text)]


async def _drain(agen) -> list[dict]:
    return [f async for f in agen]


def _no_retry(*texts: str) -> None:
    joined = " ".join(texts)
    for word in RETRY_WORDS:
        assert word not in joined, (word, joined)


# =========================================================================== #
# 1. claude-cli — the REAL transport and relay, a loopback fake Anthropic
# =========================================================================== #
def _refusal_sse(text: str = "") -> bytes:
    events: list[dict] = [{
        "type": "message_start",
        "message": {"id": "msg_r", "type": "message", "role": "assistant", "model": "claude-sonnet-5",
                    "content": [], "stop_reason": None, "stop_sequence": None,
                    "usage": {"input_tokens": 120, "output_tokens": 0}},
    }]
    if text:
        events += [
            {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text}},
            {"type": "content_block_stop", "index": 0},
        ]
    events += [
        {"type": "message_delta",
         "delta": {"stop_reason": "refusal", "stop_sequence": None, "stop_details": CYBER},
         "usage": {"output_tokens": 0}},
        {"type": "message_stop"},
    ]
    return sse(events)


class _RefusingAnthropic:
    """api.anthropic.com on loopback, answering every Messages request with a
    200 whose stop_reason is "refusal" (the reviewer's shape)."""

    def __init__(self, text: str = "") -> None:
        body = _refusal_sse(text)
        self.hits = 0
        owner = self

        class Peer(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                owner.hits += 1
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Peer)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def refusing():
    made: list[_RefusingAnthropic] = []

    def make(text: str = "") -> _RefusingAnthropic:
        up = _RefusingAnthropic(text)
        made.append(up)
        return up

    yield make
    for up in made:
        up.stop()


@pytest.mark.usefixtures("_claude_env_guard")
@pytest.mark.parametrize("streaming", [False, True])
async def test_claude_cli_refusal_is_a_typed_error_read_off_the_relay(
    tmp_path, monkeypatch, refusing, streaming
):
    up = refusing()
    fake = Fake(tmp_path, monkeypatch, scenario="relay_refusal")
    adapter = fake.adapter(up.url)
    with pytest.raises(ProviderRefusal) as info:
        if streaming:
            await _drain(adapter.stream(system="", messages=_user(), tools=[]))
        else:
            await adapter.complete(system="", messages=_user(), tools=[])
    err = info.value
    assert err.kind == "refusal" and err.category == "cyber"
    assert err.explanation == "The request was flagged."
    assert err.whole_conversation is True and err.partial is False
    assert err.no_failover is True and err.transient is False and is_transient_error(err) is False
    assert err.failed_provider == "claude-cli"
    # It was the RELAY's capture that said so (native's own words differ).
    assert "stop_reason refusal, category cyber" in str(err)
    assert up.hits == 1, "one upstream request: the refusal is never re-sent"


@pytest.mark.usefixtures("_claude_env_guard")
async def test_claude_cli_refusal_after_streamed_text_keeps_the_text_and_is_partial(
    tmp_path, monkeypatch, refusing
):
    up = refusing(text="Here is the ")
    fake = Fake(tmp_path, monkeypatch, scenario="relay_refusal", streamed="Here is the ")
    texts: list[str] = []
    with pytest.raises(ProviderRefusal) as info:
        async for frame in fake.adapter(up.url).stream(system="", messages=_user(), tools=[]):
            if frame.get("type") == "text":
                texts.append(frame["text"])
    assert texts == ["Here is the "], "the text that streamed reached the user"
    assert info.value.partial is True


def test_assemble_reads_stop_details_and_a_missing_category_is_never_guessed():
    """The pure reader: a null category stays "", the message's own text
    makes it partial."""
    err = transport.refusal_error([{"stop_reason": "refusal", "stop_details": {"type": "refusal", "category": None},
                                     "content": []}])
    assert err is not None and err.category == "" and err.partial is False
    err = transport.refusal_error([{"stop_reason": "refusal", "content": [{"type": "text", "text": "Sure, "}]}])
    assert err is not None and err.partial is True
    assert transport.refusal_error([{"stop_reason": "end_turn", "content": []}]) is None


# =========================================================================== #
# 2. Anthropic API adapter — complete() and stream()
# =========================================================================== #
class _AnthropicMessages:
    def __init__(self, content, stop_details=CYBER, deltas=()):
        self.content, self.stop_details, self.deltas = content, stop_details, list(deltas)

    def _msg(self):
        return SimpleNamespace(
            content=self.content, stop_reason="refusal",
            stop_details=SimpleNamespace(**self.stop_details) if isinstance(self.stop_details, dict)
            else self.stop_details,
            usage=SimpleNamespace(input_tokens=5, output_tokens=0),
        )

    async def create(self, **kw):
        return self._msg()

    def stream(self, **kw):
        outer = self

        class _S:
            def __aiter__(self):
                async def gen():
                    for piece in outer.deltas:
                        yield SimpleNamespace(type="content_block_delta",
                                              delta=SimpleNamespace(type="text_delta", text=piece))
                return gen()

            async def get_final_message(self):
                return outer._msg()

        @asynccontextmanager
        async def cm():
            yield _S()

        return cm()


def _anthropic(monkeypatch, **kw) -> AnthropicAdapter:
    a = AnthropicAdapter(model="claude-opus-4-8", api_key="sk-ant-test")
    monkeypatch.setattr(a, "_client", lambda: SimpleNamespace(messages=_AnthropicMessages(**kw)))
    return a


async def test_anthropic_api_refusal_is_typed_in_complete_and_stream(monkeypatch):
    a = _anthropic(monkeypatch, content=[])
    with pytest.raises(ProviderRefusal) as info:
        await a.complete(system="", messages=_user(), tools=[])
    assert info.value.category == "cyber" and info.value.partial is False
    assert info.value.failed_provider == "anthropic" and info.value.whole_conversation is True
    with pytest.raises(ProviderRefusal) as info:
        await _drain(a.stream(system="", messages=_user(), tools=[]))
    assert info.value.partial is False
    # After text streamed: partial, the text already yielded.
    b = _anthropic(monkeypatch, content=[SimpleNamespace(type="text", text="Sure, ")], deltas=["Sure, "])
    frames: list[dict] = []
    with pytest.raises(ProviderRefusal) as info:
        async for f in b.stream(system="", messages=_user(), tools=[]):
            frames.append(f)
    assert [f["text"] for f in frames if f["type"] == "text"] == ["Sure, "]
    assert info.value.partial is True


# =========================================================================== #
# 3. OpenAI (Chat Completions + the ChatGPT Responses backend)
# =========================================================================== #
def _chat(message: dict, finish: str) -> dict:
    return {"choices": [{"message": message, "finish_reason": finish}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 0}}


def test_openai_content_filter_and_refusal_field_are_typed():
    with pytest.raises(ProviderRefusal) as info:
        OpenAIAdapter._parse(_chat({"content": None}, "content_filter"))
    assert info.value.partial is False and info.value.category == ""
    assert info.value.whole_conversation is False, "OpenAI declined THIS answer"
    with pytest.raises(ProviderRefusal) as info:
        OpenAIAdapter._parse(_chat({"content": "The first half", "role": "assistant"}, "content_filter"))
    assert info.value.partial is True
    with pytest.raises(ProviderRefusal) as info:
        OpenAIAdapter._parse(_chat({"content": None, "refusal": "I can't help with that."}, "stop"))
    assert info.value.explanation == "I can't help with that." and info.value.partial is False
    # An ordinary answer is untouched; a refusal string beside a real answer is the answer.
    assert OpenAIAdapter._parse(_chat({"content": "hi"}, "stop")).text == "hi"
    assert OpenAIAdapter._parse(_chat({"content": "hi", "refusal": "no"}, "stop")).text == "hi"


class _Lines:
    def __init__(self, chunks: list[dict]):
        self.lines = ["data: " + json.dumps(c) for c in chunks] + ["data: [DONE]"]

    async def aiter_lines(self):
        for line in self.lines:
            yield line


async def test_openai_stream_refusal_after_text_is_partial():
    adapter = OpenAIAdapter(api_key="sk-test")
    chunks = [
        {"choices": [{"delta": {"content": "Step one"}}]},
        {"choices": [{"delta": {}, "finish_reason": "content_filter"}]},
    ]
    frames: list[dict] = []
    with pytest.raises(ProviderRefusal) as info:
        async for f in adapter._consume_chat_stream(_Lines(chunks)):
            frames.append(f)
    assert [f["text"] for f in frames if f["type"] == "text"] == ["Step one"]
    assert info.value.partial is True
    # A streamed `refusal` field with no answer.
    with pytest.raises(ProviderRefusal):
        await _drain(adapter._consume_chat_stream(_Lines([
            {"choices": [{"delta": {"refusal": "I can't "}}]},
            {"choices": [{"delta": {"refusal": "help."}, "finish_reason": "stop"}]},
        ])))


def test_openai_responses_backend_content_filter_and_refusal_part():
    incomplete = {"type": "response.incomplete", "response": {
        "status": "incomplete", "incomplete_details": {"reason": "content_filter"}, "output": []}}
    with pytest.raises(ProviderRefusal):
        OpenAIAdapter._parse_sse("data: " + json.dumps(incomplete))
    completed = {"type": "response.completed", "response": {"output": [
        {"type": "message", "content": [{"type": "refusal", "refusal": "I can't help with that."}]}]}}
    with pytest.raises(ProviderRefusal) as info:
        OpenAIAdapter._parse_sse("data: " + json.dumps(completed))
    assert info.value.explanation == "I can't help with that."


# =========================================================================== #
# 4. Gemini — a SAFETY block is a refusal; RECITATION keeps the old error
# =========================================================================== #
def test_gemini_safety_blocks_are_refusals_and_other_endings_are_not():
    with pytest.raises(ProviderRefusal) as info:
        GoogleAdapter._parse({"promptFeedback": {"blockReason": "SAFETY"}, "candidates": []})
    assert info.value.whole_conversation is True, "the prompt (the conversation) was blocked"
    assert "SAFETY" in str(info.value) and info.value.blocked is True
    with pytest.raises(ProviderRefusal) as info:
        GoogleAdapter._parse({"candidates": [{"content": {"parts": []}, "finishReason": "PROHIBITED_CONTENT"}]})
    assert info.value.whole_conversation is False
    # After text: partial.
    with pytest.raises(ProviderRefusal) as info:
        GoogleAdapter._parse({"candidates": [{"content": {"parts": [{"text": "Part"}]},
                                              "finishReason": "SAFETY"}]})
    assert info.value.partial is True
    # Not a safety refusal: the old typed "no usable content" error, unchanged.
    with pytest.raises(ProviderError) as info:
        GoogleAdapter._parse({"candidates": [{"content": {"parts": []}, "finishReason": "RECITATION"}]})
    assert not isinstance(info.value, ProviderRefusal)


async def test_gemini_stream_refusal_after_text_keeps_the_text():
    http = _StreamHTTP([
        {"candidates": [{"content": {"parts": [{"text": "bon"}]}}]},
        {"candidates": [{"content": {"parts": []}, "finishReason": "SAFETY"}]},
    ])
    frames: list[dict] = []
    with pytest.raises(ProviderRefusal) as info:
        async for f in GoogleAdapter(api_key="g", http=http).stream(system="", messages=_user(), tools=[]):
            frames.append(f)
    assert [f["text"] for f in frames if f["type"] == "text"] == ["bon"]
    assert info.value.partial is True and http.posts == [], "never re-spent on the non-streaming path"


# =========================================================================== #
# 5. Codex app-server — its own policy variants
# =========================================================================== #
def test_codex_policy_variants_are_refusals():
    f = codex_protocol.turn_failure({"message": "flagged", "codexErrorInfo": "cyberPolicy"}, emitted=False)
    assert isinstance(f.error, ProviderRefusal) and f.error.category == "cyber"
    assert f.signal is None, "never an Iron-Proxy account signal"
    f = codex_protocol.turn_failure({"message": "x", "codexErrorInfo": "misalignmentPolicyViolation"},
                                    emitted=True)
    assert isinstance(f.error, ProviderRefusal) and f.error.category == "" and f.error.partial is True


# =========================================================================== #
# 6. the router: no retry, no fallback, no failover (Auto too), no strike
# =========================================================================== #
class _Refusing(LLMAdapter):
    def __init__(self, provider: str, exc: Exception, head: str = ""):
        self.provider, self.model, self.exc, self.head, self.calls = provider, "m", exc, head, 0

    async def complete(self, *, system, messages, tools, **kw):
        self.calls += 1
        raise self.exc

    async def stream(self, *, system, messages, tools, **kw):
        self.calls += 1
        if self.head:
            yield {"type": "text", "text": self.head}
        raise self.exc


class _Healthy(LLMAdapter):
    def __init__(self, provider: str):
        self.provider, self.model, self.calls = provider, "ok-model", 0

    async def complete(self, *, system, messages, tools, **kw):
        self.calls += 1
        return LLMResponse(text=f"answer from {self.provider}", tool_calls=[], usage={})


class _Mgr:
    def __init__(self, adapters):
        self.adapters = adapters

    def available(self, provider):
        return provider in self.adapters

    def has_available_api_provider(self):
        return True

    def get(self, provider, model=None):
        return self.adapters[provider]


class _Bus(EventBus):
    def __init__(self):
        super().__init__()
        self.seen: list[tuple[str, dict]] = []

    async def publish(self, type, payload=None, session_id=None):
        self.seen.append((type, dict(payload or {})))
        return await super().publish(type, payload, session_id)

    def of(self, etype):
        return [p for t, p in self.seen if t == etype]


async def _ask(router, lane: str, **kw):
    if lane == "complete":
        return await router.complete(system="", messages=_user(), tools=[], **kw)
    return await _drain(router.stream(system="", messages=_user(), tools=[], **kw))


@pytest.mark.parametrize("lane", ["complete", "stream"])
@pytest.mark.parametrize("route", ["explicit", "default", "auto"])
async def test_the_router_never_moves_retries_or_strikes_a_refusal(lane, route):
    refused = _Refusing("claude-cli", _refusal())
    others = {p: _Healthy(p) for p in ("anthropic", "codex-cli", "openai")}
    bus = _Bus()

    async def auto_route(system, messages, tools, task_class):
        return {"provider": "claude-cli", "model": None, "tier": "t"}

    default = {"explicit": "anthropic", "default": "claude-cli", "auto": "auto"}[route]
    router = ModelRouter(_Mgr({"claude-cli": refused, **others}), default_provider=default,
                         event_bus=bus, auto_route=auto_route)
    kw = {"provider": "claude-cli"} if route == "explicit" else {}
    for _ in range(4):  # the breaker opens after 3 strikes: a refusal strikes nothing
        with pytest.raises(ProviderRefusal):
            await _ask(router, lane, **kw)
    assert refused.calls == 4, "never retried on the same adapter"
    assert all(o.calls == 0 for o in others.values()), "no fallback, no failover — Auto included"
    assert router.health.allow("claude-cli"), "the provider answered: no breaker strike"
    assert bus.of(EventType.PROVIDER_FAILOVER) == []
    failed = bus.of(EventType.PROVIDER_FAILED)
    assert failed and all(p["kind"] == "refusal" and p["category"] == "cyber" for p in failed)
    assert all(p["provider"] == "claude-cli" for p in failed)


async def test_a_refusal_after_the_first_words_is_raised_as_itself_with_no_strike():
    refused = _Refusing("claude-cli", _refusal(partial=True), head="Here is ")
    bus = _Bus()
    router = ModelRouter(_Mgr({"claude-cli": refused, "anthropic": _Healthy("anthropic")}),
                         default_provider="claude-cli", event_bus=bus)
    for _ in range(4):
        frames: list[dict] = []
        with pytest.raises(ProviderRefusal):
            async for f in router.stream(system="", messages=_user(), tools=[]):
                frames.append(f)
        assert [f["text"] for f in frames if f.get("type") == "text"] == ["Here is "]
    assert router.health.allow("claude-cli")
    failed = bus.of(EventType.PROVIDER_FAILED)
    assert failed and all(p.get("partial") is True and p["kind"] == "refusal" for p in failed)


def test_a_refusal_is_no_failover_never_transient_and_found_through_a_wrapper():
    err = _refusal()
    assert is_no_failover(err) and not is_transient_error(err)
    try:
        try:
            raise err
        except ProviderRefusal as inner:
            raise RuntimeError("wrapped") from inner
    except RuntimeError as outer:
        assert refusal_of(outer) is err
    assert refusal_of(ProviderError("x", status_code=500)) is None


# =========================================================================== #
# 7. the words — one sentence, no Retry, the category only when known
# =========================================================================== #
def test_the_words_per_surface_and_the_press():
    words = friendly_error(_refusal())
    assert (words["title"], words["fix"]) == (CHAT_TITLE, CHAT_FIX)
    _no_retry(words["title"], words["fix"])
    assert "stop_reason refusal" in words["technical"], "the vendor's words only in technical"
    assert fix_action(_refusal()) == {"fix_kind": "refusal", "fix_provider": "claude-cli"}
    # Build: the way out is Rewind.
    token = FW.set_build_surface()
    try:
        assert FW.friendly_error(_refusal())["fix"] == BUILD_FIX
    finally:
        FW.reset_surface(token)
    # A category with no plain words (or none) is left out, never guessed.
    odd = FW.friendly_error(_refusal(category="frontier_llm"))["fix"]
    assert odd.startswith("Its safety check flagged something") and "frontier" not in odd
    # Partial: the reply above is incomplete.
    part = FW.friendly_error(_refusal(partial=True))
    assert part["title"] == "Claude Code declined to finish this answer"
    assert part["fix"].startswith("The reply above is incomplete. ")
    # Not the whole conversation (OpenAI-style): says so, honestly.
    single = ProviderRefusal("x", provider="openai")
    assert "may decline new messages" in FW.friendly_error(single)["fix"]


# =========================================================================== #
# 8. both chat lanes, lock-step: the sentence and the press, no Retry
# =========================================================================== #
def _wire_refusal(platform, monkeypatch, exc, *, head: str = ""):
    async def fake_complete(**kw):
        raise exc

    async def fake_stream(**kw):
        if head:
            yield {"type": "text", "text": head}
        raise exc

    monkeypatch.setattr(platform.router, "complete", fake_complete)
    monkeypatch.setattr(platform.router, "stream", fake_stream)


def _sse_frames(raw: str) -> list[tuple[str, dict]]:
    return frames_of(raw.replace("\r\n", "\n"))


def test_the_stream_lane_error_frame_is_the_refusal_sentence(tmp_path, monkeypatch):
    client = TestClient(create_app(str(tmp_path)))
    _wire_refusal(client.app.state.platform, monkeypatch, _refusal())
    r = client.post("/chat/stream", json={"messages": [{"role": "user", "content": "Say hello"}]})
    assert r.status_code == 200, r.text
    [err] = [d for ev, d in _sse_frames(r.text) if ev == "error"]
    assert (err["title"], err["fix"]) == (CHAT_TITLE, CHAT_FIX)
    assert err["fix_kind"] == "refusal" and err["fix_provider"] == "claude-cli"
    _no_retry(err["title"], err["fix"])
    assert "empty answer" not in r.text


def test_the_post_lane_502_carries_the_same_words(tmp_path, monkeypatch):
    client = TestClient(create_app(str(tmp_path)))
    _wire_refusal(client.app.state.platform, monkeypatch, _refusal())
    r = client.post("/chat", json={"messages": [{"role": "user", "content": "Say hello"}]})
    assert r.status_code == 502, r.text
    body = r.json()
    assert (body["title"], body["fix"]) == (CHAT_TITLE, CHAT_FIX)
    assert body["fix_kind"] == "refusal" and body["fix_provider"] == "claude-cli"
    _no_retry(body["title"], body["fix"])


def test_a_refusal_after_streamed_text_keeps_the_text_and_says_the_rest_was_declined(tmp_path, monkeypatch):
    client = TestClient(create_app(str(tmp_path)))
    _wire_refusal(client.app.state.platform, monkeypatch, _refusal(partial=True), head="Here is the plan: ")
    r = client.post("/chat/stream", json={"messages": [{"role": "user", "content": "plan it"}]})
    frames = _sse_frames(r.text)
    assert [d["text"] for ev, d in frames if ev == "token"] == ["Here is the plan: "]
    [err] = [d for ev, d in frames if ev == "error"]
    assert err["title"] == "Claude Code declined to finish this answer"
    assert err["fix"].startswith("The reply above is incomplete. ")
    _no_retry(err["title"], err["fix"])


@pytest.mark.parametrize("lane", ["stream", "post"])
def test_a_declined_final_answer_nudge_says_so_instead_of_press_retry(tmp_path, monkeypatch, lane):
    """The model was reached and wrote nothing, then the one "write your
    answer" nudge was DECLINED: the reply names the refusal, never "Press
    Retry" (lock-step: both lanes)."""
    client = TestClient(create_app(str(tmp_path)))
    platform = client.app.state.platform
    calls = {"n": 0}

    async def fake_complete(**kw):
        calls["n"] += 1
        if calls["n"] == 1:
            from iron_jarvis.providers.router import RouteResult

            return RouteResult(LLMResponse(text="", usage={"input_tokens": 1, "output_tokens": 0}),
                               "claude-cli", "m")
        raise _refusal()

    async def fake_stream(**kw):
        calls["n"] += 1
        if calls["n"] == 1:
            yield {"type": "final", "response": LLMResponse(text="", usage={"input_tokens": 1, "output_tokens": 0}),
                   "provider": "claude-cli", "model": "m"}
            return
        raise _refusal()

    monkeypatch.setattr(platform.router, "complete", fake_complete)
    monkeypatch.setattr(platform.router, "stream", fake_stream)
    body = {"messages": [{"role": "user", "content": "Say hello"}]}
    if lane == "stream":
        r = client.post("/chat/stream", json=body)
        [done] = [d for ev, d in _sse_frames(r.text) if ev == "done"]
        reply = done["reply"]
    else:
        r = client.post("/chat", json=body)
        reply = r.json()["reply"]
    assert reply.startswith(f"{CHAT_TITLE}. {CHAT_FIX}"), reply
    assert "Press Retry" not in reply and calls["n"] == 2


# =========================================================================== #
# 9. a Build turn (the IJ harness): Rewind words, the line marked
# =========================================================================== #
class _RefusingScript(Script):
    def __init__(self, exc, head: str = ""):
        super().__init__([])
        self.exc, self.head = exc, head

    async def stream(self, **kw):
        await self._next(kw.get("system", ""), kw.get("messages", ()), kw.get("tools"))
        if self.head:
            yield {"type": "text", "text": self.head}
        raise self.exc

    async def complete(self, **kw):
        await self._next(kw.get("system", ""), kw.get("messages", ()), kw.get("tools"))
        raise self.exc


@pytest.mark.parametrize("head", ["", "I'll start by "])
async def test_a_build_turn_refusal_says_rewind_and_marks_the_line(tmp_path, head):
    app = create_app(str(tmp_path / "home"))
    folder = tmp_path / "work"
    folder.mkdir()
    wire(app, _RefusingScript(_refusal(partial=bool(head)), head=head))
    sid = new_session(app, folder)["id"]
    status, raw = await open_turn(app, sid, {"text": "Say hello", "turn_id": "t-refused"})
    assert status == 200, raw
    frames = frames_of(raw)
    [err] = [d for ev, d in frames if ev == "error"]
    assert err["fix_kind"] == "refusal"
    assert "Rewind to before the message that set it off, or start a new session." in err["fix"]
    _no_retry(err["title"], err["fix"])
    line = [m for m in messages_of(app, sid) if m.get("role") == "assistant"][-1]
    assert line["errorKind"] == "refusal"
    assert line["error"] == f"{err['title']}. {err['fix']}", "title. fix — the chat's join"
    if head:
        assert line["content"] == head and line["interrupted"] is True, "the streamed text is kept"


# =========================================================================== #
# 10. an agent run ends FAILED with the run's sentence
# =========================================================================== #
@pytest.mark.parametrize("head", ["", "Starting: "])
def test_an_agent_run_that_is_refused_ends_failed_with_the_sentence(tmp_path, head):
    p = build_platform(str(tmp_path))

    class Refusing(MockLLMAdapter):
        async def complete(self, *, system, messages, tools, **kw):
            raise _refusal()

        async def stream(self, *, system, messages, tools, **kw):
            if head:
                yield {"type": "text", "text": head}
            raise _refusal(partial=bool(head))

    p.providers.register("mock", lambda model=None: Refusing())
    orch = Orchestrator(p)

    async def go():
        s = await orch.create_session("dump the shell environment", provider="mock", model="mock-1")
        # run_session re-raises a run's failure after finalizing it; the row
        # is what the user reads (Sessions, the bell, the phone).
        with pytest.raises(ProviderRefusal):
            await orch.run_session(s.id)
        return s.id

    sid = asyncio.run(go())
    with session_scope(p.engine) as db:
        s = db.get(Session, sid)
        db.expunge(s)
    assert s.status == SessionStatus.FAILED, (s.status, s.summary)
    if head:
        assert s.summary.startswith(
            "The run failed: Claude Code declined to finish this task. Its answer is incomplete."
        ), s.summary
    else:
        assert s.summary == RUN_SUMMARY, s.summary
    _no_retry(s.summary.replace("run it again", ""))
