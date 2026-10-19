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

import re
from typing import Any

from ..core.config import persist_config_values
from ..mcp.tools import mcp_tools, unload_pack_tools
from .catalog import CATALOG, LINK_CARDS, connector_dict, get_connector


def _mcp_servers(platform) -> list[dict]:
    return list(getattr(platform.config, "mcp_servers", None) or [])


def _server_cfg(platform, connector_id: str) -> "dict | None":
    return next((s for s in _mcp_servers(platform) if s.get("name") == connector_id), None)


#: v1.353.0 (G42): the gallery's group for the packs the user added outside
#: the catalog (pasted, imported, added on the Tools page). It read "Custom"
#: beside the words "Your own MCP server." on every such card.
USER_CATEGORY = "Added by you"

#: v1.353.0 (G43): OTHER names the SAME pack is installed under. The Tools
#: page's prebuilt catalog (daemon/app.py) adds Sequential Thinking as
#: "sequentialthinking" while this catalog's id is "sequential_thinking", so a
#: running pack showed under Custom beside its own card reading "not
#: connected". A catalog card lights from the first alias that is configured
#: (only when its own id is not), and that pack is not listed a second time.
PACK_ALIASES: dict[str, tuple[str, ...]] = {
    "sequential_thinking": ("sequentialthinking",),
}


def _configured_name(platform, connector_id: str) -> "str | None":
    """The ``mcp_servers`` name a catalog connector runs under: its own id
    when configured, else the first configured alias, else None."""
    if _server_cfg(platform, connector_id) is not None:
        return connector_id
    for alias in PACK_ALIASES.get(connector_id, ()):
        if _server_cfg(platform, alias) is not None:
            return alias
    return None


#: v1.353.0 (G45): the helper program a launcher command needs, in plain
#: words ("Needs Node.js"), and where to get it. Keyed by the command's bare
#: name (no folder, no .cmd/.exe).
_RUNTIMES: dict[str, tuple[str, str]] = {
    "npx": ("Node.js", "https://nodejs.org"),
    "node": ("Node.js", "https://nodejs.org"),
    "npm": ("Node.js", "https://nodejs.org"),
    "uvx": ("uv", "https://docs.astral.sh/uv/"),
    "uv": ("uv", "https://docs.astral.sh/uv/"),
    "bunx": ("Bun", "https://bun.sh"),
    "python": ("Python", "https://www.python.org/downloads/"),
    "python3": ("Python", "https://www.python.org/downloads/"),
    "pipx": ("pipx", "https://pipx.pypa.io"),
    "docker": ("Docker Desktop", "https://www.docker.com/products/docker-desktop/"),
}


def _runtime_for(command: str) -> tuple[str, str]:
    """``(plain name, where to get it)`` for a launcher command."""
    cmd = str(command or "").strip()
    base = cmd.lower().rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    for ext in (".cmd", ".exe"):
        base = base.removesuffix(ext)
    return _RUNTIMES.get(base, (cmd, ""))


def _launcher_found(command: str) -> bool:
    """Would the launcher find this stdio command right now? The launcher's
    OWN resolution (``mcp.tools.resolve_launcher``: the real PATH, then the
    per-user bin folders a GUI-started app misses) — the same answer
    ``launcher_ready`` gives the Tools catalog and the import preview, read
    here without importing a route module. Any fault is False, as there."""
    from ..mcp.tools import resolve_launcher

    try:
        resolve_launcher(command)
        return True
    except Exception:  # noqa: BLE001 — not found / unreadable folder = not ready
        return False


def _runtime_fields(command: str, cache: dict[str, bool]) -> dict[str, Any]:
    """``runtime_ready`` / ``runtime`` / ``runtime_url`` for a catalog MCP row
    (v1.353.0, G45): would this PC start the pack's helper program RIGHT NOW?
    Read once per command per listing (``cache``), never once per card."""
    cmd = str(command or "").strip()
    if not cmd:
        return {}
    if cmd not in cache:
        cache[cmd] = _launcher_found(cmd)
    name, url = _runtime_for(cmd)
    return {"runtime_ready": cache[cmd], "runtime": name, "runtime_url": url}


#: v1.351.0 (G08): the plain words for a saved pack with ZERO tools and no
#: load record saying why, and for a connect whose pack did not start. The
#: Directory card shows them beside its own Test and Retry, and Retry reloads
#: the pack in place: they used to say "restart the daemon", which was never
#: the fix a user could reach from that card.
NO_TOOLS_DETAIL = "Saved, but it isn't answering yet. Press Test, then Retry."
DID_NOT_START_NOTE = "Saved, but it didn't start. Press Retry."
#: A saved memory source that is not live: it loads at start-up (no Retry).
MEMORY_NOT_LIVE_NOTE = (
    "Saved, but it isn't live yet. Check its settings on the Memory page, "
    "then restart Iron Jarvis."
)


