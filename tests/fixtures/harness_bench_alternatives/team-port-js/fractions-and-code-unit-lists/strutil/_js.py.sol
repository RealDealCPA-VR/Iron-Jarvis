"""JavaScript strings for the port: lists of UTF-16 code units."""


def units(text):
    out = []
    for ch in text:
        cp = ord(ch)
        if cp > 0xFFFF:
            cp -= 0x10000
            out.append(0xD800 + (cp >> 10))
            out.append(0xDC00 + (cp & 0x3FF))
        else:
            out.append(cp)
    return out


def text_of(code_units):
    out = []
    i = 0
    n = len(code_units)
    while i < n:
        u = code_units[i]
        if 0xD800 <= u <= 0xDBFF and i + 1 < n and 0xDC00 <= code_units[i + 1] <= 0xDFFF:
            out.append(chr(0x10000 + ((u - 0xD800) << 10) + (code_units[i + 1] - 0xDC00)))
            i += 2
        else:
            out.append(chr(u))
            i += 1
    return "".join(out)


def js_integer(x):
    """Number.isInteger."""
    if isinstance(x, bool):
        return False
    if isinstance(x, int):
        return True
    return isinstance(x, float) and x == x and x not in (float("inf"), float("-inf")) and x == int(x)
