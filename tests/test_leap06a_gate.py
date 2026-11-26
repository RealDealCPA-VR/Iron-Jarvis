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

import json
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


# --------------------------------------------------------------------------- #
# Every tool a rehearsal lets run, reviewed (plan 06-a, Risk)
# --------------------------------------------------------------------------- #

_FILES = "reads files or folders on this PC; writes nothing"
_DOCS = "reads a document or a workbook (scans read by the vision model the real run would use); writes nothing"
_RECORDS = "lists Iron Jarvis's own records or settings (a key by its name only); changes none"
_SELF = "says how Iron Jarvis itself is doing; changes nothing"
_MEMORY = "reads memory, past work or kept notes (a rehearsal is never in them); writes nothing"
_WEB = "reads the public web: D-06.2 says a rehearsal may read web pages and searches"
_OWN_BROWSER = "reads a page in Iron Jarvis's own invisible browser, never the user's (D-06.2: web pages)"
_USER_BROWSER = "reads the user's own browser through the add-on at read access; clicks, types and switches nothing"
_APP_READS = "reads what a connected app offers to be read (D-06.2: an app's reads only)"
_PANES = "reads a Build pane; types nothing into it"
_OWN_LIST = "the run's own working list (REHEARSAL_OWN_STATE); it changes nothing outside the run"
_HELPER = (
    "a helper's hand-off: the helper's run inherits the rehearsal, so its writes are held "
    "(test_leap06a_helpers_inherit.py)"
)
_LOOKUP = "looks up what Iron Jarvis can do or the user's own things by name; changes nothing"

