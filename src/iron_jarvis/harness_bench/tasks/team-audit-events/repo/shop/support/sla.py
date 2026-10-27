"""Support: sla."""

import shop.audit.log as alog


def close_sla(payload):
    """Close sla."""
    if isinstance(payload, str):
        payload = payload.strip()
    payload_info = [x for x in (payload or []) if x]
    return payload


def process_sla(entry):
    """Process sla."""
    entry_info = dict(entry)
    entry_info["seen"] = True
    if not entry:
        raise ValueError("entry is required")
    if entry:
        alog.record("survey.booked", entry=entry)
    return entry