def _mcp_status(configured: bool, tools_loaded: int, name: str = "") -> dict[str, Any]:
    """The honest states of an MCP connector (v1.172.0; ``failed`` v1.342.0).

    ``disconnected``  — no config entry; the user never added it.
    ``failed``        — configured, zero tools, and the pack's LOAD RECORD
                        says why (it did not start, or its calls fail): the
                        row carries ``reason``, ``fix`` and the child's masked
                        last output (``stderr_tail``), ``detail`` = the raw
                        error. Read off ``mcp.tools.load_status`` when a
                        ``name`` is given — one record, one opinion.
    ``no_tools``      — the connection EXISTS but zero tools are loaded and no
                        record says why, so the model can call NOTHING through
                        it. This is the state a flat "connected" badge used to
                        hide: a server whose command failed to resolve
                        (npx/uvx cold, PATH missing in the frozen daemon, a
                        timeout) stayed dark with a confident green light.
    ``connected``     — configured AND at least one tool is callable.

    ``connected`` deliberately stays TRUE for ``no_tools`` and ``failed``: the
    user's connection really does exist and survives restarts (that is what
    ``/connectors/{id}/connect`` persisted), and flipping it would tell them
    their connect FAILED — a different lie. The truth lives in ``status`` +
    ``tools_loaded``, which the Connections page renders as its own state.
    """
    if not configured:
        return {"connected": False, "status": "disconnected", "detail": ""}
    if tools_loaded <= 0:
        record: dict[str, Any] = {}
        if name:
            try:
                from ..mcp.tools import load_status

                record = dict(load_status(name) or {})
            except Exception:  # noqa: BLE001 — a status read never raises
                record = {}
        last_error = str(record.get("last_error") or "")
        if last_error or record.get("state") in ("failed", "failing"):
            return {
                "connected": True,
                "status": "failed",
                "detail": last_error or "the pack did not start",
                "reason": str(record.get("reason") or ""),
                "fix": str(record.get("fix") or ""),
                "stderr_tail": str(record.get("stderr_tail") or ""),
            }
        # v1.351.0 (G08): the card under this detail carries Test AND Retry,
        # and Retry reloads the pack in place — "restart" was never the fix.
        return {
            "connected": True,
            "status": "no_tools",
            "detail": NO_TOOLS_DETAIL,
            "reason": "",
            "fix": "",
            "stderr_tail": "",
        }
    return {"connected": True, "status": "connected", "detail": ""}


def _status_for(
    platform,
    connector,
    conn_status: dict,
    *,
    runtime_cache: "dict[str, bool] | None" = None,
    memory_kinds: "set[str] | None" = None,
) -> dict[str, Any]:
    if connector.connect_via == "mcp":
        server = _configured_name(platform, connector.id)
        configured = server is not None
        loaded = platform.registry.mcp_names(server) if server else []
        row: dict[str, Any] = {
            # CONNECTED MEANS REACHABLE (v1.172.0). This used to be "a config
            # entry exists", so a server whose tools failed to load at boot —
            # npx/uvx unresolved, a cold cache, a 15s timeout — reported a
            # confident green while the model could not call a single tool.
            # The one surface that could have revealed a dark wiki was the
            # surface insisting it was fine. Tools loaded IS the connection.
            **_mcp_status(configured, len(loaded), server or connector.id),
            "tools_loaded": len(loaded),
            "tool_names": [n.split("__", 2)[-1] for n in loaded],
            "account": "",
            # v1.353.0 (G45): before Connect, the card says when this PC lacks
            # the helper program the pack starts with ("Needs Node.js").
            **_runtime_fields(connector.command, runtime_cache if runtime_cache is not None else {}),
        }
        if server and server != connector.id:
            # v1.353.0 (G43): lit by an ALIAS. The row takes the pack's real
            # name as its id, because every door the card and the chat use —
            # Test, Retry (/mcp/servers/{name}/reload), Disconnect, the chat's
            # connector toggle (registry.mcp_names(id)) — works by that name.
            row["id"] = server
            row["catalog_id"] = connector.id
        return row
    st = conn_status.get(connector.provider)
    if st:
        row = {
            "connected": bool(st.get("connected")),
            "status": st.get("status", "disconnected"),
            "tools_loaded": 0,
            "account": st.get("account", ""),
        }
    else:
        row = {"connected": False, "status": "disconnected", "tools_loaded": 0, "account": ""}
    if connector.connect_via == "oauth":
        # v1.353.0 (G45): could a sign-in START right now? The registry's own
        # answer (the same lookup start_oauth makes); the card says the
        # one-time developer setup BEFORE Connect while it is False, instead
        # of only after the press failed. A row the registry did not give
        # reads as not configured (the press would fail the same way).
        configured = bool(st.get("oauth_client_configured")) if st else False
        row["oauth_client_configured"] = configured
        row["needs"] = "" if (configured or row["connected"]) else connector.needs
        if memory_kinds is not None and connector.provider in _CLOUD_DRIVE_KINDS:
            # v1.353.0 (G45): a signed-in drive searches NOTHING until a
            # memory base points at one of its folders; the card says so.
            row["memory_source"] = connector.provider in memory_kinds
    return row


