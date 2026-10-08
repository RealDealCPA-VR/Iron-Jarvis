"""THE ONE SETTINGS WRITER (calm UI redesign S2).

Before this, eleven routes changed configuration, and only ``PUT /settings``
left a ledger row with an Undo; the rest called ``_persist_config`` and left
no trace (Iron-Proxy on/off, profile sharing, routing, the kill switch, the
calendar trigger, skill learning, fleet code routing, the default provider
from Connections, MCP auto-approve). Their side effects were copied by hand
and had drifted: undoing a calendar, fleet or browser setting did not re-arm
its loop, so the undo took effect only at the next restart.

Now every change — the Settings page, those routes, Undo, and chat's
``config_set`` — goes through :meth:`ConfigWriter.apply`:

1. resolve every key in the ONE schema (``settings.schema``) — an unknown key
   is refused, a secret is never a setting (it has its own card);
2. validate everything on a throwaway copy before anything changes (one bad
   value must not half-apply), plus the schema's own checks;
3. apply — plain config fields are assigned and persisted together; keys with
   a side effect of their own use that service's own setter (Iron-Proxy,
   profile sharing, the agent browser, per-tool permissions); profile and
   device keys go to their stores;
4. run the live side effects ONCE, the same for a save and an undo (re-point
   local endpoints, re-arm the autonomy / sentinels / calendar / fleet /
   browser loops, refresh the OpenCode cache, retire a backup-copy loop);
5. journal — a ``ToolInvocation`` (``update_settings``) and a
   ``setting_restore`` ``UndoJournal`` row holding the PRIOR values of what
   actually changed (never a secret-looking key), and a ``config.changed``
   event. Undo applies that prior through this same writer (``journal=False``;
   the undo route records the undo itself).

The writer is blocking (it may start a process, write files, probe a folder):
call it from a worker thread, never on the event loop.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from typing import Any

from ..core.config import is_secret_config_key, persist_config_values
from . import schema
from .device import DevicePrefs

log = logging.getLogger("ironjarvis.settings")

#: Loops that re-arm live when a key with this prefix changes.
_REARM_GROUPS = ("autonomy", "sentinels", "calendar", "fleet", "browser")
_LOCAL_ENDPOINT_KEYS = ("ollama_base_url", "ollama_model", "custom_base_url", "custom_model")
#: Keys applied by their own service's setter (they persist themselves).
_HOOK_KEYS = frozenset(
    {"iron_proxy_enabled", "profile_share_claude_code", "profile_share_codex", "agent_browser_enabled"}
)


class SettingError(ValueError):
    """A change was refused; ``str()`` is one plain sentence naming the key."""


@dataclass
class ChangeSet:
    updated: list[str] = field(default_factory=list)
    changed: list[dict[str, Any]] = field(default_factory=list)
    action_id: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"updated": self.updated, "changed": self.changed, "action_id": self.action_id}


def _label(key: str) -> str:
    try:
        d = schema.get(key)
    except KeyError:
        return key
    if d.pattern:
        return f"{d.label}: {schema.pattern_arg(d, key)}"
    return d.label


class ConfigWriter:
    def __init__(self, d: Any) -> None:
        self.d = d
        self.platform = d.platform
        self.devices = DevicePrefs(self.platform.config.home)
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ read
    def current(self, key: str, *, device_id: str = "") -> Any:
        d = schema.get(key)
        cfg = self.platform.config
        if d.store == "device":
            return self.devices.get(device_id, key)
        if d.store == "profile":
            from ..profile.store import ProfileStore

            return getattr(ProfileStore(self.platform.engine).get(), d.field_name, None)
        if d.pattern and key.startswith("permissions."):
            tool = schema.pattern_arg(d, key)
            return (getattr(cfg, "permissions", {}) or {}).get(tool)
        if "." in d.field_name:
            outer, inner = d.field_name.split(".", 1)
            return (getattr(cfg, outer, {}) or {}).get(inner)
        return getattr(cfg, d.field_name, None)

    # ------------------------------------------------------------- validate
    def _validate(self, values: dict[str, Any]) -> dict[str, Any]:
        """Every value checked before anything changes; returns the cleaned set."""
        from ..core.config import Config  # noqa: F401 — the trial copy's type

        cfg = self.platform.config
        trial = cfg.model_copy(deep=True)
        clean: dict[str, Any] = {}
        for key, value in values.items():
            try:
                d = schema.get(key)
            except KeyError:
                raise SettingError(f"There is no setting called {key!r}.") from None
            if value is None and (d.store == "device" or (d.pattern and key.startswith("permissions."))):
                # "Not set" — a device preference never chosen, a tool with no
                # rule of its own. Restoring it removes the entry.
                clean[key] = None
                continue
            if d.type == "bool":
                if not isinstance(value, bool):
                    raise SettingError(f"{d.label} must be on or off.")
            elif d.type == "enum":
                allowed = [v for v, _ in d.options]
                if value not in allowed:
                    raise SettingError(f"{d.label} must be one of: {', '.join(map(str, allowed))}.")
            elif d.type == "number":
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise SettingError(f"{d.label} must be a number.")
            elif d.type == "string":
                if value is None:
                    value = ""
                if not isinstance(value, str):
                    raise SettingError(f"{d.label} must be text.")
                value = value.strip()
            elif d.type == "list" and not isinstance(value, list):
                raise SettingError(f"{d.label} must be a list.")
            elif d.type == "dict" and not isinstance(value, dict):
                raise SettingError(f"{d.label} must be a set of name: value pairs.")
            if d.check is not None:
                try:
                    d.check(value)
                except ValueError as exc:
                    raise SettingError(f"{d.label} {exc}.") from None
            if d.pattern and key.startswith("permissions."):
                self._check_permission(schema.pattern_arg(d, key), value)
            elif d.store == "config" and "." not in d.field_name:
                try:
                    setattr(trial, d.field_name, value)
                except Exception:  # noqa: BLE001 — pydantic validate_assignment
                    raise SettingError(f"That is not a valid value for {d.label}.") from None
                value = getattr(trial, d.field_name)
            clean[key] = value
        if "backup_mirror_dir" in clean and clean["backup_mirror_dir"]:
            from ..maintenance import mirror_dir_problem

            problem = mirror_dir_problem(cfg.home, clean["backup_mirror_dir"])
            if problem:
                raise SettingError(f"backup copy folder: {problem}")
        for cli_key, cli in (("profile_share_claude_code", "claude-code"), ("profile_share_codex", "codex")):
            if clean.get(cli_key) is True:
                share = getattr(self.platform, "profile_share", None)
                if share is not None and not share.available(cli):
                    raise SettingError(
                        f"{_label(cli_key).replace('Share my profile with ', '')} is not installed on this PC, "
                        "so there is nothing to share with."
                    )
        return clean

    @staticmethod
    def _check_permission(tool: str, mode: Any) -> None:
        from ..tools.permissions import DENY_FLOOR_TOOLS

        if mode == "allow" and tool in DENY_FLOOR_TOOLS:
            raise SettingError(
                f"{tool} touches your PC directly, so it can never run without asking."
            )

    # ----------------------------------------------------------------- apply
    def apply(
        self,
        values: dict[str, Any],
        *,
        actor: str,
        device_id: str = "",
        journal: bool = True,
        session_id: str = "settings",
    ) -> ChangeSet:
        """Validate, apply, run side effects once, journal. Blocking."""
        with self._lock:
            clean = self._validate(values)
            prior = {k: self.current(k, device_id=device_id) for k in clean}
            out = ChangeSet()
            plain: dict[str, Any] = {}
            profile: dict[str, Any] = {}
            device: dict[str, Any] = {}
            cfg = self.platform.config
            for key, value in clean.items():
                d = schema.get(key)
                if d.store == "device":
                    device[key] = value
                elif d.store == "profile":
                    profile[d.field_name] = value
                elif key == "iron_proxy_enabled":
                    self._iron_proxy(bool(value))
                elif key in ("profile_share_claude_code", "profile_share_codex"):
                    self.platform.profile_share.set_on(
                        "claude-code" if key.endswith("claude_code") else "codex", bool(value)
                    )
                elif key == "agent_browser_enabled":
                    self._agent_browser(bool(value))
                elif d.pattern and key.startswith("permissions."):
                    self._permission(schema.pattern_arg(d, key), "" if value is None else str(value))
                else:
                    setattr(cfg, d.field_name, value)
                    plain[d.field_name] = value
                out.updated.append(key)
            if plain:
                persist_config_values(cfg.home, plain)
            if profile:
                from ..profile.store import ProfileStore

                ProfileStore(self.platform.engine).save(profile)
            if device:
                self.devices.set_many(device_id, device)
            self._side_effects(out.updated)
            now = {k: self.current(k, device_id=device_id) for k in clean}
            changed_prior = {k: prior[k] for k in clean if prior[k] != now[k] and not is_secret_config_key(k)}
            out.changed = [
                {"key": k, "label": _label(k), "old": prior[k], "new": now[k]} for k in clean if prior[k] != now[k]
            ]
            if journal and changed_prior:
                out.action_id = self._journal(changed_prior, actor=actor, device_id=device_id, session_id=session_id)
            if out.changed:
                self._publish(out, actor)
            return out

    def restore(
        self,
        prior: dict[str, Any],
        *,
        device_id: str = "",
        restore_fn: Any = None,
    ) -> ChangeSet:
        """Undo: apply the prior values unjournaled (the undo route records the
        reversal). A key that no longer exists or no longer validates is
        skipped, never fatal. ``restore_fn`` (``core.config.
        restore_config_values``, handed in by the undo route) restores the
        plain config fields in one write; everything else — hooked keys,
        profile, device — goes through :meth:`apply`. Side effects run once."""
        ok: dict[str, Any] = {}
        for k, v in prior.items():
            try:
                ok.update(self._validate({k: v}))
            except SettingError:
                continue
        if restore_fn is None:
            return self.apply(ok, actor="undo", device_id=device_id, journal=False)

        def _plain(k: str) -> bool:
            d = schema.get(k)
            return d.store == "config" and not d.pattern and "." not in d.field_name and k not in _HOOK_KEYS

        plain = {k: v for k, v in ok.items() if _plain(k)}
        rest = {k: v for k, v in ok.items() if k not in plain}
        with self._lock:
            restored = (
                list(restore_fn(self.platform.config, {schema.get(k).field_name: v for k, v in plain.items()}))
                if plain
                else []
            )
            out = self.apply(rest, actor="undo", device_id=device_id, journal=False)
            self._side_effects(restored)
            out.updated = restored + out.updated
            return out

    # ---------------------------------------------------------- side effects
    def _iron_proxy(self, on: bool) -> None:
        svc = getattr(self.platform, "iron_proxy", None)
        if svc is None:
            self.platform.config.iron_proxy_enabled = on
            persist_config_values(self.platform.config.home, {"iron_proxy_enabled": on})
            return
        svc.set_enabled(on)
        try:
            svc.start() if on else svc.stop()
        except Exception:  # noqa: BLE001 — the switch is saved; the card shows status.error
            log.debug("iron-proxy start/stop after a settings change failed", exc_info=True)

    def _agent_browser(self, on: bool) -> None:
        cfg = self.platform.config
        cu_cfg = dict(getattr(cfg, "computer_use", {}) or {})
        cu_cfg["enabled"] = on
        cfg.computer_use = cu_cfg
        persist_config_values(cfg.home, {"computer_use": cu_cfg})
        cu = getattr(self.platform, "computeruse", None)
        if cu is not None:
            cu.policy.enabled = on  # updated IN PLACE: every holder sees it (v1.235.0)

    def _permission(self, tool: str, mode: str) -> None:
        cfg = self.platform.config
        perms = dict(getattr(cfg, "permissions", {}) or {})
        engine = getattr(self.platform, "permissions", None)
        if mode in ("", "None"):
            perms.pop(tool, None)
            if engine is not None:
                engine._base.pop(tool, None)
        else:
            perms[tool] = mode
            if engine is not None:
                engine._base[tool] = mode  # the live gate reads this dict
        cfg.permissions = perms
        persist_config_values(cfg.home, {"permissions": perms})

    def _side_effects(self, updated: list[str]) -> None:
        d = self.d
        cfg = self.platform.config
        if "backup_mirror_dir" in updated and not getattr(cfg, "backup_mirror_dir", ""):
            getattr(d, "loop_health", {}).pop("backup_mirror", None)
        rearm = getattr(d, "_live_rearm", None) or {}
        loop = rearm.get("loop")
        if loop is not None:
            for group in _REARM_GROUPS:
                if any(k.startswith(group) for k in updated):
                    fn = rearm.get(group)
                    if fn is not None:
                        try:
                            loop.call_soon_threadsafe(fn)
                        except Exception:  # noqa: BLE001 — a closed loop (shutdown)
                            pass
        if any(k in _LOCAL_ENDPOINT_KEYS for k in updated):
            try:
                self.platform.providers.configure_local(
                    ollama_base_url=cfg.ollama_base_url,
                    ollama_model=cfg.ollama_model,
                    custom_base_url=cfg.custom_base_url,
                    custom_model=cfg.custom_model,
                )
            except Exception:  # noqa: BLE001 — the next boot picks it up
                pass
        if "opencode_local_models" in updated:
            try:
                self.platform.providers.refresh_opencode()
            except Exception:  # noqa: BLE001
                pass

    # ---------------------------------------------------------------- record
    def _journal(self, prior: dict[str, Any], *, actor: str, device_id: str, session_id: str) -> str:
        from ..core.db import session_scope
        from ..core.ids import new_id
        from ..core.models import PermissionMode, ToolInvocation, UndoJournal
        from ..tools.base import Reversibility

        inv_id = new_id("tool")
        keys = sorted(prior)
        try:
            with session_scope(self.platform.engine) as db:
                db.add(
                    ToolInvocation(
                        id=inv_id,
                        session_id=session_id,
                        agent_run_id="",
                        tool="update_settings",
                        args_json=json.dumps({"changed": keys, "actor": actor}),
                        verdict=PermissionMode.ALLOW,
                        ok=True,
                        output="changed " + ", ".join(keys),
                        reversibility=Reversibility.REVERSIBLE.value,
                    )
                )
                db.add(
                    UndoJournal(
                        action_id=inv_id,
                        session_id=session_id,
                        agent_run_id="",
                        tool="update_settings",
                        kind="setting_restore",
                        reversible=True,
                        pre_inline=json.dumps({"prior": prior, "device_id": device_id}, default=str),
                    )
                )
                db.commit()
        except Exception:  # noqa: BLE001 — journaling must never break the write
            log.warning("settings change was applied but could not be journaled", exc_info=True)
            return ""
        return inv_id

    def _publish(self, out: ChangeSet, actor: str) -> None:
        bus = getattr(self.platform, "event_bus", None)
        loop = (getattr(self.d, "_live_rearm", None) or {}).get("loop")
        if bus is None or loop is None:
            return
        payload = {
            "keys": out.updated,
            "actor": actor,
            "action_id": out.action_id,
        }
        try:
            import asyncio

            from ..core.events import EventType

            asyncio.run_coroutine_threadsafe(bus.publish(EventType.CONFIG_CHANGED, payload), loop)
        except Exception:  # noqa: BLE001 — an event is a courtesy, the change landed
            log.debug("config.changed publish failed", exc_info=True)
