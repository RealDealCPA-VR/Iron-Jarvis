"""Apps that talk back — the routes (v1.324.0, wave C; ideas from the MCP spec
2025-06-18, no code copied).

* ``POST /chat/mcp/elicitations/{id}`` ``{action, content?}`` — the user's
  answer to a pack's question card. 200 ``{ok: true}``; 404 an unknown or
  finished id; 400 ``{detail: {"errors": {field: sentence}}}`` when an
  accepted answer fails ``interact.check_elicitation_answer`` — the ask
  STAYS pending, so the card can show the errors and send again.
* ``POST /chat/mcp/sampling/{id}`` ``{decision: "approve"|"deny"}`` — the
  user's Allow / Deny on a pack's request to ask the turn's model. 200 / 404.
* ``GET /mcp/prompts`` — every running pack's prompts (the "/" picker's
  "From your apps"): ``{prompts: [...], failed: [{pack, error}]}``.
* ``POST /mcp/prompts/get`` ``{pack, name, arguments}`` — one prompt's text
  for the composer (never sent by itself): ``{text, messages, flagged}``.
* ``GET /mcp/resources?q=`` — every running pack's resources (the "@"
  picker's "From your apps"): ``{resources: [...], failed}``.

Every pack call goes through the pack's live ``MCPClient`` (its transport
runs on a worker thread — nothing here blocks the loop), bounded per pack,
and lists are cached :data:`CACHE_S` seconds per pack (a reloaded pack —
a new client object — is read afresh). A pack that answers -32601 / "Method
not found" to a list request simply has none (the client returns ``[]``).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from ...core.promptguard import publish_blocked, scan_context
from .. import mcp_turn

log = logging.getLogger(__name__)

#: A pack's prompt / resource list is reused this long.
CACHE_S = 60.0
#: One pack's list request is bounded by this.
LIST_TIMEOUT_S = 5.0
#: One ``prompts/get`` is bounded by this.
GET_TIMEOUT_S = 15.0
#: At most this many resource rows come back.
MAX_RESOURCE_ROWS = 200
#: Text caps on what a pack describes itself with.
_NAME_CAP = 200
_DESC_CAP = 1_000
_URI_CAP = 2_000

#: (pack, kind) -> (monotonic time, id(client), rows)
_CACHE: dict[tuple[str, str], tuple[float, int, list[dict[str, Any]]]] = {}


def reset_caches() -> None:
    """Forget every cached list (tests; a pack reload replaces its client
    and is read afresh anyway)."""
    _CACHE.clear()


class ElicitationAnswer(BaseModel):
    action: str
    content: dict[str, Any] | None = None


class SamplingDecision(BaseModel):
    decision: str


class PromptGetBody(BaseModel):
    pack: str
    name: str
    arguments: dict[str, str] = Field(default_factory=dict)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _live_clients() -> dict[str, Any]:
    from ...mcp import tools as _mcp_tools

    getter = getattr(_mcp_tools, "live_clients", None)
    try:
        clients = getter() if callable(getter) else {}
    except Exception:  # noqa: BLE001 — no packs is an answer, never a 500
        clients = {}
    return dict(clients or {})


def _s(value: Any, cap: int) -> str:
    return value[:cap] if isinstance(value, str) else ""


def _is_method_not_found(exc: Exception) -> bool:
    text = str(exc)
    return "-32601" in text or "method not found" in text.lower()


def _pack_error(pack: str, exc: BaseException) -> str:
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return f"The app {pack} took too long to answer."
    return f"The app {pack} could not be reached."


async def _cached_list(
    pack: str, client: Any, kind: str, fetch
) -> tuple[list[dict[str, Any]] | None, str]:
    """``(rows, "")`` from the cache or the pack, else ``(None, error)``."""
    key = (pack, kind)
    hit = _CACHE.get(key)
    now = time.monotonic()
    if hit is not None and hit[1] == id(client) and now - hit[0] < CACHE_S:
        return hit[2], ""
    try:
        raw = await asyncio.wait_for(fetch(client), LIST_TIMEOUT_S)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 — one bad pack is listed as failed
        if _is_method_not_found(exc):
            raw = []
        else:
            log.info("pack %r: %s list failed (%s)", pack, kind, type(exc).__name__)
            return None, _pack_error(pack, exc)
    rows = [r for r in (raw or []) if isinstance(r, dict)]
    _CACHE[key] = (time.monotonic(), id(client), rows)
    return rows, ""


async def _list_all(kind: str, fetch) -> tuple[list[tuple[str, list[dict]]], list[dict]]:
    clients = _live_clients()
    names = sorted(clients)
    results = await asyncio.gather(
        *(_cached_list(n, clients[n], kind, fetch) for n in names)
    )
    ok: list[tuple[str, list[dict]]] = []
    failed: list[dict[str, str]] = []
    for name, (rows, error) in zip(names, results):
        if rows is None:
            failed.append({"pack": name, "error": error})
        else:
            ok.append((name, rows))
    return ok, failed


async def _fetch_prompts(client: Any) -> list[dict[str, Any]]:
    return await client.list_prompts()


async def _fetch_resources(client: Any) -> list[dict[str, Any]]:
    return await client.list_resources()


def _prompt_row(pack: str, p: dict[str, Any]) -> dict[str, Any] | None:
    name = _s(p.get("name"), _NAME_CAP).strip()
    if not name:
        return None
    args: list[dict[str, Any]] = []
    for a in p.get("arguments") or []:
        if not isinstance(a, dict) or not _s(a.get("name"), _NAME_CAP).strip():
            continue
        args.append({
            "name": _s(a.get("name"), _NAME_CAP).strip(),
            "title": _s(a.get("title"), _NAME_CAP),
            "description": _s(a.get("description"), _DESC_CAP),
            "required": bool(a.get("required") is True),
        })
    return {
        "pack": pack,
        "name": name,
        "title": _s(p.get("title"), _NAME_CAP),
        "description": _s(p.get("description"), _DESC_CAP),
        "arguments": args,
    }


def _resource_row(pack: str, r: dict[str, Any]) -> dict[str, Any] | None:
    uri = _s(r.get("uri"), _URI_CAP).strip()
    if not uri:
        return None
    return {
        "pack": pack,
        "uri": uri,
        "name": _s(r.get("name"), _NAME_CAP),
        "title": _s(r.get("title"), _NAME_CAP),
        "description": _s(r.get("description"), _DESC_CAP),
        "mime_type": _s(r.get("mimeType"), _NAME_CAP),
    }


def _content_text(content: Any) -> str:
    """One prompt message's content as text: a text block's text; anything
    else a placeholder that says what was left out."""
    blocks = content if isinstance(content, list) else [content]
    parts: list[str] = []
    for b in blocks:
        if isinstance(b, str):
            parts.append(b)
            continue
        if not isinstance(b, dict):
            continue
        kind = str(b.get("type") or "")
        if kind == "text":
            parts.append(_s(b.get("text"), 1_000_000))
        elif kind == "image":
            parts.append("[image omitted]")
        elif kind == "audio":
            parts.append("[audio omitted]")
        elif kind in ("resource", "resource_link"):
            res = b.get("resource") if kind == "resource" else b
            uri = _s((res or {}).get("uri") if isinstance(res, dict) else "", _URI_CAP)
            parts.append(f"[resource: {uri}]")
    return "\n".join(parts)


def _screen_prompt(pack: str, name: str, result: Any) -> dict[str, Any]:
    """``{text, messages, flagged}`` from a ``prompts/get`` result (PURE but
    for the scan, which never raises)."""
    raw = result.get("messages") if isinstance(result, dict) else None
    messages: list[dict[str, str]] = []
    flagged = False
    scans = []
    for m in raw or []:
        if not isinstance(m, dict):
            continue
        role = "assistant" if str(m.get("role") or "") == "assistant" else "user"
        text = _content_text(m.get("content"))
        scan = scan_context(text, source=f"{pack} prompt {name}")
        if scan.blocked:
            flagged = True
            scans.append(scan)
        messages.append({"role": role, "text": scan.text})
    text = "\n\n".join(m["text"] for m in messages if m["role"] == "user" and m["text"])
    return {"text": text, "messages": messages, "flagged": flagged, "_scans": scans}


# --------------------------------------------------------------------------- #
# the routes
# --------------------------------------------------------------------------- #


def register(app: FastAPI, d) -> None:
    """Attach the five routes; ``d`` is the create_app deps object."""

    @app.post("/chat/mcp/elicitations/{ask_id}")
    async def answer_elicitation(ask_id: str, body: ElicitationAnswer) -> dict[str, Any]:
        """The user's answer to a pack's question (see the module doc)."""
        action = (body.action or "").strip().lower()
        if action not in ("accept", "decline", "cancel"):
            raise HTTPException(
                status_code=400, detail="action must be accept, decline or cancel"
            )
        ask = mcp_turn.find(ask_id)
        if ask is None or ask.kind != "elicitation" or ask.answered:
            raise HTTPException(status_code=404, detail=f"no such open question: {ask_id}")
        content: dict[str, Any] = {}
        if action == "accept":
            from ...mcp.interact import check_elicitation_answer

            content = dict(body.content or {})
            errors = check_elicitation_answer(ask.fields, content)
            if errors:
                raise HTTPException(status_code=400, detail={"errors": errors})
        if not ask.resolve(action, content if action == "accept" else None):
            raise HTTPException(status_code=404, detail=f"no such open question: {ask_id}")
        return {"ok": True}

    @app.post("/chat/mcp/sampling/{ask_id}")
    async def decide_sampling(ask_id: str, body: SamplingDecision) -> dict[str, Any]:
        """Allow once / Deny on a pack's request to ask the turn's model."""
        decision = (body.decision or "").strip().lower()
        if decision not in ("approve", "deny"):
            raise HTTPException(status_code=400, detail="decision must be approve or deny")
        ask = mcp_turn.find(ask_id)
        if ask is None or ask.kind != "sampling" or ask.answered:
            raise HTTPException(status_code=404, detail=f"no such open request: {ask_id}")
        if not ask.resolve("approved" if decision == "approve" else "denied"):
            raise HTTPException(status_code=404, detail=f"no such open request: {ask_id}")
        return {"ok": True}

    @app.get("/mcp/prompts")
    async def pack_prompts() -> dict[str, Any]:
        """Every running pack's prompts, for the "/" picker."""
        ok, failed = await _list_all("prompts", _fetch_prompts)
        prompts: list[dict[str, Any]] = []
        for pack, rows in ok:
            for p in rows:
                row = _prompt_row(pack, p)
                if row is not None:
                    prompts.append(row)
        return {"prompts": prompts, "failed": failed}

    @app.post("/mcp/prompts/get")
    async def pack_prompt_get(body: PromptGetBody) -> dict[str, Any]:
        """One prompt's text for the composer — scanned, never sent."""
        pack = (body.pack or "").strip()
        name = (body.name or "").strip()
        client = _live_clients().get(pack)
        if client is None:
            raise HTTPException(status_code=404, detail=f"no such app: {pack}")
        rows, error = await _cached_list(pack, client, "prompts", _fetch_prompts)
        if rows is None:
            raise HTTPException(status_code=502, detail=error)
        if not any(_s(p.get("name"), _NAME_CAP).strip() == name for p in rows):
            raise HTTPException(status_code=404, detail=f"{pack} has no prompt called {name}")
        try:
            result = await asyncio.wait_for(
                client.get_prompt(name, dict(body.arguments or {})), GET_TIMEOUT_S
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — a pack error is one sentence
            log.info("pack %r: prompts/get %r failed (%s)", pack, name, type(exc).__name__)
            if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
                raise HTTPException(
                    status_code=502, detail=f"The app {pack} took too long to give that prompt."
                ) from None
            raise HTTPException(
                status_code=502, detail=f"The app {pack} could not give that prompt."
            ) from None
        out = await asyncio.to_thread(_screen_prompt, pack, name, result)
        for scan in out.pop("_scans"):
            publish_blocked(getattr(d.platform, "event_bus", None), "chat", scan)
        return out

    @app.get("/mcp/resources")
    async def pack_resources(q: str = "") -> dict[str, Any]:
        """Every running pack's resources, for the "@" picker."""
        ok, failed = await _list_all("resources", _fetch_resources)
        needle = (q or "").strip().casefold()
        resources: list[dict[str, Any]] = []
        for pack, rows in ok:
            for r in rows:
                row = _resource_row(pack, r)
                if row is None:
                    continue
                if needle and not any(
                    needle in row[k].casefold() for k in ("name", "title", "uri")
                ):
                    continue
                resources.append(row)
                if len(resources) >= MAX_RESOURCE_ROWS:
                    break
            if len(resources) >= MAX_RESOURCE_ROWS:
                break
        return {"resources": resources, "failed": failed}


__all__ = ["register", "reset_caches", "CACHE_S"]