#: The OAuth drives whose files reach chat only through a memory base
#: (``ltm.sources.CLOUD_DRIVE_KINDS``; repeated here so a listing never
#: imports the LTM package just to name three providers — pinned equal).
_CLOUD_DRIVE_KINDS: tuple[str, ...] = ("google_drive", "onedrive", "dropbox")


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


#: v1.353.0 (G42): what a pack added by the user says about itself on its card
#: — the first sentence of its first two tools' own descriptions plus the
#: count. Bounds keep a pack's long (or hostile) text from filling the card;
#: React renders it as text, never markup.
_BLURB_SENTENCES = 2
_BLURB_SENTENCE_MAX = 110
#: A description that names the mechanism (the registry's own fallback reads
#: "Remote MCP tool 'x' from server 'y'.") is skipped: no card says MCP.
_MECHANISM_RX = re.compile(r"\bMCP\b", re.IGNORECASE)
#: Markdown-ish noise at the start of a description line.
_LEAD_NOISE_RX = re.compile(r"^[\s#>*_`\-]+")
#: Where a first sentence ends: ". " / "! " / "? " or a line break.
_SENTENCE_END_RX = re.compile(r"(?<=[.!?])\s|\n")


def _first_sentence(text: str) -> str:
    raw = _LEAD_NOISE_RX.sub("", str(text or "")).strip()
    if not raw:
        return ""
    first = _SENTENCE_END_RX.split(raw, maxsplit=1)[0].strip()
    first = re.sub(r"\s+", " ", first).replace("`", "").replace("**", "")
    if len(first) > _BLURB_SENTENCE_MAX:
        first = first[: _BLURB_SENTENCE_MAX - 1].rstrip() + "…"
    elif first and first[-1] not in ".!?…":
        first += "."
    return first


def user_pack_blurb(platform, name: str, loaded: list[str]) -> str:
    """The card blurb of a pack the user added (v1.353.0, G42): what its tools
    say they do, then how many there are — "Read a file. Search the web. 6
    tools." A pack with no tools yet reads "A tool you added." (its status
    line says why). Never raises; secret-shaped text is masked."""
    n = len(loaded)
    if n == 0:
        return "A tool you added."
    sentences: list[str] = []
    for tool_name in loaded:
        if len(sentences) >= _BLURB_SENTENCES:
            break
        try:
            tool = platform.registry.get(tool_name)
            desc = str(getattr(tool, "description", "") or "")
        except Exception:  # noqa: BLE001 — a blurb never breaks the gallery
            desc = ""
        sentence = _first_sentence(desc)
        if not sentence or _MECHANISM_RX.search(sentence) or sentence in sentences:
            continue
        sentences.append(sentence)
    count = f"{n} tool{'' if n == 1 else 's'}."
    text = " ".join([*sentences, count])
    try:
        from ..detections.redact import mask

        text = mask(text)
    except Exception:  # noqa: BLE001
        pass
    return text


def _user_mcp_connectors(platform, skip: "set[str] | None" = None) -> list[dict[str, Any]]:
    """MCP servers the user added OUTSIDE the catalog (pasted configs, the
    /mcp routes) — established connections, so they belong in the gallery.
    ``skip``: names a catalog card already shows (an alias it lit, v1.353.0)."""
    catalog_ids = {c.id for c in CATALOG} | set(skip or ())
    out: list[dict[str, Any]] = []
    for cfg in _mcp_servers(platform):
        name = str(cfg.get("name") or "").strip()
        if not name or name in catalog_ids:
            continue
        loaded = platform.registry.mcp_names(name)
        out.append({
            **_entry_base(
                name, name, USER_CATEGORY, "🧩",
                user_pack_blurb(platform, name, loaded),
                "Its tools are available to chat and agents once toggled on.",
                "mcp",
            ),
            # Was hardcoded connected:true — a user-added server that loaded no
            # tools claimed a working connection (v1.172.0).
            **_mcp_status(True, len(loaded), name),
            "tools_loaded": len(loaded),
            "tool_names": [n.split("__", 2)[-1] for n in loaded],
            "account": "",
        })
    return out


