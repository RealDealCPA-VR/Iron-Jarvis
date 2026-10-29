"""Which tool of an MCP-served memory base does what (v1.408.0).

v1.406.0 picked a server's note tools by the WORDS in their names. Measured
against the user's real wiki server (hermes-brain) that went wrong three ways:
"save a note" picked ``create_page``, whose required ``type`` and ``tags``
nothing could fill (the server refused, and the save was filed as "the base is
unavailable"); "add to a note" picked ``append_section`` without the
``heading`` it requires; and ``update_page`` would have had the text put in its
``note`` field (a log comment). A name is a hint, not a contract.

So a ROLE is now an explicit mapping the USER confirms:
``{"tool": "<name>", "args": {"<param>": <source>}}`` where a source is one of
:data:`SOURCES` (the value Iron Jarvis fills in) or ``{"const": "<text>"}`` (a
fixed value the user typed — ``type: concept``, ``tags: inbox``). The app
PROPOSES a mapping from the server's own schemas (:func:`propose`); nothing is
used to save, add to or change a note until the user confirms it
(``LTMSourceRecord.roles_json``). Searching and reading keep working before
that through the read-only safe pick, because they cannot change anything.

Pure functions over the tool dicts a ``tools/list`` returns — no I/O.
"""

from __future__ import annotations

import json
import re
from typing import Any

ROLES = ("search", "read", "create", "append", "archive", "delete", "rename", "move")
SOURCES = ("query", "ref", "title", "content", "to")
#: Roles that only read: their tool must never carry a write-like word.
READ_ROLES = frozenset({"search", "read"})
#: The values each role must hand the server, or it cannot do its job.
ROLE_NEEDS: dict[str, frozenset[str]] = {
    "search": frozenset({"query"}),
    "read": frozenset({"ref"}),
    "create": frozenset({"content"}),
    "append": frozenset({"ref", "content"}),
    "archive": frozenset({"ref"}),
    "delete": frozenset({"ref"}),
    "rename": frozenset({"ref", "to"}),
    "move": frozenset({"ref", "to"}),
}
WORDS = {
    "search": "Find notes",
    "read": "Read one note",
    "create": "Save a new note",
    "append": "Add to an existing note",
    "archive": "Archive a note",
    "delete": "Delete a note",
    "rename": "Rename a note",
    "move": "Move a note",
}

_NAME_WORD_RX = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+")
_WRITE_WORDS = frozenset({
    "write", "update", "delete", "remove", "replace", "insert", "create", "set",
    "put", "patch", "upsert", "drop", "exec", "execute", "run", "sql", "mutate",
    "append", "save", "archive", "move", "rename", "trash", "add", "new",
    # v1.408.0 review: verbs that change a note the first list missed
    # (``edit_page`` could be confirmed as the reader).
    "edit", "modify", "change", "clear", "purge", "reset", "merge", "overwrite",
    "import", "upload", "sync", "publish", "restore", "unarchive",
    "store", "ingest", "send", "duplicate",
})


def writes(tool: "dict[str, Any] | None", name: str = "") -> bool:
    """Does this tool change anything, by its NAME's words or by what its MCP
    annotations SAY (``readOnlyHint: false`` / ``destructiveHint: true``)? A
    reader must answer False here."""
    tool = tool if isinstance(tool, dict) else {}
    if words(name or str(tool.get("name") or "")) & _WRITE_WORDS:
        return True
    ann = tool.get("annotations")
    return isinstance(ann, dict) and (
        ann.get("readOnlyHint") is False or ann.get("destructiveHint") is True
    )
_NOTE_NOUNS = frozenset({"note", "notes", "page", "pages", "doc", "docs", "document",
                         "entry", "memory", "section", "wiki", "file"})
