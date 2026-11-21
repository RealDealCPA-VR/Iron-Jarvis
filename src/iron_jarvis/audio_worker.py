"""Phone voice notes, decoded and heard in their OWN process (10-a).

A voice note from Telegram, Slack or email is an OGG (Opus), M4A, MP3 or WebM
file. Turning it into words needs a decoder (PyAV, which carries its own
ffmpeg libraries: this PC has no ffmpeg on PATH) and the offline speech model
(Vosk). Both are native code, and a malformed file from a phone must never be
able to take the daemon down with it, so neither runs in the daemon:
:mod:`iron_jarvis.comm.voice_note` starts this module as a short-lived child
(``ironjarvis.exe audio-worker`` when frozen, served by
``packaging/ironjarvis_entry._fast_path`` without the CLI import;
``python -m iron_jarvis.audio_worker`` from source) through ``core/procs``,
hands it ONE JSON request on stdin, and kills its tree at the deadline.

Protocol: stdin = one JSON object, stdout = one JSON object (ASCII).
* ``{"op": "decode", "path": p, "max_audio_s": n}`` →
  ``{"seconds": s, "decoder": "pyav" | "wave"}`` (the doctor's check, and a
  test of the child itself);
* ``{"op": "transcribe", "path": p, "model": dir, "max_audio_s": n}`` →
  ``{"text": str, "seconds": s}``;
* ``{"op": "check"}`` → decode the one-second clip that ships with the app
  with PyAV only: ``{"seconds": s, "decoder": "pyav"}``.
Any failure → ``{"error": "<words>"}``. At most ``max_audio_s`` seconds of
audio are read (the rest of a longer recording is left out).

Imports stay stdlib at module level: this runs from the frozen exe's fast
path. A plain 16-bit WAV is read with the stdlib ``wave`` module when PyAV is
not installed (a source install without the ``voice`` extra).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Iterator

#: The rate Vosk listens at.
RATE = 16000
#: The clip the doctor decodes (ships with the app: iron_jarvis/comm/assets).
CHECK_CLIP = Path(__file__).resolve().parent / "comm" / "assets" / "voice-check.ogg"


def _pcm_pyav(path: str, max_s: float) -> Iterator[bytes]:
    """16 kHz mono 16-bit PCM from any file PyAV can open, in chunks."""
    import av  # noqa: PLC0415 — native, only in this child

    limit = int(max_s * RATE)
    done = 0
    with av.open(path) as container:
        stream = next((s for s in container.streams if s.type == "audio"), None)
        if stream is None:
            raise ValueError("the file has no sound in it")
        resampler = av.AudioResampler(format="s16", layout="mono", rate=RATE)
        for frame in container.decode(stream):
            for out in resampler.resample(frame) or []:
                raw = out.to_ndarray().tobytes()
                take = min(len(raw) // 2, limit - done)
                if take <= 0:
                    return
                done += take
                yield raw[: take * 2]
            if done >= limit:
                return
        for out in resampler.resample(None) or []:
            raw = out.to_ndarray().tobytes()
            take = min(len(raw) // 2, limit - done)
            if take <= 0:
                return
            done += take
            yield raw[: take * 2]


def _pcm_wave(path: str, max_s: float) -> Iterator[bytes]:
    """16 kHz mono 16-bit PCM from a plain 16-bit WAV (stdlib only)."""
    import wave  # noqa: PLC0415

    import numpy as np  # noqa: PLC0415

    with wave.open(path, "rb") as w:
        if w.getsampwidth() != 2:
            raise ValueError("only 16-bit WAV is read without the audio decoder")
        rate, channels = w.getframerate(), w.getnchannels()
        frames = w.readframes(min(w.getnframes(), int(max_s * rate)))
    samples = np.frombuffer(frames, dtype="<i2").astype(np.float32)
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    if rate != RATE and len(samples):
        n = int(round(len(samples) * RATE / rate))
        samples = np.interp(np.linspace(0, len(samples) - 1, n), np.arange(len(samples)), samples)
    yield samples.clip(-32768, 32767).astype("<i2").tobytes()


def _pcm(path: str, max_s: float, *, require_pyav: bool = False) -> tuple[Iterator[bytes], str]:
    try:
        import av  # noqa: F401, PLC0415

        return _pcm_pyav(path, max_s), "pyav"
    except ImportError:
        if require_pyav or Path(path).suffix.lower() != ".wav":
            raise ValueError("the audio decoder (PyAV) is missing from this install") from None
        return _pcm_wave(path, max_s), "wave"


def _decode(req: dict) -> dict[str, Any]:
    path = str(req.get("path") or "")
    max_s = float(req.get("max_audio_s") or 600)
    chunks, decoder = _pcm(path, max_s, require_pyav=bool(req.get("require_pyav")))
    total = sum(len(c) for c in chunks) // 2
    return {"seconds": round(total / RATE, 3), "decoder": decoder}


def _transcribe(req: dict) -> dict[str, Any]:
    import vosk  # noqa: PLC0415 — native, only in this child

    path = str(req.get("path") or "")
    max_s = float(req.get("max_audio_s") or 600)
    vosk.SetLogLevel(-1)
    model = vosk.Model(str(req.get("model") or ""))
    rec = vosk.KaldiRecognizer(model, RATE)
    chunks, _decoder = _pcm(path, max_s)
    words: list[str] = []
    total = 0
    for chunk in chunks:
        total += len(chunk) // 2
        for i in range(0, len(chunk), 8000):
            if rec.AcceptWaveform(chunk[i:i + 8000]):
                said = json.loads(rec.Result()).get("text", "")
                if said:
                    words.append(said)
    said = json.loads(rec.FinalResult()).get("text", "")
    if said:
        words.append(said)
    return {"text": " ".join(words).strip(), "seconds": round(total / RATE, 3)}


def answer(req: Any) -> dict[str, Any]:
    """One request → one answer; never raises."""
    try:
        if not isinstance(req, dict):
            return {"error": "the request was not understood"}
        op = req.get("op")
        if op == "decode":
            return _decode(req)
        if op == "transcribe":
            return _transcribe(req)
        if op == "check":
            return _decode({"path": str(CHECK_CLIP), "require_pyav": True, "max_audio_s": 5})
        return {"error": f"unknown op {op!r}"}
    except ImportError as exc:
        return {"error": f"a part of the speech reader is missing ({exc.name or 'unknown'})"}
    except Exception as exc:  # noqa: BLE001 — every failure is an answer
        return {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}


def main() -> int:
    try:
        req = json.loads(sys.stdin.read() or "null")
    except Exception:  # noqa: BLE001
        req = None
    sys.stdout.write(json.dumps(answer(req)))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
