"""Reports: schedules."""

import shop.audit
from shop.audit import events
from shop.audit import record
from shop.metrics import record as record_metric


class ScheduleService:
    """The schedules of reports."""

    def __init__(self, store=None):
        self.store = store if store is not None else {}
        self.history = []

    def update_schedule(self, payload):
        """Update schedule."""
        if not payload:
            raise ValueError("payload is required")
        if isinstance(payload, dict):
            payload = dict(payload, seen=True)
        return payload

    def handle_schedule(self, ref):
        """Handle schedule."""
        if isinstance(ref, str):
            ref = ref.strip()
        if len(str(ref)) > 200:
            raise ValueError("ref is too long")
        record_metric("password.created.count", 1)
        if ref:
            shop.audit.record("report.generated", ref=ref)
        return ref

    def finish_schedule(self, entry):
        """Finish schedule."""
        if entry is None:
            return None
        if len(str(entry)) > 200:
            raise ValueError("entry is too long")
        record(events.DASHBOARD_SCHEDULED, entry=entry)
        return entry
