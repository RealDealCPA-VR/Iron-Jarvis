"""v1.381.0: shell lines from a PROJECT's or a PLUGIN's Claude Code command
always ask first, in every approval setting.

THE USER (2026-10-23, final): "Shell lines from a project's or plugin's
command files should always ask before running, even when the session
approves things for me. My own command files can follow the session's
setting."

Before: ``harness/claude_commands`` expanded a command file and asked the
MODEL to run its ``!`…``` / ```` ```! ```` lines with its own shell tool — so
under "Approve for me" or "Don't ask" a repository's command file had them run
with no card. Pinned here, through the REAL Build route, the real stream lane
and the real registry ``invoke`` (only the model is scripted; commands really
run where the user allows them):

* a project's or a plugin's command with shell lines: EVERY command-running
  call of its turn (``shell``, ``process_start``, ``process_write``) cards
  ALONE in every posture, past every grant (conversation, standing), "Allow
  once" and Deny only — a wider answer is read as once and the next call asks
  again; the card names the command and why (``once_reason`` "command");
* the model is told so; a personal command keeps the session's posture (no
  card under yolo); a command with no shell lines changes nothing;
* a helper the turn starts asks the same way (and its card says once only);
* a LATER turn of the session cards a command that runs one of those lines
  (recorded on the session, matched after normalizing quotes and spaces) and
  nothing else; a CONTINUATION of the command's turn (the daemon's own "carry
  on") is that turn's work, so it cards every command; Branch from here keeps
  the record;
* the POST lane, which can show no card, refuses those calls in words;
* low trust (the program tools kept away, the shell isolated) and Local only
  (its own "Allow once" card) are unchanged.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from sqlmodel import select

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.grants import StandingGrantRecord
from iron_jarvis.core.grants import args_hash as grant_hash
from iron_jarvis.core.models import ToolInvocation
from iron_jarvis.daemon.app import create_app
from iron_jarvis.harness import claude_commands as CC
from iron_jarvis.harness import command_shell as K
from iron_jarvis.providers.adapters.base import ToolCall
from tests.test_build_sessions_v1367 import (
    Script,
    asgi,
    frames_of,
    messages_of,
    new_session,
    open_turn,
    setup_of,
    turn_frames,
    turn_log,
    until,
    wire,
)
from tests.test_chat_turn_stop_v1241 import _asgi_post

GPT = ("openai", "gpt-5.1")


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))
    return path


@pytest.fixture()
def cc(tmp_path, monkeypatch) -> Path:
    """A scratch Claude Code folder (the user's real ~/.claude is never read)."""
    root = tmp_path / "claude-config"
    root.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(root))
    monkeypatch.delenv("CLAUDE_CODE_PLUGIN_CACHE_DIR", raising=False)
    return root


@pytest.fixture()
def app(tmp_path, cc):
    a = create_app(str(tmp_path / "home"))
    a.state.platform.config.chat_files_root = str(tmp_path / "chat-files")
    return a


@pytest.fixture()
def work(tmp_path) -> Path:
    folder = tmp_path / "work"
    folder.mkdir()
    (folder / "app.py").write_bytes(b"x = 1\n")
    return folder


#: A project command whose shell line is harmless.
DEPLOY = "---\ndescription: Deploy\n---\nDeploy $ARGUMENTS.\n- Status: !`echo IJ-STATUS`\n"


def project_command(work: Path, name: str = "deploy", text: str = DEPLOY) -> None:
    write(work / ".claude" / "commands" / f"{name}.md", text)


def plugin_command(cc: Path, plugin: str = "tools", name: str = "deploy", text: str = DEPLOY) -> None:
    root = cc / "plugins" / "cache" / "mkt" / plugin / "1.0"
    write(root / ".claude-plugin" / "plugin.json", json.dumps({"name": plugin}))
    write(root / "commands" / f"{name}.md", text)
    write(cc / "plugins" / "installed_plugins.json", json.dumps({"version": 2, "plugins": {
        f"{plugin}@mkt": [{"scope": "user", "installPath": str(root)}],
    }}))


def bash(call_id: str, command: str, **extra) -> list[ToolCall]:
    return [ToolCall(id=call_id, name="Bash", arguments={"command": command, **extra})]


def shell_rows(app, tool: str = "shell") -> list[ToolInvocation]:
    with session_scope(app.state.platform.engine) as db:
        rows = [r for r in db.exec(select(ToolInvocation).order_by(ToolInvocation.created_at)) if r.tool == tool]
        for r in rows:
            db.expunge(r)
        return rows


