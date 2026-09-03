"""Tool Registry (§19).

Central registration, discovery, permission enforcement, execution, logging,
and event emission. Every invocation is gated by the Permission Engine and
recorded as a ToolInvocation (§19 responsibilities).
"""

from __future__ import annotations

import asyncio
import copy
import dataclasses
import json
import os
from collections import OrderedDict
from pathlib import Path
from typing import Any, Iterable

from ..core.db import dumps, session_scope
from ..core.events import EventType
from ..core.ids import new_id
from ..core.fs_policy import fs_read_ok
from ..core.models import PermissionMode, ToolInvocation, UndoJournal
from .base import Reversibility, Tool, ToolContext, ToolResult, safe_path
from .permissions import PermissionDecision, PermissionEngine
from .undo import make_file_descriptor
from .undo import finalize_post_hash


#: Tools whose output is routinely LARGE enough to be worth keeping out of the
#: context window — the only ones that advertise ``_store_as`` (v1.159.0).
#:
#: Deliberately a short list rather than every tool. The parameter costs ~30
#: tokens of schema on every request it appears in, so putting it on all ~60
#: tools would spend more context than the feature saves — which would be a
#: peculiar way to build a context-saving feature. These are the ones that
#: return listings, documents, page text and search results.
VERBOSE_TOOLS: frozenset[str] = frozenset({
    "list_files", "grep", "file_search", "code_search",
    "read_file", "read_document", "extract_pdf", "convert_document",
    "excel_query", "batch_documents",
    "web_fetch", "web_search",
    "ltm_search", "recall", "history_search",
    "shell", "run_code",
})

#: What the model is told about it. One line: the `repl` tool's own description
#: carries the fuller explanation, and repeating it per tool is the cost above.
_STORE_AS_SCHEMA = {
    "type": "string",
    "description": (
        "Optional variable name. Binds this result into the session's Python "
        "namespace INSTEAD of returning it, and returns a one-line receipt — "
        "use the `repl` tool to inspect it (len, slicing, comprehensions). "
        "The variable is a dict: v['output'] is the tool's text payload, "
        "v['data'] its structured metadata. Prefer this whenever the result "
        "may be large."
    ),
}


#: Ceiling on the failure text composed for a failed tool call (v1.174.0).
#: Big enough for a real stack trace or a compiler's complaint, small enough
#: that a failing loop cannot spend the context window on repeats.
_FAILURE_DIAGNOSTIC_CHARS = 2000

#: Marker used when the captured stdout/stderr is longer than the budget. The
#: TAIL is what survives: a command that fails says WHY on its last lines, and
#: the head is usually progress noise.
_FAILURE_DROPPED = "[earlier output dropped]\n"

#: Header separating the tool's own error from the process output it captured.
_FAILURE_JOIN = "\n[output]\n"


def compose_failure_text(
    error: str | None,
    output: str | None,
    *,
    limit: int = _FAILURE_DIAGNOSTIC_CHARS,
) -> str:
    """The diagnostic a FAILED tool call hands to the model and the ledger.

    THE MEASURED BUG (v1.174.0): ``ShellTool`` already captured stderr into
    ``output`` — and every consumer threw it away, because both the runtime
    (``result.output if result.ok else result.error``) and this module's
    ``_record`` read ``error`` alone on failure. A real run spent five steps on
    five shell calls whose entire feedback was the string ``exit 1``. The model
    had nothing to correct, so it guessed, failed again, and burned its budget.

    Composed HERE, once, so the runtime, both chat lanes and the execution
    ledger all improve without touching any of them.

    Byte-identical to the old behaviour whenever there is no captured output —
    that is the additive guarantee, not a happy accident: a tool that only ever
    sets ``error`` gets its error back unchanged.

    STRICTLY ADDITIVE AT THE EDGES TOO (v1.174.0 review). Only the OUTPUT half is
    ever bounded. An earlier draft returned ``header[:limit]`` once the error
    alone filled the budget, so a feature whose entire purpose is to PRESERVE
    diagnostics could shorten the one part that was never lost before — a
    2100-char traceback got clipped only because the tool had also captured a
    byte of stdout. The error is now returned whole; the output tail is appended
    only while there is genuinely room for it (marker included), so the result
    never exceeds ``limit`` unless the error itself already did.
    """
    err = (error or "").strip()
    out = (output or "").strip()
    if not out:
        return err
    header = err or "the tool reported no error message"
    room = limit - len(header) - len(_FAILURE_JOIN)
    if room <= len(_FAILURE_DROPPED):
        # No honest room for a tail (nor for the marker that would admit the
        # tail was cut). Hand back the error EXACTLY as the tool wrote it.
        return header
    if len(out) > room:
        keep = room - len(_FAILURE_DROPPED)
        out = _FAILURE_DROPPED + out[len(out) - keep :]
    return header + _FAILURE_JOIN + out


