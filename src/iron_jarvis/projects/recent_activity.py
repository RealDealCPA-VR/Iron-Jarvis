"""'Recent activity in this project' — runs, chats and coding sessions
(v1.414.0).

The block every chat turn and agent run in a project carries ("Recent
activity in this project (newest first):") read the project's last five
``Session`` rows and nothing else, so a project whose real work happened in
its chats or its coding sessions looked idle to the model. This merges in the
project's recent CHATS (threads tagged with the project) and its CODING
SESSIONS from both Code views (``projects/coding_sessions`` — by folder),
newest first, under a small fixed cap.

Rules, each pinned:

* BYTE-IDENTICAL when the project has no chats and no coding sessions: the
  run lines are built here in the exact format both callers always used
  (:func:`run_line`) and come back in the same order, so a project with only
  runs gets the same five lines as before.
* A chat / coding-session line is a TITLE, its KIND and its AGE — never a
  transcript. Every title goes through ``core/promptguard.scan_context``; a
  flagged one is left out (never repeated as a placeholder: a title is the
  user's typing, and a line naming a blocked title helps no one).
* LOCAL ONLY (v1.332.0): a chat or coding session switched to local only
  keeps even its title home from work that leaves this network — the fabric
  recall's own rule (``egress.may_recall_local_only_chats``: only a turn whose
  scope is on and that the user has not allowed off the network for this
  message may see them). The scope is a context variable and
  ``asyncio.to_thread`` copies it, so this reads the CALLER's scope.
* The turn's OWN conversation (``exclude_thread``) is not listed as recent
  activity of itself.
* BLOCKING (SQLite), bounded (a few indexed reads), never raises: a store
  that cannot answer leaves the extra lines out and the run lines stand.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Iterable

from sqlalchemy import literal_column
from sqlmodel import select

from ..core.db import session_scope

log = logging.getLogger(__name__)

#: Run lines (the cap the block always had).
RUNS_MAX = 5
#: Chats / coding sessions looked at, each.
OTHERS_MAX = 5
#: Lines in the merged block, at most.
RECENT_MAX = 8
#: A title's length on its line.
TITLE_CHARS = 80
#: Coding-session rows of each view looked at for the block (the newest —
#: recent activity is about now, so far fewer than the project list's scan).
RECENT_SCAN = 60

KIND_CHAT = "chat"
KIND_CODING = "coding session"


def run_line(s: Any) -> str:
    """One run's line — THE format both callers used (v1.141.0 parity)."""
    status = getattr(getattr(s, "status", None), "value", getattr(s, "status", ""))
    return f"- [{status}] {str(s.task)[:80]}: {(s.summary or '(no summary)')[:160]}"


def _naive(value: Any) -> datetime:
    if not isinstance(value, datetime):
        return datetime.min
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def age_words(when: Any, now: datetime) -> str:
    """Coarse on purpose (the prompt should not change every minute):
    "within the hour", "N h ago", "N d ago"."""
    then = _naive(when)
    if then == datetime.min:
        return "a while ago"
    secs = max(0.0, (now - then).total_seconds())
    if secs < 3600:
        return "within the hour"
    if secs < 86400:
        return f"{int(secs // 3600)} h ago"
    return f"{int(secs // 86400)} d ago"


def _clean_title(raw: Any) -> str:
    title = " ".join(str(raw or "").split())
    if len(title) > TITLE_CHARS:
        title = title[: TITLE_CHARS - 1].rstrip() + "…"
    return title


def _safe_title(raw: Any, source: str) -> str:
    """The title, or "" when it is empty or reads as an injection."""
    title = _clean_title(raw)
    if not title:
        return ""
    from ..core.promptguard import scan_context

    if scan_context(title, source=source, cap=None).blocked:
        return ""
    return title


def _withhold_local_only() -> bool:
    try:
        from ..providers.egress import may_recall_local_only_chats

        return not may_recall_local_only_chats()
    except Exception:  # noqa: BLE001 — unsure: keep them home (fail closed)
        return True