def ran(app, word: str, tool: str = "shell") -> bool:
    """Whether a call naming ``word`` RAN and succeeded (its own output)."""
    return any(r.ok and word in (r.output or "") for r in shell_rows(app, tool))


async def start(app, sid: str, turn_id: str, *, text: str = "", command: str = ""):
    drop = asyncio.Event()
    body: dict = {"text": text, "turn_id": turn_id}
    if command:
        body["command"] = command
    task = asyncio.create_task(open_turn(app, sid, body, drop=drop, timeout=60))
    return drop, task


async def finish(app, turn_id: str, drop, task):
    lg = await until(lambda: turn_log(app, turn_id))
    assert lg is not None, "the turn never started"
    await until(lambda: lg.status != "running", 60)
    drop.set()
    await task
    return lg


async def nth_card(app, turn_id: str, n: int) -> dict:
    got = await until(lambda: [d for e, d in turn_frames(app, turn_id) if e == "approval"][n:n + 1], 30)
    assert got, f"card {n + 1} never came"
    return got[0]


async def answer(app, card: dict, decision: str) -> None:
    status, out = await _asgi_post(app, f"/chat/approvals/{card['id']}", {"decision": decision})
    assert status == 200, out


def cards_of(app, turn_id: str) -> list[dict]:
    return [d for e, d in turn_frames(app, turn_id) if e == "approval"]


def resolved_of(app, turn_id: str) -> list[str]:
    return [d["decision"] for e, d in turn_frames(app, turn_id) if e == "approval_resolved"]


PROJECT_WORDS = ("This step comes from the project's /deploy command, which asks to run shell "
                 "commands. Those always ask you first.")


def assert_command_card(card: dict, tool: str = "shell", words: str = PROJECT_WORDS) -> None:
    assert card["tool"] == tool
    assert card["once_only"] is True and card.get("once_reason") == "command", card
    assert card["can_always"] is False
    assert card["cost_note"] == words


# --------------------------------------------------------------------------- #
# the expansion: lines found, the model told the truth
# --------------------------------------------------------------------------- #


def test_a_projects_and_a_plugins_shell_lines_are_found_and_the_model_is_told_they_ask(cc, work):
    text = ("Ship $ARGUMENTS.\n- Status: !`git status --short`\n"
            "```!\nnpm run build\nfi\n```\nUser text: !`not mine`\n")
    project_command(work, "ship", text)
    plugin_command(cc, "tools", "ship", text)
    found = {c.name: c for c in CC.discover([str(work)]).commands}
    proj = CC.expand(found["ship"], "app.py", folder=str(work), read_room=4)
    assert proj.asks_first is True
    assert proj.lines == ["git status --short", "npm run build", "fi", "not mine"]
    assert proj.text.endswith(CC.SHELL_LINES_NOTE + " " + CC.asks_first_note("project", ""))
    assert "this project's command files, not from the user" in proj.text
    assert "whatever this session's approval setting" in proj.text
    plug = CC.expand(found["tools:ship"], "", folder=str(work), read_room=4)
    assert plug.asks_first is True and "comes from the tools plugin, not from the user" in plug.text
    # The user's own words after the name are never a shell line.
    typed = CC.expand(found["ship"], "!`del everything`", folder=str(work), read_room=4)
    assert "del everything" not in " ".join(typed.lines)


def test_a_personal_command_and_one_with_no_shell_lines_keep_the_old_text(cc, work):
    write(cc / "commands" / "mine.md", "Mine.\n!`echo MINE`\n")
    project_command(work, "plain", "Just words, no shell.\n")
    found = {c.name: c for c in CC.discover([str(work)]).commands}
    mine = CC.expand(found["mine"], "", folder=str(work), read_room=4)
    assert mine.asks_first is False and mine.text.endswith(CC.SHELL_LINES_NOTE)
    assert "not from the user" not in mine.text
    plain = CC.expand(found["plain"], "", folder=str(work), read_room=4)
    assert plain.asks_first is False and plain.lines == [] and CC.SHELL_LINES_NOTE not in plain.text
    assert K.from_expansion(mine, "t") is None and K.from_expansion(plain, "t") is None


# --------------------------------------------------------------------------- #
# the check itself
# --------------------------------------------------------------------------- #


