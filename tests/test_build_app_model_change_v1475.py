"""v1.475.0: a model or effort change in a Build › Code conversation leaves
ONE quiet line in it ("Model changed: X → Y, from your next message"), in the
session's own log, so every window and a reload show it. Nothing before the
conversation's first message; nothing for a change that changes nothing.
"""
from __future__ import annotations

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.hosted import store
from iron_jarvis.hosted import words as W
from tests._build_app_helpers_v1409 import client


@pytest.fixture()
def app(tmp_path):
    return create_app(str(tmp_path / "home"))


@pytest.fixture()
def work(tmp_path):
    d = tmp_path / "work"
    d.mkdir()
    return d


def _notices(view: dict) -> list[str]:
    return [e.get("text", "") for e in view["events"] if e.get("type") == "notice"]


def test_the_words_name_both_sides_and_when():
    rows = [{"id": "", "label": "Claude Code's default"}, {"id": "sonnet", "label": "Sonnet 5.5"},
            {"id": "opus", "label": "Opus 5.5"}]
    assert W.model_change_words("claude-code", rows, "sonnet", "opus", "", "", "now") == \
        "Model changed: Sonnet 5.5 → Opus 5.5."
    assert W.model_change_words("claude-code", rows, "sonnet", "opus", "", "", "next") == \
        "Model changed: Sonnet 5.5 → Opus 5.5, from your next message."
    assert W.model_change_words("claude-code", rows, "", "opus", "", "", "") == \
        "Model changed: Claude Code's default → Opus 5.5."
    assert W.model_change_words("claude-code", rows, "opus", "gone-id", "", "", "") == \
        "Model changed: Opus 5.5 → gone-id."
    assert W.model_change_words("claude-code", rows, "opus", "opus", "high", "low", "") == \
        "Effort changed: High → Low."
    assert W.model_change_words("claude-code", rows, "opus", "opus", "high", "", "") == \
        "Effort changed: High → Default."
    assert W.model_change_words("claude-code", rows, "sonnet", "opus", "", "high", "next") == \
        "Model changed: Sonnet 5.5 → Opus 5.5; effort: Default → High, from your next message."
    assert W.model_change_words("claude-code", rows, "opus", "opus", "high", "high", "now") == ""
    # Applied at different times: each part says its own (the review's case).
    assert W.model_change_words("claude-code", rows, "sonnet", "opus", "high", "low", "now", "next") ==         "Model changed: Sonnet 5.5 → Opus 5.5. Effort changed: High → Low, from your next message."
    assert W.model_change_words("claude-code", rows, "sonnet", "opus", "high", "low", "next", "next") ==         "Model changed: Sonnet 5.5 → Opus 5.5; effort: High → Low, from your next message."
    assert W.model_change_words("claude-code", rows, "opus", "opus", "high", "low", "", "next") ==         "Effort changed: High → Low, from your next message."
    # Claude Code's own short name reads as the one listed model of its family.
    named = [{"id": "claude-haiku-5-5", "label": "Haiku 5.5"}, {"id": "claude-sonnet-5-5", "label": "Sonnet 5.5"}]
    assert W.model_change_words("claude-code", named, "haiku", "claude-sonnet-5-5", "", "", "") ==         "Model changed: Haiku 5.5 → Sonnet 5.5."
    two = named + [{"id": "claude-haiku-4-8", "label": "Haiku 4.8"}]
    assert W.model_change_words("claude-code", two, "haiku", "claude-sonnet-5-5", "", "", "") ==         "Model changed: haiku → Sonnet 5.5."
    assert W.model_change_words("codex", named, "haiku", "x", "", "", "") == "Model changed: haiku → x."


async def test_a_change_in_a_conversation_leaves_one_line(app, work):
    row = store.create(app.state.platform.engine, harness="codex", folder=str(work), title="t", account="",
                       account_json="", model="", effort="", mode="ask", harness_session_id="thread-1")
    async with client(app) as c:
        r = await c.patch(f"/build/app/sessions/{row.id}", json={"effort": "high"})
        assert r.status_code == 200, r.text
        view = (await c.get(f"/build/app/sessions/{row.id}")).json()
        lines = [n for n in _notices(view) if "changed" in n]
        assert lines == ["Effort changed: Default → High."] or lines == [
            "Effort changed: Default → High, from your next message."
        ], lines
        # The same value again changes nothing and says nothing more.
        await c.patch(f"/build/app/sessions/{row.id}", json={"effort": "high"})
        view = (await c.get(f"/build/app/sessions/{row.id}")).json()
        assert len([n for n in _notices(view) if "changed" in n]) == 1


async def test_nothing_is_said_before_the_conversation_has_begun(app, work):
    row = store.create(app.state.platform.engine, harness="codex", folder=str(work), title="t", account="",
                       account_json="", model="", effort="", mode="ask", harness_session_id="")
    async with client(app) as c:
        r = await c.patch(f"/build/app/sessions/{row.id}", json={"effort": "high"})
        assert r.status_code == 200, r.text
        view = (await c.get(f"/build/app/sessions/{row.id}")).json()
        assert [n for n in _notices(view) if "changed" in n] == []
        assert view["session"]["effort"] == "high"


async def test_a_failing_line_never_refuses_the_change(app, work, monkeypatch):
    row = store.create(app.state.platform.engine, harness="codex", folder=str(work), title="t", account="",
                       account_json="", model="", effort="", mode="ask", harness_session_id="thread-1")

    def boom(*a, **k):
        raise RuntimeError("no words")

    monkeypatch.setattr(W, "model_change_words", boom)
    async with client(app) as c:
        r = await c.patch(f"/build/app/sessions/{row.id}", json={"effort": "low"})
        assert r.status_code == 200, r.text
        assert (await c.get(f"/build/app/sessions/{row.id}")).json()["session"]["effort"] == "low"


async def test_a_typed_model_change_writes_its_own_line_and_no_second_one(app, work):
    # The typed /model and /effort paths say the change themselves.
    row = store.create(app.state.platform.engine, harness="codex", folder=str(work), title="t", account="",
                       account_json="", model="", effort="", mode="ask", harness_session_id="thread-1")
    from iron_jarvis.hosted.service import service_for

    svc = service_for(app.state.platform)
    await svc.patch(row.id, {"effort": "high"}, say_change=False)
    async with client(app) as c:
        view = (await c.get(f"/build/app/sessions/{row.id}")).json()
    assert [n for n in _notices(view) if "changed" in n] == []
    assert view["session"]["effort"] == "high"
