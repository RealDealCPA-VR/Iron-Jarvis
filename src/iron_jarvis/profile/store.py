"""Read/write the single :class:`UserProfileRecord` (v1.144.0).

Two behaviours worth stating up front, because the seams depend on them:

* **A read never writes.** :meth:`ProfileStore.get` returns an in-memory
  default record when the row does not exist yet. Every chat turn, every agent
  run, and every phone message calls this — a get-or-create would mint a row
  (and a write lock) on a machine where the user never opened /you.
* **A save is a partial update.** The /you page PUTs only the fields it edited;
  anything absent from the payload keeps its stored value. That keeps a future
  field (or a second editor, like v1.145.0's voice card) from being blanked by
  an older client that doesn't know about it.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Engine

from ..core.db import session_scope
from ..core.ids import utcnow
from .models import PROFILE_ID, UserProfileRecord
from .presets import ACCESSIBILITY

#: field -> max characters. Everything the user types is capped at the STORE,
#: not at the renderer, so the cap is visible in the API response and cannot be
#: bypassed by a direct PUT. The prompt-side budget is separate (see block.py).
_LIMITS: dict[str, int] = {
    "about": 2000,
    "formatting_rules": 1200,
    "voice_card": 1500,
    "voice_source": 200,
    "tone": 200,
    "writing_style": 200,
    "formatting": 120,
    "reading_level": 120,
    "response_length": 120,
    "accessibility": 120,
    "language": 16,
}

_BOOLS = ("enabled", "enforce_language")

#: Everything a client may write.
EDITABLE = tuple(_LIMITS) + _BOOLS


class ProfileStore:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def get(self) -> UserProfileRecord:
        """The stored profile, or a default (UNSAVED) record — never writes."""
        with session_scope(self.engine) as db:
            row = db.get(UserProfileRecord, PROFILE_ID)
            if row is not None:
                # Detach a plain copy: callers read this outside the session.
                return UserProfileRecord(**row.model_dump())
        return UserProfileRecord(id=PROFILE_ID)

    def save(self, values: dict[str, Any]) -> UserProfileRecord:
        """Partial-update the profile. Unknown keys are ignored; strings are
        stripped + capped; booleans are coerced. Returns the saved record."""
        clean: dict[str, Any] = {}
        for key, raw in (values or {}).items():
            if key in _BOOLS:
                clean[key] = bool(raw)
            elif key in _LIMITS:
                clean[key] = str(raw or "").strip()[: _LIMITS[key]]
        with session_scope(self.engine) as db:
            row = db.get(UserProfileRecord, PROFILE_ID)
            if row is None:
                row = UserProfileRecord(id=PROFILE_ID)
            for key, val in clean.items():
                setattr(row, key, val)
            row.updated_at = utcnow()
            db.add(row)
            db.commit()
            db.refresh(row)
            return UserProfileRecord(**row.model_dump())

    def apply_accessibility(self, name: str) -> UserProfileRecord:
        """Turn on an accessibility mode AND seed its editable companion fields.

        Only fields the user has left EMPTY are seeded: a mode must never
        silently overwrite a preference they set on purpose. Everything it fills
        is an ordinary editable field afterwards — that is the whole point (the
        brief asked for these preferences to be customizable and saved).
        """
        name = (name or "").strip()
        spec = ACCESSIBILITY.get(name)
        current = self.get()
        values: dict[str, Any] = {"accessibility": name if spec else ""}
        if spec:
            defaults = spec.get("defaults") or {}
            if isinstance(defaults, dict):
                for key, val in defaults.items():
                    if key in _LIMITS and not str(getattr(current, key, "") or "").strip():
                        values[key] = val
        return self.save(values)


def as_dict(record: UserProfileRecord) -> dict[str, Any]:
    """The wire shape the /profile routes return (and /you consumes)."""
    return {
        "enabled": bool(record.enabled),
        "about": record.about,
        "tone": record.tone,
        "writing_style": record.writing_style,
        "formatting": record.formatting,
        "formatting_rules": record.formatting_rules,
        "reading_level": record.reading_level,
        "response_length": record.response_length,
        "accessibility": record.accessibility,
        "language": record.language,
        "enforce_language": bool(record.enforce_language),
        "voice_card": record.voice_card,
        "voice_source": record.voice_source,
        "updated_at": record.updated_at,
    }
