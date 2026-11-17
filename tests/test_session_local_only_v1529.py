"""Owner decision 2 after the third fix plan: an open coding session (Chat ›
Code) has its own Local only switch, in the session's ⋯ menu
(``dashboard/components/harness/HarnessSession.tsx``, the row
``harness-local-only`` beside the reply limit). The chat tool that refuses to
change Local only for a coding session names that switch where it really is,
in plain words, and changes nothing.

The dashboard side (the row, the PATCH, the hold) is pinned in
``dashboard/__tests__/session-local-only-v1529.test.tsx``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import BUILD_SURFACE, ChatThreadRecord
from iron_jarvis.daemon.app import create_app
from iron_jarvis.settings import records_history as H
from iron_jarvis.tools.base import ToolContext

ROOT = Path(__file__).resolve().parents[1]
SESSION_TSX = ROOT / "dashboard" / "components" / "harness" / "HarnessSession.tsx"


def _read(path: Path) -> str:
    # CI's Windows runners check out with CRLF.
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def _session_menu() -> str:
    """The session's ⋯ menu block (``harness-session-menu``) of the page."""
    src = _read(SESSION_TSX)
    start = src.index('data-testid="harness-session-menu"')
    end = src.index('data-testid="harness-archive"', start)
    return src[start:end]


def test_the_sentence_names_the_session_menu_in_chat_code():
    words = H.LOCAL_ONLY_IN_CODE
    assert words == (
        "This is a coding session: turn Local only on or off from the ⋯ menu at the top of the "
        "session in Chat › Code. Nothing changed."
    )
    assert "its own Local only switch in Code" not in words
    # No engine words: no route, no key, no file.
    assert not re.search(r"local_only|/build|PATCH|harness|\.tsx", words)


def test_the_switch_the_sentence_names_is_really_in_that_menu():
    menu = _session_menu()
    assert 'data-testid="harness-local-only"' in menu
    assert ">Local only<" in menu
    assert "local_only: !view.setup.localOnly" in menu, "the row changes the session through its PATCH"
    # Beside the reply limit (the session's other setting in that menu).
    assert 'data-testid="harness-cap-open"' in menu


@pytest.mark.asyncio
async def test_the_chat_tool_refuses_a_coding_session_with_that_sentence(tmp_path):
    app = create_app(str(tmp_path / "home"))
    p = app.state.platform
    tid = "build_lo"
    with session_scope(p.engine) as db:
        db.add(ChatThreadRecord(id=tid, surface=BUILD_SURFACE, messages_json="[]", setup_json="{}"))
        db.commit()
    tool = H.ChatLocalOnlyTool(p)
    ctx = ToolContext(
        workspace=tmp_path, session_id="chat", agent_run_id="chat",
        config=p.config, event_bus=p.event_bus, engine=p.engine, thread_id=tid,
    )
    for on in (True, False):
        res = await tool.execute({"on": on}, ctx)
        assert res.ok is False
        assert res.error == H.LOCAL_ONLY_IN_CODE
    with session_scope(p.engine) as db:
        assert json.loads(db.get(ChatThreadRecord, tid).setup_json or "{}") == {}, "nothing changed"
