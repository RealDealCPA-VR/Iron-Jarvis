"""`tool_script` — the model writes ONE script that calls its tools itself.

Without it, every tool call is a round trip: the model asks, the tool runs,
the whole result is pasted into the model's context, the model reads it and
asks for the next. Forty workbooks is forty model turns and forty results in
the window. With it, the model writes a short Python script; the script calls
the tools, keeps what they return in variables, and only what it ``print()``s
comes back.

THE SCRIPT RUNS IN AN ISOLATED WORKER (``repl/session.py``'s ``isolated``
namespace, ``repl/worker.py``'s ``ENV_ISOLATED``):

* its own process, with an environment that holds no key or token;
* no network, no other program, no registry change, no file written outside
  a private scratch folder, files read only inside the turn's working folder;
* Windows itself holds "no other program", a memory ceiling and "no clipboard
  or desktop" on the process (a Job — ``core.procs.restrict``), so those hold
  even against code that gets around the worker's own checks. The network
  and file rules are the worker's own checks: a guardrail against a careless
  or misled model, not a sandbox against hostile code. Said plainly here
  because the Handbook says it too.

SO THE ONLY WAY A SCRIPT ACTS IS BY CALLING A TOOL, and every such call is
judged here (:meth:`ToolScriptTool._refusal`) and then goes through
``ToolRegistry.invoke`` with the calling turn's own roster, overrides, grants
and deadline (``registry.current_call_scope``) — the same gate, the same
ledger row, the same events as a call the model makes directly. On top of
that gate a script may call ONLY a tool that

* is one of this turn's tools (the roster),
* only reads (``Reversibility.READONLY``),
* would run without asking (its permission resolves to allow, it has no
  spend check that asks, it is not held back for a look),
* and, in a Local only chat, does not reach outside this PC.

Anything else is refused inside the script with a sentence that says to call
it directly, where the user can be asked. A script is never a way around an
approval card.

WHAT A SCRIPT HAS READ STAYS WHERE IT WAS READ (v1.473.0). A script could
read a client's files and call `web_fetch` in the same loop, up to 300 times,
with nobody looking at any one call. So the app keeps, per worker, WHERE the
private data its scripts hold came from ("this PC", or one connected app),
and a call to a tool that reaches outside this PC is refused once the worker
holds data from anywhere else. Public web pages are not private: fetching
first and reading files after is fine; so is a loop over one app's own data.
The record lives on the worker's session (``ReplSession.script_origins``) and
is emptied when the worker is replaced, because the variables go with it. The
child reports its own direct file reads; the tool reads are the app's record.

MORE THAN ONE CALL AT A TIME (v1.473.0). ``tools.map(name, items)`` sends its
calls together. The ones that are safe to run side by side
(:data:`MAP_TOGETHER`, the chat lanes' own list) run :data:`MAP_PARALLEL` at
a time ONCE A CALL HAS BEEN SEEN TO WAIT (a web page, a scan read in its own
process); calls that are quick, or that compute in this process (parsing a
workbook or a PDF), run one after another, because side by side they were
measured no faster or slower (see ``on_batch``). Each still goes through the
same gate and gets its own ledger row; a failed item is a failed RESULT,
never an exception that stops the rest.

RESULTS GO TO A FILE WITHOUT BEING RETYPED (v1.473.0). ``tools.keep(name,
value)`` hands a value to the app (``tools/script_values.py``); the model
then calls the file tool itself and names the value (``_from_script``).
``tools.plan(tool, ...)`` notes a CHANGE for later: nothing runs, the list is
kept as ``changes``, and ``apply_changes`` runs it after ONE approval.

A SAVED SCRIPT (v1.473.0). ``saved="<skill name>"`` runs the script a skill
was saved with (``skills/saved_scripts.py``) instead of new code, with
``params`` bound as a dict. Same worker, same gate, same rules.

NO COST THE USER NOTICES. The worker is started ahead of time, the moment the
tool is offered for a turn (:func:`prewarm`), and stays up for the session; a
call crosses one local pipe (well under a millisecond, measured) and then
runs exactly as a direct call does. There is no before/after scan of the
working folder (the script cannot write there).
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from pathlib import Path
from typing import Any

from ..core import progress
from .base import Reversibility, RiskClass, Tool, ToolContext, ToolResult

log = logging.getLogger("iron_jarvis.tools.tool_script")

NAME = "tool_script"

#: Seconds a script's OWN work may take (time spent waiting on a tool is the
#: tool's, bounded by the tool's deadline).
DEFAULT_TIMEOUT_S = 60.0
MAX_TIMEOUT_S = 300.0
#: The most a whole script may take, its tool calls included (v1.471.0).
#: The registry no longer cuts a script at ONE call's deadline
#: (``Tool.bounds_itself``): forty slow reads, each inside that deadline,
#: were stopped at minute ten. Each call still has that deadline.
MAX_TOTAL_S = 3600.0

#: Tool names listed in the footer and the result data.
_MAX_LISTED = 12

#: How many of a `tools.map`'s calls run at the same time.
MAP_PARALLEL = 4
#: A call quicker than this gains nothing from running beside others (see
#: ``on_batch``): `tools.map` runs such calls in order.
MAP_QUICK_S = 0.05
#: A call WAITED (and is worth running beside others) when this process spent
#: less than this share of the call's time on its own processor.
MAP_WAIT_SHARE = 0.5

#: The tools whose calls may run side by side inside a `tools.map`: the chat
#: lanes' own list of readers that are safe together
#: (``chat_turn.CONCURRENT_READ_TOOLS``; a test holds this a subset of it).
#: Any other tool in a map runs one call after another.
MAP_TOGETHER: frozenset[str] = frozenset({
    "web_search", "web_fetch", "read_document", "read_file", "list_files",
    "file_search", "memory_search", "memory_read", "ltm_search", "recall",
    "guide_search", "guide_read", "app_search", "app_status", "grep", "glob",
})

#: Public web content: reaching it is "outside", what comes back is not
#: private.
_WEB_READERS: frozenset[str] = frozenset({"web_search", "web_fetch", "web_extract"})

#: Tools that only read but do not declare ``Reversibility.READONLY`` (their
#: class predates the field, or keeps the fail-safe default). Named one by
#: one: each was read before it joined, and each changes nothing. The web
#: ones reach outside, so a Local only chat keeps them out (the gate below).
EXTRA_READERS: frozenset[str] = frozenset({
    "web_fetch", "web_search", "web_extract", "file_search", "image_info",
    "memory_search", "memory_read", "recall", "recall_lessons",
    "guide_search", "guide_read", "app_search", "app_status",
})

#: Read-only by their own declaration, and still never called from a script:
#: each one talks to the user, shows a card, starts a model or another agent,
#: opens a page, or waits. A script has nobody to show anything to.
NOT_FROM_A_SCRIPT: frozenset[str] = frozenset({
    "ask_user", "exit_plan_mode", "consult", "config_secret", "plan_update",
    "page_open", "page_read", "page_screenshot", "web_look", "pane_wait",
    "view_image", "excel_live_look",
    # v1.479.0: a lookup brings tools into the reply; a script must not make
    # them (its nested calls are not the turn's own).
    "find_capability",
})

_NO_OUTPUT_NOTE = (
    "(the script ran and printed nothing. Only what it print()s comes back: "
    "print the answer you need.)"
)
_TRUNCATED_NOTE = (
    "[output TRUNCATED: what you see above is PARTIAL. Print less: a count, "
    "a total, the few rows that matter.]"
)
_RESTARTED_NOTE = (
    "[the script's worker was started again before this ran, so variables "
    "from earlier scripts are GONE. Re-create anything you still need.]"
)


#: Readers of a MEMORY BASE (v1.473.0 review). A base can be a notes folder
#: on this PC or live off it (Notion, a memory server): the QUERY goes there,
#: so with a base that is not local these reach outside like any app's tool.
_MEMORY_BASE_READERS: frozenset[str] = frozenset({"ltm_search", "brain_search", "brain_read"})
_MEMORY_ORIGIN = "outside:memory base"

#: `web_extract` reads the page the browser has OPEN (v1.473.0 review): it
#: may be a signed-in page of the user's, so what it returns is private and
#: must not ride a later web call the way a public page may.
_BROWSER_PAGE_READERS: frozenset[str] = frozenset({"web_extract"})
_BROWSER_PAGE_ORIGIN = "outside:browser page"

#: The tool that reads ONE connected app's resource (`server` names the app).
_RESOURCE_READER = "mcp_read_resource"

#: Argument names that carry a file or folder path.
_PATH_KEY = re.compile(
    r"(?i)(?:^|_)(?:path|paths|file|files|folder|folders|dir|dirs|directory|root|roots|cwd|"
    r"source|src|workbook|document|input|output)(?:_|$)"
)
#: `\\host\share` written with backslashes, wherever it appears. The host
#: must read as a host name or an address (a search pattern such as
#: `\\d+\.\d+` starts with two backslashes too).
_UNC = re.compile(r"^\\\\[A-Za-z0-9][A-Za-z0-9._@-]{0,253}\\[^\\/\s]")


def network_path(value: Any, key: str = "") -> bool:
    """Is *value* spelled as a place on ANOTHER computer (``\\\\host\\share``,
    ``//host/share`` in a path argument, ``\\\\?\\UNC\\…``, a device path)?
    Read from the TEXT only: nothing is resolved or opened (opening is the
    harm: Windows connects to the host and offers it the user's sign-in)."""
    if not isinstance(value, str):
        return False
    text = value.strip()
    if len(text) < 3:
        return False
    backslashed = text.replace("/", "\\")
    if not backslashed.startswith("\\\\"):
        return False
    if re.match(r"^\\\\[?.]\\[A-Za-z]:(\\|$)", backslashed):
        return False  # a local drive behind a long-path prefix
    if key and _PATH_KEY.search(key):
        return True
    return bool(_UNC.match(text)) or backslashed.lower().startswith("\\\\?\\unc\\")


def _network_path_in(args: Any, workspace: Any) -> str:
    """The first network path a call's arguments carry, cut for a sentence;
    "" for none. A path inside the turn's own working folder is not one (a
    firm whose client folders live on a share works there all day)."""
    try:
        home = str(workspace or "").replace("/", "\\").rstrip("\\").lower()
    except Exception:  # noqa: BLE001
        home = ""
    on_share = home.startswith("\\\\")
    seen = 0

    def walk(value: Any, key: str, depth: int) -> str:
        nonlocal seen
        if seen > 400 or depth > 3:
            return ""
        if isinstance(value, str):
            seen += 1
            if not network_path(value, key):
                return ""
            spelled = value.strip().replace("/", "\\").lower()
            if on_share and (spelled == home or spelled.startswith(home + "\\")):
                return ""
            return value.strip()[:80]
        if isinstance(value, dict):
            for k, v in list(value.items())[:60]:
                found = walk(v, str(k), depth + 1)
                if found:
                    return found
        elif isinstance(value, (list, tuple)):
            for v in list(value)[:200]:
                found = walk(v, key, depth + 1)
                if found:
                    return found
        return ""

    return walk(args, "", 0)


def _origin_words(origin: str) -> str:
    if origin == "local":
        return "files or data on this PC"
    if origin == _MEMORY_ORIGIN:
        return "your memory base"
    if origin == _BROWSER_PAGE_ORIGIN:
        return "a page open in the browser"
    if origin.startswith("pack:"):
        return f"the app {origin[5:]}"
    if origin.startswith("outside:"):
        return f"`{origin[8:]}`"
    return origin


def _cpu_s() -> float:
    """Seconds of processor time this process has used (every thread). A
    seam: a test says "this call computed" or "this call waited" with it."""
    return time.process_time()


def _wall_s() -> float:
    """The clock a call's length is read from. A seam: a test says how long
    a call took, so "quick" never depends on how loaded the PC is."""
    return time.perf_counter()


def _linked_chat(turn_id: str) -> str:
    """The saved chat a turn was linked to ("" when not yet, or unknown)."""
    if not turn_id:
        return ""
    try:
        from ..providers.egress import linked_thread

        return str(linked_thread(turn_id) or "")
    except Exception:  # noqa: BLE001 — the turn's own key then
        return ""


def worker_key(ctx: Any) -> tuple[str, bool]:
    """``(key, once)``: which isolated worker a call's script runs in.

    An agent run or a coding session has an id of its own, and that is the
    key. Every CHAT turn runs under the one id "chat", so keying on it would
    put every chat's script variables in one process: rows a Local only chat
    loaded would be readable by a script in another chat answered by a cloud
    model. A chat is therefore keyed by its saved thread, else by the turn
    (a new chat's first message). With neither (a headless caller), the
    worker is used once and thrown away (``once``)."""
    sid = str(getattr(ctx, "session_id", "") or "")
    if sid != "chat":
        return sid, False
    own = str(getattr(ctx, "thread_id", "") or "")
    if not own:
        turn = str(getattr(ctx, "turn_id", "") or "")
        # A NEW chat's first message: once the page has linked the turn to
        # its saved chat, the chat's name is the key (v1.473.0 review). The
        # link usually lands BEFORE the first script runs; keyed by the turn
        # all the same, that script's variables and kept values sat under a
        # name the chat's next message never looked at.
        own = _linked_chat(turn) or turn
    if own:
        return f"chat:{own}", False
    import uuid

    return f"chat:once-{uuid.uuid4().hex}", True


class ToolScriptTool(Tool):
    name = NAME
    permission_key = NAME
    # It can change nothing by itself (see the module docstring) and may call
    # only tools that read, so it is a read.
    reversibility = Reversibility.READONLY
    risk_class = RiskClass.READ
    returns_untrusted_content = True  # it prints what the tools it called read
    # Each call it makes has the per-call deadline; the script has its own
    # overall bound (MAX_TOTAL_S) instead of one call's.
    bounds_itself = True
    description = (
        "Run ONE short Python script that calls this turn's read-only tools "
        "itself, so many calls take one step and their results never fill "
        "your context. Use it when a tool is needed many times (every file in "
        "a folder, each client, each sheet) or when results must be filtered, "
        "joined or added up before you look at them.\n"
        "In the script: `r = tools.call(\"read_document\", path=p)` (or "
        "`tools.read_document(path=p)`) gives `r.output` (the text) and "
        "`r.data`; a refused or failed call raises `ToolError`.\n"
        "ONE TOOL, MANY TIMES: `rs = tools.map(\"read_document\", "
        "[{\"path\": p} for p in paths])` makes every call in one step "
        "(several at a time when the calls wait, such as web pages) "
        "and returns one result each, in order. A failed call does not stop "
        "the rest: check `r.ok`, read `r.error`. "
        "`tools.names` lists what the script may call: tools that only read. "
        "A tool that changes a file, sends something or needs the user's "
        "approval must be called directly, not from a script.\n"
        "ONLY WHAT THE SCRIPT print()s COMES BACK. Print the answer (a "
        "count, a total, the rows that matter), never the raw results.\n"
        "EVERY FIGURE COMES FROM THE SCRIPT: work out each number you will "
        "say or write (a grand total, a count, an average) in the script "
        "and print it. Never add or combine its results yourself "
        "afterwards.\n"
        "A RESULT FOR A FILE: do not print it and type it again. "
        "`tools.keep(\"totals\", rows)` in the script, then call the file "
        "tool yourself with `_from_script`, e.g. write_document(path="
        "\"totals.xlsx\", _from_script={\"content\": \"totals\"}).\n"
        "The script runs isolated: no network, no other programs, no file "
        "writes, files read only in the working folder (reach everything "
        "else through a tool), and only Python's standard library to "
        "import (csv, json, statistics, decimal, datetime; no pandas, no "
        "numpy). Once it has read this PC's files or data it "
        "cannot call a tool that reaches outside this PC. Variables persist "
        "between scripts. `saved` runs a skill's saved script instead of "
        "`code`, with `params`."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": (
                    "Python. Call tools with tools.call(name, **args); "
                    "print() only what you need to see."
                ),
            },
            "timeout_s": {
                "type": "number",
                "description": (
                    f"Seconds the script's own work may take, not counting the "
                    f"tools it waits on (default {DEFAULT_TIMEOUT_S:g}, max "
                    f"{MAX_TIMEOUT_S:g})."
                ),
            },
            "saved": {
                "type": "string",
                "description": (
                    "Instead of `code`: the name of a skill that was saved "
                    "with a script. Its script runs."
                ),
            },
            "params": {
                "type": "object",
                "description": "With `saved`: values the saved script reads as `params`.",
            },
        },
        "required": [],
    }

    #: What the description gains when `apply_changes` is one of the turn's
    #: tools too (``ToolRegistry.specs`` adds it): a sentence about a tool the
    #: model cannot call would send it into a refusal.
    PLAN_SENTENCE = (
        "\nTO CHANGE MANY FILES: a script changes nothing, but it can plan. "
        "`tools.plan(\"rename_file\", path=p, new_path=n)` for each change, "
        "print a short summary, then call `apply_changes` yourself with "
        "_from_script={\"changes\": \"changes\"}: the user approves all of "
        "it once and each change can be undone."
    )

    def __init__(self, repl_registry: Any, tool_registry: Any) -> None:
        self._repl = repl_registry
        self._registry = tool_registry

    # -- which tools a script may call ----------------------------------------

    def _fixed_refusal(self, name: str, tool: Any) -> str:
        """Why *name* can never be called from a script, or "". Facts of the
        tool itself: the same answer for every turn."""
        if tool is None:
            return f"there is no tool named `{name}`"
        if name == NAME:
            return "a script cannot start another script"
        if name in NOT_FROM_A_SCRIPT:
            return (
                f"`{name}` shows the user something or waits for them, so it "
                "cannot be called from a script. Call it directly"
            )
        if (
            name not in EXTRA_READERS
            and getattr(tool, "reversibility", Reversibility.IRREVERSIBLE) != Reversibility.READONLY
        ):
            return (
                f"`{name}` changes something or acts outside this script, so it "
                "cannot be called from a script. Call it directly, where the "
                "user can be asked"
            )
        if getattr(tool, "deadline_exempt", False):
            return f"`{name}` waits for a person or another agent; call it directly"
        if getattr(tool, "quarantined", False):
            return (
                f"`{name}` is new in its app and is held for the user's first "
                "look; call it directly so they can approve it"
            )
        return ""

    def callable_names(self, scope: Any) -> list[str]:
        """The tools a script of THIS call may reach: the turn's roster, cut
        to the ones that pass :meth:`_fixed_refusal`. A caller that named no
        roster offers none (nothing here guesses one)."""
        allowed = getattr(scope, "allowed_names", None)
        if not allowed:
            return []
        out: list[str] = []
        for name in sorted(allowed):
            try:
                tool = self._registry.get(name)
            except Exception:  # noqa: BLE001
                tool = None
            if tool is not None and not self._fixed_refusal(name, tool):
                out.append(name)
        return out

    async def _refusal(
        self, name: str, args: dict[str, Any], scope: Any, ctx: ToolContext
    ) -> str:
        """Why this one call is not run, or "". Fail-closed: a check that
        cannot be made refuses."""
        try:
            tool = self._registry.get(name)
        except Exception:  # noqa: BLE001
            tool = None
        fixed = self._fixed_refusal(name, tool)
        if fixed:
            return fixed
        allowed = getattr(scope, "allowed_names", None)
        if not allowed or name not in allowed:
            return f"`{name}` is not one of this turn's tools"
        try:
            from ..providers import egress

            if egress.current_scope().on and (
                name in egress.INTERNET_TOOLS or egress.reaches_outside(tool, self._registry)
            ):
                return (
                    f"this chat is Local only and `{name}` reaches outside this "
                    "PC. Call it directly, so the user can allow that one call"
                )
            perms = getattr(scope, "perms", None)
            key = tool.perm_key()
            # v1.479.0 (review): an "ask" rule of THIS reply holds here
            # whatever the tool's tier says. `mode_for` upgrades an ask on the
            # read-only web tools to allow (so headless researchers can
            # search), which let a web tool a LOOKUP brought as ask-first run
            # from a script with no card.
            rules = getattr(scope, "agent_overrides", None) or {}
            if "ask" in (str(rules.get(name) or ""), str(rules.get(key) or "")):
                granted = getattr(scope, "session_allow", None) or ()
                if key not in granted and name not in granted:
                    return (
                        f"`{name}` asks the user first. Call it directly, not "
                        "from a script"
                    )
            mode = getattr(perms.mode_for(key, getattr(scope, "agent_overrides", None)), "value", "ask")
            if mode == "deny":
                return f"`{name}` is set to never run here"
            if mode != "allow":
                granted = getattr(scope, "session_allow", None) or ()
                if key not in granted and name not in granted:
                    return (
                        f"`{name}` asks the user first. Call it directly, not "
                        "from a script"
                    )
            check = await tool.spend_check(args, ctx)
            if check is not None and getattr(check, "ask", False):
                return (
                    f"`{name}` asks the user before this call. Call it "
                    "directly, not from a script"
                )
        except Exception as exc:  # noqa: BLE001 — cannot tell: do not run it
            log.debug("tool_script gate failed for %s", name, exc_info=True)
            return f"`{name}` could not be checked ({type(exc).__name__}), so it was not run"
        return ""

    # -- execute ---------------------------------------------------------------

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        code = str(args.get("code") or "")
        saved = str(args.get("saved") or "").strip()
        variables: "dict[str, Any] | None" = None
        if saved:
            if code.strip():
                return ToolResult(
                    ok=False, error="give `code` or `saved`, not both"
                )
            from ..skills.saved_scripts import read_script

            home = getattr(getattr(ctx, "config", None), "home", None)
            code, problem = await asyncio.to_thread(read_script, home, saved)
            if problem:
                return ToolResult(ok=False, error=problem)
            params = args.get("params")
            variables = {"params": params if isinstance(params, dict) else {}}
        if not code.strip():
            return ToolResult(ok=False, error="code is required (or `saved`: a skill's saved script)")
        try:
            timeout = float(args.get("timeout_s") or DEFAULT_TIMEOUT_S)
        except (TypeError, ValueError):
            timeout = DEFAULT_TIMEOUT_S
        if timeout != timeout:  # NaN
            timeout = DEFAULT_TIMEOUT_S
        timeout = min(max(timeout, 1.0), MAX_TIMEOUT_S)
        if self._repl is None:
            return ToolResult(ok=False, error="tool scripts are not available in this build")

        from . import script_values
        from .registry import current_call_scope
        from ..repl.session import MAX_TOOL_OUTPUT_CHARS

        scope = current_call_scope()
        names = self.callable_names(scope)
        made: dict[str, int] = {}
        refused: dict[str, str] = {}
        kept: dict[str, str] = {}
        planned: dict[str, Any] = {}
        session_id, once = worker_key(ctx)
        workspace = Path(getattr(ctx, "workspace", ".") or ".")
        own_origins: set[str] = set()
        # "A kept value with nowhere to go" is about the NEWEST script only.
        script_values.SHELF.take_unplaced(session_id)
        # THE WORKER THIS SCRIPT RUNS IN, held as an object (v1.473.0
        # review). It used to be looked up by NAME on every call, and a new
        # chat's worker is RENAMED the moment the chat is saved
        # (`adopt_turn_worker`): a link landing while the first script ran
        # made the lookup miss, the record of what the script had read fell
        # back to an empty set, and the outside call the rule exists to
        # refuse went through.
        worker: list[Any] = []

        def on_session(live: Any) -> None:
            worker[:] = [live]

        def origins() -> "set[str]":
            # Where the private data THIS WORKER's scripts hold came from. On
            # the worker's own session, so it lasts exactly as long as the
            # variables do; a registry double that hands no session over gets
            # this run's own set.
            live = worker[0] if worker else None
            if live is None:
                peek = getattr(self._repl, "peek", None)
                try:
                    live = peek(session_id, workspace, True) if callable(peek) else None
                except Exception:  # noqa: BLE001
                    live = None
            got = getattr(live, "script_origins", None)
            return got if isinstance(got, set) else own_origins

        def key_now() -> str:
            # The worker's name NOW (the chat's, once the chat was saved).
            live = worker[0] if worker else None
            return str(getattr(live, "session_id", "") or session_id)

        async def on_call(name: str, call_args: dict[str, Any]) -> dict[str, Any]:
            name = str(name or "").strip()
            call_args = {
                str(k): v
                for k, v in (call_args or {}).items()
                if str(k) not in ("_store_as", "_from_script")
            }
            why = await self._refusal(name, call_args, scope, ctx)
            if not why:
                why = self._carry_refusal(name, origins(), call_args, workspace)
            if why:
                refused.setdefault(name or "(no name)", why)
                return {"ok": False, "error": f"{why}."}
            made[name] = made.get(name, 0) + 1
            # A script that keeps calling tools is working, not stalled (a
            # coding session's stall watch hears this; a no-op elsewhere).
            progress.touch()
            result = await self._registry.invoke(
                name,
                call_args,
                ctx,
                scope.perms,
                agent_overrides=scope.agent_overrides,
                session_allow=scope.session_allow,
                allowed_names=set(scope.allowed_names),
                deadline_s=scope.deadline_s,
                grants=scope.grants,
                scopes=scope.scopes,
            )
            # The script now holds what this call read (public web pages are
            # not private, so they are not noted). A call that FAILED counts
            # too (v1.473.0 review): a tool's refusal often quotes what it
            # found ("no customer 'jane'. Did you mean: Jane Client (SSN …)",
            # a workbook's sheet names), and the script holds those words.
            origin = self._origin(name, call_args)[0]
            if origin != "web":
                origins().add(origin)
            if not result.ok:
                return {"ok": False, "error": str(result.error or f"{name} failed")}
            output = str(result.output or "")
            if len(output) > MAX_TOOL_OUTPUT_CHARS:
                dropped = len(output) - MAX_TOOL_OUTPUT_CHARS
                output = (
                    output[:MAX_TOOL_OUTPUT_CHARS]
                    + f"\n[{name} output TRUNCATED: the last {dropped} characters "
                    "were left out]"
                )
            return {"ok": True, "output": output, "data": result.data}

        async def safely(name: str, call_args: dict[str, Any]) -> dict[str, Any]:
            try:
                return await on_call(name, call_args)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — one item, one sentence
                return {"ok": False, "error": f"{name} could not be run ({type(exc).__name__}: {exc})"}

        # Tools whose calls WAIT (a page, a scan, a big file): only those are
        # worth running side by side. Learned per script, from the calls.
        waits: "set[str]" = set()

        async def on_batch(items: "list[dict[str, Any]]") -> "list[dict[str, Any]]":
            # `tools.map`: the calls that are safe together run MAP_PARALLEL
            # at a time; anything else waits its turn. Results keep the
            # script's order whatever order the calls finished in.
            #
            # SIDE BY SIDE ONLY WHEN A CALL WAITS (measured, v1.473.0).
            # Reads that each WAIT 0.2 s (a page, a scan in its own process)
            # are 3.9x faster four at a time. Reads that COMPUTE are not:
            # four quick ones together were slower than four in a row (their
            # ledger rows queue behind one another: 200 small files, 0.47 s
            # against 0.38 s), and twelve cold workbooks or PDFs were 0.9x
            # (parsing is one core's work in this process, whatever the
            # thread count). So the calls run in order until one both takes
            # MAP_QUICK_S or longer AND spends less than half of that on this
            # process's own processor; from then on that tool's calls run
            # together, for the rest of the script.
            results: "list[dict[str, Any] | None]" = [None] * len(items)
            gate = asyncio.Semaphore(MAP_PARALLEL)

            async def together(at: int) -> None:
                async with gate:
                    results[at] = await safely(items[at]["tool"], items[at]["args"])

            side_by_side = [
                at for at, item in enumerate(items) if item["tool"] in MAP_TOGETHER
            ]
            while side_by_side and items[side_by_side[0]]["tool"] not in waits:
                at = side_by_side.pop(0)
                began, worked = _wall_s(), _cpu_s()
                results[at] = await safely(items[at]["tool"], items[at]["args"])
                took = _wall_s() - began
                if took >= MAP_QUICK_S and _cpu_s() - worked < took * MAP_WAIT_SHARE:
                    waits.add(items[at]["tool"])
            if len(side_by_side) > 1:
                await asyncio.gather(*(together(at) for at in side_by_side))
            for at, item in enumerate(items):
                if results[at] is None:
                    results[at] = await safely(item["tool"], item["args"])
            return [r if r is not None else {"ok": False, "error": "not run"} for r in results]

        def on_keep(name: str, text: Any, plan: bool) -> dict[str, Any]:
            # `tools.keep` / `tools.plan`. On the worker's thread: the shelf
            # is thread-safe and nothing here awaits.
            from . import script_values

            if once:
                return {
                    "ok": False,
                    "error": "a value can only be kept inside a chat or a run",
                }
            if plan:
                from .apply_changes import NAME as APPLY, plan_problem

                allowed = getattr(scope, "allowed_names", None) or frozenset()
                if APPLY not in allowed:
                    return {
                        "ok": False,
                        "error": (
                            "changes cannot be applied in this turn (`apply_changes` "
                            "is not one of its tools). Make each change yourself, "
                            "with the tool for it"
                        ),
                    }
                try:
                    import json as _json

                    changes = _json.loads(text) if isinstance(text, str) else None
                except (ValueError, RecursionError):
                    changes = None
                problem = plan_problem(changes, allowed, self._registry)
                if problem:
                    return {"ok": False, "error": problem}
                planned["count"] = len(changes or [])
                counts: dict[str, int] = {}
                for change in changes or []:
                    counts[str(change.get("tool"))] = counts.get(str(change.get("tool")), 0) + 1
                planned["tools"] = counts
            try:
                # Under the worker's name NOW: a chat saved while its first
                # script ran is found by the chat's name from then on.
                summary = script_values.SHELF.put(key_now(), name, str(text or ""), plan=plan)
            except ValueError as exc:
                return {"ok": False, "error": str(exc)}
            if not plan:
                kept[name] = summary
            return {"ok": True, "output": f"kept `{name}`: {summary}"}

        hooks: dict[str, Any] = {
            "on_tool_batch": on_batch, "on_keep": on_keep, "on_session": on_session,
        }
        if variables is not None:
            hooks["variables"] = variables
        try:
            async with asyncio.timeout(MAX_TOTAL_S) as bound:
                payload = await self._repl.execute(
                    session_id,
                    code,
                    workspace=workspace,
                    timeout=timeout,
                    isolated=True,
                    tools=names,
                    on_tool_call=on_call,
                    **hooks,
                )
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            if not bound.expired():
                raise
            return ToolResult(
                ok=False,
                error=(
                    f"the script was still running after {MAX_TOTAL_S / 60:g} minutes "
                    "with its tool calls, so it was stopped and its variables are "
                    "gone. Do the work in smaller scripts."
                ),
                data={"tool_calls": dict(made), "refused": sorted(refused)},
            )
        except Exception as exc:  # noqa: BLE001 — never crash the agent loop
            return ToolResult(
                ok=False, error=f"the script could not be run: {type(exc).__name__}: {exc}"
            )
        finally:
            if once:
                # Nobody can come back to this worker: end it now, in the
                # background (a cancel must not wait on it).
                try:
                    task = asyncio.get_running_loop().create_task(self._repl.dispose(session_id))
                    _WARMING.add(task)
                    task.add_done_callback(_WARMING.discard)
                except Exception:  # noqa: BLE001
                    pass
        if not isinstance(payload, dict):
            return ToolResult(ok=False, error="the script worker gave no usable answer")
        allowed = getattr(scope, "allowed_names", None) or frozenset()
        file_tools = sorted(t for t in _fillable() if t in allowed and t != "apply_changes")
        if kept and not file_tools and bool(payload.get("ok")):
            # Nothing in this turn can take what the script kept. The chat
            # lanes read this and bring the file tool in for the next step
            # (chat_turn.offer_file_tool): which tools a turn starts with
            # follows the user's WORDS, and "put them in one workbook" must
            # not leave the rows with nowhere to go.
            script_values.SHELF.note_unplaced(key_now())
        return self._shape(
            payload, made, refused,
            kept=kept, planned=planned, file_tools=file_tools,
        )

    # -- what a script has read stays where it was read ------------------------

    def _origin(self, name: str, args: Any = None) -> "tuple[str, bool]":
        """``(origin, outside)`` for a tool: where the data it returns comes
        from, and whether calling it reaches outside this PC (the app's own
        Local only definition, ``egress``). Fail-closed: a tool that cannot
        be judged counts as reaching outside."""
        if name in _BROWSER_PAGE_READERS:
            return _BROWSER_PAGE_ORIGIN, True
        if name in _WEB_READERS:
            return "web", True
        if name in _MEMORY_BASE_READERS:
            # The query goes wherever the memory base lives.
            return ("local", False) if self._memory_bases_local() else (_MEMORY_ORIGIN, True)
        if name == _RESOURCE_READER:
            # One place per connected app, like that app's own tools: what
            # one app's resource said must not ride a read of another's.
            server = str((args or {}).get("server") or "").strip() if isinstance(args, dict) else ""
            return (f"pack:{server}" if server else f"outside:{name}"), True
        try:
            from ..providers import egress

            tool = self._registry.get(name)
            outside = name in egress.INTERNET_TOOLS or bool(
                egress.reaches_outside(tool, self._registry)
            )
        except Exception:  # noqa: BLE001
            outside = True
        if name.startswith("mcp__") and name.count("__") >= 2:
            return "pack:" + name.split("__", 2)[1], outside
        return (f"outside:{name}" if outside else "local"), outside

    def _memory_bases_local(self) -> bool:
        """Does every memory base live on this PC (a notes folder)? Cannot
        tell = no (fail-closed)."""
        try:
            from ..providers import egress

            manager = getattr(self._registry.get("ltm_search"), "manager", None)
            if manager is None:
                return False
            return all(egress.ltm_connector_is_local(c) for c in list(manager.connectors()))
        except Exception:  # noqa: BLE001
            return False

    def _carry_refusal(
        self, name: str, held: "set[str]", args: Any = None, workspace: Any = None
    ) -> str:
        """Why *name* is not called given what this worker's scripts already
        hold (*held*), or "". A tool that reaches outside is refused once the
        worker holds private data from anywhere but that tool's own place.

        v1.473.0 review: so is ANY call whose arguments name a place on
        another computer (``\\\\host\\share\\…``). The file readers count as
        local, but opening such a path makes Windows connect to that host:
        a script holding this PC's data could put it in the PATH."""
        if not held:
            return ""
        share = _network_path_in(args, workspace) if args else ""
        if share:
            return (
                f"this script has already read {_origin_words(sorted(held)[0])}, and "
                f"`{share}` is on another computer, so `{name}` cannot open it from "
                "the same script. Call the tool directly, one call at a time"
            )
        origin, outside = self._origin(name, args)
        if not outside:
            return ""
        others = sorted(o for o in held if o != origin)
        if not others:
            return ""
        return (
            f"this script has already read {_origin_words(others[0])}, and "
            f"`{name}` reaches outside this PC, so it cannot be called from "
            "the same script. Call it directly, one call at a time"
        )

    @staticmethod
    def _shape(
        payload: dict[str, Any],
        made: dict[str, int],
        refused: dict[str, str],
        *,
        kept: "dict[str, str] | None" = None,
        planned: "dict[str, Any] | None" = None,
        file_tools: "list[str] | None" = None,
    ) -> ToolResult:
        ok = bool(payload.get("ok"))
        stdout = str(payload.get("stdout") or "")
        stderr = str(payload.get("stderr") or "")
        value = str(payload.get("result") or "")
        error = str(payload.get("error") or "")
        truncated = bool(payload.get("truncated"))
        restarted = bool(payload.get("restarted"))

        parts: list[str] = []
        if restarted:
            parts.append(_RESTARTED_NOTE)
        if stdout.strip():
            parts.append(stdout.rstrip())
        if stderr.strip():
            parts.append("[stderr]\n" + stderr.rstrip())
        if value.strip():
            parts.append("[result] " + value.strip())
        if ok and not (stdout.strip() or stderr.strip() or value.strip()):
            parts.append(_NO_OUTPUT_NOTE)
        if truncated:
            parts.append(_TRUNCATED_NOTE)
        total = sum(made.values())
        if total:
            ranked = sorted(made.items(), key=lambda kv: (-kv[1], kv[0]))
            listed = ", ".join(f"{n} x{c}" for n, c in ranked[:_MAX_LISTED])
            more = len(ranked) - _MAX_LISTED
            parts.append(
                f"[the script made {total} tool call{'s' if total != 1 else ''}: {listed}"
                + (f" and {more} more tools" if more > 0 else "")
                + "]"
            )
        if refused:
            lines = "\n".join(f"- {why}" for why in list(refused.values())[:_MAX_LISTED])
            parts.append("[not run from the script]\n" + lines)
        if ok and kept:
            lines = "\n".join(f"- {name}: {what}" for name, what in list(kept.items())[:_MAX_LISTED])
            if file_tools:
                # SHORT ON PURPOSE (measured, six live runs of one request):
                # a longer hint that spelled the arguments out and said "no
                # `content`" made the model add a placeholder `content` in 4
                # of 6 runs. A reference typed INTO the content is caught by
                # its own refusal (`script_values.misplaced_reference`).
                how = (
                    "To put one in a file WITHOUT retyping it, call the file tool "
                    f"yourself and name it: {file_tools[0]}(..., "
                    '_from_script={"<argument>": "<name>"}).'
                )
            else:
                how = NO_FILE_TOOL_LINE
            parts.append("[kept for a file tool]\n" + lines + "\n" + how)
        if ok and planned and planned.get("count"):
            ranked = sorted((planned.get("tools") or {}).items(), key=lambda kv: (-kv[1], kv[0]))
            listed = ", ".join(f"{n} x{c}" for n, c in ranked[:_MAX_LISTED])
            parts.append(
                f"[planned {planned['count']} change{'s' if planned['count'] != 1 else ''}, "
                f"kept as `changes`: {listed}. NOTHING HAS RUN. To run them, call "
                'apply_changes yourself with _from_script={"changes": "changes"}: '
                "the user approves all of it once.]"
            )
        body = "\n".join(p for p in parts if p).strip()
        data: dict[str, Any] = {
            "truncated": truncated,
            "restarted": restarted,
            "tool_calls": dict(made),
            "refused": sorted(refused),
        }
        if kept:
            data["kept"] = sorted(kept)
        if planned and planned.get("count"):
            data["planned"] = int(planned["count"])
        if not ok:
            tail = error or stderr or "the script raised, but no traceback was captured"
            return ToolResult(
                ok=False,
                output=body,
                error=(body + "\n" + tail).strip() if body else tail,
                data=data,
            )
        return ToolResult(ok=True, output=body, data=data)


