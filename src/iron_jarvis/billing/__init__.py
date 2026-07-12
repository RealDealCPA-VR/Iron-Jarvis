"""Epic Tech AI billing — credits ledger, usage meters, Stripe (keys from vault/env).

No API keys are ever hardcoded. Stripe credentials resolve at runtime from:
  1. Environment variables (STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET, …)
  2. The encrypted secrets vault (names from Config.stripe_*_secret_name)

Default: billing_enabled=False — local free use with mock/Ollama is unlimited.
"""

from __future__ import annotations

from .ledger import BillingService, DEFAULT_CREDIT_PACKS
from .models import (
    CreditBalance,
    LedgerEntry,
    ProductRecord,
    PurchaseRecord,
    SubscriptionRecord,
    UsageMeterRecord,
    Wallet,
)

__all__ = [
    "BillingService",
    "DEFAULT_CREDIT_PACKS",
    "Wallet",
    "CreditBalance",
    "LedgerEntry",
    "ProductRecord",
    "PurchaseRecord",
    "SubscriptionRecord",
    "UsageMeterRecord",
]