def _chat_rows(engine: Any, project_id: str, exclude: str) -> list[tuple[str, Any, Any, Any]]:
    from ..core.models import ChatThreadRecord as T, chat_surface_only

    with session_scope(engine) as db:
        stmt = (
            select(T.id, T.title, T.updated_at, T.setup_json)
            .where(T.project_id == project_id)
            .where(chat_surface_only(T.surface))
            .where(T.archived_at.is_(None))  # type: ignore[union-attr]
            .order_by(T.updated_at.desc(), literal_column("rowid").desc())  # type: ignore[attr-defined]
            .limit(OTHERS_MAX + 1)
        )
        rows = list(db.exec(stmt))
    return [(str(t), title, u, s) for t, title, u, s in rows if str(t) != exclude][:OTHERS_MAX]


def extra_entries(
    engine: Any, project_id: str, *, platform: Any = None, exclude_thread: str = "",
    now: datetime | None = None,
) -> list[tuple[datetime, str]]:
    """``[(when, line)]`` for the project's recent chats and coding sessions
    (never runs). Never raises."""
    pid = str(project_id or "")
    if not pid or engine is None:
        return []
    exclude = str(exclude_thread or "")
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    withhold = _withhold_local_only()
    out: list[tuple[datetime, str]] = []
    try:
        from ..providers.egress import setup_says_local_only

        for _tid, title, updated, setup in _chat_rows(engine, pid, exclude):
            if withhold and setup_says_local_only(setup or ""):
                continue
            words = _safe_title(title, "chat title") or ""
            if not words:
                continue
            out.append((_naive(updated), f"- [{KIND_CHAT}] {words} · {age_words(updated, now)}"))
    except Exception:  # noqa: BLE001 — the chats are extra; the runs stand
        log.debug("recent chats for the project could not be read", exc_info=True)
    try:
        from .coding_sessions import for_project

        rows = for_project(
            engine, pid, platform=platform, scan=RECENT_SCAN, limit=OTHERS_MAX + 1,
            with_state=False,
        )
        kept = 0
        for r in rows:
            if r["id"] == exclude or kept >= OTHERS_MAX:
                continue
            if withhold and r.get("local_only"):
                continue
            words = _safe_title(r.get("title"), "coding session title")
            if not words:
                continue
            kind = f"{KIND_CODING} · {r['tool']}" if r.get("tool") else KIND_CODING
            out.append((_naive(r.get("_updated")),
                        f"- [{kind}] {words} · {age_words(r.get('_updated'), now)}"))
            kept += 1
    except Exception:  # noqa: BLE001
        log.debug("recent coding sessions for the project could not be read", exc_info=True)
    return out


def recent_lines(
    engine: Any,
    project_id: str,
    runs: Iterable[Any],
    *,
    platform: Any = None,
    exclude_thread: str = "",
    now: datetime | None = None,
) -> list[str]:
    """The block's lines, newest first: ``runs`` (the caller's ``Session``
    rows, newest first, at most :data:`RUNS_MAX`) merged with the project's
    recent chats and coding sessions, at most :data:`RECENT_MAX`. With no
    chats and no coding sessions this is exactly ``[run_line(s) for s in
    runs]``."""
    run_list = list(runs)
    base = [(_naive(getattr(s, "created_at", None)), run_line(s)) for s in run_list]
    extras = extra_entries(
        engine, project_id, platform=platform, exclude_thread=exclude_thread, now=now,
    )
    if not extras:
        return [line for _when, line in base]
    merged = base + extras
    # Stable: equal times keep runs before the others, each in its own order.
    merged.sort(key=lambda pair: pair[0], reverse=True)
    return [line for _when, line in merged[:RECENT_MAX]]


__all__ = [
    "KIND_CHAT",
    "KIND_CODING",
    "OTHERS_MAX",
    "RECENT_MAX",
    "RUNS_MAX",
    "age_words",
    "extra_entries",
    "recent_lines",
    "run_line",
]
