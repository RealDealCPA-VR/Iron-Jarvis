"""Second review D9 (v1.512.0): a pull-request watch never hangs "fixing" or
"reviewing" for ever, and a review is never paid for twice for one fix.

* ``_after_fix`` read the session's worktree with ``B._live(facts)`` uncaught.
  A worktree deleted in Explorer, or on a drive that is away, raised
  ``GitRefused`` on every 20-second pass; the pass logged a warning and the
  watch stayed as it was, with nothing said. It now ends STOPPED with the
  refusal's own sentence, as ``_check`` and ``_start_fix`` do.
* An error other than ``GitRefused`` / ``AskUser`` escaping the landing step
  (``_keep_on_side`` / ``_record_pushed``) left the watch REVIEWING, and the
  next pass started ANOTHER cloud review with no round counted. It now rests
  in "gave up" once, in plain words.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from iron_jarvis.daemon.routes import ci_watch as W
from iron_jarvis.harness import branches as B
from iron_jarvis.harness import ci_fix as CI
from iron_jarvis.harness import sessions as S
from tests.test_build_sessions_v1367 import setup_of
from tests.test_ci_fix_v1406 import (  # noqa: F401 — the fixtures
    FakeReviewer,
    _clean,
    _to_review,
    _world,
    app,
    fake,
    model,
)

pytestmark = pytest.mark.skipif(__import__("shutil").which("git") is None, reason="git is not installed")


def _drive_away(w, tmp_path: Path) -> None:
    """The session's worktree is no longer there (its drive is away)."""
    facts = setup_of(w.app, w.sid)["git"]
    S.update(w.engine, w.sid, setup={"git": {**facts, "worktree": str(tmp_path / "away" / "wt")}})


async def _review_over(w) -> None:
    """The review task in flight (if any) has ended, however it ended."""
    task = W._REVIEWS.get(w.sid)
    if task is not None:
        await asyncio.wait({task}, timeout=120.0)
        assert task.done()


async def _fixed(w) -> None:
    """watching → fix due → fixing, and the fix turn has ended."""
    await w.step()
    assert w.rec()["state"] == CI.FIX_DUE, w.rec()
    await w.step()
    assert w.rec()["state"] == CI.FIXING, w.rec()
    await w.settle_turn()


@pytest.mark.parametrize("state", [CI.FIXING, CI.REVIEWING])
async def test_a_watch_whose_worktree_is_gone_ends_stopped_and_says_why(  # noqa: F811 — the fixtures
        app, tmp_path, fake, model, monkeypatch, state):  # noqa: F811
    reviewer = FakeReviewer(["good"])
    monkeypatch.setattr(W, "REVIEWER", reviewer)
    w = await _world(app, tmp_path, fake)
    await _fixed(w)
    if state == CI.REVIEWING:
        # A restart cut the review off: the next pass judges the ended fix again.
        CI.save(w.engine, w.sid, state=CI.REVIEWING)
    _drive_away(w, tmp_path)
    await w.step()
    rec = w.rec()
    assert rec["state"] == CI.STOPPED, rec
    last = w.ci_lines()[-1]
    assert last["ci"]["kind"] == CI.KIND_STOPPED
    assert B.GONE in last["content"], last["content"]
    assert "nothing was pushed" in last["content"]
    assert reviewer.jobs == []
    # A second pass does nothing more.
    lines = len(w.ci_lines())
    w.due_now()
    await w.step()
    assert w.rec()["state"] == CI.STOPPED
    assert len(w.ci_lines()) == lines
    assert reviewer.jobs == [] and w.remote_sha() == w.sha0


async def test_an_unexpected_error_while_landing_rests_the_watch_once_and_reviews_nothing_again(
        app, tmp_path, fake, model, monkeypatch):  # noqa: F811 — the fixtures
    reviewer = FakeReviewer(["bad", "bad", "good"])
    monkeypatch.setattr(W, "REVIEWER", reviewer)

    def broken(facts, side):
        raise OSError("the disk went away (test)")

    monkeypatch.setattr(CI, "move_to_side", broken)
    w = await _world(app, tmp_path, fake)
    await _to_review(w)
    await _review_over(w)
    assert len(reviewer.jobs) == 1
    rec = w.rec()
    assert rec["state"] == CI.GAVE_UP, rec
    last = w.ci_lines()[-1]
    assert last["ci"]["kind"] == CI.KIND_GAVE_UP
    words = last["content"]
    assert "OSError" not in words and "disk went away" not in words, words
    assert "stopped fixing" in words
    # Later passes start no second review of the same fix.
    for _ in range(3):
        w.due_now()
        await w.step()
        await _review_over(w)
    assert len(reviewer.jobs) == 1, "the same fix was reviewed again"
    assert w.rec()["state"] == CI.GAVE_UP
    assert w.remote_sha() == w.sha0
