"""The sorting plan's card (v1.560.0, idea 31-a, "Sort this stack").

* ``GET  /documents/stack/{plan_id}`` — the card as it is now.
* ``POST /documents/stack/{plan_id}/apply`` — Confirm: writes the plan as the
  user left it, THROUGH ``registry.invoke("stack_apply")`` with the real
  permission engine (the ledger row, the undo journal, the roster gate). The
  press is the user's answer to the tool's ask, for this one call
  (``session_allow``); a tool the user set to Never run stays refused.
* ``POST /documents/stack/{plan_id}/undo`` — the card's Undo: the ordinary
  Undo of that one recorded call (``routes/undo.perform_undo``, pressed). It
  needs the ledger row, not the kept plan: a plan swept after
  ``stack.PLAN_DAYS`` days still undoes.
* ``POST /documents/stack/{plan_id}/read-more`` — "Read N more pages with
  <model>?": up to ``stack.READ_MORE_MAX`` of the pages this PC could not
  read go to THE MODEL THE CARD NAMED (``provider``), each call pinned to it,
  and to no other; if the choice changed since, nothing is sent and the card
  shows the new name. Then the plan is made again (one model call) with what
  was read.

LOCAL ONLY on every press: the request's flag OR the saved chat's setting OR
the verdict of the turn that made the plan (``plan["local_only"]``; a new
chat's first turn has no thread id yet). It covers which model is offered,
the pages sent and the second planning call.

Registered from ``routes/documents.register``. Every blocking step (the plan
file, the PDF work, the ledger read, the availability probes) runs off the
event loop.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from sqlmodel import select

from ...core.db import session_scope
from ...documents import stack as S
from ..schemas import StackApplyBody, StackReadMoreBody


def _apply_action(engine: Any, plan_id: str) -> str:
    """The ledger row of this plan's newest successful write that is not
    undone yet ("" when there is none)."""
    from ...core.models import ToolInvocation, UndoJournal

    needle = f'"{plan_id}"'
    with session_scope(engine) as db:
        rows = db.exec(
            select(ToolInvocation)
            .where(ToolInvocation.tool == "stack_apply", ToolInvocation.ok == True)  # noqa: E712
            .where(ToolInvocation.undone_at == None)  # noqa: E711 — SQL, not Python
            .where(ToolInvocation.args_json.contains(needle))
            .order_by(ToolInvocation.created_at.desc())
        ).all()
        for row in rows:
            if db.get(UndoJournal, row.id) is not None:
                return str(row.id)
    return ""


async def plan_local_only(engine: Any, plan: "dict[str, Any]", local_only: Any = None, thread_id: Any = "") -> bool:
    """A press's Local only verdict: the request's flag, the plan's own (the
    turn that made it), or the saved chat's (by the request's thread id, else
    the plan's). Any one of them is enough."""
    from ...providers import egress as _egress

    asked = local_only is True or plan.get("local_only") is True
    return await _egress.effective_local_only(
        engine, asked, str(thread_id or "") or str(plan.get("thread_id") or ""),
    )


