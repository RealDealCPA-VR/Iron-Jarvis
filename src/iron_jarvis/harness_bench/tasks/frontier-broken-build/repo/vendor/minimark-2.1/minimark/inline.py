"""Inline text (strong, emphasis, code, links), read by tinylex."""
import warnings

import tinylex

escape = tinylex.escape


def check_tinylex(version):
    """Warn when the tinylex found is older than the series minimark was tested with."""
    try:
        found = tuple(int(part) for part in tinylex.__version__.split(".")[:2])
    except (AttributeError, ValueError):
        found = (0, 0)
    if found < (0, 4):
        warnings.warn(
            f"minimark {version} is tested with tinylex 0.4.x; tinylex "
            f"{getattr(tinylex, '__version__', '?')} is older and may render text differently",
            RuntimeWarning,
            stacklevel=2,
        )


def render_inline(text):
    """HTML for one line of inline text."""
    out = []
    for token in tinylex.tokenize(text):
        if token.kind == "strong":
            out.append(f"<strong>{escape(token.text)}</strong>")
        elif token.kind == "em":
            out.append(f"<em>{escape(token.text)}</em>")
        elif token.kind == "code":
            out.append(f"<code>{escape(token.text)}</code>")
        elif token.kind == "link":
            out.append(f'<a href="{escape(token.href)}">{escape(token.text)}</a>')
        else:
            out.append(escape(token.text))
    return "".join(out)
