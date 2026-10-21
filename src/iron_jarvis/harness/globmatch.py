"""Glob patterns for the IJ harness's ``glob`` tool and ``grep``'s filter
(v1.364.0).

One translator, two readings:

* ``glob`` (Claude Code's ``Glob``): the pattern is matched against the
  WHOLE path relative to the search folder. ``*`` and ``?`` never cross a
  ``/``; ``**`` crosses any number of folders (``**/*.py`` = every .py file,
  ``src/**`` = everything under src). ``*.py`` therefore matches only files
  directly in the folder — the shell's own rule.
* ``grep``'s ``glob`` filter (ripgrep's ``--glob``): a pattern WITHOUT a
  ``/`` matches the file's NAME at any depth (``*.py``), one WITH a ``/``
  matches the relative path.

Braces expand (``*.{ts,tsx}``), ``[abc]`` / ``[!abc]`` are character sets.
Matching is case-insensitive on Windows, where the file system is.
Pure; no I/O.
"""

from __future__ import annotations

import functools
import os
import re

#: A pattern may expand into at most this many alternatives.
MAX_BRACE_EXPANSIONS = 64
#: Characters that make a path segment a pattern, not a literal folder.
GLOB_CHARS = frozenset("*?[{")


class GlobError(ValueError):
    """A pattern that cannot be used; the message is the sentence."""


def _expand_braces(pattern: str, budget: list[int]) -> list[str]:
    depth = 0
    start = -1
    for i, ch in enumerate(pattern):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth:
            depth -= 1
            if depth == 0:
                inner = pattern[start + 1 : i]
                parts: list[str] = []
                level = 0
                cur = ""
                for c in inner:
                    if c == "," and level == 0:
                        parts.append(cur)
                        cur = ""
                        continue
                    if c == "{":
                        level += 1
                    elif c == "}":
                        level -= 1
                    cur += c
                parts.append(cur)
                head, tail = pattern[:start], pattern[i + 1 :]
                out: list[str] = []
                for part in parts:
                    for expanded in _expand_braces(head + part + tail, budget):
                        budget[0] -= 1
                        if budget[0] < 0:
                            raise GlobError(
                                f"the pattern expands into more than {MAX_BRACE_EXPANSIONS} "
                                "alternatives — use fewer {a,b} groups"
                            )
                        out.append(expanded)
                return out
    return [pattern]


def _segment_regex(pattern: str) -> str:
    """One brace-free pattern → a regex body (no anchors)."""
    out: list[str] = []
    i = 0
    n = len(pattern)
    while i < n:
        ch = pattern[i]
        if ch == "*":
            if pattern.startswith("**", i):
                # "**/" — any number of folders (including none);
                # a trailing "**" — everything below.
                j = i + 2
                if j < n and pattern[j] == "/":
                    out.append("(?:.*/)?")
                    i = j + 1
                    continue
                out.append(".*")
                i = j
                continue
            out.append("[^/]*")
        elif ch == "?":
            out.append("[^/]")
        elif ch == "[":
            close = pattern.find("]", i + 2 if pattern[i + 1 : i + 2] in ("!", "^") else i + 1)
            if close < 0:
                out.append(re.escape(ch))
            else:
                body = pattern[i + 1 : close]
                if body[:1] in ("!", "^"):
                    body = "^" + body[1:]
                body = body.replace("\\", "\\\\")
                out.append(f"[{body}]")
                i = close + 1
                continue
        else:
            out.append(re.escape(ch))
        i += 1
    return "".join(out)


def normalize_pattern(pattern: str) -> str:
    """Forward slashes, no leading ``./``, no empty segments. Refuses an
    absolute pattern and a ``..`` segment (the search folder is the root)."""
    text = str(pattern or "").strip().replace("\\", "/")
    if not text:
        raise GlobError("the pattern is empty")
    if text.startswith("/") or re.match(r"^[A-Za-z]:", text):
        raise GlobError(
            "the pattern must be relative to the folder searched — put the folder in `path`"
        )
    while text.startswith("./"):
        text = text[2:]
    parts = [p for p in text.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise GlobError("the pattern may not climb out of the folder with '..'")
    return "/".join(parts)


@functools.lru_cache(maxsize=256)
def compile_glob(pattern: str) -> re.Pattern[str]:
    """The compiled matcher for one (normalised) pattern, braces expanded."""
    alternatives = _expand_braces(pattern, [MAX_BRACE_EXPANSIONS])
    body = "|".join(f"(?:{_segment_regex(p)})" for p in alternatives)
    flags = re.IGNORECASE if os.name == "nt" else 0
    return re.compile(f"^(?:{body})$", flags | re.DOTALL)


def matches_path(rel_posix: str, pattern: str) -> bool:
    """Claude Code ``Glob`` reading: the pattern against the whole relative
    path."""
    return bool(compile_glob(normalize_pattern(pattern)).match(rel_posix))


def matches_filter(rel_posix: str, pattern: str) -> bool:
    """ripgrep ``--glob`` reading: no ``/`` in the pattern → the file's name
    at any depth; with one → the relative path."""
    norm = normalize_pattern(pattern)
    if "/" not in norm:
        return bool(compile_glob(norm).match(rel_posix.rsplit("/", 1)[-1]))
    return bool(compile_glob(norm).match(rel_posix))


def literal_prefix(pattern: str) -> str:
    """The leading folders of a pattern that hold no pattern characters
    (``src/app/**/*.ts`` → ``src/app``), so a search can start there instead
    of walking the whole tree. "" when the first segment is a pattern."""
    parts = normalize_pattern(pattern).split("/")
    keep: list[str] = []
    for part in parts[:-1]:  # the last segment names files, not a folder
        if any(c in GLOB_CHARS for c in part):
            break
        keep.append(part)
    return "/".join(keep)
