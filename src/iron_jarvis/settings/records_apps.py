"""Apps and notification destinations from chat (v1.435.0, chat-first plan
Wave 3 group H, Part B2's "Apps" and "Notification destinations" rows).

Chat could CONNECT an app (``app_connect``) and add a Telegram / Slack /
Discord destination (``channel_connect``) or switch its two-way listening
(``channel_toggle``). Everything else was a page: turning an app off,
disconnecting one, bringing over a server the user already runs in Claude
Desktop, removing a destination, choosing which alerts it gets, and adding an
email destination. These tools close that gap the way every record tool does
(``settings.records``): each one ASKS FIRST (the ordinary approval card) and
leaves an Undo on the reply that puts the record back exactly.

* ``app_switch`` — the Tools page's switch (``mcp/pack_door.set_pack_enabled``):
  the app is kept with its keys; off unloads its tools, on loads them again.
  Undo puts the row back as it was (``records.restore_app``).
* ``app_disconnect`` — the Directory's Disconnect (``connectors.service.
  _disconnect_mcp_server`` → ``mcp/pack_door.remove_pack``): the row, every
  vault row, the manifest, the live tools. A removal deletes the app's keys,
  so the Undo would be a lie unless they are kept: right before removing,
  each key is copied ENCRYPTED into the vault under a backup name (the
  ``settings/credentials`` ``__undo__`` way); the journal keeps only NAMES.
  Undo puts the keys, the manifest and the row back and starts the app.
* ``app_import`` — "add the QuickBooks server I use in Claude Desktop": the
  file is read READ-ONLY by its scan key (``mcp/import_sources``), and the
  server is added through the import route's own code (``routes/agents.
  _apply_specs``, the ONE add door ``mcp/pack_door.add_pack`` underneath) —
  values go from the disk into the vault, never through the model. With no
  ``name`` it LISTS what the other apps have (names only, no card). A server
  whose config leaves a value to be typed in is refused with where to type it
  (the Tools page's import asks for it there), never asked for in chat.
* ``channel_remove`` — the Notifications page's Remove. The destination's
  keys (vault rows, and a webhook address kept in its settings) are copied
  encrypted for the Undo first, the same way.
* ``channel_alerts`` — which alerts a destination gets (its ``events`` list,
  the Notifications page's boxes). A destination can choose alert TYPES only:
  a finished task is sent whether it worked or not, and the tool says so.
* the email destination type is ``channel_connect``'s (``settings.records``);
  its password comes through the secure card.

A credential is NEVER a tool argument, NEVER in a log row, NEVER on a card.
Words a person reads say "app" and "destination" — never the engine's words.

Snapshots and restores are BLOCKING (config.toml, the vault, an app's start);
the tools run them through ``asyncio.to_thread``.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import re
from contextvars import ContextVar
from functools import lru_cache
from types import SimpleNamespace
from typing import Any

from ..core.ids import new_id
from ..tools.base import SpendCheck, ToolContext, ToolResult
from . import records as R

log = logging.getLogger("ironjarvis.settings")

#: The tools this module adds and their default permission (mirrored, row
#: for row, in ``records.RECORD_TOOL_TIERS`` and ``core.config``'s table).
APPS_TOOL_TIERS: dict[str, str] = {
    "app_switch": "ask",
    "app_disconnect": "ask",
    "app_import": "ask",
    "channel_remove": "ask",
    "channel_alerts": "ask",
}

#: The record kinds this module adds to ``records._SNAP`` / ``records.restore``.
APP_REMOVED = "app_removed"
DESTINATION_REMOVED = "destination_removed"
DESTINATION_ALERTS = "destination_alerts"
#: v1.435.0 review: an app's switch has its OWN kind (it used ``app``, whose
#: restore puts the whole stored row back — so an Undo re-added an app the
#: user had disconnected since, without its keys, and wrote over an edit).
APP_SWITCHED = "app_switched"
KINDS = (APP_REMOVED, DESTINATION_REMOVED, DESTINATION_ALERTS, APP_SWITCHED)

#: Where a key is kept for an Undo (the ``settings.credentials`` prefix).
BACKUP_PREFIX = "__undo__"

#: A removal's backup plan, set by ``capture_undo`` and read by ``execute``
#: (the same task; ``records._CHANGE_ID`` rides the same way).
_PLAN: ContextVar[dict[str, Any] | None] = ContextVar("ij_apps_removal_plan", default=None)

#: Plain fields of a destination's settings that ARE credentials (a webhook
#: address: whoever holds it posts as the destination). Kept out of the
#: journal: a removal copies them into the vault, encrypted.
CREDENTIAL_FIELDS = ("webhook_url",)

#: v1.435.0 review: what a removal says about the keys it kept — the copy
#: exists (encrypted) and the reply must not read as "the key is gone from
#: this PC". It expires with the change's history (core.db.prune_events).
KEPT_FOR_UNDO = (
    "A copy of its keys is kept, encrypted, only so the Undo on the reply can bring it back; "
    "it is deleted when the Undo is used or the change leaves the activity history."
)


def _kept_words(written: list[str]) -> str:
    return KEPT_FOR_UNDO if written else "The Undo on the reply brings it back."


#: The one sentence a removal says when no Undo could be prepared.
NO_UNDO_PLAN = (
    "Iron Jarvis couldn't keep a copy to undo this, so nothing was removed. "
    "Try again, or remove it on its page."
)


def _fold(text: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(text or "").lower())


def _backup_name() -> str:
    return f"{BACKUP_PREFIX}{new_id('bak')}"


# =========================================================================== #
# Apps
# =========================================================================== #
def _servers(platform: Any) -> list[dict[str, Any]]:
    return [s for s in list(getattr(platform.config, "mcp_servers", None) or []) if isinstance(s, dict)]


def resolve_app(platform: Any, ref: Any) -> tuple[str, str, Any]:
    """``(stored name, display name, catalog card or None)`` for the app the
    user named — a stored name ("quickbooks"), a catalog id ("github"), its
    name or an alias ("GitHub", "Outlook"). The stored name is "" when no app
    of that name is added. BLOCKING-safe (config reads only); never raises."""
    raw = str(ref or "").strip()
    if not raw:
        return "", "", None
    try:
        from ..connectors.catalog import CATALOG, get_connector
        from ..connectors.service import _configured_name
    except Exception:  # noqa: BLE001
        return "", raw, None
    servers = _servers(platform)
    fold = _fold(raw)
    stored = next((str(s.get("name")) for s in servers if str(s.get("name") or "") == raw), "")
    if not stored:
        folded = [str(s.get("name")) for s in servers if _fold(s.get("name")) == fold and fold]
        if len(folded) > 1:
            # v1.435.0 review: two added apps spelled alike ("My-App",
            # "my_app") — never the first one silently; the refusal lists
            # both and the model names one exactly.
            return "", raw, None
        stored = folded[0] if folded else ""
    conn = get_connector(stored) if stored else None
    if conn is None:
        conn = get_connector(raw.lower()) or next(
            (c for c in CATALOG if _fold(c.id) == fold or _fold(c.name) == fold), None
        )
    if conn is None:
        try:
            from ..connectors.app_names import apps_named

            hits = apps_named(raw)
        except Exception:  # noqa: BLE001
            hits = []
        conn = get_connector(hits[0]) if len(hits) == 1 else None
    if not stored and conn is not None:
        try:
            stored = _configured_name(platform, conn.id) or ""
        except Exception:  # noqa: BLE001
            stored = ""
    display = conn.name if conn is not None else (stored or raw)
    return stored, display, conn


def _short_name(display: str) -> str:
    """"Microsoft 365 (Outlook, Calendar, OneDrive)" → "Microsoft 365"."""
    return str(display or "").split(" (", 1)[0].strip() or str(display or "")


def _no_such_app(platform: Any, ref: str) -> str:
    names = [str(s.get("name")) for s in _servers(platform) if s.get("name")]
    alike = [n for n in names if _fold(n) == _fold(ref) and _fold(ref)]
    if len(alike) > 1:
        return f"Several added apps are spelled like {ref!r} ({', '.join(alike)}): name one exactly."
    have = f" The apps added are: {', '.join(names)}." if names else " No apps are added yet."
    return f"There is no app called {ref!r} added in Iron Jarvis.{have}"


def _not_switchable(conn: Any) -> str:
    """The words for a catalog app that is not an added app (one that signs
    in through the browser, or a key) — where it IS changed. "" otherwise."""
    from ..connectors.app_names import APPS_PLACE

    if conn is None:
        return ""
    if conn.connect_via == "oauth":
        return (
            f"{conn.name} signs in through the browser and has no on/off switch here: "
            f"disconnect it on its card in {APPS_PLACE}."
        )
    if conn.connect_via == "api_key":
        return f"{conn.name} is a key, not an app with a switch: remove its key on its card in {APPS_PLACE}."
    return ""


def snap_app_removal(platform: Any, name: str) -> dict[str, Any] | None:
    """What an Undo of ``app_disconnect`` needs, NAMES only: the stored row,
    the vault rows it owns (each with the backup name the removal will copy
    it to), and its manifest's text (not a secret: tool names and shapes)."""
    cfg = R.snap_app(platform, name)
    if cfg is None:
        return None
    from ..mcp.pack_door import vault_refs
    from ..mcp.secrets import pack_vault_names

    names = list(pack_vault_names(getattr(platform, "secrets", None), name, cfg))
    for ref in vault_refs(cfg):
        if ref not in names:
            names.append(ref)
    manifest = None
    try:
        from ..mcp.manifest import PackManifest

        path = PackManifest(platform.config.home).path(name)
        if path.is_file():
            manifest = path.read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001 — a manifest is a convenience, not a key
        manifest = None
    secrets = getattr(platform, "secrets", None)
    return {
        "row": cfg,
        "vault": {n: _backup_name() for n in names},
        "kinds": _kinds_of(secrets, names),
        "manifest": manifest,
    }


