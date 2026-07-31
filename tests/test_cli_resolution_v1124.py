"""v1.124.0 — one resolver, one truth for subscription CLIs.

Live-hit 2026-07-31: availability said claude-cli was present (ai_clis._find
scans per-user bin dirs), but the ADAPTER used bare shutil.which — under the
packaged daemon's GUI PATH it found nothing, so the very first request
errored "not installed/on PATH" and failed over into a custom endpoint with
a stale model name. The adapters now default to the same finder the
availability probe uses.
"""

from __future__ import annotations

import pytest

from iron_jarvis.providers.adapters.subprocess_cli import (
    ClaudeCliAdapter,
    SubprocessCliAdapter,
    _which_cli,
)


def test_adapters_default_to_the_availability_resolver():
    # Both adapter classes must share the probe's resolver — a split brain
    # here is exactly the shipped bug.
    assert SubprocessCliAdapter.__init__.__kwdefaults__["which"] is _which_cli
    assert ClaudeCliAdapter.__init__.__kwdefaults__["which"] is _which_cli


def test_which_cli_falls_past_bare_path(monkeypatch, tmp_path):
    # PATH misses the binary; the per-user bin dirs (ai_clis._find) still
    # resolve it — the packaged-daemon scenario.
    import iron_jarvis.terminals.ai_clis as ai_clis

    fake = tmp_path / "bin"
    fake.mkdir()
    exe = fake / ("claude.cmd")
    exe.write_text("@echo off", encoding="utf-8")
    monkeypatch.setattr("shutil.which", lambda name: None)
    monkeypatch.setattr(ai_clis, "_extra_bin_dirs", lambda: [fake])
    assert _which_cli("claude") == str(exe)


def test_health_classes_subscription_clis_honestly(tmp_path):
    from iron_jarvis.platform import build_platform

    platform = build_platform(str(tmp_path))
    classes = {p["provider"]: p["class"] for p in platform.providers.health()}
    assert classes.get("claude-cli") == "cli"
    assert classes.get("codex-cli") == "cli"
    assert classes.get("mock", "mock") == "mock"


def test_doctor_flags_a_stale_custom_endpoint_model(tmp_path, monkeypatch):
    from iron_jarvis.onboarding.doctor import runtime_checks
    from iron_jarvis.platform import build_platform

    platform = build_platform(str(tmp_path))
    platform.config.custom_base_url = "http://gw.example/v1"
    platform.config.custom_model = "brain"

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"data": [{"id": "fleet"}, {"id": "vision"}, {"id": "frontier"}]}

    import httpx

    monkeypatch.setattr(httpx, "get", lambda *a, **k: _Resp())
    checks = {c["name"]: c for c in runtime_checks(platform)}
    row = checks.get("custom_endpoint_model")
    assert row is not None and row["ok"] is False
    assert "brain" in row["detail"] and "fleet" in row["detail"]
    assert "Connections page" in row["fix"]

    # And a VALID model passes clean.
    platform.config.custom_model = "fleet"
    checks = {c["name"]: c for c in runtime_checks(platform)}
    assert checks["custom_endpoint_model"]["ok"] is True
