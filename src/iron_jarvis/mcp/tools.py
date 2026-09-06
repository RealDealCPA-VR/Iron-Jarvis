"""Wrap remote MCP tools as native Iron Jarvis ``Tool`` objects (§19).

Each tool advertised by an external MCP server is exposed to agents as an
:class:`MCPRemoteTool` named ``mcp__<server>__<tool>`` and gated by the single
``mcp_call`` permission key. ``mcp_tools`` is the builder the platform calls: it
connects to each configured server, lists its tools, and returns the wrapped
``Tool`` objects — defaulting to a **no-op empty list** when nothing is
configured, and skipping (never crashing on) any server it cannot reach.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
from typing import Any, Callable

from ..core.logging import get_logger
from ..tools.base import Tool, ToolContext, ToolResult
from .client import FakeTransport, HttpTransport, MCPClient, StdioTransport

log = get_logger("mcp")

#: A resolver for secret-referenced auth: ``name -> plaintext value`` (or None).
SecretResolver = Callable[[str], "str | None"]


def _content_to_text(content: Any) -> str:
    """Flatten MCP ``content`` blocks into plain text for ``ToolResult.output``."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):  # a single block
        content = [content]
    parts: list[str] = []
    for block in content:
        if isinstance(block, dict):
            if block.get("type") == "text" and "text" in block:
                parts.append(str(block["text"]))
            elif "text" in block:
                parts.append(str(block["text"]))
            else:  # image / resource / other — describe, don't drop
                parts.append(f"[{block.get('type', 'content')}]")
        else:
            parts.append(str(block))
    return "\n".join(parts)


class MCPRemoteTool(Tool):
    """A native ``Tool`` that proxies to one remote MCP tool via an ``MCPClient``."""

    permission_key = "mcp_call"
    #: EVERYTHING a remote MCP server returns is third-party text (v1.98.1).
    #: This is the surface that reads mail, issues, tickets and shared docs — the
    #: most attacker-reachable content in the product: someone emails the user, or
    #: comments on their GitHub issue, with "ignore previous instructions...", and
    #: the next agent read hands it to the model. Declaring this makes the runtime
    #: and both chat loops fence it as DATA and scan it for injection first.
    #:
    #: Declared rather than self-fenced on purpose: tools either set this flag OR
    #: call wrap_untrusted themselves (web_search/browse do the latter). Doing both
    #: would double-wrap the same text.
    returns_untrusted_content = True

    def __init__(
        self,
        client: MCPClient,
        server_name: str,
        remote_name: str,
        description: str = "",
        input_schema: dict[str, Any] | None = None,
    ) -> None:
        self.client = client
        self.server_name = server_name
        self.remote_name = remote_name
        self.name = f"mcp__{server_name}__{remote_name}"
        self.description = description or (
            f"Remote MCP tool '{remote_name}' from server '{server_name}'."
        )
        self.input_schema = input_schema or {"type": "object", "properties": {}}

    @classmethod
    def from_spec(
        cls, client: MCPClient, server_name: str, spec: dict[str, Any]
    ) -> "MCPRemoteTool":
        return cls(
            client,
            server_name,
            spec.get("name", "tool"),
            spec.get("description", ""),
            spec.get("inputSchema") or spec.get("input_schema"),
        )

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        try:
            result = await self.client.call_tool(self.remote_name, args)
        except Exception as exc:  # a remote failure must never crash the runtime
            return ToolResult(ok=False, error=f"{type(exc).__name__}: {exc}")

        result = result if isinstance(result, dict) else {}
        text = _content_to_text(result.get("content"))
        if result.get("isError"):
            return ToolResult(
                ok=False, output=text, error=text or "remote MCP tool error", data=result
            )
        return ToolResult(ok=True, output=text, data=result)


