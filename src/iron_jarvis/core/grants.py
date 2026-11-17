"""Standing grants — an approval that is remembered BY ARGUMENTS (v1.299.0).

Every grant the app carried before this module was keyed by NAME alone:
``Session.allow_tools_json`` (a run's ``session_allow``), a goal contract's
``allowed_grants_json``, the browser's ``TabGrants``. "Allow for this
conversation" on ``shell`` therefore allowed EVERY shell command for the rest
of the run, and the goals trust ladder could only ever offer "always allow
``web_fetch``" — never "always allow THIS command". A standing grant is the
missing shape: ``(scope, tool, args_hash)`` — the user said yes to this exact
call, in this goal / for this agent / in this project / in chat, for thirty
days, and the next identical call needs no card.

Three rules, each pinned in ``tests/test_standing_grants_v1299.py``:

* **A deny is never lifted.** The permission engine consults the store only
  AFTER its base/override deny check and the name-based ``session_allow``
  lift (``tools/permissions.py::authorize``). A low-trust run's kept-away
  tools are denies (``core/trust.low_trust_overrides``), so a grant never
  reaches them either.
* **A floor tool is granted only EXACTLY.** ``args_hash == ""`` means "any
  arguments" and is minted ONLY by the goals ladder's per-tool offer, never
  by a card; on a ``DENY_FLOOR_TOOLS`` name it is refused at ``create`` AND
  ignored at ``match`` (belt and braces: a row written by hand cannot arm
  "any shell command" for an unattended goal).
* **The hash is over the REAL arguments**, never the redacted display the
  card shows — two calls that redact to the same line are not the same
  call. The ``label`` column is the redacted one-liner, for the Grants list
  and the Activity page; it is never matched on.

THE MATCH RUNS ON THE LOOP, SO IT IS IN MEMORY. ``authorize`` is synchronous
and called inside ``registry.invoke`` on the daemon's one event loop; a
SQLite SELECT there, per tool call, is the shape the "nothing blocking on
the loop" rule exists for. The store keeps the LIVE rows in a dict (loaded
once, lazily; this process is the only writer — ``create``/``revoke`` update
it in place) and ``match`` is a dict scan. The ``uses``/``last_used_at``
bookkeeping is a cheap UPDATE scheduled OFF the loop (``run_in_executor``),
or inline when no loop is running (a sync route handler, a test).

Events: ``grant.created`` / ``grant.revoked`` ``{id, scope_kind, scope_id,
tool, label, expires_at}`` — published best-effort from sync code the way
``assignments/store.py`` does (a task on the running loop, a hop to the
bound loop from a worker thread, dropped with neither).
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from sqlalchemy.engine import Engine
from sqlmodel import Field, SQLModel, select

from .db import session_scope
from .events import EventType
from .ids import new_id, utcnow
from .logging import get_logger

log = get_logger("core.grants")

#: Where a grant applies. ``scope_id`` is the goal id / the roster name /
#: the project id / the literal ``"chat"`` / the schedule's name / the
#: workflow's name as its runs' origin stamp carries it
#: (``workflows.engine.workflow_grant_scope_id``). v1.505.0 (north-star 5.2,
#: D10): ``schedule`` and ``workflow`` — a schedule or a workflow is approved
#: ONCE; its runs read the scope off their origin (``schedule:<name>``,
#: ``workflow:<name>``), never from anything the model wrote.
SCOPE_KINDS: tuple[str, ...] = ("goal", "agent", "project", "chat", "schedule", "workflow")

#: The origin prefixes a run's own stamp turns into a scope (v1.505.0).
ORIGIN_SCOPES: tuple[str, ...] = ("goal", "schedule", "workflow")

#: How long a card-minted grant lives. The ladder may pass ``None`` (never)
#: for a GOAL-scoped grant only — a goal is the one scope with its own
#: lifecycle: ``DELETE /goals/{id}`` (routes/goals.py) revokes every live
#: goal-scoped grant of that goal, so a never-expiring row cannot outlive
#: the goal it was minted for.
GRANT_DEFAULT_DAYS = 30

#: The redacted one-line display, capped.
LABEL_MAX = 200

#: ``args_hash`` is 64 hex chars (sha256) or "" (any arguments).
HASH_LEN = 64

#: The precedence an "always" answer picks its scope by, strongest first.
#: Runs: agent > project > goal. Chat lanes: agent > project > chat (a chat
#: lane never has an agent, so project > chat in practice). ONE table, so
#: the two lanes and the runtime cannot disagree about which row they mint.
#: v1.505.0 (D10): a schedule's or a workflow's run keeps its "Always" ON the
#: schedule / workflow ("Always for this schedule"), ahead of the agent it
#: runs and the project it is grounded in: the card names that place, its row
#: shows the grant with a Remove press, and it covers nothing else. (A run
#: has one origin, so the two never meet a goal's scope.)
SCOPE_PRECEDENCE: tuple[str, ...] = ("schedule", "workflow", "agent", "project", "goal", "chat")


#: The app's OWN control keys — never part of a call's identity (third fix
#: plan, PG7). ``_store_as`` (v1.159.0, ``tools/registry.invoke`` strips it
#: before it authorises) and ``_isolate`` (v1.298.0, a lane or the runtime
#: adds it under low trust): hashing them would make the card's check and the
#: registry's check disagree about the same call. NAMED, not "every key that
#: starts with ``_``": a tool's own argument such as an app tool's ``_id`` is
#: the call's data, and dropping it let "Always allow exactly this" for one
#: record cover every other. A grant minted before this hashed such a call
#: without its ``_id``; it now matches no call that has one (the user is asked
#: again) and still matches what it did for a call that has none — never more.
CONTROL_ARGS: frozenset[str] = frozenset({"_store_as", "_isolate"})


def _canon_args(args: Any) -> dict:
    """The arguments that are the CALL's identity: a dict, minus the app's
    own control keys (:data:`CONTROL_ARGS`)."""
    if not isinstance(args, dict):
        return {}
    out = {str(k): v for k, v in args.items() if str(k) not in CONTROL_ARGS}
    # A SCRIPT'S KEPT VALUE (v1.499.3 review). ``_from_script`` is the one
    # underscore key the MODEL writes, and it carries the call's DATA: an
    # "always allow exactly this" grant must not cover a different kept
    # value written to the same path. Its identity (each argument's kept
    # name and the value's fingerprint) is hashed; the one-line summary is
    # wording only and is left out.
    if FROM_SCRIPT_ARG in args:
        out[FROM_SCRIPT_ARG] = _kept_identity(args.get(FROM_SCRIPT_ARG))
    return out


#: ``tools.script_values.ARG``, spelled here so this module imports nothing
#: from the tool layer (pinned equal by tests/test_review_fixes_tools_v1499_3.py).
FROM_SCRIPT_ARG = "_from_script"


def _kept_identity(spec: Any) -> Any:
    """``{argument: {"name": kept name, "sha": fingerprint}}`` for a kept-value
    reference in either spelling: the model's ``{"content": "rows"}`` (no
    fingerprint yet: ``sha`` "") or the registry's filled record
    ``{"content": {"name": "rows", "sha": "...", "what": "..."}}``."""
    if not isinstance(spec, dict):
        return spec
    out: dict[str, Any] = {}
    for arg, ref in spec.items():
        if isinstance(ref, dict):
            out[str(arg)] = {"name": str(ref.get("name") or ""), "sha": str(ref.get("sha") or "")}
        else:
            out[str(arg)] = {"name": str(ref or "").strip(), "sha": ""}
    return out


def _unpinned_kept_value(args: Any) -> bool:
    """True when *args* name a kept value whose fingerprint is not known (the
    model's own spelling, before the registry fills it). No EXACT grant can
    cover that call: what it would write is not known yet."""
    if not isinstance(args, dict) or FROM_SCRIPT_ARG not in args:
        return False
    spec = _kept_identity(args.get(FROM_SCRIPT_ARG))
    if not isinstance(spec, dict) or not spec:
        return True
    return any(not (isinstance(v, dict) and v.get("sha")) for v in spec.values())


def args_hash(tool: str, args: dict | None) -> str:
    """sha256 over ``tool + "\\n" + json.dumps(args, sort_keys=True,
    separators=(",", ":"), default=str)`` — hex, 64 chars. Argument ORDER
    never changes the identity; an unserialisable value degrades to its
    ``str`` rather than raising inside a tool loop."""
    canon = json.dumps(
        _canon_args(args), sort_keys=True, separators=(",", ":"), default=str
    )
    return hashlib.sha256((str(tool) + "\n" + canon).encode("utf-8")).hexdigest()


def grant_label(tool: str, safe_args: Any) -> str:
    """The one-line display for a grant: ``shell {"command": "git status"}``.
    ``safe_args`` MUST be the tool's REDACTED arguments (``tool.redact_args``)
    — this string is listed, logged and shown; it is never matched on."""
    try:
        shown = json.dumps(safe_args if safe_args is not None else {}, sort_keys=True, default=str)
    except (TypeError, ValueError):
        shown = repr(safe_args)
    text = f"{tool} {shown}".strip()
    if len(text) > LABEL_MAX:
        text = text[: LABEL_MAX - 1] + "…"
    return text


def pick_scope(scopes: Iterable[tuple[str, str]]) -> tuple[str, str] | None:
    """The STRONGEST scope present, by :data:`SCOPE_PRECEDENCE`; ``None``
    when nothing usable was given (then an "always" answer is only "once")."""
    best: tuple[str, str] | None = None
    best_rank = len(SCOPE_PRECEDENCE)
    for kind, sid in scopes or ():
        k, s = str(kind or ""), str(sid or "")
        if k not in SCOPE_PRECEDENCE or not s:
            continue
        rank = SCOPE_PRECEDENCE.index(k)
        if rank < best_rank:
            best, best_rank = (k, s), rank
    return best


def _aware(dt: datetime | None) -> datetime | None:
    """SQLite hands naive datetimes back; compare everything as UTC-aware."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


class StandingGrantRecord(SQLModel, table=True):
    """One remembered approval. Additive, plain columns (the reconciler's
    rules); ``args_hash == ""`` = any arguments (ladder-only)."""

    __tablename__ = "standinggrant"

    id: str = Field(default_factory=lambda: new_id("grant"), primary_key=True)
    scope_kind: str = Field(default="", index=True)
    scope_id: str = Field(default="", index=True)
    #: The tool NAME the call was made by (the registry also matches on the
    #: permission key, so a grouped tool's key works too).
    tool: str = Field(default="", index=True)
    args_hash: str = ""
    label: str = ""
    created_by: str = "user"
    created_at: datetime = Field(default_factory=utcnow)
    expires_at: datetime | None = None
    revoked_at: datetime | None = None
    uses: int = 0
    last_used_at: datetime | None = None

    def is_live(self, now: datetime | None = None) -> bool:
        now = now or utcnow()
        if self.revoked_at is not None:
            return False
        exp = _aware(self.expires_at)
        return exp is None or exp > now


def as_dict(rec: StandingGrantRecord) -> dict[str, Any]:
    def _iso(dt: datetime | None) -> str | None:
        return _aware(dt).isoformat() if dt is not None else None

    return {
        "id": rec.id,
        "scope_kind": rec.scope_kind,
        "scope_id": rec.scope_id,
        "tool": rec.tool,
        "args_hash": rec.args_hash,
        "exact": bool(rec.args_hash),
        "label": rec.label,
        "created_by": rec.created_by,
        "created_at": _iso(rec.created_at),
        "expires_at": _iso(rec.expires_at),
        "revoked_at": _iso(rec.revoked_at),
        "uses": int(rec.uses or 0),
        "last_used_at": _iso(rec.last_used_at),
        "live": rec.is_live(),
    }


class GrantStore:
    """The standing-grant table + its in-memory live view (see the module
    docstring for why the match is a dict scan)."""

    def __init__(self, engine: Engine, event_bus: Any = None) -> None:
        self.engine = engine
        self.event_bus = event_bus
        self._lock = threading.Lock()
        self._live: dict[str, StandingGrantRecord] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._jobs: set[Any] = set()

    as_dict = staticmethod(as_dict)

    # -- loop / events ----------------------------------------------------- #

    def bind_loop(self, loop: asyncio.AbstractEventLoop | None) -> None:
        self._loop = loop

    async def flush(self) -> None:
        """Await every in-flight publish / use-bump (tests; a route that must
        answer only once the ledger has the event)."""
        pending = list(self._jobs)
        if not pending:
            return
        awaitables = []
        for item in pending:
            if isinstance(item, asyncio.Future):
                awaitables.append(item)
            else:
                awaitables.append(asyncio.wrap_future(item))
        await asyncio.gather(*awaitables, return_exceptions=True)

    def _track(self, job: Any) -> None:
        self._jobs.add(job)
        job.add_done_callback(self._jobs.discard)

    def _publish(self, type_: str, payload: dict) -> None:
        bus = self.event_bus
        if bus is None:
            return
        try:
            result = bus.publish(type_, payload)
        except Exception:  # noqa: BLE001 — a sync double that raises
            log.debug("grant publish failed", exc_info=True)
            return
        if not inspect.isawaitable(result):
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is not None:
            self._track(running.create_task(result))
            return
        loop = self._loop
        if loop is not None and not loop.is_closed():
            try:
                self._track(asyncio.run_coroutine_threadsafe(result, loop))
                return
            except Exception:  # noqa: BLE001
                log.debug("grant publish hand-off failed", exc_info=True)
        try:
            result.close()  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _event_payload(rec: StandingGrantRecord) -> dict[str, Any]:
        return {
            "id": rec.id,
            "scope_kind": rec.scope_kind,
            "scope_id": rec.scope_id,
            "tool": rec.tool,
            "label": rec.label,
            "expires_at": _aware(rec.expires_at).isoformat() if rec.expires_at else None,
        }

    # -- the live view ----------------------------------------------------- #

    def _ensure_loaded(self) -> dict[str, StandingGrantRecord]:
        with self._lock:
            if self._live is not None:
                return self._live
            live: dict[str, StandingGrantRecord] = {}
            try:
                with session_scope(self.engine) as db:
                    rows = db.exec(
                        select(StandingGrantRecord).where(
                            StandingGrantRecord.revoked_at.is_(None)  # type: ignore[union-attr]
                        )
                    ).all()
                    for r in rows:
                        db.expunge(r)
                        live[r.id] = r
            except Exception:  # noqa: BLE001 — no table yet = no grants yet
                log.debug("standing grants not loadable yet", exc_info=True)
                live = {}
            self._live = live
            return live

    def refresh(self) -> None:
        """Drop the live view so the next call re-reads the table."""
        with self._lock:
            self._live = None

    # -- writes ------------------------------------------------------------ #

    @staticmethod
    def _floor_any_args(tool: str, hash_: str) -> str:
        from ..tools.permissions import DENY_FLOOR_TOOLS

        if not hash_ and tool in DENY_FLOOR_TOOLS:
            return (
                f"{tool} is on the deny floor — a standing grant for it must "
                "name the exact arguments, never any arguments"
            )
        return ""

    def create(
        self,
        scope_kind: str,
        scope_id: str,
        tool: str,
        args_hash: str,
        label: str,
        expires_days: int | None = GRANT_DEFAULT_DAYS,
        *,
        created_by: str = "user",
    ) -> StandingGrantRecord:
        """Mint a grant (blocking; callers on the loop hop off). An identical
        LIVE grant is returned instead of duplicated. ``ValueError`` names
        every refusal: an unknown scope kind, an empty scope id / tool, a
        malformed hash, "never" outside the goal scope, any-args on a floor
        tool."""
        kind = str(scope_kind or "").strip()
        sid = str(scope_id or "").strip()
        name = str(tool or "").strip()
        hash_ = str(args_hash or "").strip().lower()
        if kind not in SCOPE_KINDS:
            raise ValueError(f"unknown grant scope {scope_kind!r}; expected one of {SCOPE_KINDS}")
        if not sid:
            raise ValueError("a grant needs a scope id")
        if not name:
            raise ValueError("a grant needs a tool")
        if hash_ and (len(hash_) != HASH_LEN or any(c not in "0123456789abcdef" for c in hash_)):
            raise ValueError("args_hash must be 64 hex characters, or empty for any arguments")
        problem = self._floor_any_args(name, hash_)
        if problem:
            raise ValueError(problem)
        if expires_days is None and kind != "goal":
            raise ValueError("only a goal-scoped grant may never expire")
        now = utcnow()
        expires_at = None
        if expires_days is not None:
            days = int(expires_days)
            if days <= 0:
                raise ValueError("expires_days must be positive, or null for never (goal scope only)")
            expires_at = now + timedelta(days=days)
        live = self._ensure_loaded()
        with self._lock:
            for rec in live.values():
                if (
                    rec.scope_kind == kind
                    and rec.scope_id == sid
                    and rec.tool == name
                    and rec.args_hash == hash_
                    and rec.is_live(now)
                ):
                    return rec
        rec = StandingGrantRecord(
            scope_kind=kind,
            scope_id=sid,
            tool=name,
            args_hash=hash_,
            label=str(label or grant_label(name, {}))[:LABEL_MAX],
            created_by=str(created_by or "user"),
            created_at=now,
            expires_at=expires_at,
        )
        with session_scope(self.engine) as db:
            db.add(rec)
            db.commit()
            db.refresh(rec)
            db.expunge(rec)
        with self._lock:
            live[rec.id] = rec
        self._publish(EventType.GRANT_CREATED, self._event_payload(rec))
        return rec

    def revoke(self, grant_id: str) -> StandingGrantRecord | None:
        """Revoke (idempotent: an already-revoked row is returned as is).
        ``None`` for an unknown id."""
        with session_scope(self.engine) as db:
            rec = db.get(StandingGrantRecord, str(grant_id or ""))
            if rec is None:
                return None
            already = rec.revoked_at is not None
            if not already:
                rec.revoked_at = utcnow()
                db.add(rec)
                db.commit()
                db.refresh(rec)
            db.expunge(rec)
        live = self._ensure_loaded()
        with self._lock:
            live.pop(rec.id, None)
        if not already:
            self._publish(EventType.GRANT_REVOKED, self._event_payload(rec))
        return rec

    def revoke_scope(self, scope_kind: str, scope_id: str) -> int:
        """Revoke every live grant kept on one scope; returns how many
        (v1.505.0, north-star 5.2). Called when a schedule or a saved
        workflow is REMOVED: its standing OKs go with it, so one made later
        under the same name starts with none and asks again. (An Undo of
        the removal brings the schedule back without them, which is the
        safe side.) Never raises."""
        kind, sid = str(scope_kind or ""), str(scope_id or "")
        if not kind or not sid:
            return 0
        done = 0
        try:
            for rec in self.list(scope_kind=kind, scope_id=sid, live_only=True):
                if self.revoke(rec.id) is not None:
                    done += 1
        except Exception:  # noqa: BLE001 — a grant we could not revoke lapses by itself
            log.warning("grants: could not revoke the %s scope %r", kind, sid, exc_info=True)
        return done

    # -- reads ------------------------------------------------------------- #

    def get(self, grant_id: str) -> StandingGrantRecord | None:
        with session_scope(self.engine) as db:
            rec = db.get(StandingGrantRecord, str(grant_id or ""))
            if rec is not None:
                db.expunge(rec)
            return rec

    def list(
        self,
        scope_kind: str | None = None,
        scope_id: str | None = None,
        live_only: bool = True,
    ) -> list[StandingGrantRecord]:
        """Newest first, from the table (the routes' view)."""
        with session_scope(self.engine) as db:
            stmt = select(StandingGrantRecord)
            if scope_kind:
                stmt = stmt.where(StandingGrantRecord.scope_kind == str(scope_kind))
            if scope_id:
                stmt = stmt.where(StandingGrantRecord.scope_id == str(scope_id))
            stmt = stmt.order_by(StandingGrantRecord.created_at.desc())  # type: ignore[arg-type]
            rows = list(db.exec(stmt).all())
            for r in rows:
                db.expunge(r)
        if live_only:
            now = utcnow()
            rows = [r for r in rows if r.is_live(now)]
        return rows

    def match(
        self,
        scopes: Iterable[tuple[str, str]] | None,
        tool: str,
        args: dict | None,
        *,
        aliases: Iterable[str] = (),
        touch: bool = True,
    ) -> StandingGrantRecord | None:
        """The live grant that covers ``tool(args)`` in one of ``scopes``, or
        ``None``. In memory (see the module docstring). An exact grant beats
        an any-args one; a floor tool never matches any-args, whatever the
        row says. A hit bumps ``uses``/``last_used_at`` off the loop —
        unless ``touch=False`` (a card predicate peeking before the registry
        makes the one real check)."""
        wanted = {str(tool or "")} | {str(a) for a in aliases if a}
        wanted.discard("")
        scope_set = {
            (str(k or ""), str(s or "")) for k, s in (scopes or ()) if k and s
        }
        if not wanted or not scope_set:
            return None
        from ..tools.permissions import DENY_FLOOR_TOOLS

        floor = bool(wanted & DENY_FLOOR_TOOLS)
        now = utcnow()
        # v1.499.3: a call naming a kept value whose fingerprint is not known
        # yet (the card's peek at the model's own arguments) matches no EXACT
        # grant; the registry's check, on the filled record, decides.
        hashes = set() if _unpinned_kept_value(args) else {args_hash(name, args) for name in wanted}
        live = self._ensure_loaded()
        exact: StandingGrantRecord | None = None
        any_args: StandingGrantRecord | None = None
        with self._lock:
            for rec in live.values():
                if rec.tool not in wanted or (rec.scope_kind, rec.scope_id) not in scope_set:
                    continue
                if not rec.is_live(now):
                    continue
                if rec.args_hash:
                    if rec.args_hash in hashes and exact is None:
                        exact = rec
                elif not floor and any_args is None:
                    any_args = rec
        hit = exact or any_args
        if hit is not None and touch:
            self._note_use(hit, now)
        return hit

    # -- use bookkeeping --------------------------------------------------- #

    def _note_use(self, rec: StandingGrantRecord, now: datetime) -> None:
        with self._lock:
            rec.uses = int(rec.uses or 0) + 1
            rec.last_used_at = now
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is not None:
            try:
                self._track(running.run_in_executor(None, self._bump, rec.id, now))
                return
            except Exception:  # noqa: BLE001 — bookkeeping must never fail a call
                log.debug("grant use bump hand-off failed", exc_info=True)
                return
        self._bump(rec.id, now)

    def _bump(self, grant_id: str, now: datetime) -> None:
        try:
            with session_scope(self.engine) as db:
                row = db.get(StandingGrantRecord, grant_id)
                if row is None:
                    return
                row.uses = int(row.uses or 0) + 1
                row.last_used_at = now
                db.add(row)
                db.commit()
        except Exception:  # noqa: BLE001
            log.debug("grant use bump failed", exc_info=True)
