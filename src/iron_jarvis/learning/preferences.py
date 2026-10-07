"""Preferences you approve (v1.305.0; idea from agent-personalizer, MIT).

Every lesson now carries a STATUS (``learning/models.py``): ``confirmed`` (it
reaches prompts), ``proposed`` (noticed, waiting for the user) or ``declined``
(the user said "not this" — final). This module owns the proposed/declined
life cycle:

* :func:`suggest_for_turn` — the ONE helper both chat lanes call after a turn
  (lock-step). When the user's message is a correction of HOW the assistant
  answers (``learning.corrections.is_correction``) and the user made a
  SIMILAR correction in a DIFFERENT turn within the last 30 days (this
  conversation's earlier turns, other chat threads, phone threads), it mints
  ONE ``proposed`` row and returns the ``suggestion`` the done frame / POST
  response carries. Never raises, never slows a turn noticeably: the
  detector runs first (pure regex, on the loop), the DB look-back runs off
  the loop under a time budget, and any failure answers ``None``.
* :func:`scan_sources` — the consent-per-press deeper look: the user's own
  typed messages in their newest Claude Code / Codex sessions (the v1.290.0
  read-only ``history/`` reader; the session's OWN file only — subagent
  transcripts hold the parent agent's prompts, not the user's), the same
  detector, the same caps.
* list / keep / decline / ask-again / edit / forget for the routes.

Rules every path keeps: a proposal whose signature matches ANY existing
confirmed, proposed or declined row is never minted (declined is final until
the user presses "Ask again", which deletes the declined row); at most
:data:`MAX_OPEN` proposed rows exist at once (the oldest are kept; extras are
not minted); evidence quotes are clipped to :data:`QUOTE_CHARS`; no message
text is ever logged.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlmodel import select

from ..core.db import session_scope
from ..core.ids import utcnow
from .corrections import MAX_CHARS, is_correction, proposal_text, signature, similar
from .models import (
    STATUS_CONFIRMED,
    STATUS_DECLINED,
    STATUS_PROPOSED,
    LessonRecord,
    lesson_status,
)

log = logging.getLogger(__name__)

#: At most this many proposed rows wait for the user at once.
MAX_OPEN = 3
#: How far back the chat look-back reads.
LOOKBACK_DAYS = 30
#: Most chat threads the look-back opens (newest first).
MAX_THREADS = 200
#: Evidence entries kept on one row / quotes on one suggestion.
MAX_EVIDENCE = 5
MAX_QUOTES = 2
#: Longest evidence quote.
QUOTE_CHARS = 160
#: Longest kept / edited preference sentence.
MAX_TEXT = 280
#: Rows the Kept list shows.
KEPT_LIMIT = 100
#: The in-turn look-back's time budget (seconds) — a suggestion is never
#: worth a slow reply. Past it the turn answers ``suggestion: null``.
TURN_BUDGET_S = 2.0
#: Newest sessions per source the deeper look reads.
SCAN_SESSIONS = 20

WHERES = ("chat", "phone", "build", "claude-code", "codex")
SCAN_SOURCES = {"claude-code": "Claude Code", "codex": "Codex"}

#: Sources the prompt never reads (task reflections — see learning.engine).
_NOT_PREFERENCES = ("reflection",)

#: One minting at a time, so two turns cannot both pass the cap / never-ask check.
_MINT_LOCK = threading.Lock()


class PreferenceConflict(ValueError):
    """The row is not in a state that action applies to (-> 409)."""


class PreferenceInvalid(ValueError):
    """The sentence cannot be kept (-> 400)."""


# --------------------------------------------------------------------------- #
# views
# --------------------------------------------------------------------------- #


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    try:
        if isinstance(value, datetime):
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            return value.isoformat()
    except Exception:  # noqa: BLE001
        pass
    return str(value) or None


def evidence_of(row: Any) -> list[dict[str, Any]]:
    """The row's evidence list, validated (a corrupt blob reads as [])."""
    raw = getattr(row, "evidence_json", None)
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return []
    out: list[dict[str, Any]] = []
    if not isinstance(data, list):
        return out
    for item in data[:MAX_EVIDENCE]:
        if not isinstance(item, dict):
            continue
        ev = _evidence(
            str(item.get("quote") or ""),
            at=str(item.get("at") or ""),
            where=str(item.get("where") or ""),
            link=str(item.get("link") or ""),
        )
        if ev is not None:
            out.append(ev)
    return out


