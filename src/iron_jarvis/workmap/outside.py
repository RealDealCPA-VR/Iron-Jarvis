"""The owner's chats in OTHER coding tools, as work with a folder (v1.518.0).

Claude Code (the desktop app's Code tab and the terminal) and Codex keep each
conversation as a file on this PC. ``history/`` already reads those files,
read-only. This module turns them into two things:

* the SIDEBAR's list (:func:`listing_view`, ``GET /work-record/other-tools``):
  the chats the owner had in those tools, by tool and by folder;
* a FIFTH SOURCE of the work record (:func:`candidates`, kind
  ``KIND_OUTSIDE``): one row per such chat, so a chat in the same folder is
  told "this was done here before" whichever tool did it.

WHAT IS LEFT OUT (``hidden_reason``), each for a reason the owner would give:

* ``program``: a program started it, not the owner. Claude Code stamps every
  record with ``entrypoint`` (``sdk-cli`` = run by a program, which is how
  Iron Jarvis's own Claude turns are run); Codex's first record names its
  ``originator`` (``iron-jarvis`` for a session Iron Jarvis started).
* ``scratch``: its folder is a temporary folder or the tool's own folder.
* ``empty``: no folder, or nothing the owner typed.
* ``open``: it is already an Iron Jarvis coding session (Build › Code holds
  the tool's session id), so it is on the list and in the record as that.
* ``gone`` (the LIST only, v1.525.0): the folder it ran in no longer exists,
  so it cannot be continued. It stays in the work record.

A chat in Claude Code's own copy of a project
(``<project>/.claude/worktrees/<name>``) is the project's work: grouped and
filed under the project, opened in the copy (v1.525.0).

RULES THIS MODULE KEEPS

* Nothing under a tool's folder is ever written, moved or deleted.
* Only the first and last 256 KB of a file are read (``history.index``'s own
  window), once per (size, modified time). A LISTING stats files and opens
  none: the work record's candidates carry a stamp from the stat alone, and a
  file is opened only when its row is built (the record builds at most 60 rows
  inline; the rest go to its background thread).
* THE SWITCH (``config.other_tools_in_memory``, :func:`allow`): which tools'
  chats may enter the work record, and so be told to a chat. It is read at
  every listing and again before a row is told (:func:`live_state`). With no
  switch on the engine the source is OFF (fail closed). The sidebar's list is
  the owner's own screen and does not depend on it.
* :data:`READS_DISK` is switched off for the whole test session
  (``tests/conftest.py``): a bare test must never read the developer's own
  ``~/.claude`` or ``~/.codex``. A test of this module turns it on and points
  ``CLAUDE_CONFIG_DIR`` / ``CODEX_HOME`` at its own folders.
* A title is the owner's own first words in that chat. It is DATA: every
  reader that puts it in a prompt sends it through promptguard
  (``workmap/block.py`` does).
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ..core.db import storable_text
from . import keys
from .models import KIND_OUTSIDE

log = logging.getLogger("iron_jarvis.workmap.outside")

#: Off for the whole test session (see the module docstring).
READS_DISK = True

#: The tools read, by ``history``'s own names.
CLAUDE = "claude-code"
CODEX = "codex"
TOOLS: tuple[str, ...] = (CLAUDE, CODEX)
#: v1.528.0: Claude desktop's Chat tab. Its chats live on claude.ai, so they
#: come from two other places: what Claude REPORTS through Iron Jarvis's
#: connector (``workmap/reported.py``, ref ``claude-chat:r:<row id>``), and
#: the Cowork sessions the app keeps on this PC (``claude-chat:c:<id>``).
#: Read-only: a Chat-tab chat is carried on in Claude, not here.
CLAUDE_CHAT = "claude-chat"
REF_TOOLS: tuple[str, ...] = (CLAUDE, CLAUDE_CHAT, CODEX)
LABELS = {CLAUDE: "Claude Code", CLAUDE_CHAT: "Claude Chat", CODEX: "Codex"}

#: ``config.other_tools_in_memory`` -> the tools whose chats enter the record.
#: "claude-code" means all of Claude (v1.528.0): its Code tab and its Chat tab.
SWITCH = {
    "all": frozenset(REF_TOOLS),
    CLAUDE: frozenset({CLAUDE, CLAUDE_CHAT}),
    CODEX: frozenset({CODEX}),
    "none": frozenset(),
}
#: The most Cowork sessions read (newest first).
COWORK_MAX = 200
#: A reported or Cowork chat's summary as the work record keeps it.
NOTE_CHARS = 240
SWITCH_DEFAULT = "all"

#: The newest files read per tool: the bound Build › Code's opener reads
#: (``hosted.service.DISK_FILES``), so a listed chat is one it can open.
LISTED = 500
TITLE_CHARS = 120

#: Text that marks a temporary folder wherever the PC keeps its own (a chat
#: copied from another Windows login still reads as scratch), and (v1.525.0)
#: the Claude desktop app's own folder on each system: its scratch
#: workspaces and agent sessions run there, never in a folder of the owner's.
SCRATCH_MARKERS: tuple[str, ...] = (
    "/appdata/local/temp/",
    "/appdata/roaming/claude/",
    "/library/application support/claude/",
    "/.config/claude/",
)

#: v1.525.0: Claude Code (the desktop app) runs a session in its own copy of
#: a project, ``<project>/.claude/worktrees/<name>``. The work is the
#: project's: it is filed and grouped under the project, and opened in the
#: copy (the tool keeps the conversation under the copy's folder).
_COPY = re.compile(r"^(.+?)[\\/]\.claude[\\/]worktrees[\\/][^\\/]+(?:[\\/].*)?$", re.I)

HIDDEN_PROGRAM = "program"
HIDDEN_SCRATCH = "scratch"
HIDDEN_EMPTY = "empty"
HIDDEN_OPEN = "open"
#: v1.525.0: the folder the chat ran in is gone (a deleted copy of a
#: project), so it cannot be continued. Left off the LIST only: it stays in
#: the work record, under its project.
HIDDEN_GONE = "gone"

_ATTR = "_ironjarvis_other_tools_allowed"
_SID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_ABSOLUTE = re.compile(r"^(?:[A-Za-z]:[\\/]|[\\/])")
_SPACES = re.compile(r"\s+")

_lock = threading.Lock()
#: path -> ((mtime_ns, size), light summary)
_light: dict[str, tuple[tuple[int, int], dict[str, Any]]] = {}
#: (tool, chat id) -> the file that holds it, from the last listing.
_paths: dict[tuple[str, str], str] = {}


# --------------------------------------------------------------------------- #
# the switch
# --------------------------------------------------------------------------- #
def allow(engine: Any, switch: Callable[[], Any]) -> None:
    """Give ``engine``'s work record its switch: ``switch()`` answers the
    setting's value now. Never raises."""
    try:
        setattr(engine, _ATTR, switch)
    except Exception:  # noqa: BLE001 — a slotted / mock engine: the source stays off
        log.debug("other tools: the switch could not be kept", exc_info=True)


