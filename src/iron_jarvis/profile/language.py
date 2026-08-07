"""Response-language enforcement (v1.144.0) — pure, offline, no model calls.

The reported failure this exists for: a local model (Qwen/GLM-family especially)
is asked a question in English and answers with Chinese sentences mixed in, or
switches language halfway through. This module is the DETECTOR half; the chat
seams own the single rewrite request (see ``daemon/chat_turn._enforce_language``)
so this file stays synchronous, pure, and trivially testable.

WHAT IT HONESTLY CATCHES
------------------------
**Script-level** leakage: text in a writing system the chosen language does not
use (Han/Kana/Hangul/Cyrillic/Arabic/Hebrew/Devanagari/Thai/Greek vs Latin, and
the reverse). It does NOT detect same-script language drift — an English-setting
reply written in Spanish reads as clean here, because separating Spanish from
English by character ranges is impossible and a bag-of-words guesser would
misfire on every quotation, place name, and code identifier. The reported
problem is script-level; this solves the reported problem and does not pretend
to solve more.

THE FALSE-POSITIVE GUARDS (each one is a real way this feature could annoy)
--------------------------------------------------------------------------
1. **Code is stripped first.** Fenced blocks, indented blocks, and inline
   backticks are removed before counting — a Chinese comment inside a code
   sample the user asked about is not the model leaking.
2. **The user's own message wins.** If the user WROTE in that script (or pasted
   it), a reply using it is responsive, not leakage — asking "what does 这个
   mean?" must never trigger a rewrite.
3. **Two thresholds, both must trip**: an absolute count (a stray glyph, an
   emoji-adjacent symbol, or one CJK punctuation mark is not leakage) and a
   ratio (a single quoted term inside a long English answer is not leakage).
"""

from __future__ import annotations

import re

# --------------------------------------------------------------------------- #
# Scripts.
# --------------------------------------------------------------------------- #
#: script name -> compiled character-class matcher.
_SCRIPT_RANGES: dict[str, str] = {
    "latin": r"A-Za-zÀ-ɏ",
    "han": r"㐀-䶿一-鿿豈-﫿",
    "kana": r"぀-ヿ",
    "hangul": r"ᄀ-ᇿ㄰-㆏가-힯",
    "cyrillic": r"Ѐ-ӿ",
    "arabic": r"؀-ۿݐ-ݿ",
    "hebrew": r"֐-׿",
    "devanagari": r"ऀ-ॿ",
    "thai": r"฀-๿",
    "greek": r"Ͱ-Ͽ",
}

_SCRIPT_RE = {name: re.compile(f"[{rng}]") for name, rng in _SCRIPT_RANGES.items()}

#: ISO-639-1 -> (label, scripts this language legitimately writes in).
#: Japanese carries Han because kanji are Japanese; Korean carries Han because
#: hanja still appear. Both also carry Latin — every one of these languages
#: quotes Latin-script product names, URLs, and identifiers routinely.
LANGUAGES: dict[str, tuple[str, tuple[str, ...]]] = {
    "en": ("English", ("latin",)),
    "es": ("Spanish", ("latin",)),
    "fr": ("French", ("latin",)),
    "de": ("German", ("latin",)),
    "pt": ("Portuguese", ("latin",)),
    "it": ("Italian", ("latin",)),
    "nl": ("Dutch", ("latin",)),
    "pl": ("Polish", ("latin",)),
    "sv": ("Swedish", ("latin",)),
    "tr": ("Turkish", ("latin",)),
    "ru": ("Russian", ("cyrillic", "latin")),
    "uk": ("Ukrainian", ("cyrillic", "latin")),
    "el": ("Greek", ("greek", "latin")),
    "he": ("Hebrew", ("hebrew", "latin")),
    "ar": ("Arabic", ("arabic", "latin")),
    "hi": ("Hindi", ("devanagari", "latin")),
    "th": ("Thai", ("thai", "latin")),
    "zh": ("Chinese", ("han", "latin")),
    "ja": ("Japanese", ("kana", "han", "latin")),
    "ko": ("Korean", ("hangul", "han", "latin")),
}

#: Minimum foreign-script CHARACTERS before anything counts as leakage. One
#: quoted glyph or a stray full-width comma is not a language failure.
MIN_FOREIGN_CHARS = 8

#: Minimum share of the reply's letters that must be foreign-script. A single
#: term in an otherwise-English answer stays under this.
MIN_FOREIGN_RATIO = 0.08

#: Foreign-script characters in the USER's own text above which their message
#: counts as "written in / about that script" — the reply may answer in kind.
USER_SCRIPT_FLOOR = 4

