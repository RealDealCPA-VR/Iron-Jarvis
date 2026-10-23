"""v1.377.0 review (IJ harness part 2, Phase 6): BRANCH FROM A MESSAGE starts
the new session's CODE where the conversation was at that message.

The doer started a branched session's new git branch from the BASE branch's
tip, so the new session's code did NOT hold the original session's committed
work while its conversation — copied up to the message — described that
work ("I fixed the parser and committed it"): the model of the new session
read a conversation about code that was not there.

The user's model is "start a new session from any message, with the
conversation up to it". So the code starts at the newest commit ON THE
ORIGINAL SESSION'S BRANCH made at or before that message (a reply: its own
time; a question: the time it was asked, before its turn ran), else at the
commit the original session started from — never the base branch's tip
(that drops the session's work) and never the branch's current tip when
later turns committed after the message (that carries work the conversation
does not describe). Changes that were NOT committed are not carried (they
live only in the original worktree, and they may be from later turns); the
view says so, and where the code starts (``branched_from.code``).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.harness import sessions as S
from tests.test_build_sessions_v1367 import asgi, thread_row

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


@pytest.fixture()
def app(tmp_path):
    return create_app(str(tmp_path / "home"))


def _git(*args: str, cwd: Path, when: str = "") -> str:
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"}
    if when:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = when
    r = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, env=env)
    assert r.returncode == 0, (args, r.stderr.decode("utf-8", "replace"))
    return r.stdout.decode("utf-8", "replace").strip()


def _commit(wt: Path, name: str, body: bytes, when: str) -> str:
    (wt / name).write_bytes(body)
    _git("add", "-A", cwd=wt)
    _git("-c", "user.name=Agent", "-c", "user.email=agent@example.test", "commit", "-q", "-m",
         f"add {name}", cwd=wt, when=when)
    return _git("rev-parse", "HEAD", cwd=wt)


def _lines(app, sid: str) -> None:
    """Two turns: t1 (09:59 → 10:01) and t2 (11:59 → 12:01), UTC."""
    S.append_lines(app.state.platform.engine, sid, [
        {"role": "user", "content": "add a", "turnId": "t1", "at": "2026-01-01T09:59:00"},
        {"role": "assistant", "content": "Added a and committed it.", "turnId": "t1", "at": "2026-01-01T10:01:00"},
        {"role": "user", "content": "add b", "turnId": "t2", "at": "2026-01-01T11:59:00"},
        {"role": "assistant", "content": "Added b and committed it.", "turnId": "t2", "at": "2026-01-01T12:01:00"},
    ])


@pytest.fixture()
def scene(app, tmp_path, monkeypatch):
    from tests.test_build_git_v1375 import make_repo, new_branch_session, no_real_gh

    no_real_gh(monkeypatch)
    repo = make_repo(tmp_path / "proj")

    async def build():
        view = await new_branch_session(app, repo)
        wt = Path(view["git"]["worktree"])
        start = _git("rev-parse", "HEAD", cwd=wt)
        c1 = _commit(wt, "a.txt", b"a\n", "2026-01-01T10:00:00Z")
        c2 = _commit(wt, "b.txt", b"b\n", "2026-01-01T12:00:00Z")
        (wt / "a.txt").write_bytes(b"a, not committed\n")  # uncommitted work
        _lines(app, view["id"])
        # the BASE branch moved on since the session started
        (repo / "base-only.txt").write_bytes(b"later on main\n")
        _git("add", "-A", cwd=repo)
        _git("-c", "user.name=U", "-c", "user.email=u@example.test", "commit", "-q", "-m", "later", cwd=repo)
        return {"view": view, "wt": wt, "start": start, "c1": c1, "c2": c2, "repo": repo}

    return build


async def _branch(app, sid: str, turn_id: str, at: str) -> dict:
    status, view = await asgi(app, "POST", f"/build/sessions/{sid}/branch", {"turn_id": turn_id, "at": at})
    assert status == 201, view
    return view


@pytest.mark.parametrize("turn_id, at, want", [
    ("t1", "reply", "c1"),      # the reply after commit 1: its code holds a, not b
    ("t2", "question", "c1"),   # before turn 2 ran: still only a
    ("t1", "question", "start"),  # before anything was committed: where the session started
    ("t2", "reply", "c2"),      # the newest reply: both commits
])
async def test_the_new_sessions_code_starts_where_the_conversation_was(app, scene, turn_id, at, want):
    s = await scene()
    sid = s["view"]["id"]
    before = thread_row(app, sid).setup_json
    view = await _branch(app, sid, turn_id, at)
    wt_new = Path(view["git"]["worktree"])
    assert wt_new != s["wt"], "never the same worktree"
    assert _git("rev-parse", "HEAD", cwd=wt_new) == s[want], f"branch from {at} {turn_id}"
    assert not (wt_new / "base-only.txt").exists(), "never the base branch's later tip"
    assert view["git"]["base"] == s["view"]["git"]["base"] == "main"
    code = view["branched_from"]["code"]
    assert code["short"] == s[want][:7]
    assert code["from"] == ("start" if want == "start" else "branch")
    assert code["branch"] == s["view"]["git"]["branch"], "the ORIGINAL session's branch is named"
    assert code["uncommitted"] is True, "the original worktree had changes that were not carried"
    assert thread_row(app, sid).setup_json == before, "the original session never changes"
    assert (s["wt"] / "a.txt").read_bytes() == b"a, not committed\n"


async def test_a_clean_original_says_nothing_was_left_behind(app, scene):
    s = await scene()
    (s["wt"] / "a.txt").write_bytes(b"a\n")  # back to the committed bytes
    view = await _branch(app, s["view"]["id"], "t2", "reply")
    assert view["branched_from"]["code"]["uncommitted"] is False


async def test_a_session_with_no_branch_has_no_code_line(app, tmp_path):
    from tests.test_build_sessions_v1367 import new_session

    work = tmp_path / "work"
    work.mkdir()
    sid = new_session(app, work)["id"]
    _lines(app, sid)
    view = await _branch(app, sid, "t1", "reply")
    assert "code" not in (view["branched_from"] or {})
