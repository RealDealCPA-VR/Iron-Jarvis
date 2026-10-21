"""Rendering."""

import math

from .config import resolve
from .stats import average_word_length, flesch_reading_ease, lexical_diversity, longest_words, top_words
from .tokens import sentences, words


def bar(value, maximum, *, config=None):
    config = resolve(config)
    if maximum <= 0 or value <= 0:
        return ""
    length = math.floor(value / maximum * config.bar_width + 0.5)
    return config.bar_char * length


def histogram(text, n=5, *, config=None):
    config = resolve(config)
    ranked = top_words(text, n, config=config)
    if not ranked:
        return ""
    width = max(len(word) for word, _ in ranked)
    top = ranked[0][1]
    return "\n".join(
        f"{word:<{width}} | {bar(count, top, config=config)} {count}" for word, count in ranked
    )


def table(rows, headers, *, config=None):
    config = resolve(config)
    cells = [[str(h) for h in headers]] + [[_cell(v, config) for v in row] for row in rows]
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


def _cell(value, config):
    if isinstance(value, float):
        return f"{value:.{config.decimals}f}"
    return str(value)


def summary(text, *, config=None):
    config = resolve(config)
    ws = words(text, config=config)
    rows = [
        ("words", len(ws)),
        ("sentences", len(sentences(text))),
        ("distinct", len(set(ws))),
        ("diversity", lexical_diversity(text, config=config)),
        ("avg length", average_word_length(text, config=config)),
        ("reading ease", flesch_reading_ease(text, config=config)),
    ]
    parts = [table(rows, ("measure", "value"), config=config)]
    longest = longest_words(text, config=config)
    if longest:
        parts.append("longest: " + ", ".join(longest))
    hist = histogram(text, config=config)
    if hist:
        parts.append(hist)
    return "\n\n".join(parts)
