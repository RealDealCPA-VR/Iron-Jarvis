"""v1.234.0 — installed is not signed in (subscription CLIs).

THE LIVE REPORT (2026-09-06). A user's chat turn failed with::

    claude-cli: CLI exited 1: {"type":"result","subtype":"success",
    "is_error":true,...,"result":"Not logged in · Please run /login",...

Two defects stacked, both pinned here, each mutation-proven (revert the fix,
watch the named assertion go red):

1. ``ClaudeCliAdapter.complete`` honoured the exit code BEFORE ``_parse`` saw
   the JSON, so the readable ``result`` sentence never reached the user —
   ``test_exit_1_with_result_json_yields_the_sentence_not_the_dump``.
2. ``available("claude-cli")`` meant "binary on disk"; a logged-out CLI read
   connected on every surface and refused the first turn —
   ``test_signed_out_cli_is_not_available_and_not_inherited``.

Plus the seams around them: the probe parsers, the non-blocking cache, the
adapter's feedback into the probe, the router's refusal wording, the /health
row, the rescan route and the doctor line.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from iron_jarvis.providers import cli_auth
from iron_jarvis.providers.adapters.subprocess_cli import make_claude_cli, make_codex_cli
from iron_jarvis.providers.cli_auth import (
    SIGN_IN_FIX,
    CliAuthProbe,
    cli_failure_message,
    is_sign_in_refusal,
    parse_claude_status,
    parse_codex_status,
)
from iron_jarvis.providers.manager import ProviderManager

LIVE_JSON = json.dumps(
    {
        "type": "result",
        "subtype": "success",
        "is_error": True,
        "api_error_status": None,
        "duration_ms": 150,
        "num_turns": 1,
        "result": "Not logged in · Please run /login",
        "session_id": "654a16bd-bbcd-4150-8305-c3dc45b68390",
        "total_cost_usd": 0,
        "usage": {"input_tokens": 0, "output_tokens": 0},
    }
)


@pytest.fixture(autouse=True)
def _fresh_shared_probe(monkeypatch):
    """The adapters report sign-in refusals to the module-wide probe; give
    every test its own so one case's refusal never colours the next."""
    monkeypatch.setattr(cli_auth, "DEFAULT_PROBE", CliAuthProbe(which=lambda b: None))


# --- 1. the message ------------------------------------------------------------


@pytest.mark.asyncio
async def test_exit_1_with_result_json_yields_the_sentence_not_the_dump(monkeypatch):
    """The exact live shape: exit 1 AND the result JSON on stdout."""
    seen: list[tuple[str, str]] = []
    monkeypatch.setattr(cli_auth, "DEFAULT_PROBE", CliAuthProbe(which=lambda b: "/x/" + b))
    monkeypatch.setattr(
        cli_auth.DEFAULT_PROBE, "mark_signed_out", lambda b, d="": seen.append((b, d))
    )
    a = make_claude_cli(
        runner=lambda argv, stdin=None: (1, LIVE_JSON, ""), which=lambda b: "/x/claude"
    )
    with pytest.raises(RuntimeError) as ei:
        await a.complete(system="", messages=[], tools=[])
    msg = str(ei.value)
    assert SIGN_IN_FIX["claude"] in msg
    assert "Not logged in" in msg  # the CLI's own words survive, in parentheses
    assert '"type":"result"' not in msg  # never the dump
    assert "CLI exited 1" not in msg
    # The shared probe was told: the next availability check is honest at once.
    assert seen and seen[0][0] == "claude"


@pytest.mark.asyncio
async def test_exit_0_is_error_json_maps_the_same_way():
    a = make_claude_cli(
        runner=lambda argv, stdin=None: (0, LIVE_JSON, ""), which=lambda b: "/x/claude"
    )
    with pytest.raises(RuntimeError, match="isn't signed in"):
        await a.complete(system="", messages=[], tools=[])


@pytest.mark.asyncio
async def test_codex_sign_in_refusal_names_codex_login():
    a = make_codex_cli(
        runner=lambda argv, stdin=None: (1, "", "Not logged in. Run `codex login`."),
        which=lambda b: "/x/codex",
    )
    with pytest.raises(RuntimeError, match="codex login"):
        await a.complete(system="", messages=[], tools=[])


