"""Memory tools (§19 tool interface, §21 layered memory).

Three thin tools over ``MemoryLayers`` — write, read, and similarity search —
each constructed with the layer manager injected. ``memory_tools(layers)`` builds
the trio for registration in the tool registry.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from ..core.events import EventType
from ..tools.base import Reversibility, Tool, ToolContext, ToolResult
from . import origin as _origin
from .layers import MemoryLayers

#: The undo journal kinds of a ``memory_write`` (v1.501.0, north-star 1.11):
#: an entry it CHANGED (the earlier text is put back) or CREATED (removed).
UNDO_RESTORE = "memory_entry_restore"
UNDO_REMOVE = "memory_entry_remove"

#: Said when an entry by this key belongs to work this turn may not be told
#: of (another client's folder): nothing about it is shown or named.
NOT_HERE = (
    "nothing was changed: an entry with this key was written during other "
    "work and is not shown here, so it is left as it is. Use another key"
)


def _hash(text: str) -> str:
    from ..tools.undo import sha256_bytes

    return sha256_bytes(str(text or "").encode("utf-8"))


class MemoryWriteTool(Tool):
    """Store or update a memory entry in a layer (§21).

    v1.501.0 (north-star 1.2): the entry is keyed and stamped by where it was
    written (``memory/origin.py``): a ``session`` entry by its session, a
    ``project`` entry by its project or folder, and every entry carries the
    writing session and the folder the client wall keyed the turn by. An
    entry this turn may not be told of is never written over.

    v1.501.0 (north-star 1.11): REVERSIBLE. The earlier text (and its stamp)
    is journaled BEFORE the write; Undo puts it back, or removes an entry
    this call created, and refuses when the entry changed since."""

    name = "memory_write"
    description = "Write or update a memory entry (layer/key/text) in layered memory."
    permission_key = "memory_write"
    reversibility = Reversibility.REVERSIBLE
    input_schema = {
        "type": "object",
        "properties": {
            "layer": {"type": "string", "enum": list(MemoryLayers.LAYERS)},
            "key": {"type": "string"},
            "text": {"type": "string"},
        },
        "required": ["layer", "key", "text"],
    }

    def __init__(self, layers: MemoryLayers) -> None:
        self.layers = layers

    def _blocked(self, row: Any, ctx: Any) -> bool:
        """Is ``row`` an entry this turn may not be told of (so not written over)?"""
        if row is None:
            return False
        verdict = _origin.told(
            self.layers.engine, [row],
            session=_origin.session_tag(ctx), project_id=getattr(ctx, "project_id", None),
        )
        return not (verdict and verdict[0])

    async def capture_undo(self, args: dict[str, Any], ctx: ToolContext) -> "dict[str, Any] | None":
        """The inverse, taken BEFORE the write (the registry calls this first):
        the row's earlier text and stamp (``memory_entry_restore``), or that
        the row will be created (``memory_entry_remove``). ``post_sha256`` is
        the hash of the text the write will leave (masked, as ``write``
        masks it), so an Undo over a later change refuses. Off the loop."""
        layer, key, text = args.get("layer"), args.get("key"), args.get("text")
        if layer not in MemoryLayers.LAYERS or not isinstance(key, str) or not isinstance(text, str):
            return None
        scope = _origin.writer_scope(layer, ctx)

        def _capture() -> "dict[str, Any] | None":
            from ..core.ids import new_id
            from ..detections.redact import mask
            from ..tools.undo import INLINE_MAX_BYTES, save_preimage

            row = self.layers.find(layer, key, scope)
            if self._blocked(row, ctx):
                return None  # the write will refuse: nothing to undo
            entry = {"layer": layer, "key": key, "scope_id": scope}
            post = _hash(mask(text))
            if row is None:
                return {"kind": UNDO_REMOVE, "reversible": True, "pre_ref": None,
                        "pre_inline": json.dumps({"entry": entry}), "pre_sha256": None,
                        "post_sha256": post}
            prior = {"text": row.text, "origin_session": row.origin_session,
                     "origin_folder": row.origin_folder}
            blob = json.dumps(prior).encode("utf-8")
            pre_ref = None
            if len(blob) > INLINE_MAX_BYTES:
                pre_ref = save_preimage(ctx.config.home, new_id("undo"), blob)
                prior = None
            return {"kind": UNDO_RESTORE, "reversible": True, "pre_ref": pre_ref,
                    "pre_inline": json.dumps({"entry": entry, "prior": prior}),
                    "pre_sha256": _hash(row.text), "post_sha256": post}

        return await asyncio.to_thread(_capture)

    async def revert(self, undo: dict[str, Any], ctx: ToolContext) -> ToolResult:
        """Put the earlier text back (or remove the created entry). Refuses
        (``RevertConflict``, the route's 409) when the entry is gone or was
        changed since this write. Off the loop."""
        from ..tools.undo import RevertConflict, delete_preimage, load_preimage, read_envelope

        meta = read_envelope(undo)
        entry = meta.get("entry") if isinstance(meta, dict) else None
        if not isinstance(entry, dict) or entry.get("layer") not in MemoryLayers.LAYERS:
            return ToolResult(ok=False, error="undo: no memory entry recorded")
        layer, key, scope = entry["layer"], str(entry.get("key") or ""), entry.get("scope_id")
        kind = undo.get("kind")

        def _revert() -> ToolResult:
            row = self.layers.find(layer, key, scope)
            if row is None or _hash(row.text) != undo.get("post_sha256"):
                raise RevertConflict(
                    f"the memory entry '{key}' changed since (or was removed), so "
                    "it was left as it is"
                )
            if kind == UNDO_REMOVE:
                self.layers.delete(layer, key, scope)
                return ToolResult(ok=True, output=f"undo: removed memory entry '{key}'")
            if kind != UNDO_RESTORE:
                return ToolResult(ok=False, error=f"undo: unknown memory undo kind {kind!r}")
            prior = meta.get("prior")
            if prior is None and undo.get("pre_ref"):
                prior = json.loads(load_preimage(ctx.config.home, undo["pre_ref"]).decode("utf-8"))
            if not isinstance(prior, dict) or not isinstance(prior.get("text"), str):
                return ToolResult(ok=False, error="undo: the earlier text is not available")
            self.layers.write(layer, key, prior["text"], scope_id=scope,
                              origin=(prior.get("origin_session"), prior.get("origin_folder")))
            delete_preimage(ctx.config.home, undo.get("pre_ref"))
            return ToolResult(ok=True, output=f"undo: memory entry '{key}' has its earlier text again")

        return await asyncio.to_thread(_revert)

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        from ..detections.redact import mask
        from ..ltm.tools import SECRET_LEFT_OUT

        text = args["text"]
        layer, key = args["layer"], args["key"]
        # v1.501.0 (north-star 1.2): keyed and stamped by where it was written.
        scope = _origin.writer_scope(layer, ctx) if layer in MemoryLayers.LAYERS else None
        stamp = _origin.writer_origin(ctx)

        def _write() -> Any:
            if layer in MemoryLayers.LAYERS and self._blocked(self.layers.find(layer, key, scope), ctx):
                return None
            return self.layers.write(layer, key, text, scope_id=scope, origin=stamp)

        # v1.499.3: MemoryLayers.write keeps a secret masked; say so, as
        # remember_preference and ltm_append do.
        left_out = isinstance(text, str) and mask(text) != text
        record = await asyncio.to_thread(_write)  # DB + the wall's map: off the loop
        if record is None:
            return ToolResult(ok=False, error=NOT_HERE)
        await ctx.event_bus.publish(
            EventType.MEMORY_UPDATED,
            {"layer": record.layer, "key": record.key, "id": record.id},
            session_id=ctx.session_id,
        )
        return ToolResult(
            ok=True,
            output=f"stored memory '{record.key}' in layer '{record.layer}'"
            + (f". {SECRET_LEFT_OUT}" if left_out else ""),
            data={"id": record.id, "layer": record.layer, "key": record.key},
        )


class MemoryReadTool(Tool):
    """Read a memory entry by (layer, key) (§21)."""

    name = "memory_read"
    # Stored memory can carry planted content, exactly like memory_search's
    # results (v1.499.3: this one went to the model unfenced).
    returns_untrusted_content = True
    description = "Read a memory entry by layer and key; empty result if absent."
    permission_key = "memory_read"
    input_schema = {
        "type": "object",
        "properties": {
            "layer": {"type": "string", "enum": list(MemoryLayers.LAYERS)},
            "key": {"type": "string"},
        },
        "required": ["layer", "key"],
    }

    def __init__(self, layers: MemoryLayers) -> None:
        self.layers = layers

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        layer, key = args["layer"], args["key"]

        def _read() -> "str | None":
            # v1.501.0 (north-star 1.2): this turn's own entry first (the key
            # ``memory_write`` keys it by), else the one shared row a write
            # before this change left; told only past the client wall.
            row = None
            if layer in MemoryLayers.LAYERS:
                scope = _origin.writer_scope(layer, ctx)
                row = self.layers.find(layer, key, scope)
                if row is None and scope is not None:
                    row = self.layers.find(layer, key, None)
            else:
                return self.layers.read(layer, key)  # the layer's own refusal
            if row is None:
                return None
            verdict = _origin.told(
                self.layers.engine, [row],
                session=_origin.session_tag(ctx), project_id=getattr(ctx, "project_id", None),
            )
            return row.text if verdict and verdict[0] else None

        text = await asyncio.to_thread(_read)
        if text is None:
            return ToolResult(ok=True, output="", data={"found": False})
        return ToolResult(ok=True, output=text, data={"found": True})


class MemorySearchTool(Tool):
    """Cosine-similarity search across memory (§22)."""

    name = "memory_search"
    returns_untrusted_content = True  # stored memory can contain planted content
    description = "Search layered memory by semantic similarity; returns top-k matches."
    permission_key = "memory_search"
    input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "k": {"type": "integer", "minimum": 1},
        },
        "required": ["query"],
    }

    def __init__(self, layers: MemoryLayers) -> None:
        self.layers = layers

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        k = int(args.get("k", 5))

        def _search() -> list:
            # v1.501.0 (north-star 1.2): the same telling as recall
            # (``memory/origin.told``); asked for a surplus so a walled entry
            # does not cost this turn its own hits.
            pairs = self.layers.search(args["query"], k=max(k, 1) * 3)
            verdicts = _origin.told(
                self.layers.engine, [rec for rec, _s in pairs],
                session=_origin.session_tag(ctx), project_id=getattr(ctx, "project_id", None),
            )
            return [p for p, ok in zip(pairs, verdicts) if ok][:k]

        hits = await asyncio.to_thread(_search)
        results = [
            {
                "id": rec.id,
                "layer": rec.layer,
                "key": rec.key,
                "text": rec.text,
                "score": score,
            }
            for rec, score in hits
        ]
        output = "\n".join(
            f"[{r['score']:.3f}] ({r['layer']}/{r['key']}) {r['text']}" for r in results
        )
        return ToolResult(
            ok=True, output=output, data={"results": results, "count": len(results)}
        )


def memory_tools(layers: MemoryLayers) -> list[Tool]:
    """Build the memory tool trio bound to a single ``MemoryLayers`` instance."""
    return [
        MemoryWriteTool(layers),
        MemoryReadTool(layers),
        MemorySearchTool(layers),
    ]