def _kinds_of(secrets: Any, names: list[str]) -> dict[str, list[str]]:
    """v1.435.0 review: each vault row's KIND and description (metadata only,
    never a value) so an Undo puts a key back exactly as it was — it used to
    come back as "password" with no description. Never raises."""
    try:
        rows = secrets.list() if secrets is not None else []
    except Exception:  # noqa: BLE001
        rows = []
    wanted = set(names)
    return {
        str(r.get("name")): [str(r.get("kind") or "generic"), str(r.get("description") or "")]
        for r in rows
        if isinstance(r, dict) and str(r.get("name") or "") in wanted
    }


def _copy_keys(secrets: Any, mapping: dict[str, str]) -> list[str]:
    """Copy each vault row in ``mapping`` (name → backup name) to its backup,
    encrypted. Returns the backups written. Raises when the vault refuses a
    write (the caller removes nothing then). Never logs a value."""
    written: list[str] = []
    try:
        for vname, backup in mapping.items():
            value = secrets.get(vname)
            if not value:
                continue
            secrets.set(backup, value, kind="password", description="kept for an Undo")
            written.append(backup)
    except Exception:
        _drop(secrets, written)
        raise
    return written


def _drop(secrets: Any, names: list[str]) -> None:
    for n in names:
        try:
            secrets.delete(n)
        except Exception:  # noqa: BLE001 — a leftover backup is harmless
            pass


def _put_keys_back(
    secrets: Any, mapping: dict[str, str], kinds: dict[str, Any] | None = None
) -> tuple[list[str], list[str]]:
    """Write each backed-up value back under its own name, with its own kind
    and description. Returns ``(lost, newer)``: the names whose backup is gone
    (an earlier Undo used it, or it was never written), and the names that
    hold a value AGAIN — set since the removal (another app reads the same
    row, or the user saved it again). A newer value is never written over
    (v1.435.0 review: the old Undo put the OLD key back over it)."""
    lost: list[str] = []
    newer: list[str] = []
    for vname, backup in (mapping or {}).items():
        if secrets.get(vname):
            newer.append(vname)
            continue
        value = secrets.get(backup) if backup else None
        if value is None:
            lost.append(vname)
            continue
        meta = list((kinds or {}).get(vname) or [])
        kind = str(meta[0]) if meta else ("api_key" if "key" in vname.lower() else "password")
        description = str(meta[1]) if len(meta) > 1 else ""
        secrets.set(vname, value, kind=kind, description=description)
    return lost, newer


#: What an Undo says about keys it could not, or would not, put back.
LOST_KEY_WORDS = " (one of its keys was not kept: add it again on {where})"
NEWER_KEY_WORDS = " (one of its keys was saved again since, so the newer one was kept)"


