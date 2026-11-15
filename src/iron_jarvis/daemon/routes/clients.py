"""Your clients (v1.499.0, vision plan leap 3, Phase 3): the routes.

The contract is the docstring of ``workmap/clients/__init__.py``. Here:

* ``GET  /clients`` → the list, the names waiting for an answer and the
  names marked Not a client (``page.clients_list``).
* ``GET  /clients/of?folder=`` → ``{client: {id, name} | null}``.
* ``GET  /clients/{id}`` → one client's page (``page.client_page``).
* ``POST /clients {folder, name?}`` → an owner-made client.
* ``PATCH /clients/{id} {name}`` → renamed.
* ``POST /clients/{id}/confirm`` (``{name?}``) | ``/decline`` |
  ``/ask-again`` | ``/restore``.
* ``POST /clients/confirm-many {ids}`` → ``{confirmed, skipped}``.
* ``POST /clients/{id}/folders {folder}``; ``POST /clients/{id}/folders/
  remove {folder}``.
* ``POST /clients/{id}/preferences {text}`` (typed on the page);
  ``PATCH`` / ``DELETE /clients/{id}/preferences/{pid}``.
* ``POST /clients/notice`` → "Look again now" (a forced noticing pass).

Every handler is a plain ``def``: the reads and writes are SQLite, so
FastAPI runs them off the event loop. GETs WRITE NOTHING and never notice.
Errors are plain sentences: 404 not on record, 409 already answered, 400 a
folder rule or a text check, 503 "Your clients could not be read. Nothing
was changed." Not under ``/memory/`` (``GET /memory/{layer}/{key}`` would
answer for it).
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from ...workmap.clients import CLIENT_ID_RE, ClientError, ClientNotFound

NOT_ON_RECORD = ClientNotFound.default


class CreateBody(BaseModel):
    folder: str = ""
    name: str = ""


class NameBody(BaseModel):
    name: str = ""


class ConfirmBody(BaseModel):
    name: str | None = None


class ManyBody(BaseModel):
    ids: list[str] = []


class FolderBody(BaseModel):
    folder: str = ""


class TextBody(BaseModel):
    text: str = ""


def _call(fn, *args, **kwargs):
    """Run one client read or change; its errors are plain-sentence HTTP."""
    try:
        return fn(*args, **kwargs)
    except ClientError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.sentence) from None


def _id(client_id: str) -> str:
    cid = str(client_id or "")
    if not CLIENT_ID_RE.match(cid):
        raise HTTPException(status_code=404, detail=NOT_ON_RECORD)
    return cid


def register(app: FastAPI, d) -> None:
    """Attach the client routes; ``d`` is the create_app deps object."""
    from ...workmap.clients import detect, page, store
    from ...workmap.models import CLIENT_PREF_TYPED

    def _platform():
        return d.platform

    # ---- reads (write nothing, notice nothing) ---------------------------
    @app.get("/clients")
    def clients_list() -> dict[str, Any]:
        return _call(page.clients_list, _platform())

    @app.get("/clients/of")
    def client_of(folder: str = "") -> dict[str, Any]:
        return _call(page.client_of_folder, _platform(), str(folder or ""))

    @app.get("/clients/{client_id}")
    def client_page(client_id: str) -> dict[str, Any]:
        return _call(page.client_page, _platform(), _id(client_id))

    # ---- answers -----------------------------------------------------------
    @app.post("/clients/confirm-many")
    def clients_confirm_many(body: ManyBody) -> dict[str, Any]:
        return _call(store.confirm_many, _platform(), list(body.ids or []))

    @app.post("/clients/notice")
    def clients_notice() -> dict[str, Any]:
        minted = _call(detect.notice, _platform(), force=True)
        return {"ok": True, "noticed": int(minted or 0)}

    @app.post("/clients")
    def clients_create(body: CreateBody) -> dict[str, Any]:
        return _call(store.create, _platform(), folder=body.folder, name=body.name)

    @app.patch("/clients/{client_id}")
    def clients_rename(client_id: str, body: NameBody) -> dict[str, Any]:
        return _call(store.rename, _platform(), _id(client_id), body.name)

    @app.post("/clients/{client_id}/confirm")
    def clients_confirm(client_id: str, body: ConfirmBody | None = None) -> dict[str, Any]:
        name = body.name if body is not None else None
        return _call(store.confirm, _platform(), _id(client_id), name=name)

    @app.post("/clients/{client_id}/decline")
    def clients_decline(client_id: str) -> dict[str, Any]:
        return _call(store.decline, _platform(), _id(client_id))

    @app.post("/clients/{client_id}/ask-again")
    def clients_ask_again(client_id: str) -> dict[str, Any]:
        return _call(store.ask_again, _platform(), _id(client_id))

    @app.post("/clients/{client_id}/restore")
    def clients_restore(client_id: str) -> dict[str, Any]:
        return _call(store.restore, _platform(), _id(client_id))

    # ---- folders -------------------------------------------------------------
    @app.post("/clients/{client_id}/folders")
    def clients_add_folder(client_id: str, body: FolderBody) -> dict[str, Any]:
        return _call(store.add_folder, _platform(), _id(client_id), body.folder)

    @app.post("/clients/{client_id}/folders/remove")
    def clients_remove_folder(client_id: str, body: FolderBody) -> dict[str, Any]:
        return _call(store.remove_folder, _platform(), _id(client_id), body.folder)

    # ---- preferences (typed on the page: the owner typed it, no card) -------
    @app.post("/clients/{client_id}/preferences")
    def clients_keep_preference(client_id: str, body: TextBody) -> dict[str, Any]:
        return _call(store.keep_preference, _platform(), _id(client_id), body.text,
                     origin=CLIENT_PREF_TYPED)

    @app.patch("/clients/{client_id}/preferences/{pref_id}")
    def clients_change_preference(client_id: str, pref_id: str, body: TextBody) -> dict[str, Any]:
        return _call(store.change_preference, _platform(), _id(client_id), pref_id, body.text)

    @app.delete("/clients/{client_id}/preferences/{pref_id}")
    def clients_forget_preference(client_id: str, pref_id: str) -> dict[str, Any]:
        return _call(store.forget_preference, _platform(), _id(client_id), pref_id)


__all__ = ["register"]
