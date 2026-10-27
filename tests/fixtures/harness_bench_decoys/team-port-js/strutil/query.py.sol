"""parse_query: the port of parseQuery in reference/strutil.js."""

from urllib.parse import unquote_plus


def parse_query(query):
    """The parameters of a query string."""
    if not isinstance(query, str):
        raise TypeError("query must be a string")
    text = query.lstrip("?").split("#", 1)[0]
    result = {}
    for part in text.split("&"):
        if not part:
            continue
        key, _, value = part.partition("=")
        key = unquote_plus(key)
        value = unquote_plus(value)
        if not key:
            continue
        if key.endswith("[]"):
            key = key[:-2]
            existing = result.get(key, [])
            result[key] = (existing if isinstance(existing, list) else [existing]) + [value]
        elif key in result:
            existing = result[key]
            result[key] = (existing if isinstance(existing, list) else [existing]) + [value]
        else:
            result[key] = value
    return result
