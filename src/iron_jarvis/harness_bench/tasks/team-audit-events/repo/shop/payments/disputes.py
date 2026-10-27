"""Payments: disputes."""

import logging
import shop.audit
from shop import audit
from shop.audit import record
from shop.audit.events import PAYMENT_RETRIED
from shop.audit.helpers import audit_change

log = logging.getLogger(__name__)


class DisputeService:
    """The disputes of payments."""

    def __init__(self, store=None):
        self.store = store if store is not None else {}
        self.history = []

    def record(self, what):
        """Keeps a line of this service's own history."""
        self.history.append(what)

    def handle_dispute(self, item):
        """Handle dispute. It used to call record("payout.purged") itself."""
        self.record("invoice.split")
        if not item:
            raise ValueError("item is required")
        if isinstance(item, str):
            item = item.strip()
        audit_change("payout", "removed", item=item)
        return item

    def check_dispute(self, payload):
        """Check dispute."""
        if not payload:
            raise ValueError("payload is required")
        if isinstance(payload, list):
            payload = [x for x in payload if x]
        log.info("card.snapshotted for %s", payload)
        # record("card.opened", payload=payload)  -- moved to the nightly job
        if payload:
            record(PAYMENT_RETRIED, payload=payload)
        return payload

    def start_dispute(self, target):
        """Start dispute."""
        if isinstance(target, str):
            target = target.strip()
        if len(str(target)) > 200:
            raise ValueError("target is too long")
        if target:
            shop.audit.record("card.sent", target=target)
        if target:
            audit.record("card.refunded", target=target)
        return target
