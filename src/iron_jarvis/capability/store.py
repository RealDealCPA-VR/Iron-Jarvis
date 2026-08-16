"""Filing, listing and DECIDING capability proposals (v1.178.0, P4).

The contract, in one line each:

* :meth:`CapabilityProposalStore.create` writes ONE ``pending`` row and touches
  nothing else. No tool is registered, no permission is written, no server is
  added. A model can call it as often as it likes and the machine is unchanged.
* :meth:`CapabilityProposalStore.approve` is the ONLY thing that creates
  anything, and it creates it through the path the app already owns —
  ``tools/dynamic.ToolCreateTool``, the same code the ``tool_create`` tool runs,
  so a proposed tool gets the identical name validation, built-in-collision
  check, ``shell=False`` argv rendering and ``custom:<name>`` permission key. No
  second way to make a tool was invented, because a second way is a second thing
  to secure.
* :meth:`CapabilityProposalStore.reject` takes it off the queue and suppresses
  the signature, so "no" sticks.

THE DENY FLOOR IS CHECKED TWICE, ON PURPOSE. :func:`floor_violation` runs at
FILE time (in the tool, where a model can still fix its request) and again at
APPROVE time (here, where the click happens). One implementation, two call
sites: the file-time check is a courtesy and the approve-time check is the
safety property, and a row that reached the database some other way — an older
build, a direct API call, a hand-edited record — still cannot be approved.

WHAT APPROVAL CANNOT DO, stated plainly because the whole feature is worthless
if it is not honest about this:

* it cannot raise a tool in :data:`~iron_jarvis.tools.permissions.
  DENY_FLOOR_TOOLS` (``shell``/``repl``/``browser_use``/``web_action``/
  ``mcp_call``). Those are refused outright — a proposal cannot become the
  loophole the deny floor exists to close;
* it cannot write ANY permission entry, not even a benign one. An approved tool
  runs at ``custom:<name>``, absent from the table, resolved ``ask``;
* it cannot add an MCP server or a connection. Both need a command and
  credentials only the user has, and an MCP server would arm ``mcp:*`` tools
  that answer to ``mcp_call`` — a floor tool. So :meth:`describe_kind` says so
  BEFORE the user clicks, and ``approve`` returns an honest ``ok=False`` with
  the row left ``pending`` (``MemoryProposalStore.approve``'s convention for a
  memory base it cannot rewrite).
"""

from __future__ import annotations

import asyncio
import json
import weakref
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlmodel import select

from ..core.db import session_scope
from ..core.ids import utcnow
from ..core.logging import get_logger
from ..core.models import PermissionMode
from ..tools.base import ToolContext
from ..tools.permissions import DENY_FLOOR_TOOLS
from .models import (
    APPROVED,
    KIND_LABELS,
    KINDS,
    MAX_NAME,
    MAX_RATIONALE,
    MAX_SCOPE,
    MAX_SPEC_JSON,
    MAX_TASK,
    PENDING,
    REJECTED,
    CapabilityProposalRecord,
    normalize_name,
    signature_for,
)

log = get_logger("capability.proposals")

#: The synthetic session id an approval files its ``tool_create`` under, so the
#: creation is auditable without pretending an agent session made it. Same trick
#: ``memory.proposals.APPLY_SESSION_ID`` uses.
APPLY_SESSION_ID = "capability-review"

#: argv[0] values a custom tool may not have. THE HOLE THIS CLOSES: ``shell`` is
#: on the deny floor and ``custom:<name>`` is not, so a proposal for a tool whose
#: command is ``["bash", "-c", "{cmd}"]`` (or ``cmd /c``, or ``python -c``)
#: rebuilds the floor tool under a name the floor never hears about. Refusing the
#: interpreter is deliberately blunt — a legitimate script tool names the SCRIPT
#: (``["python", "report.py", "{file}"]`` is refused too), which is a real cost,
#: paid because the alternative is a bypass that looks like a feature.
#:
#: WRITTEN WITHOUT EXECUTABLE SUFFIXES: :func:`floor_violation` tests the file
#: name AND its stem, so ``bash`` covers ``bash.exe``/``bash.cmd``. Listing the
#: suffixed spellings by hand is what broke it — the set carried ``cmd.exe`` and
#: ``python.exe`` but not ``bash.exe``, and on Windows (the platform this app
#: ships on) ``["bash.exe", "-c", "{cmd}"]`` was MEASURED filing, approving and
#: creating a working shell-under-another-name.
_INTERPRETER_ARGV0: frozenset[str] = frozenset(
    {
        "sh", "bash", "zsh", "dash", "ksh", "csh", "tcsh", "fish",
        "cmd", "command.com", "command", "powershell",
        "pwsh", "wsl", "env",
        "python", "python3", "pythonw", "py",
        "node", "deno", "bun", "ruby", "perl", "php",
        "osascript", "wscript", "cscript", "rundll32", "mshta",
        # RUNNERS: these take the program to execute FROM their arguments, which
        # is the same hole as an interpreter — `["npx", "{pkg}"]` fetches and
        # runs arbitrary code, `["xargs", "{prog}"]` execs whatever it is given.
        # Omitting them made the list arbitrary: `python -c` was refused while
        # `npx` — strictly more reach, since it also downloads — went through.
        "npx", "bunx", "uvx", "xargs", "awk", "gawk", "mawk",
    }
)