def restore_app_removal(platform: Any, name: str, prior: dict[str, Any] | None) -> str:
    """Undo of ``app_disconnect``: the keys, the manifest and the row go back,
    then the app starts. BLOCKING."""
    if not isinstance(prior, dict) or not isinstance(prior.get("row"), dict):
        raise R.RecordError("this change kept nothing to bring back")
    if R.snap_app(platform, name) is not None:
        raise R.RecordError(f"An app called {name!r} is added again, so there is nothing to bring back.")
    secrets = platform.secrets
    mapping = dict(prior.get("vault") or {})
    lost, newer = _put_keys_back(secrets, mapping, prior.get("kinds"))
    text = prior.get("manifest")
    if isinstance(text, str) and text:
        try:
            from ..mcp.manifest import PackManifest

            path = PackManifest(platform.config.home).path(name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        except Exception:  # noqa: BLE001 — the next start writes a fresh one
            log.warning("app %s: its manifest could not be put back", name)
    from ..mcp.pack_door import install_config

    install_config(platform, copy.deepcopy(prior["row"]), load=True)
    _drop(secrets, [b for v, b in mapping.items() if v not in lost])
    out = f"undo: brought back the app '{name}'"
    if lost:
        out += LOST_KEY_WORDS.format(where="its card")
    if newer:
        out += NEWER_KEY_WORDS
    return out


def restore_app_switch(platform: Any, name: str, prior: dict[str, Any] | None) -> str:
    """Undo of ``app_switch``: the switch goes back to where it was — only
    while the app is still there and nothing else on it changed since (an
    Undo never re-adds an app the user disconnected since, and never writes
    over an edit). BLOCKING (turning an app on connects it)."""
    if not isinstance(prior, dict):
        raise R.RecordError("this change kept nothing to bring back")
    now = R.snap_app(platform, name)
    if now is None:
        raise R.RecordError(f"The app {name!r} was disconnected since, so there is nothing to switch back.")

    def _rest(row: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in row.items() if k != "enabled"}

    if _rest(now) != _rest(prior):
        raise R.RecordError(
            f"The app {name!r} was changed since, so this Undo would undo that too: switch it on its row in Settings › Tools."
        )
    from ..mcp.pack_door import install_config, set_pack_enabled
    from ..mcp.tools import pack_enabled

    want = pack_enabled(prior)
    if pack_enabled(now) != want:
        set_pack_enabled(platform, name, want)
    if R.snap_app(platform, name) != prior:
        # The row as it was, key for key (an explicit ``enabled = true``).
        install_config(platform, copy.deepcopy(prior), load=False)
    return f"undo: switched the app '{name}' {'on' if want else 'off'} again"


# =========================================================================== #
# Destinations
# =========================================================================== #
def _configured_channels(platform: Any) -> dict[str, Any]:
    return dict(((platform.config.comm or {}).get("channels")) or {})


def resolve_destination(platform: Any, ref: Any) -> tuple[str, str]:
    """``(name, problem)``: the destination the user named — its own name,
    or its type when exactly one destination has that type ("slack"). The
    problem is one sentence when there is no such one or it is ambiguous."""
    raw = str(ref or "").strip()
    channels = _configured_channels(platform)
    if raw in channels:
        return raw, ""
    fold = _fold(raw)
    by_name = [n for n in channels if _fold(n) == fold and fold]
    if len(by_name) == 1:
        return by_name[0], ""
    kind = {"email": "email", "mail": "email", "text": "telegram"}.get(fold, fold)
    by_type = [n for n, c in channels.items() if isinstance(c, dict) and _fold(c.get("type")) == kind and kind]
    if len(by_type) == 1:
        return by_type[0], ""
    if len(by_type) > 1:
        return "", f"There are {len(by_type)} {raw} destinations ({', '.join(sorted(by_type))}): say which one."
    try:
        live = list(platform.notifier.channels())
    except Exception:  # noqa: BLE001
        live = []
    if raw in live or fold in {_fold(n) for n in live}:
        return "", f"{raw!r} is built in: it can't be removed or changed here."
    have = ", ".join(sorted(channels)) if channels else ""
    tail = f" The destinations added are: {have}." if have else " No destinations are added yet."
    return "", f"There is no destination called {raw!r}.{tail}"


def snap_destination_removal(platform: Any, name: str) -> dict[str, Any] | None:
    """What an Undo of ``channel_remove`` needs. The settings WITHOUT their
    credentials (a webhook address is a key); every vault row the settings
    name (``<field>_secret``) and every credential field, each with the
    backup name the removal copies it to."""
    cfg = R.snap_channel(platform, name)
    if cfg is None:
        return None
    clean = {k: v for k, v in cfg.items() if k not in CREDENTIAL_FIELDS}
    vault = {
        str(v): _backup_name()
        for k, v in cfg.items()
        if str(k).endswith("_secret") and isinstance(v, str) and v
    }
    plain = {k: _backup_name() for k in CREDENTIAL_FIELDS if cfg.get(k)}
    kinds = _kinds_of(getattr(platform, "secrets", None), list(vault))
    return {"cfg": clean, "vault": vault, "plain": plain, "kinds": kinds}


def remove_destination(platform: Any, name: str) -> tuple[bool, dict[str, Any] | None]:
    """THE removal of a destination — the Notifications page's Remove
    (``DELETE /comm/channels/{name}``) and ``channel_remove`` both: the live
    destination goes, its settings go (persisted), every vault row they name
    goes, and a Slack listener stops. Returns ``(was live, its settings)``.
    BLOCKING."""
    removed_live = bool(platform.notifier.remove_channel(name))
    cfg = R.snap_channel(platform, name)
    if cfg is not None:
        R.apply_channel(platform, name, None)
        for key, val in cfg.items():
            if str(key).endswith("_secret") and isinstance(val, str) and val:
                try:
                    platform.secrets.delete(val)
                except Exception:  # noqa: BLE001 — a leftover row is not a failed removal
                    pass
    return removed_live, cfg


def restore_destination_removal(platform: Any, name: str, prior: dict[str, Any] | None) -> str:
    if not isinstance(prior, dict) or not isinstance(prior.get("cfg"), dict):
        raise R.RecordError("this change kept nothing to bring back")
    if R.snap_channel(platform, name) is not None:
        raise R.RecordError(f"A destination called {name!r} is added again, so there is nothing to bring back.")
    secrets = platform.secrets
    vault = dict(prior.get("vault") or {})
    plain = dict(prior.get("plain") or {})
    lost, newer = _put_keys_back(secrets, vault, prior.get("kinds"))
    cfg = copy.deepcopy(prior["cfg"])
    for field_name, backup in plain.items():
        value = secrets.get(backup)
        if value is None:
            lost.append(field_name)
        else:
            cfg[field_name] = value
    R.apply_channel(platform, name, cfg)
    _drop(secrets, [b for v, b in vault.items() if v not in lost] + [b for f, b in plain.items() if f not in lost])
    out = f"undo: brought back the destination '{name}'"
    if lost:
        out += LOST_KEY_WORDS.format(where="the Notifications page")
    if newer:
        out += NEWER_KEY_WORDS
    return out


# --- alerts -----------------------------------------------------------------
#: Each alert a destination can get, in the Notifications page's own words
#: (``NotificationsPage.EVENT_LABELS``; tests/test_apps_destinations_v14350
#: pins the two tables equal, key for key and word for word).
ALERT_WORDS: dict[str, str] = {
    "review.requested": "A review needs you",
    "approval.requested": "An agent asks permission to use a tool",
    "session.completed": "A task finished",
    "workflow.completed": "A workflow finished",
    "provider.failed": "A model failed",
    "provider.failover": "A model failed over",
    "autonomy.executed": "Autonomy acted on its own",
    "skill.proposal_created": "Skill suggestions",
    "goal.satisfied": "A goal was achieved",
    "goal.tripped": "A goal hit its safety breaker",
    "goal.iteration_refused": "A goal run was held back (budget or state)",
    "build.night_summary": "The night shift's summary (one message a night)",
    "build.ci_done": "Your pull request's checks: fixed, or Iron Jarvis gave up",
    # v1.446.0: a rule that matched but could not start its action.
    "reflex.failed": "A rule couldn't start",
}
#: The alerts that are only ever bad news — what "only send me failures"
#: can choose. A finished task or workflow is sent whether it worked or not.
PROBLEM_ALERTS = ("provider.failed", "goal.tripped", "goal.iteration_refused", "reflex.failed")
#: What a destination still cannot do — said whenever a finished task or
#: workflow is left out, so the model never promises "only the failed ones".
ONLY_FAILED_NOT_YET = (
    "A destination gets alert TYPES: a finished task or workflow is sent whether it worked or not, "
    "so leaving them out also leaves out the ones that failed."
)


#: v1.435.0 review: what an alert choice does NOT narrow, said whenever a
#: destination's alerts are narrowed. Read off the senders: a schedule's own
#: result line goes to every destination unless the schedule names its own
#: (platform._run_scheduled), a workflow's notify step to every destination
#: (workflows/engine), and neither looks at a destination's alert list — so
#: "only failures" would otherwise read as true while every scheduled run's
#: result still arrived there.
STILL_SENT = (
    "A schedule's own result message and a workflow's notify step are not alert types: "
    "they still go to every destination unless that schedule or workflow names its own."
)


def _wanted_alerts(args: dict[str, Any]) -> tuple[list[str], str]:
    """``(the alert list to keep, problem)`` from a ``channel_alerts`` call:
    ``[]`` = every alert. The ONE reading of the call, for its ``execute``
    and for the ``now`` its Undo checks."""
    if (args or {}).get("all") is True:
        return [], ""
    raw = (args or {}).get("alerts")
    if not isinstance(raw, list) or not raw:
        return [], "Choose at least one alert in `alerts`, or set `all` to true."
    unknown = [str(a) for a in raw if str(a) not in ALERT_WORDS]
    if unknown:
        return [], (
            f"Not an alert a destination can get: {', '.join(unknown)}. The alerts: {', '.join(ALERT_WORDS)}."
        )
    wanted = list(dict.fromkeys(str(a) for a in raw))
    return ([] if set(wanted) == set(ALERT_WORDS) else wanted), ""


def _alerts_words(events: Any) -> str:
    if not events:
        return "every alert"
    items = [ALERT_WORDS.get(str(e), str(e)) for e in events]
    return f"{len(items)} alert{'s' if len(items) != 1 else ''}: " + "; ".join(items)


def snap_destination_alerts(platform: Any, name: str) -> dict[str, Any] | None:
    """Only the alert list (never a credential): its Undo puts the list back
    on the destination as it is then."""
    cfg = R.snap_channel(platform, name)
    if cfg is None:
        return None
    return {"events": list(cfg.get("events") or [])}


def restore_destination_alerts(platform: Any, name: str, prior: dict[str, Any] | None, now: str = "") -> str:
    """Undo of ``channel_alerts``. ``now`` is the alert list the change set
    (JSON); when the destination's list is no longer that, it was changed
    since and the Undo refuses rather than write over it (v1.435.0 review)."""
    cfg = R.snap_channel(platform, name)
    if cfg is None:
        raise R.RecordError(f"The destination {name!r} is no longer there, so its alerts can't be put back.")
    if now:
        try:
            set_then = list(json.loads(now))
        except (TypeError, ValueError):
            set_then = None
        if set_then is not None and list(cfg.get("events") or []) != set_then:
            raise R.RecordError(
                f"The alerts of the destination {name!r} were changed since, so this Undo would undo that too: "
                "choose them on the Notifications page."
            )
    events = list((prior or {}).get("events") or [])
    if events:
        cfg["events"] = events
    else:
        cfg.pop("events", None)
    R.apply_channel(platform, name, cfg)
    return f"undo: put back the alerts of the destination '{name}'"


SNAP = {
    APP_REMOVED: snap_app_removal,
    DESTINATION_REMOVED: snap_destination_removal,
    DESTINATION_ALERTS: snap_destination_alerts,
    APP_SWITCHED: lambda platform, name: R.snap_app(platform, name),
}


def restore_kind(platform: Any, record: str, name: str, prior: Any, now: str = "") -> str:
    """``records.restore`` for this module's kinds. BLOCKING."""
    if record == APP_REMOVED:
        return restore_app_removal(platform, name, prior)
    if record == DESTINATION_REMOVED:
        return restore_destination_removal(platform, name, prior)
    if record == DESTINATION_ALERTS:
        return restore_destination_alerts(platform, name, prior, now)
    if record == APP_SWITCHED:
        return restore_app_switch(platform, name, prior)
    raise R.RecordError(f"unknown record kind {record!r}")


def backups_named(record: str, prior: Any) -> list[str]:
    """The ``__undo__`` vault rows a journal row of ``record`` keeps (v1.435.0
    review): read by ``core.db.prune_events`` so a backup expires with the
    Undo that could use it — a disconnected app's key must not outlive the
    change's history in the vault. Names only; never raises."""
    if record not in (APP_REMOVED, DESTINATION_REMOVED) or not isinstance(prior, dict):
        return []
    out: list[str] = []
    for key in ("vault", "plain"):
        for backup in dict(prior.get(key) or {}).values():
            if isinstance(backup, str) and backup.startswith(BACKUP_PREFIX):
                out.append(backup)
    return out


# =========================================================================== #
# Tools
# =========================================================================== #
_NOTHING = SpendCheck(ask=False, cost_note="changes nothing by itself")


class _RemovalTool(R._RecordTool):
    """A removal keeps an Undo plan (backup names) for its ``execute``."""

    async def capture_undo(self, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any] | None:
        _PLAN.set(None)
        desc = await super().capture_undo(args, ctx)
        if desc is None:
            return None
        try:
            meta = json.loads(desc.get("pre_inline") or "{}")
        except (TypeError, ValueError):
            return None
        if not isinstance(meta.get("prior"), dict):
            return None
        _PLAN.set({"name": meta.get("name"), "prior": meta["prior"]})
        return desc

    def _plan_for(self, name: str) -> dict[str, Any] | None:
        plan = _PLAN.get()
        _PLAN.set(None)
        if not plan or plan.get("name") != name:
            return None
        return plan.get("prior")


# --- app_switch ------------------------------------------------------------


class AppSwitchTool(R._RecordTool):
    name = "app_switch"
    RECORD = APP_SWITCHED
    description = (
        "Turn an app the user added on or off (\"turn off the GitHub app\"): `app` is its name "
        "(github, notion, quickbooks, …), `on` true or false. Off keeps the app and its keys and "
        "takes its tools away until it is turned back on. The user approves on a card; the change "
        "has an Undo."
    )
    input_schema = {
        "type": "object",
        "properties": {"app": {"type": "string"}, "on": {"type": "boolean"}},
        "required": ["app", "on"],
    }

    def _target(self, args: dict[str, Any]) -> str:
        return str(args.get("app") or "").strip()

    def _resolve(self, args: dict[str, Any]) -> str:
        return resolve_app(self.platform, self._target(args))[0]

    async def spend_check(self, args: dict[str, Any], ctx: ToolContext) -> SpendCheck | None:
        """No card for a call that changes nothing: an app that is not added,
        or one already in the state asked for (the v1.227.0 rule)."""
        try:
            stored, _d, _c = await asyncio.to_thread(resolve_app, self.platform, self._target(args))
            if not stored:
                return _NOTHING
            row = await asyncio.to_thread(R.snap_app, self.platform, stored)
            from ..connectors.locked_pack import unlocked_copy
            from ..mcp.tools import pack_enabled

            on = (args or {}).get("on")
            if row is None or not isinstance(on, bool) or pack_enabled(row) == on:
                return _NOTHING
            if on and unlocked_copy(row):
                return _NOTHING  # refused by `execute`: never started
        except Exception:  # noqa: BLE001 — unsure: the ordinary card
            return None
        return None

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        ref = self._target(args)
        if not isinstance(args.get("on"), bool):
            return ToolResult(ok=False, error="Say `on` true to turn the app on, or false to turn it off.")
        on = bool(args["on"])
        stored, display, conn = await asyncio.to_thread(resolve_app, self.platform, ref)
        name = _short_name(display)
        if not stored:
            return ToolResult(ok=False, error=_not_switchable(conn) or await asyncio.to_thread(_no_such_app, self.platform, ref))
        before = await asyncio.to_thread(R.snap_app, self.platform, stored)
        from ..mcp.tools import pack_enabled

        if before is not None and pack_enabled(before) == on:
            return ToolResult(ok=True, output=f"{name} is already {'on' if on else 'off'}; nothing changed.")
        if on:
            from ..connectors.locked_pack import locked_elsewhere_words, unlocked_copy

            copy_of = unlocked_copy(before or {})
            if copy_of:
                # v1.428.0: Iron Jarvis never starts a copy of a locked app.
                return ToolResult(ok=False, error=locked_elsewhere_words(copy_of))
        from ..mcp.pack_door import PackDoorError, set_pack_enabled

        try:
            out = await asyncio.to_thread(set_pack_enabled, self.platform, stored, on)
        except PackDoorError:
            return ToolResult(ok=False, error=await asyncio.to_thread(_no_such_app, self.platform, ref))
        if on:
            loaded = int(out.get("tools_loaded") or 0)
            state = f"on, {loaded} tool{'s' if loaded != 1 else ''}" if loaded else "on, but it did not start"
            output = (
                f"{name} is on again ({loaded} tool{'s' if loaded != 1 else ''})."
                if loaded
                else f"{name} is switched on, but it did not start{R._why_not_started(stored)}"
            )
        else:
            state = "off"
            output = f"{name} is off. It keeps its keys; its tools are gone until it is turned back on."
        return ToolResult(
            ok=True,
            output=output,
            data={
                "record_change": self._card(
                    f"App switched {'on' if on else 'off'}", name, "off" if on else "on", state, f"app.{stored}"
                )
            },
        )


# --- app_disconnect --------------------------------------------------------


class AppDisconnectTool(_RemovalTool):
    name = "app_disconnect"
    RECORD = APP_REMOVED
    description = (
        "Disconnect an app the user added (\"disconnect Notion\"): `app` is its name. Its tools "
        "and saved keys go; the Undo on the reply brings it back with its keys. To pause an app "
        "and keep it, use app_switch instead. The user approves on a card."
    )
    input_schema = {"type": "object", "properties": {"app": {"type": "string"}}, "required": ["app"]}

    def _target(self, args: dict[str, Any]) -> str:
        return str(args.get("app") or "").strip()

    def _resolve(self, args: dict[str, Any]) -> str:
        return resolve_app(self.platform, self._target(args))[0]

    async def spend_check(self, args: dict[str, Any], ctx: ToolContext) -> SpendCheck | None:
        try:
            stored, _d, _c = await asyncio.to_thread(resolve_app, self.platform, self._target(args))
        except Exception:  # noqa: BLE001
            return None
        return None if stored else _NOTHING

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        ref = self._target(args)
        stored, display, conn = await asyncio.to_thread(resolve_app, self.platform, ref)
        if not stored:
            return ToolResult(ok=False, error=_not_switchable(conn) or await asyncio.to_thread(_no_such_app, self.platform, ref))
        plan = self._plan_for(stored)
        if plan is None:
            return ToolResult(ok=False, error=NO_UNDO_PLAN)
        name = _short_name(display)
        secrets = self.platform.secrets
        try:
            written = await asyncio.to_thread(_copy_keys, secrets, dict(plan.get("vault") or {}))
        except Exception:  # noqa: BLE001 — names only
            return ToolResult(
                ok=False,
                error=f"Iron Jarvis couldn't keep a copy of {name}'s keys for an Undo, so nothing was removed.",
            )
        try:
            from ..connectors.service import _disconnect_mcp_server

            await asyncio.to_thread(_disconnect_mcp_server, self.platform, stored)
        except Exception as exc:  # noqa: BLE001
            await asyncio.to_thread(_drop, secrets, written)
            return ToolResult(ok=False, error=f"{name} could not be disconnected ({type(exc).__name__}).")
        return ToolResult(
            ok=True,
            output=f"Disconnected {name}: its tools and saved keys are gone. {_kept_words(written)}",
            data={"record_change": self._card("App disconnected", name, "connected", None, f"app.{stored}")},
        )


# --- app_import ------------------------------------------------------------


def _find_server(platform: Any, name: str, source: str) -> tuple[Any, Any, str]:
    """``(scan, spec, problem)`` for server ``name`` in another app's config
    — the one ``source`` names, else the only readable one that has it.
    BLOCKING (reads files, read-only). Never a value in ``problem``."""
    from ..mcp.import_sources import KNOWN_SOURCES, read_source, scan

    fold = _fold(name)
    places = ", ".join(f"{d.label} ({d.source})" for d in KNOWN_SOURCES)
    if source:
        item = read_source(platform, source)
        if item is None:
            return None, None, f"There is no app called {source!r} that Iron Jarvis reads. It reads: {places}."
        if not item.readable:
            return None, None, item.note or f"{item.label}'s settings were not found on this PC."
        scans = [item]
    else:
        scans = [s for s in scan(platform) if s.readable]
    found = [(s, spec) for s in scans for spec in s.specs if spec.name and _fold(spec.name) == fold]
    if not found:
        listed = "; ".join(f"{s.label}: {', '.join(sp.name for sp in s.specs if sp.name)}" for s in scans if s.specs)
        tail = f" What they have: {listed}." if listed else " None of them lists any server."
        return None, None, f"No other app on this PC has a server called {name!r}.{tail}"
    distinct = {(sp.transport, sp.command, tuple(sp.args), sp.url) for _s, sp in found}
    if len(distinct) > 1:
        where = ", ".join(f"{s.label} ({s.source})" for s, _sp in found)
        return None, None, f"Several apps have a server called {name!r} set up differently ({where}): say which one."
    return found[0][0], found[0][1], ""


def list_other_apps(platform: Any) -> str:
    """Names only: each readable app's servers, and which are added already.
    BLOCKING (reads files, read-only)."""
    from ..mcp.import_sources import scan

    have = {_fold(s.get("name")) for s in _servers(platform)}
    lines: list[str] = []
    for item in scan(platform):
        if not item.readable or not item.specs:
            continue
        names = [
            f"{sp.name}{' (already added)' if _fold(sp.name) in have else ''}{' (switched off there)' if sp.disabled else ''}"
            for sp in item.specs
            if sp.name
        ]
        if names:
            lines.append(f"{item.label} ({item.source}): {', '.join(names)}")
    if not lines:
        return "No other app on this PC lists a server Iron Jarvis can bring over."
    return (
        "Servers the user's other apps have (call app_import with `name`, and `source` when "
        "two apps list the same name):\n" + "\n".join(lines)
    )


#: v1.435.0 review: the most an import's card shows of what it starts.
PREVIEW_CHARS = 300


def import_preview(found: Any, spec: Any) -> str:
    """The import card's sentence (v1.435.0 review): "Adds quickbooks from
    Claude Desktop; it starts the program: npx -y @acme/qb (its keys come
    from Claude Desktop's settings)." The command line as the add door
    stores it (``mcp.secrets.split_args``: the value after a secret-named
    flag, a password in an address — masked), then masked again; never an
    environment value or a header. "" when it cannot be said (the ordinary
    card still asks). Never raises."""
    try:
        from ..mcp.secrets import split_args, split_url

        transport = str(getattr(spec, "transport", "") or "").lower()
        if transport in ("http", "sse") or getattr(spec, "url", ""):
            shown_url, _ = split_url(getattr(spec, "url", "") or "")
            what = f"it connects to {shown_url}"
        else:
            shown_args, _ = split_args(list(getattr(spec, "args", None) or []))
            line = " ".join([str(getattr(spec, "command", "") or ""), *[str(a) for a in shown_args]]).strip()
            if not line:
                return ""
            what = f"it starts the program: {line}"
        what = " ".join(str(R.masked_args(what)).split())
        if len(what) > PREVIEW_CHARS:
            what = what[: PREVIEW_CHARS - 1].rstrip() + "…"
        label = str(getattr(found, "label", "") or "another program")
        return f"Adds {spec.name} from {label}; {what} (its keys come from {label}'s settings)."
    except Exception:  # noqa: BLE001
        return ""


#: A config that leaves a value to be TYPED (``${input:token}``): never typed in chat.
IMPORT_NEEDS_VALUES = (
    "{name}'s settings in {app} leave {keys} to be typed in. Add it from Settings › Tools: its "
    "\"Add\" button reads your other apps and asks for {them} there — never type it in chat."
)


def _import_state(platform: Any, name: str, entry: dict[str, Any]) -> tuple[str, str]:
    from ..mcp.tools import STATE_OFF

    if entry.get("state") == STATE_OFF:
        return "switched off", (
            "It is switched off in the app it came from, so it is added switched off: "
            "app_switch turns it on."
        )
    loaded = int(entry.get("tools_loaded") or 0)
    if loaded:
        return f"{loaded} tool{'s' if loaded != 1 else ''}", ""
    return R.NOT_STARTED_STATE, f"It did not start{R._why_not_started(name)}"


class AppImportTool(R._RecordTool):
    name = "app_import"
    RECORD = "app"
    description = (
        "Bring over an app (a server) the user already runs in another program on this PC — Claude "
        "Desktop, Claude Code, Codex, Cursor, VS Code, Windsurf, Gemini CLI (\"add the QuickBooks "
        "server I use in Claude Desktop\"). Call it with no `name` first to list what those programs "
        "have (no card); then with `name` (and `source` when two list the same name). Its keys come "
        "straight from that program's settings — never ask for one in chat. The user approves on a "
        "card; Undo removes it again."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "the server's name as the list shows it"},
            "source": {
                "type": "string",
                "description": "where it is: claude-desktop, claude-code, codex, cursor, vscode-mcp, …",
            },
        },
    }

    def _target(self, args: dict[str, Any]) -> str:
        return str(args.get("name") or "").strip()

    def _resolve(self, args: dict[str, Any]) -> str:
        name = self._target(args)
        if not name:
            return ""
        _scan, spec, _problem = _find_server(self.platform, name, str(args.get("source") or "").strip())
        return spec.name if spec is not None else ""

    async def spend_check(self, args: dict[str, Any], ctx: ToolContext) -> SpendCheck | None:
        """No card for a call that adds nothing: the list (no name), a server
        no other app has, one already added, or one whose settings leave a
        value to be typed in (each refused by ``execute`` in words)."""
        name = self._target(args)
        if not name:
            return _NOTHING
        try:
            found, spec, _p = await asyncio.to_thread(
                _find_server, self.platform, name, str((args or {}).get("source") or "").strip()
            )
            if spec is None or list(spec.asks):
                return _NOTHING
            if await asyncio.to_thread(R.snap_app, self.platform, spec.name) is not None:
                return _NOTHING
            from ..connectors.locked_pack import unlocked_copy

            if unlocked_copy(spec):
                return _NOTHING  # refused by the add door: never started
        except Exception:  # noqa: BLE001 — unsure: the ordinary card
            return None
        # v1.435.0 review — the plan's "preview card": an import STARTS a
        # program (or reaches an address) from another app's settings, so its
        # card says exactly what, every time — also when the page armed the
        # tool by name (arming alone would otherwise grant it). "Don't ask"
        # keeps its meaning (the user chose it); the POST lane, which cannot
        # show a card, refuses it.
        preview = import_preview(found, spec)
        if not preview:
            return None
        return SpendCheck(ask=True, words=preview, once_only=True, dont_ask_skips=True)

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        name = self._target(args)
        source = str(args.get("source") or "").strip()
        if not name:
            return ToolResult(ok=True, output=await asyncio.to_thread(list_other_apps, self.platform))
        found, spec, problem = await asyncio.to_thread(_find_server, self.platform, name, source)
        if spec is None:
            return ToolResult(ok=False, error=problem)
        asks = list(spec.asks)
        if asks:
            return ToolResult(
                ok=False,
                error=IMPORT_NEEDS_VALUES.format(
                    name=spec.name, app=found.label, keys=", ".join(asks), them="them" if len(asks) > 1 else "it"
                ),
            )
        from ..daemon.routes.agents import _apply_specs
        from ..mcp.pack_door import persist_for

        door = SimpleNamespace(platform=self.platform, _persist_config=persist_for(self.platform))
        result = await asyncio.to_thread(_apply_specs, door, [spec], [spec.name], {}, False, False)
        added = list(result.get("added") or [])
        if not added:
            why = next((str(s.get("why") or "") for s in result.get("skipped") or []), "") or "it was not added"
            return ToolResult(ok=False, error=f"{spec.name} from {found.label} was not added: {why}.")
        state, note = await asyncio.to_thread(_import_state, self.platform, spec.name, added[0])
        output = f"Added {spec.name} from {found.label} ({state}). Its keys came from {found.label}'s settings."
        if note:
            output += " " + note
        return ToolResult(
            ok=True,
            output=output,
            data={
                "record_change": self._card(
                    "App added", f"{spec.name} (from {found.label})", None, state, f"app.{spec.name}"
                )
            },
        )


