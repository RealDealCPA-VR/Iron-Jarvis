"""v1.377.0 (IJ harness part 2, Phase 6): the daemon's half of the smaller
touches and loose ends.

* NEXT STEP — ``POST /build/sessions/{id}/followups``: the chat's follow-ups
  (``chat_followups``, OFF by default: no model call) on the SESSION's own
  model, pinned, billed to the session, at most two;
* BRANCH FROM A MESSAGE — ``POST /build/sessions/{id}/branch``: a NEW session
  with the conversation up to a message, the old one untouched; a session on
  its own git branch gets its OWN new branch from the same base;
* CONTINUATION TURNS carry the Browser pane's address (kept on the session);
* REWIND puts the question's pictures and files back (the answer carries
  them) and says when the rewound work was COMMITTED on the session's branch;
* a session read mid-Compact says so (``compacting``).

REAL app factory, real routes, real store; the model is the only double
(``Script`` / a recorder on ``router.complete``); real git in a temp folder.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlmodel import select

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import AgentRun
from iron_jarvis.daemon.app import create_app
from iron_jarvis.harness import sessions as S
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from tests.test_build_sessions_v1367 import (
    CLAUDE,
    Script,
    asgi,
    asgi_get,
    messages_of,
    new_session,
    open_turn,
    setup_of,
    thread_row,
    turn_log,
    until,
    wire,
)


@pytest.fixture()
def app(tmp_path):
    return create_app(str(tmp_path / "home"))


@pytest.fixture()
def work(tmp_path) -> Path:
    folder = tmp_path / "work"
    folder.mkdir()
    (folder / "app.py").write_text("x = 1\n", encoding="utf-8")
    return folder


def _write_turns(app, sid: str, turns: list[tuple[str, str, dict]]) -> None:
    """Question + reply lines the way the daemon writes them (stamped)."""
    for turn_id, question, extra in turns:
        S.append_lines(app.state.platform.engine, sid, [{"role": "user", "content": question,
                                                         "turnId": turn_id, **extra}])
        S.append_lines(app.state.platform.engine, sid, [{"role": "assistant", "content": f"answer to {question}",
                                                         "turnId": turn_id}])


# --------------------------------------------------------------------------- #
# Next step: follow-ups for a Build turn
# --------------------------------------------------------------------------- #


class _Complete:
    """Records ``router.complete`` calls, the hold and pin each one ran under."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.calls: list[dict] = []

    async def __call__(self, **kw):
        from iron_jarvis.providers.hold import current_hold

        hold = current_hold()
        self.calls.append({**kw, "hold": (getattr(hold, "provider", None), getattr(hold, "model", None))})
        return SimpleNamespace(
            provider=kw.get("provider"), model=kw.get("model"), reason="explicit",
            response=LLMResponse(text=self.text, usage={"input_tokens": 30, "output_tokens": 9}),
        )


async def test_followups_are_off_by_default_and_make_no_model_call(app, work):
    sid = new_session(app, work)["id"]
    _write_turns(app, sid, [("t1", "add a test", {})])
    rec = _Complete('["What next?"]')
    app.state.platform.router.complete = rec
    status, out = await asgi(app, "POST", f"/build/sessions/{sid}/followups", {})
    assert status == 200 and out == {"suggestions": [], "reason": "off"}
    assert rec.calls == []


async def test_followups_on_use_the_sessions_own_model_pinned_billed_and_at_most_two(app, work):
    status, _ = await asgi(app, "PUT", "/settings/values", {"values": {"chat_followups": True}})
    assert status == 200
    sid = new_session(app, work)["id"]
    _write_turns(app, sid, [("t1", "add a test", {})])
    rec = _Complete('["Run the tests?", "Add a fixture?", "Commit it?"]')
    app.state.platform.router.complete = rec
    status, out = await asgi(app, "POST", f"/build/sessions/{sid}/followups", {})
    assert status == 200, out
    assert out == {"suggestions": ["Run the tests?", "Add a fixture?"], "reason": ""}
    call = rec.calls[0]
    assert (call["provider"], call["model"]) == CLAUDE, "the session's own model"
    assert call["pin"] is True and call["tools"] == [], "pinned, no tools"
    assert call["hold"] == CLAUDE, "under the session's model hold"
    with session_scope(app.state.platform.engine) as db:
        billed = [r for r in db.exec(select(AgentRun)) if r.thread_id == sid]
    assert [(r.input_tokens, r.output_tokens) for r in billed] == [(30, 9)], "billed to the session"


async def test_followups_wait_for_a_reply_and_never_for_the_demo_model(app, work):
    await asgi(app, "PUT", "/settings/values", {"values": {"chat_followups": True}})
    rec = _Complete('["x?"]')
    app.state.platform.router.complete = rec
    sid = new_session(app, work)["id"]
    S.append_lines(app.state.platform.engine, sid, [{"role": "user", "content": "q", "turnId": "t1"}])
    status, out = await asgi(app, "POST", f"/build/sessions/{sid}/followups", {})
    assert out["suggestions"] == [] and out["reason"]
    demo = new_session(app, work, provider="mock", model="")["id"]
    _write_turns(app, demo, [("t1", "q", {})])
    status, out = await asgi(app, "POST", f"/build/sessions/{demo}/followups", {})
    assert out["suggestions"] == []
    assert rec.calls == []


