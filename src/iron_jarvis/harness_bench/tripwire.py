"""The tripwire: a run never starts while the grader is within the agent's reach.

Harness² principle 4 — measurement is separate from the method. The thing that
runs the task never sees the grader: hidden check files live outside the copy
the agent works in, and they are applied only in a separate grading copy after
the run (:mod:`.grading`). This module is the guard that makes that a fact
rather than an intention: before any runner starts, :func:`assert_context_clean`
looks at everything the agent is given — the workspace folder AND the prompt —
and refuses when any of it carries hidden material, recognised four ways:

* a file with the NAME of a hidden file (``task.toml``, ``test_hidden_*.py``);
* a file whose content IS a hidden file (same normalized bytes);
* a file or prompt containing :data:`catalog.HIDDEN_CANARY`;
* a file or prompt containing a hidden file's whole text (pasted in).

Read-only: it opens files, never changes one.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from .catalog import HIDDEN_CANARY, Task, normalized_bytes

#: Files bigger than this are not opened for the content checks (a task repo
#: is tiny; an agent-made blob is not grader material).
_MAX_SCAN_BYTES = 2_000_000
#: A hidden file's text must be at least this long to count as "pasted in" —
#: shorter fragments are too common to mean anything.
_MIN_PASTE_CHARS = 40


class HiddenCheckLeak(RuntimeError):
    """The agent's context holds grader material; the run must not start."""


def _signature(task: Task) -> tuple[set[str], set[str], list[str]]:
    names: set[str] = set()
    digests: set[str] = set()
    texts: list[str] = []
    for path in task.hidden_files():
        names.add(path.name.lower())
        data = normalized_bytes(path)
        digests.add(hashlib.sha256(data).hexdigest())
        text = data.decode("utf-8", errors="replace").strip()
        if len(text) >= _MIN_PASTE_CHARS:
            texts.append(text)
    return names, digests, texts


def leaks(task: Task, workspace: Path, prompt: str) -> list[str]:
    """Every way the agent's context carries hidden material (empty = clean)."""
    names, digests, texts = _signature(task)
    found: list[str] = []
    if HIDDEN_CANARY in prompt:
        found.append("the prompt carries the hidden-check marker")
    for text in texts:
        if text in prompt:
            found.append("the prompt contains a hidden check file")
            break
    root = Path(workspace)
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if path.name.lower() in names:
            found.append(f"{rel} has the name of a hidden check file")
            continue
        try:
            if path.stat().st_size > _MAX_SCAN_BYTES:
                continue
            data = normalized_bytes(path)
        except OSError:
            continue
        if hashlib.sha256(data).hexdigest() in digests:
            found.append(f"{rel} is a copy of a hidden check file")
            continue
        text = data.decode("utf-8", errors="replace")
        if HIDDEN_CANARY in text:
            found.append(f"{rel} carries the hidden-check marker")
            continue
        if any(t in text for t in texts):
            found.append(f"{rel} contains a hidden check file")
    return found


def assert_context_clean(task: Task, workspace: Path, prompt: str) -> None:
    """Raise :class:`HiddenCheckLeak` naming every leak; return when clean."""
    found = leaks(task, workspace, prompt)
    if found:
        raise HiddenCheckLeak(
            f"refused to start {task.id}: the agent would see the grader ("
            + "; ".join(found)
            + ")"
        )
