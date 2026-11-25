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
* ``{"op": "transcribe_timed", "path": p, "model": dir, "max_audio_s": n,
  "piece_s": 60, "progress": file}`` (idea 33-a, a recording handed to a
  chat) → ``{"lines": [{"start": s, "end": s, "text": str}], "seconds": s,
  "duration": s, "cut": bool, "decoder": str}``: Vosk with WORD timings, one
  recognizer over the whole recording (so every time is the recording's own),
  and after each ``piece_s`` seconds of audio ``{"heard_s", "total_s"}`` is
  written to ``progress`` (replaced whole, never half-written);
* ``{"op": "pieces", "path": p, "out_dir": dir, "max_audio_s": n,
  "piece_s": 60}`` (33-a, a speech server chosen for recordings) → the
  recording as 16 kHz mono WAV files of ``piece_s`` seconds in ``out_dir``:
  ``{"pieces": [{"path", "start", "seconds"}], "seconds": s, "duration": s,
  "cut": bool, "decoder": str}``.
Any failure → ``{"error": "<words>"}``. At most ``max_audio_s`` seconds of
audio are read (the rest of a longer recording is left out).

Imports stay stdlib at module level: this runs from the frozen exe's fast
path. A plain 16-bit WAV is read with the stdlib ``wave`` module when PyAV is
not installed (a source install without the ``voice`` extra). numpy comes in
only through ``core/_numpy`` (one OpenBLAS thread), and PyAV's frames are
read from their plane, never through ``to_ndarray`` (which would import
numpy behind that door).
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


def _frame_pcm(out: Any) -> bytes:
    """The bytes of one resampled frame (packed s16 mono: two bytes a sample).

    Read straight from the frame's one plane, never ``to_ndarray()``: that
    makes PyAV import numpy outside ``core/_numpy``'s door, which sizes
    OpenBLAS to the machine (about 760 MB and 23 idle threads on a 24-core
    PC). A plane may be padded past its samples, so it is cut to them."""
    return bytes(out.planes[0])[: int(out.samples) * 2]


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
                raw = _frame_pcm(out)
                take = min(len(raw) // 2, limit - done)
                if take <= 0:
                    return
                done += take
                yield raw[: take * 2]
            if done >= limit:
                return
        for out in resampler.resample(None) or []:
            raw = _frame_pcm(out)
            take = min(len(raw) // 2, limit - done)
            if take <= 0:
                return
            done += take
            yield raw[: take * 2]


#: How much of a WAV is read at once, in seconds (33-a): a two-hour 44.1 kHz
#: stereo WAV is 1.3 GB, and it is read a block at a time, never whole.
WAVE_BLOCK_S = 10


def _pcm_wave(path: str, max_s: float) -> Iterator[bytes]:
    """16 kHz mono 16-bit PCM from a plain 16-bit WAV (stdlib and numpy), a
    block of :data:`WAVE_BLOCK_S` seconds at a time."""
    import wave  # noqa: PLC0415

    # numpy only through its one door (one OpenBLAS thread), and only here.
    from iron_jarvis.core._numpy import np  # noqa: PLC0415

    with wave.open(path, "rb") as w:
        if w.getsampwidth() != 2:
            raise ValueError("only 16-bit WAV is read without the audio decoder")
        rate, channels = w.getframerate(), w.getnchannels()
        left = min(w.getnframes(), int(max_s * rate))
        block = max(1, int(rate * WAVE_BLOCK_S))
        while left > 0:
            frames = w.readframes(min(block, left))
            got = len(frames) // (2 * max(1, channels))
            if got <= 0:
                return
            left -= got
            samples = np.frombuffer(frames[: got * 2 * max(1, channels)], dtype="<i2").astype(np.float32)
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


# --------------------------------------------------------------------------- #
# a recording handed to a chat (idea 33, phase 33-a)
# --------------------------------------------------------------------------- #
#: A line of the transcript ends after this many words...
LINE_WORDS = 24
#: ...or where the speaker paused this long, in seconds.
LINE_GAP_S = 1.2
#: How much PCM is handed to the recognizer at once (a quarter second).
FEED_BYTES = 8000


def _duration(path: str) -> float:
    """The recording's whole length in seconds, from its container (0.0 when
    it does not say). Best effort; never raises."""
    try:
        import av  # noqa: PLC0415 — native, only in this child

        with av.open(path) as container:
            if container.duration:
                return float(container.duration) / 1_000_000.0
            stream = next((s for s in container.streams if s.type == "audio"), None)
            if stream is not None and stream.duration and stream.time_base:
                return float(stream.duration * stream.time_base)
            return 0.0
    except ImportError:
        pass
    except Exception:  # noqa: BLE001 — a length we cannot read is "not known"
        return 0.0
    try:
        import wave  # noqa: PLC0415

        with wave.open(path, "rb") as w:
            rate = w.getframerate()
            return float(w.getnframes()) / float(rate) if rate else 0.0
    except Exception:  # noqa: BLE001
        return 0.0


def _write_progress(target: str, heard_s: float, total_s: float, *, done: bool = False) -> None:
    """``{"heard_s", "total_s", "done"}`` into ``target``, replaced whole (a
    reader never sees half a file). Never raises: progress is a courtesy."""
    if not target:
        return
    import os  # noqa: PLC0415

    try:
        tmp = f"{target}.tmp"
        with open(tmp, "w", encoding="ascii") as fh:
            fh.write(json.dumps({"heard_s": round(heard_s, 1), "total_s": round(total_s, 1), "done": done}))
        os.replace(tmp, target)
    except Exception:  # noqa: BLE001
        pass


def _lines_of(result: dict, fallback_start: float) -> "list[dict[str, Any]]":
    """One recognizer result as transcript lines: a new line after
    :data:`LINE_WORDS` words or a pause of :data:`LINE_GAP_S`. A result with
    no word timings (an older model) is one line at ``fallback_start``."""
    words = result.get("result") if isinstance(result.get("result"), list) else []
    out: list[dict[str, Any]] = []
    cur: list[dict] = []

    def _close() -> None:
        if cur:
            out.append({
                "start": round(float(cur[0].get("start") or 0.0), 2),
                "end": round(float(cur[-1].get("end") or cur[-1].get("start") or 0.0), 2),
                "text": " ".join(str(w.get("word") or "") for w in cur).strip(),
            })

    for w in words:
        if not isinstance(w, dict) or not str(w.get("word") or "").strip():
            continue
        if cur and (len(cur) >= LINE_WORDS
                    or float(w.get("start") or 0.0) - float(cur[-1].get("end") or 0.0) > LINE_GAP_S):
            _close()
            cur = []
        cur.append(w)
    _close()
    if not out:
        text = str(result.get("text") or "").strip()
        if text:
            out.append({"start": round(fallback_start, 2), "end": round(fallback_start, 2), "text": text})
    return [line for line in out if line["text"]]


def _transcribe_timed(req: dict) -> dict[str, Any]:
    import vosk  # noqa: PLC0415 — native, only in this child

    path = str(req.get("path") or "")
    max_s = float(req.get("max_audio_s") or 600)
    piece_s = max(1.0, float(req.get("piece_s") or 60))
    progress = str(req.get("progress") or "")
    total = _duration(path)
    _write_progress(progress, 0.0, min(total, max_s) if total else 0.0)
    vosk.SetLogLevel(-1)
    model = vosk.Model(str(req.get("model") or ""))
    rec = vosk.KaldiRecognizer(model, RATE)
    try:
        rec.SetWords(True)
    except Exception:  # noqa: BLE001 — a model without word times: one line a result
        pass
    chunks, decoder = _pcm(path, max_s)
    lines: list[dict[str, Any]] = []
    fed = 0  # samples handed to the recognizer
    said_from = 0.0  # where the current result began (for a result with no times)
    next_mark = int(piece_s * RATE)
    for chunk in chunks:
        for i in range(0, len(chunk), FEED_BYTES):
            part = chunk[i:i + FEED_BYTES]
            fed += len(part) // 2
            if rec.AcceptWaveform(part):
                lines.extend(_lines_of(json.loads(rec.Result()), said_from))
                said_from = fed / RATE
            if fed >= next_mark:
                _write_progress(progress, fed / RATE, min(total, max_s) if total else 0.0)
                next_mark += int(piece_s * RATE)
    lines.extend(_lines_of(json.loads(rec.FinalResult()), said_from))
    heard = fed / RATE
    _write_progress(progress, heard, min(total, max_s) if total else heard, done=True)
    return {
        "lines": lines,
        "seconds": round(heard, 3),
        "duration": round(total or heard, 3),
        # Longer than was read: by the container's own length, or (when it
        # does not say) because reading stopped at the bound.
        "cut": bool(total > max_s + 0.5) if total else heard >= max_s - 0.01,
        "decoder": decoder,
    }


def _pieces(req: dict) -> dict[str, Any]:
    import wave  # noqa: PLC0415

    path = str(req.get("path") or "")
    out_dir = Path(str(req.get("out_dir") or ""))
    max_s = float(req.get("max_audio_s") or 600)
    piece_s = max(1.0, float(req.get("piece_s") or 60))
    if not out_dir.is_dir():
        raise ValueError("the folder for the pieces is missing")
    total = _duration(path)
    chunks, decoder = _pcm(path, max_s)
    per = int(piece_s * RATE) * 2
    pieces: list[dict[str, Any]] = []
    buf = bytearray()
    done = 0  # samples written

    def _write(data: bytes) -> None:
        nonlocal done
        target = out_dir / f"piece-{len(pieces):04d}.wav"
        with wave.open(str(target), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(RATE)
            w.writeframes(data)
        n = len(data) // 2
        pieces.append({"path": str(target), "start": round(done / RATE, 3), "seconds": round(n / RATE, 3)})
        done += n

    for chunk in chunks:
        buf += chunk
        while len(buf) >= per:
            _write(bytes(buf[:per]))
            del buf[:per]
    if len(buf) >= 2:
        _write(bytes(buf))
    return {
        "pieces": pieces,
        "seconds": round(done / RATE, 3),
        "duration": round(total or done / RATE, 3),
        "cut": bool(total > max_s + 0.5) if total else done / RATE >= max_s - 0.01,
        "decoder": decoder,
    }


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
        if op == "transcribe_timed":
            return _transcribe_timed(req)
        if op == "pieces":
            return _pieces(req)
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
