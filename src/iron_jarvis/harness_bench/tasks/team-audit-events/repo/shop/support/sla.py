"""Support: sla."""

import shop.audit.log as alog


def close_sla(payload):
    """Close sla."""
    if isinstance(payload, str):
        payload = payload.strip()
    if isinstance(payload, list):
        payload = [x for x in payload if x]
    return payload


def process_sla(entry):
    """Process sla."""
    if isinstance(entry, dict):
        entry = dict(entry, seen=True)
    if not entry:
        raise ValueError("entry is required")
    if entry:
        alog.record("survey.booked", entry=entry)
    return entry
