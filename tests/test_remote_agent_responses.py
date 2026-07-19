"""Remote agents must speak the OpenAI **Responses API**, not only chat.

Live-hit 2026-07-19: a user registered a remote Hermes agent with correct
credentials and the Test button returned

    remote returned HTTP 400: {"error": {"message": "Missing 'input' field",
    "type": "invalid_request_error", ...}}

Nothing was wrong with the credentials. The endpoint speaks the Responses API
(``input``), and Iron Jarvis only knew ``chat/completions`` (``messages``).
Two fixes are covered here: the missing dialect, and turning that specific 4xx
into the setting that fixes it instead of a bare status code.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from iron_jarvis.agents.remote import (
    KINDS,
    RemoteAgentRecord,
    _dialect_hint,
    _responses_text,
)


class _Resp:
    def __init__(self, status=200, payload=None, text=""):
        self.status_code = status
        self._payload = payload
        self.text = text or (json.dumps(payload) if payload is not None else "")

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


def _call(record, sent, resp):
    """Drive RemoteAgentRegistry.run with httpx.AsyncClient stubbed out.

    ``run`` imports httpx INSIDE the function, so the patch has to land on the
    httpx module itself. It never touches ``self``, so an unbound call keeps
    the test free of a database engine.
    """
    import httpx

    from iron_jarvis.agents.remote import RemoteAgentRegistry

    class _Client:
        def __init__(self, **_kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, headers=None):  # noqa: A002
            sent["url"] = url
            sent["payload"] = json
            sent["headers"] = headers or {}
            return resp

    original = httpx.AsyncClient
    httpx.AsyncClient = _Client
    try:
        return asyncio.run(
            RemoteAgentRegistry.run(None, record, "do the thing", lambda _n: "")
        )
    finally:
        httpx.AsyncClient = original


def _record(kind, base_url="https://agent.example.com/v1", model="hermes-1"):
    return RemoteAgentRecord(
        name="hermes", base_url=base_url, kind=kind, model=model, secret_name=""
    )


# --- the new dialect ------------------------------------------------------------


def test_openai_responses_is_a_supported_kind():
    assert "openai-responses" in KINDS


def test_responses_request_sends_input_not_messages():
    sent = {}
    _call(
        _record("openai-responses"),
        sent,
        _Resp(payload={"output_text": "done"}),
    )
    assert sent["url"] == "https://agent.example.com/v1/responses"
    assert sent["payload"]["input"] == "do the thing"
    assert "messages" not in sent["payload"]  # the exact cause of the live 400
    assert sent["payload"]["model"] == "hermes-1"


def test_a_base_url_already_ending_in_responses_is_not_doubled():
    sent = {}
    _call(
        _record("openai-responses", base_url="https://agent.example.com/v1/responses"),
        sent,
        _Resp(payload={"output_text": "ok"}),
    )
    assert sent["url"] == "https://agent.example.com/v1/responses"


def test_chat_kind_is_unchanged():
    sent = {}
    _call(
        _record("openai-chat"),
        sent,
        _Resp(payload={"choices": [{"message": {"content": "hi"}}]}),
    )
    assert sent["url"].endswith("/chat/completions")
    assert sent["payload"]["messages"][0]["content"] == "do the thing"
    assert "input" not in sent["payload"]


# --- reply parsing --------------------------------------------------------------


def test_reads_the_output_text_convenience_field():
    out = _call(_record("openai-responses"), {}, _Resp(payload={"output_text": "hello"}))
    assert out["ok"] is True and out["result"] == "hello"


def test_reads_the_structured_output_items():
    payload = {
        "output": [
            {
                "type": "message",
                "content": [
                    {"type": "output_text", "text": "part one "},
                    {"type": "output_text", "text": "part two"},
                ],
            }
        ]
    }
    out = _call(_record("openai-responses"), {}, _Resp(payload=payload))
    assert out["result"] == "part one part two"


def test_reasoning_and_unknown_items_are_skipped_not_guessed_at():
    payload = {
        "output": [
            {"type": "reasoning", "summary": ["internal"]},
            {"type": "message", "content": [{"type": "output_text", "text": "answer"}]},
        ]
    }
    assert _responses_text(payload) == "answer"


def test_an_empty_reply_is_an_honest_failure_not_a_blank_success():
    out = _call(_record("openai-responses"), {}, _Resp(payload={"output": []}))
    assert out["ok"] is False
    assert "output_text" in out["detail"]


@pytest.mark.parametrize("payload", [{}, {"output_text": ""}, None, [], "text"])
def test_responses_text_never_raises_on_odd_payloads(payload):
    assert _responses_text(payload) == ""


# --- the hint that should have saved the user the guess -------------------------


def test_missing_input_400_names_the_setting_that_fixes_it():
    """The exact live body."""
    body = json.dumps(
        {
            "error": {
                "message": "Missing 'input' field",
                "type": "invalid_request_error",
                "param": None,
                "code": None,
            }
        }
    )
    out = _call(_record("openai-chat"), {}, _Resp(status=400, text=body))
    assert out["ok"] is False
    assert "openai-responses" in out["detail"]
    assert "HTTP 400" in out["detail"]  # the raw fact is still reported


def test_the_mirror_case_points_back_at_chat():
    body = json.dumps({"error": {"message": "Missing 'messages' field"}})
    out = _call(_record("openai-responses"), {}, _Resp(status=400, text=body))
    assert "openai-chat" in out["detail"]


def test_a_task_webhook_getting_an_openai_error_is_told_it_is_openai_shaped():
    hint = _dialect_hint("http-task", '{"error":{"message":"Missing \'input\' field"}}')
    assert "openai-responses" in hint


def test_an_unrelated_4xx_gets_no_invented_hint():
    """A 401 is not a dialect problem — inventing one would send the user off
    fixing the wrong thing."""
    out = _call(_record("openai-chat"), {}, _Resp(status=401, text="Unauthorized"))
    assert out["ok"] is False
    assert "openai-responses" not in out["detail"]
    assert "HTTP 401" in out["detail"]
