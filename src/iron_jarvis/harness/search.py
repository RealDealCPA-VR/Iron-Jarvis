"""``grep``'s full form and the ``glob`` tool (v1.364.0, IJ harness Phase 1).

Claude Code's ``Grep`` (ripgrep underneath) takes an output mode, context
lines, a case switch, a file filter, a type, a head limit with an offset and
a multiline switch; its ``Glob`` lists files by pattern, newest first, capped
at 100. Jarvis's ``grep`` took a pattern and a folder. This module is the
engine behind the new arguments; the tools (``tools/builtins.GrepTool`` and
``harness/tools.GlobTool``) only parse arguments and render.

THE OLD FORM IS UNTOUCHED: ``grep`` with only ``pattern`` / ``path`` still
runs its original code and prints exactly what it printed. Any new argument
switches to this engine.

Bounded like every walk since v1.153.1: the shared ``_walk_files`` (pruned
heavy folders, a file cap, a deadline), a per-file size cap, a hard ceiling on
output entries, and every limit hit is SAID in the output — a silently short
answer reads as complete. BLOCKING throughout: the tools call it from a
worker thread.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .globmatch import GlobError, literal_prefix, matches_filter, matches_path, normalize_pattern

#: Files the new grep looks at, at most (the old form keeps its 2,000).
GREP_MAX_FILES = 20_000
#: Files the glob walk visits, at most.
GLOB_MAX_FILES = 20_000
#: Per-file size cap (the old form's too).
GREP_MAX_FILE_BYTES = 2_000_000
#: Search time budget.
SEARCH_DEADLINE_S = 10.0
#: Default and ceiling of grep's ``head_limit`` (0 means "the ceiling").
GREP_DEFAULT_HEAD_LIMIT = 250
GREP_HARD_LIMIT = 5_000
#: Context lines either side, at most.
GREP_MAX_CONTEXT = 50
#: A shown line is cut past this.
GREP_LINE_MAX = 2_000
#: Glob shows at most this many paths.
GLOB_MAX_RESULTS = 100

OUTPUT_MODES = ("content", "files_with_matches", "count")

#: ``type`` → file name suffixes (ripgrep's common names).
FILE_TYPES: dict[str, tuple[str, ...]] = {
    "py": (".py", ".pyi", ".pyw"),
    "python": (".py", ".pyi", ".pyw"),
    "js": (".js", ".mjs", ".cjs", ".jsx"),
    "ts": (".ts", ".tsx", ".mts", ".cts"),
    "jsx": (".jsx",),
    "tsx": (".tsx",),
    "json": (".json", ".jsonc"),
    "md": (".md", ".markdown", ".mdx"),
    "markdown": (".md", ".markdown", ".mdx"),
    "html": (".html", ".htm"),
    "css": (".css", ".scss", ".sass", ".less"),
    "yaml": (".yaml", ".yml"),
    "toml": (".toml",),
    "rust": (".rs",),
    "rs": (".rs",),
    "go": (".go",),
    "java": (".java",),
    "kotlin": (".kt", ".kts"),
    "c": (".c", ".h"),
    "cpp": (".cpp", ".cc", ".cxx", ".hpp", ".hh", ".hxx", ".h"),
    "cs": (".cs",),
    "csharp": (".cs",),
    "rb": (".rb",),
    "ruby": (".rb",),
    "php": (".php",),
    "sh": (".sh", ".bash", ".zsh"),
    "ps": (".ps1", ".psm1", ".psd1"),
    "powershell": (".ps1", ".psm1", ".psd1"),
    "sql": (".sql",),
    "xml": (".xml",),
    "txt": (".txt",),
    "swift": (".swift",),
    "lua": (".lua",),
}


class SearchError(ValueError):
    """A search that cannot run as asked; the message is the sentence."""


@dataclass
class GrepOptions:
    pattern: str
    output_mode: str = "content"
    before: int = 0
    after: int = 0
    case_insensitive: bool = False
    glob: str | None = None
    file_type: str | None = None
    head_limit: int = GREP_DEFAULT_HEAD_LIMIT
    offset: int = 0
    multiline: bool = False
    line_numbers: bool = True


@dataclass
class SearchReport:
    """What a search found and every hole in it, for the tool to render."""

    entries: list[str] = field(default_factory=list)
    total_entries: int = 0
    files_matched: int = 0
    matches: int = 0
    walk_truncated: str = ""
    unreadable: int = 0
    oversize: int = 0


def _rel(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root)).replace("\\", "/")
    except ValueError:
        return ""


def _decode(raw: bytes) -> str | None:
    """The old grep's decode, shared: UTF-16 by BOM, NUL = binary (None),
    else the project's decoder."""
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16")
    if b"\x00" in raw:
        return None
    from ..tools.builtins import _decode_text

    return _decode_text(raw)