def allowed(db: Any) -> frozenset[str]:
    """The tools whose chats may enter this database's work record right now.
    Nothing when the engine has no switch, the setting cannot be read, or its
    value is not one this build knows (fail closed)."""
    try:
        switch = getattr(db.get_bind(), _ATTR, None)
        if switch is None:
            return frozenset()
        return SWITCH.get(str(switch() or "").strip().lower(), frozenset())
    except Exception:  # noqa: BLE001
        return frozenset()


# --------------------------------------------------------------------------- #
# reading
# --------------------------------------------------------------------------- #
def _mod(tool: str) -> Any:
    from ..history import index as HI

    return HI.HARNESSES[tool]


def _files(tool: str) -> tuple[list[tuple[int, int, str, Path]], bool]:
    """BLOCKING: ``(mtime_ns, size, chat id, path)`` for the tool's newest
    :data:`LISTED` conversation files, newest first, and whether that is all
    of them. One stat each; nothing is opened."""
    mod = _mod(tool)
    found: list[tuple[int, int, str, Path]] = []
    for path in mod.discover():
        if tool == CLAUDE and not _SID.match(path.stem):
            continue  # not a conversation (the opener reads the same names)
        try:
            st = os.stat(path)
        except OSError:
            continue
        sid = str(mod.id_from_path(path) or "").strip()
        if sid:
            found.append((st.st_mtime_ns, st.st_size, sid, path))
    found.sort(key=lambda f: (f[0], f[2]), reverse=True)
    # One chat, one row: a tool can keep the same chat id in two of its
    # folders (Claude Code, after a chat is carried on from another folder).
    # The newest file is the chat.
    seen: set[str] = set()
    found = [f for f in found if not (f[2] in seen or seen.add(f[2]))]
    whole = len(found) <= LISTED
    found = found[:LISTED]
    with _lock:
        for _, _, sid, path in found:
            _paths[(tool, sid)] = str(path)
    return found, whole


