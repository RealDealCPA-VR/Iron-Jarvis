"""Voice speech-to-text: configurable model + endpoint + auto-discovery.

Regression coverage for the "model 'whisper-1' not found" bug: a custom
OpenAI-compatible endpoint (e.g. an Ollama LLM server, or any whisper server that
names its model differently) must no longer be locked to the single model id
`whisper-1`. The daemon now (a) honors an explicit `voice_transcribe_model`,
(b) auto-discovers a whisper model from the endpoint's /v1/models, and (c) can be
pointed at a DEDICATED speech-to-text endpoint independent of the chat endpoint.
"""

from __future__ import annotations

import base64

import httpx
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app

_AUDIO = base64.b64encode(b"RIFFfakewav" * 500).decode()


def _patch_httpx(monkeypatch, handler):
    """Route every AsyncClient in the voice route through a MockTransport."""
    real = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs.pop("timeout", None)
        return real(transport=httpx.MockTransport(handler), timeout=5.0)

    monkeypatch.setattr(httpx, "AsyncClient", factory)


# --- backend resolution ----------------------------------------------------- #
def test_dedicated_stt_endpoint_wins(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    client.put("/settings", json={"values": {
        "custom_base_url": "http://100.0.0.1:8003/v1",  # an Ollama LLM endpoint
        "voice_transcribe_base_url": "http://stt.local:8000",  # a real whisper server
    }})
    st = client.get("/voice/status").json()
    assert st["available"] is True
    assert st["backend"] == "stt"  # the dedicated endpoint wins over the chat one


def test_custom_status_is_honest(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    client.put("/settings", json={"values": {"custom_base_url": "http://100.0.0.1:8003/v1"}})
    st = client.get("/voice/status").json()
    assert st["backend"] == "custom"
    # Honest: tells the user a plain LLM endpoint (Ollama) can't transcribe.
    assert "whisper" in st["hint"].lower() and "ollama" in st["hint"].lower()


# --- model selection -------------------------------------------------------- #
def test_transcribe_autodiscovers_whisper_model(tmp_path, monkeypatch):
    """The endpoint's model list is queried; a whisper-looking id is used —
    NOT the hardcoded whisper-1."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [
                {"id": "qwen3.6:27b"},
                {"id": "Systran/faster-whisper-large-v3"},
            ]})
        if request.url.path.endswith("/audio/transcriptions"):
            return httpx.Response(200, json={"text": "hello world"})
        return httpx.Response(404)

    _patch_httpx(monkeypatch, handler)
    client = TestClient(create_app(str(tmp_path)))
    client.put("/settings", json={"values": {"custom_base_url": "http://mock.local/v1"}})
    r = client.post("/voice/transcribe", json={"audio_b64": _AUDIO, "mime": "audio/wav"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["text"] == "hello world"
    assert body["model"] == "Systran/faster-whisper-large-v3"  # discovered, not whisper-1


def test_transcribe_honors_explicit_model(tmp_path, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        # No /models discovery needed when a model is pinned.
        if request.url.path.endswith("/audio/transcriptions"):
            return httpx.Response(200, json={"text": "ok"})
        return httpx.Response(404)

    _patch_httpx(monkeypatch, handler)
    client = TestClient(create_app(str(tmp_path)))
    client.put("/settings", json={"values": {
        "custom_base_url": "http://mock.local/v1",
        "voice_transcribe_model": "my-custom-whisper",
    }})
    r = client.post("/voice/transcribe", json={"audio_b64": _AUDIO, "mime": "audio/wav"})
    assert r.status_code == 200, r.text
    assert r.json()["model"] == "my-custom-whisper"


def test_transcribe_all_models_missing_gives_guidance(tmp_path, monkeypatch):
    """When no model on the endpoint works, the 424 error is ACTIONABLE — the
    exact failure the user hit (whisper-1 not found on an Ollama endpoint)."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "qwen3.6:27b"}]})
        if request.url.path.endswith("/audio/transcriptions"):
            return httpx.Response(404, json={"error": {"message": "model not found"}})
        return httpx.Response(404)

    _patch_httpx(monkeypatch, handler)
    client = TestClient(create_app(str(tmp_path)))
    client.put("/settings", json={"values": {"custom_base_url": "http://mock.local/v1"}})
    r = client.post("/voice/transcribe", json={"audio_b64": _AUDIO, "mime": "audio/wav"})
    assert r.status_code == 424
    detail = r.json()["detail"].lower()
    assert "voice_transcribe_model" in detail or "voice_transcribe_base_url" in detail
    assert "transcribe" in detail or "whisper" in detail
