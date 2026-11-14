"""What a task requires and where it may write, read from the USER's message.

Only the user's own words are read, never a model's reply or a document the
model opened: a file the model reads must not be able to widen (or move) where
the turn may write. Everything here is deterministic text reading; the one
blocking part (:func:`scope_for_turn`: a few stats, one bounded walk of the task
folder, one ``mkdir`` of the scratch folder) is called off the event loop.

WHAT IT READS (each pinned both ways in ``tests/test_deliverables_v1481.py``):

* REQUIRED FILES. A file named under a heading such as "Deliverables",
  "Success criteria", "Outputs", "Required files"; or the object of a making
  verb ("write `OUTPUT/FINAL_REPORT.md`", "save it as report.docx", "export
  the rows to totals.csv"). A file after "of", "from", "using", "based on" is
  an input ("write a summary of report.pdf" requires nothing); a negated verb
  requires nothing.
* WHERE IT MAY WRITE. "Create files only in `OUTPUT/`", "outputs go in
  `results/`", a write or create line under a "Permissions" heading. With no
  such line, the one folder every required file is in, when its name says it
  is an output folder (``output``, ``results``, ``deliverables``…).
* WHAT IT MUST ONLY READ. "READ `INPUT/`" under a permissions heading, "the
  `INPUT/` folder is read-only", "do not modify `INPUT/`".

THE TASK FOLDER. Paths in the message are relative to the folder the user
means, which is not always the chat's folder: when the message names the chat's
own folder as a path's first part ("the workbook in `INPUT/`" while the chat is
in ``...\\INPUT``), the task folder is its parent, and the turn's tools work
there (``rebase``). The live case: ``OUTPUT/x`` written from ``INPUT`` made a
stray ``INPUT\\OUTPUT\\``.

WHAT IT CHANGES. With a scope the turn's file tools refuse a write outside the
allowed folders, into a read-only folder, or a helper script into a deliverable
folder (:func:`write_refusal`, asked by ``tools/registry.invoke``). A scope with
no write rule changes no write at all: it only checks the required files.
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

#: How much of the user's message is read (a pasted README fits; a pasted
#: book does not need to).
MAX_TEXT_CHARS = 20_000
#: Required files kept (more is a list the model cannot do in one turn anyway).
MAX_DELIVERABLES = 20
#: Entries recorded when the task folder is looked at, and how deep.
MAX_WATCH_ENTRIES = 4_000
MAX_WATCH_DEPTH = 8
#: Folders never walked when the task folder is recorded.
_WATCH_PRUNE = frozenset({
    ".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache",
    ".pytest_cache", ".ruff_cache", ".next", "dist", "build", ".idea", ".vs",
})

#: Folder names that say "this is where the results go" (the default write
#: rule when the message names required files but no write rule).
OUTPUT_FOLDER_NAMES = frozenset({
    "output", "outputs", "out", "results", "result", "deliverables",
    "deliverable", "export", "exports", "final",
})

#: A helper script never belongs in a deliverable folder unless it IS one.
SCRIPT_EXTS = frozenset({
    ".py", ".pyw", ".ps1", ".psm1", ".bat", ".cmd", ".sh", ".bash", ".js",
    ".mjs", ".cjs", ".ts", ".vbs", ".rb", ".pl",
})

#: Extensions a bare (un-backticked, separator-less) word must have to count
#: as a file name: document kinds only, so "a Node.js script" or "v1.2"
#: names no file.
_DOC_EXTS = frozenset({
    "md", "markdown", "txt", "csv", "tsv", "xlsx", "xlsm", "xls", "docx", "doc",
    "pdf", "pptx", "json", "html", "htm", "xml", "yaml", "yml", "rtf", "odt",
    "ods", "log", "zip",
})

_TICK_RX = re.compile(r"`([^`\n]{1,240})`")
#: A bare path: optional folders, a name, an extension starting with a letter.
#: Each repetition of the folder part ends at a separator, and the name part
#: is bounded, so this is linear (pinned with a pathological input).
_BARE_FILE_RX = re.compile(
    r"(?<![\w/\\.:-])"
    r"((?:[A-Za-z0-9_][\w.-]{0,80}[\\/]){0,8}[A-Za-z0-9_][\w-]{0,120}(?:\.[\w-]{1,40}){0,3}"
    r"\.([A-Za-z][A-Za-z0-9]{0,7}))"
    r"(?![\w\\/-])"
)
#: A bare folder: a word followed by a separator, then the end of the word.
_BARE_DIR_RX = re.compile(r"(?<![\w/\\.:-])([A-Za-z][\w-]{0,60})[\\/](?=[\s,.;:)\]!?]|$)")
_LIST_RX = re.compile(r"^\s*(?:[-*+•]|\d{1,3}[.)])\s+")
_HEAD_DELIVER_RX = re.compile(
    r"\b(?:deliverables?|success criteria|outputs?|required (?:files|outputs|deliverables)|"
    r"expected (?:outputs?|files)|what (?:to|you must) (?:deliver|produce|hand in))\b",
    re.I,
)
_HEAD_PERMS_RX = re.compile(r"\b(?:permissions?|access|constraints|ground rules|rules)\b", re.I)
#: A verb whose direct object is the thing made.
_MAKE_RX = re.compile(
    r"\b(?:write|create|produce|generate|make(?!\s+sure)|draft|prepare|build|deliver|save|output|compose)\b",
    re.I,
)
#: A verb whose direct object is the source and whose "to/into/as" is the
#: thing made.
_MOVE_RX = re.compile(r"\b(?:export|convert|copy|turn|transform|render)\b", re.I)
_SENTENCE_END_RX = re.compile(r"(?<=[.!?;])\s|\s[-–—]\s")
#: Words between a verb and a file that make the file an INPUT.
_INPUT_WORDS = frozenset({
    "of", "from", "about", "using", "use", "with", "based", "on", "reading",
    "read", "review", "summarizing", "summarising", "against", "per", "via",
})
#: Words that make the next file an OUTPUT after a moving verb.
_OUTPUT_WORDS = frozenset({"to", "into", "as"})
_NEGATED_RX = re.compile(
    r"\b(?:do not|don't|dont|never|without|no need to|must not|mustn't|should not|shouldn't)\b"
    r"(?:\W+\w+){0,3}\W*$",
    re.I,
)
_ONLY_RX = re.compile(r"\b(?:only|solely|exclusively)\b", re.I)
_OUTPUTS_GO_RX = re.compile(
    r"\b(?:outputs?|deliverables|results|all (?:new |output )?files|everything you "
    r"(?:create|make|write|produce))\b.{0,40}?\b(?:go|goes|belong|belongs|must be "
    r"(?:saved|written|placed|put)|should be (?:saved|written|placed|put)|are (?:saved|written))"
    r"\b.{0,20}?\b(?:in|to|into|under)\b",
    re.I,
)
_READ_ONLY_RX = re.compile(r"\bread[- ]only\b", re.I)
_LEAD_READ_RX = re.compile(r"^\s*(?:read|reading|inputs?|sources?)\b", re.I)
_NO_MODIFY_RX = re.compile(
    r"\b(?:do not|don't|dont|never|must not|mustn't|should not)\s+(?:\w+\s+){0,3}?"
    r"(?:modify|change|overwrite|edit|alter|delete|touch|move|rename|write (?:to|in|into))\b",
    re.I,
)
_CONTINUE_RX = re.compile(
    r"^\s*(?:continue|keep going|go on|go ahead|carry on|finish(?: it| up| the task)?|"
    r"do it|yes|ok(?:ay)?|proceed|resume|please continue|try again)\W*$",
    re.I,
)


@dataclass(frozen=True)
class Deliverable:
    """One required file: as the user wrote it, and where it is on disk."""

    text: str
    path: Path


@dataclass
class TaskScope:
    """What one turn's task requires and where it may write.

    ``write_roots`` empty = no write rule: writes are exactly as they always
    were (only the required files are checked). ``workspace`` is the folder
    the chat is bound to; ``root`` is the task folder the user's paths mean
    (the turn's tools work there when ``rebase``).
    """

    root: Path
    workspace: Path
    deliverables: tuple[Deliverable, ...]
    write_roots: tuple[Path, ...] = ()
    read_only: tuple[Path, ...] = ()
    scratch: "Path | None" = None
    rebase: bool = False
    #: Where the write rule came from: "said" (a line of the message),
    #: "output folder" (every required file is in one output folder),
    #: "chat folder" (a rebased task with no rule: the chat's folder plus
    #: the required files' folders), "" (no rule).
    rule: str = ""
    #: The task folder as it was when the turn began (``rel -> (dir, size,
    #: mtime_ns)``), without the write roots and the scratch folder.
    baseline: dict = field(default_factory=dict, compare=False, repr=False)
    watch_complete: bool = field(default=True, compare=False, repr=False)

    @property
    def enforced(self) -> bool:
        return bool(self.write_roots)

    def rel(self, path: Path) -> str:
        """*path* as the user would write it: relative to the task folder."""
        try:
            return str(Path(path).resolve().relative_to(self.root.resolve()))
        except (ValueError, OSError):
            return str(path)

    def is_deliverable(self, path: Path) -> bool:
        key = _key(path)
        return any(_key(d.path) == key for d in self.deliverables)


def _key(path: Path) -> str:
    try:
        return os.path.normcase(str(Path(path).resolve()))
    except OSError:
        return os.path.normcase(os.path.abspath(str(path)))


def _within(target: Path, root: Path) -> bool:
    t, r = _key(target), _key(root)
    return t == r or t.startswith(r.rstrip("\\/") + os.sep)


# --- reading the message ----------------------------------------------------


@dataclass(frozen=True)
class _Token:
    start: int
    end: int
    text: str
    kind: str  # "file" | "dir"


def _clean_token(raw: str) -> str:
    return raw.strip().strip("\"'").rstrip(".,;:)!?")


def _looks_url(text: str) -> bool:
    low = text.lower()
    return "://" in low or low.startswith(("www.", "mailto:"))


def _tick_kind(text: str) -> str:
    if not text or _looks_url(text) or " " in text.strip():
        return ""
    if text.endswith(("/", "\\")):
        return "dir"
    if re.search(r"\.[A-Za-z][A-Za-z0-9]{0,7}$", text):
        return "file"
    return ""


def _bare_counts(path_text: str, ext: str) -> bool:
    """A bare word is a file name only when it is unmistakably one: it has a
    folder, an underscore, a hyphen or a digit in its name, or a document
    extension. "a Node.js script" and "v1.2" name no file."""
    if "/" in path_text or "\\" in path_text:
        return True
    if ext.lower() in _DOC_EXTS:
        return True
    name = path_text.rsplit(".", 1)[0]
    return bool(re.search(r"[_\d-]", name))


def _tokens(line: str) -> list[_Token]:
    found: list[_Token] = []
    taken: list[tuple[int, int]] = []
    for m in _TICK_RX.finditer(line):
        text = _clean_token(m.group(1))
        kind = _tick_kind(text)
        taken.append((m.start(), m.end()))
        if kind:
            found.append(_Token(m.start(), m.end(), text, kind))

    def free(a: int, b: int) -> bool:
        return all(b <= s or a >= e for s, e in taken)

    for m in _BARE_FILE_RX.finditer(line):
        if not free(m.start(), m.end()):
            continue
        text = m.group(1)
        if _looks_url(line[max(0, m.start() - 8): m.end()]) or not _bare_counts(text, m.group(2)):
            continue
        found.append(_Token(m.start(), m.end(), text, "file"))
        taken.append((m.start(), m.end()))
    for m in _BARE_DIR_RX.finditer(line):
        if free(m.start(), m.end()):
            found.append(_Token(m.start(), m.end(), m.group(1) + "/", "dir"))
    found.sort(key=lambda t: t.start)
    return found


def _strip_markdown(line: str) -> str:
    text = line.replace("**", "").replace("__", "")
    text = re.sub(r"^\s*>+\s?", "", text)
    return text


def _is_heading(raw: str, clean: str, has_tokens: bool) -> bool:
    if raw.lstrip().startswith("#"):
        return True
    stripped = clean.strip()
    if has_tokens or not stripped:
        return False
    if re.fullmatch(r"\*\*[^*]{1,80}\*\*:?", raw.strip()):
        return True
    return stripped.endswith(":") and len(stripped) <= 80 and not _LIST_RX.match(clean)


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z']+", text.lower())


@dataclass
class _Reading:
    deliverables: list[str] = field(default_factory=list)
    write_dirs: list[str] = field(default_factory=list)
    read_only: list[str] = field(default_factory=list)
    leads: set = field(default_factory=set)
    #: A required file came from a Deliverables / Outputs list.
    listed: bool = False


def _segment(line: str, start: int) -> str:
    """From *start* to the end of its sentence (or clause after a dash)."""
    m = _SENTENCE_END_RX.search(line, start)
    return line[start: m.start() if m else len(line)]


def _verb_outputs(line: str, toks: list[_Token]) -> list[_Token]:
    """The files a making or moving verb on *line* names as what is made."""
    out: list[_Token] = []
    for rx, moving in ((_MAKE_RX, False), (_MOVE_RX, True)):
        for m in rx.finditer(line):
            if _NEGATED_RX.search(line[max(0, m.start() - 60): m.start()]):
                continue
            seg_end = m.end() + len(_segment(line, m.end()))
            anchor = m.end()
            for tok in toks:
                if tok.start < m.end() or tok.start >= seg_end:
                    continue
                between = _words(line[anchor: tok.start])
                anchor = tok.end
                if len(between) > 8:
                    break
                if _INPUT_WORDS & set(between):
                    # This token is an input; a later one may still be the output.
                    continue
                if moving and not (_OUTPUT_WORDS & set(between)):
                    continue
                out.append(tok)
                if not moving:
                    break
    return out


def _sentences(body: str) -> list[str]:
    """*body* cut into sentences ("Write `a.md`. Do not modify `INPUT/`." is
    two rules); a backticked span is never cut."""
    out: list[str] = []
    start = 0
    in_tick = False
    for i, ch in enumerate(body):
        if ch == "`":
            in_tick = not in_tick
        elif (
            not in_tick and ch in ".!?;" and i + 1 < len(body) and body[i + 1] in " \t"
        ):
            out.append(body[start: i + 1])
            start = i + 1
    out.append(body[start:])
    return [s for s in out if s.strip()]


def _read_sentence(reading: _Reading, text: str, section: str, *, first_of_item: bool) -> None:
    toks = _tokens(text)
    if not toks:
        return
    files = [t for t in toks if t.kind == "file"]
    dirs = [t for t in toks if t.kind == "dir"]
    has_make = bool(_MAKE_RX.search(text))
    lead_read = bool(_LEAD_READ_RX.match(text))
    # What the task must only read.
    if (
        _READ_ONLY_RX.search(text)
        or _NO_MODIFY_RX.search(text)
        or (section == "permissions" and lead_read and not has_make)
    ):
        reading.read_only.extend(t.text for t in toks)
        return
    # Where the task may write.
    if dirs and has_make and (_ONLY_RX.search(text) or section == "permissions"):
        reading.write_dirs.extend(t.text for t in dirs)
    elif dirs and _OUTPUTS_GO_RX.search(text):
        reading.write_dirs.extend(t.text for t in dirs)
    # What the task must produce.
    if (
        section == "deliverables" and files and not lead_read
        and (first_of_item or files[0].start <= 2)
        and not _NEGATED_RX.search(text[: files[0].start])
    ):
        reading.deliverables.append(files[0].text)
        reading.listed = True
    for tok in _verb_outputs(text, files):
        reading.deliverables.append(tok.text)


def _read(text: str) -> _Reading:
    reading = _Reading()
    section = ""
    for raw in text.replace("\r\n", "\n").split("\n"):
        clean = _strip_markdown(raw)
        toks = _tokens(clean)
        for tok in toks:
            parts = re.split(r"[\\/]", tok.text.lstrip("./\\"))
            if len(parts) > 1 and parts[0]:
                reading.leads.add(parts[0].casefold())
        if _is_heading(raw, clean, bool(toks)):
            if _HEAD_DELIVER_RX.search(clean):
                section = "deliverables"
            elif _HEAD_PERMS_RX.search(clean):
                section = "permissions"
            else:
                section = ""
            continue
        if not toks:
            continue
        body = _LIST_RX.sub("", clean, count=1)
        is_item = bool(_LIST_RX.match(clean))
        for i, sentence in enumerate(_sentences(body)):
            _read_sentence(reading, sentence.strip(), section, first_of_item=is_item and i == 0)
    return reading


def _user_text(messages: Any) -> str:
    """The newest user message that states a task: the newest one, or, when
    that is only "continue" / "keep going", the latest earlier one (up to four
    back) that names something to produce."""
    users: list[str] = []
    for msg in list(messages or [])[::-1]:
        role = getattr(msg, "role", None) or (msg.get("role") if isinstance(msg, dict) else None)
        if role != "user":
            continue
        content = getattr(msg, "content", None)
        if content is None and isinstance(msg, dict):
            content = msg.get("content")
        if isinstance(content, str):
            users.append(content[:MAX_TEXT_CHARS])
        if len(users) >= 5:
            break
    if not users:
        return ""
    if not _CONTINUE_RX.match(users[0]):
        return users[0]
    for text in users[1:]:
        reading = _read(text)
        if reading.deliverables or reading.write_dirs:
            return text
    return users[0]


# --- the scope --------------------------------------------------------------


def _resolve(root: Path, text: str) -> Path:
    raw = text.replace("\\", "/").strip()
    while raw.startswith("./"):
        raw = raw[2:]
    path = Path(raw)
    return path if path.is_absolute() else (root / path)


def _usable(root: Path) -> bool:
    try:
        from ..core.fs_policy import fs_path_allowed, is_protected_path

        return root.is_dir() and fs_path_allowed(root) and not is_protected_path(root)
    except Exception:  # noqa: BLE001 - cannot tell: never rebase on a guess
        return False


def _scratch_for(root: Path) -> "Path | None":
    digest = hashlib.sha1(_key(root).encode("utf-8", "replace")).hexdigest()[:12]
    path = Path(tempfile.gettempdir()) / "ij-task-scratch" / digest
    try:
        path.mkdir(parents=True, exist_ok=True)
        return path
    except OSError:
        return None


def snapshot(root: Path, skip: "tuple[Path, ...]") -> "tuple[dict, bool]":
    """``rel -> (is_dir, size, mtime_ns)`` for the task folder, without *skip*
    (the folders the task may write in) and never through a link. Bounded:
    ``complete`` False when it stopped early."""
    found: dict = {}
    skip_keys = {_key(p) for p in skip}
    complete = True
    stack: list[tuple[Path, int]] = [(root, 0)]
    while stack:
        folder, depth = stack.pop()
        try:
            entries = list(os.scandir(folder))
        except OSError:
            continue
        for entry in entries:
            if len(found) >= MAX_WATCH_ENTRIES:
                return found, False
            path = Path(entry.path)
            try:
                is_link = entry.is_symlink() or bool(
                    getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0) & 0x400
                )
            except OSError:
                continue
            if _key(path) in skip_keys:
                continue
            try:
                st = entry.stat(follow_symlinks=False)
                is_dir = entry.is_dir(follow_symlinks=False)
            except OSError:
                continue
            rel = os.path.relpath(entry.path, root)
            found[rel] = (is_dir, 0 if is_dir else st.st_size, 0 if is_dir else st.st_mtime_ns)
            if is_dir and not is_link and entry.name not in _WATCH_PRUNE:
                if depth + 1 >= MAX_WATCH_DEPTH:
                    complete = False
                else:
                    stack.append((path, depth + 1))
    return found, complete


def scope_for_text(text: str, workspace: Path) -> "TaskScope | None":
    """The scope *text* states for a chat bound to *workspace*; None when it
    names no required file and no write rule. BLOCKING (stats, a walk, a
    mkdir): call it off the event loop."""
    if not text or not workspace:
        return None
    reading = _read(text[:MAX_TEXT_CHARS])
    # A TASK, not a one-line request: a Deliverables list, two or more
    # required files, or a write rule. "write new.txt" alone is an ordinary
    # request, and the turn stays exactly as it always was.
    if not (
        reading.listed or len(set(reading.deliverables)) >= 2
        or reading.write_dirs or (reading.deliverables and reading.read_only)
    ):
        return None
    workspace = Path(workspace)
    root = workspace
    rebase = False
    parent = workspace.parent
    if (
        workspace.name
        and workspace.name.casefold() in reading.leads
        and parent != workspace
        and _usable(parent)
    ):
        root, rebase = parent, True
    seen: set[str] = set()
    deliverables: list[Deliverable] = []
    for text_path in reading.deliverables:
        path = _resolve(root, text_path)
        key = _key(path)
        if key in seen:
            continue
        seen.add(key)
        deliverables.append(Deliverable(text_path, path))
        if len(deliverables) >= MAX_DELIVERABLES:
            break
    write_roots = _unique(_resolve(root, t) for t in reading.write_dirs)
    rule = "said" if write_roots else ""
    read_only = _unique(_resolve(root, t) for t in reading.read_only)
    if not write_roots and deliverables:
        parents = _unique(d.path.parent for d in deliverables)
        if (
            len(parents) == 1
            and _key(parents[0]) != _key(root)
            and parents[0].name.casefold() in OUTPUT_FOLDER_NAMES
        ):
            write_roots, rule = parents, "output folder"
    if not write_roots and rebase:
        # The tools now work in the task folder: never wider than what the
        # user chose plus where they asked the files to go.
        write_roots = _unique([workspace, *(d.path.parent for d in deliverables)])
        rule = "chat folder"
    # A folder the user named read-only is never also a place to write.
    read_keys = {_key(p) for p in read_only}
    write_roots = tuple(p for p in write_roots if _key(p) not in read_keys)
    if not deliverables and not write_roots:
        return None
    scratch = _scratch_for(root) if write_roots else None
    scope = TaskScope(
        root=root, workspace=workspace, deliverables=tuple(deliverables),
        write_roots=write_roots, read_only=read_only, scratch=scratch,
        rebase=rebase, rule=rule,
    )
    skip = tuple(write_roots) + ((scratch,) if scratch else ())
    scope.baseline, scope.watch_complete = snapshot(root, skip)
    return scope


def scope_for_turn(messages: Any, workspace: "Path | None") -> "TaskScope | None":
    """The scope of this turn: read from the user's newest message (or the
    task message a "continue" goes back to). BLOCKING; never raises."""
    if workspace is None:
        return None
    try:
        return scope_for_text(_user_text(messages), Path(workspace))
    except Exception:  # noqa: BLE001 - a scope that cannot be read is no scope
        return None


def required_files(text: str, workspace: Path) -> "list[Deliverable]":
    """The files *text* requires that lie INSIDE *workspace*: the reading
    alone (v1.493.0, for a coding session's check-again loop,
    ``harness/finish``). Nothing is made or looked at on disk here: no
    snapshot, no scratch folder, no moving to the folder above. [] for a
    one-line request (the task threshold of :func:`scope_for_text`), for a
    path spelled as another computer's (``\\\\host`` / ``//host``, by its text)
    and for one that leaves the folder. Pure; never raises."""
    try:
        if not text or not workspace:
            return []
        reading = _read(str(text)[:MAX_TEXT_CHARS])
        if not (
            reading.listed or len(set(reading.deliverables)) >= 2
            or reading.write_dirs or (reading.deliverables and reading.read_only)
        ):
            return []
        root = Path(workspace)
        out: list[Deliverable] = []
        seen: set[str] = set()
        for text_path in reading.deliverables:
            raw = str(text_path).replace("\\", "/").strip()
            if raw.startswith("//") or ".." in raw.split("/"):
                continue
            path = _resolve(root, text_path)
            if not _inside_text(path, root):
                continue
            key = os.path.normcase(str(path))
            if key in seen:
                continue
            seen.add(key)
            out.append(Deliverable(text_path, path))
            if len(out) >= MAX_DELIVERABLES:
                break
        return out
    except Exception:  # noqa: BLE001 - a reading that fails names no file
        return []


def _inside_text(path: Path, root: Path) -> bool:
    """Is *path* under *root*, judged by the two paths' TEXT (nothing on disk
    is touched or followed)?"""
    a = os.path.normcase(os.path.normpath(str(path)))
    b = os.path.normcase(os.path.normpath(str(root)))
    return a == b or a.startswith(b.rstrip("\\/") + os.sep)


def _unique(paths: Any) -> tuple[Path, ...]:
    out: list[Path] = []
    seen: set[str] = set()
    for p in paths:
        key = _key(p)
        if key not in seen:
            seen.add(key)
            out.append(Path(p))
    return tuple(out)


# --- the turn's scope while a tool runs -------------------------------------

_SCOPE: "ContextVar[TaskScope | None]" = ContextVar("task_scope", default=None)


def current_scope() -> "TaskScope | None":
    """The scope of the tool call now running (set by ``registry.invoke``)."""
    return _SCOPE.get()


@contextmanager
def using_scope(scope: "TaskScope | None") -> Iterator[None]:
    token = _SCOPE.set(scope)
    try:
        yield
    finally:
        _SCOPE.reset(token)


def in_scratch(target: Path) -> bool:
    """True when *target* is inside the running call's scratch folder (the one
    place outside the task folder a scoped turn's file tools may write)."""
    scope = _SCOPE.get()
    return bool(scope and scope.scratch and _within(target, scope.scratch))


