"""What Jarvis knows about the user, in one dict (v1.279.0).

The Memory page's "Working" tab was a search box over a store that is empty on
most installs, so the page never said the one thing a person opening it wants
to know: *what do you actually remember about me?* This answers that from the
stores that hold user-level facts — the profile, the preference/feedback/
distilled lessons, the note bases, the working-memory graph, the history index
— and says plainly when the answer is "nothing yet". Task reflections are
counted but not shown as knowledge (they are not; see ``learning.engine``).

Read-only, never raises: every part is wrapped, a broken store contributes an
empty section rather than a 500 on the page that reports on it. Blocking
(a folder glob for note counts) — call it off the loop.
"""

from __future__ import annotations

from typing import Any

from .index_block import _connectors, _kind_of, _local_notes

#: How many preferences the card lists.
MAX_PREFERENCES = 12


def _profile(platform: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"filled": False, "enabled": True, "about_line": "", "tone": "", "writing_style": ""}
    try:
        from ..profile.store import ProfileStore

        row = ProfileStore(platform.engine).get()
        about = str(getattr(row, "about", "") or "")
        line = ""
        for raw in about.splitlines():
            stripped = raw.strip()
            if not stripped or stripped.startswith("#"):
                continue  # blank lines and markdown headings are not the fact
            text = stripped.lstrip("-* ").strip()
            if text:
                line = text[:160]
                break
        out.update(
            {
                "enabled": bool(getattr(row, "enabled", True)),
                "about_line": line,
                "tone": str(getattr(row, "tone", "") or ""),
                "writing_style": str(getattr(row, "writing_style", "") or ""),
            }
        )
        out["filled"] = bool(about.strip() or out["tone"] or out["writing_style"])
    except Exception:  # noqa: BLE001 — a profile that cannot be read is "not filled"
        pass
    return out


def _preferences(platform: Any) -> tuple[list[dict[str, Any]], dict[str, int]]:
    prefs: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    try:
        learning = platform.learning
        from ..learning.engine import _PROMPT_EXCLUDED_SOURCES

        for row in learning.lessons(
            scope="user", limit=MAX_PREFERENCES, exclude_sources=_PROMPT_EXCLUDED_SOURCES
        ):
            prefs.append(
                {
                    "id": str(getattr(row, "id", "") or ""),
                    "text": str(getattr(row, "text", "") or ""),
                    "source": str(getattr(row, "source", "") or ""),
                    "weight": int(getattr(row, "weight", 0) or 0),
                    "created_at": str(getattr(row, "created_at", "") or ""),
                }
            )
        counter = getattr(learning, "counts_by_source", None)
        if callable(counter):
            counts = dict(counter(scope="user") or {})
    except Exception:  # noqa: BLE001
        pass
    return prefs, counts


def _bases(platform: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for conn in _connectors(platform):
        try:
            files = _local_notes(conn)
            out.append(
                {
                    "name": str(getattr(conn, "name", "") or ""),
                    "kind": _kind_of(conn),
                    "notes": (len(files) if files is not None else None),
                }
            )
        except Exception:  # noqa: BLE001 — one bad connector is skipped
            continue
    return out


def _working(platform: Any) -> dict[str, int]:
    out: dict[str, int] = {}
    try:
        mem = platform.memory
        for layer in tuple(getattr(mem, "LAYERS", ("session", "project", "user", "org"))):
            try:
                out[str(layer)] = len(mem.list(layer))
            except Exception:  # noqa: BLE001
                continue
    except Exception:  # noqa: BLE001
        pass
    return out


def _history(platform: Any) -> dict[str, Any]:
    try:
        index = getattr(platform, "search_index", None)
        stats = (index.stats() if index is not None else {}) or {}
        return {"docs": int(stats.get("docs") or 0), "available": bool(stats.get("available"))}
    except Exception:  # noqa: BLE001
        return {"docs": 0, "available": False}


def memory_overview(platform: Any) -> dict[str, Any]:
    """Everything the "what Jarvis knows about you" card shows. Never raises."""
    profile = _profile(platform)
    prefs, counts = _preferences(platform)
    bases = _bases(platform)
    working = _working(platform)
    history = _history(platform)
    notes_known = sum(int(b["notes"] or 0) for b in bases if b.get("notes") is not None)
    empty = not (
        profile["filled"] or prefs or notes_known > 0 or any(v > 0 for v in working.values())
    )
    return {
        "profile": profile,
        "preferences": prefs,
        "lessons": {
            "total": int(sum(counts.values())),
            "reflections": int(counts.get("reflection", 0)),
            "by_source": counts,
        },
        "bases": bases,
        "working": working,
        "history": history,
        "empty": empty,
    }


__all__ = ["memory_overview", "MAX_PREFERENCES"]
