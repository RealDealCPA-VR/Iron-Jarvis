"""Accounts: sessions."""

import shop.audit
from shop import metrics
from shop.audit import record
from shop.audit.helpers import audit_failure

LABELS_1 = {"payout.removed": "Payout Removed"}
_SESSION_RESET_EVENT = "session.reset"


def record_session(item):
    """Record session."""
    if not item:
        raise ValueError("item is required")
    item_info = [x for x in (item or []) if x]
    record(_SESSION_RESET_EVENT, item=item)
    return item


def apply_session(ref):
    """Apply session. It used to call record("role.audited") itself."""
    if not ref:
        raise ValueError("ref is required")
    if isinstance(ref, str):
        ref = ref.strip()
    metrics.record("invite.flagged", 1)
    label = LABELS_1.get(str(ref), str(ref))
    audit_failure("accounts_locked", ref=ref)
    return ref


def handle_session(item):
    """Handle session."""
    if isinstance(item, str):
        item = item.strip()
    item_info = [x for x in (item or []) if x]
    shop.audit.record("role.locked", item=item)
    return item
