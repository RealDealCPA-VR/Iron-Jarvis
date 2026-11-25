"""v1.572.0: THE HARNESS FITS A LOCAL MODEL A LITTLE BETTER.

The benchmark of glm-5.3-flash on v1.570.0 (48 tasks x 3 repeats, real coding
sessions) lost no task to a tool mistake, and still spent about 6% of its
tool calls on three of them. Each has a harness answer:

1. A LINUX COMMAND ON THE WINDOWS SHELL (about 90 calls, in 31 of 48 tasks).
   A Claude lead's ``Bash`` has run in Git for Windows' bash since v1.386.0
   and every helper's ``shell`` since v1.421.0; the LEAD on Jarvis's own tool
   names (glm and every unrecognised local model) was left on cmd.exe. Now a
   coding session's own ``shell`` / ``process_start`` write bash there too,
   decided once per turn by ``command_env.helper_command_facts`` (the words
   and the shell that runs them cannot disagree). No bash found, another OS,
   or a turn that is not a coding session's: exactly as before.
2. AN EDIT WHOSE TEXT IS NOWHERE IN THE FILE. A near miss has shown the
   lines around it since v1.508.0; "nothing close" said only "read the file
   again". A SHORT file is now shown whole in the refusal.
3. ANOTHER TOOL'S ARGUMENT NAMES (``new_string`` for ``new``). Renamed where
   a turn's calls are translated, BEFORE any check reads the call.

Driven through the real Build turn where it matters (stream lane, registry,
shell tool, real Git bash on this PC); only the model is scripted.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlmodel import select

from iron_jarvis.core import procs
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import ToolInvocation
from iron_jarvis.daemon.app import create_app
from iron_jarvis.harness import command_env as CE
from iron_jarvis.harness import dialects as D
from iron_jarvis.harness import finish as F
from iron_jarvis.harness import mode as M
from iron_jarvis.harness import sessions as S
from iron_jarvis.harness import textedit as T
from iron_jarvis.providers.adapters.base import ToolCall
from iron_jarvis.sandbox import git_bash
from iron_jarvis.tools.base import ToolContext
from tests.test_build_sessions_v1367 import Script, frames_of, new_session, open_turn, wire

WIN = os.name == "nt"
BASH = git_bash.find() if WIN else None
needs_bash = pytest.mark.skipif(BASH is None, reason="Git for Windows' bash is not installed here")
needs_win = pytest.mark.skipif(not WIN, reason="cmd.exe vs bash is a Windows question")

#: A scripted model the dialect table does not know: the generic tool set.
LOCAL = ("mock", "mock")
#: Two commands in bash; one echo with a long argument in cmd.exe.
BOTH = "echo one; echo two"


@pytest.fixture()
def app(tmp_path):
    a = create_app(str(tmp_path / "home"))
    yield a
    procs.end_all()


def _rows(app, tool: str) -> list[ToolInvocation]:
    with session_scope(app.state.platform.engine) as db:
        rows = [r for r in db.exec(select(ToolInvocation).order_by(ToolInvocation.created_at)) if r.tool == tool]
        for r in rows:
            db.expunge(r)
        return rows


def _spec(call: dict, name: str) -> dict:
    return next(s for s in call["specs"] if s["name"] == name)


def _lines(text: str) -> list[str]:
    return [x.strip() for x in (text or "").splitlines() if x.strip()]


async def _turn(app, work: Path, moves: list, turn_id: str, *, who=LOCAL, grant=()) -> Script:
    sid = new_session(app, work, provider=who[0], model=who[1], approval_mode="yolo")["id"]
    if grant:
        S.update(app.state.platform.engine, sid, setup={"granted_tools": list(grant)})
    script = Script([*moves, "Done."], who=who)
    wire(app, script)
    try:
        status, raw = await open_turn(app, sid, {"text": "go", "turn_id": turn_id}, timeout=90)
    finally:
        procs.end_session(f"build:{sid}")
    assert status == 200 and dict(frames_of(raw)).get("done"), raw[-600:]
    return script


def _platform(tmp_path):
    from iron_jarvis.platform import build_platform

    return build_platform(str(tmp_path / "home"))


def _built(platform, who=LOCAL, requested="auto", **kw) -> M.HarnessTurn:
    return M.build(registry=platform.registry, permissions=platform.permissions, requested=requested,
                   provider=who[0], model=who[1], stream=True, today="", **kw)


# --------------------------------------------------------------------------- #
# 1. the lead on Jarvis's own tool names writes bash
# --------------------------------------------------------------------------- #


def test_only_a_tool_set_with_no_command_tool_of_its_own_calls_shell_itself():
    assert D.own_command_tools(D.GENERIC)
    assert not D.own_command_tools(D.CLAUDE) and not D.own_command_tools(D.GPT)
    assert not D.own_command_tools(D.QWEN)
    assert M.own_command_tools("auto", "custom", "glm-5.3-flash")
    assert M.own_command_tools("auto", *LOCAL)
    assert not M.own_command_tools("auto", "anthropic", "claude-sonnet-4-5")
    assert not M.own_command_tools("no-such-dialect", *LOCAL)


@needs_bash
async def test_a_coding_sessions_own_shell_runs_in_bash_and_is_told_so(app, tmp_path):
    """Through the real stream lane: the model's ``shell`` AND ``process_start``
    run in bash (their output says so), the model was SHOWN bash's words for
    both, and the ledger rows record the language (the finish check re-runs a
    command in the shell its row names)."""
    work = tmp_path / "w"
    work.mkdir()
    script = await _turn(app, work, [
        [ToolCall(id="s1", name="shell", arguments={"command": BOTH})],
        [ToolCall(id="p1", name="process_start", arguments={
            "command": "echo started; echo again", "until_exit": True, "wait_ms": 15_000})],
    ], "own-bash", grant=["process_start"])
    (shell_row,) = _rows(app, "shell")
    assert shell_row.ok, shell_row.output
    assert {"one", "two"} <= set(_lines(shell_row.output)), shell_row.output
    assert shell_row.shell_syntax == "bash"
    (start_row,) = _rows(app, "process_start")
    assert _lines((start_row.output or "").split("Output:", 1)[-1])[:2] == ["started", "again"], start_row.output
    assert start_row.shell_syntax == "bash"
    first = script.calls[0]
    words = _spec(first, "shell")["description"]
    assert "Git for Windows' bash" in words and "Windows cmd.exe with" not in words
    # Jarvis's own shell has no run_in_background: it is told its own tool.
    assert "run_in_background" not in words
    assert "starts in Git for Windows' bash" in _spec(first, "process_start")["description"]


@needs_win
async def test_no_bash_found_keeps_cmd_and_its_words(app, tmp_path, monkeypatch):
    monkeypatch.setattr(git_bash, "find", lambda *a, **k: None)
    work = tmp_path / "w"
    work.mkdir()
    script = await _turn(app, work, [[ToolCall(id="s1", name="shell", arguments={"command": BOTH})]], "own-cmd")
    (row,) = _rows(app, "shell")
    assert "one; echo two" in (row.output or ""), row.output
    assert row.shell_syntax == ""
    words = _spec(script.calls[0], "shell")["description"]
    assert words == M.unreachable_cut(
        CE.build_shell_description(), set(script.calls[0]["tools"]), app.state.platform.registry.names())
    assert "Git for Windows' bash" not in words


@needs_bash
async def test_a_claude_coding_turn_is_untouched(app, tmp_path):
    """The Claude tool set keeps its own ``Bash`` words (run_in_background)
    and shows no ``shell`` of Jarvis's."""
    work = tmp_path / "w"
    work.mkdir()
    who = ("anthropic", "claude-sonnet-4-5")
    script = await _turn(app, work, [[ToolCall(id="b1", name="Bash", arguments={"command": BOTH})]],
                         "claude-bash", who=who)
    first = script.calls[0]
    assert "shell" not in first["tools"]
    assert "run_in_background" in _spec(first, "Bash")["description"]
    (row,) = _rows(app, "shell")
    assert row.shell_syntax == "bash" and row.called_as == "Bash"