def _origin(row: Any) -> str | None:
    raw = getattr(row, "origin", None)
    if raw in ("said", "noticed"):
        return raw
    # An older row: a stated preference was said; anything else has no origin.
    return "said" if getattr(row, "source", "") == "preference" and raw is None else None


def pref_view(row: Any) -> dict[str, Any]:
    """The one wire shape of a preference row (every route returns this)."""
    evidence = evidence_of(row)
    return {
        "id": str(getattr(row, "id", "") or ""),
        "text": str(getattr(row, "text", "") or ""),
        "status": lesson_status(row),
        "origin": _origin(row),
        "source": str(getattr(row, "source", "") or ""),
        "weight": int(getattr(row, "weight", 0) or 0),
        "created_at": _iso(getattr(row, "created_at", None)),
        "decided_at": _iso(getattr(row, "decided_at", None)),
        "count": len(evidence),
        "evidence": evidence,
    }


def _evidence(quote: str, *, at: str = "", where: str = "chat", link: str = "") -> dict[str, Any] | None:
    q = " ".join(str(quote or "").split())
    if not q:
        return None
    if len(q) > QUOTE_CHARS:
        q = q[: QUOTE_CHARS - 1].rstrip() + "…"
    out: dict[str, Any] = {
        "quote": q,
        "at": at or "",
        "where": where if where in WHERES else "chat",
    }
    if link:
        out["link"] = link
    return out


# --------------------------------------------------------------------------- #
# list + decisions
# --------------------------------------------------------------------------- #


def list_preferences(engine) -> dict[str, Any]:
    """``{kept, suggested, never}`` — every list bounded."""
    from .engine import confirmed_clause

    with session_scope(engine) as db:
        kept = list(
            db.exec(
                select(LessonRecord)
                .where(LessonRecord.scope == "user")
                .where(LessonRecord.source.not_in(list(_NOT_PREFERENCES)))
                .where(confirmed_clause())
                .order_by(LessonRecord.weight.desc(), LessonRecord.created_at.desc())
                .limit(KEPT_LIMIT)
            )
        )
        suggested = list(
            db.exec(
                select(LessonRecord)
                .where(LessonRecord.status == STATUS_PROPOSED)
                .order_by(LessonRecord.created_at.asc())
                .limit(KEPT_LIMIT)
            )
        )
        never = list(
            db.exec(
                select(LessonRecord)
                .where(LessonRecord.status == STATUS_DECLINED)
                .order_by(LessonRecord.decided_at.desc(), LessonRecord.created_at.desc())
                .limit(KEPT_LIMIT)
            )
        )
    return {
        "kept": [pref_view(r) for r in kept],
        "suggested": [pref_view(r) for r in suggested],
        "never": [pref_view(r) for r in never],
        "open_limit": MAX_OPEN,
    }


def clean_text(text: Any) -> str:
    """A kept / edited sentence: 1–280 chars, scanned for prompt injection.

    Raises :class:`PreferenceInvalid` with a plain sentence otherwise."""
    from ..core.promptguard import scan_context

    clean = " ".join(str(text or "").split())
    if not clean:
        raise PreferenceInvalid("Write the preference as a sentence first — it is empty.")
    if len(clean) > MAX_TEXT:
        raise PreferenceInvalid(
            f"That preference is {len(clean)} characters — keep it to {MAX_TEXT} or fewer."
        )
    result = scan_context(clean, source="preference", cap=None)
    if result.blocked:
        raise PreferenceInvalid(
            "That sentence reads like an instruction planted for the assistant, "
            "so it was not kept — say it in your own words."
        )
    return clean


def _get(db, pref_id: str) -> LessonRecord:
    row = db.get(LessonRecord, pref_id) if pref_id else None
    if row is None:
        raise KeyError(pref_id)
    return row


def _detached(db, row: LessonRecord) -> LessonRecord:
    db.refresh(row)
    db.expunge(row)
    return row


def keep(engine, pref_id: str, text: Any = None) -> LessonRecord:
    """proposed -> confirmed (optionally with the user's edited sentence)."""
    new_text = clean_text(text) if text is not None else None
    with session_scope(engine) as db:
        row = _get(db, pref_id)
        if lesson_status(row) != STATUS_PROPOSED:
            raise PreferenceConflict("That is not a suggestion waiting for an answer any more.")
        if new_text is not None:
            row.text = new_text
        row.status = STATUS_CONFIRMED
        row.decided_at = utcnow()
        db.add(row)
        db.commit()
        out = _detached(db, row)
    _shared_changed()
    return out


