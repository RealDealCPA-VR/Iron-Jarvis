"""Built-in tools (§18). Workspace-scoped subset for the Phase 0–3 slice.

read_file / write_file / edit_file / list_files / grep operate strictly inside
the session workspace (§17 filesystem=workspace_only). shell is included but
defaults to permission ``ask`` and real isolation lands with the Sandbox Manager
(§16, Phase 4).
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

from .base import Reversibility, Tool, ToolContext, ToolResult, safe_path
from .undo import (
    make_file_descriptor,
    revert_workspace_file,
    sha256_bytes,
)


def _text_sha(content: str) -> str:
    """Newline-invariant hash of text content — matches ``sha256_target(mode=text)``."""
    return sha256_bytes(content.encode("utf-8"))


# --------------------------------------------------------------------------- #
# Bounded workspace walking (v1.153.1)
#
# `list_files` and `grep` each did `base.rglob("*")` — an UNBOUNDED recursive
# walk — INLINE on the daemon's single event loop. Pointed at a large folder,
# one call wedged the entire daemon: it kept listening on 8787 but answered
# nothing, so every request hung, the dashboard reported "Daemon offline", retry
# hung identically, and no threads would load. Observed live at 84% CPU with the
# MainThread parked in `pathlib.is_file` under `ListFilesTool.execute`.
#
# ShellTool three definitions below already carried the rule in a comment ("the
# tool runs on the daemon's single event loop — inline it would freeze ALL
# requests"); these two were simply never brought in line with it.
#
# Two independent defects, so two independent fixes: the walk is now BOUNDED
# (caps + a deadline + pruned heavy directories), and it is OFFLOADED to a
# thread so even a pathological tree can only ever slow down its own request.
# --------------------------------------------------------------------------- #

#: Directories never worth walking for a listing or a text search, and the usual
#: reason a workspace walk explodes.
_SKIP_DIRS = frozenset({
    ".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv",
    ".next", ".turbo", "dist", "build", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", ".gradle", "target", ".idea", ".vscode", "site-packages",
})

#: Hard ceilings. Every one of them is REPORTED when hit — a listing silently
#: truncated is worse than a slow one, because the model treats what it got as
#: the whole picture and confidently concludes a file does not exist.
_MAX_WALK_ENTRIES = 5000
_MAX_GREP_FILES = 2000
_MAX_GREP_HITS = 500
_MAX_GREP_FILE_BYTES = 2_000_000
_WALK_DEADLINE_S = 10.0


def _is_under(path: Path, root: Path) -> bool:
    """True when *path* really sits under *root*.

    ``relative_to`` raises for anything outside, and a walk that followed a
    junction could hand us such a path — a raised ValueError inside the listing
    comprehension would surface as a tool crash rather than a skipped entry.
    """
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _walk_files(base: Path, *, limit: int, deadline_s: float = _WALK_DEADLINE_S):
    """Yield files under *base*, pruned and bounded.

    Returns ``(paths, truncated_reason)``. Uses ``os.walk`` rather than
    ``rglob`` because only ``os.walk`` can PRUNE — dropping ``node_modules``
    after descending into it is the expensive half of the problem.
    """
    import os
    import time

    started = time.monotonic()
    out: list[Path] = []
    truncated = ""
    for root, dirs, files in os.walk(base, followlinks=False):
        dirs[:] = [dd for dd in dirs if dd not in _SKIP_DIRS and not dd.startswith(".")]
        for fn in files:
            out.append(Path(root) / fn)
            if len(out) >= limit:
                return out, f"stopped at {limit} files"
        if time.monotonic() - started > deadline_s:
            truncated = f"stopped after {deadline_s:.0f}s"
            break
    return out, truncated


#: Office formats that are not plain text. ``read_file`` DELEGATES these to the
#: document extractor rather than failing: the user's expectation is simply
#: "the app reads my documents", and making that depend on the model picking
#: the right tool is a trap it already fell into — a live report had the
#: assistant announce that .docx files were "blocked by the filter" (no such
#: filter exists; the documents extract perfectly) after read_file handed it a
#: bare UnicodeDecodeError. Doing the right thing beats explaining the wrong one.
_DOC_EXTENSIONS = {
    ".docx": "Word", ".doc": "Word", ".pdf": "PDF", ".xlsx": "Excel",
    ".xls": "Excel", ".pptx": "PowerPoint", ".ppt": "PowerPoint",
    ".odt": "OpenDocument", ".rtf": "Rich Text",
}


def _extract_document(path: Path, kind: str) -> ToolResult:
    """Serve a document through the extractor, LABELLED as extracted text.

    The label is not decoration. This is a lossy, read-only view of the file —
    round-tripping it through ``write_file`` would replace a real .docx with
    plain text and destroy the document, so the reply says so explicitly.
    """
    from ..documents import extract_text

    try:
        text = extract_text(str(path))
    except ValueError as exc:  # legacy/protected/oversized — a real, nameable no
        return ToolResult(
            ok=False, error=f"cannot read {path.name}: {exc}"
        )
    except Exception as exc:  # noqa: BLE001 — surface the cause, never a guess
        return ToolResult(
            ok=False, error=f"cannot read {path.name}: {type(exc).__name__}: {exc}"
        )
    note = (
        f"[extracted text from a {kind} document — read-only view; to change it "
        f"use write_document, never write_file]\n\n"
    )
    return ToolResult(
        ok=True,
        output=note + text,
        data={"bytes": len(text), "extracted": True, "format": kind},
    )


class ReadFileTool(Tool):
    name = "read_file"
    description = (
        "Read a file from the session workspace: UTF-8 text (code, .md, .txt, "
        ".json) directly, and Word/PDF/Excel/PowerPoint/RTF by extracting their "
        "text automatically. read_document does the same with page/sheet "
        "selection for large documents."
    )
    reversibility = Reversibility.READONLY  # a read has no side effect to undo
    input_schema = {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    }

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        path = safe_path(ctx.workspace, args["path"])
        if not path.is_file():
            return ToolResult(ok=False, error=f"no such file: {args['path']}")
        # A known office format goes straight to the extractor — the extension
        # is certain knowledge, so there is nothing to try and fail at first.
        kind = _DOC_EXTENSIONS.get(path.suffix.lower())
        if kind:
            return _extract_document(path, kind)
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            # Not text and not a known document extension: let the extractor
            # sniff it (it handles unknown-suffix files and images) rather than
            # returning a decode traceback nothing can act on.
            return _extract_document(path, "binary")
        except OSError as exc:  # permissions, a vanished file, a locked handle
            return ToolResult(ok=False, error=f"could not read {path.name}: {exc}")
        return ToolResult(ok=True, output=text, data={"bytes": len(text)})


class WriteFileTool(Tool):
    name = "write_file"
    description = "Create or overwrite a UTF-8 text file in the session workspace."
    reversibility = Reversibility.REVERSIBLE  # TX-01: prior bytes are captured
    input_schema = {
        "type": "object",
        "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
        "required": ["path", "content"],
    }

    async def capture_undo(
        self, args: dict[str, Any], ctx: ToolContext
    ) -> "dict[str, Any] | None":
        """Snapshot the inverse of the write: prior bytes when overwriting an
        existing file (``file_restore``), or a delete of the path we are about to
        CREATE (``file_delete``). ``post_sha256`` is the newline-invariant hash of
        the content we will write, so a later external edit is detected on undo."""
        try:
            target = safe_path(ctx.workspace, args["path"])
        except Exception:
            return None
        post = _text_sha(args["content"])
        if target.is_file():
            try:
                prior = target.read_text(encoding="utf-8").encode("utf-8")
                mode = "text"
            except (UnicodeDecodeError, OSError):
                prior = target.read_bytes()
                mode, post = "raw", None  # can't predict text-write bytes for binary
            return make_file_descriptor(
                ctx.config.home,
                kind="file_restore",
                path=args["path"],
                mode=mode,
                prior_bytes=prior,
                pre_sha256=sha256_bytes(prior),
                post_sha256=post,
            )
        return make_file_descriptor(
            ctx.config.home,
            kind="file_delete",
            path=args["path"],
            mode="text",
            post_sha256=post,
        )

    async def revert(self, undo: dict[str, Any], ctx: ToolContext) -> ToolResult:
        return await revert_workspace_file(undo, ctx)

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        path = safe_path(ctx.workspace, args["path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        content = args["content"]
        path.write_text(content, encoding="utf-8")
        return ToolResult(
            ok=True,
            output=f"wrote {len(content)} bytes to {args['path']}",
            data={"path": args["path"], "bytes": len(content)},
        )


class EditFileTool(Tool):
    name = "edit_file"
    description = "Replace the first occurrence of `old` with `new` in a workspace file."
    reversibility = Reversibility.REVERSIBLE  # TX-01: prior bytes are captured
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "old": {"type": "string"},
            "new": {"type": "string"},
        },
        "required": ["path", "old", "new"],
    }

    async def capture_undo(
        self, args: dict[str, Any], ctx: ToolContext
    ) -> "dict[str, Any] | None":
        """Snapshot the pre-edit text. ``post_sha256`` is the hash of the exact
        text ``execute`` will produce (first-occurrence replace), so a concurrent
        edit is caught on undo. No-op when the edit won't apply (file missing / old
        text absent) — nothing will change, so there is nothing to undo."""
        try:
            target = safe_path(ctx.workspace, args["path"])
        except Exception:
            return None
        if not target.is_file():
            return None
        try:
            text = target.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            return None
        if args["old"] not in text:
            return None
        new_text = text.replace(args["old"], args["new"], 1)
        return make_file_descriptor(
            ctx.config.home,
            kind="file_restore",
            path=args["path"],
            mode="text",
            prior_bytes=text.encode("utf-8"),
            pre_sha256=_text_sha(text),
            post_sha256=_text_sha(new_text),
        )

    async def revert(self, undo: dict[str, Any], ctx: ToolContext) -> ToolResult:
        return await revert_workspace_file(undo, ctx)

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        path = safe_path(ctx.workspace, args["path"])
        if not path.is_file():
            return ToolResult(ok=False, error=f"no such file: {args['path']}")
        text = path.read_text(encoding="utf-8")
        if args["old"] not in text:
            return ToolResult(ok=False, error="`old` text not found")
        path.write_text(text.replace(args["old"], args["new"], 1), encoding="utf-8")
        return ToolResult(ok=True, output=f"edited {args['path']}")


class ListFilesTool(Tool):
    name = "list_files"
    description = "List files under a workspace directory (default: workspace root)."
    reversibility = Reversibility.READONLY  # a listing has no side effect
    input_schema = {
        "type": "object",
        "properties": {"path": {"type": "string"}},
    }

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        import asyncio

        base = safe_path(ctx.workspace, args.get("path", "."))
        if not base.exists():
            return ToolResult(ok=False, error="no such directory")

        def _list():
            root = ctx.workspace.resolve()
            paths, truncated = _walk_files(base, limit=_MAX_WALK_ENTRIES)
            names = sorted(
                str(p.relative_to(root)).replace("\\", "/")
                for p in paths
                if _is_under(p, root)
            )
            return names, truncated

        # Offloaded for the same reason ShellTool is: this runs on the daemon's
        # single event loop, and inline it freezes every other request.
        entries, truncated = await asyncio.to_thread(_list)
        out = "\n".join(entries)
        if truncated:
            # Said OUT LOUD. A silently-truncated listing reads as complete, and
            # the model then reports that a file is not there.
            out += (
                f"\n\n[listing truncated — {truncated}. This is NOT the whole "
                f"directory; narrow `path` to see the rest.]"
            )
        return ToolResult(
            ok=True,
            output=out,
            data={"count": len(entries), "truncated": bool(truncated)},
        )


class GrepTool(Tool):
    name = "grep"
    description = "Regex-search workspace files; returns matching path:line entries."
    reversibility = Reversibility.READONLY  # a search has no side effect
    input_schema = {
        "type": "object",
        "properties": {"pattern": {"type": "string"}, "path": {"type": "string"}},
        "required": ["pattern"],
    }

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        import asyncio

        base = safe_path(ctx.workspace, args.get("path", "."))
        try:
            rx = re.compile(args["pattern"])
        except re.error as exc:
            return ToolResult(ok=False, error=f"bad regex: {exc}")
        def _search():
            import time

            root = ctx.workspace.resolve()
            started = time.monotonic()
            hits: list[str] = []
            if base.is_file():
                files, truncated = [base], ""
            else:
                files, truncated = _walk_files(base, limit=_MAX_GREP_FILES)
            for fp in files:
                if time.monotonic() - started > _WALK_DEADLINE_S:
                    truncated = truncated or f"stopped after {_WALK_DEADLINE_S:.0f}s"
                    break
                try:
                    # Skip anything too big to be worth scanning line-by-line;
                    # reading a 300MB log into memory on this path is what turns
                    # a slow search into an unresponsive app.
                    if fp.stat().st_size > _MAX_GREP_FILE_BYTES:
                        continue
                    for i, line in enumerate(
                        fp.read_text(encoding="utf-8").splitlines(), 1
                    ):
                        if rx.search(line):
                            rel = str(fp.relative_to(root)).replace("\\", "/")
                            hits.append(f"{rel}:{i}: {line.strip()}")
                            if len(hits) >= _MAX_GREP_HITS:
                                return hits, f"stopped at {_MAX_GREP_HITS} matches"
                except (UnicodeDecodeError, OSError, ValueError):
                    continue
            return hits, truncated

        # Offloaded: unlike the old inline version, a pathological tree can now
        # only ever slow down THIS request instead of the whole daemon.
        hits, truncated = await asyncio.to_thread(_search)
        out = "\n".join(hits)
        if truncated:
            out += f"\n\n[search truncated — {truncated}. Narrow `path` or the pattern.]"
        return ToolResult(
            ok=True,
            output=out,
            data={"matches": len(hits), "truncated": bool(truncated)},
        )


class ShellTool(Tool):
    name = "shell"
    description = "Run a shell command in the workspace. (Sandboxing arrives in Phase 4.)"
    permission_key = "shell"  # defaults to 'ask' — fail-closed in headless mode
    input_schema = {
        "type": "object",
        "properties": {"command": {"type": "string"}},
        "required": ["command"],
    }

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        import asyncio

        try:
            # Offload to a thread: subprocess.run blocks its OS thread for up to 60s,
            # and the tool runs on the daemon's single event loop — inline it would
            # freeze ALL requests, WS event delivery, and every other session.
            proc = await asyncio.to_thread(
                lambda: subprocess.run(
                    args["command"],
                    shell=True,
                    cwd=ctx.workspace,
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
            )
        except subprocess.TimeoutExpired:
            return ToolResult(ok=False, error="command timed out")
        out = proc.stdout + (("\n[stderr]\n" + proc.stderr) if proc.stderr else "")
        return ToolResult(
            ok=proc.returncode == 0,
            output=out.strip(),
            data={"returncode": proc.returncode},
            error=None if proc.returncode == 0 else f"exit {proc.returncode}",
        )


def default_registry():
    """Build a registry populated with the built-in tools."""
    from .registry import ToolRegistry

    registry = ToolRegistry()
    for tool_cls in (
        ReadFileTool,
        WriteFileTool,
        EditFileTool,
        ListFilesTool,
        GrepTool,
        ShellTool,
    ):
        registry.register(tool_cls())
    return registry
