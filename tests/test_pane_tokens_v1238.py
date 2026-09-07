"""The fifth credential, and the wire the harness half speaks (v1.238.0, Ship 4).

Ship 4 lets an EXTERNAL harness — Claude Code, Codex, Pi — drive the same
``BrowserService`` through Jarvis with the same gates. Two things make that safe
rather than merely possible, and this file pins both:

1. **A pane token is not any of the other four credentials** (plan section 7).
   ``/mcp`` takes exactly one thing. The install bearer and a browser pairing
   token are refused by their OWN explicit checks, and each has its own test here
   because a single "not a pane token" refusal would pass those tests while
   telling the user nothing and while quietly depending on the two stores never
   colliding.
2. **The wire agrees with the client this repository already ships.**
   ``mcp/client.py`` has spoken Streamable HTTP against real servers since long
   before this ship, so the server half is asserted BY RUNNING ITS OUTPUT THROUGH
   THAT CLIENT — ``_extract_result`` and ``HttpTransport._parse_body`` — rather
   than by inspecting dict keys. A shape test would stay green through a
   disagreement; a round-trip cannot.

Three traps this file exists to catch, each of which has a cheap wrong answer:

* **A token derived from the pane id.** Pane ids are public (they ride
  ``IRONJARVIS_PANE_ID`` into every child process) and are REUSED — ``restore``
  brings a pane back under its original id — so a derived token would be
  forgeable and would survive a restart into a different shell.
* **Revocation wired to one chokepoint.** There is no pane-closed event, so
  ``kill``, ``purge_dead`` and ``kill_all`` each need the call. Each is driven
  here through a stand-in that wires EXACTLY ONE of them, so a pin cannot be
  satisfied by a sibling's wiring. **The real wiring lives in
  ``terminals/manager.py``, which is another lane's file.**
* **Capabilities read at mint time.** Unticking Browser mid-run has to take
  effect on the very next call, so ``resolve`` reads the live pane.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from iron_jarvis.browser.pairing import PairingStore
from iron_jarvis.browser.panetokens import (
    INSTALL_TOKEN_ENV,
    PANE_TOKEN_EXPIRED_MESSAGE,
    REASON_INSTALL_BEARER,
    REASON_NO_TOKEN,
    REASON_PAIRING_TOKEN,
    REASON_UNKNOWN_TOKEN,
    PaneTokenRefused,
    PaneTokenStore,
    authorize_mcp,
    capability_enabled,
    normalise_capabilities,
)
from iron_jarvis.core.db import open_db
from iron_jarvis.core.ids import utcnow
from iron_jarvis.mcp import client as mcp_client
from iron_jarvis.mcpserver import jsonrpc as J
from iron_jarvis.mcpserver.session import (
    SESSION_HEADER,
    McpSessionRegistry,
    session_id_from_headers,
)
from iron_jarvis.terminals import TerminalManager
from iron_jarvis.terminals.backend import FakeBackend


# --------------------------------------------------------------------------- #
# Doubles.
# --------------------------------------------------------------------------- #
class FakePane:
    """A stand-in for a TerminalSession: the two attributes ``resolve`` reads."""

    def __init__(self, capabilities=None, alive: bool = True) -> None:
        self.capabilities = capabilities if capabilities is not None else {}
        self.alive = alive


class FakeClock:
    """A clock a test moves by hand. No test in this repository asserts a wall clock."""

    def __init__(self) -> None:
        self.now = utcnow()

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now = self.now + timedelta(seconds=seconds)


class FakeHttpResponse:
    """Just enough of an httpx response for ``HttpTransport._parse_body``."""

    def __init__(self, body: str, content_type: str) -> None:
        self.text = body
        self.headers = {"content-type": content_type}

    def json(self):
        return json.loads(self.text)


def _panes(manager: TerminalManager):
    """``pane_lookup`` over a real manager: the shape ``platform.py`` will wire."""
    return manager.get


def _live_ids(manager: TerminalManager) -> list[str]:
    return [s.id for s in manager._sessions.values() if s.alive]


class KillWiredManager(TerminalManager):
    """A manager with the revoke call wired into ``kill()`` ONLY.

    One chokepoint per stand-in on purpose: with all three wired, deleting any one
    call would leave the other two covering for it and every pin would stay green
    — which is exactly the "a pin that cannot fail" defect Ship 3's review found.
    """

    def __init__(self, tokens: PaneTokenStore, **kw) -> None:
        super().__init__(**kw)
        self.tokens = tokens

    def kill(self, id: str) -> bool:
        killed = super().kill(id)
        if killed:
            self.tokens.revoke_pane(id)
        return killed


class PurgeWiredManager(TerminalManager):
    """A manager with the revoke call wired into ``purge_dead()`` ONLY."""

    def __init__(self, tokens: PaneTokenStore, **kw) -> None:
        super().__init__(**kw)
        self.tokens = tokens

    def purge_dead(self) -> int:
        evicted = super().purge_dead()
        self.tokens.sync_panes(_live_ids(self))
        return evicted


class KillAllWiredManager(TerminalManager):
    """A manager with the revoke call wired into ``kill_all()`` ONLY."""

    def __init__(self, tokens: PaneTokenStore, **kw) -> None:
        super().__init__(**kw)
        self.tokens = tokens

    def kill_all(self) -> None:
        super().kill_all()
        self.tokens.revoke_all()


@pytest.fixture
def tokens() -> PaneTokenStore:
    return PaneTokenStore()


# --------------------------------------------------------------------------- #
# The credential itself.
# --------------------------------------------------------------------------- #
def test_token_is_random_and_never_derived_from_the_pane_id(tokens):
    """Two mints for the SAME pane differ, and neither contains the id.

    A pane id is public and reused across restarts. Anything derived from it
    would be forgeable by whatever saw the id, and would keep working after a
    restart handed that id to a different shell process.
    """
    first = tokens.mint("term_abc123", {"browser": True})
    second = tokens.mint("term_abc123", {"browser": True})
    assert first != second
    for token in (first, second):
        assert "term_abc123" not in token
        assert "abc123" not in token
        # 32 bytes base64url-encoded is 43 characters; anything materially
        # shorter would mean the randomness budget was quietly cut.
        assert len(token) >= 43


def test_a_second_mint_replaces_the_panes_token(tokens):
    """Relaunching a harness in a pane must not leave the old credential live."""
    old = tokens.mint("term_1")
    new = tokens.mint("term_1")
    assert tokens.resolve(old) is None
    assert tokens.resolve(new) is not None
    assert tokens.pane_ids() == ["term_1"]


def test_resolve_returns_the_pane_and_its_live_capabilities():
    pane = FakePane({"browser": True, "files": False})
    store = PaneTokenStore(pane_lookup=lambda pid: pane if pid == "term_1" else None)
    grant = store.resolve(store.mint("term_1", {"browser": True}))
    assert grant is not None
    assert grant.pane_id == "term_1"
    assert grant.allows("browser") is True
    assert grant.allows("files") is False
    assert grant.enabled() == ["browser"]
    assert grant.live is True


def test_unticking_a_capability_takes_effect_on_the_next_call_without_a_re_mint():
    """The capability snapshot is AS OF RESOLUTION TIME, not as of mint.

    The mint-time snapshot is the tempting implementation and it is wrong: the
    user unticks Browser in the pane's Capabilities popover, the harness keeps
    the token it already has, and a mint-time grant would keep answering yes for
    the life of the pane.
    """
    pane = FakePane({"browser": True})
    store = PaneTokenStore(pane_lookup=lambda _pid: pane)
    token = store.mint("term_1", {"browser": True})
    assert store.resolve(token).allows("browser") is True

    pane.capabilities = {"browser": False}
    assert store.resolve(token).allows("browser") is False
    # ...and back again, with no new token issued at any point.
    pane.capabilities = {"browser": True}
    assert store.resolve(token).allows("browser") is True


def test_a_token_is_not_reusable_across_panes():
    panes = {"term_a": FakePane({"browser": True}), "term_b": FakePane({"browser": True})}
    store = PaneTokenStore(pane_lookup=panes.get)
    token_a = store.mint("term_a", {"browser": True})
    token_b = store.mint("term_b", {"browser": True})
    assert store.resolve(token_a).pane_id == "term_a"
    assert store.resolve(token_b).pane_id == "term_b"
    assert store.resolve(token_a).pane_id != "term_b"


def test_capabilities_that_round_tripped_through_json_fail_closed():
    """``bool("false")`` is True, and ``capabilities`` lives in ``terminals.json``.

    Read naively, a single restart would turn every disabled capability back on.
    """
    assert capability_enabled(True) is True
    assert capability_enabled(1) is True
    assert capability_enabled("true") is True
    for no in (False, 0, None, "false", "off", "no", "", [], {}, object()):
        assert capability_enabled(no) is False
    assert normalise_capabilities({"browser": "false", "files": True, 7: True}) == {
        "files": True
    }
    assert normalise_capabilities(None) == {}


def test_a_pane_that_is_gone_resolves_to_nothing_and_the_record_is_dropped():
    store = PaneTokenStore(pane_lookup=lambda _pid: None)
    token = store.mint("term_1", {"browser": True})
    assert store.resolve(token) is None
    assert store.pane_ids() == []


def test_a_pane_whose_shell_died_resolves_to_nothing():
    pane = FakePane({"browser": True}, alive=False)
    store = PaneTokenStore(pane_lookup=lambda _pid: pane)
    assert store.resolve(store.mint("term_1", {"browser": True})) is None


def test_a_grant_cannot_be_widened_by_the_caller_that_holds_it():
    pane = FakePane({})
    store = PaneTokenStore(pane_lookup=lambda _pid: pane)
    grant = store.resolve(store.mint("term_1"))
    with pytest.raises(TypeError):
        grant.capabilities["browser"] = True  # type: ignore[index]
    assert grant.allows("browser") is False


def test_rows_carry_no_secret_material(tokens):
    token = tokens.mint("term_1", {"browser": True})
    blob = json.dumps(tokens.rows())
    assert "term_1" in blob
    assert token not in blob
    assert "sha" not in blob


# --------------------------------------------------------------------------- #
# Revocation: three chokepoints, three stand-ins, one call each.
# --------------------------------------------------------------------------- #
def test_kill_revokes_the_panes_token(tokens, tmp_path):
    manager = KillWiredManager(tokens, state_path=tmp_path / "terminals.json")
    pane = manager.create(cwd=str(tmp_path), backend=FakeBackend())
    tokens.mint(pane.id, {"browser": True})
    assert tokens.has_pane(pane.id) is True

    assert manager.kill(pane.id) is True
    assert tokens.has_pane(pane.id) is False


def test_purge_dead_revokes_a_pane_whose_shell_died_on_its_own(tokens, tmp_path):
    """``purge_dead`` is where a shell nobody killed is noticed — so it revokes too."""
    manager = PurgeWiredManager(
        tokens, state_path=tmp_path / "terminals.json", max_dead_retained=0
    )
    pane = manager.create(cwd=str(tmp_path), backend=FakeBackend())
    tokens.mint(pane.id, {"browser": True})

    pane.kill()  # the shell exits by itself; nothing told the manager
    assert tokens.has_pane(pane.id) is True

    manager.purge_dead()
    assert tokens.has_pane(pane.id) is False


def test_kill_all_revokes_every_pane_token(tokens, tmp_path):
    manager = KillAllWiredManager(tokens, state_path=tmp_path / "terminals.json")
    first = manager.create(cwd=str(tmp_path), backend=FakeBackend())
    second = manager.create(cwd=str(tmp_path), backend=FakeBackend())
    tokens.mint(first.id, {"browser": True})
    tokens.mint(second.id, {"browser": True})
    assert len(tokens) == 2

    manager.kill_all()
    assert tokens.pane_ids() == []


# --------------------------------------------------------------------------- #
# Restart recovery. The subtle one.
# --------------------------------------------------------------------------- #
def test_a_restored_pane_has_no_token_and_mcp_says_so(tmp_path):
    """``rehydrate`` brings the pane back under the SAME id with a FRESH shell.

    Re-minting silently for it would hand a live credential to whatever now
    occupies that pane id, so a restored pane holds no token at all and ``/mcp``
    answers 401 with the sentence that tells the user what to do about it.
    """
    state = tmp_path / "terminals.json"
    before = TerminalManager(state_path=state)
    pane = before.create(cwd=str(tmp_path), backend=FakeBackend())
    original_id = pane.id
    old_store = PaneTokenStore(pane_lookup=_panes(before))
    token = old_store.mint(original_id, {"browser": True})
    assert old_store.resolve(token) is not None
    before.snapshot()

    # --- the daemon restarts: new manager, new (empty) token store ---
    after = TerminalManager(state_path=state)
    assert after.rehydrate(backend=FakeBackend()) == 1
    restored = after.get(original_id)
    assert restored is not None and restored.id == original_id  # same id, new shell

    new_store = PaneTokenStore(pane_lookup=_panes(after))
    assert new_store.pane_ids() == []
    assert new_store.resolve(token) is None
    with pytest.raises(PaneTokenRefused) as excinfo:
        authorize_mcp(token, tokens=new_store, install_token="")
    assert excinfo.value.reason == REASON_UNKNOWN_TOKEN
    assert excinfo.value.message == PANE_TOKEN_EXPIRED_MESSAGE
    assert excinfo.value.status_code == 401


# --------------------------------------------------------------------------- #
# THE CROSS-REJECTION. /mcp takes one credential and refuses the others by name.
# --------------------------------------------------------------------------- #
def test_a_valid_pane_token_is_authorised():
    pane = FakePane({"browser": True})
    store = PaneTokenStore(pane_lookup=lambda _pid: pane)
    grant = authorize_mcp(store.mint("term_1"), tokens=store, install_token="install-secret")
    assert grant.pane_id == "term_1"
    assert grant.allows("browser") is True


def test_no_token_is_refused_by_its_own_reason(tokens):
    for empty in ("", "   ", None):
        with pytest.raises(PaneTokenRefused) as excinfo:
            authorize_mcp(empty, tokens=tokens, install_token="install-secret")
        assert excinfo.value.reason == REASON_NO_TOKEN
        assert "IRONJARVIS_MCP_TOKEN" in excinfo.value.message


def test_the_install_bearer_is_refused_by_its_own_explicit_check(tokens):
    """And it is refused EVEN IF the same string is a valid pane token.

    That second half is what makes the check explicit rather than incidental: an
    implementation that consulted the pane store first would authorise this call,
    and a test that only tried a random install token would never notice.
    """
    with pytest.raises(PaneTokenRefused) as excinfo:
        authorize_mcp("install-secret", tokens=tokens, install_token="install-secret")
    assert excinfo.value.reason == REASON_INSTALL_BEARER
    assert "not accepted at /mcp" in excinfo.value.message

    pane = FakePane({"browser": True})
    colliding = PaneTokenStore(pane_lookup=lambda _pid: pane)
    token = colliding.mint("term_1")
    with pytest.raises(PaneTokenRefused) as excinfo:
        authorize_mcp(token, tokens=colliding, install_token=token)
    assert excinfo.value.reason == REASON_INSTALL_BEARER


def test_the_install_token_is_read_from_the_environment_when_not_passed(tokens, monkeypatch):
    monkeypatch.setenv(INSTALL_TOKEN_ENV, "env-install-secret")
    with pytest.raises(PaneTokenRefused) as excinfo:
        authorize_mcp("env-install-secret", tokens=tokens)
    assert excinfo.value.reason == REASON_INSTALL_BEARER


def test_install_token_env_matches_the_daemons():
    """The constant is spelled twice; drift would disarm the refusal silently."""
    from iron_jarvis.daemon import auth as daemon_auth

    assert INSTALL_TOKEN_ENV == daemon_auth._TOKEN_ENV


def test_a_browser_pairing_token_is_refused_by_its_own_explicit_check(tokens, tmp_path):
    """Driven against the REAL PairingStore, not a lambda that says yes."""
    pairing = PairingStore(open_db(tmp_path / "pairing.db"))
    request = pairing.open_request(extension_id="lgihfomaieifpnemakmpadmggjnoojmm")
    pairing_token = pairing.mint(request.request_id)

    with pytest.raises(PaneTokenRefused) as excinfo:
        authorize_mcp(
            pairing_token,
            tokens=tokens,
            pairing_verify=pairing.verify,
            install_token="install-secret",
        )
    assert excinfo.value.reason == REASON_PAIRING_TOKEN
    assert "pairing token is not accepted at /mcp" in excinfo.value.message


def test_a_pane_token_survives_the_pairing_verifier(tmp_path):
    """The pairing check must not refuse a legitimate pane token in passing."""
    pairing = PairingStore(open_db(tmp_path / "pairing.db"))
    pane = FakePane({"browser": True})
    store = PaneTokenStore(pane_lookup=lambda _pid: pane)
    grant = authorize_mcp(
        store.mint("term_1"),
        tokens=store,
        pairing_verify=pairing.verify,
        install_token="install-secret",
    )
    assert grant.pane_id == "term_1"


def test_a_pairing_verifier_that_raises_cannot_become_a_500(tokens):
    def boom(_token):
        raise RuntimeError("database is locked")

    with pytest.raises(PaneTokenRefused) as excinfo:
        authorize_mcp("whatever", tokens=tokens, pairing_verify=boom, install_token="")
    assert excinfo.value.reason == REASON_UNKNOWN_TOKEN


# --------------------------------------------------------------------------- #
# The wire: asserted THROUGH the client this repository already ships.
# --------------------------------------------------------------------------- #
def test_the_server_speaks_the_clients_protocol_version():
    assert J.PROTOCOL_VERSION == mcp_client.PROTOCOL_VERSION == "2024-11-05"


def test_a_result_response_round_trips_through_the_clients_extractor():
    payload = J.result_response(7, {"tools": [{"name": "browser_get_status"}]})
    assert payload["jsonrpc"] == "2.0" and payload["id"] == 7
    assert mcp_client._extract_result(payload) == {
        "tools": [{"name": "browser_get_status"}]
    }


def test_an_empty_result_is_still_a_result_key():
    """``_extract_result`` answers ``{}`` for a MISSING result, so omitting the key
    would make "the tool returned nothing" indistinguishable from a server bug."""
    assert "result" in J.result_response(1, None)


def test_an_error_response_raises_in_the_client_with_our_words():
    payload = J.error_response(3, J.METHOD_NOT_FOUND, "unknown method: tools/spawn")
    with pytest.raises(mcp_client.MCPError) as excinfo:
        mcp_client._extract_result(payload)
    assert "unknown method: tools/spawn" in str(excinfo.value)
    assert str(J.METHOD_NOT_FOUND) in str(excinfo.value)
    assert "data" not in payload["error"]  # not added when there is none


def test_an_sse_body_is_parsed_by_the_clients_own_sse_parser():
    """The client scans ``data:`` lines, so the payload must be ONE line."""
    body = J.sse_body(J.result_response(1, {"ok": True}))
    response = FakeHttpResponse(body, "text/event-stream; charset=utf-8")
    parsed = mcp_client.HttpTransport._parse_body(response)
    assert mcp_client._extract_result(parsed) == {"ok": True}
    data_lines = [ln for ln in body.splitlines() if ln.startswith("data:")]
    assert len(data_lines) == 1


def test_the_clients_own_envelope_parses_as_a_request():
    """``client._envelope`` is what the server will actually receive."""
    request = J.parse_request(mcp_client._envelope(4, "tools/call", {"name": "x"}))
    assert request.method == "tools/call"
    assert request.params == {"name": "x"}
    assert request.id == 4
    assert request.is_notification is False


def test_the_clients_initialized_notification_is_recognised_as_one():
    """``HttpTransport._handshake`` posts it with NO id and expects no response.

    Answering a notification breaks the handshake, so "no id" and "id is null"
    must not be decided by the same test.
    """
    notification = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    assert J.parse_request(notification).is_notification is True
    assert J.parse_request({**notification, "id": None}).is_notification is False


@pytest.mark.parametrize(
    "payload",
    [
        [{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}],  # a batch
        "not an object",
        {"id": 1, "method": "tools/list"},  # no jsonrpc
        {"jsonrpc": "1.0", "id": 1, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 1},  # no method
        {"jsonrpc": "2.0", "id": 1, "method": ""},
    ],
)
def test_a_malformed_request_is_an_invalid_request(payload):
    with pytest.raises(J.JsonRpcError) as excinfo:
        J.parse_request(payload)
    assert excinfo.value.code == J.INVALID_REQUEST


def test_positional_params_are_refused_rather_than_guessed_at():
    with pytest.raises(J.JsonRpcError) as excinfo:
        J.parse_request({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": ["x"]})
    assert excinfo.value.code == J.INVALID_PARAMS


def test_absent_or_null_params_are_an_empty_object():
    assert J.parse_request({"jsonrpc": "2.0", "id": 1, "method": "m"}).params == {}
    assert (
        J.parse_request({"jsonrpc": "2.0", "id": 1, "method": "m", "params": None}).params
        == {}
    )


def test_invalid_json_is_a_parse_error_carrying_its_own_envelope():
    with pytest.raises(J.JsonRpcError) as excinfo:
        J.parse_body(b"{not json")
    assert excinfo.value.code == J.PARSE_ERROR
    envelope = excinfo.value.envelope(None)
    assert envelope["error"]["code"] == J.PARSE_ERROR
    with pytest.raises(mcp_client.MCPError):
        mcp_client._extract_result(envelope)


def test_accept_negotiation_matches_what_the_client_sends():
    sent = "application/json, text/event-stream"
    assert J.accepts_json(sent) is True
    assert J.accepts_sse(sent) is True
    assert J.accepts_sse("application/json") is False
    assert J.accepts_json("*/*") is True
    assert J.accepts_sse("*/*") is True


# --------------------------------------------------------------------------- #
# The Mcp-Session-Id lifecycle.
# --------------------------------------------------------------------------- #
def test_a_session_id_is_random_and_bound_to_the_pane():
    registry = McpSessionRegistry()
    first = registry.open("term_1", client_info={"name": "claude-code"})
    second = registry.open("term_1")
    assert first.id != second.id
    assert "term_1" not in first.id
    assert registry.get(first.id, pane_id="term_1") is first
    assert first.initialized is False


def test_a_session_id_presented_by_another_pane_resolves_to_nothing():
    """Otherwise pane A's harness could continue pane B's conversation, and the
    capability filter would be computed from the wrong pane."""
    registry = McpSessionRegistry()
    session = registry.open("term_a")
    assert registry.get(session.id, pane_id="term_b") is None
    assert registry.close(session.id, pane_id="term_b") is False
    assert registry.get(session.id, pane_id="term_a") is session


def test_initialized_is_recorded_and_close_ends_the_session():
    registry = McpSessionRegistry()
    session = registry.open("term_1")
    assert registry.mark_initialized(session.id, pane_id="term_1") is True
    assert registry.get(session.id, pane_id="term_1").initialized is True
    assert registry.close(session.id, pane_id="term_1") is True
    assert registry.get(session.id, pane_id="term_1") is None
    assert registry.close(session.id, pane_id="term_1") is False


def test_closing_a_pane_closes_its_sessions_and_leaves_its_neighbours_alone():
    registry = McpSessionRegistry()
    mine = registry.open("term_a")
    theirs = registry.open("term_b")
    assert registry.revoke_pane("term_a") == 1
    assert registry.get(mine.id) is None
    assert registry.get(theirs.id) is theirs


def test_an_idle_session_is_pruned_on_the_next_read():
    clock = FakeClock()
    registry = McpSessionRegistry(clock=clock, idle_ttl_s=60)
    session = registry.open("term_1")
    clock.advance(30)
    assert registry.get(session.id, pane_id="term_1") is session  # touched
    clock.advance(59)
    assert registry.get(session.id, pane_id="term_1") is session  # silence < ttl
    clock.advance(61)
    assert registry.get(session.id, pane_id="term_1") is None
    assert len(registry) == 0


def test_a_re_initialising_harness_replaces_its_own_sessions_not_a_neighbours():
    registry = McpSessionRegistry(max_per_pane=2)
    neighbour = registry.open("term_b")
    first = registry.open("term_a")
    registry.open("term_a")
    registry.open("term_a")
    assert registry.get(first.id) is None
    assert registry.get(neighbour.id) is neighbour
    assert len([r for r in registry.rows() if r["pane_id"] == "term_a"]) == 2


def test_the_session_header_is_read_case_insensitively():
    """The client reads ``mcp-session-id`` lowercase; the spec spells it mixed-case.

    A plain dict is not case-insensitive, so a miss here would present every call
    as a brand-new session — which reads to the user as a harness that keeps
    losing its connection.
    """
    assert session_id_from_headers({SESSION_HEADER: "abc"}) == "abc"
    assert session_id_from_headers({"mcp-session-id": "abc"}) == "abc"
    assert session_id_from_headers({"MCP-SESSION-ID": " abc "}) == "abc"
    assert session_id_from_headers({}) == ""
    assert session_id_from_headers(None) == ""