def register(app: FastAPI, d) -> None:
    def _plan(plan_id: str) -> "dict[str, Any]":
        try:
            return S.load_plan(d.platform.config.home, plan_id)
        except S.StackError as exc:
            raise HTTPException(status_code=404, detail=str(exc))

    def _plan_or_none(plan_id: str) -> "dict[str, Any] | None":
        try:
            return S.load_plan(d.platform.config.home, plan_id)
        except S.StackError:
            return None

    async def _choice(lo: bool) -> "dict[str, Any] | None":
        from ...providers import egress as _egress

        def _pick() -> "dict[str, Any] | None":
            with _egress.local_only_scope(lo):
                return S.vision_choice(getattr(d.platform, "router", None), d.platform.config)

        return await asyncio.to_thread(_pick)

    async def _offer(plan: "dict[str, Any]", thread_id: str = "", local_only: Any = None) -> "dict[str, Any] | None":
        if not any(pg.how == S.HOW_UNREAD for pg in S.pages_of(plan)):
            return None
        return await _choice(await plan_local_only(d.platform.engine, plan, local_only, thread_id))

    @app.get("/documents/stack/{plan_id}")
    async def stack_plan_card(plan_id: str, thread_id: str = "", local_only: bool = False) -> dict[str, Any]:
        plan = await asyncio.to_thread(_plan, plan_id)
        return {"card": S.card_of(plan, offer=await _offer(plan, thread_id, local_only))}

    @app.post("/documents/stack/{plan_id}/apply")
    async def stack_plan_apply(plan_id: str, body: StackApplyBody) -> dict[str, Any]:
        from ...tools.base import ToolContext

        plan = await asyncio.to_thread(_plan, plan_id)
        if d.platform.registry.get("stack_apply") is None:
            raise HTTPException(status_code=501, detail="Writing a sorting plan is not available here.")
        ctx = ToolContext(
            workspace=Path(str(plan.get("workspace") or "")),
            session_id="chat",
            agent_run_id="chat",
            config=d.platform.config,
            event_bus=d.platform.event_bus,
            engine=d.platform.engine,
            thread_id=str(plan.get("thread_id") or body.thread_id or ""),
        )
        args: dict[str, Any] = {"plan_id": plan_id}
        if body.documents is not None:
            args["documents"] = body.documents
        # THE PRESS IS THE ANSWER to this one call's ask, nothing wider: the
        # name is granted for this invoke only, the roster holds this one
        # tool, and a Never run (deny) is never lifted by it.
        result = await d.platform.registry.invoke(
            "stack_apply", args, ctx, d.platform.permissions, None,
            session_allow={"stack_apply"}, allowed_names={"stack_apply"},
        )
        if not result.ok:
            raise HTTPException(status_code=409, detail=result.error or "The documents were not written.")
        action_id = await asyncio.to_thread(_apply_action, d.platform.engine, plan_id)
        fresh = await asyncio.to_thread(_plan, plan_id)
        data = result.data or {}
        return {
            "output": result.output,
            "files": (data.get("stack_applied") or {}).get("files") or [],
            "action_id": action_id,
            "card": S.card_of(fresh),
        }

    @app.post("/documents/stack/{plan_id}/undo")
    async def stack_plan_undo(plan_id: str) -> dict[str, Any]:
        from .undo import perform_undo

        if not S.valid_plan_id(plan_id):
            raise HTTPException(status_code=404, detail="That plan does not exist.")
        # The ledger row is what is undone; the kept plan may have been swept.
        action_id = await asyncio.to_thread(_apply_action, d.platform.engine, plan_id)
        if not action_id:
            raise HTTPException(status_code=409, detail="There is nothing of this plan to undo.")
        out = await perform_undo(d, action_id, pressed=True)
        fresh = await asyncio.to_thread(_plan_or_none, plan_id)
        return {
            "output": out.get("output", "") if isinstance(out, dict) else "",
            "card": S.card_of(fresh) if fresh is not None else None,
        }

    @app.post("/documents/stack/{plan_id}/read-more")
    async def stack_plan_read_more(plan_id: str, body: StackReadMoreBody) -> dict[str, Any]:
        from ...providers import egress as _egress

        plan = await asyncio.to_thread(_plan, plan_id)
        if plan.get("applied"):
            raise HTTPException(status_code=409, detail="This plan was already written. Undo it first to change it.")
        router = getattr(d.platform, "router", None)
        home = d.platform.config.home
        lo = await plan_local_only(d.platform.engine, plan, body.local_only, body.thread_id)
        choice = await _choice(lo)
        if choice is None:
            raise HTTPException(status_code=409, detail=(
                "No connected model can read pictures here, so those pages stay where they are on the plan."
            ))
        if not body.provider or choice["provider"] != body.provider:
            # Never a silent swap: the user pressed for the model the card
            # named, and a press that names none sends nothing either (the
            # rule is this route's, whatever the client). Nothing was sent;
            # the card now names the model a press would reach.
            return {"read": 0, "sent": 0, "stopped": "", "changed": True, "card": S.card_of(plan, offer=choice)}
        with _egress.local_only_scope(lo):
            try:
                # Each page is saved as soon as it is read, so a model that
                # fails later (or a card that stops waiting) never makes the
                # user pay for the same pages twice.
                res = await S.read_more(
                    plan, router=router, config=d.platform.config, choice=choice,
                    save=lambda p: S.save_plan(home, p),
                )
            except S.StackError as exc:
                raise HTTPException(status_code=409, detail=str(exc))
            await asyncio.to_thread(S.save_plan, home, plan)
            if res.read:
                try:
                    docs = await S.plan_pages_async(
                        S.pages_of(plan), source_name=str(plan.get("source_name") or ""), router=router,
                        config=d.platform.config,
                    )
                except S.StackError as exc:
                    raise HTTPException(status_code=409, detail=f"The pages were read, but the new plan was not made: {exc}")
                plan["documents"] = docs
                await asyncio.to_thread(S.save_plan, home, plan)
        offer = await _choice(lo) if res.left else None
        return {
            "read": res.read, "sent": res.sent, "left": res.left, "stopped": res.stopped, "changed": False,
            "card": S.card_of(plan, offer=offer),
        }


__all__ = ["plan_local_only", "register"]
