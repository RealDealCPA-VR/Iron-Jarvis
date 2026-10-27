"""parse_query: the port of parseQuery in reference/strutil.js."""

import re

_ESCAPES = re.compile(r"(?:%[0-9A-Fa-f]{2})+")
_INDEX = re.compile(r"0|[1-9][0-9]*")


def _decode_run(match):
    run = match.group(0)
    data = bytes(int(run[i + 1:i + 3], 16) for i in range(0, len(run), 3))
    return data.decode("utf-8")  # strict, like decodeURIComponent


def _decode_component(text):
    spaced = text.replace("+", " ")
    try:
        # every "%" must start a valid escape, else decodeURIComponent throws
        if "%" in _ESCAPES.sub("", spaced):
            return spaced
        return _ESCAPES.sub(_decode_run, spaced)
    except UnicodeDecodeError:
        return spaced


def _index_like(key):
    return _INDEX.fullmatch(key) is not None and int(key) < 2**32 - 1


def parse_query(query):
    """The parameters of a query string: parse_query("?a=1&b=x+y") -> {"a": "1", "b": "x y"}.
    A name given more than once, or written "name[]", collects its values in a list."""
    if not isinstance(query, str):
        raise TypeError("query must be a string")
    text = query
    if text.startswith("?"):
        text = text[1:]
    h = text.find("#")
    if h != -1:
        text = text[:h]
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
            if name == "__proto__":
                continue  # sets the object's prototype in JavaScript, never an own key
            if name not in result:
                result[name] = []
            elif not isinstance(result[name], list):
                result[name] = [result[name]]
            result[name].append(value)
        elif key == "__proto__":
            continue
        elif key in result:
            if not isinstance(result[key], list):
                result[key] = [result[key]]
            result[key].append(value)
        else:
            result[key] = value
    # JavaScript objects list integer-like keys first, in ascending order.
    ints = sorted((k for k in result if _index_like(k)), key=int)
    others = [k for k in result if not _index_like(k)]
    return {k: result[k] for k in ints + others}
