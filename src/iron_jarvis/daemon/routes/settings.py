"""Settings, diagnostics, onboarding and doctor routes.

Moved verbatim from daemon/app.py's create_app; closure-local state is
reached through ``d`` (see the deps object built in create_app).
"""

from __future__ import annotations

import threading

from fastapi import FastAPI, HTTPException
from pathlib import Path
from pydantic import BaseModel, Field
from typing import Any

from .. import app as _app
from ..schemas import RepairBody, RestoreBody, SettingsBody, _SETTINGS_KEYS
from ...core.config import capture_config_undo, persist_config_values
from ...core.logging import get_logger

log = get_logger("daemon.maintenance")


class UseModelBody(BaseModel):
    """``POST /onboarding/use-model`` (v1.310.0, W2-1)."""

    provider: str = Field(..., max_length=64)


#: v1.310.0 (W2-1): a subscription CLI is promoted to the INHERITED API name
#: the "Make default" path uses, so the quality dial (which knows anthropic /
#: openai model ids) keeps working; the router serves it through the CLI.
_CLI_INHERITS = {"claude-cli": "anthropic", "codex-cli": "openai"}


def _use_model_refusal(d, name: str) -> str | None:
    """One plain sentence saying why *name* can't answer right now, or None
    when it can. Reads cached availability only (no CLI is spawned)."""
    from ...onboarding.checklist import provider_label
    from ...onboarding.readiness import USE_MODEL_CLIS, USE_MODEL_LOCAL
    from ...providers.cli_auth import CLI_BINARIES, SIGN_IN_FIX
    from ...providers.manager import API_PROVIDERS

    pm = d.platform.providers
    cfg = d.platform.config
    known = set(API_PROVIDERS) | set(USE_MODEL_CLIS) | set(USE_MODEL_LOCAL)
    try:
        registered = name in pm._factories  # noqa: SLF001
    except Exception:  # noqa: BLE001 — be conservative: unknown
        registered = False
    if not registered:
        shown = name or "that"
        return (
            f"I don't know a model called “{shown}”, so nothing changed. "
            "Pick one from the Connections page."
        )
    if name not in known:
        # v1.310.0 (review): a REAL registered model this one press can't
        # choose (a Grok / OpenCode sign-in, a fleet node, the offline demo)
        # -- say so, and where it CAN be chosen; "I don't know it" was false.
        return (
            f"{provider_label(d.platform, name)} can't be chosen with this "
            "button, so nothing changed. Choose it on the Connections page."
        )
    try:
        if pm.available(name):
            return None
    except Exception:  # noqa: BLE001 — a probe fault reads as "not right now"
        pass
    label = provider_label(d.platform, name)
    if name in USE_MODEL_CLIS:
        binary = CLI_BINARIES[name]
        try:
            status = pm.cli_login_status(name)
        except Exception:  # noqa: BLE001
            status = {}
        if not status.get("installed"):
            tool = "Claude Code" if binary == "claude" else "Codex"
            return (
                f"{tool} isn't installed on this PC, so it can't answer for you "
                "yet. A subscription you use in the web or desktop app can't be "
                "shared with Iron Jarvis — only the command-line tool can."
            )
        return SIGN_IN_FIX[binary]
    if name == "ollama":
        if not (getattr(cfg, "ollama_base_url", None) or "").strip():
            return "Ollama isn't set up yet. Add its address on the Connections page first."
        return "Ollama isn't answering right now. Make sure it's running, then try again."
    if name == "custom":
        if not (getattr(cfg, "custom_base_url", None) or "").strip():
            return (
                "Your own model server (custom) isn't set up yet. Add its address "
                "on the Connections page first."
            )
        return (
            "Your own model server (custom) isn't answering right now. Make sure "
            "it's running, then try again."
        )
    return f"{label} isn't connected yet. Add its key on the Connections page first."