#: What a script is told when it kept a value and the turn holds no tool
#: that can take one. The chat lanes replace this line when they bring the
#: file tool in (``chat_turn.offer_file_tool``).
NO_FILE_TOOL_LINE = "No file tool is one of this turn's tools, so nothing can take them yet."


def _fillable() -> "frozenset[str]":
    from .script_values import FROM_SCRIPT_ARGS

    return frozenset(FROM_SCRIPT_ARGS)


async def adopt_turn_worker(platform: Any, turn_id: str, thread_id: str) -> int:
    """A new chat was saved: its first message's script worker (keyed by the
    TURN, :func:`worker_key`) becomes the chat's, so its variables are there
    for the next message (v1.471.0; they were silently gone). Called by the
    link route. Returns how many workers moved. Never raises."""
    try:
        repl = getattr(platform, "repl", None)
        turn, thread = str(turn_id or "").strip(), str(thread_id or "").strip()
        if not turn or not thread:
            return 0
        # What the first message's scripts KEPT follows the worker (v1.473.0).
        try:
            from .script_values import SHELF

            SHELF.rekey(f"chat:{turn}", f"chat:{thread}")
        except Exception:  # noqa: BLE001
            pass
        if repl is None or not hasattr(repl, "rekey"):
            return 0
        return int(await repl.rekey(f"chat:{turn}", f"chat:{thread}"))
    except Exception:  # noqa: BLE001 — the next message just starts fresh
        log.debug("tool_script: could not hand turn %s's worker to its chat", turn_id, exc_info=True)
        return 0


#: Warm-up tasks in flight (held so they are not collected mid-start).
_WARMING: "set[asyncio.Task[Any]]" = set()


def prewarm(platform: Any, armed: Any, ctx: Any) -> bool:
    """Start the isolated worker *ctx*'s scripts will run in NOW, when
    ``tool_script`` is one of the turn's tools, so the model's first script
    does not wait for a process to start. Call from the event loop; returns
    at once (the start runs in the background). True when a warm-up was
    started. Never raises."""
    try:
        if NAME not in {str(n) for n in (armed or ())}:
            return False
        repl = getattr(platform, "repl", None)
        if repl is None or not hasattr(repl, "warm"):
            return False
        key, once = worker_key(ctx)
        if once:
            return False  # a worker used once is not worth starting early
        task = asyncio.get_running_loop().create_task(
            repl.warm(key, getattr(ctx, "workspace", "."), isolated=True)
        )
        _WARMING.add(task)
        task.add_done_callback(_WARMING.discard)
        return True
    except Exception:  # noqa: BLE001 — warming is a courtesy
        return False
