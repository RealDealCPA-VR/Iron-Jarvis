"""Tokenizing."""

import re
from functools import lru_cache

from .config import resolve

_BUILTIN_STOPWORDS = frozenset((
    "a an and are as at be but by for from had has have he her his i in is it its "
    "not of on or she that the their they this to was we were will with you"
).split())

_WORD_RE = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")
_SENTENCE_RE = re.compile(r"[^.!?]+[.!?]*")


def words(text, *, config=None):
    config = resolve(config)
    found = _WORD_RE.findall(text)
    if config.lowercase:
        found = [w.lower() for w in found]
    return [w for w in found if len(w) >= config.min_word_length]


def sentences(text):
    out = []
    for piece in _SENTENCE_RE.findall(text):
        piece = " ".join(piece.split())
        if piece and any(ch.isalpha() for ch in piece):
            out.append(piece)
    return out


def ngrams(tokens, n):
    if n < 1:
        raise ValueError("n must be at least 1")
    return [tuple(tokens[i:i + n]) for i in range(len(tokens) - n + 1)]


@lru_cache(maxsize=32)
def _read_stopwords(path):
    with open(path, encoding="utf-8") as fh:
        lines = [line.strip() for line in fh]
    return frozenset(line.lower() for line in lines if line and not line.startswith("#"))


def stopwords(*, config=None):
    config = resolve(config)
    if config.stopwords_file is None:
        return _BUILTIN_STOPWORDS
    return _read_stopwords(config.stopwords_file)


def content_words(text, *, config=None):
    config = resolve(config)
    stop = stopwords(config=config)
    return [w for w in words(text, config=config) if w.lower() not in stop]
