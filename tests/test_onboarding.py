"""Onboarding + doctor tests — fully offline.

Uses the shared ``platform`` fixture (conftest, built via ``build_platform`` on a
tmp dir). The first-run / checklist assertions clear ANTHROPIC_API_KEY so a key
in the developer's environment can't make a fresh install look "connected".
"""

from __future__ import annotations

import pytest

from iron_jarvis import __version__
from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.onboarding import doctor, getting_started, is_first_run, readiness
from iron_jarvis.onboarding.checklist import voice_backend_present


@pytest.fixture(autouse=True)
def _no_real_provider(monkeypatch):
    # A fresh install must look offline-only regardless of the host environment.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


# --- doctor ---------------------------------------------------------------


def test_doctor_returns_ok_shape_with_python_ok():
    result = doctor()

    assert set(result) >= {"ok", "checks"}
    assert isinstance(result["ok"], bool)  # never raises; always a bool
    assert isinstance(result["checks"], list) and result["checks"]

    # Every check carries the documented, renderable shape.
    for check in result["checks"]:
        assert {"name", "ok", "detail", "fix"} <= set(check)
        assert isinstance(check["ok"], bool)

    # The Python check must pass — the test suite is running on a supported Python.
    py = next(c for c in result["checks"] if c["name"] == "python")
    assert py["ok"] is True

    # The dashboard/voice checks exist and are non-fatal (recommended).
    names = {c["name"] for c in result["checks"]}
    assert {"git", "node", "pnpm", "uv", "browser"} <= names


# --- getting_started ------------------------------------------------------


def test_getting_started_returns_core_steps_plus_optional_voice(platform):
    steps = getting_started(platform)

    for step in steps:
        assert {"key", "title", "detail", "done", "action"} <= set(step)
        assert isinstance(step["done"], bool)

    keys = [s["key"] for s in steps]
    # Four core steps in order, then the OPTIONAL voice step last.
    assert keys == [
        "connect_ai",
        "first_session",
        "work_with_document",
        "teach_style",
        "set_up_voice",
    ]

    # The core steps are never optional; only set_up_voice is.
    by_key = {s["key"]: s for s in steps}
    for core in ("connect_ai", "first_session", "work_with_document", "teach_style"):
        assert by_key[core].get("optional", False) is False
    assert by_key["set_up_voice"]["optional"] is True


async def test_first_session_step_flips_after_running(platform):
    before = {s["key"]: s["done"] for s in getting_started(platform)}
    assert before["first_session"] is False

    await Orchestrator(platform).run("make a file")

    after = {s["key"]: s["done"] for s in getting_started(platform)}
    assert after["first_session"] is True


# --- is_first_run ---------------------------------------------------------


async def test_is_first_run_true_then_false(platform):
    assert is_first_run(platform) is True

    await Orchestrator(platform).run("make a file")

    assert is_first_run(platform) is False


# --- readiness ------------------------------------------------------------


def test_readiness_includes_version_doctor_and_checklist(platform):
    report = readiness(platform)

    assert report["version"] == __version__
    assert report["first_run"] is True  # fresh platform, no real provider
    assert "ok" in report["doctor"] and isinstance(report["doctor"]["checks"], list)
    assert isinstance(report["checklist"], list) and len(report["checklist"]) == 5
    # next_step is the first incomplete REQUIRED step on a fresh install — the
    # optional voice step is never advertised as "next".
    assert report["next_step"]["key"] == "connect_ai"


# --- voice readiness ------------------------------------------------------


def test_readiness_carries_voice_field_absent_by_default(platform):
    """A fresh install has no speech-to-text backend, reported honestly."""
    report = readiness(platform)

    assert "voice" in report
    assert report["voice"] == {"available": False, "backend": None}


def test_voice_backend_present_detects_openai_key(platform):
    """An OpenAI API key in the vault flips voice readiness to available."""
    assert voice_backend_present(platform) == (False, None)

    platform.secrets.set("openai_api_key", "sk-test-voice")

    assert voice_backend_present(platform) == (True, "openai")

    report = readiness(platform)
    assert report["voice"] == {"available": True, "backend": "openai"}
    # The voice checklist item is now done, but STILL optional and never next.
    voice_step = next(s for s in report["checklist"] if s["key"] == "set_up_voice")
    assert voice_step["done"] is True
    assert voice_step["optional"] is True


def test_voice_backend_present_detects_custom_endpoint(platform):
    """A configured custom OpenAI-compatible endpoint counts as a backend."""
    platform.config.custom_base_url = "http://localhost:1234/v1"

    assert voice_backend_present(platform) == (True, "custom")
    assert readiness(platform)["voice"] == {"available": True, "backend": "custom"}


def test_optional_voice_step_is_never_next_step_even_when_incomplete(platform):
    """The optional voice item must never be surfaced as next_step, even though
    it is incomplete on a fresh install (a real required step outranks it)."""
    report = readiness(platform)

    voice_step = next(s for s in report["checklist"] if s["key"] == "set_up_voice")
    assert voice_step["done"] is False  # no backend yet
    assert voice_step["optional"] is True
    # next_step is a REQUIRED step, never the optional voice one.
    assert report["next_step"] is not None
    assert report["next_step"]["key"] != "set_up_voice"


async def test_optional_voice_step_never_keeps_first_run_true(platform):
    """The incomplete optional voice step must NOT keep first_run true: once a
    session has run, first_run flips to False even with no voice backend."""
    await Orchestrator(platform).run("make a file")

    report = readiness(platform)
    assert report["first_run"] is False
    voice_step = next(s for s in report["checklist"] if s["key"] == "set_up_voice")
    assert voice_step["done"] is False  # voice still not set up, yet not blocking
