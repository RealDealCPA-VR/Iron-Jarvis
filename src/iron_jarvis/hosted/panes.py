"""THE CODE VIEW'S RIGHT PANES (v1.414.0): Changes, Usage and Session for a
Build › Code session — the same column Chat › Code has (the user: "the
icons for changes, remaining usage, files and sessions to the right just as
in the native harness").

Every reader here is READ-ONLY, BLOCKING (the routes hop it off the loop) and
never starts a process of the coding tool, a model call or a probe.

CHANGES — the honest source. The REAL tool writes files itself, outside Iron
Jarvis's undo journal, so the journal knows nothing. Two sources, said by
name on the pane:

* ``git`` (the folder is inside a repository): every file that differs from
  the commit HEAD was on when this session was STARTED HERE (the newest
  first-parent commit no later than the session's ``created_at``), committed
  since or not, new untracked files included (``.gitignore`` applies). LIMITS,
  said: it includes changes the user or another program made in that folder
  meanwhile; a commit whose date was rewritten can land on the wrong side;
  ignored files are not listed. Files the tool's own file tools reported
  editing are marked ``by_tool``.
* ``tool`` (no repository, or git is not installed): the tool's OWN record of
  its file edits (the ``tool`` events with a path and a diff), each edit's
  diff as the tool reported it at the time. LIMITS, said: changes made by a
  command (a script, ``sed -i``) are not listed; a diff is cut at the event's
  own bound; lines may have moved since.

No Undo on either: Iron Jarvis did not make these writes and keeps no copy of
what was there before (the tool's own undo, or git, is the way back).

USAGE — cached only: the session's Iron-Proxy account row (the cached
snapshot, ``harness.build_accounts``), the windows the TOOL itself last
reported (:mod:`.seen`, kept on the ``usage`` events with their time), and the
session's totals summed from its ``usage`` events. A subscription's cost is a
list-price EQUIVALENT (``list_price``), never spend.

SESSION — the branch the folder is on, the instruction files in it, how full
the conversation was at the tool's last report (:mod:`.seen`), and the
programs running under the tool's own process right now (its helpers
included).
"""

from __future__ import annotations

import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import seen as SEEN
from .events import one_line

#: How many changed files are listed (the Branch view's own bound).
MAX_FILES = 50
#: A unified diff of one file is cut here (the Branch view's own bound).
DIFF_CAP = 200_000
#: Programs listed under the tool's process.
MAX_PROGRAMS = 20
#: Instruction files a coding tool may read, looked for in the folder.
RULE_FILES: tuple[str, ...] = (
    "CLAUDE.md", "CLAUDE.local.md", "AGENTS.md", "AGENTS.override.md", ".claude/CLAUDE.md",
)
#: Process names that are plumbing, not programs (Windows' console host).
_PLUMBING = frozenset({"conhost.exe"})

NO_FOLDER = "This session has no folder yet: it is made with your first message."
FOLDER_GONE = "This session's folder is not there any more, so nothing can be compared."
ON_SHARE = "This session's folder is on a network share, so its changes are not read here."
GIT_WORDS = (
    "Every file in this folder that differs from where git was when the session "
    "started here, committed since or not (new files included). It includes "
    "changes you or another program made meanwhile; files git ignores are not listed."
)
TOOL_WORDS = (
    "This folder is not in a git repository, so this is {tool}'s own record of the "
    "files it edited with its file tools. Changes made by commands it ran are not "
    "listed, and lines may have moved since."
)
#: v1.414.0 review: git missing is said as git missing (it read "not in a git
#: repository" of a folder that may well be one).
NO_GIT_WORDS = (
    "git is not installed on this PC, so this is {tool}'s own record of the "
    "files it edited with its file tools. Changes made by commands it ran are not "
    "listed, and lines may have moved since."
)
#: v1.414.0 review: git that did not answer is never "no changes" or "not a
#: repository" — both were what the first cut said.
GIT_UNREADABLE = "git could not read this folder's changes, so none are listed: {why}."
GIT_TIMED_OUT = "it did not answer in time"
#: v1.414.0 review: past this many characters of pathspec (Windows' command
#: line holds 32,767) each changed file is read alone, as the first cut did.
PATHSPEC_CHARS = 16_000
#: A changed file whose diff git did not give (said on its row).
DIFF_UNREAD = "its diff could not be read here"
NO_UNDO = "No Undo here: {tool} made these changes itself, so use git or {tool} to take one back."


