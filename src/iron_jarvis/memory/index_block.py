"""Memory awareness index — "# What I can remember" (v1.141.0).

Chat and agents could *push* into memory and the fabric could *ground* a turn,
but nothing ever told the model WHAT memory exists — so a model that wasn't
handed a relevant snippet assumed ignorance instead of reaching for ``recall``.
:func:`memory_index_block` renders one compact, honest inventory block for the
system prompt:

* long-term bases (name + kind + item count **only when cheaply real** —
  a local markdown folder's file count is a glob; an MCP/Notion/SSH/cloud
  base is listed by name+kind ONLY, because counting it is a network call);
* memory-graph layer counts (session/project/user/org rows);
* the project's memory-base binding when a ``project_id`` is given
  (``Project.memory_sources``, read via the fabric's own accessor);
* up to a handful of recent note TITLES (title-only — never content) from
  the bound bases (all local bases when unbound);
* the size of the searchable conversation history (v1.142.0) — omitted when
  the index is empty or FTS5 is unavailable;
* a closing pointer at the ``recall`` tool.

House pattern (mirrors :mod:`..agents.roster`): read-only composition over
existing accessors, NEVER raises — even on a platform whose ``__getattr__``
blows up — bounded (≤ ~700 chars), ``""`` when there is nothing to say, and
no embedder or network calls ever. The block carries no leading blank lines;
callers join with ``"\\n\\n"`` (same contract as ``roster_block``).
"""

from __future__ import annotations

import time
from typing import Any

_HEADER = "# What I can remember"
#: "when available" is deliberate honesty (v1.141.0 review): agents carry
#: ``recall`` on every type now, and phone/desktop chat defaults to
#: auto_tools=True — but chat only ARMS recall when the memory sentence rule
#: fires (autoselect), and an auto_tools=False turn with nothing armed cannot
#: call it at all. The block cannot cheaply know its caller's tool list (the
#: signature is frozen), so the pointer is conditional instead of asserting a
#: tool the model may not have — an unconditional "search with recall" would
#: invite a model to claim a search it cannot run.
_CLOSING = (
    "Search these with the recall tool when available"
    " before assuming something isn't known."
)

#: Per-line clamps. Header (21) + bases (185) + graph (90) + project (110)
#: + titles (200) + closing (87) + 5 newlines = 698 chars worst case — the
#: whole block stays ≤ ~700 by construction.
_BASES_LINE_CHARS = 185
_GRAPH_LINE_CHARS = 90
_PROJECT_LINE_CHARS = 110
_TITLES_LINE_CHARS = 200
#: The hard block budget the worst case above was engineered against, now
#: ENFORCED rather than merely arithmetic — the history line (v1.142.0) is the
#: one line that yields when the budget is already spent (see
#: :func:`memory_index_block`). Every pre-existing line keeps its exact clamp.
_MAX_BLOCK_CHARS = 700
#: Caps inside the variable lines.
_MAX_BASES_LISTED = 8
_TITLE_CHARS = 40

#: Connector class name -> user-facing kind label. MRO-walked, so subclasses
#: (brain/Obsidian are both markdown folders) resolve without new wiring. An
#: unknown class renders NO kind (and, being unproven-local, no count).
_KIND_BY_CLASS: dict[str, str] = {
    "MarkdownBrainConnector": "markdown",
    "ObsidianConnector": "markdown",
    "MarkdownDirConnector": "markdown",
    "NotionConnector": "notion",
    "SSHBrainConnector": "ssh",
    "GoogleDriveConnector": "google_drive",
    "OneDriveConnector": "onedrive",
    "DropboxConnector": "dropbox",
    "HttpRagConnector": "http_rag",
    "McpBrainConnector": "mcp",
}


def _one_line(text: Any) -> str:
    """Collapse whitespace runs — a newline inside a user-authored note title
    must not escape the block's bullet list (same hygiene as the roster)."""
    return " ".join(str(text or "").split())


