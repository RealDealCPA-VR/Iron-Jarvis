"""The offline voice model loads OFF the event loop (v1.333.0, D10).

``/voice/stream`` called ``d._vosk_model()`` (a ~1 s disk read and native
decoder load) and built each Kaldi recognizer on the daemon's one loop, so a
dictation start froze every request, terminal and chat stream for that long.
Both now run in a worker thread, as the browser panel's dictation does.
"""

from __future__ import annotations

import json
import threading

from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import voice


class _Recognizer:
    def AcceptWaveform(self, pcm):  # noqa: N802 — Vosk's spelling
        return False

    def PartialResult(self):  # noqa: N802
        return json.dumps({"partial": ""})

    def FinalResult(self):  # noqa: N802
        return json.dumps({"text": "hello"})


def test_the_model_and_each_recognizer_are_built_off_the_loop(tmp_path, monkeypatch):
    monkeypatch.delenv("IRONJARVIS_TOKEN", raising=False)
    threads: dict[str, list[int]] = {"model": [], "recognizer": []}

    def load_model():
        threads["model"].append(threading.get_ident())
        return object()

    def build(model):
        threads["recognizer"].append(threading.get_ident())
        return _Recognizer()

    monkeypatch.setattr(voice, "_recognizer", build)
    app = create_app(str(tmp_path))
    app.state.d._vosk_model = load_model
    with TestClient(app) as client:
        loop_thread = client.portal.call(threading.get_ident)
        with client.websocket_connect("/voice/stream") as ws:
            ws.send_text('{"eof": true}')
            assert ws.receive_json() == {"text": "hello", "final": True}
    assert len(threads["model"]) == 1
    assert len(threads["recognizer"]) == 2  # the first, and the one after eof
    assert loop_thread not in threads["model"] + threads["recognizer"]
