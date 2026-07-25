"""Code Lab tools (v1.97.0) — agents reuse what they already built.

Until now the Code Lab was write-only from the agent's side: every ``run_code``
run was SAVED, but nothing could read it back, so an agent hitting the same
blocker next week wrote the same script from scratch. These three tools close
that loop, mirroring the skills library's search → load shape:

``code_search``  what did we already write for this? (read-only)
``code_load``    show me its source so I can reuse or adapt it (read-only)
``code_run``     run that exact script again (EXECUTES — gated like run_code)

The trust split matters and is deliberate. Search and load only read text, so
they are "allow" and safe for chat's auto-arming. ``code_run`` executes arbitrary
saved code — the same power as ``shell`` — so it stays "ask" and is never
auto-armed; the user arming it is the consent.

Honesty rule: a hit is prior art, not a guarantee. Every result carries its last
exit code, and a script that last FAILED says so plainly instead of being
presented as a working solution.
"""

from __future__ import annotations

from typing import Any

from ..tools.base import Reversibility, Tool, ToolContext, ToolResult
from .store import CodeArtifactStore

#: Source shown in a search hit. Enough to judge relevance without pasting
#: whole files into the context of a tool call the agent may not even want.
_PREVIEW_CHARS = 320


def _status(rec) -> str:
    if rec.last_exit_code is None:
        return "never run"
    return "worked (exit 0)" if rec.last_exit_code == 0 else f"FAILED (exit {rec.last_exit_code})"


class CodeSearchTool(Tool):
    """Find a previously-written script for this problem (v1.97.0)."""

    name = "code_search"
    reversibility = Reversibility.READONLY
    description = (
        "Search scripts YOU (or another agent) already wrote and ran, by what "
        "they were for. Call this BEFORE writing a new script with run_code — "
        "when a task is blocked or fiddly, someone may have already solved it, "
        "and reusing a proven script beats re-deriving one. Returns each match's "
        "id, purpose, language, and whether it last worked or failed."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "What you are trying to do, in plain words.",
            },
            "k": {"type": "integer", "description": "Max results (default 5)."},
        },
        "required": ["query"],
    }

    def __init__(self, store: CodeArtifactStore) -> None:
        self._store = store

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        query = str(args.get("query") or "").strip()
        if not query:
            return ToolResult(ok=False, error="query is required")
        k = int(args.get("k") or 5)
        hits = self._store.search(query, k)
        if not hits:
            return ToolResult(
                ok=True,
                output=(
                    "(no saved script matches — write one with run_code and pass "
                    "a clear `purpose`; it will be here next time)"
                ),
                data={"artifacts": []},
            )
        lines = [
            f"{r.id} [{r.language}] {_status(r)} — {r.description or '(no stated purpose)'}"
            for r in hits
        ]
        return ToolResult(
            ok=True,
            output="\n".join(lines),
            data={
                "artifacts": [
                    {
                        "id": r.id,
                        "name": r.name,
                        "language": r.language,
                        "purpose": r.description,
                        "last_exit_code": r.last_exit_code,
                        "run_count": r.run_count,
                        "preview": (r.source or "")[:_PREVIEW_CHARS],
                    }
                    for r in hits
                ]
            },
        )


class CodeLoadTool(Tool):
    """Read a saved script's full source (v1.97.0)."""

    name = "code_load"
    reversibility = Reversibility.READONLY
    description = (
        "Return a saved script's full source by id (from code_search). Read it "
        "to reuse the approach — run it unchanged with code_run, or adapt it "
        "into a new run_code call when this task differs."
    )
    input_schema = {
        "type": "object",
        "properties": {"id": {"type": "string"}},
        "required": ["id"],
    }

    def __init__(self, store: CodeArtifactStore) -> None:
        self._store = store

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        rec = self._store.get(str(args.get("id") or "").strip())
        if rec is None:
            return ToolResult(ok=False, error="no such code artifact")
        header = (
            f"{rec.name} [{rec.language}] — {_status(rec)}\n"
            f"purpose: {rec.description or '(none stated)'}\n"
        )
        return ToolResult(
            ok=True,
            output=header + "\n" + (rec.source or ""),
            data={
                "id": rec.id,
                "name": rec.name,
                "language": rec.language,
                "purpose": rec.description,
                "source": rec.source,
                "last_exit_code": rec.last_exit_code,
                "last_output": rec.last_output,
            },
        )


class CodeRunTool(Tool):
    """Re-run a saved script unchanged (v1.97.0)."""

    name = "code_run"
    #: A saved script can do anything its author's script could — same tier as
    #: run_code/shell, never auto-armed.
    reversibility = Reversibility.IRREVERSIBLE
    returns_untrusted_content = True  # its output may echo untrusted file text
    description = (
        "Run a saved script again, unchanged, by id. Use when code_search found "
        "one that already does exactly this job. It runs in its own durable "
        "folder (not this session's workspace), so files it produced before are "
        "still there. If the task differs even slightly, prefer code_load + a "
        "fresh run_code over forcing a re-run."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "timeout_s": {"type": "integer", "description": "Seconds (default 60, max 300)"},
        },
        "required": ["id"],
    }

    def __init__(self, store: CodeArtifactStore, codelab_dir) -> None:  # noqa: ANN001
        self._store = store
        self._dir = codelab_dir

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        from ..tools.runcode import ScriptRunFailed, execute_script

        rec = self._store.get(str(args.get("id") or "").strip())
        if rec is None:
            return ToolResult(ok=False, error="no such code artifact")
        try:
            rc, output = await execute_script(
                rec.language,
                rec.source,
                self._dir / rec.id,
                timeout_s=int(args.get("timeout_s") or 60),
            )
        except ScriptRunFailed as exc:
            self._store.record_run(rec.id, -1, f"[not run] {exc}")
            return ToolResult(ok=False, error=str(exc))
        self._store.record_run(rec.id, rc, output)
        return ToolResult(
            ok=rc == 0,
            output=f"exit {rc} · reran {rec.name}\n{output}".strip(),
            error=None if rc == 0 else f"saved script exited {rc}",
            data={"id": rec.id, "exit_code": rc, "language": rec.language},
        )


def code_tools(store: CodeArtifactStore, codelab_dir) -> list[Tool]:  # noqa: ANN001
    return [CodeSearchTool(store), CodeLoadTool(store), CodeRunTool(store, codelab_dir)]