# --- refusing a write -------------------------------------------------------

#: The arguments each file-writing tool WRITES (a tool not listed is never
#: refused here; what it writes is still seen by the folder check at the end).
#: A tuple of keys is "the first one given".
WRITE_ARGS: dict[str, tuple[tuple[str, ...], ...]] = {
    "write_document": (("path",),),
    "write_file": (("path",),),
    "edit_file": (("path",),),
    "multi_edit": (("path",),),
    "notebook_edit": (("path",),),
    "excel_edit": (("path",),),
    "excel_apply_spec": (("path",),),
    "docx_edit": (("output", "path"),),
    "pdf_form_fill": (("output", "path"),),
    "pdf_split": (("out_dir",),),
    "pdf_arrange": (("output",),),
    "redact_pii": (("output_path",),),
    "convert_document": (("target",),),
    "image_convert": (("target",),),
    "image_resize": (("target",),),
    "excel_reconcile": (("save_as",),),
    "batch_documents": (("output",),),
    "rename_file": (("path",), ("new_path",)),
}

#: Tools whose ``command`` is read for writes (redirects, copy, del…).
COMMAND_TOOLS = frozenset({"shell", "process_start"})

_REDIRECT_RX = re.compile(r"(?<![<\w&])[12]?>>?\s*(?!&)(\"[^\"\n]{1,260}\"|[^\s\"|&<>;]{1,260})")
_PS_WRITE_RX = re.compile(
    r"\b(?:Out-File|Set-Content|Add-Content|New-Item|Copy-Item|Move-Item|Remove-Item|Rename-Item)\b"
    r"((?:\s+(?:-[A-Za-z]+\s+)?(?:\"[^\"\n]{1,260}\"|'[^'\n]{1,260}'|[^\s\"'|;&]{1,260})){0,6})",
    re.I,
)
_CMD_WRITE_RX = re.compile(
    r"(?:^|[&|;(]\s*)(copy|xcopy|robocopy|move|del|erase|rd|rmdir|md|mkdir|ren|rename|cp|mv|rm|touch)\s+"
    r"([^&|;\n]{1,600})",
    re.I,
)
_NULL_TARGETS = frozenset({"nul", "nul:", "con", "$null", "/dev/null", "&1", "&2"})


