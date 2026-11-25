"""EACH APP'S OWN SETTINGS, read live (v1.577.0, "large apps work well").

A connected app (an MCP server) used to be one switch: on, or off. Large
real-world apps (eighty tools, their own approval screens, long calls) need a
few more, all OPTIONAL keys on the app's own row in ``config.mcp_servers``.
A row without them behaves exactly as before (``approval`` ""):

``pinned_tools``  list of the app's own tool names (as the app names them) that
                  join every chat reply and agent run the app is part of,
                  before any picking by the message's words.
``approval``      who asks before a tool of this app runs:
                  ``""``       as before (the app's switch and the turn decide);
                  ``"ask"``    Iron Jarvis asks before every call (a card about
                               THAT tool; an answer "for this chat" covers that
                               tool in that chat);
                  ``"writes"`` Iron Jarvis asks only before a tool that can
                               change things (no ``readOnlyHint``); reading
                               tools run without a card;
                  ``"server"`` the app asks in its own window (it has its own
                               approvals), so Iron Jarvis does not.
                  A tool held back (new in a later version of the app, or
                  flagged by the scan) and every tool of a LOCKED app still
                  ask before each use, whatever this says.
``trust_all``     tools a later version of the app adds are trusted like the
                  ones the user saw when they connected it (no hold-back),
                  except a tool whose words the scan flagged.
``resource_timeout_s``  seconds one resource read may take (default 15;
                  the list of resources gets a third of it, at least 5).
``resource_chars``      characters of one resource the model is shown
                  (default 20,000).

Read LIVE: the platform binds the saved rows at start (:func:`bind`), and
every check reads them, so a change made through any door applies to the next
call with no restart. Never raises.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

APPROVAL_DEFAULT = ""
APPROVAL_ASK = "ask"
APPROVAL_WRITES = "writes"
APPROVAL_SERVER = "server"
APPROVAL_MODES = (APPROVAL_DEFAULT, APPROVAL_ASK, APPROVAL_WRITES, APPROVAL_SERVER)

#: The words each mode is shown with (Settings › Tools, and chat's answer).
APPROVAL_WORDS = {
    APPROVAL_DEFAULT: "As set for the chat",
    APPROVAL_ASK: "Ask before every tool",
    APPROVAL_WRITES: "Ask only before changes",
    APPROVAL_SERVER: "The extension asks in its own window",
}

RESOURCE_TIMEOUT_S = 15.0
RESOURCE_LIST_MIN_S = 5.0
RESOURCE_CHARS = 20_000
#: Bounds a row may set (a typo must not make a read wait an hour, or flood
#: the model's context).
RESOURCE_TIMEOUT_MAX_S = 600.0
RESOURCE_CHARS_MAX = 200_000
#: The most pinned tools one app may have (they ride every turn it is in).
MAX_PINNED = 40


@dataclass(frozen=True)
class AppPolicy:
    approval: str = APPROVAL_DEFAULT
    trust_all: bool = False
    pinned: tuple[str, ...] = field(default_factory=tuple)
    resource_timeout_s: float = RESOURCE_TIMEOUT_S
    resource_chars: int = RESOURCE_CHARS

    @property
    def resource_list_timeout_s(self) -> float:
        return max(RESOURCE_LIST_MIN_S, self.resource_timeout_s / 3.0)


_DEFAULT = AppPolicy()
_LOCK = threading.Lock()
_TABLE: dict[str, AppPolicy] = {}


def _number(value: Any, default: float, low: float, high: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number or number <= 0:  # NaN, zero, negative: the default
        return default
    return max(low, min(high, number))


def normalise_approval(value: Any) -> str:
    """One of :data:`APPROVAL_MODES` ("" for anything else)."""
    text = str(value or "").strip().lower()
    return text if text in APPROVAL_MODES else APPROVAL_DEFAULT


def normalise_pins(value: Any) -> list[str]:
    """A clean, ordered, de-duplicated list of tool names (at most
    :data:`MAX_PINNED`)."""
    if isinstance(value, str):
        value = [v for v in value.replace("\n", ",").split(",")]
    out: list[str] = []
    for item in value or []:
        name = str(item or "").strip()
        if name.startswith("mcp__"):
            # A full name pasted in: keep the app's own part.
            parts = name.split("__", 2)
            name = parts[2] if len(parts) == 3 else name
        if name and name not in out:
            out.append(name)
        if len(out) >= MAX_PINNED:
            break
    return out


def policy_of(cfg: Any) -> AppPolicy:
    """The :class:`AppPolicy` a saved app row says (defaults for anything it
    does not say)."""
    if not isinstance(cfg, dict):
        return _DEFAULT
    return AppPolicy(
        approval=normalise_approval(cfg.get("approval")),
        trust_all=bool(cfg.get("trust_all")),
        pinned=tuple(normalise_pins(cfg.get("pinned_tools"))),
        resource_timeout_s=_number(cfg.get("resource_timeout_s"), RESOURCE_TIMEOUT_S, 1.0, RESOURCE_TIMEOUT_MAX_S),
        resource_chars=int(_number(cfg.get("resource_chars"), RESOURCE_CHARS, 1000, RESOURCE_CHARS_MAX)),
    )


#: Where the saved rows are read from: set once at start (``bind``) to read
#: the live config, so every door that changes an app row (the Tools page,
#: chat, an import, the vault migration) is seen at the next call.
_SOURCE: "list[Callable[[], Any]]" = []


def bind(rows: "Callable[[], Any] | None") -> None:
    """Read the saved rows from *rows()* from now on (None: the table)."""
    with _LOCK:
        _SOURCE.clear()
        if rows is not None:
            _SOURCE.append(rows)


def sync(server_configs: "Iterable[Any] | None") -> None:
    """Replace the fallback table with what *server_configs* say (for a
    caller with no live config: a script, a test)."""
    table: dict[str, AppPolicy] = {}
    for cfg in server_configs or ():
        if isinstance(cfg, dict) and cfg.get("name"):
            table[str(cfg["name"])] = policy_of(cfg)
    with _LOCK:
        _TABLE.clear()
        _TABLE.update(table)


def policy(server: str) -> AppPolicy:
    """The live policy of the app named *server* (the defaults when unknown)."""
    name = str(server or "")
    with _LOCK:
        source = _SOURCE[0] if _SOURCE else None
        fallback = _TABLE.get(name, _DEFAULT)
    if source is None:
        return fallback
    try:
        for cfg in source() or ():
            if isinstance(cfg, dict) and str(cfg.get("name") or "") == name:
                return policy_of(cfg)
    except Exception:  # noqa: BLE001 — unreadable: the defaults (as before)
        return _DEFAULT
    return _DEFAULT


def resource_limits(
    server: str, timeout_s: float, chars: int
) -> "tuple[float, float, int]":
    """``(list timeout, read timeout, characters)`` for one app's resources:
    what its row sets, else the caller's own defaults (*timeout_s* for a read,
    a third of it, at least 5 s, for the list; *chars*)."""
    row = _row(server)
    p = policy(server)
    read = p.resource_timeout_s if isinstance(row, dict) and row.get("resource_timeout_s") else float(timeout_s)
    cut = p.resource_chars if isinstance(row, dict) and row.get("resource_chars") else int(chars)
    return max(RESOURCE_LIST_MIN_S, read / 3.0), read, cut


def _row(server: str) -> Any:
    with _LOCK:
        source = _SOURCE[0] if _SOURCE else None
    if source is None:
        return None
    try:
        for cfg in source() or ():
            if isinstance(cfg, dict) and str(cfg.get("name") or "") == str(server or ""):
                return cfg
    except Exception:  # noqa: BLE001
        return None
    return None


def pinned_names(server: str) -> list[str]:
    """The registry names (``mcp__<server>__<tool>``) of the app's pins."""
    return [f"mcp__{server}__{t}" for t in policy(server).pinned]


