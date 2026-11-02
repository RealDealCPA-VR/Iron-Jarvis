"""v1.422.2 REVIEW — the folder's config made ``git_read`` READ a file it
names, and show what it read.

Found by running every allowed call (the allowlist's whole RUN_TABLE plus a
reviewer's usual calls) through the REAL tool against one repository whose
own ``.git/config`` names every program and file a repo config can — no
program ran and no file changed, but two settings read a file OUTSIDE the
folder (any file: a key in Iron Jarvis's own protected stores too, which
``read_file`` refuses) and put its text in the answer:

1. ``mailmap.file`` — ``log``, ``show``, ``blame`` and ``shortlog`` map every
   author through it (git's ``log.mailmap`` is on by default), so a line
   ``<text> <a@b.c>`` of that file came back as the author's NAME of every
   commit by ``a@b.c`` (measured: the outside file's text in ``git log``,
   ``git blame`` and ``git shortlog``);
2. ``core.attributesFile`` — a word on a line that is not a valid attribute
   name is warned about BY NAME on stderr, and a call that FAILS shows its
   stderr (measured: ``blame -L 99,100`` on a 4-line file printed the outside
   file's token).

Now, when the REPOSITORY's own config (local or worktree scope — an included
file counts as the scope that includes it) sets either, ``git_read`` points
it at the null device (``-c mailmap.file=<null> -c
core.attributesFile=<null>``). The user's OWN global setting is left as it is
(as for filters): a setting a worker cannot write. The in-tree ``.mailmap`` /
``.gitattributes`` keep working (git reads those without following a link).

Each test's control runs plain git with the same trap and sees the outside
text, so no pin is vacuous.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from iron_jarvis.harness import git_read as G
from iron_jarvis.tools.base import ToolContext


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-c", "user.email=a@b.c", "-c", "user.name=t", "-c", "core.autocrlf=false", *args],
        cwd=str(cwd), capture_output=True, timeout=60,
    )


def _ctx(folder: Path) -> ToolContext:
    return ToolContext(workspace=folder, session_id="s", agent_run_id="r", config=None,
                       event_bus=None, engine=None)


async def _read(repo: Path, command: str, args: list[str]):
    return await G.GitReadTool().execute({"command": command, "args": args}, _ctx(repo))


def _repo(root: Path) -> Path:
    repo = root / "repo"
    repo.mkdir()
    assert _git(repo, "init", "-q").returncode == 0
    (repo / "app.py").write_bytes(b"X = 1\n\ndef f():\n    return X\n")
    assert _git(repo, "add", "-A").returncode == 0
    assert _git(repo, "commit", "-q", "-m", "first").returncode == 0
    return repo


@pytest.fixture()
def no_global(tmp_path, monkeypatch):
    """The user's own global config is out of the way (an empty one), so only
    the repository's settings decide; a test sets a global one itself."""
    empty = tmp_path / "global.cfg"
    empty.write_bytes(b"")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    return empty


# --------------------------------------------------------------------------- #
# 1. mailmap.file
# --------------------------------------------------------------------------- #


MAILMAP_CALLS = [("log", ["-1"]), ("log", ["--format=%aN", "-1"]), ("show", ["-s"]),
                 ("blame", ["app.py"]), ("shortlog", ["-sne", "HEAD"]), ("log", ["--pretty=fuller", "-1"])]


@pytest.mark.parametrize("via_include", [False, True], ids=["local", "included"])
async def test_the_folders_mailmap_file_is_never_read(tmp_path, no_global, via_include):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_bytes(b"OUTSIDE-SECRET-TEXT <a@b.c>\n")
    repo = _repo(tmp_path)
    value = (outside / "secret.txt").as_posix()
    if via_include:
        inc = repo / ".git" / "more.cfg"
        inc.write_text(f"[mailmap]\n\tfile = {value}\n")
        assert _git(repo, "config", "include.path", inc.as_posix()).returncode == 0
    else:
        assert _git(repo, "config", "mailmap.file", value).returncode == 0
    plain = _git(repo, "--no-pager", "log", "-1").stdout.decode()
    assert "OUTSIDE-SECRET-TEXT" in plain, "the control did not read the file: the test would be vacuous"
    for command, args in MAILMAP_CALLS:
        res = await _read(repo, command, args)
        assert res.ok, (command, args, res.error)
        assert "OUTSIDE-SECRET-TEXT" not in res.output, (command, args, res.output)


