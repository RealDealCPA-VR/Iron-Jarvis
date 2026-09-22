"""suite-03: the blackboard's "oldest first" must survive two posts in one clock tick.

On Windows, Python 3.12 (CI AND the shipped app, which bundles python312.dll)
reads the wall clock via GetSystemTimeAsFileTime: ``utcnow()`` ticks every
15.6 ms. Two agents posting inside one tick share a ``created_at``, and the old
tiebreak was the RANDOM ``bb_`` id, so the Team board listed them in coin-flip
order (14-23 of 60 back-to-back pairs flipped on 3.12; 0 on 3.13, whose clock
is 0.1 us — which is why a local 3.13 venv never saw it). The fix breaks the
tie on INSERTION order (the SQLite rowid), and ``.python-version`` pins the
local interpreter to CI's so the next clock-sensitive bug is seen here first.
"""

from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import text

from iron_jarvis.core.db import session_scope
from iron_jarvis.daemon.app import create_app
from iron_jarvis.terminals.backend import FakeBackend

_REPO = Path(__file__).resolve().parents[1]


def _app(tmp_path, monkeypatch):
    monkeypatch.setattr("iron_jarvis.terminals.session.default_backend", lambda: FakeBackend())
    app = create_app(str(tmp_path))
    return app, TestClient(app)


def _board_texts(client: TestClient, board_id: str) -> list[str]:
    r = client.get(f"/blackboard/{board_id}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["board_id"] == board_id  # served its own board, not a parent's
    return [rec["text"] for rec in body["records"]]


def test_a_same_tick_pair_lists_in_the_order_it_was_posted(tmp_path, monkeypatch):
    """Deterministic: force the tie, and make the random id point the WRONG way
    (the later post gets the smaller id) — the one case the old ``(created_at,
    id)`` order always got wrong, whatever interpreter runs the test."""
    app, client = _app(tmp_path, monkeypatch)
    store = app.state.platform.blackboard
    engine = app.state.platform.engine
    boards = [f"sess_tie_{n}" for n in range(5)]
    for board in boards:
        first = store.post(board, "run_a", "first")
        second = store.post(board, "run_b", "second")
        with session_scope(engine) as db:
            # One tick: both rows carry the first post's timestamp.
            db.exec(
                text("UPDATE blackboardrecord SET created_at = :at WHERE board_id = :b"),
                params={"at": db.exec(
                    text("SELECT created_at FROM blackboardrecord WHERE id = :i"),
                    params={"i": first.id},
                ).scalar_one(), "b": board},
            )
            # The later post sorts FIRST by id.
            db.exec(text("UPDATE blackboardrecord SET id = :n WHERE id = :o"),
                    params={"n": f"bb_zzzz{board}", "o": first.id})
            db.exec(text("UPDATE blackboardrecord SET id = :n WHERE id = :o"),
                    params={"n": f"bb_0000{board}", "o": second.id})
            db.commit()
        assert _board_texts(client, board) == ["first", "second"], board


def test_back_to_back_posts_list_oldest_first_on_the_real_clock(tmp_path, monkeypatch):
    """The real-world shape: two posts with no await between them, on whatever
    clock this interpreter has. Under 3.12 on Windows about a quarter of these
    pairs share a tick, so the old random tiebreak flipped some of 50."""
    app, client = _app(tmp_path, monkeypatch)
    store = app.state.platform.blackboard
    flipped = []
    for n in range(50):
        board = f"sess_live_{n}"
        store.post(board, "run_a", "first")
        store.post(board, "run_b", "second")
        store.post(board, "run_a", "third")
        if _board_texts(client, board) != ["first", "second", "third"]:
            flipped.append(board)
    assert not flipped, f"{len(flipped)}/50 boards listed out of posting order"


def test_the_local_interpreter_is_pinned_to_the_one_ci_and_the_installer_use():
    """A local venv on a newer Python hid this bug: uv honours .python-version,
    so it must name the SAME minor every CI setup-python step installs."""
    pinned = (_REPO / ".python-version").read_text(encoding="utf-8").strip()
    ci_versions = set()
    for wf in (_REPO / ".github" / "workflows").glob("*.yml"):
        ci_versions |= set(
            re.findall(r'python-version:\s*["\']?([0-9.]+)', wf.read_text(encoding="utf-8"))
        )
    assert ci_versions, "no setup-python step found in .github/workflows"
    assert ci_versions == {pinned}, (pinned, ci_versions)