def _type_suffixes(file_type: str | None) -> tuple[str, ...] | None:
    if not file_type:
        return None
    key = file_type.strip().lower().lstrip(".")
    if key in FILE_TYPES:
        return FILE_TYPES[key]
    raise SearchError(
        f"unknown file type {file_type!r} — use one of: "
        + ", ".join(sorted(FILE_TYPES))
        + " (or a `glob` like '*.ext')"
    )


def _cut_line(text: str) -> str:
    if len(text) > GREP_LINE_MAX:
        return text[:GREP_LINE_MAX] + f"… [line cut: {len(text):,} characters]"
    return text


def grep(base: Path, root: Path, opts: GrepOptions) -> SearchReport:
    """Search ``base`` (a file or folder under ``root``) and return the
    report. BLOCKING."""
    from ..tools.builtins import _walk_files

    flags = re.IGNORECASE if opts.case_insensitive else 0
    if opts.multiline:
        flags |= re.MULTILINE | re.DOTALL
    try:
        rx = re.compile(opts.pattern, flags)
    except re.error as exc:
        raise SearchError(f"bad regex: {exc}") from None
    suffixes = _type_suffixes(opts.file_type)
    if opts.glob:
        try:
            normalize_pattern(opts.glob)
        except GlobError as exc:
            raise SearchError(f"bad glob: {exc}") from None

    report = SearchReport()
    started = time.monotonic()
    if base.is_file():
        files, report.walk_truncated = [base], ""
    else:
        files, report.walk_truncated = _walk_files(
            base, limit=GREP_MAX_FILES, deadline_s=SEARCH_DEADLINE_S
        )
    #: (rel, mtime, matched line numbers ordered, total matches, lines)
    found: list[tuple[str, float, list[int], int, list[str]]] = []
    for fp in sorted(files, key=lambda p: _rel(p, root)):
        if time.monotonic() - started >= SEARCH_DEADLINE_S:
            report.walk_truncated = report.walk_truncated or f"stopped after {SEARCH_DEADLINE_S:.0f}s"
            break
        rel = _rel(fp, root)
        if not rel:
            continue
        if suffixes is not None and not rel.lower().endswith(suffixes):
            continue
        if opts.glob and not matches_filter(rel, opts.glob):
            continue
        try:
            stat = fp.stat()
            if stat.st_size > GREP_MAX_FILE_BYTES:
                report.oversize += 1
                continue
            raw = fp.read_bytes()
        except (OSError, ValueError):
            report.unreadable += 1
            continue
        try:
            text = _decode(raw)
        except Exception:  # noqa: BLE001 — a REPORTED skip, never a silent miss
            report.unreadable += 1
            continue
        if text is None:
            continue  # binary
        text = text.replace("\r\n", "\n")
        lines = text.split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        hit_lines: list[int] = []
        count = 0
        if opts.multiline:
            seen: set[int] = set()
            for m in rx.finditer(text):
                count += 1
                first = text.count("\n", 0, m.start()) + 1
                last = text.count("\n", 0, max(m.end() - 1, m.start())) + 1
                for no in range(first, last + 1):
                    if no not in seen:
                        seen.add(no)
                        hit_lines.append(no)
            hit_lines.sort()
        else:
            for no, line in enumerate(lines, 1):
                if rx.search(line):
                    hit_lines.append(no)
            count = len(hit_lines)
        if not hit_lines:
            continue
        found.append((rel, stat.st_mtime, hit_lines, count, lines))

    report.files_matched = len(found)
    report.matches = sum(f[3] for f in found)
    #: (text, starts a new group) — a "--" line goes before a new group in
    #: content mode with context, as ripgrep prints it.
    entries: list[tuple[str, bool]] = []
    if opts.output_mode == "files_with_matches":
        # Claude Code's order: most recently modified first.
        for rel, _mt, _h, _c, _l in sorted(found, key=lambda f: (-f[1], f[0])):
            entries.append((rel, False))
    elif opts.output_mode == "count":
        for rel, _mt, _h, count, _l in found:
            entries.append((f"{rel}:{count}", False))
    else:
        grouped = bool(opts.before or opts.after)
        for rel, _mt, hits, _c, lines in found:
            wanted: dict[int, bool] = {}
            for no in hits:
                for ctx_no in range(max(1, no - opts.before), min(len(lines), no + opts.after) + 1):
                    wanted.setdefault(ctx_no, False)
                wanted[no] = True
            prev: int | None = None
            for no in sorted(wanted):
                new_group = grouped and (prev is None or no != prev + 1)
                sep = ":" if wanted[no] else "-"
                body = _cut_line(lines[no - 1]) if no - 1 < len(lines) else ""
                text = f"{rel}{sep}{no}{sep}{body}" if opts.line_numbers else f"{rel}{sep}{body}"
                entries.append((text, new_group))
                prev = no
    # head_limit / offset count ENTRIES (lines in content mode, files
    # otherwise); the "--" separators are not entries.
    report.total_entries = len(entries)
    limit = opts.head_limit if opts.head_limit > 0 else GREP_HARD_LIMIT
    limit = min(limit, GREP_HARD_LIMIT)
    shown: list[str] = []
    for text, new_group in entries[opts.offset : opts.offset + limit]:
        if new_group and shown:
            shown.append("--")
        shown.append(text)
    report.entries = shown
    return report


