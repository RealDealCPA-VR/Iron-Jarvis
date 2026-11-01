"""v1.420.0: an account's USAGE LIMIT is not "busy".

Lane O (C6.5) words a failed helper through ``run_failure_words``; a Codex
account at its usage limit then read "Codex is busy. Run it again shortly" —
for a limit that can last hours. A 429 the adapter marked ``transient=False``,
or whose reset is minutes away, is the account's own limit and says so; a
plain 429 (an overload) keeps "is busy". The bench reads the new words as an
infrastructure reason that names the limit (its v1.396.0 pin asserts
"usage limit" in the reason)."""

from __future__ import annotations

from iron_jarvis.harness_bench import failures
from iron_jarvis.providers import failure_words as fw
from iron_jarvis.providers.adapters.base import ProviderError


def _limit(**kw) -> ProviderError:
    return ProviderError(
        "codex-cli: this ChatGPT account hit its Codex usage limit — try again after it "
        "resets. Codex said: You've hit your usage limit.",
        status_code=429,
        **kw,
    )


def test_a_non_transient_429_is_the_accounts_limit():
    title, fix = fw.classify(_limit(transient=False), provider="codex-cli", surface="run")
    assert title == "Codex hit its usage limit"
    assert fix.startswith("Run it again after it resets")
    assert "shortly" not in fix


def test_a_429_whose_reset_is_far_away_is_the_limit_and_says_when():
    title, fix = fw.classify(_limit(retry_after=3 * 3600), provider="codex-cli", surface="chat")
    assert title == "Codex hit its usage limit"
    assert "(in about 3 hours)" in fix and fix.startswith("Try again after it resets")
    _, fix_min = fw.classify(_limit(retry_after=20 * 60), provider="codex-cli", surface="chat")
    assert "(in about 20 minutes)" in fix_min


def test_a_plain_429_is_still_busy():
    title, fix = fw.classify(ProviderError("codex-cli: 429", status_code=429), provider="codex-cli", surface="run")
    assert title == "Codex is busy"
    assert fix.startswith("Run it again shortly")


def test_the_limit_is_read_through_the_cause_chain():
    inner = _limit(transient=False)
    outer = RuntimeError("the helper failed")
    outer.__cause__ = inner
    assert fw.failure_facts(outer).limit is True
    title, _ = fw.classify(outer, provider="codex-cli")
    assert title == "Codex hit its usage limit"


def test_the_bench_reads_the_limit_words_as_the_accounts_limit():
    rows = [{"member": "codex", "state": "failed", "result": "The run failed: Codex hit its usage limit. Run it again after it resets, or choose another model in Settings."}]
    reason = failures.helper_infra(rows)
    assert reason == "codex could not do its piece (the account's usage limit was reached)"


def test_reset_words_round_up_and_stay_silent_under_a_minute():
    assert fw._reset_words(0) == ""
    assert fw._reset_words(30) == ""
    assert fw._reset_words(61) == " (in about 2 minutes)"
    assert fw._reset_words(7200) == " (in about 2 hours)"
