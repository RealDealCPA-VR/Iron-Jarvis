from __future__ import annotations

import pytest

from iron_jarvis.providers.adapters.base import LLMMessage, ToolCall


async def test_mock_requests_tool_first(platform):
    res = await platform.router.complete(
        provider="mock",
        system="",
        messages=[LLMMessage(role="user", content="do a thing")],
        tools=platform.registry.specs(["write_file"]),
    )
    assert res.provider == "mock"
    assert res.response.wants_tools
    assert res.response.tool_calls[0].name == "write_file"


async def test_mock_finalizes_after_tool_result(platform):
    messages = [
        LLMMessage(role="user", content="do a thing"),
        LLMMessage(role="assistant", tool_calls=[ToolCall("c1", "write_file", {})]),
        LLMMessage(role="tool", tool_call_id="c1", content="wrote file"),
    ]
    res = await platform.router.complete(
        provider="mock",
        system="",
        messages=messages,
        tools=platform.registry.specs(["write_file"]),
    )
    assert not res.response.wants_tools
    assert res.response.finish_reason == "stop"


async def test_refuses_instead_of_mocking_when_the_provider_is_unavailable(
    platform, monkeypatch
):
    """A provider with no credentials REFUSES; it does not quietly become the
    mock (v1.162.0).

    This asserted `res.provider == "mock"` until a user with a downed local
    endpoint received the mock's scripted "Done. Wrote RESULT.md summarizing the
    task." and reasonably read it as work that happened. Note the `tools=` here:
    the mock answers a write_file-armed request by EMITTING a write_file call,
    so the old behaviour could fabricate a FILE, not just a sentence.
    """
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    # Keyless Claude now inherits the local `claude` CLI when present; force it
    # absent so this exercises the true "nothing real connected" net
    # deterministically (independent of whether the CLI is installed on the box).
    monkeypatch.setattr(
        platform.providers, "_cli_binary_present", lambda binary: False
    )
    with pytest.raises(Exception, match="isn't connected"):
        await platform.router.complete(
            provider="anthropic",
            system="",
            messages=[LLMMessage(role="user", content="x")],
            tools=platform.registry.specs(["write_file"]),
        )
