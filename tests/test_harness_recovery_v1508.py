"""v1.508.0: the coding harness recovers from the failures the v1.500.2 ledger showed.

Read off the owner's live ledger (2026-11-15), one coding session with a team:

1. EDITS. A worker sent eleven refused ``edit_file`` calls on two test files
   another worker had already changed. It had never read either file; each
   refusal named one line and it guessed again (and probed with ``new: "x"``).
2. SHELL. ``rmdir /s /q a && ... && dir /b`` in Git's bash, and five
   ``powershell -Command "... $_.ProcessId ..."`` lines whose ``$_`` bash
   replaced before PowerShell read them.
3. GIT. Two reviewers opened with ``git_read status`` in a folder that is not
   a git repository and got one bare sentence.
4. DELEGATION. The lead listed the agents (every model-team member marked
   "team member") and handed a piece to ``custom:grok-4.7`` — not on the
   session's team.
5. RECOVERY. The identical-call breaker never saw loops whose arguments
   changed a little every time.
6. CLASSIFICATION. "Copy as a fix request" listed every refused call as an
   app defect: the model's own corrected mistakes and its probes included.
"""

from __future__ import annotations

import json
import os
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from iron_jarvis.harness import edit_memory as EM
from iron_jarvis.harness import failure_kind as K
from iron_jarvis.harness import textedit as T
from iron_jarvis.sandbox import git_bash as GB
from iron_jarvis.tools.base import ToolContext
from iron_jarvis.tools.builtins import EditFileTool, ReadFileTool, WriteFileTool


def _ctx(ws: Path, session: str = "s1") -> ToolContext:
    return ToolContext(workspace=ws, session_id=session, agent_run_id="r",
                       config=None, event_bus=None, engine=None)


#: The ledger's test file, as the earlier worker left it.
KILL_TREE = (
    'const { spawn } = require("child_process");\n'
    "let msgId = 0;\n"
    "function killTree(child){\n"
    '  try { child.kill("SIGTERM"); } catch {}\n'
    "}\n"
    "async function main() {\n"
    "  try {\n"
    "    await run();\n"
    "  } finally {\n"
    "    killTree(child);\n"
    "  }\n"
    "}\n"
)


# --------------------------------------------------------------------------- #
# 1. edits: the refusal shows the file as it is now
# --------------------------------------------------------------------------- #


def test_a_near_miss_shows_the_lines_around_the_closest_one():
    with pytest.raises(T.EditError) as err:
        T.apply_exact_edit(KILL_TREE, 'try { child.kill("SIGKILL"); } catch {}', "x2", where="t.js")
    msg = str(err.value)
    assert "The closest line in the file is line 4" in msg
    assert "Lines 1-7 of the file now:" in msg
    assert '  4|   try { child.kill("SIGTERM"); } catch {}' in msg
    assert "  1| const { spawn }" in msg and "  8|" not in msg


def test_text_that_differs_only_in_spacing_is_shown_exactly_and_never_applied():
    """The model's `function killTree(child) {` block with other indentation:
    the engine still refuses (strict), and hands back the file's exact text."""
    old = "function killTree(child){\n    try { child.kill(\"SIGTERM\"); } catch {}\n}"
    with pytest.raises(T.EditError) as err:
        T.apply_exact_edit(KILL_TREE, old, "function killTree(c){}", where="t.js")
    msg = str(err.value)
    assert "matches lines 3-5 only when the spacing" in msg
    exact = "function killTree(child){\n  try { child.kill(\"SIGTERM\"); } catch {}\n}"
    assert f"<<<\n{exact}\n>>>" in msg
    # ...and a copy of what it showed is the edit that works.
    new_text, n = T.apply_exact_edit(KILL_TREE, exact, "function killTree(c){}", where="t.js")
    assert n == 1 and "function killTree(c){}" in new_text


def test_a_spacing_match_in_several_places_names_each_line():
    text = "a\n  x = 1\nb\n    x = 1\n"
    with pytest.raises(T.EditError) as err:
        T.apply_exact_edit(text, "x = 1 ", "x = 2", where="f.py")
    assert "matches 2 places (lines 2 and 4)" in str(err.value)


