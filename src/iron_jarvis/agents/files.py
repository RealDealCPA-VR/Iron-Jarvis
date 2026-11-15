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


# --- the notebook's folders (v1.501.0, north-star 1.3) ---------------------
#
# NOTES.md is per AGENT, so a note taken while working in one client's folder
# used to ride the agent's next job for another client. A note written during
# work in a folder now ends its first line with FOLDER_MARK (the folder as the
# client wall keys it, ``TurnWall.folder_key``); the block a run is told keeps
# a marked note only where ``workmap.clients.wall.may_tell`` lets that run
# hear it, and never shows the marker. A note with no marker (written outside
# any folder, by the owner on the agent's page, or before this change) is
# told as before. One note = its first line plus the indented lines under it
# (``notebook_tool.stamp_line``'s continuation).

#: The marker at the end of a note's first line.
FOLDER_MARK = "<!-- folder: {key} -->"
_MARK_RX = re.compile(r"\s*<!-- folder: (.*?) -->\s*$")


def _turn_folder() -> str:
    """The folder key of the turn or run in flight ("" outside one, and on
    the owner's own screens). Never raises."""
    try:
        from ..workmap.clients import wall

        if wall.on_owner_screen():
            return ""
        turn = wall.inherited()
        return str(turn.folder_key or "") if turn is not None else ""
    except Exception:  # noqa: BLE001 — no wall: no folder
        return ""


def _marked(line: str, key: str) -> str:
    """``line`` with ``key``'s marker at its end (a line already marked, or
    a blank one, is left as it is)."""
    if not key or not line.strip() or _MARK_RX.search(line):
        return line
    return f"{line.rstrip()} {FOLDER_MARK.format(key=key)}"


def _is_continuation(line: str) -> bool:
    return bool(line) and line[:1] in (" ", "\t") and bool(line.strip())


def _notes_entries(text: str) -> list[tuple[str, list[str], list[str]]]:
    """``[(folder key or "", lines, raw lines)]``: each note with the folder
    its first line is marked with. ``lines`` are what a run is told (markers
    removed); ``raw lines`` are the file's own (markers kept). Indented lines
    belong to the note above them; an indented line with no note above (the
    head was trimmed away) is kept under an unknown folder, so it is never
    told."""
    out: list[tuple[str, list[str], list[str]]] = []
    for line in text.split("\n"):
        if _is_continuation(line) and out:
            out[-1][1].append(line)
            out[-1][2].append(line)
            continue
        if _is_continuation(line):
            out.append(("\0", [line], [line]))  # an orphan: nobody knows its folder
            continue
        m = _MARK_RX.search(line)
        out.append((m.group(1).strip() if m else "", [line[: m.start()] if m else line], [line]))
    return out


def _told_verdicts(entries: list[tuple[str, list[str], list[str]]], *, engine=None) -> list[bool]:
    """One verdict per note: may the turn in flight be told it? A note with
    no folder is always told. A turn that failed closed keeps the notes of
    exactly its folder; a client record that cannot be read tells no marked
    note. Never raises: unsure tells only unmarked notes."""
    if not any(key for key, _lines, _raw in entries):
        return [True] * len(entries)
    try:
        from ..workmap.clients import store as client_store
        from ..workmap.clients import wall

        turn = wall.current()
        try:
            cmap = client_store.load_map(engine) if engine is not None else None
            if not isinstance(cmap, client_store.ClientMap):
                cmap = None
        except Exception:  # noqa: BLE001 — unreadable: no marked note (fail closed)
            cmap = None
        out: list[bool] = []
        for key, lines, _raw in entries:
            if key == "\0":
                out.append(False)
            elif key and (
                (cmap is None and turn.ok)
                or not wall.may_tell(turn, cmap or client_store.EMPTY_MAP, folder=key,
                                     snippet="\n".join(lines))
            ):
                out.append(False)
            else:
                out.append(True)
        return out
    except Exception:  # noqa: BLE001
        log.debug("notebook: the client wall could not be read", exc_info=True)
        return [not key for key, _lines, _raw in entries]


def told_notes_text(text: str, *, engine=None) -> str:
    """``text`` (a notebook) with only the notes the turn in flight may be
    told, markers removed (module note above; :func:`_told_verdicts`)."""
    entries = _notes_entries(text)
    if not any(key for key, _lines, _raw in entries):
        return text
    verdicts = _told_verdicts(entries, engine=engine)
    return "\n".join(ln for (_k, lines, _raw), ok in zip(entries, verdicts) if ok for ln in lines)


