"""A REMOTE memory base can say whether it is reachable (v1.173.0).

v1.172.0 taught local markdown bases to report ``health()`` — a vault whose
folder had moved stopped being indistinguishable from a vault with no matches.
Remote kinds kept reporting ``available: null``, and the MCP-served brain is
exactly the kind that goes dark: its tools are listed ONCE, at daemon boot, so
the honest unknown was least useful precisely where it mattered most.

The rules these tests hold:

* available means SEARCHABLE — the server answers AND exposes a search-like
  tool. "A socket opened" is not the capability recall needs.
* the verdict is re-derived from a FRESH ``tools/list``; answering from the
  boot-time cache would report green for a server that has since died.
* a probe that outruns its deadline is ``None``, NEVER ``False`` — an
  unanswered check is not proof of a broken base.
* a page poll cannot hang on it: the probe is bounded, cached, and the
  listing endpoint has its own budget.
"""

from __future__ import annotations

import threading
import time

from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import knowledge as knowledge_routes
from iron_jarvis.ltm.mcp_brain import (
    _HEALTH_TTL,
    _HEALTH_UNKNOWN_TTL,
    McpBrainConnector,
)

_SEARCH_TOOL = {
    "name": "search_notes",
    "description": "Search the vault",
    "inputSchema": {"type": "object", "properties": {"query": {}, "limit": {}}},
}
_APPEND_TOOL = {
    "name": "append_note",
    "description": "Add a note",
    "inputSchema": {"type": "object", "properties": {"title": {}, "content": {}}},
}
_TOOLS = [_SEARCH_TOOL, _APPEND_TOOL]


class _Fake:
    """An MCPClient stand-in whose ``list_tools`` can fail, stall, or change
    its answer between calls (a server that went dark after boot)."""

    def __init__(self, tools=_TOOLS, *, fail: BaseException | None = None, gate=None):
        self._tools = tools
        self._fail = fail
        self._gate = gate
        self.list_calls = 0

    def list_tools(self):
        self.list_calls += 1
        if self._gate is not None:
            self._gate.wait(timeout=30)
        if self._fail is not None:
            raise self._fail
        return self._tools(self.list_calls) if callable(self._tools) else self._tools

    def call_tool(self, name, arguments):  # pragma: no cover - not exercised here
        return {"content": [{"type": "text", "text": "[]"}], "isError": False}


def _conn(fake, name="hermes-brain", url="http://127.0.0.1:9/mcp"):
    return McpBrainConnector(name, url=url, client=fake)


# --- the verdict --------------------------------------------------------------


def test_a_live_brain_is_available_and_names_the_tool_serving_search():
    fake = _Fake()
    health = _conn(fake).health()
    assert health["available"] is True
    assert health["detail"] == ""  # same shape as a healthy local base
    assert health["path"] == "http://127.0.0.1:9/mcp"
    assert health["tool"] == "search_notes"


def test_availability_is_the_capability_search_needs_not_merely_a_connection():
    """A server that answers but has no search-like tool returns NOTHING to
    every recall — that is unavailable, and it must be pinned to the SAME pick
    the real search uses."""
    fake = _Fake([_APPEND_TOOL])
    conn = _conn(fake)
    health = conn.health()
    assert health["available"] is False, "a connectable-but-unsearchable base is not green"
    assert "search" in health["detail"]
    assert "append_note" in health["detail"], "the detail names the tools it DID find"
    # The detail says what to do — and names the surface that can actually DO
    # it. MCP-kind LTM bases are added/removed on the Memory page's Long-term
    # tab; the Connections page manages MCP TOOL servers, a different registry
    # where this base does not appear at all.
    assert "Memory page" in health["detail"], "the detail says what to do about it"
    assert "Long-term" in health["detail"]
    assert "Connections" not in health["detail"], "sent to a page that cannot fix it"
    # ... and the connector's own search agrees, so the probe cannot drift.
    try:
        conn.search("anything")
        raise AssertionError("expected search to refuse a server with no search tool")
    except RuntimeError as exc:
        assert "no search-like tool" in str(exc)


