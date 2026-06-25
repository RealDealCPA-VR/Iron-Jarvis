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
    ) -> None:
        self.dir = Path(directory)
        self.embedder = embedder
        self.recursive = recursive
        self.dir.mkdir(parents=True, exist_ok=True)

    # -- helpers ----------------------------------------------------------
    def _files(self) -> list[Path]:
        if not self.dir.exists():
            return []
        pattern = "**/*.md" if self.recursive else "*.md"
        return sorted(p for p in self.dir.glob(pattern) if p.is_file())

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
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self.dir / f"{slugify(title)}.md"
        if path.exists():
            existing = self._read(path).rstrip()
            body = f"{existing}\n\n{content.rstrip()}\n"
        else:
            body = f"# {title}\n\n{content.rstrip()}\n"
        path.write_text(body, encoding="utf-8")
        return str(path)