def render_grep(report: SearchReport, opts: GrepOptions) -> str:
    """The text the model reads: the entries, then one line per hole."""
    shown = sum(1 for e in report.entries if e != "--")
    if report.total_entries == 0:
        out = "No matches found."
    else:
        out = "\n".join(report.entries)
        if opts.output_mode == "count":
            out += f"\n\n{report.matches} match{'es' if report.matches != 1 else ''} in {report.files_matched} file{'s' if report.files_matched != 1 else ''}."
        first = opts.offset + 1
        last = opts.offset + shown
        if shown < report.total_entries:
            unit = {"content": "lines", "files_with_matches": "files", "count": "files"}[opts.output_mode]
            if shown == 0:
                out = f"[offset {opts.offset:,} is past the end: there are {report.total_entries:,} {unit}]"
            else:
                more = "use offset to see more" if last < report.total_entries else "lower the offset to see the earlier ones"
                out += f"\n\n[showing {unit} {first:,}–{last:,} of {report.total_entries:,}; {more}]"
    if report.walk_truncated:
        out += f"\n\n[search truncated — {report.walk_truncated}. This did NOT cover every file; narrow `path`, `glob` or `type`.]"
    if report.unreadable:
        out += (
            f"\n\n[{report.unreadable} file(s) skipped — unreadable encoding or denied "
            "access. This search did NOT cover them.]"
        )
    if report.oversize:
        out += (
            f"\n\n[{report.oversize} file(s) skipped as oversize (over "
            f"{GREP_MAX_FILE_BYTES // 1_000_000} MB each). This search did NOT cover them; "
            "open one directly with read_file if it matters.]"
        )
    return out


