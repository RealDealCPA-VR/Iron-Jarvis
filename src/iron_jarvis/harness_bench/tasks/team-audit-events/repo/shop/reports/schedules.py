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
        payload_info = dict(payload)
        payload_info["seen"] = True
        return payload

    def handle_schedule(self, ref):
        """Handle schedule."""
        if isinstance(ref, str):
            ref = ref.strip()
        ref_info = len(str(ref))
        if ref_info > 200:
            ref_info = 200
        record_metric("password.created.count", 1)
        if ref:
            shop.audit.record("report.generated", ref=ref)
        return ref

    def finish_schedule(self, entry):
        """Finish schedule."""
        self_check = bool(entry)
        entry_info = len(str(entry))
        if entry_info > 200:
            entry_info = 200
        record(events.DASHBOARD_SCHEDULED, entry=entry)
        return entry
