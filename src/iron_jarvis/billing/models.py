"""Billing / commerce persistence models (SQLModel).

Importing this package before ``init_db`` registers tables on SQLModel.metadata.
Works on SQLite (default) and future Postgres via engine-URL swap.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel

from ..core.ids import new_id, utcnow


class Wallet(SQLModel, table=True):
    """One commercial identity (local device, telegram user, or email)."""

    id: str = Field(default_factory=lambda: new_id("wallet"), primary_key=True)
    kind: str = "local"  # local | telegram | email | device
    external_id: str = Field(index=True, default="default")
    display_name: str = ""
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    # Unique external identity per kind is enforced in BillingService.


class CreditBalance(SQLModel, table=True):
    """Current credit balance for a wallet (mutable snapshot)."""

    wallet_id: str = Field(primary_key=True)
    balance: float = 0.0
    currency: str = "credits"
    updated_at: datetime = Field(default_factory=utcnow)


class LedgerEntry(SQLModel, table=True):
    """Immutable append-only ledger row."""

    id: str = Field(default_factory=lambda: new_id("ledger"), primary_key=True)
    wallet_id: str = Field(index=True)
    kind: str = Field(index=True)  # purchase | burn | refund | grant | marketplace | overage
    amount: float  # + credit, - debit
    balance_after: float = 0.0
    currency: str = "credits"
    ref_type: str = ""  # session | purchase | listing | stripe | grant
    ref_id: str = ""
    meta_json: str = "{}"
    created_at: datetime = Field(default_factory=utcnow, index=True)


class ProductRecord(SQLModel, table=True):
    """Sellable SKU (credit pack, plan, marketplace item wrapper)."""

    id: str = Field(primary_key=True)  # e.g. credits_100
    name: str
    kind: str = "credit_pack"  # credit_pack | plan | marketplace
    credits: float = 0.0
    price_cents: int = 0
    # Stripe Price id is NEVER a secret key — it's a public-ish price reference.
    # Still loaded from env (STRIPE_PRICE_*) rather than hardcoded production ids.
    stripe_price_env: str = ""  # env var name holding the Stripe price id
    active: bool = True
    meta_json: str = "{}"


class PurchaseRecord(SQLModel, table=True):
    """A checkout / payment attempt."""

    id: str = Field(default_factory=lambda: new_id("purchase"), primary_key=True)
    wallet_id: str = Field(index=True)
    product_id: str = ""
    status: str = "pending"  # pending | completed | failed | refunded
    credits_granted: float = 0.0
    amount_cents: int = 0
    # Stripe session / payment intent ids (not secrets).
    stripe_session_id: str = Field(default="", index=True)
    stripe_payment_intent: str = ""
    created_at: datetime = Field(default_factory=utcnow)
    completed_at: Optional[datetime] = None


class SubscriptionRecord(SQLModel, table=True):
    """Optional plan subscription for overage metering."""

    id: str = Field(default_factory=lambda: new_id("sub"), primary_key=True)
    wallet_id: str = Field(index=True)
    plan_id: str = "free"  # free | pro | team
    status: str = "active"  # active | past_due | canceled
    included_credits: float = 0.0
    overage_rate: float = 0.0  # credits per 1k tokens over included
    period_start: Optional[datetime] = None
    period_end: Optional[datetime] = None
    stripe_subscription_id: str = ""
    updated_at: datetime = Field(default_factory=utcnow)


class UsageMeterRecord(SQLModel, table=True):
    """Per-session token + cost meter (links agent runs to the ledger)."""

    id: str = Field(default_factory=lambda: new_id("meter"), primary_key=True)
    wallet_id: str = Field(index=True, default="default")
    session_id: str = Field(index=True, default="")
    provider: str = ""
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_usd: float = 0.0
    credits_burned: float = 0.0
    created_at: datetime = Field(default_factory=utcnow, index=True)
