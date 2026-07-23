"""v1.88.1 regression: GET /mcp/servers normalizes env/args.

Rows saved by the MARKETPLACE connect flow omit ``env``/``args`` when empty
(only the hand-add POST always writes them), and the Tools page crashed with
"Cannot convert undefined or null to object" on a real install (an env-less
brave_search row). The route must guarantee both keys on every row.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app


def test_mcp_servers_rows_always_carry_env_and_args(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    platform = client.app.state.platform
    platform.config.mcp_servers = [
        # Marketplace-style row: NO env, NO args key at all.
        {"name": "brave_search", "command": "npx"},
        # Hand-added style row: everything present (must pass through intact).
        {"name": "full", "command": "uvx", "args": ["-x"], "env": {"K": "v"}},
    ]
    rows = client.get("/mcp/servers").json()["servers"]
    by_name = {r["name"]: r for r in rows}
    assert by_name["brave_search"]["env"] == {}
    assert by_name["brave_search"]["args"] == []
    assert by_name["full"]["env"] == {"K": "v"}
    assert by_name["full"]["args"] == ["-x"]
    for r in rows:  # every row is shape-complete for the dashboard
        assert "tools_loaded" in r and "tool_names" in r
