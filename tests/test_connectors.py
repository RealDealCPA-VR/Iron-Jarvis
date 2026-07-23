"""The Connector Marketplace (CX-01) is real, safe, and restart-proof.

Fully offline. Proves the catalog invariants (pure data), the connect/test/
disconnect service, and the security property that matters most: an MCP token a
user supplies is stored ENCRYPTED in the vault and only referenced by name in
config — never written to disk in plaintext — and is resolved onto (not over) the
process environment at launch so ``npx``/``uvx`` still find ``PATH``.

Real MCP connectors shell out to ``npx``/``uvx`` (not installed in CI), so a live
tools-load yields ``tools_loaded == 0`` with a ``note`` — that is EXPECTED. These
tests assert on the persisted config + vault + reported status, never on tools
actually loading, and touch no network or external command.
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from iron_jarvis.connectors.catalog import (
    CATALOG,
    CATEGORY_ORDER,
    CONNECT_VIA,
    FIELD_KINDS,
    connector_dict,
    get_connector,
)
from iron_jarvis.daemon.app import create_app


# --------------------------------------------------------------------------- #
# Helpers.
# --------------------------------------------------------------------------- #
def _rows(client: TestClient) -> list[dict]:
    return client.get("/connectors").json()["connectors"]


def _row(client: TestClient, cid: str) -> dict:
    return next(r for r in _rows(client) if r["id"] == cid)


def _server(platform, cid: str) -> dict:
    return next(s for s in (platform.config.mcp_servers or []) if s.get("name") == cid)


# --------------------------------------------------------------------------- #
# (1) Catalog invariants — pure data, no platform.
# --------------------------------------------------------------------------- #
def test_catalog_invariants():
    ids = [c.id for c in CATALOG]
    assert ids, "the catalog is not empty"
    assert len(ids) == len(set(ids)), "connector ids are unique"

    for c in CATALOG:
        assert c.connect_via in CONNECT_VIA, c.id
        assert c.name and c.glyph and c.blurb and c.unlocks, c.id
        assert c.category in CATEGORY_ORDER, c.id
        if c.connect_via == "mcp":
            assert c.command, f"{c.id} is mcp so it needs a command"
        else:  # oauth / api_key route through the connection registry
            assert c.provider, f"{c.id} is {c.connect_via} so it needs a provider"
        for f in c.fields:
            assert f.kind in FIELD_KINDS, (c.id, f.name)

        d = connector_dict(c)
        # The catalog dict is safe to hand to a browser: it carries the gallery
        # copy but NONE of the wiring that could leak a secret.
        assert "fields" in d and "unlocks" in d and "scopes" in d
        for secret_bearing in ("command", "args", "env", "env_secrets", "token", "key"):
            assert secret_bearing not in d, f"{c.id} dict leaks {secret_bearing!r}"


# --------------------------------------------------------------------------- #
# (2) A few known connectors exist with the expected wiring.
# --------------------------------------------------------------------------- #
def test_known_connectors_present():
    gh = get_connector("github")
    assert gh is not None and gh.connect_via == "mcp" and gh.command
    assert any(
        f.name == "GITHUB_PERSONAL_ACCESS_TOKEN" and f.kind == "secret" for f in gh.fields
    )

    fs = get_connector("filesystem")
    assert fs is not None and fs.connect_via == "mcp"
    assert any(f.kind == "arg" for f in fs.fields)

    assert get_connector("slack").connect_via == "mcp"

    pixio = get_connector("pixio")
    assert pixio.connect_via == "api_key" and pixio.provider == "pixio"

    gd = get_connector("google_drive")
    assert gd.connect_via == "oauth" and gd.provider == "google_drive"


# --------------------------------------------------------------------------- #
# (3) GET /connectors — the annotated gallery.
# --------------------------------------------------------------------------- #
def test_list_connectors_http(tmp_path):
    with TestClient(create_app(str(tmp_path))) as client:
        r = client.get("/connectors")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["connectors"], "the gallery is not empty"
        # Curated categories first, then the dynamic groups (the user's own
        # MCP servers + memory sources) so the gallery can order + filter them.
        assert body["categories"] == [*CATEGORY_ORDER, "Custom", "Memory"]
        for row in body["connectors"]:
            assert {"connected", "status", "fields", "unlocks"} <= set(row), row["id"]
        assert _row(client, "github")["connected"] is False


# --------------------------------------------------------------------------- #
# (4) MCP connect: the token lands in the VAULT, config carries only its NAME.
# --------------------------------------------------------------------------- #
def test_mcp_connect_stores_token_in_vault(tmp_path):
    with TestClient(create_app(str(tmp_path))) as client:
        p = client.app.state.platform
        r = client.post(
            "/connectors/github/connect",
            json={"values": {"GITHUB_PERSONAL_ACCESS_TOKEN": "ghp_x"}},
        )
        assert r.status_code == 200, r.text

        cfg = _server(p, "github")
        sname = cfg["env_secrets"]["GITHUB_PERSONAL_ACCESS_TOKEN"]
        # env_secrets names a vault secret, it does NOT hold the plaintext.
        assert sname.startswith("conn_github_")
        assert sname != "ghp_x"
        # The security property: the persisted config has no plaintext token.
        assert "ghp_x" not in json.dumps(p.config.mcp_servers)
        # …but the vault can decrypt it back.
        assert p.secrets.get(sname) == "ghp_x"

        assert _row(client, "github")["connected"] is True


# --------------------------------------------------------------------------- #
# (5) Building the transport resolves the secret AND preserves PATH (merge, not
#     replace) so the child ``npx`` process can still launch.
# --------------------------------------------------------------------------- #
def test_env_build_resolves_and_preserves_path(tmp_path):
    from iron_jarvis.mcp.tools import _build_transport

    with TestClient(create_app(str(tmp_path))) as client:
        p = client.app.state.platform
        client.post(
            "/connectors/github/connect",
            json={"values": {"GITHUB_PERSONAL_ACCESS_TOKEN": "ghp_x"}},
        )
        cfg = _server(p, "github")

        transport = _build_transport(cfg, p.secrets.get)
        assert transport.env["GITHUB_PERSONAL_ACCESS_TOKEN"] == "ghp_x"
        # Merged onto os.environ, not replaced — PATH survives.
        assert "PATH" in transport.env


# --------------------------------------------------------------------------- #
# (6) An ``arg`` field substitutes the <name> placeholder in args.
# --------------------------------------------------------------------------- #
def test_arg_substitution(tmp_path):
    with TestClient(create_app(str(tmp_path))) as client:
        p = client.app.state.platform
        folder = str(tmp_path / "allowed")
        r = client.post(
            "/connectors/filesystem/connect", json={"values": {"folder": folder}}
        )
        assert r.status_code == 200, r.text

        cfg = _server(p, "filesystem")
        assert folder in cfg["args"]
        assert all("<folder>" not in a for a in cfg["args"])
        # A pure-arg connector mints no vault secret.
        assert "env_secrets" not in cfg


# --------------------------------------------------------------------------- #
# (7) A missing required field is a 400 (not a 500 / not a silent connect).
# --------------------------------------------------------------------------- #
def test_missing_required_field_400(tmp_path):
    with TestClient(create_app(str(tmp_path))) as client:
        p = client.app.state.platform
        r = client.post("/connectors/github/connect", json={"values": {}})
        assert r.status_code == 400, r.text
        assert r.json().get("detail")
        # Nothing was persisted.
        assert all(s.get("name") != "github" for s in (p.config.mcp_servers or []))


# --------------------------------------------------------------------------- #
# (8) An api_key connector routes through the connection registry.
# --------------------------------------------------------------------------- #
def test_api_key_connect(tmp_path):
    with TestClient(create_app(str(tmp_path))) as client:
        r = client.post("/connectors/pixio/connect", json={"values": {"key": "pxio_test"}})
        assert r.status_code == 200, r.text
        assert _row(client, "pixio")["connected"] is True


# --------------------------------------------------------------------------- #
# (9) Disconnect drops the server AND deletes the vault secret it minted.
# --------------------------------------------------------------------------- #
def test_disconnect_removes_server_and_secret(tmp_path):
    with TestClient(create_app(str(tmp_path))) as client:
        p = client.app.state.platform
        client.post(
            "/connectors/github/connect",
            json={"values": {"GITHUB_PERSONAL_ACCESS_TOKEN": "ghp_x"}},
        )
        sname = _server(p, "github")["env_secrets"]["GITHUB_PERSONAL_ACCESS_TOKEN"]
        assert p.secrets.get(sname) == "ghp_x"

        r = client.delete("/connectors/github")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is True and body["disconnected"] == "github"

        assert all(s.get("name") != "github" for s in (p.config.mcp_servers or []))
        assert p.registry.mcp_names("github") == []
        assert p.secrets.get(sname) is None  # the minted secret is gone


# --------------------------------------------------------------------------- #
# (10) Restart survival: a fresh app on the same root re-reports the connection
#      from persisted config, still pointing env_secrets at the vault.
# --------------------------------------------------------------------------- #
def test_restart_survival(tmp_path):
    root = str(tmp_path)
    with TestClient(create_app(root)) as client:
        p = client.app.state.platform
        client.post(
            "/connectors/github/connect",
            json={"values": {"GITHUB_PERSONAL_ACCESS_TOKEN": "ghp_x"}},
        )
        sname = _server(p, "github")["env_secrets"]["GITHUB_PERSONAL_ACCESS_TOKEN"]

    # Second boot on the SAME root.
    with TestClient(create_app(root)) as client2:
        p2 = client2.app.state.platform
        assert _row(client2, "github")["connected"] is True
        cfg2 = _server(p2, "github")
        assert cfg2["env_secrets"]["GITHUB_PERSONAL_ACCESS_TOKEN"] == sname
        assert p2.secrets.get(sname) == "ghp_x"  # vault survived too


# --------------------------------------------------------------------------- #
# (11) Unknown connector ids 404 on every verb.
# --------------------------------------------------------------------------- #
def test_unknown_connector_404(tmp_path):
    with TestClient(create_app(str(tmp_path))) as client:
        assert client.post("/connectors/nope/connect", json={"values": {}}).status_code == 404
        assert client.post("/connectors/nope/test").status_code == 404
        assert client.delete("/connectors/nope").status_code == 404


# --------------------------------------------------------------------------- #
# (12) OAuth connect with no embedded client fails honestly (4xx) and creates no
#      mcp server. google_drive has no public client id, so start_oauth raises a
#      ValueError, which the route maps to 400.
# --------------------------------------------------------------------------- #
def test_oauth_connect_without_client_is_4xx(tmp_path):
    with TestClient(create_app(str(tmp_path))) as client:
        p = client.app.state.platform
        r = client.post("/connectors/google_drive/connect", json={"values": {}})
        assert r.status_code in (400, 422), r.text
        assert r.json().get("detail")
        # An oauth failure must not leave a half-built mcp server behind.
        assert all(s.get("name") != "google_drive" for s in (p.config.mcp_servers or []))
