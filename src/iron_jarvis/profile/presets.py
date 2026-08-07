"""Profile preset vocabularies (v1.144.0).

Each preset maps a stored KEY to (label, instruction):

* the **label** is what the /you page shows,
* the **instruction** is the sentence :mod:`iron_jarvis.profile.block` puts in
  the system prompt.

Two rules hold everywhere in this module:

1. **An empty key renders nothing.** "No preference" must cost zero tokens and
   leave the model's default behaviour byte-identical — a profile the user
   never filled in must not change a single prompt.
2. **An unknown key is used VERBATIM as free text.** The persona system has
   always worked this way (``personas.resolve_prompt``), and it means a user can
   type "like a Bloomberg terminal" into the tone box and have it applied,
   instead of being silently dropped because it wasn't on our list.

The accessibility presets are deliberately thin: :data:`ACCESSIBILITY` carries
only the rules that define the mode, and turning one on POPULATES the editable
``formatting_rules``/length/reading-level fields (see ``store.apply_preset``) so
the user can then change any of it. An accessibility mode the user cannot tune
is a mode they turn off.
"""

from __future__ import annotations

#: key -> (label, instruction)
Preset = dict[str, tuple[str, str]]

TONES: Preset = {
    "neutral": ("Neutral", "Keep the tone neutral and even."),
    "warm": ("Warm", "Keep the tone warm and encouraging, without gushing."),
    "direct": (
        "Direct",
        "Be direct. Lead with the answer, skip preamble and hedging.",
    ),
    "formal": ("Formal", "Keep the tone professional and formal."),
    "casual": ("Casual", "Keep the tone casual and conversational."),
    "dry": ("Dry", "Keep the tone dry and understated; no exclamation marks."),
}

WRITING_STYLES: Preset = {
    "plain": ("Plain", "Write plainly. Short words, concrete nouns, active voice."),
    "technical": (
        "Technical",
        "Write precisely and technically. Name things exactly; prefer exact terms "
        "over approximations.",
    ),
    "narrative": (
        "Narrative",
        "Write in flowing prose that carries the reader from one point to the next.",
    ),
    "journalistic": (
        "Journalistic",
        "Put the conclusion first, then the supporting detail underneath it.",
    ),
}

FORMATTING: Preset = {
    "prose": (
        "Prose",
        "Answer in prose paragraphs. Use lists only when the content is genuinely "
        "a list.",
    ),
    "bullets": (
        "Bullets",
        "Prefer short bullet points over paragraphs wherever the content allows.",
    ),
    "headings": (
        "Headings + bullets",
        "Structure answers with short headings, and bullets underneath them.",
    ),
    "minimal": (
        "Minimal",
        "Use no headings, no bold, and no bullets unless they are essential.",
    ),
}

READING_LEVELS: Preset = {
    "plain": (
        "Plain language",
        "Use plain, everyday language. Explain any unavoidable jargon the first "
        "time it appears.",
    ),
    "standard": ("Standard", ""),  # the model's default — renders nothing
    "technical": (
        "Technical",
        "Assume domain expertise. Use precise technical vocabulary and skip the "
        "basics.",
    ),
}

RESPONSE_LENGTHS: Preset = {
    "brief": (
        "Brief",
        "Keep answers SHORT — a few sentences, or a tight list. Offer to expand "
        "rather than expanding by default.",
    ),
    "balanced": ("Balanced", ""),  # the model's default — renders nothing
    "thorough": (
        "Thorough",
        "Be thorough: cover the edge cases and the reasoning, not just the "
        "conclusion.",
    ),
}

#: Accessibility modes. The instruction is the NON-NEGOTIABLE core of the mode;
#: ``defaults`` seeds the editable fields when the mode is applied from the UI.
ACCESSIBILITY: dict[str, dict[str, object]] = {
    "dyslexia_friendly": {
        "label": "Dyslexia-friendly",
        "instruction": (
            "This person reads more easily with a specific shape of answer. "
            "Follow these rules in EVERY reply:\n"
            "- One idea per sentence. Keep sentences short.\n"
            "- Use simple, common words in place of long or unusual ones.\n"
            "- Never write a wall of text: break anything longer than about "
            "three sentences into separate short paragraphs.\n"
            "- Put a short, clear heading above each distinct topic.\n"
            "- Use bullets whenever you are listing things.\n"
            "- Leave a blank line between concepts so they are visually "
            "separated.\n"
            "- Keep the formatting the same from answer to answer — same "
            "heading style, same bullet style, every time."
        ),
        "defaults": {
            "reading_level": "plain",
            "formatting": "headings",
            "response_length": "brief",
            "formatting_rules": (
                "Keep sentences under about 15 words.\n"
                "Put the answer first, then the detail."
            ),
        },
    },
    "screen_reader": {
        "label": "Screen-reader friendly",
        "instruction": (
            "This person reads replies with a screen reader. Write so it sounds "
            "right when spoken:\n"
            "- Use plain sentences; avoid ASCII art, tables, and decorative "
            "symbols.\n"
            "- Introduce a list in a sentence before starting it.\n"
            "- Spell out what a code block is for before showing it.\n"
            "- Avoid emoji and avoid bold used purely for emphasis."
        ),
        "defaults": {
            "formatting": "prose",
            "reading_level": "plain",
            "formatting_rules": "",
        },
    },
}


def instruction(preset: Preset, key: str) -> str:
    """The instruction for ``key``: a known preset's sentence, an unknown key
    verbatim (free text), or "" for an empty key. See the module docstring —
    this three-way rule is the contract every profile field follows."""
    key = (key or "").strip()
    if not key:
        return ""
    hit = preset.get(key)
    if hit is not None:
        return hit[1]
    return key


def options(preset: Preset) -> list[dict[str, str]]:
    """``[{key, label}]`` for the /you page's selects."""
    return [{"key": k, "label": v[0]} for k, v in preset.items()]


def accessibility_options() -> list[dict[str, str]]:
    return [
        {"key": k, "label": str(v.get("label") or k)} for k, v in ACCESSIBILITY.items()
    ]
