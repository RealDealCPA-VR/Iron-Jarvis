"""v1.420.0 (C3.5): a Codex turn plans against Codex's REAL window.

Before: the context ladder had a catalog rung for claude-cli only, so every
codex-cli turn planned its history against ``DEFAULT_WINDOW`` (32k): history
was dropped early and the 92% ceiling summarised a conversation Codex itself
had room for many times over. Now Codex's OWN model catalog
(``<CODEX_HOME>/models_cache.json``, written by Codex; read in the background,
never on the turn's thread) answers — the effective window, ``context_window``
x ``effective_context_window_percent`` — and a documented table copied from it
answers when the file cannot be read.
"""

from __future__ import annotations

import json
import threading
from types import SimpleNamespace

import pytest

from iron_jarvis.daemon import chat_turn
from iron_jarvis.daemon.chat_turn import (
    _context_window_source,
    codex_context_window,
    refresh_codex_windows,
)


@pytest.fixture(autouse=True)
def _fresh_catalog(monkeypatch, tmp_path):
    monkeypatch.setattr(chat_turn, "_CODEX_CATALOG", {"at": 0.0, "rows": None, "busy": False})
    home = tmp_path / "codex"
    home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(home))
    return home


def _d(inherits: dict[str, str] | None = None, pins: dict | None = None):
    inherits = inherits or {}
    providers = SimpleNamespace(
        measured_context_window=lambda p, m: None,
        inherited_from=lambda p: inherits.get(p),
    )
    config = SimpleNamespace(default_provider="", default_model="", model_context_windows=pins or {})
    return SimpleNamespace(platform=SimpleNamespace(config=config, providers=providers, fleet=None))


def _write_catalog(home, rows) -> None:
    (home / "models_cache.json").write_text(json.dumps({"models": rows}), encoding="utf-8")


ROWS = [
    {"slug": "gpt-6-astra", "context_window": 272_000, "effective_context_window_percent": 95,
     "visibility": "list"},
    {"slug": "gpt-5.6-terra", "context_window": 272_000, "effective_context_window_percent": 95,
     "visibility": "list"},
    {"slug": "codex-auto-review", "context_window": 100_000, "visibility": "hide"},
]


def test_codex_reads_its_own_catalogs_effective_window(_fresh_catalog):
    _write_catalog(_fresh_catalog, ROWS)
    refresh_codex_windows()
    assert _context_window_source(_d(), "codex-cli", "gpt-6-astra") == (258_400, "catalog")
    assert _context_window_source(_d(), "codex-cli", "gpt-5.6-terra") == (258_400, "catalog")
    assert _context_window_source(_d(), "codex-cli", "codex-auto-review") == (100_000, "catalog")


def test_codexs_own_default_answers_when_every_listed_model_agrees(_fresh_catalog):
    _write_catalog(_fresh_catalog, ROWS)
    refresh_codex_windows()
    assert _context_window_source(_d(), "codex-cli", "subscription") == (258_400, "catalog")
    assert _context_window_source(_d(), "codex-cli", "") == (258_400, "catalog")
    # Two listed windows that disagree: the default is not known.
    _write_catalog(_fresh_catalog, ROWS + [
        {"slug": "gpt-mini", "context_window": 128_000, "visibility": "list"},
    ])
    refresh_codex_windows()
    assert _context_window_source(_d(), "codex-cli", "default") == (None, "default")


def test_a_keyless_openai_served_by_codex_uses_it_too(_fresh_catalog):
    _write_catalog(_fresh_catalog, ROWS)
    refresh_codex_windows()
    d = _d(inherits={"openai": "codex-cli"})
    assert _context_window_source(d, "openai", "gpt-6-astra") == (258_400, "catalog")
    # Anti-vacuity: an OpenAI API key in use (no inheritance) stays on the old ladder.
    assert _context_window_source(_d(), "openai", "gpt-6-astra") == (None, "default")


def test_the_documented_table_answers_when_the_catalog_cannot_be_read(_fresh_catalog):
    refresh_codex_windows()  # no file
    assert _context_window_source(_d(), "codex-cli", "gpt-6-sol") == (258_400, "catalog")
    assert _context_window_source(_d(), "codex-cli", "gpt-5.6-luna") == (258_400, "catalog")
    assert _context_window_source(_d(), "codex-cli", "some-other-model") == (None, "default")


def test_a_user_pin_and_a_learned_window_still_win(_fresh_catalog, monkeypatch):
    _write_catalog(_fresh_catalog, ROWS)
    refresh_codex_windows()
    pinned = _d(pins={"codex-cli::gpt-6-astra": 64_000})
    assert _context_window_source(pinned, "codex-cli", "gpt-6-astra") == (64_000, "pin")
    monkeypatch.setattr(chat_turn, "learned_window", lambda d, p, m: 100_000)
    assert _context_window_source(_d(), "codex-cli", "gpt-6-astra") == (100_000, "learned")


def test_the_catalog_is_never_read_on_the_turns_own_thread(_fresh_catalog, monkeypatch):
    _write_catalog(_fresh_catalog, ROWS)
    readers: list[str] = []
    real = chat_turn.refresh_codex_windows
    done = threading.Event()

    def spy():
        readers.append(threading.current_thread().name)
        try:
            return real()
        finally:
            done.set()

    monkeypatch.setattr(chat_turn, "refresh_codex_windows", spy)
    # The first ask answers from the table at once and starts ONE background read.
    assert codex_context_window("gpt-6-astra") == 258_400
    assert codex_context_window("gpt-6-astra") == 258_400
    assert done.wait(10)
    assert readers == ["codex-windows"], readers
    assert threading.current_thread().name not in readers
