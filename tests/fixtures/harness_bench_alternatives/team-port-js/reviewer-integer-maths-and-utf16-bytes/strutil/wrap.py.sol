"""wrap_text: the port of wrapText in reference/strutil.js.

Reviewer's alternative: every length and cut is done on the text's UTF-16
code units (a list of ints), as JavaScript's ``length`` and ``slice`` do, and
turned back into a str only to join."""


def _units(text):
    data = text.encode("utf-16-le", "surrogatepass")
    return [data[i] | (data[i + 1] << 8) for i in range(0, len(data), 2)]


def _text(units):
    return b"".join(u.to_bytes(2, "little") for u in units).decode("utf-16-le", "surrogatepass")


def wrap_text(text, width):
    """Wraps text to lines of at most ``width`` characters (see the reference)."""
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    if isinstance(width, bool) or not isinstance(width, (int, float)) or width != width \
            or width in (float("inf"), float("-inf")) or width != int(width) or width < 1:
        raise ValueError("width must be a positive integer")
    width = int(width)
    lines = []
    space = _units(" ")
    for paragraph in text.split("\n"):
        words = [_units(w) for w in paragraph.split(" ") if w != ""]
        line = []
        for word in words:
            while len(word) > width:
                room = width if not line else width - len(line) - 1
                if room > 0:
                    lines.append(word[:room] if not line else line + space + word[:room])
                    word = word[room:]
                else:
                    lines.append(line)
                line = []
            if not word:
                continue
            if not line:
                line = word
            elif len(line) + 1 + len(word) <= width:
                line = line + space + word
            else:
                lines.append(line)
                line = word
        lines.append(line)
    return "\n".join(_text(units) for units in lines)