def decline(engine, pref_id: str) -> LessonRecord:
    """proposed -> declined (final: never proposed again)."""
    with session_scope(engine) as db:
        row = _get(db, pref_id)
        if lesson_status(row) != STATUS_PROPOSED:
            raise PreferenceConflict("That is not a suggestion waiting for an answer any more.")
        row.status = STATUS_DECLINED
        row.decided_at = utcnow()
        db.add(row)
        db.commit()
        return _detached(db, row)


def ask_again(engine, pref_id: str) -> str:
    """Delete a declined row, so the same correction may be suggested again."""
    with session_scope(engine) as db:
        row = _get(db, pref_id)
        if lesson_status(row) != STATUS_DECLINED:
            raise PreferenceConflict("Only a preference you said \"not this\" to can be asked again.")
        db.delete(row)
        db.commit()
    return pref_id


def edit(engine, pref_id: str, text: Any) -> LessonRecord:
    """Change a kept preference's sentence."""
    new_text = clean_text(text)
    with session_scope(engine) as db:
        row = _get(db, pref_id)
        if lesson_status(row) != STATUS_CONFIRMED:
            raise PreferenceConflict("Only a kept preference can be edited — keep it first.")
        row.text = new_text
        db.add(row)
        db.commit()
        out = _detached(db, row)
    _shared_changed()
    return out


def forget(engine, pref_id: str) -> str:
    """Delete a kept preference."""
    with session_scope(engine) as db:
        row = _get(db, pref_id)
        if lesson_status(row) != STATUS_CONFIRMED:
            raise PreferenceConflict(
                "Only a kept preference can be forgotten here — a suggestion is answered with Not this."
            )
        db.delete(row)
        db.commit()
    _shared_changed()
    return pref_id


def _shared_changed() -> None:
    """v1.306.0: a confirmed preference changed — a switched-on "share with
    Build" re-renders its block (debounced, off the loop; never raises)."""
    from ..profile.share import notify_changed

    notify_changed()


# --------------------------------------------------------------------------- #
# minting
# --------------------------------------------------------------------------- #


def _blocked(db, sig: str) -> bool:
    """Does any existing row already cover ``sig``? Confirmed (kept or said),
    proposed (already asked) and declined (never ask again) all block."""
    rows = db.exec(
        select(LessonRecord.text, LessonRecord.signature, LessonRecord.status, LessonRecord.source)
        .where(LessonRecord.source.not_in(list(_NOT_PREFERENCES)))
        .limit(2000)
    )
    for text, row_sig, _status, _source in rows:
        other = row_sig if row_sig else signature(text or "")
        if similar(sig, other):
            return True
    return False


def _open_count(db) -> int:
    return len(
        list(
            db.exec(
                select(LessonRecord.id).where(LessonRecord.status == STATUS_PROPOSED).limit(MAX_OPEN + 1)
            )
        )
    )


def mint(engine, *, quotes: list[str], sig: str, evidence: list[dict[str, Any]]) -> LessonRecord | None:
    """Mint ONE proposed row, or ``None`` (covered already / cap full / no
    usable sentence). Serialised by a lock so concurrent turns cannot both
    pass the checks."""
    text = proposal_text(quotes)
    if not text or not sig:
        return None
    with _MINT_LOCK, session_scope(engine) as db:
        if _blocked(db, sig):
            return None
        if _open_count(db) >= MAX_OPEN:
            return None
        row = LessonRecord(
            text=text,
            scope="user",
            source="preference",
            weight=5,
            status=STATUS_PROPOSED,
            origin="noticed",
            evidence_json=json.dumps(evidence[:MAX_EVIDENCE]),
            signature=sig,
        )
        db.add(row)
        db.commit()
        return _detached(db, row)


# --------------------------------------------------------------------------- #
# the chat look-back
# --------------------------------------------------------------------------- #


def _norm(text: Any) -> str:
    return " ".join(str(text or "").split())


