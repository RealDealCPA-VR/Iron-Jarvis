"""v1.358.0 (wave 8, lane 8C): a run's step-by-step log says what a tool DID,
in the dashboard's own words — never the tool's id.

THE REGRESSION (the post-build re-audit, G19): the helper-agent card's "What
it did" fold read ``agents/mission.tool_line``, whose fallback for any tool
its own table did not word was the de-underscored id: the live receipt said
"Builder used history search" twenty times, and a connected app's tool read
"used mcp  gmail  search messages".

Now a tool ``_TOOL_WORDS`` does not word reads in the words the dashboard
already uses for the same call (lib/runWords ``toolStepLine``: the run
card's lines, lib/stepWords' counts, lib/toolWords' app words):
"searched your chats", "used gmail: search messages", "called a web
service". ONE fixture holds Python and TypeScript to the same words:
``dashboard/__tests__/fixtures/tool-lines-v13580.json`` is computed HERE from
the real ``mission.tool_line`` and checked; the dashboard pin
(``__tests__/one-receipt-v13580.test.tsx``) feeds every id in it through the
real ``toolStepLine`` and must get the same line.

Regenerate after an INTENDED word change:
``IJ_REGEN_TOOL_LINES=1 uv run pytest tests/test_one_receipt_v13580.py``
and commit the fixture with the TypeScript change.

The receipt is driven through the REAL app factory over a seeded ledger
(``GET /sessions/{id}/result`` — the read the chat's settled row and the run
page show), not through a stub of the words.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from iron_jarvis.agents import mission
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import (
    AgentRun,
    PermissionMode,
    Session,
    SessionStatus,
    ToolInvocation,
)
from iron_jarvis.daemon.app import create_app

_ROOT = Path(__file__).resolve().parents[1]
_FIXTURE = _ROOT / "dashboard" / "__tests__" / "fixtures" / "tool-lines-v13580.json"
_BASE = datetime(2026, 10, 19, 9, 0, 0)

#: Connected apps, custom tools and odd ids the fallback must word too.
_EXTRA_IDS = (
    "mcp__gmail__search_messages",
    "mcp__github__listOpenIssues",
    "mcp__google_drive__search_files",
    "mcp__tracker__get_PR",
    "mcp__bad",
    "rename_real_file",
    "some_new_tool",
    "browser_click",
    "browser_read_page",
)


def _fallback_ids() -> list[str]:
    """Every id ``tool_line`` words through the dashboard twin: the agent
    built-ins, the work line's kinds and the browser tools ``_TOOL_WORDS``
    does not word itself, plus apps and custom tools."""
    own = set(mission._TOOL_WORDS)
    ids = set(mission._RUN_TOOL_WORDS) | set(mission._STEP_KIND) | set(mission._BROWSER_TOOLS)
    ids |= set(_EXTRA_IDS)
    return sorted(i for i in ids if i not in own)


def _computed() -> dict:
    lines = {
        tool: {"ok": mission.tool_line(tool, {}, True), "failed": mission.tool_line(tool, {}, False)}
        for tool in _fallback_ids()
    }
    return {
        "lines": lines,
        # The built-ins the activity log words in its OWN richer phrases
        # ("read k1.pdf", "checked memory for “q”") — never through the
        # fallback, so the dashboard pin accepts them as covered.
        "python_own_words": sorted(mission._TOOL_WORDS),
    }


def test_the_fallback_words_are_the_fixture_the_dashboard_reads():
    want = _computed()
    if os.environ.get("IJ_REGEN_TOOL_LINES") == "1":
        _FIXTURE.write_text(json.dumps(want, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    stored = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    assert stored == want, "regenerate with IJ_REGEN_TOOL_LINES=1 after an intended change"
    # Anti-vacuity: the fixture really holds the audit's own examples.
    assert stored["lines"]["history_search"]["ok"] == "searched your chats"
    assert stored["lines"]["mcp__gmail__search_messages"]["ok"] == "used gmail: search messages"
    assert len(stored["lines"]) >= 40


def test_no_fallback_line_reads_a_tool_id():
    for tool, said in _computed()["lines"].items():
        if tool == "mcp__bad":
            continue  # not an app id at all: said by its own name, as the TS twin does
        for text in (said["ok"], said["failed"]):
            assert "mcp" not in text, (tool, text)
            assert "_" not in text, (tool, text)
            # "used <the id with spaces>" is the audit's leak shape ("used
            # history search"); a custom tool has no other name, so only the
            # built-ins are held to it.
            if tool in mission._RUN_TOOL_WORDS or tool in mission._STEP_KIND:
                assert not text.startswith(f"used {tool.replace('_', ' ')}"), (tool, text)
                assert not text.startswith(f"tried {tool.replace('_', ' ')} "), (tool, text)


def test_an_app_tool_reads_app_colon_action_in_words():
    assert mission.tool_line("mcp__github__listOpenIssues", {}, True) == "used github: list open issues"
    assert mission.tool_line("mcp__google_drive__search_files", {}, False) == (
        "tried google drive: search files and it failed"
    )
    assert mission.tool_own_name("mcp__tracker__get_PR") == "tracker: get PR"
    # A built-in ask still uses the approval card's words; a refusal nobody
    # was asked about names an app tool the same way.
    assert mission.ask_words("rest_call") == "call one of your saved web services"


def test_a_refusal_nobody_was_asked_about_names_an_app_tool_in_words():
    rows = [
        ("i1", "s1", "mcp__gmail__search_messages", "{}", False, "ask", False, _BASE),
        ("i2", "s1", "write_file", "{}", False, "ask", False, _BASE),
    ]
    lines, _last = mission.tool_activity(rows, {"i1": "not armed", "i2": "not armed"}, {"s1": "Builder"})
    assert [l["text"] for l in lines] == [
        "Builder tried gmail: search messages, a tool it was not given — nothing ran",
        # A built-in keeps its own name, as the v1.309.0 pin holds.
        "Builder tried write file, a tool it was not given — nothing ran",
    ]


def test_a_rest_call_names_the_service_never_the_path_or_key():
    assert mission.tool_line("rest_call", {"service": "crm (svc_1a2b)", "path": "/contacts/42"}, True) == (
        "called crm"
    )
    assert mission.tool_line("rest_call", {"service": "crm", "path": "/deals"}, False) == "could not call crm"
    assert mission.tool_line("rest_call", {}, True) == "called a web service"
    line = mission.tool_line("rest_call", {"service": "crm", "path": "/contacts/42", "body": {"k": "v"}}, True)
    assert "/contacts" not in line and "42" not in line


def test_the_known_words_are_unchanged():
    # Anti-regression: the table's own phrases (and the custom-tool fallback
    # an older pin holds) did not move.
    assert mission.tool_line("read_file", {"path": "C:/clients/acme/k1.pdf"}, True) == "read k1.pdf"
    assert mission.tool_line("write_document", {}, False) == "could not create a file"
    assert mission.tool_line("some_new_tool", {"secret": "x"}, True) == "used some new tool"


# --------------------------------------------------------------------------- #
# the receipt the chat's settled row shows (GET /sessions/{id}/result)
# --------------------------------------------------------------------------- #


def _call(db, iid, sid, tool, seconds, *, ok=True, args=None):
    db.add(
        ToolInvocation(
            id=iid,
            session_id=sid,
            agent_run_id=f"run_{sid}",
            tool=tool,
            args_json=json.dumps(args or {}),
            verdict=PermissionMode.ALLOW,
            ok=ok,
            reversibility="readonly",
            created_at=_BASE + timedelta(seconds=seconds),
        )
    )


def test_the_receipts_activity_says_tool_words_never_ids(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    engine = client.app.state.platform.engine
    sid = "sess_words"
    with session_scope(engine) as db:
        db.add(
            Session(
                id=sid,
                task="find what we decided",
                agent_name="custom:researcher",
                provider="anthropic",
                model="claude-sonnet-4-6",
                status=SessionStatus.COMPLETED,
                origin="chat",
                created_at=_BASE,
                finished_at=_BASE + timedelta(minutes=2),
            )
        )
        db.add(AgentRun(id=f"run_{sid}", session_id=sid, created_at=_BASE))
        _call(db, "w1", sid, "history_search", 1, args={"query": "S-corp"})
        _call(db, "w2", sid, "mcp__gmail__search_messages", 2, args={"q": "invoice"})
        _call(db, "w3", sid, "rest_call", 3, args={"service": "crm", "path": "/contacts/42"})
        _call(db, "w4", sid, "memory_read", 4, ok=False)
        db.commit()
    r = client.get(f"/sessions/{sid}/result")
    assert r.status_code == 200
    texts = [a["text"] for a in r.json()["activity"]]
    assert texts == [
        "researcher searched your chats",
        "researcher used gmail: search messages",
        "researcher called crm",
        "researcher tried a memory lookup and it failed",
    ], texts
    seen = " ".join(texts)
    for raw in ("history search", "history_search", "mcp", "memory read", "rest_call", "/contacts"):
        assert raw not in seen, raw
