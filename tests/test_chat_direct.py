"""Direct /chat: real conversational replies + personas + attachments + vision."""

from __future__ import annotations

import base64

from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app


def _client(tmp_path):
    return TestClient(create_app(str(tmp_path)))


def _spy(client, captured, reply="hey there!"):
    platform = client.app.state.platform
    real_get = platform.providers.get

    def spy_get(p, m=None):
        adapter = real_get(p, m)

        async def canned(*, system, messages, tools):
            from iron_jarvis.providers.adapters.base import LLMResponse

            captured["system"] = system
            captured["messages"] = messages
            return LLMResponse(text=reply, tool_calls=[], usage={})

        adapter.complete = canned
        return adapter

    platform.providers.get = spy_get


def test_chat_replies_directly_with_history(tmp_path):
    client = _client(tmp_path)
    captured = {}
    _spy(client, captured)
    r = client.post(
        "/chat",
        json={"messages": [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello!"},
            {"role": "user", "content": "what did I just say?"},
        ]},
    )
    assert r.status_code == 200
    assert r.json()["reply"] == "hey there!"
    # FULL history reaches the model (real multi-turn, not recap-chaining).
    assert [m.content for m in captured["messages"]] == ["hi", "hello!", "what did I just say?"]


def test_personas_builtin_and_custom(tmp_path):
    client = _client(tmp_path)
    names = {p["name"] for p in client.get("/chat/personas").json()["personas"]}
    assert {"assistant", "developer", "accountant", "writer", "researcher"} <= names

    captured = {}
    _spy(client, captured)
    client.post("/chat", json={"messages": [{"role": "user", "content": "x"}], "persona": "accountant"})
    assert "CPA" in captured["system"]
    client.post("/chat", json={"messages": [{"role": "user", "content": "x"}],
                               "persona": "You are a pirate. Answer in pirate speak."})
    assert "pirate" in captured["system"]


def test_attachments_text_and_image(tmp_path):
    client = _client(tmp_path)
    captured = {}
    _spy(client, captured)
    # Upload a text file through the real upload endpoint.
    up = client.post("/documents/upload", json={
        "filename": "notes.txt",
        "content_b64": base64.b64encode(b"SECRET-NOTE-77").decode(),
    }).json()
    # Tiny valid PNG (1x1).
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
    )
    up2 = client.post("/documents/upload", json={
        "filename": "shot.png", "content_b64": base64.b64encode(png).decode(),
    }).json()
    r = client.post("/chat", json={
        "messages": [{"role": "user", "content": "summarize my file + image"}],
        "attachments": [up["path"], up2["path"]],
    })
    assert r.status_code == 200
    assert "SECRET-NOTE-77" in captured["system"]  # text extracted into context
    imgs = captured["messages"][-1].images
    assert imgs and imgs[0]["media_type"] == "image/png"  # image rides to vision
    assert r.json()["images"] == 1


def test_empty_messages_400(tmp_path):
    assert _client(tmp_path).post("/chat", json={"messages": []}).status_code == 400