def _started_by(tool: str, head: list) -> tuple[str, str]:
    """``(what started it, how)`` as the tool's own records say it."""
    for _, rec in head:
        if not isinstance(rec, dict):
            continue
        if tool == CLAUDE:
            entry = rec.get("entrypoint")
            if isinstance(entry, str) and entry.strip():
                return entry.strip(), ""
        elif rec.get("type") == "session_meta":
            payload = rec.get("payload") if isinstance(rec.get("payload"), dict) else rec
            origin = payload.get("originator")
            source = payload.get("source")
            return (origin.strip() if isinstance(origin, str) else "",
                    source.strip() if isinstance(source, str) else "")
    return "", ""


def by_a_program(tool: str, origin: str, source: str) -> bool:
    """Did a program start this chat, rather than the owner in the tool?"""
    origin = origin.lower()
    if tool == CLAUDE:
        return origin == "sdk-cli"
    return origin.startswith("iron-jarvis") or origin == "codex_exec" or source.lower() == "exec"


def _scratch_roots() -> list[str]:
    roots = [keys.folder_key(tempfile.gettempdir())]
    for tool in TOOLS:
        try:
            roots.append(keys.folder_key(_mod(tool).root()))
        except Exception:  # noqa: BLE001
            continue
    return [r for r in roots if r]


def is_scratch(folder_key: str, roots: "list[str] | None" = None) -> bool:
    """A temporary folder, or a tool's own folder (compared as text)."""
    if not folder_key:
        return False
    text = (folder_key + "/").lower()
    if any(marker in text for marker in SCRATCH_MARKERS):
        return True
    return any(keys.inside(folder_key, r) for r in (roots if roots is not None else _scratch_roots()))


def project_of(folder: str) -> str:
    """The folder a chat's work belongs to: the project for a Claude Code
    copy of it, else the folder itself (text only)."""
    m = _COPY.match(str(folder or "").strip())
    return m.group(1) if m else str(folder or "")


def _iso_of_ns(ns: int) -> str:
    return datetime.fromtimestamp(ns / 1e9, tz=timezone.utc).isoformat()


# --------------------------------------------------------------------------- #
# what a session is doing (v1.530.0): Chat › Code's "Running elsewhere"
# --------------------------------------------------------------------------- #
#: Written to within this many seconds: "working now".
WORKING_S = 90.0
#: Steps the watch view shows (newest last).
STEPS_MAX = 12
STEP_CHARS = 160
#: Wall time; tests set it.
wall_clock = time.time


def _step_words(ev: dict[str, Any], label: str) -> tuple[str, str] | None:
    """``(kind, words)`` for one of the history reader's events, masked and
    cut; None for a step not worth a line."""
    from ..hosted.events import clip

    action = str(ev.get("action") or "")
    name = keys.file_name(ev.get("path") or "") or str(ev.get("path") or "")
    if action == "prompt.submitted" and ev.get("text"):
        return "asked", "You: " + clip(" ".join(str(ev["text"]).split()), STEP_CHARS)
    if action == "response.completed" and ev.get("text"):
        return "said", f"{label}: " + clip(" ".join(str(ev["text"]).split()), STEP_CHARS)
    if action in ("command.executed", "container.exec") and ev.get("command"):
        return "ran", "Ran: " + clip(" ".join(str(ev["command"]).split()), STEP_CHARS)
    if action == "file.write" and name:
        return "changed", f"Changed {clip(name, STEP_CHARS)}"
    if action == "file.read" and name:
        return "read", f"Read {clip(name, STEP_CHARS)}"
    if action == "file.delete" and name:
        return "deleted", f"Deleted {clip(name, STEP_CHARS)}"
    if action == "network.request":
        return "web", "Looked something up on the web"
    if action == "tool.called" and ev.get("tool"):
        return "used", f"Used {clip(tool_words(str(ev['tool'])), 80)}"
    return None


