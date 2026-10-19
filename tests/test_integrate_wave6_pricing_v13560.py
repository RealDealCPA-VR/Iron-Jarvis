"""v1.356.0 integrate (the v1.355.0 "Known" line): ``eval/pricing.py`` has
rows for Google's default Gemini models, read on Google's own pricing page
(https://ai.google.dev/gemini-api/docs/pricing, "Last updated 2026-10-16 UTC",
read 2026-10-19; Standard paid tier, USD per 1M tokens). Before this, both
fell to the generic ``gemini`` row ($1.25 / $5.00) and an agent's dollar
allowance over-counted every Gemini 3.8 Flash run.

gemini-3.8-flash's price DOUBLES on 2027-01-01 (the page says so); the row's
comment says to update it then, and this pin names the date so a reader of a
red test knows why.
"""

from __future__ import annotations

from pathlib import Path

from iron_jarvis.eval.pricing import PRICES, cost_for


def test_gemini_default_models_price_at_googles_published_rates():
    m = 1_000_000
    assert cost_for("google", "gemini-3.8-flash", m, 0) == 0.75
    assert cost_for("google", "gemini-3.8-flash", 0, m) == 3.75
    assert cost_for("google", "gemini-3.5-flash-lite", m, 0) == 0.30
    assert cost_for("google", "gemini-3.5-flash-lite", 0, m) == 2.50
    assert PRICES[("google", "gemini-3.8-flash")]["cache_read"] == 0.075
    assert PRICES[("google", "gemini-3.5-flash-lite")]["cache_read"] == 0.03


def test_the_rows_cite_the_page_and_the_date():
    src = (Path(__file__).resolve().parents[1] / "src" / "iron_jarvis" / "eval" / "pricing.py").read_text(
        encoding="utf-8"
    ).replace("\r\n", "\n")
    at = src.find('("google", "gemini-3.8-flash")')
    assert at > 0
    before = src[max(0, at - 700) : at]
    assert "ai.google.dev/gemini-api/docs/pricing" in before
    assert "2026-10-19" in before and "January 1, 2027" in before
