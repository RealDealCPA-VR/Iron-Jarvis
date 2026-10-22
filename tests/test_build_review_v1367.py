"""v1.367.0 (IJ harness Phase 4) — the REVIEW's pins. Each one names the
defect it closes; every one was checked by putting the defect back (the
mutation) and watching the pin go red.

Driven through the REAL app factory / lanes; only the model is scripted.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from sqlmodel import select

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import ChatThreadRecord, ChatTurnRecord, ToolInvocation
from iron_jarvis.daemon import mcp_turn
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.chat_stream import stream_chat_turn
from iron_jarvis.daemon.schemas import ChatBody, ChatMessageBody
from iron_jarvis.harness import session_tools as T
from iron_jarvis.harness import sessions as S
from iron_jarvis.personas.builtins import BUILTIN_PERSONAS
from iron_jarvis.platform import build_platform
from iron_jarvis.providers.adapters.base import ToolCall
from tests.test_build_sessions_v1367 import (
    CLAUDE,
    Script,
    frames_of,
    messages_of,
    new_session,
    open_turn,
    turn_log,
    until,
    wire,
)


@pytest.fixture()
def app(tmp_path):
    return create_app(str(tmp_path / "home"))


@pytest.fixture()
def work(tmp_path):
    folder = tmp_path / "work"
    folder.mkdir()
    (folder / "app.py").write_text("x = 1\n", encoding="utf-8")
    return folder


# --------------------------------------------------------------------------- #
# 1. ask_user armed by name in a CHAT turn never parks the turn
# --------------------------------------------------------------------------- #


async def test_ask_user_armed_by_name_in_a_chat_turn_refuses_instead_of_hanging(tmp_path):
    """The chat page's "+" menu lists every registered tool, so a user (or a
    "/" skill whose playbook names it) can arm ``ask_user`` in a plain chat
    turn — whose page draws NO ask_user card. Before the review every stream
    turn's interaction scope carried ``ask``, so the call parked the turn on a
    question nobody could see, with no clock: a watched chat waited forever.
    Now only a harness turn that armed it gets the question channel; anywhere
    else the tool refuses at once, in words."""
    platform = build_platform(str(tmp_path / "home"))
    script = Script([[ToolCall(id="q1", name="ask_user", arguments={"question": "Which one?"})], "Fine."])
    platform.router.stream = script.stream
    platform.router.complete = script.complete
    body = ChatBody(
        messages=[ChatMessageBody(role="user", content="ask me something")],
        provider=CLAUDE[0], model=CLAUDE[1], tools=["ask_user"], mcp_cards=True,
    )

    async def drain() -> list[str]:
        out: list[str] = []
        async for chunk in await stream_chat_turn(platform, BUILTIN_PERSONAS, body):
            out.append(chunk)
        return out

    raw = "".join(await asyncio.wait_for(drain(), timeout=20))
    frames = frames_of(raw)
    assert "ask_user" not in [e for e, _ in frames], "a question no page renders was sent"
    assert frames[-1][0] == "done"
    tool_msgs = [m for m in script.calls[1]["messages"] if m.role == "tool"]
    assert tool_msgs and tool_msgs[0].content.startswith(T.NOBODY_TO_ASK[:40]), tool_msgs
    assert mcp_turn.PENDING == {} or all(a.kind != "ask_user" for a in mcp_turn.PENDING.values())