def _entry(lines=("echo IJ-STATUS", "npm run build", "fi"), source="project", plugin="") -> K.CommandLines:
    return K.CommandLines(name="deploy", source=source, plugin=plugin, turn_id="t1", lines=tuple(lines))


@pytest.mark.parametrize("command,asks", [
    ("echo IJ-STATUS", True),
    ('cmd /c "echo IJ-STATUS"', True),
    ("bash -lc 'ECHO   ij-status'", True),
    ('echo \\"IJ-STATUS\\"', True),
    ("cd web && npm run build -- --prod", True),
    ("npm run builder", False),
    ("echo IJ-STATUSES", False),
    ("if [ -f x ]; then y; fi", False),
    ("echo something else", False),
    ("", False),
])
def test_a_later_turn_asks_only_for_a_command_that_runs_a_recorded_line(command, asks):
    token = K.set_turn_rule(K.TurnRule(earlier=(_entry(),)))
    try:
        check = K.command_check(command)
    finally:
        K.reset_turn_rule(token)
    if asks:
        assert check.ask is True and check.once_only is True and check.reason == "command"
        assert check.words == ("This runs a shell line from the project's /deploy command. Shell lines from a "
                               "project's or a plugin's command always ask you first.")
    else:
        assert check is None


def test_the_commands_own_turn_asks_for_every_command_and_no_rule_asks_for_none():
    assert K.command_check("echo anything") is None, "no rule: exactly as before"
    token = K.set_turn_rule(K.TurnRule(started_by=_entry(source="plugin", plugin="tools")))
    try:
        check = K.command_check("python -c 1")
        assert check.ask and check.once_only and check.reason == "command"
        assert check.words == ("This step comes from the tools plugin's /deploy command, which asks to run "
                               "shell commands. Those always ask you first.")
        assert K.command_check("   ") is None, "a call with no command runs nothing"
    finally:
        K.reset_turn_rule(token)


def test_the_git_guards_refusal_still_wins_in_a_commands_turn():
    from iron_jarvis.harness import branches as B

    branch = B.set_turn_branch({"repo": "C:\\r", "worktree": "C:\\w", "branch": "ij/x", "base": "main",
                                "checkout": "C:\\r"}, "C:\\w")
    rule = K.set_turn_rule(K.TurnRule(started_by=_entry()))
    try:
        refused = K.tool_check("git push --force origin HEAD")
        assert refused.ask is False and refused.reason == "git", "a refused push shows no card"
        assert K.tool_check("git push origin HEAD").reason == "git"
        assert K.tool_check("npm test").reason == "command"
    finally:
        K.reset_turn_rule(rule)
        B.reset_turn_branch(branch)


def test_the_record_is_whitelisted_and_bounded():
    from iron_jarvis.daemon.routes.chat import _clean_setup

    junk = [
        {"name": "ok", "source": "project", "lines": ["echo a", "", 3]},
        {"name": "mine", "source": "personal", "lines": ["echo b"]},
        {"name": "", "source": "project", "lines": ["echo c"]},
        {"name": "nolines", "source": "plugin", "lines": []},
        "nope",
    ]
    stored = json.loads(_clean_setup({K.SETUP_KEY: junk}))
    assert stored[K.SETUP_KEY] == [{"name": "ok", "source": "project", "plugin": "", "turn_id": "",
                                    "lines": ["echo a"]}]
    many = [{"name": f"c{i}", "source": "project", "lines": [f"echo {i}"]} for i in range(40)]
    kept = K.clean_record(many)
    assert len(kept) == K.MAX_RECORDED and kept[-1]["name"] == "c39"
    assert _clean_setup({K.SETUP_KEY: "echo x"}) == ""


# --------------------------------------------------------------------------- #
# the command's own turn, through the real route, lane and registry
# --------------------------------------------------------------------------- #


