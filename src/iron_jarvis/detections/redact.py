"""Secrets never leave a finding (v1.290.0 review).

A rule that catches a credential being SENT fires on a command that carries
the credential — ``curl -H "Authorization: Bearer ghp_…" -d password=…`` —
so the evidence of a finding is exactly where a secret sits. Everything a
finding hands out (``Finding.to_dict`` → ``GET /detections/findings``, the
history route's findings) goes through :func:`mask` first; the bell's
``detection.finding`` event carries no command / url / text at all
(``bell.bell_payload``).

:func:`mask` replaces the VALUE and keeps the shape, so a reader still sees
what kind of thing was sent: ``Authorization: Bearer ***``, ``password=***``,
``-u me:***``, ``https://me:***@host``, ``ghp_***``, ``AKIA***``.
"""

from __future__ import annotations

import re

MASK = "***"

_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    # Authorization: Bearer <v> / Basic <v> / Token <v> / <v>
    (
        re.compile(
            r"""(\bauthorization["']?\s*[:=]\s*["']?(?:(?:bearer|basic|token)\s+)?)[^\s"',;]+""",
            re.IGNORECASE,
        ),
        r"\1" + MASK,
    ),
    # key = value / "key": "value" for secret-named keys (refresh_token=…, pwd:…)
    (
        re.compile(
            r"""(\b[\w.-]*(?:api[_-]?key|access[_-]?key|client[_-]?secret|token|password|passwd|secret|pwd)["']?\s*[=:]\s*["']?)(?![*]{3})[^\s"'&,;}]+""",
            re.IGNORECASE,
        ),
        r"\1" + MASK,
    ),
    # curl -u user:pass / --user user:pass
    (
        re.compile(r"""((?:^|\s)(?:-u|--user)[\s=]+["']?[^\s:"']+:)[^\s"']+"""),
        r"\1" + MASK,
    ),
    # scheme://user:pass@host
    (re.compile(r"""(\b[a-z][a-z0-9+.-]*://[^/\s:@]+:)[^@\s/]+(@)""", re.IGNORECASE), r"\1" + MASK + r"\2"),
    # known token shapes
    (
        re.compile(r"""\b(ghp_|gho_|ghu_|ghs_|ghr_|github_pat_|sk-ant-|sk-|xox[abprs]-)[A-Za-z0-9_\-]{6,}"""),
        r"\1" + MASK,
    ),
    (re.compile(r"""\b(AKIA|ASIA)[0-9A-Z]{16}\b"""), r"\1" + MASK),
)


def mask(value):
    """*value* with every secret-looking VALUE replaced by ``***``. Non-strings
    pass through untouched."""
    if not isinstance(value, str) or not value:
        return value
    for pattern, repl in _RULES:
        value = pattern.sub(repl, value)
    return value


#: The evidence fields that can carry a secret.
MASKED_FIELDS = ("command", "url", "text", "path")


def mask_event(event: dict) -> dict:
    """A copy of *event* with its free-text fields masked."""
    out = dict(event)
    for key in MASKED_FIELDS:
        if key in out:
            out[key] = mask(out[key])
    return out