def test_the_language_is_the_turns_never_an_arguments(tmp_path):
    platform = _platform(tmp_path)
    turn = _built(platform, own_shell_syntax="bash", start_note="\n\nNOTE")
    assert turn.own_shell_syntax == "bash" and turn.start_note == "\n\nNOTE"
    ctx = ToolContext(workspace=tmp_path, session_id="build:x", agent_run_id="r", config=platform.config,
                      event_bus=None, engine=None)
    calls = turn.translate_calls([
        ToolCall(id="1", name="shell", arguments={"command": "ls; pwd"}),
        ToolCall(id="2", name="process_start", arguments={"command": "npm run dev"}),
        ToolCall(id="3", name="read_file", arguments={"path": "a.py"}),
        # an argument can never ask for another shell
        ToolCall(id="4", name="shell", arguments={"command": "dir", "shell_syntax": "cmd"}),
    ])
    got = [(c.name, turn.ctx_for(ctx, c).shell_syntax) for c in calls]
    assert got == [("shell", "bash"), ("process_start", "bash"), ("read_file", ""), ("shell", "bash")]
    # Without the turn's decision: the platform's own shell, as before.
    plain = _built(platform)
    assert plain.own_shell_syntax == "" and plain.start_note == ""
    assert [plain.ctx_for(ctx, c).shell_syntax for c in plain.translate_calls(calls)] == ["", "", "", ""]
    # A note with no language says nothing (the words and the shell agree).
    assert _built(platform, start_note="\n\nNOTE").start_note == ""


