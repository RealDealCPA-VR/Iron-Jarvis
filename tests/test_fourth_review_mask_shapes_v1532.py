"""v1.532.0 (fourth review, decision D2): the shared secret mask
(``detections.redact.mask``) knows the key shapes people paste.

Before: ``mask`` caught OpenAI/Anthropic/GitHub/Slack/AWS shapes and secret-
named ``key=value`` pairs, and only ``providers.failure_words.mask_failure_text``
knew a bare ``Bearer <value>``, Google ``AIza…``, Hugging Face ``hf_…``, Groq
``gsk_…`` and xAI ``xai-…``. Missed by both: Stripe ``sk_live_``/``rk_live_``
(and ``_test_``), GitLab ``glpat-``, npm ``npm_``, Google OAuth ``GOCSPX-``, a
JWT, a PEM private key block and an Entra (Azure AD) client secret. Every
surface that masks (findings, approvals, step detail, recaps, memory, the work
record) goes through ``mask``, so the shapes live there now.

Pinned here, per shape: a positive (a value ASSEMBLED AT RUN TIME from pieces,
never one literal shaped like a real key), a negative (ordinary words close to
it stay), idempotence (masking twice changes nothing), and linear cost (four
times the text costs about four times the time; ratios, never wall-clock
bars). Plus: ``mask_failure_text`` says what it said before for text with no
secret in it, and its own extra shapes are the shared mask's, verbatim.
The lock-step with ``dashboard/lib/stepDetail.ts`` is
``tests/test_redact_linear_v1349.py::test_the_dashboard_masks_with_the_daemon_s_rules``.
"""

from __future__ import annotations

import random
import re
import time

import pytest

from iron_jarvis.detections.redact import _RULES, mask
from iron_jarvis.providers import failure_words as FW

# --------------------------------------------------------------- the values
# Every value is built from pieces at run time (the repository is public; no
# literal here is shaped like a real credential).

FILL = "Fake" + "Example" + "0123456789" + "NotReal" + "Value12"  # 35 letters and digits


def _stripe(kind: str, mode: str) -> str:
    return kind + "_" + mode + "_" + FILL[:24]


def _gitlab() -> str:
    return "gl" + "pat-" + FILL[:20]


def _npm() -> str:
    return "np" + "m_" + (FILL + "XYZ12")[:36]


def _google_oauth() -> str:
    return "GOC" + "SPX-" + FILL[:28]


def _jwt() -> str:
    return ("ey" + "J" + "hbGciFake0Example") + "." + ("ey" + "J" + "zdWIExample1Fake") + "." + ("c2ln" + "NotReal2Sig_value")


def _pem(kind: str = "RSA ") -> str:
    body = "\n".join(["MII" + "Fake" + "Body" + str(i) * 40 for i in range(4)])
    return "-----BEGIN " + kind + "PRIVATE KEY-----\n" + body + "\n-----END " + kind + "PRIVATE KEY-----"


def _entra() -> str:
    # three characters, a digit, "Q~", then 34 characters: 40 in all.
    return "Abc" + "8" + "Q" + "~" + (FILL + "a.b")[:34]


# Folded in from failure_words (already masked by mask_failure_text).
def _bearer() -> str:
    return "Bear" + "er " + "abc" + "123" + "Example.value"


def _aiza() -> str:
    return "AI" + "za" + FILL[:33]


def _hf() -> str:
    return "h" + "f_" + FILL[:30]


def _gsk() -> str:
    return "gs" + "k_" + FILL[:30]


def _xai() -> str:
    return "xa" + "i-" + FILL[:30]


