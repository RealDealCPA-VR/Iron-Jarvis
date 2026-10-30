"""v1.412.0 review — Chat › Code's "Choose…" folder MOVE and New session.

Choosing a folder before the first message MOVES the empty session: a new one
is made in that folder and the old one goes. The independent review found that
the old session was deleted blindly — a message sent while the move was in
flight (or from another window) was deleted with it. So:

* ``DELETE /build/sessions/{id}?only_if_empty=1`` refuses (409, ``code:
  "not_empty"``, plain words) a session that has any message, a waiting
  message, or a reply running or starting; the delete without the flag is
  exactly as before;
* Local only and a reply cap are carried AT CREATE (``POST /build/sessions``
  takes ``local_only`` and ``cap``, checked as the PATCH checks them) — a
  carry that fails refuses the create, so the old session is never deleted
  for a move that lost Local only.

Real app factory, real routes, real store.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import ChatThreadRecord
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import build as B
from iron_jarvis.harness import sessions as S


@pytest.fixture()
def app(tmp_path):
    a = create_app(str(tmp_path / "home"))
    a.state.platform.config.chat_files_root = str(tmp_path / "chat files")
    return a


@pytest.fixture()
def work(tmp_path) -> Path:
    folder = tmp_path / "work"
    folder.mkdir()
    return folder


def _new(c: TestClient, **body) -> dict:
    r = c.post("/build/sessions", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _exists(app, sid: str) -> bool:
    return S.get_row(app.state.platform.engine, sid) is not None


# --------------------------------------------------------------------------- #
# DELETE ?only_if_empty=1
# --------------------------------------------------------------------------- #


def test_only_if_empty_deletes_an_empty_session(app):
    with TestClient(app) as c:
        s = _new(c)
        r = c.delete(f"/build/sessions/{s['id']}?only_if_empty=1")
        assert r.status_code == 200, r.text
        assert not _exists(app, s["id"])


def test_only_if_empty_refuses_a_session_with_a_message(app):
    with TestClient(app) as c:
        s = _new(c)
        assert S.append_lines(app.state.platform.engine, s["id"], [
            {"role": "user", "content": "fix the login page", "turn_id": "t1"},
        ])["ok"]
        r = c.delete(f"/build/sessions/{s['id']}?only_if_empty=1")
        assert r.status_code == 409, r.text
        body = r.json()
        assert body["code"] == "not_empty" and isinstance(body["detail"], str) and body["detail"]
        assert _exists(app, s["id"]), "a session holding a message was deleted"
        # Without the flag the delete is exactly as before.
        assert c.delete(f"/build/sessions/{s['id']}").status_code == 200
        assert not _exists(app, s["id"])


def test_only_if_empty_refuses_a_session_with_a_waiting_message(app):
    with TestClient(app) as c:
        s = _new(c)
        with session_scope(app.state.platform.engine) as db:
            row = db.get(ChatThreadRecord, s["id"])
            row.queued_json = '[{"text": "and the footer"}]'
            db.add(row)
            db.commit()
        r = c.delete(f"/build/sessions/{s['id']}?only_if_empty=1")
        assert r.status_code == 409 and r.json()["code"] == "not_empty", r.text
        assert _exists(app, s["id"])


def test_only_if_empty_refuses_a_session_whose_reply_is_starting(app):
    with TestClient(app) as c:
        s = _new(c)
        B._STARTING.add(s["id"])
        try:
            r = c.delete(f"/build/sessions/{s['id']}?only_if_empty=1")
        finally:
            B._STARTING.discard(s["id"])
        assert r.status_code == 409, r.text
        assert _exists(app, s["id"])


# --------------------------------------------------------------------------- #
# the move carries Local only and the cap at create
# --------------------------------------------------------------------------- #


def test_create_carries_local_only_and_the_cap(app, work):
    with TestClient(app) as c:
        s = _new(c, folder=str(work), branch=False, local_only=True, cap={"minutes": 30, "usd": 2.5})
        assert s["setup"].get("local_only") is True, s["setup"]
        assert s["setup"].get("cap") == {"minutes": 30, "usd": 2.5}
        plain = _new(c, folder=str(work), branch=False)
        assert "local_only" not in plain["setup"] and "cap" not in plain["setup"]
        off = _new(c, folder=str(work), branch=False, local_only=False)
        assert "local_only" not in off["setup"]


def test_a_cap_the_create_cannot_keep_is_refused_whole(app, work):
    with TestClient(app) as c:
        before = len(c.get("/build/sessions").json().get("folders") or [])
        r = c.post("/build/sessions", json={"folder": str(work), "branch": False, "local_only": True,
                                             "cap": {"minutes": 0, "usd": "lots"}})
        assert r.status_code == 400 and r.json()["detail"] == B.CAP_REFUSED, r.text
        # Nothing was made: the old session stays the user's.
        assert len(c.get("/build/sessions").json().get("folders") or []) == before


# --------------------------------------------------------------------------- #
# a moved-from session on its own branch: the normal delete removes its worktree
# --------------------------------------------------------------------------- #


def test_only_if_empty_removes_an_empty_branch_sessions_worktree(app, tmp_path, monkeypatch):
    import shutil

    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    from tests.test_build_git_v1375 import make_repo, no_real_gh

    no_real_gh(monkeypatch)
    repo = make_repo(tmp_path / "proj")
    with TestClient(app) as c:
        s = _new(c, folder=str(repo), branch=True)
        wt = Path(s["git"]["worktree"])
        assert wt.is_dir() and wt != repo
        r = c.delete(f"/build/sessions/{s['id']}?only_if_empty=1")
        assert r.status_code == 200, r.text
        assert not wt.exists(), "the moved-from session's worktree was left beside the repository"
        assert r.json()["worktree"]["branch"].startswith("ij/")
        assert not _exists(app, s["id"])
