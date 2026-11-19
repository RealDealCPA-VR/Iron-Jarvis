"""v1.543.0 — the whole-folder restore's ONE rule: nothing is removed or
written over without a copy (``hosted/checkpoints.py``).

Every case here is one an independent review of the first version measured
against real git and found losing data:

* ``git restore`` DELETES a folder that stands where a file must go, with
  whatever is inside it — a file git ignores, a nested repository, a junction
  to a folder OUTSIDE the repository — and deletes a file that stands where a
  folder must go;
* a file the checkpoint holds but git ignores NOW was written over;
* an Undo kept nothing of what it wrote over, and could be offered after a
  message had run;
* git held (and, stopped half way, left behind) the user's own ``index.lock``;
* a failed restore could strand files in the trash, or hide an earlier
  rewind's Undo;
* a session whose folder was already gone left its hidden refs behind.

Real git in a scratch repository every time.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

from iron_jarvis.hosted import checkpoints as CK
from iron_jarvis.hosted import panes as PN
from tests.test_build_code_checkpoints_v1543 import SID, _files, _git, _refs, _row, _rows, _work


@pytest.fixture()
def repo(tmp_path) -> Path:
    """The same scratch repository as ``test_build_code_checkpoints_v1543``."""
    folder = tmp_path / "repo"
    folder.mkdir()
    _git(folder, "init", "-q", "-b", "main")
    _git(folder, "config", "core.autocrlf", "false")  # the runners' global setting is true
    (folder / "a.py").write_bytes(b"a = 0\n")
    (folder / "b.py").write_bytes(b"b = 0\n")
    (folder / "sub").mkdir()
    (folder / "sub" / "s.py").write_bytes(b"s = 0\n")
    (folder / ".gitignore").write_bytes(b"*.log\n")
    _git(folder, "add", "-A")
    _git(folder, "commit", "-q", "-m", "first", when="2020-01-01T00:00:00+00:00")
    return folder


@pytest.fixture()
def home(tmp_path) -> Path:
    return tmp_path / "home"


def _junction(link: Path, target: Path) -> None:
    """A folder link that needs no privilege: a junction on Windows, a
    symlink elsewhere."""
    if sys.platform == "win32":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True, capture_output=True)
    else:
        os.symlink(target, link, target_is_directory=True)


def _refused(home: Path, repo: Path, turn: str = "t1") -> CK.RestoreRefused:
    with pytest.raises(CK.RestoreRefused) as said:
        CK.restore(home, SID, _row(repo), turn)
    return said.value


# --------------------------------------------------------------------------- #
# something that is in no checkpoint stands where a file must go
# --------------------------------------------------------------------------- #


def test_a_folder_holding_a_file_git_ignores_is_never_deleted_to_make_room(repo, home):
    (repo / "config").write_bytes(b"one file\n")
    _git(repo, "add", "-A")
    PN.record_turn_start(home, SID, str(repo), "t1")
    (repo / "config").unlink()
    (repo / "config").mkdir()
    (repo / "config" / "settings.py").write_bytes(b"s = 1\n")
    (repo / "config" / "run.log").write_bytes(b"ignored, and in no checkpoint\n")  # *.log is ignored
    as_is = _files(repo)
    made = CK.plan(home, SID, _row(repo), "t1")
    assert made["problems"] == [{"rel": "config", "why": CK.WHY_FOLDER}] and made["can"] is False
    assert _refused(home, repo).sentence == CK.PROBLEMS
    assert _files(repo) == as_is and not (home / "trash").exists()


def test_a_junction_inside_that_folder_is_never_followed_and_the_outside_folder_is_whole(repo, home, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_bytes(b"not this repository's file\n")
    (repo / "vendor").write_bytes(b"one file\n")
    PN.record_turn_start(home, SID, str(repo), "t1")
    (repo / "vendor").unlink()
    (repo / "vendor").mkdir()
    _junction(repo / "vendor" / "shared", outside)
    made = CK.plan(home, SID, _row(repo), "t1")
    assert {"rel": "vendor", "why": CK.WHY_FOLDER} in made["problems"]
    assert "vendor/shared/keep.txt" not in made["new"]  # never moved through the link
    assert _refused(home, repo).sentence == CK.PROBLEMS
    assert (outside / "keep.txt").read_bytes() == b"not this repository's file\n"
    assert (repo / "vendor" / "shared").exists()


def test_the_second_look_before_git_writes_stops_it_when_the_plan_missed_something(repo, home, tmp_path, monkeypatch):
    """Defence in depth: with the plan's own check switched off, the look at
    the folder right before git writes still refuses — and the outside folder
    a junction leads to is whole."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_bytes(b"outside\n")
    (repo / "vendor").write_bytes(b"one file\n")
    PN.record_turn_start(home, SID, str(repo), "t1")
    (repo / "vendor").unlink()
    (repo / "vendor").mkdir()
    _junction(repo / "vendor" / "shared", outside)
    (repo / "made.py").write_bytes(b"made\n")
    monkeypatch.setattr(CK, "_only_new_inside", lambda path, rel, new: True)
    said = _refused(home, repo)
    assert said.status == 409 and CK.WHY_IN_THE_WAY in said.sentence and "left as it was" in said.sentence
    assert (outside / "keep.txt").read_bytes() == b"outside\n"
    assert (repo / "made.py").read_bytes() == b"made\n"  # moved out, then brought back
    assert (repo / "vendor" / "shared").exists()


def test_a_cloned_repository_inside_that_folder_is_never_deleted(repo, home):
    (repo / "lib").write_bytes(b"one file\n")
    PN.record_turn_start(home, SID, str(repo), "t1")
    (repo / "lib").unlink()
    (repo / "lib" / "cloned").mkdir(parents=True)
    _git(repo / "lib" / "cloned", "init", "-q", "-b", "main")
    (repo / "lib" / "cloned" / "theirs.py").write_bytes(b"theirs\n")
    _git(repo / "lib" / "cloned", "add", "-A")
    _git(repo / "lib" / "cloned", "commit", "-q", "-m", "their work")
    made = CK.plan(home, SID, _row(repo), "t1")
    assert {"rel": "lib", "why": CK.WHY_FOLDER} in made["problems"]
    assert _refused(home, repo).sentence == CK.PROBLEMS
    assert (repo / "lib" / "cloned" / "theirs.py").read_bytes() == b"theirs\n"
    assert _git(repo / "lib" / "cloned", "log", "--oneline").count("their work") == 1


