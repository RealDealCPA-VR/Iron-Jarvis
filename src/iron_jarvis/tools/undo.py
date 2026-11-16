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

import asyncio
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
        # Streamed (v1.499.3): the same digest as hashing the whole bytes,
        # without holding a large created file in memory.
        digest = hashlib.sha256()
        with p.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                digest.update(chunk)
        return digest.hexdigest()
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
    exact: bool = False,
) -> dict[str, Any]:
    """Build the journal descriptor for a file mutation.

    The target ``path`` and byte ``mode`` (which the fixed journal columns can't
    hold) plus any small inline pre-image are packed into a JSON envelope stored
    in ``pre_inline``; a large pre-image spills to a blob referenced by
    ``pre_ref``. ``kind`` is ``file_restore`` (overwrote existing → restore prior
    bytes) or ``file_delete`` (created new → unlink on undo)."""
    meta: dict[str, Any] = {"path": path, "mode": mode, "data": None}
    if exact:
        # v1.370.0: ``prior_bytes`` are the file's exact bytes — the revert
        # writes them back as they are, even in ``text`` mode (whose HASH
        # stays newline-invariant). Rows without the flag keep the old write.
        meta["exact"] = True
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


def capture_file_inverse(
    home: str | Path, workspace: str | Path, target: Path
) -> "dict[str, Any] | None":
    """The inverse of writing *target* (a workspace file) whose new bytes are
    not known in advance (v1.504.0, 4.5: ``convert_document``,
    ``image_convert``, ``image_resize``): its prior RAW bytes when it exists
    (``file_restore``), else a delete of the file it will create
    (``file_delete``). The after-hash is filled in once the write has landed
    (``finalize_post_hash``). None when *target* is not inside *workspace* or
    is not a file. BLOCKING."""
    try:
        rel = str(Path(target).resolve().relative_to(Path(workspace).resolve())).replace("\\", "/")
    except (ValueError, OSError):
        return None
    if target.is_file():
        try:
            prior = target.read_bytes()
        except OSError:
            return None
        return make_file_descriptor(
            home, kind="file_restore", path=rel, mode="raw",
            prior_bytes=prior, pre_sha256=sha256_bytes(prior),
        )
    if target.exists():
        return None
    return make_file_descriptor(home, kind="file_delete", path=rel, mode="raw")


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


# --- the multi-file patch envelope (v1.364.0, IJ harness apply_patch) ------

#: ONE journal row (its key is the invocation id) for an ``apply_patch`` call,
#: holding one entry PER FILE the patch touched: ``{"path": <workspace-
#: relative>, "existed": bool, "data": <base64 before-image> | None, "ref":
#: <blob ref> | None, "post_sha256": <hash of what the patch left> | None}``
#: (``post_sha256`` None = the patch left no file there). The envelope also
#: lists ``created`` / ``changed`` / ``deleted`` for the readers
#: (``agents/outcome``, the chat's changes view).
PATCH_UNDO_KIND = "files_patch"


def patch_entries(meta: dict[str, Any]) -> list[dict[str, Any]]:
    """The per-file entries of a ``files_patch`` envelope (``[]`` when none)."""
    files = meta.get("files") if isinstance(meta, dict) else None
    return [e for e in files if isinstance(e, dict) and e.get("path")] if isinstance(files, list) else []


def patch_lists(meta: dict[str, Any]) -> tuple[list[str], list[str], list[str]]:
    """``(created, changed, deleted)`` workspace-relative paths of a
    ``files_patch`` envelope."""

    def _strs(key: str) -> list[str]:
        value = meta.get(key) if isinstance(meta, dict) else None
        return [str(p) for p in value if isinstance(p, str) and p] if isinstance(value, list) else []

    return _strs("created"), _strs("changed"), _strs("deleted")


