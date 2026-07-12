"""Credits ledger + usage burn + Stripe checkout (secrets from vault/env only).

Never hardcode API keys. Resolve Stripe credentials with :meth:`resolve_stripe_secret`.
"""

from __future__ import annotations

import json
import os
from datetime import timedelta
from typing import Any, Callable, Optional

from sqlalchemy import Engine
from sqlmodel import select

from ..core.db import session_scope
from ..core.ids import utcnow
from ..core.logging import get_logger
from .models import (
    CreditBalance,
    LedgerEntry,
    ProductRecord,
    PurchaseRecord,
    SubscriptionRecord,
    UsageMeterRecord,
    Wallet,
)

log = get_logger("billing")

#: Catalog of default credit packs. Prices are illustrative USD cents for
#: offline/dev grants; live Stripe Price IDs come from env vars named here.
DEFAULT_CREDIT_PACKS: list[dict[str, Any]] = [
    {
        "id": "credits_100",
        "name": "100 Credits",
        "kind": "credit_pack",
        "credits": 100.0,
        "price_cents": 499,
        "stripe_price_env": "STRIPE_PRICE_CREDITS_100",
    },
    {
        "id": "credits_500",
        "name": "500 Credits",
        "kind": "credit_pack",
        "credits": 500.0,
        "price_cents": 1999,
        "stripe_price_env": "STRIPE_PRICE_CREDITS_500",
    },
    {
        "id": "credits_2000",
        "name": "2000 Credits",
        "kind": "credit_pack",
        "credits": 2000.0,
        "price_cents": 6999,
        "stripe_price_env": "STRIPE_PRICE_CREDITS_2000",
    },
]

#: Rough USD per 1M tokens by provider family (estimates for budgeting only).
_USD_PER_MTOK: dict[str, tuple[float, float]] = {
    "anthropic": (3.0, 15.0),
    "openai": (0.15, 0.60),
    "google": (0.10, 0.40),
    "xai": (2.0, 10.0),
    "openrouter": (1.0, 5.0),
    "custom": (1.0, 5.0),
    "ollama": (0.0, 0.0),
    "mock": (0.0, 0.0),
}

#: Credits burned per estimated USD (configurable scale; 1 credit ≈ $0.01).
CREDITS_PER_USD = 100.0

SecretResolver = Callable[[str], Optional[str]]


def estimate_usd(provider: str, input_tokens: int, output_tokens: int) -> float:
    rates = _USD_PER_MTOK.get((provider or "").lower(), (1.0, 5.0))
    return (input_tokens / 1_000_000.0) * rates[0] + (output_tokens / 1_000_000.0) * rates[1]


def estimate_credits(provider: str, input_tokens: int, output_tokens: int) -> float:
    usd = estimate_usd(provider, input_tokens, output_tokens)
    return round(usd * CREDITS_PER_USD, 4)


def is_billable_provider(provider: str) -> bool:
    return (provider or "").lower() not in {"mock", "ollama", ""}


