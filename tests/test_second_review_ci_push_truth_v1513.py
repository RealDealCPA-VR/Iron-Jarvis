"""Second review D1 and D4 (v1.513.0): the pull-request auto-fix never says
"Pushed the reviewed fix" over something else, and never has a fix reviewed
or pushed whose own finish check says it is not done.

* D1. The push step's commit is the night shift's SCREENED commit: a file that
  looks like a secret, one over the size limit or a new file in a dependency
  folder is left out (``branches.commit`` answers ``left_out``; when nothing
  else was left it refuses "nothing to commit" carrying ``exc.left_out``).
  ``ci_watch`` never read either: it pushed without the file (or pushed
  nothing new) and said "Pushed the reviewed fix to <branch>", then CI failed
  on the same head and rounds 2 and 3 repeated. Now a commit that left ANY
  file out is not pushed: the watch rests ASKING, naming the file and why; a
  push step that committed nothing (the branch tip still the failing head)
  never pushes and says so in words.
* D4. ``_after_fix`` never read the fix turn's finish record. A fix whose own
  tests still failed went to the paid cloud review and, approved, was pushed.
  Now the watch rests ASKING before any review, in plain words.

Driven with the module's own world (real git, a bare remote, a fake ``gh``,
real build turns): ``tests/test_ci_fix_v1406.py``.
"""

from __future__ import annotations

import pytest

from iron_jarvis.daemon.routes import ci_watch as W
from iron_jarvis.harness import ci_fix as CI
from iron_jarvis.harness import night as N
from tests.test_ci_fix_v1406 import (  # noqa: F401 — the fixtures
    FakeReviewer,
    _clean,
    _world,
    app,
    fake,
    model,
)

pytestmark = pytest.mark.skipif(__import__("shutil").which("git") is None, reason="git is not installed")

PUSHED = "Pushed the reviewed fix"


async def _fixed(w) -> None:
    """watching → fix due → fixing, and the fix turn has ended."""
    await w.step()
    assert w.rec()["state"] == CI.FIX_DUE, w.rec()
    await w.step()
    assert w.rec()["state"] == CI.FIXING, w.rec()
    await w.settle_turn()


def _spy_push(monkeypatch) -> list[str]:
    calls: list[str] = []
    real = CI.push_fix

    def spy(facts, pr, **k):
        calls.append(facts["branch"])
        return real(facts, pr, **k)

    monkeypatch.setattr(CI, "push_fix", spy)
    return calls


def _finish_on_line(monkeypatch, finish) -> None:
    """The fix turn's line carries ``finish`` (``None``: no finish record)."""
    real = N.last_reply_line

    def patched(messages, turn_ids=None):
        line = real(messages, turn_ids)
        if isinstance(line, dict):
            if finish is None:
                line.pop("finish", None)
            else:
                line["finish"] = dict(finish)
        return line

    monkeypatch.setattr(N, "last_reply_line", patched)


def _said(w) -> str:
    return "\n".join(str(m.get("content") or "") for m in w.ci_lines())


async def _no_second_round(w, model, reviewer) -> None:  # noqa: F811 - a fixture
    fixes, jobs = len(model.fix_prompts), len(reviewer.jobs)
    for _ in range(2):
        w.due_now()
        await w.step()
        await w.settle_review()
    assert w.rec()["state"] == CI.ASKING, w.rec()
    assert len(model.fix_prompts) == fixes, "a second fix turn started"
    assert len(reviewer.jobs) == jobs, "a second review was paid for"


# --------------------------------------------------------------------------- #
# D1: a screened commit that left a file out is never pushed
# --------------------------------------------------------------------------- #


async def test_a_fix_whose_commit_left_a_secret_named_file_out_is_not_pushed_and_names_it(
        app, tmp_path, fake, model, monkeypatch):  # noqa: F811 — the fixtures
    reviewer = FakeReviewer(["good"])
    monkeypatch.setattr(W, "REVIEWER", reviewer)
    pushes = _spy_push(monkeypatch)
    w = await _world(app, tmp_path, fake)
    await _fixed(w)
    # The fix also left a settings file whose NAME is a secret's.
    (w.wt / ".env").write_bytes(b"GREETING=hello\n")
    await w.step()
    assert w.rec()["state"] == CI.REVIEWING, w.rec()
    await w.settle_review()
    assert len(reviewer.jobs) == 1
    rec = w.rec()
    assert rec["state"] == CI.ASKING, rec
    assert pushes == [], "the fix was pushed without the file its commit left out"
    assert w.remote_sha() == w.sha0
    last = w.ci_lines()[-1]
    assert last["ci"]["kind"] == CI.KIND_ASKING
    words = last["content"]
    assert ".env (looks like a secret)" in words, words
    assert "not pushed" in words, words
    assert PUSHED not in _said(w)
    # The rest of the fix is committed on the session's branch (kept, said).
    assert w.local_sha() != w.sha0
    assert "committed on the session's branch" in words, words
    assert (w.wt / ".env").is_file()
    await _no_second_round(w, model, reviewer)
    assert w.remote_sha() == w.sha0


