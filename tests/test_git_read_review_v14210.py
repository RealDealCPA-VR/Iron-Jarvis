"""v1.421.0 (Wave 1, A4) REVIEW — ``git_read`` holes the first cut left open.

Each was found by running the REAL git (2.50 on the review PC) against a
throwaway repository whose own config — what a worker can write — names a
program; every program appends to ``marker``. The control in each test runs
plain git with the same trap and sees the program run, so the pin is never
vacuous.

1. ``git status -v`` (and ``--verbose``, ``--v``, ``-bv``) shows the staged
   diff THROUGH the folder's textconv driver — status takes no
   ``--no-textconv``; the first cut armed nothing against it and the program
   ran;
2. a filter driver whose NAME holds ``=`` (``[filter "a=b"]`` +
   ``.gitattributes`` ``filter=a=b``) escaped the emptying ``-c
   filter.<x>.clean=``: ``git -c`` splits a key at its first ``=``, so the
   override set the wrong key and the filter ran on ``git diff``, ``git
   status`` and ``git blame``;
3. a signed commit (a fake ``gpgsig`` header is enough) plus the repo's own
   ``format.pretty`` / ``pretty.<alias>`` holding ``%G?`` and ``gpg.program``
   ran the program on a PLAIN ``git log -1`` / ``git show`` — the ``%G``
   refusal reads only the model's own words;
4. ``git grep -nO<program>``: ``-O`` (the pager it runs) inside a cluster of
   short options — the first cut checked only a word that STARTS ``-O``;
5. submodules: the repo's ``diff.submodule = diff`` (or the model's
   ``--submodule=diff``) makes ``git log -p`` / ``git show`` run ``git diff``
   INSIDE the submodule, where that repository's own ``diff.external`` runs;
   and the model's ``--ignore-submodules=none`` came after (and beat) the
   guard ``--ignore-submodules=all``;
6. ``core.worktree`` in the repo's config sends git to files OUTSIDE the
   folder (``git status`` listed them);
7. ``git describe --dirty`` / ``--broken`` REWRITE ``.git/index`` (git takes
   the index lock and saves the refreshed stat data whatever
   ``GIT_OPTIONAL_LOCKS`` says) — the tool says it never changes the
   repository;
8. a ``..`` step glued to a cluster (``grep -nf../x``) read a file outside the
   folder past the text check;
9. a folder holding Iron Jarvis's own keys and records (the protected roots
   ``read_file`` refuses): ``blame --contents`` printed a key file;
10. ``blame.ignoreRevsFile`` naming a file outside: blame echoed its first
    line in an error;
11. a REMERGE (``--remerge-diff``, or ``log -m -p`` under the repo's own
    ``log.diffMerges = remerge``) ran the folder's merge driver.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import time
from pathlib import Path

import pytest

from iron_jarvis.harness import git_read as G
from iron_jarvis.tools.base import ToolContext


def _git(cwd: Path, *args: str, input: "bytes | None" = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-c", "user.email=a@b.c", "-c", "user.name=t", "-c", "core.autocrlf=false",
         "-c", "protocol.file.allow=always", *args],
        cwd=str(cwd), capture_output=True, timeout=60, input=input,
    )


def _ctx(folder: Path) -> ToolContext:
    return ToolContext(workspace=folder, session_id="s", agent_run_id="r", config=None,
                       event_bus=None, engine=None)


def _repo(root: Path, name: str = "repo") -> Path:
    repo = root / name
    repo.mkdir()
    assert _git(repo, "init", "-q").returncode == 0
    (repo / "app.py").write_bytes(b"X = 1\n")
    (repo / "notes.txt").write_bytes(b"hello\n")
    assert _git(repo, "add", "-A").returncode == 0
    assert _git(repo, "commit", "-q", "-m", "first").returncode == 0
    return repo


def _program(tmp_path: Path, marker: Path, word: str) -> str:
    """A shell line that appends ``word`` to ``marker`` (and passes its input
    on, so a filter or a textconv still answers)."""
    return f"sh -c 'echo {word} >> {marker.as_posix()}; cat \"$0\" 2>/dev/null || cat'"


async def _read(repo: Path, command: str, args: list[str]):
    return await G.GitReadTool().execute({"command": command, "args": args}, _ctx(repo))


# --------------------------------------------------------------------------- #
# 1. status -v: the staged diff through the folder's textconv
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("args", [["-v"], ["-vv"], ["--verbose"], ["--v"], ["--verb"], ["-bv"],
                                  ["--short", "-v"]])
def test_status_verbose_is_refused_in_plain_words(args):
    said = G.refusal("status", args)
    assert said and "git diff --cached" in said, (args, said)


def test_status_keeps_its_ordinary_options():
    for args in (["--short"], ["-sb"], ["--porcelain=v2"], ["-uno"], ["--ignored"], ["--branch"]):
        assert G.refusal("status", args) == "", args


async def test_status_verbose_never_runs_the_folders_textconv(tmp_path):
    marker = tmp_path / "marker"
    repo = _repo(tmp_path)
    (repo / ".gitattributes").write_bytes(b"*.txt diff=conv\n")
    assert _git(repo, "config", "diff.conv.textconv", _program(tmp_path, marker, "textconv")).returncode == 0
    _git(repo, "add", ".gitattributes")
    _git(repo, "commit", "-q", "-m", "attr")
    (repo / "notes.txt").write_bytes(b"hello again\n")
    _git(repo, "add", "notes.txt")
    _git(repo, "--no-pager", "status", "-v")
    assert marker.exists(), "the control did not run the trap: the test would be vacuous"
    marker.unlink()
    res = await _read(repo, "status", ["-v"])
    assert not res.ok and not marker.exists()


# --------------------------------------------------------------------------- #
# 2. a filter whose name holds "="
# --------------------------------------------------------------------------- #


async def test_a_filter_named_with_an_equals_sign_runs_nothing(tmp_path):
    marker = tmp_path / "marker"
    repo = _repo(tmp_path)
    (repo / ".gitattributes").write_bytes(b"*.py filter=a=b\n")
    assert _git(repo, "config", "filter.a=b.clean", _program(tmp_path, marker, "filter")).returncode == 0
    assert _git(repo, "config", "filter.a=b.required", "true").returncode == 0
    _git(repo, "add", ".gitattributes")
    _git(repo, "commit", "-q", "-m", "attr")
    (repo / "app.py").write_bytes(b"X = 2\n")
    _git(repo, "--no-pager", "diff")
    assert marker.exists(), "the control did not run the trap: the test would be vacuous"
    for command, args in (("diff", []), ("status", []), ("blame", ["app.py"])):
        if marker.exists():
            marker.unlink()
        res = await _read(repo, command, args)
        assert not marker.exists(), (command, marker.read_text())
        assert not res.ok and "filter" in res.error, (command, res.error)


# --------------------------------------------------------------------------- #
# 3. a signature checked through the repo's own pretty formats
# --------------------------------------------------------------------------- #


#: Git picks the signature's program by its ARMOUR, whatever ``gpg.format``
#: says: (the armour's kind, its program setting).
_SIGNATURES = {
    "openpgp": ("PGP SIGNATURE", "gpg.program"),
    "ssh": ("SSH SIGNATURE", "gpg.ssh.program"),
    "x509": ("SIGNED MESSAGE", "gpg.x509.program"),
}


def _signed_commit(repo: Path, armour: str) -> None:
    """A commit carrying a (fake) signature — enough for git to call the
    signature program when a format shows ``%G?``."""
    tree = _git(repo, "rev-parse", "HEAD^{tree}").stdout.decode().strip()
    parent = _git(repo, "rev-parse", "HEAD").stdout.decode().strip()
    body = (f"tree {tree}\nparent {parent}\nauthor t <a@b.c> 1700000000 +0000\n"
            "committer t <a@b.c> 1700000000 +0000\n"
            f"gpgsig -----BEGIN {armour}-----\n \n iQEzBAABCAAdFiEE\n -----END {armour}-----\n"
            "\nsigned\n").encode()
    sha = _git(repo, "hash-object", "-t", "commit", "-w", "--stdin", input=body).stdout.decode().strip()
    assert sha and _git(repo, "update-ref", "HEAD", sha).returncode == 0


@pytest.mark.parametrize("fmt", sorted(_SIGNATURES))
async def test_the_repos_own_pretty_format_runs_no_signature_program(tmp_path, fmt):
    marker = tmp_path / "marker"
    repo = _repo(tmp_path)
    armour, program_key = _SIGNATURES[fmt]
    _signed_commit(repo, armour)
    evil = tmp_path / "evil-sign.sh"
    evil.write_text(f"#!/bin/sh\necho sign >> {marker.as_posix()}\nexit 1\n")
    evil.chmod(0o755)  # Linux and macOS run a script only when it is marked runnable
    allowed = tmp_path / "allowed-signers"
    allowed.write_bytes(b"")
    for key, value in ((program_key, evil.as_posix()), ("gpg.ssh.allowedSignersFile", allowed.as_posix()),
                       ("format.pretty", "%h %G?"), ("pretty.sig", "%h %G?")):
        assert _git(repo, "config", key, value).returncode == 0
    _git(repo, "--no-pager", "log", "-1")
    assert marker.exists(), "the control did not run the trap: the test would be vacuous"
    for command, args in (("log", ["-1"]), ("show", ["-s"]), ("log", ["--pretty=sig", "-1"]),
                          ("show", [])):
        if marker.exists():
            marker.unlink()
        res = await _read(repo, command, args)
        assert res.ok, (command, res.error)
        assert not marker.exists(), (fmt, command, args)


# --------------------------------------------------------------------------- #
# 4. -O inside a cluster of short options
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("word", ["-nOcalc", "-iO/bin/sh", "-nO", "-cO"])
def test_a_pager_inside_a_cluster_is_refused(word):
    assert G.refusal("grep", [word, "X"]), word


async def test_grep_runs_no_pager_from_a_cluster(tmp_path):
    marker = tmp_path / "marker"
    repo = _repo(tmp_path)
    evil = tmp_path / "pager.sh"
    evil.write_text(f"#!/bin/sh\necho pager >> {marker.as_posix()}\n")
    evil.chmod(0o755)  # Linux and macOS run a script only when it is marked runnable
    _git(repo, "--no-pager", "grep", "-nO" + evil.as_posix(), "X")
    assert marker.exists(), "the control did not run the trap: the test would be vacuous"
    marker.unlink()
    res = await _read(repo, "grep", ["-nO" + evil.as_posix(), "X"])
    assert not res.ok and not marker.exists()


@pytest.mark.parametrize("command,word", [("grep", "-nf../patterns.txt"), ("grep", "-if/etc/x"),
                                          ("blame", "-wS../revs"), ("ls-files", "-iX../ignore")])
def test_a_path_glued_to_a_cluster_is_checked(command, word):
    assert G.refusal(command, [word]), (command, word)


# --------------------------------------------------------------------------- #
# 5. submodules
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("command,args", [
    ("diff", ["--ignore-submodules=none"]), ("diff", ["--ignore-submodules", "HEAD"]),
    ("status", ["--ignore-submodules=dirty"]), ("log", ["-p", "--submodule=diff"]),
    ("show", ["--submodule", "diff"]), ("diff", ["--submod=diff"]), ("diff", ["--ignore-sub=none"]),
])
def test_the_model_cannot_undo_the_submodule_guard(command, args):
    assert G.refusal(command, args), (command, args)


async def test_a_submodules_own_external_diff_never_runs(tmp_path):
    marker = tmp_path / "marker"
    src = _repo(tmp_path, "subsrc")
    sup = _repo(tmp_path, "super")
    assert _git(sup, "submodule", "add", "-q", str(src), "sub").returncode == 0
    _git(sup, "commit", "-q", "-m", "add sub")
    sub = sup / "sub"
    (sub / "notes.txt").write_bytes(b"changed in sub\n")
    _git(sub, "commit", "-q", "-am", "sub change")
    _git(sup, "add", "sub")
    _git(sup, "commit", "-q", "-m", "bump sub")
    assert _git(sub, "config", "diff.external", _program(tmp_path, marker, "subext")).returncode == 0
    assert _git(sup, "config", "diff.submodule", "diff").returncode == 0
    _git(sup, "--no-pager", "log", "-p", "-1")
    assert marker.exists(), "the control did not run the trap: the test would be vacuous"
    for command, args in (("log", ["-p", "-1"]), ("show", []), ("diff", ["HEAD~1"])):
        if marker.exists():
            marker.unlink()
        res = await _read(sup, command, args)
        assert res.ok, (command, res.error)
        assert not marker.exists(), (command, args)


# --------------------------------------------------------------------------- #
# 6. core.worktree outside the folder
# --------------------------------------------------------------------------- #


async def test_a_worktree_outside_the_folder_is_refused(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_bytes(b"TOPSECRET\n")
    repo = _repo(tmp_path)
    assert _git(repo, "config", "core.worktree", outside.as_posix()).returncode == 0
    plain = _git(repo, "--no-pager", "status", "--short").stdout.decode()
    assert "secret.txt" in plain, "the control did not leave the folder: the test would be vacuous"
    for command, args in (("status", ["--short"]), ("grep", ["--untracked", "TOPSECRET"]),
                          ("ls-files", ["--others"])):
        res = await _read(repo, command, args)
        assert not res.ok, (command, res.output)
        assert "secret.txt" not in (res.output or "") and "TOPSECRET" not in (res.error or "")


async def test_a_network_worktree_is_refused_by_its_spelling(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    assert _git(repo, "config", "core.worktree", "//no-such-host-ij/share").returncode == 0
    calls: list[list[str]] = []
    from iron_jarvis.git import integration as I

    orig = I.run_git

    def spy(args, *a, **k):
        calls.append(list(args))
        return orig(args, *a, **k)

    monkeypatch.setattr(I, "run_git", spy)
    res = await _read(repo, "status", [])
    assert not res.ok
    # Refused from the setting's TEXT: git was never asked to find that
    # folder (a lookup of //host/share is a connection to that host).
    assert not any("status" in c or "--show-toplevel" in c for c in calls), calls


async def test_a_worktree_inside_the_repository_still_reads(tmp_path):
    # A submodule's own gitdir names its working folder with core.worktree:
    # one that CONTAINS the folder is fine.
    repo = _repo(tmp_path)
    assert _git(repo, "config", "core.worktree", repo.as_posix()).returncode == 0
    res = await _read(repo, "status", ["--short"])
    assert res.ok, res.error


# --------------------------------------------------------------------------- #
# 7. describe --dirty rewrites the index
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("args", [["--dirty"], ["--always", "--dirty=-x"], ["--broken"], ["--dirt"]])
def test_describe_never_takes_dirty_or_broken(args):
    said = G.refusal("describe", args)
    assert said and "git status" in said, (args, said)


async def test_describe_leaves_the_index_alone(tmp_path):
    repo = _repo(tmp_path)
    index = repo / ".git" / "index"
    time.sleep(1.1)
    os.utime(repo / "app.py", None)  # the stat data moves; the content does not

    def state():
        return hashlib.sha1(index.read_bytes()).hexdigest(), index.stat().st_mtime_ns

    before = state()
    res = await _read(repo, "describe", ["--always"])
    assert res.ok and state() == before
    await _read(repo, "describe", ["--always", "--dirty"])
    assert state() == before


def test_the_argv_carries_the_review_guards():
    argv = " ".join(G.safe_argv("log", ["-p"], []))
    for piece in ("diff.submodule=short", "submodule.recurse=false", "gpg.program=",
                  "gpg.openpgp.program=", "gpg.x509.program=", "gpg.ssh.program="):
        assert piece in argv, piece


# --------------------------------------------------------------------------- #
# 9. Iron Jarvis's own keys and records (the protected roots)
# --------------------------------------------------------------------------- #


async def test_a_folder_holding_iron_jarviss_own_keys_is_refused(tmp_path, monkeypatch):
    """``read_file`` and ``grep`` refuse the protected roots (the secrets and
    undo stores, the database — ``core.fs_policy``); git reads files by
    itself, so a session folder that HOLDS them (the user's home holds the
    app's state home) let ``git blame --contents`` print a key file and
    ``git grep --untracked`` search the secrets store. The folder git would
    read — the repository's top when there is one — must hold none."""
    from iron_jarvis.core import fs_policy

    repo = _repo(tmp_path, "home")  # a home folder that is a repository
    secrets = repo / ".ironjarvis" / "secrets"
    secrets.mkdir(parents=True)
    (secrets / "vault.txt").write_bytes(b"FERNETKEYVALUE\n")
    monkeypatch.setattr(fs_policy, "_PROTECTED_ROOTS", set(fs_policy._PROTECTED_ROOTS))
    fs_policy.register_protected_root(secrets)
    ok, _why = fs_policy.fs_read_ok(secrets / "vault.txt")
    assert not ok, "the control: read_file would refuse this file"
    plain = _git(repo, "--no-pager", "blame", "--contents", ".ironjarvis/secrets/vault.txt", "app.py")
    assert b"FERNETKEYVALUE" in plain.stdout, "the control did not read the key: the test would be vacuous"
    for command, args in (("blame", ["--contents", ".ironjarvis/secrets/vault.txt", "app.py"]),
                          ("grep", ["--untracked", "-n", "FERNET"]), ("status", ["--short"])):
        res = await _read(repo, command, args)
        assert not res.ok, (command, res.output)
        assert "FERNETKEYVALUE" not in (res.output or "") + (res.error or "")
    # A SUBFOLDER of that repository: git still reads the whole repository
    # (``:/``), so it is refused too.
    sub = repo / "proj"
    sub.mkdir()
    res = await _read(sub, "grep", ["--untracked", "FERNET", "--", ":/"])
    assert not res.ok and "FERNETKEYVALUE" not in (res.output or "")


async def test_a_folder_beside_the_keys_still_reads(tmp_path, monkeypatch):
    from iron_jarvis.core import fs_policy

    repo = _repo(tmp_path)
    secrets = tmp_path / ".ironjarvis" / "secrets"
    secrets.mkdir(parents=True)
    monkeypatch.setattr(fs_policy, "_PROTECTED_ROOTS", set(fs_policy._PROTECTED_ROOTS))
    fs_policy.register_protected_root(secrets)
    res = await _read(repo, "status", ["--short"])
    assert res.ok, res.error


async def test_outside_a_repository_the_folder_itself_must_hold_no_keys(tmp_path, monkeypatch):
    from iron_jarvis.core import fs_policy

    folder = tmp_path / "plain"
    secrets = folder / "secrets"
    secrets.mkdir(parents=True)
    (secrets / "k.txt").write_bytes(b"FERNETKEYVALUE\n")
    (folder / "b.txt").write_bytes(b"x\n")
    monkeypatch.setattr(fs_policy, "_PROTECTED_ROOTS", set(fs_policy._PROTECTED_ROOTS))
    fs_policy.register_protected_root(secrets)
    # Outside a repository `git diff a b` compares two files (no index needed).
    res = await _read(folder, "diff", ["secrets/k.txt", "b.txt"])
    assert not res.ok and "FERNETKEYVALUE" not in (res.output or "") + (res.error or "")


# --------------------------------------------------------------------------- #
# 10. blame.ignoreRevsFile names a file outside the folder
# --------------------------------------------------------------------------- #


async def test_blame_never_echoes_a_file_the_repos_config_names_outside_the_folder(tmp_path):
    """``git blame`` READS ``blame.ignoreRevsFile`` and names a line it cannot
    read as a commit in its error (measured: the first line of a file outside
    the folder came back in "fatal: invalid object name: <line>"); an empty
    ``-c`` value does not clear it."""
    secret = tmp_path / "outside-secret.txt"
    secret.write_bytes(b"SECRETLINE-ONE\n")
    repo = _repo(tmp_path)
    assert _git(repo, "config", "blame.ignoreRevsFile", secret.as_posix()).returncode == 0
    plain = _git(repo, "--no-pager", "blame", "app.py")
    assert b"SECRETLINE-ONE" in plain.stderr, "the control did not echo the file: the test would be vacuous"
    res = await _read(repo, "blame", ["app.py"])
    assert not res.ok and "SECRETLINE" not in (res.output or "") + (res.error or "")
    # The usual setting — a list kept in the repository — still blames.
    (repo / ".git-blame-ignore-revs").write_bytes(b"")
    assert _git(repo, "config", "blame.ignoreRevsFile", ".git-blame-ignore-revs").returncode == 0
    res = await _read(repo, "blame", ["app.py"])
    assert res.ok, res.error


# --------------------------------------------------------------------------- #
# 11. a merge shown by re-merging runs the folder's merge driver
# --------------------------------------------------------------------------- #


def _merged_repo(tmp_path: Path, marker: Path) -> Path:
    repo = tmp_path / "merge"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "app.py").write_bytes(b"X = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    _git(repo, "switch", "-q", "-c", "side")
    (repo / "app.py").write_bytes(b"X = 2\n")
    _git(repo, "commit", "-qam", "side")
    _git(repo, "switch", "-q", "main")
    (repo / "app.py").write_bytes(b"X = 3\n")
    _git(repo, "commit", "-qam", "main")
    _git(repo, "merge", "side", "-m", "merge")  # a conflict, resolved by hand
    (repo / "app.py").write_bytes(b"X = 23\n")
    _git(repo, "add", "app.py")
    assert _git(repo, "commit", "-qm", "merged").returncode == 0
    (repo / ".gitattributes").write_bytes(b"*.py merge=evil\n")
    assert _git(repo, "config", "merge.evil.driver",
                f"sh -c 'echo mergedriver >> {marker.as_posix()}; exit 1'").returncode == 0
    return repo


@pytest.mark.parametrize("command,args", [
    ("show", ["--remerge-diff", "HEAD"]), ("log", ["--remerge-diff", "-1"]),
    ("log", ["--diff-merges=remerge", "-1"]), ("log", ["--diff-merges=r", "-1"]),
    ("log", ["--diff-merges", "remerge", "-1"]), ("show", ["--remerge"]),
])
def test_a_remerge_is_refused(command, args):
    assert G.refusal(command, args), (command, args)


async def test_showing_a_merge_never_runs_the_folders_merge_driver(tmp_path):
    """``--remerge-diff`` re-runs a merge in memory, and that runs the merge
    driver ``.gitattributes`` + ``merge.<x>.driver`` name (measured); the
    repo's own ``log.diffMerges = remerge`` makes a plain ``git log -m -p``
    do the same."""
    marker = tmp_path / "marker"
    repo = _merged_repo(tmp_path, marker)
    _git(repo, "--no-pager", "show", "--remerge-diff", "HEAD")
    assert marker.exists(), "the control did not run the trap: the test would be vacuous"
    assert _git(repo, "config", "log.diffMerges", "remerge").returncode == 0
    for command, args in (("log", ["-m", "-p", "-1"]), ("log", ["--diff-merges=on", "-p", "-1"]),
                          ("show", ["-m", "HEAD"]), ("show", ["HEAD"])):
        if marker.exists():
            marker.unlink()
        res = await _read(repo, command, args)
        assert res.ok, (command, res.error)
        assert not marker.exists(), (command, args)
