"""Render the user profile into a system-prompt block (v1.144.0).

ONE renderer, called at every seam that talks to the user. That is the whole
point of the wave: personality and formatting stopped being a property of
*which model answered* and became a property of *the user*.

Contract (relied on by ``tests/test_profile_v1144.py``):

* an empty / disabled profile renders ``""`` — an untouched install must send a
  byte-identical prompt to what it sent before this feature existed;
* the block is BOUNDED (:data:`MAX_BLOCK_CHARS`); a user who pastes their
  memoir into "about me" cannot eat a 8k local model's whole context window;
* it NEVER raises — like ``memory_index_block`` and the fabric grounding, a
  broken profile must cost its own block, not the user's turn.

Section order is fixed and deliberate: who → how to answer → their voice. The
voice section comes last so the "never let style change WHAT you answer" guard
is the final thing the model reads before the conversation.
"""

from __future__ import annotations

import logging

from . import presets
from .language import language_instruction
from .models import UserProfileRecord

log = logging.getLogger(__name__)

WHO_HEADER = "# Who you are working with"
HOW_HEADER = "# How to answer this person"
VOICE_HEADER = "# Their voice"

#: Hard ceiling for the whole rendered block. Sized against the smallest model
#: this app routinely drives (an 8k-window local 14B): ~2.4k chars ≈ 600 tokens
#: ≈ 7% of that window, which buys real personalization without crowding out
#: the conversation. v1.146.0's context ladder accounts for this block by name.
MAX_BLOCK_CHARS = 2400

#: Per-section caps, applied before the whole-block cap so one long field can
#: never starve the others.
_ABOUT_CHARS = 900
_RULES_CHARS = 700
_VOICE_CHARS = 900

#: The one guard the brief asked for by name: imitate the voice, but never let
#: imitation shrink the answer.
_VOICE_GUARD = (
    "Match the rhythm, vocabulary, and register described below when you write. "
    "Matching their style must NEVER change WHAT you answer: address every part "
    "of every question in full, even when their own writing is terse."
)


def _clip(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


#: The three renderable sections, in prompt order.
ALL_SECTIONS = ("who", "how", "voice")


def render(
    profile: UserProfileRecord, include: tuple[str, ...] = ALL_SECTIONS
) -> str:
    """The pure renderer — a record in, a prompt block out ("" when empty).

    ``include`` narrows the sections. One caller uses it: the ROUND TABLE, which
    deliberately runs several panelists with distinct characters. Telling each
    of them "write in the user's voice" would flatten exactly the difference
    that makes a panel worth reading — but "answer in English", "plain
    language", and the dyslexia rules must still hold for every panelist. So the
    panel takes ``("how",)``: the user's ACCESSIBILITY and language needs are not
    negotiable, their voice is not the panel's to imitate.
    """
    if profile is None or not getattr(profile, "enabled", True):
        return ""

    sections: list[str] = []

    about = _clip(getattr(profile, "about", ""), _ABOUT_CHARS)
    if about and "who" in include:
        sections.append(f"{WHO_HEADER}\n{about}")

    # --- how to answer ------------------------------------------------------
    lines: list[str] = []
    for preset, key in (
        (presets.RESPONSE_LENGTHS, getattr(profile, "response_length", "")),
        (presets.READING_LEVELS, getattr(profile, "reading_level", "")),
        (presets.FORMATTING, getattr(profile, "formatting", "")),
        (presets.WRITING_STYLES, getattr(profile, "writing_style", "")),
        (presets.TONES, getattr(profile, "tone", "")),
    ):
        text = presets.instruction(preset, key).strip()
        if text:
            lines.append(f"- {text}")

    rules = _clip(getattr(profile, "formatting_rules", ""), _RULES_CHARS)
    for raw in rules.splitlines():
        raw = raw.strip().lstrip("-").strip()
        if raw:
            lines.append(f"- {raw}")

    lang = language_instruction(getattr(profile, "language", ""))
    if lang:
        lines.append(f"- {lang}")

    access = presets.ACCESSIBILITY.get((getattr(profile, "accessibility", "") or "").strip())
    access_text = str((access or {}).get("instruction") or "").strip()

    if (lines or access_text) and "how" in include:
        body = "\n".join(lines)
        if access_text:
            body = f"{body}\n{access_text}" if body else access_text
        sections.append(f"{HOW_HEADER}\n{body}")

    # --- their voice --------------------------------------------------------
    voice = _clip(getattr(profile, "voice_card", ""), _VOICE_CHARS)
    if voice and "voice" in include:
        sections.append(f"{VOICE_HEADER}\n{_VOICE_GUARD}\n\n{voice}")

    if not sections:
        return ""
    return _clip("\n\n".join(sections), MAX_BLOCK_CHARS)


def profile_block(platform, include: tuple[str, ...] = ALL_SECTIONS) -> str:
    """The seam-facing renderer: read the profile off *platform* and render it.

    Never raises and never returns None — a seam can append the result
    unconditionally. Mirrors ``memory.index_block.memory_index_block``'s shape
    so the injections at each seam read the same.
    """
    try:
        from .store import ProfileStore

        return render(ProfileStore(platform.engine).get(), include)
    except Exception:  # noqa: BLE001 — the profile must never break a turn
        log.warning("profile block unavailable (turn continues)", exc_info=True)
        return ""


def profile_language(platform) -> tuple[str, bool]:
    """``(language_code, enforce)`` for the language guard — ``("", False)``
    when unset, disabled, or unreadable. Never raises."""
    try:
        from .store import ProfileStore

        rec = ProfileStore(platform.engine).get()
        if not getattr(rec, "enabled", True):
            return ("", False)
        code = (getattr(rec, "language", "") or "").strip().lower()
        if not code:
            return ("", False)
        return (code, bool(getattr(rec, "enforce_language", True)))
    except Exception:  # noqa: BLE001
        log.warning("profile language unavailable (turn continues)", exc_info=True)
        return ("", False)