#: Engines whose table has already been ensured (idempotent DDL; this just keeps
#: a per-request store construction from re-running it). A WeakSet of the ENGINES
#: rather than a set of ``id(engine)``: ids are reused after a garbage collection,
#: so an id-keyed cache can report "already ensured" for an engine that has never
#: seen the DDL — rare in the daemon (one engine) and routine in a test run that
#: builds a platform per test.
_ENSURED: "weakref.WeakSet[Any]" = weakref.WeakSet()


def _ensure_table(engine) -> None:
    """Create ``capabilityproposalrecord`` if missing.

    Belt-and-braces beside ``core.db._LATE_MODEL_MODULES``: boot registers this
    module so ``create_all`` builds the table AND the additive-column reconciler
    can see it (the v1.151.2 lesson — a lazily-created table lands on every fresh
    test DB and on no real install). This covers a store constructed against an
    engine that never went through ``init_db`` at all. Never raises.
    """
    try:
        if engine in _ENSURED:
            return
    except TypeError:  # pragma: no cover — a stub engine that is not hashable
        pass
    try:
        CapabilityProposalRecord.__table__.create(engine, checkfirst=True)
    except Exception:  # noqa: BLE001 — a DDL hiccup must never break a request
        log.exception("could not ensure the capability-proposal table")
    try:
        _ENSURED.add(engine)
    except TypeError:  # pragma: no cover — not weak-referenceable; re-run is safe
        pass


def floor_violation(kind: str, name: str, spec: dict[str, Any] | None = None) -> str:
    """The reason this request may never be granted, or ``""``.

    Pure and importable so the TOOL can refuse at file time and the STORE can
    refuse at approve time from ONE rule set. Returns the sentence a user or a
    model should read — never a bare bool, because "refused" without "why" is
    what makes a model retry the same thing.

    Note what is NOT checked here: the proposal's ``requested_permission``. It
    needs no guard because it is never applied — approval writes no permission
    entry at all, so a request for ``allow`` is a wish on a card, not a grant.
    """
    if normalize_name(name) in DENY_FLOOR_TOOLS:
        return (
            f"“{name}” is on the deny floor — {', '.join(sorted(DENY_FLOOR_TOOLS))} "
            "can never be raised to allow, by an agent definition or by an "
            "approved proposal. Ask for a NARROW tool that does the one thing "
            "you need instead."
        )
    if str(kind or "").strip().lower() != "tool":
        return ""
    argv = [str(a).strip() for a in ((spec or {}).get("command") or []) if str(a).strip()]
    if not argv:
        return ""
    raw_head = argv[0].strip('"').strip("'")
    # A TEMPLATED PROGRAM IS THE WHOLE SHELL, not a tool. ``["{prog}", "{a}",
    # "{b}"]`` names no program at all: CommandTool fills argv[0] from the CALL
    # arguments, so the created tool runs whatever the model passes — measured
    # end to end (`prog=cmd, a=/c, b=echo …` executed). Every interpreter name
    # below is irrelevant when argv[0] is a hole, so this is checked FIRST.
    if "{" in raw_head and "}" in raw_head:
        return (
            f"a custom tool cannot take its PROGRAM from a parameter — “{argv[0]}” "
            "means the caller chooses what runs, which is `shell` with extra steps "
            "and escapes the deny floor `shell` sits on. Name the program "
            "literally and use {placeholders} only for its arguments."
        )
    # Both spellings: Windows ships `bash.exe`/`python.exe`, and a set written
    # with the suffixes by hand missed `bash.exe` while carrying `cmd.exe`.
    head = Path(raw_head).name.casefold()
    if head in _INTERPRETER_ARGV0 or Path(head).stem in _INTERPRETER_ARGV0:
        return (
            f"a custom tool cannot run “{argv[0]}” — that is a shell or an "
            "interpreter, so the tool would be `shell` under another name and "
            "would escape the deny floor `shell` sits on. Name the actual "
            "program the job needs, with its arguments."
        )
    return ""