def _network(path: str) -> bool:
    from ..harness.sessions import _network_spelling

    return _network_spelling(path)


def _key(path: str) -> str:
    return os.path.normcase(os.path.normpath(path))


def _created(row: Any) -> datetime:
    when = getattr(row, "created_at", None)
    if not isinstance(when, datetime):
        return datetime.now(timezone.utc)
    return when if when.tzinfo is not None else when.replace(tzinfo=timezone.utc)


def _iso(when: datetime) -> str:
    return when.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def tool_paths(events: list[dict[str, Any]], folder: str) -> dict[str, list[dict[str, Any]]]:
    """The files the tool's own file tools reported changing (finished, not
    failed), absolute key -> the events in order."""
    out: dict[str, list[dict[str, Any]]] = {}
    for ev in events:
        if ev.get("type") != "tool" or ev.get("kind") not in ("edit", "write"):
            continue
        if ev.get("status") != "done":
            continue
        path = str(ev.get("path") or "").strip()
        if not path or _network(path):
            continue
        if not os.path.isabs(path):
            if not folder:
                continue
            path = os.path.join(folder, path)
        out.setdefault(_key(path), []).append(ev)
    return out


# --------------------------------------------------------------------------- #
# CHANGES
# --------------------------------------------------------------------------- #


def _git(args: list[str], cwd: str, *, input: bytes | None = None) -> Any:
    from ..harness.branches import _g

    return _g(args, cwd, reads=True, input=input)


class GitUnreadable(Exception):
    """git ran (or may have) in a repository and did not answer: the pane says
    so in words, never "no changes" or "not a repository" (v1.414.0 review)."""

    def __init__(self, got: Any) -> None:
        why = GIT_TIMED_OUT if getattr(got, "timed_out", False) else _git_reason(got)
        super().__init__(why)
        self.words = GIT_UNREADABLE.format(why=why)


def _git_reason(got: Any) -> str:
    """git's own first error line, masked and cut (``fatal:`` dropped)."""
    for raw in str(getattr(got, "err", "") or "").splitlines():
        line = raw.strip()
        if line:
            if line.lower().startswith("fatal:"):
                line = line[6:].strip()
            return one_line(line, 160).rstrip(".")
    return "it stopped with an error"


def _base_commit(folder: str, since: datetime) -> str | None:
    """The commit HEAD was on when the session started (the newest
    first-parent commit no later than ``since``), the empty tree when every
    commit is newer (or there is none yet), None when this is no repository.
    Raises :class:`GitUnreadable` when git did not answer (a time limit, a
    repository it refuses to read — "dubious ownership"): that is not "no
    repository"."""
    inside = _git(["rev-parse", "--is-inside-work-tree"], folder)
    if not inside.ok:
        if not inside.timed_out and "not a git repository" in str(inside.err or "").lower():
            return None
        raise GitUnreadable(inside)
    if inside.out.strip() != "true":
        return None
    # git dates have whole seconds and ``--before`` includes its second: a
    # commit made in the session's first second must not become the base.
    start = since.astimezone(timezone.utc).replace(microsecond=0) - timedelta(seconds=1)
    stamp = start.strftime("%Y-%m-%d %H:%M:%S +0000")
    got = _git(["rev-list", "-1", "--first-parent", f"--before={stamp}", "HEAD"], folder)
    if not got.ok:
        # No commits yet (HEAD names no commit: ``rev-parse --verify -q``
        # exits 1 and says nothing) reads against the empty tree; anything
        # else (a time limit) is not that — the empty tree would list every
        # tracked file as created.
        if got.timed_out:
            raise GitUnreadable(got)
        head = _git(["rev-parse", "--verify", "-q", "HEAD"], folder)
        if head.ok or head.timed_out or head.code != 1:
            raise GitUnreadable(got)
    sha = got.out.strip() if got.ok else ""
    if sha:
        return sha
    empty = _git(["hash-object", "-t", "tree", "--stdin"], folder, input=b"")
    return empty.out.strip() if empty.ok and empty.out.strip() else None


