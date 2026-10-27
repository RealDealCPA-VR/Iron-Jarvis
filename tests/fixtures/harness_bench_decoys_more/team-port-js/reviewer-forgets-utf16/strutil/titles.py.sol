"""title_case: the port of titleCase in reference/strutil.js.

Reviewer's alternative: no regular expressions for the words. JavaScript's
``\\s`` is spelled out as a set of characters (it is NOT Python's: it has
U+FEFF and lacks U+001C..U+001F and U+0085), the text is split into runs of
space and runs of the rest by hand, and ``[A-Z]`` / ``[a-z]`` / ``[0-9]``
are ASCII ranges."""

_JS_SPACE = frozenset(
    "\t\n\x0b\x0c\r \xa0     　﻿"
    + "".join(chr(c) for c in range(0x2000, 0x200B))
)
SMALL_WORDS = frozenset({
    "a", "an", "and", "as", "at", "but", "by", "en", "for", "if", "in", "nor",
    "of", "on", "or", "per", "the", "to", "v", "vs", "via",
})


def _runs(text):
    """text.split(/(\\s+)/): the words with the spaces between them, an empty
    word at either end when the text starts or ends with spaces."""
    tokens, current, in_space = [], "", False
    for ch in text:
        is_space = ch in _JS_SPACE
        if is_space != in_space:
            tokens.append(current)
            current, in_space = "", is_space
        current += ch
    tokens.append(current)
    return tokens


def _ascii(ch, lo, hi):
    return lo <= ch <= hi


def _capitalize(part):
    """charAt(0).toUpperCase() + slice(1), on UTF-16 code units."""
    if not part:
        return part
    units = part.encode("utf-16-le", "surrogatepass")
    first = units[:2].decode("utf-16-le", "surrogatepass")
    rest = units[2:].decode("utf-16-le", "surrogatepass")
    return first.upper() + rest


def _bare(lower):
    """lower.replace(/^[^a-z]+|[^a-z]+$/g, "")."""
    start, end = 0, len(lower)
    while start < end and not _ascii(lower[start], "a", "z"):
        start += 1
    while end > start and not _ascii(lower[end - 1], "a", "z"):
        end -= 1
    return lower[start:end]


def title_case(text):
    """Title Case for headlines: "the lord of the rings" -> "The Lord of the Rings"."""
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    tokens = _runs(text)
    words = [i for i, t in enumerate(tokens) if t and t[0] not in _JS_SPACE]
    last = words[-1] if words else -1
    starts_phrase = True
    out = []
    for i, token in enumerate(tokens):
        if not token or token[0] in _JS_SPACE:
            out.append(token)
            continue
        if any(_ascii(c, "A", "Z") for c in token[1:]) or any(_ascii(c, "0", "9") for c in token):
            word = token
        else:
            lower = token.lower()
            if not starts_phrase and i != last and _bare(lower) in SMALL_WORDS:
                word = lower
            else:
                word = "-".join(_capitalize(p) for p in lower.split("-"))
        starts_phrase = token[-1] in ":.!?"
        out.append(word)
    return "".join(out)
