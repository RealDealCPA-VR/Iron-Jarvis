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

import hashlib
import json
import os
import re
import threading
from typing import Any

from ..core.config import persist_config_values
from ..mcp.tools import mcp_tools, unload_pack_tools
from .catalog import (
    CATALOG,
    LINK_CARDS,
    OLDER_PACKAGE_NOTE,
    choice_field,
    connector_dict,
    get_connector,
    is_remote,
    option_for,
    signin_env,
    signin_launch_id,
)


def _mcp_servers(platform) -> list[dict]:
    return list(getattr(platform.config, "mcp_servers", None) or [])


def _persist(platform):
    """The config writer the pack door is handed (v1.355.0, G44): this
    module's own ``persist_config_values``, so the Directory's doors write
    config.toml exactly as they always did."""

    def _write(keys: list[str]) -> None:
        cfg = platform.config
        persist_config_values(cfg.home, {k: getattr(cfg, k, None) for k in keys})

    return _write


def _server_cfg(platform, connector_id: str) -> "dict | None":
    return next((s for s in _mcp_servers(platform) if s.get("name") == connector_id), None)


#: v1.353.0 (G42): the gallery's group for the packs the user added outside
#: the catalog (pasted, imported, added on the Tools page). It read "Custom"
#: beside the words "Your own MCP server." on every such card.
USER_CATEGORY = "Added by you"

