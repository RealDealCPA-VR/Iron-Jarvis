"""Stand-ins for idea 33-a's tests (a recording handed to a chat).

* :data:`FAKE_VOSK` — a ``vosk`` module for the REAL worker child (put on its
  ``PYTHONPATH``): it "hears" the words of ``IJ_FAKE_VOSK_WORDS`` (``[[start,
  end, word], ...]``) when the audio fed to it has passed them, with their
  own times, as Vosk's ``SetWords(True)`` results do. CI has no Vosk model;
  everything else in the child (the decode, the feeding, the lines, the
  progress file, the bound) is the real code.
* :func:`wav` — a plain 16-bit WAV of silence of any length and rate.
* :class:`Deps` — the daemon's deps the job borrows (``_voice_backend``,
  ``_vosk_model_path``, ``_VOICE_MAX_BYTES``, ``platform``).
* :class:`NotesModel` — a router ``complete`` that answers the notes prompt
  with a fixed JSON and records every call (provider, pin, prompt).
"""

from __future__ import annotations

import json
import wave
from pathlib import Path
from types import SimpleNamespace
from typing import Any

FAKE_VOSK = r'''
import json, os

_WORDS = json.loads(os.environ.get("IJ_FAKE_VOSK_WORDS", "[]"))

def SetLogLevel(level):
    pass

class Model:
    def __init__(self, path):
        self.path = path

class KaldiRecognizer:
    def __init__(self, model, rate):
        self.rate = rate
        self.fed = 0
        self.given = 0
        self.words = False

    def SetWords(self, on):
        self.words = bool(on)

    def _ready(self, final):
        # An utterance ends at a pause, as Vosk's do: its words are given
        # once half a second has passed since the last one ended and the
        # next one has not begun (or at the end of the audio).
        now = self.fed / self.rate
        i = j = self.given
        while j < len(_WORDS) and _WORDS[j][1] <= now:
            j += 1
        if j == i:
            return []
        if not final:
            if now - _WORDS[j - 1][1] < 0.5:
                return []
            if j < len(_WORDS) and _WORDS[j][0] <= now:
                return []
        self.given = j
        return [{"word": w, "start": s, "end": e, "conf": 1.0} for s, e, w in _WORDS[i:j]]

    def AcceptWaveform(self, data):
        self.fed += len(data) // 2
        self._pending = self._ready(False)
        return bool(self._pending)

    def _result(self, words):
        text = " ".join(w["word"] for w in words)
        return json.dumps({"result": words, "text": text} if self.words else {"text": text})

    def Result(self):
        words, self._pending = getattr(self, "_pending", []), []
        return self._result(words)

    def FinalResult(self):
        return self._result(self._ready(True))
'''


def fake_vosk_dir(tmp_path: Path) -> Path:
    """A folder holding the fake ``vosk`` package (for ``PYTHONPATH``)."""
    root = tmp_path / "fakevosk"
    pkg = root / "vosk"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text(FAKE_VOSK, encoding="utf-8")
    return root


def use_fake_vosk(monkeypatch, tmp_path: Path, words: "list[tuple[float, float, str]]") -> None:
    """The real worker child, with the fake recognizer hearing ``words``."""
    import os

    root = fake_vosk_dir(tmp_path)
    src = Path(__file__).resolve().parents[2] / "src"
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join([str(root), str(src)]))
    monkeypatch.setenv("IJ_FAKE_VOSK_WORDS", json.dumps([list(w) for w in words]))


def wav(path: Path, seconds: float, rate: int = 8000, channels: int = 1) -> Path:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        frames = int(rate * seconds)
        block = b"\x00\x00" * channels * rate
        for _ in range(frames // rate):
            w.writeframes(block)
        w.writeframes(b"\x00\x00" * channels * (frames % rate))
    return path


#: A meeting, word by word (start, end, word), as the fake hears it.
MEETING = [
    (0.5, 0.9, "good"), (1.0, 1.4, "morning"), (1.5, 1.9, "everyone"),
    (70.2, 70.6, "we"), (70.7, 71.1, "decided"), (71.2, 71.4, "to"), (71.5, 71.9, "move"),
    (72.0, 72.2, "the"), (72.3, 72.8, "launch"), (72.9, 73.1, "to"), (73.2, 73.7, "march"),
    (95.0, 95.2, "i"), (95.3, 95.6, "will"), (95.7, 96.0, "send"), (96.1, 96.3, "the"),
    (96.4, 96.9, "revised"), (97.0, 97.4, "plan"), (97.5, 97.7, "on"), (97.8, 98.3, "friday"),
    (110.0, 110.4, "maria"), (110.5, 110.8, "will"), (110.9, 111.3, "check"), (111.4, 111.6, "the"),
    (111.7, 112.2, "numbers"),
]


class Deps:
    """The daemon's deps the recording job borrows."""

    def __init__(self, platform: Any = None, *, model: "str | None" = "C:/fake/vosk-model",
                 backend: "tuple[str, str, Any] | None" = None) -> None:
        self.platform = platform
        self.model = model
        self.backend = backend
        self._VOICE_MAX_BYTES = 25 * 1024 * 1024

    def _vosk_model_path(self) -> "str | None":
        return self.model

    def _voice_backend(self) -> "tuple[str, str, Any] | None":
        return self.backend


#: What the notes model answers: two decisions it can quote, one item it
#: made up (never said), one with too few words, and their owes.
NOTES_ANSWER = {
    "decisions": [
        {"time": "01:10", "said": "we decided to move the launch to march", "note": "The launch moves to March."},
        {"time": "01:12", "said": "we agreed to cut the budget in half", "note": "The budget is cut in half."},
    ],
    "you_owe": [
        {"time": "01:35", "said": "i will send the revised plan on friday", "note": "Send the revised plan on Friday."},
        {"time": "01:35", "said": "send", "note": "Too short to check."},
    ],
    "they_owe": [
        {"time": "01:50", "said": "maria will check the numbers", "note": "Maria checks the numbers."},
    ],
}


class NotesModel:
    """A router ``complete`` for the notes: answers ``answer`` (JSON) as
    ``provider``; records each call."""

    def __init__(self, answer: "dict | str" = None, provider: str = "ollama") -> None:
        self.answer = NOTES_ANSWER if answer is None else answer
        self.provider = provider
        self.calls: list[dict[str, Any]] = []

    async def complete(self, **kw: Any) -> Any:
        self.calls.append(kw)
        text = self.answer if isinstance(self.answer, str) else json.dumps(self.answer)
        return SimpleNamespace(provider=kw.get("provider") or self.provider,
                               response=SimpleNamespace(text=text))


LOCAL_CHOICE = {"provider": "ollama", "model": "llama3", "name": "Ollama on this PC", "local": True}
CLOUD_CHOICE = {"provider": "anthropic", "model": None, "name": "Claude", "local": False}
