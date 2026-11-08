"""v1.453.0: the short Markdown summary a Build › Code session keeps when it
is ARCHIVED (the owner: "a general summary … in markdown (to avoid bloat) …
what project was worked on, what state the project was in before any work
was done and what state the project was in after it finished").

Built from the session's OWN record only — its events log
(``<home>/hosted/<id>/events.jsonl``) and git in its folder — with NO model
call. The house rule for written summaries (CLAUDE.md "Compaction is
MODEL-written and LEDGER-checked") is that a summary may never claim a fact
the record does not support; a deterministic summary meets it by
construction: every line below is either a fact read from the log or git,
or a span QUOTED verbatim from the log and marked as a quotation. What the
record cannot tell is SAID ("not a git repository", "the log dropped older
events"), never guessed.

Every string from the log or git is masked (``detections.redact.mask``)
before it is cut, and the whole document is masked once more at the end.

The file is ``<home>/hosted/<id>/summary.md`` beside the events log: it goes
with the session (a delete moves the folder to Iron Jarvis's trash), stays
when the session is brought back, and a later archive writes it again.
BLOCKING throughout (git calls are bounded by ``harness.branches._g``).
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..core.ids import utcnow
from ..detections.redact import mask
from .events import session_dir

log = logging.getLogger(__name__)

FILE_NAME = "summary.md"
#: The document's bound (characters): "to avoid bloat".
MAX_CHARS = 8_000
#: How many asks, files, commits and open plan steps are listed.
MAX_ASKS = 8
MAX_FILES = 15
MAX_COMMITS = 10
MAX_CHECKS = 6
#: One-line cuts.
ASK_CHARS = 200
FIRST_ASK_CHARS = 400
LAST_REPLY_CHARS = 700

#: A command whose own result is a CI check (GitHub's command line).
_CI_RX = re.compile(r"\bgh\s+(?:run\s+(?:watch|view|list)|pr\s+checks)\b", re.IGNORECASE)
_PUSH_RX = re.compile(r"\bgit\s+push\b", re.IGNORECASE)


class SummaryNotWritten(Exception):
    """The summary could not be written; ``words`` says why, plainly."""

    def __init__(self, words: str) -> None:
        super().__init__(words)
        self.words = words


def summary_path(home: Any, sid: str) -> Path:
    return session_dir(home, sid) / FILE_NAME


def read_summary(home: Any, sid: str) -> dict[str, Any] | None:
    """BLOCKING. ``{text, path, at}`` of the kept summary, or None."""
    path = summary_path(home, sid)
    try:
        text = path.read_text(encoding="utf-8")
        at = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return None
    return {"text": text, "path": str(path), "at": _iso(at)}


def summary_facts(home: Any, sid: str) -> dict[str, Any] | None:
    """BLOCKING, cheap (one stat). ``{path, at}`` when a summary is kept."""
    path = summary_path(home, sid)
    try:
        at = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return None
    return {"path": str(path), "at": _iso(at)}


def write_summary(home: Any, row: Any, events: list[dict[str, Any]], *, dropped: int = 0,
                  engine: Any = None, now: datetime | None = None) -> dict[str, Any]:
    """BLOCKING. Build and write the summary (temp + replace); ``{text, path,
    at}``. Raises :class:`SummaryNotWritten` when the file cannot be written."""
    text = build_summary(row, events, dropped=dropped, engine=engine, now=now)
    path = summary_path(home, row.id)
    tmp = path.with_name(f"{FILE_NAME}.{os.getpid()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(text, encoding="utf-8", newline="\n")
        os.replace(tmp, path)
    except OSError as exc:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise SummaryNotWritten(f"The archive summary could not be saved: {mask(str(exc))[:200]}") from None
    return {"text": text, "path": str(path), "at": _iso(now or utcnow())}


# --------------------------------------------------------------------------- #
# building it
# --------------------------------------------------------------------------- #


def build_summary(row: Any, events: list[dict[str, Any]], *, dropped: int = 0, engine: Any = None,
                  now: datetime | None = None) -> str:
    """The Markdown text. Pure except for git in the session's folder and the
    project lookup (both read-only)."""
    from . import words as W

    tool = W.label(str(getattr(row, "harness", "") or ""))
    folder = str(getattr(row, "folder", "") or "")
    title = _one(getattr(row, "title", "") or "", 120) or "Untitled session"
    own = [e for e in events if isinstance(e, dict) and not e.get("parent")]
    asks = [e for e in own if e.get("type") == "user" and str(e.get("text") or "").strip()]
    replies = [e for e in own if e.get("type") == "done"]
    git = _git_facts(folder, _created(row)) if folder else {"state": "no_folder"}

    out: list[str] = [f"# {title}", ""]
    # -- who worked where --------------------------------------------------------
    project = _project_name(engine, folder)
    out.append(f"- **Folder:** {_code(folder) if folder else 'none (no folder was ever made)'}")
    if project:
        out.append(f"- **Project:** {project}")
    out.append(f"- **Tool:** {tool}" + (f", model {_code(str(row.model))}" if getattr(row, "model", "") else ""))
    out.append(f"- **Account:** {_account_words(row)}")
    out.append(f"- **Session:** started {_when(_created(row))}, archived {_when(now or utcnow())}")
    out.append("")

    # -- before --------------------------------------------------------------------
    out.append("## Before")
    out.extend(_before_lines(git))
    if asks:
        first = asks[0]
        lead = "The first message in the log" if dropped else "The first message"
        out.append(f"- {lead} asked:")
        out.append(_quote(_one(first.get("text"), FIRST_ASK_CHARS)))
    else:
        out.append("- No messages from this view are in the session's log.")
    if dropped:
        out.append(f"- The log had dropped {dropped} older event{'s' if dropped != 1 else ''}, so earlier "
                   "messages are not listed.")
    out.append("")

    # -- what was done -------------------------------------------------------------
    out.append("## What was done")
    if asks:
        out.append(f"- Messages sent: {len(asks)}" + _replies_words(replies))
        later = asks[1:]
        if later:
            out.append("- Later asks:")
            for ev in later[-MAX_ASKS:]:
                out.append(f"  - {_one(ev.get('text'), ASK_CHARS)}")
            if len(later) > MAX_ASKS:
                out.append(f"  - …and {len(later) - MAX_ASKS} earlier")
    out.extend(_files_lines(git, own, folder))
    out.extend(_commit_lines(git))
    out.extend(_check_lines(own))
    errors = [e for e in own if e.get("type") == "error"]
    if errors:
        last = _one(errors[-1].get("title") or errors[-1].get("technical") or "", 200)
        out.append(f"- Errors in the log: {len(errors)}" + (f" (the last: {last})" if last else ""))
    out.append("")

    # -- after ---------------------------------------------------------------------
    out.append("## After")
    out.extend(_after_lines(git))
    open_steps = _open_plan_steps(own)
    if open_steps:
        out.append("- Plan steps not marked done:")
        for step in open_steps[:MAX_ASKS]:
            out.append(f"  - {step}")
    last_reply = _last_reply(own)
    if last_reply:
        out.append(f"- The last reply from {tool} ended:")
        out.append(_quote(last_reply))
    out.append("")
    out.append("_Written by Iron Jarvis from this session's own log and git when it was archived; "
               "no AI wrote this summary._")

    text = mask("\n".join(out)).rstrip() + "\n"
    if len(text) > MAX_CHARS:
        text = text[: MAX_CHARS - 40].rstrip() + "\n\n_(cut to keep it short)_\n"
    return text


# --------------------------------------------------------------------------- #
# the pieces
# --------------------------------------------------------------------------- #


def _before_lines(git: dict[str, Any]) -> list[str]:
    state = git.get("state")
    if state != "ok":
        return [f"- {_git_state_words(state, git)}"]
    base = git.get("base") or {}
    if not base:
        return ["- The repository had no commits yet when the session started."]
    lines = [f"- Last commit before the session started: {_commit_words(base)}"]
    return lines


def _after_lines(git: dict[str, Any]) -> list[str]:
    state = git.get("state")
    if state != "ok":
        return [f"- {_git_state_words(state, git)}"]
    lines: list[str] = []
    branch = git.get("branch") or ""
    head = git.get("head") or {}
    if head:
        where = f"on branch {_code(branch)}" if branch and branch != "HEAD" else "with no branch checked out"
        lines.append(f"- Ended {where} at commit {_commit_words(head)}")
    elif branch:
        lines.append(f"- Ended on branch {_code(branch)} (no commits yet)")
    dirty = git.get("uncommitted")
    if dirty is None:
        lines.append("- git could not say whether files were left uncommitted.")
    elif dirty:
        lines.append(f"- Left uncommitted: {dirty} file{'s' if dirty != 1 else ''}")
    else:
        lines.append("- Nothing was left uncommitted.")
    ahead = git.get("ahead")
    if isinstance(ahead, int) and ahead > 0:
        lines.append(f"- Not pushed yet: {ahead} commit{'s' if ahead != 1 else ''} ahead of "
                     f"{_code(str(git.get('upstream') or 'its upstream'))}")
    elif isinstance(ahead, int) and git.get("upstream"):
        lines.append(f"- Every commit is pushed to {_code(str(git.get('upstream')))}.")
    return lines


def _files_lines(git: dict[str, Any], events: list[dict[str, Any]], folder: str) -> list[str]:
    if git.get("state") == "ok" and git.get("changes") is not None:
        changes = git["changes"]
        if not changes:
            return ["- No files differ from where git was when the session started."]
        lines = [f"- Files that differ from the start (committed or not; git cannot tell your own edits "
                 f"or another program's from the tool's): {len(changes)}"
                 + (" or more" if git.get("changes_more") else "")]
        for c in changes[:MAX_FILES]:
            lines.append(f"  - {c['status']}: {_code(c['rel'])}")
        if len(changes) > MAX_FILES:
            lines.append(f"  - …and {len(changes) - MAX_FILES} more")
        return lines
    # No git: the files the tool's own file steps reported changing.
    from .panes import tool_paths

    try:
        paths = tool_paths(events, folder)
    except Exception:  # noqa: BLE001 — a summary never fails over this
        paths = {}
    if not paths:
        return []
    names = []
    for evs in paths.values():
        p = str(evs[-1].get("path") or "")
        names.append(os.path.relpath(p, folder) if folder and os.path.isabs(p) else p)
    lines = [f"- Files the tool's own edit steps changed: {len(names)}"]
    for name in names[:MAX_FILES]:
        lines.append(f"  - {_code(name)}")
    if len(names) > MAX_FILES:
        lines.append(f"  - …and {len(names) - MAX_FILES} more")
    return lines


def _commit_lines(git: dict[str, Any]) -> list[str]:
    if git.get("state") != "ok":
        return []
    commits = git.get("commits")
    if commits is None:
        return []
    if not commits:
        return ["- No commits were made after the session started."]
    lines = [f"- Commits after the session started: {len(commits)}" + (" or more" if git.get("commits_more") else "")]
    for c in commits[:MAX_COMMITS]:
        lines.append(f"  - {_commit_words(c)}")
    return lines


def _check_lines(events: list[dict[str, Any]]) -> list[str]:
    """Test runs, CI checks and pushes the tool's own command steps show,
    in the record's words: a step "finished without an error" or "ended with
    an error" — never "passed", which the log cannot prove."""
    from ..harness.finish import status_is_tests

    runs: list[tuple[str, str, str]] = []  # (what, command, status)
    for ev in events:
        if ev.get("type") != "tool" or ev.get("kind") != "command" or ev.get("partial"):
            continue
        status = str(ev.get("status") or "")
        if status not in ("done", "failed"):
            continue
        cmd = _command_of(ev)
        if not cmd:
            continue
        try:
            is_test = status_is_tests(cmd)
        except Exception:  # noqa: BLE001
            is_test = False
        if is_test:
            runs.append(("test run", cmd, status))
        elif _CI_RX.search(cmd):
            runs.append(("CI check", cmd, status))
        elif _PUSH_RX.search(cmd):
            runs.append(("push", cmd, status))
    if not runs:
        return []
    lines = ["- Test runs, CI checks and pushes the log shows (the newest last):"]
    for what, cmd, status in runs[-MAX_CHECKS:]:
        verdict = "finished without an error" if status == "done" else "ended with an error"
        lines.append(f"  - {what} {_code(_one(cmd, 140))}: {verdict}")
    return lines


