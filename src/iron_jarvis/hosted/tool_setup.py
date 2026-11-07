"""The real CLI's OWN setup, as the CLI itself reports it (v1.426.0, Wave 6
lane B, E4 of ``docs/HARNESS-BUILD-AUDIT-2026-11-01.md``).

Build › Code drives the user's real Claude Code / Codex with their own tools
ON — including their own MCP servers (some of which need a sign-in), their
own invocable items and their own compact. None of that was visible: a
server that never started, or one waiting for a sign-in, showed nowhere.

This module turns what the driver ALREADY RECEIVES into one shape the
Session tab reads. Nothing here starts the tool, asks it anything extra or
reads its config files; every name and error is masked BEFORE it is cut
(:func:`events.clip`).

MEASURED, not recalled (scratch homes, ``scratchpad/w6b-builder/measure``):

* Claude Code 2.1.316 (a throwaway ``CLAUDE_CONFIG_DIR`` + ``--mcp-config``
  with a working stdio server, one that exits, and an HTTP server answering
  401 with OAuth discovery): its ``system`` ``init`` frame carries
  ``mcp_servers: [{name, status, source}]`` with ``status`` one of
  connected | failed | needs-auth | pending | disabled (the same enum the
  bundle's ``McpServerStatus`` schema names). No error text there (the
  separate ``mcp_status`` control request has it — not asked: one more
  round-trip per turn for a sentence).
* Codex 0.157.0 (a throwaway ``CODEX_HOME`` with the same three servers):
  after ``thread/start`` it sends ``mcpServer/startupStatus/updated``
  ``{threadId, name, status: starting|ready|failed|cancelled, error,
  failureReason: null|"reauthenticationRequired"}`` once per server; the
  HTTP server needing a sign-in arrives as ``failed`` with Codex's own
  words "The authserver MCP server is not logged in. Run `codex mcp login
  authserver`." (``failureReason`` stayed null). Codex's ``skills/list``
  ``{cwds}`` answers ``{data: [{cwd, skills: [{name, description, path,
  scope, enabled}]}]}``; ``$<skill>`` in a message's TEXT injects that skill
  (the request to the model carried a ``<skill>`` block — measured against a
  local stand-in Responses server), and the old custom prompts
  (``$CODEX_HOME/prompts/*.md``) are NOT read any more: ``/prompts:hello``
  went to the model as plain text. ``thread/compact/start {threadId}``
  answers ``{}`` and then runs as a turn of its own: ``turn/started`` →
  ``item/started|completed {type: contextCompaction}`` → ``turn/completed``
  (status completed).

v1.441.0, Grok 1.0.70 (a scratch ``GROK_HOME`` + ``USERPROFILE`` whose
``config.toml`` named a working stdio server, an HTTP server on a closed
port with a made-up key in its URL and a header, and a turned-off server;
the session opened on a stand-in model entry so no xAI sign-in was used and
no model was called): after ``session/new`` Grok pushes
``_x.ai/mcp/servers_updated {mcpServers: [{name, source, type, url |
command, args}]}`` — the turned-on servers' CONFIG, the URL with its key
in it — then ``_x.ai/mcp/server_status {sessionId, name, source, status:
ready | unavailable, reason: initialized | handshake_failed | spawn_failed |
timeout | auth_required | client_error, detail, tools}`` once per server
(``detail`` repeats the URL, key included). Only ``name``, ``status`` and
``reason`` are ever read (:func:`grok_servers`, :func:`grok_status`): never
a URL, a command, its arguments, a header, ``detail`` or anything else.
(Grok's own ``_x.ai/mcp/list`` request answers the same per server with
``session.enabled``; it is not asked — one more round-trip for a turned-off
server's name.)

Pi 1.3.0 has MCP servers of its own (``~/.pi/agent/mcp.json``) but no RPC
command or event that lists them: their state reaches the Code view only as
the text of its own notices — the start-up "MCP servers need attention"
warning, and ``/mcp`` typed in the box (an extension command; Pi answers it
with a status notice, measured). OpenCode's ACP reports none. The Session
tab says so in words.

v1.447.0, Antigravity 1.5.3: its ACP reports no MCP server, but ``agy mcp
list`` prints the servers of its own settings with ``enabled`` / ``disabled``
— read once per process start, NAME and STATUS only
(``hosted.antigravity_agents.parse_mcp_list``), shown as :data:`ON` /
:data:`OFF`: turned on is not the same as connected, and the words say so.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from .events import clip, make, one_line

#: The five states the Session tab knows (both tools map onto them).
CONNECTED = "connected"
STARTING = "starting"
FAILED = "failed"
NEEDS_SIGN_IN = "needs_sign_in"
OFF = "off"
#: v1.447.0: turned on in the tool's own settings, with no word on whether it
#: connected (Antigravity's ``agy mcp list``: its ACP reports no connection).
ON = "on"
STATES: tuple[str, ...] = (CONNECTED, STARTING, FAILED, NEEDS_SIGN_IN, OFF, ON)

#: Claude Code's own status words (its bundle's enum) -> ours.
CLAUDE_STATUS: dict[str, str] = {
    "connected": CONNECTED,
    "pending": STARTING,
    "failed": FAILED,
    "needs-auth": NEEDS_SIGN_IN,
    "disabled": OFF,
}
#: Codex's ``mcpServer/startupStatus/updated`` states -> ours (a ``failed``
#: one that says it is not logged in is :data:`NEEDS_SIGN_IN`).
CODEX_STATUS: dict[str, str] = {
    "starting": STARTING,
    "ready": CONNECTED,
    "failed": FAILED,
    "cancelled": OFF,
}
#: Codex's words for a server waiting for a sign-in (measured: "is not logged
#: in. Run `codex mcp login <name>`"), and its own failure reason.
_CODEX_SIGN_IN_RX = re.compile(r"not logged in|codex mcp login|re-?authenticat", re.IGNORECASE)

#: v1.441.0: Grok's ``_x.ai/mcp/server_status`` ``status`` words -> ours.
GROK_STATUS: dict[str, str] = {
    "ready": CONNECTED,
    "connected": CONNECTED,
    "unavailable": FAILED,
    "failed": FAILED,
    "pending": STARTING,
    "starting": STARTING,
    "connecting": STARTING,
    "reconnecting": STARTING,
    "needs_auth": NEEDS_SIGN_IN,
    "needs-auth": NEEDS_SIGN_IN,
    "disabled": OFF,
}
#: Grok's ``reason`` words for a server that did not start, said plainly
#: (Grok's own ``detail`` is never read: it repeats the server's URL).
GROK_REASON_WORDS: dict[str, str] = {
    "spawn_failed": "its program did not start",
    "timeout": "it did not answer in time",
    "handshake_failed": "it did not answer when Grok connected",
    "client_error": "Grok could not reach it",
}

#: Bounds: a session never lists more servers than this, nor longer words.
MAX_SERVERS = 50
NAME_CHARS = 120
ERROR_CHARS = 300

#: The tools that report their MCP servers here (the others: :data:`NOT_REPORTED`).
REPORTS_MCP: frozenset[str] = frozenset({"claude-code", "codex", "grok", "antigravity"})
NOT_REPORTED = "{tool} does not report its own MCP servers here."
#: v1.441.0: where a tool that does not report them says their state itself.
NOT_REPORTED_HOW: dict[str, str] = {
    "pi": "Type /mcp in the box and Pi says each one's state.",
}
NOT_YET = "{tool} says which of its own MCP servers started when it starts — they show after its next start."
NONE_REPORTED = "{tool} reported no MCP servers of its own."

#: How each tool signs in to one of its own MCP servers (ITS OWN way; never a
#: press of Iron Jarvis's). "Open in terminal" opens this session's
#: conversation in a Build terminal on the session's own account, so the
#: sign-in lands in the account the session uses.
SIGN_IN_WORDS: dict[str, str] = {
    "claude-code": (
        "To sign in, press Open in terminal, type /mcp there, choose {name} and pick "
        "Authenticate; Claude Code uses it from its next start here."
    ),
    # v1.426.0 review (lane B): `codex mcp login` is a SHELL command, and Open
    # in terminal opens the conversation in Codex's own screen — typed there it
    # is a message to the model. Leave Codex first (its own /exit: "exit Codex"
    # in 0.157.0's command list; its /mcp only lists servers).
    "codex": (
        "To sign in, press Open in terminal, leave Codex there with /exit, then run "
        "`codex mcp login {name}` in that terminal; Codex uses it from its next start here."
    ),
    # v1.441.0: Grok's own MCP screen (/mcps; ``i`` signs in, its docs).
    "grok": (
        "To sign in, press Open in terminal, type /mcps there, choose {name} and press i; "
        "Grok uses it from its next start here."
    ),
}
STATE_WORDS: dict[str, str] = {
    CONNECTED: "Connected",
    STARTING: "Starting…",
    FAILED: "Did not start",
    NEEDS_SIGN_IN: "Needs you to sign in",
    OFF: "Turned off",
    ON: "Turned on (whether it connected is not reported)",
}


def _name(raw: Any) -> str:
    return clip(str(raw or "").strip(), NAME_CHARS)


def _row(name: str, state: str, error: str = "", source: str = "") -> dict[str, str]:
    out = {"name": name, "state": state if state in STATES else FAILED}
    if error:
        out["error"] = error
    if source:
        out["source"] = source
    return out


def claude_servers(raw: Any) -> list[dict[str, str]] | None:
    """Claude Code's ``init`` ``mcp_servers`` in our shape. None = the frame
    did not say (an older CLI, a recording without the key): NOT KNOWN,
    which is never the same as "no servers"."""
    if not isinstance(raw, list):
        return None
    out: list[dict[str, str]] = []
    for item in raw[:MAX_SERVERS]:
        if not isinstance(item, dict):
            continue
        name = _name(item.get("name"))
        if not name:
            continue
        status = str(item.get("status") or "").strip().lower()
        state = CLAUDE_STATUS.get(status, FAILED)
        # v1.441.0 (g1): ``mcp_status`` (read after a reconnect or a switch)
        # carries Claude Code's own error for a server that failed; the init
        # frame never does.
        error = one_line(item.get("error") or "", ERROR_CHARS) if item.get("error") else ""
        out.append(_row(name, state, error if state in (FAILED, NEEDS_SIGN_IN) else "",
                        clip(str(item.get("source") or ""), 40)))
    return out


def codex_update(current: list[dict[str, str]] | None, params: Any) -> list[dict[str, str]] | None:
    """One ``mcpServer/startupStatus/updated`` folded into the list Codex has
    reported so far (a server keeps its place; a new one is added at the
    end). Returns the NEW list, or ``current`` unchanged for a frame that
    names no server."""
    p = params if isinstance(params, dict) else {}
    name = _name(p.get("name"))
    if not name:
        return current
    status = str(p.get("status") or "").strip()
    error = one_line(p.get("error") or "", ERROR_CHARS) if p.get("error") else ""
    state = CODEX_STATUS.get(status, FAILED)
    if state == FAILED and (
        str(p.get("failureReason") or "") == "reauthenticationRequired" or _CODEX_SIGN_IN_RX.search(error)
    ):
        state = NEEDS_SIGN_IN
    rows = [dict(r) for r in (current or [])]
    row = _row(name, state, error if state in (FAILED, NEEDS_SIGN_IN) else "")
    for i, r in enumerate(rows):
        if r.get("name") == name:
            rows[i] = row
            return rows
    if len(rows) >= MAX_SERVERS:
        return rows
    rows.append(row)
    return rows


#: v1.447.0: Codex's ``mcpServerStatus/list`` ``runtimeStatus`` (its schema's
#: ``McpServerConnectionStatus``) -> ours.
CODEX_RUNTIME: dict[str, str] = {
    "notStarted": STARTING,
    "starting": STARTING,
    "connected": CONNECTED,
    "authenticationRequired": NEEDS_SIGN_IN,
    "failed": FAILED,
    "cancelled": OFF,
    "disabled": OFF,
}


def codex_listed(current: list[dict[str, str]] | None, result: Any, *,
                 replace: bool) -> list[dict[str, str]] | None:
    """v1.447.0: Codex's ``mcpServerStatus/list {threadId, detail:
    toolsAndAuthOnly}`` answer folded in — ONLY each server's ``name``,
    ``runtimeStatus`` and ``authStatus`` are read (never ``httpOrigin``, its
    tools or anything else). MEASURED on codex 0.157.0 (a scratch
    ``CODEX_HOME``, ``tests/fixtures/build_app_v14471/codex_mcp_reload.json``):
    a server the config turns off (``enabled = false``) is listed
    ``disabled`` — the start-up statuses never name it — and after
    ``config/mcpServer/reload`` a server taken out of the config is gone.

    ``replace`` False (the read at start): only servers the list did not
    have are added (a turned-off one as off); known ones keep their state.
    ``replace`` True (after a reload): the answer IS the list, in its order,
    each known server keeping its error while it is still failing. None (or
    ``current``) for an answer that is not a list."""
    data = result.get("data") if isinstance(result, dict) else None
    if not isinstance(data, list):
        return current
    known = {r.get("name"): r for r in current or []}
    listed: list[dict[str, str]] = []
    for item in data[:MAX_SERVERS]:
        if not isinstance(item, dict):
            continue
        name = _name(item.get("name"))
        if not name or any(r["name"] == name for r in listed):
            continue
        state = CODEX_RUNTIME.get(str(item.get("runtimeStatus") or ""), "")
        if state == FAILED and str(item.get("authStatus") or "") == "notLoggedIn":
            state = NEEDS_SIGN_IN
        if not state:
            # No reading for it (no thread, a newer word): what is known, else starting.
            row = dict(known.get(name) or _row(name, STARTING))
        else:
            old = known.get(name) or {}
            err = str(old.get("error") or "") if state == old.get("state") else ""
            row = _row(name, state, err)
        listed.append(row)
    if replace:
        return listed
    rows = [dict(r) for r in (current or [])]
    have = {r.get("name") for r in rows}
    for r in listed:
        if r["name"] not in have and len(rows) < MAX_SERVERS:
            rows.append(r)
    return rows


def grok_servers(current: list[dict[str, str]] | None, params: Any) -> list[dict[str, str]] | None:
    """v1.441.0: Grok's ``_x.ai/mcp/servers_updated`` folded into the list:
    ONLY each server's ``name`` is read (the rest is the user's config — a URL
    with its key, a command line). A server Grok has said nothing more about
    is "starting"; one it no longer lists is gone; one already known keeps its
    state. None for a frame that is not a list (not known)."""
    p = params if isinstance(params, dict) else {}
    raw = p.get("mcpServers")
    if not isinstance(raw, list):
        return current
    known = {r.get("name"): r for r in current or []}
    rows: list[dict[str, str]] = []
    for item in raw[:MAX_SERVERS]:
        if not isinstance(item, dict):
            continue
        name = _name(item.get("name"))
        if not name or any(r["name"] == name for r in rows):
            continue
        rows.append(dict(known.get(name) or _row(name, STARTING)))
    return rows


def grok_status(current: list[dict[str, str]] | None, params: Any) -> list[dict[str, str]] | None:
    """v1.441.0: one ``_x.ai/mcp/server_status`` folded in: ``name``,
    ``status`` and ``reason`` only — never ``detail`` (it repeats the URL,
    key included). A server it names that the list did not is added."""
    p = params if isinstance(params, dict) else {}
    name = _name(p.get("name"))
    if not name:
        return current
    status = str(p.get("status") or "").strip().lower()
    reason = str(p.get("reason") or "").strip().lower()
    state = GROK_STATUS.get(status, FAILED)
    if reason == "auth_required" and state in (FAILED, STARTING):
        state = NEEDS_SIGN_IN
    error = GROK_REASON_WORDS.get(reason, "") if state == FAILED else ""
    rows = [dict(r) for r in (current or [])]
    row = _row(name, state, error)
    for i, r in enumerate(rows):
        if r.get("name") == name:
            rows[i] = row
            return rows
    if len(rows) >= MAX_SERVERS:
        return rows
    rows.append(row)
    return rows


def grok_listed(result: Any) -> list[str] | None:
    """v1.447.0: the NAMES in Grok's ``_x.ai/mcp/list`` answer (``{result:
    {servers: [...]}}``, measured on 1.0.70) — every server its config names,
    turned on or off. Only ``name`` is read. None for an answer that is not
    that shape (not known)."""
    inner = result.get("result") if isinstance(result, dict) else None
    servers = inner.get("servers") if isinstance(inner, dict) else None
    if not isinstance(servers, list):
        return None
    names: list[str] = []
    for item in servers[:MAX_SERVERS]:
        name = _name(item.get("name")) if isinstance(item, dict) else ""
        if name and name not in names:
            names.append(name)
    return names


def grok_with_off(rows: list[dict[str, str]] | None, listed: list[str] | None) -> list[dict[str, str]] | None:
    """v1.447.0: Grok's pushed list plus each server ``_x.ai/mcp/list`` names
    that Grok did not push, as off (``servers_updated`` names only the
    turned-on ones — measured). Unchanged while either is not known."""
    if rows is None or not listed:
        return rows
    out = [dict(r) for r in rows]
    have = {r.get("name") for r in out}
    for name in listed:
        if name not in have and len(out) < MAX_SERVERS:
            out.append(_row(name, OFF))
    return out


def setup_event(servers: list[dict[str, str]]) -> dict[str, Any]:
    """The ``setup`` event a driver emits when the list it knows CHANGED."""
    return make("setup", mcp_servers=[dict(s) for s in servers])


def note_servers(driver: Any, servers: list[dict[str, str]] | None, attr: str = "mcp_servers") -> None:
    """Keep ``servers`` on the driver and emit a ``setup`` event — only when
    the list is known and differs from the last one said (a tool restating
    the same list every turn writes nothing). Never raises. v1.441.0:
    ``attr`` names where the list is kept — an ACP driver's own
    ``mcp_servers`` holds the servers Iron Jarvis HANDS it (``acp_servers``),
    so Grok keeps its reported list as ``reported_mcp``."""
    if servers is None:
        return
    try:
        if servers == getattr(driver, attr, None):
            return
        setattr(driver, attr, [dict(s) for s in servers])
        driver.emit(setup_event(servers))
    except Exception:  # noqa: BLE001 — a status list never breaks a reply
        return


# --------------------------------------------------------------------------- #
# the Session tab's reading (``panes.session_view``)
# --------------------------------------------------------------------------- #


def _tool_label(harness: str) -> str:
    from .words import label

    return label(harness)


def view(harness: str, events: Iterable[dict[str, Any]], running: bool = False) -> dict[str, Any]:
    """``overview.mcp``: the newest list the tool reported (``setup`` events
    in the session's log), each server with its state in words and — for one
    waiting for a sign-in — the tool's own way to sign in. ``known`` False =
    the tool never said (a tool that does not report: :data:`NOT_REPORTED`;
    one that has not started yet: :data:`NOT_YET`). Never raises."""
    tool = _tool_label(harness)
    if harness not in REPORTS_MCP:
        note = NOT_REPORTED.format(tool=tool)
        how = NOT_REPORTED_HOW.get(harness, "")
        return {"known": False, "servers": [], "note": f"{note} {how}" if how else note}
    newest: dict[str, Any] | None = None
    try:
        for ev in events:
            if isinstance(ev, dict) and ev.get("type") == "setup" and isinstance(ev.get("mcp_servers"), list):
                newest = ev
    except Exception:  # noqa: BLE001
        newest = None
    if newest is None:
        return {"known": False, "servers": [], "note": NOT_YET.format(tool=tool)}
    rows: list[dict[str, str]] = []
    how = SIGN_IN_WORDS.get(harness, "")
    for s in newest["mcp_servers"][:MAX_SERVERS]:
        if not isinstance(s, dict) or not s.get("name"):
            continue
        name = _name(s.get("name"))
        state = str(s.get("state") or FAILED)
        state = state if state in STATES else FAILED
        words = STATE_WORDS[state]
        err = one_line(s.get("error") or "", ERROR_CHARS) if s.get("error") else ""
        row = {"name": name, "state": state, "words": words}
        if err and state == FAILED:
            row["error"] = err
        if state == NEEDS_SIGN_IN and how:
            row["sign_in"] = how.format(name=name)
        rows.append(row)
    out: dict[str, Any] = {"known": True, "servers": rows, "at": str(newest.get("at") or "")}
    if not rows:
        out["note"] = NONE_REPORTED.format(tool=tool)
    return out


# --------------------------------------------------------------------------- #
# Codex's own invocable items in the `/` menu
# --------------------------------------------------------------------------- #
#: Codex's own compact, offered in its `/` menu: the CodexDriver turns a
#: message that is exactly "/compact" into ``thread/compact/start``.
CODEX_COMPACT_TEXT = "/compact"
CODEX_COMPACT_ROW: dict[str, str] = {
    "name": "compact",
    "description": "Summarize the conversation to free up room (Codex's own compact).",
}
#: A Codex skill is invoked by ``$<name>`` in the message (measured).
SKILL_PREFIX = "$"
MAX_SKILLS = 200


def codex_skill_rows(result: Any) -> list[dict[str, str]]:
    """``skills/list``'s answer as `/` menu rows: ``{name, description,
    prefix: "$"}`` — an ENABLED skill only, each name once, masked and cut."""
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    data = result.get("data") if isinstance(result, dict) else None
    for entry in data or []:
        skills = entry.get("skills") if isinstance(entry, dict) else None
        for s in skills or []:
            if not isinstance(s, dict) or s.get("enabled") is False:
                continue
            name = clip(str(s.get("name") or "").strip(), 80)
            if not name or name in seen or not re.fullmatch(r"[A-Za-z0-9_.:-]+", name):
                continue
            seen.add(name)
            rows.append({"name": name, "description": clip(one_line(s.get("description") or "", 200), 200),
                         "prefix": SKILL_PREFIX})
            if len(rows) >= MAX_SKILLS:
                return rows
    return rows
