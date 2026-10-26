"""YOUR MODEL TEAM's routes (v1.394.0, the long-jobs plan's Phase 5).

* ``GET  /team`` → ``{roles: [{id, label}], members: [...]}`` — every custom
  agent with one or more roles, with its model, where it runs, its cost type
  and its usage from Iron-Proxy's CACHED snapshot (never a vendor call), and
  (v1.395.0) its RECORD per role in plain words (``records``);
* ``GET  /team/starter?paid=0|1`` → the **Make a starter team** preview
  (``agents/model_team.starter_preview``) — NOTHING is created;
* ``POST /team/starter {rows: [{key, name?, roles?}], paid}`` → creates the
  ticked members from the preview rebuilt at press time
  (``model_team.create_starter``): ``{created, skipped}``; 409 in words when
  no model is connected or nothing could be added.

A member's roles and account are edited on its row (``PATCH /agents/{name}``
— one door for every custom-agent change). Every read here is off the loop
(FastAPI runs a sync ``def`` in its threadpool).
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from ...agents import model_team as T


class StarterPick(BaseModel):
    key: str
    name: str | None = None
    roles: list[str] | None = None


class StarterBody(BaseModel):
    rows: list[StarterPick] = []
    #: The "Include pay-per-use models" switch, as it was when Create was
    #: pressed — a pay-per-use row is offered (and created) only with it.
    paid: bool = False


def member_row(platform: Any, record: Any, records: "dict[str, Any] | None" = None) -> dict[str, Any]:
    """One member as Settings › Model team shows it. Never raises.

    v1.395.0: ``records`` — the member's RECORD per role it holds
    (``agents/team_record``; read for every member in one query by
    :func:`team_view`): ``{role: {runs, passed, streak, status, words}}``,
    the words plain ("good (4 of 5 passed)", "no record yet")."""
    facts = T.member_facts(platform, record) or {}
    provider = str(facts.get("provider") or "")
    available: bool | None = None
    manager = getattr(platform, "providers", None)
    if provider and manager is not None:
        try:
            available = bool(manager.available(provider))
        except Exception:  # noqa: BLE001 — unknown, said as unknown
            available = None
    account_name = ""
    usage = facts.get("usage")
    if isinstance(usage, dict) and facts.get("account"):
        account_name = str(usage.get("account_name") or "")
    from ...harness import build_accounts as BA

    if facts.get("account") and not account_name:
        try:
            row = BA.find(getattr(platform, "iron_proxy", None), str(facts["account"]))
            account_name = str((row or {}).get("name") or "")
        except Exception:  # noqa: BLE001
            account_name = ""
    # Which subscription's accounts its account picker lists ("" = none).
    vendor = ""
    if provider and facts.get("cost") == T.COST_PLAN:
        try:
            vendor = BA.vendor_of(platform, provider)
        except Exception:  # noqa: BLE001
            vendor = ""
    return {
        "name": record.name,
        "roster_name": f"custom:{record.name}",
        "description": str(getattr(record, "description", "") or ""),
        "provider": provider,
        "model": str(facts.get("model") or ""),
        "place": str(facts.get("place") or ""),
        "cost": str(facts.get("cost") or ""),
        "roles": list(facts.get("roles") or []),
        "account": str(facts.get("account") or ""),
        "account_name": account_name,
        "vendor": vendor,
        "usage": usage if isinstance(usage, dict) else None,
        "available": available,
        "paused": bool(str(getattr(record, "paused_reason", "") or "")),
        "records": _records_view(facts.get("roles") or [], (records or {}).get(record.name) or {}),
    }


def _records_view(roles: list[str], mine: dict[str, Any]) -> dict[str, Any]:
    """Each role the member holds, with its record ("no record yet" when it
    has none)."""
    from ...agents.team_record import RoleRecord

    return {role: (mine.get(role) or RoleRecord()).view() for role in roles}


def team_view(platform: Any) -> dict[str, Any]:
    # A STATUS VIEW (v1.333.0, as /health): a CLI's sign-in verdict is read
    # from the cache — no ``claude auth status`` is started from here
    # (v1.394.0 review).
    from ...providers.cli_auth import cached_verdicts

    from ...agents.team_record import records_for

    members = T.team_members(platform)
    records = records_for(getattr(platform, "engine", None), [r.name for r in members])
    with cached_verdicts():
        return {
            "roles": [{"id": rid, "label": label} for rid, label in T.ROLES],
            "members": [member_row(platform, r, records) for r in members],
        }


def register(app: FastAPI, d) -> None:
    @app.get("/team")
    def get_team() -> dict[str, Any]:
        return team_view(d.platform)

    def _preview(paid: bool) -> dict[str, Any]:
        from .connections import selectable_models

        try:
            catalog = selectable_models(d)
        except Exception as exc:  # noqa: BLE001 — said, never an empty "no models"
            raise HTTPException(
                status_code=503, detail=f"The model list could not be read: {type(exc).__name__}"
            ) from None
        return T.starter_preview(d.platform, catalog, paid=paid)

    @app.get("/team/starter")
    def get_starter(paid: int = 0) -> dict[str, Any]:
        return _preview(bool(paid))

    @app.post("/team/starter")
    def post_starter(body: StarterBody) -> dict[str, Any]:
        preview = _preview(bool(body.paid))
        try:
            out = T.create_starter(
                d.platform, preview, [p.model_dump() for p in body.rows]
            )
        except T.StarterRefused as refused:
            raise HTTPException(status_code=refused.status, detail=str(refused)) from None
        out["team"] = team_view(d.platform)
        return out
