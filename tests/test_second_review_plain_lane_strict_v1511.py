"""Second review, finding L7 (v1.511.0): the plain chat lane honours "Ask
before every action".

The streaming lane (``routes/chat.py``) cards a ``STRICT_ASK_TOOLS`` call
(file writers, the web, a click in the agent's browser) under the posture
``always_ask`` even when the turn auto-armed it, unless a card already
allowed it for this conversation (``card_grants``) or a standing grant
covers it. The plain lane (``POST /chat`` -> ``chat_turn.run_chat_turn``)
handled the eased postures but never read the strict one, so a caller that
sent ``approval_mode: "always_ask"`` had its file written with nobody
asked. The plain lane cannot show a card, so the call is now REFUSED through
the registry's ledgered deny path, in the lane's existing words ("needs your
OK, which can only be given in the Iron Jarvis window"). Every other
posture, and a call with no posture at all (the phone doors send none),
runs exactly as before.

Driven through the REAL ``run_chat_turn`` and the REAL ``registry.invoke``;
the model is the only double.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from iron_jarvis.agents.mission import ask_words
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.chat_turn import (
    ONCE_NOT_ASKED_TAIL,
    REFUSAL_FOOTNOTE_WORDS,
    SPEND_NOT_ASKED_FOOTNOTE,
    SPEND_NOT_ASKED_LABEL,
    STRICT_ASK_TOOLS,
    run_chat_turn,
)
from iron_jarvis.daemon.schemas import ChatBody
from iron_jarvis.providers.adapters.base import ToolCall
from tests.test_generate_media_tool_v1346 import _ledger, _PostRouter

WRITER = "write_file"
READER = "list_files"
FOOTNOTE = REFUSAL_FOOTNOTE_WORDS[SPEND_NOT_ASKED_FOOTNOTE]


@pytest.fixture()
def app(tmp_path):
    return create_app(str(tmp_path))


class _Router(_PostRouter):
    """Round 0 calls the tool, round 1 answers; keeps what the model saw."""

    def __init__(self, calls, final="Done."):
        super().__init__(calls, final=final)
        self.seen: list[list[Any]] = []

    async def complete(self, **kw):
        self.seen.append(list(kw.get("messages") or []))
        return await super().complete(**kw)


def _write_call() -> ToolCall:
    return ToolCall(id="c1", name=WRITER, arguments={"path": "notes.txt", "content": "a short report"})


def _turn(platform, tool: str, mode: "str | None", **extra) -> dict:
    kw: dict[str, Any] = {"messages": [{"role": "user", "content": "save a short report"}],
                          "tools": [tool], "thread_id": "chat_strict"}
    if mode is not None:
        kw["approval_mode"] = mode
    kw.update(extra)
    return asyncio.run(run_chat_turn(platform, {}, ChatBody(**kw)))


def _tool_messages(router: _Router) -> list[Any]:
    assert len(router.seen) >= 2, "the lane never asked the model for its answer"
    return [m for m in router.seen[1] if getattr(m, "role", "") == "tool"]


def _denied(platform, name: str) -> list[Any]:
    return [e for e in list(platform.event_bus.history)
            if e.type == "tool.denied" and (e.payload or {}).get("tool") == name]


def test_the_tools_are_what_the_strict_posture_asks_about():
    assert WRITER in STRICT_ASK_TOOLS
    assert READER not in STRICT_ASK_TOOLS


def test_ask_before_every_action_refuses_a_file_write_it_cannot_ask_about(app):
    platform = app.state.platform
    platform.router = router = _Router([_write_call()])
    res = _turn(platform, WRITER, "always_ask")
    # Armed for the model, never run.
    assert WRITER in router.tools[0]
    assert WRITER not in res["tools_used"]
    (msg,) = _tool_messages(router)
    assert msg.content.startswith(f"{SPEND_NOT_ASKED_LABEL}: ")
    assert ONCE_NOT_ASKED_TAIL in msg.content
    # The ledger has the refusal, and the denied event says what kind.
    rows = _ledger(platform, WRITER)
    assert len(rows) == 1 and rows[0].ok is False
    assert ONCE_NOT_ASKED_TAIL in rows[0].output
    (ev,) = _denied(platform, WRITER)
    assert ev.payload["kind"] == SPEND_NOT_ASKED_LABEL
    # The reply says why, in the app's words.
    assert f"_Note: Jarvis couldn't {ask_words(WRITER)} ({FOOTNOTE})._" in res["reply"]


@pytest.mark.parametrize("mode", [None, "approve_for_me", "yolo"])
def test_any_other_posture_writes_the_file_as_before(app, mode):
    platform = app.state.platform
    platform.router = router = _Router([_write_call()])
    res = _turn(platform, WRITER, mode)
    assert WRITER in res["tools_used"]
    rows = _ledger(platform, WRITER)
    assert len(rows) == 1 and rows[0].ok is True
    assert _denied(platform, WRITER) == []
    (msg,) = _tool_messages(router)
    assert SPEND_NOT_ASKED_LABEL not in msg.content


def test_a_tool_the_strict_posture_does_not_ask_about_still_runs(app):
    platform = app.state.platform
    platform.router = _Router([ToolCall(id="c1", name=READER, arguments={})])
    res = _turn(platform, READER, "always_ask")
    assert READER in res["tools_used"]
    rows = _ledger(platform, READER)
    assert len(rows) == 1 and rows[0].ok is True
    assert _denied(platform, READER) == []


def test_allowed_for_this_conversation_is_the_card_grant_and_runs(app):
    """The streaming lane skips the strict card for a tool the user allowed
    "for this conversation" (``card_grants``): the plain lane reads the same
    grant and does not refuse it."""
    platform = app.state.platform
    platform.router = _Router([_write_call()])
    res = _turn(platform, WRITER, "always_ask", granted_tools=[WRITER])
    assert WRITER in res["tools_used"]
    rows = _ledger(platform, WRITER)
    assert len(rows) == 1 and rows[0].ok is True


def test_the_phone_doors_send_no_posture():
    """The phone doors build their ChatBody with no ``approval_mode``, so they
    take the default posture and run exactly as before."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "src" / "iron_jarvis"
    for rel in ("comm/inbound.py", "daemon/routes/comm.py"):
        text = (root / rel).read_text(encoding="utf-8").replace("\r\n", "\n")
        assert "approval_mode" not in text, rel
    assert ChatBody(messages=[{"role": "user", "content": "x"}]).approval_mode == ""
