"""THE door every pack is added and removed through (v1.355.0, G44).

v1.343.0 made ``routes/agents.add_mcp_pack`` / ``remove_mcp_pack`` the one
add/remove path for ``POST /mcp/servers``, the import door and the npm door.
Two other doors kept their own copies: the Directory's Connect / Disconnect
(``connectors/service``) and the chat's ``app_connect`` / its Undo
(``settings/records.apply_app``). Their removals skipped the pack's manifest,
its live client and its load record — so a pack disconnected from the
Directory and connected again was DIFFED against the manifest of the pack the
user had removed, and a removed pack could keep reading "ok" — and their adds
wrote their own config shapes and vault names.

The bodies of those two route functions live HERE now, platform-based, so the
services can call them: a service must never import a route module (pinned in
``tests/test_mcp_close_on_disconnect_v1342.py``), which is why they could not
simply call the routes. The routes keep their names and answers byte for byte
(they wrap :func:`add_pack` / :func:`remove_pack` and turn a
:class:`PackDoorError` into the ``HTTPException`` they always raised).

* :func:`add_pack` — a ``McpServerBody`` in: name rule, the vault split (env /
  args / url / headers / auth; ``secret_keys`` marks env keys that are secrets
  even when their names do not say so — a Directory field of kind "secret"),
  the stored row, the live load and the note. ``replace=True`` is a RECONNECT:
  the row is replaced in place, the pack's manifest is KEPT (a write-like tool
  that arrived since is held back — v1.299.0), and vault rows the old row
  named that the new one does not are deleted.
* :func:`remove_pack` — the row, every vault row of the pack, its manifest
  (so a re-add is a FIRST load), its live tools AND their client, its live
  client and its load record.
* :func:`install_config` — a stored row that already names its vault rows
  (``app_connect`` saves the pack before the secure card stores the token;
  an Undo puts a prior row back): put it in place, persist it, (re)load it.
* :func:`load_config` — unload (closing the client) and load one pack through
  the loader boot uses, the failure kept in ``errors``.

Nothing here logs, returns or raises a VALUE — names only.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Iterable

from ..core.logging import get_logger

log = get_logger("mcp.pack_door")

#: ``persist(["mcp_servers"])`` writes those config keys to config.toml.
Persist = Callable[[list[str]], None]

#: The stored-row keys that name a vault row (and the vault-name lists inside
#: them): what a reconnect compares to find rows the new config no longer uses.
_VAULT_REF_KEYS = ("env_secrets", "header_secrets", "arg_secrets")


class PackDoorError(Exception):
    """A refusal from the door: ``status_code`` + one sentence (``detail``),
    exactly what the route answered as an ``HTTPException`` before."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = int(status_code)
        self.detail = detail


def persist_for(platform: Any) -> Persist:
    """The default writer: the named config keys to ``<home>/config.toml``.
    ``core.config.persist_config_values`` is looked up when it runs (a test
    that patches it on the module keeps the door off the disk)."""

    def _persist(keys: list[str]) -> None:
        from ..core import config as _config

        cfg = platform.config
        _config.persist_config_values(cfg.home, {k: getattr(cfg, k, None) for k in keys})

    return _persist


def _servers(platform: Any) -> list[dict[str, Any]]:
    return list(getattr(platform.config, "mcp_servers", None) or [])


def vault_refs(cfg: dict[str, Any] | None) -> list[str]:
    """Every vault NAME a stored row points at (env, header, argument, url
    and auth secrets) — prefixed or not, in the row's order, no repeats."""
    names: list[str] = []
    row = dict(cfg or {})
    for key in _VAULT_REF_KEYS:
        for value in dict(row.get(key) or {}).values():
            if value and str(value) not in names:
                names.append(str(value))
    for value in (row.get("url_secret"), (row.get("auth") or {}).get("secret") if isinstance(row.get("auth"), dict) else None):
        if value and str(value) not in names:
            names.append(str(value))
    return names


