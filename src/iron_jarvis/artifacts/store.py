"""Artifact store (SPEC §26).

Versioned agent outputs on the local filesystem. Each ``save`` of a given
``name`` writes a fresh ``v<n>`` directory under ``root/<safe_name>/``; an
optional SQLModel engine mirrors every version as an ``ArtifactRecord`` row.
Names and filenames are slugified so a stored artifact can never escape ``root``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from sqlalchemy import Engine
from sqlmodel import select

from ..core.db import session_scope
from ..core.ids import utcnow
from .models import ArtifactRecord

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_name(name: str) -> str:
    """Slugify ``name`` into a single path-safe directory segment (no traversal)."""
    slug = _UNSAFE.sub("_", name.strip()).strip("._")
    return slug or "artifact"


def _safe_filename(filename: str) -> str:
    """Reduce to a slugified basename so a filename cannot escape its version dir."""
    slug = _UNSAFE.sub("_", Path(filename).name.strip()).strip("._")
    return slug or "artifact"


@dataclass
class Artifact:
    """A single saved artifact version (SPEC §26)."""

    name: str
    version: int
    kind: str
    path: Path
    size: int
    created_at: datetime


class ArtifactStore:
    """Filesystem-backed, versioned artifact store (SPEC §26)."""

    def __init__(self, root: Path | str, engine: Engine | None = None) -> None:
        self.root = Path(root)
        self.engine = engine
        self.root.mkdir(parents=True, exist_ok=True)
        #: Optional observer called after every successful ``save`` with
        #: ``(artifact, session_id)`` — the platform wires this to publish the
        #: ``artifact.generated`` event. A failing observer never breaks a save.
        self.on_save: "Callable[[Artifact, str | None], None] | None" = None

    def _dir(self, name: str) -> Path:
        return self.root / _safe_name(name)

    def versions(self, name: str) -> list[int]:
        """Return the sorted list of stored version numbers for ``name``."""
        base = self._dir(name)
        if not base.is_dir():
            return []
        out: list[int] = []
        try:
            children = list(base.iterdir())
        except OSError:  # dir deleted mid-listing (concurrent delete()) = gone
            return []
        for child in children:
            if child.is_dir() and child.name.startswith("v"):
                try:
                    out.append(int(child.name[1:]))
                except ValueError:
                    continue
        return sorted(out)

    def save(
        self,
        name: str,
        content: str | bytes,
        kind: str = "file",
        filename: str | None = None,
        session_id: str | None = None,
        project_id: str | None = None,
    ) -> Artifact:
        """Write ``content`` as the next version of ``name`` (SPEC §26).

        Context spine: an explicit ``project_id`` always wins. When it isn't
        given but the producing ``session_id`` is, the artifact inherits that
        session's project — which covers AGENT-SESSION producers only. Chat
        and Studio producers run as ``session_id="chat"`` (not a Session row),
        so inheritance finds nothing for them: those callers MUST pass
        ``project_id`` explicitly or the artifact strands in the global
        gallery with ``project_id`` NULL (the v1.200.0 audit finding)."""
        existing = self.versions(name)
        version = (existing[-1] + 1) if existing else 1

        vdir = self._dir(name) / f"v{version}"
        vdir.mkdir(parents=True, exist_ok=True)
        fname = _safe_filename(filename) if filename else _safe_filename(name)
        path = vdir / fname

        if isinstance(content, (bytes, bytearray)):
            path.write_bytes(bytes(content))
        else:
            path.write_text(content, encoding="utf-8")

        size = path.stat().st_size
        created = utcnow()
        artifact = Artifact(
            name=name,
            version=version,
            kind=kind,
            path=path,
            size=size,
            created_at=created,
        )

        if self.engine is not None:
            with session_scope(self.engine) as db:
                # Inherit the producing session's project when not told otherwise.
                if project_id is None and session_id:
                    from ..core.models import Session as _Session

                    parent = db.get(_Session, session_id)
                    if parent is not None:
                        project_id = parent.project_id
                db.add(
                    ArtifactRecord(
                        name=name,
                        version=version,
                        kind=kind,
                        path=str(path),
                        session_id=session_id,
                        project_id=project_id,
                        size=size,
                        created_at=created,
                    )
                )
                db.commit()

        if self.on_save is not None:
            try:
                self.on_save(artifact, session_id)
            except Exception:  # noqa: BLE001 - observers never break a save
                pass

        return artifact

    def _version_file(self, name: str, version: int) -> Path | None:
        vdir = self._dir(name) / f"v{version}"
        if not vdir.is_dir():
            return None
        files = sorted(p for p in vdir.iterdir() if p.is_file())
        return files[0] if files else None

    def version_path(self, name: str, version: int | None = None) -> Path | None:
        """The stored file's path for ``name`` (latest version when ``None``).
        Traversal-safe: the name is slugified into a single segment under root."""
        vs = self.versions(name)
        if not vs:
            return None
        want = vs[-1] if version is None else version
        return self._version_file(name, want)

    def read(self, name: str, version: int | None = None) -> bytes:
        """Return the bytes of ``name`` (latest version if ``version`` is None)."""
        vs = self.versions(name)
        if not vs:
            raise FileNotFoundError(f"no artifact named {name!r}")
        want = vs[-1] if version is None else version
        path = self._version_file(name, want)
        if path is None:
            raise FileNotFoundError(f"no artifact {name!r} v{want}")
        return path.read_bytes()

    def latest(self, name: str) -> Artifact | None:
        """Return the newest version of ``name`` as an :class:`Artifact`, or None."""
        vs = self.versions(name)
        if not vs:
            return None
        version = vs[-1]
        path = self._version_file(name, version)
        if path is None:
            return None
        st = path.stat()
        return Artifact(
            name=name,
            version=version,
            kind="file",
            path=path,
            size=st.st_size,
            created_at=datetime.fromtimestamp(st.st_mtime, tz=timezone.utc),
        )

    def delete(self, name: str) -> bool:
        """Remove EVERY stored version of ``name`` — its whole slug directory —
        plus its :class:`ArtifactRecord` rows when an engine is present.

        Returns True when something was actually removed (files or rows), False
        when the name is unknown. Traversal-safe: the name is slugified into a
        single path segment under root, same as every other lookup here.
        """
        import shutil

        base = self._dir(name)
        existed = base.is_dir()
        if existed:
            # Tolerate per-file failures (Windows: a version still open by a
            # streaming FileResponse can't be unlinked) — remove what we can
            # rather than aborting the whole delete on the first locked file.
            # (onexc, not the 3.12-deprecated onerror; requires-python >= 3.12.)
            shutil.rmtree(base, onexc=lambda fn, path, exc: None)

        rows = 0
        if self.engine is not None:
            with session_scope(self.engine) as db:
                records = list(
                    db.exec(select(ArtifactRecord).where(ArtifactRecord.name == name))
                )
                for record in records:
                    db.delete(record)
                rows = len(records)
                db.commit()

        return existed or rows > 0

    def list_names(self) -> list[str]:
        """Return the sorted (slugified) names of all stored artifacts."""
        if not self.root.is_dir():
            return []
        return sorted(d.name for d in self.root.iterdir() if d.is_dir())
