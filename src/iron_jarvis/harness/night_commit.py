"""WHAT A NIGHT TASK'S OWN COMMIT MAY TAKE (v1.399.0 review of night shift N2).

When a night task ends, Iron Jarvis commits its work to the task's own branch
while the user sleeps (decision 2). The Commit button's ``git add -A`` takes
EVERYTHING in the branch folder — and a night task can leave a ``.env`` it
made, a key it pasted into a config file, a 200 MB build artefact or a whole
``node_modules`` it installed. A secret committed into a branch the user may
push in the morning is a real harm (history keeps it after the file is gone),
so the NIGHT's commit (never a person's press) is screened after staging:

* a file whose NAME is a secret's (``.env`` and ``.env.*`` — but not
  ``.env.example`` and the like —, private keys and keystores, ``id_rsa``,
  ``.netrc``, ``.npmrc``, ``.pypirc``, ``.git-credentials``, ``credentials``,
  ``secrets.*``, a cloud service-account file);
* a file whose ADDED text holds a secret-shaped value — the key shapes the app
  already masks (``detections.redact``, ``providers.failure_words``), a
  private key block, a long literal assigned to a secret-named key, a URL
  carrying a password — and, for a file the branch already tracked, only a
  value its committed copy did not hold (the user's own code that reads a
  password from the environment is not a secret);
* a file larger than :data:`MAX_COMMIT_FILE_BYTES`;
* a NEW file inside a dependency or cache folder (``node_modules``, ``.venv``,
  ``__pycache__``, …).

Each is UNSTAGED — it stays in the branch folder, uncommitted — and the
record names it with why (:func:`left_out_words`). If what is left staged
cannot be proved clean, NOTHING is committed. A screen is a guardrail on
shapes, not a proof that no secret is ever committed: the summary says what
was left out, and the branch is never pushed by Iron Jarvis.

BLOCKING (git and file reads): callers hop off the loop.
"""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath
from typing import Any

#: A file larger than this is never committed by Iron Jarvis at night
#: (GitHub warns past 50 MB and refuses a push past 100 MB).
MAX_COMMIT_FILE_BYTES = 50 * 1024 * 1024
#: The record keeps at most this many left-out paths (the count is kept too).
MAX_LEFT_OUT_KEPT = 200
#: The words name at most this many paths.
WORDS_PATHS = 8

#: Why a file was left out → the record's code.
SECRET = "secret"
LARGE = "large"
DEPENDENCY = "dependency"

# A secret's own file name (the basename, case-insensitive).
_SECRET_NAME_RX = re.compile(
    r"""(?ix)^(?:
        \.env(?:\..+)?                                   # .env, .env.local, .env.production
      | .+\.(?:pem|key|p12|pfx|jks|keystore|ppk|kdbx|asc|gpg|ovpn)
      | id_(?:rsa|dsa|ecdsa|ed25519)(?:_[^.]*)?          # an SSH private key (never .pub)
      | \.netrc | _netrc | \.pgpass | \.git-credentials | \.pypirc | \.npmrc | \.htpasswd
      | credentials(?:\.(?:json|ya?ml|toml|ini|txt))?
      | (?:.+[-_.])?secrets?\.(?:json|ya?ml|toml|ini|env|txt)
      | service[-_]?account.*\.json
    )$"""
)
# An .env file that only shows the shape (committed on purpose).
_EXAMPLE_RX = re.compile(r"(?i)(?:example|sample|template|dist|defaults?|schema)")

# A dependency or cache folder (a NEW file inside one is never committed).
_HEAVY_DIRS = frozenset({
    "node_modules", ".venv", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox",
    ".nox", ".gradle", ".next", ".turbo", ".parcel-cache", ".cache",
})
# A folder that is a virtual environment only when it says so.
_VENV_NAMES = frozenset({"venv", "env", ".env"})
_HEAVY_SUFFIXES = (".pyc", ".pyo")