def test_an_ambiguous_edit_names_the_lines_it_occurs_on():
    with pytest.raises(T.EditError) as err:
        T.apply_exact_edit(KILL_TREE, "  try {", "  try {{", where="t.js")
    assert "occurs 2 times in t.js (lines 4 and 7)" in str(err.value)


# --------------------------------------------------------------------------- #
# 1. edits: what this session has seen, and the gate after repeated refusals
# --------------------------------------------------------------------------- #


async def test_an_edit_from_text_the_session_never_read_says_so(tmp_path):
    """The ledger's case: the worker's `old` came from the lead's brief."""
    (tmp_path / "t.js").write_text(KILL_TREE, encoding="utf-8")
    res = await EditFileTool().execute(
        {"path": "t.js", "old": "  if (!child || child.killed) return;", "new": "  return;"},
        _ctx(tmp_path, "worker-a"))
    assert res.ok is False
    assert res.error.endswith(EM.NOT_READ.format(name="t.js"))


async def test_an_edit_after_another_writer_changed_the_file_says_it_changed(tmp_path):
    f = tmp_path / "t.js"
    f.write_text(KILL_TREE, encoding="utf-8")
    ctx = _ctx(tmp_path, "worker-b")
    assert (await ReadFileTool().execute({"path": "t.js"}, ctx)).ok
    # another worker (another session) rewrites it
    f.write_text(KILL_TREE.replace("killTree(child){", "killTree(child) {\n  if (!child) return;"),
                 encoding="utf-8")
    os.utime(f, ns=(f.stat().st_atime_ns, f.stat().st_mtime_ns + 5_000_000_000))
    res = await EditFileTool().execute(
        {"path": "t.js", "old": "function killTree(child){\n", "new": "function k(c){\n"}, ctx)
    assert res.ok is False and res.error.endswith(EM.CHANGED.format(name="t.js"))


async def test_a_current_view_adds_no_note(tmp_path):
    (tmp_path / "t.js").write_text(KILL_TREE, encoding="utf-8")
    ctx = _ctx(tmp_path, "worker-c")
    await ReadFileTool().execute({"path": "t.js"}, ctx)
    res = await EditFileTool().execute({"path": "t.js", "old": "nothing like it at all qq", "new": "y"}, ctx)
    assert res.ok is False
    assert "This session has not read" not in res.error and "changed after" not in res.error


async def test_after_three_refused_edits_of_a_file_the_next_waits_for_a_read(tmp_path):
    f = tmp_path / "t.js"
    f.write_text(KILL_TREE, encoding="utf-8")
    ctx = _ctx(tmp_path, "worker-d")
    for guess in ("  child.kill();", "  child.killed = true;", "  return child;"):
        res = await EditFileTool().execute({"path": "t.js", "old": guess, "new": "  y"}, ctx)
        assert res.ok is False and not (res.data or {}).get("gated")
    # the fourth is not tried — even one that would match
    right = '  try { child.kill("SIGTERM"); } catch {}'
    res = await EditFileTool().execute({"path": "t.js", "old": right, "new": "  y();"}, ctx)
    assert res.ok is False and res.data["gated"] is True
    assert res.error == EM.GATED.format(n=3, name="t.js")
    assert f.read_text(encoding="utf-8") == KILL_TREE
    # another session is not held by this one's count
    other = await EditFileTool().execute({"path": "t.js", "old": right, "new": "  z();"},
                                         _ctx(tmp_path, "worker-e"))
    assert other.ok
    # a read clears the gate; the edit from fresh text runs
    f.write_text(KILL_TREE, encoding="utf-8")
    assert (await ReadFileTool().execute({"path": "t.js"}, ctx)).ok
    res = await EditFileTool().execute({"path": "t.js", "old": right, "new": "  y();"}, ctx)
    assert res.ok, res.error
    assert "  y();" in f.read_text(encoding="utf-8")


async def test_a_write_counts_as_seen_and_clears_the_count(tmp_path):
    ctx = _ctx(tmp_path, "worker-f")
    (tmp_path / "t.js").write_text(KILL_TREE, encoding="utf-8")
    for _ in range(3):
        await EditFileTool().execute({"path": "t.js", "old": "zzz qqq www", "new": "y"}, ctx)
    assert EM.gate(ctx, (tmp_path / "t.js").resolve(), "t.js")
    assert (await WriteFileTool().execute({"path": "t.js", "content": KILL_TREE}, ctx)).ok
    assert EM.gate(ctx, (tmp_path / "t.js").resolve(), "t.js") == ""


