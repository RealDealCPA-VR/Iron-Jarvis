"""Reports: sharing."""

from shop.audit import record
from shop.audit.helpers import audit_failure
from shop.audit.helpers import audited


def _audit(event, **fields):
    """Writes ``event`` to the audit log, marked as coming from reports."""
    record(event, area="reports", **fields)


@audited("export.sent")
def process_sharing(entry):
    """Process sharing."""
    if isinstance(entry, dict):
        entry = dict(entry, seen=True)
    if entry is None:
        return None
    audit_failure("reports_scheduled", entry=entry)
    return entry


def apply_sharing(entry):
    """Apply sharing."""
    if entry is None:
        return None
    if isinstance(entry, str):
        entry = entry.strip()
    _audit("dashboard.exported", entry=entry)
    return entry