# --------------------------------------------------------------------------- #
# Branch from a message
# --------------------------------------------------------------------------- #


async def test_branch_from_a_reply_starts_a_new_session_and_leaves_the_old_one_untouched(app, work):
    sid = new_session(app, work, approval_mode="always_ask", reasoning="high")["id"]
    _write_turns(app, sid, [("t1", "first", {}), ("t2", "second", {})])
    before = thread_row(app, sid)
    status, view = await asgi(app, "POST", f"/build/sessions/{sid}/branch", {"turn_id": "t1", "at": "reply"})
    assert status == 201, view
    assert view["id"] != sid and view["folder"] == str(work)
    assert [(m["role"], m["content"]) for m in view["messages"]] == [
        ("user", "first"), ("assistant", "answer to first"),
    ]
    assert view["setup"]["provider"] == CLAUDE[0] and view["setup"]["approval_mode"] == "always_ask"
    assert view["setup"]["reasoning"] == "high"
    assert view["branched_from"]["thread_id"] == sid and view["branched_from"]["turn_id"] == "t1"
    assert "draft" not in view
    after = thread_row(app, sid)
    assert (after.messages_json, after.setup_json, after.title) == (
        before.messages_json, before.setup_json, before.title,
    ), "nothing of the old session changed"


async def test_branch_from_a_question_holds_the_conversation_before_it_and_hands_back_its_words(app, work):
    sid = new_session(app, work)["id"]
    _write_turns(app, sid, [
        ("t1", "first", {}),
        ("t2", "look at this", {"attachmentPaths": ["C:\\up\\shot.png"], "filePaths": ["src/a.py"],
                                "skill": "tidy"}),
    ])
    status, view = await asgi(app, "POST", f"/build/sessions/{sid}/branch", {"turn_id": "t2", "at": "question"})
    assert status == 201, view
    assert [m["content"] for m in view["messages"]] == ["first", "answer to first"]
    assert view["draft"] == {"text": "look at this", "attachments": ["C:\\up\\shot.png"],
                             "picked_files": ["src/a.py"], "skill": "tidy"}


async def test_branch_refuses_a_message_that_is_not_there_or_a_reply_not_written_yet(app, work):
    sid = new_session(app, work)["id"]
    S.append_lines(app.state.platform.engine, sid, [{"role": "user", "content": "q", "turnId": "t1"}])
    status, out = await asgi(app, "POST", f"/build/sessions/{sid}/branch", {"turn_id": "nope"})
    assert status == 404
    status, out = await asgi(app, "POST", f"/build/sessions/{sid}/branch", {"turn_id": "t1", "at": "reply"})
    assert status == 400 and "no reply yet" in out["detail"]


needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


@needs_git
async def test_a_branch_sessions_branch_gets_its_own_new_branch_from_the_same_base(app, tmp_path, monkeypatch):
    from tests.test_build_git_v1375 import git, make_repo, new_branch_session, no_real_gh

    no_real_gh(monkeypatch)
    repo = make_repo(tmp_path / "proj")
    old = await new_branch_session(app, repo)
    sid, og = old["id"], old["git"]
    wt_old = Path(og["worktree"])
    (wt_old / "app.py").write_bytes(b"x = 2\n")  # uncommitted work in the old worktree
    _write_turns(app, sid, [("t1", "first", {})])
    before = thread_row(app, sid).setup_json
    status, view = await asgi(app, "POST", f"/build/sessions/{sid}/branch", {"turn_id": "t1"})
    assert status == 201, view
    ng = view["git"]
    assert ng["branch"].startswith("ij/") and ng["branch"] != og["branch"]
    assert ng["worktree"] != og["worktree"], "never the same worktree"
    assert ng["base"] == og["base"] == "main"
    wt_new = Path(ng["worktree"])
    assert git("rev-parse", "HEAD", cwd=wt_new) == git("rev-parse", "main", cwd=repo), "from the base's tip"
    assert (wt_new / "app.py").read_bytes() == b"x = 1\n", "the old worktree's work is not carried"
    assert (wt_old / "app.py").read_bytes() == b"x = 2\n" and thread_row(app, sid).setup_json == before
    assert view["branched_from"]["branch"] == ng["branch"]


# --------------------------------------------------------------------------- #
# continuation turns carry the Browser pane's address
# --------------------------------------------------------------------------- #


