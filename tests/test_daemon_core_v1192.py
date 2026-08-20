"""daemon-core defects from the 2026-08-20 deep review (findings 16, 17, 37 + 13-residue).

Four independent defects that happen to share the daemon's core modules:

* **16** — ``_build_workflow`` computed the mock-failover adapter and then
  called the MOCK anyway, so an install with ``default_provider = "mock"`` and a
  real provider connected got a 422 "try rephrasing".
* **17** — ``DaemonClient`` sent no bearer token and never raised on 4xx, so
  ``ironjarvis cancel`` printed ``{'detail': 'missing or invalid token'}`` and
  exited 0 against the packaged (token-protected) daemon. The fix's own second
  round: the discovered token is scoped to LOOPBACK targets, so ``--url
  https://elsewhere`` can no longer exfiltrate this install's bearer key.
* **37** — ``hmac.compare_digest`` raises ``TypeError`` on a non-ASCII ``str``,
  so a client-supplied non-ASCII token produced a CORS-less 500 ("daemon
  offline") instead of a 401 / 1008 policy close.
* **13 (residue)** — the one-shot utilities call the adapter directly and then
  walk their own failover ladder, so a LOCAL endpoint that never answered —
  down, cold-loading past the 60s read timeout, or dropped mid-request — still
  shipped the payload to a cloud provider, outside the router's v1.162.0 guard.
"""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from iron_jarvis.daemon import app as _app
from iron_jarvis.daemon import client as _client_mod
from iron_jarvis.daemon.app import _ws_token_ok, create_app
from iron_jarvis.daemon.auth import token_matches
from iron_jarvis.daemon.cli import app as cli_app
from iron_jarvis.daemon.client import DaemonClient, DaemonError, daemon_token
from iron_jarvis.providers.adapters.base import LLMResponse
from iron_jarvis.providers.adapters.mock import MockLLMAdapter

_WF = {
    "name": "Daily Report",
    "description": "Compile a daily report",
    "steps": [{"name": "Gather", "agent": "researcher", "task": "collect data"}],
}


class _Recording:
    """A real-provider stand-in that counts how many times it was asked."""

    def __init__(self, provider: str, text: str) -> None:
        self.provider = provider
        self.model = f"{provider}-1"
        self.text = text
        self.calls = 0

    async def complete(self, *, system, messages, tools):
        self.calls += 1
        return LLMResponse(text=self.text)


class _Unreachable:
    """A configured-but-DOWN local endpoint: the connect never lands."""

    def __init__(self, provider: str = "ollama") -> None:
        self.provider = provider
        self.model = "llama3"
        self.calls = 0

    async def complete(self, *, system, messages, tools):
        self.calls += 1
        # The real shape: httpx words a refused local port this way, and
        # is_transient_error classifies a ConnectError TRANSIENT by type — which
        # is exactly why the failover ladder used to run.
        raise httpx.ConnectError("All connection attempts failed")


class _FailingLocal:
    """A configured LOCAL endpoint whose failure is TRANSPORT-shaped.

    Parameterised because "never reached" is only ONE of the three kinds the
    router refuses on (router.local_failure_kind), and it is the LESS common
    one: a single OpenAIAdapter with a 60s timeout serves both the ``ollama``
    and ``custom`` slots, so a box that is UP but cold-loading a 30B/70B raises
    ``httpx.ReadTimeout`` — transient BY TYPE, not unreachable.
    """

    def __init__(self, exc: Exception, provider: str = "ollama") -> None:
        self.provider = provider
        self.model = "llama3"
        self.calls = 0
        self._exc = exc

    async def complete(self, *, system, messages, tools):
        self.calls += 1
        raise self._exc


# --------------------------------------------------------------------------- #
# Finding 16 — the workflow builder must ADOPT the failover adapter.
# --------------------------------------------------------------------------- #
def test_generate_uses_the_failover_adapter_instead_of_the_mock(tmp_path):
    """default_provider="mock" + a real provider connected => the REAL provider
    answers. Before the fix the tuple was bound to ``_alt_*`` and discarded, the
    mock's non-JSON reply was parsed, and the endpoint 422'd "try rephrasing"."""
    app = create_app(str(tmp_path))
    platform = app.state.platform
    assert platform.config.default_provider == "mock"  # fresh-install default
    real = _Recording("claude-cli", json.dumps(_WF))
    mock = MockLLMAdapter()

    platform.providers.available = lambda name, *a, **k: name == "claude-cli"
    platform.providers.get = (
        lambda name="", model=None, *a, **k: real if name == "claude-cli" else mock
    )

    r = TestClient(app).post("/workflows/generate", json={"description": "a report"})
    assert r.status_code == 200, r.text
    assert real.calls == 1  # the mock was never asked
    assert r.json()["name"] == "daily-report"


