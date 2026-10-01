"""The Skill Curator (v1.297.0) — agent-made skills get a lifecycle.

``skill_create`` (v1.90.0) and the learning engine's approved proposals write
skills into ``<home>/skills`` and nothing ever looked at them again: a skill a
run saved once in March sits beside the user's own forever, searchable and
injectable. The curator gives those files provenance (``created_by`` /
``created_session`` in the frontmatter, see ``loader.py``) and a deterministic
sweep:

* :meth:`candidates` — the agent/proposal skills in the user root that are
  old enough (``curator_min_age_days``, 14) and either never used NOR injected,
  or idle past ``curator_stale_after_days`` (30). **A user-authored skill is
  never a candidate**: the user wrote it, and a tool does not decide that the
  user's own work is clutter. **A pinned skill is never a candidate** either.
* :meth:`sweep` — before the first move, a tarball of the whole skills folder
  lands in ``<home>/backups/skills-<stamp>.tar.gz``; then each candidate's
  folder MOVES to ``<home>/skills/.archive/<name>/`` (a name already there
  gets a ``-<stamp>`` suffix) with ``archived_at`` stamped into its
  frontmatter, and the registry is rescanned. Nothing is ever deleted — a
  destructive bulk action moves (v1.256.0), and :meth:`restore` moves it back.
* :meth:`pin` / :meth:`unpin`, :meth:`archive` (by hand — ANY user-root skill,
  the user's own included: only the automatic sweep spares them),
  :meth:`archived`, :meth:`status`, and :meth:`run_forever` (the daemon loop:
  first run an hour after boot, then every ``curator_interval_hours``, every
  disk op through ``asyncio.to_thread``, every cycle reported through the
  ``tick_cb(ok, exc)`` seam — armed is not healthy, v1.229.0).

"Used" and "injected" are two different counters on ``SkillStatRecord``:
``use_count`` means the agent chose to ``skill_load`` it (the learning
engine's own telemetry, untouched here) and ``inject_count`` means
``SkillRegistry.inject`` put it into a prompt (the seam this wave added). A
skill with either is "active" and judged by idleness; a skill with neither
is judged by age alone.

``created_at`` is the SKILL.md file's mtime. The curator's own frontmatter
rewrites (pin, unpin, the archive stamp, restore) put the original mtime back,
so pinning a skill does not make it young again.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import tarfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from sqlmodel import select

from ..core.db import session_scope
from ..core.ids import utcnow
from ..core.logging import get_logger
from .framework import ARCHIVE_DIRNAME, SkillRegistry
from .learning_models import SkillStatRecord
from .loader import SKILL_FILE, Skill, load_skill, read_skill_md, update_frontmatter

log = get_logger("skill_curator")

#: Settings, read live through ``getattr(config, name, default)`` so a config
#: that predates this wave (or a bare namespace in a test) works unchanged.
DEFAULT_SETTINGS: dict[str, Any] = {
    "curator_enabled": True,
    "curator_min_age_days": 14,
    "curator_stale_after_days": 30,
    "curator_interval_hours": 24,
}
#: Provenance values the automatic sweep may touch. "user" is NOT here, on
#: purpose: the user wrote those, and the sweep never decides for them.
SWEEPABLE_CREATED_BY = ("agent", "proposal")
#: The sweep's run record, beside the skills (not in the DB: it describes
#: files, and it should travel with them in a backup).
STATE_FILE = ".curator.json"
#: The daemon loop's first run waits this long after boot by default — a
#: sweep is never the first thing a fresh daemon does.
FIRST_RUN_DELAY_S = 3600.0

TickCb = Callable[[bool, "BaseException | None"], None]


def _as_utc(value: datetime | None) -> datetime | None:
    """SQLite drops tzinfo on the way out; read a stored stamp back as UTC."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return _as_utc(value).isoformat() if value is not None else None


def _stamp(now: datetime) -> str:
    return now.strftime("%Y%m%d-%H%M%S")


def _days(delta_s: float) -> float:
    return round(max(0.0, delta_s) / 86400.0, 2)