#: Plain-English blurb per custom-LTM-source kind (memory connectors).
_MEMORY_BLURB = {
    # v1.353.0 (G42): no card says MCP.
    "mcp": "A memory brain served by a tool you added.",
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
            # v1.351.0 (G08): plain words, no "daemon". A memory source is
            # loaded at start-up and has no Retry, so a restart IS its fix.
            "note": None if connected else MEMORY_NOT_LIVE_NOTE,
        })
    return out


def _memory_kinds(platform) -> "set[str] | None":
    """The kinds of the user's memory bases (``google_drive`` …), or None when
    the store cannot be read — then no drive card claims "no folder yet"."""
    try:
        from ..ltm.sources import CustomSourceStore

        return {str(rec.kind) for rec in CustomSourceStore(platform.engine).list()}
    except Exception:  # noqa: BLE001 — unknown is not "none"
        return None


def _link_row(card) -> dict[str, Any]:
    """A LINK card's row (v1.353.0, G43): never connected here; its one action
    opens ``href``."""
    return {**connector_dict(card), "connected": False, "status": "disconnected",
            "tools_loaded": 0, "account": ""}


def list_connectors(platform) -> list[dict[str, Any]]:
    """The curated catalog PLUS the user's own established connections (custom
    MCP servers, memory sources), each annotated with live status. No secrets."""
    try:
        conn_status = {c["provider"]: c for c in platform.connections.status()}
    except Exception:  # noqa: BLE001 — a broken registry shouldn't blank the gallery
        conn_status = {}
    runtime_cache: dict[str, bool] = {}
    memory_kinds = _memory_kinds(platform)
    out = [
        {
            **connector_dict(c),
            **_status_for(
                platform, c, conn_status, runtime_cache=runtime_cache, memory_kinds=memory_kinds
            ),
        }
        for c in CATALOG
    ]
    # A pack an alias lit is shown by its catalog card, never a second time.
    shown = {row["id"] for row in out}
    out += [_link_row(card) for card in LINK_CARDS]
    out += _user_mcp_connectors(platform, skip=shown)
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

    # v1.351.0 (G12): a value typed into an ARGUMENT (Postgres's
    # `postgresql://me:password@host/db`) goes to the vault like every other
    # pack's argument secret — config.toml holds the masked text, the pack
    # launches with the real one (`_build_transport` resolves `arg_secrets`).
    # Before, this door stored it plain until the next boot's migration.
    from ..mcp.secrets import split_args, store_arg_secrets

    args, arg_values = split_args(args)
    arg_secrets = (
        store_arg_secrets(platform.secrets, connector.id, arg_values) if arg_values else {}
    )

    cfg: dict[str, Any] = {"name": connector.id, "command": connector.command, "args": args}
    if arg_secrets:
        cfg["arg_secrets"] = arg_secrets
    if env:
        cfg["env"] = env
    if env_secrets:
        cfg["env_secrets"] = env_secrets

    # Persist (replacing any prior config for this connector), then hot-load.
    servers = [s for s in _mcp_servers(platform) if s.get("name") != connector.id]
    servers.append(cfg)
    platform.config.mcp_servers = servers
    persist_config_values(platform.config.home, {"mcp_servers": servers})

    # v1.342.0 (lane D): a RECONNECT replaces the pack's live tools. Registering
    # the fresh ones over the old names used to orphan the old client — its
    # stdio child ran on until restart (the v1.328.1 leak, by another door).
    # Unload what is there, closing its client, before the fresh load.
    unload_pack_tools(platform.registry, connector.id)
    loaded = 0
    try:
        # `home=` (v1.299.0): a reconnect diffs the pack against its manifest, so
        # a write-like tool that arrived since is registered quarantined NOW,
        # not at the next boot. The two probes below stay record=False.
        for tool in mcp_tools(
            [cfg], secret_resolver=platform.secrets.get, home=platform.config.home
        ):
            platform.registry.register(tool, mcp=True)
            loaded += 1
    except Exception:  # noqa: BLE001 — persisted config still loads on restart
        loaded = 0
    return {
        "ok": True,
        "connector": connector.id,
        "tools_loaded": loaded,
        "note": None if loaded else DID_NOT_START_NOTE,
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


def _probe_mcp(platform, cfg: dict[str, Any]) -> dict[str, Any]:
    """Connect to ONE MCP server config right now and report (v1.342.0).

    A probe: ``record=False`` registers nothing and leaves the load record
    alone; ``errors`` carries the REAL failure back (its error, reason, fix
    and the child's last output), so a pack that did not start is reported
    as itself — "advertised no tools" only when it connected and listed none.
    """
    name = str(cfg.get("name") or "")
    errors: dict[str, dict[str, Any]] = {}
    try:
        tools = mcp_tools([cfg], secret_resolver=platform.secrets.get, record=False, errors=errors)
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "count": 0,
            "tools": [],
            "error": f"{type(exc).__name__}: {exc}",
            "reason": "",
            "fix": "",
            "stderr_tail": "",
        }
    failure = errors.get(name) if name else next(iter(errors.values()), None)
    if failure is not None:
        return {
            "ok": False,
            "count": 0,
            "tools": [],
            "error": failure.get("error") or "the pack did not start",
            "reason": failure.get("reason") or "",
            "fix": failure.get("fix") or "",
            "stderr_tail": failure.get("stderr_tail") or "",
        }
    names = [t.name.split("__", 2)[-1] for t in tools]
    return {
        "ok": bool(tools),
        "count": len(tools),
        "tools": names,
        "error": None if tools else "connected but advertised no tools",
        "reason": "",
        "fix": "",
        "stderr_tail": "",
    }


