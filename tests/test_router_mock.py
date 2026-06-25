from __future__ import annotations

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


async def test_falls_back_to_mock_when_provider_unavailable(platform, monkeypatch):
    # anthropic has no API key in tests -> router selects the offline mock
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    res = await platform.router.complete(
        provider="anthropic",
        system="",
        messages=[LLMMessage(role="user", content="x")],
        tools=platform.registry.specs(["write_file"]),
    )
    assert res.provider == "mock"
