"""Voice routes: dictation status + server-side transcription.

Moved verbatim from daemon/app.py's create_app; closure-local state is
reached through ``d`` (see the deps object built in create_app).
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from typing import Any

from ..schemas import TranscribeBody


def register(app: FastAPI, d) -> None:
    """Attach these routes to *app*; ``d`` is the create_app deps object."""
    @app.get("/voice/status")
    def voice_status() -> dict[str, Any]:
        """Whether server-side dictation works right now (and via what), so the
        dashboard can offer or grey the mic honestly instead of failing late."""
        backend = d._voice_backend()
        return {
            "available": backend is not None,
            "backend": backend[0] if backend else None,
            "hint": (
                ""
                if backend
                else "Connect an OpenAI API key (Connections page) — or a custom "
                "OpenAI-compatible endpoint that serves /v1/audio/transcriptions — "
                "to enable voice dictation inside the desktop app."
            ),
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
        # OpenAI: current transcribe model with the classic whisper-1 as the
        # ladder rung (same retired-model-id lesson as chat). Custom servers
        # conventionally accept whisper-1.
        models = (
            ["gpt-4o-mini-transcribe", "whisper-1"] if label == "openai" else ["whisper-1"]
        )
        last_err = "transcription failed"
        async with httpx.AsyncClient(timeout=60.0) as client:
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
        raise HTTPException(
            status_code=424, detail=f"{label} transcription failed: {last_err}"
        )
