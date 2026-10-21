"""textkit: small text statistics and plain-text reports.

Public functions:

    words, sentences, ngrams, stopwords, content_words          (tokenizing)
    frequencies, top_words, syllables, flesch_reading_ease,
    lexical_diversity, average_word_length, longest_words       (statistics)
    bar, histogram, table, summary                               (rendering)

Settings live in the module-level CONFIG dict; ``configure(**changes)``
changes them and ``reset_config()`` puts the defaults back.
"""

import math
import re
from collections import Counter

DEFAULTS = {
    "min_word_length": 1,
    "lowercase": True,
    "stopwords_file": None,
    "bar_width": 30,
    "bar_char": "#",
    "decimals": 2,
}

CONFIG = dict(DEFAULTS)

_BUILTIN_STOPWORDS = (
    "a an and are as at be but by for from had has have he her his i in is it its "
    "not of on or she that the their they this to was we were will with you"
).split()

_STOPWORDS_CACHE = {}

_WORD_RE = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")
_SENTENCE_RE = re.compile(r"[^.!?]+[.!?]*")
_VOWELS = "aeiouy"


def configure(**changes):
    """Change settings; an unknown name or a bad value raises ValueError."""
    for key, value in changes.items():
        if key not in DEFAULTS:
            raise ValueError(f"unknown setting {key!r}")
        if key in ("min_word_length", "bar_width", "decimals"):
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"{key} must be a whole number of at least 0")
        if key == "bar_char" and (not isinstance(value, str) or len(value) != 1):
            raise ValueError("bar_char must be one character")
        CONFIG[key] = value


def reset_config():
    CONFIG.clear()
    CONFIG.update(DEFAULTS)


# --------------------------------------------------------------------------- #
# Tokenizing
# --------------------------------------------------------------------------- #


def words(text):
    """The words of ``text``: letters with an optional apostrophe part
    ("don't"), lower-cased when CONFIG["lowercase"], and only those at least
    CONFIG["min_word_length"] letters long."""
    found = _WORD_RE.findall(text)
    if CONFIG["lowercase"]:
        found = [w.lower() for w in found]
    return [w for w in found if len(w) >= CONFIG["min_word_length"]]


def sentences(text):
    """The sentences of ``text``, stripped, empty ones dropped."""
    out = []
    for piece in _SENTENCE_RE.findall(text):
        piece = " ".join(piece.split())
        if piece and any(ch.isalpha() for ch in piece):
            out.append(piece)
    return out


def ngrams(tokens, n):
    """Consecutive n-tuples of ``tokens``; n must be at least 1."""
    if n < 1:
        raise ValueError("n must be at least 1")
    return [tuple(tokens[i:i + n]) for i in range(len(tokens) - n + 1)]


def stopwords():
    """The stop words: the built-in list, or one word per line from
    CONFIG["stopwords_file"] (blank lines and lines starting with # skipped).
    A file is read once and cached."""
    path = CONFIG["stopwords_file"]
    if path is None:
        return frozenset(_BUILTIN_STOPWORDS)
    if path not in _STOPWORDS_CACHE:
        with open(path, encoding="utf-8") as fh:
            lines = [line.strip() for line in fh]
        _STOPWORDS_CACHE[path] = frozenset(
            line.lower() for line in lines if line and not line.startswith("#")
        )
    return _STOPWORDS_CACHE[path]


def content_words(text):
    """``words(text)`` without the stop words (compared lower-case)."""
    stop = stopwords()
    return [w for w in words(text) if w.lower() not in stop]


# --------------------------------------------------------------------------- #
# Statistics
# --------------------------------------------------------------------------- #


def frequencies(text, include_stopwords=True):
    """A Counter of the words of ``text``."""
    return Counter(words(text) if include_stopwords else content_words(text))


def top_words(text, n=5, include_stopwords=False):
    """The n most frequent words as (word, count), most frequent first and
    alphabetical among equal counts."""
    counts = frequencies(text, include_stopwords=include_stopwords)
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return ranked[:n]


