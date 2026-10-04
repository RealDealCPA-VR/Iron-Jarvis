"""A HANDOFF of a Claude Code conversation, for a fresh start (v1.303.2).

When the next account cannot continue a carried conversation
(``resume_failed``), the pane starts a NEW Claude Code conversation on that
account and points it at a short markdown summary of where the old one
stopped. Deterministic and local — NO model call: it is built from the
conversation file itself.

What it holds (each capped, the whole <= :data:`TOTAL_CAP` characters):

* the FIRST thing the user asked (<= :data:`FIRST_CAP`);
* the last :data:`LAST_TURNS` user/assistant TEXT turns (each <=
  :data:`TURN_CAP`) — tool calls and tool RESULTS are never copied (a tool
  result can be a whole file, a secret, a binary);
* the distinct file paths the conversation's tools touched (``file_path`` /
  ``path`` / ``notebook_path`` of a ``tool_use``), the most recent
  :data:`PATHS_CAP`.

Secrets are MASKED (``detections.redact.mask``) before anything is written.
It is written under Iron Jarvis's OWN home
(``<home>/handoffs/<id>/handoff.md``, one folder per conversation), never
into the user's project; folders older than 30 days are pruned. Reading the
file is BLOCKING; a very large conversation is read as its head (the first
message) and its tail (the rest).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Mapping

from ..detections.redact import mask

__all__ = ["build_handoff", "write_handoff", "at_shell_prompt", "HANDOFF_PROMPT"]

FIRST_CAP = 1500
TURN_CAP = 800
LAST_TURNS = 6
PATHS_CAP = 30
TOTAL_CAP = 8000

#: A conversation file bigger than this is read as head + tail.
_WHOLE_MAX = 32 * 1024 * 1024
_HEAD_BYTES = 2 * 1024 * 1024
_TAIL_BYTES = 24 * 1024 * 1024

#: What the fresh Claude Code is asked to do (``{path}`` = the handoff file).
HANDOFF_PROMPT = "Read the handoff at {path} and continue that work from where it stopped."

_PATH_KEYS = ("file_path", "path", "notebook_path")

#: Text Claude Code writes into the transcript itself, not the user: slash
#: command echoes, local command output, reminders, interrupt markers.
_NOT_SAID = re.compile(
    r"^\s*(?:<(?:command-name|command-message|command-args|local-command-stdout|local-command-stderr"
    r"|system-reminder|bash-input|bash-stdout|bash-stderr)>|\[Request interrupted)",
    re.I,
)


def _lines(path: Path) -> Iterator[bytes]:
    size = path.stat().st_size
    with open(path, "rb") as fh:
        if size <= _WHOLE_MAX:
            yield from fh
            return
        head = fh.read(_HEAD_BYTES).splitlines()
        yield from head[:-1]  # the last head line may be cut
        fh.seek(max(_HEAD_BYTES, size - _TAIL_BYTES))
        tail = fh.read().splitlines()
        yield from tail[1:]  # the first tail line may be cut


def _records(path: Path) -> Iterator[dict[str, Any]]:
    for raw in _lines(path):
        if not raw.strip():
            continue
        try:
            rec = json.loads(raw)
        except ValueError:
            continue
        if isinstance(rec, dict):
            yield rec


def _texts(rec: Mapping[str, Any]) -> tuple[str, list[str]]:
    """``(the record's own text, the paths its tool calls name)``. A tool
    RESULT contributes nothing — its payload is never copied."""
    msg = rec.get("message")
    content = msg.get("content") if isinstance(msg, Mapping) else None
    if isinstance(content, str):
        return ("" if _NOT_SAID.match(content) else content.strip()), []
    texts: list[str] = []
    paths: list[str] = []
    for block in content if isinstance(content, list) else []:
        if not isinstance(block, Mapping):
            continue
        kind = block.get("type")
        if kind == "text":
            text = str(block.get("text") or "")
            if text.strip() and not _NOT_SAID.match(text):
                texts.append(text.strip())
        elif kind == "tool_use":
            args = block.get("input")
            if isinstance(args, Mapping):
                for key in _PATH_KEYS:
                    value = args.get(key)
                    if isinstance(value, str) and value.strip():
                        paths.append(value.strip())
    return "\n\n".join(texts), paths


def _cap(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def build_handoff(path: Path | str, from_title: str, *, when: datetime | None = None) -> str:
    """The handoff markdown for the conversation file at ``path``. BLOCKING."""
    first = ""
    turns: list[list[str]] = []  # [role, text]
    paths: dict[str, None] = {}
    for rec in _records(Path(path)):
        role = rec.get("type")
        if role not in ("user", "assistant"):
            continue
        if rec.get("isSidechain") is True or rec.get("isMeta") is True:
            continue
        if rec.get("isApiErrorMessage") is True or rec.get("isCompactSummary") is True:
            continue
        text, used = _texts(rec)
        text = mask(text)  # a token pasted into the chat never reaches the file
        for p in used:
            p = mask(p)
            paths.pop(p, None)
            paths[p] = None  # most recent last
        if not text:
            continue
        if role == "user" and not first:
            first = text
        if turns and turns[-1][0] == role:
            turns[-1][1] += "\n\n" + text  # one turn per speaker in a row
        else:
            turns.append([role, text])
    stamp = (when or datetime.now().astimezone()).strftime("%Y-%m-%d")
    head = [
        f"# Handoff from {from_title} — {stamp}",
        "",
        f'This conversation ran on the account "{from_title}" and could not be continued on '
        "this one. It is summarised here so the work can carry on in a fresh conversation.",
    ]
    first_part = ["", "## What was asked first", "", _cap(first, FIRST_CAP) if first else "(nothing recorded)"]
    path_list = list(paths)[-PATHS_CAP:]

    def render(last: list[list[str]], files: list[str]) -> str:
        out = head + first_part
        if last:
            out += ["", "## Where it stopped (the last turns)"]
            for role, text in last:
                who = "You" if role == "user" else "Claude"
                out += ["", f"**{who}:** {_cap(text, TURN_CAP)}"]
        if files:
            out += ["", "## Files it worked with", ""] + [f"- {p}" for p in files]
        return "\n".join(out) + "\n"

    last = turns[-LAST_TURNS:]
    text = render(last, path_list)
    while len(text) > TOTAL_CAP and (last or path_list):
        if path_list:
            path_list = path_list[1:]  # the oldest file first
        else:
            last = last[1:]  # then the oldest turn
        text = render(last, path_list)
    return text if len(text) <= TOTAL_CAP else text[: TOTAL_CAP - 2] + "…\n"


#: The handoff's file name inside its conversation's own folder.
HANDOFF_NAME = "handoff.md"
#: Handoff folders older than this are pruned whenever a new one is written.
KEEP_DAYS = 30

_SESSION_ID = re.compile(r"[0-9A-Za-z\-]{1,64}")


def _prune(root: Path, keep: str, now: float) -> None:
    """Remove handoff folders (session-id names only) whose handoff is older
    than :data:`KEEP_DAYS`. They are Iron Jarvis's own derived files — the
    conversation itself stays in each account's home. Never raises."""
    cutoff = now - KEEP_DAYS * 86400.0
    try:
        entries = list(root.iterdir())
    except OSError:
        return
    for entry in entries:
        if entry.name == keep or not _SESSION_ID.fullmatch(entry.name) or not entry.is_dir():
            continue
        try:
            newest = max((f.stat().st_mtime for f in entry.iterdir()), default=entry.stat().st_mtime)
            if newest < cutoff:
                shutil.rmtree(entry)
        except OSError:
            continue


def write_handoff(text: str, root: Path | str, session_id: str) -> Path:
    """Write ``text`` to ``<root>/<session_id>/handoff.md`` atomically and
    return the path. ONE FOLDER PER CONVERSATION (review): the fresh Claude is
    given ``--add-dir`` of THAT folder only, so a session on one account can
    never read another account's handoff. Prunes old folders first.
    ``session_id`` must be a plain id (no separators)."""
    if not _SESSION_ID.fullmatch(str(session_id or "")):
        raise ValueError("not a session id")
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    _prune(root, str(session_id), time.time())
    folder = root / str(session_id)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / HANDOFF_NAME
    tmp = folder / f".{HANDOFF_NAME}.{os.getpid()}.tmp"
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)
    return target