def tool_words(name: str) -> str:
    """A tool's name as a person reads it: a connector's tool
    (``mcp__Claude_Browser__browser_batch``) is its own name and its
    connector's ("browser batch (Claude Browser)"); any other is as named."""
    if name.startswith("mcp__"):
        parts = [p for p in name.split("__")[1:] if p]
        if len(parts) >= 2:
            server, own = parts[0], "__".join(parts[1:])
            return f"{own.replace('_', ' ')} ({server.replace('_', ' ')})"
    return name


def _steps(tool: str, mod: Any, path: Path, sid: str, records: list) -> list[dict[str, Any]]:
    """The newest :data:`STEPS_MAX` steps in the records given (the end of the
    file), oldest first. Never raises."""
    try:
        events = mod.map_events(records, path, sid)
    except Exception:  # noqa: BLE001 — a step line is extra
        return []
    out: list[dict[str, Any]] = []
    for ev in events:
        got = _step_words(ev, LABELS.get(tool, tool))
        if got is not None:
            out.append({"at": str(ev.get("ts") or ""), "kind": got[0], "words": got[1]})
    return out[-STEPS_MAX:]


def quiet_for(mtime_ns: int) -> float:
    """Seconds since the file was last written."""
    return max(0.0, wall_clock() - mtime_ns / 1e9)


def watch(tool: str, chat_id: str) -> dict[str, Any] | None:
    """BLOCKING: one listed session's newest steps, read again from the end
    of its file (nothing is kept between calls): ``{id, tool, title, folder,
    working, quiet_s, steps: [{at, kind, words}]}``; None when no listed
    session of that tool has that id (never a path join)."""
    if not READS_DISK or tool not in TOOLS:
        return None
    from ..history import index as HI

    for mtime_ns, size, sid, path in _files(tool)[0]:
        if sid != chat_id:
            continue
        chat = light(tool, path, mtime_ns, size)
        try:
            head, tail = HI._head_tail(path, size)
        except OSError:
            return None
        quiet = quiet_for(mtime_ns)
        return {
            "id": sid, "tool": tool, "title": (chat or {}).get("title") or "",
            "folder": (chat or {}).get("folder") or "", "working": quiet < WORKING_S,
            "quiet_s": int(quiet), "steps": _steps(tool, _mod(tool), path, sid, tail or head),
        }
    return None


def light(tool: str, path: Path, mtime_ns: int, size: int) -> dict[str, Any] | None:
    """BLOCKING: one chat's title, folder, times and what started it, from
    the first and last window of its file (cached per size and modified
    time). None when the file cannot be read."""
    key = (int(mtime_ns), int(size))
    with _lock:
        hit = _light.get(str(path))
    if hit is not None and hit[0] == key:
        return dict(hit[1])
    from ..history import index as HI

    mod = _mod(tool)
    try:
        head, tail = HI._head_tail(path, size)
        fields = mod.summarize(head, tail, path)
    except Exception:  # noqa: BLE001 — one unreadable file never stops a listing
        log.debug("other tools: %s could not be read", path, exc_info=True)
        return None
    origin, source = _started_by(tool, head)
    folder = str(fields.get("project") or "").strip()
    if not _ABSOLUTE.match(folder):
        folder = ""  # a folder NAME (Claude Code's slug) is not a folder
    stamp = _iso_of_ns(mtime_ns)
    sid = str(mod.id_from_path(path) or "")
    steps = _steps(tool, mod, path, sid, tail or head)
    out = {
        "tool": tool,
        "id": sid,
        "folder": folder,
        "home": project_of(folder) if folder else "",
        "title": _SPACES.sub(" ", str(fields.get("title") or "")).strip()[:TITLE_CHARS],
        "started": fields.get("started") or fields.get("ended") or stamp,
        "ended": fields.get("ended") or stamp,
        "origin": origin,
        "source": source,
        # v1.530.0: the newest step in plain words ("Ran: pytest -q").
        "doing": next((s["words"] for s in reversed(steps) if s["kind"] != "said"), ""),
    }
    with _lock:
        _light[str(path)] = (key, out)
        if len(_light) > 4 * LISTED:  # files that left the listing
            for stale in [p for p in _light if not os.path.exists(p)]:
                _light.pop(stale, None)
    return dict(out)