async def test_on_dont_ask_every_command_of_a_project_commands_turn_asks_once_each(app, work):
    project_command(work)
    sid = new_session(app, work, approval_mode="yolo")["id"]
    wire(app, Script([bash("c1", "echo IJ-ONE"), bash("c2", "echo IJ-TWO"), bash("c3", "echo IJ-THREE"), "Done."]))
    drop, task = await start(app, sid, "cs-yolo", text="prod", command="deploy")
    first = await nth_card(app, "cs-yolo", 0)
    assert_command_card(first)
    # "Allow for this chat" is read as once: this one runs, nothing is granted.
    await answer(app, first, "conversation")
    second = await nth_card(app, "cs-yolo", 1)
    assert_command_card(second)
    await answer(app, second, "always")
    third = await nth_card(app, "cs-yolo", 2)
    assert_command_card(third)
    await answer(app, third, "deny")
    await finish(app, "cs-yolo", drop, task)
    assert resolved_of(app, "cs-yolo") == ["once", "once", "deny"]
    assert ran(app, "IJ-ONE") and ran(app, "IJ-TWO") and not ran(app, "IJ-THREE")
    with session_scope(app.state.platform.engine) as db:
        assert list(db.exec(select(StandingGrantRecord))) == [], "no answer left a standing grant"
    assert "granted_tools" not in setup_of(app, sid)
    # The session records the command's lines (not the model's commands).
    assert setup_of(app, sid)[K.SETUP_KEY] == [{
        "name": "deploy", "source": "project", "plugin": "", "turn_id": "cs-yolo", "lines": ["echo IJ-STATUS"],
    }]


@pytest.mark.parametrize("posture", ["approve_for_me", "always_ask", ""])
async def test_every_other_posture_asks_once_only_for_the_commands_shell(app, work, posture):
    project_command(work)
    sid = new_session(app, work, approval_mode=posture)["id"]
    wire(app, Script([bash("c1", "echo IJ-POSTURE"), "Done."]))
    turn_id = f"cs-{posture or 'default'}"
    drop, task = await start(app, sid, turn_id, command="deploy")
    card = await nth_card(app, turn_id, 0)
    assert_command_card(card)
    await answer(app, card, "conversation")
    await finish(app, turn_id, drop, task)
    assert resolved_of(app, turn_id) == ["once"]
    assert ran(app, "IJ-POSTURE")


async def test_a_conversation_grant_never_skips_it(app, work):
    project_command(work)
    sid = new_session(app, work, approval_mode="approve_for_me", granted_tools=["shell"])["id"]
    # Control: the grant really covers the shell in an ordinary turn.
    wire(app, Script([bash("g0", "echo IJ-GRANTED"), "Done."]))
    status, raw = await open_turn(app, sid, {"text": "check", "turn_id": "cs-grant-0"}, timeout=60)
    assert status == 200 and frames_of(raw)[-1][0] == "done"
    assert cards_of(app, "cs-grant-0") == [] and ran(app, "IJ-GRANTED")
    wire(app, Script([bash("g1", "echo IJ-NOT-GRANTED"), "Done."]))
    drop, task = await start(app, sid, "cs-grant-1", command="deploy")
    card = await nth_card(app, "cs-grant-1", 0)
    assert_command_card(card)
    await answer(app, card, "deny")
    await finish(app, "cs-grant-1", drop, task)
    assert not ran(app, "IJ-NOT-GRANTED")


async def test_a_standing_grant_for_exactly_this_call_never_skips_it(app, work):
    project_command(work)
    sid = new_session(app, work, approval_mode="approve_for_me")["id"]
    call = {"command": "echo IJ-STANDING"}
    app.state.platform.grants.create("chat", "chat", "shell", grant_hash("shell", call), "echo IJ-STANDING")
    # Control: the standing grant covers exactly this call in an ordinary turn.
    wire(app, Script([bash("s0", "echo IJ-STANDING"), "Done."]))
    status, raw = await open_turn(app, sid, {"text": "check", "turn_id": "cs-standing-0"}, timeout=60)
    assert status == 200 and frames_of(raw)[-1][0] == "done"
    assert cards_of(app, "cs-standing-0") == [] and len([r for r in shell_rows(app) if r.ok]) == 1
    wire(app, Script([bash("s1", "echo IJ-STANDING"), "Done."]))
    drop, task = await start(app, sid, "cs-standing-1", command="deploy")
    card = await nth_card(app, "cs-standing-1", 0)
    assert_command_card(card)
    await answer(app, card, "deny")
    await finish(app, "cs-standing-1", drop, task)
    assert len([r for r in shell_rows(app) if r.ok]) == 1, "the denied call did not run"


async def test_a_plugins_command_asks_the_same_way_and_names_the_plugin(app, cc, work):
    plugin_command(cc)
    sid = new_session(app, work, approval_mode="yolo")["id"]
    wire(app, Script([bash("p1", "echo IJ-PLUGIN"), "Done."]))
    drop, task = await start(app, sid, "cs-plugin", command="tools:deploy")
    card = await nth_card(app, "cs-plugin", 0)
    assert_command_card(card, words=("This step comes from the tools plugin's /tools:deploy command, which "
                                     "asks to run shell commands. Those always ask you first."))
    await answer(app, card, "deny")
    await finish(app, "cs-plugin", drop, task)
    assert not ran(app, "IJ-PLUGIN")
    assert setup_of(app, sid)[K.SETUP_KEY][0]["plugin"] == "tools"


