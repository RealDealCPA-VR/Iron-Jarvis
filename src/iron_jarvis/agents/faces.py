"""Per-agent FACE overrides (v1.180.0).

Every agent already wears a DETERMINISTIC face: ``AgentFace.tsx`` seeds a
shape, a body colour and an eye style from the agent's NAME (FNV-1a), so the
same agent looks the same on the roster, the kanban board, the round table and
the @-mention popover. That is a good default and a bad ceiling — the user
asked to be able to choose the shape, the eyes and the colour themselves.

This module is the STORE behind that choice, and it deliberately mirrors the
portrait precedent (``routes/agents.py``, v1.171.0):

* One small JSON per agent under ``<home>/faces/<slug>.json``. The file's
  existence IS the record — no schema change, no migration, and deleting the
  file is a complete reset.
* THE SLUG IS THE CALLER'S. ``face_path`` takes an already-computed slug
  rather than computing its own, because an agent's portrait and its face key
  must never disagree: the route passes ``_avatar_slug(name)``, the exact
  function that names ``avatars/<slug>.png``. Two slug functions would drift
  the first time one of them learned about a new hostile name shape (Windows
  device names, case-folding on NTFS, lossy sanitization + digest — all of
  that lives in ``_avatar_slug`` and none of it is duplicated here).
* An OVERRIDE IS PARTIAL. Each of ``shape`` / ``color`` / ``eyes`` is
  independent: a stored ``shape`` overrides the seeded shape and leaves the
  colour and the eyes deriving from the name. An absent field means "derive",
  never "empty".

Reads are LENIENT, writes are STRICT. ``normalize_override`` (the write path)
rejects a value outside the allowed set with :class:`FaceValueError`, which the
route turns into an honest 400 — a silently-defaulted value would tell the user
they picked something they did not. ``read_face`` (the render path) drops a
value it does not recognise instead: a file written by a newer build, or edited
by hand, degrades that one field back to the derived face rather than erroring
or rendering a colour the palette no longer contains.

Every function here is BLOCKING file IO and pure otherwise — the async routes
call them through ``asyncio.to_thread`` (v1.153.1: nothing blocking on the
event loop), and the sync list/roster handlers call them from FastAPI's
threadpool.

THE ALLOWED SETS ARE A CONTRACT WITH ``dashboard/components/agents/AgentFace.tsx``.
``SHAPES`` / ``COLORS`` / ``EYE_STYLES`` there and ``FACE_SHAPES`` /
``FACE_COLORS`` / ``FACE_EYES`` here must name the same values in the same
order: the daemon validates against this list and the component draws from
that one, so a value accepted here but unknown there renders as a derived face
while the picker claims it is set.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

#: Body shapes — must match ``SHAPES`` in AgentFace.tsx (same order).
FACE_SHAPES: tuple[str, ...] = (
    "circle",
    "squircle",
    "pill",
    "triangle",
    "hexagon",
    "cloud",
    "drop",
)

#: Ten flat body colours — must match ``COLORS`` in AgentFace.tsx (same order).
FACE_COLORS: tuple[str, ...] = (
    "#e8e4da",  # parchment
    "#8a6f52",  # brown
    "#c65949",  # red
    "#d98a3d",  # orange
    "#3f9e8b",  # teal
    "#3fb1c9",  # cyan
    "#4f6fd8",  # royal
    "#8b64c9",  # violet
    "#c65a9e",  # magenta
    "#a8b0b8",  # silver
)

#: Named eye styles — must match ``EYE_STYLES`` in AgentFace.tsx (same order).
#: A REAL NAMED SET, never a free-form string: the component draws geometry per
#: name, so an unknown value has nothing to draw.
FACE_EYES: tuple[str, ...] = (
    "round",
    "oval",
    "wide",
    "sleepy",
    "square",
    "visor",
)

#: The three independent fields of an override.
FACE_FIELDS: tuple[str, ...] = ("shape", "color", "eyes")

_ALLOWED: dict[str, tuple[str, ...]] = {
    "shape": FACE_SHAPES,
    "color": FACE_COLORS,
    "eyes": FACE_EYES,
}

#: Upper bound on a ``list_faces`` scan — a directory that somehow grew huge
#: must not turn the agents list into an unbounded walk (the v1.153.1 lesson:
#: every filesystem loop is bounded and truncation is reported, never silent).
_MAX_LISTED = 500


class FaceValueError(ValueError):
    """A write carried a value outside the allowed set for its field."""

    def __init__(self, field: str, value: Any) -> None:
        self.field = field
        self.value = value
        allowed = ", ".join(_ALLOWED.get(field, ()))
        super().__init__(
            f"{field} must be one of: {allowed} — got {value!r}"
        )


def face_options() -> dict[str, list[str]]:
    """What a picker may offer. Served by the route so the UI can render the
    daemon's OWN allowed sets rather than a hardcoded second copy."""
    return {
        "shapes": list(FACE_SHAPES),
        "colors": list(FACE_COLORS),
        "eyes": list(FACE_EYES),
    }


