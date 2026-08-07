"""One Iron Jarvis voice, everywhere (v1.144.0).

The reported symptom: "switching models makes Jarvis communicate differently."
Tracing the prompt assembly showed it is not really about the model at all —
the persona (Iron Jarvis's own character) was resolved in ``chat_turn`` and the
``/chat/stream`` mirror and NOWHERE ELSE. The moment a request escalated to an
agent session, the system prompt started from ``agent_def.system_prompt`` — the
ROLE ("you are a builder agent…") — and the character was simply absent.

:func:`voice_section` closes that: the same default persona chat resolves is
appended to agent runs and round-table turns as a bounded, explicitly SCOPED
section.

Why scoped rather than prepended: an agent prompt already assigns a role, and
two "you are X" statements in one prompt is how a builder starts writing chatty
prose instead of calling tools. So the section says what it governs — the words
of the user-facing answer — and states outright that it changes neither the
role nor the task. The rest of the identity (who the USER is, how they want to
be answered, their voice) rides in the profile block, which every seam also
carries; this module covers only the assistant's own character.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

VOICE_HEADER = "# Voice (how you write to the user)"

#: Cap. A persona is a couple of sentences; a user who pastes an essay into a
#: custom persona gets it clipped rather than crowding an 8k local window.
MAX_VOICE_CHARS = 700

_SCOPE = (
    "Write anything the user reads — summaries, explanations, the final "
    "answer — in this voice. It does not change your role, your task, or "
    "which tools you use:"
)


def voice_section(platform) -> str:
    """``"\\n\\n" + block`` for the configured default persona, or ``""``.

    Never raises: like every other prompt injection in this app, a failure here
    costs its own section, not the run.
    """
    try:
        from .builtins import BUILTIN_PERSONAS
        from .store import PersonaStore, resolve_prompt

        want = str(getattr(platform.config, "default_persona", "") or "").strip()
        if not want:
            return ""
        prompt = (
            resolve_prompt(PersonaStore(platform.engine), BUILTIN_PERSONAS, want) or ""
        ).strip()
        if not prompt:
            return ""
        if len(prompt) > MAX_VOICE_CHARS:
            prompt = prompt[: MAX_VOICE_CHARS - 1].rstrip() + "…"
        return f"\n\n{VOICE_HEADER}\n{_SCOPE}\n{prompt}"
    except Exception:  # noqa: BLE001 — the voice must never break a run
        log.warning("persona voice unavailable (run continues)", exc_info=True)
        return ""