# --------------------------------------------------------------------------- #
# Builder.
# --------------------------------------------------------------------------- #
def _run_sync(coro: Any) -> Any:
    """Drive an async coroutine to completion from synchronous platform wiring.

    Platform assembly is synchronous, but the client API is async. If we are
    already inside a running event loop, run the coroutine on a worker thread to
    avoid nesting; otherwise just ``asyncio.run`` it.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


#: npm packages superseded upstream — the old name installs but yields a
#: server that advertises no tools. Healed transparently at transport build.
_SUPERSEDED_PACKAGES: dict[str, str] = {
    "@modelcontextprotocol/server-brave-search": "@brave/brave-search-mcp-server",
}


def _build_transport(cfg: dict[str, Any], secret_resolver: SecretResolver | None) -> Any:
    """Construct (or accept an injected) transport from a server config dict."""
    # Direct injection wins (tests / advanced use): a pre-built transport object.
    injected = cfg.get("transport_obj")
    if injected is not None:
        return injected
    raw = cfg.get("transport")
    if raw is not None and not isinstance(raw, str) and hasattr(raw, "request"):
        return raw

    kind = (raw if isinstance(raw, str) else "stdio").lower()

    if kind in ("http", "streamable-http", "streamable_http", "sse"):
        headers = dict(cfg.get("headers") or {})
        auth = cfg.get("auth")
        if isinstance(auth, dict):
            value = None
            if auth.get("secret") and secret_resolver is not None:
                value = secret_resolver(auth["secret"])
            elif auth.get("value"):
                value = auth["value"]
            if value:
                header = auth.get("header", "Authorization")
                fmt = auth.get("format", "Bearer {value}")
                headers[header] = fmt.format(value=value)
        return HttpTransport(cfg["url"], headers=headers)

    # Default: stdio subprocess.
    #
    # env resolution: literal ``env`` entries, plus ``env_secrets`` (a map of
    # ENV_VAR -> vault secret name) resolved through ``secret_resolver`` at LAUNCH
    # — so a connector's token (GitHub PAT, Slack bot token, …) stays encrypted in
    # the vault instead of living plaintext in config.toml. Anything provided is
    # MERGED onto ``os.environ`` (never replaces it): Popen(env=…) replaces the
    # whole environment, so a bare {TOKEN: …} would drop PATH and npx/uvx would
    # fail to launch. When nothing is added we pass ``None`` to inherit as before.
    import os as _os

    env: dict[str, str] = dict(cfg.get("env") or {})
    env_secrets = cfg.get("env_secrets")
    if isinstance(env_secrets, dict) and secret_resolver is not None:
        for env_key, secret_name in env_secrets.items():
            try:
                value = secret_resolver(str(secret_name))
            except Exception:  # noqa: BLE001 — a vault miss just omits the var
                value = None
            if value:
                env[str(env_key)] = value
    merged_env = {**_os.environ, **env} if env else None
    # Heal superseded package names at LAUNCH: the upstream Brave server was
    # deprecated/archived — it still connects but advertises no tools, which
    # read as a broken connector. Mapping here fixes EXISTING config.toml
    # entries without a manual disconnect/reconnect.
    args = [
        _SUPERSEDED_PACKAGES.get(str(a), str(a)) for a in (cfg.get("args") or [])
    ]
    return StdioTransport(
        resolve_launcher(str(cfg["command"])),
        args or None,
        env=merged_env,
        cwd=cfg.get("cwd"),
    )


def resolve_launcher(command: str) -> str:
    """The executable path for a stdio server's ``command`` (v1.233.0).

    THE LIVE DEFECT: ``brave_search`` is configured as ``command = "npx"`` and
    ``subprocess.Popen(["npx", ...])`` raised ``FileNotFoundError: [WinError 2]``
    on the packaged daemon — for two reasons at once. On Windows ``npx`` is
    ``npx.cmd``; ``CreateProcess`` does not consult ``PATHEXT`` the way
    ``cmd.exe`` does, so the bare name never resolves even when it IS on PATH.
    And a GUI-launched daemon inherits Electron's environment, which lacks the
    per-user Node dirs (``%LOCALAPPDATA%\\pi-node\\current``, ``%APPDATA%\\npm``)
    the doctor's ``mcp`` check already searches. So the pack was "visible as
    failed" (v1.229.0) but could never start.

    Resolution mirrors the doctor exactly — ``terminals.ai_clis._find``: real
    PATH via ``shutil.which`` (PATHEXT-aware, so ``npx`` → ``npx.CMD``), then
    the well-known per-user bin dirs with Windows extensions. A command that
    already carries a path separator is the user's explicit choice and is
    passed through untouched. When nothing resolves, raise a FileNotFoundError
    that SAYS what is missing and how to fix it, so the Tools-page row and the
    toast read as instructions instead of a WinError code; that error rides the
    same ``last_error`` path as any launch failure.
    """
    import os as _os

    cmd = (command or "").strip()
    if not cmd:
        raise FileNotFoundError("this pack has no launcher command configured")
    if _os.sep in cmd or (_os.altsep and _os.altsep in cmd):
        return cmd  # an explicit path: the user's choice, never rewritten
    from ..terminals.ai_clis import _find

    found = _find(cmd)
    if found:
        return found
    hint = {
        "npx": "install Node.js LTS (https://nodejs.org)",
        "uvx": "install uv (https://docs.astral.sh/uv/)",
    }.get(cmd.lower().removesuffix(".cmd"), f"install '{cmd}'")
    raise FileNotFoundError(
        f"launcher '{cmd}' was not found on PATH or in the usual install "
        f"folders — {hint}, restart Iron Jarvis, then press Retry on the pack"
    )


#: Max seconds to wait for one MCP server to connect + list its tools at boot. A
#: stdio server that spawns but never answers blocks on a pipe read with no
#: timeout, so without this bound a single misbehaving server hangs daemon boot
#: forever. Override via IRONJARVIS_MCP_CONNECT_TIMEOUT.
def _mcp_connect_timeout() -> float:
    import os

    try:
        return max(1.0, float(os.environ.get("IRONJARVIS_MCP_CONNECT_TIMEOUT", "15")))
    except ValueError:
        return 15.0


def _connect_with_timeout(cfg, name, secret_resolver, timeout):
    """Connect + list_tools for one server on a daemon thread, bounded by
    ``timeout``. On timeout, close the transport (killing a hung stdio child to
    unblock its pipe read) and raise TimeoutError so the caller skips the server."""
    import threading

    box: dict[str, Any] = {}

    def work() -> None:
        try:
            transport = _build_transport(cfg, secret_resolver)
            box["transport"] = transport
            client = MCPClient(transport, name=name)
            box["specs"] = _run_sync(client.list_tools())
            box["client"] = client
        except Exception as exc:  # noqa: BLE001 — surfaced to the caller below
            box["error"] = exc

    th = threading.Thread(target=work, daemon=True)
    th.start()
    th.join(timeout)
    if th.is_alive():
        transport = box.get("transport")
        if transport is not None and hasattr(transport, "close"):
            try:
                transport.close()  # terminate the hung child; unblocks readline
            except Exception:  # noqa: BLE001
                pass
        raise TimeoutError(f"did not respond within {timeout:g}s")
    if "error" in box:
        raise box["error"]
    return box["client"], box["specs"]


#: Per-server load record (v1.229.0, audit U4): ``name -> {last_error, tools_loaded,
#: at}``. A server skipped at load used to leave ONE warning line in daemon.log
#: and nothing else — ``/mcp/servers`` reported ``tools_loaded: 0`` with no
#: reason, the Tools page rendered "0 tools", and the Overview said nominal.
#: Kept here rather than on the config row: ``config.mcp_servers`` is persisted
#: verbatim to config.toml, and an exception text is not configuration.
_LOAD_STATUS: dict[str, dict[str, Any]] = {}


def _record_load(name: str, *, error: str | None, tools_loaded: int) -> None:
    from datetime import datetime, timezone

    _LOAD_STATUS[name] = {
        "last_error": error,
        "tools_loaded": tools_loaded,
        "at": datetime.now(timezone.utc).isoformat(),
    }


def load_status(name: str) -> dict[str, Any] | None:
    """The last load attempt for server ``name`` (``None`` = never attempted
    in this process). ``last_error`` is ``None`` after a successful connect."""
    rec = _LOAD_STATUS.get(name)
    return dict(rec) if rec is not None else None


def load_statuses() -> dict[str, dict[str, Any]]:
    """Every server's last load record, by name."""
    return {k: dict(v) for k, v in _LOAD_STATUS.items()}


