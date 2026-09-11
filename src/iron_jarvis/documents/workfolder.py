"""A conversation's own folder, for chat with no project (v1.244.0).

THE REPORT: "when I attach documents in the chat module and ask for a task to
be completed, I often get a lagging delay, a request for information and then a
completed screen with absolutely no output. Projects are great, but sometimes I
just need to attach a document and ask for some work to be completed."

Replayed on the live model: a chat with no project has no folder of its own and
no file tools. Its tool workspace is the hidden ``<home>/uploads``, so the model
could not create the workbook it was asked for, handed the job to an agent,
and that agent ran in a throwaway ``workspaces/<session>`` folder under AppData —
where it built the right workbook, stopped twice to ask to run Python, ran out
of steps, and reported "Task failed" beside a file nobody could find. Inside a
project none of that happens, because selecting a project gives the chat a
REAL folder and the file essentials.

So a no-project conversation that is handed a file now gets the same two
things: a dated folder under the user's Documents (``Documents\\Iron Jarvis``
by default — ``config.chat_files_root``), with the attached files copied in so
the conversation's inputs and outputs sit side by side where the user will
look. The page binds it as the chat's working folder, so everything the
project path already does — the folder grounding block, the file tools, a
hand-off that carries the folder — applies unchanged.

Pure filesystem logic, synchronous and blocking by design: call it from a sync
route (FastAPI runs those on the threadpool) or through ``asyncio.to_thread``.
"""

from __future__ import annotations

import re
import shutil
from datetime import date
from pathlib import Path
from typing import Any

#: Windows refuses these as a whole file or folder name, whatever the extension.
_RESERVED = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}

#: Longest folder title kept — long enough to recognise, short enough that the
#: whole path stays far from MAX_PATH once the files inside are named.
_TITLE_MAX = 60


def folder_title(raw: str) -> str:
    """A readable, Windows-safe folder title from a file name or a sentence.

    ``"HarborPoint_Q1_Expenses.pdf"`` → ``"HarborPoint Q1 Expenses"``. Never
    empty: a title that sanitises to nothing becomes ``"Chat files"``.
    """
    text = (raw or "").strip()
    stem = Path(text).stem if re.search(r"\.[A-Za-z0-9]{1,5}$", text) else text
    stem = stem.replace("_", " ")
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", stem)
    stem = re.sub(r"\s+", " ", stem).strip(" .")
    if len(stem) > _TITLE_MAX:
        stem = stem[:_TITLE_MAX].rstrip(" .")
    if not stem or stem.lower() in _RESERVED:
        return "Chat files"
    return stem


def _unique(path: Path) -> Path:
    """``path`` if free, else ``name (2)``, ``name (3)``… beside it."""
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    for n in range(2, 1000):
        candidate = path.with_name(f"{stem} ({n}){suffix}")
        if not candidate.exists():
            return candidate
    raise OSError(f"no free name for {path.name} in {path.parent}")


def _inside(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except (ValueError, OSError):
        return False


def copy_uploads_into(
    folder: Path, files: list[str], uploads_dir: Path
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Copy each file that is one of the app's own uploads into ``folder``.

    Only files under ``uploads_dir`` are copied: this is the attach path, and a
    route that copied ANY path it was handed into a folder of the caller's
    choosing would be a general file-copy primitive nobody asked for. Anything
    else is reported in ``skipped`` with the reason, never silently dropped.
    Returns ``(copied, skipped)``; each copied entry carries the original path
    as ``source`` so the page can swap a chip's path for its new home.
    """
    copied: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    for raw in files or []:
        src = Path(str(raw or "").strip())
        if not str(src) or not src.is_absolute():
            skipped.append({"source": str(raw), "reason": "not an absolute path"})
            continue
        if not _inside(src, uploads_dir):
            skipped.append({"source": str(src), "reason": "not an attached upload"})
            continue
        if not src.is_file():
            skipped.append({"source": str(src), "reason": "file no longer exists"})
            continue
        target = _unique(folder / src.name)
        shutil.copy2(src, target)
        copied.append(
            {
                "name": target.name,
                "path": str(target),
                "bytes": target.stat().st_size,
                "source": str(src),
            }
        )
    return copied, skipped


def make_chat_workfolder(
    root: Path,
    title: str,
    files: list[str],
    uploads_dir: Path,
    *,
    today: date | None = None,
) -> dict[str, Any]:
    """Create ``<root>/<YYYY-MM-DD> <title>`` (unique) and copy the uploads in.

    Raises ``OSError`` when the folder cannot be created — the caller turns
    that into an honest refusal naming the folder.
    """
    stamp = (today or date.today()).isoformat()
    root.mkdir(parents=True, exist_ok=True)
    folder = _unique(root / f"{stamp} {folder_title(title)}")
    folder.mkdir(parents=False, exist_ok=False)
    copied, skipped = copy_uploads_into(folder, files, uploads_dir)
    return {"path": str(folder), "created": True, "files": copied, "skipped": skipped}
