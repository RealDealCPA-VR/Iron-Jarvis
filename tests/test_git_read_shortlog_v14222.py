"""v1.422.2 REVIEW — ``git_read shortlog`` with no revision answered nothing.

``git shortlog`` with no revision reads its STANDARD INPUT (git adds HEAD only
when that is a terminal), and ``git_read`` gives git an empty one — so a
reviewer's ``shortlog -sn`` (the call a person types) answered "(git printed
nothing)", as if nobody had committed (measured on this repository). Now, with
no word before ``--`` (and no ``--all``), HEAD is the revision, as in a
terminal; a revision or path the model named is left as it named it; and a
shortlog that still prints nothing says why. The control proves git itself
prints nothing for the old call.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from iron_jarvis.harness import git_read as G
from iron_jarvis.tools.base import ToolContext


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-c", "user.email=a@b.c", "-c", "user.name=Ada", "-c", "core.autocrlf=false", *args],
        cwd=str(cwd), capture_output=True, timeout=60, stdin=subprocess.DEVNULL,
    )


@pytest.fixture()
def repo(tmp_path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    assert _git(repo, "init", "-q", "-b", "main").returncode == 0
    (repo / "app.py").write_bytes(b"X = 1\n")
    _git(repo, "add", "-A")
    assert _git(repo, "commit", "-q", "-m", "first").returncode == 0
    (repo / "app.py").write_bytes(b"X = 2\n")
    assert _git(repo, "commit", "-q", "-am", "second").returncode == 0
    return repo


def _ctx(folder: Path) -> ToolContext:
    return ToolContext(workspace=folder, session_id="s", agent_run_id="r", config=None,
                       event_bus=None, engine=None)


async def _read(repo: Path, args: list[str]):
    return await G.GitReadTool().execute({"command": "shortlog", "args": args}, _ctx(repo))


@pytest.mark.parametrize("args", [["-sn"], ["-sne"], [], ["--since=2000-01-01", "-s"], ["-sn", "--", "app.py"],
                                  ["-2", "-s"]])
async def test_a_shortlog_with_no_revision_reads_head(repo, args):
    plain = _git(repo, "--no-pager", "shortlog", *args)
    assert plain.stdout.decode().strip() == "", "the control printed something: the test would be vacuous"
    res = await _read(repo, args)
    assert res.ok, res.error
    assert "Ada" in res.output, (args, res.output)


@pytest.mark.parametrize("args,expect", [
    (["-sn"], ["-sn", "HEAD"]),
    (["-sn", "--", "app.py"], ["-sn", "HEAD", "--", "app.py"]),
    (["--author", "x"], ["--author", "x", "HEAD"]),  # "x" is the author, not a revision
    (["-sn", "main"], ["-sn", "main"]),  # the model named one: left as it is
    (["-sn", "--all"], ["-sn", "--all"]),
    (["-sn", "app.py"], ["-sn", "app.py"]),
])
def test_head_is_added_only_when_no_revision_is_named(args, expect):
    assert G._default_revision("shortlog", args) == expect
    # Every other command is never changed.
    for command in G.ALLOWED:
        if command != "shortlog":
            assert G._default_revision(command, args) == args


async def test_a_shortlog_that_still_prints_nothing_says_why(repo):
    # A path before "--" reads as the model's own revision-or-path: git still
    # reads its (empty) input; the answer says so instead of "nothing".
    res = await _read(repo, ["-sn", "app.py"])
    assert res.ok and res.output == G.SHORTLOG_EMPTY, res.output
    assert "HEAD" in G.SHORTLOG_EMPTY and "\n" not in G.SHORTLOG_EMPTY