def syllables(word):
    """A syllable estimate: groups of vowels (y counts), minus a silent final
    "e" (but not "le" after a consonant), at least one."""
    w = word.lower().strip("'")
    if not w:
        return 0
    groups = 0
    previous_vowel = False
    for ch in w:
        is_vowel = ch in _VOWELS
        if is_vowel and not previous_vowel:
            groups += 1
        previous_vowel = is_vowel
    if w.endswith("e") and not (w.endswith("le") and len(w) > 2 and w[-3] not in _VOWELS):
        groups -= 1
    return max(groups, 1)


def flesch_reading_ease(text):
    """206.835 - 1.015 * (words / sentences) - 84.6 * (syllables / words),
    rounded to CONFIG["decimals"]; 0.0 for a text with no words."""
    ws = words(text)
    ss = sentences(text)
    if not ws or not ss:
        return 0.0
    total_syllables = sum(syllables(w) for w in ws)
    score = 206.835 - 1.015 * (len(ws) / len(ss)) - 84.6 * (total_syllables / len(ws))
    return round(score, CONFIG["decimals"])


def lexical_diversity(text):
    """Distinct words / all words, rounded to CONFIG["decimals"] (0.0 if none)."""
    ws = words(text)
    if not ws:
        return 0.0
    return round(len(set(ws)) / len(ws), CONFIG["decimals"])


def average_word_length(text):
    """The mean number of letters per word, rounded to CONFIG["decimals"]."""
    ws = words(text)
    if not ws:
        return 0.0
    letters = sum(sum(ch.isalpha() for ch in w) for w in ws)
    return round(letters / len(ws), CONFIG["decimals"])


def longest_words(text, n=3):
    """The n longest distinct words, longest first, alphabetical among equals."""
    distinct = set(words(text))
    return sorted(distinct, key=lambda w: (-len(w), w))[:n]


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #


def bar(value, maximum):
    """A bar of CONFIG["bar_char"], CONFIG["bar_width"] long at ``maximum``,
    rounded half up; an empty string when maximum is 0."""
    if maximum <= 0 or value <= 0:
        return ""
    length = math.floor(value / maximum * CONFIG["bar_width"] + 0.5)
    return CONFIG["bar_char"] * length


def histogram(text, n=5):
    """One line per top word: the word left-aligned to the longest one, " | ",
    its bar, a space and its count."""
    ranked = top_words(text, n)
    if not ranked:
        return ""
    width = max(len(word) for word, _ in ranked)
    top = ranked[0][1]
    return "\n".join(f"{word:<{width}} | {bar(count, top)} {count}" for word, count in ranked)


def table(rows, headers):
    """A plain-text table: columns separated by two spaces, each as wide as its
    widest cell; numbers right-aligned, text left-aligned; a dashed line under
    the headers."""
    cells = [[str(h) for h in headers]] + [[_cell(v) for v in row] for row in rows]
    widths = [max(len(row[i]) for row in cells) for i in range(len(headers))]
    numeric = [
        all(isinstance(row[i], (int, float)) and not isinstance(row[i], bool) for row in rows) and bool(rows)
        for i in range(len(headers))
    ]
    lines = []
    for index, row in enumerate(cells):
        parts = []
        for i, cell in enumerate(row):
            parts.append(cell.rjust(widths[i]) if numeric[i] and index > 0 else cell.ljust(widths[i]))
        lines.append("  ".join(parts).rstrip())
        if index == 0:
            lines.append("  ".join("-" * w for w in widths))
    return "\n".join(lines)


def _cell(value):
    if isinstance(value, float):
        return f"{value:.{CONFIG['decimals']}f}"
    return str(value)


def summary(text):
    """A short report: counts, scores, the top words and their histogram."""
    rows = [
        ("words", len(words(text))),
        ("sentences", len(sentences(text))),
        ("distinct", len(set(words(text)))),
        ("diversity", lexical_diversity(text)),
        ("avg length", average_word_length(text)),
        ("reading ease", flesch_reading_ease(text)),
    ]
    parts = [table(rows, ("measure", "value"))]
    longest = longest_words(text)
    if longest:
        parts.append("longest: " + ", ".join(longest))
    hist = histogram(text)
    if hist:
        parts.append(hist)
    return "\n\n".join(parts)
