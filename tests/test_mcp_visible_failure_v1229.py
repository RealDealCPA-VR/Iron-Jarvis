"""v1.229.0 (audit Wave 3, U4) — a failed MCP server is visible where the user looks.

Before: a server skipped at load left ONE warning line in daemon.log;
``GET /mcp/servers`` said ``tools_loaded: 0`` with no reason, the Tools page
rendered "0 tools · Allowed", the doctor had no check, and the Overview said
"All systems nominal". Now the load keeps a record per server (``last_error``
= the exception text, e.g. ``FileNotFoundError: ...``), the servers route and
``/diagnostics`` carry it, ``POST /mcp/servers/{name}/reload`` is the Retry,
and the doctor's ``mcp`` check names the server and the missing ``npx``.

Fully offline: FakeTransport, or a command that cannot exist.
"""

from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.mcp import FakeTransport
from iron_jarvis.mcp import tools as mcp_tools_mod
from iron_jarvis.mcp.tools import load_status, mcp_tools

doctor_mod = importlib.import_module("iron_jarvis.onboarding.doctor")

TOOLS_LIST = {
    "tools": [
        {"name": "search", "description": "Search.", "inputSchema": {"type": "object", "properties": {}}},
        {"name": "fetch", "description": "Fetch.", "inputSchema": {"type": "object", "properties": {}}},
    ]
}

#: A stdio command that cannot exist — spawning it is the live shape ("npx not found").
NO_SUCH_COMMAND = "ij-no-such-mcp-launcher-v1229"


@pytest.fixture(autouse=True)
def _fresh_load_records():
    mcp_tools_mod._LOAD_STATUS.clear()
    yield
    mcp_tools_mod._LOAD_STATUS.clear()


# --- the record ---------------------------------------------------------------


def test_skipped_server_keeps_the_exception_text_as_last_error():
    bad = FakeTransport(raise_on=True)
    assert mcp_tools([{"name": "broken", "transport_obj": bad}]) == []
    rec = load_status("broken")
    assert rec is not None
    assert rec["tools_loaded"] == 0
    assert rec["last_error"], "the skip reason must be on the record, not only in the log"
    assert ":" in rec["last_error"]  # "<ExceptionType>: <message>"
    assert rec["at"]


def test_loaded_server_records_no_error_and_its_tool_count():
    good = FakeTransport({"tools/list": TOOLS_LIST})
    tools = mcp_tools([{"name": "srv", "transport_obj": good}])
    assert len(tools) == 2
    rec = load_status("srv")
    assert rec["last_error"] is None
    assert rec["tools_loaded"] == 2


def test_missing_launcher_reads_as_file_not_found():
    # The exact live report: the catalog server is launched with `npx`, the
    # GUI-launched daemon has no npx on its PATH.
    assert mcp_tools([{"name": "brave_search", "command": NO_SUCH_COMMAND, "args": ["-y", "x"]}]) == []
    err = load_status("brave_search")["last_error"]
    assert "FileNotFoundError" in err or "not found" in err.lower(), err


# --- the routes ---------------------------------------------------------------


