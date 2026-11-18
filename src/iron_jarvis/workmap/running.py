"""What Iron Jarvis's own coding sessions are doing RIGHT NOW, for another app
(v1.530.0): the other direction of Chat › Code's "Running elsewhere".

A connected app (Claude Code, Codex, Claude desktop) asks ``work_search``;
the answer starts with the coding work running in Iron Jarvis at that
moment: Chat › Code sessions (the IJ harness) and Build › Code sessions (the
real tools run inside Iron Jarvis), each with its title, its folder and
whether it waits for the owner. Plain chats are left out: this is about
coding work in folders.

THE RULES

* In memory, plus the work record's own rows for a session's folder. Never a
  message body, never a transcript.
* A session whose folder is not known is left out (fail closed).
* Every row goes through the client wall of an asker that is ANOTHER APP
  (``clients.wall.outside_and_map``), exactly as ``work_search``'s other
  lines do: work for a client of any kind is never told to another app.
* LOCAL ONLY (fourth review, MS1): each row carries ``held``. A Chat › Code
  session is held when its record row is Local only or kept home, or when
  its chat, asked again right now (``collect.live_state``), is Local only
  (unreadable: held); a Build › Code session when its tool is not one that
  sends to its own vendor anyway (``coding_sessions.VENDOR_TOOLS``, the
  record's own kept-home rule). A held row, or one with no ``held`` fact, is
  never told: the asker is always another app's model.

BLOCKING (the record's refresh): a caller is off the event loop.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("iron_jarvis.workmap.running")

#: The most rows told.
RUNNING_MAX = 8
HEAD = "Running now in Iron Jarvis:"
#: Said for every empty answer (idle, walled or held alike): it reveals
#: nothing and is never false.
NOTHING = "Nothing Iron Jarvis may share with this app is running right now."


def _held_now(engine: Any, refs: list[str]) -> "dict[str, bool] | None":
    """``{ref: local_only}`` for each Chat › Code chat in ``refs``, asked
    again right now (a ref whose chat is gone is absent). None when the
    chats cannot be read (the caller then holds every row)."""
    from ..core.db import session_scope
    from . import collect
    from .models import KIND_CODE

    try:
        with session_scope(engine) as db:
            live = collect.live_state(db, [(KIND_CODE, ref) for ref in refs])
        return {ref: bool(state.get("local_only")) for (_kind, ref), state in live.items()}
    except Exception:  # noqa: BLE001 — unsure: held
        log.debug("running now: the chats could not be asked again", exc_info=True)
        return None


def _rows(platform: Any) -> list[dict[str, Any]]:
    """``[{where, title, folder, waiting, held}]`` for each coding session
    running; ``held`` = it may not leave this PC (Local only, kept home)."""
    from ..core.turns import TURNS
    from ..daemon import durable_turns
    from ..hosted.news import running_rows
    from ..projects.coding_sessions import VENDOR_TOOLS
    from .index import work_map

    out: list[dict[str, Any]] = []
    approvals = getattr(platform, "approvals", None)
    try:
        pending = approvals.pending_ids() if approvals is not None else ()
    except Exception:  # noqa: BLE001
        pending = ()
    replies = durable_turns.running_replies(getattr(platform, "turn_logs", None), TURNS.thread_states(), pending)
    build = [r for r in replies if r.get("surface") == "build"]
    if build:
        wm = work_map(getattr(platform, "engine", None), getattr(getattr(platform, "config", None), "home", None))
        if wm is not None:
            wm.refresh(budget=20)
            by_ref = {r["ref"]: r for r in wm._rows() if r.get("kind") == "code"}
            known = [(r, by_ref.get(str(r.get("thread_id") or ""))) for r in build]
            known = [(r, row) for r, row in known if row is not None and row.get("folder")]
            # The source asked again: a chat switched to Local only a moment
            # ago is held even when its record row was not rebuilt yet.
            now = _held_now(getattr(platform, "engine", None), [row["ref"] for _r, row in known]) if known else {}
            for r, row in known:
                if now is not None and row["ref"] not in now:
                    continue  # deleted since the record was built
                held = bool(row.get("local_only") or row.get("keep_home")) or now is None or now[row["ref"]]
                out.append({"where": "Chat › Code", "title": row.get("title") or "",
                            "folder": row["folder"], "waiting": bool(r.get("waiting")), "held": held})
    svc = getattr(platform, "hosted", None)
    for r in running_rows(platform):
        live = getattr(svc, "live", {}).get(str(r.get("thread_id") or "")) if svc is not None else None
        folder = str(getattr(live, "folder", "") or "")
        if not folder:
            continue
        from ..hosted.words import label

        harness = str(r.get("harness") or "")
        out.append({"where": f"Build › Code ({label(harness)})",
                    "title": str(r.get("title") or ""), "folder": folder, "waiting": bool(r.get("waiting")),
                    # A tool that may run on a local model stays home.
                    "held": harness not in VENDOR_TOOLS})
    return out


def running_block(platform: Any) -> str:
    """The lines for ``work_search``: :data:`HEAD` and one line per coding
    session running, not held and allowed past the wall, or :data:`NOTHING`.
    Never raises (a part that cannot be read says nothing is known)."""
    try:
        from .block import _safe
        from .clients import wall

        rows = _rows(platform)
        if not rows:
            return NOTHING
        turn, cmap = wall.outside_and_map(getattr(platform, "engine", None))
        lines: list[str] = []
        for r in rows:
            if r.get("held", True) is not False:
                continue  # Local only or kept home (no fact: held)
            if not wall.may_tell(turn, cmap, folder=r["folder"], title=r["title"], files=[]):
                continue
            state = " (waiting for the user)" if r["waiting"] else ""
            line = _safe(f"- {r['where']}: {r['title'] or '(no title yet)'}, in {r['folder']}{state}")
            if line:
                lines.append(line)
            if len(lines) >= RUNNING_MAX:
                break
        return "\n".join([HEAD, *lines]) if lines else NOTHING
    except Exception:  # noqa: BLE001 — a part that cannot be read
        log.debug("running now could not be read", exc_info=True)
        return ""


__all__ = ["HEAD", "NOTHING", "RUNNING_MAX", "running_block"]
