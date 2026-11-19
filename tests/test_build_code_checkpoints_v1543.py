"""v1.543.0 — Build › Code: checkpoints that LAST, and the whole folder put back
(``hosted/checkpoints.py``; the recording side in ``hosted/panes.py``).

Real git in a scratch repository every time (never a stub of git): the claims
here are about what git itself keeps, shows and writes.

* a message's checkpoint is kept by a hidden ref that points at a TREE: it
  survives ``git gc --prune=now`` and is in no log, branch or tag list;
* a deleted session leaves no ``refs/ironjarvis/`` name; a row that falls out
  of the record loses its ref; idle reads cannot push a message's checkpoint
  out;
* a restore returns every file byte for byte, MOVES the files made since to
  the trash, leaves the staging area, the branch and the commits alone, and
  changes nothing at all when one file stands in the way;
* Undo returns the folder byte for byte, until the next message;
* a checkpoint git no longer holds is said, never read as "that file was not
  there" (a tracked file was refused as staged, an untracked one was moved).
"""

from __future__ import annotations

import json
import os
import subprocess
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from iron_jarvis.hosted import checkpoints as CK
from iron_jarvis.hosted import panes as PN

GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid"]
NOW = datetime.now(timezone.utc)
SID = "app_s1"


def _git(cwd: Path, *args: str, when: str | None = None) -> str:
    env = dict(os.environ)
    if when:
        env["GIT_COMMITTER_DATE"] = env["GIT_AUTHOR_DATE"] = when
    return subprocess.run([*GIT, *args], cwd=cwd, check=True, capture_output=True, env=env).stdout.decode()


def _row(folder: Path | str) -> types.SimpleNamespace:
    return types.SimpleNamespace(folder=str(folder), created_at=NOW - timedelta(hours=1), harness="codex")


@pytest.fixture()
def repo(tmp_path) -> Path:
    folder = tmp_path / "repo"
    folder.mkdir()
    _git(folder, "init", "-q", "-b", "main")
    # The product restores through the REPO's own git config, and GitHub's
    # Windows runners set a GLOBAL core.autocrlf=true: the byte pins below
    # need the repository's own setting (the autocrlf test sets its own).
    _git(folder, "config", "core.autocrlf", "false")
    (folder / "a.py").write_bytes(b"a = 0\n")
    (folder / "b.py").write_bytes(b"b = 0\n")
    (folder / "sub").mkdir()
    (folder / "sub" / "s.py").write_bytes(b"s = 0\n")
    (folder / ".gitignore").write_bytes(b"*.log\n")
    _git(folder, "add", "-A")
    # Older than the session (``_row``): the session's own base is this commit.
    _git(folder, "commit", "-q", "-m", "first", when="2020-01-01T00:00:00+00:00")
    return folder


@pytest.fixture()
def home(tmp_path) -> Path:
    return tmp_path / "home"


def _files(folder: Path) -> dict[str, bytes]:
    """Every file under *folder* outside ``.git`` (path -> bytes)."""
    out: dict[str, bytes] = {}
    for p in sorted(folder.rglob("*")):
        rel = p.relative_to(folder).as_posix()
        if p.is_file() and not rel.startswith(".git/"):
            out[rel] = p.read_bytes()
    return out


def _refs(folder: Path) -> list[str]:
    return [ln for ln in _git(folder, "for-each-ref", "--format=%(refname)", "refs/ironjarvis/").splitlines() if ln]


def _rows(home: Path, how: str | None = None) -> list[dict]:
    return [r for r in PN.read_trees(home, SID) if how is None or r.get("how") == how]


def _work(repo: Path) -> None:
    """What a coding tool does in one message: change, delete, add, nest."""
    (repo / "a.py").write_bytes(b"a = 1\n# more\n")
    (repo / "b.py").unlink()
    (repo / "new.py").write_bytes(b"new = 1\n")
    (repo / "deep" / "er").mkdir(parents=True)
    (repo / "deep" / "er" / "n.txt").write_bytes(b"n\n")
    (repo / "run.log").write_bytes(b"ignored\n")


# --------------------------------------------------------------------------- #
# a checkpoint lasts, and is hidden
# --------------------------------------------------------------------------- #


