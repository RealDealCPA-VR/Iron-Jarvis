"""Media generation in chat (v1.346.0).

ONE provider interface (:mod:`.base`) over any image/video/audio/music
service the user chooses: Pixio (:mod:`.pixio`, the user's own), OpenAI
Images (:mod:`.openai_images`), and an offline fake (:mod:`.fake`, tests and
``IRONJARVIS_MEDIA_FAKE=1`` only). :mod:`.registry` picks the provider —
explicit name, the ``media_provider`` setting, or the ONLY connected one; it
never guesses between two. :mod:`.deliver` lands finished bytes in the
workspace and the Creative gallery (the delivery path lifted out of
``tools/pixio.py``, which now calls it).

The money rule lives in the provider's :class:`Quote`: a paid or unknown
price asks before every spend; a quote the provider says is FREE (Pixio's
Maker allowance, a plan-covered model) runs without a card unless
``media_ask_when_free`` is on. Secrets never reach a log, an error, an event
or a tool output; a CDN download never carries the key; every network call
runs off the loop and is bounded.
"""

from __future__ import annotations

from .base import (
    KINDS,
    JobHandle,
    JobState,
    MediaError,
    MediaProvider,
    Quote,
)

__all__ = [
    "KINDS",
    "JobHandle",
    "JobState",
    "MediaError",
    "MediaProvider",
    "Quote",
]