async def test_your_own_command_follows_dont_ask_with_no_card(app, cc, work):
    write(cc / "commands" / "deploy.md", DEPLOY)
    sid = new_session(app, work, approval_mode="yolo")["id"]
    wire(app, Script([bash("m1", "echo IJ-MINE"), "Done."]))
    status, raw = await open_turn(app, sid, {"text": "", "command": "deploy", "turn_id": "cs-mine"}, timeout=60)
    assert status == 200 and frames_of(raw)[-1][0] == "done", raw[-400:]
    assert cards_of(app, "cs-mine") == []
    assert ran(app, "IJ-MINE")
    assert K.SETUP_KEY not in setup_of(app, sid)
    assert messages_of(app, sid)[0]["commandText"].endswith(CC.SHELL_LINES_NOTE)


async def test_a_project_command_with_no_shell_lines_changes_nothing(app, work):
    project_command(work, "plain", "Just tidy $ARGUMENTS.\n")
    sid = new_session(app, work, approval_mode="yolo")["id"]
    wire(app, Script([bash("n1", "echo IJ-PLAIN"), "Done."]))
    status, raw = await open_turn(app, sid, {"text": "app.py", "command": "plain", "turn_id": "cs-plain"},
                                  timeout=60)
    assert status == 200 and frames_of(raw)[-1][0] == "done", raw[-400:]
    assert cards_of(app, "cs-plain") == [] and ran(app, "IJ-PLAIN")
    assert K.SETUP_KEY not in setup_of(app, sid)


async def test_a_program_started_in_the_commands_turn_asks_too(app, work):
    project_command(work)
    sid = new_session(app, work, approval_mode="yolo")["id"]
    wire(app, Script([bash("b1", "echo IJ-BACKGROUND", run_in_background=True), "Done."]))
    drop, task = await start(app, sid, "cs-bg", command="deploy")
    card = await nth_card(app, "cs-bg", 0)
    assert_command_card(card, tool="process_start")
    await answer(app, card, "deny")
    await finish(app, "cs-bg", drop, task)
    assert [r.ok for r in shell_rows(app, "process_start")] == [False]


async def test_under_the_gpt_dialect_a_program_and_text_typed_into_one_ask(app, work):
    project_command(work)
    sid = new_session(app, work, provider=GPT[0], model=GPT[1], approval_mode="yolo")["id"]
    wire(app, Script([
        [ToolCall(id="x1", name="exec_command", arguments={"cmd": "echo IJ-EXEC"})],
        [ToolCall(id="x2", name="write_stdin", arguments={"session_id": "p1", "chars": "echo IJ-TYPED\n"})],
        "Done.",
    ], who=GPT))
    drop, task = await start(app, sid, "cs-gpt", command="deploy")
    first = await nth_card(app, "cs-gpt", 0)
    assert_command_card(first, tool="process_start")
    await answer(app, first, "deny")
    second = await nth_card(app, "cs-gpt", 1)
    assert_command_card(second, tool="process_write")
    await answer(app, second, "deny")
    await finish(app, "cs-gpt", drop, task)
    assert all(not r.ok for r in shell_rows(app, "process_start") + shell_rows(app, "process_write"))


# --------------------------------------------------------------------------- #
# a helper of that turn
# --------------------------------------------------------------------------- #


