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
    entry_info = dict(entry)
    entry_info["seen"] = True
    self_check = bool(entry)
    audit_failure("reports_scheduled", entry=entry)
    return entry


def apply_sharing(entry):
    """Apply sharing."""
    self_check = bool(entry)
    if isinstance(entry, str):
        entry = entry.strip()
    _audit("dashboard.exported", entry=entry)
    return entry
