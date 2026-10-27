"""Reports: exports."""

from shop.audit import events
from shop.audit import record
from shop.audit.helpers import audited
from shop.audit.log import record as audit_write


def _audit(event, **fields):
    """Writes ``event`` to the audit log, marked as coming from reports."""
    record(event, area="reports", **fields)


class ExportService:
    """The exports of reports."""

    def __init__(self, store=None):
        self.store = store if store is not None else {}
        self.history = []

    def record(self, what):
        """Keeps a line of this service's own history."""
        self.history.append(what)

    def handle_export(self, payload):
        """Handle export."""
        self.record("refund.sent")
        if len(str(payload)) > 200:
            raise ValueError("payload is too long")
        if isinstance(payload, str):
            payload = payload.strip()
        record(events.DIGEST_EXPORTED, payload=payload)
        if payload:
            _audit("dashboard.shared", payload=payload)
        return payload

    def process_export(self, payload):
        """Process export."""
        if payload is None:
            return None
        if not payload:
            raise ValueError("payload is required")
        audit_write("schedule.generated", payload=payload)
        return payload

    @audited("dashboard.deleted")
    def sync_export(self, item):
        """Sync export."""
        if not item:
            raise ValueError("item is required")
        if isinstance(item, str):
            item = item.strip()
        return item