#: (name, value, the masked text's kept prefix: what kind of thing it was)
SHAPES = [
    ("stripe secret live", lambda: _stripe("sk", "live"), "sk_live_***"),
    ("stripe restricted live", lambda: _stripe("rk", "live"), "rk_live_***"),
    ("stripe secret test", lambda: _stripe("sk", "test"), "sk_test_***"),
    ("stripe restricted test", lambda: _stripe("rk", "test"), "rk_test_***"),
    ("gitlab", _gitlab, "glpat-***"),
    ("npm", _npm, "npm_***"),
    ("google oauth", _google_oauth, "GOCSPX-***"),
    ("jwt", _jwt, "eyJ***"),
    ("pem rsa", _pem, "-----BEGIN RSA PRIVATE KEY-----***-----END RSA PRIVATE KEY-----"),
    ("pem plain", lambda: _pem(""), "-----BEGIN PRIVATE KEY-----***-----END PRIVATE KEY-----"),
    ("pem openssh", lambda: _pem("OPENSSH "), "-----BEGIN OPENSSH PRIVATE KEY-----***-----END OPENSSH PRIVATE KEY-----"),
    ("entra", _entra, "***"),
    ("bare bearer", _bearer, "Bearer ***"),
    ("google api key", _aiza, "AIza***"),
    ("hugging face", _hf, "hf_***"),
    ("groq", _gsk, "gsk_***"),
    ("xai", _xai, "xai-***"),
]


@pytest.mark.parametrize("name,make,masked", SHAPES, ids=[s[0] for s in SHAPES])
def test_the_shared_mask_takes_out_the_key_people_paste(name, make, masked):
    value = make()
    text = f"here it is: {value} and that was all"
    out = mask(text)
    assert out == f"here it is: {masked} and that was all", (name, out)
    # Nothing of the secret body survives (its first distinctive piece).
    for piece in ("FakeExample", "MIIFakeBody", "hbGciFake0", "abc123Example"):
        assert piece not in out, (name, out)
    # Idempotent: the D1 pass and the dashboard's second mask re-mask stored text.
    assert mask(out) == out, (name, out)
    # The failure words say the same (they go through the shared mask first).
    assert FW.mask_failure_text(text) == out, name
    assert FW.mask_failure_text(text, strict=True) == out, name


def test_a_pem_block_cut_before_its_end_line_is_still_masked():
    cut = _pem()[:90]
    assert "-----END" not in cut
    out = mask(cut + " ")
    assert out.startswith("-----BEGIN RSA PRIVATE KEY-----***"), out
    assert "MIIFakeBody" not in out, out


@pytest.mark.parametrize(
    "text",
    [
        "run npm_install first, then npm_ci",
        "set the mode to sk_live or sk_test in the settings",
        "the sk_live_ prefix is for live mode",
        "a JWT is three parts split by dots, the first starts with eyJ",
        "decode the JWT header eyJhbGci to read it",
        "GOCSPX is the prefix Google uses",
        "glpat- tokens expire",
        "the files are a.b.c and x.y.z",
        "-----BEGIN CERTIFICATE-----\nMIIBpublicpart\n-----END CERTIFICATE-----",
        "-----BEGIN PUBLIC KEY-----\nMIIBpublicpart\n-----END PUBLIC KEY-----",
        "a path like ~/notes/8Q~draft stays",
        "Bearer authentication failed",
        "the version is 1.2.3-rc.4 and the build is 20261118",
        "hf_hub and gsk_ and xai- are prefixes",
    ],
)
def test_ordinary_words_close_to_a_key_are_left_alone(text):
    assert mask(text) == text
    assert FW.mask_failure_text(text) == text


def test_an_entra_shape_too_long_or_too_short_is_left_alone():
    entra = _entra()
    assert mask(entra) == "***"
    too_long = entra + "Q"
    assert mask(too_long) == too_long
    too_short = entra[:-4]
    assert mask(too_short) == too_short
    no_tilde = entra.replace("~", "x")
    assert mask(no_tilde) == no_tilde


# ---------------------------------------------- the failure words, unchanged

# The v1.389 failure masks as they were, verbatim: the reference.
OLD_FAILURE_MASKS = (
    (
        re.compile(r"(\bbearer:?\s+)(?![*]{3})(?=[A-Za-z0-9._~+/=-]*\d)[A-Za-z0-9._~+/=-]{12,}", re.IGNORECASE),
        r"\1***",
    ),
    (re.compile(r"\b(AIza)[0-9A-Za-z_-]{30,}"), r"\1***"),
    (re.compile(r"\b(xai-|gsk_|hf_)[A-Za-z0-9]{20,}"), r"\1***"),
)


