"""One search over files, memory and conversations (C-08).

THE NEED: "where's the Smith 2024 summary sheet?" and "what did we note about
the S-corp election?" lived in three different places — the palette searched
titles and conversation history, memory recall had its own box on /memory, and
files the app made were not searchable at all. ``GET /search/all`` answers all
three at once so the command palette can show them in one list.

THREE LANES, RUN SIDE BY SIDE, EACH ON ITS OWN CLOCK. A lane that is slow (a
huge project folder, a cold memory store) must not hold the others hostage,
and must not freeze the daemon: every lane runs in a worker thread under
``LANE_DEADLINE_S``, the file walk additionally stops at ``FILE_WALK_CAP``
files and ``FILES_DEADLINE_S`` seconds, and any lane that was cut short is
NAMED in ``partial`` — a list that stopped early is never presented as
complete. A lane that fails returns empty; the endpoint never raises.

WHERE THE FILES ARE: the chat's own folder root (``config.chat_files_dir``,
where attach-and-ask chats save their work) and every project's folder. Roots
the file policy refuses are skipped. The active project's folder is walked
first and its files rank first — "favour", never "filter".
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger("iron_jarvis.search")

MIN_QUERY = 2
LANE_LIMIT = 8
LANE_DEADLINE_S = 3.0
FILES_DEADLINE_S = 2.0
FILE_WALK_CAP = 6_000
#: Memory stores this lane covers. ``chats``/``sessions`` are the history
#: lane's job and ``files`` is the file lane's, so they are not asked twice.
MEMORY_SOURCES = ["notes", "memory", "knowledge", "lessons"]
_MEMORY_KIND = {
    "notes": "note",
    "memory": "memory",
    "knowledge": "project",
    "lessons": "lesson",
}


def query_words(q: str) -> list[str]:
    return [w for w in (q or "").lower().split() if len(w) >= 2][:6]


def file_roots(platform: Any, project_id: str = "") -> list[tuple[Path, str | None]]:
    """``[(folder, owning project id or None)]``, active project first.
    BLOCKING (database + disk)."""
    from sqlmodel import select

    from ..core.db import session_scope
    from ..core.fs_policy import fs_read_ok
    from ..core.models import Project

    roots: list[tuple[Path, str | None]] = []
    seen: set[str] = set()

    def add(raw: Any, pid: str | None) -> None:
        try:
            rp = Path(str(raw)).expanduser().resolve()
        except (OSError, ValueError):
            return
        key = str(rp).lower()
        if key in seen or not rp.is_dir():
            return
        ok, _why = fs_read_ok(rp)
        if not ok:
            return
        seen.add(key)
        roots.append((rp, pid))

    projects: list[tuple[str, str]] = []
    try:
        with session_scope(platform.engine) as db:
            projects = [
                (p.id, p.root) for p in db.exec(select(Project)) if (p.root or "").strip()
            ]
    except Exception:  # noqa: BLE001 — no projects readable = just the chat folder
        projects = []
    for pid, root in sorted(projects, key=lambda t: t[0] != project_id):
        add(root, pid)
    try:
        add(platform.config.chat_files_dir, None)
    except Exception:  # noqa: BLE001 — an unset Documents folder is not an error
        pass
    return roots


def find_files(platform: Any, words: list[str], project_id: str = "") -> tuple[list[dict], bool]:
    """The file lane. BLOCKING."""
    roots = file_roots(platform, project_id)
    fs = getattr(platform, "filesearch", None)
    if not roots or not words or fs is None:
        return [], False
    hits, partial = fs.search_words(
        words,
        [r for r, _ in roots],
        limit=LANE_LIMIT * 3,
        max_walk=FILE_WALK_CAP,
        deadline_s=FILES_DEADLINE_S,
    )
    owner = {str(r).lower(): pid for r, pid in roots}
    rows: list[dict[str, Any]] = []
    for h in hits:
        p = Path(h["path"])
        try:
            mtime = p.stat().st_mtime
        except OSError:
            continue
        rows.append(
            {
                "kind": "file",
                "title": p.name,
                "path": str(p),
                "folder": str(p.parent),
                "at": datetime.fromtimestamp(mtime, timezone.utc).isoformat(),
                "project_id": owner.get(str(h["root"]).lower()),
                "_mtime": mtime,
            }
        )
    rows.sort(
        key=lambda r: (
            bool(project_id) and r["project_id"] != project_id,
            -r["_mtime"],
        )
    )
    for r in rows:
        r.pop("_mtime", None)
    return rows[:LANE_LIMIT], partial


def find_memory(platform: Any, q: str, project_id: str = "") -> list[dict]:
    """The memory lane: notes, remembered facts, project knowledge, lessons."""
    fabric = getattr(platform, "fabric", None)
    if fabric is None:
        return []
    hits = fabric.recall(
        q,
        k=LANE_LIMIT,
        project_id=project_id or None,
        sources=MEMORY_SOURCES,
        min_score=0.05,
    )
    out: list[dict[str, Any]] = []
    for h in hits:
        pid = str((h.extra or {}).get("project_id") or "")
        out.append(
            {
                "kind": _MEMORY_KIND.get(h.source, h.source),
                "source": h.source,
                "title": (h.title or h.ref or _MEMORY_KIND.get(h.source, h.source))[:160],
                "snippet": (h.snippet or "")[:200],
                "href": f"/projects/{pid}" if h.source == "knowledge" and pid else "/memory",
                "project_id": pid or None,
                "score": round(float(h.score), 4),
            }
        )
    if project_id:
        out.sort(key=lambda r: r["project_id"] != project_id)
    return out


def find_history(index: Any, q: str, project_id: str = "") -> list[dict]:
    """The conversation lane (the palette already shows this one; other
    callers get it here too)."""
    if index is None:
        return []
    rows = [h.as_dict() for h in index.search(q, limit=LANE_LIMIT)]
    if project_id:
        rows.sort(key=lambda r: r.get("project_id") != project_id)
    return rows


async def search_all(platform: Any, index: Any, q: str, project_id: str = "") -> dict[str, Any]:
    """Run the three lanes side by side; never raises."""
    q = " ".join((q or "").split())[:200]
    pid = (project_id or "").strip()
    out: dict[str, Any] = {"q": q, "files": [], "memory": [], "history": [], "partial": []}
    if len(q) < MIN_QUERY:
        return out

    async def lane(name: str, fn: Any, *args: Any) -> tuple[str, Any, bool]:
        try:
            value = await asyncio.wait_for(asyncio.to_thread(fn, *args), timeout=LANE_DEADLINE_S)
            return name, value, False
        except asyncio.TimeoutError:
            return name, None, True
        except Exception:  # noqa: BLE001 — one broken store is one empty lane
            log.warning("search lane %s failed", name, exc_info=True)
            return name, None, False

    results = await asyncio.gather(
        lane("files", find_files, platform, query_words(q), pid),
        lane("memory", find_memory, platform, q, pid),
        lane("history", find_history, index, q, pid),
    )
    for name, value, timed_out in results:
        if timed_out:
            out["partial"].append(name)
            continue
        if value is None:
            continue
        if name == "files":
            rows, cut = value
            out["files"] = rows
            if cut:
                out["partial"].append("files")
        else:
            out[name] = value
    return out
