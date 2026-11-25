"""AN APP THAT STARTS SLOWLY, FAILS ONCE OR CHANGES ITS TOOLS KEEPS WORKING
(v1.577.0).

Three things, each bounded and each never raising into the daemon:

1. RETRY WITH BACKOFF. A pack whose start failed at boot used to hold no tools
   until the user pressed Retry. :func:`retry_failed_packs` tries it again in
   the background after :data:`RETRY_DELAYS_S` (10 s, 30 s, 90 s, 5 min).
   A failed attempt is SILENT (a probe: no new load record, no second bell);
   a successful one loads it through the same ``reload_pack`` the Retry
   button uses. It stops for a pack the user removed, switched off or
   retried themselves.
2. START ON FIRST USE (an app's own ``lazy`` key, opt-in). The tool list of
   every successful load is kept (``<home>/mcp/tool-cache/<name>.json``,
   names, descriptions and schemas only: never a value). A ``lazy`` app is
   not started at boot when its list is kept: its tools are registered from
   the list, and the program starts on the first call (the transports start
   their server on the first request). A ``lazy`` app whose start FAILS is
   registered the same way, so a call later tries again.
3. ITS TOOLS CHANGED. A server that sends ``notifications/tools/list_changed``
   has its list read again over the SAME connection and the registry updated
   (new tools registered, gone ones removed, the hold-back re-applied);
   nothing is restarted.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable

from ..core.logging import get_logger

log = get_logger("mcp")

RETRY_DELAYS_S: tuple[float, ...] = (10.0, 30.0, 90.0, 300.0)
#: A burst of list_changed notifications is read once, after this quiet.
REFRESH_DEBOUNCE_S = 1.0

LIST_CHANGED = "notifications/tools/list_changed"


# --------------------------------------------------------------------------- #
# the kept tool list
# --------------------------------------------------------------------------- #
def _safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in str(name))[:120] or "mcp"


def cache_path(home: "str | Path", name: str) -> Path:
    return Path(home) / "mcp" / "tool-cache" / f"{_safe(name)}.json"


def connection_sig(cfg: Any) -> str:
    """A digest of HOW the app is started (command, args, url, transport):
    a kept list is used only for the same connection. Values of secrets are
    not in a row (they are vault names), and the digest is one-way."""
    if not isinstance(cfg, dict):
        return ""
    shape = {k: cfg.get(k) for k in ("command", "args", "url", "transport", "cwd")}
    blob = json.dumps(shape, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:24]


def _clean_spec(spec: dict[str, Any]) -> dict[str, Any]:
    keep = {}
    for key in ("name", "title", "description", "inputSchema", "annotations"):
        if key in spec:
            keep[key] = spec[key]
    return keep


def save_specs(home: "str | Path | None", cfg: dict[str, Any], specs: list[dict[str, Any]],
               instructions: str = "") -> None:
    """Keep a successful load's tool list. Never raises."""
    if home is None:
        return
    try:
        path = cache_path(home, cfg.get("name") or "mcp")
        path.parent.mkdir(parents=True, exist_ok=True)
        doc = {
            "sig": connection_sig(cfg),
            "at": time.time(),
            "instructions": str(instructions or "")[:8000],
            "tools": [_clean_spec(s) for s in specs if isinstance(s, dict) and s.get("name")],
        }
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    except Exception as exc:  # noqa: BLE001 — a cache is never worth a failed load
        log.debug("tool list of %r not kept: %s", cfg.get("name"), exc)


