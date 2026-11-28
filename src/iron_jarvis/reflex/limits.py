"""Leap 05-c: a rule runs hands-off inside the Limits you approved once.

"Keep it" on a rehearsal of a rule (``tools/rehearsal_runs.keep``) writes the
rule's LIMITS, read from that rehearsal's ledger rows by code, never from the
model's words:

* WHERE IT MAY WRITE. The folder it works in (``ReflexRule.workspace_root``,
  pinned to the rehearsal's own folder when the rule had none) for every
  write the rehearsal listed inside it, and the folder of each write it
  listed outside it. The targets are read by ``deliverables.scope._targets``,
  the same reading the registry's refusal uses, so what was approved and
  what is checked cannot drift. Its runs carry the folders in their OWN
  options (:data:`LIMITS_OPTION`, written by the rule's door,
  ``router._run_session``); the runtime builds the call's ``task_scope``
  from them (:func:`scope_for`) and ``registry.invoke`` refuses a write
  elsewhere (``deliverables.scope.refusal_for``). The run then STOPS
  (``agents/runtime``), ends "Needs you" (``agents/outcome`` counts the
  refused row) and the rule's row says why (:func:`note_stop`). A helper
  agent the run starts inherits the folders (``Orchestrator.create_session``,
  :func:`inherited`).
* WHAT IT MAY DO WITHOUT ASKING. One rule-scoped standing OK
  (``core/grants``) per tool the rehearsal said would ASK, any arguments,
  30 days. A deny floor tool (``shell``…) gets none, as the grant store
  refuses it: it keeps asking, and the Limits line says so. A priced call
  still asks whatever the OKs say (``runtime._pause_needed``).

A rehearsal's own run carries no Limits (it shows what the rule would do,
and Keep it approves that). An edit of what the rule does or where it works
drops them (``ReflexStore.update``: the kept OKs are revoked by the hook the
platform registers, :func:`drop_kept_grants`); removing the rule revokes all
its OKs (05-a). The card's Limits line is :func:`words`.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from ..core.logging import get_logger
from ..deliverables.scope import LIMITS_LEAD, TaskScope

log = get_logger("reflex.limits")

#: The run option a rule's door writes: ``{"rule_id", "folders"}``.
LIMITS_OPTION = "rule_limits"
#: The run option a rule's door writes on a REHEARSAL's run: what the rule
#: did when it was rehearsed (:func:`shape`), so Keep it never approves a
#: rehearsal of what the rule no longer does.
SHAPE_OPTION = "rule_shape"
#: ``TaskScope.rule`` of a rule's Limits.
LIMITS_RULE = "rule limits"
#: ``StandingGrantRecord.created_by`` of an OK Keep it made.
GRANT_BY = "rehearsal"
#: How long such an OK lives (the card-minted default).
GRANT_DAYS = 30
#: How a run's summary begins when it stopped at its Limits (written by the
#: runtime, read by ``agents/outcome.run_ending`` for the reason).
STOP_LEAD = "Stopped at its limits:"
#: The most folders and OKs one Keep it writes.
MAX_FOLDERS = 12
MAX_GRANTS = 24

#: What each kind of step is, in the Limits line's words.
KIND_WORDS = {
    "write": "write files",
    "change": "change files",
    "move": "move or rename files",
    "remove": "remove files",
    "send": "send messages",
    "run": "run commands",
    "app": "use your apps",
    "page": "act on web pages",
}

__all__ = [
    "LIMITS_LEAD", "LIMITS_OPTION", "LIMITS_RULE", "GRANT_BY", "STOP_LEAD",
    "decoded", "approved_from", "keep", "options_for", "scope_for", "inherited",
    "words", "note_stop", "stop_words", "drop_kept_grants", "LIMITS_FIELDS",
    "SHAPE_OPTION", "shape", "unchanged", "STALE_WORDS",
]

#: The fields whose change drops the Limits (what the rule does, what starts
#: it, where it works). Its name and its hourly limit do not.
LIMITS_FIELDS = (
    "source", "match", "action", "target", "task_template", "project_id",
    "start_json", "agent_name", "workspace_root",
)


#: Keep it's answer when the rule changed after the rehearsal.
STALE_WORDS = (
    "The rule changed after this rehearsal (what it does, what starts it or where it works). "
    "Rehearse it again, then keep it."
)


def shape(rule: Any) -> str:
    """A fingerprint of :data:`LIMITS_FIELDS` as ``ReflexStore.update``
    compares them (a rename or a new hourly limit leaves it alone). Pure."""
    import hashlib

    from .store import _limits_value

    data = {k: _limits_value(rule, k) for k in LIMITS_FIELDS}
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:24]


def unchanged(platform: Any, rec: Any, rule: Any) -> bool:
    """Does ``rule`` still do what it did when ``rec`` rehearsed it? Read
    from the ``rule_shape`` its door wrote on the rehearsal's first run. A
    rehearsal with no such mark (or one that cannot be read) is NOT
    unchanged: Keep it then writes nothing. BLOCKING; never raises."""
    try:
        from ..core.db import session_scope
        from ..core.models import Session
        from ..scheduling.knobs import session_options
        from ..tools.rehearsal_store import RehearsalStore

        started = RehearsalStore.sessions(rec)
        if not started:
            return False
        with session_scope(platform.engine) as db:
            row = db.get(Session, started[0])
            said = str(session_options(row).get(SHAPE_OPTION) or "") if row is not None else ""
        return bool(said) and said == shape(rule)
    except Exception:  # noqa: BLE001
        log.warning("limits: the rehearsal %s could not be read", getattr(rec, "id", "?"), exc_info=True)
        return False


def _loads(raw: Any) -> dict[str, Any]:
    try:
        data = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def decoded(rule: Any) -> dict[str, Any]:
    """The rule's kept Limits: ``{"folders", "grants", "asks", ...}``, or
    ``{}`` when none are kept. Pure; junk reads ``{}``."""
    data = _loads(getattr(rule, "limits_json", "") or "")
    folders = [str(f) for f in data.get("folders") or [] if isinstance(f, str) and f.strip()]
    if not folders:
        return {}
    data["folders"] = folders
    data["grants"] = [str(g) for g in data.get("grants") or [] if isinstance(g, str)]
    data["asks"] = [str(a) for a in data.get("asks") or [] if isinstance(a, str)]
    return data


def _key(path: Path) -> str:
    try:
        return os.path.normcase(str(Path(path).resolve()))
    except OSError:
        return os.path.normcase(os.path.abspath(str(path)))


def _within(target: Path, root: Path) -> bool:
    t, r = _key(target), _key(root)
    return t == r or t.startswith(r.rstrip("\\/") + os.sep)


def _made_here(platform: Any, folder: str) -> bool:
    """Is ``folder`` one Iron Jarvis made for a run
    (``orchestrator.is_managed_workspace``)? False when it cannot be told."""
    try:
        from ..agents.orchestrator import is_managed_workspace

        cfg = getattr(platform, "config", None)
        return bool(cfg is not None and is_managed_workspace(cfg, folder))
    except Exception:  # noqa: BLE001
        return False


def _folder_of(target: Path, root: Path | None) -> Path:
    if root is not None and _within(target, root):
        return root
    return target.parent


# --------------------------------------------------------------------------- #
# Reading what a rehearsal approved
# --------------------------------------------------------------------------- #


def approved_from(platform: Any, rec: Any, rule: Any) -> dict[str, Any]:
    """What Keep it on the rehearsal ``rec`` of ``rule`` would approve, read
    from the rehearsal's ledger rows: ``{"root", "folders", "tools":
    [{"tool", "kind"}], "asks": [tool, ...]}``. ``tools`` are the steps that
    would have asked and get an OK; ``asks`` those that keep asking (a deny
    floor tool). BLOCKING (a few reads). Never raises (an unreadable ledger
    approves the folder it works in and nothing else)."""
    from sqlmodel import select

    from ..core.db import session_scope
    from ..core.models import Session, ToolInvocation
    from ..deliverables.scope import _targets
    from ..tools import rehearsal as R
    from ..tools.permissions import DENY_FLOOR_TOOLS
    from ..tools.rehearsal_runs import sessions_of

    root_text = str(getattr(rule, "workspace_root", "") or "").strip()
    out: dict[str, Any] = {"root": root_text, "folders": [], "tools": [], "asks": []}
    try:
        sessions = sessions_of(platform, rec)
        with session_scope(platform.engine) as db:
            workspaces = {}
            for sid in sessions:
                row = db.get(Session, sid)
                if row is not None:
                    workspaces[sid] = str(row.workspace_path or "")
            rows = list(db.exec(
                select(ToolInvocation)
                .where(ToolInvocation.session_id.in_(sessions or [""]))  # type: ignore[union-attr]
                .where(ToolInvocation.rehearsed == True)  # noqa: E712
                .order_by(ToolInvocation.created_at)  # type: ignore[arg-type]
            ).all())
            for r in rows:
                db.expunge(r)
    except Exception:  # noqa: BLE001
        log.warning("limits: the rehearsal %s could not be read", getattr(rec, "id", "?"), exc_info=True)
        sessions, workspaces, rows = [], {}, []
    from ..tools.rehearsal_store import RehearsalStore

    started = [s for s in RehearsalStore.sessions(rec) if s in workspaces] or sessions[:1]
    if not root_text and started:
        # The rule had no folder of its own: it keeps the one its rehearsal
        # ran in, so its next runs work where the folders were approved.
        root_text = workspaces.get(started[0], "")
    out["root"] = root_text
    root = Path(root_text) if root_text else None
    # A step an OK kept by an EARLIER Keep it let through (no ask in this
    # rehearsal) is still a step that asks without one: a new Keep it, which
    # revokes the earlier OKs, must keep it too.
    earlier = set(decoded(rule).get("grants") or [])
    folders: list[Path] = []
    seen_tools: set[str] = set()
    for r in rows:
        would = str(r.would or "run")
        name = str(r.tool or "")
        if would == "refuse" or not name or name in R.SIDE_DOORS:
            continue
        try:
            args = json.loads(r.args_json or "{}")
        except (TypeError, ValueError):
            args = {}
        base = Path(workspaces.get(r.session_id) or root_text or ".")
        try:
            writes, _touches = _targets(name, args if isinstance(args, dict) else {}, base)
        except Exception:  # noqa: BLE001
            writes = []
        own = workspaces.get(r.session_id) or ""
        # A helper's own work folder, when Iron Jarvis made it: a helper may
        # always write there (:func:`scope_for`, the same test). A helper
        # handed the user's folder has its writes there approved like any.
        own_made = (r.session_id not in started) and bool(own) and _made_here(platform, own)
        for target in writes:
            if own_made and _within(Path(target), Path(own)):
                continue
            folder = _folder_of(Path(target), root)
            if all(_key(folder) != _key(f) for f in folders):
                folders.append(folder)
        if would == "run" and str(getattr(r, "grant_id", "") or "") in earlier:
            would = "ask"
        if would == "ask" and name not in seen_tools:
            seen_tools.add(name)
            tool = platform.registry.get(name) if getattr(platform, "registry", None) else None
            if tool is None:
                continue
            if name in DENY_FLOOR_TOOLS:
                out["asks"].append(name)
                continue
            out["tools"].append({"tool": name, "kind": R.effect_of(name, args, tool).kind})
    if not folders and root is not None:
        folders.append(root)
    out["folders"] = [str(f) for f in folders[:MAX_FOLDERS]]
    out["tools"] = out["tools"][:MAX_GRANTS]
    return out


def keep(platform: Any, rec: Any, rule_id: str) -> dict[str, Any] | None:
    """Keep it on a rule's rehearsal: write the rule's Limits from what the
    rehearsal listed (:func:`approved_from`), replacing earlier ones (their
    OKs are revoked), and pin its folder when it had none. Returns the kept
    Limits, or None when the rule is gone. BLOCKING."""
    from ..core.db import session_scope
    from ..core.ids import utcnow
    from .models import ReflexRule

    rule = platform.reflex.get(rule_id)
    if rule is None or str(rule.action or "") != "session":
        # Only an agent run of this PC has Limits; a hand-off to a remote
        # agent is held by its own side door, and keeps none.
        return None
    approved = approved_from(platform, rec, rule)
    grants = getattr(platform, "grants", None)
    before = decoded(rule)
    if grants is not None:
        drop_kept_grants(grants, before.get("grants") or [])
    made: list[str] = []
    asks = list(approved["asks"])
    kinds: list[str] = []
    if grants is not None:
        for item in approved["tools"]:
            tool = item["tool"]
            try:
                g = grants.create(
                    "rule", rule.id, tool, "",
                    f"{tool}: any arguments (kept after a rehearsal)",
                    GRANT_DAYS, created_by=GRANT_BY,
                )
            except ValueError:
                asks.append(tool)
                continue
            made.append(g.id)
            kinds.append(item["kind"])
    else:
        asks.extend(i["tool"] for i in approved["tools"])
    kept = {
        "folders": approved["folders"],
        "grants": made,
        "kinds": kinds,
        "asks": asks,
        "rehearsal_id": str(getattr(rec, "id", "") or ""),
        "kept_at": utcnow().isoformat(),
    }
    with session_scope(platform.engine) as db:
        row = db.get(ReflexRule, rule_id)
        if row is None:
            if grants is not None:
                drop_kept_grants(grants, made)
            return None
        if not str(row.workspace_root or "").strip() and approved["root"]:
            row.workspace_root = approved["root"]
        row.limits_json = json.dumps(kept) if approved["folders"] else ""
        row.limits_stop_json = ""
        db.add(row)
        db.commit()
    return kept


def drop_kept_grants(grants: Any, grant_ids: Iterable[str]) -> int:
    """Revoke the OKs Keep it made (by id; one the user said Always to on a
    card is never among them). Returns how many. Never raises."""
    done = 0
    for gid in list(grant_ids or []):
        try:
            rec = grants.get(gid)
            if rec is None or str(rec.created_by or "") != GRANT_BY or rec.revoked_at is not None:
                continue
            if grants.revoke(gid) is not None:
                done += 1
        except Exception:  # noqa: BLE001 — an OK we could not revoke lapses by itself
            log.warning("limits: could not revoke %s", gid, exc_info=True)
    return done


# --------------------------------------------------------------------------- #
# The run: its options, its scope, its stop
# --------------------------------------------------------------------------- #


def options_for(rule: Any) -> dict[str, Any] | None:
    """The run option a REAL fire of ``rule`` carries (None without kept
    Limits). Pure."""
    kept = decoded(rule)
    if not kept:
        return None
    return {"rule_id": str(getattr(rule, "id", "") or ""), "folders": list(kept["folders"])}


def _option_of(options: Any) -> dict[str, Any] | None:
    raw = options.get(LIMITS_OPTION) if isinstance(options, dict) else None
    if not isinstance(raw, dict):
        return None
    folders = [str(f) for f in raw.get("folders") or [] if isinstance(f, str) and f.strip()]
    rid = str(raw.get("rule_id") or "").strip()
    if not folders or not rid:
        return None
    return {"rule_id": rid, "folders": folders, "helper": raw.get("helper") is True}


def scope_for(options: Any, workspace: Any, *, own_folder: bool = False) -> TaskScope | None:
    """The ``task_scope`` of a run whose OWN options carry a rule's Limits,
    else None (every other run: nothing changes). ``own_folder``: is
    ``workspace`` a folder Iron Jarvis made for this run (the caller asks
    ``orchestrator.is_managed_workspace``)? Pure (no disk)."""
    opt = _option_of(options)
    if opt is None:
        return None
    root = Path(str(workspace or ".") or ".")
    roots = [Path(f) for f in opt["folders"]]
    if opt["helper"] and own_folder and str(workspace or "").strip():
        # A helper the rule's run started may also write in its OWN work
        # folder, only when Iron Jarvis made it: a helper handed the user's
        # folder (its parent works right there) gets no more than its parent.
        roots.append(root)
    return TaskScope(
        root=root,
        workspace=root,
        deliverables=(),
        write_roots=tuple(roots),
        limits_folders=tuple(opt["folders"]),
        rule=LIMITS_RULE,
        limits_of=opt["rule_id"],
    )


def inherited(options: dict[str, Any]) -> None:
    """A run made while a call of a run with Limits runs (a helper agent's
    hand-off) carries the same Limits (in place). Never raises."""
    try:
        if _option_of(options) is not None:
            return
        from ..deliverables.scope import current_scope

        scope = current_scope()
        rid = str(getattr(scope, "limits_of", "") or "")
        if scope is None or not rid:
            return
        # The PARENT's approved folders only (a helper of a helper does not
        # inherit its parent's own work folder).
        folders = [str(p) for p in (getattr(scope, "limits_folders", ()) or scope.write_roots)]
        options[LIMITS_OPTION] = {"rule_id": rid, "folders": list(folders), "helper": True}
    except Exception:  # noqa: BLE001
        log.warning("limits: a helper's run could not inherit them", exc_info=True)


def stop_words(error: str, folders: Iterable[str] = ()) -> str:
    """The stopped run's summary (and the row's words), from the registry's
    refusal: which file it would have written and where it may write."""
    text = " ".join(str(error or "").split())
    target = ""
    marker = " would write "
    if marker in text:
        target = text.split(marker, 1)[1].split(". Stop here", 1)[0].strip()
    where = ", ".join(str(f) for f in folders) or "the folders you approved"
    what = f"it would have written {target}" if target else "it would have written a file"
    return (
        f"{STOP_LEAD} {what}, outside the folders it may write in ({where}). "
        "Nothing was written there. Rehearse it again and press Keep it to widen its limits."
    )


def note_stop(platform: Any, rule_id: str, words: str, href: str) -> None:
    """Put a stopped run's words on the rule's row ("Needs you"). BLOCKING;
    never raises."""
    try:
        from ..core.db import session_scope
        from .models import ReflexRule

        with session_scope(platform.engine) as db:
            row = db.get(ReflexRule, str(rule_id or ""))
            if row is None:
                return
            row.limits_stop_json = json.dumps({
                "words": str(words or "")[:500],
                "href": str(href or "")[:300],
                "at": datetime.now(timezone.utc).isoformat(),
            })
            db.add(row)
            db.commit()
    except Exception:  # noqa: BLE001 — the run's own record still says it
        log.warning("limits: the rule %s's row could not be told", rule_id, exc_info=True)


def stop_of(rule: Any) -> dict[str, str] | None:
    """The row's "Needs you" line: ``{"words", "href", "at"}`` or None."""
    data = _loads(getattr(rule, "limits_stop_json", "") or "")
    said = str(data.get("words") or "").strip()
    if not said:
        return None
    href = str(data.get("href") or "")
    return {
        "words": said,
        "href": href if href.startswith("/") and not href.startswith("//") else "",
        "at": str(data.get("at") or ""),
    }