def patch_entry_before(home: str | Path, entry: dict[str, Any]) -> bytes | None:
    """One ``files_patch`` entry's before-image (``None`` = it did not exist)."""
    if entry.get("data") is not None:
        return base64.b64decode(entry["data"])
    if entry.get("ref"):
        return load_preimage(home, entry["ref"])
    return None


# --- an inverse whose call did not succeed (v1.504.0, north-star 4.1) -------

#: The kinds whose target is ONE file a hash can check, and how its path is
#: recorded: workspace-relative (the file tools) or absolute (a memory note).
#: A call of one of these that failed or was cut off keeps its inverse unless
#: the hash PROVES the file is as it was. Every other kind (a setting, a
#: record, a multi-file patch) is dropped on failure, as before this version.
ONE_FILE_KINDS: dict[str, str] = {
    "file_restore": "workspace",
    "file_delete": "workspace",
    "memory_restore": "absolute",
    "memory_delete_file": "absolute",
}
#: The envelope flag of an inverse kept for a call cut off while it may still
#: have been writing (Stop, the per-call deadline): its after-hash is not
#: known, so its revert never trusts one (:func:`revert_workspace_file`) and
#: the next boot settles it (``registry.reconcile_interrupted_tool_calls``).
INTERRUPTED_KEY = "interrupted"


def drop_preimages(home: str | Path, undo: "dict[str, Any] | None") -> None:
    """Remove every before-copy blob *undo* refers to (its ``pre_ref`` and a
    ``files_patch`` envelope's per-file refs). For an inverse no row will
    hold. Never raises."""
    if not undo:
        return
    try:
        delete_preimage(home, undo.get("pre_ref"))
        if undo.get("kind") == PATCH_UNDO_KIND:
            for entry in patch_entries(read_envelope(undo)):
                delete_preimage(home, entry.get("ref"))
    except Exception:  # noqa: BLE001 — reclaiming is best-effort
        pass


def _with_meta(undo: dict[str, Any], **changes: Any) -> dict[str, Any]:
    """*undo* with its envelope's keys changed (``None`` removes a key)."""
    meta = read_envelope(undo)
    for key, value in changes.items():
        if value is None:
            meta.pop(key, None)
        else:
            meta[key] = value
    return {**undo, "pre_inline": json.dumps(meta)}


def _one_file_target(undo: dict[str, Any], workspace: "str | Path | None") -> "Path | None":
    """The ONE file an inverse of :data:`ONE_FILE_KINDS` is about, or None.
    A workspace path is resolved the way the revert resolves it
    (:func:`safe_path` against the capture-time workspace)."""
    meta = read_envelope(undo)
    raw = meta.get("path")
    if not raw:
        return None
    if ONE_FILE_KINDS.get(str(undo.get("kind") or "")) == "absolute":
        return Path(str(raw))
    base = meta.get("workspace") or workspace
    if not base:
        return None
    try:
        return safe_path(Path(str(base)), str(raw))
    except Exception:  # noqa: BLE001 — cannot place it: not provably clean
        return None