#: Every tool a rehearsal does NOT hold (it runs for real), reviewed: each
#: one only reads, or is the run's own state, or a helper whose run inherits
#: the rehearsal. A tool found to act although it declares READONLY goes in
#: ``tools/rehearsal.REHEARSAL_HOLDS`` instead (``browser_screenshot`` did).
LETS_RUN: dict[str, str] = {
    "app_health": _SELF,
    "app_search": _LOOKUP,
    "app_status": _SELF,
    "app_tools_find": "reads the names and descriptions of the tools already loaded for the extensions in the reply; calls none of them (v1.577.0)",
    "ask_user": "asks in a coding session and waits (refused anywhere else: no one to ask); changes nothing",
    "brain_read": _APP_READS,
    "brain_search": _APP_READS,
    "browse": _WEB,
    "browser_get_active_tab": _USER_BROWSER,
    "browser_get_elements": _USER_BROWSER,
    "browser_get_status": _USER_BROWSER,
    "browser_list_tabs": _USER_BROWSER,
    "browser_read_page": _USER_BROWSER,
    "build_folder_show": _RECORDS,
    "calculate": "exact arithmetic in the spreadsheet engine; touches no file",
    "change_results": (
        "reads the results Iron Jarvis already counted for the changes you accepted (18-a); "
        "counts nothing again and writes nothing"
    ),
    "changes_list": _RECORDS,
    "chat_fullness": (
        "reads the calling chat's own record of what each reply was sent (sizes only; the intelligence "
        "plan, release 6); takes no argument and writes nothing"
    ),
    "client_list": _RECORDS,
    "code_handoff_check": "waits on a coding helper that code_handoff_start started, and a rehearsal holds that",
    "code_handoff_list": "lists the coding tools and this session's helpers; starts none",
    "code_load": "reads a saved script's source; runs nothing",
    "code_search": "searches saved scripts; runs nothing",
    "coding_reply_check": (
        "reads Iron Jarvis's own record of a Build › Code reply's test check and the folder's "
        "check setting (22-a); runs no test and writes nothing"
    ),
    "compare_documents": _DOCS,
    "computer_use_status": _SELF,
    "config_list": _RECORDS,
    "config_secret": "shows the secure card: only the person's own typing stores a key, never this call",
    "consult": (
        "one question to a teammate's model, no session and no files; a REMOTE agent's question "
        "is held (SIDE_DOORS remote_handoff, test_leap06a_leaks.py)"
    ),
    "delegate": _HELPER,
    "downloads_list": "lists what the browser downloaded; moves nothing",
    "excel_accounts_diff": _DOCS,
    "excel_formula_check": _DOCS,
    "excel_live_look": (
        "looks at Excel as it is open on this PC and types nothing; in a rehearsal it never "
        "moves Excel to a range (SIDE_DOORS excel_show, test_excel_live_look_never_moves_excel_in_a_rehearsal)"
    ),
    "excel_profile": _DOCS,
    "excel_query": _DOCS,
    "excel_read": _DOCS,
    "excel_sheet_spec": _DOCS,
    "excel_trace": _DOCS,
    "exit_plan_mode": "a coding session's plan card (refused outside one); it writes nothing",
    "extract_pdf": _DOCS,
    "file_search": _FILES,
    "find_capability": _LOOKUP,
    "git_read": "asks git about a folder with read-only commands only; refuses any that writes",
    "glob": _FILES,
    "goal_list": _RECORDS,
    "grep": _FILES,
    "guide_read": _LOOKUP,
    "guide_search": _LOOKUP,
    "history_search": _MEMORY,
    "image_info": _FILES,
    "integration_list": _RECORDS,
    "list_agents": _RECORDS,
    "list_files": _FILES,
    "list_folder": _FILES,
    "ltm_search": _APP_READS,
    "mcp_list_resources": _APP_READS,
    "mcp_read_resource": _APP_READS,
    "memory_read": _MEMORY,
    "memory_search": _MEMORY,
    "model_library": (
        "lists the models the library offers and which fit this PC (43-a): reads the shipped list, "
        "this PC's memory and a model server's own list of what it has installed; downloads "
        "nothing (model_download is a write, so it is held)"
    ),
    "night_task_list": _RECORDS,
    "pack_status": _SELF,
    "page_open": _OWN_BROWSER,
    "page_read": _OWN_BROWSER,
    "page_screenshot": (
        "a picture of a page in Iron Jarvis's own browser, kept in its own shots folder "
        "(deleted with the run), never the user's folder"
    ),
    "pane_list": _PANES,
    "pane_read": _PANES,
    "pane_wait": _PANES,
    "pdf_form_fields": _DOCS,
    "peer_allows": (
        "lists the paired computers and what each may do here (41-a); turns no switch on or "
        "off (peer_allow_off is a write, so it is held)"
    ),
    "pixio_models": "lists media models; generates nothing and spends nothing",
    "pixio_params": "lists one media model's settings; generates nothing and spends nothing",
    "plan_update": "a coding session's own step list; changes nothing outside it",
    "pr_comments_list": (
        "reads Iron Jarvis's own record of a pull request's review comments (26-a); never calls "
        "GitHub and writes nothing (pr_comments_quiet is a write, so it is held)"
    ),
    "preference_list": _MEMORY,
    "process_read": "reads a program that process_start started, and a rehearsal holds that",
    "read_document": _DOCS,
    "read_file": _FILES,
    "recall": _MEMORY,
    "recall_lessons": _MEMORY,
    "redact_scan": _DOCS,
    "rehearse": "refuses inside a rehearsal: a rehearsal cannot start another one",
    "run_doctor": _SELF,
    "schedule_list": _RECORDS,
    "secret_list": _RECORDS,
    "skill_load": "reads a skill's instructions; runs nothing",
    "skill_search": "searches the skills; runs nothing",
    "spawn_agent": _HELPER,
    "stack_plan": (
        "reads one scan on this PC and asks the model the real run would use for a sorting plan, "
        "kept in Iron Jarvis's own folder (31-a); writes no document (stack_apply is a write, so "
        "it is held)"
    ),
    "suggestion_list": _RECORDS,
    "tool_list": _RECORDS,
    "tool_script": (
        "a script that may call only the tools on this list, each through registry.invoke, "
        "in an isolated worker that writes no file"
    ),
    "usage_report": (
        "reads Iron Jarvis's own usage record, through the client wall (46-a); "
        "changes nothing"
    ),
    "view_image": "looks at a picture with the vision model the real run would use; writes nothing",
    "web_extract": _WEB,
    "web_fetch": _WEB,
    "web_look": "looks at Iron Jarvis's own browser page with the vision model the real run would use",
    "web_search": _WEB,
    "work_history": _MEMORY,
    "work_search": _MEMORY,
    "workflow_list": _RECORDS,
    "worklist_add": _OWN_LIST,
    "worklist_done": _OWN_LIST,
    "worklist_next": _OWN_LIST,
    "worklist_status": _OWN_LIST,
}


