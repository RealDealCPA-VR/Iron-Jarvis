"""Preferences you approve — the routes (v1.305.0; idea from agent-personalizer, MIT).

* ``GET    /memory/preferences`` → ``{kept, suggested, never, open_limit, scan}``;
* ``POST   /memory/preferences/{id}/keep {text?}`` → ``{preference}`` (proposed → confirmed);
* ``POST   /memory/preferences/{id}/decline`` → ``{preference}`` (→ declined, final);
* ``POST   /memory/preferences/{id}/ask-again`` → ``{deleted}`` (removes a declined row);
* ``PATCH  /memory/preferences/{id} {text}`` → ``{preference}`` (edit a kept one);
* ``DELETE /memory/preferences/{id}`` → ``{deleted}`` (forget a kept one);
* ``POST   /memory/preferences/scan {sources}`` → ``{sessions_read, by_source,
  suggestions, suggested, skipped_full}`` — the consent-per-press look through
  the user's own Claude Code / Codex typed messages (nothing automatic).

Every DB / file read runs off the loop. Refusals are plain sentences: 404
unknown id, 409 a row in the wrong state, 400 a sentence that is empty, too
long or flagged by the prompt-injection scanner, or an unknown scan source.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from ...learning import preferences as prefs


class KeepBody(BaseModel):
    text: str | None = None


class EditBody(BaseModel):
    text: str = ""


class ScanBody(BaseModel):
    sources: list[str] = []


def _run(fn, *args):
    """Call a preferences action, mapping its errors to plain-sentence HTTP."""
    try:
        return fn(*args)
    except KeyError:
        raise HTTPException(status_code=404, detail="There is no such preference.") from None
    except prefs.PreferenceConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except prefs.PreferenceInvalid as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


def _said(text: Any) -> str:
    """A preference's sentence as the ledger's summary says it: masked
    (``learning.engine.without_secrets``) BEFORE it is cut at 120. A row kept
    by an older path (``POST /lessons``) may still hold a secret-looking
    value, and the summary is a ledger line, which never carries one."""
    from ...learning.engine import without_secrets

    return without_secrets(str(text or ""))[:120]


def register(app: FastAPI, d) -> None:
    """Attach the preference routes to *app*; ``d`` is the create_app deps object."""

    def _engine():
        return d.platform.engine

    @app.get("/memory/preferences")
    async def memory_preferences() -> dict[str, Any]:
        """Kept, suggested and never-ask preferences, plus which other coding
        agents' history this PC has (for the look-through button)."""
        out = await asyncio.to_thread(prefs.list_preferences, _engine())
        out["scan"] = {"sources": await asyncio.to_thread(prefs.sources_available)}
        return out

    @app.post("/memory/preferences/scan")
    async def memory_preferences_scan(body: ScanBody) -> dict[str, Any]:
        """Look through YOUR typed messages in your newest Claude Code / Codex
        sessions (read-only, on this PC) for repeated corrections — only when
        pressed; returns how many sessions were read and what was suggested."""
        wanted = [str(s).strip() for s in (body.sources or []) if str(s).strip()]
        if not wanted:
            raise HTTPException(
                status_code=400,
                detail="Choose what to look through: claude-code, codex or both.",
            )
        unknown = [s for s in wanted if s not in prefs.SCAN_SOURCES]
        if unknown:
            raise HTTPException(
                status_code=400,
                detail=f"{unknown[0]!r} is not something Jarvis can look through — use claude-code or codex.",
            )
        sources = list(dict.fromkeys(wanted))
        result = await asyncio.to_thread(prefs.scan_sources, _engine(), sources)
        minted = result.pop("_minted", [])
        await prefs.publish_scan(d.platform, minted)
        return result

    @app.post("/memory/preferences/{pref_id}/keep")
    async def memory_preference_keep(pref_id: str, body: KeepBody | None = None) -> dict[str, Any]:
        """Keep a suggestion as a standing preference (optionally edited first)."""
        text = body.text if body is not None else None
        row = await asyncio.to_thread(_run, prefs.keep, _engine(), pref_id, text)
        await prefs.publish_kept(d.platform, row)
        return {"preference": prefs.pref_view(row)}

    @app.post("/memory/preferences/{pref_id}/decline")
    async def memory_preference_decline(pref_id: str) -> dict[str, Any]:
        """Not this — the suggestion is never asked again."""
        row = await asyncio.to_thread(_run, prefs.decline, _engine(), pref_id)
        await prefs.publish_declined(d.platform, row)
        return {"preference": prefs.pref_view(row)}

    @app.post("/memory/preferences/{pref_id}/ask-again")
    async def memory_preference_ask_again(pref_id: str) -> dict[str, Any]:
        """Forget a "not this", so the same correction may be suggested again."""
        deleted = await asyncio.to_thread(_run, prefs.ask_again, _engine(), pref_id)
        return {"deleted": deleted}

    @app.patch("/memory/preferences/{pref_id}")
    async def memory_preference_edit(pref_id: str, body: EditBody) -> dict[str, Any]:
        """Change the sentence of a kept preference.

        Next leaps 15-a: recorded with its Undo, the way the DELETE below and
        chat's ``preference_edit`` are (snapshot before and after, one ledger
        row, ``POST /undo/{action_id}`` puts the old sentence back unless it
        was changed again since). The answer carries ``action_id`` ("" when
        it could not be recorded: the change stands)."""
        from ...settings import page_records as _pr

        prior = await asyncio.to_thread(_pr.snapshot, d.platform, "preference", pref_id)
        row = await asyncio.to_thread(_run, prefs.edit, _engine(), pref_id, body.text)
        out: dict[str, Any] = {"preference": prefs.pref_view(row)}
        if prior is not None:
            after = await asyncio.to_thread(_pr.snapshot, d.platform, "preference", pref_id)
            out["action_id"] = await asyncio.to_thread(
                lambda: _pr.journal_removal(
                    d.platform, tool="preference_edit", record="preference", name=pref_id, prior=prior,
                    summary=(f"Changed the preference “{_said(prior.get('text'))}” to "
                             f"“{_said(row.text)}”."),
                    extra={"after": after},
                )
            )
        return out

    @app.delete("/memory/preferences/{pref_id}")
    async def memory_preference_forget(pref_id: str) -> dict[str, Any]:
        """Forget a kept preference.

        v1.504.0 (north-star plan 4.8): recorded with its Undo, the way chat's
        preference_forget is (snapshot before, journal after; off the loop)."""
        from ...settings import page_records as _pr

        prior = await asyncio.to_thread(_pr.snapshot, d.platform, "preference", pref_id)
        deleted = await asyncio.to_thread(_run, prefs.forget, _engine(), pref_id)
        out: dict[str, Any] = {"deleted": deleted}
        if deleted and prior is not None:
            out["action_id"] = await asyncio.to_thread(
                lambda: _pr.journal_removal(
                    d.platform, tool="preference_forget", record="preference", name=pref_id, prior=prior,
                    summary=f"Forgot the preference “{_said(prior.get('text'))}”.",
                    extra={"after": None},
                )
            )
        return out
