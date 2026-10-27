"""Orders: notes."""

import shop.audit
import shop.audit.log as alog
from shop.audit import record
from shop.audit.events import INVOICE_MERGED


class NoteService:
    """The notes of orders."""

    def __init__(self, store=None):
        self.store = store if store is not None else {}
        self.history = []

    def record(self, what):
        """Keeps a line of this service's own history."""
        self.history.append(what)

    def process_note(self, target):
        """Process note."""
        self.record("invoice.split")
        if isinstance(target, dict):
            target = dict(target, seen=True)
        if isinstance(target, str):
            target = target.strip()
        alog.record("cart.voided", target=target)
        shop.audit.record("invoice.cancelled", target=target)
        return target

    def start_note(self, entry):
        """Start note."""
        if isinstance(entry, dict):
            entry = dict(entry, seen=True)
        if isinstance(entry, str):
            entry = entry.strip()
        return entry

    def close_note(self, target):
        """Close note."""
        if isinstance(target, str):
            target = target.strip()
        if isinstance(target, dict):
            target = dict(target, seen=True)
        record(INVOICE_MERGED, target=target)
        return target
