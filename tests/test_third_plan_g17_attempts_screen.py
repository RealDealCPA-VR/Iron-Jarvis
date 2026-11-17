"""Third north-star fix plan, decision G13 (finding NA5): the files "Try
on…" carries into its attempts pass THE SAME screen as the night's commit
(``harness/night_commit``), and a file left out is named on the card.

A coding session's work not committed yet is carried into each attempt's
branch (``harness/attempts.snapshot_commit``). Before this fix it was a bare
``git add -A``: a secret-named file, a huge file, all went onto every
``ij/try-…`` branch, and a later Push sent them. Driven like the attempts'
own proofs: the REAL app factory, the real build turn route, real git in a
scratch repository; only the models are scripted doubles. Nothing here
reaches a real model or service, and no value here is shaped like a real
credential.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.harness import attempts as A
from iron_jarvis.harness import night_commit as N
from tests.test_build_accounts_v1368 import enable, isolate, make_fake
from tests.test_build_attempts_v1397 import (
    BUGGY,
    World,
    attempts_turn,
    card_of,
    failing_script,
    item_for,
    make_calc_repo,
    new_session,
    passing_script,
    wire_models,
)
from tests.test_build_git_v1375 import git, no_real_gh, snapshot
from tests.test_build_sessions_v1367 import asgi, setup_of

GIT = shutil.which("git")
pytestmark = pytest.mark.skipif(GIT is None, reason="git is not installed")


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    no_real_gh(monkeypatch)
    yield from isolate(tmp_path, monkeypatch)


@pytest.fixture
def fake(tmp_path):
    f = make_fake(tmp_path)
    yield f
    f.stop()


@pytest.fixture
def app(tmp_path):
    return create_app(str(tmp_path / "home"))


@pytest.fixture
def repo(tmp_path) -> Path:
    return make_calc_repo(tmp_path / "proj")


NOTES = b"Notes the session wrote, never committed.\n"
#: A secret's own file NAME by the night screen's rule; the content is plain
#: words (never shaped like a real credential).
SECRET_NAME = ".env.local"
SECRET_BODY = b"GREETING=hello there\n"
#: An ignored file (the scratch repository ignores ``__pycache__/``).
IGNORED = "__pycache__/scratch.txt"
IGNORED_BODY = b"left alone\n"
TRY = "Try this with Codex and the local model: fix add in calc.py."


def _log_all(cwd: Path, rel: str) -> str:
    return git("log", "--all", "--format=%H", "--", rel, cwd=cwd)


def _in_commit(cwd: Path, ref: str, rel: str) -> bool:
    return rel in git("ls-tree", "-r", "--name-only", ref, cwd=cwd).splitlines()


def _write_session_files(folder: Path) -> None:
    (folder / "NOTES.md").write_bytes(NOTES)
    (folder / SECRET_NAME).write_bytes(SECRET_BODY)
    (folder / IGNORED).parent.mkdir(parents=True, exist_ok=True)
    (folder / IGNORED).write_bytes(IGNORED_BODY)


async def test_a_folder_sessions_attempts_leave_out_a_secret_file_and_keep_never_loses_it(
    app, fake, repo, monkeypatch,
):
    """A session that works in its folder directly (no branch of its own):
    (a) an ordinary new file IS carried onto each attempt's branch, (b) a
    secret-named file is in NO commit and is named on the card, (c) an
    ignored file is untouched. After Keep the secret file is still in the
    user's folder byte for byte and in no branch's history."""
    await enable(app)
    wire_models(app, monkeypatch, World({"codex-cli": passing_script(), "ollama": failing_script()}))
    view = await new_session(app, repo, branch=False)
    sid = view["id"]
    _write_session_files(repo)
    before = snapshot(repo)

    card = card_of(await attempts_turn(app, sid, TRY, "g13-1"))
    codex, local = item_for(card, "codex-cli"), item_for(card, "ollama")
    assert card.get("carried") is True
    for it in (codex, local):
        # (a) the ordinary new file is on the attempt's branch and folder.
        assert _in_commit(repo, it["branch"], "NOTES.md")
        assert (Path(it["folder"]) / "NOTES.md").read_bytes() == NOTES
        # (b) the secret-named file is not, (c) nor the ignored one.
        assert not _in_commit(repo, it["branch"], SECRET_NAME)
        assert not (Path(it["folder"]) / SECRET_NAME).exists()
        assert not _in_commit(repo, it["branch"], IGNORED)
    assert _log_all(repo, SECRET_NAME) == ""
    # The card names it, with why, in plain words (the night's own words).
    assert card["left_out"] == [{"path": SECRET_NAME, "why": N.why_words(N.SECRET)}]
    # The model reads it too (the reply line's words).
    done_words = A.summary_words(card)
    assert SECRET_NAME in done_words and "Not carried into the attempts" in done_words

    # The user's folder is exactly as it was while they ran.
    assert snapshot(repo) == before

    status, kept = await asgi(app, "POST", f"/build/sessions/{sid}/attempts/keep",
                              {"turn_id": "g13-1", "attempt": codex["id"]})
    assert status == 200, kept
    assert setup_of(app, sid)["git"]["branch"] == codex["branch"]
    # Keep never touches the user's folder: the secret file is there, as it was.
    assert (repo / SECRET_NAME).read_bytes() == SECRET_BODY
    assert (repo / IGNORED).read_bytes() == IGNORED_BODY
    assert snapshot(repo) == before
    # …and in no commit of any branch (Keep's own commit included).
    assert _log_all(repo, SECRET_NAME) == ""
    # Keep says the kept folder lacks it (the session works there now).
    line = next(m for m in _messages(app, sid) if m.get("turnId") == "g13-1" and m.get("role") == "assistant")
    assert SECRET_NAME in line[A.LINE_KEY].get("keep_note", "")


async def test_a_branch_sessions_attempts_leave_out_a_secret_file_and_keep_lands_around_it(
    app, fake, repo, monkeypatch,
):
    """A session on its own branch with work not committed: the secret file
    in its folder is not carried, Keep lands the kept result around it (the
    file stays, byte for byte, still not committed)."""
    await enable(app)
    wire_models(app, monkeypatch, World({"codex-cli": passing_script(), "ollama": failing_script()}))
    view = await new_session(app, repo)
    sid = view["id"]
    wt = Path(view["git"]["worktree"])
    _write_session_files(wt)

    card = card_of(await attempts_turn(app, sid, TRY, "g13-2"))
    codex = item_for(card, "codex-cli")
    assert card["left_out"] == [{"path": SECRET_NAME, "why": N.why_words(N.SECRET)}]
    assert _in_commit(repo, codex["branch"], "NOTES.md")
    assert _log_all(repo, SECRET_NAME) == ""

    status, kept = await asgi(app, "POST", f"/build/sessions/{sid}/attempts/keep",
                              {"turn_id": "g13-2", "attempt": codex["id"]})
    assert status == 200, kept
    assert (wt / SECRET_NAME).read_bytes() == SECRET_BODY
    assert (wt / "NOTES.md").read_bytes() == NOTES
    assert (wt / IGNORED).read_bytes() == IGNORED_BODY
    assert _log_all(repo, SECRET_NAME) == ""
    # Still not committed, still untracked: Keep did not report it as a change.
    assert git("status", "--porcelain", "--", SECRET_NAME, cwd=wt) == f"?? {SECRET_NAME}"


def test_a_file_over_the_nights_size_limit_is_left_out_of_the_carried_commit(repo, monkeypatch):
    """The size rule is the night screen's own constant (made a few bytes
    here: no 50 MB file is written)."""
    monkeypatch.setattr(N, "MAX_COMMIT_FILE_BYTES", 64)
    (repo / "NOTES.md").write_bytes(NOTES)
    (repo / "big.bin").write_bytes(b"x" * 200)
    head = git("rev-parse", "HEAD", cwd=repo)
    sha, left = A.snapshot_commit(repo, head)
    assert sha
    names = git("ls-tree", "-r", "--name-only", sha, cwd=repo).splitlines()
    assert "NOTES.md" in names and "big.bin" not in names
    assert left == [{"path": "big.bin", "why": N.why_words(N.LARGE)}]
    # Only the folder's files were read: its index, files and refs are as they were.
    assert git("status", "--porcelain", cwd=repo).splitlines() == ["?? NOTES.md", "?? big.bin"]


def test_when_only_left_out_files_are_new_nothing_is_carried_but_they_are_still_named(repo):
    (repo / SECRET_NAME).write_bytes(SECRET_BODY)
    head = git("rev-parse", "HEAD", cwd=repo)
    sha, left = A.snapshot_commit(repo, head)
    assert sha == ""
    assert left == [{"path": SECRET_NAME, "why": N.why_words(N.SECRET)}]


def test_a_tracked_file_that_gains_a_secret_shaped_value_is_carried_at_its_committed_copy(repo):
    """The screen's content rule: a committed file that now holds a
    secret-shaped value (built here from parts, never a real one) is carried
    as it was committed."""
    value = "token = " + "ab12" * 6
    (repo / "calc.py").write_bytes(BUGGY + f"{value}\n".encode())
    head = git("rev-parse", "HEAD", cwd=repo)
    sha, left = A.snapshot_commit(repo, head)
    assert sha == ""  # nothing else changed
    assert left == [{"path": "calc.py", "why": N.why_words(N.SECRET)}]


def test_the_saved_card_keeps_the_left_out_names_and_drops_junk():
    card = A.saved_card({
        "items": [{"id": "try1", "state": "done"}],
        "left_out": [{"path": ".env", "why": "looks like a secret"}, {"path": 3}, "junk", {"why": "x"}],
    })
    assert card is not None
    assert card["left_out"] == [{"path": ".env", "why": "looks like a secret"}]
    assert "left_out" not in A.saved_card({"items": [{"id": "try1", "state": "done"}]})


def _messages(app, sid: str) -> list[dict]:
    from tests.test_build_sessions_v1367 import messages_of

    return messages_of(app, sid)
