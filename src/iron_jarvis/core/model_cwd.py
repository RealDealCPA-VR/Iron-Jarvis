"""A path a model wrote against ITS OWN idea of "this folder" (v1.465.0).

Claude Code is started in a private, empty scratch folder
(``<temp>/ironjarvis-claude-cwd``; see ``claude_native.transport.stable_workdir``),
and it tells its model that folder is the working directory. Asked to "read
october-expenses.xlsx in this folder", the model then names
``…/ironjarvis-claude-cwd/october-expenses.xlsx``: a file that cannot exist,
in a folder the user never heard of. Seen live in two of three turns: one
failed read, one refused listing, then a search for the real folder.

That scratch folder is Iron Jarvis's own and always empty, so a path inside it
can only mean "the same name in the work folder". :func:`remap` says so.
Pure text: it never touches the disk and never changes any other path.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path, PurePath

#: The scratch folder's name starts with this (the shared one is exactly it;
#: a per-process fallback adds a suffix).
SCRATCH_PREFIX = "ironjarvis-claude-cwd"


def _same(a: str, b: str) -> bool:
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


def remap(raw: "str | os.PathLike[str]", workspace: "str | os.PathLike[str]") -> str:
    """*raw* unchanged, unless it is an absolute path inside the model's
    scratch folder: then the same relative path under *workspace*."""
    text = str(raw)
    if SCRATCH_PREFIX not in text:
        return text
    try:
        path = PurePath(text)
        if not path.is_absolute():
            return text
        temp = tempfile.gettempdir()
        parts = path.parts
        for i in range(1, len(parts)):
            if parts[i].startswith(SCRATCH_PREFIX) and _same(str(PurePath(*parts[:i])), temp):
                rest = parts[i + 1:]
                if ".." in rest:
                    return text  # a path that climbs out is left exactly as written
                return str(Path(workspace, *rest)) if rest else str(Path(workspace))
    except (TypeError, ValueError):
        return text
    return text
