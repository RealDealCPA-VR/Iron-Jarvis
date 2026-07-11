"""Agent-facing long-term-memory tools (§19 tool interface).

Two thin tools over :class:`LongTermMemory`, each constructed with the manager
injected:

* ``ltm_search`` — search one source or merge across all connectors.
* ``ltm_append`` — append a note/page (defaults to the built-in ``brain`` store).

``ltm_tools(manager)`` builds the pair for registration in the tool registry.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..tools.base import Reversibility, Tool, ToolContext, ToolResult
from ..tools.undo import (
    RevertConflict,
    delete_preimage,
    guard_unchanged,
    make_file_descriptor,
    read_envelope,
    resolve_prior_bytes,
    sha256_bytes,
    sha256_target,
)
from .base import slugify
from .manager import LongTermMemory


class LTMSearchTool(Tool):
    """Search long-term memory connectors (Obsidian / brain / Notion)."""

    name = "ltm_search"
    reversibility = Reversibility.READONLY  # a search has no side effect
    description = (
        "Search long-term memory (external knowledge stores: Obsidian vault, "
        "markdown brain, Notion). Omit `source` to merge across all connectors."
    )
    permission_key = "ltm_search"
    input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "k": {"type": "integer", "minimum": 1},
            "source": {"type": "string"},
        },
        "required": ["query"],
    }

    def __init__(self, manager: LongTermMemory) -> None:
        self.manager = manager

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        k = int(args.get("k", 5))
        source = args.get("source")
        try:
            hits = self.manager.search(args["query"], k=k, source=source)
        except Exception as exc:  # incl. a real embedder failing on a named source
            # Never raise into the agent loop: a flaky embedder / store degrades
            # to "no results", it does not crash the session.
            return ToolResult(ok=False, error=str(exc))
        output = "\n".join(
            f"[{h['source']}] {h['title']}: {h['snippet']}" for h in hits
        )
        return ToolResult(
            ok=True, output=output, data={"results": hits, "count": len(hits)}
        )


class LTMAppendTool(Tool):
    """Append a note/page to a long-term memory store."""

    name = "ltm_append"
    reversibility = Reversibility.REVERSIBLE  # TX-01: markdown-store appends undo
    description = (
        "Append a titled note to a long-term memory store. Defaults to the "
        "built-in `brain`; pass `source` to target Obsidian or Notion."
    )
    permission_key = "ltm_append"
    input_schema = {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "content": {"type": "string"},
            "source": {"type": "string"},
        },
        "required": ["title", "content"],
    }

    def __init__(self, manager: LongTermMemory) -> None:
        self.manager = manager

    def _resolve_source(self, args: dict[str, Any]) -> str | None:
        return args.get("source") or self.manager.default_source()

    async def capture_undo(
        self, args: dict[str, Any], ctx: ToolContext
    ) -> "dict[str, Any] | None":
        """Snapshot the inverse of the append.

        For a filesystem markdown store (brain / Obsidian) the target note is
        ``<dir>/<slug(title)>.md``: capture its prior bytes (``memory_restore``) or
        record that it will be CREATED (``memory_delete_file``), predicting the
        post-append content so a later edit is caught on undo. Connectors with no
        local file (Notion / cloud / SSH / HTTP-RAG) have no safe delete primitive,
        so the inverse is journaled ``reversible=False`` — an HONEST 'cannot undo'
        rather than a fake one."""
        source = self._resolve_source(args)
        conn = self.manager.get(source) if source else None
        directory = getattr(conn, "dir", None) if conn is not None else None
        if directory is None:
            # No local file to restore/delete — be honest, not fake.
            return {"kind": "memory_external", "reversible": False, "tool": self.name}
        path = Path(directory) / f"{slugify(args['title'])}.md"
        content = args["content"]
        if path.is_file():
            try:
                prior = path.read_text(encoding="utf-8").encode("utf-8")
            except (UnicodeDecodeError, OSError):
                return {"kind": "memory_external", "reversible": False, "tool": self.name}
            # Mirror MarkdownDirConnector.append's body construction so post_sha256
            # matches the file the append will leave behind.
            post_body = f"{prior.decode('utf-8').rstrip()}\n\n{content.rstrip()}\n"
            return make_file_descriptor(
                ctx.config.home,
                kind="memory_restore",
                path=str(path),
                mode="text",
                prior_bytes=prior,
                pre_sha256=sha256_bytes(prior),
                post_sha256=sha256_bytes(post_body.encode("utf-8")),
            )
        post_body = f"# {args['title']}\n\n{content.rstrip()}\n"
        return make_file_descriptor(
            ctx.config.home,
            kind="memory_delete_file",
            path=str(path),
            mode="text",
            post_sha256=sha256_bytes(post_body.encode("utf-8")),
        )

    async def revert(self, undo: dict[str, Any], ctx: ToolContext) -> ToolResult:
        """Undo a markdown-store append: refuse on drift, then restore the prior
        note bytes or delete the note we created. The note lives in the trusted LTM
        store (not the session workspace), so it is addressed by its absolute path."""
        meta = read_envelope(undo)
        raw = meta.get("path")
        if not raw:
            return ToolResult(ok=False, error="undo: no memory target recorded")
        path = Path(raw)
        home = ctx.config.home
        conflict = guard_unchanged(sha256_target(path, "text"), undo.get("post_sha256"))
        if conflict is not None:
            raise RevertConflict(conflict)
        kind = undo.get("kind")
        if kind == "memory_delete_file":
            try:
                if path.exists():
                    path.unlink()
            except OSError as exc:
                return ToolResult(ok=False, error=f"undo: could not remove note: {exc}")
            delete_preimage(home, undo.get("pre_ref"))
            return ToolResult(ok=True, output=f"undo: removed memory note {path.name}")
        if kind == "memory_restore":
            prior = resolve_prior_bytes(home, undo, meta)
            if prior is None:
                return ToolResult(ok=False, error="undo: pre-image unavailable")
            try:
                path.write_text(prior.decode("utf-8"), encoding="utf-8")
            except OSError as exc:
                return ToolResult(ok=False, error=f"undo: could not restore note: {exc}")
            delete_preimage(home, undo.get("pre_ref"))
            return ToolResult(ok=True, output=f"undo: restored memory note {path.name}")
        return ToolResult(ok=False, error=f"undo: unknown memory undo kind {kind!r}")

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        source = args.get("source") or self.manager.default_source()
        if source is None:
            return ToolResult(ok=False, error="no LTM connector registered")
        try:
            ref = self.manager.append(args["title"], args["content"], source)
        except ValueError as exc:
            return ToolResult(ok=False, error=str(exc))
        return ToolResult(
            ok=True,
            output=f"appended to {source}: {ref}",
            data={"ref": ref, "source": source},
        )


def ltm_tools(manager: LongTermMemory) -> list[Tool]:
    """Build the LTM tool pair bound to a single ``LongTermMemory`` instance."""
    return [LTMSearchTool(manager), LTMAppendTool(manager)]
