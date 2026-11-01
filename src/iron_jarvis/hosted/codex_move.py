"""Continue a CODEX conversation on another account (v1.422.0, Wave 2 B5) —
the twin of Claude Code's copy (``terminals/continue_on.carry_over``).

MEASURED on this PC (codex-cli 0.157.0, two SCRATCH ``CODEX_HOME`` folders,
never ``~/.codex`` and never an account; the model was a local fake
OpenAI-Responses endpoint, so no model was called): a thread started and
answered in home A is written to ``A/sessions/<YYYY>/<MM>/<DD>/
rollout-<time>-<thread id>.jsonl`` (lazily — ``thread/start`` alone writes
nothing; the first turn does). Copying THAT FILE to the same relative path
under home B is all a move needs: in B, ``thread/list`` lists it (Codex
fills its own ``state_*.sqlite`` index from the sessions folder),
``thread/read`` reads it, ``thread/resume`` reopens it under the SAME
thread id, and the next turn's request carried the earlier user message and
answer to the model. The resumed thread then writes into B's copy; A's copy
is untouched. Nothing in the rollout names the account.

NOT measured (no second signed-in ChatGPT account, and no model call was
allowed): whether OpenAI accepts one account's encrypted reasoning items
under another. If it refuses, the carried turn fails in the tool's own words
— the conversation in the first account is untouched (this is a COPY), and
the move counts toward the carry-on chain (it never loops).

THE RULES are Claude's: the conversation is COPIED, never moved; the copy
lands only inside the other account's home; a destination that already holds
an OLDER copy of the same conversation (a byte prefix — the rollout is
append-only, and A -> B -> A meets A's older copy) is replaced, its old
bytes kept in Iron Jarvis's own trash first; a DIVERGED copy is never
overwritten (refused in words); a link is never followed; everything is
checked BEFORE Iron-Proxy is told anything or anything is copied.
"""

from __future__ import annotations

import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

#: Iron-Proxy's provider of Codex accounts.
PROVIDER = "openai"
#: A Codex thread id (a UUID, v7 in 0.157.0) — never globbed unchecked.
_THREAD = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

NO_CODEX_FILE = (
    "The conversation's file is not in this account's Codex folder, so it cannot move to "
    "another account — nothing was changed."
)
NO_OTHER = "There is no other Codex account in Iron-Proxy to continue on."
NONE_FREE = "No other Codex account can take over right now: {why}."
NOT_OTHER = "That account is not another Codex account in Iron-Proxy."
CANNOT_TAKE = '"{title}" cannot take over right now.'
CANNOT_COPY = "The conversation could not be copied to the other account — nothing was changed."
DIVERGED = (
    '"{title}" already holds a different copy of this conversation, so it was not moved — '
    "nothing was changed."
)
FILE_BUSY = (
    'The conversation file is open in Codex on "{title}" — close it there and try again.'
)


class MoveRefused(Exception):
    """One plain sentence; nothing was told or copied."""


def find_rollout(home: str, thread_id: str) -> Path | None:
    """The thread's rollout file in ``home``'s ``sessions`` folder (the newest
    when there are several), or None. Never a link. BLOCKING."""
    tid = str(thread_id or "").strip()
    if not home or not _THREAD.match(tid):
        return None
    root = Path(home) / "sessions"
    try:
        hits = [p for p in root.glob(f"*/*/*/rollout-*-{tid}.jsonl") if p.is_file() and not p.is_symlink()]
    except OSError:
        return None
    if not hits:
        return None
    return max(hits, key=lambda p: p.stat().st_mtime)


def _others(profiles: list[dict[str, Any]], current: str) -> list[dict[str, Any]]:
    return sorted(
        (
            p for p in profiles
            if isinstance(p, dict) and p.get("provider") == PROVIDER
            and (p.get("lane") or "cli") == "cli" and p.get("enabled") is not False
            and p.get("id") != current
        ),
        key=lambda p: p.get("order") or 0,
    )


