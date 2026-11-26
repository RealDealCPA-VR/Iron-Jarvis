"""Search by meaning's routes (next leaps 16-a, build-out plan R2.11, idea 16;
``memory/meaning.py`` has the rules).

* ``GET /memory/meaning`` -> ``memory.meaning.view``: the state, the status
  line, the offer (never answered and a model to offer), every search model
  found on this PC or the owner's network with what it costs here, and
  whether a look is running. Reading it starts a look on its own thread
  (``meaning.page_look``) when the last one is older than ``FIND_EVERY_S``
  (the page re-reads while ``looking``, only while it is on screen).
  ``?all=1`` (the model list is open, "Use another model") measures every
  model found that has no cost yet, a few a look; the list re-reads until
  each has one, and only a model that answered can be chosen there.
* ``POST /memory/meaning/choose {choice}`` -> turn it on with that model (or
  "on": the one chosen before, else the first that works), through THE
  settings writer: one probe of the model first, one ledger row with Undo.
* ``POST /memory/meaning/decline`` -> Not now: answered, never offered again.
* ``POST /memory/meaning/off`` -> off (the ledger row's Undo turns it back on).

Each answers ``{ok, note, view}``; a refusal is a 400 with the sentence the
owner reads. Plain ``def`` handlers: the writer probes a server and writes a
file, so FastAPI runs them off the event loop.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException


def register(app: FastAPI, d: Any) -> None:
    from ...memory import meaning

    def _writer() -> Any:
        from .settings import config_writer

        return config_writer(d)

    def _answer(value: Any, note: str) -> dict[str, Any]:
        from ...settings.writer import SettingError

        try:
            change = _writer().apply({meaning.SETTING: value}, actor="settings_page")
        except SettingError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        view = meaning.view(d)
        return {"ok": True, "note": note or view["words"], "view": view, "action_id": change.action_id}

    @app.get("/memory/meaning")
    def meaning_view(all: int = 0) -> dict[str, Any]:  # noqa: A002 — the query's own name
        meaning.page_look(d, every=bool(all))
        return meaning.view(d)

    @app.post("/memory/meaning/choose")
    def meaning_choose(body: dict[str, Any]) -> dict[str, Any]:
        choice = str((body or {}).get("choice") or "").strip() or "on"
        out = _answer(choice, "")
        out["note"] = out["view"]["words"]
        return out

    @app.post("/memory/meaning/decline")
    def meaning_decline() -> dict[str, Any]:
        return _answer("", "Not now. Iron Jarvis won't offer it again; Settings › Memory & you › Search by "
                           "meaning can turn it on.")

    @app.post("/memory/meaning/off")
    def meaning_off() -> dict[str, Any]:
        return _answer("", "Search by meaning is off. Searches match words.")