#: Tools whose successful text output is worth REMEMBERING for the rest of a
#: session (contract 3, v1.174.0).
#:
#: The evidence: on a 26-file folder an agent called ``extract_pdf`` six times
#: (one file twice) and then ``read_document`` six times — three of them on
#: files it had ALREADY read two steps earlier. Twelve of eighteen tool calls
#: re-derived text the run already had.
#:
#: The cache is deliberately NOT keyed on the tool name, because the wasteful
#: pattern in the evidence crosses tools: ``extract_pdf`` then ``read_document``
#: on the same PDF. What identifies a read is the FILE, not the door used to
#: open it — so the note names which call produced the text.
#:
#: BUT THE DOORS ARE NOT EQUALLY CAPABLE (v1.174.0 review), and that is the one
#: thing the file-identity key cannot see. ``read_document``/``extract_pdf``
#: carry a router and can transcribe a scan; ``read_file`` is constructed by
#: ``default_registry()`` with no resolver, so it returns a scanned PDF's empty
#: extraction. Serving THAT to a later ``read_document`` would let the blind
#: door permanently answer for the sighted one — on the acceptance folder (11
#: image-only scans) any file opened with ``read_file`` first would stay
#: unreadable for the rest of the scope. So each entry records whether the call
#: that produced it could OCR, and an OCR-capable tool never accepts an entry
#: produced without that capability. See ``_ocr_capable``.
CACHEABLE_READ_TOOLS: frozenset[str] = frozenset(
    {"read_file", "read_document", "extract_pdf"}
)

#: Bounds. The cache lives for the life of the process, so both the entry count
#: and the size of any single entry are capped; an oversized read is simply not
#: remembered (an optimisation that declines is still correct).
_READ_CACHE_MAX_ENTRIES = 256
_READ_CACHE_MAX_CHARS = 400_000

