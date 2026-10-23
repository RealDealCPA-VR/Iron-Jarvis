"""v1.375.0 REVIEW (IJ harness part 2, Phase 4): defects found in the doer's
branch code, each pinned with REAL git in a temp folder (and a fake ``gh``,
never the real GitHub).

* Commit / Merge / Create PR read the worktree's HEAD as the session's
  branch: a ``git switch main`` in the worktree made Commit commit onto the
  USER'S ``main``. Refused in words now, nothing changed.
* A folder inside a git SUBMODULE put its session worktree inside the
  superproject's ``.git``; it cannot branch now.
* Discard ran ``git worktree prune``, which forgot EVERY worktree whose
  folder was missing right then — a user's own worktree on an unplugged
  drive lost its index and HEAD. Only this session's worktree is forgotten.
* Discard with the trash on ANOTHER DRIVE copied, then deleted, and found a
  file in use part of the way through: the worktree was left half deleted
  (``.git`` first), and every later Discard refused it. A refusal now leaves
  the worktree whole.
* Merge with the base "checked out nowhere" moved the base under a checkout
  that was in the middle of a REBASE of it; refused now (git's own guard).
* Merge with the base checked out in a folder that is not there crashed (a
  500); said in words now.
* A ``gh`` that is a BATCH FILE ran a pull request title's ``" & … & "`` as
  commands; such a title is refused before anything runs.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

from iron_jarvis.harness import branches as B
from tests.test_build_git_v1375 import git, git_ok, make_remote, make_repo, no_real_gh

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


@pytest.fixture(autouse=True)
def _no_real_gh(monkeypatch):
    no_real_gh(monkeypatch)
    yield
    B.forget_gh_state()


@pytest.fixture()
def repo(tmp_path) -> Path:
    return make_repo(tmp_path / "proj")


def _start(folder: Path) -> dict:
    facts = B.start(str(folder))
    facts.pop("folder")
    return facts


def _commit_in(wt: Path, name: str, data: bytes) -> None:
    (wt / name).write_bytes(data)
    git("add", "-A", cwd=wt)
    git("commit", "-q", "-m", f"add {name}", cwd=wt)


# --------------------------------------------------------------------------- #
# the worktree's HEAD must be the session's branch
# --------------------------------------------------------------------------- #


def test_commit_merge_and_pr_refuse_when_the_worktree_was_switched_off_its_branch(repo, tmp_path):
    # The user's checkout is on another branch, so git lets the worktree
    # switch to main (a careless model's `git switch main`).
    git("checkout", "-q", "-b", "feature", cwd=repo)
    facts = _start(repo)
    wt = Path(facts["worktree"])
    main_before = git("rev-parse", "refs/heads/main", cwd=repo)
    git("checkout", "-q", "main", cwd=wt)
    (wt / "app.py").write_bytes(b"x = 2\n")
    with pytest.raises(B.GitRefused) as got:
        B.commit(facts, "edit")
    assert "instead of its branch " + facts["branch"] in str(got.value)
    assert "the branch main" in str(got.value) and got.value.status == 409
    assert git("rev-parse", "refs/heads/main", cwd=repo) == main_before, "main got a commit"
    assert git("status", "--porcelain", cwd=wt) == " M app.py", "nothing was staged either"
    with pytest.raises(B.GitRefused, match="instead of its branch"):
        B.merge(facts)
    with pytest.raises(B.GitRefused, match="instead of its branch"):
        B.create_pr(facts, "t", "b")
    # A detached worktree says so too.
    git("checkout", "-q", "--detach", cwd=wt)
    with pytest.raises(B.GitRefused, match=r"a single commit \(detached\)"):
        B.commit(facts, "edit")
    # Switched back, Commit works on the session's branch.
    git("checkout", "-q", facts["branch"], cwd=wt)
    done = B.commit(facts, "edit")
    assert done["committed"] and git("rev-parse", "refs/heads/main", cwd=repo) == main_before
    assert git("log", "-1", "--format=%s", facts["branch"], cwd=repo) == "edit"


# --------------------------------------------------------------------------- #
# a submodule cannot branch
# --------------------------------------------------------------------------- #


def test_a_folder_inside_a_submodule_cannot_branch_and_nothing_lands_in_the_superprojects_git(tmp_path):
    src = make_repo(tmp_path / "libsrc")
    sup = make_repo(tmp_path / "super")
    git("-c", "protocol.file.allow=always", "submodule", "add", "-q", str(src), "libs/sub", cwd=sup)
    git("commit", "-q", "-m", "sub", cwd=sup)
    info = B.inspect_folder(str(sup / "libs" / "sub"))
    assert info["repo"] is True and info["can_branch"] is False
    assert info["note"] == B.SUBMODULE
    with pytest.raises(B.GitRefused) as got:
        B.start(str(sup / "libs" / "sub"))
    assert str(got.value) == B.SUBMODULE and got.value.status == 400
    assert not [p for p in (sup / ".git").rglob("*") if p.name.endswith(B.WORKTREES_SUFFIX)]
    assert git("status", "--porcelain", "--untracked-files=all", cwd=sup) == ""
    # The superproject itself still branches as before.
    assert B.inspect_folder(str(sup))["can_branch"] is True


# --------------------------------------------------------------------------- #
# a start git refuses leaves nothing behind
# --------------------------------------------------------------------------- #


def test_a_start_a_hook_fails_leaves_no_branch_and_no_worktree_behind(repo, tmp_path):
    # Git LFS's own post-checkout hook exits non-zero when git-lfs is missing;
    # git then fails `worktree add` AFTER it made the branch and the checkout.
    hook = repo / ".git" / "hooks" / "post-checkout"
    hook.write_bytes(
        b"#!/bin/sh\n"
        b"echo 'This repository is configured for Git LFS but git-lfs was not found' >&2\n"
        b"exit 2\n"
    )
    hook.chmod(0o755)
    before = git("rev-parse", "HEAD", cwd=repo)
    refusals = []
    for _ in range(2):  # a retry leaves nothing either
        with pytest.raises(B.GitRefused) as got:
            B.start(str(repo))
        refusals.append(got.value)
    assert git("branch", "--list", "ij/*", cwd=repo) == "", "a stray ij/ branch was left"
    assert git("worktree", "list", "--porcelain", cwd=repo).count("worktree ") == 1, "a stray worktree"
    assert not (tmp_path / f"proj{B.WORKTREES_SUFFIX}").exists()
    assert git("rev-parse", "HEAD", cwd=repo) == before and git("status", "--porcelain", cwd=repo) == ""
    assert all(str(r).endswith("so nothing was made.") and "git-lfs" in r.details for r in refusals)


# --------------------------------------------------------------------------- #
# discard forgets THIS worktree only
# --------------------------------------------------------------------------- #


def test_discard_never_forgets_a_users_own_worktree_whose_folder_is_missing(repo, tmp_path):
    usb = tmp_path / "usb"
    git("worktree", "add", "-q", "-b", "usb-work", str(usb / "work"), cwd=repo)
    (usb / "work" / "staged.txt").write_bytes(b"staged, not committed\n")
    git("add", "staged.txt", cwd=usb / "work")
    os.rename(usb, tmp_path / "usb-unplugged")  # the drive is not plugged in
    facts = _start(repo)
    done = B.remove_worktree(tmp_path / "home", facts)
    assert done["moved"]
    listed = git("worktree", "list", "--porcelain", cwd=repo)
    assert facts["worktree"].replace("\\", "/") not in listed, "this session's worktree is forgotten"
    assert str(usb / "work").replace("\\", "/") in listed, "the user's own worktree is still known"
    assert git_ok("rev-parse", "--verify", "-q", f"refs/heads/{facts['branch']}", cwd=repo), "branch kept"
    os.rename(tmp_path / "usb-unplugged", usb)  # plugged back in
    assert git("status", "--porcelain", cwd=usb / "work") == "A  staged.txt"
    assert git("branch", "--show-current", cwd=usb / "work") == "usb-work"


# --------------------------------------------------------------------------- #
# discard across drives
# --------------------------------------------------------------------------- #


def _trash_on_another_drive(monkeypatch, home: Path) -> None:
    """``os.rename`` into ``home/trash`` fails as a move to another drive does
    (Windows ERROR_NOT_SAME_DEVICE); every other rename is real."""
    real = os.rename
    trash = os.path.normcase(str(home / "trash"))

    def fake(src, dst, *a, **k):
        if os.path.normcase(str(dst)).startswith(trash):
            err = OSError(18, "The system cannot move the file to a different disk drive")
            err.winerror = 17  # type: ignore[attr-defined]
            raise err
        return real(src, dst, *a, **k)

    monkeypatch.setattr(B.os, "rename", fake)
    monkeypatch.setattr(B, "RENAME_TRIES", 2)
    monkeypatch.setattr(B, "RENAME_RETRY_S", 0.01)


def _hold(path: Path):
    """Open ``path`` the way an editor does on Windows: no delete sharing."""
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.restype = wintypes.HANDLE
    k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    h = k32.CreateFileW(str(path), 0x80000000, 0x1, None, 3, 0x80, None)
    assert h and h != wintypes.HANDLE(-1).value
    return lambda: k32.CloseHandle(wintypes.HANDLE(h))


@pytest.mark.skipif(sys.platform != "win32", reason="a held file blocks a rename on Windows")
def test_a_discard_across_drives_with_a_file_in_use_leaves_the_worktree_whole(repo, tmp_path, monkeypatch):
    home = tmp_path / "home"
    facts = _start(repo)
    wt = Path(facts["worktree"])
    (wt / "notes.txt").write_bytes(b"work not committed\n")
    (wt / "zz-held.log").write_bytes(b"a dev server's log\n")  # held, and last in any order
    before = sorted(p.relative_to(wt).as_posix() for p in wt.rglob("*") if p.is_file())
    _trash_on_another_drive(monkeypatch, home)
    release = _hold(wt / "zz-held.log")
    try:
        with pytest.raises(B.GitRefused) as got:
            B.remove_worktree(home, facts)
    finally:
        release()
    after = sorted(p.relative_to(wt).as_posix() for p in wt.rglob("*") if p.is_file())
    assert after == before, "the worktree is whole after a refusal"
    assert (wt / ".git").is_file()
    assert "in use" in str(got.value) and "nothing was moved" in str(got.value)
    assert not list((home / "trash").rglob("*.txt")), "no copy was left in the trash"
    assert facts["worktree"].replace("\\", "/") in git("worktree", "list", "--porcelain", cwd=repo)
    # Nothing holds it now: the same Discard goes through, across the drives.
    done = B.remove_worktree(home, facts)
    assert done["moved"] and "leftover" not in done
    moved = Path(done["trash"])
    assert sorted(p.relative_to(moved).as_posix() for p in moved.rglob("*") if p.is_file()) == before
    assert not wt.exists() and not list(wt.parent.glob(f".{wt.name}.discarding-*"))
    assert facts["worktree"].replace("\\", "/") not in git("worktree", "list", "--porcelain", cwd=repo)


def test_a_discard_across_drives_whose_copy_fails_puts_the_worktree_back(repo, tmp_path, monkeypatch):
    home = tmp_path / "home"
    facts = _start(repo)
    wt = Path(facts["worktree"])
    (wt / "notes.txt").write_bytes(b"work not committed\n")
    _trash_on_another_drive(monkeypatch, home)

    def broken_copy(src, dst, **k):
        Path(dst).mkdir(parents=True)
        (Path(dst) / "partial").write_bytes(b"x")
        raise OSError(28, "There is not enough space on the disk")

    monkeypatch.setattr(B.shutil, "copytree", broken_copy)
    with pytest.raises(B.GitRefused, match="nothing was moved"):
        B.remove_worktree(home, facts)
    assert (wt / "notes.txt").read_bytes() == b"work not committed\n" and (wt / ".git").is_file()
    assert not list((home / "trash").rglob("partial"))
    assert not list(wt.parent.glob(f".{wt.name}.discarding-*"))


# --------------------------------------------------------------------------- #
# merge
# --------------------------------------------------------------------------- #


def test_merge_never_moves_a_base_that_a_checkout_is_rebasing(repo, tmp_path, monkeypatch):
    for n in (2, 3):
        (repo / "app.py").write_bytes(f"x = {n}\n".encode())
        git("commit", "-q", "-am", f"step {n}", cwd=repo)
    facts = _start(repo)
    _commit_in(Path(facts["worktree"]), "new.py", b"print('new')\n")
    # The user is in the middle of an interactive rebase of main (stopped to
    # edit a commit): `git worktree list` shows their checkout as detached.
    monkeypatch.setenv("GIT_SEQUENCE_EDITOR", f'"{sys.executable}" -c "import sys,re;p=sys.argv[1];'
                       "s=open(p).read();open(p,'w').write(re.sub(r'^pick','edit',s,count=1,flags=re.M))\"")
    git("rebase", "-q", "-i", "HEAD~2", cwd=repo)
    assert "detached" in git("worktree", "list", "--porcelain", cwd=repo)
    main_before = git("rev-parse", "refs/heads/main", cwd=repo)
    with pytest.raises(B.GitRefused) as got:
        B.merge(facts)
    assert "in use in one of your checkouts" in str(got.value) and "nothing was merged" in str(got.value)
    assert git("rev-parse", "refs/heads/main", cwd=repo) == main_before
    monkeypatch.delenv("GIT_SEQUENCE_EDITOR")
    # The user's rebase finishes as it would have.
    git("-c", "core.editor=true", "rebase", "--continue", cwd=repo)
    assert git("branch", "--show-current", cwd=repo) == "main"


def test_merge_into_a_base_checked_out_nowhere_still_lands(repo, tmp_path):
    facts = _start(repo)
    _commit_in(Path(facts["worktree"]), "new.py", b"print('new')\n")
    git("checkout", "-q", "--detach", cwd=repo)
    base = git("rev-parse", "refs/heads/main", cwd=repo)
    head = git("rev-parse", f"refs/heads/{facts['branch']}", cwd=repo)
    done = B.merge(facts)
    assert done["where"] == ""
    assert git("log", "-1", "--format=%P", "refs/heads/main", cwd=repo) == f"{base} {head}"
    assert not (repo / ".git" / "FETCH_HEAD").exists(), "the guard writes nothing"


def test_merge_with_the_base_in_a_folder_that_is_not_there_is_said_in_words(repo, tmp_path):
    facts = _start(repo)  # from main
    _commit_in(Path(facts["worktree"]), "new.py", b"print('new')\n")
    # The user's main lives in another checkout of theirs, on a drive that
    # is not plugged in right now.
    git("checkout", "-q", "-b", "feature", cwd=repo)
    git("worktree", "add", "-q", str(tmp_path / "mainwt"), "main", cwd=repo)
    os.rename(tmp_path / "mainwt", tmp_path / "mainwt-away")
    main_before = git("rev-parse", "refs/heads/main", cwd=repo)
    with pytest.raises(B.GitRefused) as got:
        B.merge(facts)
    assert "a folder that is not there right now" in str(got.value)
    assert git("rev-parse", "refs/heads/main", cwd=repo) == main_before


def test_a_merge_git_refuses_over_untracked_files_says_so_and_touches_nothing(repo):
    facts = _start(repo)
    _commit_in(Path(facts["worktree"]), "new.py", b"print('new')\n")
    (repo / "new.py").write_bytes(b"MY OWN WORK, NOT IN GIT\n")
    main_before = git("rev-parse", "refs/heads/main", cwd=repo)
    with pytest.raises(B.GitRefused) as got:
        B.merge(facts)
    assert "files that are not in git which the merge would overwrite" in str(got.value)
    assert "new.py" in got.value.details
    assert (repo / "new.py").read_bytes() == b"MY OWN WORK, NOT IN GIT\n"
    assert git("rev-parse", "refs/heads/main", cwd=repo) == main_before
    assert not (repo / ".git" / "MERGE_HEAD").exists()


# --------------------------------------------------------------------------- #
# gh through a batch file
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(sys.platform != "win32", reason="cmd.exe reads a batch file's arguments")
def test_a_batch_file_gh_is_never_handed_a_title_cmd_would_run(repo, tmp_path, monkeypatch):
    from tests.test_build_git_v1375 import install_fake_gh

    bare = make_remote(tmp_path, repo)
    git("remote", "set-url", "origin", "https://github.com/acme/proj.git", cwd=repo)
    git("config", f"url.{bare.as_posix()}.insteadOf", "https://github.com/acme/proj.git", cwd=repo)
    gh = install_fake_gh(tmp_path, monkeypatch)
    assert B.gh_path().lower().endswith(".cmd"), "the fake gh is a batch file"
    facts = _start(repo)
    wt = Path(facts["worktree"])
    _commit_in(wt, "new.py", b"print('new')\n")
    refused = None
    try:
        B.create_pr(facts, 'Fix it" & echo owned>PWNED.txt & rem "', "body")
    except B.GitRefused as exc:
        refused = exc
    assert not (wt / "PWNED.txt").exists(), "the title's command RAN through cmd.exe"
    assert refused is not None and "batch file" in str(refused) and refused.status == 400
    assert not any(c[:2] == ["pr", "create"] for c in gh.read().get("calls", []))
    # The one door every gh call goes through refuses it too (any later call).
    with pytest.raises(B.GitRefused, match="batch file"):
        B._gh(["pr", "view", "x%PATH%"], wt)
    assert not any(c[:2] == ["pr", "view"] for c in gh.read().get("calls", []))
    assert not git_ok("rev-parse", "--verify", "-q", f"refs/heads/{facts['branch']}", cwd=bare), "nothing pushed"
    # An ordinary title goes through the same batch file.
    made = B.create_pr(facts, "Fix the greeting", "body")
    assert made["number"] == 7 and gh.read()["title"] == "Fix the greeting"


# --------------------------------------------------------------------------- #
# the routes: one git action at a time, said in words; facts never go stale
# --------------------------------------------------------------------------- #


@pytest.fixture()
def app(tmp_path):
    from iron_jarvis.daemon.app import create_app

    return create_app(str(tmp_path / "home"))


async def test_a_second_press_while_a_git_action_runs_says_the_branch_is_busy(app, repo, monkeypatch):
    import asyncio
    import threading

    from iron_jarvis.daemon.routes.build_git import GIT_BUSY, GIT_RUNNING
    from tests.test_build_git_v1375 import new_branch_session
    from tests.test_build_sessions_v1367 import asgi

    view = await new_branch_session(app, repo)
    sid, wt = view["id"], Path(view["git"]["worktree"])
    (wt / "app.py").write_bytes(b"x = 2\n")
    entered, gate = threading.Event(), threading.Event()
    real = B.commit

    def slow(facts, message):
        entered.set()
        gate.wait(20)
        return real(facts, message)

    monkeypatch.setattr(B, "commit", slow)
    first = asyncio.create_task(asgi(app, "POST", f"/build/sessions/{sid}/git/commit", {"message": "one"}))
    try:
        assert await asyncio.to_thread(entered.wait, 20)
        for path, body in (("git/commit", {"message": "two"}), ("git/push", {}), ("git/merge", {}),
                           ("git/discard", {"confirm": True})):
            status, got = await asgi(app, "POST", f"/build/sessions/{sid}/{path}", body)
            assert status == 409 and got["detail"] == GIT_BUSY != GIT_RUNNING, (path, got)
        status, got = await asgi(app, "DELETE", f"/build/sessions/{sid}")
        assert status == 409 and got["detail"] == GIT_BUSY
    finally:
        gate.set()
    status, done = await first
    assert status == 200 and done["committed"] is True
    assert wt.is_dir(), "nothing was discarded or deleted meanwhile"


async def test_an_action_records_onto_the_facts_as_stored_now(app, repo, monkeypatch):
    from iron_jarvis.harness import sessions as S
    from tests.test_build_git_v1375 import new_branch_session
    from tests.test_build_sessions_v1367 import asgi, setup_of

    view = await new_branch_session(app, repo)
    sid, wt = view["id"], Path(view["git"]["worktree"])
    _commit_in(wt, "new.py", b"print('new')\n")
    engine = app.state.platform.engine
    real = B.merge

    def merge_after_a_pr_landed(facts):
        # A Create PR recorded its link after this request read its facts.
        stored = S.git_of(S.get_row(engine, sid))
        S.update(engine, sid, setup={B.SETUP_KEY: {**stored, "pr_url": "https://github.com/a/b/pull/7",
                                                   "pr_number": "7"}})
        return real(facts)

    monkeypatch.setattr(B, "merge", merge_after_a_pr_landed)
    status, got = await asgi(app, "POST", f"/build/sessions/{sid}/git/merge", {})
    assert status == 200, got
    facts = setup_of(app, sid)["git"]
    assert facts.get("merged_at") and facts.get("pr_url") == "https://github.com/a/b/pull/7"
