"""The Guide's corpus — what the built-in Iron Jarvis expert is allowed to know.

Two kinds of source, and the distinction is the honesty argument:

* **Bundled docs** — the user-facing guides and the repo's own reference
  files, shipped INSIDE the packaged app (``packaging/ironjarvis.spec`` copies
  each one into ``_MEIPASS/ijdocs``, the same place ``routes/helpdocs.py``
  reads from). A build that dropped one degrades to "the Guide does not know
  that doc" and SAYS so (``GuideIndex.status()``/``doctor``), never to a
  model improvising the missing chapter.
* **Live catalogs** — generated from the running daemon: version, install
  paths, connected models, every API route with its docstring, every tool the
  agents can use, every skill, persona and agent type. These cannot go stale
  the way prose does, because they ARE the running program.

Retrieval is lexical (BM25 over markdown sections, headings weighted) and
deterministic: no embedder, so the Guide answers identically offline, on a
fresh install, and in the test suite — and a wrong answer is reproducible.
The block handed to the model names each section's origin in brackets so the
answer can cite it, and the persona is instructed to answer ONLY from that
block. "I don't know, look here" beats an invented setting.
"""

from __future__ import annotations

import logging
import math
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger("iron_jarvis.guide")

#: The persona slug that switches the Guide's grounding on (``personas/
#: builtins.py`` carries the matching prompt).
GUIDE_PERSONA = "guide"

#: (slug, path relative to the repo root, title). In a frozen build every
#: file lives flat in ``_MEIPASS/ijdocs/<basename>``. This list is ALSO the
#: allowlist the .spec bundles from — keep the two in step, and keep the
#: TOFIX/audit/plan files in docs/ out of it: they are maintainer material.
BUNDLED_DOCS: tuple[tuple[str, str, str], ...] = (
    ("handbook", "docs/HANDBOOK.md", "The Handbook"),
    ("recommended-settings", "docs/RECOMMENDED-SETTINGS.md", "Recommended Settings"),
    ("local-models", "docs/LOCAL-MODELS.md", "Local Models by RAM Tier"),
    ("reflex", "docs/REFLEX.md", "Reflex rules"),
    ("computer-use", "docs/COMPUTER-USE.md", "Computer use"),
    ("readme", "README.md", "README"),
    ("vocabulary", "VOCABULARY.md", "Vocabulary (one name per concept)"),
    ("spec", "SPEC.MD", "Product spec"),
    ("operating-manual", "CLAUDE.md", "Architecture and operating manual"),
)

#: One retrieved section is capped here when a markdown section runs long —
#: a whole chapter in one block would spend the budget on one topic.
SECTION_MAX_CHARS = 1600
#: Default size of the block injected into a Guide turn.
DEFAULT_GROUND_CHARS = 7000
#: How long a live catalog is trusted before it is rebuilt.
LIVE_TTL_SECONDS = 60.0

_WORD = re.compile(r"[a-z0-9]+")
_HEADING = re.compile(r"^(#{1,4})\s+(.*\S)\s*$")

#: Question words carry no topic. BM25's idf already dampens them, but with a
#: few hundred sections "how"/"does" still outrank a rare term now and then.
_STOP = frozenset(
    "a an the and or of to in on at for by with is are was were be been do does "
    "did how what which who whom whose why when where can could should would "
    "will i me my we our you your it its this that these those there here "
    "from into about as if than then so not no yes any some all".split()
)

#: A per-document prior. The Guide serves the USER, so the user-facing guides
#: outrank the maintainers' operating manual and the product spec when both
#: mention a term — the manual is still retrievable for internals nothing else
#: covers. Live catalogs stay neutral.
_DOC_PRIOR: dict[str, float] = {
    "handbook": 1.3,
    "vocabulary": 1.3,
    "readme": 1.0,
    "recommended-settings": 1.15,
    "local-models": 1.15,
    "reflex": 1.15,
    "computer-use": 1.15,
    "spec": 0.85,
    "operating-manual": 0.85,
}
#: Bonus for an adjacent query pair appearing verbatim ("memory base",
#: "restart to update") — the strongest signal a section is ABOUT the thing.
_PHRASE_BONUS = 2.0


