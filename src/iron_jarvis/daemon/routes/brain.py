"""Brain Profile routes (v1.406.0, ``brain/``).

``GET /brain`` (profiles, the app's choice, the memory bases, unsaved count),
profile CRUD, ``GET /brain/effective`` (what applies to a project), and the
unsaved changes (``/brain/pending``) with Retry / Discard. The app-wide choice
itself is a settings row (``brain_profile``) written through the ONE writer
(``PUT /settings/values``); a project's choice rides ``PATCH /projects/{id}``.
Every route is a sync ``def`` — FastAPI runs it off the event loop.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException
from ...brain import profiles as P
from ...brain.tools import already_saved, pending_rows, write_note
from ...brain.turn import forget_guides


def register(app: FastAPI, d: Any) -> None:
    def _store() -> P.BrainProfileStore:
        return P.BrainProfileStore(d.platform)

    @app.get("/brain")
    def brain_view() -> dict[str, Any]:
        """Everything the Settings section draws, in one read."""
        return {
            "profiles": _store().list(),
            "selected": str(getattr(d.platform.config, "brain_profile", "") or ""),
            "bases": P.base_rows(d.platform),
            "pending": len(pending_rows(d.platform)),
        }

    @app.post("/brain/profiles", status_code=201)
    def brain_create(body: dict[str, Any]) -> dict[str, Any]:
        try:
            return _store().create(body)
        except P.ProfileError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

    @app.patch("/brain/profiles/{profile_id}")
    def brain_update(profile_id: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            row = _store().update(profile_id, body)
        except P.ProfileError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        if row is None:
            raise HTTPException(status_code=404, detail="That profile no longer exists.")
        forget_guides()  # a new guide ref must be read at once, not in 10 minutes
        return row

    @app.delete("/brain/profiles/{profile_id}")
    def brain_delete(profile_id: str) -> dict[str, Any]:
        out = _store().delete(profile_id)
        if out is None:
            raise HTTPException(status_code=404, detail="That profile no longer exists.")
        return out

    @app.get("/brain/effective")
    def brain_effective(project_id: str = "") -> dict[str, Any]:
        return P.effective_view(d.platform, project_id.strip() or None)

    @app.get("/brain/pending")
    def brain_pending() -> dict[str, Any]:
        return {"pending": pending_rows(d.platform)}

    def _pending_row(pending_id: str):
        from ...core.db import session_scope
        from ...brain.models import BrainPendingWrite

        with session_scope(d.platform.engine) as db:
            row = db.get(BrainPendingWrite, pending_id)
            if row is None or row.status != "pending":
                raise HTTPException(status_code=404, detail="That unsaved change is gone.")
            db.expunge(row)
            return row

    def _settle(pending_id: str, status: str, error: str = "") -> None:
        from ...core.db import session_scope
        from ...brain.models import BrainPendingWrite

        with session_scope(d.platform.engine) as db:
            row = db.get(BrainPendingWrite, pending_id)
            if row is not None:
                row.status = status
                if error:
                    row.error = error[:500]
                db.add(row)
                db.commit()

    @app.post("/brain/pending/{pending_id}/retry")
    def brain_pending_retry(pending_id: str) -> dict[str, Any]:
        """Send one kept save again, to the base it was meant for, through
        the SAME save path the tool uses (look for the note, add or create,
        read back). Still unreachable = 409 in words and the row stays."""
        row = _pending_row(pending_id)
        profile = _store().get(row.profile_id) or {
            "id": row.profile_id, "name": row.profile_name, "primary": row.base,
            "prefer_update": True,
        }
        active = P.ActiveBrain(profile={**profile, "primary": row.base}, source="app")
        conn = d.platform.ltm.get(row.base)
        label = P.base_label(d.platform, row.base)
        if conn is None:
            raise HTTPException(status_code=409, detail=f"{label} is not set up as a memory base any more.")
        # v1.408.0: the earlier attempt may have LANDED and then lost its
        # answer (a timeout on the reply) — then sending it again would add
        # the same text twice. Look first.
        landed = already_saved(d.platform, active, conn, row.title, row.content)
        if landed:
            _settle(pending_id, "saved")
            return {"ok": True, "saved": {"ref": landed, "verified": True, "action": "already"}}
        res = write_note(d.platform, active, conn, row.title, row.content)
        if not res["ok"]:
            kind = res.get("kind") or ("unconfirmed" if res.get("action") else "unreachable")
            _settle(pending_id, "pending", res["error"])
            if kind == "unconfirmed":
                # It answered but the note is not there — do not keep re-sending.
                raise HTTPException(status_code=409, detail=f"Not confirmed in {label}: {res['error']}.")
            if kind == "setup":
                raise HTTPException(status_code=409, detail=res["error"])
            if kind == "refused":
                raise HTTPException(
                    status_code=409,
                    detail=f"{label} refused it: {res['error']}. Retrying will not help; discard it or change it there.",
                )
            raise HTTPException(status_code=409, detail=f"{label} is still unavailable: {res['error']}")
        _settle(pending_id, "saved")
        return {"ok": True, "saved": {"ref": res["ref"], "verified": bool(res["verified"]),
                                       "action": res["action"]}}

    # -- v1.408.0: check a profile against the REAL memory base -------------
    def _active_for(profile_id: str):
        profile = _store().get(profile_id)
        if profile is None:
            raise HTTPException(status_code=404, detail="That profile no longer exists.")
        active = P.ActiveBrain(profile=profile, source="app")
        conn = d.platform.ltm.get(active.base)
        return active, conn, P.base_label(d.platform, active.base)

    @app.post("/brain/profiles/{profile_id}/check")
    def brain_check(profile_id: str) -> dict[str, Any]:
        """READ-ONLY steps against the profile's memory base, each said in
        words: can it be reached, does a search answer, can the operating
        guide be read, and can it save (which tool, for an MCP base). Nothing
        is written. Sync — the base's own calls run in the threadpool."""
        active, conn, label = _active_for(profile_id)
        steps: list[dict[str, Any]] = []
        if conn is None:
            steps.append({"step": "reach", "ok": False,
                          "words": f"{label} is not set up as a memory base any more."})
            return {"steps": steps}
        health = getattr(conn, "health", None)
        try:
            verdict = health() if callable(health) else None
        except Exception as exc:  # noqa: BLE001
            verdict = {"available": False, "detail": str(exc)[:200]}
        if isinstance(verdict, dict) and verdict.get("available") is False:
            steps.append({"step": "reach", "ok": False,
                          "words": f"{label} could not be reached: {verdict.get('detail') or 'no answer'}."})
            return {"steps": steps}
        steps.append({"step": "reach", "ok": True if isinstance(verdict, dict) else None,
                      "words": f"{label} answered." if isinstance(verdict, dict)
                      else f"{label} has no quick check; the search below tells."})
        try:
            hits = d.platform.ltm.search(active.name or "notes", 3, active.base, expand=False)
            steps.append({"step": "search", "ok": True,
                          "words": f"Search works ({len(hits)} note(s) for “{active.name or 'notes'}”)."})
        except Exception as exc:  # noqa: BLE001
            steps.append({"step": "search", "ok": False, "words": f"Search failed: {str(exc)[:200]}"})
        ref = str(active.profile.get("guide_ref") or "").strip()
        if ref:
            try:
                text = conn.read(ref)
                ok = bool(str(text or "").strip())
                steps.append({"step": "guide", "ok": ok, "words": (
                    f"The operating guide “{ref}” was read ({len(text)} characters)." if ok
                    else f"The operating guide “{ref}” is empty.")})
            except Exception as exc:  # noqa: BLE001
                steps.append({"step": "guide", "ok": False,
                              "words": f"The operating guide “{ref}” could not be read: {str(exc)[:200]}"})
        else:
            steps.append({"step": "guide", "ok": None, "words": "No operating guide is set."})
        if active.profile.get("save_to") == "never":
            steps.append({"step": "save", "ok": None, "words": "This profile never saves."})
        else:
            setup = getattr(conn, "setup_needed", None)
            sentence = setup("create") if callable(setup) else ""
            if sentence:
                steps.append({"step": "save", "ok": False, "words": sentence})
            else:
                tool = ((getattr(conn, "roles", None) or {}).get("create") or {}).get("tool")
                steps.append({"step": "save", "ok": None, "words": (
                    f"Saves use {tool}. Press Try a test save to check one." if tool
                    else "Saving is possible. Press Try a test save to check one.")})
        return {"steps": steps}

    @app.post("/brain/profiles/{profile_id}/test-save")
    def brain_test_save(profile_id: str) -> dict[str, Any]:
        """ONE real save the user pressed for, through the SAME save path a
        chat uses (look up, add or create, read back), of a note that says
        what it is. Nothing is queued on failure: this is a test."""
        import datetime as _dt

        active, conn, label = _active_for(profile_id)
        if conn is None:
            raise HTTPException(status_code=409, detail=f"{label} is not set up as a memory base any more.")
        if active.profile.get("save_to") == "never":
            raise HTTPException(status_code=409, detail="This profile never saves, so there is nothing to test.")
        stamp = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
        title = "Iron Jarvis test note"
        content = f"A test save from Iron Jarvis ({stamp}). It is safe to delete this note."
        res = write_note(d.platform, active, conn, title, content)
        if res["ok"]:
            confirmed = "read back and confirmed" if res["verified"] else "saved, but this base cannot be read back to confirm it"
            return {"ok": True, "ref": res["ref"], "verified": bool(res["verified"]),
                    "words": f"Saved “{title}” to {label} ({confirmed}). You can delete it there."}
        return {"ok": False, "verified": False, "words": f"The test save did not work: {res['error']}"}

    @app.delete("/brain/pending/{pending_id}")
    def brain_pending_discard(pending_id: str) -> dict[str, Any]:
        _pending_row(pending_id)
        _settle(pending_id, "discarded")
        return {"discarded": True}