#: A shell waiting for a command, by the pane's shell, judged on the LAST line
#: of the ANSI-stripped tail. Windows (MEASURED LIVE, claude 2.1.289 in a
#: ConPTY pane): the stream is a screen DIFF, so after Claude exits the prompt
#: can arrive glued to leftovers of the row it overwrites ("...shift+tab to
#: cycle)PS C:\x>") — the PowerShell and cmd prompts are therefore read at the
#: END of that line, from the drive letter on. POSIX is ANCHORED (review): a
#: bare "$"/"#"/"%" line (Git Bash's second line), a user@host prompt, or
#: "bash-5.2$" — never any line that merely ends in $ (or a "12%").
_PROMPTS = {
    "powershell": re.compile(r"PS (?:[A-Za-z]:[\\/]|/)[^>]*>$"),
    "cmd": re.compile(r"[A-Za-z]:\\[^>]*>$"),
    "posix": re.compile(
        r"^(?:[$#%]"
        r"|[\w.\-]+@[\w.\-]+(?:[: ][^$#%\s]*)*\s?[$#%]"
        r"|(?:ba|z|k)?sh-\d+(?:\.\d+)*[$#%])$"
    ),
}
#: How each is applied: the Windows prompts are SEARCHED at the line end.
_SEARCH = {"powershell": True, "cmd": True, "posix": False}

#: Control bytes a TUI leaves behind on exit (SI, a stray BEL…), which the
#: tail's ANSI strip does not remove.
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def _prompt_kind(shell: str) -> str:
    name = os.path.basename(str(shell or "")).lower()
    if name.startswith(("pwsh", "powershell")):
        return "powershell"
    if name in ("cmd", "cmd.exe"):
        return "cmd"
    return "posix"


def at_shell_prompt(tail: str, shell: str) -> bool:
    """Is the last rendered line of ``tail`` (ANSI-stripped) ``shell``'s prompt?"""
    kind = _prompt_kind(shell)
    pattern = _PROMPTS[kind]
    for raw in reversed(str(tail or "").splitlines()):
        line = _CONTROL.sub("", raw).strip()
        if line:
            return bool(pattern.search(line) if _SEARCH[kind] else pattern.match(line))
    return False
