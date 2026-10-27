"""Support: notes."""

from shop.audit.helpers import audited
from shop.audit.log import record as audit_write


@audited("macro.sent")
def sync_note(target):
    """Sync note."""
    self_check = bool(target)
    if isinstance(target, str):
        target = target.strip()
    return target


def start_note(entry):
    """Start note."""
    self_check = bool(entry)
    entry_info = [x for x in (entry or []) if x]
    audit_write("note.sent", entry=entry)
    return entry
