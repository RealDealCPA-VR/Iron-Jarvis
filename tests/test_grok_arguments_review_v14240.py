"""v1.424.0 (review of audit D7): a WHOLE Grok reply's tool-call arguments go
through the ONE recovery ladder (``core/jsonish.loads_object`` + the
``{"arguments": "<json>"}`` envelope peel — CLAUDE.md v1.225.0 / v1.228.0),
and arguments nothing can read are NEVER run as ``{}``.

The first cut read them with a bare ``json.loads`` and turned anything else
into ``{}``: a trailing comma (a local model's commonest slip) lost every
argument, an envelope reached the tool as ``{"arguments": "..."}``, and an
unreadable call ran with INVENTED empty arguments — a tool whose every
argument is optional (``list_files``, ``app_health`` …) simply ran.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from iron_jarvis.providers import failure_words
from iron_jarvis.providers.adapters import grok_cli as G
from iron_jarvis.providers.adapters.base import LLMMessage, ProviderError
from iron_jarvis.providers.router import is_no_failover
from tests.test_grok_output_budget_v14240 import FakeProxy, _adapter, _drain, _sse


def _reply(*calls: dict, text: str = "Working on it.") -> str:
    """A whole Responses reply (``response.completed``) holding ``calls``."""
    output = [{"type": "message", "content": [{"type": "output_text", "text": text}]}]
    output += [{"type": "function_call", "status": "completed", "call_id": f"c{i}", **c}
               for i, c in enumerate(calls)]
    return _sse([{"type": "response.output_text.delta", "delta": text},
                 {"type": "response.completed", "response": {
                     "status": "completed", "usage": {"input_tokens": 3, "output_tokens": 4},
                     "output": output}}])


def _complete(body: str):
    return asyncio.run(_adapter(FakeProxy(body)).complete(
        system="", messages=[LLMMessage("user", "hi")], tools=[{"name": "read_file"}]))


@pytest.mark.parametrize("raw, want", [
    ('{"path": "a.py",}', {"path": "a.py"}),  # a trailing comma
    ("```json\n{\"path\": \"a.py\"}\n```", {"path": "a.py"}),  # a fence
    ("{'path': 'a.py'}", {"path": "a.py"}),  # single quotes
    (json.dumps({"arguments": json.dumps({"path": "a.py"})}), {"path": "a.py"}),  # the envelope
    (json.dumps({"path": "a.py"}), {"path": "a.py"}),  # plain JSON, unchanged
    ("", {}),  # a call that sent no arguments at all
])
def test_a_whole_replys_arguments_go_through_the_recovery_ladder(raw, want):
    res = _complete(_reply({"name": "read_file", "arguments": raw}))
    assert [(c.name, c.arguments) for c in res.tool_calls] == [("read_file", want)]
    assert res.finish_reason == "tool_use" and res.cut_calls == []


@pytest.mark.parametrize("raw", ["not json at all", "[1, 2]", '"a.py"', "{{{"])
def test_unreadable_arguments_are_never_run_as_empty_ones(raw):
    """Nothing is returned to run — the reply ends in one plain sentence."""
    with pytest.raises(ProviderError) as info:
        _complete(_reply({"name": "list_files", "arguments": raw},
                         {"name": "read_file", "arguments": json.dumps({"path": "a.py"})}))
    err = info.value
    assert "nothing was run" in str(err)
    # Said in words, never moved to another model (D1), never retried as transient.
    assert err.plain_words is True and is_no_failover(err) and err.transient is False
    words = failure_words.friendly_error(err)
    assert "could not be read" in words["title"] and "grok-cli" not in words["title"]


def test_the_stream_never_hands_on_an_unreadable_call():
    proxy = FakeProxy(_reply({"name": "list_files", "arguments": "not json at all"}))
    with pytest.raises(ProviderError, match="nothing was run"):
        asyncio.run(_drain(_adapter(proxy).stream(
            system="", messages=[LLMMessage("user", "hi")], tools=[{"name": "list_files"}])))


def test_a_cut_reply_keeps_its_strict_reading():
    """At the output limit the ladder is NOT used (it could pull an object out
    of half-written content): a lenient-only call is cut, never run."""
    body = _sse([{"type": "response.incomplete", "response": {
        "status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}, "usage": {},
        "output": [{"type": "function_call", "status": "completed", "call_id": "c1", "name": "write_file",
                    "arguments": '{"path": "a.py", "content": "x",}'}]}}])
    res = _complete(body)
    assert res.tool_calls == [] and res.cut_calls == ["write_file"] and res.finish_reason == "max_tokens"


def test_the_helpers_never_raise():
    assert G._call_arguments(None) == {}
    assert G._call_arguments({"a": 1}) == {"a": 1}
    assert G._call_arguments(12) is None  # not a string: unreadable, never {}


@pytest.mark.parametrize("status", ["incomplete", "in_progress"])
def test_a_call_the_server_left_unfinished_is_cut_even_with_closed_arguments(status):
    """The first cut's own rule, unpinned until now: at the output limit a
    call whose ITEM is not finished is never run, even when its arguments
    happen to read as whole JSON (the server says it was not done)."""
    body = _sse([{"type": "response.incomplete", "response": {
        "status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}, "usage": {},
        "output": [{"type": "function_call", "status": status, "call_id": "c1", "name": "write_file",
                    "arguments": json.dumps({"path": "a.py"})}]}}])
    res = _complete(body)
    assert res.tool_calls == [] and res.cut_calls == ["write_file"] and res.finish_reason == "max_tokens"
