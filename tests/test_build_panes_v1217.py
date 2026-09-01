"""Build knows what the agent in each pane is doing (v1.217.0).

Adapted from herdr, a terminal multiplexer for coding agents, whose framing is
"never hunt for the stuck one". Build could already START a coding CLI in a
pane (the Launch catalog) and then went blind: the session offered `alive`,
`exit_code` and bytes, so the pane where the real work happened was the pane
the app could say least about.

TWO OF HERDR'S RULES ARE COPIED VERBATIM because they are honesty rules:

    "blocked means Herdr recognized an approval or question UI."
    "unknown means an agent is present but Herdr cannot classify it
     confidently; it does not prove completion."

The second is already this repo's own law elsewhere — the roster's liveness
note says a missing signal "is NOT 'free' — it is 'no claim'" — so most of what
these tests defend is the refusal to guess.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from iron_jarvis.terminals.agent_state import AgentState, classify, known_clis
from iron_jarvis.tools.base import ToolContext
from iron_jarvis.tools.pane_tools import (
    PaneListTool,
    PaneReadTool,
    PaneSendTool,
    PaneSpawnTool,
    PaneWaitTool,
)


# --------------------------------------------------------------- classifier


def test_an_approval_prompt_is_blocked_for_every_phrasing_we_have_seen():
    for tail in [
        "Edit file src/app.py?\n❯ 1. Yes\n  2. No, tell Claude what to do",
        "Do you want to proceed? (y/n)",
        "Allow this command to run? [y/N]",
        "Waiting for your approval…",
        "Run `rm -rf build`?\nApprove this command? yes/no:",
    ]:
        act = classify(tail, cli="claude")
        assert act.state is AgentState.BLOCKED, tail
        assert act.line, "a blocked pane must carry the line that proves it"


def test_an_approval_with_no_selection_caret_is_still_blocked():
    """The caret is not load-bearing, and assuming it was cost a live miss.

    Driving a real PTY showed this exact shape classified as `unknown`: every
    phrase pattern keys off wording these three CLIs happen to use, and the
    only shape rule wanted the selection caret -- which is absent on the
    unselected rows and is sometimes lost with the ANSI anyway. `unknown` on a
    pane that is holding up the user is the one wrong answer this feature
    cannot afford.
    """
    for tail in [
        "Edit src/app.py?\n  1. Yes\n  2. No",
        "Overwrite README.md?\n1) Yes\n2) No\n3) Always",
        "Thinking...\nRun `pytest -q`?\n  1. Yes\n  2. No, tell me what to do",
    ]:
        act = classify(tail, cli="claude")
        assert act.state is AgentState.BLOCKED, tail
        assert act.line.rstrip().endswith("?"), "the QUESTION is the evidence"


def test_a_question_alone_is_not_an_approval_prompt():
    """Both halves are required, and this test is why.

    An agent's prose ends in a question mark constantly ("Want me to run the
    tests?"), and an ordinary numbered list appears without one. Arming on
    either half alone turns a chatty reply into a false "needs you" badge --
    and a summary strip that cries wolf is one the user stops reading, which
    costs the real blocks too.
    """
    for tail in [
        "Want me to run the tests next?\n> ",
        "Here are the steps:\n  1. install\n  2. build\n  3. test\n> ",
        # A question with only ONE option under it is a list, not a choice.
        "Should I continue with the refactor?\n  1. see docs/PLAN.md\n> ",
    ]:
        assert classify(tail, cli="claude", seen=True).state is not AgentState.BLOCKED


def test_a_running_turn_is_working():
    for tail in [
        "Thinking…",
        "· Analyzing the diff…",
        "Running tests…\n  esc to interrupt",
        "⠹ working",
    ]:
        assert classify(tail, cli="claude").state is AgentState.WORKING, tail


def test_a_prompt_is_idle_when_seen_and_done_when_not():
    tail = "All set — the tests pass.\n> "
    assert classify(tail, cli="claude", seen=True).state is AgentState.IDLE
    # herdr's distinction: the same underlying ready state, reached while the
    # user was looking elsewhere, is a different thing to be TOLD.
    assert classify(tail, cli="claude", seen=False).state is AgentState.DONE


def test_a_bare_shell_is_unknown_not_idle():
    """A `>` in an ordinary shell is a shell, not a waiting agent.

    This is the whole reason `_PROMPT` is gated on knowing a CLI is present:
    without the gate, every idle terminal on the canvas would claim to be an
    agent standing by.
    """
    act = classify("C:\\work> ", cli=None)
    assert act.state is AgentState.UNKNOWN
    assert act.cli is None


def test_nothing_printed_is_unknown_and_never_idle():
    assert classify("", cli="claude").state is AgentState.UNKNOWN
    assert classify("   \n\n  ", cli="claude").state is AgentState.UNKNOWN


def test_a_dead_pane_is_unknown_not_working():
    assert (
        classify("Thinking…", cli="claude", alive=False).state is AgentState.UNKNOWN
    )


def test_blocked_beats_working_when_both_are_on_screen():
    """A CLI draws its spinner and then its question. Reading only the last
    line would report `working` for a pane that is actually waiting on a human
    — the exact failure the feature exists to prevent."""
    tail = "Thinking…\nEdit src/app.py?\n❯ 1. Yes\n  2. No"
    assert classify(tail, cli="claude").state is AgentState.BLOCKED


def test_an_answered_approval_stops_winning_once_the_agent_resumes():
    """The other half of "blocked beats working": ORDER decides which is live.

    An answered prompt does not leave the scrollback, and the first cut let it
    pin a pane to "needs you" for as long as it sat in the window -- found by
    driving a real PTY, where a resolved question outranked a live spinner two
    commands later. A pane that says "needs you" when it does not is how a
    summary strip stops being read.
    """
    resumed = (
        "Edit src/app.py?\n  1. Yes\n  2. No\n"
        "> yes\nAnalyzing the diff...\n"
    )
    assert classify(resumed, cli="claude").state is AgentState.WORKING

    # ...and the reverse ordering still reads as blocked: a spinner ABOVE the
    # question is the turn that LED UP to it, not work that came after.
    asked = "Analyzing the diff...\nEdit src/app.py?\n  1. Yes\n  2. No"
    assert classify(asked, cli="claude").state is AgentState.BLOCKED


def test_a_stale_spinner_far_up_the_scrollback_does_not_win():
    tail = "Thinking…\n" + "\n".join(f"line {i}" for i in range(20)) + "\n> "
    assert classify(tail, cli="claude", seen=True).state is AgentState.IDLE


def test_the_launch_hint_beats_sniffing():
    """The catalog knows what it started; reading it back out of scrollback is
    guesswork by comparison."""
    assert classify("> ", cli="codex").cli == "codex"
    assert classify("welcome to Claude Code\n> ").cli == "claude"


def test_the_hint_is_honoured_for_every_cli_the_catalog_can_LAUNCH():
    """Not just the three we can sniff (v1.219.0).

    Sniffing needs a pattern written against a CLI's real output and there are
    three of those; the Launch catalog, by contrast, knows exactly what it
    typed into the pane. The first cut gated the hint on the sniffable set, so
    a user launching Grok or Gemini from a menu THIS APP OWNS had the answer
    thrown away and the pane reported as a plain shell. `known_clis()` reads
    the catalog now, so adding a CLI there makes it classifiable in one edit.
    """
    from iron_jarvis.terminals.ai_clis import AI_CLIS

    ids = {str(c["id"]) for c in AI_CLIS}
    assert {"grok", "gemini", "opencode", "aider"} <= ids, "catalog shrank"
    assert set(known_clis()) == ids

    for cli in ("grok", "gemini", "cursor-agent"):
        act = classify("> ", cli=cli, seen=True)
        assert act.cli == cli, cli
        # …and knowing a CLI is there is what lets a prompt read as `idle`
        # rather than as an unreadable shell.
        assert act.state is AgentState.IDLE, cli


def test_an_unknown_hint_is_still_refused():
    """The hint is trusted because it comes from the catalog, not because it
    is a string. Anything else falls back to sniffing, and a bare prompt with
    no CLI in sight stays `unknown` — the rule that keeps every idle shell on
    the canvas from claiming to be an agent standing by."""
    act = classify("> ", cli="definitely-not-a-cli", seen=True)
    assert act.cli is None
    assert act.state is AgentState.UNKNOWN


def test_unknown_is_never_reported_as_finished():
    """The invariant, stated as a test so it cannot be softened by accident."""
    for tail in ["some program output", "make: *** [all] Error 2", "$ "]:
        act = classify(tail, cli=None)
        assert act.state is not AgentState.DONE
        assert act.state is not AgentState.IDLE


# ------------------------------------------------------------- fake manager


class _FakeSession:
    def __init__(self, sid, *, tail="", cli=None, name=None, alive=True, cwd="C:\\w"):
        self.id = sid
        self._tail = tail
        self.agent_cli = cli
        self.pane_name = name
        self.alive = alive
        self.cwd = cwd
        self.written: list[str] = []
        self.pane_env_extra = {"IRONJARVIS_BUILD": "1", "IRONJARVIS_PANE_ID": sid}

    def output_tail(self):
        return self._tail

    def write(self, data):
        self.written.append(data)

    def activity(self, *, seen: bool = True):
        return classify(self._tail, cli=self.agent_cli, seen=seen, alive=self.alive)

    def info(self):
        act = self.activity()
        return {
            "id": self.id,
            "cwd": self.cwd,
            "alive": self.alive,
            "name": self.pane_name,
            "agent_cli": act.cli,
            "state": act.state.value,
            "state_line": act.line,
        }


class _FakeManager:
    def __init__(self, sessions=()):
        self._s = {s.id: s for s in sessions}
        self.created: list[dict[str, Any]] = []

    def get(self, sid):
        return self._s.get(sid)

    def list(self):
        return [s.info() for s in self._s.values()]

    def create(self, cwd=None, shell=None, cols=80, rows=24, *, name=None, **kw):
        s = _FakeSession(f"t{len(self._s) + 1}", name=name, cwd=cwd or "C:\\home")
        self._s[s.id] = s
        self.created.append({"cwd": cwd, "name": name})
        return s


def _ctx(tmp_path):
    return ToolContext(
        workspace=tmp_path,
        session_id="s",
        agent_run_id="r",
        config=None,
        event_bus=None,
        engine=None,
    )


# ------------------------------------------------------------------- tools


async def test_pane_list_reports_state_and_folder(tmp_path):
    mgr = _FakeManager(
        [
            _FakeSession("t1", tail="Thinking…", cli="claude", name="builder"),
            _FakeSession("t2", tail="C:\\> ", name=None),
        ]
    )
    res = await PaneListTool(mgr).execute({}, _ctx(tmp_path))
    assert res.ok
    assert "builder" in res.output and "working" in res.output
    # The plain shell is `unknown`, never `idle`.
    states = {p["id"]: p["state"] for p in res.data["panes"]}
    assert states == {"t1": "working", "t2": "unknown"}


async def test_pane_read_is_fenced_as_untrusted(tmp_path):
    """A terminal shows whatever ran in it, including output this app did not
    produce — so it is data, not instructions, exactly like a file read."""
    assert PaneReadTool(None).returns_untrusted_content is True
    mgr = _FakeManager([_FakeSession("t1", tail="a\nb\nc\n> ", cli="claude")])
    res = await PaneReadTool(mgr).execute({"pane": "t1"}, _ctx(tmp_path))
    assert res.ok and res.output.splitlines()[-1].strip() == ">"


async def test_a_pane_is_addressable_by_name(tmp_path):
    mgr = _FakeManager([_FakeSession("t1", tail="> ", cli="claude", name="reviewer")])
    res = await PaneReadTool(mgr).execute({"pane": "reviewer"}, _ctx(tmp_path))
    assert res.ok
    missing = await PaneReadTool(mgr).execute({"pane": "nobody"}, _ctx(tmp_path))
    assert not missing.ok and "nobody" in missing.error


async def test_pane_send_refuses_a_pane_that_is_waiting_on_a_human(tmp_path):
    """herdr: it "rejects an agent already waiting at an approval or question
    dialog … before sending any input". Answering someone else's approval is
    the one thing an agent must never do by accident — and "y" is one
    keystroke from the command that was being held for review."""
    blocked = _FakeSession(
        "t1", tail="Run `rm -rf build`?\n❯ 1. Yes\n  2. No", cli="claude"
    )
    res = await PaneSendTool(_FakeManager([blocked])).execute(
        {"pane": "t1", "text": "yes"}, _ctx(tmp_path)
    )
    assert not res.ok
    assert "approval" in res.error or "question" in res.error
    assert blocked.written == [], "nothing may reach the PTY"


async def test_pane_send_types_the_line_and_presses_enter(tmp_path):
    s = _FakeSession("t1", tail="> ", cli="claude")
    res = await PaneSendTool(_FakeManager([s])).execute(
        {"pane": "t1", "text": "run the tests"}, _ctx(tmp_path)
    )
    assert res.ok
    assert s.written == ["run the tests\r"]


async def test_pane_send_refuses_a_dead_pane(tmp_path):
    s = _FakeSession("t1", tail="> ", cli="claude", alive=False)
    res = await PaneSendTool(_FakeManager([s])).execute(
        {"pane": "t1", "text": "hi"}, _ctx(tmp_path)
    )
    assert not res.ok and "exited" in res.error
    assert s.written == []


async def test_pane_send_wait_says_so_when_nothing_changed(tmp_path, monkeypatch):
    """herdr's `agent_prompt_stalled`. A pane that never reacts is a different
    fact from a pane still working, and a tool that waited out the full
    timeout would report the two identically."""
    import iron_jarvis.tools.pane_tools as pt

    monkeypatch.setattr(pt, "_STALL_S", 0.05)
    monkeypatch.setattr(pt, "_POLL_S", 0.01)
    s = _FakeSession("t1", tail="unrecognised program output", cli=None)
    res = await pt.PaneSendTool(_FakeManager([s])).execute(
        {"pane": "t1", "text": "hello", "wait": True}, _ctx(tmp_path)
    )
    assert res.ok  # the text WAS sent; the report is about what followed
    assert res.data["stalled"] is True
    assert "may not have received it" in res.output


async def test_pane_wait_reports_a_timeout_instead_of_claiming_success(tmp_path, monkeypatch):
    import iron_jarvis.tools.pane_tools as pt

    monkeypatch.setattr(pt, "_POLL_S", 0.01)
    s = _FakeSession("t1", tail="Thinking…", cli="claude")
    res = await pt.PaneWaitTool(_FakeManager([s])).execute(
        {"pane": "t1", "until": "idle", "timeout_seconds": 1}, _ctx(tmp_path)
    )
    assert res.ok
    assert res.data["timed_out"] is True
    assert res.data["state"] == "working"
    assert "did not reach idle" in res.output


async def test_pane_wait_returns_as_soon_as_it_settles(tmp_path, monkeypatch):
    import iron_jarvis.tools.pane_tools as pt

    monkeypatch.setattr(pt, "_POLL_S", 0.01)
    s = _FakeSession("t1", tail="Thinking…", cli="claude")

    async def flip():
        await asyncio.sleep(0.05)
        s._tail = "done.\n> "

    task = asyncio.create_task(flip())
    res = await pt.PaneWaitTool(_FakeManager([s])).execute(
        {"pane": "t1", "timeout_seconds": 5}, _ctx(tmp_path)
    )
    await task
    assert res.ok and res.data.get("timed_out") is None
    assert res.data["state"] in {"idle", "done"}


async def test_pane_spawn_names_a_pane_and_refuses_a_duplicate(tmp_path):
    mgr = _FakeManager([_FakeSession("t1", name="reviewer")])
    dup = await PaneSpawnTool(mgr).execute({"name": "reviewer"}, _ctx(tmp_path))
    assert not dup.ok and "already exists" in dup.error
    ok = await PaneSpawnTool(mgr).execute(
        {"name": "tester", "cwd": "C:\\proj"}, _ctx(tmp_path)
    )
    assert ok.ok and ok.data["pane"]["name"] == "tester"
    assert mgr.created[-1] == {"cwd": "C:\\proj", "name": "tester"}


async def test_a_build_without_terminals_says_so_rather_than_crashing(tmp_path):
    for tool in (PaneListTool(None), PaneReadTool(None), PaneSpawnTool(None)):
        res = await tool.execute({"pane": "x"}, _ctx(tmp_path))
        assert not res.ok and "Build module" in res.error


# ------------------------------------------------------------- permissions


def test_acting_on_a_pane_is_on_the_deny_floor():
    """`pane_send` types into a LIVE PTY — a shell that is already running,
    already in a folder, and possibly already authenticated. That is strictly
    more reach than `shell`, so an agent definition must never be able to
    raise it to allow."""
    from iron_jarvis.tools.permissions import DENY_FLOOR_TOOLS

    assert {"pane_send", "pane_spawn"} <= DENY_FLOOR_TOOLS
    # …and observing is NOT gated behind the floor.
    assert not ({"pane_list", "pane_read", "pane_wait"} & DENY_FLOOR_TOOLS)


def test_the_default_policy_reads_but_asks_before_acting():
    from iron_jarvis.core.config import default_permissions

    perms = default_permissions()
    assert perms["pane_list"] == "allow"
    assert perms["pane_read"] == "allow"
    assert perms["pane_wait"] == "allow"
    assert perms["pane_send"] == "ask"
    assert perms["pane_spawn"] == "ask"


def test_the_tools_are_registered_with_the_live_terminal_manager(tmp_path):
    """A tool handed None reports "the Build module is not available" — which
    would be a lie on a machine that has one. The registration therefore has
    to happen AFTER the manager is constructed, and this pins it."""
    from iron_jarvis.platform import build_platform

    p = build_platform(str(tmp_path))
    for name in ("pane_list", "pane_read", "pane_send", "pane_spawn", "pane_wait"):
        tool = p.registry.get(name)
        assert tool is not None, f"{name} is not registered"
        assert tool.mgr is p.terminals, f"{name} was handed the wrong manager"
