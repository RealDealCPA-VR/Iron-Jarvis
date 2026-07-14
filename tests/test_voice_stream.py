"""Bundled OFFLINE voice (Vosk) — streaming WebSocket + gating.

The real transcription is proven with the model present (skipped otherwise, e.g.
in CI where the ~40MB model isn't downloaded). The GATING + graceful-degradation
tests always run: no model => the daemon reports a clip backend (not streaming),
and the /voice/stream socket closes cleanly with an honest error rather than
crashing. Off-by-default: a source install without the model behaves exactly as
before.
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app


def _no_model_env(monkeypatch):
    monkeypatch.delenv("IRONJARVIS_VOSK_MODEL", raising=False)
    monkeypatch.delenv("IRONJARVIS_TOKEN", raising=False)


def test_status_is_not_stream_without_a_model(tmp_path, monkeypatch):
    _no_model_env(monkeypatch)
    with TestClient(create_app(str(tmp_path))) as client:
        st = client.get("/voice/status").json()
        # No bundled model => never the streaming/local backend; falls through to
        # the existing clip backends (here: none configured).
        assert st.get("backend") != "local"
        assert st.get("mode") != "stream"


def test_stream_ws_closes_cleanly_without_a_model(tmp_path, monkeypatch):
    _no_model_env(monkeypatch)
    with TestClient(create_app(str(tmp_path))) as client:
        with client.websocket_connect("/voice/stream") as ws:
            msg = ws.receive_json()
            assert "error" in msg  # honest "no model", not a crash


def test_stream_transcribes_when_model_present(tmp_path, monkeypatch):
    """End-to-end streaming transcription — runs only where vosk + a model are
    available (a dev box or the packaged build), skipped otherwise."""
    pytest.importorskip("vosk")
    model = os.environ.get("IRONJARVIS_VOSK_MODEL")
    if not model or not os.path.isdir(os.path.join(model, "am")):
        pytest.skip("no IRONJARVIS_VOSK_MODEL pointing at a real model")
    monkeypatch.delenv("IRONJARVIS_TOKEN", raising=False)

    # A short 16kHz mono PCM tone (won't transcribe to words, but must drive the
    # recognizer through partial/final frames without error).
    import struct

    silence = struct.pack("<" + "h" * 16000, *([0] * 16000))  # 1s of quiet

    with TestClient(create_app(str(tmp_path))) as client:
        st = client.get("/voice/status").json()
        assert st["backend"] == "local" and st["mode"] == "stream"
        with client.websocket_connect("/voice/stream") as ws:
            for i in range(0, len(silence), 8000):
                ws.send_bytes(silence[i : i + 8000])
                got = ws.receive_json()
                assert "partial" in got or "text" in got
            ws.send_text('{"eof": true}')
            final = ws.receive_json()
            assert "text" in final and final.get("final") is True
