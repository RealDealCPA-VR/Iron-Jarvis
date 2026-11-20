"""Leap 06-a: the rehearsal gate in the registry.

A run that carries a rehearsal (``ToolContext.rehearsal_id``, set only by the
lane from the run's own options) really reads, and lists what it WOULD have
written, moved, sent or run without doing any of it. Everything here goes
through the REAL registry, the real permission engine, the real file,
document and notify tools and the real ledger, in a temp folder:

* the bytes on disk are unchanged, no ``UndoJournal`` row and no
  ``ToolIntent`` is written, and each held call is ONE ``rehearsed`` ledger
  row with the engine's true ``would``;
* the event is ``tool.rehearsed``, never ``tool.executed``;
* a READONLY tool runs (a rehearsal reads for real);
* a tool that never said what it does is held (the fail-safe direction);
* the roster and a Deny are refused for real, and written down as "would be
  refused";
* without a rehearsal the same call writes (the control).
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlmodel import select

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import ToolIntent, ToolInvocation, UndoJournal
from iron_jarvis.platform import build_platform
from iron_jarvis.tools import rehearsal as R
from iron_jarvis.tools.base import Tool, ToolContext, ToolResult
from iron_jarvis.tools.permissions import PermissionEngine

pytestmark = pytest.mark.usefixtures("no_network")

OLD = "the client's ledger line\n" * 3


class _Undeclared(Tool):
    """A tool that never said whether it changes anything (the base class's
    fail-safe defaults). Its execute WOULD write a file."""

    name = "undeclared_v06a"
    description = "writes a marker file"
    input_schema = {"type": "object", "properties": {}}

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        (ctx.workspace / "undeclared.txt").write_text("written", encoding="utf-8")
        return ToolResult(ok=True, output="wrote undeclared.txt")


def _setup(tmp_path, *, rehearsal: str = "reh_06a_test", perms: dict | None = None):
    p = build_platform(str(tmp_path / "home"))
    p.registry.register(_Undeclared())
    table = {
        **p.config.permissions,
        "write_file": "allow",
        "edit_file": "allow",
        "rename_file": "allow",
        "write_document": "allow",
        "read_file": "allow",
        "notify": "ask",
        "undeclared_v06a": "allow",
        **(perms or {}),
    }
    engine = PermissionEngine(table)
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "ledger.txt").write_text(OLD, encoding="utf-8")
    (ws / "a.txt").write_text("alpha", encoding="utf-8")
    ctx = ToolContext(
        workspace=ws, session_id="s-06a", agent_run_id="r-06a",
        config=p.config, event_bus=p.event_bus, engine=p.engine,
        rehearsal_id=rehearsal,
    )
    events: list[dict] = []
    real = p.event_bus.publish

    async def spy(type, payload=None, session_id=None, **kw):
        events.append({"type": str(getattr(type, "value", type)), "payload": payload or {}})
        return await real(type, payload, session_id=session_id, **kw)

    p.event_bus.publish = spy
    return p, engine, ws, ctx, events


def _rows(engine, model):
    with session_scope(engine) as db:
        out = list(db.exec(select(model)).all())
        for r in out:
            db.expunge(r)
        return out


def _snapshot(ws) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in sorted(ws.iterdir()) if p.is_file()}


async def test_held_writes_change_nothing_and_leave_one_rehearsed_row_each(tmp_path):
    p, perms, ws, ctx, events = _setup(tmp_path)
    before = _snapshot(ws)
    calls = [
        ("write_file", {"path": "report.txt", "content": "new"}),
        ("edit_file", {"path": "ledger.txt", "old": "client's", "new": "CHANGED"}),
        ("rename_file", {"path": "a.txt", "new_path": "b.txt"}),
        ("write_document", {"path": "memo.docx", "content": "# Memo\n\nhello", "kind": "docx"}),
    ]
    for name, args in calls:
        res = await p.registry.invoke(name, args, ctx, perms)
        assert res.ok, (name, res.error)
        assert res.output.startswith("Rehearsal: not done."), res.output
        assert "Carry on as if it worked" in res.output

    assert _snapshot(ws) == before, "a rehearsal changed the folder"
    assert _rows(p.engine, UndoJournal) == [], "a rehearsal wrote an Undo"
    assert _rows(p.engine, ToolIntent) == [], "a rehearsal wrote an intent"
    rows = _rows(p.engine, ToolInvocation)
    assert sorted((r.tool, r.rehearsed, r.would) for r in rows) == sorted(
        (n, True, "run") for n, _ in calls
    )
    kinds = [e["type"] for e in events if e["type"].startswith("tool.")]
    assert kinds == ["tool.rehearsed"] * len(calls), kinds


async def test_the_would_is_the_engines_real_decision(tmp_path):
    """``notify`` is set to ask here. A lane that would have shown a card says
    so (``rehearsal_would="ask"``); with nobody to ask it is refused for real,
    as the real run would be. Neither sends anything."""
    p, perms, ws, ctx, events = _setup(tmp_path)
    asked = await p.registry.invoke(
        "notify", {"message": "Anna, the return is filed"}, ctx, perms, rehearsal_would="ask",
    )
    assert asked.ok and "it would ask you first" in asked.output
    refused = await p.registry.invoke("notify", {"message": "Anna, again"}, ctx, perms)
    assert not refused.ok
    rows = sorted(_rows(p.engine, ToolInvocation), key=lambda r: r.created_at)
    assert [(r.tool, r.rehearsed, r.would, r.ok) for r in rows] == [
        ("notify", True, "ask", True),
        ("notify", True, "refuse", False),
    ]
    assert [e["type"] for e in events if e["type"].startswith("tool.")] == [
        "tool.rehearsed", "tool.denied",
    ]


async def test_a_readonly_tool_really_runs(tmp_path):
    p, perms, ws, ctx, _ = _setup(tmp_path)
    res = await p.registry.invoke("read_file", {"path": "a.txt"}, ctx, perms)
    assert res.ok and "alpha" in res.output
    (row,) = _rows(p.engine, ToolInvocation)
    assert row.rehearsed is False and row.ok is True


async def test_an_undeclared_tool_is_held(tmp_path):
    from types import SimpleNamespace

    p, perms, ws, ctx, _ = _setup(tmp_path)
    assert R.holds(p.registry.get("undeclared_v06a"))
    # Not even the base class's default to lean on: still held.
    assert R.holds(SimpleNamespace(name="a_tool_from_somewhere_new"))
    res = await p.registry.invoke("undeclared_v06a", {}, ctx, perms)
    assert res.ok and res.output.startswith("Rehearsal: not done.")
    assert not (ws / "undeclared.txt").exists()


async def test_the_roster_and_a_deny_are_refused_for_real(tmp_path):
    p, perms, ws, ctx, events = _setup(tmp_path, perms={"rename_file": "deny"})
    not_armed = await p.registry.invoke(
        "write_file", {"path": "x.txt", "content": "x"}, ctx, perms, allowed_names={"read_file"},
    )
    denied = await p.registry.invoke("rename_file", {"path": "a.txt", "new_path": "c.txt"}, ctx, perms)
    assert not not_armed.ok and "not armed" in (not_armed.error or "")
    assert not denied.ok
    assert not (ws / "x.txt").exists() and (ws / "a.txt").exists()
    rows = _rows(p.engine, ToolInvocation)
    assert {(r.tool, r.would, r.ok) for r in rows} == {
        ("write_file", "refuse", False), ("rename_file", "refuse", False),
    }
    assert [e["type"] for e in events if e["type"].startswith("tool.")] == ["tool.denied"] * 2


async def test_without_a_rehearsal_the_same_call_writes(tmp_path):
    """The control: the branch above is what held the call."""
    p, perms, ws, ctx, events = _setup(tmp_path, rehearsal="")
    res = await p.registry.invoke("write_file", {"path": "report.txt", "content": "new"}, ctx, perms)
    assert res.ok and (ws / "report.txt").read_text(encoding="utf-8") == "new"
    (row,) = _rows(p.engine, ToolInvocation)
    assert row.rehearsed is False and row.would == ""
    assert [e["type"] for e in events if e["type"].startswith("tool.")] == ["tool.executed"]


def test_the_effect_words_name_the_target():
    assert R.effect_of("rename_file", {"path": "in/a.pdf", "new_path": "b.pdf"}).kind == "move"
    w = R.effect_of("rename_file", {"path": "in/a.pdf", "new_path": "b.pdf"}).words
    assert "a.pdf" in w and "b.pdf" in w
    assert R.effect_of("write_file", {"path": "r.txt", "content": "x"}).kind == "write"
    assert R.effect_of("notify", {"message": "hi"}).kind == "send"
    assert R.effect_of("shell", {"command": "del *.pdf"}).kind == "run"
    assert R.effect_of("mcp__notes__delete_page", {}).kind == "app"
    # Never the content of a write in the words.
    assert "secret body" not in R.effect_of("write_file", {"path": "r.txt", "content": "secret body"}).words


def test_reads_the_plan_allows_are_not_held_and_apps_hold_unless_read_only():
    """D-06.2: web pages and searches are read in a rehearsal; an app's tool
    only when the app marks it read only."""
    from types import SimpleNamespace

    from iron_jarvis.tools.base import Reversibility

    assert not R.holds(SimpleNamespace(name="web_fetch", reversibility=Reversibility.IRREVERSIBLE))
    assert not R.holds(SimpleNamespace(name="web_search", reversibility=Reversibility.IRREVERSIBLE))
    assert R.holds(SimpleNamespace(name="mcp__notes__update_page", reversibility=Reversibility.IRREVERSIBLE))
    assert not R.holds(SimpleNamespace(name="mcp__notes__read_page", reversibility=Reversibility.READONLY))