# Secret-shaped VALUES (the shapes the app already masks, plus a key block,
# a long literal given to a secret-named key, and a URL with a password).
_SHAPES: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(?:ghp_|gho_|ghu_|ghs_|ghr_|github_pat_|sk-ant-|sk-|xox[abprs]-)[A-Za-z0-9_\-]{20,}"),
    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}"),
    re.compile(r"\b(?:xai-|gsk_|hf_)[A-Za-z0-9]{20,}"),
    re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----"),
    re.compile(r"(?i)\bauthorization\b[\"']?\s*[:=]\s*[\"']?(?:bearer|token|basic)\s+[A-Za-z0-9._~+/=\-]{20,}"),
    # No unbounded prefix before the keyword (the v1.349.0 lesson: a
    # `\b[\w.-]*` start rescans a long `a.a.a…` run from every boundary).
    re.compile(
        r"""(?ix)(?:api[_-]?key|access[_-]?key|secret[_-]?key|client[_-]?secret|private[_-]?key
            |auth[_-]?token|access[_-]?token|refresh[_-]?token|bearer|password|passwd|token|secret)[\w\-]{0,40}
            ["']?\s{0,8}[:=]\s{0,8}["']?(?=[A-Za-z0-9_\-+/=.]{0,200}\d)(?=[A-Za-z0-9_\-+/=.]{0,200}[A-Za-z])
            (?!(?:your|xxx|example|changeme|placeholder|dummy|test|fake|sample))[A-Za-z0-9_\-+/=.]{16,}"""
    ),
    # A URL carrying a password with a digit and a letter (``postgres:postgres``
    # in a dev compose file is not one). The start is bounded, never a rescan.
    re.compile(
        r"(?i)(?<![a-z0-9+.\-])[a-z][a-z0-9+.\-]{0,30}://[^/\s:@\"']{1,200}:"
        r"(?=[^@\s/\"']{0,200}\d)(?=[^@\s/\"']{0,200}[A-Za-z])[^@\s/\"']{8,200}@"
    ),
)


def secret_name(rel: str) -> bool:
    """Is this path's FILE NAME a secret's own (``.env.example`` is not)?"""
    name = PurePosixPath(rel).name
    if not _SECRET_NAME_RX.match(name):
        return False
    if name.lower().startswith(".env") and _EXAMPLE_RX.search(name[4:]):
        return False
    return True


def secrets_in(text: str) -> set[str]:
    """The secret-shaped values in ``text`` (the matched strings)."""
    found: set[str] = set()
    for rx in _SHAPES:
        for m in rx.finditer(text):
            found.add(m.group(0))
    return found


def _heavy(rel: str, wt: Path) -> bool:
    parts = PurePosixPath(rel).parts
    if rel.lower().endswith(_HEAVY_SUFFIXES):
        return True
    for i, part in enumerate(parts[:-1]):
        low = part.lower()
        if low in _HEAVY_DIRS:
            return True
        if low in _VENV_NAMES and (wt.joinpath(*parts[: i + 1]) / "pyvenv.cfg").is_file():
            return True
    return False


def _split_z(text: str) -> list[str]:
    return [p for p in text.split("\0")]


def _staged(wt: Path) -> "list[tuple[str, str, str]]":
    """``(status, new mode, path)`` of every staged change (no renames)."""
    from .branches import GitRefused, _g

    got = _g(["diff", "--cached", "--raw", "-z", "--no-renames", "--no-abbrev"], wt, reads=True)
    if not got.ok:
        raise GitRefused("Git could not list what it was about to commit.", 409)
    return _parse_raw(got.out)


def _committed(wt: Path) -> "list[tuple[str, str, str]]":
    """``(status, new mode, path)`` of every change the newest commit made."""
    from .branches import GitRefused, _g

    got = _g(["diff", "--raw", "-z", "--no-renames", "--no-abbrev", "HEAD~1", "HEAD"], wt, reads=True)
    if not got.ok:
        raise GitRefused("Git could not list what it committed.", 409)
    return _parse_raw(got.out)


def _parse_raw(out_text: str) -> "list[tuple[str, str, str]]":
    tokens = _split_z(out_text)
    out: list[tuple[str, str, str]] = []
    i = 0
    while i + 1 < len(tokens):
        meta, path = tokens[i], tokens[i + 1]
        i += 2
        if not meta.startswith(":"):
            continue
        bits = meta[1:].split()
        if len(bits) < 5:
            continue
        out.append((bits[4][:1], bits[1], path))
    return out


def _old_blobs(wt: Path, paths: list[str], rev: str = "HEAD") -> dict[str, bytes]:
    """The committed (``rev``) copy of each path, in ONE ``git cat-file --batch``."""
    from ..git.integration import git_env, run_git

    wanted = [p for p in paths if "\n" not in p and "\r" not in p]
    if not wanted:
        return {}
    # Through THE one git spawner, its stdout kept as bytes (a blob's size is
    # in bytes, and the text decode would turn CRLF into LF).
    res = run_git(
        ["-c", "core.quotepath=off", "cat-file", "--batch"], wt, timeout=120.0,
        env=git_env({"GIT_OPTIONAL_LOCKS": "0"}), identity=False,
        input="".join(f"{rev}:{p}\n" for p in wanted).encode("utf-8"), keep_bytes=True,
    )
    if not res.ok:
        raise OSError("git could not read the committed copies")
    data = res.stdout
    out: dict[str, bytes] = {}
    pos = 0
    for p in wanted:
        nl = data.find(b"\n", pos)
        if nl < 0:
            break
        header = data[pos:nl].decode("utf-8", "replace").split()
        pos = nl + 1
        if len(header) == 3 and header[1] == "blob":
            size = int(header[2])
            out[p] = data[pos:pos + size]
            pos += size + 1  # the blob, then its newline
    return out


