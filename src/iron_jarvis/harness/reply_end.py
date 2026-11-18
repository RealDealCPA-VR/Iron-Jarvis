"""ONE READING OF "WAS THIS REPLY CUT SHORT?" (fourth review CS1, CS2, CS3).

A reply can end on its ``done`` frame without having finished: the model's
last answer ran out of output tokens (``truncated``), or a model call was cut
off mid-answer and never recovered (``route.cut_off`` with ``recovered`` not
true). The day notice, an approved plan's act turn, the night shift's ending,
an attempt's row and the next turn's history note all read it HERE, so no
reader can call such a reply "finished" with its own copy of the rule.

It reads a ``done`` frame and a reply LINE alike: the line keeps the same two
keys under the same names (``routes/build._ReplyWatch.lines``).

A leaf module: it imports nothing from the night, the attempts or the routes,
so every layer can import it without a cycle.
"""

from __future__ import annotations

from typing import Any

#: The answer ran out of output tokens (``truncated``).
LENGTH = "length"
#: A model call was cut off mid-answer and not recovered (``route.cut_off``).
CUT_OFF = "cut_off"

#: What happened, in the user's words (the first is ``hosted/words.
#: OUTPUT_LIMIT_CUT``'s own phrase).
CUT_WORDS: dict[str, str] = {
    LENGTH: "Its answer was cut off at the model's output limit, so it did not finish.",
    CUT_OFF: "The model's answers kept being cut off, so it did not finish.",
}


def cut_short(d: Any) -> str:
    """:data:`LENGTH` when ``d["truncated"]`` is set, :data:`CUT_OFF` when
    ``d["route"]["cut_off"]`` is a dict whose ``recovered`` is not set, ""
    otherwise. ``d`` is a ``done`` frame or a reply line; junk reads as "".
    Pure; never raises."""
    if not isinstance(d, dict):
        return ""
    if d.get("truncated"):
        return LENGTH
    route = d.get("route")
    cut = route.get("cut_off") if isinstance(route, dict) else None
    if isinstance(cut, dict) and not cut.get("recovered"):
        return CUT_OFF
    return ""


__all__ = ["CUT_OFF", "CUT_WORDS", "LENGTH", "cut_short"]