def _parse_at(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _user_texts(messages: Any) -> list[tuple[str, Any]]:
    out: list[tuple[str, Any]] = []
    if not isinstance(messages, list):
        return out
    for m in messages:
        if isinstance(m, dict) and m.get("role") == "user":
            out.append((_norm(m.get("content")), m.get("at")))
    return out


def _same_conversation(stored: list[str], current: list[str]) -> bool:
    """Is a stored thread THIS conversation (or a fork of it)? Its user
    messages and the turn's agree from the first one on. Those messages are
    already in the turn's body (or ARE the current message), so they must not
    be counted twice."""
    if not stored or not current or stored[0] != current[0]:
        return False
    return all(a == b for a, b in zip(stored, current))


def _matches(text: str, sig: str) -> bool:
    if not text or len(text) > MAX_CHARS:
        return False
    return is_correction(text) and similar(signature(text), sig)


def chat_occurrences(
    engine, body_user: list[str], cur_sig: str, *, now: datetime, where_now: str = "chat"
) -> list[dict[str, Any]]:
    """Earlier similar corrections: this conversation's earlier turns (from the
    body) and other chat / phone threads touched in the last 30 days.
    Newest first; each ``{quote, at, where, link?}``."""
    from ..core.models import ChatThreadRecord

    found: list[tuple[str, dict[str, Any]]] = []
    for text in body_user[:-1]:
        if _matches(text, cur_sig):
            ev = _evidence(text, at="", where=where_now)
            if ev is not None:
                found.append(("", ev))
    cutoff = now - timedelta(days=LOOKBACK_DAYS)
    with session_scope(engine) as db:
        rows = list(
            db.exec(
                select(
                    ChatThreadRecord.id,
                    ChatThreadRecord.owner,
                    ChatThreadRecord.messages_json,
                    ChatThreadRecord.updated_at,
                )
                .where(ChatThreadRecord.updated_at >= cutoff)
                .order_by(ChatThreadRecord.updated_at.desc())
                .limit(MAX_THREADS)
            )
        )
    for thread_id, owner, blob, updated in rows:
        try:
            msgs = json.loads(blob or "[]")
        except (TypeError, ValueError):
            continue
        users = _user_texts(msgs)
        if _same_conversation([t for t, _ in users], body_user):
            continue
        where = "phone" if (owner or "user") == "daemon" else "chat"
        fallback = _iso(updated) or ""
        for text, at_raw in users:
            at = _parse_at(at_raw)
            if at is not None and at < cutoff:
                continue
            if not _matches(text, cur_sig):
                continue
            ev = _evidence(
                text,
                at=(at.isoformat() if at is not None else fallback),
                where=where,
                link=f"/chat?thread={thread_id}",
            )
            if ev is not None:
                found.append((ev["at"], ev))
    found.sort(key=lambda pair: pair[0], reverse=True)
    return [ev for _, ev in found]


def _since(evidence: list[dict[str, Any]]) -> str:
    stamps = [e["at"] for e in evidence if e.get("at")]
    return min(stamps) if stamps else ""


def suggest_from_turn(engine, body: Any) -> dict[str, Any] | None:
    """The synchronous core of :func:`suggest_for_turn` (call OFF the loop)."""
    msgs = list(getattr(body, "messages", None) or [])
    body_user = [
        _norm(getattr(m, "content", "")) for m in msgs if getattr(m, "role", "") == "user"
    ]
    if not body_user or not body_user[-1]:
        return None
    current = body_user[-1]
    if not is_correction(current):
        return None
    cur_sig = signature(current)
    if not cur_sig:
        return None
    now = utcnow()
    where_now = "build" if str(getattr(body, "pane_id", "") or "").strip() else "chat"
    earlier = chat_occurrences(engine, body_user, cur_sig, now=now, where_now=where_now)
    if not earlier:
        return None
    first = _evidence(current, at=now.isoformat(), where=where_now)
    if first is None:
        return None
    evidence = [first, *earlier][:MAX_EVIDENCE]
    row = mint(engine, quotes=[e["quote"] for e in evidence], sig=cur_sig, evidence=evidence)
    if row is None:
        return None
    return {
        "id": row.id,
        "text": row.text,
        "count": 1 + len(earlier),
        "quotes": evidence[:MAX_QUOTES],
        "since": _since([first, *earlier]),
    }


async def suggest_for_turn(platform: Any, body: Any) -> dict[str, Any] | None:
    """THE lock-step helper both chat lanes call once per finished turn.

    Returns the ``suggestion`` value for the done frame / POST response —
    ``None`` almost always. The detector runs here first (pure, cheap); only a
    correction-shaped message pays the off-loop look-back, under
    :data:`TURN_BUDGET_S`. Never raises. (A look-back that overruns the
    budget keeps running on its worker thread; a row it mints then is not on
    this reply's card but waits on the Memory page.)
    """
    try:
        msgs = list(getattr(body, "messages", None) or [])
        last = ""
        for m in reversed(msgs):
            if getattr(m, "role", "") == "user":
                last = str(getattr(m, "content", "") or "")
                break
        if not last.strip() or not is_correction(last):
            return None
        engine = getattr(platform, "engine", None)
        if engine is None:
            return None
        suggestion = await asyncio.wait_for(
            asyncio.to_thread(suggest_from_turn, engine, body), timeout=TURN_BUDGET_S
        )
        if suggestion is not None:
            await _publish_suggested(platform, suggestion["id"], suggestion["text"], suggestion["count"], "chat")
        return suggestion
    except Exception as exc:  # noqa: BLE001 — a suggestion never fails a turn
        # The exception's TYPE only: a database error's text carries the
        # INSERT's parameters — the user's quoted words (reviewer).
        log.warning("preference suggestion skipped (%s)", type(exc).__name__)
        return None


async def _publish(platform: Any, event: str, payload: dict[str, Any]) -> None:
    bus = getattr(platform, "event_bus", None)
    if bus is None:
        return
    try:
        await bus.publish(event, payload)
    except Exception:  # noqa: BLE001 — a bus hiccup never loses the row
        log.debug("%s publish failed", event, exc_info=True)


async def _publish_suggested(platform: Any, pref_id: str, text: str, count: int, via: str) -> None:
    from ..core.events import EventType

    await _publish(
        platform,
        EventType.PREFERENCE_SUGGESTED,
        {"id": pref_id, "text": text, "count": int(count), "via": via},
    )


async def publish_kept(platform: Any, row: Any) -> None:
    from ..core.events import EventType

    await _publish(platform, EventType.PREFERENCE_KEPT, {"id": row.id, "text": row.text})


async def publish_declined(platform: Any, row: Any) -> None:
    from ..core.events import EventType

    await _publish(platform, EventType.PREFERENCE_DECLINED, {"id": row.id})


# --------------------------------------------------------------------------- #
# the consent-per-press deeper look
# --------------------------------------------------------------------------- #


def sources_available() -> list[dict[str, Any]]:
    """Which of the user's other coding agents keep history on this PC."""
    from ..history import claude_code, codex

    out = []
    for sid, mod, sub in (("claude-code", claude_code, "projects"), ("codex", codex, "sessions")):
        try:
            available = (Path(mod.root()) / sub).is_dir()
        except Exception:  # noqa: BLE001
            available = False
        out.append({"id": sid, "label": SCAN_SOURCES[sid], "available": available})
    return out


def _typed_messages(harness: str, limit: int = SCAN_SESSIONS) -> tuple[int, list[dict[str, Any]]]:
    """``(sessions read, [{text, at}])`` — the user's OWN typed prompts in the
    newest ``limit`` sessions of ``harness``. The session's own file only:
    subagent transcripts hold the parent agent's prompts, not the user's.
    Read-only (the history reader opens files ``rb``)."""
    from ..history import index as hindex

    mod = hindex.HARNESSES[harness]
    read = 0
    out: list[dict[str, Any]] = []
    for summary in hindex.list_sessions(harness, limit=limit)[:limit]:
        path = Path(summary.get("file") or "")
        if not path.name:
            continue
        cut: list = []
        try:
            events = mod.map_events(hindex._records(path, cut), path, summary.get("id", ""))
        except OSError:
            continue
        read += 1
        for ev in events:
            if ev.get("action") != "prompt.submitted" or ev.get("subagent"):
                continue
            text = _norm(ev.get("text"))
            # A record wrapped in markup is the CLI's own, never typed: Claude
            # Code's `!` bash mode stores the command's OUTPUT as a user record
            # ("<bash-stdout>…</bash-stdout>"), as it does memory input and
            # other wrappers the history reader does not list (reviewer).
            if text.startswith("<"):
                continue
            if text and len(text) <= MAX_CHARS:
                out.append({"text": text, "at": str(ev.get("ts") or "")})
    return read, out


def _recent_chat_corrections(engine, now: datetime) -> list[dict[str, Any]]:
    """Correction-shaped user messages in chat / phone threads (30 days) —
    counted toward a scanned cluster, never minted on their own here."""
    from ..core.models import ChatThreadRecord

    cutoff = now - timedelta(days=LOOKBACK_DAYS)
    with session_scope(engine) as db:
        rows = list(
            db.exec(
                select(ChatThreadRecord.id, ChatThreadRecord.owner, ChatThreadRecord.messages_json)
                .where(ChatThreadRecord.updated_at >= cutoff)
                .order_by(ChatThreadRecord.updated_at.desc())
                .limit(MAX_THREADS)
            )
        )
    out: list[dict[str, Any]] = []
    for thread_id, owner, blob in rows:
        try:
            msgs = json.loads(blob or "[]")
        except (TypeError, ValueError):
            continue
        where = "phone" if (owner or "user") == "daemon" else "chat"
        for text, at_raw in _user_texts(msgs):
            if text and len(text) <= MAX_CHARS and is_correction(text):
                at = _parse_at(at_raw)
                out.append({
                    "text": text,
                    "at": at.isoformat() if at is not None else "",
                    "where": where,
                    "link": f"/chat?thread={thread_id}",
                })
    return out


def scan_sources(engine, sources: list[str]) -> dict[str, Any]:
    """Read the chosen sources' typed messages, cluster repeated corrections,
    mint proposed rows (same never-ask rule, same open cap). Blocking — call
    it off the loop. The result never carries message text beyond the
    minted proposal sentences, and nothing here logs message text."""
    by_source: dict[str, int] = {}
    pool: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for src in sources:
        n, msgs = _typed_messages(src)
        by_source[src] = n
        for m in msgs:
            key = (src, m["text"], m["at"])
            if key in seen:  # a resumed session repeats its history in a new file
                continue
            seen.add(key)
            if is_correction(m["text"]):
                pool.append({"text": m["text"], "at": m["at"], "where": src})
    now = utcnow()
    try:
        chat = _recent_chat_corrections(engine, now)
    except Exception:  # noqa: BLE001 — the chat side is a bonus, never a failure
        chat = []

    clusters: list[dict[str, Any]] = []
    for item in [*pool, *chat]:
        sig = signature(item["text"])
        if not sig:
            continue
        for c in clusters:
            if similar(sig, c["sig"]):
                c["items"].append(item)
                break
        else:
            clusters.append({"sig": sig, "items": [item]})

    minted: list[LessonRecord] = []
    skipped_full = False
    ranked = sorted(clusters, key=lambda c: len(c["items"]), reverse=True)
    for c in ranked:
        items = c["items"]
        if len(items) < 2 or not any(i["where"] in SCAN_SOURCES for i in items):
            continue
        items = sorted(items, key=lambda i: i.get("at") or "", reverse=True)
        evidence = [
            e for e in (
                _evidence(i["text"], at=i.get("at") or "", where=i["where"], link=i.get("link") or "")
                for i in items
            ) if e is not None
        ][:MAX_EVIDENCE]
        with _MINT_LOCK, session_scope(engine) as db:
            full = _open_count(db) >= MAX_OPEN
        if full:
            skipped_full = True
            break
        row = mint(engine, quotes=[e["quote"] for e in evidence], sig=c["sig"], evidence=evidence)
        if row is not None:
            minted.append(row)
    return {
        "sessions_read": int(sum(by_source.values())),
        "by_source": by_source,
        "suggestions": len(minted),
        "suggested": [pref_view(r) for r in minted],
        "skipped_full": skipped_full,
        "_minted": minted,
    }


async def publish_scan(platform: Any, minted: list[Any]) -> None:
    """``preference.suggested`` (via "scan") for each row a press minted."""
    for row in minted:
        await _publish_suggested(platform, row.id, row.text, len(evidence_of(row)), "scan")


__all__ = [
    "MAX_OPEN",
    "PreferenceConflict",
    "PreferenceInvalid",
    "SCAN_SOURCES",
    "ask_again",
    "chat_occurrences",
    "clean_text",
    "decline",
    "edit",
    "evidence_of",
    "forget",
    "keep",
    "list_preferences",
    "mint",
    "pref_view",
    "publish_declined",
    "publish_kept",
    "publish_scan",
    "scan_sources",
    "sources_available",
    "suggest_for_turn",
    "suggest_from_turn",
]
