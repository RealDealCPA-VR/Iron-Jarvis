"""Your clients (v1.499.0, vision plan leap 3, Phase 3): the routes.

The contract is the docstring of ``workmap/clients/__init__.py``. Here:

* ``GET  /clients`` → the list, the names waiting for an answer and the
  names marked Not a client (``page.clients_list``).
* ``GET  /clients/of?folder=`` → ``{client: {id, name} | null}``.
* ``GET  /clients/{id}`` → one client's page (``page.client_page``).
* ``POST /clients {folder, name?}`` → an owner-made client.
* ``PATCH /clients/{id} {name}`` → renamed.
* ``POST /clients/{id}/confirm`` (``{name?}``) | ``/decline`` (``{expect?}``:
  a waiting name unless it says ``confirmed``) |
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
NOTICING_OFF = (
    "Notice clients in your folders is off, so Iron Jarvis did not look. "
    "It is under Settings › Memory & you."
)


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


class DeclineBody(BaseModel):
    #: The status the press was drawn for: "proposed" (default) or "confirmed".
    expect: str | None = None


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
    from ...workmap.models import CLIENT_CONFIRMED, CLIENT_PREF_TYPED, CLIENT_PROPOSED

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
        # Off means no pass ran: never "Looked again" over a look that did
        # not happen (the independent review).
        if not detect.enabled(_platform()):
            raise HTTPException(status_code=409, detail=NOTICING_OFF)
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
    def clients_decline(client_id: str, body: DeclineBody | None = None) -> dict[str, Any]:
        # A press with no ``expect`` is a one-press No for a WAITING name; a
        # confirmed client is taken away only by the page's two-press footer.
        expect = body.expect if body is not None and body.expect else CLIENT_PROPOSED
        if expect not in (CLIENT_PROPOSED, CLIENT_CONFIRMED):
            raise HTTPException(status_code=400, detail="Say which answer this is.")
        return _call(store.decline, _platform(), _id(client_id), expect=expect)

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
        cid = _id(client_id)
        prior = _client_snapshot(cid)
        out = _call(store.change_preference, _platform(), cid, pref_id, body.text)
        return _with_undo(out, cid, prior, tool="client_preference_change", op={
            "op": "change_pref", "id": cid, "pref_id": pref_id,
            "text": str((out.get("preference") or {}).get("text") or ""), "old": str(out.get("before") or ""),
        })

    @app.delete("/clients/{client_id}/preferences/{pref_id}")
    def clients_forget_preference(client_id: str, pref_id: str) -> dict[str, Any]:
        cid = _id(client_id)
        prior = _client_snapshot(cid)
        out = _call(store.forget_preference, _platform(), cid, pref_id)
        return _with_undo(out, cid, prior, tool="client_preference_forget", op={
            "op": "forget_pref", "id": cid, "pref_id": pref_id, "view": dict(out.get("preference") or {}),
        })

    # ---- Undo for a preference changed on a page (next leaps 15-a) ---------
    # A change made from the client's page or the chat's "From memory" box is
    # recorded the way chat's ``client_preference_change`` / ``_forget`` are:
    # one ledger row and one journal row whose ops ``records_clients`` undoes
    # (``POST /undo/{action_id}``: refused when the client changed again
    # since). The answer carries ``action_id`` ("" when it could not be
    # recorded: the change stands, the log says so).
    def _client_snapshot(cid: str) -> Any:
        try:
            from ...settings import records_clients as _rc

            return _rc.snap_client(_platform(), cid)
        except Exception:  # noqa: BLE001 — no snapshot: the change is made, with no Undo
            return None

    def _with_undo(out: dict[str, Any], cid: str, prior: Any, *, tool: str, op: dict[str, Any]) -> dict[str, Any]:
        if prior is None or not isinstance(out, dict):
            return out
        from ...settings import page_records as _pr
        from ...settings import records_clients as _rc

        after = _client_snapshot(cid)
        if after is None:
            return out
        name = str((out.get("client") or {}).get("name") or prior.get("name") or "")
        what = "Changed" if op.get("op") == "change_pref" else "Forgot"
        action = _pr.journal_removal(
            _platform(), tool=tool, record=_rc.KIND, name=cid, prior=prior,
            summary=f"{what} what is kept for {name}: “{str(op.get('old') or (op.get('view') or {}).get('text') or '')[:120]}”.",
            extra={"ops": [op], "after": after, "label_name": name},
        )
        return {**out, "action_id": action}


__all__ = ["register"]
