"""Long-term memory connectors (§21 external knowledge stores).

An :class:`LTMConnector` is a thin adapter over an *external* knowledge store —
an Obsidian vault, a generic markdown "brain" folder, a Notion database — that
the agent can search and append to. Every connector returns a uniform hit shape:
``{"title", "snippet", "ref", "source"}``.

This module also ships the shared, fully-offline markdown-folder implementation
(:class:`MarkdownDirConnector`) reused by both the Obsidian and brain connectors.
"""

from __future__ import annotations

import math
import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_SNIPPET_LEN = 200


class LTMWriteRefused(ValueError):
    """An LTM write that would MISPLACE or DESTROY the user's notes, refused.

    Subclasses :class:`ValueError` on purpose: every append caller already
    handles that (``LTMAppendTool.execute`` -> ``ok=False``, ``POST /ltm/append``
    and ``/ltm/ingest-document`` -> HTTP 400), so the refusal surfaces as an
    honest error everywhere instead of a 500 or a swallowed exception.
    """


def slugify(title: str) -> str:
    """Filesystem-safe slug for a note title (``My Note!`` -> ``my-note``)."""
    slug = re.sub(r"[^\w\s-]", "", title.strip().lower())
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"-{2,}", "-", slug).strip("-")
    return slug or "untitled"


