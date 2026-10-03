"""v1.300.0 — a Claude-subscription turn plans against the model's REAL window.

Before: claude-cli had no rung on the context ladder, so every subscription
turn planned with the conservative default budgets (an unknown window), even
for 1M-context models. Now the account's live picker (providers/claude_models,
never spawning the CLI on this path) answers between the measured envelope and
the fleet probe, for claude-cli and for a keyless provider served through it.
"""
from __future__ import annotations

from types import SimpleNamespace

from iron_jarvis.daemon.chat_turn import _context_window_source
from iron_jarvis.providers import claude_models


def _d(inherits: dict[str, str] | None = None, pins: dict | None = None):
    inherits = inherits or {}
    providers = SimpleNamespace(
        measured_context_window=lambda p, m: None,
        inherited_from=lambda p: inherits.get(p),
    )
    config = SimpleNamespace(default_provider="", default_model="", model_context_windows=pins or {})
    return SimpleNamespace(platform=SimpleNamespace(config=config, providers=providers, fleet=None))


def _catalog(monkeypatch, rows):
    monkeypatch.setattr(claude_models, "_known", lambda: {"models": rows})


ROWS = [
    {"id": "claude-opus-5-5", "value": "opus", "context_window": 1_000_000, "default": True},
    {"id": "claude-haiku-4-5-20251001", "value": "haiku", "context_window": 200_000},
]


def test_claude_cli_reads_the_live_picker_window(monkeypatch):
    _catalog(monkeypatch, ROWS)
    assert _context_window_source(_d(), "claude-cli", "claude-opus-5-5") == (1_000_000, "catalog")
    assert _context_window_source(_d(), "claude-cli", "claude-haiku-4-5-20251001") == (200_000, "catalog")


def test_the_cli_default_answers_through_the_default_row(monkeypatch):
    _catalog(monkeypatch, ROWS)
    assert _context_window_source(_d(), "claude-cli", "subscription") == (1_000_000, "catalog")


def test_a_keyless_provider_served_by_the_cli_uses_it_too(monkeypatch):
    _catalog(monkeypatch, ROWS)
    d = _d(inherits={"anthropic": "claude-cli"})
    assert _context_window_source(d, "anthropic", "claude-opus-5-5") == (1_000_000, "catalog")


def test_an_api_provider_does_not_read_the_subscription_catalog(monkeypatch):
    # Anti-vacuity: an anthropic key in use (no inheritance) stays on the old ladder.
    _catalog(monkeypatch, ROWS)
    assert _context_window_source(_d(), "anthropic", "claude-opus-5-5") == (None, "default")


def test_a_user_pin_still_wins(monkeypatch):
    _catalog(monkeypatch, ROWS)
    d = _d(pins={"claude-cli::claude-opus-5-5": 300_000})
    assert _context_window_source(d, "claude-cli", "claude-opus-5-5") == (300_000, "pin")


def test_an_unknown_model_stays_unknown(monkeypatch):
    _catalog(monkeypatch, ROWS)
    assert _context_window_source(_d(), "claude-cli", "claude-new-thing-9") == (None, "default")