# --------------------------------------------------------------------------- #
# Load / install.
# --------------------------------------------------------------------------- #
def load_config(
    platform: Any, cfg: dict[str, Any], *, errors: "dict[str, dict[str, Any]] | None" = None
) -> int:
    """(Re)load ONE pack's tools live: unregister AND close what it had, then
    connect through the loader boot uses (``home``: the manifest is written on
    a first load and diffed on a reload). Returns how many tools registered;
    a failure is in ``errors[name]`` and on the pack's load record. Never
    raises. BLOCKING (a connect with a timeout)."""
    from . import tools as _tools

    name = str(cfg.get("name") or "")
    if not _tools.pack_enabled(cfg):
        # v1.356.0 (G78): a row the user switched off is saved, never loaded
        # (an Undo putting a switched-off row back, a reconnect of one): its
        # live tools, client and load record go; nothing fails.
        _tools.switch_off_pack(platform.registry, name)
        return 0
    _tools.unload_pack_tools(platform.registry, name)
    errs: dict[str, dict[str, Any]] = errors if errors is not None else {}
    loaded = 0
    try:
        for tool in _tools.mcp_tools(
            [cfg],
            secret_resolver=platform.secrets.get,
            home=platform.config.home,
            errors=errs,
        ):
            platform.registry.register(tool, mcp=True)
            loaded += 1
    except Exception as exc:  # noqa: BLE001 — the row is saved; say why it did not start
        # The text rides the add answer's note (the browser): secret VALUES
        # masked, as the per-pack load failure masks them (mcp.tools).
        text = _tools._mask_text(f"{type(exc).__name__}: {exc}")
        errs.setdefault(name, {"error": text, "reason": text})
        log.warning("pack %s: tools did not load (%s)", name, type(exc).__name__)
        loaded = 0
    return loaded


def _server_identity(cfg: dict[str, Any]) -> tuple[str, str, str, str]:
    """Which server a row launches or reaches: its transport, its address,
    and (stdio) its command and the package it runs (the first argument that
    is not a flag). A changed folder or key is the SAME server."""
    transport = str(cfg.get("transport") or "stdio").lower()
    if transport in ("http", "sse"):
        return (transport, str(cfg.get("url") or ""), "", "")
    package = next((str(a) for a in (cfg.get("args") or []) if not str(a).startswith("-")), "")
    return ("stdio", "", str(cfg.get("command") or ""), package)


def _forget_manifest(platform: Any, name: str) -> bool:
    """Delete ``<home>/mcp/manifests/<name>.json`` (the next load is a first
    load). Never raises."""
    try:
        from .manifest import PackManifest

        path = PackManifest(platform.config.home).path(name)
        if path.is_file():
            path.unlink()
            return True
    except (OSError, AttributeError, TypeError) as exc:
        log.warning("MCP pack %r: manifest could not be removed: %s", name, type(exc).__name__)
    return False


def install_config(
    platform: Any,
    cfg: dict[str, Any],
    *,
    persist: Persist | None = None,
    load: bool = True,
    errors: "dict[str, dict[str, Any]] | None" = None,
) -> int:
    """Put a stored row in place (replacing the row of that name where it
    stood, else at the end), persist it, and — unless ``load`` is False, "save
    only", which leaves the live tools as they are — reload it
    (:func:`load_config`). Returns the tools loaded."""
    name = str(cfg.get("name") or "")
    servers = _servers(platform)
    idx = next((i for i, s in enumerate(servers) if isinstance(s, dict) and s.get("name") == name), None)
    if idx is None:
        servers.append(cfg)
    else:
        if _server_identity(servers[idx]) != _server_identity(cfg):
            # v1.355.0 integrate (lane 5B note 4): the row now names a
            # DIFFERENT server (an older GitHub pack's stdio package replaced
            # by GitHub's own http server). Its tools are not the old
            # server's, so the old manifest would quarantine every write tool
            # of the server the user just chose; a new server is a first load.
            _forget_manifest(platform, name)
        servers[idx] = cfg
    platform.config.mcp_servers = servers
    (persist or persist_for(platform))(["mcp_servers"])
    return load_config(platform, cfg, errors=errors) if load else 0


