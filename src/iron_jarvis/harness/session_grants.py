"""ONE APPROVAL PER CODING SESSION (v1.404.1, a USER BUG).

The user, after a team session's workers asked four times: "it keeps asking
for approval. it should be one approval per command. It stalled out 4 times
waiting additional approvals." Read off the live record: four helper runs of
ONE coding session (``chat_d22d01230268``), each a separate run, each parked
on its own card for running a command (``process_start`` once, ``shell``
three times) — one for 11 hours — and every card answered "Allow for this
run". That answer was kept ONLY on the helper's own run
(``allow_tools_json``), so the next helper started from nothing and asked
again; a helper already parked on the same card was never woken.

Now a grant made on any card of a coding session — a helper's or the
session's own — is the SESSION's:

* :func:`note` keeps it for the life of this Iron Jarvis (every helper the
  session starts later in the same reply inherits it through
  ``chat_turn.helper_parent_now``), and the runtime writes it to the
  session's ``setup.granted_tools`` (:func:`persist`), which every later
  reply reads (``routes/build.start_build_turn``);
* :func:`release_helpers` wakes every OTHER helper of the session parked on
  a card the grant covers, with the same answer;
* running a command is ONE kind of approval: ``shell``, ``process_start``
  and ``process_write`` (:data:`COMMAND_FAMILY`) are granted together
  (:func:`widen`) — the user approved "running commands", not one tool name.

What it NEVER does (the review of this change found the first two):

* share a grant by a permission key several tools share — every app (MCP)
  tool is ``mcp_call``, so allowing one app's tool woke another app's card.
  Only a tool whose permission key IS its own name, and not a quarantined
  pack tool, is shared (:func:`shareable`);
* wake or cover a helper whose own rules would not have been handed the
  grant — "Ask before every action", Local only, low trust, a folder rule or
  a night task's "ask" (:func:`narrowed`, read off the helper's own
  ``HelperParent``, the same narrowing ``helper_parent_now`` applies); and a
  helper under those rules never shares ITS grant with the session;
* wake a card that is answered once every time (``once_only`` — a commit or
  push on the session's branch, a project's or a plugin's command lines) or a
  priced one: those keep their own card.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Iterable

log = logging.getLogger("ironjarvis.harness.session_grants")

#: Running a command is one approval: the command itself, a command started
#: in the background, and typing into one already running.
COMMAND_FAMILY = frozenset({"shell", "process_start", "process_write"})

#: build thread id -> tool names granted for the session in this process.
_LIVE: dict[str, set[str]] = {}
_LOCK = threading.Lock()


def widen(names: Iterable[str]) -> set[str]:
    """``names`` with the whole :data:`COMMAND_FAMILY` when any of it is
    there; anything else unchanged."""
    out = {str(n) for n in (names or ()) if str(n).strip()}
    if out & COMMAND_FAMILY:
        out |= COMMAND_FAMILY
    return out


def shareable(registry: Any, name: str) -> bool:
    """May a grant on ``name`` be the session's? Only a registered tool
    whose permission key is its OWN name (never ``mcp_call``, which every app
    tool shares) and that is not a quarantined pack tool. Never raises."""
    try:
        tool = registry.get(str(name or "")) if registry is not None else None
        if tool is None:
            return False
        return tool.perm_key() == str(name) and not getattr(tool, "quarantined", False)
    except Exception:  # noqa: BLE001
        return False


def shares(parent: Any) -> bool:
    """May a helper started from ``parent`` share its grants with the
    session (and be woken by the session's)? Not under "Ask before every
    action", Local only or low trust — and not when nothing says who it is."""
    if parent is None:
        return False
    return (
        str(getattr(parent, "approval_mode", "") or "") != "always_ask"
        and not getattr(parent, "local_only", False)
        and not getattr(parent, "low_trust", False)
    )


def narrowed(parent: Any, name: str, registry: Any) -> bool:
    """Would the helper started from ``parent`` NOT be handed a grant on
    ``name``? (:func:`shares` says no, or one of its own rules — a folder
    rule, a night task's "ask" — asks or denies it.)"""
    if not shares(parent):
        return True
    rules = dict(getattr(parent, "rules", ()) or ())
    keys = {str(name)}
    try:
        tool = registry.get(str(name)) if registry is not None else None
        if tool is not None:
            keys.add(tool.perm_key())
    except Exception:  # noqa: BLE001
        pass
    return any(rules.get(k) in ("ask", "deny") for k in keys)


def covers(registry: Any, thread_id: str, child: str, names: Iterable[str]) -> bool:
    """Does the session's grant cover EVERY one of ``names`` for its live
    helper ``child`` (a helper that was already running when another card
    granted it)?"""
    from .helpers import child_parent

    wanted = {str(n) for n in (names or ()) if str(n).strip()}
    have = granted(thread_id)
    if not wanted or not wanted <= have:
        return False
    parent = child_parent(f"build:{str(thread_id or '').strip()}", child)
    return all(shareable(registry, n) and not narrowed(parent, n, registry) for n in wanted)


def note(thread_id: str, names: Iterable[str], registry: Any = None) -> set[str]:
    """Keep ``names`` (widened) as granted for coding session ``thread_id``;
    returns the widened set. With ``registry``, only :func:`shareable` names
    count. A blank thread keeps nothing."""
    thread = str(thread_id or "").strip()
    wide = widen(names)
    if registry is not None:
        wide = {n for n in wide if shareable(registry, n)}
    if not thread or not wide:
        return set()
    with _LOCK:
        _LIVE.setdefault(thread, set()).update(wide)
    return wide


def granted(thread_id: str) -> set[str]:
    """What coding session ``thread_id`` was granted in this process (a copy)."""
    with _LOCK:
        return set(_LIVE.get(str(thread_id or "").strip(), ()))


def forget(thread_id: "str | None" = None) -> None:
    """Drop one session's live grants (None: every session) — tests, and a
    session that is deleted."""
    with _LOCK:
        if thread_id is None:
            _LIVE.clear()
        else:
            _LIVE.pop(str(thread_id or "").strip(), None)


def persist(engine: Any, thread_id: str, names: Iterable[str]) -> None:
    """Add ``names`` to the session's saved ``setup.granted_tools`` (what
    every later reply of the session reads). BLOCKING: call it off the loop.
    Never raises."""
    try:
        from .sessions import add_granted_tools

        add_granted_tools(engine, thread_id, sorted({str(n) for n in names if str(n).strip()}))
    except Exception:  # noqa: BLE001 — the live grant above still holds
        log.warning("saving a grant on build session %s failed", thread_id, exc_info=True)


def _covered(entry: dict, names: set[str], registry: Any, parent: Any) -> "str | None":
    """The tool name of a pending ask the grant covers, else None: by the
    tool's EXACT name (never a shared permission key), a shareable tool, not
    narrowed for that helper, never an ask answered once every time or a
    priced one."""
    if entry.get("once_only") is True or entry.get("cost_note"):
        return None
    name = str(entry.get("tool") or "")
    if not name or name not in names or not shareable(registry, name):
        return None
    return None if narrowed(parent, name, registry) else name


def release_helpers(approvals: Any, registry: Any, thread_id: str, names: Iterable[str], *,
                    skip_session: str = "") -> int:
    """Answer every pending card of the session's OTHER live helpers that the
    grant covers with "conversation" (settled by the grant, not counted as
    another answer by the person). Returns how many were woken. Never raises."""
    from .helpers import child_parent, live_helpers

    wide = {n for n in widen(names) if shareable(registry, n)}
    thread = str(thread_id or "").strip()
    if not thread or not wide:
        return 0
    pending = getattr(approvals, "pending_for", None)
    resolve = getattr(approvals, "resolve", None)
    if pending is None or resolve is None:
        return 0
    try:
        children = live_helpers(f"build:{thread}")
    except Exception:  # noqa: BLE001
        return 0
    woken = 0
    for child in children:
        if not child or child == skip_session:
            continue
        try:
            entries = list(pending(child))
            parent = child_parent(f"build:{thread}", child)
        except Exception:  # noqa: BLE001
            continue
        for entry in entries:
            # By the ask's OWN id: answering by tool name would also wake a
            # once-only ask of the same tool (a push is a `shell` call too).
            if _covered(entry, wide, registry, parent) is None:
                continue
            try:
                if resolve(str(entry.get("approval_id") or ""), "conversation", actor="grant"):
                    woken += 1
            except Exception:  # noqa: BLE001
                continue
    return woken
