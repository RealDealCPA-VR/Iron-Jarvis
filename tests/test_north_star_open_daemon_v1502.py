"""North-star 2.15 (v1.502.0): Iron Jarvis never listens on the network with no token.

``daemon/auth.TokenAuthMiddleware`` leaves every route open when no token is
configured (local development), and ``ironjarvis serve --host <address>``
started anyway: a fully open Iron Jarvis — every chat, file and tool — for
anyone on that network. Now ``serve`` refuses a host other than this PC's own
loopback with no token, in plain words that say how to set one; and the
middleware, with no token, refuses a request that arrives from another
computer's address (the second lock, for any other way the app is served).

The packaged app always has a token, so neither lock changes anything there.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from iron_jarvis.daemon import cli as cli_mod


class _PassedTheGuard(Exception):
    """Raised by the port preflight stand-in: serve got past the token check."""


@pytest.fixture()
def no_bind(monkeypatch):
    def _probe(*_a, **_k):
        raise _PassedTheGuard()

    monkeypatch.setattr(cli_mod, "_port_in_use", _probe)


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.20", "::", "spark1"])
def test_serve_on_the_network_with_no_token_refuses_in_plain_words(host, monkeypatch, tmp_path, no_bind):
    monkeypatch.delenv("IRONJARVIS_TOKEN", raising=False)
    result = CliRunner().invoke(
        cli_mod.app, ["serve", "--host", host, "--port", "59871", "--root", str(tmp_path)]
    )
    assert result.exit_code == 2, result.output
    assert not isinstance(result.exception, _PassedTheGuard)
    out = " ".join(result.output.split())
    assert "IRONJARVIS_TOKEN" in out and "token" in out.lower()
    assert "127.0.0.1" in out, "the words say the other way: stay on this PC"


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1", "127.0.0.5"])
def test_serve_on_this_pc_needs_no_token(host, monkeypatch, tmp_path, no_bind):
    monkeypatch.delenv("IRONJARVIS_TOKEN", raising=False)
    result = CliRunner().invoke(
        cli_mod.app, ["serve", "--host", host, "--port", "59871", "--root", str(tmp_path)]
    )
    assert isinstance(result.exception, _PassedTheGuard), result.output


def test_serve_on_the_network_with_a_token_starts_as_before(monkeypatch, tmp_path, no_bind):
    """The packaged app's case ("Let other computers reach this PC" starts
    serve with --host 0.0.0.0 and always has a token)."""
    monkeypatch.setenv("IRONJARVIS_TOKEN", "not-a-real-token-just-a-test-value")
    result = CliRunner().invoke(
        cli_mod.app, ["serve", "--host", "0.0.0.0", "--port", "59871", "--root", str(tmp_path)]
    )
    assert isinstance(result.exception, _PassedTheGuard), result.output


@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.delenv("IRONJARVIS_TOKEN", raising=False)
    from iron_jarvis.daemon.app import create_app

    return create_app(str(tmp_path))


def test_with_no_token_a_request_from_another_computer_is_refused(app):
    far = TestClient(app, client=("192.168.1.30", 50000))
    r = far.get("/sessions")
    assert r.status_code == 401, r.text
    assert "token" in r.json()["detail"].lower()


@pytest.mark.parametrize("client", [("127.0.0.1", 50000), ("::1", 50000), ("testclient", 50000)])
def test_with_no_token_this_pc_is_still_let_in(app, client):
    r = TestClient(app, client=client).get("/sessions")
    assert r.status_code == 200, r.text


def test_with_a_token_another_computer_still_gets_in_with_it(app, monkeypatch):
    monkeypatch.setenv("IRONJARVIS_TOKEN", "not-a-real-token-just-a-test-value")
    far = TestClient(app, client=("192.168.1.30", 50000))
    assert far.get("/sessions").status_code == 401
    ok = far.get("/sessions", headers={"Authorization": "Bearer not-a-real-token-just-a-test-value"})
    assert ok.status_code == 200, ok.text
