"""Readiness 12A (G33) — a signed-out Claude Code or Codex is met with the one
Sign in press (or its words where no press is possible), never a typed
terminal recipe, and never inside a green "complete" note.

THE GAP (docs/PRODUCT-READINESS-RESULTS.md 0d.6, carried through 0e/0f):
``cli_auth.SIGN_IN_FIX`` read "Claude Code isn't signed in. Open a terminal,
run `claude`, then `/login`, then click Test on Connections." and reached a
person after Rescan on Settings › Connections (``POST /providers/rescan``'s
row ``detail``, printed INSIDE the page's green "Rescan complete" note), in
Jarvis's own health answers (``run_doctor`` / ``app_health``), in the
use-model refusal, and — through ``/health``'s ``sign_in_fix`` — on the chat's
warning above the message box and the New task door, which hand-wrote their
own "run `claude` in a terminal, then /login". The app has had a Sign in
press beside each CLI since v1.353.0 (``lib/cliSignIn``).

NOW the sentence names the press ("Press Sign in beside Claude Code in
Settings › Connections." — ``failure_words.ACCOUNTS_PLACE``), the rescan row
carries it with ``signed_in`` beside it, the doctor's row names the program
plainly, and every dashboard surface draws the press itself
(``components/connections/CliSignInPress``). The failure words still
recognise a CLI's own sign-in refusal (they match the constant).

Driven through the REAL app factory (``/providers/rescan``, ``/health``,
``/onboarding/use-model``), the real doctor and the real self-care tool, with
only the CLI's own status answer standing in. The CONTRACT FIXTURE
``dashboard/__tests__/fixtures/sign-in-press-v1387.json`` is those answers
(``IJ_REGEN_SIGN_IN_PRESS=1``), read by ``dashboard/__tests__/
sign-in-press-v1387.test.tsx`` through the real Connections page, the real
chat warning and the real New task door.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.providers import cli_auth
from iron_jarvis.providers import failure_words as fw
from iron_jarvis.providers.cli_auth import SIGN_IN_FIX, CliAuthProbe, cli_failure_message
from iron_jarvis.providers.manager import ProviderManager

_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = _ROOT / "dashboard" / "__tests__" / "fixtures" / "sign-in-press-v1387.json"

#: A typed terminal recipe, in any of the shapes it took.
RECIPE = ("terminal", "`claude`", "/login", "codex login", "run claude", "click Test")


def _no_recipe(text: str) -> None:
    for word in RECIPE:
        assert word not in text, (word, text)


@pytest.fixture()
def signed_out(monkeypatch):
    """Both CLIs on this PC, both answering their own status check "not
    logged in" (the only stand-in: the CLI's answer)."""
    monkeypatch.setattr(ProviderManager, "_cli_binary_present", staticmethod(lambda b: b in ("claude", "codex")))
    # tests/conftest.py stubs the cached verdict to None (unknown); here the
    # CLI SAID it is signed out.
    monkeypatch.setattr(ProviderManager, "_cli_signed_in", staticmethod(lambda b: False))
    fake = CliAuthProbe(runner=lambda argv: (1, "Not logged in", ""), which=lambda b: "/x/" + b)
    monkeypatch.setattr(cli_auth, "DEFAULT_PROBE", fake)
    return fake


def test_the_sentence_names_the_press_and_never_a_terminal():
    assert SIGN_IN_FIX["claude"] == (
        f"Claude Code isn't signed in. Press Sign in beside Claude Code in {fw.ACCOUNTS_PLACE}."
    )
    assert SIGN_IN_FIX["codex"] == f"Codex isn't signed in. Press Sign in beside Codex in {fw.ACCOUNTS_PLACE}."
    for text in SIGN_IN_FIX.values():
        _no_recipe(text)


def test_a_cli_refusal_carrying_it_is_still_read_as_signed_out():
    """The failure words recognise the CLI's own sign-in refusal by this ONE
    constant: the chat's headline and fix are unchanged."""
    msg = cli_failure_message("claude-cli", "claude", 1, '{"is_error":true,"result":"Not logged in"}', "")
    assert SIGN_IN_FIX["claude"] in msg
    w = fw.friendly_error(RuntimeError(msg))
    assert w["title"] == "Claude Code isn't signed in"
    assert w["fix"] == f"Press Sign in beside Claude Code in {fw.ACCOUNTS_PLACE}, then retry."
    _no_recipe(w["title"] + w["fix"])


