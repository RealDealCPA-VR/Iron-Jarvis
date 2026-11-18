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
* Every row goes through the client wall of an asker that is not the owner
  (no folder, no words), exactly as ``work_search``'s other lines do: work
  for a client is never told to another app.

BLOCKING (the record's refresh): a caller is off the event loop.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("iron_jarvis.workmap.running")

#: The most rows told.
RUNNING_MAX = 8
HEAD = "Running now in Iron Jarvis:"
NOTHING = "Nothing is running in Iron Jarvis right now."


def _rows(platform: Any) -> list[dict[str, Any]]:
    """``[{where, title, folder, waiting}]`` for each coding session running."""
    from ..core.turns import TURNS
    from ..daemon import durable_turns
    from ..hosted.news import running_rows
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
            for r in build:
                row = by_ref.get(str(r.get("thread_id") or ""))
                if row is None or not row.get("folder"):
                    continue  # its folder is not known: left out
                out.append({"where": "Chat › Code", "title": row.get("title") or "",
                            "folder": row["folder"], "waiting": bool(r.get("waiting"))})
    svc = getattr(platform, "hosted", None)
    for r in running_rows(platform):
        live = getattr(svc, "live", {}).get(str(r.get("thread_id") or "")) if svc is not None else None
        folder = str(getattr(live, "folder", "") or "")
        if not folder:
            continue
        from ..hosted.words import label

        out.append({"where": f"Build › Code ({label(str(r.get('harness') or ''))})",
                    "title": str(r.get("title") or ""), "folder": folder, "waiting": bool(r.get("waiting"))})
    return out


def running_block(platform: Any) -> str:
    """The lines for ``work_search``: :data:`HEAD` and one line per coding
    session running and allowed past the wall, or :data:`NOTHING`. Never
    raises (a part that cannot be read says nothing is known)."""
    try:
        from .block import _safe
        from .clients import wall

        rows = _rows(platform)
        if not rows:
            return NOTHING
        turn, cmap = wall.turn_and_map(getattr(platform, "engine", None), "", "")
        lines: list[str] = []
        for r in rows:
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
