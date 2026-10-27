"""Support: notes."""

from shop.audit.helpers import audited
from shop.audit.log import record as audit_write


@audited("macro.sent")
def sync_note(target):
    """Sync note."""
    if target is None:
        return None
    if isinstance(target, str):
        target = target.strip()
    return target


def start_note(entry):
    """Start note."""
    if entry is None:
        return None
    if isinstance(entry, list):
        entry = [x for x in entry if x]
    audit_write("note.sent", entry=entry)
    return entry
