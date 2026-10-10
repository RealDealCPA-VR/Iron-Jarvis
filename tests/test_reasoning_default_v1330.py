"""v1.330.0 - the level a model runs at when nothing is sent.

The composer's reasoning chip said "Reasoning" with no pick, so the user could
not tell what level the model would use. ``providers.reasoning.reasoning_default``
answers it ONLY where the vendor documents the default for that exact model
(absent = unknown, never a guess), and GET /models carries it as
``reasoning_default``, LAST on every row.
"""

from __future__ import annotations

import pytest

from iron_jarvis.providers import reasoning as R


@pytest.mark.parametrize(
    "provider,model,expected",
    [
        # Anthropic Messages API: the older family thinks only when asked.
        ("anthropic", "claude-opus-4-8", "off"),
        ("anthropic", "claude-opus-4-8[1m]", "off"),
        ("anthropic", "claude-haiku-4-5-20251001", "off"),
        ("anthropic", "claude-sonnet-4-6", "off"),
        ("anthropic", "claude-3-7-sonnet-20250219", "off"),
        ("anthropic", "claude-opus-4-1-20250805", "off"),
        # ...the 5 family thinks adaptively at the documented effort default.
        ("anthropic", "claude-opus-5", "high"),
        ("anthropic", "claude-sonnet-5", "high"),
        ("anthropic", "claude-sonnet-5-5", "high"),
        ("anthropic", "claude-fable-5-1", "high"),
        ("anthropic", "claude-opus-5-5", "medium"),
        ("anthropic", "claude-opus-5-5[1m]", "medium"),
        # A Claude id the table has not read docs for: unknown.
        ("anthropic", "claude-opus-6", ""),
        # A model with no levels has no default to show.
        ("anthropic", "claude-3-5-sonnet-20241022", ""),
        # OpenAI: documented per model.
        ("openai", "o3", "medium"),
        ("openai", "o3-2025-04-16", "medium"),
        ("openai", "o4-mini", "medium"),
        ("openai", "gpt-5", "medium"),
        ("openai", "gpt-5-mini-2025-08-07", "medium"),
        ("openai", "gpt-5-pro", "high"),
        ("openai", "gpt-5.1", "off"),
        ("openai", "gpt-5.4", "off"),
        ("openai", "gpt-5.5", "medium"),
        ("openai", "gpt-5.5-pro", "high"),
        ("openai", "gpt-5.6", "medium"),
        ("openai", "gpt-5.1-codex", ""),
        ("openai", "gpt-5.6-terra", ""),
        ("openai", "gpt-4o", ""),
        # Gemini: 2.5 thinks dynamically, 3.x at a documented level.
        ("google", "gemini-2.5-pro", "auto"),
        ("google", "models/gemini-2.5-flash", "auto"),
        ("google", "gemini-2.5-flash-preview-05-20", "auto"),
        ("google", "gemini-2.5-flash-lite", "off"),
        ("google", "gemini-3-pro-preview", "high"),
        ("google", "gemini-3.6-flash", "medium"),
        ("google", "gemini-3.5-flash-lite", ""),
        ("google", "gemini-2.5-flash-image", ""),
        ("google", "gemini-1.5-flash", ""),
        # CLIs and local endpoints: no single documented default.
        ("claude-cli", "subscription", ""),
        ("claude-cli", "claude-opus-4-8", ""),
        ("codex-cli", "subscription", ""),
        ("custom", "gpt-oss-120b", ""),
        ("fleet-a1b2c3", "Qwen/Qwen3-235B-A22B-Thinking", ""),
        ("ollama", "deepseek-r1:70b", ""),
        ("openrouter", "anthropic/claude-opus-4-8", ""),
        ("openrouter", "openai/gpt-5", ""),
        ("", "gpt-5", ""),
        ("openai", "", ""),
    ],
)
def test_the_documented_default_per_provider_and_model(provider, model, expected):
    assert R.reasoning_default(provider, model) == expected, (provider, model)


def test_every_answer_is_in_the_closed_vocabulary_and_only_where_levels_exist():
    tables = (
        ("anthropic", R._ANTHROPIC_DEFAULTS),
        ("openai", R._OPENAI_DEFAULTS),
        ("google", R._GEMINI_DEFAULTS),
    )
    for provider, table in tables:
        for model, value in table.items():
            assert value in R.DEFAULTS, (model, value)
            # A row whose model offers no levels would never show the chip:
            # the table must not carry dead rows.
            assert R.reasoning_levels(provider, model), (provider, model)
            assert R.reasoning_default(provider, model) == value, (provider, model)
    # "off" and "auto" are never levels the composer SENDS.
    assert R.normalize_level("off") == ""
    assert R.normalize_level("auto") == ""


def test_a_keyless_provider_served_by_a_cli_is_unknown():
    """A keyless ``anthropic`` is served through the logged-in claude CLI,
    whose effort depends on the user's own settings: never claim the API's."""
    assert R.reasoning_default("anthropic", "claude-opus-4-8") == "off"
    assert R.reasoning_default("anthropic", "claude-opus-4-8", served_by="claude-cli") == ""
    assert R.reasoning_default("openai", "gpt-5", served_by="codex-cli") == ""
    assert R.reasoning_default("openai", "gpt-5", served_by=None) == "medium"


def test_the_catalog_row_carries_the_default_last(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from iron_jarvis.daemon.app import create_app

    monkeypatch.setenv("IRONJARVIS_TOKEN", "t")
    app = create_app(str(tmp_path / "home"))
    with TestClient(app) as client:
        rows = client.get("/models", headers={"Authorization": "Bearer t"}).json()["models"]
    assert rows, "no catalog"
    for row in rows:
        assert "reasoning_default" in row, row
        assert list(row)[-1] == "reasoning_default", list(row)
        served = row.get("inherited_from") or None
        assert row["reasoning_default"] == R.reasoning_default(
            row["provider"], row["model"], served_by=served
        ), row
    # Anti-vacuity: the catalog shows at least one documented default, and
    # at least one unknown, so the field discriminates on a real install.
    assert any(row["reasoning_default"] for row in rows), [
        (r["provider"], r["model"], r.get("inherited_from")) for r in rows
    ]
    assert any(not row["reasoning_default"] for row in rows)


def test_a_served_by_cli_row_reports_unknown_on_the_route(tmp_path, monkeypatch):
    """Through the real route: an anthropic row the manager says is served by
    the claude CLI carries "", an API-key one carries the documented value."""
    from fastapi.testclient import TestClient

    from iron_jarvis.daemon.app import create_app

    monkeypatch.setenv("IRONJARVIS_TOKEN", "t")
    app = create_app(str(tmp_path / "home"))
    providers = app.state.platform.providers
    with TestClient(app) as client:
        monkeypatch.setattr(
            providers, "inherited_from", lambda p: "claude-cli" if p == "anthropic" else None
        )
        served = client.get("/models", headers={"Authorization": "Bearer t"}).json()["models"]
        monkeypatch.setattr(providers, "inherited_from", lambda p: None)
        keyed = client.get("/models", headers={"Authorization": "Bearer t"}).json()["models"]
    served_claude = [r for r in served if r["provider"] == "anthropic" and r["reasoning"]]
    keyed_claude = [r for r in keyed if r["provider"] == "anthropic" and r["reasoning"]]
    assert served_claude and keyed_claude
    assert all(r["reasoning_default"] == "" for r in served_claude)
    assert any(r["reasoning_default"] for r in keyed_claude), [
        (r["model"], r["reasoning_default"]) for r in keyed_claude
    ]