def test_a_tool_set_with_its_own_command_tool_never_takes_the_decision(tmp_path):
    """Claude's, Codex's and Qwen Code's command tools carry their own shell
    language: the turn's own is not theirs to take."""
    platform = _platform(tmp_path)
    ctx = ToolContext(workspace=tmp_path, session_id="build:x", agent_run_id="r", config=platform.config,
                      event_bus=None, engine=None)
    for requested in ("claude", "gpt", "qwen"):
        turn = _built(platform, who=("anthropic", "claude-sonnet-4-5"), requested=requested,
                      own_shell_syntax="bash", start_note="\n\nNOTE")
        assert turn.own_shell_syntax == "" and turn.start_note == "", requested
        # a model writing the REAL name was shown no such tool: its own shell
        (call,) = turn.translate_calls([ToolCall(id="1", name="shell", arguments={"command": "dir"})])
        assert turn.ctx_for(ctx, call).shell_syntax == "", requested


def test_the_harness_own_check_command_keeps_the_shell_it_was_written_for(tmp_path):
    """A call the HARNESS makes (the finish check) carries the language it is
    marked with: a folder's own check command is the user's words (cmd.exe),
    a re-run of the model's bash command is bash."""
    platform = _platform(tmp_path)
    turn = _built(platform, own_shell_syntax="bash")
    ctx = ToolContext(workspace=tmp_path, session_id="build:x", agent_run_id="r", config=platform.config,
                      event_bus=None, engine=None)
    folder_check = ToolCall(id="f1", name="shell", arguments={"command": "npm test"})
    turn.mark_called_as("f1", "finish check")
    assert turn.ctx_for(ctx, folder_check).shell_syntax == ""
    rerun = ToolCall(id="f2", name="shell", arguments={"command": "pytest -q; echo done"})
    turn.mark_called_as("f2", "finish check", shell_syntax="bash")
    assert turn.ctx_for(ctx, rerun).shell_syntax == "bash"


def test_the_start_note_reaches_process_start_only(tmp_path):
    platform = _platform(tmp_path)
    turn = _built(platform, own_shell_syntax="bash", start_note="\n\nSTARTS-IN-BASH", extra=["process_start"])
    specs = {s["name"]: s for s in turn.specs(platform.registry)}
    plain = {s["name"]: s for s in _built(platform, extra=["process_start"]).specs(platform.registry)}
    if "process_start" not in specs:
        pytest.skip("process_start is not offered in this turn")
    assert specs["process_start"]["description"] == plain["process_start"]["description"] + "\n\nSTARTS-IN-BASH"
    assert all(specs[n] == plain[n] for n in specs if n != "process_start")


def test_an_earlier_part_of_the_reply_is_rerun_in_the_shell_it_ran_in(tmp_path):
    """A carried-on reply's cut part comes back from the ledger as (tool,
    args, called_as): Jarvis's own ``shell`` is the turn's own language."""
    platform = _platform(tmp_path)
    own = _built(platform, own_shell_syntax="bash")
    assert F.earlier_command_syntax(own, "", "shell") == "bash"
    assert F.earlier_command_syntax(own, "shell", "shell") == "bash"
    assert F.earlier_command_syntax(own, "", "process_start") == "bash"
    assert F.earlier_command_syntax(own, "", "read_file") == ""
    assert F.earlier_command_syntax(_built(platform), "", "shell") == ""
    claude = _built(platform, who=("anthropic", "claude-sonnet-4-5"), own_shell_syntax="bash")
    assert F.earlier_command_syntax(claude, "Bash", "shell") == "bash"
    assert F.earlier_command_syntax(claude, "", "shell") == ""
    assert F.earlier_command_syntax(None, "", "shell") == ""
    # ...and the finish check asks with the tool's name beside the name called.
    source = Path(F.__file__).read_text(encoding="utf-8")
    assert 'check.keep_command(name, args, "", _syntax(called, name))' in source
    assert "return earlier_command_syntax(harness, called_as, name)" in source


