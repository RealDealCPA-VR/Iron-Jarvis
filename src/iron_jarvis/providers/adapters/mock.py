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


#: The start of the chat's "no media service" Tools line
#: (``daemon.chat_turn.NO_MEDIA_CONNECT_LINE``; pinned equal by
#: tests/test_integrate_wave3_v13530.py — the provider layer does not import
#: the daemon).
NO_MEDIA_MARK = "NO MEDIA SERVICE:"

#: v1.359.0 (wave 9): the demo model's own words, in the canon's
#: (VOCABULARY.md's demo-model row; dashboard lib/demoWords ``DEMO_MODEL``
#: and ``DEMO_REPLIES``, pinned equal by tests/test_run_words_and_stop_v13590).
DEMO_MODEL = "demo model"
DEMO_REPLIES = "a scripted demo, not real answers"
#: The first sentence of every reply the demo model writes itself. Before
#: v1.359.0 it opened "Done. Wrote RESULT.md summarizing the task." — a
#: fresh install's first answer read like finished work (and, in a chat with
#: no file tool, claimed a file nobody wrote).
DEMO_OPENING = f"This is the {DEMO_MODEL}: {DEMO_REPLIES}."
#: Its one scripted step, when it really ran (a ``write_file`` that saved).
DEMO_SAVED_LINE = f"{DEMO_OPENING} Its one scripted step saved RESULT.md with your task in it."
#: …when that step was refused or failed (nothing claims a file).
DEMO_NOT_SAVED_LINE = (
    f"{DEMO_OPENING} Its one scripted step tried to save RESULT.md, and that did not happen."
)
#: A demo hand-off to a helper agent (which ran the demo model too).
DEMO_HANDED_OFF_LINE = (
    f"{DEMO_OPENING} It handed the task to a helper agent, which ran the demo too."
)
#: No step at all (a chat with no file tool): it read nothing and did nothing.
DEMO_NOTHING_DONE_LINE = f"{DEMO_OPENING} No AI model read your message, so nothing was done."


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
        # Guided-decoding knobs (v1.203.0): accepted so callers can pass them
        # uniformly across adapters; IGNORED — openai-compat wire only this wave.
        response_format: dict | None = None,
        tool_choice: str | dict | None = None,
        extra_body: dict | None = None,
        reasoning: str = "",
    ) -> LLMResponse:
        if self._script:
            return self._script.pop(0)
        return self._default_behavior(messages, tools, system)

    async def stream(
        self,
        *,
        system: str,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]],
        # Guided-decoding knobs (v1.203.0): accepted-ignored, as in complete().
        response_format: dict | None = None,
        tool_choice: str | dict | None = None,
        extra_body: dict | None = None,
        reasoning: str = "",
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
        messages: list[LLMMessage], tools: list[dict[str, Any]], system: str = ""
    ) -> LLMResponse:
        task = next(
            (m.content for m in messages if m.role == "user"), "(unspecified task)"
        )
        has_tool_result = any(m.role == "tool" for m in messages)
        tool_names = {t["name"] for t in tools}
        # v1.353.0 integrate: the chat said "no media service is connected"
        # (chat_turn.NO_MEDIA_CONNECT_LINE, swapped in for generate_media) —
        # the demo model does what that line tells a real one: app_connect
        # for pixio, which shows the secure key card. Before this the demo
        # answered a fresh install's "make a picture" with its scripted
        # "Wrote RESULT.md" line and no card.
        wants_media_key = "app_connect" in tool_names and NO_MEDIA_MARK in (system or "")

        if not has_tool_result:
            if wants_media_key:
                return LLMResponse(
                    tool_calls=[
                        ToolCall(id="call_1", name="app_connect", arguments={"app": "pixio"})
                    ],
                    finish_reason="tool_use",
                )
            # MEDIA IN CHAT (v1.346.0): with ``generate_media`` armed (a
            # media-making sentence ask-armed it), the demo model MAKES the
            # media — the one action the sentence asked for — so the whole
            # lane (quote → card or free → inline picture / background job →
            # the line in the chat) can be driven with zero network through
            # the offline fake service. Checked FIRST: "make a logo" inside a
            # project with file tools armed is a picture, not a RESULT.md.
            if "generate_media" in tool_names:
                return LLMResponse(
                    tool_calls=[
                        ToolCall(
                            id="call_1",
                            name="generate_media",
                            arguments={"kind": _media_kind(task), "prompt": task},
                        )
                    ],
                    finish_reason="tool_use",
                )
            if "write_file" in tool_names:
                content = (
                    "# Iron Jarvis result\n\n"
                    f"Task: {task}\n\n"
                    f"Written by the {DEMO_MODEL}: {DEMO_REPLIES}. No AI model ran.\n"
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

        if wants_media_key:
            return LLMResponse(
                text=(
                    "Nothing can be made yet: no image, video or music service is"
                    " connected. Paste your Pixio key in the secure card, then ask again."
                ),
                finish_reason="stop",
            )
        if "generate_media" in tool_names:
            # The media call ran (it is made first above): the chat lanes put
            # the picture / the "it will appear here" words in the reply.
            return LLMResponse(text="Done.", finish_reason="stop")
        if "delegate" in tool_names:
            return LLMResponse(text=DEMO_HANDED_OFF_LINE, finish_reason="stop")
        if has_tool_result and "write_file" in tool_names:
            return LLMResponse(
                text=DEMO_SAVED_LINE if _saved(messages) else DEMO_NOT_SAVED_LINE,
                finish_reason="stop",
            )
        return LLMResponse(text=DEMO_NOTHING_DONE_LINE, finish_reason="stop")


def _saved(messages: list[LLMMessage]) -> bool:
    """Did the demo's own ``write_file`` step really save? (The tool's
    success output is "wrote N bytes to RESULT.md"; a refusal or a failure
    carries its error instead.) Never raises."""
    try:
        for m in reversed(messages):
            if m.role == "tool" and (getattr(m, "name", "") or "") == "write_file":
                return str(m.content or "").lstrip().lower().startswith("wrote ")
    except Exception:  # noqa: BLE001
        return False
    return False


def _media_kind(task: str) -> str:
    """The media kind a demo sentence asks for, by its nouns — image unless
    the words say video / song / sound."""
    words = task.lower()
    if any(w in words for w in ("video", "clip", "animation", "animate")):
        return "video"
    if any(w in words for w in ("song", "music", "jingle", "soundtrack", "track")):
        return "music"
    if any(w in words for w in ("voice", "narration", "sound effect", "sfx", "audio")):
        return "audio"
    return "image"
