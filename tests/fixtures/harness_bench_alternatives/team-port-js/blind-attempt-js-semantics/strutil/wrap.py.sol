"""wrap_text: the port of wrapText in reference/strutil.js."""

from ._js import from_units, is_integer, to_units


def wrap_text(text, width):
    """Wraps text to lines of at most ``width`` characters (see the reference)."""
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    if not is_integer(width) or width < 1:
        raise ValueError("width must be a positive integer")
    width = int(width)
    lines = []
    for paragraph in to_units(text).split("\n"):
        words = [w for w in paragraph.split(" ") if w != ""]
        line = ""
        for word in words:
            while len(word) > width:
                room = width if line == "" else width - len(line) - 1
                if room > 0:
                    lines.append(word[:room] if line == "" else line + " " + word[:room])
                    word = word[room:]
                else:
                    lines.append(line)
                line = ""
            if word == "":
                continue
            if line == "":
                line = word
            elif len(line) + 1 + len(word) <= width:
                line += " " + word
            else:
                lines.append(line)
                line = word
        lines.append(line)
    return from_units("\n".join(lines))