def mcp_tools(
    server_configs: list[dict[str, Any]] | None,
    secret_resolver: SecretResolver | None = None,
    *,
    record: bool = True,
) -> list[Tool]:
    """Build the wrapped MCP tools for every configured server.

    * Empty / ``None`` config (the default — no MCP servers) -> ``[]`` so platform
      wiring is a safe no-op.
    * Each server is connected, ``tools/list``-ed, and its tools wrapped.
    * A server that cannot be reached, errors, OR does not respond within the
      connect timeout is **skipped** with a warning so one bad/hung server never
      breaks (or hangs) boot — and the reason is kept on its load record
      (:func:`load_status`) so the Tools page and the doctor can NAME it.
    * ``record=False`` is for a PROBE (the read-only ``/mcp/servers/{name}/test``
      route): it connects and lists but registers nothing, so writing its
      outcome onto the load record would report a server as started while the
      registry still holds none of its tools — and every truth surface (Tools
      row, ``/diagnostics``, the doctor) would go quiet over a pack agents
      cannot use. Only a load that hands its tools to the registry records.
    """
    if not server_configs:
        return []

    timeout = _mcp_connect_timeout()
    tools: list[Tool] = []
    for cfg in server_configs:
        name = cfg.get("name") or "mcp"
        try:
            client, specs = _connect_with_timeout(cfg, name, secret_resolver, timeout)
        except Exception as exc:  # skip the bad/hung server; keep booting
            log.warning("skipping MCP server %r: %s: %s", name, type(exc).__name__, exc)
            if record:
                _record_load(name, error=f"{type(exc).__name__}: {exc}", tools_loaded=0)
            continue
        count = 0
        for spec in specs:
            if not isinstance(spec, dict) or not spec.get("name"):
                continue
            tools.append(MCPRemoteTool.from_spec(client, name, spec))
            count += 1
        if record:
            _record_load(name, error=None, tools_loaded=count)
    return tools


__all__ = [
    "MCPRemoteTool",
    "SecretResolver",
    "mcp_tools",
    "load_status",
    "load_statuses",
    "FakeTransport",
    "MCPClient",
    "StdioTransport",
    "HttpTransport",
]