def test_the_failure_words_extra_shapes_are_the_shared_mask_s_own():
    shared = {(p.pattern, p.flags) for p, _ in _RULES}
    for rx, _ in FW._FAILURE_MASKS:
        assert (rx.pattern, rx.flags) in shared, rx.pattern
    for rx, _ in OLD_FAILURE_MASKS:
        assert (rx.pattern, rx.flags) in shared, rx.pattern


FUZZ = [
    "Bearer ", "bearer: ", "AIza", "hf_", "gsk_", "xai-", "token=", "password: ",
    "Authorization: ", "sk-", "ghp_", "AKIA", "a", "Z", "1", "9", "x", "_", "-", ".",
    "/", "=", "+", " ", "\n", "***", "abcdefgh", "12345678", "ABCDEFGHIJKLMNOP",
    "https://u:p@h", "-u me:pw", '"', "'", ",", ";",
]


def test_the_failure_words_say_what_they_said_before_about_text_without_a_new_shape():
    # Old answer = the first six shared rules (unchanged, still in _RULES) then
    # the old failure masks. Strings here hold none of the new shapes'
    # prefixes, so the answer must be identical.
    old_rules = [
        (p, r) for p, r in _RULES
        if not any(m in p.pattern for m in ("PRIVATE KEY", "sk|rk", "glpat", "npm_", "eyJ", "Q~"))
        and (p.pattern, p.flags) not in {(o.pattern, o.flags) for o, _ in OLD_FAILURE_MASKS}
    ]
    assert len(old_rules) == 6, [p.pattern for p, _ in old_rules]

    def old(text: str) -> str:
        for p, r in old_rules:
            text = p.sub(r, text)
        for p, r in OLD_FAILURE_MASKS:
            text = p.sub(r, text)
        return text

    rng = random.Random(1532)
    changed = 0
    for _ in range(20_000):
        s = "".join(rng.choice(FUZZ) for _ in range(rng.randint(0, 16)))
        want = old(s)
        assert FW.mask_failure_text(s) == want, repr(s)
        changed += want != s
    assert changed > 1_000, f"anti-vacuity: only {changed} fuzzed strings were masked"


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
    small = unit * max(1, 2_000 // len(unit))
    big = unit * max(4, 8_000 // len(unit))
    fn(small)  # warm
    t_small = _best(fn, small, 9)
    t_big = _best(fn, big, 5)
    return t_big / max(t_small, 1e-7), t_small, t_big


def _rule(marker: str) -> tuple[re.Pattern[str], str]:
    hits = [(p, r) for p, r in _RULES if marker in p.pattern]
    assert len(hits) == 1, (marker, [p.pattern for p, _ in hits])
    return hits[0]


#: (a piece of the rule's pattern that names it, the hostile runs to time)
HOSTILE = [
    ("PRIVATE KEY", ["-----BEGIN PRIVATE KEY-----", "-----BEGIN RSA PRIVATE KEY-----a", "-----BEGIN AAAA"]),
    ("eyJ", ["eyJa-", "eyJabcdefgh.", "eyJabcdefgh-eyJ.", "eyJ_"]),
    ("Q~", ["a8Q~", "aaa8Q~", "aaa8Q~aaaaaaaaaa", "a.~", "aaa8Q~-"]),
    ("sk|rk", ["sk_live_a", "sk_live_", "rk_test_aaaaaaaaaaaaaaaa_"]),
    ("glpat", ["glpat-a", "GOCSPX-", "glpat-glpat-"]),
    ("npm_", ["npm_a", "npm_", "npm_aaaaaaaa_"]),
    ("bbearer:", ["bearer a", "Bearer 1.", "bearer aaaaaaaaaa"]),
]


@pytest.mark.parametrize("marker,units", HOSTILE, ids=[h[0] for h in HOSTILE])
def test_each_new_rule_costs_what_its_text_does(marker, units):
    rx, repl = _rule(marker)
    for unit in units:
        ratio, small, big = _ratio(lambda s: rx.sub(repl, s), unit)
        assert ratio < 9, (marker, unit, ratio, small, big)


def test_mask_as_a_whole_costs_what_its_text_does_on_the_new_shapes():
    for unit in ("eyJa-", "-----BEGIN PRIVATE KEY-----", "aaa8Q~", "npm_a", "a.", "a-"):
        ratio, small, big = _ratio(mask, unit)
        assert ratio < 9, (unit, ratio, small, big)