async def test_multi_edit_carries_the_same_gate_and_notes(tmp_path):
    from iron_jarvis.harness.tools import MultiEditTool

    (tmp_path / "t.js").write_text(KILL_TREE, encoding="utf-8")
    ctx = _ctx(tmp_path, "worker-g")
    res = await MultiEditTool().execute(
        {"path": "t.js", "edits": [{"old": "nope nope nope", "new": "y"}]}, ctx)
    assert res.ok is False and res.error.endswith(EM.NOT_READ.format(name="t.js"))
    for _ in range(2):
        await MultiEditTool().execute({"path": "t.js", "edits": [{"old": "nope nope", "new": "y"}]}, ctx)
    res = await MultiEditTool().execute({"path": "t.js", "edits": [{"old": "let msgId", "new": "var msgId"}]}, ctx)
    assert res.ok is False and res.data["gated"] is True


# --------------------------------------------------------------------------- #
# 2. shell: bash is told apart from cmd.exe and PowerShell before anything runs
# --------------------------------------------------------------------------- #

#: The ledger's own lines.
RMDIR = "rmdir /s /q .edge_profile_gameplay && rmdir /s /q .edge_profile_smoketest && dir /b"
PS_DOUBLE = (
    "powershell -NoProfile -Command \"Get-CimInstance Win32_Process -Filter \\\"Name='msedge.exe'\\\" "
    "| Where-Object { $_.CommandLine -match 'edge_profile_(smoketest|gameplay|cap)' } | "
    "ForEach-Object { Write-Output ('KILL ' + $_.ProcessId); Stop-Process -Id $_.ProcessId -Force }; "
    "'DONE'\""
)


def test_cmds_folder_removal_in_bash_is_refused_before_it_runs():
    said = GB.bash_refusal(RMDIR)
    assert "`rmdir /s`" in said and "Nothing was run" in said and "rm -rf <folder>" in said
    assert GB.bash_refusal("rd /q old") != ""
    # bash's own folder removal is untouched
    assert GB.bash_refusal("rmdir empty_dir") == ""
    assert GB.bash_refusal("rm -rf .edge_profile_gameplay") == ""


def test_powershells_dollar_inside_double_quotes_is_refused_before_bash_eats_it():
    said = GB.bash_refusal(PS_DOUBLE)
    assert "`$_`" in said and "Nothing was run" in said and "-File" in said
    assert GB.bash_refusal('pwsh.exe -c "Write-Output $env:PATH"') != ""
    # what bash leaves alone runs
    assert GB.bash_refusal("powershell -NoProfile -Command 'Get-Process | Where-Object { $_.Id -gt 4 }'") == ""
    assert GB.bash_refusal('powershell -NoProfile -Command "Get-Process | Where-Object { \\$_.Id -gt 4 }"') == ""
    assert GB.bash_refusal("powershell -NoProfile -ExecutionPolicy Bypass -File _build/stop_edge.ps1") == ""
    assert GB.bash_refusal('kill "$PID_FILE"; echo "$_"') == ""  # bash's own $_ outside PowerShell


def test_the_hint_after_a_failed_rmdir_names_rmdir():
    hint = GB.windows_command_hint("rmdir /s /q x", "rmdir: failed to remove '/s'")
    assert "`rmdir` here is the GNU program" in hint and "rm -rf <folder>" in hint