def _split_args(text: str) -> list[str]:
    return [a.strip("\"'") for a in re.findall(r"\"[^\"]*\"|'[^']*'|\S+", text or "")]


def command_targets(command: str) -> "tuple[list[str], list[str]]":
    """``(writes, touches)`` a command line names: the files a redirect, a copy
    or a ``New-Item`` writes, and the paths a ``del``/``move``/``ren`` changes.
    Text only: a program the command starts can write anything (the folder
    check at the end of the turn sees that)."""
    writes: list[str] = []
    touches: list[str] = []
    text = str(command or "")[:8000]
    for m in _REDIRECT_RX.finditer(text):
        target = m.group(1).strip("\"")
        if target.lower() not in _NULL_TARGETS:
            writes.append(target)
    for m in _CMD_WRITE_RX.finditer(text):
        verb = m.group(1).lower()
        args = [a for a in _split_args(m.group(2)) if a and not a.startswith(("/", "-"))]
        if not args:
            continue
        if verb in ("copy", "xcopy", "robocopy", "cp"):
            writes.append(args[-1])
        elif verb in ("move", "mv"):
            writes.append(args[-1])
            touches.extend(args[:-1])
        elif verb in ("md", "mkdir", "touch"):
            writes.extend(args)
        else:
            touches.extend(args)
    for m in _PS_WRITE_RX.finditer(text):
        args = [a for a in _split_args(m.group(1)) if a and not a.startswith("-")]
        if args:
            writes.append(args[-1])
            touches.extend(args[:-1])
    return writes, touches