def git_changes(folder: str, since: datetime) -> dict[str, Any] | None:
    """The folder's changes since the session started, from git (the Branch
    view's shape and bounds); None = not a repository / git missing."""
    from ..harness import branches as B

    if B.git_path() is None:
        return None
    base = _base_commit(folder, since)
    if base is None:
        return None
    wt = Path(folder)
    status = _git(["diff", "--no-color", "--no-ext-diff", "-M", "--relative", "--name-status", "-z", base], folder)
    if not status.ok:
        raise GitUnreadable(status)
    numstat = _git(["diff", "--no-color", "--no-ext-diff", "-M", "--relative", "--numstat", "-z", base], folder)
    if not numstat.ok:
        # Without the counts every file would read as a binary file.
        raise GitUnreadable(numstat)
    counts: dict[str, tuple[int | None, int | None]] = {}
    if numstat.ok:
        parts = B._split_z(numstat.out)
        i = 0
        while i < len(parts):
            head = parts[i]
            i += 1
            bits = head.split("\t") if head else []
            if len(bits) < 3:
                continue
            added = None if bits[0] == "-" else int(bits[0])
            removed = None if bits[1] == "-" else int(bits[1])
            if bits[2] == "":
                counts[parts[i + 1] if i + 1 < len(parts) else ""] = (added, removed)
                i += 2
            else:
                counts[bits[2]] = (added, removed)
    entries: list[tuple[str, str, str]] = []
    parts = B._split_z(status.out)
    i = 0
    while i < len(parts):
        code = parts[i]
        i += 1
        if not code:
            continue
        if code[:1] in ("R", "C"):
            entries.append((code[:1], parts[i], parts[i + 1] if i + 1 < len(parts) else ""))
            i += 2
        else:
            entries.append((code[:1], parts[i] if i < len(parts) else "", parts[i] if i < len(parts) else ""))
            i += 1
    changes: list[dict[str, Any]] = []
    more = len(entries) > MAX_FILES
    shown = entries[:MAX_FILES]
    for (code, old, new), patch in zip(shown, _patches(folder, base, shown)):
        rel = new or old
        added, removed = counts.get(rel, (None, None))
        row: dict[str, Any] = {
            "path": str(wt / rel), "rel": rel, "name": rel.rsplit("/", 1)[-1],
            "status": {"A": "created", "D": "deleted"}.get(code, "modified"),
            "added": added, "removed": removed, "diff": patch or "",
        }
        notes = [f"renamed from {old}"] if code == "R" and old != new else []
        if added is None and removed is None:
            row.update(binary=True, diff="binary file", size=B._size_now(wt / rel))
        elif patch is None:
            # v1.414.0 review: said, never an empty diff under real counts.
            notes.append(DIFF_UNREAD)
        if notes:
            row["note"] = "; ".join(notes)
        if len(row["diff"]) > DIFF_CAP:
            row["diff"], row["truncated"] = row["diff"][:DIFF_CAP], True
        changes.append(row)
    untracked = _git(["ls-files", "--others", "--exclude-standard", "-z"], folder)
    if not untracked.ok:
        raise GitUnreadable(untracked)
    for rel in [p for p in B._split_z(untracked.out) if p]:
        if len(changes) >= MAX_FILES:
            more = True
            break
        changes.append(B._new_file_change(wt, rel))
    return {"changes": changes, "truncated_files": more, "base": base[:12]}


def _diff_head(old: str, new: str) -> str | None:
    """The ``diff --git`` line git writes for one entry under
    ``core.quotepath=off``; None when git would C-quote a name (a quote, a
    backslash, a control character) — that file is then read alone."""
    both = old + new
    if any(c in both for c in ('"', "\\")) or any(ord(c) < 32 or ord(c) == 127 for c in both):
        return None
    return f"diff --git a/{old} b/{new}"


def _patches(folder: str, base: str, entries: list[tuple[str, str, str]]) -> list[str | None]:
    """Each entry's unified diff against ``base``, with ONE git child for all
    of them (v1.414.0 review: the first cut ran one per file — 54 children,
    ~3 s, for 50 files, on every 5-second poll while a reply ran). The
    combined output is cut at its ``diff --git`` lines; each piece must START
    with the line predicted for its entry, in order. Anything else (a name git
    quotes, a count that does not match, a pathspec too long for a command
    line) reads that file alone, exactly as the first cut did. None = that
    file's diff could not be read; a combined read that ran out of TIME is not
    retried file by file (50 more waits of up to 30 s each)."""
    if not entries:
        return []
    paths: list[str] = []
    for _code, old, new in entries:
        for p in ([old, new] if old != new else [new]):
            if p and p not in paths:
                paths.append(p)
    pieces: list[str] = []
    if sum(len(p) + 3 for p in paths) <= PATHSPEC_CHARS:
        got = _git(["diff", "--no-color", "--no-ext-diff", "-M", "--relative", base, "--", *paths], folder)
        if got.timed_out:
            return [None] * len(entries)
        if got.ok:
            cur: list[str] = []
            for line in got.out.splitlines(keepends=True):
                if line.startswith("diff --git ") and cur:
                    pieces.append("".join(cur))
                    cur = []
                cur.append(line)
            if cur:
                pieces.append("".join(cur))
    whole = len(pieces) == len(entries)
    out: list[str | None] = []
    for i, (_code, old, new) in enumerate(entries):
        head = _diff_head(old, new)
        piece = pieces[i] if whole else ""
        if head is not None and piece.split("\n", 1)[0].rstrip("\r") == head:
            out.append(piece)
            continue
        alone = [old, new] if old != new else [new]
        one = _git(["diff", "--no-color", "--no-ext-diff", "-M", "--relative", base, "--", *alone], folder)
        out.append(one.out if one.ok else None)
    return out


