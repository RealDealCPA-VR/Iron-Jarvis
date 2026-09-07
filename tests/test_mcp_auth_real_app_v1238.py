"""``/mcp`` and the pane's child environment, DRIVEN THROUGH THE REAL APP (v1.238.0).

Ship 4's two S1 credential defects were both invisible to 89 green tests, and
both for the same reason: every existing pin looked at a component instead of at
the product.

* ``tests/test_mcp_server_v1238.py`` mounts ``/mcp`` on a bare ``FastAPI()``
  with **no middleware stack**. So it could never see that
  ``TokenAuthMiddleware`` refused a valid pane token with
  ``{"detail": "missing or invalid token"}`` BEFORE the route ran — on every
  packaged install, because ``desktop/main.js`` always spawns the daemon with
  ``IRONJARVIS_TOKEN``. Measured on the real ``create_app`` before the fix:
  pane token -> 401, install bearer -> 401, no token -> 401. There was no
  credential that could reach ``/mcp`` at all, and the harness's report ("not
  authorised") sent the user to check a token that was correct.
* The pane-environment tests asked the manager for its dict. So none of them saw
  that the child inherited ``IRONJARVIS_TOKEN`` — the install bearer, full
  authority over an RCE-by-design daemon — verbatim beside the pane-scoped
  token, which made the capability scope decorative against exactly the actor it
  exists to contain.

So every test here either builds the app through ``create_app`` **with
IRONJARVIS_TOKEN set** (the shipped configuration, not the developer's), or
reads the environment the terminal BACKEND was handed. Nothing here asserts a
dict the code under test also wrote.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.browser.panetokens import (
    INSTALL_BEARER_MESSAGE,
    INSTALL_TOKEN_ENV,
    MCP_TOKEN_ENV,
    MISSING_TOKEN_MESSAGE,
    REASON_INSTALL_BEARER,
    REASON_NO_TOKEN,
    REASON_UNKNOWN_TOKEN,
)
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes.mcpserver import NO_STREAM_MESSAGE
from iron_jarvis.mcp.client import PROTOCOL_VERSION as CLIENT_PROTOCOL_VERSION
from iron_jarvis.mcpserver.session import SESSION_HEADER
from iron_jarvis.terminals import TerminalManager
from iron_jarvis.terminals.backend import FakeBackend

#: The install bearer this app is configured with. A real one is opaque; the
#: value only has to be distinguishable from the pane token in an assertion.
INSTALL_TOKEN = "install-bearer-for-the-whole-app"

#: ``TokenAuthMiddleware``'s own refusal (``daemon/auth.py``). Spelled here so a
#: test can tell WHICH layer said no: the middleware's sentence names no
#: remedy, which is precisely why a harness hitting it was unfixable.
MIDDLEWARE_REFUSAL = "missing or invalid token"

ACCEPT = "application/json, text/event-stream"


class _FakePane:
    """A pane as ``PaneTokenStore.resolve`` reads one.

    A real ``TerminalManager.create`` here would spawn a shell for a test about
    HTTP authorisation; the store's documented seam is ``pane_lookup``, so the
    app's own store is pointed at this instead. The capability grant is read
    LIVE off this object, so it is the same code path a real pane takes.
    """

    def __init__(self, pane_id: str) -> None:
        self.id = pane_id
        self.capabilities = {"browser": True}
        self.cwd = ""
        self.alive = True


class RealApp:
    """``create_app`` with auth ON, plus a pane token minted by ITS OWN store."""

    PANE_ID = "term_real_app_pane"

    def __init__(self, root: str) -> None:
        self.app = create_app(root)
        self.platform = self.app.state.platform
        self.store = self.platform.pane_tokens
        self.pane = _FakePane(self.PANE_ID)
        self.store.pane_lookup = lambda pane_id: (
            self.pane if pane_id == self.PANE_ID else None
        )
        self.token = self.store.mint(self.PANE_ID, {"browser": True})
        self.client = TestClient(self.app)

    def headers(self, token: str | None) -> dict[str, str]:
        out = {"Accept": ACCEPT}
        if token:
            out["Authorization"] = f"Bearer {token}"
        return out

    def initialize(self, *, token: str | None = ""):
        """POST a real ``initialize``; ``token=""`` means this pane's token."""
        return self.client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": CLIENT_PROTOCOL_VERSION,
                    "clientInfo": {"name": "pytest", "version": "1"},
                },
            },
            headers=self.headers(self.token if token == "" else token),
        )


