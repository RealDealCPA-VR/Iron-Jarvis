"""The persistent Python REPL tool — context protection from the other end.

Tool output floods the model's context window, and this app has spent three
releases attacking that from the CONSUMING end: a per-result character cap,
stale-output trimming, a token budget, then model-written compaction. Every one
of those decides what to THROW AWAY after the flood already happened.

This is the other end. A REPL session keeps a live Python namespace per session,
so a result stops being 40k characters of tool output and becomes a VARIABLE the
model manipulates by reference. Only what the code ``print()``s ever enters the
context. Fetch once, bind it, then print ``len(rows)`` and ``rows[:3]``.

The tool itself is a thin, honest shell over ``repl/session.py``'s registry:
it resolves this session's namespace, runs the code, and shapes the result so
nothing is silently lost —

* a TRUNCATED capture says so (this repo's rule: a silently shortened result
  reads as complete, and the model then reasons from a partial picture),
* a RESTARTED namespace says plainly that the variables are gone (otherwise the
  next call's ``NameError`` looks like a bug in the model's own reasoning),
* an exception comes back as the REAL traceback, verbatim — a summarised
  traceback cannot be debugged.

Files the code creates in the workspace are detected by diffing a bounded walk
taken before and after the run, and reported in ``ToolResult.created_paths``
(absolute), which is what makes a created file show up in the run's result card
and the preview rail.
"""

from __future__ import annotations

import asyncio
import inspect
import os
import time
from pathlib import Path
from typing import Any, Callable

from .base import Reversibility, Tool, ToolContext, ToolResult

#: Seconds a single ``repl`` call may run before the session interrupts it.
DEFAULT_TIMEOUT_S = 30.0
#: Ceiling for a caller-supplied ``timeout_s`` (matches run_code's _MAX_TIMEOUT).
MAX_TIMEOUT_S = 300.0
#: Extra seconds we wait on the session beyond its own timeout before giving up
#: ourselves. The session owns interruption; this is only a backstop so a wedged
#: worker can never hang the agent loop forever.
_TIMEOUT_GRACE_S = 15.0

#: Ceilings for the created-file diff. Bounded and cheap by construction: the
#: daemon is ONE asyncio loop, and an unbounded workspace walk on it is the
#: exact defect that once presented as "Daemon offline" for four hours.
MAX_CREATED_PATHS = 50
_MAX_SCAN_ENTRIES = 5000
_SCAN_DEADLINE_S = 3.0

#: Never walked when diffing the workspace: huge, machine-generated, and never
#: what the user means by "the file it created".
_SKIP_DIRS = frozenset({
    ".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv",
    ".next", ".turbo", "dist", "build", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", "site-packages",
})

#: Attribute names tried, in order, to get this session's namespace out of the
#: registry (``ReplRegistry`` maps session_id -> ReplSession). Tolerating a few
#: spellings keeps the tool from being welded to one accessor name.
_SESSION_ACCESSORS = ("session", "get", "for_session", "get_session", "ensure")

_TRUNCATED_NOTE = (
    "[output TRUNCATED — what you see above is PARTIAL, not the whole result. "
    "Do not reason from it as if it were complete: print less next time "
    "(len(), a slice, a summary) or narrow what you print.]"
)
_RESTARTED_NOTE = (
    "[the Python namespace was RESET before this ran — the REPL restarted, so "
    "every variable, import and function from earlier calls is GONE. Re-create "
    "anything you still need.]"
)
_NO_OUTPUT_NOTE = (
    "(the code ran and printed nothing — state is kept, so print() what you "
    "want to see)"
)


