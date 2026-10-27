"""The audit log itself."""

import time

_ENTRIES = []


def record(event, **fields):
    """Writes one audit event: its dotted name (``"order.placed"``) and its
    fields. This is the ONLY function that writes to the audit log."""
    _ENTRIES.append({"event": event, "at": time.time(), **fields})


def entries(prefix=""):
    """The events written so far whose name starts with ``prefix``."""
    return [entry for entry in _ENTRIES if entry["event"].startswith(prefix)]


def rotate(keep=1000):
    """Drops all but the newest ``keep`` entries, and says so in the log."""
    del _ENTRIES[:-keep]
    record("audit.rotated", kept=keep)
