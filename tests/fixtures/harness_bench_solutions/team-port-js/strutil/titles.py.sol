"""title_case: the port of titleCase in reference/strutil.js."""

import re

# JavaScript's \s (Python's \s differs at the edges: \x1c-\x1f, \x85, ﻿).
_JS_SPACE = "\t\n\x0b\x0c\r    -     　﻿"
_SPLIT = re.compile(f"([{_JS_SPACE}]+)")
_ALL_SPACE = re.compile(f"[{_JS_SPACE}]+")
_EDGES = re.compile(r"^[^a-z]+|[^a-z]+$")

SMALL_WORDS = {
    "a", "an", "and", "as", "at", "but", "by", "en", "for", "if", "in", "nor",
    "of", "on", "or", "per", "the", "to", "v", "vs", "via",
}


def _first_unit_upper(part):
    """charAt(0).toUpperCase() + slice(1), on UTF-16 code units."""
    if part == "":
        return part
    units = part.encode("utf-16-le", "surrogatepass")
    first = units[:2].decode("utf-16-le", "surrogatepass")
    rest = units[2:].decode("utf-16-le", "surrogatepass")
    return first.upper() + rest


def _is_space(token):
    return _ALL_SPACE.fullmatch(token) is not None


def title_case(text):
    """Title Case for headlines: "the lord of the rings" -> "The Lord of the Rings"."""
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    tokens = _SPLIT.split(text)
    last = -1
    for i, token in enumerate(tokens):
        if token != "" and not _is_space(token):
            last = i
    starts_phrase = True
    out = []
    for i, token in enumerate(tokens):
        if token == "" or _is_space(token):
            out.append(token)
            continue
        if re.search(r"[A-Z]", token[1:]) or re.search(r"[0-9]", token):
            word = token
        else:
            lower = token.lower()
            bare = _EDGES.sub("", lower)
            if not starts_phrase and i != last and bare in SMALL_WORDS:
                word = lower
            else:
                word = "-".join(_first_unit_upper(p) for p in lower.split("-"))
        starts_phrase = token[-1:] in (":", ".", "!", "?")
        out.append(word)
    return "".join(out)