def settle_unfinished(
    undo: "dict[str, Any] | None",
    *,
    workspace: "str | Path | None",
    home: str | Path,
    finished: bool,
    reclaim: bool = True,
) -> "dict[str, Any] | None":
    """What becomes of a captured inverse when its call did NOT succeed.

    ``finished`` True: the tool is done (it returned a failure or raised, or
    the process that ran it died) — the target is hashed now. Nothing changed
    (the file has its before-hash, or a file it would have created is not
    there) → ``None``. Anything else → the inverse, its after-hash being what
    the failure LEFT, so the revert's guard still protects a later edit.

    ``finished`` False: cut off while it may still be writing (a worker thread
    runs on after Stop or the deadline) → the inverse, marked
    :data:`INTERRUPTED_KEY`; nothing can be proven yet.

    Only :data:`ONE_FILE_KINDS` are kept; any other kind is ``None``, as
    before this version. Whenever ``None`` is returned, the before-copy blob is
    removed (no row will refer to it) — unless ``reclaim`` is False, for a
    caller that removes it once its own transaction has committed. Blocking:
    call it off the event loop."""
    if not undo:
        return None
    kind = str(undo.get("kind") or "")
    if kind not in ONE_FILE_KINDS or not undo.get("reversible", True):
        if reclaim:
            drop_preimages(home, undo)
        return None
    if not finished:
        return _with_meta(undo, **{INTERRUPTED_KEY: True})
    target = _one_file_target(undo, workspace)
    if target is None:
        # Cannot look at it: keep it, as cautiously as a cut-off call.
        return _with_meta(undo, **{INTERRUPTED_KEY: True})
    memory = ONE_FILE_KINDS[kind] == "absolute"
    # A memory note's before-hash is over its text (newline-invariant); a
    # workspace file's is over its exact bytes.
    mode = "text" if memory else str(read_envelope(undo).get("mode") or "raw")
    if kind in ("file_delete", "memory_delete_file"):
        clean = not target.exists()
    else:
        pre = undo.get("pre_sha256")
        clean = pre is not None and target.is_file() and (
            sha256_target(target, "text" if memory else "raw") == pre
        )
    if clean:
        if reclaim:
            drop_preimages(home, undo)
        return None
    return _with_meta(
        {**undo, "post_sha256": sha256_target(target, mode)}, **{INTERRUPTED_KEY: None}
    )


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


def _write_prior(target: Path, prior: bytes, mode: str, meta: dict[str, Any]) -> None:
    """Put the before-copy back, exactly as the ordinary restore does."""
    target.parent.mkdir(parents=True, exist_ok=True)
    if mode == "text" and not meta.get("exact"):
        target.write_text(prior.decode("utf-8"), encoding="utf-8")
    else:
        target.write_bytes(prior)


def _revert_interrupted(
    undo: dict[str, Any], meta: dict[str, Any], target: Path, rel: str, mode: str, home: Any
) -> ToolResult:
    """The revert of a call cut off mid-write (v1.504.0, 4.1). BLOCKING.

    Nobody knows what it left, so: a file still as it was is left alone and
    the answer says nothing was changed; otherwise the earlier version goes
    back, and whatever is there now is MOVED to the app's trash first unless
    its hash proves it is exactly what the call meant to write (a later edit
    of the user's is kept, never overwritten).

    "Nothing changed YET" is a refusal, not a success: the stopped call's
    worker may still be writing, so the row (and its before-copy) stays for
    an Undo pressed after the write lands; the next boot settles it."""
    kind = undo.get("kind")
    exists = target.exists()
    if kind == "file_delete" and not exists:
        return ToolResult(
            ok=False,
            error=f"nothing to undo yet: {rel} has not been made (the call was stopped first)",
        )
    pre = undo.get("pre_sha256")
    if kind == "file_restore" and pre and target.is_file() and sha256_target(target, "raw") == pre:
        return ToolResult(
            ok=False,
            error=f"nothing to undo yet: {rel} is as it was before the stopped call",
        )
    prior: "bytes | None" = None
    if kind == "file_restore":
        prior = resolve_prior_bytes(home, undo, meta)
        if prior is None:
            return ToolResult(ok=False, error="undo: pre-image unavailable")
    post = undo.get("post_sha256")
    proven = (
        exists and post is not None and guard_unchanged(sha256_target(target, mode), post) is None
    )
    kept: "Path | None" = None
    try:
        if exists and not proven:
            kept = move_to_trash(home, target, rel)
        elif exists and kind == "file_delete":
            target.unlink()
        if prior is not None:
            _write_prior(target, prior, mode, meta)
    except OSError as exc:
        return ToolResult(ok=False, error=f"undo: could not put {rel} back: {exc}")
    delete_preimage(home, undo.get("pre_ref"))
    if kind == "file_delete":
        words = f"undo: removed created file {rel}"
        if kept is not None:
            words += f" (moved to the trash: {kept})"
        return ToolResult(ok=True, output=words)
    words = f"undo: restored prior content of {rel}"
    if kept is not None:
        words += f" (the version that was there is kept in the trash: {kept})"
    return ToolResult(ok=True, output=words)


