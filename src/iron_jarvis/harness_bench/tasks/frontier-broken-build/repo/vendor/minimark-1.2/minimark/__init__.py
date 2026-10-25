"""minimark 1.2 — a very small markup language.

    >>> import minimark
    >>> minimark.render("Hello *world*")
    '<p>Hello <em>world</em></p>\\n'

Supported: ``#`` headings, paragraphs, ``- `` lists, ``**strong**`` and
``*emphasis*``. See CHANGELOG.md for the known issues of this release.
"""
import re

__version__ = "1.2.0"
__all__ = ["render", "__version__"]

_HEADING = re.compile(r"^(#{1,4})\s+(.*)$")
_STRONG = re.compile(r"\*\*([^*]+)\*\*")
_EM = re.compile(r"\*([^*]+)\*")


def _escape(text):
    # Escapes the angle brackets. (& and " are left as they are: see CHANGELOG.md.)
    return text.replace("<", "&lt;").replace(">", "&gt;")


def _inline(text):
    text = _escape(text)
    text = _STRONG.sub(r"<strong>\1</strong>", text)
    return _EM.sub(r"<em>\1</em>", text)


def render(text):
    """Render minimark text to an HTML fragment (a string ending in a line break)."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out = []
    para = []
    items = []

    def flush():
        if para:
            out.append("<p>" + _inline(" ".join(para)) + "</p>")
            para.clear()
        if items:
            out.append("<ul>")
            out.extend("<li>" + _inline(item) + "</li>" for item in items)
            out.append("</ul>")
            items.clear()

    for raw in lines:
        line = raw.strip()
        if not line:
            flush()
            continue
        heading = _HEADING.match(line)
        if heading:
            flush()
            level = len(heading.group(1)) + 1
            out.append(f"<h{level}>" + _inline(heading.group(2).strip()) + f"</h{level}>")
        elif line.startswith("- "):
            if para:
                flush()
            items.append(line[2:].strip())
        else:
            if items:
                flush()
            para.append(line)
    flush()
    return "".join(part + "\n" for part in out)