def _clamp(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _kind_of(conn: Any) -> str:
    try:
        for klass in type(conn).__mro__:
            kind = _KIND_BY_CLASS.get(klass.__name__)
            if kind:
                return kind
    except Exception:  # noqa: BLE001 — a weird metaclass costs the label only
        pass
    return ""


#: Folder-scan cache (v1.141.0 review). The naive version globbed + statted
#: every note on EVERY turn (chat and agents both inject): measured ~81ms per
#: call for a 2000-note vault and ~600ms for 10k notes on Windows. Entries are
#: keyed by (folder path, recursive) and live ``_SCAN_TTL_SECONDS`` — but a
#: change to the folder's own mtime busts the entry immediately, so a note
#: appended this turn (append writes a top-level file, which bumps the folder
#: mtime) is visible on the very next turn; only subfolder churn in a
#: recursive vault waits out the TTL. Values carry (path, mtime) pairs so
#: recent-title ordering never re-stats the world either. Plain dict on
#: purpose: worst concurrent-access case is a redundant re-scan.
_SCAN_TTL_SECONDS = 60.0
_SCAN_CACHE_MAX = 32
_scan_cache: "dict[tuple[str, bool], tuple[float, float, list[tuple[Any, float]]]]" = {}


def _local_notes(conn: Any) -> "list[tuple[Any, float]] | None":
    """``[(path, mtime)]`` for the connector's notes IFF it is a local
    markdown folder — the one connector family whose ``_files()`` is a pure
    local glob. Everything else (MCP, Notion, SSH, cloud drives, HTTP RAG)
    returns ``None``: counting or listing those is a network call, so the
    block must not try. ``None`` also covers a scan failure — even one raised
    MID-glob — no count is honest, a guessed count is not. Cached; see
    ``_scan_cache``."""
    try:
        from ..ltm.base import MarkdownDirConnector

        if not isinstance(conn, MarkdownDirConnector):
            return None
        key = (str(conn.dir), bool(getattr(conn, "recursive", True)))
        try:
            dir_mtime = float(conn.dir.stat().st_mtime)
        except Exception:  # noqa: BLE001 — folder unstattable: scan uncached
            dir_mtime = -1.0
        hit = _scan_cache.get(key)
        if (
            hit is not None
            and time.monotonic() - hit[0] <= _SCAN_TTL_SECONDS
            and hit[1] == dir_mtime >= 0.0
        ):
            return hit[2]
        entries: list[tuple[Any, float]] = []
        for path in conn._files():
            try:
                mtime = float(path.stat().st_mtime)
            except Exception:  # noqa: BLE001 — unstattable still lists (last)
                mtime = 0.0
            entries.append((path, mtime))
        if key not in _scan_cache and len(_scan_cache) >= _SCAN_CACHE_MAX:
            _scan_cache.pop(next(iter(_scan_cache)), None)
        _scan_cache[key] = (time.monotonic(), dir_mtime, entries)
        return entries
    except Exception:  # noqa: BLE001
        return None


def _connectors(platform: Any) -> list[Any]:
    try:
        ltm = getattr(platform, "ltm", None)
        if ltm is None:
            return []
        return [c for c in ltm.connectors() if getattr(c, "name", "")]
    except Exception:  # noqa: BLE001 — a poisoned ltm contributes nothing
        return []


def _bases_line(conns: list[Any], files_by_name: dict[str, list]) -> str:
    parts: list[str] = []
    for conn in conns[:_MAX_BASES_LISTED]:
        try:
            name = _one_line(getattr(conn, "name", ""))
            kind = _kind_of(conn)
            files = files_by_name.get(name)
            if files is not None:
                n = len(files)
                count = "empty" if n == 0 else f"{n} note" + ("s" if n != 1 else "")
                detail = f"{kind}, {count}" if kind else count
            else:
                detail = kind  # no cheap count — list by name (+kind) only
            parts.append(f"{name} ({detail})" if detail else name)
        except Exception:  # noqa: BLE001 — skip one bad connector, keep the rest
            continue
    if not parts:
        return ""
    more = len(conns) - _MAX_BASES_LISTED
    if more > 0:
        parts.append(f"+{more} more")
    return _clamp("- Long-term bases: " + "; ".join(parts), _BASES_LINE_CHARS)


def _graph_line(platform: Any) -> str:
    try:
        mem = getattr(platform, "memory", None)
        if mem is None:
            return ""
        layers = tuple(getattr(mem, "LAYERS", ("session", "project", "user", "org")))
        parts: list[str] = []
        for layer in layers:
            try:
                n = len(mem.list(layer))
            except Exception:  # noqa: BLE001 — a broken layer read costs its count
                continue
            if n > 0:
                parts.append(f"{n} {layer}")
        if not parts:
            return ""
        return _clamp("- Memory graph: " + ", ".join(parts) + " memories", _GRAPH_LINE_CHARS)
    except Exception:  # noqa: BLE001
        return ""


#: Index-size cache (v1.142.0), the same discipline ``_scan_cache`` exists for.
#: ``SearchIndex.stats()`` is a full ``COUNT(*)`` + ``COUNT(DISTINCT ...)`` over
#: ``searchdocrecord`` — free at 500 docs, tens of ms once a daily driver's
#: history is six figures — and this block is composed on EVERY chat and agent
#: turn. One entry per index (keyed by its engine, so two platforms in one test
#: run never share), living ``_SCAN_TTL_SECONDS``. A count up to a minute stale
#: is harmless here: the line is PROSE the model reads for "history exists and
#: is searchable", never a figure anything computes with.
#:
#: With ONE exception, and it is the folder-scan lesson applied: an EMPTY index
#: is never cached. Every other staleness only moves the number (9,412 vs
#: 9,417 — nobody can tell), but 0 → 1 moves the line's EXISTENCE, and it is
#: exactly the transition a brand-new install makes on the user's very first
#: exchange. Caching it would spend that first minute telling the model it has
#: no searchable history seconds after the user gave it some. The re-read is
#: free precisely because the table is empty — the expensive ``COUNT`` this
#: cache exists for is the one over a six-figure history, which by definition
#: is not this case.
_history_cache: "dict[str, tuple[float, str]]" = {}


def _history_line(platform: Any) -> str:
    """"- Past conversations: <n> indexed …" — the ONE line that tells the model
    its own history is searchable (v1.142.0).

    Honest by the same rule as the base counts: the number comes from
    ``SearchIndex.stats()`` (which never raises), and the line is OMITTED when
    the index holds nothing or FTS5 is unavailable — an inventory entry for an
    index that can't answer is worse than silence, because it would invite the
    model to promise a search it cannot run. TTL-cached — except for a healthy
    EMPTY index, which is re-read every turn so a fresh install notices its very
    first conversation immediately; see ``_history_cache``."""
    try:
        index = getattr(platform, "search_index", None)
        if index is None:
            return ""
        key = str(getattr(index, "engine", "") or id(index))
        now = time.monotonic()
        hit = _history_cache.get(key)
        if hit is not None and now - hit[0] <= _SCAN_TTL_SECONDS:
            return hit[1]
        stats = index.stats() or {}
        docs = int(stats.get("docs") or 0)
        available = bool(stats.get("available"))
        line = (
            f"- Past conversations: {docs} indexed (search with history_search)."
            if available and docs > 0
            else ""
        )
        if docs == 0 and available:
            # Healthy but empty: don't cache — see ``_history_cache``. A stale
            # NUMBER is invisible; a stale "there is no history" is a lie the
            # moment the first conversation lands.
            _history_cache.pop(key, None)
            return line
        if key not in _history_cache and len(_history_cache) >= _SCAN_CACHE_MAX:
            _history_cache.pop(next(iter(_history_cache)), None)
        _history_cache[key] = (now, line)
        return line
    except Exception:  # noqa: BLE001 — one line never takes the block down
        return ""


def _project_bases(platform: Any, project_id: "str | None") -> "list[str] | None":
    """The project's bound base names via the fabric's OWN accessor (never a
    re-implementation). ``None`` = unbound/unknown (search-everything)."""
    if not project_id:
        return None
    try:
        fabric = getattr(platform, "fabric", None)
        if fabric is None:
            from .fabric import MemoryFabric

            fabric = MemoryFabric.from_platform(platform)
        return fabric._project_bases(project_id)
    except Exception:  # noqa: BLE001
        return None


def _recent_titles(
    conns: list[Any],
    files_by_name: dict[str, list],
    bound: "list[str] | None",
    limit: int,
) -> list[str]:
    """Newest-first note titles from the bound bases (all local bases when
    unbound). Titles only — file stems — never content. Recency by mtime
    (already captured by the folder scan — no re-stat here); a base whose
    files can't be statted contributes what it can."""
    if limit <= 0:
        return []
    names = (
        [n for n in bound if n in files_by_name]
        if bound
        else [str(getattr(c, "name", "")) for c in conns]
    )
    stamped: list[tuple[float, str]] = []
    for name in names:
        for path, mtime in files_by_name.get(name) or []:
            try:
                title = _one_line(getattr(path, "stem", "")) or _one_line(path)
                if not title:
                    continue
                stamped.append((mtime, _clamp(title, _TITLE_CHARS)))
            except Exception:  # noqa: BLE001 — skip one bad file, keep the rest
                continue
    stamped.sort(key=lambda t: t[0], reverse=True)
    out: list[str] = []
    for _, title in stamped:
        if title not in out:
            out.append(title)
        if len(out) >= limit:
            break
    return out


def memory_index_block(
    platform: Any, *, project_id: "str | None" = None, limit_titles: int = 6
) -> str:
    """The compact "# What I can remember" prompt block, or ``""`` when there
    is nothing to say (no bases, no graph rows, no titles). NEVER raises."""
    try:
        conns = _connectors(platform)
        files_by_name: dict[str, list] = {}
        for conn in conns:
            files = _local_notes(conn)
            if files is not None:
                files_by_name[str(getattr(conn, "name", ""))] = files

        lines: list[str] = []
        bases = _bases_line(conns, files_by_name)
        if bases:
            lines.append(bases)
        graph = _graph_line(platform)
        if graph:
            lines.append(graph)
        bound = _project_bases(platform, project_id)
        if bound:
            lines.append(
                _clamp(
                    "- This project searches bases: "
                    + ", ".join(_one_line(n) for n in bound),
                    _PROJECT_LINE_CHARS,
                )
            )
        try:
            titles = _recent_titles(
                conns, files_by_name, bound, int(limit_titles or 0)
            )
        except Exception:  # noqa: BLE001 — titles are a bonus, never the block
            titles = []
        if titles:
            lines.append(
                _clamp("- Recent notes: " + "; ".join(titles), _TITLES_LINE_CHARS)
            )
        history = _history_line(platform)
        # "" when nothing: a project-binding note with no visible bases, graph
        # rows, titles, or indexed history would be an index of nothing — say
        # nothing instead.
        if not (bases or graph or titles or history):
            return ""
        block = "\n".join([_HEADER, *lines, _CLOSING])
        # The history line is LAST in priority as well as position: it joins
        # only if the block still fits the budget the other lines' clamps were
        # engineered around. That keeps ≤700 an invariant instead of leaving it
        # one worst-case-everything turn away from being false.
        if history and len(block) + 1 + len(history) <= _MAX_BLOCK_CHARS:
            lines.append(history)
            block = "\n".join([_HEADER, *lines, _CLOSING])
        return block
    except Exception:  # noqa: BLE001 — awareness must never take a caller down
        return ""