def parameter_violation(params: Any) -> str:
    """Why this ``parameters`` value cannot become a tool, or ``""``.

    THE MEASURED FAILURE, reproduced end to end on this box: a spec carrying
    ``parameters: ["file"]`` (a list of STRINGS — the shape a model reaches for
    when the schema says "one object per placeholder") is accepted by
    ``tool_create``, which PERSISTS the ``DynamicToolRecord`` and only then
    calls ``build_tool`` → ``_build_input_schema`` → ``p.get("name")`` on a
    ``str`` → ``AttributeError``. The approval reports an honest "could not
    create it" and leaves the row pending, so it LOOKS like nothing happened —
    but the poisoned row is on disk, and ``build_platform`` rebuilds every
    persisted custom tool at boot, so the NEXT daemon start raises the same
    AttributeError and the app never comes up again. A refused approval that
    bricks the install is the worst possible outcome of a suggest-don't-act
    feature.

    Same one-implementation-two-call-sites shape as :func:`floor_violation`: the
    tool screens at FILE time (where the model can still fix the request) and
    ``_create_tool`` screens again at APPROVE time, because the click is where a
    row from an older build or a direct API call would take effect. The deeper
    fix belongs in ``tools/dynamic.py`` (persisting before building is what makes
    a bad shape durable); this refuses to hand it that shape at all.
    """
    if params is None or params == []:
        return ""
    if not isinstance(params, list):
        return (
            "‘parameters’ must be an ARRAY of objects, one per {placeholder} — "
            "e.g. [{\"name\": \"file\", \"type\": \"string\", \"required\": true}]."
        )
    for entry in params:
        if not isinstance(entry, dict):
            return (
                f"‘parameters’ must be objects, not {type(entry).__name__} "
                f"({entry!r}) — write each one as {{\"name\": \"file\", \"type\": "
                "\"string\", \"required\": true}}, because a bare name cannot "
                "describe the argument."
            )
        if not str(entry.get("name") or "").strip():
            return (
                "every entry in ‘parameters’ needs a non-empty \"name\" matching "
                "the {placeholder} it fills."
            )
    return ""


@dataclass
class ApplyResult:
    """The honest outcome of approving one proposal.

    ``ok=False`` is a NORMAL answer here (an MCP server cannot be created from
    this side), and the caller then leaves the row ``pending`` — an unsupported
    kind is a refusal, never a silent "approved" that made nothing.
    """

    ok: bool
    detail: str = ""
    error: str = ""
    #: The capability that now exists (empty when nothing was created).
    created: str = ""
    #: The permission key the created thing RUNS under, and the mode that key
    #: actually resolves to right now — read back from the live engine, not
    #: assumed, because "it runs at ask" is the claim this feature rests on.
    permission_key: str = ""
    permission_mode: str = ""
    #: What the user still has to do themselves (mcp/connection).
    next_step: str = ""
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "detail": self.detail,
            "error": self.error,
            "created": self.created,
            "permission_key": self.permission_key,
            "permission_mode": self.permission_mode,
            "next_step": self.next_step,
            "notes": list(self.notes),
        }


