"""Connector Marketplace service — turn a catalog entry into a live connection.

Dispatches by ``connect_via``:

* ``mcp``     — collect the connector's fields, store secret fields in the vault,
  add the MCP server (env tokens injected from the vault via ``env_secrets``),
  and hot-load its tools.
* ``oauth``   — start the OAuth flow through the :class:`ConnectionRegistry`.
* ``api_key`` — store the key through the registry.

:func:`list_connectors` returns the catalog annotated with each connector's LIVE
status. Everything is platform-based (no ``d`` deps object) so it is callable
from routes and tests alike. No secret value is ever returned.
"""

from __future__ import annotations

from typing import Any

from ..core.config import persist_config_values
from ..mcp.tools import mcp_tools
from .catalog import CATALOG, connector_dict, get_connector


def _mcp_servers(platform) -> list[dict]:
    return list(getattr(platform.config, "mcp_servers", None) or [])


def _server_cfg(platform, connector_id: str) -> "dict | None":
    return next((s for s in _mcp_servers(platform) if s.get("name") == connector_id), None)


def _status_for(platform, connector, conn_status: dict) -> dict[str, Any]:
    if connector.connect_via == "mcp":
        connected = _server_cfg(platform, connector.id) is not None
        loaded = platform.registry.mcp_names(connector.id) if connected else []
        return {
            "connected": connected,
            "status": "connected" if connected else "disconnected",
            "tools_loaded": len(loaded),
            "tool_names": [n.split("__", 2)[-1] for n in loaded],
            "account": "",
        }
    st = conn_status.get(connector.provider)
    if st:
        return {
            "connected": bool(st.get("connected")),
            "status": st.get("status", "disconnected"),
            "tools_loaded": 0,
            "account": st.get("account", ""),
        }
    return {"connected": False, "status": "disconnected", "tools_loaded": 0, "account": ""}


def _entry_base(id: str, name: str, category: str, glyph: str, blurb: str,
                unlocks: str, connect_via: str) -> dict[str, Any]:
    """The catalog-half shape (see catalog.connector_dict) for a DYNAMIC entry
    the curated catalog doesn't know about. Never includes secrets."""
    return {
        "id": id, "name": name, "category": category, "glyph": glyph,
        "blurb": blurb, "unlocks": unlocks, "connect_via": connect_via,
        "scopes": [], "docs_url": "", "fields": [], "provider": "",
        "source": "user",
    }


def _user_mcp_connectors(platform) -> list[dict[str, Any]]:
    """MCP servers the user added OUTSIDE the catalog (pasted configs, the
    /mcp routes) — established connections, so they belong in the gallery."""
    catalog_ids = {c.id for c in CATALOG}
    out: list[dict[str, Any]] = []
    for cfg in _mcp_servers(platform):
        name = str(cfg.get("name") or "").strip()
        if not name or name in catalog_ids:
            continue
        loaded = platform.registry.mcp_names(name)
        out.append({
            **_entry_base(
                name, name, "Custom", "🧩",
                "Your own MCP server.",
                "Its tools are available to chat and agents once toggled on.",
                "mcp",
            ),
            "connected": True,
            "status": "connected",
            "tools_loaded": len(loaded),
            "tool_names": [n.split("__", 2)[-1] for n in loaded],
            "account": "",
        })
    return out


#: Plain-English blurb per custom-LTM-source kind (memory connectors).
_MEMORY_BLURB = {
    "mcp": "An MCP-served memory brain.",
    "notion": "A Notion database as memory.",
    "markdown": "A markdown folder as memory.",
    "ssh": "A remote brain over SSH.",
    "http_rag": "An external RAG service.",
    "google_drive": "A Google Drive folder as memory.",
    "onedrive": "A OneDrive folder as memory.",
    "dropbox": "A Dropbox folder as memory.",
}


