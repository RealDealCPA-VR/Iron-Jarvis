"""v1.335.0 — a "too long for this model" refusal is shrunk and tried once.

From pi-durable: "when a provider rejects a request because the context is
too long, generation compacts and retries once." Before this the only
defence was the planner's proactive budget, so a model whose real window was
smaller than the app believed (an unannounced proxy limit, a mis-pinned
endpoint) failed the turn outright, and the next turn failed the same way.

Now both lanes (lock-step) hand their model call a ``refit``: on a refusal
that says the request was too long, before any word was shown, the window
the refusal names (else 3/4 of what was sent) is LEARNED for that model, the
oldest history is left out (the question and the current round never are),
the model is told so, and the call is made ONCE more. The learned window
then steers every later turn's planner.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import iron_jarvis.daemon.chat_turn as ct
from iron_jarvis.daemon.app import create_app
from iron_jarvis.providers.adapters.base import LLMResponse
from iron_jarvis.providers.router import RouteResult


@pytest.fixture(autouse=True)
def _forget_learned():
    ct._LEARNED_WINDOWS.clear()
    yield
    ct._LEARNED_WINDOWS.clear()


@pytest.mark.parametrize(
    "text",
    [
        "This model's maximum context length is 8192 tokens. However, your messages resulted in 9000 tokens.",
        "Error code: 400 - {'error': {'code': 'context_length_exceeded'}}",
        "prompt is too long: 213000 tokens > 200000 maximum",
        "the request exceeds the available context size, try increasing it",
        "Input is too long for requested model.",
        "Context limit reached",
        "the input length exceeds the context length",
    ],
)
def test_overflow_refusals_are_recognised(text):
    assert ct.is_context_overflow(RuntimeError(text))


@pytest.mark.parametrize(
    "text",
    [
        "rate limited: 429 Too Many Requests",
        "401 unauthorized",
        "connection refused",
        "the context of this question is unclear",
        "model 'x' not found",
    ],
)
def test_other_failures_are_not(text):
    assert not ct.is_context_overflow(RuntimeError(text))


def test_the_named_window_is_read_off_the_refusal():
    assert ct.overflow_limit(RuntimeError("maximum context length is 8,192 tokens")) == 8192
    assert ct.overflow_limit(RuntimeError("prompt is too long: 213000 tokens > 200000 maximum")) == 200000
    assert ct.overflow_limit(RuntimeError('{"n_ctx": 4096, "error": "exceeds context"}')) == 4096
    assert ct.overflow_limit(RuntimeError("context limit reached")) is None


def _route(text: str) -> RouteResult:
    return RouteResult(
        provider="mock", model="m", response=LLMResponse(text=text, finish_reason="stop")
    )


def _history(n: int, size: int = 4000) -> list[dict]:
    msgs = []
    for i in range(n):
        msgs.append({"role": "user", "content": f"question {i} " + "x" * size})
        msgs.append({"role": "assistant", "content": f"answer {i} " + "y" * size})
    msgs.append({"role": "user", "content": "THE QUESTION"})
    return msgs


def _chars(messages) -> int:
    return sum(len(getattr(m, "content", "") or "") for m in messages)


def test_post_lane_shrinks_and_retries_once_then_learns_the_window(tmp_path, monkeypatch):
    client = TestClient(create_app(str(tmp_path)))
    platform = client.app.state.platform
    calls: list[dict] = []

    async def fake_complete(*, provider=None, model=None, system, messages, tools, task_class, **kw):
        calls.append({"system": system, "messages": list(messages)})
        if _chars(messages) > 12_000:
            raise RuntimeError(
                "This model's maximum context length is 6000 tokens. Please reduce the length of the messages."
            )
        return _route("fits now")

    monkeypatch.setattr(platform.router, "complete", fake_complete)
    r = client.post("/chat", json={"messages": _history(6)})
    assert r.status_code == 200, r.text
    assert r.json()["reply"] == "fits now"
    assert len(calls) == 2
    first, second = calls
    assert len(second["messages"]) < len(first["messages"])
    # The question always survives, and the model is told what happened.
    assert second["messages"][-1].content == "THE QUESTION"
    assert second["messages"][0].role == "user"
    assert ct.OVERFLOW_NOTE in second["system"]
    # Learned: the next turn's planner plans against the refusal's window.
    d = client.app.state.d
    assert ct._context_window_source(d, "", "") == (6000, "learned")


def test_stream_lane_shrinks_and_retries_once(tmp_path, monkeypatch):
    client = TestClient(create_app(str(tmp_path)))
    platform = client.app.state.platform
    sent: list[int] = []

    async def fake_stream(*, provider=None, model=None, system, messages, tools, task_class, **kw):
        sent.append(len(messages))
        if _chars(messages) > 12_000:
            raise RuntimeError("prompt is too long: 9000 tokens > 6000 maximum")
        yield {"type": "text", "text": "streamed fine"}
        yield {"type": "final", "response": LLMResponse(text="streamed fine", finish_reason="stop"),
               "provider": "mock", "model": "m"}

    monkeypatch.setattr(platform.router, "stream", fake_stream)
    r = client.post("/chat/stream", json={"messages": _history(6)})
    assert "event: done" in r.text, r.text[-600:]
    assert "streamed fine" in r.text
    assert len(sent) == 2 and sent[1] < sent[0]


def test_a_refusal_that_is_not_about_length_is_not_retried(tmp_path, monkeypatch):
    client = TestClient(create_app(str(tmp_path)))
    platform = client.app.state.platform
    n = {"calls": 0}

    async def fake_complete(**kw):
        n["calls"] += 1
        raise RuntimeError("401 unauthorized")

    monkeypatch.setattr(platform.router, "complete", fake_complete)
    r = client.post("/chat", json={"messages": _history(6)})
    assert r.status_code >= 400
    assert n["calls"] == 1
    assert ct._LEARNED_WINDOWS == {}


def test_a_second_refusal_is_the_answer(tmp_path, monkeypatch):
    client = TestClient(create_app(str(tmp_path)))
    platform = client.app.state.platform
    n = {"calls": 0}

    async def fake_complete(**kw):
        n["calls"] += 1
        raise RuntimeError("maximum context length is 6000 tokens")

    monkeypatch.setattr(platform.router, "complete", fake_complete)
    r = client.post("/chat", json={"messages": _history(6)})
    assert r.status_code >= 400
    assert "maximum context length" in r.text
    assert n["calls"] == 2  # once, then the refusal stands


def test_once_words_are_shown_a_failure_is_never_retried(tmp_path, monkeypatch):
    client = TestClient(create_app(str(tmp_path)))
    platform = client.app.state.platform
    n = {"calls": 0}

    async def fake_stream(**kw):
        n["calls"] += 1
        yield {"type": "text", "text": "half an ans"}
        raise RuntimeError("context length exceeded")

    monkeypatch.setattr(platform.router, "stream", fake_stream)
    r = client.post("/chat/stream", json={"messages": _history(6)})
    assert "event: error" in r.text
    assert n["calls"] == 1


def test_nothing_to_leave_out_means_no_retry(tmp_path, monkeypatch):
    """A one-message turn that is itself too long cannot be shrunk: the same
    request again would only fail again."""
    client = TestClient(create_app(str(tmp_path)))
    platform = client.app.state.platform
    n = {"calls": 0}

    async def fake_complete(**kw):
        n["calls"] += 1
        raise RuntimeError("maximum context length is 2000 tokens")

    monkeypatch.setattr(platform.router, "complete", fake_complete)
    r = client.post("/chat", json={"messages": [{"role": "user", "content": "z" * 30_000}]})
    assert r.status_code >= 400
    assert n["calls"] == 1