async def test_a_helpers_command_asks_once_only_with_the_same_words(app, work):
    from tests.test_harness_helpers_v1372 import Model, _helper_frames, _listed_ask
    from tests.test_harness_helpers_v1372 import wire as wire_model

    project_command(work)
    # The conversation grants the shell, so without the rule the helper would
    # run it with no ask at all.
    sid = new_session(app, work, approval_mode="yolo", granted_tools=["shell"])["id"]

    def helper(task, results, rec):
        if not results:
            return [ToolCall(id="h1", name="shell", arguments={"command": "echo IJ-HELPER"})]
        return "helper finished"

    wire_model(app, Model(main=[[ToolCall(id="k1", name="Agent",
                                          arguments={"description": "d", "prompt": "Run the status."})],
                                "ok"], helper=helper))
    drop, task = await start(app, sid, "cs-helper", command="deploy")
    waiting = await until(lambda: [d for e, d in turn_frames(app, "cs-helper")
                                   if e == "helper" and d.get("state") == "waiting" and d.get("ask")], 30)
    assert waiting, "the helper's command never asked"
    ask = waiting[0]["ask"]
    assert ask["tool"] == "shell" and ask["args"]["command"] == "echo IJ-HELPER"
    assert ask["once_only"] is True and ask["once_reason"] == "command"
    assert ask["cost_note"] == PROJECT_WORDS
    listed = await _listed_ask(app, ask["id"])
    assert listed and listed[0]["once_only"] is True, "the bell offers no wider answer either"
    await answer(app, {"id": ask["id"]}, "conversation")
    await finish(app, "cs-helper", drop, task)
    frames = turn_frames(app, "cs-helper")
    assert [d for d in _helper_frames(frames) if d.get("ended")]
    assert ran(app, "IJ-HELPER"), "Allow (read as once) ran exactly that call"


async def test_a_helper_of_an_ordinary_turn_still_runs_its_granted_shell_unasked(app, work):
    """The control for the helper test above: no command, no ask."""
    from tests.test_harness_helpers_v1372 import Model
    from tests.test_harness_helpers_v1372 import wire as wire_model

    project_command(work)
    sid = new_session(app, work, approval_mode="yolo", granted_tools=["shell"])["id"]

    def helper(task, results, rec):
        return [ToolCall(id="h1", name="shell", arguments={"command": "echo IJ-HELPER-PLAIN"})] if not results \
            else "helper finished"

    wire_model(app, Model(main=[[ToolCall(id="k1", name="Agent", arguments={"description": "d", "prompt": "Go."})],
                                "ok"], helper=helper))
    status, raw = await open_turn(app, sid, {"text": "go", "turn_id": "cs-helper-plain"}, timeout=60)
    assert status == 200 and frames_of(raw)[-1][0] == "done", raw[-400:]
    assert not [d for e, d in turn_frames(app, "cs-helper-plain") if e == "helper" and d.get("ask")]
    assert ran(app, "IJ-HELPER-PLAIN")


def test_a_helpers_task_is_created_with_the_turns_rule_whatever_the_context_held():
    from iron_jarvis.harness.helpers import HelperParent, pinned

    rule = K.TurnRule(started_by=_entry())
    parent = HelperParent(session_id="build:x", folder="", provider="anthropic", model="m", command_rule=rule)
    assert K.current() is None
    with pinned(parent):
        assert K.current() is rule
    assert K.current() is None
    token = K.set_turn_rule(K.TurnRule(earlier=(_entry(),)))
    try:
        with pinned(HelperParent(session_id="build:x", folder="", provider="a", model="m")):
            assert K.current() is None, "the parent decides, not a stray context"
    finally:
        K.reset_turn_rule(token)


async def test_work_an_event_handler_starts_is_not_the_commands_turn():
    """A helper is finalized INSIDE the turn; a ``session.completed`` handler
    that schedules work (the skill sweep) must not carry the rule into it —
    the event bus drops it in the handlers' copy, like the hold and the pin."""
    from iron_jarvis.core.events import EventBus

    bus = EventBus()
    seen: list = []
    bus.add_handler(lambda event: seen.append(K.current()))
    rule = K.TurnRule(started_by=_entry())
    token = K.set_turn_rule(rule)
    try:
        await bus.publish("session.completed", {})
        assert K.current() is rule, "the turn itself keeps it"
    finally:
        K.reset_turn_rule(token)
    assert seen == [None]


def test_the_helper_base_reads_the_turns_rule():
    from iron_jarvis.daemon.chat_turn import helper_base

    rule = K.TurnRule(started_by=_entry())
    token = K.set_turn_rule(rule)
    try:
        base = helper_base("auto", tool_session="build:x", tool_ws="", in_project_folder=False, project_id=None)
    finally:
        K.reset_turn_rule(token)
    assert base.command_rule is rule


# --------------------------------------------------------------------------- #
# later turns, a continuation, Branch from here
# --------------------------------------------------------------------------- #