# --------------------------------------------------------------------------- #
# Add.
# --------------------------------------------------------------------------- #
def add_pack(
    platform: Any,
    body: Any,
    *,
    persist: Persist | None = None,
    secret_keys: Iterable[str] = (),
    replace: bool = False,
    enabled: bool = True,
) -> dict[str, Any]:
    """Register an external MCP server (persisted; loaded live best-effort,
    guaranteed on the next restart). The body of ``routes/agents.
    add_mcp_pack`` (v1.343.0), lifted here unchanged in what it stores and
    answers; raises :class:`PackDoorError` where the route raised
    ``HTTPException``.

    ``secret_keys`` (v1.355.0): env keys that go to the vault even when their
    NAME does not say secret (the Directory's Box "client id" field is kind
    "secret"). ``replace`` (v1.355.0): a reconnect — see the module doc.

    ``enabled`` (v1.356.0, G78): ``False`` saves the pack SWITCHED OFF (the
    import door, for a server its app had turned off and the user ticked):
    the row and its vault secrets are stored exactly as an add stores them,
    plus ``enabled = false``, and nothing is loaded. The answer then carries
    ``enabled: false`` and a note saying so; an ordinary add's answer is
    unchanged byte for byte.
    """
    persist = persist or persist_for(platform)
    name = (body.name or "").strip()
    # v1.343.0 (lane J): the npm/GitHub door (a body naming a `launcher` or
    # a `package`) gets the contract's name rule applied HERE, not only in
    # the browser: `safe_name` (dots and spaces become "-", lowercase, a
    # letter first, 40 chars), falling back to the package's own tail when
    # the typed name leaves nothing. A name that still fails the rule is a
    # 422 that says the rule. Every other door keeps its 400 as before.
    npm_door = bool(body.launcher or body.package)
    if npm_door:
        from .import_config import NAME_RX as _NAME_RX
        from .import_config import bare_package as _bare_package
        from .import_config import safe_name as _safe_name

        name = _safe_name(name)
        if not name and body.package:
            tail = _bare_package(body.package).rsplit("/", 1)[-1].split("@", 1)[0]
            name = _safe_name(tail)
        if not _NAME_RX.match(name):
            raise PackDoorError(
                422,
                "an extension's name starts with a letter and uses letters, digits, "
                "- or _ (at most 40 characters)",
            )
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9_-]{0,39}$", name):
        raise PackDoorError(400, "invalid server name")
    is_http = body.transport in ("http", "sse")
    if not is_http and not (body.command or "").strip():
        raise PackDoorError(400, "command is required")
    servers = _servers(platform)
    old_row = next((s for s in servers if isinstance(s, dict) and s.get("name") == name), None)
    if old_row is not None and not replace:
        raise PackDoorError(400, f"server '{name}' already exists")
    # v1.342.0 (lane C): an env KEY that names a secret (SECRET_KEY_RX)
    # goes to the vault as `mcp.<name>.<KEY>`; the stored row keeps only
    # the plain entries in `env` and the vault NAMES in `env_secrets`
    # (resolved at spawn by `_build_transport`). The catalog, npm and
    # suggest doors all POST here, so this is the one split. A vault that
    # refuses the write refuses the add — a secret never lands in
    # config.toml as a fallback.
    # v1.343.0 (lane H): an http/sse pack splits the same way — a HEADER
    # whose name matches SECRET_KEY_RX goes to the vault as
    # `mcp.<name>.header.<H>` (the row keeps `header_secrets[H]`, resolved
    # at launch beside `env_secrets`), and the auth VALUE goes to
    # `mcp.<name>.auth.value` (the row keeps `auth.secret`, the shape
    # `_build_transport` has resolved since the first HTTP pack). A stdio
    # body is stored exactly as before, byte for byte.
    from .secrets import is_secret_key as _is_secret_key
    from .secrets import split_args as _split_args
    from .secrets import split_env as _split_env
    from .secrets import split_url as _split_url
    from .secrets import store_arg_secrets as _store_arg_secrets
    from .secrets import store_env_secrets as _store_env_secrets
    from .secrets import store_url_secret as _store_url_secret
    from .secrets import vault_name as _vault_name

    plain_env, secret_values = _split_env(name, body.env or {})
    # v1.355.0 (G44): a key the CALLER knows is a secret is one, whatever its
    # name says (an empty value is dropped, as the split drops it).
    for key in [str(k) for k in secret_keys]:
        if key in plain_env:
            value = plain_env.pop(key)
            if value:
                secret_values[key] = value
    # v1.351.0 (G12): a secret INSIDE an argument (an mcp-remote block's
    # `--header "Authorization: Bearer …"`, `--api-key=…`, `--api-key …`, a
    # connection string's password) or a url (`?api_key=…`) is kept in the
    # vault too: the stored arg / url is the MASKED text, `arg_secrets[i]` /
    # `url_secret` name the vault rows, and `_build_transport` puts the real
    # text back at launch. Every pasted, imported, typed or Directory pack
    # comes through here, so this is the one split for them all.
    shown_args, arg_values = ([], {}) if is_http else _split_args([str(a) for a in body.args])
    shown_url, url_value = _split_url(body.url) if is_http else ("", "")
    plain_headers: dict[str, str] = {}
    header_secret_values: dict[str, str] = {}
    if is_http:
        for header_name, value in dict(body.headers or {}).items():
            h = str(header_name).strip()
            v = "" if value is None else str(value)
            if not h:
                continue
            if _is_secret_key(h):
                if v:
                    header_secret_values[h] = v
            else:
                plain_headers[h] = v
    auth_value = (body.auth.value if (is_http and body.auth is not None) else "") or ""
    secrets = platform.secrets
    # Every vault write happens BEFORE the row is built: a refusal
    # refuses the add, naming the KEYS only.
    try:
        env_secrets = _store_env_secrets(secrets, name, secret_values)
        header_secrets: dict[str, str] = {}
        for h, v in header_secret_values.items():
            sname = _vault_name(name, f"header.{h}")
            secrets.set(sname, v, kind="api_key", description=f"MCP pack {name}: header {h}")
            header_secrets[h] = sname
        auth_secret = ""
        if auth_value:
            auth_secret = _vault_name(name, "auth.value")
            secrets.set(
                auth_secret,
                auth_value,
                kind="api_key",
                description=f"MCP pack {name}: {body.auth.header} value",
            )
        arg_secrets = _store_arg_secrets(secrets, name, arg_values)
        url_secret = _store_url_secret(secrets, name, url_value) if url_value else ""
    except Exception as exc:  # noqa: BLE001 — names only, never the value
        refused = sorted(secret_values) + sorted(
            f"header {h}" for h in header_secret_values
        ) + (["the auth value"] if auth_value else []) + [
            f"argument {i + 1}" for i in sorted(arg_values)
        ] + (["the url"] if url_value else [])
        raise PackDoorError(
            503,
            f"the vault could not store {', '.join(refused)} "
            f"({type(exc).__name__}); the extension was not added",
        )
    if is_http:
        cfg = {
            "name": name,
            "transport": body.transport,
            "url": shown_url,
            **({"url_secret": url_secret} if url_secret else {}),
            **({"headers": plain_headers} if plain_headers else {}),
            **({"header_secrets": header_secrets} if header_secrets else {}),
            **(
                {
                    "auth": {
                        "header": body.auth.header.strip(),
                        "format": body.auth.format,
                        "secret": auth_secret,
                    }
                }
                if auth_secret
                else {}
            ),
            **({"auto_approve": True} if body.auto_approve else {}),
        }
    else:
        cfg = {
            "name": name,
            "command": body.command.strip(),
            "args": shown_args,
            **({"arg_secrets": arg_secrets} if arg_secrets else {}),
            "env": plain_env,
            **({"env_secrets": env_secrets} if env_secrets else {}),
            **({"cwd": body.cwd} if body.cwd else {}),
            **({"auto_approve": True} if body.auto_approve else {}),
        }
    if not enabled:
        # v1.356.0 (G78): saved switched off — `install_config` stores it and
        # `load_config` loads nothing for it.
        cfg["enabled"] = False
    # Best-effort LIVE load so its tools appear without a restart. Registered
    # with mcp=True so the ``mcp:*`` sentinel reaches them from the agent loop
    # and they survive the next restart (boot re-registers the same way).
    # v1.342.0: the loader says WHY a pack did not start (`errors`), so
    # the note names the reason instead of "restart the daemon".
    # `home` (v1.299.0): a FIRST load writes the pack's manifest — everything
    # it advertises now is what the user is connecting; a reconnect
    # (`replace`) is diffed against it.
    errors: dict[str, dict[str, Any]] = {}
    loaded = install_config(platform, cfg, persist=persist, load=True, errors=errors)
    if old_row is not None:
        # A reconnect: vault rows the old row named and the new one does not
        # are the old pack's leftovers (a renamed key, an old Directory
        # `conn_<id>_<field>` name, a secret argument that is gone).
        stale = [n for n in vault_refs(old_row) if n not in vault_refs(cfg)]
        for sname in stale:
            try:
                secrets.delete(sname)
            except Exception as exc:  # noqa: BLE001 — the row no longer names it
                log.warning("MCP pack %r: vault row %r could not be deleted: %s", name, sname, type(exc).__name__)
    # auto_approve is applied when the resolver is built at boot, so a server
    # added live with auto_approve won't be trusted by headless agents until
    # the next restart — be honest about that.
    note = None
    if not enabled:
        note = PACK_SAVED_OFF_NOTE
    elif not loaded:
        failure = errors.get(name)
        if failure is not None:
            reason = failure.get("reason") or failure.get("error") or "it did not start"
            note = f"saved, but it did not start: {reason} — see its row for the fix"
        else:
            note = "saved — it connected but advertised no tools; see its row"
    elif body.auto_approve:
        note = "loaded — restart Iron Jarvis so autonomous agents may use it without asking"
    return {
        "name": name,
        "added": True,
        "tools_loaded": loaded,
        "auto_approve": bool(body.auto_approve),
        "note": note,
        # v1.342.0: what was kept where — key NAMES only.
        "env_keys": sorted(plain_env),
        "secret_keys": sorted(env_secrets),
        # v1.343.0: the http shape, names only.
        "transport": body.transport,
        "header_names": sorted(list(plain_headers) + list(header_secrets)),
        "auth_set": bool(auth_secret),
        # v1.343.0 (lane J): the npm door's effective launcher (the one named,
        # else the guess); absent for every other door.
        **({"launcher": body.launcher} if npm_door else {}),
        # v1.356.0 (G78): present only for a pack saved switched off.
        **({"enabled": False} if not enabled else {}),
    }


