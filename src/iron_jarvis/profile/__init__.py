"""The user profile (v1.144.0) — one identity, injected at every seam.

``profile_block(platform)`` is the whole public surface for prompt assembly:
chat, the streamed chat, the phone lane, agent sessions, and the round table
all append it, so the way Iron Jarvis writes stops depending on which model
took the turn.
"""

from .block import (  # noqa: F401
    HOW_HEADER,
    MAX_BLOCK_CHARS,
    VOICE_HEADER,
    WHO_HEADER,
    profile_block,
    profile_language,
    render,
)
from .models import PROFILE_ID, UserProfileRecord  # noqa: F401
from .store import ProfileStore, as_dict  # noqa: F401

__all__ = [
    "HOW_HEADER",
    "MAX_BLOCK_CHARS",
    "PROFILE_ID",
    "ProfileStore",
    "UserProfileRecord",
    "VOICE_HEADER",
    "WHO_HEADER",
    "as_dict",
    "profile_block",
    "profile_language",
    "render",
]
