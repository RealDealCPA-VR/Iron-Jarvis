"""17-a (build-out plan R1.8, idea 17): THE NIGHT BUDGET.

``improve/budget.may_spend`` is the ONE answer to "may a background model
request be made now?" for the night review: "" = yes, else the reason in
plain words. Each refusal is pinned here, and each is a refusal of the
function itself (no layer below is stubbed): the demo model, a model that
bills per use (or may), Local only work with a model off this network, an
account at its limit or at 70% or more of a window, Emergency stop, and the
night's cap, which is kept in ``<home>/night-review.json`` so a restart does
not reset it.

Mutation (by hand, see the commit): deleting the per-use branch in
``may_spend`` turns ``test_a_model_that_bills_per_use_is_refused`` red.
"""

from __future__ import annotations

import json

import pytest

from iron_jarvis.improve import budget as B
from iron_jarvis.platform import build_platform

NIGHT = "2026-11-20"


@pytest.fixture
def platform(tmp_path):
    p = build_platform(str(tmp_path))
    # A model server on this PC (no request is ever sent to it here).
    p.providers.configure_local(ollama_base_url="http://127.0.0.1:11434")
    p.config.ollama_base_url = "http://127.0.0.1:11434"
    B.reset()
    yield p
    B.reset()


class FakeProxy:
    """Iron-Proxy's cached snapshot: one Claude Code account, ``used``% of
    its 5-hour window (or parked at its limit)."""

    enabled = True

    def __init__(self, used: float = 10.0, parked: bool = False) -> None:
        self.used = used
        self.parked = parked

    def cached_accounts(self):
        state = {"status": "ready", "windows": [{"usedPercent": self.used, "windowMins": 300, "kind": "5h"}]}
        if self.parked:
            state = {"status": "parked", "parkedReason": {"kind": "quota-exhausted"}}
        return (
            [{"id": "acc_work", "title": "Work Max", "provider": "anthropic", "lane": "cli", "order": 0}],
            {"acc_work": state},
        )


def test_a_plan_model_and_a_model_on_this_network_may_be_spent(platform):
    assert B.may_spend(platform, provider="claude-cli", model="claude-sonnet-4-6", night=NIGHT) == ""
    assert B.may_spend(platform, provider="ollama", model="qwen3:8b", night=NIGHT) == ""


def test_the_demo_model_is_refused(platform):
    why = B.may_spend(platform, provider="mock", model="", night=NIGHT)
    assert why == B.WHY_DEMO
    assert B.needs_you(why)


def test_auto_is_refused_by_name(platform):
    why = B.may_spend(platform, provider="auto", model="", night=NIGHT)
    assert why == B.WHY_AUTO and B.needs_you(why)


def test_no_recorded_model_is_refused(platform):
    assert B.may_spend(platform, provider="", model="", night=NIGHT) == B.WHY_NO_MODEL


def test_a_model_that_bills_per_use_is_refused(platform):
    why = B.may_spend(platform, provider="openai", model="gpt-5", night=NIGHT)
    assert why.endswith("bills per use, and the night review never spends money"), why
    assert why.startswith("OpenAI"), why
    assert B.needs_you(why)


def test_a_reading_that_fails_is_read_as_pay_per_use(platform, monkeypatch):
    from iron_jarvis.providers import egress

    def boom(*_a, **_k):
        raise RuntimeError("cannot tell")

    monkeypatch.setattr(egress, "stays_on_this_network", boom)
    why = B.may_spend(platform, provider="claude-cli", model="claude-sonnet-4-6", night=NIGHT)
    assert why.endswith("bills per use, and the night review never spends money"), why


def test_local_only_work_is_refused_a_model_off_this_network(platform):
    why = B.may_spend(platform, provider="claude-cli", model="claude-sonnet-4-6", local_only=True, night=NIGHT)
    assert why.startswith("this work was Local only, and "), why
    assert why.endswith("runs off this network, so nothing was sent"), why
    assert B.needs_you(why)
    # ...and a model on this network takes it.
    assert B.may_spend(platform, provider="ollama", model="qwen3:8b", local_only=True, night=NIGHT) == ""


def test_an_account_at_70_percent_of_a_window_is_refused(platform):
    platform.iron_proxy = FakeProxy(used=82)
    why = B.may_spend(platform, provider="claude-cli", model="claude-sonnet-4-6", account="acc_work", night=NIGHT)
    assert why == "your Claude Code account was at 82% of its window", why
    assert not B.needs_you(why)
    # With no account named, the Default account of that tool is the one asked.
    assert B.may_spend(platform, provider="claude-cli", model="", night=NIGHT) == why
    # Under 70% it may be spent.
    platform.iron_proxy = FakeProxy(used=69)
    assert B.may_spend(platform, provider="claude-cli", model="", account="acc_work", night=NIGHT) == ""


def test_an_account_at_its_limit_is_refused(platform):
    platform.iron_proxy = FakeProxy(parked=True)
    why = B.may_spend(platform, provider="claude-cli", model="", account="acc_work", night=NIGHT)
    assert why == "your Claude Code account is at its limit", why


def test_emergency_stop_refuses_everything(platform):
    platform.config.autonomy_kill_switch = True
    assert B.may_spend(platform, provider="ollama", model="qwen3:8b", night=NIGHT) == B.WHY_STOPPED


def test_the_cap_stops_the_night_and_survives_a_restart(platform):
    home = platform.config.home
    platform.config.night_review_requests = 2
    assert B.cap_of(platform.config) == 2
    assert B.count(home, NIGHT) == 1
    assert B.count(home, NIGHT) == 2
    why = B.may_spend(platform, provider="ollama", model="qwen3:8b", night=NIGHT)
    assert why == "tonight's limit of 2 requests was reached", why
    # A restart forgets this process's own tally; the file keeps the night's.
    B.reset()
    assert B.requests_tonight(home, NIGHT) == 2
    assert B.may_spend(platform, provider="ollama", model="qwen3:8b", night=NIGHT) == why
    data = json.loads((platform.config.home / B.MARKER).read_text(encoding="utf-8"))
    assert data["nights"][NIGHT] == 2
    # Another night starts from nothing.
    assert B.requests_tonight(home, "2026-11-21") == 0
    assert B.may_spend(platform, provider="ollama", model="qwen3:8b", night="2026-11-21") == ""


def test_a_tally_file_that_cannot_be_read_counts_this_process(platform, monkeypatch):
    home = platform.config.home
    (home / B.MARKER).write_text("not json", encoding="utf-8")
    assert B.requests_tonight(home, NIGHT) == 0
    assert B.count(home, NIGHT) == 1
    # A file that cannot be READ (locked) never resets the night: this
    # process's own count still stands.
    monkeypatch.setattr(B, "_read", lambda _home: None)
    assert B.requests_tonight(home, NIGHT) == 1


def test_the_cap_is_bounded_one_to_twenty(platform):
    from types import SimpleNamespace

    assert B.cap_of(platform.config) == 6  # the default (D-17.1)
    for raw, want in ((0, 1), (99, 20), ("x", 6), (None, 6), (True, 6), (7, 7)):
        assert B.cap_of(SimpleNamespace(night_review_requests=raw)) == want, raw
