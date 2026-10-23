"""What a tool step was done TO, in a few safe words — the daemon's twin of
the dashboard's ``lib/workTarget.stepTarget`` (v1.378.0 review, IJ harness
part 2 Phase 7).

The chat page works a step's target out in the browser while the turn runs
and saves it with the step ("Read cart.py", "Ran python"). A Build session's
reply line is written by the DAEMON (``routes/build._ReplyWatch``), so it had
none: a finished reply's steps read "Ran · shell", "Changed · edit_file"
while the same steps read "Ran python", "Changed cart.py" a moment earlier,
live (the walkthrough's gap 3). This module gives the saved line the same
words from the same input — the ``tool_call`` frame's own arguments.

The rules are the dashboard's, kept equal by ONE shared table
(``dashboard/__tests__/fixtures/step-target-v1378.json``: the TypeScript test
writes and checks it, ``tests/test_step_target_v1378.py`` holds this file to
it). Deliberately SMALL and SAFE, like the original:

* a file or folder: its base name only, never the full path;
* a search: the query or pattern, one line, cut to 60 characters;
* a web page: its host only; a page in the agent's own browser: host, port
  and path, never the query string;
* a command: the program's name only ("git"), never its arguments;
* any tool not known here by its exact name: nothing at all;
* anything that looks like a credential: dropped whole.

The dashboard makes a saved target safe AGAIN when it reads it
(``cleanTarget``), so this file never has the last word. Pure; never raises.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

#: Built-in tools by EXACT name (lib/workTarget KIND_OF).
KIND_OF: dict[str, str] = {
    "read_file": "read",
    "read_document": "read",
    "extract_pdf": "read",
    "view_image": "read",
    "excel_read": "read",
    "excel_profile": "read",
    "image_info": "read",
    "pdf_form_fields": "read",
    "write_file": "make",
    "write_document": "make",
    "convert_document": "make",
    "image_convert": "make",
    "image_resize": "make",
    "pdf_split": "make",
    "redact_pii": "make",
    "edit_file": "change",
    "docx_edit": "change",
    "excel_edit": "change",
    "excel_apply_spec": "change",
    "rename_file": "change",
    "pdf_arrange": "change",
    "pdf_form_fill": "change",
    "list_files": "folder",
    "list_folder": "folder",
    "grep": "search",
    "web_search": "web",
    "web_fetch": "page",
    "shell": "command",
    "run_code": "command",
    "repl": "command",
    "rest_call": "service",
    "multi_edit": "change",
    "notebook_edit": "change",
    "glob": "search",
    "process_start": "command",
    "page_open": "site",
    "page_click": "site",
    "page_type": "site",
    "page_read": "site",
    "page_screenshot": "site",
}

#: The longest target kept, in characters (the cut adds one "…").
TARGET_MAX = 60
#: How much of a value is checked for a credential shape.
CHECK_SPAN = 240

_A = re.ASCII
#: lib/configCards SECRET_SHAPES.
_SECRET_SHAPES = [
    re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{16,}", _A),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}", _A),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}", _A),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b", _A),
    re.compile(r"\bBearer\s+[A-Za-z0-9._-]{20,}", _A),
    re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{30,}\b", _A),
]
#: lib/workTarget EXTRA_SECRET_SHAPES.
_EXTRA_SECRET_SHAPES = [
    re.compile(
        r"\b(?:pass(?:word|wd)?|pwd|secret|token|api[_-]?key|apikey|access[_-]?key|auth|credential"
        r"|private[_-]?key|session[_-]?id)\s*[:=]\s*\S",
        re.I | _A,
    ),
    re.compile(r"-----BEGIN [A-Z ]*KEY-----", _A),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.", _A),
]
_RUN = re.compile(r"[A-Za-z0-9+/_-]{32,}", _A)
_PLAIN_RUN = re.compile(r"[A-Za-z0-9+/]+", _A)
_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")
_SPACE = re.compile(r"\s+")
_SCHEME = re.compile(r"^[a-z][a-z0-9+.\-]*://", re.I | _A)
_DRIVE = re.compile(r"^[A-Za-z]:$", _A)

PATH_KEYS = ("path", "file_path", "file", "filename", "source", "src", "folder", "directory", "dir", "root")
#: A tool that MAKES a file names the file it made, not the one it read.
MADE_KEYS = ("target", "output", "out_dir", *PATH_KEYS)


def _js_len(s: str) -> int:
    """A string's length as JavaScript counts it (UTF-16 code units)."""
    return sum(2 if ord(ch) > 0xFFFF else 1 for ch in s)


def _js_slice(s: str, units: int) -> str:
    """The first ``units`` UTF-16 code units, never half a character."""
    out: list[str] = []
    used = 0
    for ch in s:
        w = 2 if ord(ch) > 0xFFFF else 1
        if used + w > units:
            break
        out.append(ch)
        used += w
    return "".join(out)


def _key_like_run(text: str) -> bool:
    for run in _RUN.findall(text):
        plain = bool(_PLAIN_RUN.fullmatch(run))
        digits = any(c.isdigit() for c in run)
        letters = any(c.isalpha() for c in run)
        if plain and digits and letters:
            return True
        if len(run) >= 40 and digits and re.search(r"[a-z]", run) and re.search(r"[A-Z]", run):
            return True
    return False


def secret_looking(text: str) -> bool:
    """True when the text looks like it carries a credential."""
    if not text:
        return False
    head = _js_slice(text, CHECK_SPAN)
    return (
        any(rx.search(head) for rx in _SECRET_SHAPES)
        or any(rx.search(head) for rx in _EXTRA_SECRET_SHAPES)
        or _key_like_run(head)
    )


def _one_line(s: str) -> str:
    return _SPACE.sub(" ", _CONTROL.sub(" ", s)).strip()


