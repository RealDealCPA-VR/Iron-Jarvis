"""What a Build › Code reply that FAILED leaves behind (readiness 0j.5 item 17).

A coding tool that edits files and then fails (a model error, a crash, its
account at a limit) keeps every edit it made: nothing is rolled back. The
reply's failure line said only what went wrong, so the user could not tell
whether the files had changed. :func:`kept_notice` reads the reply's OWN
finished edit/write steps (the tool's own record of what it wrote, in the
turn's coalesced events) and says, in one notice before the ``done``, which
files are kept. Names only (``Path.name``); a failed step's file is never
counted. Pure and bounded: no file is read.
"""

from __future__ import annotations

from pathlib import PurePath
from typing import Any, Iterable

#: Steps whose ``path`` names a file the tool WROTE.
WRITE_KINDS = frozenset({"edit", "write"})
#: How many names the notice says before "and N more".
NAMES_MAX = 5


def written_paths(events: Iterable[dict[str, Any]]) -> list[str]:
    """The paths of the reply's FINISHED edit/write steps, first seen first,
    each once. A running, failed or stopped step does not count."""
    out: list[str] = []
    for ev in events or ():
        if not isinstance(ev, dict) or ev.get("type") != "tool":
            continue
        if ev.get("kind") not in WRITE_KINDS or ev.get("status") != "done":
            continue
        path = str(ev.get("path") or "").strip()
        if path and path not in out:
            out.append(path)
    return out


def kept_words(paths: list[str]) -> str:
    """"The file it changed is kept: app.py." / "The files it changed are
    kept: a.py, b.py (and 3 more)." — ``""`` for none."""
    names: list[str] = []
    for p in paths:
        name = PurePath(str(p).replace("\\", "/")).name.strip()
        if name and name not in names:
            names.append(name)
    if not names:
        return ""
    shown = ", ".join(names[:NAMES_MAX])
    more = len(names) - NAMES_MAX
    tail = f" (and {more} more)" if more > 0 else ""
    lead = "The file it changed is kept" if len(names) == 1 else "The files it changed are kept"
    return f"{lead}: {shown}{tail}."


def kept_notice(events: Iterable[dict[str, Any]]) -> str:
    """The notice a failed reply carries, or ``""``. Never raises."""
    try:
        return kept_words(written_paths(events))
    except Exception:  # noqa: BLE001 — wording never fails a reply's end
        return ""