def test_non_sign_in_failures_keep_the_old_wording():
    # An API error is NOT relabelled as a sign-in problem.
    msg = cli_failure_message("claude-cli", "claude", 1, "", "overloaded_error: try again")
    assert msg == "claude-cli: CLI exited 1: overloaded_error: try again"
    # JSON with is_error and a non-login result: the result sentence, no dump.
    js = json.dumps({"is_error": True, "result": "Rate limit reached"})
    assert cli_failure_message("claude-cli", "claude", 1, js, "") == "claude-cli: Rate limit reached"


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Not logged in · Please run /login", True),
        ("login required", True),
        ("Please sign in", True),
        ("input_tokens: 12, login_id: 44", False),
        ("Rate limit reached", False),
        ("", False),
    ],
)
def test_sign_in_refusal_detector_is_narrow(text, expected):
    assert is_sign_in_refusal(text) is expected


# --- 2. the probe ----------------------------------------------------------------


def test_claude_status_parser():
    assert parse_claude_status(0, '{"loggedIn": true, "authMethod": "claude.ai"}', "") == (
        True,
        "signed in via claude.ai",
    )
    assert parse_claude_status(0, '{"loggedIn": false}', "")[0] is False
    # An older CLI printing usage is INCONCLUSIVE, never signed-out.
    assert parse_claude_status(1, "", "error: unknown command 'auth'")[0] is None


def test_codex_status_parser():
    assert parse_codex_status(0, "Logged in using ChatGPT\n", "")[0] is True
    assert parse_codex_status(1, "Not logged in\n", "")[0] is False
    assert parse_codex_status(2, "", "usage: codex ...")[0] is None


def test_probe_refresh_marks_and_timeouts_are_inconclusive():
    calls: list[list[str]] = []

    def runner(argv):
        calls.append(argv)
        if argv[0].endswith("claude"):
            return 0, '{"loggedIn": false}', ""
        raise subprocess.TimeoutExpired(argv, 1)

    probe = CliAuthProbe(runner=runner, which=lambda b: f"/x/{b}")
    st = probe.refresh("claude")
    assert (st.installed, st.signed_in) == (True, False)
    st = probe.refresh("codex")
    assert (st.installed, st.signed_in) == (True, None)
    assert "timed out" in st.detail
    # Not installed: no subprocess at all.
    probe2 = CliAuthProbe(runner=runner, which=lambda b: None)
    assert probe2.refresh("claude").installed is False
    assert probe.refresh("nope").signed_in is None


def test_verdict_never_blocks_and_refreshes_in_the_background():
    started = []

    class Probe(CliAuthProbe):
        def _start(self, binary):  # no threads in the test: record the intent
            started.append(binary)

    clock = [0.0]
    probe = Probe(runner=lambda argv: (0, '{"loggedIn": true}', ""), which=lambda b: "/x/" + b,
                  ttl_s=10, clock=lambda: clock[0])
    assert probe.verdict("claude") is None  # nothing cached yet
    assert started == ["claude"]
    probe.refresh("claude")
    assert probe.verdict("claude") is True
    assert started == ["claude"]  # fresh cache: no second refresh
    clock[0] = 11.0
    assert probe.verdict("claude") is True  # stale value still returned...
    assert started == ["claude", "claude"]  # ...and ONE refresh started
    assert probe.verdict("claude") is True
    assert started == ["claude", "claude"]  # in-flight guard: not a third
    probe.mark_signed_out("claude")
    assert probe.verdict("claude") is False


# --- 3. availability -------------------------------------------------------------


def test_signed_out_cli_is_not_available_and_not_inherited(monkeypatch):
    monkeypatch.setattr(
        ProviderManager, "_cli_binary_present", staticmethod(lambda b: b == "claude")
    )
    monkeypatch.setattr(ProviderManager, "_cli_signed_in", staticmethod(lambda b: False))
    m = ProviderManager(inherit_cli_logins=True)
    assert m.available("claude-cli") is False
    assert m.inherited_from("anthropic") is None
    assert m.available("anthropic") is False
    row = next(r for r in m.health() if r["provider"] == "claude-cli")
    assert row["available"] is False
    assert row["installed"] is True
    assert row["signed_in"] is False