def test_a_dead_server_is_unavailable_with_the_underlying_error_and_the_fix():
    fake = _Fake(fail=ConnectionRefusedError("[Errno 111] Connection refused"))
    health = _conn(fake).health()
    assert health["available"] is False
    assert "Connection refused" in health["detail"]
    assert "cannot connect" in health["detail"]
    assert "Memory page" in health["detail"], "the fix names a page that manages it"
    assert "Connections" not in health["detail"]
    assert health["path"] == "http://127.0.0.1:9/mcp"


def test_a_credential_failure_is_named_as_credentials_not_as_unreachable():
    """A 401 and a dead socket have DIFFERENT fixes; telling a user to restart
    a server that is running fine wastes the whole diagnosis."""
    fake = _Fake(fail=RuntimeError("Client error '401 Unauthorized' for url '...'"))
    detail = _conn(fake).health()["detail"]
    assert "refused the credentials" in detail
    assert "token" in detail
    assert "cannot connect" not in detail


def test_a_stdio_brain_reports_its_command_as_the_path():
    conn = McpBrainConnector("local-brain", command="npx", args=["-y", "brain-mcp"],
                             client=_Fake())
    health = conn.health()
    assert health["available"] is True
    assert health["path"] == "npx -y brain-mcp"


def test_a_base_with_no_endpoint_configured_says_so():
    conn = McpBrainConnector("halfway")
    health = conn.health()
    assert health["available"] is False
    assert "no MCP url or command" in health["detail"]


# --- honesty under load -------------------------------------------------------


def test_a_slow_server_reports_unknown_never_false_and_returns_promptly():
    """The exact anti-lie: a check that timed out is not proof of a broken
    base, and it must not hold the page while it waits."""
    gate = threading.Event()
    fake = _Fake(gate=gate)
    conn = _conn(fake)
    try:
        started = time.monotonic()
        health = conn.health(timeout=0.2)
        elapsed = time.monotonic() - started
    finally:
        gate.set()  # release the abandoned probe thread
    assert health["available"] is None
    assert health["available"] is not False
    assert "could not check in time" in health["detail"]
    assert "unknown" in health["detail"]
    assert health["path"] == "http://127.0.0.1:9/mcp"
    # It returned long before the server would have answered (the fake holds
    # its gate for up to 30s) — a relative bound, not a machine-speed one.
    assert elapsed < 5.0, f"the probe blocked for {elapsed:.2f}s"


def test_a_second_poll_during_a_hanging_probe_does_not_pile_on():
    gate = threading.Event()
    fake = _Fake(gate=gate)
    conn = _conn(fake)
    try:
        conn.health(timeout=0.2)
        conn.invalidate_health()  # a fresh poll, no cache to lean on
        second = conn.health(timeout=0.2)
        assert second["available"] is None
        assert "still running" in second["detail"]
        assert fake.list_calls == 1, "a second thread was launched at the dead server"
    finally:
        gate.set()


def test_the_verdict_is_cached_so_a_polling_page_does_not_hammer_the_server():
    fake = _Fake()
    conn = _conn(fake)
    for _ in range(4):
        assert conn.health()["available"] is True
    assert fake.list_calls == 1, "every poll opened a connection"
    conn.invalidate_health()
    assert conn.health()["available"] is True
    assert fake.list_calls == 2
    # refresh= bypasses the cache without a separate invalidate call.
    conn.health(refresh=True)
    assert fake.list_calls == 3


def test_an_unknown_verdict_expires_sooner_than_a_settled_one():
    """A grey "couldn't tell" must not outlive the outage that caused it."""
    assert _HEALTH_UNKNOWN_TTL < _HEALTH_TTL
    gate = threading.Event()
    fake = _Fake(gate=gate)
    conn = _conn(fake)
    try:
        assert conn.health(timeout=0.2)["available"] is None
    finally:
        gate.set()
    stamp, verdict = conn._health
    conn._health = (stamp - (_HEALTH_UNKNOWN_TTL + 1), verdict)
    assert conn.cached_health() is None, "an unknown was reused past its TTL"

    good = _conn(_Fake())
    assert good.health()["available"] is True
    stamp, verdict = good._health
    good._health = (stamp - (_HEALTH_UNKNOWN_TTL + 1), verdict)
    assert (good.cached_health() or {}).get("available") is True, (
        "a settled verdict must survive the SHORT unknown TTL"
    )


