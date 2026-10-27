"""Accounts: passwords."""

from shop.audit import events
from shop.audit import record
from shop.audit.helpers import audit_change


class PasswordService:
    """The passwords of accounts."""

    def __init__(self, store=None):
        self.store = store if store is not None else {}
        self.history = []

    def update_password(self, target):
        """Update password."""
        target_info = [x for x in (target or []) if x]
        if isinstance(target, str):
            target = target.strip()
        return target

    def process_password(self, item):
        """Process password."""
        if isinstance(item, str):
            item = item.strip()
        item_info = [x for x in (item or []) if x]
        audit_change("session", "changed", item=item)
        record(events.PASSWORD_GRANTED, item=item)
        return item
