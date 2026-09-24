"""Claude Code session transcripts -> summaries + normalized EVENTs.

Where they live: ``<config>/projects/<project-slug>/<session-uuid>.jsonl``,
``<config>`` being ``CLAUDE_CONFIG_DIR`` when set, else ``~/.claude``. Only
the top-level ``<uuid>.jsonl`` files are sessions; ``<uuid>/subagents/**``
transcripts belong to their parent and are not listed on their own.

Record shapes (confirmed against agent-beacon's ``claudesession`` mapper and
real local files, structure only): one JSON object per line with ``type``
(``user`` / ``assistant`` / many bookkeeping types this reader skips),
``sessionId``, ``cwd``, ``timestamp``, ``isMeta`` and ``message`` whose
``content`` is a string or a list of blocks — ``text``, ``thinking``,
``tool_use {id, name, input}`` on the assistant side and
``tool_result {tool_use_id, content, is_error}`` on the user side. READ-ONLY.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from .common import clip, event, first_str, iso, result_text, s, title_of

HARNESS = "claude-code"

#: Byte needles counted across the whole file for the summary's ``tools``.
#: Claude Code writes compact JSON, and a needle inside a tool result's TEXT is
#: escaped (``\"type\"``), so it cannot be counted twice.
TOOL_NEEDLES = (b'"type":"tool_use"',)

#: Tool name (lower-cased) -> EVENT action. Anything not here is tool.called.
_COMMAND_TOOLS = {"bash", "powershell", "shell"}
_READ_TOOLS = {"read", "notebookread"}
#: Searches READ the files they match: the searched folder is the path.
_SEARCH_TOOLS = {"grep", "glob"}
_WRITE_TOOLS = {"write", "edit", "multiedit", "notebookedit"}
_NETWORK_TOOLS = {"webfetch", "websearch"}

#: A user string starting with one of these is harness bookkeeping (slash
#: command echo, local command output, background-task notices), not a prompt.
_NOT_A_PROMPT = (
    "<command-name>",
    "<command-message>",
    "<command-args>",
    "<local-command-caveat>",
    "<local-command-stdout>",
    "<local-command-stderr>",
    "<task-notification>",
    "<system-reminder>",
)


def root() -> Path:
    """The Claude Code config dir (``CLAUDE_CONFIG_DIR`` or ``~/.claude``)."""
    override = (os.environ.get("CLAUDE_CONFIG_DIR") or "").strip()
    if override:
        return Path(os.path.expanduser(override))
    return Path.home() / ".claude"


def discover() -> Iterator[Path]:
    """Every top-level session file under ``<root>/projects/*/``. Never raises."""
    projects = root() / "projects"
    try:
        project_dirs = [
            e for e in os.scandir(projects) if e.is_dir(follow_symlinks=False)
        ]
    except OSError:
        return
    for project in project_dirs:
        try:
            entries = list(os.scandir(project.path))
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.name.endswith(".jsonl") and entry.is_file(
                    follow_symlinks=False
                ):
                    yield Path(entry.path)
            except OSError:
                continue


def id_from_path(path: Path) -> str:
    return path.stem


_ABSOLUTE = re.compile(r"^([A-Za-z]:|[/\\~])")

#: Bound on subagent transcripts folded into one session.
MAX_SUBAGENT_FILES = 5000


def subagent_files(path: Path) -> list[Path]:
    """Every subagent transcript that belongs to the session file ``path``.

    They live under ``<project>/<session-uuid>/subagents/`` (workflow runs one
    level deeper, ``subagents/workflows/<run>/``). Sorted by their path below
    ``subagents/`` so the order is stable. Never raises.
    """
    base = path.parent / path.stem / "subagents"
    found: list[tuple[str, Path]] = []
    for dirpath, dirnames, filenames in os.walk(base):  # errors are ignored
        dirnames.sort()
        for name in filenames:
            if name.endswith(".jsonl"):
                full = Path(dirpath) / name
                found.append((full.relative_to(base).as_posix(), full))
                if len(found) >= MAX_SUBAGENT_FILES:
                    break
        if len(found) >= MAX_SUBAGENT_FILES:
            break
    return [p for _, p in sorted(found)]


def search_path(tool: str, args: dict) -> str:
    """The folder a Grep/Glob call reads.

    Grep: its ``path``. Glob: its ``path`` joined with the directory part of
    its ``pattern`` (``~/.ssh/*`` reads ``~/.ssh``); an absolute pattern
    directory stands alone. A wildcard directory part is dropped.
    """
    base = first_str(args, "path")
    if tool.lower() != "glob":
        return base
    pattern = first_str(args, "pattern")
    cut = max(pattern.rfind("/"), pattern.rfind("\\"))
    folder = pattern[:cut] if cut > 0 else ""
    if not folder or any(c in folder for c in "*?[{"):
        return base
    if not base or _ABSOLUTE.match(folder):
        return folder
    sep = "\\" if "\\" in base and "/" not in base else "/"
    return base.rstrip("/\\") + sep + folder


def _prompt_text(rec: dict) -> str:
    """The user's typed prompt in a ``user`` record, or ``""``."""
    if rec.get("type") != "user" or rec.get("isMeta"):
        return ""
    msg = rec.get("message")
    content = msg.get("content") if isinstance(msg, dict) else None
    if isinstance(content, str):
        return _clean(content)
    if isinstance(content, list):
        parts = [
            _clean(s(b.get("text")))
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        ]
        return "\n".join(p for p in parts if p).strip()
    return ""


def _clean(text: str) -> str:
    text = text.strip()
    if not text or text.startswith(_NOT_A_PROMPT):
        return ""
    return text


def summarize(
    head: Iterable[tuple[int, dict]], tail: Iterable[tuple[int, dict]], path: Path
) -> dict:
    """Summary fields readable from the head and tail records alone."""
    out: dict[str, Any] = {
        "id": "",
        "project": "",
        "title": "",
        "started": None,
        "ended": None,
    }
    named = ""
    for _, rec in head:
        if not out["id"]:
            out["id"] = s(rec.get("sessionId"))
        if not out["project"]:
            out["project"] = s(rec.get("cwd"))
        if out["started"] is None:
            out["started"] = iso(rec.get("timestamp"))
        if not out["title"]:
            prompt = _prompt_text(rec)
            if prompt:
                out["title"] = title_of(prompt)
        named = _name_of(rec) or named
        out["ended"] = iso(rec.get("timestamp")) or out["ended"]
    for _, rec in tail:
        out["ended"] = iso(rec.get("timestamp")) or out["ended"]
        named = _name_of(rec) or named
    if not out["title"] and named:
        out["title"] = title_of(named)
    if not out["project"]:
        out["project"] = path.parent.name
    return out


def _name_of(rec: dict) -> str:
    """A title Claude Code itself recorded (custom or AI-written), if any."""
    kind = rec.get("type")
    if kind == "custom-title":
        return first_str(rec, "customTitle", "title")
    if kind == "ai-title":
        return first_str(rec, "aiTitle", "title")
    return ""


def action_for(tool: str) -> str:
    """The EVENT action a Claude Code tool call maps to."""
    name = tool.lower()
    if name in _COMMAND_TOOLS:
        return "command.executed"
    if name in _READ_TOOLS or name in _SEARCH_TOOLS:
        return "file.read"
    if name in _WRITE_TOOLS:
        return "file.write"
    if name in _NETWORK_TOOLS:
        return "network.request"
    return "tool.called"


def map_events(
    records: Iterable[tuple[int, dict]], path: Path, session_id: str
) -> list[dict]:
    """The full normalized EVENT list for one transcript."""
    out: list[dict] = []
    pending: dict[str, dict] = {}
    ref_base = str(path)
    for line, rec in records:
        kind = rec.get("type")
        if kind not in ("user", "assistant"):
            continue
        ts = iso(rec.get("timestamp"))
        ref = f"{ref_base}:{line}"
        msg = rec.get("message")
        content = msg.get("content") if isinstance(msg, dict) else None
        if kind == "user":
            prompt = _prompt_text(rec)
            if prompt:
                out.append(
                    event(
                        "prompt.submitted",
                        session_id,
                        HARNESS,
                        ts=ts,
                        text=prompt,
                        ref=ref,
                    )
                )
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        _pair_result(block, pending, out, session_id, ts, ref)
            continue
        # assistant
        if not isinstance(content, list):
            continue
        texts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                text = s(block.get("text")).strip()
                if text:
                    texts.append(text)
            elif btype == "tool_use":
                ev = _tool_event(block, session_id, ts, ref)
                if ev is None:
                    continue
                if texts:  # what the model said BEFORE this call comes first
                    out.append(_response(session_id, ts, texts, ref))
                    texts = []
                out.append(ev)
                call_id = s(block.get("id"))
                if call_id:
                    pending[call_id] = ev
        if texts:
            out.append(_response(session_id, ts, texts, ref))
    return out


def _response(session_id: str, ts: str | None, texts: list[str], ref: str) -> dict:
    text = "\n".join(texts)
    return event("response.completed", session_id, HARNESS, ts=ts, text=text, ref=ref)


def _tool_event(block: dict, session_id: str, ts: str | None, ref: str) -> dict | None:
    name = s(block.get("name"))
    if not name:
        return None
    args = block.get("input") if isinstance(block.get("input"), dict) else {}
    action = action_for(name)
    fields: dict[str, Any] = {"ts": ts, "tool": name, "ref": ref}
    if action == "command.executed":
        fields["command"] = first_str(args, "command", "cmd")
    elif name.lower() in _SEARCH_TOOLS:
        fields["path"] = search_path(name, args)
    elif action in ("file.read", "file.write"):
        fields["path"] = first_str(
            args, "file_path", "notebook_path", "path", "filePath"
        )
    elif action == "network.request":
        fields["url"] = first_str(args, "url")
        if not fields["url"]:
            fields["command"] = first_str(args, "query")
    else:
        fields["path"] = first_str(args, "file_path", "path")
    return event(action, session_id, HARNESS, **fields)


def _pair_result(
    block: dict, pending: dict, out: list, session_id: str, ts: str | None, ref: str
) -> None:
    """Attach a tool result to its call; an orphan becomes a tool.called event."""
    text = result_text(block.get("content"))
    ok = not bool(block.get("is_error"))
    ev = pending.pop(s(block.get("tool_use_id")), None)
    if ev is not None:
        if text:
            ev["text"] = clip(text)
        ev["ok"] = ok
        return
    out.append(
        event("tool.called", session_id, HARNESS, ts=ts, text=text, ok=ok, ref=ref)
    )