async def test_a_later_turn_asks_for_the_commands_own_line_and_nothing_else(app, work):
    project_command(work)
    sid = new_session(app, work, approval_mode="yolo")["id"]
    wire(app, Script([bash("l0", "echo IJ-FIRST"), "Done."]))
    drop, task = await start(app, sid, "cs-later-0", command="deploy")
    await answer(app, await nth_card(app, "cs-later-0", 0), "deny")
    await finish(app, "cs-later-0", drop, task)
    # The next turn the USER typed: the command's own line (reworded by the
    # model's quoting) asks; any other command follows "Don't ask".
    wire(app, Script([bash("l1", 'cmd /c "echo IJ-STATUS"'), bash("l2", "echo IJ-OTHER"), "Done."]))
    drop, task = await start(app, sid, "cs-later-1", text="now run its status line")
    card = await nth_card(app, "cs-later-1", 0)
    assert card["tool"] == "shell" and card["once_only"] is True and card["once_reason"] == "command"
    assert card["cost_note"] == ("This runs a shell line from the project's /deploy command. Shell lines from "
                                 "a project's or a plugin's command always ask you first.")
    await answer(app, card, "once")
    await finish(app, "cs-later-1", drop, task)
    assert len(cards_of(app, "cs-later-1")) == 1, "the other command did not ask"
    assert ran(app, "IJ-STATUS") and ran(app, "IJ-OTHER")


async def test_a_continuation_of_the_commands_turn_asks_for_every_command(app, work):
    from iron_jarvis.daemon.routes.build import CONTINUE_TEXT, start_build_turn

    project_command(work)
    sid = new_session(app, work, approval_mode="yolo")["id"]
    wire(app, Script([bash("q0", "echo IJ-CUT"), "Done."]))
    drop, task = await start(app, sid, "cs-cont-0", command="deploy")
    await answer(app, await nth_card(app, "cs-cont-0", 0), "deny")
    await finish(app, "cs-cont-0", drop, task)
    # The daemon's own "carry on" (an account limit cut the turn off): the same
    # work, so every command it runs asks — not only the recorded line.
    wire(app, Script([bash("q1", "echo IJ-CARRY-ON"), "Done."]))
    lg = await start_build_turn(app.state.d, sid, CONTINUE_TEXT, "cs-cont-1", continuation=True)
    assert K.current() is None, "armed for the turn's own task only: the caller's context is clean again"
    card = await nth_card(app, "cs-cont-1", 0)
    assert_command_card(card)
    await answer(app, card, "deny")
    await until(lambda: lg.status != "running", 60)
    assert not ran(app, "IJ-CARRY-ON")
    # A turn the user types next is not that turn: the same command runs.
    wire(app, Script([bash("q2", "echo IJ-CARRY-ON"), "Done."]))
    status, raw = await open_turn(app, sid, {"text": "go on", "turn_id": "cs-cont-2"}, timeout=60)
    assert status == 200 and frames_of(raw)[-1][0] == "done"
    assert cards_of(app, "cs-cont-2") == [] and ran(app, "IJ-CARRY-ON")


async def test_branch_from_here_keeps_the_record(app, work):
    project_command(work)
    sid = new_session(app, work, approval_mode="yolo")["id"]
    wire(app, Script([bash("r0", "echo IJ-B"), "Done."]))
    drop, task = await start(app, sid, "cs-branch-0", command="deploy")
    await answer(app, await nth_card(app, "cs-branch-0", 0), "deny")
    await finish(app, "cs-branch-0", drop, task)
    status, view = await asgi(app, "POST", f"/build/sessions/{sid}/branch", {"turn_id": "cs-branch-0"})
    assert status == 201, view
    assert setup_of(app, view["id"])[K.SETUP_KEY] == setup_of(app, sid)[K.SETUP_KEY]


async def test_a_client_cannot_write_or_clear_the_record(app, work):
    project_command(work)
    sid = new_session(app, work, approval_mode="yolo")["id"]
    wire(app, Script([bash("w0", "echo IJ-W"), "Done."]))
    drop, task = await start(app, sid, "cs-write-0", command="deploy")
    await answer(app, await nth_card(app, "cs-write-0", 0), "deny")
    await finish(app, "cs-write-0", drop, task)
    before = setup_of(app, sid)[K.SETUP_KEY]
    status, _ = await asgi(app, "PATCH", f"/build/sessions/{sid}", {"command_shell": [], "approval_mode": "yolo"})
    assert status == 200
    status, _ = await asgi(app, "PUT", f"/chat/threads/{sid}",
                           {"messages": [], "setup": {"command_shell": []}})
    assert status == 409
    assert setup_of(app, sid)[K.SETUP_KEY] == before