def normalize_override(body: Mapping[str, Any] | None) -> dict[str, str]:
    """The STRICT write path: a partial, validated override.

    * an absent field, ``None``, or ``""`` means UNSET — "derive this one from
      the name". (An empty string is "I did not choose one", the same reading
      the remote-agent token box uses; it is never "make it blank".)
    * anything else must be a member of that field's allowed set, matched
      case-insensitively and stored lowercase, or :class:`FaceValueError` is
      raised so the caller can answer with an honest 400.
    """
    out: dict[str, str] = {}
    data = body or {}
    for field in FACE_FIELDS:
        if field not in data:
            continue
        raw = data[field]
        if raw is None:
            continue
        if not isinstance(raw, str):
            raise FaceValueError(field, raw)
        value = raw.strip().lower()
        if not value:
            continue
        if value not in _ALLOWED[field]:
            raise FaceValueError(field, raw)
        out[field] = value
    return out


def coerce_override(data: Any) -> dict[str, str]:
    """The LENIENT read path: keep the fields this build understands, drop the
    rest. A value a newer build wrote (or a hand edit) degrades that ONE field
    back to the derived face instead of failing the whole record."""
    if not isinstance(data, Mapping):
        return {}
    out: dict[str, str] = {}
    for field in FACE_FIELDS:
        raw = data.get(field)
        if not isinstance(raw, str):
            continue
        value = raw.strip().lower()
        if value in _ALLOWED[field]:
            out[field] = value
    return out


def faces_dir(home: Path | str) -> Path:
    return Path(home) / "faces"


def face_path(home: Path | str, slug: str) -> Path:
    """``<home>/faces/<slug>.json``.

    ``slug`` MUST be the same value that names the agent's portrait
    (``avatars/<slug>.png``) — see the module docstring. This function does not
    sanitize; the one sanitizer lives with the portraits.
    """
    return faces_dir(home) / f"{slug}.json"


def read_face(home: Path | str, slug: str) -> dict[str, str]:
    """The stored override, or ``{}`` when there is none.

    Never raises: a missing file, an unreadable one, invalid JSON and a
    non-object payload all mean "no override" — the face derives from the name,
    which is exactly what every surface did before this feature existed.
    """
    try:
        raw = face_path(home, slug).read_text("utf-8")
    except (OSError, ValueError):
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        return {}
    return coerce_override(data)


def write_face(
    home: Path | str, slug: str, override: Mapping[str, str], *, name: str = ""
) -> dict[str, str]:
    """Persist a validated override atomically; returns what was stored.

    ``name`` is the agent's ORIGINAL name, kept in the record so ``list_faces``
    can key by name — a slug is lossy by design (it lowercases and digests) and
    cannot be turned back into the name the dashboard holds.

    Atomic publish (the portrait/backup convention): unique temp file beside
    the target, then ``os.replace``. A concurrent read must never see a
    half-written record, and a crash mid-write must not leave a corrupt file
    that then reads as "no override" forever.
    """
    clean = {k: v for k, v in dict(override).items() if k in FACE_FIELDS}
    payload: dict[str, Any] = {"name": str(name or ""), **clean}
    p = face_path(home, slug)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.parent / f"{p.name}.{uuid.uuid4().hex}.tmp"
    try:
        tmp.write_text(json.dumps(payload, ensure_ascii=False), "utf-8")
        os.replace(tmp, p)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    return dict(clean)


def delete_face(home: Path | str, slug: str) -> bool:
    """Remove the override. ``True`` when one was actually removed.

    Deliberately IDEMPOTENT: "reset to the derived face" is the state the user
    asked for, and an already-derived face is not an error to report.
    """
    try:
        face_path(home, slug).unlink()
        return True
    except (OSError, ValueError):
        return False


def list_faces(home: Path | str) -> dict[str, dict[str, str]]:
    """Every stored override, keyed by the agent NAME it was written for.

    Records written before a name was recorded (or with an empty one) are
    skipped rather than guessed at — the slug cannot be reversed, and inventing
    a name here would attach one agent's face to another.
    """
    out: dict[str, dict[str, str]] = {}
    try:
        entries = sorted(faces_dir(home).glob("*.json"))
    except OSError:
        return out
    for p in entries[:_MAX_LISTED]:
        try:
            data = json.loads(p.read_text("utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, Mapping):
            continue
        name = str(data.get("name") or "").strip()
        if not name:
            continue
        fields = coerce_override(data)
        if fields:
            out[name] = fields
    return out