def test_health_re_lists_the_tools_instead_of_trusting_the_boot_time_cache():
    """The whole point: MCP tools load once at boot, so a brain that died at
    10am still has a full ``_tools`` list at 4pm."""
    fake = _Fake(lambda n: _TOOLS if n == 1 else [])
    conn = _conn(fake)
    conn._connect()  # boot-time discovery, exactly like the first search
    assert conn._tools == _TOOLS
    health = conn.health()
    assert health["available"] is False, "answered from the stale boot-time tool list"
    assert fake.list_calls == 2


def test_a_probe_against_a_restart_blip_never_blinds_the_base():
    """THE anti-regression: a health check is a READER.

    ``_probe`` used to publish whatever ``tools/list`` returned. One probe
    landing while the server restarted (an empty list) blanked ``_tools`` — and
    because search re-lists only when the cache is None, EVERY later recall
    raised "no search-like tool", which the manager swallows into an empty
    result. A transient outage became permanent, silent blindness that looks
    exactly like "no such note"."""
    # call 1: boot discovery. call 2: the probe, landing on the blip. Then the
    # server is fine again — but nothing would ever ask it a third time.
    fake = _Fake(lambda n: [] if n == 2 else _TOOLS)
    conn = _conn(fake)
    assert conn.search("anything") == []  # boots, works
    assert conn.health()["available"] is False, "the blip is still an honest verdict"
    assert conn._tools == _TOOLS, "a transient empty list overwrote a good tool list"
    # ...and the base is still searchable, which is the whole point.
    assert conn.search("anything") == []
    assert fake.list_calls == 2, "search re-listed when it did not need to"


def test_an_empty_tool_list_at_boot_self_heals_instead_of_dying_for_the_process():
    """Belt and braces for the same failure from the other side: a bridge that
    is still starting answers with no tools at all. The NEXT call re-lists
    rather than raising forever off a cache written once."""
    fake = _Fake(lambda n: [] if n == 1 else _TOOLS)
    conn = _conn(fake)
    conn._connect()
    assert conn._tools == []
    assert conn.search("anything") == [], "an empty boot list was never re-listed"
    assert fake.list_calls == 2
    assert conn._tools == _TOOLS
    assert conn.search("anything") == []
    assert fake.list_calls == 2, "the repaired cache is reused"


def test_two_concurrent_polls_of_a_healthy_base_both_get_the_real_verdict():
    """/memory mounts two consumers of /ltm/sources in one commit, so two
    probes land milliseconds apart. The loser must RIDE ALONG with the
    in-flight probe, not invent a grey verdict for a server answering fine —
    the card loads once and never re-polls, so that grey would stick."""
    gate = threading.Event()
    entered = threading.Event()
    results: dict[str, dict] = {}

    class _Gated(_Fake):
        def list_tools(self):
            entered.set()  # "the probe is now INSIDE the server call"
            return super().list_tools()

    fake = _Gated(gate=gate)
    conn = _conn(fake)
    first = threading.Thread(target=lambda: results.__setitem__("a", conn.health(timeout=5)))
    first.start()
    assert entered.wait(5), "the first probe never started"
    # The first probe is now INSIDE the server call; caller B arrives here.
    threading.Timer(0.1, gate.set).start()
    results["b"] = conn.health(timeout=5)
    first.join(5)
    gate.set()

    assert results["a"]["available"] is True
    assert results["b"]["available"] is True, "a healthy base went grey on a page load"
    assert results["b"]["tool"] == "search_notes"
    assert fake.list_calls == 1, "the second caller piled onto the same server"


