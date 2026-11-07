"""Work with what the browser downloaded: list it, rename it, move it, copy it.

v1.444.0 (the user's request: the app "should be aware of where the file was
downloaded and have the capability of working with these files in a safe way
and can rename, modify or move them").

Two tools over :class:`~iron_jarvis.browser.downloads.DownloadLedger`, the
kept list of the browser's verified downloads:

* ``downloads_list`` (allow, READONLY) — what the browser downloaded, where
  each file is now, from which site, how big, when;
* ``download_manage`` (ASK, REVERSIBLE) — rename, move or copy ONE downloaded
  file. The safety rules, each enforced here, not left to the model:

  - it acts only on a file the kept list names (its id or its current path):
    never an arbitrary file on the disk;
  - a network share or device spelling is refused by its text before anything
    touches the disk (a path on a share is an SMB connection — the v1.379.0
    lesson);
  - a destination folder must be one this app may work in
    (``fs_policy.root_problem``: absolute, a folder here, not protected, not
    outside the user's folder limits, writable) or the turn's own work folder;
  - it NEVER overwrites: a rename to a name that exists is refused; a move or
    copy into a folder that has the name gets " (2)", " (3)" … and says so;
  - every change is undoable from the receipt and the history: a rename or
    move is put back (refused if the file was changed or the old place is
    taken again); an undone COPY is moved to Iron Jarvis's trash, never
    deleted;
  - it is ask tier (a card every time) and denied under low trust (a page the
    browser read can say anything).

To CHANGE a downloaded file's contents, ``copy`` it into the work folder (no
``to``): the ordinary file and document tools then edit the copy there, with
their own undo, and the original download stays as it came.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..browser.downloads import DownloadLedger, same_path
from .base import Reversibility, Tool, ToolContext, ToolResult
from .undo import make_file_descriptor, read_envelope

#: Characters Windows refuses in a file name, plus the separators.
_BAD_NAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)),
             *(f"lpt{i}" for i in range(1, 10))}
#: A copy larger than this is checked by size at undo, not by its hash.
_HASH_LIMIT = 256 * 1024 * 1024
#: The undo journal kind of this tool's changes.
UNDO_KIND = "download_change"


def _size(n: Any) -> str:
    if not isinstance(n, int) or n < 0:
        return "size unknown"
    for unit in ("bytes", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n} {unit}" if unit == "bytes" else f"{n:.1f} {unit}"
        n /= 1024  # type: ignore[assignment]
    return "size unknown"


def _ago(at: str) -> str:
    try:
        when = datetime.fromisoformat(str(at).replace("Z", "+00:00"))
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        secs = int((datetime.now(timezone.utc) - when).total_seconds())
    except Exception:  # noqa: BLE001
        return ""
    if secs < 90:
        return "just now"
    if secs < 5400:
        return f"{secs // 60} min ago"
    if secs < 129600:
        return f"{secs // 3600} h ago"
    return f"{secs // 86400} d ago"


def _network_or_device(raw: str) -> bool:
    try:
        from ..harness.code_editor import _network_or_device as check

        return bool(check(raw))
    except Exception:  # noqa: BLE001 — fall back to the plain spelling rule
        s = raw.replace("/", "\\")
        return s.startswith("\\\\")


def name_problem(name: str) -> str | None:
    """Why ``name`` cannot be a Windows file name, or None."""
    if not name or name in (".", ".."):
        return "the new name is empty"
    if _BAD_NAME_CHARS.search(name):
        return 'a file name cannot hold < > : " / \\ | ? * (give the name only, not a folder)'
    if name.endswith((" ", ".")):
        return "a file name cannot end with a space or a dot"
    if name.split(".")[0].lower() in _RESERVED:
        return f"Windows reserves the name {name.split('.')[0].upper()}"
    if len(name) > 200:
        return "the new name is longer than 200 characters"
    return None


def free_name(folder: Path, name: str) -> Path:
    """``folder/name``, or ``name (2).ext``, ``(3)`` … — the first that is free."""
    target = folder / name
    if not target.exists():
        return target
    stem, ext = Path(name).stem, Path(name).suffix
    for n in range(2, 1000):
        candidate = folder / f"{stem} ({n}){ext}"
        if not candidate.exists():
            return candidate
    raise FileExistsError(f"too many files named like {name} in {folder}")


def _digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _fingerprint(path: Path) -> dict[str, Any]:
    st = path.stat()
    out: dict[str, Any] = {"size": st.st_size}
    if st.st_size <= _HASH_LIMIT:
        out["sha256"] = _digest(path)
    return out


def _same_file_as(path: Path, print_: dict[str, Any]) -> bool:
    try:
        if not path.is_file() or path.stat().st_size != print_.get("size"):
            return False
        want = print_.get("sha256")
        return want is None or _digest(path) == want
    except OSError:
        return False


class DownloadsListTool(Tool):
    """The browser's downloads, newest first (read-only)."""

    name = "downloads_list"
    description = (
        "List the files YOUR BROWSER downloaded (through the Iron Jarvis browser "
        "add-on), newest first: an id, the file name, where it is now, the site it "
        "came from, its size and when. Use it to find 'the file I just downloaded'. "
        "Give `query` to filter by name or site. To rename, move or copy one of them "
        "use download_manage with its id; to read one, give its path to read_file "
        "or read_document."
    )
    reversibility = Reversibility.READONLY
    input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "words in the name or site"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50},
        },
    }

    def __init__(self, ledger: DownloadLedger) -> None:
        self.ledger = ledger

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        try:
            limit = max(1, min(50, int(args.get("limit") or 10)))
        except (TypeError, ValueError):
            limit = 10
        words = [w for w in str(args.get("query") or "").lower().split() if w]
        rows = await asyncio.to_thread(self.ledger.entries)
        if words:
            rows = [
                r for r in rows
                if all(w in f"{r.get('name', '')} {r.get('site', '')}".lower() for w in words)
            ]
        if not rows:
            return ToolResult(
                ok=True,
                output=(
                    "No browser downloads match." if words else
                    "Iron Jarvis has not seen the browser download anything yet. "
                    "(Downloads are noticed through the Iron Jarvis browser add-on, "
                    "from the moment it is paired.)"
                ),
                data={"downloads": []},
            )
        shown = rows[:limit]
        lines = []
        for r in shown:
            bits = [r["id"], r.get("name", ""), _size(r.get("bytes"))]
            if r.get("site"):
                bits.append(f"from {r['site']}")
            ago = _ago(r.get("at", ""))
            if ago:
                bits.append(ago)
            line = " · ".join(b for b in bits if b) + f"\n  {r['path']}"
            if not r.get("exists"):
                line += "  (no longer there: moved or deleted outside Iron Jarvis)"
            lines.append(line)
        more = len(rows) - len(shown)
        if more > 0:
            lines.append(f"({more} more not shown; give a larger limit or a query)")
        keep = ("id", "name", "path", "site", "mime", "bytes", "at", "exists")
        return ToolResult(
            ok=True,
            output="\n".join(lines),
            data={"downloads": [{k: r.get(k) for k in keep} for r in shown]},
        )