# --- channel_remove --------------------------------------------------------


class ChannelRemoveTool(_RemovalTool):
    name = "channel_remove"
    RECORD = DESTINATION_REMOVED
    description = (
        "Remove a notification destination (\"stop sending to Slack\"): `name` is the "
        "destination's name, or its type when there is only one of that type. Its saved keys go "
        "with it; the Undo on the reply brings it back. To keep it and get fewer alerts, use "
        "channel_alerts. The user approves on a card."
    )
    input_schema = {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}

    def _resolve(self, args: dict[str, Any]) -> str:
        return resolve_destination(self.platform, self._target(args))[0]

    async def spend_check(self, args: dict[str, Any], ctx: ToolContext) -> SpendCheck | None:
        try:
            name, _p = await asyncio.to_thread(resolve_destination, self.platform, self._target(args))
        except Exception:  # noqa: BLE001
            return None
        return None if name else _NOTHING

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        name, problem = await asyncio.to_thread(resolve_destination, self.platform, self._target(args))
        if not name:
            return ToolResult(ok=False, error=problem)
        plan = self._plan_for(name)
        if plan is None:
            return ToolResult(ok=False, error=NO_UNDO_PLAN)
        cfg = await asyncio.to_thread(R.snap_channel, self.platform, name) or {}
        secrets = self.platform.secrets
        written: list[str] = []
        try:
            written = await asyncio.to_thread(_copy_keys, secrets, dict(plan.get("vault") or {}))
            for field_name, backup in dict(plan.get("plain") or {}).items():
                value = cfg.get(field_name)
                if value:
                    await asyncio.to_thread(secrets.set, backup, str(value), kind="password", description="kept for an Undo")
                    written.append(backup)
        except Exception:  # noqa: BLE001 — names only
            await asyncio.to_thread(_drop, secrets, written)
            return ToolResult(
                ok=False,
                error=f"Iron Jarvis couldn't keep a copy of the destination {name!r}'s keys for an Undo, so nothing was removed.",
            )
        await asyncio.to_thread(remove_destination, self.platform, name)
        kind = str(cfg.get("type") or "destination")
        return ToolResult(
            ok=True,
            output=f"Removed the {kind} destination '{name}': nothing is sent there now. {_kept_words(written)}",
            data={
                "record_change": self._card(
                    "Destination removed", f"Destination “{name}”", R._describe_channel(cfg), None, f"channel.{name}"
                )
            },
        )


