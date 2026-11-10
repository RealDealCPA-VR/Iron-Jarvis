"""Skills from a GitHub repository (v1.464.0): look first, then load.

The owner's request: beside the starter bundles, a "custom" way to pull skills
from GitHub, with a warning. A skill is INSTRUCTIONS A MODEL FOLLOWS, written
by somebody else, so every step here is on the careful side:

* ONLY ``https://github.com/<owner>/<repo>`` addresses, and only two hosts are
  ever asked: ``api.github.com`` (to list the files) and
  ``raw.githubusercontent.com`` (to read them). A redirect to any other host is
  not followed. Nothing about the user or this PC is sent: the request names
  the repository and nothing else.
* ONLY files named ``SKILL.md`` are read. A skill's scripts and other files are
  NEVER downloaded and nothing from the repository is ever run; the preview
  says how many files were left behind.
* THE PREVIEW IS WHAT IS LOADED. The preview resolves the branch to one commit;
  the load reads that commit again by its id, so the text cannot change between
  the look and the press.
* Every skill is scanned (``core/promptguard``) before it is offered. One that
  reads as an attempt to override the app's rules cannot be loaded.
* A loaded skill is marked ``created_by: github`` with where it came from. That
  mark makes the app scan it AGAIN every time it is about to reach a model
  (``framework.SCANNED_CREATED_BY``), exactly like a skill an agent wrote. The
  frontmatter is REBUILT here: nothing the repository wrote (``created_by``,
  ``allowed-tools``, switches) survives except the name and the description.
* A skill whose name the user already has is never overwritten.

BLOCKING: every function here does network or file work; routes call it off
the event loop. Tests replace :data:`HTTP_GET`; nothing here is reached by the
test suite's real network.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote, urlsplit

from .loader import SKILL_FILE, _parse_frontmatter, read_skill_md, slugify, write_skill_md

GITHUB = "github"
API_HOST = "api.github.com"
RAW_HOST = "raw.githubusercontent.com"
ALLOWED_HOSTS = frozenset({API_HOST, RAW_HOST})

MAX_SKILLS = 40
MAX_SKILL_BYTES = 100_000
MAX_TREE_BYTES = 8_000_000
TIMEOUT_S = 15.0
MAX_HOPS = 2
DESCRIPTION_CHARS = 400

USER_AGENT = "IronJarvis/1 (desktop assistant; reads skill files its user asked for)"

#: Said on the page before anything is loaded, and returned with every preview.
WARNING = (
    "These skills were written by someone else. A skill is a set of instructions "
    "Jarvis follows, so only load skills from a source you trust, and read each "
    "one first. Only the instructions are downloaded: a skill's scripts and other "
    "files are not, and nothing from the repository is run."
)

_NAME = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9_.-]{0,99}\Z")
_SHA = re.compile(r"\A[0-9a-f]{40}\Z")


class ImportRefused(Exception):
    """A refusal in one plain sentence (the route answers 400 with it)."""


def _default_http_get(url: str, accept: str = "") -> tuple[int, bytes]:
    """GET ``url`` (one of the two GitHub hosts). ``(status, body)``.

    Redirects are followed by hand, at most :data:`MAX_HOPS`, and only to one
    of the two hosts. The body is read up to :data:`MAX_TREE_BYTES` + 1."""
    import httpx

    headers = {"User-Agent": USER_AGENT}
    if accept:
        headers["Accept"] = accept
    current = url
    for _ in range(MAX_HOPS + 1):
        host = (urlsplit(current).hostname or "").lower()
        if urlsplit(current).scheme != "https" or host not in ALLOWED_HOSTS:
            raise ImportRefused("GitHub sent Iron Jarvis to another site, so nothing was read.")
        with httpx.Client(follow_redirects=False, timeout=httpx.Timeout(TIMEOUT_S, connect=5.0)) as client:
            with client.stream("GET", current, headers=headers) as resp:
                if resp.status_code in (301, 302, 307, 308) and resp.headers.get("location"):
                    current = str(httpx.URL(current).join(resp.headers["location"]))
                    continue
                body = b""
                for chunk in resp.iter_bytes():
                    body += chunk
                    if len(body) > MAX_TREE_BYTES:
                        break
                return resp.status_code, body
    raise ImportRefused("GitHub redirected too many times, so nothing was read.")


#: The one network seam (tests replace it).
HTTP_GET: Callable[..., tuple[int, bytes]] = _default_http_get


def parse_repo_url(url: str) -> dict[str, str]:
    """``{owner, repo, ref, subpath}`` from a github.com address.

    Accepted: ``https://github.com/o/r``, ``…/o/r.git``, ``…/o/r/tree/<branch>``,
    ``…/o/r/tree/<branch>/<folder>``, ``…/o/r/blob/<branch>/<path>/SKILL.md``.
    A branch whose own name holds a "/" cannot be told from a folder; the
    address of the repository itself (its default branch) always works."""
    text = str(url or "").strip()
    if len(text) > 500:
        raise ImportRefused("That address is too long to be a GitHub repository.")
    if text.lower().startswith("github.com/"):
        text = "https://" + text
    parts = urlsplit(text)
    if parts.scheme != "https" or (parts.hostname or "").lower() not in ("github.com", "www.github.com"):
        raise ImportRefused(
            "Give the address of a GitHub repository, like https://github.com/owner/repository."
        )
    if parts.username or parts.password or parts.port:
        raise ImportRefused("Give the plain address of the repository, with no sign-in in it.")
    segs = [s for s in parts.path.split("/") if s]
    if len(segs) < 2:
        raise ImportRefused(
            "Give the address of a GitHub repository, like https://github.com/owner/repository."
        )
    owner, repo = segs[0], segs[1]
    if repo.lower().endswith(".git"):
        repo = repo[:-4]
    if not _NAME.match(owner) or not _NAME.match(repo):
        raise ImportRefused("That does not look like a GitHub repository address.")
    ref, sub = "", ""
    rest = segs[2:]
    if rest:
        if rest[0] not in ("tree", "blob") or len(rest) < 2:
            raise ImportRefused(
                "Give the address of the repository or of a folder in it (the address bar while you look at the folder)."
            )
        ref = rest[1]
        tail = rest[2:]
        if rest[0] == "blob" and tail and tail[-1].lower() == SKILL_FILE.lower():
            tail = tail[:-1]
        sub = "/".join(tail)
    for piece in [ref] + ([s for s in sub.split("/")] if sub else []):
        if piece in (".", "..") or "\\" in piece or any(ord(c) < 32 for c in piece):
            raise ImportRefused("That does not look like a GitHub repository address.")
    return {"owner": owner, "repo": repo, "ref": ref, "subpath": sub}


def _api(path: str, accept: str = "application/vnd.github+json") -> Any:
    status, body = HTTP_GET(f"https://{API_HOST}{path}", accept)
    if status == 404:
        raise ImportRefused(
            "GitHub has no public repository, branch or folder at that address. "
            "A private repository cannot be read."
        )
    if status in (403, 429):
        raise ImportRefused(
            "GitHub is limiting how often this PC may ask right now. Try again in a few minutes."
        )
    if status != 200:
        raise ImportRefused(f"GitHub did not answer the request (it said {status}). Try again.")
    if len(body) > MAX_TREE_BYTES:
        raise ImportRefused("That repository is too large to look through. Give the address of the folder that holds the skills.")
    if accept.endswith(".sha"):
        return body.decode("ascii", "replace").strip()
    try:
        return json.loads(body.decode("utf-8", "replace"))
    except ValueError:
        raise ImportRefused("GitHub's answer could not be read. Try again.") from None


def _resolve_commit(owner: str, repo: str, ref: str) -> str:
    if not ref:
        info = _api(f"/repos/{quote(owner)}/{quote(repo)}")
        ref = str((info or {}).get("default_branch") or "") if isinstance(info, dict) else ""
        if not ref:
            raise ImportRefused("GitHub did not say which branch that repository uses.")
    sha = _api(f"/repos/{quote(owner)}/{quote(repo)}/commits/{quote(ref, safe='')}",
               "application/vnd.github.sha")
    if not isinstance(sha, str) or not _SHA.match(sha):
        raise ImportRefused("GitHub did not name one version of that branch, so nothing was read.")
    return sha


def _safe_path(path: str) -> bool:
    if not path or len(path) > 400 or path.startswith("/") or "\\" in path:
        return False
    parts = path.split("/")
    if any(p in ("", ".", "..") or any(ord(c) < 32 for c in p) for p in parts):
        return False
    return parts[-1] == SKILL_FILE


def _raw(owner: str, repo: str, commit: str, path: str) -> str:
    url = f"https://{RAW_HOST}/{quote(owner)}/{quote(repo)}/{commit}/{quote(path)}"
    status, body = HTTP_GET(url, "")
    if status != 200:
        raise ImportRefused(f"{path} could not be read from GitHub (it said {status}).")
    if len(body) > MAX_SKILL_BYTES:
        raise ImportRefused(f"{path} is too large to be a skill.")
    if b"\x00" in body:
        raise ImportRefused(f"{path} is not a text file.")
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        raise ImportRefused(f"{path} is not a text file Iron Jarvis can read.") from None


def _one_line(text: Any, limit: int) -> str:
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= limit else flat[: limit - 1].rstrip() + "…"


def _read_skill(text: str, path: str) -> dict[str, Any]:
    """``{name, description, body, flagged}`` of one fetched SKILL.md."""
    from ..core.promptguard import scan_context

    try:
        meta, body = _parse_frontmatter(text.replace("\r\n", "\n"))
    except Exception:  # noqa: BLE001 - unreadable frontmatter: the whole text is the body
        meta, body = {}, text
    folder = path.rsplit("/", 2)[-2] if "/" in path else ""
    raw_name = meta.get("name") if isinstance(meta.get("name"), str) else ""
    name = slugify(raw_name or folder or "skill")
    description = _one_line(meta.get("description") if isinstance(meta.get("description"), (str, int, float)) else "",
                            DESCRIPTION_CHARS)
    flagged = False
    try:
        for piece in (body, description, str(raw_name)):
            if scan_context(piece, source="a skill from GitHub").blocked:
                flagged = True
    except Exception:  # noqa: BLE001 - cannot scan means cannot vouch for it
        flagged = True
    return {"name": name, "description": description, "body": body.strip(), "flagged": flagged}


def _state(home: Path, name: str, repo: str, path: str, taken: set[str]) -> str:
    """"new" | "same" (this very skill, loaded before) | "taken"."""
    folder = Path(home) / "skills" / slugify(name)
    if (folder / SKILL_FILE).is_file():
        try:
            meta, _ = read_skill_md(folder)
        except (OSError, ValueError):
            return "taken"
        mine = (str(meta.get("created_by") or "").lower() == GITHUB
                and str(meta.get("source_repo") or "") == repo
                and str(meta.get("source_path") or "") == path)
        return "same" if mine else "taken"
    return "taken" if name in taken else "new"


def preview(url: str, home: Path, taken: "set[str] | None" = None) -> dict[str, Any]:
    """Look at a repository: which skills it holds, at which commit.

    Nothing is written. ``taken`` = names the app already has (any source)."""
    where = parse_repo_url(url)
    owner, repo, sub = where["owner"], where["repo"], where["subpath"]
    commit = _resolve_commit(owner, repo, where["ref"])
    tree = _api(f"/repos/{quote(owner)}/{quote(repo)}/git/trees/{commit}?recursive=1")
    entries = tree.get("tree") if isinstance(tree, dict) else None
    if not isinstance(entries, list):
        raise ImportRefused("GitHub's list of files could not be read. Try again.")
    prefix = sub + "/" if sub else ""
    blobs = [e for e in entries if isinstance(e, dict) and e.get("type") == "blob" and isinstance(e.get("path"), str)]
    paths = sorted(
        e["path"] for e in blobs
        if _safe_path(e["path"]) and (not prefix or e["path"].startswith(prefix))
        and int(e.get("size") or 0) <= MAX_SKILL_BYTES
    )
    if not paths:
        raise ImportRefused(
            "No skill was found there. A skill is a folder holding a file named SKILL.md."
        )
    more = max(0, len(paths) - MAX_SKILLS)
    skills: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in paths[:MAX_SKILLS]:
        try:
            sk = _read_skill(_raw(owner, repo, commit, path), path)
        except ImportRefused as exc:
            skills.append({"path": path, "name": path, "description": "", "state": "unreadable",
                           "flagged": False, "can_load": False, "why": str(exc), "other_files": 0, "chars": 0})
            continue
        folder = path[: -len(SKILL_FILE)]
        others = sum(1 for e in blobs if e["path"].startswith(folder) and e["path"] != path) if folder else 0
        state = _state(home, sk["name"], f"{owner}/{repo}", path, set(taken or ()))
        if sk["name"] in seen:
            state = "taken"
        seen.add(sk["name"])
        why = ""
        if not sk["body"]:
            why = "it has no instructions"
        elif sk["flagged"]:
            why = "it holds text that reads as an attempt to override Iron Jarvis's rules"
        elif state == "taken":
            why = "you already have a skill with this name"
        skills.append({
            "path": path, "name": sk["name"], "description": sk["description"], "state": state,
            "flagged": sk["flagged"], "can_load": not why, "why": why,
            "other_files": others, "chars": len(sk["body"]),
        })
    return {
        "repo": f"{owner}/{repo}", "commit": commit, "folder": sub, "skills": skills,
        "more": more, "truncated": bool(isinstance(tree, dict) and tree.get("truncated")),
        "warning": WARNING,
    }


def load(url: str, commit: str, paths: list[str], home: Path, *, confirm: bool,
         taken: "set[str] | None" = None) -> dict[str, Any]:
    """Load the chosen skills, read again at the commit the preview named."""
    if confirm is not True:
        raise ImportRefused("Press Load again to confirm you have read these skills and trust where they come from.")
    where = parse_repo_url(url)
    owner, repo = where["owner"], where["repo"]
    if not _SHA.match(str(commit or "")):
        raise ImportRefused("Look at the repository again first, then load from that list.")
    wanted = [p for p in dict.fromkeys(str(p) for p in (paths or []))]
    if not wanted:
        raise ImportRefused("Choose at least one skill to load.")
    if len(wanted) > MAX_SKILLS:
        raise ImportRefused(f"Load at most {MAX_SKILLS} skills at a time.")
    loaded: list[str] = []
    skipped: list[dict[str, str]] = []
    names_taken = set(taken or ())
    for path in wanted:
        if not _safe_path(path):
            skipped.append({"name": path, "reason": "it is not a skill file"})
            continue
        try:
            sk = _read_skill(_raw(owner, repo, commit, path), path)
        except ImportRefused as exc:
            skipped.append({"name": path, "reason": str(exc)})
            continue
        if not sk["body"]:
            skipped.append({"name": sk["name"], "reason": "it has no instructions"})
            continue
        if sk["flagged"]:
            skipped.append({"name": sk["name"],
                            "reason": "it holds text that reads as an attempt to override Iron Jarvis's rules"})
            continue
        state = _state(home, sk["name"], f"{owner}/{repo}", path, names_taken)
        if state == "taken" or sk["name"] in loaded:
            skipped.append({"name": sk["name"], "reason": "you already have a skill with this name"})
            continue
        write_skill_md(
            Path(home) / "skills" / slugify(sk["name"]),
            {
                "name": sk["name"],
                "description": sk["description"],
                "created_by": GITHUB,
                "source_repo": f"{owner}/{repo}",
                "source_commit": commit,
                "source_path": path,
            },
            sk["body"],
        )
        loaded.append(sk["name"])
    return {"repo": f"{owner}/{repo}", "commit": commit, "loaded": loaded, "skipped": skipped}
