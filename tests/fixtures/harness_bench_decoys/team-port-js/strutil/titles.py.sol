"""title_case: the port of titleCase in reference/strutil.js."""

SMALL_WORDS = {
    "a", "an", "and", "as", "at", "but", "by", "en", "for", "if", "in", "nor",
    "of", "on", "or", "per", "the", "to", "v", "vs", "via",
}


def title_case(text):
    """Title Case for headlines."""
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    words = text.split()
    out = []
    for i, word in enumerate(words):
        if any(c.isupper() for c in word[1:]) or any(c.isdigit() for c in word):
            out.append(word)
        elif 0 < i < len(words) - 1 and word.lower() in SMALL_WORDS and not words[i - 1].endswith((":", ".", "!", "?")):
            out.append(word.lower())
        else:
            out.append("-".join(p.capitalize() for p in word.lower().split("-")))
    return " ".join(out)
