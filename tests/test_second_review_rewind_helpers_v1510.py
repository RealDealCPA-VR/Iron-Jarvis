"""v1.510.0 (second north-star review, T3): Rewind puts back what a coding
session's HELPERS changed.

A helper (a worker the session handed a piece of work to) runs under its own
tool session; its rows carry ``options["build_session"]`` = the session's
thread. Rewind used to read only the session's own rows, so on a team session
(the lead never edits, the workers do) it restored nothing, answered
``rewound: true`` and listed the hand-off as a "command" that was not undone.

Driven through the real app: a real build turn, the real registry writing the
helper's files and their undo journal, the real rewind routes and
``POST /undo``'s own code. Only the model is scripted; the hand-off row and the
helper's command row are written as the registry would write them (a real
helper run needs a second scripted model and a shell).
"""

from __future__ import annotations

import json

import pytest

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import Session, ToolInvocation
from iron_jarvis.daemon.app import create_app
from iron_jarvis.harness import rewind as R
from iron_jarvis.tools.base import ToolContext
from iron_jarvis.tools.permissions import PermissionEngine
from tests.test_build_sessions_v1367 import Script, asgi, frames_of, new_session, open_turn, wire


@pytest.fixture()
def app(tmp_path):
    return create_app(str(tmp_path / "home"))


@pytest.fixture()
def work(tmp_path):
    folder = tmp_path / "work"
    folder.mkdir()
    (folder / "report.md").write_bytes(b"# Report\n\nfirst draft\n")
    (folder / "earlier.txt").write_bytes(b"before the session\n")
    return folder


def _helper(app, thread_id: str, work, name: str = "builder") -> str:
    """A helper's run as the helper door makes it: its own session, carrying
    the coding session's thread in ``options["build_session"]``."""
    with session_scope(app.state.platform.engine) as db:
        row = Session(task="write the summary", agent_name=name, workspace_path=str(work),
                      options_json=json.dumps({"build_session": thread_id}))
        db.add(row)
        db.commit()
        return row.id


async def _helper_call(app, helper_sid: str, work, tool: str, args: dict) -> None:
    p = app.state.platform
    ctx = ToolContext(workspace=work, session_id=helper_sid, agent_run_id="run-helper-1510",
                      config=p.config, event_bus=p.event_bus, engine=p.engine)
    perms = PermissionEngine({**p.config.permissions, tool: "allow"})
    res = await p.registry.invoke(tool, args, ctx, perms)
    assert res.ok, res.error


def _ledger_row(app, session_id: str, tool: str, args: dict, *, called_as: str = "",
                reversibility: str = "irreversible") -> None:
    """A row as ``registry._record`` writes it for a call with no inverse."""
    with session_scope(app.state.platform.engine) as db:
        db.add(ToolInvocation(session_id=session_id, agent_run_id="run-1510", tool=tool,
                              args_json=json.dumps(args), ok=True, output="done",
                              reversibility=reversibility, called_as=called_as))
        db.commit()


async def _team_turn(app, work) -> tuple[str, str]:
    """One turn in which the lead hands the work to a helper; the helper
    writes a new file, edits an existing one, and runs a command. A helper
    write from BEFORE the turn must stay."""
    sid = new_session(app, work, approval_mode="yolo")["id"]
    helper = _helper(app, sid, work)
    # Before the turn: not this turn's work, never rewound.
    await _helper_call(app, helper, work, "write_file", {"path": "earlier.txt", "content": "kept\n"})
    wire(app, Script(["Handed it to a helper."]))
    status, raw = await open_turn(app, sid, {"text": "write the summary", "turn_id": "t1"})
    assert status == 200, raw
    assert frames_of(raw)[-1][0] == "done"
    # The lead's hand-off (Claude Code's `Agent`), then the helper's own work.
    _ledger_row(app, f"build:{sid}", "delegate", {"agent_type": "builder", "task": "write the summary"},
                called_as="Agent")
    await _helper_call(app, helper, work, "write_file", {"path": "summary.md", "content": "# Summary\n"})
    await _helper_call(app, helper, work, "edit_file",
                       {"path": "report.md", "old": "first draft", "new": "second draft"})
    return sid, helper


async def test_rewind_puts_back_what_a_helper_wrote_and_edited(app, work):
    sid, _helper_sid = await _team_turn(app, work)
    assert (work / "summary.md").exists()
    assert b"second draft" in (work / "report.md").read_bytes()

    status, preview = await asgi(app, "GET", f"/build/sessions/{sid}/rewind?to=t1")
    assert status == 200, preview
    assert preview["can_rewind"] is True and preview["problems"] == []
    assert {(f["rel"], f["change"]) for f in preview["files"]} == {
        ("summary.md", "removed"), ("report.md", "put back"),
    }
    # The hand-off is not a command left as it was: its helper's work goes back.
    assert preview["commands"] == []

    status, out = await asgi(app, "POST", f"/build/sessions/{sid}/rewind", {"to": "t1"})
    assert status == 200, out
    assert out["rewound"] is True and len(out["files"]) == 2
    assert not (work / "summary.md").exists()
    assert (work / "report.md").read_bytes() == b"# Report\n\nfirst draft\n"
    # The helper's write from before the turn is not touched.
    assert (work / "earlier.txt").read_text(encoding="utf-8") == "kept\n"
    assert "Agent" not in out["note"]