def test_re_check_during_a_hanging_probe_keeps_the_last_known_verdict():
    """Pressing "confirm this is still fine" must never make the UI LESS sure:
    the cache is dropped only once this call actually owns the probe."""
    gate = threading.Event()
    conn = _conn(_Fake(gate=gate))
    try:
        gate.set()
        assert conn.health()["available"] is True
        gate.clear()
        conn.invalidate_health()
        hang = threading.Thread(target=lambda: conn.health(timeout=0.2))
        hang.start()
        hang.join(5)
        # A probe is now hanging; put a known-good verdict back in the cache.
        conn._remember({"available": True, "detail": "", "path": conn.location()})
        again = conn.health(refresh=True, timeout=0.2)
        assert again["available"] is True, "Re-check destroyed a good verdict"
        assert (conn.cached_health() or {}).get("available") is True
    finally:
        gate.set()


def test_a_port_that_looks_like_a_status_code_is_not_a_credential_failure():
    """'401'/'403' as bare substrings matched the ENDPOINT URL: a brain on
    :4013 that was simply down got 're-add it with a fresh token'."""
    fake = _Fake(fail=RuntimeError("ConnectError: all attempts failed for 'http://127.0.0.1:4013/mcp'"))
    detail = McpBrainConnector(
        "hermes-brain", url="http://127.0.0.1:4013/mcp", client=fake
    ).health()["detail"]
    assert "cannot connect" in detail
    assert "credentials" not in detail
    # ...while a real status code still routes to the credential fix.
    forbidden = _conn(_Fake(fail=RuntimeError("HTTP 403 from the server"))).health()
    assert "refused the credentials" in forbidden["detail"]


def test_an_async_client_is_resolved_off_the_calling_thread():
    """The real MCPClient's methods are coroutines; the probe runs on its own
    thread where there is no running loop."""

    class _AsyncFake(_Fake):
        async def list_tools(self):  # type: ignore[override]
            return super().list_tools()

    assert _conn(_AsyncFake()).health()["available"] is True


# --- the listing endpoint -----------------------------------------------------