def test_a_file_that_stands_where_a_folder_must_go_is_never_deleted(repo, home):
    (repo / "cache").mkdir()
    (repo / "cache" / "x.txt").write_bytes(b"x\n")
    _git(repo, "add", "-A")
    PN.record_turn_start(home, SID, str(repo), "t1")
    shutil.rmtree(repo / "cache")
    (repo / "cache").write_bytes(b"a file git ignores now, in no checkpoint\n")
    (repo / ".gitignore").write_bytes(b"*.log\n/cache\n")
    as_is = _files(repo)
    made = CK.plan(home, SID, _row(repo), "t1")
    assert {"rel": "cache/x.txt", "why": CK.WHY_PARENT} in made["problems"]
    assert _refused(home, repo).sentence == CK.PROBLEMS
    assert _files(repo) == as_is


def test_a_folder_holding_only_new_files_still_goes_back_to_the_file_it_was(repo, home):
    """The plan's new check must not refuse the ordinary case."""
    (repo / "thing").write_bytes(b"a file\n")
    _git(repo, "add", "-A")
    start = _files(repo)
    PN.record_turn_start(home, SID, str(repo), "t1")
    (repo / "thing").unlink()
    (repo / "thing" / "deep").mkdir(parents=True)
    (repo / "thing" / "deep" / "n.py").write_bytes(b"n\n")
    got = CK.restore(home, SID, _row(repo), "t1")
    assert _files(repo) == start
    assert (Path(got["trash"]) / "made-since" / "thing" / "deep" / "n.py").read_bytes() == b"n\n"


# --------------------------------------------------------------------------- #
# a file git ignores now
# --------------------------------------------------------------------------- #


def test_a_file_git_ignores_now_is_left_alone_by_the_rewind_and_by_the_undo(repo, home):
    (repo / ".env").write_bytes(b"KEY=placeholder\n")  # untracked, in the checkpoint
    PN.record_turn_start(home, SID, str(repo), "t1")
    (repo / ".gitignore").write_bytes(b"*.log\n.env\n")  # now ignored: in no later tree
    (repo / ".env").write_bytes(b"KEY=the value the user typed\n")
    (repo / "a.py").write_bytes(b"a = 1\n")
    made = CK.plan(home, SID, _row(repo), "t1")
    assert {"rel": ".env", "why": CK.WHY_IGNORED} in made["left"]
    assert ".env" not in made["back"] and ".env" not in made["gone"]
    got = CK.restore(home, SID, _row(repo), "t1")
    assert (repo / ".env").read_bytes() == b"KEY=the value the user typed\n"
    assert (repo / "a.py").read_bytes() == b"a = 0\n" and "1 left as it is" in got["words"]
    # The undo puts the ignore rule back FIRST, so .env is ignored again and
    # stays exactly where it is (it is in no checkpoint).
    back = CK.undo(home, SID, _row(repo))
    assert (repo / ".env").read_bytes() == b"KEY=the value the user typed\n"
    assert (repo / "a.py").read_bytes() == b"a = 1\n" and b".env" in (repo / ".gitignore").read_bytes()
    assert back["stayed"] == 1 and back["moved"] == 0


def test_a_file_the_checkpoints_own_rules_ignore_is_not_moved_to_the_trash(repo, home):
    """The checkpoint ignored ``.env`` (so it never held it). The tool then
    un-ignored it: it shows as "made since", but nothing can say it was not
    there — it stays, and the result says so."""
    (repo / ".gitignore").write_bytes(b"*.log\n.env\n")
    (repo / ".env").write_bytes(b"KEY=mine\n")
    _git(repo, "add", "-A")
    PN.record_turn_start(home, SID, str(repo), "t1")
    (repo / ".gitignore").write_bytes(b"*.log\n")  # the tool removed the rule
    (repo / "made.py").write_bytes(b"made\n")
    made = CK.plan(home, SID, _row(repo), "t1")
    assert sorted(made["new"]) == [".env", "made.py"]  # by the rules of NOW it looks new
    got = CK.restore(home, SID, _row(repo), "t1")
    assert (repo / ".env").read_bytes() == b"KEY=mine\n"
    assert not (repo / "made.py").exists() and got["moved"] == 1 and got["stayed"] == 1
    assert "1 that git ignores again stayed where it is" in got["words"]
    assert not (Path(got["trash"]) / "made-since" / ".env").exists()


# --------------------------------------------------------------------------- #
# Undo
# --------------------------------------------------------------------------- #


def test_an_undo_keeps_what_it_writes_over_and_what_it_moves(repo, home):
    PN.record_turn_start(home, SID, str(repo), "t1")
    _work(repo)
    as_is = _files(repo)
    CK.restore(home, SID, _row(repo), "t1")
    (repo / "a.py").write_bytes(b"a = edited by hand after the rewind\n")
    (repo / "hand.txt").write_bytes(b"made by hand after the rewind\n")
    got = CK.undo(home, SID, _row(repo))
    assert _files(repo) == as_is
    trash = Path(got["trash"])
    assert (trash / "replaced" / "a.py").read_bytes() == b"a = edited by hand after the rewind\n"
    assert (trash / "made-since" / "hand.txt").read_bytes() == b"made by hand after the rewind\n"
    assert "kept in Iron Jarvis's trash" in got["words"]


def test_a_message_closes_the_undo_even_when_its_own_checkpoint_is_missing_or_later_dropped(repo, home):
    PN.record_turn_start(home, SID, str(repo), "t1")
    _work(repo)
    CK.restore(home, SID, _row(repo), "t1")
    assert CK.can_undo(home, SID)
    # A message starts and its checkpoint is NOT recorded (the folder could
    # not be read in time): only the send path's own close says so.
    CK.close_undo(home, SID)
    assert not CK.can_undo(home, SID)
    # Its row recorded, then dropped by a conversation rewind: still closed.
    PN.record_turn_start(home, SID, str(repo), "t2")
    CK.forget_turns(home, SID, str(repo), {"t1"})
    assert [r["turn"] for r in _rows(home, "start")] == ["t1"]
    assert not CK.can_undo(home, SID)
    after = _files(repo)
    with pytest.raises(CK.RestoreRefused) as said:
        CK.undo(home, SID, _row(repo))
    assert said.value.sentence == CK.UNDO_TOO_LATE and _files(repo) == after


