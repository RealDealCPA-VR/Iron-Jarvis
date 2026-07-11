"""TX-01 undo ENGINE — the pre-image blob store + revert-safety primitives.

The registry (``tools/registry.py``) journals a reversible tool's INVERSE in
``UndoJournal`` BEFORE the mutation runs. This module owns everything that
inverse needs to be stored and, later, safely replayed by ``POST /undo``:

* a blob store for pre-images under ``<home>/undo/`` so a large snapshot never
  bloats the SQLite row (``save_preimage`` / ``load_preimage`` / ``delete_preimage``),
* small pre-images (< :data:`INLINE_MAX_BYTES`) ride INLINE in the journal row
  instead of a blob file,
* sha256 helpers, and
* :func:`guard_unchanged` — re-hash the CURRENT target and REFUSE the revert on a
  mismatch versus the hash recorded right after the mutation (the target changed
  since, so undoing now would silently clobber a newer edit).

The ``UndoJournal`` table has a FIXED column set (kind / reversible / pre_ref /
pre_inline / pre_sha256 / post_sha256) and the registry copies only those, so any
extra per-action metadata a tool needs at revert time (the target path, whether
the bytes are text or raw) is packed into a small JSON envelope carried in
``pre_inline`` — see :func:`make_file_descriptor` / :func:`read_envelope`.
"""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Any

from ..core.ids import new_id
from .base import ToolContext, ToolResult, safe_path

#: Pre-images at or below this size ride INLINE (base64 inside the journal's
#: pre_inline envelope) instead of a blob file — keeps tiny edits off the disk
#: store. 8 KB covers the overwhelming majority of source-file/note edits.
INLINE_MAX_BYTES = 8192


# --- hashing ---------------------------------------------------------------

def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_target(path: str | Path, mode: str = "raw") -> str | None:
    """Hash the CURRENT bytes of ``path``, or ``None`` when it does not exist.

    ``mode="text"`` hashes the file's DECODED text re-encoded as UTF-8, which is
    newline-representation invariant — a note written with ``\\n`` and read back as
    ``\\r\\n`` on Windows hashes identically. ``mode="raw"`` hashes the bytes
    verbatim (for binary documents)."""
    p = Path(path)
    if mode == "text":
        try:
            return sha256_bytes(p.read_text(encoding="utf-8").encode("utf-8"))
        except (FileNotFoundError, IsADirectoryError, OSError, UnicodeDecodeError):
            return None
    try:
        return sha256_bytes(p.read_bytes())
    except (FileNotFoundError, IsADirectoryError, OSError):
        return None


# --- blob store ------------------------------------------------------------

def undo_dir(home: str | Path) -> Path:
    """The pre-image blob directory (``<home>/undo/``), created on demand.

    The platform registers this as a PROTECTED fs root so an agent file tool can
    never read a stored pre-image (a snapshot can contain prior file content)."""
    d = Path(home) / "undo"
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_preimage(home: str | Path, action_id: str, data: bytes) -> str:
    """Persist a pre-image blob; return an opaque ref (a filename under undo/).

    ``action_id`` only namespaces the file — the ref is content-addressed by a
    short sha so two captures of the same action never collide."""
    ref = f"{action_id}-{sha256_bytes(data)[:16]}.bin"
    (undo_dir(home) / ref).write_bytes(data)
    return ref


def load_preimage(home: str | Path, ref: str) -> bytes:
    return (undo_dir(home) / ref).read_bytes()


def delete_preimage(home: str | Path, ref: str | None) -> bool:
    """Remove a consumed pre-image blob (no-op for ``None``/inline). Never raises."""
    if not ref:
        return False
    try:
        (undo_dir(home) / ref).unlink()
        return True
    except (FileNotFoundError, OSError):
        return False


# --- descriptors (what capture_undo returns / revert consumes) -------------

def make_file_descriptor(
    home: str | Path,
    *,
    kind: str,
    path: str,
    mode: str,
    prior_bytes: bytes | None = None,
    pre_sha256: str | None = None,
    post_sha256: str | None = None,
) -> dict[str, Any]:
    """Build the journal descriptor for a file mutation.

    The target ``path`` and byte ``mode`` (which the fixed journal columns can't
    hold) plus any small inline pre-image are packed into a JSON envelope stored
    in ``pre_inline``; a large pre-image spills to a blob referenced by
    ``pre_ref``. ``kind`` is ``file_restore`` (overwrote existing → restore prior
    bytes) or ``file_delete`` (created new → unlink on undo)."""
    meta: dict[str, Any] = {"path": path, "mode": mode, "data": None}
    pre_ref: str | None = None
    if prior_bytes is not None:
        if len(prior_bytes) <= INLINE_MAX_BYTES:
            meta["data"] = base64.b64encode(prior_bytes).decode("ascii")
        else:
            pre_ref = save_preimage(home, new_id("undo"), prior_bytes)
    return {
        "kind": kind,
        "reversible": True,
        "pre_ref": pre_ref,
        "pre_inline": json.dumps(meta),
        "pre_sha256": pre_sha256,
        "post_sha256": post_sha256,
    }


