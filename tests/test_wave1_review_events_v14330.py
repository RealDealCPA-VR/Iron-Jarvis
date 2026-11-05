"""v1.433.0 (Wave 1, A10) — answering a review SAYS so on the event bus.

``POST /reviews/{id}/approve`` and ``/reject`` published nothing, while the
bell (``NotificationBell`` — a ``review.requested`` stays pending until a later
``review.*`` event for the same session) and the mood orb (``alert`` on an
unresolved ``review.requested``) wait for one. So after the person answered,
the bell's badge and the orb's rose "alert" stayed on until a reload.

Now each route publishes ``review.approved`` / ``review.rejected`` tagged with
the SESSION's id — the key both dashboard readers clear by (``reviewKey`` /
``sid``: ``e.session_id``). Neither is a default alert (the person just did
it), and both have words in the one event-words table.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from iron_jarvis.comm.notifier import DEFAULT_ALERT_EVENTS, EVENT_WORDS
from iron_jarvis.core.events import EventType
from iron_jarvis.daemon.app import create_app


def _events(app, etype: str) -> list:
    return [e for e in app.state.platform.event_bus.history if e.type == etype]


def _fake_review(app, monkeypatch, *, sid: str = "s_rev1", merged: str = "merged abc123") -> dict:
    """Stand the orchestrator's review calls in (the git work is tested
    elsewhere); what is pinned here is that the ROUTE says it happened."""
    orch = app.state.d.orchestrator
    reviews = {sid: SimpleNamespace(branch="ironjarvis/session-x")}
    calls: list[str] = []
    monkeypatch.setattr(orch, "get_review", lambda s: reviews.get(s))

    def _approve(s):
        calls.append(f"approve:{s}")
        reviews.pop(s, None)
        return merged

    def _reject(s):
        calls.append(f"reject:{s}")
        reviews.pop(s, None)

    monkeypatch.setattr(orch, "approve_review", _approve)
    monkeypatch.setattr(orch, "reject_review", _reject)
    return {"sid": sid, "calls": calls}


def test_approving_a_review_publishes_review_approved_for_that_session(tmp_path, monkeypatch):
    app = create_app(str(tmp_path))
    fake = _fake_review(app, monkeypatch)
    with TestClient(app) as c:
        assert _events(app, "review.approved") == []
        r = c.post(f"/reviews/{fake['sid']}/approve")
        assert r.status_code == 200, r.text
        assert r.json() == {"merged": "merged abc123"}
    assert fake["calls"] == [f"approve:{fake['sid']}"]
    got = _events(app, "review.approved")
    assert len(got) == 1, got
    assert got[0].session_id == fake["sid"], "the bell and the orb clear by the session id"
    assert got[0].payload == {"branch": "ironjarvis/session-x", "merged": "merged abc123"}
    assert _events(app, "review.rejected") == []


def test_rejecting_a_review_publishes_review_rejected_for_that_session(tmp_path, monkeypatch):
    app = create_app(str(tmp_path))
    fake = _fake_review(app, monkeypatch)
    with TestClient(app) as c:
        r = c.post(f"/reviews/{fake['sid']}/reject")
        assert r.status_code == 200 and r.json() == {"status": "rejected"}, r.text
    got = _events(app, "review.rejected")
    assert len(got) == 1 and got[0].session_id == fake["sid"], got
    assert got[0].payload == {"branch": "ironjarvis/session-x"}
    assert _events(app, "review.approved") == []


def test_no_review_is_a_404_and_publishes_nothing(tmp_path, monkeypatch):
    app = create_app(str(tmp_path))
    _fake_review(app, monkeypatch)
    with TestClient(app) as c:
        assert c.post("/reviews/nope/approve").status_code == 404
        assert c.post("/reviews/nope/reject").status_code == 404
    assert _events(app, "review.approved") == [] and _events(app, "review.rejected") == []


def test_the_event_names_are_known_and_worded_and_not_default_alerts():
    assert EventType.REVIEW_APPROVED == "review.approved"
    assert EventType.REVIEW_REJECTED == "review.rejected"
    # The bell matches `review.` and treats anything but `requested` as an
    # answer; a name outside that family would never clear it.
    for name in (EventType.REVIEW_APPROVED, EventType.REVIEW_REJECTED):
        assert name.startswith("review.") and name != EventType.REVIEW_REQUESTED
        assert name in EVENT_WORDS, f"{name} has no words in the event-words table"
        assert name not in DEFAULT_ALERT_EVENTS, f"{name} would ping the phone for what the user just did"


# --------------------------------------------------------------------------
# End to end on a real git-native session (the review.requested -> answer pair)
# --------------------------------------------------------------------------


def _init_git_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)

    def g(*args: str) -> None:
        subprocess.run(
            ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
            cwd=repo,
            check=True,
            capture_output=True,
        )

    g("init", "-q")
    (repo / ".gitignore").write_text(".ironjarvis/\n", encoding="utf-8")
    (repo / "README.md").write_text("hello", encoding="utf-8")
    g("add", "-A")
    g("commit", "-qm", "base")


def test_a_real_review_is_requested_then_answered_under_the_same_session_id(tmp_path, monkeypatch):
    repo = tmp_path / "proj"
    _init_git_repo(repo)
    monkeypatch.setenv("IRONJARVIS_GIT_NATIVE", "1")
    app = create_app(str(repo))
    with TestClient(app) as c:
        sid = c.post("/sessions", json={"task": "create a summary file", "wait": True}).json()["id"]
        assert c.get("/diagnostics").json()["pending_reviews"] == 1
        r = c.post(f"/reviews/{sid}/reject")
        assert r.status_code == 200, r.text
        assert c.get("/diagnostics").json()["pending_reviews"] == 0
    asked = _events(app, "review.requested")
    answered = _events(app, "review.rejected")
    assert [e.session_id for e in asked] == [sid], asked
    assert [e.session_id for e in answered] == [sid], answered