# --- channel_alerts --------------------------------------------------------


class ChannelAlertsTool(R._RecordTool):
    name = "channel_alerts"
    RECORD = DESTINATION_ALERTS
    description = (
        "Choose which alerts a notification destination gets (\"only send me failures on "
        "Telegram\"): `name` is the destination (or its type when there is only one), `alerts` the "
        "alert types to keep, or `all` true for every alert. The types: "
        + "; ".join(f"{k} = {v}" for k, v in ALERT_WORDS.items())
        + ". Only bad news: "
        + ", ".join(PROBLEM_ALERTS)
        + ". A finished task or workflow is sent whether it worked or not — say so when it matters. "
        + STILL_SENT
        + " The user approves on a card; the change has an Undo."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "alerts": {"type": "array", "items": {"type": "string", "enum": sorted(ALERT_WORDS)}},
            "all": {"type": "boolean"},
        },
        "required": ["name"],
    }

    def _resolve(self, args: dict[str, Any]) -> str:
        return resolve_destination(self.platform, self._target(args))[0]

    def _now(self, args: dict[str, Any]) -> str:
        """The alert list this change sets (JSON), so its Undo can tell when
        the list was changed since (v1.435.0 review). "" when the call is
        refused anyway."""
        wanted, problem = _wanted_alerts(args)
        return "" if problem else json.dumps(wanted)

    async def spend_check(self, args: dict[str, Any], ctx: ToolContext) -> SpendCheck | None:
        try:
            name, _p = await asyncio.to_thread(resolve_destination, self.platform, self._target(args))
        except Exception:  # noqa: BLE001
            return None
        return None if name else _NOTHING

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        name, problem = await asyncio.to_thread(resolve_destination, self.platform, self._target(args))
        if not name:
            return ToolResult(ok=False, error=problem)
        wanted, problem = _wanted_alerts(args)
        if problem:
            return ToolResult(ok=False, error=problem)
        cfg = await asyncio.to_thread(R.snap_channel, self.platform, name)
        if cfg is None:
            return ToolResult(ok=False, error=f"There is no destination called {name!r}.")
        before = list(cfg.get("events") or [])
        if wanted:
            cfg["events"] = wanted
        else:
            cfg.pop("events", None)
        if before == wanted:
            return ToolResult(ok=True, output=f"The destination '{name}' already gets {_alerts_words(wanted)}; nothing changed.")
        await asyncio.to_thread(R.apply_channel, self.platform, name, cfg)
        output = f"The destination '{name}' now gets {_alerts_words(wanted)}."
        if wanted and not {"session.completed", "workflow.completed"} <= set(wanted):
            output += " " + ONLY_FAILED_NOT_YET
        elif wanted:
            output += " A finished task or workflow is sent whether it worked or not."
        if wanted:
            output += " " + STILL_SENT
        return ToolResult(
            ok=True,
            output=output,
            data={
                "record_change": self._card(
                    "Alerts changed", f"Destination “{name}”", _alerts_words(before), _alerts_words(wanted), f"channel.{name}"
                )
            },
        )


