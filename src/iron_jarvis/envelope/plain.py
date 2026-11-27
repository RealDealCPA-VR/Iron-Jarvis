"""What a model of the user's own is good at, in plain words (release 9).

The SAME up-to-three lines the Model servers card shows
(``dashboard/components/connections/EnvelopeCard.plainModelLines`` is the
twin: change both together), each said ONLY when it was measured:

* tools — from the measured tool rungs, or the guided state;
* how much it holds — pages of made-up text it read back to its first line
  ("at least" when a larger window was never tried);
* how fast it writes — words a second.

Nothing measured, nothing said: a figure is never guessed.
"""

from __future__ import annotations

from .probes import PAGE_CHARS
from .profile import TOOL_PROTOCOL_LADDER, TOOL_PROTOCOL_THRESHOLDS, CapabilityProfile

#: The card's guided line (the reply's quiet line says the same reason).
GUIDED_CARD_LINE = "It had trouble with tools, so Iron Jarvis is guiding it step by step."


def plain_lines(profile: CapabilityProfile | None, guided: bool = False) -> list[str]:
    if profile is None:
        return [GUIDED_CARD_LINE] if guided else []
    lines: list[str] = []
    mf = set(profile.measured_fields or [])
    if guided:
        lines.append(GUIDED_CARD_LINE)
    elif profile.is_measured():
        scored = [r for r in TOOL_PROTOCOL_LADDER if f"tool_protocols.{r}" in mf]
        if scored:
            top = next(
                (
                    r for r in TOOL_PROTOCOL_LADDER
                    if f"tool_protocols.{r}" in mf
                    and profile.tool_protocols.get(r, 0.0) >= TOOL_PROTOCOL_THRESHOLDS[r]
                ),
                None,
            )
            if top == "native":
                lines.append("Uses tools well.")
            elif top == "strict_json":
                lines.append("Uses tools when guided step by step.")
            elif len(scored) == len(TOOL_PROTOCOL_LADDER):
                lines.append("Does not use tools reliably.")
    if "holds_chars" in mf and profile.holds_chars >= PAGE_CHARS:
        if profile.holds_more == 1:
            lines.append(f"Holds at least {profile.holds_chars // PAGE_CHARS} pages at once.")
        else:
            # JS Math.round twin: half rounds up.
            pages = int(profile.holds_chars / PAGE_CHARS + 0.5)
            lines.append(f"Holds about {pages} pages at once.")
    if "words_per_second" in mf and profile.words_per_second > 0:
        n = int(profile.words_per_second + 0.5)
        if n < 1:
            lines.append("Writes less than one word a second.")
        elif n == 1:
            lines.append("Writes about 1 word a second.")
        else:
            lines.append(f"Writes about {n} words a second.")
    return lines
