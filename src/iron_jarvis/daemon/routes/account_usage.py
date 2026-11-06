"""``POST /build/accounts/{id}/usage/refresh`` (v1.439.0): "Check usage now".

One press reads ONE Iron-Proxy account's usage from its own CLI (Claude
Code's ``get_usage``, Codex's ``account/rateLimits/read`` — no model call),
tells Iron-Proxy, and answers the account row as every list now sees it
(``iron_proxy.usage_read``). A SYNC route: FastAPI runs it in its thread
pool, so the up-to-15-second read never blocks the event loop.

Answers ``{account: <the build_accounts row + usage_read_at, can_refresh>,
read: {at, ok, words, throttled}}``. 404 for an account Iron-Proxy does not
list; 409 (one sentence) when Iron-Proxy is off, the account is turned off,
needs to sign in, or is not a Claude Code / Codex account — and (v1.441.0)
when Iron-Proxy is too old to keep the number or is not answering, decided
BEFORE the CLI starts. A read that RAN
and failed is a 200 with ``read.ok: false`` and its words (the card says
them).
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException


def register(app: FastAPI, d: Any) -> None:
    @app.post("/build/accounts/{account_id}/usage/refresh")
    def refresh_account_usage(account_id: str) -> dict[str, Any]:
        from ...harness import build_accounts as BA
        from ...iron_proxy import usage_read as U

        svc = getattr(d.platform, "iron_proxy", None)
        try:
            read = U.read_usage(svc, account_id)
        except U.Refused as exc:
            raise HTTPException(status_code=404 if exc.code == "unknown" else 409,
                                detail=exc.words) from None
        row = BA.find(svc, account_id)
        return {"account": U.decorate(dict(row), svc) if row else None, "read": read}
