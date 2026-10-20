"""v1.342.0 integration pins — the lines the six lanes asked the integrator
to place, each one proven by a test that is red without it.

* ``build_platform`` hands its event bus to ``mcp.tools.set_event_bus`` so a
  FAILED LOAD (boot, deferred boot, Retry) publishes ``mcp.pack_failed``.
* ``platform._close_mcp_tools`` is a delegation to the ONE closer,
  ``mcp.tools.close_tools_clients`` (lane D lifted it; two bodies drift).
* The system map's Tools star counts the load record's ``"ok"`` (the contract
  word; it used to be ``"ready"``).
* A Directory (connectors) row for a pack with zero tools reads the load
  record: ``status: "failed"`` + ``reason`` / ``fix`` / ``stderr_tail`` when
  the record says why, ``no_tools`` (with the three keys empty) when nothing
  does — the Directory card (lane F) renders exactly those fields.
* ``GET /mcp/servers`` never echoes an HTTP pack's ``auth.value`` (the one
  non-env secret shape ``_build_transport`` reads).

The lane-C boot migration line is pinned by
``tests/test_mcp_secrets_vault_v1342.py::test_boot_migrates_a_planted_plain_token_before_packs_load``
and the bell lines by ``dashboard/__tests__/bell-pack-failed-v1342.test.tsx``.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import iron_jarvis.mcp.tools as mcp_tools
from iron_jarvis.daemon.app import create_app


@pytest.fixture
def clean_load_status():
    """The load record is process-wide; keep this file's plants to itself."""
    saved = dict(mcp_tools._LOAD_STATUS)
    saved_announced = set(mcp_tools._ANNOUNCED)
    mcp_tools._LOAD_STATUS.clear()
    mcp_tools._ANNOUNCED.clear()
    try:
        yield
    finally:
        mcp_tools._LOAD_STATUS.clear()
        mcp_tools._LOAD_STATUS.update(saved)
        mcp_tools._ANNOUNCED.clear()
        mcp_tools._ANNOUNCED.update(saved_announced)


def _failed_record(**extra):
    rec = {
        "state": "failed",
        "last_error": "MCPServerStopped: pack 'dying': the MCP server closed the connection (exit code 1)",
        "reason": "the extension's server exited (exit code 1)",
        "fix": "read its last output below; a missing key or setting is the usual cause",
        "stderr_tail": "Error: BRAVE_API_KEY is not set",
        "failures": 0,
        "at": "2026-10-16T00:00:00Z",
    }
    rec.update(extra)
    return rec


# --- the event bus reaches the loader ---------------------------------------


def test_build_platform_wires_its_event_bus_into_the_mcp_loader(tmp_path):
    app = create_app(str(tmp_path))
    platform = app.state.platform
    assert mcp_tools._EVENT_BUS is platform.event_bus, (
        "build_platform must call mcp.tools.set_event_bus(event_bus): without it a "
        "pack that fails to LOAD records everything and announces nothing"
    )


# --- one closer ----------------------------------------------------------------


def test_platform_close_mcp_tools_delegates_to_the_one_closer(monkeypatch):
    from iron_jarvis import platform as platform_mod

    seen: list[list] = []
    monkeypatch.setattr(mcp_tools, "close_tools_clients", lambda tools: seen.append(list(tools or [])))

    class _Client:
        closed = False

        def close(self):
            self.closed = True

    class _Tool:
        def __init__(self):
            self.client = _Client()

    tool = _Tool()
    platform_mod._close_mcp_tools([tool])
    assert seen == [[tool]], "platform._close_mcp_tools must hand its list to mcp.tools.close_tools_clients"
    assert tool.client.closed is False, "the platform body must not close on its own beside the delegation"


# --- the system map reads the contract's word -----------------------------------


def test_system_map_counts_an_ok_pack_as_running(tmp_path, monkeypatch):
    app = create_app(str(tmp_path))
    app.state.d.platform.config.mcp_servers = [{"name": "github", "command": "x"}]
    monkeypatch.setattr(mcp_tools, "load_status", lambda name: {"state": "ok", "last_error": None})
    body = TestClient(app).get("/ui/system-map").json()
    tools = {n["key"]: n for a in body["areas"] for n in a["nodes"]}["tools"]
    # v1.360.0 (lane 10C, G76; moved on purpose): the canon's "extension".
    assert tools["detail"].startswith("1 of 1 extension running"), tools["detail"]
    assert tools["attention"] is None


# --- the Directory row reads the load record --------------------------------------


def test_directory_row_for_a_failed_pack_carries_reason_fix_and_last_output(tmp_path, clean_load_status):
    from iron_jarvis.connectors import service as connectors

    app = create_app(str(tmp_path))
    platform = app.state.platform
    platform.config.mcp_servers = [{"name": "dying", "command": "x"}]
    mcp_tools._LOAD_STATUS["dying"] = _failed_record()

    rows = {r["id"]: r for r in connectors._user_mcp_connectors(platform)}
    row = rows["dying"]
    assert row["status"] == "failed"
    assert row["connected"] is True  # the connection exists; it is the LOAD that failed
    assert row["tools_loaded"] == 0
    assert row["detail"].startswith("MCPServerStopped: pack 'dying'")
    assert row["reason"] == "the extension's server exited (exit code 1)"
    assert row["fix"].startswith("read its last output below")
    assert row["stderr_tail"] == "Error: BRAVE_API_KEY is not set"

    # The same row through the HTTP door (the Directory page's read).
    via_route = TestClient(app).get("/connectors").json()
    got = next(r for r in via_route["connectors"] if r["id"] == "dying")
    assert got["status"] == "failed" and got["stderr_tail"] == "Error: BRAVE_API_KEY is not set"


def test_directory_row_with_no_record_is_no_tools_with_empty_why_fields(tmp_path, clean_load_status):
    from iron_jarvis.connectors import service as connectors

    app = create_app(str(tmp_path))
    platform = app.state.platform
    platform.config.mcp_servers = [{"name": "quiet", "command": "x"}]

    row = {r["id"]: r for r in connectors._user_mcp_connectors(platform)}["quiet"]
    assert row["status"] == "no_tools"
    assert row["reason"] == "" and row["fix"] == "" and row["stderr_tail"] == ""
    # v1.351.0 (G08): plain words naming the card's own Test and Retry.
    assert "isn't answering yet" in row["detail"]
    assert "Retry" in row["detail"]


def test_a_failing_pack_with_zero_tools_is_failed_even_without_last_error(clean_load_status):
    from iron_jarvis.connectors.service import _mcp_status

    mcp_tools._LOAD_STATUS["f"] = _failed_record(state="failing", last_error="", stderr_tail="")
    st = _mcp_status(True, 0, "f")
    assert st["status"] == "failed"
    assert st["detail"] == "the pack did not start"


# --- no secret value leaves GET /mcp/servers ------------------------------------


def test_get_mcp_servers_never_echoes_an_http_auth_value(tmp_path):
    app = create_app(str(tmp_path))
    app.state.platform.config.mcp_servers = [
        {
            "name": "hosted",
            "transport": "http",
            "url": "http://127.0.0.1:9/mcp",
            "auth": {"header": "Authorization", "format": "Bearer {value}", "value": "tok-SECRET-9x7"},
        }
    ]
    res = TestClient(app).get("/mcp/servers")
    assert res.status_code == 200, res.text
    assert "tok-SECRET-9x7" not in res.text
    row = res.json()["servers"][0]
    assert row["auth"]["value_set"] is True
    assert row["auth"]["header"] == "Authorization"
    assert "value" not in row["auth"]