def _cut(s: str) -> str:
    if _js_len(s) > TARGET_MAX:
        return _js_slice(s, TARGET_MAX - 1).rstrip() + "…"
    return s


def clean_target(v: Any) -> "str | None":
    """A target made safe to show: one line, at most :data:`TARGET_MAX`
    characters, None when blank or credential-shaped."""
    if not isinstance(v, str):
        return None
    line = _one_line(v)
    if not line or secret_looking(line):
        return None
    return _cut(line)


def _base_name(p: str) -> str:
    parts = [x for x in re.split(r"[/\\]", p.strip()) if x]
    last = parts[-1] if parts else ""
    return "" if _DRIVE.match(last) else last


def _str(args: dict[str, Any], key: str) -> "str | None":
    v = args.get(key)
    return v if isinstance(v, str) and v.strip() else None


def _file_target(args: dict[str, Any], keys: tuple[str, ...]) -> "str | None":
    for k in keys:
        v = _str(args, k)
        if v:
            return clean_target(_base_name(v))
    paths = args.get("paths")
    if isinstance(paths, list):
        names = [_base_name(p) for p in paths if isinstance(p, str) and p.strip()]
        names = [n for n in names if n]
        if names:
            first = clean_target(names[0])
            if not first:
                return None
            return f"{first} and {len(names) - 1} more" if len(names) > 1 else first
    return None


def _host_of(raw: str) -> "str | None":
    try:
        u = urlsplit(raw.strip())
        host = u.hostname or ""
    except ValueError:
        return None
    if u.scheme.lower() not in ("http", "https"):
        return None
    host = host.lower()
    if ":" in host:  # an IPv6 literal keeps its brackets, as a browser writes it
        host = f"[{host}]"
    if host.startswith("www."):
        host = host[4:]
    return clean_target(host) if host else None


def _scrub_paths(q: str) -> str:
    out: list[str] = []
    for w in re.split(r"\s+", q):
        if _SCHEME.match(w):
            w = _host_of(w) or ""
        elif (
            re.match(r"^[A-Za-z]:[\\/]", w)
            or re.match(r"^\\\\[^\\]", w)
            or re.match(r"^~[\\/]", w)
            or re.match(r"^/[^/\s]+/.", w)
        ):
            w = _base_name(w)
        if w:
            out.append(w)
    return " ".join(out)


def page_where(raw: str) -> "str | None":
    """A page as the agent's browser names it: host, port (not the scheme's
    own) and path — never the scheme, query string or fragment."""
    text = raw.strip() if _SCHEME.match(raw.strip()) else f"http://{raw.strip()}"
    try:
        u = urlsplit(text)
        host = (u.hostname or "").lower()
        port = u.port
    except ValueError:
        return None
    scheme = u.scheme.lower()
    if scheme not in ("http", "https") or not host:
        return None
    if ":" in host:  # an IPv6 literal keeps its brackets, as a browser writes it
        host = f"[{host}]"
    default = 80 if scheme == "http" else 443
    netloc = host if port in (None, default) else f"{host}:{port}"
    path = u.path if u.path and u.path != "/" else ""
    return clean_target(f"{netloc}{path}")


def _program_of(command: str) -> "str | None":
    s = command.strip()
    quoted = re.match(r"^([\"'])(.*?)\1", s)
    first = quoted.group(2) if quoted else (re.split(r"\s+", s)[0] if s else "")
    if not first or "=" in first:
        return None
    name = re.sub(r"\.(exe|cmd|bat|ps1)$", "", _base_name(first), flags=re.I)
    return name if re.fullmatch(r"[A-Za-z][A-Za-z0-9._+\-]{0,31}", name, flags=_A) else None


def _service_words(raw: Any) -> str:
    if not isinstance(raw, str):
        return ""
    flat = _SPACE.sub(" ", _CONTROL.sub(" ", raw)).strip()
    named = re.match(r"^(.+?)\s*\([^()]*\)$", flat)
    return (named.group(1) if named else flat).strip()


def step_target(name: str, args: Any) -> "str | None":
    """The safe target of one call, or None when there is nothing safe to
    say. ``args`` are the call's arguments as the stream lane carried them
    (the ``tool_call`` frame's ``args``)."""
    try:
        if not isinstance(args, dict):
            return None
        kind = KIND_OF.get(name) if isinstance(name, str) else None
        if kind in ("read", "change", "folder"):
            return _file_target(args, PATH_KEYS)
        if kind == "make":
            return _file_target(args, MADE_KEYS)
        if kind in ("search", "web"):
            q = _str(args, "query") or _str(args, "q") or _str(args, "pattern")
            return clean_target(_scrub_paths(q)) if q else None
        if kind == "page":
            u = _str(args, "url")
            return _host_of(u) if u else None
        if kind == "site":
            if name in ("page_open", "page_screenshot"):
                u = _str(args, "url")
                return page_where(u) if u else None
            if name in ("page_click", "page_type"):
                t = _str(args, "target") or _str(args, "css")
                return clean_target(t) if t else None
            return None
        if kind == "service":
            s = _str(args, "service")
            return clean_target(_service_words(s)) if s else None
        if kind == "command":
            if name == "run_code":
                lang = _str(args, "language")
                ok = lang and re.fullmatch(r"[A-Za-z][A-Za-z0-9+#.\-]{0,19}", lang.strip(), flags=_A)
                return lang.strip().lower() if ok else None
            c = _str(args, "command")
            return _program_of(c) if c else None
        return None
    except Exception:  # noqa: BLE001 — a step's words never break a reply line
        return None


__all__ = ["KIND_OF", "TARGET_MAX", "clean_target", "page_where", "secret_looking", "step_target"]
