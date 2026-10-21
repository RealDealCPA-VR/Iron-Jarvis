"""Statistics."""

from collections import Counter

from .config import resolve
from .tokens import content_words, sentences, words

_VOWELS = "aeiouy"


def frequencies(text, include_stopwords=True, *, config=None):
    config = resolve(config)
    return Counter(words(text, config=config) if include_stopwords else content_words(text, config=config))


def top_words(text, n=5, include_stopwords=False, *, config=None):
    counts = frequencies(text, include_stopwords=include_stopwords, config=resolve(config))
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return ranked[:n]


def syllables(word):
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


def flesch_reading_ease(text, *, config=None):
    config = resolve(config)
    ws = words(text, config=config)
    ss = sentences(text)
    if not ws or not ss:
        return 0.0
    total_syllables = sum(syllables(w) for w in ws)
    score = 206.835 - 1.015 * (len(ws) / len(ss)) - 84.6 * (total_syllables / len(ws))
    return round(score, config.decimals)


def lexical_diversity(text, *, config=None):
    config = resolve(config)
    ws = words(text, config=config)
    if not ws:
        return 0.0
    return round(len(set(ws)) / len(ws), config.decimals)


def average_word_length(text, *, config=None):
    config = resolve(config)
    ws = words(text, config=config)
    if not ws:
        return 0.0
    letters = sum(sum(ch.isalpha() for ch in w) for w in ws)
    return round(letters / len(ws), config.decimals)


def longest_words(text, n=3, *, config=None):
    distinct = set(words(text, config=resolve(config)))
    return sorted(distinct, key=lambda w: (-len(w), w))[:n]