@pytest.mark.skipif(os.name != "nt", reason="bash-written commands run in Git's bash on Windows only")
async def test_both_command_doors_refuse_without_running(tmp_path, monkeypatch):
    from iron_jarvis.core import procs
    from iron_jarvis.harness import process as P
    from iron_jarvis.sandbox import shell_tool as ST

    if GB.find() is None:
        pytest.skip("Git for Windows' bash is not installed here")
    ran: list = []

    def _no(*a, **k):
        ran.append(a)
        raise AssertionError("a command ran")

    monkeypatch.setattr(procs, "run", _no)
    monkeypatch.setattr(procs, "run_to_files", _no, raising=False)
    monkeypatch.setattr(procs, "spawn", _no)
    ws = tmp_path / "ws"
    ws.mkdir()
    ctx = ToolContext(workspace=ws, session_id="build:dialect", agent_run_id="r",
                      config=SimpleNamespace(home=tmp_path, sandbox={}), event_bus=None, engine=None,
                      called_as="Bash", shell_syntax="bash")
    res = await ST.SandboxedShellTool().execute({"command": RMDIR}, ctx)
    assert res.ok is False and "rm -rf <folder>" in (res.error or "")
    res = await ST.SandboxedShellTool().execute({"command": PS_DOUBLE}, ctx)
    assert res.ok is False and "`$_`" in (res.error or "")
    res = await P.ProcessStartTool().execute({"command": PS_DOUBLE}, ctx)
    assert res.ok is False and "`$_`" in (res.error or "")
    assert ran == []


# --------------------------------------------------------------------------- #
# 3. git: a folder with no git history
# --------------------------------------------------------------------------- #


async def test_git_read_outside_a_repository_says_what_to_use_instead(tmp_path):
    from iron_jarvis.harness import git_read as G

    folder = tmp_path / "plain"
    folder.mkdir()
    if G.in_repository(folder):
        pytest.skip("the temp folder sits inside a git repository on this machine")
    res = await G.GitReadTool().execute({"command": "status", "args": ["--short"]}, _ctx(folder))
    assert res.ok is False and res.data["not_a_repo"] is True
    assert "Do not call git_read again in this folder" in res.error and "read_file" in res.error
    assert K.classify("git_read", {}, res.error, res.data) == K.ENVIRONMENT


def test_in_repository_finds_git_above_the_folder(tmp_path):
    from iron_jarvis.harness import git_read as G

    (tmp_path / "repo" / ".git").mkdir(parents=True)
    (tmp_path / "repo" / "a" / "b").mkdir(parents=True)
    assert G.in_repository(tmp_path / "repo" / "a" / "b")
    (tmp_path / "wt").mkdir()
    (tmp_path / "wt" / ".git").write_text("gitdir: elsewhere\n", encoding="utf-8")  # a worktree
    assert G.in_repository(tmp_path / "wt")


def test_the_finish_reviewer_is_not_sent_to_git_where_there_is_none():
    from iron_jarvis.harness import finish as F

    with_git = F.review_brief("r", "d", "c", git=True)
    without = F.review_brief("r", "d", "c", git=False)
    assert "git_read" in with_git and "git_read" not in without
    assert "not in a git repository" in without
    assert F.review_lead(False).startswith("Review a change another model just made")


# --------------------------------------------------------------------------- #
# 4. delegation: the lead sees its real team
# --------------------------------------------------------------------------- #


def _team_turn():
    from iron_jarvis.harness.team import SessionTeam, TeamTurn

    return TeamTurn(team=SessionTeam(workers=("glm-5.3-flash",), reviewer="claude-opus-5-5"))


async def test_list_agents_in_a_team_turn_names_the_team_and_marks_everyone_else(monkeypatch):
    from iron_jarvis.agents import agent_tools as AT
    from iron_jarvis.agents import delegate_tool as DT
    from iron_jarvis.agents import model_team as MT
    from iron_jarvis.harness import helpers as H

    monkeypatch.setattr(MT, "member_facts", lambda platform, r: None)
    monkeypatch.setattr(DT, "_local_only_now", lambda: False)
    rows = [SimpleNamespace(name=n, base_type="builder", description="") for n in
            ("glm-5.3-flash", "grok-4.7", "claude-opus-5-5")]
    registry = SimpleNamespace(list=lambda: rows, definition=lambda name: None)
    tool = AT.ListAgentsTool(SimpleNamespace(), registry)
    ctx = SimpleNamespace(session_id="build:chat_x", agent_run_id="r")
    token = H.HELPER_PARENT.set(SimpleNamespace(session_id="build:chat_x", team=_team_turn()))
    try:
        res = await tool.execute({}, ctx)
    finally:
        H.HELPER_PARENT.reset(token)
    first = res.output.split("\n", 1)[0]
    assert first.startswith("This coding session's team")
    assert "workers custom:glm-5.3-flash; reviewer custom:claude-opus-5-5" in first
    grok = next(ln for ln in res.output.split("\n") if "grok-4.7" in ln)
    assert "NOT on this session's team" in grok
    glm = next(ln for ln in res.output.split("\n") if ln.strip().startswith("- glm-5.3-flash"))
    assert "NOT on" not in glm
    assert {r["name"]: r["on_session_team"] for r in res.data["dynamic"]} == {
        "glm-5.3-flash": True, "grok-4.7": False, "claude-opus-5-5": True}