def _open_plan_steps(events: list[dict[str, Any]]) -> list[str]:
    plans = [e for e in events if e.get("type") == "plan" and isinstance(e.get("steps"), list)]
    if not plans:
        return []
    out = []
    for step in plans[-1]["steps"]:
        if not isinstance(step, dict):
            continue
        if str(step.get("status") or "") in ("completed", "done"):
            continue
        text = _one(step.get("text"), 160)
        if text:
            out.append(text)
    return out


def _last_reply(events: list[dict[str, Any]]) -> str:
    """The END of the tool's last reply, quoted (its last paragraphs, up to
    :data:`LAST_REPLY_CHARS`)."""
    for ev in reversed(events):
        if ev.get("type") == "text_done":
            text = mask(str(ev.get("text") or "")).strip()
            if not text:
                continue
            paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
            kept: list[str] = []
            size = 0
            for p in reversed(paras):
                if kept and size + len(p) > LAST_REPLY_CHARS:
                    break
                kept.insert(0, p)
                size += len(p)
            joined = "\n\n".join(kept)
            if len(joined) > LAST_REPLY_CHARS:
                joined = "…" + joined[-(LAST_REPLY_CHARS - 1):]
            return joined
    return ""


def _replies_words(replies: list[dict[str, Any]]) -> str:
    if not replies:
        return ""
    stops: dict[str, int] = {}
    for ev in replies:
        stop = str(ev.get("stop") or "completed")
        stops[stop] = stops.get(stop, 0) + 1
    parts = [f"replies: {len(replies)}"]
    for stop, word in (("interrupted", "stopped"), ("failed", "failed"), ("max_turns", "cut off at a limit")):
        if stops.get(stop):
            parts.append(f"{stops[stop]} {word}")
    return " · " + ", ".join(parts)