def test_a_rewind_that_failed_and_rolled_back_does_not_hide_the_earlier_rewinds_undo(repo, home, monkeypatch):
    PN.record_turn_start(home, SID, str(repo), "t1")
    _work(repo)
    before_first = _files(repo)
    CK.restore(home, SID, _row(repo), "t1")
    after_first = _files(repo)
    (repo / "a.py").write_bytes(b"a = 9\n")
    real = CK._restore_paths
    monkeypatch.setattr(CK, "_restore_paths", lambda *a: types.SimpleNamespace(
        ok=False, err="fatal: no", timed_out=False))
    assert _refused(home, repo).status == 409
    monkeypatch.setattr(CK, "_restore_paths", real)
    assert (repo / "a.py").read_bytes() == b"a = 9\n"
    assert CK.can_undo(home, SID)  # the first rewind's
    (repo / "a.py").write_bytes(after_first["a.py"])
    CK.undo(home, SID, _row(repo))
    assert _files(repo) == before_first


def test_a_restored_file_keeps_its_line_endings_where_git_would_change_them(repo, home):
    """``core.autocrlf=true`` (git for Windows' default): git writes CRLF for
    a file that had LF. The file keeps the endings it had a moment ago, so it
    comes back byte for byte; the undo is exact too."""
    _git(repo, "config", "core.autocrlf", "true")
    (repo / "run.sh").write_bytes(b"#!/bin/sh\necho 0\n")
    start = _files(repo)
    PN.record_turn_start(home, SID, str(repo), "t1")
    (repo / "run.sh").write_bytes(b"#!/bin/sh\necho 1\n")
    (repo / "made.sh").write_bytes(b"#!/bin/sh\necho made\n")
    as_is = _files(repo)
    made = CK.plan(home, SID, _row(repo), "t1")
    assert "core.autocrlf is true" in made["endings"] and "line endings" in made["endings"]
    got = CK.restore(home, SID, _row(repo), "t1")
    assert _files(repo) == start  # LF, as it was (git alone writes CRLF here)
    assert (Path(got["trash"]) / "replaced" / "run.sh").read_bytes() == b"#!/bin/sh\necho 1\n"
    CK.undo(home, SID, _row(repo))
    assert _files(repo) == as_is  # byte for byte, LF and all


def test_a_windows_style_file_comes_back_windows_style_under_autocrlf_input(repo, home):
    """Measured live on the owner's PC (``core.autocrlf=input``): a CRLF
    ``run.bat`` the tool edited came back with LF."""
    _git(repo, "config", "core.autocrlf", "input")
    (repo / "run.bat").write_bytes(b"@echo off\r\necho hi\r\n")
    (repo / "unix.sh").write_bytes(b"echo hi\n")
    start = _files(repo)
    PN.record_turn_start(home, SID, str(repo), "t1")
    (repo / "run.bat").write_bytes(b"@echo off\r\necho bye\r\n")
    (repo / "unix.sh").write_bytes(b"echo bye\n")
    CK.restore(home, SID, _row(repo), "t1")
    assert _files(repo) == start
    assert (repo / "run.bat").read_bytes() == b"@echo off\r\necho hi\r\n"


def test_in_an_undo_the_exact_copy_wins_over_the_line_ending_rule(repo, home):
    """After the rewind the user saves the file with the OTHER kind of line
    endings. The undo must return the file exactly as it was before the
    rewind (the rewind's own copy), not in the style of that later save."""
    _git(repo, "config", "core.autocrlf", "true")
    (repo / "doc.txt").write_bytes(b"v1\r\n")
    PN.record_turn_start(home, SID, str(repo), "t1")
    (repo / "doc.txt").write_bytes(b"v2\r\n")
    CK.restore(home, SID, _row(repo), "t1")
    assert (repo / "doc.txt").read_bytes() == b"v1\r\n"
    (repo / "doc.txt").write_bytes(b"v1 saved by hand\n")  # LF now
    CK.undo(home, SID, _row(repo))
    assert (repo / "doc.txt").read_bytes() == b"v2\r\n"


def test_only_uniform_text_gets_its_line_endings_kept(repo, home):
    """Mixed endings and binary content are left exactly as git wrote them,
    and a file deleted since comes back with git's own (nothing to read the
    style from): what the preview's note says."""
    _git(repo, "config", "core.autocrlf", "true")
    (repo / "mixed.txt").write_bytes(b"one\ntwo\n")
    (repo / "gone.txt").write_bytes(b"gone\n")
    (repo / "blob.bin").write_bytes(b"\x00\x01\n\x02")
    PN.record_turn_start(home, SID, str(repo), "t1")
    (repo / "mixed.txt").write_bytes(b"one\r\nTWO\n")  # both kinds now
    (repo / "gone.txt").unlink()
    (repo / "blob.bin").write_bytes(b"\x00\x09\n\x02")
    CK.restore(home, SID, _row(repo), "t1")
    assert (repo / "mixed.txt").read_bytes() == b"one\r\ntwo\r\n"  # git's own
    assert (repo / "gone.txt").read_bytes() == b"gone\r\n"  # git's own
    assert (repo / "blob.bin").read_bytes() == b"\x00\x01\n\x02"
    assert CK._style(b"a\r\nb\r\n") == "crlf" and CK._style(b"a\nb\n") == "lf"
    assert CK._style(b"a\r\nb\n") == "" and CK._style(b"no line") == "" and CK._style(b"\0\n") == ""


def test_the_plan_says_nothing_about_line_endings_where_git_changes_none(repo, home):
    PN.record_turn_start(home, SID, str(repo), "t1")
    (repo / "a.py").write_bytes(b"a = 1\n")
    assert CK.plan(home, SID, _row(repo), "t1")["endings"] == ""
    (repo / ".gitattributes").write_bytes(b"*.txt text eol=crlf\n")
    assert ".gitattributes" in CK.plan(home, SID, _row(repo), "t1")["endings"]
    _git(repo, "config", "core.autocrlf", "input")
    assert "core.autocrlf is input" in CK.public(CK.plan(home, SID, _row(repo), "t1"))["endings"]


# --------------------------------------------------------------------------- #
# the user's own index and its lock
# --------------------------------------------------------------------------- #


