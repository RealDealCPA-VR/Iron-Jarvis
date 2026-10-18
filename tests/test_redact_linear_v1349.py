"""v1.349.0 integrate — ``detections.redact.mask`` is LINEAR, and its
dashboard port says the same thing.

Lane B1d's review measured two of the masking rules as QUADRATIC on long runs
of word characters split by ``.``/``-`` (``a.a.a…``, base64url): the key=value
rule's ``\\b[\\w.-]*`` prefix and the URL rule's ``\\b[a-z][a-z0-9+.-]*`` start
retried at EVERY word boundary of the run and rescanned it from there. ``mask``
runs on the event loop, and v1.349.0 added a second ``mask`` over every chat
tool output (``chat_turn.step_detail``) plus one in the agent runtime: a 50 KB
``a.a.…`` output took 61 s.

Both rules were rewritten so a match can only start where it could produce
output; the replacement keeps group 1 as TEXT, so where a match starts never
shows. Pinned here:

  * equivalence — the old and new patterns give the SAME output on fuzzed
    strings built to exercise each rule (anti-vacuity: many really masked);
  * cost — four times the text costs about four times the time, never ~16x
    (RATIOS, never wall-clock bars: the v1.290.0 detections pattern);
  * lock-step — ``dashboard/lib/stepDetail.ts`` ``DETAIL_MASK_RULES`` (the
    dashboard's second mask over a saved step detail) carries the same
    patterns with the same case rule, in the same order.
"""

from __future__ import annotations

import random
import re
import time
from pathlib import Path

from iron_jarvis.detections.redact import _RULES, mask

ROOT = Path(__file__).resolve().parents[1]
STEP_DETAIL_TS = ROOT / "dashboard" / "lib" / "stepDetail.ts"

# The v1.290.0 shapes, verbatim (the reference the rewrite must agree with).
OLD_KEY_VALUE = re.compile(
    r"""(\b[\w.-]*(?:api[_-]?key|access[_-]?key|client[_-]?secret|token|password|passwd|secret|pwd)["']?\s*[=:]\s*["']?)(?![*]{3})[^\s"'&,;}]+""",
    re.IGNORECASE,
)
OLD_URL = re.compile(r"""(\b[a-z][a-z0-9+.-]*://[^/\s:@]+:)[^@\s/]+(@)""", re.IGNORECASE)


def _rule(marker: str) -> tuple[re.Pattern[str], str]:
    hits = [(p, r) for p, r in _RULES if marker in p.pattern]
    assert len(hits) == 1, (marker, [p.pattern for p, _ in hits])
    return hits[0]


KEY_VALUE, KEY_VALUE_REPL = _rule("passwd")
URL, URL_REPL = _rule("://")

KV_ALPHABET = [
    "token", "password", "pwd", "secret", "client_secret", "api-key", "API_KEY",
    "access_key", "passwd", "=", ":", " ", '"', "'", "a", "b", "1", ".", "-", "_",
    "/", "://", "@", "x+y", "\u00e9", "&", ",", ";", "}", "***", "\n", "Z", "9",
]
URL_ALPHABET = [
    "a", "Z", "1", "+", ".", "-", "_", "\u00e9", " ", "/", ":", "@", "://",
    "http", "u", "p", "\n", "x", "9", "***",
]


def _pieces(rng: random.Random, alphabet: list[str], most: int) -> str:
    return "".join(rng.choice(alphabet) for _ in range(rng.randint(0, most)))


def _kv_case(rng: random.Random) -> str:
    return _pieces(rng, KV_ALPHABET, 14)


def _url_case(rng: random.Random) -> str:
    s = (_pieces(rng, URL_ALPHABET, 3) + _pieces(rng, URL_ALPHABET, 3) + "://"
         + _pieces(rng, URL_ALPHABET, 2) + ":" + _pieces(rng, URL_ALPHABET, 2) + "@"
         + _pieces(rng, URL_ALPHABET, 3))
    if rng.random() < 0.3:
        s += (_pieces(rng, URL_ALPHABET, 2) + "://" + _pieces(rng, URL_ALPHABET, 2) + ":"
              + _pieces(rng, URL_ALPHABET, 1) + "@")
    return s


# --------------------------------------------------------------- equivalence