def _command_of(ev: dict[str, Any]) -> str:
    sub = str(ev.get("subtitle") or "").strip()
    if sub:
        return sub
    raw = str(ev.get("input") or "").strip()
    if raw.startswith("{"):
        try:
            data = json.loads(raw)
        except ValueError:
            data = None
        if isinstance(data, dict):
            for key in ("command", "cmd"):
                val = data.get(key)
                if isinstance(val, list):
                    val = " ".join(str(v) for v in val)
                if isinstance(val, str) and val.strip():
                    return val.strip()
    return raw or str(ev.get("title") or "").strip()


# --------------------------------------------------------------------------- #
# git
# --------------------------------------------------------------------------- #


def _git_facts(folder: str, since: datetime) -> dict[str, Any]:
    """What git can say about the folder, read-only and bounded. ``state``:
    ok | no_folder | gone | share | no_git | not_repo | unreadable."""
    from ..harness import branches as B
    from . import panes as PN

    if PN._network(folder):
        return {"state": "share"}
    if not os.path.isdir(folder):
        return {"state": "gone"}
    if B.git_path() is None:
        return {"state": "no_git"}
    try:
        base = PN._base_commit(folder, since)
    except PN.GitUnreadable as exc:
        return {"state": "unreadable", "why": exc.words}
    except Exception:  # noqa: BLE001 — a summary never fails over git
        log.warning("archive summary: git could not read the folder", exc_info=True)
        return {"state": "unreadable", "why": ""}
    if base is None:
        return {"state": "not_repo"}
    facts: dict[str, Any] = {"state": "ok"}
    empty = _empty_tree(folder)
    facts["base"] = {} if base == empty else _commit(folder, base)
    head = PN._git(["rev-parse", "--verify", "-q", "HEAD"], folder)
    head_sha = head.out.strip() if head.ok else ""
    facts["head"] = _commit(folder, head_sha) if head_sha else {}
    br = PN._git(["rev-parse", "--abbrev-ref", "HEAD"], folder)
    facts["branch"] = br.out.strip() if br.ok else ""
    # Commits after the start (on HEAD's history).
    if head_sha:
        rng = [head_sha] if base == empty else [f"{base}..{head_sha}"]
        got = PN._git(["log", f"-{MAX_COMMITS + 1}", "--format=%h%x1f%s%x1f%cI", *rng], folder)
        if got.ok:
            commits = [_parse_commit(line) for line in got.out.splitlines() if line.strip()]
            facts["commits_more"] = len(commits) > MAX_COMMITS
            facts["commits"] = [c for c in commits if c][:MAX_COMMITS]
    else:
        facts["commits"] = []
    # Files that differ from the start (committed or not, new ones too).
    try:
        ch = PN.git_changes(folder, since, base=base)
    except Exception:  # noqa: BLE001
        ch = None
    if ch is not None:
        facts["changes"] = [{"status": c.get("status", "modified"), "rel": c.get("rel", "")} for c in ch["changes"]]
        facts["changes_more"] = bool(ch.get("truncated_files"))
    st = PN._git(["status", "--porcelain=v1", "-z", "--untracked-files=normal"], folder)
    facts["uncommitted"] = _status_count(st.out) if st.ok else None
    up = PN._git(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], folder)
    if up.ok and up.out.strip():
        facts["upstream"] = up.out.strip()
        cnt = PN._git(["rev-list", "--count", "@{u}..HEAD"], folder)
        if cnt.ok and cnt.out.strip().isdigit():
            facts["ahead"] = int(cnt.out.strip())
    return facts


