# ruff: noqa: F811 — the fixtures are imported from test_ci_fix_v1406 by name.
"""v1.406.0 part 3 follow-ups: the five open items of the review, each closed
the SAFE way — the fix waits for the user, and nothing is pushed by itself.

1. THE FIX TURN'S POSTURE. A fix is Iron Jarvis starting work by itself while
   it reads a log an attacker may influence, so it runs under the night
   shift's guarded posture whatever the session's own (yolo included): the
   night allow-list runs unasked, everything else is carded and, with nobody
   there, declined after the night's card wait — and the watch then waits for
   the user, saying which step. A session set to "Ask before everything"
   keeps that stricter posture.
2. INTERMEDIATE COMMITS. A commit Iron Jarvis did not make between the
   failing head and what would be pushed: the fix waits for the user, the
   commit named.
3. THE PUSH ADDRESS. The branch's push URL (as git really resolves it,
   ``insteadOf`` included) must be a GitHub URL, on the pull request's own
   host exactly, of the repository its head lives in — never a folder on this
   PC, another host, an ssh alias or another repository.
4. HOOKS AND CANCEL. Emergency stop ends the commit's hooks and the push's
   (``Kill`` through the night commit's ``on_spawn``); a push that was cut off
   is READ BACK from the remote and said truthfully (pushed, or not).
5. BINARIES. A fix that changes a file that is not text (the reviewer cannot
   read it) waits for the user.

Real git, a bare remote, a fake ``gh`` on PATH, real build turns.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest

from iron_jarvis.daemon.routes import ci_watch as W
from iron_jarvis.harness import branches as B
from iron_jarvis.harness import ci_fix as CI
from iron_jarvis.harness import night as N
from iron_jarvis.harness import sessions as S
from iron_jarvis.providers.adapters.base import ToolCall
from iron_jarvis.tools.base import ToolResult
from tests.test_build_git_v1375 import git
from tests.test_build_sessions_v1367 import messages_of, until
from tests.test_ci_fix_v1406 import (  # noqa: F401 — the fixtures
    CiModel,
    FakeReviewer,
    _clean,
    _to_review,
    _world,
    _text,
    app,
    fake,
    model,
)

pytestmark = pytest.mark.skipif(__import__("shutil").which("git") is None, reason="git is not installed")

#: The REAL reading of where git pushes (``_clean`` replaces it in every test
#: whose push lands in a local bare repository).
REAL_PUSH_URL = getattr(CI, "resolved_push_url", None)


def _last_ci(w) -> str:
    return w.ci_lines()[-1]["content"] if w.ci_lines() else ""


class FetchFirstModel(CiModel):
    """A fix turn that first fetches a web page (NOT on the night allow-list),
    then writes the fix."""

    def _move(self, messages, tools):
        from iron_jarvis.harness.finish import REVIEW_BRIEF_LEAD

        texts = [_text(m) for m in messages]
        if any(REVIEW_BRIEF_LEAD in t for t in texts):
            return super()._move(messages, tools)
        marks = [i for i, (m, t) in enumerate(zip(messages, texts))
                 if getattr(m, "role", "") == "user" and t.startswith("Fix the failing checks")]
        if tools and marks:
            done = [m for m in messages[marks[-1]:] if getattr(m, "role", "") == "tool"]
            if not done:
                self.fix_prompts.append(texts[marks[-1]])
                return [ToolCall(id="f1", name="WebFetch",
                                 arguments={"url": "https://example.invalid/hint", "prompt": "read"})]
            if len(done) == 1:
                return [ToolCall(id="w1", name="Write", arguments={"file_path": "app.py", "content": "x = 3\n"})]
        return "Fixed it."


def _wire(app, m) -> None:
    app.state.platform.router.stream = m.stream
    app.state.platform.router.complete = m.complete


@pytest.fixture()
def fetched(app, monkeypatch) -> list:
    """``web_fetch`` never reaches the network: a stand-in records the call."""
    calls: list = []
    tool = app.state.platform.registry.get("web_fetch")
    assert tool is not None

    async def execute(args, ctx):
        calls.append(dict(args or {}))
        return ToolResult(ok=True, output="a page")

    monkeypatch.setattr(tool, "execute", execute)
    return calls


@pytest.fixture()
def short_wait(monkeypatch):
    monkeypatch.setattr(N, "card_wait_seconds", lambda config: 1.0)


# --------------------------------------------------------------------------- #
# 1. the fix turn's posture
# --------------------------------------------------------------------------- #


async def test_a_yolo_session_still_cards_a_step_off_the_night_list_and_the_watch_waits(
        app, tmp_path, fake, monkeypatch, fetched, short_wait):
    m = FetchFirstModel()
    _wire(app, m)
    reviewer = FakeReviewer(["good"])
    monkeypatch.setattr(W, "REVIEWER", reviewer)
    w = await _world(app, tmp_path, fake)
    S.update(w.engine, w.sid, setup={"approval_mode": "yolo"})
    await w.step()
    await w.step()
    assert w.rec()["state"] == CI.FIXING
    await w.settle_turn()
    await w.step()
    # The page fetch asked (not on the night allow-list), nobody answered
    # within the card wait, so it was declined — never run…
    assert fetched == []
    # …and the watch waits for the user, saying which step; nothing reviewed
    # or pushed.
    assert w.rec()["state"] == CI.ASKING, w.rec()
    assert "needed your OK" in _last_ci(w)
    assert reviewer.jobs == [] and w.remote_sha() == w.sha0
    # The session's own posture is untouched (nothing stored).
    from tests.test_build_sessions_v1367 import setup_of

    assert setup_of(app, w.sid).get("approval_mode") == "yolo"


async def test_an_ask_before_everything_session_keeps_its_stricter_posture(app, tmp_path, fake, model,
                                                                          monkeypatch, short_wait):
    reviewer = FakeReviewer(["good"])
    monkeypatch.setattr(W, "REVIEWER", reviewer)
    w = await _world(app, tmp_path, fake)
    S.update(w.engine, w.sid, setup={"approval_mode": "always_ask"})
    await w.step()
    await w.step()
    await w.settle_turn()
    await w.step()
    # Even the edit asked (the session asks before everything); nobody
    # answered: declined, and the watch waits for the user.
    assert w.rec()["state"] == CI.ASKING, w.rec()
    assert reviewer.jobs == [] and w.remote_sha() == w.sha0
    assert (w.wt / "app.py").read_bytes() == b"x = 2\n"


# --------------------------------------------------------------------------- #
# 2. intermediate commits
# --------------------------------------------------------------------------- #


async def test_a_commit_iron_jarvis_did_not_make_is_never_pushed_by_itself(app, tmp_path, fake, model, monkeypatch):
    monkeypatch.setattr(W, "REVIEWER", FakeReviewer(["good"]))
    w = await _world(app, tmp_path, fake)
    await w.step()
    await w.step()
    await w.settle_turn()
    (w.wt / "notes.txt").write_bytes(b"mine\n")
    git("add", "notes.txt", cwd=w.wt)
    git("commit", "-q", "-m", "Someone else's commit", cwd=w.wt)
    theirs = git("rev-parse", "HEAD", cwd=w.wt)
    await w.step()
    await w.settle_review()
    assert w.rec()["state"] == CI.ASKING, w.rec()
    words = _last_ci(w)
    assert "did not make" in words and theirs[:12] in words and "Someone else's commit" in words
    assert w.remote_sha() == w.sha0


# --------------------------------------------------------------------------- #
# 3. the push address
# --------------------------------------------------------------------------- #


async def test_the_push_address_must_be_the_pull_requests_own_github_repository(app, tmp_path, fake, model):
    w = await _world(app, tmp_path, fake)
    from tests.test_build_sessions_v1367 import setup_of

    facts = setup_of(app, w.sid)["git"]

    def pr(**over):
        base = dict(state="open", head_ref=w.branch, head_sha=w.sha0, base_ref="main",
                    url="https://github.com/acme/proj/pull/7", number="7", head_owner="acme", head_repo="proj")
        base.update(over)
        return CI.PrChecks(**base)

    def refusal() -> str:
        return CI.push_address_refusal(facts, pr(), w.wt)

    import iron_jarvis.harness.ci_fix as mod

    real = REAL_PUSH_URL
    mod.resolved_push_url = real
    try:
        # A folder on this PC (the test's own bare remote).
        assert "a folder on this PC" in refusal()
        for url, where in (("https://gitlab.com/acme/proj.git", "gitlab.com"),
                           ("git@github-work:acme/proj.git", "github-work"),
                           ("git@gh-work:acme/proj.git", "gh-work"),
                           ("https://github.com/evil/proj.git", "evil/proj")):
            git("remote", "set-url", "--push", "origin", url, cwd=w.wt)
            got = refusal()
            assert got and where in got, (url, got)
        for url in ("git@github.com:acme/proj.git", "https://github.com/acme/proj.git",
                    "ssh://git@github.com/ACME/Proj.git"):
            git("remote", "set-url", "--push", "origin", url, cwd=w.wt)
            assert refusal() == "", url
        # A rewrite git applies (insteadOf) is where the push REALLY goes.
        git("config", f"url.{w.bare.as_posix()}.insteadOf", "https://github.com/acme/proj.git", cwd=w.wt)
        git("remote", "set-url", "--push", "origin", "https://github.com/acme/proj.git", cwd=w.wt)
        assert "a folder on this PC" in refusal()
        assert CI.push_address_refusal(facts, pr(head_owner="", head_repo=""), w.wt) == CI.PUSH_ADDRESS_UNKNOWN
    finally:
        mod.resolved_push_url = real


async def test_a_push_to_a_folder_on_this_pc_waits_for_the_user(app, tmp_path, fake, model, monkeypatch):
    monkeypatch.setattr(W, "REVIEWER", FakeReviewer(["good"]))
    monkeypatch.setattr(CI, "resolved_push_url", REAL_PUSH_URL)
    w = await _world(app, tmp_path, fake)
    await _to_review(w)
    await w.settle_review()
    assert w.rec()["state"] == CI.ASKING, w.rec()
    assert "a folder on this PC" in _last_ci(w)
    assert w.remote_sha() == w.sha0


# --------------------------------------------------------------------------- #
# 4. hooks and a cancelled push
# --------------------------------------------------------------------------- #


def _hook(w, name: str, tmp_path: Path) -> tuple[Path, Path]:
    common = Path(git("rev-parse", "--path-format=absolute", "--git-common-dir", cwd=w.wt))
    started, done = tmp_path / f"{name}.started", tmp_path / f"{name}.done"
    hook = common / "hooks" / name
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_bytes(
        f'#!/bin/sh\necho started > "{started.as_posix()}"\nsleep 40\necho done > "{done.as_posix()}"\n'
        .encode())
    hook.chmod(0o755)
    return started, done


async def test_emergency_stop_ends_the_commits_hook_and_nothing_is_pushed(app, tmp_path, fake, model, monkeypatch):
    monkeypatch.setattr(W, "REVIEWER", FakeReviewer(["good"]))
    w = await _world(app, tmp_path, fake)
    started, done = _hook(w, "pre-commit", tmp_path)
    await _to_review(w)
    assert await until(started.exists, 120.0), "the commit's hook never ran"
    app.state.platform.config.autonomy_kill_switch = True
    try:
        t0 = time.monotonic()
        await w.settle_review()
        assert time.monotonic() - t0 < 30.0
        assert w.rec()["state"] == CI.STOPPED, w.rec()
        assert w.remote_sha() == w.sha0
        await asyncio.sleep(0.5)
        assert not done.exists(), "the hook ran on after Emergency stop"
    finally:
        app.state.platform.config.autonomy_kill_switch = False


async def test_a_cancel_during_the_push_ends_it_and_says_it_was_not_pushed(app, tmp_path, fake, model, monkeypatch):
    from iron_jarvis.agents.orchestrator import Orchestrator

    monkeypatch.setattr(W, "REVIEWER", FakeReviewer(["good"]))
    w = await _world(app, tmp_path, fake)
    started, done = _hook(w, "pre-push", tmp_path)
    await _to_review(w)
    assert await until(started.exists, 120.0), "the push's hook never ran"
    app.state.platform.config.autonomy_kill_switch = True
    try:
        stopped = await asyncio.to_thread(Orchestrator(app.state.platform).stop_unattended)
        assert f"ci:{w.sid}" in stopped
        task = W._REVIEWS.get(w.sid)
        if task is not None:
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 120.0)
        assert w.remote_sha() == w.sha0
        rec = w.rec()
        assert rec["state"] == CI.STOPPED, rec
        words = _last_ci(w)
        assert "stopped the push" in words and w.sha0[:12] in words and "not pushed" in words, words
        await asyncio.sleep(0.5)
        assert not done.exists(), "the push's hook ran on after the stop"
    finally:
        app.state.platform.config.autonomy_kill_switch = False


async def test_a_push_that_went_out_before_it_was_cut_off_is_said_as_pushed(app, tmp_path, fake, model, monkeypatch):
    monkeypatch.setattr(W, "REVIEWER", FakeReviewer(["good"]))
    real_push = CI.push_fix

    def push_then_fail(facts, pr, **kw):
        real_push(facts, pr, **kw)
        raise B.GitRefused("The push did not go through.", 409)  # cut off after the remote took it

    monkeypatch.setattr(CI, "push_fix", push_then_fail)
    w = await _world(app, tmp_path, fake)
    await _to_review(w)
    await w.settle_review()
    rec = w.rec()
    assert w.remote_sha() != w.sha0
    assert rec["state"] == CI.WATCHING and rec["pushed_sha"] == w.remote_sha(), rec
    assert "had already gone out" in _last_ci(w)


# --------------------------------------------------------------------------- #
# 5. binaries
# --------------------------------------------------------------------------- #


async def test_a_fix_with_a_file_that_is_not_text_waits_for_the_user(app, tmp_path, fake, model, monkeypatch):
    reviewer = FakeReviewer(["good"])
    monkeypatch.setattr(W, "REVIEWER", reviewer)
    w = await _world(app, tmp_path, fake)
    await w.step()
    await w.step()
    await w.settle_turn()
    (w.wt / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\0\0\0\rIHDR")
    await w.step()
    assert w.rec()["state"] == CI.ASKING, w.rec()
    assert "logo.png" in _last_ci(w) and "not text" in _last_ci(w)
    assert reviewer.jobs == [] and w.remote_sha() == w.sha0


def test_a_changed_tracked_binary_is_named(tmp_path):
    from tests.test_build_git_v1375 import make_repo

    repo = make_repo(tmp_path / "r")
    (repo / "data.bin").write_bytes(b"\0\1\2")
    git("add", "-A", cwd=repo)
    git("commit", "-q", "-m", "bin", cwd=repo)
    base = git("rev-parse", "HEAD", cwd=repo)
    assert CI.binary_changes(repo, base) == []
    (repo / "data.bin").write_bytes(b"\0\1\3")
    (repo / "app.py").write_bytes(b"x = 9\n")
    assert CI.binary_changes(repo, base) == ["data.bin"]
    assert CI.binary_changes(repo, "") is None


def test_the_night_turn_hears_its_notes_and_foreign_commits_are_listed(tmp_path):
    heard: list = []
    t = N.NightTurn(task_id="", card_wait_s=1.0, engine=None, on_note=lambda k, x: heard.append((k, x)))
    t.note("declined", "Needs your OK: fetch a page")
    assert heard == [("declined", "Needs your OK: fetch a page")]
    from tests.test_build_git_v1375 import make_repo

    repo = make_repo(tmp_path / "r")
    base = git("rev-parse", "HEAD", cwd=repo)
    (repo / "a.txt").write_bytes(b"a\n")
    git("add", "-A", cwd=repo)
    git("commit", "-q", "-m", "mine", cwd=repo)
    mine = git("rev-parse", "HEAD", cwd=repo)
    (repo / "b.txt").write_bytes(b"b\n")
    git("add", "-A", cwd=repo)
    git("commit", "-q", "-m", "theirs", cwd=repo)
    theirs = git("rev-parse", "HEAD", cwd=repo)
    got = CI.foreign_commits(repo, base, [mine])
    assert got == [f"{theirs[:12]} “theirs”"]
    assert CI.foreign_commits(repo, base, [mine, theirs]) == []
    assert CI.foreign_commits(repo, "", [mine]) is None
    assert json.loads(json.dumps(got)) == got
    _ = messages_of  # (imported for the pins' shared helpers)
