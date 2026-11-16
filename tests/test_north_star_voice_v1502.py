"""North-star 2.12 (D9, v1.502.0): dictation stays on the PC unless the owner picks otherwise.

``daemon/app._voice_backend`` sent dictation to OpenAI's audio API whenever an
OpenAI key was in the vault, and ``routes/voice.voice_capability`` let the
bundled offline model (Vosk) win only when there was no key. The owner added
that key for CHAT, not for voice. The owner's decision D9: the offline model
unless the owner chose a voice backend — a dedicated speech-to-text server
(``voice_transcribe_base_url``) or OpenAI picked for voice
(``voice_backend = "openai"``). And ``POST /voice/transcribe`` takes the
chat's Local only flag: under it a speech server off this network is refused
by name, nothing sent.
"""

from __future__ import annotations

import base64

import httpx
import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app

_AUDIO = base64.b64encode(b"RIFFfakewav" * 50).decode()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("IRONJARVIS_VOSK_MODEL", raising=False)
    monkeypatch.delenv("IRONJARVIS_TOKEN", raising=False)
    c = TestClient(create_app(str(tmp_path)))
    c.app.state.platform.secrets.set(
        "openai_api_key", "not-a-real-key-for-a-voice-test", kind="api_key"
    )
    return c


def _offline_model(client, tmp_path):
    model = tmp_path / "vosk-model"
    (model / "am").mkdir(parents=True)
    object.__setattr__(client.app.state.platform.config, "voice_vosk_model_path", str(model))


def _choose_openai_for_voice(client):
    # The setting the lead adds to the schema (``voice_backend``); set the way
    # the settings writer would leave it on the live config.
    object.__setattr__(client.app.state.platform.config, "voice_backend", "openai")


def _net(monkeypatch):
    calls: list[str] = []
    real = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.path.endswith("/audio/transcriptions"):
            return httpx.Response(200, json={"text": "hello"})
        return httpx.Response(404)

    def factory(*args, **kwargs):
        kwargs.pop("timeout", None)
        return real(transport=httpx.MockTransport(handler), timeout=5.0)

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    return calls


def test_an_openai_key_for_chat_does_not_take_dictation_from_the_offline_model(client, tmp_path):
    _offline_model(client, tmp_path)
    st = client.get("/voice/status").json()
    assert st["backend"] == "local" and st["mode"] == "stream", st


def test_with_no_offline_model_an_openai_key_alone_sends_nothing(client, monkeypatch):
    calls = _net(monkeypatch)
    st = client.get("/voice/status").json()
    assert st["available"] is False and st["backend"] is None, st
    r = client.post("/voice/transcribe", json={"audio_b64": _AUDIO, "mime": "audio/wav"})
    assert r.status_code == 424 and calls == []
    assert "OpenAI" in r.json()["detail"]


def test_openai_picked_for_voice_is_used(client, tmp_path, monkeypatch):
    _offline_model(client, tmp_path)
    _choose_openai_for_voice(client)
    assert client.get("/voice/status").json()["backend"] == "openai"
    calls = _net(monkeypatch)
    r = client.post("/voice/transcribe", json={"audio_b64": _AUDIO, "mime": "audio/wav"})
    assert r.status_code == 200, r.text
    assert calls and calls[0].startswith("https://api.openai.com/")


def test_under_local_only_a_speech_service_off_this_network_is_refused_by_name(client, monkeypatch):
    _choose_openai_for_voice(client)
    calls = _net(monkeypatch)
    r = client.post(
        "/voice/transcribe",
        json={"audio_b64": _AUDIO, "mime": "audio/wav", "local_only": True},
    )
    assert r.status_code == 409, r.text
    body = r.json()
    assert "OpenAI" in body["detail"] and "nothing was sent" in body["detail"]
    assert body["code"] == "local_only"
    assert calls == []


def test_under_local_only_a_speech_server_on_this_network_still_transcribes(client, monkeypatch):
    client.put("/settings", json={"values": {"voice_transcribe_base_url": "http://stt.local:8000"}})
    calls = _net(monkeypatch)
    r = client.post(
        "/voice/transcribe",
        json={"audio_b64": _AUDIO, "mime": "audio/wav", "local_only": True},
    )
    assert r.status_code == 200, r.text
    assert all(c.startswith("http://stt.local:8000/") for c in calls)


def test_a_saved_local_only_chat_refuses_even_when_the_flag_is_left_out(client, monkeypatch):
    client.put("/settings", json={"values": {"voice_transcribe_base_url": "https://stt.example.com"}})
    made = client.put(
        "/chat/threads/new",
        json={"messages": [{"role": "user", "content": "hi"}], "setup": {"local_only": True}},
    )
    assert made.status_code == 200, made.text
    calls = _net(monkeypatch)
    r = client.post(
        "/voice/transcribe",
        json={"audio_b64": _AUDIO, "mime": "audio/wav", "thread_id": made.json()["id"]},
    )
    assert r.status_code == 409, r.text
    assert "stt.example.com" in r.json()["detail"] and calls == []