def test_mcp_servers_route_carries_last_error(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    platform = client.app.state.platform
    platform.config.mcp_servers = [{"name": "brave_search", "command": NO_SUCH_COMMAND, "args": []}]
    # Boot-shaped load of the configured list (the platform did this before the
    # row existed); the route reads the record it left.
    mcp_tools(platform.config.mcp_servers, secret_resolver=platform.secrets.get)
    row = client.get("/mcp/servers").json()["servers"][0]
    assert row["tools_loaded"] == 0
    assert row["last_error"] and "FileNotFoundError" in row["last_error"]
    assert row["last_attempt_at"]


def test_mcp_servers_route_reports_null_error_when_never_failed(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    platform = client.app.state.platform
    platform.config.mcp_servers = [{"name": "quiet", "command": "uvx"}]
    row = client.get("/mcp/servers").json()["servers"][0]
    assert row["last_error"] is None  # never attempted in this process: no lie either way


def test_reload_route_is_the_retry_and_refreshes_the_record(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    platform = client.app.state.platform
    good = FakeTransport({"tools/list": TOOLS_LIST})
    platform.config.mcp_servers = [
        {"name": "brave_search", "command": NO_SUCH_COMMAND, "args": []},
        {"name": "srv", "transport_obj": good},
    ]
    # A failed retry says why, in the same words the row will show.
    r = client.post("/mcp/servers/brave_search/reload").json()
    assert r["ok"] is False and r["tools_loaded"] == 0
    assert "FileNotFoundError" in r["last_error"]
    assert load_status("brave_search")["last_error"] == r["last_error"]
    # A successful retry LOADS the tools live (the read-only /test never did).
    assert platform.registry.mcp_names("srv") == []
    r = client.post("/mcp/servers/srv/reload").json()
    assert r == {"ok": True, "tools_loaded": 2, "last_error": None}
    assert platform.registry.mcp_names("srv") == ["mcp__srv__fetch", "mcp__srv__search"]
    # Retrying again does not double-register.
    client.post("/mcp/servers/srv/reload")
    assert len(platform.registry.mcp_names("srv")) == 2
    assert client.post("/mcp/servers/nope/reload").status_code == 404


def test_probe_records_nothing_when_asked(tmp_path):
    # record=False: the read-only /test path connects and lists but leaves the
    # load record exactly as the last REAL load left it — both outcomes.
    mcp_tools([{"name": "srv", "transport_obj": FakeTransport(raise_on=True)}])
    before = load_status("srv")
    got = mcp_tools([{"name": "srv", "transport_obj": FakeTransport({"tools/list": TOOLS_LIST})}], record=False)
    assert len(got) == 2
    assert load_status("srv") == before
    mcp_tools([{"name": "srv", "transport_obj": FakeTransport(raise_on=True)}], record=False)
    assert load_status("srv") == before
    assert load_status("never") is None
    mcp_tools([{"name": "never", "transport_obj": FakeTransport({"tools/list": TOOLS_LIST})}], record=False)
    assert load_status("never") is None


def test_green_test_over_a_pack_that_failed_at_boot_keeps_every_truth_surface_loud(tmp_path, monkeypatch):
    # Review finding (fix round): boot fails (npx missing) -> user installs
    # Node -> clicks Test (green) -> /test registered nothing, so the row,
    # /diagnostics and the doctor must STILL say the pack did not start until
    # a Retry (reload) actually hands its tools to the registry.
    monkeypatch.setattr(doctor_mod, "_find_npx", lambda: "C:/x/npx.cmd")
    client = TestClient(create_app(str(tmp_path)))
    platform = client.app.state.platform
    mcp_tools([{"name": "srv", "transport_obj": FakeTransport(raise_on=True)}])  # boot-shaped
    boot_error = load_status("srv")["last_error"]
    assert boot_error.startswith("MCPError")
    # A plain command row (the route serializes the row; a transport object
    # cannot ride in it) whose spawn now "works" — Node got installed.
    platform.config.mcp_servers = [{"name": "srv", "command": "npx", "args": ["-y", "srv"]}]
    monkeypatch.setattr(
        mcp_tools_mod, "_build_transport", lambda cfg, resolver: FakeTransport({"tools/list": TOOLS_LIST})
    )

    r = client.post("/mcp/servers/srv/test").json()
    assert r["ok"] is True and r["count"] == 2  # the probe itself is green

    assert platform.registry.mcp_names("srv") == []  # Test registers nothing ...
    assert load_status("srv")["last_error"] == boot_error  # ... so the record stands
    row = next(s for s in client.get("/mcp/servers").json()["servers"] if s["name"] == "srv")
    assert row["tools_loaded"] == 0 and row["last_error"] == boot_error
    diag = client.get("/diagnostics").json()["mcp_servers"]
    assert diag == [{"name": "srv", "tools_loaded": 0, "last_error": boot_error}]
    check = next(c for c in client.get("/doctor").json()["checks"] if c["name"] == "mcp")
    assert check["ok"] is False and "srv didn't start:" in check["detail"]

    # The Retry is what makes the surfaces go quiet — because the tools are LIVE.
    assert client.post("/mcp/servers/srv/reload").json()["ok"] is True
    assert len(platform.registry.mcp_names("srv")) == 2
    assert load_status("srv")["last_error"] is None
    check = next(c for c in client.get("/doctor").json()["checks"] if c["name"] == "mcp")
    assert check["ok"] is True


def test_connections_page_test_is_a_probe_too(tmp_path):
    # Same clobber through the other door: connectors.service.test() over a
    # user-added MCP server must not rewrite the boot record either.
    from iron_jarvis.connectors import service as connectors

    client = TestClient(create_app(str(tmp_path)))
    platform = client.app.state.platform
    mcp_tools([{"name": "srv", "transport_obj": FakeTransport(raise_on=True)}])
    boot_error = load_status("srv")["last_error"]
    platform.config.mcp_servers = [{"name": "srv", "transport_obj": FakeTransport({"tools/list": TOOLS_LIST})}]
    assert connectors.test(platform, "srv")["ok"] is True
    assert load_status("srv")["last_error"] == boot_error
    assert platform.registry.mcp_names("srv") == []


def test_diagnostics_names_the_pack_that_did_not_start(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    platform = client.app.state.platform
    platform.config.mcp_servers = [{"name": "brave_search", "command": NO_SUCH_COMMAND, "args": []}]
    client.post("/mcp/servers/brave_search/reload")
    rows = client.get("/diagnostics").json()["mcp_servers"]
    assert rows[0]["name"] == "brave_search"
    assert rows[0]["tools_loaded"] == 0
    assert "FileNotFoundError" in rows[0]["last_error"]


# --- the doctor ---------------------------------------------------------------


def _platform_with(servers):
    return SimpleNamespace(config=SimpleNamespace(mcp_servers=servers))


def test_doctor_mcp_check_is_recommended_and_quiet_with_no_servers():
    row = doctor_mod.check_mcp(_platform_with([]))
    assert row["name"] == "mcp" and row["ok"] is True
    assert row["level"] == doctor_mod.RECOMMENDED


def test_doctor_mcp_check_names_missing_npx_and_the_server_needing_it(monkeypatch):
    monkeypatch.setattr(doctor_mod, "_find_npx", lambda: None)
    row = doctor_mod.check_mcp(_platform_with([{"name": "brave_search", "command": "npx"}]))
    assert row["ok"] is False
    assert "npx not found" in row["detail"] and "brave_search" in row["detail"]
    assert "nodejs.org" in row["fix"]


def test_doctor_mcp_check_reports_each_servers_last_error(monkeypatch):
    monkeypatch.setattr(doctor_mod, "_find_npx", lambda: "C:/x/npx.cmd")
    mcp_tools([{"name": "broken", "transport_obj": FakeTransport(raise_on=True)}])
    mcp_tools([{"name": "fine", "transport_obj": FakeTransport({"tools/list": TOOLS_LIST})}])
    row = doctor_mod.check_mcp(
        _platform_with([{"name": "broken", "command": "npx"}, {"name": "fine", "command": "npx"}])
    )
    assert row["ok"] is False
    assert "broken didn't start:" in row["detail"]
    assert "fine didn't start" not in row["detail"]
    assert "Retry" in row["fix"]


def test_doctor_mcp_check_passes_when_every_server_started(monkeypatch):
    monkeypatch.setattr(doctor_mod, "_find_npx", lambda: "C:/x/npx.cmd")
    mcp_tools([{"name": "fine", "transport_obj": FakeTransport({"tools/list": TOOLS_LIST})}])
    row = doctor_mod.check_mcp(_platform_with([{"name": "fine", "command": "npx"}]))
    assert row["ok"] is True and "1 MCP server started" in row["detail"]


def test_doctor_mcp_check_trusts_the_registry_over_a_clean_record(monkeypatch):
    # Belt: an attempted server with a clean record but ZERO live tools is not
    # started (the record can be clean after a probe or an empty tools/list);
    # a platform without a registry (unit shape) keeps the record's verdict.
    monkeypatch.setattr(doctor_mod, "_find_npx", lambda: "C:/x/npx.cmd")
    mcp_tools([{"name": "fine", "transport_obj": FakeTransport({"tools/list": TOOLS_LIST})}])
    empty = SimpleNamespace(
        config=SimpleNamespace(mcp_servers=[{"name": "fine", "command": "npx"}]),
        registry=SimpleNamespace(mcp_names=lambda server=None: []),
    )
    row = doctor_mod.check_mcp(empty)
    assert row["ok"] is False and "fine didn't start: no tools loaded" in row["detail"]
    live = SimpleNamespace(
        config=empty.config,
        registry=SimpleNamespace(mcp_names=lambda server=None: ["mcp__fine__search"]),
    )
    assert doctor_mod.check_mcp(live)["ok"] is True
    # Never attempted in this process (no record) -> nothing to report yet.
    mcp_tools_mod._LOAD_STATUS.clear()
    assert doctor_mod.check_mcp(empty)["ok"] is True


def test_npx_is_resolved_beside_node_via_the_cli_finder(monkeypatch):
    # The same resolver the Build page uses (PATH, then %LOCALAPPDATA%\pi-node\current ...).
    import iron_jarvis.terminals.ai_clis as ai_clis

    seen: list[str] = []

    def fake_find(cmd):
        seen.append(cmd)
        return "C:/Users/x/AppData/Local/pi-node/current/npx.cmd"

    monkeypatch.setattr(ai_clis, "_find", fake_find)
    assert doctor_mod._find_npx().endswith("npx.cmd")
    assert seen == ["npx"]


def test_get_doctor_includes_the_mcp_check(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    checks = {c["name"]: c for c in client.get("/doctor").json()["checks"]}
    assert "mcp" in checks and checks["mcp"]["level"] == doctor_mod.RECOMMENDED
