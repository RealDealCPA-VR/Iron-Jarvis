"""tinylex 0.3 — splits a line of text into inline tokens.

    >>> tinylex.tokenize("a **b** c")
    [Token(kind='text', text='a '), Token(kind='strong', text='b'), Token(kind='text', text=' c')]

Token kinds: ``text``, ``strong`` (``**b**``) and ``em`` (``*e*``).
"""
import re
from collections import namedtuple

__version__ = "0.3.1"
__all__ = ["Token", "tokenize", "escape", "__version__"]

Token = namedtuple("Token", ["kind", "text"])

_PATTERN = re.compile(r"\*\*(?P<strong>[^*]+)\*\*|\*(?P<em>[^*]+)\*")


def tokenize(text):
    """The tokens of one line of text, in order."""
    out = []
    pos = 0
    for match in _PATTERN.finditer(text):
        if match.start() > pos:
            out.append(Token("text", text[pos:match.start()]))
        if match.group("strong") is not None:
            out.append(Token("strong", match.group("strong")))
        else:
            out.append(Token("em", match.group("em")))
        pos = match.end()
    if pos < len(text):
        out.append(Token("text", text[pos:]))
    return out


def escape(text):
    """``text`` made safe to put between HTML tags."""
    return text.replace("<", "&lt;").replace(">", "&gt;")