# --------------------------------------------------------------------------- #
# On / off (v1.356.0, G78).
# --------------------------------------------------------------------------- #
#: The add answer's note for a pack saved switched off (the import door).
PACK_SAVED_OFF_NOTE = "saved switched off, as it was in the app it came from; turn it on to use it"


def set_pack_enabled(
    platform: Any,
    name: str,
    enabled: bool,
    *,
    persist: Persist | None = None,
) -> dict[str, Any]:
    """Switch a configured pack ON or OFF without losing it (v1.356.0, G78).

    OFF: the row stays where it is with ``enabled = false`` (persisted), every
    vault row it names and its manifest stay untouched, and the live half of
    a removal runs (``mcp.tools.switch_off_pack``): its tools are unregistered
    and their client closed — so chat, agents and an ``mcp:*`` allowlist lose
    them at once — its live client is forgotten and its load record dropped
    (nothing reads "failed"). The loader skips it from now on, boot included.

    ON: the ``enabled`` key is taken off the row (absent = on, the stored row
    is byte for byte what it was before the switch) and the pack is reloaded
    through the SAME reload a Retry uses (``mcp.tools.reload_pack``) — the
    stored vault names resolve at launch, so no token is typed again; the
    manifest is re-diffed (a tool that arrived meanwhile is held back).

    Switching to the state a pack is already in changes nothing. Raises
    ``PackDoorError(404)`` for an unknown name. BLOCKING when it turns a pack
    on (a connect with a timeout): call it off the event loop."""
    from . import tools as _tools

    persist = persist or persist_for(platform)
    servers = _servers(platform)
    idx = next(
        (i for i, s in enumerate(servers) if isinstance(s, dict) and s.get("name") == name),
        None,
    )
    if idx is None:
        raise PackDoorError(404, "no such server")
    row = dict(servers[idx])
    was_on = _tools.pack_enabled(row)
    want = bool(enabled)
    if was_on == want:
        out: dict[str, Any] = {"name": name, "enabled": want, "changed": False}
        if not want:
            out["tools_unloaded"] = 0
        return out
    if want:
        row.pop("enabled", None)
    else:
        row["enabled"] = False
    servers[idx] = row
    platform.config.mcp_servers = servers
    persist(["mcp_servers"])
    if not want:
        unloaded = _tools.switch_off_pack(platform.registry, name)
        log.info("MCP pack %r switched off (%d tools unloaded)", name, unloaded)
        return {"name": name, "enabled": False, "changed": True, "tools_unloaded": unloaded}
    result = _tools.reload_pack(platform, name, home=platform.config.home)
    log.info("MCP pack %r switched on (%d tools loaded)", name, int(result.get("tools_loaded") or 0))
    return {"name": name, "enabled": True, "changed": True, **result}