def _tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def _snippet(text: str, query: str, length: int = _SNIPPET_LEN) -> str:
    """A short excerpt centred on the first query match, else the head of the text."""
    flat = " ".join(text.split())
    if not flat:
        return ""
    needle = query.lower().strip()
    idx = flat.lower().find(needle) if needle else -1
    if idx < 0:
        for tok in _tokens(query):
            idx = flat.lower().find(tok)
            if idx >= 0:
                break
    if idx < 0:
        return flat[:length]
    start = max(0, idx - length // 3)
    end = min(len(flat), start + length)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(flat) else ""
    return f"{prefix}{flat[start:end]}{suffix}"


class LTMConnector(ABC):
    """A searchable/appendable connector to one external knowledge store."""

    name: str = ""

    @abstractmethod
    def search(self, query: str, k: int = 5) -> list[dict[str, Any]]:
        """Return up to ``k`` hits as ``{title, snippet, ref, source}`` dicts."""

    @abstractmethod
    def append(self, title: str, content: str) -> str:
        """Create/append a note; return a ref (path/id) to the stored item."""

    # -- optional note operations (v1.406.0, Brain Profiles) ----------------
    # A base that cannot do one of these raises :class:`NoteOpUnsupported`
    # with a sentence that names the base, so the brain tools can say so
    # instead of guessing. They are NOT abstract: every existing connector
    # (and every test double) keeps working untouched.
    def read(self, ref: str) -> str:
        """The full text of one note (by the ``ref`` a search hit carried)."""
        raise NoteOpUnsupported(f"{self.name}: this memory base cannot read a note back")

    def update(self, ref: str, content: str) -> str:
        """ADD ``content`` to an EXISTING note; return its ref. Never replaces
        what the note already says."""
        raise NoteOpUnsupported(
            f"{self.name}: this memory base cannot add to an existing note"
        )

    def change(self, action: str, ref: str, to: str = "") -> str:
        """A structural change: ``archive`` | ``delete`` | ``rename`` | ``move``.
        Callers ask the user first (``brain_change`` cards every call)."""
        raise NoteOpUnsupported(
            f"{self.name}: this memory base cannot {action} a note from Iron Jarvis"
        )


class NoteOpUnsupported(RuntimeError):
    """A memory base that has no way to do a note operation (v1.406.0)."""


#: Structural actions ``change`` understands (v1.406.0).
CHANGE_ACTIONS = ("archive", "delete", "rename", "move")


class MarkdownDirConnector(LTMConnector):
    """Shared offline implementation: a folder of ``.md`` files as an LTM store.

    Search ranks notes by case-insensitive filename + content match count, with
    an optional injected ``embedder`` (``.embed(text) -> list[float]``) adding a
    semantic-similarity boost. Append writes/extends ``<slug(title)>.md``.
    """

    name = "markdown"

    def __init__(
        self,
        directory: Path | str,
        embedder: Any = None,
        recursive: bool = True,
        create: bool = True,
    ) -> None:
        self.dir = Path(directory)
        self.embedder = embedder
        self.recursive = recursive
        # REFUSE, DON'T RECREATE (v1.172.0). This mkdir used to be
        # unconditional, so a USER's vault that had moved, been renamed, sat on
        # an unmounted drive, or de-synced from a cloud folder was silently
        # re-created EMPTY at the old path — after which every read honestly
        # returned nothing and the connector looked perfectly healthy. The app
        # went blind and said nothing. Only the app's OWN store (the built-in
        # brain, under the state home) is created on demand; a configured user
        # path is left alone and reports itself missing.
        self.create = bool(create)
        if self.create:
            self.dir.mkdir(parents=True, exist_ok=True)

    @property
    def missing(self) -> bool:
        """True when a user-configured directory is not there right now."""
        try:
            return not self.dir.is_dir()
        except OSError:  # unreadable/offline share — treat as missing, loudly
            return True

    def health(self) -> dict[str, Any]:
        """Availability for the UI: a base that cannot be read SAYS SO, with
        the path, instead of blending into 'no matches' (v1.172.0)."""
        if not self.missing:
            return {"available": True, "detail": "", "path": str(self.dir)}
        return {
            "available": False,
            "path": str(self.dir),
            "detail": (
                f"folder not found: {self.dir} — it was moved, renamed, or is "
                "on a drive/cloud folder that isn't available right now "
                "(nothing was created in its place)"
            ),
        }

    # -- helpers ----------------------------------------------------------
    def _files(self) -> list[Path]:
        if not self.dir.exists():
            return []
        pattern = "**/*.md" if self.recursive else "*.md"
        # A note moved to the folder's trash (v1.406.0 ``change``) is gone.
        return sorted(
            p for p in self.dir.glob(pattern)
            if p.is_file() and ".trash" not in p.relative_to(self.dir).parts
        )

    @staticmethod
    def _read(path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return ""

    @staticmethod
    def _lexical_score(name: str, text: str, query: str) -> float:
        q = query.lower().strip()
        if not q:
            return 0.0
        name_l = name.lower()
        text_l = text.lower()
        # whole-query substring matches (filename weighted highest)
        score = name_l.count(q) * 5.0 + text_l.count(q) * 2.0
        # per-token matches
        for tok in set(_tokens(q)):
            score += name_l.count(tok) * 3.0
            score += float(text_l.count(tok))
        return score

    # -- LTMConnector -----------------------------------------------------
    def search(self, query: str, k: int = 5) -> list[dict[str, Any]]:
        q_emb = None
        if self.embedder is not None and query.strip():
            q_emb = self.embedder.embed(query)
        scored: list[tuple[float, dict[str, Any]]] = []
        for path in self._files():
            text = self._read(path)
            title = path.stem
            score = self._lexical_score(title, text, query)
            if q_emb is not None:
                score += _cosine(q_emb, self.embedder.embed(f"{title}\n{text}")) * 10.0
            if score <= 0.0:
                continue
            scored.append(
                (
                    score,
                    {
                        "title": title,
                        "snippet": _snippet(text, query),
                        "ref": str(path),
                        "source": self.name,
                    },
                )
            )
        scored.sort(key=lambda item: item[0], reverse=True)
        return [hit for _, hit in scored[:k]]

    def append(self, title: str, content: str) -> str:
        # REFUSE, DON'T RECREATE — ON THE WRITE PATH TOO. v1.172.0 put that
        # invariant in __init__ only, and this mkdir stayed UNCONDITIONAL: an
        # append against a user vault that had moved, been renamed, or sat on an
        # unmounted/de-synced drive re-created it EMPTY at the old path, filed
        # the note there, split the vault across two folders — and because
        # `missing` then went False, health() flipped back to available:True and
        # masked the breakage permanently. Only the app's OWN store (create=True,
        # the built-in brain under the state home) still self-creates.
        if self.create:
            self.dir.mkdir(parents=True, exist_ok=True)
        elif self.missing:
            raise LTMWriteRefused(f"cannot append: {self.health()['detail']}")
        path = self.dir / f"{slugify(title)}.md"
        if path.exists():
            # AN APPEND-ONLY TOOL MUST NEVER DELETE. This rewrites the WHOLE
            # file, so the prior text has to be read in full first — and `_read`
            # swallows OSError AND UnicodeDecodeError to "" (right for search,
            # catastrophic here): a cp1252/ANSI note written by Notepad, or one
            # momentarily locked by antivirus, was silently replaced by the new
            # paragraph alone. Unrecoverably: LTMAppendTool.capture_undo hits the
            # same error and journals reversible=False, so no pre-image exists.
            # Distinguish ABSENT (write a new note) from UNREADABLE (refuse).
            try:
                existing = path.read_text(encoding="utf-8").rstrip()
            except (OSError, UnicodeDecodeError) as exc:
                raise LTMWriteRefused(
                    f"cannot append to {path}: its existing contents could not be "
                    f"read ({exc}). An append rewrites the whole note, so this "
                    "would have destroyed it — nothing was written. Check the "
                    "file's encoding (UTF-8 expected) or whether it is locked."
                ) from exc
            body = f"{existing}\n\n{content.rstrip()}\n"
        else:
            body = f"# {title}\n\n{content.rstrip()}\n"
        # Atomic write: a crash mid-write must not lose the ENTIRE prior note (this
        # rewrites the whole file). Stage to a sibling temp then os.replace.
        import os
        import tempfile

        fd, tmp = tempfile.mkstemp(dir=str(self.dir), prefix=path.name + ".", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(body)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        return str(path)

    # -- note operations (v1.406.0) ----------------------------------------
    def _note_path(self, ref: str) -> Path:
        """``ref`` (an absolute path a search hit carried, a path inside the
        folder, or a title) as a ``.md`` file INSIDE this folder — anything
        that resolves outside it is refused, never read or written."""
        raw = str(ref or "").strip()
        if not raw:
            raise LTMWriteRefused(f"{self.name}: no note was named")
        # v1.406.0 review: refused BEFORE anything touches the disk. A network
        # or device spelling would be opened by the existence check and the
        # resolve below — an SMB connection that hands the user's sign-in hash
        # to whatever host a model (or a note) named.
        _refuse_network_path(self.name, raw)
        base = self.dir.resolve()
        cand = Path(raw)
        if not cand.is_absolute():
            cand = self.dir / raw
        if not _lexically_inside(cand, self.dir, base):
            raise LTMWriteRefused(f"{self.name}: {raw} is not a note inside {self.dir}")
        if cand.suffix.lower() != ".md":
            # ADD ".md", never ``with_suffix``: "Plan v1.2" is not "Plan v1" —
            # a with_suffix turned the one into the other, so a read or a
            # DELETE reached a different note than the card named.
            named = Path(str(cand) + ".md")
            cand = named if named.exists() else self.dir / f"{slugify(raw)}.md"
        try:
            resolved = cand.resolve()
            resolved.relative_to(base)
        except (OSError, ValueError) as exc:
            raise LTMWriteRefused(
                f"{self.name}: {raw} is not a note inside {self.dir}"
            ) from exc
        return resolved

    def read(self, ref: str) -> str:
        if self.missing:
            raise LTMWriteRefused(f"cannot read: {self.health()['detail']}")
        path = self._note_path(ref)
        if not path.is_file():
            raise LTMWriteRefused(f"{self.name}: no note at {path.name}")
        try:
            return path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise LTMWriteRefused(f"{self.name}: {path.name} could not be read ({exc})") from exc

    def update(self, ref: str, content: str) -> str:
        if self.missing:
            raise LTMWriteRefused(f"cannot add to a note: {self.health()['detail']}")
        path = self._note_path(ref)
        if not path.is_file():
            raise LTMWriteRefused(f"{self.name}: no note at {path.name} to add to")
        try:
            raw = path.read_bytes().decode("utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise LTMWriteRefused(
                f"cannot add to {path}: its existing contents could not be read "
                f"({exc}) — nothing was written"
            ) from exc
        # v1.406.0 review: the note keeps its OWN line endings. A text-mode
        # write turned every LF note in an Obsidian vault into CRLF on the
        # first save that added to it.
        nl = "\r\n" if "\r\n" in raw else "\n"
        add = content.rstrip().replace("\r\n", "\n").replace("\n", nl)
        _atomic_write(path, f"{raw.rstrip()}{nl}{nl}{add}{nl}", newline="")
        return str(path)

    def change(self, action: str, ref: str, to: str = "") -> str:
        """Archive/delete MOVE the note into the folder's ``.trash`` (Obsidian's
        own trash folder) — never an unlink (the v1.256.0 rule); rename/move
        stay inside this folder."""
        action = str(action or "").strip().lower()
        if action not in CHANGE_ACTIONS:
            raise LTMWriteRefused(f"{self.name}: unknown change {action!r}")
        if self.missing:
            raise LTMWriteRefused(f"cannot change a note: {self.health()['detail']}")
        path = self._note_path(ref)
        if not path.is_file():
            raise LTMWriteRefused(f"{self.name}: no note at {path.name}")
        import os
        import time as _time

        base = self.dir.resolve()
        if action in ("archive", "delete"):
            trash = base / ".trash"
            trash.mkdir(exist_ok=True)
            dest = trash / path.name
            if dest.exists():
                dest = trash / f"{path.stem}-{_time.strftime('%Y%m%d-%H%M%S')}{path.suffix}"
        else:
            target = str(to or "").strip()
            if not target:
                raise LTMWriteRefused(f"{self.name}: {action} needs a new name or folder")
            if action == "rename":
                dest = path.with_name(f"{slugify(target)}.md")
            else:
                _refuse_network_path(self.name, target)
                dest = (base / target / path.name)
                if not _lexically_inside(dest, base):
                    raise LTMWriteRefused(f"{self.name}: {target} is outside {self.dir}")
            try:
                dest.resolve().relative_to(base)
            except (OSError, ValueError) as exc:
                raise LTMWriteRefused(f"{self.name}: {target} is outside {self.dir}") from exc
            if dest.exists():
                raise LTMWriteRefused(f"{self.name}: {dest.name} already exists there")
            dest.parent.mkdir(parents=True, exist_ok=True)
        os.replace(path, dest)
        return str(dest)


def _refuse_network_path(source: str, raw: str) -> None:
    """Refuse a network or device spelling (a UNC share, a forward-slash
    share, a long-path or device prefix) before it is looked up (v1.406.0
    review). Pure string work: never touches the disk or the network."""
    s = str(raw or "").strip().replace("/", "\\")
    if s.startswith("\\\\"):
        raise LTMWriteRefused(
            f"{source}: {raw} is a network or device path, not a note in this memory base"
        )


def _lexically_inside(cand: Path, *roots: Path) -> bool:
    """``cand`` sits inside one of ``roots`` by its SPELLING alone — ``..``
    folded, made absolute, no filesystem call (v1.406.0 review). The resolve
    that follows still catches a link that leads out."""
    import os

    here = Path(os.path.normpath(os.path.abspath(str(cand))))
    for root in roots:
        try:
            here.relative_to(Path(os.path.normpath(os.path.abspath(str(root)))))
            return True
        except ValueError:
            continue
    return False


def _atomic_write(path: Path, body: str, *, newline: "str | None" = None) -> None:
    import os
    import tempfile

    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline=newline) as fh:
            fh.write(body)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
