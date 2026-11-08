"""v1.449.0 (readiness Wave C, item 26) — the home's helper rows speak to the
person using the packaged app.

The Status page's fix line for a missing .xls reader said "Reinstall
dependencies (`uv sync`)" — a developer's fix, shown to the user of the
installed app. A frozen build that lacks its reader is a broken build; its
fix is a reinstall, in plain words. The source checkout keeps the developer
words.
"""

from __future__ import annotations

import importlib
import sys

from iron_jarvis.onboarding.readiness import end_user_checks

# The package re-exports the FUNCTION `doctor`, which shadows the module of
# the same name (the v1.254.0 lesson): reach the module through importlib.
doctor_mod = importlib.import_module("iron_jarvis.onboarding.doctor")


def _row(monkeypatch, *, frozen: bool) -> dict:
    monkeypatch.setattr("iron_jarvis.documents.legacy.xls_available", lambda: False)
    monkeypatch.setattr("iron_jarvis.documents.legacy.word_available", lambda: False)
    if frozen:
        monkeypatch.setattr(sys, "frozen", True, raising=False)
    else:
        monkeypatch.delattr(sys, "frozen", raising=False)
    return doctor_mod.check_legacy_office()


def test_the_packaged_app_never_says_uv_sync(monkeypatch):
    """MUTATION: hand back the developer words for a frozen build -> red."""
    row = _row(monkeypatch, frozen=True)
    assert row["ok"] is False and row["level"] == "recommended"
    assert "uv sync" not in row["fix"] and "`" not in row["fix"]
    assert "Reinstall" in row["fix"] and "Updates page" in row["fix"]


def test_a_source_checkout_keeps_the_developers_words(monkeypatch):
    row = _row(monkeypatch, frozen=False)
    assert "uv sync" in row["fix"]


def test_the_end_user_rows_carry_the_same_fix(monkeypatch):
    """GET /onboarding's rows are the doctor's rows: the home reads the
    words the row carries."""
    monkeypatch.setattr("iron_jarvis.documents.legacy.xls_available", lambda: False)
    monkeypatch.setattr("iron_jarvis.documents.legacy.word_available", lambda: False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    rows = {c["name"]: c for c in end_user_checks()["checks"]}
    assert "uv sync" not in rows["old Office files"]["fix"]
    assert rows["old Office files"]["label"] == "Reading old .doc and .xls files"