@pytest.fixture()
def real_app(tmp_path, monkeypatch) -> RealApp:
    """The SHIPPED configuration: a daemon with an install bearer configured.

    The whole defect lived in this one line of setup. With ``IRONJARVIS_TOKEN``
    unset — a developer's shell, and the coordinator's smoke test — ``/mcp``
    behaves correctly and nothing looks wrong.
    """
    monkeypatch.setenv(INSTALL_TOKEN_ENV, INSTALL_TOKEN)
    return RealApp(str(tmp_path))


# --------------------------------------------------------------------------- #
# The credential the route takes must REACH the route
# --------------------------------------------------------------------------- #
def test_a_valid_pane_token_reaches_mcp_through_the_whole_middleware_stack(real_app):
    """THE PIN THIS SHIP WAS MISSING: a 401 here means the feature does not exist.

    Not a shape assertion — the handshake has to actually complete, so the
    session id has to come back on the RESPONSE HEADER, where every MCP client
    (including this repository's own ``HttpTransport``) reads it.
    """
    response = real_app.initialize()
    assert response.status_code == 200, (
        "a valid pane token was refused before /mcp ran — TokenAuthMiddleware "
        f"covers /mcp again: {response.text}"
    )
    assert MIDDLEWARE_REFUSAL not in response.text
    assert response.json()["result"]["serverInfo"]["name"]
    assert response.headers.get(SESSION_HEADER)


def test_the_pane_token_also_works_as_a_query_parameter(real_app):
    """A harness configured with a URL and no header control uses ``?token=``.

    The route reads both forms; the middleware read both too, and refused both.
    """
    response = real_app.client.post(
        f"/mcp?token={real_app.token}",
        json={
            "jsonrpc": "2.0",
            "id": 7,
            "method": "initialize",
            "params": {
                "protocolVersion": CLIENT_PROTOCOL_VERSION,
                "clientInfo": {"name": "pytest", "version": "1"},
            },
        },
        headers={"Accept": ACCEPT},
    )
    assert response.status_code == 200, response.text


def test_get_and_delete_reach_the_route_too(real_app):
    """The exemption is per-PATH, so all three verbs land on ``authorize_mcp``.

    ``GET`` answers its own 405 (this server pushes nothing) and ``DELETE``
    answers 200 — neither may be the middleware's 401, or a harness tidying up
    after itself is told its credential is wrong.
    """
    get = real_app.client.get("/mcp", headers=real_app.headers(real_app.token))
    assert get.status_code == 405, get.text
    assert NO_STREAM_MESSAGE in get.text

    delete = real_app.client.delete("/mcp", headers=real_app.headers(real_app.token))
    assert delete.status_code == 200, delete.text
    assert delete.json() == {"closed": False}


# --------------------------------------------------------------------------- #
# ...and every WRONG credential is still refused, by the route's own check
# --------------------------------------------------------------------------- #
def test_the_install_bearer_is_refused_by_the_route_not_admitted_by_the_exemption(
    real_app,
):
    """The exemption must not turn ``/mcp`` into a door the install token opens.

    The refusal is identified by ``reason`` and by the sentence, not by the
    status code: the middleware's 401 and the route's 401 are both 401, and it
    was the middleware's — which names no remedy — that the user used to get.
    """
    response = real_app.initialize(token=INSTALL_TOKEN)
    assert response.status_code == 401
    body = response.json()
    assert body["reason"] == REASON_INSTALL_BEARER
    assert body["detail"] == INSTALL_BEARER_MESSAGE