def test_generate_still_refuses_when_there_is_no_real_provider(tmp_path):
    """The honest offline hint (v1.135.0) survives the reassignment."""
    app = create_app(str(tmp_path))
    app.state.platform.providers.available = lambda *a, **k: False
    r = TestClient(app).post("/workflows/generate", json={"description": "x"})
    assert r.status_code == 400
    assert "connect a model" in r.json()["detail"].lower()


# --------------------------------------------------------------------------- #
# Finding 13 residue — a never-reached LOCAL primary REFUSES, it never fails over.
# --------------------------------------------------------------------------- #
@pytest.fixture()
def _no_backoff(monkeypatch):
    """Keep the real retry/classification code, drop its 1.5s+3.75s backoff."""
    real = _app._complete_with_retry

    async def _once(adapter, **kw):
        kw.pop("attempts", None)
        return await real(adapter, attempts=1, **kw)

    monkeypatch.setattr(_app, "_complete_with_retry", _once)


def test_one_shot_refuses_cloud_failover_for_a_down_local_endpoint(
    tmp_path, _no_backoff
):
    """A down Ollama + a connected cloud API: the conversation must NOT leave the
    machine. Before the fix ``_one_shot_complete`` classified ConnectError as
    transient and handed the payload to the first cloud candidate."""
    app = create_app(str(tmp_path))
    platform = app.state.platform
    platform.config.default_provider = "ollama"
    platform.config.default_model = "llama3"
    down = _Unreachable("ollama")
    cloud = _Recording("anthropic", json.dumps(_WF))

    platform.providers.available = lambda name, *a, **k: name in {"ollama", "anthropic"}
    platform.providers.get = (
        lambda name="", model=None, *a, **k: cloud if name == "anthropic" else down
    )

    published: list[tuple] = []
    bus = platform.router.event_bus
    real_publish = bus.publish

    async def _spy(event_type, payload, **kw):
        published.append((event_type, payload))
        return await real_publish(event_type, payload, **kw)

    bus.publish = _spy

    r = TestClient(app).post("/workflows/generate", json={"description": "x"})

    assert cloud.calls == 0, "the down LOCAL endpoint's turn reached a cloud provider"
    assert down.calls == 1
    assert r.status_code == 502, r.text
    detail = r.json()["detail"]
    # Same wording as the router's refusal (ModelRouter._unavailable_error).
    assert "ollama isn't connected right now" in detail
    assert "stand-in answer" in detail
    # ...and the same banner event, so the dashboard points at Connections.
    downgraded = [p for t, p in published if p.get("used") == "none"]
    assert downgraded and downgraded[0]["requested"] == "ollama"


@pytest.mark.parametrize(
    ("exc", "lead"),
    [
        # THE MORE COMMON LOCAL FAILURE (router.py local_failure_kind): the box
        # answered the socket and then blew the adapter's 60s read timeout
        # cold-loading a model. Transient by type, NOT unreachable — a guard
        # keyed on is_unreachable_error lets this one reach the cloud.
        (httpx.ReadTimeout("timed out"), "ollama didn't respond in time"),
        # ...and the connection that broke mid-request.
        (
            httpx.RemoteProtocolError("server disconnected without sending a response"),
            "the connection to ollama dropped mid-request",
        ),
    ],
    ids=["read-timeout", "interrupted"],
)
def test_one_shot_refuses_for_every_transport_shaped_local_failure(
    tmp_path, _no_backoff, exc, lead
):
    """A LOCAL primary that is UP-but-slow, or that drops mid-request, must
    refuse exactly like a down one — and the refusal must say WHICH happened.

    ``is_unreachable_error`` covers only connect-shaped errors, so both of these
    used to fall through ``_is_transient_provider_error`` into
    ``_failover_candidates`` and ship the payload to the first connected cloud
    provider. The wording assertion pins the second half: reporting a timed-out
    endpoint as "isn't connected right now" is the fabrication ``kind=`` exists
    to prevent (it sends the user to restart a server that never went down)."""
    app = create_app(str(tmp_path))
    platform = app.state.platform
    platform.config.default_provider = "ollama"
    platform.config.default_model = "llama3"
    slow = _FailingLocal(exc, "ollama")
    cloud = _Recording("anthropic", json.dumps(_WF))

    platform.providers.available = lambda name, *a, **k: name in {"ollama", "anthropic"}
    platform.providers.get = (
        lambda name="", model=None, *a, **k: cloud if name == "anthropic" else slow
    )

    published: list[tuple] = []
    bus = platform.router.event_bus
    real_publish = bus.publish

    async def _spy(event_type, payload, **kw):
        published.append((event_type, payload))
        return await real_publish(event_type, payload, **kw)

    bus.publish = _spy

    r = TestClient(app).post("/workflows/generate", json={"description": "x"})

    assert cloud.calls == 0, "the LOCAL endpoint's turn reached a cloud provider"
    assert slow.calls == 1
    assert r.status_code == 502, r.text
    detail = r.json()["detail"]
    assert lead in detail, detail
    # It must NOT claim the endpoint is disconnected — it answered the socket.
    assert "isn't connected right now" not in detail
    downgraded = [p for t, p in published if p.get("used") == "none"]
    assert downgraded and downgraded[0]["requested"] == "ollama"
    # The banner is honest about the same thing.
    assert "not connected" not in downgraded[0]["reason"]