_VERBS: dict[str, tuple[str, ...]] = {
    "search": ("search", "recall", "retrieve", "lookup", "query", "find"),
    "read": ("read", "get", "fetch", "open", "view"),
    "create": ("create", "new", "add", "save", "write", "store", "ingest"),
    "append": ("append", "add", "update"),
    "archive": ("archive", "trash"),
    "delete": ("delete", "remove", "trash"),
    "rename": ("rename",),
    "move": ("move",),
}
_REF_KEYS = ("ref", "id", "page_id", "pageId", "note_id", "path", "slug", "url", "uri")
_QUERY_KEYS = ("query", "q", "search", "keywords", "text", "prompt", "input")
_TITLE_KEYS = ("title", "name", "subject", "heading", "summary", "filename")
_CONTENT_KEYS = ("content", "body", "text", "markdown", "data")
_APPEND_KEYS = ("append_text", "append", "content", "text", "body", "markdown")
_TO_KEYS = ("to", "new_title", "new_name", "new_path", "destination", "target")
#: A param a role must never write the note's text into (a log comment).
_NOT_CONTENT = frozenset({"note", "reason", "comment", "message", "summary"})


def words(name: str) -> set[str]:
    return {w.lower() for w in _NAME_WORD_RX.findall(str(name or ""))}


def params(tool: dict[str, Any]) -> list[dict[str, Any]]:
    schema = tool.get("inputSchema") or tool.get("input_schema") or {}
    props = schema.get("properties") or {}
    required = set(schema.get("required") or [])
    out = []
    for name, spec in props.items():
        t = spec.get("type") if isinstance(spec, dict) else ""
        if isinstance(t, list):
            t = next((x for x in t if x != "null"), "")
        out.append({"name": str(name), "required": name in required, "type": str(t or "")})
    return out


def tool_view(tool: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": str(tool.get("name") or ""),
        "description": str(tool.get("description") or "")[:300],
        "params": params(tool),
    }


def _first(keys: tuple[str, ...], have: list[str], taken: set[str]) -> "str | None":
    for k in keys:
        if k in have and k not in taken:
            return k
    return None


def _propose_one(role: str, tool: dict[str, Any]) -> dict[str, Any]:
    have = [p["name"] for p in params(tool)]
    args: dict[str, Any] = {}
    taken: set[str] = set()

    def put(keys: tuple[str, ...], source: str) -> None:
        k = _first(keys, have, taken)
        if k is not None:
            args[k] = source
            taken.add(k)

    if role == "search":
        put(_QUERY_KEYS, "query")
    elif role == "read":
        put(_REF_KEYS + ("title", "name"), "ref")
    elif role == "create":
        put(_CONTENT_KEYS, "content")
        put(_TITLE_KEYS, "title")
    elif role == "append":
        put(_REF_KEYS + ("title", "name"), "ref")
        put(_APPEND_KEYS, "content")
        put(("heading", "section", "section_title", "title"), "title")
    else:
        put(_REF_KEYS + ("title", "name"), "ref")
        if role in ("rename", "move"):
            put(_TO_KEYS, "to")
    return {"tool": str(tool.get("name") or ""), "args": args}


def propose(tools: list[dict[str, Any]]) -> dict[str, "dict[str, Any] | None"]:
    """The app's best guess per role — a SUGGESTION for the user to confirm,
    never used to write by itself. Ranked by the role's verbs in order, then
    a note noun in the name, then the server's order."""
    out: dict[str, "dict[str, Any] | None"] = {}
    for role in ROLES:
        best: "tuple[tuple[int, int, int], dict[str, Any]] | None" = None
        for i, t in enumerate(tools or []):
            if not isinstance(t, dict):
                continue
            w = words(str(t.get("name", "")))
            if role in READ_ROLES and writes(t):
                continue
            if role == "create" and w & {"search", "find", "query", "read", "get"}:
                continue
            hit = next((n for n, v in enumerate(_VERBS[role]) if v in w), None)
            if hit is None:
                continue
            rank = (hit, 0 if w & _NOTE_NOUNS else 1, i)
            if best is None or rank < best[0]:
                best = (rank, t)
        out[role] = _propose_one(role, best[1]) if best is not None else None
    return out


