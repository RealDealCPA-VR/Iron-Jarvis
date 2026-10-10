"""v1.330.0 (calm chat W12 N1): the last two Anthropic defaults.

Read on 2026-10-10 from Anthropic's effort page ("Claude Opus 5.5 and Claude
Haiku 5.5 default to medium"; `high` "The default on every model that supports
effort except Claude Opus 5.5 and Claude Haiku 5.5") and the thinking
troubleshooting table (Haiku 5.5: thinking "On"; Mythos Preview: "Always on",
supports effort). So, with nothing picked, the composer's chip can name them:
Haiku 5.5 runs at medium, Mythos Preview at high.
"""

from __future__ import annotations

import pytest

from iron_jarvis.providers import reasoning as R


@pytest.mark.parametrize(
    "model, expected",
    [
        ("claude-haiku-5-5", "medium"),
        ("claude-haiku-5-5[1m]", "medium"),
        ("claude-mythos-preview", "high"),
        ("CLAUDE-MYTHOS-PREVIEW", "high"),
    ],
)
def test_the_documented_defaults(model, expected):
    assert R.reasoning_default("anthropic", model) == expected


def test_both_rows_offer_levels_and_stay_in_the_vocabulary():
    for model in ("claude-haiku-5-5", "claude-mythos-preview"):
        assert R.reasoning_levels("anthropic", model)
        assert R._ANTHROPIC_DEFAULTS[model] in R.DEFAULTS


def test_served_through_the_cli_is_still_unknown():
    for model in ("claude-haiku-5-5", "claude-mythos-preview"):
        assert R.reasoning_default("anthropic", model, served_by="claude-cli") == ""
        assert R.reasoning_default("claude-cli", model) == ""


def test_older_haiku_rows_are_untouched():
    assert R.reasoning_default("anthropic", "claude-haiku-4-5") == "off"
    assert R.reasoning_default("anthropic", "claude-haiku-6") == ""