def _in_model_turn() -> bool:
    """Is a chat turn or agent run in flight (so the notebook the model sees
    is the FILTERED one), as opposed to the owner's own page or an Undo?"""
    try:
        from ..workmap.clients import wall

        return wall.inherited() is not None and not wall.on_owner_screen()
    except Exception:  # noqa: BLE001 — no wall: the owner's view
        return False


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

    def write_notes(self, name: str, text: str, *, engine=None) -> str:
        """Replace the notebook (bounded to :data:`NOTES_CAP_BYTES`, oldest
        lines dropped). Returns the absolute path written.

        v1.501.0 (north-star 1.3): written during a turn or run (the agent's
        ``replace``), the agent rewrote the notebook IT WAS SHOWN, which leaves
        out the notes the client wall keeps from that run. So:

        * every note the run could not be told is KEPT, word for word, ahead
          of the new text (a replace in one client's folder never deletes
          another client's notes);
        * a note the run kept as it was shown gets its own marker back;
        * a note that is NEW is marked with the run's folder, when it has one.

        Written outside a turn (the owner's page, an Undo) the text is the
        whole file: nothing is kept back and nothing is marked. ``engine``
        reads the client record (without one, every marked note is kept)."""
        path = self._notes_path(name)
        text = str(text or "")
        if _in_model_turn():
            entries = _notes_entries(self.read_notes(name))
            verdicts = _told_verdicts(entries, engine=engine)
            hidden: list[str] = []
            raw_of: dict[str, str] = {}
            for (_k, lines, raw), ok in zip(entries, verdicts):
                if ok:
                    raw_of.setdefault(lines[0], raw[0])
                else:
                    hidden.extend(raw)
            key = _turn_folder()
            body = "\n".join(
                ln if _is_continuation(ln) else raw_of[ln] if ln in raw_of else _marked(ln, key)
                for ln in text.split("\n")
            )
            text = "\n".join([*hidden, body]) if hidden else body
        _atomic_write(path, _trim_to_bytes(text, NOTES_CAP_BYTES))
        return str(path)

    def append_notes(self, name: str, line: str) -> str:
        """Add one line (or block) to the notebook; when the result is over the
        cap the OLDEST lines go. Never raises on the cap.

        v1.501.0 (north-star 1.3): added during a turn or run in a folder, the
        note's first line is marked with that folder (``FOLDER_MARK``)."""
        current = self.read_notes(name)
        addition = str(line or "").rstrip("\n")
        key = _turn_folder()
        if key:
            head, sep, rest = addition.partition("\n")
            addition = _marked(head, key) + sep + rest
        if current and not current.endswith("\n"):
            current += "\n"
        path = self._notes_path(name)
        _atomic_write(path, _trim_to_bytes(current + addition + "\n", NOTES_CAP_BYTES))
        return str(path)

    def told_notes(self, name: str, *, engine=None) -> str:
        """The notebook as the turn in flight may be told it (v1.501.0,
        north-star 1.3: :func:`told_notes_text`), markers removed. For a
        reader on the model's side; the owner's page reads :meth:`read_notes`."""
        return told_notes_text(self.read_notes(name), engine=engine)

    def notebook_block(
        self, name: str, *, event_bus=None, session_id: "str | None" = None, engine=None,
    ) -> str:
        """The system-prompt block for a run: ``""`` when the notebook is empty,
        else a ``# Your notebook`` section with the notes head+tail trimmed to
        :data:`NOTEBOOK_INJECT_CHARS` characters.

        v1.298.0: the notes are SCANNED for prompt injection first
        (``core/promptguard``) — an agent writes its own notebook, and a tool
        result it copied in verbatim is exactly how an attack would persist
        across runs. A flagged paragraph becomes ``[BLOCKED: …]``; the rest of
        the notebook still loads. ``event_bus``/``session_id`` (optional) let
        the caller have the one ``context.blocked`` event published.

        v1.501.0 (north-star 1.3): only the notes THE CLIENT WALL lets the run
        in flight hear (:meth:`told_notes`; ``engine`` reads the client
        record, and without one no note written in a folder is told)."""
        from ..core.promptguard import guard

        notes = self.told_notes(name, engine=engine).strip()
        if not notes:
            return ""
        body = trim_head_tail(notes, NOTEBOOK_INJECT_CHARS)
        body = guard(
            body, source=f"notebook of {name}", event_bus=event_bus,
            session_id=session_id, cap=None,  # trim_head_tail already bounded it
        )
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