def test_a_message_checkpoint_is_kept_by_a_hidden_ref_that_points_at_a_tree(repo, home):
    before = {k: _git(repo, *k.split()) for k in ("log --all --oneline", "branch -a", "tag")}
    tree = PN.record_turn_start(home, SID, str(repo), "t1", uid="2026-11-19T10:00:00Z")
    row = _rows(home, "start")[0]
    assert row["tree"] == tree and row["turn"] == "t1" and row["uid"] == "2026-11-19T10:00:00Z"
    assert row["head"] == _git(repo, "rev-parse", "HEAD").strip()
    ref = row["ref"]
    assert ref.startswith(f"refs/ironjarvis/checkpoints/{SID}/start-") and _refs(repo) == [ref]
    # It names the TREE itself: no commit was made for it.
    assert _git(repo, "cat-file", "-t", ref).strip() == "tree"
    assert _git(repo, "rev-parse", ref).strip() == tree
    # Nothing the user looks at shows it.
    assert {k: _git(repo, *k.split()) for k in before} == before
    assert _git(repo, "status", "--porcelain") == ""


def test_a_checkpoint_survives_gits_own_clean_up_and_an_unkept_tree_does_not(repo, home):
    (repo / "a.py").write_bytes(b"a = 7\n")  # a state no commit holds
    kept = PN.record_turn_start(home, SID, str(repo), "t1")
    (repo / "a.py").write_bytes(b"a = 8\n")
    loose = PN.snapshot_tree(str(repo))  # the same kind of tree, with no ref
    assert CK.tree_kept(str(repo), kept) and CK.tree_kept(str(repo), loose)
    _git(repo, "gc", "--prune=now", "-q")
    assert CK.tree_kept(str(repo), kept)
    assert not CK.tree_kept(str(repo), loose)  # the control: this is what used to happen
    assert _git(repo, "cat-file", "-p", f"{kept}:a.py") == "a = 7\n"


def test_a_deleted_sessions_refs_go_and_another_sessions_stay(repo, home):
    PN.record_turn_start(home, SID, str(repo), "t1")
    PN.record_turn_start(home, SID, str(repo), "t2")
    PN.record_turn_start(home, "app_other", str(repo), "t1")
    assert len(_refs(repo)) == 3
    assert CK.forget_session(home, SID, str(repo)) == 2
    left = _refs(repo)
    assert len(left) == 1 and "/app_other/" in left[0]


def test_idle_reads_never_push_a_message_checkpoint_out_and_a_fallen_row_loses_its_ref(repo, home, monkeypatch):
    monkeypatch.setattr(CK, "MAX_SEEN", 2)
    monkeypatch.setattr(CK, "MAX_STARTS", 3)
    for i in range(3):
        (repo / "a.py").write_bytes(b"a = %d\n" % (i + 10))
        PN.record_turn_start(home, SID, str(repo), f"t{i}")
    for i in range(6):  # idle reads: the old single limit let these push starts out
        (repo / "a.py").write_bytes(b"a = %d\n" % (i + 50))
        at = f"2026-11-19T11:00:0{i}Z"
        PN._record(home, SID, str(repo), {"how": "seen", "tree": PN.snapshot_tree(str(repo)), "at": at},
                   "seen-" + PN._ref_stamp(at))
    assert [r["turn"] for r in _rows(home, "start")] == ["t0", "t1", "t2"]
    assert len(_rows(home, "seen")) == 2
    # One ref per row that is still recorded, none for a row that fell out.
    assert sorted(_refs(repo)) == sorted(r["ref"] for r in _rows(home))
    (repo / "a.py").write_bytes(b"a = 99\n")
    PN.record_turn_start(home, SID, str(repo), "t3")
    assert [r["turn"] for r in _rows(home, "start")] == ["t1", "t2", "t3"]
    assert sorted(_refs(repo)) == sorted(r["ref"] for r in _rows(home))


def test_a_ref_name_is_only_ever_inside_iron_jarvis_own_place():
    assert CK.ref_for("app_1", "start-1") == "refs/ironjarvis/checkpoints/app_1/start-1"
    assert CK.ref_for("../../heads/main", "x") == "refs/ironjarvis/checkpoints/heads_main/x"
    assert CK.ref_for("", "x") == "" and CK.ref_for("a", "..") == ""