# --------------------------------------------------------------------------- #
# Remove.
# --------------------------------------------------------------------------- #
def remove_pack(platform: Any, name: str, *, persist: Persist | None = None) -> dict[str, Any]:
    """Remove a configured pack: its config row, its vault rows
    (``mcp.<name>.*``), its manifest, its live tools and their client, its
    live client and its load record. The body of ``routes/agents.
    remove_mcp_pack`` (v1.343.0), lifted here; raises
    ``PackDoorError(404)`` for an unknown name. Its answer is the route's,
    byte for byte."""
    persist = persist or persist_for(platform)
    servers = _servers(platform)
    kept = [s for s in servers if s.get("name") != name]
    if len(kept) == len(servers):
        raise PackDoorError(404, "no such server")
    removed_cfg = next((s for s in servers if s.get("name") == name), None)
    platform.config.mcp_servers = kept
    persist(["mcp_servers"])
    # v1.342.0 (lane C): the pack's vault rows (`mcp.<name>.*`) and its
    # manifest (`<home>/mcp/manifests/<name>.json`) go with it, so a
    # re-add is a FIRST load again (every tool trusted, no quarantine
    # diff against a pack the user removed) and no orphaned secret
    # outlives the row that named it.
    from .manifest import PackManifest as _PackManifest
    from .secrets import delete_pack_secrets as _delete_pack_secrets

    secrets_removed = _delete_pack_secrets(getattr(platform, "secrets", None), name, removed_cfg)
    manifest_removed = False
    try:
        _manifest_path = _PackManifest(platform.config.home).path(name)
        if _manifest_path.is_file():
            _manifest_path.unlink()
            manifest_removed = True
    except OSError as exc:
        log.warning("MCP pack %r: manifest could not be removed: %s", name, type(exc).__name__)
    # Unregister its live tools NOW so they disappear from every agent's
    # loadout immediately (they won't come back on restart either — the
    # config no longer lists the server).
    unloaded = 0
    removed = []
    for tool_name in platform.registry.mcp_names(name):
        live = platform.registry.get(tool_name)
        if platform.registry.unregister(tool_name):
            unloaded += 1
            if live is not None:
                removed.append(live)
    # v1.328.1: and CLOSE their client, like a Retry closes what it
    # replaces. Unregistering alone left the pack's stdio child running
    # until the app restarted, with no surface left that could stop it.
    from .tools import close_tools_clients

    close_tools_clients(removed)
    # v1.324.0: its prompts and resources go with it ("/" and "@").
    from .tools import forget_live_client, forget_load_record

    forget_live_client(name)
    # v1.350.2: and its load record — a removed pack that had finished
    # loading kept reading as "ok" (the background loader only forgot it for
    # a pack deleted WHILE starting).
    forget_load_record(name)
    return {
        "removed": name,
        "tools_unloaded": unloaded,
        "secrets_removed": len(secrets_removed),
        "manifest_removed": manifest_removed,
    }