def test_the_restore_goes_through_a_private_index_never_the_users_own(repo, home):
    PN.record_turn_start(home, SID, str(repo), "t1")
    _work(repo)
    _git(repo, "add", "a.py")  # the user's own staging
    index = (repo / ".git" / "index").read_bytes()
    # Another git holds the user's index lock for the whole restore (or one
    # stopped half way left it): the restore neither needs it nor removes it.
    lock = repo / ".git" / "index.lock"
    lock.write_bytes(b"held by another git")
    got = CK.restore(home, SID, _row(repo), "t1")
    assert got["ok"] and (repo / "a.py").read_bytes() == b"a = 0\n" and (repo / "b.py").exists()
    assert lock.read_bytes() == b"held by another git"
    assert (repo / ".git" / "index").read_bytes() == index
    lock.unlink()
    # Nothing of the private index is left behind (only the empty folder git
    # is pointed at for hooks, so the user's own hooks stay out of it).
    left = sorted(p.name for p in CK._index_dir(home).glob("*"))
    assert left == ["no-hooks"] and list((CK._index_dir(home) / "no-hooks").iterdir()) == []


def test_git_gets_time_to_write_and_a_git_that_ran_out_of_time_is_said(repo, home, monkeypatch):
    assert CK.RESTORE_S >= 120
    PN.record_turn_start(home, SID, str(repo), "t1")
    _work(repo)
    as_is = _files(repo)
    monkeypatch.setattr(CK, "_restore_paths", lambda *a: types.SimpleNamespace(ok=False, err="", timed_out=True))
    said = _refused(home, repo)
    assert CK.WHY_GIT_SLOW in said.sentence and said.status == 409 and _files(repo) == as_is


def test_a_failure_is_called_whole_only_when_the_folder_really_is(repo, home, monkeypatch):
    PN.record_turn_start(home, SID, str(repo), "t1")
    _work(repo)

    def breaks(folder, tree, rels, index_dir):
        (Path(folder) / "a.py").write_bytes(b"half written\n")
        return types.SimpleNamespace(ok=False, err="fatal: stopped", timed_out=False)

    monkeypatch.setattr(CK, "_restore_paths", breaks)
    monkeypatch.setattr(CK, "_roll_back", lambda *a: True)  # a roll-back that claims success and did nothing
    said = _refused(home, repo)
    assert said.status == 500 and "could not be returned" in said.sentence
    assert "code-rewind" in said.sentence  # where the copies are
    assert (list((home / "trash").glob("*/code-rewind/" + SID + "/replaced/a.py"))[0]).read_bytes() == b"a = 1\n# more\n"
    assert CK.can_undo(home, SID)  # the way back stays offered


# --------------------------------------------------------------------------- #
# the trash
# --------------------------------------------------------------------------- #


def test_a_move_that_fails_part_way_brings_back_what_already_moved(repo, home, monkeypatch):
    PN.record_turn_start(home, SID, str(repo), "t1")
    _work(repo)
    as_is = _files(repo)
    real = shutil.move
    seen: list[str] = []

    def second_fails(src, dst, *a, **k):
        if str(home) in str(dst):  # a move INTO the trash
            seen.append(str(src))
            if len(seen) == 2:
                raise PermissionError("the trash could not take it")
        return real(src, dst, *a, **k)

    monkeypatch.setattr(CK.shutil, "move", second_fails)
    said = _refused(home, repo)
    assert said.status == 409 and CK.WHY_MOVE in said.sentence and len(seen) == 2
    assert _files(repo) == as_is
    assert [p for p in (home / "trash").rglob("*") if p.is_file()] == []


def test_a_trash_folder_that_cannot_be_made_strands_nothing(repo, home, monkeypatch):
    PN.record_turn_start(home, SID, str(repo), "t1")
    _work(repo)
    as_is = _files(repo)
    real = Path.mkdir
    made: list[str] = []

    def second_fails(self, *a, **k):
        if "made-since" in self.parts:
            made.append(str(self))
            if len(made) == 2:
                raise OSError("no room")
        return real(self, *a, **k)

    monkeypatch.setattr(Path, "mkdir", second_fails)
    said = _refused(home, repo)
    assert said.status == 409 and len(made) == 2
    assert _files(repo) == as_is  # the first file is back out of the trash


