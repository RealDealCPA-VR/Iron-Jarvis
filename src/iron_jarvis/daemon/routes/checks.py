"""The second model's double-check (the intelligence plan, 5b;
``checks/second`` has the rules).

* ``GET /checks/double-check`` -> ``{state, words, choice, offer}``: how it
  stands ("unset" | "off" | "on"), the model and account a yes stored, and
  what a yes NOW would store (``offer``: null with fewer than two model
  accounts). A read.
* ``POST /checks/double-check {answer}`` -> ``answer`` is "no", or the
  ``choice`` the card named: it must be what a yes stores now
  (``checks/second.value_for``), else a 400 with the sentence. Written
  through THE settings writer (one ledger row with Undo). ``{ok, note,
  view, action_id}``.
* ``GET /checks/double-check/run/{token}`` -> ``{running, checked}``: the
  page asks while a reply's double-check runs; ``running`` is True only
  while it really is, and ``checked`` is the reply's whole line once it has
  landed. An unknown token (a restart) is not running and has no line.

Plain ``def`` handlers: the writer writes a file, the record is a database
read, so FastAPI runs them off the event loop.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException


def register(app: FastAPI, d: Any) -> None:
    from ...checks import second

    @app.get("/checks/double-check")
    def double_check_view() -> dict[str, Any]:
        return second.view(d.platform)

    @app.post("/checks/double-check")
    def double_check_answer(body: dict[str, Any]) -> dict[str, Any]:
        from ...settings.writer import SettingError
        from .settings import config_writer

        try:
            value = second.value_for(d.platform, (body or {}).get("answer"))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        try:
            change = config_writer(d).apply({second.SETTING: value}, actor="settings_page")
        except SettingError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        view = second.view(d.platform)
        note = (
            f"{view['words']} double-checks finished work. Settings › Models changes it." if value
            else "Iron Jarvis's own checks only. Settings › Models changes it."
        )
        return {"ok": True, "note": note, "view": view, "action_id": change.action_id}

    @app.get("/checks/double-check/run/{token}")
    def double_check_run(token: str) -> dict[str, Any]:
        return second.status(d.platform.engine, token)
