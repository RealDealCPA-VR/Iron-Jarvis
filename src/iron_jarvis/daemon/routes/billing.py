"""Epic Tech AI billing routes — credits, ledger, Stripe webhook.

Secrets resolve from env / vault only. Responses never include API keys.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel


class CheckoutBody(BaseModel):
    product_id: str
    success_url: str | None = None
    cancel_url: str | None = None


class GrantBody(BaseModel):
    amount: float
    reason: str = "manual_grant"


class CompletePurchaseBody(BaseModel):
    purchase_id: str | None = None
    stripe_session_id: str | None = None


def register(app: FastAPI, d: Any) -> None:
    platform = d.platform

    def _billing():
        b = getattr(platform, "billing", None)
        if b is None:
            raise HTTPException(status_code=503, detail="billing unavailable")
        return b

    @app.get("/billing")
    def billing_summary() -> dict[str, Any]:
        return _billing().summary()

    @app.get("/billing/ledger")
    def billing_ledger(limit: int = 50) -> dict[str, Any]:
        return {"entries": _billing().ledger(limit=limit)}

    @app.get("/billing/products")
    def billing_products() -> dict[str, Any]:
        return {"products": _billing().list_products()}

    @app.post("/billing/checkout")
    def billing_checkout(body: CheckoutBody) -> dict[str, Any]:
        try:
            return _billing().create_checkout(
                body.product_id,
                success_url=body.success_url,
                cancel_url=body.cancel_url,
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc))

    @app.post("/billing/complete")
    def billing_complete(body: CompletePurchaseBody) -> dict[str, Any]:
        try:
            return _billing().complete_purchase(
                purchase_id=body.purchase_id,
                stripe_session_id=body.stripe_session_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc))

    @app.post("/billing/grant")
    def billing_grant(body: GrantBody) -> dict[str, Any]:
        if body.amount <= 0:
            raise HTTPException(status_code=400, detail="amount must be positive")
        entry = _billing().grant(body.amount, ref_type="grant", ref_id=body.reason)
        return {"balance": entry.balance_after, "amount": entry.amount, "id": entry.id}

    @app.post("/billing/webhook/stripe")
    async def billing_stripe_webhook(request: Request) -> dict[str, Any]:
        billing = _billing()
        wh_secret = billing.resolve_stripe_secret("webhook")
        payload = await request.body()
        sig = request.headers.get("stripe-signature", "")
        if wh_secret:
            try:
                import stripe  # type: ignore

                event = stripe.Webhook.construct_event(payload, sig, wh_secret)
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(status_code=400, detail=f"webhook verify failed: {exc}")
        else:
            try:
                event = json.loads(payload.decode("utf-8"))
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(status_code=400, detail=f"invalid payload: {exc}")

        etype = event.get("type") if isinstance(event, dict) else getattr(event, "type", None)
        data_obj = (
            event.get("data", {}).get("object", {})
            if isinstance(event, dict)
            else getattr(getattr(event, "data", None), "object", {}) or {}
        )
        if etype == "checkout.session.completed":
            session_id = (
                data_obj.get("id")
                if isinstance(data_obj, dict)
                else getattr(data_obj, "id", None)
            )
            if session_id:
                try:
                    return billing.complete_purchase(stripe_session_id=session_id)
                except ValueError:
                    return {"status": "ignored", "reason": "purchase not found"}
        return {"status": "ignored", "type": etype}