def read_envelope(undo: dict[str, Any]) -> dict[str, Any]:
    """Unpack the JSON envelope stored in ``pre_inline`` (path / mode / inline data)."""
    try:
        return json.loads(undo.get("pre_inline") or "{}")
    except (TypeError, ValueError):
        return {}


def resolve_prior_bytes(
    home: str | Path, undo: dict[str, Any], meta: dict[str, Any]
) -> bytes | None:
    """Recover the pre-image bytes from the inline envelope or the blob ref."""
    inline = meta.get("data")
    if inline is not None:
        return base64.b64decode(inline)
    ref = undo.get("pre_ref")
    if ref:
        return load_preimage(home, ref)
    return None


# --- revert-safety + shared file revert ------------------------------------

def guard_unchanged(current_sha: str | None, post_sha: str | None) -> str | None:
    """Return a conflict reason when the target changed since the mutation, else
    ``None``. When no post-hash was captured (``post_sha`` is ``None``) the check
    is skipped (best-effort — e.g. a binary document whose output we can't predict
    before it is written)."""
    if post_sha is not None and current_sha != post_sha:
        return (
            "target changed since the action — refusing to undo "
            "(it would overwrite a newer change)"
        )
    return None


def finalize_post_hash(undo: "dict[str, Any] | None", ctx: ToolContext) -> None:
    """Fill ``post_sha256`` from the ACTUAL written file when the capture could not
    predict it (raw/binary writes: ``write_document``, a non-UTF-8 ``write_file``).

    Runs AFTER a successful mutation, so re-hashing the target yields exactly the
    bytes just written. Without it a raw write journals ``post_sha256=None`` and
    :func:`guard_unchanged` SKIPS the drift check — so a later undo would silently
    clobber (or delete) a file that changed since the action. Best-effort: never
    raises, never overwrites an already-predicted hash, and leaves non-workspace
    targets unguarded (they cannot be reverted through :func:`revert_workspace_file`
    anyway)."""
    if not undo or undo.get("post_sha256") is not None:
        return
    meta = read_envelope(undo)
    rel = meta.get("path")
    if not rel:
        return
    mode = meta.get("mode", "raw")
    try:
        target = safe_path(ctx.workspace, rel)
    except Exception:  # noqa: BLE001 — a non-workspace target just stays unguarded
        return
    undo["post_sha256"] = sha256_target(target, mode)


class RevertConflict(Exception):
    """Raised when a revert would clobber a change made since the action."""


async def revert_workspace_file(
    undo: dict[str, Any], ctx: ToolContext
) -> ToolResult:
    """Shared inverse for the workspace file writers (write_file / edit_file /
    write_document).

    Re-hashes the CURRENT target and refuses on drift, then either restores the
    prior bytes (``file_restore``) or unlinks the path we created
    (``file_delete``). Resolution goes through :func:`safe_path`, so the revert
    obeys the SAME workspace-only fs policy as the forward write."""
    meta = read_envelope(undo)
    rel = meta.get("path")
    mode = meta.get("mode", "raw")
    kind = undo.get("kind")
    home = ctx.config.home
    if not rel:
        return ToolResult(ok=False, error="undo: no target path recorded")
    try:
        target = safe_path(ctx.workspace, rel)
    except Exception as exc:  # path escaped the workspace — never write outside it
        return ToolResult(ok=False, error=f"undo: unsafe path: {exc}")

    conflict = guard_unchanged(sha256_target(target, mode), undo.get("post_sha256"))
    if conflict is not None:
        raise RevertConflict(conflict)

    if kind == "file_delete":
        try:
            if target.exists():
                target.unlink()
        except OSError as exc:
            return ToolResult(ok=False, error=f"undo: could not remove {rel}: {exc}")
        delete_preimage(home, undo.get("pre_ref"))
        return ToolResult(ok=True, output=f"undo: removed created file {rel}")

    if kind == "file_restore":
        prior = resolve_prior_bytes(home, undo, meta)
        if prior is None:
            return ToolResult(ok=False, error="undo: pre-image unavailable")
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            if mode == "text":
                target.write_text(prior.decode("utf-8"), encoding="utf-8")
            else:
                target.write_bytes(prior)
        except OSError as exc:
            return ToolResult(ok=False, error=f"undo: could not restore {rel}: {exc}")
        delete_preimage(home, undo.get("pre_ref"))
        return ToolResult(ok=True, output=f"undo: restored prior content of {rel}")

    return ToolResult(ok=False, error=f"undo: unknown file undo kind {kind!r}")