def _server_of(tool: Any) -> str:
    return str(getattr(tool, "server_name", "") or "")


def _is_app_tool(tool: Any) -> bool:
    return bool(getattr(tool, "remote_name", None)) and bool(_server_of(tool))


def policy_asks(tool: Any) -> bool:
    """Does the app's own setting make *tool* ask before EACH use (a card
    about this tool, never covered by the app being switched on for the
    chat)? ``"ask"``: every tool; ``"writes"``: a tool that can change
    things. Read by ``tools.permissions.asks_each_use``."""
    if not _is_app_tool(tool):
        return False
    mode = policy(_server_of(tool)).approval
    if mode == APPROVAL_ASK:
        return True
    if mode == APPROVAL_WRITES:
        return bool(getattr(tool, "write_like", True))
    return False


def runs_unasked(tool: Any) -> bool:
    """Does the app's own setting let *tool* run with no Iron Jarvis card?
    ``"server"``: every tool (the app asks in its own window); ``"writes"``:
    a tool the app declared read-only. Never a held-back tool or a LOCKED
    app's tool."""
    if not _is_app_tool(tool):
        return False
    if getattr(tool, "quarantined", False) or getattr(tool, "locked", False):
        return False
    if getattr(tool, "description_flagged", False):
        return False
    mode = policy(_server_of(tool)).approval
    if mode == APPROVAL_SERVER:
        return True
    if mode == APPROVAL_WRITES:
        return not bool(getattr(tool, "write_like", True))
    return False


def unasked_names(registry: Any, names: Iterable[str]) -> list[str]:
    """The names among *names* whose tool :func:`runs_unasked`."""
    out: list[str] = []
    for name in names or ():
        try:
            tool = registry.get(name)
        except Exception:  # noqa: BLE001
            continue
        if tool is not None and runs_unasked(tool) and name not in out:
            out.append(name)
    return out


def describe(cfg: Any) -> dict[str, Any]:
    """The row keys the Tools page and chat read (names only, no values that
    are secret: there are none here)."""
    p = policy_of(cfg)
    return {
        "approval": p.approval,
        "approval_words": APPROVAL_WORDS[p.approval],
        "trust_all": p.trust_all,
        "pinned_tools": list(p.pinned),
        "resource_timeout_s": p.resource_timeout_s,
        "resource_chars": p.resource_chars,
        # Start on first use (``mcp/resilience``): the program starts on the
        # first call once its tool list has been kept.
        "lazy": bool(cfg.get("lazy")) if isinstance(cfg, dict) else False,
    }


__all__ = [
    "APPROVAL_ASK",
    "APPROVAL_DEFAULT",
    "APPROVAL_MODES",
    "APPROVAL_SERVER",
    "APPROVAL_WORDS",
    "APPROVAL_WRITES",
    "AppPolicy",
    "bind",
    "describe",
    "normalise_approval",
    "normalise_pins",
    "pinned_names",
    "policy",
    "policy_asks",
    "policy_of",
    "runs_unasked",
    "sync",
    "unasked_names",
]
