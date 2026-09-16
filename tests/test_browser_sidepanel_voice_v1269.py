"""v1.269.0 — the composer owns the model icon and a mic/send button; dictation rides
the panel socket into the app's OWN voice engine; the top is the mark.

THE REQUEST. "Model selection on the bottom right of the chat, with a simple
icon … another small icon to the right that offers voice to text and send all
in one icon — a mike when nothing is typed, an arrow when the user starts
typing. Clean up the view at the top; an IJ icon in the style of the overview
page would suffice."

WHAT IS PINNED.

* **The vocabulary**: the ``voice`` action and the ``transcript`` event exist,
  are generated into the add-on, and the panel has a branch for each.
* **One voice answer for every surface**: ``voice_capability`` is what
  ``GET /voice/status`` returns AND what the panel's ``models`` frame carries as
  ``voice`` — driven through the REAL app.
* **Dictation, offline**: with a bundled speech model the daemon streams partials
  as chunks arrive and settles the text at stop — driven through the real panel
  with a stand-in recogniser that behaves like Vosk's.
* **Dictation, through an HTTP backend**: chunks accumulate, stop wraps them in a
  WAV and hands ONE clip to ``transcribe_clip`` (the route's own lifted body),
  and the words come back final.
* **Refusals are sentences**: no engine → ``voice_unavailable`` with the app's own
  hint; a chunk too large → ``voice_failed``; cancel drops the session; a chunk
  with no session is ignored.
* **The page**: the header carries the mark (the app's reactor drawing) and no
  select; the composer's bottom row carries the model select over an icon and
  the one button with three faces; ``composerMode`` lifted and run under node.
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

from iron_jarvis.browser import protocol as P
from iron_jarvis.core.turns import TURNS
from tests._fakes.panel_harness import RealApp, _headers, _wait_for

ROOT = Path(__file__).resolve().parents[1]
ADDON = ROOT / "extensions" / "chrome" / "src"
PANEL_HTML = ADDON / "sidepanel" / "sidepanel.html"
PANEL_TS = ADDON / "sidepanel" / "sidepanel.ts"
PROTOCOL_TS = ADDON / "protocol.ts"

requires_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


@pytest.fixture(autouse=True)
def _clean_registry():
    for tid in TURNS.running_ids():
        TURNS.release(tid)
    yield
    for tid in TURNS.running_ids():
        TURNS.release(tid)


def _src(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def _panel(app: RealApp):
    return app.platform.browser.backend.panel_handler.__self__


def _pcm(n_samples: int, value: int = 1000) -> str:
    return base64.b64encode(value.to_bytes(2, "little", signed=True) * n_samples).decode("ascii")


class _FakeRecognizer:
    """Behaves like ``vosk.KaldiRecognizer``: partials while words form, a result at a pause."""

    def __init__(self, model, rate):
        assert rate == 16000
        self.fed = 0

    def AcceptWaveform(self, pcm: bytes) -> bool:  # noqa: N802 — Vosk's own name
        self.fed += len(pcm)
        return self.fed >= 4000  # the second chunk ends a phrase

    def PartialResult(self) -> str:  # noqa: N802
        return json.dumps({"partial": "hello wor"})

    def Result(self) -> str:  # noqa: N802
        return json.dumps({"text": "hello world"})

    def FinalResult(self) -> str:  # noqa: N802
        return json.dumps({"text": "again"})


# --------------------------------------------------------------------------- #
# Vocabulary
# --------------------------------------------------------------------------- #


def test_the_vocabulary_and_the_generated_protocol():
    assert P.PANEL_ACTION_VOICE in P.ALL_PANEL_ACTIONS and P.PANEL_EVENT_TRANSCRIPT in P.ALL_PANEL_EVENTS
    generated = _src(PROTOCOL_TS)
    assert 'export const PANEL_ACTION_VOICE = "voice";' in generated
    assert 'export const PANEL_EVENT_TRANSCRIPT = "transcript";' in generated
    ts = _src(PANEL_TS)
    assert "case PANEL_EVENT_TRANSCRIPT:" in ts and "PANEL_ACTION_VOICE" in ts


# --------------------------------------------------------------------------- #
# One answer for every surface
# --------------------------------------------------------------------------- #


def test_voice_status_and_the_panels_models_frame_agree(tmp_path, monkeypatch):
    from iron_jarvis.daemon.routes import voice as voice_routes

    with RealApp(tmp_path, monkeypatch, access="interactive") as app:
        app.pair()
        deps = _panel(app).deps
        assert deps is not None, "the panel was installed without the app's deps"
        monkeypatch.setattr(deps, "_voice_backend", lambda: None)
        monkeypatch.setattr(deps, "_vosk_model_path", lambda: None)
        status = app.client.get("/voice/status", headers=_headers()).json()
        assert status["available"] is False and status["hint"]
        assert status == voice_routes.voice_capability(deps), "the route must answer with the shared function"

        app.panel.send(P.PANEL_ACTION_OPEN)
        frame = app.panel.wait_for(P.PANEL_EVENT_MODELS, "open never emitted the model list")
        assert frame["voice"] == {"available": False, "backend": None, "hint": status["hint"]}

        app.panel.send(P.PANEL_ACTION_VOICE, op="start")
        err = app.panel.wait_for(P.PANEL_EVENT_ERROR, "no refusal without an engine")
        assert err["reason"] == "voice_unavailable" and err["text"] == status["hint"]


# --------------------------------------------------------------------------- #
# Dictation, offline (streaming)
# --------------------------------------------------------------------------- #


def test_offline_dictation_streams_partials_and_settles_at_stop(tmp_path, monkeypatch):
    with RealApp(tmp_path, monkeypatch, access="interactive") as app:
        app.pair()
        deps = _panel(app).deps
        monkeypatch.setattr(deps, "_voice_backend", lambda: None)
        monkeypatch.setattr(deps, "_vosk_model_path", lambda: r"C:\models\vosk")
        monkeypatch.setattr(deps, "_vosk_model", lambda: object())
        monkeypatch.setitem(sys.modules, "vosk", types.SimpleNamespace(KaldiRecognizer=_FakeRecognizer))

        app.panel.send(P.PANEL_ACTION_OPEN)
        frame = app.panel.wait_for(P.PANEL_EVENT_MODELS, "open never emitted the model list")
        assert frame["voice"]["available"] is True and frame["voice"]["backend"] == "local"

        app.panel.send(P.PANEL_ACTION_VOICE, op="start")
        started = app.panel.wait_for(P.PANEL_EVENT_TRANSCRIPT, "start was not acknowledged")
        assert started["listening"] is True and started["final"] is False

        app.panel.send(P.PANEL_ACTION_VOICE, op="chunk", pcm_b64=_pcm(1000))
        partial = app.panel.wait_for(P.PANEL_EVENT_TRANSCRIPT, "no partial", nth=2)
        assert partial == {"text": "", "partial": "hello wor", "final": False, "backend": "local"}

        app.panel.send(P.PANEL_ACTION_VOICE, op="chunk", pcm_b64=_pcm(1000))
        settled = app.panel.wait_for(P.PANEL_EVENT_TRANSCRIPT, "no settled phrase", nth=3)
        assert settled == {"text": "hello world", "partial": "", "final": False, "backend": "local"}

        app.panel.send(P.PANEL_ACTION_VOICE, op="stop")
        final = app.panel.wait_for(P.PANEL_EVENT_TRANSCRIPT, "no final", nth=4)
        assert final == {"text": "hello world again", "partial": "", "final": True, "backend": "local"}
        assert _panel(app)._voice_session is None


# --------------------------------------------------------------------------- #
# Dictation through an HTTP backend (one clip at stop)
# --------------------------------------------------------------------------- #


def test_clip_dictation_hands_one_wav_to_the_shared_transcriber(tmp_path, monkeypatch):
    from iron_jarvis.daemon.routes import voice as voice_routes

    handed: list[dict] = []

    async def fake_transcribe_clip(d, *, audio_b64, mime="audio/webm", language=""):
        handed.append({"audio": base64.b64decode(audio_b64), "mime": mime, "d": d})
        return {"text": "  the quick fox  ", "backend": "openai", "model": "whisper-1"}

    monkeypatch.setattr(voice_routes, "transcribe_clip", fake_transcribe_clip)
    with RealApp(tmp_path, monkeypatch, access="interactive") as app:
        app.pair()
        deps = _panel(app).deps
        monkeypatch.setattr(deps, "_voice_backend", lambda: ("openai", "https://api.example/v1/audio/transcriptions", "k"))
        monkeypatch.setattr(deps, "_vosk_model_path", lambda: None)

        app.panel.send(P.PANEL_ACTION_VOICE, op="start")
        app.panel.wait_for(P.PANEL_EVENT_TRANSCRIPT, "start was not acknowledged")
        app.panel.send(P.PANEL_ACTION_VOICE, op="chunk", pcm_b64=_pcm(800, 7))
        app.panel.send(P.PANEL_ACTION_VOICE, op="chunk", pcm_b64=_pcm(800, 9))
        app.panel.send(P.PANEL_ACTION_VOICE, op="stop")
        final = app.panel.wait_for(P.PANEL_EVENT_TRANSCRIPT, "no final", nth=2)
    assert final == {"text": "the quick fox", "partial": "", "final": True, "backend": "openai"}
    assert len(handed) == 1 and handed[0]["mime"] == "audio/wav" and handed[0]["d"] is deps
    wav = handed[0]["audio"]
    assert wav[:4] == b"RIFF" and wav[8:12] == b"WAVE", "the clip must be a WAV the backends accept"
    assert len(wav) == 44 + 1600 * 2, "every PCM byte of both chunks, one 44-byte header"
    assert wav[44:46] == (7).to_bytes(2, "little", signed=True)


def test_a_refused_clip_is_one_sentence(tmp_path, monkeypatch):
    from fastapi import HTTPException

    from iron_jarvis.daemon.routes import voice as voice_routes

    async def refusing(d, **kw):
        raise HTTPException(status_code=424, detail="openai transcription failed: no such model")

    monkeypatch.setattr(voice_routes, "transcribe_clip", refusing)
    with RealApp(tmp_path, monkeypatch, access="interactive") as app:
        app.pair()
        deps = _panel(app).deps
        monkeypatch.setattr(deps, "_voice_backend", lambda: ("openai", "u", "k"))
        monkeypatch.setattr(deps, "_vosk_model_path", lambda: None)
        app.panel.send(P.PANEL_ACTION_VOICE, op="start")
        app.panel.wait_for(P.PANEL_EVENT_TRANSCRIPT, "start")
        app.panel.send(P.PANEL_ACTION_VOICE, op="chunk", pcm_b64=_pcm(100))
        app.panel.send(P.PANEL_ACTION_VOICE, op="stop")
        err = app.panel.wait_for(P.PANEL_EVENT_ERROR, "no refusal")
    assert err == {"text": "openai transcription failed: no such model", "reason": "voice_failed"}


# --------------------------------------------------------------------------- #
# Refusals and bookkeeping
# --------------------------------------------------------------------------- #


def test_cancel_drops_the_session_and_a_stray_chunk_is_ignored(tmp_path, monkeypatch):
    with RealApp(tmp_path, monkeypatch, access="interactive") as app:
        app.pair()
        deps = _panel(app).deps
        monkeypatch.setattr(deps, "_voice_backend", lambda: ("openai", "u", "k"))
        monkeypatch.setattr(deps, "_vosk_model_path", lambda: None)
        app.panel.send(P.PANEL_ACTION_VOICE, op="start")
        app.panel.wait_for(P.PANEL_EVENT_TRANSCRIPT, "start")
        assert _panel(app)._voice_session is not None
        app.panel.send(P.PANEL_ACTION_VOICE, op="cancel")
        _wait_for(lambda: _panel(app)._voice_session is None, "cancel did not drop the session")
        app.panel.send(P.PANEL_ACTION_VOICE, op="chunk", pcm_b64=_pcm(10))
        app.panel.send(P.PANEL_ACTION_OPEN)  # a frame that must still arrive after the stray chunk
        app.panel.wait_for(P.PANEL_EVENT_STATE, "the panel stopped answering after a stray chunk")
        assert not [e for e, _ in app.panel.events() if e == P.PANEL_EVENT_ERROR]


def test_an_oversized_chunk_ends_the_dictation_with_a_sentence(tmp_path, monkeypatch):
    with RealApp(tmp_path, monkeypatch, access="interactive") as app:
        app.pair()
        deps = _panel(app).deps
        monkeypatch.setattr(deps, "_voice_backend", lambda: ("openai", "u", "k"))
        monkeypatch.setattr(deps, "_vosk_model_path", lambda: None)
        turns = _panel(app)
        turns.VOICE_MAX_CHUNK_BYTES = 100
        app.panel.send(P.PANEL_ACTION_VOICE, op="start")
        app.panel.wait_for(P.PANEL_EVENT_TRANSCRIPT, "start")
        app.panel.send(P.PANEL_ACTION_VOICE, op="chunk", pcm_b64=_pcm(200))
        err = app.panel.wait_for(P.PANEL_EVENT_ERROR, "no refusal for an oversized chunk")
        assert err["reason"] == "voice_failed" and "larger" in err["text"]
        assert turns._voice_session is None


def test_wav_bytes_is_a_valid_16k_mono_container():
    from iron_jarvis.browser.panel import _wav_bytes

    wav = _wav_bytes(b"\x01\x00" * 16000)
    assert wav[:4] == b"RIFF" and wav[8:12] == b"WAVE"
    assert int.from_bytes(wav[22:24], "little") == 1, "mono"
    assert int.from_bytes(wav[24:28], "little") == 16000, "16 kHz"
    assert int.from_bytes(wav[34:36], "little") == 16, "16-bit"


# --------------------------------------------------------------------------- #
# The page
# --------------------------------------------------------------------------- #


def test_the_top_is_the_mark_and_the_controls_are_in_the_composer():
    html = _src(PANEL_HTML)
    header = re.search(r"<header>(.*?)</header>", html, flags=re.S)
    assert header
    assert "<select" not in header.group(1), "the model select left the header"
    assert 'class="mark"' in header.group(1) and "<line" in header.group(1), "no reactor mark up top"
    assert re.search(r'<span class="wordmark">IJ</span>', header.group(1))
    assert 'id="state-word"' in header.group(1) and 'id="access"' in header.group(1)
    composer = re.search(r'<div class="composer">(.*?)</div>\s*<!--', html, flags=re.S)
    assert composer
    body = composer.group(1)
    assert re.search(r'<select[^>]*id="model"', body), "the model select lives in the composer"
    assert body.index('id="model"') < body.index('id="send"'), "the model icon sits left of the mic/send"
    assert 'class="icon icon-mic"' in body and 'class="icon icon-send"' in body and 'class="icon icon-stop"' in body
    css = re.search(r"<style>(.*?)</style>", html, flags=re.S).group(1)
    assert '#send[data-mode="mic"] .icon-mic' in css and '#send[data-mode="send"] .icon-send' in css
    assert 'body[data-voice="listening"] #send' in css


def test_the_panel_decides_the_button_from_the_box_and_talks_only_to_the_daemon():
    ts = _src(PANEL_TS)
    assert re.search(r'if \(voiceState === "listening"\) \{\s*stopDictation\(\);', ts)
    assert "if (text) {\n    sendNow();\n  } else {\n    void startDictation();\n  }" in ts
    assert "webkitSpeechRecognition" not in ts and "SpeechRecognition" not in ts, (
        "dictation must not go through the browser vendor's speech service"
    )
    assert 'post(PANEL_ACTION_VOICE, { op: "chunk", pcm_b64: base64Of(pcm) })' in ts
    assert "NotAllowedError" in ts, "a blocked microphone must be named, not swallowed"


_MODE_HARNESS = """
__FN__
process.stdout.write(JSON.stringify([
  composerMode("", "idle"), composerMode("  ", "idle"), composerMode("hi", "idle"),
  composerMode("", "listening"), composerMode("hi", "listening"), composerMode("hi", "transcribing"),
]) + "\\n");
"""


@requires_node
def test_composer_mode_under_node(tmp_path):
    ts = _src(PANEL_TS)
    start = ts.index("export function composerMode")
    fn = ts[start : ts.index("\n}\n", start) + 3].replace("export function", "function")
    fn = re.sub(r'\): "mic" \| "send" \| "stop"', ")", fn)
    fn = fn.replace("(text: string, state: VoiceState)", "(text, state)")
    f = tmp_path / "mode.js"
    f.write_text(_MODE_HARNESS.replace("__FN__", fn), encoding="utf-8")
    proc = subprocess.run(["node", str(f)], capture_output=True, text=True, encoding="utf-8", timeout=60, env={**os.environ})
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout.strip().splitlines()[-1]) == ["mic", "mic", "send", "stop", "stop", "send"]


def test_the_shared_helpers_are_module_level_and_the_routes_are_wrappers():
    from iron_jarvis.daemon.routes import voice as voice_routes

    assert callable(getattr(voice_routes, "voice_capability", None))
    assert callable(getattr(voice_routes, "transcribe_clip", None))
    src = Path(voice_routes.__file__).read_text(encoding="utf-8")
    assert "return voice_capability(d)" in src and "return await transcribe_clip(" in src