def move_to_trash(home: str | Path, target: Path, rel: str) -> Path:
    """MOVE a created file into ``<home>/trash/<stamp>/undo/<rel>`` instead of
    deleting it (the v1.256.0 rule: the move IS the recoverability). Used when
    Undo cannot PROVE the file is still the one the tool made. Raises
    ``OSError`` when it cannot be moved (the caller then removes nothing)."""
    import os
    import shutil
    from datetime import datetime, timezone

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    parts = [p for p in str(rel).replace("\\", "/").split("/") if p not in ("", ".", "..")]
    dest = Path(home) / "trash" / stamp / "undo" / Path(*(parts or [target.name]))
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest = dest.with_name(f"{dest.name}.{os.getpid()}-{new_id('t')[-6:]}")
    shutil.move(str(target), str(dest))
    return dest


async def revert_workspace_file(
    undo: dict[str, Any], ctx: ToolContext
) -> ToolResult:
    """Shared inverse for the workspace file writers (write_file / edit_file /
    write_document).

    Re-hashes the CURRENT target and refuses on drift, then either restores the
    prior bytes (``file_restore``) or unlinks the path we created
    (``file_delete``). Resolution goes through :func:`safe_path`, so the revert
    obeys the SAME workspace-only fs policy as the forward write.

    OFF THE EVENT LOOP (v1.153.1's rule; the capture half was brought in line in
    ``tools/builtins.py:369-402`` and this — the shared inverse for write_file /
    edit_file / write_document / excel_edit / pdf_merge / redact_pii — was not).
    One hop covers the whole body because every branch is filesystem work: the
    re-hash of the CURRENT target, the pre-image load (a blob file for anything
    over 8 KB, i.e. every real document), and the full write-back. ``RevertConflict``
    is RAISED, not returned, and ``asyncio.to_thread`` re-raises it in the awaiting
    coroutine unchanged — the ``/undo`` route's 409 mapping is untouched."""

    def _revert() -> ToolResult:
        meta = read_envelope(undo)
        rel = meta.get("path")
        mode = meta.get("mode", "raw")
        kind = undo.get("kind")
        home = ctx.config.home

        if kind == "files_delete":
            # Multi-file creation envelope (v1.166.0): unlink every path we created.
            # No pre-image was captured (these are post-hoc creations), so there is
            # nothing to restore — a path already gone is reported, not an error.
            paths = meta.get("paths")
            if not isinstance(paths, list) or not paths:
                return ToolResult(ok=False, error="undo: no target paths recorded")
            # v1.499.3: each file's hash, taken as the row was written. ALL
            # are checked before ANY is touched: one file edited since
            # refuses the whole Undo (it would remove a newer change). A row
            # from before the hashes (or a file that could not be hashed)
            # proves nothing, so its file is MOVED to the trash, not deleted.
            made = meta.get("sha256") if isinstance(meta.get("sha256"), dict) else {}
            targets: list[tuple[str, Path]] = []
            for p in paths:
                try:
                    targets.append((str(p), safe_path(ctx.workspace, str(p))))
                except Exception as exc:  # path escaped the workspace
                    return ToolResult(ok=False, error=f"undo: unsafe path: {exc}")
            for p, target in targets:
                if target.exists():
                    conflict = guard_unchanged(sha256_target(target, "raw"), made.get(p))
                    if conflict is not None:
                        raise RevertConflict(f"{p}: {conflict}")
            removed: list[str] = []
            missing: list[str] = []
            trashed: list[str] = []
            for p, target in targets:
                try:
                    if not target.exists():
                        missing.append(p)
                        continue
                    if made.get(p):
                        target.unlink()
                    else:
                        move_to_trash(home, target, p)
                        trashed.append(p)
                    removed.append(p)
                except OSError as exc:
                    return ToolResult(
                        ok=False, error=f"undo: could not remove {p}: {exc}"
                    )
            note = f"undo: removed {len(removed)} created file(s)"
            if trashed:
                note += f" ({len(trashed)} moved to the trash, in {Path(home) / 'trash'})"
            if missing:
                note += f" ({len(missing)} already gone)"
            return ToolResult(ok=True, output=note)

        if not rel:
            return ToolResult(ok=False, error="undo: no target path recorded")
        try:
            target = safe_path(ctx.workspace, rel)
        except Exception as exc:  # path escaped the workspace — never write outside it
            return ToolResult(ok=False, error=f"undo: unsafe path: {exc}")

        if kind == "file_rename":
            # The inverse of a rename is a rename back (v1.177.2). No pre-image and
            # no content hash: the BYTES never changed, so `guard_unchanged` has
            # nothing to compare and running it would refuse every undo. What must
            # be checked instead is that the two ends still look like a rename —
            # the file is where we left it, and putting it back would not clobber
            # something that has since taken the old name.
            origin = meta.get("rename_from")
            if not origin:
                return ToolResult(ok=False, error="undo: no original path recorded")
            try:
                back = safe_path(ctx.workspace, str(origin))
            except Exception as exc:  # noqa: BLE001 — never write outside the workspace
                return ToolResult(ok=False, error=f"undo: unsafe path: {exc}")
            if not target.exists():
                return ToolResult(
                    ok=False,
                    error=f"undo: {rel} is no longer there (renamed or removed since)",
                )
            if back.exists():
                return ToolResult(
                    ok=False,
                    error=(
                        f"undo: {back.name} already exists again — restoring the old "
                        "name would overwrite it"
                    ),
                )
            try:
                target.replace(back)
            except OSError as exc:
                return ToolResult(ok=False, error=f"undo: could not rename back: {exc}")
            return ToolResult(ok=True, output=f"undo: renamed {rel} back to {origin}")

        if meta.get(INTERRUPTED_KEY) and kind in ("file_restore", "file_delete"):
            return _revert_interrupted(undo, meta, target, str(rel), mode, home)

        conflict = guard_unchanged(sha256_target(target, mode), undo.get("post_sha256"))
        if conflict is not None:
            raise RevertConflict(conflict)

        if kind == "file_delete":
            # v1.499.3: unlinked only when the hash above PROVED it is the
            # file the action made; with no hash (a row from before hashes
            # were taken) it is moved to the trash, never deleted unchecked.
            moved: "Path | None" = None
            try:
                if target.exists():
                    if undo.get("post_sha256") is not None:
                        target.unlink()
                    else:
                        moved = move_to_trash(home, target, str(rel))
            except OSError as exc:
                return ToolResult(ok=False, error=f"undo: could not remove {rel}: {exc}")
            delete_preimage(home, undo.get("pre_ref"))
            if moved is not None:
                return ToolResult(
                    ok=True, output=f"undo: removed created file {rel} (moved to the trash: {moved})"
                )
            return ToolResult(ok=True, output=f"undo: removed created file {rel}")

        if kind == "file_restore":
            prior = resolve_prior_bytes(home, undo, meta)
            if prior is None:
                return ToolResult(ok=False, error="undo: pre-image unavailable")
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                if mode == "text" and not meta.get("exact"):
                    target.write_text(prior.decode("utf-8"), encoding="utf-8")
                else:
                    target.write_bytes(prior)
            except OSError as exc:
                return ToolResult(ok=False, error=f"undo: could not restore {rel}: {exc}")
            delete_preimage(home, undo.get("pre_ref"))
            return ToolResult(ok=True, output=f"undo: restored prior content of {rel}")

        return ToolResult(ok=False, error=f"undo: unknown file undo kind {kind!r}")

    return await asyncio.to_thread(_revert)