async def test_list_agents_outside_a_team_turn_is_unchanged(monkeypatch):
    from iron_jarvis.agents import agent_tools as AT
    from iron_jarvis.agents import delegate_tool as DT
    from iron_jarvis.agents import model_team as MT

    monkeypatch.setattr(MT, "member_facts", lambda platform, r: None)
    monkeypatch.setattr(DT, "_local_only_now", lambda: False)
    rows = [SimpleNamespace(name="grok-4.7", base_type="builder", description="")]
    tool = AT.ListAgentsTool(SimpleNamespace(), SimpleNamespace(list=lambda: rows, definition=lambda n: None))
    res = await tool.execute({}, SimpleNamespace(session_id="chat", agent_run_id="r"))
    assert res.output.startswith("Built-in agents: ")
    assert "session's team" not in res.output and "on_session_team" not in res.data["dynamic"][0]


def test_an_off_team_refusal_says_what_to_tell_a_user_who_asked_for_that_model():
    said = _team_turn().off_team("custom:grok-4.7")
    assert said.startswith("'custom:grok-4.7' is not on this session's team.")
    assert "If the user asked for 'custom:grok-4.7' by name, tell them" in said
    assert _team_turn().off_team("custom:glm-5.3-flash") == ""


# --------------------------------------------------------------------------- #
# 5. recovery: the same target, different guesses
# --------------------------------------------------------------------------- #


def test_a_streak_on_one_target_earns_a_note_every_third_failure():
    from iron_jarvis.agents import runtime as RT

    assert RT.target_streak_note("edit_file", "t.js", 2) == ""
    note = RT.target_streak_note("edit_file", "t.js", 3)
    assert "failure 3 in a row of `edit_file` on t.js" in note and "Stop guessing" in note
    assert RT.target_streak_note("edit_file", "t.js", 4) == ""
    assert RT.target_streak_note("edit_file", "t.js", 6)
    assert RT.target_streak_note("edit_file", "", 3) == ""  # nothing named, nothing said
    # the user-facing copy drops it, like the identical-call note
    assert RT._user_facing_tool_output("edit_file", "refused" + note) == "refused"


def test_a_worker_is_told_to_read_errors_and_files_before_trying_again():
    from iron_jarvis.harness.team import worker_rules

    rules = worker_rules()
    assert "When a tool call fails, read the error" in rules
    assert "read a file before you edit it" in rules


# --------------------------------------------------------------------------- #
# 6. classification and recovery
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("tool, args, error, kind", [
    ("edit_file", {"old": "PLACEHOLDER_TO_TRIGGER_ERROR_MESSAGE", "new": "x"},
     "`old` text was not found in flappy.html.", K.PROBE),
    ("edit_file", {"old": "  child.kill();", "new": "  x"}, "`old` text was not found in t.js.", K.PROBE),
    ("edit_file", {"old": "a = 1", "new": "a = 2"}, "`old` text was not found in t.js.", K.MODEL),
    ("edit_file", {"old": "debug()\n", "new": ""}, "`old` text was not found in t.js.", K.MODEL),
    ("edit_file", {"old": "  try {", "new": "  try {{"}, "`old` text occurs 2 times in t.js", K.MODEL),
    ("delegate", {"agent_type": "custom:grok-4.7"},
     "'custom:grok-4.7' is not on this session's team. Hand this piece to a worker", K.MODEL),
    ("shell", {"command": RMDIR}, GB.bash_refusal(RMDIR), K.MODEL),
    ("git_read", {"command": "status"}, "This folder is not in a git repository.", K.ENVIRONMENT),
    ("shell", {"command": "rm -rf x"}, "rm: cannot remove 'x/LOG': Device or resource busy", K.ENVIRONMENT),
    ("read_file", {"path": "a"},
     'Traceback (most recent call last):\n  File "C:/app/iron_jarvis/tools/builtins.py", line 9\nKeyError: 1',
     K.HARNESS),
    ("tool_script", {"code": "x"},
     'Traceback:\n  File "iron_jarvis/repl/worker.py", line 1505\n  File "<repl>#7", line 4\nSyntaxError',
     K.MODEL),
    ("shell", {"command": "git push"}, "permission denied by your rules", K.POLICY),
    ("excel_live_edit", {}, "boom", K.UNKNOWN),
])
def test_whose_failure_it_is(tool, args, error, kind):
    assert K.classify(tool, args, error) == kind