class ReplTool(Tool):
    name = "repl"
    permission_key = "repl"
    # A REPL runs arbitrary Python in-process: it can write files, call the
    # network, spend money. Exactly run_code's reasoning — "a script can do
    # anything" — so it declares the same fail-safe contract and never offers a
    # fake "undone". (Files it creates ARE journaled via created_paths, so the
    # session-level revert can still remove those.)
    reversibility = Reversibility.IRREVERSIBLE
    returns_untrusted_content = True  # its output may echo file/web text
    # This text is the feature. It is also charged on EVERY request, so it says
    # the four things a model cannot infer and stops: state persists, only
    # print() returns, summarise instead of dumping, and `_store_as` values are
    # already bound here. Test-pinned, including the length.
    description = (
        "Run Python in a namespace that PERSISTS for this whole session: "
        "variables, imports and functions from one call are still there in the "
        "next, so build the work up in steps instead of re-deriving it.\n"
        "ONLY WHAT YOU print() COMES BACK TO YOU — that is the point. Keep the "
        "big thing in a variable and print something small about it: "
        "`rows = json.load(open(p))`, then `print(len(rows), rows[0].keys())` "
        "or `print(rows[:3])`. Never print a whole file, table, DataFrame or "
        "API response; you can always print more next call, but you cannot "
        "un-flood your context.\n"
        "Values other tools saved with `_store_as` are ALREADY bound in this "
        "namespace under that name — use them, do not re-fetch.\n"
        "Write files into your WORKING DIRECTORY (the user's project when one "
        "is selected); writes outside it are refused and the refusal names the "
        "folder. Files you write are reported back with absolute paths, and a "
        "raise comes back as the real traceback. Prefer this over run_code "
        "when the result is worth keeping."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": (
                    "Python to run in this session's persistent namespace. "
                    "print() only what you need to see."
                ),
            },
            "timeout_s": {
                "type": "number",
                "description": (
                    f"Seconds before the run is interrupted "
                    f"(default {DEFAULT_TIMEOUT_S:g}, max {MAX_TIMEOUT_S:g})."
                ),
            },
        },
        "required": ["code"],
    }

    def __init__(self, registry: Any = None) -> None:
        #: The ``ReplRegistry`` (or a zero-arg callable returning one). The
        #: platform wires this as ``platform.repl``; when it is absent the tool
        #: also looks for ``ctx.repl``, and failing that says honestly that the
        #: REPL is unavailable rather than pretending to have run anything.
        self._registry = registry

    # -- plumbing ----------------------------------------------------------

    def _resolve_registry(self, ctx: ToolContext) -> Any:
        reg = self._registry
        if reg is None:
            reg = getattr(ctx, "repl", None)
        if callable(reg) and not hasattr(reg, "execute") and not any(
            hasattr(reg, name) for name in _SESSION_ACCESSORS
        ):
            reg = reg()  # a late-bound provider, so boot order can't matter
        return reg

    @staticmethod
    def _registry_call(
        registry: Any, session_id: str, code: str, workspace: Path, timeout: float
    ) -> Any:
        """The registry-level entry point, when there is one.

        ``ReplRegistry.execute(session_id, code, *, workspace, timeout)`` is the
        SANCTIONED door: it creates the namespace on demand and enforces the
        session cap. Going through ``get()`` instead would work only for a
        session that already exists — i.e. never on the first call — so the
        dispatcher prefers this whenever the signature takes a ``session_id``.
        Returns a zero-arg INVOKER (so nothing runs before the pre-run workspace
        snapshot is taken), or None when this registry isn't shaped that way and
        the per-session path should be used.
        """
        fn = getattr(registry, "execute", None)
        if not callable(fn):
            return None
        try:
            params = inspect.signature(fn).parameters
        except (TypeError, ValueError):  # builtins/mocks: can't introspect
            return None
        if "session_id" not in params:
            return None
        kwargs: dict[str, Any] = {}
        if "workspace" in params:
            kwargs["workspace"] = workspace
        if "timeout" in params:
            kwargs["timeout"] = timeout
        elif "timeout_s" in params:
            kwargs["timeout_s"] = timeout
        return lambda: fn(session_id, code, **kwargs)

    async def _resolve_session(self, registry: Any, session_id: str) -> Any:
        """This session's ReplSession, or None when the registry has no such
        thing. Never raises for a merely-missing session — the caller turns
        that into an honest tool error."""
        for name in _SESSION_ACCESSORS:
            accessor = getattr(registry, name, None)
            if not callable(accessor):
                continue
            sess = accessor(session_id)
            if inspect.isawaitable(sess):
                sess = await sess
            if sess is not None and hasattr(sess, "execute"):
                return sess
        try:
            sess = registry[session_id]  # mapping-shaped registry
        except (TypeError, KeyError, LookupError):
            sess = None
        if sess is not None and hasattr(sess, "execute"):
            return sess
        # Last resort: the registry IS the executor (single-namespace builds).
        if hasattr(registry, "execute"):
            return registry
        return None

    # -- workspace diff ----------------------------------------------------

    @staticmethod
    def _scan(base: Path) -> "tuple[set[str], bool]":
        """Bounded set of file paths under *base*. Returns ``(paths, complete)``.

        ``os.walk`` rather than ``rglob`` because only os.walk can PRUNE, and
        pruning ``node_modules`` is the expensive half of the problem. Runs in a
        worker thread (see ``_snapshot``) — never on the event loop.
        """
        started = time.monotonic()
        out: set[str] = set()
        try:
            root = str(base.resolve())
        except OSError:
            return out, False
        for dirpath, dirs, files in os.walk(root, followlinks=False):
            dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
            for fn in files:
                out.add(os.path.join(dirpath, fn))
                if len(out) >= _MAX_SCAN_ENTRIES:
                    return out, False
            if time.monotonic() - started >= _SCAN_DEADLINE_S:
                return out, False
        return out, True

    async def _snapshot(self, base: Path) -> "tuple[set[str], bool]":
        try:
            return await asyncio.to_thread(self._scan, base)
        except Exception:  # noqa: BLE001 — bookkeeping never breaks the run
            return set(), False

    @staticmethod
    def _created(
        before: "set[str]", after: "set[str]", workspace: Path
    ) -> "tuple[list[str], bool]":
        """New files, absolute, capped. Returns ``(paths, capped)``.

        Only paths that really sit under the workspace survive: a symlink the
        code followed out of the tree is not this session's artifact, and a
        preview rail pointed at ``C:/Windows`` would be worse than no rail.
        """
        try:
            root = workspace.resolve()
        except OSError:
            return [], False
        fresh: list[str] = []
        for raw in sorted(after - before):
            path = Path(raw)
            try:
                if not path.is_file():
                    continue
                resolved = path.resolve()
                if resolved != root and not resolved.is_relative_to(root):
                    continue
            except OSError:
                continue
            fresh.append(str(resolved))
        capped = len(fresh) > MAX_CREATED_PATHS
        return fresh[:MAX_CREATED_PATHS], capped

    async def _diff(
        self, before: "set[str]", after: "set[str]", workspace: Path
    ) -> "tuple[list[str], bool]":
        """``_created``, off the loop. NOT a formality: the diff can be up to
        ``_MAX_SCAN_ENTRIES`` paths and it calls ``is_file()`` and ``resolve()``
        on each one, which is the exact shape of the v1.153.1 outage — the
        MainThread parked in ``pathlib.is_file`` inside a tool, reaching the user
        as "Daemon offline" for four hours."""
        try:
            return await asyncio.to_thread(self._created, before, after, workspace)
        except Exception:  # noqa: BLE001 — bookkeeping never breaks the run
            return [], False

    # -- execute -----------------------------------------------------------

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        code = str(args.get("code") or "")
        if not code.strip():
            return ToolResult(ok=False, error="code is required")
        try:
            timeout = float(args.get("timeout_s") or DEFAULT_TIMEOUT_S)
        except (TypeError, ValueError):
            timeout = DEFAULT_TIMEOUT_S
        timeout = min(max(timeout, 1.0), MAX_TIMEOUT_S)

        try:
            registry = self._resolve_registry(ctx)
        except Exception as exc:  # noqa: BLE001 — never crash the agent loop
            return ToolResult(
                ok=False, error=f"repl unavailable: {type(exc).__name__}: {exc}"
            )
        if registry is None:
            return ToolResult(
                ok=False,
                error=(
                    "the persistent Python REPL is not available in this build "
                    "— use run_code for a one-off script instead"
                ),
            )

        session_id = str(getattr(ctx, "session_id", "") or "")
        workspace = Path(getattr(ctx, "workspace", ".") or ".")

        try:
            invoke = self._registry_call(
                registry, session_id, code, workspace, timeout
            )
            if invoke is None:
                session = await self._resolve_session(registry, session_id)
                if session is None:
                    return ToolResult(
                        ok=False,
                        error=(
                            "no REPL namespace for session "
                            f"{session_id or '(unknown)'}"
                        ),
                    )

                def invoke(_s: Any = session) -> Any:
                    return _s.execute(code, timeout)
        except Exception as exc:  # noqa: BLE001 — a broken registry is an error,
            return ToolResult(  # not a crash
                ok=False,
                error=f"repl session lookup failed: {type(exc).__name__}: {exc}",
            )

        # Taken BEFORE anything runs, so the created-file diff is honest.
        before, before_complete = await self._snapshot(workspace)

        try:
            call = invoke()
            if inspect.isawaitable(call):
                payload = await asyncio.wait_for(
                    call, timeout=timeout + _TIMEOUT_GRACE_S
                )
            else:
                payload = call
        except asyncio.TimeoutError:
            return ToolResult(
                ok=False,
                error=(
                    f"the REPL did not return within {timeout:g}s (+grace). The "
                    f"namespace may still be busy; keep the next call small."
                ),
            )
        except Exception as exc:  # noqa: BLE001 — registry/session blew up
            return ToolResult(
                ok=False, error=f"repl execution failed: {type(exc).__name__}: {exc}"
            )
        if not isinstance(payload, dict):
            return ToolResult(
                ok=False,
                error=(
                    "the REPL returned an unusable result "
                    f"({type(payload).__name__}) — nothing was run"
                ),
            )

        return await self._shape(payload, workspace, before, before_complete)

    async def _shape(
        self,
        payload: dict[str, Any],
        workspace: Path,
        before: "set[str]",
        before_complete: bool,
    ) -> ToolResult:
        ok = bool(payload.get("ok"))
        stdout = str(payload.get("stdout") or "")
        stderr = str(payload.get("stderr") or "")
        value = str(payload.get("result") or "")
        error = str(payload.get("error") or "")
        truncated = bool(payload.get("truncated"))
        restarted = bool(payload.get("restarted"))

        created: list[str] = []
        capped = False
        scan_skipped = False
        if ok:
            after, after_complete = await self._snapshot(workspace)
            if before_complete and after_complete:
                created, capped = await self._diff(before, after, workspace)
            else:
                # A truncated scan cannot tell "new" from "never seen", and
                # naming a pre-existing file as CREATED is a lie the preview
                # rail would repeat. Say nothing, and say that we said nothing.
                scan_skipped = True

        parts: list[str] = []
        if restarted:
            parts.append(_RESTARTED_NOTE)
        if stdout.strip():
            parts.append(stdout.rstrip())
        if stderr.strip():
            parts.append("[stderr]\n" + stderr.rstrip())
        if value.strip():
            parts.append("[result] " + value.strip())
        if ok and not stdout.strip() and not stderr.strip() and not value.strip():
            parts.append(_NO_OUTPUT_NOTE)
        if truncated:
            parts.append(_TRUNCATED_NOTE)
        if created:
            listed = "\n".join(f"- {p}" for p in created)
            parts.append(f"Files created in the workspace:\n{listed}")
            if capped:
                parts.append(
                    f"[only the first {MAX_CREATED_PATHS} created files are "
                    f"listed — more were written]"
                )
        elif scan_skipped:
            parts.append(
                "[created-file detection skipped: the workspace is too large "
                "to diff, so any files this code wrote are NOT listed here]"
            )
        body = "\n".join(p for p in parts if p).strip()

        data: dict[str, Any] = {
            "truncated": truncated,
            "restarted": restarted,
            "created": created,
        }
        if not ok:
            # The runtime shows ONLY `error` for a failed tool call, so the
            # traceback goes there VERBATIM — never summarised, never clipped —
            # with whatever was printed before the raise kept alongside it, or
            # it is lost. That traceback is the entire debugging surface.
            tail = error or stderr or "the code raised, but no traceback was captured"
            return ToolResult(
                ok=False,
                output=body,
                error=(body + "\n" + tail).strip() if body else tail,
                data=data,
            )
        return ToolResult(
            ok=True,
            output=body,
            data=data,
            created_paths=created or None,
        )


def repl_tools(registry: Any = None) -> "list[Tool]":
    """Factory used by the platform wiring (``platform.repl`` is the registry)."""
    return [ReplTool(registry)]


#: Kept so a lazily-built registry can be handed over as a callable.
RegistryProvider = Callable[[], Any]