class BillingService:
    """Wallet + ledger + meters. Stripe keys never stored on this object."""

    def __init__(
        self,
        engine: Engine,
        *,
        secret_resolver: SecretResolver | None = None,
        currency: str = "credits",
        stripe_secret_name: str = "stripe_secret_key",
        stripe_webhook_secret_name: str = "stripe_webhook_secret",
        site_url: str | None = None,
        enabled: bool = False,
        require_credits: bool = False,
        min_credits: float = 1.0,
    ) -> None:
        self.engine = engine
        self._secrets = secret_resolver or (lambda _n: None)
        self.currency = currency
        self.stripe_secret_name = stripe_secret_name
        self.stripe_webhook_secret_name = stripe_webhook_secret_name
        self.site_url = (site_url or "").rstrip("/") or None
        self.enabled = enabled
        self.require_credits = require_credits
        self.min_credits = min_credits
        self._ensure_default_products()
        self._ensure_default_wallet()

    # --- secret resolution (never hardcode) ---------------------------------
    def resolve_stripe_secret(self, kind: str = "secret") -> str | None:
        """Resolve a Stripe credential from env first, then vault by name.

        kind: ``secret`` | ``webhook`` | ``publishable``
        """
        env_map = {
            "secret": ("STRIPE_SECRET_KEY", self.stripe_secret_name),
            "webhook": ("STRIPE_WEBHOOK_SECRET", self.stripe_webhook_secret_name),
            "publishable": ("STRIPE_PUBLISHABLE_KEY", "stripe_publishable_key"),
        }
        env_name, vault_name = env_map.get(kind, ("", ""))
        if env_name:
            val = os.environ.get(env_name, "").strip()
            if val:
                return val
        if vault_name:
            return self._secrets(vault_name)
        return None

    def stripe_configured(self) -> bool:
        return bool(self.resolve_stripe_secret("secret"))

    # --- wallets ------------------------------------------------------------
    def _ensure_default_wallet(self) -> None:
        self.get_or_create_wallet("local", "default", "Local owner")

    def get_or_create_wallet(
        self, kind: str, external_id: str, display_name: str = ""
    ) -> Wallet:
        with session_scope(self.engine) as db:
            row = db.exec(
                select(Wallet).where(
                    Wallet.kind == kind, Wallet.external_id == external_id
                )
            ).first()
            if row is not None:
                return row
            w = Wallet(kind=kind, external_id=external_id, display_name=display_name or external_id)
            db.add(w)
            db.add(
                CreditBalance(
                    wallet_id=w.id, balance=0.0, currency=self.currency, updated_at=utcnow()
                )
            )
            db.commit()
            db.refresh(w)
            return w

    def default_wallet(self) -> Wallet:
        return self.get_or_create_wallet("local", "default", "Local owner")

    def balance(self, wallet_id: str | None = None) -> float:
        wid = wallet_id or self.default_wallet().id
        with session_scope(self.engine) as db:
            bal = db.get(CreditBalance, wid)
            return float(bal.balance) if bal else 0.0

    # --- products -----------------------------------------------------------
    def _ensure_default_products(self) -> None:
        with session_scope(self.engine) as db:
            for pack in DEFAULT_CREDIT_PACKS:
                if db.get(ProductRecord, pack["id"]) is None:
                    db.add(ProductRecord(**pack))
            db.commit()

    def list_products(self) -> list[dict[str, Any]]:
        with session_scope(self.engine) as db:
            rows = db.exec(select(ProductRecord).where(ProductRecord.active == True)).all()  # noqa: E712
            out = []
            for r in rows:
                price_id = os.environ.get(r.stripe_price_env, "").strip() if r.stripe_price_env else ""
                out.append(
                    {
                        "id": r.id,
                        "name": r.name,
                        "kind": r.kind,
                        "credits": r.credits,
                        "price_cents": r.price_cents,
                        "stripe_price_configured": bool(price_id),
                        # Never return secret keys — only whether a price env is set.
                    }
                )
            return out

    # --- ledger ops ---------------------------------------------------------
    def grant(
        self,
        amount: float,
        *,
        wallet_id: str | None = None,
        kind: str = "grant",
        ref_type: str = "grant",
        ref_id: str = "",
        meta: dict[str, Any] | None = None,
    ) -> LedgerEntry:
        if amount <= 0:
            raise ValueError("grant amount must be positive")
        return self._post(amount, wallet_id=wallet_id, kind=kind, ref_type=ref_type, ref_id=ref_id, meta=meta)

    def burn(
        self,
        amount: float,
        *,
        wallet_id: str | None = None,
        ref_type: str = "session",
        ref_id: str = "",
        meta: dict[str, Any] | None = None,
    ) -> LedgerEntry:
        if amount <= 0:
            raise ValueError("burn amount must be positive")
        return self._post(-amount, wallet_id=wallet_id, kind="burn", ref_type=ref_type, ref_id=ref_id, meta=meta)

    def _post(
        self,
        amount: float,
        *,
        wallet_id: str | None,
        kind: str,
        ref_type: str,
        ref_id: str,
        meta: dict[str, Any] | None,
    ) -> LedgerEntry:
        wid = wallet_id or self.default_wallet().id
        with session_scope(self.engine) as db:
            bal = db.get(CreditBalance, wid)
            if bal is None:
                bal = CreditBalance(wallet_id=wid, balance=0.0, currency=self.currency)
                db.add(bal)
            new_bal = float(bal.balance) + float(amount)
            if new_bal < -1e-9 and amount < 0:
                raise ValueError("insufficient credits")
            bal.balance = max(0.0, new_bal) if amount < 0 else new_bal
            bal.updated_at = utcnow()
            entry = LedgerEntry(
                wallet_id=wid,
                kind=kind,
                amount=float(amount),
                balance_after=float(bal.balance),
                currency=self.currency,
                ref_type=ref_type,
                ref_id=ref_id,
                meta_json=json.dumps(meta or {}),
            )
            db.add(entry)
            db.commit()
            db.refresh(entry)
            return entry

    def ledger(self, wallet_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        wid = wallet_id or self.default_wallet().id
        with session_scope(self.engine) as db:
            rows = db.exec(
                select(LedgerEntry)
                .where(LedgerEntry.wallet_id == wid)
                .order_by(LedgerEntry.created_at.desc())  # type: ignore[attr-defined]
                .limit(limit)
            ).all()
            return [
                {
                    "id": r.id,
                    "kind": r.kind,
                    "amount": r.amount,
                    "balance_after": r.balance_after,
                    "ref_type": r.ref_type,
                    "ref_id": r.ref_id,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
                for r in rows
            ]

    # --- preflight / burn on session ----------------------------------------
    def can_start_run(self, provider: str) -> tuple[bool, str]:
        """Return (ok, reason). Free when billing off or non-billable provider."""
        if not self.enabled or not self.require_credits:
            return True, "ok"
        if not is_billable_provider(provider):
            return True, "local_or_mock"
        bal = self.balance()
        if bal < self.min_credits:
            return False, f"insufficient credits ({bal:.2f} < {self.min_credits})"
        return True, "ok"

    def record_session_usage(
        self,
        *,
        session_id: str,
        provider: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        wallet_id: str | None = None,
    ) -> dict[str, Any]:
        wid = wallet_id or self.default_wallet().id
        credits = estimate_credits(provider, input_tokens, output_tokens)
        usd = estimate_usd(provider, input_tokens, output_tokens)
        burned = 0.0
        if self.enabled and is_billable_provider(provider) and credits > 0:
            try:
                # Clamp burn to available balance so a finished run never crashes.
                available = self.balance(wid)
                burn_amt = min(credits, available) if available > 0 else 0.0
                if burn_amt > 0:
                    self.burn(
                        burn_amt,
                        wallet_id=wid,
                        ref_type="session",
                        ref_id=session_id,
                        meta={
                            "provider": provider,
                            "model": model,
                            "input_tokens": input_tokens,
                            "output_tokens": output_tokens,
                            "estimated_usd": usd,
                        },
                    )
                    burned = burn_amt
            except ValueError:
                burned = 0.0
        with session_scope(self.engine) as db:
            meter = UsageMeterRecord(
                wallet_id=wid,
                session_id=session_id,
                provider=provider,
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                estimated_usd=usd,
                credits_burned=burned,
            )
            db.add(meter)
            db.commit()
        return {
            "session_id": session_id,
            "estimated_usd": usd,
            "credits_estimated": credits,
            "credits_burned": burned,
            "balance": self.balance(wid),
        }

    # --- budgets (token guardrails, independent of Stripe) ------------------
    def check_token_budgets(
        self,
        *,
        max_tokens_per_day: int = 0,
        max_usd_per_day: float = 0.0,
        max_runs_per_hour: int = 0,
        pending_tokens: int = 0,
    ) -> tuple[bool, str]:
        """Soft preflight against usage meters (local DB, no network)."""
        now = utcnow()
        day_ago = now - timedelta(days=1)
        hour_ago = now - timedelta(hours=1)
        with session_scope(self.engine) as db:
            day_rows = db.exec(
                select(UsageMeterRecord).where(UsageMeterRecord.created_at >= day_ago)
            ).all()
            hour_rows = db.exec(
                select(UsageMeterRecord).where(UsageMeterRecord.created_at >= hour_ago)
            ).all()
        day_tokens = sum(r.input_tokens + r.output_tokens for r in day_rows) + pending_tokens
        day_usd = sum(r.estimated_usd for r in day_rows)
        hour_runs = len(hour_rows)
        if max_tokens_per_day and day_tokens > max_tokens_per_day:
            return False, f"daily token budget exceeded ({day_tokens} > {max_tokens_per_day})"
        if max_usd_per_day and day_usd > max_usd_per_day:
            return False, f"daily $ budget exceeded (${day_usd:.4f} > ${max_usd_per_day})"
        if max_runs_per_hour and hour_runs >= max_runs_per_hour:
            return False, f"hourly run budget exceeded ({hour_runs} >= {max_runs_per_hour})"
        return True, "ok"

    # --- Stripe Checkout (optional; keys from env/vault) --------------------
    def create_checkout(
        self,
        product_id: str,
        *,
        wallet_id: str | None = None,
        success_url: str | None = None,
        cancel_url: str | None = None,
    ) -> dict[str, Any]:
        """Create a Stripe Checkout Session. Requires STRIPE_SECRET_KEY or vault.

        Returns checkout URL + purchase id. Never logs the secret key.
        """
        secret = self.resolve_stripe_secret("secret")
        if not secret:
            raise RuntimeError(
                "Stripe not configured. Set STRIPE_SECRET_KEY in the environment "
                "or store it in the secrets vault (never commit keys)."
            )
        with session_scope(self.engine) as db:
            product = db.get(ProductRecord, product_id)
            if product is None or not product.active:
                raise ValueError(f"unknown product {product_id!r}")
            price_id = (
                os.environ.get(product.stripe_price_env, "").strip()
                if product.stripe_price_env
                else ""
            )
            credits = product.credits
            price_cents = product.price_cents
            name = product.name

        wid = wallet_id or self.default_wallet().id
        purchase = PurchaseRecord(
            wallet_id=wid,
            product_id=product_id,
            status="pending",
            credits_granted=credits,
            amount_cents=price_cents,
        )
        with session_scope(self.engine) as db:
            db.add(purchase)
            db.commit()
            db.refresh(purchase)
            purchase_id = purchase.id

        site = self.site_url or "http://127.0.0.1:3000"
        success = success_url or f"{site}/billing?success=1&purchase={purchase_id}"
        cancel = cancel_url or f"{site}/billing?canceled=1"

        try:
            import stripe  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "stripe package not installed. Run: uv add stripe  (or pip install stripe)"
            ) from exc

        # Assign at call time only — never store key on module/global long-term.
        stripe.api_key = secret
        try:
            if price_id:
                session = stripe.checkout.Session.create(
                    mode="payment",
                    line_items=[{"price": price_id, "quantity": 1}],
                    success_url=success,
                    cancel_url=cancel,
                    metadata={"purchase_id": purchase_id, "wallet_id": wid, "product_id": product_id},
                )
            else:
                # Dev-friendly: price_data when no Stripe Price id env is set.
                session = stripe.checkout.Session.create(
                    mode="payment",
                    line_items=[
                        {
                            "price_data": {
                                "currency": "usd",
                                "unit_amount": price_cents,
                                "product_data": {"name": name},
                            },
                            "quantity": 1,
                        }
                    ],
                    success_url=success,
                    cancel_url=cancel,
                    metadata={"purchase_id": purchase_id, "wallet_id": wid, "product_id": product_id},
                )
        finally:
            stripe.api_key = None  # scrub

        with session_scope(self.engine) as db:
            row = db.get(PurchaseRecord, purchase_id)
            if row:
                row.stripe_session_id = session.id
                db.add(row)
                db.commit()

        return {
            "purchase_id": purchase_id,
            "checkout_url": session.url,
            "session_id": session.id,
        }

    def complete_purchase(
        self,
        *,
        purchase_id: str | None = None,
        stripe_session_id: str | None = None,
    ) -> dict[str, Any]:
        """Mark purchase completed and grant credits (idempotent)."""
        with session_scope(self.engine) as db:
            row: PurchaseRecord | None = None
            if purchase_id:
                row = db.get(PurchaseRecord, purchase_id)
            elif stripe_session_id:
                row = db.exec(
                    select(PurchaseRecord).where(
                        PurchaseRecord.stripe_session_id == stripe_session_id
                    )
                ).first()
            if row is None:
                raise ValueError("purchase not found")
            if row.status == "completed":
                return {"status": "already_completed", "purchase_id": row.id}
            row.status = "completed"
            row.completed_at = utcnow()
            pid, wid, credits = row.id, row.wallet_id, row.credits_granted
            db.add(row)
            db.commit()

        entry = self.grant(
            credits,
            wallet_id=wid,
            kind="purchase",
            ref_type="purchase",
            ref_id=pid,
            meta={"source": "stripe_or_manual"},
        )
        return {
            "status": "completed",
            "purchase_id": pid,
            "credits": credits,
            "balance": entry.balance_after,
        }

    def summary(self) -> dict[str, Any]:
        w = self.default_wallet()
        return {
            "enabled": self.enabled,
            "require_credits": self.require_credits,
            "min_credits": self.min_credits,
            "currency": self.currency,
            "balance": self.balance(w.id),
            "wallet_id": w.id,
            "stripe_configured": self.stripe_configured(),
            "products": self.list_products(),
            # Never include secret material.
        }

    def ensure_subscription(self, plan_id: str = "free", *, wallet_id: str | None = None) -> dict[str, Any]:
        wid = wallet_id or self.default_wallet().id
        plans = {
            "free": {"included_credits": 0.0, "overage_rate": 0.0},
            "pro": {"included_credits": 2000.0, "overage_rate": 1.0},
            "team": {"included_credits": 10000.0, "overage_rate": 0.8},
        }
        cfg = plans.get(plan_id, plans["free"])
        with session_scope(self.engine) as db:
            row = db.exec(
                select(SubscriptionRecord).where(SubscriptionRecord.wallet_id == wid)
            ).first()
            if row is None:
                row = SubscriptionRecord(wallet_id=wid, plan_id=plan_id, **cfg)
            else:
                row.plan_id = plan_id
                row.included_credits = cfg["included_credits"]
                row.overage_rate = cfg["overage_rate"]
                row.updated_at = utcnow()
            db.add(row)
            db.commit()
            db.refresh(row)
            return {
                "id": row.id,
                "plan_id": row.plan_id,
                "status": row.status,
                "included_credits": row.included_credits,
                "overage_rate": row.overage_rate,
            }