@needs_bash
def test_the_benchmark_shows_and_names_the_same_shell(tmp_path):
    """The benchmark's identity builds the turn a coding session builds: bash's
    words for Jarvis's own tool names, and bash in its fingerprint."""
    from iron_jarvis.harness_bench.runners import JarvisRunner

    platform = _platform(tmp_path)
    runner = JarvisRunner(platform, provider="custom", model="glm-5.3-flash")
    turn = runner.harness()
    assert turn.own_shell_syntax == "bash"
    shell = next(s for s in turn.specs(platform.registry) if s["name"] == "shell")
    assert "Git for Windows' bash" in shell["description"]
    assert runner._command_shell(turn) == CE.bash_identity() and runner._command_shell(turn).startswith("bash: ")


@needs_win
def test_the_benchmark_without_bash_names_no_bash(tmp_path, monkeypatch):
    from iron_jarvis.harness_bench.runners import JarvisRunner

    monkeypatch.setattr(git_bash, "find", lambda *a, **k: None)
    platform = _platform(tmp_path)
    runner = JarvisRunner(platform, provider="custom", model="glm-5.3-flash")
    turn = runner.harness()
    assert turn.own_shell_syntax == "" and runner._command_shell(turn) == ""


# --------------------------------------------------------------------------- #
# 2. a short file is shown whole after a missed edit
# --------------------------------------------------------------------------- #

GUESS = "zzzzzzzzzzzz qqqqqqqq"


def _missed(text: str) -> str:
    with pytest.raises(T.EditError) as err:
        T.apply_exact_edit(text, GUESS, "y", where="f.py")
    return str(err.value)


def test_a_short_file_is_shown_whole_with_its_line_numbers():
    said = _missed("alpha\n  beta\ngamma")
    assert said == "`old` text was not found in f.py." + T.NOTHING_CLOSE_SHOWN + "\n  1| alpha\n  2|   beta\n  3| gamma"
    assert "Read the file again" not in said
    # A file that ends in a newline shows no empty last line.
    assert _missed("alpha\nbeta\n").endswith("\n  1| alpha\n  2| beta")


def test_a_file_past_either_bound_says_read_it_as_before():
    lines = "\n".join(f"line {i}" for i in range(1, T.SHORT_FILE_LINES + 1))
    assert T.NOTHING_CLOSE_SHOWN in _missed(lines)
    assert _missed(lines + "\none more") == "`old` text was not found in f.py." + T.NOTHING_CLOSE
    wide = "x" * (T.SHORT_FILE_CHARS - 1)
    assert T.NOTHING_CLOSE_SHOWN in _missed(wide + "\n")
    assert _missed(wide + "xx") == "`old` text was not found in f.py." + T.NOTHING_CLOSE


def test_an_empty_file_is_not_shown():
    assert _missed("") == "`old` text was not found in f.py." + T.NOTHING_CLOSE
    assert _missed("\n\n  \n") == "`old` text was not found in f.py." + T.NOTHING_CLOSE


def test_a_near_miss_keeps_its_own_words():
    with pytest.raises(T.EditError) as err:
        T.apply_exact_edit("export const port = 3000;\n", "export const port = 3001;", "y", where="c.ts")
    assert "The closest line in the file is line 1" in str(err.value)
    assert T.NOTHING_CLOSE_SHOWN not in str(err.value)


async def test_the_edit_tool_hands_the_short_file_back(app, tmp_path):
    """Through the real turn: the refused edit's result holds the file, and
    the next edit, copied from it, lands."""
    work = tmp_path / "w"
    work.mkdir()
    (work / "config.py").write_text("PORT = 3000\nHOST = 'localhost'\n", encoding="utf-8")
    script = await _turn(app, work, [
        [ToolCall(id="e1", name="edit_file", arguments={"path": "config.py", "old": GUESS, "new": "y"})],
        [ToolCall(id="e2", name="edit_file", arguments={"path": "config.py", "old": "PORT = 3000", "new": "PORT = 8080"})],
    ], "short-file")
    first, second = _rows(app, "edit_file")
    assert not first.ok and second.ok, (first.output, second.output)
    told = "".join(str(getattr(m, "content", "")) for m in script.calls[1]["messages"])
    assert "  1| PORT = 3000" in told and "  2| HOST = 'localhost'" in told
    assert (work / "config.py").read_text(encoding="utf-8") == "PORT = 8080\nHOST = 'localhost'\n"