def test_no_copy_of_a_file_means_no_restore(repo, home, monkeypatch):
    PN.record_turn_start(home, SID, str(repo), "t1")
    _work(repo)
    as_is = _files(repo)
    monkeypatch.setattr(CK.shutil, "copy2", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    called: list[int] = []
    real = CK._restore_paths
    monkeypatch.setattr(CK, "_restore_paths", lambda *a: called.append(1) or real(*a))
    said = _refused(home, repo)
    assert CK.WHY_NO_COPY in said.sentence and said.status == 409
    assert called == [] and _files(repo) == as_is  # git never ran


def test_the_way_back_never_writes_over_something_that_now_stands_there(repo, home):
    root = home / "trash" / "x"
    kept = root / "made-since" / "n.txt"
    kept.parent.mkdir(parents=True)
    kept.write_bytes(b"the moved file\n")
    (repo / "n.txt").write_bytes(b"something else stands here now\n")
    assert CK._roll_back(str(repo), root, [], [], [("n.txt", str(kept))]) is False
    assert (repo / "n.txt").read_bytes() == b"something else stands here now\n"
    assert kept.read_bytes() == b"the moved file\n"


def test_emptied_folders_go_but_never_the_sessions_own_folder(repo, home):
    work = repo / "empty-at-first"
    work.mkdir()
    PN.record_turn_start(home, SID, str(work), "t1")
    (work / "deep").mkdir()
    (work / "deep" / "only.py").write_bytes(b"only\n")
    CK.restore(home, SID, _row(work), "t1")
    assert work.is_dir() and list(work.iterdir()) == []


# --------------------------------------------------------------------------- #
# what is left as it is
# --------------------------------------------------------------------------- #


def test_a_nested_repository_that_moved_on_is_left_as_it_is(repo, home):
    nested = repo / "dep"
    nested.mkdir()
    _git(nested, "init", "-q", "-b", "main")
    (nested / "d.py").write_bytes(b"d = 0\n")
    _git(nested, "add", "-A")
    _git(nested, "commit", "-q", "-m", "one")
    PN.record_turn_start(home, SID, str(repo), "t1")
    (nested / "d.py").write_bytes(b"d = 1\n")
    _git(nested, "commit", "-q", "-a", "-m", "two")
    (repo / "a.py").write_bytes(b"a = 1\n")
    made = CK.plan(home, SID, _row(repo), "t1")
    assert made["left"] == [{"rel": "dep", "why": CK.WHY_SUBMODULE}] and made["back"] == ["a.py"]
    got = CK.restore(home, SID, _row(repo), "t1")
    assert (nested / "d.py").read_bytes() == b"d = 1\n"
    assert _git(nested, "log", "--oneline").count("two") == 1
    assert "it is a repository of its own" in got["words"]


def test_a_link_is_left_as_it_is(repo, home, tmp_path):
    target = tmp_path / "target.txt"
    target.write_bytes(b"target\n")
    try:
        os.symlink(target, repo / "link.txt")
    except (OSError, NotImplementedError):
        pytest.skip("this account cannot make a symbolic link")
    PN.record_turn_start(home, SID, str(repo), "t1")
    (repo / "link.txt").unlink()
    (repo / "link.txt").write_bytes(b"a plain file now\n")
    made = CK.plan(home, SID, _row(repo), "t1")
    assert {"rel": "link.txt", "why": CK.WHY_LINK} in made["left"] and "link.txt" not in made["back"]
    CK.restore(home, SID, _row(repo), "t1")
    assert (repo / "link.txt").read_bytes() == b"a plain file now\n" and target.read_bytes() == b"target\n"


def test_files_reached_only_through_a_link_are_left_where_they_are(repo, home, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    PN.record_turn_start(home, SID, str(repo), "t1")
    _junction(repo / "linked", outside)
    (outside / "f.txt").write_bytes(b"outside\n")
    (repo / "a.py").write_bytes(b"a = 1\n")
    made = CK.plan(home, SID, _row(repo), "t1")
    through = [x for x in made["left"] if x["rel"].startswith("linked")]
    assert through and all(x["why"] in (CK.WHY_PATH, CK.WHY_LINK) for x in through)
    assert not [n for n in made["new"] if n.startswith("linked")]
    CK.restore(home, SID, _row(repo), "t1")
    assert (outside / "f.txt").read_bytes() == b"outside\n" and (repo / "a.py").read_bytes() == b"a = 0\n"


@pytest.mark.skipif(sys.platform != "win32", reason="a file held open without sharing is a Windows state")
def test_a_file_another_program_holds_open_is_seen_for_real(repo, home):
    import ctypes
    from ctypes import wintypes

    PN.record_turn_start(home, SID, str(repo), "t1")
    (repo / "a.py").write_bytes(b"a = 1\n")
    create = ctypes.windll.kernel32.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    # Open for reading and let others READ it too (git can), but not write.
    handle = create(str(repo / "a.py"), 0x80000000, 0x1, None, 3, 0x80, None)
    assert handle and handle != wintypes.HANDLE(-1).value
    try:
        assert CK._writable(str(repo / "a.py")) is False
        made = CK.plan(home, SID, _row(repo), "t1")
        assert made["problems"] == [{"rel": "a.py", "why": CK.WHY_LOCKED}]
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)
    assert CK._writable(str(repo / "a.py")) is True


def test_a_checkpoint_of_the_folder_that_did_not_stick_stops_the_restore(repo, home, monkeypatch):
    PN.record_turn_start(home, SID, str(repo), "t1")
    _work(repo)
    as_is = _files(repo)
    now = CK.plan(home, SID, _row(repo), "t1")["now"]
    real = CK.tree_kept
    monkeypatch.setattr(CK, "tree_kept", lambda folder, tree: False if tree == now else real(folder, tree))
    assert _refused(home, repo).sentence == CK.NOT_KEPT
    assert _files(repo) == as_is and _rows(home, CK.HOW_BEFORE) == []


# --------------------------------------------------------------------------- #
# refs when the folder is gone
# --------------------------------------------------------------------------- #


def test_a_session_whose_worktree_is_already_gone_leaves_no_ref_in_the_repository(repo, home, tmp_path):
    wt = tmp_path / "wt"
    _git(repo, "worktree", "add", "-q", "-b", "side", str(wt))
    (wt / "big.txt").write_bytes(b"untracked work\n")
    PN.record_turn_start(home, SID, str(wt), "t1")
    row = _rows(home, "start")[0]
    assert os.path.normcase(row["repo"]) == os.path.normcase(str(repo / ".git"))
    assert len(_refs(repo)) == 1
    _git(repo, "worktree", "remove", "--force", str(wt))
    assert not wt.exists()
    assert CK.forget_session(home, SID, str(wt)) == 1
    assert _refs(repo) == []


def test_a_row_that_falls_out_after_its_folder_is_gone_still_loses_its_ref(repo, home, tmp_path, monkeypatch):
    wt = tmp_path / "wt"
    _git(repo, "worktree", "add", "-q", "-b", "side", str(wt))
    monkeypatch.setattr(CK, "MAX_STARTS", 1)
    PN.record_turn_start(home, SID, str(wt), "t1")
    first = _rows(home, "start")[0]
    _git(repo, "worktree", "remove", "--force", str(wt))
    CK.drop_rows(str(wt), [first])
    assert _refs(repo) == []


def test_a_name_that_changed_only_in_upper_or_lower_case_is_left_and_never_moved(repo, home, monkeypatch):
    """Where the file system ignores case, the old name "is on disk" (it is
    the same file as the new one). Reading that as "git ignores it now" left
    the old name alone and MOVED the file itself to the trash under its new
    name. Both are left as they are."""
    (repo / "Readme.md").write_bytes(b"read me\n")
    if not (repo / "README.MD").exists():
        pytest.skip("this file system tells upper and lower case apart")
    _git(repo, "add", "-A")
    PN.record_turn_start(home, SID, str(repo), "t1")
    os.rename(repo / "Readme.md", repo / "tmp.md")
    os.rename(repo / "tmp.md", repo / "README.md")
    (repo / "a.py").write_bytes(b"a = 1\n")
    real = CK._raw

    def as_git_tells_it_where_case_counts(folder, a, b):
        rows = [r for r in real(folder, a, b) if r[3].lower() != "readme.md"]
        return [*rows, ("D", "100644", "000000", "Readme.md"), ("A", "000000", "100644", "README.md")]

    monkeypatch.setattr(CK, "_raw", as_git_tells_it_where_case_counts)
    made = CK.plan(home, SID, _row(repo), "t1")
    assert {"rel": "Readme.md", "why": CK.WHY_CASE} in made["left"]
    assert "README.md" not in made["new"] and "Readme.md" not in made["back"]
    CK.restore(home, SID, _row(repo), "t1")
    assert (repo / "README.md").read_bytes() == b"read me\n" and (repo / "a.py").read_bytes() == b"a = 0\n"
    assert not list((home / "trash").rglob("README.md"))


# --------------------------------------------------------------------------- #
# the second review
# --------------------------------------------------------------------------- #


def _events(turn: str = "t1") -> list[dict]:
    return [{"type": "user", "turn": turn, "at": "2026-11-19T10:00:00+00:00"}]


def test_the_per_file_revert_never_deletes_a_file_that_stands_where_the_folder_must_go(repo, home):
    """Shipped since v1.420.0: reverting ``sub/s.py`` when ``sub`` is now a
    FILE let git delete that file to make the folder, with no copy."""
    PN.record_turn_start(home, SID, str(repo), "t1")
    shutil.rmtree(repo / "sub")
    (repo / "sub").write_bytes(b"the user's own file, in no checkpoint\n")
    as_is = _files(repo)
    with pytest.raises(PN.RevertRefused) as said:
        PN.revert_file(home, SID, _row(repo), _events(), "Codex", rel="sub/s.py", blob="", scope="turn", turn="t1")
    assert "stands where it must go" in said.value.sentence and "s.py" in said.value.sentence
    assert _files(repo) == as_is and not (home / "trash").exists()
    # The ordinary revert of a deleted file still works.
    (repo / "a.py").unlink()
    got = PN.revert_file(home, SID, _row(repo), _events(), "Codex", rel="a.py", blob="", scope="turn", turn="t1")
    assert got["ok"] and (repo / "a.py").read_bytes() == b"a = 0\n"


def test_line_endings_are_left_alone_where_the_checkpoint_is_exact(repo, home):
    """``core.autocrlf=false``: the checkpoint holds the file's own bytes.
    The tool flipped the endings; the rewind must return the checkpoint's,
    not re-apply the tool's (review: it did, and never converged)."""
    (repo / "req.http").write_bytes(b"GET /\r\nHost: x\r\n")
    (repo / "unix.txt").write_bytes(b"one\ntwo\n")
    start = _files(repo)
    tree = PN.record_turn_start(home, SID, str(repo), "t1")
    (repo / "req.http").write_bytes(b"GET /\nHost: y\n")  # flipped to LF
    (repo / "unix.txt").write_bytes(b"one\r\nTWO\r\n")  # flipped to CRLF
    CK.restore(home, SID, _row(repo), "t1")
    assert _files(repo) == start
    assert PN.snapshot_tree(str(repo)) == tree and CK.plan(home, SID, _row(repo), "t1")["back"] == []


def test_a_rule_that_names_the_line_endings_or_says_not_text_is_never_overridden(repo, home):
    _git(repo, "config", "core.autocrlf", "true")
    (repo / ".gitattributes").write_bytes(b"*.sh text eol=lf\n*.bat text eol=crlf\n*.csv -text\n*.dat binary\n")
    files = {"run.sh": b"echo 0\n", "run.bat": b"echo 0\r\n", "t.csv": b"a,b\r\n1,2\r\n", "d.dat": b"x\r\ny\r\n"}
    for name, data in files.items():
        (repo / name).write_bytes(data)
    _git(repo, "add", "-A")
    tree = PN.record_turn_start(home, SID, str(repo), "t1")
    (repo / "run.sh").write_bytes(b"echo 1\r\n")  # every one flipped by the tool
    (repo / "run.bat").write_bytes(b"echo 1\n")
    (repo / "t.csv").write_bytes(b"a,b\n9,9\n")
    (repo / "d.dat").write_bytes(b"x\nz\n")
    CK.restore(home, SID, _row(repo), "t1")
    for name, data in files.items():
        assert (repo / name).read_bytes() == data, name
    assert PN.snapshot_tree(str(repo)) == tree


def test_a_binary_file_with_no_nul_byte_keeps_every_byte(repo, home):
    """Under ``core.autocrlf=true`` too: git keeps a blob that holds CRLF as
    it is, so the checkpoint is exact and nothing is rewritten."""
    for setting in ("false", "true"):
        _git(repo, "config", "core.autocrlf", setting)
        (repo / "key.bin").write_bytes(b"\x89P\r\n\x1a\xff\x01\x02")
        _git(repo, "add", "key.bin")
        turn = "bin-" + setting
        PN.record_turn_start(home, SID, str(repo), turn)
        (repo / "key.bin").write_bytes(b"\x89P\n\x1a\xff\x09\x02")
        CK.restore(home, SID, _row(repo), turn)
        assert (repo / "key.bin").read_bytes() == b"\x89P\r\n\x1a\xff\x01\x02", setting


def test_line_endings_are_kept_in_a_folder_inside_the_repository_too(repo, home):
    _git(repo, "config", "core.autocrlf", "input")
    (repo / "sub" / "run.bat").write_bytes(b"@echo off\r\necho hi\r\n")
    PN.record_turn_start(home, SID, str(repo / "sub"), "t1")
    (repo / "sub" / "run.bat").write_bytes(b"@echo off\r\necho bye\r\n")
    CK.restore(home, SID, _row(repo / "sub"), "t1")
    assert (repo / "sub" / "run.bat").read_bytes() == b"@echo off\r\necho hi\r\n"


@pytest.mark.skipif(sys.platform != "win32" and not hasattr(os.stat_result, "st_birthtime"),
                    reason="this system does not say when a file was made")
def test_a_file_that_was_there_before_the_message_is_never_called_made_since(repo, home):
    """The rule that ignored it lived OUTSIDE the session folder's own
    .gitignore files (the repository's root .gitignore, .git/info/exclude)
    and was removed since: the two-phase check cannot see that. The file was
    MADE before the checkpoint, so the checkpoint ignored it. Real birth
    times here: the checkpoint is said to be a minute from now, as if the
    files were a minute older."""
    import datetime as dt
    import time

    (repo / ".gitignore").write_bytes(b"*.log\n*.env\n")
    (repo / ".git" / "info" / "exclude").write_bytes(b"local.cfg\n")
    (repo / "sub" / ".env").write_bytes(b"KEY=mine\n")
    (repo / "sub" / "local.cfg").write_bytes(b"mine too\n")
    born = CK._born(os.lstat(repo / "sub" / ".env"))
    assert born is not None and abs(born - time.time()) < 120  # this system does say
    tree = PN.record_turn_start(home, SID, str(repo / "sub"), "t1")
    (repo / ".gitignore").write_bytes(b"*.log\n")  # both rules removed since
    (repo / ".git" / "info" / "exclude").write_bytes(b"")
    later = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=60)
    made = CK._plan(home, SID, str(repo / "sub"), tree, since=later)
    left = {x["rel"]: x["why"] for x in made["left"]}
    assert left == {".env": CK.WHY_OLDER, "local.cfg": CK.WHY_OLDER} and made["new"] == []
    # With the checkpoint's real time (the files are NEWER than it by the
    # clock here? no: they were made before it) the same answer comes out of
    # the public plan.
    again = CK.plan(home, SID, _row(repo / "sub"), "t1")
    assert {x["rel"] for x in again["left"]} | set(again["new"]) == {".env", "local.cfg"}