def _text(data: bytes) -> "str | None":
    if b"\0" in data[:8192]:
        return None  # binary: its name decides
    return data.decode("utf-8", errors="replace")


def screen(wt: Path) -> list[dict[str, str]]:
    """Unstage what a night commit must not take (see the module docstring)
    and answer ``[{path, why}]`` — what was left out. The index is left
    holding only what passed; :class:`branches.GitRefused` (with the index
    unstaged entirely) when that cannot be made sure of. BLOCKING."""
    from .branches import GitRefused, _g

    left = _judge(wt, _staged(wt), "HEAD")
    if not left:
        return []
    paths = sorted(left)
    unstaged = _g(["--literal-pathspecs", "reset", "-q", "--pathspec-from-file=-", "--pathspec-file-nul"], wt,
                  input="\0".join(paths).encode("utf-8"))
    still = {rel for _s, _m, rel in _staged(wt)} if unstaged.ok else set(paths)
    if not unstaged.ok or still & set(paths):
        _g(["reset", "-q"], wt)  # nothing half-screened stays staged
        raise GitRefused(
            "Iron Jarvis could not leave out the files it must not commit, so it committed nothing.", 409,
        )
    return [{"path": rel, "why": left[rel]} for rel in paths]


def check_commit(wt: Path) -> list[dict[str, str]]:
    """What the NEWEST commit (the night's own, just made) holds that a night
    commit must never take, by the same rules as :func:`screen` — the
    repository's own hooks ran between the screen and the commit, and a hook
    that stages everything again (``git add -A`` after a formatter) put back
    what the screen left out. ``[]`` when the commit is clean. BLOCKING."""
    left = _judge(wt, _committed(wt), "HEAD~1")
    return [{"path": rel, "why": left[rel]} for rel in sorted(left)]


def _judge(wt: Path, entries: "list[tuple[str, str, str]]", old_rev: str) -> dict[str, str]:
    """``{path: why}`` of the changes (``entries``) a night commit must not
    take; a changed file's secrets count only when ``old_rev``'s copy did not
    hold them. BLOCKING."""
    left: dict[str, str] = {}
    to_read: list[tuple[str, str]] = []
    for status, mode, rel in entries:
        if status == "D":
            continue
        if secret_name(rel):
            left[rel] = SECRET
            continue
        if status == "A" and _heavy(rel, wt):
            left[rel] = DEPENDENCY
            continue
        if mode in ("120000", "160000"):
            continue  # a link (its target's name) or a submodule's commit
        try:
            size = (wt / rel).lstat().st_size
        except OSError:
            continue  # gone since it was staged: git commits what it staged
        if size > MAX_COMMIT_FILE_BYTES:
            left[rel] = LARGE
            continue
        to_read.append((status, rel))
    old = _old_blobs(wt, [rel for status, rel in to_read if status != "A"], old_rev) if to_read else {}
    for status, rel in to_read:
        try:
            data = (wt / rel).read_bytes()
        except OSError:
            continue
        text = _text(data)
        if text is None:
            continue
        found = secrets_in(text)
        if found and status != "A" and rel in old:
            before = _text(old[rel]) or ""
            found -= secrets_in(before)
        if found:
            left[rel] = SECRET
    return left


def why_words(why: str) -> str:
    if why == SECRET:
        return "looks like a secret"
    if why == LARGE:
        return f"larger than {MAX_COMMIT_FILE_BYTES // (1024 * 1024)} MB"
    if why == DEPENDENCY:
        return "in a dependency or cache folder"
    return "left out"


def left_out_words(left: list[dict[str, Any]], total: "int | None" = None) -> str:
    """The record's sentence about what the night's commit left out: "3 files
    were not committed and are still in its branch folder: .env (looks like a
    secret), …". "" when nothing was."""
    n = int(total if total is not None else len(left))
    if n <= 0:
        return ""
    shown = [f"{x.get('path')} ({why_words(str(x.get('why') or ''))})" for x in left[:WORDS_PATHS]]
    more = n - len(shown)
    tail = f", and {more} more" if more > 0 else ""
    files = "1 file was" if n == 1 else f"{n} files were"
    return (f"{files} not committed and {'is' if n == 1 else 'are'} still in its branch folder: "
            + ", ".join(shown) + tail + ". Look at them before you commit or push them yourself.")


__all__ = [
    "DEPENDENCY", "LARGE", "MAX_COMMIT_FILE_BYTES", "MAX_LEFT_OUT_KEPT", "SECRET", "left_out_words",
    "check_commit", "screen", "secret_name", "secrets_in", "why_words",
]