def test_recovery_counts_a_mistake_put_right_on_the_same_target():
    calls = [
        ("edit_file", {"path": "a.js"}, False, "`old` text was not found in a.js."),
        ("read_file", {"path": "a.js"}, True, ""),
        ("edit_file", {"path": "a.js"}, True, ""),
        ("edit_file", {"path": "b.js"}, False, "`old` text occurs 2 times in b.js"),
        ("edit_file", {"path": "c.js", "old": "PLACEHOLDER", "new": "x"}, False, "`old` text was not found"),
        ("git_read", {"command": "status"}, False, "This folder is not in a git repository."),
    ]
    got = K.recovery(calls)
    assert got["model_mistakes"] == 2 and got["recovered"] == 1 and got["unrecovered"] == 1
    assert got["by_kind"][K.PROBE] == 1 and got["by_kind"][K.ENVIRONMENT] == 1 and got["failed"] == 4


def test_the_target_of_a_command_is_its_program_after_any_cd():
    assert K.target_of("shell", {"command": 'cd "C:/a b" && node smoke_test.js 2>&1'}) == "node"
    assert K.target_of("shell", {"command": "FOO=1 py.exe -c 1"}) == "py.exe"
    assert K.target_of("edit_file", {"path": "C:\\A\\b.js"}) == "c:/a/b.js"


def test_the_fix_request_leaves_out_corrected_mistakes_and_probes(tmp_path):
    """The report reads whose failure each call was: a mistake the model put
    right and a probe are left out (and counted); one never put right and a
    folder mismatch are listed under their own kinds — not as app defects."""
    from tests.test_fix_request_v14230 import HELPER, T0, _call, _engine, _helper_session, _report, _thread

    from iron_jarvis.core.db import session_scope

    engine = _engine(tmp_path)
    m = lambda n: T0 + timedelta(minutes=n)  # noqa: E731
    with session_scope(engine) as db:
        _thread(db)
        _helper_session(db)
        _call(db, iid="e1", sid=HELPER, tool="edit_file", ok=False, at=m(2),
              args={"path": "t.js", "old": "a = 1", "new": "a = 2"},
              output="`old` text was not found in t.js. No line of the file is close to it.")
        _call(db, iid="r1", sid=HELPER, tool="read_file", ok=True, at=m(3), args={"path": "t.js"})
        _call(db, iid="e2", sid=HELPER, tool="edit_file", ok=True, at=m(4),
              args={"path": "t.js", "old": "a = 3", "new": "a = 2"}, output="edited t.js")
        _call(db, iid="p1", sid=HELPER, tool="edit_file", ok=False, at=m(5),
              args={"path": "t.js", "old": "PLACEHOLDER_THAT_DOES_NOT_EXIST", "new": "x"},
              output="`old` text was not found in t.js. No line of the file is close to it.")
        _call(db, iid="u1", sid=HELPER, tool="edit_file", ok=False, at=m(6),
              args={"path": "u.js", "old": "b = 1", "new": "b = 2"},
              output="`old` text was not found in u.js. No line of the file is close to it.")
        _call(db, iid="g1", sid=HELPER, tool="git_read", ok=False, at=m(7),
              args={"command": "status"}, output="This folder is not in a git repository.")
        db.commit()
    text = _report(engine)["text"]
    assert "A model's mistake it did not put right" in text
    assert "A call that did not fit this PC or folder" in text
    assert "A tool call that failed" not in text
    assert "Left out: 1 failed call the model put right itself (1 of 2 model mistakes recovered)" in text
    assert "Left out: 1 intentional negative test" in text
    assert "PLACEHOLDER" not in text