# --------------------------------------------------------------------------- #
# the plan and the restore
# --------------------------------------------------------------------------- #


def test_the_plan_names_what_goes_back_what_moves_and_changes_nothing(repo, home):
    PN.record_turn_start(home, SID, str(repo), "t1")
    _work(repo)
    as_is = _files(repo)
    made = CK.plan(home, SID, _row(repo), "t1")
    assert sorted(made["back"]) == ["a.py", "b.py"] and made["gone"] == ["b.py"]
    assert sorted(made["new"]) == ["deep/er/n.txt", "new.py"]
    assert made["left"] == [] and made["problems"] == [] and made["can"] is True
    shown = CK.public(made)
    assert shown["back_count"] == 2 and shown["new_count"] == 2 and shown["id"] == made["id"]
    assert "tree" not in shown and "now" not in shown
    assert _files(repo) == as_is  # a plan reads


def test_a_restore_returns_every_file_byte_for_byte_and_moves_the_new_ones_to_the_trash(repo, home):
    start = _files(repo)
    PN.record_turn_start(home, SID, str(repo), "t1")
    _work(repo)
    made = CK.plan(home, SID, _row(repo), "t1")
    got = CK.restore(home, SID, _row(repo), "t1", expect=made["id"])
    assert got["ok"] and got["back"] == 2 and got["moved"] == 2
    now = _files(repo)
    ignored = now.pop("run.log")
    assert now == start  # byte for byte
    assert ignored == b"ignored\n"  # a file git ignores is in no checkpoint: left alone
    assert not (repo / "deep").exists()  # the folders the new files left empty go too
    trash = Path(got["trash"])
    assert trash.is_relative_to(home / "trash") and trash.parts[-2:] == ("code-rewind", SID)
    assert (trash / "made-since" / "new.py").read_bytes() == b"new = 1\n"
    assert (trash / "made-since" / "deep" / "er" / "n.txt").read_bytes() == b"n\n"
    # The version git wrote over is kept too (b.py was not there: nothing to keep).
    assert (trash / "replaced" / "a.py").read_bytes() == b"a = 1\n# more\n"
    assert got["replaced"] == 1 and not (trash / "replaced" / "b.py").exists()
    assert "kept there too" in got["words"]
    assert "2 put back" in got["words"] and "2 made since moved" in got["words"]


def test_a_restore_leaves_the_staging_area_the_branch_and_the_commits_alone(repo, home):
    PN.record_turn_start(home, SID, str(repo), "t1")
    (repo / "a.py").write_bytes(b"a = 1\n")
    _git(repo, "add", "a.py")  # the user (or the tool) staged it
    (repo / "c.py").write_bytes(b"c = 1\n")
    _git(repo, "add", "c.py")
    _git(repo, "commit", "-q", "-m", "the tool committed")
    (repo / "a.py").write_bytes(b"a = 2\n")
    _git(repo, "add", "a.py")
    index = _git(repo, "ls-files", "-s")
    head = _git(repo, "rev-parse", "HEAD")
    made = CK.plan(home, SID, _row(repo), "t1")
    assert made["commits_since"] == 1
    got = CK.restore(home, SID, _row(repo), "t1")
    assert (repo / "a.py").read_bytes() == b"a = 0\n" and not (repo / "c.py").exists()
    # git's own records are exactly as they were: the difference shows as changes.
    assert _git(repo, "ls-files", "-s") == index
    assert _git(repo, "rev-parse", "HEAD") == head and _git(repo, "branch", "--show-current").strip() == "main"
    assert "1 commit made since then is still in git" in got["words"]
    assert (Path(got["trash"]) / "made-since" / "c.py").read_bytes() == b"c = 1\n"