def repo_root() -> Path:
    """Dev: the repo root (guide → iron_jarvis → src → repo)."""
    return Path(__file__).resolve().parents[3]


def docs_root() -> Path | None:
    """Where bundled docs live, or None in dev (paths are repo-relative then).
    Frozen: ``_MEIPASS/ijdocs`` — the flat directory the .spec fills."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", "")) / "ijdocs"
    return None


def doc_path(relative: str) -> Path:
    """The on-disk path of one bundled doc for THIS install."""
    root = docs_root()
    if root is not None:
        return root / Path(relative).name
    return repo_root() / relative


def tokens(text: str) -> list[str]:
    return _WORD.findall((text or "").lower())


@dataclass
class Section:
    """One retrievable unit: a markdown section (or a live-catalog page)."""

    doc: str  # slug
    doc_title: str
    heading: str  # "H1 › H2 › H3" path
    text: str
    live: bool = False
    #: Term frequencies over body + heading (heading terms count double —
    #: a section titled "Updates" answers "how do updates work" even when
    #: the body says "the installer").
    tf: dict[str, int] = field(default_factory=dict)
    length: int = 0

    def __post_init__(self) -> None:
        words = tokens(self.text) + tokens(self.heading) * 2
        tf: dict[str, int] = {}
        for w in words:
            tf[w] = tf.get(w, 0) + 1
        self.tf = tf
        self.length = len(words)

    @property
    def label(self) -> str:
        return f"{self.doc_title} › {self.heading}" if self.heading else self.doc_title


def split_markdown(
    slug: str, title: str, text: str, *, max_chars: int = SECTION_MAX_CHARS
) -> list[Section]:
    """Split a markdown document into heading-delimited sections, each no
    longer than ``max_chars`` (long ones are cut at paragraph boundaries and
    numbered). Fenced code blocks never split a section and a ``#`` inside a
    fence is not a heading."""
    out: list[Section] = []
    path: list[str] = []
    buf: list[str] = []
    in_fence = False

    def flush() -> None:
        body = "\n".join(buf).strip()
        buf.clear()
        if not body:
            return
        heading = " › ".join(path)
        parts = _chunk(body, max_chars)
        for i, part in enumerate(parts):
            h = heading if len(parts) == 1 else f"{heading} ({i + 1}/{len(parts)})"
            out.append(Section(slug, title, h, part))

    for line in (text or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            buf.append(line)
            continue
        m = None if in_fence else _HEADING.match(line)
        if m is None:
            buf.append(line)
            continue
        flush()
        level = len(m.group(1))
        name = m.group(2).strip("# ").strip()
        path = path[: level - 1] + [name]
        # Pad shallow paths so "H1 › H3" never reads as "H1 › H2".
        while len(path) < level:
            path.insert(len(path) - 1, "")
        path = [p for p in path if p]
    flush()
    return out


def _chunk(body: str, max_chars: int) -> list[str]:
    if len(body) <= max_chars:
        return [body]
    parts: list[str] = []
    cur: list[str] = []
    size = 0
    for para in re.split(r"\n{2,}", body):
        p = para.strip()
        if not p:
            continue
        if size and size + len(p) + 2 > max_chars:
            parts.append("\n\n".join(cur))
            cur, size = [], 0
        while len(p) > max_chars:  # one enormous paragraph (a table, a list)
            parts.append(p[:max_chars])
            p = p[max_chars:]
        cur.append(p)
        size += len(p) + 2
    if cur:
        parts.append("\n\n".join(cur))
    return parts


# --------------------------------------------------------------- live catalog


def _first_line(doc: str | None) -> str:
    for line in (doc or "").strip().splitlines():
        s = line.strip()
        if s:
            return s
    return ""


def live_sections(platform, app=None) -> list[Section]:
    """The catalogs generated from the running daemon. Each reader is
    best-effort: a store that cannot answer contributes nothing and logs,
    rather than taking the whole Guide down with it."""
    out: list[Section] = []
    L = "live"

    def add(heading: str, lines: list[str]) -> None:
        body = "\n".join(x for x in lines if x)
        if body.strip():
            out.append(Section(L, "Live: this install", heading, body, live=True))

    # Version, install, defaults.
    try:
        from .. import __version__

        cfg = platform.config
        lines = [
            f"- Iron Jarvis version: {__version__}",
            f"- Packaged (frozen) build: {'yes' if getattr(sys, 'frozen', False) else 'no (running from source)'}",
            f"- State home (config.toml, ironjarvis.db, secrets/, skills/): {getattr(cfg, 'home', '')}",
            f"- Default provider / model: {getattr(cfg, 'default_provider', '')} / {getattr(cfg, 'default_model', '') or '(provider default)'}",
            f"- Default persona: {getattr(cfg, 'default_persona', '') or 'assistant'}",
            f"- Active project id: {getattr(cfg, 'active_project_id', None) or 'none'}",
            "- Daemon: http://127.0.0.1:8787 (every request needs the bearer token from "
            "%APPDATA%/Iron Jarvis/token.txt in the packaged app); dashboard: http://127.0.0.1:8788",
        ]
        add("Version and install", lines)
    except Exception:  # noqa: BLE001
        log.exception("guide live catalog: version/install unavailable")

    # Settings keys (names only — values may be sensitive).
    try:
        keys = sorted(getattr(type(platform.config), "model_fields", {}).keys())
        if keys:
            add(
                "Settings keys (config.toml / Settings page)",
                ["Every configurable key, by name: " + ", ".join(keys)],
            )
    except Exception:  # noqa: BLE001
        log.exception("guide live catalog: settings keys unavailable")

    # Connected models.
    try:
        rows = platform.providers.health()
        lines = [
            f"- {r.get('provider')}: class={r.get('class')}, "
            f"{'available now' if r.get('available') else 'not available'}"
            for r in rows
        ]
        add("Model providers on this install (from /health)", lines)
    except Exception:  # noqa: BLE001
        log.exception("guide live catalog: providers unavailable")

    # Tools.
    try:
        specs = platform.registry.specs()
        lines = [f"- {s.get('name')}: {_first_line(s.get('description'))[:160]}" for s in specs]
        for i in range(0, len(lines), 30):
            add(f"Tools agents can call ({i // 30 + 1})", lines[i : i + 30])
    except Exception:  # noqa: BLE001
        log.exception("guide live catalog: tools unavailable")

    # Skills.
    try:
        skills = platform.skills.list()
        lines = [
            f"- {getattr(s, 'name', '')}: {_first_line(getattr(s, 'description', ''))[:160]}"
            for s in skills
        ]
        for i in range(0, len(lines), 30):
            add(f"Skills installed ({i // 30 + 1})", lines[i : i + 30])
    except Exception:  # noqa: BLE001
        log.exception("guide live catalog: skills unavailable")

    # Personas + agent types.
    try:
        from ..personas.builtins import BUILTIN_PERSONAS

        lines = [f"- {n}: {p.get('description', '')}" for n, p in BUILTIN_PERSONAS.items()]
        add("Built-in chat personas", lines)
    except Exception:  # noqa: BLE001
        log.exception("guide live catalog: personas unavailable")
    try:
        from ..agents.types import _DEFINITIONS

        lines = []
        for t, d in _DEFINITIONS.items():
            prompt = _first_line(getattr(d, "system_prompt", ""))
            lines.append(f"- {t.value}: {prompt[:160]}")
        add("Built-in agent types", lines)
    except Exception:  # noqa: BLE001
        log.exception("guide live catalog: agent types unavailable")

    # API routes, grouped by first path segment.
    if app is not None:
        try:
            groups: dict[str, list[str]] = {}
            for r in getattr(app, "routes", []):
                path = getattr(r, "path", "")
                methods = getattr(r, "methods", None)
                if not path or not methods:
                    continue
                seg = path.strip("/").split("/")[0] or "root"
                doc = _first_line(getattr(getattr(r, "endpoint", None), "__doc__", ""))
                for m in sorted(methods):
                    if m in ("HEAD", "OPTIONS"):
                        continue
                    groups.setdefault(seg, []).append(
                        f"- {m} {path}" + (f" — {doc[:140]}" if doc else "")
                    )
            for seg in sorted(groups):
                add(f"API routes: /{seg}", groups[seg])
        except Exception:  # noqa: BLE001
            log.exception("guide live catalog: routes unavailable")
    return out


# ------------------------------------------------------------------- the index


class GuideIndex:
    """Bundled docs + live catalogs, searchable. One per platform (see
    :func:`index_for`); the live half is rebuilt after :data:`LIVE_TTL_SECONDS`.
    """

    def __init__(self, platform=None, app=None) -> None:
        self.platform = platform
        self.app = app
        self.docs: list[Section] = []
        self.loaded: list[dict[str, Any]] = []
        self.missing: list[dict[str, Any]] = []
        self._live: list[Section] = []
        self._live_at = 0.0
        self.reload_docs()

    # -- loading -------------------------------------------------------------

    def reload_docs(self) -> None:
        self.docs, self.loaded, self.missing = [], [], []
        for slug, rel, title in BUNDLED_DOCS:
            path = doc_path(rel)
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                self.missing.append({"slug": slug, "file": Path(rel).name, "error": str(exc)})
                continue
            secs = split_markdown(slug, title, text)
            self.docs.extend(secs)
            self.loaded.append(
                {"slug": slug, "file": Path(rel).name, "title": title,
                 "sections": len(secs), "chars": len(text)}
            )

    def live(self, *, force: bool = False) -> list[Section]:
        if self.platform is None:
            return []
        now = time.monotonic()
        if force or not self._live or now - self._live_at > LIVE_TTL_SECONDS:
            try:
                self._live = live_sections(self.platform, self.app)
            except Exception:  # noqa: BLE001
                log.exception("guide live catalog failed; keeping the previous one")
            self._live_at = now
        return self._live

    def sections(self) -> list[Section]:
        return self.docs + self.live()

    # -- retrieval -----------------------------------------------------------

    def search(self, query: str, k: int = 8) -> list[tuple[float, Section]]:
        """BM25 over every section (stopwords dropped), plus a verbatim-phrase
        bonus and the per-document prior; ``[]`` for an empty query."""
        raw = tokens(query)
        q = [t for t in raw if t not in _STOP] or raw
        if not q:
            return []
        secs = self.sections()
        n = len(secs)
        if n == 0:
            return []
        avg = sum(s.length for s in secs) / n or 1.0
        df: dict[str, int] = {}
        for term in set(q):
            df[term] = sum(1 for s in secs if term in s.tf)
        phrases = [f"{a} {b}" for a, b in zip(q, q[1:]) if a != b]
        k1, b = 1.2, 0.75
        scored: list[tuple[float, Section]] = []
        for s in secs:
            score = 0.0
            for term in q:
                f = s.tf.get(term)
                if not f:
                    continue
                idf = math.log(1.0 + (n - df[term] + 0.5) / (df[term] + 0.5))
                score += idf * (f * (k1 + 1)) / (f + k1 * (1 - b + b * s.length / avg))
            if score <= 0.0:
                continue
            if phrases:
                flat = " ".join(tokens(s.heading) + tokens(s.text))
                score += _PHRASE_BONUS * sum(1 for p in phrases if p in flat)
            score *= _DOC_PRIOR.get(s.doc, 1.0)
            scored.append((score, s))
        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[:k]

    def overview(self) -> list[Section]:
        """What to show with no question: the Handbook's opening + the live
        version section."""
        out = [s for s in self.docs if s.doc == "handbook"][:2]
        out += [s for s in self.live() if s.heading.startswith("Version")]
        return out

    def ground(self, query: str, *, char_budget: int = DEFAULT_GROUND_CHARS, k: int = 8) -> str:
        """The block a Guide turn injects: retrieved sections, each labelled
        with its origin, under a header that states what it is and how to
        treat gaps. Never raises; ``""`` only when there is nothing at all."""
        try:
            hits = [s for _, s in self.search(query, k=k)] or self.overview()
        except Exception:  # noqa: BLE001
            log.exception("guide retrieval failed")
            hits = []
        header = [
            "# Iron Jarvis reference",
            "(Retrieved for this question from the app's own docs and live "
            "catalogs. Answer from it. When it does not cover the question, say "
            "so and point to where the answer lives — do not invent a page, "
            "setting, route, hotkey, or behaviour.)",
        ]
        if self.missing:
            names = ", ".join(m["file"] for m in self.missing)
            header.append(
                f"(Note: {len(self.missing)} reference file(s) are missing from this "
                f"install — {names} — so their topics may be absent below.)"
            )
        body = "\n".join(header)
        used = len(body)
        for s in hits:
            block = f"\n\n## [{s.label}]\n{s.text}"
            if used + len(block) > char_budget:
                remaining = char_budget - used
                if remaining > 400:
                    body += block[:remaining].rstrip() + "\n…(truncated)"
                break
            body += block
            used += len(block)
        return body if hits else (body + "\n\n(No reference material is available on this install.)")

    def base_knowledge(self, *, char_budget: int = 6000) -> str:
        """What the Guide AGENT starts every session knowing (v1.224.0): the
        Handbook's opening (what the app is, the three processes, hotkeys,
        where state lives) and its surfaces tour, the live version/install
        facts, and the list of live catalogs its tools can search. Everything
        else it looks up with guide_search / app_search — this block is the
        map, not the territory."""
        parts: list[Section] = [s for s in self.docs if s.doc == "handbook"][:4]
        live = self.live()
        parts += [s for s in live if s.heading.startswith("Version")]
        lines = [
            "# Iron Jarvis reference (base knowledge)",
            "(From the app's own Handbook and this install's live facts. For "
            "anything beyond it, call guide_search / guide_read; for the user's "
            "own things call app_search / app_status. Do not invent what is not "
            "here or in a tool result.)",
        ]
        if self.missing:
            names = ", ".join(m["file"] for m in self.missing)
            lines.append(f"(Note: reference file(s) missing from this install: {names}.)")
        body = "\n".join(lines)
        used = len(body)
        for s in parts:
            block = f"\n\n## [{s.label}]\n{s.text}"
            if used + len(block) > char_budget:
                break
            body += block
            used += len(block)
        heads = [s.heading for s in live]
        if heads:
            body += "\n\nLive catalogs guide_search can reach: " + "; ".join(heads)
        return body

    # -- status --------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        live = self.live()
        return {
            "persona": GUIDE_PERSONA,
            "docs": self.loaded,
            "missing": self.missing,
            "doc_sections": len(self.docs),
            "live_sections": len(live),
            "live_headings": [s.heading for s in live],
            "frozen": bool(getattr(sys, "frozen", False)),
            "docs_root": str(docs_root() or repo_root()),
        }


def index_for(platform, app=None) -> GuideIndex:
    """The one index per platform, built on first use and cached on it. ``app``
    (the FastAPI app) is remembered the first time it is offered so the route
    catalog can be generated later — by then every route is registered."""
    idx = getattr(platform, "_guide_index", None)
    if idx is None:
        idx = GuideIndex(platform, app)
        try:
            setattr(platform, "_guide_index", idx)
        except Exception:  # noqa: BLE001 — a frozen/slotted platform just rebuilds
            pass
    elif app is not None and idx.app is None:
        idx.app = app
    return idx


def ground(platform, query: str, *, app=None, char_budget: int = DEFAULT_GROUND_CHARS) -> str:
    return index_for(platform, app).ground(query, char_budget=char_budget)


def base_knowledge(platform, *, app=None, char_budget: int = 6000) -> str:
    return index_for(platform, app).base_knowledge(char_budget=char_budget)