def test_unknown_sign_in_keeps_the_cli_available(monkeypatch):
    """Inconclusive is not signed-out — an old CLI without the status
    subcommand must not lose a working provider."""
    monkeypatch.setattr(
        ProviderManager, "_cli_binary_present", staticmethod(lambda b: b == "claude")
    )
    monkeypatch.setattr(ProviderManager, "_cli_signed_in", staticmethod(lambda b: None))
    m = ProviderManager(inherit_cli_logins=True)
    assert m.available("claude-cli") is True
    assert m.inherited_from("anthropic") == "claude-cli"
    assert m.cli_login_status("claude-cli") == {
        "installed": True,
        "signed_in": None,
        "detail": "",
    }


# --- 4. the router's refusal names the remedy ------------------------------------


@pytest.mark.asyncio
async def test_router_refuses_a_signed_out_default_with_the_remedy(tmp_path, monkeypatch):
    from iron_jarvis.platform import build_platform
    from iron_jarvis.providers.adapters.base import LLMMessage, ProviderError

    monkeypatch.setattr(
        ProviderManager, "_cli_binary_present", staticmethod(lambda b: b == "claude")
    )
    monkeypatch.setattr(ProviderManager, "_cli_signed_in", staticmethod(lambda b: False))
    platform = build_platform(str(tmp_path))
    platform.config.default_provider = "claude-cli"
    router = platform.router
    with pytest.raises(ProviderError) as ei:
        await router.complete(
            system="", messages=[LLMMessage(role="user", content="hi")], tools=[]
        )
    text = str(ei.value)
    assert "installed but not signed in" in text
    assert SIGN_IN_FIX["claude"] in text
    assert "isn't connected right now" not in text


@pytest.mark.asyncio
async def test_router_names_the_cli_behind_a_keyless_inherited_provider(tmp_path, monkeypatch):
    """`anthropic` with no key inherits `claude-cli`; when that CLI is signed
    out the refusal must name Claude Code, not say anthropic is unreachable."""
    from iron_jarvis.platform import build_platform
    from iron_jarvis.providers.adapters.base import LLMMessage, ProviderError

    monkeypatch.setattr(
        ProviderManager, "_cli_binary_present", staticmethod(lambda b: b == "claude")
    )
    monkeypatch.setattr(ProviderManager, "_cli_signed_in", staticmethod(lambda b: False))
    platform = build_platform(str(tmp_path))
    platform.config.default_provider = "anthropic"
    with pytest.raises(ProviderError) as ei:
        await platform.router.complete(
            system="", messages=[LLMMessage(role="user", content="hi")], tools=[]
        )
    text = str(ei.value)
    assert "anthropic runs through claude-cli" in text
    assert SIGN_IN_FIX["claude"] in text


# --- 5. surfaces: rescan + doctor -------------------------------------------------


def test_rescan_reports_installed_not_signed_in(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from iron_jarvis.daemon.app import create_app

    monkeypatch.setattr(
        ProviderManager, "_cli_binary_present", staticmethod(lambda b: b == "codex")
    )
    fake = CliAuthProbe(runner=lambda argv: (1, "Not logged in", ""), which=lambda b: "/x/" + b)
    monkeypatch.setattr(cli_auth, "DEFAULT_PROBE", fake)
    client = TestClient(create_app(str(tmp_path)))
    rows = client.post("/providers/rescan").json()["detected"]
    codex = [r for r in rows if r["provider"] == "codex-cli"]
    assert len(codex) == 1
    assert codex[0]["available"] is False
    assert "codex login" in codex[0]["detail"]
    assert not [r for r in rows if r["provider"] == "claude-cli"]  # not installed → no row


def test_doctor_names_a_signed_out_cli(tmp_path, monkeypatch):
    from iron_jarvis.onboarding.doctor import runtime_checks
    from iron_jarvis.platform import build_platform

    monkeypatch.setattr(
        ProviderManager, "_cli_binary_present", staticmethod(lambda b: b == "claude")
    )
    monkeypatch.setattr(ProviderManager, "_cli_signed_in", staticmethod(lambda b: False))
    platform = build_platform(str(tmp_path))
    checks = {c["name"]: c for c in runtime_checks(platform)}
    c = checks["claude-cli_login"]
    assert c["ok"] is False
    assert "NOT signed in" in c["detail"]
    assert c["fix"] == SIGN_IN_FIX["claude"]
    assert "codex-cli_login" not in checks  # not installed → silent
