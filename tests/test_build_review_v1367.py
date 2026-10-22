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


def _record_status(app, turn_id: str) -> str:
    with session_scope(app.state.platform.engine) as db:
        row = db.get(ChatTurnRecord, turn_id)
        return row.status if row is not None else ""


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


# --------------------------------------------------------------------------- #
# 2. a turn id already RUN is never run again — after the in-memory window
#    or a restart, its record answers
# --------------------------------------------------------------------------- #


async def test_a_turn_id_this_process_forgot_is_replayed_from_its_record_never_run_twice(app, work):
    """``TurnLogs`` keeps a finished turn ``FINISHED_KEEP_S`` (10 min) and a
    restart keeps none — before the review, the same ``turn_id`` POSTed after
    that (a screen resending a request it lost) RAN THE TURN AGAIN: the same
    edits and commands a second time and the question twice in the
    transcript. The record (7 days) now answers it."""
    sid = new_session(app, work)["id"]
    other = new_session(app, work)["id"]
    script = wire(app, Script([
        [ToolCall(id="c1", name="Write", arguments={"file_path": "made.py", "content": "y = 1\n"})],
        "Made it.",
    ]))
    status, raw = await open_turn(app, sid, {"text": "make made.py", "turn_id": "rv-once"})
    assert status == 200 and frames_of(raw)[-1][0] == "done"
    calls_after_first = len(script.calls)
    lg = await until(lambda: turn_log(app, "rv-once"))
    await until(lambda: lg.status != "running")
    await until(lambda: any(m.get("role") == "assistant" for m in messages_of(app, sid)))
    await until(lambda: _record_status(app, "rv-once") == "done")
    # The process forgets it (the 10-minute window passed, or a restart).
    app.state.platform.turn_logs._logs.pop("rv-once")
    (work / "made.py").unlink()

    status, raw = await open_turn(app, sid, {"text": "make made.py", "turn_id": "rv-once"})
    assert status == 200
    frames = frames_of(raw)
    assert frames and frames[-1][0] == "done", "the record's frames are replayed"
    assert len(script.calls) == calls_after_first, "the model was asked again: the turn RAN TWICE"
    assert not (work / "made.py").exists(), "the turn's edit was applied a second time"
    questions = [m for m in messages_of(app, sid) if m.get("role") == "user" and m.get("turnId") == "rv-once"]
    assert len(questions) == 1, "the question was appended twice"
    # Another session cannot claim that turn id.
    status, body = await open_turn(app, other, {"text": "x", "turn_id": "rv-once"})
    assert status == 409, body