def _source_ok(src: Any) -> bool:
    if isinstance(src, str):
        return src in SOURCES
    return isinstance(src, dict) and set(src) == {"const"} and isinstance(src["const"], str)


def problems(role: str, mapping: "dict[str, Any] | None", tools: list[dict[str, Any]]) -> list[str]:
    """Plain sentences; [] = this mapping can be used."""
    if mapping is None:
        return []
    if not isinstance(mapping, dict) or not isinstance(mapping.get("tool"), str):
        return ["Choose a tool."]
    by_name = {str(t.get("name")): t for t in tools or [] if isinstance(t, dict)}
    tool = by_name.get(mapping["tool"])
    if tool is None:
        return [f"The server has no tool called {mapping['tool']!r}."]
    out: list[str] = []
    if role in READ_ROLES and writes(tool, mapping["tool"]):
        out.append(f"{mapping['tool']} looks like it changes notes, so it is never used to {WORDS[role].lower()}.")
    args = mapping.get("args") or {}
    if not isinstance(args, dict):
        return out + ["The tool's values are not set."]
    have = {p["name"]: p for p in params(tool)}
    for param, src in args.items():
        if param not in have:
            out.append(f"{mapping['tool']} has no setting called {param!r}.")
        elif not _source_ok(src):
            out.append(f"Choose where {param!r} comes from.")
        elif isinstance(src, str) and src == "content" and param in _NOT_CONTENT and role in ("create", "append"):
            out.append(f"{param!r} looks like a comment field, not the note's text.")
    for p in have.values():
        if p["required"] and p["name"] not in args:
            out.append(f"{p['name']!r} is required — choose a value for it (a fixed value if nothing else fits).")
    used = {s for s in args.values() if isinstance(s, str)}
    missing = ROLE_NEEDS[role] - used
    if missing:
        names = ", ".join(sorted(missing))
        out.append(f"To {WORDS[role].lower()}, the tool must be given: {names}.")
    return out


def clean(roles: Any, tools: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Validate a whole set for saving; raises ValueError with ONE sentence."""
    if not isinstance(roles, dict):
        raise ValueError("Send the tools as a set of roles.")
    out: dict[str, dict[str, Any]] = {}
    for role, mapping in roles.items():
        if role not in ROLES:
            raise ValueError(f"There is no role called {role!r}.")
        if mapping is None:
            continue
        bad = problems(role, mapping, tools)
        if bad:
            raise ValueError(f"{WORDS[role]}: {bad[0]}")
        out[role] = {"tool": mapping["tool"], "args": dict(mapping.get("args") or {})}
    return out


def build_args(mapping: dict[str, Any], tool: "dict[str, Any] | None", values: dict[str, str]) -> dict[str, Any]:
    """The arguments for one call: each source filled from ``values`` (a
    source with no value is left out), a fixed value as typed — split on
    commas for a list-typed param (``tags: inbox, notes``)."""
    types = {p["name"]: p["type"] for p in params(tool or {})}
    out: dict[str, Any] = {}
    for param, src in (mapping.get("args") or {}).items():
        if isinstance(src, dict):
            v = str(src.get("const", ""))
            if types.get(param) == "array":
                out[param] = [x.strip() for x in v.split(",") if x.strip()]
            elif types.get(param) in ("integer", "number"):
                try:
                    out[param] = int(v) if types.get(param) == "integer" else float(v)
                except ValueError:
                    out[param] = v
            elif types.get(param) == "boolean":
                out[param] = v.strip().lower() in ("1", "true", "yes", "on")
            else:
                out[param] = v
        elif src in values and values[src] != "":
            out[param] = values[src]
    return out


def decode(raw: Any) -> "dict[str, dict[str, Any]] | None":
    """``LTMSourceRecord.roles_json`` → the confirmed roles, or None."""
    if not raw:
        return None
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return {r: m for r, m in data.items() if r in ROLES and isinstance(m, dict) and m.get("tool")}