def _hunks_only(diff: str) -> str:
    """A diff's hunks without its ``---``/``+++`` header (so several edits of
    one file read as one diff: a header after a hunk would read as lines)."""
    lines = str(diff or "").splitlines()
    for i, line in enumerate(lines):
        if line.startswith("@@"):
            return "\n".join(lines[i:])
    return ""


def tool_changes(events: list[dict[str, Any]], folder: str) -> dict[str, Any]:
    """The tool's own record of its file edits (no repository)."""
    changes: list[dict[str, Any]] = []
    more = False
    for key, evs in tool_paths(events, folder).items():
        if len(changes) >= MAX_FILES:
            more = True
            break
        path = str(evs[-1].get("path") or "")
        if not os.path.isabs(path):
            path = os.path.join(folder, path)
        try:
            rel = os.path.relpath(path, folder).replace("\\", "/") if folder else path
        except ValueError:
            rel = path
        bodies = [_hunks_only(str(e.get("diff") or "")) for e in evs if e.get("diff")]
        bodies = [b for b in bodies if b]
        diff = ""
        if bodies:
            diff = f"--- a/{rel}\n+++ b/{rel}\n" + "\n".join(bodies)
        added = sum(1 for b in bodies for ln in b.splitlines() if ln.startswith("+"))
        removed = sum(1 for b in bodies for ln in b.splitlines() if ln.startswith("-"))
        created = all(e.get("kind") == "write" for e in evs) and "--- /dev/null" in str(evs[0].get("diff") or "")
        row: dict[str, Any] = {
            "path": path, "rel": rel, "name": rel.rsplit("/", 1)[-1],
            "status": "created" if created else "modified",
            "added": added if bodies else None, "removed": removed if bodies else None,
            "diff": diff, "by_tool": True,
        }
        if not bodies:
            row["note"] = "the tool sent no diff for this file"
        if len(evs) > 1:
            row["note"] = f"{len(evs)} edits, each as the tool reported it"
        if len(diff) > DIFF_CAP:
            row["diff"], row["truncated"] = diff[:DIFF_CAP], True
        changes.append(row)
    return {"changes": changes, "truncated_files": more}


def changes_view(row: Any, events: list[dict[str, Any]], tool: str) -> dict[str, Any]:
    """``GET /build/app/sessions/{id}/changes`` — what this session changed,
    with the source named. BLOCKING."""
    folder = str(getattr(row, "folder", "") or "")
    since = _created(row)
    out: dict[str, Any] = {
        "source": "none", "words": "", "since": _iso(since), "changes": [],
        "files": 0, "added": 0, "removed": 0, "truncated_files": False, "by_tool": 0,
        "no_undo": NO_UNDO.format(tool=tool),
    }
    if not folder:
        out["words"] = NO_FOLDER
        return out
    # A network spelling is refused from its TEXT, before any stat or git call
    # (a stat of \\host\share is an SMB connection — the v1.379.0 lesson).
    if _network(folder):
        out["words"] = ON_SHARE
        return out
    if not os.path.isdir(folder):
        out["words"] = FOLDER_GONE
        return out
    try:
        got = git_changes(folder, since)
    except GitUnreadable as gone:
        out["words"] = gone.words
        return out
    if got is not None:
        mine = tool_paths(events, folder)
        for c in got["changes"]:
            c["by_tool"] = _key(c["path"]) in mine
        out.update(source="git", words=GIT_WORDS, base=got["base"])
    else:
        from ..harness import branches as B

        got = tool_changes(events, folder)
        words = NO_GIT_WORDS if B.git_path() is None else TOOL_WORDS
        out.update(source="tool", words=words.format(tool=tool))
    changes = got["changes"]
    out.update(
        changes=changes,
        files=len(changes),
        added=sum(int(c.get("added") or 0) for c in changes),
        removed=sum(int(c.get("removed") or 0) for c in changes),
        truncated_files=bool(got.get("truncated_files")),
        by_tool=sum(1 for c in changes if c.get("by_tool")),
    )
    return out