def _targets(name: str, args: dict, workspace: Path) -> "tuple[list[Path], list[Path]]":
    writes: list[Path] = []
    touches: list[Path] = []
    for keys in WRITE_ARGS.get(name, ()):
        for key in keys:
            value = args.get(key)
            if isinstance(value, str) and value.strip():
                writes.append(_resolve(workspace, value))
                break
    if name == "rename_file" and isinstance(args.get("path"), str):
        touches.append(_resolve(workspace, args["path"]))
    if name == "apply_patch":
        try:
            from ..core.auto_approve import patch_paths

            heads, _removes = patch_paths(args.get("patch"))
            writes.extend(_resolve(workspace, h) for h in heads)
        except Exception:  # noqa: BLE001 - an unreadable patch is the tool's to refuse
            pass
    if name in COMMAND_TOOLS:
        base = workspace
        cwd = args.get("cwd")
        if isinstance(cwd, str) and cwd.strip() not in ("", "."):
            base = _resolve(workspace, cwd)
        w, t = command_targets(str(args.get("command") or ""))
        writes.extend(_resolve(base, x) for x in w)
        touches.extend(_resolve(base, x) for x in t)
    return writes, touches


def _folder_list(scope: TaskScope, paths: "tuple[Path, ...]") -> str:
    return ", ".join(scope.rel(p).rstrip("\\/") + os.sep for p in paths) or "(none)"


