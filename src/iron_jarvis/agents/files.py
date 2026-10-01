"""An agent keeps a folder (v1.297.0): ``<home>/agents/<slug>/``.

Every custom agent (a :class:`~iron_jarvis.agents.dynamic_models.DynamicAgentRecord`)
owns one folder on disk with three human-facing things in it:

* ``AGENTS.md`` — its instructions. The SAME text as the record's
  ``system_prompt``: the DB stays the mirror the runtime reads, the file is the
  copy a person opens in an editor. :class:`~iron_jarvis.agents.dynamic.
  DynamicAgentRegistry` writes it on every prompt change (and backfills a
  missing one on ``load()``), so the two never disagree for long.
* ``NOTES.md`` — its private notebook (≤ :data:`NOTES_CAP_BYTES`). The agent
  writes it through the ``notebook`` tool and reads it back at the top of
  every run (:meth:`AgentFiles.notebook_block`, head+tail trimmed to
  :data:`NOTEBOOK_INJECT_CHARS`).
* ``revisions/<stamp>-<reason>.md`` — the PREVIOUS text of ``AGENTS.md``
  every time it changed, newest :data:`MAX_REVISIONS` kept, each with a
  three-line header (``# revision`` / ``reason:`` / ``at:``) so a restore can
  say what it is restoring.

CONFINEMENT: every path is built from :func:`agent_slug` (ONE sanitizer, the
same one the portraits use — lifted here so the two can never disagree) and
then checked to still resolve UNDER ``<home>/agents`` with the same containment
test ``core/fs_policy`` uses. A slug cannot escape the folder by spelling.

NEVER DELETE: :meth:`AgentFiles.remove` MOVES the folder to
``<home>/trash/agents-<slug>-<stamp>/`` (the v1.256.0 ``clear_media`` rule —
freeing the disk is a second, deliberate press on the Settings page).

Everything here is SYNCHRONOUS disk I/O. Callers on the event loop hop to a
thread (``asyncio.to_thread``); the routes are sync ``def`` (threadpool).
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..core.fs_policy import _canonical, _within

log = logging.getLogger("iron_jarvis.agents.files")

AGENTS_DIRNAME = "agents"
TRASH_DIRNAME = "trash"
INSTRUCTIONS_FILE = "AGENTS.md"
NOTES_FILE = "NOTES.md"
REVISIONS_DIRNAME = "revisions"

#: NOTES.md never grows past this; an append over the cap drops the OLDEST lines.
NOTES_CAP_BYTES = 16 * 1024
#: What a run sees of the notebook: head + tail around this marker.
NOTEBOOK_INJECT_CHARS = 4_000
TRIM_MARKER = "[… middle trimmed …]"
#: Revisions of AGENTS.md kept per agent; the oldest are pruned past this.
MAX_REVISIONS = 50

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")
_REASON_UNSAFE = re.compile(r"[^a-z0-9]+")
_REVISION_ID = re.compile(r"^[A-Za-z0-9._-]{1,120}$")

#: DOS device names: opening ``<dir>/nul.md`` opens the DEVICE, not a file —
#: Windows matches the segment before the FIRST dot, case-insensitively.
_WINDOWS_RESERVED = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{i}" for i in "123456789"}
    | {f"lpt{i}" for i in "123456789"}
)


def agent_slug(name: str) -> str:
    """One path-safe folder/file segment that never EATS the identity.

    Lifted verbatim from the portraits' ``_avatar_slug`` (v1.153.2) so an
    agent's folder, portrait and face key are built from ONE rule. A clean
    LOWERCASE name passes through verbatim; any name the sanitizer had to touch
    (or that case-folding changed — NTFS/APFS are case-insensitive) gets a
    short digest of the ORIGINAL appended, so ``a/b`` and ``a_b`` can never
    collide on one folder. Windows reserved device names get the digest
    PREFIXED (the device match keys on the segment before the first dot).
    Idempotent: a slug this produced slugs to itself.
    """
    raw = str(name or "").strip()
    slug = _UNSAFE.sub("_", raw).strip("._")
    lowered = slug.lower()
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8]
    if lowered.split(".", 1)[0] in _WINDOWS_RESERVED:
        return f"{digest}-{lowered}"
    if not lowered or lowered != raw:
        return f"{lowered or 'agent'}-{digest}"
    return lowered


def _reason_slug(reason: str) -> str:
    text = _REASON_UNSAFE.sub("-", str(reason or "").strip().lower()).strip("-")
    return (text or "edit")[:32].rstrip("-") or "edit"


def _stamp(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")


def _trim_to_bytes(text: str, cap: int) -> str:
    """Keep the TAIL of ``text`` under ``cap`` bytes, dropping whole oldest
    lines first; a single oversize line is clipped from the front."""
    if len(text.encode("utf-8")) <= cap:
        return text
    lines = text.split("\n")
    while lines and len("\n".join(lines).encode("utf-8")) > cap:
        lines.pop(0)
    if lines:
        return "\n".join(lines)
    # One line bigger than the cap: clip it (never raise, never grow).
    data = text.encode("utf-8")[-cap:]
    return data.decode("utf-8", errors="ignore")


def trim_head_tail(text: str, limit: int = NOTEBOOK_INJECT_CHARS) -> str:
    """``text`` unchanged when it fits; otherwise its head and tail around
    :data:`TRIM_MARKER`, the whole result at most ``limit`` characters."""
    if len(text) <= limit:
        return text
    marker = f"\n{TRIM_MARKER}\n"
    room = max(0, limit - len(marker))
    head = room * 2 // 3
    tail = room - head
    return text[:head] + marker + (text[-tail:] if tail else "")


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


@dataclass(frozen=True)
class RevisionInfo:
    """What a write of ``AGENTS.md`` did. ``revision`` is the id of the revision
    file holding the PREVIOUS text, or ``""`` when none was written (first
    write, or the text did not change). ``text`` is what the file holds now."""

    slug: str
    path: str
    text: str
    revision: str = ""
    reason: str = ""
    changed: bool = False


class AgentFiles:
    """The folders under ``<home>/agents/``. Every method takes the agent's
    NAME (or an already-made slug — :func:`agent_slug` is idempotent) and
    confines the resulting path; a slug that would leave the root raises
    :class:`ValueError` rather than touching anything."""

    def __init__(self, home: Path | str) -> None:
        self.home = Path(home)
        self.root = self.home / AGENTS_DIRNAME

    # --- paths ------------------------------------------------------------

    def folder(self, name: str) -> Path:
        """``<home>/agents/<slug>/`` — raises ``ValueError`` if the sanitized
        slug would still resolve outside the agents root (belt and braces: the
        sanitizer leaves no separators, this check is the proof)."""
        if not str(name or "").strip():
            raise ValueError("an agent name is required for a folder")
        slug = agent_slug(name)
        if not slug or slug in (".", ".."):
            raise ValueError("agent name does not make a folder")
        self.root.mkdir(parents=True, exist_ok=True)
        root = _canonical(self.root)
        target = _canonical(self.root / slug)
        if target == root or not _within(target, root):
            raise ValueError(f"refusing a path outside the agents folder: {name!r}")
        return self.root / slug

    def _instructions_path(self, name: str) -> Path:
        return self.folder(name) / INSTRUCTIONS_FILE

    def _notes_path(self, name: str) -> Path:
        return self.folder(name) / NOTES_FILE

    def _revisions_dir(self, name: str) -> Path:
        return self.folder(name) / REVISIONS_DIRNAME

    def _revision_path(self, name: str, revision_id: str) -> Path:
        rid = str(revision_id or "").strip()
        if not _REVISION_ID.match(rid) or rid in (".", ".."):
            raise ValueError("bad revision id")
        base = self._revisions_dir(name)
        target = base / (rid if rid.endswith(".md") else f"{rid}.md")
        if not _within(_canonical(target), _canonical(base)):
            raise ValueError("bad revision id")
        return target

    def exists(self, name: str) -> bool:
        try:
            return self.folder(name).is_dir()
        except (ValueError, OSError):
            return False

    # --- AGENTS.md --------------------------------------------------------

    def read_instructions(self, name: str) -> str | None:
        """The file's text, or ``None`` when there is no AGENTS.md yet."""
        try:
            return self._instructions_path(name).read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            return None

    def write_instructions(self, name: str, text: str, reason: str = "edit") -> RevisionInfo:
        """Put ``text`` in AGENTS.md. When the file already holds a DIFFERENT
        text, that previous text is kept first as a revision (so an edit is
        never a loss). An unchanged text writes nothing."""
        text = str(text or "")
        path = self._instructions_path(name)
        slug = path.parent.name
        previous = self.read_instructions(name)
        if previous is not None and previous == text:
            return RevisionInfo(slug=slug, path=str(path), text=text, reason=reason)
        revision = ""
        if previous is not None:
            revision = self._write_revision(name, previous, reason)
        _atomic_write(path, text)
        return RevisionInfo(
            slug=slug, path=str(path), text=text, revision=revision,
            reason=reason, changed=True,
        )

    def _write_revision(self, name: str, previous: str, reason: str) -> str:
        rdir = self._revisions_dir(name)
        rdir.mkdir(parents=True, exist_ok=True)
        at = datetime.now(timezone.utc)
        base = f"{_stamp(at)}-{_reason_slug(reason)}"
        rid = base
        n = 2
        while (rdir / f"{rid}.md").exists():  # same second, same reason
            rid = f"{base}-{n:03d}"
            n += 1
        header = (
            "# revision\n"
            f"reason: {' '.join(str(reason or 'edit').split())}\n"
            f"at: {at.isoformat()}\n"
            "\n"
        )
        _atomic_write(rdir / f"{rid}.md", header + previous)
        self._prune_revisions(rdir)
        return rid

    @classmethod
    def _scan_revisions(cls, rdir: Path) -> list[dict]:
        """Every revision in ``rdir``, NEWEST FIRST by the header's ``at``
        (microsecond precision — the file stamp is per second, so two writes
        in one second would otherwise sort by reason), then by name."""
        out: list[dict] = []
        try:
            entries = list(rdir.iterdir())
        except OSError:
            return out
        for p in entries:
            if p.suffix != ".md" or not p.is_file():
                continue
            try:
                raw = p.read_text(encoding="utf-8")
            except (OSError, ValueError):
                continue
            at, reason, body = cls._parse_revision(raw)
            out.append({
                "id": p.stem, "at": at, "reason": reason,
                "bytes": len(body.encode("utf-8")), "_path": p,
            })
        out.sort(key=lambda r: (r["at"], r["id"]), reverse=True)
        return out

    @classmethod
    def _prune_revisions(cls, rdir: Path) -> None:
        for extra in cls._scan_revisions(rdir)[MAX_REVISIONS:]:
            try:
                extra["_path"].unlink()  # a pruned revision is the one deliberate delete
            except OSError:
                pass

    @staticmethod
    def _parse_revision(raw: str) -> tuple[str, str, str]:
        """``(at, reason, text)`` from a revision file; a file without the
        header reads as (``""``, ``""``, whole text)."""
        lines = raw.split("\n")
        if len(lines) >= 3 and lines[0].strip() == "# revision":
            reason = lines[1].partition(":")[2].strip() if lines[1].startswith("reason:") else ""
            at = lines[2].partition(":")[2].strip() if lines[2].startswith("at:") else ""
            body = lines[3:]
            if body and body[0] == "":
                body = body[1:]
            return at, reason, "\n".join(body)
        return "", "", raw

    def list_revisions(self, name: str) -> list[dict]:
        """``[{id, at, reason, bytes}]``, newest first. Never raises."""
        try:
            rdir = self._revisions_dir(name)
            if not rdir.is_dir():
                return []
            return [
                {k: v for k, v in row.items() if k != "_path"}
                for row in self._scan_revisions(rdir)
            ]
        except (ValueError, OSError):
            return []

    def read_revision(self, name: str, revision_id: str) -> dict | None:
        """``{id, at, reason, text}`` or ``None`` when there is no such revision
        (a malformed id reads as none, never as an error)."""
        try:
            path = self._revision_path(name, revision_id)
            raw = path.read_text(encoding="utf-8")
        except (ValueError, OSError):
            return None
        at, reason, text = self._parse_revision(raw)
        return {"id": path.stem, "at": at, "reason": reason, "text": text}

    def restore_revision(self, name: str, revision_id: str, reason: str = "restore") -> RevisionInfo:
        """Put a revision's text back into AGENTS.md. The CURRENT text is kept
        as a revision first (a restore is itself undoable). ``KeyError`` for an
        unknown revision."""
        old = self.read_revision(name, revision_id)
        if old is None:
            raise KeyError(revision_id)
        return self.write_instructions(name, old["text"], reason=f"{reason} {old['id']}")

    # --- NOTES.md ---------------------------------------------------------

    def read_notes(self, name: str) -> str:
        try:
            return self._notes_path(name).read_text(encoding="utf-8")
        except (OSError, ValueError):
            return ""

    def write_notes(self, name: str, text: str) -> str:
        """Replace the notebook (bounded to :data:`NOTES_CAP_BYTES`, oldest
        lines dropped). Returns the absolute path written."""
        path = self._notes_path(name)
        _atomic_write(path, _trim_to_bytes(str(text or ""), NOTES_CAP_BYTES))
        return str(path)

    def append_notes(self, name: str, line: str) -> str:
        """Add one line (or block) to the notebook; when the result is over the
        cap the OLDEST lines go. Never raises on the cap."""
        current = self.read_notes(name)
        addition = str(line or "").rstrip("\n")
        if current and not current.endswith("\n"):
            current += "\n"
        return self.write_notes(name, current + addition + "\n")

    def notebook_block(self, name: str) -> str:
        """The system-prompt block for a run: ``""`` when the notebook is empty,
        else a ``# Your notebook`` section with the notes head+tail trimmed to
        :data:`NOTEBOOK_INJECT_CHARS` characters."""
        notes = self.read_notes(name).strip()
        if not notes:
            return ""
        body = trim_head_tail(notes, NOTEBOOK_INJECT_CHARS)
        return (
            "# Your notebook\n"
            "Your own notes from earlier runs (NOTES.md in your folder; the "
            "`notebook` tool reads and writes it):\n\n" + body
        )

    # --- the whole folder -------------------------------------------------

    def remove(self, name: str) -> Path | None:
        """MOVE the folder to ``<home>/trash/agents-<slug>-<stamp>/`` and
        return that path; ``None`` when there was no folder. Nothing is
        deleted."""
        try:
            src = self.folder(name)
        except ValueError:
            return None
        if not src.is_dir():
            return None
        trash = self.home / TRASH_DIRNAME
        trash.mkdir(parents=True, exist_ok=True)
        base = trash / f"agents-{src.name}-{_stamp()}"
        dest = base
        n = 2
        while dest.exists():
            dest = base.with_name(f"{base.name}-{n}")
            n += 1
        shutil.move(str(src), str(dest))
        return dest