def test(platform, connector_id: str) -> dict[str, Any]:
    """Prove a connector's config RIGHT NOW. A probe: it registers nothing,
    so it passes ``record=False`` and leaves the MCP load record to the loads
    that actually hand tools to the registry (boot, connect, reload)."""
    connector = get_connector(connector_id)
    if connector is None:
        # Dynamic entries: a user-added MCP server tests exactly like a
        # catalog MCP connector; a memory source runs a tiny live search.
        cfg = _server_cfg(platform, connector_id)
        if cfg is not None:
            return _probe_mcp(platform, cfg)
        if _memory_record(platform, connector_id) is not None:
            conn = platform.ltm.get(connector_id)
            if conn is None:
                return {"ok": False, "error": MEMORY_NOT_LIVE_NOTE}
            try:
                conn.search("memory", k=1)
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            return {"ok": True, "kind": "memory"}
        raise KeyError(connector_id)
    if connector.connect_via == "mcp":
        # v1.353.0 (G43): the pack may run under an alias (PACK_ALIASES).
        server = _configured_name(platform, connector.id)
        cfg = _server_cfg(platform, server) if server else None
        if cfg is None:
            return {"ok": False, "error": "not connected yet"}
        return _probe_mcp(platform, cfg)
    return platform.connections.test(connector.provider)


def _disconnect_mcp_server(platform, server_id: str) -> dict[str, Any]:
    """Remove one MCP server (catalog or user-added): config row, live tools,
    and any vault secrets minted for it."""
    servers = _mcp_servers(platform)
    removed = _server_cfg(platform, server_id)
    kept = [s for s in servers if s.get("name") != server_id]
    platform.config.mcp_servers = kept
    persist_config_values(platform.config.home, {"mcp_servers": kept})
    # v1.342.0 (lane D): unregister AND close — like DELETE /mcp/servers/{name}
    # since v1.328.1; a bare unregister left the stdio child running.
    unload_pack_tools(platform.registry, server_id)
    if removed:  # drop the vault secrets we minted for it
        for sname in (removed.get("env_secrets") or {}).values():
            try:
                platform.secrets.delete(sname)
            except Exception:  # noqa: BLE001
                pass
        # v1.351.0 (G12): and every `mcp.<server>.*` row — argument, url,
        # header and auth secrets — the same sweep DELETE /mcp/servers/{name}
        # does (names only in the log); they used to stay in the vault.
        from ..mcp.secrets import delete_pack_secrets

        try:
            delete_pack_secrets(platform.secrets, server_id, removed)
        except Exception:  # noqa: BLE001 — the pack is gone either way
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
        # v1.353.0 (G43): the pack the card shows, even under an alias.
        return _disconnect_mcp_server(platform, _configured_name(platform, connector.id) or connector.id)
    platform.connections.disconnect(connector.provider)
    return {"ok": True, "disconnected": connector.id}