#: Tools whose SUCCESS means the filesystem may have changed under the cache.
#:
#: THE BUG THIS REPLACES (v1.174.0 review): the invalidation used to fire on
#: ``reversibility != READONLY``, which is UNDO semantics standing in for "wrote
#: something". ``Reversibility`` defaults to IRREVERSIBLE (the fail-safe answer
#: for undo), so 69 of the 87 registered tools purged the scope on success —
#: including pure readers like ``worklist_status``, ``memory_read``,
#: ``blackboard_read``, ``recall``, ``tool_list`` and ``web_search``. In the
#: supervisor loop this wave ships (``worklist_next`` → read → rename →
#: ``worklist_done``) a non-READONLY call sits between every pair of reads, so
#: the cache would essentially never hit in the run it was written for.
#:
#: An explicit list is a maintenance cost, and it is the honest one: undo
#: capability is not a filesystem-mutation signal and never was. It is BACKED UP
#: by two objective signals the registry already holds — a journalled undo
#: descriptor and ``created_paths`` — so a tool that really wrote something
#: still drops the scope even if nobody remembered to list it here.
_MUTATING_TOOLS: frozenset[str] = frozenset({
    # arbitrary code / commands
    "shell", "run_code", "code_run", "repl",
    # direct file writes
    "write_file", "edit_file", "write_document", "convert_document",
    "redact_pii", "pdf_arrange", "pdf_split",
    "excel_edit", "excel_apply_spec", "batch_documents",
    "image_convert", "image_resize",
    "pixio_generate",
    # anything that hands the work to something else that can write
    "delegate", "delegate_remote", "spawn_agent", "workflow_run",
})


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        #: names of agent/user-authored (custom) tools, expanded by the
        #: ``"custom:*"`` allowlist sentinel so every agent can reach them.
        self._custom: set[str] = set()
        #: names of external MCP tools (``mcp__<server>__<tool>``), expanded by
        #: the ``"mcp:*"`` allowlist sentinel. Kept SEPARATE from ``_custom`` so
        #: an agent can opt into user-authored tools without also inheriting
        #: every connected external integration (Gmail/Drive/GitHub/...).
        self._mcp: set[str] = set()
        #: READ CACHE (contract 3, v1.174.0). ``key -> {output, data, tool,
        #: step}``, LRU-ordered. See ``CACHEABLE_READ_TOOLS``.
        self._read_cache: "OrderedDict[tuple, dict[str, Any]]" = OrderedDict()
        #: Per-scope mutation generation. Bumped by every SUCCESSFUL non-readonly
        #: tool call, which also purges that scope's cached reads — see
        #: ``_invalidate_scope`` for why mtime+size alone is not enough.
        self._scope_gen: dict[str, int] = {}

    def register(self, tool: Tool, custom: bool = False, mcp: bool = False) -> None:
        if not tool.name:
            raise ValueError("tool must have a name")
        self._tools[tool.name] = tool
        if custom:
            self._custom.add(tool.name)
        if mcp:
            self._mcp.add(tool.name)

    def unregister(self, name: str) -> bool:
        """Remove a tool (used when a custom or MCP tool is deleted). False if absent."""
        self._custom.discard(name)
        self._mcp.discard(name)
        return self._tools.pop(name, None) is not None

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def custom_names(self) -> list[str]:
        return sorted(self._custom)

    def mcp_names(self, server: str | None = None) -> list[str]:
        """Registered external MCP tool names. With ``server`` given, only the
        tools of ``mcp__<server>__*`` (used to count/unload one server's tools)."""
        if server is None:
            return sorted(self._mcp)
        prefix = f"mcp__{server}__"
        return sorted(n for n in self._mcp if n.startswith(prefix))

    def specs(self, allowed: list[str] | None = None) -> list[dict[str, Any]]:
        tools = list(self._tools.values())
        if allowed is not None:
            allow = set(allowed)
            wild = "custom:*" in allow  # reach every custom tool, by name unknown
            mcp_wild = "mcp:*" in allow  # reach every connected MCP tool
            tools = [
                t for t in tools
                if t.name in allow
                or (wild and t.name in self._custom)
                or (mcp_wild and t.name in self._mcp)
            ]
        # ADVERTISE-TIME HEALTH (v1.205.0): a custom tool whose program is no
        # longer installed is not offered to models — every model-facing
        # catalog (both chat lanes, the agent runtime, decompose) builds
        # through here, so gating once covers them all. The record itself is
        # NEVER deleted: it stays in the dynamic-tool registry and on the
        # Tools page (GET /tools/custom reads that directly), where the user
        # can see it and choose to delete it. Grounded in a live task where a
        # dead `rename_real_file` was advertised forever and failed 22/22.
        tools = [t for t in tools if not self._unavailable_custom(t)]
        return [self._spec_with_store_as(t) for t in tools]

    def _unavailable_custom(self, tool: Tool) -> bool:
        """True only for a CUSTOM tool that reports its program missing.

        Built-ins and MCP tools are never probed (they carry no
        ``missing_program``), and a probe that CRASHES fails open — a broken
        health check must not hide a working tool from every model.
        """
        if tool.name not in self._custom:
            return False
        probe = getattr(tool, "missing_program", None)
        if not callable(probe):
            return False
        try:
            return bool(probe())
        except Exception:  # noqa: BLE001 — health must never break the catalog
            return False

    def _spec_with_store_as(self, tool: Tool) -> dict[str, Any]:
        """A tool's spec, plus ``_store_as`` for the verbose ones.

        Added HERE rather than in each tool's own ``input_schema`` because the
        parameter is a registry-level convention: the registry is what strips
        and honours it, and a tool that declared it would be declaring something
        it never sees.

        DEEP-COPIED FIRST, and that is not a nicety. ``spec()`` hands back a dict
        that still REFERENCES the tool's class-level ``input_schema``, so
        injecting in place permanently rewrote the tool's own declared schema for
        the life of the process — every later caller, and every later test, saw a
        schema it never declared. Caught by the full suite: a schema-shape test
        passed alone and failed after anything else had called ``specs()``.
        """
        spec = tool.spec()
        if tool.name not in VERBOSE_TOOLS:
            return spec
        try:
            spec = copy.deepcopy(spec)
            params = spec.get("parameters") or spec.get("input_schema") or {}
            props = params.get("properties")
            if isinstance(props, dict) and "_store_as" not in props:
                props["_store_as"] = dict(_STORE_AS_SCHEMA)
        except Exception:  # noqa: BLE001 — a spec we cannot extend still works
            return tool.spec()
        return spec

    async def invoke(
        self,
        name: str,
        args: dict[str, Any],
        ctx: ToolContext,
        perms: PermissionEngine,
        agent_overrides: dict[str, str] | None = None,
        session_allow: "Iterable[str] | None" = None,
        deny_reason: str = "",
        deny_label: str = "permission denied",
    ) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(ok=False, error=f"unknown tool '{name}'")

        # `_store_as` (v1.159.0): put the RESULT IN A VARIABLE instead of in the
        # context window.
        #
        # This is the whole point of the session namespace. Tool output is what
        # floods a model's context, and this app spent three releases attacking
        # that from the wrong end — a 16k-char cap per result, stale-output
        # trimming, a token budget, then model-written compaction. All of them
        # decide what to THROW AWAY. `_store_as` decides what never has to
        # arrive: `list_files(path=".", _store_as="files")` returns a one-line
        # receipt, the 5,000 entries live on as `files` in the session's Python
        # namespace, and the model reaches them with len(), a slice, or a
        # comprehension through the `repl` tool.
        #
        # Stripped from `args` BEFORE the tool sees it: every tool validates its
        # own schema and an unexpected key is not the tool's problem to handle.
        store_as = ""
        if isinstance(args, dict) and args.get("_store_as"):
            args = dict(args)
            store_as = str(args.pop("_store_as") or "").strip()

        # A caller that already asked a human and was refused passes the answer
        # in (v1.155.0). It goes through the SAME record-and-publish path as any
        # other denial, so the execution ledger — which `agents/outcome` derives
        # what a run did from — never loses a refusal just because the decision
        # was made upstream.
        #
        # `deny_label` names WHO refused (v1.174.0 review). The seam is also used
        # by the runtime's repeated-failure breaker, and calling the app's own
        # circuit breaker "permission denied" is a misattribution the model
        # relays to the user as "I don't have permission" — and both chat lanes
        # string-match that exact phrase to list a turn's user-refused tools.
        # Default unchanged, so every existing caller reads byte-identically.
        if deny_reason:
            decision = PermissionDecision(False, PermissionMode.ASK, deny_reason)
        else:
            decision = perms.authorize(
                tool.perm_key(), args, agent_overrides, session_allow=session_allow
            )
        reversibility = getattr(tool, "reversibility", Reversibility.IRREVERSIBLE)
        rev_value = reversibility.value if isinstance(reversibility, Reversibility) else str(reversibility)

        if not decision.allowed:
            # Only a caller-supplied refusal may relabel itself; a decision the
            # engine really made is always "permission denied".
            label = deny_label if deny_reason else "permission denied"
            inv_id = await asyncio.to_thread(
                self._record,
                ctx, name, args, decision.mode, ok=False,
                output=decision.reason, reversibility=rev_value,
            )
            await ctx.event_bus.publish(
                EventType.TOOL_DENIED,
                {"tool": name, "mode": decision.mode.value, "reason": decision.reason,
                 "invocation_id": inv_id, "reversibility": rev_value,
                 "kind": label},
                session_id=ctx.session_id,
            )
            return ToolResult(ok=False, error=f"{label}: {decision.reason}")

        # READ CACHE (contract 3, v1.174.0) — checked AFTER authorization, never
        # before it. The plan sketched this between the tool lookup and the
        # permission check; serving remembered file text to a call the engine
        # would have refused is a hole, and the whole benefit (skipping the
        # extraction/OCR) survives the later placement intact.
        #
        # AUTHORIZATION IS NOT THE WHOLE GATE (v1.174.0 review). The
        # PermissionEngine authorizes by tool NAME; the PATH authority lives
        # inside each tool (`safe_path` for read_file, `fs_read_ok` for the
        # document tools) and the cache sits in front of it. Measured: read_file
        # on an out-of-workspace file returned ok=False, read_document (which may
        # read anywhere) then read it, and the IDENTICAL read_file call came back
        # ok=True with the full contents. `_read_cache_key` therefore runs the
        # REQUESTING tool's own path gate and yields None when it refuses — no
        # identity, so nothing is served and nothing is remembered, and the tool
        # runs and produces its own honest error.
        cache_key: tuple | None = None
        cache_gen = 0
        if name in CACHEABLE_READ_TOOLS:
            cache_key = await self._read_cache_key(name, args, ctx)
            if cache_key is not None:
                cache_gen = self._scope_gen.get(cache_key[0], 0)
                hit = self._read_cache.get(cache_key)
                # A blind door never answers for a sighted one: an OCR-capable
                # tool re-runs rather than inherit a non-OCR extraction of what
                # may be a scan.
                if hit is not None and self._ocr_capable(name) and not hit.get(
                    "ocr_capable"
                ):
                    hit = None
                if hit is not None:
                    self._read_cache.move_to_end(cache_key)
                    cached = self._cached_result(hit)
                    inv_id = await asyncio.to_thread(
                        self._record,
                        ctx, name, args, decision.mode, ok=True,
                        output=cached.output, reversibility=rev_value,
                    )
                    await ctx.event_bus.publish(
                        EventType.TOOL_EXECUTED,
                        {"tool": name, "ok": True, "mode": decision.mode.value,
                         "invocation_id": inv_id, "reversibility": rev_value,
                         "cached": True},
                        session_id=ctx.session_id,
                    )
                    if store_as:
                        cached = await self._store_result(store_as, cached, ctx)
                    return cached

        # TX-01 undo: snapshot the INVERSE *before* the mutation, for reversible
        # tools only. Best-effort — a capture failure degrades to no-undo (the
        # tool still runs) rather than blocking the action, matching the
        # returns_untrusted_content best-effort discipline.
        undo_desc: dict[str, Any] | None = None
        if reversibility == Reversibility.REVERSIBLE:
            try:
                undo_desc = await tool.capture_undo(args, ctx)
            except Exception:  # noqa: BLE001 — capture never blocks the tool
                undo_desc = None

        try:
            result = await tool.execute(args, ctx)
        except Exception as exc:  # tools must not crash the runtime
            result = ToolResult(ok=False, error=f"{type(exc).__name__}: {exc}")

        # CONTRACT 1 (v1.174.0): a failure carries its REAL diagnostic from here
        # on. Rewriting `error` (rather than teaching each consumer to also read
        # `output`) is what makes the runtime, both chat lanes and the ledger
        # improve without a line of change in any of them.
        if not result.ok:
            diagnostic = compose_failure_text(result.error, result.output)
            if diagnostic != (result.error or ""):
                result = dataclasses.replace(result, error=diagnostic)

        # For a raw/binary write the capture could not predict the post-image, so
        # re-hash the file NOW (after a successful write) to arm the anti-clobber
        # guard on a future undo. Best-effort — never blocks the tool.
        if result.ok and undo_desc is not None:
            try:
                finalize_post_hash(undo_desc, ctx)
            except Exception:  # noqa: BLE001 — telemetry/guard must never break the tool
                pass

        inv_id = await asyncio.to_thread(
            self._record,
            ctx,
            name,
            args,
            decision.mode,
            ok=result.ok,
            output=result.output if result.ok else (result.error or ""),
            reversibility=rev_value,
            # Only journal an inverse for a SUCCESSFUL mutation (a failed write
            # changed nothing, so there is nothing to undo).
            undo=undo_desc if result.ok else None,
            # Files the tool could not name until it had done the work
            # (v1.157.0) — see ToolResult.created_paths. Journaled through the
            # same path as any other creation so agents/outcome, the run's
            # result card and the preview rail all see them.
            created_paths=result.created_paths if result.ok else None,
        )
        await ctx.event_bus.publish(
            EventType.TOOL_EXECUTED,
            {"tool": name, "ok": result.ok, "mode": decision.mode.value,
             "invocation_id": inv_id, "reversibility": rev_value},
            session_id=ctx.session_id,
        )

        # Cache bookkeeping (contract 3). A cacheable read that SUCCEEDED is
        # remembered; anything else that succeeded and is not read-only has
        # changed the world, so this scope's remembered reads are dropped.
        # Failures are never cached — a transient error must not become the
        # answer for the rest of the session.
        if name in CACHEABLE_READ_TOOLS:
            if result.ok and cache_key is not None:
                self._remember_read(cache_key, name, result, ctx, cache_gen)
        elif result.ok and self._mutates(name, result, undo_desc):
            self._invalidate_scope(self._cache_scope(ctx))

        # Hand the payload to the namespace and return a RECEIPT. Only on
        # success: storing an error message as a variable would be a lie the
        # model then reasons from. A namespace that is unavailable degrades to
        # the ordinary result — the feature is an optimisation, never a
        # precondition.
        if store_as and result.ok:
            result = await self._store_result(store_as, result, ctx)
        return result

    async def _store_result(
        self, name: str, result: ToolResult, ctx: ToolContext
    ) -> ToolResult:
        """Bind ``result`` to ``name`` in this session's namespace.

        The receipt states the TYPE and SIZE of what was stored, because a model
        that cannot see the value needs to know what it is holding before it can
        write code against it. It also names the variable back, so the next
        `repl` call has something concrete to reach for.
        """
        registry = getattr(self, "_repl", None)
        if registry is None:
            return result
        if not name.isidentifier() or name.startswith("__"):
            return ToolResult(
                ok=True,
                output=(
                    f"{result.output}\n\n[not stored: `{name}` is not a usable "
                    f"Python variable name]"
                ),
                data=result.data,
                created_paths=result.created_paths,
            )
        # BOTH halves, always (v1.166.2). The old `data if data else output`
        # pick destroyed the real payload for every actual verbose builtin:
        # list_files/grep/shell/read_file put the payload in `output` and only
        # metadata in `data`, so `list_files(_store_as="tree")` bound
        # {'count': N} and the file names existed NOWHERE — while the receipt
        # asserted "nothing else was returned". Storing the pair loses nothing
        # for any tool shape.
        payload: Any = {"output": result.output, "data": result.data}
        try:
            # Bound by EXECUTING a binding, over the namespace's own public
            # `execute` — the session module owns the pipe protocol and this
            # does not reach around it.
            #
            # The value travels as a JSON string embedded as a Python string
            # LITERAL, so nothing in a tool result can be interpreted as code:
            # json.dumps produces text, repr() makes it an inert literal, and
            # json.loads on the far side turns it back into data. A payload that
            # will not serialise degrades to its str() rather than failing the
            # tool call.
            try:
                encoded = json.dumps(payload, default=str)
            except (TypeError, ValueError):
                encoded = json.dumps(str(payload))
            # The receipt is computed INSIDE the namespace and printed, so the
            # sizes it reports are the sizes of the object that actually landed —
            # not something this side guessed about a value it then shipped. It
            # names BOTH halves explicitly (v1.166.2): a model that cannot see
            # the value must know the payload lives at ['output'] or it will
            # compute over the metadata believing it is the result.
            code = (
                "import json as __ij_json\n"
                f"{name} = __ij_json.loads({encoded!r})\n"
                f"__ij_v = {name}\n"
                "if isinstance(__ij_v, dict) and set(__ij_v) == {'output', 'data'}:\n"
                "    __ij_o, __ij_d = __ij_v['output'], __ij_v['data']\n"
                "    __ij_ds = 'None' if __ij_d is None else '%s%s' % ("
                "type(__ij_d).__name__, "
                "(', %d items' % len(__ij_d)) if hasattr(__ij_d, '__len__') else '')\n"
                "    print(\"stored as `%s` — %s['output'] holds the tool's text "
                "(str, %d chars), %s['data'] its metadata (%s); reach both in the "
                "repl tool; nothing else was returned.\" % ("
                f"{name!r}, {name!r}, len(__ij_o or ''), {name!r}, __ij_ds))\n"
                "    del __ij_o, __ij_d, __ij_ds\n"
                "else:\n"
                "    print('stored as `%s` (%s%s) — reach it in the repl tool; "
                "nothing else was returned.' % ("
                f"{name!r}, type(__ij_v).__name__, "
                "(', %d items' % len(__ij_v)) if hasattr(__ij_v, '__len__') else ''))\n"
                "del __ij_v\n"
            )
            reply = await registry.execute(
                ctx.session_id, code, workspace=str(ctx.workspace)
            )
            if not reply.get("ok"):
                raise RuntimeError(reply.get("error") or "namespace refused the value")
            summary = (reply.get("stdout") or "").strip() or (
                f"stored as `{name}`"
            )
        except Exception as exc:  # noqa: BLE001 — never fail a good tool call
            return ToolResult(
                ok=True,
                output=(
                    f"{result.output}\n\n[could not store as `{name}`: "
                    f"{type(exc).__name__} — the full result is above]"
                ),
                data=result.data,
                created_paths=result.created_paths,
            )
        return ToolResult(
            ok=True,
            output=summary,
            data={"stored_as": name, "kind": type(payload).__name__},
            created_paths=result.created_paths,
        )

    # ---------------------------------------------------------------- #
    # READ CACHE (contract 3, v1.174.0)
    # ---------------------------------------------------------------- #

    def _cache_scope(self, ctx: ToolContext) -> str:
        """``session@folder`` — the SAME composite key shape the session
        namespace uses (``repl.session.namespace_key``), and for the same
        reason: chat runs every turn under the literal session id ``"chat"``
        while its tool workspace follows the grounded project, so a key made of
        the session id alone would carry one project's file text into the next.
        """
        try:
            from ..repl.session import namespace_key

            return namespace_key(ctx.session_id, ctx.workspace)
        except Exception:  # noqa: BLE001 — never let the cache break a call
            return f"{ctx.session_id}@{ctx.workspace}"

    def _ocr_capable(self, name: str) -> bool:
        """Can the tool registered under *name* transcribe a scan?

        The honest signal is the one the OCR reach point itself keys off: a
        ``router_resolver``. ``documents/tools.py`` passes exactly this into
        ``ocr_if_unreadable``, which returns immediately when it is None — so a
        tool without one CANNOT recover a scanned PDF, whatever its name says.

        Measured on a real built platform: ``read_document`` and ``extract_pdf``
        carry a resolver (``document_tools(router_resolver=...)``) and
        ``read_file`` does not (``default_registry()`` constructs it bare and
        nothing re-registers it), which is precisely why the entries must record
        this instead of assuming the three doors are interchangeable.
        """
        tool = self._tools.get(name)
        return getattr(tool, "_router_resolver", None) is not None

    def _mutates(
        self, name: str, result: ToolResult, undo_desc: "dict[str, Any] | None"
    ) -> bool:
        """Did this successful call change the filesystem under the cache?

        Three signals, none of them ``reversibility`` — see ``_MUTATING_TOOLS``
        for why undo semantics were the wrong proxy. The two objective ones come
        free: a captured undo descriptor means a mutation was about to happen,
        and ``created_paths`` means files appeared. The list catches the rest
        (``shell`` journals nothing and names nothing). Agent-authored custom
        tools count too: they run commands the registry cannot inspect.
        """
        if name in _MUTATING_TOOLS or name in self._custom:
            return True
        if undo_desc is not None:
            return True
        return bool(getattr(result, "created_paths", None))

    async def _read_cache_key(
        self, name: str, args: dict[str, Any], ctx: ToolContext
    ) -> tuple | None:
        """``(scope, resolved path, mtime_ns, size, other-args)`` or None.

        None means "do not cache and do not serve" — a missing/unstattable file,
        a call with no usable ``path``, or a path the REQUESTING tool's own gate
        refuses. The tool then runs and produces its own honest error.

        THE GATE IS PART OF THE IDENTITY (v1.174.0 review). ``read_file`` is
        workspace-only by design (``safe_path`` raises on an escape) while
        ``read_document``/``extract_pdf`` may read anywhere fs-policy allows, so
        one remembered entry can be legitimately reachable through one door and
        forbidden through another. Running the requesting tool's gate here — not
        merely at serve time — means a refused path is never served AND never
        recorded against a call that had no right to it.

        The stat runs in a THREAD, and so does the gate: it resolves paths. This
        code sits on the daemon's single event loop and the paths it is handed
        are the user's real ones — an unhydrated OneDrive file or a dead network
        share is exactly the case that turns one syscall into a frozen app
        (v1.153.1).

        The rest of the arguments join the key so a page/sheet slice never
        collides with a whole-document read; empty ones are dropped so
        ``extract_pdf(path)`` and ``read_document(path, page_range=None)`` share
        one entry.
        """
        raw = args.get("path")
        if not isinstance(raw, str) or not raw.strip():
            return None

        def _probe() -> tuple[str, int, int]:
            if name == "read_file":
                # §17 filesystem=workspace_only — the tool's own first line.
                target = safe_path(ctx.workspace, raw)
            else:
                target = Path(raw)
                if not target.is_absolute():
                    target = Path(ctx.workspace) / raw
                target = target.resolve()
                allowed, why = fs_read_ok(str(target))
                if not allowed:
                    raise PermissionError(why)
            stat = target.stat()
            return os.path.normcase(str(target)), stat.st_mtime_ns, stat.st_size

        try:
            resolved, mtime_ns, size = await asyncio.to_thread(_probe)
        except Exception:  # noqa: BLE001 — unreadable/absent: no cache identity
            return None
        extra = {
            k: v
            for k, v in args.items()
            if k != "path" and v is not None and v != ""
        }
        try:
            signature = json.dumps(extra, sort_keys=True, default=str)
        except (TypeError, ValueError):
            signature = repr(sorted(extra))
        return (self._cache_scope(ctx), resolved, mtime_ns, size, signature)

    def _current_step(self, ctx: ToolContext) -> int | None:
        """This run's step number, or None when there is no agent run (chat).

        Read from ``AgentRun.steps``, which the runtime now persists BEFORE it
        fans its tool calls out — so the number a cached read quotes back is the
        step the original read actually happened on, not the one before it.
        """
        run_id = (getattr(ctx, "agent_run_id", "") or "").strip()
        if not run_id:
            return None
        try:
            from ..core.models import AgentRun

            with session_scope(ctx.engine) as db:
                run = db.get(AgentRun, run_id)
                steps = int(getattr(run, "steps", 0) or 0) if run is not None else 0
        except Exception:  # noqa: BLE001 — the note degrades, the read does not
            return None
        return steps or None

    def _remember_read(
        self,
        key: tuple,
        tool: str,
        result: ToolResult,
        ctx: ToolContext,
        generation: int,
    ) -> None:
        """Store a successful read, unless the workspace moved under us.

        The generation check closes the concurrency hole the runtime opens on
        purpose: it gathers a turn's tool calls CONCURRENTLY, so a write can land
        while a read of the same file is in flight. If anything mutated this
        scope between the key being computed and the read finishing, the text we
        are holding may already describe a file that no longer exists in that
        form — so it is simply not remembered.
        """
        scope = key[0]
        if self._scope_gen.get(scope, 0) != generation:
            return
        text = result.output or ""
        if len(text) > _READ_CACHE_MAX_CHARS:
            return
        data = dict(result.data) if isinstance(result.data, dict) else {}
        self._read_cache[key] = {
            "output": text,
            "data": data,
            "tool": tool,
            "step": self._current_step(ctx),
            # Whether the producing call could have transcribed a scan. An
            # OCR-capable read overwrites a blind one for the same key, so the
            # entry only ever gets better.
            "ocr_capable": self._ocr_capable(tool),
        }
        self._read_cache.move_to_end(key)
        while len(self._read_cache) > _READ_CACHE_MAX_ENTRIES:
            self._read_cache.popitem(last=False)

    def _invalidate_scope(self, scope: str) -> None:
        """Forget this scope's reads after any successful mutation.

        mtime+size is the contract's staleness key and it is right almost
        always — but "almost" is not a property a file cache may have. Filesystem
        timestamp granularity is not universally sub-second (FAT and some network
        mounts round to 2s), so a rename-and-rewrite that preserves the byte
        count inside one tick would be invisible to it. Dropping the scope on any
        write costs a re-read; missing one would hand the model the OLD contents
        of a file it had just changed, which is the failure this whole feature
        exists to make less likely, not more.
        """
        self._scope_gen[scope] = self._scope_gen.get(scope, 0) + 1
        for key in [k for k in self._read_cache if k[0] == scope]:
            self._read_cache.pop(key, None)

    def _cached_result(self, entry: dict[str, Any]) -> ToolResult:
        """The remembered text plus a note that SAYS it was not re-read.

        Silence here would be the v1.153.1 mistake in a new place: output that
        looks freshly derived but is not. The note also tells the model what to
        do with the fact, because "this is cached" alone invites a re-read to
        'make sure'.
        """
        step = entry.get("step")
        source = str(entry.get("tool") or "read")
        where = f"at step {step}" if step else "earlier in this session"
        note = (
            f"\n\n[cached — already read {where} — unchanged since (same size "
            f"and modified time), so it was not read again; the text above is "
            f"the file's current content, served from that `{source}` call. "
            f"Move on rather than re-reading it.]"
        )
        data = dict(entry.get("data") or {})
        data["cached"] = True
        data["cached_from"] = source
        if step:
            data["cached_step"] = step
        return ToolResult(ok=True, output=(entry.get("output") or "") + note, data=data)

    def attach_repl(self, registry: Any) -> None:
        """Wire the session-namespace registry in (called once by platform)."""
        self._repl = registry

    def _record(
        self,
        ctx: ToolContext,
        name: str,
        args: dict,
        mode: PermissionMode,
        ok: bool,
        output: str,
        *,
        reversibility: str | None = None,
        undo: "dict[str, Any] | None" = None,
        created_paths: "list[str] | None" = None,
    ) -> str:
        """Persist the ToolInvocation (+ an UndoJournal row when an inverse was
        captured) and return the invocation id so the caller can tag its event.

        SYNC; ``invoke`` hops every call through ``asyncio.to_thread`` (v1.226.0)
        so a per-tool-call write blocked behind a long writer (VACUUM) waits on
        a worker thread instead of parking the event loop."""
        # Redact secret-bearing args BEFORE persisting — args_json is stored in the
        # DB at rest, returned by /sessions/{id}/export, and included in backups, so
        # a plaintext credential here would defeat the encrypted vault.
        tool = self._tools.get(name)
        safe_args = tool.redact_args(args) if tool is not None else args
        inv_id = new_id("tool")

        def _stamp_workspace(desc: "dict[str, Any]") -> "dict[str, Any]":
            """Record WHICH workspace the envelope's relative path is against
            (v1.166.3). POST /undo used to reconstruct the workspace from the
            Session table — and chat runs every turn as session id "chat",
            which has no row, so every chat undo resolved against a guessed
            folder (workspaces_dir/chat) and either 409'd or targeted the
            wrong tree. Chat's tool workspace also varies per turn (uploads
            vs. the grounded project), so only capture-time truth can work.
            Best-effort: an unparseable envelope is left untouched."""
            try:
                meta = json.loads(desc.get("pre_inline") or "{}")
            except (TypeError, ValueError):
                return desc
            if not isinstance(meta, dict) or meta.get("workspace"):
                return desc
            try:
                meta["workspace"] = str(Path(ctx.workspace).resolve())
            except Exception:  # noqa: BLE001 — never fail the recording
                return desc
            return {**desc, "pre_inline": json.dumps(meta)}
        record = ToolInvocation(
            id=inv_id,
            session_id=ctx.session_id,
            agent_run_id=ctx.agent_run_id,
            tool=name,
            args_json=dumps(safe_args),
            verdict=mode,
            ok=ok,
            output=output[:4000],
            reversibility=reversibility,
        )
        with session_scope(ctx.engine) as db:
            db.add(record)
            if undo:
                # The inverse descriptor (from Tool.capture_undo) is a small,
                # redaction-safe dict; the big pre-image itself is already a blob
                # ref or a small inline value inside it.
                stamped = _stamp_workspace(undo)
                db.add(
                    UndoJournal(
                        action_id=inv_id,
                        session_id=ctx.session_id,
                        agent_run_id=ctx.agent_run_id,
                        tool=name,
                        kind=str(stamped.get("kind") or ""),
                        reversible=bool(stamped.get("reversible", True)),
                        pre_ref=stamped.get("pre_ref"),
                        pre_inline=stamped.get("pre_inline"),
                        pre_sha256=stamped.get("pre_sha256"),
                        post_sha256=stamped.get("post_sha256"),
                    )
                )
            # Post-hoc creations (v1.157.0, reshaped v1.166.0). action_id is the
            # table's PRIMARY KEY (== the ToolInvocation id), so this invocation
            # owns AT MOST ONE journal row:
            #   - a capture_undo descriptor (`undo`) already holds the slot and is
            #     strictly better information (it saw the pre-image), so when one
            #     exists the created_paths are NOT journaled again — the old
            #     unconditional loop made every reversible tool with created_paths
            #     IntegrityError, rolling back the ToolInvocation + event while
            #     the file stayed on disk (an invisible write).
            #   - multiple created paths collapse into ONE `files_delete` envelope
            #     row (pre_inline {"paths": [...]}) instead of N same-key rows.
            # Best-effort — a journal problem must never turn a successful tool
            # call into a failure.
            #
            # NEVER for a REVERSIBLE tool (v1.167.0): for those, `undo is None`
            # means the capture FAILED (disk full, blob-store permission error —
            # exactly the fragile moments), and the registry's contract is that
            # a failed capture degrades to NO undo. Journaling the write as
            # "file_delete" ("created → unlink on undo") would fabricate an
            # inverse: undoing an OVERWRITE would then delete the user's only
            # remaining copy instead of refusing.
            if (
                undo is None
                and created_paths
                and reversibility != Reversibility.REVERSIBLE.value
            ):
                rels: list[str] = []
                for raw_path in created_paths:
                    try:
                        rels.append(
                            str(
                                Path(raw_path).resolve().relative_to(
                                    Path(ctx.workspace).resolve()
                                )
                            ).replace("\\", "/")
                        )
                    except Exception:  # noqa: BLE001 — outside the workspace: skip
                        continue
                desc = None
                try:
                    if len(rels) == 1:
                        desc = make_file_descriptor(
                            ctx.config.home, kind="file_delete", path=rels[0], mode="raw"
                        )
                    elif rels:
                        desc = {
                            "kind": "files_delete",
                            "reversible": True,
                            "pre_ref": None,
                            "pre_inline": dumps({"paths": rels, "mode": "raw"}),
                            "pre_sha256": None,
                            "post_sha256": None,
                        }
                except Exception:  # noqa: BLE001
                    desc = None
                if desc is not None:
                    desc = _stamp_workspace(desc)
                    db.add(
                        UndoJournal(
                            action_id=inv_id,
                            session_id=ctx.session_id,
                            agent_run_id=ctx.agent_run_id,
                            tool=name,
                            kind=str(desc.get("kind") or ""),
                            reversible=True,
                            pre_ref=desc.get("pre_ref"),
                            pre_inline=desc.get("pre_inline"),
                            pre_sha256=desc.get("pre_sha256"),
                            post_sha256=desc.get("post_sha256"),
                        )
                    )
            db.commit()
        return inv_id