def _run_blocking(coro):
    """Run an async tool's ``execute`` from this synchronous store.

    ``ToolCreateTool.execute`` is a coroutine and this store is called from
    FastAPI ``def`` handlers, which run in a worker thread — so there is no
    running loop here and ``asyncio.run`` is correct. If a caller ever reaches
    this ON the loop we refuse loudly instead of deadlocking: v1.153.1's rule is
    that nothing blocking runs on the event loop, and the failure mode when it
    does is not an exception, it is "Daemon offline" for every other request.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    raise RuntimeError(
        "approve() blocks and must not run on the event loop — call it from a "
        "sync FastAPI handler (threadpool) or via asyncio.to_thread"
    )


class CapabilityProposalStore:
    """Persist / list / decide capability proposals.

    ``platform`` is only needed to APPROVE (it supplies the tool registry, the
    dynamic-tool registry and the permission engine); the read side works
    without one, so a bare ``CapabilityProposalStore(engine)`` is a perfectly
    good listing store for a degraded route and for tests.
    """

    def __init__(self, engine, platform=None) -> None:
        self.engine = engine
        self.platform = platform
        _ensure_table(engine)

    # -- write side -----------------------------------------------------------

    def known_tool(self, name: str) -> str:
        """The EXISTING tool whose name matches *name*, or ``""``.

        The measured failure was an agent that did not know the app could do
        something (it shelled out to re-read PDFs it had already read). A
        proposal for a capability that is already installed is that same
        blindness, and answering it with the real name turns a wasted request
        into a working call.
        """
        registry = getattr(self.platform, "registry", None)
        if registry is None:
            return ""
        try:
            names = list(registry.names() or [])
        except Exception:  # noqa: BLE001 — a listing failure must not block filing
            return ""
        norm = normalize_name(name)
        for existing in names:
            if normalize_name(existing) == norm:
                return str(existing)
        return ""

    def create(
        self,
        *,
        kind: str,
        name: str,
        rationale: str,
        scope: str,
        task: str = "",
        spec: dict[str, Any] | None = None,
        requested_permission: str = "ask",
        run_id: str = "",
    ) -> CapabilityProposalRecord | None:
        """File one request. Returns ``None`` when it is SUPPRESSED.

        Raises ``ValueError`` for input the caller got wrong (unknown kind, no
        name); a database hiccup logs and returns ``None`` — filing a suggestion
        must never be the reason an agent run dies.
        """
        kind = str(kind or "").strip().lower()
        if kind not in KINDS:
            raise ValueError(f"unknown capability kind {kind!r}; expected one of {KINDS}")
        clean_name = str(name or "").strip()[:MAX_NAME]
        if not clean_name:
            raise ValueError("a capability proposal must name what it is asking for")
        raw_spec = json.dumps(spec if isinstance(spec, dict) else {})
        # An oversize spec is dropped WHOLE, never clipped: a truncated JSON
        # string does not parse, so a clipped row would read as empty anyway —
        # and would do so silently, at approve time, weeks later.
        spec_json = raw_spec if len(raw_spec) <= MAX_SPEC_JSON else "{}"
        sig = signature_for(kind, clean_name)
        record = CapabilityProposalRecord(
            kind=kind,
            name=clean_name,
            name_norm=normalize_name(clean_name),
            rationale=str(rationale or "").strip()[:MAX_RATIONALE],
            scope=str(scope or "").strip()[:MAX_SCOPE],
            task=str(task or "").strip()[:MAX_TASK],
            spec_json=spec_json,
            requested_permission=str(requested_permission or "ask").strip().lower()[:16],
            signature=sig,
            run_id=str(run_id or "")[:120],
        )
        try:
            with session_scope(self.engine) as db:
                clash = db.exec(
                    select(CapabilityProposalRecord).where(
                        CapabilityProposalRecord.signature == sig,
                        # "approved" is deliberately NOT suppressed: the user may
                        # have deleted the tool since, and asking again then is a
                        # real request rather than a repeat.
                        CapabilityProposalRecord.status.in_((PENDING, REJECTED)),
                    )
                ).first()
                if clash is not None:
                    return None
                db.add(record)
                db.commit()
                db.refresh(record)
                return record
        except Exception:  # noqa: BLE001
            log.exception("could not file capability proposal (%s / %s)", kind, name)
            return None

    # -- read side (never raises) ---------------------------------------------

    def list(self, status: str | None = None) -> list[CapabilityProposalRecord]:
        """Proposals for the review card: pending first, newest first within."""
        try:
            with session_scope(self.engine) as db:
                query = select(CapabilityProposalRecord)
                if status is not None:
                    query = query.where(CapabilityProposalRecord.status == status)
                rows = list(db.exec(query))
        except Exception:  # noqa: BLE001
            log.exception("capability-proposal list failed")
            return []
        rows.sort(
            key=lambda r: (
                r.status != PENDING,
                -(r.created_at.timestamp() if r.created_at else 0.0),
            )
        )
        return rows

    def get(self, proposal_id: str) -> CapabilityProposalRecord | None:
        try:
            with session_scope(self.engine) as db:
                return db.get(CapabilityProposalRecord, proposal_id)
        except Exception:  # noqa: BLE001
            log.exception("capability-proposal get failed")
            return None

    def suppressed(self, signature: str) -> bool:
        """True when this signature would be refused by :meth:`create`."""
        try:
            with session_scope(self.engine) as db:
                return (
                    db.exec(
                        select(CapabilityProposalRecord).where(
                            CapabilityProposalRecord.signature == signature,
                            CapabilityProposalRecord.status.in_((PENDING, REJECTED)),
                        )
                    ).first()
                    is not None
                )
        except Exception:  # noqa: BLE001
            log.exception("capability-proposal suppression check failed")
            return False

    def stats(self) -> dict[str, Any]:
        """Counts for the status line (never raises)."""
        empty = {"pending": 0, "approved": 0, "rejected": 0, "total": 0, "by_kind": {}}
        try:
            with session_scope(self.engine) as db:
                rows = list(db.exec(select(CapabilityProposalRecord)))
        except Exception:  # noqa: BLE001
            log.exception("capability-proposal stats failed")
            return empty
        out = dict(empty)
        by_kind: dict[str, int] = {}
        for r in rows:
            out["total"] += 1
            if r.status in out:
                out[r.status] += 1
            if r.status == PENDING:
                by_kind[r.kind] = by_kind.get(r.kind, 0) + 1
        out["by_kind"] = by_kind
        return out

    # -- what approval can and cannot do (the UI must say this BEFORE a click) -

    def describe_kind(self, kind: str) -> dict[str, Any]:
        """Can a proposal of this kind actually be applied from here?

        ``describe_base``'s job in ``memory/proposals.py``, and the same honesty
        argument: a card that offers Approve for something approval cannot do
        turns a click into a lie.
        """
        kind = str(kind or "").strip().lower()
        if kind == "tool":
            if getattr(self.platform, "tools_registry", None) is None:
                return {
                    "can_apply": False,
                    "note": (
                        "custom tools are unavailable in this build, so this "
                        "can’t be created from here."
                    ),
                }
            return {
                "can_apply": True,
                "note": (
                    "Approving creates it as a custom tool. It will still ask "
                    "for your approval every time it runs (custom:<name>)."
                ),
            }
        if kind == "mcp":
            return {
                "can_apply": False,
                "note": (
                    "Iron Jarvis can’t add an MCP server for you: it needs a "
                    "command and credentials only you have, and its tools answer "
                    "to `mcp_call`, which can never be raised to allow. Add it on "
                    "the Connections page if you want it."
                ),
            }
        if kind == "connection":
            return {
                "can_apply": False,
                "note": (
                    "A connection carries YOUR credentials, so it is added on the "
                    "Connections page and never by an approval here."
                ),
            }
        return {"can_apply": False, "note": f"unknown capability kind “{kind}”."}

    # -- decisions ------------------------------------------------------------

    def approve(self, proposal_id: str) -> tuple[CapabilityProposalRecord, ApplyResult]:
        """Create the capability and mark the proposal approved.

        Returns ``(record, result)``. When ``result.ok`` is False NOTHING was
        created and the record stays ``pending`` — the user can fix the request
        (or satisfy it themselves) and try again. Raises ``ValueError`` for an
        unknown (404) or already-decided (409) proposal, mirroring
        ``MemoryProposalStore.approve``.
        """
        with session_scope(self.engine) as db:
            row = db.get(CapabilityProposalRecord, proposal_id)
            if row is None:
                raise ValueError(f"no such proposal: {proposal_id}")
            if row.status != PENDING:
                raise ValueError(f"proposal already {row.status}")
            kind, name = row.kind, row.name
            spec = row.decoded_spec()
            scope, rationale = row.scope, row.rationale

        result = self._apply(
            kind=kind, name=name, spec=spec, scope=scope, rationale=rationale
        )
        if not result.ok:
            # Leave it PENDING and stamp WHY on the row, so a card refreshed
            # later still explains the refusal instead of silently offering the
            # same button again.
            self._stamp(proposal_id, result, status=None)
            return self.get(proposal_id) or row, result

        with session_scope(self.engine) as db:
            row = db.get(CapabilityProposalRecord, proposal_id)
            if row is None:  # deleted underneath us — the tool still exists
                raise ValueError(f"no such proposal: {proposal_id}")
            row.status = APPROVED
            row.decided_at = utcnow()
            row.applied_json = json.dumps({**result.to_dict(), "at": utcnow().isoformat()})
            db.add(row)
            db.commit()
            db.refresh(row)
            return row, result

    def reject(self, proposal_id: str) -> CapabilityProposalRecord:
        """Take a pending request off the queue for good.

        The row survives as a REJECTED record rather than being deleted, because
        the signature is what suppresses the re-ask: a model that re-derives the
        same gap every run would otherwise file it again tomorrow and read its
        own success message as progress. Raises ``ValueError`` for unknown /
        already-decided.
        """
        with session_scope(self.engine) as db:
            row = db.get(CapabilityProposalRecord, proposal_id)
            if row is None:
                raise ValueError(f"no such proposal: {proposal_id}")
            if row.status != PENDING:
                raise ValueError(f"proposal already {row.status}")
            row.status = REJECTED
            row.decided_at = utcnow()
            db.add(row)
            db.commit()
            db.refresh(row)
            return row

    def _stamp(self, proposal_id: str, result: ApplyResult, status: str | None) -> None:
        """Record an attempt's outcome. Best-effort; never raises."""
        try:
            with session_scope(self.engine) as db:
                row = db.get(CapabilityProposalRecord, proposal_id)
                if row is None or row.status != PENDING:
                    return
                row.applied_json = json.dumps(
                    {**result.to_dict(), "at": utcnow().isoformat()}
                )
                if status:
                    row.status = status
                    row.decided_at = utcnow()
                db.add(row)
                db.commit()
        except Exception:  # noqa: BLE001
            log.exception("could not record a capability-approval attempt")

    # -- the apply mechanism --------------------------------------------------

    def _apply(
        self,
        *,
        kind: str,
        name: str,
        spec: dict[str, Any],
        scope: str,
        rationale: str,
    ) -> ApplyResult:
        """Create the capability. NEVER raises."""
        # THE SAFETY PROPERTY, checked here and not only in the tool: a row can
        # reach the database from an older build or a direct API call, and the
        # click is where it would take effect.
        violation = floor_violation(kind, name, spec)
        if violation:
            return ApplyResult(ok=False, error=violation)
        capability = self.describe_kind(kind)
        if not capability["can_apply"]:
            return ApplyResult(
                ok=False,
                error=capability["note"],
                next_step=capability["note"],
            )
        try:
            return self._create_tool(name=name, spec=spec, scope=scope, rationale=rationale)
        except Exception as exc:  # noqa: BLE001 — an honest error beats a 500
            log.exception("approving a capability proposal failed (%s / %s)", kind, name)
            return ApplyResult(
                ok=False, error=f"could not create it: {type(exc).__name__}: {exc}"
            )

    def _tool_create(self):
        """The LIVE ``tool_create`` tool, or a fresh one bound to the platform.

        Resolved from the registry first so this path is literally the tool the
        agent-facing ``tool_create`` runs — including any future change to its
        validation. The fallback covers a platform whose registry never got the
        management tools (a bare unit-test platform).
        """
        registry = getattr(self.platform, "registry", None)
        if registry is not None:
            live = registry.get("tool_create")
            if live is not None:
                return live
        from ..tools.dynamic import ToolCreateTool

        return ToolCreateTool(self.platform)

    def _context(self) -> ToolContext:
        """A minimal ToolContext for the create call.

        ``ToolCreateTool`` reads only ``session_id`` (it stamps ``created_by``),
        but the dataclass is fully populated so a future field cannot make this
        a silent ``None`` dereference.
        """
        config = getattr(self.platform, "config", None)
        home = getattr(config, "home", None) or "."
        return ToolContext(
            workspace=Path(str(home)),
            session_id=APPLY_SESSION_ID,
            agent_run_id="",
            config=config,
            event_bus=getattr(self.platform, "event_bus", None),
            engine=self.engine,
        )

    def _create_tool(
        self, *, name: str, spec: dict[str, Any], scope: str, rationale: str
    ) -> ApplyResult:
        """Create the proposed custom tool THROUGH ``tool_create``."""
        # THE SECOND HALF OF THE TOOL'S OWN "you already have this" REFUSAL, and
        # it belongs here for the same reason the floor check does: the click is
        # what takes effect, and a card outlives the state it was filed against.
        # A request for `pdf_pages` filed on Monday and approved on Friday —
        # after the user authored their own `pdf_pages` with tool_create — went
        # straight through `tools_registry.register`, which UPSERTS by name: the
        # user's argv was silently replaced and the live registry rebound
        # (measured). `tool_create` allows that overwrite on purpose (an agent
        # revising its own tool); an approval is not a revision.
        existing = self.known_tool(name)
        if existing:
            return ApplyResult(
                ok=False,
                error=(
                    f"“{existing}” already exists in this app, so approving this "
                    "would REPLACE it rather than add anything. Reject the "
                    "request, or delete the existing tool first if replacing it "
                    "is genuinely what you want."
                ),
            )
        argv = [str(a) for a in (spec.get("command") or []) if str(a).strip()]
        if not argv:
            return ApplyResult(
                ok=False,
                error=(
                    "this request doesn’t say what the tool would RUN, so there "
                    "is nothing to create. Reject it and ask again with a "
                    "command."
                ),
            )
        params = spec.get("parameters")
        # THE CLICK IS WHERE IT TAKES EFFECT (see parameter_violation): a spec
        # whose parameters are strings persists a DynamicToolRecord and then
        # breaks EVERY subsequent boot, so it is refused here as well as at
        # file time — the row may predate the file-time check.
        bad_params = parameter_violation(params)
        if bad_params:
            return ApplyResult(
                ok=False,
                error=(
                    f"this request's parameters cannot be created: {bad_params} "
                    "Reject it and ask again."
                ),
            )
        args: dict[str, Any] = {
            "name": name,
            # The user approved THIS sentence, so it becomes the tool's
            # description — every future agent then reads the scope the user
            # agreed to rather than a paraphrase written later.
            "description": (scope or rationale or f"custom tool {name}")[:MAX_SCOPE],
            "command": argv,
            "parameters": params if isinstance(params, list) else [],
        }
        timeout = spec.get("timeout_seconds")
        if isinstance(timeout, int) and timeout > 0:
            args["timeout_seconds"] = timeout

        result = _run_blocking(self._tool_create().execute(args, self._context()))
        if not getattr(result, "ok", False):
            return ApplyResult(
                ok=False, error=str(getattr(result, "error", "") or "could not create it")
            )

        key = f"custom:{name}"
        mode = self._mode_for(key)
        notes = [
            f"It runs under {key}, which is not in the permission table — so it "
            "asks every time. Approving the request did not grant the tool."
        ]
        return ApplyResult(
            ok=True,
            detail=str(getattr(result, "output", "") or f"created custom tool {name}"),
            created=name,
            permission_key=key,
            permission_mode=mode,
            notes=notes,
        )

    def _mode_for(self, permission_key: str) -> str:
        """The mode ``permission_key`` ACTUALLY resolves to, read from the live
        engine rather than assumed — this is the claim the feature rests on."""
        engine = getattr(self.platform, "permissions", None)
        if engine is None:
            return PermissionMode.ASK.value
        try:
            return str(engine.mode_for(permission_key).value)
        except Exception:  # noqa: BLE001
            return PermissionMode.ASK.value


def proposal_view(record: CapabilityProposalRecord, store: CapabilityProposalStore) -> dict:
    """One request, FLAT, with the honesty fields a review card needs."""
    capability = store.describe_kind(record.kind)
    spec = record.decoded_spec()
    return {
        "id": record.id,
        "kind": record.kind,
        "kind_label": KIND_LABELS.get(record.kind, record.kind),
        "name": record.name,
        "rationale": record.rationale,
        "scope": record.scope,
        "task": record.task,
        "spec": spec,
        "command": [str(c) for c in (spec.get("command") or [])],
        "requested_permission": record.requested_permission,
        # What it will ACTUALLY run under if approved. Shown next to the request
        # so nobody reads "requested: allow" as "will be allowed".
        "runs_under": f"custom:{record.name}" if record.kind == "tool" else "",
        "status": record.status,
        "run_id": record.run_id,
        "applied": record.decoded_applied(),
        "can_apply": bool(capability.get("can_apply")),
        "kind_note": str(capability.get("note") or ""),
        "blocked": floor_violation(record.kind, record.name, spec),
        "created_at": record.created_at.isoformat() if record.created_at else None,
        "decided_at": record.decided_at.isoformat() if record.decided_at else None,
    }