def test_every_tool_a_rehearsal_lets_run_was_reviewed(tmp_path):
    """Plan 06-a, Risk: "a tool that acts but wrongly declares READONLY would
    act in a rehearsal". Every tool of the REAL registry that a rehearsal does
    not hold is in :data:`LETS_RUN` with why; a new one fails until it is
    reviewed. (An app's tool runs only when its app marks it read only,
    D-06.2; none is connected here.)"""
    p = build_platform(str(tmp_path / "home"))
    lets_run = sorted(n for n in p.registry.names() if not R.holds(p.registry.get(n)))
    assert len(lets_run) > 50, "control: the real registry was built"
    unreviewed = [n for n in lets_run if n not in LETS_RUN]
    assert not unreviewed, (
        "A tool a rehearsal lets run that nobody reviewed (leap 06-a). If it only reads, add it "
        "to LETS_RUN with why; if it acts, hold it (tools/rehearsal.REHEARSAL_HOLDS): "
        + ", ".join(unreviewed)
    )
    assert all(len(why.split()) >= 5 for why in LETS_RUN.values())


async def test_a_tool_that_says_it_reads_but_acts_is_held(tmp_path):
    """``browser_screenshot`` declares READONLY, yet it brings a tab of the
    user's own browser to the front: a rehearsal holds it (and the browser
    is never reached)."""
    from iron_jarvis.tools.base import Reversibility

    p, perms, ws, ctx, events = _setup(tmp_path, perms={"browser_screenshot": "allow"})
    tool = p.registry.get("browser_screenshot")
    assert tool is not None and tool.reversibility == Reversibility.READONLY
    assert R.holds(tool)
    res = await p.registry.invoke("browser_screenshot", {}, ctx, perms)
    assert res.ok and res.output.startswith(
        "Rehearsal: not done. Would bring a tab of your browser to the front"
    ), res.output
    (row,) = _rows(p.engine, ToolInvocation)
    assert row.rehearsed is True and row.tool == "browser_screenshot"


#: What Excel answers to a look at Totals!A1:B2 of an open Budget.xlsx.
_EXCEL_LOOK = {
    "ok": True, "excel_open": True,
    "workbooks": [{"name": "Budget.xlsx", "path": "", "active": True, "saved": True,
                   "sheets": [{"name": "Totals", "used": "A1:B2"}]}],
    "active": {"workbook": "Budget.xlsx", "sheet": "Totals", "selection": "A1", "cell": "A1", "path": ""},
    "range": {"workbook": "Budget.xlsx", "sheet": "Totals", "address": "A1:B2", "path": "",
              "rows": [["total", 1250], ["tax", 310]], "formulas": {}},
}


def _fake_excel(monkeypatch) -> tuple[list, list]:
    """Excel stood in for: ``ask`` answers the look, ``goto`` records each
    move (the call that brings Excel to the front and selects the range)."""
    from iron_jarvis.documents import excel_live as XL

    looks: list = []
    moves: list = []

    async def fake_ask(req, timeout):
        looks.append(dict(req))
        return {k: (dict(v) if isinstance(v, dict) else list(v) if isinstance(v, list) else v)
                for k, v in _EXCEL_LOOK.items()}

    async def fake_goto(workbook, sheet, cell):
        moves.append((workbook, sheet, cell))
        return {"ok": True, "sheet": sheet, "address": cell}

    monkeypatch.setattr(XL, "ask", fake_ask)
    monkeypatch.setattr(XL, "goto", fake_goto)
    return looks, moves