def skill_view(skill: Skill) -> dict[str, Any]:
    """The route/dashboard shape of one skill, provenance included."""
    return {
        "name": skill.name,
        "description": skill.description,
        "source": skill.source,
        "dir": str(skill.dir),
        "created_by": skill.created_by,
        "created_session": skill.created_session,
        "pinned": bool(skill.pinned),
        "archived_at": skill.archived_at or None,
    }


class SkillCurator:
    """Provenance + lifecycle for the skills in ``<home>/skills``."""

    def __init__(
        self,
        home: Path,
        framework: SkillRegistry | None,
        learning: Any,
        *,
        config: Any = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.home = Path(home)
        self.framework = framework
        #: The learning engine (``.engine`` is the SQLModel engine the stat
        #: rows live in). None = no stats, every skill reads as never active.
        self.learning = learning
        self._config = config
        self._now = now or utcnow
        # One sweep/archive/restore at a time: the daemon loop and a manual
        # press must not move the same folder twice.
        self._lock = threading.Lock()

    # -- paths ---------------------------------------------------------------

    @property
    def skills_root(self) -> Path:
        return self.home / "skills"

    @property
    def archive_root(self) -> Path:
        return self.skills_root / ARCHIVE_DIRNAME

    @property
    def state_path(self) -> Path:
        return self.skills_root / STATE_FILE

    # -- settings ------------------------------------------------------------

    def settings(self) -> dict[str, Any]:
        """The four knobs, read live off the config with the defaults above."""
        out: dict[str, Any] = {}
        for key, default in DEFAULT_SETTINGS.items():
            raw = getattr(self._config, key, default) if self._config is not None else default
            if isinstance(default, bool):
                out[key] = bool(raw)
            else:
                try:
                    out[key] = max(0, int(raw))
                except (TypeError, ValueError):
                    out[key] = default
        return out

    def enabled(self) -> bool:
        return bool(self.settings()["curator_enabled"])

    # -- reads ---------------------------------------------------------------

    def _live_dirs(self) -> list[Path]:
        root = self.skills_root
        if not root.is_dir():
            return []
        out: list[Path] = []
        for child in sorted(root.iterdir()):
            if child.name.startswith(".") or not child.is_dir():
                continue  # .archive is never "live"
            if (child / SKILL_FILE).is_file():
                out.append(child)
        return out

    def _live_skills(self) -> list[Skill]:
        skills: list[Skill] = []
        for d in self._live_dirs():
            try:
                skills.append(load_skill(d, source="user"))
            except Exception:  # noqa: BLE001 — a mangled file is not the curator's
                continue
        return skills

    def _find_live(self, name: str) -> Skill | None:
        """A user-root skill by frontmatter name, else by folder name."""
        wanted = (name or "").strip()
        if not wanted:
            return None
        skills = self._live_skills()
        for s in skills:
            if s.name == wanted:
                return s
        for s in skills:
            if s.dir.name == wanted:
                return s
        return None

    def _stats(self) -> dict[str, dict[str, Any]]:
        """Per-skill activity off the learning engine's stat rows."""
        engine = getattr(self.learning, "engine", None)
        if engine is None:
            return {}
        out: dict[str, dict[str, Any]] = {}
        try:
            with session_scope(engine) as db:
                rows = list(db.exec(select(SkillStatRecord)))
        except Exception:  # noqa: BLE001 — no stats reads as never active
            log.exception("skill curator could not read skill stats")
            return {}
        for s in rows:
            out[s.skill_name] = {
                "use_count": int(s.use_count or 0),
                "inject_count": int(getattr(s, "inject_count", 0) or 0),
                "last_used_at": _as_utc(s.last_used_at),
                "last_injected_at": _as_utc(getattr(s, "last_injected_at", None)),
            }
        return out

    def _judge(self, skill: Skill, stat: dict[str, Any] | None, now: datetime) -> dict[str, Any]:
        """One skill's lifecycle facts + whether (and why) it is a candidate."""
        settings = self.settings()
        min_age = settings["curator_min_age_days"]
        stale_after = settings["curator_stale_after_days"]
        try:
            created = datetime.fromtimestamp(
                (skill.dir / SKILL_FILE).stat().st_mtime, tz=timezone.utc
            )
        except OSError:
            created = now
        stat = stat or {}
        use_count = int(stat.get("use_count") or 0)
        inject_count = int(stat.get("inject_count") or 0)
        last_used = stat.get("last_used_at")
        last_injected = stat.get("last_injected_at")
        activity = [t for t in (last_used, last_injected) if t is not None]
        last_activity = max(activity) if activity else None
        age_days = _days((now - created).total_seconds())
        idle_days = (
            _days((now - last_activity).total_seconds())
            if last_activity is not None
            else age_days
        )
        never_active = use_count == 0 and inject_count == 0 and last_activity is None

        reason = ""
        # Three exemptions, in order: the user wrote it (never a candidate —
        # a tool does not decide the user's own work is clutter); it is
        # pinned; it is younger than the minimum age.
        exempt = (
            skill.created_by not in SWEEPABLE_CREATED_BY
            or skill.pinned
            or age_days < min_age
        )
        if exempt:
            reason = ""
        elif never_active:
            reason = (
                f"made by {'an agent' if skill.created_by == 'agent' else 'a proposal'} "
                f"{age_days:g} days ago and never loaded or injected since"
            )
        elif idle_days >= stale_after:
            reason = f"not loaded or injected for {idle_days:g} days"

        return {
            "name": skill.name,
            "created_by": skill.created_by,
            "created_session": skill.created_session,
            "pinned": bool(skill.pinned),
            "use_count": use_count,
            "inject_count": inject_count,
            "last_used_at": _iso(last_used),
            "last_injected_at": _iso(last_injected),
            "created_at": created.isoformat(),
            "age_days": age_days,
            "idle_days": idle_days,
            "reason": reason,
            "candidate": bool(reason),
        }

    def candidates(self) -> list[dict[str, Any]]:
        """The skills the automatic sweep would archive right now, with the
        reason for each. User-authored skills are never in this list — the
        user wrote them — and neither are pinned ones or anything younger
        than ``curator_min_age_days``."""
        now = self._now()
        stats = self._stats()
        out: list[dict[str, Any]] = []
        for skill in self._live_skills():
            view = self._judge(skill, stats.get(skill.name), now)
            if view["candidate"]:
                out.append(view)
        return out

    def archived(self) -> list[dict[str, Any]]:
        """Everything parked under ``.archive``, newest archive stamp first."""
        root = self.archive_root
        if not root.is_dir():
            return []
        out: list[dict[str, Any]] = []
        for child in sorted(root.iterdir()):
            if not child.is_dir() or not (child / SKILL_FILE).is_file():
                continue
            try:
                skill = load_skill(child, source="user")
            except Exception:  # noqa: BLE001
                continue
            view = skill_view(skill)
            view["folder"] = child.name
            out.append(view)
        out.sort(key=lambda v: (v.get("archived_at") or "", v["name"]), reverse=True)
        return out

    def _read_state(self) -> dict[str, Any]:
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _write_state(self, result: dict[str, Any], when: datetime) -> None:
        self.skills_root.mkdir(parents=True, exist_ok=True)
        payload = {"last_sweep_at": _as_utc(when).isoformat(), "last_result": result}
        tmp = self.state_path.with_name(self.state_path.name + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        os.replace(tmp, self.state_path)

    def status(self) -> dict[str, Any]:
        state = self._read_state()
        return {
            "enabled": self.enabled(),
            "last_sweep_at": state.get("last_sweep_at"),
            "last_result": state.get("last_result"),
            "candidates": len(self.candidates()),
            "archived": len(self.archived()),
            "settings": self.settings(),
            "archive_dir": str(self.archive_root),
        }

    def overview(self) -> dict[str, Any]:
        """``GET /skills/curator``: status + the candidate list + the archive."""
        out = self.status()
        out["candidates"] = self.candidates()
        out["archived"] = self.archived()
        return out

    # -- writes --------------------------------------------------------------

    def _rescan(self) -> None:
        if self.framework is None:
            return
        try:
            self.framework.repopulate(
                self.home, getattr(self._config, "extra_skill_paths", None)
            )
        except Exception:  # noqa: BLE001 — the files moved; next boot rescans
            log.exception("skill registry rescan failed after a curator move")

    @staticmethod
    def _rewrite_keeping_mtime(skill_dir: Path, **changes: Any) -> None:
        """Frontmatter rewrite that leaves ``created_at`` (the mtime) alone."""
        md = Path(skill_dir) / SKILL_FILE
        try:
            st = md.stat()
            times = (st.st_atime, st.st_mtime)
        except OSError:
            times = None
        update_frontmatter(skill_dir, **changes)
        if times is not None:
            try:
                os.utime(md, times)
            except OSError:
                pass

    def backup(self, now: datetime | None = None) -> Path:
        """Tar the WHOLE skills folder (archive included) to
        ``<home>/backups/skills-<stamp>.tar.gz``; atomic (temp + replace)."""
        now = now or self._now()
        backups = self.home / "backups"
        backups.mkdir(parents=True, exist_ok=True)
        out = backups / f"skills-{_stamp(now)}.tar.gz"
        i = 2
        while out.exists():  # two sweeps in one second — never overwrite one
            out = backups / f"skills-{_stamp(now)}-{i}.tar.gz"
            i += 1
        tmp = out.with_name(out.name + ".tmp")
        root = self.skills_root
        try:
            with tarfile.open(tmp, "w:gz") as tar:
                if root.is_dir():
                    for p in sorted(root.rglob("*")):
                        if p.is_file():
                            tar.add(p, arcname=str(p.relative_to(self.home)))
            os.replace(tmp, out)
        finally:
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass
        return out

    def _move_to_archive(self, skill: Skill, now: datetime) -> Path:
        """Move one live skill folder under ``.archive`` and stamp it."""
        self.archive_root.mkdir(parents=True, exist_ok=True)
        dest = self.archive_root / skill.dir.name
        if dest.exists():
            dest = self.archive_root / f"{skill.dir.name}-{_stamp(now)}"
            i = 2
            while dest.exists():
                dest = self.archive_root / f"{skill.dir.name}-{_stamp(now)}-{i}"
                i += 1
        shutil.move(str(skill.dir), str(dest))
        self._rewrite_keeping_mtime(
            dest,
            archived_at=_as_utc(now).isoformat(),
            archived_from=skill.dir.name,
        )
        return dest

    def sweep(self, dry_run: bool = False) -> dict[str, Any]:
        """Archive every candidate. ``dry_run`` moves nothing and lists what
        a real sweep would move under ``would_archive``.

        Returns ``{"archived": [names], "kept": [names], "backup": path|None,
        "dry_run": bool, "would_archive": [names]}`` — ``kept`` is every live
        user-root skill the sweep left in place. A manual sweep runs whether
        or not ``curator_enabled`` is on (the switch gates the LOOP; a press
        is the user asking).
        """
        with self._lock:
            now = self._now()
            cands = self.candidates()
            live = [s.name for s in self._live_skills()]
            names = [c["name"] for c in cands]
            if dry_run:
                return {
                    "dry_run": True,
                    "archived": [],
                    "would_archive": names,
                    "kept": [n for n in live if n not in names],
                    "backup": None,
                }
            backup: Path | None = None
            archived: list[str] = []
            by_name = {s.name: s for s in self._live_skills()}
            for name in names:
                skill = by_name.get(name)
                if skill is None:
                    continue
                if backup is None:
                    backup = self.backup(now)  # BEFORE the first move, once per sweep
                try:
                    self._move_to_archive(skill, now)
                except OSError:
                    log.exception("skill curator could not archive %s", name)
                    continue
                archived.append(name)
            if archived:
                self._rescan()
            result = {
                "dry_run": False,
                "archived": archived,
                "would_archive": names,
                "kept": [n for n in live if n not in archived],
                "backup": str(backup) if backup is not None else None,
            }
            self._write_state(result, now)
            return result

    def archive(self, name: str) -> dict[str, Any] | None:
        """Archive ONE user-root skill by hand — any provenance, pinned or
        not: the user decides. None when there is no such live skill."""
        with self._lock:
            skill = self._find_live(name)
            if skill is None:
                return None
            dest = self._move_to_archive(skill, self._now())
            self._rescan()
            moved = load_skill(dest, source="user")
            view = skill_view(moved)
            view["folder"] = dest.name
            return view

    def restore(self, name: str) -> dict[str, Any] | None:
        """Move an archived skill back into service. None when nothing in the
        archive carries that name (frontmatter name or archive folder name);
        ``ValueError`` when a live skill already owns the folder it came from.
        """
        with self._lock:
            wanted = (name or "").strip()
            root = self.archive_root
            match: Path | None = None
            if root.is_dir() and wanted:
                for child in sorted(root.iterdir()):
                    if not (child / SKILL_FILE).is_file():
                        continue
                    if child.name == wanted:
                        match = child
                        break
                    try:
                        if load_skill(child, source="user").name == wanted:
                            match = child
                            break
                    except Exception:  # noqa: BLE001
                        continue
            if match is None:
                return None
            archived = load_skill(match, source="user")
            try:
                meta, _ = read_skill_md(match)
                meta_from = str(meta.get("archived_from") or "").strip()
            except Exception:  # noqa: BLE001
                meta_from = ""
            folder = meta_from or match.name
            dest = self.skills_root / folder
            if dest.exists():
                raise ValueError(
                    f"a live skill already uses the folder '{folder}' — "
                    f"archive or rename that one first, then restore '{archived.name}'"
                )
            shutil.move(str(match), str(dest))
            self._rewrite_keeping_mtime(dest, archived_at=None, archived_from=None)
            self._rescan()
            return skill_view(load_skill(dest, source="user"))

    def _set_pinned(self, name: str, pinned: bool) -> dict[str, Any] | None:
        with self._lock:
            skill = self._find_live(name)
            if skill is None:
                return None
            self._rewrite_keeping_mtime(skill.dir, pinned=bool(pinned))
            self._rescan()
            return skill_view(load_skill(skill.dir, source="user"))

    def pin(self, name: str) -> dict[str, Any] | None:
        """Exempt a live user-root skill from the sweep. None = no such skill
        (a builtin or an external skill cannot be pinned — the sweep never
        looks at those roots, so there is nothing to exempt)."""
        return self._set_pinned(name, True)

    def unpin(self, name: str) -> dict[str, Any] | None:
        return self._set_pinned(name, False)

    # -- the daemon loop -----------------------------------------------------

    @staticmethod
    async def _wait(stop: asyncio.Event, seconds: float) -> bool:
        """Sleep ``seconds`` or until ``stop`` is set; True when it was."""
        try:
            await asyncio.wait_for(stop.wait(), timeout=max(0.0, seconds))
            return True
        except asyncio.TimeoutError:
            return False

    async def run_forever(
        self,
        stop: asyncio.Event,
        tick_cb: TickCb | None = None,
        *,
        first_delay_s: float = FIRST_RUN_DELAY_S,
        interval_s: float | None = None,
    ) -> None:
        """Sweep every ``curator_interval_hours`` (first run ``first_delay_s``
        after boot). The sweep runs off the loop (``asyncio.to_thread``); a
        cycle's exception is reported through ``tick_cb(False, exc)`` and
        swallowed; a cycle that ran — or was skipped because the curator is
        switched off — reports ``tick_cb(True, None)``. Ends when ``stop`` is
        set; cancellation propagates.
        """
        if await self._wait(stop, first_delay_s):
            return
        while not stop.is_set():
            try:
                if self.enabled():
                    await asyncio.to_thread(self.sweep)
                if tick_cb is not None:
                    tick_cb(True, None)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — a cycle must never kill the loop
                log.exception("skill curator sweep failed")
                if tick_cb is not None:
                    try:
                        tick_cb(False, exc)
                    except Exception:  # noqa: BLE001
                        pass
            wait_s = (
                float(interval_s)
                if interval_s is not None
                else float(self.settings()["curator_interval_hours"]) * 3600.0
            )
            if await self._wait(stop, wait_s):
                return