def test_made_before_the_message_is_read_from_the_files_own_birth(repo, home, monkeypatch):
    """The rule itself, with the birth times given: older than the checkpoint
    is left, newer is "made since", and a file that stands in a restored
    file's way is never left there by this rule."""
    import datetime as dt

    PN.record_turn_start(home, SID, str(repo), "t1")
    row = _rows(home, "start")[0]
    at = PN._at(row["at"])
    (repo / "old.env").write_bytes(b"mine\n")
    (repo / "new.py").write_bytes(b"made\n")
    (repo / "a.py").write_bytes(b"a = 1\n")
    births = {"old.env": at.timestamp() - 3600, "new.py": at.timestamp() + 5}
    monkeypatch.setattr(CK, "_born", lambda st: births.get(getattr(st, "_name", ""), None))
    real = CK._lstat

    def named(path):
        st = real(path)
        if st is None:
            return None
        box = types.SimpleNamespace(st_mode=st.st_mode, st_size=st.st_size, _name=os.path.basename(path))
        return box

    monkeypatch.setattr(CK, "_lstat", named)
    monkeypatch.setattr(CK, "_link", lambda path, st: False)
    made = CK._plan(home, SID, str(repo), row["tree"], since=at)
    assert made["new"] == ["new.py"] and {"rel": "old.env", "why": CK.WHY_OLDER} in made["left"]
    assert CK._plan(home, SID, str(repo), row["tree"])["new"] == ["new.py", "old.env"]  # no time given: no rule
    assert isinstance(at, dt.datetime)


