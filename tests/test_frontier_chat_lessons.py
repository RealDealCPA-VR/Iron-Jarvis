"""The chat surface folds learned lessons into its system prompt."""

from __future__ import annotations

from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app


def _client(tmp_path):
    return TestClient(create_app(str(tmp_path)))


def test_learned_lesson_injected_into_chat_prompt(tmp_path, monkeypatch):
    client = _client(tmp_path)
    platform = client.app.state.platform

    # Seed a distinctive, high-weight lesson (a user preference) so
    # apply_to_prompt would fold it into any system prompt it touches.
    platform.learning.note_preference("CHAT-LESSON-MARKER-42")

    captured = {}
    real_get = platform.providers.get

    def spy_get(p, m=None):
        adapter = real_get(p, m)
        real_complete = adapter.complete

        async def spy(*, system, messages, tools):
            captured["system"] = system
            return await real_complete(system=system, messages=messages, tools=tools)

        adapter.complete = spy
        return adapter

    monkeypatch.setattr(platform.providers, "get", spy_get)

    r = client.post("/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 200
    assert "CHAT-LESSON-MARKER-42" in captured["system"]  # learned lesson folded in
