"""v1.346.0 (media in chat, integration): the demo model MAKES media when
``generate_media`` is armed, so the whole lane can be driven offline.

Before this, ``MockLLMAdapter._default_behavior`` knew two actions —
``write_file`` for a worker and ``delegate`` for a supervisor — so a demo
chat that ask-armed ``generate_media`` from "make me a picture" produced a
RESULT.md (or plain text) and the media lane could not be seen working on a
mock-default install (the scratch-daemon drive this release did, and every
first-run demo). Pins: the call, its kind by the sentence's nouns, the
finalize text after the tool result, and the untouched write_file /
delegate / plain paths (a mutation deleting the branch goes red on the
first two; one inverting the order goes red on the "picture, not a
RESULT.md" case).
"""

from __future__ import annotations

import pytest

from iron_jarvis.providers.adapters.base import LLMMessage
from iron_jarvis.providers.adapters.mock import DEMO_NOTHING_DONE_LINE, DEMO_NOT_SAVED_LINE, MockLLMAdapter

pytestmark = pytest.mark.asyncio


def _tools(*names: str) -> list[dict]:
    return [{"name": n, "description": "", "input_schema": {"type": "object"}} for n in names]


async def _ask(text: str, *names: str, after_tool: bool = False):
    msgs = [LLMMessage(role="user", content=text)]
    if after_tool:
        msgs.append(LLMMessage(role="tool", content="image made"))
    return await MockLLMAdapter().complete(system="", messages=msgs, tools=_tools(*names))


async def test_the_mock_calls_generate_media_with_the_kind_the_sentence_names():
    for text, kind in [
        ("make me a free picture of a lighthouse", "image"),
        ("make a 10 second video of waves", "video"),
        ("write me a song about spring", "music"),
        ("record a voiceover for the intro", "audio"),
    ]:
        resp = await _ask(text, "generate_media")
        assert resp.finish_reason == "tool_use", text
        (call,) = resp.tool_calls
        assert call.name == "generate_media"
        assert call.arguments == {"kind": kind, "prompt": text}


async def test_the_media_call_wins_over_write_file_and_finalizes_after_the_result():
    resp = await _ask("make a logo for the shop", "write_file", "generate_media")
    assert [c.name for c in resp.tool_calls] == ["generate_media"]
    done = await _ask("make a logo for the shop", "write_file", "generate_media", after_tool=True)
    assert done.tool_calls == [] and done.text == "Done."
    assert "RESULT.md" not in done.text


async def test_without_generate_media_the_mock_is_untouched():
    worker = await _ask("do the thing", "write_file")
    assert [c.name for c in worker.tool_calls] == ["write_file"]
    boss = await _ask("do the thing", "delegate")
    assert [c.name for c in boss.tool_calls] == ["delegate"]
    plain = await _ask("do the thing")
    # v1.359.0 (wave 9): the demo says it is the demo, and claims no file it
    # did not save — moved on purpose.
    assert plain.tool_calls == [] and plain.text == DEMO_NOTHING_DONE_LINE
    finished = await _ask("do the thing", "write_file", after_tool=True)
    assert finished.text == DEMO_NOT_SAVED_LINE