def load_specs(home: "str | Path | None", cfg: dict[str, Any]) -> "list[dict[str, Any]] | None":
    """The kept tool list for this app AND this connection, else None."""
    if home is None:
        return None
    try:
        doc = json.loads(cache_path(home, cfg.get("name") or "mcp").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — none kept, or unreadable: none
        return None
    if not isinstance(doc, dict) or doc.get("sig") != connection_sig(cfg):
        return None
    tools = doc.get("tools")
    return [t for t in tools if isinstance(t, dict)] if isinstance(tools, list) else None


def forget_specs(home: "str | Path | None", name: str) -> None:
    if home is None:
        return
    try:
        cache_path(home, name).unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        pass


def is_lazy(cfg: Any) -> bool:
    """The app's own ``lazy`` key: start it on first use."""
    if not isinstance(cfg, dict):
        return False
    value = cfg.get("lazy")
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


# --------------------------------------------------------------------------- #
# retry with backoff
# --------------------------------------------------------------------------- #
def _cfg(platform: Any, name: str) -> "dict[str, Any] | None":
    for cfg in list(getattr(platform.config, "mcp_servers", None) or []):
        if isinstance(cfg, dict) and cfg.get("name") == name:
            return cfg
    return None


def _probe(platform: Any, cfg: dict[str, Any]) -> bool:
    """Does the app start now? A probe: nothing recorded, nothing kept."""
    from .tools import close_tools_clients, mcp_tools

    errors: dict[str, Any] = {}
    tools = mcp_tools([cfg], secret_resolver=platform.secrets.get, record=False, errors=errors)
    close_tools_clients(tools)
    return bool(tools)


async def retry_failed_packs(
    platform: Any,
    names: "list[str]",
    *,
    delays: "tuple[float, ...]" = RETRY_DELAYS_S,
    sleep: "Callable[[float], Any]" = asyncio.sleep,
) -> dict[str, bool]:
    """Try each pack in *names* again after each delay until it starts.
    Returns ``{name: started}``. A pack is left alone once it is removed,
    switched off, or holds tools (the user's Retry won). Cancellable."""
    from .tools import pack_enabled, reload_pack

    pending = [n for n in dict.fromkeys(names or []) if n]
    done: dict[str, bool] = {n: False for n in pending}
    for delay in delays:
        if not pending:
            break
        await sleep(delay)
        still: list[str] = []
        for name in pending:
            cfg = _cfg(platform, name)
            if cfg is None or not pack_enabled(cfg) or platform.registry.mcp_names(name):
                continue
            try:
                started = await asyncio.to_thread(_probe, platform, cfg)
            except Exception:  # noqa: BLE001
                started = False
            if started:
                try:
                    result = await asyncio.to_thread(
                        reload_pack, platform, name, home=platform.config.home
                    )
                    started = bool(result.get("ok"))
                except Exception:  # noqa: BLE001
                    started = False
            if started:
                done[name] = True
                log.info("app %r started on a later try", name)
            else:
                still.append(name)
        pending = still
    return done


def failed_after_boot(summary: dict[str, Any]) -> list[str]:
    """The packs a boot load summary says failed with no tools."""
    out = []
    for row in (summary or {}).get("packs") or []:
        if isinstance(row, dict) and row.get("state") == "failed" and not row.get("tools_loaded"):
            out.append(str(row.get("name") or ""))
    return [n for n in out if n]


# --------------------------------------------------------------------------- #
# the tool list changed
# --------------------------------------------------------------------------- #
#: Set by the platform at start: ``() -> platform`` (the registry, config and
#: secrets a refresh needs). None: list_changed is only logged.
_PLATFORM: list[Callable[[], Any]] = []
_TIMERS: dict[str, threading.Timer] = {}
_TIMERS_LOCK = threading.Lock()


def bind(get_platform: "Callable[[], Any] | None") -> None:
    _PLATFORM[:] = [get_platform] if get_platform is not None else []


def watch(client: Any, name: str) -> None:
    """Listen for the app's ``tools/list_changed`` on *client* (a client
    without listeners — an older transport, a double — is left as it is)."""
    add = getattr(client, "add_listener", None)
    if not callable(add):
        return
    try:
        add(LIST_CHANGED, lambda _msg, _n=name: schedule_refresh(_n))
    except Exception:  # noqa: BLE001
        log.debug("cannot listen to %r's tool changes", name, exc_info=True)


def schedule_refresh(name: str, delay: float = REFRESH_DEBOUNCE_S) -> None:
    """Read the app's tools again after *delay* of quiet (a debounce), on a
    worker thread: never on the transport's own reader."""
    def _run() -> None:
        with _TIMERS_LOCK:
            _TIMERS.pop(name, None)
        try:
            platform = _PLATFORM[0]() if _PLATFORM else None
            if platform is not None:
                refresh_pack_tools(platform, name)
        except Exception:  # noqa: BLE001
            log.debug("refreshing %r's tools failed", name, exc_info=True)

    with _TIMERS_LOCK:
        old = _TIMERS.pop(name, None)
        if old is not None:
            old.cancel()
        timer = threading.Timer(delay, _run)
        timer.daemon = True
        _TIMERS[name] = timer
        timer.start()


def refresh_pack_tools(platform: Any, name: str) -> dict[str, list[str]]:
    """Read the tools of a RUNNING app again over its live connection and
    bring the registry in step: ``{added, removed, changed}`` (names as the
    app names them). The hold-back is applied again, so a write tool that
    arrived this way is held exactly as at a restart. BLOCKING."""
    from .tools import _run_sync, _wrap_specs, apply_quarantine, live_client

    cfg = _cfg(platform, name)
    client = live_client(name)
    if cfg is None or client is None:
        return {"added": [], "removed": [], "changed": []}
    specs = _run_sync(client.list_tools())
    registry = platform.registry
    old = {n.split("__", 2)[-1]: registry.get(n) for n in registry.mcp_names(name)}
    # The same wrapping a load does (locked, kept local), recorded nowhere.
    new_tools = {t.remote_name: t for t in _wrap_specs(cfg, name, client, specs, False, None)}
    added = sorted(set(new_tools) - set(old))
    removed = sorted(set(old) - set(new_tools))
    changed = sorted(
        n for n in set(new_tools) & set(old)
        if getattr(old[n], "manifest_spec", lambda: None)() != new_tools[n].manifest_spec()
        or getattr(old[n], "description", "") != new_tools[n].description
    )
    for n in removed:
        registry.unregister(f"mcp__{name}__{n}")
    for n in (*added, *changed):
        registry.unregister(f"mcp__{name}__{n}")
        registry.register(new_tools[n], mcp=True)
    try:
        apply_quarantine(name, [registry.get(f"mcp__{name}__{n}") for n in new_tools], platform.config.home)
    except Exception as exc:  # noqa: BLE001
        log.warning("the hold-back for %r was not applied after its tools changed: %s", name, exc)
    save_specs(platform.config.home, cfg, specs, getattr(client, "server_instructions", ""))
    if added or removed or changed:
        log.info("app %r changed its tools: +%s -%s ~%s", name, added, removed, changed)
    return {"added": added, "removed": removed, "changed": changed}


__all__ = [
    "LIST_CHANGED",
    "RETRY_DELAYS_S",
    "bind",
    "cache_path",
    "connection_sig",
    "failed_after_boot",
    "forget_specs",
    "is_lazy",
    "load_specs",
    "refresh_pack_tools",
    "retry_failed_packs",
    "save_specs",
    "schedule_refresh",
    "watch",
]