SHOW = {"workbook": "Budget.xlsx", "sheet": "Totals", "range": "A1:B2", "show": True}


async def test_excel_live_look_never_moves_excel_in_a_rehearsal(tmp_path, monkeypatch):
    """Review 5: ``excel_live_look`` declares READONLY, and with ``show`` it
    brings Excel to the front and selects the range (``excel_worker.op_goto``).
    In a rehearsal it still LOOKS for real (the read runs, the cells come
    back), but Excel is never moved: one rehearsed row lists the move, so the
    report counts it, and the model is told it was not done."""
    looks, moves = _fake_excel(monkeypatch)
    p, perms, ws, ctx, events = _setup(tmp_path)
    tool = p.registry.get("excel_live_look")
    assert tool is not None and not R.holds(tool), "control: the look itself runs in a rehearsal"

    res = await p.registry.invoke("excel_live_look", dict(SHOW), ctx, perms)

    assert res.ok, res.error
    assert moves == [], "a rehearsal moved Excel"
    assert len(looks) == 1 and "1250" in res.output, "the look did not run for real"
    assert "Rehearsal: Excel was not moved. Would show Totals!A1:B2 of Budget.xlsx selected in Excel." in res.output
    assert "Excel now shows" not in res.output
    assert (res.data or {}).get("shown") is False
    rows = sorted(_rows(p.engine, ToolInvocation), key=lambda r: r.created_at)
    assert sorted((r.tool, r.rehearsed, r.would, r.ok) for r in rows) == [
        ("excel_live_look", False, "", True), ("excel_show", True, "run", True),
    ]
    # The report counts the move from that row ("This would show 1 range …").
    (held,) = [r for r in rows if r.rehearsed]
    effect = R.effect_of(held.tool, json.loads(held.args_json), None)
    assert (effect.kind, effect.words) == ("change", "show Totals!A1:B2 of Budget.xlsx selected in Excel")
    assert R.sentence([effect]) == "This would show 1 range in Excel. Nothing was changed."


async def test_without_a_rehearsal_excel_live_look_moves_excel_when_asked(tmp_path, monkeypatch):
    """The control: the branch above is what kept Excel still."""
    _looks, moves = _fake_excel(monkeypatch)
    p, perms, ws, ctx, events = _setup(tmp_path, rehearsal="")
    res = await p.registry.invoke("excel_live_look", dict(SHOW), ctx, perms)
    assert res.ok, res.error
    assert moves == [("Budget.xlsx", "Totals", "A1:B2")]
    assert "Excel now shows Totals!A1:B2 selected." in res.output
    assert [(r.tool, r.rehearsed) for r in _rows(p.engine, ToolInvocation)] == [("excel_live_look", False)]


def test_reads_the_plan_allows_are_not_held_and_apps_hold_unless_read_only():
    """D-06.2: web pages and searches are read in a rehearsal; an app's tool
    only when the app marks it read only."""
    from types import SimpleNamespace

    from iron_jarvis.tools.base import Reversibility

    assert not R.holds(SimpleNamespace(name="web_fetch", reversibility=Reversibility.IRREVERSIBLE))
    assert not R.holds(SimpleNamespace(name="web_search", reversibility=Reversibility.IRREVERSIBLE))
    assert R.holds(SimpleNamespace(name="mcp__notes__update_page", reversibility=Reversibility.IRREVERSIBLE))
    assert not R.holds(SimpleNamespace(name="mcp__notes__read_page", reversibility=Reversibility.READONLY))
