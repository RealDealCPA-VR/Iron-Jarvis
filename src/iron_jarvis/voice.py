"""Locate the bundled/configured offline Vosk speech-to-text model.

This module is the single source of truth for finding the Vosk model
directory, used by BOTH the daemon's voice routes (which delegate here) and
the onboarding checklist — sharing the locator keeps the checklist's "local"
answer identical to ``/voice/status``'s for this backend. (The checklist
mirrors the other three backends — stt / openai / custom — by enumerating the
same config and vault reads the daemon's ``_voice_backend`` does.) The desktop
app bundles a model and points ``IRONJARVIS_VOSK_MODEL`` at it, which is why
"no OpenAI key" must never be read as "no voice" on a packaged install.

Stdlib-only on purpose: the checklist runs on every readiness poll and this
must stay import-light, offline, and safe to call from anywhere.
"""

from __future__ import annotations

import os
from pathlib import Path


def vosk_model_path(config) -> str | None:
    """Directory of the bundled/configured Vosk model, or ``None``.

    Resolution order (identical to what the daemon's voice routes use):
    ``IRONJARVIS_VOSK_MODEL`` env (the desktop app points this at the bundled
    model) > ``config.voice_vosk_model_path`` > ``<config.home>/vosk-model``.
    A directory qualifies only if it looks like a real model (has an ``am``
    subdir). Never raises — a broken config or unstatable path just means
    "no model here".
    """
    try:
        home = getattr(config, "home", None)
        candidates = (
            os.environ.get("IRONJARVIS_VOSK_MODEL"),
            (getattr(config, "voice_vosk_model_path", "") or "").strip() or None,
            (str(Path(home) / "vosk-model") if home else None),
        )
    except Exception:  # noqa: BLE001 — a broken config means "no model"
        return None
    for cand in candidates:
        if not cand:
            continue
        try:
            p = Path(cand)
            if p.is_dir() and (p / "am").is_dir():
                return str(p)
        except Exception:  # noqa: BLE001 — unstatable path = doesn't qualify
            continue
    return None
