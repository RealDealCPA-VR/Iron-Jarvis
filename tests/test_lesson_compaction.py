"""Lesson compaction: deterministic dedup + model distillation (honest offline)."""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.learning.engine import _parse_distilled


def _texts(learning, scope=None):
    return [r.text for r in learning.lessons(scope=scope, limit=100)]


def test_dedup_collapses_task_echoes_and_identical_text(platform):
    learning = platform.learning
    # Three echoes of the same task — only the newest should survive.
    learning.reflect("s1", task="fix the tests", summary="ran pytest, all green", ok=True)
    learning.reflect("s2", task="fix the tests", summary="patched conftest and reran", ok=True)
    learning.reflect("s3", task="fix the tests", summary="final: suite green after fix", ok=True)
    # Two normalized-identical generic notes.
    learning.reflect("s4", summary="Prefer small commits.", ok=True)
    learning.reflect("s5", summary="prefer SMALL commits", ok=True)
    # A preference must never be touched.
    learning.note_preference("Always write tests first")

    removed = learning.dedup()
    assert removed == 3  # 2 task echoes + 1 identical text
    texts = _texts(learning)
    assert "Always write tests first" in texts
    assert sum("fix the tests" in t for t in texts) == 1
    assert sum("small commits" in t.lower() for t in texts) == 1


def test_dedup_keeps_feedback_lessons(platform):
    learning = platform.learning
    learning.record_feedback("s1", "down", "too verbose")
    learning.record_feedback("s2", "down", "too verbose")
    assert learning.dedup() == 0  # feedback is user signal — never auto-removed
    assert sum("too verbose" in t for t in _texts(learning)) == 2


def test_distill_replaces_raw_reflections(platform):
    learning = platform.learning
    for i in range(6):
        learning.reflect(f"s{i}", task=f"task {i}", summary=f"note {i}", ok=True)
    learning.note_preference("Keep replies short")

    async def fake_complete(prompt: str) -> str:
        assert "task 0" in prompt  # raws actually reach the model
        return json.dumps(["Break work into small verified steps.", "State assumptions up front."])

    res = asyncio.run(learning.distill(fake_complete))
    assert res == {"reviewed": 6, "distilled": 2, "removed": 6}
    texts = _texts(learning)
    assert "Break work into small verified steps." in texts
    assert "Keep replies short" in texts  # preference untouched
    assert not any(t.startswith("Worked well for") for t in texts)
    distilled = [r for r in learning.lessons(limit=100) if r.source == "distilled"]
    assert all(r.weight == 2 for r in distilled)


def test_distill_noop_when_too_few_raws(platform):
    learning = platform.learning
    learning.reflect("s1", task="only one", summary="note", ok=True)

    async def should_not_be_called(prompt: str) -> str:  # pragma: no cover
        raise AssertionError("model must not be called for a tiny pile")

    res = asyncio.run(learning.distill(should_not_be_called))
    assert res["distilled"] == 0 and "nothing to distill" in res["note"]


def test_distill_raises_on_unusable_reply(platform):
    learning = platform.learning
    for i in range(6):
        learning.reflect(f"s{i}", task=f"task {i}", summary=f"note {i}", ok=True)

    async def garbage(prompt: str) -> str:
        return "I cannot help with that."

    with pytest.raises(ValueError):
        asyncio.run(learning.distill(garbage))
    # Raw lessons must survive a failed pass — nothing was replaced.
    assert learning.raw_reflection_count() == 6


def test_parse_distilled_json_fence_and_bullets():
    assert _parse_distilled('["a", "b"]', max_out=8) == ["a", "b"]
    assert _parse_distilled('```json\n["x"]\n```', max_out=8) == ["x"]
    assert _parse_distilled("- one\n* two\n3. three", max_out=2) == ["one", "two"]
    assert _parse_distilled("", max_out=8) == []


def test_compact_endpoint_honest_offline(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    r = client.post("/lessons/compact")
    assert r.status_code == 200
    data = r.json()
    assert data["distilled"] == 0
    assert "no real model connected" in data["note"]
