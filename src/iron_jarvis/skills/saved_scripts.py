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


def run_note(name: str) -> str:
    """The block a skill's instructions carry when it has a script."""
    return (
        f"{NOTE_HEAD}\n"
        "This skill has a saved script that does its reading and adding up. "
        "Run it with the `tool_script` tool instead of writing new code:\n\n"
        f'    tool_script(saved="{name}", params={{...}})\n\n'
        "`params` is a dict the script reads (say which folder, which month). "
        "The script only reads and prints; to put its result in a file, it "
        "keeps the value with tools.keep(...) and you call the file tool "
        "with `_from_script`."
    )


def with_run_note(instructions: str, name: str) -> str:
    """*instructions* ending in exactly one current run note."""
    text = without_run_note(instructions)
    return (text.rstrip() + "\n\n" + run_note(name)).strip() + "\n"


def without_run_note(instructions: str) -> str:
    text = str(instructions or "")
    at = text.find(NOTE_HEAD)
    return text[:at].rstrip() + ("\n" if at > 0 else "") if at >= 0 else text


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