def parse_grep_options(args: dict[str, Any]) -> GrepOptions:
    """The new arguments, checked; :class:`SearchError` in words. Claude
    Code's spellings (``-A``, ``-B``, ``-C``, ``-i``, ``-n``) are accepted
    beside the plain ones."""
    from ..tools.builtins import _bool_arg, _int_arg

    def _pick(*names: str) -> Any:
        for n in names:
            if args.get(n) is not None:
                return args[n]
        return None

    try:
        mode = str(_pick("output_mode") or "content").strip()
        if mode not in OUTPUT_MODES:
            raise SearchError(f"output_mode must be one of {', '.join(OUTPUT_MODES)}, got {mode!r}")
        ctx = _pick("context", "-C")
        both = 0 if ctx is None else _int_arg(ctx, "context", minimum=0)
        before_raw = _pick("before_context", "-B")
        after_raw = _pick("after_context", "-A")
        before = both if before_raw is None else _int_arg(before_raw, "before_context", minimum=0)
        after = both if after_raw is None else _int_arg(after_raw, "after_context", minimum=0)
        ci_raw = _pick("case_insensitive", "-i")
        ln_raw = _pick("line_numbers", "-n")
        head_raw = _pick("head_limit")
        off_raw = _pick("offset")
        ml_raw = _pick("multiline")
        opts = GrepOptions(
            pattern=str(args.get("pattern", "")),
            output_mode=mode,
            before=min(before, GREP_MAX_CONTEXT),
            after=min(after, GREP_MAX_CONTEXT),
            case_insensitive=False if ci_raw is None else _bool_arg(ci_raw, "case_insensitive"),
            glob=(str(args["glob"]).strip() or None) if args.get("glob") is not None else None,
            file_type=(str(args["type"]).strip() or None) if args.get("type") is not None else None,
            head_limit=GREP_DEFAULT_HEAD_LIMIT if head_raw is None else _int_arg(head_raw, "head_limit", minimum=0),
            offset=0 if off_raw is None else _int_arg(off_raw, "offset", minimum=0),
            multiline=False if ml_raw is None else _bool_arg(ml_raw, "multiline"),
            line_numbers=True if ln_raw is None else _bool_arg(ln_raw, "line_numbers"),
        )
    except ValueError as exc:
        raise SearchError(str(exc)) from None
    return opts


#: Every argument that switches ``grep`` to this engine.
GREP_NEW_KEYS = (
    "output_mode", "context", "before_context", "after_context", "-A", "-B", "-C",
    "case_insensitive", "-i", "glob", "type", "head_limit", "offset", "multiline",
    "line_numbers", "-n",
)


def wants_new_grep(args: dict[str, Any]) -> bool:
    return any(args.get(k) is not None for k in GREP_NEW_KEYS)


# --------------------------------------------------------------------------- #
# glob
# --------------------------------------------------------------------------- #


@dataclass
class GlobReport:
    paths: list[str] = field(default_factory=list)
    total: int = 0
    walk_truncated: str = ""


def glob_files(base: Path, root: Path, pattern: str) -> GlobReport:
    """Files under ``base`` whose path relative to ``base`` matches
    ``pattern``, newest first, as paths relative to ``root``. BLOCKING."""
    from ..tools.builtins import _walk_files

    try:
        norm = normalize_pattern(pattern)
    except GlobError as exc:
        raise SearchError(str(exc)) from None
    start = base
    prefix = literal_prefix(norm)
    if prefix:
        candidate = base / prefix
        if not candidate.is_dir():
            return GlobReport()
        start = candidate
    report = GlobReport()
    files, report.walk_truncated = _walk_files(start, limit=GLOB_MAX_FILES, deadline_s=SEARCH_DEADLINE_S)
    hits: list[tuple[float, str]] = []
    for fp in files:
        rel_base = _rel(fp, base)
        if not rel_base or not matches_path(rel_base, norm):
            continue
        rel_root = _rel(fp, root)
        if not rel_root:
            continue
        try:
            mtime = fp.stat().st_mtime
        except OSError:
            mtime = 0.0
        hits.append((mtime, rel_root))
    hits.sort(key=lambda h: (-h[0], h[1]))
    report.total = len(hits)
    report.paths = [h[1] for h in hits[:GLOB_MAX_RESULTS]]
    return report


def render_glob(report: GlobReport) -> str:
    if not report.total:
        out = "No files found."
    else:
        out = "\n".join(report.paths)
        if report.total > len(report.paths):
            out += f"\n\n[{report.total - len(report.paths):,} more not shown — newest {len(report.paths)} of {report.total:,}; narrow the pattern or `path`]"
    if report.walk_truncated:
        out += f"\n\n[search truncated — {report.walk_truncated}. Files beyond that were NOT looked at; narrow `path` or the pattern.]"
    return out


__all__ = [
    "GrepOptions", "SearchError", "SearchReport", "GlobReport", "grep", "render_grep",
    "parse_grep_options", "wants_new_grep", "glob_files", "render_glob",
]
