"""title_case: the port of titleCase in reference/strutil.js."""

import re

# JavaScript's \s
_WS = "\t\n\u000b\u000c\r    -     　﻿"
_SPLIT = re.compile("([" + _WS + "]+)")
_ALL_WS = re.compile("[" + _WS + "]+\\Z")
_EDGES = re.compile(r"^[^a-z]+|[^a-z]+\Z")

_SMALL_WORDS = {
    "a", "an", "and", "as", "at", "but", "by", "en", "for", "if", "in", "nor",
    "of", "on", "or", "per", "the", "to", "v", "vs", "via",
}


def _is_space(token):
    return _ALL_WS.match(token) is not None


def _capitalize(part):
    if part == "" or ord(part[0]) > 0xFFFF:
        return part  # charAt(0) is half a surrogate pair: unchanged
    return part[0].upper() + part[1:]


def title_case(text):
    """Title Case for headlines: "the lord of the rings" -> "The Lord of the Rings"."""
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    tokens = _SPLIT.split(text)
    last = -1
    for i, t in enumerate(tokens):
        if t != "" and not _is_space(t):
            last = i
    starts_phrase = True
    out_tokens = []
    for i, token in enumerate(tokens):
        if token == "" or _is_space(token):
            out_tokens.append(token)
            continue
        if re.search("[A-Z]", token[1:]) or re.search("[0-9]", token):
            out = token
        else:
            lower = token.lower()
            bare = _EDGES.sub("", lower)
            if not starts_phrase and i != last and bare in _SMALL_WORDS:
                out = lower
            else:
                out = "-".join(_capitalize(p) for p in lower.split("-"))
        starts_phrase = re.search(r"[:.!?]\Z", token) is not None
        out_tokens.append(out)
    return "".join(out_tokens)
