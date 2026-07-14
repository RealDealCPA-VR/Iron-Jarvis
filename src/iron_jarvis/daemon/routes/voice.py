"""Voice routes: dictation status + server-side transcription.

Moved verbatim from daemon/app.py's create_app; closure-local state is
reached through ``d`` (see the deps object built in create_app).
"""

from __future__ import annotations

import asyncio
import json

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from typing import Any

from ..app import _ws_token_ok
from ..schemas import TranscribeBody


def register(app: FastAPI, d) -> None:
    """Attach these routes to *app*; ``d`` is the create_app deps object."""
    @app.get("/voice/status")
    def voice_status() -> dict[str, Any]:
        """Whether server-side dictation works right now (and via what), so the
        dashboard can offer or grey the mic honestly instead of failing late."""
        backend = d._voice_backend()
        cfg = d.platform.config
        model = (getattr(cfg, "voice_transcribe_model", "") or "").strip()
        label = backend[0] if backend else None
        # Bundled OFFLINE Vosk streaming is the zero-config, no-key, no-internet
        # default that makes voice "just work" in the desktop app (the browser's
        # speech engine isn't available there). It wins over NOTHING and over a
        # `custom` chat endpoint that may not transcribe (e.g. Ollama) — but an
        # EXPLICIT higher-accuracy STT the user configured (a dedicated endpoint
        # or an OpenAI key) still takes precedence. `mode` tells the client the
        # transport: "stream" (WebSocket /voice/stream, live partials) vs "clip"
        # (POST /voice/transcribe).
        if label in (None, "custom") and d._vosk_model_path() is not None:
            return {
                "available": True,
                "backend": "local",
                "mode": "stream",
                "model": "vosk",
                "hint": "",
            }
        if backend is None:
            hint = (
                "Connect an OpenAI API key (Connections page), or set a dedicated "
                "speech-to-text endpoint (voice_transcribe_base_url) pointing at a "
                "whisper server, to enable voice dictation in the desktop app."
            )
        elif label == "custom":
            # Reusing the chat endpoint — which only transcribes if it's actually a
            # whisper server. Be honest so a failure isn't a surprise.
            hint = (
                "Using your custom chat endpoint for speech-to-text. If it isn't a "
                "whisper server (e.g. it's an Ollama LLM endpoint), set "
                "voice_transcribe_base_url to a dedicated STT server, or "
                "voice_transcribe_model to the model it serves."
            )
        else:
            hint = ""
        return {
            "available": backend is not None,
            "backend": label,
            "mode": "clip",
            "model": model or None,
            "hint": hint,
        }

    @app.post("/voice/transcribe")
    async def voice_transcribe(body: TranscribeBody) -> dict[str, Any]:
        """Transcribe one dictation clip. Explicit error (never fabricated text)
        when no speech-to-text backend is connected. 424, not 5xx: a missing
        backend is a config precondition, and the dashboard's api client shows a
        global "daemon error" banner for any 5xx — wrong signal here."""
        backend = d._voice_backend()
        if backend is None:
            raise HTTPException(
                status_code=424,
                detail=(
                    "No speech-to-text backend connected. Add an OpenAI API key on "
                    "the Connections page (or a custom endpoint serving "
                    "/v1/audio/transcriptions) to use voice in the desktop app."
                ),
            )
        label, url, key = backend
        import base64

        try:
            data = base64.b64decode(body.audio_b64, validate=False)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=f"invalid base64 audio: {exc}")
        if not data:
            raise HTTPException(status_code=400, detail="empty audio")
        if len(data) > d._VOICE_MAX_BYTES:
            raise HTTPException(status_code=413, detail="audio too large (25MB max)")

        import httpx  # lazy: keep import cost off the offline path

        mime = (body.mime or "audio/webm").split(";")[0].strip()
        ext = {
            "audio/webm": "webm",
            "audio/ogg": "ogg",
            "audio/mp4": "m4a",
            "audio/mpeg": "mp3",
            "audio/wav": "wav",
            "audio/x-wav": "wav",
        }.get(mime, mime.split("/")[-1] or "webm")
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        # Which transcription model(s) to try:
        #  * an explicit `voice_transcribe_model` config wins (no guessing);
        #  * OpenAI has a known ladder (current model + classic whisper-1);
        #  * a custom / dedicated-STT endpoint is DISCOVERED (GET /v1/models,
        #    pick ids that look like whisper/transcribe) then falls back to a
        #    ladder of the common self-hosted-server names — so "whisper-1" is no
        #    longer the only thing tried (that was the bug: an Ollama endpoint,
        #    or any server that names its model differently, only ever 404'd).
        override = (getattr(d.platform.config, "voice_transcribe_model", "") or "").strip()
        last_err = "transcription failed"
        async with httpx.AsyncClient(timeout=60.0) as client:
            if override:
                models = [override]
            elif label == "openai":
                models = ["gpt-4o-mini-transcribe", "whisper-1"]
            else:
                discovered: list[str] = []
                try:
                    models_url = url.rsplit("/audio/transcriptions", 1)[0] + "/models"
                    mr = await client.get(models_url, headers=headers, timeout=8.0)
                    if mr.status_code == 200:
                        for m in mr.json().get("data") or []:
                            mid = str(m.get("id") or "")
                            low = mid.lower()
                            if mid and ("whisper" in low or "transcrib" in low):
                                discovered.append(mid)
                except Exception:  # noqa: BLE001 — discovery is best-effort
                    pass
                ladder = [
                    "whisper-1", "whisper", "whisper-large-v3",
                    "whisper-large-v3-turbo", "Systran/faster-whisper-large-v3",
                    "distil-whisper-large-v3-en", "gpt-4o-mini-transcribe",
                ]
                seen: set[str] = set()
                models = [m for m in discovered + ladder if not (m in seen or seen.add(m))]
            for model in models:
                form: dict[str, Any] = {"model": model}
                if body.language.strip():
                    form["language"] = body.language.strip()
                try:
                    resp = await client.post(
                        url,
                        headers=headers,
                        data=form,
                        files={
                            "file": (
                                f"dictation.{ext}",
                                data,
                                mime or "application/octet-stream",
                            )
                        },
                    )
                except httpx.HTTPError as exc:
                    raise HTTPException(
                        status_code=424,
                        detail=f"{label} transcription unreachable: {exc}",
                    )
                if resp.status_code == 200:
                    try:
                        text = str(resp.json().get("text", "")).strip()
                    except Exception:  # noqa: BLE001 - some servers reply text/plain
                        text = resp.text.strip()
                    return {"text": text, "backend": label, "model": model}
                last_err = resp.text[:300]
                if resp.status_code not in (400, 404):
                    break  # auth/rate/server trouble — a different model won't help
        guidance = (
            ""
            if label == "openai"
            else (
                " — this endpoint didn't accept any known transcription model. "
                "Set `voice_transcribe_model` to the exact model your server "
                "serves, or point `voice_transcribe_base_url` at a dedicated "
                "speech-to-text server (a plain LLM endpoint like Ollama can't "
                "transcribe)."
            )
        )
        raise HTTPException(
            status_code=424,
            detail=f"{label} transcription failed: {last_err}{guidance}",
        )

    @app.websocket("/voice/stream")
    async def voice_stream(ws: WebSocket) -> None:
        """Real-time OFFLINE dictation (bundled Vosk). The client streams 16 kHz
        mono PCM16 binary frames; we feed a KaldiRecognizer and send back live
        ``{"partial": …}`` as words form and ``{"text": …, "final": true}`` at
        each pause — the browser-like feel, with NO key, NO server, NO internet.
        A ``{"eof": true}`` text control flushes the final hypothesis."""
        # BaseHTTPMiddleware can't see WS scope, so guard the ?token= here (same
        # as the /events + terminal sockets).
        if not _ws_token_ok(ws):
            await ws.close(code=1008)
            return
        model = d._vosk_model()
        if model is None:
            await ws.accept()
            await ws.send_json({"error": "offline speech model not available"})
            await ws.close()
            return
        import vosk

        rec = vosk.KaldiRecognizer(model, 16000)
        await ws.accept()
        try:
            while True:
                msg = await ws.receive()
                if msg["type"] == "websocket.disconnect":
                    break
                pcm = msg.get("bytes")
                if pcm:
                    # Kaldi decode is CPU-bound — off the event loop so a burst of
                    # frames never stalls the daemon.
                    done = await asyncio.to_thread(rec.AcceptWaveform, pcm)
                    if done:
                        text = json.loads(rec.Result()).get("text", "")
                        if text:
                            await ws.send_json({"text": text, "final": True})
                    else:
                        partial = json.loads(rec.PartialResult()).get("partial", "")
                        await ws.send_json({"partial": partial})
                    continue
                text_msg = msg.get("text")
                if text_msg:
                    try:
                        obj = json.loads(text_msg)
                    except (ValueError, TypeError):
                        obj = None
                    if isinstance(obj, dict) and obj.get("eof"):
                        final = json.loads(rec.FinalResult()).get("text", "")
                        await ws.send_json({"text": final, "final": True})
                        rec = vosk.KaldiRecognizer(model, 16000)  # ready for next
        except WebSocketDisconnect:
            pass
        except Exception:  # noqa: BLE001 — a bad frame must never crash the daemon
            pass
        finally:
            try:
                await ws.close()
            except Exception:  # noqa: BLE001
                pass
