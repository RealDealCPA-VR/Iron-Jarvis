"""An offline media provider for tests and demos (v1.346.0, contract item 1).

Registered ONLY when ``IRONJARVIS_MEDIA_FAKE=1`` is in the environment or a
test builds it with ``enabled=True`` — ``available()`` is False otherwise,
so it can never be chosen on a real install by accident.

* image → a real PNG drawn with Pillow after ~1 s (``IMAGE_DELAY_S``);
* video → the tiny MP4 fixture (``tests/fixtures/media_tiny_v1346.mp4``)
  after ``IRONJARVIS_MEDIA_FAKE_DELAY_S`` seconds (default 3);
* audio / music → a short silent WAV after the same delay;
* the quote is FREE when the prompt contains the word "free", else 7 credits.

Time is read from an injected ``clock`` so a test drives the states without
sleeping. A job this process never started polls as failed "lost at
restart" (the boot reconcile relies on that).
"""

from __future__ import annotations

import io
import os
import struct
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from .base import JobHandle, JobState, MediaError, MediaProvider, Quote

ENV_FLAG = "IRONJARVIS_MEDIA_FAKE"
ENV_DELAY = "IRONJARVIS_MEDIA_FAKE_DELAY_S"
IMAGE_DELAY_S = 1.0
DEFAULT_DELAY_S = 3.0
PAID_CREDITS = 7.0

FIXTURE_MP4 = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "media_tiny_v1346.mp4"


def fake_enabled_by_env() -> bool:
    return os.environ.get(ENV_FLAG, "").strip() == "1"


def _delay_from_env() -> float:
    try:
        return max(0.0, float(os.environ.get(ENV_DELAY, "") or DEFAULT_DELAY_S))
    except ValueError:
        return DEFAULT_DELAY_S


def make_png(prompt: str, size: int = 256) -> bytes:
    """A real PNG (Pillow): a colour derived from the prompt, so two prompts
    give two different pictures."""
    from PIL import Image, ImageDraw

    seed = sum(ord(c) for c in prompt) or 1
    colour = ((seed * 37) % 200 + 30, (seed * 91) % 200 + 30, (seed * 53) % 200 + 30)
    img = Image.new("RGB", (size, size), colour)
    draw = ImageDraw.Draw(img)
    draw.rectangle((size // 4, size // 4, 3 * size // 4, 3 * size // 4), outline=(255, 255, 255), width=4)
    draw.text((12, 12), (prompt or "fake")[:24], fill=(255, 255, 255))
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def make_wav(seconds: float = 0.5, rate: int = 8000) -> bytes:
    """A short silent 8 kHz mono 16-bit WAV."""
    frames = int(seconds * rate)
    data = b"\x00\x00" * frames
    header = b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVE"
    header += b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
    header += b"data" + struct.pack("<I", len(data))
    return header + data


class FakeProvider(MediaProvider):
    id = "fake"
    label = "Fake media"

    def __init__(
        self,
        *,
        enabled: bool | None = None,
        delay_s: float | None = None,
        clock: Callable[[], float] = time.monotonic,
        fixture: Path | None = None,
    ) -> None:
        self._enabled = fake_enabled_by_env() if enabled is None else bool(enabled)
        self._delay = _delay_from_env() if delay_s is None else max(0.0, float(delay_s))
        self._clock = clock
        self._fixture = fixture or FIXTURE_MP4
        self._jobs: dict[str, dict[str, Any]] = {}

    def kinds(self) -> set[str]:
        return {"image", "video", "audio", "music"}

    def available(self) -> bool:
        return self._enabled

    async def default_model(self, kind: str) -> str:
        return f"fake/{kind}"

    async def models(self, kind: str) -> list[dict[str, Any]]:
        return [{"id": f"fake/{kind}", "name": f"Fake {kind}", "kind": kind}]

    async def quote(self, model: str, params: dict[str, Any], kind: str) -> Quote:
        prompt = str(params.get("prompt") or "")
        if "free" in prompt.lower():
            return Quote(0.0, "credits", free=True, free_label="Maker: 4 left today", note="fake")
        return Quote(PAID_CREDITS, "credits", free=False, note="fake price")

    async def start(
        self, model: str, params: dict[str, Any], kind: str, idem_key: str
    ) -> JobHandle:
        if not self._enabled:
            raise MediaError("not_configured", "the fake media provider is off")
        if kind not in self.kinds():
            raise MediaError("unknown_model", f"the fake provider cannot make {kind}")
        for job_id, job in self._jobs.items():
            if job.get("idem_key") == idem_key:
                return JobHandle(job_id=job_id, eta_s=int(job["delay"]))
        delay = IMAGE_DELAY_S if kind == "image" else self._delay
        job_id = f"fake-{uuid.uuid4().hex[:10]}"
        self._jobs[job_id] = {
            "kind": kind,
            "prompt": str(params.get("prompt") or ""),
            "started": self._clock(),
            "delay": delay,
            "idem_key": idem_key,
        }
        return JobHandle(job_id=job_id, eta_s=int(delay))

    async def poll(self, job_id: str) -> JobState:
        job = self._jobs.get(job_id)
        if job is None:
            return JobState("failed", error="lost at restart")
        if self._clock() - job["started"] < job["delay"]:
            return JobState("running")
        kind = job["kind"]
        if kind == "image":
            return JobState("succeeded", payload=make_png(job["prompt"]), ext=".png", cost_note="fake")
        if kind == "video":
            try:
                blob = self._fixture.read_bytes()
            except OSError:
                return JobState("failed", error="the fake video fixture is missing")
            return JobState("succeeded", payload=blob, ext=".mp4", cost_note="fake")
        return JobState("succeeded", payload=make_wav(), ext=".wav", cost_note="fake")
