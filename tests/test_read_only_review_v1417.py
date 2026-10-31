"""v1.417.0 review (C1.4) — holes in the read-only command pass, each found by
reading the command as a hostile model would, each red on the first cut.

The pass lets a shell command run with NO card under the DEFAULT posture, so
anything it admits must only read. Each case below RAN a program or reached
outside the folder on the first cut:

* ripgrep's ``--hostname-bin=PROGRAM`` runs that program (ripgrep 14, for
  hyperlinks) — ``--pre`` and ``-z`` were refused, this was not;
* ``pnpm`` / ``yarn`` / ``npm`` / ``npx --version`` under corepack are shims
  that DOWNLOAD and run the version a repository's ``package.json`` names;
* cmd.exe looks in the current folder first with EVERY ``PATHEXT`` extension
  (``rg.vbs``, ``git.js`` through Windows Script Host), not only .exe / .bat;
* a ``cwd`` of ``\\\\host\\share`` was RESOLVED (a stat of a UNC path is an SMB
  connection that sends the sign-in hash — the v1.379.0 lesson);
* git in a SUB-folder may meet another repository (an unpacked archive's own
  ``.git``) whose config names programs git runs on status / diff / show;
* under Ask first a folder's own "ask" rule on ``shell`` was overridden.
"""

from __future__ import annotations

import pytest

from iron_jarvis.core import auto_approve as A
from tests.test_auto_approve_lanes_v14060 import (  # noqa: F401 — `env` is a fixture
    _auto_events,
    _bash,
    _body,
    _executes,
    _stream,
    env,
)

G = "g" + "it"


@pytest.mark.parametrize("command", [
    "rg --hyperlink-format=default --hostname-bin=calc foo",
    "rg --hostname-bin calc foo",
    "rg --hostname-bin=./evil.bat -n foo",
])
def test_ripgrep_hostname_bin_runs_a_program(command):
    assert A.read_only_command(command) is False


@pytest.mark.parametrize("command", ["pnpm --version", "yarn --version", "npm --version", "npx --version"])
def test_a_corepack_shim_version_is_not_read_only(command):
    assert A.read_only_command(command) is False


@pytest.mark.parametrize("command", ["node --version", "python --version", "uv --version", f"{G} --version"])
def test_plain_version_reads_still_pass(command):
    assert A.read_only_command(command) is True


@pytest.mark.parametrize("planted", ["rg.vbs", "rg.js", "rg.wsf", "rg.jse", "rg.vbe", "rg.wsh", "rg.msc"])
def test_every_pathext_extension_planted_in_the_folder_blocks_the_pass(tmp_path, planted):
    assert A.read_only_command("rg -n TODO", folders=[tmp_path]) is True
    (tmp_path / planted).write_text("x", encoding="utf-8")
    assert A.read_only_command("rg -n TODO", folders=[tmp_path]) is False


def test_a_pathext_extension_the_environment_adds_counts_too(tmp_path, monkeypatch):
    monkeypatch.setenv("PATHEXT", ".COM;.EXE;.ZZQ")
    (tmp_path / "ls.zzq").write_text("x", encoding="utf-8")
    assert A.read_only_command("ls", folders=[tmp_path]) is False


@pytest.mark.parametrize("cwd", [r"\\evil-host\share", "//evil-host/share", r"\\?\UNC\evil-host\s", "~", "C:sub"])
def test_a_cwd_that_names_another_place_is_never_resolved(tmp_path, monkeypatch, cwd):
    resolved: list[str] = []
    real = A._inside

    def spy(path, workspace):
        resolved.append(str(path))
        return real(path, workspace)

    monkeypatch.setattr(A, "_inside", spy)
    assert A.read_only_call("shell", {"command": "ls", "cwd": cwd}, tmp_path) is False
    assert resolved == []


def test_git_in_a_sub_folder_is_not_passed_but_in_the_folder_it_is(tmp_path):
    ws = tmp_path / "ws"
    (ws / "unpacked").mkdir(parents=True)
    assert A.read_only_call("shell", {"command": f"{G} status"}, ws) is True
    assert A.read_only_call("shell", {"command": f"{G} status", "cwd": "."}, ws) is True
    assert A.read_only_call("shell", {"command": f"{G} status", "cwd": "unpacked"}, ws) is False
    assert A.read_only_call("shell", {"command": f"{G} log | head", "cwd": "unpacked"}, ws) is False
    # Other read-only programs in a sub-folder still pass.
    assert A.read_only_call("shell", {"command": "ls", "cwd": "unpacked"}, ws) is True


@pytest.mark.parametrize("command", [
    f"{G} -c core.fsmonitor=calc status",
    f"{G} -c core.pager=calc log",
    f"{G} -c alias.status=!calc status",
    f"{G} --exec-path=x status",
    f"{G} -C .. status",
    f"{G} --git-dir=../x status",
    f"{G} -p status",
    f"{G} show HEAD:../x",
    f"{G} stash",
    f"{G} diff --no-index C:/a C:/b",
    f"{G} log --output x.txt",
    f"{G} diff --output=x",
    f"{G} branch --edit-description",
    f"{G} branch --set-upstream-to=x",
    "find . -fprint x", "find . -fls x", "find . -ok rm ;", "find . -okdir rm +",
    "echo hi | tee x", "cat x | xargs rm", "ls | Out-File x", "cmd /c ls", "bash -c ls",
    "powershell -Command ls", "node -e 1", "python -c 1", "uv run x", "npm run x",
    "ls &> x", "ls 2> x", "ls ^& calc", "ls !x!", r"ls a\|calc", "type %APPDATA%\\x",
    "echo $SECRET", "echo %SECRET%", "X=1 ls", "ls # rm -rf x", "@ls",
])
def test_more_hostile_shapes_are_not_read_only(command):
    assert A.read_only_command(command) is False, command


async def test_under_ask_first_a_folders_own_ask_rule_on_shell_keeps_the_card(env, monkeypatch):
    """A folder that says "ask before shell" (``plugins.toml`` [[rules]]) is
    the user's own rule: the read-only pass never overrides it under the
    default posture."""
    rules = env.ws / ".ironjarvis" / "harness"
    rules.mkdir(parents=True, exist_ok=True)
    (rules / "plugins.toml").write_text('[[rules]]\ntool = "shell"\naction = "ask"\n', encoding="utf-8")
    ran = _executes(env.platform, monkeypatch, "shell")
    _, judge = env.wire([_bash(f"{G} status")])
    out = await _stream(env.platform, _body(env.ws, mode="approve_for_me"), harness=True, answer="deny")
    assert [c["tool"] for c in out["cards"]] == ["shell"]
    assert ran["shell"] == [] and judge.seen == []


async def test_ask_first_never_asks_the_judge_nor_writes_an_auto_event_for_a_command_it_cannot_pass(env, monkeypatch):
    """Rules only under Ask first: an undecided command cards as before, with
    no model call and no auto-approved event."""
    ran = _executes(env.platform, monkeypatch, "shell")
    _, judge = env.wire([_bash("python app.py")])
    out = await _stream(env.platform, _body(env.ws, mode="approve_for_me"), harness=True, answer="deny")
    assert [c["tool"] for c in out["cards"]] == ["shell"]
    assert ran["shell"] == [] and judge.seen == []
    assert _auto_events(env.bus) == []


def test_with_no_folder_known_nothing_is_read_only():
    """Where the command runs, and what sits there, cannot be read."""
    assert A.read_only_call("shell", {"command": "ls"}, None) is False