def test_a_rewind_that_changed_nothing_leaves_nothing_to_undo(repo, home):
    PN.record_turn_start(home, SID, str(repo), "t1")
    got = CK.restore(home, SID, _row(repo), "t1")
    assert got["words"].startswith(CK.DONE_NOTHING)
    assert not CK.can_undo(home, SID)
    (repo / "a.py").write_bytes(b"a = the user's own later work\n")
    with pytest.raises(CK.RestoreRefused) as said:
        CK.undo(home, SID, _row(repo))
    assert said.value.sentence == CK.NOTHING_TO_UNDO
    assert (repo / "a.py").read_bytes() == b"a = the user's own later work\n"


def test_attributes_go_back_before_the_files_they_govern(repo, home):
    """The tool removed an eol line from ``.gitattributes``. A file whose
    name sorts BEFORE it was written under the tool's rules."""
    (repo / ".gitattributes").write_bytes(b"*.cfg text eol=crlf\n")
    (repo / "-early.cfg").write_bytes(b"one\r\n")
    _git(repo, "add", "-A")
    start = _files(repo)
    PN.record_turn_start(home, SID, str(repo), "t1")
    (repo / ".gitattributes").write_bytes(b"")
    (repo / "-early.cfg").write_bytes(b"two\n")
    CK.restore(home, SID, _row(repo), "t1")
    assert _files(repo) == start
    assert CK._is_rules("x/.gitattributes") and CK._is_rules(".gitignore") and not CK._is_rules("a.py")


def test_files_git_is_told_not_to_watch_are_said_and_not_touched(repo, home):
    _git(repo, "update-index", "--assume-unchanged", "a.py")
    _git(repo, "update-index", "--skip-worktree", "b.py")
    PN.record_turn_start(home, SID, str(repo), "t1")
    (repo / "a.py").write_bytes(b"a = changed, unseen by git\n")
    (repo / "b.py").write_bytes(b"b = changed, unseen by git\n")
    (repo / "sub" / "s.py").write_bytes(b"s = 1\n")
    made = CK.plan(home, SID, _row(repo), "t1")
    left = {x["rel"]: x["why"] for x in made["left"]}
    assert left == {"a.py": CK.WHY_UNWATCHED, "b.py": CK.WHY_UNWATCHED} and made["back"] == ["sub/s.py"]
    got = CK.restore(home, SID, _row(repo), "t1")
    assert (repo / "a.py").read_bytes() == b"a = changed, unseen by git\n" and "2 left as they are" in got["words"]


def test_what_the_preview_allows_the_press_does(repo, home):
    """Four states where the plan said yes and the press then refused."""
    # 1. an EMPTY folder (and one holding only empty folders) where a file was
    (repo / "thing").write_bytes(b"a file\n")
    (repo / "other").write_bytes(b"another\n")
    _git(repo, "add", "-A")
    start = _files(repo)
    PN.record_turn_start(home, SID, str(repo), "t1")
    (repo / "thing").unlink()
    (repo / "thing").mkdir()
    (repo / "other").unlink()
    (repo / "other" / "empty" / "deeper").mkdir(parents=True)
    (repo / "other" / "new.py").write_bytes(b"new\n")
    assert CK.plan(home, SID, _row(repo), "t1")["can"] is True
    CK.restore(home, SID, _row(repo), "t1")
    assert _files(repo) == start
    # 2. a folder that held a .gitignore is now a file
    (repo / "conf").mkdir()
    (repo / "conf" / ".gitignore").write_bytes(b"*.tmp\n")
    (repo / "conf" / "c.ini").write_bytes(b"c\n")
    _git(repo, "add", "-A")
    start = _files(repo)
    PN.record_turn_start(home, SID, str(repo), "t2")
    shutil.rmtree(repo / "conf")
    (repo / "conf").write_bytes(b"now a file\n")
    assert CK.plan(home, SID, _row(repo), "t2")["can"] is True
    got = CK.restore(home, SID, _row(repo), "t2")
    assert _files(repo) == start
    assert (Path(got["trash"]) / "made-since" / "conf").read_bytes() == b"now a file\n"
    # 3. a file the checkpoint's rules ignore sits inside a folder that must
    #    become a file again: it cannot stay there, so it moves to the trash
    (repo / ".gitignore").write_bytes(b"*.log\n*.keep\n")
    (repo / "slot").write_bytes(b"a file\n")
    _git(repo, "add", "-A")
    start = _files(repo)
    PN.record_turn_start(home, SID, str(repo), "t3")
    (repo / ".gitignore").write_bytes(b"*.log\n")
    (repo / "slot").unlink()
    (repo / "slot").mkdir()
    (repo / "slot" / "x.keep").write_bytes(b"kept\n")
    assert CK.plan(home, SID, _row(repo), "t3")["can"] is True
    got = CK.restore(home, SID, _row(repo), "t3")
    assert _files(repo) == start and got["stayed"] == 0
    assert (Path(got["trash"]) / "made-since" / "slot" / "x.keep").read_bytes() == b"kept\n"


