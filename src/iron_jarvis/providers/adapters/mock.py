"""MockLLM adapter — deterministic, offline, no network or API key (§6).

Two modes:
  * **scripted** — pop pre-canned ``LLMResponse`` objects in order (tests).
  * **default behavior** — drive a real two-step agent loop with no script: on
    the first turn it calls ``write_file`` to record a result, then on the next
    turn (once a tool result is present) it finalizes. This lets the whole
    runtime demo end-to-end with zero external dependencies.
"""

from __future__ import annotations

from typing import Any

from .base import LLMAdapter, LLMMessage, LLMResponse, ToolCall


class MockLLMAdapter(LLMAdapter):
    provider = "mock"
    model = "mock-1"

    def __init__(self, script: list[LLMResponse] | None = None) -> None:
        self._script = list(script or [])

    async def complete(
        self,
        *,
        system: str,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]],
    ) -> LLMResponse:
        if self._script:
            return self._script.pop(0)
        return self._default_behavior(messages, tools)

    @staticmethod
    def _default_behavior(
        messages: list[LLMMessage], tools: list[dict[str, Any]]
    ) -> LLMResponse:
        task = next(
            (m.content for m in messages if m.role == "user"), "(unspecified task)"
        )
        has_tool_result = any(m.role == "tool" for m in messages)
        tool_names = {t["name"] for t in tools}

        if not has_tool_result and "write_file" in tool_names:
            content = (
                "# Iron Jarvis result\n\n"
                f"Task: {task}\n\n"
                "Completed offline by the MockLLM adapter (no network).\n"
            )
            return LLMResponse(
                tool_calls=[
                    ToolCall(
                        id="call_1",
                        name="write_file",
                        arguments={"path": "RESULT.md", "content": content},
                    )
                ],
                finish_reason="tool_use",
            )
        return LLMResponse(
            text="Done. Wrote RESULT.md summarizing the task.",
            finish_reason="stop",
        )
