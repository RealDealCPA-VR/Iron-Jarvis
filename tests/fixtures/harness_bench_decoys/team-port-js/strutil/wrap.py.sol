"""wrap_text: the port of wrapText in reference/strutil.js."""


def wrap_text(text, width):
    """Wraps text to lines of at most ``width`` characters."""
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    if not isinstance(width, int) or width < 1:
        raise ValueError("width must be a positive integer")
    lines = []
    for paragraph in text.splitlines() or [""]:
        line = ""
        for word in paragraph.split():
            while len(word) > width:
                if line:
                    lines.append(line)
                    line = ""
                lines.append(word[:width])
                word = word[width:]
            if not word:
                continue
            if not line:
                line = word
            elif len(line) + 1 + len(word) <= width:
                line += " " + word
            else:
                lines.append(line)
                line = word
        lines.append(line)
    return "\n".join(lines)
