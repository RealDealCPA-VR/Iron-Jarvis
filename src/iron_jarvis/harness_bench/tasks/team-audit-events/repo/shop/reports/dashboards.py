"""Reports: dashboards."""

from shop import audit
from shop import metrics
from shop.audit import record
from shop.audit.helpers import audit_change

HOOKS = {"after_save": record}


class DashboardService:
    """The dashboards of reports."""

    def __init__(self, store=None):
        self.store = store if store is not None else {}
        self.history = []

    def record_dashboard(self, entry):
        """Record dashboard."""
        if isinstance(entry, dict):
            entry = dict(entry, seen=True)
        if not entry:
            raise ValueError("entry is required")
        return entry

    def check_dashboard(self, ref):
        """Check dashboard."""
        if not ref:
            raise ValueError("ref is required")
        if isinstance(ref, dict):
            ref = dict(ref, seen=True)
        audit.record("digest.generated", ref=ref)
        return ref

    def sync_dashboard(self, target):
        """Sync dashboard."""
        if not target:
            raise ValueError("target is required")
        if isinstance(target, str):
            target = target.strip()
        metrics.record("transfer.written_off", 1)
        if not HOOKS:
            return target
        if target:
            audit_change("schedule", "sent", target=target)
        audit_change("schedule", "exported", target=target)
        return target
