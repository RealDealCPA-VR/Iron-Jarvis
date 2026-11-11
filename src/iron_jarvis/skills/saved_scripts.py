"""A script that worked, saved with a skill and run again later (v1.473.0).

A tool script that read forty statements and added them up is worth keeping:
next month the same code should run, not a new script the model writes from
memory. So a skill can carry ONE script::

    <home>/skills/<slug>/SKILL.md
    <home>/skills/<slug>/scripts/main.py

``skill_create`` / ``skill_update`` write it (the user approves the card that
shows the code); ``tool_script(saved="<skill name>", params={...})`` runs it.

WHAT RUNNING IT MEANS. Exactly what running a new tool script means: the code
runs in the isolated worker (no network, no other program, no file writes)
and acts only by calling this turn's read-only tools through the app's own
gate. A saved script gets no power a fresh script lacks, which is why it may
run from a schedule or an agent run without a second approval.

ONLY THE USER'S OWN SKILLS FOLDER. A script is read from
``<home>/skills/<slug>/scripts/main.py`` and nowhere else: never from another
program's skill folder, never through a link, never past the size cap.
"""

from __future__ import annotations

import ast
import os
import re
from pathlib import Path
from typing import Any

from .loader import SKILL_FILE, slugify

SCRIPT_DIR = "scripts"
SCRIPT_FILE = "main.py"
MAX_SCRIPT_CHARS = 100_000

#: The heading the skill's instructions gain, so a model that loads the skill
#: learns how to run its script. One block, replaced in place on an update.
NOTE_HEAD = "## Saved script"


def script_path(skills_root: Any, name: str) -> Path:
    return Path(skills_root) / slugify(name) / SCRIPT_DIR / SCRIPT_FILE


def has_script(skill_dir: Any) -> bool:
    try:
        path = Path(skill_dir) / SCRIPT_DIR / SCRIPT_FILE
        return path.is_file() and not path.is_symlink()
    except OSError:
        return False


def check_code(code: Any) -> str:
    """Why *code* cannot be saved as a script, or "". Parsed, never run."""
    if not isinstance(code, str) or not code.strip():
        return "the script is empty"
    if len(code) > MAX_SCRIPT_CHARS:
        return (
            f"the script is {len(code):,} characters; the most a saved script "
            f"may hold is {MAX_SCRIPT_CHARS:,}"
        )
    if "\x00" in code:
        return "the script holds a character Python cannot read"
    try:
        ast.parse(code)
    except (SyntaxError, ValueError, RecursionError, MemoryError) as exc:
        return f"the script is not valid Python ({type(exc).__name__}: {exc})"
    return ""


#: The note's first and last words. :func:`without_run_note` removes ONLY a
#: block that starts and ends with them, so they are written once, here.
_NOTE_FIRST = "This skill has a saved script that does its reading and adding up."
_NOTE_LAST = "with `_from_script`."

#: Iron Jarvis's own note, exactly: the heading alone on its line, then the
#: note's first sentence, through its last words. Bounded, so it is read in
#: one pass.
_NOTE_RX = re.compile(
    r"(?m)^" + re.escape(NOTE_HEAD) + r"[ \t]*\r?\n" + re.escape(_NOTE_FIRST)
    + r"[\s\S]{0,1500}?" + re.escape(_NOTE_LAST) + r"[ \t]*(?:\r?\n)?"
)


def run_note(name: str) -> str:
    """The block a skill's instructions carry when it has a script."""
    return (
        f"{NOTE_HEAD}\n"
        f"{_NOTE_FIRST} "
        "Run it with the `tool_script` tool instead of writing new code:\n\n"
        f'    tool_script(saved="{name}", params={{...}})\n\n'
        "`params` is a dict the script reads (say which folder, which month). "
        "The script only reads and prints; to put its result in a file, it "
        "keeps the value with tools.keep(...) and you call the file tool "
        f"{_NOTE_LAST}"
    )


def with_run_note(instructions: str, name: str) -> str:
    """*instructions* ending in exactly one current run note."""
    text = without_run_note(instructions)
    return (text.rstrip() + "\n\n" + run_note(name)).strip() + "\n"


def without_run_note(instructions: str) -> str:
    """*instructions* without Iron Jarvis's own run note, and nothing else.

    v1.473.0 review: this used to cut the text at the first place the
    characters "## Saved script" appeared, anywhere. A skill of the user's
    own with a heading such as "### Saved script library" lost everything
    from that heading on, on ANY update (even one that changed only the
    description). Only the exact note (:data:`_NOTE_RX`) is removed now;
    whatever the user wrote, before it or after it, stays."""
    text = str(instructions or "")
    if NOTE_HEAD not in text:
        return text
    out = _NOTE_RX.sub("", text)
    if out == text:
        return text
    return (out.rstrip() + "\n") if out.strip() else ""