def test_one_shot_still_fails_over_for_a_cloud_primary(tmp_path, _no_backoff):
    """The guard is NARROW: cloud->cloud arbitrage on a transient failure is
    untouched (only a LOCAL, never-reached primary refuses)."""
    app = create_app(str(tmp_path))
    platform = app.state.platform
    platform.config.default_provider = "anthropic"
    platform.config.default_model = "claude-x"
    down = _Unreachable("anthropic")  # a CLOUD primary that timed out
    alt = _Recording("openai", json.dumps(_WF))

    platform.providers.available = lambda name, *a, **k: name in {"anthropic", "openai"}
    platform.providers.get = (
        lambda name="", model=None, *a, **k: alt if name == "openai" else down
    )

    r = TestClient(app).post("/workflows/generate", json={"description": "x"})
    assert r.status_code == 200, r.text
    assert alt.calls == 1


# --------------------------------------------------------------------------- #
# Finding 37 — a non-ASCII candidate token is a 401 / 1008, never a crash.
# --------------------------------------------------------------------------- #
def test_token_matches_never_raises_on_non_ascii():
    assert token_matches("café", "secret") is False
    assert token_matches("secret", "secret") is True
    assert token_matches(None, "secret") is False
    # A non-ASCII CONFIGURED token still matches itself.
    assert token_matches("café", "café") is True


@pytest.mark.parametrize("bad", ["café", "тест", "🔥"])
def test_non_ascii_bearer_header_is_401_not_500(tmp_path, monkeypatch, bad):
    monkeypatch.setenv("IRONJARVIS_TOKEN", "goodtoken")
    client = TestClient(create_app(str(tmp_path)))
    # Sent as raw BYTES, which is all a curl/scanner ever sends; Starlette then
    # latin-1 decodes them into the non-ASCII str compare_digest choked on.
    r = client.get(
        "/sessions", headers={"Authorization": f"Bearer {bad}".encode("utf-8")}
    )
    assert r.status_code == 401, r.text
    assert r.json()["detail"] == "missing or invalid token"


def test_non_ascii_query_token_is_401_not_500(tmp_path, monkeypatch):
    monkeypatch.setenv("IRONJARVIS_TOKEN", "goodtoken")
    client = TestClient(create_app(str(tmp_path)))
    r = client.get("/sessions", params={"token": "café"})
    assert r.status_code == 401, r.text


def test_valid_token_still_passes(tmp_path, monkeypatch):
    monkeypatch.setenv("IRONJARVIS_TOKEN", "goodtoken")
    client = TestClient(create_app(str(tmp_path)))
    assert client.get(
        "/sessions", headers={"Authorization": "Bearer goodtoken"}
    ).status_code == 200


class _FakeWS:
    def __init__(self, token: str) -> None:
        self.query_params = {"token": token}


def test_ws_token_check_rejects_non_ascii_without_raising(monkeypatch):
    """/events, /terminals/{id}/ws and /voice/stream close 1008 on a False; the
    unguarded compare_digest raised INSIDE the handshake instead (FastAPI's
    Exception handler is HTTP-only, so nothing turned it into a clean close)."""
    monkeypatch.setenv("IRONJARVIS_TOKEN", "goodtoken")
    assert _ws_token_ok(_FakeWS("café")) is False
    assert _ws_token_ok(_FakeWS("goodtoken")) is True


def test_events_ws_policy_closes_on_a_non_ascii_token(tmp_path, monkeypatch):
    from starlette.websockets import WebSocketDisconnect

    monkeypatch.setenv("IRONJARVIS_TOKEN", "goodtoken")
    client = TestClient(create_app(str(tmp_path)))
    with pytest.raises(WebSocketDisconnect) as err:  # not a TypeError
        with client.websocket_connect("/events?token=caf%C3%A9"):
            pass
    assert err.value.code == 1008


