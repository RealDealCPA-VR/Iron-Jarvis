"""wrap_text: the port of wrapText in reference/strutil.js."""

import math


def _length(text):
    """String.prototype.length: UTF-16 code units."""
    return len(text.encode("utf-16-le", "surrogatepass")) // 2


def _slice(text, start, end=None):
    """String.prototype.slice on UTF-16 code units."""
    data = text.encode("utf-16-le", "surrogatepass")
    stop = len(data) if end is None else 2 * end
    return data[2 * start:stop].decode("utf-16-le", "surrogatepass")


def _is_js_integer(x):
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return False
    return math.isfinite(x) and float(x).is_integer()


def wrap_text(text, width):
    """Wraps text to lines of at most ``width`` characters (see the reference)."""
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    if not _is_js_integer(width) or width < 1:
        raise ValueError("width must be a positive integer")
    width = int(width)
    lines = []
    for paragraph in text.split("\n"):
        words = [w for w in paragraph.split(" ") if w != ""]
        line = ""
        for word in words:
            while _length(word) > width:
                room = width if line == "" else width - _length(line) - 1
                if room > 0:
                    head = _slice(word, 0, room)
                    lines.append(head if line == "" else line + " " + head)
                    word = _slice(word, room)
                else:
                    lines.append(line)
                line = ""
            if word == "":
                continue
            if line == "":
                line = word
            elif _length(line) + 1 + _length(word) <= width:
                line += " " + word
            else:
                lines.append(line)
                line = word
        lines.append(line)
    return "\n".join(lines)
