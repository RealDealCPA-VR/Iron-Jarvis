"""YOUR OWN THEME COLOURS, settable from chat (chat-first plan, Wave 7 group C).

The walkthrough's request 12 — "Change the theme to something green and
calm" — had nowhere to land: ``device.theme`` takes only the built-in themes
(none of them green), and the theme maker (Settings › Appearance,
``dashboard/components/ThemeMaker.tsx``) is a screen-only form.

``device.theme_colors`` is that form's PICKS as one per-device setting:

    {"accent": "#2f855a", "second": "#1e3a2f", "surface": "tinted",
     "mode": "dark", "name": "Green and calm"}

The daemon only checks and stores the picks. The SCREEN turns them into a
theme with the theme maker's own generator (``lib/themePalette.ts``
``generatePalette``, which also keeps every text colour readable), saves it
among this device's own themes under one fixed id, and paints it
(``lib/deviceSettings.ts``) — so there is no second palette generator here.

ONE THEME AT A TIME (``LINKED``): this row and ``device.theme`` name the same
thing two ways, so setting either one to a value clears the other, in the
same write (``settings.writer``). Whichever was set LAST is what the screen
shows, and an Undo puts both back.
"""

from __future__ import annotations

import re
from typing import Any

SURFACES = ("neutral", "cool", "warm", "tinted")
MODES = ("dark", "light", "system")
#: The theme maker's own name limit (lib/themePalette.ts MAX_NAME).
MAX_NAME = 40
KEYS = ("accent", "second", "surface", "mode", "name")

_HEX = re.compile(r"^#?([0-9a-fA-F]{6}|[0-9a-fA-F]{3})$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")

#: The two rows that each name "the theme on this device".
LINKED: dict[str, str] = {"device.theme": "device.theme_colors", "device.theme_colors": "device.theme"}

#: What ``config_list`` tells the model about the value's shape.
HOW = (
    " (a theme of your own colours: {\"accent\": \"#RRGGBB\" (the main colour), \"second\": "
    "\"#RRGGBB\" (optional, background glows only), \"surface\": neutral|cool|warm|tinted, "
    "\"mode\": dark|light|system, \"name\": up to 40 characters}; Iron Jarvis keeps every text "
    "colour readable. Setting it replaces the built-in theme, and setting a built-in theme "
    "replaces it)"
)


def _hex(raw: Any, what: str) -> str:
    if not isinstance(raw, str) or not _HEX.match(raw.strip()):
        raise ValueError(f"need the {what} written as #RRGGBB (for example #2f855a)")
    h = raw.strip().lstrip("#").lower()
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return "#" + h


def normalize(value: Any) -> dict[str, Any]:
    """The picks, checked and in one canonical form (lower-case six-digit
    hex; ``second`` and ``name`` only when given). Raises ValueError with
    the words the writer puts after the row's label."""
    if not isinstance(value, dict):
        raise ValueError("must be a set of colours: accent, surface and mode")
    extra = sorted(k for k in value if k not in KEYS)
    if extra:
        raise ValueError(f"take only accent, second, surface, mode and name, not {', '.join(map(str, extra))}")
    if "accent" not in value:
        raise ValueError("need a main colour (accent) written as #RRGGBB (for example #2f855a)")
    out: dict[str, Any] = {"accent": _hex(value.get("accent"), "main colour (accent)")}
    second = value.get("second")
    if second not in (None, ""):
        out["second"] = _hex(second, "second colour")
    surface = value.get("surface", "neutral")
    if surface in (None, ""):
        surface = "neutral"
    if surface not in SURFACES:
        raise ValueError(f"take a surface of {', '.join(SURFACES)}")
    out["surface"] = surface
    mode = value.get("mode", "dark")
    if mode in (None, ""):
        mode = "dark"
    if mode not in MODES:
        raise ValueError(f"take a mode of {', '.join(MODES)}")
    out["mode"] = mode
    name = value.get("name")
    if name not in (None, ""):
        if not isinstance(name, str):
            raise ValueError("take a name written as text")
        clean = " ".join(_CONTROL.sub(" ", name).split())
        if len(clean) > MAX_NAME:
            raise ValueError(f"take a name of at most {MAX_NAME} characters")
        if clean:
            out["name"] = clean
    return out


def describe(value: Any) -> str:
    """The value in words, for chat's "Changed …: old → new" line."""
    if not isinstance(value, dict) or not value.get("accent"):
        return str(value)
    name = value.get("name")
    surface = value.get("surface", "neutral")
    mode = value.get("mode", "dark")
    mode_words = {"dark": "dark", "light": "light", "system": "following Windows' light or dark"}.get(mode, mode)
    parts = f"main colour {value['accent']}, {surface} background, {mode_words}"
    if value.get("second"):
        parts = f"main colour {value['accent']} with {value['second']}, {surface} background, {mode_words}"
    return f"{name} ({parts})" if name else parts