# --------------------------------------------------------------------------- #
# 3. another tool's argument names
# --------------------------------------------------------------------------- #


def test_the_names_that_are_renamed():
    assert D.own_arguments("edit_file", {"file_path": "a", "old_string": "x", "new_string": "y", "replace_all": True}) == {
        "path": "a", "old": "x", "new": "y", "replace_all": True}
    assert D.own_arguments("read_file", {"file_path": "a"}) == {"path": "a"}
    assert D.own_arguments("write_file", {"file_path": "a", "content": "c"}) == {"path": "a", "content": "c"}
    assert D.own_arguments("multi_edit", {"file_path": "a", "edits": [
        {"old_string": "x", "new_string": "y"}, {"old": "p", "new": "q"}, "not an edit"]}) == {
        "path": "a", "edits": [{"old": "x", "new": "y"}, {"old": "p", "new": "q"}, "not an edit"]}
    assert D.own_arguments("multi_edit", {"path": "a", "edits": [{"old_string": "x", "new": "y"}]}) == {
        "path": "a", "edits": [{"old": "x", "new": "y"}]}
    # An entry that is no object is left for the tool to refuse, never read as one.
    assert D.own_arguments("multi_edit", {"file_path": "a", "edits": [5, "old_string", None]}) == {
        "path": "a", "edits": [5, "old_string", None]}


def test_nothing_else_is_renamed():
    # the tool's own names: nothing to do
    assert D.own_arguments("edit_file", {"path": "a", "old": "x", "new": "y"}) is None
    assert D.own_arguments("multi_edit", {"path": "a", "edits": [{"old": "x", "new": "y"}]}) is None
    # both sent: the tool's own name stands, the other is left for the registry to see
    both = {"path": "a", "old": "x", "new": "y", "new_string": "z"}
    assert D.own_arguments("edit_file", both) is None
    # another tool, another name, arguments that are no object
    assert D.own_arguments("shell", {"file_path": "a"}) is None
    assert D.own_arguments("grep", {"file_path": "a"}) is None
    assert D.own_arguments("edit_file", {"path": "a", "old": "x", "new_type": "y"}) is None
    assert D.own_arguments("edit_file", None) is None and D.own_arguments("edit_file", "x") is None
    # the caller's arguments are never changed in place
    sent = {"file_path": "a", "old_string": "x", "new_string": "y"}
    D.own_arguments("edit_file", sent)
    assert sent == {"file_path": "a", "old_string": "x", "new_string": "y"}


def test_the_rename_happens_where_calls_are_translated(tmp_path):
    """Before any check reads the call: the approval rules, the card and the
    tool all see the same arguments."""
    platform = _platform(tmp_path)
    turn = _built(platform)
    sent = ToolCall(id="1", name="edit_file", arguments={"file_path": "a.py", "old_string": "x", "new_string": "y"})
    same = ToolCall(id="2", name="edit_file", arguments={"path": "a.py", "old": "x", "new": "y"})
    out = turn.translate_calls([sent, same])
    assert (out[0].id, out[0].name, out[0].arguments) == ("1", "edit_file", {"path": "a.py", "old": "x", "new": "y"})
    assert out[1] is same
    # A dialect's own tool keeps its own translation (Claude Code's Edit).
    claude = _built(platform, who=("anthropic", "claude-sonnet-4-5"), requested="claude")
    (edit,) = claude.translate_calls([ToolCall(id="3", name="Edit", arguments={
        "file_path": "a.py", "old_string": "x", "new_string": "y"})])
    assert edit.name == "edit_file" and edit.arguments["path"] == "a.py" and "file_path" not in edit.arguments


async def test_an_edit_sent_in_another_tools_names_lands(app, tmp_path):
    work = tmp_path / "w"
    work.mkdir()
    (work / "a.py").write_text("x = 1\n", encoding="utf-8")
    await _turn(app, work, [[ToolCall(id="e1", name="edit_file", arguments={
        "file_path": "a.py", "old_string": "x = 1", "new_string": "x = 2"})]], "alias-edit")
    (row,) = _rows(app, "edit_file")
    assert row.ok, row.output
    assert (work / "a.py").read_text(encoding="utf-8") == "x = 2\n"
    # The record holds what ran: the tool's own names.
    assert '"path"' in row.args_json and "file_path" not in row.args_json