def test_an_empty_folder_is_only_ever_removed_when_it_holds_nothing_at_all(repo, home):
    (repo / "d" / "empty").mkdir(parents=True)
    (repo / "e" / "full").mkdir(parents=True)
    (repo / "e" / "full" / "f.txt").write_bytes(b"f\n")
    CK._drop_empty_folders(str(repo), ["d", "e", "a.py", "nope"])
    assert not (repo / "d").exists()
    assert (repo / "e" / "full" / "f.txt").read_bytes() == b"f\n" and (repo / "a.py").exists()


def test_the_users_own_hooks_do_not_run_inside_the_restore(repo, home):
    hooks = repo / ".git" / "hooks"
    hooks.mkdir(exist_ok=True)
    mark = repo.parent / "hook-ran.txt"
    (hooks / "post-checkout").write_bytes(
        b"#!/bin/sh\necho ran > '" + str(mark).replace("\\", "/").encode() + b"'\n")
    os.chmod(hooks / "post-checkout", 0o755)
    PN.record_turn_start(home, SID, str(repo), "t1")
    (repo / "a.py").write_bytes(b"a = 1\n")
    CK.restore(home, SID, _row(repo), "t1")
    assert (repo / "a.py").read_bytes() == b"a = 0\n" and not mark.exists()


def test_a_file_that_cannot_be_read_is_never_written_over_blind(repo, home, monkeypatch):
    PN.record_turn_start(home, SID, str(repo), "t1")
    (repo / "a.py").write_bytes(b"a = 1\n")
    (repo / "sub" / "s.py").write_bytes(b"s = 1\n")
    as_is = _files(repo)
    real = CK._lstat
    armed = {"on": False}

    def blind(path):
        return None if armed["on"] and str(path).endswith("a.py") else real(path)

    real_copy = CK._copy_replaced

    def copy(*a, **k):
        armed["on"] = True  # the file cannot be stat'ed from here on
        try:
            return real_copy(*a, **k)
        finally:
            armed["on"] = False

    monkeypatch.setattr(CK, "_lstat", blind)
    monkeypatch.setattr(CK, "_copy_replaced", copy)
    said = _refused(home, repo)
    assert CK.WHY_NO_COPY in said.sentence and said.status == 409 and _files(repo) == as_is


def test_the_second_look_refuses_each_thing_it_is_there_for(repo, home, tmp_path):
    outside = tmp_path / "empty-outside"
    outside.mkdir()
    # a file that appeared where a "gone" file must be made
    CK._still_clear(str(repo), ["a.py"], set())  # an ordinary file to write over: fine
    with pytest.raises(CK._Stopped):
        CK._still_clear(str(repo), ["a.py"], {"a.py"})
    # a FILE where the path's own folder must be
    with pytest.raises(CK._Stopped):
        CK._still_clear(str(repo), ["a.py/inner.txt"], set())
    # a link where the path's own folder must be
    _junction(repo / "jump", outside)
    with pytest.raises(CK._Stopped):
        CK._still_clear(str(repo), ["jump/x.txt"], set())
    # A folder that holds only a junction to an EMPTY outside folder is not
    # "only new files": the link is never walked into.
    (repo / "box").mkdir()
    _junction(repo / "box" / "out", outside)
    assert CK._only_new_inside(str(repo / "box"), "box", set()) is False


def test_a_made_since_file_another_program_holds_stops_the_restore_before_anything_moves(repo, home, monkeypatch):
    PN.record_turn_start(home, SID, str(repo), "t1")
    _work(repo)
    as_is = _files(repo)
    real = CK._writable
    monkeypatch.setattr(CK, "_writable", lambda p: False if p.endswith("new.py") else real(p))
    made = CK.plan(home, SID, _row(repo), "t1")
    assert made["problems"] == [{"rel": "new.py", "why": CK.WHY_LOCKED}] and "new.py" not in made["new"]
    assert _refused(home, repo).sentence == CK.PROBLEMS and _files(repo) == as_is


def test_a_file_the_users_index_tracks_still_stays_when_the_rules_ignore_it(repo, home):
    """The question is the RULES alone (``--no-index``): a file the user
    staged since is otherwise never "ignored" to git, and it was moved."""
    (repo / ".gitignore").write_bytes(b"*.log\n*.env\n")
    (repo / ".env").write_bytes(b"KEY=mine\n")
    _git(repo, "add", "-A")
    PN.record_turn_start(home, SID, str(repo), "t1")
    (repo / ".gitignore").write_bytes(b"*.log\n")
    _git(repo, "add", "-f", ".env")  # staged since, by the tool
    assert CK._ignored_now(str(repo), [".env"]) == set()  # by the rules of now: not ignored
    got = CK.restore(home, SID, _row(repo), "t1")
    assert (repo / ".env").read_bytes() == b"KEY=mine\n" and got["stayed"] == 1 and got["moved"] == 0
    assert not list((home / "trash").rglob(".env"))


def test_the_way_back_clears_the_folders_it_emptied(repo, home):
    root = home / "trash" / "y"
    (repo / "deep" / "made").mkdir(parents=True)
    (repo / "deep" / "made" / "again.txt").write_bytes(b"git made this again\n")
    assert CK._roll_back(str(repo), root, [], ["deep/made/again.txt"], []) is True
    assert not (repo / "deep").exists()
    assert (root / "rolled-back" / "deep" / "made" / "again.txt").read_bytes() == b"git made this again\n"