def _scratch_words(scope: TaskScope) -> str:
    if scope.scratch is None:
        return "run the code with tool_script or run_code instead"
    return f"save it in the scratch folder {scope.scratch} (outside the task folder), run it, and read what it printed"


def refusal_for(scope: TaskScope, name: str, target: Path, *, touch: bool = False) -> str:
    """Iron Jarvis's own refusal of one write, or ""."""
    if scope.scratch is not None and _within(target, scope.scratch):
        return ""
    for folder in scope.read_only:
        if _within(target, folder):
            return (
                f"NOTHING WAS WRITTEN. {scope.rel(folder).rstrip(chr(92) + '/') + os.sep} is read-only "
                f"for this task (the user's instructions say so), and `{name}` would change "
                f"{scope.rel(target)}. "
                + (f"This task writes only in {_folder_list(scope, scope.write_roots)}. "
                   if scope.write_roots else "")
                + f"For a helper script, {_scratch_words(scope)}."
            )
    if touch or not scope.enforced:
        return ""
    if not any(_within(target, root) for root in scope.write_roots):
        return (
            f"NOTHING WAS WRITTEN. This task may write only in "
            f"{_folder_list(scope, scope.write_roots)} (task folder {scope.root}), and "
            f"{scope.rel(target)} is outside it. Write each required file there directly; "
            f"for a helper script, {_scratch_words(scope)}."
        )
    if Path(target).suffix.lower() in SCRIPT_EXTS and not scope.is_deliverable(target):
        return (
            f"NOTHING WAS WRITTEN. {_folder_list(scope, scope.write_roots)} is for the task's "
            f"required files, and {Path(target).name} is a helper script, not one of them. "
            "Write the required files directly with write_document or write_file. If you "
            f"need a script, {_scratch_words(scope)}."
        )
    return ""


def write_refusal(name: str, args: Any, workspace: Path, scope: "TaskScope | None") -> str:
    """The refusal of this call's writes under *scope*, or "". BLOCKING (it
    resolves paths); ``tools/registry.invoke`` calls it off the loop."""
    if scope is None or not isinstance(args, dict):
        return ""
    try:
        writes, touches = _targets(name, args, Path(workspace))
    except Exception:  # noqa: BLE001 - unreadable arguments: the tool's own check decides
        return ""
    for target in writes:
        words = refusal_for(scope, name, target)
        if words:
            return words
    for target in touches:
        words = refusal_for(scope, name, target, touch=True)
        if words:
            return words
    return ""