# --------------------------------------------------------------------------- #
# the POST lane refuses; low trust and Local only unchanged
# --------------------------------------------------------------------------- #


async def test_the_post_lane_refuses_in_words(app, work):
    from iron_jarvis.daemon.chat_turn import ONCE_NOT_ASKED_TAIL, run_chat_turn
    from iron_jarvis.daemon.schemas import ChatBody
    from tests.test_build_git_agent_v1375 import _PostRouter

    platform = app.state.platform
    platform.router = router = _PostRouter([ToolCall(id="c1", name="shell", arguments={"command": "echo IJ-POST"})])
    token = K.set_turn_rule(K.TurnRule(started_by=_entry()))
    try:
        body = ChatBody(messages=[{"role": "user", "content": "deploy"}], tools=["shell"],
                        workspace_dir=str(work), thread_id="chat_post", approval_mode="yolo")
        await run_chat_turn(platform, {}, body)
    finally:
        K.reset_turn_rule(token)
    tool_msgs = [m for m in router.seen[1] if getattr(m, "role", "") == "tool"]
    assert PROJECT_WORDS in tool_msgs[0].content and ONCE_NOT_ASKED_TAIL in tool_msgs[0].content
    assert not ran(app, "IJ-POST")


async def test_low_trust_still_keeps_programs_away_and_isolates_the_shell(app, work, monkeypatch):
    from iron_jarvis.daemon.schemas import ChatBody, ChatMessageBody
    from iron_jarvis.tools.base import ToolResult
    from tests.test_harness_mode_lanes_v1366 import _stream_answering

    platform = app.state.platform
    seen: list[dict] = []

    async def execute(args, ctx):
        seen.append(dict(args))
        return ToolResult(ok=True, output="ran")

    monkeypatch.setattr(platform.registry.get("shell"), "execute", execute)
    wire(app, Script([bash("t1", "echo IJ-LOW"), bash("t2", "echo IJ-LOW-BG", run_in_background=True), "Done."]))
    body = ChatBody(messages=[ChatMessageBody(role="user", content="deploy")], provider="anthropic",
                    model="claude-sonnet-4-5", workspace_dir=str(work), approval_mode="yolo")
    token = K.set_turn_rule(K.TurnRule(started_by=_entry()))
    try:
        frames = await _stream_answering(platform, body, "once", harness=True, trust="low")
    finally:
        K.reset_turn_rule(token)
    cards = [d for e, d in frames if e == "approval"]
    assert [c["tool"] for c in cards] == ["shell"], "the program tools stay kept away (refused, never asked)"
    assert cards[0]["once_only"] is True and cards[0]["once_reason"] == "command"
    assert seen and seen[0].get("_isolate") is True, "low trust still isolates the shell"
    assert not [r for r in shell_rows(app, "process_start") if r.ok]


async def test_local_only_keeps_its_own_allow_once_card(app, work):
    from iron_jarvis.daemon.schemas import ChatBody, ChatMessageBody
    from tests.test_harness_mode_lanes_v1366 import _stream_answering

    platform = app.state.platform
    # The demo model speaks the generic dialect: Jarvis's own tool names.
    wire(app, Script([[ToolCall(id="o1", name="shell", arguments={"command": "echo IJ-LOCAL"})], "Done."],
                     who=("mock", "mock")))
    body = ChatBody(messages=[ChatMessageBody(role="user", content="deploy")], provider="mock", model="mock",
                    workspace_dir=str(work), approval_mode="yolo", local_only=True)
    token = K.set_turn_rule(K.TurnRule(started_by=_entry()))
    try:
        frames = await _stream_answering(platform, body, "deny", harness=True)
    finally:
        K.reset_turn_rule(token)
    cards = [d for e, d in frames if e == "approval"]
    assert len(cards) == 1 and cards[0]["once_only"] is True
    assert "once_reason" not in cards[0], "the card keeps the local-only line"
    assert not ran(app, "IJ-LOCAL")


def test_a_harness_turn_cannot_reach_the_other_code_runners():
    """``run_code``, ``repl`` and the terminal tools are not in a harness
    turn's set (so a helper, bounded by the turn's tools, has none either):
    the three command tools above are every way a Build turn runs a command."""
    from iron_jarvis.harness.mode import CODING_TOOLS, PLAN_MODE_TOOLS

    for name in ("run_code", "repl", "pane_send", "pane_spawn"):
        assert name not in CODING_TOOLS and name not in PLAN_MODE_TOOLS