async def test_the_rewind_menu_counts_a_helpers_files(app, work):
    sid, _helper_sid = await _team_turn(app, work)
    status, points = await asgi(app, "GET", f"/build/sessions/{sid}/rewind")
    assert status == 200, points
    assert [(p["turn_id"], p["files"], p["commands"]) for p in points["checkpoints"]] == [("t1", 2, 0)]


async def test_a_helpers_command_is_still_named_as_not_undone(app, work):
    sid, helper = await _team_turn(app, work)
    _ledger_row(app, helper, "shell", {"command": "npm run build"})
    status, preview = await asgi(app, "GET", f"/build/sessions/{sid}/rewind?to=t1")
    assert status == 200, preview
    assert [(c["what"], c.get("helper")) for c in preview["commands"]] == [("npm run build", "builder")]
    status, points = await asgi(app, "GET", f"/build/sessions/{sid}/rewind")
    assert [(p["files"], p["commands"]) for p in points["checkpoints"]] == [(2, 1)]
    status, out = await asgi(app, "POST", f"/build/sessions/{sid}/rewind", {"to": "t1"})
    assert status == 200, out
    assert "`npm run build` (by the helper builder)" in out["note"]
    assert "not undone" in out["note"]
    assert not (work / "summary.md").exists()


async def test_a_lead_edit_and_a_helper_edit_of_one_file_go_back_in_order(app, work):
    """All rows, the session's and its helpers', are undone newest first as
    ONE sequence: the helper's later edit first, then the lead's earlier one."""
    sid, helper = await _team_turn(app, work)
    p = app.state.platform
    lead_ctx = ToolContext(workspace=work, session_id=f"build:{sid}", agent_run_id="run-lead-1510",
                           config=p.config, event_bus=p.event_bus, engine=p.engine)
    perms = PermissionEngine({**p.config.permissions, "edit_file": "allow"})
    res = await p.registry.invoke("edit_file", {"path": "report.md", "old": "second draft",
                                                "new": "third draft"}, lead_ctx, perms)
    assert res.ok, res.error
    await _helper_call(app, helper, work, "edit_file",
                       {"path": "report.md", "old": "third draft", "new": "fourth draft"})
    plan = R.plan(p.engine, p.config.home, p.registry, sid, "t1", p.permissions)
    assert plan.problems == []
    status, out = await asgi(app, "POST", f"/build/sessions/{sid}/rewind", {"to": "t1"})
    assert status == 200, out
    assert (work / "report.md").read_bytes() == b"# Report\n\nfirst draft\n"


def test_a_helper_starts_no_helpers_of_its_own():
    """One level is enough: the helper doors are never handed to a helper."""
    from iron_jarvis.harness.helpers import HELPER_TOOLS, NOT_FOR_HELPERS

    assert HELPER_TOOLS <= NOT_FOR_HELPERS


async def test_a_stopped_write_is_named_by_rewind_never_skipped_in_silence(app, work):
    """Lead review of T4. A write that failed or was stopped part-way keeps
    its Undo (it may have changed the file). Rewind does not put it back, so
    it must SAY so, with where to undo it."""
    from sqlmodel import select

    sid, helper = await _team_turn(app, work)
    await _helper_call(app, helper, work, "write_file", {"path": "half.txt", "content": "half\n"})
    # The row as the registry leaves a call that was cut off: not ok, its
    # inverse still in the journal.
    with session_scope(app.state.platform.engine) as db:
        rows = list(db.exec(select(ToolInvocation).where(ToolInvocation.tool == "write_file")))
        row = next(r for r in rows if "half.txt" in (r.args_json or ""))
        row.ok = False
        db.add(row)
        db.commit()

    status, preview = await asgi(app, "GET", f"/build/sessions/{sid}/rewind?to=t1")
    assert status == 200, preview
    assert "half.txt" not in {f["rel"] for f in preview["files"]}
    stopped = [c for c in preview["commands"] if c.get("stopped")]
    assert len(stopped) == 1 and "half.txt" in stopped[0]["what"], preview["commands"]
    assert "stopped part-way is not put back by Rewind" in preview["note"]
    assert "half.txt" in preview["note"] and "Activity page" in preview["note"]
    # ...and a helper's own command keeps its own sentence beside it.
    status, out = await asgi(app, "POST", f"/build/sessions/{sid}/rewind", {"to": "t1"})
    assert status == 200, out
    assert out["rewound"] is True and "is not put back by Rewind" in out["note"]
    assert (work / "half.txt").exists(), "the rewind touched a file it said it would not"