def test_the_key_value_rule_says_exactly_what_it_said_before():
    rng = random.Random(1349)
    masked = 0
    for _ in range(60_000):
        s = _kv_case(rng)
        old = OLD_KEY_VALUE.sub(KEY_VALUE_REPL, s)
        assert KEY_VALUE.sub(KEY_VALUE_REPL, s) == old, repr(s)
        masked += old != s
    assert masked > 3_000, f"anti-vacuity: only {masked} fuzzed strings were masked"


def test_the_url_rule_says_exactly_what_it_said_before():
    rng = random.Random(7)
    masked = 0
    for _ in range(60_000):
        s = _url_case(rng)
        old = OLD_URL.sub(URL_REPL, s)
        assert URL.sub(URL_REPL, s) == old, repr(s)
        masked += old != s
    assert masked > 2_000, f"anti-vacuity: only {masked} fuzzed strings were masked"


def test_the_edges_where_a_naive_rewrite_would_differ():
    # Each line: the old rule's own answer is the expectation.
    for s in (
        "token_secret=abc",          # two keywords end together
        "x/token=abc",               # keyword after a non-word separator
        "\u00e9token=x",             # keyword after a non-ASCII letter
        "a.b.access_key: v",         # dotted prefix run
        "-abc://u:pw@h",             # a run that starts with '-' (no \b there)
        "_abc://u:pw@h",             # '_' is a word char: the old rule never matched
        "1abc://u:pw@h",             # a run that starts with a digit
        "a.b+c://me:hunter2@example.com/x",
        "\u00e9http://me:pw@h",      # scheme after a non-ASCII letter
        "xhttp://u:p@h ftp://v:q@k",
    ):
        assert KEY_VALUE.sub(KEY_VALUE_REPL, s) == OLD_KEY_VALUE.sub(KEY_VALUE_REPL, s), s
        assert URL.sub(URL_REPL, s) == OLD_URL.sub(URL_REPL, s), s
    assert mask("https://me:hunter2@example.com/x") == "https://me:***@example.com/x"
    assert mask('{"refresh_token": "r-123"}') == '{"refresh_token": "***"}'


# ---------------------------------------------------------------------- cost


def _best(fn, text: str, repeat: int) -> float:
    best = float("inf")
    for _ in range(repeat):
        t0 = time.perf_counter()
        fn(text)
        best = min(best, time.perf_counter() - t0)
    return best


def _ratio(fn, unit: str) -> tuple[float, float, float]:
    """(ratio, small, big) for 2,000 vs 8,000 characters of *unit*: linear is
    ~4, quadratic ~16."""
    small = unit * (2_000 // len(unit))
    big = unit * (8_000 // len(unit))
    fn(small)  # warm
    t_small = _best(fn, small, 9)
    t_big = _best(fn, big, 5)
    return t_big / max(t_small, 1e-7), t_small, t_big


def test_the_key_value_rule_costs_what_its_text_does():
    for unit in ("a.", "a-", "Zq-"):
        ratio, small, big = _ratio(lambda s: KEY_VALUE.sub(KEY_VALUE_REPL, s), unit)
        assert ratio < 9, (unit, ratio, small, big)


def test_the_url_rule_costs_what_its_text_does():
    for unit in ("a.", "a-", "a+"):
        ratio, small, big = _ratio(lambda s: URL.sub(URL_REPL, s), unit)
        assert ratio < 9, (unit, ratio, small, big)


def test_mask_as_a_whole_costs_what_its_text_does():
    for unit in ("a.", "a-"):
        ratio, small, big = _ratio(mask, unit)
        assert ratio < 9, (unit, ratio, small, big)


# ----------------------------------------------------------------- lock-step


def _ts_rules() -> list[tuple[str, bool]]:
    text = STEP_DETAIL_TS.read_text(encoding="utf-8").replace("\r\n", "\n")
    start = text.index("export const DETAIL_MASK_RULES")
    block = text[start:text.index("\n];", start)]
    code = "\n".join(line for line in block.split("\n") if not line.strip().startswith("//"))
    out = []
    for m in re.finditer(r"/(?P<src>[^\n]+)/(?P<flags>[a-z]*),", code):
        src = m.group("src").replace("\\/", "/").replace("\\-", "-")
        out.append((src, "i" in m.group("flags")))
    return out


def test_the_dashboard_masks_with_the_daemon_s_rules():
    ours = [(p.pattern.replace("\\-", "-"), bool(p.flags & re.IGNORECASE)) for p, _ in _RULES]
    theirs = _ts_rules()
    assert len(theirs) == len(ours), theirs
    assert theirs == ours
