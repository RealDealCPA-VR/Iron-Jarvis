"""wrap_text, ported from wrapText: every string handled as UTF-16 code units."""

from ._js import js_integer, text_of, units

SPACE = ord(" ")


def wrap_text(text, width):
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    if not js_integer(width) or width < 1:
        raise ValueError("width must be a positive integer")
    width = int(width)
    out = []
    for paragraph in text.split("\n"):
        line = []  # code units of the line being built
        for word_text in paragraph.split(" "):
            if word_text == "":
                continue
            word = units(word_text)
            while len(word) > width:
                room = width - len(line) - 1 if line else width
                if room > 0:
                    out.append(line + [SPACE] + word[:room] if line else word[:room])
                    word = word[room:]
                else:
                    out.append(line)
                line = []
            if not word:
                continue
            if not line:
                line = word
            elif len(line) + 1 + len(word) <= width:
                line = line + [SPACE] + word
            else:
                out.append(line)
                line = word
        out.append(line)
    return "\n".join(text_of(u) for u in out)
