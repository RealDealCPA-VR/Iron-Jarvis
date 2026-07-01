"""One-shot loopback listener for OAuth redirects (RFC 8252) — fully offline.

Binds port 0 (ephemeral) so tests never collide with a real :1455 user of the
Codex flow (or each other in parallel CI).
"""

from __future__ import annotations

import urllib.error
import urllib.request

from iron_jarvis.connections.loopback import OAuthLoopbackServer


def _get(url: str) -> tuple[int, str]:
    with urllib.request.urlopen(url, timeout=5) as resp:  # noqa: S310 — loopback only
        return resp.status, resp.read().decode("utf-8")


def test_loopback_captures_code_and_shuts_down():
    seen: dict = {}
    srv = OAuthLoopbackServer(
        port=0,  # ephemeral — the (port, path) contract is proven via bound_port
        path="/auth/callback",
        provider="openai",
        on_code=lambda code, state: seen.update(code=code, state=state),
    )
    srv.start()
    try:
        status, body = _get(
            f"http://127.0.0.1:{srv.bound_port}/auth/callback?code=abc&state=xyz"
        )
        assert status == 200
        assert "Connected to openai" in body
        assert "ironjarvis-oauth" in body  # postMessage payload for the dashboard
        assert seen == {"code": "abc", "state": "xyz"}
    finally:
        srv.stop()  # idempotent — the handler already scheduled the shutdown


def test_loopback_404s_other_paths_and_renders_failure():
    def boom(code, state):
        raise ValueError("unknown or expired OAuth state")

    srv = OAuthLoopbackServer(
        port=0, path="/auth/callback", provider="openai", on_code=boom
    )
    srv.start()
    try:
        # A wrong path must NOT complete or stop the listener.
        try:
            _get(f"http://127.0.0.1:{srv.bound_port}/nope")
            raise AssertionError("expected 404")
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
        # A failing completion renders the failure page (HTML-escaped detail).
        status, body = _get(
            f"http://127.0.0.1:{srv.bound_port}/auth/callback?code=x&state=y"
        )
        assert status == 200
        assert "Connection failed" in body
    finally:
        srv.stop()