def apps_record_tools(platform: Any) -> list[Any]:
    return [
        AppSwitchTool(platform),
        AppDisconnectTool(platform),
        AppImportTool(platform),
        ChannelRemoveTool(platform),
        ChannelAlertsTool(platform),
    ]


# =========================================================================== #
# Arming: which of these a chat message asks for
# =========================================================================== #
#: The words an added app is called by beside its name ("the GitHub app",
#: "the QuickBooks server").
_TOOL_NOUN = r"(?:apps?|connectors?|extensions?|integrations?|plug-?ins?|add-?ons?|mcp\s+servers?|servers?)"
_DET = r"(?:(?:the|my|our|that|this|your)\s+)"
#: Who the thing is: a catalog app by its brand name ("Notion", "GitHub"),
#: a generic-named one with its noun ("the Box app"), or any word with the
#: noun ("the QuickBooks server").
_BRANDS = (
    r"git\s*hub|postgres(?:ql)?|sentry|slack|g-?mail|microsoft\s*365|office\s*365|m365|outlook"
    r"|notion|google\s*maps|google\s*drive|one\s*drive|drop\s*box|brave\s*search|stripe|pixio"
)
_THING = (
    r"(?:" + _DET + r"?(?:" + _BRANDS + r")(?:\s+" + _TOOL_NOUN + r")?"
    r"|" + _DET + r"?[\w&.-]{2,40}\s+" + _TOOL_NOUN + r")"
)
#: What may follow the thing for it to be the object of the request — never
#: another noun ("delete the GitHub issue", "remove the slack from the rope").
_AFTER = (
    r"(?=\s*(?:$|[.,!?;:)]|\b(?:and|then|please|too|now|again|for\s+now|for\s+a\s+(?:bit|while)"
    r"|until|so|from\s+(?:jarvis|iron\s+jarvis|chat|here|(?:my|the|your)\s+(?:apps?|connections?"
    r"|directory|tools?|connectors?|extensions?|list|setup)))\b))"
)
_SWITCH = re.compile(
    r"\b(?:(?:turn|switch|toggle)\s+(?:off|on)|disable|enable|pause|resume|unpause|deactivate|reactivate"
    r"|activate|stop\s+using|start\s+using)\s+" + _THING + _AFTER
    + r"|\b(?:turn|switch|toggle)\s+" + _THING + r"\s+(?:off|on|back\s+on)" + _AFTER,
    re.IGNORECASE,
)
_DISCONNECT = re.compile(
    r"\b(?:disconnect|uninstall|unlink|unhook|remove|delete|drop|forget|get\s+rid\s+of)\s+" + _THING + _AFTER,
    re.IGNORECASE,
)
#: The other programs whose servers can be brought over.
_OTHER_PROGRAM = (
    r"(?:claude\s+desktop|claude\s+code|codex|cursor|vs\s?code|visual\s+studio\s+code|windsurf"
    r"|gemini\s+cli)"
)
_IMPORT_VERB = r"(?:import|bring(?:\s+over)?|copy(?:\s+over)?|move|add|set\s*up|get|pull\s+in|reuse)"
#: "import my servers from Cursor", "add the QuickBooks server I use in
#: Claude Desktop". The span between is checked by :func:`_import_span_ok`.
_IMPORT_FROM = re.compile(
    r"\b" + _IMPORT_VERB + r"\b([^.?!\n]{0,80}?)\b(?:from|in|on)\s+(?:my\s+)?" + _OTHER_PROGRAM + r"\b",
    re.IGNORECASE,
)
#: "bring over my Claude Desktop tools", "import Cursor's servers".
_IMPORT_NAMED = re.compile(
    r"\b" + _IMPORT_VERB + r"\s+" + _DET + r"?(?:all\s+(?:of\s+)?(?:my\s+)?)?" + _OTHER_PROGRAM
    + r"(?:['’]s)?\s+(?:mcp\s+)?(?:servers?|tools?|apps?|extensions?|connectors?|integrations?|plug-?ins?)\b",
    re.IGNORECASE,
)
_TOOLISH = re.compile(r"\b(?:" + _TOOL_NOUN + r"|tools?|mcp)\b", re.IGNORECASE)
_NOT_A_SERVER = re.compile(
    r"\b(?:files?|text|code|snippets?|settings?|themes?|keybindings?|lines?|functions?|documents?"
    r"|data|csv|photos?|pictures?|images?|notes?|chats?|conversations?|history|projects?|folders?"
    r"|passwords?|keys?|tokens?|extensions?\s+list)\b",
    re.IGNORECASE,
)


