"""v1.232.0 (audit Wave 6, U6) — the Usage page lists models that did work.

The live Usage page showed ``mock · mock-1 0 tok`` and ``opencode/bogusprov ·
bogusmodel 0 tok`` as models and counted them in "Across N models". The one
usage view (``eval/usage_view.merged_usage``) now drops the mock provider and
zero-token rows from ``by_model`` and reports ``model_count``; the unfiltered
list is still in the JSON as ``by_model_raw`` and ``totals`` are untouched.
"""

from __future__ import annotations

from iron_jarvis.eval.usage_view import is_noise_row, merged_usage


class _Obs:
    def __init__(self, rows):
        self._rows = rows

    def usage_summary(self, days):
        return {
            "since_days": days,
            "totals": {"input_tokens": 100, "output_tokens": 10, "cost_usd": 0.0, "runs": 5},
            "by_day": [],
            "by_model": list(self._rows),
        }


class _Platform:
    def __init__(self, rows):
        self.observability = _Obs(rows)
        self.config = None  # no CLI stores: both merges report unavailable


REAL = {"provider": "custom", "model": "brain", "input_tokens": 100,
        "output_tokens": 10, "cost_usd": 0.0, "runs": 2}
MOCK = {"provider": "mock", "model": "mock-1", "input_tokens": 0,
        "output_tokens": 0, "cost_usd": 0.0, "runs": 2}
BOGUS = {"provider": "opencode/bogusprov", "model": "bogusmodel",
         "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "runs": 1}
# A mock row that somehow carries tokens is STILL noise: no real model ran.
MOCK_WITH_TOKENS = {"provider": "Mock", "model": "mock-1", "input_tokens": 40,
                    "output_tokens": 4, "cost_usd": 0.0, "runs": 1}


def test_is_noise_row_names_mock_and_zero_token_rows():
    assert is_noise_row(MOCK) is True
    assert is_noise_row(BOGUS) is True
    assert is_noise_row(MOCK_WITH_TOKENS) is True
    assert is_noise_row(REAL) is False
    # Hostile shapes never raise: a row with junk token fields is noise.
    assert is_noise_row({"provider": "custom", "input_tokens": "x"}) is True


def test_by_model_drops_noise_and_keeps_the_raw_list():
    out = merged_usage(_Platform([REAL, MOCK, BOGUS, MOCK_WITH_TOKENS]), days=30)
    assert out["by_model"] == [REAL]
    assert out["model_count"] == 1
    # Nothing is hidden: the unfiltered rows ride under their own key.
    assert out["by_model_raw"] == [REAL, MOCK, BOGUS, MOCK_WITH_TOKENS]
    # Totals are the ledger's totals, not a function of the model list.
    assert out["totals"]["runs"] == 5
    assert out["totals"]["input_tokens"] == 100


def test_an_empty_window_is_still_an_empty_window():
    out = merged_usage(_Platform([]), days=7)
    assert out["by_model"] == []
    assert out["by_model_raw"] == []
    assert out["model_count"] == 0
