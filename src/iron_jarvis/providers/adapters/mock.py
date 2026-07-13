"""MockLLM adapter — deterministic, offline, no network or API key (§6).

Two modes:
  * **scripted** — pop pre-canned ``LLMResponse`` objects in order (tests).
  * **default behavior** — drive a real two-step agent loop with no script: on
    the first turn it takes one concrete action (``write_file`` for a worker
    agent, or ``delegate`` for a supervisor that only has the ``delegate``
    tool), then on the next turn (once a tool result is present) it finalizes.
    This lets the whole runtime — including supervised delegation — demo
    end-to-end with zero external dependencies.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
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

    async def stream(
        self,
        *,
        system: str,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]],
    ) -> AsyncIterator[dict[str, Any]]:
        """Actually stream the offline reply word-by-word so the streaming path
        demos + smoke-tests end-to-end with zero network. A tool-use turn (empty
        text) just yields ``final``. The ``response`` is identical to complete()."""
        resp = await self.complete(system=system, messages=messages, tools=tools)
        if resp.text:
            words = resp.text.split(" ")
            for i, w in enumerate(words):
                yield {"type": "text", "text": (w if i == 0 else " " + w)}
                await asyncio.sleep(0)  # yield to the loop so the client sees deltas
        yield {"type": "final", "response": resp}

    @staticmethod
    def _default_behavior(
        messages: list[LLMMessage], tools: list[dict[str, Any]]
    ) -> LLMResponse:
        task = next(
            (m.content for m in messages if m.role == "user"), "(unspecified task)"
        )
        has_tool_result = any(m.role == "tool" for m in messages)
        tool_names = {t["name"] for t in tools}

        if not has_tool_result:
            if "write_file" in tool_names:
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
            # A supervisor only has the ``delegate`` tool: hand the task to one
            # subagent, then finalize once the subagent's summary comes back.
            if "delegate" in tool_names:
                return LLMResponse(
                    tool_calls=[
                        ToolCall(
                            id="call_1",
                            name="delegate",
                            arguments={"agent_type": "builder", "task": task},
                        )
                    ],
                    finish_reason="tool_use",
                )

        if "delegate" in tool_names:
            return LLMResponse(
                text="Delegated the task to a subagent; all subtasks complete.",
                finish_reason="stop",
            )
        return LLMResponse(
            text="Done. Wrote RESULT.md summarizing the task.",
            finish_reason="stop",
        )