def hidden_reason(chat: dict[str, Any], used: "set[str] | frozenset[str]" = frozenset(),
                  roots: "list[str] | None" = None) -> str:
    """Why this chat is left out, or "" when it is shown."""
    if chat.get("id") in used:
        return HIDDEN_OPEN
    if by_a_program(str(chat.get("tool") or ""), str(chat.get("origin") or ""), str(chat.get("source") or "")):
        return HIDDEN_PROGRAM
    if not chat.get("folder") or not chat.get("title"):
        return HIDDEN_EMPTY
    if is_scratch(keys.folder_key(chat["folder"]), roots):
        return HIDDEN_SCRATCH
    return ""


def used_ids(db: Any) -> set[str]:
    """The tool session ids Build › Code already holds (a read that fails
    RAISES: a chat must never be listed twice because of it)."""
    from sqlalchemy import inspect
    from sqlmodel import select

    try:
        from ..hosted.models import HostedSessionRecord as H
    except Exception:  # noqa: BLE001 — no Build › Code in this build
        return set()
    if not inspect(db.get_bind()).has_table("hostedsessionrecord"):
        return set()
    return {str(s) for s in db.exec(select(H.harness_session_id)) if s}


# --------------------------------------------------------------------------- #
# Claude desktop's Chat tab (v1.528.0)
# --------------------------------------------------------------------------- #
def cowork_root() -> Path:
    """Where Claude desktop keeps its Cowork sessions on this PC."""
    from ..mcpserver.apps import claude_config_path

    return claude_config_path().parent / "local-agent-mode-sessions"


def _when(value: Any) -> str:
    """An ISO time from Claude desktop's own stamps (seconds or milliseconds
    since 1970, or ISO text); "" when it is neither."""
    if isinstance(value, (int, float)) and value > 0:
        seconds = value / 1000 if value > 1e12 else value
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            return ""
    stamp = _naive(value) if isinstance(value, str) and value.strip() else None
    return stamp.replace(tzinfo=timezone.utc).isoformat() if stamp else ""


def cowork_sessions() -> list[dict[str, Any]]:
    """BLOCKING: the Cowork sessions Claude desktop keeps on this PC, newest
    first (its own small record per session: title, times, the folders the
    owner chose). Archived ones are left out. Nothing is written."""
    if not READS_DISK:
        return []
    root = cowork_root()
    try:
        files = [(p.stat().st_mtime_ns, p.stat().st_size, p) for p in root.glob("*/*/local_*.json")]
    except OSError:
        return []
    files.sort(key=lambda f: f[0], reverse=True)
    out: list[dict[str, Any]] = []
    for mtime_ns, size, path in files[:COWORK_MAX]:
        key = (int(mtime_ns), int(size))
        with _lock:
            hit = _light.get(str(path))
        if hit is not None and hit[0] == key:
            row = dict(hit[1])
        else:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(data, dict):
                continue
            chosen = [f for f in (data.get("userSelectedFolders") or []) if isinstance(f, str) and _ABSOLUTE.match(f)]
            sid = str(data.get("sessionId") or path.stem.removeprefix("local_"))
            stamp = _iso_of_ns(mtime_ns)
            row = {
                "id": f"c:{sid}",
                "title": _SPACES.sub(" ", str(data.get("title") or "")).strip()[:TITLE_CHARS],
                "folder": chosen[0] if chosen else "",
                "started": _when(data.get("createdAt")) or stamp,
                "ended": _when(data.get("lastActivityAt")) or stamp,
                "summary": "",
                "link": "",
                "archived": bool(data.get("isArchived")),
                "path": str(path),
            }
            with _lock:
                _light[str(path)] = (key, row)
        if row["archived"] or not row["title"]:
            continue
        out.append(row)
    return out


def reported_claude(engine: Any) -> list[dict[str, Any]]:
    """BLOCKING: the chats Claude desktop reported through the connector."""
    from . import reported

    if engine is None:
        return []
    return [{
        "id": f"r:{r['id']}", "title": r["title"], "folder": r["folder"], "started": r["started"],
        "ended": r["ended"], "summary": r["summary"], "link": r["link"], "files": r["files"],
    } for r in reported.rows(engine, reported.APP_CLAUDE)]