def forget_legacy_refs(platform: Any, name: str, cfg: dict[str, Any] | None) -> list[str]:
    """Delete the vault rows a REMOVED pack's row named OUTSIDE its
    ``mcp.<name>.`` prefix — the ``conn_<id>_<field>`` names the Directory and
    ``app_connect`` gave a token before v1.355.0, which :func:`remove_pack`'s
    prefix sweep does not reach. Names only in the log; never raises. Called
    by the Directory's Disconnect and the chat's Undo, which always dropped
    the secrets they minted."""
    from .secrets import vault_prefix

    secrets = getattr(platform, "secrets", None)
    removed: list[str] = []
    if secrets is None or not cfg:
        return removed
    prefix = vault_prefix(name)
    for sname in vault_refs(cfg):
        if sname.startswith(prefix):
            continue
        try:
            secrets.delete(sname)
            removed.append(sname)
        except Exception as exc:  # noqa: BLE001 — the pack is gone either way
            log.warning("MCP pack %r: vault row %r could not be deleted: %s", name, sname, type(exc).__name__)
    return removed


__all__ = [
    "PACK_SAVED_OFF_NOTE",
    "PackDoorError",
    "add_pack",
    "forget_legacy_refs",
    "install_config",
    "load_config",
    "persist_for",
    "remove_pack",
    "set_pack_enabled",
    "vault_refs",
]