def test_one_file_in_the_way_means_nothing_changes(repo, home, monkeypatch):
    PN.record_turn_start(home, SID, str(repo), "t1")
    _work(repo)
    as_is = _files(repo)
    real = CK._writable
    monkeypatch.setattr(CK, "_writable", lambda p: False if p.endswith("a.py") else real(p))
    made = CK.plan(home, SID, _row(repo), "t1")
    assert made["can"] is False and made["problems"] == [{"rel": "a.py", "why": CK.WHY_LOCKED}]
    with pytest.raises(CK.RestoreRefused) as said:
        CK.restore(home, SID, _row(repo), "t1")
    assert said.value.sentence == CK.PROBLEMS and said.value.problems[0]["rel"] == "a.py"
    assert _files(repo) == as_is
    assert not (home / "trash").exists() and _rows(home, CK.HOW_BEFORE) == []


def test_a_folder_that_changed_after_the_preview_is_refused_untouched(repo, home):
    PN.record_turn_start(home, SID, str(repo), "t1")
    _work(repo)
    shown = CK.plan(home, SID, _row(repo), "t1")["id"]
    (repo / "late.py").write_bytes(b"late\n")  # after the user read the list
    as_is = _files(repo)
    with pytest.raises(CK.RestoreRefused) as said:
        CK.restore(home, SID, _row(repo), "t1", expect=shown)
    assert said.value.sentence == CK.FOLDER_CHANGED and _files(repo) == as_is
    # The list read again goes through.
    assert CK.restore(home, SID, _row(repo), "t1", expect=CK.plan(home, SID, _row(repo), "t1")["id"])["ok"]


def test_a_failed_restore_returns_the_folder_to_how_it_was(repo, home, monkeypatch):
    PN.record_turn_start(home, SID, str(repo), "t1")
    _work(repo)
    as_is = _files(repo)
    real = CK._restore_paths
    calls: list[str] = []

    def half(folder, tree, rels, index_dir):
        calls.append(tree)  # git writes the files, then stops with an error
        real(folder, tree, rels, index_dir)
        assert (repo / "b.py").exists()  # the deleted file was made again
        return types.SimpleNamespace(ok=False, err="fatal: unable to write file b.py", timed_out=False)

    monkeypatch.setattr(CK, "_restore_paths", half)
    with pytest.raises(CK.RestoreRefused) as said:
        CK.restore(home, SID, _row(repo), "t1")
    assert "left as it was" in said.value.sentence and "unable to write file b.py" in said.value.sentence
    assert said.value.status == 409
    # The changed file has the tool's version again, the file git made again
    # is gone again (moved aside, never deleted), the new files are back.
    assert _files(repo) == as_is
    assert len(calls) == 1  # the way back is the kept copies, not a second git write
    aside = list((home / "trash").glob("*/code-rewind/" + SID + "/rolled-back/b.py"))
    assert len(aside) == 1 and aside[0].read_bytes() == b"b = 0\n"
    # Nothing was changed, so there is nothing to undo.
    assert not CK.can_undo(home, SID)


def test_a_file_that_became_a_folder_goes_back_and_so_does_the_other_way_round(repo, home):
    (repo / "was_dir").mkdir()
    (repo / "was_dir" / "x.py").write_bytes(b"x\n")
    start = _files(repo)
    PN.record_turn_start(home, SID, str(repo), "t1")
    (repo / "a.py").unlink()
    (repo / "a.py").mkdir()
    (repo / "a.py" / "inner.txt").write_bytes(b"inner\n")  # a file became a folder
    (repo / "was_dir" / "x.py").unlink()
    (repo / "was_dir").rmdir()
    (repo / "was_dir").write_bytes(b"now a file\n")  # a folder became a file
    got = CK.restore(home, SID, _row(repo), "t1")
    assert _files(repo) == start
    assert (Path(got["trash"]) / "made-since" / "a.py" / "inner.txt").read_bytes() == b"inner\n"
    assert (Path(got["trash"]) / "made-since" / "was_dir").read_bytes() == b"now a file\n"


