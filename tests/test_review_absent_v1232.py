"""v1.232.0 (audit Wave 6, U14) — ``GET /sessions/{id}/review`` without a review.

The dashboard's session page asks for the review on EVERY detail visit, and
"there is none" is the normal state (git-native off, or the review already
approved). The route answered 404 for it, so every visit logged a benign
failure. It now answers ``200 {"review": null}``; a session that does not
exist is still a 404, and a real review still comes back flat.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app


def test_no_review_is_200_review_null(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    created = client.post("/sessions", json={"task": "x", "wait": True}).json()
    res = client.get(f"/sessions/{created['id']}/review")
    assert res.status_code == 200, res.text
    assert res.json() == {"review": None}


def test_unknown_session_is_still_404(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    res = client.get("/sessions/no-such-session/review")
    assert res.status_code == 404
    assert res.json()["detail"] == "session not found"