def _status_count(out: str) -> int:
    """Entries of ``git status --porcelain=v1 -z`` (a rename's or copy's
    second path is part of its entry, not one of its own)."""
    parts = out.split("\0")
    count = 0
    i = 0
    while i < len(parts):
        entry = parts[i]
        i += 1
        if len(entry) < 4:
            continue
        count += 1
        if entry[0] in ("R", "C"):
            i += 1
    return count


def _empty_tree(folder: str) -> str:
    from . import panes as PN

    got = PN._git(["hash-object", "-t", "tree", "--stdin"], folder, input=b"")
    return got.out.strip() if got.ok else ""


def _commit(folder: str, sha: str) -> dict[str, str]:
    from . import panes as PN

    got = PN._git(["log", "-1", "--format=%h%x1f%s%x1f%cI", sha], folder)
    if not got.ok:
        return {"sha": sha[:9], "subject": "", "when": ""}
    return _parse_commit(got.out.strip()) or {"sha": sha[:9], "subject": "", "when": ""}


def _parse_commit(line: str) -> dict[str, str] | None:
    bits = line.split("\x1f")
    if len(bits) < 2 or not bits[0].strip():
        return None
    return {"sha": bits[0].strip(), "subject": _one(bits[1], 120), "when": bits[2].strip() if len(bits) > 2 else ""}