#: What may follow the tool noun in the span: nothing, "over", or the
#: clause that says whose it is ("the server I use", "the tools that I set up").
_SPAN_TAIL_OK = re.compile(r"^(?:over|(?:that|which)\s+\w+|(?:i|we|you)\b)", re.IGNORECASE)


def _import_span_ok(span: str) -> bool:
    """The words between the verb and "from <program>": a tool noun ("the
    QuickBooks server I use"), or a NAME — one or two words, capitalised,
    no article, not a file, a setting or text ("add QuickBooks from Claude
    Desktop", never "add a breakpoint in VS Code" or "copy the text from
    Cursor").

    v1.435.0 review: the tool noun must END what is being moved — "the
    server logs", "the tool output", "the app version" name something OF a
    server, never the server (each armed ``app_import`` before)."""
    nouns = list(_TOOLISH.finditer(span))
    if nouns:
        tail = span[nouns[-1].end():].strip()
        return not tail or bool(_SPAN_TAIL_OK.match(tail))
    words = span.split()
    if words and words[0].lower() == "my":
        words = words[1:]
    return (
        0 < len(words) <= 2
        and words[0][:1].isupper()
        and words[0].lower() not in ("a", "an", "the", "some", "any", "this", "that", "these", "those")
        and not _NOT_A_SERVER.search(span)
    )


