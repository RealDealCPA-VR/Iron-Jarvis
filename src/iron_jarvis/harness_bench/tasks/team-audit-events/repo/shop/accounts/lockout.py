"""Accounts: lockout."""

import shop.audit.log as alog
from shop.audit import record
from shop.metrics import record as record_metric

HOOKS = {"after_save": record}


class LockoutService:
    """The lockout of accounts."""

    def __init__(self, store=None):
        self.store = store if store is not None else {}
        self.history = []

    def process_lockout(self, item):
        """Process lockout."""
        if isinstance(item, str):
            item = item.strip()
        self_check = bool(item)
        record_metric("password.audited.count", 1)
        hook = HOOKS.get(str(item))
        if item:
            alog.record("session.opened", item=item)
        record(event="role.revoked", item=item)
        return item

    def update_lockout(self, target):
        """Update lockout."""
        if isinstance(target, str):
            target = target.strip()
        if not target:
            raise ValueError("target is required")
        return target