def _commit_words(c: dict[str, str]) -> str:
    out = _code(c.get("sha") or "?")
    if c.get("subject"):
        out += f" “{c['subject']}”"
    when = c.get("when") or ""
    if when:
        try:
            out += f" ({_when(datetime.fromisoformat(when))})"
        except ValueError:
            pass
    return out


def _git_state_words(state: Any, git: dict[str, Any]) -> str:
    return {
        "no_folder": "The session never had a folder, so there is no project state to compare.",
        "gone": "The session's folder is not there any more, so its state cannot be read.",
        "share": "The session's folder is on a network share, so git was not read there.",
        "no_git": "git is not installed on this PC, so the folder's state is not recorded.",
        "not_repo": "The folder is not a git repository, so its state is not recorded beyond the messages.",
        "unreadable": "git could not read this folder" + (f": {git.get('why')}" if git.get("why") else "."),
    }.get(str(state), "The folder's state could not be read.")


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #


def _project_name(engine: Any, folder: str) -> str:
    if engine is None or not folder:
        return ""
    try:
        from ..projects.locate import project_for_path

        proj = project_for_path(engine, folder)
    except Exception:  # noqa: BLE001
        return ""
    return _one(getattr(proj, "name", "") or "", 80) if proj is not None else ""


def _account_words(row: Any) -> str:
    from .store import account_record

    try:
        acc = account_record(row)
    except Exception:  # noqa: BLE001
        acc = {}
    name = str((acc or {}).get("title") or "").strip()
    if name:
        return _one(name, 80)
    if getattr(row, "account", ""):
        return "an account of the account manager"
    return "this PC's own sign-in"


def _created(row: Any) -> datetime:
    when = getattr(row, "created_at", None)
    if not isinstance(when, datetime):
        return utcnow()
    return when if when.tzinfo is not None else when.replace(tzinfo=timezone.utc)


def _when(when: datetime) -> str:
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _iso(when: datetime) -> str:
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _one(text: Any, cap: int) -> str:
    """One line: masked FIRST, whitespace folded, then cut."""
    value = " ".join(mask(str(text if text is not None else "")).split())
    if len(value) <= cap:
        return value
    return value[: max(0, cap - 1)] + "…"


def _code(text: str) -> str:
    value = str(text).replace("\n", " ")
    fence = "``" if "`" in value else "`"
    pad = " " if value.startswith("`") or value.endswith("`") else ""
    return f"{fence}{pad}{value}{pad}{fence}"


def _quote(text: str) -> str:
    return "\n".join(f"  > {line}" if line.strip() else "  >" for line in str(text).splitlines() or [""])
