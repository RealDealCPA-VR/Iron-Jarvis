"""textkit: small text statistics and plain-text reports.

Settings are a :class:`Config` passed as ``config=`` (None means the defaults).
"""

from .config import Config
from .render import bar, histogram, summary, table
from .stats import (
    average_word_length,
    flesch_reading_ease,
    frequencies,
    lexical_diversity,
    longest_words,
    syllables,
    top_words,
)
from .tokens import content_words, ngrams, sentences, stopwords, words

__all__ = [
    "Config", "words", "sentences", "ngrams", "stopwords", "content_words",
    "frequencies", "top_words", "syllables", "flesch_reading_ease", "lexical_diversity",
    "average_word_length", "longest_words", "bar", "histogram", "table", "summary",
]
