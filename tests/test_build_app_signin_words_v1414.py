"""v1.414.0 (fix): a Build › Code reply that ends on a SIGN-IN failure says the
tool must be signed in again, in that tool's words, with the app's one Sign in
press (``fix_kind: "sign_in"``) — never the generic "Press Retry. If it happens
again, pick another model": Retry cannot fix an expired login.

The text is the REAL one Claude Code printed in the live check (Claude Code
signed out on this PC, 2026-10-30)."""

from __future__ import annotations

from iron_jarvis.hosted import opencode, pi
from iron_jarvis.hosted import words as W
from iron_jarvis.providers.cli_auth import is_sign_in_refusal

LIVE_CLAUDE = "Failed to authenticate: OAuth session expired and could not be refreshed"


def test_the_live_claude_text_is_a_sign_in_failure_with_the_press():
    ev = W.failure_for("claude-code", LIVE_CLAUDE)
    assert ev["fix_kind"] == "sign_in"
    assert ev["title"] == "Claude Code needs to sign in again"
    assert "Sign in" in ev["fix"] and "Retry" not in ev["fix"] and "another model" not in ev["fix"]
    assert ev["technical"] == LIVE_CLAUDE


def test_codex_says_it_in_its_own_name():
    ev = W.failure_for("codex", "Your access token could not be refreshed. Please log out and sign in again.")
    assert ev["fix_kind"] == "sign_in" and ev["title"].startswith("Codex")
    assert "Retry" not in ev["fix"]


def test_pi_and_opencode_read_an_expired_login_as_sign_in_too():
    for mod, name in ((pi, "Pi"), (opencode, "OpenCode")):
        ev = mod.failure_event("OAuth token has expired and could not be refreshed")
        assert ev["fix_kind"] == "sign_in", name
        assert ev["title"].startswith(name) and "Retry" not in ev["fix"]


def test_the_rule_stays_narrow():
    assert is_sign_in_refusal(LIVE_CLAUDE)
    # A model-access refusal and an overload are NOT sign-in problems.
    for text in ("Failed to authenticate. API Error: 403 your organization does not have access to this model",
                 "API Error: 529 overloaded", "The session expired after 60 minutes of idle time"):
        assert not is_sign_in_refusal(text), text
        assert W.failure_for("claude-code", text).get("fix_kind") != "sign_in", text


def test_a_plain_not_signed_in_keeps_its_title():
    ev = W.failure_for("claude-code", "Not logged in · Please run /login")
    assert ev["fix_kind"] == "sign_in" and ev["title"] == "Claude Code isn't signed in"


def test_an_expired_oauth_token_alone_is_a_sign_in_failure():
    # Anthropic's own words for a lapsed login (no "could not be refreshed").
    said = 'API Error: 401 {"type":"error","error":{"type":"authentication_error","message":"OAuth token has expired."}}'
    assert is_sign_in_refusal(said)
    ev = W.failure_for("claude-code", said)
    assert ev["fix_kind"] == "sign_in" and ev["title"] == "Claude Code needs to sign in again"