def test_a_session_in_a_folder_inside_the_repository_touches_only_that_folder(repo, home):
    sub = repo / "sub"
    PN.record_turn_start(home, SID, str(sub), "t1")
    (sub / "s.py").write_bytes(b"s = 9\n")
    (sub / "made.py").write_bytes(b"made\n")
    (repo / "a.py").write_bytes(b"a = outside\n")  # the user's own work beside it
    (repo / "outside.py").write_bytes(b"outside\n")
    made = CK.plan(home, SID, _row(sub), "t1")
    assert made["back"] == ["s.py"] and made["new"] == ["made.py"]
    CK.restore(home, SID, _row(sub), "t1")
    assert (sub / "s.py").read_bytes() == b"s = 0\n" and not (sub / "made.py").exists()
    assert (repo / "a.py").read_bytes() == b"a = outside\n" and (repo / "outside.py").exists()


def test_names_git_would_read_as_patterns_are_restored_as_plain_names(repo, home):
    (repo / "[x].py").write_bytes(b"one\n")
    (repo / "x.py").write_bytes(b"other\n")
    PN.record_turn_start(home, SID, str(repo), "t1")
    (repo / "[x].py").write_bytes(b"changed\n")
    (repo / "x.py").write_bytes(b"other, the user's own edit\n")
    made = CK.plan(home, SID, _row(repo), "t1")
    assert sorted(made["back"]) == ["[x].py", "x.py"]
    # Only the bracketed name is asked for: its pattern must not also take x.py.
    CK._restore_paths(str(repo), made["tree"], ["[x].py"], home / "idx")
    assert (repo / "[x].py").read_bytes() == b"one\n"
    assert (repo / "x.py").read_bytes() == b"other, the user's own edit\n"


def test_more_files_than_the_limit_is_refused_untouched(repo, home, monkeypatch):
    PN.record_turn_start(home, SID, str(repo), "t1")
    _work(repo)
    as_is = _files(repo)
    monkeypatch.setattr(CK, "RESTORE_MAX_FILES", 3)
    assert "4 files changed" in CK.public(CK.plan(home, SID, _row(repo), "t1"))["too_many"]
    with pytest.raises(CK.RestoreRefused) as said:
        CK.restore(home, SID, _row(repo), "t1")
    assert "4 files changed" in said.value.sentence and _files(repo) == as_is


def test_no_checkpoint_and_no_repository_are_said(repo, home, tmp_path):
    with pytest.raises(CK.RestoreRefused) as said:
        CK.plan(home, SID, _row(repo), "t-unknown")
    assert said.value.sentence == CK.NO_CHECKPOINT
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(CK.RestoreRefused) as said:
        CK.plan(home, SID, _row(plain), "t1")
    assert said.value.sentence == CK.NOT_REPO
    with pytest.raises(CK.RestoreRefused) as said:
        CK.plan(home, SID, _row("\\\\host\\share\\x"), "t1")
    assert said.value.sentence == PN.ON_SHARE


def test_with_gits_line_ending_setting_on_the_restore_still_matches_what_git_holds(repo, home):
    """``core.autocrlf=true`` (git for Windows' default): the files go back
    with the line endings git itself writes there, and git then sees no
    difference from the checkpoint."""
    _git(repo, "config", "core.autocrlf", "true")
    (repo / "a.py").write_bytes(b"a = 0\r\n")
    tree = PN.record_turn_start(home, SID, str(repo), "t1")
    (repo / "a.py").write_bytes(b"a = 5\r\nmore\r\n")
    CK.restore(home, SID, _row(repo), "t1")
    assert (repo / "a.py").read_bytes() == b"a = 0\r\n"
    assert PN.snapshot_tree(str(repo)) == tree
    assert CK.plan(home, SID, _row(repo), "t1")["back"] == []


# --------------------------------------------------------------------------- #
# Undo
# --------------------------------------------------------------------------- #


