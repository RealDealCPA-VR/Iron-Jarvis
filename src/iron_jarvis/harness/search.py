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

ONE PATTERN CANNOT FREEZE THE APP (v1.516.0, second review S4). Python's
``re`` cannot be interrupted, and it HOLDS THE INTERPRETER LOCK while it
matches: one pattern that backtracks without end (``(a+)+$`` against a long
run of ``a`` then ``b``) stopped the whole daemon, event loop included, from a
worker thread too (measured: the loop stood still for the whole match). The
time limit was read only between files, so it never fired. A pattern that can
backtrack (:func:`plain_pattern` says no) is therefore matched in its OWN
short-lived program (``search-worker``: this module's :func:`main`, started
through ``core/procs`` like the OCR and Excel children), which is killed at
the limit (plus :data:`SEARCH_START_S` for its start). What it found before
then is kept, and the answer names the file it was stuck on and says why. A
plain pattern (no repeat, no group, short) cannot backtrack and still
searches in this process, as before.
"""

from __future__ import annotations

import json
import logging
import re
import sys
import time
from dataclasses import asdict, dataclass, field
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
#: v1.516.0 (S4): how long the search program may take to START, on top of
#: the time budget, before it is killed (a frozen start is ~0.3 s, a cold
#: start from source on a busy PC a few seconds).
SEARCH_START_S = 5.0
#: The hidden subcommand of the frozen exe that runs :func:`main`
#: (``packaging/ironjarvis_entry._fast_path``, ``daemon/cli.search_worker``).
WORKER_SUBCOMMAND = "search-worker"
#: A pattern longer than this is never judged plain.
PLAIN_PATTERN_MAX = 200
#: Lead's review of S4: the search program costs about half a second per
#: search, and most patterns a model writes carry a repeat (``\w+``, ``.*``).
#: A pattern with NO group can only cost a polynomial of the line's length,
#: so it is matched in this process, line by line, as long as no line could
#: cost more than about this many steps (:func:`in_process_line_limit`); the
#: first longer line sends the whole search to the search program instead.
IN_PROCESS_STEPS = 50_000_000
#: With more repeats than this a pattern always goes to the search program.
IN_PROCESS_MAX_REPEATS = 2
#: In this process the time limit is read every this many lines of a file too.
_DEADLINE_EVERY_LINES = 64

log = logging.getLogger("iron_jarvis.harness.search")
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


class LineTooLong(Exception):
    """A line longer than this process may match with the search's pattern
    (:func:`in_process_line_limit`): the search goes to the search program."""


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


#: Characters that make a pattern able to backtrack: a repeat (``*``, ``+``,
#: ``?``, ``{n,m}``) or a group (which a backreference or a look-around needs).
_NOT_PLAIN_RX = re.compile(r"[*+?{(]")


def plain_pattern(pattern: str) -> bool:
    """Can ``pattern`` be matched in this process without any risk of running
    for ever (v1.516.0, S4)? True for a short pattern with no repeat and no
    group — words, classes, anchors, ``|`` — read after its escaped
    characters are set aside (``\\(`` is a plain bracket). Such a pattern
    tries at most its own length at each place in a line. Anything else is
    matched by the search program."""
    text = str(pattern or "")
    if len(text) > PLAIN_PATTERN_MAX:
        return False
    return not _NOT_PLAIN_RX.search(re.sub(r"\\.", "", text, flags=re.S))


def in_process_line_limit(pattern: str, *, multiline: bool = False) -> int:
    """How long a line this process may match with ``pattern`` (lead's review
    of S4): ``-1`` any length (a plain pattern), ``0`` none (the search
    program matches it), else the longest line allowed.

    A pattern with no group cannot nest a repeat, so matching one line of
    ``n`` characters costs at most about ``n ** (repeats + 1)`` steps, doubled
    for each ``?``. The limit is the ``n`` that keeps that under
    :data:`IN_PROCESS_STEPS` (about 7,000 characters with one repeat, 368
    with two), which bounds how long ONE line can hold the
    interpreter lock; the time limit is read between lines. A group, a
    multiline search, or more than :data:`IN_PROCESS_MAX_REPEATS` repeats is
    always the search program's."""
    if plain_pattern(pattern):
        return -1
    text = str(pattern or "")
    if multiline or len(text) > PLAIN_PATTERN_MAX:
        return 0
    bare = re.sub(r"\\.", "", text, flags=re.S)
    if "(" in bare:
        return 0
    repeats = len(re.findall(r"[*+{]", bare))
    optional = bare.count("?")
    if repeats > IN_PROCESS_MAX_REPEATS or optional > 8:
        return 0
    return max(1, int((IN_PROCESS_STEPS / (2 ** optional)) ** (1.0 / (repeats + 1))))


def _secs(value: float) -> str:
    return f"{value:.0f}" if value >= 1 else f"{value:.1f}"


def stuck_words(rel: str, seconds: float) -> str:
    """Why a search was cut short by its pattern (v1.516.0, S4): the words
    the search's note carries (``[search truncated — <these>. …]``)."""
    where = f"on {rel}" if rel else "when it started"
    return (
        f"the pattern was still running {where} after {_secs(seconds)}s, so the search was stopped "
        "there and nothing after it was searched (a pattern with a repeat inside a repeat, like "
        "(a+)+, can run for a very long time on some lines; a simpler pattern avoids that)"
    )


def _compile(opts: GrepOptions) -> "re.Pattern[str]":
    flags = re.IGNORECASE if opts.case_insensitive else 0
    if opts.multiline:
        flags |= re.MULTILINE | re.DOTALL
    try:
        return re.compile(opts.pattern, flags)
    except re.error as exc:
        raise SearchError(f"bad regex: {exc}") from None


def _scan(
    base: Path,
    root: Path,
    opts: GrepOptions,
    limits: "dict[str, float]",
    on_file: "Any" = None,
    on_found: "Any" = None,
    line_limit: "int | None" = None,
) -> "tuple[str, int, int]":
    """Walk, read and match. ``line_limit`` (in this process, for a pattern
    with a repeat): a longer line raises :class:`LineTooLong`, and the time
    limit is read every :data:`_DEADLINE_EVERY_LINES` lines too (a file cut
    that way is not reported as searched). ``on_file(rel, unreadable, oversize)`` is
    called before a file is matched (with the skips so far), ``on_found``
    with each matched file: ``(rel, mtime, matched line numbers in order,
    total matches, the lines content mode shows as (number, is a match, the
    line already cut))``. Returns ``(walk_truncated, unreadable, oversize)``.
    BLOCKING."""
    from ..tools.builtins import _walk_files

    rx = _compile(opts)
    suffixes = _type_suffixes(opts.file_type)
    deadline_s = float(limits["deadline_s"])
    max_bytes = int(limits["max_file_bytes"])
    unreadable = oversize = 0
    started = time.monotonic()
    if base.is_file():
        files, walk_truncated = [base], ""
    else:
        files, walk_truncated = _walk_files(base, limit=int(limits["max_files"]), deadline_s=deadline_s)
    for fp in sorted(files, key=lambda p: _rel(p, root)):
        if time.monotonic() - started >= deadline_s:
            walk_truncated = walk_truncated or f"stopped after {_secs(deadline_s)}s"
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
            if stat.st_size > max_bytes:
                oversize += 1
                continue
            raw = fp.read_bytes()
        except (OSError, ValueError):
            unreadable += 1
            continue
        try:
            text = _decode(raw)
        except Exception:  # noqa: BLE001 — a REPORTED skip, never a silent miss
            unreadable += 1
            continue
        if text is None:
            continue  # binary
        if on_file is not None:
            on_file(rel, unreadable, oversize)
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
            cut = False
            for no, line in enumerate(lines, 1):
                if line_limit is not None:
                    if len(line) > line_limit:
                        raise LineTooLong(rel)
                    if not no % _DEADLINE_EVERY_LINES and time.monotonic() - started >= deadline_s:
                        cut = True
                        break
                if rx.search(line):
                    hit_lines.append(no)
            if cut:
                walk_truncated = walk_truncated or f"stopped after {_secs(deadline_s)}s"
                break
            count = len(hit_lines)
        if not hit_lines:
            continue
        shown: list[tuple[int, bool, str]] = []
        if opts.output_mode == "content":
            wanted: dict[int, bool] = {}
            for no in hit_lines:
                for ctx_no in range(max(1, no - opts.before), min(len(lines), no + opts.after) + 1):
                    wanted.setdefault(ctx_no, False)
                wanted[no] = True
            for no in sorted(wanted):
                body = _cut_line(lines[no - 1]) if no - 1 < len(lines) else ""
                shown.append((no, wanted[no], body))
        if on_found is not None:
            on_found((rel, stat.st_mtime, hit_lines, count, shown))
    return walk_truncated, unreadable, oversize


def _limits(deadline_s: "float | None") -> "dict[str, float]":
    """The limits, read NOW (a test or a caller may have changed them)."""
    return {
        "deadline_s": SEARCH_DEADLINE_S if deadline_s is None else float(deadline_s),
        "max_files": GREP_MAX_FILES,
        "max_file_bytes": GREP_MAX_FILE_BYTES,
    }


def worker_argv() -> "list[str]":
    """How to start the search program: the frozen exe's hidden subcommand
    (served by ``packaging/ironjarvis_entry._fast_path``), else this
    interpreter."""
    if getattr(sys, "frozen", False):
        return [sys.executable, WORKER_SUBCOMMAND]
    return [sys.executable, "-m", "iron_jarvis.harness.search"]


#: What still works when the search program does not (lead's review).
SIMPLER_WORDS = (
    "A pattern with no brackets group and at most a few repeats is searched without that program."
)


def _in_program(request: dict[str, Any], deadline_s: float) -> "tuple[list[dict[str, Any]], bool]":
    """Run ``request`` in the search program, killed (its whole tree) at
    ``deadline_s`` + :data:`SEARCH_START_S`. Returns ``(records it wrote,
    timed out)``. BLOCKING — never on the event loop. Raises
    :class:`SearchError` in words when the program could not run at all."""
    from ..core import procs

    try:
        res = procs.run(
            worker_argv(),
            timeout=deadline_s + SEARCH_START_S,
            input=json.dumps(request).encode("ascii"),
        )
    except OSError as exc:
        log.warning("search: the search program did not start: %s", exc)
        raise SearchError("the search could not run: its search program did not start. " + SIMPLER_WORDS) from None
    records: list[dict[str, Any]] = []
    for raw in (res.stdout or b"").split(b"\n"):
        if not raw.strip():
            continue
        try:
            rec = json.loads(raw)
        except ValueError:
            continue  # the last line, cut by the kill
        if isinstance(rec, dict):
            records.append(rec)
    if not res.timed_out and not any("end" in r for r in records):
        log.warning("search: the search program ended without an answer (exit %s): %s",
                    res.returncode, procs.text(res.stderr)[-600:])
        raise SearchError(
            "the search could not run: its search program stopped without an answer. " + SIMPLER_WORDS)
    return records, bool(res.timed_out)


def _stuck_on(records: list[dict[str, Any]]) -> str:
    at = [r["at"] for r in records if "at" in r]
    return str(at[-1]) if at else ""


def grep(base: Path, root: Path, opts: GrepOptions, *, deadline_s: "float | None" = None) -> SearchReport:
    """Search ``base`` (a file or folder under ``root``) and return the
    report. BLOCKING, and bounded in time INSIDE a file too (v1.516.0, S4):
    a pattern that can backtrack is matched by the search program, killed at
    the limit; what it found before then is kept and the report says where
    and why it stopped."""
    _compile(opts)  # a bad pattern is said here, before any program starts
    _type_suffixes(opts.file_type)
    if opts.glob:
        try:
            normalize_pattern(opts.glob)
        except GlobError as exc:
            raise SearchError(f"bad glob: {exc}") from None

    report = SearchReport()
    limits = _limits(deadline_s)
    found: list[Any] = []
    line_limit = in_process_line_limit(opts.pattern, multiline=opts.multiline)
    here = False
    if line_limit != 0:
        try:
            report.walk_truncated, report.unreadable, report.oversize = _scan(
                base, root, opts, limits, on_found=found.append,
                line_limit=None if line_limit < 0 else line_limit,
            )
            here = True
        except LineTooLong:
            found.clear()  # a line too long to match here: the search program does it all
    if not here:
        request = {"kind": "grep", "base": str(base), "root": str(root), "opts": asdict(opts),
                   "limits": limits}
        records, _timed_out = _in_program(request, limits["deadline_s"])
        found = [tuple(r["found"]) for r in records if "found" in r]
        end = next((r["end"] for r in records if "end" in r), None)
        if end is not None:
            report.walk_truncated, report.unreadable, report.oversize = str(end[0]), int(end[1]), int(end[2])
        else:
            report.walk_truncated = stuck_words(_stuck_on(records), limits["deadline_s"])
            skips = [r["skipped"] for r in records if "skipped" in r]
            if skips:
                report.unreadable, report.oversize = int(skips[-1][0]), int(skips[-1][1])

    report.files_matched = len(found)
    report.matches = sum(f[3] for f in found)
    #: (text, starts a new group) — a "--" line goes before a new group in
    #: content mode with context, as ripgrep prints it.
    entries: list[tuple[str, bool]] = []
    if opts.output_mode == "files_with_matches":
        # Claude Code's order: most recently modified first.
        for rel, _mt, _h, _c, _s in sorted(found, key=lambda f: (-f[1], f[0])):
            entries.append((rel, False))
    elif opts.output_mode == "count":
        for rel, _mt, _h, count, _s in found:
            entries.append((f"{rel}:{count}", False))
    else:
        grouped = bool(opts.before or opts.after)
        for rel, _mt, _h, _c, shown in found:
            prev: int | None = None
            for no, is_hit, body in shown:
                new_group = grouped and (prev is None or no != prev + 1)
                sep = ":" if is_hit else "-"
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


# --------------------------------------------------------------------------- #
# the search program (v1.516.0, S4)
# --------------------------------------------------------------------------- #


def plain_grep_bounded(base: Path, root: Path, pattern: str) -> "tuple[list[str], str, int, int]":
    """``grep``'s original form (pattern and path only,
    ``tools/builtins._plain_grep``) for a pattern that can backtrack: run in
    the search program, killed at the original form's limit. The same
    ``(hits, truncated, unreadable, oversize)`` as the original; when it was
    cut short, the matches before then and the words of why. BLOCKING."""
    from ..tools import builtins as B

    line_limit = in_process_line_limit(pattern)
    if line_limit != 0:
        try:
            return B._plain_grep(
                base, root, re.compile(pattern), line_limit=None if line_limit < 0 else line_limit,
            )
        except LineTooLong:
            pass  # a line too long to match here: the search program does it all
    limits = {"deadline_s": float(B._WALK_DEADLINE_S), "max_files": B._MAX_GREP_FILES,
              "max_file_bytes": B._MAX_GREP_FILE_BYTES, "max_hits": B._MAX_GREP_HITS}
    request = {"kind": "plain", "base": str(base), "root": str(root), "pattern": pattern, "limits": limits}
    records, _timed_out = _in_program(request, limits["deadline_s"])
    end = next((r["end"] for r in records if "end" in r), None)
    if end is not None:
        return [str(h) for h in end[0]], str(end[1]), int(end[2]), int(end[3])
    hits = [str(h) for r in records if "hits" in r for h in r["hits"]]
    skips = [r["skipped"] for r in records if "skipped" in r]
    unreadable, oversize = (int(skips[-1][0]), int(skips[-1][1])) if skips else (0, 0)
    return hits, stuck_words(_stuck_on(records), limits["deadline_s"]), unreadable, oversize


def _serve(request: dict[str, Any], emit: Any) -> None:
    """Answer one request, writing records with ``emit`` as it goes: ``at``
    (the file about to be matched, with the skips so far), ``found`` /
    ``hits`` (what matched) and, when it finished, ``end``."""
    base, root, limits = Path(request["base"]), Path(request["root"]), request["limits"]
    if request.get("kind") == "plain":
        from ..tools.builtins import _plain_grep

        hits: list[str] = []
        sent = 0

        def on_plain_file(rel: str, unreadable: int, oversize: int) -> None:
            nonlocal sent
            if len(hits) > sent:
                emit({"hits": hits[sent:]})
                sent = len(hits)
            emit({"at": rel, "skipped": [unreadable, oversize]})

        out = _plain_grep(
            base, root, re.compile(request["pattern"]), deadline_s=float(limits["deadline_s"]),
            max_files=int(limits["max_files"]), max_bytes=int(limits["max_file_bytes"]),
            max_hits=int(limits["max_hits"]), hits=hits, on_file=on_plain_file,
        )
        emit({"end": list(out)})
        return
    opts = GrepOptions(**request["opts"])

    def on_file(rel: str, unreadable: int, oversize: int) -> None:
        emit({"at": rel, "skipped": [unreadable, oversize]})

    end = _scan(base, root, opts, limits, on_file=on_file, on_found=lambda f: emit({"found": list(f)}))
    emit({"end": list(end)})


def main() -> int:
    """The search program (``search-worker``): one JSON request on stdin, one
    JSON record per line on stdout, flushed as it goes (what it wrote before
    it was killed is still read). Exit 0 when it answered, 2 on a bad
    request."""
    out = sys.stdout.buffer

    def emit(record: dict[str, Any]) -> None:
        out.write(json.dumps(record, ensure_ascii=True).encode("ascii") + b"\n")
        out.flush()

    try:
        request = json.loads(sys.stdin.buffer.read().decode("ascii") or "null")
        if not isinstance(request, dict):
            raise ValueError("not a request")
        _serve(request, emit)
    except (ValueError, KeyError, TypeError, re.error, SearchError) as exc:
        sys.stderr.write(f"search-worker: {exc}\n")
        return 2
    return 0


__all__ = [
    "GrepOptions", "SearchError", "SearchReport", "GlobReport", "grep", "render_grep",
    "parse_grep_options", "wants_new_grep", "glob_files", "render_glob", "plain_grep_bounded",
    "plain_pattern", "in_process_line_limit", "LineTooLong", "stuck_words", "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
