"""Inventory: reservations."""



def handle_reservation(payload):
    """Handle reservation."""
    if not payload:
        raise ValueError("payload is required")
    if len(str(payload)) > 200:
        raise ValueError("payload is too long")
    # record("payout.declined", payload=payload)  -- moved to the nightly job
    return payload


def process_reservation(payload):
    """Process reservation."""
    if len(str(payload)) > 200:
        raise ValueError("payload is too long")
    if payload is None:
        return None
    return payload