def _memory_connectors(platform) -> list[dict[str, Any]]:
    """Custom long-term-memory sources (incl. MCP-served brains) as connector
    entries — they ARE established connections, just to memory rather than
    tools, and were previously invisible in the gallery."""
    try:
        from ..ltm.sources import CustomSourceStore

        records = CustomSourceStore(platform.engine).list()
    except Exception:  # noqa: BLE001 — a broken store shouldn't blank the gallery
        return []
    live = set()
    try:
        live = set(platform.ltm.sources())
    except Exception:  # noqa: BLE001
        pass
    out: list[dict[str, Any]] = []
    for rec in records:
        connected = rec.name in live
        out.append({
            **_entry_base(
                rec.name, rec.name, "Memory", "🧠",
                _MEMORY_BLURB.get(rec.kind, "A custom memory source."),
                "Grounds chat and agents with this memory; searchable from the"
                " Memory page and the chat connector toggle.",
                "memory",
            ),
            "kind": rec.kind,
            "connected": connected,
            "status": "connected" if connected else "disconnected",
            "tools_loaded": 0,
            "account": "",
            "note": None if connected else
            "saved but not live — check its config, then restart the daemon",
        })
    return out


def list_connectors(platform) -> list[dict[str, Any]]:
    """The curated catalog PLUS the user's own established connections (custom
    MCP servers, memory sources), each annotated with live status. No secrets."""
    try:
        conn_status = {c["provider"]: c for c in platform.connections.status()}
    except Exception:  # noqa: BLE001 — a broken registry shouldn't blank the gallery
        conn_status = {}
    out = [{**connector_dict(c), **_status_for(platform, c, conn_status)} for c in CATALOG]
    out += _user_mcp_connectors(platform)
    out += _memory_connectors(platform)
    return out


# --------------------------------------------------------------------------- #
# Connect / test / disconnect.
# --------------------------------------------------------------------------- #
def connect(platform, connector_id: str, values: dict[str, Any] | None = None) -> dict[str, Any]:
    connector = get_connector(connector_id)
    if connector is None:
        raise KeyError(connector_id)
    values = values or {}
    if connector.connect_via == "mcp":
        return _connect_mcp(platform, connector, values)
    if connector.connect_via == "api_key":
        return _connect_api_key(platform, connector, values)
    if connector.connect_via == "oauth":
        return _connect_oauth(platform, connector)
    raise ValueError(f"unknown connect_via '{connector.connect_via}'")


def _secret_name(connector_id: str, field_name: str) -> str:
    return f"conn_{connector_id}_{field_name.lower()}"


def _connect_mcp(platform, connector, values: dict[str, Any]) -> dict[str, Any]:
    missing = [
        f.label
        for f in connector.fields
        if not f.optional and not str(values.get(f.name, "")).strip()
    ]
    if missing:
        raise ValueError("missing required field(s): " + ", ".join(missing))

    args = list(connector.args)
    env: dict[str, str] = {}
    env_secrets: dict[str, str] = {}
    for f in connector.fields:
        val = str(values.get(f.name, "")).strip()
        if not val:
            continue
        if f.kind == "arg":
            args = [a.replace(f"<{f.name}>", val) for a in args]
        elif f.kind == "env":
            env[f.name] = val
        else:  # secret → vault, injected as an env var at launch
            sname = _secret_name(connector.id, f.name)
            platform.secrets.set(sname, val)
            env_secrets[f.name] = sname

    cfg: dict[str, Any] = {"name": connector.id, "command": connector.command, "args": args}
    if env:
        cfg["env"] = env
    if env_secrets:
        cfg["env_secrets"] = env_secrets

    # Persist (replacing any prior config for this connector), then hot-load.
    servers = [s for s in _mcp_servers(platform) if s.get("name") != connector.id]
    servers.append(cfg)
    platform.config.mcp_servers = servers
    persist_config_values(platform.config.home, {"mcp_servers": servers})

    loaded = 0
    try:
        for tool in mcp_tools([cfg], secret_resolver=platform.secrets.get):
            platform.registry.register(tool, mcp=True)
            loaded += 1
    except Exception:  # noqa: BLE001 — persisted config still loads on restart
        loaded = 0
    return {
        "ok": True,
        "connector": connector.id,
        "tools_loaded": loaded,
        "note": None if loaded else "saved — restart the daemon (or check the command is installed) to load its tools",
    }


