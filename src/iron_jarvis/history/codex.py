"""Codex CLI rollout files -> summaries + normalized EVENTs.

Where they live: ``<home>/sessions/YYYY/MM/DD/rollout-<ts>-<uuid>.jsonl``,
``<home>`` being ``CODEX_HOME`` when set, else ``~/.codex``.

Record shapes (confirmed against agent-beacon's ``codexsession`` mapper and
real local files, structure only): ``{timestamp, type, payload}`` where
``type`` is ``session_meta`` (payload ``id``, ``cwd``, ``timestamp``),
``turn_context`` (``cwd``), ``response_item`` (payload ``type`` ``message``
with ``role`` + ``content`` blocks, ``function_call {name, arguments (JSON
string), call_id}``, ``custom_tool_call {name, input (string), call_id}``,
``local_shell_call``, and their ``*_output {call_id, output}``), or
``event_msg`` / ``token_usage_record`` / others this reader skips (the
``event_msg`` user/agent messages duplicate the ``response_item`` ones).
READ-ONLY.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from .common import clip, event, first_str, iso, result_text, s, title_of

HARNESS = "codex"

#: Byte needles counted across the whole file for the summary's ``tools``
#: (the closing quote keeps ``function_call_output`` from matching).
TOOL_NEEDLES = (
    b'"type":"function_call"',
    b'"type":"custom_tool_call"',
    b'"type":"local_shell_call"',
)

_COMMAND_TOOLS = {
    "shell",
    "shell_command",
    "exec_command",
    "exec",
    "bash",
    "local_shell",
    "container.exec",
    "js",  # runs model-written code: its ``code`` is the command
}
_READ_TOOLS = {"read_file", "view_image"}
_PATCH_TOOLS = {"apply_patch"}
_NETWORK_TOOLS = {"web_search", "web_fetch", "fetch"}

#: Codex injects context as ``role=user`` messages (``<environment_context>``,
#: AGENTS.md instructions...). Those are not what the user typed.
_INJECTED = re.compile(r"^\s*(<[a-z_]+>|# AGENTS\.md instructions)")
_PATCH_HEADER = re.compile(r"^\*\*\* (Add|Update|Delete) File: (.+?)\s*$", re.M)
_WEEK_DIR = re.compile(r"^\d{2,4}$")
_UUID_TAIL = re.compile(
    r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})$"
)
_EXIT_CODE = re.compile(r"^\s*Exit code:\s*(-?\d+)")


def root() -> Path:
    """The Codex home (``CODEX_HOME`` or ``~/.codex``)."""
    override = (os.environ.get("CODEX_HOME") or "").strip()
    if override:
        return Path(os.path.expanduser(override))
    return Path.home() / ".codex"


def _subdirs(path: str) -> list[str]:
    try:
        return [
            e.path
            for e in os.scandir(path)
            if _WEEK_DIR.match(e.name) and e.is_dir(follow_symlinks=False)
        ]
    except OSError:
        return []


def discover() -> Iterator[Path]:
    """Every rollout under ``<root>/sessions/YYYY/MM/DD/``. Never raises."""
    base = str(root() / "sessions")
    for year in _subdirs(base):
        for month in _subdirs(year):
            for day in _subdirs(month):
                try:
                    entries = list(os.scandir(day))
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
    m = _UUID_TAIL.search(path.stem)
    return m.group(1) if m else path.stem


def _payload(rec: dict) -> dict:
    p = rec.get("payload")
    return p if isinstance(p, dict) else {}


def _message_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    parts = []
    for block in content:
        if isinstance(block, dict) and block.get("type") in (
            "input_text",
            "output_text",
            "text",
        ):
            text = s(block.get("text")).strip()
            if text:
                parts.append(text)
    return "\n".join(parts).strip()


def _user_prompt(payload: dict) -> str:
    """The typed prompt in a ``role=user`` message, or ``""`` if injected."""
    if payload.get("type") != "message" or payload.get("role") != "user":
        return ""
    text = _message_text(payload.get("content"))
    if not text or _INJECTED.match(text):
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
    for _, rec in head:
        payload = _payload(rec)
        kind = rec.get("type")
        if kind == "session_meta":
            out["id"] = out["id"] or first_str(payload, "id", "session_id")
            out["project"] = out["project"] or s(payload.get("cwd"))
            out["started"] = out["started"] or iso(payload.get("timestamp"))
        elif kind == "turn_context" and not out["project"]:
            out["project"] = s(payload.get("cwd"))
        elif kind == "response_item" and not out["title"]:
            prompt = _user_prompt(payload)
            if prompt:
                out["title"] = title_of(prompt)
        ts = iso(rec.get("timestamp"))
        out["started"] = out["started"] or ts
        out["ended"] = ts or out["ended"]
    for _, rec in tail:
        out["ended"] = iso(rec.get("timestamp")) or out["ended"]
    return out


def action_for(tool: str) -> str:
    """The EVENT action a Codex tool call maps to (patches: see _patch_events)."""
    name = tool.lower()
    if name in _COMMAND_TOOLS:
        return "command.executed"
    if name in _READ_TOOLS:
        return "file.read"
    if name in _PATCH_TOOLS:
        return "file.write"
    if name in _NETWORK_TOOLS:
        return "network.request"
    return "tool.called"


def _args(payload: dict) -> Any:
    """``function_call.arguments`` parsed (dict) or the raw ``input`` string."""
    raw = payload.get("arguments")
    if isinstance(raw, str) and raw.strip():
        try:
            return json.loads(raw)
        except ValueError:
            return raw
    if isinstance(raw, dict):
        return raw
    return payload.get("input")


def _command_of(args: Any) -> str:
    if isinstance(args, str):
        return args.strip()
    if isinstance(args, dict):
        for key in ("command", "cmd", "shell_command", "code"):
            value = args.get(key)
            if isinstance(value, str) and value.strip():
                return value
            if isinstance(value, list):
                joined = " ".join(v for v in value if isinstance(v, str)).strip()
                if joined:
                    return joined
    if isinstance(args, list):
        return " ".join(v for v in args if isinstance(v, str)).strip()
    return ""


def _tool_events(
    payload: dict, session_id: str, ts: str | None, ref: str
) -> list[dict]:
    kind = payload.get("type")
    if kind == "local_shell_call":
        action = (
            payload.get("action") if isinstance(payload.get("action"), dict) else {}
        )
        return [
            event(
                "command.executed",
                session_id,
                HARNESS,
                ts=ts,
                tool="local_shell",
                command=_command_of(action),
                ref=ref,
            )
        ]
    name = s(payload.get("name"))
    if not name:
        return []
    args = _args(payload)
    action = action_for(name)
    base: dict[str, Any] = {"ts": ts, "tool": name, "ref": ref}
    if action == "command.executed":
        return [event(action, session_id, HARNESS, command=_command_of(args), **base)]
    if action == "file.write":  # apply_patch: one event per file the patch names
        patch = args if isinstance(args, str) else first_str(args, "input", "patch")
        found = [
            event(
                "file.delete" if verb == "Delete" else "file.write",
                session_id,
                HARNESS,
                path=target,
                **base,
            )
            for verb, target in _PATCH_HEADER.findall(patch or "")
        ]
        return found or [event("file.write", session_id, HARNESS, **base)]
    if action == "file.read":
        return [
            event(
                action,
                session_id,
                HARNESS,
                path=first_str(args, "path", "file_path"),
                **base,
            )
        ]
    if action == "network.request":
        return [
            event(
                action,
                session_id,
                HARNESS,
                url=first_str(args, "url"),
                command=first_str(args, "query"),
                **base,
            )
        ]
    return [
        event(
            action,
            session_id,
            HARNESS,
            path=first_str(args, "path", "file_path"),
            **base,
        )
    ]


def _output_ok(payload: dict, text: str) -> tuple[str, bool | None]:
    """(clean output text, ok) from a ``*_output`` payload."""
    status = s(payload.get("status")).lower()
    ok: bool | None = None
    if status in ("failed", "failure", "error"):
        ok = False
    elif status in ("completed", "success"):
        ok = True
    stripped = text.strip()
    if stripped.startswith("{"):
        try:
            data = json.loads(stripped)
        except ValueError:
            data = None
        if isinstance(data, dict) and ("output" in data or "metadata" in data):
            meta = (
                data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
            )
            code = meta.get("exit_code")
            if isinstance(code, int) and not isinstance(code, bool):
                ok = code == 0
            text = s(data.get("output")) or text
    m = _EXIT_CODE.match(text)
    if m and ok is None:
        ok = int(m.group(1)) == 0
    return text, ok


def map_events(
    records: Iterable[tuple[int, dict]], path: Path, session_id: str
) -> list[dict]:
    """The full normalized EVENT list for one rollout."""
    out: list[dict] = []
    pending: dict[str, list[dict]] = {}
    ref_base = str(path)
    for line, rec in records:
        if rec.get("type") != "response_item":
            continue
        payload = _payload(rec)
        kind = payload.get("type")
        ts = iso(rec.get("timestamp"))
        ref = f"{ref_base}:{line}"
        if kind == "message":
            role = payload.get("role")
            if role == "user":
                prompt = _user_prompt(payload)
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
            elif role == "assistant":
                text = _message_text(payload.get("content"))
                if text:
                    out.append(
                        event(
                            "response.completed",
                            session_id,
                            HARNESS,
                            ts=ts,
                            text=text,
                            ref=ref,
                        )
                    )
        elif kind in ("function_call", "custom_tool_call", "local_shell_call"):
            evs = _tool_events(payload, session_id, ts, ref)
            out.extend(evs)
            call_id = first_str(payload, "call_id", "id")
            if call_id and evs:
                pending[call_id] = evs
        elif kind in (
            "function_call_output",
            "custom_tool_call_output",
            "local_shell_call_output",
        ):
            text, ok = _output_ok(payload, result_text(payload.get("output")))
            evs = pending.pop(s(payload.get("call_id")), None)
            if evs:
                for ev in evs:
                    if text:
                        ev["text"] = clip(text)
                    ev["ok"] = ok
            else:
                out.append(
                    event(
                        "tool.called",
                        session_id,
                        HARNESS,
                        ts=ts,
                        text=text,
                        ok=ok,
                        ref=ref,
                    )
                )
    return out
