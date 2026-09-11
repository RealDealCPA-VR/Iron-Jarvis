"""Settings, diagnostics, onboarding and doctor routes.

Moved verbatim from daemon/app.py's create_app; closure-local state is
reached through ``d`` (see the deps object built in create_app).
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pathlib import Path
from typing import Any

from .. import app as _app
from ..schemas import RepairBody, RestoreBody, SettingsBody, _SETTINGS_KEYS
from ...core.config import capture_config_undo, persist_config_values
from ...core.logging import get_logger

log = get_logger("daemon.maintenance")


def _record_settings_undo(platform, prior: "dict[str, Any]") -> None:
    """Journal a settings change as a reversible ``setting_restore`` action (TX-01),
    so it appears on the audit timeline and can be reversed from time-travel
    (``POST /undo`` restores the prior values via ``restore_config_values``).

    ``prior`` holds only NON-SECRET keys whose value actually changed (secret-named
    keys are refused capture upstream, so no credential lands in the journal).
    Best-effort — a telemetry failure must never fail the settings write itself."""
    if not prior:
        return
    import json

    from ...core.db import session_scope
    from ...core.ids import new_id
    from ...core.models import PermissionMode, ToolInvocation, UndoJournal
    from ...tools.base import Reversibility

    inv_id = new_id("tool")
    keys = sorted(prior)
    try:
        with session_scope(platform.engine) as db:
            db.add(
                ToolInvocation(
                    id=inv_id,
                    session_id="settings",
                    agent_run_id="",
                    tool="update_settings",
                    args_json=json.dumps({"changed": keys}),
                    verdict=PermissionMode.ALLOW,
                    ok=True,
                    output="changed " + ", ".join(keys),
                    reversibility=Reversibility.REVERSIBLE.value,
                )
            )
            db.add(
                UndoJournal(
                    action_id=inv_id,
                    session_id="settings",
                    agent_run_id="",
                    tool="update_settings",
                    kind="setting_restore",
                    reversible=True,
                    pre_inline=json.dumps({"prior": prior}),
                )
            )
            db.commit()
    except Exception:  # noqa: BLE001 — journaling must never break the settings write
        pass


def register(app: FastAPI, d) -> None:
    """Attach these routes to *app*; ``d`` is the create_app deps object."""
    @app.get("/settings")
    def get_settings() -> dict[str, Any]:
        cfg = d.platform.config
        return {"settings": {k: getattr(cfg, k, None) for k in _SETTINGS_KEYS}}

    @app.put("/settings")
    def put_settings(body: SettingsBody) -> dict[str, Any]:
        cfg = d.platform.config
        candidates = {k: v for k, v in body.values.items() if k in _SETTINGS_KEYS}
        # Validate ALL keys on a throwaway copy first, so one bad value can't
        # partially mutate (and then persist) the live config — which previously
        # could brick the next boot or break in-flight sessions.
        trial = cfg.model_copy(deep=True)
        for key, value in candidates.items():
            try:
                setattr(trial, key, value)
            except Exception:  # noqa: BLE001 - pydantic validation
                raise HTTPException(status_code=400, detail=f"invalid value for {key}")
        # v1.249.0 (R-05): the backup copy folder must be usable NOW — a folder
        # on this machine the app may write in, outside its own data folder
        # (maintenance.mirror_dir_problem → fs_policy.root_problem, the one
        # definition). Checked HERE, where the blocking writability probe runs
        # in the threadpool, never at config load: an unplugged drive must not
        # stop the app from starting.
        if "backup_mirror_dir" in candidates:
            from ...maintenance import mirror_dir_problem

            raw_mirror = str(candidates.get("backup_mirror_dir") or "").strip()
            candidates["backup_mirror_dir"] = raw_mirror
            problem = mirror_dir_problem(cfg.home, raw_mirror)
            if problem:
                raise HTTPException(status_code=400, detail=f"backup copy folder: {problem}")
        # Everything validated — snapshot the PRIOR values (non-secret keys only)
        # for a settings-change undo (TX-01) BEFORE mutating, then commit to the
        # running config.
        undo_snapshot = capture_config_undo(cfg, list(candidates.keys()))
        updated: list[str] = []
        for key, value in candidates.items():
            setattr(cfg, key, value)
            updated.append(key)
        # Persist atomically (temp + os.replace) so a crash mid-write can't leave a
        # torn config.toml that aborts the next boot.
        persist_config_values(cfg.home, {k: getattr(cfg, k, None) for k in updated})
        # TX-01: journal the change (only keys that actually changed value) as a
        # reversible action so it lands on the audit timeline and can be undone.
        changed_prior = {
            k: v
            for k, v in undo_snapshot.get("prior", {}).items()
            if getattr(cfg, k, None) != v
        }
        _record_settings_undo(d.platform, changed_prior)
        # v1.249.0 (R-05): switching the backup copy OFF retires its loop entry,
        # or the Overview would keep naming a copy nobody asked for any more.
        if "backup_mirror_dir" in updated and not getattr(cfg, "backup_mirror_dir", ""):
            d.loop_health.pop("backup_mirror", None)
        # LIVE re-arm: an autonomy_*/sentinels_* change re-arms its background
        # loop immediately (this endpoint runs in a threadpool, so hop onto the
        # daemon loop). Previously the toggle waited for the next restart.
        # `browser` joined the groups in v1.235.0: moving browser_access to `off`
        # must DROP the live paired socket, and a capability the user just turned
        # off that keeps driving their real Chrome until the next restart is the
        # one failure mode this whole switch exists to prevent.
        loop = d._live_rearm.get("loop")
        if loop is not None:
            for group in ("autonomy", "sentinels", "calendar", "fleet", "browser"):
                if any(k.startswith(group) for k in updated):
                    fn = d._live_rearm.get(group)
                    if fn is not None:
                        loop.call_soon_threadsafe(fn)
        # LIVE re-point: the ProviderManager captured the local/custom endpoint
        # config at boot — without this, a freshly saved endpoint stayed
        # unavailable (and adapters bound stale URLs/models) until restart.
        if any(
            k in ("ollama_base_url", "ollama_model", "custom_base_url", "custom_model")
            for k in updated
        ):
            try:
                d.platform.providers.configure_local(
                    ollama_base_url=cfg.ollama_base_url,
                    ollama_model=cfg.ollama_model,
                    custom_base_url=cfg.custom_base_url,
                    custom_model=cfg.custom_model,
                )
            except Exception:  # noqa: BLE001 — next boot still picks config up
                pass
        # Editing the OpenCode allowlist must take effect NOW: the manager
        # caches the resolved local models (available() is on the hot path).
        if "opencode_local_models" in updated:
            try:
                d.platform.providers.refresh_opencode()
            except Exception:  # noqa: BLE001 — a cache drop never breaks a save
                pass
        return {
            "settings": {k: getattr(cfg, k, None) for k in _SETTINGS_KEYS},
            "updated": updated,
        }

    @app.get("/diagnostics")
    def diagnostics() -> dict[str, Any]:
        """Read-only health of the running state (never raises).

        ``db_liveness`` (v1.229.0, audit OBS5) is what this endpoint really
        measures: a ``SELECT 1`` — the database file opens and answers.
        ``db_integrity`` carries the SAME value and is kept for compatibility
        only; it OVERSTATES, because a real integrity check is ``PRAGMA
        integrity_check`` (a whole-DB page scan), which is on-demand via
        ``POST /diagnostics/repair {"action": "db_integrity"}`` and never runs
        on this polled route. Read ``db_liveness``.
        """
        from sqlalchemy import text

        cfg = d.platform.config
        out: dict[str, Any] = {}
        try:
            with d.platform.engine.connect() as conn:
                # Cheap liveness probe only — a full PRAGMA integrity_check is a
                # whole-DB page scan (hundreds of ms on a large DB) and this endpoint
                # is polled ~every 15s app-wide (NotificationBell). Deep integrity is
                # on-demand via POST /diagnostics/repair {db_integrity}.
                conn.execute(text("SELECT 1")).scalar()
            out["db_liveness"] = "ok"
        except Exception as exc:  # noqa: BLE001
            out["db_liveness"] = f"error: {exc}"
        out["db_integrity"] = out["db_liveness"]  # compat alias; see docstring
        try:
            db_path = cfg.db_path
            out["db_bytes"] = db_path.stat().st_size if db_path.exists() else 0
            wal = Path(str(db_path) + "-wal")
            out["wal_bytes"] = wal.stat().st_size if wal.exists() else 0
        except Exception:  # noqa: BLE001
            pass
        out["secrets_key_present"] = (cfg.home / "secrets" / ".secrets.key").exists()
        # Real decryptability check (not mere file existence): catches a lost /
        # mismatched key (e.g. a key-less restore) that would silently break every
        # stored credential while still reading as "present".
        try:
            out["secrets_key_valid"] = d.platform.secrets.key_valid()
        except Exception:  # noqa: BLE001 — diagnostics must never raise
            out["secrets_key_valid"] = False
        out["running_sessions"] = len(d.orchestrator._running)
        out["pending_reviews"] = len(d.orchestrator._reviews)
        out["background_loops"] = dict(d.loop_health)  # silent-failure visibility
        # v1.229.0 (audit U4): a configured MCP server that did not start, by
        # name with its reason — the Overview hero reads this so "All systems
        # nominal" cannot sit over a pack whose tools every agent is missing.
        try:
            from ...mcp.tools import load_status as _mcp_load_status

            out["mcp_servers"] = [
                {
                    "name": str(s.get("name") or ""),
                    "tools_loaded": len(d.platform.registry.mcp_names(str(s.get("name") or ""))),
                    "last_error": (_mcp_load_status(str(s.get("name") or "")) or {}).get("last_error"),
                }
                for s in (getattr(cfg, "mcp_servers", None) or [])
                if isinstance(s, dict) and s.get("name")
            ]
        except Exception:  # noqa: BLE001 — diagnostics must never raise
            out["mcp_servers"] = []
        out["tracked_worktrees"] = len(d.orchestrator._git_sessions)
        try:
            out["providers"] = d.platform.providers.health()
        except Exception:  # noqa: BLE001
            out["providers"] = []
        return out

    @app.get("/diagnostics/errors")
    def diagnostics_errors(limit: int = 50) -> dict[str, Any]:
        """The last WARNING+ log records (v1.229.0, audit OBS5), oldest first:
        ``{ts, level, logger, message}`` from the in-memory ring buffer
        ``core/logging.RecentErrorsHandler`` keeps on the whole logger tree —
        the app's two names and the libraries. Memory only, so it costs
        nothing on the loop; "Copy diagnostics" on Settings → Maintenance
        includes it. Additive; never raises."""
        from ...core.logging import RECENT_ERRORS_CAPACITY, recent_errors

        return {"errors": recent_errors(limit), "capacity": RECENT_ERRORS_CAPACITY}

    @app.get("/maintenance/backups")
    def maintenance_backups() -> dict[str, Any]:
        """The archives under ``<home>/backups`` (v1.229.0, audit CL7), newest
        first, for Settings → Maintenance → Restore from backup."""
        from ...maintenance import BACKUP_DIRNAME, list_backups, mirror_status

        home = d.platform.config.home
        return {
            "dir": str(home / BACKUP_DIRNAME),
            "backups": list_backups(home),
            # v1.249.0 (R-05): the second-drive copy — folder, whether it is
            # there right now, and the last copy's outcome — for the card.
            "mirror": mirror_status(home, d.platform.config),
        }

    @app.post("/maintenance/restore")
    def maintenance_restore(body: RestoreBody) -> dict[str, Any]:
        """Restore one archive from ``GET /maintenance/backups`` over the live
        home and schedule a daemon stop (v1.229.0, audit CL7); the desktop
        app relaunches it within ~2 s and the process boots on the restored
        state. Refuses (409) while sessions or workflow runs are in flight —
        the same gate as ``db_vacuum`` — and while the database is held open
        (the replace fails BEFORE any other file moved). ``name`` is a file
        name from the listing, never a path."""
        import threading as _threading

        from .system import activity_snapshot
        from ...maintenance import BACKUP_DIRNAME, restore_backup_live

        name = (body.name or "").strip()
        home = d.platform.config.home
        backups_dir = (home / BACKUP_DIRNAME).resolve()
        if (
            not name
            or name != Path(name).name
            or not name.startswith("ironjarvis-backup-")
            or not name.endswith(".tar.gz")
        ):
            raise HTTPException(status_code=400, detail="name must be an archive from GET /maintenance/backups")
        archive = (backups_dir / name).resolve()
        if archive.parent != backups_dir or not archive.is_file():
            raise HTTPException(status_code=404, detail=f"no backup named {name!r}")
        act = activity_snapshot(d)
        running = len(d.orchestrator._running)
        if act["active_sessions"] or act["writing_workflow_runs"] or running:
            what = []
            if act["active_sessions"] or running:
                what.append(f"{max(act['active_sessions'], running)} session(s) running")
            if act["writing_workflow_runs"]:
                what.append(f"{act['writing_workflow_runs']} workflow run(s) running")
            raise HTTPException(
                status_code=409,
                detail=(
                    f"cannot restore while work is in flight ({', '.join(what)}) — "
                    "it would replace the database out from under them; wait for "
                    "them to finish or cancel them, then retry"
                ),
            )
        try:
            d.platform.engine.dispose()
        except Exception:  # noqa: BLE001 — a pool that will not close still gets the replace attempt
            pass
        try:
            moved = restore_backup_live(home, archive)
        except PermissionError as exc:
            raise HTTPException(
                status_code=409,
                detail=f"restore aborted, nothing changed: the database is still held open ({exc})",
            ) from exc
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=f"restore failed: {exc}") from exc
        log.warning("restored %d file(s) from backup %s; restarting", moved, name)
        _threading.Timer(0.5, _app._graceful_stop).start()
        return {"ok": True, "restored_from": name, "files": moved, "restart": "scheduled"}

    @app.post("/diagnostics/repair")
    def diagnostics_repair(body: RepairBody) -> dict[str, Any]:
        """Gated, idempotent, in-app remediation — let the app FIX (not just report)
        the common infrastructure problems a daily driver hits, without dropping to
        a shell. Each action is logged and safe to re-run."""
        from sqlalchemy import text

        action = body.action
        if action in ("db_vacuum", "prune_events"):
            # v1.226.0: VACUUM holds an EXCLUSIVE lock for its whole duration;
            # every write a live session / workflow run makes meanwhile waits
            # up to busy_timeout (30s) and then FAILS the step. Refuse honestly
            # while anything is actually writing, naming what is running.
            from .system import activity_snapshot

            act = activity_snapshot(d)
            if act["active_sessions"] or act["writing_workflow_runs"]:
                what = []
                if act["active_sessions"]:
                    what.append(f"{act['active_sessions']} session(s) running")
                if act["writing_workflow_runs"]:
                    what.append(f"{act['writing_workflow_runs']} workflow run(s) running")
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"cannot {action} while work is in flight ({', '.join(what)}) — "
                        "it would lock the database out from under them; wait for "
                        "them to finish or cancel them, then retry"
                    ),
                )
        if action == "db_integrity":
            with d.platform.engine.connect() as conn:
                res = conn.execute(text("PRAGMA integrity_check")).scalar()
            return {"action": action, "ok": res == "ok", "result": res}
        if action == "db_vacuum":
            # Standalone VACUUM (compact/defragment) — run outside a transaction
            # via the raw DBAPI connection in autocommit, as the offline CLI does.
            raw = d.platform.engine.raw_connection()
            try:
                dbapi = getattr(raw, "dbapi_connection", None) or raw.connection
                old_iso = dbapi.isolation_level
                dbapi.isolation_level = None  # VACUUM cannot run inside a transaction
                dbapi.execute("VACUUM")
                dbapi.isolation_level = old_iso
            finally:
                raw.close()
            return {"action": action, "ok": True, "result": "vacuumed"}
        if action == "prune_events":
            from ...core.db import prune_events

            n = prune_events(d.platform.engine, body.older_than_days, vacuum=True)
            return {"action": action, "ok": True, "result": f"pruned {n} event(s) + vacuumed"}
        if action == "backup_now":
            from ...maintenance import mirror_loop_health, mirror_status, run_auto_backup

            cfg = d.platform.config
            # v1.249.0 (R-05): a manual backup is copied to the second drive
            # too, and its outcome refreshes the loop entry — plugging the
            # drive back in and pressing Back up now clears a stale failure.
            p = run_auto_backup(cfg.home, engine=d.platform.engine, config=cfg)
            status = mirror_status(cfg.home, cfg)
            entry = mirror_loop_health(status)
            if entry is not None:
                d.loop_health["backup_mirror"] = entry
            return {"action": action, "ok": True, "result": str(p), "mirror": status}
        if action == "recheck":
            from ...onboarding import doctor as _doctor

            return {"action": action, "ok": True, "result": _doctor(d.platform)}
        raise HTTPException(
            status_code=400,
            detail=(
                f"unknown repair action '{action}' "
                "(db_integrity | db_vacuum | prune_events | backup_now | recheck)"
            ),
        )

    @app.get("/onboarding")
    def onboarding() -> dict[str, Any]:
        from ...onboarding import readiness

        return readiness(d.platform)

    @app.get("/doctor")
    def doctor_ep() -> dict[str, Any]:
        from ...onboarding import doctor

        # Pass the live platform so doctor also runs RUNTIME checks (model
        # connected, secrets key valid, DB integrity) — the failures a daily
        # driver actually hits, not just machine prerequisites.
        return doctor(d.platform)
