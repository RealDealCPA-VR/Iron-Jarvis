"""One usage view for /usage and /fleet/usage (v1.102.0).

`/usage` learned to fold in the user's OpenCode sessions in v1.94.0.
`/fleet/usage` predated that and called `observability.usage_summary` DIRECTLY,
so it never saw the merge. Measured on the machine this was found on: the Local
Fleet page reported 141,623 local tokens against a true 54,264,758 — a 383x
under-report, which reads as "my hardware is idle" while it is doing ~all the
work.

Two callers deriving "usage" from two different views is the actual bug, so both
now go through `eval/usage_view.merged_usage`.

The savings figure makes classification safety-critical: local tokens are priced
as "cost you avoided", so counting hosted spend as local reports money saved
that was in fact money spent.
"""

from __future__ import annotations

import pytest

from iron_jarvis.eval.usage_view import is_local_provider, merged_usage


# --- classification ----------------------------------------------------------


@pytest.mark.parametrize(
    "provider",
    ["custom", "ollama", "fleet-custom", "fleet-spark-049d",
     "opencode/spark", "opencode/fleet", "opencode/lmstudio"],
)
def test_own_hardware_counts_as_local(provider):
    assert is_local_provider(provider) is True


@pytest.mark.parametrize(
    "provider",
    ["anthropic", "openai", "google", "openrouter",
     "opencode/anthropic", "opencode/openai", "opencode/openrouter"],
)
def test_hosted_never_counts_as_local(provider):
    """THE SAFETY-CRITICAL CASE. Iron Jarvis's own OpenCode connector is
    local-only, but this rollup reads OpenCode's OWN store — sessions the user
    ran directly, where that restriction does not apply and hosted models are
    reachable. Counting those as local would price real spend as avoided spend."""
    assert is_local_provider(provider) is False


def test_an_unknown_opencode_runner_is_treated_as_local():
    """Self-hosted runners have arbitrary names (a machine, a rig). Unknown ==
    local is the right default; the hosted list is the exception."""
    assert is_local_provider("opencode/tower-3090") is True
    assert is_local_provider("opencode/my-rig:8000") is True


def test_classification_is_case_insensitive():
    assert is_local_provider("OpenCode/Anthropic") is False
    assert is_local_provider("Custom") is True


# --- the merge ---------------------------------------------------------------


class _Obs:
    def __init__(self, rows):
        self._rows = rows

    def usage_summary(self, days):
        return {
            "since_days": days,
            "totals": {"input_tokens": 100, "output_tokens": 10, "cost_usd": 0.0, "runs": 2},
            "by_day": [{"day": "2026-07-20", "input_tokens": 100,
                        "output_tokens": 10, "cost_usd": 0.0}],
            "by_model": list(self._rows),
        }


class _Platform:
    def __init__(self, rows, config=None):
        self.observability = _Obs(rows)
        self.config = config


def test_merge_adds_opencode_totals(monkeypatch):
    rows = [{"provider": "custom", "model": "brain", "input_tokens": 100,
             "output_tokens": 10, "cost_usd": 0.0, "runs": 2}]
    monkeypatch.setattr(
        "iron_jarvis.eval.opencode_usage.opencode_usage",
        lambda *a, **k: {
            "available": True, "note": "n",
            "totals": {"input_tokens": 900, "output_tokens": 90,
                       "cache_tokens": 0, "cost_usd": 0.0, "runs": 3},
            "by_model": [{"provider": "opencode/spark", "model": "fleet",
                          "input_tokens": 900, "output_tokens": 90,
                          "cost_usd": 0.0, "runs": 3}],
            "by_day": [{"day": "2026-07-20", "input_tokens": 900,
                        "output_tokens": 90, "cost_usd": 0.0}],
        },
    )
    out = merged_usage(_Platform(rows), 30)
    assert out["totals"]["input_tokens"] == 1000
    assert out["opencode"]["available"] is True
    assert {r["provider"] for r in out["by_model"]} == {"custom", "opencode/spark"}
    # Same day merges rather than duplicating.
    assert len(out["by_day"]) == 1
    assert out["by_day"][0]["input_tokens"] == 1000


def test_merge_never_raises_when_opencode_is_unreadable(monkeypatch):
    """Usage is a reporting surface — it must degrade, not 500."""
    def _boom(*a, **k):
        raise RuntimeError("db locked")

    monkeypatch.setattr("iron_jarvis.eval.opencode_usage.opencode_usage", _boom)
    out = merged_usage(_Platform([]), 30)
    assert out["opencode"]["available"] is False
    assert out["totals"]["input_tokens"] == 100  # untouched


def test_absent_opencode_leaves_the_rollup_alone(monkeypatch):
    monkeypatch.setattr(
        "iron_jarvis.eval.opencode_usage.opencode_usage",
        lambda *a, **k: {"available": False, "note": "not found"},
    )
    out = merged_usage(_Platform([]), 30)
    assert out["totals"]["input_tokens"] == 100


# --- the two routes must not drift again -------------------------------------


def test_both_routes_use_the_shared_view():
    """The defect was /usage and /fleet/usage deriving usage two different ways.
    Pin that they share one, so a future edit can't silently re-fork them."""
    from pathlib import Path

    routes = Path(__file__).resolve().parents[1] / "src" / "iron_jarvis" / "daemon" / "routes"
    system = (routes / "system.py").read_text(encoding="utf-8")
    fleet = (routes / "fleet.py").read_text(encoding="utf-8")

    assert "merged_usage" in system
    assert "merged_usage" in fleet
    # Neither may reach past it to the raw rollup.
    assert "observability.usage_summary" not in system
    assert "observability.usage_summary" not in fleet
    # And fleet must classify through the shared helper, not a local copy.
    assert "is_local_provider" in fleet
    assert "_LOCAL_PROVIDERS" not in fleet
