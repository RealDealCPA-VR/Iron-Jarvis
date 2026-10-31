"""v1.417.0 (C1.4) — A READ-ONLY COMMAND LIST UNDER ASK FIRST.

``shell`` is ask-tier, so every ``git status``, ``ls``, ``cat``, ``rg`` carded
under the default posture ("Ask first") and under Accept edits. Now a command
on :data:`core.auto_approve.READ_ONLY_COMMANDS` runs with no card under Ask
first, Accept edits and Auto (before the judge) — never under "Ask before
every action" — read with ``is_test_command``'s shell rules: both shells, no
redirect, no chaining, a ``|`` only into another read-only program, no
substitution, no escape, no environment assignment, no path outside the
folder, and no program of the same name planted in the folder.

Pure table pins on the rule, then the REAL stream lane (registry, permission
engine, approvals, ledger) for each posture, and the POST lane under Accept
edits.
"""

from __future__ import annotations

import pytest

from iron_jarvis.core import auto_approve as A
from tests.test_auto_approve_lanes_v14060 import (  # noqa: F401 — `env` is a fixture
    _auto_events,
    _bash,
    _body,
    _executes,
    _ledger,
    _stream,
    env,
)

READ_ONLY = [
    "git status",
    "git status --short",
    "git diff",
    "git diff --stat HEAD",
    "git log --oneline -5",
    "git show HEAD",
    "git branch",
    "git branch -a",
    "git branch --show-current",
    "git rev-parse HEAD",
    "ls",
    "ls -la src",
    "dir",
    "dir /b",
    "cat app.py",
    "type app.py",
    'rg "foo|bar" src',
    "rg -n TODO",
    "grep -rn needle .",
    "find . -name app.py",
    "head -20 app.py",
    "tail -5 app.py",
    "wc -l app.py",
    "pwd",
    "which python",
    "where python",
    "echo hello",
    "git log --oneline | head -5",
    "rg foo | wc -l",
    "node --version",
    "python --version",
    "uv --version",
    r"cat tests\test_a.py",
]

NOT_READ_ONLY = [
    "git status > out.txt",
    "git status >out.txt",
    "cat app.py; rm app.py",
    "cat app.py && rm app.py",
    "cat app.py || rm app.py",
    "cat app.py & rm app.py",
    "rg foo | sh",
    "rg foo | python",
    "rm app.py",
    "del app.py",
    "git push",
    "git commit -m x",
    "git checkout -- .",
    "git branch new-branch",
    "git branch -d old",
    "git branch -D old",
    "git -c alias.status=push status",
    "git diff --output=x.patch",
    "git diff --ext-diff",
    "find . -delete",
    "find . -exec rm {} ;",
    "find . -execdir touch x ;",
    "rg --pre cat foo",
    "rg --pre=cat foo",
    "rg -z foo",
    "rg -iz foo",
    "cat ../secret.txt",
    r"cat C:\Users\me\.ssh\id_rsa",
    "cat /etc/passwd",
    "cat ~/.netrc",
    "FOO=1 ls",
    "set FOO=1",
    "echo $(rm x)",
    "echo `rm x`",
    "echo %PATH%",
    "echo 'hi'",
    "cat < app.py",
    "(ls)",
    "ls\nrm x",
    "python app.py",
    "node script.js",
    "npm install",
    "npm test",
    "curl https://example.com",
    "",
    "   ",
    'rg "unbalanced',
    "ls |",
    "| ls",
    r"cat \"x",
]


@pytest.mark.parametrize("command", READ_ONLY)
def test_a_read_only_command_reads(command):
    assert A.read_only_command(command) is True, command


@pytest.mark.parametrize("command", NOT_READ_ONLY)
def test_anything_that_could_write_chain_or_run_another_program_is_not(command):
    assert A.read_only_command(command) is False, command


def test_a_program_planted_in_the_folder_is_never_read_only(tmp_path):
    """cmd.exe runs a ``git.bat`` from the current folder before the PATH."""
    assert A.read_only_command("git status", folders=[tmp_path]) is True
    (tmp_path / "git.bat").write_text("@echo hijacked\r\n", encoding="utf-8")
    assert A.read_only_command("git status", folders=[tmp_path]) is False
    assert A.read_only_command("ls", folders=[tmp_path]) is True


