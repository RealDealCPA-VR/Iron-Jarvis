"""Settings, diagnostics, onboarding and doctor routes.

Moved verbatim from daemon/app.py's create_app; closure-local state is
reached through ``d`` (see the deps object built in create_app).
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pathlib import Path
from typing import Any

from ..schemas import RepairBody, SettingsBody, _SETTINGS_KEYS
from ...core.config import persist_config_values


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
        # Everything validated — commit to the running config.
        updated: list[str] = []
        for key, value in candidates.items():
            setattr(cfg, key, value)
            updated.append(key)
        # Persist atomically (temp + os.replace) so a crash mid-write can't leave a
        # torn config.toml that aborts the next boot.
        persist_config_values(cfg.home, {k: getattr(cfg, k, None) for k in updated})
        # LIVE re-arm: an autonomy_*/sentinels_* change re-arms its background
        # loop immediately (this endpoint runs in a threadpool, so hop onto the
        # daemon loop). Previously the toggle waited for the next restart.
        loop = d._live_rearm.get("loop")
        if loop is not None:
            for group in ("autonomy", "sentinels"):
                if any(k.startswith(group) for k in updated):
                    fn = d._live_rearm.get(group)
                    if fn is not None:
                        loop.call_soon_threadsafe(fn)
        return {
            "settings": {k: getattr(cfg, k, None) for k in _SETTINGS_KEYS},
            "updated": updated,
        }

    @app.get("/diagnostics")
    def diagnostics() -> dict[str, Any]:
        """Read-only health of the running state (never raises)."""
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
            out["db_integrity"] = "ok"
        except Exception as exc:  # noqa: BLE001
            out["db_integrity"] = f"error: {exc}"
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
        out["tracked_worktrees"] = len(d.orchestrator._git_sessions)
        try:
            out["providers"] = d.platform.providers.health()
        except Exception:  # noqa: BLE001
            out["providers"] = []
        return out

    @app.post("/diagnostics/repair")
    def diagnostics_repair(body: RepairBody) -> dict[str, Any]:
        """Gated, idempotent, in-app remediation — let the app FIX (not just report)
        the common infrastructure problems a daily driver hits, without dropping to
        a shell. Each action is logged and safe to re-run."""
        from sqlalchemy import text

        action = body.action
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
            from ...maintenance import run_auto_backup

            p = run_auto_backup(d.platform.config.home, engine=d.platform.engine)
            return {"action": action, "ok": True, "result": str(p)}
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