# --------------------------------------------------------------------------- #
# Finding 17 — DaemonClient carries the token and FAILS LOUDLY on a refusal.
# --------------------------------------------------------------------------- #
def test_daemon_token_prefers_env_then_the_packaged_token_file(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.delenv("IRONJARVIS_TOKEN", raising=False)
    assert daemon_token() == ""

    dest = tmp_path / "Iron Jarvis"
    dest.mkdir()
    (dest / "token.txt").write_text("filetoken\n", encoding="utf-8")
    assert daemon_token() == "filetoken"

    monkeypatch.setenv("IRONJARVIS_TOKEN", "envtoken")
    assert daemon_token() == "envtoken"


def test_discovered_token_is_scoped_to_loopback_targets(tmp_path, monkeypatch):
    """The credential leak the finding-17 fix itself introduced.

    Auto-discovery reads THIS install's ``token.txt`` — the key to a daemon that
    runs shell and spawns PTYs. Attaching it to whatever host ``--url`` names
    meant ``ironjarvis cancel X --url https://elsewhere`` silently handed it to a
    stranger. Remote deployments stay supported: they just have to say --token.
    """
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.delenv("IRONJARVIS_TOKEN", raising=False)
    dest = tmp_path / "Iron Jarvis"
    dest.mkdir()
    (dest / "token.txt").write_text("installtoken\n", encoding="utf-8")
    assert daemon_token() == "installtoken"  # discovery itself still works

    # A host the user typed after --url gets NOTHING.
    remote = DaemonClient("https://example.invalid")
    assert remote.token == ""
    assert "Authorization" not in remote._headers()

    # The local daemon — the only thing that token belongs to — gets it.
    local = DaemonClient("http://127.0.0.1:8787")
    assert local._headers() == {"Authorization": "Bearer installtoken"}

    # ...and an explicitly-supplied token still reaches a remote deployment,
    # so `--token` keeps working (auth.py supports tailnet/public installs).
    explicit = DaemonClient("https://example.invalid", token="explicit")
    assert explicit._headers() == {"Authorization": "Bearer explicit"}


@pytest.mark.parametrize(
    ("url", "loopback"),
    [
        ("http://127.0.0.1:8787", True),
        ("http://localhost:8787", True),
        ("http://[::1]:8787", True),  # hostname strips the brackets AND the port
        ("http://LOCALHOST:8787", True),
        ("https://example.invalid", False),
        ("https://127.0.0.1.evil.example", False),  # a loopback-looking prefix
        ("http://[::1", False),  # unparsable => fail closed
        ("", False),  # no host proves nothing
    ],
)
def test_loopback_scope_classification(url, loopback):
    assert _client_mod._is_loopback_url(url) is loopback


def test_explicitly_set_env_token_still_reaches_a_remote_daemon(monkeypatch):
    """The env var is an explicit act (it is how a remote deployment is driven
    from a shell); only the silent token.txt lookup is loopback-scoped."""
    monkeypatch.setenv("IRONJARVIS_TOKEN", "envtoken")
    assert DaemonClient("https://example.invalid")._headers() == {
        "Authorization": "Bearer envtoken"
    }


@pytest.fixture()
def _routed(tmp_path, monkeypatch):
    """Point the module-level ``httpx.request`` DaemonClient uses at a real,
    token-protected daemon."""
    monkeypatch.setenv("IRONJARVIS_TOKEN", "goodtoken")
    monkeypatch.delenv("APPDATA", raising=False)
    tc = TestClient(create_app(str(tmp_path)), base_url="http://127.0.0.1:8787")

    def _request(method, url, **kw):
        kw.pop("timeout", None)  # TestClient warns on an explicit timeout
        return tc.request(method, url, **kw)

    monkeypatch.setattr(_client_mod.httpx, "request", _request)
    return tc


def test_client_sends_the_discovered_bearer_token(_routed):
    # IRONJARVIS_TOKEN is what the packaged daemon was started with.
    assert DaemonClient().sessions() == {"sessions": []}


def test_client_raises_on_a_401_instead_of_returning_it(_routed):
    """The whole defect: httpx does not raise on 4xx and the 401 body parses, so
    the caller printed the refusal as a RESULT."""
    with pytest.raises(DaemonError) as err:
        DaemonClient(token="").cancel("sess_abc")
    assert err.value.status_code == 401
    assert "missing or invalid token" in str(err.value)
    # It also says how to supply one — there was no way before.
    assert "--token" in str(err.value)


def test_client_raises_on_a_404(_routed):
    with pytest.raises(DaemonError) as err:
        DaemonClient().cancel("sess_does_not_exist")
    assert err.value.status_code == 404


def test_cli_cancel_exits_nonzero_when_the_daemon_refuses(_routed):
    """End-to-end: `ironjarvis cancel` used to print the 401 dict and exit 0
    while the runaway session kept running."""
    result = CliRunner().invoke(cli_app, ["cancel", "sess_abc", "--token", "wrong"])
    assert result.exit_code == 1
    assert "cancel failed" in result.output