#: v1.530.0: the connected app whose reports add a summary to a coding
#: tool's chats (read from that tool's own files).
REPORTING_APP = {CODEX: "codex", CLAUDE: "claude-code"}


def _reports_for(engine: Any, tool: str) -> list[dict[str, Any]]:
    from . import reported

    app = REPORTING_APP.get(tool)
    return reported.rows(engine, app) if engine is not None and app else []


def _codex_reports(engine: Any) -> list[dict[str, Any]]:
    return _reports_for(engine, CODEX)


# --------------------------------------------------------------------------- #
# the sidebar's list
# --------------------------------------------------------------------------- #
def _grouped(chats: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Chats by project folder, newest first; "" is "No folder"."""
    groups: dict[str, dict[str, Any]] = {}
    for chat in chats:
        home = project_of(chat["folder"]) if chat.get("folder") else ""
        group = groups.setdefault(keys.folder_key(home) if home else "", {
            "folder": home, "name": (keys.file_name(home) or home) if home else "No folder", "chats": [],
        })
        group["chats"].append(chat)
    for group in groups.values():
        group["chats"].sort(key=lambda c: str(c.get("ended") or ""), reverse=True)
    return sorted(groups.values(), key=lambda g: str(g["chats"][0].get("ended") or ""), reverse=True)


def listing_view(used: "set[str] | None" = None, engine: Any = None) -> dict[str, Any]:
    """BLOCKING: the owner's chats in the other tools, by tool then by
    folder, newest first: ``{tools: [{tool, label, total, openable, folders:
    [{folder, name, chats: [{id, title, folder, started, ended, summary?,
    link?}]}]}], hidden, whole}``.

    A group's folder is the project; a chat's own ``folder`` is where it ran
    and is opened (a Claude Code copy of the project). A chat whose folder is
    gone is left out (``HIDDEN_GONE``). Claude Chat's chats (reported, and
    Cowork sessions) are not ``openable``: they are carried on in Claude. A
    Codex chat carries the ``summary`` Codex reported for it, if any. A tool
    with nothing to show is left out."""
    used = used or set()
    found: dict[str, list[dict[str, Any]]] = {}
    hidden = 0
    whole = True
    roots = _scratch_roots() if READS_DISK else []
    there: dict[str, bool] = {}
    for tool in TOOLS if READS_DISK else ():
        try:
            files, all_of_them = _files(tool)
        except Exception:  # noqa: BLE001 — one tool's folder never hides the other's
            log.debug("other tools: %s could not be listed", tool, exc_info=True)
            whole = False
            continue
        whole = whole and all_of_them
        chats: list[dict[str, Any]] = []
        for mtime_ns, size, _sid, path in files:
            chat = light(tool, path, mtime_ns, size)
            if chat is None:
                continue
            if hidden_reason(chat, used, roots):
                hidden += 1
                continue
            if chat["folder"] not in there:  # one look per folder per listing
                there[chat["folder"]] = os.path.isdir(chat["folder"])
            if not there[chat["folder"]]:
                hidden += 1  # HIDDEN_GONE: it cannot be continued
                continue
            # ``folder`` is where the chat ran, which is where it is opened.
            # v1.530.0: working now (written a moment ago), how long quiet,
            # and its newest step, for Chat › Code's "Running elsewhere".
            quiet = quiet_for(mtime_ns)
            chats.append({"id": chat["id"], "title": chat["title"], "folder": chat["folder"],
                          "started": chat["started"], "ended": chat["ended"],
                          "working": quiet < WORKING_S, "quiet_s": int(quiet),
                          "doing": chat.get("doing") or ""})
        found[tool] = chats
    for tool in REPORTING_APP:
        if not found.get(tool):
            continue
        try:
            from .reported import match_reports

            for cid, rep in match_reports(_reports_for(engine, tool), found[tool]).items():
                for chat in found[tool]:
                    if chat["id"] == cid:
                        chat["summary"] = rep.get("summary") or ""
        except Exception:  # noqa: BLE001 — a summary is extra
            log.debug("other tools: %s's reports could not be read", tool, exc_info=True)
    claude_chat: list[dict[str, Any]] = []
    try:
        claude_chat += reported_claude(engine)
    except Exception:  # noqa: BLE001 — the reports are one source of this group
        log.debug("other tools: Claude's reported chats could not be read", exc_info=True)
        whole = False
    claude_chat += [{k: c[k] for k in ("id", "title", "folder", "started", "ended", "summary", "link")}
                    for c in cowork_sessions()]
    found[CLAUDE_CHAT] = claude_chat
    tools: list[dict[str, Any]] = []
    for tool in REF_TOOLS:
        chats = found.get(tool) or []
        if chats:
            tools.append({"tool": tool, "label": LABELS[tool], "total": len(chats),
                          "openable": tool != CLAUDE_CHAT, "folders": _grouped(chats)})
    return {"tools": tools, "hidden": hidden, "whole": whole}


# --------------------------------------------------------------------------- #
# the work record's fifth source
# --------------------------------------------------------------------------- #
def ref_of(tool: str, chat_id: str) -> str:
    return f"{tool}:{chat_id}"


def split_ref(ref: str) -> tuple[str, str]:
    tool, _, chat_id = str(ref or "").partition(":")
    return (tool, chat_id) if tool in REF_TOOLS and chat_id else ("", "")


def _naive(iso: Any) -> datetime | None:
    try:
        value = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return value if value.tzinfo is None else value.astimezone(timezone.utc).replace(tzinfo=None)


def _note(summary: Any) -> str:
    return storable_text(_SPACES.sub(" ", str(summary or "")).strip())[:NOTE_CHARS]


def _claude_chat_row(chat: dict[str, Any]) -> dict[str, Any] | None:
    """A Claude Chat chat as a work-record row (None without a folder: the
    record holds work that has a folder)."""
    folder = chat.get("folder") or ""
    if not folder or is_scratch(keys.folder_key(folder), []):
        return None
    home = project_of(folder)
    files = [{"path": p, "how": "wrote"} for p in (chat.get("files") or [])]
    return {
        "title": storable_text(chat["title"]),
        "folder": storable_text(home),
        "folder_key": storable_text(keys.folder_key(home)),
        "tool": LABELS[CLAUDE_CHAT],
        "note": _note(chat.get("summary")),
        "files_json": json.dumps(files, ensure_ascii=True),
        "files_total": len(files),
        "started_at": _naive(chat.get("started")),
        "updated_at": _naive(chat.get("ended")) or datetime.now(timezone.utc).replace(tzinfo=None),
    }


def candidates(db: Any, candidate: Callable[..., Any]) -> tuple[list[Any], bool]:
    """The work record's candidates for the other tools' chats, and whether
    the listing was read whole. ``candidate`` is ``collect.Candidate``. A
    coding tool's candidate's stamp comes from one stat, and its file is
    opened by ``build``; a Codex stamp also carries the newest Codex report,
    so a report that arrives later rebuilds the row with its summary."""
    tools = allowed(db)
    if not tools:
        return [], True  # switched off: the rows this source wrote are removed
    engine = db.get_bind()
    out: list[Any] = []
    whole = True
    if READS_DISK and (tools & set(TOOLS)):
        used = used_ids(db)
        roots = _scratch_roots()
        reports = {t: (_reports_for(engine, t) if t in tools else []) for t in REPORTING_APP}
        marks = {t: f"{len(r)}:{max((x['ended'] for x in r), default='')}" for t, r in reports.items()}
        for tool in TOOLS:
            if tool not in tools:
                continue
            files, all_of_them = _files(tool)  # a folder that cannot be read RAISES
            whole = whole and all_of_them
            for mtime_ns, size, sid, path in files:
                if sid in used:
                    continue  # already a Build › Code session: that row is the record

                def build(_db: Any, *, tool=tool, path=path, mtime_ns=mtime_ns, size=size) -> dict[str, Any] | None:
                    chat = light(tool, path, mtime_ns, size)
                    if chat is None or hidden_reason(chat, frozenset(), roots):
                        return None
                    home = chat.get("home") or chat["folder"]
                    row = {
                        "title": storable_text(chat["title"]),
                        "folder": storable_text(home),
                        "folder_key": storable_text(keys.folder_key(home)),
                        "tool": LABELS[tool],
                        "note": "",
                        "started_at": _naive(chat["started"]),
                        "updated_at": _naive(chat["ended"]) or _naive(_iso_of_ns(mtime_ns)),
                    }
                    if reports.get(tool):
                        from .reported import match_reports

                        rep = match_reports(reports[tool], [chat]).get(chat["id"])
                        row["note"] = _note(rep.get("summary")) if rep else ""
                    return row

                ref = ref_of(tool, sid)
                stamp = f"{mtime_ns}|{size}" + (f"|{marks[tool]}" if tool in marks else "")
                out.append(candidate(f"{KIND_OUTSIDE}:{ref}", KIND_OUTSIDE, ref, stamp, build))
    if CLAUDE_CHAT in tools:
        chats = reported_claude(engine)  # a read that fails RAISES: rows are left as they are
        sessions = cowork_sessions()
        for chat in [*chats, *sessions]:
            ref = ref_of(CLAUDE_CHAT, chat["id"])
            stamp = f"{chat['ended']}|{chat['title']}|{chat.get('summary', '')[:40]}|{chat.get('folder', '')}"
            out.append(candidate(f"{KIND_OUTSIDE}:{ref}", KIND_OUTSIDE, ref, stamp,
                                 lambda _db, chat=chat: _claude_chat_row(chat)))
    return out, whole


def live_state(db: Any, refs: list[str]) -> dict[str, dict[str, Any]]:
    """``{ref: {"local_only": False, "archived": False}}`` for each ref whose
    chat is still on disk, whose tool is still switched on, and which has not
    become an Iron Jarvis session since. Anything else is simply absent, so
    it is never told."""
    if not refs:
        return {}
    tools = allowed(db)
    if not tools:
        return {}
    out: dict[str, dict[str, Any]] = {}
    if CLAUDE_CHAT in tools:
        # v1.528.0: a reported chat still on record; a Cowork session whose
        # own record is still on disk and not archived.
        from sqlmodel import select

        from .models import ReportedChat

        wanted = [sid for tool, sid in (split_ref(r) for r in refs) if tool == CLAUDE_CHAT]
        ids = [w[2:] for w in wanted if w.startswith("r:")]
        if ids:
            have = {str(i) for i in db.exec(select(ReportedChat.id).where(ReportedChat.id.in_(ids)))}  # type: ignore[attr-defined]
            for i in have:
                out[ref_of(CLAUDE_CHAT, f"r:{i}")] = {"local_only": False, "archived": False}
        if any(w.startswith("c:") for w in wanted):
            live = {c["id"] for c in cowork_sessions()}
            for w in wanted:
                if w.startswith("c:") and w in live:
                    out[ref_of(CLAUDE_CHAT, w)] = {"local_only": False, "archived": False}
    if not READS_DISK:
        return out
    used = used_ids(db)
    listed: set[str] = set()
    for ref in refs:
        tool, sid = split_ref(ref)
        if tool not in TOOLS or tool not in tools or sid in used:
            continue
        with _lock:
            path = _paths.get((tool, sid))
        if path is None and tool not in listed:
            listed.add(tool)
            _files(tool)
            with _lock:
                path = _paths.get((tool, sid))
        if path and os.path.exists(path):
            out[ref] = {"local_only": False, "archived": False}
    return out


def find(tool: str, chat_id: str) -> dict[str, Any] | None:
    """BLOCKING: one listed chat by its id (never a path join), or None."""
    if not READS_DISK or tool not in TOOLS:
        return None
    for mtime_ns, size, sid, path in _files(tool)[0]:
        if sid == chat_id:
            return light(tool, path, mtime_ns, size)
    return None


def forget_cache() -> None:
    """Drop what this module remembers (tests)."""
    with _lock:
        _light.clear()
        _paths.clear()


__all__ = [
    "CLAUDE", "CODEX", "LABELS", "LISTED", "READS_DISK", "SWITCH", "SWITCH_DEFAULT", "TOOLS",
    "allow", "allowed", "by_a_program", "candidates", "find", "forget_cache", "hidden_reason",
    "is_scratch", "light", "listing_view", "live_state", "project_of", "ref_of", "split_ref", "used_ids",
]
