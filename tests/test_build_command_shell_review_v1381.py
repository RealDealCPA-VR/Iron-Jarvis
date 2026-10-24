"""v1.381.0 REVIEW: shell lines from a project's or a plugin's command always
ask — the ways round the rule the doer's pins did not cover, each found by
driving it, each red on the doer's code before its fix.

* A STEER NOTE in a continuation ended the rule: the next continuation looked
  for "the newest question that is not a continuation", found the steer note
  (it carries the CONTINUATION's turn id) and so found no command — every
  command of the carry-on after it ran with no card under "Don't ask".
* THE RECORD THAT FAILED TO WRITE was logged and the turn ran: the turn itself
  carded (its rule never read the record), but its continuation — which reads
  ONLY the record — ran every command with no card. A project's or a plugin's
  command whose lines cannot be kept on the session no longer starts.
* AN EMPTY SHELL LINE (a ```! block with nothing in it) made the command ask
  in its turn, and its record (no lines) was dropped by the whitelist — so its
  continuation ran with no card. A record now keeps a command with no lines
  when it names its turn.
* ONE COMMAND RUN OVER AND OVER filled the 16 places of the record with copies
  of itself and pushed an earlier command's lines out — a later turn running
  those lines no longer asked. A copy now replaces the older copy.
* A FOLDER'S PLUGIN CHECK (``plugins.toml`` ``[[checks]]``) ran through the
  registry and never through a spend check: in a command's own turn, with the
  shell allowed for the conversation, the folder's check ran with no card while
  every command the model ran asked. Now a check whose command would ask (this
  rule, or the git guard on a session's branch) is not run — the model is told
  to run it itself, with its own command tool, which asks.
* A LINE CONTINUATION (``\\`` or ``^`` at a line's end) hid a recorded line from
  the later-turn match, and a ```! block's continued command was recorded as
  two pieces.
* LOCAL ONLY hid WHY the card asks (its own line won), so the screen could not
  treat it as the safety card it is.
* THE WORDS: the card's line names what matched in a later turn; the model's
  note says exactly what the match does.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from sqlmodel import select

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import ToolInvocation
from iron_jarvis.harness import claude_commands as CC
from iron_jarvis.harness import command_shell as K
from iron_jarvis.harness import sessions as S
from iron_jarvis.providers.adapters.base import ToolCall
from tests.test_build_command_shell_ask_v1381 import (  # noqa: F401 — fixtures
    DEPLOY,
    answer,
    app,
    assert_command_card,
    bash,
    cards_of,
    cc,
    finish,
    nth_card,
    project_command,
    ran,
    start,
    work,
    write,
)
from tests.test_build_sessions_v1367 import (
    Script,
    frames_of,
    messages_of,
    new_session,
    open_turn,
    setup_of,
    turn_log,
    until,
    wire,
)
from tests.test_chat_turn_stop_v1241 import _asgi_post

PROJECT_WORDS = ("This turn runs the project's /deploy command, which has shell lines, so every "
                 "command in it asks you first.")


async def _command_turn_denied(app, sid: str, turn_id: str) -> None:
    """The project's /deploy command's own turn: one shell call, denied."""
    wire(app, Script([bash(f"{turn_id}-c", "echo IJ-FIRST"), "Done."]))
    drop, task = await start(app, sid, turn_id, command="deploy")
    await answer(app, await nth_card(app, turn_id, 0), "deny")
    await finish(app, turn_id, drop, task)


# --------------------------------------------------------------------------- #
# continuations
# --------------------------------------------------------------------------- #


async def test_a_steer_note_in_a_continuation_does_not_end_the_commands_rule(app, work):
    from iron_jarvis.daemon.routes.build import CONTINUE_TEXT, start_build_turn

    project_command(work)
    sid = new_session(app, work, approval_mode="yolo")["id"]
    await _command_turn_denied(app, sid, "st-0")
    # The daemon's first carry-on: the user steers it while it works.
    gate = asyncio.Event()

    async def wait_then_read(_script):
        await gate.wait()
        return [ToolCall(id="st1-r", name="Read", arguments={"file_path": "app.py"})]

    wire(app, Script([wait_then_read, "Cut off again."]))
    lg = await start_build_turn(app.state.d, sid, CONTINUE_TEXT, "st-1", continuation=True)
    assert (await _asgi_post(app, "/chat/turns/st-1/steer", {"text": "keep it short"}))[0] == 200
    gate.set()
    await until(lambda: lg.status != "running", 60)
    lines = messages_of(app, sid)
    assert [m.get("steer") for m in lines if m.get("turnId") == "st-1" and m["role"] == "user"] == [None, True], \
        "the steer note was saved as the user's line of the carry-on"
    # The daemon's SECOND carry-on is still the command's work: it asks.
    wire(app, Script([bash("st2-c", "echo IJ-SECOND-CARRY-ON"), "Done."]))
    lg2 = await start_build_turn(app.state.d, sid, CONTINUE_TEXT, "st-2", continuation=True)
    card = await nth_card(app, "st-2", 0)
    assert_command_card(card, words=PROJECT_WORDS)
    await answer(app, card, "deny")
    await until(lambda: lg2.status != "running", 60)
    assert not ran(app, "IJ-SECOND-CARRY-ON")


def test_the_turn_a_continuation_carries_on_skips_steer_notes_and_continuations():
    setup = {K.SETUP_KEY: [{"name": "deploy", "source": "project", "turn_id": "T1", "lines": ["echo x"]}]}
    lines = [
        {"role": "user", "content": "/deploy", "turnId": "T1"},
        {"role": "user", "content": "faster", "turnId": "T1", "steer": True},
        {"role": "assistant", "content": "limit", "turnId": "T1"},
        {"role": "user", "content": "carry on", "turnId": "C1", "continuation": True},
        {"role": "user", "content": "keep it short", "turnId": "C1", "steer": True},
        {"role": "assistant", "content": "limit again", "turnId": "C1"},
    ]
    rule = K.rule_for_turn(setup, lines, continuation=True)
    assert rule is not None and rule.started_by is not None and rule.started_by.turn_id == "T1"
    # A turn the USER typed after it is what a later carry-on continues.
    typed = [*lines, {"role": "user", "content": "now this", "turnId": "T2"},
             {"role": "user", "content": "and that", "turnId": "T2", "steer": True}]
    assert K.rule_for_turn(setup, typed, continuation=True).started_by is None


# --------------------------------------------------------------------------- #
# the record
# --------------------------------------------------------------------------- #


async def test_a_project_command_does_not_start_when_its_lines_cannot_be_kept(app, work, monkeypatch):
    project_command(work)
    sid = new_session(app, work, approval_mode="yolo")["id"]
    real = S.update

    def failing(engine, thread_id, *, title=None, setup=None):
        if setup and K.SETUP_KEY in setup:
            raise RuntimeError("database is locked")
        return real(engine, thread_id, title=title, setup=setup)

    monkeypatch.setattr(S, "update", failing)
    script = wire(app, Script([bash("rf-c", "echo IJ-UNRECORDED"), "Done."]))
    status, raw = await open_turn(app, sid, {"text": "", "command": "deploy", "turn_id": "rf-0"}, timeout=30)
    assert status == 503, (status, raw[-400:])
    assert "could not keep" in json.loads(raw)["detail"]
    assert script.calls == [], "no model was asked"
    assert messages_of(app, sid) == [], "no question was written"
    assert turn_log(app, "rf-0") is None
    assert not ran(app, "IJ-UNRECORDED")


async def test_a_record_that_is_lost_never_uncards_the_commands_own_turn(app, work, monkeypatch):
    """The turn's rule is the command it runs, never the record: a write that
    silently keeps nothing still leaves every command of the turn asking."""
    project_command(work)
    sid = new_session(app, work, approval_mode="yolo")["id"]
    real = S.update

    def forgetful(engine, thread_id, *, title=None, setup=None):
        if setup and K.SETUP_KEY in setup:
            return S.get_row(engine, thread_id)
        return real(engine, thread_id, title=title, setup=setup)

    monkeypatch.setattr(S, "update", forgetful)
    wire(app, Script([bash("rl-c", "echo IJ-LOST"), "Done."]))
    drop, task = await start(app, sid, "rl-0", command="deploy")
    card = await nth_card(app, "rl-0", 0)
    assert_command_card(card, words=PROJECT_WORDS)
    await answer(app, card, "deny")
    await finish(app, "rl-0", drop, task)
    assert K.SETUP_KEY not in setup_of(app, sid) and not ran(app, "IJ-LOST")


async def test_an_empty_shell_block_asks_and_so_does_its_continuation(app, work):
    from iron_jarvis.daemon.routes.build import CONTINUE_TEXT, start_build_turn

    project_command(work, "deploy", "Deploy $ARGUMENTS.\n```!\n```\nThen run whatever the README says.\n")
    sid = new_session(app, work, approval_mode="yolo")["id"]
    await _command_turn_denied(app, sid, "eb-0")
    kept = setup_of(app, sid)[K.SETUP_KEY]
    assert kept == [{"name": "deploy", "source": "project", "plugin": "", "turn_id": "eb-0", "lines": []}]
    wire(app, Script([bash("eb1-c", "echo IJ-EMPTY-CARRY-ON"), "Done."]))
    lg = await start_build_turn(app.state.d, sid, CONTINUE_TEXT, "eb-1", continuation=True)
    card = await nth_card(app, "eb-1", 0)
    assert_command_card(card, words=PROJECT_WORDS)
    await answer(app, card, "deny")
    await until(lambda: lg.status != "running", 60)
    assert not ran(app, "IJ-EMPTY-CARRY-ON")


def test_a_record_with_no_lines_needs_its_turn_and_matches_nothing_later():
    stored = K.clean_record([
        {"name": "a", "source": "project", "turn_id": "t1", "lines": []},
        {"name": "b", "source": "project", "lines": []},
    ])
    assert stored == [{"name": "a", "source": "project", "plugin": "", "turn_id": "t1", "lines": []}]
    token = K.set_turn_rule(K.TurnRule(earlier=tuple(K.entries(stored))))
    try:
        assert K.command_check("echo anything") is None
    finally:
        K.reset_turn_rule(token)


def test_running_one_command_again_never_pushes_another_commands_lines_out():
    deploy = K.CommandLines(name="deploy", source="project", plugin="", turn_id="t0", lines=("npm run deploy",))
    setup = {K.SETUP_KEY: K.recorded_with({}, deploy)}
    for i in range(K.MAX_RECORDED + 4):
        status = K.CommandLines(name="status", source="project", plugin="", turn_id=f"s{i}",
                                lines=("git status --short",))
        setup = {K.SETUP_KEY: K.recorded_with(setup, status)}
    kept = K.entries(setup[K.SETUP_KEY])
    assert [e.name for e in kept] == ["deploy", "status"]
    assert kept[-1].turn_id == f"s{K.MAX_RECORDED + 3}", "the newest copy keeps its turn (a carry-on reads it)"
    token = K.set_turn_rule(K.TurnRule(earlier=tuple(kept)))
    try:
        assert K.command_check("npm run deploy").ask is True
    finally:
        K.reset_turn_rule(token)


async def test_rewinding_the_commands_turn_keeps_the_record_so_it_only_asks_more(app, work):
    from tests.test_build_sessions_v1367 import asgi

    project_command(work)
    sid = new_session(app, work, approval_mode="yolo")["id"]
    await _command_turn_denied(app, sid, "rw-0")
    before = setup_of(app, sid)[K.SETUP_KEY]
    status, out = await asgi(app, "POST", f"/build/sessions/{sid}/rewind", {"to": "rw-0"})
    assert status == 200, out
    assert messages_of(app, sid) == [], "the command's turn is gone from the conversation"
    assert setup_of(app, sid)[K.SETUP_KEY] == before, "its record is not"
    # The line still asks in a later turn; another command follows "Don't ask".
    wire(app, Script([bash("rw1-a", "echo IJ-STATUS"), bash("rw1-b", "echo IJ-AFTER-REWIND"), "Done."]))
    drop, task = await start(app, sid, "rw-1", text="run the status line")
    card = await nth_card(app, "rw-1", 0)
    assert card["once_reason"] == "command" and card["args"]["command"] == "echo IJ-STATUS"
    await answer(app, card, "deny")
    await finish(app, "rw-1", drop, task)
    assert len(cards_of(app, "rw-1")) == 1 and ran(app, "IJ-AFTER-REWIND") and not ran(app, "IJ-STATUS")


@pytest.mark.parametrize("command", [
    "", "echo x", "git push origin HEAD", "rm -rf /", "npm run deploy", "python -c 1", "fi", "   ",
])
@pytest.mark.parametrize("rule", ["none", "started", "earlier"])
def test_the_rule_only_ever_asks_it_never_lets_a_call_skip_its_card(command, rule):
    """``SpendCheck(ask=False)`` means "free: run with no card" (a media
    allowance). This rule must never answer it — a record is read as ask,
    never as allow."""
    entry = K.CommandLines(name="deploy", source="plugin", plugin="tools", turn_id="t", lines=("npm run deploy",))
    armed = {"none": None, "started": K.TurnRule(started_by=entry), "earlier": K.TurnRule(earlier=(entry,))}[rule]
    token = K.set_turn_rule(armed)
    try:
        check = K.command_check(command)
    finally:
        K.reset_turn_rule(token)
    assert check is None or (check.ask is True and check.once_only is True)


# --------------------------------------------------------------------------- #
# a folder's plugin check
# --------------------------------------------------------------------------- #


def _plugin_check(folder: Path, command: str) -> None:
    write(folder / ".ironjarvis" / "harness" / "plugins.toml",
          f'[[checks]]\nfiles = ["*.py"]\ncommand = "{command}"\n')


def _check_rows(app) -> list[tuple[str, bool]]:
    with session_scope(app.state.platform.engine) as db:
        return [(r.tool, r.ok) for r in db.exec(select(ToolInvocation)) if r.called_as == "plugin check"]


async def test_a_folders_plugin_check_never_runs_on_its_own_in_a_commands_turn(app, work):
    project_command(work)
    _plugin_check(work, "echo IJ-CHECKED")
    sid = new_session(app, work, approval_mode="approve_for_me", granted_tools=["shell"])["id"]
    edit = [ToolCall(id="pc-e", name="Edit", arguments={"file_path": "app.py", "old_string": "x = 1",
                                                        "new_string": "x = 2"})]
    # Control: in an ordinary turn the user's grant runs the folder's check.
    script = wire(app, Script([edit, "Done."]))
    status, raw = await open_turn(app, sid, {"text": "set x", "turn_id": "pc-0"}, timeout=60)
    assert status == 200 and frames_of(raw)[-1][0] == "done", raw[-400:]
    assert ("shell", True) in _check_rows(app)
    assert "Check `echo IJ-CHECKED` (passed)" in [m for m in script.calls[1]["messages"] if m.role == "tool"][0].content
    # The project's command's turn: the same grant, the same edit — the check
    # is the model's to run, and so it asks.
    (work / "app.py").write_bytes(b"x = 1\n")
    edit2 = [ToolCall(id="pc-e2", name="Edit", arguments={"file_path": "app.py", "old_string": "x = 1",
                                                         "new_string": "x = 3"})]
    script = wire(app, Script([edit2, "Done."]))
    status, raw = await open_turn(app, sid, {"text": "", "command": "deploy", "turn_id": "pc-1"}, timeout=60)
    assert status == 200 and frames_of(raw)[-1][0] == "done", raw[-400:]
    assert (work / "app.py").read_bytes() == b"x = 3\n"
    assert len(_check_rows(app)) == 1, "the folder's check did not run in the command's turn"
    told = [m for m in script.calls[1]["messages"] if m.role == "tool"][0].content
    assert "Check not run (running it needs the user's approval)" in told and "echo IJ-CHECKED" in told
    assert cards_of(app, "pc-1") == []


async def test_a_plugin_check_the_git_guard_would_card_is_not_run_on_its_own(app, work):
    """The same seam closes the git guard's gap: a folder's check that
    pushes, on a session's own branch, was run with the user's grant."""
    from iron_jarvis.daemon.chat_turn import harness_check_runner
    from iron_jarvis.harness import branches as B
    from iron_jarvis.tools.base import ToolContext

    platform = app.state.platform
    called: list = []
    real_invoke = platform.registry.invoke

    async def invoke(name, args, *a, **kw):
        called.append((name, dict(args)))
        return await real_invoke(name, args, *a, **kw)

    platform.registry.invoke = invoke
    branch = B.set_turn_branch({"repo": str(work), "worktree": str(work), "branch": "ij/x", "base": "main",
                                "checkout": str(work)}, str(work))
    try:
        run = harness_check_runner(app.state.d, ToolContext(session_id="build:x", workspace=work,
                                                            agent_run_id="", config=platform.config,
                                                            event_bus=platform.event_bus,
                                                            engine=platform.engine),
                                   {}, lambda: {"shell"}, {"shell"}, {"low": False, "tainted": False})
        status, text = await run("git push origin HEAD", 10)
    finally:
        B.reset_turn_branch(branch)
        platform.registry.invoke = real_invoke
    assert status == "refused" and called == []
    assert "push" in text.lower()


# --------------------------------------------------------------------------- #
# matching: line continuations
# --------------------------------------------------------------------------- #


def test_a_continued_line_is_recorded_whole_and_matched_through_a_continuation(cc, work):
    project_command(work, "ship", "Ship.\n```!\nnpm run build \\\n  -- --prod\n```\n")
    found = {c.name: c for c in CC.discover([str(work)]).commands}
    lines = CC.expand(found["ship"], "", folder=str(work), read_room=4).lines
    assert lines == ["npm run build -- --prod"]
    entry = K.CommandLines(name="ship", source="project", plugin="", turn_id="t", lines=tuple(lines))
    assert K.runs_a_line("npm run build -- --prod", entry)
    assert K.runs_a_line("cd web && npm run build \\\n   -- --prod", entry), "bash"
    assert K.runs_a_line("npm run build ^\r\n -- --prod", entry), "cmd"
    assert K.runs_a_line("npm run build `\n -- --prod", entry), "PowerShell"
    assert not K.runs_a_line("npm run build", entry)


# --------------------------------------------------------------------------- #
# Local only keeps the reason
# --------------------------------------------------------------------------- #


async def test_under_local_only_the_card_still_says_why_it_asks(app, work):
    from iron_jarvis.daemon.schemas import ChatBody, ChatMessageBody
    from tests.test_harness_mode_lanes_v1366 import _stream_answering

    platform = app.state.platform
    wire(app, Script([[ToolCall(id="lo1", name="shell", arguments={"command": "echo IJ-LOCAL"})], "Done."],
                     who=("mock", "mock")))
    body = ChatBody(messages=[ChatMessageBody(role="user", content="deploy")], provider="mock", model="mock",
                    workspace_dir=str(work), approval_mode="yolo", local_only=True)
    entry = K.CommandLines(name="deploy", source="project", plugin="", turn_id="t1", lines=("echo IJ-STATUS",))
    token = K.set_turn_rule(K.TurnRule(started_by=entry))
    try:
        frames = await _stream_answering(platform, body, "conversation", harness=True)
    finally:
        K.reset_turn_rule(token)
    cards = [d for e, d in frames if e == "approval"]
    assert len(cards) == 1 and cards[0]["once_only"] is True and cards[0]["once_reason"] == "command"
    assert cards[0]["cost_note"] == PROJECT_WORDS
    assert [d["decision"] for e, d in frames if e == "approval_resolved"] == ["once"]


# --------------------------------------------------------------------------- #
# the words
# --------------------------------------------------------------------------- #


def test_a_later_turns_card_names_the_line_that_matched():
    entry = K.CommandLines(name="deploy", source="project", plugin="", turn_id="t",
                           lines=("echo IJ-STATUS", "npm run build"))
    token = K.set_turn_rule(K.TurnRule(earlier=(entry,)))
    try:
        check = K.command_check("cd web && npm run build -- --prod")
    finally:
        K.reset_turn_rule(token)
    assert check.words == "This runs “npm run build”, a shell line from the project's /deploy command."
    long = K.CommandLines(name="x", source="plugin", plugin="tools", turn_id="t", lines=("echo " + "a" * 300,))
    words = K.line_words(long, long.lines[0])
    assert words.startswith("This runs “echo aaa") and "…”, a shell line from the tools plugin's /x" in words
    assert len(words) < 200


def test_a_secret_in_a_matched_line_is_masked_on_the_card():
    entry = K.CommandLines(name="deploy", source="project", plugin="", turn_id="t",
                           lines=("curl -H 'Authorization: Bearer sk-abcdefghijklmnopqrstuvwxyz0123' x",))
    words = K.line_words(entry, entry.lines[0])
    assert "sk-abcdefghijklmnopqrstuvwxyz0123" not in words


def test_the_models_note_says_exactly_what_asks():
    note = CC.asks_first_note("project", "")
    assert note == (
        "This command comes from this project's command files, not from the user, so the user is asked "
        "before each command run in this turn, and in a later turn before any command that contains one "
        "of these lines, whatever this session's approval setting. Wait for that answer; if the user "
        "declines, say what you could not do."
    )
    assert "the tools plugin, not from the user" in CC.asks_first_note("plugin", "tools")


# --------------------------------------------------------------------------- #
# which commands are the user's own
# --------------------------------------------------------------------------- #


async def test_a_session_in_the_home_folder_reads_your_own_commands_as_yours(app, tmp_path, monkeypatch):
    """With no CLAUDE_CONFIG_DIR, ``~/.claude/commands`` is BOTH the personal
    folder and, for a session working in the home folder, that folder's
    project commands: it is listed once, as yours, and follows the setting."""
    fake_home = tmp_path / "fake-home"
    fake_home.mkdir()
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    write(fake_home / ".claude" / "commands" / "deploy.md", DEPLOY)
    found = CC.discover([str(fake_home)]).commands
    assert [(c.name, c.source) for c in found] == [("deploy", "personal")]
    sid = new_session(app, fake_home, approval_mode="yolo")["id"]
    wire(app, Script([bash("hm-c", "echo IJ-HOME-MINE"), "Done."]))
    status, raw = await open_turn(app, sid, {"text": "", "command": "deploy", "turn_id": "hm-0"}, timeout=60)
    assert status == 200 and frames_of(raw)[-1][0] == "done", raw[-400:]
    assert cards_of(app, "hm-0") == [] and ran(app, "IJ-HOME-MINE")


async def test_with_another_config_folder_the_home_folders_commands_are_a_projects(app, cc, tmp_path, monkeypatch):
    """With CLAUDE_CONFIG_DIR elsewhere, Claude Code itself reads
    ``~/.claude/commands`` as the open folder's PROJECT commands — so they ask."""
    fake_home = tmp_path / "fake-home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    write(fake_home / ".claude" / "commands" / "deploy.md", DEPLOY)
    assert [(c.name, c.source) for c in CC.discover([str(fake_home)]).commands] == [("deploy", "project")]
    sid = new_session(app, fake_home, approval_mode="yolo")["id"]
    wire(app, Script([bash("hp-c", "echo IJ-HOME-PROJECT"), "Done."]))
    drop, task = await start(app, sid, "hp-0", command="deploy")
    card = await nth_card(app, "hp-0", 0)
    assert_command_card(card, words=PROJECT_WORDS)
    await answer(app, card, "deny")
    await finish(app, "hp-0", drop, task)
    assert not ran(app, "IJ-HOME-PROJECT")


async def test_a_plugin_the_user_installed_still_asks_even_when_a_personal_command_shares_its_folder(app, cc, work):
    from tests.test_build_command_shell_ask_v1381 import plugin_command

    plugin_command(cc)
    write(cc / "commands" / "mine.md", "Mine.\n!`echo IJ-MINE`\n")
    names = {c.name: c.source for c in CC.discover([str(work)]).commands}
    assert names == {"mine": "personal", "tools:deploy": "plugin"}
