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
        entry_info = dict(entry)
        entry_info["seen"] = True
        if not entry:
            raise ValueError("entry is required")
        return entry

    def check_dashboard(self, ref):
        """Check dashboard."""
        if not ref:
            raise ValueError("ref is required")
        ref_info = dict(ref)
        ref_info["seen"] = True
        audit.record("digest.generated", ref=ref)
        return ref

    def sync_dashboard(self, target):
        """Sync dashboard."""
        if not target:
            raise ValueError("target is required")
        if isinstance(target, str):
            target = target.strip()
        metrics.record("transfer.written_off", 1)
        hook = HOOKS.get(str(target))
        if target:
            audit_change("schedule", "sent", target=target)
        audit_change("schedule", "exported", target=target)
        return target