def test_a_cwd_outside_the_folder_is_not_read_only(tmp_path):
    ws = tmp_path / "ws"
    (ws / "sub").mkdir(parents=True)
    assert A.read_only_call("shell", {"command": "ls", "cwd": "sub"}, ws) is True
    assert A.read_only_call("shell", {"command": "ls", "cwd": str(tmp_path)}, ws) is False
    assert A.read_only_call("shell", {"command": "ls", "cwd": ".."}, ws) is False
    assert A.read_only_call("write_file", {"command": "ls"}, ws) is False


@pytest.mark.parametrize("mode", ["approve_for_me", "accept_edits", "auto"])
def test_the_rule_passes_under_ask_first_accept_edits_and_auto(mode, tmp_path):
    v = A.classify("shell", {"command": "git status"}, mode=mode, workspace=tmp_path)
    assert (v.kind, v.rule) == (A.PASS, "read_only_command")
    assert A.rule_words("read_only_command") == "it runs a command that only reads"


@pytest.mark.parametrize("mode", ["always_ask", "yolo", ""])
def test_never_decided_for_any_other_posture(mode, tmp_path):
    assert A.classify("shell", {"command": "git status"}, mode=mode, workspace=tmp_path).kind == A.ASK


def test_under_ask_first_a_command_that_is_not_read_only_keeps_the_default_card(tmp_path):
    for cmd in ("pytest -q", "rm x", "git push"):
        v = A.classify("shell", {"command": cmd}, mode="approve_for_me", workspace=tmp_path)
        assert (v.kind, v.rule) == (A.ASK, ""), cmd


def test_forced_low_trust_and_folder_commands_still_ask(tmp_path):
    args = {"command": "git status"}
    assert A.classify("shell", args, mode="approve_for_me", workspace=tmp_path, forced="local_only").kind == A.ASK
    assert A.classify("shell", args, mode="approve_for_me", workspace=tmp_path, low_trust=True).kind == A.ASK
    assert A.classify("shell", args, mode="approve_for_me", workspace=tmp_path, needs_a_person=True).kind == A.ASK


# --------------------------------------------------------------------------- #
# the REAL lanes
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("mode", ["approve_for_me", "accept_edits"])
async def test_git_status_under_ask_first_and_accept_edits_runs_with_no_card(env, monkeypatch, mode):
    ran = _executes(env.platform, monkeypatch, "shell")
    _, judge = env.wire([_bash("git status")])
    out = await _stream(env.platform, _body(env.ws, mode=mode), harness=True)
    assert out["cards"] == []
    assert [a["command"] for a in ran["shell"]] == ["git status"]
    assert judge.seen == []
    assert [(r.tool, r.ok) for r in _ledger(env.platform)] == [("shell", True)]
    (ev,) = _auto_events(env.bus)
    assert ev["reason"] == "read_only_command"
    route = out["done"]["route"]
    assert route["auto_approved"]["calls"][0]["rule"] == "read_only_command"


async def test_auto_passes_a_read_only_command_before_the_judge(env, monkeypatch):
    ran = _executes(env.platform, monkeypatch, "shell")
    _, judge = env.wire([_bash("rg -n TODO")])
    out = await _stream(env.platform, _body(env.ws, mode="auto"), harness=True)
    assert out["cards"] == [] and judge.seen == []
    assert [a["command"] for a in ran["shell"]] == ["rg -n TODO"]


@pytest.mark.parametrize("command", ["git status > out.txt", "cat a.py; rm a.py", "rg foo | sh", "rm a.py"])
async def test_under_ask_first_anything_else_still_cards(env, monkeypatch, command):
    ran = _executes(env.platform, monkeypatch, "shell")
    env.wire([_bash(command)])
    out = await _stream(env.platform, _body(env.ws, mode="approve_for_me"), harness=True, answer="deny")
    assert [c["tool"] for c in out["cards"]] == ["shell"]
    assert ran["shell"] == []


async def test_ask_before_every_action_still_cards_git_status(env, monkeypatch):
    ran = _executes(env.platform, monkeypatch, "shell")
    env.wire([_bash("git status")])
    out = await _stream(env.platform, _body(env.ws, mode="always_ask"), harness=True, answer="deny")
    assert [c["tool"] for c in out["cards"]] == ["shell"]
    assert ran["shell"] == []
