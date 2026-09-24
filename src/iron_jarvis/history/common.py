"""Small shared helpers for the history readers (no I/O here)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

#: Every event's ``text`` is clipped to this (the shared EVENT contract).
TEXT_CAP = 4000
#: A session summary's ``title`` is the first prompt clipped to this.
TITLE_CAP = 120


def clip(text: Any, cap: int = TEXT_CAP) -> str:
    """``text`` as a string no longer than ``cap`` characters."""
    if not isinstance(text, str):
        return ""
    return text if len(text) <= cap else text[:cap]


def title_of(text: str) -> str:
    """One line, whitespace collapsed, clipped to ``TITLE_CAP``."""
    return clip(" ".join(text.split()), TITLE_CAP)


def s(value: Any) -> str:
    """``value`` when it is a string, else ``""`` (never raises)."""
    return value if isinstance(value, str) else ""


def first_str(d: Any, *keys: str) -> str:
    """The first non-empty string value among ``keys`` of dict ``d``."""
    if not isinstance(d, dict):
        return ""
    for key in keys:
        value = d.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def iso(value: Any) -> str | None:
    """Normalise a timestamp (ISO string, epoch seconds or ms) to UTC ISO-8601.

    One fixed shape (millisecond precision, ``+00:00``) so summaries sort as
    plain strings. Anything unparseable is ``None``, never an exception.
    """
    try:
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            secs = value / 1000.0 if value > 1e11 else float(value)
            dt = datetime.fromtimestamp(secs, tz=UTC)
        elif isinstance(value, str) and value.strip():
            text = value.strip()
            if text.endswith(("Z", "z")):
                text = text[:-1] + "+00:00"
            dt = datetime.fromisoformat(text)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
        else:
            return None
        return dt.astimezone(UTC).isoformat(timespec="milliseconds")
    except (ValueError, OverflowError, OSError):
        return None


def parse_line(raw: bytes) -> dict | None:
    """One JSONL line as a dict, or ``None`` (malformed / not an object)."""
    raw = raw.strip()
    if not raw:
        return None
    try:
        value = json.loads(raw.decode("utf-8", errors="replace"))
    except (ValueError, RecursionError):
        return None
    return value if isinstance(value, dict) else None


def event(action: str, session_id: str, source: str, **fields: Any) -> dict:
    """A normalized EVENT dict; empty/None optional fields are left out.

    ``ok`` is kept even when ``None`` so a caller can tell "no result seen"
    from a key that was never set.
    """
    ev: dict[str, Any] = {"action": action, "session_id": session_id, "source": source}
    for key, value in fields.items():
        if key == "ok":
            ev["ok"] = value
        elif value not in (None, ""):
            ev[key] = clip(value) if key == "text" else value
    ev.setdefault("ok", None)
    return ev


def result_text(content: Any) -> str:
    """Tool-result content (string, or a list of text blocks) as plain text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str) and block.strip():
                parts.append(block)
            elif isinstance(block, dict):
                text = first_str(block, "text", "content", "output")
                if text:
                    parts.append(text)
        return "\n".join(parts).strip()
    if isinstance(content, dict):
        return first_str(content, "text", "content", "output")
    return ""