async def test_a_continuation_turn_carries_the_browser_panes_address(app, work):
    from iron_jarvis.daemon.routes import build as build_routes

    sid = new_session(app, work, approval_mode="yolo")["id"]
    script = wire(app, Script(["Done.", "Carried on.", "Again."]))
    status, raw = await open_turn(app, sid, {"text": "check the page", "turn_id": "pv-1",
                                             "preview_url": "http://localhost:5173/cart"})
    assert status == 200, raw
    assert setup_of(app, sid)["preview_url"] == "http://localhost:5173/cart"
    lg = await build_routes.start_build_turn(app.state.d, sid, build_routes.CONTINUE_TEXT, "pv-cont",
                                             continuation=True)
    await until(lambda: lg.status != "running")
    assert "Browser pane is showing http://localhost:5173/cart" in script.calls[-1]["system"]
    # A message the screen sends with no address clears it (the pane shows none).
    status, raw = await open_turn(app, sid, {"text": "no page now", "turn_id": "pv-2"})
    assert "preview_url" not in setup_of(app, sid)
    # Never a public address, even stored by hand.
    S.update(app.state.platform.engine, sid, setup={"preview_url": "https://example.com/"})
    assert "preview_url" not in setup_of(app, sid)


# --------------------------------------------------------------------------- #
# rewind: the question's files go back; committed work is said
# --------------------------------------------------------------------------- #


async def test_rewind_hands_back_the_questions_pictures_files_and_skill(app, work):
    sid = new_session(app, work)["id"]
    _write_turns(app, sid, [("t1", "look at this", {"attachmentPaths": ["C:\\up\\a.png", "C:\\up\\b.png"],
                                                    "filePaths": ["app.py"], "skill": "tidy"})])
    status, preview = await asgi_get(app, f"/build/sessions/{sid}/rewind?to=t1")
    assert status == 200 and preview["attachments"] == ["C:\\up\\a.png", "C:\\up\\b.png"]
    status, out = await asgi(app, "POST", f"/build/sessions/{sid}/rewind", {"to": "t1"})
    assert status == 200 and out["rewound"] is True
    assert out["question"] == "look at this"
    assert out["attachments"] == ["C:\\up\\a.png", "C:\\up\\b.png"]
    assert out["picked_files"] == ["app.py"] and out["skill"] == "tidy"


@needs_git
async def test_rewinding_committed_work_says_the_commits_stay_and_the_files_come_back_uncommitted(
    app, tmp_path, monkeypatch,
):
    from tests.test_build_git_v1375 import git, make_repo, new_branch_session, no_real_gh

    no_real_gh(monkeypatch)
    repo = make_repo(tmp_path / "proj")
    view = await new_branch_session(app, repo, approval_mode="yolo")
    sid, wt = view["id"], Path(view["git"]["worktree"])
    wire(app, Script([[ToolCall(id="w1", name="Write", arguments={"file_path": "app.py", "content": "x = 7\n"})],
                      "Done."]))
    status, raw = await open_turn(app, sid, {"text": "set x to seven", "turn_id": "rc-1"})
    assert status == 200
    assert (wt / "app.py").read_bytes().replace(b"\r\n", b"\n") == b"x = 7\n"
    status, done = await asgi(app, "POST", f"/build/sessions/{sid}/git/commit", {"message": "Set x to seven"})
    assert status == 200, done
    status, preview = await asgi_get(app, f"/build/sessions/{sid}/rewind?to=rc-1")
    assert status == 200, preview
    assert preview["committed"]["count"] == 1 and preview["committed"]["branch"] == view["git"]["branch"]
    note = preview["commits_note"]
    assert "Set x to seven" in note and "commits stay on the branch" in note and "not committed" in note
    status, out = await asgi(app, "POST", f"/build/sessions/{sid}/rewind", {"to": "rc-1"})
    assert status == 200 and out["rewound"] is True and out["commits_note"] == note
    assert git("log", "-1", "--format=%s", cwd=wt) == "Set x to seven", "the commit stays"
    assert (wt / "app.py").read_bytes() == b"x = 1\n"
    assert "app.py" in git("status", "--porcelain", cwd=wt), "the file shows as not committed"


async def test_a_rewind_with_nothing_committed_says_nothing_about_commits(app, work):
    sid = new_session(app, work)["id"]
    _write_turns(app, sid, [("t1", "q", {})])
    status, preview = await asgi_get(app, f"/build/sessions/{sid}/rewind?to=t1")
    assert "commits_note" not in preview and "committed" not in preview


# --------------------------------------------------------------------------- #
# a session read while Compact runs
# --------------------------------------------------------------------------- #


async def test_a_session_read_mid_compact_says_it_is_summarizing(app, work):
    from iron_jarvis.daemon.routes import build as build_routes

    sid = new_session(app, work)["id"]
    status, view = await asgi_get(app, f"/build/sessions/{sid}")
    assert view["compacting"] is False
    build_routes._COMPACTING.add(sid)
    try:
        status, view = await asgi_get(app, f"/build/sessions/{sid}")
        assert view["compacting"] is True
    finally:
        build_routes._COMPACTING.discard(sid)