def test_no_token_gets_the_routes_sentence_which_says_what_to_do(real_app):
    """``missing or invalid token`` sends a harness author to the wrong credential.

    The route's sentence names ``IRONJARVIS_MCP_TOKEN``, which is the one thing
    that makes the 401 actionable.
    """
    response = real_app.initialize(token=None)
    assert response.status_code == 401
    body = response.json()
    assert body["reason"] == REASON_NO_TOKEN
    assert body["detail"] == MISSING_TOKEN_MESSAGE
    assert MIDDLEWARE_REFUSAL not in response.text


def test_a_bogus_pane_token_is_refused_by_the_pane_store(real_app):
    """The post-restart case: a token no store knows has to REACH the store to
    be refused with the relaunch instruction, instead of being swallowed."""
    response = real_app.initialize(token="not-a-pane-token-at-all")
    assert response.status_code == 401
    assert response.json()["reason"] == REASON_UNKNOWN_TOKEN
    assert "relaunch the harness" in response.json()["detail"]


# --------------------------------------------------------------------------- #
# The exemption is EXACT, and that is a security property
# --------------------------------------------------------------------------- #
def test_the_mcp_admin_routes_still_require_the_install_token(real_app):
    """``POST /mcp/servers`` registers a command the daemon will EXECUTE.

    A ``startswith("/mcp")`` exemption would hand it (and ``PATCH
    /mcp/settings``) to any unauthenticated local caller. This asserts the
    MIDDLEWARE's own refusal, so a 422 or a 200 both fail it.
    """
    for method, path in (
        ("post", "/mcp/servers"),
        ("patch", "/mcp/settings"),
        ("post", "/mcp/servers/x/test"),
    ):
        response = getattr(real_app.client, method)(path, json={})
        assert response.status_code == 401, f"{path}: {response.status_code}"
        assert response.json()["detail"] == MIDDLEWARE_REFUSAL, path


def test_a_path_that_merely_starts_with_mcp_is_not_exempt(real_app):
    """``/mcp-anything`` must be refused by the middleware, not routed.

    With an exact match this is a 401 before routing; with a prefix match the
    request reaches the router and comes back 404 — which is the signature of
    an exemption that has been widened.
    """
    response = real_app.client.get("/mcp-anything")
    assert response.status_code == 401, response.text
    assert response.json()["detail"] == MIDDLEWARE_REFUSAL


def test_auth_still_covers_the_rest_of_the_app(real_app):
    """A guard against a blunt fix: only ``/mcp`` may have moved."""
    assert real_app.client.get("/sessions").status_code == 401
    assert real_app.client.get("/terminals").status_code == 401
    # ...and the pane token is NOT an app-wide credential.
    assert (
        real_app.client.get(
            "/sessions", headers=real_app.headers(real_app.token)
        ).status_code
        == 401
    )


# --------------------------------------------------------------------------- #
# The credential that reaches everything must not reach a pane's child
# --------------------------------------------------------------------------- #
class _RecordingBackend(FakeBackend):
    """Keeps the environment the pane's shell was actually started with."""

    def __init__(self) -> None:
        super().__init__()
        self.env: dict | None = None

    def start(self, argv, cwd, env, cols, rows) -> None:  # type: ignore[override]
        self.env = dict(env) if env is not None else None
        super().start(argv, cwd, env, cols, rows)


@pytest.fixture()
def daemon_env(monkeypatch):
    """The daemon's own environment on a packaged install, plus the noise a dev
    box adds: a stale pane token from the shell Build was started from."""
    monkeypatch.setenv(INSTALL_TOKEN_ENV, INSTALL_TOKEN)
    monkeypatch.setenv(MCP_TOKEN_ENV, "another-panes-live-capability-token")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "the-users-own-key")
    monkeypatch.setenv("IJ_TEST_MARKER", "ordinary-inherited-value")