async def test_the_users_own_global_mailmap_still_maps(tmp_path, monkeypatch):
    """A setting the user made (global scope) is theirs, not the folder's."""
    mine = tmp_path / "my-mailmap"
    mine.write_bytes(b"Proper Name <a@b.c>\n")
    glob = tmp_path / "global.cfg"
    glob.write_text(f"[mailmap]\n\tfile = {mine.as_posix()}\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(glob))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    repo = _repo(tmp_path)
    res = await _read(repo, "log", ["--format=%aN", "-1"])
    assert res.ok and res.output.strip() == "Proper Name", res.output


async def test_the_in_tree_mailmap_still_maps(tmp_path, no_global):
    repo = _repo(tmp_path)
    (repo / ".mailmap").write_bytes(b"Team Name <a@b.c>\n")
    res = await _read(repo, "log", ["--format=%aN", "-1"])
    assert res.ok and res.output.strip() == "Team Name", res.output


# --------------------------------------------------------------------------- #
# 2. core.attributesFile
# --------------------------------------------------------------------------- #


async def test_the_folders_attributes_file_is_never_read(tmp_path, no_global):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keys.json").write_bytes(b'{"key": "OUTSIDE-TOKEN!x"}\n')
    repo = _repo(tmp_path)
    assert _git(repo, "config", "core.attributesFile", (outside / "keys.json").as_posix()).returncode == 0
    plain = _git(repo, "--no-pager", "blame", "-L", "99,100", "app.py").stderr.decode()
    assert "OUTSIDE-TOKEN" in plain, "the control did not read the file: the test would be vacuous"
    res = await _read(repo, "blame", ["-L", "99,100", "app.py"])
    assert not res.ok
    assert "OUTSIDE-TOKEN" not in (res.error or ""), res.error
    res = await _read(repo, "diff", ["--stat"])
    assert res.ok and "OUTSIDE-TOKEN" not in res.output


async def test_the_in_tree_attributes_still_count(tmp_path, no_global):
    """``.gitattributes`` in the folder is read as before (``binary``)."""
    repo = _repo(tmp_path)
    (repo / ".gitattributes").write_bytes(b"*.py binary\n")
    (repo / "app.py").write_bytes(b"X = 2\n")
    res = await _read(repo, "diff", [])
    assert res.ok and "Binary files" in res.output, res.output


# --------------------------------------------------------------------------- #
# 3. the argv
# --------------------------------------------------------------------------- #


def test_the_argv_points_each_named_setting_at_the_null_device():
    argv = G.safe_argv("log", ["-1"], [], files=["mailmap.file", "core.attributesfile"])
    pairs = [argv[i + 1] for i, w in enumerate(argv[:-1]) if w == "-c"]
    assert f"mailmap.file={os.devnull}" in pairs
    assert f"core.attributesFile={os.devnull}" in pairs
    # Before the command: a -c after it would be the command's own option.
    assert argv.index("log") > max(i for i, w in enumerate(argv) if w == "-c")
    # None named: none added (the user's own settings stay).
    plain = " ".join(G.safe_argv("log", ["-1"], []))
    assert "mailmap.file" not in plain and "attributesFile" not in plain


def test_a_git_that_cannot_say_the_scope_points_both_away(tmp_path, monkeypatch):
    """An older git with no ``--show-scope``: whatever scope set either, it is
    pointed away (as every filter is emptied there)."""
    from iron_jarvis.git import integration as I

    class Got:
        def __init__(self, code, out):
            self.code, self.out, self.err, self.timed_out = code, out, "", False

    def fake(args, *a, **k):
        if "--show-scope" in args:
            return Got(129, "")
        return Got(0, "mailmap.file\nC:/x\0core.attributesfile\nC:/y\0filter.f.clean\ncat\0")

    monkeypatch.setattr(I, "run_git", fake)
    got = G._repo_settings(tmp_path, {})
    assert got is not None
    filters, _worktrees, _skips, files = got
    assert filters == ["f"]
    assert sorted(files) == ["core.attributesfile", "mailmap.file"]