# --------------------------------------------------------------------------- #
# USAGE
# --------------------------------------------------------------------------- #


def _usage_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in events if e.get("type") == "usage"]


def totals(events: list[dict[str, Any]]) -> dict[str, Any]:
    """The session's totals over its ``usage`` events (each one reply)."""
    rows = _usage_events(events)

    def n(e: dict[str, Any], k: str) -> int:
        v = e.get(k)
        return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0

    costs = [float(e["cost_usd"]) for e in rows
             if isinstance(e.get("cost_usd"), (int, float)) and not isinstance(e.get("cost_usd"), bool)]
    by = sorted({str(e.get("cost_reported_by") or "") for e in rows if e.get("cost_reported_by")})
    return {
        "replies": len(rows),
        "input": sum(n(e, "input") for e in rows),
        "output": sum(n(e, "output") for e in rows),
        "cache_read": sum(n(e, "cache_read") for e in rows),
        "cost_usd": round(sum(costs), 6) if costs else None,
        "list_price": any(e.get("list_price") is True for e in rows),
        "cost_reported_by": by[0] if len(by) == 1 else "",
    }


def seen_windows(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The windows the TOOL last reported (newest ``usage`` event that had
    them), with when."""
    for e in reversed(_usage_events(events)):
        w = e.get("windows")
        if isinstance(w, list) and w:
            clean = []
            now = _now()
            for x in w:
                if not isinstance(x, dict) or not isinstance(x.get("percent"), (int, float)):
                    continue
                # v1.414.0 review: a window whose reset has passed is over —
                # its old percent is not what is left (Iron-Proxy's windows
                # are dropped the same way in account_usage).
                ends = _when(x.get("resets_at"))
                if ends is not None and ends <= now:
                    continue
                clean.append({
                    "key": str(x.get("key") or "")[:40],
                    "label": str(x.get("label") or "")[:60],
                    "percent": int(x["percent"]),
                    "resets_at": x.get("resets_at") if isinstance(x.get("resets_at"), str) else None,
                })
            if clean:
                return {"windows": clean, "at": e.get("at"), "plan": str(e.get("plan") or "")[:40]}
    return None


def _now() -> datetime:
    """Now, UTC (a seam: a recorded frame's windows are judged against the
    time they were recorded in a test)."""
    return datetime.now(timezone.utc)


def _when(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        t = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo is not None else t.replace(tzinfo=timezone.utc)


def seen_context(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    for e in reversed(_usage_events(events)):
        c = e.get("context")
        if isinstance(c, dict):
            used, window = c.get("used"), c.get("window")
            if isinstance(used, int) and isinstance(window, int) and window > 0 and used >= 0:
                return {
                    "used": used, "window": window,
                    "percent": max(0, min(100, int(round(100 * used / window)))),
                    "at": e.get("at"),
                }
    return None


def account_usage(platform: Any, row: Any) -> dict[str, Any] | None:
    """The session's Iron-Proxy account from the CACHED snapshot: name, plan,
    status and EVERY window Iron-Proxy holds. None = this PC's own login."""
    from ..harness import build_accounts as BA
    from . import store

    account = str(getattr(row, "account", "") or "")
    if not account:
        return None
    svc = getattr(platform, "iron_proxy", None)
    rec = store.account_record(row)
    acc = BA.session_account(svc, account, str(rec.get("title") or "")) or {"id": account}
    out: dict[str, Any] = {
        "id": account,
        "name": str(acc.get("name") or rec.get("title") or account),
        "plan_label": acc.get("plan_label"),
        "status": acc.get("status"),
        "resets_at": acc.get("resets_at"),
        "windows": [],
    }
    now = _now()
    for w in BA.state_of(svc, account).get("windows") or []:
        if not isinstance(w, dict):
            continue
        used = w.get("usedPercent")
        if isinstance(used, bool) or not isinstance(used, (int, float)):
            continue
        resets = BA._parse(w.get("resetsAt"))
        if resets is not None and resets <= now:
            continue
        out["windows"].append({
            "label": SEEN.window_label(w.get("windowMins"), w.get("kind")),
            "percent": int(round(float(used))),
            "resets_at": BA._iso(resets),
            # v1.414.0 review: when Iron-Proxy SAW this number (its own
            # ``at``) — a number from a call hours ago is shown with its age.
            "at": BA._iso(BA._parse(w.get("at"))),
        })
    seen = [w["at"] for w in out["windows"] if w["at"]]
    # The account's age is its OLDEST number's: never younger than any shown.
    out["at"] = min(seen, key=lambda a: BA._parse(a)) if seen else None
    return out


def usage_view(platform: Any, row: Any, events: list[dict[str, Any]]) -> dict[str, Any]:
    """``GET /build/app/sessions/{id}/usage``. Cached reads only. BLOCKING."""
    return {
        "harness": str(getattr(row, "harness", "") or ""),
        "account": account_usage(platform, row),
        "seen": seen_windows(events),
        "totals": totals(events),
        "context": seen_context(events),
    }


# --------------------------------------------------------------------------- #
# SESSION
# --------------------------------------------------------------------------- #


def branch_of(folder: str) -> dict[str, Any]:
    """``{repo: False}`` | ``{repo: True, branch, detached}``."""
    from ..harness import branches as B

    if not folder or _network(folder) or not os.path.isdir(folder) or B.git_path() is None:
        return {"repo": False}
    got = _git(["rev-parse", "--abbrev-ref", "HEAD"], folder)
    if not got.ok:
        inside = _git(["rev-parse", "--is-inside-work-tree"], folder)
        if inside.ok and inside.out.strip() == "true":
            return {"repo": True, "branch": "", "detached": False, "no_commits": True}
        return {"repo": False}
    name = got.out.strip()
    return {"repo": True, "branch": "" if name == "HEAD" else name, "detached": name == "HEAD"}


def rule_files(folder: str) -> list[str]:
    if not folder or _network(folder):
        return []
    out = []
    for name in RULE_FILES:
        try:
            if os.path.isfile(os.path.join(folder, name)):
                out.append(name)
        except OSError:
            continue
    return out


def program_listing(pid: int | None) -> dict[str, Any]:
    """The programs running under the tool's own process right now (its
    helpers included), names and a masked command line, as ``{programs,
    known, more}``. v1.414.0 review: ``known`` False = the listing could not
    be read (never "nothing is running"); ``more`` = cut at
    :data:`MAX_PROGRAMS`."""
    if not pid:
        return {"programs": [], "known": True, "more": False}
    try:
        import psutil
    except ImportError:  # pragma: no cover - a dependency
        return {"programs": [], "known": False, "more": False}
    try:
        kids = psutil.Process(int(pid)).children(recursive=True)
    except (psutil.Error, OSError, ValueError):
        return {"programs": [], "known": False, "more": False}
    from ..harness.process import _shown_command

    out: list[dict[str, Any]] = []
    more = False
    for p in kids:
        try:
            name = p.name()
            if name.lower() in _PLUMBING:
                continue
            if len(out) >= MAX_PROGRAMS:
                more = True
                break
            try:
                cmd = subprocess.list2cmdline(p.cmdline())
            except (psutil.Error, OSError):
                cmd = ""
            # v1.414.0 review: the Session tab's v1.404.0 rule — the word after
            # a secret-named flag (--token VALUE, --api-key VALUE) hidden, the
            # known shapes masked, THEN the cut (a key never shows).
            out.append({"pid": p.pid, "name": name[:80], "command": _shown_command(cmd)})
        except (psutil.Error, OSError):
            continue
    return {"programs": out, "known": True, "more": more}


def programs_under(pid: int | None) -> list[dict[str, Any]]:
    """:func:`program_listing`'s programs alone."""
    return program_listing(pid)["programs"]


def session_view(row: Any, events: list[dict[str, Any]], pid: int | None, running: bool) -> dict[str, Any]:
    """``GET /build/app/sessions/{id}/overview``. BLOCKING."""
    folder = str(getattr(row, "folder", "") or "")
    present = bool(folder) and not _network(folder) and os.path.isdir(folder)
    listing = program_listing(pid) if running else {"programs": [], "known": True, "more": False}
    return {
        "folder": folder,
        "folder_exists": present,
        "on_share": bool(folder) and _network(folder),
        "git": branch_of(folder) if present else {"repo": False},
        "rules": rule_files(folder) if present else [],
        "context": seen_context(events),
        "tool_running": bool(running),
        "programs": listing["programs"],
        "programs_known": listing["known"],
        "programs_more": listing["more"],
    }
