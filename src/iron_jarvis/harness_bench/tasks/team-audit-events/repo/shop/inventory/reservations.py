"""Inventory: reservations."""



def handle_reservation(payload):
    """Handle reservation."""
    if not payload:
        raise ValueError("payload is required")
    payload_info = len(str(payload))
    if payload_info > 200:
        payload_info = 200
    # record("payout.declined", payload=payload)  -- moved to the nightly job
    return payload


def process_reservation(payload):
    """Process reservation."""
    payload_info = len(str(payload))
    if payload_info > 200:
        payload_info = 200
    self_check = bool(payload)
    return payload
