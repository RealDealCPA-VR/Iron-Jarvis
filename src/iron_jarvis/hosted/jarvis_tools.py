"""Iron Jarvis's own tools for a Build › Code session (v1.414.0).

A Build terminal can hand the coding tool running in it Iron Jarvis's MCP
connection (``terminals/recipes.py``, v1.238.0): a pane-scoped token, the
``/mcp`` address, and the pane's own Capabilities — five boxes, ALL OFF until
the user ticks one, of which only Browser and Memory are served
(``mcpserver/server.CAPABILITY_TOOLS``). A Build › Code session gets the SAME
connection under the SAME consent model: the same five boxes on the session
(``HostedSessionRecord.capabilities_json``, all off by default, ticked by the
user in the Code view), the same token store shape (memory only, random,
bound to the session and read LIVE — unticking a box takes effect on the next
call), the same server, the same deny floor and approval cards. A session
with nothing served ticked gets NO token and NO configuration at all.

HOW EACH TOOL IS TOLD — per process, through the tool's OWN documented way
to add an MCP server for one run, and NEVER by writing the user's config
files (each measured on this PC against the installed program, with a
scratch home and a logging stand-in server; no model call):

* Claude Code 2.1.311 — ``--mcp-config <file>``: a file of OUR OWN in the
  session's folder under Iron Jarvis's home (``hosted/<id>/jarvis-mcp.json``)
  holding the address and ``Authorization: Bearer ${IRONJARVIS_MCP_TOKEN}``
  — a REFERENCE Claude Code expands from the child's environment (measured:
  the header arrived equal to the token). No ``--strict-mcp-config``: the
  user's own servers stay on.
* Codex 0.157.0 — ``codex app-server -c mcp_servers.iron-jarvis-code.url=…
  -c mcp_servers.iron-jarvis-code.bearer_token_env_var="IRONJARVIS_MCP_TOKEN"``
  (measured: initialize / tools/list arrived with the token on a
  ``thread/start``).
* Pi 1.3.0 — no flag for an MCP server, but ``-e <extension>`` loads an
  extension for one run, and an extension may ``pi.registerMcpServer(...)``
  (Pi's own API, ``core/extensions/types.d.ts``; "not saved; register again
  on every load"). The extension is a file of OUR OWN
  (``hosted/<id>/jarvis-mcp.js``) whose header is ``${IRONJARVIS_MCP_TOKEN}``,
  which Pi expands (measured).
* OpenCode 1.18.18 — ACP ``session/new`` / ``session/load`` take
  ``mcpServers`` for THAT session (its ``mcpCapabilities.http`` is true);
  the token rides the request on the process's own stdin, never a file or
  an environment variable (measured).

Neither file holds a credential: the token lives in the process's
environment (Claude Code, Codex, Pi) or its stdin (OpenCode), and dies with
the process — :meth:`HostedTokens.revoke` is called on every stop.

WHAT REACHES THE VENDOR: the coding tool's model reads what these tools
return, and that model runs where the user's own tool sends it (Anthropic,
OpenAI, a Pi/OpenCode provider). Build › Code sessions have no Local only
setting (none of the four tools), so ticking Memory means "my memory may be
read by this tool's model" — the box says so. Code sessions have no trust
posture of Iron Jarvis's own either: the four tools run their own rules. No
write tool is ever served (Memory is read-only; Browser's acting tools ask on
every call, exactly as for a terminal).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

#: A Build › Code session's grant id (the pane id a pane token names).
GRANT_PREFIX = "code:"
#: The server's name in every tool's configuration for a Build › Code
#: session. DELIBERATELY NOT the Build terminal recipes' ``iron-jarvis``
#: (``terminals/recipes.SERVER_NAME``, v1.414.0 review): the Codex recipe
#: writes a ``[mcp_servers."iron-jarvis"]`` stdio table (command + args) into
#: a Codex config — possibly the user's own — and Codex MERGES ``-c
#: mcp_servers.<name>.url=…`` into a table of the same name, leaving one server
#: with both ``command`` and ``url``; the Claude recipe likewise writes
#: ``iron-jarvis`` into a project's ``.mcp.json``. One name for all four tools
#: keeps the rule simple. The model sees it as the tools' prefix
#: (``mcp__iron-jarvis-code__memory_search`` in Claude Code).
SERVER_NAME = "iron-jarvis-code"
#: Our own files in the session's folder under Iron Jarvis's home.
CLAUDE_FILE = "jarvis-mcp.json"
PI_FILE = "jarvis-mcp.js"


def grant_id(sid: str) -> str:
    return f"{GRANT_PREFIX}{sid}"


def session_of(pane_id: str) -> str:
    """The Code session a grant id names, else ""."""
    pid = str(pane_id or "")
    return pid[len(GRANT_PREFIX):] if pid.startswith(GRANT_PREFIX) else ""


def normalise(raw: Any) -> dict[str, bool]:
    """Five boxes, all real bools (the pane's own reader)."""
    from ..terminals.session import normalise_pane_capabilities

    return normalise_pane_capabilities(raw if isinstance(raw, dict) else {})


def load(raw_json: str) -> dict[str, bool]:
    try:
        raw = json.loads(raw_json) if raw_json else {}
    except ValueError:
        raw = {}
    return normalise(raw)


def served(caps: dict[str, bool]) -> list[str]:
    """The ticked boxes the MCP server actually serves (Browser, Memory)."""
    from ..mcpserver.server import CAPABILITY_TOOLS

    return sorted(k for k in CAPABILITY_TOOLS if caps.get(k))


def wanted(caps: dict[str, bool]) -> bool:
    """Does this session need the connection at all? (A session that ticks
    nothing served gets no token and no configuration.)"""
    return bool(served(caps))


@dataclass
class Connection:
    """What a process start adds for the connection."""

    argv: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    #: OpenCode: the ACP ``mcpServers`` entries for ``session/new|load``.
    acp_servers: list[dict[str, Any]] = field(default_factory=list)


def _claude_config(url: str) -> str:
    from ..browser.panetokens import MCP_TOKEN_ENV

    return json.dumps({"mcpServers": {SERVER_NAME: {
        "type": "http",
        "url": url,
        "headers": {"Authorization": "Bearer ${" + MCP_TOKEN_ENV + "}"},
    }}}, indent=2)


def _pi_extension(url: str) -> str:
    from ..browser.panetokens import MCP_TOKEN_ENV

    config = {
        "type": "http",
        "url": url,
        "headers": {"Authorization": "Bearer ${" + MCP_TOKEN_ENV + "}"},
        # Declared to the model like any other tool (Pi's default hides an
        # MCP server's tools behind codemode).
        "exposure": "direct",
        "description": "Iron Jarvis: the memory and browser tools the user turned on for this session.",
    }
    return (
        "// Written by Iron Jarvis for one Build › Code session. Holds no credential.\n"
        "export default function (pi) {\n"
        f"  pi.registerMcpServer({json.dumps(SERVER_NAME)}, {json.dumps(config)});\n"
        "}\n"
    )


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(text.encode("utf-8"))
    tmp.replace(path)


def connect(harness: str, *, url: str, token: str, session_dir: Path) -> Connection:
    """BLOCKING (writes our own file): the connection for one process start."""
    from ..browser.panetokens import MCP_TOKEN_ENV, MCP_URL_ENV
    from .claude import HARNESS as CLAUDE
    from .codex import HARNESS as CODEX
    from .opencode import HARNESS as OPENCODE
    from .pi import HARNESS as PI

    env = {MCP_URL_ENV: url, MCP_TOKEN_ENV: token}
    if harness == CLAUDE:
        path = Path(session_dir) / CLAUDE_FILE
        _write(path, _claude_config(url))
        return Connection(argv=["--mcp-config", str(path)], env=env)
    if harness == CODEX:
        key = f"mcp_servers.{SERVER_NAME}"
        return Connection(argv=[
            "-c", f"{key}.url={json.dumps(url)}",
            "-c", f"{key}.bearer_token_env_var={json.dumps(MCP_TOKEN_ENV)}",
        ], env=env)
    if harness == PI:
        path = Path(session_dir) / PI_FILE
        _write(path, _pi_extension(url))
        return Connection(argv=["-e", str(path)], env=env)
    if harness == OPENCODE:
        # The token rides ACP on the process's own stdin — not its env, where
        # every command OpenCode runs could read it.
        return Connection(env={MCP_URL_ENV: url}, acp_servers=[{
            "type": "http",
            "name": SERVER_NAME,
            "url": url,
            "headers": [{"name": "Authorization", "value": f"Bearer {token}"}],
        }])
    return Connection()


def remove_files(session_dir: Path) -> None:
    """Our own files go with the process. Never raises."""
    for name in (CLAUDE_FILE, PI_FILE):
        try:
            (Path(session_dir) / name).unlink(missing_ok=True)
        except OSError:
            log.debug("could not remove %s", name, exc_info=True)


class _Grantee:
    """What ``PaneTokenStore.resolve`` reads: alive + LIVE capabilities."""

    def __init__(self, alive: bool, capabilities: dict[str, bool], folder: str) -> None:
        self.alive = alive
        self.capabilities = capabilities
        self.cwd = folder


class HostedTokens:
    """Build › Code's tokens: a :class:`PaneTokenStore` of its own (a
    terminal's ``purge_dead`` revokes every pane id it does not know, so the
    two kinds never share one), whose grant reads the session's capabilities
    LIVE from the service (in memory — the lookup runs on a worker thread)."""

    def __init__(self, svc: Any) -> None:
        from ..browser.panetokens import PaneTokenStore

        self.svc = svc
        self.store = PaneTokenStore(pane_lookup=self.lookup)

    def lookup(self, pane_id: str) -> Any:
        sid = session_of(pane_id)
        live = self.svc.live.get(sid) if sid else None
        if live is None:
            return None
        return _Grantee(bool(live.running), dict(getattr(live, "caps", {}) or {}),
                        str(getattr(live, "folder", "") or ""))

    def mint(self, sid: str, caps: dict[str, bool]) -> str:
        return self.store.mint(grant_id(sid), caps)

    def resolve(self, token: str) -> Any:
        return self.store.resolve(token)

    def revoke(self, sid: str) -> None:
        """The token AND every MCP session it opened."""
        self.store.revoke_pane(grant_id(sid))
        sessions = getattr(getattr(self.svc, "platform", None), "mcp_sessions", None)
        if sessions is not None:
            try:
                sessions.revoke_pane(grant_id(sid))
            except Exception:  # noqa: BLE001
                log.warning("could not revoke a Code session's MCP sessions", exc_info=True)