def _spawn(manager: TerminalManager, tmp_path, **kw) -> _RecordingBackend:
    backend = _RecordingBackend()
    manager.create(cwd=str(tmp_path), backend=backend, **kw)
    assert backend.env is not None, "the shell must be given an environment"
    return backend


def test_a_harness_pane_gets_the_scoped_token_and_not_the_install_bearer(
    daemon_env, tmp_path
):
    """The capability scope is only worth something if this holds.

    A harness that reads ``$IRONJARVIS_TOKEN`` from its own environment has the
    whole daemon — ``/chat``, ``/terminals``, ``/documents/write``, ``/settings``
    — and the checklist, the 403s and the pane token are all theatre.
    """
    manager = TerminalManager()
    backend = _spawn(manager, tmp_path, recipe="claude", capabilities={"browser": True})

    assert INSTALL_TOKEN_ENV not in backend.env, (
        "the install bearer reached a harness pane's process — the pane "
        "capability scope is decorative while this is true"
    )
    assert INSTALL_TOKEN not in "\n".join(f"{k}={v}" for k, v in backend.env.items())
    # The SCOPED credential must still be there, or the harness cannot call /mcp
    # at all and this "fix" would have shipped by deleting the feature.
    minted = backend.env.get(MCP_TOKEN_ENV, "")
    assert minted and minted != "another-panes-live-capability-token"
    grant = manager.pane_tokens.resolve(minted)
    assert grant is not None and grant.pane_id == backend.env["IRONJARVIS_PANE_ID"]


def test_an_ordinary_pane_is_stripped_too(daemon_env, tmp_path):
    """The user launches harnesses by TYPING into a pane, so the boundary cannot
    depend on how the pane was created.

    A pane without a recipe gets no capability token at all — and must not get
    the install bearer, nor the stale one belonging to another pane, as a
    consolation prize.
    """
    backend = _spawn(TerminalManager(), tmp_path)
    assert INSTALL_TOKEN_ENV not in backend.env
    assert MCP_TOKEN_ENV not in backend.env, (
        "a pane with no capabilities inherited another pane's live token"
    )


def test_the_rest_of_the_environment_is_untouched(daemon_env, tmp_path):
    """The strip must stay a strip.

    The backends REPLACE the child environment, so a base that lost its
    ordinary variables spawns a shell with no PATH — and the user's OWN
    provider keys are theirs, not the daemon's: removing them would break the
    user's tooling in a pane that is meant to be their shell.
    """
    backend = _spawn(TerminalManager(), tmp_path)
    assert backend.env.get("IJ_TEST_MARKER") == "ordinary-inherited-value"
    assert backend.env.get("ANTHROPIC_API_KEY") == "the-users-own-key"
    assert backend.env.get("PATH")
    assert backend.env["IRONJARVIS_BUILD"] == "1"


def test_a_caller_supplied_environment_is_stripped_as_well(daemon_env, tmp_path):
    """The credential is the same credential whichever dict it travelled in."""
    backend = _RecordingBackend()
    TerminalManager().create(
        cwd=str(tmp_path),
        backend=backend,
        env={INSTALL_TOKEN_ENV: INSTALL_TOKEN, "PATH": "/usr/bin"},
    )
    assert INSTALL_TOKEN_ENV not in backend.env
    assert backend.env["PATH"] == "/usr/bin"


def test_the_stripped_names_are_the_real_ones(daemon_env):
    """``manager.py`` spells both names as literals (it must not import the
    browser package eagerly). Drift does not raise — it silently stops the
    strip from ever matching — so it is pinned against the definitions."""
    from iron_jarvis.terminals.manager import _DAEMON_ONLY_ENV

    assert INSTALL_TOKEN_ENV in _DAEMON_ONLY_ENV
    assert MCP_TOKEN_ENV in _DAEMON_ONLY_ENV