#: A notification destination by its type or the word itself.
_DEST_TYPE = r"(?:slack|telegram|discord|e-?mail|phone|text|sms)"
_DEST = (
    r"(?:" + _DET + r"?(?:(?:" + _DEST_TYPE + r")\s+)?(?:notification\s+|alert\s+)?(?:destinations?|channels?)"
    r"|" + _DET + r"?(?:slack|telegram|discord))"
)
_DEST_REMOVE = re.compile(
    r"\b(?:remove|delete|disconnect|drop|get\s+rid\s+of|stop\s+using)\s+" + _DEST + _AFTER
    + r"|\bstop\s+(?:send(?:ing)?|notify(?:ing)?|alert(?:ing)?|messag(?:e|ing)|text(?:ing)?|ping(?:ing)?)"
    r"(?:\s+me)?(?:\s+(?:alerts?|notifications?|messages?|updates?|anything))?\s+(?:to|on|via|through|in|by)\s+"
    + _DET + r"?" + _DEST_TYPE + r"\b",
    re.IGNORECASE,
)
_ALERT_VERB = r"(?:send|notify|alert|message|tell|text|ping|email)"
_ALERT_WHAT = (
    r"(?:about|when|if|for|whenever|on|the\s+(?:failures?|errors?|problems?|important|bad)"
    r"|failures?|errors?|problems?|important|bad\s+news|alerts?|notifications?)"
)
_ALERTS = re.compile(
    r"\bonly\s+" + _ALERT_VERB + r"\s+me\s+" + _ALERT_WHAT + r"\b(?!-)"
    + r"|\b" + _ALERT_VERB + r"\s+me\s+only\s+" + _ALERT_WHAT + r"\b(?!-)"
    + r"|\b(?:stop|quit|don'?t|do\s+not|no\s+more)\s+(?:send(?:ing)?|notify(?:ing)?|alert(?:ing)?"
    r"|messag(?:e|ing)|text(?:ing)?|ping(?:ing)?|email(?:ing)?)\s+me\s+"
    r"(?:(?:alerts?|notifications?|messages?|updates?)\s+)?(?:about|when|for|every\s+time)\b"
    r"[^.?!\n]{0,40}?\b(?:tasks?|runs?|workflows?|goals?|reviews?|approvals?|failures?|errors?"
    r"|summar(?:y|ies)|night\s+shift|pull\s+requests?|checks|skills?|models?|finished|completed|done)\b",
    re.IGNORECASE,
)
_ALERT_CHOICE = re.compile(
    r"\b(?:change|choose|pick|set|select|adjust|limit|narrow|filter|edit|customi[sz]e|mute|unmute|reduce)"
    r"\s+(?:(?:the|my|which|what|its)\s+)?(?:[\w'-]+\s+){0,2}?(?:alerts|notifications)\b"
    # v1.435.0 review: the alerts themselves — never "the alert sound", "a
    # calendar alert", "the notification sound in Windows" (each armed it).
    r"(?!\s+(?:sounds?|tones?|ringtones?|volume|style|badges?|banners?|previews?)\b)",
    re.IGNORECASE,
)
_DEST_WORD = re.compile(r"\b(?:" + _DEST_TYPE + r"|destinations?|channels?)\b", re.IGNORECASE)

#: The longest message the matchers read (a pasted page is not a request).
_MAX_TEXT = 20_000


def apps_tools_for(text: str) -> tuple[str, ...]:
    """The tools of this module a chat message asks for, in a fixed order.
    Called ONCE from ``records.record_tools_for`` (which drops writers for a
    question). Linear-time matchers only; never raises.

    v1.435.0 review: the cache is keyed on the CUT text — keyed on the whole
    message it held up to 256 whole pasted messages (a pasted key among them)
    for the life of the process."""
    return _apps_tools_for(str(text or "")[:_MAX_TEXT])


@lru_cache(maxsize=256)
def _apps_tools_for(t: str) -> tuple[str, ...]:
    if not t.strip():
        return ()
    out: list[str] = []
    try:
        if _SWITCH.search(t):
            out.append("app_switch")
        if _DISCONNECT.search(t):
            out.append("app_disconnect")
        if _IMPORT_NAMED.search(t) or any(_import_span_ok(m.group(1)) for m in _IMPORT_FROM.finditer(t)):
            out.append("app_import")
        if _DEST_REMOVE.search(t):
            out.append("channel_remove")
        if _ALERTS.search(t) or (_ALERT_CHOICE.search(t) and _DEST_WORD.search(t)):
            out.append("channel_alerts")
    except Exception:  # noqa: BLE001 — a matcher fault arms nothing new
        return ()
    return tuple(out)


#: The tests clear the cache through the public name, as before.
apps_tools_for.cache_clear = _apps_tools_for.cache_clear  # type: ignore[attr-defined]
apps_tools_for.cache_info = _apps_tools_for.cache_info  # type: ignore[attr-defined]
