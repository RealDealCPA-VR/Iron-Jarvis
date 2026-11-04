"""Which Claude account an Iron-Proxy account is SIGNED IN TO (v1.427.3, a USER BUG).

THE BUG (2026-11-03, the user's live Build › Code session): three Iron-Proxy
Claude accounts — two of Iron-Proxy's own homes and the adopted ``~/.claude`` —
were signed in to ONE Claude account. Claude Code reported the same eleven
claude.ai connectors and the same usage windows (5-hour 100%, 7-day 43%, the
same resets) from every one of them. Claude Code's sign-in authorises whichever
claude.ai account the BROWSER has open, so signing a second account in again
quietly gave it the first one's login. Continue on another account then moved
twice onto the same limit, told Iron-Proxy each time that the account it left
was limited, and refused the next press: every other account was parked.

THE ANSWER: Claude Code writes the signed-in account into ``.claude.json``'s
``oauthAccount`` (``organizationUuid`` + ``accountUuid``) — in
``$CLAUDE_CONFIG_DIR/.claude.json`` for an account's own home, and in
``~/.claude.json`` for this PC's own login (the adopted default home sets no
variable, :func:`iron_proxy.accounts.is_default_home`). Two accounts with the
SAME pair share one usage limit. Only the two ids are read and compared: no
e-mail, name or token is kept, logged or returned. Unknown (no file, no pair,
too big, unreadable, a network path) is None — never "the same".

BLOCKING (file reads, cached by size and time): call it off the loop.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Iterable

#: The variable a Claude account's home rides (``accounts.make_lease``).
HOME_VAR = "CLAUDE_CONFIG_DIR"

#: ``.claude.json`` past this is not read (the same bound the synced-plugins
#: reader uses, ``harness.claude_commands.MAX_ACCOUNT_FILE_BYTES``).
MAX_FILE_BYTES = 32 * 1024 * 1024

_CACHE_MAX = 64
_lock = threading.Lock()
#: path -> ((size, mtime_ns), identity or None)
_cache: dict[str, tuple[tuple[int, int], tuple[str, str] | None]] = {}


def _uuid(value: Any) -> str:
    from ..harness.claude_commands import _uuid as _u

    return _u(value)


def config_file(home: str) -> Path | None:
    """The ``.claude.json`` Claude Code reads for an account whose home is
    ``home``: ``~/.claude.json`` for this PC's own login, else inside the home.
    None for no home or a network / device spelling (never touched: a lookup of
    a ``\\\\host`` path is an SMB connection, the v1.379.0 lesson)."""
    from .accounts import is_default_home

    text = str(home or "").strip()
    if not text or text.startswith(("\\\\", "//")):
        return None
    if is_default_home(HOME_VAR, text):
        return Path(os.path.expanduser("~")) / ".claude.json"
    return Path(text) / ".claude.json"


def identity_of(home: str) -> tuple[str, str] | None:
    """``(organizationUuid, accountUuid)`` of the account signed in to ``home``,
    lower case; None when it cannot be told. Never raises."""
    path = config_file(home)
    if path is None:
        return None
    key = str(path)
    try:
        st = path.stat()
    except OSError:
        return None
    sig = (int(st.st_size), int(st.st_mtime_ns))
    with _lock:
        hit = _cache.get(key)
        if hit is not None and hit[0] == sig:
            return hit[1]
    found: tuple[str, str] | None = None
    if 0 < st.st_size <= MAX_FILE_BYTES:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeDecodeError):
            data = None
        oauth = data.get("oauthAccount") if isinstance(data, dict) else None
        if isinstance(oauth, dict):
            org, account = _uuid(oauth.get("organizationUuid")), _uuid(oauth.get("accountUuid"))
            if org and account:
                found = (org, account)
    with _lock:
        if len(_cache) >= _CACHE_MAX and key not in _cache:
            _cache.pop(next(iter(_cache)))
        _cache[key] = (sig, found)
    return found


def same_login(home_a: str, home_b: str) -> bool:
    """True only when BOTH accounts' sign-ins are known and are one Claude
    account. Two names for the very same home folder are the same login too."""
    if not str(home_a or "").strip() or not str(home_b or "").strip():
        return False
    a, b = identity_of(home_a), identity_of(home_b)
    return a is not None and a == b


def shared_groups(accounts: Iterable[tuple[str, str]]) -> list[list[str]]:
    """Accounts ``(id, home)`` grouped by the Claude account they are signed in
    to — only the groups of two or more, each in the order given. Never raises."""
    by: dict[tuple[str, str], list[str]] = {}
    for pid, home in accounts:
        try:
            ident = identity_of(home)
        except Exception:  # noqa: BLE001 — a status read never fails on this
            ident = None
        if ident is not None and pid:
            by.setdefault(ident, []).append(str(pid))
    return [ids for ids in by.values() if len(ids) > 1]


def forget() -> None:
    """Drop the cache (tests)."""
    with _lock:
        _cache.clear()
