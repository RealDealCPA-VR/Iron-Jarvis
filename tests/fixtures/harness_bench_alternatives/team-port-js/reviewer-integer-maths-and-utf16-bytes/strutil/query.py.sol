"""parse_query: the port of parseQuery in reference/strutil.js.

Reviewer's alternative: decodeURIComponent written out by hand (no
urllib): every %XX must be two hex digits, the bytes they make must be
strict UTF-8 (no surrogate code points, no overlong forms), and any failure
keeps the WHOLE component as written (after + became a space), as the
reference's try/catch does."""

_HEX = "0123456789abcdefABCDEF"


class _Malformed(Exception):
    pass


def _decode_uri_component(text):
    out = []
    pending = bytearray()
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "%":
            if i + 3 > len(text) or text[i + 1] not in _HEX or text[i + 2] not in _HEX:
                raise _Malformed()
            pending.append(int(text[i + 1:i + 3], 16))
            i += 3
            continue
        if pending:
            out.append(_utf8(pending))
            pending = bytearray()
        out.append(ch)
        i += 1
    if pending:
        out.append(_utf8(pending))
    return "".join(out)


def _utf8(data):
    try:
        # Python's strict UTF-8 refuses surrogates and overlong forms, as
        # decodeURIComponent does (it throws URIError on both).
        return bytes(data).decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise _Malformed() from None


def _component(text):
    spaced = text.replace("+", " ")
    try:
        return _decode_uri_component(spaced)
    except _Malformed:
        return spaced


def parse_query(query):
    """The parameters of a query string: parse_query("?a=1&b=x+y") -> {"a": "1", "b": "x y"}.
    A name given more than once, or written "name[]", collects its values in a list."""
    if not isinstance(query, str):
        raise TypeError("query must be a string")
    text = query[1:] if query.startswith("?") else query
    text = text.partition("#")[0]
    result = {}
    for part in text.split("&"):
        if part == "":
            continue
        name, eq, raw = part.partition("=")
        key = _component(name)
        value = _component(raw) if eq else ""
        if key == "":
            continue
        if key.endswith("[]"):
            key = key[:-2]
            current = result.setdefault(key, [])
            if not isinstance(current, list):
                current = result[key] = [current]
            current.append(value)
        elif key in result:
            if not isinstance(result[key], list):
                result[key] = [result[key]]
            result[key].append(value)
        else:
            result[key] = value
    return result
