"""v1.422.0 review (Wave 2 "Never lose work", B1): what the patience must
never wait out, read against the failures the adapters really raise.

THE DEFECT (found in review). ``harness/patience.why_waited`` read "the
account's own usage limit" ONLY from the typed facts (``failure_facts.limit``:
a 429 marked not transient, or one whose ``Retry-After`` is five minutes or
more). Codex's ``usageLimitExceeded`` with no rate-limit snapshot (its reset
not known) is a 429 with NO ``retry_after`` — read as "busy", so a coding
reply on a ChatGPT account at its Codex usage limit waited 1 min 50 s and
asked Codex four more times before it failed, its live line saying "Codex is
busy (too many requests). Trying again…". The same for an OpenAI key out of
quota (``insufficient_quota``, a 429). Now a failure whose own words say an
ACCOUNT's limit is never waited out; a passing rate limit still is.

Driven through the REAL Codex error mapping (``codex_native.protocol.
turn_failure``) and the real stream lane; only the model is a double.
"""

from __future__ import annotations

import pytest

from iron_jarvis.harness import patience as P
from iron_jarvis.providers.adapters.base import ProviderError
from iron_jarvis.providers.adapters.codex_native import protocol as CX
from tests.test_build_patience_v14220 import _body, _stream, env, waits  # noqa: F401 — fixtures


def _codex(variant: str, message: str) -> Exception:
    return CX.turn_failure({"message": message, "codexErrorInfo": variant}, emitted=False).error


@pytest.mark.parametrize("exc", [
    # Codex's own usage limit, its reset NOT known (no snapshot).
    _codex("usageLimitExceeded", "You've hit your usage limit. Upgrade to Pro or try again later."),
    # An OpenAI key out of quota (a 429 that is billing, not traffic).
    ProviderError(
        "openai API error 429: You exceeded your current quota, please check your plan and billing"
        " details. (insufficient_quota)", status_code=429,
    ),
    # Claude Code's own limit words.
    ProviderError("claude-cli: You've hit your session limit · resets 3:45pm", status_code=429),
    ProviderError("claude-cli: You're out of usage credits", status_code=429),
])
def test_an_accounts_own_limit_is_never_waited_out(exc):
    assert P.why_waited(exc) == ""


@pytest.mark.parametrize("exc", [
    # A passing rate limit (per minute) is still waited out.
    _codex("rateLimitExceeded", "Rate limit reached for requests per min. Please try again in 2s."),
    ProviderError("anthropic API error 429: rate_limit_error: Number of request tokens has exceeded"
                  " your per-minute rate limit", status_code=429),
    ProviderError("anthropic API error 529: overloaded_error", status_code=529),
])
def test_a_passing_limit_is_still_waited_out(exc):
    assert P.why_waited(exc) in ("busy", "overloaded")


async def test_a_coding_reply_at_its_codex_usage_limit_fails_at_once(env, waits):  # noqa: F811
    platform, ws, wire = env
    err = _codex("usageLimitExceeded", "You've hit your usage limit. Upgrade to Pro or try again later.")
    model = wire([err, err, err, err, err, "never asked"], who=("codex-cli", "gpt-5.5"))
    frames = await _stream(platform, _body(ws), harness=True)
    assert [e for e, _ in frames if e == "error"] == ["error"]
    assert not [d for e, d in frames if e == P.FRAME], "the live line said it would try again"
    assert waits == [] and len(model.calls) == 1
