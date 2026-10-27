"""parse_query: the port of parseQuery in reference/strutil.js."""

import re

_ESCAPE_RUN = re.compile(r"(?:%[0-9A-Fa-f]{2})+")


def _decode_uri_component(text):
    """decodeURIComponent: every %XX run is UTF-8; a stray "%" or bytes that
    are not UTF-8 raise ValueError (JavaScript's URIError)."""
    out = []
    pos = 0
    for match in _ESCAPE_RUN.finditer(text):
        plain = text[pos:match.start()]
        if "%" in plain:
            raise ValueError("malformed escape")
        out.append(plain)
        out.append(bytes.fromhex(match.group().replace("%", "")).decode("utf-8"))
        pos = match.end()
    rest = text[pos:]
    if "%" in rest:
        raise ValueError("malformed escape")
    out.append(rest)
    return "".join(out)


def _decode_component(text):
    spaced = text.replace("+", " ")
    try:
        return _decode_uri_component(spaced)
    except ValueError:
        # A malformed escape: the component is kept as it was written.
        return spaced


def parse_query(query):
    """The parameters of a query string: parse_query("?a=1&b=x+y") -> {"a": "1", "b": "x y"}.
    A name given more than once, or written "name[]", collects its values in a list."""
    if not isinstance(query, str):
        raise TypeError("query must be a string")
    text = query
    if text.startswith("?"):
        text = text[1:]
    hash_at = text.find("#")
    if hash_at != -1:
        text = text[:hash_at]
    result = {}
    for part in text.split("&"):
        if part == "":
            continue
        eq = part.find("=")
        key = _decode_component(part if eq == -1 else part[:eq])
        value = "" if eq == -1 else _decode_component(part[eq + 1:])
        if key == "":
            continue
        if key.endswith("[]"):
            name = key[:-2]
            if name not in result:
                result[name] = []
            elif not isinstance(result[name], list):
                result[name] = [result[name]]
            result[name].append(value)
        elif key in result:
            if not isinstance(result[key], list):
                result[key] = [result[key]]
            result[key].append(value)
        else:
            result[key] = value
    return result