def test_ltm_sources_fills_availability_for_an_mcp_base(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    platform = client.app.state.platform
    fake = _Fake()
    platform.ltm.register(_conn(fake))
    platform.ltm.register(_conn(_Fake([_APPEND_TOOL]), name="dark-brain"))

    listing = client.get("/ltm/sources", params={"probe": "true"}).json()
    bases = {b["name"]: b for b in listing["bases"]}
    assert bases["hermes-brain"]["available"] is True
    assert bases["hermes-brain"]["path"] == "http://127.0.0.1:9/mcp"
    assert bases["dark-brain"]["available"] is False
    assert "search" in bases["dark-brain"]["detail"]
    # v1.172.0's local base is untouched by the new branch.
    assert bases["brain"]["available"] is True
    assert client.get("/ltm/sources").json()["active"] == platform.ltm.sources()


def test_the_listing_does_not_probe_the_network_unless_it_is_asked_to(tmp_path):
    """``/ltm/sources`` has callers that want only ``sources``/``active`` —
    the Long-term tab's list and the project page's LTM chip. Neither asked to
    wait on a network round trip to a dead brain, so the probe is OPT-IN and
    the default listing stays as instant as it was in v1.172.0."""
    client = TestClient(create_app(str(tmp_path)))
    fake = _Fake()
    client.app.state.platform.ltm.register(_conn(fake))

    bases = {b["name"]: b for b in client.get("/ltm/sources").json()["bases"]}
    assert fake.list_calls == 0, "an unrelated caller paid for a network probe"
    assert bases["hermes-brain"]["available"] is None, "unchecked is never a verdict"
    assert "not checked yet" in bases["hermes-brain"]["detail"]
    assert "probe=true" in bases["hermes-brain"]["detail"], "say how to get a check"
    # The row still says WHERE the base is — the unverdicted row is the one the
    # user is most likely to want to check against reality.
    assert bases["hermes-brain"]["path"] == "http://127.0.0.1:9/mcp"
    # The CHEAP local probe is never opted out of.
    assert bases["brain"]["available"] is True

    # ...and the card that does want a verdict asks for one.
    probed = client.get("/ltm/sources", params={"probe": "true"}).json()["bases"]
    assert {b["name"]: b for b in probed}["hermes-brain"]["available"] is True
    assert fake.list_calls == 1


def test_the_listing_reuses_the_cache_and_refresh_forces_a_recheck(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    fake = _Fake()
    client.app.state.platform.ltm.register(_conn(fake))

    client.get("/ltm/sources", params={"probe": "true"})
    client.get("/ltm/sources", params={"probe": "true"})
    assert fake.list_calls == 1, "each page poll re-probed the remote server"
    # refresh alone implies a check: it is the explicit "re-check now".
    r = client.get("/ltm/sources", params={"refresh": "true"})
    assert r.status_code == 200
    assert fake.list_calls == 2


def test_a_probe_is_handed_the_time_the_page_budget_has_left(tmp_path, monkeypatch):
    """The budget must bound the RESPONSE, not merely decide whether to start
    one more probe: an unbudgeted probe could add its own full 4s timeout on
    top of a nearly-spent 6s budget."""
    monkeypatch.setattr(knowledge_routes, "_LTM_PROBE_BUDGET", 1.0)

    class _Recording:
        name = "recording-brain"

        def __init__(self) -> None:
            self.timeouts: list[float | None] = []

        def location(self):
            return "http://brain.example/mcp"

        def cached_health(self):
            return None

        def invalidate_health(self):
            return None

        def health(self, *, timeout=None, refresh=False):
            self.timeouts.append(timeout)
            return {"available": True, "detail": "", "path": self.location()}

        def search(self, query, k=5):  # pragma: no cover - not exercised
            return []

        def append(self, title, content):  # pragma: no cover - not exercised
            return ""

    rec = _Recording()
    client = TestClient(create_app(str(tmp_path)))
    client.app.state.platform.ltm.register(rec)

    assert client.get("/ltm/sources", params={"probe": "true"}).status_code == 200
    assert rec.timeouts and rec.timeouts[0] is not None, "the probe ran unbounded"
    assert rec.timeouts[0] <= 1.0, (
        f"the probe got {rec.timeouts[0]}s of a 1.0s page budget"
    )
    assert rec.timeouts[0] > 0.5, "the budget was not what bounded it"


def test_a_spent_probe_budget_reports_not_checked_rather_than_a_guess(
    tmp_path, monkeypatch
):
    """Remote probes share an endpoint-wide budget; bases past it are honestly
    unchecked — and the CHEAP local probe is never sacrificed to it."""
    monkeypatch.setattr(knowledge_routes, "_LTM_PROBE_BUDGET", -1.0)
    client = TestClient(create_app(str(tmp_path)))
    fake = _Fake()
    client.app.state.platform.ltm.register(_conn(fake))

    r = client.get("/ltm/sources", params={"probe": "true"})
    bases = {b["name"]: b for b in r.json()["bases"]}
    skipped = bases["hermes-brain"]
    assert skipped["available"] is None
    assert "not checked yet" in skipped["detail"]
    assert "budget" in skipped["detail"]
    assert fake.list_calls == 0, "the budget was reported but not actually honoured"
    # A skipped row is still a COMPLETE row: name, kind, the tri-state, the
    # reason, and where the base lives.
    assert set(skipped) == {"name", "kind", "available", "detail", "path"}
    assert skipped["path"] == "http://127.0.0.1:9/mcp"
    assert bases["brain"]["available"] is True


def test_a_connector_whose_health_explodes_never_500s_the_listing(tmp_path):
    client = TestClient(create_app(str(tmp_path)))

    class _Exploding:
        name = "boom-brain"

        def health(self):
            raise RuntimeError("probe blew up")

        def search(self, query, k=5):  # pragma: no cover - not exercised
            return []

        def append(self, title, content):  # pragma: no cover - not exercised
            return ""

    client.app.state.platform.ltm.register(_Exploding())
    r = client.get("/ltm/sources")
    assert r.status_code == 200
    bases = {b["name"]: b for b in r.json()["bases"]}
    assert bases["boom-brain"]["available"] is False
    assert "probe blew up" in bases["boom-brain"]["detail"]
