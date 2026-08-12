"""Tool Registry (§19).

Central registration, discovery, permission enforcement, execution, logging,
and event emission. Every invocation is gated by the Permission Engine and
recorded as a ToolInvocation (§19 responsibilities).
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Iterable

from ..core.db import dumps, session_scope
from ..core.events import EventType
from ..core.ids import new_id
from ..core.models import PermissionMode, ToolInvocation, UndoJournal
from .base import Reversibility, Tool, ToolContext, ToolResult
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
        "Prefer this whenever the result may be large."
    ),
}


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
        return [self._spec_with_store_as(t) for t in tools]

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
        if deny_reason:
            decision = PermissionDecision(False, PermissionMode.ASK, deny_reason)
        else:
            decision = perms.authorize(
                tool.perm_key(), args, agent_overrides, session_allow=session_allow
            )
        reversibility = getattr(tool, "reversibility", Reversibility.IRREVERSIBLE)
        rev_value = reversibility.value if isinstance(reversibility, Reversibility) else str(reversibility)

        if not decision.allowed:
            inv_id = self._record(
                ctx, name, args, decision.mode, ok=False,
                output=decision.reason, reversibility=rev_value,
            )
            await ctx.event_bus.publish(
                EventType.TOOL_DENIED,
                {"tool": name, "mode": decision.mode.value, "reason": decision.reason,
                 "invocation_id": inv_id, "reversibility": rev_value},
                session_id=ctx.session_id,
            )
            return ToolResult(ok=False, error=f"permission denied: {decision.reason}")

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

        # For a raw/binary write the capture could not predict the post-image, so
        # re-hash the file NOW (after a successful write) to arm the anti-clobber
        # guard on a future undo. Best-effort — never blocks the tool.
        if result.ok and undo_desc is not None:
            try:
                finalize_post_hash(undo_desc, ctx)
            except Exception:  # noqa: BLE001 — telemetry/guard must never break the tool
                pass

        inv_id = self._record(
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
        payload: Any = result.data if result.data not in (None, {}) else result.output
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
            # size it reports is the size of the object that actually landed —
            # not something this side guessed about a value it then shipped.
            code = (
                "import json as __ij_json\n"
                f"{name} = __ij_json.loads({encoded!r})\n"
                f"__ij_v = {name}\n"
                "print('stored as `%s` (%s%s) — reach it in the repl tool; "
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
        captured) and return the invocation id so the caller can tag its event."""
        # Redact secret-bearing args BEFORE persisting — args_json is stored in the
        # DB at rest, returned by /sessions/{id}/export, and included in backups, so
        # a plaintext credential here would defeat the encrypted vault.
        tool = self._tools.get(name)
        safe_args = tool.redact_args(args) if tool is not None else args
        inv_id = new_id("tool")
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
                db.add(
                    UndoJournal(
                        action_id=inv_id,
                        session_id=ctx.session_id,
                        agent_run_id=ctx.agent_run_id,
                        tool=name,
                        kind=str(undo.get("kind") or ""),
                        reversible=bool(undo.get("reversible", True)),
                        pre_ref=undo.get("pre_ref"),
                        pre_inline=undo.get("pre_inline"),
                        pre_sha256=undo.get("pre_sha256"),
                        post_sha256=undo.get("post_sha256"),
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
            if undo is None and created_paths:
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
