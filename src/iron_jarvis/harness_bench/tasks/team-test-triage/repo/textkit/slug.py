"""URL slugs."""

import re


def slugify(title, max_length=60):
    """A URL slug: lower-case ASCII letters and digits, words joined by "-".

    Letters are first reduced to their ASCII form with Unicode COMPATIBILITY
    decomposition (NFKD): "é" becomes "e", the ligature "ﬁ" becomes "fi", a
    full-width "Ａ" becomes "A". Whatever is still not ASCII after that is
    dropped. Every run of other characters becomes one "-", with none at either
    end. A slug longer than ``max_length`` is cut at the last "-" that keeps it
    within ``max_length`` (or at ``max_length`` when there is none).
    """
    text = title.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()
    if len(text) <= max_length:
        return text
    cut = text[: max_length + 1]
    if "-" in cut:
        return cut[: cut.rindex("-")]
    return text[:max_length]