#: v1.353.0 (G43): OTHER names the SAME pack is installed under. The Tools
#: page's catalog adds Sequential Thinking as "sequentialthinking" while this
#: card's id is "sequential_thinking", so a running pack showed under Custom
#: beside its own card reading "not connected". A catalog card lights from the
#: first alias that is configured (only when its own id is not), and that pack
#: is not listed a second time. v1.357.0 (G82): read off the one catalog —
#: a card whose Tools row (``tools_id``) saves the pack under another name.
PACK_ALIASES: dict[str, tuple[str, ...]] = {
    c.id: (c.tools_id,) for c in CATALOG if c.tools_id and c.tools_id != c.id
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
                "detail": last_error or "the extension did not start",
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


#: v1.356.0 (G78/G60): what a saved pack the user SWITCHED OFF says on its
#: card — the words mcp/tools gives a switched-off pack (``PACK_OFF_REASON``),
#: never "isn't answering, press Retry" (nothing is wrong with it).
PACK_OFF_DETAIL = "It's switched off, so its tools are not loaded."


def pack_on(cfg: "dict | None") -> bool:
    """Whether a saved pack row is switched ON (v1.356.0): the loader's own
    reading (``mcp.tools.pack_enabled`` — absent ``enabled`` = on), so the
    Directory card and the loader can never disagree. A row that cannot be
    read counts as on, as every row saved before the switch existed is."""
    if not isinstance(cfg, dict):
        return True
    from ..mcp.tools import pack_enabled

    try:
        return bool(pack_enabled(cfg))
    except Exception:  # noqa: BLE001 — a status read never raises
        return True


def _off_status() -> dict[str, Any]:
    """The status of a saved pack the user switched off (v1.356.0): still a
    saved connection (``connected`` stays true, as for every saved pack),
    state ``off``, the plain words — no load record, no Retry."""
    from ..mcp import tools as _tools

    return {
        "connected": True,
        "status": "off",
        "detail": str(getattr(_tools, "PACK_OFF_REASON", "") or PACK_OFF_DETAIL),
        "reason": "",
        "fix": "",
        "stderr_tail": "",
    }


def signin_row(connector, cfg: "dict | None") -> "dict[str, str] | None":
    """What the card's Sign in press needs (v1.356.0, G60): ``{cli, label,
    account}`` — the Build set-up job to open (``POST /terminals/launch
    {cli}``), the press's words, and which kind of account it signs in.

    Offered only for an app that signs in on this PC, once it is SAVED, and
    only while the saved pack still runs exactly what that sign-in runs: the
    same command and arguments, the same settings (the account kind chosen +
    the card's fixed ones, nothing more, nothing vaulted). A pack changed by
    hand (Tools › Edit) gets no press rather than one that would ask
    Microsoft for a different sign-in than the pack uses. None otherwise."""
    if not getattr(connector, "signin_args", None) or not isinstance(cfg, dict):
        return None
    if str(cfg.get("command") or "") != connector.command:
        return None
    if [str(a) for a in (cfg.get("args") or [])] != list(connector.args):
        return None
    if cfg.get("env_secrets") or cfg.get("arg_secrets"):
        return None
    env = {str(k): str(v) for k, v in dict(cfg.get("env") or {}).items()}
    key, account = "", ""
    f = choice_field(connector)
    if f is not None:
        opt = option_for(f, env.get(f.name, ""))
        if opt is None:
            return None
        key, account = opt.key, opt.label
    if env != signin_env(connector, key):
        return None
    return {
        "cli": signin_launch_id(connector.id, key),
        "label": f"Sign in to {connector.signin_name or connector.name}",
        "account": account,
    }


#: v1.358.0 (lane 8D): the card status of a saved app that signs in on this
#: PC (Microsoft 365) whose tools loaded but whose OWN sign-in has not been
#: seen to land yet (``connectors.signin_seen``). ``connected`` stays true (the
#: app is saved, as for every saved pack); the card is never green on it.
STATUS_SIGNIN = "signin"


def signin_needed_line(connector) -> str:
    """What a saved app that is not signed in yet says on its card (v1.358.0,
    lane 8D): "Sign in to Microsoft to finish." - the same words as its
    Sign in press (``signin_row``'s label)."""
    return f"Sign in to {connector.signin_name or connector.name} to finish."


def signin_identity(cfg: "dict | None") -> str:
    """Which saved app a sign-in verdict is about (v1.358.0): its command,
    arguments and plain settings (the account kind is one of them), so an app
    saved again with another answer is asked again. Never a secret: a vaulted
    value is not in ``env``."""
    if not isinstance(cfg, dict):
        return ""
    try:
        return json.dumps(
            {
                "command": str(cfg.get("command") or ""),
                "args": [str(a) for a in (cfg.get("args") or [])],
                "env": {str(k): str(v) for k, v in dict(cfg.get("env") or {}).items()},
            },
            sort_keys=True,
        )
    except Exception:  # noqa: BLE001
        return ""


def _signin_state(platform, connector, server: str, cfg: "dict | None", row: dict[str, Any]) -> None:
    """v1.358.0 (lane 8D): a saved, switched-on app that signs in on this PC
    and whose tools loaded is GREEN only once its own sign-in check (or a
    successful call) says signed in (``connectors.signin_seen`` - cached, never
    blocks; a stale answer is re-checked behind this read). Until then the row
    reads ``status: "signin"`` with the plain line, so the card says "Sign in
    to Microsoft to finish" above the existing Sign in press - never
    "Connected" with 24 tools. ``signed_in`` rides the row either way."""
    if row.get("status") != "connected":
        return
    try:
        from . import signin_seen

        verdict = signin_seen.seen(platform, server, signin_identity(cfg))
    except Exception:  # noqa: BLE001 - unsure is "not signed in yet"
        verdict = None
    if verdict is not None and verdict.signed_in:
        row["signed_in"] = True
        if verdict.account:
            row["account"] = verdict.account
        return
    row["signed_in"] = False
    row["status"] = STATUS_SIGNIN
    row["detail"] = signin_needed_line(connector)


# --------------------------------------------------------------------------- #
# v1.358.0 (lane 8D): an API key is checked when it is saved.
# --------------------------------------------------------------------------- #
#: provider -> (fingerprint of the key that was checked, ok, the check's own
#: words). Process memory only; the key itself is never kept here - a short
#: hash tells "the key checked" from a key changed since (the Connections
#: page, the chat's card), whose verdict is then unknown again.
_KEY_CHECKS: dict[str, tuple[str, bool, str]] = {}
_KEY_LOCK = threading.Lock()


def _key_fingerprint(platform, provider: str) -> str:
    """A short hash of the key this provider holds right now ("" when none)
    - the same key the registry's Test probes (its own key, else Pixio's
    environment variable)."""
    try:
        cred = platform.connections.credential(provider) or ""
    except Exception:  # noqa: BLE001
        cred = ""
    if not cred and provider == "pixio":
        cred = os.environ.get("PIXIO_API_KEY", "").strip()
    if not cred:
        return ""
    return hashlib.sha256(str(cred).encode("utf-8")).hexdigest()[:16]


def note_key_check(platform, provider: str, result: "dict | None") -> None:
    """Remember what checking this provider's key just answered (the
    Directory's save and Test, and the Connections page's Test), so the
    Directory card says the same thing until the key changes. Never raises."""
    try:
        if not isinstance(result, dict):
            return
        fp = _key_fingerprint(platform, provider)
        if not fp:
            return
        with _KEY_LOCK:
            _KEY_CHECKS[provider] = (fp, bool(result.get("ok")), str(result.get("detail") or ""))
    except Exception:  # noqa: BLE001
        pass


def forget_key_check(provider: str) -> None:
    """Drop what was learned about a provider's key (it was removed)."""
    with _KEY_LOCK:
        _KEY_CHECKS.pop(provider, None)


def key_check(platform, provider: str) -> "tuple[bool, str] | None":
    """``(ok, the check's words)`` for the key this provider holds NOW, or
    None when that key was never checked here."""
    with _KEY_LOCK:
        entry = _KEY_CHECKS.get(provider)
    if entry is None:
        return None
    if entry[0] != _key_fingerprint(platform, provider):
        return None
    return entry[1], entry[2]


def key_check_line(connector, ok: bool, detail: str) -> str:
    """The card's sentence for a key check, in plain words (the probe's own
    text names the provider by its id and an HTTP code)."""
    name = connector.name
    if ok:
        return f"{name} accepted the key."
    low = (detail or "").lower()
    if "could not reach" in low or "probe failed" in low:
        return f"Couldn't reach {name} to check the key. Press Test to try again."
    if "probe returned http" in low:
        # v1.358.0 review: the service answered with an error that is not a
        # refusal (a 5xx, a 404 — connections/probe.py words only 401/403 as
        # "rejected the credential"), so the key may be fine; never tell the
        # user to remove it.
        return f"{name} couldn't check the key right now. Press Test to try again."
    return f"{name} didn't accept this key. Remove it and connect again with a working key."


def auto_approve_on(platform) -> bool:
    """Do pack tools run WITHOUT asking? The rule the boot resolver applies
    (platform.py: the global "Run app tools without asking" OR any pack's own
    flag — ``mcp_call`` is one shared permission key) and the one the Tools
    page's switch reads (``auto_approve_effective``)."""
    if bool(getattr(platform.config, "mcp_auto_approve", False)):
        return True
    from ..mcp.tools import pack_grants_auto

    return any(pack_grants_auto(s) for s in _mcp_servers(platform))


def pack_powers(platform, server: str, loaded: list[str], *, asks_first: bool) -> dict[str, Any]:
    """What a connected pack can actually DO (v1.355.0, G70), read off the
    live tools — never the catalog's hand-written scopes:

    ``can_change``  — how many of its loaded tools can change things (the
                      tool's own ``write_like``: an MCP ``readOnlyHint``
                      makes a tool read-only, anything else may write —
                      the same flag the quarantine and the Tools row use);
    ``asks_first``  — whether its tools ask before they run (no auto-approve,
                      :func:`auto_approve_on`);
    ``held_back``   — tools that arrived since it was connected and ask
                      until trusted, whatever ``asks_first`` says (v1.299.0);
    ``keyed``       — the pack holds a key or token the user can change
                      (the Directory's Change key).

    No separate permission model: write_like, quarantine and auto-approve are
    what the app already enforces."""
    can_change = 0
    held = 0
    for full in loaded:
        try:
            tool = platform.registry.get(full)
        except Exception:  # noqa: BLE001 — a count never breaks the gallery
            tool = None
        if bool(getattr(tool, "write_like", True)):
            can_change += 1
        if bool(getattr(tool, "quarantined", False)):
            held += 1
    cfg = _server_cfg(platform, server) or {}
    from ..mcp.pack_door import vault_refs

    keyed = bool(vault_refs(cfg)) or isinstance(cfg.get("auth"), dict)
    return {"can_change": can_change, "asks_first": bool(asks_first), "held_back": held, "keyed": keyed}


def _runs_replaced_package(cfg: "dict | None", connector) -> bool:
    """True when a saved pack row still launches a package this card
    ``replaces`` (an exact argument, or one with an ``@version`` suffix)."""
    replaced = list(getattr(connector, "replaces", None) or [])
    if not cfg or not replaced:
        return False
    for arg in cfg.get("args") or []:
        text = str(arg)
        if any(text == pkg or text.startswith(pkg + "@") for pkg in replaced):
            return True
    return False


def _status_for(
    platform,
    connector,
    conn_status: dict,
    *,
    runtime_cache: "dict[str, bool] | None" = None,
    memory_kinds: "set[str] | None" = None,
    asks_first: "bool | None" = None,
) -> dict[str, Any]:
    if connector.connect_via == "mcp":
        server = _configured_name(platform, connector.id)
        configured = server is not None
        cfg = _server_cfg(platform, server) if server else None
        # v1.356.0: a saved pack the user switched off says so (G78's switch
        # on the card reads `enabled`); one never saved reads as on.
        on = pack_on(cfg) if cfg is not None else True
        loaded = platform.registry.mcp_names(server) if server else []
        row: dict[str, Any] = {
            # CONNECTED MEANS REACHABLE (v1.172.0). This used to be "a config
            # entry exists", so a server whose tools failed to load at boot —
            # npx/uvx unresolved, a cold cache, a 15s timeout — reported a
            # confident green while the model could not call a single tool.
            # The one surface that could have revealed a dark wiki was the
            # surface insisting it was fine. Tools loaded IS the connection.
            **(
                _mcp_status(configured, len(loaded), server or connector.id)
                if on
                else _off_status()
            ),
            "tools_loaded": len(loaded),
            "tool_names": [n.split("__", 2)[-1] for n in loaded],
            "account": "",
            # v1.353.0 (G45): before Connect, the card says when this PC lacks
            # the helper program the pack starts with ("Needs Node.js"). A
            # remote server (v1.355.0, G44: GitHub's) launches nothing here.
            **_runtime_fields(connector.command, runtime_cache if runtime_cache is not None else {}),
            "enabled": on,
        }
        if getattr(connector, "signin_args", None):
            # v1.356.0 (G60): an app that signs in on this PC — what its Sign
            # in press opens (None until it is saved, or when the saved pack
            # no longer runs what the sign-in runs).
            row["signin"] = signin_row(connector, cfg)
            if server and on:
                # v1.358.0 (lane 8D): green only once it is signed in.
                _signin_state(platform, connector, server, cfg, row)
        if server:
            # v1.355.0 (G70): what the connected pack can actually do.
            row.update(
                pack_powers(
                    platform,
                    server,
                    loaded,
                    asks_first=(not auto_approve_on(platform)) if asks_first is None else asks_first,
                )
            )
        if server and _runs_replaced_package(cfg, connector):
            # v1.355.0 integrate (lane 5B note 4): a pack saved before the card
            # moved to its maker's own server (GitHub's) still runs the older
            # package and is never moved for the user; its card says so, and
            # says how to switch.
            # v1.356.0 integrate (G74): the card's button reads "Remove".
            row["package_note"] = (
                f"{OLDER_PACKAGE_NOTE} Remove it and connect again to use "
                f"{connector.name}'s own server."
            )
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
    if connector.connect_via == "api_key" and row["connected"]:
        # v1.358.0 (lane 8D): a key that was CHECKED and refused (on save, or
        # by a Test here or on the Connections page) is never a green card:
        # the row says so in the check's plain words until the key changes.
        checked = key_check(platform, connector.provider)
        if checked is not None:
            ok, raw = checked
            row["checked"] = True
            if not ok:
                row["status"] = "failed"
                row["detail"] = key_check_line(connector, False, raw)
                row["reason"] = row["detail"]
                row["technical"] = raw
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


def _user_mcp_connectors(
    platform, skip: "set[str] | None" = None, *, asks_first: "bool | None" = None
) -> list[dict[str, Any]]:
    """MCP servers the user added OUTSIDE the catalog (pasted configs, the
    /mcp routes) — established connections, so they belong in the gallery.
    ``skip``: names a catalog card already shows (an alias it lit, v1.353.0)."""
    catalog_ids = {c.id for c in CATALOG} | set(skip or ())
    if asks_first is None:
        asks_first = not auto_approve_on(platform)
    out: list[dict[str, Any]] = []
    for cfg in _mcp_servers(platform):
        name = str(cfg.get("name") or "").strip()
        if not name or name in catalog_ids:
            continue
        loaded = platform.registry.mcp_names(name)
        on = pack_on(cfg)
        out.append({
            **_entry_base(
                name, name, USER_CATEGORY, "🧩",
                user_pack_blurb(platform, name, loaded),
                "Its tools are available to chat and agents once toggled on.",
                "mcp",
            ),
            # Was hardcoded connected:true — a user-added server that loaded no
            # tools claimed a working connection (v1.172.0). v1.356.0: one the
            # user switched off says so instead of "isn't answering".
            **(_mcp_status(True, len(loaded), name) if on else _off_status()),
            "tools_loaded": len(loaded),
            "tool_names": [n.split("__", 2)[-1] for n in loaded],
            "account": "",
            # v1.355.0 (G70): an imported pack has no hand-written scopes; its
            # card says what its tools can do and whether they ask first.
            **pack_powers(platform, name, loaded, asks_first=asks_first),
            # v1.356.0: the pack's switch (G78), as the loader reads it.
            "enabled": on,
        })
    return out


#: Plain-English blurb per custom-LTM-source kind (memory connectors).
_MEMORY_BLURB = {
    # v1.353.0 (G42): no card says MCP.
    "mcp": "A memory base served by an extension you added.",
    "notion": "A Notion database as memory.",
    "markdown": "A markdown folder as memory.",
    "ssh": "A memory base on another computer, reached over SSH.",
    "http_rag": "Your own search service, as memory.",
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
                _MEMORY_BLURB.get(rec.kind, "A memory base you added."),
                "Grounds chat and agents with this memory; searchable from the"
                " Memory page and the chat's Tools › Connections switch.",
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
    # v1.355.0 (G70): read once per listing, the same answer on every card.
    asks_first = not auto_approve_on(platform)
    out = [
        {
            **connector_dict(c),
            **_status_for(
                platform,
                c,
                conn_status,
                runtime_cache=runtime_cache,
                memory_kinds=memory_kinds,
                asks_first=asks_first,
            ),
        }
        for c in CATALOG
    ]
    # A pack an alias lit is shown by its catalog card, never a second time.
    shown = {row["id"] for row in out}
    out += [_link_row(card) for card in LINK_CARDS]
    out += _user_mcp_connectors(platform, skip=shown, asks_first=asks_first)
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
    """The vault row a Directory app's secret field is kept under — the SAME
    name the one add door (mcp/pack_door.add_pack) gives it (v1.355.0, G44):
    ``mcp.<id>.<FIELD>`` for a value the pack reads as an env var, and
    ``mcp.<id>.auth.value`` for a remote server's token (its card's
    ``auth_field``). The chat's secure card (``app.<id>``,
    settings/credentials) and ``app_connect`` name it through here, and the
    Directory's Change key (``PATCH /mcp/servers/{id}``) writes the same row,
    so all three doors replace ONE token.

    Before v1.355.0 it was ``conn_<id>_<field>``: a pack saved then still
    names that row and keeps launching with it; settings/records points it at
    this name when a card stores a new token (``adopt_card_secrets``)."""
    from ..mcp.secrets import vault_name
    from . import catalog as _catalog

    conn = _catalog.get_connector(connector_id)
    if conn is not None and is_remote(conn) and conn.auth_field and conn.auth_field == field_name:
        return vault_name(connector_id, "auth.value")
    return vault_name(connector_id, field_name)


#: What a remote card's token header looks like (GitHub's own server reads
#: ``Authorization: Bearer <personal access token>``) — the add door's
#: defaults (``McpAuthBody``), said once.
REMOTE_AUTH_HEADER = "Authorization"
REMOTE_AUTH_FORMAT = "Bearer {value}"


def choice_refusal(f) -> str:
    """The sentence a choice field answered with something not on its list
    gets (v1.356.0, G60): its own question, then its answers by name."""
    answers = " or ".join(f'"{o.label}"' for o in f.options)
    return f"{f.label} Pick {answers}."


def pack_body(connector, values: dict[str, Any]):
    """The ``McpServerBody`` the one add door takes for a Directory card and
    the values the user typed (v1.355.0, G44). Secret fields ride as VALUES —
    the door vaults them (``secret_keys`` marks the ones whose names do not
    say so); an ``arg`` field fills its ``<name>`` placeholder (a secret
    inside it, a connection string's password, is vaulted by the door too).

    A REMOTE card (GitHub's server) is ``transport`` http + ``url``; its
    ``auth_field`` is the bearer token. Raises ``ValueError`` when the shape
    does not validate (the route answers it as a 400)."""
    from ..daemon.schemas import McpAuthBody, McpServerBody

    if is_remote(connector):
        url = connector.url
        headers: dict[str, str] = {}
        auth = None
        for f in connector.fields:
            val = str(values.get(f.name, "")).strip()
            if not val:
                continue
            if f.name == connector.auth_field:
                auth = McpAuthBody(header=REMOTE_AUTH_HEADER, format=REMOTE_AUTH_FORMAT, value=val)
            elif f.kind == "arg":
                url = url.replace(f"<{f.name}>", val)
            else:
                headers[f.name] = val
        return McpServerBody(
            name=connector.id, transport=connector.transport, url=url, headers=headers, auth=auth
        )
    args = list(connector.args)
    # v1.356.0 (G60): the settings the pack always starts with (Microsoft
    # 365's tool filter), then the fields' answers on top.
    env: dict[str, str] = dict(getattr(connector, "env", None) or {})
    for f in connector.fields:
        val = str(values.get(f.name, "")).strip()
        if not val:
            continue
        if f.options and option_for(f, val) is None:
            # v1.356.0 (G60): a choice is answered from its list, never typed
            # free (the value is what the pack reads — a guessed one would
            # sign in the wrong way).
            raise ValueError(choice_refusal(f))
        if f.kind == "arg":
            args = [a.replace(f"<{f.name}>", val) for a in args]
        else:  # env, or a secret the door vaults (injected as an env var at launch)
            env[f.name] = val
    return McpServerBody(name=connector.id, command=connector.command, args=args, env=env)


def _connect_mcp(platform, connector, values: dict[str, Any]) -> dict[str, Any]:
    missing = [
        f.label
        for f in connector.fields
        if not f.optional and not str(values.get(f.name, "")).strip()
    ]
    if missing:
        raise ValueError("missing required field(s): " + ", ".join(missing))

    # v1.355.0 (G44): THE ONE ADD DOOR (mcp/pack_door.add_pack, the body of
    # POST /mcp/servers): the vault split (env, argument, url and auth
    # secrets — v1.351.0's G12 argument rule included), the stored row, the
    # live load and the load record that names WHY it did not start. This
    # door used to keep its own copy, with its own vault names.
    # `replace=True`: connecting a card that is already saved is a RECONNECT —
    # the row is replaced, its old client closed (v1.342.0), and its manifest
    # KEPT so a write-like tool that arrived since is held back now
    # (v1.299.0). Disconnect + Connect is a clean first load (remove_pack
    # drops the manifest).
    from ..mcp.pack_door import PackDoorError, add_pack

    body = pack_body(connector, values)
    secret_keys = [f.name for f in connector.fields if f.kind == "secret"]
    try:
        out = add_pack(
            platform, body, persist=_persist(platform), secret_keys=secret_keys, replace=True
        )
    except PackDoorError as exc:  # the vault refused, a name the door refuses
        raise ValueError(exc.detail)
    loaded = int(out.get("tools_loaded") or 0)
    result: dict[str, Any] = {
        "ok": True,
        "connector": connector.id,
        "tools_loaded": loaded,
        "note": None if loaded else DID_NOT_START_NOTE,
    }
    if not loaded:
        # v1.355.0 (G44): the REAL reason rides the answer too (the load
        # record's plain words — a refused token, an unreachable server); the
        # card's status line shows the same record beside its Retry.
        try:
            from ..mcp.tools import load_status

            record = load_status(connector.id) or {}
        except Exception:  # noqa: BLE001
            record = {}
        if record.get("reason"):
            result["reason"] = str(record.get("reason") or "")
            result["fix"] = str(record.get("fix") or "")
    return result


def _connect_api_key(platform, connector, values: dict[str, Any]) -> dict[str, Any]:
    """Save the key, then CHECK it the way the Connections page does on save
    (v1.358.0, lane 8D): the registry's own Test (``connections.test`` - the
    live probe the platform wires, one cheap read with a short timeout; this
    runs on the threadpool, never on the loop). The answer carries ``test``
    ``{ok, detail}`` in plain words; a refused key is still saved (the user
    may fix it at the service) but its card reads "Needs attention" with
    those words, never green."""
    key = str(values.get("key") or values.get("api_key") or "").strip()
    if not key:
        raise ValueError("an API key is required")
    platform.connections.set_api_key(connector.provider, key)
    try:
        result = platform.connections.test(connector.provider)
    except Exception as exc:  # noqa: BLE001 - a check that broke is not a pass
        result = {"ok": False, "detail": f"probe failed ({type(exc).__name__})"}
    if not isinstance(result, dict):
        result = {"ok": False, "detail": "probe failed"}
    note_key_check(platform, connector.provider, result)
    ok = bool(result.get("ok"))
    return {
        "ok": True,
        "connector": connector.id,
        "test": {"ok": ok, "detail": key_check_line(connector, ok, str(result.get("detail") or ""))},
    }


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
            "error": failure.get("error") or "the extension did not start",
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
        result = _probe_mcp(platform, cfg)
        if result.get("ok") and getattr(connector, "signin_args", None) and pack_on(cfg):
            # v1.358.0 (lane 8D): its tools starting is not the app working -
            # Test asks the app's own sign-in check too (bounded), and says
            # so in the card's words when nobody has signed in yet.
            from . import signin_seen

            verdict = signin_seen.check_now(platform, server, signin_identity(cfg))
            if not verdict.signed_in:
                press = signin_row(connector, cfg)
                return {
                    **result,
                    "ok": False,
                    "error": signin_needed_line(connector),
                    "reason": "",
                    "fix": (
                        f"Press {press['label']} on this card and finish in the window it opens."
                        if press
                        else ""
                    ),
                }
            if verdict.account:
                result = {**result, "account": verdict.account}
        return result
    result = platform.connections.test(connector.provider)
    if connector.connect_via == "api_key":
        # v1.358.0 (lane 8D): the card keeps saying what this check said.
        note_key_check(platform, connector.provider, result)
    return result


def _disconnect_mcp_server(platform, server_id: str) -> dict[str, Any]:
    """Remove one MCP server (catalog or user-added) through THE ONE REMOVE
    DOOR (v1.355.0, G44: mcp/pack_door.remove_pack, the body of DELETE
    /mcp/servers/{name}): config row, every vault row, its manifest (so a
    connect after this is a FIRST load, never diffed against the pack the
    user removed), its live tools AND their client, its live client and its
    load record (a removed pack must not keep reading "ok")."""
    from ..mcp.pack_door import PackDoorError, forget_legacy_refs, remove_pack

    removed = _server_cfg(platform, server_id)
    try:
        from . import signin_seen

        signin_seen.forget(server_id)  # v1.358.0: a new connect is asked afresh
    except Exception:  # noqa: BLE001
        pass
    try:
        remove_pack(platform, server_id, persist=_persist(platform))
    except PackDoorError:
        # Nothing saved under that name: still unregister AND close anything
        # live under it (v1.342.0, lane D), as this door always did.
        unload_pack_tools(platform.registry, server_id)
    # A pack connected here before v1.355.0 names its token `conn_<id>_<field>`
    # (outside the `mcp.<id>.` rows the door sweeps): drop those too, as this
    # door always dropped the secrets it minted.
    forget_legacy_refs(platform, server_id, removed)
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
    forget_key_check(connector.provider)  # v1.358.0: no verdict outlives its key
    return {"ok": True, "disconnected": connector.id}