def test_undo_returns_the_folder_byte_for_byte_and_only_until_the_next_message(repo, home):
    PN.record_turn_start(home, SID, str(repo), "t1")
    _work(repo)
    as_is = _files(repo)
    assert not CK.can_undo(home, SID)
    CK.restore(home, SID, _row(repo), "t1")
    before = _rows(home, CK.HOW_BEFORE)
    assert len(before) == 1 and before[0]["ref"] in _refs(repo)  # the copy is a kept checkpoint
    assert CK.can_undo(home, SID)
    got = CK.undo(home, SID, _row(repo))
    assert got["ok"] and _files(repo) == as_is
    # Once undone there is nothing more to undo.
    assert not CK.can_undo(home, SID)
    with pytest.raises(CK.RestoreRefused) as said:
        CK.undo(home, SID, _row(repo))
    assert said.value.sentence == CK.NOTHING_TO_UNDO
    # A rewind, then a new message: the rewind can no longer be undone.
    CK.restore(home, SID, _row(repo), "t1")
    assert CK.can_undo(home, SID)
    PN.record_turn_start(home, SID, str(repo), "t2")
    assert not CK.can_undo(home, SID)
    after = _files(repo)
    with pytest.raises(CK.RestoreRefused) as said:
        CK.undo(home, SID, _row(repo))
    assert said.value.sentence == CK.UNDO_TOO_LATE and _files(repo) == after


def test_a_restore_that_could_not_keep_the_folder_first_changes_nothing(repo, home, monkeypatch):
    PN.record_turn_start(home, SID, str(repo), "t1")
    _work(repo)
    as_is = _files(repo)
    real = CK.keep
    monkeypatch.setattr(CK, "keep", lambda folder, ref, tree: False if "/before-" in ref else real(folder, ref, tree))
    with pytest.raises(CK.RestoreRefused) as said:
        CK.restore(home, SID, _row(repo), "t1")
    assert said.value.sentence == CK.NOT_KEPT and _files(repo) == as_is


# --------------------------------------------------------------------------- #
# a checkpoint git no longer holds (recorded before they were kept)
# --------------------------------------------------------------------------- #


def _lose_the_tree(repo: Path, home: Path) -> None:
    """The state v1.542 could reach: the record names a tree git cleaned up."""
    path = PN._trees_path(home, SID)
    rows = json.loads(path.read_text(encoding="utf-8"))
    for r in rows:
        r["tree"] = "0123456789abcdef0123456789abcdef01234567"
        r.pop("ref", None)
    path.write_text(json.dumps(rows), encoding="utf-8")


def test_a_lost_checkpoint_is_said_and_no_file_is_refused_as_staged_or_moved_as_new(repo, home):
    events = [{"type": "user", "turn": "t1", "at": NOW.isoformat()}]
    PN.record_turn_start(home, SID, str(repo), "t1")
    (repo / "a.py").write_bytes(b"a = 1\n")  # tracked: was refused as "added to git's staging area"
    (repo / "mine.txt").write_bytes(b"mine\n")  # untracked: was MOVED to the trash as if new
    _lose_the_tree(repo, home)
    for rel in ("a.py", "mine.txt"):
        with pytest.raises(PN.RevertRefused) as said:
            PN.revert_file(home, SID, _row(repo), events, "Codex", rel=rel,
                           blob=PN._blob(str(repo / rel)), scope="turn", turn="t1")
        assert "no longer kept" in said.value.sentence and rel in said.value.sentence
    assert (repo / "a.py").read_bytes() == b"a = 1\n" and (repo / "mine.txt").read_bytes() == b"mine\n"
    assert not (home / "trash").exists()
    # The whole-folder restore says the same, and the Changes pane falls back.
    with pytest.raises(CK.RestoreRefused) as gone:
        CK.plan(home, SID, _row(repo), "t1")
    assert gone.value.sentence == CK.CHECKPOINT_GONE
    view = PN.changes_view(_row(repo), events, "Codex", scope="turn", turn="t1", home=home, sid=SID, running=True)
    assert view["scope"] == "session" and view["words"].startswith(PN.TURN_CHECKPOINT_GONE)
    assert {c["rel"] for c in view["changes"]} == {"a.py", "mine.txt"}


def test_the_session_wide_revert_is_untouched_by_the_checkpoint_guard(repo, home):
    """The session's own base is a commit: the new guard must not refuse it."""
    events = [{"type": "user", "turn": "t1", "at": NOW.isoformat()}]
    (repo / "a.py").write_bytes(b"a = 1\n")
    got = PN.revert_file(home, SID, _row(repo), events, "Codex", rel="a.py",
                         blob=PN._blob(str(repo / "a.py")), scope="session")
    assert got["ok"] and (repo / "a.py").read_bytes() == b"a = 0\n"