def plan(platform: Any, row: Any, account: str, src_home: str) -> tuple[Any, Path, Any, str]:
    """BLOCKING: pick the next Codex account and FIND the conversation —
    refusing in words BEFORE Iron-Proxy is told anything or anything is
    copied. Returns ``(lease, rollout, client, current account id)``."""
    from ..iron_proxy import accounts as _acc
    from ..terminals import continue_on as CO
    from ..terminals import pane_accounts as PA
    from . import store
    from . import words as W

    rec = store.account_record(row)
    current = str(rec.get("id") or "")
    if rec.get("source") != "iron-proxy" or not current:
        raise MoveRefused(W.THIS_PC)
    svc = getattr(platform, "iron_proxy", None)
    if svc is None or not getattr(svc, "enabled", False):
        raise MoveRefused(PA._OFF)
    try:
        client = svc.lease_client(timeout_s=PA.LEASE_TIMEOUT_S)
    except Exception as exc:  # noqa: BLE001
        raise MoveRefused(PA._unavailable_sentence(exc)) from None
    if client is None:
        raise MoveRefused(PA._OFF)
    try:
        profiles, states = client.profiles(), client.states()
    except Exception as exc:  # noqa: BLE001
        raise MoveRefused(PA._unavailable_sentence(exc)) from None
    profiles = [p for p in (profiles if isinstance(profiles, list) else []) if isinstance(p, dict)]
    states = states if isinstance(states, dict) else {}
    others = _others(profiles, current)
    spec = PA.SPECS[PROVIDER]
    keys = sorted(PA.PANE_OWN_KEYS[PROVIDER])
    lease = None
    for prof in others:
        if account and str(prof.get("id")) != account:
            continue
        st = states.get(prof.get("id")) if isinstance(states.get(prof.get("id")), dict) else {}
        cli = prof.get("cli") if isinstance(prof.get("cli"), dict) else {}
        home = str(cli.get("home") or "").strip()
        title = str(prof.get("title") or prof.get("id"))
        if home and CO._usable(st):
            lease = _acc.make_lease(spec, str(prof.get("id")), title, home, keys)
            break
        if account:
            raise MoveRefused(CANNOT_TAKE.format(title=title))
    if lease is None:
        if account:
            raise MoveRefused(NOT_OTHER)
        if not others:
            raise MoveRefused(NO_OTHER)
        raise MoveRefused(NONE_FREE.format(why="each is signed out, turned off or at its limit"))
    source = find_rollout(src_home, row.harness_session_id)
    if source is None:
        raise MoveRefused(NO_CODEX_FILE)
    dest = destination(source, src_home, lease.home)
    if dest is None:
        raise MoveRefused(CANNOT_COPY)
    if dest.exists() and dest.resolve() != source.resolve() and not (
        CO._same_bytes(source, dest) or CO._is_prefix(dest, source)
    ):
        raise MoveRefused(DIVERGED.format(title=lease.title))
    return lease, source, client, current


def destination(source: Path, src_home: str, dest_home: str) -> Path | None:
    """Where the copy goes: the SAME relative path under the other home's
    ``sessions`` folder (Codex's own date folders and file name — never
    recomputed), or None when that is not inside the other home."""
    from ..terminals import continue_on as CO

    if not str(dest_home or "").strip():
        return None
    try:
        rel = source.resolve().relative_to((Path(src_home) / "sessions").resolve())
    except (OSError, ValueError):
        return None
    root = Path(dest_home)
    dest = root / "sessions" / rel
    return dest if CO._inside(dest, root) else None


def carry(lease: Any, source: Path, src_home: str, client: Any, current: str, limit: Any,
          trash_root: Path) -> Path:
    """BLOCKING: tell Iron-Proxy (ONLY the limit message) and COPY the
    rollout into the other account's home. Returns the copy."""
    from ..iron_proxy import accounts as _acc
    from ..terminals import continue_on as CO

    dest = destination(source, src_home, lease.home)
    if dest is None:
        raise MoveRefused(CANNOT_COPY)
    if limit is not None:
        try:
            client.signal(current, text=limit.signal_text())
        except Exception as exc:  # noqa: BLE001 — the move goes on
            _acc._note("continue signal", exc)
    try:
        if dest.exists():
            if dest.resolve() == source.resolve() or CO._same_bytes(source, dest):
                return dest
            if not CO._is_prefix(dest, source):
                raise MoveRefused(DIVERGED.format(title=lease.title))
            trash = (Path(trash_root) / datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
                     / "codex-carry")
            CO._replace_keeping_old(source, dest, trash, Path(dest.name))
            return dest
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(f".{dest.name}.ij-carry-{os.getpid()}")
        try:
            shutil.copy2(source, tmp)
            os.replace(tmp, dest)
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
        return dest
    except MoveRefused:
        raise
    except CO.CarryBusy:
        raise MoveRefused(FILE_BUSY.format(title=lease.title)) from None
    except OSError:
        raise MoveRefused(CANNOT_COPY) from None