def _answers(tmp_path) -> dict:
    client = TestClient(create_app(str(tmp_path / "home")))
    rescan = client.post("/providers/rescan").json()["detected"]
    health = client.get("/health").json()
    use = client.post("/onboarding/use-model", json={"provider": "claude-cli"})
    return {
        "client": client,
        "rescan": [r for r in rescan if r.get("provider") in ("claude-cli", "codex-cli")],
        "health": [r for r in health.get("providers", []) if r.get("provider") in ("claude-cli", "codex-cli")],
        "use_model": {"status": use.status_code, "detail": use.json().get("detail")},
    }


def test_rescan_names_the_press_and_says_signed_out(tmp_path, signed_out):
    got = _answers(tmp_path)
    rows = {r["provider"]: r for r in got["rescan"]}
    assert set(rows) == {"claude-cli", "codex-cli"}
    for prov, binary in (("claude-cli", "claude"), ("codex-cli", "codex")):
        row = rows[prov]
        assert row["available"] is False and row["signed_in"] is False
        # RED on the old code: "<the CLI's words>. … Open a terminal, run …".
        assert row["detail"] == SIGN_IN_FIX[binary]
        _no_recipe(row["detail"])


def test_health_and_the_use_model_refusal_name_the_press(tmp_path, signed_out):
    got = _answers(tmp_path)
    for row in got["health"]:
        if row.get("sign_in_fix"):
            _no_recipe(row["sign_in_fix"])
    assert got["use_model"]["status"] == 409
    assert got["use_model"]["detail"] == SIGN_IN_FIX["claude"]


def test_the_doctor_row_names_the_program_and_the_press(tmp_path, monkeypatch):
    from iron_jarvis.onboarding.doctor import runtime_checks
    from iron_jarvis.platform import build_platform

    monkeypatch.setattr(ProviderManager, "_cli_binary_present", staticmethod(lambda b: b == "codex"))
    monkeypatch.setattr(ProviderManager, "_cli_signed_in", staticmethod(lambda b: False))
    checks = {c["name"]: c for c in runtime_checks(build_platform(str(tmp_path)))}
    row = checks["codex-cli_login"]
    assert row["ok"] is False
    assert row["detail"] == "Codex is installed but not signed in, so it can't answer yet"
    assert row["fix"] == SIGN_IN_FIX["codex"]
    for raw in ("codex CLI", "NOT signed in", "refuses every request"):
        assert raw not in row["detail"], raw


def test_jarvis_own_health_answer_names_the_model_and_the_press(tmp_path, monkeypatch):
    """``app_health`` (what Jarvis says when asked "what's wrong?"): the model
    by its plain name, the fix the press's words."""
    from iron_jarvis.tools.selfcare import AppHealthTool

    words = AppHealthTool._model_section(
        "claude-cli",
        "subscription",
        [{"provider": "claude-cli", "available": False, "sign_in_fix": SIGN_IN_FIX["claude"]}],
    )["words"]
    assert words == (
        "Model: Claude Code is NOT reachable. Claude Code isn't signed in. "
        "Press Sign in beside Claude Code in Settings › Connections."
    )
    assert "claude-cli" not in words and "subscription" not in words
    assert AppHealthTool._packs_words([]) == "No extensions are set up."


def test_the_contract_fixture_is_the_real_daemons_answer(tmp_path, signed_out):
    got = _answers(tmp_path)
    data = {
        "about": (
            "Readiness 12A (G33): the REAL daemon's answers with Claude Code and Codex on this PC "
            "but signed out — POST /providers/rescan's rows, GET /health's rows and the use-model "
            "refusal. Written by tests/test_sign_in_press_v1387.py (IJ_REGEN_SIGN_IN_PRESS=1); read "
            "by dashboard/__tests__/sign-in-press-v1387.test.tsx."
        ),
        "rescan": got["rescan"],
        "health": [
            {k: r.get(k) for k in ("provider", "available", "installed", "signed_in", "sign_in_fix", "class")}
            for r in got["health"]
        ],
        "use_model": got["use_model"],
    }
    if os.environ.get("IJ_REGEN_SIGN_IN_PRESS") == "1":
        FIXTURE.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    assert json.loads(FIXTURE.read_text(encoding="utf-8")) == json.loads(json.dumps(data)), (
        "regenerate with IJ_REGEN_SIGN_IN_PRESS=1 after an intended change"
    )


def test_no_new_sentence_uses_a_retired_word():
    from tests.test_vocabulary_beyond_dashboard_v13580 import _hits

    for line in (*SIGN_IN_FIX.values(), "No extensions are set up.",
                 "Codex is installed but not signed in, so it can't answer yet"):
        assert _hits(line) == [], line


_ = asyncio  # (kept for parity with the other contract-fixture files)