async def test_a_fix_where_everything_was_screened_out_pushes_nothing_and_never_says_pushed(
        app, tmp_path, fake, model, monkeypatch):  # noqa: F811 — the fixtures
    reviewer = FakeReviewer(["good"])
    monkeypatch.setattr(W, "REVIEWER", reviewer)
    pushes = _spy_push(monkeypatch)
    w = await _world(app, tmp_path, fake)
    await _fixed(w)
    # The only change left is a file the screen refuses.
    (w.wt / "app.py").write_bytes(b"x = 2\n")
    (w.wt / ".env").write_bytes(b"GREETING=hello\n")
    await w.step()
    assert w.rec()["state"] == CI.REVIEWING, w.rec()
    await w.settle_review()
    rec = w.rec()
    assert rec["state"] == CI.ASKING, rec
    assert pushes == []
    assert w.remote_sha() == w.sha0 and w.local_sha() == w.sha0
    words = w.ci_lines()[-1]["content"]
    assert ".env (looks like a secret)" in words, words
    assert "nothing was committed" in words.lower() or "committed nothing" in words.lower(), words
    assert PUSHED not in _said(w)
    await _no_second_round(w, model, reviewer)


async def test_a_push_step_that_committed_nothing_never_pushes_the_failing_head(
        app, tmp_path, fake, model, monkeypatch):  # noqa: F811 — the fixtures
    reviewer = FakeReviewer(["good"])
    monkeypatch.setattr(W, "REVIEWER", reviewer)
    pushes = _spy_push(monkeypatch)
    w = await _world(app, tmp_path, fake)
    await _fixed(w)
    # The commit makes nothing (git took none of it), and leaves nothing out.
    monkeypatch.setattr(CI, "commit_fix", lambda *a, **k: None)
    await w.step()
    await w.settle_review()
    rec = w.rec()
    assert rec["state"] == CI.ASKING, rec
    assert pushes == [], "push_fix ran with the branch tip still the failing head"
    assert w.remote_sha() == w.sha0
    words = w.ci_lines()[-1]["content"]
    assert words == W.NOTHING_COMMITTED_WORDS, words
    assert PUSHED not in _said(w)


# --------------------------------------------------------------------------- #
# D4: a fix whose own finish check says it is not done
# --------------------------------------------------------------------------- #


async def test_a_fix_whose_own_tests_still_fail_is_never_reviewed_nor_pushed(
        app, tmp_path, fake, model, monkeypatch):  # noqa: F811 — the fixtures
    reviewer = FakeReviewer(["good"])
    monkeypatch.setattr(W, "REVIEWER", reviewer)
    pushes = _spy_push(monkeypatch)
    _finish_on_line(monkeypatch, {"tests": "failed"})
    w = await _world(app, tmp_path, fake)
    await _fixed(w)
    await w.step()
    assert reviewer.jobs == [], "a review was paid for over failing tests"
    rec = w.rec()
    assert rec["state"] == CI.ASKING, rec
    assert pushes == [] and w.remote_sha() == w.sha0
    last = w.ci_lines()[-1]
    assert last["ci"]["kind"] == CI.KIND_ASKING
    words = last["content"]
    assert "own tests still fail" in words and "nothing was pushed" in words.lower(), words
    assert PUSHED not in _said(w)


def test_the_not_done_words_name_the_reason():
    assert "own tests still fail" in W._not_done_words({"tests": "failed"})
    two = W._not_done_words({"tests": "passed", "unmet": 2})
    assert "2 points of the request are still open" in two, two
    one = W._not_done_words({"unmet": 1})
    assert "1 point of the request is still open" in one, one


# --------------------------------------------------------------------------- #
# a clean fix: exactly as before
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("finish", [None, {"tests": "passed"}], ids=["no-finish-record", "tests-passed"])
async def test_a_clean_fix_still_commits_pushes_and_says_pushed(
        app, tmp_path, fake, model, monkeypatch, finish):  # noqa: F811 — the fixtures
    reviewer = FakeReviewer(["good"])
    monkeypatch.setattr(W, "REVIEWER", reviewer)
    pushes = _spy_push(monkeypatch)
    _finish_on_line(monkeypatch, finish)
    w = await _world(app, tmp_path, fake)
    await _fixed(w)
    await w.step()
    await w.settle_review()
    assert len(reviewer.jobs) == 1
    rec = w.rec()
    assert rec["state"] == CI.WATCHING, rec
    assert pushes == [w.branch]
    assert w.remote_sha() != w.sha0 and rec["pushed_sha"] == w.remote_sha()
    last = w.ci_lines()[-1]
    assert last["ci"]["kind"] == CI.KIND_PUSHED
    assert last["content"] == (f"Pushed the reviewed fix to {w.branch} (commit {w.remote_sha()[:12]}). "
                               "Watching the pull request's checks again.")