def _connect_api_key(platform, connector, values: dict[str, Any]) -> dict[str, Any]:
    key = str(values.get("key") or values.get("api_key") or "").strip()
    if not key:
        raise ValueError("an API key is required")
    platform.connections.set_api_key(connector.provider, key)
    return {"ok": True, "connector": connector.id}


def _connect_oauth(platform, connector) -> dict[str, Any]:
    info = platform.connections.start_oauth(connector.provider)
    return {"ok": True, "connector": connector.id, "oauth": info}


def _memory_record(platform, connector_id: str):
    """The custom-LTM-source record named ``connector_id`` (or None)."""
    try:
        from ..ltm.sources import CustomSourceStore

        return CustomSourceStore(platform.engine).get(connector_id)
    except Exception:  # noqa: BLE001
        return None


def test(platform, connector_id: str) -> dict[str, Any]:
    connector = get_connector(connector_id)
    if connector is None:
        # Dynamic entries: a user-added MCP server tests exactly like a
        # catalog MCP connector; a memory source runs a tiny live search.
        cfg = _server_cfg(platform, connector_id)
        if cfg is not None:
            try:
                tools = mcp_tools([cfg], secret_resolver=platform.secrets.get)
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "tools": []}
            names = [t.name.split("__", 2)[-1] for t in tools]
            return {
                "ok": bool(tools),
                "count": len(tools),
                "tools": names,
                "error": None if tools else "connected but advertised no tools",
            }
        if _memory_record(platform, connector_id) is not None:
            conn = platform.ltm.get(connector_id)
            if conn is None:
                return {"ok": False, "error": "saved but not live — restart the daemon"}
            try:
                conn.search("memory", k=1)
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            return {"ok": True, "kind": "memory"}
        raise KeyError(connector_id)
    if connector.connect_via == "mcp":
        cfg = _server_cfg(platform, connector.id)
        if cfg is None:
            return {"ok": False, "error": "not connected yet"}
        try:
            tools = mcp_tools([cfg], secret_resolver=platform.secrets.get)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "tools": []}
        names = [t.name.split("__", 2)[-1] for t in tools]
        return {
            "ok": bool(tools),
            "count": len(tools),
            "tools": names,
            "error": None if tools else "connected but advertised no tools",
        }
    return platform.connections.test(connector.provider)


def _disconnect_mcp_server(platform, server_id: str) -> dict[str, Any]:
    """Remove one MCP server (catalog or user-added): config row, live tools,
    and any vault secrets minted for it."""
    servers = _mcp_servers(platform)
    removed = _server_cfg(platform, server_id)
    kept = [s for s in servers if s.get("name") != server_id]
    platform.config.mcp_servers = kept
    persist_config_values(platform.config.home, {"mcp_servers": kept})
    for name in platform.registry.mcp_names(server_id):
        platform.registry.unregister(name)
    if removed:  # drop the vault secrets we minted for it
        for sname in (removed.get("env_secrets") or {}).values():
            try:
                platform.secrets.delete(sname)
            except Exception:  # noqa: BLE001
                pass
    return {"ok": True, "disconnected": server_id}


def disconnect(platform, connector_id: str) -> dict[str, Any]:
    connector = get_connector(connector_id)
    if connector is None:
        # Dynamic entries: a user-added MCP server tears down like a catalog
        # one; a memory source is removed from the store AND deregistered live.
        if _server_cfg(platform, connector_id) is not None:
            return _disconnect_mcp_server(platform, connector_id)
        if _memory_record(platform, connector_id) is not None:
            from ..ltm.sources import CustomSourceStore

            CustomSourceStore(platform.engine).remove(connector_id)
            try:
                platform.ltm.deregister(connector_id)
            except Exception:  # noqa: BLE001 — the row is gone either way
                pass
            return {"ok": True, "disconnected": connector_id}
        raise KeyError(connector_id)
    if connector.connect_via == "mcp":
        return _disconnect_mcp_server(platform, connector.id)
    platform.connections.disconnect(connector.provider)
    return {"ok": True, "disconnected": connector.id}
