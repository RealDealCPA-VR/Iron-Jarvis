"""parse_query, ported from parseQuery."""

HEX = "0123456789abcdefABCDEF"


class _Malformed(Exception):
    pass


def _read_escape(text, i):
    pair = text[i + 1:i + 3]
    if len(pair) != 2 or pair[0] not in HEX or pair[1] not in HEX:
        raise _Malformed()
    return int(pair, 16)


def _decode_uri_component(text):
    out = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch != "%":
            out.append(ch)
            i += 1
            continue
        first = _read_escape(text, i)
        i += 3
        if first < 0x80:
            out.append(chr(first))
            continue
        if 0xC2 <= first <= 0xDF:
            more, cp, low = 1, first & 0x1F, 0x80
        elif 0xE0 <= first <= 0xEF:
            more, cp, low = 2, first & 0x0F, 0x800
        elif 0xF0 <= first <= 0xF4:
            more, cp, low = 3, first & 0x07, 0x10000
        else:
            raise _Malformed()
        for _ in range(more):
            if i >= len(text) or text[i] != "%":
                raise _Malformed()
            byte = _read_escape(text, i)
            i += 3
            if byte & 0xC0 != 0x80:
                raise _Malformed()
            cp = (cp << 6) | (byte & 0x3F)
        if cp < low or cp > 0x10FFFF or 0xD800 <= cp <= 0xDFFF:
            raise _Malformed()
        out.append(chr(cp))
    return "".join(out)


def _component(raw):
    raw = raw.replace("+", " ")
    try:
        return _decode_uri_component(raw)
    except _Malformed:
        return raw


def _add(result, name, value, force_list):
    if name in result:
        current = result[name]
        result[name] = (current if isinstance(current, list) else [current]) + [value]
    else:
        result[name] = [value] if force_list else value


def parse_query(query):
    if not isinstance(query, str):
        raise TypeError("query must be a string")
    body = query[1:] if query[:1] == "?" else query
    body = body.partition("#")[0]
    result = {}
    for piece in body.split("&"):
        if not piece:
            continue
        raw_key, eq, raw_value = piece.partition("=")
        key = _component(raw_key)
        value = _component(raw_value) if eq else ""
        if not key:
            continue
        if key[-2:] == "[]":
            _add(result, key[:-2], value, True)
        else:
            _add(result, key, value, False)
    return result
