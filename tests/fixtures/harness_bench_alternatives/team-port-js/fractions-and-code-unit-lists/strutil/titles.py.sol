"""title_case, ported from titleCase."""

import re

from ._js import text_of, units

JS_WHITESPACE = "\t\n\x0b\x0c\r    -     　﻿"
TOKEN = re.compile(f"[{JS_WHITESPACE}]+|[^{JS_WHITESPACE}]+")
SMALL = frozenset("a an and as at but by en for if in nor of on or per the to v vs via".split())


def _cap(part):
    u = units(part)
    if not u:
        return part
    return text_of(u[:1]).upper() + text_of(u[1:])


def _is_word(token):
    return not re.fullmatch(f"[{JS_WHITESPACE}]+", token)


def title_case(text):
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    tokens = TOKEN.findall(text)
    words = [i for i, t in enumerate(tokens) if _is_word(t)]
    last = words[-1] if words else -1
    result = []
    after_stop = True
    for i, token in enumerate(tokens):
        if not _is_word(token):
            result.append(token)
            continue
        if any("A" <= c <= "Z" for c in token[1:]) or any(c in "0123456789" for c in token):
            result.append(token)
        else:
            low = token.lower()
            core = re.sub("^[^a-z]+", "", re.sub("[^a-z]+$", "", low))
            if core in SMALL and not after_stop and i != last:
                result.append(low)
            else:
                result.append("-".join(_cap(p) for p in low.split("-")))
        after_stop = token[-1] in ":.!?"
    return "".join(result)