# --------------------------------------------------------------------------- #
# The card's Limits line
# --------------------------------------------------------------------------- #


def _folder_name(path: str) -> str:
    p = str(path or "").rstrip("\\/")
    return p.replace("\\", "/").rsplit("/", 1)[-1] or p


def _join(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def words(rule: Any, live_kinds: Iterable[str] | None = None) -> str:
    """The first sentences of the Limits line when Limits are kept ("" when
    none): where it may write, what it may do without asking (``live_kinds``:
    the kinds of the kept OKs that are still live; None reads the kinds kept),
    what keeps asking. Pure."""
    kept = decoded(rule)
    if not kept:
        return ""
    names = [_folder_name(f) for f in kept["folders"][:4]]
    more = len(kept["folders"]) - len(names)
    where = _join(names) + (f" and {more} more folder{'s' if more != 1 else ''}" if more > 0 else "")
    out = f"Writes only in {where}; anything outside stops it and it needs you."
    kinds = list(live_kinds) if live_kinds is not None else list(kept.get("kinds") or [])
    said: list[str] = []
    for k in kinds:
        w = KIND_WORDS.get(str(k), "")
        if w and w not in said:
            said.append(w)
    if said:
        out += f" It may {_join(said)} without asking."
    asks = []
    for name in kept["asks"]:
        from ..tools import rehearsal as R

        w = KIND_WORDS.get(R.effect_of(name, {}).kind, "")
        if w and w not in asks:
            asks.append(w)
    if asks:
        out += f" To {_join(asks)} it still asks you each time."
    return out


def offer(platform: Any, rec: Any) -> dict[str, Any] | None:
    """What Keep it on the rule rehearsal ``rec`` sets, for its card:
    ``{"folders", "words"}`` (the Limits line it would write), or None when
    the rule is gone. BLOCKING; never raises."""
    try:
        rule = platform.reflex.get(str(getattr(rec, "subject_id", "") or ""))
        if rule is None or str(rule.action or "") != "session":
            return None
        if not unchanged(platform, rec, rule):
            # The rule changed after this rehearsal: Keep it is refused, so
            # the card offers nothing.
            return None
        approved = approved_from(platform, rec, rule)
        if not approved["folders"]:
            return None
        from ..tools.permissions import DENY_FLOOR_TOOLS

        probe = type("_Probe", (), {})()
        probe.limits_json = json.dumps({
            "folders": approved["folders"],
            "kinds": [t["kind"] for t in approved["tools"] if t["tool"] not in DENY_FLOOR_TOOLS],
            "asks": approved["asks"],
        })
        return {"folders": approved["folders"], "words": words(probe)}
    except Exception:  # noqa: BLE001
        log.warning("limits: the offer of %s could not be read", getattr(rec, "id", "?"), exc_info=True)
        return None


def live_kinds(platform: Any, rule: Any) -> list[str] | None:
    """The kinds of the OKs Keep it made that are still live (removed ones
    drop out of the line). None when they cannot be read. Never raises."""
    kept = decoded(rule)
    if not kept:
        return []
    grants = getattr(platform, "grants", None)
    if grants is None:
        return None
    try:
        live = set(grants.live_ids("rule", str(getattr(rule, "id", "") or "")))
    except Exception:  # noqa: BLE001
        return None
    kinds = list(kept.get("kinds") or [])
    return [k for gid, k in zip(kept["grants"], kinds) if gid in live]