def _first_local_model(d, name: str) -> str:
    """The FIRST model a configured local endpoint lists ('' when none).

    Not ``config.ollama_model``: that defaults to 'llama3.1' while the wizard
    tells the user to ``ollama pull llama3.2`` -- promoting a model the
    server doesn't have would trade the demo for an error. Discovery is
    cached (~10 min) and may hit the endpoint once: the route is sync, so
    it runs on the threadpool, never on the event loop."""
    from ...providers.discovery import discover_models

    cfg = d.platform.config
    base = (cfg.ollama_base_url if name == "ollama" else cfg.custom_base_url) or ""
    try:
        live = discover_models(
            name,
            lambda: d.platform.providers._cred(name),  # noqa: SLF001
            base_url=base,
        )
    except Exception:  # noqa: BLE001 — discovery degrades to "nothing learned"
        live = []
    if live:
        return str(live[0])
    if name == "custom":
        return str(getattr(cfg, "custom_model", "") or "")
    return ""


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
        # v1.250.0 (S-01): WHERE THE BOOT'S SECONDS WENT — total plus per-phase
        # ms, so "the app takes 6-15 s to open" is a number on the install that
        # shows it instead of a guess on one that does not. The daemon logs the
        # same breakdown as ONE INFO line at startup; this is the copy nobody
        # has to grep a log for. Names and numbers only — the phase names are
        # code identifiers, never a path or a file name, because this blob gets
        # pasted into bug reports.
        try:
            _boot = getattr(d, "startup", None) or {}
            out["startup"] = {
                "total_ms": _boot.get("total_ms"),
                "at": _boot.get("at"),
                "steps_ms": dict(_boot.get("steps_ms") or {}),
            }
        except Exception:  # noqa: BLE001 — diagnostics must never raise
            out["startup"] = {"total_ms": None, "at": None, "steps_ms": {}}
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
                    # v1.256.0 (R-02): the plain-words cause and the next action,
                    # so the hero's "1 thing needs attention" can say WHAT to do.
                    "reason": (_mcp_load_status(str(s.get("name") or "")) or {}).get("reason"),
                    "fix": (_mcp_load_status(str(s.get("name") or "")) or {}).get("fix"),
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

    @app.get("/maintenance/storage")
    def maintenance_storage(older_than_days: int = 30) -> dict[str, Any]:
        """What Iron Jarvis is keeping on disk, by category (v1.256.0, R-01).

        THE SILENT GROWTH THIS ANSWERS: measured on the live install, 783 MB of
        814 MB was generated media nothing pruned and no screen reported. This is
        that screen. ``candidates`` is the dry run for the clear — the same rules
        the move uses, so the count the user agrees to is the count that moves.

        Read-only. BLOCKING (it walks the media folders), hence a sync def so
        FastAPI runs it in the threadpool rather than on the event loop.
        """
        from ...maintenance import media_candidates, storage_report

        home = d.platform.config.home
        report = storage_report(home)
        report["candidates"] = media_candidates(home, older_than_days)
        report["older_than_days"] = max(0, int(older_than_days))
        return report

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
        if action == "clear_media":
            # v1.256.0 (R-01): MOVES old generated media into <home>/trash/<stamp>/
            # and names what it moved. It does not delete: the undo journal's two
            # file kinds cannot reverse "remove bytes that already existed" (one
            # needs a pre-image of the very bytes being freed, the other inverts
            # to unlinking), so recoverability is the move itself and freeing the
            # disk is the separate `purge_trash` press.
            from ...maintenance import clear_media

            res = clear_media(d.platform.config.home, body.older_than_days)
            log.warning(
                "cleared %d media file(s) (%d bytes) to %s",
                res["moved"], res["bytes"], res["trash"] or "(nothing moved)",
            )
            return {"action": action, "ok": True, **res}
        if action == "purge_trash":
            # The deliberate second press: after this the cleared files are gone.
            from ...maintenance import purge_trash

            res = purge_trash(d.platform.config.home)
            log.warning("purged %d cleared file(s) (%d bytes)", res["deleted"], res["bytes"])
            return {"action": action, "ok": True, **res}
        if action == "recheck":
            from ...onboarding import doctor as _doctor

            return {"action": action, "ok": True, "result": _doctor(d.platform)}
        raise HTTPException(
            status_code=400,
            detail=(
                f"unknown repair action '{action}' "
                "(db_integrity | db_vacuum | prune_events | backup_now | clear_media | "
                "purge_trash | recheck)"
            ),
        )

    @app.get("/onboarding")
    def onboarding() -> dict[str, Any]:
        from ...onboarding import readiness

        return readiness(d.platform)

    # Serialises the check-then-write below: two quick presses (wizard + the
    # Overview card) must not both read "still mock" and race the write.
    use_model_lock = threading.Lock()

    @app.post("/onboarding/use-model")
    def onboarding_use_model(body: UseModelBody) -> dict[str, Any]:
        """The ONE explicit "use this for answers" press (v1.310.0, W2-1).

        THE TRAP IT CLEARS: a signed-in Claude Code / Codex user (or a fresh
        Ollama) is "connected", so the wizard never opens -- but the default
        never left the offline ``mock``, and their first answer was the
        scripted "Done. Wrote RESULT.md". Boot and rescan must NOT promote
        silently (cloud-vs-local is the user's privacy decision), so the
        wizard doors, the Overview card and the chat empty state all call
        this one route when the user presses.

        It replaces ONLY the untouched ``mock`` (or blank) default -- a
        user's own choice is never overwritten: that answers 200 with
        ``promoted: null`` and a sentence naming the choice. Unknown or
        unusable -> 409 with one plain sentence. A success is persisted
        exactly like ``POST /connections/{p}/default`` (config.toml + the
        default_provider/default_model /health reports). Sync on purpose:
        a local endpoint's model listing may touch the network, and the
        threadpool keeps that off the event loop.
        """
        from ...onboarding.checklist import default_is_mock, provider_label

        name = (body.provider or "").strip().lower()
        refusal = _use_model_refusal(d, name)
        if refusal is not None:
            raise HTTPException(status_code=409, detail=refusal)

        if name in _CLI_INHERITS:
            target = _CLI_INHERITS[name]
            # v1.310.0 (review): promote to the API name ONLY while that name
            # is SERVED THROUGH this sign-in. ``available(target)`` is also
            # true when the user has their own Anthropic/OpenAI key (env var
            # or vault) -- and a stored key always wins over the CLI, so the
            # door labelled "your Claude Code sign-in" would bill every answer
            # to the pay-per-use key. Otherwise (a key present, or inheritance
            # turned off) the CLI itself answers on its own subscription model
            # (the rescan rows' name).
            if d.platform.providers.inherited_from(target) == name:
                model = d._PROMOTE_DEFAULT_MODEL[target]
            else:
                target, model = name, "subscription"
        elif name in ("ollama", "custom"):
            target, model = name, _first_local_model(d, name)
            if not model:
                hint = " (for example `ollama pull llama3.2`)" if name == "ollama" else ""
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"{provider_label(d.platform, name)} is set up, but I "
                        f"couldn't find a model on it yet. Add one{hint}, then "
                        "try again."
                    ),
                )
        else:
            target = name
            model = d._PROMOTE_DEFAULT_MODEL.get(name, d.platform.config.default_model)

        cfg = d.platform.config
        with use_model_lock:
            if not default_is_mock(d.platform):
                current = str(cfg.default_provider)
                return {
                    "promoted": None,
                    "reason": (
                        f"You already chose {provider_label(d.platform, current)} "
                        "for answers, so nothing changed. You can switch any "
                        "time on the Connections page."
                    ),
                }
            cfg.default_provider = target
            cfg.default_model = model
            d._persist_config(["default_provider", "default_model"])
        log.info("default model chosen by the user's press: %s/%s", target, model)
        return {"promoted": {"provider": target, "model": model}, "reason": ""}

    @app.get("/doctor")
    def doctor_ep() -> dict[str, Any]:
        from ...onboarding import doctor

        # Pass the live platform so doctor also runs RUNTIME checks (model
        # connected, secrets key valid, DB integrity) — the failures a daily
        # driver actually hits, not just machine prerequisites.
        return doctor(d.platform)