def write_script(skills_root: Any, name: str, code: str) -> Path:
    """Write the skill's script. Raises ``ValueError`` with a sentence when
    it cannot be saved. The skill's own SKILL.md must already exist."""
    problem = check_code(code)
    if problem:
        raise ValueError(problem)
    skill_dir = Path(skills_root) / slugify(name)
    if not (skill_dir / SKILL_FILE).is_file():
        raise ValueError(f"there is no skill named {name!r} to save a script with")
    folder = skill_dir / SCRIPT_DIR
    if skill_dir.is_symlink() or (folder.exists() and folder.is_symlink()):
        raise ValueError("that skill's folder is a link, so nothing was written through it")
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / SCRIPT_FILE
    if path.is_symlink():
        raise ValueError("that skill's script is a link, so nothing was written through it")
    tmp = folder / f".{SCRIPT_FILE}.{os.getpid()}.tmp"
    try:
        tmp.write_bytes(code.replace("\r\n", "\n").encode("utf-8"))
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass
    return path


#: `tools.call("read_document", …)` / `tools.map("read_document", …)` and
#: `tools.read_document(…)`. Bounded, so a long script is read in one pass.
_CALLED = re.compile(r"""\btools\.(?:call|map)\(\s*["']([A-Za-z_][A-Za-z0-9_:.-]{0,80})["']""")
_CALLED_AS_NAME = re.compile(r"\btools\.([a-z_][a-z0-9_]{0,80})\(")
#: The script object's own methods (not tools).
_NOT_TOOLS = frozenset({"call", "map", "keep", "plan", "names"})
#: The most tool names one script is read for.
MAX_SCRIPT_TOOLS = 12


def script_tools(home: Any, name: str) -> "list[str] | None":
    """The tools the saved script of the skill *name* calls, by name, in the
    order they first appear; ``None`` when that skill has no saved script
    (or it cannot be read). BLOCKING (a small file read).

    WHY: the skill's note says "run it with tool_script(saved=…)". A later
    chat that brings the skill must therefore be given the script tool AND
    the read-only tools the script calls, or the saved script cannot run
    (``chat_turn._resolve_armed_tools``). The names are only a wish list:
    the caller arms a name only when it is one of its own safe readers."""
    code, error = read_script(home, name)
    if error or not code:
        return None
    found: list[str] = []
    for match in _CALLED.finditer(code):
        if match.group(1) not in found:
            found.append(match.group(1))
    for match in _CALLED_AS_NAME.finditer(code):
        word = match.group(1)
        if word not in _NOT_TOOLS and word not in found:
            found.append(word)
    return found[:MAX_SCRIPT_TOOLS]


def any_saved_script(home: Any, names: Any) -> bool:
    """Does any of these skills have a saved script? BLOCKING (file checks).
    Never raises."""
    try:
        if home is None:
            return False
        root = Path(home) / "skills"
        return any(has_script(root / slugify(str(n))) for n in (names or ()) if str(n).strip())
    except Exception:  # noqa: BLE001
        return False


def read_script(home: Any, name: str) -> "tuple[str, str]":
    """``(code, error)`` for the saved script of the skill called *name*.
    BLOCKING (a small file read): call it off the event loop."""
    label = str(name or "").strip()
    if not label:
        return "", "`saved` needs the name of a skill that has a saved script"
    if home is None:
        return "", "saved scripts are not available here"
    try:
        root = Path(home) / "skills"
        skill_dir = root / slugify(label)
        path = skill_dir / SCRIPT_DIR / SCRIPT_FILE
        if not (skill_dir / SKILL_FILE).is_file():
            return "", f"there is no skill named `{label}` in your skills"
        if skill_dir.is_symlink() or (skill_dir / SCRIPT_DIR).is_symlink() or path.is_symlink():
            return "", f"the skill `{label}` is a link, so its script was not run"
        if not path.is_file():
            return "", (
                f"the skill `{label}` has no saved script. Write the script as "
                "`code`, or save one with the skill first"
            )
        if path.stat().st_size > MAX_SCRIPT_CHARS * 4:
            return "", f"the saved script of `{label}` is too large to run"
        code = path.read_bytes().decode("utf-8", "replace")
    except OSError as exc:
        return "", f"the saved script of `{label}` could not be read ({type(exc).__name__})"
    problem = check_code(code)
    if problem:
        return "", f"the saved script of `{label}` cannot run: {problem}"
    return code, ""
