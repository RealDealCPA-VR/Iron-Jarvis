"""v1.546.0 — Build › Code: checkpoints for a folder that is NOT a git
repository (Release D of ``docs/BUILD-CODE-T3-PLAN-2026-11-19.md``, phase 1d;
``hosted/checkpoints.py``, ``hosted/panes.py``).

A session whose folder is in no repository keeps its checkpoints in a store of
Iron Jarvis's own, one per session, outside the folder. Everything Release A
built (the kept checkpoint, the preview, the copy-first restore, Undo) then
works there too:

* a message gets a kept checkpoint, and NOTHING is written into the folder;
* a rewind puts the files back byte for byte (line endings and binary files
  included), moves new files to the trash, and Undo returns the folder;
* folders such as ``node_modules`` are in no checkpoint: neither put back
  nor removed;
* a folder past the limits gets no checkpoint, and the Changes tab says why;
* two sessions in one folder have a store each and share the folder's lock;
* deleting the session removes its store; a git folder never gets one.

Real git, real routes and service; the coding tool is the recorded fake, and
the test itself writes the files a tool would have written.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.hosted import checkpoints as CK
from iron_jarvis.hosted import panes as PN
from iron_jarvis.hosted import service as S
from iron_jarvis.hosted import store
from tests._build_app_helpers_v1409 import FAKE, client, new_session, stop_all

CLAUDE_FIX = Path(__file__).parent / "fixtures" / "build_app_v1409" / "claude_resume.json"
CRLF = b"line one\r\nline two\r\n"
BINARY = bytes(range(256)) * 4


@pytest.fixture()
def plain(tmp_path) -> Path:
    """A folder in NO git repository, with a line-ending file, a binary file
    and a folder every walk of the app leaves out."""
    folder = tmp_path / "plain folder"
    folder.mkdir()
    (folder / "notes.txt").write_bytes(CRLF)
    (folder / "data").mkdir()
    (folder / "data" / "blob.bin").write_bytes(BINARY)
    (folder / "node_modules" / "pkg").mkdir(parents=True)
    (folder / "node_modules" / "pkg" / "index.js").write_bytes(b"module.exports = 1\n")
    return folder


@pytest.fixture(autouse=True)
def _a_checkpoint_for_every_message(monkeypatch):
    """(As ``test_build_app_rewind_files_v1543``: these tests are about what
    happens WITH a checkpoint, so a message waits for it.)"""
    real = PN.record_turn_start_async

    async def patient(home, sid, folder, turn_id, *, bound=120.0, uid=""):
        return await real(home, sid, folder, turn_id, bound=120.0, uid=uid)

    monkeypatch.setattr(PN, "record_turn_start_async", patient)


@pytest.fixture()
def claude_home(tmp_path, monkeypatch) -> Path:
    home = tmp_path / "claude-home"
    home.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    return home


@pytest.fixture()
def app(tmp_path):
    return create_app(str(tmp_path / "home"))


def _fake(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(S, "binary_for", lambda harness: [sys.executable, str(FAKE)])
    monkeypatch.setenv("IJ_FAKE_SCENARIO", str(CLAUDE_FIX))
    monkeypatch.setenv("IJ_FAKE_LOG", str(tmp_path / "fake.log"))


async def _turn(c, sid: str, text: str, tid: str) -> None:
    r = await c.post(f"/build/app/sessions/{sid}/turn", json={"text": text, "turn_id": tid})
    assert r.status_code == 200, r.text


def _files(folder: Path) -> dict[str, bytes]:
    return {p.relative_to(folder).as_posix(): p.read_bytes() for p in sorted(folder.rglob("*")) if p.is_file()}


def _ats(view: dict) -> list[str]:
    return [e["at"] for e in view["events"] if e["type"] == "user"]


def _git_in(store_dir: Path, *args: str) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return subprocess.run(["git", "--git-dir", str(store_dir), *args], check=True, capture_output=True,
                          env=env).stdout.decode()


def _home(app) -> Path:
    return Path(S.service_for(app.state.platform).home)


# --------------------------------------------------------------------------- #
# the store itself
# --------------------------------------------------------------------------- #


def test_the_store_is_iron_jarvis_own_outside_the_folder_and_left_out_of_backups(tmp_path):
    home = tmp_path / "home"
    path = CK.own_store(home, "app_1")
    assert path == home / "workspaces" / "code-checkpoints" / "app_1.git"
    assert CK.own_store(home, "") is None and CK.own_store(None, "app_1") is None
    # A session id is never a path of its own.
    assert CK.own_store(home, "../../x").parent == home / "workspaces" / "code-checkpoints"
    assert not CK.has_own_store(home, "app_1")
    made = CK.make_own_store(home, "app_1")
    assert made == path and CK.has_own_store(home, "app_1")
    assert CK.make_own_store(home, "app_1") == path  # made once
    # Its own settings: a restore is byte for byte whatever this PC's git says.
    assert _git_in(path, "config", "core.autocrlf").strip() == "false"
    assert _git_in(path, "config", "core.bare").strip() == "false"
    assert _git_in(path, "config", "gc.autoDetach").strip() == "false"
    exclude = (path / "info" / "exclude").read_text(encoding="utf-8")
    assert "node_modules/\n" in exclude and ".venv/\n" in exclude and ".git/" not in exclude
    # Never a name a person gives a folder of their work (the review).
    for name in ("build", "dist", "target", "venv", ".idea", ".vscode"):
        assert f"\n{name}/\n" not in exclude
    # The folder's own .gitattributes never filters what is kept.
    assert (path / "info" / "attributes").read_text(encoding="utf-8") == CK.OWN_ATTRIBUTES
    # A store an earlier version made is brought up to date at its next use.
    (path / "info" / "exclude").write_text("build/\n", encoding="utf-8")
    (path / "info" / "attributes").unlink()
    CK.make_own_store(home, "app_1")
    assert (path / "info" / "exclude").read_text(encoding="utf-8") == CK.own_exclude()
    assert (path / "info" / "attributes").read_text(encoding="utf-8") == CK.OWN_ATTRIBUTES
    assert not list(path.parent.glob("*.new"))  # nothing half-made is left


def test_a_store_that_cannot_be_made_leaves_nothing_half_made(tmp_path, monkeypatch):
    from iron_jarvis.git import integration as GI

    home = tmp_path / "home"
    real = GI.run_git

    def no_config(args, *a, **k):
        if "config" in args:
            return SimpleNamespace(ok=False, timed_out=False, err="fatal: could not lock config file", out="",
                                   code=255)
        return real(args, *a, **k)

    monkeypatch.setattr(GI, "run_git", no_config)
    with pytest.raises(PN.GitUnreadable):
        CK.make_own_store(home, "app_1")
    assert not CK.has_own_store(home, "app_1")
    assert list((home / "workspaces" / "code-checkpoints").iterdir()) == []


def test_git_is_told_about_the_store_only_for_its_own_folder_and_only_inside_the_block(tmp_path):
    home = tmp_path / "home"
    folder = tmp_path / "work"
    other = tmp_path / "other"
    folder.mkdir()
    other.mkdir()
    assert CK.store_env(str(folder)) == {}
    with CK.in_own_store(home, "app_1", str(folder)) as none:
        assert none is None and CK.store_env(str(folder)) == {}  # it has no store: nothing changes
    path = CK.make_own_store(home, "app_1")
    with CK.in_own_store(home, "app_1", str(folder)) as used:
        assert used == path
        assert CK.store_env(str(folder)) == {"GIT_DIR": str(path), "GIT_WORK_TREE": str(folder)}
        assert CK.store_env(str(other)) == {} and CK.store_env("") == {}
        # The store is never remembered as the FOLDER's repository.
        assert CK.repo_of(str(folder)) == str(path)
    assert CK.store_env(str(folder)) == {}
    assert CK.repo_of(str(folder)) == ""  # outside the block the folder is in no repository
    assert CK.lock_key(str(folder)).endswith(os.path.normcase(str(folder)))


def test_a_checkpoint_of_a_plain_folder_writes_nothing_into_it(tmp_path, plain):
    home = tmp_path / "home"
    before = _files(plain)
    tree = PN.record_turn_start(home, "app_1", str(plain), "t1", uid="2026-11-20T10:00:00Z")
    assert tree and CK.has_own_store(home, "app_1")
    assert _files(plain) == before and not (plain / ".git").exists()
    [row] = PN.read_trees(home, "app_1")
    assert row["tree"] == tree and row["ref"].startswith(CK.REF_ROOT + "/app_1/start-")
    assert row["repo"] == str(CK.own_store(home, "app_1"))
    store_dir = CK.own_store(home, "app_1")
    # Kept by a ref in the store, so git's own clean-up never deletes it.
    assert row["ref"] in _git_in(store_dir, "for-each-ref", "--format=%(refname)")
    _git_in(store_dir, "gc", "--prune=now", "--quiet")
    names = _git_in(store_dir, "ls-tree", "-r", "--name-only", tree).splitlines()
    assert sorted(names) == ["data/blob.bin", "notes.txt"]  # node_modules is in no checkpoint
    with CK.in_own_store(home, "app_1", str(plain)):
        assert CK.tree_kept(str(plain), tree)
    assert not CK.tree_kept(str(plain), tree)  # the folder itself is in no repository


def test_a_git_folder_never_gets_a_store_of_its_own(tmp_path):
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True, env=env)
    (repo / "a.py").write_bytes(b"a = 0\n")
    assert PN.record_turn_start(home, "app_1", str(repo), "t1", uid="u1")
    assert not CK.has_own_store(home, "app_1")
    assert not (home / "workspaces" / "code-checkpoints").exists()


def test_a_git_folder_that_does_not_answer_never_gets_a_store_of_its_own(tmp_path, monkeypatch):
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()

    def slow(*a, **k):
        raise PN.GitUnreadable(SimpleNamespace(timed_out=True, err="", out="", ok=False))

    monkeypatch.setattr(PN, "_record_start", slow)
    assert PN.record_turn_start(home, "app_1", str(repo), "t1") is None
    assert not CK.has_own_store(home, "app_1")
    # Refused for another reason ("dubious ownership"): not "no repository" either.

    def refused(*a, **k):
        raise PN.GitUnreadable(SimpleNamespace(timed_out=False, ok=False, out="",
                                               err="fatal: detected dubious ownership in repository"))

    monkeypatch.setattr(PN, "_record_start", refused)
    assert PN.record_turn_start(home, "app_1", str(repo), "t1") is None
    assert not CK.has_own_store(home, "app_1")


def test_a_checkpoint_that_falls_off_the_list_loses_its_ref_in_the_store(tmp_path, plain, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setattr(CK, "MAX_STARTS", 1)
    PN.record_turn_start(home, "app_1", str(plain), "t1", uid="u1")
    first = PN.read_trees(home, "app_1")[0]["ref"]
    (plain / "notes.txt").write_bytes(b"next\n")
    PN.record_turn_start(home, "app_1", str(plain), "t2", uid="u2")
    [kept] = PN.read_trees(home, "app_1")
    refs = _git_in(CK.own_store(home, "app_1"), "for-each-ref", "--format=%(refname)").split()
    assert refs == [kept["ref"]] and first not in refs


def test_a_conversation_rewind_drops_the_checkpoints_of_the_messages_it_took_away(tmp_path, plain):
    home = tmp_path / "home"
    PN.record_turn_start(home, "app_1", str(plain), "t1", uid="u1")
    (plain / "notes.txt").write_bytes(b"next\n")
    PN.record_turn_start(home, "app_1", str(plain), "t2", uid="u2")
    assert CK.forget_turns(home, "app_1", str(plain), {"t1"}) == 1
    [kept] = PN.read_trees(home, "app_1")
    assert kept["turn"] == "t1"
    refs = _git_in(CK.own_store(home, "app_1"), "for-each-ref", "--format=%(refname)").split()
    assert refs == [kept["ref"]]


def test_a_repository_git_cannot_read_now_never_gets_a_store(tmp_path):
    home = tmp_path / "home"
    # A worktree whose main repository is away: its ``.git`` FILE names a
    # folder that is not there, and git says "not a git repository".
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    (worktree / ".git").write_text("gitdir: " + str(tmp_path / "gone" / ".git" / "worktrees" / "w") + "\n",
                                   encoding="utf-8")
    (worktree / "a.txt").write_bytes(b"a\n")
    assert PN.record_turn_start(home, "app_1", str(worktree), "t1") is None
    assert not CK.has_own_store(home, "app_1")
    # (The same for a folder under it.)
    (worktree / "sub").mkdir()
    assert PN.record_turn_start(home, "app_2", str(worktree / "sub"), "t1") is None
    assert not CK.has_own_store(home, "app_2")


def test_a_folder_that_became_a_repository_never_gets_iron_jarvis_names_and_a_delete_sweeps_them(
        tmp_path, plain):
    home = tmp_path / "home"
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    assert PN.record_turn_start(home, "app_1", str(plain), "t1", uid="u1")
    # The tool runs ``git init`` (or a folder above becomes one).
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=plain, check=True, env=env)
    (plain / "notes.txt").write_bytes(b"changed\n")
    # The Changes tab's idle read writes nothing into the user's repository…
    events = [{"type": "user", "at": "2026-11-20T10:00:00Z", "turn": "t1", "text": "x"},
              {"type": "done", "at": "2026-11-20T10:00:05Z", "turn": "t1", "stop": "completed"}]
    PN.note_seen(home, "app_1", str(plain), events)
    row = SimpleNamespace(id="app_1", folder=str(plain), created_at=None)
    turn = PN.changes_view(row, events, "Codex", scope=PN.SCOPE_TURN, turn="t1", home=home, sid="app_1")
    assert PN.TURN_CHECKPOINT_GONE not in turn["words"]
    out = subprocess.run(["git", "for-each-ref"], cwd=plain, check=True, capture_output=True, env=env).stdout
    assert b"ironjarvis" not in out
    assert {r["how"] for r in PN.read_trees(home, "app_1")} == {"start"}
    # …and a name that got there anyway (a row that names that repository)
    # is swept when the session is deleted, with the store.
    tree = subprocess.run(["git", "write-tree"], cwd=plain, check=True, capture_output=True, env=env).stdout.strip()
    ref = CK.ref_for("app_1", "seen-1")
    subprocess.run(["git", "update-ref", ref, tree.decode()], cwd=plain, check=True, env=env)
    rows = PN.read_trees(home, "app_1") + [{"how": "seen", "turn": "t1", "tree": tree.decode(), "at": "x",
                                            "ref": ref, "repo": str(plain / ".git")}]
    PN._write_trees(home, "app_1", rows)
    # (The folder itself is gone by then — only the row names the repository.)
    keep = tmp_path / "repo-of-the-folder.git"
    shutil.copytree(plain / ".git", keep)
    rows[-1]["repo"] = str(keep)
    PN._write_trees(home, "app_1", rows)
    shutil.rmtree(plain / ".git", onerror=lambda f, p, e: (os.chmod(p, 0o700), f(p)))
    assert CK.forget_session(home, "app_1", str(tmp_path / "no-such-folder")) == 1
    out = subprocess.run(["git", "--git-dir", str(keep), "for-each-ref"], check=True, capture_output=True,
                         env=env).stdout
    assert b"ironjarvis" not in out and not CK.has_own_store(home, "app_1")


def test_a_network_folder_and_a_missing_folder_get_nothing(tmp_path):
    home = tmp_path / "home"
    assert PN.record_turn_start(home, "app_1", "\\\\server\\share\\work", "t1") is None
    assert PN.record_turn_start(home, "app_1", str(tmp_path / "gone"), "t1") is None
    assert not CK.has_own_store(home, "app_1")


def test_a_session_goes_on_with_the_store_it_has_when_the_folder_becomes_a_repository(tmp_path, plain):
    home = tmp_path / "home"
    first = PN.record_turn_start(home, "app_1", str(plain), "t1", uid="u1")
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=plain, check=True, env=env)
    (plain / "notes.txt").write_bytes(b"changed\n")
    second = PN.record_turn_start(home, "app_1", str(plain), "t2", uid="u2")
    assert first and second and first != second
    rows = PN.read_trees(home, "app_1")
    assert {r["repo"] for r in rows} == {str(CK.own_store(home, "app_1"))}
    # The folder's own repository was given no hidden name.
    out = subprocess.run(["git", "for-each-ref"], cwd=plain, check=True, capture_output=True, env=env).stdout
    assert b"ironjarvis" not in out


def test_folders_a_person_names_build_or_target_are_in_the_checkpoint(tmp_path, plain):
    home = tmp_path / "home"
    for name in ("Target", "Build", "dist", "venv"):
        (plain / name).mkdir()
        (plain / name / "work.txt").write_bytes(name.encode() + b" client file\n")
    tree = PN.record_turn_start(home, "app_1", str(plain), "t1", uid="u1")
    names = _git_in(CK.own_store(home, "app_1"), "ls-tree", "-r", "--name-only", tree).splitlines()
    for name in ("Target", "Build", "dist", "venv"):
        assert f"{name}/work.txt" in names
    assert not [n for n in names if n.startswith("node_modules/")]
    # The tool rewrites one and deletes another: both go back.
    (plain / "Target" / "work.txt").write_bytes(b"fee: 1\n")
    (plain / "Build" / "work.txt").unlink()
    row = SimpleNamespace(id="app_1", folder=str(plain))
    made = CK.plan(home, "app_1", row, "t1")
    assert sorted(made["back"]) == ["Build/work.txt", "Target/work.txt"]
    CK.restore(home, "app_1", row, "t1", expect=made["id"])
    assert (plain / "Target" / "work.txt").read_bytes() == b"Target client file\n"
    assert (plain / "Build" / "work.txt").read_bytes() == b"Build client file\n"


def test_a_folders_gitattributes_never_changes_the_bytes_that_come_back(tmp_path, plain):
    home = tmp_path / "home"
    (plain / ".gitattributes").write_bytes(b"* text=auto\n")
    (plain / "lf.txt").write_bytes(b"one\ntwo\n")
    (plain / "mixed.txt").write_bytes(b"one\r\ntwo\n")
    assert PN.record_turn_start(home, "app_1", str(plain), "t1", uid="u1")
    before = _files(plain)
    for name in ("lf.txt", "mixed.txt", "notes.txt"):
        (plain / name).unlink()
    row = SimpleNamespace(id="app_1", folder=str(plain))
    made = CK.plan(home, "app_1", row, "t1")
    assert made["endings"] == ""  # nothing converts line endings here
    CK.restore(home, "app_1", row, "t1", expect=made["id"])
    assert _files(plain) == before


# --------------------------------------------------------------------------- #
# the limits
# --------------------------------------------------------------------------- #


def test_a_folder_past_the_limits_gets_no_checkpoint_and_the_reason_is_kept(tmp_path, plain, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setattr(CK, "OWN_MAX_FILES", 2)
    (plain / "third.txt").write_bytes(b"3\n")
    assert PN.record_turn_start(home, "app_1", str(plain), "t1", uid="u1") is None
    assert PN.read_trees(home, "app_1") == []
    assert CK.own_note(home, "app_1") == CK.OWN_TOO_MANY.format(n=2)
    assert CK.own_words(home, "app_1") == CK.OWN_TOO_MANY.format(n=2)
    # node_modules is not counted: with the third file gone it fits again,
    # the message has a checkpoint and the reason is cleared.
    (plain / "third.txt").unlink()
    assert PN.record_turn_start(home, "app_1", str(plain), "t2", uid="u2")
    assert CK.own_note(home, "app_1") == "" and CK.own_words(home, "app_1") == CK.OWN_WORDS
    # Too many bytes.
    monkeypatch.setattr(CK, "OWN_MAX_BYTES", 100)
    assert PN.record_turn_start(home, "app_1", str(plain), "t3", uid="u3") is None
    assert CK.own_note(home, "app_1") == CK.OWN_TOO_BIG.format(mb=0)
    assert len(PN.read_trees(home, "app_1")) == 1
    assert CK.own_words(home, "nobody") == ""


def test_a_folder_that_cannot_be_read_in_time_says_so(tmp_path, plain, monkeypatch):
    home = tmp_path / "home"
    assert PN.record_turn_start(home, "app_1", str(plain), "t1", uid="u1")
    assert CK.own_note(home, "app_1") == ""

    def slow(folder):
        raise PN.GitUnreadable(SimpleNamespace(timed_out=True, err="", out="", ok=False))

    monkeypatch.setattr(CK, "own_fits", slow)
    assert PN.record_turn_start(home, "app_1", str(plain), "t2", uid="u2") is None
    assert CK.own_note(home, "app_1") == CK.OWN_SLOW and CK.own_words(home, "app_1") == CK.OWN_SLOW
    assert len(PN.read_trees(home, "app_1")) == 1
    monkeypatch.undo()
    # The snapshot itself not answering is said the same way.
    real = PN._record_start

    def no_answer(*a, **k):
        raise PN.GitUnreadable(SimpleNamespace(timed_out=True, err="", out="", ok=False))

    monkeypatch.setattr(PN, "_record_start", no_answer)
    assert PN.record_turn_start(home, "app_1", str(plain), "t3", uid="u3") is None
    assert CK.own_note(home, "app_1") == CK.OWN_SLOW
    monkeypatch.setattr(PN, "_record_start", real)
    assert PN.record_turn_start(home, "app_1", str(plain), "t4", uid="u4")
    assert CK.own_note(home, "app_1") == ""


def test_a_failure_that_is_not_slowness_says_its_own_reason(tmp_path, plain, monkeypatch):
    home = tmp_path / "home"
    assert PN.record_turn_start(home, "app_1", str(plain), "t1", uid="u1")

    def refused(*a, **k):
        raise PN.GitUnreadable(SimpleNamespace(timed_out=False, ok=False, out="",
                                               err="error: open(\"Ledger.qbw\"): Permission denied"))

    monkeypatch.setattr(PN, "_record_start", refused)
    assert PN.record_turn_start(home, "app_1", str(plain), "t2", uid="u2") is None
    assert CK.own_note(home, "app_1") == CK.OWN_NOT_READ.format(
        why='error: open("Ledger.qbw"): Permission denied')
    assert "in time" not in CK.own_note(home, "app_1")


def test_a_message_that_stopped_waiting_says_it_has_no_checkpoint(tmp_path, plain):
    import threading

    home = tmp_path / "home"
    assert PN.record_turn_start(home, "app_1", str(plain), "t1", uid="u1")
    late = threading.Event()
    late.set()
    assert PN.record_turn_start(home, "app_1", str(plain), "t2", abandon=late, uid="u2") is None
    assert CK.own_note(home, "app_1") == CK.OWN_LATE
    assert len(PN.read_trees(home, "app_1")) == 1


def test_a_preview_or_an_undo_never_copies_a_folder_past_the_limits(tmp_path, plain, monkeypatch):
    home = tmp_path / "home"
    assert PN.record_turn_start(home, "app_1", str(plain), "t1", uid="u1")
    store_dir = CK.own_store(home, "app_1")
    objects = len(_git_in(store_dir, "rev-list", "--all", "--objects").split())
    (plain / "big.bin").write_bytes(b"x" * 300_000)
    monkeypatch.setattr(CK, "OWN_MAX_BYTES", 200_000)
    row = SimpleNamespace(id="app_1", folder=str(plain))
    with pytest.raises(CK.RestoreRefused) as said:
        CK.plan(home, "app_1", row, "t1")
    assert said.value.sentence == "The files in this folder are more than 0 MB in all, so nothing was changed."
    with pytest.raises(CK.RestoreRefused):
        CK.restore(home, "app_1", row, "t1")
    # Nothing of the folder as it is now was copied into the store.
    assert not list(store_dir.rglob("*.lock"))
    assert len(_git_in(store_dir, "rev-list", "--all", "--objects").split()) == objects
    assert (plain / "big.bin").exists()


def test_the_limits_of_iron_jarvis_own_copies_never_apply_to_a_git_folder(tmp_path, monkeypatch):
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True, env=env)
    for i in range(3):
        (repo / f"f{i}.txt").write_bytes(b"x\n")
    assert PN.record_turn_start(home, "app_1", str(repo), "t1", uid="u1")
    monkeypatch.setattr(CK, "OWN_MAX_FILES", 1)
    (repo / "f0.txt").write_bytes(b"changed\n")
    made = CK.plan(home, "app_1", SimpleNamespace(id="app_1", folder=str(repo)), "t1")
    assert made["back"] == ["f0.txt"] and made["own"] is False


def test_the_words_inside_the_store_name_no_git(tmp_path):
    home = tmp_path / "home"
    folder = tmp_path / "work"
    folder.mkdir()
    CK.make_own_store(home, "app_1")
    many = {"back": ["a"] * (CK.RESTORE_MAX_FILES + 1), "new": [], "left": [], "problems": [], "id": "x",
            "can": False}
    assert "git" not in CK.public({**many, "own": True})["too_many"]
    assert "git" in CK.public({**many, "own": False})["too_many"]
    (folder / ".gitattributes").write_bytes(b"* text=auto\n")
    assert CK._endings(str(folder)) != ""  # (outside the store: the git folder's words)
    with CK.in_own_store(home, "app_1", str(folder)):
        assert CK._say(CK.WHY_GIT_SLOW) == CK.WHY_SLOW_OWN
        assert CK._endings(str(folder)) == ""
        slow = PN.GitUnreadable(SimpleNamespace(timed_out=True, ok=False, out="", err=""))
        assert CK._unread_words(slow) == CK.OWN_UNREAD.format(why=PN.GIT_TIMED_OUT)
        assert "git" not in CK._unread_words(slow)
    assert CK._unread_words(slow) == slow.words  # a git folder keeps git's words


def test_a_gitignore_in_the_folder_leaves_files_out_of_the_count_and_the_checkpoint(tmp_path, plain, monkeypatch):
    home = tmp_path / "home"
    (plain / "cache").mkdir()
    for i in range(5):
        (plain / "cache" / f"c{i}.tmp").write_bytes(b"x")
    (plain / ".gitignore").write_bytes(b"cache/\n")
    monkeypatch.setattr(CK, "OWN_MAX_FILES", 3)  # notes.txt, data/blob.bin, .gitignore
    tree = PN.record_turn_start(home, "app_1", str(plain), "t1", uid="u1")
    assert tree
    names = _git_in(CK.own_store(home, "app_1"), "ls-tree", "-r", "--name-only", tree).splitlines()
    assert sorted(names) == [".gitignore", "data/blob.bin", "notes.txt"]


# --------------------------------------------------------------------------- #
# through the routes: a rewind in a plain folder
# --------------------------------------------------------------------------- #


async def test_a_plain_folders_files_go_back_byte_for_byte_and_undo_returns_them(
        app, plain, claude_home, tmp_path, monkeypatch):
    _fake(monkeypatch, tmp_path)
    home = _home(app)
    async with client(app) as c:
        sid = (await new_session(c, plain))["id"]
        await _turn(c, sid, "first one", "t1")
        # What the tool did after the first message…
        (plain / "notes.txt").write_bytes(b"rewritten\n")
        (plain / "made.txt").write_bytes(b"made by the tool\n")
        at_second = _files(plain)
        await _turn(c, sid, "second one", "t2")
        # …and after the second.
        (plain / "notes.txt").write_bytes(b"rewritten again\r\n")
        (plain / "data" / "blob.bin").unlink()
        (plain / "later.txt").write_bytes(b"later\n")
        (plain / "node_modules" / "pkg" / "index.js").write_bytes(b"module.exports = 2\n")
        (plain / "node_modules" / "new-pkg").mkdir()
        (plain / "node_modules" / "new-pkg" / "x.js").write_bytes(b"x\n")
        as_it_is = _files(plain)

        view = (await c.get(f"/build/app/sessions/{sid}")).json()
        ats = _ats(view)
        assert view["branch"]["files_at"] == ats and CK.has_own_store(home, sid)
        assert not (plain / ".git").exists()

        # The preview reads only.
        r = await c.get(f"/build/app/sessions/{sid}/rewind/preview", params={"to": ats[1]})
        assert r.status_code == 200, r.text
        shown = r.json()
        assert _files(plain) == as_it_is
        assert sorted(shown["back"]) == ["data/blob.bin", "notes.txt"]
        assert shown["new"] == ["later.txt"] and shown["can"] is True
        # No line-ending note (the store converts nothing) and no commits (it holds none).
        assert shown["endings"] == "" and shown["commits_since"] == 0
        assert shown["own"] is True  # (the screen's words then name no git)

        r = await c.post(f"/build/app/sessions/{sid}/rewind/files",
                         json={"to": ats[1], "expect": shown["id"]})
        assert r.status_code == 200, r.text
        # Byte for byte as it was when the second message started; what
        # checkpoints leave out is neither put back nor removed.
        now = _files(plain)
        kept_out = {k: v for k, v in as_it_is.items() if k.startswith("node_modules/")}
        assert {k: v for k, v in now.items() if not k.startswith("node_modules/")} == \
            {k: v for k, v in at_second.items() if not k.startswith("node_modules/")}
        assert {k: v for k, v in now.items() if k.startswith("node_modules/")} == kept_out
        assert now["data/blob.bin"] == BINARY
        # The new file was MOVED to Iron Jarvis's trash, never deleted.
        moved = list((home / "trash").rglob("later.txt"))
        assert moved and moved[0].read_bytes() == b"later\n"
        assert not (plain / ".git").exists()

        # Undo: the folder is as it was just before the rewind.
        assert (await c.get(f"/build/app/sessions/{sid}")).json()["branch"]["undo_rewind"] is True
        r = await c.post(f"/build/app/sessions/{sid}/rewind/undo", json={})
        assert r.status_code == 200, r.text
        assert _files(plain) == as_it_is
    await stop_all(app)


async def test_the_changes_tab_says_how_a_plain_folder_is_kept_and_why_not(app, plain, claude_home, tmp_path,
                                                                             monkeypatch):
    _fake(monkeypatch, tmp_path)
    home = _home(app)
    async with client(app) as c:
        sid = (await new_session(c, plain))["id"]
        # Before any message there is no store, and nothing is promised.
        words = (await c.get(f"/build/app/sessions/{sid}/changes")).json()["words"]
        assert "not in a git repository" in words and CK.OWN_WORDS not in words
        await _turn(c, sid, "first one", "t1")
        got = (await c.get(f"/build/app/sessions/{sid}/changes")).json()
        assert got["source"] == "tool" and got["words"].endswith(CK.OWN_WORDS)
        # "This message" still needs git: the whole session's list, as before
        # (never "the checkpoint for this message is no longer kept").
        turn = (await c.get(f"/build/app/sessions/{sid}/changes", params={"scope": "turn"})).json()
        assert turn["scope"] == "session" and turn["source"] == "tool"
        assert PN.TURN_CHECKPOINT_GONE not in turn["words"] and turn["words"] == got["words"]
        # Past the limit: the next message has no checkpoint, and the tab says why.
        monkeypatch.setattr(CK, "OWN_MAX_FILES", 1)
        await _turn(c, sid, "second one", "t2")
        view = (await c.get(f"/build/app/sessions/{sid}")).json()
        assert view["branch"]["files_at"] == _ats(view)[:1]
        words = (await c.get(f"/build/app/sessions/{sid}/changes")).json()["words"]
        assert words.endswith(CK.OWN_TOO_MANY.format(n=1)) and CK.OWN_WORDS not in words
        assert len(PN.read_trees(home, sid)) == 1
    await stop_all(app)


async def test_two_sessions_in_one_plain_folder_keep_a_store_each_and_share_its_lock(app, plain, monkeypatch):
    svc = S.service_for(app.state.platform)
    home = _home(app)
    a = store.create(app.state.platform.engine, harness="grok", folder=str(plain), harness_session_id="x-1")
    b = store.create(app.state.platform.engine, harness="grok", folder=str(plain), harness_session_id="x-2")
    assert await PN.record_turn_start_async(home, a.id, str(plain), "t1", uid="ua")
    (plain / "notes.txt").write_bytes(b"between\n")
    assert await PN.record_turn_start_async(home, b.id, str(plain), "t1", uid="ub")
    assert CK.own_store(home, a.id) != CK.own_store(home, b.id)
    assert CK.has_own_store(home, a.id) and CK.has_own_store(home, b.id)
    [ra], [rb] = PN.read_trees(home, a.id), PN.read_trees(home, b.id)
    assert ra["tree"] != rb["tree"] and ra["repo"] != rb["repo"]
    # One lock for the folder, whichever session writes its files.
    assert CK.lock_key(str(plain)) and "code-checkpoints" not in CK.lock_key(str(plain))
    # Each restores from ITS store.
    made_a = await asyncio.to_thread(CK.plan, home, a.id, a, "t1")
    made_b = await asyncio.to_thread(CK.plan, home, b.id, b, "t1")
    assert made_a["back"] == ["notes.txt"] and made_b["back"] == []
    assert svc is not None


async def test_deleting_the_session_removes_its_store_and_an_archive_keeps_it(app, plain, claude_home, tmp_path,
                                                                               monkeypatch):
    _fake(monkeypatch, tmp_path)
    home = _home(app)
    async with client(app) as c:
        sid = (await new_session(c, plain))["id"]
        await _turn(c, sid, "first one", "t1")
        store_dir = CK.own_store(home, sid)
        assert store_dir.is_dir()
        assert (await c.post(f"/build/app/sessions/{sid}/archive")).status_code == 200
        assert store_dir.is_dir()
        assert (await c.post(f"/build/app/sessions/{sid}/unarchive")).status_code == 200
        r = await c.delete(f"/build/app/sessions/{sid}")
        assert r.status_code == 200, r.text
        assert not store_dir.exists()
        # A copy of files that are still in the folder is not trash.
        assert not list((home / "trash").rglob("code-checkpoints"))
        assert _files(plain)["notes.txt"] == CRLF
    await stop_all(app)


def test_a_store_that_is_gone_is_simply_no_store(tmp_path):
    home = tmp_path / "home"
    assert CK.forget_own_store(home, "app_1") is False
    CK.make_own_store(home, "app_1")
    assert CK.forget_own_store(home, "app_1") is True and not CK.has_own_store(home, "app_1")
    assert CK.forget_session(home, "app_1", str(tmp_path)) == 0


async def test_a_plain_folders_reasons_never_speak_of_git(app, plain, monkeypatch):
    home = _home(app)
    row = store.create(app.state.platform.engine, harness="grok", folder=str(plain), harness_session_id="x-1")
    (plain / "cache.txt").write_bytes(b"in the checkpoint\n")
    (plain / "secret.txt").write_bytes(b"left out then\n")
    (plain / ".gitignore").write_bytes(b"secret.txt\n")
    assert await PN.record_turn_start_async(home, row.id, str(plain), "t1", uid="u1")
    # (A file "made before" the checkpoint is told by its creation time, with
    # two seconds' slack: the test says secret.txt is far older than that.)
    monkeypatch.setattr(CK, "_born", lambda st: 0.0)
    # Since then the rules changed: cache.txt is left out NOW, secret.txt no longer.
    (plain / ".gitignore").write_bytes(b"cache.txt\n")
    made = await asyncio.to_thread(CK.plan, home, row.id, row, "t1")
    why = {x["rel"]: x["why"] for x in made["left"]}
    assert why["cache.txt"] == CK.WHY_IGNORED_OWN
    assert why["secret.txt"] == CK.WHY_OLDER_OWN
    assert all("git" not in w.replace(".gitignore", "") for w in why.values())
    assert made["own"] is True and made["back"] == [".gitignore"]
    # The same reasons in a git folder still say git (outside the store).
    assert CK._say(CK.WHY_IGNORED) == CK.WHY_IGNORED
    with CK.in_own_store(home, row.id, str(plain)):
        assert CK._say(CK.WHY_IGNORED) == CK.WHY_IGNORED_OWN
        assert CK._say(CK.DONE_STAYED) == CK.DONE_STAYED_OWN
        assert CK._say("any other sentence") == "any other sentence"