def test_failure_kind_is_pure_on_odd_input():
    assert K.classify(None, "not json", None) == K.UNKNOWN
    assert K.target_of("edit_file", json.dumps({"path": "x.py"})) == "x.py"
    assert K.is_probe("multi_edit", {"edits": [{"old": "NONEXISTENT_TEXT", "new": "y"}]})


async def test_the_agent_loop_notes_the_third_different_guess_on_one_file(platform, tmp_path):
    """The REAL agent loop: three failed calls on one file, each with other
    arguments (the identical-call breaker sees three different calls)."""
    from tests.test_tool_loop_v1174 import _QueueTool, _run_loop

    class _EditDouble(_QueueTool):
        input_schema = {"type": "object", "properties": {"path": {"type": "string"},
                                                         "old": {"type": "string"}}}

    tool = _EditDouble("qedit", [False, False, False, True])
    msgs = await _run_loop(platform, tool, [{"path": "t.js", "old": f"guess {i}"} for i in range(4)])
    texts = [str(m.content) for m in msgs]
    assert "in a row of" not in texts[0] and "in a row of" not in texts[1]
    assert "failure 3 in a row of `qedit` on t.js, each time with different arguments" in texts[2]
    assert "stderr: no such thing" in texts[2]  # the note adds, never replaces
    assert "in a row of" not in texts[3]


def test_a_note_after_a_fenced_refusal_still_reads_as_plain_words():
    """A refusal of an outside-text tool is fenced (read_file since v1.502.0);
    a loop note appended after the fence must not hide the breaker's words."""
    from iron_jarvis.agents import runtime as RT
    from iron_jarvis.computeruse.safety import wrap_untrusted

    fenced = wrap_untrusted("refused: repeated-failure breaker — `read_file` has now failed 2 times")
    said = RT._user_facing_tool_output("read_file", fenced + RT.target_streak_note("read_file", "a.pdf", 3))
    assert said.startswith("Its last call to read_file was refused")


def test_the_daily_failure_scan_counts_only_failures_that_are_the_apps_to_fix(tmp_path):
    """Master's v1.507 daily scan ("Find out why X keeps failing") read every
    refused call as the tool's failure. A mistake the model put right, a
    probe and a folder with no git history are not the maintainer's to fix;
    a mistake never put right still counts."""
    from iron_jarvis.core.db import session_scope
    from iron_jarvis.core.ids import utcnow
    from iron_jarvis.core.models import PermissionMode, ToolInvocation
    from iron_jarvis.platform import build_platform

    p = build_platform(str(tmp_path))
    now = utcnow()
    miss = "`old` text was not found in t.js. No line of the file is close to it."

    def row(sid, tool, ok, minute, args, output=""):
        return ToolInvocation(session_id=sid, agent_run_id="r", tool=tool, ok=ok, output=output,
                              args_json=json.dumps(args), verdict=PermissionMode.ALLOW,
                              created_at=now - timedelta(minutes=60 - minute))

    with session_scope(p.engine) as db:
        for i in range(3):  # three guesses on t.js, then one that worked
            db.add(row("s_fixed", "edit_file", False, i, {"path": "t.js", "old": f"g{i}", "new": "y"}, miss))
        db.add(row("s_fixed", "edit_file", True, 5, {"path": "t.js", "old": "real", "new": "y"}, "edited t.js"))
        for i in range(3):  # probes
            db.add(row("s_probe", "edit_file", False, i, {"path": "t.js", "old": "PLACEHOLDER_X", "new": "x"}, miss))
        for i in range(3):  # no git history here
            db.add(row("s_git", "git_read", False, i, {"command": "status"},
                       "This folder is not in a git repository."))
        db.commit()
    assert p.improvement.scan_tool_failures(now=now) == []
    with session_scope(p.engine) as db:
        for i in range(3):  # never put right
            db.add(row("s_stuck", "edit_file", False, 10 + i, {"path": "u.js", "old": f"h{i}", "new": "y"},
                       miss.replace("t.js", "u.js")))
        db.commit()
    got = p.improvement.scan_tool_failures(now=now)
    assert [(c["tool"], c["failures"]) for c in got] == [("edit_file", 3)]