class DownloadManageTool(Tool):
    """Rename, move or copy ONE file the browser downloaded. Asks first."""

    name = "download_manage"
    description = (
        "Rename, move or copy ONE file your browser downloaded (find it with "
        "downloads_list, or use the path a browser click reported). `download`: its "
        "id (dl_3) or its current path. `action`: rename | move | copy. `to`: for "
        "rename, the new file name only; for move or copy, the folder it should go "
        "to. Copy with no `to` puts a copy in this chat's work folder, where the "
        "file tools can change its contents while the download stays as it came. "
        "Never overwrites: a name already taken gets ' (2)'. Reversible."
    )
    reversibility = Reversibility.REVERSIBLE
    input_schema = {
        "type": "object",
        "properties": {
            "download": {"type": "string"},
            "action": {"type": "string", "enum": ["rename", "move", "copy"]},
            "to": {"type": "string"},
        },
        "required": ["download", "action"],
    }

    def __init__(self, ledger: DownloadLedger) -> None:
        self.ledger = ledger

    # -- planning (BLOCKING; one rule for capture_undo and execute) ----------
    def _plan(self, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
        """``{entry, src, dst, action, numbered}`` or ``{error}``."""
        ref = str(args.get("download") or "").strip()
        action = str(args.get("action") or "").strip().lower()
        to = str(args.get("to") or "").strip().strip('"')
        if action not in ("rename", "move", "copy"):
            return {"error": "action must be rename, move or copy"}
        if _network_or_device(ref) or (to and _network_or_device(to)):
            return {"error": "Iron Jarvis does not work with files on a network share or device path here"}
        entry = self.ledger.find(ref)
        if entry is None:
            return {"error": (
                f"{ref!r} is not a file the browser downloaded. download_manage only works "
                "on downloads Iron Jarvis saw (see downloads_list); for files in the work "
                "folder use rename_file."
            )}
        src = Path(entry["path"])
        if not src.is_file():
            return {"error": f"{entry.get('name')} is no longer at {src} (moved or deleted outside Iron Jarvis)"}
        from ..core.fs_policy import fs_read_ok, root_problem

        ok, why = fs_read_ok(src)
        if not ok:
            return {"error": why}
        if action == "rename":
            if not to:
                return {"error": "give the new file name in `to`"}
            if os.path.dirname(to):
                return {"error": "for rename give the new NAME only; to change the folder use move"}
            problem = name_problem(to)
            if problem:
                return {"error": problem}
            dst = src.with_name(to)
            if str(dst) == str(src):
                return {"error": f"the new name is the same as the old one: {src.name}"}
            if dst.exists() and not same_path(str(dst), str(src)):
                return {"error": f"{to} already exists in {src.parent}; pick another name"}
            folder_problem = root_problem(src.parent)
            if folder_problem:
                return {"error": folder_problem}
            return {"entry": entry, "src": src, "dst": dst, "action": action, "numbered": False}
        if to:
            folder = Path(to)
            if not folder.is_absolute():
                folder = Path(ctx.workspace) / folder
        elif action == "copy":
            folder = Path(ctx.workspace)
        else:
            return {"error": "give the folder to move it to in `to`"}
        if action == "copy" and not to:
            folder.mkdir(parents=True, exist_ok=True)
        if not folder.is_dir():
            return {"error": f"{folder} is not a folder on this PC"}
        in_work = same_path(str(folder), str(ctx.workspace)) or _within(folder, Path(ctx.workspace))
        if not in_work:
            problem = root_problem(folder)
            if problem:
                return {"error": problem}
        if same_path(str(folder), str(src.parent)) and action == "move":
            return {"error": f"{src.name} is already in {folder}"}
        dst = free_name(folder, src.name)
        return {"entry": entry, "src": src, "dst": dst, "action": action,
                "numbered": dst.name != src.name}

    async def capture_undo(self, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any] | None:
        plan = await asyncio.to_thread(self._plan, args, ctx)
        if "error" in plan:
            return None
        src: Path = plan["src"]
        try:
            print_ = await asyncio.to_thread(_fingerprint, src)
        except OSError:
            return None
        descriptor = make_file_descriptor(
            ctx.config.home, kind=UNDO_KIND, path=str(plan["dst"]), mode="raw"
        )
        import json as _json

        meta = _json.loads(descriptor["pre_inline"])
        meta.update({
            "action": plan["action"],
            "from": str(src),
            "to": str(plan["dst"]),
            "entry": plan["entry"]["id"],
            "print": print_,
        })
        descriptor["pre_inline"] = _json.dumps(meta)
        return descriptor

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        plan = await asyncio.to_thread(self._plan, args, ctx)
        if "error" in plan:
            return ToolResult(ok=False, error=plan["error"])
        src: Path = plan["src"]
        dst: Path = plan["dst"]
        action = plan["action"]
        try:
            if action == "copy":
                await asyncio.to_thread(shutil.copy2, src, dst)
            else:
                await asyncio.to_thread(shutil.move, str(src), str(dst))
                await asyncio.to_thread(self.ledger.moved, plan["entry"]["id"], str(dst))
        except OSError as exc:
            return ToolResult(ok=False, error=f"could not {action} {src.name}: {exc}")
        verb = {"rename": "Renamed", "move": "Moved", "copy": "Copied"}[action]
        note = ""
        if plan["numbered"]:
            note = f" ({src.name} was already taken there, so it is named {dst.name})"
        ext_note = ""
        if action == "rename" and src.suffix.lower() != dst.suffix.lower():
            ext_note = (
                f" The extension changed from {src.suffix or '(none)'} to "
                f"{dst.suffix or '(none)'}; the contents were not converted."
            )
        work_note = ""
        if action == "copy" and same_path(str(dst.parent), str(ctx.workspace)):
            work_note = " The copy is in this chat's work folder; edit it there."
        return ToolResult(
            ok=True,
            output=f"{verb} {src.name} to {dst}{note}.{ext_note}{work_note} Reversible from the receipt.",
            data={"action": action, "from": str(src), "abs_path": str(dst),
                  "download": plan["entry"]["id"]},
        )

    async def revert(self, undo: dict[str, Any], ctx: ToolContext) -> ToolResult:
        meta = read_envelope(undo)
        action, src, dst = meta.get("action"), meta.get("from"), meta.get("to")
        if action not in ("rename", "move", "copy") or not src or not dst:
            return ToolResult(ok=False, error="undo: this change was not recorded fully")
        src_p, dst_p = Path(src), Path(dst)
        print_ = meta.get("print") or {}

        def _do() -> ToolResult:
            if not _same_file_as(dst_p, print_):
                return ToolResult(ok=False, error=(
                    f"undo: {dst_p.name} was changed or moved since, so Iron Jarvis "
                    "left it where it is"
                ))
            if action == "copy":
                stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
                trash = Path(ctx.config.home) / "trash" / stamp / "downloads"
                trash.mkdir(parents=True, exist_ok=True)
                shutil.move(str(dst_p), str(free_name(trash, dst_p.name)))
                return ToolResult(ok=True, output=f"undo: moved the copy {dst_p.name} to Iron Jarvis's trash")
            if src_p.exists():
                return ToolResult(ok=False, error=(
                    f"undo: {src_p.name} exists again in {src_p.parent}; putting the "
                    "file back would overwrite it"
                ))
            shutil.move(str(dst_p), str(src_p))
            entry = meta.get("entry")
            if entry:
                self.ledger.moved(str(entry), str(src_p))
            return ToolResult(ok=True, output=f"undo: put {src_p.name} back in {src_p.parent}")

        try:
            return await asyncio.to_thread(_do)
        except OSError as exc:
            return ToolResult(ok=False, error=f"undo: could not put it back: {exc}")


def _within(target: Path, root: Path) -> bool:
    try:
        target.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def download_tools(ledger: DownloadLedger) -> list[Tool]:
    return [DownloadsListTool(ledger), DownloadManageTool(ledger)]
