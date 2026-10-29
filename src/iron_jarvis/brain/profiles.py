"""Brain Profile records, validation, and the ONE resolve rule (v1.406.0).

A profile names a memory base (by its NAME — the identity every other surface
uses) as the user's primary durable brain, and says how Iron Jarvis treats it.
``resolve(platform, project_id)`` is the only place that decides which profile
applies to a turn or a run: the project's own choice, else the app's.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from sqlmodel import select

from ..core.db import session_scope
from ..core.ids import utcnow
from .models import BrainProfileRecord

log = logging.getLogger(__name__)

SAVE_TO = ("primary", "ask", "never")
ON_CONFLICT = ("ask", "newest", "brain")
#: A project that wants NO primary brain even though the app has one.
PROJECT_OFF = "off"
MAX_NAME = 60
MAX_GUIDE_REF = 500
MAX_SKILLS = 12

#: Plain names for the built-in bases (a custom base is named by the user).
BUILTIN_LABELS = {
    "brain": "Iron Jarvis memory",
    "obsidian": "Obsidian vault",
    "notion": "Notion",
}

#: The fields a profile has, in the order the API returns them.
FIELDS = (
    "name", "primary", "search_first", "save_to", "prefer_update",
    "on_conflict", "guide_ref", "skills", "repo_routing",
)


class ProfileError(ValueError):
    """A profile the user asked for that cannot be stored — one sentence."""


@dataclass(frozen=True)
class ActiveBrain:
    """The profile that applies to one turn or run, and where it came from."""

    profile: dict[str, Any]
    source: str  # "project" | "app"

    @property
    def base(self) -> str:
        return str(self.profile.get("primary") or "brain")

    @property
    def name(self) -> str:
        return str(self.profile.get("name") or "")


def base_label(platform: Any, base: str) -> str:
    """How a memory base is named to the user."""
    if base in BUILTIN_LABELS:
        return BUILTIN_LABELS[base]
    return base


def base_rows(platform: Any) -> list[dict[str, Any]]:
    """Every registered memory base, for the profile form. ``available`` comes
    from the CACHED health only (never a probe here): True/False, else None."""
    ltm = getattr(platform, "ltm", None)
    rows: list[dict[str, Any]] = []
    if ltm is None:
        return rows
    kinds: dict[str, str] = {}
    try:
        from ..ltm.sources import CustomSourceStore

        for rec in CustomSourceStore(platform.engine).list():
            kinds[rec.name] = rec.kind
    except Exception:  # noqa: BLE001 — a missing table reads as "no custom bases"
        pass
    for name in ltm.sources():
        conn = ltm.get(name)
        available: "bool | None" = None
        try:
            cached = getattr(conn, "cached_health", None)
            health = cached() if callable(cached) else None
            if health is None and hasattr(conn, "missing") and hasattr(conn, "dir"):
                health = conn.health()
            if isinstance(health, dict) and "available" in health:
                available = bool(health["available"])
        except Exception:  # noqa: BLE001 — unknown, never a crash
            available = None
        rows.append({
            "name": name,
            "label": base_label(platform, name),
            "kind": kinds.get(name, name if name in BUILTIN_LABELS else "markdown"),
            "available": available,
        })
    return rows


def public(rec: BrainProfileRecord) -> dict[str, Any]:
    try:
        skills = [str(s) for s in json.loads(rec.skills_json or "[]") if str(s).strip()]
    except (TypeError, ValueError):
        skills = []
    return {
        "id": rec.id,
        "name": rec.name,
        "primary": rec.primary or "brain",
        "search_first": bool(rec.search_first),
        "save_to": rec.save_to if rec.save_to in SAVE_TO else "primary",
        "prefer_update": bool(rec.prefer_update),
        "on_conflict": rec.on_conflict if rec.on_conflict in ON_CONFLICT else "ask",
        "guide_ref": rec.guide_ref or "",
        "skills": skills,
        "repo_routing": bool(rec.repo_routing),
        "created_at": rec.created_at.isoformat() + "Z" if rec.created_at else "",
        "updated_at": rec.updated_at.isoformat() + "Z" if rec.updated_at else "",
    }


def _clean(platform: Any, values: dict[str, Any], *, partial: bool) -> dict[str, Any]:
    """Validate the fields the caller sent; one plain sentence on a refusal."""
    out: dict[str, Any] = {}
    unknown = sorted(set(values) - set(FIELDS))
    if unknown:
        raise ProfileError(f"A profile has no setting called {unknown[0]!r}.")
    if "name" in values or not partial:
        name = str(values.get("name") or "").strip()
        if not name:
            raise ProfileError("Give the profile a name.")
        if len(name) > MAX_NAME:
            raise ProfileError(f"Keep the name under {MAX_NAME} characters.")
        out["name"] = name
    if "primary" in values or not partial:
        base = str(values.get("primary") or "brain").strip()
        ltm = getattr(platform, "ltm", None)
        known = list(ltm.sources()) if ltm is not None else ["brain"]
        if base not in known:
            raise ProfileError(
                f"There is no memory base called {base!r}. Add it under Memory → "
                "Long-term first, then choose it here."
            )
        out["primary"] = base
    for key, allowed in (("save_to", SAVE_TO), ("on_conflict", ON_CONFLICT)):
        if key in values:
            v = str(values.get(key) or "").strip()
            if v not in allowed:
                raise ProfileError(f"{key.replace('_', ' ')} must be one of: {', '.join(allowed)}.")
            out[key] = v
    for key in ("search_first", "prefer_update", "repo_routing"):
        if key in values:
            if not isinstance(values[key], bool):
                raise ProfileError(f"{key.replace('_', ' ')} must be on or off.")
            out[key] = values[key]
    if "guide_ref" in values:
        ref = str(values.get("guide_ref") or "").strip()
        if len(ref) > MAX_GUIDE_REF:
            raise ProfileError("The operating guide should be a note's title or link, not its text.")
        out["guide_ref"] = ref
    if "skills" in values:
        raw = values.get("skills") or []
        if not isinstance(raw, list) or not all(isinstance(s, str) for s in raw):
            raise ProfileError("Skills must be a list of skill names.")
        names: list[str] = []
        for s in raw:
            s = s.strip()
            if s and s not in names:
                names.append(s)
        if len(names) > MAX_SKILLS:
            raise ProfileError(f"Choose at most {MAX_SKILLS} skills.")
        registry = getattr(platform, "skills", None)
        for s in names:
            if registry is not None and registry.get(s) is None:
                raise ProfileError(f"There is no skill called {s!r}.")
        out["skills_json"] = json.dumps(names)
    return out


class BrainProfileStore:
    def __init__(self, platform: Any) -> None:
        self.platform = platform
        self.engine = platform.engine

    def list(self) -> list[dict[str, Any]]:
        with session_scope(self.engine) as db:
            rows = db.exec(select(BrainProfileRecord).order_by(BrainProfileRecord.created_at)).all()
            return [public(r) for r in rows]

    def get(self, profile_id: str) -> "dict[str, Any] | None":
        if not profile_id:
            return None
        with session_scope(self.engine) as db:
            rec = db.get(BrainProfileRecord, profile_id)
            return public(rec) if rec is not None else None

    def create(self, values: dict[str, Any]) -> dict[str, Any]:
        clean = _clean(self.platform, values, partial=False)
        rec = BrainProfileRecord(**clean)
        with session_scope(self.engine) as db:
            db.add(rec)
            db.commit()
            db.refresh(rec)
            return public(rec)

    def update(self, profile_id: str, values: dict[str, Any]) -> "dict[str, Any] | None":
        clean = _clean(self.platform, values, partial=True)
        with session_scope(self.engine) as db:
            rec = db.get(BrainProfileRecord, profile_id)
            if rec is None:
                return None
            for k, v in clean.items():
                setattr(rec, k, v)
            rec.updated_at = utcnow()
            db.add(rec)
            db.commit()
            db.refresh(rec)
            return public(rec)

    def delete(self, profile_id: str) -> "dict[str, Any] | None":
        """Remove a profile and every choice that named it (the app's and each
        project's) — a selection must never point at nothing."""
        from ..core.models import Project

        with session_scope(self.engine) as db:
            rec = db.get(BrainProfileRecord, profile_id)
            if rec is None:
                return None
            db.delete(rec)
            projects = db.exec(select(Project).where(Project.brain_profile == profile_id)).all()
            for p in projects:
                p.brain_profile = ""
                db.add(p)
            db.commit()
        cleared_app = False
        config = getattr(self.platform, "config", None)
        if config is not None and getattr(config, "brain_profile", "") == profile_id:
            writer = getattr(self.platform, "config_writer", None)
            try:
                if writer is not None:
                    writer.apply({"brain_profile": ""}, actor="brain_profiles")
                else:
                    config.brain_profile = ""
                cleared_app = True
            except Exception:  # noqa: BLE001 — the profile is gone; resolve() ignores a stale id
                log.warning("could not clear the app's brain profile", exc_info=True)
                config.brain_profile = ""
                cleared_app = True
        return {"deleted": True, "cleared": {"app": cleared_app, "projects": len(projects)}}


def project_choice(platform: Any, project_id: "str | None") -> str:
    if not project_id:
        return ""
    try:
        from ..core.models import Project

        with session_scope(platform.engine) as db:
            proj = db.get(Project, project_id)
            return str(getattr(proj, "brain_profile", "") or "") if proj is not None else ""
    except Exception:  # noqa: BLE001 — an unreadable project follows the app
        return ""


def resolve(platform: Any, project_id: "str | None") -> "ActiveBrain | None":
    """THE rule: the project's own choice ("off" = none), else the app's
    (``Config.brain_profile``). A choice naming a deleted profile is none.
    Blocking (SQLite) — call off the event loop. Never raises."""
    try:
        choice = project_choice(platform, project_id)
        source = "project"
        if choice == PROJECT_OFF:
            return None
        if not choice:
            choice = str(getattr(getattr(platform, "config", None), "brain_profile", "") or "")
            source = "app"
        if not choice:
            return None
        profile = BrainProfileStore(platform).get(choice)
        if profile is None:
            return None
        return ActiveBrain(profile=profile, source=source)
    except Exception:  # noqa: BLE001 — no brain beats a crashed turn
        log.warning("brain profile could not be resolved", exc_info=True)
        return None


def effective_view(platform: Any, project_id: "str | None") -> dict[str, Any]:
    if project_choice(platform, project_id) == PROJECT_OFF:
        return {"profile": None, "source": "off"}
    active = resolve(platform, project_id)
    if active is None:
        return {"profile": None, "source": "none"}
    return {"profile": active.profile, "source": active.source}
