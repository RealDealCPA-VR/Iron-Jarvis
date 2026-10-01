"""``notebook`` — a named agent reads and writes ITS OWN notebook (v1.297.0).

The notebook is ``NOTES.md`` in the agent's folder (``agents/files.py``). The
tool acts on the CALLING agent's notebook only: the slug comes from the
session row's ``agent_name`` (``custom:<slug>``), never from an argument, so
no agent can write into another's file. A builtin, a chat turn, or a session
with no custom agent gets a plain refusal.

PERMISSION TIER: ``allow`` (seeded in ``core/config.py`` beside
``assign_work`` and in ``platform.py``). It is the agent's own private file,
bounded (``NOTES_CAP_BYTES``) and confined (``AgentFiles.folder``) — strictly
weaker than the ``write_file`` the same session already holds, and a note the
agent could not take in a headless run would reproduce the "it forgot" the
notebook exists to end. NOT on any builtin roster: the runtime arms it for a
``custom:<slug>`` run (``AgentRuntime._run_body``) because the file is theirs.

Every write SAYS WHERE (the absolute path) — v1.153.2.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from ..tools.base import Reversibility, RiskClass, Tool, ToolContext, ToolResult

log = logging.getLogger("iron_jarvis.agents.notebook_tool")

NO_NOTEBOOK = "only a named agent has a notebook"
ACTIONS = ("append", "replace", "read")


def caller_agent_slug(engine, session_id: str) -> str:
    """The custom agent's name behind ``session_id`` (``custom:<name>`` on the
    Session row), or ``""`` for a builtin / chat / unknown session. Never
    raises — an unreadable caller has no notebook."""
    try:
        from ..core.db import session_scope
        from ..core.models import Session

        sid = str(session_id or "")
        if not sid or engine is None:
            return ""
        with session_scope(engine) as db:
            row = db.get(Session, sid)
        name = str(getattr(row, "agent_name", "") or "").strip() if row is not None else ""
        if not name.lower().startswith("custom:"):
            return ""
        return name.split(":", 1)[1].strip()
    except Exception:  # noqa: BLE001
        log.debug("notebook: caller read failed", exc_info=True)
        return ""


def stamp_line(text: str, now: datetime | None = None) -> str:
    """``- [YYYY-MM-DD] text`` — one dated bullet; inner newlines are kept as
    an indented continuation so a multi-line note stays one entry."""
    day = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d")
    body = str(text or "").strip()
    body = "\n  ".join(body.splitlines()) if "\n" in body else body
    return f"- [{day}] {body}"


class NotebookTool(Tool):
    name = "notebook"
    description = (
        "Your own private notebook (NOTES.md in your folder) — what you learned, "
        "what to remember next run, where things are. It is shown to you at the "
        "start of every run. Args: action = 'append' (add one dated line — the "
        "usual move), 'replace' (rewrite the whole notebook), or 'read'; "
        "text = the note (for append/replace)."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": list(ACTIONS)},
            "text": {"type": "string"},
        },
        "required": ["action"],
    }
    permission_key = "notebook"
    # A notebook write lands in the agent's own folder; the file is a private
    # note and never leaves the machine (v1.235.0 D12: declared for logging).
    risk_class = RiskClass.LOCAL_UI
    reversibility = Reversibility.IRREVERSIBLE

    def __init__(self, platform) -> None:
        self.platform = platform

    def _files(self):
        registry = getattr(self.platform, "agents_registry", None)
        return getattr(registry, "files", None) if registry is not None else None

    async def execute(self, args: dict[str, Any], ctx: ToolContext | None) -> ToolResult:
        action = str((args or {}).get("action") or "").strip().lower()
        text = str((args or {}).get("text") or "")
        if action not in ACTIONS:
            return ToolResult(
                ok=False, output="",
                error=f"action must be one of {', '.join(ACTIONS)}; got {action!r}",
            )
        files = self._files()
        if files is None:
            return ToolResult(ok=False, output="", error="agent folders are not available")
        engine = getattr(self.platform, "engine", None)
        session_id = str(getattr(ctx, "session_id", "") or "") if ctx is not None else ""
        slug = await asyncio.to_thread(caller_agent_slug, engine, session_id)
        if not slug:
            return ToolResult(ok=False, output="", error=NO_NOTEBOOK)
        registry = getattr(self.platform, "agents_registry", None)
        if registry is not None and registry.get(slug) is None:
            return ToolResult(ok=False, output="", error=NO_NOTEBOOK)
        try:
            if action == "read":
                notes = await asyncio.to_thread(files.read_notes, slug)
                path = str(files.folder(slug) / "NOTES.md")
                return ToolResult(
                    ok=True,
                    output=notes if notes.strip() else f"(your notebook is empty — {path})",
                    data={"path": path, "bytes": len(notes.encode("utf-8"))},
                )
            if action == "append":
                if not text.strip():
                    return ToolResult(ok=False, output="", error="text is required for append")
                line = stamp_line(text)
                path = await asyncio.to_thread(files.append_notes, slug, line)
                return ToolResult(
                    ok=True,
                    output=f"noted in {path}",
                    data={"path": path, "abs_path": path, "line": line},
                )
            if not text.strip():
                return ToolResult(
                    ok=False, output="",
                    error="text is required for replace (use append to add a line)",
                )
            path = await asyncio.to_thread(files.write_notes, slug, text)
            return ToolResult(
                ok=True,
                output=f"notebook replaced at {path}",
                data={"path": path, "abs_path": path},
            )
        except ValueError as exc:  # the confinement refusal
            return ToolResult(ok=False, output="", error=str(exc))
        except OSError as exc:
            return ToolResult(ok=False, output="", error=f"could not write the notebook: {exc}")
