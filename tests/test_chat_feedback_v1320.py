"""v1.320.0 — a chat reply can be rated, and "Teach it your style" says how.

The user's report: the setup checklist said "Tell it how you like things in
Chat (or rate a finished session)"; the rating lived only on an agent run's
own page (Advanced-only in the Simple menu since v1.318.0), and the step's
button opened Chat, which had nothing to rate with. Now:

- ``POST /chat/feedback`` stores a 👍 / 👎 on a chat reply through the SAME
  learning engine as a session rating — it counts as teaching, and a note
  becomes a lesson. A bare 👎 is recorded WITHOUT the generic "be more
  careful" lesson (it says nothing about what to change).
- The step's words name what to do in Chat, never "a finished session".
- The example the dashboard types into Chat for the step really arms the
  preference tool.
"""

from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient
from sqlmodel import select

from iron_jarvis.core.db import session_scope
from iron_jarvis.daemon.app import create_app
from iron_jarvis.learning.models import FeedbackRecord, LessonRecord

ROOT = Path(__file__).resolve().parents[1]


def _client(tmp_path) -> TestClient:
    return TestClient(create_app(str(tmp_path)))


def _step(client, key: str) -> dict:
    return next(s for s in client.get("/onboarding").json()["checklist"] if s["key"] == key)


def _rows(client, model):
    with session_scope(client.app.state.platform.engine) as db:
        return list(db.exec(select(model)))


def _feedback_lessons(client) -> list[str]:
    return [lsn.text for lsn in _rows(client, LessonRecord) if lsn.source == "feedback"]


def test_a_thumbs_up_is_recorded_against_the_thread_and_counts_as_teaching(tmp_path):
    client = _client(tmp_path)
    assert _step(client, "teach_style")["done"] is False
    r = client.post("/chat/feedback", json={"rating": "up", "thread_id": "th_1"})
    assert r.status_code == 200, r.text
    assert r.json()["rating"] == "up" and r.json()["remembered"] is False
    rows = _rows(client, FeedbackRecord)
    assert [(f.session_id, f.rating) for f in rows] == [("chat:th_1", "up")]
    assert _feedback_lessons(client) == []  # a 👍 alone teaches no rule
    assert _step(client, "teach_style")["done"] is True


def test_a_thumbs_down_with_a_note_becomes_a_lesson(tmp_path):
    client = _client(tmp_path)
    r = client.post(
        "/chat/feedback",
        json={"rating": "down", "comment": "  shorter, with bullet points  ", "thread_id": ""},
    )
    assert r.status_code == 200, r.text
    assert r.json()["remembered"] is True
    assert [f.session_id for f in _rows(client, FeedbackRecord)] == ["chat"]
    (lesson,) = _feedback_lessons(client)
    assert "shorter, with bullet points" in lesson


def test_a_bare_thumbs_down_is_recorded_without_the_vague_lesson(tmp_path):
    client = _client(tmp_path)
    r = client.post("/chat/feedback", json={"rating": "down"})
    assert r.status_code == 200, r.text
    assert [f.rating for f in _rows(client, FeedbackRecord)] == ["down"]
    assert _feedback_lessons(client) == []


def test_anti_vacuity_a_session_rating_keeps_its_old_lesson(tmp_path):
    """The session page's 👎 is unchanged: the flag is chat's alone."""
    client = _client(tmp_path)
    client.post("/sessions/sess_x/feedback", json={"rating": "down"})
    assert len(_feedback_lessons(client)) == 1


def test_only_up_or_down_and_the_note_is_capped(tmp_path):
    client = _client(tmp_path)
    assert client.post("/chat/feedback", json={"rating": "meh"}).status_code == 422
    client.post("/chat/feedback", json={"rating": "down", "comment": "x" * 2000})
    (fb,) = _rows(client, FeedbackRecord)
    assert len(fb.comment) == 500


def test_the_step_says_what_to_do_in_chat_and_never_a_finished_session(tmp_path):
    detail = _step(_client(tmp_path), "teach_style")["detail"]
    assert "finished session" not in detail
    assert "👍" in detail and "From now on" in detail


def test_the_example_the_dashboard_types_arms_the_preference_tool():
    from iron_jarvis.tools.autoselect import select_auto_tools

    src = (ROOT / "dashboard" / "components" / "OnboardingWelcome.tsx").read_text(encoding="utf-8")
    m = re.search(r'export const TEACH_EXAMPLE = "([^"]+)"', src)
    assert m, "OnboardingWelcome.tsx must export TEACH_EXAMPLE"
    assert "remember_preference" in select_auto_tools(m.group(1)), m.group(1)