#: The SECOND check, and the one that makes this symmetric rather than an
#: English-only feature. Every non-Latin language above also allows Latin (they
#: all quote product names, URLs, and identifiers), so the foreign-script check
#: alone can never notice a reply that is entirely in English under a Chinese
#: setting — every character is "allowed". This check asks the opposite
#: question: is the language's OWN script essentially missing? A reply long
#: enough to judge, in which the primary script holds less than
#: :data:`MIN_PRIMARY_RATIO` of the letters, is not in that language.
#:
#: It deliberately does NOT run when the primary script is Latin: separating
#: English from Spanish by character ranges is impossible, and pretending
#: otherwise here is how this feature would start rewriting correct answers.
MIN_PRIMARY_LETTERS = 40
MIN_PRIMARY_RATIO = 0.10

_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_TILDE_FENCE_RE = re.compile(r"~~~.*?~~~", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
_URL_RE = re.compile(r"https?://\S+|www\.\S+")


def label(code: str) -> str:
    """Human label for a language code; unknown codes echo back unchanged."""
    code = (code or "").strip().lower()
    hit = LANGUAGES.get(code)
    return hit[0] if hit else code


def options() -> list[dict[str, str]]:
    """``[{code, label}]`` for the /you page's language select."""
    return [{"code": c, "label": v[0]} for c, v in LANGUAGES.items()]


def strip_code(text: str) -> str:
    """Remove fenced blocks, inline code, and URLs — see guard (1)."""
    out = _FENCE_RE.sub(" ", text or "")
    out = _TILDE_FENCE_RE.sub(" ", out)
    out = _INLINE_CODE_RE.sub(" ", out)
    out = _URL_RE.sub(" ", out)
    return out


def script_counts(text: str) -> dict[str, int]:
    """Letters per script in *text* (scripts with no hits are omitted)."""
    counts: dict[str, int] = {}
    for name, rx in _SCRIPT_RE.items():
        n = len(rx.findall(text or ""))
        if n:
            counts[name] = n
    return counts


def language_instruction(code: str) -> str:
    """The system-prompt line for a chosen response language ("" when unset)."""
    code = (code or "").strip().lower()
    if not code:
        return ""
    name = label(code)
    return (
        f"Write EVERY response in {name}, from the first word to the last, "
        f"whatever language the question or the source material is in. Quoting a "
        f"phrase in another language is fine when the user asks about that "
        f"phrase; switching the answer into another language is not."
    )


def rewrite_instruction(code: str) -> str:
    """The single corrective turn's instruction (see ``_enforce_language``)."""
    name = label(code)
    return (
        f"Your previous reply contained text that was not in {name}. Rewrite that "
        f"same reply completely in {name}. Keep the meaning, structure, and any "
        f"code blocks exactly as they were — translate only the prose. Reply with "
        f"the rewritten answer alone."
    )


def detect_leak(reply: str, want: str, user_text: str = "") -> str | None:
    """A short reason when *reply* leaks out of the *want* language, else None.

    ``user_text`` is the user's own message for this turn — guard (2): when they
    wrote in the foreign script themselves, answering in it is responsive.
    """
    code = (want or "").strip().lower()
    if not code or not (reply or "").strip():
        return None
    scripts = LANGUAGES.get(code, ("", ("latin",)))[1]
    allowed = set(scripts)

    body = strip_code(reply)
    counts = script_counts(body)
    if not counts:
        return None
    letters_total = sum(counts.values())

    # Check 2 (see MIN_PRIMARY_LETTERS): the language's own script is missing.
    primary = scripts[0] if scripts else "latin"
    if (
        primary != "latin"
        and letters_total >= MIN_PRIMARY_LETTERS
        and (counts.get(primary, 0) / letters_total) < MIN_PRIMARY_RATIO
    ):
        return f"almost no {primary} text in a reply set to {label(code)}"

    foreign = {s: n for s, n in counts.items() if s not in allowed}
    if not foreign:
        return None
    foreign_total = sum(foreign.values())
    if foreign_total < MIN_FOREIGN_CHARS:
        return None
    if letters_total <= 0 or (foreign_total / letters_total) < MIN_FOREIGN_RATIO:
        return None

    # Guard (2): the user's own message used the same script.
    user_counts = script_counts(strip_code(user_text or ""))
    worst = max(foreign, key=lambda s: foreign[s])
    if user_counts.get(worst, 0) >= USER_SCRIPT_FLOOR:
        return None

    return (
        f"{foreign_total} {worst} characters in a reply set to "
        f"{label(code)}"
    )


#: Appended when the single rewrite succeeded — the user should know their
#: setting acted, not silently wonder why the wording changed.
NOTE_CORRECTED = "rewritten in {name} (the first reply used another language)"
#: Appended when the rewrite ALSO leaked. We keep the ORIGINAL reply in that
#: case (a second wrong answer is not better than the first) and say so.
NOTE_FAILED = (
    "this model kept answering outside {name}; the reply below is as it came "
    "back. Try a different model for {name} output"
)
