"""22-b (R3.4 of ``docs/BUILD-OUT-PLAN-2026-11-20.md``): COMMIT and OPEN A
PULL REQUEST for a Build › Code session, one press each.

Two kinds of session, decided when the session is made and never after
(``HostedSessionRecord.git_json``, written only by the create and the git
routes, ``routes/build_app_git``):

* ON ITS OWN BRANCH (the user said yes to "Work on a new branch in this
  folder?", D-22.1: offered once per folder, never the default). The create
  runs ``harness/branches.start`` off the loop; the session's ``folder`` is its
  own copy of the folder (a worktree beside the repository) and the row keeps
  the very facts Chat › Code keeps. Commit, push, pull request, merge and
  discard are the SAME ``branches`` functions ``routes/build_git`` calls. The
  user's main checkout is touched by Merge only.
* IN THE FOLDER DIRECTLY (the default: "Build › Code keeps working in your
  folder"). Commit lists every changed file of the repository with a tick
  (the git source also counts other programs' changes, ``panes.py`` 9-20) and
  commits ONLY the ticked ones (``git commit --only -F - -- <paths>``): what
  the user staged for any other path stays staged, untouched. A pull request
  is offered only when the folder is NOT on the repository's main branch
  (:data:`ON_MAIN_PR`); its branch is pushed as it is (never a force, no
  upstream written into the user's config).

THE DRAFT (:func:`draft_message`) is written from the session's own record:
the user's first message and the files. No model call, ever (the plan: "never
by a model the user did not pick").

THE TICKS (:func:`ours`): a file is ticked when it changed since the
session's FIRST message (the checkpoint tree ``panes.record_turn_start``
kept); a file that was already changed before the session started, or that
no checkpoint covers, is listed unticked. Without a checkpoint, the files the
tool's own file tools reported changing are ticked.

Every function here is BLOCKING (git through ``core.procs``, bounded): the
routes hop them off the loop.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from ..harness import branches as B

#: The record kind a session in the folder directly keeps (its pull request).
DIRECT = "direct"
#: How many changed files Commit lists, at most (the rest is said).
MAX_LISTED = 200
#: The draft's summary line, at most (git's own convention).
SUMMARY_CHARS = 72
#: How much of the first message the draft's body keeps.
BODY_CHARS = 1_000
#: How many file names the draft lists.
DRAFT_FILES = 20

# --------------------------------------------------------------------------- #
# words
# --------------------------------------------------------------------------- #
ON_MAIN_PR = "Start the next session on its own branch to open a pull request."
NOT_A_REPO_HERE = "This folder is not in a git repository, so there is nothing to commit here."
NO_FOLDER_YET = "This session has no folder yet, so there is nothing to commit."
TICK_FIRST = "Tick the files to commit first."
NOT_CHANGED = "{path} is not one of this folder's changed files now, so nothing was committed."
NOTHING_CHANGED = "Nothing to commit: this folder has no changes that are not committed yet."
DETACHED_HERE = (
    "This folder is on a single commit, not on a branch, so nothing was committed. Switch it to a "
    "branch first."
)
NO_MERGE_HERE = "This session works in your folder directly, so there is no branch of its own to merge."
NO_DISCARD_HERE = "This session works in your folder directly; it has no copy of the folder to discard."
NO_DIFF_HERE = "This session works in your folder directly; its changes are in the Changes tab."
CHECKING_NOW = (
    "Iron Jarvis is checking this reply's tests. Wait for its line, or stop the check, first."
)
BRANCH_NEEDS_FOLDER = "Choose a folder to work on a new branch in."
BRANCH_NO_RESUME = "A saved conversation goes on in its own folder, so it cannot start on a new branch."
NO_MAIN = (
    "Iron Jarvis could not tell which branch is this repository's main one, so no pull request is "
    "offered from here."
)
#: 22-b review: a branch session's copy of the folder that another session
#: works in too ("Branch from here" made one there) is never moved under it.
COPY_SHARED = (
    "{who} works in this copy of the folder too, so nothing was discarded. Delete that session first, "
    "then discard."
)
#: A delete refused because the copy could not be moved (its words first).
NOT_DELETED = "The session was not deleted; its coding tool was stopped."
#: The claim a git action holds on the session (``service._claim``: "This
#: session is <what> — try again in a moment.").
CLAIM = "saving its work in git"
#: The fallback summary when the record holds no message.
NO_MESSAGE = "Changes from a coding session"


# --------------------------------------------------------------------------- #
# the row's facts
# --------------------------------------------------------------------------- #
def record_of(row: Any) -> dict[str, Any]:
    """``git_json`` as stored ({} for none or unreadable)."""
    try:
        data = json.loads(str(getattr(row, "git_json", "") or "") or "{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def facts_of(row: Any) -> "dict[str, str] | None":
    """The facts of a session on its OWN branch (``branches`` whitelist), or
    None for a session that works in its folder directly."""
    rec = record_of(row)
    if rec.get(DIRECT):
        return None
    return B.clean_git_record(rec)


def checkout_of(row: Any) -> str:
    """The folder the user PICKED: a branch session's checkout, else the
    session's own folder. Grouping, the folder's defaults and the folder's
    check consent are keyed on this."""
    facts = facts_of(row)
    if facts and facts.get("checkout"):
        return facts["checkout"]
    return str(getattr(row, "folder", "") or "")


def checkout_from(folder: Any, git_json: Any) -> str:
    """:func:`checkout_of` from the two columns alone (readers that select
    columns, not rows: the work record, a project's sessions)."""
    class _Row:  # noqa: D401 — a stand-in with the two fields
        pass

    row = _Row()
    row.folder = str(folder or "")  # type: ignore[attr-defined]
    row.git_json = str(git_json or "")  # type: ignore[attr-defined]
    return checkout_of(row)


def view(row: Any) -> "dict[str, Any] | None":
    """``git`` on the session's view (a branch session only), no git call."""
    return B.view(facts_of(row))


def shared_json(row: Any) -> str:
    """22-b review: the git facts a session made IN ``row``'s own copy of the
    folder carries ("Branch from here", a helper it hands work to): the same
    branch and the same copy, so it is listed under the folder the user
    picked, borrows the main folder's environment and commits on that branch.
    "" for a session in its folder directly (or whose copy is gone)."""
    facts = facts_of(row)
    if not facts or facts.get("removed_at"):
        return ""
    return json.dumps(facts, sort_keys=True)


def others_in_copy(engine: Any, row: Any) -> list[Any]:
    """BLOCKING: the OTHER sessions whose folder is this branch session's own
    copy of the folder (archived ones too: an archived session keeps its
    folder, so unarchiving finds it) — a branch made with "Branch from here"
    works there too. A helper THIS session started is its own work (it stops
    with it), never another session's. [] for any other session."""
    facts = facts_of(row)
    copy = str(getattr(row, "folder", "") or "")
    if not facts or facts.get("removed_at") or not copy:
        return []
    from . import store
    from .service import same_folder

    sid = str(getattr(row, "id", "") or "")
    return [r for r in store.list_rows(engine, archived=True)
            if r.id != sid and same_folder(r.folder, copy) and str(getattr(r, "handed_from", "") or "") != sid]


def shared_refusal(others: list[Any]) -> str:
    """:data:`COPY_SHARED` for the first of ``others``."""
    title = " ".join(str(getattr(others[0], "title", "") or "").split())[:120]
    return COPY_SHARED.format(who=f"The session “{title}”" if title else "Another session")


_PR_URL = re.compile(r"^https://[^\s/]+/\S+/pull/\d+$")


def direct_pr(row: Any) -> "dict[str, Any] | None":
    """The pull request a session in the folder directly opened, or None."""
    rec = record_of(row)
    if not rec.get(DIRECT):
        return None
    url = str(rec.get("pr_url") or "")
    if not _PR_URL.match(url):
        return None
    number = str(rec.get("pr_number") or "")
    return {"url": url, "number": int(number) if number.isdigit() else None,
            "branch": str(rec.get("branch") or "")[:300]}


def tool_env(row: Any, env: dict[str, str]) -> dict[str, str]:
    """The coding tool's environment for a session on its own branch: the
    main folder's ``.venv`` / ``node_modules`` in front (``main_env.find``,
    read, never copied), as a Chat › Code branch does. ``env`` itself for any
    other session or when there is nothing to borrow. BLOCKING (stats);
    never raises."""
    facts = facts_of(row)
    if not facts or facts.get("removed_at"):
        return env
    try:
        from ..harness.main_env import find

        found = find(facts.get("checkout", ""), str(getattr(row, "folder", "") or ""), facts.get("repo", ""),
                     facts.get("worktree", ""))
    except Exception:  # noqa: BLE001 — nothing borrowed
        return env
    return found.apply(env) if found else env


# --------------------------------------------------------------------------- #
# the draft, from the record
# --------------------------------------------------------------------------- #
def first_message(events: list[dict[str, Any]]) -> str:
    """The user's first message of the session (never a hidden carry-on or a
    note typed while it worked)."""
    for ev in events:
        if not isinstance(ev, dict) or ev.get("type") != "user":
            continue
        if ev.get("carry") or ev.get("steer") or ev.get("hidden"):
            continue
        text = str(ev.get("text") or "").strip()
        if text:
            return text
    return ""


def _summary(text: str) -> str:
    line = " ".join(str(text or "").split("\n", 1)[0].split())
    if len(line) > SUMMARY_CHARS:
        cut = line[:SUMMARY_CHARS]
        sp = cut.rfind(" ")
        line = (cut[:sp] if sp > 20 else cut).rstrip(" ,;:-")
    line = line.rstrip(" .:;")
    return (line[:1].upper() + line[1:]) if line else ""


def draft_message(events: list[dict[str, Any]], files: list[str]) -> str:
    """The commit message, from the record: a summary line from the user's
    first message, the message itself when it says more, and the files.
    Masked (a message can hold a pasted key; a commit is pushed)."""
    from ..detections.redact import mask

    asked = str(mask(first_message(events)))
    summary = _summary(asked)
    if not summary:
        names = [f for f in files if f][:3]
        summary = (f"Change {names[0]}" + (f" and {len(files) - 1} more" if len(files) > 1 else "")
                   if names else NO_MESSAGE)
    parts = [summary]
    whole = " ".join(asked.split()).rstrip(" .:;")
    if whole and len(whole) > len(summary):
        # The message says more than its summary line: it is the body.
        body = asked.strip()
        if len(body) > BODY_CHARS:
            body = body[:BODY_CHARS].rstrip() + " …"
        parts.append(body)
    names = [f for f in files if f]
    if names:
        listed = "\n".join(f"- {n}" for n in names[:DRAFT_FILES])
        if len(names) > DRAFT_FILES:
            listed += f"\n- and {len(names) - DRAFT_FILES} more"
        parts.append("Files:\n" + listed)
    return B.clean_message("\n\n".join(parts))


# --------------------------------------------------------------------------- #
# a session in the folder directly
# --------------------------------------------------------------------------- #
def repo_top(folder: str) -> str:
    """The repository's top folder for ``folder``, or ""."""
    if not folder or not os.path.isdir(folder) or B.git_path() is None:
        return ""
    got = B._g(["rev-parse", "--show-toplevel"], folder, reads=True)
    return B._norm(got.out.strip()) if got.ok and got.out.strip() else ""


def current_branch(top: str) -> str:
    got = B._g(["symbolic-ref", "--short", "-q", "HEAD"], top, reads=True)
    return got.out.strip() if got.ok else ""


def main_branch(top: str, remote: str) -> str:
    """The repository's main branch: the remote's own HEAD (``origin/HEAD``),
    else a local ``main``, else ``master``; "" when neither is known."""
    if remote:
        got = B._g(["symbolic-ref", "--short", "-q", f"refs/remotes/{remote}/HEAD"], top, reads=True)
        name = got.out.strip() if got.ok else ""
        if name.startswith(remote + "/") and name[len(remote) + 1:]:
            return name[len(remote) + 1:]
    for name in ("main", "master"):
        if B._ref_sha(Path(top), f"refs/heads/{name}"):
            return name
    return ""


def changed_files(top: str) -> list[dict[str, Any]]:
    """Every changed file of the repository (``git status``: staged or not,
    new files included, ``.gitignore`` honoured), relative to its top:
    ``{rel, status, old?, new}`` — ``new`` = not in git yet. BLOCKING."""
    got = B._g(["status", "--porcelain=v1", "-z", "--untracked-files=all"], top, reads=True)
    if not got.ok:
        raise B.GitRefused("This folder's changes could not be read.", 409, B._masked(got.err))
    out: list[dict[str, Any]] = []
    parts = got.out.split("\0")
    i = 0
    while i < len(parts):
        entry = parts[i]
        i += 1
        if len(entry) < 4:
            continue
        xy, rel = entry[:2], entry[3:]
        row: dict[str, Any] = {"rel": rel, "new": xy == "??"}
        if "R" in xy or "C" in xy:
            row["old"] = parts[i] if i < len(parts) else ""
            i += 1
            row["status"] = "renamed"
        elif xy == "??" or "A" in xy:
            row["status"] = "created"
        elif "D" in xy:
            row["status"] = "deleted"
        else:
            row["status"] = "modified"
        out.append(row)
    return out


def ours(home: Any, sid: str, folder: str, top: str, events: list[dict[str, Any]]) -> "set[str]":
    """The files (relative to ``top``) this session changed: those that differ
    from the folder as it was at its FIRST message (the kept checkpoint);
    without one, those the tool's own file tools reported changing. Never
    raises (nothing known = nothing ticked)."""
    from . import panes as PN

    try:
        first = next((e for e in PN.read_trees(home, sid) if e.get("how") == "start" and e.get("tree")), None)
        if first is not None:
            now = PN.snapshot_tree(folder, keep=PN.kept_index(home, sid))
            got = B._g(["diff", "--no-renames", "--name-only", "-z", str(first["tree"]), now], top, reads=True)
            if got.ok:
                return {p for p in got.out.split("\0") if p}
    except Exception:  # noqa: BLE001 — fall back to the tool's own record
        pass
    out: set[str] = set()
    try:
        for key in PN.tool_paths(events, folder):
            with contextlib.suppress(ValueError):
                rel = os.path.relpath(key, top).replace("\\", "/")
                if not rel.startswith(".."):
                    out.add(rel)
    except Exception:  # noqa: BLE001
        pass
    return out


def _norm_rel(rel: str) -> str:
    return os.path.normcase(str(rel or "").replace("\\", "/"))


def folder_status(home: Any, sid: str, row: Any, events: list[dict[str, Any]]) -> dict[str, Any]:
    """``GET …/git`` for a session in the folder directly. BLOCKING."""
    folder = str(getattr(row, "folder", "") or "")
    if not folder:
        return {"kind": "none", "note": NO_FOLDER_YET}
    top = repo_top(folder)
    if not top:
        return {"kind": "none", "note": NOT_A_REPO_HERE}
    branch = current_branch(top)
    remote = B.pick_remote(Path(top), branch) if branch else ""
    main = main_branch(top, remote)
    rows = changed_files(top)
    mine = {_norm_rel(p) for p in ours(home, sid, folder, top, events)}
    listed = []
    for r in rows[:MAX_LISTED]:
        listed.append({"rel": r["rel"], "status": r["status"], "ticked": _norm_rel(r["rel"]) in mine or
                       bool(r.get("old") and _norm_rel(r["old"]) in mine)})
    ticked = [f["rel"] for f in listed if f["ticked"]]
    out: dict[str, Any] = {
        "kind": "folder", "repo": top, "branch": branch, "main": main, "on_main": bool(branch) and branch == main,
        "files": listed, "more": max(0, len(rows) - MAX_LISTED), "uncommitted": len(rows),
        "remote": remote, "draft": draft_message(events, ticked),
    }
    url = B._remote_url(Path(top), remote)
    out["remote_host"] = (B.github_repo(url) or ("", "", ""))[0] or B._host_of(url)
    head = B._ref_sha(Path(top), "HEAD")
    tracking = B._ref_sha(Path(top), f"refs/remotes/{remote}/{branch}") if remote and branch else ""
    out["pushed"] = bool(tracking) and tracking == head
    base = _base_ref(top, remote, main)
    ahead = B._g(["rev-list", "--count", f"{base}..HEAD"], top, reads=True) if base and head else None
    out["ahead"] = int(ahead.out.strip() or 0) if ahead is not None and ahead.ok else 0
    pr = direct_pr(row)
    out["pr_url"] = pr["url"] if pr and pr.get("branch") == branch else ""
    out["can_pr"], out["pr_note"] = _can_pr(branch, main, remote)
    if out["can_pr"]:
        log_ = B._g(["log", "--format=%h%x1f%s%x1f%b%x1e", "-n", "20", f"{base}..HEAD"], top, reads=True) \
            if base else None
        commits: list[dict[str, str]] = []
        if log_ is not None and log_.ok:
            for rec in log_.out.split("\x1e"):
                bits = rec.strip("\n").split("\x1f")
                if len(bits) >= 2 and bits[0].strip():
                    commits.append({"sha": bits[0].strip(), "subject": bits[1].strip()[:300],
                                    "body": (bits[2] if len(bits) > 2 else "").strip()[:2000]})
        out["pr_draft"] = B.pr_draft(commits, {"branch": branch})
        out["compare_url"] = B.compare_url({"base": main, "branch": branch}, url)
        out["gh"] = B.gh_state(out["remote_host"] or "github.com") if B.github_repo(url) else {
            "installed": bool(B.gh_path()), "signed_in": None, "note": "This remote is not on GitHub."}
    return out


def _base_ref(top: str, remote: str, main: str) -> str:
    """What "ahead of the main branch" is counted from: the remote's copy of
    it when known, else the local one."""
    if not main:
        return ""
    if remote and B._ref_sha(Path(top), f"refs/remotes/{remote}/{main}"):
        return f"refs/remotes/{remote}/{main}"
    return f"refs/heads/{main}" if B._ref_sha(Path(top), f"refs/heads/{main}") else ""


def _can_pr(branch: str, main: str, remote: str) -> tuple[bool, str]:
    if not branch:
        return False, DETACHED_HERE
    if not main:
        return False, NO_MAIN
    if branch == main:
        return False, f"This folder is on {main}, the repository's main branch. {ON_MAIN_PR}"
    if not remote:
        return False, B.NO_REMOTE
    return True, ""


def _literal(rel: str) -> str:
    return f":(literal){rel}"


def folder_commit(folder: str, message: str, paths: list[str]) -> dict[str, Any]:
    """Commit ONLY ``paths`` (each one of the repository's changed files now)
    on the folder's current branch, with ``message`` on stdin: ``git commit
    --only`` — what the user staged for any other path stays staged. A new
    file is told to git as "to be added" first (``git add -N``), and taken
    back out of the index when the commit does not go through. BLOCKING;
    :class:`branches.GitRefused` in words."""
    from ..git.integration import default_identity, git_env, has_identity

    top = repo_top(folder)
    if not top:
        raise B.GitRefused(NOT_A_REPO_HERE, 409)
    msg = str(message or "").replace("\r\n", "\n").strip()
    if not msg:
        raise B.GitRefused(B.EMPTY_MESSAGE, 400)
    if len(msg) > B.MAX_MESSAGE_CHARS:
        raise B.GitRefused(f"A commit message holds at most {B.MAX_MESSAGE_CHARS:,} characters.", 400)
    want: list[str] = []
    for p in paths or []:
        text = str(p or "")
        if text and text not in want:
            want.append(text)
    if not want:
        raise B.GitRefused(TICK_FIRST, 400)
    if not current_branch(top):
        raise B.GitRefused(DETACHED_HERE, 409)
    rows = {r["rel"]: r for r in changed_files(top)}
    if not rows:
        raise B.GitRefused(NOTHING_CHANGED, 409)
    for p in want:
        if p not in rows:
            raise B.GitRefused(NOT_CHANGED.format(path=p), 400)
    specs: list[str] = []
    added: list[str] = []
    for p in want:
        specs.append(_literal(p))
        old = rows[p].get("old")
        if old:
            specs.append(_literal(old))
        if rows[p].get("new"):
            added.append(_literal(p))
    if added:
        got = B._g(["add", "--intent-to-add", "--", *added], top)
        if not got.ok:
            raise B.GitRefused("Git could not take the new files.", 409, B._masked(got.err or got.out))
    own = has_identity(Path(top), git_env())
    args = ([] if own else default_identity()) + ["commit", "--only", "-F", "-", "--", *specs]
    got = B._g(args, top, timeout=B.START_S, input=(msg + "\n").encode("utf-8"))
    if not got.ok:
        if added:
            B._g(["reset", "-q", "--", *added], top)
        if got.timed_out:
            raise B.GitRefused(B._timed_out_words("The commit", B.START_S), 504)
        raise B.GitRefused("Git did not make the commit.", 409, B._masked(got.err or got.out))
    sha = B._ref_sha(Path(top), "HEAD")
    files = B._g(["diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD"], top, reads=True)
    out: dict[str, Any] = {
        "committed": True, "sha": sha[:12], "subject": msg.split("\n", 1)[0][:300],
        "files": len([f for f in files.out.splitlines() if f.strip()]) if files.ok else 0,
        "branch": current_branch(top),
    }
    if not own:
        out["note"] = (
            "Git has no name and e-mail set on this PC, so the commit is signed \"Iron Jarvis\". "
            "Set yours with git config to commit as you."
        )
    return out


def _pushable(folder: str) -> tuple[str, str, str, str]:
    """``(top, branch, main, remote)`` of a folder whose branch may be pushed,
    or :class:`branches.GitRefused` in words."""
    top = repo_top(folder)
    if not top:
        raise B.GitRefused(NOT_A_REPO_HERE, 409)
    branch = current_branch(top)
    remote = B.pick_remote(Path(top), branch) if branch else ""
    main = main_branch(top, remote)
    ok, why = _can_pr(branch, main, remote)
    if not ok:
        raise B.GitRefused(why, 409)
    return top, branch, main, remote


def folder_push(folder: str) -> dict[str, Any]:
    """``git push <remote> <branch>:<branch>`` of the folder's own branch —
    never a force, never the main branch, and no upstream written into the
    user's config. BLOCKING."""
    top, branch, _main, remote = _pushable(folder)
    got = B._g(["push", remote, f"{branch}:{branch}"], top, timeout=B.PUSH_S)
    details = B._masked((got.err or "") + ("\n" + got.out if got.out.strip() else ""))
    if got.timed_out:
        raise B.GitRefused(
            B._timed_out_words("The push", B.PUSH_S) + " If a sign-in window opened, finish it and push again.",
            504, details,
        )
    if not got.ok:
        text = (got.err or "") + (got.out or "")
        if B._REJECTED_RX.search(text):
            raise B.GitRefused(
                f"The remote has commits on {branch} that this folder does not, so nothing was pushed (a push "
                "here would overwrite them).", 409, details,
            )
        if B._AUTH_RX.search(text):
            raise B.GitRefused(
                f"Git could not sign in to the remote \"{remote}\", so nothing was pushed. Check your git "
                "sign-in for it, then push again.", 409, details,
            )
        raise B.GitRefused("The push did not go through.", 409, details)
    return {"pushed": True, "remote": remote, "branch": branch, "details": details}


def folder_pr(folder: str, title: str, body: str) -> dict[str, Any]:
    """Open a pull request of the folder's own branch against the main branch
    with ``gh pr create`` (the branch is pushed first when the remote lacks
    its newest commit). Answers ``{url, number, existed, pushed, branch}``.
    BLOCKING."""
    top, branch, main, remote = _pushable(folder)
    title = " ".join(str(title or "").split())[:B.MAX_TITLE_CHARS]
    if not title:
        raise B.GitRefused("Give the pull request a title first.", 400)
    body = str(body or "")[:B.MAX_BODY_CHARS]
    base = _base_ref(top, remote, main)
    ahead = B._g(["rev-list", "--count", f"{base}..HEAD"], top, reads=True) if base else None
    if ahead is None or not ahead.ok or int(ahead.out.strip() or 0) == 0:
        raise B.GitRefused(f"{branch} has no commits that {main} does not have, so there is nothing for a "
                           "pull request. Commit first.", 409)
    url = B._remote_url(Path(top), remote)
    host = (B.github_repo(url) or ("github.com", "", ""))[0]
    gh = B.gh_state(host)
    if not gh["installed"] or not gh["signed_in"]:
        raise B.GitRefused(gh["note"], 409)
    if B._gh_batch_unsafe(B.gh_path(), [title, main, branch]):
        raise B.GitRefused(B.GH_BATCH_UNSAFE, 400)
    head = B._ref_sha(Path(top), "HEAD")
    tracking = B._ref_sha(Path(top), f"refs/remotes/{remote}/{branch}")
    pushed = False
    if not tracking or tracking != head:
        folder_push(folder)
        pushed = True
    fd, tmp = tempfile.mkstemp(prefix="ij-pr-", suffix=".md")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(body)
        got = B._gh(["pr", "create", "--base", main, "--head", branch, "--title", title, "--body-file", tmp],
                    Path(top))
    finally:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
    text = (got.out or "") + "\n" + (got.err or "")
    m = B._PR_URL_RX.search(text)
    if got.timed_out:
        raise B.GitRefused(B._timed_out_words("Making the pull request", B.GH_S), 504, B._masked(text))
    if not got.ok and not (m and "already exists" in text):
        raise B.GitRefused("GitHub did not make the pull request.", 409, B._masked(text))
    if not m:
        raise B.GitRefused("GitHub made no pull request link.", 409, B._masked(text))
    return {"url": m.group(0).rstrip(".,"), "number": int(m.group(1)), "existed": not got.ok,
            "pushed": pushed, "branch": branch}


def folder_pr_status(folder: str, branch: str) -> dict[str, Any]:
    """The pull request of the folder's branch and its checks (``branches.
    pr_status``: ``gh pr view <branch>``). BLOCKING."""
    top = repo_top(folder)
    if not top or not branch:
        raise B.GitRefused(NOT_A_REPO_HERE if not top else DETACHED_HERE, 409)
    return B.pr_status({"worktree": top, "branch": branch})


def uncommitted_names(facts: dict[str, str]) -> list[str]:
    """A branch session's files not committed yet, by name (the draft's)."""
    try:
        return B._porcelain(B._live(facts))
    except B.GitRefused:
        return []


__all__ = [
    "BRANCH_NEEDS_FOLDER", "BRANCH_NO_RESUME", "CHECKING_NOW", "CLAIM", "COPY_SHARED", "DIRECT", "NO_DIFF_HERE",
    "NO_DISCARD_HERE", "NO_MERGE_HERE", "NOT_A_REPO_HERE", "NOT_DELETED", "ON_MAIN_PR", "TICK_FIRST",
    "changed_files", "checkout_of", "direct_pr", "draft_message", "facts_of", "first_message", "folder_commit",
    "folder_pr", "folder_pr_status", "folder_push", "folder_status", "main_branch", "others_in_copy", "ours",
    "record_of", "repo_top", "shared_json", "shared_refusal", "tool_env", "uncommitted_names", "view",
]
